"""Целостность медицинской истории: мягкое удаление и восстановление событий,
адресация по стабильному id и проверка версии (WP2.1)."""
import json
import sqlite3

from .conftest import (
    event_add_body,
    stage_body,
    DEV_USERNAME,
    create_account,
    create_department,
    event_ref,
    get_conn,
    login,
    legacy_export,
    make_patient_payload,
    unsigned_import,
)
from .test_consilium_appointments import appointment_payload
from .test_preprod2_reminders import _event, _queued, _setup


def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _events(client, pid, occ=0):
    return client.get(f"/api/patients/{pid}").json()["history"][occ]["events"]


def _delete(client, pid, event_id, version):
    return client.delete(f"/api/patients/{pid}/events/{event_id}?version={version}", headers=client.csrf)


def _restore(client, pid, event_id, version):
    return client.post(f"/api/patients/{pid}/events/{event_id}/restore", json={"version": version}, headers=client.csrf)


def _patch(client, pid, event_id, field, value, version):
    return client.patch(f"/api/patients/{pid}/events/{event_id}",
                        json={"field": field, "value": value, "version": version}, headers=client.csrf)


def _deleted(client, pid):
    r = client.get(f"/api/patients/{pid}/events/deleted")
    assert r.status_code == 200, r.text
    return r.json()["events"]


def _row(event_id):
    return dict(get_conn().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())


def _audit(pid):
    return [dict(r) for r in get_conn().execute(
        "SELECT event_type, detail, actor_user_id FROM audit_events WHERE patient_id = ? ORDER BY id", (pid,))]


def _decision_patient(client):
    """Пациент с ручной отметкой решения ОК (штатный путь смены этапа)."""
    patient = _create(client, stage="consult", events=[{"date": "2030-01-10", "time": "10:00"}])
    r = client.post(f"/api/patients/{patient['id']}/stage", json=stage_body(client, patient['id'], {
        "newStage": "treatment-prep",
        "lastEventUpdate": {"actualDate": "2030-01-10", "actualTime": "10:00", "matchedPlan": True,
                            "consiliumDecision": "treatment_strategy_defined"},
    }), headers=client.csrf)
    assert r.status_code == 200, r.text
    return patient["id"]


# 1–6. Удаление и восстановление событий разного вида -------------------------

def test_soft_delete_and_restore_keep_every_kind_of_event_intact(auth_client):
    planned = _create(auth_client, events=[{"date": "2030-01-10", "time": "10:00"}])["id"]
    actual = _create(auth_client, events=[{"date": "2030-01-10", "time": "10:00", "actualDate": "2030-01-10", "actualTime": "10:00"}])["id"]
    decision = _decision_patient(auth_client)
    for pid in (planned, actual, decision):
        before = _events(auth_client, pid)[0]
        stored = _row(before["id"])
        r = _delete(auth_client, pid, before["id"], before["version"])
        assert r.status_code == 200, r.text
        assert r.json()["history"][0]["events"] == []          # исчезло из активной истории
        row = _row(before["id"])                                 # но строка в БД сохранена
        assert row["deleted_at"] and row["deleted_by_user_id"]
        for column in ("date", "time", "actual_date", "actual_time", "consilium_decision", "seq", "history_entry_id"):
            assert row[column] == stored[column], column
        listed = _deleted(auth_client, pid)
        assert [e["id"] for e in listed] == [before["id"]]
        assert listed[0]["historyIndex"] == 0 and listed[0]["deletedByName"]
        # Повторное удаление предсказуемо отклоняется.
        again = _delete(auth_client, pid, before["id"], listed[0]["version"])
        assert again.status_code == 409 and "уже удалено" in again.json()["detail"]
        restored = _restore(auth_client, pid, before["id"], listed[0]["version"])
        assert restored.status_code == 200, restored.text
        after = restored.json()["history"][0]["events"]
        assert len(after) == 1 and after[0]["id"] == before["id"]  # без дубликатов
        assert {k: v for k, v in after[0].items() if k != "version"} == {k: v for k, v in before.items() if k != "version"}
        assert after[0]["version"] == before["version"] + 2
        assert _deleted(auth_client, pid) == []
        # Повторное восстановление предсказуемо отклоняется.
        twice = _restore(auth_client, pid, before["id"], after[0]["version"])
        assert twice.status_code == 409 and "не удалено" in twice.json()["detail"]
        assert _row(before["id"])["deleted_at"] is None


