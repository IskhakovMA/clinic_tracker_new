"""Second review of the pre-audit integration (after 234ee4c): fixes.

1. A backup whose patient_portal_access lost or changed its CHECK rules (or got
   foreign definitions) must not be recognised as the verified portal-a3fbe00
   build, must be refused by the whole restore chain before the live database
   is replaced, and check_database() must not report such a v27 schema as ok.
2. A UNIQUE conflict caught at write time (a writer that bypassed the
   service) is audited fail-closed: no audit row — no ordinary 409.
Synthetic data only.
"""
import hashlib
import sqlite3

import pytest

from .conftest import PROJECT_ROOT, get_conn, make_patient_payload
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _rows
from .test_wp24a_patient_create import _world

PORTAL_V10 = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "portal_v10_synthetic.sql"
COLUMNS = ("patient_id, email_normalized, status, linked_at, linked_by_user_id, activated_at, "
           "enrollment_state, last_login_at, revoked_at, revoked_by_user_id, updated_at")
REFERENCE_DDL = """CREATE TABLE patient_portal_access (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL UNIQUE COLLATE NOCASE,
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
STATUS_CHECK = " CHECK (status IN ('linked', 'active', 'revoked'))"
ENROLLMENT_CHECK = "\n        CHECK (enrollment_state IN ('none', 'totp_enrolled'))"
REVOKED_CHECK = ",\n    CHECK (status <> 'revoked' OR revoked_at IS NOT NULL)"


def _variant(old, new):
    assert REFERENCE_DDL.count(old) == 1, old
    return REFERENCE_DDL.replace(old, new)


VARIANTS = {
    "no_checks_at_all": _variant(STATUS_CHECK, "").replace(ENROLLMENT_CHECK, "").replace(REVOKED_CHECK, ""),
    "no_status_check": _variant(STATUS_CHECK, ""),
    "status_allows_invalid": _variant("('linked', 'active', 'revoked')", "('linked', 'active', 'revoked', 'INVALID')"),
    "status_check_weakened": _variant(STATUS_CHECK, " CHECK (status IS NOT NULL)"),
    "no_enrollment_check": _variant(ENROLLMENT_CHECK, ""),
    "enrollment_values_changed": _variant("('none', 'totp_enrolled')", "('none', 'totp_enrolled', 'sms')"),
    "no_revoked_check": _variant(REVOKED_CHECK, ""),
    "revoked_rule_changed": _variant("revoked_at IS NOT NULL)", "revoked_by_user_id IS NOT NULL)"),
    "revoked_rule_weakened": _variant("revoked_at IS NOT NULL)", "revoked_at IS NOT NULL OR revoked_by_user_id IS NULL)"),
    "foreign_extra_check": _variant(REVOKED_CHECK, REVOKED_CHECK + ",\n    CHECK (length(email_normalized) > 0)"),
    "check_hidden_in_comment": _variant(STATUS_CHECK, " /* CHECK (status IN ('linked', 'active', 'revoked')) */"),
}
FOREIGN_TRIGGER = """CREATE TRIGGER portal_rewrite AFTER INSERT ON patient_portal_access
    BEGIN UPDATE patient_portal_access SET status = 'active' WHERE patient_id = NEW.patient_id; END"""


def _replace_portal_table(conn, ddl):
    """Rebuild patient_portal_access with `ddl`, keeping every row."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TRIGGER IF EXISTS trg_erroneous_mark_requires_no_mobile_access")
    conn.execute(ddl.replace("CREATE TABLE patient_portal_access (", "CREATE TABLE portal_tmp (", 1))
    conn.execute(f"INSERT INTO portal_tmp ({COLUMNS}) SELECT {COLUMNS} FROM patient_portal_access")  # nosec - test
    conn.execute("DROP TABLE patient_portal_access")
    conn.execute("ALTER TABLE portal_tmp RENAME TO patient_portal_access")
    # Индекс пропал вместе с таблицей: возвращается тем же определением, чтобы
    # отличие было ровно в проверяемом правиле.
    conn.execute("CREATE INDEX idx_portal_access_status\n    ON patient_portal_access(status)")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sql_fixture(tmp_path, name, edit):
    path = _sqlite_from_fixture(tmp_path / f"{name}.sqlite", fixture=PORTAL_V10, edit=edit)
    conn = sqlite3.connect(str(path))
    sql = tmp_path / f"{name}.sql"
    sql.write_text("\n".join(conn.iterdump()), encoding="utf-8")
    conn.close()
    path.unlink()
    return sql


