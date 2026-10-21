"""WP2.2-B: отдельная ручная отметка решения ОК.

Ручная отметка решения ОК ≠ перевод этапа ≠ подписанное решение председателя.
Проверяется состояние SQLite (events, patients, history_entries, audit_events,
oncology_consilium_appointments), а не только коды ответа. Все данные синтетические,
база — временная (фикстура client)."""
import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

from .conftest import (
    DECISION_DOCTOR_USERNAME,
    DEV_USERNAME,
    PROJECT_ROOT,
    create_account,
    create_department,
    decision_doctor_id,
    get_conn,
    login,
    make_patient_payload,
    mark_decision,
    stage_body,
    unsigned_import,
    LEGACY_CONSILIUM_DATES,
    consilium_grid,
)
from .test_consilium_appointments import appointment_payload
from .test_consilium_security import DECISION as SIGN_DECISION

DECISION_COLUMNS = ("consilium_decision", "consilium_decision_by_user_id", "consilium_decision_at",
                    "consilium_decision_source")
FACT_COLUMNS = ("actual_date", "actual_time", "matched_plan", "set_at", "date", "time", "no_plan")
V20_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp22a_v20_synthetic.sql"


# ---------- вспомогательное ----------

def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _row(event_id):
    return dict(get_conn().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())


def _snapshot(pid):
    conn = get_conn()
    return {
        "patient": tuple(conn.execute("SELECT stage, updated_at FROM patients WHERE id = ?", (pid,)).fetchone()),
        "history": [tuple(r) for r in conn.execute(
            "SELECT id, seq, stage, entered_at, checklist_json, note FROM history_entries WHERE patient_id = ? ORDER BY seq",
            (pid,))],
        "events": [tuple(r) for r in conn.execute(
            """SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id
               WHERE h.patient_id = ? ORDER BY h.seq, e.seq""", (pid,))],
        "decision_audit": conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'consilium_decision_recorded' AND patient_id = ?",
            (pid,)).fetchone()[0],
        "consilium": [tuple(r) for r in conn.execute(
            "SELECT * FROM oncology_consilium_appointments WHERE patient_id = ? ORDER BY id", (pid,))],
    }


def _body(card, event, decision="treatment_strategy_defined", **over):
    return {"decision": decision, "version": event["version"], "expectedStage": card["stage"],
            "expectedHistoryEntryId": card["history"][-1]["id"], **over}


def _post(client, pid, event_id, body):
    return client.post(f"/api/patients/{pid}/events/{event_id}/consilium-decision", json=body, headers=client.csrf)


def _ok_patient(client, *, events=None, doctor_id=None):
    """Пациент на этапе ОК, лечащий врач — вымышленный врач отметок (создает разработчик)."""
    return _create(client, stage="consult", events=events or [{"date": "2026-08-20", "time": "10:00"}],
                   treatingDoctorUserId=doctor_id or decision_doctor_id())


def _as(client, username):
    assert login(client, username).status_code == 200


def _fail_audit(event_type):
    conn = get_conn()
    conn.execute(f"""CREATE TRIGGER wp22b_fail_audit BEFORE INSERT ON audit_events
                     WHEN NEW.event_type = '{event_type}'
                     BEGIN SELECT RAISE(ABORT, 'audit storage unavailable'); END""")
    conn.commit()


def _restore_audit():
    conn = get_conn()
    conn.execute("DROP TRIGGER IF EXISTS wp22b_fail_audit")
    conn.commit()


# ---------- 1–3. Успешная отметка, без перевода этапа, явный выбор события ----------

def test_01_manual_decision_is_recorded_with_server_author_time_and_source(auth_client):
    p = _ok_patient(auth_client)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    before = _row(ev["id"])
    r = _post(auth_client, p["id"], ev["id"], _body(card, ev, "treatment_strategy_changed"))
    assert r.status_code == 200, r.text
    after = _row(ev["id"])
    assert after["consilium_decision"] == "treatment_strategy_changed"
    assert after["consilium_decision_by_user_id"] == decision_doctor_id()
    assert after["consilium_decision_source"] == "manual"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", after["consilium_decision_at"])
    assert after["version"] == before["version"] + 1
    out = r.json()["history"][0]["events"][0]
    assert out["consiliumDecisionSource"] == "manual" and out["consiliumDecisionByName"] == "Отметкин Врач Решениевич"
    assert out["consiliumDecisionAt"] == after["consilium_decision_at"]


