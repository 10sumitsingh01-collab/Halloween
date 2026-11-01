from contextlib import asynccontextmanager
import http
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.routers import patients, caregivers, medications, doses, admin
from app.scheduler import start_scheduler, stop_scheduler


import logging
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate notifier channel & token, fail fast if missing
    channel = getattr(settings, "NOTIFIER_CHANNEL", "mock").lower()
    if channel == "telegram":
        token = settings.TELEGRAM_BOT_TOKEN.get_secret_value() if settings.TELEGRAM_BOT_TOKEN else None
        if not token or not token.strip():
            raise RuntimeError("TELEGRAM_BOT_TOKEN must be configured when NOTIFIER_CHANNEL=telegram")
        logger.info("notifier channel: telegram")
    else:
        logger.info("notifier channel: %s", channel)

    # Start background scheduler if enabled
    start_scheduler()
    yield
    # Shutdown: gracefully stop background scheduler
    stop_scheduler()


app = FastAPI(
    title="MedMate API",
    description="Deterministic medication assistant for elderly patients",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    phrase = "HTTP Error"
    try:
        phrase = http.HTTPStatus(exc.status_code).phrase
    except ValueError:
        pass

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": phrase, "detail": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": "Validation Error",
            "detail": exc.errors(),
        },
    )


# Include routers under /api/v1 prefix
app.include_router(patients.router, prefix="/api/v1")
app.include_router(caregivers.router, prefix="/api/v1")
app.include_router(medications.router, prefix="/api/v1")
app.include_router(doses.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "MedMate"}
