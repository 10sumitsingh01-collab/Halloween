from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models import Medication, Schedule
from app.schemas.medication import MedicationCreate, MedicationUpdate, ScheduleCreate
from app.services.patient_service import get_patient


def create_medication(
    db: Session, patient_id: int, med_in: MedicationCreate
) -> Medication:
    get_patient(db, patient_id)
    medication = Medication(
        patient_id=patient_id,
        name=med_in.name,
        strength=med_in.strength,
        form=med_in.form,
        instructions=med_in.instructions,
        stock_remaining=med_in.stock_remaining,
        refill_threshold=med_in.refill_threshold,
        prescribed_by=med_in.prescribed_by,
        active=med_in.active,
    )
    db.add(medication)
    db.commit()
    db.refresh(medication)
    return medication


def get_medication(db: Session, medication_id: int) -> Medication:
    medication = db.query(Medication).filter(Medication.id == medication_id).first()
    if not medication:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"Medication with id {medication_id} not found"},
        )
    return medication


def update_medication(
    db: Session, medication_id: int, update_in: MedicationUpdate
) -> Medication:
    medication = get_medication(db, medication_id)
    update_data = update_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(medication, field, value)
    db.commit()
    db.refresh(medication)
    return medication


def create_schedule(
    db: Session, medication_id: int, sched_in: ScheduleCreate
) -> Schedule:
    get_medication(db, medication_id)
    schedule = Schedule(
        medication_id=medication_id,
        time_of_day=sched_in.time_of_day,
        days_of_week=sched_in.days_of_week,
        dose_amount=sched_in.dose_amount,
        start_date=sched_in.start_date,
        end_date=sched_in.end_date,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule
