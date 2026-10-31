from datetime import datetime, date, time, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect

from app.models import (
    Patient,
    Caregiver,
    PatientCaregiver,
    Medication,
    Schedule,
    DoseEvent,
    PatientNote,
    EscalationLog,
    PatientPreference,
    NotifyLevel,
    DoseStatus,
    DoseSource,
    NoteKind,
)


def test_models_and_foreign_keys(db_session):
    # Create Patient
    patient = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    assert patient.id is not None
    assert patient.name == "Lakshmi Devi"

    # Create Caregiver
    caregiver = Caregiver(
        name="Ramesh Kumar",
        phone="+919876543210",
        whatsapp_id="whatsapp:+919876543210",
        relationship="son",
        notify_level=NotifyLevel.ALL,
    )
    db_session.add(caregiver)
    db_session.commit()
    db_session.refresh(caregiver)

    # Link via PatientCaregiver
    link = PatientCaregiver(patient_id=patient.id, caregiver_id=caregiver.id)
    db_session.add(link)
    db_session.commit()

    # Verify many-to-many relationship
    assert len(patient.caregivers) == 1
    assert patient.caregivers[0].name == "Ramesh Kumar"
    assert len(caregiver.patients) == 1
    assert caregiver.patients[0].name == "Lakshmi Devi"

    # Create Medication
    med = Medication(
        patient_id=patient.id,
        name="Metformin",
        strength="500mg",
        form="tablet",
        instructions="after breakfast",
        stock_remaining=14,
        refill_threshold=10,
        prescribed_by="Dr. Rao",
        active=True,
    )
    db_session.add(med)
    db_session.commit()
    db_session.refresh(med)
    assert med.id is not None

    # Create Schedule
    schedule = Schedule(
        medication_id=med.id,
        time_of_day=time(8, 0),
        days_of_week="0,1,2,3,4,5,6",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    # Create DoseEvent
    now_utc = datetime.now(timezone.utc)
    dose_event = DoseEvent(
        schedule_id=schedule.id,
        due_at=now_utc,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose_event)
    db_session.commit()
    db_session.refresh(dose_event)
    assert dose_event.id is not None
    assert dose_event.status == DoseStatus.PENDING

    # Create PatientNote
    note = PatientNote(
        patient_id=patient.id,
        text="Mild dizziness in the morning",
        kind=NoteKind.SYMPTOM,
        flagged_to_caregiver=True,
    )
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)
    assert note.id is not None
    assert note.kind == NoteKind.SYMPTOM

    # Create EscalationLog
    escalation = EscalationLog(
        dose_event_id=dose_event.id,
        step="REMINDER_1",
        channel="sms",
        sent_at=now_utc,
    )
    db_session.add(escalation)
    db_session.commit()
    db_session.refresh(escalation)
    assert escalation.id is not None

    # Create PatientPreference
    pref = PatientPreference(
        patient_id=patient.id,
        medication_id=med.id,
        reminder_offset_minutes=15,
    )
    db_session.add(pref)
    db_session.commit()
    db_session.refresh(pref)
    assert pref.reminder_offset_minutes == 15


def test_foreign_key_constraint(db_session):
    # Attempting to add medication for non-existent patient should fail under foreign key constraint
    med = Medication(
        patient_id=99999,
        name="Invalid Med",
        strength="10mg",
        form="tablet",
    )
    db_session.add(med)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_indexes_exist(db_session):
    inspector = inspect(db_session.bind)
    dose_indexes = inspector.get_indexes("dose_events")
    indexed_columns = [col for idx in dose_indexes for col in idx["column_names"]]
    assert "due_at" in indexed_columns
    assert "schedule_id" in indexed_columns
