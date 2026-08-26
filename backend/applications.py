import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from backend.database import get_connection
from backend.outreach import DraftResponse, _row_to_draft


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
