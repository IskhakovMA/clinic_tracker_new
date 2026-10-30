"""Independent review of the combined pre-audit integration (schema v27): fixes.

1. v27 NOCASE rebuild of patient_portal_access: a case-only address conflict is
   found before anything is dropped; the migration stops with a controlled error,
   the whole transaction is rolled back and the file stays byte-identical.
2. Mobile access link / change / revoke follow the WP2.3–2.4 write standard:
   rights re-read under BEGIN IMMEDIATE, required audit in the same transaction,
   safe concurrent and repeated requests, no disclosure of addresses or cards.
3. Mobile access and erroneous cards (owner decision): linked/active block the
   mark, revoked does not, no automatic revoke, the access row is immutable after
   a mark by any path; a separate dependency code, checked under the mark lock.
4. Restore keeps only the exactly verified portal-a3fbe00 v10 structure; the
   init_db version chain has no duplicate branches.
Synthetic data only.
"""
import ast
import hashlib
import json
import logging
import sqlite3
import uuid

import pytest

from .conftest import card_version, DEV_USERNAME, PROJECT_ROOT, get_conn, make_patient_payload
from .test_restore_migration import _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _parallel, _rows
from .test_wp24a_patient_create import _while_locked, _world
from .test_wp24d_duplicate_review import (_audit, _candidate_for, _consilium, _detail, _erroneous, _mark, _pair,
                                          _restore_audit, _scan, _state, _unmark)

FIXTURES = PROJECT_ROOT / "backend" / "tests" / "fixtures"
PORTAL_V10 = FIXTURES / "portal_v10_synthetic.sql"
P1, P2, P3 = "253f68a6d5e3456eaa909671711dd7c6", "2d45a20bd03f4b70956ce3e9aa0876fb", "9933f10e185d4f2093096bc57283b988"
PORTAL_COLUMNS = ("patient_id, email_normalized, status, linked_at, linked_by_user_id, activated_at, "
                  "enrollment_state, last_login_at, revoked_at, revoked_by_user_id, updated_at")
PRE_NOCASE_DDL = """CREATE TABLE patient_portal_access_old (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('linked', 'active', 'revoked')),
    linked_at TEXT NOT NULL,
    linked_by_user_id TEXT REFERENCES users(id),
    activated_at TEXT,
    enrollment_state TEXT NOT NULL DEFAULT 'none'
        CHECK (enrollment_state IN ('none', 'totp_enrolled')),
    last_login_at TEXT,
    revoked_at TEXT,
    revoked_by_user_id TEXT REFERENCES users(id),
    updated_at TEXT NOT NULL,
    CHECK (status <> 'revoked' OR revoked_at IS NOT NULL)
)"""
PORTAL_GUARDS = {f"trg_erroneous_readonly_patient_portal_access_{op}" for op in ("insert", "update", "delete")}
MARK_TRIGGER = "trg_erroneous_mark_requires_no_mobile_access"


# ================================================================ 1. v27 NOCASE rebuild

def _pre_nocase(conn, extra=()):
    """The table as an early (pre-release) v10 build created it: UNIQUE without
    COLLATE NOCASE. Same rows as the fixture plus `extra`."""
    conn.execute(PRE_NOCASE_DDL)
    conn.execute(f"INSERT INTO patient_portal_access_old ({PORTAL_COLUMNS}) "  # nosec - test constants
                 f"SELECT {PORTAL_COLUMNS} FROM patient_portal_access")
    conn.execute("DROP TABLE patient_portal_access")
    conn.execute("ALTER TABLE patient_portal_access_old RENAME TO patient_portal_access")
    for row in extra:
        conn.execute(f"INSERT INTO patient_portal_access ({PORTAL_COLUMNS}) VALUES ({', '.join('?' * 11)})", row)


def _row(pid, email, status="linked", **cols):
    values = {"patient_id": pid, "email_normalized": email, "status": status, "linked_at": "2026-09-01T09:00:00+00:00",
              "linked_by_user_id": None, "activated_at": None, "enrollment_state": "none", "last_login_at": None,
              "revoked_at": None, "revoked_by_user_id": None, "updated_at": "2026-09-01T09:00:00+00:00", **cols}
    return tuple(values[c.strip()] for c in PORTAL_COLUMNS.split(","))


