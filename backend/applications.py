import sqlite3
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.application_semantics import FILTER_SQL, FILTER_STATUSES
from backend.database import get_connection
from backend.outreach import DraftResponse, _row_to_draft
from backend.status_history import (
    add_status_history,
    is_correction_target,
    status_history_rows,
)
from backend.services.gmail_service import (
    GmailNotConnectedError,
    GmailReadError,
    GmailSendError,
    check_thread_replies,
    get_latest_reply_content,
    send_thread_follow_up,
)
from backend.services.follow_up import (
    FollowUpGenerationError,
    evaluate_follow_up,
    generate_follow_up,
)
from backend.services.application_analytics import calculate_application_analytics
from backend.services.reply_intelligence import (
    SUGGESTED_STATUSES,
    ReplyAnalysisError,
    analyze_reply,
)


router = APIRouter(prefix="/applications", tags=["applications"])
follow_up_send_lock = Lock()

ApplicationStatus = Literal[
    "draft", "sent", "failed", "replied", "interview", "rejected", "offer"
]
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
    follow_up_disabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if (
            self.status is None
            and self.notes is None
            and self.follow_up_disabled is None
        ):
            raise ValueError("Durum veya not alanlarından en az biri gereklidir.")
        return self


class StatusHistoryResponse(BaseModel):
    id: int
    application_id: int
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    source: Literal["system", "gmail", "user", "ai_confirmed", "user_correction"]
    note: str
    changed_at: str


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


class ReplyAnalysisResponse(BaseModel):
    classification: str
    suggested_status: ApplicationStatus
    confidence: float
    reason: str
    analyzed_at: str


class ReplyAnalysisDecision(BaseModel):
    action: Literal["confirm", "change", "ignore"]
    status: ApplicationStatus | None = None

    @model_validator(mode="after")
    def require_override_status(self):
        if self.action == "change" and self.status is None:
            raise ValueError("Değiştirme işlemi için durum gereklidir.")
        return self


class FollowUpSettingsResponse(BaseModel):
    follow_up_enabled: bool
    follow_up_after_days: int
    max_follow_ups: int


class FollowUpSettingsUpdate(BaseModel):
    follow_up_enabled: bool
    follow_up_after_days: Literal[3, 5, 7, 10, 14]
    max_follow_ups: Literal[0, 1, 2]


class FollowUpEligibilityResponse(BaseModel):
    eligible: bool
    reason_code: str
    reason: str
    days_remaining: int


class FollowUpDraftResponse(BaseModel):
    recipient: str
    subject: str
    body: str
    original_application_date: str
    follow_up_count: int


class FollowUpSendRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)
    confirm_send: bool = False

    @field_validator("subject", "body")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class AnalyticsCounts(BaseModel):
    total: int
    draft: int
    sent: int
    replied: int
    interview_reached: int
    rejected: int
    failed: int
    offer_reached: int
    waiting_for_reply: int
    follow_up_due: int


class AnalyticsRates(BaseModel):
    reply_rate: float | None
    reply_to_interview_rate: float | None
    application_to_interview_rate: float | None
    interview_to_offer_rate: float | None


class AnalyticsTiming(BaseModel):
    average_reply_time_hours: float | None
    median_reply_time_hours: float | None
    average_time_to_interview_hours: float | None


class AnalyticsDataQuality(BaseModel):
    applications_with_full_history: int
    baseline_only_migrated_records: int


class ApplicationAnalyticsResponse(BaseModel):
    counts: AnalyticsCounts
    rates: AnalyticsRates
    timing: AnalyticsTiming
    data_quality: AnalyticsDataQuality


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


def _get_follow_up_settings() -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM follow_up_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Follow-up ayarları bulunamadı.",
        )
    return row


def _persist_reply_result(application_id: int, current_status: str, result) -> None:
    next_status = (
        "replied" if result.has_reply and current_status == "sent" else current_status
    )
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
        if next_status != current_status:
            add_status_history(
                connection,
                application_id,
                current_status,
                next_status,
                "gmail",
                "Gmail yanıtı tespit edildi.",
                now,
            )


@router.get("/follow-up/settings", response_model=FollowUpSettingsResponse)
def get_follow_up_settings() -> FollowUpSettingsResponse:
    row = _get_follow_up_settings()
    return FollowUpSettingsResponse(
        follow_up_enabled=bool(row["follow_up_enabled"]),
        follow_up_after_days=row["follow_up_after_days"],
        max_follow_ups=row["max_follow_ups"],
    )


