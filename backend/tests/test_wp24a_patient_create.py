"""WP2.4-A: reliable creation of patient cards (schema v23).

One BEGIN IMMEDIATE transaction per creation owns the fresh permission check,
the choice of the treating doctor, every insert, the required audit row and
the response. Cards record their true author and source (form, import,
migration; 'legacy' for cards before v23). The consilium chair no longer
creates cards (R6) but keeps every other right. Creation is rate-limited per
user. Synthetic data only.
"""
import json
import re
import sqlite3
import time
from contextlib import contextmanager

import pytest

from .conftest import (card_version, app_stopped, DEV_USERNAME, PROJECT_ROOT, create_account, create_department, get_conn, legacy_export,
                       login, make_patient_payload, unsigned_import)
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import _client_for, _fail_audit, _in_thread, _parallel, _rows

V22_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp24a_v22_synthetic.sql"
APP = PROJECT_ROOT / "backend" / "app"
PROVENANCE = {"created_by_user_id", "creation_source"}
PROVENANCE_TRIGGERS = {"trg_patients_provenance_immutable", "trg_patients_creation_author_required"}
EVENTS = [{"date": "2026-11-20", "time": "10:00"}, {"date": "2026-11-27", "time": "11:00"}]


# ---------------------------------------------------------------- helpers

def _world():
    d1, d2 = create_department("Отделение WP24A 1"), create_department("Отделение WP24A 2")
    ids = {
        "dev": create_account(DEV_USERNAME, "developer"),
        "doc.a": create_account("doc.a", "doctor", "Синтетова Анна Врачевна", department_ids=[d1]),
        "doc.a2": create_account("doc.a2", "doctor", "Синтетов Алексей Врачевич", department_ids=[d1]),
        "doc.b": create_account("doc.b", "doctor", "Синтетов Борис Врачевич", department_ids=[d2]),
        "head.1": create_account("head.1", "department_head", "Заведующая Синтетическая", department_ids=[d1]),
        "chair": create_account("chair", "consilium_chair", "Председатель Синтетический"),
        "deputy": create_account("deputy", "deputy_chief_oncology", "Заместитель Синтетический"),
        "caop": create_account("caop", "caop_head", "ЦАОП Синтетический"),
    }
    ids["d1"], ids["d2"] = d1, d2
    return ids


def _create(client, **kw):
    return client.post("/api/patients", json=make_patient_payload(**kw), headers=client.csrf)


def _counts():
    return {
        "patients": _rows("SELECT COUNT(*) AS c FROM patients")[0]["c"],
        "history": _rows("SELECT COUNT(*) AS c FROM history_entries")[0]["c"],
        "events": _rows("SELECT COUNT(*) AS c FROM events")[0]["c"],
        "created_audit": _rows("SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'patient_created'")[0]["c"],
    }


def _patient(pid):
    r = get_conn().execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    return dict(r) if r else None


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


def _service():
    from backend.app.services import patient_create
    return patient_create


def _commit_elsewhere(sql, params):
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute(sql, params)
    raw.close()


class _AfterCommit:
    """Run `sql` from another connection right after the creation transaction commits."""

    def __init__(self, monkeypatch, sql, params):
        self.fired = False
        service = _service()
        real = service.transaction

        @contextmanager
        def boundary(conn):
            with real(conn) as inner:
                yield inner
            if not self.fired:
                self.fired = True
                _commit_elsewhere(sql, params)

        monkeypatch.setattr(service, "transaction", boundary)


def _while_locked(fn, change_sql=None, params=()):
    """Run fn while another connection holds the write lock; optionally commit
    a change from that connection before releasing it."""
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    box, done, thread = _in_thread(fn)
    time.sleep(0.6)
    waited = not done.is_set()
    if change_sql:
        raw.execute(change_sql, params)
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    return box["response"], waited


def _clinical(conn, table, order="rowid"):
    """Rows of a table without the v23 service columns."""
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]  # nosec - test constant
    # WP-C: patients.version (v28) — тоже служебный столбец, появившийся миграцией.
    skip = PROVENANCE | ({"version"} if table == "patients" else set())
    return [{k: v for k, v in row.items() if k not in skip} for row in rows]


CLINICAL_TABLES = ("patients", "history_entries", "events", "patient_diagnosis_history",
                   "oncology_consilium_appointments", "consilium_revision_history", "consilium_resubmissions",
                   "consilium_backdating_reasons", "consilium_schedule_slots", "users", "audit_events")


