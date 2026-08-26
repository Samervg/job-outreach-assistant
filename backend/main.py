from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.companies import router as companies_router
from backend.applications import router as applications_router
from backend.config import APP_NAME
from backend.database import initialize_database
from backend.gmail import router as gmail_router
from backend.outreach import router as outreach_router
from backend.profile import router as profile_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.include_router(profile_router)
app.include_router(companies_router)
app.include_router(outreach_router)
app.include_router(gmail_router)
app.include_router(applications_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
