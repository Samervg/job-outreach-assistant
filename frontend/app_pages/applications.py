import streamlit as st

from application_state import application_metrics, should_show_ai_approval
from api_client import (
    analyze_application_reply,
    decide_reply_analysis,
    generate_follow_up_draft,
    get_application,
    get_application_reply_content,
    get_follow_up_eligibility,
    get_follow_up_settings,
    list_applications,
    send_follow_up,
    sync_application_reply,
    update_application,
    update_follow_up_settings,
)


STATUS_LABELS = {
    "all": "Tümü",
    "draft": "Taslak",
    "sent": "Gönderildi",
    "failed": "Başarısız",
    "replied": "Yanıt geldi",
    "interview": "Mülakat",
    "rejected": "Olumsuz",
    "offer": "Teklif",
}
MANUAL_OPTIONS = {
    "sent": ["sent", "replied", "interview", "rejected", "offer"],
    "replied": ["replied", "interview", "rejected", "offer"],
    "interview": ["interview", "rejected", "offer"],
    "rejected": ["rejected"],
    "offer": ["offer"],
}
CLASSIFICATION_LABELS = {
    "positive_interest": "Olumlu ilgi",
    "interview": "Mülakat",
    "rejection": "Olumsuz",
    "more_information": "Ek bilgi talebi",
    "neutral": "Nötr",
    "automated_reply": "Otomatik yanıt",
    "unclear": "Belirsiz",
}


st.header("Başvurular")

settings, settings_error = get_follow_up_settings()
if settings_error:
    st.warning(settings_error)
else:
    with st.expander("Follow-up ayarları"):
        with st.form("follow_up_settings"):
            settings_enabled = st.checkbox(
                "Follow-up önerilerini etkinleştir",
                value=settings["follow_up_enabled"],
            )
            settings_days = st.selectbox(
                "Kaç gün sonra",
                options=[3, 5, 7, 10, 14],
                index=[3, 5, 7, 10, 14].index(settings["follow_up_after_days"]),
            )
            settings_max = st.selectbox(
                "Maksimum follow-up",
                options=[0, 1, 2],
                index=[0, 1, 2].index(settings["max_follow_ups"]),
            )
            save_settings = st.form_submit_button("Ayarları kaydet")
        if save_settings:
            _, save_settings_error = update_follow_up_settings(
                settings_enabled, settings_days, settings_max
            )
            if save_settings_error:
                st.error(save_settings_error)
            else:
                st.success("Follow-up ayarları kaydedildi.")
                st.rerun()

all_applications, all_error = list_applications("all")
if all_error:
    st.error(all_error)
    st.stop()

all_applications = all_applications or []
counts = application_metrics(all_applications)

first_metrics = st.columns(4)
first_metrics[0].metric("Toplam", counts["total"])
first_metrics[1].metric("Taslak", counts["draft"])
first_metrics[2].metric("Gönderilen", counts["sent_total"])
first_metrics[3].metric("Yanıt gelen", counts["reply_total"])
second_metrics = st.columns(3)
second_metrics[0].metric("Mülakat", counts["interview"])
second_metrics[1].metric("Olumsuz", counts["rejected"])
second_metrics[2].metric("Teklif", counts["offer"])

selected_filter = st.segmented_control(
    "Duruma göre filtrele",
    options=["all", "draft", "sent", "failed", "replied", "interview", "rejected", "offer"],
    format_func=lambda value: STATUS_LABELS[value],
    default="all",
    key="applications_status_filter",
)

applications, applications_error = list_applications(selected_filter or "all")
if applications_error:
    st.error(applications_error)
    st.stop()

applications = applications or []
if not applications:
    st.info("Bu filtreye uygun başvuru bulunamadı.")
    st.stop()

overview = [
    {
        "Şirket": item["company_name"],
        "Pozisyon": item["position"],
        "Alıcı": item["recipient_email"],
        "Durum": STATUS_LABELS.get(item["status"], item["status"]),
        "Gönderim tarihi": item.get("sent_at") or "—",
    }
    for item in applications
]
st.dataframe(overview, hide_index=True)

application_by_id = {item["id"]: item for item in applications}
if st.session_state.get("selected_application_id") not in application_by_id:
    st.session_state["selected_application_id"] = next(iter(application_by_id))
selected_id = st.selectbox(
    "Başvuru detayı",
    options=list(application_by_id),
    format_func=lambda application_id: (
        f"#{application_id} — {application_by_id[application_id]['company_name']} — "
        f"{application_by_id[application_id]['position']}"
    ),
    key="selected_application_id",
)
if st.session_state.get("follow_up_draft_application_id") != selected_id:
    st.session_state.pop("follow_up_draft", None)
    st.session_state["follow_up_draft_application_id"] = selected_id

application, application_error = get_application(selected_id)
if application_error:
    st.error(application_error)
    st.stop()

