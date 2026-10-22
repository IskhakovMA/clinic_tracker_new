"""WP2.4-D: global registry of possible duplicates, clinical review, erroneous marks (schema v26).

Scan (developer) → candidates → technical review (developer) → clinical decision
(deputy chief of oncology) → mark one card erroneous only without clinical
dependencies (U10) → unmark with a reason. Nothing is merged, deleted or moved;
an erroneous card drops out of active work through the single scope condition
and is read-only at database level. Synthetic data only.
"""
import json
import logging
import sqlite3
import time
import uuid

import pytest

from .conftest import DEV_USERNAME, PROJECT_ROOT, get_conn, login, make_patient_payload
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _parallel, _rows
from .test_wp24a_patient_create import _while_locked, _world

V25_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp24d_v25_synthetic.sql"
X = "7700000000051001"
Y = "7700000000051002"
REASON = "Синтетическая причина решения <b>разбора</b>"
DISCLAIMER = "Совпадение данных не доказывает, что это один пациент."
MEDICAL = ("history_entries", "events", "patient_diagnosis_history", "future_plans", "patient_attention_issues",
           "patient_notification_preferences", "notifications", "oncology_consilium_appointments",
           "consilium_revision_history", "consilium_resubmissions", "consilium_backdating_reasons")
ROLES_WITHOUT_REGISTRY = ("doc.a", "doc.b", "head.1", "chair", "caop")


# ---------------------------------------------------------------- helpers

def _card(owner_id, *, policy=None, name="Синтетик Дублев", events=(), **kw):
    """Карточка напрямую через store — стартовые условия теста."""
    from backend.app import store
    from backend.app.models import EventIn, PatientCreateIn
    kw.setdefault("phone", "")                 # без общего синтетического телефона: свой признак задает тест
    payload = make_patient_payload(name=name, treatingDoctorUserId=owner_id, events=[], **kw)
    if policy is not None:
        payload["policyNumber"] = policy
    data = PatientCreateIn(**payload)
    data.events = [EventIn(**e) for e in events]
    pid = store.create_patient(get_conn(), data, owner_id)
    get_conn().commit()
    return pid


def _scan(tab):
    response = tab.post("/api/duplicates/scans", headers=tab.csrf)
    assert response.status_code == 200, response.text
    return response.json()


def _candidate_for(a, b):
    first, second = sorted((a, b))
    rows = _rows("SELECT * FROM patient_duplicate_candidates WHERE patient_a_id = ? AND patient_b_id = ?", first, second)
    return rows[0] if rows else None


def _detail(tab, cid):
    return tab.get(f"/api/duplicates/candidates/{cid}")


def _mark(tab, cid, pid, reason=REASON, version=None):
    if version is None:
        version = _rows("SELECT version FROM patient_duplicate_candidates WHERE id = ?", cid)[0]["version"]
    return tab.post(f"/api/duplicates/candidates/{cid}/mark", headers=tab.csrf,
                    json={"patientId": pid, "reason": reason, "expectedVersion": version})


def _unmark(tab, pid, mark_id, reason=REASON):
    return tab.post(f"/api/duplicates/erroneous/{pid}/unmark", headers=tab.csrf,
                    json={"markId": mark_id, "reason": reason})


def _decide(tab, cid, decision, reason=REASON, version=None):
    if version is None:
        version = _rows("SELECT version FROM patient_duplicate_candidates WHERE id = ?", cid)[0]["version"]
    return tab.post(f"/api/duplicates/candidates/{cid}/decision", headers=tab.csrf,
                    json={"decision": decision, "reason": reason, "expectedVersion": version})


def _technical(tab, cid, action, comment=None, version=None):
    body = {"action": action, "comment": comment}
    if version is not None:
        body["expectedVersion"] = version
    return tab.post(f"/api/duplicates/candidates/{cid}/technical-review", headers=tab.csrf, json=body)


def _state(cid):
    return _rows("SELECT state FROM patient_duplicate_candidates WHERE id = ?", cid)[0]["state"]


def _erroneous():
    return {r["patient_id"] for r in _rows("SELECT patient_id FROM patient_erroneous_state")}


def _audit(event_type):
    return _rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", event_type)


def _restore_audit():
    get_conn().execute("DROP TRIGGER IF EXISTS wp23a_audit_down")
    get_conn().commit()


def _med_digest(*pids):
    """Медицинские записи карточек — для проверки, что ничего не изменено и не перенесено."""
    conn = get_conn()
    out = {"patients": [tuple(r) for r in conn.execute(
        f"SELECT * FROM patients WHERE id IN ({','.join('?' * len(pids))}) ORDER BY id", pids)]}  # nosec - test
    for table in MEDICAL:
        if table == "events":
            sql = ("SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id "
                   f"WHERE h.patient_id IN ({','.join('?' * len(pids))}) ORDER BY e.id")
        elif table in ("consilium_revision_history", "consilium_resubmissions", "consilium_backdating_reasons"):
            sql = f"SELECT * FROM {table} ORDER BY rowid"  # nosec - test constant
            out[table] = [tuple(r) for r in conn.execute(sql)]
            continue
        else:
            sql = f"SELECT * FROM {table} WHERE patient_id IN ({','.join('?' * len(pids))}) ORDER BY rowid"  # nosec
        out[table] = [tuple(r) for r in conn.execute(sql, pids)]
    return out


def _pair(ids, *, policy=X, other_owner="doc.b"):
    """Две карточки с одним полисом в разных отделениях."""
    a = _card(ids["doc.a"], policy=policy, name="Синтетик Первый")
    b = _card(ids[other_owner], policy=policy.replace("0000", "0000 "), name="Синтетик Второй",
              events=[{"date": "2026-11-20", "time": "10:00"}])          # плановое событие не мешает пометке
    return a, b


def _setup(client, *, deps=None):
    """Мир, пара с одним полисом, сканирование. Возвращает (ids, a, b, cid, dev, deputy)."""
    ids = _world()
    a, b = _pair(ids)
    if deps:
        deps(ids, b)
    dev = _client_for(client, DEV_USERNAME)
    deputy = _client_for(client, "deputy")
    _scan(dev)
    cid = _candidate_for(a, b)["id"]
    return ids, a, b, cid, dev, deputy


# ================================================================ 1–2. global and cross-department pairs

def test_scan_finds_global_policy_pairs_across_departments(client):
    ids = _world()
    a, b = _pair(ids)
    lonely = _card(ids["doc.a"], policy=Y, name="Синтетик Одиночка")
    login(client, "doc.a")
    assert client.get(f"/api/patients/{b}").status_code == 404          # врач отделения 1 вторую карточку не видит
    dev = _client_for(client, DEV_USERNAME)
    result = _scan(dev)
    assert result["status"] == "completed" and result["pairsNew"] == 1 and result["patientsScanned"] == 3
    row = _candidate_for(a, b)
    assert json.loads(row["reason_codes"])[0] == "policy" and row["strength"] == "strong" and row["state"] == "open"
    assert row["source"] == "scan" and row["patient_a_id"] < row["patient_b_id"]
    assert all(lonely not in (r["patient_a_id"], r["patient_b_id"])
               for r in _rows("SELECT * FROM patient_duplicate_candidates"))
    scan = _rows("SELECT * FROM patient_duplicate_scans")[0]
    assert scan["status"] == "completed" and scan["pairs_found"] == 1
    assert X not in json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False)


def test_scan_signals_lookalike_names_and_weak_signals(client):
    ids = _world()
    p1 = _card(ids["doc.a"], policy=X, name="Сидоров Пётр", age=44, phone="")
    p2 = _card(ids["doc.b"], policy=Y, name="СИДОРOВ  Петр", age=44, phone="")      # латинская «O»
    p3 = _card(ids["doc.a"], policy="7700000000051003", name="Синтетик Телефона", phone="+7 (999) 111-22-33")
    p4 = _card(ids["doc.b"], policy="7700000000051004", name="Другой Человек", phone="8 999 111 22 33")
    _scan(_client_for(client, DEV_USERNAME))
    named = _candidate_for(p1, p2)
    assert "name_age" in json.loads(named["reason_codes"]) and named["strength"] == "medium"
    phone = _candidate_for(p3, p4)
    assert json.loads(phone["reason_codes"]) == ["phone"] and phone["strength"] == "weak"


# ================================================================ 3. repeated scan

