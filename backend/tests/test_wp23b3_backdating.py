"""WP2.3-B3: a consilium recorded after the fact (backdating).

Stage 1 — the chair creates a historical slot (past date and time in Moscow,
mandatory reason). Stage 2 — a department head books a patient of own
department into such a slot (mandatory reason). Reasons live in
consilium_backdating_reasons, never in the technical audit or general
responses; reading them is a separate, audited operation.
Test clock: 01.10.2026 09:00 Moscow (conftest). Synthetic data only.
"""
import json
import logging
import sqlite3
import time

import pytest

from .conftest import DEV_USERNAME, consilium_grid, consilium_version, get_conn, login, set_moscow_now
from .test_consilium_appointments import appointment_payload, create_patient
from .test_wp23a_scheduling_integrity import (  # noqa: F401 — _high_limits is autouse here too
    DECISION, _Hold, _audit, _client_for, _consilium, _fail_audit, _high_limits, _in_thread, _interleave,
    _med_digest, _parallel, _rows,
)
from .test_wp23b2_commit_boundary import _AfterCommit, _commit_elsewhere
from .test_wp23b2_scheduling_rbac import DAY, _post, _row, _sign, _world

PAST = "2026-09-15"          # вторник, до часов теста
SUNDAY = "2026-09-13"
SLOT_REASON = "Заседание прошло на бумаге: сервер был недоступен"
APPT_REASON = "Протокол заседания передан в отделение с опозданием"
FUTURE_MSG = "Задним числом можно оформить только прошедшие дату и время (по Москве)"
SLOT_REQUIRED_MSG = "Запись задним числом возможна только в исторический слот председателя"


# ---------------------------------------------------------------- helpers

def _slot(client, d=PAST, t="10:00", group="thoracic", reason=SLOT_REASON):
    return client.post("/api/consilium/schedule/backdated", headers=client.csrf, json={
        "scheduledDate": d, "scheduledTime": t, "consiliumGroup": group, "reason": reason})


def _backdate(client, pid, d=PAST, t="10:00", reason=APPT_REASON, **kw):
    return client.post("/api/consilium/appointments/backdated", headers=client.csrf,
                       json={**appointment_payload(pid, scheduledDate=d, scheduledTime=t, **kw), "reason": reason})


def _reasons(client, aid):
    return client.get(f"/api/consilium/appointments/{aid}/backdating")


def _counts():
    return {
        "slots": _rows("SELECT COUNT(*) AS c FROM consilium_schedule_slots WHERE backdated = 1")[0]["c"],
        "appointments": _rows("SELECT COUNT(*) AS c FROM oncology_consilium_appointments")[0]["c"],
        "reasons": _rows("SELECT COUNT(*) AS c FROM consilium_backdating_reasons")[0]["c"],
        "audit": _rows("SELECT COUNT(*) AS c FROM audit_events WHERE event_type LIKE 'consilium_%'")[0]["c"],
    }


def _historical(client, d=PAST, t="10:00", group="thoracic"):
    login(client, "chair")
    response = _slot(client, d, t, group)
    assert response.status_code == 201, response.text
    return response.json()


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


# ================================================================ 1–2. the two stages

def test_chair_creates_a_historical_slot_with_a_mandatory_reason(client):
    _world(client)
    login(client, "chair")
    for reason in ("", "   ", "\n\t "):
        assert _slot(client, reason=reason).status_code == 422
    assert _counts()["reasons"] == 0
    response = _slot(client)
    assert response.status_code == 201, response.text
    slot = response.json()
    assert (slot["scheduledDate"], slot["scheduledTime"], slot["backdated"], slot["bookable"]) == (
        PAST, "10:00", True, False)
    row = _rows("SELECT * FROM consilium_schedule_slots WHERE id = ?", slot["id"])[0]
    reason = _rows("SELECT * FROM consilium_backdating_reasons WHERE id = ?", row["backdating_reason_id"])[0]
    assert (row["backdated"], reason["kind"], reason["reason"]) == (1, "slot", SLOT_REASON)
    # Фактическое время создания хранится отдельно от даты консилиума.
    assert not row["created_at"].startswith(PAST) and not reason["created_at"].startswith(PAST)
    audit = _audit("consilium_backdated_slot_created")
    assert len(audit) == 1
    assert audit[0]["detail"] == (f"slot_id={slot['id']};slot_date={PAST};slot_time=10:00;group=thoracic;"
                                  f"reason_id={row['backdating_reason_id']}")
    assert SLOT_REASON not in json.dumps(slot, ensure_ascii=False)
    duplicate = _slot(client)
    assert duplicate.status_code == 409 and SLOT_REASON not in duplicate.text


