from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import require_caregiver_key, require_any_key, require_assistant_or_patient_key
from app.notifiers import BaseNotifier, get_notifier
from app.schemas.patient import (
    PatientCreate,
    PatientResponse,
    PatientNoteCreate,
    PatientNoteResponse,
    AdherenceResponse,
    AssistantAlertRequest,
    AssistantAlertResponse,
)
from app.schemas.medication import RefillResponse, MedicationResponse
from app.services import patient_service, alert_service
from app.services import dose_engine
from app.services.clock import get_clock

router = APIRouter(tags=["patients"])


@router.post(
    "/patients",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new patient (Caregiver only)",
)
def create_patient(
    patient_in: PatientCreate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return patient_service.create_patient(db, patient_in)


@router.get(
    "/patients/{id}",
    response_model=PatientResponse,
    summary="Get patient details",
)
def get_patient(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
):
    return patient_service.get_patient(db, id)


@router.post(
    "/patients/{id}/caregivers/{cid}",
    summary="Link a caregiver to a patient (Caregiver only)",
)
def link_caregiver(
    id: int,
    cid: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
):
    return patient_service.link_caregiver_to_patient(db, id, cid)


@router.post(
    "/patients/{id}/notes",
    response_model=PatientNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a patient note/symptom/mood/question",
)
def create_note(
    id: int,
    note_in: PatientNoteCreate,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_assistant_or_patient_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return patient_service.create_patient_note(db, id, note_in, clock)


@router.get(
    "/patients/{id}/adherence",
    response_model=AdherenceResponse,
    summary="Get patient adherence statistics over the last N days",
)
def get_adherence(
    id: int,
    days: int = Query(default=7, ge=1),
    db: Session = Depends(get_db),
    _auth: str = Depends(require_assistant_or_patient_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return patient_service.calculate_patient_adherence(db, id, days, clock)


@router.post(
    "/patients/{id}/assistant/alerts",
    response_model=AssistantAlertResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger caregiver alert from patient assistant",
)
def create_assistant_alert(
    id: int,
    alert_in: AssistantAlertRequest,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_assistant_or_patient_key),
    clock: Callable[[], datetime] = Depends(get_clock),
    notifier: BaseNotifier = Depends(get_notifier),
):
    return alert_service.process_assistant_alert(
        db=db,
        patient_id=id,
        alert_in=alert_in,
        notifier=notifier,
        clock=clock,
    )


@router.get(
    "/patients/{id}/refills",
    response_model=RefillResponse,
    summary="List active medications at or below their refill threshold",
)
def get_refills(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
):
    meds = dose_engine.medications_needing_refill(db, patient_id=id)
    return RefillResponse(
        patient_id=id,
        medications=[MedicationResponse.model_validate(m) for m in meds],
    )
