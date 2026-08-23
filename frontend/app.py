import streamlit as st

from api_client import BACKEND_URL, get_backend_health


st.set_page_config(page_title="Job Outreach Assistant", page_icon=":material/work:")

st.title("Job Outreach Assistant")
st.write("İş başvurusu iletişim asistanı MVP")

is_connected, message = get_backend_health()
if is_connected:
    st.success(message)
else:
    st.error(message)
    st.caption(f"Backend adresi: {BACKEND_URL}")
    st.stop()

page = st.navigation(
    [
        st.Page("app_pages/profile.py", title="Profil", icon=":material/person:"),
        st.Page(
            "app_pages/companies.py",
            title="Şirketler",
            icon=":material/business:",
        ),
        st.Page(
            "app_pages/new_application.py",
            title="Yeni başvuru",
            icon=":material/edit_note:",
        ),
    ],
    position="top",
)
page.run()