def test_head_backdates_a_patient_of_own_department(client):
    ids, pts = _world(client)
    slot = _historical(client)
    login(client, "head.1")
    response = _backdate(client, pts["a1"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["backdated"] is True and body["status"] == "recorded"
    assert (body["scheduledDate"], body["scheduledTime"]) == (PAST, "10:00")
    assert APPT_REASON not in response.text and SLOT_REASON not in response.text
    row = _row(body["id"])
    reason = _rows("SELECT * FROM consilium_backdating_reasons WHERE id = ?", row["backdating_reason_id"])[0]
    assert (row["backdated"], row["created_by_user_id"], reason["kind"], reason["reason"]) == (
        1, ids["head.1"], "appointment", APPT_REASON)
    assert body["createdAt"] == row["created_at"] and not row["created_at"].startswith(PAST)
    audit = _audit("consilium_backdated_appointment_created")
    assert len(audit) == 1 and audit[0]["patient_id"] == pts["a1"]
    assert audit[0]["detail"] == (f"appointment_id={body['id']};slot_id={slot['id']};slot_date={PAST};"
                                  f"slot_time=10:00;group=thoracic;reason_id={row['backdating_reason_id']}")
    # Врач пациента видит запись с отметкой, но без текста причины.
    login(client, "doc.a1")
    item = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert item["backdated"] is True and APPT_REASON not in json.dumps(item, ensure_ascii=False)


def test_weekend_historical_slot_is_allowed(client):
    _, pts = _world(client)
    _historical(client, SUNDAY, "11:00")
    login(client, "head.1")
    assert _backdate(client, pts["a1"], SUNDAY, "11:00").status_code == 201


# ================================================================ 3–4. roles and scope

@pytest.mark.parametrize("who", ["doc.a1", "dev", "chair", "deputy", "caop", "head.2"])
def test_only_the_head_of_the_department_can_backdate(client, who):
    _, pts = _world(client)
    _historical(client)
    before = _counts()
    login(client, DEV_USERNAME if who == "dev" else who)
    response = _backdate(client, pts["a1"])
    assert response.status_code == (404 if who == "head.2" else 403), (who, response.text)
    assert _counts()["appointments"] == before["appointments"] and _counts()["reasons"] == before["reasons"]


@pytest.mark.parametrize("who", ["head.1", "doc.a1", "dev", "deputy", "caop"])
def test_only_the_chair_creates_historical_slots(client, who):
    _world(client)
    login(client, DEV_USERNAME if who == "dev" else who)
    assert _slot(client).status_code == 403
    assert _counts()["slots"] == 0 and _counts()["reasons"] == 0


# ================================================================ 5–7. slots and time

def test_an_ordinary_past_slot_or_no_slot_is_refused(client):
    _, pts = _world(client)
    consilium_grid((PAST,), times=("10:00",), groups=("thoracic",))   # обычный слот, время прошло
    login(client, "head.1")
    for d, t in ((PAST, "10:00"), (PAST, "11:00")):
        response = _backdate(client, pts["a1"], d, t)
        assert response.status_code == 409 and response.json()["detail"] == SLOT_REQUIRED_MSG
    # Обычная запись в исторический слот тоже невозможна (время прошло).
    _historical(client, PAST, "12:00")
    login(client, "head.1")
    normal = client.post("/api/consilium/appointments", headers=client.csrf,
                         json=appointment_payload(pts["a1"], scheduledDate=PAST, scheduledTime="12:00"))
    assert normal.status_code == 409
    assert _counts()["appointments"] == 0


def test_future_time_is_refused_by_both_backdating_apis(client, monkeypatch):
    _, pts = _world(client)
    login(client, "chair")
    before = _counts()
    # Будущее и ровно текущий момент (B3-04: только slot_moment < now_moscow).
    for d, t in ((DAY, "10:00"), ("2026-10-01", "09:01"), ("2026-10-01", "09:00")):
        response = _slot(client, d, t)
        assert response.status_code == 409 and response.json()["detail"] == FUTURE_MSG, (d, t)
    assert _counts() == before and _audit("consilium_backdated_slot_created") == []
    assert _slot(client, "2026-10-01", "08:59").status_code == 201        # на минуту раньше — уже прошло
    # Исторический слот, а часы отодвинуты назад (например, исправлено время сервера):
    login(client, "head.1")
    for now in ("2026-10-01T08:30:00+03:00", "2026-10-01T08:59:00+03:00"):   # раньше и ровно в момент слота
        set_moscow_now(monkeypatch, now)
        response = _backdate(client, pts["a1"], "2026-10-01", "08:59")
        assert response.status_code == 409 and response.json()["detail"] == FUTURE_MSG, now
    assert _counts()["appointments"] == 0


def test_an_occupied_historical_slot_cannot_be_reused(client):
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    assert _backdate(client, pts["a1"]).status_code == 201
    taken = _backdate(client, pts["a2"])
    assert taken.status_code == 409 and taken.json()["detail"] == "Выбранный слот уже занят"


# ================================================================ 8–10. concurrency

def test_two_records_into_one_historical_slot_forced(client, monkeypatch):
    _, pts = _world(client)
    _historical(client)
    hold = _Hold(monkeypatch, _consilium(), "_require_backdated_slot")
    tab1, tab2 = _client_for(client, "head.1"), _client_for(client, "head.1")
    first, second, waited = _interleave(lambda: _backdate(tab1, pts["a1"]), lambda: _backdate(tab2, pts["a2"]), hold)
    assert waited and first.status_code == 201 and second.status_code == 409
    assert _counts()["appointments"] == 1 and _counts()["reasons"] == 2   # слот + одна запись


def test_two_records_into_one_historical_slot_burst(client):
    _, pts = _world(client)
    _historical(client)
    tab1, tab2 = _client_for(client, "head.1"), _client_for(client, "head.1")
    for _ in range(20):
        res = _parallel(lambda: _backdate(tab1, pts["a1"]), lambda: _backdate(tab2, pts["a2"]))
        assert sorted(r.status_code for r, *_ in res) == [201, 409]
        assert _counts()["appointments"] == 1
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().commit()


def test_reassignment_committed_while_waiting_is_respected(client):
    ids, pts = _world(client)
    _historical(client)
    head = _client_for(client, "head.1")
    before = _counts()
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.b"], pts["a1"]))
    box, done, thread = _in_thread(lambda: _backdate(head, pts["a1"]))
    time.sleep(0.6)
    waited = not done.is_set()
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    assert waited and box["response"].status_code == 404
    after = _counts()
    assert after["appointments"] == before["appointments"] and after["reasons"] == before["reasons"]


def test_head_losing_the_department_while_waiting_is_refused(client):
    ids, pts = _world(client)
    _historical(client)
    head = _client_for(client, "head.1")
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    box, done, thread = _in_thread(lambda: _backdate(head, pts["a1"]))
    time.sleep(0.6)
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    assert box["response"].status_code == 404 and _counts()["appointments"] == 0


def test_scope_change_after_the_commit_keeps_the_true_response(client, monkeypatch):
    ids, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    boundary = _AfterCommit(monkeypatch, "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    response = _backdate(client, pts["a1"])
    assert boundary.fired and response.status_code == 201, response.text
    aid = _rows("SELECT id FROM oncology_consilium_appointments")[0]["id"]
    assert response.json()["id"] == aid and response.json()["version"] == _row(aid)["version"]
    assert response.json()["backdated"] is True


def test_chair_role_lost_after_commit_keeps_the_true_slot_response(client, monkeypatch):
    ids, _ = _world(client)
    login(client, "chair")
    boundary = _AfterCommit(monkeypatch, "UPDATE users SET status = 'blocked' WHERE id = ?", (ids["chair"],))
    response = _slot(client)
    assert boundary.fired and response.status_code == 201 and response.json()["backdated"] is True


# ================================================================ 11. audit outage

@pytest.mark.parametrize("stage", ["slot", "appointment"])
def test_audit_outage_rolls_back_slot_record_and_reason(client, stage):
    _, pts = _world(client)
    if stage == "appointment":
        _historical(client)
    before = _counts()
    _fail_audit("consilium_backdated_slot_created" if stage == "slot" else "consilium_backdated_appointment_created")
    if stage == "slot":
        login(client, "chair")
        response = _slot(client, PAST, "11:00")
    else:
        login(client, "head.1")
        response = _backdate(client, pts["a1"])
    assert response.status_code == 503 and "журнала аудита" in response.json()["detail"]
    assert _counts() == before


def test_reading_a_reason_is_audited_fail_closed(client):
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    _fail_audit("consilium_backdating_reason_viewed")
    response = _reasons(client, aid)
    assert response.status_code == 503 and APPT_REASON not in response.text


# ================================================================ 12–13. who reads reasons

def test_reason_access_and_idor(client):
    _, pts = _world(client)
    slot = _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    login(client, "doc.b")
    plain = client.post("/api/consilium/appointments", headers=client.csrf,
                        json=appointment_payload(pts["b"], scheduledDate=DAY, scheduledTime="13:00")).json()["id"]
    # Председатель: обе причины.
    login(client, "chair")
    body = _reasons(client, aid).json()
    assert (body["appointmentReason"]["text"], body["slotReason"]["text"]) == (APPT_REASON, SLOT_REASON)
    assert body["createdAt"] == _row(aid)["created_at"] and body["scheduledDate"] == PAST
    assert client.get(f"/api/consilium/schedule/{slot['id']}/backdating").json()["slotReason"]["text"] == SLOT_REASON
    # Заведующий своего отделения: обе причины в контексте записи; причина слота напрямую — нет.
    login(client, "head.1")
    body = _reasons(client, aid).json()
    assert (body["appointmentReason"]["text"], body["slotReason"]["text"]) == (APPT_REASON, SLOT_REASON)
    assert client.get(f"/api/consilium/schedule/{slot['id']}/backdating").status_code == 403
    # Чужое отделение, обычная запись, несуществующий id — 404 без различий.
    login(client, "head.2")
    for target in (aid, plain, "no-such-id"):
        response = _reasons(client, target)
        assert response.status_code == 404 and APPT_REASON not in response.text
    # Врач пациента — только отметка; остальные роли — 403.
    for who in ("doc.a1", DEV_USERNAME, "deputy", "caop"):
        login(client, who)
        assert _reasons(client, aid).status_code == 403, who
        assert client.get(f"/api/consilium/schedule/{slot['id']}/backdating").status_code == 403, who
    # Чтения по reason_id нет вовсе.
    reason_id = _row(aid)["backdating_reason_id"]
    login(client, "chair")
    for path in (f"/api/consilium/backdating-reasons/{reason_id}", f"/api/consilium/appointments/{reason_id}/backdating",
                 f"/api/consilium/schedule/{reason_id}/backdating"):
        response = client.get(path)
        assert response.status_code in (404, 405) and APPT_REASON not in response.text, path
    viewed = _audit("consilium_backdating_reason_viewed")
    # Прочитано трижды (председатель: запись и слот; заведующий: запись); отказы не считаются.
    assert len(viewed) == 3 and all(APPT_REASON not in (v["detail"] or "") for v in viewed)


def test_reasons_never_leak_into_general_responses_audit_or_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Уточните"}).status_code == 200
    payloads = []
    for who in ("chair", "head.1", "doc.a1"):
        login(client, who)
        for path in ("/api/consilium/appointments", "/api/consilium/schedule", "/api/consilium/meta",
                     f"/api/consilium/appointments/{aid}/revisions", "/api/internal-notifications"):
            payloads.append(client.get(path).text)
    login(client, "chair")
    for path in ("/api/consilium/analytics", f"/api/consilium/registries?date={PAST}"):
        payloads.append(client.get(path).text)
    login(client, DEV_USERNAME)
    audit_view = client.get("/api/audit?limit=200")
    assert audit_view.status_code == 200
    payloads.append(audit_view.text)
    dumped = " ".join(payloads) + json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False)
    dumped += " ".join(record.getMessage() for record in caplog.records)
    for secret in (APPT_REASON, SLOT_REASON):
        assert secret not in dumped


# ================================================================ 14–17. medical history

def _signed_later_consilium(client, monkeypatch, pts, day="2026-09-18"):
    """A consilium held and signed on 18.09 (normal booking made before it)."""
    consilium_grid((day,), times=("10:00",), groups=("thoracic",))
    set_moscow_now(monkeypatch, "2026-09-17T09:00:00+03:00")
    login(client, "doc.a1")
    aid = client.post("/api/consilium/appointments", headers=client.csrf,
                      json=appointment_payload(pts["a1"], scheduledDate=day, scheduledTime="10:00")).json()["id"]
    _sign(client, aid, back_to="doc.a1")
    set_moscow_now(monkeypatch, "2026-10-01T09:00:00+03:00")
    return aid


def test_backdated_consilium_before_a_later_signed_one(client, monkeypatch):
    _, pts = _world(client)
    signed = _signed_later_consilium(client, monkeypatch, pts)
    signed_before = _row(signed)
    med_before = _med_digest(pts["a1"])
    _historical(client, PAST, "10:00")
    login(client, "head.1")
    response = _backdate(client, pts["a1"], PAST, "10:00")
    assert response.status_code == 201, response.text
    earlier = response.json()["id"]
    assert _row(signed) == signed_before                       # решение от 18.09 не изменилось
    # Хронология — по дате проведения, а не по порядку внесения в базу.
    login(client, "doc.a1")
    listed = [(a["id"], a["scheduledDate"], a["backdated"]) for a in client.get("/api/consilium/appointments").json()["appointments"]]
    assert listed == [(earlier, PAST, True), (signed, "2026-09-18", False)]
    assert _row(earlier)["created_at"] > _row(signed)["created_at"]
    # Подписанная запись по-прежнему единственное подписанное решение; новая — незавершенная.
    assert (_row(signed)["status"], _row(earlier)["status"]) == ("signed", "recorded")
    med_after = _med_digest(pts["a1"])
    for d in (med_before, med_after):
        d["patient"].pop("updated_at", None)
    assert med_after == med_before
    # Аналитика относит запись к периоду проведения.
    login(client, "chair")
    september = client.get("/api/consilium/analytics?date_from=2026-09-01&date_to=2026-09-30").json()
    assert september["summary"]["total"] == 2 and september["summary"]["signed"] == 1
    october = client.get("/api/consilium/analytics?date_from=2026-10-01&date_to=2026-10-31").json()
    assert october["summary"]["total"] == 0


def test_backdated_record_then_follows_the_normal_workflow(client):
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    reason_id = _row(aid)["backdating_reason_id"]
    # Правка без смены слота — можно; перенос в другой слот — нет.
    edit = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pts["a1"], scheduledDate=PAST, scheduledTime="10:00", comment="Уточнено", version=consilium_version(aid)))
    assert edit.status_code == 200 and edit.json()["backdated"] is True
    moved = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pts["a1"], scheduledDate=DAY, scheduledTime="13:00", version=consilium_version(aid)))
    assert moved.status_code == 409 and _row(aid)["scheduled_date"] == PAST
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Уточните"}).status_code == 200
    login(client, "doc.a1")
    assert _post(client, aid, "resubmit").status_code == 200
    _sign(client, aid, back_to="doc.a1")
    assert _post(client, aid, "archive").status_code == 200
    signed = _row(aid)
    assert (signed["status"], signed["backdated"], signed["backdating_reason_id"]) == ("signed", 1, reason_id)
    conn = get_conn()
    for change in ("backdated = 0", "backdating_reason_id = NULL", "scheduled_date = '2026-09-16'"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE oncology_consilium_appointments SET {change} WHERE id = ?", (aid,))
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="consilium_backdating_reason_immutable"):
        conn.execute("UPDATE consilium_backdating_reasons SET reason = 'подмена' WHERE id = ?", (reason_id,))
    conn.rollback()
    assert len(_rows("SELECT id FROM consilium_resubmissions WHERE appointment_id = ?", aid)) == 1