# ================================================================ A-T1. audit outage, full rollback

def test_audit_outage_rolls_back_card_route_and_events(client):
    _world()
    login(client, "doc.a")
    before = _counts()
    _fail_audit("patient_created")
    response = _create(client, events=EVENTS)
    assert response.status_code == 503, response.text
    assert "журнала аудита" in response.json()["detail"] and "Пациент не создан" in response.json()["detail"]
    assert _counts() == before


def test_database_error_inside_the_transaction_rolls_back(client, monkeypatch):
    from backend.app import store
    _world()
    login(client, "doc.a")
    before = _counts()
    real = store.insert_event
    calls = []

    def flaky(conn, *a, **kw):
        calls.append(1)
        if len(calls) == 2:
            exc = sqlite3.OperationalError("database is locked")
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise exc
        return real(conn, *a, **kw)

    monkeypatch.setattr(store, "insert_event", flaky)
    response = _create(client, events=EVENTS)
    assert response.status_code == 503 and "временно занята" in response.json()["detail"]
    assert _counts() == before


@pytest.fixture()
def short_busy(monkeypatch):
    monkeypatch.setenv("CLINIC_BUSY_TIMEOUT_MS", "200")


def test_sqlite_busy_on_the_write_lock_is_503_without_a_card(short_busy, client):
    _world()
    doctor = _client_for(client, "doc.a")
    before = _counts()
    raw = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    try:
        response = _create(doctor)
    finally:
        raw.execute("COMMIT")
        raw.close()
    assert response.status_code == 503, response.text
    assert _counts() == before


# ================================================================ A-T2. response is built inside the transaction

def test_response_reflects_the_committed_card_after_a_later_change(client, monkeypatch):
    ids = _world()
    login(client, "head.1")
    boundary = _AfterCommit(monkeypatch, "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    response = _create(client, treatingDoctorUserId=ids["doc.a"], events=EVENTS)
    assert boundary.fired and response.status_code == 201, response.text
    body = response.json()
    row = _patient(body["id"])
    assert (body["treatingDoctorUserId"], body["name"]) == (row["treating_doctor_user_id"], row["name"])
    assert len(body["history"]) == 1 and len(body["history"][0]["events"]) == 2


def test_response_is_not_reread_after_reassignment(client, monkeypatch):
    ids = _world()
    login(client, "doc.a")
    pid_holder = {}
    service = _service()
    real = service.transaction

    @contextmanager
    def boundary(conn):
        with real(conn) as inner:
            yield inner
        pid = conn.execute("SELECT id FROM patients ORDER BY created_at DESC LIMIT 1").fetchone()["id"]
        pid_holder["id"] = pid
        _commit_elsewhere("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.b"], pid))

    monkeypatch.setattr(service, "transaction", boundary)
    response = _create(client)
    assert response.status_code == 201 and response.json()["treatingDoctorUserId"] == ids["doc.a"]
    assert _patient(pid_holder["id"])["treating_doctor_user_id"] == ids["doc.b"]


# ================================================================ A-T3. rights changed while waiting for the lock

