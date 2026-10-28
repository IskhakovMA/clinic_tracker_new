"""WP-C: сохранность медицинской истории (AUD-03, 06, 08, 09, 10, 11, 13, 14).

Только синтетические данные. Конкурентные сценарии используют независимые
SQLite-соединения и явную точку остановки (trace callback останавливает поток
перед нужным оператором), а не случайный тайминг.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from .conftest import (
    DEV_USERNAME,
    PROJECT_ROOT,
    TEST_MOSCOW_NOW,
    card_version,
    checklist_state,
    create_account,
    decision_doctor_id,
    get_conn,
    login,
    make_patient_payload,
    mark_decision,
    stage_body,
)

TEST_BACKUP_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 32 zero bytes (synthetic test key)
FIXTURES = PROJECT_ROOT / "backend" / "tests" / "fixtures"
TODAY = TEST_MOSCOW_NOW.date().isoformat()


@pytest.fixture(autouse=True)
def backup_key(monkeypatch):
    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)


# ---------------------------------------------------------------- helpers

def _create(client, **kw):
    r = client.post("/api/patients", json=make_patient_payload(**kw), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _patch(client, pid, field, value, version=None):
    return client.patch(f"/api/patients/{pid}", json={
        "field": field, "value": value, "expectedVersion": card_version(pid) if version is None else version,
    }, headers=client.csrf)


def _set_fact(client, pid, value, field="actualDate", idx=0):
    ev = _card(client, pid)["history"][0]["events"][idx]
    return client.patch(f"/api/patients/{pid}/events/{ev['id']}",
                        json={"field": field, "value": value, "version": ev["version"]}, headers=client.csrf)


def _count(sql, *args):
    return get_conn().execute(sql, args).fetchone()[0]


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


def _audit_max():
    return _count("SELECT COALESCE(MAX(id), 0) FROM audit_events")


def _break_audit(kind="raise"):
    """Отказ записи в журнал аудита: недоступное хранилище или нарушение ограничения."""
    message = {"raise": "test: audit storage unavailable",
               "constraint": "UNIQUE constraint failed: audit_events.id"}[kind]
    conn = get_conn()
    conn.execute("CREATE TRIGGER zz_test_fail_audit BEFORE INSERT ON audit_events "
                 f"BEGIN SELECT RAISE(ABORT, '{message}'); END")
    conn.commit()


def _restore_audit():
    conn = get_conn()
    conn.execute("DROP TRIGGER IF EXISTS zz_test_fail_audit")
    conn.commit()


def _paused(path, statement_prefix, fn):
    """Run fn(conn) on its own connection, stopping right before `statement_prefix` runs.

    Returns (thread, result, paused, resume): `paused` is set when the thread
    reached the statement, `resume` lets it continue."""
    paused, resume = threading.Event(), threading.Event()
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    def trace(sql):
        if sql.lstrip().upper().startswith(statement_prefix) and not paused.is_set():
            paused.set()
            assert resume.wait(20), "test did not resume the paused writer"
    conn.set_trace_callback(trace)
    result = {}

    def run():
        try:
            result["value"] = fn(conn)
        except Exception as exc:           # noqa: BLE001 - the test inspects the outcome
            result["error"] = exc
        finally:
            conn.close()
    thread = threading.Thread(target=run)
    thread.start()
    assert paused.wait(20), "writer never reached the statement"
    return thread, result, resume


def _short_conn(path, timeout=0.2):
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ================================================================ AUD-03
# Удаление карточки: только при отсутствии клинической истории и зависимостей.

def _dep_stage(c, pid):
    assert c.post(f"/api/patients/{pid}/stage", json=stage_body(c, pid, {"newStage": "appointment"}),
                  headers=c.csrf).status_code == 200


def _dep_note(c, pid):
    assert c.patch(f"/api/patients/{pid}/history/0/note", json={"note": "Заметка врача (синтетика)"},
                   headers=c.csrf).status_code == 200


def _dep_checklist(c, pid):
    assert c.patch(f"/api/patients/{pid}/history/0/checklist",
                   json={"item": "Анализ крови", "checked": True, "expected": False}, headers=c.csrf).status_code == 200


def _dep_fact(c, pid):
    assert _set_fact(c, pid, "2026-08-20").status_code == 200


def _dep_fact_history_only(c, pid):
    # Факт внесён и очищен: самого факта нет, но история его значений осталась.
    assert _set_fact(c, pid, "2026-08-20").status_code == 200
    assert _set_fact(c, pid, "").status_code == 200


def _dep_decision(c, pid):
    assert mark_decision(c, pid, "treatment_strategy_defined").status_code == 200


def _dep_archived(c, pid):
    ev = _card(c, pid)["history"][0]["events"][0]
    assert c.delete(f"/api/patients/{pid}/events/{ev['id']}?version={ev['version']}", headers=c.csrf).status_code == 200


def _dep_diagnosis(c, pid):
    assert _patch(c, pid, "diagnosis", "C34.9").status_code == 200


def _dep_attention(c, pid):
    assert c.post(f"/api/patients/{pid}/attention", json={"reasonCode": "emergency_hospitalization",
                  "eventDate": "2026-08-21"}, headers=c.csrf).status_code == 201


def _dep_consent(c, pid):
    assert c.put(f"/api/notifications/patients/{pid}/preferences", json={"smsEnabled": True,
                 "consentStatus": "granted"}, headers=c.csrf).status_code == 200


def _dep_notification(c, pid):
    conn = get_conn()
    conn.execute("""INSERT INTO notifications (id, patient_id, channel, template_code, template_version, source_type,
                    source_id, scheduled_at, idempotency_key, status, created_at, updated_at)
                    VALUES ('n-syn', ?, 'sms', 'event_reminder', 1, 'manual', 'syn', '2026-09-01T09:00:00+00:00',
                    'idem-syn', 'cancelled', '2026-09-01T08:00:00+00:00', '2026-09-01T08:00:00+00:00')""", (pid,))
    conn.commit()


def _dep_mobile(c, pid):
    conn = get_conn()
    conn.execute("""INSERT INTO patient_portal_access (patient_id, email_normalized, status, linked_at, revoked_at,
                    enrollment_state, updated_at)
                    VALUES (?, 'synthetic.revoked@example.test', 'revoked', '2026-09-01T09:00:00+00:00',
                    '2026-09-02T09:00:00+00:00', 'none', '2026-09-02T09:00:00+00:00')""", (pid,))
    conn.commit()


def _dep_consilium(c, pid):
    conn = get_conn()
    uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    conn.execute("""INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
                    scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
                    needs_surgeon, needs_radiotherapist, created_at, updated_at)
                    VALUES ('ap-syn', ?, ?, '2026-10-20', '10:00', 'thoracic', 0, 'Врач Синтетический', 'C34.1',
                    0, 0, '2026-09-01T09:00:00+00:00', '2026-09-01T09:00:00+00:00')""", (pid, uid))
    conn.commit()


def _dep_duplicate(c, pid):
    conn = get_conn()
    uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    conn.execute("""INSERT INTO patient_duplicate_confirmations (id, operation, patient_id, matched_patient_ids, reason,
                    created_by_user_id, created_at)
                    VALUES ('dc-syn', 'create', ?, '["other-syn"]', 'Синтетическая причина', ?,
                    '2026-09-01T09:00:00+00:00')""", (pid, uid))
    conn.commit()


DEPENDENCIES = {
    "stage_transitions": _dep_stage,
    "history_notes": _dep_note,
    "checklist_marks": _dep_checklist,
    "performed_events": _dep_fact,
    "fact_history": _dep_fact_history_only,
    "consilium_decision_marks": _dep_decision,
    "archived_events": _dep_archived,
    "diagnosis_history": _dep_diagnosis,
    "attention_issues": _dep_attention,
    "notification_consent": _dep_consent,
    "notification_history": _dep_notification,
    "mobile_access": _dep_mobile,
    "consilium_records": _dep_consilium,
    "duplicate_registry": _dep_duplicate,
}


def _protected_rows(pid):
    tables = {
        "events": "SELECT COUNT(*) FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = ?",
        "history": "SELECT COUNT(*) FROM history_entries WHERE patient_id = ?",
        "diagnosis": "SELECT COUNT(*) FROM patient_diagnosis_history WHERE patient_id = ?",
        "facts": "SELECT COUNT(*) FROM patient_event_fact_history WHERE patient_id = ?",
        "attention": "SELECT COUNT(*) FROM patient_attention_issues WHERE patient_id = ?",
        "consent": "SELECT COUNT(*) FROM patient_notification_preferences WHERE patient_id = ?",
        "notifications": "SELECT COUNT(*) FROM notifications WHERE patient_id = ?",
        "mobile": "SELECT COUNT(*) FROM patient_portal_access WHERE patient_id = ?",
        "consilium": "SELECT COUNT(*) FROM oncology_consilium_appointments WHERE patient_id = ?",
    }
    return {k: _count(sql, pid) for k, sql in tables.items()}


def test_empty_card_is_deleted_with_required_audit(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-11-20", "time": "10:00"}])["id"]   # план, не факт
    r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND event_type = 'patient_deleted' "
                  "AND result = 'ok'", pid) == 1


@pytest.mark.parametrize("dependency", sorted(DEPENDENCIES))
def test_card_with_each_dependency_is_not_deleted(auth_client, dependency):
    c = auth_client
    stage = "consult" if dependency == "consilium_decision_marks" else "exam"
    kw = {"treatingDoctorUserId": decision_doctor_id()} if dependency == "consilium_decision_marks" else {}
    pid = _create(c, stage=stage, events=[{"date": "2026-08-20", "time": "10:00"}], **kw)["id"]
    DEPENDENCIES[dependency](c, pid)
    if dependency == "consilium_decision_marks":
        login(c, DEV_USERNAME)
        c.csrf = {"X-CSRF-Token": c.cookies.get("clinic_csrf")}
    before = _protected_rows(pid)
    r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "PATIENT_HAS_CLINICAL_HISTORY"
    assert dependency in body["dependencies"]
    assert "Синтетик" not in body["detail"] and "C34" not in body["detail"]   # без ПДн и диагнозов
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 1
    assert _protected_rows(pid) == before


def test_card_with_several_dependencies_lists_all_of_them(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    for dep in ("performed_events", "diagnosis_history", "attention_issues", "notification_consent"):
        DEPENDENCIES[dep](c, pid)
    r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
    assert r.status_code == 409
    assert {"performed_events", "diagnosis_history", "attention_issues", "notification_consent",
            "fact_history"} <= set(r.json()["dependencies"])


def test_database_guard_blocks_direct_delete_of_card_with_history(auth_client):
    """Обход проверки маршрута: прямой DELETE (другой путь кода, ручной SQL) тоже не удаляет историю."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    _dep_fact(c, pid)
    raw = _short_conn(_db_path())
    try:
        with pytest.raises(sqlite3.IntegrityError, match="patient_has_clinical_history"):
            raw.execute("DELETE FROM patients WHERE id = ?", (pid,))
        raw.rollback()
    finally:
        raw.close()
    from backend.app import store
    with pytest.raises(store.PatientDeleteBlocked):
        store.delete_patient(get_conn(), pid)
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 1


