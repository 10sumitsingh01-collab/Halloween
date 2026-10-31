import enum


class NotifyLevel(str, enum.Enum):
    ALL = "all"
    MISSED_ONLY = "missed_only"
    URGENT_ONLY = "urgent_only"


class DoseStatus(str, enum.Enum):
    PENDING = "pending"
    TAKEN = "taken"
    LATE = "late"
    MISSED = "missed"
    SKIPPED = "skipped"


class DoseSource(str, enum.Enum):
    WATCH = "watch"
    PHONE = "phone"
    VOICE = "voice"
    CAREGIVER = "caregiver"
    WHATSAPP = "whatsapp"


class NoteKind(str, enum.Enum):
    SYMPTOM = "symptom"
    MOOD = "mood"
    QUESTION = "question"


class EscalationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

