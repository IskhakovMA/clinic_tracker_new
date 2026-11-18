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
    DEV_FULL_NAME,
    DEV_USERNAME,
    PROJECT_ROOT,
    TEST_MOSCOW_NOW,
    card_version,
    checklist_state,
    create_account,
    create_empty_card,
    decision_doctor_id,
    get_conn,
    login,
    make_patient_payload,
    mark_decision,
    stage_body,
    unsigned_import,
    app_stopped,
    legacy_export,
    preserve_ids_confirmation,
    set_moscow_now,
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


def _empty_card(client, **kw):
    """Пустая карточка (FIX1-P1): ни одного заполненного медицинского поля (через импорт)."""
    return create_empty_card(client, **{"stage": "exam", "events": [{"date": "2026-11-20", "time": "10:00"}], **kw})


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _patch(client, pid, field, value, version=None):
    return client.patch(f"/api/patients/{pid}", json={
        "field": field, "value": value, "expectedVersion": card_version(pid) if version is None else version,
    }, headers=client.csrf)


def _set_fact(client, pid, value, field="actualDate", idx=0, event=None):
    ev = event if event is not None else _card(client, pid)["history"][0]["events"][idx]
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
    pid = _empty_card(c)                                              # план, не факт; без медицинских полей
    card = _card(c, pid)
    assert (card["diagnosis"], card["complaint"], card["nosology"], card["pgiResult"]) == ("", "", "", "")
    audit_before = _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ?", pid)
    r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", pid) == 0   # каскад FK
    assert _count("SELECT COUNT(*) FROM events WHERE history_entry_id NOT IN (SELECT id FROM history_entries)") == 0
    assert _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND event_type = 'patient_deleted' "
                  "AND result = 'ok'", pid) == 1
    # Прежние записи аудита карточки не удаляются вместе с ней.
    assert _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ?", pid) == audit_before + 1


@pytest.mark.parametrize("dependency", sorted(DEPENDENCIES))
def test_card_with_each_dependency_is_not_deleted(auth_client, dependency):
    c = auth_client
    stage = "consult" if dependency == "consilium_decision_marks" else "exam"
    kw = {"treatingDoctorUserId": decision_doctor_id()} if dependency == "consilium_decision_marks" else {}
    pid = _empty_card(c, stage=stage, events=[{"date": "2026-08-20", "time": "10:00"}], **kw)
    DEPENDENCIES[dependency](c, pid)
    if dependency == "consilium_decision_marks":
        login(c, DEV_USERNAME)
        c.csrf = {"X-CSRF-Token": c.cookies.get("clinic_csrf")}
    before = _protected_rows(pid)
    r = c.delete(f"/api/patients/{pid}", headers=c.csrf)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "PATIENT_HAS_CLINICAL_HISTORY"
    # Пустая карточка: блокирует именно эта зависимость (у истории факта — вместе с самим фактом;
    # у правки диагноза — вместе с текущим диагнозом и выведенной из него локализацией).
    companions = {"fact_history": {"performed_events"}, "performed_events": {"fact_history"},
                  "diagnosis_history": {"current_diagnosis", "localization"}}
    assert dependency in body["dependencies"]
    assert set(body["dependencies"]) <= {dependency, *companions.get(dependency, set())}, body["dependencies"]
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
    pid = _empty_card(c, events=[{"date": "2026-11-20"}])
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
    pid = _empty_card(c, events=[{"date": "2026-11-20"}])
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
    pid = _empty_card(client, events=[{"date": "2026-11-20"}], treatingDoctorUserId=doc_id)
    keep = _empty_card(client, events=[{"date": "2026-11-20"}], treatingDoctorUserId=doc_id)
    assert login(client, "del.reader").status_code == 200
    assert client.delete(f"/api/patients/{keep}", headers=client.csrf).status_code == 403
    assert login(client, "del.doctor").status_code == 200
    assert client.delete(f"/api/patients/{pid}", headers=client.csrf).status_code == 200
    _dep_diagnosis(client, keep)
    assert client.delete(f"/api/patients/{keep}", headers=client.csrf).status_code == 409


# ---------------------------------------------------------------- FIX1-P1
# Текущие клинически значимые поля карточки — такая же зависимость, как история.

CURRENT_FIELDS = {
    # код зависимости: (столбец, синтетическое значение)
    "current_diagnosis": ("diagnosis", "C34.1"),
    "current_complaint": ("complaint", "Кашель (синтетика)"),
    "localization": ("nosology", "lung34"),
    "pgi_result": ("pgi_malignancy_confirmed", 1),
    "pgi_code": ("pgi_code", "ПГИ-001"),
    "consilium_code": ("consilium_code", "ОК-001"),
    "disease_stage": ("disease_stage", "II"),
}


def _set_columns(pid, **cols):
    conn = get_conn()
    for col, value in cols.items():
        conn.execute(f"UPDATE patients SET {col} = ? WHERE id = ?", (value, pid))  # nosec B608 - фиксированные имена
    conn.commit()


def _delete(c, pid):
    return c.delete(f"/api/patients/{pid}", headers=c.csrf)


def _direct_delete_blocked(pid):
    raw = _short_conn(_db_path())
    try:
        with pytest.raises(sqlite3.IntegrityError, match="patient_has_clinical_history"):
            raw.execute("DELETE FROM patients WHERE id = ?", (pid,))
        raw.rollback()
    finally:
        raw.close()


