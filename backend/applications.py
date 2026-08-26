import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from backend.database import get_connection
from backend.outreach import DraftResponse, _row_to_draft
from backend.services.gmail_service import (
    GmailNotConnectedError,
    GmailReadError,
    check_thread_replies,
    get_latest_reply_content,
)


router = APIRouter(prefix="/applications", tags=["applications"])

ApplicationStatus = Literal[
    "draft", "sent", "failed", "replied", "interview", "rejected", "offer"
]
FILTER_STATUSES = {
    "draft", "sent", "failed", "replied", "interview", "rejected", "offer"
}
MANUAL_TRANSITIONS = {
    "sent": {"replied", "interview", "rejected", "offer"},
    "replied": {"interview", "rejected", "offer"},
    "interview": {"rejected", "offer"},
    "rejected": set(),
    "offer": set(),
}


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_change(self):
        if self.status is None and self.notes is None:
            raise ValueError("Durum veya not alanlarından en az biri gereklidir.")
        return self


class ReplySyncResponse(BaseModel):
    has_reply: bool
    reply_count: int
    latest_reply_at: str | None
    latest_reply_from: str | None
    latest_reply_subject: str | None
    latest_reply_snippet: str | None
    gmail_thread_id: str
    application: DraftResponse


class ReplyContentResponse(BaseModel):
    from_: str = Field(serialization_alias="from")
    subject: str | None
    received_at: str | None
    body_text: str
    gmail_thread_id: str


def _get_application(application_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (application_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Başvuru bulunamadı.",
        )
    return row


@router.get("", response_model=list[DraftResponse])
def list_applications(
    application_status: str = Query(default="all", alias="status"),
) -> list[DraftResponse]:
    if application_status != "all" and application_status not in FILTER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Geçersiz başvuru durumu.",
        )

    query = "SELECT * FROM outreach"
    parameters: tuple[str, ...] = ()
    if application_status != "all":
        query += " WHERE status = ?"
        parameters = (application_status,)
    query += " ORDER BY COALESCE(sent_at, created_at) DESC, id DESC"

    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_draft(row) for row in rows]


@router.get("/{application_id}", response_model=DraftResponse)
def get_application(application_id: int) -> DraftResponse:
    return _row_to_draft(_get_application(application_id))


@router.patch("/{application_id}", response_model=DraftResponse)
def update_application(
    application_id: int, update: ApplicationUpdate
) -> DraftResponse:
    current = _get_application(application_id)
    next_status = update.status or current["status"]

    if update.status is not None and update.status != current["status"]:
        allowed = MANUAL_TRANSITIONS.get(current["status"], set())
        if update.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"'{current['status']}' durumundan '{update.status}' "
                    "durumuna manuel geçiş yapılamaz."
                ),
            )

    notes = current["notes"] if update.notes is None else update.notes.strip()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET status = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, notes, now, application_id),
        )
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (application_id,)
        ).fetchone()
    return _row_to_draft(row)


@router.post("/{application_id}/sync-reply", response_model=ReplySyncResponse)
def sync_application_reply(application_id: int) -> ReplySyncResponse:
    current = _get_application(application_id)
    eligible_statuses = {"sent", "replied", "interview", "rejected", "offer"}
    if current["status"] not in eligible_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Yalnızca Gmail ile gönderilmiş başvurular kontrol edilebilir.",
        )
    message_id = str(current["gmail_message_id"] or "").strip()
    if not message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Başvurunun Gmail mesaj kimliği bulunmuyor.",
        )

    try:
        result = check_thread_replies(
            message_id=message_id,
            thread_id=current["gmail_thread_id"],
        )
    except GmailNotConnectedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    except GmailReadError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error

    next_status = "replied" if result.has_reply and current["status"] == "sent" else current["status"]
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET status = ?, gmail_thread_id = ?, replied_at = ?,
                latest_reply_from = ?, latest_reply_subject = ?,
                latest_reply_snippet = ?, reply_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                result.thread_id,
                result.latest_reply_at,
                result.latest_reply_from,
                result.latest_reply_subject,
                result.latest_reply_snippet,
                result.reply_count,
                now,
                application_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (application_id,)
        ).fetchone()
    return ReplySyncResponse(
        has_reply=result.has_reply,
        reply_count=result.reply_count,
        latest_reply_at=result.latest_reply_at,
        latest_reply_from=result.latest_reply_from,
        latest_reply_subject=result.latest_reply_subject,
        latest_reply_snippet=result.latest_reply_snippet,
        gmail_thread_id=result.thread_id,
        application=_row_to_draft(row),
    )


@router.get(
    "/{application_id}/reply-content",
    response_model=ReplyContentResponse,
    response_model_by_alias=True,
)
def get_application_reply_content(application_id: int) -> ReplyContentResponse:
    current = _get_application(application_id)
    message_id = str(current["gmail_message_id"] or "").strip()
    if not message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Başvurunun Gmail mesaj kimliği bulunmuyor.",
        )
    if not current["replied_at"] or int(current["reply_count"] or 0) < 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu başvuru için henüz doğrulanmış bir Gmail yanıtı yok.",
        )
    try:
        result = get_latest_reply_content(
            message_id=message_id,
            thread_id=current["gmail_thread_id"],
        )
    except GmailNotConnectedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    except GmailReadError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    return ReplyContentResponse(
        from_=result.sender,
        subject=result.subject,
        received_at=result.received_at,
        body_text=result.body_text,
        gmail_thread_id=result.thread_id,
    )