def test_head_losing_the_department_while_waiting_is_refused(client):
    ids = _world()
    head = _client_for(client, "head.1")
    before = _counts()
    response, waited = _while_locked(lambda: _create(head, treatingDoctorUserId=ids["doc.a"]),
                                     "DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    assert waited and response.status_code == 403, response.text
    assert _counts() == before


@pytest.mark.parametrize("change", ["blocked", "role_chair"])
def test_doctor_blocked_or_made_chair_while_waiting_is_refused(client, change):
    ids = _world()
    doctor = _client_for(client, "doc.a")
    before = _counts()
    sql = ("UPDATE users SET status = 'blocked' WHERE id = ?" if change == "blocked"
           else "UPDATE users SET role_id = 'consilium_chair' WHERE id = ?")
    response, waited = _while_locked(lambda: _create(doctor), sql, (ids["doc.a"],))
    assert waited and response.status_code == 403, response.text
    assert _counts() == before


def test_doctor_moved_to_a_department_while_waiting_creates_with_fresh_rights(client):
    """The fresh scope is used: a head who gains a department while waiting may assign its doctor."""
    ids = _world()
    head = _client_for(client, "head.1")
    response, waited = _while_locked(lambda: _create(head, treatingDoctorUserId=ids["doc.b"]),
                                     "INSERT INTO department_heads (department_id, user_id, created_at) "
                                     "VALUES (?, ?, '2026-10-01T00:00:00+00:00')",
                                     (ids["d2"], ids["head.1"]))
    assert waited and response.status_code == 201, response.text
    assert _patient(response.json()["id"])["treating_doctor_user_id"] == ids["doc.b"]


# ================================================================ A-T4 / A-T5. consilium chair (R6)

def test_chair_cannot_create_a_card(client):
    _world()
    login(client, "chair")
    me = client.get("/api/auth/me").json()["user"]
    assert me["permissions"]["createPatients"] is False
    assert me["permissions"]["editPatients"] is True        # прежнее право на свои карточки
    before = _counts()
    response = _create(client)
    assert response.status_code == 403
    assert _counts() == before
    denied = _rows("SELECT detail FROM audit_events WHERE event_type = 'access_denied'")
    assert [d["detail"] for d in denied] == ["action=patient_create"]


@pytest.mark.parametrize("who,expected", [("doc.a", 201), ("head.1", 201), ("dev", 201),
                                          ("chair", 403), ("deputy", 403), ("caop", 403)])
def test_create_permission_matrix(client, who, expected):
    ids = _world()
    login(client, DEV_USERNAME if who == "dev" else who)
    kw = {"treatingDoctorUserId": ids["doc.a"]} if who in ("head.1", "dev") else {}
    response = _create(client, **kw)
    assert response.status_code == expected, (who, response.text)
    flags = client.get("/api/auth/me").json()["user"]["permissions"]
    assert flags["createPatients"] is (expected == 201)


def test_chair_keeps_rights_on_a_legacy_card(client):
    """A card created by the chair before R6 (legacy) stays with the chair: no reassignment."""
    from backend.app import store
    from backend.app.models import PatientCreateIn
    ids = _world()
    pid = store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(name="Синтетический Наблюдаемый")),
                               ids["chair"])
    assert (_patient(pid)["creation_source"], _patient(pid)["created_by_user_id"]) == ("legacy", None)
    login(client, "chair")
    assert client.get(f"/api/patients/{pid}").status_code == 200
    r = client.patch(f"/api/patients/{pid}", json={"expectedVersion": card_version(pid), "field": "complaint", "value": "Уточнено"}, headers=client.csrf)
    assert r.status_code == 200, r.text
    assert _patient(pid)["treating_doctor_user_id"] == ids["chair"]


def test_chair_consilium_rights_are_unchanged(client):
    from backend.app import policy
    ids = _world()
    chair = policy.load_current_user(get_conn(), ids["chair"])
    assert policy.can_write_patient_data(chair) is True
    assert policy.can_access_consilium(chair) and policy.can_manage_consilium(chair) and policy.can_decide_consilium(chair)
    assert policy.can_mark_consilium_decision(chair) and policy.can_create_backdated_slot(chair)
    assert policy.can_book_consilium(chair) is False and policy.can_import(chair) is False


# ================================================================ A-T6. provenance

@pytest.mark.parametrize("who", ["doc.a", "head.1", "dev"])
def test_form_records_the_true_author_and_source(client, who):
    ids = _world()
    login(client, DEV_USERNAME if who == "dev" else who)
    kw = {"treatingDoctorUserId": ids["doc.a"]} if who != "doc.a" else {}
    response = _create(client, events=EVENTS, **kw)
    assert response.status_code == 201, response.text
    row = _patient(response.json()["id"])
    assert (row["created_by_user_id"], row["creation_source"]) == (ids[who], "form")
    audit = _rows("SELECT * FROM audit_events WHERE event_type = 'patient_created'")
    assert len(audit) == 1
    assert (audit[0]["patient_id"], audit[0]["actor_user_id"], audit[0]["target_user_id"], audit[0]["result"]) == (
        row["id"], ids[who], ids["doc.a"], "ok")
    assert "Тестов" not in json.dumps(audit, ensure_ascii=False)   # без ФИО и полиса
    assert "7700000000000000" not in json.dumps(audit, ensure_ascii=False)


@pytest.mark.parametrize("field,value", [("createdByUserId", "someone"), ("creationSource", "legacy"),
                                         ("created_by_user_id", "x"), ("creation_source", "migration")])
