"""
app/notifiers package.
"""
from app.notifiers.base import BaseNotifier
from app.notifiers.mock import MockNotifier

_shared_mock_notifier = MockNotifier(print_to_console=False)


def get_notifier() -> BaseNotifier:
    """
    FastAPI dependency returning the active notifier.
    Defaults to the shared MockNotifier instance until Layer 5.
    Can be overridden in tests via app.dependency_overrides[get_notifier].
    """
    return _shared_mock_notifier


__all__ = ["BaseNotifier", "MockNotifier", "get_notifier"]
