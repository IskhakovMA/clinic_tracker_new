"""WP2.4-C: safe detection of possible duplicate patients (schema v25).

A policy number that matches a card INSIDE the employee's access scope gives
409 POSSIBLE_DUPLICATE; a confirmation with a reason that exactly matches the
list recomputed under the write lock lets the operation proceed. Matches outside
the scope are never searched and change nothing in any answer. No automatic
merge, no deletion, no global search. Synthetic data only.
"""
import json
import logging
import re
import sqlite3
import time
import uuid

import pytest

from .conftest import pre_v28_rows, card_version, create_empty_card, app_stopped, DEV_USERNAME, PROJECT_ROOT, get_conn, login, make_patient_payload
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _parallel, _rows
from .test_wp24a_patient_create import _db_path, _while_locked, _world

V24_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp24c_v24_synthetic.sql"
REPLAYED = "Idempotent-Replayed"
X = "7700000000031001"
Y = "7700000000031002"
REASON = "Синтетическая причина: второй полис временно совпадает <script>alert(1)</script>"
MATCH_KEYS = {"id", "name", "age", "treatingDoctorName"}


# ---------------------------------------------------------------- helpers

def _key():
    return str(uuid.uuid4())


def _create(client, policy=X, key=None, confirm=None, **kw):
    body = make_patient_payload(policyNumber=policy, **kw)
    if confirm is not None:
        body["duplicateConfirmation"] = confirm
    headers = {**client.csrf, **({"Idempotency-Key": key} if key else {})}
    return client.post("/api/patients", json=body, headers=headers)


def _confirm(ids, reason=REASON):
    return {"reason": reason, "matchedPatientIds": list(ids)}


def _patch_policy(client, pid, value, confirm=None):
    body = {"field": "policyNumber", "value": value, "expectedVersion": card_version(pid)}
    if confirm is not None:
        body["duplicateConfirmation"] = confirm
    return client.patch(f"/api/patients/{pid}", json=body, headers=client.csrf)


def _file(*rows):
    return json.dumps({"schemaVersion": 1, "patients": list(rows)}, ensure_ascii=False)


def _row(name, policy="", **kw):
    return {"name": name, "age": 60, "priority": "normal", "stage": "exam", "phone": "", "policyNumber": policy,
            "diagnosis": "C34.1", "nosology": "lung34", "head": "Синтетов", **kw}


def _sha(raw):
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _import(client, raw, mode="add", key=None, confirm=None, repeat=False, url="/api/import/commit"):
    body = {"raw": raw, "mode": mode}
    if url == "/api/import/commit":
        body["unsignedConfirmation"] = {"confirmed": True, "fileSha256": _sha(raw), "mode": mode}
    if confirm is not None:
        body["duplicateConfirmation"] = confirm
    if repeat:
        body["repeatImportConfirmation"] = {"confirmed": True, "fileSha256": _sha(raw), "mode": mode,
                                            "action": "repeat_processed_file"}
    return client.post(url, json=body, headers={**client.csrf, **({"Idempotency-Key": key} if key else {})})


def _patients():
    return _rows("SELECT id, name, policy_number, treating_doctor_user_id FROM patients ORDER BY created_at, id")


def _confirmations():
    return _rows("SELECT * FROM patient_duplicate_confirmations ORDER BY created_at, id")


def _audit(event_type):
    return _rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", event_type)


def _restore_audit():
    get_conn().execute("DROP TRIGGER IF EXISTS wp23a_audit_down")
    get_conn().commit()


def _med_snapshot():
    conn = get_conn()
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]  # nosec - test constant
            for t in ("history_entries", "events", "patient_diagnosis_history", "oncology_consilium_appointments")}


def _shape(response):
    """Ответ без значений, зависящих от конкретной карточки и времени."""
    body = response.json()
    volatile = {"id", "policyNumber", "arrival", "updatedAt", "createdAt", "name"}

    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in volatile and not k.endswith("At")}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    headers = {k.lower() for k in response.headers} - {"date", "content-length", "x-request-id"}
    return response.status_code, strip(body), sorted(headers)


# ================================================================ 1–4. scope of the search

def test_match_in_own_scope_is_409_with_minimal_visible_data(client):
    ids = _world()
    login(client, "doc.a")
    first = _create(client, name="Синтетик Первый")
    before = _patients()
    second = _create(client, name="Синтетик Второй")
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "POSSIBLE_DUPLICATE"
    assert [set(m) for m in body["matches"]] == [MATCH_KEYS]
    assert body["matches"][0] == {"id": first.json()["id"], "name": "Синтетик Первый", "age": 50,
                                  "treatingDoctorName": "Синтетова Анна Врачевна"}
    for hidden in (X, "C34.1", "Жалобы для теста", "history", "diagnosis", "consilium"):
        assert hidden not in second.text, hidden
    assert _patients() == before and _confirmations() == []
    assert ids["doc.a"]


def test_match_outside_scope_changes_nothing(client):
    _world()
    login(client, "doc.b")                                    # другое отделение
    assert _create(client, name="Синтетик Чужой").status_code == 201
    login(client, "doc.a")
    outside = _create(client, name="Синтетик Свой")           # тот же полис только вне области
    nobody = _create(client, policy=Y, name="Синтетик Свой")  # полиса нет ни у кого
    assert outside.status_code == nobody.status_code == 201
    assert _shape(outside) == _shape(nobody)
    assert "Синтетик Чужой" not in outside.text
    assert _confirmations() == [] and _audit("patient_duplicate_confirmed") == []


@pytest.mark.parametrize("creator,owner,expected", [
    ("doc.a", "doc.a", 409),        # своя карточка
    ("doc.a2", "doc.a", 201),       # коллега того же отделения: карточка вне области врача
    ("head.1", "doc.a", 409),       # заведующий — свое отделение
    ("head.1", "doc.b", 201),       # заведующий — чужое отделение
    ("dev", "doc.b", 409),          # разработчик — глобальная область
    ("doc.b", "doc.a", 201),
])
def test_scope_matrix(client, creator, owner, expected):
    ids = _world()
    login(client, owner)
    assert _create(client, name="Синтетик Владельца").status_code == 201
    login(client, DEV_USERNAME if creator == "dev" else creator)
    kw = {"treatingDoctorUserId": ids["doc.a"]} if creator in ("head.1", "dev") else {}
    response = _create(client, name="Синтетик Нового", **kw)
    assert response.status_code == expected, (creator, owner, response.text)
    if expected == 201:
        assert "Синтетик Владельца" not in response.text


def test_chair_still_cannot_create(client):
    _world()
    login(client, "chair")
    assert _create(client).status_code == 403


def test_search_query_is_restricted_by_the_scope_itself(client, monkeypatch):
    """L2: без условия области совпадений нет даже для своей карточки — поиск идет
    только запросом с условием области, глобального поиска с фильтрацией нет."""
    from backend.app import identity, policy
    ids = _world()
    login(client, "doc.a")
    _create(client)
    actor = policy.load_current_user(get_conn(), ids["doc.a"])
    assert len(identity.policy_matches(get_conn(), actor, X)) == 1
    monkeypatch.setattr(policy, "patient_scope_filter", lambda user: ("0 = 1", []))
    assert identity.policy_matches(get_conn(), actor, X) == []
    assert identity.policy_matches_many(get_conn(), actor, [X]) == {}
    source = (PROJECT_ROOT / "backend" / "app" / "identity.py").read_text(encoding="utf-8")
    assert source.count("patient_scope_filter(actor)") == 2 and source.count("WHERE ({cond})") == 2


# ================================================================ 5–7, 9. confirmation contract