def test_history_added_concurrently_is_not_destroyed_by_delete(auth_client):
    """Удаление держит блокировку записи от проверки зависимостей до DELETE: история,
    которую пытаются добавить в этот момент, не может проскочить в окно и быть стёрта."""
    from backend.app import store
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-11-20"}])["id"]
    path = _db_path()
    thread, result, resume = _paused(path, "DELETE FROM PATIENTS", lambda conn: store.delete_patient(conn, pid))
    # Удаление проверило зависимости (их нет) и стоит перед DELETE с блокировкой записи.
    other = _short_conn(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("INSERT INTO patient_diagnosis_history (id, patient_id, changed_at, old_diagnosis, new_diagnosis) "
                          "VALUES ('dh-race', ?, '2026-10-01T09:00:00+00:00', 'C34.1', 'C34.2')", (pid,))
    finally:
        other.close()
    resume.set()
    thread.join(20)
    assert result.get("value") is True, result
    assert _count("SELECT COUNT(*) FROM patient_diagnosis_history WHERE id = 'dh-race'") == 0   # ничего не стёрто молча


def test_history_committed_first_blocks_the_delete(auth_client):
    from backend.app import store
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-11-20"}])["id"]
    path = _db_path()
    writer = _short_conn(path, timeout=5)
    writer.execute("BEGIN IMMEDIATE")
    uid = get_conn().execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    writer.execute("INSERT INTO patient_attention_issues (id, patient_id, reason_code, status, comment, created_at, "
                   "created_by_user_id) VALUES ('ai-race', ?, 'emergency_hospitalization', 'open', '', "
                   "'2026-10-01T09:00:00+00:00', ?)", (pid, uid))
    deleter = _short_conn(path, timeout=0.2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.delete_patient(deleter, pid)        # ждёт блокировку и сдаётся, ничего не удалив
    finally:
        deleter.close()
    writer.commit()
    writer.close()
    with pytest.raises(store.PatientDeleteBlocked) as exc:
        store.delete_patient(get_conn(), pid)
    assert "attention_issues" in exc.value.dependencies


def test_delete_permissions_are_unchanged(client):
    """Модель прав прежняя: врач удаляет свою пустую карточку, read-only роль — нет."""
    from backend.app import users
    dept = users.create_department(get_conn(), "Отделение удаления (синтетика)")
    create_account("del.doctor", "doctor", department_ids=[dept])
    create_account("del.reader", "deputy_chief_oncology")   # глобальная роль только для чтения
    assert login(client, "del.doctor").status_code == 200
    doc_id = get_conn().execute("SELECT id FROM users WHERE username = 'del.doctor'").fetchone()[0]
    pid = _create(client, stage="exam", events=[{"date": "2026-11-20"}], treatingDoctorUserId=doc_id)["id"]
    keep = _create(client, stage="exam", events=[{"date": "2026-11-20"}], treatingDoctorUserId=doc_id)["id"]
    assert login(client, "del.reader").status_code == 200
    assert client.delete(f"/api/patients/{keep}", headers=client.csrf).status_code == 403
    assert login(client, "del.doctor").status_code == 200
    assert client.delete(f"/api/patients/{pid}", headers=client.csrf).status_code == 200
    _dep_diagnosis(client, keep)
    assert client.delete(f"/api/patients/{keep}", headers=client.csrf).status_code == 409


# ================================================================ AUD-06
# Обязательный (fail-closed) аудит критичных изменений.

def _snapshot(pid):
    conn = get_conn()
    p = dict(conn.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone())
    return {"patient": p, **_protected_rows(pid)}


@pytest.mark.parametrize("kind", ["raise", "constraint"])
@pytest.mark.parametrize("operation", ["diagnosis", "pgiResult", "complaint", "name", "policyNumber",
                                       "treating_doctor", "delete", "consent", "attention_create",
                                       "attention_close", "checklist"])
def test_patient_change_is_not_saved_without_audit(auth_client, operation, kind):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-11-20"}])["id"]
    if operation == "attention_close":
        issue = c.post(f"/api/patients/{pid}/attention", json={"reasonCode": "emergency_hospitalization",
                       "eventDate": "2026-08-21"}, headers=c.csrf).json()["issue"]
    doctor = create_account("audit.doctor", "doctor")
    before = _snapshot(pid)
    audit_before = _audit_max()
    _break_audit(kind)
    try:
        if operation in ("diagnosis", "complaint", "name"):
            r = _patch(c, pid, operation, {"diagnosis": "C34.9", "complaint": "новая", "name": "Новый Синтетик"}[operation])
        elif operation == "pgiResult":
            r = _patch(c, pid, "pgiResult", {"result": "confirmed", "date": "2026-08-01"})
        elif operation == "policyNumber":
            r = _patch(c, pid, "policyNumber", "7700000099999999")
        elif operation == "treating_doctor":
            r = c.put(f"/api/patients/{pid}/treating-doctor",
                      json={"userId": doctor, "expectedVersion": card_version(pid)}, headers=c.csrf)
        elif operation == "delete":
            r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
        elif operation == "consent":
            r = c.put(f"/api/notifications/patients/{pid}/preferences",
                      json={"smsEnabled": True, "consentStatus": "granted"}, headers=c.csrf)
        elif operation == "attention_create":
            r = c.post(f"/api/patients/{pid}/attention", json={"reasonCode": "emergency_hospitalization",
                       "eventDate": "2026-08-21"}, headers=c.csrf)
        elif operation == "attention_close":
            r = c.post(f"/api/patients/{pid}/attention/{issue['id']}/close",
                       json={"resolutionComment": "закрыто"}, headers=c.csrf)
        else:
            r = c.patch(f"/api/patients/{pid}/history/0/checklist",
                        json={"item": "Анализ крови", "checked": True, "expected": False}, headers=c.csrf)
    finally:
        _restore_audit()
    assert r.status_code == 503, r.text
    after = _snapshot(pid)
    assert after == before
    assert _audit_max() == audit_before
    if operation == "attention_close":
        assert _count("SELECT COUNT(*) FROM patient_attention_issues WHERE id = ? AND status = 'open'", issue["id"]) == 1


def test_staff_and_structure_changes_are_not_saved_without_audit(auth_client):
    c = auth_client
    uid = create_account("audit.staff", "doctor")
    hash_before = get_conn().execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()[0]
    sessions_before = _count("SELECT COUNT(*) FROM sessions")
    audit_before = _audit_max()
    _break_audit()
    try:
        results = {
            "role": c.patch(f"/api/users/{uid}", json={"roleId": "department_head"}, headers=c.csrf).status_code,
            "status": c.patch(f"/api/users/{uid}", json={"status": "blocked"}, headers=c.csrf).status_code,
            "reset": c.post(f"/api/users/{uid}/reset-password", json={"newPassword": "Synthetic-Reset-Pass-42"},
                            headers=c.csrf).status_code,
            "create": c.post("/api/users", json={"username": "audit.new", "fullName": "Новиков Синтетик",
                             "roleId": "doctor", "password": "Synthetic-New-Pass-42"}, headers=c.csrf).status_code,
            "department": c.post("/api/departments", json={"name": "Отделение без аудита"}, headers=c.csrf).status_code,
            "own_password": c.post("/api/auth/change-password", json={"currentPassword": "correct-horse-battery",
                                   "newPassword": "Synthetic-Changed-42"}, headers=c.csrf).status_code,
        }
    finally:
        _restore_audit()
    assert results == {k: 503 for k in results}, results
    row = get_conn().execute("SELECT role_id, status, password_hash FROM users WHERE id = ?", (uid,)).fetchone()
    assert (row[0], row[1], row[2]) == ("doctor", "active", hash_before)
    assert _count("SELECT COUNT(*) FROM users WHERE username = 'audit.new'") == 0
    assert _count("SELECT COUNT(*) FROM departments WHERE name = 'Отделение без аудита'") == 0
    assert _count("SELECT COUNT(*) FROM sessions") == sessions_before
    assert _audit_max() == audit_before
    # Журнал снова доступен — те же операции проходят и записываются.
    assert c.patch(f"/api/users/{uid}", json={"roleId": "department_head"}, headers=c.csrf).status_code == 200
    assert _count("SELECT COUNT(*) FROM audit_events WHERE event_type = 'role_changed' AND target_user_id = ?", uid) == 1


def test_export_is_not_released_without_audit(auth_client):
    c = auth_client
    _create(c)
    audit_before = _audit_max()
    _break_audit()
    try:
        r = c.get("/api/export", headers=c.csrf)
    finally:
        _restore_audit()
    assert r.status_code == 503
    assert "patients" not in r.text and "Тестов" not in r.text
    assert r.json()["code"] == "AUDIT_UNAVAILABLE"
    assert _audit_max() == audit_before
    ok = c.get("/api/export", headers=c.csrf)
    assert ok.status_code == 200
    assert _count("SELECT COUNT(*) FROM audit_events WHERE event_type = 'export' AND result = 'ok'") == 1


def test_locked_database_rolls_back_without_partial_change(auth_client, monkeypatch):
    """SQLite занята другим писателем: правка ждёт и отказывает (503), ничего не сохранив."""
    from backend.app import config
    c = auth_client
    pid = _create(c)["id"]
    monkeypatch.setattr(config, "BUSY_TIMEOUT_MS", 100)
    before = _snapshot(pid)
    version = card_version(pid)
    holder = _short_conn(_db_path(), timeout=5)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE departments SET updated_at = updated_at")
    try:
        r = _patch(c, pid, "diagnosis", "C34.9", version=version)
    finally:
        holder.rollback()
        holder.close()
    assert r.status_code == 503
    assert _snapshot(pid) == before


def test_transaction_error_after_change_rolls_everything_back(auth_client):
    """Ошибка после записи поля и истории (внутри той же транзакции) откатывает всё."""
    from backend.app import store
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    before = _snapshot(pid)

    def failing_hook():
        raise RuntimeError("synthetic failure after the change")

    with pytest.raises(RuntimeError):
        store.update_patient_field_with_actor(get_conn(), pid, "diagnosis", "C34.5", expected_version=card_version(pid),
                                              audit_hook=failing_hook)
    assert _snapshot(pid) == before


def test_audit_detail_carries_no_patient_values(auth_client):
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    assert _patch(c, pid, "diagnosis", "C50.4").status_code == 200
    assert _patch(c, pid, "complaint", "секретная жалоба").status_code == 200
    details = " ".join(r[0] or "" for r in get_conn().execute("SELECT detail FROM audit_events WHERE patient_id = ?", (pid,)))
    assert "C50.4" not in details and "секретная" not in details


# ================================================================ AUD-08
# История исправления фактов событий.

def _facts(pid):
    return [dict(r) for r in get_conn().execute(
        "SELECT field, old_value, new_value, change_type, source FROM patient_event_fact_history "
        "WHERE patient_id = ? ORDER BY event_id, seq", (pid,))]


def test_fact_history_initial_correction_cleared_reentry(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])["id"]
    for value in ("2026-08-20", "2026-08-22", "", "2026-08-23"):
        assert _set_fact(c, pid, value).status_code == 200
    assert _set_fact(c, pid, "09:40", field="actualTime").status_code == 200
    assert _facts(pid) == [
        {"field": "actual_date", "old_value": None, "new_value": "2026-08-20", "change_type": "initial", "source": "event_update"},
        {"field": "actual_date", "old_value": "2026-08-20", "new_value": "2026-08-22", "change_type": "correction", "source": "event_update"},
        {"field": "actual_date", "old_value": "2026-08-22", "new_value": None, "change_type": "cleared", "source": "event_update"},
        {"field": "actual_date", "old_value": None, "new_value": "2026-08-23", "change_type": "reentry", "source": "event_update"},
        {"field": "actual_time", "old_value": None, "new_value": "09:40", "change_type": "initial", "source": "event_update"},
    ]
    actors = {r[0] for r in get_conn().execute("SELECT actor_user_id FROM patient_event_fact_history WHERE patient_id = ?", (pid,))}
    assert len(actors) == 1 and None not in actors


def test_plan_changes_are_not_fact_history(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])["id"]
    assert _set_fact(c, pid, "2030-01-05", field="date").status_code == 200          # план в будущем допустим
    assert _set_fact(c, pid, "11:15", field="time").status_code == 200
    assert _facts(pid) == []


