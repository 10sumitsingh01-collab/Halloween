"""
tests/test_telegram_notifier.py

Stage 5a tests:
- Settings: TELEGRAM_BOT_TOKEN stored as SecretStr; repr(settings) masks token.
- Conftest isolation: proves test suite runs with NOTIFIER_CHANNEL=mock and no token from .env.
- TelegramNotifier payload & keyboard: correct URL, inline buttons, and plain text formatting.
- Error mapping: 200 -> True, 429/500/timeout -> False, 400/403 -> PermanentNotifierError.
- Escalation engine integration: permanent failure marks 'failed' with detail 'recipient unreachable' and 0 retries.
- Escalation contact check: patient missing telegram_chat_id marked 'failed' with 'patient has no telegram_chat_id'.
- Strict privacy: transport error containing token in message never leaks token into caplog at DEBUG,
  EscalationLog.detail, or notifier output.
- Schema privacy: telegram_chat_id is NOT included in PatientResponse or CaregiverResponse.
- Scripts: link_telegram.py and telegram_test.py functionality and privacy.
"""
import json
import logging
from datetime import datetime, date, time, timezone
import pytest
import httpx
from pydantic import SecretStr

from app.config import Settings, settings
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, NotifyLevel, EscalationStatus
from app.notifiers.base import PermanentNotifierError
from app.notifiers.telegram import TelegramNotifier
from app.notifiers import get_notifier
from app.services.escalation import process_tick
from app.schemas.patient import PatientResponse
from app.schemas.caregiver import CaregiverResponse
from scripts.link_telegram import mask_chat_id


FROZEN_NOW = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
TEST_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"


def _setup_patient_and_dose(db_session, chat_id="112233445"):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=time(6, 0),
        sleep_time=time(22, 0),
        telegram_chat_id=chat_id,
        whatsapp_id="+919876543210",
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

    due_at = FROZEN_NOW
    dose = DoseEvent(
        schedule_id=s.id,
        due_at=due_at,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()
    db_session.refresh(dose)

    return p, m, s, dose


class TestSettingsAndIsolation:
    """Verifies privacy of token in settings and conftest test suite isolation."""

    def test_settings_repr_masks_token(self):
        s = Settings(TELEGRAM_BOT_TOKEN="super_secret_telegram_token_12345")
        settings_repr = repr(s)
        settings_str = str(s)
        assert "super_secret_telegram_token_12345" not in settings_repr
        assert "super_secret_telegram_token_12345" not in settings_str
        assert "**********" in settings_repr

    def test_conftest_ensures_mock_and_no_real_token(self):
        # The test environment must strictly run with mock notifier and cleared token
        assert settings.NOTIFIER_CHANNEL == "mock"
        assert settings.TELEGRAM_BOT_TOKEN is None
        notifier = get_notifier()
        assert notifier.channel == "mock"
        assert notifier.contact_attr == "whatsapp_id"

    def test_response_schemas_do_not_expose_telegram_chat_id(self):
        patient_fields = PatientResponse.model_fields.keys()
        assert "telegram_chat_id" not in patient_fields
        caregiver_fields = CaregiverResponse.model_fields.keys()
        assert "telegram_chat_id" not in caregiver_fields


class TestTelegramNotifierPayloads:
    """Verifies TelegramNotifier HTTP requests, URLs, and inline keyboard structure."""

    def test_send_patient_reminder_payload_and_buttons(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="99887766")
        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)

        success = notifier.send_patient_reminder(p, dose)
        assert success is True
        assert len(captured_requests) == 1

        req = captured_requests[0]
        assert req.method == "POST"
        assert req.url.path == f"/bot{TEST_TOKEN}/sendMessage"

        body = json.loads(req.content.decode("utf-8"))
        assert body["chat_id"] == "99887766"
        assert "Medication Reminder: Metformin (500mg)" in body["text"]
        assert "Scheduled for: 1:30 PM" in body["text"]

        keyboard = body.get("reply_markup", {}).get("inline_keyboard", [])
        assert len(keyboard) == 1
        buttons = keyboard[0]
        assert len(buttons) == 2
        assert buttons[0] == {"text": "Taken", "callback_data": f"taken:{dose.id}"}
        assert buttons[1] == {"text": "Snooze 10 min", "callback_data": f"snooze:{dose.id}"}

    def test_send_caregiver_alert_payload_no_buttons(self, db_session):
        c = Caregiver(
            name="Ravi Kumar",
            phone="+919876543210",
            whatsapp_id="+919876543210",
            telegram_chat_id="55443322",
            relationship="Son",
            notify_level=NotifyLevel.ALL,
        )
        db_session.add(c)
        db_session.commit()

        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 102}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)

        success = notifier.send_caregiver_alert(c, "Caregiver alert text")
        assert success is True
        assert len(captured_requests) == 1

        req = captured_requests[0]
        body = json.loads(req.content.decode("utf-8"))
        assert body["chat_id"] == "55443322"
        assert body["text"] == "Caregiver alert text"
        assert "reply_markup" not in body


class TestTelegramNotifierErrors:
    """Verifies error mapping: 429/500/timeout -> False, 400/403 -> PermanentNotifierError."""

    def test_transient_failure_500_returns_false(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"ok": False, "description": "Internal Server Error"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)
        assert notifier.send_patient_reminder(p, dose) is False

    def test_transient_failure_429_returns_false(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)
        assert notifier.send_patient_reminder(p, dose) is False

    def test_transient_network_timeout_returns_false(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("Connection timed out")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)
        assert notifier.send_patient_reminder(p, dose) is False

    def test_permanent_failure_403_raises_permanent_notifier_error(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)
        with pytest.raises(PermanentNotifierError) as exc_info:
            notifier.send_patient_reminder(p, dose)
        assert "recipient unreachable" in str(exc_info.value)

    def test_permanent_failure_400_raises_permanent_notifier_error(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)
        with pytest.raises(PermanentNotifierError):
            notifier.send_patient_reminder(p, dose)


