"""
tests/test_doses.py

Tests for dose-related HTTP endpoints.  All timing-sensitive tests use a
frozen clock so they do not depend on the time of day.

Rules:
- Do not use datetime.now() for due_at in tests that POST to taken/skip/snooze.
- Use make_client(fixed_now) so the router clock matches due_at.
- Existing assertions are preserved unchanged.
"""
from datetime import datetime, date, time, timedelta, timezone
import zoneinfo
from fastapi import status

from app.models import (
    Patient,
    Medication,
    Schedule,
    DoseEvent,
    DoseStatus,
    DoseSource,
)


# ---------------------------------------------------------------------------
# Timezone boundary test (doses/today)
# ---------------------------------------------------------------------------

def test_doses_today_respects_patient_timezone(make_client, db_session, patient_headers):
    """
    The /doses/today endpoint must filter by the patient's local timezone, not UTC.
    Frozen to 12:00 UTC on 2026-06-01 (= 17:30 IST on 2026-06-01).
    """
    FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    client = make_client(FIXED_NOW)

    # Patient in Asia/Kolkata (UTC +05:30)
    patient = Patient(
        name="Lakshmi Devi",
        age=72,
        language="kn",
        timezone="Asia/Kolkata",
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)

    med = Medication(
        patient_id=patient.id,
        name="Metformin",
        strength="500mg",
        form="tablet",
        instructions="after breakfast",
        stock_remaining=20,
        refill_threshold=5,
    )
    db_session.add(med)
    db_session.commit()
    db_session.refresh(med)

    sched = Schedule(
        medication_id=med.id,
        time_of_day=time(8, 0),
        days_of_week="0,1,2,3,4,5,6",
        dose_amount=1.0,
        start_date=date(2026, 1, 1),
    )
    db_session.add(sched)
    db_session.commit()
    db_session.refresh(sched)

    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    # Fixed "today" in IST is 2026-06-01
    today_ist = datetime(2026, 6, 1, 0, 0, 0, tzinfo=tz).date()  # 2026-06-01

    # Dose A: yesterday IST (23:59:59 IST on 2026-05-31 = 18:29:59 UTC 2026-05-31)
    dt_a = datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz)
    dose_a = DoseEvent(schedule_id=sched.id, due_at=dt_a.astimezone(timezone.utc))

    # Dose B: 00:00:01 IST today (= 18:30:01 UTC 2026-05-31 — previous UTC day, but IST today)
    dt_b = datetime(2026, 6, 1, 0, 0, 1, tzinfo=tz)
    dose_b = DoseEvent(schedule_id=sched.id, due_at=dt_b.astimezone(timezone.utc))

    # Dose C: 12:00:00 IST today
    dt_c = datetime(2026, 6, 1, 12, 0, 0, tzinfo=tz)
    dose_c = DoseEvent(schedule_id=sched.id, due_at=dt_c.astimezone(timezone.utc))

    # Dose D: 00:00:01 IST tomorrow
    dt_d = datetime(2026, 6, 2, 0, 0, 1, tzinfo=tz)
    dose_d = DoseEvent(schedule_id=sched.id, due_at=dt_d.astimezone(timezone.utc))

    db_session.add_all([dose_a, dose_b, dose_c, dose_d])
    db_session.commit()

    res = client.get(
        f"/api/v1/patients/{patient.id}/doses/today", headers=patient_headers
    )
    assert res.status_code == status.HTTP_200_OK
    doses = res.json()

    returned_ids = [d["id"] for d in doses]

    # B and C are today IST
    assert dose_b.id in returned_ids
    assert dose_c.id in returned_ids

    # A (yesterday IST) and D (tomorrow IST) must be excluded
    assert dose_a.id not in returned_ids
    assert dose_d.id not in returned_ids

    # Ordered by due_at ascending
    assert doses[0]["id"] == dose_b.id
    assert doses[1]["id"] == dose_c.id

    # Medication fields must be present
    assert doses[0]["medication_name"] == "Metformin"
    assert doses[0]["medication_strength"] == "500mg"
    assert doses[0]["medication_instructions"] == "after breakfast"


# ---------------------------------------------------------------------------
# Mark taken (frozen clock at due_at → should be TAKEN)
# ---------------------------------------------------------------------------