def test_restore_returns_event_to_its_place_without_changing_the_stage(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}, {"date": "2030-03-10"}])["id"]
    middle = _events(auth_client, pid)[1]
    _delete(auth_client, pid, middle["id"], middle["version"])
    r = auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {"newStage": "appointment"}), headers=auth_client.csrf)
    assert r.status_code == 200
    version = _deleted(auth_client, pid)[0]["version"]
    restored = _restore(auth_client, pid, middle["id"], version).json()
    assert restored["stage"] == "appointment"                    # этап не меняется
    assert [e["date"] for e in restored["history"][0]["events"]] == ["2030-01-10", "2030-02-10", "2030-03-10"]
    assert restored["history"][1]["events"] == []


def test_signed_consilium_decision_is_untouched_by_event_deletion(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = _create(client, events=[{"date": "2030-01-10", "time": "10:00"}])
    created = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf).json()
    create_account("chair.events", "consilium_chair")
    login(client, "chair.events")
    assert client.post(f"/api/consilium/appointments/{created['id']}/review", json={}, headers=client.csrf).status_code == 200
    assert client.post(f"/api/consilium/appointments/{created['id']}/sign",
                       json={"treatmentType": "surgery", "treatmentScheme": ""}, headers=client.csrf).status_code == 200
    columns = "status, treatment_type, signed_at, signed_route_stage, signed_route_history_entry_id, signed_route_entered_at"
    before = dict(get_conn().execute(f"SELECT {columns} FROM oncology_consilium_appointments WHERE id = ?", (created["id"],)).fetchone())
    login(client, DEV_USERNAME)
    event = _events(client, patient["id"])[0]
    assert _delete(client, patient["id"], event["id"], event["version"]).status_code == 200
    after = dict(get_conn().execute(f"SELECT {columns} FROM oncology_consilium_appointments WHERE id = ?", (created["id"],)).fetchone())
    assert after == before and after["status"] == "signed"


# 7–8, 21–25. Права, область доступа, отсутствие события ---------------------

def test_event_operations_follow_patient_scope_and_write_rights(client):
    dept_a = create_department("События А")
    dept_b = create_department("События Б")
    create_account("ev.dev", "developer")
    create_account("ev.head.a", "department_head", department_ids=[dept_a])
    create_account("ev.doc.a", "doctor", department_ids=[dept_a])
    create_account("ev.doc.b", "doctor", department_ids=[dept_b])
    create_account("ev.deputy", "deputy_chief_oncology")
    create_account("ev.caop", "caop_head")
    create_account("ev.chair", "consilium_chair")
    login(client, "ev.doc.a")
    own = _create(client, events=[{"date": "2030-01-10"}])["id"]
    login(client, "ev.doc.b")
    other = _create(client, events=[{"date": "2030-01-10"}])["id"]
    login(client, "ev.dev")
    event_id, version = event_ref(client, own)
    foreign_id, _ = event_ref(client, other)

    # Чужие роли: не видят пациента (404) или не имеют права записи (403).
    for username, code in (("ev.doc.b", 404), ("ev.chair", 404), ("ev.deputy", 403), ("ev.caop", 403)):
        login(client, username)
        assert _delete(client, own, event_id, version).status_code == code, username
        assert _patch(client, own, event_id, "date", "2030-02-02", version).status_code == code, username
        assert _restore(client, own, event_id, version).status_code == code, username
        assert client.get(f"/api/patients/{own}/events/deleted").status_code == code, username
    assert _row(event_id)["deleted_at"] is None and _row(event_id)["version"] == version

    # Событие другого пациента через своего пациента не находится.
    login(client, "ev.doc.a")
    assert _delete(client, own, foreign_id, 1).status_code == 404
    assert _patch(client, own, foreign_id, "date", "2030-02-02", 1).status_code == 404
    assert _restore(client, own, foreign_id, 1).status_code == 404
    assert _delete(client, own, "no-such-event", 1).status_code == 404

    # Врач, заведующий и разработчик в своей области работают.
    for username in ("ev.doc.a", "ev.head.a", "ev.dev"):
        login(client, username)
        event_id, version = event_ref(client, own)
        assert _patch(client, own, event_id, "time", "09:00", version).status_code == 200, username
        _, version = event_ref(client, own)
        assert _delete(client, own, event_id, version).status_code == 200, username
        version = _deleted(client, own)[0]["version"]
        assert _restore(client, own, event_id, version).status_code == 200, username


