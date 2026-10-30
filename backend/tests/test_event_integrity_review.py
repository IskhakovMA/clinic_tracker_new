"""WP2.1, замечания ревью F2–F6: удалённые события медицинской истории не
уничтожаются и не оживают через импорт и удаление карточки, факт этапа пишется
только с проверкой версии, неподдерживаемая схема БД останавливает запуск,
удаление и восстановление события не сохраняются без записи аудита.

Все данные синтетические; каждая проверка смотрит и на ответ, и на базу."""
import json
import sqlite3

import pytest

from .conftest import (
    DEV_USERNAME,
    create_account,
    create_empty_card,
    create_department,
    get_conn,
    login,
    legacy_export,
    make_patient_payload,
    unsigned_import,
    LEGACY_CONSILIUM_DATES,
    consilium_grid,
)
from .test_consilium_appointments import appointment_payload


# --- вспомогательные --------------------------------------------------------

def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _events(client, pid, occ=0):
    return _card(client, pid)["history"][occ]["events"]


def _delete(client, pid, event):
    return client.delete(f"/api/patients/{pid}/events/{event['id']}?version={event['version']}", headers=client.csrf)


def _restore(client, pid, event_id, version):
    return client.post(f"/api/patients/{pid}/events/{event_id}/restore", json={"version": version}, headers=client.csrf)


def _deleted(client, pid):
    return client.get(f"/api/patients/{pid}/events/deleted").json()["events"]


def _row(event_id):
    row = get_conn().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def _snapshot():
    """Полное состояние таблиц маршрута — для проверки «данные не изменены»."""
    conn = get_conn()
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]  # nosec B608
        for table in ("patients", "history_entries", "events", "oncology_consilium_appointments", "notifications")
    }


def _audit(pid=None, event_type=None):
    sql = "SELECT event_type, result, detail, actor_user_id, actor_role FROM audit_events WHERE 1 = 1"
    params = []
    if pid is not None:
        sql += " AND patient_id = ?"
        params.append(pid)
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    return [dict(r) for r in get_conn().execute(sql + " ORDER BY id", params).fetchall()]


def _export(client):
    r = client.get("/api/export", headers=client.csrf)
    assert r.status_code == 200
    return r.json()


def _import(client, document, mode):
    """Подписанная выгрузка — как есть; иначе прежний формат с явным подтверждением."""
    raw = json.dumps(document)
    if "signature" in document:
        return client.post("/api/import/commit", json={"raw": raw, "mode": mode}, headers=client.csrf)
    return unsigned_import(client, raw, mode)


def _preview(client, document):
    r = client.post("/api/import/preview", json={"raw": json.dumps(document)}, headers=client.csrf)
    assert r.status_code == 200, r.text
    return r.json()


def _archived_patient(client, name="Архив Синтетический", *, empty=False):
    """Пациент с двумя событиями, первое из которых удалено (в архиве).

    empty=True — карточка без медицинских полей (WP-C FIX1-P1: только такую можно удалить)."""
    events = [{"date": "2026-01-10"}, {"date": "2026-02-10"}]
    pid = (create_empty_card(client, name=name, events=events) if empty
           else _create(client, name=name, events=events)["id"])
    first = _events(client, pid)[0]
    assert _delete(client, pid, first).status_code == 200
    return pid, first["id"]


def _assert_archive_intact(event_id, deleted_by):
    row = _row(event_id)
    assert row is not None, "удалённое событие физически уничтожено"
    assert row["deleted_at"] and row["deleted_by_user_id"] == deleted_by
    assert row["date"] == "2026-01-10"


def _dev_id():
    return get_conn().execute("SELECT id FROM users WHERE username = ?", (DEV_USERNAME,)).fetchone()["id"]


# === F2. Импорт ==============================================================

def test_update_of_patient_with_deleted_event_is_blocked_and_archive_survives(auth_client):
    pid, archived = _archived_patient(auth_client)
    document = legacy_export(_export(auth_client))            # правка файла снимает подпись
    document["patients"][0]["name"] = "Изменённое Имя"
    before = _snapshot()
    r = _import(auth_client, document, "update")
    assert r.status_code == 409, r.text
    assert "удалёнными событиями" in r.json()["detail"] and "Данные не изменены" in r.json()["detail"]
    assert _snapshot() == before
    _assert_archive_intact(archived, _dev_id())
    denied = _audit(event_type="import_committed")[-1]
    assert denied["result"] == "denied"
    assert denied["detail"].startswith("mode=update archived_event_patients=1 archived_events=1 ")


def test_replace_is_blocked_while_any_patient_has_deleted_events(auth_client):
    _create(auth_client, name="Без Архива", events=[{"date": "2026-03-01"}])
    pid, archived = _archived_patient(auth_client)
    # Файл даже не содержит пациента с архивом: replace удалил бы всех.
    document = {"schemaVersion": 20, "patients": [make_patient_payload(name="Новый Из Файла")]}
    before = _snapshot()
    r = _import(auth_client, document, "replace")
    assert r.status_code == 409, r.text
    assert "Полная замена" in r.json()["detail"]
    assert _snapshot() == before
    _assert_archive_intact(archived, _dev_id())


def test_add_without_archive_conflicts_is_unchanged(auth_client):
    pid, _ = _archived_patient(auth_client)
    document = {"schemaVersion": 20, "patients": [make_patient_payload(name="Новый Пациент Файла")]}
    preview = _preview(auth_client, document)
    assert preview["archivedEventsImpact"]["add"]["blocked"] is False
    r = _import(auth_client, document, "add")
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    # Существующая карточка не тронута.
    assert len(_deleted(auth_client, pid)) == 1


