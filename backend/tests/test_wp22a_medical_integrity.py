"""WP2.2-A: защита существующих медицинских фактов при переводе этапа и обязательный аудит.

Проверяются реальные строки SQLite до и после операции (patients.stage, events.actual_date,
events.actual_time, events.matched_plan, events.version, history_entries, audit_events), а не
только код ответа. База — временная, создается фикстурой client для каждого теста."""
import sqlite3
import threading

import pytest

from .conftest import checklist_state, DEV_USERNAME, create_account, decision_doctor_id, get_conn, login, make_patient_payload, mark_decision, stage_body
from .test_consilium_appointments import appointment_payload
from .test_consilium_security import DECISION, _make_state, _post


# ---------- вспомогательное ----------

def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _snapshot(pid):
    """Все, что не должно меняться при отказе: этап, история, события и журнал аудита."""
    conn = get_conn()
    try:
        return {
            "stage": conn.execute("SELECT stage, updated_at FROM patients WHERE id = ?", (pid,)).fetchone()[:],
            "history": [tuple(r) for r in conn.execute(
                "SELECT id, seq, stage, entered_at, checklist_json, note FROM history_entries WHERE patient_id = ? ORDER BY seq",
                (pid,))],
            "events": [tuple(r) for r in conn.execute(
                """SELECT e.id, e.seq, e.date, e.time, e.actual_date, e.actual_time, e.matched_plan, e.no_plan,
                          e.consilium_decision, e.set_at, e.deleted_at, e.version
                   FROM events e JOIN history_entries h ON h.id = e.history_entry_id
                   WHERE h.patient_id = ? ORDER BY h.seq, e.seq""", (pid,))],
            "audit": conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
            "notifications": [tuple(r) for r in conn.execute(
                "SELECT id, status FROM notifications WHERE patient_id = ? ORDER BY id", (pid,))],
        }
    finally:
        pass   # get_conn() — общее соединение теста, не закрывается


def _event_row(pid, occ=0, idx=0):
    conn = get_conn()
    try:
        return dict(conn.execute(
            """SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id
               WHERE h.patient_id = ? AND h.seq = ? AND e.deleted_at IS NULL ORDER BY e.seq LIMIT 1 OFFSET ?""",
            (pid, occ, idx)).fetchone())
    finally:
        pass   # get_conn() — общее соединение теста, не закрывается


def _stage(client, pid, body):
    return client.post(f"/api/patients/{pid}/stage", json=stage_body(client, pid, body), headers=client.csrf)


def _with_fact(client, stage="exam", date="2026-08-20", time="10:00", actual_date="2026-08-20", actual_time="10:00"):
    """Пациент, у последнего события текущего этапа которого факт уже записан (через правку события)."""
    p = _create(client, stage=stage, events=[{"date": date, "time": time}])
    ev = _card(client, p["id"])["history"][0]["events"][0]
    r = client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                     json={"field": "actualDate", "value": actual_date, "version": ev["version"]}, headers=client.csrf)
    assert r.status_code == 200, r.text
    if actual_time is not None:
        ev = r.json()["history"][0]["events"][0]
        r = client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                         json={"field": "actualTime", "value": actual_time, "version": ev["version"]}, headers=client.csrf)
        assert r.status_code == 200, r.text
    return p["id"]


def _fail_audit(event_type):
    """Сбой записи аудита в реальной SQLite: триггер прерывает вставку нужного типа."""
    conn = get_conn()
    try:
        conn.execute(f"""CREATE TRIGGER wp22a_fail_audit BEFORE INSERT ON audit_events
                         WHEN NEW.event_type = '{event_type}'
                         BEGIN SELECT RAISE(ABORT, 'audit storage unavailable'); END""")
        conn.commit()
    finally:
        pass   # get_conn() — общее соединение теста, не закрывается


def _restore_audit():
    conn = get_conn()
    try:
        conn.execute("DROP TRIGGER IF EXISTS wp22a_fail_audit")
        conn.commit()
    finally:
        pass   # get_conn() — общее соединение теста, не закрывается


# ---------- защита фактической даты и времени (A–I) ----------

