"""
tests/test_escalation.py

Deterministic reminder delivery, snooze resume, and escalation tests for MedMate.
Covers:
Stage 4b Part 1:
- nothing sent one second before reminder_time; sent at reminder_time
- second reminder after REMINDER_REPEAT_MINUTES; none past the cap
- no second reminder if the first failed
- tick twice sends nothing twice (idempotency)
- failure retried: attempts 1, 2, 3, then status failed and never again;
  verified with a file-based temporary database and fresh sessions to prove
  attempts survive restarts
- patient with no whatsapp_id: log step as failed with detail, no retries
- doses older than lookback trigger zero messages
- terminal dose (taken, skipped): nothing sent and acknowledged_at set
- dose with inactive medication sends nothing
- dose confirmed late sets acknowledged_at

Stage 4b Part 2:
- caregiver alert not sent one second before due_at + 20 min, sent at it
- each notify_level: all gets both steps, missed_only gets only the missed
  alert, urgent_only gets nothing
- two caregivers: one failing or missing a number does not stop the other
- no CAREGIVER_UNCONFIRMED once the dose is missed; only MISSED_ALERT
- dose taken before the caregiver time: no caregiver message at all
- snooze: no patient message during snooze, one SNOOZE_RESUME after it, and
  no second one on the next tick; caregiver step still fires during snooze
- SNOOZE_RESUME still sent when the normal reminder cap was reached
- spam guard: snooze that ends at the same minute REMINDER_2 would be due
  produces exactly one patient message, not two
- missed alert sent once, not on every tick
"""
from datetime import datetime, date, time, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.patient import Patient, PatientPreference, PatientCaregiver
from app.models.caregiver import Caregiver
from app.models.medication import Medication, Schedule
from app.models.dose import DoseEvent, EscalationLog
from app.models.enums import DoseStatus, EscalationStatus, NotifyLevel
from app.notifiers.mock import MockNotifier
from app.services.escalation import process_tick
from app.config import settings

FROZEN_NOW = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)


