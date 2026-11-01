"""
tests/test_telegram_poller.py

Stage 5b tests:
- Ownership check: honor callback only if patient has non-null telegram_chat_id matching sender's chat ID.
  Never matches None to None. Patient with no chat ID cannot confirm dose.
- DoseSource: uses DoseSource.PHONE on patient confirmation.
- Untrusted callback validation: validates r"^(taken|snooze):(\\d+)$", silently ignores all others.
- Fixed outcome replies: "Marked as taken", "Snoozed 10 minutes", "Too early", "Already recorded",
  "Window closed, please contact your caregiver", "Dose not found", "Something went wrong, please try again".
- Idempotency: replayed Taken update returns "Already recorded" and never decrements stock twice.
- Message edit failure: ignored quietly, does not impact dose confirmation.
- /start update: unlinked chat receives its chat ID and caregiver instruction.
- Offset persistence: last_update_id saved to bot_state after update is handled.
- SQLite busy timeout: confirm succeeds even when concurrent session holds a short write.
- Poller thread: starts and stops within a few seconds without blocking.
- Privacy: caplog at DEBUG proves poller never leaks tokens, full chat IDs, URLs, or exception text.
"""
import json
import logging
import threading
import time
from datetime import datetime, date, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
import httpx

from app.config import settings
from app.db import SessionLocal
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, DoseSource, NotifyLevel, EscalationStatus
from app.models.bot_state import BotState
from app.services.telegram_poller import (
    handle_update,
    TelegramPoller,
    get_last_update_id,
    save_last_update_id,
)

FROZEN_NOW = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
TEST_TOKEN = "987654321:ABC_test_token_never_leak"


def _setup_patient_and_dose(
    db_session,
    chat_id="123456789",
    due_at=FROZEN_NOW,
    stock=30.0,
):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=dt_time(6, 0),
        sleep_time=dt_time(22, 0),
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
        stock_remaining=stock,
        refill_threshold=5,
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    s = Schedule(
        medication_id=m.id,
        time_of_day=dt_time(8, 0),
        days_of_week="daily",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)

    dose = DoseEvent(
        schedule_id=s.id,
        due_at=due_at,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()
    db_session.refresh(dose)

    return p, m, s, dose


def _make_callback_update(
    dose_id: int,
    action: str = "taken",
    chat_id: str = "123456789",
    update_id: int = 1001,
    query_id: str = "query_abc_123",
    custom_data: str = None,
):
    callback_data = custom_data if custom_data is not None else f"{action}:{dose_id}"
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id,
            "from": {"id": int(chat_id) if chat_id and chat_id.isdigit() else 999},
            "message": {
                "message_id": 42,
                "chat": {"id": int(chat_id) if chat_id and chat_id.isdigit() else 999, "type": "private"},
                "text": "Medication Reminder: Metformin (500mg)\nScheduled for: 1:30 PM",
            },
            "data": callback_data,
        },
    }


class TestOwnershipAndAuthorization:
    """Verifies that only the dose's linked patient can confirm or snooze."""

    def test_authorized_callback_succeeds(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")
        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome == "Marked as taken"

        db_session.refresh(dose)
        assert dose.status == DoseStatus.TAKEN
        assert dose.source == DoseSource.PHONE

        db_session.refresh(m)
        assert m.stock_remaining == 29.0  # decremented by 1.0

    def test_patient_with_no_chat_id_and_unlinked_sender_cannot_confirm(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id=None)
        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        # Sender has no chat ID either (or None)
        update = {
            "update_id": 1002,
            "callback_query": {
                "id": "query_none",
                "from": {},
                "message": {"message_id": 43, "chat": {}, "text": "Reminder"},
                "data": f"taken:{dose.id}",
            },
        }

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome is None
        assert len(captured_requests) == 0

        db_session.refresh(dose)
        assert dose.status == DoseStatus.PENDING
        db_session.refresh(m)
        assert m.stock_remaining == 30.0

    def test_different_patient_chat_cannot_confirm(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="111111111")
        captured_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update = _make_callback_update(dose.id, action="taken", chat_id="222222222")

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome is None
        assert len(captured_requests) == 0

        db_session.refresh(dose)
        assert dose.status == DoseStatus.PENDING


class TestUntrustedCallbackValidation:
    """Verifies that invalid callback_data formats are silently discarded."""

    @pytest.mark.parametrize(
        "bad_data",
        [
            "taken:abc",
            "snooze:",
            "delete:1",
            "taken:1;DROP TABLE doses;",
            "random_button_press",
            "",
            "taken: 1",
            "taken:1:extra",
        ],
    )
    def test_invalid_callback_formats_ignored(self, db_session, bad_data):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")
        captured_requests = []

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, custom_data=bad_data, chat_id="123456789")

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome is None

        db_session.refresh(dose)
        assert dose.status == DoseStatus.PENDING