CONFLICT = (_row(P3, "Patient.One@Example.test", "active", activated_at="2026-09-02T09:00:00+00:00",
                 enrollment_state="totp_enrolled", last_login_at="2026-09-03T09:00:00+00:00"),)


def _db_file(tmp_path, extra=()):
    return _sqlite_from_fixture(tmp_path / "pre-nocase-v10.sqlite", fixture=PORTAL_V10,
                                edit=lambda c: _pre_nocase(c, extra))


def _open(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portal_rows(conn):
    return [tuple(r) for r in conn.execute(f"SELECT {PORTAL_COLUMNS} FROM patient_portal_access ORDER BY patient_id")]  # nosec


def _table_sql(conn):
    return conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patient_portal_access'").fetchone()[0]


def test_case_only_conflict_stops_the_migration_before_anything_changes(client, tmp_path):
    from backend.app import db
    path = _db_file(tmp_path, CONFLICT)
    digest = _sha(path)
    conn = _open(path)
    rows, schema = _portal_rows(conn), sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    assert len(rows) == 3 and "NOCASE" not in _table_sql(conn).upper()
    with pytest.raises(db.MigrationConflictError) as caught:
        db.init_db(conn)
    assert isinstance(caught.value, db.SchemaVersionError)              # start-up refuses it like an unknown schema
    message = str(caught.value)
    assert "1 адрес(а)" in message and "2 карточек" in message and "База не изменена" in message
    assert "@" not in message and "patient.one" not in message.lower() and P1 not in message and P3 not in message
    assert not conn.in_transaction
    assert _portal_rows(conn) == rows                                   # every row, every value kept
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == schema
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 10
    conn.close()
    assert _sha(path) == digest                                         # the file is byte-identical


def test_repeated_runs_refuse_again_and_never_lose_a_row(client, tmp_path):
    from backend.app import db
    path = _db_file(tmp_path, CONFLICT)
    digest = _sha(path)
    for _ in range(3):
        conn = _open(path)
        with pytest.raises(db.MigrationConflictError):
            db.init_db(conn)
        assert len(_portal_rows(conn)) == 3
        conn.close()
        assert _sha(path) == digest


def test_startup_refuses_a_conflicting_database(client, tmp_path, monkeypatch, caplog):
    """The production connection (WAL, foreign keys) — as the live file is opened."""
    from backend.app import db
    path = _db_file(tmp_path, CONFLICT)
    wal = sqlite3.connect(str(path))
    assert wal.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    wal.close()
    digest = _sha(path)
    conn = db._connect(path)
    with caplog.at_level(logging.DEBUG), pytest.raises(db.MigrationConflictError):
        db.init_db(conn)
    conn.close()
    assert _sha(path) == digest
    assert "example.test" not in caplog.text.lower()


def test_migration_succeeds_once_a_person_resolves_the_conflict(client, tmp_path):
    from backend.app import config, db
    path = _db_file(tmp_path, CONFLICT)
    conn = _open(path)
    with pytest.raises(db.MigrationConflictError):
        db.init_db(conn)
    # Решение принимает человек: адрес второй карточки исправлен, ни одна запись не удалена.
    conn.execute("UPDATE patient_portal_access SET email_normalized = 'patient.three@example.test' WHERE patient_id = ?", (P3,))
    conn.commit()
    rows = _portal_rows(conn)
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 30   # WP-C: текущая схема v28
    assert _portal_rows(conn) == rows
    assert "COLLATE NOCASE" in _table_sql(conn).upper()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name IN "
                                        "('patient_portal_access', 'patient_erroneous_marks')")}
    assert PORTAL_GUARDS | {MARK_TRIGGER, "idx_portal_access_status"} <= names
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    schema = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    db.init_db(conn)                                                    # repeated run changes nothing
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == schema
    assert _portal_rows(conn) == rows
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE patient_portal_access SET email_normalized = 'PATIENT.ONE@EXAMPLE.TEST' WHERE patient_id = ?", (P3,))
    conn.close()