def test_dose_taken_action(make_client, db_session, patient_headers):
    """Confirming a dose exactly at due_at → status=taken, source recorded."""
    FIXED_NOW = datetime(2026, 6, 1, 21, 0, 0, tzinfo=timezone.utc)
    client = make_client(FIXED_NOW)

    patient = Patient(
        name="Lakshmi Devi",
        age=72,
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(patient)
    db_session.commit()

    med = Medication(
        patient_id=patient.id,
        name="Amlodipine",
        strength="5mg",
        form="tablet",
        stock_remaining=10,
        refill_threshold=5,
    )
    db_session.add(med)
    db_session.commit()

    sched = Schedule(
        medication_id=med.id,
        time_of_day=time(21, 0),
        days_of_week="daily",
        start_date=date(2026, 1, 1),
        dose_amount=1.0,
    )
    db_session.add(sched)
    db_session.commit()

    dose = DoseEvent(
        schedule_id=sched.id,
        due_at=FIXED_NOW,           # due exactly at frozen now
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()
    db_session.refresh(dose)

    # 404 for unknown dose
    res_404 = client.post("/api/v1/doses/99999/taken", headers=patient_headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND

    # Mark taken with explicit source
    res = client.post(
        f"/api/v1/doses/{dose.id}/taken",
        json={"source": "watch"},
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "taken"
    assert data["source"] == "watch"
    assert data["responded_at"] is not None


# ---------------------------------------------------------------------------
# Skip (timing-insensitive, but frozen for consistency)
# ---------------------------------------------------------------------------

def test_dose_skip_action(make_client, db_session, patient_headers):
    FIXED_NOW = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    client = make_client(FIXED_NOW)

    patient = Patient(
        name="Lakshmi Devi",
        age=72,
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(patient)
    db_session.commit()

    med = Medication(patient_id=patient.id, name="Test", strength="1g", form="tab")
    db_session.add(med)
    db_session.commit()

    sched = Schedule(
        medication_id=med.id,
        time_of_day=time(10, 0),
        days_of_week="daily",
        start_date=date(2026, 1, 1),
    )
    db_session.add(sched)
    db_session.commit()

    dose = DoseEvent(
        schedule_id=sched.id,
        due_at=FIXED_NOW,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()

    res = client.post(f"/api/v1/doses/{dose.id}/skip", headers=patient_headers)
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["status"] == "skipped"


# ---------------------------------------------------------------------------
# Snooze (timing-insensitive, but frozen for consistency)
# ---------------------------------------------------------------------------

def test_dose_snooze_action(make_client, db_session, patient_headers):
    FIXED_NOW = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    client = make_client(FIXED_NOW)

    patient = Patient(
        name="Lakshmi Devi",
        age=72,
        wake_time=time(7, 0),
        sleep_time=time(22, 0),
    )
    db_session.add(patient)
    db_session.commit()

    med = Medication(patient_id=patient.id, name="Test", strength="1g", form="tab")
    db_session.add(med)
    db_session.commit()

    sched = Schedule(
        medication_id=med.id,
        time_of_day=time(10, 0),
        days_of_week="daily",
        start_date=date(2026, 1, 1),
    )
    db_session.add(sched)
    db_session.commit()

    dose = DoseEvent(
        schedule_id=sched.id,
        due_at=FIXED_NOW,
        status=DoseStatus.PENDING,
    )
    db_session.add(dose)
    db_session.commit()

    # 422 for invalid minutes (> 60 or < 1)
    res_invalid = client.post(
        f"/api/v1/doses/{dose.id}/snooze",
        json={"minutes": 120},
        headers=patient_headers,
    )
    assert res_invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Happy path snooze (default 10 minutes)
    res_default = client.post(f"/api/v1/doses/{dose.id}/snooze", headers=patient_headers)
    assert res_default.status_code == status.HTTP_200_OK
    data = res_default.json()
    assert data["dose_id"] == dose.id
    assert data["minutes"] == 10
    assert "snooze_until" in data

    # Snooze with 25 minutes
    res_25 = client.post(
        f"/api/v1/doses/{dose.id}/snooze",
        json={"minutes": 25},
        headers=patient_headers,
    )
    assert res_25.status_code == status.HTTP_200_OK
    assert res_25.json()["minutes"] == 25