# ================================================================ 1. CHECK rules are part of the verified structure

def test_reference_ddl_is_the_verified_portal_table():
    conn = sqlite3.connect(":memory:")
    conn.executescript(PORTAL_V10.read_text(encoding="utf-8"))
    stored = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'patient_portal_access'").fetchone()[0]
    assert " ".join(stored.split()) == " ".join(REFERENCE_DDL.split())


@pytest.mark.parametrize("name", sorted(VARIANTS) + ["foreign_trigger"])
def test_modified_portal_table_is_not_the_verified_build(client, tmp_path, name):
    from backend.app import maintenance

    def edit(conn):
        if name == "foreign_trigger":
            conn.execute(FOREIGN_TRIGGER)
        else:
            _replace_portal_table(conn, VARIANTS[name])

    path = _sqlite_from_fixture(tmp_path / f"{name}.sqlite", fixture=PORTAL_V10, edit=edit)
    with pytest.raises(maintenance.MaintenanceError, match="не совпадает ни с одной"):
        maintenance.identify_backup_build(path, 10)


@pytest.mark.parametrize("name", ["no_checks_at_all", "status_allows_invalid", "revoked_rule_changed", "foreign_extra_check"])
def test_restore_refuses_a_modified_v10_before_touching_the_live_database(auth_client, tmp_path, backup_key, name):
    from backend.app import maintenance
    fixture = _sql_fixture(tmp_path, name, lambda c: _replace_portal_table(c, VARIANTS[name]))
    backup = _v9_backup(tmp_path, f"{name}.cbk", header=10, fixture=fixture)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.validate_backup(backup)
    live = _live(auth_client)
    digest = _sha(live)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert _sha(live) == digest                                         # live database not replaced
    assert maintenance.check_database(live)["ok"]


def test_reviewer_scenario_invalid_status_never_reaches_a_restored_database(auth_client, tmp_path, backup_key):
    """Без CHECK база после миграции приняла бы status = 'INVALID'."""
    from backend.app import maintenance
    fixture = _sql_fixture(tmp_path, "nochk", lambda c: _replace_portal_table(c, VARIANTS["no_checks_at_all"]))
    live = _live(auth_client)
    with pytest.raises(maintenance.MaintenanceError, match="не совпадает ни с одной"):
        maintenance.restore_database(_v9_backup(tmp_path, "nochk.cbk", header=10, fixture=fixture), live, confirm_offline=True)
    conn = sqlite3.connect(str(live))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO patient_portal_access (patient_id, email_normalized, status, linked_at, updated_at) "
                     "SELECT id, 'invalid@example.test', 'INVALID', 'now', 'now' FROM patients LIMIT 1")
    conn.close()


def _v27(tmp_path, name, edit=None):
    from backend.app import db
    path = tmp_path / f"{name}.sqlite"
    conn = db._connect(path)
    db.init_db(conn)
    if edit:
        edit(conn)
        conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return path


@pytest.mark.parametrize("name", sorted(VARIANTS) + ["foreign_trigger", "guard_trigger_missing", "mark_trigger_missing"])
def test_check_database_does_not_pass_a_v27_portal_table_without_its_rules(client, tmp_path, name):
    from backend.app import maintenance

    def edit(conn):
        if name == "foreign_trigger":
            conn.execute(FOREIGN_TRIGGER)
        elif name == "guard_trigger_missing":
            conn.execute("DROP TRIGGER trg_erroneous_readonly_patient_portal_access_update")
        elif name == "mark_trigger_missing":
            conn.execute("DROP TRIGGER trg_erroneous_mark_requires_no_mobile_access")
        else:
            _replace_portal_table(conn, VARIANTS[name])

    report = maintenance.check_database(_v27(tmp_path, name, edit))
    assert report["ok"] is False
    assert any("patient_portal_access" in p for p in report["problems"]), report["problems"]
    assert "example.test" not in str(report)


