"""WP2.3-B2, B2-01: the response of a committed change matches the change.

A treating doctor reassignment (or a head losing a department) is committed
by a separate SQLite connection exactly at the end of the operation's
transaction — after its COMMIT and before anything else happens in the
request. The permissions were checked under the lock, the change committed,
so the response must describe that change (201/200 with the record), never
null or 404. A reassignment committed before the lock refuses the operation
without any change. Synthetic data only.
"""
import sqlite3
from contextlib import contextmanager

import pytest

from .conftest import DEV_USERNAME, consilium_version, get_conn, login
from .test_consilium_appointments import appointment_payload
from .test_wp23a_scheduling_integrity import _audit_module, _consilium, _med_digest, _rows  # noqa: F401
from .test_wp23a_scheduling_integrity import _high_limits  # noqa: F401 — autouse here too
from .test_wp23b2_scheduling_rbac import DAY, NEXT_DAY, _book, _post, _return, _row, _sign, _world


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


def _commit_elsewhere(sql, params):
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        raw.execute(sql, params)
        raw.execute("COMMIT")
    finally:
        raw.close()


class _AfterCommit:
    """Run `sql` from another connection right after the next successful
    transaction of the consilium service commits (once)."""

    def __init__(self, monkeypatch, sql, params):
        self.fired = False
        cons = _consilium()
        real = cons.transaction

        @contextmanager
        def boundary(conn):
            with real(conn) as inner:
                yield inner
            # Здесь транзакция операции уже закоммичена, ответ еще не отдан.
            if not self.fired:
                self.fired = True
                _commit_elsewhere(sql, params)

        monkeypatch.setattr(cons, "transaction", boundary)


