import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./medmate.db"
    CAREGIVER_API_KEY: str = "test_caregiver_key_123"
    PATIENT_API_KEY: str = "test_patient_key_456"
    DEFAULT_TIMEZONE: str = "Asia/Kolkata"

    # Dose status timing constants (Layer 3, floats allowed for demo mode)
    TAKEN_WINDOW_MINUTES: float = 15.0        # pending→taken if confirmed within this many min of due_at
    LATE_WINDOW_MINUTES: float = 120.0       # pending→late if confirmed within this many min; missed after
    DOUBLE_DOSE_GUARD_MINUTES: float = 30.0   # block a second confirm if previous responded_at < this ago
    EARLY_WINDOW_MINUTES: float = 60.0        # block confirm if now < due_at - this many min
    DEFAULT_SNOOZE_MINUTES: int = 10
    MAX_SNOOZE_MINUTES: int = 60

    # Escalation & Reminder timing constants (Layer 4, decimals allowed for demo mode)
    REMINDER_REPEAT_MINUTES: float = 10.0
    MAX_PATIENT_REMINDERS: int = 2
    CAREGIVER_ESCALATION_MINUTES: float = 20.0
    SCHEDULER_TICK_SECONDS: float = 30.0
    SCHEDULER_ENABLED: bool = True
    ESCALATION_LOOKBACK_HOURS: float = 24.0
    MISSED_ALERT_MAX_AGE_MINUTES: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