@pytest.mark.parametrize("code", sorted(CURRENT_FIELDS))
def test_card_with_a_current_medical_field_is_not_deleted(auth_client, code):
    """Каждое поле отдельно: API отвечает 409 с этим кодом, прямой SQL останавливает триггер."""
    c = auth_client
    pid = _empty_card(c)
    column, value = CURRENT_FIELDS[code]
    _set_columns(pid, **{column: value})
    before = _protected_rows(pid)
    r = _delete(c, pid)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "PATIENT_HAS_CLINICAL_HISTORY"
    assert r.json()["dependencies"] == [code]
    assert str(value) not in r.json()["detail"]                        # без медицинских значений
    _direct_delete_blocked(pid)
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 1
    assert _protected_rows(pid) == before


def test_pgi_result_date_alone_blocks_the_delete(auth_client):
    c = auth_client
    pid = _empty_card(c)
    _set_columns(pid, pgi_result_date="2026-08-01")
    assert _delete(c, pid).json()["dependencies"] == ["pgi_result"]
    _direct_delete_blocked(pid)


@pytest.mark.parametrize("value", ["", None])
def test_blank_medical_fields_do_not_block(auth_client, value):
    """Пустые строки и NULL — не медицинская информация; ФИО, телефон, полис, приоритет не блокируют."""
    c = auth_client
    pid = _empty_card(c, priority="high")
    _set_columns(pid, diagnosis=value, complaint=value, nosology=value, pgi_code=value, consilium_code=value,
                 disease_stage=value, pgi_result_date=value)
    assert _delete(c, pid).status_code == 200


def test_imported_card_with_diagnosis_complaint_and_pgi_is_not_deleted(auth_client):
    """Воспроизведение P1: импортированная карточка с медицинскими полями из файла (без истории изменений)."""
    c = auth_client
    pid = _empty_card(c, diagnosis="C34.1", complaint="Одышка (синтетика)", pgiResult="confirmed",
                      pgiResultDate="2026-08-01")
    assert _count("SELECT COUNT(*) FROM patient_diagnosis_history WHERE patient_id = ?", pid) == 0
    r = _delete(c, pid)
    assert r.status_code == 409
    # Локализацию импорт выводит из диагноза.
    assert set(r.json()["dependencies"]) == {"current_diagnosis", "current_complaint", "localization", "pgi_result"}
    _direct_delete_blocked(pid)


@pytest.mark.parametrize("combo", [
    ("current_diagnosis", "current_complaint"),
    ("current_diagnosis", "pgi_result"),
    ("current_complaint", "pgi_result"),
    ("current_diagnosis", "current_complaint", "pgi_result", "pgi_code", "consilium_code"),
])
def test_combinations_of_current_fields_are_all_listed(auth_client, combo):
    c = auth_client
    pid = _empty_card(c)
    _set_columns(pid, **{CURRENT_FIELDS[code][0]: CURRENT_FIELDS[code][1] for code in combo})
    r = _delete(c, pid)
    assert r.status_code == 409 and sorted(r.json()["dependencies"]) == sorted(combo)
    _direct_delete_blocked(pid)