def _setup_patient_and_dose(
    db,
    due_at=FROZEN_NOW,
    whatsapp_id="+919876543210",
    med_active=True,
    status=DoseStatus.PENDING,
    reminder_offset=0,
):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=time(6, 0),
        sleep_time=time(22, 0),
        whatsapp_id=whatsapp_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    m = Medication(
        patient_id=p.id,
        name="Metformin",
        strength="500mg",
        form="tablet",
        instructions="After food",
        active=med_active,
        stock_remaining=30.0,
        refill_threshold=5,
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    s = Schedule(
        medication_id=m.id,
        time_of_day=time(8, 0),
        days_of_week="daily",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    if reminder_offset != 0:
        pref = PatientPreference(
            patient_id=p.id,
            medication_id=m.id,
            reminder_offset_minutes=reminder_offset,
        )
        db.add(pref)
        db.commit()

    dose = DoseEvent(
        schedule_id=s.id,
        due_at=due_at,
        status=status,
    )
    db.add(dose)
    db.commit()
    db.refresh(dose)

    return p, m, s, dose


def _setup_caregiver(
    db,
    patient,
    name="Priya",
    phone="+919876543211",
    whatsapp_id="+919876543211",
    notify_level=NotifyLevel.ALL,
    relationship="Daughter",
):
    c = Caregiver(
        name=name,
        phone=phone,
        whatsapp_id=whatsapp_id if whatsapp_id is not None else "",
        relationship=relationship,
        notify_level=notify_level,
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    # Link through relationship and patient_caregivers secondary table
    patient.caregivers.append(c)
    db.commit()
    db.refresh(patient)
    return c


class TestEscalationPatientLadder:
    """Tests the deterministic patient reminder escalation ladder (Part 1 updated to string steps)."""

    def test_nothing_sent_one_second_before_reminder_time_and_sent_at_reminder_time(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session,
            due_at=FROZEN_NOW,
            reminder_offset=-5,
        )
        reminder_due = FROZEN_NOW - timedelta(minutes=5)

        # 1. One second before reminder time (07:54:59)
        clock_before = lambda: reminder_due - timedelta(seconds=1)
        res_before = process_tick(db_session, clock=clock_before, notifier=notifier)
        assert res_before["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 0

        log_before = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id)
            .first()
        )
        assert log_before is None

        # 2. Exactly at reminder time (07:55:00)
        clock_at = lambda: reminder_due
        res_at = process_tick(db_session, clock=clock_at, notifier=notifier)
        assert res_at["sent"] == 1
        assert len(notifier.sent_patient_reminders) == 1

        db_session.expire_all()
        log_at = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_1")
            .first()
        )
        assert log_at is not None
        assert log_at.status == EscalationStatus.SENT
        assert log_at.recipient == "patient"
        assert log_at.attempts == 0

    def test_second_reminder_after_repeat_minutes_and_none_past_cap(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Step 1 sent at due_at (08:00:00)
        clock_step1 = lambda: FROZEN_NOW
        res1 = process_tick(db_session, clock=clock_step1, notifier=notifier)
        assert res1["sent"] == 1
        assert len(notifier.sent_patient_reminders) == 1

        # At +5 minutes (08:05:00): REMINDER_REPEAT_MINUTES=10 has not elapsed
        clock_mid = lambda: FROZEN_NOW + timedelta(minutes=5)
        res_mid = process_tick(db_session, clock=clock_mid, notifier=notifier)
        assert res_mid["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 1

        # At +10 minutes (08:10:00): Second reminder sent
        clock_step2 = lambda: FROZEN_NOW + timedelta(minutes=10)
        res2 = process_tick(db_session, clock=clock_step2, notifier=notifier)
        assert res2["sent"] == 1
        assert len(notifier.sent_patient_reminders) == 2

        db_session.expire_all()
        log_step2 = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_2")
            .first()
        )
        assert log_step2 is not None
        assert log_step2.status == EscalationStatus.SENT

        # At +20 minutes (08:20:00): MAX_PATIENT_REMINDERS=2 cap reached; none sent
        clock_step3 = lambda: FROZEN_NOW + timedelta(minutes=20)
        res3 = process_tick(db_session, clock=clock_step3, notifier=notifier)
        assert res3["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 2

        log_step3 = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_3")
            .first()
        )
        assert log_step3 is None

    def test_no_second_reminder_if_first_failed(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        notifier.fail_patient_reminders = True

        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Step 1 attempted and fails at 08:00:00
        clock_step1 = lambda: FROZEN_NOW
        res1 = process_tick(db_session, clock=clock_step1, notifier=notifier)
        assert res1["failed"] == 1
        assert len(notifier.sent_patient_reminders) == 0

        # At +10 minutes (08:10:00): notifier restored to working order
        notifier.fail_patient_reminders = False
        clock_step2 = lambda: FROZEN_NOW + timedelta(minutes=10)

        # Next tick should retry Step 1, but MUST NOT send Step 2 yet
        res2 = process_tick(db_session, clock=clock_step2, notifier=notifier)
        assert res2["sent"] == 1
        assert len(notifier.sent_patient_reminders) == 1

        db_session.expire_all()
        step1_log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_1")
            .first()
        )
        assert step1_log.status == EscalationStatus.SENT

        step2_log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_2")
            .first()
        )
        assert step2_log is None

    def test_tick_twice_sends_nothing_twice(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        clock = lambda: FROZEN_NOW
        res1 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res1["sent"] == 1
        assert len(notifier.sent_patient_reminders) == 1

        res2 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res2["sent"] == 0
        assert res2["skipped"] == 1
        assert len(notifier.sent_patient_reminders) == 1

    def test_failure_retried_up_to_3_attempts_then_permanently_failed_survives_restart(self, tmp_path):
        db_path = tmp_path / "escalation_restart_test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)

        notifier = MockNotifier(print_to_console=False)
        notifier.fail_patient_reminders = True
        clock = lambda: FROZEN_NOW

        # Session 1: Setup data and first failed attempt
        with SessionLocal() as db1:
            p, m, s, dose = _setup_patient_and_dose(db1, due_at=FROZEN_NOW)
            dose_id = dose.id
            res1 = process_tick(db1, clock=clock, notifier=notifier)
            assert res1["failed"] == 1

            log1 = (
                db1.query(EscalationLog)
                .filter(EscalationLog.dose_event_id == dose_id, EscalationLog.step == "REMINDER_1")
                .first()
            )
            assert log1.attempts == 1
            assert log1.status == EscalationStatus.PENDING

        # Session 2: Fresh session, attempt 2 fails
        with SessionLocal() as db2:
            res2 = process_tick(db2, clock=clock, notifier=notifier)
            assert res2["failed"] == 1

            log2 = (
                db2.query(EscalationLog)
                .filter(EscalationLog.dose_event_id == dose_id, EscalationLog.step == "REMINDER_1")
                .first()
            )
            assert log2.attempts == 2
            assert log2.status == EscalationStatus.PENDING

        # Session 3: Fresh session, attempt 3 fails -> status becomes FAILED
        with SessionLocal() as db3:
            res3 = process_tick(db3, clock=clock, notifier=notifier)
            assert res3["failed"] == 1

            log3 = (
                db3.query(EscalationLog)
                .filter(EscalationLog.dose_event_id == dose_id, EscalationLog.step == "REMINDER_1")
                .first()
            )
            assert log3.attempts == 3
            assert log3.status == EscalationStatus.FAILED

        # Session 4: Fresh session, notifier fixed. Permanently skipped and never retried.
        notifier.fail_patient_reminders = False
        with SessionLocal() as db4:
            res4 = process_tick(db4, clock=clock, notifier=notifier)
            assert res4["sent"] == 0
            assert res4["skipped"] == 1

            log4 = (
                db4.query(EscalationLog)
                .filter(EscalationLog.dose_event_id == dose_id, EscalationLog.step == "REMINDER_1")
                .first()
            )
            assert log4.attempts == 3
            assert log4.status == EscalationStatus.FAILED

        assert len(notifier.sent_patient_reminders) == 0

    def test_patient_without_whatsapp_id_fails_immediately_no_retries(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session,
            due_at=FROZEN_NOW,
            whatsapp_id=None,
        )

        clock = lambda: FROZEN_NOW
        res1 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res1["failed"] == 1
        assert len(notifier.sent_patient_reminders) == 0

        db_session.expire_all()
        log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_1")
            .first()
        )
        assert log is not None
        assert log.status == EscalationStatus.FAILED
        assert log.detail == "patient has no whatsapp_id"

        # Subsequent tick skips it and does not retry
        res2 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res2["sent"] == 0
        assert res2["skipped"] == 1

    def test_doses_older_than_lookback_trigger_zero_messages(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        old_due_at = FROZEN_NOW - timedelta(hours=25)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=old_due_at)

        clock = lambda: FROZEN_NOW
        res = process_tick(db_session, clock=clock, notifier=notifier)
        assert res["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 0

        logs = db_session.query(EscalationLog).filter(EscalationLog.dose_event_id == dose.id).all()
        assert len(logs) == 0

    def test_terminal_dose_stops_ladder_and_sets_acknowledged_at(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session,
            due_at=FROZEN_NOW,
            status=DoseStatus.PENDING,
        )

        clock1 = lambda: FROZEN_NOW
        process_tick(db_session, clock=clock1, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1

        db_session.expire_all()
        log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_1")
            .first()
        )
        assert log.acknowledged_at is None

        # Dose confirmed taken
        dose.status = DoseStatus.TAKEN
        db_session.commit()

        # Next tick acknowledges log and sends nothing further
        now2 = FROZEN_NOW + timedelta(minutes=10)
        clock2 = lambda: now2
        res2 = process_tick(db_session, clock=clock2, notifier=notifier)
        assert res2["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 1

        db_session.expire_all()
        log_after = db_session.get(EscalationLog, log.id)
        assert log_after.acknowledged_at is not None

    def test_inactive_medication_sends_nothing(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session,
            due_at=FROZEN_NOW,
            med_active=False,
        )

        clock = lambda: FROZEN_NOW
        res = process_tick(db_session, clock=clock, notifier=notifier)
        assert res["sent"] == 0
        assert len(notifier.sent_patient_reminders) == 0

        logs = db_session.query(EscalationLog).filter(EscalationLog.dose_event_id == dose.id).all()
        assert len(logs) == 0

    def test_dose_confirmed_late_sets_acknowledged_at(self, db_session):
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session,
            due_at=FROZEN_NOW,
            status=DoseStatus.PENDING,
        )

        clock1 = lambda: FROZEN_NOW
        process_tick(db_session, clock=clock1, notifier=notifier)

        db_session.expire_all()
        log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_1")
            .first()
        )
        assert log.acknowledged_at is None

        dose.status = DoseStatus.LATE
        db_session.commit()

        now2 = FROZEN_NOW + timedelta(minutes=20)
        clock2 = lambda: now2
        process_tick(db_session, clock=clock2, notifier=notifier)

        db_session.expire_all()
        log_after = db_session.get(EscalationLog, log.id)
        assert log_after.acknowledged_at is not None


class TestEscalationCaregiverAndSnooze:
    """Stage 4b Part 2 tests: Caregiver escalation, missed alerts, and snooze resume."""

    def test_caregiver_alert_boundary_one_second_before_and_at_20_min(self, db_session):
        """Caregiver alert not sent one second before due_at + 20 min, sent at it."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        escalation_time = FROZEN_NOW + timedelta(minutes=settings.CAREGIVER_ESCALATION_MINUTES)

        # 1. One second before due_at + 20 min (08:19:59)
        clock_before = lambda: escalation_time - timedelta(seconds=1)
        res_before = process_tick(db_session, clock=clock_before, notifier=notifier)
        assert len(notifier.sent_caregiver_alerts) == 0

        log_before = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "CAREGIVER_UNCONFIRMED",
            )
            .first()
        )
        assert log_before is None

        # 2. Exactly at due_at + 20 min (08:20:00)
        clock_at = lambda: escalation_time
        res_at = process_tick(db_session, clock=clock_at, notifier=notifier)
        assert len(notifier.sent_caregiver_alerts) == 1
        alert = notifier.sent_caregiver_alerts[0]
        assert alert["caregiver_id"] == c.id
        assert "has not confirmed" in alert["message"]

        db_session.expire_all()
        log_at = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "CAREGIVER_UNCONFIRMED",
                EscalationLog.recipient == f"caregiver:{c.id}",
            )
            .first()
        )
        assert log_at is not None
        assert log_at.status == EscalationStatus.SENT

    def test_notify_levels_routing(self, db_session):
        """
        Each notify_level:
        - 'all' gets both CAREGIVER_UNCONFIRMED and MISSED_ALERT
        - 'missed_only' gets only MISSED_ALERT
        - 'urgent_only' gets neither in this layer
        """
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        c_all = _setup_caregiver(
            db_session, p, name="All", phone="+919876543201", whatsapp_id="+919876543201",
            notify_level=NotifyLevel.ALL
        )
        c_missed = _setup_caregiver(
            db_session, p, name="MissedOnly", phone="+919876543202", whatsapp_id="+919876543202",
            notify_level=NotifyLevel.MISSED_ONLY
        )
        c_urgent = _setup_caregiver(
            db_session, p, name="UrgentOnly", phone="+919876543203", whatsapp_id="+919876543203",
            notify_level=NotifyLevel.URGENT_ONLY
        )

        # 1. Dose is pending at +20 min (08:20:00): only c_all receives unconfirmed alert
        clock_20m = lambda: FROZEN_NOW + timedelta(minutes=20)
        process_tick(db_session, clock=clock_20m, notifier=notifier)

        recipients_20m = [a["caregiver_id"] for a in notifier.sent_caregiver_alerts]
        assert recipients_20m == [c_all.id]

        # 2. Dose transitions to MISSED at +121 min (10:01:00)
        # mark_overdue_as_missed marks the dose as missed during process_tick
        notifier.sent_caregiver_alerts.clear()
        clock_missed = lambda: FROZEN_NOW + timedelta(minutes=121)
        process_tick(db_session, clock=clock_missed, notifier=notifier)

        # Confirm dose status changed to missed
        db_session.expire_all()
        refreshed_dose = db_session.get(DoseEvent, dose.id)
        assert refreshed_dose.status == DoseStatus.MISSED

        # c_all and c_missed receive MISSED_ALERT; c_urgent receives nothing
        recipients_missed = sorted([a["caregiver_id"] for a in notifier.sent_caregiver_alerts])
        expected_recipients = sorted([c_all.id, c_missed.id])
        assert recipients_missed == expected_recipients

        # Verify escalation log entries
        all_logs = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id)
            .all()
        )
        recipients_and_steps = [(l.recipient, l.step) for l in all_logs]
        assert (f"caregiver:{c_all.id}", "CAREGIVER_UNCONFIRMED") in recipients_and_steps
        assert (f"caregiver:{c_all.id}", "MISSED_ALERT") in recipients_and_steps
        assert (f"caregiver:{c_missed.id}", "MISSED_ALERT") in recipients_and_steps
        assert not any(l.recipient == f"caregiver:{c_urgent.id}" for l in all_logs)

    def test_two_caregivers_fault_isolation(self, db_session):
        """Two caregivers: one failing or missing a number does not stop the other."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # c1 has no whatsapp_id
        c1 = _setup_caregiver(
            db_session, p, name="CaregiverNoWA", phone="+919876543201", whatsapp_id="",
            notify_level=NotifyLevel.ALL
        )
        # c2 has a valid whatsapp_id
        c2 = _setup_caregiver(
            db_session, p, name="CaregiverValidWA", phone="+919876543202", whatsapp_id="+919876543202",
            notify_level=NotifyLevel.ALL
        )

        clock = lambda: FROZEN_NOW + timedelta(minutes=20)
        res = process_tick(db_session, clock=clock, notifier=notifier)

        # c2 must have received the alert despite c1 failing
        assert len(notifier.sent_caregiver_alerts) == 1
        assert notifier.sent_caregiver_alerts[0]["caregiver_id"] == c2.id

        db_session.expire_all()
        log_c1 = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "CAREGIVER_UNCONFIRMED",
                EscalationLog.recipient == f"caregiver:{c1.id}",
            )
            .first()
        )
        assert log_c1 is not None
        assert log_c1.status == EscalationStatus.FAILED
        assert log_c1.detail == "caregiver has no whatsapp_id"
        assert log_c1.attempts == 1

        log_c2 = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "CAREGIVER_UNCONFIRMED",
                EscalationLog.recipient == f"caregiver:{c2.id}",
            )
            .first()
        )
        assert log_c2 is not None
        assert log_c2.status == EscalationStatus.SENT

        # Next tick skips both (c1 has failed with no retries, c2 was sent)
        notifier.sent_caregiver_alerts.clear()
        res2 = process_tick(db_session, clock=clock, notifier=notifier)
        assert len(notifier.sent_caregiver_alerts) == 0

    def test_no_caregiver_unconfirmed_once_dose_is_missed(self, db_session):
        """No CAREGIVER_UNCONFIRMED once the dose is missed; only MISSED_ALERT."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session, due_at=FROZEN_NOW, status=DoseStatus.MISSED
        )
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        clock = lambda: FROZEN_NOW + timedelta(minutes=130)
        res = process_tick(db_session, clock=clock, notifier=notifier)

        assert len(notifier.sent_caregiver_alerts) == 1
        assert "Dose missed" in notifier.sent_caregiver_alerts[0]["message"]

        db_session.expire_all()
        unconfirmed_log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "CAREGIVER_UNCONFIRMED",
            )
            .first()
        )
        assert unconfirmed_log is None

        missed_log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == "MISSED_ALERT",
            )
            .first()
        )
        assert missed_log is not None
        assert missed_log.status == EscalationStatus.SENT

    def test_dose_taken_before_caregiver_time_sends_no_caregiver_message(self, db_session):
        """Dose taken before the caregiver time: no caregiver message at all."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        # Step 1 patient reminder at 08:00
        clock_0800 = lambda: FROZEN_NOW
        process_tick(db_session, clock=clock_0800, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1

        # Dose marked TAKEN at 08:10
        dose.status = DoseStatus.TAKEN
        db_session.commit()

        # At 08:20 (caregiver unconfirmed escalation time)
        clock_0820 = lambda: FROZEN_NOW + timedelta(minutes=20)
        res_0820 = process_tick(db_session, clock=clock_0820, notifier=notifier)

        assert len(notifier.sent_caregiver_alerts) == 0
        caregiver_logs = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.recipient == f"caregiver:{c.id}",
            )
            .all()
        )
        assert len(caregiver_logs) == 0

    def test_snooze_ladder_behavior_and_caregiver_step_still_fires(self, db_session):
        """
        Snooze:
        - no patient message during snooze
        - caregiver unconfirmed STILL fires during snooze at due_at + 20 min
        - after snooze ends: ONE SNOOZE_RESUME_<utc iso> sent
        - no second snooze resume on next tick
        """
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        # Step 1 sent at 08:00
        clock_0800 = lambda: FROZEN_NOW
        process_tick(db_session, clock=clock_0800, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1

        # Dose snoozed at 08:05 until 08:25 (20 minutes snooze)
        snooze_until_time = FROZEN_NOW + timedelta(minutes=25)
        dose.snoozed_until = snooze_until_time
        db_session.commit()

        # At 08:15 (during snooze): patient ladder sends nothing
        clock_0815 = lambda: FROZEN_NOW + timedelta(minutes=15)
        process_tick(db_session, clock=clock_0815, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1
        assert len(notifier.sent_caregiver_alerts) == 0

        # At 08:20 (still snoozed, but due_at + 20 min):
        # Caregiver step STILL fires during snooze!
        clock_0820 = lambda: FROZEN_NOW + timedelta(minutes=20)
        process_tick(db_session, clock=clock_0820, notifier=notifier)
        assert len(notifier.sent_caregiver_alerts) == 1
        assert len(notifier.sent_patient_reminders) == 1  # patient still paused

        # At 08:25 (snooze ends): ONE SNOOZE_RESUME reminder sent to patient
        clock_0825 = lambda: snooze_until_time
        process_tick(db_session, clock=clock_0825, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 2

        db_session.expire_all()
        expected_snooze_step = f"SNOOZE_RESUME_{snooze_until_time.strftime('%Y-%m-%dT%H:%M')}"
        snooze_log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == expected_snooze_step,
            )
            .first()
        )
        assert snooze_log is not None
        assert snooze_log.status == EscalationStatus.SENT

        # At 08:26 (next tick): no second snooze resume or duplicate sent
        clock_0826 = lambda: snooze_until_time + timedelta(minutes=1)
        res_0826 = process_tick(db_session, clock=clock_0826, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 2
        assert len(notifier.sent_caregiver_alerts) == 1

    def test_snooze_resume_sent_even_when_normal_cap_was_reached(self, db_session):
        """SNOOZE_RESUME still sent even if normal reminder cap (MAX_PATIENT_REMINDERS=2) was reached."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Step 1 at 08:00
        process_tick(db_session, clock=lambda: FROZEN_NOW, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1

        # Step 2 at 08:10 (cap of 2 reached)
        process_tick(db_session, clock=lambda: FROZEN_NOW + timedelta(minutes=10), notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 2

        # Dose snoozed at 08:15 until 08:30
        snooze_time = FROZEN_NOW + timedelta(minutes=30)
        dose.snoozed_until = snooze_time
        db_session.commit()

        # At 08:30 (snooze ends): SNOOZE_RESUME is sent despite normal cap being reached
        clock_0830 = lambda: snooze_time
        res_0830 = process_tick(db_session, clock=clock_0830, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 3

        db_session.expire_all()
        snooze_step = f"SNOOZE_RESUME_{snooze_time.strftime('%Y-%m-%dT%H:%M')}"
        log = (
            db_session.query(EscalationLog)
            .filter(
                EscalationLog.dose_event_id == dose.id,
                EscalationLog.step == snooze_step,
            )
            .first()
        )
        assert log is not None
        assert log.status == EscalationStatus.SENT

    def test_spam_guard_snooze_ending_when_reminder_2_due_produces_one_message(self, db_session):
        """
        Spam guard: the normal reminder ladder must not send a patient message
        within REMINDER_REPEAT_MINUTES of the most recent patient message that
        was actually sent (including SNOOZE_RESUME).
        A snooze that ends at the same minute REMINDER_2 would be due produces
        exactly one patient message, not two.
        """
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(db_session, due_at=FROZEN_NOW)

        # Step 1 sent at 08:00:00
        process_tick(db_session, clock=lambda: FROZEN_NOW, notifier=notifier)
        assert len(notifier.sent_patient_reminders) == 1

        # At 08:05:00: patient snoozes until 08:10:00 (which is exactly when REMINDER_2 would be due)
        snooze_target = FROZEN_NOW + timedelta(minutes=10)
        dose.snoozed_until = snooze_target
        db_session.commit()

        # At 08:10:00: both snooze ends and REMINDER_2 would be eligible
        clock_0810 = lambda: snooze_target
        res = process_tick(db_session, clock=clock_0810, notifier=notifier)

        # Exactly ONE patient message must be sent (SNOOZE_RESUME), not two!
        assert len(notifier.sent_patient_reminders) == 2  # Total: Step 1 + Snooze Resume

        db_session.expire_all()
        snooze_step = f"SNOOZE_RESUME_{snooze_target.strftime('%Y-%m-%dT%H:%M')}"
        snooze_log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == snooze_step)
            .first()
        )
        assert snooze_log is not None
        assert snooze_log.status == EscalationStatus.SENT

        reminder2_log = (
            db_session.query(EscalationLog)
            .filter(EscalationLog.dose_event_id == dose.id, EscalationLog.step == "REMINDER_2")
            .first()
        )
        assert reminder2_log is None  # REMINDER_2 was NOT sent concurrently due to spam guard

    def test_missed_alert_sent_once_not_on_every_tick(self, db_session):
        """Missed alert sent once, not on every tick."""
        notifier = MockNotifier(print_to_console=False)
        p, m, s, dose = _setup_patient_and_dose(
            db_session, due_at=FROZEN_NOW, status=DoseStatus.MISSED
        )
        c = _setup_caregiver(db_session, p, notify_level=NotifyLevel.ALL)

        clock = lambda: FROZEN_NOW + timedelta(minutes=130)

        # Tick 1: sends missed alert
        res1 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res1["sent"] == 1
        assert len(notifier.sent_caregiver_alerts) == 1

        # Tick 2 at same or later time: skips
        res2 = process_tick(db_session, clock=clock, notifier=notifier)
        assert res2["sent"] == 0
        assert res2["skipped"] == 1
        assert len(notifier.sent_caregiver_alerts) == 1