def test_confirmed_creation_is_stored_and_audited_without_the_reason(client):
    ids = _world()
    login(client, "doc.a")
    first = _create(client, name="Синтетик Первый").json()["id"]
    response = _create(client, name="Синтетик Второй", confirm=_confirm([first]))
    assert response.status_code == 201, response.text
    new_id = response.json()["id"]
    rows = _confirmations()
    assert len(rows) == 1
    row = rows[0]
    assert (row["operation"], row["patient_id"], json.loads(row["matched_patient_ids"]), row["created_by_user_id"],
            row["import_id"]) == ("create", new_id, [first], ids["doc.a"], None)
    assert row["reason"] == REASON
    audit = _audit("patient_duplicate_confirmed")
    assert len(audit) == 1 and audit[0]["patient_id"] == new_id and audit[0]["detail"] == "operation=create matches=1"
    assert len(_audit("patient_created")) == 2
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False)
    assert "Синтетическая причина" not in dump and X not in dump


@pytest.mark.parametrize("confirm", [
    {"reason": "", "matchedPatientIds": ["x"]}, {"reason": "   ", "matchedPatientIds": ["x"]},
    {"reason": "Причина", "matchedPatientIds": []}, {"reason": "Причина"}, {"matchedPatientIds": ["x"]},
    {"reason": "a" * 2001, "matchedPatientIds": ["x"]}, {"reason": "Причина", "matchedPatientIds": ["x", "x"]},
    {"reason": "Причина\x00", "matchedPatientIds": ["x"]}, {"reason": "Причина", "matchedPatientIds": ["x"], "extra": 1},
])
def test_invalid_confirmation_is_422(client, confirm):
    _world()
    login(client, "doc.a")
    _create(client)
    before = _patients()
    response = _create(client, confirm=confirm)
    assert response.status_code == 422 and _patients() == before


def test_foreign_or_forged_ids_never_confirm_and_disclose_nothing(client):
    ids = _world()
    login(client, "doc.b")
    foreign = _create(client, name="Синтетик Чужой").json()["id"]                 # тот же полис, чужая область
    login(client, "doc.a")
    own = _create(client, name="Синтетик Свой").json()["id"]
    before = _patients()
    with_foreign = _create(client, confirm=_confirm([own, foreign]))
    only_foreign = _create(client, confirm=_confirm([foreign]))
    nonexistent = _create(client, confirm=_confirm(["f" * 32]))
    assert {r.status_code for r in (with_foreign, only_foreign, nonexistent)} == {409}
    assert only_foreign.json() == nonexistent.json()                    # чужой и несуществующий неотличимы
    for r in (with_foreign, only_foreign, nonexistent):
        assert [m["id"] for m in r.json()["matches"]] == [own] and "Синтетик Чужой" not in r.text
    assert _patients() == before and ids["doc.b"]


def test_confirmation_without_any_visible_match_is_not_used(client):
    """Подтверждать нечего: создание как обычно, присланный список не сохраняется."""
    _world()
    login(client, "doc.a")
    response = _create(client, policy=Y, confirm=_confirm(["f" * 32]))
    assert response.status_code == 201 and _confirmations() == []


def test_stale_confirmation_is_409_without_creation(client):
    _world()
    login(client, "doc.a")
    first = _create(client, name="Синтетик Первый").json()["id"]
    second = _create(client, name="Синтетик Второй", confirm=_confirm([first])).json()["id"]
    before = _patients()
    stale = _create(client, name="Синтетик Третий", confirm=_confirm([first]))
    assert stale.status_code == 409
    assert sorted(m["id"] for m in stale.json()["matches"]) == sorted([first, second])
    assert _patients() == before and len(_confirmations()) == 1


# ================================================================ 8. rights change while waiting for the lock

@pytest.mark.parametrize("change,expected", [
    ("UPDATE users SET role_id = 'consilium_chair' WHERE id = ?", 403),
    ("UPDATE users SET status = 'blocked' WHERE id = ?", 403),
])
def test_rights_revoked_while_waiting_refuse_confirmed_creation(client, change, expected):
    ids = _world()
    tab = _client_for(client, "doc.a")
    first = _create(tab).json()["id"]
    before = (_patients(), _confirmations())
    response, waited = _while_locked(lambda: _create(tab, confirm=_confirm([first])), change, (ids["doc.a"],))
    assert waited and response.status_code == expected and (_patients(), _confirmations()) == before
    assert "Тестов" not in response.text


def test_match_leaving_the_scope_while_waiting_reveals_nothing(client):
    """Совпадающая карточка переназначена в другое отделение во время ожидания:
    ответ — обычное создание, без сведений о ней и без записи подтверждения."""
    ids = _world()
    tab = _client_for(client, "doc.a")
    first = _create(tab, name="Синтетик Уходящий").json()["id"]
    response, waited = _while_locked(lambda: _create(tab, confirm=_confirm([first])),
                                     "UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?",
                                     (ids["doc.b"], first))
    assert waited and response.status_code == 201 and "Синтетик Уходящий" not in response.text
    assert _confirmations() == []


