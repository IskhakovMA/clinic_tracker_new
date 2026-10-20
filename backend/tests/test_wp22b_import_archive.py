"""WP2.2-B, финальные исправления: перенос исторических решений ОК из файла и
сохранность архивных решений этапа ОК.

Проверяется состояние SQLite (patients, events, audit_events), а не только коды
ответа. Данные синтетические, база временная (фикстура client)."""
import hashlib
import json
import sqlite3
import threading

import pytest

from .conftest import (
    DECISION_DOCTOR_USERNAME,
    DEV_USERNAME,
    PROJECT_ROOT,
    create_account,
    create_department,
    decision_doctor_id,
    get_conn,
    login,
    make_patient_payload,
    mark_decision,
    stage_body,
    unsigned_import,
)

DECISION_COLUMNS = ("consilium_decision", "consilium_decision_by_user_id", "consilium_decision_at",
                    "consilium_decision_source", "consilium_decision_imported_by_user_id",
                    "consilium_decision_imported_at", "consilium_decision_import_id")


# ---------- вспомогательное ----------

def _create(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _card(client, pid):
    return client.get(f"/api/patients/{pid}").json()


def _row(event_id):
    return dict(get_conn().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())


def _as(client, username):
    assert login(client, username).status_code == 200


def _counts():
    conn = get_conn()
    return {
        "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "decisions": conn.execute("SELECT COUNT(*) FROM events WHERE consilium_decision IS NOT NULL").fetchone()[0],
        "imported_audit": conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'consilium_decision_imported'").fetchone()[0],
        "import_ok": conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'import_committed' AND result = 'ok'").fetchone()[0],
    }


def _doc(*patients):
    return json.dumps({"schemaVersion": 21, "patients": list(patients)}, ensure_ascii=False)


def _file_patient(name, decisions=("dispensary_observation",)):
    base = {k: v for k, v in make_patient_payload(name=name, stage="consult").items() if k != "events"}
    events = [{"date": f"2026-08-2{i}", **({"consiliumDecision": d} if d else {})} for i, d in enumerate(decisions)]
    return {**base, "history": [{"stage": "consult", "enteredAt": "2026-08-01T00:00:00+00:00", "events": events}]}


def _patient_events(name):
    return [dict(r) for r in get_conn().execute(
        """SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id
           JOIN patients p ON p.id = h.patient_id WHERE p.name = ? ORDER BY h.seq, e.seq""", (name,))]


def _fail_audit(event_type):
    conn = get_conn()
    conn.execute(f"""CREATE TRIGGER wp22b_fix_fail_audit BEFORE INSERT ON audit_events
                     WHEN NEW.event_type = '{event_type}'
                     BEGIN SELECT RAISE(ABORT, 'audit storage unavailable'); END""")
    conn.commit()


def _restore_audit():
    conn = get_conn()
    conn.execute("DROP TRIGGER IF EXISTS wp22b_fix_fail_audit")
    conn.commit()


def _dev_id():
    return get_conn().execute("SELECT id FROM users WHERE username = ?", (DEV_USERNAME,)).fetchone()[0]


# ====================== ИМПОРТ ======================

def test_i01_regular_import_without_decisions_is_unchanged(auth_client):
    before = _counts()
    raw = _doc(_file_patient("Импортов Без Решений", decisions=(None,)))
    preview = auth_client.post("/api/import/preview", json={"raw": raw}).json()
    assert preview["historicalDecisions"]["add"] == {"required": False, "decisions": 0, "message": ""}
    r = unsigned_import(auth_client, raw, "add")
    assert r.status_code == 200 and r.json()["added"] == 1
    assert "importedDecisions" not in r.json()                         # ответ импорта — прежний
    after = _counts()
    assert after["decisions"] == before["decisions"] and after["imported_audit"] == before["imported_audit"]


@pytest.mark.parametrize("variant", ["none", "wrong_count", "other_file", "other_mode"])
def test_i02_regular_import_with_a_decision_is_refused_before_any_write(auth_client, variant):
    raw = _doc(_file_patient("Импортов Отказ", decisions=("treatment_strategy_defined",)),
               _file_patient("Импортов Отказ Второй", decisions=(None,)))
    before = _counts()
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    body = {"raw": raw, "mode": "add", "unsignedConfirmation": {"confirmed": True, "fileSha256": sha, "mode": "add"}}
    confirmation = {"confirmed": True, "fileSha256": sha, "mode": "add",
                    "action": "import_historical_consilium_decisions", "decisions": 1}
    if variant == "wrong_count":
        body["historicalDecisionsConfirmation"] = {**confirmation, "decisions": 2}
    elif variant == "other_file":
        body["historicalDecisionsConfirmation"] = {**confirmation, "fileSha256": "0" * 64}
    elif variant == "other_mode":
        body["historicalDecisionsConfirmation"] = {**confirmation, "mode": "replace"}
    r = auth_client.post("/api/import/commit", json=body, headers=auth_client.csrf)
    assert r.status_code == 428, r.text
    assert r.json()["code"] == "HISTORICAL_DECISIONS_CONFIRMATION_REQUIRED" and r.json()["decisions"] == 1
    assert _counts() == before                                          # ни одной карточки — частичного импорта нет
    denied = get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'import_committed' AND result = 'denied' "
        "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "reason=historical_decisions_confirmation_required" in denied and "decisions=1" in denied


def test_i03_i04_i05_confirmed_historical_import_by_developer_keeps_author_unknown(auth_client):
    raw = _doc(_file_patient("Импортов Исторический", decisions=("treatment_strategy_changed",)))
    r = unsigned_import(auth_client, raw, "add", historical_decisions=1)
    assert r.status_code == 200, r.text
    ev = _patient_events("Импортов Исторический")[0]
    assert ev["consilium_decision"] == "treatment_strategy_changed" and ev["consilium_decision_source"] == "import"
    # Автор и время решения неизвестны; исполнитель импорта хранится отдельно и автором не становится.
    assert ev["consilium_decision_by_user_id"] is None and ev["consilium_decision_at"] is None
    assert ev["consilium_decision_imported_by_user_id"] == _dev_id()
    assert ev["consilium_decision_imported_at"] and len(ev["consilium_decision_import_id"]) == 32
    pid = get_conn().execute("SELECT id FROM patients WHERE name = 'Импортов Исторический'").fetchone()[0]
    out = _card(auth_client, pid)["history"][0]["events"][0]
    assert out["consiliumDecisionByName"] is None and out["consiliumDecisionAt"] is None
    assert out["consiliumDecisionImportedByName"] == "Разработчиков Тест Системович"


def test_i04_import_rights_are_not_extended(client):
    d = create_department("Отделение импорта")
    create_account(DEV_USERNAME)
    create_account("imp.doctor", "doctor", "Импортов Врач", department_ids=[d])
    create_account("imp.chair", "consilium_chair", "Импортов Председатель")
    create_account("imp.deputy", "deputy_chief_oncology", "Импортов Заместитель")
    raw = _doc(_file_patient("Импортов Права", decisions=("treatment_strategy_defined",)))
    for username in ("imp.chair", "imp.deputy"):
        _as(client, username)
        r = unsigned_import(client, raw, "add", historical_decisions=1)
        assert r.status_code == 403, (username, r.status_code)
    # Врач с действующим правом импорта (add, своя область): то же подтверждение, автор неизвестен.
    _as(client, "imp.doctor")
    r = unsigned_import(client, raw, "add", historical_decisions=1)
    assert r.status_code == 200, r.text
    ev = _patient_events("Импортов Права")[0]
    doctor = get_conn().execute("SELECT id FROM users WHERE username = 'imp.doctor'").fetchone()[0]
    assert (ev["consilium_decision_by_user_id"], ev["consilium_decision_imported_by_user_id"]) == (None, doctor)
    # update/replace — по-прежнему только разработчику.
    assert unsigned_import(client, raw, "replace", historical_decisions=1).status_code == 403


def test_i06_each_imported_decision_has_its_own_required_audit(auth_client):
    raw = _doc(_file_patient("Импортов Аудит А", decisions=("treatment_strategy_defined", "dispensary_observation")),
               _file_patient("Импортов Аудит Б", decisions=("treatment_strategy_changed",)))
    assert unsigned_import(auth_client, raw, "add", historical_decisions=3).status_code == 200
    rows = [dict(r) for r in get_conn().execute(
        "SELECT * FROM audit_events WHERE event_type = 'consilium_decision_imported' ORDER BY id")]
    assert len(rows) == 3
    events = {e["id"]: e for name in ("Импортов Аудит А", "Импортов Аудит Б") for e in _patient_events(name)}
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    import_ids = set()
    for row in rows:
        detail = dict(part.split("=", 1) for part in row["detail"].split(";"))
        assert detail["source"] == "import" and detail["file_sha256"] == sha
        ev = events[detail["event_id"]]
        assert ev["consilium_decision_import_id"] == detail["import_id"]
        assert row["patient_id"] == get_conn().execute(
            "SELECT h.patient_id FROM history_entries h WHERE h.id = ?", (ev["history_entry_id"],)).fetchone()[0]
        assert row["actor_user_id"] == _dev_id() and row["created_at"]
        import_ids.add(detail["import_id"])
    assert len(import_ids) == 1
    committed = get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'import_committed' AND result = 'ok' ORDER BY id DESC").fetchone()[0]
    assert f"import_id={import_ids.pop()}" in committed and "imported_decisions=3" in committed
    text = json.dumps([dict(r) for r in get_conn().execute("SELECT * FROM audit_events")], ensure_ascii=False)
    for secret in ("treatment_strategy", "dispensary_observation", "Импортов", "C34", "+7"):
        assert secret not in text


def test_i07_audit_failure_rolls_the_whole_import_back(auth_client):
    raw = _doc(_file_patient("Импортов Сбой", decisions=("treatment_strategy_defined", "dispensary_observation")),
               _file_patient("Импортов Сбой Второй", decisions=(None,)))
    before = _counts()
    _fail_audit("consilium_decision_imported")
    try:
        r = unsigned_import(auth_client, raw, "add", historical_decisions=2)
        assert r.status_code == 503, r.text
        assert _counts() == before
    finally:
        _restore_audit()


def test_i08_repeated_import_is_audited_separately_and_keeps_earlier_data(auth_client):
    raw = _doc(_file_patient("Импортов Повтор", decisions=("treatment_strategy_defined",)))
    assert unsigned_import(auth_client, raw, "add", historical_decisions=1).status_code == 200
    first = _patient_events("Импортов Повтор")[0]
    assert unsigned_import(auth_client, raw, "add", historical_decisions=1).status_code == 200
    rows = _patient_events("Импортов Повтор")
    assert len(rows) == 2 and first in rows                                        # прежняя строка не изменена
    assert rows[0]["consilium_decision_import_id"] != rows[1]["consilium_decision_import_id"]
    # Обновление тем же (экспортированным) файлом решений не создает и подтверждения не требует.
    r = auth_client.get("/api/export", headers=auth_client.csrf)
    before = _counts()
    upd = auth_client.post("/api/import/commit", json={"raw": r.text, "mode": "update"}, headers=auth_client.csrf)
    assert upd.status_code == 200, upd.text
    assert upd.json()["unchanged"] >= 2 and _counts()["decisions"] == before["decisions"]


def test_i09_several_decisions_need_the_exact_count(auth_client):
    raw = _doc(_file_patient("Импортов Много А", decisions=("treatment_strategy_defined", "treatment_strategy_changed")),
               _file_patient("Импортов Много Б", decisions=("dispensary_observation", None)))
    preview = auth_client.post("/api/import/preview", json={"raw": raw}).json()
    assert preview["historicalDecisions"]["add"]["decisions"] == 3
    assert preview["historicalDecisions"]["update"]["decisions"] == 0      # update решения не создает
    assert unsigned_import(auth_client, raw, "add", historical_decisions=2).status_code == 428
    assert unsigned_import(auth_client, raw, "add", historical_decisions=3).status_code == 200
    decided = [e for n in ("Импортов Много А", "Импортов Много Б") for e in _patient_events(n) if e["consilium_decision"]]
    assert len(decided) == 3 and {e["consilium_decision_source"] for e in decided} == {"import"}


def test_i10_imported_historical_data_is_immutable(auth_client):
    raw = _doc(_file_patient("Импортов Неизменный", decisions=("treatment_strategy_defined",)))
    assert unsigned_import(auth_client, raw, "add", historical_decisions=1).status_code == 200
    ev = _patient_events("Импортов Неизменный")[0]
    conn = get_conn()
    for sql in ("UPDATE events SET consilium_decision_imported_by_user_id = NULL WHERE id = ?",
                "UPDATE events SET consilium_decision_import_id = 'other' WHERE id = ?",
                "UPDATE events SET consilium_decision_by_user_id = consilium_decision_imported_by_user_id WHERE id = ?",
                "UPDATE events SET consilium_decision_source = 'manual' WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_immutable"):
            conn.execute(sql, (ev["id"],))
        conn.rollback()
    entry = ev["history_entry_id"]
    for columns, values in (
        ("consilium_decision, consilium_decision_source", "'treatment_strategy_defined', 'import'"),
        ("consilium_decision, consilium_decision_source", "'treatment_strategy_defined', 'legacy'"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_decision_unattributed"):
            conn.execute(f"INSERT INTO events (id, history_entry_id, seq, {columns}) VALUES ('forged', ?, 99, {values})",
                         (entry,))
        conn.rollback()
    assert _patient_events("Импортов Неизменный")[0] == ev


def test_i11_partially_invalid_file_creates_nothing(auth_client):
    bad = _file_patient("Импортов Ошибочный", decisions=("treatment_strategy_defined",))
    bad["history"][0]["events"][0]["consiliumDecision"] = "unknown_code"
    raw = _doc(_file_patient("Импортов Верный", decisions=("treatment_strategy_defined",)), bad)
    before = _counts()
    r = unsigned_import(auth_client, raw, "add", historical_decisions=2)
    assert r.status_code == 422
    assert _counts() == before


def test_i12_legacy_local_migration_follows_the_same_rule(auth_client):
    raw = _doc(_file_patient("Мигрантов Локальный", decisions=("dispensary_observation",)))
    preview = auth_client.post("/api/migrate/localstorage/preview", json={"raw": raw}).json()
    assert preview["historicalDecisions"]["required"] is True and preview["historicalDecisions"]["decisions"] == 1
    before = _counts()
    r = auth_client.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=auth_client.csrf)
    assert r.status_code == 428 and _counts() == before
    body = {"raw": raw, "mode": "add", "historicalDecisionsConfirmation": {
        "confirmed": True, "fileSha256": preview["historicalDecisions"]["fileSha256"], "mode": "add",
        "action": "import_historical_consilium_decisions", "decisions": 1}}
    r = auth_client.post("/api/migrate/localstorage/commit", json=body, headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    ev = _patient_events("Мигрантов Локальный")[0]
    assert (ev["consilium_decision_source"], ev["consilium_decision_by_user_id"]) == ("import", None)
    assert get_conn().execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'consilium_decision_imported'").fetchone()[0] == 1


# ====================== АРХИВ ======================

def _ok_two_events(client):
    p = _create(client, stage="consult", events=[{"date": "2026-08-20"}, {"date": "2026-08-27"}],
                treatingDoctorUserId=decision_doctor_id())
    return p["id"]


def _delete(client, pid, idx):
    ev = _card(client, pid)["history"][-1]["events"][idx]
    r = client.delete(f"/api/patients/{pid}/events/{ev['id']}?version={ev['version']}", headers=client.csrf)
    assert r.status_code == 200, r.text
    return ev["id"]


def _mark(client, pid, event_id, ack=None, decision="dispensary_observation"):
    _as(client, DECISION_DOCTOR_USERNAME)
    card = _card(client, pid)
    ev = next(e for e in card["history"][-1]["events"] if e["id"] == event_id)
    body = {"decision": decision, "version": ev["version"], "expectedStage": card["stage"],
            "expectedHistoryEntryId": card["history"][-1]["id"]}
    if ack is not None:
        body["acknowledgedArchivedEventIds"] = ack
    r = client.post(f"/api/patients/{pid}/events/{event_id}/consilium-decision", json=body, headers=client.csrf)
    _as(client, DEV_USERNAME)
    return r


def _archived_case(client):
    """A — решение и мягкое удаление; B — активное событие того же этапа ОК."""
    pid = _ok_two_events(client)
    assert mark_decision(client, pid, "treatment_strategy_defined", idx=0).status_code == 200
    a = _delete(client, pid, 0)
    b = _card(client, pid)["history"][-1]["events"][0]["id"]
    return pid, a, b


def test_a11_a15_a16_deleted_event_keeps_decision_author_time_and_source(auth_client):
    pid = _ok_two_events(auth_client)
    assert mark_decision(auth_client, pid, "treatment_strategy_defined", idx=0).status_code == 200
    ev_id = _card(auth_client, pid)["history"][0]["events"][0]["id"]
    before = {c: _row(ev_id)[c] for c in DECISION_COLUMNS}
    _delete(auth_client, pid, 0)
    row = _row(ev_id)
    assert row["deleted_at"] and {c: row[c] for c in DECISION_COLUMNS} == before
    deleted = auth_client.get(f"/api/patients/{pid}/events/deleted").json()["events"][0]
    assert deleted["consiliumDecision"] == "treatment_strategy_defined" and deleted["consiliumDecisionByName"]
    assert "decision=1" in get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'patient_event_deleted' AND patient_id = ?", (pid,)).fetchone()[0]


def test_a13_new_mark_without_acknowledgement_is_refused(auth_client):
    pid, a, b = _archived_case(auth_client)
    archived_before, b_before = _row(a), _row(b)
    r = _mark(auth_client, pid, b)
    assert r.status_code == 409 and r.json()["code"] == "ARCHIVED_DECISION_EXISTS"
    assert r.json()["archivedEventIds"] == [a]
    assert "архиве" in r.json()["detail"] and "не исправляет и не удаляет" in r.json()["detail"]
    assert _row(a) == archived_before and _row(b) == b_before                  # отметка не создана
    assert _mark(auth_client, pid, b, ack=[]).status_code == 409
    assert _mark(auth_client, pid, b, ack=["unrelated"]).status_code == 409


def test_a14_a15_a16_acknowledged_mark_is_recorded_and_the_archive_is_untouched(auth_client):
    pid, a, b = _archived_case(auth_client)
    archived_before = _row(a)
    r = _mark(auth_client, pid, b, ack=[a])
    assert r.status_code == 200, r.text
    assert _row(a) == archived_before                                           # архивное решение, автор, время, источник
    new = _row(b)
    assert (new["consilium_decision"], new["consilium_decision_source"]) == ("dispensary_observation", "manual")
    detail = get_conn().execute(
        "SELECT detail FROM audit_events WHERE event_type = 'consilium_decision_recorded' AND patient_id = ? "
        "ORDER BY id DESC", (pid,)).fetchone()[0]
    assert detail == f"event_id={b};history=0;source=manual;archived_decisions_acknowledged=1;archived_event_ids={a}"
    # Обе записи доступны: новая — в карточке, архивная — в списке удаленных событий.
    assert _card(auth_client, pid)["history"][0]["events"][0]["consiliumDecision"] == "dispensary_observation"
    assert auth_client.get(f"/api/patients/{pid}/events/deleted").json()["events"][0]["consiliumDecision"] == \
        "treatment_strategy_defined"
    assert _card(auth_client, pid)["stage"] == "consult"                     # этап не переведен
    assert new["actual_date"] is None                                           # факт не создан


def test_a12_after_restore_no_acknowledgement_is_needed(auth_client):
    pid, a, b = _archived_case(auth_client)
    deleted = auth_client.get(f"/api/patients/{pid}/events/deleted").json()["events"][0]
    assert auth_client.post(f"/api/patients/{pid}/events/{a}/restore", json={"version": deleted["version"]},
                            headers=auth_client.csrf).status_code == 200
    assert _row(a)["deleted_at"] is None and _row(a)["consilium_decision"] == "treatment_strategy_defined"
    assert _mark(auth_client, pid, b).status_code == 200                       # оба решения активны и видны


def test_a17_only_the_current_route_entry_is_considered(auth_client):
    pid = _ok_two_events(auth_client)
    assert mark_decision(auth_client, pid, "treatment_strategy_defined", idx=0).status_code == 200
    _delete(auth_client, pid, 0)
    # Пациент уходит с ОК и возвращается: архивное решение прошлого посещения не требует подтверждения.
    for target in ("exam", "consult"):
        assert auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {
            "newStage": target, "lastEventUpdate": None}), headers=auth_client.csrf).status_code == 200
    assert auth_client.post(f"/api/patients/{pid}/history/2/events", json={"expectedEventIds": []},
                            headers=auth_client.csrf).status_code == 200
    new_event = _card(auth_client, pid)["history"][2]["events"][0]["id"]
    assert _mark(auth_client, pid, new_event).status_code == 200


def test_a18_concurrent_deletion_between_warning_and_mark_is_detected(auth_client):
    pid = _create(auth_client, stage="consult", events=[{"date": "2026-08-20"}, {"date": "2026-08-27"},
                                                         {"date": "2026-09-03"}],
                  treatingDoctorUserId=decision_doctor_id())["id"]
    for idx in (0, 1):
        assert mark_decision(auth_client, pid, "treatment_strategy_defined", idx=idx).status_code == 200
    a = _delete(auth_client, pid, 0)
    c = _card(auth_client, pid)["history"][0]["events"][-1]["id"]
    first = _mark(auth_client, pid, c)
    assert first.json()["archivedEventIds"] == [a]
    second_deleted = _delete(auth_client, pid, 0)          # другой сотрудник удаляет еще одно событие с решением
    r = _mark(auth_client, pid, c, ack=[a])                 # подтверждение устаревшего набора
    assert r.status_code == 409 and sorted(r.json()["archivedEventIds"]) == sorted([a, second_deleted])
    assert _row(c)["consilium_decision"] is None
    assert _mark(auth_client, pid, c, ack=[a, second_deleted]).status_code == 200


def test_a18_thread_race_never_records_an_unacknowledged_mark_after_deletion(auth_client):
    from backend.app import store

    pid = _ok_two_events(auth_client)
    assert mark_decision(auth_client, pid, "treatment_strategy_defined", idx=0).status_code == 200
    card = _card(auth_client, pid)
    a, b = card["history"][0]["events"]
    path = get_conn().execute("PRAGMA database_list").fetchone()["file"]
    results, barrier = {}, threading.Barrier(2)

    def delete():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results["delete"] = store.delete_event(conn, pid, a["id"], a["version"], _dev_id()) is not None
        finally:
            conn.close()

    def mark():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results["mark"] = store.mark_consilium_decision(
                conn, pid, b["id"], "dispensary_observation", b["version"], expected_stage="consult",
                expected_history_entry_id=card["history"][0]["id"], user_id=decision_doctor_id()) is not None
        except store.DecisionConflict as e:
            results["mark"] = e.code
        finally:
            conn.close()

    threads = [threading.Thread(target=delete), threading.Thread(target=mark)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results["delete"] is True
    b_row, a_row = _row(b["id"]), _row(a["id"])
    if results["mark"] is True:
        # Отметка прошла без подтверждения — значит, на момент ее транзакции событие A еще не было удалено.
        assert b_row["version"] == b["version"] + 1 and a_row["deleted_at"]
    else:
        assert results["mark"] == "ARCHIVED_DECISION_EXISTS" and b_row["consilium_decision"] is None


def test_a19_repeated_request_does_not_create_a_second_mark(auth_client):
    pid, a, b = _archived_case(auth_client)
    _as(auth_client, DECISION_DOCTOR_USERNAME)
    card = _card(auth_client, pid)
    ev = next(e for e in card["history"][0]["events"] if e["id"] == b)
    body = {"decision": "dispensary_observation", "version": ev["version"], "expectedStage": "consult",
            "expectedHistoryEntryId": card["history"][0]["id"], "acknowledgedArchivedEventIds": [a]}
    url = f"/api/patients/{pid}/events/{b}/consilium-decision"
    assert auth_client.post(url, json=body, headers=auth_client.csrf).status_code == 200
    first = _row(b)
    r = auth_client.post(url, json=body, headers=auth_client.csrf)
    assert r.status_code == 409 and r.json()["code"] == "DECISION_ALREADY_RECORDED"
    assert _row(b) == first


def test_a20_audit_failure_rolls_the_acknowledged_mark_back(auth_client):
    pid, a, b = _archived_case(auth_client)
    archived, b_before = _row(a), _row(b)
    _fail_audit("consilium_decision_recorded")
    try:
        r = _mark(auth_client, pid, b, ack=[a])
        assert r.status_code == 503
        assert _row(a) == archived and _row(b) == b_before
    finally:
        _restore_audit()


# ====================== ОБЩИЕ РЕГРЕССИИ ======================

def test_g22_migration_leaves_import_columns_empty_for_legacy(tmp_path):
    from backend.app import db
    from .test_wp22b_consilium_decision import _v20_db
    conn = sqlite3.connect(str(_v20_db(tmp_path / "v20.db")))
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM events WHERE consilium_decision IS NOT NULL")]
    assert rows and all(r["consilium_decision_source"] == "legacy" and r["consilium_decision_imported_by_user_id"] is None
                        and r["consilium_decision_imported_at"] is None and r["consilium_decision_import_id"] is None
                        for r in rows)
    conn.close()


def test_g26_export_still_excludes_decision_and_import_metadata(auth_client):
    raw = _doc(_file_patient("Импортов Экспорт", decisions=("treatment_strategy_defined",)))
    assert unsigned_import(auth_client, raw, "add", historical_decisions=1).status_code == 200
    exported = json.loads(auth_client.get("/api/export", headers=auth_client.csrf).text)
    ev = next(p for p in exported["patients"] if p["name"] == "Импортов Экспорт")["history"][0]["events"][0]
    assert ev["consiliumDecision"] == "treatment_strategy_defined"
    assert not [k for k in ev if k.startswith("consiliumDecision") and k != "consiliumDecision"]


def test_g27_static_app_handles_archive_acknowledgement_and_historical_import():
    source = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    start = source.index("    async markConsiliumDecision(")
    mark = source[start:source.index("\n    },\n", start)]
    assert "ARCHIVED_DECISION_EXISTS" in mark and "acknowledgedArchivedEventIds = archived" in mark
    assert "ui.confirm" in mark and "if (!acknowledged) return false;" in mark
    assert "err.data = data" in source
    assert "historicalDecisionsConfirmation" in source and "import_historical_consilium_decisions" in source
    assert 'id="importHistoricalDecisionsConfirm"' in html
    for text in ("архиве", "решения ОК"):
        assert text in mark or text in source
    # Собственные новые тексты — через «е».
    for fragment in ("В архиве этапа есть решение ОК", "Подтверждаю, отметить новое решение",
                     "Историческое решение перенесено из файла импорта",
                     "Перенести исторические решения ОК из этого файла"):
        assert fragment in source or fragment in html
        assert "ё" not in fragment
