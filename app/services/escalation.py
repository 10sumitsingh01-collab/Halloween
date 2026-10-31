"""
app/services/escalation.py

Deterministic reminder delivery and escalation ladder for MedMate.
Implements Layer 4 escalation tick, lookback filtering, contact validation,
retry tracking, terminal dose acknowledgement, caregiver escalation,
and snooze resume.
"""
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
import logging

from sqlalchemy.orm import Session

from app.models.dose import DoseEvent, EscalationLog
from app.models.patient import Patient, PatientPreference
from app.models.caregiver import Caregiver
from app.models.enums import DoseStatus, EscalationStatus, NotifyLevel
from app.services.clock import ensure_utc, utc_now
from app.services.dose_engine import reminder_time, mark_overdue_as_missed
from app.notifiers.base import BaseNotifier
from app.notifiers.templates import (
    format_patient_reminder,
    format_caregiver_unconfirmed_alert,
    format_caregiver_missed_alert,
)
from app.config import settings

logger = logging.getLogger(__name__)


def doses_in_lookback(db: Session, now: datetime) -> List[DoseEvent]:
    """
    Return all DoseEvents whose due_at is within the lookback window
    (due_at >= now - ESCALATION_LOOKBACK_HOURS).
    Excludes old doses and seed data outside the active window.
    """
    lookback_cutoff = now - timedelta(hours=settings.ESCALATION_LOOKBACK_HOURS)
    return (
        db.query(DoseEvent)
        .filter(DoseEvent.due_at >= lookback_cutoff)
        .order_by(DoseEvent.due_at.asc())
        .all()
    )


def acknowledge_terminal_logs(db: Session, now: datetime) -> int:
    """
    Set acknowledged_at = now on all open EscalationLogs for doses
    that are in a terminal state (TAKEN, LATE, or SKIPPED).
    Returns count of updated logs.
    """
    open_logs = (
        db.query(EscalationLog)
        .join(DoseEvent, EscalationLog.dose_event_id == DoseEvent.id)
        .filter(
            DoseEvent.status.in_([DoseStatus.TAKEN, DoseStatus.LATE, DoseStatus.SKIPPED]),
            EscalationLog.acknowledged_at.is_(None),
        )
        .all()
    )
    for log in open_logs:
        log.acknowledged_at = now

    if open_logs:
        db.commit()
    return len(open_logs)


def normalize_step_group(step: str) -> str:
    """Group SNOOZE_RESUME_<timestamp> under SNOOZE_RESUME; leave others unchanged."""
    if step.startswith("SNOOZE_RESUME_"):
        return "SNOOZE_RESUME"
    return step


def record_summary(summary: Dict[str, Any], step: str, result: str) -> None:
    """Update top-level counts and by_step breakdown in process_tick summary."""
    if result not in ("sent", "failed", "skipped"):
        return
    summary[result] += 1
    group = normalize_step_group(step)
    if "by_step" not in summary:
        summary["by_step"] = {}
    if group not in summary["by_step"]:
        summary["by_step"][group] = {"sent": 0, "failed": 0, "skipped": 0}
    summary["by_step"][group][result] += 1


def get_or_create_log(
    db: Session,
    dose_id: int,
    step: str,
    recipient: str,
    now: datetime,
    channel: str = "unknown",
) -> EscalationLog:
    """
    Retrieve existing EscalationLog row for (dose_event_id, step, recipient)
    or create an uncommitted / pending row.
    """
    row = (
        db.query(EscalationLog)
        .filter(
            EscalationLog.dose_event_id == dose_id,
            EscalationLog.step == step,
            EscalationLog.recipient == recipient,
        )
        .first()
    )
    if not row:
        row = EscalationLog(
            dose_event_id=dose_id,
            step=step,
            recipient=recipient,
            channel=channel,
            sent_at=now,
            status=EscalationStatus.PENDING,
            attempts=0,
        )
        db.add(row)
        db.flush()
    return row


