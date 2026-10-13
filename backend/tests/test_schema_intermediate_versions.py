"""Migrations from the intermediate consilium schemas (Max v10–v17) to current.

No database built by those Max builds exists: the Max snapshot only ships
the final code. Each version is therefore reconstructed on top of the real
v9 fixture (made by v0.9.0-stable) from what its migration added — the
consilium columns of that version, the favorites table from v12, the slot
grid from v16, the revision CHECK from v16 and the booking triggers from
v17. The data is synthetic.
"""
import sqlite3

import pytest

from .conftest import current_schema_version
from .test_restore_migration import V9_FIXTURE

BASE_COLUMNS = """
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    consilium_group TEXT NOT NULL,
    has_benefit INTEGER NOT NULL CHECK (has_benefit IN (0, 1)),
    treating_doctor_name TEXT NOT NULL,
    primary_diagnosis TEXT NOT NULL,
    additional_diagnosis TEXT,
    comment TEXT,
    additional_participant TEXT,
    needs_surgeon INTEGER NOT NULL CHECK (needs_surgeon IN (0, 1)),
    needs_radiotherapist INTEGER NOT NULL CHECK (needs_radiotherapist IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL"""

# Columns each Max migration added (db.migrate_v10_to_v11 … migrate_v15_to_v16).
ADDED_COLUMNS = {
    11: ["status TEXT NOT NULL DEFAULT 'recorded' CHECK (status IN ('recorded', 'in_review', 'signed'))",
         "treatment_type TEXT", "treatment_scheme TEXT", "revision_comment TEXT",
         "review_started_at TEXT", "signed_at TEXT", "signed_by_user_id TEXT REFERENCES users(id)"],
    12: ["policy_number TEXT", "referral_completed_at TEXT",
         "referral_completed_by_user_id TEXT REFERENCES users(id)"],
    13: ["diagnosis_localization TEXT", "treatment_scheme_code TEXT"],
    14: ["goc INTEGER NOT NULL DEFAULT 0 CHECK (goc IN (0, 1))",
         "goc_65_plus INTEGER NOT NULL DEFAULT 0 CHECK (goc_65_plus IN (0, 1))"],
    15: ["archived_at TEXT", "archived_by_user_id TEXT REFERENCES users(id)"],
    16: ["uv_ruv INTEGER NOT NULL DEFAULT 0 CHECK (uv_ruv IN (0, 1))"],
}