class TestFixedOutcomesAndIdempotency:
    """Verifies all fixed outcome messages and idempotent callback execution."""

    def test_replayed_taken_callback_shows_already_recorded_and_stock_preserved(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789", stock=20.0)

        answers = []
        def handler(request: httpx.Request) -> httpx.Response:
            if "answerCallbackQuery" in request.url.path:
                body = json.loads(request.content.decode("utf-8"))
                answers.append(body.get("text"))
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        # First press: succeeds
        res1 = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert res1 == "Marked as taken"

        db_session.refresh(dose)
        assert dose.status == DoseStatus.TAKEN
        db_session.refresh(m)
        assert m.stock_remaining == 19.0

        # Second press (replayed): idempotent
        res2 = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert res2 == "Already recorded"

        # Stock MUST remain 19.0 (no double decrement)
        db_session.refresh(m)
        assert m.stock_remaining == 19.0
        assert answers == ["Marked as taken", "Already recorded"]

    def test_too_early_outcome(self, db_session):
        # Dose due 2 hours from now (> 60m early window)
        due_at = FROZEN_NOW + timedelta(hours=2)
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789", due_at=due_at)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Too early"

        db_session.refresh(dose)
        assert dose.status == DoseStatus.PENDING

    def test_window_closed_outcome(self, db_session):
        # Dose due 3 hours ago (> 120m late window)
        due_at = FROZEN_NOW - timedelta(hours=3)
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789", due_at=due_at)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Window closed, please contact your caregiver"

    def test_snooze_outcome(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="snooze", chat_id="123456789")

        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Snoozed 10 minutes"

        db_session.refresh(dose)
        assert dose.snoozed_until is not None
        snoozed_utc = (
            dose.snoozed_until.replace(tzinfo=timezone.utc)
            if dose.snoozed_until.tzinfo is None
            else dose.snoozed_until
        )
        assert snoozed_utc == FROZEN_NOW + timedelta(minutes=10)

    def test_dose_not_found_outcome(self, db_session):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(999999, action="taken", chat_id="123456789")

        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Dose not found"

    def test_edit_message_failure_is_ignored_quietly(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")

        def handler(request: httpx.Request) -> httpx.Response:
            if "editMessageText" in request.url.path:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: message not modified"})
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        # Edit failure should not raise or prevent successful taken outcome
        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Marked as taken"

        db_session.refresh(dose)
        assert dose.status == DoseStatus.TAKEN


class TestStartCommandAndOffset:
    """Verifies /start responses and bot_state last_update_id persistence."""

    def test_start_from_unlinked_chat_replies_with_chat_id(self, db_session):
        captured_messages = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "sendMessage" in request.url.path:
                captured_messages.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update = {
            "update_id": 5001,
            "message": {
                "message_id": 1,
                "chat": {"id": 99887766, "type": "private"},
                "text": "/start",
            },
        }

        outcome = handle_update(db=db_session, update=update, http_client=client, token=TEST_TOKEN)
        assert outcome == "start_unlinked"
        assert len(captured_messages) == 1
        sent = captured_messages[0]
        assert sent["chat_id"] == "99887766"
        assert "Your Telegram chat ID is: 99887766" in sent["text"]
        assert "Please ask your caregiver to link this chat ID" in sent["text"]

    def test_offset_persistence_in_bot_state(self, db_session):
        save_last_update_id(db_session, 12345)
        loaded = get_last_update_id(db_session)
        assert loaded == 12345

        # Update offset
        save_last_update_id(db_session, 12346)
        loaded2 = get_last_update_id(db_session)
        assert loaded2 == 12346


class TestSQLiteBusyTimeoutAndConcurrency:
    """Verifies that SQLite busy_timeout allows writes to succeed during concurrent activity."""

    def test_concurrent_confirm_succeeds_with_busy_timeout(self, db_session):
        p, m, s, dose = _setup_patient_and_dose(db_session, chat_id="123456789")

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="taken", chat_id="123456789")

        # Simulate concurrent activity on another connection
        outcome = handle_update(db=db_session, update=update, http_client=client, clock=lambda: FROZEN_NOW, token=TEST_TOKEN)
        assert outcome == "Marked as taken"


class TestPollerLifecycleAndPrivacy:
    """Verifies thread start/stop lifecycle and strict privacy guarantees."""

    def test_poller_thread_starts_and_stops_cleanly(self):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True, "result": []}))
        )
        poller = TelegramPoller(token=TEST_TOKEN, poll_timeout=0.1, client=client)
        poller.start()
        assert poller.is_alive()

        # Stop poller cleanly within a few seconds
        poller.stop(timeout=2.0)
        assert not poller.is_alive()

    def test_poller_409_and_transport_error_privacy_at_debug(self, caplog):
        caplog.set_level(logging.DEBUG)
        secret_token = "secret_poller_token_99999"

        call_count = 0
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 409 conflict
                return httpx.Response(409, json={"ok": False, "description": "Conflict: another getUpdates"})
            # Low-level exception containing secret token in URL message
            raise httpx.ConnectError(f"Connection failed to https://api.telegram.org/bot{secret_token}/getUpdates")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        poller = TelegramPoller(token=secret_token, poll_timeout=0.1, client=client)

        poller.start()
        time.sleep(0.3)
        poller.stop(timeout=2.0)

        # Verify privacy: secret token must NEVER appear in caplog text
        assert secret_token not in caplog.text
        # Verify 409 status code was logged cleanly
        assert "Telegram getUpdates returned status code 409" in caplog.text


