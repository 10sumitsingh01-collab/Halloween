"""
app/notifiers/templates.py

Deterministic, fixed message templates for patient reminders and caregiver alerts.
All timestamps are rendered in the patient's configured timezone.
No advice, no diagnosis, no LLM calls.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.models.patient import Patient
from app.models.dose import DoseEvent



def _get_patient_tz(patient: Patient) -> ZoneInfo:
    tz_str = getattr(patient, "timezone", None) or "Asia/Kolkata"
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("Asia/Kolkata")


def _format_due_time(due_at, tz: ZoneInfo) -> str:
    # Ensure timezone-aware conversion from UTC
    utc_dt = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone(tz)
    return local_dt.strftime("%I:%M %p").lstrip("0")



def format_patient_reminder(patient: Patient, dose_event: DoseEvent) -> str:
    """
    Fixed reminder text for the patient:
      Medicine name, strength, instructions, and due time in patient's timezone.
    """
    tz = _get_patient_tz(patient)
    due_str = _format_due_time(dose_event.due_at, tz)
    med = dose_event.schedule.medication

    parts = [
        f"Medication Reminder: {med.name} ({med.strength})",
        f"Scheduled for: {due_str}",
    ]
    if med.instructions:
        parts.append(f"Instructions: {med.instructions}")

    return "\n".join(parts)


def format_caregiver_unconfirmed_alert(patient: Patient, dose_event: DoseEvent) -> str:
    """
    Caregiver Escalation (Step 3: dose not taken yet after CAREGIVER_ESCALATION_MINUTES):
      Includes patient name, medicine, strength, and due time in patient's timezone.
    """
    tz = _get_patient_tz(patient)
    due_str = _format_due_time(dose_event.due_at, tz)
    med = dose_event.schedule.medication

    return (
        f"Caregiver Alert: {patient.name} has not confirmed their {due_str} "
        f"dose of {med.name} ({med.strength})."
    )


def format_caregiver_missed_alert(patient: Patient, dose_event: DoseEvent) -> str:
    """
    Caregiver Alert (Step 4: dose missed):
      Includes patient name, medicine, strength, and due time in patient's timezone.
    """
    tz = _get_patient_tz(patient)
    due_str = _format_due_time(dose_event.due_at, tz)
    med = dose_event.schedule.medication

    return (
        f"Caregiver Alert: Dose missed. {patient.name} did not take their {due_str} "
        f"dose of {med.name} ({med.strength})."
    )


def format_caregiver_assistant_alert(
    patient: Patient,
    category: str,
    report_time: datetime,
) -> str:
    """
    Caregiver alert triggered by patient via assistant key:
      Includes patient name, category, and report time in patient's timezone.
      Never includes note text or model-written text.
    """
    tz = _get_patient_tz(patient)
    time_str = _format_due_time(report_time, tz)
    return (
        f"MedMate alert: {patient.name} says they need help "
        f"({category} reported at {time_str}). Please check on them."
    )