def _build(path, version, *, duplicate_slot=False):
    """A synthetic database exactly at Max `version`, with two bookings
    (one signed from v11 on) for the fixture's patients."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(V9_FIXTURE.read_text(encoding="utf-8"))
    columns = [BASE_COLUMNS]
    for v in range(11, min(version, 16) + 1):
        columns += [f"    {c}" for c in ADDED_COLUMNS[v]]
    conn.execute(f"CREATE TABLE oncology_consilium_appointments ({','.join(columns)}\n)")
    if version >= 12:
        conn.execute("""CREATE TABLE consilium_chair_favorites (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, PRIMARY KEY (user_id, patient_id))""")
    if version >= 16:
        conn.execute("""CREATE TABLE consilium_schedule_slots (
            id TEXT PRIMARY KEY, scheduled_date TEXT NOT NULL, scheduled_time TEXT NOT NULL,
            consilium_group TEXT NOT NULL, created_by_user_id TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, UNIQUE (scheduled_date, scheduled_time, consilium_group))""")
        _with_revision_check(conn)
    user = conn.execute("SELECT id FROM users ORDER BY username LIMIT 1").fetchone()["id"]
    patients = [r["id"] for r in conn.execute("SELECT id FROM patients ORDER BY name")]
    for index, patient_id in enumerate(patients[:2]):
        values = {
            "id": f"appt-{index}", "patient_id": patient_id, "created_by_user_id": user,
            "scheduled_date": "2026-11-12",
            "scheduled_time": "10:00" if duplicate_slot else f"1{index}:00",
            "consilium_group": "thoracic", "has_benefit": 0, "treating_doctor_name": "Фикстурова Врач Тестовна",
            "primary_diagnosis": "C34.1", "needs_surgeon": 0, "needs_radiotherapist": 0,
            "created_at": "2026-11-01T09:00:00+00:00", "updated_at": "2026-11-01T09:00:00+00:00",
        }
        if version >= 11 and index == 0:
            values.update(status="signed", treatment_type="surgery", signed_at="2026-11-12T10:30:00+00:00",
                          signed_by_user_id=user)
        names = ", ".join(values)
        conn.execute(
            f"INSERT INTO oncology_consilium_appointments ({names}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )
    if version >= 17:
        conn.executescript("""
            CREATE TRIGGER trg_consilium_one_appointment_per_patient_insert
            BEFORE INSERT ON oncology_consilium_appointments
            WHEN EXISTS (SELECT 1 FROM oncology_consilium_appointments WHERE patient_id = NEW.patient_id)
            BEGIN SELECT RAISE(ABORT, 'consilium_patient_already_booked'); END;
        """)
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()
    conn.close()
    return path


def _with_revision_check(conn):
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'internal_notification_state'"
    ).fetchone()["sql"]
    assert "consilium_revision" not in sql
    conn.executescript(f"""
        ALTER TABLE internal_notification_state RENAME TO internal_notification_state_v9;
        {sql.replace("'attention')", "'attention', 'consilium_revision')")};
        INSERT INTO internal_notification_state SELECT * FROM internal_notification_state_v9;
        DROP TABLE internal_notification_state_v9;
    """)


def _migrate(path):
    from backend.app.db import connect_readonly_check, init_db
    conn = connect_readonly_check(path)
    try:
        init_db(conn)
    finally:
        conn.close()


def _snapshot(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    data = {
        "version": conn.execute("SELECT version FROM schema_version").fetchone()[0],
        "appointments": [dict(r) for r in conn.execute("SELECT * FROM oncology_consilium_appointments ORDER BY id")],
        "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "schema": sorted(r[0] or "" for r in conn.execute("SELECT sql FROM sqlite_master")),
    }
    conn.close()
    return data


@pytest.mark.parametrize("version", range(10, 18))
def test_intermediate_max_schema_migrates_and_is_idempotent(client, tmp_path, version):
    from backend.app import maintenance

    path = _build(tmp_path / f"max-v{version}.sqlite", version)
    _migrate(path)
    first = _snapshot(path)
    report = maintenance.check_database(path)
    assert report["ok"], (version, report["problems"])
    assert first["version"] == current_schema_version()
    assert first["patients"] == 3

    rows = {r["id"]: r for r in first["appointments"]}
    assert set(rows) == {"appt-0", "appt-1"}
    assert rows["appt-1"]["status"] == "recorded"
    if version >= 11:
        assert rows["appt-0"]["status"] == "signed"
        assert rows["appt-0"]["treatment_type"] == "surgery"
        # Врач на момент подписи до v18 не хранился: берётся значение записи.
        assert rows["appt-0"]["signed_treating_doctor_name"] == "Фикстурова Врач Тестовна"
        assert rows["appt-0"]["signed_treating_doctor_user_id"] is None
    assert rows["appt-1"]["signed_treating_doctor_name"] is None
    assert all(r["revision_submitted_at"] is None for r in rows.values())

    _migrate(path)
    assert _snapshot(path) == first

    conn = sqlite3.connect(str(path))
    if version >= 11:
        with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
            conn.execute("UPDATE oncology_consilium_appointments SET treatment_type = 'radiotherapy' WHERE id = 'appt-0'")
    check = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'internal_notification_state'").fetchone()[0]
    assert "consilium_revision" in check
    conn.close()


@pytest.mark.parametrize("version", (12, 16))
def test_duplicate_slots_stop_start_with_readable_diagnosis(client, tmp_path, version):
    from backend.app.db import SchemaMigrationError

    path = _build(tmp_path / f"dup-v{version}.sqlite", version, duplicate_slot=True)
    with pytest.raises(SchemaMigrationError) as raised:
        _migrate(path)
    message = str(raised.value)
    assert "2026-11-12 10:00 thoracic: appt-0,appt-1" in message
    assert "Записи не удалены" in message
    assert "Вымышлен" not in message and "C34.1" not in message

    conn = sqlite3.connect(str(path))
    assert conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments").fetchone()[0] == 2
    conn.execute("UPDATE oncology_consilium_appointments SET scheduled_time = '11:00' WHERE id = 'appt-1'")
    conn.commit()
    conn.close()
    _migrate(path)
    assert _snapshot(path)["version"] == current_schema_version()


@pytest.mark.parametrize("old_version", (4, 5))
def test_v4_v5_branches_keep_revision_notification_type(client, tmp_path, old_version):
    """init_db's v4/v5 branches rebuild internal_notification_state with the
    v6 CHECK after the consilium migrations ran. Approximation: a current
    database relabelled as v4/v5, which takes exactly those branches."""
    from backend.app.db import connect_readonly_check, init_db

    path = tmp_path / f"v{old_version}.sqlite"
    conn = connect_readonly_check(path)
    init_db(conn)
    conn.execute("UPDATE schema_version SET version = ?", (old_version,))
    conn.commit()
    init_db(conn)
    check = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'internal_notification_state'").fetchone()[0]
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert "consilium_revision" in check
    assert version == current_schema_version()


def test_migration_keeps_the_only_surviving_revision_comment(client, tmp_path):
    """Before v18 every return overwrote revision_comment: only the last text
    of an unsigned record survives, and it becomes a migrated history row."""
    path = _build(tmp_path / "max-v16-revision.sqlite", 16)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "UPDATE oncology_consilium_appointments SET revision_comment = 'Последний комментарий' WHERE id = 'appt-1'"
    )
    conn.commit()
    conn.close()
    _migrate(path)
    _migrate(path)
    conn = sqlite3.connect(str(path))
    rows = conn.execute(
        "SELECT appointment_id, round, comment, returned_by_user_id, migrated FROM consilium_revision_history"
    ).fetchall()
    conn.close()
    assert rows == [("appt-1", 1, "Последний комментарий", None, 1)]


@pytest.mark.parametrize("signed_at, expected_source", [
    ("2099-01-01T00:00:00+00:00", "derived"),   # после записей истории фикстуры
    ("2000-01-01T00:00:00+00:00", "unknown"),   # раньше любой записи истории
])
def test_v19_derives_route_context_for_decisions_signed_by_older_builds(client, tmp_path, signed_at, expected_source):
    path = _build(tmp_path / "max-v16-route.sqlite", 16)
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE oncology_consilium_appointments SET signed_at = ? WHERE id = 'appt-0'", (signed_at,))
    conn.commit()
    conn.close()
    _migrate(path)
    _migrate(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM oncology_consilium_appointments WHERE id = 'appt-0'").fetchone()
    expected_entry = conn.execute(
        "SELECT id, stage, entered_at FROM history_entries WHERE patient_id = ? AND entered_at <= ? "
        "ORDER BY seq DESC LIMIT 1", (row["patient_id"], signed_at)).fetchone()
    unsigned = conn.execute("SELECT signed_route_link_source FROM oncology_consilium_appointments "
                            "WHERE id = 'appt-1'").fetchone()[0]
    conn.close()
    assert row["signed_route_link_source"] == expected_source
    if expected_source == "derived":
        assert (row["signed_route_history_entry_id"], row["signed_route_stage"], row["signed_route_entered_at"]) == (
            expected_entry["id"], expected_entry["stage"], expected_entry["entered_at"])
    else:
        assert expected_entry is None and row["signed_route_stage"] is None
    assert unsigned is None


def _triggers(path):
    conn = sqlite3.connect(str(path))
    rows = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())
    conn.close()
    return rows


@pytest.mark.parametrize("version", (16, None))
def test_concurrent_init_db_is_safe(client, tmp_path, version):
    """init_db may run in several connections at once (the first requests);
    trigger rebuilds must neither fail nor leave a window without them."""
    import threading
    from backend.app.db import connect_readonly_check, init_db

    path = tmp_path / "concurrent.sqlite"
    if version:
        _build(path, version)
    else:
        _migrate(path)
    errors = []
    barrier = threading.Barrier(8)

    def run():
        conn = connect_readonly_check(path)
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            barrier.wait()
            init_db(conn)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(repr(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    names = set(_triggers(path))
    assert {"trg_consilium_signed_immutable", "trg_consilium_one_appointment_per_patient_insert",
            "trg_consilium_one_appointment_per_patient_update"} <= names


def test_init_db_on_an_up_to_date_database_drops_no_trigger(client, tmp_path):
    from backend.app.db import connect_readonly_check, init_db

    path = tmp_path / "current.sqlite"
    _migrate(path)
    before = _triggers(path)
    conn = connect_readonly_check(path)
    statements = []
    conn.set_trace_callback(statements.append)
    init_db(conn)
    conn.close()
    assert not [s for s in statements if "DROP TRIGGER" in s.upper()]
    assert _triggers(path) == before


def test_concurrent_init_db_holds_the_lock_from_check_to_change(client, tmp_path, monkeypatch):
    """Every migration step reads the schema and then changes it. Two runs
    on the same v16 file meet right after reading that revision_submitted_at
    (added by v17 -> v18) is missing, the race CI caught: without one write
    lock from the first check to the end both add the column. With the lock
    the second run cannot read until the first has committed, the meeting
    times out and the runs go in turn."""
    import threading
    from backend.app import db

    path = _build(tmp_path / "lockstep.sqlite", 16)
    columns = db._table_columns
    meeting = threading.Barrier(2, timeout=1)

    def columns_then_meet(conn, table):
        found = columns(conn, table)
        if table == "oncology_consilium_appointments" and "revision_submitted_at" not in found:
            try:
                meeting.wait()
            except threading.BrokenBarrierError:
                pass
        return found

    monkeypatch.setattr(db, "_table_columns", columns_then_meet)
    errors = []

    def run():
        conn = db.connect_readonly_check(path)
        conn.execute("PRAGMA busy_timeout = 60000")
        try:
            db.init_db(conn)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(repr(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _snapshot(path)["version"] == current_schema_version()


def test_failed_migration_leaves_the_database_unchanged(client, tmp_path):
    from backend.app.db import SchemaMigrationError

    path = _build(tmp_path / "dup-v16.sqlite", 16, duplicate_slot=True)
    before = _snapshot(path)
    with pytest.raises(SchemaMigrationError):
        _migrate(path)
    assert _snapshot(path) == before


def test_init_db_refuses_an_open_transaction(client, tmp_path):
    from backend.app.db import connect_readonly_check, init_db

    path = _build(tmp_path / "open.sqlite", 16)
    conn = connect_readonly_check(path)
    conn.execute("DELETE FROM oncology_consilium_appointments WHERE id = 'appt-1'")
    assert conn.in_transaction
    with pytest.raises(RuntimeError):
        init_db(conn)
    conn.rollback()
    conn.close()
    assert len(_snapshot(path)["appointments"]) == 2
