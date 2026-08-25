import streamlit as st

from api_client import analyze_cv, get_cv_analysis, get_profile, save_profile, upload_cv


st.header("Profil")

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

with st.form("profile_form"):
    name = st.text_input("Ad soyad", value=profile.get("name", ""))
    target_job_title = st.text_input(
        "Hedef pozisyon", value=profile.get("target_job_title", "")
    )
    professional_summary = st.text_area(
        "Kısa profesyonel özet",
        value=profile.get("professional_summary", ""),
        height=160,
    )
    linkedin_url = st.text_input(
        "LinkedIn URL (opsiyonel)", value=profile.get("linkedin_url") or ""
    )
    github_url = st.text_input(
        "GitHub URL (opsiyonel)", value=profile.get("github_url") or ""
    )

    current_cv_name = profile.get("cv_original_name")
    if current_cv_name:
        st.info(f"Yüklü CV: {current_cv_name}")
        if cv_analysis_state and cv_analysis_state.get("analyzed"):
            st.success("CV analiz edildi.")
        else:
            st.warning("CV henüz analiz edilmedi.")
    else:
        st.info("Henüz bir CV yüklenmedi.")

    cv_file = st.file_uploader("CV yükle veya değiştir", type=["pdf"])
    submitted = st.form_submit_button("Profili kaydet", type="primary")

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
            st.subheader("CV analiz özeti")

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
