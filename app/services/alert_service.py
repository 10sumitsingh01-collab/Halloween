import logging
from datetime import datetime, timedelta
from typing import Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.patient import Patient, PatientNote
from app.models.enums import NoteKind, NotifyLevel
from app.models.alert import AlertLog
from app.notifiers.base import BaseNotifier, PermanentNotifierError
from app.notifiers.templates import format_caregiver_assistant_alert
from app.schemas.patient import AssistantAlertRequest, AssistantAlertResponse
from app.services.clock import ensure_utc, utc_now

logger = logging.getLogger("uvicorn.error")

SUCCESS_MESSAGES = {
    "symptom": "I have told your caregiver. If you feel worse, please contact your doctor.",
    "needs_help": "I have told your caregiver that you need help.",
    "emergency": "If this is an emergency, call 112 now. I have told your caregiver.",
}

FAILURE_MESSAGES = {
    "symptom": "I could not reach your caregiver. If you feel worse, please contact your doctor.",
    "needs_help": "I could not reach your caregiver. Please ask someone near you for help.",
    "emergency": "I could not reach your caregiver. If this is an emergency, call 112 now.",
}


def process_assistant_alert(
    db: Session,
    patient_id: int,
    alert_in: AssistantAlertRequest,
    notifier: BaseNotifier,
    clock: Callable[[], datetime] = utc_now,
) -> AssistantAlertResponse:
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"Patient {patient_id} not found"},
        )

    now = ensure_utc(clock())
    category_val = alert_in.category.value

    # Rule 5: Always store note (when given) before cooldown check
    if alert_in.note and alert_in.note.strip():
        note = PatientNote(
            patient_id=patient.id,
            text=alert_in.note.strip(),
            kind=NoteKind.SYMPTOM,
            created_at=now,
            flagged_to_caregiver=True,
        )
        db.add(note)
        db.commit()

    # Rule 2: Cooldown check — count only AlertLog rows with status 'sent'
    cutoff = now - timedelta(minutes=settings.ALERT_COOLDOWN_MINUTES)
    recent_sent = (
        db.query(AlertLog)
        .filter(
            AlertLog.patient_id == patient.id,
            AlertLog.category == category_val,
            AlertLog.status == "sent",
            AlertLog.created_at >= cutoff,
        )
        .first()
    )
    if recent_sent:
        logger.info("assistant alert category=%s outcome=rate_limited", category_val)
        cooldown_msg = SUCCESS_MESSAGES[category_val]
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Too Many Requests",
                "detail": cooldown_msg,
                "patient_message": cooldown_msg,
                "sent": False,
                "caregivers_notified": 0,
            },
        )

    # Filter caregivers by notify_level
    eligible_caregivers = [
        cg for cg in patient.caregivers
        if cg.notify_level in (NotifyLevel.ALL, NotifyLevel.URGENT_ONLY, "all", "urgent_only")
    ]

    contact_attr = getattr(notifier, "contact_attr", "whatsapp_id")
    usable_caregivers = [
        cg for cg in eligible_caregivers
        if getattr(cg, contact_attr, None)
    ]

    if not usable_caregivers:
        outcome = "no_recipients"
        sent = False
        notified_count = 0
        patient_msg = FAILURE_MESSAGES[category_val]
    else:
        alert_text = format_caregiver_assistant_alert(patient, category_val, now)
        notified_count = 0
        for cg in usable_caregivers:
            try:
                success = notifier.send_caregiver_alert(cg, alert_text)
                if success:
                    notified_count += 1
            except PermanentNotifierError:
                pass
            except Exception as exc:
                logger.warning("caregiver alert failed with %s", exc.__class__.__name__)

        if notified_count > 0:
            outcome = "sent"
            sent = True
            patient_msg = SUCCESS_MESSAGES[category_val]
        else:
            outcome = "failed"
            sent = False
            patient_msg = FAILURE_MESSAGES[category_val]

    # Record in AlertLog
    log_row = AlertLog(
        patient_id=patient.id,
        category=category_val,
        created_at=now,
        caregivers_notified=notified_count,
        status=outcome,
    )
    db.add(log_row)
    db.commit()

    # Exactly one fixed INFO line per alert
    logger.info("assistant alert category=%s outcome=%s", category_val, outcome)

    return AssistantAlertResponse(
        sent=sent,
        caregivers_notified=notified_count,
        patient_message=patient_msg,
    )