def test_a_null_date_keeps_existing_fact(auth_client):
    pid = _with_fact(auth_client)
    before = _event_row(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": None}})
    assert r.status_code == 200, r.text
    after = _event_row(pid)
    for col in ("actual_date", "actual_time", "matched_plan", "set_at", "version"):
        assert after[col] == before[col], col
    assert get_conn().execute("SELECT stage FROM patients WHERE id = ?", (pid,)).fetchone()[0] == "consult"


def test_b_other_date_is_fact_conflict_without_any_change(auth_client):
    pid = _with_fact(auth_client)
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-25", "matchedPlan": True}})
    assert r.status_code == 409
    assert r.json()["code"] == "FACT_CONFLICT" and "данные не изменены" in r.json()["detail"]
    assert _snapshot(pid) == before


def test_c_same_date_and_time_keep_original_fact(auth_client):
    pid = _with_fact(auth_client)
    before = _event_row(pid)
    # Клиент присылает противоположный matchedPlan — сервер его не принимает.
    r = _stage(auth_client, pid, {"newStage": "consult",
                                  "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "10:00", "matchedPlan": False}})
    assert r.status_code == 200, r.text
    after = _event_row(pid)
    assert (after["actual_date"], after["actual_time"], after["matched_plan"]) == ("2026-08-20", "10:00", 1)
    assert after["set_at"] == before["set_at"] and after["version"] == before["version"]


def test_d_same_date_empty_time_does_not_erase_time(auth_client):
    pid = _with_fact(auth_client)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": ""}})
    assert r.status_code == 200, r.text
    assert _event_row(pid)["actual_time"] == "10:00"


@pytest.mark.parametrize("stored_time", ["10:00", None])
def test_e_other_time_is_fact_conflict(auth_client, stored_time):
    pid = _with_fact(auth_client, actual_time=stored_time)
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "11:30"}})
    assert r.status_code == 409 and r.json()["code"] == "FACT_CONFLICT"
    assert _snapshot(pid) == before


def test_f_first_fact_is_written_and_matched_plan_is_computed_by_server(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])
    r = _stage(auth_client, p["id"], {"newStage": "consult",
                                      "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "10:00", "matchedPlan": False}})
    assert r.status_code == 200, r.text
    row = _event_row(p["id"])
    assert (row["actual_date"], row["actual_time"], row["matched_plan"]) == ("2026-08-20", "10:00", 1)
    assert row["set_at"]
    q = _create(auth_client, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])
    r = _stage(auth_client, q["id"], {"newStage": "consult",
                                      "lastEventUpdate": {"actualDate": "2026-08-22", "actualTime": "", "matchedPlan": True}})
    assert r.status_code == 200
    assert _event_row(q["id"])["matched_plan"] == 0


def test_f_event_without_planned_date_keeps_matched_plan_undetermined(auth_client):
    p = _create(auth_client, stage="exam", events=[{"time": ""}])
    r = _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "matchedPlan": True}})
    assert r.status_code == 200, r.text
    assert _event_row(p["id"])["matched_plan"] is None


def test_f_stage_without_events_inserts_requested_fact_only(auth_client):
    p = _create(auth_client, stage="exam", events=[])
    r = _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "matchedPlan": True}})
    assert r.status_code == 200, r.text
    row = _event_row(p["id"])
    assert row["actual_date"] == "2026-08-20" and row["matched_plan"] is None


def test_server_never_invents_a_fact_date(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    r = _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {}})
    assert r.status_code == 200, r.text
    row = _event_row(p["id"])
    assert row["actual_date"] is None and row["matched_plan"] is None and row["version"] == 1


