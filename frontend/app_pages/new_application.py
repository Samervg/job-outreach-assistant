import streamlit as st

from api_client import (
    generate_draft,
    get_draft,
    get_ollama_status,
    list_companies,
    list_drafts,
    update_draft,
)


st.header("Yeni başvuru")
st.info("Bu yalnızca bir taslaktır. Henüz e-posta gönderilmez.")

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