# 9–11. Стабильный id, устаревший индекс, одновременная правка ---------------

def test_stale_card_cannot_change_another_event(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    first, second = _events(auth_client, pid)
    # Старая адресация по позиции отключена — иначе после удаления первого
    # события «событие 0» стало бы вторым.
    assert auth_client.patch(f"/api/patients/{pid}/history/0/events/0",
                             json={"field": "date", "value": "2030-03-03"}, headers=auth_client.csrf).status_code == 410
    assert auth_client.delete(f"/api/patients/{pid}/history/0/events/0", headers=auth_client.csrf).status_code == 410
    assert _delete(auth_client, pid, first["id"], first["version"]).status_code == 200
    # Карточка, открытая до удаления, правит второе событие по его id.
    r = _patch(auth_client, pid, second["id"], "date", "2030-04-04", second["version"])
    assert r.status_code == 200
    assert _row(second["id"])["date"] == "2030-04-04" and _row(first["id"])["date"] == "2030-01-10"
    # Правка удалённого события из устаревшей карточки отклоняется.
    stale = _patch(auth_client, pid, first["id"], "date", "2030-05-05", first["version"])
    assert stale.status_code == 409 and "удалено" in stale.json()["detail"]


def test_concurrent_edits_with_the_same_version_conflict(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10", "time": "10:00"}])["id"]
    event = _events(auth_client, pid)[0]
    first = _patch(auth_client, pid, event["id"], "time", "11:00", event["version"])
    second = _patch(auth_client, pid, event["id"], "time", "12:00", event["version"])
    assert first.status_code == 200
    assert second.status_code == 409 and "изменено другим пользователем" in second.json()["detail"]
    assert _row(event["id"])["time"] == "11:00"
    # Удаление и восстановление тоже требуют актуальной версии.
    assert _delete(auth_client, pid, event["id"], event["version"]).status_code == 409
    assert _row(event["id"])["deleted_at"] is None


def test_stage_change_updates_the_last_active_event_only(auth_client):
    pid = _create(auth_client, stage="exam", events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    first, last = _events(auth_client, pid)
    assert _delete(auth_client, pid, last["id"], last["version"]).status_code == 200
    r = auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {
        "newStage": "appointment", "lastEventUpdate": {"actualDate": "2030-01-10", "matchedPlan": True},
    }), headers=auth_client.csrf)
    assert r.status_code == 200
    assert _row(first["id"])["actual_date"] == "2030-01-10"
    assert _row(last["id"])["actual_date"] is None and _row(last["id"])["deleted_at"]


# 12–13. SMS-напоминания ----------------------------------------------------

def test_deletion_cancels_pending_reminder_and_restore_does_not_duplicate_it(auth_client):
    pid = _setup(auth_client)                      # одно событие и одно ожидающее напоминание
    assert [q["status"] for q in _queued(get_conn(), pid)] == ["pending"]
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event["id"], event["version"]).status_code == 200
    queued = _queued(get_conn(), pid)
    assert [q["status"] for q in queued] == ["cancelled"]
    # Генерация не ставит напоминаний по удалённому событию.
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] == 0
    version = _deleted(auth_client, pid)[0]["version"]
    assert _restore(auth_client, pid, event["id"], version).status_code == 200
    # Восстановление само ничего не отправляет; обычная генерация ставит одно.
    assert [q["status"] for q in _queued(get_conn(), pid)] == ["cancelled"]
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] == 1
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] == 0
    assert sorted(q["status"] for q in _queued(get_conn(), pid)) == ["cancelled", "pending"]


