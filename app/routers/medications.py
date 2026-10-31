from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import require_caregiver_key, require_any_key
from app.schemas.medication import (
    MedicationCreate,
    MedicationUpdate,
    MedicationResponse,
    ScheduleCreate,
    ScheduleResponse,
)
from app.services import medication_service

router = APIRouter(tags=["medications"])


@router.post(
    "/patients/{id}/medications",
    response_model=MedicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create medication for a patient (Caregiver only)",
)
def create_medication(
    id: int,
    med_in: MedicationCreate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return medication_service.create_medication(db, id, med_in)


@router.get(
    "/medications/{id}",
    response_model=MedicationResponse,
    summary="Get medication details",
)
def get_medication(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
):
    return medication_service.get_medication(db, id)


@router.patch(
    "/medications/{id}",
    response_model=MedicationResponse,
    summary="Update medication details (Caregiver only)",
)
def update_medication(
    id: int,
    med_in: MedicationUpdate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return medication_service.update_medication(db, id, med_in)


@router.post(
    "/medications/{id}/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule for medication (Caregiver only)",
)
def create_schedule(
    id: int,
    sched_in: ScheduleCreate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return medication_service.create_schedule(db, id, sched_in)
