"""Full integration smoke: the entire operator workflow in one scenario."""
import json

from .conftest import card_version, DEV_USERNAME, TEST_PASSWORD, create_account, login, make_patient_payload, event_add_body


def test_full_scenario(client):
    # 1. Backend is up: login page served
    r = client.get("/")
    assert r.status_code == 200
    assert "loginScreen" in r.text
    # frontend must not contain the old hardcoded credential check
    js = client.get("/clinic-v2.js").text
    assert "=== 'password'" not in js
    assert "normalizedPassword" not in js
    assert "admin:" not in js  # the old hardcoded users object

    # 2. Login (персональная учётная запись Разработчика)
    create_account(DEV_USERNAME)
    r = login(client, DEV_USERNAME)
    assert r.status_code == 200
    csrf = {"X-CSRF-Token": client.cookies.get("clinic_csrf")}

    # 3. Create
    created = client.post("/api/patients", json=make_patient_payload(), headers=csrf).json()
    pid = created["id"]

    # 4. Read
    assert client.get(f"/api/patients/{pid}").json()["name"] == created["name"]

    # 5. Update
    r = client.patch(f"/api/patients/{pid}", json={"expectedVersion": card_version(pid), "field": "doctor", "value": "Др. Смоуков"}, headers=csrf)
    assert r.json()["doctor"] == "Др. Смоуков"

    # 6. Add event
    r = client.post(f"/api/patients/{pid}/history/0/events", json=event_add_body(client, pid, 0), headers=csrf)
    assert len(r.json()["history"][0]["events"]) == 1

    # 7. Export
    exported = client.get("/api/export", headers=csrf)
    assert exported.status_code == 200
    doc = json.loads(exported.text)
    assert doc["patients"][0]["id"] == pid

    # 8. Delete — WP-C FIX1-P1: карточка с диагнозом и жалобами физически не удаляется.
    r = client.delete(f"/api/patients/{pid}", headers=csrf)
    assert r.status_code == 409 and r.json()["code"] == "PATIENT_HAS_CLINICAL_HISTORY"
    assert [p["id"] for p in client.get("/api/patients").json()] == [pid]

    # 9. Import the export back — та же карточка совпадает с файлом, дубль не создаётся.
    assert client.post("/api/import/commit", json={"raw": exported.text, "mode": "add"}, headers=csrf).status_code == 409
    r = client.post("/api/import/commit", json={"raw": exported.text, "mode": "update"}, headers=csrf)
    assert r.status_code == 200 and r.json()["unchanged"] == 1 and r.json()["added"] == 0

    # 10. Logout
    assert client.post("/api/auth/logout", headers=csrf).status_code == 200

    # 11. API is closed after logout
    assert client.get("/api/patients").status_code == 401
    assert client.get("/api/export").status_code == 401
    assert client.post("/api/patients", json=make_patient_payload(), headers=csrf).status_code == 401