# 14–15. Аналитика и качество данных -----------------------------------------

def test_deleted_event_leaves_quality_and_analytics(auth_client):
    pid = _create(auth_client, stage="appointment", events=[{"date": "2000-01-01"}])["id"]

    def overdue():
        quality = auth_client.get(f"/api/data-quality/patients/{pid}").json()["issues"]
        analytics = auth_client.get("/api/analytics/details?type=quality_all&code=overdue_event").json()
        return [i["code"] for i in quality].count("overdue_event"), analytics["total"]

    assert overdue() == (1, 1)
    event = _events(auth_client, pid)[0]
    _delete(auth_client, pid, event["id"], event["version"])
    assert overdue() == (0, 0)
    _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"])
    assert overdue() == (1, 1)


# 16. Экспорт и импорт ------------------------------------------------------

def test_export_keeps_format_and_import_does_not_revive_deleted_events(auth_client):
    pid = _create(auth_client, name="Экспорт События", events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    first = _events(auth_client, pid)[0]
    _delete(auth_client, pid, first["id"], first["version"])
    export = auth_client.get("/api/export", headers=auth_client.csrf)
    assert export.status_code == 200
    patient = next(p for p in export.json()["patients"] if p["id"] == pid)
    events = patient["history"][0]["events"]
    assert [e["date"] for e in events] == ["2030-02-10"]
    # Подписанный формат: стабильные id есть, служебной версии нет.
    assert all("id" in e and "version" not in e for e in events) and "signature" in export.json()
    # Добавление записи с ID карточки, где есть удалённые события, отклоняется:
    # копия карточки не создаётся, архив исходной не меняется.
    before = get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    r = auth_client.post("/api/import/commit", json={"raw": export.text, "mode": "add"}, headers=auth_client.csrf)
    assert r.status_code == 409, r.text
    assert get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0] == before
    # Без ID (сторонний файл) запись импортируется как новая — только с активными событиями.
    document = legacy_export(export.json())
    for p in document["patients"]:
        p.pop("id", None)
    r = unsigned_import(auth_client, json.dumps(document), "add")
    assert r.status_code == 200, r.text
    copies = [p for p in auth_client.get("/api/patients").json() if p["name"] == "Экспорт События" and p["id"] != pid]
    assert len(copies) == 1
    copy = auth_client.get(f"/api/patients/{copies[0]['id']}").json()
    assert [e["date"] for e in copy["history"][0]["events"]] == ["2030-02-10"]
    assert _deleted(auth_client, copies[0]["id"]) == []
    # Исходный пациент сохранил удалённое событие.
    assert [e["id"] for e in _deleted(auth_client, pid)] == [first["id"]]


# 17–18. Миграция ------------------------------------------------------------

