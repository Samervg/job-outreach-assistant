import streamlit as st

from application_state import (
    application_metrics,
    format_duration_hours,
    format_percentage,
    should_show_ai_approval,
)
from api_client import (
    analyze_application_reply,
    decide_reply_analysis,
    delete_application,
    generate_follow_up_draft,
    get_application,
    get_application_analytics,
    get_application_history,
    get_application_reply_content,
    get_follow_up_eligibility,
    get_follow_up_settings,
    list_applications,
    send_follow_up,
    sync_application_reply,
    update_application,
    update_follow_up_settings,
)
from ui import (
    render_empty_state,
    render_metric_card,
    render_page_header,
    render_section_header,
    render_status_badge,
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
HISTORY_SOURCE_LABELS = {
    "system": "Sistem",
    "gmail": "Gmail",
    "user": "Kullanıcı",
    "ai_confirmed": "AI önerisi onayı",
    "user_correction": "Kullanıcı düzeltmesi",
}


render_page_header(
    "Başvurular",
    "Başvuru huninizi, yanıtları ve takip gerektiren işleri tek yerden yönetin.",
    icon="dashboard",
    action_label="Yeni başvuru",
    action_page="app_pages/new_application.py",
)

delete_success = st.session_state.pop("application_delete_success", None)
if delete_success:
    st.success(delete_success)

settings, settings_error = get_follow_up_settings()

all_applications, all_error = list_applications("all")
if all_error:
    st.error(all_error)
    st.stop()

all_applications = all_applications or []
counts = application_metrics(all_applications)

analytics, analytics_error = get_application_analytics()
render_section_header(
    "Başvuru özeti",
    "Güncel durumlar ve doğrulanmış başvuru geçmişinden oluşturulur.",
)
primary_values = (
    {
        "sent": counts["sent_total"],
        "replied": counts["reply_total"],
        "interview": counts["interview"],
        "offer": counts["offer"],
    }
    if analytics_error
    else {
        "sent": analytics["counts"]["sent"],
        "replied": analytics["counts"]["replied"],
        "interview": analytics["counts"]["interview_reached"],
        "offer": analytics["counts"]["offer_reached"],
    }
)
primary_metrics = st.columns(3)
with primary_metrics[0]:
    render_metric_card("Toplam başvuru", counts["total"])
with primary_metrics[1]:
    render_metric_card("Gönderilen", primary_values["sent"])
with primary_metrics[2]:
    render_metric_card("Yanıt gelen", primary_values["replied"])
secondary_primary = st.columns(3)
with secondary_primary[0]:
    render_metric_card("Mülakata ulaşan", primary_values["interview"])
with secondary_primary[1]:
    render_metric_card("Olumsuz", counts["rejected"])
with secondary_primary[2]:
    render_metric_card("Teklife ulaşan", primary_values["offer"])

if analytics_error:
    st.warning(analytics_error)
else:
    analytics_counts = analytics["counts"]
    analytics_rates = analytics["rates"]
    analytics_timing = analytics["timing"]
    with st.expander("Dönüşüm ve süre analizi", icon=":material/analytics:"):
        rate_columns = st.columns(4)
        rate_values = [
            ("Yanıt oranı", analytics_rates["reply_rate"]),
            ("Yanıttan mülakata", analytics_rates["reply_to_interview_rate"]),
            ("Başvurudan mülakata", analytics_rates["application_to_interview_rate"]),
            ("Mülakattan teklife", analytics_rates["interview_to_offer_rate"]),
        ]
        for column, (label, value) in zip(rate_columns, rate_values):
            with column:
                render_metric_card(label, format_percentage(value))
        timing_columns = st.columns(4)
        with timing_columns[0]:
            render_metric_card(
                "Ortalama yanıt süresi",
                format_duration_hours(analytics_timing["average_reply_time_hours"]),
            )
        with timing_columns[1]:
            render_metric_card(
                "Ortalama mülakata ulaşma",
                format_duration_hours(analytics_timing["average_time_to_interview_hours"]),
            )
        with timing_columns[2]:
            render_metric_card("Cevap bekleyen", analytics_counts["waiting_for_reply"])
        with timing_columns[3]:
            render_metric_card("Follow-up gereken", analytics_counts["follow_up_due"])
        quality = analytics["data_quality"]
        if quality["baseline_only_migrated_records"]:
            st.caption(
                "Geçmiş takibi sonradan eklenen "
                f"{quality['baseline_only_migrated_records']} kayıt konservatif olarak "
                "değerlendirildi. Tarihsel analizler yeni geçişlerle daha kesin hale gelir."
            )

render_section_header("Başvuru akışı", "Duruma göre filtreleyin ve detayını inceleyin.")
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
    render_empty_state(
        "Bu görünümde başvuru yok",
        "Filtreyi değiştirin veya yeni bir başvuru oluşturun.",
        icon="filter_alt_off",
    )
    st.stop()

for item in applications:
    with st.container(border=True):
        card_columns = st.columns([4, 2, 2], vertical_alignment="center")
        with card_columns[0]:
            st.markdown(f"**{item['company_name']}**")
            st.caption(item["position"])
        with card_columns[1]:
            render_status_badge(item["status"])
        with card_columns[2]:
            st.caption(item.get("sent_at") or item.get("created_at") or "—")
            if item.get("replied_at"):
                st.caption(":material/mark_email_read: Yanıt alındı")
            elif item.get("follow_up_count"):
                st.caption(f":material/update: {item['follow_up_count']} follow-up")

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

history, history_error = get_application_history(selected_id)
history = history or []
eligible_for_reply_sync = (
    application["status"] in {"sent", "replied", "interview", "rejected", "offer"}
    and bool(application.get("gmail_message_id"))
)

render_section_header(
    "Seçili başvuru",
    "Güncel durum, iletişim, follow-up ve geçmiş aynı başvuru bağlamında yönetilir.",
)
overview_tab, communication_tab, follow_up_tab, history_tab = st.tabs(
    ["Genel bakış", "İletişim", "Follow-up", "Geçmiş"]
)

with overview_tab:
    with st.container(border=True):
        detail_columns = st.columns([3, 1])
        with detail_columns[0]:
            st.write(f"**{application['company_name']}**")
            st.caption(f"{application['position']} · {application['recipient_email']}")
        with detail_columns[1]:
            render_status_badge(application["status"])
        date_columns = st.columns(2)
        with date_columns[0]:
            st.write(f"**Oluşturulma:** {application.get('created_at') or '—'}")
        with date_columns[1]:
            st.write(f"**Gönderim:** {application.get('sent_at') or '—'}")
        if application.get("replied_at"):
            st.success(
                f"Yanıt alındı · {application['replied_at']}",
                icon=":material/mark_email_read:",
            )
        elif application.get("error_message"):
            st.error(f"Gönderim hatası: {application['error_message']}")

    st.markdown("#### Durum ve özel notlar")
    with st.form(f"application_tracking_{selected_id}"):
        current_status = application["status"]
        status_options = list(MANUAL_OPTIONS.get(current_status, [current_status]))
        previously_reached = [
            event["to_status"]
            for event in history
            if event["to_status"] != current_status
            and event["to_status"]
            in {"sent", "replied", "interview", "rejected", "offer"}
        ]
        for previous_status in previously_reached:
            if previous_status not in status_options:
                status_options.append(previous_status)
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
        submitted = st.form_submit_button("Takibi kaydet", type="primary")
    if submitted:
        _, update_error = update_application(
            selected_id,
            application_status=(
                tracked_status if tracked_status != current_status else None
            ),
            notes=notes,
        )
        if update_error:
            st.error(update_error)
        else:
            st.success("Başvuru takibi kaydedildi.")
            st.rerun()
    if application["status"] in {"draft", "failed"}:
        st.caption(
            "Bu kayıt henüz gönderim sonrası takip aşamasında değil. Durum, Gmail "
            "gönderimi başarıyla tamamlandığında otomatik güncellenir."
        )
    elif application["status"] in {"rejected", "offer"}:
        st.caption("Bu başvuru son duruma ulaştı; özel notlar güncellenebilir.")

with communication_tab:
    st.markdown("#### Gönderilen e-posta")
    with st.container(border=True):
        st.write(f"**Konu:** {application['subject']}")
        st.text_area(
            "E-posta içeriği",
            value=application["body"],
            height=260,
            disabled=True,
            key=f"application_email_{selected_id}",
        )
        if application.get("gmail_message_id"):
            st.caption(f"Gmail mesaj kimliği: {application['gmail_message_id']}")

    st.markdown("#### Gmail yanıtı")
    with st.container(border=True):
        if application.get("replied_at"):
            st.success("Yanıt var", icon=":material/mark_email_read:")
            st.write(f"**Son yanıt zamanı (UTC):** {application['replied_at']}")
            st.write(f"**Gönderen:** {application.get('latest_reply_from') or '—'}")
            if application.get("latest_reply_subject"):
                st.write(f"**Yanıt konusu:** {application['latest_reply_subject']}")
            if application.get("latest_reply_snippet"):
                st.write(f"**Kısa yanıt:** {application['latest_reply_snippet']}")
            st.caption(f"Tespit edilen dış yanıt: {application.get('reply_count', 0)}")
        elif application.get("error_message"):
            st.error(f"Gönderim hatası: {application['error_message']}")
        else:
            st.caption("Henüz doğrulanmış bir Gmail yanıtı bulunmuyor.")

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
            with st.spinner(
                "Yalnızca bu başvurunun doğrulanmış Gmail konuşması okunuyor..."
            ):
                reply_content, reply_content_error = get_application_reply_content(
                    selected_id
                )
            if reply_content_error:
                st.error(reply_content_error)
            else:
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
        st.markdown("#### AI yanıt değerlendirmesi")
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
                    "**Sınıflandırma:** "
                    f"{CLASSIFICATION_LABELS.get(classification, classification)}"
                )
                st.write(
                    f"**Güven:** %{round((application.get('ai_reply_confidence') or 0) * 100)}"
                )
                st.write(f"**Neden:** {application.get('ai_reply_reason') or '—'}")
                st.write(f"**Önerilen durum:** {STATUS_LABELS[suggested_status]}")
                st.caption(
                    "AI yalnızca öneri sunar; başvuru durumunu kendisi değiştirmez."
                )
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
                with st.form(f"analysis_override_{selected_id}"):
                    override_status = st.selectbox(
                        "Farklı bir durum seç",
                        options=allowed_statuses,
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

with follow_up_tab:
    if settings_error:
        st.warning(settings_error)
    else:
        with st.expander("Genel follow-up ayarları"):
            with st.form("follow_up_settings"):
                settings_enabled = st.checkbox(
                    "Follow-up önerilerini etkinleştir",
                    value=settings["follow_up_enabled"],
                )
                settings_days = st.selectbox(
                    "Kaç gün sonra",
                    options=[3, 5, 7, 10, 14],
                    index=[3, 5, 7, 10, 14].index(
                        settings["follow_up_after_days"]
                    ),
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

    st.markdown("#### Uygunluk ve tercih")
    with st.container(border=True):
        follow_up_columns = st.columns(2)
        with follow_up_columns[0]:
            st.write(f"**Follow-up sayısı:** {application.get('follow_up_count', 0)}")
            st.write(
                f"**Son follow-up:** {application.get('last_follow_up_at') or '—'}"
            )
        with follow_up_columns[1]:
            st.write(
                "**Başvuru tercihi:** "
                + (
                    "Follow-up kapalı"
                    if application.get("follow_up_disabled")
                    else "Follow-up açık"
                )
            )
        eligibility, eligibility_error = get_follow_up_eligibility(selected_id)
        if eligibility_error:
            st.warning(eligibility_error)
        elif eligibility["eligible"]:
            st.success("Follow-up uygun")
        elif eligibility["reason_code"] == "waiting_period":
            st.info(eligibility["reason"])
        else:
            st.caption(f"Follow-up önerilmiyor: {eligibility['reason']}")

        with st.form(f"follow_up_preference_{selected_id}"):
            follow_up_disabled = st.checkbox(
                "Bu başvuru için follow-up istemiyorum",
                value=bool(application.get("follow_up_disabled")),
            )
            save_preference = st.form_submit_button("Tercihi kaydet")
        if save_preference:
            _, preference_error = update_application(
                selected_id, follow_up_disabled=follow_up_disabled
            )
            if preference_error:
                st.error(preference_error)
            else:
                st.success("Follow-up tercihi kaydedildi.")
                st.rerun()

    if not eligibility_error and eligibility["eligible"]:
        st.markdown("#### Taslak oluştur ve gözden geçir")
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
                st.caption(
                    f"Orijinal başvuru: {follow_up_draft['original_application_date']} · "
                    f"Önceki follow-up: {follow_up_draft['follow_up_count']}"
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
                        selected_id, follow_up_subject, follow_up_body, True
                    )
                if send_error:
                    st.error(send_error)
                else:
                    st.session_state.pop("follow_up_draft", None)
                    st.success("Follow-up aynı Gmail konuşmasında gönderildi.")
                    st.rerun()

with history_tab:
    st.markdown("#### Durum geçmişi")
    st.caption("Bu başvurunun doğrulanmış durum değişiklikleri kronolojik sıradadır.")
    if history_error:
        st.warning(history_error)
    elif not history:
        st.caption("Bu başvuru için durum geçmişi bulunmuyor.")
    else:
        for event in history:
            with st.container(border=True):
                from_label = (
                    STATUS_LABELS.get(event["from_status"], event["from_status"])
                    if event.get("from_status")
                    else "Başlangıç"
                )
                to_label = STATUS_LABELS.get(event["to_status"], event["to_status"])
                st.write(f"**{from_label} → {to_label}**")
                st.caption(
                    f"{event['changed_at']} · "
                    f"{HISTORY_SOURCE_LABELS.get(event['source'], event['source'])}"
                )
                if event.get("note"):
                    st.write(event["note"])

if application["status"] in {"draft", "failed"}:
    render_section_header(
        "Tehlikeli işlemler",
        "Yalnızca gönderilmemiş taslak ve başarısız kayıtlar kalıcı olarak silinebilir.",
    )
    confirmation_id = st.session_state.get("delete_application_confirmation_id")
    if confirmation_id is not None and confirmation_id != selected_id:
        st.session_state.pop("delete_application_confirmation_id", None)
        confirmation_id = None
    if confirmation_id != selected_id:
        if st.button(
            "Başvuruyu sil",
            icon=":material/delete:",
            key=f"request_delete_application_{selected_id}",
        ):
            st.session_state["delete_application_confirmation_id"] = selected_id
            st.rerun()
    else:
        st.warning(
            "Bu işlem kalıcıdır. Başvuru ve durum geçmişi geri alınamaz biçimde silinecek."
        )
        with st.container(horizontal=True):
            confirm_delete = st.button(
                "Kalıcı olarak sil",
                type="primary",
                icon=":material/delete_forever:",
                key=f"confirm_delete_application_{selected_id}",
            )
            cancel_delete = st.button(
                "Vazgeç",
                key=f"cancel_delete_application_{selected_id}",
            )
        if cancel_delete:
            st.session_state.pop("delete_application_confirmation_id", None)
            st.rerun()
        if confirm_delete:
            result, delete_error = delete_application(selected_id)
            if delete_error:
                st.error(delete_error)
            elif result and result.get("deleted"):
                st.session_state.pop("delete_application_confirmation_id", None)
                st.session_state.pop("selected_application_id", None)
                st.session_state.pop("follow_up_draft", None)
                st.session_state.pop("follow_up_draft_application_id", None)
                st.session_state["application_delete_success"] = (
                    "Başvuru kalıcı olarak silindi."
                )
                st.rerun()