def test_future_fact_is_rejected_and_nothing_changes(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])["id"]
    assert _set_fact(c, pid, "2026-08-20").status_code == 200
    before = _card(c, pid)["history"][0]["events"][0]
    r = _set_fact(c, pid, "2031-01-01")
    assert r.status_code == 422 and r.json()["code"] == "FACT_DATE_IN_FUTURE"
    after = _card(c, pid)["history"][0]["events"][0]
    assert (after["actualDate"], after["version"]) == (before["actualDate"], before["version"])
    assert _set_fact(c, pid, TODAY).status_code == 200            # «сегодня» учреждения — допустимо
    future_stage = c.post(f"/api/patients/{pid}/stage", json=stage_body(c, pid, {
        "newStage": "appointment", "lastEventUpdate": {"actualDate": "2031-01-01"}}), headers=c.csrf)
    assert future_stage.status_code == 422 and future_stage.json()["code"] == "FACT_DATE_IN_FUTURE"
    created = c.post("/api/patients", json=make_patient_payload(events=[{"date": "2026-08-20", "actualDate": "2031-01-01"}]),
                     headers=c.csrf)
    assert created.status_code == 422


def test_stage_change_fact_is_recorded_as_initial(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])["id"]
    r = c.post(f"/api/patients/{pid}/stage", json=stage_body(c, pid, {
        "newStage": "appointment", "lastEventUpdate": {"actualDate": "2026-08-20", "actualTime": "10:05"}}),
        headers=c.csrf)
    assert r.status_code == 200, r.text
    assert _facts(pid) == [
        {"field": "actual_date", "old_value": None, "new_value": "2026-08-20", "change_type": "initial", "source": "stage_change"},
        {"field": "actual_time", "old_value": None, "new_value": "10:05", "change_type": "initial", "source": "stage_change"},
    ]