def test_one_unfinished_consilium_rule_still_holds(client):
    _, pts = _world(client)
    login(client, "doc.a1")
    open_id = client.post("/api/consilium/appointments", headers=client.csrf,
                          json=appointment_payload(pts["a1"], scheduledDate=DAY, scheduledTime="13:00")).json()["id"]
    _historical(client)
    login(client, "head.1")
    response = _backdate(client, pts["a1"])
    assert response.status_code == 409 and response.json()["detail"].startswith("Пациент уже записан")
    assert _row(open_id)["backdated"] == 0 and _counts()["appointments"] == 1


def test_slot_reason_cannot_back_a_record_and_vice_versa(client):
    """Kinds are enforced: a slot reason never serves as a record reason."""
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    slot_reason = _rows("SELECT backdating_reason_id FROM consilium_schedule_slots WHERE backdated = 1")[0][
        "backdating_reason_id"]
    get_conn().execute("UPDATE oncology_consilium_appointments SET backdating_reason_id = ? WHERE id = ?",
                       (slot_reason, aid))
    get_conn().commit()
    login(client, "chair")
    assert _reasons(client, aid).status_code == 404       # причина не того вида — не показывается


# ================================================================ 19. input and XSS

@pytest.mark.parametrize("field, value", [
    ("scheduledDate", "2026-W38-2"), ("scheduledDate", "20260915  "), ("scheduledDate", "2026-02-30"),
    ("scheduledTime", "24:00"), ("scheduledTime", "9:30"), ("reason", "a" * 2001), ("reason", "текст\x00скрытый"),
    ("reason", "текст\x1b[2Kподмена"),
])
def test_invalid_backdating_input_is_rejected(client, field, value):
    _, pts = _world(client)
    login(client, "chair")
    body = {"scheduledDate": PAST, "scheduledTime": "10:00", "consiliumGroup": "thoracic", "reason": SLOT_REASON}
    body[field] = value
    assert client.post("/api/consilium/schedule/backdated", headers=client.csrf, json=body).status_code == 422
    assert _counts()["slots"] == 0 and _counts()["reasons"] == 0