def test_add_with_id_of_archived_card_is_blocked_no_active_duplicate(auth_client):
    pid, archived = _archived_patient(auth_client)
    old_export = legacy_export(_export(auth_client))
    # Старый экспорт до удаления содержал бы событие активным — моделируем его.
    old_export["patients"][0]["history"][0]["events"].insert(0, {"date": "2026-01-10"})
    before = _snapshot()
    r = _import(auth_client, old_export, "add")
    assert r.status_code == 409, r.text
    assert _snapshot() == before
    active = get_conn().execute(
        "SELECT COUNT(*) FROM events WHERE date = '2026-01-10' AND deleted_at IS NULL").fetchone()[0]
    assert active == 0


def test_old_export_does_not_revive_deleted_event_in_any_mode(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}, {"date": "2026-02-10"}])["id"]
    old_export = _export(auth_client)                       # оба события ещё активны
    first = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, first).status_code == 200
    for mode in ("update", "replace", "add"):
        before = _snapshot()
        assert _import(auth_client, old_export, mode).status_code == 409, mode
        assert _snapshot() == before, mode
    _assert_archive_intact(first["id"], _dev_id())
    assert [e["date"] for e in _events(auth_client, pid)] == ["2026-02-10"]


def test_import_after_restore_follows_previous_rules(auth_client):
    pid, archived = _archived_patient(auth_client)
    assert _restore(auth_client, pid, archived, _deleted(auth_client, pid)[0]["version"]).status_code == 200
    document = legacy_export(_export(auth_client))
    preview = _preview(auth_client, document)
    assert preview["archivedEventsImpact"]["update"]["blocked"] is False
    # Совпадающий файл — без изменений; правка значения — конфликт (решение A).
    r = _import(auth_client, document, "update")
    assert r.status_code == 200 and r.json()["unchanged"] == 1, r.text
    document["patients"][0]["name"] = "После Восстановления"
    assert _import(auth_client, document, "update").status_code == 409
    card = _card(auth_client, pid)
    assert card["name"] != "После Восстановления"
    assert [e["date"] for e in card["history"][0]["events"]] == ["2026-01-10", "2026-02-10"]


def test_repeated_blocked_import_changes_nothing(auth_client):
    pid, archived = _archived_patient(auth_client)
    document = _export(auth_client)
    before = _snapshot()
    for _ in range(3):
        assert _import(auth_client, document, "update").status_code == 409
    assert _snapshot() == before
    assert len([a for a in _audit(event_type="import_committed") if a["result"] == "denied"]) == 3


def test_preview_reports_blocked_modes_with_counts_and_reasons(auth_client):
    pid, _ = _archived_patient(auth_client)
    _create(auth_client, name="Без Архива", events=[{"date": "2026-03-01"}])
    document = _export(auth_client)
    report = _preview(auth_client, document)
    impact = report["archivedEventsImpact"]
    assert set(impact) == {"add", "update", "replace"}
    for mode in ("add", "update", "replace"):
        assert impact[mode]["blocked"] is True, mode
        assert impact[mode]["patients"] == 1 and impact[mode]["events"] == 1
        assert impact[mode]["reason"] and impact[mode]["advice"]
    # В отчёте только числа и тексты правил — без медицинских данных.
    text = json.dumps(impact, ensure_ascii=False)
    for secret in ("2026-01-10", "Архив Синтетический", "C34"):
        assert secret not in text


def test_direct_commit_without_preview_is_checked_on_the_server(auth_client):
    pid, archived = _archived_patient(auth_client)
    document = _export(auth_client)
    before = _snapshot()
    assert _import(auth_client, document, "update").status_code == 409   # preview не вызывался
    assert _snapshot() == before


def test_change_between_preview_and_commit_is_caught_at_commit(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}, {"date": "2026-02-10"}])["id"]
    document = _export(auth_client)
    assert _preview(auth_client, document)["archivedEventsImpact"]["update"]["blocked"] is False
    first = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, first).status_code == 200            # после preview
    before = _snapshot()
    assert _import(auth_client, document, "update").status_code == 409
    assert _snapshot() == before
    _assert_archive_intact(first["id"], _dev_id())


def test_mixed_import_is_all_or_nothing(auth_client):
    clean = _create(auth_client, name="Чистая Карточка", events=[{"date": "2026-03-01"}])["id"]
    pid, archived = _archived_patient(auth_client)
    document = legacy_export(_export(auth_client))
    for p in document["patients"]:
        p["name"] = p["name"] + " Изм"
    before = _snapshot()
    assert _import(auth_client, document, "update").status_code == 409
    assert _snapshot() == before
    assert _card(auth_client, clean)["name"] == "Чистая Карточка"


def test_failure_in_the_middle_of_import_rolls_everything_back(auth_client, monkeypatch):
    from backend.app import importexport, policy

    # WP2.4-B (U9): разные синтетические полисы — файл с одинаковыми полисами отклоняется до записи.
    _create(auth_client, name="Первая", policyNumber="7700000000024111", events=[{"date": "2026-03-01"}])
    _create(auth_client, name="Вторая", policyNumber="7700000000024112", events=[{"date": "2026-03-02"}])
    document_json = _export(auth_client)
    document, errors = importexport.validate_import(json.dumps(document_json))
    assert not errors
    original = importexport._insert_full_patient
    calls = []

    def failing(conn, p, *args, **kwargs):
        calls.append(p.name)
        if len(calls) == 2:
            raise RuntimeError("synthetic failure")
        return original(conn, p, *args, **kwargs)

    monkeypatch.setattr(importexport, "_insert_full_patient", failing)
    conn = get_conn()
    actor = policy.load_current_user(conn, _dev_id())
    before = _snapshot()
    # WP2.4-C: копии своих карточек (те же полисы) — с подтверждением всего набора
    # возможных дублей; сбой на второй записи откатывает и записи подтверждений.
    from backend.app.models import DuplicateConfirmation
    impact = importexport.possible_duplicates_impact(conn, document, "add", actor)
    confirmation = DuplicateConfirmation(reason="Синтетическая причина",
                                         matchedPatientIds=impact["matchedPatientIds"])
    with pytest.raises(RuntimeError):
        importexport.commit_import(conn, document, "add", actor, duplicate_confirmation=confirmation,
                                   import_id="synthetic-import")
    assert not conn.in_transaction
    assert _snapshot() == before
    assert conn.execute("SELECT COUNT(*) FROM patient_duplicate_confirmations").fetchone()[0] == 0