def test_pre_nocase_table_without_conflict_keeps_every_value(client, tmp_path):
    from backend.app import db
    extra = (_row(P3, "patient.three@example.test", "active", activated_at="2026-09-02T09:00:00+00:00",
                  enrollment_state="totp_enrolled", last_login_at="2026-09-03T09:00:00+00:00"),)
    conn = _open(_db_file(tmp_path, extra))
    rows = _portal_rows(conn)
    db.init_db(conn)
    assert _portal_rows(conn) == rows and len(rows) == 3
    assert "COLLATE NOCASE" in _table_sql(conn).upper()
    conn.close()


def test_conflict_check_follows_the_column_collation(client, tmp_path):
    """Only what UNIQUE COLLATE NOCASE would reject counts as a conflict: different
    addresses are migrated even when they look alike."""
    from backend.app import db
    extra = (_row(P3, "patient.one@example.test.", "linked"),)
    conn = _open(_db_file(tmp_path, extra))
    db.init_db(conn)
    assert len(_portal_rows(conn)) == 3
    conn.close()


def test_init_db_version_chain_has_no_duplicate_branches():
    tree = ast.parse((PROJECT_ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8"))
    init = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "init_db")
    versions = [node.test.comparators[0].value for node in ast.walk(init)
                if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.ops[0], ast.Eq) and isinstance(node.test.comparators[0], ast.Constant)]
    assert versions == sorted(set(versions)) and versions == list(range(1, 22))


# ================================================================ 4. restore: only the verified v10 build

def _dump(tmp_path, edit, name):
    path = _sqlite_from_fixture(tmp_path / f"{name}.sqlite", fixture=PORTAL_V10, edit=edit)
    conn = sqlite3.connect(str(path))
    sql = tmp_path / f"{name}.sql"
    sql.write_text("\n".join(conn.iterdump()), encoding="utf-8")
    conn.close()
    return sql


@pytest.mark.parametrize("name,edit", [
    ("pre_nocase", lambda c: _pre_nocase(c)),
    ("pre_nocase_conflict", lambda c: _pre_nocase(c, CONFLICT)),
    ("no_cascade", lambda c: (c.execute(PRE_NOCASE_DDL.replace(" ON DELETE CASCADE", "").replace(
        "UNIQUE,", "UNIQUE COLLATE NOCASE,")), c.execute("DROP TABLE patient_portal_access"),
        c.execute("ALTER TABLE patient_portal_access_old RENAME TO patient_portal_access"))),
])
def test_modified_v10_portal_structures_are_refused(client, tmp_path, backup_key, name, edit):
    from backend.app import maintenance
    fixture = _dump(tmp_path, edit, name)
    plain = _sqlite_from_fixture(tmp_path / f"{name}-check.sqlite", fixture=fixture)
    with pytest.raises(maintenance.MaintenanceError, match="не совпадает ни с одной"):
        maintenance.identify_backup_build(plain, 10)
    backup = _v9_backup(tmp_path, f"{name}.cbk", header=10, fixture=fixture)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.validate_backup(backup)


def test_verified_portal_build_is_still_recognised(client, tmp_path):
    from backend.app import maintenance
    plain = _sqlite_from_fixture(tmp_path / "a3f.sqlite", fixture=PORTAL_V10)
    assert maintenance.identify_backup_build(plain, 10) == "portal-a3fbe00"
    builds = {b["id"]: b for b in maintenance.restore_builds()}
    verified = builds["portal-a3fbe00"]["strict_tables"]["patient_portal_access"]
    current = builds["current"]["strict_tables"]["patient_portal_access"]
    # Таблица та же; v27 добавляет только свои триггеры (защита помеченной карточки, запрет пометки).
    assert {k: v for k, v in verified.items() if k != "triggers"} == {k: v for k, v in current.items() if k != "triggers"}
    assert verified["triggers"] == []
    names = sorted(t.split()[2] for t in current["triggers"])
    # WP-C (v28): защита удаления карточки проверяет и мобильный доступ — её текст ссылается на таблицу.
    assert names == sorted(PORTAL_GUARDS | {MARK_TRIGGER, "trg_patients_delete_requires_no_history"})


