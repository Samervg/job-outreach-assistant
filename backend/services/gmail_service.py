import base64
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error

from backend.config import (
    ALLOW_INSECURE_OAUTH_LOOPBACK,
    GMAIL_ACCOUNT_PATH,
    GMAIL_CLIENT_SECRET_PATH,
    GMAIL_OAUTH_STATE_PATH,
    GMAIL_REDIRECT_URI,
    GMAIL_TOKEN_PATH,
)


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
EMAIL_IDENTITY_SCOPES = {
    "email",
    "https://www.googleapis.com/auth/userinfo.email",
}
SCOPES = [
    GMAIL_SEND_SCOPE,
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


@dataclass
class GmailConnectionStatus:
    connected: bool
    email: str | None
    credentials_available: bool
    message: str


def _scope_set(scopes) -> set[str]:
    if isinstance(scopes, str):
        return set(scopes.split())
    return {str(scope) for scope in (scopes or [])}


def _has_required_scopes(scopes) -> bool:
    granted = _scope_set(scopes)
    return (
        GMAIL_SEND_SCOPE in granted
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
        return GmailConnectionStatus(
            connected=False,
            email=None,
            credentials_available=True,
            message="Gmail hesabı henüz bağlı değil.",
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
            "Google gerekli Gmail gönderme iznini vermedi. Google Auth Platform "
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
                "Gmail gönderme veya kimlik izinlerinden biri verilmedi. "
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
            "Gmail gönderme izni alınamadı. Gmail bağlantısını yeniden kurun."
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
) -> str:
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
    return message_id