def test_legacy_fact_keeps_its_value_when_corrected(auth_client):
    """Факт, внесённый до v28 (истории ещё нет): при очистке прежнее значение сохраняется."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    conn = get_conn()
    conn.execute("UPDATE events SET actual_date = '2026-08-19' WHERE history_entry_id IN "
                 "(SELECT id FROM history_entries WHERE patient_id = ?)", (pid,))
    conn.commit()
    assert _set_fact(c, pid, "").status_code == 200
    assert _facts(pid) == [{"field": "actual_date", "old_value": "2026-08-19", "new_value": None,
                            "change_type": "cleared", "source": "event_update"}]


def test_concurrent_fact_edits_with_same_version_record_one_change(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    ev = _card(c, pid)["history"][0]["events"][0]
    first = c.patch(f"/api/patients/{pid}/events/{ev['id']}", json={"field": "actualDate", "value": "2026-08-20",
                    "version": ev["version"]}, headers=c.csrf)
    second = c.patch(f"/api/patients/{pid}/events/{ev['id']}", json={"field": "actualDate", "value": "2026-08-21",
                     "version": ev["version"]}, headers=c.csrf)
    assert (first.status_code, second.status_code) == (200, 409)
    assert [f["new_value"] for f in _facts(pid)] == ["2026-08-20"]


def test_fact_history_survives_audit_failure_only_with_the_change(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    _break_audit()
    try:
        assert _set_fact(c, pid, "2026-08-20").status_code == 503
    finally:
        _restore_audit()
    assert _facts(pid) == []
    assert _card(c, pid)["history"][0]["events"][0]["actualDate"] in (None, "")


# ================================================================ AUD-09
# Цепочка истории диагноза при конкурентных изменениях.

def _chain(pid):
    return [tuple(r) for r in get_conn().execute(
        "SELECT old_diagnosis, new_diagnosis FROM patient_diagnosis_history WHERE patient_id = ? "
        "ORDER BY changed_at, rowid", (pid,))]


def _chain_ok(chain, first):
    expected = first
    for old, new in chain:
        if old != expected:
            return False
        expected = new
    return True


def test_diagnosis_reader_holds_the_write_lock_no_lost_intermediate_change(auth_client):
    """A прочитал диагноз и стоит перед записью. B не может вклиниться: он ждёт и
    сдаётся явной ошибкой. Цепочка old→new не разрывается."""
    from backend.app import store
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    path = _db_path()
    thread, result, resume = _paused(path, "UPDATE PATIENTS SET DIAGNOSIS", lambda conn: store.update_patient_field_with_actor(
        conn, pid, "diagnosis", "C34.A", expected_version=card_version(pid)))
    b = _short_conn(path, timeout=0.2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.update_patient_field_with_actor(b, pid, "diagnosis", "C34.B", expected_version=1)
    finally:
        b.close()
    resume.set()
    thread.join(20)
    assert result.get("value") is True, result
    assert _chain(pid) == [("C34.1", "C34.A")]


def test_stale_diagnosis_change_is_rejected_explicitly(auth_client):
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    seen = card_version(pid)
    assert _patch(c, pid, "diagnosis", "C34.2", version=seen).status_code == 200
    stale = _patch(c, pid, "diagnosis", "C34.3", version=seen)
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_PATIENT_VERSION"
    assert _card(c, pid)["diagnosis"] == "C34.2"
    assert _chain(pid) == [("C34.1", "C34.2")]


def test_three_connections_keep_the_diagnosis_chain(auth_client):
    """Три независимых соединения, каждое со своей прочитанной версией: побеждает
    одно, остальные получают конфликт; цепочка без разрывов."""
    from backend.app import store
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    path = _db_path()
    seen = card_version(pid)
    outcomes = []
    for value in ("C34.X", "C34.Y", "C34.Z"):
        conn = _short_conn(path, timeout=5)
        try:
            store.update_patient_field_with_actor(conn, pid, "diagnosis", value, expected_version=seen)
            outcomes.append("ok")
        except store.PatientVersionConflict:
            outcomes.append("conflict")
        finally:
            conn.close()
    assert outcomes == ["ok", "conflict", "conflict"]
    assert _chain(pid) == [("C34.1", "C34.X")]
    # Последовательные правки с актуальной версией сохраняют каждое промежуточное значение.
    for value in ("C34.Y", "C34.Z"):
        assert _patch(c, pid, "diagnosis", value).status_code == 200
    assert _chain_ok(_chain(pid), "C34.1") and len(_chain(pid)) == 3


def test_diagnosis_audit_failure_rolls_back_field_and_history(auth_client):
    c = auth_client
    pid = _create(c, diagnosis="C34.1")["id"]
    _break_audit()
    try:
        assert _patch(c, pid, "diagnosis", "C34.8").status_code == 503
    finally:
        _restore_audit()
    assert _card(c, pid)["diagnosis"] == "C34.1"
    assert _chain(pid) == []


# ================================================================ AUD-10
# Чек-лист: атомарное изменение пунктов.

def _checklist(pid, occ=0):
    row = get_conn().execute("SELECT checklist_json FROM history_entries WHERE patient_id = ? ORDER BY seq "
                             "LIMIT 1 OFFSET ?", (pid, occ)).fetchone()
    return json.loads(row[0]) if row and row[0] else {}


def _check(c, pid, item, checked, expected=None):
    return c.patch(f"/api/patients/{pid}/history/0/checklist", json={
        "item": item, "checked": checked,
        "expected": checklist_state(pid, 0, item) if expected is None else expected}, headers=c.csrf)


def test_concurrent_marks_of_different_items_are_both_kept(auth_client):
    from backend.app import store
    c = auth_client
    pid = _create(c)["id"]
    path = _db_path()
    thread, result, resume = _paused(path, "UPDATE HISTORY_ENTRIES SET CHECKLIST_JSON", lambda conn: store.update_checklist_item(
        conn, pid, 0, "Анализ крови", True, expected=False))
    # B стартует, пока A держит блокировку записи: он ждёт и читает чек-лист уже после A.
    other = {}

    def run_b():
        conn = _short_conn(path, timeout=10)
        try:
            other["value"] = store.update_checklist_item(conn, pid, 0, "КТ грудной клетки", True, expected=False)
        finally:
            conn.close()
    b = threading.Thread(target=run_b)
    b.start()
    resume.set()
    thread.join(20)
    b.join(20)
    assert result.get("value") is True and other.get("value") is True, (result, other)
    assert _checklist(pid) == {"Анализ крови": True, "КТ грудной клетки": True}


def test_same_item_conflict_repeat_and_audit(auth_client):
    c = auth_client
    pid = _create(c)["id"]
    first = _check(c, pid, "Анализ крови", True, expected=False)
    assert first.status_code == 200
    repeat = _check(c, pid, "Анализ крови", True, expected=False)       # повтор того же запроса
    assert repeat.status_code == 200
    stale = _check(c, pid, "Анализ крови", False, expected=False)       # видел «не отмечен», а уже отмечен
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_CHECKLIST_ITEM"
    assert _checklist(pid) == {"Анализ крови": True}
    details = [r[0] for r in get_conn().execute(
        "SELECT detail FROM audit_events WHERE patient_id = ? AND detail LIKE '%action=checklist%' ORDER BY id", (pid,))]
    assert details == ["history=0;action=checklist;outcome=changed;checked=1",
                       "history=0;action=checklist;outcome=unchanged;checked=1"]
    assert _check(c, pid, "Анализ крови", False, expected=True).status_code == 200
    assert _checklist(pid) == {"Анализ крови": False}


def test_checklist_write_failure_changes_nothing(auth_client):
    c = auth_client
    pid = _create(c)["id"]
    _break_audit()
    try:
        assert _check(c, pid, "Анализ крови", True, expected=False).status_code == 503
    finally:
        _restore_audit()
    assert _checklist(pid) == {}


def test_checklist_requires_expected_state(auth_client):
    c = auth_client
    pid = _create(c)["id"]
    r = c.patch(f"/api/patients/{pid}/history/0/checklist", json={"item": "Анализ крови", "checked": True},
                headers=c.csrf)
    assert r.status_code == 422
    assert _checklist(pid) == {}


# ================================================================ AUD-11
# Версия полей карточки.

def test_card_version_increments_and_stale_write_is_rejected(auth_client):
    c = auth_client
    pid = _create(c, complaint="исходная")["id"]
    v1 = _card(c, pid)["version"]
    assert v1 == 1
    r = _patch(c, pid, "complaint", "правка первого", version=v1)
    assert r.status_code == 200 and r.json()["version"] == 2
    stale = _patch(c, pid, "complaint", "устаревшая правка", version=v1)
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "STALE_PATIENT_VERSION" and body["reload"] == "patient"
    assert "правка первого" not in body["detail"] and "исходная" not in body["detail"]
    assert _card(c, pid)["complaint"] == "правка первого"


def test_missing_version_is_refused_not_last_write_wins(auth_client):
    c = auth_client
    pid = _create(c, complaint="исходная")["id"]
    r = c.patch(f"/api/patients/{pid}", json={"field": "complaint", "value": "без версии"}, headers=c.csrf)
    assert r.status_code == 422
    assert _card(c, pid)["complaint"] == "исходная"


def test_event_and_route_changes_do_not_invalidate_the_card_version(auth_client):
    """Версию меняют только поля карточки: правка события или заметки не вызывает ложного конфликта."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    seen = card_version(pid)
    assert _set_fact(c, pid, "2026-08-20").status_code == 200
    assert c.patch(f"/api/patients/{pid}/history/0/note", json={"note": "заметка"}, headers=c.csrf).status_code == 200
    assert _patch(c, pid, "complaint", "после события", version=seen).status_code == 200


