import json
import re
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from backend.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.services.email_generator import (
    OllamaModelUnavailableError,
    OllamaUnavailableError,
    ensure_configured_model,
)


class CVParsingError(Exception):
    pass


class CVAnalysisError(Exception):
    pass


class CVProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    technologies: list[str] = Field(default_factory=list, max_length=20)
    category: Literal["personal", "academic", "unspecified"] = "unspecified"


class CVExperience(BaseModel):
    organization: str = Field(default="", max_length=200)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    type: Literal["professional", "internship"]


class CVAnalysis(BaseModel):
    education: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=100)
    projects: list[CVProject] = Field(default_factory=list, max_length=30)
    experience: list[CVExperience] = Field(default_factory=list, max_length=30)


class RelevantProject(BaseModel):
    name: str
    description: str
    technologies: list[str]
    category: str
    reason: str


class RelevantExperience(BaseModel):
    organization: str
    title: str
    description: str
    type: str
    reason: str


class RelevantEvidence(BaseModel):
    relevant_skills: list[str] = Field(default_factory=list, max_length=5)
    relevant_projects: list[RelevantProject] = Field(default_factory=list, max_length=2)
    relevant_experience: list[RelevantExperience] = Field(
        default_factory=list, max_length=2
    )


class _EvidenceChoice(BaseModel):
    relevant_skills: list[str] = Field(default_factory=list)
    relevant_projects: list[str] = Field(default_factory=list)
    relevant_experience: list[str] = Field(default_factory=list)


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        reader = PdfReader(pdf_path)
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (OSError, PdfReadError) as error:
        raise CVParsingError("CV PDF dosyası okunamadı.") from error

    text = "\n\n".join(page for page in pages if page).strip()
    if len(text) < 30:
        raise CVParsingError(
            "CV'den kullanılabilir metin çıkarılamadı. Metin tabanlı bir PDF yükleyin."
        )
    return text


def _ollama_structured_request(
    *, system_prompt: str, user_prompt: str, schema: dict, timeout: int = 180
) -> dict:
    ensure_configured_model()
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 800},
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=timeout
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return json.loads(content)
    except requests.RequestException as error:
        raise OllamaUnavailableError(
            "Ollama CV analizi sırasında yanıt vermedi. Ollama'nın çalıştığını kontrol edin."
        ) from error
    except (ValueError, TypeError, AttributeError) as error:
        raise CVAnalysisError("Ollama geçerli bir yapılandırılmış CV analizi döndürmedi.") from error


def analyze_cv_text(cv_text: str) -> CVAnalysis:
    text = cv_text[:40000]

    def section(start: str, end: str | None = None) -> str:
        normalized_text = text.casefold()
        start_index = normalized_text.find(start.casefold())
        if start_index == -1:
            return text
        end_index = normalized_text.find(end.casefold(), start_index + len(start)) if end else -1
        return text[start_index : end_index if end_index != -1 else None]

    education_text = section("EĞİTİM", "TEKNİK YETENEKLER")
    skills_text = section("TEKNİK YETENEKLER", "PROJELER")
    projects_text = section("PROJELER", "STAJ TECRÜBELERİM")
    experience_text = section("STAJ TECRÜBELERİM")
    system_prompt = (
        "Sen yalnızca verilen CV metnindeki açık gerçekleri çıkaran bir veri "
        "çıkarma aracısın. Asla bilgi uydurma."
    )
    def array_schema(field: str) -> dict:
        return {
            "type": "object",
            "properties": {
                field: {"type": "array", "items": {"type": "string"}}
            },
            "required": [field],
        }

    education_raw = _ollama_structured_request(
        system_prompt=system_prompt,
        user_prompt=(
            "CV'de açıkça yazan eğitim kayıtlarını kısa metinler olarak çıkar. "
            "Başka bilgi ekleme.\n\n" + education_text
        ),
        schema=array_schema("education"),
        timeout=120,
    )
    skills_raw = _ollama_structured_request(
        system_prompt=system_prompt,
        user_prompt=(
            "CV'de açıkça yazan teknik beceri ve araç adlarını aynen çıkar. "
            "Tekrar etme ve yeni beceri ekleme.\n\n" + skills_text
        ),
        schema=array_schema("skills"),
        timeout=120,
    )

    projects_schema = {
        "type": "object",
        "properties": {
            "projects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "technologies": {"type": "array", "items": {"type": "string"}},
                        "category": {
                            "type": "string",
                            "enum": ["personal", "academic", "unspecified"],
                        },
                    },
                    "required": ["name", "description", "technologies", "category"],
                },
            }
        },
        "required": ["projects"],
    }
    projects_raw = _ollama_structured_request(
        system_prompt=system_prompt,
        user_prompt=(
            "Yalnızca CV'nin projeler bölümündeki projeleri çıkar. Açıklamaları kısa "
            "tut; teknoloji, sonuç veya kategori uydurma. Kategori açık değilse "
            "unspecified kullan. Projeleri iş deneyimi yapma.\n\n" + projects_text
        ),
        schema=projects_schema,
        timeout=150,
    )

    experience_schema = {
        "type": "object",
        "properties": {
            "experience": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "organization": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "type": {"type": "string", "enum": ["professional", "internship"]},
                    },
                    "required": ["organization", "title", "description", "type"],
                },
            }
        },
        "required": ["experience"],
    }
    experience_raw = _ollama_structured_request(
        system_prompt=system_prompt,
        user_prompt=(
            "Yalnızca CV'deki staj ve profesyonel iş kayıtlarını çıkar. Stajları "
            "internship yap. Projeleri deneyim olarak ekleme; şirket veya görev "
            "uydurma.\n\n" + experience_text
        ),
        schema=experience_schema,
        timeout=150,
    )

    normalized_cv = _normalized(text)
    skills = [
        skill
        for skill in skills_raw.get("skills", [])
        if _appears_in_text(skill, normalized_cv)
    ]
    projects = []
    for project in projects_raw.get("projects", []):
        if not _appears_in_text(str(project.get("name", "")), normalized_cv):
            continue
        project["technologies"] = [
            technology
            for technology in project.get("technologies", [])
            if _appears_in_text(str(technology), normalized_cv)
        ]
        category = str(project.get("category", "unspecified"))
        if category != "unspecified" and category not in normalized_cv:
            project["category"] = "unspecified"
        projects.append(project)

    experience = [
        item
        for item in experience_raw.get("experience", [])
        if _appears_in_text(str(item.get("title", "")), normalized_cv)
        and (
            not item.get("organization")
            or _appears_in_text(str(item["organization"]), normalized_cv)
        )
    ]
    for item in experience:
        if _normalized(str(item.get("description", ""))) in {
            "no description provided",
            "not provided",
            "n/a",
        }:
            item["description"] = ""
    raw = {
        "education": education_raw.get("education", []),
        "skills": skills,
        "projects": projects,
        "experience": experience,
    }
    try:
        return CVAnalysis.model_validate(raw)
    except ValidationError as error:
        raise CVAnalysisError("CV analizi beklenen şemaya uymuyor.") from error


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _appears_in_text(value: str, normalized_text: str) -> bool:
    normalized_value = _normalized(value)
    if not normalized_value:
        return False
    return bool(
        re.search(rf"(?<!\w){re.escape(normalized_value)}(?!\w)", normalized_text)
    )


