from scripts.seed import seed_database
from app.models import Patient, Caregiver, Medication, Schedule, DoseEvent


def test_seed_idempotency(db_session):
    # Run first time
    result1 = seed_database(db_session)
    assert result1["patient"] == "Lakshmi Devi"
    assert result1["caregivers_count"] == 2
    assert result1["medications_count"] == 3
    assert result1["schedules_count"] == 3
    assert result1["dose_events_count"] == 3

    # Snapshot entity counts
    patients_cnt_1 = db_session.query(Patient).count()
    caregivers_cnt_1 = db_session.query(Caregiver).count()
    medications_cnt_1 = db_session.query(Medication).count()
    schedules_cnt_1 = db_session.query(Schedule).count()
    doses_cnt_1 = db_session.query(DoseEvent).count()

    # Run second time
    result2 = seed_database(db_session)
    patients_cnt_2 = db_session.query(Patient).count()
    caregivers_cnt_2 = db_session.query(Caregiver).count()
    medications_cnt_2 = db_session.query(Medication).count()
    schedules_cnt_2 = db_session.query(Schedule).count()
    doses_cnt_2 = db_session.query(DoseEvent).count()

    # Must be identical - no duplicate rows created
    assert patients_cnt_1 == patients_cnt_2 == 1
    assert caregivers_cnt_1 == caregivers_cnt_2 == 2
    assert medications_cnt_1 == medications_cnt_2 == 3
    assert schedules_cnt_1 == schedules_cnt_2 == 3
    assert doses_cnt_1 == doses_cnt_2 == 3

    # Check medication close to refill threshold
    metformin = db_session.query(Medication).filter(Medication.name == "Metformin").first()
    assert metformin.stock_remaining == 12
    assert metformin.refill_threshold == 10
    assert metformin.stock_remaining - metformin.refill_threshold <= 2
