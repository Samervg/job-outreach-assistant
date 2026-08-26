import streamlit as st

from api_client import get_application, list_applications, update_application


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


st.header("Başvurular")

all_applications, all_error = list_applications("all")
if all_error:
    st.error(all_error)
    st.stop()

all_applications = all_applications or []
counts = {status: 0 for status in STATUS_LABELS if status != "all"}
for application in all_applications:
    counts[application["status"]] = counts.get(application["status"], 0) + 1

first_metrics = st.columns(4)
first_metrics[0].metric("Toplam", len(all_applications))
first_metrics[1].metric("Taslak", counts["draft"])
first_metrics[2].metric("Gönderildi", counts["sent"])
first_metrics[3].metric("Yanıt geldi", counts["replied"])
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
    if application.get("error_message"):
        st.error(f"Gönderim hatası: {application['error_message']}")

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
    submitted = st.form_submit_button("Takibi kaydet", type="primary")

if submitted:
    updated, update_error = update_application(
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
        "Bu kayıt henüz gönderim sonrası takip aşamasında değil. "
        "Durum, Gmail gönderimi başarıyla tamamlandığında otomatik güncellenir."
    )
elif application["status"] in {"rejected", "offer"}:
    st.caption("Bu başvuru son duruma ulaştı; özel notlar güncellenebilir.")
