"""
tests/test_dose_engine.py

Layer 3 deterministic engine tests.

File is split into three logical sections:
  (b)  Boundary timing tests and double-dose guard  ← this file, part 1
  (c)  Generation, missed-marking, refill, adherence, timezone  ← part 2

All times are frozen via make_client(fixed_now).
due_at is always set explicitly; nothing uses datetime.now().

Reference boundaries (from PROJECT CONTRACT + approval):
  ┌──────────────────────┬───────────────────────────────────┐
  │ Offset from due_at   │ Expected outcome                  │
  ├──────────────────────┼───────────────────────────────────┤
  │ -61 min              │ 409 Too early                     │
  │ -60 min              │ allowed → taken                   │
  │ -59 min              │ allowed → taken                   │
  │  0 (due_at)          │ taken                             │
  │ +15:00               │ taken                             │
  │ +15:01               │ late                              │
  │ +120:00              │ late                              │
  │ +120:01              │ patient → 409; caregiver → missed │
  ├──────────────────────┼───────────────────────────────────┤
  │ Double-dose guard    │                                   │
  │  previous +30 min    │ blocked → 409, stock unchanged    │
  │  previous +31 min    │ allowed → taken, stock decremented│
  └──────────────────────┴───────────────────────────────────┘
"""

from datetime import date, datetime, time, timedelta, timezone
import pytest
from fastapi import status

from app.models import (
    Patient,
    Medication,
    Schedule,
    DoseEvent,
    DoseStatus,
    DoseSource,
)
from app.services.dose_engine import reminder_time
from app.services.clock import ensure_utc
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers shared across tests in this file
# ---------------------------------------------------------------------------

DUE_AT = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)   # 8:00 AM UTC, a Monday
INITIAL_STOCK = 20


def _make_patient(db):
    p = Patient(
        name="Lakshmi Devi",
        age=72,
        timezone="Asia/Kolkata",
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_med(db, patient_id, stock=INITIAL_STOCK, threshold=5):
    m = Medication(
        patient_id=patient_id,
        name="Metformin",
        strength="500mg",
        form="tablet",
        stock_remaining=stock,
        refill_threshold=threshold,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_sched(db, med_id, dose_amount=1.0):
    s = Schedule(
        medication_id=med_id,
        time_of_day=time(8, 0),
        days_of_week="daily",
        start_date=date(2026, 1, 1),
        dose_amount=dose_amount,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_dose(db, sched_id, due_at=DUE_AT, stock_snapshot=None):
    d = DoseEvent(
        schedule_id=sched_id,
        due_at=due_at,
        status=DoseStatus.PENDING,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _patient_headers():
    from app.config import settings
    return {"X-API-Key": settings.PATIENT_API_KEY}


def _caregiver_headers():
    from app.config import settings
    return {"X-API-Key": settings.CAREGIVER_API_KEY}


def _get_stock(db, med_id):
    db.expire_all()
    m = db.query(Medication).filter(Medication.id == med_id).first()
    return m.stock_remaining


# ===========================================================================
# SECTION B-1: Timing boundary tests
# ===========================================================================

class TestTakenWindow:
    """Spec: taken at due_at and due_at + 15:00 → status=taken, stock decremented."""

    def test_taken_at_due_at(self, make_client, db_session):
        """now == due_at → TAKEN, stock decremented by dose_amount."""
        now = DUE_AT
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "taken"
        assert _get_stock(db_session, m.id) == 19   # 20 - 1

    def test_taken_at_plus_15m00s(self, make_client, db_session):
        """now == due_at + 15:00 (boundary inclusive) → TAKEN."""
        now = DUE_AT + timedelta(minutes=15)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "taken"
        assert _get_stock(db_session, m.id) == 19


class TestLateWindow:
    """Spec: due_at + 15:01 through due_at + 120:00 → status=late, stock decremented."""

    def test_late_at_plus_15m01s(self, make_client, db_session):
        """now == due_at + 15:01 (one second past taken boundary) → LATE."""
        now = DUE_AT + timedelta(minutes=15, seconds=1)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "late"
        assert _get_stock(db_session, m.id) == 19   # stock still decremented on late

    def test_late_at_plus_120m00s(self, make_client, db_session):
        """now == due_at + 120:00 (boundary inclusive) → LATE, not missed."""
        now = DUE_AT + timedelta(minutes=120)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "late"
        assert _get_stock(db_session, m.id) == 19


class TestMissedWindow:
    """Spec: due_at + 120:01 → patient gets 409; caregiver records missed, stock unchanged."""

    def test_patient_key_rejected_after_120m01s(self, make_client, db_session):
        """Patient key cannot confirm past the late window → 409, stock unchanged."""
        now = DUE_AT + timedelta(minutes=120, seconds=1)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert res.status_code == status.HTTP_409_CONFLICT
        assert _get_stock(db_session, m.id) == 20   # unchanged

    def test_caregiver_key_records_missed_after_120m01s(self, make_client, db_session):
        """Caregiver key past late window → 200 but status stays missed, stock unchanged."""
        now = DUE_AT + timedelta(minutes=120, seconds=1)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id, dose_amount=1.0)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          json={"source": "caregiver"}, headers=_caregiver_headers())
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["status"] == "missed"           # status is missed, not taken
        assert _get_stock(db_session, m.id) == 20   # stock never decremented on missed


class TestEarlyWindow:
    """
    Spec: 60 min early → allowed; 61 min early → 409 "too early".
    Approval explicitly adds: 59 min early → also allowed.
    """

    def test_59_minutes_early_allowed(self, make_client, db_session):
        """59 min before due_at → well within early window → allowed (taken)."""
        now = DUE_AT - timedelta(minutes=59)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        # 59 min early is within the taken window (due_at + 15 min is still > due_at - 59 min
        # so now <= taken_cutoff), but taken_cutoff = due_at + 15 min and now = due_at - 59 min.
        # now <= taken_cutoff (due_at-59 <= due_at+15) → True → TAKEN
        assert res.json()["status"] == "taken"
        assert _get_stock(db_session, m.id) == 19

    def test_60_minutes_early_allowed(self, make_client, db_session):
        """60 min before due_at = exactly the early_cutoff boundary → allowed (taken)."""
        now = DUE_AT - timedelta(minutes=60)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          headers=_patient_headers())
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["status"] == "taken"
        assert _get_stock(db_session, m.id) == 19

    def test_61_minutes_early_rejected(self, make_client, db_session):
        """61 min before due_at → beyond early window → 409 Too early, stock unchanged."""
        now = DUE_AT - timedelta(minutes=61)
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=20)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        res = client.post(f"/api/v1/doses/{dose.id}/taken",
                          headers=_patient_headers())
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "too early" in res.json()["detail"].lower()
        assert _get_stock(db_session, m.id) == 20   # stock unchanged


