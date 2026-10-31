"""
tests/test_snooze_and_notifiers.py

Stage 4a tests:
- Snooze persistence in database (DoseEvent.snoozed_until in UTC)
- Snooze validation and API responses
- Notifier templates (fixed wording, due time in patient's timezone, no LLM)
- MockNotifier recording, console output, and failure injection
- EscalationLog schema: recipient, attempts, status, and unique constraint
"""
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.exc import IntegrityError

from fastapi import status
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, NotifyLevel, EscalationStatus
from app.notifiers.mock import MockNotifier
from app.notifiers.templates import (
    format_patient_reminder,
    format_caregiver_unconfirmed_alert,
    format_caregiver_missed_alert,
)
from app.config import settings

FROZEN_NOW = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)


def _setup_patient_and_dose(db_session, tz_name="Asia/Kolkata"):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone=tz_name,
        wake_time=time(6, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    m = Medication(
        patient_id=p.id,
        name="Metformin",
        strength="500mg",
        form="tablet",
        instructions="After food",
        active=True,
        stock_remaining=30.0,
        refill_threshold=5,
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    s = Schedule(
        medication_id=m.id,
        time_of_day=time(8, 0),
        days_of_week="daily",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)

    due_at = datetime(2026, 6, 1, 2, 30, 0, tzinfo=timezone.utc)  # 8:00 AM IST
    dose = DoseEvent(
        schedule_id=s.id,
        due_at=due_at,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()
    db_session.refresh(dose)

    return p, m, s, dose


class TestSnoozePersistence:
    """Verifies that snooze persists snoozed_until to the database and adheres to boundaries."""

    def test_snooze_persists_snoozed_until_in_utc(self, make_client, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)
        client = make_client(FROZEN_NOW)

        headers = {"X-API-Key": settings.PATIENT_API_KEY}
        r = client.post(
            f"/api/v1/doses/{dose.id}/snooze",
            json={"minutes": 15},
            headers=headers,
        )
        assert r.status_code == status.HTTP_200_OK
        data = r.json()
        assert data["dose_id"] == dose.id
        assert data["minutes"] == 15

        expected_until = FROZEN_NOW + timedelta(minutes=15)
        returned_until = datetime.fromisoformat(data["snooze_until"])
        assert returned_until == expected_until

        # Verify DB directly
        db_session.expire_all()
        db_dose = db_session.get(DoseEvent, dose.id)
        assert db_dose.snoozed_until is not None
        # Normalise for timezone-aware SQLite comparison
        stored_utc = db_dose.snoozed_until.replace(tzinfo=timezone.utc) if db_dose.snoozed_until.tzinfo is None else db_dose.snoozed_until
        assert stored_utc == expected_until

    def test_snooze_reflected_in_today_doses_api(self, make_client, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)
        # 8:00 AM IST on 2026-06-01 is 2:30 AM UTC
        clock_time = datetime(2026, 6, 1, 2, 30, 0, tzinfo=timezone.utc)
        client = make_client(clock_time)

        headers = {"X-API-Key": settings.PATIENT_API_KEY}
        # Snooze for 20 minutes
        r_snooze = client.post(
            f"/api/v1/doses/{dose.id}/snooze",
            json={"minutes": 20},
            headers=headers,
        )
        assert r_snooze.status_code == status.HTTP_200_OK

        # Query today doses
        r_today = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=headers)
        assert r_today.status_code == status.HTTP_200_OK
        items = r_today.json()
        matching = [item for item in items if item["id"] == dose.id]
        assert len(matching) == 1
        assert matching[0]["snoozed_until"] is not None
        snoozed_dt = datetime.fromisoformat(matching[0]["snoozed_until"])
        snoozed_utc = snoozed_dt if snoozed_dt.tzinfo is not None else snoozed_dt.replace(tzinfo=timezone.utc)
        assert snoozed_utc == clock_time + timedelta(minutes=20)


    def test_snooze_exceeding_max_returns_422(self, make_client, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)
        client = make_client(FROZEN_NOW)

        headers = {"X-API-Key": settings.PATIENT_API_KEY}
        r = client.post(
            f"/api/v1/doses/{dose.id}/snooze",
            json={"minutes": 61},  # Max permitted is 60
            headers=headers,
        )
        assert r.status_code == 422

    def test_snooze_nonexistent_dose_returns_404(self, make_client):
        client = make_client(FROZEN_NOW)
        headers = {"X-API-Key": settings.PATIENT_API_KEY}
        r = client.post(
            "/api/v1/doses/999999/snooze",
            json={"minutes": 10},
            headers=headers,
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND


class TestMessageTemplates:
    """Verifies deterministic, fixed text formatting in patient's timezone."""

    def test_patient_reminder_formatting(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, tz_name="Asia/Kolkata")
        # due_at is 2:30 AM UTC = 8:00 AM IST
        msg = format_patient_reminder(p, dose)
        assert "Medication Reminder: Metformin (500mg)" in msg
        assert "Scheduled for: 8:00 AM" in msg
        assert "Instructions: After food" in msg

    def test_caregiver_unconfirmed_alert_formatting(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, tz_name="Asia/Kolkata")
        msg = format_caregiver_unconfirmed_alert(p, dose)
        assert "Caregiver Alert: Lakshmi Devi has not confirmed their 8:00 AM dose of Metformin (500mg)." == msg

    def test_caregiver_missed_alert_formatting(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, tz_name="Asia/Kolkata")
        msg = format_caregiver_missed_alert(p, dose)
        assert "Caregiver Alert: Dose missed. Lakshmi Devi did not take their 8:00 AM dose of Metformin (500mg)." == msg


class TestMockNotifier:
    """Verifies in-memory call recording and failure simulation of MockNotifier."""

    def test_send_patient_reminder_success(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session)

        success = notifier.send_patient_reminder(p, dose)
        assert success is True
        assert len(notifier.sent_patient_reminders) == 1
        rec = notifier.sent_patient_reminders[0]
        assert rec["patient_id"] == p.id
        assert rec["dose_event_id"] == dose.id
        assert "Metformin (500mg)" in rec["message"]

    def test_send_caregiver_alert_success(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        c = Caregiver(
            name="Ravi Kumar",
            phone="+919876543210",
            whatsapp_id="919876543210",
            relationship="Son",
            notify_level=NotifyLevel.ALL,
        )
        db_session.add(c)
        db_session.commit()

        success = notifier.send_caregiver_alert(c, "Test alert message")
        assert success is True
        assert len(notifier.sent_caregiver_alerts) == 1
        rec = notifier.sent_caregiver_alerts[0]
        assert rec["caregiver_id"] == c.id
        assert rec["message"] == "Test alert message"

    def test_notifier_injected_failures(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session)

        # Flag failure
        notifier.fail_patient_reminders = True
        assert notifier.send_patient_reminder(p, dose) is False
        assert len(notifier.sent_patient_reminders) == 0

        # Counted failure
        notifier.fail_patient_reminders = False
        notifier.fail_count = 2
        assert notifier.send_patient_reminder(p, dose) is False
        assert notifier.send_patient_reminder(p, dose) is False
        # Third attempt succeeds
        assert notifier.send_patient_reminder(p, dose) is True
        assert len(notifier.sent_patient_reminders) == 1

        # Reset
        notifier.reset()
        assert len(notifier.sent_patient_reminders) == 0
        assert notifier.fail_count == 0


class TestEscalationLogSchema:
    """Verifies recipient, attempts, status, and uniqueness constraints on EscalationLog."""

    def test_escalation_log_defaults_and_persistence(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)
        log = EscalationLog(
            dose_event_id=dose.id,
            step="REMINDER_1",
            recipient=f"patient:{p.id}",
            channel="push",
            sent_at=FROZEN_NOW,
            status=EscalationStatus.SENT,
            attempts=1,
        )
        db_session.add(log)
        db_session.commit()
        db_session.refresh(log)

        assert log.id is not None
        assert log.recipient == f"patient:{p.id}"
        assert log.attempts == 1
        assert log.status == EscalationStatus.SENT

    def test_escalation_log_unique_constraint(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)
        log1 = EscalationLog(
            dose_event_id=dose.id,
            step="REMINDER_1",
            recipient=f"patient:{p.id}",
            channel="push",
            sent_at=FROZEN_NOW,
            status=EscalationStatus.SENT,
        )
        db_session.add(log1)
        db_session.commit()

        # Duplicate (dose_event_id, step, recipient) must be rejected
        log2 = EscalationLog(
            dose_event_id=dose.id,
            step="REMINDER_1",
            recipient=f"patient:{p.id}",
            channel="sms",
            sent_at=FROZEN_NOW + timedelta(minutes=5),
            status=EscalationStatus.SENT,
        )
        db_session.add(log2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        # Different recipient or different step is permitted
        log3 = EscalationLog(
            dose_event_id=dose.id,
            step="REMINDER_2",
            recipient=f"patient:{p.id}",
            channel="sms",
            sent_at=FROZEN_NOW + timedelta(minutes=10),
            status=EscalationStatus.SENT,
        )
        db_session.add(log3)
        db_session.commit()
        assert log3.id is not None