def test_client_cannot_set_or_spoof_provenance(client, field, value):
    _world()
    login(client, "doc.a")
    before = _counts()
    response = client.post("/api/patients", json={**make_patient_payload(), field: value}, headers=client.csrf)
    assert response.status_code == 422
    assert _counts() == before


def test_response_does_not_expose_new_fields_or_change_shape(client):
    _world()
    login(client, "doc.a")
    body = _create(client).json()
    assert not {"createdByUserId", "creationSource", "created_by_user_id", "creation_source"} & set(body)


def test_import_records_import_and_migration_records_migration(client):
    ids = _world()
    login(client, DEV_USERNAME)
    doc = json.dumps({"schemaVersion": 1, "patients": [{
        "name": "Перенесенный Синтетик", "age": 60, "priority": "normal", "stage": "exam", "phone": "",
        "diagnosis": "C34.1", "nosology": "lung34", "head": "Синтетов",
        "treatingDoctorUserId": ids["doc.a"]}]}, ensure_ascii=False)
    migrated = client.post("/api/migrate/localstorage/commit", json={"raw": doc, "mode": "add"}, headers=client.csrf)
    assert migrated.status_code == 200, migrated.text
    login(client, "doc.a")
    file = json.dumps({"schemaVersion": 1, "patients": [{
        "name": "Импортированный Синтетик", "age": 61, "priority": "normal", "stage": "exam", "phone": "",
        "diagnosis": "C34.1", "nosology": "lung34", "head": "Синтетов"}]}, ensure_ascii=False)
    assert unsigned_import(client, file, "add").status_code == 200
    rows = {r["name"]: r for r in _rows("SELECT name, created_by_user_id, creation_source FROM patients")}
    assert (rows["Перенесенный Синтетик"]["creation_source"], rows["Перенесенный Синтетик"]["created_by_user_id"]) == (
        "migration", ids["dev"])
    assert (rows["Импортированный Синтетик"]["creation_source"],
            rows["Импортированный Синтетик"]["created_by_user_id"]) == ("import", ids["doc.a"])


def test_file_cannot_set_provenance(client):
    """Provenance-like keys in a file are rejected, never trusted."""
    _world()
    login(client, "doc.a")
    file = json.dumps({"schemaVersion": 1, "patients": [{
        "name": "Синтетик Файла", "age": 61, "priority": "normal", "stage": "exam", "phone": "",
        "diagnosis": "C34.1", "nosology": "lung34", "head": "Синтетов",
        "createdByUserId": "someone", "creationSource": "legacy"}]}, ensure_ascii=False)
    preview = client.post("/api/import/preview", json={"raw": file}).json()
    assert preview["valid"] is False
    assert _counts()["patients"] == 0


def test_import_update_keeps_the_original_author(client):
    ids = _world()
    login(client, "doc.a")
    pid = _create(client).json()["id"]
    login(client, DEV_USERNAME)
    exported = client.get("/api/export", headers=client.csrf).text
    data = legacy_export(json.loads(exported))        # неподписанный файл с id карточки (как в существующих тестах)
    data["patients"][0]["history"][0]["events"].append({"date": "2026-12-01", "time": "10:00"})
    raw = json.dumps(data, ensure_ascii=False)
    result = unsigned_import(client, raw, "update")
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 1
    row = _patient(pid)
    assert (row["created_by_user_id"], row["creation_source"]) == (ids["doc.a"], "form")


def test_database_guards_provenance(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    ids = _world()
    login(client, "doc.a")
    pid = _create(client).json()["id"]
    conn = get_conn()
    for change in ("created_by_user_id = NULL", f"created_by_user_id = '{ids['dev']}'", "creation_source = 'legacy'",
                   "creation_source = 'import'"):
        with pytest.raises(sqlite3.IntegrityError, match="patient_provenance_immutable"):
            conn.execute(f"UPDATE patients SET {change} WHERE id = ?", (pid,))   # nosec - test constant
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="patient_creation_author_required"):
        conn.execute("INSERT INTO patients (id, name, priority, stage, arrival, created_at, creation_source) "
                     "VALUES ('x1', 'Синтетик', 'normal', 'exam', '2026-01-01', '2026-01-01', 'form')")
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO patients (id, name, priority, stage, arrival, created_at, created_by_user_id, "
                     "creation_source) VALUES ('x2', 'Синтетик', 'normal', 'exam', '2026-01-01', '2026-01-01', ?, "
                     "'api')", (ids["dev"],))
    conn.rollback()
    data = PatientCreateIn(**make_patient_payload())
    for kwargs in ({"created_by_user_id": None, "creation_source": "form"},
                   {"created_by_user_id": ids["dev"], "creation_source": "api"}):
        with pytest.raises(RuntimeError):
            store.insert_patient_rows(conn, data, None, **kwargs)
        conn.rollback()


