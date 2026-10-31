"""
app/services/dose_service.py

Thin coordination layer between routers and dose_engine.
All business rules live in dose_engine.py; this module only orchestrates
DB queries that don't belong to a single engine function.
"""
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List
import zoneinfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    DoseEvent,
    Schedule,
    Medication,
    DoseStatus,
    DoseSource,
)
from app.services.clock import ensure_utc, utc_now
from app.services.patient_service import get_patient
from app.services import dose_engine


def get_today_doses(
    db: Session,
    patient_id: int,
    clock: Callable[[], datetime] = utc_now,
) -> List[Dict[str, Any]]:
    """Return all DoseEvents whose due_at falls on TODAY in the patient's local timezone."""
    patient = get_patient(db, patient_id)
    try:
        tz = zoneinfo.ZoneInfo(patient.timezone)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    now_utc = ensure_utc(clock())
    now_patient_tz = now_utc.astimezone(tz)
    today_patient = now_patient_tz.date()

    start_local = datetime.combine(today_patient, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(today_patient, time.max).replace(tzinfo=tz)

    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    results = (
        db.query(
            DoseEvent,
            Medication.name.label("medication_name"),
            Medication.strength.label("medication_strength"),
            Medication.instructions.label("medication_instructions"),
        )
        .join(Schedule, DoseEvent.schedule_id == Schedule.id)
        .join(Medication, Schedule.medication_id == Medication.id)
        .filter(
            Medication.patient_id == patient.id,
            DoseEvent.due_at >= start_utc,
            DoseEvent.due_at <= end_utc,
        )
        .order_by(DoseEvent.due_at.asc())
        .all()
    )

    return [
        {
            "id": event.id,
            "schedule_id": event.schedule_id,
            "due_at": event.due_at,
            "reminded_at": event.reminded_at,
            "responded_at": event.responded_at,
            "snoozed_until": event.snoozed_until,
            "status": event.status,
            "source": event.source,
            "medication_name": med_name,
            "medication_strength": med_strength,
            "medication_instructions": med_instructions,
        }
        for event, med_name, med_strength, med_instructions in results
    ]


def confirm_dose_taken(
    db: Session,
    dose_id: int,
    source: DoseSource | None,
    is_caregiver: bool,
    clock: Callable[[], datetime] = utc_now,
) -> DoseEvent:
    """Delegate to dose_engine.confirm_taken (all rules live there)."""
    return dose_engine.confirm_taken(db, dose_id, source, is_caregiver, clock)


def mark_dose_skipped(
    db: Session,
    dose_id: int,
    clock: Callable[[], datetime] = utc_now,
) -> DoseEvent:
    dose = db.query(DoseEvent).filter(DoseEvent.id == dose_id).first()
    if not dose:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"DoseEvent with id {dose_id} not found"},
        )

    dose.status = DoseStatus.SKIPPED
    dose.responded_at = ensure_utc(clock())
    db.commit()
    db.refresh(dose)
    return dose


def snooze_dose(
    db: Session,
    dose_id: int,
    minutes: int = 10,
    clock: Callable[[], datetime] = utc_now,
) -> Dict[str, Any]:
    dose = db.query(DoseEvent).filter(DoseEvent.id == dose_id).first()
    if not dose:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"DoseEvent with id {dose_id} not found"},
        )

    snooze_until = ensure_utc(clock()) + timedelta(minutes=minutes)
    dose.snoozed_until = snooze_until
    db.commit()
    db.refresh(dose)

    return {
        "dose_id": dose.id,
        "snooze_until": snooze_until,
        "minutes": minutes,
    }