def try_send(
    db: Session,
    dose: DoseEvent,
    step: str,
    recipient: str,
    send_fn: Callable[[], bool],
    now: datetime,
    channel: str = "unknown",
    patient: Optional[Patient] = None,
    caregiver: Optional[Caregiver] = None,
) -> str:
    """
    Attempt to deliver a notification with per-send commit and retry tracking:
      - If already 'sent' or 'failed', skip.
      - If missing recipient contact, fail immediately with detail and no retries.
      - On send success: status='sent', sent_at=now.
      - On send failure: increment attempts. If attempts >= 3, status='failed'.
      - Detail contains only fixed strings or the exception CLASS NAME (no raw text).
    Commits to DB after EVERY send attempt.
    Returns: 'sent', 'failed', or 'skipped'.
    """
    row = get_or_create_log(db, dose.id, step, recipient, now, channel=channel)
    if row.status in (EscalationStatus.SENT, EscalationStatus.FAILED):
        return "skipped"

    # Contact check for patient recipient
    if recipient == "patient":
        if not patient or not getattr(patient, "whatsapp_id", None):
            row.status = EscalationStatus.FAILED
            row.attempts = 1
            row.detail = "patient has no whatsapp_id"
            db.commit()
            return "failed"

    # Contact check for caregiver recipient
    if recipient.startswith("caregiver:"):
        if not caregiver or not getattr(caregiver, "whatsapp_id", None):
            row.status = EscalationStatus.FAILED
            row.attempts = 1
            row.detail = "caregiver has no whatsapp_id"
            db.commit()
            return "failed"

    # Invoke delivery callback
    try:
        ok = bool(send_fn())
    except Exception as exc:
        logger.warning("Notifier exception for dose %s step %s: %s", dose.id, step, exc)
        ok = False
        # Privacy: Store only the exception class name, never raw exception text
        row.detail = exc.__class__.__name__

    if ok:
        row.status = EscalationStatus.SENT
        row.sent_at = now
        row.detail = None
        if recipient == "patient" and dose.reminded_at is None:
            dose.reminded_at = now
    else:
        row.attempts += 1
        if row.attempts >= 3:
            row.status = EscalationStatus.FAILED

    db.commit()
    return "sent" if ok else "failed"


