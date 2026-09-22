"""Pre-production hardening, round 3: update writers match import validation.

Regression test for preprod-run-3 candidate
backend/app/store.py:update_event+update_patient_field+update_checklist_item:import-validation-parity:
PATCH could store an unknown treatmentType, an empty name, a 101st checklist
item or a dangerous checklist key, and import then rejected the whole export.
"""
from .conftest import make_patient_payload


def _new_patient(auth_client):
    payload = make_patient_payload(events=[{"date": "2030-01-10", "time": "10:00"}])
    return auth_client.post("/api/patients", json=payload, headers=auth_client.csrf).json()["id"]


def test_event_treatment_type_is_restricted_to_the_known_values(auth_client):
    pid = _new_patient(auth_client)
    url = f"/api/patients/{pid}/history/0/events/0"
    r = auth_client.patch(url, json={"field": "treatmentType", "value": "x"}, headers=auth_client.csrf)
    assert r.status_code == 422
    r = auth_client.patch(url, json={"field": "treatmentType", "value": "chemo"}, headers=auth_client.csrf)
    assert r.status_code == 200


def test_patient_name_cannot_be_emptied(auth_client):
    pid = _new_patient(auth_client)
    r = auth_client.patch(f"/api/patients/{pid}", json={"field": "name", "value": "  "}, headers=auth_client.csrf)
    assert r.status_code == 422


def test_checklist_cap_and_dangerous_keys_match_import(auth_client):
    pid = _new_patient(auth_client)
    url = f"/api/patients/{pid}/history/0/checklist"
    r = auth_client.patch(url, json={"item": "__proto__", "checked": True}, headers=auth_client.csrf)
    assert r.status_code == 422
    for i in range(100):
        r = auth_client.patch(url, json={"item": f"item-{i}", "checked": True}, headers=auth_client.csrf)
        assert r.status_code == 200, (i, r.text)
    r = auth_client.patch(url, json={"item": "item-100", "checked": True}, headers=auth_client.csrf)
    assert r.status_code == 422


def test_export_of_patched_patient_still_imports(auth_client):
    pid = _new_patient(auth_client)
    auth_client.patch(f"/api/patients/{pid}/history/0/events/0",
                      json={"field": "treatmentType", "value": "chemo"}, headers=auth_client.csrf)
    export = auth_client.get("/api/export", headers=auth_client.csrf)
    assert export.status_code == 200
    r = auth_client.post("/api/import/commit", json={"raw": export.text, "mode": "replace"}, headers=auth_client.csrf)
    assert r.status_code == 200, r.text
