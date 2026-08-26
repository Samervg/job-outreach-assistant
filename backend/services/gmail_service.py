import base64
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error
from bs4 import BeautifulSoup

from backend.config import (
    ALLOW_INSECURE_OAUTH_LOOPBACK,
    GMAIL_ACCOUNT_PATH,
    GMAIL_CLIENT_SECRET_PATH,
    GMAIL_OAUTH_STATE_PATH,
    GMAIL_REDIRECT_URI,
    GMAIL_TOKEN_PATH,
)


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
EMAIL_IDENTITY_SCOPES = {
    "email",
    "https://www.googleapis.com/auth/userinfo.email",
}
SCOPES = [
    GMAIL_SEND_SCOPE,
    GMAIL_READONLY_SCOPE,
    "openid",
    "email",
]
logger = logging.getLogger(__name__)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class GmailConfigurationError(Exception):
    pass


class GmailNotConnectedError(Exception):
    pass


class GmailSendError(Exception):
    pass


class GmailReadError(Exception):
    pass


@dataclass
class GmailConnectionStatus:
    connected: bool
    email: str | None
    credentials_available: bool
    message: str


@dataclass
class GmailSendResult:
    message_id: str
    thread_id: str | None


@dataclass
class GmailReplyResult:
    has_reply: bool
    reply_count: int
    latest_reply_at: str | None
    latest_reply_from: str | None
    latest_reply_subject: str | None
    latest_reply_snippet: str | None
    thread_id: str


@dataclass
class GmailReplyContent:
    sender: str
    subject: str | None
    received_at: str | None
    body_text: str
    thread_id: str


def _scope_set(scopes) -> set[str]:
    if isinstance(scopes, str):
        return set(scopes.split())
    return {str(scope) for scope in (scopes or [])}


def _has_required_scopes(scopes) -> bool:
    granted = _scope_set(scopes)
    return (
        GMAIL_SEND_SCOPE in granted
        and GMAIL_READONLY_SCOPE in granted
        and "openid" in granted
        and bool(granted & EMAIL_IDENTITY_SCOPES)
    )


def _configure_oauth_transport() -> None:
    """Allow HTTP only for an explicitly enabled local loopback callback."""
    redirect = urlparse(GMAIL_REDIRECT_URI)

    if redirect.scheme == "https":
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        return

    if (
        redirect.scheme == "http"
        and redirect.hostname in LOOPBACK_HOSTS
        and ALLOW_INSECURE_OAUTH_LOOPBACK
    ):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        return

    os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    raise GmailConfigurationError(
        "HTTPS olmayan OAuth callback yalnızca açıkça etkinleştirilmiş "
        "localhost/loopback geliştirme ortamında kullanılabilir."
    )


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(path)


def _save_credentials(credentials: Credentials) -> None:
    _write_json(GMAIL_TOKEN_PATH, json.loads(credentials.to_json()))


