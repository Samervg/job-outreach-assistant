import sqlite3
from datetime import datetime, timezone
from statistics import mean, median

from backend.application_semantics import has_reply, is_sent
from backend.services.follow_up import evaluate_follow_up
from backend.status_history import first_reached_at, has_ever_reached, status_history_rows


BASELINE_MIGRATION_NOTE = "Mevcut kayıt history sistemine aktarıldı."


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _duration_hours(start_value: str | None, end_value: str | None) -> float | None:
    start = _parse_timestamp(start_value)
    end = _parse_timestamp(end_value)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 3600


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _baseline_only(rows: list[sqlite3.Row]) -> bool:
    return (
        len(rows) == 1
        and rows[0]["from_status"] is None
        and rows[0]["source"] == "system"
        and rows[0]["note"] == BASELINE_MIGRATION_NOTE
    )


def calculate_application_analytics(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict:
    """Calculate deterministic, correction-aware analytics from persisted data."""
    applications = [
        dict(row)
        for row in connection.execute("SELECT * FROM outreach ORDER BY id").fetchall()
    ]
    settings_row = connection.execute(
        "SELECT * FROM follow_up_settings WHERE id = 1"
    ).fetchone()
    settings = dict(settings_row) if settings_row is not None else None

    sent_count = 0
    replied_count = 0
    interview_count = 0
    offer_count = 0
    waiting_count = 0
    follow_up_due_count = 0
    full_history_count = 0
    baseline_only_count = 0
    reply_durations: list[float] = []
    interview_durations: list[float] = []

    for application in applications:
        application_id = int(application["id"])
        history = status_history_rows(connection, application_id)
        baseline_only = _baseline_only(history)
        if baseline_only:
            baseline_only_count += 1
        elif history:
            full_history_count += 1

        actually_sent = is_sent(application)
        actually_replied = has_reply(application)
        interview_reached = has_ever_reached(
            connection, application_id, "interview"
        )
        offer_reached = has_ever_reached(connection, application_id, "offer")

        sent_count += int(actually_sent)
        replied_count += int(actually_replied)
        interview_count += int(interview_reached)
        offer_count += int(offer_reached)

        if (
            actually_sent
            and not actually_replied
            and application["status"] == "sent"
        ):
            waiting_count += 1

        if settings is not None and evaluate_follow_up(
            application, settings, now=now
        ).eligible:
            follow_up_due_count += 1

        if actually_sent and actually_replied:
            first_reply_at = None
            if not baseline_only and has_ever_reached(
                connection, application_id, "replied"
            ):
                first_reply_at = first_reached_at(
                    connection, application_id, "replied"
                )
            first_reply_at = first_reply_at or application.get("replied_at")
            duration = _duration_hours(application.get("sent_at"), first_reply_at)
            if duration is not None:
                reply_durations.append(duration)

        if actually_sent and interview_reached and not baseline_only:
            interview_at = first_reached_at(
                connection, application_id, "interview"
            )
            duration = _duration_hours(application.get("sent_at"), interview_at)
            if duration is not None:
                interview_durations.append(duration)

    return {
        "counts": {
            "total": len(applications),
            "draft": sum(item["status"] == "draft" for item in applications),
            "sent": sent_count,
            "replied": replied_count,
            "interview_reached": interview_count,
            "rejected": sum(item["status"] == "rejected" for item in applications),
            "failed": sum(item["status"] == "failed" for item in applications),
            "offer_reached": offer_count,
            "waiting_for_reply": waiting_count,
            "follow_up_due": follow_up_due_count,
        },
        "rates": {
            "reply_rate": _ratio(replied_count, sent_count),
            "reply_to_interview_rate": _ratio(interview_count, replied_count),
            "application_to_interview_rate": _ratio(interview_count, sent_count),
            "interview_to_offer_rate": _ratio(offer_count, interview_count),
        },
        "timing": {
            "average_reply_time_hours": (
                round(mean(reply_durations), 2) if reply_durations else None
            ),
            "median_reply_time_hours": (
                round(median(reply_durations), 2) if reply_durations else None
            ),
            "average_time_to_interview_hours": (
                round(mean(interview_durations), 2)
                if interview_durations
                else None
            ),
        },
        "data_quality": {
            "applications_with_full_history": full_history_count,
            "baseline_only_migrated_records": baseline_only_count,
        },
    }
