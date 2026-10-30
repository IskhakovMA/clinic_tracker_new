"""WP2.3-B1: repeated consiliums, record version and explicit resubmission.

Schema v22: at most one unfinished consilium per patient (signed and deleted
records do not count), an integer row version for optimistic locking, an
append-only history of resubmissions, prepared (inactive) B3 columns.
Concurrency uses separate HTTP clients, each request on its own SQLite
connection (helpers of the WP2.3-A suite). Synthetic data only.
"""
import sqlite3
import threading

import pytest

from .conftest import app_stopped, DEV_USERNAME, PROJECT_ROOT, consilium_version, get_conn, login
from .test_consilium_appointments import appointment_payload
from .test_restore_migration import _live, _sqlite_from_fixture, _v9_backup, backup_key  # noqa: F401
from .test_wp23a_scheduling_integrity import (  # noqa: F401 — _high_limits is autouse here too
    DECISION, FUTURE, FUTURE2, _Hold, _audit, _book, _client_for, _consilium, _fail_audit, _high_limits,
    _interleave, _med_digest, _move, _parallel, _rows, _world,
)

V21_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp23a_v21_synthetic.sql"
V20_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp22a_v20_synthetic.sql"
MAX_V17_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "max_v17_synthetic.sql"
CONFLICT_TEXT = ("Данные записи изменились. Пока вы редактировали запись, ее обновили. "
                 "Обновите данные и повторите действие")
OLD_GUARDS = {"trg_consilium_one_appointment_per_patient_insert", "trg_consilium_one_appointment_per_patient_update"}
NEW_GUARDS = {"trg_consilium_one_open_per_patient_insert", "trg_consilium_one_open_per_patient_update"}
B1_TRIGGERS = NEW_GUARDS | {
    "trg_consilium_signed_immutable", "trg_consilium_version_bump", "trg_consilium_version_forward",
    "trg_consilium_resubmissions_no_update", "trg_consilium_resubmissions_no_delete",
    "trg_consilium_backdating_reasons_no_update", "trg_consilium_backdating_reasons_no_delete",
}
V22_COLUMNS = {"version", "backdated", "backdating_reason_id"}


# ---------------------------------------------------------------- helpers

def _row(aid):
    r = get_conn().execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone()
    return dict(r) if r else None


def _post(client, aid, action, body=None):
    if body is None:
        body = {"version": consilium_version(aid)} if action in ("archive", "restore", "resubmit") else {}
    return client.post(f"/api/consilium/appointments/{aid}/{action}", json=body, headers=client.csrf)


def _delete(client, aid, version=None):
    version = consilium_version(aid) if version is None else version
    return client.delete(f"/api/consilium/appointments/{aid}?version={version}", headers=client.csrf)


def _sign(client, aid, author="doc.a"):
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "sign", DECISION).status_code == 200
    login(client, author)


def _return(client, aid, comment="Уточните данные", author="doc.a"):
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": comment}).status_code == 200
    login(client, author)