def test_v19_database_migrates_keeping_events_active_and_reinit_is_safe(tmp_path):
    from backend.app import config, db
    from backend.app.maintenance import identify_backup_build

    path = tmp_path / "v19.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    with conn:
        conn.execute("INSERT INTO patients (id, name, priority, stage, arrival, created_at) VALUES ('p1', 'Синтетический', 'normal', 'exam', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO history_entries (id, patient_id, seq, stage, entered_at) VALUES ('h1', 'p1', 0, 'exam', '2026-01-01')")
        conn.execute("INSERT INTO events (id, history_entry_id, seq, date, actual_date) VALUES ('e1', 'h1', 0, '2026-01-10', '2026-01-10')")
        # Привести таблицу событий к виду v19: без новых колонок, индекса и триггера.
        for trigger in ("trg_events_archived_not_deleted", "trg_events_archived_not_replaced",
                        "trg_events_archived_not_updated"):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DROP INDEX IF EXISTS idx_events_history_active")
        for column in ("version", "deleted_by_user_id", "deleted_at"):
            conn.execute(f"ALTER TABLE events DROP COLUMN {column}")
        conn.execute("UPDATE schema_version SET version = 19")
    # Резервная копия опубликованной v19 принимается к восстановлению.
    conn.close()
    assert identify_backup_build(path, 19) == "integration-a9a3a20"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    row = dict(conn.execute("SELECT * FROM events WHERE id = 'e1'").fetchone())
    assert row["deleted_at"] is None and row["deleted_by_user_id"] is None and row["version"] == 1
    assert row["date"] == "2026-01-10" and row["actual_date"] == "2026-01-10"
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION == 20
    db.init_db(conn)                                             # повторная инициализация безопасна
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert {r[1] for r in conn.execute("PRAGMA index_list(events)")} >= {"idx_events_history", "idx_events_history_active"}
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")} >= {
        "trg_events_archived_not_deleted", "trg_events_archived_not_replaced", "trg_events_archived_not_updated"}
    conn.close()


# 20. Аудит без персональных и медицинских данных ----------------------------

def test_delete_and_restore_audit_is_distinct_and_has_no_phi(auth_client):
    pid = _decision_patient(auth_client)
    event = _events(auth_client, pid)[0]
    _delete(auth_client, pid, event["id"], event["version"])
    _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"])
    rows = _audit(pid)
    deleted = [r for r in rows if r["event_type"] == "patient_event_deleted"]
    restored = [r for r in rows if r["event_type"] == "patient_event_restored"]
    assert len(deleted) == len(restored) == 1
    assert f"event_id={event['id']}" in deleted[0]["detail"] and "action=event_delete" in deleted[0]["detail"]
    assert "planned=1;actual=1;decision=1" in deleted[0]["detail"]
    assert "action=event_restore" in restored[0]["detail"]
    assert deleted[0]["actor_user_id"] and restored[0]["actor_user_id"]
    text = " ".join(r["detail"] or "" for r in rows)
    for secret in ("2030-01-10", "treatment_strategy", "C34", "Тестов", "+7"):
        assert secret not in text, secret


# Смена этапа: факт пишется только в то событие, которое видел пользователь -----

def test_stage_change_with_stale_last_event_is_rejected(auth_client):
    pid = _create(auth_client, stage="exam", events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    first, last = _events(auth_client, pid)
    assert _delete(auth_client, pid, last["id"], last["version"]).status_code == 200
    _restore(auth_client, pid, last["id"], _deleted(auth_client, pid)[0]["version"])
    # Карточка видела «последнее» событие до восстановления — сейчас последнее другое.
    stale = auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {
        "newStage": "appointment", "lastEventUpdate": {"actualDate": "2030-01-10"},
        "lastEventId": first["id"], "lastEventVersion": first["version"],
    }), headers=auth_client.csrf)
    assert stale.status_code == 409
    assert auth_client.get(f"/api/patients/{pid}").json()["stage"] == "exam"
    assert _row(first["id"])["actual_date"] is None and _row(last["id"])["actual_date"] is None
    current = _events(auth_client, pid)[-1]
    ok = auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {
        "newStage": "appointment", "lastEventUpdate": {"actualDate": "2030-02-10"},
        "lastEventId": current["id"], "lastEventVersion": current["version"],
    }), headers=auth_client.csrf)
    assert ok.status_code == 200
    assert _row(current["id"])["actual_date"] == "2030-02-10"