@pytest.mark.parametrize("name", ["no_checks_at_all", "status_allows_invalid"])
def test_current_version_backup_with_a_modified_portal_table_is_refused(auth_client, tmp_path, backup_key, name):
    from backend.app import maintenance
    from .test_restore_migration import _encrypt
    plain = _v27(tmp_path, name, lambda c: _replace_portal_table(c, VARIANTS[name]))
    backup = _encrypt(plain, tmp_path / f"{name}.cbk", 27)
    live = _live(auth_client)
    digest = _sha(live)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.validate_backup(backup)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert _sha(live) == digest


def test_verified_and_migrated_databases_still_pass(client, tmp_path):
    from backend.app import db, maintenance
    assert maintenance.check_database(_v27(tmp_path, "fresh"))["ok"]
    # portal-a3fbe00 → v27 and the early (pre-NOCASE) v10 → v27, both migrated in place.
    a3f = _sqlite_from_fixture(tmp_path / "a3f.sqlite", fixture=PORTAL_V10)
    assert maintenance.identify_backup_build(a3f, 10) == "portal-a3fbe00"
    early = _sqlite_from_fixture(tmp_path / "early.sqlite", fixture=PORTAL_V10, edit=lambda c: _replace_portal_table(
        c, REFERENCE_DDL.replace("UNIQUE COLLATE NOCASE", "UNIQUE")))
    for path in (a3f, early):
        conn = db._connect(path)
        rows = conn.execute(f"SELECT {COLUMNS} FROM patient_portal_access ORDER BY patient_id").fetchall()  # nosec
        db.init_db(conn)
        assert conn.execute(f"SELECT {COLUMNS} FROM patient_portal_access ORDER BY patient_id").fetchall() == rows  # nosec
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        report = maintenance.check_database(path)
        assert report["ok"], report["problems"]


# ================================================================ 2. fail-closed audit of a write-time UNIQUE conflict

def _cards(client):
    ids = _world()
    head = _client_for(client, "head.1")
    pids = [head.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"],
                                                                  policyNumber=f"77000000000282{i:02d}",
                                                                  name=f"Синтетик Конфликтный {i}"),
                      headers=head.csrf).json()["id"] for i in range(2)]
    return head, pids


def _bypass_precheck(monkeypatch):
    """The pre-check under the lock misses the owner (as for a writer that went
    around the service): the INSERT itself hits UNIQUE."""
    from backend.app.services import portal_access
    monkeypatch.setattr(portal_access, "_owner_of_email", lambda conn, email: None)


def _link(tab, pid, email):
    return tab.put(f"/api/patients/{pid}/portal-access", json={"email": email}, headers=tab.csrf)


def test_write_time_conflict_is_audited_once(client, monkeypatch):
    head, (p1, p2) = _cards(client)
    assert _link(head, p1, "unique.case@example.test").status_code == 200
    _bypass_precheck(monkeypatch)
    response = _link(head, p2, "UNIQUE.case@example.test")
    assert response.status_code == 409 and "unique.case" not in response.text.lower() and p1 not in response.text
    conflicts = _rows("SELECT * FROM audit_events WHERE event_type = 'portal_access_conflict'")
    assert len(conflicts) == 1 and conflicts[0]["result"] == "denied" and conflicts[0]["patient_id"] == p2
    assert conflicts[0]["detail"] == "status=conflict"
    assert _rows("SELECT patient_id FROM patient_portal_access") == [{"patient_id": p1}]


def test_write_time_conflict_without_an_audit_row_is_a_safe_refusal(client, monkeypatch):
    head, (p1, p2) = _cards(client)
    assert _link(head, p1, "unique.down@example.test").status_code == 200
    before = (_rows("SELECT * FROM patient_portal_access"), _rows("SELECT * FROM audit_events"))
    _bypass_precheck(monkeypatch)
    _fail_audit("portal_access_conflict")
    response = _link(head, p2, "unique.down@example.test")
    assert response.status_code == 503
    assert "unique.down" not in response.text and p1 not in response.text
    get_conn().execute("DROP TRIGGER IF EXISTS wp23a_audit_down")
    get_conn().commit()
    assert (_rows("SELECT * FROM patient_portal_access"), _rows("SELECT * FROM audit_events")) == before
