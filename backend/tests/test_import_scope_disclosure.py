"""WP2.1, F9 и решение B: импорт не раскрывает состояние карточек вне области
доступа, а файлы с внутренними идентификаторами системы импортирует только
разработчик.

Врач и заведующий получают одинаковый отказ для файла с id своей, чужой,
несуществующей карточки или удалённого чужого события — до любых обращений к
базе. Новые пациенты из внешних файлов без id добавляются по-прежнему.
Только синтетические данные."""
import json

from .conftest import (card_version,
    DEV_USERNAME,
    create_account,
    create_department,
    get_conn,
    login,
    make_patient_payload,
    unsigned_import,
)


def _setup(client):
    dept_a = create_department("Область А")
    dept_b = create_department("Область Б")
    create_account(DEV_USERNAME)
    ids = {
        "dept_a": dept_a,
        "doc_a": create_account("scope.doc.a", "doctor", department_ids=[dept_a]),
        "doc_b": create_account("scope.doc.b", "doctor", department_ids=[dept_b]),
        "head_a": create_account("scope.head.a", "department_head", department_ids=[dept_a]),
    }
    create_account("scope.deputy", "deputy_chief_oncology")
    create_account("scope.caop", "caop_head")
    login(client, "scope.doc.a")
    r = client.post("/api/patients", json=make_patient_payload(name="Областной Пациент",
                    events=[{"date": "2030-01-10"}, {"date": "2030-02-10"}]), headers=client.csrf)
    assert r.status_code == 201, r.text
    ids["pid"] = r.json()["id"]
    assert client.get(f"/api/patients/{ids['pid']}").status_code == 200       # свой пациент виден
    ids["signed"] = client.get("/api/export", headers=client.csrf).text        # своя подписанная выгрузка
    return ids


def _move_to_b(client, ids):
    login(client, DEV_USERNAME)
    r = client.put(f"/api/patients/{ids['pid']}/treating-doctor", json={"expectedVersion": card_version(ids['pid']), "userId": ids["doc_b"]}, headers=client.csrf)
    assert r.status_code == 200, r.text


def _external(name="Внешний Пациент", **overrides):
    """Внешний файл без внутренних id системы."""
    record = make_patient_payload(name=name, **overrides)
    record.pop("id", None)
    return json.dumps({"schemaVersion": 20, "patients": [record]}, ensure_ascii=False)


def _with_patient_id(record_id):
    record = make_patient_payload(name="Файл С Идентификатором")
    record["id"] = record_id
    return json.dumps({"schemaVersion": 20, "patients": [record]}, ensure_ascii=False)


def _with_event_id(event_id):
    record = make_patient_payload(name="Файл С Событием")
    record["history"] = [{"stage": "exam", "enteredAt": "2030-01-01T00:00:00+00:00", "checklist": {}, "note": "",
                          "events": [{"id": event_id, "date": "2030-01-10"}]}]
    return json.dumps({"schemaVersion": 20, "patients": [record]}, ensure_ascii=False)


def _preview(client, raw):
    r = client.post("/api/import/preview", json={"raw": raw}, headers=client.csrf)
    assert r.status_code == 200, r.text
    return r.json()


def _patients_count():
    return get_conn().execute("SELECT COUNT(*) FROM patients").fetchone()[0]


def _without_file_fields(body):
    return {k: v for k, v in body.items() if k != "provenance"}


# 17–18. Внешние файлы без id ------------------------------------------------

def test_doctor_and_head_import_new_patients_from_files_without_ids(client):
    ids = _setup(client)
    login(client, "scope.doc.a")
    raw = _external("Новый От Врача")
    assert _preview(client, raw)["valid"] is True
    r = unsigned_import(client, raw, "add")
    assert r.status_code == 200 and r.json()["added"] == 1
    login(client, "scope.head.a")
    raw = _external("Новый От Заведующего", treatingDoctorUserId=ids["doc_a"])
    assert unsigned_import(client, raw, "add").status_code == 200
    names = [r[0] for r in get_conn().execute("SELECT name FROM patients")]
    assert "Новый От Врача" in names and "Новый От Заведующего" in names


# 19–28. Файлы с внутренними id ----------------------------------------------

