"""WP2.4-B: idempotency of patient creation, import and localStorage migration;
history of processed import files (U4) and repeated policy numbers inside one
file (U9). Schema v24. Synthetic data only.

B1 — Idempotency-Key of creation; B2 — Idempotency-Key of import/migration;
B3 — processed-file history and repeated policies inside a file. A key protects
against repeats of one operation only: two operations with different keys may
still create duplicates (matching between cards is WP2.4-C).
"""
import hashlib
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager

import pytest

from .conftest import pre_v28_rows, app_stopped, DEV_USERNAME, PROJECT_ROOT, get_conn, login, make_patient_payload, synthetic_policy_for_key
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _in_thread, _parallel, _rows
from .test_wp24a_patient_create import EVENTS, _counts, _db_path, _patient, _world

V23_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp24b_v23_synthetic.sql"
REPLAYED = "Idempotent-Replayed"


# ---------------------------------------------------------------- helpers

def _key():
    return str(uuid.uuid4())


def _headers(client, key):
    return {**client.csrf, **({"Idempotency-Key": key} if key is not None else {})}


def _create(client, key, **kw):
    # WP2.4-C: у каждой синтетической анкеты свой полис (conftest); повтор с тем же
    # ключом — то же тело, поэтому номер закреплен за ключом.
    if key is not None and "policyNumber" not in kw:
        kw["policyNumber"] = synthetic_policy_for_key(key)
    return client.post("/api/patients", json=make_patient_payload(**kw), headers=_headers(client, key))


def _keys():
    return _rows("SELECT * FROM idempotency_keys ORDER BY created_at")


def _audit(event_type):
    return _rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", event_type)


def _restore_audit():
    get_conn().execute("DROP TRIGGER IF EXISTS wp23a_audit_down")
    get_conn().commit()


def _file(*rows):
    return json.dumps({"schemaVersion": 1, "patients": list(rows)}, ensure_ascii=False)


def _row(name, policy="", **kw):
    return {"name": name, "age": 60, "priority": "normal", "stage": "exam", "phone": "", "policyNumber": policy,
            "diagnosis": "C34.1", "nosology": "lung34", "head": "Синтетов", **kw}