def test_stage_change_does_not_write_into_an_event_deleted_concurrently(auth_client):
    """Удаление между выборкой последнего события и записью факта (гонка).

    Смена этапа держит блокировку записи с первой выборки (BEGIN IMMEDIATE):
    параллельное удаление не может вклиниться между проверкой и записью — оно
    ждёт и получает отказ, а факт пишется в событие, которое видел клиент."""
    from backend.app import store

    pid = _create(auth_client, stage="exam", events=[{"date": "2030-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    entry_id = auth_client.get(f"/api/patients/{pid}").json()["history"][-1]["id"]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    real = sqlite3.connect(path)
    real.row_factory = sqlite3.Row
    attempts = []

    class RacingConnection:
        """Пропускает запросы к настоящему соединению; сразу после выборки
        событий этапа другое соединение пытается удалить событие."""

        def __init__(self, conn):
            self._conn, self._fired = conn, False

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, params=()):
            cursor = self._conn.execute(sql, params)
            if not self._fired and "FROM events WHERE history_entry_id" in sql and "deleted_at IS NULL" in sql:
                self._fired = True
                other = sqlite3.connect(path, timeout=0.2)
                other.row_factory = sqlite3.Row
                try:
                    store.delete_event(other, pid, event["id"], event["version"], None)
                    attempts.append("deleted")
                except sqlite3.OperationalError as exc:
                    attempts.append(str(exc))
                finally:
                    other.close()
            return cursor

    try:
        assert store.change_stage(RacingConnection(real), pid, "appointment", store.EventIn(actualDate="2030-01-10"),
                                  expected_event_id=event["id"], expected_event_version=event["version"],
                                  expected_stage="exam",
                                  expected_history_entry_id=entry_id)
    finally:
        real.close()
    assert attempts == ["database is locked"]
    row = _row(event["id"])
    assert row["deleted_at"] is None and row["actual_date"] == "2030-01-10"
    assert auth_client.get(f"/api/patients/{pid}").json()["stage"] == "appointment"
    # Удаление по версии, которую видел клиент до смены этапа, теперь устарело.
    assert _delete(auth_client, pid, event["id"], event["version"]).status_code == 409


def test_oversized_version_is_rejected_without_server_error(auth_client):
    pid = _create(auth_client, events=[{"date": "2030-01-10"}])["id"]
    event_id, _ = event_ref(auth_client, pid)
    huge = 2 ** 70
    assert _delete(auth_client, pid, event_id, huge).status_code == 422
    assert _restore(auth_client, pid, event_id, huge).status_code == 422
    assert _patch(auth_client, pid, event_id, "date", "2030-02-02", huge).status_code == 422


def test_event_limit_counts_active_events_and_restore_respects_it(auth_client, monkeypatch):
    from backend.app import config

    monkeypatch.setattr(config, "MAX_EVENTS_PER_HISTORY_ENTRY", 2)
    pid = _create(auth_client, events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}])["id"]
    first = _events(auth_client, pid)[0]
    assert auth_client.post(f"/api/patients/{pid}/history/0/events", json=event_add_body(auth_client, pid, 0), headers=auth_client.csrf).status_code == 422
    _delete(auth_client, pid, first["id"], first["version"])
    # После удаления активных событий меньше лимита — новое добавляется.
    assert auth_client.post(f"/api/patients/{pid}/history/0/events", json=event_add_body(auth_client, pid, 0), headers=auth_client.csrf).status_code == 200
    # Восстановление сверх лимита отклоняется, событие остаётся удалённым.
    r = _restore(auth_client, pid, first["id"], _deleted(auth_client, pid)[0]["version"])
    assert r.status_code == 422
    assert _row(first["id"])["deleted_at"] is not None
