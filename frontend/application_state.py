from backend.application_semantics import application_metrics


def should_show_ai_approval(
    current_status: str,
    proposed_status: str,
    allowed_statuses: list[str],
) -> bool:
    return proposed_status != current_status and proposed_status in allowed_statuses


def format_percentage(value: float | None) -> str:
    return "—" if value is None else f"%{value:.1f}"


def format_duration_hours(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 24:
        return f"{value:.1f} saat"
    return f"{value / 24:.1f} gün"