def test_card_from_the_form_keeps_its_diagnosis_and_is_not_deleted(auth_client):
    """Форма всегда требует диагноз: такая карточка больше не удаляется (прежний путь потери данных)."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-11-20"}])["id"]
    r = _delete(c, pid)
    assert r.status_code == 409
    assert {"current_diagnosis", "current_complaint", "localization"} == set(r.json()["dependencies"])
    assert _card(c, pid)["diagnosis"] == "C34.1"


def test_complaint_and_pgi_entered_through_the_api_block_the_delete(auth_client):
    c = auth_client
    pid = _empty_card(c)
    assert _patch(c, pid, "complaint", "Слабость (синтетика)").status_code == 200
    assert _delete(c, pid).json()["dependencies"] == ["current_complaint"]
    other = _empty_card(c)
    assert _patch(c, other, "pgiResult", {"result": "not_confirmed", "date": "2026-08-01"}).status_code == 200
    assert "pgi_result" in _delete(c, other).json()["dependencies"]
    _direct_delete_blocked(other)


def test_diagnosis_written_while_delete_holds_the_lock_waits(auth_client):
    """Конкурентно: удаление проверило поля (пусто) и держит блокировку — диагноз не проскочит в окно."""
    from backend.app import store
    c = auth_client
    pid = _empty_card(c)
    path = _db_path()
    thread, result, resume = _paused(path, "DELETE FROM PATIENTS", lambda conn: store.delete_patient(conn, pid))
    other = _short_conn(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("UPDATE patients SET diagnosis = 'C34.2' WHERE id = ?", (pid,))
    finally:
        other.close()
    resume.set()
    thread.join(20)
    assert result.get("value") is True, result          # удалена пустая карточка; диагноз не был записан


def test_diagnosis_committed_first_blocks_the_delete(auth_client):
    from backend.app import store
    c = auth_client
    pid = _empty_card(c)
    path = _db_path()
    writer = _short_conn(path, timeout=5)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE patients SET diagnosis = 'C34.2' WHERE id = ?", (pid,))
    deleter = _short_conn(path, timeout=0.2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.delete_patient(deleter, pid)
    finally:
        deleter.close()
    writer.commit()
    writer.close()
    with pytest.raises(store.PatientDeleteBlocked) as exc:
        store.delete_patient(get_conn(), pid)
    assert list(exc.value.dependencies) == ["current_diagnosis"]


def test_field_set_and_delete_in_one_transaction_is_blocked_by_the_trigger(auth_client):
    """Обход сервиса: одна транзакция заполняет жалобы и сразу удаляет — триггер видит текущее значение."""
    c = auth_client
    pid = _empty_card(c)
    raw = _short_conn(_db_path())
    try:
        raw.execute("BEGIN IMMEDIATE")
        raw.execute("UPDATE patients SET complaint = 'Боль (синтетика)' WHERE id = ?", (pid,))
        with pytest.raises(sqlite3.IntegrityError, match="patient_has_clinical_history"):
            raw.execute("DELETE FROM patients WHERE id = ?", (pid,))
        raw.rollback()
    finally:
        raw.close()
    assert _count("SELECT COUNT(*) FROM patients WHERE id = ?", pid) == 1
    assert _count("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?", pid) == 1   # каскад не прошёл


def test_service_check_and_trigger_share_one_dependency_list():
    from backend.app import db, store
    codes = [code for code, _ in db.PATIENT_HISTORY_DEPENDENCIES]
    assert set(CURRENT_FIELDS) <= set(codes)
    assert set(codes) == set(store.PATIENT_DELETE_DEPENDENCY_LABELS)
    guard = db._patient_delete_guard_sql()
    for column, _ in CURRENT_FIELDS.values():
        assert column in guard


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
    if operation == "delete":
        pid = _empty_card(c, events=[{"date": "2026-11-20"}])     # удалить можно только пустую карточку
    else:
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
    event = _card(c, pid)["history"][0]["events"][0]
    _break_audit()
    try:
        assert _set_fact(c, pid, "2026-08-20", event=event).status_code == 503
    finally:
        _restore_audit()
    assert _facts(pid) == []
    assert _card(c, pid)["history"][0]["events"][0]["actualDate"] in (None, "")


# ---------------------------------------------------------------- FIX1-P2
# Новый факт, внесённый любым путём записи, получает запись initial.

def _fact_rows(pid):
    return [dict(r) for r in get_conn().execute(
        "SELECT field, old_value, new_value, change_type, source, actor_user_id, changed_at, anomaly "
        "FROM patient_event_fact_history WHERE patient_id = ? ORDER BY event_id, seq", (pid,))]


def _user_id(username=DEV_USERNAME):
    return get_conn().execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]


def _imported_id(name):
    return get_conn().execute("SELECT id FROM patients WHERE name = ?", (name,)).fetchone()[0]


def test_fact_entered_in_the_create_form_is_recorded_as_initial(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "actualDate": "2026-08-20",
                                            "actualTime": "10:00"}])["id"]
    rows = _fact_rows(pid)
    assert [(r["field"], r["old_value"], r["new_value"], r["change_type"], r["source"]) for r in rows] == [
        ("actual_date", None, "2026-08-20", "initial", "patient_create"),
        ("actual_time", None, "10:00", "initial", "patient_create"),
    ]
    assert {r["actor_user_id"] for r in rows} == {_user_id()}          # автор — создатель карточки
    assert all(r["anomaly"] is None for r in rows)
    # Время записи — момент внесения на сервере, а не фактическая дата события.
    assert all(r["changed_at"][:10] != "2026-08-20" for r in rows)


def test_card_without_facts_has_no_fact_history(auth_client):
    pid = _create(auth_client, stage="exam", events=[{"date": "2026-11-20", "time": "10:00"}])["id"]
    assert _fact_rows(pid) == []


def test_imported_fact_is_initial_without_a_forged_author(auth_client):
    c = auth_client
    name = "Импортов Факт Синтетик"
    raw = json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(
        name=name, events=[{"date": "2026-08-20", "actualDate": "2026-08-21", "actualTime": "09:00"}])]},
        ensure_ascii=False)
    assert unsigned_import(c, raw, "add").status_code == 200
    rows = _fact_rows(_imported_id(name))
    assert [(r["field"], r["old_value"], r["new_value"], r["change_type"], r["source"]) for r in rows] == [
        ("actual_date", None, "2026-08-21", "initial", "import"),
        ("actual_time", None, "09:00", "initial", "import"),
    ]
    # Автор исторического факта неизвестен: импортирующий сотрудник им не назначается.
    assert {r["actor_user_id"] for r in rows} == {None}


def test_migrated_fact_is_initial_with_source_migration(auth_client):
    client = auth_client
    name = "Перенесенов Синтетик"
    raw = json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(
        name=name, events=[{"date": "2026-08-20", "actualDate": "2026-08-20"}])]}, ensure_ascii=False)
    r = client.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=client.csrf)
    assert r.status_code == 200, r.text
    rows = _fact_rows(_imported_id(name))
    assert [(r["change_type"], r["source"], r["actor_user_id"]) for r in rows] == [("initial", "migration", None)]


def _legacy_doc(c, pid):
    """Неподписанная выгрузка одной карточки (прежний формат) — для дописывания событий режимом update."""
    from .conftest import legacy_export
    exported = c.get("/api/export", headers=c.csrf).json()
    return legacy_export({**exported, "patients": [p for p in exported["patients"] if p["id"] == pid]})


def test_created_fact_then_correction_and_clearing(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "actualDate": "2026-08-20"}])["id"]
    assert _set_fact(c, pid, "2026-08-21").status_code == 200
    assert _set_fact(c, pid, "").status_code == 200
    assert [(r["old_value"], r["new_value"], r["change_type"], r["source"]) for r in _fact_rows(pid)] == [
        (None, "2026-08-20", "initial", "patient_create"),
        ("2026-08-20", "2026-08-21", "correction", "event_update"),
        ("2026-08-21", None, "cleared", "event_update"),
    ]


def test_stage_change_on_a_card_created_with_a_fact_keeps_one_initial(auth_client):
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "actualDate": "2026-08-20"}])["id"]
    r = c.post(f"/api/patients/{pid}/stage", json=stage_body(c, pid, {"newStage": "appointment"}), headers=c.csrf)
    assert r.status_code == 200, r.text
    assert [(r["change_type"], r["source"]) for r in _fact_rows(pid)] == [("initial", "patient_create")]


def test_insert_event_with_a_fact_requires_a_source(auth_client):
    """Новый путь записи не может забыть историю факта: без источника вставка отклоняется."""
    from backend.app import store
    from backend.app.models import EventIn
    pid = _create(auth_client, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    conn = get_conn()
    entry = conn.execute("SELECT id FROM history_entries WHERE patient_id = ?", (pid,)).fetchone()[0]
    with pytest.raises(RuntimeError, match="fact_source"):
        store.insert_event(conn, entry, 99, EventIn(date="2026-08-20", actualDate="2026-08-20"))
    conn.rollback()
    store.insert_event(conn, entry, 99, EventIn(date="2026-08-30"))     # план без факта — без истории
    conn.rollback()


def test_v27_fact_keeps_compatibility_without_initial(auth_client):
    """Факт, записанный до v28 (без истории), не получает задним числом выдуманной записи initial."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    conn = get_conn()
    conn.execute("UPDATE events SET actual_date = '2026-08-19' WHERE history_entry_id IN "
                 "(SELECT id FROM history_entries WHERE patient_id = ?)", (pid,))
    conn.commit()
    assert _fact_rows(pid) == []
    assert _set_fact(c, pid, "2026-08-18").status_code == 200
    assert [(r["old_value"], r["new_value"], r["change_type"]) for r in _fact_rows(pid)] == [
        ("2026-08-19", "2026-08-18", "correction")]


