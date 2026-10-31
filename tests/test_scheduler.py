"""
tests/test_scheduler.py

Tests for MedMate Layer 4 Stage 4c:
- BackgroundScheduler startup, shutdown, and disabled-by-default behavior.
- Non-blocking execution with fresh database session per job invocation.
- Safe privacy logging: exceptions logged with class name only, no sensitive info.
- SessionLocal patched in all scheduler tests to avoid touching medmate.db.
- Safety Rule 1a: generate_dose_events skip_past parameter skips doses due before now.
- Safety Rule 1b: MISSED_ALERT max age (60 minutes past late window cutoff).
"""
import logging
from datetime import datetime, date, time, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models.caregiver import Caregiver
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, NotifyLevel
from app.models.medication import Medication, Schedule
from app.models.patient import Patient, PatientCaregiver
from app.notifiers.mock import MockNotifier
from app.scheduler import (
    run_generation_job,
    run_tick_job,
    start_scheduler,
    stop_scheduler,
)
from app.services.clock import ensure_utc
from app.services.dose_engine import generate_dose_events
from app.services.escalation import process_tick


# ---------------------------------------------------------------------------
# In-memory database setup fixture for scheduler tests (avoids medmate.db)
# ---------------------------------------------------------------------------

@pytest.fixture
def sched_db_factory():
    """Provides isolated in-memory SQLAlchemy sessions for scheduler worker tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return TestingSession


# ---------------------------------------------------------------------------
# Test 1 & 2: Scheduler startup, shutdown, and disabled-by-default
# ---------------------------------------------------------------------------

def test_scheduler_disabled_by_default():
    """When SCHEDULER_ENABLED is False, start_scheduler returns None and doesn't run."""
    with patch.object(settings, "SCHEDULER_ENABLED", False):
        sched = start_scheduler()
        assert sched is None
        stop_scheduler()


def test_scheduler_starts_and_stops_cleanly_when_enabled():
    """When SCHEDULER_ENABLED is True, start_scheduler starts BackgroundScheduler."""
    with patch.object(settings, "SCHEDULER_ENABLED", True), \
         patch("app.scheduler.run_generation_job") as mock_gen_job:
        sched = start_scheduler()
        try:
            assert sched is not None
            assert sched.running is True
            # Verify jobs were added
            job_ids = [job.id for job in sched.get_jobs()]
            assert "escalation_tick" in job_ids
            assert "dose_generation" in job_ids
            mock_gen_job.assert_called_once()
        finally:
            stop_scheduler()
            assert not sched.running


# ---------------------------------------------------------------------------
# Test 3: Safe privacy error logging (class name only)
# ---------------------------------------------------------------------------

def test_job_exception_logged_as_class_name_without_stopping(caplog):
    """Job failures log ONLY the exception class name, never raw strings or phone numbers."""
    fake_session = MagicMock()
    # Simulate an exception carrying sensitive patient information (e.g. phone number)
    fake_session.query.side_effect = RuntimeError("Failed connecting to WhatsApp for +919876543210")

    with patch("app.scheduler.SessionLocal", return_value=fake_session):
        with caplog.at_level(logging.ERROR):
            run_tick_job()
            run_generation_job()

    # Exception class name MUST be present
    assert "RuntimeError" in caplog.text
    # Sensitive details (phone number, message) MUST NOT be present
    assert "+919876543210" not in caplog.text
    assert "Failed connecting to WhatsApp" not in caplog.text
    # Fake session must be closed cleanly in both jobs
    assert fake_session.close.call_count == 2


# ---------------------------------------------------------------------------
# Test 4: Each tick uses a fresh session and closes it in finally
# ---------------------------------------------------------------------------

