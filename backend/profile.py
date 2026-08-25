import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from backend.config import MAX_CV_SIZE_BYTES, MAX_CV_SIZE_MB, PROJECT_ROOT, UPLOAD_DIR
from backend.database import get_connection
from backend.services.cv_parser import (
    CVAnalysis,
    CVAnalysisError,
    CVParsingError,
    analyze_cv_text,
    extract_pdf_text,
)
from backend.services.email_generator import (
    OllamaModelUnavailableError,
    OllamaUnavailableError,
)


router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_job_title: str = Field(min_length=1, max_length=150)
    professional_summary: str = Field(min_length=1, max_length=3000)
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None

    @field_validator("name", "target_job_title", "professional_summary")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Bu alan boş bırakılamaz.")
        return value

    @field_validator("linkedin_url", "github_url", mode="before")
    @classmethod
    def empty_url_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    target_job_title: str
    professional_summary: str
    linkedin_url: str | None
    github_url: str | None
    cv_file_path: str | None
    cv_original_name: str | None
    created_at: str
    updated_at: str


class CVAnalysisResponse(BaseModel):
    analyzed: bool
    cv_original_name: str | None
    analyzed_at: str | None = None
    analysis: CVAnalysis | None = None


def _row_to_profile(row: sqlite3.Row) -> ProfileResponse:
    return ProfileResponse(**dict(row))


def _get_profile_row() -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM user_profile WHERE id = 1"
        ).fetchone()


@router.get("", response_model=ProfileResponse | None)
def get_current_profile() -> ProfileResponse | None:
    row = _get_profile_row()
    return _row_to_profile(row) if row else None


@router.put("", response_model=ProfileResponse)
def create_or_update_profile(profile: ProfileUpsert) -> ProfileResponse:
    now = datetime.now(timezone.utc).isoformat()
    linkedin_url = str(profile.linkedin_url) if profile.linkedin_url else None
    github_url = str(profile.github_url) if profile.github_url else None

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO user_profile (
                id, name, target_job_title, professional_summary,
                linkedin_url, github_url, created_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                target_job_title = excluded.target_job_title,
                professional_summary = excluded.professional_summary,
                linkedin_url = excluded.linkedin_url,
                github_url = excluded.github_url,
                updated_at = excluded.updated_at
            """,
            (
                profile.name,
                profile.target_job_title,
                profile.professional_summary,
                linkedin_url,
                github_url,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM user_profile WHERE id = 1"
        ).fetchone()

    return _row_to_profile(row)


def _safe_old_cv_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None

    candidate = (PROJECT_ROOT / relative_path).resolve()
    uploads_root = UPLOAD_DIR.resolve()
    try:
        candidate.relative_to(uploads_root)
    except ValueError:
        return None
    return candidate


@router.post("/cv", response_model=ProfileResponse)
def upload_or_replace_cv(
    file: Annotated[UploadFile, File(description="PDF CV file")],
) -> ProfileResponse:
    current_profile = _get_profile_row()
    if current_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV yüklemeden önce profilinizi kaydedin.",
        )

    original_name = Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yalnızca PDF dosyaları yüklenebilir.",
        )

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dosya türü PDF olmalıdır.",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOAD_DIR / f"{uuid4().hex}.pdf"
    total_size = 0

    try:
        with saved_path.open("wb") as destination:
            first_chunk = file.file.read(1024 * 1024)
            if not first_chunk.startswith(b"%PDF-"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Dosya geçerli bir PDF içeriğine sahip değil.",
                )

            total_size += len(first_chunk)
            if total_size > MAX_CV_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"CV dosyası en fazla {MAX_CV_SIZE_MB} MB olabilir.",
                )
            destination.write(first_chunk)

            while chunk := file.file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_CV_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"CV dosyası en fazla {MAX_CV_SIZE_MB} MB olabilir.",
                    )
                destination.write(chunk)

        relative_path = saved_path.relative_to(PROJECT_ROOT).as_posix()
        now = datetime.now(timezone.utc).isoformat()

        with get_connection() as connection:
            connection.execute(
                """
                UPDATE user_profile
                SET cv_file_path = ?, cv_original_name = ?, updated_at = ?
                WHERE id = 1
                """,
                (relative_path, original_name, now),
            )
            connection.execute("DELETE FROM cv_analysis WHERE id = 1")
            row = connection.execute(
                "SELECT * FROM user_profile WHERE id = 1"
            ).fetchone()

        old_path = _safe_old_cv_path(current_profile["cv_file_path"])
        if old_path and old_path != saved_path and old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                # The new CV remains active even if an old local file is locked.
                pass

        return _row_to_profile(row)
    except Exception:
        if saved_path.is_file():
            saved_path.unlink()
        raise
    finally:
        file.file.close()


@router.get("/cv/analysis", response_model=CVAnalysisResponse)
def get_cv_analysis() -> CVAnalysisResponse:
    profile = _get_profile_row()
    if profile is None or not profile["cv_file_path"]:
        return CVAnalysisResponse(analyzed=False, cv_original_name=None)

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM cv_analysis WHERE id = 1"
        ).fetchone()

    if row is None or row["cv_file_path"] != profile["cv_file_path"]:
        return CVAnalysisResponse(
            analyzed=False,
            cv_original_name=profile["cv_original_name"],
        )

    try:
        analysis = CVAnalysis.model_validate(json.loads(row["analysis_json"]))
    except (ValueError, TypeError):
        return CVAnalysisResponse(
            analyzed=False,
            cv_original_name=profile["cv_original_name"],
        )

    return CVAnalysisResponse(
        analyzed=True,
        cv_original_name=profile["cv_original_name"],
        analyzed_at=row["updated_at"],
        analysis=analysis,
    )


@router.post("/cv/analyze", response_model=CVAnalysisResponse)
def analyze_current_cv() -> CVAnalysisResponse:
    profile = _get_profile_row()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV analizi için önce profilinizi kaydedin.",
        )
    if not profile["cv_file_path"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Analiz edilecek bir CV bulunamadı.",
        )

    cv_path = _safe_old_cv_path(profile["cv_file_path"])
    if cv_path is None or not cv_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Yüklenen CV dosyası yerel diskte bulunamadı.",
        )

    try:
        text = extract_pdf_text(cv_path)
        analysis = analyze_cv_text(text)
    except CVParsingError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except CVAnalysisError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    except (OllamaUnavailableError, OllamaModelUnavailableError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error

    now = datetime.now(timezone.utc).isoformat()
    analysis_json = analysis.model_dump_json()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO cv_analysis (id, cv_file_path, analysis_json, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cv_file_path = excluded.cv_file_path,
                analysis_json = excluded.analysis_json,
                updated_at = excluded.updated_at
            """,
            (profile["cv_file_path"], analysis_json, now, now),
        )

    return CVAnalysisResponse(
        analyzed=True,
        cv_original_name=profile["cv_original_name"],
        analyzed_at=now,
        analysis=analysis,
    )