def test_commit_import_rejects_an_unknown_source(client):
    from backend.app import importexport, policy
    ids = _world()
    document, _ = importexport.validate_import(json.dumps({"schemaVersion": 1, "patients": []}))
    with pytest.raises(ValueError, match="creation source"):
        importexport.commit_import(get_conn(), document, "add", policy.load_current_user(get_conn(), ids["dev"]),
                                   creation_source="form")


# ================================================================ A-T7. migration v22 → v23 and backups

def _fixture_db(tmp_path, name="v22.sqlite"):
    path = _sqlite_from_fixture(tmp_path / name, fixture=V22_FIXTURE)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return path, conn


def _init(conn):
    from backend.app import db
    db.init_db(conn)


def _triggers(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}


def test_v22_fixture_is_a_real_v22_database(client, tmp_path):
    from backend.app.maintenance import identify_backup_build
    path, conn = _fixture_db(tmp_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 22
    assert not PROVENANCE & {r[1] for r in conn.execute("PRAGMA table_info(patients)")}
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 7
    conn.close()
    assert identify_backup_build(path, 22) == "wp23b3-bd75612"


def test_migration_v22_to_v23_keeps_every_clinical_row(client, tmp_path):
    from backend.app import config
    path, conn = _fixture_db(tmp_path)
    before = {t: _clinical(conn, t) for t in CLINICAL_TABLES}
    _init(conn)
    # WP2.4-B/C/D: цепочка доводит v22 до текущей схемы (v26), а не только до v23.
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 31   # WP-C: v28 (история фактов, защита истории); combined-preaudit: v27 — мобильный доступ пациента
    for table in CLINICAL_TABLES:
        assert _clinical(conn, table) == before[table], table
    assert [tuple(r) for r in conn.execute("SELECT DISTINCT creation_source, created_by_user_id FROM patients")] == [
        ("legacy", None)]
    assert PROVENANCE_TRIGGERS <= _triggers(conn)
    # Подписанное решение, решения ОК, архивное событие и консилиум задним числом — на месте.
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE consilium_decision IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM events WHERE deleted_at IS NOT NULL").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM consilium_backdating_reasons").fetchone()[0] == 2
    conn.close()


def test_repeated_init_changes_nothing(client, tmp_path):
    path, conn = _fixture_db(tmp_path)
    _init(conn)
    snapshot = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]  # nosec - constant
                for t in CLINICAL_TABLES}
    sql_before = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    statements = []
    conn.set_trace_callback(statements.append)
    _init(conn)
    _init(conn)
    conn.set_trace_callback(None)
    assert not [s for s in statements if "DROP TRIGGER" in s.upper() or "ALTER TABLE" in s.upper()]
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == sql_before
    for table, rows in snapshot.items():
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] == rows, table  # nosec
    conn.close()


def test_migrated_legacy_chair_card_keeps_the_chairs_decision_and_rights(client, tmp_path):
    """The v22 fixture holds a card created by the chair with the chair's OK decision."""
    path, conn = _fixture_db(tmp_path)
    _init(conn)
    row = conn.execute(
        """SELECT p.id, p.creation_source, e.consilium_decision, e.consilium_decision_by_user_id, u.role_id
           FROM patients p JOIN users u ON u.id = p.treating_doctor_user_id
           JOIN history_entries h ON h.patient_id = p.id JOIN events e ON e.history_entry_id = h.id
           WHERE u.role_id = 'consilium_chair'""").fetchone()
    assert row["creation_source"] == "legacy" and row["consilium_decision"] == "dispensary_observation"
    assert row["consilium_decision_by_user_id"] is not None
    conn.close()