def run_patient_ladder(
    db: Session,
    dose: DoseEvent,
    now: datetime,
    notifier: BaseNotifier,
    summary: Dict[str, Any],
) -> None:
    """
    Run patient reminder steps for a pending DoseEvent:
      1. Snooze check: while now < snoozed_until, sends nothing.
         After snooze ends: if still pending and within due_at + LATE_WINDOW,
         sends ONE reminder with step SNOOZE_RESUME_<utc iso to the minute>.
      2. Late window check: no reminder if now > due_at + LATE_WINDOW_MINUTES.
      3. Spam guard: does not send any normal reminder within REMINDER_REPEAT_MINUTES
         of the most recent patient message that was actually sent (including SNOOZE_RESUME).
      4. Step 1 (REMINDER_1): at reminder_time(due_at + offset).
      5. Step 2..N: at previous sent_at + REMINDER_REPEAT_MINUTES, ONLY IF
         the previous step was successfully 'sent'. Capped at MAX_PATIENT_REMINDERS.
    """
    due_at_utc = ensure_utc(dose.due_at)
    med = dose.schedule.medication
    patient = med.patient
    channel = getattr(notifier, "channel", "unknown")

    # 1. Snooze handling
    if dose.snoozed_until is not None:
        snoozed_until_utc = ensure_utc(dose.snoozed_until)
        if now < snoozed_until_utc:
            # While snoozed, patient ladder sends nothing
            return

        # After snooze ends: if still pending and within late window
        if dose.status == DoseStatus.PENDING and now <= due_at_utc + timedelta(minutes=settings.LATE_WINDOW_MINUTES):
            snooze_step = f"SNOOZE_RESUME_{snoozed_until_utc.strftime('%Y-%m-%dT%H:%M')}"
            res_snooze = try_send(
                db=db,
                dose=dose,
                step=snooze_step,
                recipient="patient",
                send_fn=lambda: notifier.send_patient_reminder(patient, dose),
                now=now,
                channel=channel,
                patient=patient,
            )
            record_summary(summary, snooze_step, res_snooze)

    # 2. Late window check
    if now > due_at_utc + timedelta(minutes=settings.LATE_WINDOW_MINUTES):
        return

    # 3. Normal reminder ladder
    pref = (
        db.query(PatientPreference)
        .filter(
            PatientPreference.patient_id == patient.id,
            (PatientPreference.medication_id == med.id)
            | (PatientPreference.medication_id.is_(None)),
        )
        .order_by(PatientPreference.medication_id.desc().nullslast())
        .first()
    )
    offset = pref.reminder_offset_minutes if pref else 0
    base_reminder = reminder_time(dose, offset)

    sent_patient_logs = (
        db.query(EscalationLog)
        .filter(
            EscalationLog.dose_event_id == dose.id,
            EscalationLog.recipient == "patient",
            EscalationLog.status == EscalationStatus.SENT,
        )
        .all()
    )

    # Step 1: REMINDER_1
    step1_log = (
        db.query(EscalationLog)
        .filter(
            EscalationLog.dose_event_id == dose.id,
            EscalationLog.step == "REMINDER_1",
            EscalationLog.recipient == "patient",
        )
        .first()
    )

    if step1_log:
        res1 = try_send(
            db=db,
            dose=dose,
            step="REMINDER_1",
            recipient="patient",
            send_fn=lambda: notifier.send_patient_reminder(patient, dose),
            now=now,
            channel=channel,
            patient=patient,
        )
        record_summary(summary, "REMINDER_1", res1)
    else:
        # Step 1 not yet attempted
        # Spam guard: do not send within REMINDER_REPEAT_MINUTES of any sent patient message
        if sent_patient_logs:
            latest_sent_at = max(ensure_utc(l.sent_at) for l in sent_patient_logs)
            repeat_delta = timedelta(minutes=settings.REMINDER_REPEAT_MINUTES)
            if now < latest_sent_at + repeat_delta:
                return

        if now >= base_reminder:
            res1 = try_send(
                db=db,
                dose=dose,
                step="REMINDER_1",
                recipient="patient",
                send_fn=lambda: notifier.send_patient_reminder(patient, dose),
                now=now,
                channel=channel,
                patient=patient,
            )
            record_summary(summary, "REMINDER_1", res1)

    # Re-query sent logs to include anything sent in this tick (e.g. SNOOZE_RESUME or REMINDER_1)
    fresh_sent_logs = (
        db.query(EscalationLog)
        .filter(
            EscalationLog.dose_event_id == dose.id,
            EscalationLog.recipient == "patient",
            EscalationLog.status == EscalationStatus.SENT,
        )
        .all()
    )

    # Prerequisite: step 1 must be successfully sent
    if not any(l.step == "REMINDER_1" for l in fresh_sent_logs):
        return

    # Spam guard for subsequent reminders:
    # Must not send within REMINDER_REPEAT_MINUTES of the most recent sent patient message (including SNOOZE_RESUME)
    latest_sent_at = max(ensure_utc(l.sent_at) for l in fresh_sent_logs)
    repeat_delta = timedelta(minutes=settings.REMINDER_REPEAT_MINUTES)
    if now < latest_sent_at + repeat_delta:
        return

    # Step 2..MAX_PATIENT_REMINDERS
    max_reminders = max(1, settings.MAX_PATIENT_REMINDERS)
    for step_idx in range(2, max_reminders + 1):
        cur_step = f"REMINDER_{step_idx}"
        prev_step = f"REMINDER_{step_idx - 1}"

        prev_log = (
            db.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == prev_step,
                EscalationLog.recipient == "patient",
            )
            .first()
        )
        if not prev_log or prev_log.status != EscalationStatus.SENT:
            break

        prev_sent_utc = ensure_utc(prev_log.sent_at)
        if now < prev_sent_utc + repeat_delta:
            break

        res_k = try_send(
            db=db,
            dose=dose,
            step=cur_step,
            recipient="patient",
            send_fn=lambda: notifier.send_patient_reminder(patient, dose),
            now=now,
            channel=channel,
            patient=patient,
        )
        record_summary(summary, cur_step, res_k)
        break


