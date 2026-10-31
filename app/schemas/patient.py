from datetime import time, datetime
from typing import Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import NoteKind


class PatientCreate(BaseModel):
    name: str = Field(..., min_length=1)
    age: int = Field(..., gt=0)
    language: str = Field(default="kn")
    timezone: str = Field(default="Asia/Kolkata")
    wake_time: time
    sleep_time: time
    whatsapp_id: Optional[str] = None


class PatientResponse(BaseModel):
    id: int
    name: str
    age: int
    language: str
    timezone: str
    wake_time: time
    sleep_time: time
    whatsapp_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)



class PatientNoteCreate(BaseModel):
    text: str = Field(..., min_length=1)
    kind: NoteKind
    flagged_to_caregiver: bool = False


class PatientNoteResponse(BaseModel):
    id: int
    patient_id: int
    text: str
    kind: NoteKind
    created_at: datetime
    flagged_to_caregiver: bool

    model_config = ConfigDict(from_attributes=True)


class AdherenceResponse(BaseModel):
    patient_id: int
    days: int
    total_events: int
    status_counts: Dict[str, int]
    adherence_percentage: Optional[float]
