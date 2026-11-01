"""
app/services/telegram_poller.py

Long polling service for receiving Telegram callback query and message updates:
- Processes button presses (Taken / Snooze 10 min) with idempotent dose confirms and stock updates.
- Validates callback_data strictly as untrusted input: matches r"^(taken|snooze):(\\d+)$".
- Ownership validation: honors callback ONLY if patient has non-null telegram_chat_id
  matching the sender's chat ID (compared as strings). Never matches None to None.
- Handles /start from unlinked chats by replying with the sender's chat ID.
- Persists last_update_id offset to bot_state table in SQLite.
- Clean shutdown via stop_event without blocking.
- Strict privacy: no URLs, tokens, full chat IDs, or HTTP exception text in logs or replies.
"""
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.bot_state import BotState
from app.models.caregiver import Caregiver
from app.models.dose import DoseEvent
from app.models.enums import DoseSource
from app.models.patient import Patient
from app.services.clock import utc_now
from app.services import dose_service

logger = logging.getLogger("uvicorn.error")

# Suppress httpx and httpcore loggers to prevent leaking URLs with tokens
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_last_update_id(db: Session) -> Optional[int]:
    """Retrieve last_update_id from bot_state table."""
    state = db.get(BotState, "last_update_id")
    if state and state.value:
        try:
            return int(state.value)
        except ValueError:
            return None
    return None


def save_last_update_id(db: Session, update_id: int) -> None:
    """Save last_update_id to bot_state table after update is handled."""
    state = db.get(BotState, "last_update_id")
    if not state:
        state = BotState(key="last_update_id", value=str(update_id))
        db.add(state)
    else:
        state.value = str(update_id)
    db.commit()


