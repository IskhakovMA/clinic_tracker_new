import json

from .conftest import make_patient_payload, unsigned_import


def _import_doc(patients, schema_version=1):
    return json.dumps({"schemaVersion": schema_version, "patients": patients}, ensure_ascii=False)


def _valid_patient(**overrides):
    p = {
        "id": "legacy-1",
        "name": "Импортов Иван",
        "age": 60,
        "priority": "high",
        "stage": "exam",
        "phone": "+7 (900) 111-22-33",
        "complaint": "жалобы",
        "diagnosis": "C34",
        "nosology": "lung34",
        "diseaseStage": "III",
        "arrival": "2026-07-01T10:00:00+00:00",
        "doctor": "Др. Импортный",
        "head": "Шашолин Михаил Аркадьевич",
        "history": [
            {
                "stage": "exam",
                "enteredAt": "2026-07-01T10:00:00+00:00",
                "events": [{"date": "2026-07-10", "time": "09:30", "treatmentType": None}],
                "checklist": {"Общий анализ крови": True},
                "note": "",
            }
        ],
        "futurePlans": {},
    }
    p.update(overrides)
    return p


def test_valid_import_add_mode(auth_client):
    raw = _import_doc([_valid_patient()])
    r = auth_client.post("/api/import/preview", json={"raw": raw})
    assert r.status_code == 200
    assert r.json()["valid"] is True
    assert r.json()["totalRecords"] == 1

    r = unsigned_import(auth_client, raw, "add")
    assert r.status_code == 200
    assert r.json() == {"added": 1, "updated": 0, "skipped": 0, "unchanged": 0, "appendedEvents": 0,
                        "provenance": "unsigned_external"}

    patients = auth_client.get("/api/patients").json()
    assert len(patients) == 1
    assert patients[0]["name"] == "Импортов Иван"
    assert patients[0]["id"] != "legacy-1"  # server minted a fresh id
    assert patients[0]["history"][0]["checklist"] == {"Общий анализ крови": True}


def test_legacy_bare_array_accepted(auth_client):
    raw = json.dumps([_valid_patient()], ensure_ascii=False)
    r = auth_client.post("/api/import/preview", json={"raw": raw})
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_corrupt_json_rejected(auth_client):
    r = auth_client.post("/api/import/preview", json={"raw": "{not json"})
    assert r.status_code == 400


def test_wrong_root_type_rejected(auth_client):
    assert auth_client.post("/api/import/preview", json={"raw": '"a string"'}).status_code == 400
    assert auth_client.post("/api/import/preview", json={"raw": "42"}).status_code == 400
    assert auth_client.post("/api/import/preview", json={"raw": '{"nope": []}'}).status_code == 400


def test_unknown_schema_version_rejected(auth_client):
    raw = _import_doc([_valid_patient()], schema_version=99)
    assert auth_client.post("/api/import/preview", json={"raw": raw}).status_code == 400


def test_too_many_records_rejected(auth_client):
    patients = [_valid_patient(id=f"p{i}", history=[]) for i in range(501)]
    raw = _import_doc(patients)
    assert auth_client.post("/api/import/preview", json={"raw": raw}).status_code == 400


def test_invalid_records_reported_with_index_and_field(auth_client):
    patients = [
        _valid_patient(id="a"),
        _valid_patient(id="b", priority="nonsense"),
        _valid_patient(id="c", name=""),
    ]
    r = auth_client.post("/api/import/preview", json={"raw": _import_doc(patients)})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    indices = {e["index"] for e in body["errors"]}
    assert 1 in indices and 2 in indices


def test_partially_invalid_set_not_committed(auth_client):
    patients = [_valid_patient(id="ok"), _valid_patient(id="bad", stage="no-such")]
    r = unsigned_import(auth_client, _import_doc(patients), "add")
    assert r.status_code == 422
    assert auth_client.get("/api/patients").json() == []


def test_bad_dates_rejected(auth_client):
    p = _valid_patient()
    p["history"][0]["events"][0]["date"] = "not-a-date"
    r = auth_client.post("/api/import/preview", json={"raw": _import_doc([p])})
    assert r.json()["valid"] is False


def test_duplicate_ids_rejected(auth_client):
    patients = [_valid_patient(id="dup"), _valid_patient(id="dup", name="Другой")]
    r = auth_client.post("/api/import/preview", json={"raw": _import_doc(patients)})
    assert r.json()["valid"] is False
    assert any(e["field"] == "id" for e in r.json()["errors"])


def test_dangerous_keys_rejected(auth_client):
    p = _valid_patient()
    p["futurePlans"] = {"__proto__": {"date": "2026-01-01", "time": "", "treatmentType": ""}}
    r = auth_client.post("/api/import/preview", json={"raw": _import_doc([p])})
    assert r.json()["valid"] is False


def test_oversized_strings_rejected(auth_client):
    r = auth_client.post("/api/import/preview", json={"raw": _import_doc([_valid_patient(name="x" * 201)])})
    assert r.json()["valid"] is False


def test_update_mode_only_touches_matches(auth_client):
    existing = auth_client.post(
        "/api/patients", json=make_patient_payload(), headers=auth_client.csrf
    ).json()

    # Изменение существующих значений — конфликт (решение A): карточка не меняется.
    patients = [
        _valid_patient(id=existing["id"], name="Обновлённый Пациент"),
        _valid_patient(id="never-seen", name="Чужой"),
    ]
    r = unsigned_import(auth_client, _import_doc(patients), "update")
    assert r.status_code == 409 and "обновление не выполнено" in r.json()["detail"]
    all_patients = auth_client.get("/api/patients").json()
    assert len(all_patients) == 1
    assert all_patients[0]["id"] == existing["id"]
    assert all_patients[0]["name"] == existing["name"]


def test_replace_mode_only_into_an_empty_database(auth_client):
    auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf)
    auth_client.post("/api/patients", json=make_patient_payload(name="Второй"), headers=auth_client.csrf)

    r = unsigned_import(auth_client, _import_doc([_valid_patient()]), "replace")
    assert r.status_code == 409 and "Полная замена заблокирована" in r.json()["detail"]
    assert len(auth_client.get("/api/patients").json()) == 2
    for patient in auth_client.get("/api/patients").json():
        assert auth_client.delete(f"/api/patients/{patient['id']}", headers=auth_client.csrf).status_code == 200
    r = unsigned_import(auth_client, _import_doc([_valid_patient()]), "replace")
    assert r.status_code == 200 and r.json()["added"] == 1
    all_patients = auth_client.get("/api/patients").json()
    assert len(all_patients) == 1
    assert all_patients[0]["name"] == "Импортов Иван"


def test_import_commit_requires_csrf(auth_client):
    raw = _import_doc([_valid_patient()])
    assert auth_client.post("/api/import/commit", json={"raw": raw, "mode": "add"}).status_code == 403


def test_migration_endpoint_blocked_when_server_has_data(auth_client):
    auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf)
    raw = _import_doc([_valid_patient()])
    r = auth_client.post(
        "/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=auth_client.csrf
    )
    assert r.status_code == 409


def test_migration_works_on_empty_server(auth_client):
    raw = json.dumps([_valid_patient()], ensure_ascii=False)  # legacy bare-array shape
    r = auth_client.post("/api/migrate/localstorage/preview", json={"raw": raw})
    assert r.json()["valid"] is True
    r = auth_client.post(
        "/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=auth_client.csrf
    )
    assert r.status_code == 200
    assert len(auth_client.get("/api/patients").json()) == 1
