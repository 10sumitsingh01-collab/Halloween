from datetime import date, time
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class MedicationCreate(BaseModel):
    name: str = Field(..., min_length=1)
    strength: str = Field(..., min_length=1)
    form: str = Field(..., min_length=1)
    instructions: Optional[str] = None
    stock_remaining: int = Field(default=0, ge=0)
    refill_threshold: int = Field(default=0, ge=0)
    prescribed_by: Optional[str] = None
    active: bool = True


class MedicationUpdate(BaseModel):
    name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    instructions: Optional[str] = None
    stock_remaining: Optional[int] = Field(default=None, ge=0)
    refill_threshold: Optional[int] = Field(default=None, ge=0)
    prescribed_by: Optional[str] = None
    active: Optional[bool] = None


class MedicationResponse(BaseModel):
    id: int
    patient_id: int
    name: str
    strength: str
    form: str
    instructions: Optional[str]
    stock_remaining: int
    refill_threshold: int
    prescribed_by: Optional[str]
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ScheduleCreate(BaseModel):
    time_of_day: time
    days_of_week: str = Field(..., min_length=1)
    dose_amount: float = Field(default=1.0, gt=0)
    start_date: date
    end_date: Optional[date] = None


class ScheduleResponse(BaseModel):
    id: int
    medication_id: int
    time_of_day: time
    days_of_week: str
    dose_amount: float
    start_date: date
    end_date: Optional[date]

    model_config = ConfigDict(from_attributes=True)


# Layer 3: refill alert response

class RefillResponse(BaseModel):
    patient_id: int
    medications: list[MedicationResponse]
