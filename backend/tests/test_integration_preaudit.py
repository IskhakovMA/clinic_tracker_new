"""Combined pre-audit integration (schema v27): WP2.4-D + Patient Portal stage 1 + redesign prototypes.

The Patient Portal identity was built as v9 → v10 on feat/executive-dashboard-redesign,
where v10 meant the patient_portal_access table; on the fixes line v10…v26 are the
consilium, medical-integrity and duplicate-registry migrations. These tests prove
that the merge keeps both: a database of either line reaches v27 without losing a
row, backups of both builds restore, and the portal follows the rules of the fixes
line (scope, erroneous cards). Synthetic data only.
"""
import ast
import json
import sqlite3
import subprocess
import shutil

import pytest

from .conftest import pre_v28_rows, app_stopped, DEV_USERNAME, PROJECT_ROOT, get_conn, login, make_patient_payload
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _rows
from .test_wp24a_patient_create import _world

FIXTURES = PROJECT_ROOT / "backend" / "tests" / "fixtures"
PORTAL_V10 = FIXTURES / "portal_v10_synthetic.sql"
V26 = FIXTURES / "wp24d_v26_synthetic.sql"


def _snapshot(conn, exclude=("schema_version",)):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: pre_v28_rows(conn, t) for t in tables if t not in exclude}


def _open(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------- migrations

def test_db_module_has_no_shadowed_functions():
    """The portal migration was named migrate_v9_to_v10 on its own line; a second
    function of that name would silently replace the consilium migration."""
    tree = ast.parse((PROJECT_ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8"))
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert duplicates == []
    source = (PROJECT_ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")
    assert "conn.executescript(" not in source            # every migration step stays in init_db's transaction


def test_portal_v10_database_migrates_to_v27_keeping_every_row(client, tmp_path):
    from backend.app import config, db
    from backend.app.maintenance import identify_backup_build
    path = _sqlite_from_fixture(tmp_path / "portal-v10.sqlite", fixture=PORTAL_V10)
    assert identify_backup_build(path, 10) == "portal-a3fbe00"
    conn = _open(path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 10
    before = _snapshot(conn)
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 33   # WP-C: текущая схема v28
    after = _snapshot(conn)
    reference = ("roles", "notification_templates")                     # справочники дополняются сидами
    for table, rows in before.items():
        old_columns = len(rows[0]) if rows else 0
        kept = [r[:old_columns] for r in after[table]]
        if table in reference:
            assert all(row in kept for row in rows), table              # прежние строки на месте
        else:
            assert kept == rows, table                                  # новые столбцы добавлены, значения прежние
    portal = conn.execute("SELECT email_normalized, status, revoked_at FROM patient_portal_access ORDER BY linked_at").fetchall()
    assert [(r["email_normalized"], r["status"]) for r in portal] == [
        ("patient.one@example.test", "linked"), ("patient.two@example.test", "revoked")]
    assert "COLLATE NOCASE" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'patient_portal_access'").fetchone()[0].upper()
    for table in ("oncology_consilium_appointments", "idempotency_keys", "patient_duplicate_confirmations",
                  "patient_duplicate_candidates", "patient_erroneous_state"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone(), table
    schema = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    db.init_db(conn)                                                    # repeated run changes nothing
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == schema
    with pytest.raises(sqlite3.IntegrityError):                         # one address — one card, any case
        conn.execute("INSERT INTO patient_portal_access (patient_id, email_normalized, status, linked_at, updated_at) "
                     "SELECT id, 'PATIENT.ONE@EXAMPLE.TEST', 'linked', 'now', 'now' FROM patients "
                     "WHERE id NOT IN (SELECT patient_id FROM patient_portal_access) LIMIT 1")
    conn.close()


def test_v26_database_migrates_to_v27_keeping_registry_and_guards(client, tmp_path):
    from backend.app import config, db
    from backend.app.maintenance import identify_backup_build
    path = _sqlite_from_fixture(tmp_path / "v26.sqlite", fixture=V26)
    assert identify_backup_build(path, 26) == "wp24d-80249a5"
    conn = _open(path)
    before = _snapshot(conn)
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 33   # WP-C: текущая схема v28
    after = _snapshot(conn)
    assert {t: after[t] for t in before} == before
    assert after["patient_portal_access"] == []
    marked = conn.execute("SELECT patient_id FROM patient_erroneous_state").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="patient_erroneous_readonly"):
        conn.execute("UPDATE patients SET name = 'x' WHERE id = ?", (marked,))
    conn.close()


@pytest.mark.parametrize("fixture,header,build", [(PORTAL_V10, 10, "portal-a3fbe00"), (V26, 26, "wp24d-80249a5")])
def test_backups_of_both_lines_restore_to_v27(auth_client, tmp_path, backup_key, fixture, header, build):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, f"{build}.cbk", header=header, fixture=fixture)
    assert maintenance.validate_backup(backup)["source_build"] == build
    with app_stopped():
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33   # WP-E FIX1: v29; WP-C: текущая схема v28
    expected_portal = 2 if header == 10 else 0
    assert conn.execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == expected_portal
    if header == 26:
        assert conn.execute("SELECT COUNT(*) FROM patient_erroneous_marks").fetchone()[0] == 1
    conn.close()


def test_current_v27_backup_round_trip(client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    _world()
    login(client, "head.1")
    ids = {r["username"]: r["id"] for r in _rows("SELECT id, username FROM users")}
    pid = client.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"]),
                      headers=client.csrf).json()["id"]
    linked = client.put(f"/api/patients/{pid}/portal-access", json={"email": "round.trip@example.test"},
                        headers=client.csrf)
    assert linked.status_code == 200, linked.text
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "v27.cbk")
    assert maintenance.validate_backup(backup)["source_build"] == "current"
    db.reset_singleton_for_tests()
    with app_stopped():
        maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    assert conn.execute("SELECT email_normalized FROM patient_portal_access").fetchall() == [("round.trip@example.test",)]
    conn.close()


# ---------------------------------------------------------------- portal under the fixes-line rules

def test_portal_access_follows_scope_and_erroneous_marks(client):
    ids = _world()
    head = _client_for(client, "head.1")
    pid = head.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"], policyNumber="7700000000027001"),
                    headers=head.csrf).json()["id"]
    assert head.put(f"/api/patients/{pid}/portal-access", json={"email": "scope.case@example.test"},
                    headers=head.csrf).status_code == 200
    other = _client_for(client, "doc.b")                              # другое отделение: карточки не видно
    assert other.get(f"/api/patients/{pid}/portal-access").status_code == 404
    assert other.delete(f"/api/patients/{pid}/portal-access", headers=other.csrf).status_code == 404
    chair = _client_for(client, "chair")                              # R6: председатель карточек не создает и не связывает
    assert chair.put(f"/api/patients/{pid}/portal-access", json={"email": "chair@example.test"},
                     headers=chair.csrf).status_code in (403, 404)
    # Помеченная ошибочной карточка (WP2.4-D) — вне активной работы и для мобильного доступа.
    twin = head.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"], policyNumber="7700000000027099",
                                                                 name="Синтетик Двойник"), headers=head.csrf).json()["id"]
    get_conn().execute("UPDATE patients SET policy_number = '7700000000027001' WHERE id = ?", (twin,))
    get_conn().commit()
    dev, deputy = _client_for(client, DEV_USERNAME), _client_for(client, "deputy")
    assert dev.post("/api/duplicates/scans", headers=dev.csrf).status_code == 200
    candidate = _rows("SELECT id, version FROM patient_duplicate_candidates")[0]
    marked = deputy.post(f"/api/duplicates/candidates/{candidate['id']}/mark", headers=deputy.csrf,
                         json={"patientId": twin, "reason": "Синтетическая причина", "expectedVersion": candidate["version"]})
    assert marked.status_code == 200, marked.text
    for call in (lambda t: t.get(f"/api/patients/{twin}/portal-access"),
                 lambda t: t.put(f"/api/patients/{twin}/portal-access", json={"email": "twin@example.test"}, headers=t.csrf),
                 lambda t: t.delete(f"/api/patients/{twin}/portal-access", headers=t.csrf)):
        assert call(head).status_code == 404
    assert _rows("SELECT COUNT(*) AS c FROM patient_portal_access WHERE patient_id = ?", twin)[0]["c"] == 0