def test_each_tick_uses_fresh_session():
    """Each scheduler tick opens a fresh session and closes it in finally."""
    session1 = MagicMock()
    session2 = MagicMock()
    sessions = [session1, session2]

    def session_factory():
        return sessions.pop(0)

    with patch("app.scheduler.SessionLocal", side_effect=session_factory), \
         patch("app.scheduler.process_tick") as mock_tick:
        run_tick_job()
        run_tick_job()

        assert mock_tick.call_count == 2
        session1.close.assert_called_once()
        session2.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Safety Rule 1a: generate_dose_events skip_past parameter
# ---------------------------------------------------------------------------

def test_generate_dose_events_rule_1a_skips_past(sched_db_factory):
    """
    Safety Rule 1a: generate_dose_events must never create a DoseEvent whose
    due_at is before now when skip_past=True (default).
    """
    db = sched_db_factory()
    try:
        # Create patient with daily schedule at 08:00 AM local (UTC+05:30 -> 02:30 AM UTC)
        patient = Patient(
            name="Lakshmi Devi",
            age=72,
            language="kn",
            timezone="Asia/Kolkata",
            wake_time=time(7, 0),
            sleep_time=time(22, 0),
            whatsapp_id="+910000000000",
        )
        db.add(patient)
        db.flush()

        med = Medication(
            patient_id=patient.id,
            name="Metformin",
            strength="500mg",
            form="tablet",
            stock_remaining=30,
            refill_threshold=5,
            active=True,
        )
        db.add(med)
        db.flush()

        sched = Schedule(
            medication_id=med.id,
            time_of_day=time(8, 0),
            days_of_week="daily",
            start_date=date(2026, 6, 1),
            dose_amount=1.0,
        )
        db.add(sched)
        db.commit()

        # Let now be June 2, 2026 at 12:00:00 UTC
        # June 1 dose was due at 2026-06-01 02:30:00 UTC (past)
        # June 2 dose was due at 2026-06-02 02:30:00 UTC (past)
        # June 3 dose is due at 2026-06-03 02:30:00 UTC (future)
        # June 4 dose is due at 2026-06-04 02:30:00 UTC (future)
        clock_now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

        # 1. With skip_past=True (default), past doses for June 1 & June 2 are skipped
        res = generate_dose_events(
            db=db,
            start_date=date(2026, 6, 1),
            days=4,
            clock=lambda: clock_now,
            skip_past=True,
        )
        assert res["generated"] == 2  # Only June 3 and June 4 generated
        assert res["skipped_duplicates"] == 0

        created_doses = db.query(DoseEvent).order_by(DoseEvent.due_at.asc()).all()
        assert len(created_doses) == 2
        for d in created_doses:
            assert ensure_utc(d.due_at) >= clock_now

        # Clean up created doses to test skip_past=False
        db.query(DoseEvent).delete()
        db.commit()

        # 2. With skip_past=False, all 4 doses are generated
        res_no_skip = generate_dose_events(
            db=db,
            start_date=date(2026, 6, 1),
            days=4,
            clock=lambda: clock_now,
            skip_past=False,
        )
        assert res_no_skip["generated"] == 4
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 6: Safety Rule 1b: MISSED_ALERT max age (60 minutes past late window)
# ---------------------------------------------------------------------------

