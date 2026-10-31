from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import require_caregiver_key
from app.schemas.caregiver import CaregiverCreate, CaregiverResponse
from app.services import caregiver_service

router = APIRouter(tags=["caregivers"])


@router.post(
    "/caregivers",
    response_model=CaregiverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a caregiver (Caregiver only)",
)
def create_caregiver(
    caregiver_in: CaregiverCreate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return caregiver_service.create_caregiver(db, caregiver_in)