def test_database_trigger_backs_up_the_import_check(auth_client, monkeypatch):
    """Даже если проверка импорта пропущена, база не даёт стереть архив."""
    from backend.app import importexport, policy

    pid, archived = _archived_patient(auth_client)
    document, _ = importexport.validate_import(json.dumps(_export(auth_client)))
    monkeypatch.setattr(importexport, "archived_event_conflicts", lambda *a, **k: (0, 0))
    conn = get_conn()
    before = _snapshot()
    # Без проверки архива update всё равно не удаляет карточку (решение A): отказ.
    with pytest.raises((importexport.ImportArchiveConflictError, importexport.ImportOverwriteError)):
        importexport.commit_import(conn, document, "update", policy.load_current_user(conn, _dev_id()))
    assert _snapshot() == before
    # Прямой SQL тоже отклоняется (WP-C: срабатывает и защита истории карточки — порядок
    # триггеров SQLite не определён, поэтому принимается сообщение любой из двух защит).
    with pytest.raises(sqlite3.IntegrityError, match="archived_event_delete_forbidden|patient_has_clinical_history"):
        with conn:
            conn.execute("DELETE FROM patients WHERE id = ?", (pid,))
    _assert_archive_intact(archived, _dev_id())


def test_consilium_block_is_kept_and_both_histories_survive(auth_client):
    pid, archived = _archived_patient(auth_client)
    consilium_grid(LEGACY_CONSILIUM_DATES)  # WP2.3-B2: запись только в слот председателя
    created = auth_client.post("/api/consilium/appointments", json=appointment_payload(pid), headers=auth_client.csrf)
    assert created.status_code in (200, 201), created.text
    document = _export(auth_client)
    report = _preview(auth_client, document)
    assert report["consiliumImpact"]["update"]["blocked"] is True
    assert report["archivedEventsImpact"]["update"]["blocked"] is True
    before = _snapshot()
    for mode in ("update", "replace"):
        r = _import(auth_client, document, mode)
        assert r.status_code == 409 and "онкоконсилиума" in r.json()["detail"], mode
    assert _snapshot() == before
    _assert_archive_intact(archived, _dev_id())


def test_doctor_add_import_is_blocked_for_archived_card_ids(client):
    dept = create_department("Импорт Архив")
    create_account("imp.doc", "doctor", department_ids=[dept])
    login(client, "imp.doc")
    pid, archived = _archived_patient(client)
    document = _export(client)
    # Решение B: файл с внутренними id врач не импортирует — до любых проверок базы.
    preview = _preview(client, document)
    assert preview["valid"] is False and preview["code"] == "internal_ids_forbidden"
    before = _snapshot()
    assert _import(client, document, "add").status_code == 403
    assert _import(client, document, "update").status_code == 403
    assert _snapshot() == before


# === F3. Факт события при смене этапа ==========================================

def _stage(client, pid, body):
    """Перевод этапа; этап карточки (expectedStage) подставляется, если тест
    не передал его сам. id и версию события тест задаёт явно."""
    card = _card(client, pid)
    if "expectedStage" not in body:
        body = {"expectedStage": card["stage"], **body}
    if "expectedHistoryEntryId" not in body:
        body = {"expectedHistoryEntryId": card["history"][-1]["id"], **body}
    return client.post(f"/api/patients/{pid}/stage", json=body, headers=client.csrf)


def test_transition_without_expected_stage_is_rejected(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10", "actualDate": "2026-01-10"}])["id"]
    before = _route_state(pid)
    r = auth_client.post(f"/api/patients/{pid}/stage", json={"newStage": "appointment"}, headers=auth_client.csrf)
    assert r.status_code == 409 and "Обновите страницу" in r.json()["detail"]
    assert _route_state(pid) == before
    assert _stage(auth_client, pid, {"newStage": "appointment"}).status_code == 200


def test_concurrent_transitions_from_the_same_stage_apply_once(auth_client):
    import threading
    from backend.app import store

    pid = _create(auth_client, events=[{"date": "2026-01-10", "actualDate": "2026-01-10"}])["id"]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    results, barrier = [], threading.Barrier(2)
    entry_id = _card(auth_client, pid)["history"][-1]["id"]

    def move(target):
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results.append(store.change_stage(conn, pid, target, None, expected_stage="exam",
                                              expected_history_entry_id=entry_id))
        except store.EventConflict:
            results.append("conflict")
        finally:
            conn.close()

    threads = [threading.Thread(target=move, args=(t,)) for t in ("appointment", "consult")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results)) == ["True", "conflict"]
    assert get_conn().execute("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", (pid,)).fetchone()[0] == 2


def _route_state(pid):
    conn = get_conn()
    return (
        conn.execute("SELECT stage FROM patients WHERE id = ?", (pid,)).fetchone()[0],
        [tuple(r) for r in conn.execute(
            """SELECT events.* FROM events JOIN history_entries h ON h.id = events.history_entry_id
               WHERE h.patient_id = ? ORDER BY events.id""", (pid,)).fetchall()],
        conn.execute("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", (pid,)).fetchone()[0],
    )


