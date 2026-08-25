import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

APP_NAME = os.getenv("APP_NAME", "Job Outreach Assistant")

database_path_value = os.getenv("DATABASE_PATH", "data/job_outreach.db")
DATABASE_PATH = Path(database_path_value)
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH

upload_dir_value = os.getenv("UPLOAD_DIR", "data/uploads")
UPLOAD_DIR = Path(upload_dir_value)
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = PROJECT_ROOT / UPLOAD_DIR

MAX_CV_SIZE_MB = int(os.getenv("MAX_CV_SIZE_MB", "5"))
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
ALLOW_INSECURE_OAUTH_LOOPBACK = os.getenv(
    "ALLOW_INSECURE_OAUTH_LOOPBACK", "true"
).lower() in {"1", "true", "yes"}
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")
