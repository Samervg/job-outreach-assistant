import json
from dataclasses import dataclass

import requests

from backend.config import OLLAMA_BASE_URL, OLLAMA_MODEL


class OllamaUnavailableError(Exception):
    pass


class OllamaModelUnavailableError(Exception):
    pass


class OllamaInvalidResponseError(Exception):
    pass


@dataclass
class GeneratedEmail:
    subject: str
    body: str


def get_available_models() -> list[str]:
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        raise OllamaUnavailableError(
            "Ollama'ya bağlanılamadı. Ollama'nın yerelde çalıştığından emin olun."
        ) from error

    return [
        model.get("name", "")
        for model in data.get("models", [])
        if isinstance(model, dict) and model.get("name")
    ]


def ensure_configured_model() -> list[str]:
    models = get_available_models()
    if OLLAMA_MODEL not in models:
        raise OllamaModelUnavailableError(
            f"Yapılandırılmış Ollama modeli bulunamadı: {OLLAMA_MODEL}. "
            f"Önce 'ollama pull {OLLAMA_MODEL}' komutunu çalıştırın."
        )
    return models


def _build_prompt(profile: dict, company: dict) -> str:
    return f"""
Aşağıdaki bilgilerle kısa, profesyonel ve doğal bir iş başvurusu e-postası yaz.

Kesin kurallar:
- Yalnızca aşağıda verilen bilgileri kullan.
- Deneyim, teknoloji, beceri, şirket projesi, ürün, çalışan veya başarı uydurma.
- Kullanıcının profesyonel deneyimi olduğunu, profil açıkça söylemiyorsa iddia etme.
- Profil sınırlı deneyim gösteriyorsa dürüst ve junior seviyesine uygun bir dil kullan.
- Şirket hakkında araştırma yapıldığını veya bilinmeyen bir bilgiye sahip olunduğunu iddia etme.
- Sahte bir alıcı adı kullanma; genel ve doğal bir selamlama kullan.
- Hedef pozisyonu açıkça belirt.
- LinkedIn ve GitHub bağlantıları varsa metne doğal biçimde ekle.
- Abartılı heyecan, klişe yapay zekâ ifadeleri ve gereksiz moda sözcüklerden kaçın.
- E-posta gövdesini nispeten kısa tut.
- Profesyonel özetin dili belirginsa aynı dilde yaz; değilse Türkçe yaz.

Kullanıcı bilgileri:
- Ad: {profile['name']}
- Genel hedef iş unvanı: {profile['target_job_title']}
- Profesyonel özet: {profile['professional_summary']}
- LinkedIn: {profile.get('linkedin_url') or 'Sağlanmadı'}
- GitHub: {profile.get('github_url') or 'Sağlanmadı'}

Şirket ve başvuru bilgileri:
- Şirket: {company['name']}
- Web sitesi: {company.get('website') or 'Sağlanmadı'}
- İletişim e-postası: {company['contact_email']}
- Hedef pozisyon: {company['target_position']}

Yalnızca istenen JSON şemasına uygun konu ve e-posta gövdesi üret.
""".strip()


def generate_email(profile: dict, company: dict) -> GeneratedEmail:
    ensure_configured_model()

    response_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["subject", "body"],
        "additionalProperties": False,
    }
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen dürüst ve kısa iş başvurusu e-postaları hazırlayan bir asistansın. "
                    "Verilmeyen hiçbir bilgiyi uydurma."
                ),
            },
            {"role": "user", "content": _build_prompt(profile, company)},
        ],
        "stream": False,
        "format": response_schema,
        "options": {"temperature": 0.2},
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=180
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        generated = json.loads(content)
    except requests.RequestException as error:
        raise OllamaUnavailableError(
            "Ollama e-posta taslağı üretirken yanıt vermedi. "
            "Ollama'nın çalıştığını kontrol edin."
        ) from error
    except (ValueError, TypeError, AttributeError) as error:
        raise OllamaInvalidResponseError(
            "Ollama kullanılabilir bir e-posta taslağı döndürmedi. Tekrar deneyin."
        ) from error

    subject = generated.get("subject", "").strip()
    body = generated.get("body", "").strip()
    if not subject or not body:
        raise OllamaInvalidResponseError(
            "Ollama boş bir konu veya e-posta metni döndürdü. Tekrar deneyin."
        )

    return GeneratedEmail(subject=subject, body=body)