# ===========================================================================
# SECTION B-2: Double-dose guard tests
# ===========================================================================

class TestDoubleDoseGuard:
    """
    Spec: blocked at 30 min after a confirmed dose, allowed at 31 min.
    Stock must be unchanged on a blocked attempt.
    """

    def _setup(self, db):
        """Create patient, medication (stock=20), one schedule, TWO dose slots.

        Both doses share the same schedule_id. The guard query is:
            DoseEvent.schedule_id == X AND DoseEvent.id != current AND responded_at >= window.
        We give dose1 due_at=DUE_AT and dose2 due_at=DUE_AT+1s so they pass the
        UniqueConstraint on (schedule_id, due_at). Both are within the clock window
        when confirmed at T1 or T1+30/31.
        """
        p = _make_patient(db)
        m = _make_med(db, p.id, stock=20)
        s = _make_sched(db, m.id, dose_amount=1.0)
        # Dose 1: due exactly at DUE_AT — confirmed first
        dose1 = DoseEvent(
            schedule_id=s.id,
            due_at=DUE_AT,
            status=DoseStatus.PENDING,
        )
        # Dose 2: due 1 second after DUE_AT — the "second dose" candidate
        # At T1+30 min the clock is 29min59s past dose2.due_at → within late window, not too early
        dose2 = DoseEvent(
            schedule_id=s.id,
            due_at=DUE_AT + timedelta(seconds=1),
            status=DoseStatus.PENDING,
        )
        db.add_all([dose1, dose2])
        db.commit()
        db.refresh(dose1)
        db.refresh(dose2)
        return p, m, s, dose1, dose2

    def test_blocked_at_30_minutes(self, make_client, db_session):
        """
        Confirm dose1 at T1, then try dose2 exactly 30 min later.
        Guard window = responded_at >= now - 30 min.
        At 30 min: window_start = T1+30 - 30 = T1; responded_at = T1 >= T1 → BLOCKED.
        Stock must remain 19 (decremented by dose1 only).
        """
        T1 = DUE_AT  # confirm dose1 at its due time

        p, m, s, dose1, dose2 = self._setup(db_session)

        # Step 1: confirm dose1 at T1 → should be TAKEN (within window)
        c1 = make_client(T1)
        r1 = c1.post(f"/api/v1/doses/{dose1.id}/taken",
                     json={"source": "phone"}, headers=_patient_headers())
        assert r1.status_code == status.HTTP_200_OK, r1.json()
        assert r1.json()["status"] == "taken"
        assert _get_stock(db_session, m.id) == 19   # 20 - 1

        # Step 2: try dose2 exactly 30 min later → BLOCKED
        # dose2.due_at is DUE_AT+1day; now = T1+30min, so it's 23h30m early — within early window
        T2 = T1 + timedelta(minutes=30)
        c2 = make_client(T2)
        r2 = c2.post(f"/api/v1/doses/{dose2.id}/taken",
                     json={"source": "phone"}, headers=_patient_headers())
        assert r2.status_code == status.HTTP_409_CONFLICT, r2.json()
        assert "double-dose" in r2.json()["detail"].lower()
        assert _get_stock(db_session, m.id) == 19   # still 19, not 18

    def test_allowed_at_31_minutes(self, make_client, db_session):
        """
        Confirm dose1 at T1, then dose2 at T1 + 31 min.
        window_start = T1+31 - 30 = T1+1; responded_at = T1 < T1+1 → NOT blocked.
        Stock decremented twice: 20 - 1 - 1 = 18.
        """
        T1 = DUE_AT

        p, m, s, dose1, dose2 = self._setup(db_session)

        # Step 1: confirm dose1 at T1
        c1 = make_client(T1)
        r1 = c1.post(f"/api/v1/doses/{dose1.id}/taken",
                     json={"source": "phone"}, headers=_patient_headers())
        assert r1.status_code == status.HTTP_200_OK
        assert _get_stock(db_session, m.id) == 19

        # Step 2: dose2 at T1 + 31 min → ALLOWED
        T2 = T1 + timedelta(minutes=31)
        c2 = make_client(T2)
        r2 = c2.post(f"/api/v1/doses/{dose2.id}/taken",
                     json={"source": "phone"}, headers=_patient_headers())
        assert r2.status_code == status.HTTP_200_OK
        assert r2.json()["status"] in ("taken", "late")
        assert _get_stock(db_session, m.id) == 18   # 20 - 1 - 1

    def test_blocked_attempt_leaves_stock_unchanged(self, make_client, db_session):
        """Explicit assertion that a blocked double-dose attempt never touches stock."""
        T1 = DUE_AT

        p, m, s, dose1, dose2 = self._setup(db_session)
        initial_stock = _get_stock(db_session, m.id)
        assert initial_stock == 20

        # Confirm dose1
        c1 = make_client(T1)
        c1.post(f"/api/v1/doses/{dose1.id}/taken",
                json={"source": "phone"}, headers=_patient_headers())
        after_first = _get_stock(db_session, m.id)
        assert after_first == 19

        # Blocked attempt at +30 min
        c2 = make_client(T1 + timedelta(minutes=30))
        blocked = c2.post(f"/api/v1/doses/{dose2.id}/taken",
                          json={"source": "phone"}, headers=_patient_headers())
        assert blocked.status_code == status.HTTP_409_CONFLICT
        # Stock must not have changed from after first confirm
        assert _get_stock(db_session, m.id) == after_first


