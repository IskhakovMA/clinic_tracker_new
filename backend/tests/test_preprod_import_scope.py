"""Pre-production hardening: import preview and provenance respect the actor.

Regression tests for audit leads NV-08 (import/migration preview counted
patient ids across the whole database, revealing whether out-of-scope ids
exist) and NV-07 (a scoped clinician's import stored any user id as the
author of diagnosis changes).
"""
import json

from .conftest import create_account, get_conn, login, unsigned_import
from .test_import import _import_doc, _valid_patient


def _patient_for(doctor_id: str) -> str:
    from backend.app import store
    from backend.app.models import PatientCreateIn
    from .conftest import make_patient_payload
    return store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(treatingDoctorUserId=doctor_id)), doctor_id)


def test_import_preview_does_not_reveal_out_of_scope_ids(client):
    doc1 = create_account("doc.one", "doctor")
    doc2 = create_account("doc.two", "doctor")
    foreign = _patient_for(doc2)
    own = _patient_for(doc1)
    assert login(client, "doc.one").status_code == 200
    assert client.get(f"/api/patients/{foreign}").status_code == 404

    # Решение B: файл с внутренними id врач не импортирует вовсе — ответ одинаков
    # для чужого, своего и несуществующего id и не зависит от базы.
    for path in ("/api/import/preview", "/api/migrate/localstorage/preview"):
        answers = []
        for record_id in (foreign, own, "f" * 32):
            r = client.post(path, json={"raw": _import_doc([_valid_patient(id=record_id)])})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["valid"] is False and body["code"] == "internal_ids_forbidden", path
            assert "existingMatches" not in body
            answers.append({k: v for k, v in body.items() if k != "provenance"})
        assert answers[0] == answers[1] == answers[2], path
    raw = _import_doc([{k: v for k, v in _valid_patient().items() if k != "id"}])
    assert client.post("/api/import/preview", json={"raw": raw}).json()["existingMatches"] == 0


def test_migration_preview_applies_import_scope_checks(client):
    doc2 = create_account("doc.two", "doctor")
    create_account("doc.one", "doctor")
    assert login(client, "doc.one").status_code == 200
    raw = _import_doc([_valid_patient(treatingDoctorUserId=doc2)])
    r = client.post("/api/migrate/localstorage/preview", json={"raw": raw})
    assert r.status_code == 200
    assert r.json()["valid"] is False


def test_clinician_import_cannot_forge_diagnosis_change_author(client):
    head = create_account("head.one", "department_head")
    doc1 = create_account("doc.one", "doctor")
    assert login(client, "doc.one").status_code == 200
    forged = _valid_patient(diagnosisHistory=[{
        "id": "x", "patientId": "x", "changedAt": "2020-01-01T00:00:00Z",
        "actorUserId": head, "oldDiagnosis": "D44.0", "newDiagnosis": "C73",
    }])
    # История диагноза несёт внутренние id (id, patientId): врач такой файл не
    # импортирует (решение B), поэтому и приписать автора некому.
    r = unsigned_import(client, _import_doc([forged]), "add")
    assert r.status_code == 403, r.text
    assert get_conn().execute("SELECT COUNT(*) FROM patient_diagnosis_history").fetchone()[0] == 0
    assert doc1


def test_clinician_import_with_unknown_author_does_not_500(client):
    doc1 = create_account("doc.one", "doctor")
    assert login(client, "doc.one").status_code == 200
    forged = _valid_patient(diagnosisHistory=[{
        "id": "x", "patientId": "x", "changedAt": "2020-01-01T00:00:00Z",
        "actorUserId": "no-such-user", "oldDiagnosis": "D44.0", "newDiagnosis": "C73",
    }])
    r = unsigned_import(client, _import_doc([forged]), "add")
    assert r.status_code == 403, r.text


def test_developer_import_keeps_known_author_and_drops_unknown(auth_client):
    known = create_account("doc.known", "doctor")
    rows = [
        {"id": "a", "patientId": "a", "changedAt": "2020-01-01T00:00:00Z", "actorUserId": known, "oldDiagnosis": "D44.0", "newDiagnosis": "C73"},
        {"id": "b", "patientId": "b", "changedAt": "2020-02-01T00:00:00Z", "actorUserId": "no-such-user", "oldDiagnosis": "C73", "newDiagnosis": "C73.9"},
    ]
    r = unsigned_import(auth_client, _import_doc([_valid_patient(diagnosisHistory=rows)]), "add")
    assert r.status_code == 200, r.text
    authors = [row[0] for row in get_conn().execute("SELECT actor_user_id FROM patient_diagnosis_history ORDER BY changed_at")]
    assert authors == [known, None]