# ---------------------------------------------------------------- FIX1-P3
# Историческая выгрузка с фактической датой из будущего: импорт разрешён, но явно.

FUTURE = "2031-01-10"


def _future_doc(name, *, future=FUTURE, extra=()):
    events = [{"date": "2026-08-20", "actualDate": future}, *extra]
    return json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(name=name, events=events)]},
                      ensure_ascii=False)


def test_preview_reports_future_facts_before_commit(auth_client):
    c = auth_client
    raw = _future_doc("Будущев Предпросмотр", extra=[{"date": "2026-08-21", "actualDate": "2026-08-21"}])
    r = c.post("/api/import/preview", json={"raw": raw}, headers=c.csrf)
    assert r.status_code == 200, r.text
    view = r.json()["futureFacts"]
    assert view["add"]["facts"] == 1 and view["add"]["patients"] == 1
    assert view["add"]["message"] and FUTURE not in json.dumps(view)          # без самих дат
    assert view["update"]["facts"] == 0                                       # update не создаёт карточек
    assert _count("SELECT COUNT(*) FROM patients WHERE name = 'Будущев Предпросмотр'") == 0


def test_preview_without_future_facts_has_no_warning(auth_client):
    c = auth_client
    raw = _future_doc("Прошлов Синтетик", future="2026-08-20")
    view = c.post("/api/import/preview", json={"raw": raw}, headers=c.csrf).json()["futureFacts"]
    assert view["add"] == {"facts": 0, "patients": 0, "message": ""}


