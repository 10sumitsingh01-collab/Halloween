"""
app/notifiers/mock.py

In-memory mock notifier for automated testing and development without live SMS/WhatsApp gateways.
Records all outbound calls and prints messages to stdout.
"""
from typing import List, Dict, Any, Optional
from app.notifiers.base import BaseNotifier
from app.notifiers.templates import format_patient_reminder
from app.models.patient import Patient
from app.models.caregiver import Caregiver
from app.models.dose import DoseEvent


class MockNotifier(BaseNotifier):
    """
    Mock notifier that logs dispatched notifications in-memory and supports failure injection.
    """
    channel: str = "mock"
    contact_attr: str = "whatsapp_id"

    def __init__(self, print_to_console: bool = True):
        self.print_to_console = print_to_console
        self.sent_patient_reminders: List[Dict[str, Any]] = []
        self.sent_caregiver_alerts: List[Dict[str, Any]] = []
        self.fail_patient_reminders: bool = False
        self.fail_caregiver_alerts: bool = False
        self.fail_count: int = 0  # Number of upcoming sends to fail before succeeding

    def reset(self) -> None:
        """Clear recorded calls and failure flags."""
        self.sent_patient_reminders.clear()
        self.sent_caregiver_alerts.clear()
        self.fail_patient_reminders = False
        self.fail_caregiver_alerts = False
        self.fail_count = 0

    def send_patient_reminder(self, patient: Patient, dose_event: DoseEvent) -> bool:
        if self.fail_count > 0:
            self.fail_count -= 1
            if self.print_to_console:
                print(f"[MockNotifier:FAILURE-INJECTED] Failed sending reminder to patient={patient.id}")
            return False

        if self.fail_patient_reminders:
            if self.print_to_console:
                print(f"[MockNotifier:FAILURE-INJECTED] Failed sending reminder to patient={patient.id}")
            return False

        message = format_patient_reminder(patient, dose_event)
        record = {
            "patient_id": patient.id,
            "patient_name": patient.name,
            "dose_event_id": dose_event.id,
            "message": message,
        }
        self.sent_patient_reminders.append(record)
        if self.print_to_console:
            print(f"[MockNotifier:PATIENT] To {patient.name} ({patient.language}):\n{message}")
        return True

    def send_caregiver_alert(self, caregiver: Caregiver, message: str) -> bool:
        if self.fail_count > 0:
            self.fail_count -= 1
            if self.print_to_console:
                print(f"[MockNotifier:FAILURE-INJECTED] Failed sending alert to caregiver={caregiver.id}")
            return False

        if self.fail_caregiver_alerts:
            if self.print_to_console:
                print(f"[MockNotifier:FAILURE-INJECTED] Failed sending alert to caregiver={caregiver.id}")
            return False

        record = {
            "caregiver_id": caregiver.id,
            "caregiver_name": caregiver.name,
            "phone": caregiver.phone,
            "notify_level": getattr(caregiver, "notify_level", None),
            "message": message,
        }
        self.sent_caregiver_alerts.append(record)
        if self.print_to_console:
            print(f"[MockNotifier:CAREGIVER] To {caregiver.name} ({caregiver.phone}):\n{message}")
        return True
