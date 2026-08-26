import sqlite3
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

from backend.database import get_connection
from backend.services.company_importer import CompanyImportError, import_company_preview


router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None
    contact_email: EmailStr
    target_position: str = Field(min_length=1, max_length=200)

    @field_validator("name", "target_position")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Bu alan boş bırakılamaz.")
        return value

    @field_validator("website", mode="before")
    @classmethod
    def empty_website_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    website: str | None
    contact_email: str
    target_position: str
    created_at: str
    updated_at: str


class CompanyImportRequest(BaseModel):
    website: str = Field(min_length=1, max_length=2000)


class OpenPositionPreview(BaseModel):
    title: str
    url: str


class CompanyImportPreview(BaseModel):
    website: str
    company_name: str | None
    contact_email: str | None
    career_page_url: str | None
    contact_page_url: str | None
    open_positions: list[OpenPositionPreview]
    source_pages: list[str]


class DuplicateCheckRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=2000)


class DuplicateCheckResponse(BaseModel):
    duplicates: list[CompanyResponse]


def _row_to_company(row: sqlite3.Row) -> CompanyResponse:
    return CompanyResponse(**dict(row))


def _get_company_row(company_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Şirket bulunamadı.",
        )
    return row


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(company: CompanyUpsert) -> CompanyResponse:
    now = datetime.now(timezone.utc).isoformat()
    website = str(company.website) if company.website else None

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO companies (
                name, website, contact_email, target_position, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                company.name,
                website,
                str(company.contact_email),
                company.target_position,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM companies WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    return _row_to_company(row)


@router.get("", response_model=list[CompanyResponse])
def list_companies() -> list[CompanyResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM companies ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [_row_to_company(row) for row in rows]


def _normalized_name(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())


def _website_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname or None


@router.post("/import-preview", response_model=CompanyImportPreview)
def import_preview(request: CompanyImportRequest) -> CompanyImportPreview:
    try:
        preview = import_company_preview(request.website)
    except CompanyImportError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from error
    return CompanyImportPreview(**preview)


@router.post("/duplicate-check", response_model=DuplicateCheckResponse)
def check_company_duplicate(
    request: DuplicateCheckRequest,
) -> DuplicateCheckResponse:
    requested_name = _normalized_name(request.name)
    requested_domain = _website_domain(request.website)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM companies ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    duplicates = [
        _row_to_company(row)
        for row in rows
        if _normalized_name(row["name"]) == requested_name
        or (
            requested_domain is not None
            and _website_domain(row["website"]) == requested_domain
        )
    ]
    return DuplicateCheckResponse(duplicates=duplicates)


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int) -> CompanyResponse:
    return _row_to_company(_get_company_row(company_id))


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(company_id: int, company: CompanyUpsert) -> CompanyResponse:
    _get_company_row(company_id)
    now = datetime.now(timezone.utc).isoformat()
    website = str(company.website) if company.website else None

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE companies
            SET name = ?, website = ?, contact_email = ?,
                target_position = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                company.name,
                website,
                str(company.contact_email),
                company.target_position,
                now,
                company_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()

    return _row_to_company(row)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(company_id: int) -> Response:
    _get_company_row(company_id)
    with get_connection() as connection:
        connection.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