# ===========================================================================
# SECTION B-3: Already-terminal rule (rule 1)
# ===========================================================================

class TestAlreadyTerminal:
    """A dose that is already taken/late/skipped cannot be confirmed again."""

    def test_taken_dose_returns_409(self, make_client, db_session):
        now = DUE_AT
        client = make_client(now)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)

        dose = DoseEvent(
            schedule_id=s.id,
            due_at=DUE_AT,
            status=DoseStatus.TAKEN,    # already terminal
            responded_at=DUE_AT - timedelta(hours=1),
        )
        db_session.add(dose)
        db_session.commit()
        db_session.refresh(dose)

        res = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "taken" in res.json()["detail"].lower()


# ===========================================================================
# SECTION B-4: reminder_time pure function
# ===========================================================================

class TestReminderTime:
    """
    reminder_time(dose, offset) must not mutate due_at and must
    return due_at + offset.
    """

    def test_reminder_time_no_offset(self, db_session):
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        result = reminder_time(dose, offset_minutes=0)
        assert result == DUE_AT
        # due_at on the dose object must not be mutated
        assert ensure_utc(dose.due_at) == DUE_AT

    def test_reminder_time_with_offset(self, db_session):
        """Offset shifts reminder time; due_at on the event is unchanged."""
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        result = reminder_time(dose, offset_minutes=30)
        expected = DUE_AT + timedelta(minutes=30)
        assert result == expected
        # due_at must be unchanged
        assert ensure_utc(dose.due_at) == DUE_AT

    def test_reminder_time_negative_offset(self, db_session):
        """Negative offset (remind before due_at) is supported."""
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        result = reminder_time(dose, offset_minutes=-15)
        expected = DUE_AT - timedelta(minutes=15)
        assert result == expected
        assert ensure_utc(dose.due_at) == DUE_AT


# ===========================================================================
# SECTION C-1: generate_dose_events
# ===========================================================================