def test_02_decision_does_not_transition_the_patient(auth_client):
    p = _ok_patient(auth_client)
    before = _snapshot(p["id"])
    assert mark_decision(auth_client, p["id"]).status_code == 200
    after = _snapshot(p["id"])
    assert after["patient"][0] == "consult" and after["history"] == before["history"]


def test_03_event_is_chosen_explicitly_not_the_last_one(auth_client):
    p = _ok_patient(auth_client, events=[{"date": "2026-08-20"}, {"date": "2026-08-27"}])
    r = mark_decision(auth_client, p["id"], "dispensary_observation", idx=0)
    assert r.status_code == 200, r.text
    first, last = _card(auth_client, p["id"])["history"][0]["events"]
    assert first["consiliumDecision"] == "dispensary_observation" and last["consiliumDecision"] is None
    assert mark_decision(auth_client, p["id"], "treatment_strategy_defined", idx=1).status_code == 200
    assert _card(auth_client, p["id"])["history"][0]["events"][1]["consiliumDecision"] == "treatment_strategy_defined"


# ---------- 4–8. Чужой пациент, другой маршрут, удаленное событие, версия, повтор ----------

def test_04_event_of_another_patient_is_not_found_and_nothing_changes(auth_client):
    a = _ok_patient(auth_client)
    b = _ok_patient(auth_client)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card_a, ev_b = _card(auth_client, a["id"]), _card(auth_client, b["id"])["history"][0]["events"][0]
    before_a, before_b = _snapshot(a["id"]), _snapshot(b["id"])
    r = _post(auth_client, a["id"], ev_b["id"], _body(card_a, ev_b))
    assert r.status_code == 404 and r.json()["detail"] == "Event not found"
    assert _snapshot(a["id"]) == before_a and _snapshot(b["id"]) == before_b


def test_05_event_of_a_past_route_entry_or_other_stage_is_refused(auth_client):
    p = _ok_patient(auth_client)
    old_event = _card(auth_client, p["id"])["history"][0]["events"][0]
    # Пациент уходит с ОК и возвращается: событие прошлой записи ОК — не текущий этап.
    for target in ("exam", "consult"):
        assert auth_client.post(f"/api/patients/{p['id']}/stage", json=stage_body(auth_client, p["id"], {
            "newStage": target, "lastEventUpdate": None}), headers=auth_client.csrf).status_code == 200
    auth_client.post(f"/api/patients/{p['id']}/history/2/events", json={"expectedEventIds": []}, headers=auth_client.csrf)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    before = _snapshot(p["id"])
    r = _post(auth_client, p["id"], old_event["id"], _body(card, old_event))
    assert r.status_code == 409 and r.json()["code"] == "EVENT_NOT_IN_CURRENT_STAGE"
    # Устаревшая запись маршрута в запросе.
    current = card["history"][-1]["events"][0]
    r = _post(auth_client, p["id"], current["id"], _body(card, current, expectedHistoryEntryId=card["history"][0]["id"]))
    assert r.status_code == 409 and r.json()["code"] == "STALE_STAGE"
    assert _snapshot(p["id"]) == before
    # Пациент не на этапе ОК.
    _as(auth_client, DEV_USERNAME)
    q = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}], treatingDoctorUserId=decision_doctor_id())
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    qc = _card(auth_client, q["id"])
    r = _post(auth_client, q["id"], qc["history"][0]["events"][0]["id"], _body(qc, qc["history"][0]["events"][0]))
    assert r.status_code == 409 and r.json()["code"] == "NOT_OK_STAGE"


def test_06_deleted_event_is_refused(auth_client):
    p = _ok_patient(auth_client, events=[{"date": "2026-08-20"}, {"date": "2026-08-27"}])
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    assert auth_client.delete(f"/api/patients/{p['id']}/events/{ev['id']}?version={ev['version']}",
                              headers=auth_client.csrf).status_code == 200
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    deleted = _row(ev["id"])
    before = _snapshot(p["id"])
    r = _post(auth_client, p["id"], ev["id"], _body(card, {"version": deleted["version"]}))
    assert r.status_code == 409 and r.json()["code"] == "EVENT_DELETED"
    assert _snapshot(p["id"]) == before


def test_07_stale_event_version_is_refused(auth_client):
    p = _ok_patient(auth_client)
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    r = auth_client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                          json={"field": "time", "value": "11:00", "version": ev["version"]}, headers=auth_client.csrf)
    assert r.status_code == 200
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    before = _snapshot(p["id"])
    r = _post(auth_client, p["id"], ev["id"], _body(card, ev))
    assert r.status_code == 409 and r.json()["code"] == "STALE_EVENT_VERSION"
    assert _snapshot(p["id"]) == before


