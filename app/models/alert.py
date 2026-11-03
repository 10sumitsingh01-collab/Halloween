from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db import Base


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id = Column(
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    caregivers_notified = Column(Integer, nullable=False, default=0)
    status = Column(String(64), nullable=False, default="sent")

    patient = relationship("Patient", backref="alert_logs")
