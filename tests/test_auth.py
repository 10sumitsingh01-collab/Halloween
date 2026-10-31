from fastapi import status


def test_auth_missing_header_rejected(client):
    response = client.get("/api/v1/patients/1")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert data["error"] == "Unauthorized"
    assert "Missing X-API-Key header" in data["detail"]


def test_auth_invalid_header_rejected(client):
    response = client.get(
        "/api/v1/patients/1",
        headers={"X-API-Key": "completely_bogus_key"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    data = response.json()
    assert data["error"] == "Forbidden"


def test_caregiver_endpoints_reject_patient_key(client, patient_headers):
    # Patient key should be rejected for POST /patients
    res = client.post(
        "/api/v1/patients",
        json={
            "name": "Test Patient",
            "age": 70,
            "wake_time": "08:00:00",
            "sleep_time": "22:00:00",
        },
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "Caregiver privileges required" in res.json()["detail"]

    # Patient key rejected for POST /caregivers
    res = client.post(
        "/api/v1/caregivers",
        json={
            "name": "Test Caregiver",
            "phone": "+1234567890",
            "whatsapp_id": "wa:1234567890",
            "relationship": "Daughter",
        },
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Patient key rejected for POST /patients/1/medications
    res = client.post(
        "/api/v1/patients/1/medications",
        json={
            "name": "Aspirin",
            "strength": "75mg",
            "form": "tablet",
        },
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Patient key rejected for PATCH /medications/1
    res = client.patch(
        "/api/v1/medications/1",
        json={"stock_remaining": 50},
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Patient key rejected for POST /medications/1/schedules
    res = client.post(
        "/api/v1/medications/1/schedules",
        json={
            "time_of_day": "09:00:00",
            "days_of_week": "daily",
            "dose_amount": 1,
            "start_date": "2026-01-01",
        },
        headers=patient_headers,
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_shared_endpoints_accept_patient_key(client, caregiver_headers, patient_headers):
    # 1. Create a patient with caregiver key
    create_res = client.post(
        "/api/v1/patients",
        json={
            "name": "Sita Raman",
            "age": 68,
            "wake_time": "06:30:00",
            "sleep_time": "21:30:00",
        },
        headers=caregiver_headers,
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    pid = create_res.json()["id"]

    # 2. Patient key CAN read patient details
    get_res = client.get(f"/api/v1/patients/{pid}", headers=patient_headers)
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["name"] == "Sita Raman"

    # 3. Patient key CAN create a patient note
    note_res = client.post(
        f"/api/v1/patients/{pid}/notes",
        json={"text": "Felt good after breakfast", "kind": "mood"},
        headers=patient_headers,
    )
    assert note_res.status_code == status.HTTP_201_CREATED
    assert note_res.json()["text"] == "Felt good after breakfast"

    # 4. Patient key CAN query adherence
    adh_res = client.get(
        f"/api/v1/patients/{pid}/adherence?days=7", headers=patient_headers
    )
    assert adh_res.status_code == status.HTTP_200_OK
