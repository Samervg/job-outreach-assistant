import streamlit as st

from api_client import (
    generate_draft,
    get_draft,
    get_gmail_status,
    get_ollama_status,
    get_profile,
    list_companies,
    list_drafts,
    send_draft,
    start_gmail_oauth,
    update_draft,
)
from ui import (
    render_empty_state,
    render_page_header,
    render_status_badge,
    render_step,
)


render_page_header(
    "Yeni başvuru",
    "Şirket seçiminden Gmail gönderimine kadar kontrollü, adım adım bir akış.",
    icon="add_circle",
)
render_step(1, "Bağlantıları doğrula", "Gmail ve yerel Ollama durumunu kontrol edin.")
connection_columns = st.columns(2)
with connection_columns[0]:
    with st.container(border=True):
        st.markdown("**Gmail bağlantısı**")
        gmail_status, gmail_error = get_gmail_status()
        if gmail_error:
            st.warning(gmail_error)
            gmail_connected = False
        elif gmail_status["connected"]:
            gmail_connected = True
            gmail_account = gmail_status.get("email") or "Bağlı Gmail hesabı"
            st.success(f"Gmail bağlı: {gmail_account}")
        else:
            gmail_connected = False
            st.warning(gmail_status["message"])
            if st.button("Gmail hesabını bağla", icon=":material/link:"):
                auth_result, auth_error = start_gmail_oauth()
                if auth_error:
                    st.error(auth_error)
                else:
                    st.session_state["gmail_authorization_url"] = auth_result[
                        "authorization_url"
                    ]
            if st.session_state.get("gmail_authorization_url"):
                st.link_button(
                    "Google ile yetkilendirmeyi aç",
                    st.session_state["gmail_authorization_url"],
                )
                st.caption("Yetkilendirme tamamlandıktan sonra bu sayfayı yenileyin.")

with connection_columns[1]:
    with st.container(border=True):
        st.markdown("**Yerel AI**")
        ollama_status, ollama_error = get_ollama_status()
        if ollama_error:
            st.warning(ollama_error)
        elif ollama_status["model_available"]:
            st.success(ollama_status["message"])
        else:
            st.warning(ollama_status["message"])

companies, companies_error = list_companies()
if companies_error:
    st.error(companies_error)
    st.stop()

companies = companies or []
if not companies:
    render_empty_state(
        "Başvuru hedefi bulunamadı",
        "Taslak oluşturmak için önce Şirketler sayfasından şirket ve pozisyon ekleyin.",
        icon="domain_add",
    )
    st.stop()

render_step(2, "Şirket ve pozisyon", "Başvurunun gönderileceği kayıtlı hedefi seçin.")
company_by_id = {company["id"]: company for company in companies}
selected_company_id = st.selectbox(
    "Başvuru yapılacak şirket",
    options=list(company_by_id),
    format_func=lambda company_id: (
        f"{company_by_id[company_id]['name']} — "
        f"{company_by_id[company_id]['target_position']}"
    ),
)
selected_company = company_by_id[selected_company_id]

with st.container(border=True):
    company_columns = st.columns([3, 2])
    with company_columns[0]:
        st.write(f"**{selected_company['name']}**")
        st.caption(selected_company['target_position'])
    with company_columns[1]:
        st.caption(f":material/mail: {selected_company['contact_email']}")
        st.caption(
            f":material/language: {selected_company.get('website') or 'Web sitesi yok'}"
        )

render_step(
    3,
    "Araştırma bağlamı ve e-posta taslağı",
    "Kaydedilmiş şirket araştırması ve CV kanıtları varsa yerel AI bunları otomatik kullanır.",
)
with st.container(border=True):
    st.caption(
        "Seçili şirket ve pozisyon için kaydedilmiş araştırma ile CV kanıtları "
        "otomatik olarak değerlendirilir."
    )
    if st.button(
        "Mail taslağı oluştur", type="primary", icon=":material/auto_awesome:"
    ):
        with st.spinner("Ollama e-posta taslağını hazırlıyor..."):
            generated_draft, generation_error = generate_draft(selected_company_id)
        if generation_error:
            st.error(generation_error)
        else:
            st.session_state["draft_selection"] = generated_draft["id"]
            st.success("Taslak oluşturuldu ve yerel olarak kaydedildi.")
            st.rerun()

