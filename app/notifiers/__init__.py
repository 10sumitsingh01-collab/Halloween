"""
app/notifiers package.
"""
from typing import Optional
from app.config import settings
from app.notifiers.base import BaseNotifier, PermanentNotifierError
from app.notifiers.mock import MockNotifier
from app.notifiers.telegram import TelegramNotifier

_shared_mock_notifier = MockNotifier(print_to_console=False)
_shared_telegram_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> BaseNotifier:
    """
    FastAPI dependency returning the active notifier based on NOTIFIER_CHANNEL setting.
    - 'telegram': TelegramNotifier
    - 'mock' (default): MockNotifier
    Can be overridden in tests via app.dependency_overrides[get_notifier].
    """
    global _shared_telegram_notifier
    channel = getattr(settings, "NOTIFIER_CHANNEL", "mock").lower()
    if channel == "telegram":
        if _shared_telegram_notifier is None:
            _shared_telegram_notifier = TelegramNotifier()
        return _shared_telegram_notifier
    return _shared_mock_notifier


__all__ = [
    "BaseNotifier",
    "MockNotifier",
    "TelegramNotifier",
    "PermanentNotifierError",
    "get_notifier",
]
