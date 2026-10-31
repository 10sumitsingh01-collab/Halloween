from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Enum,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.db import Base
from app.models.enums import DoseStatus, DoseSource, EscalationStatus


class DoseEvent(Base):
    __tablename__ = "dose_events"
    __table_args__ = (
        UniqueConstraint("schedule_id", "due_at", name="uq_dose_schedule_due"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    schedule_id = Column(
        Integer,
        ForeignKey("schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    due_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    reminded_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(DoseStatus, native_enum=False),
        nullable=False,
        default=DoseStatus.PENDING,
        index=True,
    )
    source = Column(
        Enum(DoseSource, native_enum=False),
        nullable=True,
    )

    schedule = relationship("Schedule", back_populates="dose_events")
    escalation_logs = relationship(
        "EscalationLog",
        back_populates="dose_event",
        cascade="all, delete-orphan",
    )


class EscalationLog(Base):
    __tablename__ = "escalation_logs"
    __table_args__ = (
        UniqueConstraint(
            "dose_event_id",
            "step",
            "recipient",
            name="uq_escalation_event_step_recipient",
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    dose_event_id = Column(
        Integer,
        ForeignKey("dose_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step = Column(String(64), nullable=False)
    recipient = Column(String, nullable=False, default="patient", server_default="patient")
    channel = Column(String, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(
        Enum(EscalationStatus, native_enum=False),
        nullable=False,
        default=EscalationStatus.PENDING,
        server_default="pending",
    )
    detail = Column(Text, nullable=True)

    dose_event = relationship("DoseEvent", back_populates="escalation_logs")