with st.container(border=True):
    st.write(f"**Şirket:** {application['company_name']}")
    st.write(f"**Pozisyon:** {application['position']}")
    st.write(f"**Alıcı:** {application['recipient_email']}")
    st.write(f"**Durum:** {STATUS_LABELS[application['status']]}")
    st.write(f"**Konu:** {application['subject']}")
    st.write("**E-posta metni:**")
    st.text(application["body"])
    if application.get("sent_at"):
        st.write(f"**Gönderim zamanı (UTC):** {application['sent_at']}")
    if application.get("gmail_message_id"):
        st.write(f"**Gmail mesaj kimliği:** {application['gmail_message_id']}")
    if application.get("replied_at"):
        st.success("Yanıt var")
        st.write(f"**Son yanıt zamanı (UTC):** {application['replied_at']}")
        st.write(f"**Gönderen:** {application.get('latest_reply_from') or '—'}")
        if application.get("latest_reply_subject"):
            st.write(f"**Yanıt konusu:** {application['latest_reply_subject']}")
        if application.get("latest_reply_snippet"):
            st.write(f"**Kısa yanıt:** {application['latest_reply_snippet']}")
        st.caption(f"Tespit edilen dış yanıt: {application.get('reply_count', 0)}")
    if application.get("error_message"):
        st.error(f"Gönderim hatası: {application['error_message']}")

eligible_for_reply_sync = (
    application["status"] in {"sent", "replied", "interview", "rejected", "offer"}
    and bool(application.get("gmail_message_id"))
)
if eligible_for_reply_sync and st.button(
    "Yanıtı kontrol et",
    icon=":material/mark_email_read:",
    key=f"sync_reply_{selected_id}",
):
    with st.spinner("Yalnızca bu başvurunun Gmail konuşması kontrol ediliyor..."):
        sync_result, sync_error = sync_application_reply(selected_id)
    if sync_error:
        st.error(sync_error)
    elif sync_result["has_reply"]:
        st.success(
            f"Yanıt bulundu ({sync_result['reply_count']}). "
            "Başvuru bilgileri güncellendi."
        )
        st.rerun()
    else:
        st.info("Henüz yanıt yok.")

if application.get("replied_at") and st.button(
    "Yanıt içeriğini göster",
    icon=":material/mail:",
    key=f"show_reply_content_{selected_id}",
):
    with st.spinner("Yalnızca bu başvurunun doğrulanmış Gmail konuşması okunuyor..."):
        reply_content, reply_content_error = get_application_reply_content(selected_id)
    if reply_content_error:
        st.error(reply_content_error)
    else:
        with st.container(border=True):
            st.write(f"**Gönderen:** {reply_content.get('from') or '—'}")
            st.write(f"**Tarih (UTC):** {reply_content.get('received_at') or '—'}")
            st.write(f"**Konu:** {reply_content.get('subject') or '—'}")
            st.text_area(
                "Yanıt metni",
                value=reply_content["body_text"],
                height=220,
                disabled=True,
                key=f"reply_body_{selected_id}",
            )

if application.get("replied_at"):
    if st.button(
        "Yanıtı analiz et",
        icon=":material/psychology:",
        key=f"analyze_reply_{selected_id}",
    ):
        with st.spinner("Yanıt yalnızca yerel Ollama ile değerlendiriliyor..."):
            _, analysis_error = analyze_application_reply(selected_id)
        if analysis_error:
            st.error(analysis_error)
        else:
            st.success("Yanıt değerlendirildi; durum henüz değiştirilmedi.")
            st.rerun()

    if application.get("ai_reply_classification"):
        with st.container(border=True):
            classification = application["ai_reply_classification"]
            suggested_status = {
                "interview": "interview",
                "rejection": "rejected",
            }.get(classification, "replied")
            st.write(
                "**AI değerlendirmesi:** "
                f"{CLASSIFICATION_LABELS.get(classification, classification)}"
            )
            st.write(
                f"**Güven:** %{round((application.get('ai_reply_confidence') or 0) * 100)}"
            )
            st.write(f"**Neden:** {application.get('ai_reply_reason') or '—'}")
            st.caption("AI yalnızca öneri sunar; başvuru durumunu kendisi değiştirmez.")

            allowed_statuses = MANUAL_OPTIONS.get(
                application["status"], [application["status"]]
            )
            show_approval = should_show_ai_approval(
                application["status"], suggested_status, allowed_statuses
            )
            confirm_analysis = False
            with st.container(horizontal=True):
                if show_approval:
                    confirm_analysis = st.button(
                        f"Onayla ({STATUS_LABELS[suggested_status]})",
                        key=f"confirm_analysis_{selected_id}",
                        type="primary",
                    )
                ignore_analysis = st.button(
                    "Yok say", key=f"ignore_analysis_{selected_id}"
                )
            if not show_approval:
                st.caption(
                    "AI önerisi mevcut durumla aynı; ayrıca onaylanması gerekmiyor."
                )
            if confirm_analysis or ignore_analysis:
                action = "confirm" if confirm_analysis else "ignore"
                _, decision_error = decide_reply_analysis(selected_id, action)
                if decision_error:
                    st.error(decision_error)
                else:
                    st.success(
                        "Öneri onaylandı."
                        if confirm_analysis
                        else "Öneri yok sayıldı; durum değiştirilmedi."
                    )
                    st.rerun()

            override_options = allowed_statuses
            with st.form(f"analysis_override_{selected_id}"):
                override_status = st.selectbox(
                    "Farklı bir durum seç",
                    options=override_options,
                    format_func=lambda value: STATUS_LABELS[value],
                )
                override_submit = st.form_submit_button("Değiştir")
            if override_submit:
                _, override_error = decide_reply_analysis(
                    selected_id, "change", override_status
                )
                if override_error:
                    st.error(override_error)
                else:
                    st.success("Seçtiğiniz durum kaydedildi.")
                    st.rerun()