# ================================================================ 2. link / change / revoke

def _link(tab, pid, email):
    return tab.put(f"/api/patients/{pid}/portal-access", json={"email": email}, headers=tab.csrf)


def _revoke(tab, pid):
    return tab.delete(f"/api/patients/{pid}/portal-access", headers=tab.csrf)


def _access(pid):
    rows = _rows("SELECT * FROM patient_portal_access WHERE patient_id = ?", pid)
    return rows[0] if rows else None


def _portal_audit():
    return _rows("SELECT event_type, result, patient_id, detail FROM audit_events "
                 "WHERE event_type LIKE 'portal_access_%' ORDER BY id")


def _cards(client, n=2):
    ids = _world()
    head = _client_for(client, "head.1")
    pids = []
    for i in range(n):
        r = head.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"],
                                                                  policyNumber=f"77000000000271{i:02d}",
                                                                  name=f"Синтетик Портальный {i}"), headers=head.csrf)
        assert r.status_code == 201, r.text
        pids.append(r.json()["id"])
    return ids, head, pids


@pytest.mark.parametrize("username,expected", [("doc.a", 403), ("caop", 403), ("deputy", 403),
                                               ("doc.a2", 404), ("chair", 404), ("doc.b", 404)])
def test_roles_and_scope_that_cannot_link(client, username, expected):
    ids, head, (pid, _) = _cards(client)
    tab = _client_for(client, username)
    response = _link(tab, pid, "denied.case@example.test")
    assert response.status_code == expected
    assert _access(pid) is None and "denied.case" not in response.text
    if expected == 403:
        assert _rows("SELECT detail FROM audit_events WHERE event_type = 'access_denied' ORDER BY id")[-1]["detail"] \
            == "action=portal_access_manage"


def test_revoke_roles_and_scope(client):
    ids, head, (pid, _) = _cards(client)
    assert _link(head, pid, "revoke.roles@example.test").status_code == 200
    for username, expected in (("doc.b", 404), ("caop", 403), ("deputy", 403)):
        assert _revoke(_client_for(client, username), pid).status_code == expected, username
        assert _access(pid)["status"] == "linked"
    assert _revoke(_client_for(client, "doc.a"), pid).status_code == 200    # лечащий врач может отключить
    assert _access(pid)["status"] == "revoked"


# Смена области: карточка переходит к врачу другого отделения, пока запрос ждет блокировку.
MOVE_CARD = "UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?"


@pytest.mark.parametrize("change,expected", [
    ("UPDATE users SET role_id = 'doctor' WHERE id = ?", 403),
    ("UPDATE users SET status = 'blocked' WHERE id = ?", 403),
    (MOVE_CARD, 404),
])
def test_rights_are_rechecked_under_the_write_lock(client, change, expected):
    ids, head, (pid, _) = _cards(client)
    params = (ids["doc.b"], pid) if change == MOVE_CARD else (ids["head.1"],)
    response, waited = _while_locked(lambda: _link(head, pid, "locked.case@example.test"), change, params)
    assert waited and response.status_code == expected and _access(pid) is None
    assert not [r for r in _portal_audit() if r["event_type"] == "portal_access_linked"]


@pytest.mark.parametrize("change,expected", [
    ("UPDATE users SET status = 'blocked' WHERE id = ?", 403),
    (MOVE_CARD, 404),
])
def test_revoke_rights_are_rechecked_under_the_write_lock(client, change, expected):
    ids, head, (pid, _) = _cards(client)
    assert _link(head, pid, "locked.revoke@example.test").status_code == 200
    doctor = _client_for(client, "doc.a")
    params = (ids["doc.b"], pid) if change == MOVE_CARD else (ids["doc.a"],)
    response, waited = _while_locked(lambda: _revoke(doctor, pid), change, params)
    assert waited and response.status_code == expected and _access(pid)["status"] == "linked"