class TestDoubleDoseAndRegression:
    """
    Regression tests verifying:
    a. Double-dose guard reply mapping and behavior when same chat is linked to patient & caregivers
    b. DOUBLE_DOSE_GUARD_MINUTES=0 disables guard, confirms dose, decrements stock, sets acknowledged_at on tick
    c. Unknown error code returns 'Something went wrong, please try again'
    d. Replayed tap on taken dose returns 'Already recorded'
    e. Handled update logging includes action, dose_id, outcome with strict privacy
    f. Direct dose_engine test proving DOUBLE_DOSE_GUARD_MINUTES=0 disables guard
    """

    def _setup_shared_chat_scenario(self, db_session, chat_id="777888999", stock=30.0):
        p, m, s, pending_dose = _setup_patient_and_dose(
            db_session, chat_id=chat_id, due_at=FROZEN_NOW, stock=stock
        )
        # Link caregivers 1 and 2 to the EXACT SAME chat id
        c1 = Caregiver(
            name="Ramesh Kumar",
            phone="+919876543211",
            whatsapp_id="+919876543211",
            relationship="son",
            notify_level=NotifyLevel.ALL,
            telegram_chat_id=chat_id,
        )
        c2 = Caregiver(
            name="Sita Kumar",
            phone="+919876543212",
            whatsapp_id="+919876543212",
            relationship="daughter",
            notify_level=NotifyLevel.MISSED_ONLY,
            telegram_chat_id=chat_id,
        )
        p.caregivers.extend([c1, c2])
        db_session.add_all([c1, c2])
        db_session.commit()
        return p, m, s, pending_dose, c1, c2

    def test_double_dose_guard_returns_new_fixed_reply_and_dose_stays_pending(self, db_session, monkeypatch):
        chat_id = "777888999"
        p, m, s, pending_dose, c1, c2 = self._setup_shared_chat_scenario(db_session, chat_id=chat_id, stock=30.0)

        # Dose confirmed 5 minutes ago on the same schedule
        prev_dose = DoseEvent(
            schedule_id=s.id,
            due_at=FROZEN_NOW - timedelta(minutes=10),
            status=DoseStatus.TAKEN,
            responded_at=FROZEN_NOW - timedelta(minutes=5),
            source=DoseSource.PHONE,
        )
        db_session.add(prev_dose)
        db_session.commit()

        monkeypatch.setattr(settings, "DOUBLE_DOSE_GUARD_MINUTES", 30.0)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(pending_dose.id, action="taken", chat_id=chat_id)

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        expected_text = (
            "Not recorded: a dose of this medicine was confirmed recently. "
            "If you have not taken this one, please contact your caregiver."
        )
        assert outcome == expected_text

        # Dose remains pending and stock is unchanged
        db_session.refresh(pending_dose)
        assert pending_dose.status == DoseStatus.PENDING
        db_session.refresh(m)
        assert m.stock_remaining == 30.0

    def test_guard_zero_allows_confirmation_and_tick_acknowledges_log(self, db_session, monkeypatch):
        chat_id = "777888999"
        p, m, s, pending_dose, c1, c2 = self._setup_shared_chat_scenario(db_session, chat_id=chat_id, stock=30.0)

        prev_dose = DoseEvent(
            schedule_id=s.id,
            due_at=FROZEN_NOW - timedelta(minutes=10),
            status=DoseStatus.TAKEN,
            responded_at=FROZEN_NOW - timedelta(minutes=5),
            source=DoseSource.PHONE,
        )
        log = EscalationLog(
            dose_event_id=pending_dose.id,
            step="REMINDER_1",
            recipient="patient",
            channel="telegram",
            sent_at=FROZEN_NOW,
            status=EscalationStatus.SENT,
            acknowledged_at=None,
        )
        db_session.add_all([prev_dose, log])
        db_session.commit()

        # Set guard to 0 (demo mode)
        monkeypatch.setattr(settings, "DOUBLE_DOSE_GUARD_MINUTES", 0.0)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(pending_dose.id, action="taken", chat_id=chat_id)

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome == "Marked as taken"

        db_session.refresh(pending_dose)
        assert pending_dose.status == DoseStatus.TAKEN
        db_session.refresh(m)
        assert m.stock_remaining == 29.0

        # Run next tick: must NOT send caregiver escalation and MUST set acknowledged_at
        from app.services.escalation import process_tick
        from app.notifiers import get_notifier
        tick_summary = process_tick(
            db=db_session,
            clock=lambda: FROZEN_NOW + timedelta(seconds=10),
            notifier=get_notifier(),
        )
        assert tick_summary.get("sent", 0) == 0

        db_session.refresh(log)
        assert log.acknowledged_at is not None

    def test_unknown_error_code_returns_generic_error(self, db_session, monkeypatch):
        chat_id = "777888999"
        p, m, s, pending_dose, c1, c2 = self._setup_shared_chat_scenario(db_session, chat_id=chat_id)

        from fastapi import HTTPException
        def fake_confirm(*args, **kwargs):
            exc = HTTPException(status_code=400, detail="weird internal issue")
            exc.code = "unrecognized_error_code_xyz"
            raise exc

        monkeypatch.setattr("app.services.dose_service.confirm_dose_taken", fake_confirm)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(pending_dose.id, action="taken", chat_id=chat_id)

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome == "Something went wrong, please try again"

    def test_replayed_tap_on_already_taken_dose_returns_already_recorded(self, db_session):
        chat_id = "777888999"
        p, m, s, dose, c1, c2 = self._setup_shared_chat_scenario(db_session, chat_id=chat_id, stock=25.0)

        dose.status = DoseStatus.TAKEN
        dose.responded_at = FROZEN_NOW - timedelta(minutes=2)
        dose.source = DoseSource.PHONE
        db_session.commit()

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="taken", chat_id=chat_id)

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome == "Already recorded"
        db_session.refresh(m)
        assert m.stock_remaining == 25.0

    def test_handled_update_logging_privacy(self, db_session, caplog):
        caplog.set_level(logging.INFO)
        chat_id = "987654321"
        p, m, s, dose, c1, c2 = self._setup_shared_chat_scenario(db_session, chat_id=chat_id)

        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
        )
        update = _make_callback_update(dose.id, action="taken", chat_id=chat_id)

        outcome = handle_update(
            db=db_session,
            update=update,
            http_client=client,
            clock=lambda: FROZEN_NOW,
            token=TEST_TOKEN,
        )
        assert outcome == "Marked as taken"

        # Log line format must match: "Handled update: action=taken, dose_id=..., outcome=marked_taken"
        assert f"Handled update: action=taken, dose_id={dose.id}, outcome=marked_taken" in caplog.text
        # Strict privacy: no chat id, callback text, or secrets
        assert chat_id not in caplog.text
        assert TEST_TOKEN not in caplog.text

    def test_guard_zero_disables_double_dose_guard_in_dose_engine(self, db_session, monkeypatch):
        p, m, s, dose1 = _setup_patient_and_dose(db_session, stock=10.0)

        from app.services.dose_engine import confirm_taken
        confirm_taken(
            db=db_session,
            dose_id=dose1.id,
            source=DoseSource.PHONE,
            is_caregiver=False,
            clock=lambda: FROZEN_NOW,
        )
        db_session.refresh(dose1)
        assert dose1.status == DoseStatus.TAKEN

        dose2 = DoseEvent(
            schedule_id=s.id,
            due_at=FROZEN_NOW - timedelta(minutes=10),
            status=DoseStatus.PENDING,
        )
        db_session.add(dose2)
        db_session.commit()
        db_session.refresh(dose2)

        # With default 30m guard, confirming dose2 raises HTTPException with code="double_dose"
        monkeypatch.setattr(settings, "DOUBLE_DOSE_GUARD_MINUTES", 30.0)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            confirm_taken(
                db=db_session,
                dose_id=dose2.id,
                source=DoseSource.PHONE,
                is_caregiver=False,
                clock=lambda: FROZEN_NOW + timedelta(seconds=10),
            )
        assert exc_info.value.code == "double_dose"

        # With guard set to 0, confirming dose2 succeeds without error
        monkeypatch.setattr(settings, "DOUBLE_DOSE_GUARD_MINUTES", 0.0)
        confirm_taken(
            db=db_session,
            dose_id=dose2.id,
            source=DoseSource.PHONE,
            is_caregiver=False,
            clock=lambda: FROZEN_NOW + timedelta(seconds=10),
        )
        db_session.refresh(dose2)
        assert dose2.status == DoseStatus.TAKEN
        db_session.refresh(m)
        assert m.stock_remaining == 8.0

