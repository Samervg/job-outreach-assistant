from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlparse


PREVIEW_FORM_KEYS = (
    "import_company_name",
    "import_company_website_edit",
    "import_contact_email",
    "import_career_page",
    "import_contact_page",
    "import_position_override",
)


def normalized_source_url(value: str) -> str:
    return value.strip()


def normalized_target_position(value: str) -> str:
    return " ".join(value.casefold().split())


def _domain(value: str) -> str | None:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname or None


def preview_matches_source_url(preview: dict, source_url: str) -> bool:
    preview_url = str(preview.get("website") or "")
    return bool(_domain(source_url) and _domain(source_url) == _domain(preview_url))


def clear_company_import_state(state: MutableMapping[str, Any]) -> None:
    for key in (
        "company_import_preview",
        "import_preview_source_url",
        "company_import_pending",
        *PREVIEW_FORM_KEYS,
    ):
        state.pop(key, None)


def clear_stale_company_import_state(
    state: MutableMapping[str, Any], current_url: str
) -> None:
    source_url = state.get("import_preview_source_url")
    if source_url is not None and source_url != normalized_source_url(current_url):
        clear_company_import_state(state)


def clear_stale_duplicate_state(
    state: MutableMapping[str, Any], current_position: str
) -> None:
    pending = state.get("company_import_pending")
    if not isinstance(pending, dict):
        return
    pending_position = str(pending.get("target_position") or "")
    if normalized_target_position(pending_position) != normalized_target_position(
        current_position
    ):
        state.pop("company_import_pending", None)


def store_company_import_preview(
    state: MutableMapping[str, Any], source_url: str, preview: dict
) -> bool:
    if not preview_matches_source_url(preview, source_url):
        clear_company_import_state(state)
        return False
    state["company_import_preview"] = preview
    state["import_preview_source_url"] = normalized_source_url(source_url)
    return True


def current_company_import_preview(
    state: MutableMapping[str, Any], current_url: str
) -> dict | None:
    if state.get("import_preview_source_url") != normalized_source_url(current_url):
        return None
    preview = state.get("company_import_preview")
    return preview if isinstance(preview, dict) else None