def test_repeated_scan_keeps_pairs_unique_and_decisions(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    second = _scan(dev)
    assert second["pairsNew"] == 0 and len(_rows("SELECT * FROM patient_duplicate_candidates")) == 1
    assert _decide(deputy, cid, "not_duplicate").status_code == 200
    _scan(dev)
    assert _state(cid) == "not_duplicate"                               # решение не сбрасывается
    # Признаки изменились: решение остается, требуется повторная оценка.
    get_conn().execute("UPDATE patients SET name = 'Синтетик Первый', age = 50 WHERE id = ?", (b,))
    get_conn().execute("UPDATE patients SET name = 'Синтетик Первый', age = 50 WHERE id = ?", (a,))
    get_conn().commit()
    third = _scan(dev)
    assert third["pairsUpdated"] == 1
    row = _candidate_for(a, b)
    assert row["state"] == "not_duplicate" and row["signals_changed_at"] and "name_age" in json.loads(row["reason_codes"])
    assert len(_rows("SELECT * FROM patient_duplicate_candidates")) == 1


# ================================================================ 4–6. concurrency, failures, data changes

def test_concurrent_scans_run_one_at_a_time(client, monkeypatch):
    from backend.app.services import duplicate_review
    ids = _world()
    _pair(ids)
    original = duplicate_review._compute_pairs

    def slow(rows):
        time.sleep(0.8)
        return original(rows)

    monkeypatch.setattr(duplicate_review, "_compute_pairs", slow)
    tabs = [_client_for(client, DEV_USERNAME), _client_for(client, DEV_USERNAME)]
    res = _parallel(lambda: tabs[0].post("/api/duplicates/scans", headers=tabs[0].csrf),
                    lambda: tabs[1].post("/api/duplicates/scans", headers=tabs[1].csrf))
    codes = sorted(r.status_code for r, *_ in res)
    assert codes == [200, 409], [r.text for r, *_ in res]
    assert [r for r, *_ in res if r.status_code == 409][0].json()["code"] == "SCAN_ALREADY_RUNNING"
    assert len(_rows("SELECT * FROM patient_duplicate_candidates")) == 1
    assert [s["status"] for s in _rows("SELECT status FROM patient_duplicate_scans")] == ["completed"]


def test_failed_scan_publishes_nothing_and_next_scan_recovers(client, monkeypatch):
    from backend.app.services import duplicate_review
    ids = _world()
    a, b = _pair(ids)
    dev = _client_for(client, DEV_USERNAME)

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure during publication")

    original = duplicate_review._publish
    monkeypatch.setattr(duplicate_review, "_publish", broken)
    assert dev.post("/api/duplicates/scans", headers=dev.csrf).status_code == 500
    assert _rows("SELECT * FROM patient_duplicate_candidates") == []
    failed = _rows("SELECT * FROM patient_duplicate_scans")[0]
    assert failed["status"] == "failed" and failed["error_code"] == "internal_error" and failed["finished_at"]
    monkeypatch.setattr(duplicate_review, "_publish", original)
    # Сбой процесса: запуск остался running; по истечении срока он прерывается новым запуском.
    stale = uuid.uuid4().hex
    get_conn().execute("INSERT INTO patient_duplicate_scans (id, status, started_by_user_id, started_at) "
                       "VALUES (?, 'running', ?, '2000-01-01T00:00:00+00:00')", (stale, ids["dev"]))
    get_conn().commit()
    assert _scan(dev)["status"] == "completed"
    assert _rows("SELECT status, error_code FROM patient_duplicate_scans WHERE id = ?", stale) == [
        {"status": "interrupted", "error_code": "stale_running_scan"}]
    assert _candidate_for(a, b) is not None
    assert len(_audit("duplicate_scan_interrupted")) == 1 and len(_audit("duplicate_scan_failed")) == 1


def test_fresh_running_scan_blocks_a_new_one(client):
    ids = _world()
    get_conn().execute("INSERT INTO patient_duplicate_scans (id, status, started_by_user_id, started_at) "
                       "VALUES (?, 'running', ?, ?)", (uuid.uuid4().hex, ids["dev"],
                                                        time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())))
    get_conn().commit()
    dev = _client_for(client, DEV_USERNAME)
    response = dev.post("/api/duplicates/scans", headers=dev.csrf)
    assert response.status_code == 409 and response.json()["code"] == "SCAN_ALREADY_RUNNING"


def test_scan_limits_fail_without_publishing(client, monkeypatch):
    from backend.app.services import duplicate_review
    ids = _world()
    _pair(ids)
    dev = _client_for(client, DEV_USERNAME)
    monkeypatch.setattr(duplicate_review, "SCAN_MAX_PAIRS", 0)
    response = dev.post("/api/duplicates/scans", headers=dev.csrf)
    assert response.status_code == 409 and response.json()["code"] == "SCAN_LIMIT_EXCEEDED"
    assert response.json()["reason"] == "too_many_pairs"
    assert _rows("SELECT * FROM patient_duplicate_candidates") == []
    assert _rows("SELECT status FROM patient_duplicate_scans") == [{"status": "failed"}]
    monkeypatch.setattr(duplicate_review, "SCAN_MAX_PAIRS", 50000)
    monkeypatch.setattr(duplicate_review, "SCAN_MAX_PATIENTS", 1)
    response = dev.post("/api/duplicates/scans", headers=dev.csrf)
    assert response.status_code == 409 and response.json()["reason"] == "too_many_patients"


def test_oversized_groups_are_reported_not_expanded(client, monkeypatch):
    from backend.app.services import duplicate_review
    ids = _world()
    for i in range(4):
        _card(ids["doc.a"], policy=X, name=f"Синтетик Группы {i}")
    monkeypatch.setattr(duplicate_review, "SCAN_GROUP_LIMIT", 3)
    result = _scan(_client_for(client, DEV_USERNAME))
    assert result["report"]["oversized_groups_by_reason"]["policy"] == 1
    assert all("policy" not in json.loads(r["reason_codes"])
               for r in _rows("SELECT reason_codes FROM patient_duplicate_candidates"))


def test_data_changed_during_scan_is_shown_as_outdated(client, monkeypatch):
    from backend.app.services import duplicate_review
    ids = _world()
    a, b = _pair(ids)
    original = duplicate_review._compute_pairs
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]

    def change_then_compute(rows):
        other = sqlite3.connect(path, timeout=10)
        other.execute("UPDATE patients SET policy_number = ? WHERE id = ?", (Y, b))
        other.commit()
        other.close()
        return original(rows)

    monkeypatch.setattr(duplicate_review, "_compute_pairs", change_then_compute)
    _scan(_client_for(client, DEV_USERNAME))
    cid = _candidate_for(a, b)["id"]                                   # опубликован снимок на начало
    detail = _detail(_client_for(client, "deputy"), cid).json()
    assert detail["reasonCodes"] == ["policy"] and detail["currentReasonCodes"] == []
    assert detail["signalsOutdated"] is True and detail["disclaimer"] == DISCLAIMER


# ================================================================ 7–8. state model