class TestEscalationEngineWithTelegramNotifier:
    """Verifies escalation ladder with TelegramNotifier: contact checks, permanent failure handling."""

    def test_missing_telegram_chat_id_logs_detail_no_retries(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id=None)
        p.telegram_chat_id = None
        db_session.commit()

        client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)

        clock_time = FROZEN_NOW
        summary = process_tick(db=db_session, clock=lambda: clock_time, notifier=notifier)
        assert summary["failed"] == 1

        log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "REMINDER_1",
                EscalationLog.recipient == "patient",
            )
            .first()
        )
        assert log is not None
        assert log.status == EscalationStatus.FAILED
        assert log.attempts == 1
        assert log.detail == "patient has no telegram_chat_id"

    def test_permanent_error_marks_failed_immediately_no_retries(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="12345")

        def blocked_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot blocked"})

        client = httpx.Client(transport=httpx.MockTransport(blocked_handler))
        notifier = TelegramNotifier(token=TEST_TOKEN, client=client)

        clock_time = FROZEN_NOW
        summary = process_tick(db=db_session, clock=lambda: clock_time, notifier=notifier)
        assert summary["failed"] == 1

        log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "REMINDER_1",
                EscalationLog.recipient == "patient",
            )
            .first()
        )
        assert log is not None
        assert log.status == EscalationStatus.FAILED
        assert log.attempts == 1
        assert log.detail == "recipient unreachable"

        # Subsequent tick skips because status is FAILED
        summary2 = process_tick(db=db_session, clock=lambda: clock_time, notifier=notifier)
        assert summary2["sent"] == 0
        assert summary2["failed"] == 0


class TestPrivacyAndMasking:
    """Hard privacy tests: token and chat IDs never leak in logs, details, or exception traces."""

    def test_transport_error_containing_token_never_leaks_in_logs_or_detail(self, db_session, caplog):
        caplog.set_level(logging.DEBUG)
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")
        secret_token = "secret_token_never_leak_xyz_123"

        def leaking_transport_handler(request: httpx.Request):
            # Simulate low-level exception whose string message embeds the URL with secret token
            raise httpx.ConnectError(f"Cannot reach https://api.telegram.org/bot{secret_token}/sendMessage")

        client = httpx.Client(transport=httpx.MockTransport(leaking_transport_handler))
        notifier = TelegramNotifier(token=secret_token, client=client)

        # 1. Direct call test
        result = notifier.send_patient_reminder(p, dose)
        assert result is False
        assert secret_token not in caplog.text

        # 2. Escalation tick test
        clock_time = FROZEN_NOW
        summary = process_tick(db=db_session, clock=lambda: clock_time, notifier=notifier)
        assert summary["failed"] >= 1

        log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "REMINDER_1",
                EscalationLog.recipient == "patient",
            )
            .first()
        )
        assert log is not None
        assert log.detail is None or secret_token not in log.detail
        assert secret_token not in caplog.text

        # 3. Direct exception in try_send to verify row.detail storing only class name
        from app.services.escalation import try_send
        def direct_raising_send():
            raise httpx.ConnectError(f"Failed to connect to https://api.telegram.org/bot{secret_token}/sendMessage")

        res = try_send(
            db=db_session,
            dose=dose,
            step="REMINDER_TEST",
            recipient="patient",
            send_fn=direct_raising_send,
            now=clock_time,
            channel="telegram",
            patient=p,
            contact_attr="telegram_chat_id",
        )
        assert res == "failed"
        log_raising = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "REMINDER_TEST",
                EscalationLog.recipient == "patient",
            )
            .first()
        )
        assert log_raising is not None
        assert log_raising.detail == "ConnectError"
        assert secret_token not in log_raising.detail
        assert secret_token not in caplog.text

    def test_mask_chat_id_utility(self):
        assert mask_chat_id("123456789") == "******789"
        assert mask_chat_id("98765") == "**765"
        assert mask_chat_id("123") == "***"
        assert mask_chat_id("12") == "***"


class TestScriptsAndStartup:
    """Verifies startup fail-fast behavior and CLI utility scripts."""

    def test_telegram_notifier_missing_token_raises_value_error(self, monkeypatch):
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
        with pytest.raises(ValueError) as exc_info:
            TelegramNotifier()
        assert "TELEGRAM_BOT_TOKEN is required" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_lifespan_fast_fails_on_telegram_without_token(self, monkeypatch):
        from app.main import lifespan, app as fastapi_app
        monkeypatch.setattr(settings, "NOTIFIER_CHANNEL", "telegram")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
        with pytest.raises(RuntimeError) as exc_info:
            async with lifespan(fastapi_app):
                pass
        assert "TELEGRAM_BOT_TOKEN must be configured" in str(exc_info.value)

    def test_link_telegram_get_latest_chat_id_filters_private_chats(self, monkeypatch):
        from scripts.link_telegram import get_latest_chat_id

        sample_updates = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": -100123456789, "type": "supergroup"},
                        "text": "Hello group",
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 88776655, "type": "private"},
                        "text": "Hello bot",
                    },
                },
            ],
        }

        def mock_get(url, params):
            return httpx.Response(200, json=sample_updates)

        monkeypatch.setattr(httpx.Client, "get", lambda self, url, params: mock_get(url, params))
        cid = get_latest_chat_id("fake_token")
        assert cid == "88776655"