def test_future_fact_is_imported_unchanged_unverified_and_audited(auth_client):
    c = auth_client
    name = "Будущев Синтетик"
    r = unsigned_import(c, _future_doc(name), "add")
    assert r.status_code == 200, r.text
    assert r.json()["futureFacts"] == 1                                       # итог для оператора
    pid = _imported_id(name)
    ev = _card(c, pid)["history"][0]["events"][0]
    assert ev["actualDate"] == FUTURE                                         # значение не изменено молча
    assert ev["actualDateUnverified"] is True                                 # не подтверждённый факт
    rows = _fact_rows(pid)
    assert [(r["new_value"], r["change_type"], r["source"], r["anomaly"], r["actor_user_id"]) for r in rows] == [
        (FUTURE, "initial", "import", "future_date", None)]
    audits = get_conn().execute("SELECT event_type, detail FROM audit_events WHERE patient_id = ? "
                                "AND event_type = 'import_future_facts'", (pid,)).fetchall()
    assert len(audits) == 1
    detail = audits[0]["detail"]
    assert "anomaly=future_date" in detail and "facts=1" in detail and "verified=0" in detail
    assert FUTURE not in detail and "C34" not in detail and "Синтетик" not in detail
    committed = get_conn().execute("SELECT detail FROM audit_events WHERE event_type = 'import_committed' "
                                   "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "future_facts=1" in committed and FUTURE not in committed


def test_update_import_never_appends_facts(auth_client):
    """update дописывает только плановые события: будущий (как и любой) факт им не вносится."""
    c = auth_client
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20", "time": "10:00"}])["id"]
    doc = _legacy_doc(c, pid)
    doc["patients"][0]["history"][0]["events"].append({"date": "2026-08-25", "actualDate": FUTURE})
    raw = json.dumps(doc, ensure_ascii=False)
    report = c.post("/api/import/preview", json={"raw": raw}, headers=c.csrf).json()
    assert report["futureFacts"]["update"]["facts"] == 0 and report["overwriteImpact"]["update"]["blocked"]
    assert unsigned_import(c, raw, "update").status_code == 409
    assert _fact_rows(pid) == [] and len(_card(c, pid)["history"][0]["events"]) == 1


def test_migration_with_a_future_fact_is_detected_and_audited(auth_client):
    c = auth_client
    raw = _future_doc("Будущев Перенесенов")
    preview = c.post("/api/migrate/localstorage/preview", json={"raw": raw}, headers=c.csrf)
    assert preview.status_code == 200 and preview.json()["futureFacts"]["facts"] == 1, preview.text
    r = c.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=c.csrf)
    assert r.status_code == 200 and r.json()["futureFacts"] == 1, r.text
    pid = _imported_id("Будущев Перенесенов")
    assert [(r["source"], r["anomaly"]) for r in _fact_rows(pid)] == [("migration", "future_date")]
    assert _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND event_type = 'import_future_facts'",
                  pid) == 1
    assert "future_facts=1" in get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'migration_committed'").fetchone()[0]


def test_unverified_flag_is_exported_and_clears_after_staff_correction(auth_client):
    c = auth_client
    name = "Будущев Исправленов"
    assert unsigned_import(c, _future_doc(name), "add").status_code == 200
    pid = _imported_id(name)
    exported = [p for p in c.get("/api/export", headers=c.csrf).json()["patients"] if p["id"] == pid][0]
    # FIX2 (вариант A): неподтверждённость уходит в файл как true; значение даты прежнее.
    assert exported["history"][0]["events"][0]["actualDateUnverified"] is True
    assert exported["history"][0]["events"][0]["actualDate"] == FUTURE
    # Сотрудник не может подтвердить дату из будущего — только исправить её.
    assert _set_fact(c, pid, "2031-01-11").json()["code"] == "FACT_DATE_IN_FUTURE"
    assert _set_fact(c, pid, "2026-08-20").status_code == 200
    ev = _card(c, pid)["history"][0]["events"][0]
    assert ev["actualDateUnverified"] is False
    assert [(r["change_type"], r["source"], r["anomaly"]) for r in _fact_rows(pid)] == [
        ("initial", "import", "future_date"), ("correction", "event_update", None)]
    exported = [p for p in c.get("/api/export", headers=c.csrf).json()["patients"] if p["id"] == pid][0]
    assert "actualDateUnverified" not in exported["history"][0]["events"][0]   # после исправления — поля нет


def test_future_fact_import_is_not_saved_without_its_anomaly_audit(auth_client):
    c = auth_client
    conn = get_conn()
    conn.execute("CREATE TRIGGER zz_test_fail_future_audit BEFORE INSERT ON audit_events "
                 "WHEN NEW.event_type = 'import_future_facts' "
                 "BEGIN SELECT RAISE(ABORT, 'test: audit storage unavailable'); END")
    conn.commit()
    try:
        r = unsigned_import(c, _future_doc("Будущев Безаудитов"), "add")
    finally:
        conn.execute("DROP TRIGGER IF EXISTS zz_test_fail_future_audit")
        conn.commit()
    assert r.status_code == 503, r.text
    assert _count("SELECT COUNT(*) FROM patients WHERE name = 'Будущев Безаудитов'") == 0


def test_staff_paths_still_reject_future_facts(auth_client):
    """Исключение только для исторических источников: форма и перевод этапа — по-прежнему 422."""
    c = auth_client
    r = c.post("/api/patients", json=make_patient_payload(events=[{"date": "2026-08-20", "actualDate": FUTURE}]),
               headers=c.csrf)
    assert r.status_code == 422
    from backend.app import store
    assert store.HISTORICAL_FACT_SOURCES == ("import", "migration", "legacy")


# ---------------------------------------------------------------- FIX2
# Неподтверждённость факта переживает экспорт и импорт в другую базу (вариант A).

def _other_database(client, monkeypatch, tmp_path, label, now):
    """Перенос в другую установку: приложение останавливается и запускается на новой
    пустой синтетической базе с тем же ключом подписи выгрузок; часы — now."""
    from backend.app import config
    data = tmp_path / label
    data.mkdir(mode=0o700)
    with app_stopped():
        monkeypatch.setattr(config, "DATA_DIR", data.resolve())
        monkeypatch.setattr(config, "DB_PATH", data.resolve() / "clinic.db")
        monkeypatch.setattr(config, "BACKUP_DIR", data.resolve() / "backups")
        from .conftest import prepare_test_database
        prepare_test_database()
    set_moscow_now(monkeypatch, now)
    assert _count("SELECT COUNT(*) FROM patients") == 0
    create_account(DEV_USERNAME, "developer", DEV_FULL_NAME)
    assert login(client, DEV_USERNAME).status_code == 200


