from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.companies import router as companies_router
from backend.applications import router as applications_router
from backend.config import APP_NAME, validate_configuration
from backend.database import initialize_database
from backend.logging_config import configure_logging
from backend.gmail import router as gmail_router
from backend.outreach import router as outreach_router
from backend.profile import router as profile_router


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_configuration()
    logger.info("Backend startup initiated")
    initialize_database()
    logger.info("Backend startup completed")
    yield
    logger.info("Backend shutdown completed")


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.include_router(profile_router)
app.include_router(companies_router)
app.include_router(outreach_router)
app.include_router(gmail_router)
app.include_router(applications_router)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "Unexpected backend error method=%s path=%s error_type=%s",
        request.method,
        request.url.path,
        type(error).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Beklenmeyen bir sunucu hatası oluştu."},
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