def test_08_second_mark_is_refused_and_keeps_the_first_decision(auth_client):
    p = _ok_patient(auth_client)
    assert mark_decision(auth_client, p["id"], "treatment_strategy_defined").status_code == 200
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    first = _row(ev_id)
    before = _snapshot(p["id"])
    for decision in ("treatment_strategy_defined", "dispensary_observation"):
        r = mark_decision(auth_client, p["id"], decision)
        assert r.status_code == 409 and r.json()["code"] == "DECISION_ALREADY_RECORDED"
    assert _snapshot(p["id"])["events"] == before["events"]
    assert {c: _row(ev_id)[c] for c in DECISION_COLUMNS} == {c: first[c] for c in DECISION_COLUMNS}


# ---------- 9. Два конкурентных запроса ----------

def test_09_concurrent_marks_record_exactly_one_decision(auth_client):
    from backend.app import store

    p = _ok_patient(auth_client)
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    results, barrier = [], threading.Barrier(2)

    def mark(decision):
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results.append(store.mark_consilium_decision(
                conn, p["id"], ev["id"], decision, ev["version"], expected_stage="consult",
                expected_history_entry_id=card["history"][0]["id"], user_id=decision_doctor_id()) is not None)
        except store.DecisionConflict as e:
            results.append(e.code)
        finally:
            conn.close()

    threads = [threading.Thread(target=mark, args=(d,)) for d in ("treatment_strategy_defined", "dispensary_observation")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results)) in (["DECISION_ALREADY_RECORDED", "True"], ["STALE_EVENT_VERSION", "True"])
    row = _row(ev["id"])
    assert row["consilium_decision"] in ("treatment_strategy_defined", "dispensary_observation")
    assert row["version"] == ev["version"] + 1


# ---------- 10, 24. Сбой аудита — откат ----------

def test_10_24_audit_failure_rolls_the_decision_back(auth_client):
    p = _ok_patient(auth_client)
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    before = _snapshot(p["id"])
    _fail_audit("consilium_decision_recorded")
    try:
        r = mark_decision(auth_client, p["id"])
        assert r.status_code == 503 and "Решение ОК не записано" in r.json()["detail"]
        # Решение, автор, время, источник, версия, факт, этап, история — без изменений.
        assert _snapshot(p["id"]) == before
    finally:
        _restore_audit()
    assert mark_decision(auth_client, p["id"]).status_code == 200
    audit = get_conn().execute(
        "SELECT detail, actor_user_id, actor_role, created_at FROM audit_events "
        "WHERE event_type = 'consilium_decision_recorded' AND patient_id = ?", (p["id"],)).fetchall()
    assert len(audit) == 1
    assert audit[0]["detail"] == f"event_id={ev_id};history=0;source=manual"
    assert audit[0]["actor_user_id"] == decision_doctor_id() and audit[0]["actor_role"] == "doctor"
    assert audit[0]["created_at"]
    text = json.dumps([dict(r) for r in get_conn().execute(
        "SELECT * FROM audit_events WHERE patient_id = ?", (p["id"],))], ensure_ascii=False)
    for secret in ("treatment_strategy", "Тестов", "C34", "+7", "Отметкин"):
        assert secret not in text


# ---------- 11. Каждая роль ----------

@pytest.fixture()
def staff(client):
    d1, d2 = create_department("Отделение А"), create_department("Отделение Б")
    ids = {
        "dev": create_account(DEV_USERNAME),
        "doc_a": create_account("role.doc.a", "doctor", "Врачев А", department_ids=[d1]),
        "doc_b": create_account("role.doc.b", "doctor", "Врачев Б", department_ids=[d2]),
        "head_a": create_account("role.head.a", "department_head", "Заведуев А", department_ids=[d1]),
        "head_b": create_account("role.head.b", "department_head", "Заведуев Б", department_ids=[d2]),
        "chair": create_account("role.chair", "consilium_chair", "Председателев"),
        "deputy": create_account("role.deputy", "deputy_chief_oncology", "Заместителев"),
        "caop": create_account("role.caop", "caop_head", "Цаопов"),
    }
    return ids


