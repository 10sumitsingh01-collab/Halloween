from datetime import datetime
from typing import Callable, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth import require_any_key, is_caregiver_key
from app.models.dose import DoseEvent, EscalationLog
from app.schemas.dose import (
    DoseTodayResponse,
    DoseTakenRequest,
    DoseSnoozeRequest,
    DoseSnoozeResponse,
    DoseResponse,
    EscalationLogResponse,
)
from app.services import dose_service
from app.services.clock import get_clock

router = APIRouter(tags=["doses"])


@router.get(
    "/patients/{id}/doses/today",
    response_model=List[DoseTodayResponse],
    summary="Get today's dose events in patient timezone",
)
def get_today_doses(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return dose_service.get_today_doses(db, id, clock)


@router.post(
    "/doses/{id}/taken",
    response_model=DoseResponse,
    summary="Mark dose event as taken (applies timing rules and stock decrement)",
)
def mark_dose_taken(
    id: int,
    request: DoseTakenRequest = DoseTakenRequest(),
    db: Session = Depends(get_db),
    api_key: str = Depends(require_any_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return dose_service.confirm_dose_taken(
        db=db,
        dose_id=id,
        source=request.source,
        is_caregiver=is_caregiver_key(api_key),
        clock=clock,
    )


@router.post(
    "/doses/{id}/skip",
    response_model=DoseResponse,
    summary="Mark dose event as skipped",
)
def mark_dose_skipped(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return dose_service.mark_dose_skipped(db, id, clock)


@router.post(
    "/doses/{id}/snooze",
    response_model=DoseSnoozeResponse,
    summary="Snooze a dose reminder",
)
def snooze_dose(
    id: int,
    request: DoseSnoozeRequest = DoseSnoozeRequest(),
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    return dose_service.snooze_dose(db, id, request.minutes, clock)


@router.get(
    "/doses/{id}/escalations",
    response_model=List[EscalationLogResponse],
    summary="Get escalation logs for a dose (ordered by id ascending)",
)
def get_dose_escalations(
    id: int,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_any_key),
):
    dose = db.get(DoseEvent, id)
    if not dose:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dose not found",
        )

    logs = (
        db.query(EscalationLog)
        .filter(EscalationLog.dose_event_id == id)
        .order_by(EscalationLog.id.asc())
        .all()
    )
    return logs