def test_extra_fields_are_rejected(client):
    _, pts = _world(client)
    login(client, "chair")
    body = {"scheduledDate": PAST, "scheduledTime": "10:00", "consiliumGroup": "thoracic", "reason": SLOT_REASON}
    for extra in ({"backdated": False}, {"reasonId": "x"}, {"createdAt": "2026-09-15T10:00:00+03:00"}):
        assert client.post("/api/consilium/schedule/backdated", headers=client.csrf,
                           json={**body, **extra}).status_code == 422
    _historical(client)
    login(client, "head.1")
    payload = {**appointment_payload(pts["a1"], scheduledDate=PAST, scheduledTime="10:00"), "reason": APPT_REASON}
    for extra in ({"backdatingReasonId": "x"}, {"createdAt": "2026-09-15"}, {"createdByUserId": "x"}):
        assert client.post("/api/consilium/appointments/backdated", headers=client.csrf,
                           json={**payload, **extra}).status_code == 422
    assert _counts()["appointments"] == 0


def test_unknown_group_and_reason_of_exactly_2000_characters(client):
    _world(client)
    login(client, "chair")
    assert _slot(client, group="zzz").status_code == 409
    assert _slot(client, reason="я" * 2000).status_code == 201


def test_markup_in_a_reason_is_stored_as_text_and_returned_only_as_json(client):
    _, pts = _world(client)
    markup = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"], reason=markup).json()["id"]
    stored = _rows("SELECT reason FROM consilium_backdating_reasons WHERE kind = 'appointment'")[0]["reason"]
    assert stored == markup
    response = _reasons(client, aid)
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["appointmentReason"]["text"] == markup
    login(client, "doc.a1")
    assert markup not in client.get("/api/consilium/appointments").text


