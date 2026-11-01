from datetime import datetime, time, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Time,
    DateTime,
    Boolean,
    Text,
    ForeignKey,
    Enum,
)
from sqlalchemy.orm import relationship
from app.db import Base
from app.models.enums import NoteKind


class PatientCaregiver(Base):
    __tablename__ = "patient_caregivers"

    patient_id = Column(
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    caregiver_id = Column(
        Integer,
        ForeignKey("caregivers.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)
    age = Column(Integer, nullable=False)
    language = Column(String, nullable=False, default="kn")
    timezone = Column(String, nullable=False, default="Asia/Kolkata")
    wake_time = Column(Time, nullable=False)
    sleep_time = Column(Time, nullable=False)
    whatsapp_id = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)


    caregivers = relationship(
        "Caregiver",
        secondary="patient_caregivers",
        back_populates="patients",
    )
    medications = relationship(
        "Medication",
        back_populates="patient",
        cascade="all, delete-orphan",
    )
    notes = relationship(
        "PatientNote",
        back_populates="patient",
        cascade="all, delete-orphan",
    )
    preferences = relationship(
        "PatientPreference",
        back_populates="patient",
        cascade="all, delete-orphan",
    )


class PatientNote(Base):
    __tablename__ = "patient_notes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id = Column(
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = Column(Text, nullable=False)
    kind = Column(
        Enum(NoteKind, native_enum=False),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    flagged_to_caregiver = Column(Boolean, nullable=False, default=False)

    patient = relationship("Patient", back_populates="notes")


class PatientPreference(Base):
    __tablename__ = "patient_preferences"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id = Column(
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medication_id = Column(
        Integer,
        ForeignKey("medications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reminder_offset_minutes = Column(Integer, nullable=False, default=0)

    patient = relationship("Patient", back_populates="preferences")
    medication = relationship("Medication")