def test_time_without_date_is_rejected_without_change(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    before = _snapshot(p["id"])
    r = _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualTime": "10:00"}})
    assert r.status_code == 422
    assert _snapshot(p["id"]) == before


def test_g_concurrent_transitions_with_facts_apply_once(auth_client):
    from backend.app import store
    from backend.app.models import EventIn

    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    card = _card(auth_client, p["id"])
    entry_id, ev = card["history"][-1]["id"], card["history"][-1]["events"][0]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    results, barrier = [], threading.Barrier(2)

    def move(target, fact):
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results.append(store.change_stage(conn, p["id"], target, EventIn(actualDate=fact),
                                              expected_event_id=ev["id"], expected_event_version=ev["version"],
                                              expected_stage="exam", expected_history_entry_id=entry_id))
        except store.EventConflict:
            results.append("conflict")
        finally:
            conn.close()

    threads = [threading.Thread(target=move, args=a) for a in (("consult", "2026-08-20"), ("appointment", "2026-08-21"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results)) == ["True", "conflict"]
    conn = get_conn()
    stage = conn.execute("SELECT stage FROM patients WHERE id = ?", (p["id"],)).fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", (p["id"],)).fetchone()[0] == 2
    row = _event_row(p["id"])
    # Факт записан ровно тем переводом, который прошел, и только один раз.
    assert (stage, row["actual_date"]) in (("consult", "2026-08-20"), ("appointment", "2026-08-21"))
    assert row["version"] == ev["version"] + 1


def test_h_stale_event_version_is_rejected_without_change(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    card = _card(auth_client, p["id"])
    ev = card["history"][0]["events"][0]
    patched = auth_client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                                json={"field": "time", "value": "12:00", "version": ev["version"]}, headers=auth_client.csrf)
    assert patched.status_code == 200
    before = _snapshot(p["id"])
    r = auth_client.post(f"/api/patients/{p['id']}/stage", json={
        "newStage": "consult", "expectedStage": "exam", "expectedHistoryEntryId": card["history"][0]["id"],
        "lastEventId": ev["id"], "lastEventVersion": ev["version"],
        "lastEventUpdate": {"actualDate": "2026-08-20"}}, headers=auth_client.csrf)
    assert r.status_code == 409 and "code" not in r.json()
    assert _snapshot(p["id"]) == before


@pytest.mark.parametrize("partial", [{}, {"actualTime": ""}, {"matchedPlan": False}, {"noPlan": False}])
def test_i_partial_update_does_not_null_existing_fields(auth_client, partial):
    pid = _with_fact(auth_client)
    before = _event_row(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": partial})
    assert r.status_code == 200, r.text
    after = _event_row(pid)
    for col in ("actual_date", "actual_time", "matched_plan", "set_at", "version", "date", "time"):
        assert after[col] == before[col], col


def test_legacy_decision_with_null_date_keeps_the_fact(auth_client):
    """WP2.2-B: решение ОК через перевод этапа больше не принимается — явный 422, а не
    молчаливое игнорирование; существующий факт, этап и история не меняются (защита WP2.2-A
    сохраняется, решение записывается отдельной операцией)."""
    pid = _with_fact(auth_client, stage="consult")
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "treatment-prep",
                                  "lastEventUpdate": {"actualDate": None, "consiliumDecision": "treatment_strategy_defined"}})
    assert r.status_code == 422 and r.json()["code"] == "DECISION_SEPARATE_OPERATION"
    assert _snapshot(pid) == before


def test_legacy_decision_with_other_date_is_fact_conflict(auth_client):
    """WP2.2-B: запрос с решением отклоняется до проверки факта (422); ничего не меняется."""
    pid = _with_fact(auth_client, stage="consult")
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "treatment-prep",
                                  "lastEventUpdate": {"actualDate": "2026-08-21", "consiliumDecision": "treatment_strategy_defined"}})
    assert r.status_code == 422 and r.json()["code"] == "DECISION_SEPARATE_OPERATION"
    assert _snapshot(pid) == before


