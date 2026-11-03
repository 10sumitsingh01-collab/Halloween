"""
tests/test_assistant_api.py

Stage 7a tests:
- Startup check refusing to start if ASSISTANT_API_KEY equals caregiver or patient key
- Route-walk test asserting assistant key rejection on all routes except the 6 allowed ones
- Assistant key accepted on each allowed endpoint for the configured patient
- Assistant key rejected for different patient
- Assistant key rejected on disallowed endpoints
- Assistant key rejected when either setting is unset
- A dose of another patient cannot be confirmed or snoozed with assistant key
- Alert sends to the right caregivers by notify_level (all, urgent_only get all; missed_only get none)
- Note text never appears in any sent message
- Note is stored even when cooldown returns 429
- Cooldown returns 429 and sends nothing inside window, then works again after window
- Cooldown only counts status=sent (failed or no_recipients never blocks retry)
- Honest patient messages and AlertLog status (sent, failed, no_recipients)
- Notifier exception and PermanentNotifierError handling
- AlertLog row created without sensitive data (no chat IDs or note text)
- Fixed patient messages per category
- Template includes report time in patient's timezone
- Alembic migration chain shows eight revisions with 0008 as head
"""
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
import pytest
from pydantic import SecretStr
from fastapi import status
from fastapi.testclient import TestClient

from app.main import app as fastapi_app, lifespan
from app.config import settings
from app.models.patient import Patient, PatientCaregiver, PatientNote
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent
from app.models.enums import DoseStatus, DoseSource, NoteKind, NotifyLevel
from app.models.alert import AlertLog
from app.notifiers import get_notifier, BaseNotifier
from app.notifiers.base import PermanentNotifierError
from app.schemas.patient import AlertCategory


FROZEN_NOW = datetime(2026, 6, 1, 8, 30, 0, tzinfo=timezone.utc)
TEST_ASSISTANT_KEY = "test_assistant_api_key_xyz987"


@pytest.fixture(autouse=True)
def setup_assistant_config():
    """Default assistant config for tests."""
    orig_key = settings.ASSISTANT_API_KEY
    orig_pid = settings.ASSISTANT_PATIENT_ID
    orig_cooldown = settings.ALERT_COOLDOWN_MINUTES

    settings.ASSISTANT_API_KEY = SecretStr(TEST_ASSISTANT_KEY)
    settings.ASSISTANT_PATIENT_ID = 1
    settings.ALERT_COOLDOWN_MINUTES = 10.0

    notifier = get_notifier()
    if hasattr(notifier, "reset"):
        notifier.reset()

    yield

    settings.ASSISTANT_API_KEY = orig_key
    settings.ASSISTANT_PATIENT_ID = orig_pid
    settings.ALERT_COOLDOWN_MINUTES = orig_cooldown
    if hasattr(notifier, "reset"):
        notifier.reset()
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def assistant_headers():
    return {"X-API-Key": TEST_ASSISTANT_KEY}