def _load_credentials() -> Credentials | None:
    if not GMAIL_TOKEN_PATH.is_file():
        return None

    try:
        credentials = Credentials.from_authorized_user_file(
            str(GMAIL_TOKEN_PATH), SCOPES
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_credentials(credentials)
        if not credentials.valid or not credentials.has_scopes(SCOPES):
            return None
        return credentials
    except (OSError, ValueError, RefreshError):
        return None


def _load_account_email() -> str | None:
    if not GMAIL_ACCOUNT_PATH.is_file():
        return None
    try:
        data = json.loads(GMAIL_ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    email = data.get("email")
    return str(email).strip() if email else None


def _fetch_account_email(credentials: Credentials) -> str | None:
    try:
        service = build(
            "oauth2", "v2", credentials=credentials, cache_discovery=False
        )
        result = service.userinfo().get().execute()
    except (HttpError, OSError, ValueError):
        return None
    email = result.get("email")
    return str(email).strip() if email else None


def get_gmail_status() -> GmailConnectionStatus:
    credentials_available = GMAIL_CLIENT_SECRET_PATH.is_file()
    if not credentials_available:
        return GmailConnectionStatus(
            connected=False,
            email=None,
            credentials_available=False,
            message=(
                "Gmail OAuth istemci dosyası bulunamadı. "
                f"Beklenen konum: {GMAIL_CLIENT_SECRET_PATH}"
            ),
        )

    credentials = _load_credentials()
    if credentials is None:
        reconnect_message = (
            "Gmail izinleri güncellendi. Hesabı yeniden bağlayın."
            if GMAIL_TOKEN_PATH.is_file()
            else "Gmail hesabı henüz bağlı değil."
        )
        return GmailConnectionStatus(
            connected=False,
            email=None,
            credentials_available=True,
            message=reconnect_message,
        )

    email = _load_account_email() or _fetch_account_email(credentials)
    if email and not GMAIL_ACCOUNT_PATH.is_file():
        _write_json(GMAIL_ACCOUNT_PATH, {"email": email})
    return GmailConnectionStatus(
        connected=True,
        email=email,
        credentials_available=True,
        message="Gmail bağlantısı hazır.",
    )


def start_oauth() -> str:
    if not GMAIL_CLIENT_SECRET_PATH.is_file():
        raise GmailConfigurationError(
            f"Gmail OAuth istemci dosyası bulunamadı: {GMAIL_CLIENT_SECRET_PATH}"
        )

    _configure_oauth_transport()

    try:
        flow = Flow.from_client_secrets_file(
            str(GMAIL_CLIENT_SECRET_PATH), scopes=SCOPES
        )
        flow.redirect_uri = GMAIL_REDIRECT_URI
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        code_verifier = flow.code_verifier
    except (OSError, ValueError) as error:
        raise GmailConfigurationError(
            "Gmail OAuth istemci dosyası okunamadı veya geçersiz."
        ) from error

    if not code_verifier or not PKCE_VERIFIER_PATTERN.fullmatch(code_verifier):
        raise GmailConfigurationError("OAuth PKCE doğrulayıcısı oluşturulamadı.")

    _write_json(
        GMAIL_OAUTH_STATE_PATH,
        {"state": state, "code_verifier": code_verifier},
    )
    return authorization_url


def _consume_oauth_state() -> None:
    """Atomically make the saved OAuth request single-use before code exchange."""
    claimed_path = GMAIL_OAUTH_STATE_PATH.with_name(
        f".{GMAIL_OAUTH_STATE_PATH.name}.{secrets.token_hex(8)}.consumed"
    )
    try:
        GMAIL_OAUTH_STATE_PATH.replace(claimed_path)
    except OSError as error:
        raise GmailConfigurationError(
            "OAuth isteği daha önce kullanılmış veya süresi dolmuş."
        ) from error
    try:
        claimed_path.unlink()
    except OSError:
        pass


def complete_oauth(authorization_response: str, returned_state: str) -> str | None:
    if not GMAIL_OAUTH_STATE_PATH.is_file():
        raise GmailConfigurationError("OAuth isteği bulunamadı veya süresi doldu.")

    try:
        state_data = json.loads(GMAIL_OAUTH_STATE_PATH.read_text(encoding="utf-8"))
        expected_state = str(state_data["state"])
        code_verifier = str(state_data["code_verifier"])
    except (OSError, ValueError, KeyError) as error:
        raise GmailConfigurationError("Kaydedilmiş OAuth durumu geçersiz.") from error

    if not secrets.compare_digest(expected_state, returned_state):
        raise GmailConfigurationError("OAuth güvenlik doğrulaması başarısız oldu.")
    if not PKCE_VERIFIER_PATTERN.fullmatch(code_verifier):
        raise GmailConfigurationError("Kaydedilmiş OAuth PKCE doğrulayıcısı geçersiz.")

    _consume_oauth_state()
    _configure_oauth_transport()

    callback_scopes = set(
        parse_qs(urlparse(authorization_response).query).get("scope", [""])[0].split()
    )
    if callback_scopes and not _has_required_scopes(callback_scopes):
        logger.error(
            "Gmail OAuth callback lacks required scopes: requested_scopes=%s "
            "returned_scopes=%s",
            sorted(SCOPES),
            sorted(callback_scopes),
        )
        raise GmailConfigurationError(
            "Google gerekli Gmail gönderme/okuma iznini vermedi. Google Auth Platform "
            "Data Access ayarını ve izin ekranındaki seçimi kontrol edip yeni bir "
            "bağlantı başlatın."
        )

    try:
        flow = Flow.from_client_secrets_file(
            str(GMAIL_CLIENT_SECRET_PATH),
            scopes=SCOPES,
            state=expected_state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = GMAIL_REDIRECT_URI
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
    except Warning as error:
        returned_scopes = _scope_set(getattr(error, "new_scope", []))
        if not _has_required_scopes(returned_scopes):
            logger.error(
                "Gmail OAuth scope mismatch rejected: exception_type=%s "
                "requested_scopes=%s returned_scopes=%s",
                type(error).__name__,
                sorted(SCOPES),
                sorted(returned_scopes),
            )
            raise GmailConfigurationError(
                "Gmail gönderme, salt okunur erişim veya kimlik izinlerinden biri verilmedi. "
                "Gmail bağlantısını yeniden kurun."
            ) from error

        # OAuthLib already completed the single token exchange and attaches the
        # parsed token to this Warning. Continue only after our strict subset
        # validation; never log the token object or its values.
        flow.oauth2session.token = error.token
        credentials = flow.credentials
        logger.info(
            "Gmail OAuth accepted equivalent identity scope normalization: "
            "returned_scopes=%s",
            sorted(returned_scopes),
        )
    except (OSError, ValueError, OAuth2Error) as error:
        # Keep development diagnostics useful without printing authorization
        # responses, codes, client secrets, or token values.
        logger.error(
            "Gmail OAuth token exchange failed: exception_type=%s oauth_error=%s",
            type(error).__name__,
            getattr(error, "error", None),
        )
        if getattr(error, "error", None) == "invalid_grant":
            raise GmailConfigurationError(
                "OAuth yetkilendirme kodu geçersiz, süresi dolmuş veya daha önce "
                "kullanılmış. Yeni bir Gmail bağlantısı başlatın."
            ) from error
        raise GmailConfigurationError("Gmail yetkilendirmesi tamamlanamadı.") from error

    granted_scopes = (
        credentials.granted_scopes
        or credentials.scopes
        or flow.oauth2session.token.get("scope")
    )
    if not _has_required_scopes(granted_scopes):
        logger.error(
            "Gmail OAuth token lacks required scopes: requested_scopes=%s "
            "granted_scopes=%s",
            sorted(SCOPES),
            sorted(_scope_set(granted_scopes)),
        )
        raise GmailConfigurationError(
            "Gmail gönderme/okuma izni alınamadı. Gmail bağlantısını yeniden kurun."
        )

    _save_credentials(credentials)
    email = _fetch_account_email(credentials)
    if email:
        _write_json(GMAIL_ACCOUNT_PATH, {"email": email})
    return email


def build_mime_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    cv_path: Path,
    cv_original_name: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(
        cv_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=Path(cv_original_name).name,
    )
    return message


def send_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    cv_path: Path,
    cv_original_name: str,
    service=None,
    sender_email: str | None = None,
) -> GmailSendResult:
    try:
        if service is None:
            credentials = _load_credentials()
            if credentials is None:
                raise GmailNotConnectedError("Gmail hesabı bağlı değil.")
            service = build(
                "gmail", "v1", credentials=credentials, cache_discovery=False
            )
            sender_email = sender_email or _load_account_email()
            if not sender_email:
                sender_email = _fetch_account_email(credentials)
    except (HttpError, OSError, ValueError) as error:
        raise GmailSendError("Gmail API bağlantısı kurulamadı.") from error

    if not sender_email:
        raise GmailNotConnectedError("Bağlı Gmail adresi belirlenemedi.")

    try:
        mime_message = build_mime_message(
            sender=sender_email,
            recipient=recipient,
            subject=subject,
            body=body,
            cv_path=cv_path,
            cv_original_name=cv_original_name,
        )
        encoded_message = base64.urlsafe_b64encode(
            mime_message.as_bytes()
        ).decode("ascii")
        result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded_message})
            .execute()
        )
    except (HttpError, OSError, ValueError) as error:
        raise GmailSendError("Gmail API e-postayı gönderemedi.") from error

    message_id = str(result.get("id") or "").strip()
    if not message_id:
        raise GmailSendError("Gmail API geçerli bir mesaj kimliği döndürmedi.")
    thread_id = str(result.get("threadId") or "").strip() or None
    return GmailSendResult(message_id=message_id, thread_id=thread_id)


