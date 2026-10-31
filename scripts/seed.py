import os
import sys
from datetime import datetime, date, time, timezone
import zoneinfo
from sqlalchemy.orm import Session

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import SessionLocal, engine, Base
from app.models import (
    Patient,
    Caregiver,
    PatientCaregiver,
    Medication,
    Schedule,
    DoseEvent,
    NotifyLevel,
    DoseStatus,
)


def seed_database(db: Session) -> dict:
    """
    Idempotent database seeding.
    Can be run multiple times safely without creating duplicates.
    """
    # 1. Patient: Lakshmi Devi
    patient = db.query(Patient).filter(Patient.name == "Lakshmi Devi").first()
    if not patient:
        patient = Patient(
            name="Lakshmi Devi",
            age=72,
            language="kn",
            timezone="Asia/Kolkata",
            wake_time=time(7, 0),
            sleep_time=time(22, 0),
            whatsapp_id="+910000000000",
        )
        db.add(patient)
        db.flush()
    else:
        # Update attributes in case they changed
        patient.age = 72
        patient.language = "kn"
        patient.timezone = "Asia/Kolkata"
        patient.wake_time = time(7, 0)
        patient.sleep_time = time(22, 0)
        patient.whatsapp_id = "+910000000000"
        db.flush()

    # 2. Caregivers
    caregiver1 = db.query(Caregiver).filter(Caregiver.phone == "+919876543210").first()
    if not caregiver1:
        caregiver1 = Caregiver(
            name="Ramesh Kumar",
            phone="+919876543210",
            whatsapp_id="whatsapp:+919876543210",
            relationship="Son",
            notify_level=NotifyLevel.ALL,
        )
        db.add(caregiver1)
        db.flush()

    caregiver2 = db.query(Caregiver).filter(Caregiver.phone == "+919876543211").first()
    if not caregiver2:
        caregiver2 = Caregiver(
            name="Dr. Ananya Rao",
            phone="+919876543211",
            whatsapp_id="whatsapp:+919876543211",
            relationship="Family Physician & Caregiver",
            notify_level=NotifyLevel.MISSED_ONLY,
        )
        db.add(caregiver2)
        db.flush()

    # Link caregivers
    for cg in [caregiver1, caregiver2]:
        link = (
            db.query(PatientCaregiver)
            .filter(
                PatientCaregiver.patient_id == patient.id,
                PatientCaregiver.caregiver_id == cg.id,
            )
            .first()
        )
        if not link:
            db.add(PatientCaregiver(patient_id=patient.id, caregiver_id=cg.id))
    db.flush()

    # 3. Medications & Schedules
    # Med 1: Metformin 500mg, 8:00 AM daily, after breakfast (close to refill threshold: 12 remaining vs 10 threshold)
    med1 = (
        db.query(Medication)
        .filter(Medication.patient_id == patient.id, Medication.name == "Metformin")
        .first()
    )
    if not med1:
        med1 = Medication(
            patient_id=patient.id,
            name="Metformin",
            strength="500mg",
            form="tablet",
            instructions="after breakfast",
            stock_remaining=12,  # Close to threshold (10)
            refill_threshold=10,
            prescribed_by="Dr. Ananya Rao",
            active=True,
        )
        db.add(med1)
        db.flush()

    sched1 = db.query(Schedule).filter(Schedule.medication_id == med1.id).first()
    if not sched1:
        sched1 = Schedule(
            medication_id=med1.id,
            time_of_day=time(8, 0),
            days_of_week="0,1,2,3,4,5,6",  # Daily
            dose_amount=1.0,
            start_date=date(2026, 1, 1),
        )
        db.add(sched1)
        db.flush()

    # Med 2: Amlodipine 5mg, 9:00 PM daily, after dinner
    med2 = (
        db.query(Medication)
        .filter(Medication.patient_id == patient.id, Medication.name == "Amlodipine")
        .first()
    )
    if not med2:
        med2 = Medication(
            patient_id=patient.id,
            name="Amlodipine",
            strength="5mg",
            form="tablet",
            instructions="after dinner",
            stock_remaining=30,
            refill_threshold=5,
            prescribed_by="Dr. Ananya Rao",
            active=True,
        )
        db.add(med2)
        db.flush()

    sched2 = db.query(Schedule).filter(Schedule.medication_id == med2.id).first()
    if not sched2:
        sched2 = Schedule(
            medication_id=med2.id,
            time_of_day=time(21, 0),
            days_of_week="0,1,2,3,4,5,6",  # Daily
            dose_amount=1.0,
            start_date=date(2026, 1, 1),
        )
        db.add(sched2)
        db.flush()

    # Med 3: Vitamin D3 60000IU, Sunday 10:00 AM, weekly
    med3 = (
        db.query(Medication)
        .filter(Medication.patient_id == patient.id, Medication.name == "Vitamin D3")
        .first()
    )
    if not med3:
        med3 = Medication(
            patient_id=patient.id,
            name="Vitamin D3",
            strength="60000IU",
            form="capsule",
            instructions="Sunday 10:00 AM, with milk",
            stock_remaining=8,
            refill_threshold=2,
            prescribed_by="Dr. Ananya Rao",
            active=True,
        )
        db.add(med3)
        db.flush()

    sched3 = db.query(Schedule).filter(Schedule.medication_id == med3.id).first()
    if not sched3:
        sched3 = Schedule(
            medication_id=med3.id,
            time_of_day=time(10, 0),
            days_of_week="6",  # Sunday
            dose_amount=1.0,
            start_date=date(2026, 1, 1),
        )
        db.add(sched3)
        db.flush()

    # 4. DoseEvents for Today (Patient's timezone Asia/Kolkata)
    tz = zoneinfo.ZoneInfo(patient.timezone)
    now_in_tz = datetime.now(tz)
    today_date = now_in_tz.date()

    # Dose 1: 8:00 AM IST today
    dt_dose1_local = datetime.combine(today_date, time(8, 0)).replace(tzinfo=tz)
    dt_dose1_utc = dt_dose1_local.astimezone(timezone.utc)
    existing_dose1 = (
        db.query(DoseEvent)
        .filter(DoseEvent.schedule_id == sched1.id, DoseEvent.due_at == dt_dose1_utc)
        .first()
    )
    if not existing_dose1:
        db.add(
            DoseEvent(
                schedule_id=sched1.id,
                due_at=dt_dose1_utc,
                status=DoseStatus.PENDING,
            )
        )

    # Dose 2: 9:00 PM (21:00) IST today
    dt_dose2_local = datetime.combine(today_date, time(21, 0)).replace(tzinfo=tz)
    dt_dose2_utc = dt_dose2_local.astimezone(timezone.utc)
    existing_dose2 = (
        db.query(DoseEvent)
        .filter(DoseEvent.schedule_id == sched2.id, DoseEvent.due_at == dt_dose2_utc)
        .first()
    )
    if not existing_dose2:
        db.add(
            DoseEvent(
                schedule_id=sched2.id,
                due_at=dt_dose2_utc,
                status=DoseStatus.PENDING,
            )
        )

    # Dose 3: 10:00 AM IST today (for testing today's dose events visibility)
    dt_dose3_local = datetime.combine(today_date, time(10, 0)).replace(tzinfo=tz)
    dt_dose3_utc = dt_dose3_local.astimezone(timezone.utc)
    existing_dose3 = (
        db.query(DoseEvent)
        .filter(DoseEvent.schedule_id == sched3.id, DoseEvent.due_at == dt_dose3_utc)
        .first()
    )
    if not existing_dose3:
        db.add(
            DoseEvent(
                schedule_id=sched3.id,
                due_at=dt_dose3_utc,
                status=DoseStatus.PENDING,
            )
        )

    db.commit()

    return {
        "patient": patient.name,
        "caregivers_count": db.query(Caregiver).count(),
        "medications_count": db.query(Medication).count(),
        "schedules_count": db.query(Schedule).count(),
        "dose_events_count": db.query(DoseEvent).count(),
    }


if __name__ == "__main__":
    db = SessionLocal()
    try:
        result = seed_database(db)
        print("Seeding completed successfully:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    finally:
        db.close()