def test_fact_update_without_event_id_or_version_is_rejected(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    before = _route_state(pid)
    update = {"actualDate": "2026-01-10", "matchedPlan": True}
    for body in (
        {"newStage": "appointment", "lastEventUpdate": update},
        {"newStage": "appointment", "lastEventUpdate": update, "lastEventId": event["id"]},
        {"newStage": "appointment", "lastEventUpdate": update, "lastEventVersion": event["version"]},
    ):
        r = _stage(auth_client, pid, body)
        assert r.status_code == 409, body
        assert "Обновите страницу" in r.json()["detail"]
        assert _route_state(pid) == before


def test_fact_update_with_stale_version_or_wrong_event_is_rejected(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}, {"date": "2026-02-10"}])["id"]
    other = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    first, last = _events(auth_client, pid)
    foreign = _events(auth_client, other)[0]
    before = _route_state(pid)
    update = {"actualDate": "2026-02-10"}
    for ref in (
        {"lastEventId": last["id"], "lastEventVersion": last["version"] + 1},     # устаревшая версия
        {"lastEventId": first["id"], "lastEventVersion": first["version"]},       # не последнее событие
        {"lastEventId": foreign["id"], "lastEventVersion": foreign["version"]},   # событие другого пациента
    ):
        assert _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": update, **ref}).status_code == 409
        assert _route_state(pid) == before
    assert _row(foreign["id"])["actual_date"] is None


def test_transition_without_event_update_needs_no_version(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10", "actualDate": "2026-01-10"}])["id"]
    _, events_before, _ = _route_state(pid)
    r = _stage(auth_client, pid, {"newStage": "appointment"})
    assert r.status_code == 200, r.text
    stage, events_after, history = _route_state(pid)
    assert stage == "appointment" and history == 2 and events_after == events_before


def test_transition_when_all_events_are_deleted_inserts_a_new_one(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event).status_code == 200
    archived_before = _row(event["id"])
    r = _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-01-11"}})
    assert r.status_code == 200, r.text
    assert _row(event["id"]) == archived_before
    active = r.json()["history"][0]["events"]
    assert len(active) == 1 and active[0]["actualDate"] == "2026-01-11" and active[0]["id"] != event["id"]
    # Клиент, который видел удалённое событие, получает конфликт, а не запись в новое.
    pid2 = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    seen = _events(auth_client, pid2)[0]
    assert _delete(auth_client, pid2, seen).status_code == 200
    stale = _stage(auth_client, pid2, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-01-11"},
                                       "lastEventId": seen["id"], "lastEventVersion": seen["version"]})
    assert stale.status_code == 409


def test_two_transitions_from_the_same_card_conflict(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    card = _card(auth_client, pid)
    event = card["history"][0]["events"][0]
    body = {"newStage": "appointment", "expectedStage": card["stage"], "lastEventUpdate": {"actualDate": "2026-01-10"},
            "lastEventId": event["id"], "lastEventVersion": event["version"]}
    assert _stage(auth_client, pid, body).status_code == 200
    after_first = _route_state(pid)
    second = _stage(auth_client, pid, {**body, "newStage": "consult"})
    assert second.status_code == 409
    assert _route_state(pid) == after_first
    # Без факта события устаревший этап тоже не проходит.
    third = _stage(auth_client, pid, {"newStage": "consult", "expectedStage": card["stage"]})
    assert third.status_code == 409 and "Этап пациента уже изменён" in third.json()["detail"]
    assert _route_state(pid) == after_first


def test_delete_or_restore_between_read_and_transition_is_a_conflict(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}, {"date": "2026-02-10"}])["id"]
    first, last = _events(auth_client, pid)
    assert _delete(auth_client, pid, last).status_code == 200            # кто-то удалил «последнее»
    before = _route_state(pid)
    r = _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-02-10"},
                                  "lastEventId": last["id"], "lastEventVersion": last["version"]})
    assert r.status_code == 409 and _route_state(pid) == before
    current_last = _events(auth_client, pid)[-1]                          # теперь последнее — first
    assert _restore(auth_client, pid, last["id"], _deleted(auth_client, pid)[0]["version"]).status_code == 200
    before = _route_state(pid)
    r = _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-01-10"},
                                  "lastEventId": current_last["id"], "lastEventVersion": current_last["version"]})
    assert r.status_code == 409 and _route_state(pid) == before
    # Повтор после обновления карточки проходит и меняет только последнее событие.
    fresh = _events(auth_client, pid)[-1]
    r = _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-02-10"},
                                  "lastEventId": fresh["id"], "lastEventVersion": fresh["version"]})
    assert r.status_code == 200
    assert _row(fresh["id"])["actual_date"] == "2026-02-10" and _row(first["id"])["actual_date"] is None


# === F4. Версия схемы базы данных ==============================================

def _fresh_db(path):
    from backend.app import db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def test_current_schema_initialises_repeatedly(tmp_path):
    from backend.app import config, db
    conn = _fresh_db(tmp_path / "v20.db")
    db.init_db(conn)
    db.init_db(conn)
    assert [tuple(r) for r in conn.execute("SELECT version FROM schema_version")] == [(config.SCHEMA_VERSION,)]
    conn.close()


@pytest.mark.parametrize("setup", [
    "UPDATE schema_version SET version = 99",  # новее текущей (WP2.3-B1: текущая теперь v22)
    "UPDATE schema_version SET version = 0",
    "UPDATE schema_version SET version = -5",
    "UPDATE schema_version SET version = 'двадцать'",
    "INSERT INTO schema_version (version) VALUES (19)",
])
def test_unsupported_or_damaged_schema_version_stops_without_changes(tmp_path, setup):
    from backend.app import db
    path = tmp_path / "bad.db"
    conn = _fresh_db(path)
    with conn:
        conn.execute(setup)
        conn.execute("DROP TABLE internal_notification_state")   # изменение init_db было бы заметно
    before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    versions = conn.execute("SELECT version FROM schema_version").fetchall()
    with pytest.raises(db.SchemaVersionError):
        db.init_db(conn)
    assert not conn.in_transaction
    assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert conn.execute("SELECT version FROM schema_version").fetchall() == versions
    conn.close()