def test_files_with_internal_ids_get_one_answer_regardless_of_database_state(client):
    ids = _setup(client)
    # Удалённое событие чужой карточки.
    _move_to_b(client, ids)
    login(client, "scope.doc.b")
    event = client.get(f"/api/patients/{ids['pid']}").json()["history"][0]["events"][0]
    assert client.delete(f"/api/patients/{ids['pid']}/events/{event['id']}?version={event['version']}",
                         headers=client.csrf).status_code == 200
    login(client, "scope.doc.a")
    own = client.post("/api/patients", json=make_patient_payload(name="Свой"), headers=client.csrf).json()["id"]
    stripped = {k: v for k, v in json.loads(ids["signed"]).items() if k not in ("format", "formatVersion", "signature")}
    files = {
        "signed_with_ids": ids["signed"],                                   # 19
        "unsigned_with_ids": json.dumps(stripped, ensure_ascii=False),      # 20, 22
        "own_id": _with_patient_id(own),                                    # 24
        "foreign_id": _with_patient_id(ids["pid"]),                         # 25
        "missing_id": _with_patient_id("0" * 32),                           # 26
        "deleted_foreign_event": _with_event_id(event["id"]),               # 27
        "missing_event": _with_event_id("1" * 32),
    }
    for username in ("scope.doc.a", "scope.head.a"):                       # 21
        login(client, username)
        previews, commits = [], []
        for name, raw in files.items():
            preview = _preview(client, raw)
            assert preview["valid"] is False and preview["code"] == "internal_ids_forbidden", (username, name)
            previews.append(_without_file_fields(preview))
            before = _patients_count()
            r = unsigned_import(client, raw, "add")                         # 23: подтверждение не помогает
            assert r.status_code == 403, (username, name)
            commits.append((r.status_code, r.json()))
            assert _patients_count() == before
        assert client.post("/api/import/commit", json={"raw": files["signed_with_ids"], "mode": "add"},
                           headers=client.csrf).status_code == 403
        # 28. Ответы не различаются по состоянию базы.
        assert all(p == previews[0] for p in previews), username
        assert all(c == commits[0] for c in commits), username
        text = json.dumps(previews + [c[1] for c in commits], ensure_ascii=False)
        for secret in (ids["pid"], event["id"], "удалённ", "недоступн", "уже есть"):
            assert secret not in text, secret
    deleted = get_conn().execute("SELECT deleted_at FROM events WHERE id = ?", (event["id"],)).fetchone()
    assert deleted["deleted_at"]
    active_copies = get_conn().execute(
        "SELECT COUNT(*) FROM events WHERE date = '2030-01-10' AND deleted_at IS NULL").fetchone()[0]
    assert active_copies == 0


def test_read_only_roles_are_refused(client):
    ids = _setup(client)
    for username in ("scope.deputy", "scope.caop"):                         # 29
        login(client, username)
        for raw in (ids["signed"], _external()):
            assert client.post("/api/import/commit", json={"raw": raw, "mode": "add"},
                               headers=client.csrf).status_code == 403, username
            assert unsigned_import(client, raw, "add").status_code == 403, username


def test_developer_imports_files_with_ids_within_all_checks(client):
    ids = _setup(client)
    login(client, DEV_USERNAME)
    raw = _with_patient_id("2" * 32)                                        # 30
    assert _preview(client, raw)["valid"] is True
    r = unsigned_import(client, raw, "add")
    assert r.status_code == 200 and r.json()["added"] == 1
    # Неподписанный id не сохраняется: карточка получает новый id.
    assert get_conn().execute("SELECT COUNT(*) FROM patients WHERE id = ?", ("2" * 32,)).fetchone()[0] == 0
    # Своя подписанная выгрузка в add — повторный импорт (полная диагностика у разработчика).
    preview = _preview(client, ids["signed"])
    assert preview["duplicateImpact"]["add"]["blocked"] is True
    assert client.post("/api/import/commit", json={"raw": ids["signed"], "mode": "add"},
                       headers=client.csrf).status_code == 409


def test_role_change_between_preview_and_commit_is_enforced(client):
    ids = _setup(client)
    login(client, DEV_USERNAME)
    raw = _with_patient_id("3" * 32)
    assert _preview(client, raw)["valid"] is True                           # 31
    dev_id = get_conn().execute("SELECT id FROM users WHERE username = ?", (DEV_USERNAME,)).fetchone()["id"]
    with get_conn() as conn:
        conn.execute("UPDATE users SET role_id = 'department_head' WHERE id = ?", (dev_id,))
        conn.execute("INSERT INTO user_departments (user_id, department_id, created_at) VALUES (?, ?, ?)",
                     (dev_id, ids["dept_a"], "2030-01-01T00:00:00+00:00"))
    before = _patients_count()
    r = unsigned_import(client, raw, "add")
    assert r.status_code in (401, 403)
    assert _patients_count() == before


def test_no_bypass_through_the_local_data_migration_route(client):
    _setup(client)
    login(client, "scope.doc.a")
    raw = _with_patient_id("4" * 32)                                        # 32
    preview = client.post("/api/migrate/localstorage/preview", json={"raw": raw}, headers=client.csrf).json()
    assert preview["valid"] is False and preview["code"] == "internal_ids_forbidden"
    r = client.post("/api/migrate/localstorage/commit", json={"raw": raw, "mode": "add"}, headers=client.csrf)
    assert r.status_code == 403


# F9 для разработчика: подробности только при глобальной области ----------------

def test_developer_keeps_full_diagnostics_for_every_patient(client):
    ids = _setup(client)
    _move_to_b(client, ids)
    login(client, "scope.doc.b")
    event = client.get(f"/api/patients/{ids['pid']}").json()["history"][0]["events"][0]
    client.delete(f"/api/patients/{ids['pid']}/events/{event['id']}?version={event['version']}", headers=client.csrf)
    login(client, DEV_USERNAME)
    preview = _preview(client, ids["signed"])
    assert preview["archivedEventsImpact"]["add"]["events"] == 1
    assert preview["duplicateImpact"]["add"]["blocked"] is True
    assert preview["accessImpact"]["add"]["blocked"] is False
    assert preview["existingMatches"] == 1