def _seed_patient_with_caregiver(db_session, pid=1, name="Ramachandra", tz="Asia/Kolkata"):
    p = Patient(
        id=pid,
        name=name,
        age=75,
        language="kn",
        timezone=tz,
        wake_time=time(6, 0),
        sleep_time=time(22, 0),
        whatsapp_id="+919876543210",
        telegram_chat_id="12345678",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    m = Medication(
        patient_id=p.id,
        name="Amlodipine",
        strength="5mg",
        form="tablet",
        stock_remaining=30,
        refill_threshold=5,
        active=True,
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    s = Schedule(
        medication_id=m.id,
        time_of_day=time(8, 30),
        days_of_week="daily",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)

    d = DoseEvent(
        schedule_id=s.id,
        due_at=FROZEN_NOW,
        status=DoseStatus.PENDING,
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)

    return p, m, s, d


@pytest.mark.anyio
async def test_startup_check_refuses_matching_keys():
    """Startup check refuses to start if ASSISTANT_API_KEY equals caregiver or patient key."""
    # Test match with caregiver key
    settings.ASSISTANT_API_KEY = SecretStr(settings.CAREGIVER_API_KEY)
    with pytest.raises(RuntimeError) as exc_info:
        async with lifespan(fastapi_app):
            pass
    assert "ASSISTANT_API_KEY must not match CAREGIVER_API_KEY or PATIENT_API_KEY" in str(exc_info.value)
    # Ensure key is not printed
    assert settings.CAREGIVER_API_KEY not in str(exc_info.value)

    # Test match with patient key
    settings.ASSISTANT_API_KEY = SecretStr(settings.PATIENT_API_KEY)
    with pytest.raises(RuntimeError) as exc_info:
        async with lifespan(fastapi_app):
            pass
    assert "ASSISTANT_API_KEY must not match CAREGIVER_API_KEY or PATIENT_API_KEY" in str(exc_info.value)
    assert settings.PATIENT_API_KEY not in str(exc_info.value)


def test_route_walk_rejects_assistant_key_on_disallowed_routes(make_client, db_session, assistant_headers):
    """
    Iterate over every route in the FastAPI app.
    Assert that the assistant key is rejected (401 or 403) on every route except the six allowed ones.
    Fail if an unclassified route appears.
    """
    _seed_patient_with_caregiver(db_session, pid=1)
    client = make_client(FROZEN_NOW)

    ALLOWED_ROUTES = {
        ("GET", "/api/v1/patients/{id}/doses/today"),
        ("POST", "/api/v1/doses/{id}/taken"),
        ("POST", "/api/v1/doses/{id}/snooze"),
        ("POST", "/api/v1/patients/{id}/notes"),
        ("GET", "/api/v1/patients/{id}/adherence"),
        ("POST", "/api/v1/patients/{id}/assistant/alerts"),
    }

    PUBLIC_ROUTES = {
        ("GET", "/health"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
    }

    DISALLOWED_ROUTES = {
        ("POST", "/api/v1/patients"),
        ("GET", "/api/v1/patients/{id}"),
        ("POST", "/api/v1/patients/{id}/caregivers/{cid}"),
        ("GET", "/api/v1/patients/{id}/refills"),
        ("POST", "/api/v1/caregivers"),
        ("POST", "/api/v1/patients/{id}/medications"),
        ("GET", "/api/v1/medications/{id}"),
        ("PATCH", "/api/v1/medications/{id}"),
        ("POST", "/api/v1/medications/{id}/schedules"),
        ("POST", "/api/v1/doses/{id}/skip"),
        ("GET", "/api/v1/doses/{id}/escalations"),
        ("POST", "/api/v1/admin/generate-doses"),
        ("POST", "/api/v1/admin/mark-missed"),
        ("POST", "/api/v1/admin/run-tick"),
    }

    for route in fastapi_app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue

        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue

            endpoint_tuple = (method, path)
            if endpoint_tuple in ALLOWED_ROUTES:
                continue

            if endpoint_tuple in PUBLIC_ROUTES:
                continue

            assert endpoint_tuple in DISALLOWED_ROUTES, (
                f"Unclassified route found in FastAPI app: {endpoint_tuple}. "
                f"Every route must be classified as allowed, public, or disallowed."
            )

            # Build a mock target path by replacing placeholders
            concrete_path = (
                path.replace("{id}", "1")
                .replace("{cid}", "1")
            )

            res = client.request(method, concrete_path, headers=assistant_headers, json={})
            assert res.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN), (
                f"Assistant key was NOT rejected on disallowed route {endpoint_tuple}! Got {res.status_code}"
            )


def test_assistant_key_accepted_on_allowed_endpoints_for_configured_patient(make_client, db_session, assistant_headers):
    """Assistant key is accepted on all six allowed endpoints for the configured patient."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    client = make_client(FROZEN_NOW)

    # 1. GET /patients/{id}/doses/today
    r1 = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=assistant_headers)
    assert r1.status_code == status.HTTP_200_OK

    # 2. POST /doses/{id}/taken
    r2 = client.post(f"/api/v1/doses/{d.id}/taken", headers=assistant_headers, json={})
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["source"] == "phone"
    assert r2.json()["status"] == "taken"

    # Create another dose for snooze test
    d2 = DoseEvent(
        schedule_id=s.id,
        due_at=FROZEN_NOW + timedelta(hours=4),
        status=DoseStatus.PENDING,
    )
    db_session.add(d2)
    db_session.commit()
    db_session.refresh(d2)

    # 3. POST /doses/{id}/snooze
    r3 = client.post(f"/api/v1/doses/{d2.id}/snooze", headers=assistant_headers, json={"minutes": 15})
    assert r3.status_code == status.HTTP_200_OK
    assert r3.json()["minutes"] == 15

    # 4. POST /patients/{id}/notes
    r4 = client.post(
        f"/api/v1/patients/{p.id}/notes",
        headers=assistant_headers,
        json={"text": "Felt fine in morning", "kind": "mood"},
    )
    assert r4.status_code == status.HTTP_201_CREATED

    # 5. GET /patients/{id}/adherence
    r5 = client.get(f"/api/v1/patients/{p.id}/adherence?days=7", headers=assistant_headers)
    assert r5.status_code == status.HTTP_200_OK

    # 6. POST /patients/{id}/assistant/alerts
    r6 = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom", "note": "Mild dizziness"},
    )
    assert r6.status_code == status.HTTP_200_OK


def test_assistant_key_rejected_for_different_patient(make_client, db_session, assistant_headers):
    """Assistant key is rejected (403) on each allowed endpoint when invoked for a different patient."""
    p1, m1, s1, d1 = _seed_patient_with_caregiver(db_session, pid=1, name="Patient One")
    p2, m2, s2, d2 = _seed_patient_with_caregiver(db_session, pid=2, name="Patient Two")
    client = make_client(FROZEN_NOW)

    # 1. GET /patients/{id}/doses/today for p2
    assert client.get(f"/api/v1/patients/{p2.id}/doses/today", headers=assistant_headers).status_code == status.HTTP_403_FORBIDDEN

    # 2. POST /doses/{id}/taken for d2 (belongs to p2)
    assert client.post(f"/api/v1/doses/{d2.id}/taken", headers=assistant_headers).status_code == status.HTTP_403_FORBIDDEN

    # 3. POST /doses/{id}/snooze for d2 (belongs to p2)
    assert client.post(f"/api/v1/doses/{d2.id}/snooze", headers=assistant_headers).status_code == status.HTTP_403_FORBIDDEN

    # 4. POST /patients/{id}/notes for p2
    assert client.post(
        f"/api/v1/patients/{p2.id}/notes",
        headers=assistant_headers,
        json={"text": "Hello", "kind": "mood"},
    ).status_code == status.HTTP_403_FORBIDDEN

    # 5. GET /patients/{id}/adherence for p2
    assert client.get(f"/api/v1/patients/{p2.id}/adherence", headers=assistant_headers).status_code == status.HTTP_403_FORBIDDEN

    # 6. POST /patients/{id}/assistant/alerts for p2
    assert client.post(
        f"/api/v1/patients/{p2.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom"},
    ).status_code == status.HTTP_403_FORBIDDEN


def test_assistant_key_rejected_when_either_setting_is_unset(make_client, db_session, assistant_headers):
    """If ASSISTANT_API_KEY or ASSISTANT_PATIENT_ID is unset, assistant key is rejected everywhere."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    client = make_client(FROZEN_NOW)

    # When ASSISTANT_API_KEY is None
    settings.ASSISTANT_API_KEY = None
    r1 = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=assistant_headers)
    assert r1.status_code == status.HTTP_403_FORBIDDEN

    # Restore key, set ASSISTANT_PATIENT_ID to None
    settings.ASSISTANT_API_KEY = SecretStr(TEST_ASSISTANT_KEY)
    settings.ASSISTANT_PATIENT_ID = None
    r2 = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=assistant_headers)
    assert r2.status_code == status.HTTP_403_FORBIDDEN


