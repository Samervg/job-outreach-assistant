from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.config import FRONTEND_URL, PROJECT_ROOT, UPLOAD_DIR
from backend.database import get_connection
from backend.outreach import DraftResponse, _row_to_draft
from backend.services.gmail_service import (
    GmailConfigurationError,
    GmailConnectionStatus,
    GmailNotConnectedError,
    GmailSendError,
    complete_oauth,
    get_gmail_status as get_service_status,
    send_email,
    start_oauth,
)


router = APIRouter(tags=["gmail"])
send_lock = Lock()


class GmailStatusResponse(BaseModel):
    connected: bool
    email: str | None
    credentials_available: bool
    message: str


class GmailAuthStartResponse(BaseModel):
    authorization_url: str


class SendDraftRequest(BaseModel):
    confirm_send: bool = False


def _safe_cv_path(stored_path: str) -> Path | None:
    candidate = Path(stored_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return None
    return candidate


def _connection_status() -> GmailConnectionStatus:
    return get_service_status()


@router.get("/gmail/status", response_model=GmailStatusResponse)
def gmail_status() -> GmailStatusResponse:
    return GmailStatusResponse(**_connection_status().__dict__)


@router.get("/gmail/auth/start", response_model=GmailAuthStartResponse)
def gmail_auth_start() -> GmailAuthStartResponse:
    try:
        authorization_url = start_oauth()
    except GmailConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    return GmailAuthStartResponse(authorization_url=authorization_url)


@router.get("/gmail/auth/callback", response_class=HTMLResponse)
def gmail_auth_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gmail yetkilendirmesi kullanıcı tarafından reddedildi.",
        )
    if not state or not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth callback state veya code içermiyor.",
        )

    try:
        email = complete_oauth(str(request.url), state)
    except GmailConfigurationError as callback_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(callback_error)
        ) from callback_error

    account_text = f" ({email})" if email else ""
    return HTMLResponse(
        "<h2>Gmail bağlantısı tamamlandı.</h2>"
        f"<p>Hesap{account_text} kullanıma hazır.</p>"
        f'<p><a href="{FRONTEND_URL}">Uygulamaya dön</a></p>'
    )


def _mark_failed(draft_id: int, error_message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET status = 'failed', sent_at = NULL, gmail_message_id = NULL,
                error_message = ?, updated_at = ?
            WHERE id = ? AND status != 'sent'
            """,
            (error_message[:1000], now, draft_id),
        )


@router.post("/drafts/{draft_id}/send", response_model=DraftResponse)
def send_draft(draft_id: int, request: SendDraftRequest) -> DraftResponse:
    if request.confirm_send is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="E-posta gönderimi için açık onay gereklidir.",
        )

    with send_lock:
        with get_connection() as connection:
            draft = connection.execute(
                "SELECT * FROM outreach WHERE id = ?", (draft_id,)
            ).fetchone()
            profile = connection.execute(
                "SELECT * FROM user_profile WHERE id = 1"
            ).fetchone()

        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Taslak bulunamadı."
            )
        if draft["status"] == "sent":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bu taslak daha önce gönderildi ve tekrar gönderilemez.",
            )

        try:
            recipient = validate_email(
                draft["recipient_email"], check_deliverability=False
            ).normalized
        except EmailNotValidError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Taslağın alıcı e-posta adresi geçersiz.",
            ) from error

        subject = str(draft["subject"] or "").strip()
        body = str(draft["body"] or "").strip()
        if not subject or not body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Taslak konusu ve gövdesi boş olamaz.",
            )
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aktif kullanıcı profili bulunamadı.",
            )
        if not profile["cv_file_path"] or not profile["cv_original_name"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gönderim için aktif bir CV gereklidir.",
            )

        cv_path = _safe_cv_path(profile["cv_file_path"])
        if (
            cv_path is None
            or cv_path.suffix.lower() != ".pdf"
            or not cv_path.is_file()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aktif CV PDF dosyası bulunamadı veya güvenli değil.",
            )
        try:
            with cv_path.open("rb") as cv_file:
                if cv_file.read(5) != b"%PDF-":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Aktif CV geçerli bir PDF dosyası değil.",
                    )
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aktif CV dosyası okunamadı.",
            ) from error

        connection_state = _connection_status()
        if not connection_state.connected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=connection_state.message,
            )

        try:
            message_id = send_email(
                recipient=recipient,
                subject=subject,
                body=body,
                cv_path=cv_path,
                cv_original_name=profile["cv_original_name"],
            )
        except (GmailNotConnectedError, GmailSendError) as error:
            _mark_failed(draft_id, str(error))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE outreach
                SET status = 'sent', sent_at = ?, gmail_message_id = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ? AND status != 'sent'
                """,
                (now, message_id, now, draft_id),
            )
            sent_row = connection.execute(
                "SELECT * FROM outreach WHERE id = ?", (draft_id,)
            ).fetchone()

    return _row_to_draft(sent_row)