def _reassign(pts, ids, key="a1", to="doc.b"):
    return ("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids[to], pts[key]))


def _counts():
    return {
        "appointments": _rows("SELECT COUNT(*) AS c FROM oncology_consilium_appointments")[0]["c"],
        "audit": _rows("SELECT COUNT(*) AS c FROM audit_events WHERE event_type LIKE 'consilium_%'")[0]["c"],
        "resubmissions": _rows("SELECT COUNT(*) AS c FROM consilium_resubmissions")[0]["c"],
    }


def _assert_body_is_the_record(body, aid):
    """The response describes exactly the committed row."""
    row = _row(aid)
    assert body is not None and body["id"] == aid
    assert (body["version"], body["scheduledDate"], body["scheduledTime"], body["patientId"]) == (
        row["version"], row["scheduled_date"], row["scheduled_time"], row["patient_id"])
    assert body["archived"] is bool(row["archived_at"])


# ================================================================ 1–4. after the commit

def test_create_then_reassignment_returns_the_created_record(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "doc.a1")
    boundary = _AfterCommit(monkeypatch, *_reassign(pts, ids))
    response = _book(client, pts["a1"], DAY, "13:00")
    assert boundary.fired
    assert response.status_code == 201, response.text
    body = response.json()
    aid = _rows("SELECT id FROM oncology_consilium_appointments")[0]["id"]
    _assert_body_is_the_record(body, aid)
    assert body["status"] == "recorded" and body["patientName"] == "Синтетический Пациент a1"
    assert len(_rows("SELECT id FROM audit_events WHERE event_type = 'consilium_appointment_created'")) == 1
    # После передачи пациента врач больше не видит запись — новых данных ответ не раскрыл.
    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    assert client.get(f"/api/consilium/appointments/{aid}/revisions").status_code == 404


@pytest.mark.parametrize("action", ["archive", "restore"])
def test_archive_and_restore_then_reassignment_return_the_change(client, monkeypatch, action):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _sign(client, aid, back_to="doc.a1")
    if action == "restore":
        assert _post(client, aid, "archive").status_code == 200
    decision = {k: v for k, v in _row(aid).items()
                if k not in ("version", "updated_at", "archived_at", "archived_by_user_id")}
    boundary = _AfterCommit(monkeypatch, *_reassign(pts, ids))
    response = _post(client, aid, action)
    assert boundary.fired
    assert response.status_code == 200, response.text
    _assert_body_is_the_record(response.json(), aid)
    assert response.json()["archived"] is (action == "archive")
    assert len(_rows("SELECT id FROM audit_events WHERE event_type = ?",
                     "consilium_archived" if action == "archive" else "consilium_restored")) == 1
    assert {k: v for k, v in _row(aid).items() if k in decision} == decision  # подписанное решение не изменено
    # Следующий запрос уже по новому назначению.
    assert _post(client, aid, "restore" if action == "archive" else "archive").status_code == 404


def test_resubmit_then_reassignment_returns_the_change(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _return(client, aid, back_to="doc.a1")
    boundary = _AfterCommit(monkeypatch, *_reassign(pts, ids))
    response = _post(client, aid, "resubmit")
    assert boundary.fired
    assert response.status_code == 200, response.text
    _assert_body_is_the_record(response.json(), aid)
    assert response.json()["status"] == "revised"
    assert len(_rows("SELECT id FROM consilium_resubmissions WHERE appointment_id = ?", aid)) == 1
    assert len(_rows("SELECT id FROM audit_events WHERE event_type = 'consilium_resubmitted'")) == 1


def test_update_then_reassignment_returns_the_change(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    boundary = _AfterCommit(monkeypatch, *_reassign(pts, ids))
    response = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pts["a1"], scheduledDate=DAY, scheduledTime="13:30", version=consilium_version(aid)))
    assert boundary.fired and response.status_code == 200, response.text
    _assert_body_is_the_record(response.json(), aid)
    assert response.json()["scheduledTime"] == "13:30"


def test_delete_then_reassignment_returns_the_released_slot(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    boundary = _AfterCommit(monkeypatch, *_reassign(pts, ids))
    response = client.delete(f"/api/consilium/appointments/{aid}?version={consilium_version(aid)}",
                             headers=client.csrf)
    assert boundary.fired and response.status_code == 200
    assert response.json()["releasedSlot"] == {"scheduledDate": DAY, "scheduledTime": "13:00",
                                               "consiliumGroup": "thoracic"}
    assert _row(aid) is None


# ================================================================ chair operations (same gap, no scope)

@pytest.mark.parametrize("action", ["review", "sign", "return"])
def test_chair_transition_response_is_built_before_a_concurrent_change(client, monkeypatch, action):
    """A doctor cancelling the record right after the chair's commit must not
    turn the committed transition into 404."""
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    login(client, "chair")
    if action in ("sign", "return"):
        assert _post(client, aid, "review").status_code == 200
    cons, audit = _consilium(), _audit_module()
    events = {"review": "consilium_review_started", "sign": "consilium_signed",
              "return": "consilium_returned_for_revision"}
    state = {"armed": False, "fired": False}
    real_audit, real_get = audit.record_required_or_fail, cons.get_for_management

    def audited(conn, event_type, **kw):
        real_audit(conn, event_type, **kw)
        if event_type == events[action]:
            state["armed"] = True

    def get_after_commit(conn, appointment_id):
        # Первое чтение вне транзакции после записи аудита — это и есть «после коммита».
        if state["armed"] and not conn.in_transaction and not state["fired"]:
            state["fired"] = True
            _commit_elsewhere("UPDATE oncology_consilium_appointments SET deleted_at = '2026-10-01T09:00:00+00:00' "
                              "WHERE id = ? AND status = 'recorded'", (aid,))
        return real_get(conn, appointment_id)

    monkeypatch.setattr(audit, "record_required_or_fail", audited)
    monkeypatch.setattr(cons, "get_for_management", get_after_commit)
    body = {"sign": {"treatmentType": "surgery", "treatmentScheme": "", "treatmentSchemeCode": ""},
            "return": {"comment": "Уточните"}}.get(action, {})
    response = _post(client, aid, action, body)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == aid
    assert response.json()["status"] == {"review": "in_review", "sign": "signed", "return": "needs_revision"}[action]
    assert not state["fired"]  # ответ сформирован внутри транзакции — чтения после коммита нет


# ================================================================ 5. before the lock

@pytest.mark.parametrize("action", ["create", "archive", "restore", "resubmit", "update", "delete"])
def test_reassignment_before_the_lock_refuses_without_any_change(client, action):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = None
    if action != "create":
        aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
        if action in ("archive", "restore"):
            _sign(client, aid, back_to="doc.a1")
            if action == "restore":
                assert _post(client, aid, "archive").status_code == 200
        if action == "resubmit":
            _return(client, aid, back_to="doc.a1")
    before_row, before_counts, before_med = (_row(aid) if aid else None), _counts(), _med_digest(pts["a1"])
    version = consilium_version(aid) if aid else None
    _commit_elsewhere(*_reassign(pts, ids))
    calls = {
        "create": lambda: _book(client, pts["a1"], NEXT_DAY, "10:00"),
        "archive": lambda: _post(client, aid, "archive", {"version": version}),
        "restore": lambda: _post(client, aid, "restore", {"version": version}),
        "resubmit": lambda: _post(client, aid, "resubmit", {"version": version}),
        "update": lambda: client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
            pts["a1"], scheduledDate=DAY, scheduledTime="13:30", version=version)),
        "delete": lambda: client.delete(f"/api/consilium/appointments/{aid}?version={version}", headers=client.csrf),
    }
    response = calls[action]()
    assert response.status_code == 404
    assert "Синтетический" not in response.text
    assert _counts() == before_counts
    if aid:
        assert _row(aid) == before_row
    after_med = _med_digest(pts["a1"])
    before_med["patient"].pop("updated_at", None)
    after_med["patient"].pop("updated_at", None)
    before_med["patient"].pop("treating_doctor_user_id", None)
    after_med["patient"].pop("treating_doctor_user_id", None)
    before_med["patient"].pop("doctor", None)
    after_med["patient"].pop("doctor", None)
    assert after_med == before_med


