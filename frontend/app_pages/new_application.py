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


st.header("Yeni başvuru")
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
    st.warning("Taslak oluşturmak için önce Şirketler sayfasından bir şirket ekleyin.")
    st.stop()

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
    st.write(f"**Şirket:** {selected_company['name']}")
    st.write(f"**Hedef pozisyon:** {selected_company['target_position']}")
    st.write(f"**Alıcı:** {selected_company['contact_email']}")

if st.button("Mail taslağı oluştur", type="primary", icon=":material/auto_awesome:"):
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
    st.caption("Henüz kayıtlı bir taslak yok.")
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

st.caption(
    f"Alıcı: {draft['recipient_email']} · Durum: {draft['status']} · "
    f"Şirket anlık görüntüsü: {draft['company_name']}"
)

with st.form(f"draft_editor_{selected_draft_id}"):
    subject = st.text_input("E-posta konusu", value=draft["subject"])
    body = st.text_area("E-posta metni", value=draft["body"], height=360)
    save_submitted = st.form_submit_button("Taslağı kaydet", type="primary")

if save_submitted:
    saved_draft, save_error = update_draft(selected_draft_id, subject, body)
    if save_error:
        st.error(save_error)
    else:
        st.success("Düzenlenen taslak kaydedildi.")
        st.rerun()

st.subheader("Son kontrol ve gönderim")
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