@router.put("/follow-up/settings", response_model=FollowUpSettingsResponse)
def update_follow_up_settings(
    update: FollowUpSettingsUpdate,
) -> FollowUpSettingsResponse:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE follow_up_settings
            SET follow_up_enabled = ?, follow_up_after_days = ?,
                max_follow_ups = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                int(update.follow_up_enabled),
                update.follow_up_after_days,
                update.max_follow_ups,
                now,
            ),
        )
    return get_follow_up_settings()


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
        query += f" WHERE {FILTER_SQL[application_status]}"
    query += " ORDER BY COALESCE(sent_at, created_at) DESC, id DESC"

    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_draft(row) for row in rows]


@router.get("/analytics/summary", response_model=ApplicationAnalyticsResponse)
def get_application_analytics() -> ApplicationAnalyticsResponse:
    with get_connection() as connection:
        analytics = calculate_application_analytics(connection)
    return ApplicationAnalyticsResponse(**analytics)


@router.get("/{application_id}", response_model=DraftResponse)
def get_application(application_id: int) -> DraftResponse:
    return _row_to_draft(_get_application(application_id))


@router.get(
    "/{application_id}/history", response_model=list[StatusHistoryResponse]
)
def get_application_history(application_id: int) -> list[StatusHistoryResponse]:
    _get_application(application_id)
    with get_connection() as connection:
        rows = status_history_rows(connection, application_id)
    return [StatusHistoryResponse(**dict(row)) for row in rows]


@router.patch("/{application_id}", response_model=DraftResponse)
def update_application(
    application_id: int, update: ApplicationUpdate
) -> DraftResponse:
    current = _get_application(application_id)
    next_status = update.status or current["status"]
    correction = False

    if update.status is not None and update.status != current["status"]:
        allowed = MANUAL_TRANSITIONS.get(current["status"], set())
        with get_connection() as connection:
            correction = is_correction_target(
                connection, application_id, update.status
            )
        if update.status not in allowed and not correction:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"'{current['status']}' durumundan '{update.status}' "
                    "durumuna manuel geçiş yapılamaz."
                ),
            )

    notes = current["notes"] if update.notes is None else update.notes.strip()
    follow_up_disabled = (
        current["follow_up_disabled"]
        if update.follow_up_disabled is None
        else int(update.follow_up_disabled)
    )
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET status = ?, notes = ?, follow_up_disabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, notes, follow_up_disabled, now, application_id),
        )
        if next_status != current["status"]:
            add_status_history(
                connection,
                application_id,
                current["status"],
                next_status,
                "user_correction" if correction else "user",
                (
                    "Kullanıcı önceki durum seçimini düzeltti."
                    if correction
                    else "Kullanıcı durumu manuel güncelledi."
                ),
                now,
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

    _persist_reply_result(application_id, current["status"], result)
    with get_connection() as connection:
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


@router.post(
    "/{application_id}/reply-analysis", response_model=ReplyAnalysisResponse
)
def analyze_application_reply(application_id: int) -> ReplyAnalysisResponse:
    current = _get_application(application_id)
    if not current["replied_at"] or int(current["reply_count"] or 0) < 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analiz için doğrulanmış bir Gmail yanıtı bulunmuyor.",
        )
    try:
        content = get_latest_reply_content(
            message_id=current["gmail_message_id"],
            thread_id=current["gmail_thread_id"],
        )
        analysis = analyze_reply(content.body_text)
    except GmailNotConnectedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    except GmailReadError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    except ReplyAnalysisError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error

    analyzed_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET ai_reply_classification = ?, ai_reply_confidence = ?,
                ai_reply_reason = ?, ai_reply_analyzed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                analysis.classification,
                analysis.confidence,
                analysis.reason,
                analyzed_at,
                analyzed_at,
                application_id,
            ),
        )
    return ReplyAnalysisResponse(
        classification=analysis.classification,
        suggested_status=analysis.suggested_status,
        confidence=analysis.confidence,
        reason=analysis.reason,
        analyzed_at=analyzed_at,
    )


