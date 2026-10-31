# MedMate Backend (Layers 1, 2 & 3: Dose Engine)

MedMate is a deterministic medication assistant backend for elderly patients, built with FastAPI, SQLAlchemy 2.x, Alembic, and Pydantic v2.

---

## Prerequisites
- Python 3.11+ (Python 3.14 compatible)
- SQLite (default) or PostgreSQL

---

## Setup & Running Instructions

### 1. Create and Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Default `.env` configuration:
```env
DATABASE_URL=sqlite:///./medmate.db
CAREGIVER_API_KEY=test_caregiver_key_123
PATIENT_API_KEY=test_patient_key_456
DEFAULT_TIMEZONE=Asia/Kolkata

# Dose Timing Windows (minutes)
TAKEN_WINDOW_MINUTES=15
LATE_WINDOW_MINUTES=120
DOUBLE_DOSE_GUARD_MINUTES=30
EARLY_WINDOW_MINUTES=60
DEFAULT_SNOOZE_MINUTES=10
MAX_SNOOZE_MINUTES=60
```

### 4. Run Database Migrations
Apply Alembic migrations to create the schema, tables, and unique constraints:
```bash
alembic upgrade head
```

### 5. Seed Initial Data
Run the idempotent seed script to populate test data:
```bash
python scripts/seed.py
```
This populates:
- **Patient**: "Lakshmi Devi", 72, Kannada (`kn`), `Asia/Kolkata`
- **Caregivers**: 2 caregivers linked to her (`all` and `missed_only` notify levels)
- **Medications & Schedules**:
  - *Metformin 500mg* (8:00 AM daily, after breakfast; stock close to refill threshold)
  - *Amlodipine 5mg* (9:00 PM daily, after dinner)
  - *Vitamin D3 60000IU* (Sunday 10:00 AM, weekly)
- **Today's DoseEvents** scheduled in `Asia/Kolkata` timezone

### 6. Run the Test Suite
Run the automated pytest test suite (100% in-memory SQLite, deterministic clock overrides):
```bash
# Run all tests
pytest -v

# Run the dose engine tests specifically
pytest tests/test_dose_engine.py -v

# Run with test failure details or quiet mode
pytest -q
```

### 7. Run the FastAPI Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 8. Interactive API Documentation
Open your browser and navigate to:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## Authentication
Requests must include the `X-API-Key` header:
- `X-API-Key: test_caregiver_key_123` (Caregiver access: all endpoints, including admin operations)
- `X-API-Key: test_patient_key_456` (Patient access: read endpoints, dose response, patient notes)

Caregiver-only endpoints strictly reject patient keys with `403 Forbidden`. Missing headers return `401 Unauthorized`.
All errors adhere to the standard envelope: `{"error": "...", "detail": "..."}`.

---

## How Dose Status is Decided

MedMate uses a deterministic, rule-based dose engine (`app/services/dose_engine.py`). Dose statuses and boundaries are evaluated strictly:

```text
       < 60m early      0 to +15:00       +15:01 to +120:00       > +120:00
---|----------------|-----------------|-----------------------|-------------->
   │   409 Too Early│      TAKEN      │         LATE          │    MISSED
   │                │ (Stock decr.)   │     (Stock decr.)     │ Patient: 409
   │                │                 │                       │ Caregiver: 200 (Note created)
```

1. **Early Confirmation Window (`EARLY_WINDOW_MINUTES = 60`)**:
   - Confirmed within 60 minutes before `due_at` (`now >= due_at - 60m`): **Allowed**.
   - Confirmed 61+ minutes before `due_at` (`now < due_at - 60m`): **Rejected with `409 Conflict`** ("Too early to record dose"). Stock is unchanged.

2. **Taken Window (`TAKEN_WINDOW_MINUTES = 15`)**:
   - Confirmed from `due_at - 60m` through `due_at + 15:00` (`now <= due_at + 15m`): Status is set to **`taken`**. Medication stock is decremented by `dose_amount`.

3. **Late Window (`LATE_WINDOW_MINUTES = 120`)**:
   - Confirmed from `due_at + 15:01` through `due_at + 120:00` (`now <= due_at + 120m`): Status is set to **`late`**. Medication stock is decremented by `dose_amount`.

4. **Missed Window (Strictly after 120 minutes: `now > due_at + 120m`)**:
   - **Patient Key**: **Rejected with `409 Conflict`** ("Dose window has expired"). Status remains unchanged; stock is unchanged.
   - **Caregiver Key**: Allowed to confirm; status remains **`missed`**, a `PatientNote` of kind `question` is automatically created with `flagged_to_caregiver=True`, and stock is **NOT** decremented.