def select_relevant_evidence(
    analysis: CVAnalysis, target_position: str, profile_summary: str
) -> RelevantEvidence:
    prompt = f"""
Hedef pozisyona en ilgili CV kanıtlarını seç. Yalnızca verilen listelerdeki değerleri
aynen döndür; yeni beceri, proje veya deneyim üretme. İlgili kanıt yoksa boş liste dön.

Sınırlar:
- En fazla 5 beceri adı.
- En fazla 2 proje adı.
- En fazla 2 deneyim için "organization | title" değeri.
- Kişisel/akademik projeyi deneyim olarak seçme.

Yalnızca şu JSON yapısını döndür:
{{"relevant_skills": ["beceri"], "relevant_projects": ["proje adı"],
  "relevant_experience": ["organization | title"]}}

Hedef pozisyon: {target_position}
Profil özeti: {profile_summary}
CV analizi:
{analysis.model_dump_json(indent=2)}
""".strip()

    schema = {
        "type": "object",
        "properties": {
            "relevant_skills": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
            },
            "relevant_projects": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
            },
            "relevant_experience": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
            },
        },
        "required": [
            "relevant_skills",
            "relevant_projects",
            "relevant_experience",
        ],
    }
    raw = _ollama_structured_request(
        system_prompt=(
            "Sen yalnızca sağlanan CV analizinden ilgili kanıt seçen bir filtresin. "
            "Verilen değerleri aynen koru ve hiçbir aday bilgisi uydurma."
        ),
        user_prompt=prompt,
        schema=schema,
        timeout=120,
    )
    try:
        choice = _EvidenceChoice.model_validate(raw)
    except ValidationError as error:
        raise CVAnalysisError("İlgili CV kanıtları beklenen şemaya uymuyor.") from error

    skill_lookup = {_normalized(skill): skill for skill in analysis.skills}
    selected_skills = []
    for skill in choice.relevant_skills:
        canonical = skill_lookup.get(_normalized(skill))
        if canonical and canonical not in selected_skills:
            selected_skills.append(canonical)

    project_lookup = {_normalized(project.name): project for project in analysis.projects}
    selected_projects = []
    for project_name in choice.relevant_projects:
        project = project_lookup.get(_normalized(project_name))
        if project and len(selected_projects) < 2:
            selected_projects.append(
                RelevantProject(
                    **project.model_dump(),
                    reason=f"{target_position} pozisyonuyla ilgili CV projesi.",
                )
            )

    experience_lookup = {
        _normalized(f"{item.organization} | {item.title}"): item
        for item in analysis.experience
    }
    selected_experience = []
    for value in choice.relevant_experience:
        item = experience_lookup.get(_normalized(value))
        if item and len(selected_experience) < 2:
            selected_experience.append(
                RelevantExperience(
                    **item.model_dump(),
                    reason=f"{target_position} pozisyonuyla ilgili CV deneyimi.",
                )
            )

    return RelevantEvidence(
        relevant_skills=selected_skills[:5],
        relevant_projects=selected_projects,
        relevant_experience=selected_experience,
    )