def test_audit_failure_rolls_back_link_change_revoke_and_conflict(client):
    ids, head, (pid, other) = _cards(client)
    _fail_audit("portal_access_linked")
    assert _link(head, pid, "audit.case@example.test").status_code == 503 and _access(pid) is None
    _restore_audit()
    assert _link(head, pid, "audit.case@example.test").status_code == 200
    before = _access(pid)
    _fail_audit("portal_access_email_changed")
    assert _link(head, pid, "audit.other@example.test").status_code == 503 and _access(pid) == before
    _restore_audit()
    _fail_audit("portal_access_revoked")
    assert _revoke(head, pid).status_code == 503 and _access(pid) == before
    _restore_audit()
    _fail_audit("portal_access_conflict")
    response = _link(head, other, "audit.case@example.test")
    assert response.status_code == 503 and _access(other) is None and "audit.case" not in response.text
    _restore_audit()
    assert [r["event_type"] for r in _portal_audit()] == ["portal_access_linked"]


@pytest.mark.parametrize("repeat", range(3))
def test_one_address_two_cards_at_once(client, repeat):
    ids, head, (p1, p2) = _cards(client)
    other = _client_for(client, "head.1")
    res = _parallel(lambda: _link(head, p1, "race.case@example.test"), lambda: _link(other, p2, "Race.Case@Example.test"))
    codes = sorted(r.status_code for r, *_ in res)
    assert codes == [200, 409], [r.text for r, *_ in res]
    assert _rows("SELECT COUNT(*) AS c FROM patient_portal_access")[0]["c"] == 1
    events = [r["event_type"] for r in _portal_audit()]
    assert sorted(events) == ["portal_access_conflict", "portal_access_linked"]
    loser = next(r for r, *_ in res if r.status_code == 409)
    assert "race.case" not in loser.text.lower() and p1 not in loser.text and p2 not in loser.text
    assert "Синтетик" not in loser.text


@pytest.mark.parametrize("repeat", range(3))
def test_the_same_request_twice_at_once(client, repeat):
    ids, head, (pid, _) = _cards(client)
    other = _client_for(client, "head.1")
    res = _parallel(lambda: _link(head, pid, "twice.case@example.test"), lambda: _link(other, pid, "twice.case@example.test"))
    assert [r.status_code for r, *_ in res] == [200, 200]
    assert [r["event_type"] for r in _portal_audit()] == ["portal_access_linked"]
    assert res[0][0].json()["access"] == res[1][0].json()["access"]


@pytest.mark.parametrize("repeat", range(3))
def test_link_and_revoke_at_once_end_consistent(client, repeat):
    ids, head, (pid, _) = _cards(client)
    assert _link(head, pid, "first.case@example.test").status_code == 200
    doctor = _client_for(client, "doc.a")
    res = _parallel(lambda: _link(head, pid, "second.case@example.test"), lambda: _revoke(doctor, pid))
    assert [r.status_code for r, *_ in res] == [200, 200]
    row = _access(pid)
    events = [r["event_type"] for r in _portal_audit()][1:]
    assert sorted(events) == ["portal_access_email_changed", "portal_access_revoked"]
    expected_status = "linked" if events[-1] == "portal_access_email_changed" else "revoked"
    assert row["status"] == expected_status and row["email_normalized"] == "second.case@example.test"


def test_resending_the_same_address_keeps_activation(client):
    ids, head, (pid, _) = _cards(client)
    assert _link(head, pid, "resend.case@example.test").status_code == 200
    get_conn().execute("UPDATE patient_portal_access SET status = 'active', activated_at = '2026-10-01T09:00:00+00:00', "
                       "enrollment_state = 'totp_enrolled', last_login_at = '2026-10-02T09:00:00+00:00' WHERE patient_id = ?",
                       (pid,))
    get_conn().commit()
    before = _access(pid)
    for email in ("resend.case@example.test", " RESEND.Case@example.test "):
        response = _link(head, pid, email)
        assert response.status_code == 200 and response.json()["access"]["status"] == "active"
        assert _access(pid) == before                                    # activation, login and enrollment kept
    assert [r["event_type"] for r in _portal_audit()] == ["portal_access_linked"]
    # Новый адрес — это смена владельца адреса: активация начинается заново.
    assert _link(head, pid, "resend.new@example.test").status_code == 200
    row = _access(pid)
    assert (row["status"], row["activated_at"], row["enrollment_state"], row["last_login_at"]) == ("linked", None, "none", None)


