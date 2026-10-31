"""
app/routers/admin.py

Admin-only endpoints (caregiver API key required).
Thin routing layer — logic lives in app/services.
"""
from datetime import date, datetime
from typing import Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import require_caregiver_key
from app.services.clock import get_clock
from app.services import dose_engine, escalation
from app.notifiers import BaseNotifier, get_notifier
from app.schemas.dose import GenerateDosesResponse, MarkMissedResponse, RunTickResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/generate-doses",
    response_model=GenerateDosesResponse,
    summary="Generate DoseEvents for active schedules (Caregiver only)",
)
def generate_doses(
    start_date: date = Query(
        default=None,
        description="Start date (ISO 8601). Defaults to today in UTC.",
    ),
    days: int = Query(default=1, ge=1, le=365, description="Number of days to generate"),
    patient_id: int | None = Query(default=None, description="Limit to one patient; omit for all"),
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    if start_date is None:
        start_date = clock().date()

    result = dose_engine.generate_dose_events(
        db=db,
        start_date=start_date,
        days=days,
        patient_id=patient_id,
        clock=clock,
    )
    return result


@router.post(
    "/mark-missed",
    response_model=MarkMissedResponse,
    summary="Mark all overdue pending doses as missed (Caregiver only)",
)
def mark_missed(
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    count = dose_engine.mark_overdue_as_missed(db=db, clock=clock)
    return {"marked_missed": count}


@router.post(
    "/run-tick",
    response_model=RunTickResponse,
    summary="Run deterministic reminder and escalation tick (Caregiver only)",
)
def run_tick(
    db: Session = Depends(get_db),
    _auth: str = Depends(require_caregiver_key),
    clock: Callable[[], datetime] = Depends(get_clock),
    notifier: BaseNotifier = Depends(get_notifier),
):
    summary = escalation.process_tick(db=db, clock=clock, notifier=notifier)
    return summary
