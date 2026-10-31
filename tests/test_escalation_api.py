"""
tests/test_escalation_api.py

Integration tests for Layer 4 Part 3 endpoints:
- POST /api/v1/admin/run-tick (caregiver key only, dependency-injected clock & notifier)
- GET /api/v1/doses/{id}/escalations (patient or caregiver key, 404 for unknown dose)
- Verification of by_step breakdown and privacy (no phone numbers or raw exception text)
"""
from datetime import datetime, date, time, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base
from app.models.patient import Patient, PatientCaregiver
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, EscalationStatus, NotifyLevel
from app.notifiers import BaseNotifier, MockNotifier, get_notifier
from app.services.clock import get_clock
from app.config import settings

FROZEN_NOW = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
PATIENT_AUTH = {"X-API-Key": settings.PATIENT_API_KEY}
CAREGIVER_AUTH = {"X-API-Key": settings.CAREGIVER_API_KEY}


@pytest.fixture(autouse=True)
def reset_mock_notifier():
    """Ensure the shared MockNotifier is clean before and after each test."""
    notifier = get_notifier()
    assert hasattr(notifier, "reset") and callable(notifier.reset)
    notifier.reset()
    yield notifier
    notifier.reset()
    app.dependency_overrides.clear()


def _setup_patient_and_dose(
    db,
    due_at=FROZEN_NOW,
    whatsapp_id="+919876543210",
    status=DoseStatus.PENDING,
):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=time(6, 0),
        sleep_time=time(22, 0),
        whatsapp_id=whatsapp_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)

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
    db.add(m)
    db.commit()
    db.refresh(m)

    s = Schedule(
        medication_id=m.id,
        time_of_day=time(8, 0),
        days_of_week="daily",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    dose = DoseEvent(
        schedule_id=s.id,
        due_at=due_at,
        status=status,
    )
    db.add(dose)
    db.commit()
    db.refresh(dose)
    return p, m, s, dose


def _setup_caregiver(
    db,
    patient,
    name="Priya",
    phone="+919876543211",
    whatsapp_id="+919876543211",
    notify_level=NotifyLevel.ALL,
):
    c = Caregiver(
        name=name,
        phone=phone,
        whatsapp_id=whatsapp_id if whatsapp_id is not None else "",
        relationship="Daughter",
        notify_level=notify_level,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    patient.caregivers.append(c)
    db.commit()
    return c


class TestRunTickEndpoint:
    """Tests POST /api/v1/admin/run-tick."""

    def test_run_tick_auth_rejection_and_schema(self, client: TestClient, db_session):
        # 1. No key -> 401
        res_no_key = client.post("/api/v1/admin/run-tick")
        assert res_no_key.status_code == 401

        # 2. Patient key -> 403
        res_pat_key = client.post("/api/v1/admin/run-tick", headers=PATIENT_AUTH)
        assert res_pat_key.status_code == 403

        # 3. Caregiver key -> 200 with summary schema
        res_care_key = client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)
        assert res_care_key.status_code == 200
        data = res_care_key.json()
        assert "sent" in data
        assert "failed" in data
        assert "skipped" in data
        assert "by_step" in data
        assert isinstance(data["by_step"], dict)

    def test_run_tick_execution_and_idempotency(self, client: TestClient, db_session):
        """
        Frozen clock with dose due now sends one reminder and response shows
        by_step REMINDER_1 sent 1; calling it again sends nothing new.
        """
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Freeze clock at dose due_at
        app.dependency_overrides[get_clock] = lambda: (lambda: FROZEN_NOW)

        # First tick
        res1 = client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["sent"] == 1
        assert "REMINDER_1" in data1["by_step"]
        assert data1["by_step"]["REMINDER_1"]["sent"] == 1

        db_session.expire_all()
        row_count_1 = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id)
            .count()
        )
        assert row_count_1 == 1

        # Second tick with the same clock
        res2 = client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["sent"] == 0

        db_session.expire_all()
        row_count_2 = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id)
            .count()
        )
        # Assert unchanged row count
        assert row_count_2 == row_count_1