def test_treating_doctor_and_policy_use_the_card_version(auth_client):
    c = auth_client
    pid = _create(c)["id"]
    doctor = create_account("ver.doctor", "doctor")
    seen = card_version(pid)
    assert _patch(c, pid, "age", 61, version=seen).status_code == 200
    stale_doctor = c.put(f"/api/patients/{pid}/treating-doctor", json={"userId": doctor, "expectedVersion": seen},
                         headers=c.csrf)
    assert stale_doctor.status_code == 409 and stale_doctor.json()["code"] == "STALE_PATIENT_VERSION"
    stale_policy = _patch(c, pid, "policyNumber", "7700000011112222", version=seen)
    assert stale_policy.status_code == 409 and stale_policy.json()["code"] == "STALE_PATIENT_VERSION"
    fresh = c.put(f"/api/patients/{pid}/treating-doctor", json={"userId": doctor, "expectedVersion": card_version(pid)},
                  headers=c.csrf)
    assert fresh.status_code == 200


def test_frontend_sends_versions_and_handles_conflicts():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "expectedVersion: cached ? cached.version : undefined" in js            # правка поля и смена врача
    assert "{ userId, expectedVersion: p.version }" in js                         # сопоставление врача
    assert "{ item: itemName, checked, expected }" in js                          # чек-лист
    assert "STALE_PATIENT_VERSION" in js and "STALE_CHECKLIST_ITEM" in js
    assert "reloadAfterStaleRecord" in js