def test_old_client_payloads_stay_compatible(auth_client):
    """Тела запросов static/app.js (changeStage). WP2.2-B: решение ОК клиент отмечает
    отдельной операцией, а перевод присылает без него."""
    # 1. Факт уже есть: решение отмечено отдельно, перевод повторяет записанные дату и время.
    pid = _with_fact(auth_client, stage="consult")
    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (decision_doctor_id(), pid))
    conn.commit()
    assert mark_decision(auth_client, pid, "treatment_strategy_changed").status_code == 200
    r = _stage(auth_client, pid, {"newStage": "treatment-prep", "lastEventUpdate": {
        "actualDate": "2026-08-20", "actualTime": "10:00", "matchedPlan": True}})
    assert r.status_code == 200, r.text
    # 2. Факт есть, перевод без решения: lastEventUpdate = null.
    pid = _with_fact(auth_client)
    assert _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": None}).status_code == 200
    # 3. Факта нет, плановой даты нет: клиент присылает «сегодня» и matchedPlan null.
    p = _create(auth_client, stage="exam", events=[{"time": ""}])
    assert _stage(auth_client, p["id"], {"newStage": "consult",
                                         "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "", "matchedPlan": None}}).status_code == 200


# ---------- обязательный аудит: успех пишет запись в той же транзакции ----------

def test_stage_change_and_legacy_decision_write_required_audit(auth_client):
    """Перевод пишет обязательный аудит; решения ОК он больше не записывает (WP2.2-B) —
    записи consilium_decision_recorded от перевода нет."""
    p = _create(auth_client, stage="consult", events=[{"date": "2026-08-20"}])
    r = _stage(auth_client, p["id"], {"newStage": "treatment-prep", "lastEventUpdate": {"actualDate": "2026-08-20"}})
    assert r.status_code == 200, r.text
    rows = get_conn().execute(
        "SELECT event_type, detail, actor_user_id FROM audit_events WHERE patient_id = ? ORDER BY id", (p["id"],)).fetchall()
    kinds = [(r["event_type"], r["detail"]) for r in rows]
    assert ("patient_updated", "stage=treatment-prep") in kinds
    assert not any(k == "consilium_decision_recorded" for k, _ in kinds)
    assert all(r["actor_user_id"] for r in rows)


# ---------- обязательный аудит: сбой записи откатывает изменение ----------

def test_rollback_stage_change_when_audit_fails(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    before = _snapshot(p["id"])
    _fail_audit("patient_updated")
    try:
        r = _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20"}})
        assert r.status_code == 503 and "Этап не изменен" in r.json()["detail"]
        assert _snapshot(p["id"]) == before
    finally:
        _restore_audit()
    assert _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20"}}).status_code == 200


def test_rollback_legacy_decision_when_its_audit_fails(auth_client):
    """WP2.2-B: решение записывается отдельной операцией; сбой ее аудита откатывает
    отметку целиком (подробно — test_wp22b_consilium_decision)."""
    p = _create(auth_client, stage="consult", events=[{"date": "2026-08-20"}], treatingDoctorUserId=decision_doctor_id())
    before = _snapshot(p["id"])
    _fail_audit("consilium_decision_recorded")
    try:
        r = mark_decision(auth_client, p["id"], "treatment_strategy_defined")
        assert r.status_code == 503
        after = _snapshot(p["id"])
        # Вход врача и обратно пишут свои записи аудита сессии — медицинских изменений и
        # записи о решении нет.
        assert {k: v for k, v in after.items() if k != "audit"} == {k: v for k, v in before.items() if k != "audit"}
        assert not get_conn().execute(
            "SELECT 1 FROM audit_events WHERE event_type = 'consilium_decision_recorded' AND patient_id = ?",
            (p["id"],)).fetchone()
    finally:
        _restore_audit()


def test_rollback_terminal_transition_keeps_reminders(auth_client):
    p = _create(auth_client, stage="treatment", events=[{"date": "2026-08-20"}])
    before = _snapshot(p["id"])
    _fail_audit("patient_updated")
    try:
        r = _stage(auth_client, p["id"], {"newStage": "completed", "lastEventUpdate": {"actualDate": "2026-08-20"}})
        assert r.status_code == 503
        assert _snapshot(p["id"]) == before
    finally:
        _restore_audit()


@pytest.mark.parametrize("operation", ["event_update", "event_add", "note", "checklist"])
def test_rollback_history_writes_when_audit_fails(auth_client, operation):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    before = _snapshot(p["id"])
    _fail_audit("patient_updated")
    try:
        if operation == "event_update":
            r = auth_client.patch(f"/api/patients/{p['id']}/events/{ev['id']}",
                                  json={"field": "actualDate", "value": "2026-08-20", "version": ev["version"]}, headers=auth_client.csrf)
        elif operation == "event_add":
            r = auth_client.post(f"/api/patients/{p['id']}/history/0/events",
                                 json={"expectedEventIds": [ev["id"]]}, headers=auth_client.csrf)
        elif operation == "note":
            r = auth_client.patch(f"/api/patients/{p['id']}/history/0/note", json={"note": "Синтетическая заметка"}, headers=auth_client.csrf)
        else:
            r = auth_client.patch(f"/api/patients/{p['id']}/history/0/checklist",
                                  json={"expected": checklist_state(p['id'], 0, "КТ органов грудной клетки"), "item": "КТ органов грудной клетки", "checked": True}, headers=auth_client.csrf)
        assert r.status_code == 503, r.text
        assert _snapshot(p["id"]) == before
    finally:
        _restore_audit()


def _consilium_state(aid, pid):
    conn = get_conn()
    try:
        return (
            dict(conn.execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone()),
            conn.execute("SELECT COUNT(*) FROM consilium_revision_history WHERE appointment_id = ?", (aid,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
            _snapshot(pid),
        )
    finally:
        pass   # get_conn() — общее соединение теста, не закрывается


@pytest.mark.parametrize("action,state,event_type,body", [
    ("review", "recorded", "consilium_review_started", None),
    ("sign", "in_review", "consilium_signed", DECISION),
    ("return", "in_review", "consilium_returned_for_revision", {"comment": "Уточните синтетические данные"}),
])
def test_rollback_consilium_transition_when_audit_fails(client, action, state, event_type, body):
    aid, patient = _make_state(client, state)
    before = _consilium_state(aid, patient["id"])
    _fail_audit(event_type)
    try:
        r = _post(client, aid, action, body)
        assert r.status_code == 503, r.text
        assert _consilium_state(aid, patient["id"]) == before
    finally:
        _restore_audit()
    r = _post(client, aid, action, body)
    assert r.status_code == 200, r.text
    rows = get_conn().execute("SELECT event_type, patient_id FROM audit_events WHERE event_type = ?", (event_type,)).fetchall()
    assert rows and rows[-1]["patient_id"] == patient["id"]


def test_signing_still_does_not_change_stage_or_events(client):
    aid, patient = _make_state(client, "in_review")
    before = _snapshot(patient["id"])
    assert _post(client, aid, "sign", DECISION).status_code == 200
    after = _snapshot(patient["id"])
    assert after["stage"][0] == before["stage"][0] and after["history"] == before["history"] and after["events"] == before["events"]


# ---------- RBAC не изменился ----------

def test_read_only_role_cannot_transition_and_nothing_is_written(client):
    create_account(DEV_USERNAME)
    create_account("deputy.wp22a", "deputy_chief_oncology")
    login(client, DEV_USERNAME)
    p = _create(client, stage="exam", events=[{"date": "2026-08-20", "actualDate": "2026-08-20"}])
    body = stage_body(client, p["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-25"}})
    before = _snapshot(p["id"])
    login(client, "deputy.wp22a")
    r = client.post(f"/api/patients/{p['id']}/stage", json=body, headers=client.csrf)
    assert r.status_code == 403
    after = _snapshot(p["id"])
    # Отказ пишет только запись о запрете (fail-open, как и раньше) — медицинские данные не изменены.
    assert {k: v for k, v in after.items() if k != "audit"} == {k: v for k, v in before.items() if k != "audit"}


# ---------- заключительная проверка: единое вычисление matched_plan ----------

def _patch(client, pid, field, value, occ=0, idx=0):
    ev = _card(client, pid)["history"][occ]["events"][idx]
    r = client.patch(f"/api/patients/{pid}/events/{ev['id']}",
                     json={"field": field, "value": value, "version": ev["version"]}, headers=client.csrf)
    assert r.status_code == 200, r.text
    return _event_row(pid, occ, idx)


@pytest.mark.parametrize("plan,fact,expected", [
    ({"date": "2026-08-20", "time": "10:00"}, ("2026-08-20", "10:00"), 1),   # совпал с планом
    ({"date": "2026-08-20", "time": "10:00"}, ("2026-08-21", ""), 0),        # отклонение
    ({"date": "2026-08-20", "time": ""}, ("2026-08-20", ""), 1),
    ({"time": ""}, ("2026-08-20", ""), None),                                 # плановой даты нет — не определено
])
def test_matched_plan_is_the_same_via_stage_change_and_event_patch(auth_client, plan, fact, expected):
    a = _create(auth_client, stage="exam", events=[plan])
    r = _stage(auth_client, a["id"], {"newStage": "consult", "lastEventUpdate": {"actualDate": fact[0], "actualTime": fact[1]}})
    assert r.status_code == 200, r.text
    b = _create(auth_client, stage="exam", events=[plan])
    _patch(auth_client, b["id"], "actualDate", fact[0])
    if fact[1]:
        _patch(auth_client, b["id"], "actualTime", fact[1])
    assert _event_row(a["id"])["matched_plan"] == expected
    assert _event_row(b["id"])["matched_plan"] == expected


@pytest.mark.parametrize("plan", [{"date": "2026-08-20"}, {"time": ""}])
def test_clearing_the_fact_by_patch_leaves_matched_plan_undetermined(auth_client, plan):
    p = _create(auth_client, stage="exam", events=[plan])
    _patch(auth_client, p["id"], "actualDate", "2026-08-20")
    row = _patch(auth_client, p["id"], "actualDate", "")
    # Раньше: 0 («с отклонением») или даже 1 («по плану») при отсутствии факта.
    assert row["actual_date"] is None and row["matched_plan"] is None


def test_patch_of_no_plan_event_keeps_its_matched_plan(auth_client):
    p = _create(auth_client, stage="primary-appointment",
                events=[{"actualDate": "2026-08-20", "noPlan": True}])
    before = _event_row(p["id"])
    row = _patch(auth_client, p["id"], "actualTime", "09:00")
    assert row["no_plan"] == 1 and row["matched_plan"] == before["matched_plan"]


def test_stored_matched_plan_is_not_recalculated(auth_client):
    """Смысл существующих данных не меняется: сохраненное значение пересчитывается только
    при записи факта, а не при чтении и не при правке других полей."""
    p = _create(auth_client, stage="exam", events=[{"time": ""}])
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    conn = get_conn()
    conn.execute("UPDATE events SET actual_date = '2026-08-20', matched_plan = 0 WHERE id = ?", (ev_id,))
    conn.commit()
    assert _card(auth_client, p["id"])["history"][0]["events"][0]["matchedPlan"] is False
    row = _patch(auth_client, p["id"], "treatmentType", "")
    assert row["matched_plan"] == 0


# ---------- заключительная проверка: откат уведомлений и гарантия аудита ----------

def _pending_reminder(pid, event_id):
    from backend.app.db import now_iso
    conn = get_conn()
    conn.execute(
        """INSERT INTO notifications (id, patient_id, channel, template_id, template_code, template_version, source_type,
                                       source_id, scheduled_at, idempotency_key, status, attempt_count, max_attempts,
                                       created_at, updated_at)
           VALUES (?, ?, 'sms', NULL, 'event_reminder', 1, 'event', ?, ?, ?, 'pending', 0, 3, ?, ?)""",
        (f"n-{event_id}", pid, event_id, now_iso(), f"wp22a-{event_id}", now_iso(), now_iso()))
    conn.commit()


@pytest.mark.parametrize("target", ["consult", "completed"])
def test_reminder_cancellation_is_rolled_back_with_the_transition(auth_client, target):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    _pending_reminder(p["id"], ev_id)
    before = _snapshot(p["id"])
    assert before["notifications"] == [(f"n-{ev_id}", "pending")]
    _fail_audit("patient_updated")
    try:
        r = _stage(auth_client, p["id"], {"newStage": target, "lastEventUpdate": {"actualDate": "2026-08-20"}})
        assert r.status_code == 503
        assert _snapshot(p["id"]) == before          # напоминание осталось pending
    finally:
        _restore_audit()
    assert _stage(auth_client, p["id"], {"newStage": target, "lastEventUpdate": {"actualDate": "2026-08-20"}}).status_code == 200
    assert _snapshot(p["id"])["notifications"] == [(f"n-{ev_id}", "cancelled")]


def test_reminder_cancellation_by_fact_patch_is_rolled_back(auth_client):
    p = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])
    ev_id = _card(auth_client, p["id"])["history"][0]["events"][0]["id"]
    _pending_reminder(p["id"], ev_id)
    before = _snapshot(p["id"])
    _fail_audit("patient_updated")
    try:
        ev = _card(auth_client, p["id"])["history"][0]["events"][0]
        r = auth_client.patch(f"/api/patients/{p['id']}/events/{ev_id}",
                              json={"field": "actualDate", "value": "2026-08-20", "version": ev["version"]}, headers=auth_client.csrf)
        assert r.status_code == 503
        assert _snapshot(p["id"]) == before
    finally:
        _restore_audit()


AUDITED_WRITERS = {
    "store": {"change_stage", "add_event", "update_event", "update_note", "update_checklist_item",
              "delete_event", "restore_event"},
    "consilium": {"start_review", "sign", "return_for_revision"},
}


def test_every_application_call_of_a_route_writer_passes_an_audit_hook():
    """Защита от обхода: каждый вызов этих функций в коде приложения (не в тестах) передает
    audit_hook. Хук необязателен в сигнатуре только для прямых вызовов из тестов."""
    import ast
    from pathlib import Path
    app_dir = Path(__file__).resolve().parent.parent / "app"
    calls, missing = [], []
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.attr in AUDITED_WRITERS.get(f.value.id, ()):
                name = f"{path.relative_to(app_dir)}:{node.lineno} {f.value.id}.{f.attr}"
            elif isinstance(f, ast.Name) and any(f.id in names for names in AUDITED_WRITERS.values()):
                name = f"{path.relative_to(app_dir)}:{node.lineno} {f.id}"
            else:
                continue
            calls.append(name)
            if not any(k.arg == "audit_hook" for k in node.keywords):
                missing.append(name)
    assert len(calls) == 10, calls     # 7 маршрутов пациентов + 3 маршрута председателя
    assert missing == []


def test_current_audit_retention_policy_is_documented_by_test(auth_client):
    """Фиксирует действующую политику (без изменений в WP2.2-A): audit-prune удаляет записи
    старше срока, кроме удаления/восстановления событий; записи о переводе этапа, решении ОК,
    правках событий и действиях председателя очищаются по общему сроку."""
    from backend.app import audit
    # WP2.2-B: решение — отдельная отметка врача пациента, затем перевод этапа.
    p = _create(auth_client, stage="consult", events=[{"date": "2026-08-20"}], treatingDoctorUserId=decision_doctor_id())
    assert mark_decision(auth_client, p["id"], "treatment_strategy_defined").status_code == 200
    assert _stage(auth_client, p["id"], {"newStage": "treatment-prep", "lastEventUpdate": {
        "actualDate": "2026-08-20"}}).status_code == 200
    ev = _card(auth_client, p["id"])["history"][0]["events"][0]
    assert auth_client.delete(f"/api/patients/{p['id']}/events/{ev['id']}?version={ev['version']}",
                              headers=auth_client.csrf).status_code == 200
    conn = get_conn()
    # WP-C (AUD-13): журнал только дополняется — правило проверяется на состаренных копиях
    # строк этого пациента (те же типы событий), свежие строки не трогаются.
    conn.execute("""INSERT INTO audit_events (created_at, event_type, patient_id, result, detail)
                    SELECT '2000-01-01T00:00:00+00:00', event_type, patient_id, result, detail
                    FROM audit_events WHERE patient_id = ?""", (p["id"],))
    conn.commit()
    audit.prune(conn, 365)
    left = {r[0] for r in conn.execute(
        "SELECT event_type FROM audit_events WHERE patient_id = ? AND created_at LIKE '2000-%'", (p["id"],))}
    assert left == {"patient_event_deleted"}
    assert audit.RETAINED_EVENT_TYPES == {"patient_event_deleted", "patient_event_restored"}


# ---------- время, записанное правкой события до фактической даты ----------

def _time_only(client, plan_time="09:45", stored="09:45"):
    """Событие с плановой датой, без фактической даты, но с фактическим временем (PATCH это допускает)."""
    p = _create(client, stage="exam", events=[{"date": "2026-08-20", "time": plan_time}])
    row = _patch(client, p["id"], "actualTime", stored)
    assert row["actual_date"] is None and row["actual_time"] == stored
    return p["id"]


def test_time_only_1_other_time_is_fact_conflict_without_any_change(auth_client):
    pid = _time_only(auth_client)
    _pending_reminder(pid, _event_row(pid)["id"])
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "10:00"}})
    assert r.status_code == 409 and r.json()["code"] == "FACT_CONFLICT"
    assert _snapshot(pid) == before          # этап, история, событие и версия, журнал, уведомления


