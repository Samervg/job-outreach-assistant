from collections.abc import Callable

import streamlit as st


STATUS_META = {
    "draft": ("Taslak", "gray", ":material/edit_note:"),
    "sent": ("Gönderildi", "blue", ":material/send:"),
    "replied": ("Yanıt geldi", "green", ":material/mark_email_read:"),
    "interview": ("Mülakat", "violet", ":material/groups:"),
    "rejected": ("Olumsuz", "red", ":material/cancel:"),
    "offer": ("Teklif", "green", ":material/celebration:"),
    "failed": ("Başarısız", "red", ":material/error:"),
}


def render_page_header(
    title: str,
    subtitle: str,
    *,
    icon: str,
    action_label: str | None = None,
    action_page: str | None = None,
) -> None:
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        st.title(f":material/{icon}: {title}")
        st.caption(subtitle)
    if action_label and action_page:
        with right:
            if st.button(
                action_label,
                type="primary",
                icon=":material/add:",
                width="stretch",
            ):
                st.switch_page(action_page)


def render_section_header(title: str, subtitle: str | None = None) -> None:
    st.subheader(title)
    if subtitle:
        st.caption(subtitle)


def render_metric_card(label: str, value: object, *, help_text: str | None = None) -> None:
    st.metric(label, value, help=help_text, border=True)


def render_status_badge(status: str) -> None:
    label, color, icon = STATUS_META.get(
        status, (status.replace("_", " ").title(), "gray", ":material/info:")
    )
    st.badge(label, color=color, icon=icon)


def render_empty_state(
    title: str,
    message: str,
    *,
    icon: str = "inbox",
    action_label: str | None = None,
    action: Callable[[], None] | None = None,
) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.markdown(f":material/{icon}:")
        st.markdown(f"**{title}**")
        st.caption(message, text_alignment="center")
        if action_label and action and st.button(action_label, type="primary"):
            action()


def render_step(number: int, title: str, description: str) -> None:
    st.markdown(f"#### {number}. {title}")
    st.caption(description)