def _triggers(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _fixture_db(tmp_path, fixture=V21_FIXTURE, name="v21.sqlite"):
    path = _sqlite_from_fixture(tmp_path / name, fixture=fixture)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return path, conn


def _init(conn):
    from backend.app import db
    db.init_db(conn)


# ================================================================ 1. migration v21 → v22

def test_v21_fixture_is_a_real_v21_database(client, tmp_path):
    from backend.app.maintenance import identify_backup_build
    path, conn = _fixture_db(tmp_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 21
    assert OLD_GUARDS <= _triggers(conn) and not NEW_GUARDS & _triggers(conn)
    assert not V22_COLUMNS & _columns(conn, "oncology_consilium_appointments")
    conn.close()
    assert identify_backup_build(path, 21) == "wp23a-efe7168"


def test_migration_v21_to_v22_keeps_every_clinical_row(client, tmp_path):
    from backend.app import config
    path, conn = _fixture_db(tmp_path)
    tables = ("oncology_consilium_appointments", "consilium_revision_history", "consilium_schedule_slots",
              "patients", "history_entries", "events", "users")
    before = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in tables}

    _init(conn)

    # WP2.4-A/B/C/D: цепочка доводит v21 до текущей схемы (v26), а не только до v22.
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 29   # WP-C: v28 (история фактов, защита истории); combined-preaudit: v27 — мобильный доступ пациента
    for table in tables:
        after = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        # Старые столбцы каждой строки — побайтно прежние; новые — только значения по умолчанию.
        assert [{k: row[k] for k in old} for old, row in zip(before[table], after)] == before[table], table
        assert len(after) == len(before[table]), table
    for row in conn.execute("SELECT version, backdated, backdating_reason_id FROM oncology_consilium_appointments"):
        assert tuple(row) == (1, 0, None)
    for row in conn.execute("SELECT backdated, backdating_reason_id FROM consilium_schedule_slots"):
        assert tuple(row) == (0, None)
    triggers = _triggers(conn)
    assert B1_TRIGGERS <= triggers and not OLD_GUARDS & triggers
    assert conn.execute("SELECT COUNT(*) FROM consilium_backdating_reasons").fetchone()[0] == 0
    conn.close()


def test_migration_carries_only_known_resubmissions(client, tmp_path):
    path, conn = _fixture_db(tmp_path)
    known = {r["id"]: (r["revision_count"], r["revision_submitted_at"]) for r in conn.execute(
        "SELECT id, revision_count, revision_submitted_at FROM oncology_consilium_appointments "
        "WHERE revision_submitted_at IS NOT NULL")}
    assert len(known) == 2  # подписанная после доработки и исправленная (revised)

    _init(conn)

    rows = [dict(r) for r in conn.execute("SELECT * FROM consilium_resubmissions ORDER BY appointment_id")]
    assert {r["appointment_id"]: (r["round"], r["submitted_at"]) for r in rows} == known
    # Автор прежних подач не хранился и не выдумывается; время — то, что было в v21.
    assert all(r["submitted_by_user_id"] is None and r["migrated"] == 1 for r in rows)
    # Ожидающая доработки и мягко удаленная записи подачи не получили.
    waiting = conn.execute("SELECT COUNT(*) FROM oncology_consilium_appointments "
                           "WHERE revision_count > 0 AND revision_submitted_at IS NULL").fetchone()[0]
    assert waiting == 2
    conn.close()


def test_repeated_init_changes_nothing_and_drops_no_trigger(client, tmp_path):
    path, conn = _fixture_db(tmp_path)
    _init(conn)
    snapshot = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                for t in ("oncology_consilium_appointments", "consilium_resubmissions", "consilium_schedule_slots")}
    sql_before = sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))
    statements = []
    conn.set_trace_callback(statements.append)
    _init(conn)
    _init(conn)
    conn.set_trace_callback(None)
    assert not [s for s in statements if "DROP TRIGGER" in s.upper() or "ALTER TABLE" in s.upper()]
    assert sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master")) == sql_before
    for table, rows in snapshot.items():
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] == rows, table
    conn.close()


@pytest.mark.parametrize("fixture", [V20_FIXTURE, MAX_V17_FIXTURE], ids=["wp22a-v20", "max-v17"])
def test_older_versions_end_with_the_v22_guard_only(client, tmp_path, fixture):
    path, conn = _fixture_db(tmp_path, fixture=fixture, name="old.sqlite")
    _init(conn)
    _init(conn)  # повторный запуск не возвращает правило «одна запись навсегда»
    triggers = _triggers(conn)
    assert NEW_GUARDS <= triggers and not OLD_GUARDS & triggers
    assert V22_COLUMNS <= _columns(conn, "oncology_consilium_appointments")
    conn.close()


def test_restore_of_a_v21_backup_migrates_and_keeps_the_history(auth_client, tmp_path):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "wp23a-v21.cbk", header=21, fixture=V21_FIXTURE)
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("wp23a-efe7168", 21)
    with app_stopped():
        result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["migrated_from_schema_version"] == 21
    assert maintenance.check_database(live)["ok"]

    conn = sqlite3.connect(str(live))
    conn.row_factory = sqlite3.Row
    statuses = sorted(r["status"] for r in conn.execute("SELECT status FROM oncology_consilium_appointments"))
    assert statuses == ["recorded"] * 5 + ["signed"]
    assert conn.execute("SELECT COUNT(*) FROM consilium_revision_history").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM consilium_resubmissions WHERE migrated = 1").fetchone()[0] == 2
    assert NEW_GUARDS <= _triggers(conn) and not OLD_GUARDS & _triggers(conn)
    signed = conn.execute("SELECT id FROM oncology_consilium_appointments WHERE status = 'signed'").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
        conn.execute("UPDATE oncology_consilium_appointments SET treatment_type = 'radiotherapy' WHERE id = ?", (signed,))
    conn.close()


def test_current_v22_backup_is_accepted(auth_client, tmp_path):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = tmp_path / "current.cbk"
    maintenance.backup_database(live, backup)
    assert maintenance.validate_backup(backup)["source_build"] == "current"


