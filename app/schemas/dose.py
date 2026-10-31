from datetime import date, datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.enums import DoseStatus, DoseSource, EscalationStatus


class DoseTodayResponse(BaseModel):
    id: int
    schedule_id: int
    due_at: datetime
    reminded_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    status: DoseStatus
    source: Optional[DoseSource] = None
    medication_name: str
    medication_strength: str
    medication_instructions: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DoseTakenRequest(BaseModel):
    source: Optional[DoseSource] = DoseSource.PHONE


class DoseSnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=60)


class DoseSnoozeResponse(BaseModel):
    dose_id: int
    snooze_until: datetime
    minutes: int


class DoseResponse(BaseModel):
    id: int
    schedule_id: int
    due_at: datetime
    reminded_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    status: DoseStatus
    source: Optional[DoseSource] = None

    model_config = ConfigDict(from_attributes=True)



# Layer 3: admin endpoint response schemas

class GenerateDosesResponse(BaseModel):
    generated: int
    skipped_duplicates: int
    patient_id: Optional[int] = None
    start_date: str
    days: int


class MarkMissedResponse(BaseModel):
    marked_missed: int


# Layer 4: Stage 4b Part 3 escalation response schemas

class RunTickResponse(BaseModel):
    sent: int
    failed: int
    skipped: int
    by_step: Dict[str, Dict[str, int]] = Field(default_factory=dict)


class EscalationLogResponse(BaseModel):
    step: str
    recipient: str
    channel: str
    status: EscalationStatus
    attempts: int
    sent_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    detail: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def handle_sent_at(cls, data: Any) -> Any:
        # Rows that are pending or failed have no sent_at in the response
        if hasattr(data, "status"):
            st = getattr(data, "status")
            if st != EscalationStatus.SENT and st != "sent":
                return {
                    "step": data.step,
                    "recipient": data.recipient,
                    "channel": data.channel,
                    "status": data.status,
                    "attempts": data.attempts,
                    "sent_at": None,
                    "acknowledged_at": data.acknowledged_at,
                    "detail": data.detail,
                }
        elif isinstance(data, dict):
            st = data.get("status")
            if st != EscalationStatus.SENT and st != "sent":
                data = dict(data)
                data["sent_at"] = None
        return data