def test_head_losing_the_department_while_waiting_is_refused(client):
    ids = _world()
    head = _client_for(client, "head.1")
    first = _create(head, treatingDoctorUserId=ids["doc.a"]).json()["id"]
    before = _patients()
    response, waited = _while_locked(
        lambda: _create(head, treatingDoctorUserId=ids["doc.a"], confirm=_confirm([first])),
        "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    assert waited and response.status_code == 403 and _patients() == before


# ================================================================ 10–12. normalisation and false matches

def test_normalised_policy_variants_match(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    ids = _world()
    data = PatientCreateIn(**make_patient_payload(name="Синтетик Исторический"))
    legacy = store.create_patient(get_conn(), data, ids["doc.a"])
    # Историческое значение с пробелами (до нормализации при записи) — прямо в БД.
    get_conn().execute("UPDATE patients SET policy_number = '7700 0000 0003 1001' WHERE id = ?", (legacy,))
    get_conn().commit()
    login(client, "doc.a")
    response = _create(client, policy="7700-0000-0003-1001")
    assert response.status_code == 409 and response.json()["matches"][0]["id"] == legacy


def test_cyrillic_and_latin_lookalikes_in_policy_match(client):
    _world()
    login(client, "doc.a")
    assert _create(client, policy="АВ123КМ").status_code == 201               # кириллица
    assert _create(client, policy="ab123km").status_code == 409               # латиница, нижний регистр


def test_canonical_forms():
    from backend.app.identity import canonical_name, canonical_policy
    assert canonical_policy(" 7700 0000–0003-1001 ") == "7700000000031001"
    assert canonical_policy("АВ-12") == canonical_policy("ab 12") == canonical_policy("AB12")
    assert canonical_policy("") == canonical_policy("  - ") == canonical_policy(None) == ""
    assert canonical_policy("770000000003100") != canonical_policy("7700000000031001")
    assert canonical_name("  Ивaнов   Иван ") == canonical_name("ИВАНОВ иван") == "иванов иван"   # латинская «a»
    assert canonical_name("Ёлкин") == canonical_name("Елкин")
    assert canonical_name("Иванов Иван") != canonical_name("Иванова Ивана")
    assert canonical_name("Ivanov") != canonical_name("Иванов")             # транслитерации нет


def test_same_name_alone_never_warns(client):
    _world()
    login(client, "doc.a")
    assert _create(client, policy=X, name="Синтетик Тезка").status_code == 201
    assert _create(client, policy=Y, name="Синтетик Тезка").status_code == 201
    assert _create(client, policy="", name="Синтетик Тезка").status_code == 201
    assert _create(client, policy="", name="Синтетик Тезка").status_code == 201


def test_empty_policies_never_warn(client):
    _world()
    login(client, "doc.a")
    codes = [_create(client, policy=p, name=f"Синтетик {i}").status_code for i, p in enumerate(["", "", " ", " - "])]
    assert codes == [201, 201, 201, 201] and _confirmations() == []


# ================================================================ 13. policy edit

def test_policy_edit_warns_excludes_own_card_and_keeps_history(client):
    ids = _world()
    login(client, "doc.a")
    a = _create(client, policy=X, name="Синтетик А", events=[{"date": "2026-11-20", "time": "10:00"}]).json()["id"]
    b = _create(client, policy=Y, name="Синтетик Б").json()["id"]
    med = _med_snapshot()
    # Тот же номер в другом написании — изменения нет, предупреждения нет.
    assert _patch_policy(client, a, "7700-0000-0003-1001").status_code == 200
    warned = _patch_policy(client, b, X)
    assert warned.status_code == 409 and [m["id"] for m in warned.json()["matches"]] == [a]
    assert _rows("SELECT policy_number FROM patients WHERE id = ?", b)[0]["policy_number"] == Y
    ok = _patch_policy(client, b, X, confirm=_confirm([a]))
    assert ok.status_code == 200 and ok.json()["policyNumber"] == X
    row = _confirmations()[0]
    assert (row["operation"], row["patient_id"], json.loads(row["matched_patient_ids"])) == ("policy_update", b, [a])
    assert _med_snapshot() == med
    assert ids["doc.a"]


def test_policy_edit_rules_do_not_open_foreign_cards(client):
    _world()
    login(client, "doc.b")
    foreign = _create(client, policy=Y, name="Синтетик Чужой").json()["id"]
    login(client, "doc.a")
    own = _create(client, policy=X).json()["id"]
    assert _patch_policy(client, foreign, X).status_code == 404
    assert _patch_policy(client, foreign, X, confirm=_confirm([own])).status_code == 404
    response = _patch_policy(client, own, Y)                   # совпадение только вне области
    assert response.status_code == 200 and "Синтетик Чужой" not in response.text
    other = client.patch(f"/api/patients/{own}", json={"expectedVersion": card_version(own), "field": "name", "value": "Новое Имя",
                                                        "duplicateConfirmation": _confirm([own])}, headers=client.csrf)
    assert other.status_code == 422
    login(client, "deputy")
    assert _patch_policy(client, own, X).status_code == 403


def test_policy_edit_audit_failure_rolls_back(client):
    _world()
    login(client, "doc.a")
    a = _create(client, policy=X).json()["id"]
    b = _create(client, policy=Y).json()["id"]
    _fail_audit("patient_duplicate_confirmed")
    response = _patch_policy(client, b, X, confirm=_confirm([a]))
    assert response.status_code == 503
    assert _rows("SELECT policy_number FROM patients WHERE id = ?", b)[0]["policy_number"] == Y
    assert _confirmations() == []


# ================================================================ 14. import

def test_import_requires_confirmation_of_the_whole_visible_set(client):
    ids = _world()
    login(client, "doc.a")
    p1 = _create(client, policy=X, name="Синтетик Карточка 1").json()["id"]
    p2 = _create(client, policy=Y, name="Синтетик Карточка 2").json()["id"]
    raw = _file(_row("Синтетик Файл А", X), _row("Синтетик Файл Б", Y), _row("Синтетик Файл В", "7700000000031099"))
    preview = client.post("/api/import/preview", json={"raw": raw}).json()
    assert preview["possibleDuplicates"]["add"]["matchedPatientIds"] == sorted([p1, p2])
    assert [r["row"] for r in preview["possibleDuplicates"]["add"]["rows"]] == [1, 2]
    assert "update" not in preview["possibleDuplicates"]
    before = _patients()
    refused = _import(client, raw, key=_key())
    assert refused.status_code == 409 and refused.json()["code"] == "POSSIBLE_DUPLICATE"
    assert refused.json()["matchedPatientIds"] == sorted([p1, p2]) and X not in refused.text
    partial = _import(client, raw, key=_key(), confirm=_confirm([p1]))     # только одна из карточек
    assert partial.status_code == 409
    assert _patients() == before and _rows("SELECT * FROM import_files_processed") == []
    ok = _import(client, raw, key=_key(), confirm=_confirm([p1, p2]))
    assert ok.status_code == 200 and ok.json()["added"] == 3
    rows = _confirmations()
    assert [(r["operation"], json.loads(r["matched_patient_ids"])) for r in rows] in (
        [("import", [p1]), ("import", [p2])], [("import", [p2]), ("import", [p1])])
    assert {r["import_id"] for r in rows} == {ok.headers["Import-Id"]}
    assert len(_audit("patient_duplicate_confirmed")) == 2 and ids["doc.a"]


def test_import_keeps_u9_u4_and_ignores_update(client):
    _world()
    login(client, "doc.a")
    p1 = _create(client, policy=X).json()["id"]
    inside = _file(_row("Синтетик 1", X), _row("Синтетик 2", X))           # U9 — повтор внутри файла
    response = _import(client, inside, key=_key(), confirm=_confirm([p1]))
    assert response.status_code == 422 and response.json()["code"] == "FILE_POLICY_DUPLICATES"
    raw = _file(_row("Синтетик Повтор", X))
    assert _import(client, raw, key=_key(), confirm=_confirm([p1])).status_code == 200
    created = [p["id"] for p in _patients() if p["name"] == "Синтетик Повтор"][0]
    # U4 раньше проверки дублей: о повторе своего файла сотрудник узнает первым.
    history = _import(client, raw, key=_key(), confirm=_confirm([p1, created]))
    assert history.status_code == 428 and history.json()["code"] == "FILE_ALREADY_IMPORTED"
    again = _import(client, raw, key=_key(), confirm=_confirm([p1]), repeat=True)   # совпадений стало больше
    assert again.status_code == 409 and sorted(again.json()["matchedPatientIds"]) == sorted([p1, created])
    final = _import(client, raw, key=_key(), confirm=_confirm([p1, created]), repeat=True)
    assert final.status_code == 200


def test_import_matches_outside_scope_change_nothing(client):
    _world()
    raw = _file(_row("Синтетик Импорта", X))
    login(client, "doc.a")
    nobody = client.post("/api/import/preview", json={"raw": raw}).json()
    login(client, "doc.b")
    _create(client, policy=X, name="Синтетик Чужой")
    login(client, "doc.a")
    outside = client.post("/api/import/preview", json={"raw": raw}).json()
    assert outside == nobody and "Синтетик Чужой" not in json.dumps(outside, ensure_ascii=False)
    assert _import(client, raw, key=_key()).status_code == 200


def test_import_audit_failure_rolls_back_everything(client):
    _world()
    login(client, "doc.a")
    p1 = _create(client, policy=X).json()["id"]
    before = _patients()
    _fail_audit("patient_duplicate_confirmed")
    raw = _file(_row("Синтетик Сбой", X), _row("Синтетик Без Совпадения", Y))
    response = _import(client, raw, key=_key(), confirm=_confirm([p1]))
    assert response.status_code == 503
    assert _patients() == before and _confirmations() == [] and _rows("SELECT * FROM idempotency_keys") == []


def test_migration_with_a_match_is_impossible_in_an_empty_base(client):
    """Перенос возможен только в пустую базу — совпадений с карточками быть не может."""
    _world()
    login(client, DEV_USERNAME)
    raw = _file(_row("Синтетик Переноса", X))
    assert _import(client, raw, url="/api/migrate/localstorage/commit", key=_key()).status_code == 200


# ================================================================ 15. races

def test_form_against_form_same_policy(client):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    for i in range(10):
        policy = f"77000000000320{i:02d}"
        res = _parallel(lambda: _create(tabs[0], policy=policy, key=_key()),
                        lambda: _create(tabs[1], policy=policy, key=_key()))
        assert sorted(r.status_code for r, *_ in res) == [201, 409], [r.text for r, *_ in res]
    assert len(_patients()) == 10


def test_form_against_import_and_import_against_import(client, monkeypatch):
    from backend.app import security
    monkeypatch.setattr(security, "check_request_rate_limit", lambda *a, **k: 0)
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    for i in range(5):
        policy = f"77000000000330{i:02d}"
        res = _parallel(lambda: _create(tabs[0], policy=policy, key=_key()),
                        lambda: _import(tabs[1], _file(_row(f"Синтетик Импорт {i}", policy)), key=_key()))
        assert sorted(r.status_code for r, *_ in res) in ([200, 409], [201, 409]), [r.text for r, *_ in res]
        policy2 = f"77000000000340{i:02d}"
        res = _parallel(lambda: _import(tabs[0], _file(_row(f"Синтетик А {i}", policy2)), key=_key()),
                        lambda: _import(tabs[1], _file(_row(f"Синтетик Б {i}", policy2)), key=_key()))
        assert sorted(r.status_code for r, *_ in res) == [200, 409], [r.text for r, *_ in res]
    assert len(_patients()) == 10


def test_creation_against_policy_update(client):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    for i in range(5):
        target = f"77000000000350{i:02d}"
        existing = _create(tabs[0], policy=f"77000000000360{i:02d}").json()["id"]
        res = _parallel(lambda: _create(tabs[0], policy=target, key=_key()),
                        lambda: _patch_policy(tabs[1], existing, target))
        assert sorted(r.status_code for r, *_ in res) in ([200, 409], [201, 409]), [r.text for r, *_ in res]


def test_two_confirmations_with_the_same_list_only_one_wins(client):
    _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a")]
    first = _create(tabs[0]).json()["id"]
    res = _parallel(lambda: _create(tabs[0], key=_key(), confirm=_confirm([first])),
                    lambda: _create(tabs[1], key=_key(), confirm=_confirm([first])))
    assert sorted(r.status_code for r, *_ in res) == [201, 409]
    assert len(_patients()) == 2 and len(_confirmations()) == 1


# ================================================================ 16. idempotency contract

def test_idempotency_contract_with_possible_duplicates(client):
    _world()
    login(client, "doc.a")
    first = _create(client, name="Синтетик Первый").json()["id"]
    k1 = _key()
    refused = _create(client, key=k1, name="Синтетик Второй")
    assert refused.status_code == 409 and REPLAYED not in refused.headers
    rows = _rows("SELECT state, status_code FROM idempotency_keys")
    assert rows == [{"state": "refused", "status_code": 409}]
    again = _create(client, key=k1, name="Синтетик Второй")                 # тот же ключ, то же тело
    assert again.status_code == 409 and len(_patients()) == 1
    assert _rows("SELECT state FROM idempotency_keys") == [{"state": "refused"}]
    reused = _create(client, key=k1, name="Синтетик Второй", confirm=_confirm([first]))
    assert reused.status_code == 422 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    k2 = _key()
    created = _create(client, key=k2, name="Синтетик Второй", confirm=_confirm([first]))
    assert created.status_code == 201
    replay = _create(client, key=k2, name="Синтетик Второй", confirm=_confirm([first]))
    assert replay.status_code == 201 and replay.headers[REPLAYED] == "true"
    assert replay.json()["id"] == created.json()["id"] and len(_patients()) == 2
    assert len(_confirmations()) == 1 and len(_audit("patient_duplicate_confirmed")) == 1
    with pytest.raises(sqlite3.IntegrityError, match="idempotency_result_immutable"):
        get_conn().execute("UPDATE idempotency_keys SET status_code = 201 WHERE state = 'completed'")
    get_conn().rollback()


def test_refused_key_is_reevaluated_when_the_match_is_gone(client):
    """Контракт refused (WP2.4-B/C): тот же ключ и тело оцениваются заново; пока
    совпадение есть — 409 без создания; исчезло — обычное создание."""
    _world()
    login(client, "doc.a")
    # WP-C FIX1-P1: совпадение исчезает удалением — удалить можно только пустую карточку.
    first = create_empty_card(client, name="Синтетик Первый", policyNumber=X)
    key = _key()
    assert _create(client, key=key, name="Синтетик Второй").status_code == 409
    assert client.delete(f"/api/patients/{first}", headers=client.csrf).status_code == 200
    after = _create(client, key=key, name="Синтетик Второй")
    assert after.status_code == 201 and REPLAYED not in after.headers
    assert _rows("SELECT state FROM idempotency_keys") == [{"state": "completed"}]


def test_lost_response_of_a_confirmed_creation_is_replayed(client, monkeypatch):
    from contextlib import contextmanager
    from backend.app.services import patient_create
    _world()
    login(client, "doc.a")
    first = _create(client).json()["id"]
    real = patient_create.transaction
    state = {"crash": True}

    @contextmanager
    def crash_after_commit(conn):
        with real(conn) as inner:
            yield inner
        if state.pop("crash", False):
            raise ConnectionResetError("synthetic: response lost after commit")

    monkeypatch.setattr(patient_create, "transaction", crash_after_commit)
    key = _key()
    lost = _create(client, key=key, confirm=_confirm([first]))
    assert lost.status_code == 500 and len(_patients()) == 2
    again = _create(client, key=key, confirm=_confirm([first]))
    assert again.status_code == 201 and again.headers[REPLAYED] == "true"
    assert len(_patients()) == 2 and len(_confirmations()) == 1


def test_import_idempotency_with_possible_duplicates(client):
    _world()
    login(client, "doc.a")
    p1 = _create(client, policy=X).json()["id"]
    raw = _file(_row("Синтетик Импорта", X))
    k1 = _key()
    assert _import(client, raw, key=k1).status_code == 409
    assert _rows("SELECT operation, state FROM idempotency_keys") == [{"operation": "import.commit", "state": "refused"}]
    assert _import(client, raw, key=k1, confirm=_confirm([p1])).status_code == 422
    k2 = _key()
    ok = _import(client, raw, key=k2, confirm=_confirm([p1]))
    again = _import(client, raw, key=k2, confirm=_confirm([p1]))
    assert ok.status_code == again.status_code == 200 and again.headers[REPLAYED] == "true"
    assert again.headers["Import-Id"] == ok.headers["Import-Id"] and len(_confirmations()) == 1


# ================================================================ 17–19. audit, logs, leaks

def test_audit_failure_of_a_confirmed_creation_rolls_back_everything(client):
    _world()
    login(client, "doc.a")
    first = _create(client).json()["id"]
    before = _patients()
    _fail_audit("patient_duplicate_confirmed")
    key = _key()
    response = _create(client, key=key, confirm=_confirm([first]))
    assert response.status_code == 503
    assert _patients() == before and _confirmations() == [] and _rows("SELECT * FROM idempotency_keys") == []
    _restore_audit()
    assert _create(client, key=key, confirm=_confirm([first])).status_code == 201


def test_no_reason_or_patient_data_in_audit_or_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    _world()
    login(client, "doc.a")
    first = _create(client, name="Синтетик Журнала").json()["id"]
    _create(client, name="Синтетик Журнала Два")
    _create(client, name="Синтетик Журнала Два", confirm=_confirm([first]))
    b = _create(client, policy=Y).json()["id"]
    _patch_policy(client, b, X)
    _patch_policy(client, b, X, confirm=_confirm(sorted([first, _patients()[1]["id"]])))
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False) + caplog.text
    for value in ("Синтетическая причина", "<script>", X, "Синтетик Журнала"):
        assert value not in dump, value


def _without_time(value):
    if isinstance(value, dict):
        return {k: _without_time(v) for k, v in value.items() if not (k.endswith("_at") or k.endswith("At"))}
    if isinstance(value, list):
        return [_without_time(v) for v in value]
    return value


def test_summaries_and_notifications_do_not_depend_on_foreign_matches(client):
    """L7/L8: сводка и список «Качества данных», уведомления, аналитика и preview
    сотрудника одинаковы при наличии и отсутствии совпадающей карточки в чужом отделении."""
    _world()
    raw = _file(_row("Синтетик Сводки", X))

    def views():
        login(client, "doc.a")
        return _without_time({
            "dq": client.get("/api/data-quality/summary").json(),
            "issues": client.get("/api/data-quality/issues?limit=200").json(),
            "notifications": client.get("/api/internal-notifications/summary").json(),
            "analytics": client.get("/api/analytics/dashboard").json(),
            "preview": client.post("/api/import/preview", json={"raw": raw}).json(),
        })

    login(client, "doc.a")
    _create(client, policy=Y, name="Синтетик Свой")
    before = views()
    login(client, "doc.b")
    _create(client, policy=X, name="Синтетик Чужой")
    after = views()
    assert after == before
    assert "Синтетик Чужой" not in json.dumps(after, ensure_ascii=False)


def test_analytics_does_not_use_the_new_duplicate_signals(client):
    """«Не менять аналитику до этапа D»: совпадение полиса видно в «Качестве данных»,
    но не меняет показатели аналитики (сравнение с теми же карточками без совпадения)."""
    _world()
    login(client, "doc.a")
    a = _create(client, policy=X, name="Синтетик Один", phone="").json()["id"]
    b = _create(client, policy=Y, name="Синтетик Два", phone="").json()["id"]
    analytics_before = _without_time([client.get(f"/api/analytics/{p}").json() for p in ("dashboard", "data-quality")])
    assert _patch_policy(client, b, X, confirm=_confirm([a])).status_code == 200
    groups = client.get("/api/data-quality/issues?category=duplicates&limit=200").json()["duplicate_groups"]
    assert [g["reason_codes"] for g in groups] == [["policy"]]
    analytics_after = _without_time([client.get(f"/api/analytics/{p}").json() for p in ("dashboard", "data-quality")])
    assert analytics_after == analytics_before


# ================================================================ 10 (quality). data quality

def test_data_quality_groups_by_policy_and_lookalike_names_inside_the_scope(client):
    _world()
    login(client, "doc.a")
    first = _create(client, policy=X, name="Синтетик Полиса", phone="").json()["id"]
    second = _create(client, policy=X, name="Другое Имя", phone="", confirm=_confirm([first])).json()["id"]
    _create(client, policy="7700000000037001", name="Сидорoв Пётр", age=44, phone="")        # латинская «o»
    _create(client, policy="7700000000037002", name="СИДОРОВ  Петр", age=44, phone="")
    login(client, "doc.b")
    _create(client, policy=X, name="Синтетик Чужой", phone="")
    login(client, "doc.a")
    groups = client.get("/api/data-quality/issues?category=duplicates&limit=200").json()["duplicate_groups"]
    by_reason = {tuple(g["reason_codes"]): sorted(g["patient_ids"]) for g in groups}
    assert by_reason[("policy",)] == sorted([first, second])
    assert any("name_age" in g["reason_codes"] for g in groups)
    assert all("Синтетик Чужой" not in json.dumps(g, ensure_ascii=False) for g in groups)
    policy_group = [g for g in groups if "policy" in g["reason_codes"]][0]
    assert policy_group["reasons"][0] == "Совпадает номер полиса ОМС"
    assert "не означает" in policy_group["explanation"]


# ================================================================ 20–21. schema v25, migration, backups

def test_schema_constraints_and_append_only(client):
    ids = _world()
    conn = get_conn()
    insert = ("INSERT INTO patient_duplicate_confirmations (id, operation, patient_id, matched_patient_ids, reason, "
              "created_by_user_id, created_at, import_id) VALUES (?, ?, ?, ?, ?, ?, '2026-10-01', ?)")
    bad = [("c1", "merge", "p", '["a"]', "r", None), ("c2", "create", "p", '[]', "r", None),
           ("c3", "create", "p", '{"a":1}', "r", None), ("c4", "create", "p", "not json", "r", None),
           ("c5", "create", "p", '["a"]', " ", None), ("c6", "create", "p", '["a"]', "r" * 2001, None),
           ("c7", "import", "p", '["a"]', "r", None), ("c8", "create", "p", '["a"]', "r", "imp")]
    for cid, op, pid, matched, reason, imp in bad:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (cid, op, pid, matched, reason, ids["doc.a"], imp))
        conn.rollback()
    conn.execute(insert, ("ok", "create", "p", '["a"]', "r", ids["doc.a"], None))
    conn.commit()
    for sql in ("UPDATE patient_duplicate_confirmations SET reason = 'x'", "DELETE FROM patient_duplicate_confirmations"):
        with pytest.raises(sqlite3.IntegrityError, match="duplicate_confirmation_immutable"):
            conn.execute(sql)
        conn.rollback()