def test_restore_of_a_v22_backup_migrates_and_keeps_history(auth_client, tmp_path, backup_key):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp23b3-v22.cbk", header=22, fixture=V22_FIXTURE)
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("wp23b3-bd75612", 22)
    with app_stopped():
        result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["migrated_from_schema_version"] == 22
    assert maintenance.check_database(live)["ok"]
    conn = sqlite3.connect(str(live))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 31   # WP-E FIX1: v29; WP-C: v28 (история фактов, защита истории); combined-preaudit (v27); WP2.4-B/C/D: текущая схема
    assert [tuple(r) for r in conn.execute("SELECT DISTINCT creation_source, created_by_user_id FROM patients")] == [
        ("legacy", None)]
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM consilium_backdating_reasons").fetchone()[0] == 2
    assert PROVENANCE_TRIGGERS <= _triggers(conn)
    conn.close()


def test_backup_and_restore_of_the_current_build_keep_provenance(auth_client, tmp_path, backup_key):
    from backend.app import config, db, maintenance
    login(auth_client, DEV_USERNAME)
    created = _create(auth_client, name="Синтетик Копии").json()["id"]
    dev_id = _rows("SELECT id FROM users WHERE username = ?", DEV_USERNAME)[0]["id"]
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "current.cbk")
    db.reset_singleton_for_tests()
    with app_stopped():
        result = maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    assert maintenance.validate_backup(backup)["source_build"] == "current"
    assert result.get("migrated_from_schema_version") in (None, 23)
    conn = sqlite3.connect(str(config.DB_PATH))
    row = conn.execute("SELECT created_by_user_id, creation_source FROM patients WHERE id = ?", (created,)).fetchone()
    assert tuple(row) == (dev_id, "form")
    conn.close()


# ================================================================ A-T8. rate limiting

@pytest.fixture()
def low_limit(monkeypatch):
    monkeypatch.setenv("CLINIC_PATIENT_CREATE_RATE_LIMIT", "3")


def test_default_limit_is_60_per_minute(client):
    from backend.app import config
    assert (config.PATIENT_CREATE_RATE_LIMIT, config.PATIENT_CREATE_RATE_LIMIT_WINDOW_SECONDS) == (60, 60)


def test_limit_returns_429_without_a_card_and_ignores_payload(low_limit, client):
    from backend.app import security
    ids = _world()
    login(client, "doc.a")
    codes = [_create(client, name=f"Синтетик {i}", policyNumber=f"77000000000{i:05d}").status_code for i in range(3)]
    assert codes == [201, 201, 201]
    before = _counts()
    over = _create(client, name="Совсем Другой", policyNumber="7711111111111111", phone="+7 900 000-00-01")
    assert over.status_code == 429 and int(over.headers["Retry-After"]) >= 1
    assert _counts() == before
    # Другая сессия того же пользователя счетчик не обходит; другой пользователь не затронут.
    second_tab = _client_for(client, "doc.a")
    assert _create(second_tab).status_code == 429
    other = _client_for(client, "doc.a2")
    assert _create(other).status_code == 201
    keys = [k for k in security._request_attempts if k.startswith("patient_create:")]
    assert sorted(keys) == sorted([f"patient_create:{ids['doc.a']}", f"patient_create:{ids['doc.a2']}"])


def test_limit_counts_only_creation(low_limit, client):
    _world()
    login(client, "doc.a")
    pid = _create(client).json()["id"]
    for _ in range(5):
        assert client.get(f"/api/patients/{pid}").status_code == 200
    assert _create(client).status_code == 201 and _create(client).status_code == 201
    assert _create(client).status_code == 429


# ================================================================ A-T9. concurrency

def test_parallel_creations_are_all_complete_and_audited(client):
    ids = _world()
    tabs = [_client_for(client, "doc.a"), _client_for(client, "doc.a"), _client_for(client, "head.1")]
    for _ in range(20):
        res = _parallel(lambda: _create(tabs[0], events=EVENTS), lambda: _create(tabs[1], events=EVENTS),
                        lambda: _create(tabs[2], treatingDoctorUserId=ids["doc.a2"]))
        assert [r.status_code for r, *_ in res] == [201, 201, 201], [r.text for r, *_ in res]
    counts = _counts()
    assert counts["patients"] == counts["created_audit"] == counts["history"] == 60
    assert counts["events"] == 80
    orphans = _rows("""SELECT p.id FROM patients p WHERE NOT EXISTS (
                         SELECT 1 FROM audit_events a WHERE a.event_type = 'patient_created' AND a.patient_id = p.id)""")
    assert orphans == []
    assert {r["creation_source"] for r in _rows("SELECT creation_source FROM patients")} == {"form"}