def send_thread_follow_up(
    *,
    recipient: str,
    subject: str,
    body: str,
    original_message_id: str,
    thread_id: str,
    service=None,
    sender_email: str | None = None,
) -> GmailSendResult:
    try:
        if service is None:
            credentials = _load_credentials()
            if credentials is None:
                raise GmailNotConnectedError("Gmail hesabı bağlı değil.")
            service = build(
                "gmail", "v1", credentials=credentials, cache_discovery=False
            )
            sender_email = sender_email or _load_account_email()
        if not sender_email:
            raise GmailNotConnectedError("Bağlı Gmail adresi belirlenemedi.")

        original = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=original_message_id,
                format="metadata",
                metadataHeaders=["Message-ID"],
            )
            .execute()
        )
        verified_thread_id = str(original.get("threadId") or "").strip()
        if not verified_thread_id or verified_thread_id != thread_id:
            raise GmailSendError("Gmail konuşma kimliği gönderilen mesajla eşleşmiyor.")
        internet_message_id = _header(original, "Message-ID")

        message = EmailMessage()
        message["From"] = sender_email
        message["To"] = recipient
        message["Subject"] = subject
        if internet_message_id:
            message["In-Reply-To"] = internet_message_id
            message["References"] = internet_message_id
        message.set_content(body)
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded, "threadId": thread_id})
            .execute()
        )
    except (GmailNotConnectedError, GmailSendError):
        raise
    except (HttpError, OSError, ValueError, TypeError) as error:
        raise GmailSendError("Gmail follow-up e-postasını gönderemedi.") from error

    sent_id = str(sent.get("id") or "").strip()
    sent_thread_id = str(sent.get("threadId") or "").strip()
    if not sent_id or sent_thread_id != thread_id:
        raise GmailSendError("Gmail follow-up için geçerli aynı-thread sonucu döndürmedi.")
    return GmailSendResult(message_id=sent_id, thread_id=sent_thread_id)


