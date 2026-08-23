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