def test_export_format_has_no_card_version(auth_client):
    c = auth_client
    _create(c)
    exported = c.get("/api/export", headers=c.csrf).json()
    assert all("version" not in p for p in exported["patients"])


# ================================================================ AUD-13
# Защита записей на уровне SQLite.

def _try(conn, sql, *args):
    try:
        conn.execute(sql, args)
        conn.commit()
        return "ok"
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        return str(exc)


def test_direct_sql_cannot_rewrite_protected_history(auth_client):
    c = auth_client
    pid = _create(c, stage="consult", events=[{"date": "2026-08-20"}, {"date": "2026-08-22"}],
                  treatingDoctorUserId=decision_doctor_id())["id"]
    assert _set_fact(c, pid, "2026-08-20").status_code == 200
    assert _patch(c, pid, "diagnosis", "C34.3").status_code == 200
    assert mark_decision(c, pid, "treatment_strategy_defined", idx=1).status_code == 200
    login(c, DEV_USERNAME)
    c.csrf = {"X-CSRF-Token": c.cookies.get("clinic_csrf")}
    events = [r["id"] for r in get_conn().execute(
        "SELECT e.id FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = ? ORDER BY e.seq",
        (pid,))]
    raw = _short_conn(_db_path())
    try:
        outcomes = {
            "audit_update": _try(raw, "UPDATE audit_events SET detail = 'tampered' WHERE id = (SELECT MAX(id) FROM audit_events)"),
            "audit_delete_recent": _try(raw, "DELETE FROM audit_events WHERE id = (SELECT MAX(id) FROM audit_events)"),
            "diagnosis_history_update": _try(raw, "UPDATE patient_diagnosis_history SET old_diagnosis = 'X' WHERE patient_id = ?", pid),
            "diagnosis_history_delete": _try(raw, "DELETE FROM patient_diagnosis_history WHERE patient_id = ?", pid),
            "fact_history_update": _try(raw, "UPDATE patient_event_fact_history SET new_value = '2020-01-01' WHERE patient_id = ?", pid),
            "fact_history_delete": _try(raw, "DELETE FROM patient_event_fact_history WHERE patient_id = ?", pid),
            "history_stage_rewrite": _try(raw, "UPDATE history_entries SET stage = 'treatment' WHERE patient_id = ?", pid),
            "history_entry_delete": _try(raw, "DELETE FROM history_entries WHERE patient_id = ?", pid),
            "event_with_fact_delete": _try(raw, "DELETE FROM events WHERE id = ?", events[0]),
            "event_with_decision_delete": _try(raw, "DELETE FROM events WHERE id = ?", events[1]),
        }
        raw.execute("UPDATE events SET deleted_at = '2026-09-01T00:00:00+00:00', version = version + 1 WHERE id = ?", (events[0],))
        raw.commit()
        outcomes["unarchive_and_rewrite_fact"] = _try(
            raw, "UPDATE events SET deleted_at = NULL, actual_date = '2020-01-01' WHERE id = ?", events[0])
        outcomes["plain_restore"] = _try(raw, "UPDATE events SET deleted_at = NULL, version = version + 1 WHERE id = ?", events[0])
    finally:
        raw.close()
    expected_errors = {
        "audit_update": "audit_events_append_only",
        "audit_delete_recent": "audit_events_retention",
        "diagnosis_history_update": "diagnosis_history_append_only",
        "diagnosis_history_delete": "diagnosis_history_append_only",
        "fact_history_update": "fact_history_append_only",
        "fact_history_delete": "fact_history_append_only",
        "history_stage_rewrite": "history_entry_immutable",
        "history_entry_delete": "history_entry_immutable",
        "event_with_fact_delete": "event_direct_delete_forbidden",
        "event_with_decision_delete": "event_direct_delete_forbidden",
        "unarchive_and_rewrite_fact": "event_restore_must_not_change_facts",
    }
    for name, error in expected_errors.items():
        assert error in outcomes[name], (name, outcomes[name])
    assert outcomes["plain_restore"] == "ok"            # законное восстановление не затронуто


