#!/usr/bin/env python3
"""
scripts/telegram_test.py

Sends one plain message "MedMate test message" to a patient's linked Telegram chat
using TelegramNotifier. Prints OK or FAILED with the exception CLASS NAME only.
Never prints the token, URL or full chat ID.

Usage:
    python scripts/telegram_test.py --patient <id>
"""
import argparse
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.patient import Patient
from app.notifiers.telegram import TelegramNotifier


def main():
    parser = argparse.ArgumentParser(description="Send a test Telegram message to a patient's linked chat.")
    parser.add_argument("--patient", type=int, required=True, help="Patient ID")
    args = parser.parse_args()

    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        patient = session.get(Patient, args.patient)
        if not patient:
            print("FAILED: PatientNotFound")
            sys.exit(1)

        chat_id = getattr(patient, "telegram_chat_id", None)
        if not chat_id:
            print("FAILED: NoTelegramChatId")
            sys.exit(1)

        try:
            notifier = TelegramNotifier()
            success = notifier.send_message(chat_id, "MedMate test message")
            if success:
                print("OK")
                sys.exit(0)
            else:
                print("FAILED: DeliveryFailed")
                sys.exit(1)
        except Exception as exc:
            print(f"FAILED: {exc.__class__.__name__}")
            sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
