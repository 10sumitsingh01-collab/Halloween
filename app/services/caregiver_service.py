from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models import Caregiver
from app.schemas.caregiver import CaregiverCreate


def create_caregiver(db: Session, caregiver_in: CaregiverCreate) -> Caregiver:
    caregiver = Caregiver(
        name=caregiver_in.name,
        phone=caregiver_in.phone,
        whatsapp_id=caregiver_in.whatsapp_id,
        relationship=caregiver_in.relationship,
        notify_level=caregiver_in.notify_level,
    )
    db.add(caregiver)
    db.commit()
    db.refresh(caregiver)
    return caregiver


def get_caregiver(db: Session, caregiver_id: int) -> Caregiver:
    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
    if not caregiver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"Caregiver with id {caregiver_id} not found"},
        )
    return caregiver