def test_alert_routing_by_notify_level_and_honest_messages(make_client, db_session, assistant_headers):
    """
    Caregivers with notify_level 'all' and 'urgent_only' receive alerts; 'missed_only' receive none.
    The note text is never in sent message. Report time in patient timezone is in message.
    """
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1, tz="Asia/Kolkata")

    cg_all = Caregiver(
        name="Son All",
        phone="+919876543211",
        whatsapp_id="wa:all",
        telegram_chat_id="chat_all",
        relationship="Son",
        notify_level=NotifyLevel.ALL,
    )
    cg_urgent = Caregiver(
        name="Daughter Urgent",
        phone="+919876543212",
        whatsapp_id="wa:urgent",
        telegram_chat_id="chat_urgent",
        relationship="Daughter",
        notify_level=NotifyLevel.URGENT_ONLY,
    )
    cg_missed = Caregiver(
        name="Neighbor Missed",
        phone="+919876543213",
        whatsapp_id="wa:missed",
        telegram_chat_id="chat_missed",
        relationship="Neighbor",
        notify_level=NotifyLevel.MISSED_ONLY,
    )
    db_session.add_all([cg_all, cg_urgent, cg_missed])
    db_session.commit()

    db_session.add_all([
        PatientCaregiver(patient_id=p.id, caregiver_id=cg_all.id),
        PatientCaregiver(patient_id=p.id, caregiver_id=cg_urgent.id),
        PatientCaregiver(patient_id=p.id, caregiver_id=cg_missed.id),
    ])
    db_session.commit()

    notifier = get_notifier()
    notifier.reset()

    client = make_client(FROZEN_NOW)
    secret_note = "Private sensitive symptoms 12345"

    res = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom", "note": secret_note},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["sent"] is True
    assert data["caregivers_notified"] == 2
    assert data["patient_message"] == "I have told your caregiver. If you feel worse, please contact your doctor."

    # Check notifier received exactly 2 alerts (cg_all and cg_urgent)
    sent = notifier.sent_caregiver_alerts
    assert len(sent) == 2
    recipients = {s["caregiver_id"] for s in sent}
    assert recipients == {cg_all.id, cg_urgent.id}
    assert cg_missed.id not in recipients

    # Check message content: names patient, category, report time; never note text
    for item in sent:
        msg = item["message"]
        assert p.name in msg
        assert "symptom" in msg
        assert secret_note not in msg
        # FROZEN_NOW is 08:30 UTC -> in Asia/Kolkata (+5:30) it is 2:00 PM
        assert "2:00 PM" in msg