def test_portal_access_stays_out_of_export_and_audit_details(client):
    ids = _world()
    head = _client_for(client, "head.1")
    pid = head.post("/api/patients", json=make_patient_payload(treatingDoctorUserId=ids["doc.a"]), headers=head.csrf).json()["id"]
    head.put(f"/api/patients/{pid}/portal-access", json={"email": "export.case@example.test"}, headers=head.csrf)
    dev = _client_for(client, DEV_USERNAME)
    assert "export.case@example.test" not in dev.get("/api/export", headers=dev.csrf).text
    assert "export.case" not in json.dumps(_rows("SELECT detail FROM audit_events"), ensure_ascii=False)


# ---------------------------------------------------------------- prototypes

def test_redesign_prototypes_and_their_tests_are_present():
    for path in ("prototype/redesign/index.html", "prototype/redesign/data.js", "prototype/staff-ux/index.html",
                 "prototype/staff-ux/tests/consilium.test.mjs", "prototype/staff-ux/tests/core.test.mjs",
                 "prototype/staff-ux/tests/events.test.mjs", "prototype/staff-ux/tests/newpatient.test.mjs"):
        assert (PROJECT_ROOT / path).is_file(), path


def test_staff_ux_prototype_node_tests_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    tests = sorted(str(p) for p in (PROJECT_ROOT / "prototype" / "staff-ux" / "tests").glob("*.test.mjs"))
    result = subprocess.run([node, "--test", *tests], capture_output=True, text=True, timeout=120, cwd=PROJECT_ROOT)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
