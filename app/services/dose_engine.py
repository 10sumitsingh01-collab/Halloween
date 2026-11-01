"""
app/services/dose_engine.py

Deterministic dose engine for MedMate.
All business rules live here; no LLM calls, no datetime.now() calls.
Pass an injectable clock (Callable[[], datetime]) for "now" so tests can
freeze time without monkeypatching.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    DoseEvent,
    DoseStatus,
    DoseSource,
    Medication,
    Schedule,
)
from app.models.patient import PatientNote
from app.models.enums import NoteKind
from app.services.clock import ensure_utc, utc_now


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("Asia/Kolkata")


def parse_days_of_week(days_str: str) -> set[int]:
    """
    Parse the Schedule.days_of_week string into a set of Python weekday ints.
    Mon = 0 … Sun = 6  (matching date.weekday()).

    Supported formats:
        "daily"           → {0, 1, 2, 3, 4, 5, 6}
        "0,1,2,3,4,5,6"  → {0, 1, 2, 3, 4, 5, 6}
        "6"               → {6}  (Sunday only)
    """
    s = days_str.strip().lower()
    if s == "daily":
        return {0, 1, 2, 3, 4, 5, 6}
    try:
        return {int(d.strip()) for d in s.split(",") if d.strip()}
    except ValueError as exc:
        raise ValueError(f"Invalid days_of_week value: {days_str!r}") from exc


# ---------------------------------------------------------------------------
# Pure helper: reminder time
# ---------------------------------------------------------------------------

def reminder_time(dose_event: DoseEvent, offset_minutes: int = 0) -> datetime:
    """
    Return the datetime at which a reminder should be sent for this dose.

    = due_at + offset_minutes  (caller passes PatientPreference.reminder_offset_minutes).
    Never mutates due_at on the DoseEvent.
    """
    return ensure_utc(dose_event.due_at) + timedelta(minutes=offset_minutes)


# ---------------------------------------------------------------------------
# Generate DoseEvents
# ---------------------------------------------------------------------------

def generate_dose_events(
    db: Session,
    start_date: date,
    days: int,
    patient_id: Optional[int] = None,
    clock: Optional[Callable[[], datetime]] = None,
    skip_past: bool = True,
) -> Dict[str, Any]:
    """
    Idempotently generate DoseEvents for all active medication schedules.

    - Weekday is computed from the PATIENT'S local timezone (not UTC).
    - If skip_past is True and clock is provided, doses with due_at < now are skipped.
    - Uses a pre-check query + IntegrityError backstop (via SAVEPOINT) for
      idempotency; no SQLite-specific SQL.
    - Returns a summary dict with generated/skipped counts.
    """
    query = (
        db.query(Schedule)
        .join(Medication, Schedule.medication_id == Medication.id)
        .filter(Medication.active == True)
    )
    if patient_id is not None:
        query = query.filter(Medication.patient_id == patient_id)
    schedules = query.all()

    generated = 0
    skipped = 0
    now_utc = ensure_utc(clock()) if clock is not None else None

    for sched in schedules:
        patient = sched.medication.patient
        tz = _get_tz(patient.timezone)
        valid_weekdays = parse_days_of_week(sched.days_of_week)

        for day_offset in range(days):
            target_date = start_date + timedelta(days=day_offset)

            # Respect schedule date bounds
            if target_date < sched.start_date:
                continue
            if sched.end_date and target_date > sched.end_date:
                continue

            # Build the local datetime so weekday is computed in patient's TZ
            target_dt_local = datetime.combine(target_date, sched.time_of_day).replace(tzinfo=tz)
            if target_dt_local.weekday() not in valid_weekdays:
                continue

            due_at_utc = target_dt_local.astimezone(timezone.utc)

            # Safety rule 1a: never create a DoseEvent whose due_at is before now
            if skip_past and now_utc is not None and due_at_utc < now_utc:
                continue

            # Primary idempotency check (portable, no SQL-specific syntax)
            existing = (
                db.query(DoseEvent)
                .filter(
                    DoseEvent.schedule_id == sched.id,
                    DoseEvent.due_at == due_at_utc,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            # Insert with IntegrityError backstop via SAVEPOINT
            try:
                with db.begin_nested():
                    event = DoseEvent(
                        schedule_id=sched.id,
                        due_at=due_at_utc,
                        status=DoseStatus.PENDING,
                    )
                    db.add(event)
                    db.flush()
                generated += 1
            except IntegrityError:
                skipped += 1

    db.commit()

    return {
        "generated": generated,
        "skipped_duplicates": skipped,
        "patient_id": patient_id,
        "start_date": start_date.isoformat(),
        "days": days,
    }


# ---------------------------------------------------------------------------
# Confirm a dose taken / late / caregiver-override
# ---------------------------------------------------------------------------

def confirm_taken(
    db: Session,
    dose_id: int,
    source: Optional[DoseSource],
    is_caregiver: bool,
    clock: Callable[[], datetime] = utc_now,
) -> DoseEvent:
    """
    Confirm a dose event, applying all safety rules in order:

    1. Already terminal (taken / late / skipped) → 409
    2. Too early (> EARLY_WINDOW_MINUTES before due_at) → 409
    3. Double-dose guard (same schedule confirmed within DOUBLE_DOSE_GUARD_MINUTES) → 409;
       no stock change on a blocked attempt.
    4. Past LATE_WINDOW_MINUTES →
          patient key → 409 "dose window closed"
          caregiver key → records acknowledged-missed (status stays MISSED) + PatientNote
    5. Within TAKEN_WINDOW_MINUTES of due_at → status = taken
       Otherwise (within LATE window) → status = late
    6. Stock decrement (taken / late only) in the SAME transaction.
    """
    dose = db.query(DoseEvent).filter(DoseEvent.id == dose_id).first()
    if not dose:
        exc = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Not Found", "detail": f"DoseEvent {dose_id} not found"},
        )
        exc.code = "not_found"
        raise exc

    now = ensure_utc(clock())
    due_at = ensure_utc(dose.due_at)
    sched = dose.schedule
    med = sched.medication

    # Rule 1 — Already terminal
    if dose.status in (DoseStatus.TAKEN, DoseStatus.LATE, DoseStatus.SKIPPED):
        exc = HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "Conflict",
                "detail": (
                    f"Dose already marked as '{dose.status.value}' and cannot be confirmed again"
                ),
            },
        )
        exc.code = "already_recorded"
        raise exc

    # Rule 2 — Too early
    early_cutoff = due_at - timedelta(minutes=settings.EARLY_WINDOW_MINUTES)
    if now < early_cutoff:
        exc = HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "Conflict",
                "detail": (
                    f"Too early: dose cannot be confirmed more than "
                    f"{settings.EARLY_WINDOW_MINUTES} minutes before it is due"
                ),
            },
        )
        exc.code = "too_early"
        raise exc

    # Rule 3 — Double-dose guard
    if settings.DOUBLE_DOSE_GUARD_MINUTES > 0:
        window_start = now - timedelta(minutes=settings.DOUBLE_DOSE_GUARD_MINUTES)
        recent = (
            db.query(DoseEvent)
            .filter(
                DoseEvent.schedule_id == dose.schedule_id,
                DoseEvent.id != dose.id,
                DoseEvent.status.in_([DoseStatus.TAKEN, DoseStatus.LATE]),
                DoseEvent.responded_at >= window_start,
            )
            .first()
        )
        if recent:
            exc = HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "Conflict",
                    "detail": (
                        f"Double-dose guard: a dose for this schedule was already confirmed within "
                        f"the last {settings.DOUBLE_DOSE_GUARD_MINUTES} minutes. "
                        "No dose logged and stock unchanged."
                    ),
                },
            )
            exc.code = "double_dose"
            raise exc

    # Rule 4 — Past the late window
    late_cutoff = due_at + timedelta(minutes=settings.LATE_WINDOW_MINUTES)
    if now > late_cutoff:
        if not is_caregiver:
            exc = HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "Conflict",
                    "detail": "Dose window closed, please contact your caregiver",
                },
            )
            exc.code = "window_closed"
            raise exc
        # Caregiver acknowledges missed dose; status stays / becomes MISSED
        dose.status = DoseStatus.MISSED
        dose.responded_at = now
        dose.source = DoseSource.CAREGIVER

        note = PatientNote(
            patient_id=med.patient_id,
            text=(
                f"Late dose acknowledged by caregiver: {med.name} {med.strength} "
                f"(due {due_at.isoformat()}, confirmed {now.isoformat()})"
            ),
            kind=NoteKind.QUESTION,
            created_at=now,
            flagged_to_caregiver=True,
        )
        db.add(note)
        db.commit()
        db.refresh(dose)
        return dose

    # Rule 5 — TAKEN vs LATE
    taken_cutoff = due_at + timedelta(minutes=settings.TAKEN_WINDOW_MINUTES)
    new_status = DoseStatus.TAKEN if now <= taken_cutoff else DoseStatus.LATE

    # Rule 6 — Decrement stock in the SAME transaction
    new_stock = max(0.0, float(med.stock_remaining) - float(sched.dose_amount))
    med.stock_remaining = int(round(new_stock))

    dose.status = new_status
    dose.responded_at = now
    dose.source = source if source is not None else DoseSource.PHONE

    db.commit()
    db.refresh(dose)
    return dose


# ---------------------------------------------------------------------------
# Batch: mark overdue doses as missed
# ---------------------------------------------------------------------------

def mark_overdue_as_missed(
    db: Session,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """
    Set status = MISSED for every PENDING DoseEvent past the late window.
    Idempotent: already-missed doses are ignored.
    Returns the count of newly missed doses.
    """
    now = ensure_utc(clock())
    cutoff = now - timedelta(minutes=settings.LATE_WINDOW_MINUTES)

    overdue = (
        db.query(DoseEvent)
        .filter(
            DoseEvent.status == DoseStatus.PENDING,
            DoseEvent.due_at < cutoff,
        )
        .all()
    )

    for d in overdue:
        d.status = DoseStatus.MISSED

    db.commit()
    return len(overdue)


# ---------------------------------------------------------------------------
# Refill alerts
# ---------------------------------------------------------------------------

def medications_needing_refill(
    db: Session,
    patient_id: int,
) -> List[Medication]:
    """
    Return active medications where stock_remaining <= refill_threshold.
    """
    return (
        db.query(Medication)
        .filter(
            Medication.patient_id == patient_id,
            Medication.active == True,
            Medication.stock_remaining <= Medication.refill_threshold,
        )
        .all()
    )
