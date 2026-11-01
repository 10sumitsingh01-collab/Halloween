#!/usr/bin/env python3
"""
scripts/link_telegram.py

Sets telegram_chat_id directly in the SQLite database for a Patient or Caregiver.
Can link a specific chat ID, or retrieve the most recent private chat via getUpdates (--from-latest).

Usage:
    python scripts/link_telegram.py --patient 1 --chat-id <id>
    python scripts/link_telegram.py --caregiver 2 --chat-id <id>
    python scripts/link_telegram.py --patient 1 --from-latest
    python scripts/link_telegram.py --caregiver 2 --from-latest

Privacy: Never prints the chat ID in full (masks all but last 3 digits).
Note: Should be run with the server stopped.
"""
import argparse
import os
import sys
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.patient import Patient
from app.models.caregiver import Caregiver


def mask_chat_id(chat_id: str) -> str:
    """Mask all but the last 3 digits of a chat ID."""
    clean = str(chat_id).strip()
    if len(clean) <= 3:
        return "***"
    return f"{'*' * (len(clean) - 3)}{clean[-3:]}"


def get_latest_chat_id(token: str) -> Optional[str]:
    """Call getUpdates once to find the latest private chat ID."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params={"limit": 20})
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get("ok"):
                return None

            updates = data.get("result", [])
            for item in reversed(updates):
                msg = item.get("message") or item.get("channel_post") or item.get("callback_query", {}).get("message")
                if msg:
                    chat = msg.get("chat", {})
                    if chat.get("type") == "private" and "id" in chat:
                        return str(chat["id"])
            return None
    except Exception as exc:
        print(f"FAILED: {exc.__class__.__name__}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Link Telegram chat ID to Patient or Caregiver.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--patient", type=int, help="Patient ID to link")
    target_group.add_argument("--caregiver", type=int, help="Caregiver ID to link")

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--chat-id", type=str, help="Explicit Telegram chat ID to link")
    source_group.add_argument("--from-latest", action="store_true", help="Fetch most recent private chat from getUpdates")

    args = parser.parse_args()

    chat_id: Optional[str] = None
    if args.from_latest:
        raw_token = settings.TELEGRAM_BOT_TOKEN
        token = raw_token.get_secret_value() if raw_token is not None else None
        if not token or not token.strip():
            print("FAILED: TELEGRAM_BOT_TOKEN not configured")
            sys.exit(1)
        chat_id = get_latest_chat_id(token.strip())
        if not chat_id:
            print("FAILED: No recent private chat messages found")
            sys.exit(1)
    else:
        chat_id = args.chat_id.strip()

    if not chat_id:
        print("FAILED: Chat ID cannot be empty")
        sys.exit(1)

    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        masked = mask_chat_id(chat_id)
        if args.patient:
            patient = session.get(Patient, args.patient)
            if not patient:
                print(f"FAILED: PatientNotFound")
                sys.exit(1)
            patient.telegram_chat_id = chat_id
            session.commit()
            print(f"Linked Telegram chat {masked} to patient {patient.id}")
        elif args.caregiver:
            caregiver = session.get(Caregiver, args.caregiver)
            if not caregiver:
                print(f"FAILED: CaregiverNotFound")
                sys.exit(1)
            caregiver.telegram_chat_id = chat_id
            session.commit()
            print(f"Linked Telegram chat {masked} to caregiver {caregiver.id}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
