#!/usr/bin/env python3
"""
scripts/demo_dose.py

Creates a single DoseEvent for the seed patient's first active medication schedule,
scheduled to become due N seconds from now (default: 20 seconds).

Usage:
    python scripts/demo_dose.py [--seconds 20]
"""
import argparse
import os
import sys
from datetime import timedelta, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import SessionLocal
from app.models import Patient, Medication, Schedule, DoseEvent, DoseStatus
from app.services.clock import utc_now


def create_demo_dose(seconds: int = 20) -> DoseEvent:
    db = SessionLocal()
    try:
        # 1. Locate seed patient
        patient = db.query(Patient).filter(Patient.name == "Lakshmi Devi").first()
        if not patient:
            patient = db.query(Patient).first()
        if not patient:
            print("Error: No patient found in the database. Please run 'python scripts/seed.py' first.")
            sys.exit(1)

        # 2. Locate first active schedule
        sched = (
            db.query(Schedule)
            .join(Medication, Schedule.medication_id == Medication.id)
            .filter(Medication.patient_id == patient.id, Medication.active == True)
            .order_by(Schedule.id.asc())
            .first()
        )
        if not sched:
            print(f"Error: No active schedule found for patient '{patient.name}'.")
            sys.exit(1)

        # 3. Calculate target due_at
        due_at = (utc_now() + timedelta(seconds=seconds)).replace(microsecond=0)

        # Handle unique constraint (schedule_id, due_at) safely
        while (
            db.query(DoseEvent)
            .filter(DoseEvent.schedule_id == sched.id, DoseEvent.due_at == due_at)
            .first()
        ):
            due_at += timedelta(seconds=1)

        # 4. Create and commit dose event
        dose = DoseEvent(
            schedule_id=sched.id,
            due_at=due_at,
            status=DoseStatus.PENDING,
        )
        db.add(dose)
        db.commit()
        db.refresh(dose)

        med_name = sched.medication.name
        print("\n=== Demo DoseEvent Created Successfully ===")
        print(f"Dose ID        : {dose.id}")
        print(f"Patient        : {patient.name} (ID: {patient.id})")
        print(f"Medication     : {med_name} (Schedule ID: {sched.id})")
        print(f"Due At (UTC)   : {dose.due_at.isoformat()}")
        print(f"Due In         : ~{seconds} seconds from now")
        print(f"Status         : {dose.status.value}")
        print("\nTo inspect escalation logs in real-time:")
        print(f'  curl -H "X-API-Key: test_caregiver_key_123" http://localhost:8000/api/v1/doses/{dose.id}/escalations')
        print("\nTo confirm the dose (as patient):")
        print(f'  curl -X POST -H "X-API-Key: test_patient_key_456" http://localhost:8000/api/v1/doses/{dose.id}/taken')
        print("===========================================\n")
        return dose
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Create a demo DoseEvent for live testing.")
    parser.add_argument(
        "-s",
        "--seconds",
        type=int,
        default=20,
        help="Number of seconds from now when the dose becomes due (default: 20)",
    )
    args = parser.parse_args()
    create_demo_dose(seconds=args.seconds)


if __name__ == "__main__":
    main()
