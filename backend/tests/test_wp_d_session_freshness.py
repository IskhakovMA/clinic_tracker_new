"""FIX2: повторный вход возвращает свежую карточку и не обновляет версию старой правки."""
import pytest

from .conftest import DEV_USERNAME, create_account, login, make_patient_payload, stage_body


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged-manual-save", "changed-manual-conflict"])
def test_reauthentication_returns_server_record_with_original_draft_precondition(auth_client, changed):
    doctor = create_account("freshness.doctor", "doctor", "Синтетический Врач")
    response = auth_client.post("/api/patients", headers=auth_client.csrf,
                                json=make_patient_payload(diagnosis="C34.1", complaint="Исходный комментарий",
                                                          treatingDoctorUserId=doctor))
    assert response.status_code == 201
    original = response.json()
    assert login(auth_client, "freshness.doctor").status_code == 200
    assert auth_client.get(f"/api/patients/{original['id']}").json() == original
    draft = {"field": "complaint", "value": "Несохраненная правка", "expectedVersion": original["version"]}
    assert login(auth_client, DEV_USERNAME).status_code == 200
    expected = original
    if changed:
        response = auth_client.patch(f"/api/patients/{original['id']}", headers=auth_client.csrf,
                                     json={"field": "diagnosis", "value": "C34.9", "expectedVersion": original["version"]})
        assert response.status_code == 200
        response = auth_client.post(f"/api/patients/{original['id']}/stage", headers=auth_client.csrf,
                                    json=stage_body(auth_client, original["id"], {"newStage": "appointment"}))
        assert response.status_code == 200
        expected = response.json()
    assert auth_client.post(f"/api/users/{doctor}/terminate-sessions", json={}, headers=auth_client.csrf).status_code == 200
    assert login(auth_client, "freshness.doctor").status_code == 200
    profile = auth_client.get("/api/auth/me").json()["user"]
    headers = {**auth_client.csrf, "X-Clinic-User": profile["id"], "X-Clinic-Access": profile["accessContext"]}
    records = auth_client.get("/api/patients", headers=headers)
    assert records.status_code == 200
    assert next(p for p in records.json() if p["id"] == original["id"]) == expected
    assert auth_client.get(f"/api/patients/{original['id']}", headers=headers).json() == expected
    response = auth_client.patch(f"/api/patients/{original['id']}", json=draft, headers=headers)
    if changed:
        assert response.status_code == 409
        assert response.json()["code"] == "STALE_PATIENT_VERSION"
        assert auth_client.get(f"/api/patients/{original['id']}", headers=headers).json() == expected
    else:
        assert response.status_code == 200
        assert response.json()["complaint"] == draft["value"]
        assert response.json()["version"] == original["version"] + 1
