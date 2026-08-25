import json
import re
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


@dataclass
class EmailEvidenceChoice:
    kind: str
    label: str
    score: int
    concepts: list[str]
    sentence: str


AI_CONCEPT_WEIGHTS = {
    "computer vision": 12,
    "bilgisayarlı görü": 12,
    "segmentasyon": 11,
    "u-net": 11,
    "resnet": 10,
    "rag": 10,
    "llm": 10,
    "pytorch": 9,
    "tensorflow": 9,
    "scikit-learn": 8,
    "machine learning": 8,
    "makine öğrenmesi": 8,
    "deep learning": 8,
    "keras": 7,
    "opencv": 7,
    "cnn": 7,
    "python": 6,
    "vektör arama": 6,
    "model": 4,
}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _target_concept_weights(target_position: str) -> dict[str, int]:
    normalized_target = _normalized(target_position)
    weights = {
        token: 5 for token in re.findall(r"[a-z0-9+#.-]+", normalized_target)
    }
    target_tokens = set(weights)

    if target_tokens.intersection({"ai", "ml"}) or any(
        phrase in normalized_target
        for phrase in ("machine learning", "artificial intelligence", "data scientist")
    ):
        weights.update(AI_CONCEPT_WEIGHTS)
    elif "backend" in target_tokens:
        weights.update(
            {
                "python": 9,
                "node.js": 9,
                "fastify": 8,
                "rest api": 8,
                "postgresql": 7,
                "sql": 7,
            }
        )
    elif "frontend" in target_tokens:
        weights.update(
            {"react": 10, "next.js": 9, "typescript": 8, "javascript": 8}
        )
    return weights


def _matching_concepts(text: str, weights: dict[str, int]) -> list[tuple[str, int]]:
    normalized_text = _normalized(text)
    matches = [
        (concept, weight)
        for concept, weight in weights.items()
        if concept in normalized_text
    ]
    return sorted(matches, key=lambda item: (-item[1], item[0]))


def _join_turkish(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} ve {items[-1]}"


def rank_email_evidence(
    relevant_evidence: dict | None, target_position: str
) -> list[EmailEvidenceChoice]:
    evidence = relevant_evidence or {}
    weights = _target_concept_weights(target_position)
    selected_skills = evidence.get("relevant_skills") or []
    for skill in selected_skills:
        weights.setdefault(_normalized(str(skill)), 3)
    ranked: list[EmailEvidenceChoice] = []

    for project in evidence.get("relevant_projects") or []:
        project_text = " ".join(
            [
                str(project.get("name", "")),
                str(project.get("description", "")),
                " ".join(project.get("technologies") or []),
            ]
        )
        matches = _matching_concepts(project_text, weights)
        score = sum(weight for _, weight in matches) + 2
        technologies = project.get("technologies") or []
        ranked_technologies = sorted(
            technologies,
            key=lambda technology: (
                -sum(
                    weight
                    for _, weight in _matching_concepts(str(technology), weights)
                ),
                technologies.index(technology),
            ),
        )
        relevant_technologies = [
            technology
            for technology in ranked_technologies
            if _matching_concepts(str(technology), weights)
        ][:2]
        project_name = str(project["name"])
        base_name = re.split(r"\s+[–—-]\s+", project_name, maxsplit=1)[0]
        if relevant_technologies:
            sentence = (
                f"{base_name} projemde {_join_turkish(relevant_technologies)} "
                "ile çalıştım."
            )
        else:
            sentence = f"{project_name} projesi üzerinde çalıştım."
        ranked.append(
            EmailEvidenceChoice(
                kind="project",
                label=project_name,
                score=score,
                concepts=[concept for concept, _ in matches],
                sentence=sentence,
            )
        )

    for experience in evidence.get("relevant_experience") or []:
        experience_text = " ".join(
            [
                str(experience.get("organization", "")),
                str(experience.get("title", "")),
                str(experience.get("description", "")),
            ]
        )
        matches = _matching_concepts(experience_text, weights)
        organization = str(experience.get("organization", "")).strip()
        title = str(experience.get("title", "")).strip()
        if not organization or not title:
            continue
        ranked.append(
            EmailEvidenceChoice(
                kind="experience",
                label=f"{organization} — {title}",
                score=sum(weight for _, weight in matches) + 1,
                concepts=[concept for concept, _ in matches],
                sentence=f"{organization} bünyesinde {title} olarak görev aldım.",
            )
        )

    if not ranked and selected_skills:
        ranked_skills = sorted(
            selected_skills,
            key=lambda skill: -sum(
                weight for _, weight in _matching_concepts(str(skill), weights)
            ),
        )[:3]
        ranked.append(
            EmailEvidenceChoice(
                kind="skills",
                label="Teknik beceriler",
                score=sum(
                    weight
                    for skill in ranked_skills
                    for _, weight in _matching_concepts(str(skill), weights)
                ),
                concepts=ranked_skills,
                sentence=f"{_join_turkish(ranked_skills)} ile çalıştım.",
            )
        )

    return sorted(ranked, key=lambda choice: (-choice.score, choice.label))