def _role_case(client, staff, actor, owner):
    """Пациент (лечащий врач owner) на этапе ОК, отметка от имени actor."""
    if owner == "chair":
        # WP2.4-A (R6): председатель новые карточки не создает. «Свой» пациент
        # председателя — синтетическая legacy-карточка, созданная до ограничения.
        _as(client, "role.chair")
        assert client.post("/api/patients", json=make_patient_payload(stage="consult"),
                           headers=client.csrf).status_code == 403
        from backend.app import store
        from backend.app.models import PatientCreateIn
        pid = store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(
            stage="consult", events=[{"date": "2026-08-20"}])), staff["chair"])
        p = {"id": pid}
    else:
        _as(client, DEV_USERNAME)
        p = _create(client, stage="consult", events=[{"date": "2026-08-20"}], treatingDoctorUserId=staff[owner])
    before = _snapshot(p["id"])
    username = {"dev": DEV_USERNAME}.get(actor, f"role.{actor.replace('_', '.')}")
    _as(client, username)
    _as(client, DEV_USERNAME)
    card = _card(client, p["id"])
    ev = card["history"][0]["events"][0]
    _as(client, username)
    r = _post(client, p["id"], ev["id"], _body(card, ev))
    return r, p["id"], before


@pytest.mark.parametrize("actor,owner,expected", [
    ("doc_a", "doc_a", 200),        # лечащий врач
    ("doc_b", "doc_a", 404),        # врач другого отделения — пациента не видит
    ("head_a", "doc_a", 200),       # заведующий своего отделения
    ("head_b", "doc_a", 404),       # заведующий другого отделения
    ("chair", "chair", 200),        # председатель — свой пациент
    ("chair", "doc_a", 404),        # председатель — не свой пациент (область не расширяется)
    ("dev", "doc_a", 403),          # разработчик видит всех, но медицинское решение не ставит
    ("deputy", "doc_a", 403),       # заместитель — только чтение
    ("caop", "doc_a", 403),         # ЦАОП — только чтение
])
def test_11_role_matrix(client, staff, actor, owner, expected):
    r, pid, before = _role_case(client, staff, actor, owner)
    assert r.status_code == expected, (actor, owner, r.status_code, r.text)
    after = _snapshot(pid)
    if expected == 200:
        row = [e for e in after["events"]][0]
        conn = get_conn()
        stored = conn.execute("SELECT consilium_decision_by_user_id FROM events WHERE consilium_decision IS NOT NULL "
                              "AND history_entry_id IN (SELECT id FROM history_entries WHERE patient_id = ?)",
                              (pid,)).fetchone()[0]
        assert stored == staff[actor] and row
    else:
        assert after == before


def test_11_permission_flags_follow_the_rule(client, staff):
    expected = {"dev": False, "doc_a": True, "head_a": True, "chair": True, "deputy": False, "caop": False}
    for actor, flag in expected.items():
        username = {"dev": DEV_USERNAME}.get(actor, f"role.{actor.replace('_', '.')}")
        _as(client, username)
        assert client.get("/api/auth/me").json()["user"]["permissions"]["markConsiliumDecision"] is flag, actor


def test_11_authentication_and_csrf_are_required(auth_client):
    p = _ok_patient(auth_client)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    url = f"/api/patients/{p['id']}/events/{ev['id']}/consilium-decision"
    assert auth_client.post(url, json=_body(card, ev)).status_code == 403          # без CSRF
    auth_client.cookies.clear()
    assert auth_client.post(url, json=_body(card, ev), headers=auth_client.csrf).status_code in (401, 403)
    assert _row(ev["id"])["consilium_decision"] is None


@pytest.mark.parametrize("change", [
    {"decision": "new_medical_code"},
    {"decisionByUserId": "someone"},
    {"consiliumDecisionAt": "2020-01-01T00:00:00"},
    {"source": "legacy"},
    {"version": 0},
    {"expectedStage": "nonexistent"},
])
def test_invalid_or_extended_request_is_422(auth_client, change):
    p = _ok_patient(auth_client)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    before = _snapshot(p["id"])
    r = _post(auth_client, p["id"], ev["id"], {**_body(card, ev), **change})
    assert r.status_code == 422
    assert _snapshot(p["id"]) == before


# ---------- 12–14. Факт и подписанное решение не меняются ----------

@pytest.mark.parametrize("event", [
    {"date": "2026-08-20", "time": "10:00"},
    {"date": "2026-08-20", "time": "10:00", "actualDate": "2026-08-20", "actualTime": "10:05", "matchedPlan": False},
])
def test_12_13_fact_date_time_and_matched_plan_stay_unchanged(auth_client, event):
    p = _ok_patient(auth_client, events=[event])
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    before = _row(ev_id)
    assert mark_decision(auth_client, p["id"]).status_code == 200
    after = _row(ev_id)
    assert {c: after[c] for c in FACT_COLUMNS} == {c: before[c] for c in FACT_COLUMNS}