def test_static_app_backdating_ui():
    from .conftest import PROJECT_ROOT
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert "/api/consilium/schedule/backdated" in js and "/api/consilium/appointments/backdated" in js
    assert "Создать слот задним числом" in html and "Оформить консилиум задним числом" in html
    assert "Оформлено задним числом" in js
    # Текст причины выводится только через textContent/text, без innerHTML.
    block = js[js.index("    async showConsiliumBackdatingReasons("):]
    block = block[:block.index("\n    },\n")]
    assert "innerHTML" not in block and "text:" in block
    for text in ("Создать слот задним числом", "Оформить консилиум задним числом", "Оформлено задним числом",
                 "Причина позднего создания слота", "Причина позднего оформления"):
        assert "ё" not in text


# ================================================================ fixes after review (B3-01 … B3-03)

def _delete_record(client, aid):
    return client.delete(f"/api/consilium/appointments/{aid}?version={consilium_version(aid)}", headers=client.csrf)


def _unlinked_reasons():
    return _rows(
        """SELECT r.id FROM consilium_backdating_reasons r
           WHERE NOT EXISTS (SELECT 1 FROM oncology_consilium_appointments a WHERE a.backdating_reason_id = r.id)
             AND NOT EXISTS (SELECT 1 FROM consilium_schedule_slots s WHERE s.backdating_reason_id = r.id)""")