def test_honest_patient_messages_and_status_when_no_recipients(make_client, db_session, assistant_headers):
    """When no eligible caregiver exists or none has usable contact, return sent=False and honest failure message."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    client = make_client(FROZEN_NOW)

    categories_and_expected = [
        ("symptom", "I could not reach your caregiver. If you feel worse, please contact your doctor."),
        ("needs_help", "I could not reach your caregiver. Please ask someone near you for help."),
        ("emergency", "I could not reach your caregiver. If this is an emergency, call 112 now."),
    ]

    for cat, expected_msg in categories_and_expected:
        res = client.post(
            f"/api/v1/patients/{p.id}/assistant/alerts",
            headers=assistant_headers,
            json={"category": cat},
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["sent"] is False
        assert data["caregivers_notified"] == 0
        assert data["patient_message"] == expected_msg

        log_row = (
            db_session.query(AlertLog)
            .filter(AlertLog.patient_id == p.id, AlertLog.category == cat)
            .order_by(AlertLog.id.desc())
            .first()
        )
        assert log_row.status == "no_recipients"
        assert log_row.caregivers_notified == 0


def test_honest_patient_messages_and_status_when_notifier_fails(make_client, db_session, assistant_headers):
    """When notifier fails, return sent=False, caregivers_notified=0, status=failed."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    cg = Caregiver(
        name="Son Caregiver",
        phone="+919876543211",
        whatsapp_id="wa:11",
        telegram_chat_id="chat_11",
        relationship="Son",
        notify_level=NotifyLevel.ALL,
    )
    db_session.add(cg)
    db_session.commit()
    db_session.add(PatientCaregiver(patient_id=p.id, caregiver_id=cg.id))
    db_session.commit()

    notifier = get_notifier()
    notifier.reset()
    notifier.fail_caregiver_alerts = True  # Injects delivery failure

    client = make_client(FROZEN_NOW)
    res = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "needs_help"},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["sent"] is False
    assert data["caregivers_notified"] == 0
    assert data["patient_message"] == "I could not reach your caregiver. Please ask someone near you for help."

    log_row = db_session.query(AlertLog).filter(AlertLog.patient_id == p.id).first()
    assert log_row.status == "failed"