def test_12_13_time_written_without_date_stays(auth_client):
    p = _ok_patient(auth_client)
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    auth_client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                      json={"field": "actualTime", "value": "09:45", "version": ev["version"]}, headers=auth_client.csrf)
    before = _row(ev["id"])
    assert mark_decision(auth_client, p["id"]).status_code == 200
    after = _row(ev["id"])
    assert (after["actual_date"], after["actual_time"], after["matched_plan"], after["set_at"]) == (
        before["actual_date"], before["actual_time"], before["matched_plan"], before["set_at"])


def test_14_signed_chair_decision_is_untouched_and_not_created(auth_client):
    create_account("chair.wp22b", "consilium_chair", "Председателев Тест")
    p = _ok_patient(auth_client)
    consilium_grid(LEGACY_CONSILIUM_DATES)  # WP2.3-B2: запись только в слот председателя
    booked = auth_client.post("/api/consilium/appointments", json=appointment_payload(p["id"]), headers=auth_client.csrf)
    assert booked.status_code == 201, booked.text
    aid = booked.json()["id"]
    _as(auth_client, "chair.wp22b")
    for action, body in (("review", {}), ("sign", SIGN_DECISION)):
        r = auth_client.post(f"/api/consilium/appointments/{aid}/{action}", json=body, headers=auth_client.csrf)
        assert r.status_code == 200, r.text
    _as(auth_client, DEV_USERNAME)
    conn = get_conn()
    signed_before = dict(conn.execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone())
    count_before = conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments").fetchone()[0]
    assert mark_decision(auth_client, p["id"], "dispensary_observation").status_code == 200
    assert dict(conn.execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone()) == signed_before
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments").fetchone()[0] == count_before


# ---------- 15. Отдельный перевод этапа ----------

def test_15_transition_neither_writes_nor_changes_a_decision(auth_client):
    p = _ok_patient(auth_client)
    assert mark_decision(auth_client, p["id"], "treatment_strategy_defined").status_code == 200
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    decided = {c: _row(ev_id)[c] for c in DECISION_COLUMNS}
    # Перевод с решением в теле — явный отказ, ничего не меняется.
    before = _snapshot(p["id"])
    r = auth_client.post(f"/api/patients/{p['id']}/stage", json=stage_body(auth_client, p["id"], {
        "newStage": "treatment-prep",
        "lastEventUpdate": {"actualDate": "2026-08-20", "consiliumDecision": "dispensary_observation"}}),
        headers=auth_client.csrf)
    assert r.status_code == 422 and r.json()["code"] == "DECISION_SEPARATE_OPERATION"
    assert _snapshot(p["id"]) == before
    # Обычный перевод (на любой этап — решение маршрут не блокирует) решение не трогает.
    r = auth_client.post(f"/api/patients/{p['id']}/stage", json=stage_body(auth_client, p["id"], {
        "newStage": "dispensary-observation", "lastEventUpdate": {"actualDate": "2026-08-20"}}), headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    assert {c: _row(ev_id)[c] for c in DECISION_COLUMNS} == decided
    assert not get_conn().execute(
        "SELECT 1 FROM events e JOIN history_entries h ON h.id = e.history_entry_id "
        "WHERE h.patient_id = ? AND h.seq > 0 AND e.consilium_decision IS NOT NULL", (p["id"],)).fetchone()


def test_15_wp22a_fact_protection_still_applies(auth_client):
    p = _ok_patient(auth_client, events=[{"date": "2026-08-20", "time": "10:00", "actualDate": "2026-08-20",
                                          "actualTime": "10:00", "matchedPlan": True}])
    assert mark_decision(auth_client, p["id"]).status_code == 200
    before = _snapshot(p["id"])
    r = auth_client.post(f"/api/patients/{p['id']}/stage", json=stage_body(auth_client, p["id"], {
        "newStage": "treatment-prep", "lastEventUpdate": {"actualDate": "2026-08-21"}}), headers=auth_client.csrf)
    assert r.status_code == 409 and r.json()["code"] == "FACT_CONFLICT"
    assert _snapshot(p["id"]) == before


# ---------- 16–19. Legacy, миграция v20 → v21, повторная инициализация, резервные копии ----------

def _v20_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(V20_FIXTURE.read_text(encoding="utf-8"))
    conn.close()
    return path


def _events_by_id(conn):
    conn.row_factory = sqlite3.Row
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM events")}


def test_16_17_migration_marks_old_decisions_legacy_without_inventing_anything(tmp_path):
    from backend.app import config, db
    path = _v20_db(tmp_path / "v20.db")
    raw = sqlite3.connect(str(path))
    before = _events_by_id(raw)
    before_history = [tuple(r) for r in raw.execute("SELECT * FROM history_entries ORDER BY patient_id, seq")]
    raw.close()
    assert sum(1 for e in before.values() if e["consilium_decision"]) == 2
    assert any(e["consilium_decision"] and e["deleted_at"] for e in before.values())   # решение в удаленном событии

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    # WP2.3-B1: цепочка доводит v20 до текущей схемы (v22), а не только до v21.
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION >= 21
    after = _events_by_id(conn)
    assert after.keys() == before.keys()
    for eid, old in before.items():
        new = after[eid]
        assert {k: new[k] for k in old} == old, eid          # прежние колонки — без изменений (версия, факт, удаление)
        if old["consilium_decision"]:
            assert (new["consilium_decision_source"], new["consilium_decision_by_user_id"], new["consilium_decision_at"]) == (
                "legacy", None, None)
        else:
            assert (new["consilium_decision_source"], new["consilium_decision_by_user_id"], new["consilium_decision_at"]) == (
                None, None, None)
    assert [tuple(r) for r in conn.execute("SELECT * FROM history_entries ORDER BY patient_id, seq")] == before_history
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"trg_events_consilium_decision_immutable", "trg_events_consilium_decision_attributed_update",
            "trg_events_consilium_decision_attributed_insert", "trg_events_archived_not_updated"} <= triggers
    assert maintenance_check(conn)
    conn.close()