def test_relinking_a_revoked_access_and_repeated_revoke(client):
    ids, head, (pid, _) = _cards(client)
    assert _link(head, pid, "revoked.case@example.test").status_code == 200
    assert _revoke(head, pid).status_code == 200
    revoked = _access(pid)
    assert _revoke(head, pid).status_code == 200 and _access(pid) == revoked     # repeat: nothing changes
    assert [r["event_type"] for r in _portal_audit()] == ["portal_access_linked", "portal_access_revoked"]
    response = _link(head, pid, "revoked.case@example.test")
    assert response.status_code == 200 and _access(pid)["status"] == "linked"
    assert _portal_audit()[-1]["detail"] == "status=linked previous=revoked email_changed=no restored=yes"
    assert _revoke(_client_for(client, "doc.a"), "0" * 32).status_code == 404


def test_revoke_without_access_is_not_found_and_writes_nothing(client):
    ids, head, (pid, _) = _cards(client)
    response = _revoke(head, pid)
    assert response.status_code == 404 and response.json()["detail"] == "Мобильный доступ не настроен"
    assert _portal_audit() == []


def test_no_address_or_other_card_leaks_through_errors_audit_or_logs(client, caplog):
    ids, head, (p1, p2) = _cards(client)
    with caplog.at_level(logging.DEBUG):
        assert _link(head, p1, "leak.case@example.test").status_code == 200
        conflict = _link(head, p2, "LEAK.case@example.test")
        invalid = _link(head, p2, "leak.invalid@@example.test")
    assert conflict.status_code == 409 and invalid.status_code == 422
    for response in (conflict, invalid):
        assert "leak." not in response.text.lower() and p1 not in response.text and "Синтетик" not in response.text
    assert "leak." not in caplog.text.lower()
    details = json.dumps(_rows("SELECT detail, patient_id FROM audit_events"), ensure_ascii=False).lower()
    assert "leak." not in details and "@" not in details
    conflict_row = [r for r in _portal_audit() if r["event_type"] == "portal_access_conflict"][0]
    assert conflict_row["patient_id"] == p2 and conflict_row["result"] == "denied"
    shown = head.get(f"/api/patients/{p1}/portal-access").json()["access"]
    assert "leak.case@example.test" not in json.dumps(shown)


def test_validation_errors_elsewhere_also_exclude_input(client):
    ids, head, (pid, _) = _cards(client)
    response = head.patch(f"/api/patients/{pid}", json={"expectedVersion": card_version(pid), "field": "unknown", "value": "x"}, headers=head.csrf)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"] and all("input" not in error and "ctx" not in error for error in body["detail"])
    assert any(error["loc"] == ["body", "field"] for error in body["detail"])


# ================================================================ 3. mobile access and erroneous cards

def _registry(client, *, access=None):
    """Pair a (doc.a) / b (doc.b) with one policy; optional access for b (via the
    developer, whose scope covers b); scan. Returns (ids, a, b, cid, dev, deputy)."""
    ids = _world()
    a, b = _pair(ids)
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    if access:
        assert _link(dev, b, "mark.case@example.test").status_code == 200
        if access == "active":
            get_conn().execute("UPDATE patient_portal_access SET status = 'active', activated_at = '2026-10-01' "
                               "WHERE patient_id = ?", (b,))
            get_conn().commit()
        elif access == "revoked":
            assert _revoke(dev, b).status_code == 200
    _scan(dev)
    return ids, a, b, _candidate_for(a, b)["id"], dev, deputy


