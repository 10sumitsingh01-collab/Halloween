from sqlalchemy import Column, Integer, String, Enum
from sqlalchemy.orm import relationship as sa_relationship
from app.db import Base
from app.models.enums import NotifyLevel


class Caregiver(Base):
    __tablename__ = "caregivers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    whatsapp_id = Column(String, nullable=False)
    telegram_chat_id = Column(String, nullable=True)
    relationship = Column(String, nullable=False)
    notify_level = Column(
        Enum(NotifyLevel, native_enum=False),
        nullable=False,
        default=NotifyLevel.ALL,
    )

    patients = sa_relationship(
        "Patient",
        secondary="patient_caregivers",
        back_populates="caregivers",
    )