def maintenance_check(conn):
    return [r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"] and not conn.execute(
        "PRAGMA foreign_key_check").fetchall()


def test_18_repeated_init_changes_nothing(tmp_path):
    from backend.app import db
    path = _v20_db(tmp_path / "v20.db")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    once = (_events_by_id(conn), sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")))
    db.init_db(conn)
    db.init_db(conn)
    twice = (_events_by_id(conn), sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")))
    assert once == twice
    conn.close()


def test_16_19_v20_backup_is_recognised_restored_and_keeps_legacy_decisions(auth_client, tmp_path, monkeypatch):
    from backend.app import maintenance
    from .test_restore_migration import TEST_BACKUP_KEY, _live, _v9_backup
    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp22a-v20.cbk", header=20, fixture=V20_FIXTURE)
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("wp22a-de128d4", 20)
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["migrated_from_schema_version"] == 20
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT consilium_decision, consilium_decision_source, consilium_decision_by_user_id, consilium_decision_at, "
        "deleted_at IS NOT NULL AS archived, actual_date FROM events WHERE consilium_decision IS NOT NULL "
        "ORDER BY consilium_decision")]
    assert rows == [
        {"consilium_decision": "dispensary_observation", "consilium_decision_source": "legacy",
         "consilium_decision_by_user_id": None, "consilium_decision_at": None, "archived": 1, "actual_date": "2026-08-22"},
        {"consilium_decision": "treatment_strategy_defined", "consilium_decision_source": "legacy",
         "consilium_decision_by_user_id": None, "consilium_decision_at": None, "archived": 0, "actual_date": "2026-08-20"},
    ]
    # Восстановленное legacy-решение защищено так же, как новое.
    with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_immutable"):
        conn.execute("UPDATE events SET consilium_decision = 'treatment_strategy_changed' WHERE consilium_decision IS NOT NULL")
    conn.close()


def test_19_current_backup_keeps_manual_author_and_time(auth_client, tmp_path, monkeypatch):
    from backend.app import config, db, maintenance
    monkeypatch.setenv("CLINIC_BACKUP_KEY", "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE")
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    p = _ok_patient(auth_client)
    assert mark_decision(auth_client, p["id"]).status_code == 200
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    decided = {c: _row(ev_id)[c] for c in DECISION_COLUMNS}
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "current.cbk")
    assert maintenance.validate_backup(backup)["source_build"] == "current"
    db.reset_singleton_for_tests()
    maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    db.reset_singleton_for_tests()
    assert {c: _row(ev_id)[c] for c in DECISION_COLUMNS} == decided


