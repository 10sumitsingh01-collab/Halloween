from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import NotifyLevel


class CaregiverCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5)
    whatsapp_id: str = Field(..., min_length=5)
    relationship: str = Field(..., min_length=1)
    notify_level: NotifyLevel = NotifyLevel.ALL


class CaregiverResponse(BaseModel):
    id: int
    name: str
    phone: str
    whatsapp_id: str
    relationship: str
    notify_level: NotifyLevel

    model_config = ConfigDict(from_attributes=True)
