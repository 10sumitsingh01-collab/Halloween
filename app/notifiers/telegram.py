"""
app/notifiers/telegram.py

Synchronous Telegram Bot notifier for MedMate:
- Outbound patient reminders with inline action buttons (Taken / Snooze 10 min).
- Outbound caregiver escalation alerts.
- Synchronous httpx client with 10 second timeout.
- Strict privacy: suppresses httpx/httpcore loggers, never logs tokens, URLs,
  chat IDs, or raw exception strings (only logs exception class names).
"""
import logging
from typing import Any, Dict, Optional
import httpx

from app.config import settings
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.dose import DoseEvent
from app.notifiers.base import BaseNotifier, PermanentNotifierError
from app.notifiers.templates import format_patient_reminder

logger = logging.getLogger(__name__)

# Strict privacy: httpx/httpcore log URLs containing the bot token by default at INFO/DEBUG
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class TelegramNotifier(BaseNotifier):
    """
    Real Telegram Bot API notifier.
    """
    channel: str = "telegram"
    contact_attr: str = "telegram_chat_id"

    def __init__(
        self,
        token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout: float = 10.0,
    ):
        if token is None:
            raw_token = settings.TELEGRAM_BOT_TOKEN
            token = raw_token.get_secret_value() if raw_token is not None else None

        if not token or not token.strip():
            raise ValueError("TELEGRAM_BOT_TOKEN is required when NOTIFIER_CHANNEL is telegram")

        self._token = token.strip()
        self.timeout = timeout
        self.client = client if client is not None else httpx.Client(timeout=self.timeout)

    def send_patient_reminder(self, patient: Patient, dose_event: DoseEvent) -> bool:
        """
        Send a reminder with inline buttons:
        - "Taken" -> callback_data="taken:<dose_id>"
        - "Snooze 10 min" -> callback_data="snooze:<dose_id>"
        """
        chat_id = getattr(patient, "telegram_chat_id", None)
        if not chat_id:
            return False

        text = format_patient_reminder(patient, dose_event)
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "Taken", "callback_data": f"taken:{dose_event.id}"},
                        {"text": "Snooze 10 min", "callback_data": f"snooze:{dose_event.id}"},
                    ]
                ]
            },
        }
        return self._send_request(payload)

    def send_caregiver_alert(self, caregiver: Caregiver, message: str) -> bool:
        """
        Send an escalation alert to a caregiver without buttons.
        """
        chat_id = getattr(caregiver, "telegram_chat_id", None)
        if not chat_id:
            return False

        payload = {
            "chat_id": chat_id,
            "text": message,
        }
        return self._send_request(payload)

    def send_message(self, chat_id: str, text: str) -> bool:
        """
        Send a plain text message to a specific chat ID (helper for scripts/tests).
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        return self._send_request(payload)

    def _send_request(self, payload: Dict[str, Any]) -> bool:
        """
        POST to Telegram sendMessage endpoint.
        Checks response status_code directly:
        - 200 -> True
        - 400 or 403 -> raise PermanentNotifierError("recipient unreachable")
        - 429, 5xx, or network exception -> False (engine retries)
        Never logs or stores URLs or exception text containing the token.
        """
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = self.client.post(url, json=payload)
            status = resp.status_code
            if status == 200:
                return True
            if status in (400, 403):
                raise PermanentNotifierError("recipient unreachable")
            # 429, 5xx, or other HTTP errors return False
            return False
        except PermanentNotifierError:
            raise
        except Exception as exc:
            # Privacy: Log only exception class name, never raw exception string or exception object
            logger.warning("Telegram send failed with %s", exc.__class__.__name__)
            return False