@pytest.mark.parametrize("who", ["head.1", "doc.a1"])
def test_cancelling_a_backdated_record_without_returns_keeps_it(client, who):
    """B3-01: a backdated record is always only marked deleted — even without
    return rounds. Id, consilium date, author, reason link and history stay;
    the slot is freed as for any cancellation."""
    ids, pts = _world(client)
    slot = _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    before = _row(aid)
    reasons_before = _rows("SELECT * FROM consilium_backdating_reasons ORDER BY id")
    login(client, who)
    response = _delete_record(client, aid)
    assert response.status_code == 200, response.text
    assert response.json()["historyPreserved"] is True
    after = _row(aid)
    assert after is not None and after["deleted_at"] and after["deleted_by_user_id"] == ids[who]
    for column in ("id", "patient_id", "scheduled_date", "scheduled_time", "consilium_group", "created_by_user_id",
                   "created_at", "backdated", "backdating_reason_id", "status"):
        assert after[column] == before[column], column
    assert _rows("SELECT * FROM consilium_backdating_reasons ORDER BY id") == reasons_before
    assert _unlinked_reasons() == []
    audit = _audit("consilium_appointment_deleted")
    assert len(audit) == 1 and "mode=soft" in audit[0]["detail"] and APPT_REASON not in audit[0]["detail"]
    # Запись ушла из списков, причины по ней не раскрываются (404), причина слота — на месте.
    assert aid not in client.get("/api/consilium/appointments").text
    login(client, "chair")
    assert _reasons(client, aid).status_code == 404
    slot_reason = client.get(f"/api/consilium/schedule/{slot['id']}/backdating")
    assert slot_reason.status_code == 200 and slot_reason.json()["slotReason"]["text"] == SLOT_REASON
    # Слот освобожден: заведующий оформляет в него другого пациента, а пациента — снова.
    login(client, "head.1")
    assert _backdate(client, pts["a2"]).status_code == 201
    _historical(client, PAST, "11:00")
    login(client, "head.1")
    assert _backdate(client, pts["a1"], PAST, "11:00").status_code == 201
    assert _unlinked_reasons() == []