def _export_text(c):
    r = c.get("/api/export", headers=c.csrf)
    assert r.status_code == 200, r.text
    return r.text


def _signed_add(c, raw):
    return c.post("/api/import/commit", json={"raw": raw, "mode": "add",
                  "preserveIdsConfirmation": preserve_ids_confirmation(raw, "add")}, headers=c.csrf)


def _first_event(raw):
    return json.loads(raw)["patients"][0]["history"][0]["events"][0]


def _source_file(c, monkeypatch, name="Переносов Синтетик"):
    """База A (2026-10-01): импорт факта 2031-01-10 из исторического файла, подписанная выгрузка."""
    set_moscow_now(monkeypatch, "2026-10-01T09:00:00+03:00")
    assert unsigned_import(c, _future_doc(name), "add").status_code == 200
    raw = _export_text(c)
    assert _first_event(raw)["actualDateUnverified"] is True
    return raw


def _only_card_event(c):
    pid = get_conn().execute("SELECT id FROM patients").fetchone()[0]
    return pid, _card(c, pid)["history"][0]["events"][0]


def test_transfer_after_the_date_keeps_the_fact_unverified(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    preview = c.post("/api/import/preview", json={"raw": raw}, headers=c.csrf).json()
    assert preview["provenance"]["status"] == "verified_signed"
    assert preview["futureFacts"]["add"]["facts"] == 0                      # дата уже наступила
    assert preview["unverifiedFacts"]["add"] == {"facts": 1, "patients": 1, "message": preview[
        "unverifiedFacts"]["add"]["message"]} and FUTURE not in json.dumps(preview["unverifiedFacts"])
    r = _signed_add(c, raw)
    assert r.status_code == 200 and (r.json()["futureFacts"], r.json()["unverifiedFacts"]) == (0, 1), r.text
    pid, ev = _only_card_event(c)
    assert ev["actualDate"] == FUTURE and ev["actualDateUnverified"] is True
    rows = _fact_rows(pid)
    assert [(r["old_value"], r["new_value"], r["change_type"], r["source"], r["anomaly"], r["actor_user_id"])
            for r in rows] == [(None, FUTURE, "initial", "import", "unverified_transfer", None)]
    assert not rows[0]["changed_at"].startswith(FUTURE)                    # время переноса, не события
    audits = get_conn().execute("SELECT detail FROM audit_events WHERE patient_id = ? "
                                "AND event_type = 'import_unverified_facts'", (pid,)).fetchall()
    assert len(audits) == 1 and "anomaly=unverified_transfer" in audits[0][0] and "facts=1" in audits[0][0]
    assert FUTURE not in audits[0][0] and "C34" not in audits[0][0]
    assert _count("SELECT COUNT(*) FROM audit_events WHERE event_type = 'import_future_facts'") == 0
    committed = get_conn().execute("SELECT detail FROM audit_events WHERE event_type = 'import_committed' "
                                   "AND result = 'ok'").fetchone()[0]
    assert "future_facts=0 unverified_facts=1" in committed and FUTURE not in committed


def test_transfer_before_the_date_counts_the_fact_once_as_future(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2026-10-02T09:00:00+03:00")
    r = _signed_add(c, raw)
    assert (r.json()["futureFacts"], r.json()["unverifiedFacts"]) == (1, 0)   # показатели не смешиваются
    pid, ev = _only_card_event(c)
    assert ev["actualDateUnverified"] is True
    assert [(r["change_type"], r["anomaly"]) for r in _fact_rows(pid)] == [("initial", "future_date")]
    assert _count("SELECT COUNT(*) FROM audit_events WHERE event_type = 'import_unverified_facts'") == 0


def test_two_consecutive_transfers_keep_the_status(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    assert _signed_add(c, raw).status_code == 200
    second = _export_text(c)
    assert _first_event(second)["actualDateUnverified"] is True
    _other_database(c, monkeypatch, tmp_path, "db-c", "2031-03-01T09:00:00+03:00")
    assert _signed_add(c, second).json()["unverifiedFacts"] == 1
    pid, ev = _only_card_event(c)
    assert ev["actualDateUnverified"] is True
    assert [(r["change_type"], r["source"], r["anomaly"]) for r in _fact_rows(pid)] == [
        ("initial", "import", "unverified_transfer")]                          # без дублей истории


def test_unverified_flag_is_covered_by_the_signature(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    for change in ("drop", "false"):
        doc = json.loads(raw)
        event = doc["patients"][0]["history"][0]["events"][0]
        if change == "drop":
            del event["actualDateUnverified"]
        else:
            event["actualDateUnverified"] = False
        tampered = json.dumps(doc, ensure_ascii=False)
        preview = c.post("/api/import/preview", json={"raw": tampered}, headers=c.csrf).json()
        assert preview["valid"] is False and preview["provenance"]["status"] == "invalid_signature", change
        assert _signed_add(c, tampered).status_code == 422, change
    assert _count("SELECT COUNT(*) FROM patients") == 0


def test_false_is_not_proof_of_confirmation(auth_client):
    c = auth_client
    for value in (False, "false", 0, None):
        events = [{"date": "2026-08-20", "actualDate": "2026-08-20", "actualDateUnverified": value}]
        raw = json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(name="Ложнов", events=events)]},
                         ensure_ascii=False)
        r = unsigned_import(c, raw, "add")
        if value is None:
            assert r.status_code == 200                       # поле не передано по смыслу — сведений нет
        else:
            assert r.status_code == 422, value
    pid = _imported_id("Ложнов")
    assert _fact_rows(pid)[0]["anomaly"] is None and _card(c, pid)["history"][0]["events"][0][
        "actualDateUnverified"] is False
    no_fact = json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(
        name="Безфактов", events=[{"date": "2026-08-20", "actualDateUnverified": True}])]}, ensure_ascii=False)
    assert unsigned_import(c, no_fact, "add").status_code == 422