def test_19_export_keeps_format_and_import_marks_decisions_as_import(auth_client):
    p = _ok_patient(auth_client)
    assert mark_decision(auth_client, p["id"]).status_code == 200
    r = auth_client.get("/api/export", headers=auth_client.csrf)
    assert r.status_code == 200
    exported = [x for x in json.loads(r.text)["patients"] if x["id"] == p["id"]][0]
    ev = exported["history"][0]["events"][0]
    assert ev["consiliumDecision"] == "treatment_strategy_defined"
    assert not any(k.startswith("consiliumDecision") and k != "consiliumDecision" for k in ev)
    assert "version" not in ev
    # Новая карточка из файла: решение сохраняется, но как import — без автора и времени.
    doc = {"schemaVersion": 21, "patients": [{
        **{k: v for k, v in make_patient_payload(name="Импортов Решений", stage="consult").items() if k != "events"},
        "history": [{"stage": "consult", "enteredAt": "2026-08-01T00:00:00+00:00",
                     "events": [{"date": "2026-08-20", "consiliumDecision": "dispensary_observation"}]}],
    }]}
    raw = json.dumps(doc, ensure_ascii=False)
    assert unsigned_import(auth_client, raw, "add").status_code == 428      # без подтверждения переноса
    r = unsigned_import(auth_client, raw, "add", historical_decisions=1)
    assert r.status_code == 200, r.text
    row = dict(get_conn().execute(
        "SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id JOIN patients p ON p.id = h.patient_id "
        "WHERE p.name = 'Импортов Решений'").fetchone())
    assert (row["consilium_decision"], row["consilium_decision_source"], row["consilium_decision_by_user_id"],
            row["consilium_decision_at"]) == ("dispensary_observation", "import", None, None)


# ---------- 20. Совместимость действующего интерфейса ----------

def _js_function(name):
    source = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = source.index(f"    async {name}(")
    end = source.index("\n    },\n", start)
    return source[start:end]


def test_20_old_client_no_longer_sends_a_decision_with_a_transition():
    change_stage = _js_function("changeStage")
    assert "consiliumDecision" not in change_stage
    mark = _js_function("markConsiliumDecision")
    assert "/consilium-decision" in mark and "expectedHistoryEntryId" in mark and "version: target.version" in mark
    assert "ui.choose" in mark                       # несколько событий — явный выбор
    # Защита WP2.2-A: сохраненное фактическое время не заменяется плановым.
    assert "const storedTime = lastEvent.actualTime || '';" in change_stage
    assert "actualTime: storedTime" in change_stage and "storedTime || lastEvent.time" in change_stage


@pytest.mark.parametrize("stored,plan_time,client_time,expected", [
    ("09:45", "10:00", "09:45", 200),    # клиент присылает сохраненное время — перевод проходит
    ("09:45", "", "09:45", 200),         # планового времени нет
    ("", "10:00", "10:00", 200),         # времени нет — клиент подставляет плановое, как раньше
    ("09:45", "10:00", "10:00", 409),    # прежнее поведение клиента — безопасный отказ сервера
])
def test_20_adapted_client_time_bodies(auth_client, stored, plan_time, client_time, expected):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20", "time": plan_time}])
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    if stored:
        auth_client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                          json={"field": "actualTime", "value": stored, "version": ev["version"]}, headers=auth_client.csrf)
    r = auth_client.post(f"/api/patients/{p['id']}/stage", json=stage_body(auth_client, p["id"], {
        "newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": client_time,
                                                   "matchedPlan": client_time == plan_time}}), headers=auth_client.csrf)
    assert r.status_code == expected, r.text
    if expected == 200:
        assert _row(ev["id"])["actual_time"] == (stored or client_time)


def test_20_choice_modal_is_present():
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="choiceModal"' in html and 'id="choiceSelect"' in html


# ---------- 21. Обход через создание пациента ----------

def test_21_patient_creation_cannot_carry_a_decision(auth_client):
    count = get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    events_count = get_conn().execute("SELECT COUNT(*) FROM events").fetchone()[0]
    r = auth_client.post("/api/patients", json=make_patient_payload(
        stage="consult", events=[{"date": "2026-08-20", "consiliumDecision": "treatment_strategy_defined"}]),
        headers=auth_client.csrf)
    assert r.status_code == 422 and "отдельной операцией" in r.json()["detail"]
    assert get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0] == count
    assert get_conn().execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_count


# ---------- 22. Удаление и восстановление события с решением ----------