def run_caregiver_unconfirmed(
    db: Session,
    dose: DoseEvent,
    now: datetime,
    notifier: BaseNotifier,
    summary: Dict[str, Any],
) -> None:
    """
    Caregiver Escalation: for a pending dose, when
    now >= due_at + CAREGIVER_ESCALATION_MINUTES, send the caregiver
    "not confirmed" template to every caregiver linked to the patient with
    notify_level "all". Recipient string: "caregiver:<id>".
    Never send it for a dose that is already missed. Ignores snooze entirely.
    """
    if dose.status != DoseStatus.PENDING:
        return

    due_at_utc = ensure_utc(dose.due_at)
    escalation_time = due_at_utc + timedelta(minutes=settings.CAREGIVER_ESCALATION_MINUTES)
    if now < escalation_time:
        return

    patient = dose.schedule.medication.patient
    caregivers = patient.caregivers
    channel = getattr(notifier, "channel", "unknown")

    for caregiver in caregivers:
        level = getattr(caregiver.notify_level, "value", caregiver.notify_level)
        if level != "all":
            continue

        msg = format_caregiver_unconfirmed_alert(patient, dose)
        res = try_send(
            db=db,
            dose=dose,
            step="CAREGIVER_UNCONFIRMED",
            recipient=f"caregiver:{caregiver.id}",
            send_fn=lambda c=caregiver, m=msg: notifier.send_caregiver_alert(c, m),
            now=now,
            channel=channel,
            caregiver=caregiver,
        )
        record_summary(summary, "CAREGIVER_UNCONFIRMED", res)


def run_missed_alert(
    db: Session,
    dose: DoseEvent,
    now: datetime,
    notifier: BaseNotifier,
    summary: Dict[str, Any],
) -> None:
    """
    Missed Alert: for a dose with status missed inside the lookback window,
    send the caregiver "missed" template to caregivers with notify_level
    "all" or "missed_only". "urgent_only" caregivers get neither step.
    Recipient string: "caregiver:<id>".
    """
    if dose.status != DoseStatus.MISSED:
        return

    # Safety rule 1b: MISSED_ALERT is sent only if now <= due_at + LATE_WINDOW_MINUTES + MISSED_ALERT_MAX_AGE_MINUTES
    due_at_utc = ensure_utc(dose.due_at)
    max_missed_window = timedelta(minutes=settings.LATE_WINDOW_MINUTES + settings.MISSED_ALERT_MAX_AGE_MINUTES)
    if now > due_at_utc + max_missed_window:
        return

    patient = dose.schedule.medication.patient
    caregivers = patient.caregivers
    channel = getattr(notifier, "channel", "unknown")

    for caregiver in caregivers:
        level = getattr(caregiver.notify_level, "value", caregiver.notify_level)
        if level not in ("all", "missed_only"):
            continue

        msg = format_caregiver_missed_alert(patient, dose)
        res = try_send(
            db=db,
            dose=dose,
            step="MISSED_ALERT",
            recipient=f"caregiver:{caregiver.id}",
            send_fn=lambda c=caregiver, m=msg: notifier.send_caregiver_alert(c, m),
            now=now,
            channel=channel,
            caregiver=caregiver,
        )
        record_summary(summary, "MISSED_ALERT", res)


def process_tick(
    db: Session,
    clock: Callable[[], datetime],
    notifier: BaseNotifier,
) -> Dict[str, Any]:
    """
    Main escalation engine tick:
      1. Calls mark_overdue_as_missed.
      2. Acknowledges open logs for terminal doses (TAKEN, LATE, SKIPPED).
      3. Iterates over doses within the lookback window.
      4. Skips doses with inactive medications.
      5. Runs patient ladder and caregiver unconfirmed for pending doses.
      6. Runs caregiver missed alert for missed doses.
      7. Returns execution summary with by_step breakdown.
    """
    now = ensure_utc(clock())
    mark_overdue_as_missed(db=db, clock=clock)
    acknowledge_terminal_logs(db=db, now=now)

    doses = doses_in_lookback(db=db, now=now)
    summary = {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "by_step": {},
    }

    for dose in doses:
        try:
            # Skip inactive medication schedules
            if not getattr(dose.schedule.medication, "active", True):
                continue

            if dose.status == DoseStatus.PENDING:
                run_patient_ladder(
                    db=db,
                    dose=dose,
                    now=now,
                    notifier=notifier,
                    summary=summary,
                )
                run_caregiver_unconfirmed(
                    db=db,
                    dose=dose,
                    now=now,
                    notifier=notifier,
                    summary=summary,
                )
            elif dose.status == DoseStatus.MISSED:
                run_missed_alert(
                    db=db,
                    dose=dose,
                    now=now,
                    notifier=notifier,
                    summary=summary,
                )

        except Exception as dose_err:
            logger.error("Error processing dose %s in escalation tick: %s", dose.id, dose_err)
            summary["failed"] += 1

    return summary