# ================================================================ 2. legacy duplicates

def test_legacy_duplicate_unfinished_records_keep_working_but_block_new_ones(client, tmp_path):
    path, conn = _fixture_db(tmp_path)
    _init(conn)
    patient = conn.execute(
        """SELECT patient_id FROM oncology_consilium_appointments WHERE deleted_at IS NULL AND status != 'signed'
           GROUP BY patient_id HAVING COUNT(*) = 2""").fetchone()["patient_id"]
    first, second = [r["id"] for r in conn.execute(
        "SELECT id FROM oncology_consilium_appointments WHERE patient_id = ? ORDER BY id", (patient,))]
    # Переходы между незавершенными статусами исторических дублей не блокируются.
    with conn:
        conn.execute("UPDATE oncology_consilium_appointments SET status = 'in_review' WHERE id = ?", (first,))
        conn.execute("UPDATE oncology_consilium_appointments SET status = 'recorded' WHERE id = ?", (first,))
    # Третья незавершенная запись того же пациента — отказ на уровне SQLite.
    with pytest.raises(sqlite3.IntegrityError, match="consilium_patient_has_open_record"):
        conn.execute(
            """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
               scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
               needs_surgeon, needs_radiotherapist, created_at, updated_at)
               SELECT 'third', patient_id, created_by_user_id, '2026-11-20', '09:00', 'thoracic', 0, 'Синтетов',
                      'C34.1', 0, 0, '2026-01-01', '2026-01-01'
               FROM oncology_consilium_appointments WHERE id = ?""", (second,))
    conn.rollback()
    conn.close()


# ================================================================ 3. repeated consilium

def test_new_consilium_is_allowed_only_after_the_previous_one_is_signed(client):
    _, pts = _world(client)
    login(client, "doc.a")
    first = _book(client, pts["pa1"], FUTURE, "10:00")
    assert first.status_code == 201
    first_id = first.json()["id"]
    blocked = _book(client, pts["pa1"], FUTURE, "11:00")
    assert blocked.status_code == 409 and blocked.json()["detail"].startswith("Пациент уже записан")
    assert pts["pa1"] in client.get("/api/consilium/booked-patients").json()["patientIds"]

    _sign(client, first_id)
    signed_before = _row(first_id)
    assert pts["pa1"] not in client.get("/api/consilium/booked-patients").json()["patientIds"]
    second = _book(client, pts["pa1"], FUTURE2, "10:00", comment="Повторный консилиум")
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second_id != first_id
    assert _book(client, pts["pa1"], FUTURE2, "11:00").status_code == 409

    # Второй консилиум проходит свой цикл; первый не меняется ни на байт.
    _return(client, second_id, comment="Второй раунд: уточните")
    assert _post(client, second_id, "resubmit").status_code == 200
    _sign(client, second_id)
    assert _row(first_id) == signed_before
    a, b = _row(first_id), _row(second_id)
    assert (a["scheduled_date"], a["scheduled_time"]) != (b["scheduled_date"], b["scheduled_time"])
    assert a["signed_at"] != b["signed_at"] and a["signed_by_user_id"] == b["signed_by_user_id"]
    assert (a["revision_count"], b["revision_count"]) == (0, 1)
    assert _rows("SELECT appointment_id FROM consilium_revision_history") == [{"appointment_id": second_id}]
    created = [r["detail"] for r in _audit("consilium_appointment_created")]
    assert len(created) == 2 and all(f"appointment_id={x}" in " ".join(created) for x in (first_id, second_id))
    listed = [r["id"] for r in client.get("/api/consilium/appointments").json()["appointments"]]
    assert set(listed) == {first_id, second_id}