@router.post(
    "/{application_id}/reply-analysis/decision", response_model=DraftResponse
)
def decide_reply_analysis(
    application_id: int, decision: ReplyAnalysisDecision
) -> DraftResponse:
    current = _get_application(application_id)
    classification = current["ai_reply_classification"]
    if not classification:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onaylanacak bir AI yanıt değerlendirmesi bulunmuyor.",
        )
    if decision.action == "ignore":
        return _row_to_draft(current)

    selected_status = (
        SUGGESTED_STATUSES.get(classification)
        if decision.action == "confirm"
        else decision.status
    )
    if selected_status is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Geçerli bir başvuru durumu seçilmelidir.",
        )
    if selected_status != current["status"]:
        allowed = MANUAL_TRANSITIONS.get(current["status"], set())
        with get_connection() as connection:
            correction = is_correction_target(
                connection, application_id, selected_status
            )
        if selected_status not in allowed and not correction:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Seçilen durum geçişine izin verilmiyor.",
            )
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                "UPDATE outreach SET status = ?, updated_at = ? WHERE id = ?",
                (selected_status, now, application_id),
            )
            source = "ai_confirmed" if decision.action == "confirm" else (
                "user_correction" if correction else "user"
            )
            note = {
                "ai_confirmed": "AI önerisi kullanıcı tarafından onaylandı.",
                "user_correction": "Kullanıcı önceki durum seçimini düzeltti.",
                "user": "Kullanıcı durumu manuel güncelledi.",
            }[source]
            add_status_history(
                connection,
                application_id,
                current["status"],
                selected_status,
                source,
                note,
                now,
            )
    return get_application(application_id)


@router.get(
    "/{application_id}/follow-up-eligibility",
    response_model=FollowUpEligibilityResponse,
)
def get_follow_up_eligibility(application_id: int) -> FollowUpEligibilityResponse:
    application = dict(_get_application(application_id))
    settings = dict(_get_follow_up_settings())
    result = evaluate_follow_up(application, settings)
    return FollowUpEligibilityResponse(**result.__dict__)


@router.post(
    "/{application_id}/follow-up-draft", response_model=FollowUpDraftResponse
)
def generate_application_follow_up(application_id: int) -> FollowUpDraftResponse:
    application = dict(_get_application(application_id))
    eligibility = evaluate_follow_up(application, dict(_get_follow_up_settings()))
    if not eligibility.eligible:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=eligibility.reason
        )
    with get_connection() as connection:
        profile = connection.execute(
            "SELECT name FROM user_profile WHERE id = 1"
        ).fetchone()
    if profile is None or not str(profile["name"] or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Follow-up için kullanıcı adı bulunan bir profil gereklidir.",
        )
    try:
        draft = generate_follow_up(application, str(profile["name"]).strip())
    except FollowUpGenerationError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    return FollowUpDraftResponse(
        recipient=application["recipient_email"],
        subject=draft.subject,
        body=draft.body,
        original_application_date=application["sent_at"],
        follow_up_count=application["follow_up_count"],
    )


@router.post("/{application_id}/follow-up-send", response_model=DraftResponse)
def send_application_follow_up(
    application_id: int, request: FollowUpSendRequest
) -> DraftResponse:
    if request.confirm_send is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Follow-up gönderimi için açık onay gereklidir.",
        )
    with follow_up_send_lock:
        current = _get_application(application_id)
        application = dict(current)
        eligibility = evaluate_follow_up(application, dict(_get_follow_up_settings()))
        if not eligibility.eligible:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=eligibility.reason
            )
        try:
            recipient = validate_email(
                application["recipient_email"], check_deliverability=False
            ).normalized
        except EmailNotValidError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Alıcı e-posta adresi geçersiz.",
            ) from error

        try:
            reply_check = check_thread_replies(
                message_id=application["gmail_message_id"],
                thread_id=application["gmail_thread_id"],
            )
        except GmailNotConnectedError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error
        except GmailReadError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

        if reply_check.has_reply:
            _persist_reply_result(application_id, current["status"], reply_check)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Bu başvuruya yeni bir yanıt geldiği için follow-up "
                    "gönderimi iptal edildi."
                ),
            )
        threaded_subject = request.subject
        if not threaded_subject.casefold().startswith("re:"):
            threaded_subject = f"Re: {application['subject']}"
        try:
            send_result = send_thread_follow_up(
                recipient=recipient,
                subject=threaded_subject,
                body=request.body,
                original_message_id=application["gmail_message_id"],
                thread_id=application["gmail_thread_id"],
            )
        except (GmailNotConnectedError, GmailSendError) as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE outreach
                SET follow_up_count = follow_up_count + 1,
                    last_follow_up_at = ?,
                    last_follow_up_gmail_message_id = ?, updated_at = ?
                WHERE id = ? AND status = 'sent'
                """,
                (now, send_result.message_id, now, application_id),
            )
    return get_application(application_id)