# ================================================================ 6. head's departments

def test_head_archive_then_department_removed_returns_the_change(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _sign(client, aid, back_to="head.1")
    boundary = _AfterCommit(monkeypatch, "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    response = _post(client, aid, "archive")
    assert boundary.fired and response.status_code == 200, response.text
    _assert_body_is_the_record(response.json(), aid)
    assert _post(client, aid, "restore").status_code == 404   # отделения уже нет


def test_head_create_then_department_removed_returns_the_created_record(client, monkeypatch):
    ids, pts = _world(client)
    login(client, "head.1")
    boundary = _AfterCommit(monkeypatch, "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    response = _book(client, pts["a2"], DAY, "13:00")
    assert boundary.fired and response.status_code == 201, response.text
    aid = _rows("SELECT id FROM oncology_consilium_appointments")[0]["id"]
    _assert_body_is_the_record(response.json(), aid)


def test_head_department_removed_before_the_lock_refuses(client):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _sign(client, aid, back_to="head.1")
    before_row, before_counts = _row(aid), _counts()
    version = consilium_version(aid)
    _commit_elsewhere("DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    assert _post(client, aid, "archive", {"version": version}).status_code == 404
    assert _row(aid) == before_row and _counts() == before_counts
    assert _book(client, pts["a2"], NEXT_DAY, "10:00").status_code == 404


# ================================================================ 7–8. history and disclosure

def test_boundary_changes_leave_medical_history_untouched_and_disclose_nothing(client, monkeypatch):
    ids, pts = _world(client)
    before = _med_digest(pts["a1"])
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _return(client, aid, back_to="doc.a1")
    _AfterCommit(monkeypatch, *_reassign(pts, ids))
    resubmitted = _post(client, aid, "resubmit")
    assert resubmitted.status_code == 200
    # Ответ содержит только запись, на которую у врача были права в момент проверки.
    assert set(resubmitted.json()) >= {"id", "patientId", "patientName"}
    assert resubmitted.json()["patientId"] == pts["a1"]
    login(client, "doc.a1")
    listed = client.get("/api/consilium/appointments").json()["appointments"]
    assert listed == []
    slot = next(s for s in client.get("/api/consilium/schedule").json()["slots"]
                if (s["scheduledDate"], s["scheduledTime"], s["consiliumGroup"]) == (DAY, "13:00", "thoracic"))
    assert slot["appointment"] is None
    after = _med_digest(pts["a1"])
    for digest in (before, after):
        for key in ("updated_at", "treating_doctor_user_id", "doctor"):
            digest["patient"].pop(key, None)
    assert after == before
    assert not [e for e in after["events"] if e.get("consilium_decision")]
    login(client, DEV_USERNAME)
