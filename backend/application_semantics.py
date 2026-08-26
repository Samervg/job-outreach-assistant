from collections.abc import Mapping


FILTER_SQL = {
    "draft": "status = 'draft'",
    "sent": "sent_at IS NOT NULL",
    "failed": "status = 'failed'",
    "replied": "(reply_count > 0 OR replied_at IS NOT NULL)",
    "interview": "status = 'interview'",
    "rejected": "status = 'rejected'",
    "offer": "status = 'offer'",
}
FILTER_STATUSES = set(FILTER_SQL)


def is_sent(application: Mapping) -> bool:
    return application.get("sent_at") is not None


def has_reply(application: Mapping) -> bool:
    return (
        int(application.get("reply_count") or 0) > 0
        or application.get("replied_at") is not None
    )


def has_status(application: Mapping, status: str) -> bool:
    return application.get("status") == status


def application_matches_filter(application: Mapping, filter_name: str) -> bool:
    if filter_name == "all":
        return True
    if filter_name == "sent":
        return is_sent(application)
    if filter_name == "replied":
        return has_reply(application)
    return filter_name in FILTER_STATUSES and has_status(application, filter_name)


def application_metrics(applications: list[Mapping]) -> dict[str, int]:
    current_status_counts = {
        status: sum(has_status(item, status) for item in applications)
        for status in FILTER_STATUSES
    }
    return {
        "total": len(applications),
        **current_status_counts,
        "sent_total": sum(is_sent(item) for item in applications),
        "reply_total": sum(has_reply(item) for item in applications),
    }