def test_creation_does_not_touch_other_cards_consiliums_or_signed_decisions(client):
    from .test_wp23a_scheduling_integrity import DECISION, FUTURE, _book
    from .conftest import consilium_grid
    ids = _world()
    consilium_grid((FUTURE,))
    login(client, "doc.a")
    pid = _create(client).json()["id"]
    aid = _book(client, pid, FUTURE, "10:00").json()["id"]
    login(client, "chair")
    assert client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf).status_code == 200
    assert client.post(f"/api/consilium/appointments/{aid}/sign", json=DECISION, headers=client.csrf).status_code == 200
    conn = get_conn()
    snapshot = {t: _clinical(conn, t) for t in ("patients", "history_entries", "events",
                                                "oncology_consilium_appointments")}
    tabs = [_client_for(client, "doc.a"), _client_for(client, "head.1")]
    _parallel(lambda: _create(tabs[0], events=EVENTS), lambda: _create(tabs[1], treatingDoctorUserId=ids["doc.a"]))
    after = {t: _clinical(conn, t) for t in snapshot}
    assert after["oncology_consilium_appointments"] == snapshot["oncology_consilium_appointments"]
    old_ids = {r["id"] for r in snapshot["patients"]}
    assert [r for r in after["patients"] if r["id"] in old_ids] == snapshot["patients"]
    assert [r for r in after["history_entries"] if r["patient_id"] in old_ids] == snapshot["history_entries"]


# ================================================================ A-T10. client: double submission

def test_static_client_guards_double_submission():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    block = js[js.index("    async addPatient(event) {"):]
    block = block[:block.index("\n    },\n")]
    assert "if (this.addPatientPending) return;" in block
    # WP2.4-B: запрос создания отправляется с ключом идемпотентности (apiPostIdempotent).
    assert block.index("this.setAddPatientPending(true)") < block.index("apiPostIdempotent('/api/patients'")
    # Ошибка: кнопка снова доступна, введенные поля не сбрасываются (форма не закрывается и не очищается).
    error_branch = block[block.index("} catch (e) {"):block.index("return;", block.index("} catch (e) {"))]
    assert "setAddPatientPending(false)" in error_branch and "reset()" not in error_branch
    assert "closeModal" not in error_branch
    setter = js[js.index("    setAddPatientPending(pending) {"):]
    setter = setter[:setter.index("\n    },\n")]
    assert "button.disabled = pending" in setter and "aria-busy" in setter
    # WP2.4-B заменил этап A: от сетевых повторов защищает ключ идемпотентности, и он
    # передается только заголовком (подробные проверки — test_wp24b_idempotency).
    assert "headers: { 'Idempotency-Key': key }" in js and "console." not in js[js.index("function newIdempotencyKey"):js.index("async function apiPostIdempotent")]
    assert "ё" not in "Добавление…"


# ================================================================ A-T11. static guards

def _app_sources():
    return {p: p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}


def test_every_patient_insert_records_provenance():
    inserts = {p.relative_to(APP).as_posix(): s for p, s in _app_sources().items()
               if re.search(r"INSERT INTO patients\b", s)}
    assert set(inserts) == {"store.py", "importexport.py"}
    store_fn = inserts["store.py"][inserts["store.py"].index("def insert_patient_rows("):]
    store_fn = store_fn[:store_fn.index("\ndef ")]
    assert "INSERT INTO patients" in store_fn and "created_by_user_id" in store_fn and "creation_source" in store_fn
    assert inserts["store.py"].count("INSERT INTO patients") == 1
    imp = inserts["importexport.py"]
    insert_fn = imp[imp.index("def _insert_full_patient("):]
    insert_fn = insert_fn[:insert_fn.index("\ndef ")]
    assert '"created_by_user_id", "creation_source"' in insert_fn and "actor.id, creation_source" in insert_fn
    assert imp.count("INSERT INTO patients") == 1


def test_production_code_never_uses_the_legacy_helper_or_old_policy():
    for path, source in _app_sources().items():
        rel = path.relative_to(APP).as_posix()
        assert "can_create_patient(" not in source, rel
        if rel != "store.py":
            assert not re.search(r"\bcreate_patient\(conn", source), rel
            assert "store.create_patient(" not in source, rel
    routes_import = (APP / "routes" / "importexport.py").read_text(encoding="utf-8")
    routes_migrate = (APP / "routes" / "migrate.py").read_text(encoding="utf-8")
    assert 'creation_source="import"' in routes_import and 'creation_source="migration"' in routes_migrate
    patients = (APP / "routes" / "patients.py").read_text(encoding="utf-8")
    assert "patient_create.create_patient_card(" in patients and "store.create_patient(" not in patients