def _header(message: dict, name: str) -> str | None:
    for header in message.get("payload", {}).get("headers", []):
        if str(header.get("name") or "").casefold() == name.casefold():
            value = str(header.get("value") or "").strip()
            return value or None
    return None


def _message_time(message: dict) -> int:
    try:
        return int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        return 0


def _received_at(message: dict) -> str | None:
    timestamp = _message_time(message)
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()


def _decode_body(data: str | None) -> str:
    if not data:
        return ""
    try:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, TypeError):
        return ""


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, .gmail_quote, blockquote.gmail_quote"):
        element.decompose()
    return unescape(soup.get_text("\n"))


def _collect_mime_text(part: dict, plain: list[str], html: list[str]) -> None:
    mime_type = str(part.get("mimeType") or "").casefold()
    decoded = _decode_body((part.get("body") or {}).get("data"))
    if mime_type == "text/plain" and decoded.strip():
        plain.append(decoded)
    elif mime_type == "text/html" and decoded.strip():
        html.append(decoded)
    for child in part.get("parts") or []:
        _collect_mime_text(child, plain, html)


def _extract_body_text(message: dict) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _collect_mime_text(message.get("payload") or {}, plain_parts, html_parts)
    if plain_parts:
        return "\n".join(plain_parts)
    return _html_to_text("\n".join(html_parts)) if html_parts else ""


QUOTED_MARKERS = (
    re.compile(r"^On .+ wrote:\s*$", re.IGNORECASE),
    re.compile(r"^.+ tarihinde .+ şunu yazdı:\s*$", re.IGNORECASE),
    re.compile(
        r"^-{2,}\s*(Original Message|Forwarded message|İletilen ileti)\s*-{2,}$",
        re.IGNORECASE,
    ),
)


def _clean_reply_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if any(pattern.match(stripped) for pattern in QUOTED_MARKERS):
            break
        kept.append(line.rstrip())
    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or text.strip()


def _external_replies(
    *, original: dict, thread: dict, message_id: str, account_email: str
) -> list[dict]:
    own_addresses = {
        parseaddr(account_email)[1].casefold(),
        parseaddr(_header(original, "From") or "")[1].casefold(),
    }
    own_addresses.discard("")
    original_time = _message_time(original)
    replies = []
    for message in thread.get("messages") or []:
        if str(message.get("id") or "") == message_id:
            continue
        if _message_time(message) <= original_time:
            continue
        sender_address = parseaddr(_header(message, "From") or "")[1].casefold()
        if not sender_address or sender_address in own_addresses:
            continue
        replies.append(message)
    replies.sort(key=_message_time)
    return replies