def test_signed_consilium_and_revision_history_cannot_be_deleted(auth_client):
    c = auth_client
    pid = _create(c)["id"]
    _dep_consilium(c, pid)
    conn = get_conn()
    conn.execute("INSERT INTO consilium_revision_history (id, appointment_id, round, comment, returned_at) "
                 "VALUES ('rv-syn', 'ap-syn', 1, 'вернуть на доработку', '2026-09-02T09:00:00+00:00')")
    conn.execute("UPDATE oncology_consilium_appointments SET status = 'signed', signed_at = '2026-09-03T09:00:00+00:00' "
                 "WHERE id = 'ap-syn'")
    conn.commit()
    raw = _short_conn(_db_path())
    try:
        assert "consilium_signed_delete_forbidden" in _try(raw, "DELETE FROM oncology_consilium_appointments WHERE id = 'ap-syn'")
        assert "consilium_revision_history_immutable" in _try(raw, "DELETE FROM consilium_revision_history WHERE id = 'rv-syn'")
        # Каскад от карточки тоже не стирает подписанное решение.
        assert "patient_has_clinical_history" in _try(raw, "DELETE FROM patients WHERE id = ?", pid)
    finally:
        raw.close()


def test_audit_retention_still_works_within_the_floor(auth_client):
    from backend.app import audit
    conn = get_conn()
    conn.execute("INSERT INTO audit_events (created_at, event_type, result) VALUES ('2000-01-01T00:00:00+00:00', 'login_success', 'ok')")
    conn.execute("INSERT INTO audit_events (created_at, event_type, result) VALUES ('2000-01-01T00:00:00+00:00', 'patient_event_deleted', 'ok')")
    conn.commit()
    removed = audit.prune(conn, retention_days=30)
    assert removed >= 1
    assert _count("SELECT COUNT(*) FROM audit_events WHERE created_at LIKE '2000-%' AND event_type = 'patient_event_deleted'") == 1
    with pytest.raises(ValueError, match="at least 30"):
        audit.prune(conn, retention_days=5)
    assert audit.RETAINED_EVENT_TYPES == set(__import__("backend.app.db", fromlist=["x"]).AUDIT_RETAINED_EVENT_TYPES)