def test_cancelling_a_backdated_record_with_history_and_ordinary_records_unchanged(client):
    """B3-01: a backdated record with return rounds keeps its history; an
    ordinary record without rounds is still removed physically (unchanged)."""
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Уточните"}).status_code == 200
    login(client, "doc.a1")
    assert _delete_record(client, aid).json()["historyPreserved"] is True
    assert _row(aid)["deleted_at"] and _rows("SELECT * FROM consilium_resubmissions WHERE appointment_id = ?", aid) == []
    assert len(_rows("SELECT * FROM consilium_revision_history WHERE appointment_id = ?", aid)) >= 1
    ordinary = client.post("/api/consilium/appointments", headers=client.csrf, json=appointment_payload(
        pts["a1b"], scheduledDate=DAY, scheduledTime="13:00")).json()["id"]
    response = _delete_record(client, ordinary)
    assert response.status_code == 200 and response.json()["historyPreserved"] is False
    assert _row(ordinary) is None
    assert _unlinked_reasons() == []


def test_cancelling_a_backdated_record_audit_outage_rolls_back(client):
    _, pts = _world(client)
    _historical(client)
    login(client, "head.1")
    aid = _backdate(client, pts["a1"]).json()["id"]
    before = _row(aid)
    _fail_audit("consilium_appointment_deleted")
    response = _delete_record(client, aid)
    assert response.status_code == 503
    assert _row(aid) == before


def _ts(value):
    from datetime import datetime
    return datetime.fromisoformat(value)


def _wait_for_lock(fn):
    """Run fn while another connection holds the write lock; returns the
    response and the moment the lock was released."""
    from datetime import datetime, timezone
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    box, done, thread = _in_thread(fn)
    time.sleep(0.6)
    waited = not done.is_set()
    released = datetime.now(timezone.utc)
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    return box["response"], released, waited


