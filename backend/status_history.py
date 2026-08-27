import sqlite3
from datetime import datetime, timezone


HISTORY_SOURCES = {
    "system",
    "gmail",
    "user",
    "ai_confirmed",
    "user_correction",
}
CORRECTION_STATUSES = {"sent", "replied", "interview", "rejected", "offer"}


def add_status_history(
    connection: sqlite3.Connection,
    application_id: int,
    from_status: str | None,
    to_status: str,
    source: str,
    note: str,
    changed_at: str | None = None,
) -> bool:
    """Append one real transition using the caller's open transaction."""
    if from_status == to_status:
        return False
    if source not in HISTORY_SOURCES:
        raise ValueError("Geçersiz durum geçmişi kaynağı.")
    connection.execute(
        """
        INSERT INTO application_status_history (
            application_id, from_status, to_status, source, note, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            application_id,
            from_status,
            to_status,
            source,
            note,
            changed_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    return True


def status_history_rows(
    connection: sqlite3.Connection, application_id: int
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM application_status_history
        WHERE application_id = ?
        ORDER BY changed_at ASC, id ASC
        """,
        (application_id,),
    ).fetchall()


def _confirmed_milestones(rows: list[sqlite3.Row]) -> dict[str, str]:
    """Return correction-aware reached stages.

    A user_correction removes the stage it explicitly leaves. Other earlier
    milestones remain confirmed; no missing intermediate stages are inferred.
    """
    milestones: dict[str, str] = {}
    for row in rows:
        if row["source"] == "user_correction" and row["from_status"]:
            milestones.pop(row["from_status"], None)
        milestones.setdefault(row["to_status"], row["changed_at"])
    return milestones


def has_ever_reached(
    connection: sqlite3.Connection, application_id: int, target_status: str
) -> bool:
    return target_status in _confirmed_milestones(
        status_history_rows(connection, application_id)
    )


def first_reached_at(
    connection: sqlite3.Connection, application_id: int, target_status: str
) -> str | None:
    return _confirmed_milestones(
        status_history_rows(connection, application_id)
    ).get(target_status)


def is_correction_target(
    connection: sqlite3.Connection, application_id: int, target_status: str
) -> bool:
    if target_status not in CORRECTION_STATUSES:
        return False
    return any(
        row["to_status"] == target_status
        for row in status_history_rows(connection, application_id)
    )