def test_notifier_permanent_error_and_exception_handling(make_client, db_session, assistant_headers):
    """Notifier raising PermanentNotifierError or arbitrary Exception is caught and status recorded as failed."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    cg = Caregiver(
        name="Son Caregiver",
        phone="+919876543211",
        whatsapp_id="wa:11",
        telegram_chat_id="chat_11",
        relationship="Son",
        notify_level=NotifyLevel.ALL,
    )
    db_session.add(cg)
    db_session.commit()
    db_session.add(PatientCaregiver(patient_id=p.id, caregiver_id=cg.id))
    db_session.commit()

    class RaisingNotifier(BaseNotifier):
        channel = "mock"
        contact_attr = "whatsapp_id"

        def __init__(self, exc_to_raise):
            self.exc_to_raise = exc_to_raise

        def send_patient_reminder(self, patient, dose_event):
            return True

        def send_caregiver_alert(self, caregiver, message):
            raise self.exc_to_raise

    client = make_client(FROZEN_NOW)

    # 1. PermanentNotifierError
    fastapi_app.dependency_overrides[get_notifier] = lambda: RaisingNotifier(PermanentNotifierError("blocked"))
    res1 = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom"},
    )
    assert res1.status_code == status.HTTP_200_OK
    assert res1.json()["sent"] is False
    assert res1.json()["caregivers_notified"] == 0
    assert res1.json()["patient_message"] == "I could not reach your caregiver. If you feel worse, please contact your doctor."

    # 2. Arbitrary Exception
    fastapi_app.dependency_overrides[get_notifier] = lambda: RaisingNotifier(ConnectionError("network down"))
    res2 = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "emergency"},
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["sent"] is False
    assert res2.json()["caregivers_notified"] == 0
    assert res2.json()["patient_message"] == "I could not reach your caregiver. If this is an emergency, call 112 now."


def test_note_saved_before_cooldown_and_rate_limiting(make_client, db_session, assistant_headers):
    """
    1. Note is always saved in DB even if rate limited.
    2. Cooldown blocks retry inside window (returns 429) and sends nothing.
    3. Emergency 429 body contains 'call 112 now'.
    4. Alert works again after cooldown window passes.
    """
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    cg = Caregiver(
        name="Son Caregiver",
        phone="+919876543211",
        whatsapp_id="wa:11",
        telegram_chat_id="chat_11",
        relationship="Son",
        notify_level=NotifyLevel.ALL,
    )
    db_session.add(cg)
    db_session.commit()
    db_session.add(PatientCaregiver(patient_id=p.id, caregiver_id=cg.id))
    db_session.commit()

    notifier = get_notifier()
    notifier.reset()

    # Alert 1 at T=0
    client = make_client(FROZEN_NOW)
    res1 = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "emergency", "note": "First emergency note"},
    )
    assert res1.status_code == status.HTTP_200_OK
    assert res1.json()["sent"] is True
    assert len(notifier.sent_caregiver_alerts) == 1

    # Check note 1 stored
    notes = db_session.query(PatientNote).filter(PatientNote.patient_id == p.id).all()
    assert len(notes) == 1
    assert notes[0].text == "First emergency note"
    assert notes[0].kind == NoteKind.SYMPTOM
    assert notes[0].flagged_to_caregiver is True

    # Alert 2 at T=5 min (inside 10-min window)
    client2 = make_client(FROZEN_NOW + timedelta(minutes=5))
    res2 = client2.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "emergency", "note": "Second note during cooldown"},
    )
    assert res2.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    data2 = res2.json()
    assert "call 112 now" in data2["patient_message"]
    # Cooldown sends nothing new
    assert len(notifier.sent_caregiver_alerts) == 1

    # Note 2 MUST still be saved even though the alert was rate limited!
    notes_after = db_session.query(PatientNote).filter(PatientNote.patient_id == p.id).all()
    assert len(notes_after) == 2
    assert notes_after[1].text == "Second note during cooldown"

    # Alert 3 at T=11 min (after cooldown window)
    client3 = make_client(FROZEN_NOW + timedelta(minutes=11))
    res3 = client3.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "emergency", "note": "Third note after cooldown"},
    )
    assert res3.status_code == status.HTTP_200_OK
    assert res3.json()["sent"] is True
    assert len(notifier.sent_caregiver_alerts) == 2


def test_failed_or_no_recipients_does_not_block_retry(make_client, db_session, assistant_headers):
    """Cooldown counts only rows with status='sent'. Failed or no_recipients alerts never block retry."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    client = make_client(FROZEN_NOW)

    # First attempt: no caregivers -> status is 'no_recipients'
    r1 = client.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom"},
    )
    assert r1.status_code == status.HTTP_200_OK
    assert r1.json()["sent"] is False

    # Second attempt immediately at T+1 min: should NOT return 429
    client_retry = make_client(FROZEN_NOW + timedelta(minutes=1))
    r2 = client_retry.post(
        f"/api/v1/patients/{p.id}/assistant/alerts",
        headers=assistant_headers,
        json={"category": "symptom"},
    )
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["sent"] is False


