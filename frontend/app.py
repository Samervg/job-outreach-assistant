import streamlit as st

from api_client import BACKEND_URL, get_backend_health


st.set_page_config(
    page_title="Job Outreach Assistant",
    page_icon=":material/work:",
    layout="wide",
    initial_sidebar_state="expanded",
)

is_connected, message = get_backend_health()
if is_connected:
    backend_ready = True
else:
    backend_ready = False

page = st.navigation(
    {
        "Başvurular": [
            st.Page(
                "app_pages/applications.py",
                title="Genel bakış",
                icon=":material/dashboard:",
                default=True,
            ),
            st.Page(
                "app_pages/new_application.py",
                title="Yeni başvuru",
                icon=":material/add_circle:",
            ),
        ],
        "Çalışma alanı": [
            st.Page(
                "app_pages/companies.py",
                title="Şirketler",
                icon=":material/domain:",
            ),
            st.Page(
                "app_pages/profile.py",
                title="Profil ve CV",
                icon=":material/person:",
            ),
        ],
    },
    position="sidebar",
)

with st.sidebar:
    st.title("Job Outreach")
    st.caption("Kişisel başvuru çalışma alanı")
    if backend_ready:
        st.badge("Backend bağlı", color="green", icon=":material/check_circle:")
    else:
        st.badge("Backend bağlantısı yok", color="red", icon=":material/error:")
        st.error(message)
        st.caption(f"Backend adresi: {BACKEND_URL}")

if not backend_ready:
    st.stop()

page.run()