from app.services.dose_engine import generate_dose_events, mark_overdue_as_missed


class TestGenerateDoseEvents:
    """
    Spec:
    - generate_dose_events is idempotent: running twice produces 0 new rows.
    - Weekday computed in the PATIENT'S timezone.
    - start_date schedule bounds are respected.
    """

    def _make_base(self, db, days_of_week="daily"):
        p = Patient(
            name="Lakshmi Devi", age=72, timezone="Asia/Kolkata",
            wake_time=time(7, 0), sleep_time=time(22, 0),
        )
        db.add(p); db.commit(); db.refresh(p)

        m = Medication(
            patient_id=p.id, name="Metformin", strength="500mg",
            form="tablet", active=True, stock_remaining=30, refill_threshold=5,
        )
        db.add(m); db.commit(); db.refresh(m)

        s = Schedule(
            medication_id=m.id, time_of_day=time(8, 0),
            days_of_week=days_of_week, start_date=date(2026, 6, 1), dose_amount=1.0,
        )
        db.add(s); db.commit(); db.refresh(s)
        return p, m, s

    def test_generate_creates_events(self, db_session):
        """generate_dose_events for 3 days of a daily schedule creates 3 events."""
        p, m, s = self._make_base(db_session)
        result = generate_dose_events(db=db_session, start_date=date(2026, 6, 1), days=3, patient_id=p.id)
        assert result["generated"] == 3
        assert result["skipped_duplicates"] == 0
        assert db_session.query(DoseEvent).count() == 3

    def test_generate_is_idempotent(self, db_session):
        """Running generate twice: second run creates 0 new rows."""
        p, m, s = self._make_base(db_session)
        first = generate_dose_events(db=db_session, start_date=date(2026, 6, 1), days=3, patient_id=p.id)
        assert first["generated"] == 3

        second = generate_dose_events(db=db_session, start_date=date(2026, 6, 1), days=3, patient_id=p.id)
        assert second["generated"] == 0
        assert second["skipped_duplicates"] == 3
        assert db_session.query(DoseEvent).count() == 3

    def test_generate_respects_weekday_in_patient_timezone(self, db_session):
        """
        Sunday-only schedule (days_of_week='6').
        2026-06-07 is a Sunday; 2026-06-01..06 are Mon-Sat.
        7-day window starting 2026-06-01 must generate exactly 1 event.
        """
        p, m, s = self._make_base(db_session, days_of_week="6")  # 6 = Sunday
        result = generate_dose_events(db=db_session, start_date=date(2026, 6, 1), days=7, patient_id=p.id)
        assert result["generated"] == 1, f"Expected 1 Sunday dose, got {result['generated']}"

    def test_generate_respects_start_date(self, db_session):
        """Events before the schedule start_date are skipped."""
        p, m, s = self._make_base(db_session)
        # Schedule starts 2026-06-01; request starts 2026-05-30 → 4 days, 2 qualify
        result = generate_dose_events(db=db_session, start_date=date(2026, 5, 30), days=4, patient_id=p.id)
        assert result["generated"] == 2

    def test_generate_api_endpoint_idempotent(self, make_client, db_session):
        """POST /admin/generate-doses twice → second call returns generated=0."""
        p, m, s = self._make_base(db_session)
        FIXED_NOW = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        client = make_client(FIXED_NOW)

        r1 = client.post(
            f"/api/v1/admin/generate-doses?days=2&patient_id={p.id}",
            headers=_caregiver_headers(),
        )
        assert r1.status_code == status.HTTP_200_OK
        assert r1.json()["generated"] == 2

        r2 = client.post(
            f"/api/v1/admin/generate-doses?days=2&patient_id={p.id}",
            headers=_caregiver_headers(),
        )
        assert r2.status_code == status.HTTP_200_OK
        assert r2.json()["generated"] == 0
        assert r2.json()["skipped_duplicates"] == 2


# ===========================================================================
# SECTION C-2: mark_overdue_as_missed
# ===========================================================================