# ================================================================ A-01. audit is part of the service itself

def _actor(user_id):
    from backend.app import policy
    return policy.load_current_user(get_conn(), user_id)


def _body(**kw):
    from backend.app.models import PatientCreateIn
    return PatientCreateIn(**make_patient_payload(**kw))


SENSITIVE = ("Тестов Тест Тестович", "7700000000000000", "C34.1", "Жалобы для теста", "+7 (999) 000-00-00",
             "Заведуев Тест Отделенович", "lung34")


def test_service_has_no_optional_audit_parameter(client):
    import inspect
    service = _service()
    assert list(inspect.signature(service.create_patient_card).parameters) == ["conn", "actor", "body"]
    ids = _world()
    with pytest.raises(TypeError):
        service.create_patient_card(get_conn(), _actor(ids["doc.a"]), _body(), audit_hook=None)
    assert _counts()["patients"] == 0


def test_direct_service_call_writes_exactly_one_audit_row(client, caplog):
    import logging
    ids = _world()
    caplog.set_level(logging.DEBUG)
    service = _service()
    card = service.create_patient_card(get_conn(), _actor(ids["head.1"]), _body(
        treatingDoctorUserId=ids["doc.a"], events=EVENTS))
    rows = _rows("SELECT * FROM audit_events WHERE event_type = 'patient_created'")
    assert len(rows) == 1
    row = rows[0]
    assert (row["patient_id"], row["actor_user_id"], row["actor_role"], row["actor_department_id"],
            row["target_user_id"], row["result"], row["detail"]) == (
        card["id"], ids["head.1"], "department_head", ids["d1"], ids["doc.a"], "ok", None)
    assert _counts() == {"patients": 1, "history": 1, "events": 2, "created_audit": 1}
    # Повторные прямые вызовы — по одной записи на каждую карточку, без лишних.
    for _ in range(3):
        service.create_patient_card(get_conn(), _actor(ids["doc.a"]), _body())
    per_card = _rows("""SELECT p.id, COUNT(a.id) AS n FROM patients p
                        LEFT JOIN audit_events a ON a.patient_id = p.id AND a.event_type = 'patient_created'
                        GROUP BY p.id""")
    assert len(per_card) == 4 and all(r["n"] == 1 for r in per_card)
    dump = json.dumps(_rows("SELECT * FROM audit_events"), ensure_ascii=False) + caplog.text
    for value in SENSITIVE:
        assert value not in dump, value


def test_direct_service_call_rolls_back_everything_when_audit_fails(client):
    from backend.app import audit
    ids = _world()
    _fail_audit("patient_created")
    conn = get_conn()
    before = _counts()
    with pytest.raises(audit.AuditWriteFailed):
        _service().create_patient_card(conn, _actor(ids["doc.a"]), _body(events=EVENTS))
    assert not conn.in_transaction
    assert _counts() == before
    assert _rows("SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'patient_created'")[0]["c"] == 0


def test_http_contract_is_201_with_one_audit_row_and_503_without_a_card(client):
    _world()
    login(client, "doc.a")
    ok = _create(client)
    assert ok.status_code == 201
    assert _rows("SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'patient_created' AND patient_id = ?",
                 ok.json()["id"])[0]["c"] == 1
    assert _counts()["created_audit"] == 1
    _fail_audit("patient_created")
    before = _counts()
    failed = _create(client, events=EVENTS)
    assert failed.status_code == 503 and "Пациент не создан" in failed.json()["detail"]
    assert _counts() == before
    for value in SENSITIVE:
        assert value not in failed.text


def test_route_does_not_write_a_second_audit_row():
    source = (APP / "routes" / "patients.py").read_text(encoding="utf-8")
    route = source[source.index("def create_patient("):]
    route = route[:route.index("\n@router.")]
    assert "PATIENT_CREATED" not in route and "record_required" not in route and "audit_hook" not in route
    service = (APP / "services" / "patient_create.py").read_text(encoding="utf-8")
    assert service.count("audit.record_required_or_fail(conn, audit.PATIENT_CREATED") == 1
    assert "audit_hook" not in service and "Optional" not in service