def test_missed_alert_rule_1b_max_age(sched_db_factory):
    """
    Safety Rule 1b: MISSED_ALERT is sent only if:
      now <= due_at + LATE_WINDOW_MINUTES + MISSED_ALERT_MAX_AGE_MINUTES
    Older missed doses get no alert:
      1. A dose that became missed 30 minutes ago alerts.
      2. A dose that became missed 2 hours (120 minutes) ago does not alert.
      3. A seeded dose due earlier today triggers no message at startup.
    """
    db = sched_db_factory()
    try:
        patient = Patient(
            name="Lakshmi Devi",
            age=72,
            language="kn",
            timezone="Asia/Kolkata",
            wake_time=time(7, 0),
            sleep_time=time(22, 0),
            whatsapp_id="+910000000000",
        )
        db.add(patient)
        db.flush()

        caregiver = Caregiver(
            name="Ramesh Kumar",
            phone="+919876543210",
            whatsapp_id="whatsapp:+919876543210",
            relationship="Son",
            notify_level=NotifyLevel.ALL,
        )
        db.add(caregiver)
        db.flush()

        db.add(PatientCaregiver(patient_id=patient.id, caregiver_id=caregiver.id))

        med = Medication(
            patient_id=patient.id,
            name="Metformin",
            strength="500mg",
            form="tablet",
            stock_remaining=20,
            refill_threshold=5,
            active=True,
        )
        db.add(med)
        db.flush()

        sched = Schedule(
            medication_id=med.id,
            time_of_day=time(8, 0),
            days_of_week="daily",
            start_date=date(2026, 6, 1),
            dose_amount=1.0,
        )
        db.add(sched)
        db.commit()

        notifier = MockNotifier()

        # Late window = 120m, Missed alert max age = 60m. Total window = 180m from due_at.
        # Dose became missed at due_at + 120m.

        # --- Subtest 1: Dose that became missed 30 minutes ago ALERTS ---
        # due_at = 08:00. Missed at 10:00. Now = 10:30 (30 min after becoming missed, 150m from due).
        # 150m <= 180m -> alert is sent.
        due_at_1 = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
        dose1 = DoseEvent(
            schedule_id=sched.id,
            due_at=due_at_1,
            status=DoseStatus.PENDING,
        )
        db.add(dose1)
        db.commit()

        clock_30m_missed = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
        summary1 = process_tick(db=db, clock=lambda: clock_30m_missed, notifier=notifier)

        db.refresh(dose1)
        assert dose1.status == DoseStatus.MISSED
        assert summary1["by_step"].get("MISSED_ALERT", {}).get("sent", 0) == 1
        assert len(notifier.sent_caregiver_alerts) == 1

        notifier.reset()

        # --- Subtest 2: Dose that became missed 2 hours (120 min) ago DOES NOT ALERT ---
        # due_at = 04:00. Missed at 06:00. Now = 08:00 (120m after becoming missed, 240m from due).
        # 240m > 180m -> alert is skipped (no message).
        due_at_2 = datetime(2026, 6, 1, 4, 0, 0, tzinfo=timezone.utc)
        dose2 = DoseEvent(
            schedule_id=sched.id,
            due_at=due_at_2,
            status=DoseStatus.PENDING,
        )
        db.add(dose2)
        db.commit()

        clock_2h_missed = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
        summary2 = process_tick(db=db, clock=lambda: clock_2h_missed, notifier=notifier)

        db.refresh(dose2)
        assert dose2.status == DoseStatus.MISSED
        # Missed alert must NOT be sent
        assert summary2["by_step"].get("MISSED_ALERT", {}).get("sent", 0) == 0
        assert len(notifier.sent_caregiver_alerts) == 0

        notifier.reset()

        # --- Subtest 3: Seeded dose due earlier today triggers no message at startup ---
        # A seed dose was created for morning 08:00 AM local (02:30 AM UTC).
        # Scheduler starts at 10:00 AM UTC (7.5 hours later).
        # 7.5 hours > 3.0 hours max age cutoff -> triggers zero alerts.
        due_at_seed = datetime(2026, 6, 1, 2, 30, 0, tzinfo=timezone.utc)
        dose_seed = DoseEvent(
            schedule_id=sched.id,
            due_at=due_at_seed,
            status=DoseStatus.PENDING,
        )
        db.add(dose_seed)
        db.commit()

        clock_startup = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        summary3 = process_tick(db=db, clock=lambda: clock_startup, notifier=notifier)

        db.refresh(dose_seed)
        assert dose_seed.status == DoseStatus.MISSED
        assert summary3["by_step"].get("MISSED_ALERT", {}).get("sent", 0) == 0
        assert len(notifier.sent_caregiver_alerts) == 0
        assert len(notifier.sent_patient_reminders) == 0

    finally:
        db.close()
