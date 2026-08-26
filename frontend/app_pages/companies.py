import streamlit as st

from api_client import (
    check_company_duplicates,
    create_company,
    delete_company,
    import_company_preview,
    list_companies,
    update_company,
)


st.header("Şirketler")
st.caption("İletişime geçmek istediğiniz şirketleri manuel olarak yönetin.")

st.subheader("Website'den şirket ekle")
import_website = st.text_input(
    "Şirket web sitesi",
    placeholder="https://example.com",
    key="company_import_website",
)
if st.button("Siteyi tara", icon=":material/travel_explore:"):
    with st.spinner("Herkese açık şirket sayfaları inceleniyor..."):
        preview, preview_error = import_company_preview(import_website)
    if preview_error:
        st.error(preview_error)
    else:
        st.session_state["company_import_preview"] = preview
        st.session_state.pop("company_import_pending", None)
        for key in (
            "import_company_name",
            "import_company_website_edit",
            "import_contact_email",
            "import_career_page",
            "import_contact_page",
            "import_position_override",
        ):
            st.session_state.pop(key, None)
        st.success("Önizleme hazır. Bilgileri kontrol edip düzenleyin.")
        st.rerun()

preview = st.session_state.get("company_import_preview")
if preview:
    positions = preview.get("open_positions") or []
    position_titles = [position["title"] for position in positions]
    with st.container(border=True):
        st.caption(
            f"{len(preview.get('source_pages') or [])} herkese açık sayfa incelendi. "
            "Henüz hiçbir şirket kaydedilmedi."
        )
        if preview.get("source_pages"):
            with st.expander("İncelenen kaynak sayfalar"):
                for source_page in preview["source_pages"]:
                    st.write(source_page)

        with st.form("company_import_preview_form"):
            import_name = st.text_input(
                "Şirket adı",
                value=preview.get("company_name") or "",
                key="import_company_name",
            )
            import_website_edit = st.text_input(
                "Web sitesi",
                value=preview.get("website") or "",
                key="import_company_website_edit",
            )
            import_email = st.text_input(
                "İletişim e-postası",
                value=preview.get("contact_email") or "",
                key="import_contact_email",
            )
            import_career_page = st.text_input(
                "Kariyer sayfası",
                value=preview.get("career_page_url") or "",
                key="import_career_page",
            )
            import_contact_page = st.text_input(
                "İletişim sayfası",
                value=preview.get("contact_page_url") or "",
                key="import_contact_page",
            )
            selected_position = st.selectbox(
                "Tespit edilen açık pozisyon",
                options=[""] + position_titles,
                format_func=lambda value: value or "Pozisyon seçilmedi",
            )
            import_position_override = st.text_input(
                "Hedef pozisyon (manuel düzenleme)",
                key="import_position_override",
                placeholder="Pozisyon tespit edilmediyse buraya yazın",
            )
            st.caption(
                f"Kariyer sayfası: {'bulundu' if import_career_page else 'bulunamadı'} · "
                f"İletişim sayfası: {'bulundu' if import_contact_page else 'bulunamadı'} · "
                f"Açık pozisyon: {len(positions)}"
            )
            import_save_submitted = st.form_submit_button(
                "Şirketi kaydet", type="primary"
            )

    if import_save_submitted:
        company_data = {
            "name": import_name,
            "website": import_website_edit or None,
            "contact_email": import_email,
            "target_position": import_position_override.strip() or selected_position,
        }
        duplicates, duplicate_error = check_company_duplicates(
            company_data["name"], company_data["website"]
        )
        if duplicate_error:
            st.error(duplicate_error)
        elif duplicates:
            st.session_state["company_import_pending"] = {
                "company": company_data,
                "duplicates": duplicates,
            }
            st.warning("Benzer bir şirket zaten kayıtlı. Güncelleme veya iptal seçin.")
            st.rerun()
        else:
            created, create_error = create_company(company_data)
            if create_error:
                st.error(create_error)
            else:
                st.session_state.pop("company_import_preview", None)
                st.success(f"{created['name']} açık onayınızla kaydedildi.")
                st.rerun()

pending_import = st.session_state.get("company_import_pending")
if pending_import:
    duplicate_by_id = {
        company["id"]: company for company in pending_import["duplicates"]
    }
    with st.container(border=True):
        st.warning("Olası tekrar kayıt bulundu; otomatik değişiklik yapılmadı.")
        duplicate_id = st.selectbox(
            "Mevcut şirket",
            options=list(duplicate_by_id),
            format_func=lambda company_id: (
                f"{duplicate_by_id[company_id]['name']} — "
                f"{duplicate_by_id[company_id]['website'] or 'web sitesi yok'}"
            ),
        )
        with st.container(horizontal=True):
            confirm_update = st.button("Mevcut şirketi güncelle")
            cancel_import = st.button("İçe aktarmayı iptal et")
    if confirm_update:
        updated, update_error = update_company(
            duplicate_id, pending_import["company"]
        )
        if update_error:
            st.error(update_error)
        else:
            st.session_state.pop("company_import_pending", None)
            st.session_state.pop("company_import_preview", None)
            st.success(f"{updated['name']} açık onayınızla güncellendi.")
            st.rerun()
    if cancel_import:
        st.session_state.pop("company_import_pending", None)
        st.info("İçe aktarma iptal edildi; hiçbir kayıt değiştirilmedi.")
        st.rerun()

st.divider()

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