def test_client_api_does_not_accept_the_flag(auth_client):
    c = auth_client
    created = c.post("/api/patients", json=make_patient_payload(events=[
        {"date": "2026-08-20", "actualDate": "2026-08-20", "actualDateUnverified": True}]), headers=c.csrf)
    assert created.status_code == 422
    pid = _create(c, stage="exam", events=[{"date": "2026-08-20"}])["id"]
    ev = _card(c, pid)["history"][0]["events"][0]
    patch = c.patch(f"/api/patients/{pid}/events/{ev['id']}",
                    json={"field": "actualDateUnverified", "value": "", "version": ev["version"]}, headers=c.csrf)
    assert patch.status_code == 422
    stage = c.post(f"/api/patients/{pid}/stage", json=stage_body(c, pid, {"newStage": "appointment", "lastEventUpdate": {
        "actualDate": "2026-08-20", "actualDateUnverified": True}}), headers=c.csrf)
    assert stage.status_code == 422
    assert _fact_rows(pid) == []


def test_unsigned_file_with_the_flag_keeps_it(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    stripped = json.dumps(legacy_export(json.loads(raw)), ensure_ascii=False)   # без подписи, признак на месте
    assert unsigned_import(c, stripped, "add", confirm=False).status_code == 428
    r = unsigned_import(c, stripped, "add")
    assert r.status_code == 200 and r.json()["unverifiedFacts"] == 1
    assert _only_card_event(c)[1]["actualDateUnverified"] is True


def test_residual_risk_stripped_signature_and_flag_is_an_explicit_unsigned_import(auth_client, monkeypatch, tmp_path):
    """Остаточный риск варианта A (задокументирован): без подписи и признака файл идёт
    неподписанным путём — только с подтверждением оператора и аудитом; факт становится
    обычным историческим фактом (без отметки о подтверждении сотрудником)."""
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    doc = legacy_export(json.loads(raw))
    del doc["patients"][0]["history"][0]["events"][0]["actualDateUnverified"]
    stripped = json.dumps(doc, ensure_ascii=False)
    preview = c.post("/api/import/preview", json={"raw": stripped}, headers=c.csrf).json()
    assert preview["provenance"]["confirmationRequired"] is True
    assert preview["provenance"]["status"] in ("unsigned_legacy", "unsigned_external")
    assert unsigned_import(c, stripped, "add", confirm=False).status_code == 428
    assert unsigned_import(c, stripped, "add").status_code == 200
    pid, ev = _only_card_event(c)
    assert ev["actualDateUnverified"] is False
    assert [(r["source"], r["actor_user_id"]) for r in _fact_rows(pid)] == [("import", None)]   # не «подтверждено»
    committed = get_conn().execute("SELECT detail FROM audit_events WHERE event_type = 'import_committed' "
                                   "AND result = 'ok'").fetchone()[0]
    assert "unsigned_confirmed=1" in committed and "provenance=unsigned" in committed


def test_transferred_unverified_import_is_not_saved_without_its_audit(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    conn = get_conn()
    conn.execute("CREATE TRIGGER zz_test_fail_unverified_audit BEFORE INSERT ON audit_events "
                 "WHEN NEW.event_type = 'import_unverified_facts' "
                 "BEGIN SELECT RAISE(ABORT, 'test: audit storage unavailable'); END")
    conn.commit()
    try:
        r = _signed_add(c, raw)
    finally:
        conn.execute("DROP TRIGGER IF EXISTS zz_test_fail_unverified_audit")
        conn.commit()
    assert r.status_code == 503, r.text
    assert _count("SELECT COUNT(*) FROM patients") == 0
    assert _count("SELECT COUNT(*) FROM patient_event_fact_history") == 0


def test_transferred_fact_history_initial_correction_cleared_reentry(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    assert _signed_add(c, raw).status_code == 200
    pid, _ = _only_card_event(c)
    # Сотрудник без права записи не может снять отметку.
    create_account("fx.reader", "deputy_chief_oncology")
    assert login(c, "fx.reader").status_code == 200
    assert _set_fact(c, pid, "2031-01-09").status_code == 403
    assert login(c, DEV_USERNAME).status_code == 200
    assert _card(c, pid)["history"][0]["events"][0]["actualDateUnverified"] is True
    for value in ("", FUTURE):                     # очистка и повторный ввод той же наступившей даты
        assert _set_fact(c, pid, value).status_code == 200
    assert _set_fact(c, pid, "2031-01-09").status_code == 200
    assert [(r["old_value"], r["new_value"], r["change_type"], r["source"], r["anomaly"]) for r in _fact_rows(pid)] == [
        (None, FUTURE, "initial", "import", "unverified_transfer"),
        (FUTURE, None, "cleared", "event_update", None),
        (None, FUTURE, "reentry", "event_update", None),
        (FUTURE, "2031-01-09", "correction", "event_update", None),
    ]
    assert _card(c, pid)["history"][0]["events"][0]["actualDateUnverified"] is False
    assert "actualDateUnverified" not in _first_event(_export_text(c))
    assert _count("SELECT COUNT(*) FROM audit_events WHERE patient_id = ? AND event_type = 'patient_updated' "
                  "AND result = 'ok' AND detail LIKE '%field=actualDate%'", pid) == 3


def test_update_mode_does_not_raise_or_lower_trust(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    assert _signed_add(c, raw).status_code == 200
    pid, _ = _only_card_event(c)
    doc = legacy_export(json.loads(_export_text(c)))
    del doc["patients"][0]["history"][0]["events"][0]["actualDateUnverified"]   # «без отметки»
    r = unsigned_import(c, json.dumps(doc, ensure_ascii=False), "update")
    assert r.status_code == 200 and r.json()["unchanged"] == 1 and r.json()["unverifiedFacts"] == 0
    assert _card(c, pid)["history"][0]["events"][0]["actualDateUnverified"] is True
    assert len(_fact_rows(pid)) == 1
    # Файл с отметкой не возвращает её факту, исправленному сотрудником (update не трогает факты).
    assert _set_fact(c, pid, "2031-01-09").status_code == 200
    stale = legacy_export(json.loads(_export_text(c)))
    stale["patients"][0]["history"][0]["events"][0]["actualDateUnverified"] = True
    r = unsigned_import(c, json.dumps(stale, ensure_ascii=False), "update")
    assert r.status_code == 200 and r.json()["unchanged"] == 1
    assert _card(c, pid)["history"][0]["events"][0]["actualDateUnverified"] is False
    assert len(_fact_rows(pid)) == 2


def test_migration_keeps_a_transferred_unverified_fact(auth_client, monkeypatch, tmp_path):
    c = auth_client
    raw = _source_file(c, monkeypatch)
    _other_database(c, monkeypatch, tmp_path, "db-b", "2031-02-01T09:00:00+03:00")
    local = json.dumps(legacy_export(json.loads(raw)), ensure_ascii=False)
    preview = c.post("/api/migrate/localstorage/preview", json={"raw": local}, headers=c.csrf).json()
    assert preview["unverifiedFacts"]["facts"] == 1 and preview["futureFacts"]["facts"] == 0
    r = c.post("/api/migrate/localstorage/commit", json={"raw": local, "mode": "add"}, headers=c.csrf)
    assert r.status_code == 200 and r.json()["unverifiedFacts"] == 1, r.text
    pid, ev = _only_card_event(c)
    assert ev["actualDateUnverified"] is True
    assert [(r["source"], r["anomaly"]) for r in _fact_rows(pid)] == [("migration", "unverified_transfer")]
    assert "unverified_facts=1" in get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'migration_committed'").fetchone()[0]


def test_files_without_unverified_facts_keep_the_previous_format(auth_client):
    """Новая выгрузка без неподтверждённых фактов — те же ключи событий, что и раньше;
    прежний подписанный файл (без признака) импортируется как обычные исторические факты."""
    c = auth_client
    _create(c, stage="exam", events=[{"date": "2026-08-20", "actualDate": "2026-08-20"}, {"date": "2026-11-20"}])
    raw = _export_text(c)
    doc = json.loads(raw)
    assert doc["formatVersion"] == 2
    for ev in doc["patients"][0]["history"][0]["events"]:
        assert set(ev) == {"id", "date", "time", "treatmentType", "actualDate", "actualTime", "matchedPlan",
                           "noPlan", "consiliumDecision", "setAt"}


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
    from .conftest import prepare_test_database
    prepare_test_database()
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
    assert "v27 → v33" in r.stdout
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
    # New operational protocol: migration success does not itself grant startup.
    assert _cli(data, "purge-sessions").returncode == 1
    (data / "clinic.db").chmod(0o600)  # operator fixes legacy file permissions explicitly
    assert _cli(data, "verify-db", "--confirm-offline").returncode == 0
    live = db.connect_live(data / "clinic.db")
    try:
        full = live.execute("SELECT id FROM patients WHERE name LIKE 'Историев%'").fetchone()[0]
        empty = live.execute("SELECT id FROM patients WHERE name LIKE 'Пустов%'").fetchone()[0]
        with pytest.raises(store.PatientDeleteBlocked) as exc:
            store.delete_patient(live, full)
        assert {"performed_events", "diagnosis_history", "mobile_access", "archived_events"} <= set(exc.value.dependencies)
        # FIX1-P1: у «пустой» карточки v27 есть текущие диагноз и жалобы — она тоже защищена.
        with pytest.raises(store.PatientDeleteBlocked) as exc:
            store.delete_patient(live, empty)
        assert set(exc.value.dependencies) == {"current_diagnosis", "current_complaint", "localization"}
        live.execute("UPDATE patients SET diagnosis = '', complaint = '', nosology = '' WHERE id = ?", (empty,))
        live.commit()
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
    assert restored["ok"] is True and restored["schema_version"] == 33