def test_application_refuses_to_start_on_a_newer_schema(client, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import config, db
    from backend.app.main import app

    # Отдельный каталог данных: блокировку единственного процесса держит client.
    other = tmp_path / "other-release"
    other.mkdir(mode=0o700)
    conn = _fresh_db(other / "clinic.db")
    with conn:
        conn.execute("UPDATE schema_version SET version = 99")
    conn.close()
    monkeypatch.setattr(config, "DATA_DIR", other)
    monkeypatch.setattr(config, "DB_PATH", other / "clinic.db")
    db.reset_singleton_for_tests()
    with pytest.raises(db.SchemaVersionError, match="v99"):
        with TestClient(app, base_url="http://127.0.0.1"):
            pass
    raw = sqlite3.connect(other / "clinic.db")
    assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 99  # база «новее» осталась нетронутой
    raw.close()


def test_check_db_reports_a_newer_schema(client, capsys):
    from backend import cli
    from backend.app import db

    with get_conn() as conn:
        conn.execute("UPDATE schema_version SET version = 99")
    db.reset_singleton_for_tests()
    assert cli.main.__module__  # модуль загружен
    import sys
    argv = sys.argv
    sys.argv = ["cli", "check-db"]
    try:
        assert cli.main() == 1
    finally:
        sys.argv = argv
    assert "v99" in capsys.readouterr().err


def test_backups_are_recognised_by_schema_and_a_newer_one_is_rejected(auth_client, tmp_path, monkeypatch):
    from backend.app import config, maintenance

    monkeypatch.setenv("CLINIC_BACKUP_KEY", "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE")
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)

    pid, archived = _archived_patient(auth_client)
    db_path = config.DB_PATH
    # Версии — от текущей сборки (WP2.2-B подняла схему до v21), смысл проверки прежний.
    cur_v, newer_v = config.SCHEMA_VERSION, config.SCHEMA_VERSION + 1
    current = maintenance.backup_database(db_path, tmp_path / f"v{cur_v}.cbk")
    report = maintenance.validate_backup(current)
    assert report["schema_version"] == cur_v and report["source_build"] == "current"

    # Копия, сделанная более новой сборкой, этой сборкой не принимается.
    with get_conn() as conn:
        conn.execute("UPDATE schema_version SET version = ?", (newer_v,))
    monkeypatch.setattr(config, "SCHEMA_VERSION", newer_v)
    newer = maintenance.backup_database(db_path, tmp_path / f"v{newer_v}.cbk")
    monkeypatch.setattr(config, "SCHEMA_VERSION", cur_v)
    with get_conn() as conn:
        conn.execute("UPDATE schema_version SET version = ?", (cur_v,))
    with pytest.raises(maintenance.MaintenanceError, match=f"v{newer_v} не поддерживается"):
        maintenance.validate_backup(newer)
    _assert_archive_intact(archived, _dev_id())


# === F5. Удаление карточки пациента ============================================

def test_patient_with_deleted_event_cannot_be_deleted(auth_client):
    pid, archived = _archived_patient(auth_client, empty=True)
    before = _snapshot()
    r = auth_client.delete(f"/api/patients/{pid}", headers=auth_client.csrf)
    assert r.status_code == 409
    assert "удалённые события" in r.json()["detail"]
    assert _snapshot() == before
    _assert_archive_intact(archived, _dev_id())
    denied = _audit(pid, "patient_deleted")[-1]
    assert denied["result"] == "denied" and denied["detail"] == "archived_events=1"


def test_patient_without_archive_is_deleted_as_before_and_consilium_still_blocks(auth_client):
    plain = create_empty_card(auth_client, events=[{"date": "2026-01-10"}])
    assert auth_client.delete(f"/api/patients/{plain}", headers=auth_client.csrf).status_code == 200
    assert auth_client.get(f"/api/patients/{plain}").status_code == 404
    booked = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]   # запись ОК требует диагноз
    consilium_grid(LEGACY_CONSILIUM_DATES)  # WP2.3-B2: запись только в слот председателя
    assert auth_client.post("/api/consilium/appointments", json=appointment_payload(booked),
                            headers=auth_client.csrf).status_code in (200, 201)
    r = auth_client.delete(f"/api/patients/{booked}", headers=auth_client.csrf)
    assert r.status_code == 409 and "консилиума" in r.json()["detail"]
    assert _audit(booked, "patient_deleted")[-1]["detail"] == (
        "current_diagnosis=1;current_complaint=1;localization=1;consilium_records=1")


def test_patient_delete_follows_restore_state(auth_client):
    pid, archived = _archived_patient(auth_client, empty=True)
    assert auth_client.delete(f"/api/patients/{pid}", headers=auth_client.csrf).status_code == 409
    # Восстановленное событие снова активно; прежняя политика удаления карточки действует.
    assert _restore(auth_client, pid, archived, _deleted(auth_client, pid)[0]["version"]).status_code == 200
    assert auth_client.delete(f"/api/patients/{pid}", headers=auth_client.csrf).status_code == 200


def test_patient_delete_store_check_runs_under_the_write_lock(auth_client):
    from backend.app import store

    pid, archived = _archived_patient(auth_client)
    with pytest.raises(store.PatientDeleteBlocked):
        store.delete_patient(get_conn(), pid)
    assert not get_conn().in_transaction
    _assert_archive_intact(archived, _dev_id())


def test_patient_delete_rights_are_unchanged(client):
    dept_a = create_department("Удаление А")
    dept_b = create_department("Удаление Б")
    create_account("del.doc.a", "doctor", department_ids=[dept_a])
    create_account("del.doc.b", "doctor", department_ids=[dept_b])
    create_account("del.deputy", "deputy_chief_oncology")
    create_account("del.caop", "caop_head")
    login(client, "del.doc.a")
    pid, archived = _archived_patient(client)
    for username, code in (("del.doc.b", 404), ("del.deputy", 403), ("del.caop", 403), ("del.doc.a", 409)):
        login(client, username)
        assert client.delete(f"/api/patients/{pid}", headers=client.csrf).status_code == code, username
    assert _row(archived)["deleted_at"]


