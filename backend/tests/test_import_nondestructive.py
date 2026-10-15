"""WP2.1, решения A и C: update/replace не уничтожают и не перезаписывают
существующие медицинские данные; создание карточки с прежними внутренними id
подписанного файла — только разработчиком, с отдельным подтверждением и
обязательным аудитом.

Только синтетические данные; каждая проверка сравнивает состояние SQLite до
и после запроса."""
import json
import sqlite3
import threading

import pytest

from .conftest import (
    DEV_USERNAME,
    create_account,
    create_department,
    get_conn,
    legacy_export,
    login,
    make_patient_payload,
    preserve_ids_confirmation,
    stage_body,
    unsigned_import,
)


def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _export(client):
    r = client.get("/api/export", headers=client.csrf)
    assert r.status_code == 200, r.text
    return r.text


def _snapshot():
    conn = get_conn()
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]  # nosec B608
        for table in ("patients", "history_entries", "events", "patient_diagnosis_history",
                      "oncology_consilium_appointments", "notifications")
    }


def _events(client, pid, occ=0):
    return client.get(f"/api/patients/{pid}").json()["history"][occ]["events"]


def _variant(raw, signed):
    """Тот же файл подписанным или в прежнем неподписанном формате (без id маршрута)."""
    return raw if signed else json.dumps(legacy_export(json.loads(raw)), ensure_ascii=False)


def _update(client, raw, signed):
    if signed:
        return client.post("/api/import/commit", json={"raw": raw, "mode": "update"}, headers=client.csrf)
    return unsigned_import(client, raw, "update")


def _patch(client, pid, event, field, value):
    return client.patch(f"/api/patients/{pid}/events/{event['id']}",
                        json={"field": field, "value": value, "version": event["version"]}, headers=client.csrf)


# A1–A3. Новые события, фактические даты и этапы после выгрузки ------------------

@pytest.mark.parametrize("signed", [True, False])
def test_old_update_does_not_destroy_an_event_added_after_export(auth_client, signed):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    old = _variant(_export(auth_client), signed)
    current = [e["id"] for e in _events(auth_client, pid)]
    r = auth_client.post(f"/api/patients/{pid}/history/0/events",
                         json={"expectedEventIds": current, "purpose": "add"}, headers=auth_client.csrf)
    assert r.status_code == 200
    third = r.json()["createdEventId"]
    before = _snapshot()
    preview = auth_client.post("/api/import/preview", json={"raw": old}, headers=auth_client.csrf).json()
    assert preview["overwriteImpact"]["update"]["blocked"] is True
    r = _update(auth_client, old, signed)
    assert r.status_code == 409 and "Во избежание потери медицинской истории" in r.json()["detail"]
    assert _snapshot() == before
    assert get_conn().execute("SELECT deleted_at FROM events WHERE id = ?", (third,)).fetchone()["deleted_at"] is None


@pytest.mark.parametrize("signed", [True, False])
def test_old_update_does_not_roll_back_a_newer_actual_date(auth_client, signed):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    old = _variant(_export(auth_client), signed)
    event = _events(auth_client, pid)[0]
    assert _patch(auth_client, pid, event, "actualDate", "2030-01-11").status_code == 200
    before = _snapshot()
    assert _update(auth_client, old, signed).status_code == 409
    assert _snapshot() == before
    assert _events(auth_client, pid)[0]["actualDate"] == "2030-01-11"


@pytest.mark.parametrize("signed", [True, False])
def test_old_update_does_not_remove_a_newer_history_entry(auth_client, signed):
    pid = _create(auth_client, events=[{"date": "2030-01-10", "actualDate": "2030-01-10"}])["id"]
    old = _variant(_export(auth_client), signed)
    r = auth_client.post(f"/api/patients/{pid}/stage",
                         json=stage_body(auth_client, pid, {"newStage": "appointment"}), headers=auth_client.csrf)
    assert r.status_code == 200
    before = _snapshot()
    assert _update(auth_client, old, signed).status_code == 409
    assert _snapshot() == before
    assert [h["stage"] for h in auth_client.get(f"/api/patients/{pid}").json()["history"]] == ["exam", "appointment"]