def test_alert_log_privacy_and_logging(make_client, db_session, assistant_headers, caplog):
    """AlertLog table contains no sensitive data. Exactly one fixed INFO line per alert."""
    p, m, s, d = _seed_patient_with_caregiver(db_session, pid=1)
    cg = Caregiver(
        name="Son Caregiver",
        phone="+919876543211",
        whatsapp_id="wa:11",
        telegram_chat_id="chat_11",
        relationship="Son",
        notify_level=NotifyLevel.ALL,
    )
    db_session.add(cg)
    db_session.commit()
    db_session.add(PatientCaregiver(patient_id=p.id, caregiver_id=cg.id))
    db_session.commit()

    client = make_client(FROZEN_NOW)
    with caplog.at_level(logging.INFO):
        res = client.post(
            f"/api/v1/patients/{p.id}/assistant/alerts",
            headers=assistant_headers,
            json={"category": "needs_help", "note": "super secret note text"},
        )
    assert res.status_code == status.HTTP_200_OK

    log_entry = db_session.query(AlertLog).filter(AlertLog.patient_id == p.id).first()
    assert log_entry is not None
    assert log_entry.patient_id == p.id
    assert log_entry.category == "needs_help"
    assert log_entry.caregivers_notified == 1
    assert log_entry.status == "sent"

    # Ensure AlertLog table has no sensitive attributes
    for col in ("note", "text", "telegram_chat_id", "whatsapp_id", "token"):
        assert not hasattr(log_entry, col)

    # Check logger output
    alert_logs = [record.message for record in caplog.records if "assistant alert" in record.message]
    assert len(alert_logs) == 1
    assert "category=needs_help outcome=sent" in alert_logs[0]
    # No patient name or note text in log
    assert p.name not in alert_logs[0]
    assert "super secret" not in alert_logs[0]


def test_alembic_history_eight_revisions():
    """Verify that Alembic history contains exactly 8 revisions and head is 0008."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(alembic_cfg)
    revisions = list(script.walk_revisions())

    assert len(revisions) == 8, f"Expected 8 revisions in alembic history, got {len(revisions)}"
    head = script.get_current_head()
    assert head == "0008abcdef01"