def check_thread_replies(
    *,
    message_id: str,
    thread_id: str | None = None,
    service=None,
    account_email: str | None = None,
) -> GmailReplyResult:
    try:
        if service is None:
            credentials = _load_credentials()
            if credentials is None:
                raise GmailNotConnectedError(
                    "Gmail hesabı bağlı değil veya gmail.readonly izni eksik."
                )
            service = build(
                "gmail", "v1", credentials=credentials, cache_discovery=False
            )
            account_email = account_email or _load_account_email()
        if not account_email:
            raise GmailNotConnectedError("Bağlı Gmail adresi belirlenemedi.")

        original = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            .execute()
        )
        original_thread_id = str(original.get("threadId") or "").strip()
        if thread_id and original_thread_id and thread_id != original_thread_id:
            raise GmailReadError("Kayıtlı Gmail konuşma kimliği mesajla eşleşmiyor.")
        resolved_thread_id = original_thread_id or str(thread_id or "").strip()
        if not resolved_thread_id:
            raise GmailReadError("Gönderilen Gmail konuşması bulunamadı.")
        thread = (
            service.users()
            .threads()
            .get(
                userId="me",
                id=resolved_thread_id,
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            .execute()
        )
    except GmailNotConnectedError:
        raise
    except GmailReadError:
        raise
    except (HttpError, OSError, ValueError, TypeError) as error:
        raise GmailReadError("Gmail yanıt bilgisi alınamadı.") from error

    external_replies = _external_replies(
        original=original,
        thread=thread,
        message_id=message_id,
        account_email=account_email,
    )
    latest = external_replies[-1] if external_replies else None
    latest_timestamp = _message_time(latest) if latest else 0
    latest_at = (
        datetime.fromtimestamp(latest_timestamp / 1000, tz=timezone.utc).isoformat()
        if latest_timestamp
        else None
    )
    snippet = " ".join(str((latest or {}).get("snippet") or "").split())[:240]
    return GmailReplyResult(
        has_reply=bool(external_replies),
        reply_count=len(external_replies),
        latest_reply_at=latest_at,
        latest_reply_from=_header(latest, "From") if latest else None,
        latest_reply_subject=_header(latest, "Subject") if latest else None,
        latest_reply_snippet=snippet or None,
        thread_id=resolved_thread_id,
    )


def get_latest_reply_content(
    *,
    message_id: str,
    thread_id: str | None = None,
    service=None,
    account_email: str | None = None,
) -> GmailReplyContent:
    try:
        if service is None:
            credentials = _load_credentials()
            if credentials is None:
                raise GmailNotConnectedError(
                    "Gmail hesabı bağlı değil veya gmail.readonly izni eksik."
                )
            service = build(
                "gmail", "v1", credentials=credentials, cache_discovery=False
            )
            account_email = account_email or _load_account_email()
        if not account_email:
            raise GmailNotConnectedError("Bağlı Gmail adresi belirlenemedi.")

        original = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata")
            .execute()
        )
        original_thread_id = str(original.get("threadId") or "").strip()
        if thread_id and original_thread_id and thread_id != original_thread_id:
            raise GmailReadError("Kayıtlı Gmail konuşma kimliği mesajla eşleşmiyor.")
        resolved_thread_id = original_thread_id or str(thread_id or "").strip()
        if not resolved_thread_id:
            raise GmailReadError("Gönderilen Gmail konuşması bulunamadı.")
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=resolved_thread_id, format="full")
            .execute()
        )
        returned_thread_id = str(thread.get("id") or "").strip()
        if returned_thread_id and returned_thread_id != resolved_thread_id:
            raise GmailReadError("Gmail farklı bir konuşma döndürdü.")
    except (GmailNotConnectedError, GmailReadError):
        raise
    except (HttpError, OSError, ValueError, TypeError) as error:
        raise GmailReadError("Gmail yanıt içeriği alınamadı.") from error

    replies = _external_replies(
        original=original,
        thread=thread,
        message_id=message_id,
        account_email=account_email,
    )
    if not replies:
        raise GmailReadError("Bu Gmail konuşmasında dış yanıt bulunamadı.")
    latest = replies[-1]
    body_text = _clean_reply_text(_extract_body_text(latest))
    if not body_text:
        raise GmailReadError("Son Gmail yanıtının okunabilir metni bulunamadı.")
    return GmailReplyContent(
        sender=_header(latest, "From") or "",
        subject=_header(latest, "Subject"),
        received_at=_received_at(latest),
        body_text=body_text,
        thread_id=resolved_thread_id,
    )