def test_update_that_changes_patient_values_is_a_conflict(auth_client):
    pid = _create(auth_client, name="Исходное Имя")["id"]
    document = legacy_export(json.loads(_export(auth_client)))
    document["patients"][0]["diagnosis"] = "C50.1"
    before = _snapshot()
    assert unsigned_import(auth_client, json.dumps(document, ensure_ascii=False), "update").status_code == 409
    assert _snapshot() == before


# Разрешённое: совпадающая карточка и дописанные новые события -----------------

def test_identical_update_is_a_no_op_and_new_events_are_appended(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    document = legacy_export(json.loads(_export(auth_client)))
    raw = json.dumps(document, ensure_ascii=False)
    before = _snapshot()
    r = unsigned_import(auth_client, raw, "update")
    assert r.status_code == 200 and r.json()["unchanged"] == 1 and r.json()["updated"] == 0
    assert _snapshot()["events"] == before["events"]
    document["patients"][0]["history"][0]["events"].append({"date": "2030-03-01", "time": "09:00"})
    raw = json.dumps(document, ensure_ascii=False)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["overwriteImpact"]["update"] == {"blocked": False, "conflicts": 0, "unchanged": 0,
                                                    "appendedEvents": 1, "updated": 1, "reason": ""}
    r = unsigned_import(auth_client, raw, "update")
    assert r.status_code == 200 and r.json()["updated"] == 1 and r.json()["appendedEvents"] == 1
    events = _events(auth_client, pid)
    assert [e["date"] for e in events] == ["2030-01-10", "2030-03-01"]
    assert events[0]["id"] == before["events"][0][0]                   # прежнее событие не пересоздано
    # Повтор того же файла — карточка уже совпадает, дубликата нет.
    assert unsigned_import(auth_client, raw, "update").json()["unchanged"] == 1
    assert len(_events(auth_client, pid)) == 2


# A4, A9. Replace ---------------------------------------------------------------

@pytest.mark.parametrize("signed", [True, False])
def test_replace_never_deletes_existing_patients(auth_client, signed):
    _create(auth_client, name="Первый", events=[{"date": "2030-01-10"}])
    raw = _variant(_export(auth_client), signed)
    _create(auth_client, name="Не В Файле", events=[{"date": "2030-02-10"}])
    before = _snapshot()
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["overwriteImpact"]["replace"]["blocked"] is True
    body = {"raw": raw, "mode": "replace", "preserveIdsConfirmation": preserve_ids_confirmation(raw, "replace")}
    r = auth_client.post("/api/import/commit", json=body, headers=auth_client.csrf) if signed \
        else unsigned_import(auth_client, raw, "replace")
    assert r.status_code == 409 and "Полная замена заблокирована" in r.json()["detail"]
    assert _snapshot() == before


def test_replace_into_an_empty_database_still_works(auth_client):
    raw = json.dumps({"schemaVersion": 20, "patients": [make_patient_payload(name="Первый В Базе")]},
                     ensure_ascii=False)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["overwriteImpact"]["replace"]["blocked"] is False
    r = unsigned_import(auth_client, raw, "replace")
    assert r.status_code == 200 and r.json()["added"] == 1


# A8, A12, A14. Транзакции и гонки ---------------------------------------------

def test_change_between_preview_and_commit_is_caught(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    raw = _export(auth_client)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["overwriteImpact"]["update"]["blocked"] is False
    event = _events(auth_client, pid)[0]
    assert _patch(auth_client, pid, event, "time", "08:30").status_code == 200   # после preview
    before = _snapshot()
    r = auth_client.post("/api/import/commit", json={"raw": raw, "mode": "update"}, headers=auth_client.csrf)
    assert r.status_code == 409
    assert _snapshot() == before


def test_concurrent_updates_append_once(auth_client):
    from backend.app import importexport, policy

    _create(auth_client, events=[{"date": "2030-01-10"}])
    document = legacy_export(json.loads(_export(auth_client)))
    document["patients"][0]["history"][0]["events"].append({"date": "2030-04-01"})
    parsed, errors = importexport.validate_import(json.dumps(document, ensure_ascii=False))
    assert not errors
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    dev_id = get_conn().execute("SELECT id FROM users WHERE username = ?", (DEV_USERNAME,)).fetchone()["id"]
    results, barrier = [], threading.Barrier(3)

    def run():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        barrier.wait()
        try:
            results.append(importexport.commit_import(conn, parsed, "update", policy.load_current_user(conn, dev_id)))
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted((r["updated"], r["unchanged"]) for r in results) == [(0, 1), (0, 1), (1, 0)]
    assert get_conn().execute("SELECT COUNT(*) FROM events WHERE date = '2030-04-01'").fetchone()[0] == 1


def test_failure_while_appending_rolls_everything_back(auth_client, monkeypatch):
    from backend.app import store
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    document = legacy_export(json.loads(_export(auth_client)))
    document["patients"][0]["history"][0]["events"] += [{"date": "2030-05-01"}, {"date": "2030-06-01"}]
    original, calls = store.insert_event, []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("synthetic failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "insert_event", failing)
    before = _snapshot()
    r = unsigned_import(auth_client, json.dumps(document, ensure_ascii=False), "update")
    assert r.status_code == 503
    assert _snapshot() == before
    assert len(_events(auth_client, pid)) == 1


def test_signed_consilium_records_stay_protected(client):
    from .test_consilium_appointments import appointment_payload
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    pid = _create(client, events=[{"date": "2030-01-10"}])["id"]
    assert client.post("/api/consilium/appointments", json=appointment_payload(pid),
                       headers=client.csrf).status_code in (200, 201)
    raw = _export(client)
    before = _snapshot()
    for mode in ("update", "replace"):
        r = client.post("/api/import/commit", json={"raw": raw, "mode": mode}, headers=client.csrf)
        assert r.status_code == 409, mode
    assert _snapshot() == before


# C. Создание карточки с сохранением внутренних id -------------------------------

def _deleted_patient_file(client):
    pid = _create(client, name="Удалённая Карточка", events=[{"date": "2030-01-10"}])["id"]
    raw = _export(client)
    assert client.delete(f"/api/patients/{pid}", headers=client.csrf).status_code == 200
    return pid, raw


def _commit(client, raw, mode, **extra):
    return client.post("/api/import/commit", json={"raw": raw, "mode": mode, **extra}, headers=client.csrf)


def test_developer_creates_card_with_internal_ids_only_with_explicit_confirmation(auth_client):
    pid, raw = _deleted_patient_file(auth_client)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["idPreservation"]["add"]["required"] is True
    assert "не подтверждает" in preview["idPreservation"]["add"]["message"]
    before = _snapshot()
    assert _commit(auth_client, raw, "add").status_code == 428                                    # 34
    other = json.dumps({**json.loads(raw), "generatedAt": "x"})
    assert _commit(auth_client, raw, "add",                                                       # 36
                   preserveIdsConfirmation=preserve_ids_confirmation(other, "add")).status_code == 428
    assert _commit(auth_client, raw, "add",                                                       # 37
                   preserveIdsConfirmation=preserve_ids_confirmation(raw, "replace")).status_code == 428
    # Подтверждение происхождения — не разрешение на сохранение id (C5).
    assert _commit(auth_client, raw, "add", unsignedConfirmation={
        "confirmed": True, "fileSha256": preserve_ids_confirmation(raw, "add")["fileSha256"], "mode": "add",
    }).status_code == 428
    assert _snapshot() == before
    r = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))  # 33, 35
    assert r.status_code == 200 and r.json()["added"] == 1
    assert auth_client.get(f"/api/patients/{pid}").status_code == 200
    # 39, 43. Повтор — конфликт, дублей id нет.
    again = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))
    assert again.status_code == 409
    assert get_conn().execute("SELECT COUNT(*) FROM patients WHERE id = ?", (pid,)).fetchone()[0] == 1