class TestDoseEscalationsEndpoint:
    """Tests GET /api/v1/doses/{id}/escalations."""

    def test_get_escalations_order_auth_and_privacy(self, client: TestClient, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        # Run tick at 08:00 (REMINDER_1)
        app.dependency_overrides[get_clock] = lambda: (lambda: FROZEN_NOW)
        client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)

        # Run tick at 08:20 (CAREGIVER_UNCONFIRMED)
        app.dependency_overrides[get_clock] = lambda: (lambda: FROZEN_NOW + timedelta(minutes=20))
        client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)

        # 1. Accessible with patient key
        res_patient = client.get(f"/api/v1/doses/{dose.id}/escalations", headers=PATIENT_AUTH)
        assert res_patient.status_code == 200
        logs_p = res_patient.json()
        assert len(logs_p) == 3

        # 2. Accessible with caregiver key
        res_caregiver = client.get(f"/api/v1/doses/{dose.id}/escalations", headers=CAREGIVER_AUTH)
        assert res_caregiver.status_code == 200
        logs_c = res_caregiver.json()
        assert logs_c == logs_p

        # 3. Ordered by id ascending
        steps = [l["step"] for l in logs_p]
        assert steps == ["REMINDER_1", "REMINDER_2", "CAREGIVER_UNCONFIRMED"]

        assert logs_p[0]["recipient"] == "patient"
        assert logs_p[0]["channel"] == "mock"
        assert logs_p[0]["status"] == "sent"
        assert logs_p[0]["sent_at"] is not None

        assert logs_p[1]["recipient"] == "patient"
        assert logs_p[1]["channel"] == "mock"
        assert logs_p[1]["status"] == "sent"

        assert logs_p[2]["recipient"] == f"caregiver:{c.id}"
        assert logs_p[2]["channel"] == "mock"
        assert logs_p[2]["status"] == "sent"

        # 4. Privacy: assert no phone numbers or whatsapp IDs in response body
        raw_text = res_patient.text
        assert p.whatsapp_id not in raw_text
        assert c.phone not in raw_text
        assert c.whatsapp_id not in raw_text
        assert "+91" not in raw_text

        # 5. Non-existent dose -> 404
        res_404 = client.get("/api/v1/doses/999999/escalations", headers=PATIENT_AUTH)
        assert res_404.status_code == 404

    def test_failed_no_whatsapp_id_row_returned_without_error(self, client: TestClient, db_session):
        """Failed row with 'no whatsapp_id' returns 200 with sent_at as None."""
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW, whatsapp_id=None)

        app.dependency_overrides[get_clock] = lambda: (lambda: FROZEN_NOW)
        client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)

        res = client.get(f"/api/v1/doses/{dose.id}/escalations", headers=PATIENT_AUTH)
        assert res.status_code == 200
        logs = res.json()
        assert len(logs) == 1
        log = logs[0]
        assert log["step"] == "REMINDER_1"
        assert log["recipient"] == "patient"
        assert log["status"] == "failed"
        assert log["sent_at"] is None
        assert log["detail"] == "patient has no whatsapp_id"
        assert log["attempts"] == 1

    def test_exception_detail_contains_only_class_name_never_phone_number(
        self, client: TestClient, db_session
    ):
        """
        When notifier raises an exception containing a phone number, detail must
        contain ONLY the exception class name, never the raw exception text or phone number.
        """
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Custom notifier that echoes sensitive phone number in exception message
        class LeakyNotifier(BaseNotifier):
            channel = "sms_gateway"

            def send_patient_reminder(self, patient, dose_event):
                raise RuntimeError(f"Gateway failure when dialing {patient.whatsapp_id}: connection refused")

            def send_caregiver_alert(self, caregiver, message):
                return True

        leaky = LeakyNotifier()
        app.dependency_overrides[get_notifier] = lambda: leaky
        app.dependency_overrides[get_clock] = lambda: (lambda: FROZEN_NOW)

        # Run tick (notifier raises RuntimeError)
        client.post("/api/v1/admin/run-tick", headers=CAREGIVER_AUTH)

        # Inspect escalations endpoint
        res = client.get(f"/api/v1/doses/{dose.id}/escalations", headers=PATIENT_AUTH)
        assert res.status_code == 200
        logs = res.json()
        assert len(logs) == 1
        log = logs[0]
        assert log["detail"] == "RuntimeError"
        assert log["channel"] == "sms_gateway"
        assert p.whatsapp_id not in log["detail"]
        assert p.whatsapp_id not in res.text