5. **Double-Dose Guard (`DOUBLE_DOSE_GUARD_MINUTES = 30`)**:
   - If another dose of the same medication was confirmed within the last 30 minutes (`now - previous.responded_at <= 30m`), confirmation is **blocked with `409 Conflict`** ("Double dose guard active").
   - Allowed at 31+ minutes after the previous dose.
   - Blocked attempts never modify medication stock.

6. **Terminal Statuses**:
   - Once a dose is terminal (`taken`, `missed`, or `skipped`), subsequent attempts return `409 Conflict`. Stock is not decremented again.

7. **Stock Depletion Safety**:
   - Medication stock is decremented on `taken` and `late` confirmations, but is clamped to `0` and **never goes negative**. Skipped doses, rejected attempts, and caregiver-recorded missed doses leave stock untouched.

---

## Timing Constants in `.env` & Demo Mode

The timing windows are configured via environment variables in `.env`:

| Variable | Default (Production) | Description |
| :--- | :--- | :--- |
| `TAKEN_WINDOW_MINUTES` | `15` | Window from `due_at` where confirmation results in `taken` status. |
| `LATE_WINDOW_MINUTES` | `120` | Window where confirmation results in `late` status; overdue doses become `missed` strictly after this window. |
| `DOUBLE_DOSE_GUARD_MINUTES` | `30` | Duration following a confirmed dose during which another dose of the same med is rejected. |
| `EARLY_WINDOW_MINUTES` | `60` | Maximum minutes before `due_at` that a dose may be confirmed. |
| `DEFAULT_SNOOZE_MINUTES`| `10` | Default duration added to reminder time when a dose is snoozed. |
| `MAX_SNOOZE_MINUTES` | `60` | Maximum cumulative snooze duration permitted. |

### How to Shrink Windows to Seconds for Demos

For live presentations and rapid demos where waiting 15–120 minutes is impractical:

1. **Option A: Reduce `.env` Constants**:
   Update `.env` with minimal integer minutes:
   ```env
   TAKEN_WINDOW_MINUTES=1
   LATE_WINDOW_MINUTES=2
   DOUBLE_DOSE_GUARD_MINUTES=1
   EARLY_WINDOW_MINUTES=1
   ```
   Restart the server (`uvicorn app.main:app --reload`). Window transitions now occur in 1 to 2 minutes.

2. **Option B: Clock Override / Fast-Forward Simulation**:
   The engine uses a deterministic clock dependency (`get_clock`). In demo scripts or tests, you can fast-forward the application clock instantly without any physical delay:
   ```python
   from app.main import app
   from app.services.clock import get_clock
   from datetime import datetime, timezone, timedelta

   base_time = datetime.now(timezone.utc)
   # Fast forward 16 minutes (triggers late status)
   app.dependency_overrides[get_clock] = lambda: base_time + timedelta(minutes=16)
   # Fast forward 121 minutes (triggers missed status)
   app.dependency_overrides[get_clock] = lambda: base_time + timedelta(minutes=121)
   ```

---

## Key Endpoints with Exact Commands

### Admin Endpoints (Caregiver Key Only)

#### 1. Generate Dose Events: `POST /api/v1/admin/generate-doses`
Generates upcoming `DoseEvent` rows for active medication schedules across the specified window. Idempotent — will skip already scheduled doses.

```bash
# Generate doses for the next 7 days for all patients
curl -X POST "http://localhost:8000/api/v1/admin/generate-doses?days=7" \
  -H "X-API-Key: test_caregiver_key_123"

# Generate doses for a specific patient and start date
curl -X POST "http://localhost:8000/api/v1/admin/generate-doses?days=3&start_date=2026-11-01&patient_id=1" \
  -H "X-API-Key: test_caregiver_key_123"
```
Response:
```json
{
  "generated": 14,
  "skipped_duplicates": 0
}
```

#### 2. Mark Overdue Doses as Missed: `POST /api/v1/admin/mark-missed`
Scans for any `PENDING` dose whose `due_at` is strictly more than 120 minutes in the past (`due_at < now - 120m`) and marks it as `MISSED`. Idempotent.

```bash
curl -X POST "http://localhost:8000/api/v1/admin/mark-missed" \
  -H "X-API-Key: test_caregiver_key_123"
```
Response:
```json
{
  "marked_missed": 2
}
```

---

### Patient Endpoints

#### 3. Refills Alert Endpoint: `GET /api/v1/patients/{id}/refills`
Returns all active medications for the patient where `stock_remaining <= refill_threshold`. Accessible by either caregiver or patient keys.

```bash
# With caregiver key
curl -X GET "http://localhost:8000/api/v1/patients/1/refills" \
  -H "X-API-Key: test_caregiver_key_123"

# With patient key
curl -X GET "http://localhost:8000/api/v1/patients/1/refills" \
  -H "X-API-Key: test_patient_key_456"
```
Response:
```json
{
  "patient_id": 1,
  "medications": [
    {
      "id": 1,
      "name": "Metformin",
      "strength": "500mg",
      "stock_remaining": 4.0,
      "refill_threshold": 5
    }
  ]
}
```