def _fixture_db(tmp_path):
    path = _sqlite_from_fixture(tmp_path / "v24.sqlite", fixture=V24_FIXTURE)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return path, conn


TABLES = ("patients", "history_entries", "events", "patient_diagnosis_history", "oncology_consilium_appointments",
          "consilium_revision_history", "consilium_resubmissions", "consilium_backdating_reasons",
          "consilium_schedule_slots", "users", "audit_events", "idempotency_keys", "import_files_processed")


def test_v24_fixture_and_migration_to_v25(client, tmp_path):
    from backend.app import config, db
    from backend.app.maintenance import identify_backup_build
    path, conn = _fixture_db(tmp_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 24
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 2
    assert identify_backup_build(path, 24) == "wp24b-5f2c9c8"
    before = {t: pre_v28_rows(conn, t) for t in TABLES}
    db.init_db(conn)
    # WP2.4-D: цепочка доводит v24 до текущей схемы (v26), а не только до v25.
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 28   # WP-C: v28 (история фактов, защита истории); combined-preaudit: v27 — мобильный доступ пациента
    for table in TABLES:
        assert pre_v28_rows(conn, table) == before[table], table
    assert conn.execute("SELECT COUNT(*) FROM patient_duplicate_confirmations").fetchone()[0] == 0
    sql_before = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    statements = []
    conn.set_trace_callback(statements.append)
    db.init_db(conn)
    conn.set_trace_callback(None)
    assert not [s for s in statements if "DROP TRIGGER" in s.upper() or "ALTER TABLE" in s.upper()]
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == sql_before
    conn.close()


def test_restore_of_a_v24_backup_migrates_to_v25(auth_client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp24b-v24.cbk", header=24, fixture=V24_FIXTURE)
    assert maintenance.validate_backup(backup)["source_build"] == "wp24b-5f2c9c8"
    with app_stopped():
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 28   # WP-C: v28 (история фактов, защита истории); combined-preaudit (v27); WP2.4-D: текущая схема
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM patient_duplicate_confirmations").fetchone()[0] == 0
    conn.close()


def test_backup_and_restore_of_v25_keep_confirmations(auth_client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    first = _create(auth_client, policy=X).json()["id"]
    assert _create(auth_client, policy=X, confirm=_confirm([first])).status_code == 201
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    current = maintenance.backup_database(config.DB_PATH, tmp_path / "v25.cbk")
    db.reset_singleton_for_tests()
    with app_stopped():
        maintenance.restore_database(current, config.DB_PATH, confirm_offline=True)
    assert maintenance.validate_backup(current)["source_build"] == "current"
    conn = sqlite3.connect(str(config.DB_PATH))
    assert conn.execute("SELECT COUNT(*) FROM patient_duplicate_confirmations").fetchone()[0] == 1
    conn.close()


def test_confirmed_operations_do_not_change_other_medical_history(client):
    from .test_wp23a_scheduling_integrity import DECISION, FUTURE, _book
    from .conftest import consilium_grid
    _world()
    consilium_grid((FUTURE,))
    login(client, "doc.a")
    first = _create(client, events=[{"date": "2026-11-20", "time": "10:00"}]).json()["id"]
    aid = _book(client, first, FUTURE, "10:00").json()["id"]
    login(client, "chair")
    assert client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf).status_code == 200
    assert client.post(f"/api/consilium/appointments/{aid}/sign", json=DECISION, headers=client.csrf).status_code == 200
    login(client, "doc.a")
    med = _med_snapshot()
    second = _create(client, confirm=_confirm([first])).json()["id"]
    other = _create(client, policy=Y).json()["id"]
    _patch_policy(client, other, X, confirm=_confirm(sorted([first, second])))
    _import(client, _file(_row("Синтетик Импорта", X)), key=_key(), confirm=_confirm(sorted([first, second, other])))
    after = _med_snapshot()
    old_entries = {r[0] for r in med["history_entries"]}
    assert [r for r in after["history_entries"] if r[0] in old_entries] == med["history_entries"]
    assert after["oncology_consilium_appointments"] == med["oncology_consilium_appointments"]
    old_events = {r[0] for r in med["events"]}
    assert [r for r in after["events"] if r[0] in old_events] == med["events"]


# ================================================================ 22. client

def test_static_client_contract():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="duplicateModal"' in html and 'id="duplicateReason"' in html and 'maxlength="2000"' in html
    asker = js[js.index("    askDuplicateConfirmation(data, { title, confirmLabel, reasonLabel }) {"):]
    asker = asker[:asker.index("\n    },\n")]
    assert "innerHTML" not in asker and "text:" in asker                 # данные карточек — только как текст
    add = js[js.index("    async addPatient(event) {"):]
    add = add[:add.index("\n    },\n")]
    assert "this.isPossibleDuplicate(e)" in add and "payload.duplicateConfirmation" in add
    # Тело изменилось (добавлено подтверждение) — новый ключ: проверка отправленного тела в цикле.
    assert add.index("const payloadText = JSON.stringify(payload);") < add.index("apiPostIdempotent('/api/patients'")
    update = js[js.index("    async updatePatientField(patientId, fieldName, value) {"):]
    assert "body.duplicateConfirmation" in update[:update.index("\n    },\n")]
    commit = js[js.index("    async confirmImportCommit() {"):]
    assert "body.duplicateConfirmation" in commit[:commit.index("\n    },\n")]


def _run_add_patient(responses, answers, presses=1):
    """Выполнить настоящий addPatient из app.js в Node с заглушками окружения.

    responses — ответы сервера по порядку: 'dup' (409 POSSIBLE_DUPLICATE),
    'network' (ответ потерян), 'ok' (201); answers — ответы окна подтверждения
    (строка причины или null); presses — сколько раз сотрудник нажимает «Создать»."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def method(name):
        start = js.index(f"    {name}(")
        return js[start:js.index("\n    },\n", start) + 7]

    methods = "\n".join(method(n) for n in ("async addPatient", "isPossibleDuplicate", "isDuplicateLimitExceeded", "openAddModal"))
    harness = r"""
const responses = RESPONSES, answers = ANSWERS, presses = PRESSES;
const sent = [], errors = [], asked = [], opened = [];
let keyCounter = 0;
const elements = {};
function $(id) {
  if (!elements[id]) elements[id] = { value: '', selectedOptions: [], reset() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
  return elements[id];
}
Object.assign($('fio'), { value: 'Синтетик Клиента' });
Object.assign($('stage'), { value: 'exam' });
Object.assign($('head'), { value: 'Заведуев' });
Object.assign($('diagnosis'), { value: 'C34.1' });
Object.assign($('policyNumber'), { value: '7700000000031001' });
const ui = { toast() {}, closeModal() {} };
function newIdempotencyKey() { keyCounter += 1; return 'key-' + keyCounter; }
async function apiPostIdempotent(path, body, key) {
  sent.push({ key, body: JSON.parse(JSON.stringify(body)) });
  const kind = responses.shift();
  if (kind === 'network') { const e = new Error('Нет ответа от сервера.'); e.network = true; throw e; }
  if (kind === 'limit') {
    const e = new Error('Совпадений слишком много'); e.status = 409;
    e.data = { code: 'DUPLICATE_MATCH_LIMIT_EXCEEDED', detail: 'Совпадений больше 50: нужен ручной разбор.' };
    throw e;
  }
  if (kind === 'dup' || kind === 'dup2') {
    const e = new Error('Возможный дубль'); e.status = 409;
    const matches = [{ id: 'p-1', name: 'Синтетик <b>Один</b>', age: 50, treatingDoctorName: 'Врач' }];
    if (kind === 'dup2') matches.push({ id: 'p-2', name: 'Синтетик Два', age: 50, treatingDoctorName: 'Врач' });
    e.data = { code: 'POSSIBLE_DUPLICATE', detail: 'совпадение', matches };
    throw e;
  }
  return { id: 'new-card', name: body.name };
}
const app = {
METHODS
  addPatientPending: false,
  setAddPatientPending(v) { this.addPatientPending = v; },
  showFormError(m) { errors.push(m); },
  clearPgiResultDateError() {}, resetDiagnosisSelect() {}, showPgiResultDateError() {},
  suggestedNosologyForDiagnosis() { return 'lung34'; },
  buildEventsFromForm() { return []; },
  replacePatientInCache() {}, loadActivity: async () => {}, fillStaticSelects() {},
  openPatient(id) { opened.push(id); },
  askDuplicateConfirmation: async (data) => { asked.push(data.matches.map(m => m.id)); const a = answers.shift(); return a ? { reason: a } : null; },
};
(async () => {
  try { app.openAddModal(); } catch (e) { /* часть формы — заглушки */ }
  for (let i = 0; i < presses; i += 1) await app.addPatient({ preventDefault() {} });
  process.stdout.write(JSON.stringify({ sent, errors, asked, opened }));
})();
"""
    script = (harness.replace("RESPONSES", json.dumps(responses)).replace("ANSWERS", json.dumps(answers))
              .replace("PRESSES", str(presses)).replace("METHODS", methods))
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_client_confirms_with_a_new_key_and_the_reason():
    result = _run_add_patient(["dup", "ok"], ["Причина клиента"])
    first, second = result["sent"]
    assert "duplicateConfirmation" not in first["body"]
    assert second["body"]["duplicateConfirmation"] == {"reason": "Причина клиента", "matchedPatientIds": ["p-1"]}
    assert first["key"] != second["key"]                     # изменилось тело — новый ключ
    assert result["opened"] == ["new-card"] and result["errors"] == []


def test_client_cancel_keeps_the_form_and_creates_nothing():
    result = _run_add_patient(["dup"], [None])
    assert len(result["sent"]) == 1 and result["opened"] == []
    assert result["errors"] and "не создана" in result["errors"][0]


def test_client_lost_response_after_confirmation_is_repeated_with_the_same_key_and_body():
    result = _run_add_patient(["dup", "network", "ok"], ["Причина клиента"], presses=2)
    first, lost, repeat = result["sent"]
    assert lost["key"] == repeat["key"] and lost["body"] == repeat["body"]   # сервер вернет replay 201
    assert repeat["body"]["duplicateConfirmation"]["matchedPatientIds"] == ["p-1"]
    assert result["asked"] == [["p-1"]] and result["opened"] == ["new-card"]


def test_client_stale_confirmation_asks_again_with_the_current_list():
    result = _run_add_patient(["dup", "dup2", "ok"], ["Первая причина", "Вторая причина"])
    assert result["asked"] == [["p-1"], ["p-1", "p-2"]]
    assert result["sent"][2]["body"]["duplicateConfirmation"] == {
        "reason": "Вторая причина", "matchedPatientIds": ["p-1", "p-2"]}
    assert len({s["key"] for s in result["sent"]}) == 3


def test_server_replays_the_confirmed_request_after_a_lost_response(client):
    """Сервер для клиентского сценария выше: тот же ключ и тело — та же карточка."""
    _world()
    login(client, "doc.a")
    first = _create(client).json()["id"]
    key = _key()
    created = _create(client, key=key, confirm=_confirm([first]))
    replay = _create(client, key=key, confirm=_confirm([first]))
    assert created.status_code == replay.status_code == 201 and replay.headers[REPLAYED] == "true"
    assert len(_confirmations()) == 1 and len(_patients()) == 2


# ================================================================ L2–L4, L9, 23

def test_l2_creation_paths_use_only_the_scoped_search():
    """L2: создание, правка полиса и импорт ищут совпадения только через
    identity.policy_matches*, где условие области стоит в том же запросе; других
    обращений к полису других карточек и записи в какой-либо реестр нет."""
    app = PROJECT_ROOT / "backend" / "app"
    allowed = {"SELECT policy_number, version FROM patients WHERE id = ?",   # своя карточка (правка полиса)
               # WP-C: правка полиса — с проверкой версии своей же карточки.
               "UPDATE patients SET policy_number = ?, updated_at = ?, version = version + 1 WHERE id = ? AND version = ?"}
    for path in ("services/patient_create.py", "services/patient_duplicates.py", "services/patient_policy.py",
                 "services/import_operations.py", "routes/patients.py"):
        source = (app / path).read_text(encoding="utf-8")
        statements = re.findall(r'"((?:SELECT|UPDATE|INSERT|DELETE)[^"]*policy_number[^"]*)"', source)
        assert set(statements) <= allowed, (path, statements)
        assert "registry" not in source.lower() and "candidates" not in source.lower(), path
    importexport = (app / "importexport.py").read_text(encoding="utf-8")
    block = importexport[importexport.index("def possible_duplicate_rows"):importexport.index("def possible_duplicates_impact")]
    assert "identity.policy_matches_many(conn, actor," in block and "FROM patients" not in block
    identity_source = (app / "identity.py").read_text(encoding="utf-8")
    queries = identity_source.split('"SELECT patients.id')[1:]
    assert len(queries) == 2
    for query in queries:
        assert "WHERE ({cond})" in query[:800]


def test_l3_no_unique_constraint_on_policy_numbers(client):
    conn = get_conn()
    for (sql,) in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall():
        lowered = " ".join(sql.lower().split())
        assert not ("unique" in lowered and "policy_number" in lowered), sql
    for index in conn.execute("PRAGMA index_list('patients')").fetchall():
        columns = [r[2] for r in conn.execute(f"PRAGMA index_info('{index['name']}')")]  # nosec - from sqlite
        assert not (index["unique"] and "policy_number" in columns), index["name"]


def test_l4_keys_are_scoped_to_the_actor(client):
    """L4: ключ сотрудника A у сотрудника B — независимая операция B со своей областью."""
    _world()
    tab_a, tab_b = _client_for(client, "doc.a"), _client_for(client, "doc.b")
    _create(tab_a, name="Синтетик A")
    key = _key()
    refused = _create(tab_a, key=key, name="Синтетик A2")
    assert refused.status_code == 409
    other = _create(tab_b, key=key, name="Синтетик A2")          # тот же ключ и тело, другая область
    assert other.status_code == 201 and REPLAYED not in other.headers
    assert "Синтетик A" not in json.dumps(other.json()["history"], ensure_ascii=False)
    rows = _rows("SELECT state FROM idempotency_keys ORDER BY created_at")
    assert sorted(r["state"] for r in rows) == ["completed", "refused"]


def test_l9_series_of_probes_and_the_rate_limit_are_identical(client, monkeypatch):
    from backend.app import config
    _world()
    login(client, "doc.b")
    foreign = [f"77000000000380{i:02d}" for i in range(6)]
    for i, policy in enumerate(foreign):
        _create(client, policy=policy, name=f"Синтетик Чужой {i}")
    monkeypatch.setattr(config, "PATIENT_CREATE_RATE_LIMIT", 4)

    def series(user, policies):
        tab = _client_for(client, user)
        return [_shape(r) if r.status_code != 429 else (429, r.json(), sorted(k.lower() for k in r.headers
                                                                             if k.lower() != "date"))
                for r in (_create(tab, policy=p, name="Синтетик Пробы", key=_key()) for p in policies)]

    probe_foreign = series("doc.a", foreign)                                      # совпадения только вне области
    probe_none = series("doc.a2", [f"77000000000390{i:02d}" for i in range(6)])   # совпадений нет вовсе
    assert [s[0] for s in probe_foreign] == [s[0] for s in probe_none] == [201, 201, 201, 201, 429, 429]

    def without_author(value):   # врачи двух серий разные — их поля различаются законно
        if isinstance(value, dict):
            return {k: without_author(v) for k, v in value.items() if "doctor" not in k.lower() and "byuser" not in k.lower()}
        if isinstance(value, (list, tuple)):
            return [without_author(v) for v in value]
        return value
    assert without_author(probe_foreign) == without_author(probe_none)


def test_unexpected_sql_error_on_create_is_500_without_details(client, monkeypatch):
    """Исправленный латентный дефект WP2.4-A: _database_busy перекрывалась, и любая
    OperationalError при создании становилась 503 с текстом исключения."""
    import sqlite3 as sql
    from backend.app.services import patient_create
    _world()
    login(client, "doc.a")

    def broken(*args, **kwargs):
        raise sql.OperationalError("synthetic: no such column secret_column")

    monkeypatch.setattr(patient_create, "create_patient_card", broken)
    response = _create(client)
    assert response.status_code == 500 and "secret_column" not in response.text

    def busy(*args, **kwargs):
        error = sql.OperationalError("database is locked")
        error.sqlite_errorcode = sql.SQLITE_BUSY
        raise error

    monkeypatch.setattr(patient_create, "create_patient_card", busy)
    response = _create(client)
    assert response.status_code == 503 and "повторите попытку" in response.json()["detail"]
    assert "locked" not in response.text


def test_markup_in_matches_is_data_and_csp_is_unchanged(client):
    _world()
    login(client, "doc.a")
    _create(client, name='<img src=x onerror="alert(1)">')
    response = _create(client)
    assert response.status_code == 409 and response.headers["content-type"].startswith("application/json")
    assert response.json()["matches"][0]["name"] == '<img src=x onerror="alert(1)">'
    page = client.get("/")
    assert "script-src 'self'" in page.headers.get("content-security-policy", "")
    assert "'unsafe-inline'" not in page.headers["content-security-policy"].split("script-src", 1)[1].split(";")[0]


# ================================================================ C-01. more than MATCH_LIMIT visible matches

LIMIT_CODE = "DUPLICATE_MATCH_LIMIT_EXCEEDED"


def _seed_cards(owner_id, count, policy=X, prefix="Синтетик Лимита"):
    """count карточек врача owner_id с одним полисом — напрямую через store (стартовые условия)."""
    from backend.app import store
    from backend.app.models import PatientCreateIn
    conn = get_conn()
    ids = [store.create_patient(conn, PatientCreateIn(**make_patient_payload(
        name=f"{prefix} {i:03d}", policyNumber=policy, treatingDoctorUserId=owner_id)), owner_id)
        for i in range(count)]
    conn.commit()
    return ids


def _first_50(policy=X):
    rows = _rows("SELECT id FROM patients WHERE policy_number = ? ORDER BY created_at, id LIMIT 50", policy)
    return [r["id"] for r in rows]


def _no_false_confirmation():
    assert _confirmations() == [] and _audit("patient_duplicate_confirmed") == []


@pytest.mark.parametrize("count", [49, 50])
def test_up_to_the_limit_a_full_confirmation_works(client, count):
    ids = _world()
    _seed_cards(ids["doc.a"], count)
    login(client, "doc.a")
    warned = _create(client, name="Синтетик Новый")
    assert warned.status_code == 409 and warned.json()["code"] == "POSSIBLE_DUPLICATE"
    shown = [m["id"] for m in warned.json()["matches"]]
    assert len(shown) == count
    created = _create(client, name="Синтетик Новый", confirm=_confirm(shown), key=_key())
    assert created.status_code == 201, created.text
    assert len(json.loads(_confirmations()[0]["matched_patient_ids"])) == count


@pytest.mark.parametrize("count", [51, 52])
@pytest.mark.parametrize("with_key", [False, True])
def test_over_the_limit_creation_is_refused_even_with_the_first_50_confirmed(client, count, with_key):
    ids = _world()
    _seed_cards(ids["doc.a"], count)
    login(client, "doc.a")
    before = _patients()
    plain = _create(client, name="Синтетик Новый", key=_key() if with_key else None)
    assert plain.status_code == 409 and plain.json()["code"] == LIMIT_CODE, plain.text
    assert "matches" not in plain.json() and "Синтетик Лимита" not in plain.text      # подтверждать нечего
    key = _key() if with_key else None
    confirmed = _create(client, name="Синтетик Новый", confirm=_confirm(_first_50()), key=key)
    assert confirmed.status_code == 409 and confirmed.json()["code"] == LIMIT_CODE
    if with_key:                                                     # повтор того же ключа и тела
        again = _create(client, name="Синтетик Новый", confirm=_confirm(_first_50()), key=key)
        assert again.status_code == 409 and again.json()["code"] == LIMIT_CODE and REPLAYED not in again.headers
        assert {r["state"] for r in _rows("SELECT state FROM idempotency_keys")} == {"refused"}
    assert _patients() == before
    _no_false_confirmation()


@pytest.mark.parametrize("count", [51, 52])
def test_over_the_limit_policy_edit_is_refused(client, count):
    ids = _world()
    _seed_cards(ids["doc.a"], count)
    login(client, "doc.a")
    target = _create(client, policy=Y, name="Синтетик Правки").json()["id"]
    plain = _patch_policy(client, target, X)
    confirmed = _patch_policy(client, target, X, confirm=_confirm(_first_50()))
    for r in (plain, confirmed):
        assert r.status_code == 409 and r.json()["code"] == LIMIT_CODE and "matches" not in r.json()
    assert _rows("SELECT policy_number FROM patients WHERE id = ?", target)[0]["policy_number"] == Y
    _no_false_confirmation()


@pytest.mark.parametrize("count", [51, 52])
def test_over_the_limit_import_creates_nothing(client, count):
    ids = _world()
    _seed_cards(ids["doc.a"], count)
    login(client, "doc.a")
    raw = _file(_row("Синтетик Без Совпадения", Y), _row("Синтетик Импорта", X))
    preview = client.post("/api/import/preview", json={"raw": raw}).json()["possibleDuplicates"]["add"]
    assert preview["limitExceeded"] is True and preview["limitExceededRows"] == [2]
    assert preview["rows"] == [] and preview["matchedPatientIds"] == []
    before = _patients()
    for confirm in (None, _confirm(_first_50())):
        key = _key()
        r = _import(client, raw, key=key, confirm=confirm)
        assert r.status_code == 409 and r.json()["code"] == LIMIT_CODE, r.text
        assert r.json()["rows"] == [2] and "matches" not in r.text and "Синтетик Лимита" not in r.text
        again = _import(client, raw, key=key, confirm=confirm)
        assert again.status_code == 409 and again.json()["code"] == LIMIT_CODE
    assert _patients() == before and _rows("SELECT * FROM import_files_processed") == []
    _no_false_confirmation()
    assert not [a for a in _audit("import_committed") if a["result"] == "ok"]


def test_over_the_limit_import_keeps_u4_first(client):
    ids = _world()
    login(client, "doc.a")
    raw = _file(_row("Синтетик Повтора", X))
    assert _import(client, raw, key=_key()).status_code == 200
    _seed_cards(ids["doc.a"], 51)
    response = _import(client, raw, key=_key())
    assert response.status_code == 428 and response.json()["code"] == "FILE_ALREADY_IMPORTED"
    response = _import(client, raw, key=_key(), repeat=True)
    assert response.status_code == 409 and response.json()["code"] == LIMIT_CODE


def test_foreign_matches_do_not_count_toward_the_limit(client):
    ids = _world()
    _seed_cards(ids["doc.b"], 51, prefix="Синтетик Чужой")
    _seed_cards(ids["doc.a"], 50)
    login(client, "doc.a")
    warned = _create(client, name="Синтетик Новый")
    assert warned.json()["code"] == "POSSIBLE_DUPLICATE" and len(warned.json()["matches"]) == 50
    assert "Синтетик Чужой" not in warned.text
    ids_shown = [m["id"] for m in warned.json()["matches"]]
    assert _create(client, name="Синтетик Новый", confirm=_confirm(ids_shown)).status_code == 201
    login(client, "doc.a2")                                         # своих совпадений нет, чужих 51+
    assert _create(client, name="Синтетик Коллеги").status_code == 201


def test_over_the_limit_rights_and_scope_are_rechecked_under_the_lock(client):
    ids = _world()
    seeded = _seed_cards(ids["doc.a"], 51)
    tab = _client_for(client, "doc.a")
    remaining = seeded[1:]                                          # 50 карточек без уходящей
    # Одна карточка уходит из области во время ожидания: остается ровно 50, полный список подтвержден.
    response, waited = _while_locked(lambda: _create(tab, name="Синтетик Новый", confirm=_confirm(remaining)),
                                     "UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?",
                                     (ids["doc.b"], seeded[0]))
    assert waited and response.status_code == 201, response.text
    # Права отозваны во время ожидания — 403, а не сведения о лимите.
    _seed_cards(ids["doc.a"], 2, prefix="Синтетик Добавки")
    response, waited = _while_locked(lambda: _create(tab, name="Синтетик Еще"),
                                     "UPDATE users SET role_id = 'consilium_chair' WHERE id = ?", (ids["doc.a"],))
    assert waited and response.status_code == 403 and LIMIT_CODE not in response.text


def test_over_the_limit_leaves_no_patient_data_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    ids = _world()
    _seed_cards(ids["doc.a"], 51)
    login(client, "doc.a")
    _create(client, name="Синтетик Журнала", confirm=_confirm(_first_50()))
    _import(client, _file(_row("Синтетик Журнала Импорта", X)), key=_key())
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False) + caplog.text
    for value in ("Синтетическая причина", "Синтетик Лимита", "Синтетик Журнала", X):
        assert value not in dump, value


def test_client_does_not_offer_to_confirm_an_incomplete_list():
    result = _run_add_patient(["limit"], ["Причина клиента"])
    assert result["asked"] == [] and len(result["sent"]) == 1
    assert result["errors"] and "ручн" in result["errors"][0]
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    impact = js[js.index("    renderImportConsiliumImpact(mode) {"):]
    impact = impact[:impact.index("\n    },\n")]
    assert "limitExceeded" in impact
    commit = js[js.index("    async confirmImportCommit() {"):]
    commit = commit[:commit.index("\n    },\n")]
    assert "limitExceeded" in commit


def test_import_with_more_than_50_matches_in_total_is_blocked(client):
    """C-01: три записи по 20 совпадений — 60 карточек; подтверждение принимает не более 50 ID,
    поэтому неполный набор не предлагается и импорт не выполняется."""
    ids = _world()
    policies = ["7700000000040001", "7700000000040002", "7700000000040003"]
    for i, policy in enumerate(policies):
        _seed_cards(ids["doc.a"], 20, policy=policy, prefix=f"Синтетик Группы {i}")
    login(client, "doc.a")
    raw = _file(*[_row(f"Синтетик Записи {i}", p) for i, p in enumerate(policies)])
    preview = client.post("/api/import/preview", json={"raw": raw}).json()["possibleDuplicates"]["add"]
    assert preview["limitExceeded"] is True and preview["limitExceededRows"] == [1, 2, 3]
    before = _patients()
    response = _import(client, raw, key=_key())
    assert response.status_code == 409 and response.json()["code"] == LIMIT_CODE and response.json()["rows"] == [1, 2, 3]
    assert _patients() == before
    _no_false_confirmation()


def test_preview_limit_flag_is_false_without_matches_and_for_update(client):
    _world()
    login(client, "doc.a")
    preview = client.post("/api/import/preview", json={"raw": _file(_row("Синтетик", Y))}).json()["possibleDuplicates"]
    assert preview["add"]["limitExceeded"] is False and "update" not in preview
