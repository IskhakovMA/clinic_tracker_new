"""WP2.1, F2 (вариант В): подписанный экспорт HMAC-SHA256, подтверждение
неподписанных файлов и обязательный аудит импорта.

Только синтетические данные и синтетические ключи. Каждый сценарий
проверяет ответ и фактическое состояние SQLite."""
import copy
import hashlib
import json
import sqlite3

import pytest

from .conftest import (
    DEV_USERNAME,
    TEST_EXPORT_SIGNING_KEY_ID,
    create_account,
    create_department,
    get_conn,
    legacy_export,
    login,
    make_patient_payload,
    preserve_ids_confirmation,
    synthetic_export_key,
    unsigned_import,
    LEGACY_CONSILIUM_DATES,
    consilium_grid,
)

OTHER_KEY = synthetic_export_key("other-synthetic-export-key-32byt")   # синтетический, 32 байта


# --- вспомогательные --------------------------------------------------------

def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _export(client):
    r = client.get("/api/export", headers=client.csrf)
    assert r.status_code == 200, r.text
    return r.text


def _preview(client, raw):
    r = client.post("/api/import/preview", json={"raw": raw}, headers=client.csrf)
    assert r.status_code == 200, r.text
    return r.json()


def _commit(client, raw, mode, **extra):
    return client.post("/api/import/commit", json={"raw": raw, "mode": mode, **extra}, headers=client.csrf)


def _snapshot():
    conn = get_conn()
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]  # nosec B608
        for table in ("patients", "history_entries", "events", "oncology_consilium_appointments")
    }


def _import_audit():
    return [dict(r) for r in get_conn().execute(
        "SELECT event_type, result, detail, actor_user_id FROM audit_events "
        "WHERE event_type = 'import_committed' ORDER BY id").fetchall()]


def _resign(document):
    """Подписать документ синтетическим ключом тестов (как это делает сервер)."""
    from backend.app import export_signing
    unsigned = {k: v for k, v in document.items() if k not in ("format", "formatVersion", "signature")}
    return export_signing.sign(unsigned, export_signing.active_key())


