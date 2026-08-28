import streamlit as st

from api_client import analyze_cv, get_cv_analysis, get_profile, save_profile, upload_cv
from ui import render_empty_state, render_page_header, render_section_header


render_page_header(
    "Profil ve CV",
    "Başvurularda kullanılacak kişisel bilgilerinizi ve aktif CV'nizi yönetin.",
    icon="person",
)

profile, profile_error = get_profile()
if profile_error:
    st.error(profile_error)
    st.stop()

profile = profile or {}
cv_analysis_state = None
if profile.get("cv_file_path"):
    cv_analysis_state, analysis_state_error = get_cv_analysis()
    if analysis_state_error:
        st.warning(analysis_state_error)

render_section_header(
    "Profesyonel profil",
    "Bu bilgiler e-posta taslaklarının kişiselleştirilmesinde kullanılır.",
)
with st.form("profile_form"):
    st.markdown("#### Kişisel bilgiler")
    identity_columns = st.columns(2)
    with identity_columns[0]:
        name = st.text_input("Ad soyad", value=profile.get("name", ""))
    with identity_columns[1]:
        target_job_title = st.text_input(
            "Hedef pozisyon", value=profile.get("target_job_title", "")
        )
    st.markdown("#### Profesyonel özet")
    professional_summary = st.text_area(
        "Kısa profesyonel özet",
        value=profile.get("professional_summary", ""),
        height=160,
    )
    st.markdown("#### Bağlantılar")
    link_columns = st.columns(2)
    with link_columns[0]:
        linkedin_url = st.text_input(
            "LinkedIn URL (opsiyonel)", value=profile.get("linkedin_url") or ""
        )
    with link_columns[1]:
        github_url = st.text_input(
            "GitHub URL (opsiyonel)", value=profile.get("github_url") or ""
        )

    st.markdown("##### CV yönetimi")
    current_cv_name = profile.get("cv_original_name")
    if current_cv_name:
        st.write(f"**Aktif dosya:** {current_cv_name}")
        if cv_analysis_state and cv_analysis_state.get("analyzed"):
            st.badge("Analiz hazır", color="green", icon=":material/check_circle:")
        else:
            st.badge("Analiz bekliyor", color="orange", icon=":material/schedule:")
    else:
        st.caption("Henüz bir CV yüklenmedi.")

    cv_file = st.file_uploader("CV yükle veya değiştir", type=["pdf"])
    submitted = st.form_submit_button(
        "Profili kaydet", type="primary", icon=":material/save:"
    )

if submitted:
    profile_data = {
        "name": name,
        "target_job_title": target_job_title,
        "professional_summary": professional_summary,
        "linkedin_url": linkedin_url or None,
        "github_url": github_url or None,
    }
    saved_profile, save_error = save_profile(profile_data)

    if save_error:
        st.error(save_error)
    elif cv_file is not None:
        saved_profile, upload_error = upload_cv(cv_file)
        if upload_error:
            st.warning(f"Profil kaydedildi ancak CV yüklenemedi: {upload_error}")
        else:
            st.success("Profil ve CV başarıyla kaydedildi.")
            st.rerun()
    else:
        st.success("Profil başarıyla kaydedildi.")
        st.rerun()

if profile.get("cv_file_path"):
    render_section_header(
        "CV içgörüleri",
        "Yerel Ollama analizi yalnızca siz başlattığınızda çalışır.",
    )
    if st.button("CV'yi analiz et", icon=":material/document_search:"):
        with st.spinner("CV metni çıkarılıyor ve Ollama ile analiz ediliyor..."):
            cv_analysis_state, analyze_error = analyze_cv()
        if analyze_error:
            st.error(analyze_error)
        else:
            st.success("CV analizi kaydedildi.")
            st.rerun()

    if cv_analysis_state and cv_analysis_state.get("analyzed"):
        analysis = cv_analysis_state.get("analysis") or {}
        with st.container(border=True):
            st.markdown("#### CV analiz özeti")

            skills = analysis.get("skills") or []
            st.write("**Beceriler:**", ", ".join(skills) if skills else "Bulunamadı")

            education = analysis.get("education") or []
            st.write("**Eğitim:**")
            if education:
                for item in education:
                    st.write(f"- {item}")
            else:
                st.caption("Eğitim bilgisi bulunamadı.")

            projects = analysis.get("projects") or []
            st.write("**Projeler:**")
            if projects:
                for project in projects:
                    description = project.get("description") or "Açıklama yok"
                    st.write(f"- {project['name']}: {description}")
            else:
                st.caption("Proje bilgisi bulunamadı.")

            experience = analysis.get("experience") or []
            st.write("**Deneyim:**")
            if experience:
                for item in experience:
                    organization = item.get("organization") or "Kurum belirtilmemiş"
                    st.write(f"- {item['title']} — {organization} ({item['type']})")
            else:
                st.caption("Profesyonel veya staj deneyimi bulunamadı.")
elif not profile:
    render_empty_state(
        "Profilinizi tamamlayın",
        "İlk kişiselleştirilmiş başvurunuzu hazırlamak için temel bilgilerinizi kaydedin.",
        icon="person_add",
    )