drafts, drafts_error = list_drafts()
if drafts_error:
    st.error(drafts_error)
    st.stop()

drafts = drafts or []
if not drafts:
    render_empty_state(
        "Henüz taslak yok",
        "Yukarıdaki şirket için ilk kişiselleştirilmiş taslağı oluşturun.",
        icon="draft",
    )
    st.stop()

draft_by_id = {draft["id"]: draft for draft in drafts}
draft_ids = list(draft_by_id)
if st.session_state.get("draft_selection") not in draft_by_id:
    st.session_state["draft_selection"] = draft_ids[0]

selected_draft_id = st.selectbox(
    "Kayıtlı taslak",
    options=draft_ids,
    format_func=lambda draft_id: (
        f"#{draft_id} — {draft_by_id[draft_id]['company_name']} — "
        f"{draft_by_id[draft_id]['position']}"
    ),
    key="draft_selection",
)

draft, draft_error = get_draft(selected_draft_id)
if draft_error:
    st.error(draft_error)
    st.stop()

with st.container(horizontal=True, vertical_alignment="center"):
    render_status_badge(draft["status"])
    st.caption(
        f"Alıcı: {draft['recipient_email']} · Şirket anlık görüntüsü: {draft['company_name']}"
    )

render_step(4, "İncele ve düzenle", "Konu ve e-posta metnini göndermeden önce düzenleyin.")
with st.form(f"draft_editor_{selected_draft_id}"):
    subject = st.text_input("E-posta konusu", value=draft["subject"])
    body = st.text_area("E-posta metni", value=draft["body"], height=360)
    save_submitted = st.form_submit_button(
        "Taslağı kaydet", type="primary", icon=":material/save:"
    )

if save_submitted:
    saved_draft, save_error = update_draft(selected_draft_id, subject, body)
    if save_error:
        st.error(save_error)
    else:
        st.success("Düzenlenen taslak kaydedildi.")
        st.rerun()

render_step(5, "Son kontrol ve gönderim", "Alıcıyı, içeriği ve CV ekini onaylayın.")
profile, profile_error = get_profile()
if profile_error:
    st.warning(profile_error)

with st.container(border=True):
    st.write(f"**Alıcı:** {draft['recipient_email']}")
    st.write(f"**Şirket:** {draft['company_name']}")
    st.write(f"**Pozisyon:** {draft['position']}")
    st.write(f"**Konu:** {draft['subject']}")
    st.write("**E-posta metni:**")
    st.text(draft["body"])
    st.write(
        f"**CV eki:** {(profile or {}).get('cv_original_name') or 'Aktif CV yok'}"
    )
    st.write(
        f"**Gönderen Gmail:** "
        f"{(gmail_status or {}).get('email') or 'Gmail bağlı değil'}"
    )

if draft["status"] == "sent":
    st.success(
        f"Bu e-posta gönderildi. Gmail mesaj kimliği: "
        f"{draft.get('gmail_message_id') or '-'}"
    )
    if draft.get("sent_at"):
        st.caption(f"Gönderim zamanı (UTC): {draft['sent_at']}")
else:
    if draft["status"] == "failed" and draft.get("error_message"):
        st.error(f"Son gönderim denemesi başarısız: {draft['error_message']}")

    confirmation = st.checkbox(
        "Alıcıyı, içeriği ve CV ekini kontrol ettim. "
        "Bu e-postayı göndermek istiyorum.",
        key=f"send_confirmation_{selected_draft_id}",
    )
    if st.button(
        "E-postayı gönder",
        type="primary",
        icon=":material/send:",
        disabled=not confirmation or not gmail_connected,
    ):
        with st.spinner("E-posta Gmail üzerinden gönderiliyor..."):
            sent_draft, send_error = send_draft(selected_draft_id, True)
        if send_error:
            st.error(send_error)
        else:
            st.success("E-posta başarıyla gönderildi.")
            st.rerun()