class TestMarkOverdueAsMissed:
    """
    Spec (strict): a dose becomes MISSED only STRICTLY after 120 minutes.
    Code: due_at < cutoff  where cutoff = now - LATE_WINDOW_MINUTES.
      +119:59 → PENDING  (due_at > cutoff)
      +120:00 → PENDING  (due_at == cutoff, strict < means not included)
      +120:01 → MISSED   (due_at < cutoff)
    Consistent with confirm_taken where +120:00 is still "late", not "missed".
    """

    def _make_pending_dose(self, db, due_at):
        p = _make_patient(db)
        m = _make_med(db, p.id)
        s = _make_sched(db, m.id)
        dose = DoseEvent(schedule_id=s.id, due_at=due_at, status=DoseStatus.PENDING)
        db.add(dose); db.commit(); db.refresh(dose)
        return dose

    def test_pending_dose_past_window_becomes_missed(self, db_session):
        """A PENDING dose 3 h overdue → MISSED."""
        now = DUE_AT + timedelta(hours=3)
        dose = self._make_pending_dose(db_session, due_at=DUE_AT)
        count = mark_overdue_as_missed(db=db_session, clock=lambda: now)
        assert count == 1
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.MISSED

    def test_pending_dose_not_yet_overdue_stays_pending(self, db_session):
        """A PENDING dose 119 min overdue → still PENDING."""
        now = DUE_AT + timedelta(minutes=119)
        dose = self._make_pending_dose(db_session, due_at=DUE_AT)
        count = mark_overdue_as_missed(db=db_session, clock=lambda: now)
        assert count == 0
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.PENDING

    # --- BOUNDARY TESTS (fix 1) ---

    def test_boundary_at_119m59s_stays_pending(self, db_session):
        """due_at + 119:59 → PENDING (strictly inside late window)."""
        now = DUE_AT + timedelta(minutes=119, seconds=59)
        dose = self._make_pending_dose(db_session, due_at=DUE_AT)
        count = mark_overdue_as_missed(db=db_session, clock=lambda: now)
        assert count == 0
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.PENDING

    def test_boundary_at_120m00s_stays_pending(self, db_session):
        """due_at + 120:00 → PENDING (strict boundary: == cutoff is NOT included)."""
        now = DUE_AT + timedelta(minutes=120)
        dose = self._make_pending_dose(db_session, due_at=DUE_AT)
        count = mark_overdue_as_missed(db=db_session, clock=lambda: now)
        assert count == 0
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.PENDING

    def test_boundary_at_120m01s_becomes_missed(self, db_session):
        """due_at + 120:01 → MISSED (one second past the strict cutoff)."""
        now = DUE_AT + timedelta(minutes=120, seconds=1)
        dose = self._make_pending_dose(db_session, due_at=DUE_AT)
        count = mark_overdue_as_missed(db=db_session, clock=lambda: now)
        assert count == 1
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.MISSED

    def test_mark_missed_is_idempotent(self, db_session):
        """Running twice: second call returns 0."""
        now = DUE_AT + timedelta(hours=3)
        self._make_pending_dose(db_session, due_at=DUE_AT)
        assert mark_overdue_as_missed(db=db_session, clock=lambda: now) == 1
        assert mark_overdue_as_missed(db=db_session, clock=lambda: now) == 0

    def test_mark_missed_api_endpoint(self, make_client, db_session):
        """POST /admin/mark-missed returns correct count and sets status."""
        FIXED_NOW = DUE_AT + timedelta(hours=3)
        client = make_client(FIXED_NOW)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        dose = DoseEvent(schedule_id=s.id, due_at=DUE_AT, status=DoseStatus.PENDING)
        db_session.add(dose); db_session.commit()

        r = client.post("/api/v1/admin/mark-missed", headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["marked_missed"] == 1
        db_session.expire_all()
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.MISSED


# ===========================================================================
# SECTION C-3: Refill alerts
# ===========================================================================

class TestRefillAlerts:
    """GET /patients/{id}/refills: stock_remaining <= refill_threshold."""

    def test_at_threshold_included(self, make_client, db_session):
        """stock == threshold → included."""
        client = make_client(DUE_AT)
        p = _make_patient(db_session)
        m = Medication(
            patient_id=p.id, name="Metformin", strength="500mg",
            form="tablet", active=True, stock_remaining=5, refill_threshold=5,
        )
        db_session.add(m); db_session.commit()
        r = client.get(f"/api/v1/patients/{p.id}/refills", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert len(r.json()["medications"]) == 1
        assert r.json()["medications"][0]["name"] == "Metformin"

    def test_above_threshold_excluded(self, make_client, db_session):
        """stock > threshold → not included."""
        client = make_client(DUE_AT)
        p = _make_patient(db_session)
        m = Medication(
            patient_id=p.id, name="Amlodipine", strength="5mg",
            form="tablet", active=True, stock_remaining=10, refill_threshold=5,
        )
        db_session.add(m); db_session.commit()
        r = client.get(f"/api/v1/patients/{p.id}/refills", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["medications"] == []

    def test_stock_decrements_into_refill_zone(self, make_client, db_session):
        """Confirming a dose that drops stock to threshold → appears in refill list."""
        client = make_client(DUE_AT)
        p = _make_patient(db_session)
        m = Medication(
            patient_id=p.id, name="VitD", strength="60000IU",
            form="capsule", active=True, stock_remaining=6, refill_threshold=5,
        )
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        s = Schedule(medication_id=m.id, time_of_day=time(8, 0), days_of_week="daily",
                     start_date=date(2026, 1, 1), dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)
        dose = DoseEvent(schedule_id=s.id, due_at=DUE_AT, status=DoseStatus.PENDING)
        db_session.add(dose); db_session.commit(); db_session.refresh(dose)

        assert client.get(f"/api/v1/patients/{p.id}/refills", headers=_patient_headers()).json()["medications"] == []
        client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert _get_stock(db_session, m.id) == 5
        assert len(client.get(f"/api/v1/patients/{p.id}/refills", headers=_patient_headers()).json()["medications"]) == 1


# ===========================================================================
# SECTION C-4: Adherence calculation
# ===========================================================================

class TestAdherence:
    """adherence_percentage = (taken+late) / (taken+late+missed) * 100."""

    def _add_dose(self, db, sched, day_offset, st):
        d = DoseEvent(schedule_id=sched.id, due_at=DUE_AT - timedelta(days=day_offset), status=st)
        db.add(d)

    def test_all_taken_100_percent(self, make_client, db_session):
        """3 taken → 100%."""
        client = make_client(DUE_AT + timedelta(hours=1))
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        for i in range(1, 4):
            self._add_dose(db_session, s, i, DoseStatus.TAKEN)
        db_session.commit()
        r = client.get(f"/api/v1/patients/{p.id}/adherence?days=30", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["adherence_percentage"] == 100.0
        assert r.json()["status_counts"]["taken"] == 3

    def test_mixed_statuses_75_percent(self, make_client, db_session):
        """2 taken + 1 late + 1 missed + 1 skipped + 1 pending → 75%."""
        client = make_client(DUE_AT + timedelta(hours=1))
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        for i, st in enumerate([
            DoseStatus.TAKEN, DoseStatus.TAKEN, DoseStatus.LATE,
            DoseStatus.MISSED, DoseStatus.SKIPPED, DoseStatus.PENDING,
        ], start=1):
            self._add_dose(db_session, s, i, st)
        db_session.commit()
        r = client.get(f"/api/v1/patients/{p.id}/adherence?days=30", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["adherence_percentage"] == 75.0

    def test_no_events_null_adherence(self, make_client, db_session):
        """No events → null."""
        client = make_client(DUE_AT)
        p = _make_patient(db_session)
        r = client.get(f"/api/v1/patients/{p.id}/adherence?days=7", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["adherence_percentage"] is None


# ===========================================================================
# SECTION C-5: 11:30 PM IST timezone edge case
# ===========================================================================

class TestTimezoneEdgeCases:
    """
    11:30 PM IST (23:30 IST) = 18:00 UTC.
    doses/today must use the patient's IST timezone, not UTC.
    """

    def test_11_30_pm_ist_dose_in_today_list(self, make_client, db_session):
        """
        Dose due 23:30 IST 2026-06-01 = 18:00 UTC.
        Clock frozen to 12:00 UTC (= 17:30 IST, same IST day) → dose must appear.
        """
        import zoneinfo
        IST = zoneinfo.ZoneInfo("Asia/Kolkata")
        due_utc = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
        FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        client = make_client(FIXED_NOW)

        p = Patient(name="Night Owl", age=65, timezone="Asia/Kolkata",
                    wake_time=time(7, 0), sleep_time=time(23, 59))
        db_session.add(p); db_session.commit(); db_session.refresh(p)
        m = Medication(patient_id=p.id, name="Amlodipine", strength="5mg",
                       form="tablet", active=True, stock_remaining=30, refill_threshold=5)
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        s = Schedule(medication_id=m.id, time_of_day=time(23, 30), days_of_week="daily",
                     start_date=date(2026, 1, 1), dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)
        dose = DoseEvent(schedule_id=s.id, due_at=due_utc, status=DoseStatus.PENDING)
        db_session.add(dose); db_session.commit(); db_session.refresh(dose)

        r = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert dose.id in [d["id"] for d in r.json()], (
            f"11:30 PM IST dose must appear in today's list (clock={FIXED_NOW} UTC = {FIXED_NOW.astimezone(IST)} IST)"
        )

    def test_11_30_pm_ist_dose_not_in_next_ist_day(self, make_client, db_session):
        """
        When clock is 00:30 IST 2026-06-02 (= 19:00 UTC 2026-06-01),
        the 23:30 IST 2026-06-01 dose must NOT appear in today's list.
        """
        import zoneinfo
        IST = zoneinfo.ZoneInfo("Asia/Kolkata")
        due_utc = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
        FIXED_NOW = datetime(2026, 6, 2, 0, 30, 0, tzinfo=IST).astimezone(timezone.utc)
        client = make_client(FIXED_NOW)

        p = Patient(name="Night Owl 2", age=65, timezone="Asia/Kolkata",
                    wake_time=time(7, 0), sleep_time=time(23, 59))
        db_session.add(p); db_session.commit(); db_session.refresh(p)
        m = Medication(patient_id=p.id, name="Amlodipine", strength="5mg",
                       form="tablet", active=True, stock_remaining=30, refill_threshold=5)
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        s = Schedule(medication_id=m.id, time_of_day=time(23, 30), days_of_week="daily",
                     start_date=date(2026, 1, 1), dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)
        dose = DoseEvent(schedule_id=s.id, due_at=due_utc, status=DoseStatus.PENDING)
        db_session.add(dose); db_session.commit(); db_session.refresh(dose)

        r = client.get(f"/api/v1/patients/{p.id}/doses/today", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert dose.id not in [d["id"] for d in r.json()], (
            "11:30 PM IST 2026-06-01 must NOT appear on 2026-06-02 IST"
        )



# ===========================================================================
# SECTION D: Stock behaviour, generate edge cases, caregiver note, admin auth
# ===========================================================================

from app.models.patient import PatientNote
from app.models.enums import NoteKind


class TestStockBehaviour:
    """
    Stock rules (all verified against app code):
      - taken confirmation (within 15m window) -> stock decremented
      - late confirmation (15:01 to 120:00) -> stock decremented
      - skipped dose -> stock unchanged
      - rejected confirmation (too early, already taken, missed+patient key)
        -> stock unchanged (engine returns before Rule 6)
      - stock never goes below zero (clamped to max(0, stock - amount))
    """

    def _setup(self, db_session, stock=INITIAL_STOCK, dose_amount=1.0, due_at=DUE_AT):
        p = _make_patient(db_session)
        m = _make_med(db_session, p.id, stock=stock)
        s = _make_sched(db_session, m.id, dose_amount=dose_amount)
        dose = _make_dose(db_session, s.id, due_at=due_at)
        return p, m, s, dose

    def test_late_confirmation_decrements_stock(self, make_client, db_session):
        """A late confirmation (now = due_at + 60 min) -> status late, stock decremented."""
        LATE_NOW = DUE_AT + timedelta(minutes=60)
        p, m, s, dose = self._setup(db_session)
        client = make_client(LATE_NOW)

        r = client.post(
            f"/api/v1/doses/{dose.id}/taken",
            headers=_patient_headers(),
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["status"] == "late"
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == INITIAL_STOCK - 1

    def test_skipped_dose_does_not_change_stock(self, make_client, db_session):
        """Skipping a dose leaves stock unchanged."""
        p, m, s, dose = self._setup(db_session)
        client = make_client(DUE_AT)

        r = client.post(f"/api/v1/doses/{dose.id}/skip", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["status"] == "skipped"
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == INITIAL_STOCK

    def test_too_early_rejection_does_not_change_stock(self, make_client, db_session):
        """Confirming 61 min early -> 409; stock unchanged."""
        TOO_EARLY = DUE_AT - timedelta(minutes=61)
        p, m, s, dose = self._setup(db_session)
        client = make_client(TOO_EARLY)

        r = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert r.status_code == status.HTTP_409_CONFLICT
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == INITIAL_STOCK

    def test_already_taken_rejection_does_not_change_stock(self, make_client, db_session):
        """Second confirm on a TAKEN dose -> 409; stock not decremented again."""
        p, m, s, dose = self._setup(db_session)
        client = make_client(DUE_AT)

        # First confirmation succeeds
        r1 = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert r1.status_code == status.HTTP_200_OK
        db_session.expire_all()
        stock_after_first = db_session.get(Medication, m.id).stock_remaining
        assert stock_after_first == INITIAL_STOCK - 1

        # Second attempt on same dose -> 409
        r2 = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert r2.status_code == status.HTTP_409_CONFLICT
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == stock_after_first

    def test_missed_with_patient_key_does_not_change_stock(self, make_client, db_session):
        """Patient key confirm after +121 min -> 409; stock unchanged."""
        PAST_WINDOW = DUE_AT + timedelta(minutes=121)
        p, m, s, dose = self._setup(db_session)
        client = make_client(PAST_WINDOW)

        r = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert r.status_code == status.HTTP_409_CONFLICT
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == INITIAL_STOCK

    def test_stock_never_goes_below_zero(self, make_client, db_session):
        """Stock of 0 cannot go negative; clamped to 0."""
        p, m, s, dose = self._setup(db_session, stock=0, dose_amount=5.0)
        client = make_client(DUE_AT)

        r = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_patient_headers())
        assert r.status_code == status.HTTP_200_OK
        db_session.expire_all()
        assert db_session.get(Medication, m.id).stock_remaining == 0


class TestGenerateEdgeCases:
    """
    Additional generate_dose_events edge cases:
      - weekly schedule on wrong weekday -> creates nothing
      - end_date in the past -> creates nothing
      - inactive medication -> creates nothing
    """

    def test_weekly_wrong_weekday_creates_nothing(self, make_client, db_session):
        """A Sunday-only schedule creates no events when target days are Mon-Sat."""
        from datetime import date as date_cls
        p = _make_patient(db_session)
        m = Medication(patient_id=p.id, name="WeeklyMed", strength="1g",
                       form="tablet", active=True, stock_remaining=30, refill_threshold=5)
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        # days_of_week "6" = Sunday only (Python weekday: Mon=0, Sun=6)
        s = Schedule(medication_id=m.id, time_of_day=time(8, 0),
                     days_of_week="6", start_date=date_cls(2026, 1, 1), dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)

        # 2026-06-01 is Monday; 6 days Mon->Sat have no Sunday
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=6&start=2026-06-01",
                        headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["generated"] == 0

    def test_end_date_in_past_creates_nothing(self, make_client, db_session):
        """A schedule whose end_date is before the generation window creates nothing."""
        from datetime import date as date_cls
        p = _make_patient(db_session)
        m = Medication(patient_id=p.id, name="ExpiredMed", strength="1g",
                       form="tablet", active=True, stock_remaining=30, refill_threshold=5)
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        s = Schedule(medication_id=m.id, time_of_day=time(8, 0),
                     days_of_week="daily", start_date=date_cls(2026, 1, 1),
                     end_date=date_cls(2026, 5, 31),  # ends before our window
                     dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)

        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=3&start=2026-06-01",
                        headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["generated"] == 0

    def test_inactive_medication_creates_nothing(self, make_client, db_session):
        """Inactive medication is skipped by generate_dose_events."""
        from datetime import date as date_cls
        p = _make_patient(db_session)
        m = Medication(patient_id=p.id, name="InactiveMed", strength="1g",
                       form="tablet", active=False, stock_remaining=30, refill_threshold=5)
        db_session.add(m); db_session.commit(); db_session.refresh(m)
        s = Schedule(medication_id=m.id, time_of_day=time(8, 0),
                     days_of_week="daily", start_date=date_cls(2026, 1, 1), dose_amount=1.0)
        db_session.add(s); db_session.commit(); db_session.refresh(s)

        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=3&start=2026-06-01",
                        headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["generated"] == 0


class TestCaregiverMissedNote:
    """
    When a caregiver confirms a dose past the 120-min window (Rule 4):
      - status stays MISSED
      - a PatientNote of kind QUESTION is created and flagged_to_caregiver=True
      - stock is NOT decremented
    """

    def test_caregiver_missed_creates_question_note_and_status_missed(
        self, make_client, db_session
    ):
        PAST = DUE_AT + timedelta(minutes=121)
        client = make_client(PAST)

        p = _make_patient(db_session)
        m = _make_med(db_session, p.id)
        s = _make_sched(db_session, m.id)
        dose = _make_dose(db_session, s.id, due_at=DUE_AT)

        r = client.post(f"/api/v1/doses/{dose.id}/taken", headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["status"] == "missed"

        db_session.expire_all()

        # Status must be MISSED, not taken/late
        assert db_session.get(DoseEvent, dose.id).status == DoseStatus.MISSED

        # A PatientNote of kind QUESTION must have been created
        notes = (
            db_session.query(PatientNote)
            .filter(PatientNote.patient_id == p.id, PatientNote.kind == NoteKind.QUESTION)
            .all()
        )
        assert len(notes) == 1, f"Expected 1 QUESTION note, found {len(notes)}"
        assert notes[0].flagged_to_caregiver is True

        # Stock must NOT be decremented
        assert db_session.get(Medication, m.id).stock_remaining == INITIAL_STOCK


class TestAdminAuthRejections:
    """
    POST /admin/generate-doses and /admin/mark-missed:
      - no key  -> 401
      - patient key -> 403
      - caregiver key -> 200
    """

    def test_generate_doses_no_key_returns_401(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=1")
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_generate_doses_patient_key_returns_403(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=1", headers=_patient_headers())
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_generate_doses_caregiver_key_returns_200(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/generate-doses?days=1", headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert "generated" in body
        assert "skipped_duplicates" in body

    def test_mark_missed_no_key_returns_401(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/mark-missed")
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mark_missed_patient_key_returns_403(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/mark-missed", headers=_patient_headers())
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_mark_missed_caregiver_key_returns_200(self, make_client):
        client = make_client(DUE_AT)
        r = client.post("/api/v1/admin/mark-missed", headers=_caregiver_headers())
        assert r.status_code == status.HTTP_200_OK
        assert "marked_missed" in r.json()