eligibility, eligibility_error = get_follow_up_eligibility(selected_id)
if eligibility_error:
    st.warning(eligibility_error)
else:
    if eligibility["eligible"]:
        st.success("Follow-up uygun")
    elif eligibility["reason_code"] == "waiting_period":
        st.info(eligibility["reason"])
    else:
        st.caption(f"Follow-up önerilmiyor: {eligibility['reason']}")

    if eligibility["eligible"]:
        with st.container(horizontal=True):
            create_follow_up = st.button(
                "Follow-up taslağı oluştur",
                icon=":material/edit_note:",
                key=f"create_follow_up_{selected_id}",
            )
            regenerate_follow_up = st.button(
                "Yeniden oluştur",
                key=f"regenerate_follow_up_{selected_id}",
                disabled="follow_up_draft" not in st.session_state,
            )
        if create_follow_up or regenerate_follow_up:
            with st.spinner("Kısa follow-up taslağı yerel Ollama ile hazırlanıyor..."):
                draft, draft_error = generate_follow_up_draft(selected_id)
            if draft_error:
                st.error(draft_error)
                st.session_state.pop("follow_up_draft", None)
            else:
                st.session_state["follow_up_draft"] = draft

        follow_up_draft = st.session_state.get("follow_up_draft")
        if follow_up_draft:
            with st.form(f"follow_up_review_{selected_id}"):
                st.write(f"**Alıcı:** {follow_up_draft['recipient']}")
                st.write(
                    f"**Orijinal başvuru tarihi:** "
                    f"{follow_up_draft['original_application_date']}"
                )
                st.write(
                    f"**Önceki follow-up sayısı:** {follow_up_draft['follow_up_count']}"
                )
                follow_up_subject = st.text_input(
                    "Konu", value=follow_up_draft["subject"], max_chars=300
                )
                follow_up_body = st.text_area(
                    "Follow-up metni", value=follow_up_draft["body"], height=220
                )
                explicit_confirmation = st.checkbox(
                    "Bu follow-up e-postasını şimdi göndermeyi onaylıyorum."
                )
                send_follow_up_submit = st.form_submit_button(
                    "Onayla ve gönder",
                    type="primary",
                    disabled=not explicit_confirmation,
                )
                cancel_follow_up = st.form_submit_button("İptal")
            if cancel_follow_up:
                st.session_state.pop("follow_up_draft", None)
                st.rerun()
            if send_follow_up_submit:
                with st.spinner("Gmail thread yeniden kontrol ediliyor..."):
                    _, send_error = send_follow_up(
                        selected_id,
                        follow_up_subject,
                        follow_up_body,
                        True,
                    )
                if send_error:
                    st.error(send_error)
                else:
                    st.session_state.pop("follow_up_draft", None)
                    st.success("Follow-up aynı Gmail konuşmasında gönderildi.")
                    st.rerun()

with st.form(f"application_tracking_{selected_id}"):
    current_status = application["status"]
    status_options = MANUAL_OPTIONS.get(current_status, [current_status])
    tracked_status = st.selectbox(
        "Başvuru durumu",
        options=status_options,
        index=0,
        format_func=lambda value: STATUS_LABELS[value],
        disabled=current_status in {"draft", "failed", "rejected", "offer"},
    )
    notes = st.text_area(
        "Özel not",
        value=application.get("notes") or "",
        max_chars=2000,
        height=140,
        placeholder="Örn. İlk görüşme 2 Eylül.",
    )
    follow_up_disabled = st.checkbox(
        "Bu başvuru için follow-up istemiyorum",
        value=bool(application.get("follow_up_disabled")),
    )
    submitted = st.form_submit_button("Takibi kaydet", type="primary")

if submitted:
    updated, update_error = update_application(
        selected_id,
        application_status=(
            tracked_status if tracked_status != current_status else None
        ),
        notes=notes,
        follow_up_disabled=follow_up_disabled,
    )
    if update_error:
        st.error(update_error)
    else:
        st.success("Başvuru takibi kaydedildi.")
        st.rerun()

if application["status"] in {"draft", "failed"}:
    st.caption(
        "Bu kayıt henüz gönderim sonrası takip aşamasında değil. "
        "Durum, Gmail gönderimi başarıyla tamamlandığında otomatik güncellenir."
    )
elif application["status"] in {"rejected", "offer"}:
    st.caption("Bu başvuru son duruma ulaştı; özel notlar güncellenebilir.")
