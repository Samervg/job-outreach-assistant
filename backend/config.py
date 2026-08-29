import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(ValueError):
    """Raised when an environment setting is unsafe or malformed."""


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} true/false değerlerinden biri olmalıdır."
    )


def _validate_http_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{name} geçerli bir http/https URL olmalıdır.")


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} pozitif bir tam sayı olmalıdır.") from error
    if value <= 0:
        raise ConfigurationError(f"{name} pozitif bir tam sayı olmalıdır.")
    return value

APP_NAME = os.getenv("APP_NAME", "Job Outreach Assistant")

database_path_value = os.getenv("DATABASE_PATH", "data/job_outreach.db")
DATABASE_PATH = Path(database_path_value)
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH

upload_dir_value = os.getenv("UPLOAD_DIR", "data/uploads")
UPLOAD_DIR = Path(upload_dir_value)
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = PROJECT_ROOT / UPLOAD_DIR

MAX_CV_SIZE_MB = _positive_int("MAX_CV_SIZE_MB", 5)
MAX_CV_SIZE_BYTES = MAX_CV_SIZE_MB * 1024 * 1024

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

credentials_dir_value = os.getenv("CREDENTIALS_DIR", "credentials")
CREDENTIALS_DIR = Path(credentials_dir_value)
if not CREDENTIALS_DIR.is_absolute():
    CREDENTIALS_DIR = PROJECT_ROOT / CREDENTIALS_DIR

gmail_client_secret_value = os.getenv(
    "GMAIL_CLIENT_SECRET_PATH", "credentials/client_secret.json"
)
GMAIL_CLIENT_SECRET_PATH = Path(gmail_client_secret_value)
if not GMAIL_CLIENT_SECRET_PATH.is_absolute():
    GMAIL_CLIENT_SECRET_PATH = PROJECT_ROOT / GMAIL_CLIENT_SECRET_PATH

GMAIL_TOKEN_PATH = CREDENTIALS_DIR / "gmail_token.json"
GMAIL_ACCOUNT_PATH = CREDENTIALS_DIR / "gmail_account.json"
GMAIL_OAUTH_STATE_PATH = CREDENTIALS_DIR / "gmail_oauth_state.json"
GMAIL_REDIRECT_URI = os.getenv(
    "GMAIL_REDIRECT_URI", "http://127.0.0.1:8000/gmail/auth/callback"
)
ALLOW_INSECURE_OAUTH_LOOPBACK = _read_bool("ALLOW_INSECURE_OAUTH_LOOPBACK", True)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
if LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    raise ConfigurationError(
        "LOG_LEVEL DEBUG, INFO, WARNING, ERROR veya CRITICAL olmalıdır."
    )

backup_dir_value = os.getenv("BACKUP_DIR", "data/backups")
BACKUP_DIR = Path(backup_dir_value)
if not BACKUP_DIR.is_absolute():
    BACKUP_DIR = PROJECT_ROOT / BACKUP_DIR


def validate_configuration() -> None:
    """Validate startup-safe settings without requiring external services."""
    if not OLLAMA_MODEL.strip():
        raise ConfigurationError("OLLAMA_MODEL boş olamaz.")
    _validate_http_url("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
    _validate_http_url("GMAIL_REDIRECT_URI", GMAIL_REDIRECT_URI)
    _validate_http_url("FRONTEND_URL", FRONTEND_URL)
    redirect = urlparse(GMAIL_REDIRECT_URI)
    if ALLOW_INSECURE_OAUTH_LOOPBACK and redirect.scheme == "http":
        if redirect.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigurationError(
                "ALLOW_INSECURE_OAUTH_LOOPBACK yalnızca loopback redirect URI ile kullanılabilir."
            )