# === F6. Аудит удаления и восстановления =====================================

def _fail_audit_for(event_type):
    with get_conn() as conn:
        conn.execute(f"""
            CREATE TRIGGER test_audit_failure BEFORE INSERT ON audit_events
            WHEN NEW.event_type = '{event_type}'
            BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")


def test_delete_and_restore_are_audited_with_the_actor(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event).status_code == 200
    assert _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"]).status_code == 200
    rows = [r for r in _audit(pid) if r["event_type"] in ("patient_event_deleted", "patient_event_restored")]
    assert [r["event_type"] for r in rows] == ["patient_event_deleted", "patient_event_restored"]
    assert all(r["actor_user_id"] == _dev_id() and r["actor_role"] == "developer" and r["result"] == "ok"
               for r in rows)
    assert all(f"event_id={event['id']}" in r["detail"] for r in rows)


@pytest.mark.parametrize("operation", ["delete", "restore"])
def test_audit_failure_rolls_the_change_back(auth_client, operation):
    pid = _setup_with_reminder(auth_client)
    event = _events(auth_client, pid)[0]
    if operation == "restore":
        assert _delete(auth_client, pid, event).status_code == 200
        _fail_audit_for("patient_event_restored")
    else:
        _fail_audit_for("patient_event_deleted")
    before = _snapshot()
    audit_before = len(_audit())
    if operation == "delete":
        r = _delete(auth_client, pid, event)
    else:
        r = _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"])
    assert r.status_code == 503, r.text
    assert "не изменено" in r.json()["detail"]
    assert _snapshot() == before                                  # событие, версия и напоминания прежние
    assert len(_audit()) == audit_before
    # После устранения сбоя операция выполняется с той же версией.
    with get_conn() as conn:
        conn.execute("DROP TRIGGER test_audit_failure")
    if operation == "delete":
        assert _delete(auth_client, pid, event).status_code == 200
    else:
        assert _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"]).status_code == 200


def _setup_with_reminder(client):
    from .test_preprod2_reminders import _setup
    return _setup(client)


def test_delete_restore_delete_chain_is_fully_audited(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event).status_code == 200
    first_deletion = _row(event["id"])["deleted_at"]
    version = _deleted(auth_client, pid)[0]["version"]
    assert _restore(auth_client, pid, event["id"], version).status_code == 200
    assert _restore(auth_client, pid, event["id"], version + 1).status_code == 409      # повторное восстановление
    assert _row(event["id"])["deleted_at"] is None
    current = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, current).status_code == 200
    assert _delete(auth_client, pid, {**current, "version": current["version"] + 1}).status_code == 409  # повторно
    rows = [r for r in _audit(pid) if r["event_type"] in ("patient_event_deleted", "patient_event_restored")]
    assert [r["event_type"] for r in rows] == ["patient_event_deleted", "patient_event_restored", "patient_event_deleted"]
    assert _row(event["id"])["deleted_at"] >= first_deletion
    allowed = {"event_id", "history", "action", "planned", "actual", "decision"}
    for r in rows:
        keys = {part.split("=", 1)[0] for part in r["detail"].split(";")}
        assert keys == allowed, r["detail"]


def test_concurrent_deletes_produce_one_change_and_one_audit_row(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    results = [_delete(auth_client, pid, event).status_code for _ in range(2)]
    assert sorted(results) == [200, 409]
    assert len(_audit(pid, "patient_event_deleted")) == 1


def test_event_audit_has_no_personal_or_medical_values(auth_client):
    pid = _create(auth_client, name="Секретов Пациент", phone="+79990001122", diagnosis="C34.1",
                  events=[{"date": "2026-01-10", "time": "10:00"}])["id"]
    event = _events(auth_client, pid)[0]
    _delete(auth_client, pid, event)
    _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"])
    text = json.dumps(_audit(pid), ensure_ascii=False)
    for secret in ("Секретов", "+7999", "C34", "2026-01-10", "10:00", "Разработчиков"):
        assert secret not in text, secret


def test_audit_prune_keeps_event_deletion_history(auth_client):
    from backend.app import audit

    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event).status_code == 200
    assert _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"]).status_code == 200
    # WP-C (AUD-13): журнал только дополняется — состарить существующие строки нельзя.
    # Правило срока хранения проверяется на состаренных копиях тех же типов событий.
    with get_conn() as conn:
        conn.execute("""INSERT INTO audit_events (created_at, event_type, patient_id, result, detail)
                        SELECT '2000-01-01T00:00:00+00:00', event_type, patient_id, result, detail
                        FROM audit_events WHERE patient_id = ?""", (pid,))
    recent = get_conn().execute(
        "SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND created_at NOT LIKE '2000-%'", (pid,)).fetchone()[0]
    audit.prune(get_conn(), retention_days=30)
    kept_old = [r[0] for r in get_conn().execute(
        "SELECT event_type FROM audit_events WHERE patient_id = ? AND created_at LIKE '2000-%' ORDER BY id", (pid,))]
    assert kept_old == ["patient_event_deleted", "patient_event_restored"]
    assert get_conn().execute("SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND created_at NOT LIKE '2000-%'",
                              (pid,)).fetchone()[0] == recent


def test_database_busy_is_not_reported_as_audit_failure(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    event = _events(auth_client, pid)[0]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    from backend.app import config
    locker = sqlite3.connect(path)
    locker.execute("BEGIN IMMEDIATE")
    original = config.BUSY_TIMEOUT_MS
    config.BUSY_TIMEOUT_MS = 50
    try:
        r = _delete(auth_client, pid, event)
    finally:
        config.BUSY_TIMEOUT_MS = original
        locker.rollback()
        locker.close()
    assert r.status_code == 503 and "занята" in r.json()["detail"]
    assert _row(event["id"])["deleted_at"] is None


def test_insert_or_replace_cannot_overwrite_a_deleted_event(auth_client):
    pid, archived = _archived_patient(auth_client)
    row = _row(archived)
    with pytest.raises(sqlite3.IntegrityError, match="archived_event_delete_forbidden"):
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO events (id, history_entry_id, seq) VALUES (?, ?, ?)",
                         ("replacement", row["history_entry_id"], row["seq"]))
    with pytest.raises(sqlite3.IntegrityError, match="archived_event_delete_forbidden"):
        with get_conn() as conn:
            conn.execute("REPLACE INTO events (id, history_entry_id, seq) VALUES (?, ?, ?)",
                         (archived, row["history_entry_id"], 999))
    _assert_archive_intact(archived, _dev_id())


def test_add_import_check_follows_actor_scope(client):
    dept_a = create_department("Импорт Область А")
    dept_b = create_department("Импорт Область Б")
    create_account("scope.doc.a", "doctor", department_ids=[dept_a])
    create_account("scope.doc.b", "doctor", department_ids=[dept_b])
    login(client, "scope.doc.b")
    foreign, archived = _archived_patient(client, name="Чужой Архив")
    document = legacy_export(_export(client))
    for record in document["patients"]:
        record.pop("treatingDoctorUserId", None)     # врач импортирует с закреплением за собой
    login(client, "scope.doc.a")
    # Решение B: файл с id врач не импортирует — ответ не зависит от того, чья
    # это карточка и есть ли в ней удалённые события.
    report = _preview(client, document)
    assert report["valid"] is False and report["code"] == "internal_ids_forbidden"
    assert "existingMatches" not in report and "archivedEventsImpact" not in report
    r = _import(client, document, "add")
    assert r.status_code == 403 and "внутренние идентификаторы" in r.json()["detail"]
    deleted_by = get_conn().execute("SELECT id FROM users WHERE username = 'scope.doc.b'").fetchone()["id"]
    _assert_archive_intact(archived, deleted_by)
    # Без id файл — новые данные: добавляется в область врача A, архив не тронут.
    for record in document["patients"]:
        record.pop("id", None)
    assert _import(client, document, "add").status_code == 200
    _assert_archive_intact(archived, deleted_by)


def test_deleted_event_rows_cannot_be_edited_while_deleted(auth_client):
    """Страховка базы: правка удалённого события (например, старой сборкой,
    которая не знает deleted_at) отклоняется; восстановление разрешено."""
    pid, archived = _archived_patient(auth_client)
    with pytest.raises(sqlite3.IntegrityError, match="archived_event_update_forbidden"):
        with get_conn() as conn:
            conn.execute("UPDATE events SET actual_date = '2026-01-10' WHERE id = ?", (archived,))
    _assert_archive_intact(archived, _dev_id())
    assert _row(archived)["actual_date"] is None
    assert _restore(auth_client, pid, archived, _deleted(auth_client, pid)[0]["version"]).status_code == 200


def test_backup_with_damaged_schema_version_is_rejected(auth_client, tmp_path, monkeypatch):
    from backend.app import config, maintenance

    monkeypatch.setenv("CLINIC_BACKUP_KEY", "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE")
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    with get_conn() as conn:
        conn.execute("INSERT INTO schema_version (version) VALUES (20)")
    report = maintenance.check_database(config.DB_PATH)
    assert report["ok"] is False and any("damaged" in p for p in report["problems"])
    # Копию такой базы не создать, а расшифрованный снимок не пройдёт проверку restore.
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.backup_database(config.DB_PATH, tmp_path / "damaged.cbk")
    snapshot = tmp_path / "snapshot.db"
    source = sqlite3.connect(config.DB_PATH)
    target = sqlite3.connect(snapshot)
    source.backup(target)
    source.close()
    target.close()
    with pytest.raises(maintenance.MaintenanceError, match="повреждена"):
        maintenance._check_or_migrate_plaintext(snapshot, {"schema_version": 20})


# === F7. Создание события из устаревшей карточки =============================

def _add(client, pid, expected, purpose="add", occ=0):
    return client.post(f"/api/patients/{pid}/history/{occ}/events",
                       json={"expectedEventIds": expected, "purpose": purpose}, headers=client.csrf)


def _active_ids(pid, occ=0):
    return [r[0] for r in get_conn().execute(
        """SELECT events.id FROM events JOIN history_entries h ON h.id = events.history_entry_id
           WHERE h.patient_id = ? AND h.seq = ? AND events.deleted_at IS NULL ORDER BY events.seq""",
        (pid, occ)).fetchall()]


def test_placeholder_creation_returns_the_created_event(auth_client):
    pid = _create(auth_client, events=[])["id"]
    r = _add(auth_client, pid, [], "placeholder")
    assert r.status_code == 200, r.text
    assert _active_ids(pid) == [r.json()["createdEventId"]]
    detail = _audit(pid)[-1]["detail"]
    assert "action=event_placeholder" in detail and f"event_id={r.json()['createdEventId']}" in detail


def test_two_users_who_saw_an_empty_stage_create_one_event(auth_client):
    pid = _create(auth_client, events=[])["id"]
    assert _add(auth_client, pid, [], "placeholder").status_code == 200
    before = _snapshot()
    second = _add(auth_client, pid, [], "placeholder")                 # вторая открытая карточка
    assert second.status_code == 409 and "Обновите карточку" in second.json()["detail"]
    assert _snapshot() == before and len(_active_ids(pid)) == 1


def test_intentional_second_event_and_stale_add(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    first = _active_ids(pid)
    r = _add(auth_client, pid, first)
    assert r.status_code == 200 and _active_ids(pid) == first + [r.json()["createdEventId"]]
    before = _snapshot()
    assert _add(auth_client, pid, first).status_code == 409            # видел только первое
    assert _add(auth_client, pid, first, "placeholder").status_code == 422
    assert _snapshot() == before


def test_add_after_delete_and_restore_uses_the_event_set_not_the_count(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10"}])["id"]
    seen = _active_ids(pid)
    event = _events(auth_client, pid)[0]
    assert _delete(auth_client, pid, event).status_code == 200
    assert _add(auth_client, pid, seen).status_code == 409             # карточка видела удалённое
    created = _add(auth_client, pid, []).json()["createdEventId"]      # свежая карточка: этап пуст
    # Число активных событий снова 1, но это другое событие: устаревшая карточка не проходит.
    assert len(_active_ids(pid)) == 1
    assert _add(auth_client, pid, seen).status_code == 409
    assert _restore(auth_client, pid, event["id"], _deleted(auth_client, pid)[0]["version"]).status_code == 200
    assert _add(auth_client, pid, [created]).status_code == 409        # после восстановления набор другой
    assert _add(auth_client, pid, _active_ids(pid)).status_code == 200
    assert _row(event["id"])["deleted_at"] is None


def test_add_without_context_is_rejected_and_creates_nothing(auth_client):
    pid = _create(auth_client, events=[])["id"]
    before = _snapshot()
    r = auth_client.post(f"/api/patients/{pid}/history/0/events", headers=auth_client.csrf)
    assert r.status_code == 409 and "Обновите страницу" in r.json()["detail"]
    assert _snapshot() == before


def test_concurrent_placeholder_creation_inserts_once(auth_client):
    import threading
    from backend.app import store

    pid = _create(auth_client, events=[])["id"]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    results, barrier = [], threading.Barrier(3)

    def create():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results.append(bool(store.add_event(conn, pid, 0, [], placeholder=True)))
        except store.EventConflict:
            results.append("conflict")
        finally:
            conn.close()

    threads = [threading.Thread(target=create) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results)) == ["True", "conflict", "conflict"]
    assert len(_active_ids(pid)) == 1


def test_add_event_rights_are_unchanged(client):
    dept_a = create_department("Добавление А")
    dept_b = create_department("Добавление Б")
    create_account("add.doc.a", "doctor", department_ids=[dept_a])
    create_account("add.doc.b", "doctor", department_ids=[dept_b])
    create_account("add.deputy", "deputy_chief_oncology")
    login(client, "add.doc.a")
    pid = _create(client, events=[])["id"]
    for username, code in (("add.doc.b", 404), ("add.deputy", 403), ("add.doc.a", 200)):
        login(client, username)
        assert _add(client, pid, [], "placeholder").status_code == code, username


# === F8. Повторный этап с тем же названием (ABA) ===============================

def _enter(client, pid, stage):
    r = _stage(client, pid, {"newStage": stage})
    assert r.status_code == 200, r.text
    return r.json()


def test_returning_to_a_stage_with_the_same_name_does_not_validate_an_old_card(auth_client):
    pid = _create(auth_client, stage="exam", events=[{"date": "2026-01-10", "actualDate": "2026-01-10"}])["id"]
    old_card = _card(auth_client, pid)
    _enter(auth_client, pid, "appointment")
    _enter(auth_client, pid, "exam")                                   # снова «exam», но другая запись
    before = _route_state(pid)
    stale = auth_client.post(f"/api/patients/{pid}/stage", json={
        "newStage": "consult", "expectedStage": "exam",
        "expectedHistoryEntryId": old_card["history"][-1]["id"],
    }, headers=auth_client.csrf)
    assert stale.status_code == 409 and "Этап пациента уже изменён" in stale.json()["detail"]
    assert _route_state(pid) == before
    # Повторная загрузка — переход проходит; повторные прохождения этапа допустимы.
    assert _enter(auth_client, pid, "consult")["stage"] == "consult"
    assert [h["stage"] for h in _card(auth_client, pid)["history"]] == ["exam", "appointment", "exam", "consult"]


def test_stage_change_requires_the_history_entry(auth_client):
    pid = _create(auth_client, events=[{"date": "2026-01-10", "actualDate": "2026-01-10"}])["id"]
    before = _route_state(pid)
    r = auth_client.post(f"/api/patients/{pid}/stage", json={"newStage": "appointment", "expectedStage": "exam"},
                         headers=auth_client.csrf)
    assert r.status_code == 409 and "Обновите страницу" in r.json()["detail"]
    assert _route_state(pid) == before


def test_stale_history_entry_with_event_update_changes_nothing(auth_client):
    pid = _create(auth_client, stage="exam", events=[{"date": "2026-01-10"}])["id"]
    old_card = _card(auth_client, pid)
    old_entry = old_card["history"][-1]["id"]
    event = old_card["history"][0]["events"][0]
    # Другой пользователь: exam → appointment → exam (факт первого события записан).
    r = _stage(auth_client, pid, {"newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-01-10"},
                                  "lastEventId": event["id"], "lastEventVersion": event["version"]})
    assert r.status_code == 200
    _enter(auth_client, pid, "exam")
    before = _route_state(pid)
    # Старая карточка: тот же этап, то же «последнее событие», но другая запись маршрута.
    for update in ({"actualDate": "2026-01-11"}, None):
        body = {"newStage": "consult", "expectedStage": "exam", "expectedHistoryEntryId": old_entry,
                "lastEventUpdate": update, "lastEventId": event["id"], "lastEventVersion": event["version"] + 1}
        assert auth_client.post(f"/api/patients/{pid}/stage", json=body, headers=auth_client.csrf).status_code == 409
        assert _route_state(pid) == before
    # Новая запись exam пуста: свежая карточка записывает факт в новое событие.
    fresh = _stage(auth_client, pid, {"newStage": "consult", "lastEventUpdate": {"actualDate": "2026-02-01"}})
    assert fresh.status_code == 200
    assert _row(event["id"])["actual_date"] == "2026-01-10"
