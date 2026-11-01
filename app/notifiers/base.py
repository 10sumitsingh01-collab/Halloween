"""
app/notifiers/base.py

Abstract base class for notifiers delivering reminders and escalation alerts.
"""
from abc import ABC, abstractmethod
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.dose import DoseEvent


class PermanentNotifierError(Exception):
    """
    Raised when notification delivery fails permanently (e.g. 400/403: blocked bot, chat not found)
    and should not be retried by the escalation engine.
    """
    pass


class BaseNotifier(ABC):
    """
    Interface for outbound patient reminders and caregiver escalation alerts.
    Methods return True on success, or False / raise exception on failure.
    """
    channel: str = "base"
    contact_attr: str = "whatsapp_id"

    @abstractmethod
    def send_patient_reminder(self, patient: Patient, dose_event: DoseEvent) -> bool:
        """
        Deliver a reminder message to the patient about an upcoming or pending dose.
        """
        pass

    @abstractmethod
    def send_caregiver_alert(self, caregiver: Caregiver, message: str) -> bool:
        """
        Deliver an escalation alert message to a linked caregiver.
        """
        pass
