import streamlit as st

from api_client import get_profile, save_profile, upload_cv


st.header("Profil")

profile, profile_error = get_profile()
if profile_error:
    st.error(profile_error)
    st.stop()

profile = profile or {}

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
