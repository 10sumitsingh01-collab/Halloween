from fastapi import status


def test_patients_crud_and_validation(client, caregiver_headers):
    # 422 Validation Error on invalid payload (missing wake_time, age <= 0)
    res = client.post(
        "/api/v1/patients",
        json={"name": "Bad Patient", "age": -5},
        headers=caregiver_headers,
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    err_data = res.json()
    assert err_data["error"] == "Validation Error"

    # Happy path: Create Patient
    res = client.post(
        "/api/v1/patients",
        json={
            "name": "Lakshmi Devi",
            "age": 72,
            "language": "kn",
            "timezone": "Asia/Kolkata",
            "wake_time": "07:00:00",
            "sleep_time": "22:00:00",
        },
        headers=caregiver_headers,
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["name"] == "Lakshmi Devi"
    patient_id = data["id"]

    # 404 for unknown patient
    res_404 = client.get("/api/v1/patients/99999", headers=caregiver_headers)
    assert res_404.status_code == status.HTTP_404_NOT_FOUND
    assert res_404.json()["error"] == "Not Found"

    # Happy path: Get Patient
    res_get = client.get(f"/api/v1/patients/{patient_id}", headers=caregiver_headers)
    assert res_get.status_code == status.HTTP_200_OK
    assert res_get.json()["id"] == patient_id


def test_caregiver_flow(client, caregiver_headers):
    # Happy path: Create Caregiver
    res = client.post(
        "/api/v1/caregivers",
        json={
            "name": "Ramesh Kumar",
            "phone": "+919876543210",
            "whatsapp_id": "whatsapp:+919876543210",
            "relationship": "Son",
            "notify_level": "all",
        },
        headers=caregiver_headers,
    )
    assert res.status_code == status.HTTP_201_CREATED
    cg_id = res.json()["id"]

    # Create patient
    p_res = client.post(
        "/api/v1/patients",
        json={
            "name": "Lakshmi Devi",
            "age": 72,
            "wake_time": "07:00:00",
            "sleep_time": "22:00:00",
        },
        headers=caregiver_headers,
    )
    p_id = p_res.json()["id"]

    # Link caregiver
    link_res = client.post(
        f"/api/v1/patients/{p_id}/caregivers/{cg_id}",
        headers=caregiver_headers,
    )
    assert link_res.status_code == status.HTTP_200_OK

    # 404 on linking to non-existent caregiver
    link_404 = client.post(
        f"/api/v1/patients/{p_id}/caregivers/99999",
        headers=caregiver_headers,
    )
    assert link_404.status_code == status.HTTP_404_NOT_FOUND


def test_medication_and_schedule_flow(client, caregiver_headers):
    # Create patient first
    p_res = client.post(
        "/api/v1/patients",
        json={
            "name": "Lakshmi Devi",
            "age": 72,
            "wake_time": "07:00:00",
            "sleep_time": "22:00:00",
        },
        headers=caregiver_headers,
    )
    p_id = p_res.json()["id"]

    # 404 Medication for unknown patient
    med_404 = client.post(
        "/api/v1/patients/99999/medications",
        json={
            "name": "Metformin",
            "strength": "500mg",
            "form": "tablet",
        },
        headers=caregiver_headers,
    )
    assert med_404.status_code == status.HTTP_404_NOT_FOUND

    # Happy path: Create Medication
    med_res = client.post(
        f"/api/v1/patients/{p_id}/medications",
        json={
            "name": "Metformin",
            "strength": "500mg",
            "form": "tablet",
            "instructions": "after breakfast",
            "stock_remaining": 12,
            "refill_threshold": 10,
        },
        headers=caregiver_headers,
    )
    assert med_res.status_code == status.HTTP_201_CREATED
    med_id = med_res.json()["id"]

    # GET Medication
    get_med = client.get(f"/api/v1/medications/{med_id}", headers=caregiver_headers)
    assert get_med.status_code == status.HTTP_200_OK
    assert get_med.json()["stock_remaining"] == 12

    # PATCH Medication
    patch_med = client.patch(
        f"/api/v1/medications/{med_id}",
        json={"stock_remaining": 20, "instructions": "with plenty of water"},
        headers=caregiver_headers,
    )
    assert patch_med.status_code == status.HTTP_200_OK
    assert patch_med.json()["stock_remaining"] == 20
    assert patch_med.json()["instructions"] == "with plenty of water"

    # POST Schedule
    sched_res = client.post(
        f"/api/v1/medications/{med_id}/schedules",
        json={
            "time_of_day": "08:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "dose_amount": 1.0,
            "start_date": "2026-01-01",
        },
        headers=caregiver_headers,
    )
    assert sched_res.status_code == status.HTTP_201_CREATED
    assert sched_res.json()["medication_id"] == med_id


def test_notes_and_adherence(client, caregiver_headers, patient_headers):
    p_res = client.post(
        "/api/v1/patients",
        json={
            "name": "Lakshmi Devi",
            "age": 72,
            "wake_time": "07:00:00",
            "sleep_time": "22:00:00",
        },
        headers=caregiver_headers,
    )
    p_id = p_res.json()["id"]

    # Note creation
    note_res = client.post(
        f"/api/v1/patients/{p_id}/notes",
        json={
            "text": "Felt slight knee pain in the morning",
            "kind": "symptom",
            "flagged_to_caregiver": True,
        },
        headers=patient_headers,
    )
    assert note_res.status_code == status.HTTP_201_CREATED
    assert note_res.json()["kind"] == "symptom"
    assert note_res.json()["flagged_to_caregiver"] is True

    # Note creation 404 for missing patient
    note_404 = client.post(
        "/api/v1/patients/99999/notes",
        json={"text": "Hello", "kind": "mood"},
        headers=patient_headers,
    )
    assert note_404.status_code == status.HTTP_404_NOT_FOUND

    # Adherence calculation
    adh_res = client.get(
        f"/api/v1/patients/{p_id}/adherence?days=7",
        headers=patient_headers,
    )
    assert adh_res.status_code == status.HTTP_200_OK
    data = adh_res.json()
    assert data["patient_id"] == p_id
    assert data["days"] == 7
    assert data["total_events"] == 0
    assert data["adherence_percentage"] is None