#### 4. Confirm Dose Taken: `POST /api/v1/doses/{id}/taken`
Records a dose as taken. Status becomes `taken` (<= +15m) or `late` (+15:01 to +120:00). Decrements stock.

```bash
curl -X POST "http://localhost:8000/api/v1/doses/1/taken" \
  -H "X-API-Key: test_patient_key_456" \
  -H "Content-Type: application/json" \
  -d '{"source": "phone"}'
```

#### 5. Skip Dose: `POST /api/v1/doses/{id}/skip`
Records a dose as skipped. Stock is not decremented.

```bash
curl -X POST "http://localhost:8000/api/v1/doses/1/skip" \
  -H "X-API-Key: test_patient_key_456"
```

#### 6. Snooze Dose: `POST /api/v1/doses/{id}/snooze`
Increments reminder offset by `DEFAULT_SNOOZE_MINUTES` (up to `MAX_SNOOZE_MINUTES`).

```bash
curl -X POST "http://localhost:8000/api/v1/doses/1/snooze" \
  -H "X-API-Key: test_patient_key_456"
```

---

## Demo Mode (Live Escalation Walkthrough)

MedMate supports rapid live demonstration using fractional minutes for timing windows and the background APScheduler.

### 1. Set Demo Timing Windows in `.env`
Update `.env` with small fractional intervals:
```env
# Demo timing windows (in minutes / seconds)
TAKEN_WINDOW_MINUTES=0.5
LATE_WINDOW_MINUTES=3.0
REMINDER_REPEAT_MINUTES=0.5
CAREGIVER_ESCALATION_MINUTES=1.5
SCHEDULER_TICK_SECONDS=5.0
SCHEDULER_ENABLED=true
```

### 2. Run the FastAPI Server
Start Uvicorn with a single worker and **NO `--reload`** (process reloading restarts background workers and confuses the scheduler):
```bash
uvicorn app.main:app --workers 1
```
*Note: On startup, the scheduler logs enabled status, tick interval, and active notifier channel (`mock`).*

### 3. Create a Demo Dose
In a separate terminal, schedule a dose for the seed patient ("Lakshmi Devi") to become due in 20 seconds:
```bash
python scripts/demo_dose.py --seconds 20
```
This prints the created `DoseEvent` ID (e.g., `4`).

### 4. Observe Escalations in Real-Time
Poll the dose's escalation timeline using the caregiver API key:
```bash
curl -s -H "X-API-Key: test_caregiver_key_123" http://localhost:8000/api/v1/doses/<DOSE_ID>/escalations | jq
```

### Expected Timeline of Events
- **T+0s**: Dose created with status `pending`, due at `T+20s`.
- **T+20s**: Dose becomes due. Scheduler tick fires -> `REMINDER_1` is sent to patient (`whatsapp:+910000000000`).
- **T+50s** (+0.5 min): Patient has not responded -> `REMINDER_2` is sent.
- **T+110s** (+1.5 min from due): Unconfirmed threshold reached -> `CAREGIVER_UNCONFIRMED` alert is sent to caregiver Ramesh Kumar.
- **T+200s** (+3.0 min from due): Late window expires -> dose is marked `missed` -> `MISSED_ALERT` is sent to caregivers.

### Stopping Escalation via Patient Confirmation
At any time before the dose becomes missed, the patient can confirm taking the medication:
```bash
curl -X POST -H "X-API-Key: test_patient_key_456" http://localhost:8000/api/v1/doses/<DOSE_ID>/confirm
```
This updates the dose status to `taken` (or `late`), stopping further escalation and acknowledging open logs.

---
```text
medmate/
├── .env.example
├── requirements.txt
├── README.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── fb162d807d6d_0001_initial_schema.py
│       └── b445df0e1011_0002_dose_unique_constraint.py
├── scripts/
│   ├── __init__.py
│   └── seed.py
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── auth.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── enums.py
│   │   ├── patient.py
│   │   ├── caregiver.py
│   │   ├── medication.py
│   │   └── dose.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── common.py
│   │   ├── patient.py
│   │   ├── caregiver.py
│   │   ├── medication.py
│   │   └── dose.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── clock.py
│   │   ├── dose_engine.py
│   │   ├── patient_service.py
│   │   ├── caregiver_service.py
│   │   ├── medication_service.py
│   │   └── dose_service.py
│   └── routers/
│       ├── __init__.py
│       ├── admin.py
│       ├── patients.py
│       ├── caregivers.py
│       ├── medications.py
│       └── doses.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_auth.py
    ├── test_dose_engine.py
    ├── test_endpoints.py
    ├── test_doses.py
    ├── test_models.py
    └── test_seed.py
```