@pytest.mark.parametrize("time_field", [{}, {"actualTime": ""}, {"actualTime": None}])
def test_time_only_2_date_is_added_and_stored_time_kept(auth_client, time_field):
    pid = _time_only(auth_client)
    before = _event_row(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", **time_field}})
    assert r.status_code == 200, r.text
    row = _event_row(pid)
    assert (row["actual_date"], row["actual_time"]) == ("2026-08-20", "09:45")
    assert row["matched_plan"] == 1                     # план 2026-08-20 09:45 — сравнение с сохраненным временем
    assert row["version"] == before["version"] + 1


def test_time_only_2_matched_plan_uses_stored_time(auth_client):
    pid = _time_only(auth_client, plan_time="10:00", stored="09:45")
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "matchedPlan": True}})
    assert r.status_code == 200, r.text
    row = _event_row(pid)
    assert (row["actual_time"], row["matched_plan"]) == ("09:45", 0)


def test_time_only_3_same_time_keeps_original(auth_client):
    pid = _time_only(auth_client)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "09:45"}})
    assert r.status_code == 200, r.text
    row = _event_row(pid)
    assert (row["actual_date"], row["actual_time"], row["matched_plan"]) == ("2026-08-20", "09:45", 1)


@pytest.mark.parametrize("update", [None, {}, {"actualDate": None}, {"matchedPlan": True}])
def test_time_only_4_no_new_fact_leaves_time_untouched(auth_client, update):
    pid = _time_only(auth_client)
    before = _event_row(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": update})
    assert r.status_code == 200, r.text
    after = _event_row(pid)
    for col in ("actual_date", "actual_time", "matched_plan", "set_at", "version"):
        assert after[col] == before[col], col


def test_time_only_4_legacy_decision_keeps_time(auth_client):
    """WP2.2-B: отдельная отметка решения ОК не трогает фактическое время, записанное без даты."""
    p = _create(auth_client, stage="consult", events=[{"date": "2026-08-20", "time": "09:45"}],
                treatingDoctorUserId=decision_doctor_id())
    _patch(auth_client, p["id"], "actualTime", "09:45")
    r = mark_decision(auth_client, p["id"], "treatment_strategy_defined")
    assert r.status_code == 200, r.text
    row = _event_row(p["id"])
    assert (row["actual_date"], row["actual_time"], row["consilium_decision"]) == (None, "09:45", "treatment_strategy_defined")


def test_time_only_stale_version_is_rejected_without_change(auth_client):
    pid = _time_only(auth_client)
    card = _card(auth_client, pid)
    ev = card["history"][0]["events"][0]
    _patch(auth_client, pid, "treatmentType", "")      # версия выросла, карточка устарела
    before = _snapshot(pid)
    r = auth_client.post(f"/api/patients/{pid}/stage", json={
        "newStage": "consult", "expectedStage": "exam", "expectedHistoryEntryId": card["history"][0]["id"],
        "lastEventId": ev["id"], "lastEventVersion": ev["version"],
        "lastEventUpdate": {"actualDate": "2026-08-20"}}, headers=auth_client.csrf)
    assert r.status_code == 409 and "code" not in r.json()
    assert _snapshot(pid) == before


def test_time_only_date_added_is_rolled_back_when_audit_fails(auth_client):
    pid = _time_only(auth_client)
    _pending_reminder(pid, _event_row(pid)["id"])
    before = _snapshot(pid)
    _fail_audit("patient_updated")
    try:
        r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-08-20"}})
        assert r.status_code == 503
        assert _snapshot(pid) == before
    finally:
        _restore_audit()


def test_time_only_old_client_bodies(auth_client):
    """Тела static/app.js (changeStage), когда фактической даты нет, а время уже сохранено."""
    # Плановая дата — сегодня: клиент присылает плановое время. Совпадает с сохраненным — перевод проходит.
    pid = _time_only(auth_client, plan_time="09:45", stored="09:45")
    assert _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {
        "actualDate": "2026-08-20", "actualTime": "09:45", "matchedPlan": True}}).status_code == 200
    # Плановое время отличается от сохраненного фактического — 409, данные не изменены (нужна адаптация клиента).
    pid = _time_only(auth_client, plan_time="10:00", stored="09:45")
    before = _snapshot(pid)
    r = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {
        "actualDate": "2026-08-20", "actualTime": "10:00", "matchedPlan": True}})
    assert r.status_code == 409 and r.json()["code"] == "FACT_CONFLICT"
    assert _snapshot(pid) == before
    # Плановой даты нет: клиент присылает actualTime '' — время сохраняется.
    p = _create(auth_client, stage="exam", events=[{"time": ""}])
    _patch(auth_client, p["id"], "actualTime", "09:45")
    assert _stage(auth_client, p["id"], {"newStage": "consult", "lastEventUpdate": {
        "actualDate": "2026-08-20", "actualTime": "", "matchedPlan": None}}).status_code == 200
    assert _event_row(p["id"])["actual_time"] == "09:45"
