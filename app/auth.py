import secrets
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models.dose import DoseEvent
from app.models.medication import Schedule, Medication


def _compare_keys(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)


def is_caregiver_key(api_key: str) -> bool:
    """Return True if the given API key has caregiver-level privileges."""
    return _compare_keys(api_key, settings.CAREGIVER_API_KEY)


def is_patient_key(api_key: str) -> bool:
    """Return True if the given API key has patient-level privileges."""
    return _compare_keys(api_key, settings.PATIENT_API_KEY)


def is_assistant_key(api_key: str) -> bool:
    """Return True if the given API key matches ASSISTANT_API_KEY and ASSISTANT_PATIENT_ID is set."""
    if not settings.ASSISTANT_API_KEY or settings.ASSISTANT_PATIENT_ID is None:
        return False
    assistant_secret = settings.ASSISTANT_API_KEY.get_secret_value()
    return _compare_keys(api_key, assistant_secret)


def get_current_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key")
) -> str:
    """Validate that an API key is provided and recognized as caregiver or patient key."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Unauthorized", "detail": "Missing X-API-Key header"},
        )
    if not (is_caregiver_key(x_api_key) or is_patient_key(x_api_key)):
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
    if not is_caregiver_key(x_api_key):
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


def require_assistant_or_patient_key(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    """
    Accept caregiver or patient keys.
    Accept assistant key ONLY for patient_id == ASSISTANT_PATIENT_ID
    and ONLY on allowed endpoints (GET today doses, POST taken, POST snooze,
    POST notes, GET adherence, POST assistant alerts).
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Unauthorized", "detail": "Missing X-API-Key header"},
        )

    if is_caregiver_key(x_api_key) or is_patient_key(x_api_key):
        return x_api_key

    if not is_assistant_key(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Forbidden", "detail": "Invalid API key"},
        )

    path_params = request.path_params
    target_id = path_params.get("id")
    if target_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Forbidden", "detail": "Assistant key not permitted for this resource"},
        )

    try:
        resource_id = int(target_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Forbidden", "detail": "Invalid resource identifier"},
        )

    path = request.url.path
    if "/doses/" in path:
        dose = (
            db.query(DoseEvent)
            .join(Schedule, DoseEvent.schedule_id == Schedule.id)
            .join(Medication, Schedule.medication_id == Medication.id)
            .filter(DoseEvent.id == resource_id)
            .first()
        )
        if not dose:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Not Found", "detail": f"DoseEvent {resource_id} not found"},
            )
        if dose.schedule.medication.patient_id != settings.ASSISTANT_PATIENT_ID:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Forbidden", "detail": "Assistant key not permitted for this patient's dose"},
            )
    else:
        if resource_id != settings.ASSISTANT_PATIENT_ID:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Forbidden", "detail": "Assistant key not permitted for this patient"},
            )

    return x_api_key