def test_22_delete_and_restore_keep_the_decision_its_author_and_time(auth_client):
    p = _ok_patient(auth_client, events=[{"date": "2026-08-20"}, {"date": "2026-08-27"}])
    assert mark_decision(auth_client, p["id"], idx=0).status_code == 200
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    decided = {c: _row(ev["id"])[c] for c in DECISION_COLUMNS}
    r = auth_client.delete(f"/api/patients/{p['id']}/events/{ev['id']}?version={ev['version']}", headers=auth_client.csrf)
    assert r.status_code == 200
    row = _row(ev["id"])
    assert row["deleted_at"] and {c: row[c] for c in DECISION_COLUMNS} == decided
    deleted = auth_client.get(f"/api/patients/{p['id']}/events/deleted").json()["events"]
    assert deleted[0]["consiliumDecision"] == "treatment_strategy_defined"
    assert deleted[0]["consiliumDecisionByName"] == "Отметкин Врач Решениевич"
    # Отметить удаленное событие нельзя.
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, p["id"])
    r = _post(auth_client, p["id"], ev["id"], _body(card, {"version": row["version"]}))
    assert r.status_code == 409 and r.json()["code"] == "EVENT_DELETED"
    _as(auth_client, DEV_USERNAME)
    r = auth_client.post(f"/api/patients/{p['id']}/events/{ev['id']}/restore", json={"version": row["version"]},
                         headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    restored = _row(ev["id"])
    assert restored["deleted_at"] is None and {c: restored[c] for c in DECISION_COLUMNS} == decided
    # Повторная отметка восстановленного события не обходит однократность.
    r = mark_decision(auth_client, p["id"], "dispensary_observation", idx=0)
    assert r.status_code == 409 and r.json()["code"] == "DECISION_ALREADY_RECORDED"
    audit = [r["detail"] for r in get_conn().execute(
        "SELECT detail FROM audit_events WHERE patient_id = ? AND event_type = 'patient_event_deleted'", (p["id"],))]
    assert audit and "decision=1" in audit[0]


# ---------- 23. Прямое обновление SQL ----------

def test_23_direct_sql_cannot_silently_replace_or_forge_a_decision(auth_client):
    p = _ok_patient(auth_client, events=[{"date": "2026-08-20"}, {"date": "2026-08-27"}])
    assert mark_decision(auth_client, p["id"], idx=0).status_code == 200
    decided_id, free_id = [e["id"] for e in _card(auth_client, p["id"])["history"][0]["events"]]
    conn = get_conn()
    for sql in (
        "UPDATE events SET consilium_decision = 'dispensary_observation' WHERE id = ?",
        "UPDATE events SET consilium_decision = NULL WHERE id = ?",
        "UPDATE events SET consilium_decision_by_user_id = NULL WHERE id = ?",
        "UPDATE events SET consilium_decision_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        "UPDATE events SET consilium_decision_source = 'legacy' WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_immutable"):
            conn.execute(sql, (decided_id,))
        conn.rollback()
    for sql in (
        "UPDATE events SET consilium_decision = 'dispensary_observation' WHERE id = ?",
        "UPDATE events SET consilium_decision = 'dispensary_observation', consilium_decision_source = 'legacy' WHERE id = ?",
        "UPDATE events SET consilium_decision_source = 'manual' WHERE id = ?",
        # Автор и время есть, источника нет (NULL-безопасная проверка триггера).
        "UPDATE events SET consilium_decision = 'dispensary_observation', consilium_decision_by_user_id = "
        "(SELECT id FROM users LIMIT 1), consilium_decision_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_unattributed"):
            conn.execute(sql, (free_id,))
        conn.rollback()
    entry = conn.execute("SELECT history_entry_id FROM events WHERE id = ?", (free_id,)).fetchone()[0]
    for columns, values in (
        ("consilium_decision", "'treatment_strategy_changed'"),
        ("consilium_decision, consilium_decision_by_user_id, consilium_decision_at",
         "'treatment_strategy_changed', (SELECT id FROM users LIMIT 1), '2026-01-01T00:00:00+00:00'"),
        ("consilium_decision, consilium_decision_source, consilium_decision_by_user_id",
         "'treatment_strategy_changed', 'legacy', (SELECT id FROM users LIMIT 1)"),
        ("consilium_decision, consilium_decision_source", "'treatment_strategy_changed', 'manual'"),
        ("consilium_decision_source", "'import'"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_unattributed"):
            conn.execute(f"INSERT INTO events (id, history_entry_id, seq, {columns}) VALUES ('forged', ?, 99, {values})",
                         (entry,))
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET consilium_decision_source = 'other' WHERE id = ?", (free_id,))
    conn.rollback()
    # Удаление и восстановление (изменение только отметки удаления) по-прежнему разрешены триггерами.
    assert _row(free_id)["consilium_decision"] is None
