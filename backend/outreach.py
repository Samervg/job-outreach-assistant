import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from backend.config import OLLAMA_MODEL
from backend.database import get_connection
from backend.services.cv_parser import (
    CVAnalysis,
    CVAnalysisError,
    select_relevant_evidence,
)
from backend.services.email_generator import (
    OllamaInvalidResponseError,
    OllamaModelUnavailableError,
    OllamaUnavailableError,
    generate_email,
    get_available_models,
)


router = APIRouter(tags=["drafts"])


class DraftGenerateRequest(BaseModel):
    company_id: int = Field(gt=0)


class DraftUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("subject", "body")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Bu alan boş bırakılamaz.")
        return value


class DraftResponse(BaseModel):
    id: int
    company_id: int
    company_name: str
    recipient_email: str
    position: str
    subject: str
    body: str
    status: str
    sent_at: str | None
    gmail_message_id: str | None
    error_message: str | None
    notes: str
    created_at: str
    updated_at: str


class OllamaStatusResponse(BaseModel):
    connected: bool
    configured_model: str
    model_available: bool
    message: str


def _row_to_draft(row: sqlite3.Row) -> DraftResponse:
    return DraftResponse(**dict(row))


def _get_draft_row(draft_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (draft_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Taslak bulunamadı.",
        )
    return row


@router.get("/ollama/status", response_model=OllamaStatusResponse)
def get_ollama_status() -> OllamaStatusResponse:
    try:
        models = get_available_models()
    except OllamaUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error

    model_available = OLLAMA_MODEL in models
    if model_available:
        message = f"Ollama ve {OLLAMA_MODEL} modeli kullanıma hazır."
    else:
        message = (
            f"Ollama çalışıyor ancak {OLLAMA_MODEL} modeli bulunamadı. "
            f"'ollama pull {OLLAMA_MODEL}' komutunu çalıştırın."
        )

    return OllamaStatusResponse(
        connected=True,
        configured_model=OLLAMA_MODEL,
        model_available=model_available,
        message=message,
    )


@router.post(
    "/drafts/generate",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_draft(request: DraftGenerateRequest) -> DraftResponse:
    with get_connection() as connection:
        profile_row = connection.execute(
            "SELECT * FROM user_profile WHERE id = 1"
        ).fetchone()
        company_row = connection.execute(
            "SELECT * FROM companies WHERE id = ?", (request.company_id,)
        ).fetchone()

    if profile_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="E-posta taslağı oluşturmadan önce profilinizi kaydedin.",
        )
    if company_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Şirket bulunamadı.",
        )

    profile = dict(profile_row)
    company = dict(company_row)
    required_profile_fields = ("name", "target_job_title", "professional_summary")
    if any(not str(profile.get(field) or "").strip() for field in required_profile_fields):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profilde ad, hedef pozisyon ve profesyonel özet bulunmalıdır.",
        )

    relevant_evidence = None
    company_research = None
    with get_connection() as connection:
        analysis_row = connection.execute(
            "SELECT * FROM cv_analysis WHERE id = 1"
        ).fetchone()
        research_row = connection.execute(
            """
            SELECT research_json, company_website_snapshot
            FROM company_research WHERE company_id = ?
            """,
            (company["id"],),
        ).fetchone()

    if (
        research_row is not None
        and research_row["company_website_snapshot"] == company.get("website")
    ):
        try:
            company_research = json.loads(research_row["research_json"])
        except (ValueError, TypeError):
            company_research = None

    if (
        analysis_row is not None
        and profile.get("cv_file_path")
        and analysis_row["cv_file_path"] == profile["cv_file_path"]
    ):
        try:
            analysis = CVAnalysis.model_validate(
                json.loads(analysis_row["analysis_json"])
            )
            relevant_evidence = select_relevant_evidence(
                analysis,
                company["target_position"],
                profile["professional_summary"],
            ).model_dump()
        except (ValueError, TypeError, CVAnalysisError) as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Kaydedilmiş CV analizi kullanılamadı. CV'yi yeniden analiz edin.",
            ) from error
        except (OllamaUnavailableError, OllamaModelUnavailableError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    try:
        generated = generate_email(
            profile, company, relevant_evidence, company_research
        )
    except OllamaModelUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except OllamaUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except OllamaInvalidResponseError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO outreach (
                company_id, company_name, recipient_email, position,
                subject, body, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                company["id"],
                company["name"],
                company["contact_email"],
                company["target_position"],
                generated.subject,
                generated.body,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    return _row_to_draft(row)


@router.get("/drafts", response_model=list[DraftResponse])
def list_drafts() -> list[DraftResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM outreach ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [_row_to_draft(row) for row in rows]


@router.get("/drafts/{draft_id}", response_model=DraftResponse)
def get_draft(draft_id: int) -> DraftResponse:
    return _row_to_draft(_get_draft_row(draft_id))


@router.put("/drafts/{draft_id}", response_model=DraftResponse)
def update_draft(draft_id: int, draft: DraftUpdate) -> DraftResponse:
    _get_draft_row(draft_id)
    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outreach
            SET subject = ?, body = ?, updated_at = ?
            WHERE id = ? AND status IN ('draft', 'failed')
            """,
            (draft.subject, draft.body, now, draft_id),
        )
        row = connection.execute(
            "SELECT * FROM outreach WHERE id = ?", (draft_id,)
        ).fetchone()

    return _row_to_draft(row)