def test_state_model_and_review_history(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    assert _technical(dev, cid, "start").json()["state"] == "under_review"
    assert _technical(dev, cid, "note", "Синтетический технический комментарий").json()["state"] == "under_review"
    assert _decide(deputy, cid, "needs_clinical_review").json()["state"] == "needs_clinical_review"
    assert _decide(deputy, cid, "not_duplicate").json()["state"] == "not_duplicate"
    assert _decide(deputy, cid, "reopen").json()["state"] == "open"
    history = [(r["action"], r["state"]) for r in _rows(
        "SELECT action, state FROM patient_duplicate_reviews WHERE candidate_id = ? ORDER BY seq", cid)]
    assert history == [("technical_review_started", "under_review"), ("technical_note", "under_review"),
                       ("needs_clinical_review", "needs_clinical_review"), ("not_duplicate", "not_duplicate"),
                       ("reopened", "open")]
    detail = _detail(deputy, cid).json()
    assert [r["action"] for r in detail["reviews"]] == [h[0] for h in history]


@pytest.mark.parametrize("prepare,act,code", [
    (None, lambda dev, dep, cid: _technical(dev, cid, "note", ""), 422),                 # комментарий обязателен
    ("under_review", lambda dev, dep, cid: _technical(dev, cid, "start"), 409),
    (None, lambda dev, dep, cid: _decide(dep, cid, "reopen"), 409),
    ("not_duplicate", lambda dev, dep, cid: _decide(dep, cid, "needs_clinical_review"), 409),
    (None, lambda dev, dep, cid: _decide(dep, cid, "not_duplicate", reason="   "), 422),
    (None, lambda dev, dep, cid: _decide(dep, cid, "not_duplicate", version=999), 409),
])
def test_invalid_transitions(client, prepare, act, code):
    ids, a, b, cid, dev, deputy = _setup(client)
    if prepare == "under_review":
        _technical(dev, cid, "start")
    elif prepare == "not_duplicate":
        _decide(deputy, cid, "not_duplicate")
    before = _rows("SELECT * FROM patient_duplicate_reviews")
    assert act(dev, deputy, cid).status_code == code
    assert _rows("SELECT * FROM patient_duplicate_reviews") == before


def test_mark_needs_an_open_or_technical_state(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    _decide(deputy, cid, "not_duplicate")
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "TRANSITION_NOT_ALLOWED"
    assert _erroneous() == set()


def test_database_guards_state_and_journals(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    conn = get_conn()
    bad = [
        ("UPDATE patient_duplicate_candidates SET state = 'resolved' WHERE id = ?", (cid,), "state_from_reviews_only"),
        ("DELETE FROM patient_duplicate_candidates WHERE id = ?", (cid,), "duplicate_candidate_immutable"),
        ("UPDATE patient_duplicate_candidates SET patient_a_id = 'x' WHERE id = ?", (cid,), "duplicate_candidate_immutable"),
        ("INSERT INTO patient_duplicate_reviews (id, candidate_id, action, state, reason, reviewed_by_user_id, reviewed_at) "
         "VALUES ('r1', ?, 'marked_erroneous', 'resolved', 'x', ?, 'now')", (cid, ids["deputy"]), "transition_not_allowed"),
        ("INSERT INTO patient_duplicate_reviews (id, candidate_id, action, state, reason, reviewed_by_user_id, reviewed_at) "
         "VALUES ('r2', ?, 'reopened', 'open', 'x', ?, 'now')", (cid, ids["deputy"]), "transition_not_allowed"),
        ("INSERT INTO patient_erroneous_state (patient_id, mark_id, marked_at) VALUES (?, 'm', 'now')", (b,),
         "erroneous_state_from_journal_only"),
        ("INSERT INTO patient_erroneous_marks (id, patient_id, candidate_id, action, reverts_mark_id, reason, by_user_id, at) "
         "VALUES ('u1', ?, ?, 'unmark', 'none', 'x', ?, 'now')", (b, cid, ids["deputy"]), "patient_not_erroneous"),
        ("UPDATE patient_duplicate_scans SET status = 'failed'", (), "duplicate_scan_immutable"),
        ("DELETE FROM patient_duplicate_scans", (), "duplicate_scan_immutable"),
    ]
    for sql, params, message in bad:
        try:
            conn.execute(sql, params)
        except sqlite3.IntegrityError as e:
            assert message in str(e), (sql, str(e))
        else:
            raise AssertionError(f"not rejected: {sql}")
        finally:
            conn.rollback()


# ================================================================ 9–12. RBAC matrix, IDOR

def _registry_calls(cid, b, conf_id="c" * 32, mark_cid=None):
    return {
        "list": lambda t: t.get("/api/duplicates/candidates"),
        "scans": lambda t: t.get("/api/duplicates/scans"),
        "detail": lambda t: t.get(f"/api/duplicates/candidates/{cid}"),
        "erroneous": lambda t: t.get("/api/duplicates/erroneous"),
        "scan": lambda t: t.post("/api/duplicates/scans", headers=t.csrf),
        "technical": lambda t: _technical(t, cid, "note", "Синтетический комментарий"),
        "decision": lambda t: _decide(t, cid, "needs_clinical_review"),
        "mark": lambda t: _mark(t, mark_cid or cid, b),
        "unmark": lambda t: _unmark(t, b, "m" * 32),
        "reason": lambda t: t.post(f"/api/duplicates/candidates/{cid}/confirmations/{conf_id}/reason", headers=t.csrf),
    }


EXPECTED = {
    "developer": {"list": 200, "scans": 200, "detail": 200, "erroneous": 200, "scan": 200, "technical": 200,
                  "decision": 403, "mark": 403, "unmark": 403, "reason": 403},
    "deputy": {"list": 200, "scans": 200, "detail": 200, "erroneous": 200, "scan": 403, "technical": 403,
               "decision": 200, "mark": 200, "unmark": 409, "reason": 404},
}


@pytest.mark.parametrize("username", [DEV_USERNAME, "deputy", *ROLES_WITHOUT_REGISTRY])
def test_role_matrix(client, username):
    ids, a, b, cid, dev, deputy = _setup(client)
    c = _card(ids["doc.a"], policy=Y, name="Синтетик Третий")
    d = _card(ids["doc.b"], policy=Y, name="Синтетик Четвертый")
    _scan(dev)
    mark_cid = _candidate_for(c, d)["id"]                          # решение и пометка — у разных кандидатов
    tab = _client_for(client, username)
    expected = EXPECTED.get("developer" if username == DEV_USERNAME else username,
                            {k: 403 for k in _registry_calls(cid, d)})
    bodies = set()
    for name, call in _registry_calls(cid, d, mark_cid=mark_cid).items():
        response = call(tab)
        assert response.status_code == expected[name], (username, name, response.text)
        if response.status_code == 403:
            bodies.add(response.text)
            assert "Синтетик" not in response.text and cid not in response.text
    assert len(bodies) <= 1                                         # единый ответ отказа


def test_unauthenticated_and_csrf(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    client.cookies.clear()
    assert client.get("/api/duplicates/candidates").status_code == 401
    assert deputy.post(f"/api/duplicates/candidates/{cid}/mark",
                       json={"patientId": b, "reason": REASON, "expectedVersion": 1}).status_code == 403
    assert _erroneous() == set()


def test_idor_unknown_candidate_and_unrelated_confirmation(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    for tab in (dev, deputy):
        for probe in ("f" * 32, "../" + cid, cid + "x", "x" * 200):
            assert tab.get(f"/api/duplicates/candidates/{probe}").status_code in (404, 422), probe
    doctor = _client_for(client, "doc.a")
    known, unknown = doctor.get(f"/api/duplicates/candidates/{cid}"), doctor.get(f"/api/duplicates/candidates/{'f' * 32}")
    assert known.status_code == unknown.status_code == 403 and known.text == unknown.text
    assert _mark(deputy, "f" * 32, b, version=1).status_code == 404
    response = _mark(deputy, cid, "f" * 32)                         # карточка не из пары
    assert response.status_code == 422 and _erroneous() == set()


def test_developer_cannot_decide_mark_unmark_or_read_reasons(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    before = (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_erroneous_marks"))
    assert _mark(dev, cid, b).status_code == 403
    assert _decide(dev, cid, "not_duplicate").status_code == 403
    assert _unmark(dev, b, "m" * 32).status_code == 403
    assert (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_erroneous_marks")) == before
    assert "confirmations" not in _detail(dev, cid).json()
    assert {a["detail"] for a in _audit("access_denied")} >= {"action=duplicate_registry_mark",
                                                                "action=duplicate_registry_decision"}


# ================================================================ 13–15. marking and clinical dependencies

def test_deputy_marks_a_card_without_dependencies(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    digest = _med_digest(a, b)
    response = _mark(deputy, cid, b)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "resolved" and body["patientId"] == b
    marks = _rows("SELECT * FROM patient_erroneous_marks")
    assert len(marks) == 1 and marks[0]["action"] == "mark" and marks[0]["by_user_id"] == ids["deputy"]
    assert marks[0]["reason"] == REASON and _erroneous() == {b}
    assert _med_digest(a, b) == digest
    audit_row = _audit("patient_marked_erroneous")[0]
    assert audit_row["patient_id"] == b and REASON not in audit_row["detail"] and audit_row["actor_role"] == "deputy_chief_oncology"


def _consilium(pid, owner, **cols):
    values = {"id": uuid.uuid4().hex, "patient_id": pid, "created_by_user_id": owner, "scheduled_date": "2026-11-12",
              "scheduled_time": "10:00", "consilium_group": "thoracic", "has_benefit": 0,
              "treating_doctor_name": "Синтетов", "primary_diagnosis": "C34.1", "needs_surgeon": 0,
              "needs_radiotherapist": 0, "created_at": "2026-10-01T00:00:00+00:00",
              "updated_at": "2026-10-01T00:00:00+00:00", **cols}
    get_conn().execute(f"INSERT INTO oncology_consilium_appointments ({', '.join(values)}) "  # nosec - test
                       f"VALUES ({', '.join('?' * len(values))})", tuple(values.values()))


def _first_event(pid):
    return _rows("SELECT e.id FROM events e JOIN history_entries h ON h.id = e.history_entry_id "
                 "WHERE h.patient_id = ? ORDER BY e.seq LIMIT 1", pid)[0]["id"]


def _notification(pid, status, **cols):
    values = {"id": uuid.uuid4().hex, "patient_id": pid, "channel": "sms", "template_code": "custom_reminder",
              "template_version": 1, "source_type": "manual", "source_id": uuid.uuid4().hex,
              "scheduled_at": "2026-10-02T09:00:00+00:00", "idempotency_key": uuid.uuid4().hex, "status": status,
              "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00", **cols}
    get_conn().execute(f"INSERT INTO notifications ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",  # nosec
                       tuple(values.values()))


DEPENDENCIES = {
    "consilium_records": lambda ids, p: _consilium(p, ids["doc.b"]),
    "consilium_archived": lambda ids, p: _consilium(p, ids["doc.b"], deleted_at="2026-10-01", deleted_by_user_id=ids["doc.b"]),
    "signed_consilium": lambda ids, p: _consilium(p, ids["doc.b"], status="signed", signed_at="2026-10-01"),
    "consilium_decision_marks": lambda ids, p: get_conn().execute(
        "UPDATE events SET consilium_decision = 'treatment_strategy_defined', consilium_decision_by_user_id = ?, "
        "consilium_decision_at = '2026-09-01T09:00:00+00:00', consilium_decision_source = 'manual' WHERE id = ?",
        (ids["doc.b"], _first_event(p))),
    "performed_events": lambda ids, p: get_conn().execute(
        "UPDATE events SET actual_date = '2026-09-01' WHERE id = ?", (_first_event(p),)),
    "archived_events": lambda ids, p: get_conn().execute(
        "UPDATE events SET deleted_at = '2026-09-01', deleted_by_user_id = ? WHERE id = ?", (ids["doc.b"], _first_event(p))),
    "stage_transitions": lambda ids, p: get_conn().execute(
        "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at) VALUES (?, ?, 1, 'appointment', '2026-09-01')",
        (uuid.uuid4().hex, p)),
    "diagnosis_history": lambda ids, p: get_conn().execute(
        "INSERT INTO patient_diagnosis_history (id, patient_id, changed_at, old_diagnosis, new_diagnosis) "
        "VALUES (?, ?, '2026-09-01', 'C34.1', 'C34.2')", (uuid.uuid4().hex, p)),
    "attention_issues": lambda ids, p: get_conn().execute(
        "INSERT INTO patient_attention_issues (id, patient_id, reason_code, status, created_at, created_by_user_id, "
        "closed_at, closed_by_user_id) VALUES (?, ?, 'other', 'closed', '2026-09-01', ?, '2026-09-02', ?)",
        (uuid.uuid4().hex, p, ids["doc.b"], ids["doc.b"])),
    "notification_consent": lambda ids, p: get_conn().execute(
        "INSERT INTO patient_notification_preferences (patient_id, sms_enabled, consent_status, updated_at) "
        "VALUES (?, 0, 'revoked', '2026-09-01')", (p,)),
    "sent_notifications": lambda ids, p: _notification(p, "sent", sent_at="2026-10-02T09:00:00+00:00"),
    "pending_notifications": lambda ids, p: _notification(p, "pending"),
}
EXPECTED_CODE = {"consilium_archived": "consilium_records"}


@pytest.mark.parametrize("dependency", sorted(DEPENDENCIES))
def test_every_clinical_dependency_blocks_the_mark(client, dependency):
    def deps(ids, p):
        DEPENDENCIES[dependency](ids, p)
        get_conn().commit()

    ids = _world()
    a, b = _pair(ids)
    get_conn().execute("UPDATE patients SET stage = stage WHERE id = ?", (b,))
    _card_events = _rows("SELECT COUNT(*) AS c FROM events")  # noqa: F841
    deps(ids, b)
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    cid = _candidate_for(a, b)["id"]
    detail = _detail(deputy, cid).json()
    card = [p for p in detail["patients"] if p["id"] == b][0]
    code = EXPECTED_CODE.get(dependency, dependency)
    assert code in {d["code"] for d in card["dependencies"]}
    assert b not in detail["allowedActions"]["markPatientIds"]
    digest = _med_digest(a, b)
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "CLINICAL_DEPENDENCIES"
    assert code in response.json()["dependencies"]
    assert _erroneous() == set() and _rows("SELECT * FROM patient_erroneous_marks") == []
    assert _state(cid) == "needs_clinical_review"
    review = _rows("SELECT * FROM patient_duplicate_reviews WHERE candidate_id = ?", cid)[-1]
    assert review["action"] == "needs_clinical_review" and code in json.loads(review["dependency_codes"])
    assert _med_digest(a, b) == digest
    refused = _audit("patient_mark_refused")[0]
    assert refused["patient_id"] == b and "Синтетик" not in refused["detail"]
    # needs_clinical_review остается открытым: пометка в этом состоянии невозможна.
    assert _mark(deputy, cid, b).json()["code"] == "TRANSITION_NOT_ALLOWED"


def test_planned_events_and_initial_route_do_not_block(client):
    ids = _world()
    a = _card(ids["doc.a"], policy=X, name="Синтетик Первый")
    b = _card(ids["doc.b"], policy=X, name="Синтетик Второй", events=[{"date": "2026-11-20", "time": "10:00"}])
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    assert _mark(deputy, _candidate_for(a, b)["id"], b).status_code == 200


def test_signed_decision_is_never_touched(client):
    ids, a, b, cid, dev, deputy = _setup(client, deps=lambda ids, p: (
        _consilium(p, ids["doc.b"], status="signed", signed_at="2026-10-01"), get_conn().commit()))
    signed = _rows("SELECT * FROM oncology_consilium_appointments")
    assert _mark(deputy, cid, b).status_code == 409
    assert _decide(deputy, cid, "not_duplicate").status_code == 200
    assert _rows("SELECT * FROM oncology_consilium_appointments") == signed


# ================================================================ 16–18. races under the SQLite write lock

def _consilium_sql(ids, pid):
    return ("INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date, "
            "scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis, needs_surgeon, "
            "needs_radiotherapist, created_at, updated_at) VALUES (?, ?, ?, '2026-11-12', '10:00', 'thoracic', 0, "
            "'Синтетов', 'C34.1', 0, 0, '2026-10-01', '2026-10-01')", (uuid.uuid4().hex, pid, ids["doc.b"]))


@pytest.mark.parametrize("repeat", range(3))
def test_mark_against_a_new_consilium_record(client, repeat):
    ids, a, b, cid, dev, deputy = _setup(client)
    sql, params = _consilium_sql(ids, b)
    response, waited = _while_locked(lambda: _mark(deputy, cid, b), sql, params)
    assert waited and response.status_code == 409 and response.json()["code"] == "CLINICAL_DEPENDENCIES"
    assert "consilium_records" in response.json()["dependencies"] and _erroneous() == set()


@pytest.mark.parametrize("repeat", range(3))
def test_mark_against_a_new_treatment_fact(client, repeat):
    ids, a, b, cid, dev, deputy = _setup(client, deps=lambda ids, p: None)
    eid = None
    b2 = _card(ids["doc.b"], policy="7700000000051077", name="Синтетик Факта",
               events=[{"date": "2026-11-20", "time": "10:00"}])
    a2 = _card(ids["doc.a"], policy="7700000000051077", name="Синтетик Пары")
    _scan(dev)
    cid2 = _candidate_for(a2, b2)["id"]
    eid = _first_event(b2)
    response, waited = _while_locked(lambda: _mark(deputy, cid2, b2),
                                     "UPDATE events SET actual_date = '2026-10-01' WHERE id = ?", (eid,))
    assert waited and response.status_code == 409 and "performed_events" in response.json()["dependencies"]
    assert _erroneous() == set()


def test_write_after_mark_is_rejected_by_the_database(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    assert _mark(deputy, cid, b).status_code == 200
    sql, params = _consilium_sql(ids, b)
    with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
        get_conn().execute(sql, params)
    get_conn().rollback()


@pytest.mark.parametrize("change", [
    "UPDATE users SET role_id = 'developer' WHERE id = ?",
    "UPDATE users SET status = 'blocked' WHERE id = ?",
])
def test_rights_change_while_waiting_refuses_mark_and_unmark(client, change):
    ids, a, b, cid, dev, deputy = _setup(client)
    response, waited = _while_locked(lambda: _mark(deputy, cid, b), change, (ids["deputy"],))
    assert waited and response.status_code == 403 and _erroneous() == set()
    get_conn().execute("UPDATE users SET role_id = 'deputy_chief_oncology', status = 'active' WHERE id = ?",
                       (ids["deputy"],))
    get_conn().commit()
    deputy = _client_for(client, "deputy")
    mark_id = _mark(deputy, cid, b).json()["markId"]
    response, waited = _while_locked(lambda: _unmark(deputy, b, mark_id), change, (ids["deputy"],))
    assert waited and response.status_code == 403 and _erroneous() == {b}


# ================================================================ 19. mandatory audit and rollback

@pytest.mark.parametrize("event", ["patient_marked_erroneous", "patient_mark_refused"])
def test_audit_failure_rolls_back_the_mark(client, event):
    deps = None if event == "patient_marked_erroneous" else (
        lambda ids, p: (_consilium(p, ids["doc.b"]), get_conn().commit()))
    ids, a, b, cid, dev, deputy = _setup(client, deps=deps)
    before = (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_duplicate_candidates"))
    _fail_audit(event)
    response = _mark(deputy, cid, b)
    assert response.status_code == 503
    assert _erroneous() == set() and _rows("SELECT * FROM patient_erroneous_marks") == []
    assert (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_duplicate_candidates")) == before


def test_audit_failure_rolls_back_unmark_decision_scan_and_views(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    mark_id = _mark(deputy, cid, b).json()["markId"]
    _fail_audit("patient_unmarked_erroneous")
    assert _unmark(deputy, b, mark_id).status_code == 503 and _erroneous() == {b}
    _restore_audit()
    _fail_audit("duplicate_candidate_viewed")
    response = _detail(deputy, cid)
    assert response.status_code == 503 and "Синтетик" not in response.text
    _restore_audit()
    _fail_audit("duplicate_registry_viewed")
    response = deputy.get("/api/duplicates/candidates")
    assert response.status_code == 503 and "Синтетик" not in response.text
    _restore_audit()
    _fail_audit("duplicate_scan_completed")
    _card(ids["doc.a"], policy=Y, name="Синтетик Новый")
    _card(ids["doc.b"], policy=Y, name="Синтетик Новый Второй")
    before = _rows("SELECT * FROM patient_duplicate_candidates")
    assert dev.post("/api/duplicates/scans", headers=dev.csrf).status_code == 503
    assert _rows("SELECT * FROM patient_duplicate_candidates") == before
    assert _rows("SELECT status FROM patient_duplicate_scans ORDER BY started_at DESC LIMIT 1") == [{"status": "failed"}]


# ================================================================ 20–22. append-only history, repeats, restore

def test_repeated_mark_and_unmark_keep_a_consistent_history(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    version = _candidate_for(a, b)["version"]
    first = _mark(deputy, cid, b, version=version)
    again = _mark(deputy, cid, b, version=version)
    assert first.status_code == 200 and again.status_code == 409
    mark_id = first.json()["markId"]
    assert _unmark(deputy, b, mark_id).status_code == 200
    repeat = _unmark(deputy, b, mark_id)
    assert repeat.status_code == 409 and repeat.json()["code"] == "NOT_ERRONEOUS"
    assert [r["action"] for r in _rows("SELECT action FROM patient_erroneous_marks ORDER BY seq")] == ["mark", "unmark"]
    for sql in ("UPDATE patient_erroneous_marks SET reason = 'x'", "DELETE FROM patient_erroneous_marks",
                "UPDATE patient_duplicate_reviews SET reason = 'x'", "DELETE FROM patient_duplicate_reviews"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            get_conn().execute(sql)
        get_conn().rollback()


@pytest.mark.parametrize("repeat", range(3))
def test_concurrent_marks_and_unmarks(client, repeat):
    ids, a, b, cid, dev, deputy = _setup(client)
    other = _client_for(client, "deputy")
    version = _candidate_for(a, b)["version"]
    res = _parallel(lambda: _mark(deputy, cid, b, version=version), lambda: _mark(other, cid, a, version=version))
    assert sorted(r.status_code for r, *_ in res) == [200, 409]
    assert len(_erroneous()) == 1 and len(_rows("SELECT * FROM patient_erroneous_marks")) == 1
    pid = next(iter(_erroneous()))
    mark_id = _rows("SELECT mark_id FROM patient_erroneous_state")[0]["mark_id"]
    res = _parallel(lambda: _unmark(deputy, pid, mark_id), lambda: _unmark(other, pid, mark_id))
    assert sorted(r.status_code for r, *_ in res) == [200, 409]
    assert _erroneous() == set() and len(_rows("SELECT * FROM patient_erroneous_marks")) == 2


def test_unmark_restores_the_card_to_normal_work(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    doctor = _client_for(client, "doc.b")
    digest = _med_digest(a, b)
    mark_id = _mark(deputy, cid, b).json()["markId"]
    assert doctor.get(f"/api/patients/{b}").status_code == 404
    response = _unmark(deputy, b, mark_id, reason="Синтетическая причина снятия")
    assert response.status_code == 200 and response.json()["candidateState"] == "open"
    assert doctor.get(f"/api/patients/{b}").status_code == 200
    assert b in {p["id"] for p in doctor.get("/api/patients").json()}
    assert doctor.patch(f"/api/patients/{b}", json={"field": "complaint", "value": "Новая жалоба"},
                        headers=doctor.csrf).status_code == 200
    history = _rows("SELECT action, reverts_mark_id FROM patient_erroneous_marks ORDER BY seq")
    assert history == [{"action": "mark", "reverts_mark_id": None}, {"action": "unmark", "reverts_mark_id": mark_id}]
    assert [r["action"] for r in _rows("SELECT action FROM patient_duplicate_reviews ORDER BY seq")] == [
        "marked_erroneous", "mark_reverted"]
    digest_after = _med_digest(a, b)
    assert {k: v for k, v in digest_after.items() if k != "patients"} == {k: v for k, v in digest.items() if k != "patients"}


# ================================================================ 23–29. erroneous card is out of active work

def _mark_b(client, **kw):
    ids, a, b, cid, dev, deputy = _setup(client, **kw)
    assert _mark(deputy, cid, b).status_code == 200
    return ids, a, b, cid, dev, deputy


def test_every_write_route_refuses_the_erroneous_card(client):
    from .conftest import stage_body  # noqa: F401
    ids = _world()
    a = _card(ids["doc.a"], policy=X, name="Синтетик Первый")
    b = _card(ids["doc.b"], policy=X, name="Синтетик Второй", events=[{"date": "2026-11-20", "time": "10:00"}])
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    doctor = _client_for(client, "doc.b")
    event = doctor.get(f"/api/patients/{b}").json()["history"][0]["events"][0]
    entry = doctor.get(f"/api/patients/{b}").json()["history"][0]
    assert _mark(deputy, _candidate_for(a, b)["id"], b).status_code == 200
    digest = _med_digest(b)
    for tab in (doctor, dev):
        calls = [
            tab.patch(f"/api/patients/{b}", json={"field": "complaint", "value": "x"}, headers=tab.csrf),
            tab.patch(f"/api/patients/{b}", json={"field": "policyNumber", "value": Y}, headers=tab.csrf),
            tab.delete(f"/api/patients/{b}", headers=tab.csrf),
            tab.post(f"/api/patients/{b}/stage", json={"newStage": "appointment", "expectedStage": "exam",
                                                      "expectedHistoryEntryId": entry.get("id")}, headers=tab.csrf),
            tab.patch(f"/api/patients/{b}/history/0/note", json={"note": "x"}, headers=tab.csrf),
            tab.post(f"/api/patients/{b}/history/0/events", json={"date": "2026-11-21", "purpose": "add",
                                                                 "expectedEventIds": [event["id"]]}, headers=tab.csrf),
            tab.patch(f"/api/patients/{b}/events/{event['id']}", json={"field": "date", "value": "2026-11-22",
                                                                      "version": event["version"]}, headers=tab.csrf),
            tab.delete(f"/api/patients/{b}/events/{event['id']}?version={event['version']}", headers=tab.csrf),
            tab.post(f"/api/patients/{b}/attention", json={"reasonCode": "other", "eventDate": None, "comment": "x"},
                     headers=tab.csrf),
            tab.put(f"/api/notifications/patients/{b}/preferences", json={"smsEnabled": True, "consentStatus": "granted"},
                    headers=tab.csrf),
            tab.post("/api/consilium/appointments", json={"patientId": b, "scheduledDate": "2026-11-12",
                                                          "scheduledTime": "10:00", "consiliumGroup": "thoracic"},
                     headers=tab.csrf),
            tab.get(f"/api/patients/{b}"),
            tab.get(f"/api/patients/{b}/attention"),
            tab.get(f"/api/notifications/patients/{b}"),
        ]
        if tab is dev:
            calls.append(tab.put(f"/api/patients/{b}/treating-doctor", json={"treatingDoctorUserId": ids["doc.a"]},
                                 headers=tab.csrf))
        for response in calls:
            assert response.status_code in (403, 404, 409, 422), (response.request.method, response.request.url, response.text)
            assert response.status_code != 200
    assert _med_digest(b) == digest and _erroneous() == {b}


def test_database_rejects_direct_writes_to_an_erroneous_card(client):
    ids, a, b, cid, dev, deputy = _mark_b(client)
    statements = [
        ("UPDATE patients SET name = 'x' WHERE id = ?", (b,)),
        ("DELETE FROM patients WHERE id = ?", (b,)),
        ("INSERT INTO history_entries (id, patient_id, seq, stage, entered_at) VALUES ('h', ?, 5, 'exam', 'now')", (b,)),
        ("UPDATE history_entries SET note = 'x' WHERE patient_id = ?", (b,)),
        ("DELETE FROM history_entries WHERE patient_id = ?", (b,)),
        ("INSERT INTO patient_diagnosis_history (id, patient_id, changed_at) VALUES ('d', ?, 'now')", (b,)),
        ("INSERT INTO future_plans (patient_id, template_index) VALUES (?, 9)", (b,)),
        ("INSERT INTO patient_attention_issues (id, patient_id, reason_code, status, created_at, created_by_user_id) "
         "VALUES ('i', ?, 'other', 'open', 'now', ?)", (b, ids["doc.b"])),
        ("INSERT INTO patient_notification_preferences (patient_id, updated_at) VALUES (?, 'now')", (b,)),
        _consilium_sql(ids, b),
    ]
    for sql, params in statements:
        with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
            get_conn().execute(sql, params)
        get_conn().rollback()
    # Карточка с событием: изменение события тоже отклоняется.
    c = _card(ids["doc.a"], policy="7700000000051088", name="Синтетик С Событием", events=[{"date": "2026-11-20"}])
    d = _card(ids["doc.b"], policy="7700000000051088", name="Синтетик Пары События")
    _scan(dev)
    cid2 = _candidate_for(c, d)["id"]
    assert _mark(deputy, cid2, c).status_code == 200
    with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
        get_conn().execute("UPDATE events SET date = '2026-12-01' WHERE id = ?", (_first_event(c),))
    get_conn().rollback()


def test_lists_before_and_after_mark_and_unmark(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    doctor = _client_for(client, "doc.b")

    def visible(tab):
        return {p["id"] for p in tab.get("/api/patients").json()}

    assert b in visible(doctor) and b in visible(dev) and b in visible(deputy)
    mark_id = _mark(deputy, cid, b).json()["markId"]
    assert b not in visible(doctor) and b not in visible(dev) and b not in visible(deputy)
    assert a in visible(dev)
    erroneous = deputy.get("/api/duplicates/erroneous").json()["items"]
    assert [i["id"] for i in erroneous] == [b] and erroneous[0]["markReason"] == REASON
    assert _unmark(deputy, b, mark_id).status_code == 200
    assert b in visible(doctor) and deputy.get("/api/duplicates/erroneous").json()["items"] == []


def _total_patients(tab):
    cards = {c["code"]: c for c in tab.get("/api/analytics/dashboard").json()["summary"]["cards"]}
    return cards["total_patients"]["value"]


def test_analytics_counts_only_active_cards(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    before = _total_patients(dev)
    before_deputy = _total_patients(deputy)
    mark_id = _mark(deputy, cid, b).json()["markId"]
    assert _total_patients(dev) == before - 1 and _total_patients(deputy) == before_deputy - 1
    _unmark(deputy, b, mark_id)
    assert _total_patients(dev) == before


def test_candidates_do_not_change_statistics_before_a_mark(client):
    ids = _world()
    a, b = _pair(ids)
    dev = _client_for(client, DEV_USERNAME)

    def without_time(value):
        return {k: v for k, v in value.items() if not k.endswith("_at")}

    def views():
        return (_total_patients(dev), without_time(dev.get("/api/data-quality/summary").json()),
                without_time(dev.get("/api/internal-notifications/summary").json()))

    before = views()
    _scan(dev)
    deputy = _client_for(client, "deputy")
    cid = _candidate_for(a, b)["id"]
    _decide(deputy, cid, "needs_clinical_review")
    assert views() == before


def test_data_quality_and_internal_notifications_exclude_the_card(client):
    ids = _world()
    a = _card(ids["doc.a"], policy=X, name="Синтетик Первый")
    b = _card(ids["doc.b"], policy=X, name="Синтетик Второй", phone="", events=[{"date": "2026-09-01", "time": "09:00"}])
    dev, deputy, doctor = (_client_for(client, u) for u in (DEV_USERNAME, "deputy", "doc.b"))
    _scan(dev)

    def dq_ids(tab):
        issues = tab.get("/api/data-quality/issues?limit=200").json()
        return {i["patient_id"] for i in issues["issues"]} | {p for g in issues["duplicate_groups"] for p in g["patient_ids"]}

    def notified(tab):
        return {i.get("patient_id") or i.get("patientId") for i in tab.get("/api/internal-notifications").json()["items"]}

    assert b in dq_ids(dev) and b in notified(doctor)
    assert _mark(deputy, _candidate_for(a, b)["id"], b).status_code == 200
    assert b not in dq_ids(dev) and b not in notified(doctor) and b not in dq_ids(doctor)


def test_consilium_and_ai_tools_ignore_the_card(client):
    from backend.app import policy
    from backend.app.ai import tools
    ids, a, b, cid, dev, deputy = _mark_b(client)
    doctor = _client_for(client, "doc.b")
    booked = doctor.get("/api/consilium/booked-patients")
    assert booked.status_code in (200, 403) and b not in booked.text
    for username in ("doc.b", DEV_USERNAME):
        user = policy.load_current_user(get_conn(), ids["dev" if username == DEV_USERNAME else username])
        found = tools.search_accessible_patients(get_conn(), user, tools.SearchAccessiblePatientsArgs(query="Синтетик"))
        assert b not in json.dumps(found, ensure_ascii=False)
        with pytest.raises(Exception):
            tools.get_patient_summary(get_conn(), user, tools.PatientIdArgs(patientId=b))


def test_export_excludes_the_card(client):
    ids, a, b, cid, dev, deputy = _mark_b(client)
    export = dev.get("/api/export", headers=dev.csrf)
    assert export.status_code == 200 and b not in export.text and a in export.text


def test_wp24c_detection_ignores_an_erroneous_card(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    # Вторая карточка своего отделения с тем же полисом — карточка b врача doc.b.
    doctor = _client_for(client, "doc.b")
    warned = doctor.post("/api/patients", json=make_patient_payload(policyNumber=X, name="Синтетик Третий"),
                         headers=doctor.csrf)
    assert warned.status_code == 409 and warned.json()["code"] == "POSSIBLE_DUPLICATE"
    assert _mark(deputy, cid, b).status_code == 200
    created = doctor.post("/api/patients", json=make_patient_payload(policyNumber=X, name="Синтетик Третий"),
                          headers=doctor.csrf)
    assert created.status_code == 201


# ================================================================ 30–32. WP2.4-C reasons and logs

def _with_confirmation(client):
    ids = _world()
    doctor = _client_for(client, "doc.a")
    first = doctor.post("/api/patients", json=make_patient_payload(policyNumber=X, name="Синтетик Исходный"),
                        headers=doctor.csrf).json()["id"]
    body = make_patient_payload(policyNumber=X, name="Синтетик Повторный")
    body["duplicateConfirmation"] = {"reason": "Синтетическая причина подтверждения C", "matchedPatientIds": [first]}
    second = doctor.post("/api/patients", json=body, headers=doctor.csrf).json()["id"]
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    cid = _candidate_for(first, second)["id"]
    conf_id = _rows("SELECT id FROM patient_duplicate_confirmations")[0]["id"]
    return ids, first, second, cid, conf_id, dev, deputy


def test_confirmation_reasons_only_for_the_deputy_with_audit(client):
    ids, first, second, cid, conf_id, dev, deputy = _with_confirmation(client)
    detail = _detail(deputy, cid).json()
    assert [c["id"] for c in detail["confirmations"]] == [conf_id]
    assert "Синтетическая причина подтверждения C" not in json.dumps(detail, ensure_ascii=False)
    assert "Синтетическая причина подтверждения C" not in deputy.get("/api/duplicates/candidates").text
    url = f"/api/duplicates/candidates/{cid}/confirmations/{conf_id}/reason"
    for username in (DEV_USERNAME, "doc.a", "head.1", "chair", "caop"):
        tab = _client_for(client, username)
        response = tab.post(url, headers=tab.csrf)
        assert response.status_code == 403 and "Синтетическая причина" not in response.text
    assert deputy.get(url).status_code == 405                                  # только POST с CSRF
    assert deputy.post(url).status_code == 403
    response = deputy.post(url, headers=deputy.csrf)
    assert response.status_code == 200 and response.json()["reason"] == "Синтетическая причина подтверждения C"
    viewed = _audit("duplicate_confirmation_reason_viewed")
    assert len(viewed) == 1 and viewed[0]["detail"] == f"candidate_id={cid} confirmation_id={conf_id}"
    assert viewed[0]["actor_role"] == "deputy_chief_oncology"


def test_confirmation_reason_needs_a_related_candidate_and_audit(client):
    ids, first, second, cid, conf_id, dev, deputy = _with_confirmation(client)
    c = _card(ids["doc.a"], policy=Y, name="Синтетик Другой Пары")
    d = _card(ids["doc.b"], policy=Y, name="Синтетик Другой Пары Два")
    _scan(dev)
    other = _candidate_for(c, d)["id"]
    unrelated = deputy.post(f"/api/duplicates/candidates/{other}/confirmations/{conf_id}/reason", headers=deputy.csrf)
    missing = deputy.post(f"/api/duplicates/candidates/{cid}/confirmations/{'0' * 32}/reason", headers=deputy.csrf)
    assert unrelated.status_code == missing.status_code == 404 and unrelated.text == missing.text
    _fail_audit("duplicate_confirmation_reason_viewed")
    failed = deputy.post(f"/api/duplicates/candidates/{cid}/confirmations/{conf_id}/reason", headers=deputy.csrf)
    assert failed.status_code == 503 and "Синтетическая причина" not in failed.text


def test_no_personal_data_or_reasons_in_audit_and_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    ids, first, second, cid, conf_id, dev, deputy = _with_confirmation(client)
    _technical(dev, cid, "note", "Синтетический технический комментарий")
    _detail(deputy, cid)
    deputy.get("/api/duplicates/candidates")
    deputy.post(f"/api/duplicates/candidates/{cid}/confirmations/{conf_id}/reason", headers=deputy.csrf)
    mark_id = _mark(deputy, cid, second).json()["markId"]
    _unmark(deputy, second, mark_id, reason="Синтетическая причина снятия")
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False) + caplog.text
    for value in ("Синтетик", X, "C34.1", "Синтетическая причина", "Синтетический технический", REASON):
        assert value not in dump, value
    events = {r["event_type"] for r in _rows("SELECT event_type FROM audit_events")}
    assert {"duplicate_scan_started", "duplicate_scan_completed", "duplicate_registry_viewed",
            "duplicate_candidate_viewed", "duplicate_technical_review", "duplicate_confirmation_reason_viewed",
            "patient_marked_erroneous", "patient_unmarked_erroneous"} <= events


def test_markup_is_data_and_client_uses_no_inner_html():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = js.index("    /* ========================================= ВОЗМОЖНЫЕ ДУБЛИ ПАЦИЕНТОВ */")
    end = js.index("    /* =========================================", start + 10)
    section = js[start:end]
    assert "innerHTML" not in section and "onclick=" not in section and "insertAdjacentHTML" not in section
    assert DISCLAIMER in section
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="view-duplicates"' in html and "onclick" not in html


def test_markup_in_names_is_returned_as_json_data(client):
    ids = _world()
    a = _card(ids["doc.a"], policy=X, name='<img src=x onerror="alert(1)">')
    b = _card(ids["doc.b"], policy=X, name="Синтетик Второй")
    dev = _client_for(client, DEV_USERNAME)
    _scan(dev)
    response = _detail(dev, _candidate_for(a, b)["id"])
    assert response.headers["content-type"].startswith("application/json")
    assert any(p["name"] == '<img src=x onerror="alert(1)">' for p in response.json()["patients"])
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]


# ================================================================ 33–34. nothing moved, deleted or rewritten

def test_mark_and_unmark_never_move_or_delete_history(client):
    def deps(ids, p):
        return None

    ids, a, b, cid, dev, deputy = _setup(client, deps=deps)
    get_conn().execute("UPDATE patients SET complaint = 'Синтетическая жалоба А' WHERE id = ?", (a,))
    get_conn().commit()
    digest = _med_digest(a, b)
    counts = {t: _rows(f"SELECT COUNT(*) AS c FROM {t}")[0]["c"] for t in ("patients", *MEDICAL)}  # nosec - test
    mark_id = _mark(deputy, cid, b).json()["markId"]
    assert _med_digest(a, b) == digest
    _unmark(deputy, b, mark_id)
    assert {t: _rows(f"SELECT COUNT(*) AS c FROM {t}")[0]["c"] for t in ("patients", *MEDICAL)} == counts  # nosec
    assert {k: v for k, v in _med_digest(a, b).items()} == digest


def test_scan_and_registry_only_read_medical_tables(client):
    ids = _world()
    a, b = _pair(ids)
    digest = _med_digest(a, b)
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    cid = _candidate_for(a, b)["id"]
    _detail(deputy, cid)
    deputy.get("/api/duplicates/candidates")
    _technical(dev, cid, "start")
    _decide(deputy, cid, "not_duplicate")
    assert _med_digest(a, b) == digest


def test_scan_is_not_called_by_creation_import_or_preview():
    app = PROJECT_ROOT / "backend" / "app"
    for path in ("services/patient_create.py", "services/patient_policy.py", "services/patient_duplicates.py",
                 "services/import_operations.py", "importexport.py", "routes/patients.py", "routes/importexport.py",
                 "routes/migrate.py", "identity.py"):
        source = (app / path).read_text(encoding="utf-8")
        assert "duplicate_review" not in source and "patient_duplicate_candidates" not in source, path


# ================================================================ 35–37. schema v26, migration, backups

def test_v25_fixture_migrates_to_v26_without_touching_rows(client, tmp_path):
    from backend.app import config, db
    from backend.app.maintenance import identify_backup_build
    path = _sqlite_from_fixture(tmp_path / "v25.sqlite", fixture=V25_FIXTURE)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 25
    assert identify_backup_build(path, 25) == "wp24c-e92e3f5"
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name")]
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}  # nosec
    signed = conn.execute("SELECT * FROM oncology_consilium_appointments WHERE status = 'signed'").fetchall()
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 26
    for t in tables:
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] == before[t], t  # nosec
    assert [tuple(r) for r in conn.execute("SELECT * FROM oncology_consilium_appointments WHERE status = 'signed'")] == [
        tuple(r) for r in signed]
    for t in ("patient_duplicate_scans", "patient_duplicate_candidates", "patient_duplicate_reviews",
              "patient_erroneous_marks", "patient_erroneous_state"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0  # nosec - test constant
    schema = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    statements = []
    conn.set_trace_callback(statements.append)
    db.init_db(conn)
    conn.set_trace_callback(None)
    assert not [s for s in statements if "DROP TRIGGER" in s.upper() or "ALTER TABLE" in s.upper()]
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == schema
    assert "UNIQUE" not in " ".join(r[0] or "" for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql LIKE '%policy_number%' AND sql LIKE '%UNIQUE%'"))
    conn.close()


def test_restore_of_a_v25_backup_migrates_to_v26(auth_client, tmp_path, backup_key):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp24c-v25.cbk", header=25, fixture=V25_FIXTURE)
    assert maintenance.validate_backup(backup)["source_build"] == "wp24c-e92e3f5"
    maintenance.restore_database(backup, live, confirm_offline=True)
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 26
    assert conn.execute("SELECT COUNT(*) FROM patient_duplicate_confirmations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()[0] == 1
    conn.close()


def test_backup_and_restore_of_v26_keep_the_registry_and_marks(client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    ids, a, b, cid, dev, deputy = _mark_b(client)
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "v26.cbk")
    assert maintenance.validate_backup(backup)["source_build"] == "current"
    db.reset_singleton_for_tests()
    maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    assert conn.execute("SELECT patient_id FROM patient_erroneous_state").fetchall() == [(b,)]
    assert conn.execute("SELECT COUNT(*) FROM patient_erroneous_marks").fetchone()[0] == 1
    assert conn.execute("SELECT state FROM patient_duplicate_candidates").fetchall() == [("resolved",)]
    with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
        conn.execute("UPDATE patients SET name = 'x' WHERE id = ?", (b,))
    conn.close()


# ================================================================ 41–42. client permissions

@pytest.mark.parametrize("username,visible", [(DEV_USERNAME, True), ("deputy", True), ("doc.a", False),
                                              ("head.1", False), ("chair", False), ("caop", False)])
def test_permissions_payload_shows_the_section_only_to_registry_roles(client, username, visible):
    _world()
    tab = _client_for(client, username)
    perms = tab.get("/api/auth/me").json()["user"]["permissions"]
    assert perms["viewDuplicateRegistry"] is visible
    assert perms["scanDuplicates"] is (username == DEV_USERNAME)
    assert perms["markErroneous"] is (username == "deputy")
    assert perms["readDuplicateConfirmationReasons"] is (username == "deputy")


def test_import_update_and_add_cannot_touch_or_revive_the_card(client):
    from .conftest import legacy_export, unsigned_import
    ids, a, b, cid, dev, deputy = _setup(client)
    exported = dev.get("/api/export", headers=dev.csrf).json()
    assert _mark(deputy, cid, b).status_code == 200
    digest = _med_digest(b)
    document = legacy_export(exported)
    for p in document["patients"]:
        p["complaint"] = "Изменено импортом"
    raw = json.dumps(document, ensure_ascii=False)
    login(client, DEV_USERNAME)
    update = unsigned_import(client, raw, "update")
    assert update.status_code in (200, 409, 422), update.text
    signed = client.post("/api/import/commit", json={"raw": json.dumps(exported, ensure_ascii=False), "mode": "add"},
                         headers=client.csrf)
    assert signed.status_code != 200                       # подписанный файл с ID помеченной карточки — не новая карточка
    assert _med_digest(b) == digest and _erroneous() == {b}


# ================================================================ review fix 1: signals must still hold at mark time

def test_mark_is_refused_when_the_pair_no_longer_matches(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    get_conn().execute("UPDATE patients SET policy_number = ? WHERE id = ?", (Y, b))
    get_conn().commit()
    detail = _detail(deputy, cid).json()
    assert detail["currentReasonCodes"] == [] and detail["signalsOutdated"] is True
    assert detail["allowedActions"]["markPatientIds"] == []
    before = (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_duplicate_candidates"))
    digest = _med_digest(a, b)
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "SIGNALS_CHANGED", response.text
    assert response.json()["currentReasonCodes"] == [] and response.json()["reasonCodes"] == ["policy"]
    assert _erroneous() == set() and _rows("SELECT * FROM patient_erroneous_marks") == []
    assert (_rows("SELECT * FROM patient_duplicate_reviews"), _rows("SELECT * FROM patient_duplicate_candidates")) == before
    assert _med_digest(a, b) == digest and _audit("patient_marked_erroneous") == []


def test_mark_is_refused_when_signals_changed_but_still_overlap(client):
    """Признаки изменились, но совпадение осталось — решение все равно принимается
    только по актуальным признакам: нужен повторный скан."""
    ids, a, b, cid, dev, deputy = _setup(client)
    get_conn().execute("UPDATE patients SET name = 'Синтетик Первый', age = 50 WHERE id IN (?, ?)", (a, b))
    get_conn().commit()
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "SIGNALS_CHANGED"
    assert set(response.json()["currentReasonCodes"]) >= {"policy", "name_age"}
    _scan(dev)                                                  # повторный скан фиксирует актуальные признаки
    assert _mark(deputy, cid, b).status_code == 200


@pytest.mark.parametrize("repeat", range(3))
def test_policy_change_between_view_and_mark(client, repeat):
    """Полис меняется после просмотра кандидата, пока пометка ждет блокировку записи."""
    ids, a, b, cid, dev, deputy = _setup(client)
    version = _detail(deputy, cid).json()["version"]           # просмотр: признаки еще совпадают
    response, waited = _while_locked(lambda: _mark(deputy, cid, b, version=version),
                                     "UPDATE patients SET policy_number = ? WHERE id = ?", (Y, b))
    assert waited and response.status_code == 409 and response.json()["code"] == "SIGNALS_CHANGED"
    assert _erroneous() == set() and _rows("SELECT * FROM patient_erroneous_marks") == []


def test_pair_signals_mark_after_restored_match_and_unmark_path_unaffected(client):
    ids, a, b, cid, dev, deputy = _setup(client)
    get_conn().execute("UPDATE patients SET policy_number = ? WHERE id = ?", (Y, b))
    get_conn().commit()
    assert _mark(deputy, cid, b).status_code == 409
    get_conn().execute("UPDATE patients SET policy_number = ? WHERE id = ?", (X, b))
    get_conn().commit()
    mark_id = _mark(deputy, cid, b).json()["markId"]          # совпадение восстановлено — пометка возможна
    assert _unmark(deputy, b, mark_id).status_code == 200      # снятие пометки от признаков не зависит


# ================================================================ review fix 2: registry pagination

def test_candidates_after_the_first_200_are_reachable(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    ids = _world()
    conn = get_conn()
    for i in range(205):                                        # 205 пар с разными полисами
        policy = f"77000000009{i:05d}"
        for owner, suffix in ((ids["doc.a"], "А"), (ids["doc.b"], "Б")):
            store.create_patient(conn, PatientCreateIn(**make_patient_payload(
                name=f"Синтетик Страниц {i:03d} {suffix}", policyNumber=policy, treatingDoctorUserId=owner,
                phone="", events=[])), owner)
    conn.commit()
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    _scan(dev)
    seen = []
    for offset in (0, 50, 100, 150, 200):
        page = deputy.get(f"/api/duplicates/candidates?state=open&limit=50&offset={offset}").json()
        assert page["total"] == 205 and page["offset"] == offset
        seen += [item["id"] for item in page["items"]]
    assert len(seen) == len(set(seen)) == 205
    last = deputy.get("/api/duplicates/candidates?state=open&limit=200&offset=200").json()
    assert len(last["items"]) == 5
    assert all(_detail(deputy, item["id"]).status_code == 200 for item in last["items"])
    assert deputy.get("/api/duplicates/candidates?state=resolved&limit=50&offset=0").json()["total"] == 0


def test_registry_client_pages_with_filters():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = js.index("    /* ========================================= ВОЗМОЖНЫЕ ДУБЛИ ПАЦИЕНТОВ */")
    section = js[start:js.index("    /* =========================================", start + 10)]
    assert "DUPLICATE_PAGE_SIZE" in section and "params.set('offset'" in section
    assert "duplicatesPrevBtn" in section and "duplicatesNextBtn" in section and "data.total" in section
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="duplicatesPrevBtn"' in html and 'id="duplicatesNextBtn"' in html and 'id="duplicatesPageInfo"' in html


def test_current_signals_list_every_matching_code():
    """Регрессия: текущие признаки пары перечисляются полностью (раньше — только первый)."""
    from backend.app.services.duplicate_review import pair_signals
    a = {"name": "Синтетик Первый", "age": 50, "phone": "+7 (999) 111-22-33", "policy_number": X,
         "diagnosis": "C34.1", "nosology": "lung34"}
    b = dict(a, policy_number="7700 0000 0005 1001", phone="8 999 111 22 33")
    assert pair_signals(a, b) == ["policy", "name_age", "name_diagnosis", "name_nosology", "phone"]
