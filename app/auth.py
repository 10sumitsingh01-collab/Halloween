from fastapi import Header, HTTPException, status
from app.config import settings


def get_current_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key")
) -> str:
    """Validate that an API key is provided and recognized."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Unauthorized", "detail": "Missing X-API-Key header"},
        )
    if x_api_key not in (settings.CAREGIVER_API_KEY, settings.PATIENT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Forbidden", "detail": "Invalid API key"},
        )
    return x_api_key


def require_caregiver_key(
    x_api_key: str | None = Header(None, alias="X-API-Key")
) -> str:
    """Enforce caregiver-authenticated access."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Unauthorized", "detail": "Missing X-API-Key header"},
        )
    if x_api_key != settings.CAREGIVER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Forbidden",
                "detail": "Caregiver privileges required for this endpoint",
            },
        )
    return x_api_key


def require_any_key(
    x_api_key: str | None = Header(None, alias="X-API-Key")
) -> str:
    """Allow either caregiver or patient API key."""
    return get_current_api_key(x_api_key)


def is_caregiver_key(api_key: str) -> bool:
    """Return True if the given API key has caregiver-level privileges."""
    return api_key == settings.CAREGIVER_API_KEY