def test_archived_signed_and_cancelled_records_do_not_block_a_new_consilium(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _sign(client, aid)
    assert _post(client, aid, "archive").status_code == 200
    again = _book(client, pts["pa1"], FUTURE, "11:00")
    assert again.status_code == 201
    # Отмененная (мягко удаленная после возврата) запись тоже не мешает.
    _return(client, again.json()["id"])
    assert _delete(client, again.json()["id"]).status_code == 200
    assert _book(client, pts["pa1"], FUTURE, "12:00").status_code == 201


@pytest.mark.parametrize("state", ["recorded", "in_review", "needs_revision", "revised"])
def test_every_unfinished_state_blocks_another_record(client, state):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    if state != "recorded":
        login(client, "chair")
        assert _post(client, aid, "review").status_code == 200
        if state in ("needs_revision", "revised"):
            assert _post(client, aid, "return", {"comment": "Уточните"}).status_code == 200
        login(client, "doc.a")
        if state == "revised":
            assert _post(client, aid, "resubmit").status_code == 200
    assert client.get(f"/api/consilium/appointments").json()["appointments"][0]["status"] == state
    assert _book(client, pts["pa1"], FUTURE, "11:00").status_code == 409


def test_database_guard_rejects_a_second_unfinished_record_by_direct_sql(client):
    _, pts = _world(client)
    login(client, "doc.a")
    open_id = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    other_id = _book(client, pts["pa2"], FUTURE, "10:30").json()["id"]
    conn = get_conn()
    insert = """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
                scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
                needs_surgeon, needs_radiotherapist, created_at, updated_at)
                SELECT ?, patient_id, created_by_user_id, ?, '09:00', 'thoracic', 0, 'Синтетов', 'C34.1', 0, 0,
                       created_at, updated_at FROM oncology_consilium_appointments WHERE id = ?"""
    with pytest.raises(sqlite3.IntegrityError, match="consilium_patient_has_open_record"):
        conn.execute(insert, ("direct-insert", FUTURE2, open_id))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="consilium_patient_has_open_record"):
        conn.execute("UPDATE oncology_consilium_appointments SET patient_id = ? WHERE id = ?", (pts["pa1"], other_id))
    conn.rollback()
    # Восстановление мягко удаленной записи при уже открытой другой — тоже отказ.
    _return(client, other_id)
    assert _delete(client, other_id).status_code == 200
    login(client, "doc.a")
    second_for_pa2 = _book(client, pts["pa2"], FUTURE2, "10:30")
    assert second_for_pa2.status_code == 201
    with pytest.raises(sqlite3.IntegrityError, match="consilium_patient_has_open_record"):
        conn.execute("UPDATE oncology_consilium_appointments SET deleted_at = NULL WHERE id = ?", (other_id,))
    conn.rollback()
    # Подписанная запись не может снова стать незавершенной.
    _sign(client, open_id)
    with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
        conn.execute("UPDATE oncology_consilium_appointments SET status = 'recorded' WHERE id = ?", (open_id,))
    conn.rollback()
    # Несколько подписанных у одного пациента — допустимо и для прямого SQL.
    with conn:
        conn.execute(insert.replace("'09:00'", "'15:00'"), ("direct-second", FUTURE2, open_id))
    assert _row("direct-second")["status"] == "recorded"