def _post_send_message(
    http_client: httpx.Client,
    token: str,
    chat_id: str,
    text: str,
) -> bool:
    """Send plain message via Telegram Bot API without logging URLs/secrets."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = http_client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram sendMessage failed with %s", exc.__class__.__name__)
        return False


def _post_answer_callback(
    http_client: httpx.Client,
    token: str,
    callback_query_id: str,
    text: str,
) -> bool:
    """Answer callback query popup without logging URLs/secrets."""
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id, "text": text}
    try:
        resp = http_client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram answerCallbackQuery failed with %s", exc.__class__.__name__)
        return False


def _post_edit_message_text(
    http_client: httpx.Client,
    token: str,
    chat_id: str,
    message_id: int,
    text: str,
) -> bool:
    """Edit message to remove inline keyboard and show outcome text."""
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    try:
        resp = http_client.post(url, json=payload)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram editMessageText failed with %s", exc.__class__.__name__)
        return False


def handle_update(
    db: Session,
    update: Dict[str, Any],
    http_client: httpx.Client,
    clock: Callable[[], datetime] = utc_now,
    token: Optional[str] = None,
) -> Optional[str]:
    """
    Core deterministic update processor.
    Handles message (/start) and callback_query updates with strict authorization.
    Can be tested synchronously without running threads.
    Returns the fixed outcome text or None.
    """
    if token is None:
        raw_token = settings.TELEGRAM_BOT_TOKEN
        token = raw_token.get_secret_value() if raw_token else ""

    # 1. Handle message updates (/start)
    message = update.get("message")
    if message:
        sender_chat = message.get("chat", {})
        sender_chat_id = str(sender_chat.get("id")) if sender_chat.get("id") is not None else None
        text = message.get("text", "").strip()
        if not sender_chat_id:
            return None

        if text == "/start":
            is_patient = (
                db.query(Patient)
                .filter(Patient.telegram_chat_id.isnot(None), Patient.telegram_chat_id == sender_chat_id)
                .first()
                is not None
            )
            is_caregiver = (
                db.query(Caregiver)
                .filter(Caregiver.telegram_chat_id.isnot(None), Caregiver.telegram_chat_id == sender_chat_id)
                .first()
                is not None
            )
            if not (is_patient or is_caregiver):
                reply_text = (
                    f"Your Telegram chat ID is: {sender_chat_id}\n"
                    "Please ask your caregiver to link this chat ID to your MedMate profile."
                )
                _post_send_message(http_client, token, sender_chat_id, reply_text)
                return "start_unlinked"
            else:
                reply_text = "MedMate is active and linked to your profile."
                _post_send_message(http_client, token, sender_chat_id, reply_text)
                return "start_linked"
        return None

    # 2. Handle callback_query updates
    cbq = update.get("callback_query")
    if not cbq:
        return None

    query_id = cbq.get("id")
    from_user = cbq.get("from", {})
    chat = cbq.get("message", {}).get("chat", {}) or from_user
    sender_chat_id = str(chat.get("id")) if chat.get("id") is not None else None
    msg = cbq.get("message", {})
    msg_id = msg.get("message_id")
    orig_text = msg.get("text", "")
    data = cbq.get("data")

    # Validate callback format: strictly r"^(taken|snooze):(\d+)$"
    if not isinstance(data, str):
        return None
    match = re.fullmatch(r"(taken|snooze):(\d+)", data)
    if not match:
        # Ignore untrusted format, never log data
        return None

    action, dose_id_str = match.groups()
    dose_id = int(dose_id_str)

    dose = db.get(DoseEvent, dose_id)
    if not dose:
        outcome = "Dose not found"
        logger.info("Handled update: action=%s, dose_id=%d, outcome=dose_not_found", action, dose_id)
        if query_id:
            _post_answer_callback(http_client, token, query_id, outcome)
        return outcome

    patient = dose.schedule.medication.patient
    patient_chat_id = patient.telegram_chat_id

    # Ownership check: honor callback ONLY if patient has non-null telegram_chat_id
    # AND it equals sender's chat ID (compared as strings). Never let None match None.
    if not patient_chat_id or not sender_chat_id or str(patient_chat_id).strip() != str(sender_chat_id).strip():
        # Discard callback from unlinked sender or other patients
        return None

    outcome = "Something went wrong, please try again"
    outcome_code = "unexpected_error"

    if action == "taken":
        try:
            dose_service.confirm_dose_taken(
                db=db,
                dose_id=dose_id,
                source=DoseSource.PHONE,
                is_caregiver=False,
                clock=clock,
            )
            outcome = "Marked as taken"
            outcome_code = "marked_taken"
        except HTTPException as exc:
            code = getattr(exc, "code", None)
            if code == "too_early":
                outcome = "Too early"
                outcome_code = "too_early"
            elif code == "window_closed":
                outcome = "Window closed, please contact your caregiver"
                outcome_code = "window_closed"
            elif code == "double_dose":
                outcome = (
                    "Not recorded: a dose of this medicine was confirmed recently. "
                    "If you have not taken this one, please contact your caregiver."
                )
                outcome_code = "double_dose"
            elif code == "already_recorded":
                outcome = "Already recorded"
                outcome_code = "already_recorded"
            else:
                outcome = "Something went wrong, please try again"
                outcome_code = "unknown_error"
        except Exception:
            outcome = "Something went wrong, please try again"
            outcome_code = "unexpected_error"

    elif action == "snooze":
        try:
            dose_service.snooze_dose(
                db=db,
                dose_id=dose_id,
                minutes=10,
                clock=clock,
            )
            outcome = "Snoozed 10 minutes"
            outcome_code = "snoozed_10m"
        except HTTPException as exc:
            code = getattr(exc, "code", None)
            if code == "not_found":
                outcome = "Dose not found"
                outcome_code = "dose_not_found"
            else:
                outcome = "Something went wrong, please try again"
                outcome_code = "unknown_error"
        except Exception:
            outcome = "Something went wrong, please try again"
            outcome_code = "unexpected_error"

    logger.info("Handled update: action=%s, dose_id=%d, outcome=%s", action, dose_id, outcome_code)

    # Answer callback query popup
    if query_id:
        _post_answer_callback(http_client, token, query_id, outcome)

    # Edit message to remove buttons and show outcome text
    if sender_chat_id and msg_id:
        new_text = f"{orig_text}\n\nOutcome: {outcome}" if orig_text else outcome
        try:
            _post_edit_message_text(http_client, token, sender_chat_id, msg_id, new_text)
        except Exception:
            # Quietly ignore message edit failure
            pass

    return outcome


class TelegramPoller(threading.Thread):
    """
    Background worker thread using long-polling getUpdates.
    """

    def __init__(
        self,
        token: str,
        poll_timeout: float = 5.0,
        client: Optional[httpx.Client] = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        super().__init__(name="TelegramPoller", daemon=True)
        self.token = token
        self.poll_timeout = poll_timeout
        self.client = client if client is not None else httpx.Client(timeout=self.poll_timeout + 5.0)
        self.clock = clock
        self.stop_event = threading.Event()

    def run(self) -> None:
        backoff = 1.0
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"

        while not self.stop_event.is_set():
            db = SessionLocal()
            try:
                last_id = get_last_update_id(db)
                params: Dict[str, Any] = {
                    "timeout": int(self.poll_timeout),
                    "allowed_updates": ["callback_query", "message"],
                }
                if last_id is not None:
                    params["offset"] = last_id + 1

                resp = self.client.get(url, params=params)
                code = resp.status_code

                if code == 200:
                    backoff = 1.0
                    data = resp.json()
                    updates: List[Dict[str, Any]] = data.get("result", [])
                    for item in updates:
                        if self.stop_event.is_set():
                            break
                        up_id = item.get("update_id")
                        handle_update(
                            db=db,
                            update=item,
                            http_client=self.client,
                            clock=self.clock,
                            token=self.token,
                        )
                        # Persist last_update_id after update is handled
                        if up_id is not None:
                            save_last_update_id(db, up_id)
                elif code == 409:
                    logger.warning("Telegram getUpdates returned status code 409")
                    self.stop_event.wait(backoff)
                    backoff = min(60.0, backoff * 2)
                else:
                    logger.warning("Telegram getUpdates returned status code %d", code)
                    self.stop_event.wait(backoff)
                    backoff = min(60.0, backoff * 2)
            except Exception as exc:
                logger.warning("Telegram getUpdates failed with %s", exc.__class__.__name__)
                self.stop_event.wait(backoff)
                backoff = min(60.0, backoff * 2)
            finally:
                db.close()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal thread to stop and wait for termination."""
        self.stop_event.set()
        try:
            self.client.close()
        except Exception:
            pass
        self.join(timeout=timeout)


_global_poller: Optional[TelegramPoller] = None


def start_poller() -> Optional[TelegramPoller]:
    """Start background Telegram poller thread if enabled."""
    global _global_poller

    channel = getattr(settings, "NOTIFIER_CHANNEL", "mock").lower()
    enabled = getattr(settings, "TELEGRAM_POLL_ENABLED", False)

    if channel != "telegram" or not enabled:
        return None

    raw_token = settings.TELEGRAM_BOT_TOKEN
    token = raw_token.get_secret_value() if raw_token else None
    if not token or not token.strip():
        return None

    if _global_poller is None or not _global_poller.is_alive():
        _global_poller = TelegramPoller(token=token.strip())
        _global_poller.start()
        logger.info("Telegram poller thread started")

    return _global_poller


def stop_poller(timeout: float = 5.0) -> None:
    """Stop background Telegram poller thread."""
    global _global_poller
    if _global_poller is not None and _global_poller.is_alive():
        logger.info("Stopping Telegram poller thread...")
        _global_poller.stop(timeout=timeout)
        _global_poller = None
        logger.info("Telegram poller thread stopped")