@pytest.mark.parametrize("access", ["linked", "active"])
def test_access_in_force_blocks_the_mark_without_touching_it(client, access):
    ids, a, b, cid, dev, deputy = _registry(client, access=access)
    detail = _detail(deputy, cid).json()
    card = [p for p in detail["patients"] if p["id"] == b][0]
    assert {"code": "mobile_access", "kind": "access", "count": 1,
            "label": "Действующий мобильный доступ пациента (адрес привязан или доступ активен)"} in card["dependencies"]
    assert all(d["kind"] == "clinical" for d in card["dependencies"] if d["code"] != "mobile_access")
    assert b not in detail["allowedActions"]["markPatientIds"]
    row, state = _access(b), _state(cid)
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "MOBILE_ACCESS_IN_FORCE"
    assert response.json()["dependencies"] == ["mobile_access"] and "mark.case" not in response.text
    assert _erroneous() == set() and _rows("SELECT * FROM patient_erroneous_marks") == []
    assert _access(b) == row                                             # no automatic revoke
    assert _state(cid) == state                                          # not a clinical case: state unchanged
    refused = _audit("patient_mark_refused")[-1]
    assert refused["patient_id"] == b and refused["detail"] == f"candidate_id={cid} dependencies=mobile_access"
    # После отключения доступа сотрудником пометка возможна.
    assert _revoke(_client_for(client, "doc.b"), b).status_code == 200
    assert _mark(deputy, cid, b).status_code == 200 and _erroneous() == {b}


def test_revoked_access_does_not_block_and_stays_as_it_was(client):
    ids, a, b, cid, dev, deputy = _registry(client, access="revoked")
    card = [p for p in _detail(deputy, cid).json()["patients"] if p["id"] == b][0]
    assert card["dependencies"] == []
    row = _access(b)
    assert _mark(deputy, cid, b).status_code == 200 and _erroneous() == {b}
    assert _access(b) == row


def test_clinical_and_access_dependencies_are_reported_separately(client):
    ids = _world()
    a, b = _pair(ids)
    _consilium(b, ids["doc.b"])
    get_conn().commit()
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    assert _link(dev, b, "both.case@example.test").status_code == 200
    _scan(dev)
    cid = _candidate_for(a, b)["id"]
    response = _mark(deputy, cid, b)
    assert response.status_code == 409 and response.json()["code"] == "CLINICAL_DEPENDENCIES"
    assert response.json()["dependencies"] == ["consilium_records", "mobile_access"]
    assert _state(cid) == "needs_clinical_review"
    assert _audit("patient_mark_refused")[-1]["detail"] == f"candidate_id={cid} dependencies=consilium_records,mobile_access"


