from backend.application_semantics import application_metrics


def should_show_ai_approval(
    current_status: str,
    proposed_status: str,
    allowed_statuses: list[str],
) -> bool:
    return proposed_status != current_status and proposed_status in allowed_statuses
