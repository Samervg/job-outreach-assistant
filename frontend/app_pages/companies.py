import streamlit as st

from api_client import create_company, delete_company, list_companies, update_company


st.header("Şirketler")
st.caption("İletişime geçmek istediğiniz şirketleri manuel olarak yönetin.")

with st.form("new_company_form", clear_on_submit=True):
    st.subheader("Yeni şirket ekle")
    new_name = st.text_input("Şirket adı")
    new_website = st.text_input("Web sitesi (opsiyonel)")
    new_contact_email = st.text_input("İletişim e-postası")
    new_target_position = st.text_input("Hedef pozisyon")
    create_submitted = st.form_submit_button("Şirketi kaydet", type="primary")

if create_submitted:
    created_company, create_error = create_company(
        {
            "name": new_name,
            "website": new_website or None,
            "contact_email": new_contact_email,
            "target_position": new_target_position,
        }
    )
    if create_error:
        st.error(create_error)
    else:
        st.success(f"{created_company['name']} kaydedildi.")
        st.rerun()

companies, companies_error = list_companies()
if companies_error:
    st.error(companies_error)
    st.stop()

companies = companies or []
st.subheader("Kayıtlı şirketler")

if not companies:
    st.info("Henüz kayıtlı bir şirket yok.")
    st.stop()

st.dataframe(
    [
        {
            "Şirket": company["name"],
            "Hedef pozisyon": company["target_position"],
            "E-posta": company["contact_email"],
            "Web sitesi": company["website"] or "—",
        }
        for company in companies
    ],
    hide_index=True,
)

company_by_id = {company["id"]: company for company in companies}
selected_company_id = st.selectbox(
    "Düzenlemek veya ileride kullanmak için şirket seçin",
    options=list(company_by_id),
    format_func=lambda company_id: (
        f"{company_by_id[company_id]['name']} — "
        f"{company_by_id[company_id]['target_position']}"
    ),
)
selected_company = company_by_id[selected_company_id]

with st.form(f"edit_company_form_{selected_company_id}"):
    st.subheader("Seçili şirketi düzenle")
    edit_name = st.text_input("Şirket adı", value=selected_company["name"])
    edit_website = st.text_input(
        "Web sitesi (opsiyonel)", value=selected_company["website"] or ""
    )
    edit_contact_email = st.text_input(
        "İletişim e-postası", value=selected_company["contact_email"]
    )
    edit_target_position = st.text_input(
        "Hedef pozisyon", value=selected_company["target_position"]
    )
    confirm_delete = st.checkbox("Bu şirketi silmek istediğimi onaylıyorum")

    with st.container(horizontal=True):
        update_submitted = st.form_submit_button("Değişiklikleri kaydet")
        delete_submitted = st.form_submit_button("Şirketi sil")

if update_submitted:
    updated_company, update_error = update_company(
        selected_company_id,
        {
            "name": edit_name,
            "website": edit_website or None,
            "contact_email": edit_contact_email,
            "target_position": edit_target_position,
        },
    )
    if update_error:
        st.error(update_error)
    else:
        st.success(f"{updated_company['name']} güncellendi.")
        st.rerun()

if delete_submitted:
    if not confirm_delete:
        st.error("Şirketi silmek için onay kutusunu işaretleyin.")
    else:
        delete_error = delete_company(selected_company_id)
        if delete_error:
            st.error(delete_error)
        else:
            st.success("Şirket silindi.")
            st.rerun()