def _with_deleted_event(client, name="Подписанный Архив"):
    pid = _create(client, name=name, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    event = client.get(f"/api/patients/{pid}").json()["history"][0]["events"][0]
    return pid, event


# 1–2, 37. Формирование и проверка подписи, roundtrip -------------------------

def test_signed_export_contains_stable_ids_and_a_valid_signature(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    raw = _export(auth_client)
    document = json.loads(raw)
    assert document["format"] == "clinic-tracker-export" and document["formatVersion"] == 2
    assert set(document["signature"]) == {"algorithm", "keyId", "value"}
    assert document["signature"]["algorithm"] == "HMAC-SHA256"
    assert document["signature"]["keyId"] == TEST_EXPORT_SIGNING_KEY_ID
    patient = next(p for p in document["patients"] if p["id"] == pid)
    entry = patient["history"][0]
    assert entry["id"] and entry["events"][0]["id"] and "version" not in entry["events"][0]
    from backend.tests.conftest import TEST_EXPORT_SIGNING_KEY
    assert TEST_EXPORT_SIGNING_KEY not in raw                     # ключ в файл не попадает
    preview = _preview(auth_client, raw)
    assert preview["provenance"]["status"] == "verified_signed"
    assert preview["provenance"]["confirmationRequired"] is False
    assert preview["provenance"]["fileSha256"] == hashlib.sha256(raw.encode()).hexdigest()


def test_signed_roundtrip_update_and_replace_work_without_confirmation(auth_client):
    pid = _create(auth_client, name="Подписанный Пациент", events=[{"date": "2030-01-10"}])["id"]
    raw = _export(auth_client)
    r = _commit(auth_client, raw, "update")
    assert r.status_code == 200, r.text
    assert r.json()["unchanged"] == 1 and r.json()["provenance"] == "verified_signed"
    raw = _export(auth_client)
    # Полная замена — только в пустую базу (решение A).
    assert _commit(auth_client, raw, "replace").status_code == 409
    names = [p["name"] for p in auth_client.get("/api/patients").json()]
    assert names == ["Подписанный Пациент"]
    rows = _import_audit()
    assert rows[-1]["result"] == "denied" and "mode=replace overwrite_conflicts=0" in rows[-1]["detail"]
    detail = next(r["detail"] for r in reversed(rows) if r["result"] == "ok")
    assert "provenance=verified_signed" in detail and f"key_id={TEST_EXPORT_SIGNING_KEY_ID}" in detail
    assert "unsigned_confirmed=0" in detail and "unchanged=1" in detail


# 3–7. Подмена содержимого, идентификаторов и подписи --------------------------

@pytest.mark.parametrize("tamper", [
    lambda d: d["patients"][0].__setitem__("diagnosis", "C50.9"),
    lambda d: d["patients"][0].__setitem__("id", "f" * 32),
    lambda d: d["patients"][0]["history"][0]["events"][0].__setitem__("id", "e" * 32),
    lambda d: d["patients"][0]["history"][0].__setitem__("id", "a" * 32),
    lambda d: d["signature"].__setitem__("value", "0" * 64),
    lambda d: d["signature"].__setitem__("value", "zz"),
    lambda d: d.__setitem__("generatedAt", "2020-01-01T00:00:00+00:00"),
    lambda d: d["patients"].append(copy.deepcopy(d["patients"][0])),
])
def test_any_change_to_a_signed_file_is_rejected(auth_client, tamper):
    _create(auth_client, events=[{"date": "2030-01-10"}])
    document = json.loads(_export(auth_client))
    tamper(document)
    raw = json.dumps(document, ensure_ascii=False)
    preview = _preview(auth_client, raw)
    assert preview["valid"] is False and preview["provenance"]["status"] == "invalid_signature"
    before = _snapshot()
    for mode in ("add", "update", "replace"):
        # Подтверждение «неподписанного» импорта не превращает такой файл в допустимый.
        r = unsigned_import(auth_client, raw, mode)
        assert r.status_code == 422 and "Подпись" in r.json()["detail"], mode
    assert _snapshot() == before
    assert _import_audit()[-1]["result"] == "denied" and "provenance=invalid_signature" in _import_audit()[-1]["detail"]


@pytest.mark.parametrize("change, status", [
    (lambda d: d["signature"].__setitem__("keyId", "unknown-key"), "unsupported_signature"),
    (lambda d: d["signature"].__setitem__("algorithm", "HMAC-SHA1"), "unsupported_signature"),
    (lambda d: d.__setitem__("formatVersion", 3), "unsupported_signature"),
    (lambda d: d.__setitem__("formatVersion", True), "unsupported_signature"),
    (lambda d: d.pop("signature"), "invalid_signature"),
    (lambda d: d.pop("format"), "invalid_signature"),
    (lambda d: d["signature"].pop("value"), "invalid_signature"),
])
def test_unknown_key_algorithm_version_or_stripped_signature_is_rejected(auth_client, change, status):
    _create(auth_client, events=[{"date": "2030-01-10"}])
    document = json.loads(_export(auth_client))
    change(document)
    raw = json.dumps(document, ensure_ascii=False)
    assert _preview(auth_client, raw)["provenance"]["status"] == status
    before = _snapshot()
    assert unsigned_import(auth_client, raw, "add").status_code == 422
    assert _snapshot() == before


def test_duplicate_json_keys_are_rejected_before_any_check(auth_client):
    _create(auth_client, events=[{"date": "2030-01-10"}])
    raw = _export(auth_client)
    # Повторный ключ: один парсер увидел бы первое значение, другой — второе.
    ambiguous = raw.replace('"schemaVersion":', '"schemaVersion": 20, "schemaVersion":', 1)
    assert auth_client.post("/api/import/preview", json={"raw": ambiguous}, headers=auth_client.csrf).status_code == 400
    assert _commit(auth_client, ambiguous, "add").status_code == 400
    nan = '{"schemaVersion": 20, "patients": [], "note": NaN}'
    assert auth_client.post("/api/import/preview", json={"raw": nan}, headers=auth_client.csrf).status_code == 400


# 8–12. Ключи ---------------------------------------------------------------

def test_export_fails_closed_without_a_signing_key(auth_client, monkeypatch):
    monkeypatch.delenv("CLINIC_EXPORT_SIGNING_KEY")
    _create(auth_client)
    r = auth_client.get("/api/export", headers=auth_client.csrf)
    assert r.status_code == 503 and "ключ подписи" in r.json()["detail"]
    for bad in ("short", "", "!!!!"):
        monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", bad)
        assert auth_client.get("/api/export", headers=auth_client.csrf).status_code == 503
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", OTHER_KEY)
    monkeypatch.delenv("CLINIC_EXPORT_SIGNING_KEY_ID")
    assert auth_client.get("/api/export", headers=auth_client.csrf).status_code == 503     # нужен key_id
    rows = [r for r in get_conn().execute("SELECT result, detail FROM audit_events WHERE event_type = 'export'")]
    assert rows and all(r["result"] == "error" and r["detail"] == "reason=signing_key_unavailable" for r in rows)


def test_signature_survives_restart_and_rotation_with_retired_keys(auth_client, monkeypatch):
    _create(auth_client, events=[{"date": "2030-01-10"}])
    old_raw = _export(auth_client)
    # «Перезапуск» с тем же ключом: проверка не зависит от состояния процесса.
    from backend.app import export_signing
    assert export_signing.verify(json.loads(old_raw))[0] == "verified_signed"
    # Ротация: новый активный ключ; старый — только для проверки.
    from backend.tests.conftest import TEST_EXPORT_SIGNING_KEY
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", OTHER_KEY)
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY_ID", "export-2026-11")
    assert _preview(auth_client, old_raw)["provenance"]["status"] == "unsupported_signature"
    monkeypatch.setenv("CLINIC_EXPORT_VERIFY_KEYS", json.dumps({TEST_EXPORT_SIGNING_KEY_ID: TEST_EXPORT_SIGNING_KEY}))
    assert _preview(auth_client, old_raw)["provenance"]["status"] == "verified_signed"
    new_doc = json.loads(_export(auth_client))
    assert new_doc["signature"]["keyId"] == "export-2026-11"
    # Конфликтующий key_id в списке проверки — ошибка конфигурации, а не «проверено».
    monkeypatch.setenv("CLINIC_EXPORT_VERIFY_KEYS", json.dumps({"export-2026-11": TEST_EXPORT_SIGNING_KEY}))
    assert _preview(auth_client, old_raw)["provenance"]["status"] == "unsupported_signature"


def test_signing_key_file_must_be_owner_only(auth_client, monkeypatch, tmp_path):
    from backend.app import export_signing
    key_file = tmp_path / "export.key"
    key_file.write_text(OTHER_KEY)
    key_file.chmod(0o644)
    monkeypatch.delenv("CLINIC_EXPORT_SIGNING_KEY")
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY_FILE", str(key_file))
    with pytest.raises(export_signing.SigningKeyError) as excinfo:
        export_signing.active_key()
    assert OTHER_KEY not in str(excinfo.value)
    key_file.chmod(0o600)
    assert export_signing.active_key().key_id == TEST_EXPORT_SIGNING_KEY_ID


# 13–20. Неподписанные файлы и подтверждение ---------------------------------

def test_legacy_and_external_files_need_explicit_confirmation(auth_client):
    _create(auth_client, name="Существующий", events=[{"date": "2030-01-10"}])
    legacy = json.dumps(legacy_export(json.loads(_export(auth_client))), ensure_ascii=False)
    external = json.dumps([make_patient_payload(name="Внешний Пациент")], ensure_ascii=False)
    for raw, status in ((legacy, "unsigned_legacy"), (external, "unsigned_external")):
        provenance = _preview(auth_client, raw)["provenance"]
        assert provenance["status"] == status and provenance["confirmationRequired"] is True
        assert "Происхождение файла не подтверждено" in provenance["message"]
        before = _snapshot()
        r = _commit(auth_client, raw, "add")                            # обычный клик без подтверждения
        assert r.status_code == 428 and "не имеет подтверждённой подписи" in r.json()["detail"]
        assert _snapshot() == before
        assert "reason=confirmation_required" in _import_audit()[-1]["detail"]
        r = unsigned_import(auth_client, raw, "add")
        if status == "unsigned_legacy":
            # Старая выгрузка с существующим patient.id — повторный импорт (F10).
            assert r.status_code == 409 and _snapshot() == before
            assert "existing_records=" in _import_audit()[-1]["detail"]
            continue
        assert r.status_code == 200 and r.json()["provenance"] == status
        row = _import_audit()[-1]
        assert row["result"] == "ok" and row["actor_user_id"]
        assert f"provenance={status}" in row["detail"] and "unsigned_confirmed=1" in row["detail"]
        assert f"file_sha256={hashlib.sha256(raw.encode()).hexdigest()}" in row["detail"]


def test_confirmation_is_bound_to_the_file_and_the_mode(auth_client):
    raw = json.dumps([make_patient_payload(name="Файл А")], ensure_ascii=False)
    other = json.dumps([make_patient_payload(name="Файл Б")], ensure_ascii=False)
    sha = _preview(auth_client, raw)["provenance"]["fileSha256"]
    before = _snapshot()
    # Подтверждение файла А не подходит к файлу Б (файл изменён после preview).
    r = _commit(auth_client, other, "add", unsignedConfirmation={"confirmed": True, "fileSha256": sha, "mode": "add"})
    assert r.status_code == 428 and "другому файлу" in r.json()["detail"]
    # И не подходит к другому режиму.
    r = _commit(auth_client, raw, "replace", unsignedConfirmation={"confirmed": True, "fileSha256": sha, "mode": "add"})
    assert r.status_code == 428
    # «confirmed: false» — не подтверждение.
    r = _commit(auth_client, raw, "add", unsignedConfirmation={"confirmed": False, "fileSha256": sha, "mode": "add"})
    assert r.status_code == 422
    assert _snapshot() == before
    r = _commit(auth_client, raw, "add", unsignedConfirmation={"confirmed": True, "fileSha256": sha, "mode": "add"})
    assert r.status_code == 200


def test_confirmation_does_not_bypass_archive_consilium_or_rbac(client):
    dept = create_department("Неподписанный Импорт")
    create_account(DEV_USERNAME)
    create_account("uns.doc", "doctor", department_ids=[dept])
    create_account("uns.deputy", "deputy_chief_oncology")
    login(client, "uns.doc")
    pid, event = _with_deleted_event(client)
    signed = json.loads(_export(client))
    assert client.delete(f"/api/patients/{pid}/events/{event['id']}?version={event['version']}",
                         headers=client.csrf).status_code == 200
    # Подпись снята, но id событий оставлены: id удалённого события блокирует импорт.
    stripped = {k: v for k, v in signed.items() if k not in ("format", "formatVersion", "signature")}
    for record in stripped["patients"]:
        record.pop("id")
        record.pop("treatingDoctorUserId", None)
    raw = json.dumps(stripped, ensure_ascii=False)
    before = _snapshot()
    r = unsigned_import(client, raw, "add")
    # id событий в файле — внутренние id: врач такой файл не импортирует (решение B).
    assert r.status_code == 403 and "внутренние идентификаторы" in r.json()["detail"]
    assert _snapshot() == before
    deleted = get_conn().execute("SELECT deleted_at, deleted_by_user_id FROM events WHERE id = ?", (event["id"],)).fetchone()
    assert deleted["deleted_at"] and deleted["deleted_by_user_id"]
    # Роль только для чтения не импортирует ни с подтверждением, ни без.
    login(client, "uns.deputy")
    assert unsigned_import(client, raw, "add").status_code == 403
    assert _commit(client, raw, "add").status_code == 403


# 21–28. Архив, повторный импорт, режимы, консилиум ---------------------------

def test_signed_file_with_an_event_deleted_later_is_rejected_in_every_mode(auth_client):
    pid, event = _with_deleted_event(auth_client)
    raw = _export(auth_client)                                          # событие ещё активно
    assert auth_client.delete(f"/api/patients/{pid}/events/{event['id']}?version={event['version']}",
                              headers=auth_client.csrf).status_code == 200
    preview = _preview(auth_client, raw)
    assert preview["provenance"]["status"] == "verified_signed"
    for mode in ("add", "update", "replace"):
        assert preview["archivedEventsImpact"][mode]["blocked"] is True, mode
    before = _snapshot()
    for mode in ("add", "update", "replace"):
        r = _commit(auth_client, raw, mode)
        assert r.status_code == 409, (mode, r.text)
    assert _snapshot() == before
    row = get_conn().execute("SELECT deleted_at, deleted_by_user_id FROM events WHERE id = ?", (event["id"],)).fetchone()
    assert row["deleted_at"] and row["deleted_by_user_id"]
    active = get_conn().execute("SELECT COUNT(*) FROM events WHERE date = '2030-01-10' AND deleted_at IS NULL").fetchone()[0]
    assert active == 0


def test_repeated_signed_import_is_a_conflict_not_a_duplicate(auth_client):
    _create(auth_client, name="Повторный Импорт", events=[{"date": "2030-01-10"}])
    raw = _export(auth_client)
    preview = _preview(auth_client, raw)
    assert preview["duplicateImpact"]["add"]["blocked"] is True
    before = _snapshot()
    r = _commit(auth_client, raw, "add")
    assert r.status_code == 409 and "уже есть в базе" in r.json()["detail"]
    assert _snapshot() == before


def test_signed_file_into_an_empty_database_and_then_again(auth_client):
    _create(auth_client, name="Перенос", events=[{"date": "2030-01-10"}])
    raw = _export(auth_client)
    pid = json.loads(raw)["patients"][0]["id"]
    assert auth_client.delete(f"/api/patients/{pid}", headers=auth_client.csrf).status_code == 200
    assert _commit(auth_client, raw, "add").status_code == 428           # без подтверждения сохранения id
    r = _commit(auth_client, raw, "add", preserveIdsConfirmation=preserve_ids_confirmation(raw, "add"))
    assert r.status_code == 200 and r.json()["added"] == 1
    assert _commit(auth_client, raw, "add",                              # повтор уже не создаёт копию
                   preserveIdsConfirmation=preserve_ids_confirmation(raw, "add")).status_code == 409
    assert len(auth_client.get("/api/patients").json()) == 1


def test_file_without_ids_is_new_data_after_confirmation(auth_client):
    _create(auth_client, name="Уже Есть", events=[{"date": "2030-01-10"}])
    legacy = legacy_export(json.loads(_export(auth_client)))
    for record in legacy["patients"]:
        record.pop("id")
    raw = json.dumps(legacy, ensure_ascii=False)
    # Ограничение HMAC: файл без признаков подписи и id неотличим от новых данных;
    # WP2.4-C: совпадение полиса в области сотрудника все же требует подтверждения.
    from .conftest import possible_duplicate_ids
    ids = possible_duplicate_ids(unsigned_import(auth_client, raw, "add"))
    assert len(auth_client.get("/api/patients").json()) == 1
    r = unsigned_import(auth_client, raw, "add", duplicates=ids)
    assert r.status_code == 200 and r.json()["added"] == 1
    assert len(auth_client.get("/api/patients").json()) == 2


def test_signed_file_respects_the_consilium_block(client):
    from .test_consilium_appointments import appointment_payload
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    pid = _create(client, events=[{"date": "2030-01-10"}])["id"]
    consilium_grid(LEGACY_CONSILIUM_DATES)  # WP2.3-B2: запись только в слот председателя
    assert client.post("/api/consilium/appointments", json=appointment_payload(pid),
                       headers=client.csrf).status_code in (200, 201)
    raw = _export(client)
    before = _snapshot()
    for mode in ("update", "replace"):
        r = _commit(client, raw, mode)
        assert r.status_code == 409 and "онкоконсилиума" in r.json()["detail"], mode
    assert _snapshot() == before


# 29–32. RBAC ---------------------------------------------------------------

def test_signed_import_follows_role_and_scope(client):
    dept_a = create_department("Подпись А")
    dept_b = create_department("Подпись Б")
    create_account(DEV_USERNAME)
    create_account("sig.doc.a", "doctor", department_ids=[dept_a])
    create_account("sig.doc.b", "doctor", department_ids=[dept_b])
    create_account("sig.head.a", "department_head", department_ids=[dept_a])
    create_account("sig.deputy", "deputy_chief_oncology")
    login(client, "sig.doc.b")
    foreign = _create(client, name="Чужой Подписанный", events=[{"date": "2030-01-10"}])["id"]
    login(client, DEV_USERNAME)
    raw = _export(client)
    login(client, "sig.doc.a")
    preview = _preview(client, raw)
    # Подпись не расширяет права: файл с внутренними id врач не импортирует.
    assert preview["valid"] is False and preview["provenance"]["status"] == "verified_signed"
    assert preview["code"] == "internal_ids_forbidden"
    before = _snapshot()
    assert _commit(client, raw, "add").status_code == 403
    assert _snapshot() == before
    assert _commit(client, raw, "update").status_code == 403
    assert _commit(client, raw, "replace").status_code == 403
    login(client, "sig.head.a")
    assert _commit(client, raw, "update").status_code == 403
    login(client, "sig.deputy")
    assert _commit(client, raw, "add").status_code == 403
    assert client.get("/api/export", headers=client.csrf).status_code in (200, 403)
    login(client, DEV_USERNAME)
    assert _preview(client, raw)["duplicateImpact"]["add"]["blocked"] is True
    assert get_conn().execute("SELECT COUNT(*) FROM patients WHERE id = ?", (foreign,)).fetchone()[0] == 1


# 33–36. Аудит и атомарность --------------------------------------------------

def test_import_is_not_saved_without_its_audit_row(auth_client):
    raw = json.dumps([make_patient_payload(name="Без Аудита")], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("""CREATE TRIGGER test_import_audit_failure BEFORE INSERT ON audit_events
                        WHEN NEW.event_type = 'import_committed' AND NEW.result = 'ok'
                        BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")
    before = _snapshot()
    r = unsigned_import(auth_client, raw, "add")
    assert r.status_code == 503 and "Данные не изменены" in r.json()["detail"]
    assert _snapshot() == before
    with get_conn() as conn:
        conn.execute("DROP TRIGGER test_import_audit_failure")
    assert unsigned_import(auth_client, raw, "add").status_code == 200


def test_failure_during_signed_import_rolls_back(auth_client, monkeypatch):
    from backend.app import importexport
    # WP2.4-B (U9): разные синтетические полисы — файл с одинаковыми полисами отклоняется до записи.
    _create(auth_client, name="Первый", policyNumber="7700000000024101", events=[{"date": "2030-01-10"}])
    _create(auth_client, name="Второй", policyNumber="7700000000024102", events=[{"date": "2030-02-10"}])
    raw = _export(auth_client)
    original, calls = importexport._insert_full_patient, []

    def failing(conn, p, *args, **kwargs):
        calls.append(p.name)
        if len(calls) == 2:
            raise sqlite3.OperationalError("synthetic failure")
        return original(conn, p, *args, **kwargs)

    for patient in auth_client.get("/api/patients").json():
        auth_client.delete(f"/api/patients/{patient['id']}", headers=auth_client.csrf)
    monkeypatch.setattr(importexport, "_insert_full_patient", failing)
    before = _snapshot()
    r = _commit(auth_client, raw, "replace", preserveIdsConfirmation=preserve_ids_confirmation(raw, "replace"))
    assert r.status_code == 503
    assert _snapshot() == before


def test_import_audit_contains_no_medical_or_personal_data(auth_client):
    raw = json.dumps([make_patient_payload(name="Секретова Анна", phone="+79991112233", diagnosis="C34.1",
                                           events=[{"date": "2030-01-10"}])], ensure_ascii=False)
    unsigned_import(auth_client, raw, "add")
    _create(auth_client, name="Подписанова Ольга")
    signed = _export(auth_client)
    _commit(auth_client, signed, "add")
    text = json.dumps(_import_audit(), ensure_ascii=False)
    for secret in ("Секретова", "+7999", "C34", "2030-01-10", "Подписанова"):
        assert secret not in text, secret
    from backend.tests.conftest import TEST_EXPORT_SIGNING_KEY
    assert TEST_EXPORT_SIGNING_KEY not in text


def test_migration_of_local_data_requires_an_empty_database_inside_the_transaction(auth_client):
    raw = json.dumps([make_patient_payload(name="Локальный")], ensure_ascii=False)
    r = auth_client.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=auth_client.csrf)
    assert r.status_code == 200
    rows = get_conn().execute("SELECT result, detail FROM audit_events WHERE event_type = 'migration_committed'").fetchall()
    assert len(rows) == 1 and rows[0]["result"] == "ok"
    assert rows[0]["detail"].startswith("added=1 provenance=local_migration file_status=unsigned_external file_sha256=")
    assert auth_client.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"},
                            headers=auth_client.csrf).status_code == 409


# Замечания независимого ревью ------------------------------------------------

def test_key_id_from_the_file_never_reaches_the_audit_unchecked(auth_client):
    _create(auth_client, events=[{"date": "2030-01-10"}])
    document = json.loads(_export(auth_client))
    document["signature"]["keyId"] = "x confirmed=1 provenance=verified_signed\nFAKE" + "A" * 5000
    raw = json.dumps(document, ensure_ascii=False)
    preview = _preview(auth_client, raw)
    assert preview["provenance"]["status"] == "invalid_signature" and preview["provenance"]["keyId"] is None
    assert _commit(auth_client, raw, "add").status_code == 422
    detail = _import_audit()[-1]["detail"]
    assert "FAKE" not in detail and "key_id=-" in detail and len(detail) < 300


def test_confirmation_must_be_json_true_and_text_must_be_valid_utf8(auth_client):
    raw = json.dumps([make_patient_payload(name="Строгое Подтверждение")], ensure_ascii=False)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    for value in (1, 1.0, "true"):
        r = _commit(auth_client, raw, "add", unsignedConfirmation={"confirmed": value, "fileSha256": sha, "mode": "add"})
        assert r.status_code == 422, value
    from backend.app import importexport
    with pytest.raises(importexport.ImportStructureError):
        importexport.inspect_import("[\ud800]")          # одиночный суррогат — не 500 в разборе импорта


def test_malformed_weak_or_conflicting_keys_stop_the_export(auth_client, monkeypatch):
    from backend.tests.conftest import TEST_EXPORT_SIGNING_KEY
    _create(auth_client)
    for bad in ("!!" + TEST_EXPORT_SIGNING_KEY + "!!", "A" * 43, "00" * 32):
        monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", bad)
        assert auth_client.get("/api/export", headers=auth_client.csrf).status_code == 503, bad
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", TEST_EXPORT_SIGNING_KEY)
    monkeypatch.setenv("CLINIC_EXPORT_VERIFY_KEYS", json.dumps({TEST_EXPORT_SIGNING_KEY_ID: OTHER_KEY}))
    # Установка не смогла бы проверить собственную выгрузку — выгрузка не выпускается.
    assert auth_client.get("/api/export", headers=auth_client.csrf).status_code == 503