def test_doctor_cannot_use_internal_id_creation(client):
    dept = create_department("Восстановление")
    create_account("rec.doc", "doctor", department_ids=[dept])
    login(client, "rec.doc")
    pid, raw = _deleted_patient_file(client)
    before = _snapshot()
    r = _commit(client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))   # 38
    assert r.status_code == 403
    assert _snapshot() == before


def test_deleted_events_are_not_restored_through_id_creation(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    raw = _export(auth_client)
    event = _events(auth_client, pid)[0]
    assert auth_client.delete(f"/api/patients/{pid}/events/{event['id']}?version={event['version']}",
                              headers=auth_client.csrf).status_code == 200
    before = _snapshot()
    r = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))  # 40
    assert r.status_code == 409
    assert _snapshot() == before
    row = get_conn().execute("SELECT deleted_at, deleted_by_user_id FROM events WHERE id = ?", (event["id"],)).fetchone()
    assert row["deleted_at"] and row["deleted_by_user_id"]


def test_internal_id_creation_is_audited_and_rolled_back_without_audit(auth_client):
    pid, raw = _deleted_patient_file(auth_client)
    history_before = [tuple(r) for r in get_conn().execute(
        "SELECT id, event_type, detail FROM audit_events WHERE patient_id = ? ORDER BY id", (pid,))]
    with get_conn() as conn:
        conn.execute("""CREATE TRIGGER test_preserve_audit_failure BEFORE INSERT ON audit_events
                        WHEN NEW.event_type = 'import_internal_ids_preserved'
                        BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")
    before = _snapshot()
    r = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))  # 42
    assert r.status_code == 503 and _snapshot() == before
    with get_conn() as conn:
        conn.execute("DROP TRIGGER test_preserve_audit_failure")
    r = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))
    assert r.status_code == 200
    rows = [dict(row) for row in get_conn().execute(
        "SELECT event_type, result, detail, actor_user_id, patient_id FROM audit_events "
        "WHERE event_type = 'import_internal_ids_preserved' ORDER BY id")]
    assert len(rows) == 1 and rows[0]["result"] == "ok" and rows[0]["actor_user_id"]               # 41
    assert rows[0]["patient_id"] == pid                       # видно, какая карточка создана с прежним id
    detail = rows[0]["detail"]
    for part in ("action=create_with_internal_ids", "mode=add", "cards=1", "provenance=verified_signed",
                 "key_id=test-export-key", "preserve_confirmed=1", "file_sha256="):
        assert part in detail, part
    for secret in ("Удалённая Карточка", "2030-01-10", "C34", "восстанов"):
        assert secret not in detail
    # 44. Прежние записи аудита не переписываются; новая запись о создании
    # с прежним id — граница между ними.
    after = [tuple(r) for r in get_conn().execute(
        "SELECT id, event_type, detail FROM audit_events WHERE patient_id = ? ORDER BY id", (pid,))]
    assert after[:len(history_before)] == history_before
    assert [r[1] for r in after[len(history_before):]] == ["import_internal_ids_preserved"]


# Замечания ревью: дописывание только планового события в текущий этап ------------

def _append_case(client, mutate, stage_moves=()):
    pid = _create(client, events=[{"date": "2030-01-10", "actualDate": "2030-01-10"}])["id"]
    for stage in stage_moves:
        r = client.post(f"/api/patients/{pid}/stage", json=stage_body(client, pid, {"newStage": stage}),
                        headers=client.csrf)
        assert r.status_code == 200, r.text
    document = legacy_export(json.loads(_export(client)))
    document["patients"] = [p for p in document["patients"] if p["id"] == pid]
    mutate(document["patients"][0])
    before = _snapshot()
    r = unsigned_import(client, json.dumps(document, ensure_ascii=False), "update")
    return r, before


@pytest.mark.parametrize("event", [
    {"date": "2030-03-01", "actualDate": "2030-03-01"},
    {"date": "2030-03-01", "consiliumDecision": "treatment_strategy_changed"},
    {"date": "2030-03-01", "matchedPlan": True},
    {"date": "2030-03-01", "noPlan": True},
])
def test_appended_events_cannot_carry_facts_or_decisions(auth_client, event):
    r, before = _append_case(auth_client, lambda p: p["history"][-1]["events"].append(event), ("appointment",))
    assert r.status_code == 409 and _snapshot() == before


def test_events_cannot_be_appended_to_past_stages_or_finished_routes(auth_client):
    r, before = _append_case(auth_client, lambda p: p["history"][0]["events"].append({"date": "2030-03-01"}),
                             ("appointment",))
    assert r.status_code == 409 and _snapshot() == before
    r, before = _append_case(auth_client, lambda p: p["history"][-1]["events"].append({"date": "2030-03-01"}),
                             ("appointment", "completed"))
    assert r.status_code == 409 and _snapshot() == before
    r, _ = _append_case(auth_client, lambda p: p["history"][-1]["events"].append({"date": "2030-03-01"}),
                        ("appointment",))
    assert r.status_code == 200 and r.json()["appendedEvents"] == 1


def test_signed_file_with_inconsistent_structure_is_a_conflict_not_a_server_error(auth_client):
    from backend.app import export_signing
    _create(auth_client, events=[{"date": "2030-01-10"}])
    document = json.loads(_export(auth_client))
    patient = document["patients"][0]
    patient["events"] = patient["history"][0]["events"]
    for event in patient["events"]:
        event.pop("id")
    patient["history"] = []
    unsigned = {k: v for k, v in document.items() if k not in ("format", "formatVersion", "signature")}
    raw = json.dumps(export_signing.sign(unsigned, export_signing.active_key()), ensure_ascii=False)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf)
    assert preview.status_code == 200 and preview.json()["overwriteImpact"]["update"]["blocked"] is True
    before = _snapshot()
    r = auth_client.post("/api/import/commit", json={"raw": raw, "mode": "update"}, headers=auth_client.csrf)
    assert r.status_code == 409 and _snapshot() == before


# F10. Повторный импорт неподписанного файла с существующим patient.id ----------------

def _legacy_reimport_file(client):
    pid = _create(client, name="Повторный Неподписанный", events=[{"date": "2030-01-10"}])["id"]
    document = legacy_export(json.loads(_export(client)))       # без подписи, id этапов и событий
    document["patients"] = [p for p in document["patients"] if p["id"] == pid]
    assert document["patients"][0]["id"] == pid                  # patient.id сохранён
    return pid, json.dumps(document, ensure_ascii=False)


def test_unsigned_add_with_an_existing_patient_id_is_rejected(auth_client):
    pid, raw = _legacy_reimport_file(auth_client)
    preview = auth_client.post("/api/import/preview", json={"raw": raw}, headers=auth_client.csrf).json()
    assert preview["duplicateImpact"]["add"]["blocked"] is True               # preview = commit
    before = _snapshot()
    r = unsigned_import(auth_client, raw, "add")
    assert r.status_code == 409, r.text
    assert "уже есть в базе" in r.json()["detail"]
    assert "Повторный Неподписанный" not in r.text and "2030-01-10" not in r.text
    assert _snapshot() == before                                              # ни пациентов, ни событий


def test_unsigned_add_with_unknown_patient_id_or_without_ids_still_imports(auth_client):
    _, raw = _legacy_reimport_file(auth_client)
    document = json.loads(raw)
    document["patients"][0]["id"] = "9" * 32                                  # несуществующий id
    r = unsigned_import(auth_client, json.dumps(document, ensure_ascii=False), "add")
    assert r.status_code == 200 and r.json()["added"] == 1
    # Неподписанный id не сохраняется и ни с чем не сопоставляется.
    assert get_conn().execute("SELECT COUNT(*) FROM patients WHERE id = ?", ("9" * 32,)).fetchone()[0] == 0
    document["patients"][0].pop("id")                                         # внешний файл без id
    r = unsigned_import(auth_client, json.dumps(document, ensure_ascii=False), "add")
    assert r.status_code == 200 and r.json()["added"] == 1


def test_concurrent_unsigned_reimports_create_nothing(auth_client):
    from backend.app import importexport, policy
    pid, raw = _legacy_reimport_file(auth_client)
    inspection = importexport.inspect_import(raw)
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    dev_id = get_conn().execute("SELECT id FROM users WHERE username = ?", (DEV_USERNAME,)).fetchone()["id"]
    outcomes, barrier = [], threading.Barrier(3)

    def run():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        barrier.wait()
        try:
            importexport.commit_import(conn, inspection.document, "add", policy.load_current_user(conn, dev_id),
                                       ids=inspection.ids)
            outcomes.append("ok")
        except importexport.ImportDuplicateError:
            outcomes.append("duplicate")
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes == ["duplicate"] * 3
    assert get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1


def test_clinicians_get_the_same_refusal_for_existing_and_unknown_patient_ids(client):
    dept = create_department("Повторный Импорт")
    create_account(DEV_USERNAME)
    create_account("reimp.doc", "doctor", department_ids=[dept])
    create_account("reimp.head", "department_head", department_ids=[dept])
    login(client, "reimp.doc")
    pid, raw = _legacy_reimport_file(client)
    unknown = json.loads(raw)
    unknown["patients"][0]["id"] = "8" * 32
    for username in ("reimp.doc", "reimp.head"):
        login(client, username)
        answers = [unsigned_import(client, body, "add") for body in (raw, json.dumps(unknown, ensure_ascii=False))]
        assert [a.status_code for a in answers] == [403, 403]
        assert answers[0].json() == answers[1].json()
    assert get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1
