from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict
import zoneinfo
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Patient,
    Caregiver,
    PatientCaregiver,
    PatientNote,
    Medication,
    Schedule,
    DoseEvent,
    DoseStatus,
)
from app.schemas.patient import PatientCreate, PatientNoteCreate
from app.services.clock import ensure_utc, utc_now


def create_patient(db: Session, patient_in: PatientCreate) -> Patient:
    patient = Patient(
        name=patient_in.name,
        age=patient_in.age,
        language=patient_in.language,
        timezone=patient_in.timezone,
        wake_time=patient_in.wake_time,
        sleep_time=patient_in.sleep_time,
        whatsapp_id=patient_in.whatsapp_id,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: int) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"Patient with id {patient_id} not found"},
        )
    return patient


def link_caregiver_to_patient(
    db: Session, patient_id: int, caregiver_id: int
) -> Dict[str, Any]:
    patient = get_patient(db, patient_id)
    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
    if not caregiver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"Caregiver with id {caregiver_id} not found"},
        )

    link = (
        db.query(PatientCaregiver)
        .filter(
            PatientCaregiver.patient_id == patient.id,
            PatientCaregiver.caregiver_id == caregiver.id,
        )
        .first()
    )
    if not link:
        link = PatientCaregiver(patient_id=patient.id, caregiver_id=caregiver.id)
        db.add(link)
        db.commit()

    return {
        "message": f"Caregiver {caregiver_id} linked to patient {patient_id}",
        "patient_id": patient_id,
        "caregiver_id": caregiver_id,
    }


def create_patient_note(
    db: Session,
    patient_id: int,
    note_in: PatientNoteCreate,
    clock: Callable[[], datetime] = utc_now,
) -> PatientNote:
    patient = get_patient(db, patient_id)
    note = PatientNote(
        patient_id=patient.id,
        text=note_in.text,
        kind=note_in.kind,
        created_at=ensure_utc(clock()),
        flagged_to_caregiver=note_in.flagged_to_caregiver,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def calculate_patient_adherence(
    db: Session,
    patient_id: int,
    days: int = 7,
    clock: Callable[[], datetime] = utc_now,
) -> Dict[str, Any]:
    patient = get_patient(db, patient_id)
    now_utc = ensure_utc(clock())
    start_time = now_utc - timedelta(days=days)

    events = (
        db.query(DoseEvent)
        .join(Schedule, DoseEvent.schedule_id == Schedule.id)
        .join(Medication, Schedule.medication_id == Medication.id)
        .filter(
            Medication.patient_id == patient.id,
            DoseEvent.due_at >= start_time,
            DoseEvent.due_at <= now_utc,
        )
        .all()
    )

    counts = {s.value: 0 for s in DoseStatus}
    for event in events:
        counts[event.status.value] += 1

    total = len(events)
    taken_count = counts.get(DoseStatus.TAKEN.value, 0)
    late_count = counts.get(DoseStatus.LATE.value, 0)
    pending_count = counts.get(DoseStatus.PENDING.value, 0)
    skipped_count = counts.get(DoseStatus.SKIPPED.value, 0)

    denominator = total - pending_count - skipped_count
    adherence_percentage = (
        round(((taken_count + late_count) / denominator) * 100.0, 2)
        if denominator > 0
        else None
    )

    return {
        "patient_id": patient.id,
        "days": days,
        "total_events": total,
        "status_counts": counts,
        "adherence_percentage": adherence_percentage,
    }
