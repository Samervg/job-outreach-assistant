import os

import requests
from dotenv import load_dotenv


load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def _error_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            messages = [item.get("msg") for item in detail if isinstance(item, dict)]
            if messages:
                return " ".join(messages)
    except ValueError:
        pass
    return f"İstek başarısız oldu ({response.status_code})."


def get_backend_health() -> tuple[bool, str]:
    """Return the backend connection state and a short status message."""
    try:
        response = requests.get(f"{BACKEND_URL}/health", timeout=3)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "ok":
            return True, "Backend bağlantısı başarılı."

        return False, "Backend beklenmeyen bir yanıt döndürdü."
    except (requests.RequestException, ValueError):
        return False, "Backend'e bağlanılamadı. FastAPI'nin çalıştığından emin olun."


def get_profile() -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/profile", timeout=5)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Profil bilgileri backend'den alınamadı."


def save_profile(profile_data: dict) -> tuple[dict | None, str | None]:
    try:
        response = requests.put(
            f"{BACKEND_URL}/profile", json=profile_data, timeout=10
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Profil kaydedilirken backend bağlantısı başarısız oldu."


def upload_cv(uploaded_file) -> tuple[dict | None, str | None]:
    try:
        files = {
            "file": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }
        response = requests.post(f"{BACKEND_URL}/profile/cv", files=files, timeout=30)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "CV yüklenirken backend bağlantısı başarısız oldu."


def get_cv_analysis() -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/profile/cv/analysis", timeout=10)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "CV analiz durumu backend'den alınamadı."


def analyze_cv() -> tuple[dict | None, str | None]:
    try:
        response = requests.post(f"{BACKEND_URL}/profile/cv/analyze", timeout=600)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "CV analiz edilirken backend bağlantısı başarısız oldu."


def list_companies() -> tuple[list[dict] | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/companies", timeout=5)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Şirketler backend'den alınamadı."


def create_company(company_data: dict) -> tuple[dict | None, str | None]:
    try:
        response = requests.post(
            f"{BACKEND_URL}/companies", json=company_data, timeout=10
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Şirket kaydedilirken backend bağlantısı başarısız oldu."


def update_company(
    company_id: int, company_data: dict
) -> tuple[dict | None, str | None]:
    try:
        response = requests.put(
            f"{BACKEND_URL}/companies/{company_id}",
            json=company_data,
            timeout=10,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Şirket güncellenirken backend bağlantısı başarısız oldu."


def delete_company(company_id: int) -> str | None:
    try:
        response = requests.delete(
            f"{BACKEND_URL}/companies/{company_id}", timeout=10
        )
        if not response.ok:
            return _error_message(response)
        return None
    except requests.RequestException:
        return "Şirket silinirken backend bağlantısı başarısız oldu."


def get_ollama_status() -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/ollama/status", timeout=10)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Ollama bağlantı durumu alınamadı."


def generate_draft(company_id: int) -> tuple[dict | None, str | None]:
    try:
        response = requests.post(
            f"{BACKEND_URL}/drafts/generate",
            json={"company_id": company_id},
            timeout=260,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Taslak oluşturulurken backend bağlantısı başarısız oldu."


def list_drafts() -> tuple[list[dict] | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/drafts", timeout=5)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Taslaklar backend'den alınamadı."


def get_draft(draft_id: int) -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/drafts/{draft_id}", timeout=5)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Taslak backend'den alınamadı."


def update_draft(
    draft_id: int, subject: str, body: str
) -> tuple[dict | None, str | None]:
    try:
        response = requests.put(
            f"{BACKEND_URL}/drafts/{draft_id}",
            json={"subject": subject, "body": body},
            timeout=10,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Taslak kaydedilirken backend bağlantısı başarısız oldu."


def get_gmail_status() -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/gmail/status", timeout=10)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Gmail bağlantı durumu alınamadı."


def start_gmail_oauth() -> tuple[dict | None, str | None]:
    try:
        response = requests.get(f"{BACKEND_URL}/gmail/auth/start", timeout=10)
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Gmail yetkilendirmesi başlatılamadı."


def send_draft(
    draft_id: int, confirm_send: bool
) -> tuple[dict | None, str | None]:
    try:
        response = requests.post(
            f"{BACKEND_URL}/drafts/{draft_id}/send",
            json={"confirm_send": confirm_send},
            timeout=60,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "E-posta gönderilirken backend bağlantısı başarısız oldu."


def list_applications(
    application_status: str = "all",
) -> tuple[list[dict] | None, str | None]:
    try:
        response = requests.get(
            f"{BACKEND_URL}/applications",
            params={"status": application_status},
            timeout=10,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Başvurular backend'den alınamadı."


def get_application(application_id: int) -> tuple[dict | None, str | None]:
    try:
        response = requests.get(
            f"{BACKEND_URL}/applications/{application_id}", timeout=10
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Başvuru backend'den alınamadı."


def update_application(
    application_id: int,
    *,
    application_status: str | None = None,
    notes: str | None = None,
) -> tuple[dict | None, str | None]:
    payload = {}
    if application_status is not None:
        payload["status"] = application_status
    if notes is not None:
        payload["notes"] = notes

    try:
        response = requests.patch(
            f"{BACKEND_URL}/applications/{application_id}",
            json=payload,
            timeout=10,
        )
        if not response.ok:
            return None, _error_message(response)
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, "Başvuru güncellenirken backend bağlantısı başarısız oldu."