def test_access_row_of_a_marked_card_cannot_change_by_any_path(client):
    ids, a, b, cid, dev, deputy = _registry(client, access="revoked")
    assert _mark(deputy, cid, b).status_code == 200
    row = _access(b)
    for sql, params in (
        ("UPDATE patient_portal_access SET status = 'linked', revoked_at = NULL WHERE patient_id = ?", (b,)),
        ("UPDATE patient_portal_access SET email_normalized = 'x@example.test' WHERE patient_id = ?", (b,)),
        ("DELETE FROM patient_portal_access WHERE patient_id = ?", (b,)),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
            get_conn().execute(sql, params)
        get_conn().rollback()
    for tab in (dev, _client_for(client, "doc.b")):
        assert _link(tab, b, "after.mark@example.test").status_code == 404
        assert _revoke(tab, b).status_code == 404
        assert tab.get(f"/api/patients/{b}/portal-access").status_code == 404
    assert _access(b) == row
    # Карточка без строки доступа: новую строку тоже не вставить.
    ids2 = {"doc.a": ids["doc.a"], "doc.b": ids["doc.b"]}
    a2, b2 = _pair(ids2, policy="7700000000052001")
    _scan(dev)
    cid2 = _candidate_for(a2, b2)["id"]
    assert _mark(deputy, cid2, b2).status_code == 200
    with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
        get_conn().execute(f"INSERT INTO patient_portal_access ({PORTAL_COLUMNS}) VALUES ({', '.join('?' * 11)})",  # nosec
                           _row(b2, "inserted@example.test"))
    get_conn().rollback()
    # Снятие пометки возвращает обычную работу с доступом.
    mark_id = _rows("SELECT mark_id FROM patient_erroneous_state WHERE patient_id = ?", b)[0]["mark_id"]
    assert _unmark(deputy, b, mark_id).status_code == 200
    assert _link(dev, b, "mark.case@example.test").status_code == 200 and _access(b)["status"] == "linked"


def test_database_refuses_a_mark_while_access_is_in_force(client):
    ids, a, b, cid, dev, deputy = _registry(client, access="linked")
    with pytest.raises(sqlite3.IntegrityError, match="patient_mobile_access_in_force"):
        get_conn().execute("INSERT INTO patient_erroneous_marks (id, patient_id, candidate_id, action, reason, by_user_id, at) "
                           "VALUES (?, ?, ?, 'mark', 'x', ?, '2026-10-01')", (uuid.uuid4().hex, b, cid, ids["deputy"]))
    get_conn().rollback()
    assert _erroneous() == set()


def _sql_link(pid):
    return (f"INSERT INTO patient_portal_access ({PORTAL_COLUMNS}) VALUES ({', '.join('?' * 11)})",  # nosec
            _row(pid, "race.mark@example.test"))


def _sql_mark(ids, pid, cid):
    return ("INSERT INTO patient_erroneous_marks (id, patient_id, candidate_id, action, reason, by_user_id, at) "
            "VALUES (?, ?, ?, 'mark', 'Синтетическая причина', ?, '2026-10-01')", (uuid.uuid4().hex, pid, cid, ids["deputy"]))


@pytest.mark.parametrize("repeat", range(3))
def test_mark_waits_for_a_new_link(client, repeat):
    ids, a, b, cid, dev, deputy = _registry(client)
    sql, params = _sql_link(b)
    response, waited = _while_locked(lambda: _mark(deputy, cid, b), sql, params)
    assert waited and response.status_code == 409 and response.json()["code"] == "MOBILE_ACCESS_IN_FORCE"
    assert _erroneous() == set() and _access(b)["status"] == "linked"


@pytest.mark.parametrize("repeat", range(3))
def test_link_waits_for_a_new_mark(client, repeat):
    ids, a, b, cid, dev, deputy = _registry(client)
    sql, params = _sql_mark(ids, b, cid)
    response, waited = _while_locked(lambda: _link(dev, b, "late.link@example.test"), sql, params)
    assert waited and response.status_code == 404 and "late.link" not in response.text
    assert _erroneous() == {b} and _access(b) is None


@pytest.mark.parametrize("repeat", range(3))
def test_mark_waits_for_a_revoke(client, repeat):
    ids, a, b, cid, dev, deputy = _registry(client, access="linked")
    response, waited = _while_locked(
        lambda: _mark(deputy, cid, b),
        "UPDATE patient_portal_access SET status = 'revoked', revoked_at = '2026-10-01' WHERE patient_id = ?", (b,))
    assert waited and response.status_code == 200 and _erroneous() == {b}
    assert _access(b)["status"] == "revoked"


@pytest.mark.parametrize("repeat", range(4))
def test_mark_link_and_revoke_at_once_never_leave_access_on_a_marked_card(client, repeat):
    ids, a, b, cid, dev, deputy = _registry(client, access="linked")
    doctor = _client_for(client, "doc.b")
    (mark, *_), (revoke, *_) = _parallel(lambda: _mark(deputy, cid, b), lambda: _revoke(doctor, b))
    assert revoke.status_code == 200 and _access(b)["status"] == "revoked"
    if mark.status_code == 200:                                          # revoke committed first
        assert _erroneous() == {b}
    else:                                                                # mark saw the access in force
        assert mark.status_code == 409 and mark.json()["code"] == "MOBILE_ACCESS_IN_FORCE" and _erroneous() == set()
        (mark, *_), (link, *_) = _parallel(lambda: _mark(deputy, cid, b), lambda: _link(dev, b, "again.case@example.test"))
        assert sorted((mark.status_code, link.status_code)) in ([200, 404], [200, 409])
        if mark.status_code == 200:
            assert link.status_code == 404 and _access(b)["status"] == "revoked"
        else:
            assert mark.json()["code"] == "MOBILE_ACCESS_IN_FORCE" and _access(b)["status"] == "linked"
    assert not (_erroneous() and _access(b)["status"] in ("linked", "active"))