def test_backdated_record_created_at_is_taken_after_the_lock(client):
    """B3-02: the real time of entry is taken inside the transaction after
    the lock: waiting for another writer does not make the record look older
    than its own reason."""
    _, pts = _world(client)
    _historical(client)
    head = _client_for(client, "head.1")
    response, released, waited = _wait_for_lock(lambda: _backdate(head, pts["a1"]))
    assert waited and response.status_code == 201, response.text
    row = _row(response.json()["id"])
    reason = _rows("SELECT * FROM consilium_backdating_reasons WHERE id = ?", row["backdating_reason_id"])[0]
    assert _ts(row["created_at"]) >= released
    assert _ts(row["created_at"]) >= _ts(reason["created_at"])
    assert row["created_at"] == row["updated_at"] == reason["created_at"] == response.json()["createdAt"]


def test_historical_slot_created_at_is_taken_after_the_lock(client):
    _world(client)
    chair = _client_for(client, "chair")
    response, released, waited = _wait_for_lock(lambda: _slot(chair))
    assert waited and response.status_code == 201, response.text
    row = _rows("SELECT * FROM consilium_schedule_slots WHERE id = ?", response.json()["id"])[0]
    reason = _rows("SELECT * FROM consilium_backdating_reasons WHERE id = ?", row["backdating_reason_id"])[0]
    assert _ts(row["created_at"]) >= released and row["created_at"] == reason["created_at"]


def _moscow_formatter_js():
    from .conftest import PROJECT_ROOT
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = js.index("    formatMoscowDateTime(iso) {")
    return js[start:js.index("\n    },\n", start) + 7]


MOSCOW_CASES = {
    "2026-10-20T16:56:00+00:00": "20.10.2026, 19:56 МСК",       # сегодня
    "2026-09-15T07:05:09.123456+00:00": "15.09.2026, 10:05 МСК",  # старше двух дней — с часами
    "2024-01-02T21:30:00+00:00": "03.01.2024, 00:30 МСК",       # переход суток по Москве
    "2026-03-29T23:59:00+03:00": "29.03.2026, 23:59 МСК",
    "": "—",
    "не дата": "—",
}


@pytest.mark.parametrize("tz", ["Europe/Moscow", "UTC", "America/New_York", "America/Los_Angeles",
                                "Asia/Tokyo", "Asia/Kolkata", "Pacific/Kiritimati", "Pacific/Pago_Pago"])
@pytest.mark.parametrize("intl", ["native", "missing-tz-data"])
def test_backdating_times_are_shown_in_moscow_for_any_browser_zone(tz, intl):
    """B3-03: full date and time in Europe/Moscow regardless of the browser
    zone and of how old the moment is; without tz data — fixed UTC+3."""
    import os
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    breaker = ("const Original = Intl.DateTimeFormat; Intl.DateTimeFormat = function (l, o) {"
               " if (o && o.timeZone) throw new RangeError('no tz'); return new Original(l, o); };"
               if intl == "missing-tz-data" else "")
    script = (breaker + "const app = {" + _moscow_formatter_js() + "};"
              + "process.stdout.write(JSON.stringify(" + json.dumps(list(MOSCOW_CASES))
              + ".map(v => app.formatMoscowDateTime(v))));")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "TZ": tz}, check=True).stdout
    assert json.loads(out) == list(MOSCOW_CASES.values())


def test_backdating_details_use_the_moscow_formatter_only_there():
    """B3-03: the details and the reasons use the dedicated formatter; the
    shared formatDateTime() is left as it was."""
    import subprocess
    from .conftest import PROJECT_ROOT
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "readonlyField('Внесено в систему', this.formatMoscowDateTime(item.createdAt))" in js
    block = js[js.index("    async showConsiliumBackdatingReasons("):]
    block = block[:block.index("\n    },\n")]
    assert "formatMoscowDateTime(reason.createdAt)" in block and "formatDateTime(" not in block.replace(
        "formatMoscowDateTime(", "")
    assert js.count("formatMoscowDateTime(") == 3          # определение и два вызова
    def shared(source):
        start = source.index("    formatDateTime(iso) {")
        return source[start:source.index("\n    },\n", start)]
    try:
        base = subprocess.run(["git", "-C", str(PROJECT_ROOT), "show", "9b0edbc:static/app.js"],
                              capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("base commit is not available")
    assert shared(js) == shared(base)
