from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Boolean,
    Time,
    Date,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from app.db import Base


class Medication(Base):
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id = Column(
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=False)
    strength = Column(String, nullable=False)
    form = Column(String, nullable=False)
    instructions = Column(String, nullable=True)
    stock_remaining = Column(Integer, nullable=False, default=0)
    refill_threshold = Column(Integer, nullable=False, default=0)
    prescribed_by = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    patient = relationship("Patient", back_populates="medications")
    schedules = relationship(
        "Schedule",
        back_populates="medication",
        cascade="all, delete-orphan",
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    medication_id = Column(
        Integer,
        ForeignKey("medications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    time_of_day = Column(Time, nullable=False)
    days_of_week = Column(String, nullable=False)  # Comma-separated days, e.g., "0,1,2,3,4,5,6" or "daily", "sun"
    dose_amount = Column(Float, nullable=False, default=1.0)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)

    medication = relationship("Medication", back_populates="schedules")
    dose_events = relationship(
        "DoseEvent",
        back_populates="schedule",
        cascade="all, delete-orphan",
    )