def _build_evidence_sentence(
    relevant_evidence: dict | None, target_position: str
) -> str | None:
    ranked = rank_email_evidence(relevant_evidence, target_position)
    return ranked[0].sentence if ranked else None


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


def _build_prompt(
    profile: dict, company: dict, relevant_evidence: dict | None = None
) -> str:
    candidate_name = str(profile["name"]).strip().title()
    company_name = " ".join(
        word.upper() if word.lower() == "ai" else word.capitalize()
        for word in str(company["name"]).strip().split()
    )
    evidence_sentence = _build_evidence_sentence(
        relevant_evidence, str(company["target_position"])
    )
    if evidence_sentence:
        evidence_section = f"""
Doğrulanmış CV kanıt cümlesi:
"{evidence_sentence}"

İkinci paragrafta yalnızca bu cümleyi AYNEN kullan. Kelime ekleme, çıkarma veya
değiştirme; öncesine ya da sonrasına başka teknik cümle ekleme. Bu proje profesyonel
iş deneyimi değildir.
""".strip()
        second_paragraph_rule = (
            "3. İkinci paragraf yalnızca aşağıdaki doğrulanmış CV kanıt cümlesi olsun."
        )
        project_rule = (
            "- Somut proje ayrıntısı yalnızca doğrulanmış CV kanıtlarında bulunuyorsa "
            "kullanılabilir."
        )
    else:
        evidence_section = (
            "Doğrulanmış CV kanıtı bulunmuyor. Yalnızca profil özetini kullan."
        )
        project_rule = (
            "- Hiçbir somut proje adı, sektör, müşteri, sistem veya kullanım "
            "senaryosu ekleme."
        )
        second_paragraph_rule = (
            "3. İkinci paragrafta aşağıdaki izinli aday bilgilerinden yalnızca 1-2 "
            "tanesini doğal biçimde kullan."
        )

    return f"""
Kısa ve doğal bir iş başvurusu e-postası yaz. Profesyonel özet Türkçe olduğu için
e-posta da Türkçe olmalı.

E-posta iskeleti:
1. Selamlama tam olarak: "Merhaba {company_name} Ekibi,"
2. İlk paragraf tek cümle olsun: {company['target_position']} pozisyonuna başvurma amacı.
{second_paragraph_rule}
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
- "Deneyim" kelimesini yalnızca doğrulanmış CV kanıtındaki experience listesi
  destekliyorsa kullan; aksi halde "projeler geliştiriyorum/yaptım" de.
{project_rule}
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

{evidence_section}

Şirket ve başvuru bilgileri:
- Şirket: {company_name}
- Hedef pozisyon: {company['target_position']}

Yalnızca istenen JSON şemasına uygun konu ve e-posta gövdesi üret. Açıklama veya
Markdown ekleme.
""".strip()


def generate_email(
    profile: dict, company: dict, relevant_evidence: dict | None = None
) -> GeneratedEmail:
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
            {
                "role": "user",
                "content": _build_prompt(profile, company, relevant_evidence),
            },
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

    evidence_sentence = _build_evidence_sentence(
        relevant_evidence, str(company["target_position"])
    )
    if evidence_sentence:
        paragraphs = [paragraph.strip() for paragraph in body.split("\n\n")]
        if evidence_sentence not in paragraphs:
            raise OllamaInvalidResponseError(
                "Ollama doğrulanmış CV kanıtını güvenli biçimde kullanmadı. Tekrar deneyin."
            )

    return GeneratedEmail(subject=subject, body=body)