def test_concurrent_repeated_bookings_after_signature_forced(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _sign(client, aid)
    hold = _Hold(monkeypatch, _consilium(), "_require_patient_not_booked")
    tab1, tab2 = _client_for(client, "doc.a"), _client_for(client, "doc.a")

    first, second, waited = _interleave(
        lambda: _book(tab1, pts["pa1"], FUTURE, "11:00"), lambda: _book(tab2, pts["pa1"], FUTURE, "12:00"), hold)

    assert waited and first.status_code == 201 and second.status_code == 409
    rows = _rows("SELECT status FROM oncology_consilium_appointments WHERE patient_id = ? ORDER BY created_at",
                 pts["pa1"])
    assert [r["status"] for r in rows] == ["signed", "recorded"]


def test_concurrent_repeated_bookings_after_signature_burst(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _sign(client, aid)
    tab1, tab2 = _client_for(client, "doc.a"), _client_for(client, "doc.a")
    for _ in range(20):
        res = _parallel(lambda: _book(tab1, pts["pa1"], FUTURE, "11:00"),
                        lambda: _book(tab2, pts["pa1"], FUTURE, "12:00"))
        assert sorted(r.status_code for r, *_ in res) == [201, 409]
        open_rows = _rows("SELECT id FROM oncology_consilium_appointments WHERE patient_id = ? "
                          "AND status != 'signed' AND deleted_at IS NULL", pts["pa1"])
        assert len(open_rows) == 1
        get_conn().execute("DELETE FROM oncology_consilium_appointments WHERE id = ?", (open_rows[0]["id"],))
        get_conn().commit()
    assert _row(aid)["status"] == "signed"


def test_direct_sql_inserts_race_one_unfinished_survives(client):
    """Two raw connections insert an unfinished record for one patient at once."""
    _, pts = _world(client)
    login(client, "doc.a")
    template = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _sign(client, template)
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    barrier, results = threading.Barrier(2), []

    def insert(new_id, time):
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            barrier.wait()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
                   scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
                   needs_surgeon, needs_radiotherapist, created_at, updated_at)
                   SELECT ?, patient_id, created_by_user_id, ?, ?, 'thoracic', 0, 'Синтетов', 'C34.1', 0, 0,
                          created_at, updated_at FROM oncology_consilium_appointments WHERE id = ?""",
                (new_id, FUTURE2, time, template))
            conn.execute("COMMIT")
            results.append("ok")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            results.append(str(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=insert, args=(f"raw-{i}", t)) for i, t in enumerate(("09:00", "09:30"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(results) == ["consilium_patient_has_open_record", "ok"]


# ================================================================ 4. versions

def _stale_cases(client, pts):
    """{action: (prepare → aid, call(aid, stale_version))}"""
    def booked():
        login(client, "doc.a")
        return _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]

    def signed():
        aid = booked()
        _sign(client, aid)
        return aid

    def archived():
        aid = signed()
        assert _post(client, aid, "archive").status_code == 200
        return aid

    def returned():
        aid = booked()
        _return(client, aid)
        return aid

    put = lambda aid, v: client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf,
                                    json=appointment_payload(pts["pa1"], scheduledDate=FUTURE, scheduledTime="11:00",
                                                             comment="Из устаревшей формы", version=v))
    return {
        "update": (booked, put),
        "delete": (booked, lambda aid, v: _delete(client, aid, v)),
        "archive": (signed, lambda aid, v: _post(client, aid, "archive", {"version": v})),
        "restore": (archived, lambda aid, v: _post(client, aid, "restore", {"version": v})),
        "resubmit": (returned, lambda aid, v: _post(client, aid, "resubmit", {"version": v})),
    }


@pytest.mark.parametrize("action", ["update", "delete", "archive", "restore", "resubmit"])
def test_stale_version_is_refused_without_any_change(client, action):
    _, pts = _world(client)
    prepare, call = _stale_cases(client, pts)[action]
    aid = prepare()
    # Другая вкладка уже изменила запись: версия выросла.
    current = consilium_version(aid)
    stale = current - 1 if current > 1 else current + 5
    before = _row(aid)
    audit_before = _rows("SELECT COUNT(*) AS c FROM audit_events")[0]["c"]

    response = call(aid, stale)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": CONFLICT_TEXT, "code": "consilium_version_conflict"}
    assert _row(aid) == before
    assert _rows("SELECT COUNT(*) AS c FROM audit_events")[0]["c"] == audit_before
    assert _rows("SELECT COUNT(*) AS c FROM consilium_resubmissions")[0]["c"] == 0
    # С актуальной версией операция проходит.
    assert call(aid, current).status_code == 200


@pytest.mark.parametrize("action", ["update", "delete", "archive", "restore", "resubmit"])
def test_version_is_required(client, action):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    before = _row(aid)
    if action == "update":
        response = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf,
                              json=appointment_payload(pts["pa1"], scheduledDate=FUTURE, scheduledTime="10:00"))
    elif action == "delete":
        response = client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)
    else:
        response = _post(client, aid, action, {})
    assert response.status_code == 422
    assert _row(aid) == before


def test_two_tabs_cannot_overwrite_a_newer_comment(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    opened = consilium_version(aid)  # обе вкладки открыли форму с этой версией
    tab1 = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pts["pa1"], scheduledDate=FUTURE, scheduledTime="10:00", comment="Важное уточнение вкладки 1", version=opened))
    assert tab1.status_code == 200 and tab1.json()["version"] == opened + 1
    tab2 = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pts["pa1"], scheduledDate=FUTURE, scheduledTime="11:00", version=opened))
    assert tab2.status_code == 409 and tab2.json()["code"] == "consilium_version_conflict"
    assert _row(aid)["comment"] == "Важное уточнение вкладки 1" and _row(aid)["scheduled_time"] == "10:00"


def test_concurrent_edits_with_one_version_only_one_wins(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    opened = consilium_version(aid)
    hold = _Hold(monkeypatch, _consilium(), "_require_group_for_patient")
    tab1, tab2 = _client_for(client, "doc.a"), _client_for(client, "doc.a")
    edit = lambda c, comment: c.put(f"/api/consilium/appointments/{aid}", headers=c.csrf, json=appointment_payload(
        pts["pa1"], scheduledDate=FUTURE, scheduledTime="10:00", comment=comment, version=opened))

    first, second, waited = _interleave(lambda: edit(tab1, "Первая правка"), lambda: edit(tab2, "Вторая правка"), hold)

    assert waited and first.status_code == 200 and second.status_code == 409
    assert _row(aid)["comment"] == "Первая правка"


def test_every_change_raises_the_version(client):
    _, pts = _world(client)
    login(client, "doc.a")
    created = _book(client, pts["pa1"], FUTURE, "10:00").json()
    aid, seen = created["id"], [created["version"]]
    assert seen == [1]
    seen.append(_move(client, aid, pts["pa1"], FUTURE, "11:00").json()["version"])
    login(client, "chair")
    seen.append(_post(client, aid, "review").json()["version"])
    seen.append(_post(client, aid, "return", {"comment": "Уточните"}).json()["version"])
    login(client, "doc.a")
    seen.append(_post(client, aid, "resubmit").json()["version"])
    login(client, "chair")
    seen.append(_post(client, aid, "review").json()["version"])
    seen.append(_post(client, aid, "sign", DECISION).json()["version"])
    seen.append(_post(client, aid, "referral-completion", {"completed": True}).json()["version"])
    login(client, "doc.a")
    seen.append(_post(client, aid, "archive").json()["version"])
    seen.append(_post(client, aid, "restore").json()["version"])
    assert seen == list(range(1, 11))


def test_version_of_a_signed_record_opens_nothing_else(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _sign(client, aid)
    decision = {k: v for k, v in _row(aid).items() if k not in ("version", "updated_at", "archived_at",
                                                                  "archived_by_user_id")}
    conn = get_conn()
    with conn:
        conn.execute("UPDATE oncology_consilium_appointments SET version = version + 1 WHERE id = ?", (aid,))
    for change in ("treatment_type = 'radiotherapy'", "comment = 'подмена'", "backdated = 1, backdating_reason_id = 'x'",
                   "version = version + 1, treatment_scheme = 'подмена'", "scheduled_time = '23:00'"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE oncology_consilium_appointments SET {change} WHERE id = ?", (aid,))
        conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="consilium_version_backwards"):
        conn.execute("UPDATE oncology_consilium_appointments SET version = 1 WHERE id = ?", (aid,))
    conn.rollback()
    assert _post(client, aid, "archive").status_code == 200
    assert {k: v for k, v in _row(aid).items() if k in decision} == decision


# ================================================================ 5. revision and resubmission

def test_reschedule_after_return_keeps_needs_revision_and_one_notification(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _return(client, aid, comment="Добавьте результаты КТ")
    notes = lambda: client.get("/api/internal-notifications?type=consilium_revision").json()["items"]
    assert len(notes()) == 1
    for time, comment in (("11:00", "Черновик 1"), ("11:30", "Черновик 2"), ("11:30", "Черновик 3")):
        moved = _move(client, aid, pts["pa1"], FUTURE, time, comment=comment)
        assert moved.status_code == 200 and moved.json()["status"] == "needs_revision"
        assert _row(aid)["revision_submitted_at"] is None
        assert len(notes()) == 1  # повторные сохранения не создают новых уведомлений
    login(client, "chair")
    refused = _post(client, aid, "review")
    assert refused.status_code == 409  # без явной подачи рассмотрение не начинается
    login(client, "doc.a")
    states = _rows("SELECT COUNT(*) AS c FROM internal_notification_state WHERE notification_type = 'consilium_revision'")
    assert states[0]["c"] == 1


def test_explicit_resubmission_records_author_time_round_and_audit(client):
    ids, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _return(client, aid, comment="Раунд 1")
    response = _post(client, aid, "resubmit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "revised" and body["statusLabel"] == "Возвращен с доработки"
    row = _row(aid)
    sub = _rows("SELECT * FROM consilium_resubmissions WHERE appointment_id = ?", aid)
    assert len(sub) == 1
    assert (sub[0]["round"], sub[0]["submitted_by_user_id"], sub[0]["migrated"]) == (1, ids["doc.a"], 0)
    assert sub[0]["submitted_at"] == row["revision_submitted_at"]
    audit = _audit("consilium_resubmitted")
    assert len(audit) == 1 and audit[0]["detail"] == f"appointment_id={aid};round=1"
    assert audit[0]["patient_id"] == pts["pa1"] and audit[0]["actor_user_id"] == ids["doc.a"]
    assert client.get("/api/internal-notifications?type=consilium_revision").json()["items"] == []
    # Второй раунд — отдельная строка; история возвратов показывает обе подачи.
    _return(client, aid, comment="Раунд 2")
    assert _post(client, aid, "resubmit").status_code == 200
    login(client, "chair")
    history = client.get(f"/api/consilium/appointments/{aid}/revisions").json()["revisions"]
    assert [(h["round"], h["comment"], h["resubmittedByName"], h["resubmissionMigrated"]) for h in history] == [
        (1, "Раунд 1", "Синтетова Анна Врачевна", False),
        (2, "Раунд 2", "Синтетова Анна Врачевна", False),
    ]
    assert all(h["resubmittedAt"] for h in history)
    assert _post(client, aid, "review").status_code == 200


@pytest.mark.parametrize("state", ["recorded", "in_review", "revised", "signed", "signed_archived"])
def test_resubmission_only_from_needs_revision(client, state):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    if state == "in_review":
        login(client, "chair")
        _post(client, aid, "review")
        login(client, "doc.a")
    if state == "revised":
        _return(client, aid)
        assert _post(client, aid, "resubmit").status_code == 200
    if state.startswith("signed"):
        _sign(client, aid)
        if state == "signed_archived":
            assert _post(client, aid, "archive").status_code == 200
    before = _row(aid)
    response = _post(client, aid, "resubmit")
    assert response.status_code == 409
    assert _row(aid) == before


# WP2.3-B2: заведующий отделения пациента управляет записью (в том числе подает
# повторно) независимо от автора; его успешный случай — в test_wp23b2_*.
@pytest.mark.parametrize("who, expected", [("doc.b", 404), ("dev", 404), ("chair", 403),
                                           ("deputy", 403), ("caop", 403)])
def test_resubmission_rights_follow_the_existing_rules(client, who, expected):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _return(client, aid)
    before = _row(aid)
    login(client, DEV_USERNAME if who == "dev" else who)
    assert _post(client, aid, "resubmit", {"version": consilium_version(aid)}).status_code == expected
    assert _row(aid) == before


def test_resubmission_audit_outage_rolls_everything_back(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _return(client, aid)
    before = _row(aid)
    _fail_audit("consilium_resubmitted")
    response = _post(client, aid, "resubmit")
    assert response.status_code == 503 and "журнала аудита" in response.json()["detail"]
    assert _row(aid) == before
    assert _rows("SELECT COUNT(*) AS c FROM consilium_resubmissions")[0]["c"] == 0
    get_conn().execute("DROP TRIGGER wp23a_audit_down")
    get_conn().commit()
    assert _post(client, aid, "resubmit").status_code == 200


def test_resubmission_history_cannot_be_changed_or_lost(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    _return(client, aid)
    assert _post(client, aid, "resubmit").status_code == 200
    conn = get_conn()
    for sql in ("UPDATE consilium_resubmissions SET submitted_at = '2000-01-01'",
                "DELETE FROM consilium_resubmissions",
                # Каскад от жесткого удаления записи тоже не стирает историю.
                f"DELETE FROM oncology_consilium_appointments WHERE id = '{aid}'"):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_resubmission_immutable"):
            conn.execute(sql)
        conn.rollback()
    # Отмена через API — мягкая: запись и вся история доработок остаются.
    _return(client, aid, comment="Еще раз")
    assert _delete(client, aid).status_code == 200
    assert _row(aid)["deleted_at"] is not None
    assert len(_rows("SELECT id FROM consilium_resubmissions WHERE appointment_id = ?", aid)) == 1
    assert len(_rows("SELECT id FROM consilium_revision_history WHERE appointment_id = ?", aid)) == 2


def test_record_without_history_is_still_hard_deleted(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    response = _delete(client, aid)
    assert response.status_code == 200 and response.json()["historyPreserved"] is False
    assert _row(aid) is None


# ================================================================ 6. B3 fields only through the backdating API
# WP2.3-B3 включил структуры задним числом — только через отдельные операции
# (test_wp23b3_backdating.py). Обычная запись, правка и генерация по-прежнему
# не принимают и не создают их.

def test_backdating_fields_are_not_writable_through_the_api(client):
    _, pts = _world(client)
    login(client, "doc.a")
    payload = {**appointment_payload(pts["pa1"], scheduledDate=FUTURE, scheduledTime="10:00"), "backdated": True}
    assert client.post("/api/consilium/appointments", json=payload, headers=client.csrf).status_code == 422
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    put = {**appointment_payload(pts["pa1"], scheduledDate=FUTURE, scheduledTime="10:00",
                                 version=consilium_version(aid)), "backdatingReasonId": "x"}
    assert client.put(f"/api/consilium/appointments/{aid}", json=put, headers=client.csrf).status_code == 422
    login(client, "chair")
    generated = client.post("/api/consilium/schedule/generate", headers=client.csrf, json={
        "scheduledDate": FUTURE2, "consiliumGroup": "thoracic", "startTime": "10:00", "endTime": "11:00",
        "intervalMinutes": 30, "backdated": True})
    assert generated.status_code == 422
    assert _rows("SELECT COUNT(*) AS c FROM consilium_backdating_reasons")[0]["c"] == 0
    assert _rows("SELECT COUNT(*) AS c FROM oncology_consilium_appointments WHERE backdated = 1")[0]["c"] == 0
    assert _rows("SELECT COUNT(*) AS c FROM consilium_schedule_slots WHERE backdated = 1")[0]["c"] == 0


def test_backdating_columns_require_a_reason(client):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    conn = get_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE oncology_consilium_appointments SET backdated = 1 WHERE id = ?", (aid,))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="consilium_backdating_reason_immutable"):
        conn.execute("INSERT INTO consilium_backdating_reasons (id, kind, reason, created_by_user_id, created_at) "
                     "SELECT 'r1', 'slot', 'Синтетическая причина', id, '2026-01-01' FROM users LIMIT 1")
        conn.execute("UPDATE consilium_backdating_reasons SET reason = 'подмена' WHERE id = 'r1'")
    conn.rollback()


# ================================================================ 7. history, frontend
# Права шести ролей: test_wp23a_scheduling_integrity.py::test_rights_matrix_is_unchanged и
# test_consilium_security.py::test_consilium_endpoint_authorization_matrix (с /resubmit),
# права повторной подачи — test_resubmission_rights_follow_the_existing_rules.

def test_repeated_consiliums_leave_medical_history_untouched(client):
    _, pts = _world(client)
    pid = pts["pa1"]
    before = _med_digest(pid)
    login(client, "doc.a")
    first = _book(client, pid, FUTURE, "10:00").json()["id"]
    _return(client, first)
    assert _move(client, first, pid, FUTURE, "11:00").status_code == 200
    assert _post(client, first, "resubmit").status_code == 200
    _sign(client, first)
    assert _post(client, first, "archive").status_code == 200
    second = _book(client, pid, FUTURE2, "10:00").json()["id"]
    _sign(client, second)
    third = _book(client, pid, FUTURE2, "11:00").json()["id"]
    assert _delete(client, third).status_code == 200
    after = _med_digest(pid)
    before["patient"].pop("updated_at", None)
    after["patient"].pop("updated_at", None)
    assert after == before
    assert not [e for e in after["events"] if e.get("consilium_decision")]
    # Фактические даты событий маршрута — без изменений.
    assert [(e["actual_date"], e["actual_time"]) for e in after["events"]] == [
        (e["actual_date"], e["actual_time"]) for e in before["events"]]


def test_static_app_sends_versions_and_handles_conflicts():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert "version: this.consilium.editingVersion" in js                       # PUT
    assert "?version=${encodeURIComponent(item.version)}" in js                # DELETE
    assert "{ version: item.version }" in js                                    # archive / restore
    assert "/resubmit`, { version: saved.version }" in js                       # повторная подача
    assert "error.data.code === 'consilium_version_conflict'" in js
    assert 'id="consiliumConflictRefreshBtn">Обновить данные</button>' in html
    assert 'id="consiliumDetailsConflictRefreshBtn">Обновить данные</button>' in html
    assert 'id="resubmitConsiliumBtn">Отправить на повторное рассмотрение</button>' in html
    # Конфликт блокирует повторную отправку устаревшей версии, текст пользователя сохраняется.
    conflict = js[js.index("    showConsiliumConflict(error) {"):js.index("    async refreshConsiliumAfterConflict()")]
    assert "$('submitConsiliumBtn').disabled = true" in conflict and "consiliumComment" not in conflict
    refresh = js[js.index("    async refreshConsiliumAfterConflict()"):js.index("    async refreshConsiliumDetailsAfterConflict()")]
    assert "const draft = $('consiliumComment').value" in refresh and "consiliumConflictDraftText" in refresh
    assert "Исключены пациенты с незавершенной записью на консилиум." in js
    # Сохранение формы больше не подменяет повторную подачу.
    start = js.index("    async resubmitConsiliumAppointment()")
    resubmit = js[start:js.index("\n    },\n", start)]
    assert "saveConsiliumForm()" in resubmit and "/resubmit" in resubmit
    for text in ("Данные записи изменились", "несохраненный", "незавершенной", "Обновить данные",
                 "Отправить на повторное рассмотрение"):
        assert "ё" not in text