def test_cli_audit_prune_below_floor_is_refused(auth_client, tmp_path):
    env = dict(os.environ, CLINIC_DATA_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    from .conftest import app_stopped
    with app_stopped():
        r = subprocess.run([sys.executable, "-m", "backend.cli", "audit-prune", "--days", "7"], cwd=str(PROJECT_ROOT),
                           env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "не очищен" in r.stderr


# ================================================================ AUD-14
# Проверка и восстановление определений триггеров.

def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tamper(conn, name="trg_events_archived_not_deleted"):
    conn.execute(f"DROP TRIGGER {name}")
    conn.execute(f"CREATE TRIGGER {name} BEFORE DELETE ON events WHEN 0 BEGIN SELECT RAISE(ABORT, 'never'); END")
    conn.commit()


@pytest.fixture()
def v28_db(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data))
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from backend.app import db
    conn = db.connect_live(data / "clinic.db")
    conn.execute("INSERT INTO patients (id, name, priority, stage, arrival, created_at) "
                 "VALUES ('p-syn', 'Синтетик Проверочный', 'normal', 'exam', 't', 't')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return data


def _cli(data, *args):
    env = dict(os.environ, CLINIC_DATA_DIR=str(data), PYTHONDONTWRITEBYTECODE="1", CLINIC_AI_MODE="disabled")
    return subprocess.run([sys.executable, "-m", "backend.cli", *args], cwd=str(PROJECT_ROOT), env=env,
                          capture_output=True, text=True, timeout=120)


def test_tampered_trigger_is_detected_by_check_db_without_change(v28_db):
    conn = sqlite3.connect(str(v28_db / "clinic.db"))
    _tamper(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before = _digest(v28_db / "clinic.db")
    r = _cli(v28_db, "check-db")
    assert r.returncode == 1
    assert "trg_events_archived_not_deleted" in r.stderr and "migrate-db" in r.stderr
    assert _digest(v28_db / "clinic.db") == before


def test_missing_trigger_and_unknown_trigger(v28_db):
    from backend.app import maintenance
    conn = sqlite3.connect(str(v28_db / "clinic.db"))
    conn.execute("DROP TRIGGER trg_audit_events_no_update")
    conn.execute("CREATE TRIGGER zz_local_tool AFTER INSERT ON departments BEGIN SELECT 1; END")
    conn.commit()
    conn.close()
    report = maintenance.diagnose_database(v28_db / "clinic.db")
    assert report["ok"] is False
    assert report["trigger_drift"]["missing"] == ["trg_audit_events_no_update"]
    assert report["unknown_triggers"] == ["zz_local_tool"]


def test_startup_refuses_tampered_protection_without_change(v28_db):
    from backend.app import db
    conn = sqlite3.connect(str(v28_db / "clinic.db"))
    _tamper(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before = _digest(v28_db / "clinic.db")
    with pytest.raises(db.SchemaDriftError, match="trg_events_archived_not_deleted"):
        db.connect_live(v28_db / "clinic.db")
    assert _digest(v28_db / "clinic.db") == before


def test_migrate_db_repairs_tampered_trigger_after_verified_backup(v28_db):
    from backend.app import db, maintenance
    conn = sqlite3.connect(str(v28_db / "clinic.db"))
    _tamper(conn)
    conn.close()
    r = _cli(v28_db, "migrate-db", "--confirm-offline")
    assert r.returncode == 0, r.stderr
    assert "trg_events_archived_not_deleted" in r.stdout
    backups = sorted((v28_db / "backups").glob("pre_migrate_*.cbk"))
    assert len(backups) == 1
    assert maintenance.validate_backup(backups[0])["patient_count"] == 1
    check = sqlite3.connect(str(v28_db / "clinic.db"))
    assert db.trigger_drift(check) == {"missing": [], "changed": [], "unknown": []}
    check.close()
    assert _cli(v28_db, "check-db").returncode == 0
    again = _cli(v28_db, "migrate-db", "--confirm-offline")
    assert again.returncode == 0 and "уже текущая" in again.stdout


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*_synthetic.sql")))
def test_historical_schemas_migrate_to_the_reference_triggers(fixture):
    from backend.app import config, db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((FIXTURES / fixture).read_text(encoding="utf-8"))
    conn.commit()
    counts_before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # nosec B608 - fixed names
                     for t in ("patients", "events", "history_entries", "audit_events")
                     if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()}
    db.init_db(conn)
    db.init_db(conn)                       # повтор миграции ничего не дублирует
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION
    assert db.trigger_drift(conn) == {"missing": [], "changed": [], "unknown": []}
    for table, n in counts_before.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n, table  # nosec B608
    assert conn.execute("SELECT COUNT(*) FROM patients WHERE version != 1").fetchone()[0] == 0


# ================================================================ миграция v27 → v28 и восстановление

def test_v27_database_is_migrated_explicitly_and_keeps_history(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data))
    conn = sqlite3.connect(str(data / "clinic.db"))
    conn.executescript((FIXTURES / "wpb_v27_synthetic.sql").read_text(encoding="utf-8"))
    conn.commit()
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in  # nosec B608
              ("patients", "events", "patient_diagnosis_history", "patient_attention_issues", "audit_events")}
    conn.close()
    sha = _digest(data / "clinic.db")
    assert _cli(data, "check-db").returncode == 1 and _digest(data / "clinic.db") == sha      # только диагностика
    r = _cli(data, "migrate-db", "--confirm-offline")
    assert r.returncode == 0, r.stderr
    assert "v27 → v28" in r.stdout
    conn = sqlite3.connect(str(data / "clinic.db"))
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}  # nosec B608
    conn.close()
    assert after["patients"] == before["patients"] and after["events"] == before["events"]
    assert after["patient_diagnosis_history"] == before["patient_diagnosis_history"]
    assert after["audit_events"] >= before["audit_events"]
    # Карточка с историей не удаляется, пустая — удаляется (через тот же store).
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from backend.app import db, store
    live = db.connect_live(data / "clinic.db")
    try:
        full = live.execute("SELECT id FROM patients WHERE name LIKE 'Историев%'").fetchone()[0]
        empty = live.execute("SELECT id FROM patients WHERE name LIKE 'Пустов%'").fetchone()[0]
        with pytest.raises(store.PatientDeleteBlocked) as exc:
            store.delete_patient(live, full)
        assert {"performed_events", "diagnosis_history", "mobile_access", "archived_events"} <= set(exc.value.dependencies)
        assert store.delete_patient(live, empty) is True
    finally:
        live.close()


def test_v27_backup_restores_into_v28(tmp_path, monkeypatch):
    """Резервная копия v27 (сборка wpb-76cc8ca) восстанавливается: миграция — на временной копии."""
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data))
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from backend.app import maintenance
    plain = tmp_path / "v27.sqlite"
    conn = sqlite3.connect(str(plain))
    conn.executescript((FIXTURES / "wpb_v27_synthetic.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    key = maintenance._load_active_key()
    nonce = b"\x03" * maintenance.NONCE_BYTES
    raw = plain.read_bytes()
    header = maintenance._build_header(key=key, nonce=nonce, ciphertext_len=len(raw) + 16, schema_version=27)
    hb = maintenance._header_bytes(header)
    backup = tmp_path / "v27.cbk"
    maintenance._write_container(backup, hb, AESGCM(key.key).encrypt(nonce, raw, maintenance._aad(hb)))
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("wpb-76cc8ca", 27)
    result = maintenance.restore_database(backup, data / "clinic.db", confirm_offline=True)
    assert result["migrated_from_schema_version"] == 27          # schema_version результата — версия самой копии
    restored = maintenance.diagnose_database(data / "clinic.db")
    assert restored["ok"] is True and restored["schema_version"] == 28
