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
    candidate_name = str(profile["name"]).strip().title()
    company_name = " ".join(
        word.upper() if word.lower() == "ai" else word.capitalize()
        for word in str(company["name"]).strip().split()
    )

    return f"""
Kısa ve doğal bir iş başvurusu e-postası yaz. Profesyonel özet Türkçe olduğu için
e-posta da Türkçe olmalı.

E-posta iskeleti:
1. Selamlama tam olarak: "Merhaba {company_name} Ekibi,"
2. İlk paragraf tek cümle olsun: {company['target_position']} pozisyonuna başvurma amacı.
3. İkinci paragrafta aşağıdaki izinli aday bilgilerinden yalnızca 1-2 tanesini doğal
   biçimde kullan. Projelerin içeriğini, türünü veya kullanım alanını detaylandırma.
4. Son paragraf şu anlamı sade biçimde versin: "Kariyerimin başında öğrenmeye ve
   katkı sunmaya istekliyim. Uygun görürseniz görüşmekten memnuniyet duyarım."
5. Kapanış tam olarak: "İyi çalışmalar,\n{candidate_name}"

Zorunlu kontroller:
- Gövde selamlama ve kapanış dahil yaklaşık 70-110 kelime, tam 3 kısa paragraf olsun.
- Kısa konu satırında hedef pozisyon bulunsun.
- Sade, düzgün, birinci tekil şahıs Türkçesi kullan.
- Yalnızca verilen aday bilgilerini kullan. Deneyim süresi, profesyonel/üretim
  deneyimi, yeni teknoloji, başarı, şirket ürünü/faaliyeti/kültürü veya alıcı adı uydurma.
- Şirket web adresi, şirket hakkında bilgi sahibi olduğun anlamına gelmez.
- Ham URL, e-posta adresi, "ekipçisi", "kariyerimizi", "destek alabileceğimden eminim",
  "en kısa sürede dönüş yapacağım" veya adayın dönüş yapacağını vadeden benzer bir
  ifade ASLA yazma.
- "İki gerçek nokta", "işimdeki en önemli projeler" gibi talimatı açıklayan ifadeler
  yazma. Doğrudan adaya ait bilgiyi doğal cümleyle anlat.
- "Deneyim" kelimesini kullanma; "projeler geliştiriyorum/yaptım" de. Hiçbir somut
  proje adı, sektör, müşteri, sistem veya kullanım senaryosu ekleme.
- "Kariyerimiz/kariyerimizin" değil, yalnızca "kariyerim/kariyerimin" de.
- Teknolojileri listeleme; bir odak seç.
- Abartılı heyecan gösterme ve aynı bilgiyi tekrarlama.

Kullanıcı bilgileri:
- Ad: {candidate_name}
- Genel hedef iş unvanı: {profile['target_job_title']}
- Profesyonel özet: {profile['professional_summary']}
- LinkedIn/GitHub mevcut mu: {'Evet' if profile.get('linkedin_url') or profile.get('github_url') else 'Hayır'}

İzinli aday bilgileri (bunların dışına çıkma):
- Bilgisayar mühendisliği mezunu.
- AI/ML alanıyla ilgileniyor.
- Python ile makine öğrenmesi projeleri geliştiriyor.
- RAG/LLM tabanlı uygulamalar geliştiriyor.
- Backend ve full-stack projeler yaptı.
- AI/ML Engineer ve AI odaklı software engineering pozisyonlarına yöneliyor.

Şirket ve başvuru bilgileri:
- Şirket: {company_name}
- Hedef pozisyon: {company['target_position']}

Yalnızca istenen JSON şemasına uygun konu ve e-posta gövdesi üret. Açıklama veya
Markdown ekleme.
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
                    "Sen doğal, kısa ve dürüst iş başvurusu e-postaları hazırlayan bir "
                    "editörsün. Yalnızca verilen aday ve şirket verilerine dayan; hiçbir "
                    "deneyim veya şirket bilgisi uydurma. Çıktıyı belirtilen JSON "
                    "şemasında ver."
                ),
            },
            {"role": "user", "content": _build_prompt(profile, company)},
        ],
        "stream": False,
        "think": False,
        "format": response_schema,
        "options": {"temperature": 0.2},
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120
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