def _sha(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _import(client, raw, mode="add", key=None, *, repeat=False, repeat_sha=None, repeat_mode=None, url="/api/import/commit",
            duplicates=None):
    body = {"raw": raw, "mode": mode}
    if duplicates is not None:   # WP2.4-C: подтверждение возможных дублей (ID из ответа 409)
        body["duplicateConfirmation"] = {"reason": "Синтетическая причина", "matchedPatientIds": list(duplicates)}
    if url == "/api/import/commit":
        body["unsignedConfirmation"] = {"confirmed": True, "fileSha256": _sha(raw), "mode": mode}
    if repeat:
        body["repeatImportConfirmation"] = {"confirmed": True, "fileSha256": repeat_sha or _sha(raw),
                                            "mode": repeat_mode or mode, "action": "repeat_processed_file"}
    return client.post(url, json=body, headers=_headers(client, key))


def _migrate(client, raw, key=None):
    return _import(client, raw, key=key, url="/api/migrate/localstorage/commit")


def _names():
    return sorted(r["name"] for r in _rows("SELECT name FROM patients"))


def _age_keys(where="1 = 1", params=()):
    """Состарить ключи: завершенный ключ неизменяем (триггер), поэтому тест
    пересоздает строку с прошедшим сроком через DELETE + INSERT."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM idempotency_keys WHERE {where}", params)]  # nosec - test
    for r in rows:
        conn.execute("DELETE FROM idempotency_keys WHERE actor_user_id = ? AND operation = ? AND key_hash = ?",
                     (r["actor_user_id"], r["operation"], r["key_hash"]))
        r.update(created_at="1999-12-25T00:00:00+00:00", expires_at="2000-01-01T00:00:00+00:00")
        conn.execute(f"INSERT INTO idempotency_keys ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})",  # nosec
                     tuple(r.values()))
    conn.commit()
    return len(rows)


def _service():
    from backend.app.services import patient_create
    return patient_create


# ================================================================ 1. one key — one card

def test_repeat_with_the_same_key_creates_one_card(client):
    ids = _world()
    login(client, "doc.a")
    key = _key()
    first = _create(client, key, events=EVENTS)
    assert first.status_code == 201 and REPLAYED not in first.headers
    before = _counts()
    second = _create(client, key, events=EVENTS)
    assert second.status_code == 201 and second.headers[REPLAYED] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["history"] == first.json()["history"]          # маршрут и события не задвоены
    assert _counts() == before == {"patients": 1, "history": 1, "events": 2, "created_audit": 1}
    replayed = _audit("patient_create_replayed")
    assert len(replayed) == 1 and (replayed[0]["patient_id"], replayed[0]["actor_user_id"], replayed[0]["detail"]) == (
        first.json()["id"], ids["doc.a"], None)
    rows = _keys()
    assert len(rows) == 1 and rows[0]["state"] == "completed" and rows[0]["status_code"] == 201
    assert rows[0]["result_patient_id"] == first.json()["id"]
    assert key not in json.dumps(rows) and len(rows[0]["key_hash"]) == len(rows[0]["request_hash"]) == 64
    assert rows[0]["expires_at"][:10] == "2026-10-28" or rows[0]["expires_at"] > rows[0]["created_at"]


def test_key_is_case_insensitive_and_server_normalisation_defines_the_fingerprint(client):
    _world()
    login(client, "doc.a")
    key = _key()
    first = _create(client, key, name="Синтетический  Пробелов ")
    again = _create(client, key.upper(), name=" Синтетический Пробелов")    # тот же нормализованный текст
    assert again.status_code == 201 and again.headers[REPLAYED] == "true" and again.json()["id"] == first.json()["id"]
    assert _counts()["patients"] == 1


# ================================================================ 2. concurrency

def test_concurrent_repeats_with_one_key_create_one_card(client):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    for round_ in range(20):
        key = _key()
        res = _parallel(lambda: _create(tabs[0], key, policyNumber=f"77000000002{round_:05d}"),
                        lambda: _create(tabs[1], key, policyNumber=f"77000000002{round_:05d}"))
        codes = sorted((r.status_code, r.headers.get(REPLAYED) or "") for r, *_ in res)
        assert codes == [(201, ""), (201, "true")], codes
        assert len({r.json()["id"] for r, *_ in res}) == 1
    assert _counts()["patients"] == 20 and _counts()["created_audit"] == 20
    assert len(_audit("patient_create_replayed")) == 20 and len(_keys()) == 20


def test_forced_lock_then_two_requests_with_one_key(client):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    key = _key()
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    boxes = [_in_thread(lambda t=t: _create(t, key)) for t in tabs]
    import time
    time.sleep(0.6)
    waited = all(not done.is_set() for _, done, _ in boxes)
    raw.execute("COMMIT")
    raw.close()
    for _, _, thread in boxes:
        thread.join(15)
    responses = [box["response"] for box, _, _ in boxes]
    assert waited and sorted(r.headers.get(REPLAYED) or "" for r in responses) == ["", "true"]
    assert {r.status_code for r in responses} == {201} and _counts()["patients"] == 1


# ================================================================ 3. same key, other data

@pytest.mark.parametrize("change", [{"name": "Другой Синтетик"}, {"policyNumber": "7799999999999999"},
                                    {"events": [{"date": "2026-11-20", "time": "10:05"}]},
                                    {"priority": "high"}])
def test_same_key_with_other_data_is_422_without_creation_or_disclosure(client, change):
    _world()
    login(client, "doc.a")
    key = _key()
    first = _create(client, key, events=[{"date": "2026-11-20", "time": "10:00"}])
    before = _counts()
    payload = {**{"events": [{"date": "2026-11-20", "time": "10:00"}]}, **change}
    response = _create(client, key, **payload)
    assert response.status_code == 422 and response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert first.json()["id"] not in response.text and "Тестов" not in response.text
    assert _counts() == before


# ids: стабильные имена тестов (значения uuid1/uuid4 случайны), иначе идентификатор теста
# меняется при каждом сборе и CI не может сверить, что запущен каждый собранный тест (WP-A).
@pytest.mark.parametrize("header", ["", "   ", "not-a-uuid", "00000000-0000-0000-0000-000000000000",
                                    str(uuid.uuid1()), str(uuid.uuid4()) + "x"],
                         ids=["empty", "spaces", "not-a-uuid", "nil-uuid", "uuid1", "uuid4-plus-char"])
def test_explicit_invalid_key_is_422(client, header):
    _world()
    login(client, "doc.a")
    response = client.post("/api/patients", json=make_patient_payload(), headers={**client.csrf, "Idempotency-Key": header})
    assert response.status_code == 422 and response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"
    assert _counts()["patients"] == 0 and _keys() == []


def test_without_a_key_the_wp24a_rules_apply(client):
    """U5: внешний клиент без ключа — прежнее поведение, без мнимой гарантии."""
    _world()
    login(client, "doc.a")
    first, second = _create(client, None), _create(client, None)
    assert first.status_code == second.status_code == 201
    assert REPLAYED not in first.headers and REPLAYED not in second.headers
    assert first.json()["id"] != second.json()["id"] and _keys() == []


# ================================================================ 4. keys of different users

def test_same_key_value_of_another_user_is_a_separate_operation(client):
    _world()
    key = _key()
    login(client, "doc.a")
    mine = _create(client, key, name="Синтетик Первого")
    login(client, "doc.a2")
    theirs = _create(client, key, name="Синтетик Первого")
    assert theirs.status_code == 201 and REPLAYED not in theirs.headers
    assert theirs.json()["id"] != mine.json()["id"] and _counts()["patients"] == 2
    other_body = _create(client, key, name="Совсем Другой")     # другой отпечаток уже у doc.a2
    assert other_body.status_code == 422 and mine.json()["id"] not in other_body.text


# ================================================================ 5. role and scope changed before the repeat

def test_repeat_after_losing_the_card_is_404_without_data(client):
    ids = _world()
    login(client, "head.1")
    key = _key()
    first = _create(client, key, treatingDoctorUserId=ids["doc.a"])
    get_conn().execute("DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    get_conn().commit()
    again = _create(client, key, treatingDoctorUserId=ids["doc.a"])
    assert again.status_code in (403, 404)
    assert first.json()["id"] not in again.text and "Тестов" not in again.text
    assert _counts()["patients"] == 1 and _audit("patient_create_replayed") == []


def test_repeat_after_reassignment_to_another_doctor_is_404(client):
    ids = _world()
    login(client, "doc.a")
    key = _key()
    pid = _create(client, key).json()["id"]
    get_conn().execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.b"], pid))
    get_conn().commit()
    again = _create(client, key)
    assert again.status_code == 404 and pid not in again.text and _counts()["patients"] == 1


def test_repeat_after_a_role_change_is_403(client):
    ids = _world()
    login(client, "doc.a")
    key = _key()
    pid = _create(client, key).json()["id"]
    get_conn().execute("UPDATE users SET role_id = 'deputy_chief_oncology' WHERE id = ?", (ids["doc.a"],))
    get_conn().commit()
    again = _create(client, key)
    assert again.status_code == 403 and pid not in again.text and _counts()["patients"] == 1


# ================================================================ 6. the response is lost after the commit

def test_lost_response_after_commit_is_recovered_by_the_same_key(client, monkeypatch):
    _world()
    login(client, "doc.a")
    service = _service()
    real = service.transaction
    state = {"crash": True}

    @contextmanager
    def crash_after_commit(conn):
        with real(conn) as inner:
            yield inner
        if state.pop("crash", False):
            raise ConnectionResetError("synthetic: response lost after commit")

    monkeypatch.setattr(service, "transaction", crash_after_commit)
    key = _key()
    lost = _create(client, key, events=EVENTS)
    assert lost.status_code == 500                                   # клиент ответа не получил
    assert _counts()["patients"] == 1
    again = _create(client, key, events=EVENTS)
    assert again.status_code == 201 and again.headers[REPLAYED] == "true"
    assert again.json()["id"] == _rows("SELECT id FROM patients")[0]["id"] and _counts()["patients"] == 1


# ================================================================ 7–8. audit failures

def test_audit_failure_of_the_first_operation_keeps_no_key(client):
    _world()
    login(client, "doc.a")
    key = _key()
    _fail_audit("patient_created")
    failed = _create(client, key, events=EVENTS)
    assert failed.status_code == 503 and _keys() == [] and _counts()["patients"] == 0
    _restore_audit()
    ok = _create(client, key, events=EVENTS)
    assert ok.status_code == 201 and REPLAYED not in ok.headers and _counts()["patients"] == 1


def test_replay_audit_failure_is_503_without_patient_data(client):
    _world()
    login(client, "doc.a")
    key = _key()
    first = _create(client, key)
    _fail_audit("patient_create_replayed")
    again = _create(client, key)
    assert again.status_code == 503 and "журнал аудита" in again.json()["detail"]
    assert first.json()["id"] not in again.text and "Тестов" not in again.text and "7700000000000000" not in again.text
    assert _counts()["patients"] == 1 and _keys()[0]["state"] == "completed"
    _restore_audit()
    assert _create(client, key).headers[REPLAYED] == "true"


# ================================================================ 9. TTL and purge

def test_expired_key_is_a_new_operation_and_purge_removes_only_expired_keys(client):
    from backend.app import idempotency
    _world()
    login(client, "doc.a")
    old, fresh = _key(), _key()
    _create(client, old, name="Синтетик Старый")
    _create(client, fresh, name="Синтетик Свежий")
    raw = _file(_row("Синтетик Файла"))
    assert _import(client, raw, key=_key()).status_code == 200
    rows = _keys()
    created = rows[0]["created_at"]
    from datetime import datetime, timedelta
    assert datetime.fromisoformat(rows[0]["expires_at"]) - datetime.fromisoformat(created) == timedelta(days=7)
    assert _age_keys("result_patient_id = (SELECT id FROM patients WHERE name = ?)", ("Синтетик Старый",)) == 1
    history_before = _rows("SELECT * FROM import_files_processed")
    assert idempotency.purge_expired(get_conn()) == 1
    assert len(_keys()) == 2 and _rows("SELECT * FROM import_files_processed") == history_before
    assert _counts()["patients"] == 3
    # Истекший ключ — новая операция (документированное ограничение срока 7 дней).
    # WP2.4-C: та же анкета совпадает по полису с уже созданной карточкой — без
    # подтверждения копия не создается; с подтверждением — обычное новое создание.
    again = _create(client, old, name="Синтетик Старый")
    assert again.status_code == 409 and again.json()["code"] == "POSSIBLE_DUPLICATE" and _counts()["patients"] == 3
    body = make_patient_payload(name="Синтетик Старый", policyNumber=synthetic_policy_for_key(old),
                                duplicateConfirmation={"reason": "Синтетическая причина",
                                                       "matchedPatientIds": [m["id"] for m in again.json()["matches"]]})
    confirmed = client.post("/api/patients", json=body, headers=_headers(client, _key()))
    assert confirmed.status_code == 201 and REPLAYED not in confirmed.headers and _counts()["patients"] == 4


def test_expired_row_with_the_same_key_is_replaced_inside_the_transaction(client):
    _world()
    login(client, "doc.a")
    key = _key()
    _create(client, key)
    _age_keys()
    # Другой отпечаток не мешает (WP2.4-C: свой полис, чтобы не совпасть с первой карточкой).
    assert _create(client, key, name="Новый Синтетик", policyNumber="7700000000024901").status_code == 201
    assert len(_keys()) == 1 and _keys()[0]["expires_at"] > "2026"


def test_login_purges_expired_keys(client):
    _world()
    login(client, "doc.a")
    _create(client, _key())
    _age_keys()
    login(client, "doc.a")
    assert _keys() == []


# ================================================================ 10. no keys or medical data in logs

def test_keys_and_patient_data_never_reach_audit_or_logs(client, caplog):
    _world()
    caplog.set_level(logging.DEBUG)
    login(client, "doc.a")
    keys = [_key() for _ in range(4)]
    _create(client, keys[0], events=EVENTS)
    _create(client, keys[0], events=EVENTS)
    _create(client, keys[0], name="Другое Имя")                         # 422
    _fail_audit("patient_create_replayed")
    _create(client, keys[0], events=EVENTS)                              # 503
    _restore_audit()
    raw = _file(_row("Синтетик Журнала", "7700000000024201"))
    _import(client, raw, key=keys[1])
    _import(client, raw, key=keys[1])
    _import(client, raw, key=keys[2])                                    # 428
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False) + json.dumps(_keys()) + \
        json.dumps(_rows("SELECT * FROM import_files_processed")) + caplog.text
    for value in [*keys, *(k.upper() for k in keys), "Тестов Тест Тестович", "7700000000000000",
                  "Синтетик Журнала", "7700000000024201", "Жалобы для теста", "C34.1"]:
        assert value not in dump, value


# ================================================================ 11–14. import and migration keys

def test_import_repeat_with_the_key_returns_the_original_result(client):
    _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Импорта А", "7700000000024301"), _row("Синтетик Импорта Б", ""))
    key = _key()
    first = _import(client, raw, key=key)
    assert first.status_code == 200 and REPLAYED not in first.headers and first.json()["added"] == 2
    import_id = first.headers["Import-Id"]
    before = {"patients": _names(), "history": _rows("SELECT COUNT(*) AS c FROM history_entries")[0]["c"]}
    again = _import(client, raw, key=key)
    assert again.status_code == 200 and again.headers[REPLAYED] == "true"
    assert again.headers["Import-Id"] == import_id and again.json() == first.json()
    assert {"patients": _names(), "history": _rows("SELECT COUNT(*) AS c FROM history_entries")[0]["c"]} == before
    assert len(_audit("import_committed")) == 1 and len(_audit("import_replayed")) == 1
    assert f"import_id={import_id}" in _audit("import_replayed")[0]["detail"]
    assert len(_rows("SELECT * FROM import_files_processed")) == 1


def test_migration_repeat_with_the_key_returns_the_original_result(client):
    _world()
    login(client, DEV_USERNAME)
    raw = _file(_row("Синтетик Переноса", "7700000000024401"))
    key = _key()
    first = _migrate(client, raw, key)
    assert first.status_code == 200 and first.json()["added"] == 1
    again = _migrate(client, raw, key)                                   # база уже не пуста
    assert again.status_code == 200 and again.headers[REPLAYED] == "true" and again.json() == first.json()
    assert again.headers["Import-Id"] == first.headers["Import-Id"] and _names() == ["Синтетик Переноса"]
    assert len(_audit("migration_replayed")) == 1 and len(_audit("migration_committed")) == 1
    assert _migrate(client, raw, None).status_code == 409                # без ключа — прежнее правило
    assert _migrate(client, raw, _key()).status_code == 409              # новый ключ — новая операция


@pytest.fixture()
def high_import_limit(monkeypatch):
    # Лимит частоты импорта (10 в минуту) — не предмет этого теста конкурентности.
    from backend.app import security
    monkeypatch.setattr(security, "check_request_rate_limit", lambda *a, **k: 0)


def test_concurrent_import_commits_with_one_key_execute_once(client, high_import_limit):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    for round_ in range(10):
        raw = _file(_row(f"Синтетик Гонки {round_}", f"77000000000250{round_:02d}"))
        key = _key()
        res = _parallel(lambda: _import(tabs[0], raw, key=key), lambda: _import(tabs[1], raw, key=key))
        assert sorted(r.headers.get(REPLAYED) or "" for r, *_ in res) == ["", "true"], [r.text for r, *_ in res]
        assert len({r.headers["Import-Id"] for r, *_ in res}) == 1
    assert len(_names()) == 10 and len(_audit("import_committed")) == 10


def test_concurrent_commits_of_one_file_with_different_keys_execute_once(client):
    """Без общего ключа второй commit того же файла видит историю под блокировкой (428)."""
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    raw = _file(_row("Синтетик Двух Ключей", "7700000000025101"))
    res = _parallel(lambda: _import(tabs[0], raw, key=_key()), lambda: _import(tabs[1], raw, key=_key()))
    assert sorted(r.status_code for r, *_ in res) == [200, 428] and _names() == ["Синтетик Двух Ключей"]


@pytest.mark.parametrize("variant", ["file", "mode", "confirmation"])
def test_import_key_with_other_content_is_422(client, variant):
    _world()
    login(client, DEV_USERNAME)
    raw = _file(_row("Синтетик Ключа", "7700000000025201"))
    key = _key()
    assert _import(client, raw, key=key).status_code == 200
    before = _names()
    if variant == "file":
        response = _import(client, _file(_row("Синтетик Другого Файла", "7700000000025202")), key=key)
    elif variant == "mode":
        response = _import(client, raw, mode="update", key=key)
    else:
        response = _import(client, raw, key=key, repeat=True)
    assert response.status_code == 422 and response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _names() == before


# ================================================================ 15–17. processed-file history (U4)

def test_own_repeated_file_needs_confirmation(client):
    _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Повтора", "7700000000025301"))
    first = _import(client, raw, key=_key())
    assert first.status_code == 200
    preview = client.post("/api/import/preview", json={"raw": raw}).json()
    previous = preview["previouslyImported"]
    assert (previous["mode"], previous["added"], previous["operation"]) == ("add", 1, "import")
    again = _import(client, raw, key=_key())
    assert again.status_code == 428 and again.json()["code"] == "FILE_ALREADY_IMPORTED"
    assert again.json()["previouslyImported"]["added"] == 1 and _names() == ["Синтетик Повтора"]
    denied = [r for r in _audit("import_committed") if r["result"] == "denied"]
    assert len(denied) == 1 and "reason=file_already_imported" in denied[0]["detail"]


def test_another_users_file_gives_no_warning(client):
    _world()
    raw = _file(_row("Синтетик Чужого Файла", "7700000000025401"))
    login(client, "doc.a")
    assert _import(client, raw, key=_key()).status_code == 200
    login(client, "doc.a2")
    preview = client.post("/api/import/preview", json={"raw": raw}).json()
    assert preview["previouslyImported"] is None
    second = _import(client, raw, key=_key())
    assert second.status_code == 200 and REPLAYED not in second.headers
    assert set(second.json()) == {"added", "updated", "skipped", "unchanged", "appendedEvents", "provenance"}
    assert "doc.a" not in second.text and "previouslyImported" not in second.text


def test_confirmed_repeat_is_bound_to_file_and_mode_and_audited(client):
    _world()
    login(client, DEV_USERNAME)
    raw = _file(_row("Синтетик Осознанного Повтора", "7700000000025501"))
    assert _import(client, raw, key=_key()).status_code == 200
    wrong_file = _import(client, raw, key=_key(), repeat=True, repeat_sha="0" * 64)
    wrong_mode = _import(client, raw, key=_key(), repeat=True, repeat_mode="update")
    assert wrong_file.status_code == wrong_mode.status_code == 428
    # WP2.4-C: повтор создает копию карточки (тот же полис) — отдельное подтверждение возможного дубля.
    duplicate = _import(client, raw, key=_key(), repeat=True)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "POSSIBLE_DUPLICATE"
    ok = _import(client, raw, key=_key(), repeat=True, duplicates=duplicate.json()["matchedPatientIds"])
    assert ok.status_code == 200 and ok.json()["added"] == 1
    confirmed = _audit("import_repeat_confirmed")
    assert len(confirmed) == 1 and f"import_id={ok.headers['Import-Id']}" in confirmed[0]["detail"]
    history = _rows("SELECT repeat_confirmed FROM import_files_processed ORDER BY processed_at")
    assert [h["repeat_confirmed"] for h in history] == [0, 1]


def test_history_keeps_only_technical_data(client):
    ids = _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Истории", "7700000000025601"))
    response = _import(client, raw, key=_key())
    row = _rows("SELECT * FROM import_files_processed")[0]
    assert set(row) == {"import_id", "actor_user_id", "operation", "file_sha256", "mode", "processed_at", "added",
                        "updated", "skipped", "unchanged", "appended_events", "repeat_confirmed"}
    assert (row["import_id"], row["actor_user_id"], row["file_sha256"], row["operation"], row["added"]) == (
        response.headers["Import-Id"], ids["doc.a"], _sha(raw), "import", 1)
    with pytest.raises(sqlite3.IntegrityError, match="import_file_history_immutable"):
        get_conn().execute("UPDATE import_files_processed SET added = 5")
    get_conn().rollback()


# ================================================================ 18–19. repeated policies inside one file (U9)

def test_repeated_policies_inside_a_file_are_refused_in_preview_and_commit(client):
    _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Один", "7700 0000 0002 5701"), _row("Синтетик Два", "7700000000025702"),
                _row("Синтетик Три", "7700-0000-0002-5701"), _row("Синтетик Без Полиса"), _row("Синтетик Тоже Без"))
    preview = client.post("/api/import/preview", json={"raw": raw}).json()
    assert preview["filePolicyDuplicates"]["add"]["blocked"] is True
    assert preview["filePolicyDuplicates"]["add"]["rows"] == [[1, 3]]
    assert "770000000002570" not in json.dumps(preview["filePolicyDuplicates"])
    response = _import(client, raw, key=_key())                         # прямой commit без preview
    assert response.status_code == 422 and response.json()["code"] == "FILE_POLICY_DUPLICATES"
    assert response.json()["rows"] == [[1, 3]] and "5701" not in response.text
    assert _names() == [] and _keys() == [] and _rows("SELECT * FROM import_files_processed") == []
    denied = [r for r in _audit("import_committed") if r["result"] == "denied"]
    assert "duplicate_policy_rows=2" in denied[-1]["detail"] and "5701" not in denied[-1]["detail"]


def test_empty_policies_are_not_duplicates(client):
    _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Пустой Один"), _row("Синтетик Пустой Два"), _row("Синтетик Пустой Три", "  "))
    assert client.post("/api/import/preview", json={"raw": raw}).json()["filePolicyDuplicates"]["add"]["blocked"] is False
    assert _import(client, raw, key=_key()).json()["added"] == 3


def test_migration_refuses_repeated_policies(client):
    _world()
    login(client, DEV_USERNAME)
    raw = _file(_row("Синтетик М1", "7700000000025801"), _row("Синтетик М2", "7700000000025801"))
    preview = client.post("/api/migrate/localstorage/preview", json={"raw": raw}).json()
    assert preview["valid"] is False and preview["code"] == "FILE_POLICY_DUPLICATES"
    assert "5801" not in json.dumps(preview)
    response = _migrate(client, raw, _key())
    assert response.status_code == 422 and response.json()["rows"] == [[1, 2]] and _names() == []


# ================================================================ 20. schema v24, migration and backups

def _fixture_db(tmp_path):
    path = _sqlite_from_fixture(tmp_path / "v23.sqlite", fixture=V23_FIXTURE)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return path, conn


CLINICAL = ("patients", "history_entries", "events", "patient_diagnosis_history", "oncology_consilium_appointments",
            "consilium_revision_history", "consilium_resubmissions", "consilium_backdating_reasons",
            "consilium_schedule_slots", "users", "audit_events")


def test_v23_fixture_is_a_real_v23_database(client, tmp_path):
    from backend.app.maintenance import identify_backup_build
    path, conn = _fixture_db(tmp_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 23
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"idempotency_keys", "import_files_processed"} & tables
    assert sorted(r[0] for r in conn.execute("SELECT creation_source FROM patients")) == (
        ["form", "import"] + ["legacy"] * 7 + ["migration"])
    conn.close()
    assert identify_backup_build(path, 23) == "wp24a-e53f42d"


def test_migration_v23_to_v24_keeps_every_clinical_row_and_is_idempotent(client, tmp_path):
    from backend.app import config, db
    path, conn = _fixture_db(tmp_path)
    before = {t: pre_v28_rows(conn, t) for t in CLINICAL}
    db.init_db(conn)
    # WP2.4-C/D: цепочка доводит v23 до текущей схемы (v26), а не только до v24.
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 28   # WP-C: v28 (история фактов, защита истории); combined-preaudit: v27 — мобильный доступ пациента
    for table in CLINICAL:
        assert pre_v28_rows(conn, table) == before[table], table
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_files_processed").fetchone()[0] == 0
    sql_before = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    statements = []
    conn.set_trace_callback(statements.append)
    db.init_db(conn)
    conn.set_trace_callback(None)
    assert not [s for s in statements if "DROP TRIGGER" in s.upper() or "ALTER TABLE" in s.upper()]
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == sql_before
    conn.close()


def test_restore_of_a_v23_backup_migrates_to_v24(auth_client, tmp_path, backup_key):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp24a-v23.cbk", header=23, fixture=V23_FIXTURE)
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("wp24a-e53f42d", 23)
    with app_stopped():
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 28   # WP-C: v28 (история фактов, защита истории); combined-preaudit (v27); WP2.4-C/D: текущая схема
    assert sorted(r[0] for r in conn.execute("SELECT creation_source FROM patients")) == (
        ["form", "import"] + ["legacy"] * 7 + ["migration"])
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()[0] == 1
    conn.close()


def test_backup_and_restore_of_v24_keep_keys_and_history(auth_client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    login(auth_client, DEV_USERNAME)
    key = _key()
    created = _create(auth_client, key, name="Синтетик Копии").json()["id"]
    raw = _file(_row("Синтетик Копии Импорта", "7700000000025901"))
    assert _import(auth_client, raw, key=_key()).status_code == 200
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "v24.cbk")
    db.reset_singleton_for_tests()
    with app_stopped():
        maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    assert maintenance.validate_backup(backup)["source_build"] == "current"
    conn = sqlite3.connect(str(config.DB_PATH))
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM import_files_processed").fetchone()[0] == 1
    conn.close()
    login(auth_client, DEV_USERNAME)
    again = _create(auth_client, key, name="Синтетик Копии")
    assert again.headers[REPLAYED] == "true" and again.json()["id"] == created


def test_schema_constraints_forbid_ambiguous_results(client):
    from backend.app import idempotency
    ids = _world()
    conn = get_conn()
    base = ("INSERT INTO idempotency_keys (actor_user_id, operation, key_hash, request_hash, state, status_code, "
            "result_patient_id, result_import_id, result_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'2026-10-01T00:00:00+00:00', '2026-10-08T00:00:00+00:00')")
    h = "a" * 64
    for values in [("patient.create", "completed", 201, "p1", "i1", None),       # два результата
                   ("patient.create", "completed", 201, None, None, None),       # нет результата
                   ("import.commit", "completed", 200, None, None, None),
                   ("import.commit", "completed", 201, None, "i1", "{}"),
                   ("patient.create", "refused", 409, "p1", None, None),          # отказ с результатом
                   ("patient.unknown", "completed", 201, "p1", None, None),
                   ("patient.create", "pending", 201, "p1", None, None)]:
        op, state, code, pid, iid, rj = values
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(base, (ids["doc.a"], op, h, h, state, code, pid, iid, rj))
        conn.rollback()
    login_ok = conn.execute(base, (ids["doc.a"], "patient.create", h, h, "completed", 201, "p1", None, None))
    assert login_ok.rowcount == 1
    with pytest.raises(sqlite3.IntegrityError):                                    # один ключ в области
        conn.execute(base, (ids["doc.a"], "patient.create", h, "b" * 64, "completed", 201, "p2", None, None))
    with pytest.raises(sqlite3.IntegrityError):                                    # одна карточка — один ключ
        conn.execute(base, (ids["doc.a"], "patient.create", "c" * 64, h, "completed", 201, "p1", None, None))
    with pytest.raises(sqlite3.IntegrityError, match="idempotency_result_immutable"):
        conn.execute("UPDATE idempotency_keys SET result_patient_id = 'p9'")
    conn.rollback()
    assert idempotency.STATE_REFUSED == "refused"


def test_refused_state_contract_for_wp24c(client):
    """refused (WP2.4-C, 409 POSSIBLE_DUPLICATE): то же содержимое оценивается заново, другое — 422."""
    from backend.app import idempotency, policy
    from backend.app.db import transaction
    from backend.app.models import PatientCreateIn
    ids = _world()
    actor = policy.load_current_user(get_conn(), ids["doc.a"])
    key = _key()
    body = PatientCreateIn(**make_patient_payload(name="Синтетик Отказа"))
    claim = idempotency.claim(actor.id, idempotency.OP_PATIENT_CREATE, key, body.model_dump(mode="json"))
    with transaction(get_conn()):
        idempotency.refuse(get_conn(), claim, 409)
    assert _keys()[0]["state"] == "refused"
    other = PatientCreateIn(**make_patient_payload(name="Синтетик Другой"))
    with pytest.raises(idempotency.IdempotencyKeyReused):
        _service().create_patient_card_idempotent(get_conn(), actor, other, key)
    card, replayed = _service().create_patient_card_idempotent(get_conn(), actor, body, key)
    assert replayed is False and _keys()[0]["state"] == "completed" and _keys()[0]["result_patient_id"] == card["id"]
    card2, replayed2 = _service().create_patient_card_idempotent(get_conn(), actor, body, key)
    assert replayed2 is True and card2["id"] == card["id"]


# ================================================================ static guards and client

def test_static_contract():
    app = PROJECT_ROOT / "backend" / "app"
    service = (app / "services" / "patient_create.py").read_text(encoding="utf-8")
    assert service.count("audit.record_required_or_fail(conn, audit.PATIENT_CREATED") == 1
    imports = (app / "routes" / "importexport.py").read_text(encoding="utf-8")
    migrate = (app / "routes" / "migrate.py").read_text(encoding="utf-8")
    for source in (imports, migrate):
        assert "import_operations.run(" in source and "in_open_transaction=True" in source
        assert 'creation_source="import"' in source or 'creation_source="migration"' in source
    module = (app / "idempotency.py").read_text(encoding="utf-8")
    assert "logger" not in module and "print(" not in module
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    helper = js[js.index("async function apiPostIdempotent"):]
    helper = helper[:helper.index("\n}\n")]
    assert "headers: { 'Idempotency-Key': key }" in helper and "console." not in helper and "?" not in helper.split("apiFetch(path")[1][:5]
    for call in ("apiPostIdempotent('/api/patients', payload, this.addPatientKey)",
                 "apiPostIdempotent('/api/import/commit', body, this.importCommitKey)",
                 "apiPostIdempotent('/api/migrate/localstorage/commit', body, this.migrationKey)"):
        assert call in js, call
    opener = js[js.index("    openAddModal() {"):]
    assert opener.index("this.addPatientKey = newIdempotencyKey()") < opener.index("\n    },\n")
    assert "localStorage.setItem" not in js[js.index("    async addPatient(event) {"):js.index("    async addPatient(event) {") + 6000]


# ================================================================ B-01. rights re-read under the write lock

def _no_import_traces():
    return {
        "patients": _rows("SELECT COUNT(*) AS c FROM patients")[0]["c"],
        "history": _rows("SELECT COUNT(*) AS c FROM history_entries")[0]["c"],
        "events": _rows("SELECT COUNT(*) AS c FROM events")[0]["c"],
        "keys": _rows("SELECT COUNT(*) AS c FROM idempotency_keys")[0]["c"],
        "files": _rows("SELECT COUNT(*) AS c FROM import_files_processed")[0]["c"],
        "ok_audit": _rows("SELECT COUNT(*) AS c FROM audit_events WHERE result = 'ok' AND event_type IN "
                          "('import_committed', 'migration_committed', 'import_replayed', 'migration_replayed')")[0]["c"],
    }


def _event_row(name="Синтетик Ожидания", policy="7700000000026001", doctor=None):
    row = _row(name, policy)
    row["history"] = [{"stage": "exam", "enteredAt": "2026-09-01T10:00:00+00:00",
                       "events": [{"date": "2026-11-20", "time": "09:30"}], "checklist": {}, "note": ""}]
    if doctor:
        row["treatingDoctorUserId"] = doctor
    return row


CHANGES = {
    "doctor_to_chair": ("doc.a", "UPDATE users SET role_id = 'consilium_chair' WHERE id = ?", "doc.a"),
    "doctor_blocked": ("doc.a", "UPDATE users SET status = 'blocked' WHERE id = ?", "doc.a"),
    "head_loses_department": ("head.1", "DELETE FROM department_heads WHERE user_id = ?", "head.1"),
}


@pytest.mark.parametrize("operation", ["import", "migration"])
@pytest.mark.parametrize("with_key", [True, False], ids=["key", "no_key"])
@pytest.mark.parametrize("change", list(CHANGES))
def test_rights_revoked_while_waiting_for_the_lock_refuse_without_changes(client, change, with_key, operation):
    from .test_wp24a_patient_create import _while_locked
    ids = _world()
    who, sql, target = CHANGES[change]
    tab = _client_for(client, who)
    raw = _file(_event_row(doctor=ids["doc.a"] if who == "head.1" else None))
    key = _key() if with_key else None
    before = _no_import_traces()
    send = (lambda: _import(tab, raw, key=key)) if operation == "import" else (lambda: _migrate(tab, raw, key))
    response, waited = _while_locked(send, sql, (ids[target],))
    assert waited and response.status_code == 403, (change, response.status_code, response.text)
    assert "Синтетик Ожидания" not in response.text
    assert _no_import_traces() == before


@pytest.mark.parametrize("operation", ["import", "migration"])
@pytest.mark.parametrize("who", ["doc.a", "head.1"])
def test_allowed_operation_after_waiting_succeeds_and_is_attributed_to_the_fresh_actor(client, who, operation):
    """Контроль: ожидание без изменения прав не мешает. Перемещение врача в другое
    отделение во время ожидания видно в аудите (актор перечитан под блокировкой);
    у заведующего во время ожидания меняется только ФИО."""
    from .test_wp24a_patient_create import _while_locked
    ids = _world()
    tab = _client_for(client, who)
    raw = _file(_event_row(doctor=ids["doc.a"] if who == "head.1" else None))
    send = (lambda: _import(tab, raw, key=_key())) if operation == "import" else (lambda: _migrate(tab, raw, _key()))
    if who == "doc.a":
        change = ("UPDATE user_departments SET department_id = ? WHERE user_id = ?", (ids["d2"], ids["doc.a"]))
    else:
        # Контроль для заведующего: отделения не меняются (у нескольких отделений
        # «основное» в аудите — первое из списка, порядок не фиксирован).
        change = ("UPDATE users SET full_name = 'Заведующая Переименованная' WHERE id = ?", (ids["head.1"],))
    response, waited = _while_locked(send, *change)
    assert waited and response.status_code == 200, response.text
    assert response.json()["added"] == 1 and _names() == ["Синтетик Ожидания"]
    event = "import_committed" if operation == "import" else "migration_committed"
    row = [r for r in _audit(event) if r["result"] == "ok"][0]
    expected_department = ids["d2"] if who == "doc.a" else ids["d1"]
    assert (row["actor_user_id"], row["actor_department_id"]) == (ids[who], expected_department)
    created_by = _rows("SELECT created_by_user_id FROM patients")[0]["created_by_user_id"]
    assert created_by == ids[who]


@pytest.mark.parametrize("operation", ["import", "migration"])
def test_valid_replay_while_waiting_returns_the_stored_result(client, operation):
    from .test_wp24a_patient_create import _while_locked
    ids = _world()
    who = "doc.a"
    tab = _client_for(client, who)
    raw = _file(_event_row())
    key = _key()
    send = (lambda: _import(tab, raw, key=key)) if operation == "import" else (lambda: _migrate(tab, raw, key))
    first = send()
    assert first.status_code == 200
    before = _no_import_traces()
    again, waited = _while_locked(send)
    assert waited and again.status_code == 200 and again.headers[REPLAYED] == "true"
    assert again.json() == first.json() and again.headers["Import-Id"] == first.headers["Import-Id"]
    after = _no_import_traces()
    assert {k: v for k, v in after.items() if k != "ok_audit"} == {k: v for k, v in before.items() if k != "ok_audit"}
    assert after["ok_audit"] == before["ok_audit"] + 1                      # только запись о повторе
    get_conn().execute("UPDATE users SET role_id = 'consilium_chair' WHERE id = ?", (ids[who],))
    get_conn().commit()
    refused, waited = _while_locked(send)
    assert refused.status_code == 403 and "Синтетик Ожидания" not in refused.text


# ================================================================ B-02. no false «not done» after a confirmed success

def _run_client_scenario(scenario: dict) -> dict:
    """Выполнить методы app.js (confirmImportCommit / runLegacyMigration) в Node
    с заглушками окружения; вернуть сообщения и состояние."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def method(name):
        start = js.index(f"    {name}(")
        return js[start:js.index("\n    },\n", start) + 7]

    names = ["async confirmImportCommit", "closeImportReviewModal", "async runLegacyMigration",
             "async refreshAfterSavedOperation"]
    # B-03: отдельная стадия удаления локальной копии (если она есть в этой версии app.js).
    names += [n for n in ("async removeLegacyLocalCopy",) if f"    {n}(" in js]
    # WP2.4-C: распознавание ответа «возможный дубль» в обработке ошибок импорта.
    names += [n for n in ("isPossibleDuplicate", "isDuplicateLimitExceeded") if f"    {n}(" in js]
    methods = "\n".join(method(n) for n in names)
    harness = r"""
const scenario = SCENARIO;
const toasts = [], removed = [], closed = [], keys = [];
let posts = 0, confirms = 0, keyCounter = 0;
const elements = {};
function $(id) {
  if (!elements[id]) elements[id] = { value: id === 'importModeSelect' ? 'add' : '', checked: true, disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
  return elements[id];
}
const ui = {
  toast: (message, type) => toasts.push({ type, message }),
  confirm: async () => {
    confirms += 1;
    if (confirms === 1) return true;
    if (scenario.confirmFail) throw new Error('диалог недоступен');
    return scenario.removeLocal;
  },
  closeModal: id => closed.push(id),
};
function newIdempotencyKey() { keyCounter += 1; return 'key-' + keyCounter; }
async function apiPost() { return { valid: true, totalRecords: 1, historicalDecisions: { required: false } }; }
async function apiPostIdempotent(path, body, key) {
  posts += 1; keys.push(key);
  if (scenario.commit === 'network') { const e = new Error('Нет ответа от сервера.'); e.network = true; throw e; }
  return { added: 1, updated: 0, unchanged: 0, skipped: 0 };
}
global.localStorage = { getItem: k => (k === 'patients' ? '[{"name":"x"}]' : null),
  removeItem: k => { if (scenario.removeFail === k) throw new Error('хранилище недоступно'); removed.push(k); } };
const app = {
METHODS
  importReport: { provenance: { confirmationRequired: false, fileSha256: 'a'.repeat(64) } },
  pendingImportRaw: '{"patients":[]}',
  loadPatients: async () => { if (scenario.fail === 'loadPatients') throw new Error('сбой обновления списка'); },
  loadActivity: async () => { if (scenario.fail === 'loadActivity') throw new Error('сбой обновления сводки'); },
  fillStaticSelects() {}, showView() {}, renderImportConsiliumImpact() {}, renderMigrationPanel() {},
};
(async () => {
  if (scenario.operation === 'import') await app.confirmImportCommit();
  else await app.runLegacyMigration();
  process.stdout.write(JSON.stringify({ toasts, posts, removed, closed, keys,
    importKey: app.importCommitKey || null, migrationKey: app.migrationKey || null }));
})();
"""
    script = harness.replace("SCENARIO", json.dumps(scenario)).replace("METHODS", methods)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("fail", ["loadPatients", "loadActivity"])
def test_import_success_with_a_failing_refresh_is_not_reported_as_failed(fail):
    result = _run_client_scenario({"operation": "import", "commit": "ok", "fail": fail})
    messages = [t["message"] for t in result["toasts"]]
    assert result["posts"] == 1
    assert not any("не выполнен" in m for m in messages), messages
    assert any(t["type"] == "good" and t["message"].startswith("Импорт завершён") for t in result["toasts"])
    assert any(t["type"] == "warning" and "Импорт сохранен" in t["message"] and "повторять операцию не нужно"
               in t["message"] for t in result["toasts"])
    assert "importReviewModal" in result["closed"] and result["importKey"] is None     # операция закрыта


def test_import_without_a_confirmed_answer_keeps_the_key_and_the_window():
    result = _run_client_scenario({"operation": "import", "commit": "network", "fail": None})
    assert [t["type"] for t in result["toasts"]] == ["danger"] and "Импорт не выполнен" in result["toasts"][0]["message"]
    assert result["importKey"] == result["keys"][0] and "importReviewModal" not in result["closed"]


@pytest.mark.parametrize("fail", ["loadPatients", "loadActivity"])
@pytest.mark.parametrize("remove_local", [True, False])
def test_migration_success_with_a_failing_refresh_is_not_reported_as_failed(fail, remove_local):
    result = _run_client_scenario({"operation": "migration", "commit": "ok", "fail": fail, "removeLocal": remove_local})
    messages = [t["message"] for t in result["toasts"]]
    assert result["posts"] == 1
    assert not any("не выполнен" in m for m in messages), messages
    assert any(t["type"] == "good" and "перенесены" in t["message"] for t in result["toasts"])
    assert any(t["type"] == "warning" and "Перенос сохранен" in t["message"] for t in result["toasts"])
    assert result["migrationKey"] is None
    # Локальная копия удаляется только после отдельного подтверждения пользователя.
    assert result["removed"] == (["patients", "user"] if remove_local else [])


def test_migration_without_a_confirmed_answer_keeps_the_key_and_the_local_copy():
    result = _run_client_scenario({"operation": "migration", "commit": "network", "fail": None, "removeLocal": True})
    assert [t["type"] for t in result["toasts"]] == ["danger"] and "Перенос не выполнен" in result["toasts"][0]["message"]
    assert result["migrationKey"] == result["keys"][0] and result["removed"] == []


# ================================================================ B-03. nothing after a confirmed migration says «not done»

def _assert_saved_migration(result):
    messages = [t["message"] for t in result["toasts"]]
    assert result["posts"] == 1, result                                   # второго commit нет
    assert not any("не выполнен" in m for m in messages), messages
    assert any(t["type"] == "good" and "перенесены" in t["message"] for t in result["toasts"])
    assert result["migrationKey"] is None                                 # операция закрыта


@pytest.mark.parametrize("key", ["patients", "user"])
def test_migration_saved_but_local_copy_removal_fails(key):
    result = _run_client_scenario({"operation": "migration", "commit": "ok", "fail": None, "removeLocal": True,
                                   "removeFail": key})
    _assert_saved_migration(result)
    assert any(t["type"] == "warning" and "Перенос сохранён, но не удалось удалить локальную копию" in t["message"]
               for t in result["toasts"]), result["toasts"]


def test_migration_saved_but_the_removal_dialog_fails():
    result = _run_client_scenario({"operation": "migration", "commit": "ok", "fail": None, "removeLocal": True,
                                   "confirmFail": True})
    _assert_saved_migration(result)
    assert result["removed"] == []                                        # без подтверждения копия не удаляется
    assert any(t["type"] == "warning" and "не удалось удалить локальную копию" in t["message"]
               for t in result["toasts"]), result["toasts"]


def test_migration_confirmed_removal_still_works():
    result = _run_client_scenario({"operation": "migration", "commit": "ok", "fail": None, "removeLocal": True})
    _assert_saved_migration(result)
    assert result["removed"] == ["patients", "user"]
    assert not any(t["type"] == "warning" for t in result["toasts"]), result["toasts"]


def test_migration_unknown_server_answer_still_keeps_the_key():
    result = _run_client_scenario({"operation": "migration", "commit": "network", "fail": None, "removeLocal": True})
    assert result["posts"] == 1 and result["migrationKey"] == result["keys"][0] and result["removed"] == []
    assert [t["type"] for t in result["toasts"]] == ["danger"] and "Перенос не выполнен" in result["toasts"][0]["message"]
