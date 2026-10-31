"""Администрирование: сотрудники, отделения, пароли, сессии.
Все имена вымышленные."""
import pytest

from .conftest import TEST_PASSWORD, create_account, create_department, get_conn, login


def test_developer_creates_user_via_api(auth_client):
    r = auth_client.post("/api/users", json={
        "username": "new.doctor",
        "fullName": "Новёхонький Врач Тестович",
        "roleId": "doctor",
        "password": "a-strong-temp-pass-1",
        "mustChangePassword": True,
        "departmentIds": [],
    }, headers=auth_client.csrf)
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "new.doctor"
    assert body["roleId"] == "doctor"
    assert body["mustChangePassword"] is True
    # пароль и хеш не возвращаются
    assert "password" not in body
    assert "passwordHash" not in body


def test_user_list_filters(auth_client):
    dept = create_department("Фильтров Отделение")
    create_account("filter.doc", "doctor", "Фильтруев Врач", department_ids=[dept])
    create_account("filter.head", "department_head", "Фильтруев Зав", department_ids=[dept])

    r = auth_client.get("/api/users?role=doctor")
    usernames = {u["username"] for u in r.json()["users"]}
    assert "filter.doc" in usernames and "filter.head" not in usernames

    r = auth_client.get("/api/users?query=Фильтруев")
    assert len(r.json()["users"]) == 2

    r = auth_client.get(f"/api/users?department={dept}")
    usernames = {u["username"] for u in r.json()["users"]}
    assert usernames == {"filter.doc", "filter.head"}


def test_duplicate_username_rejected(auth_client):
    create_account("taken.name", "doctor", "Первый Занявший")
    r = auth_client.post("/api/users", json={
        "username": "TAKEN.NAME",  # регистронезависимо
        "fullName": "Второй Претендент",
        "roleId": "doctor",
        "password": "another-strong-pass-2",
    }, headers=auth_client.csrf)
    assert r.status_code == 422


def test_weak_password_rejected_on_create_and_reset(auth_client):
    r = auth_client.post("/api/users", json={
        "username": "weak.pass",
        "fullName": "Слабопарольный Тест",
        "roleId": "doctor",
        "password": "short",
    }, headers=auth_client.csrf)
    assert r.status_code == 422

    uid = create_account("reset.me", "doctor", "Сбросов Пароль")
    r = auth_client.post(f"/api/users/{uid}/reset-password",
                         json={"newPassword": "password"}, headers=auth_client.csrf)
    assert r.status_code == 422


def test_password_reset_terminates_sessions_and_requires_change(client):
    create_account("dev.admin", "developer", "Разработчиков Админ")
    uid = create_account("reset.target", "doctor", "Сбросов Цель")

    # у цели есть активная сессия
    login(client, "reset.target")
    target_cookies = dict(client.cookies)

    login(client, "dev.admin")
    r = client.post(f"/api/users/{uid}/reset-password",
                    json={"newPassword": "fresh-temp-password-3", "mustChangePassword": True},
                    headers=client.csrf)
    assert r.status_code == 200

    # старая сессия завершена
    client.cookies.clear()
    for k, v in target_cookies.items():
        client.cookies.set(k, v)
    assert client.get("/api/patients").status_code == 401

    # вход по новому паролю → принудительная смена
    client.cookies.clear()
    r = login(client, "reset.target", "fresh-temp-password-3")
    assert r.status_code == 200
    assert r.json()["user"]["mustChangePassword"] is True
    assert client.get("/api/patients").status_code == 403


def test_role_change_terminates_sessions(client):
    create_account("dev.admin", "developer", "Разработчиков Админ")
    uid = create_account("role.target", "doctor", "Ролев Цель")
    login(client, "role.target")
    target_cookies = dict(client.cookies)

    login(client, "dev.admin")
    r = client.patch(f"/api/users/{uid}", json={"roleId": "department_head"}, headers=client.csrf)
    assert r.status_code == 200
    assert r.json()["roleId"] == "department_head"

    client.cookies.clear()
    for k, v in target_cookies.items():
        client.cookies.set(k, v)
    assert client.get("/api/patients").status_code == 401


def test_terminate_sessions_endpoint(client):
    create_account("dev.admin", "developer", "Разработчиков Админ")
    uid = create_account("term.target", "doctor", "Сессиев Цель")
    login(client, "term.target")
    target_cookies = dict(client.cookies)

    # A different administrative device, not an identity switch on the target's
    # browser (which now correctly revokes that browser session immediately).
    from fastapi.testclient import TestClient
    admin = TestClient(client.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    try:
        login(admin, "dev.admin")
        r = admin.post(f"/api/users/{uid}/terminate-sessions", headers=admin.csrf)
        assert r.status_code == 200
        assert r.json()["terminated"] == 1
    finally:
        admin.close()

    client.cookies.clear()
    for k, v in target_cookies.items():
        client.cookies.set(k, v)
    assert client.get("/api/patients").status_code == 401


def test_department_lifecycle(auth_client):
    # создать
    r = auth_client.post("/api/departments", json={"name": "Жизненный Цикл"}, headers=auth_client.csrf)
    assert r.status_code == 201
    dept_id = r.json()["id"]
    # дубликат имени отклоняется
    assert auth_client.post("/api/departments", json={"name": "Жизненный Цикл"},
                            headers=auth_client.csrf).status_code == 422
    # переименовать
    r = auth_client.patch(f"/api/departments/{dept_id}",
                          json={"name": "Цикл Переименованный"}, headers=auth_client.csrf)
    assert r.json()["name"] == "Цикл Переименованный"
    # назначить заведующего
    head_id = create_account("cycle.head", "department_head", "Циклов Зав")
    r = auth_client.patch(f"/api/departments/{dept_id}",
                          json={"headUserId": head_id}, headers=auth_client.csrf)
    assert [h["id"] for h in r.json()["heads"]] == [head_id]
    # добавить врача
    doc_id = create_account("cycle.doc", "doctor", "Циклов Врач")
    r = auth_client.post(f"/api/departments/{dept_id}/doctors",
                         json={"userId": doc_id}, headers=auth_client.csrf)
    assert r.json()["doctorCount"] == 1
    # перевод: удалить из этого отделения, добавить в другое
    other = auth_client.post("/api/departments", json={"name": "Другое Отделение"},
                             headers=auth_client.csrf).json()["id"]
    assert auth_client.delete(f"/api/departments/{dept_id}/doctors/{doc_id}",
                              headers=auth_client.csrf).status_code == 200
    r = auth_client.post(f"/api/departments/{other}/doctors",
                         json={"userId": doc_id}, headers=auth_client.csrf)
    assert r.json()["doctorCount"] == 1
    # физического удаления нет — только архив
    r = auth_client.delete(f"/api/departments/{dept_id}", headers=auth_client.csrf)
    assert r.status_code == 200 and r.json() == {"archived": True}
    from backend.app import users as users_mod
    assert users_mod.get_department(get_conn(), dept_id)["status"] == "archived"


def test_archived_department_leaves_head_scope(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    from .conftest import make_patient_payload
    dept = create_department("Архивируемое")
    create_account("arch.head", "department_head", "Архивов Зав", department_ids=[dept])
    doc_id = create_account("arch.doc", "doctor", "Архивов Врач", department_ids=[dept])
    conn = get_conn()
    store.create_patient(conn, PatientCreateIn(**make_patient_payload(name="Архивов Пациент")), doc_id)

    login(client, "arch.head")
    assert {p["name"] for p in client.get("/api/patients").json()} == {"Архивов Пациент"}

    create_account("dev.arch", "developer", "Разработчиков Архив")
    login(client, "dev.arch")
    client.delete(f"/api/departments/{dept}", headers=client.csrf)

    login(client, "arch.head")
    assert client.get("/api/patients").json() == []


def test_head_assignment_requires_head_role(auth_client):
    dept = create_department("Строгое Отделение")
    doc_id = create_account("not.head", "doctor", "Неглавный Врач")
    r = auth_client.patch(f"/api/departments/{dept}",
                          json={"headUserId": doc_id}, headers=auth_client.csrf)
    assert r.status_code == 422


def test_admin_actions_recorded_in_audit(auth_client):
    uid = create_account("audit.target", "doctor", "Аудитов Цель")
    auth_client.patch(f"/api/users/{uid}", json={"status": "blocked"}, headers=auth_client.csrf)
    auth_client.post("/api/departments", json={"name": "Аудитово"}, headers=auth_client.csrf)
    events = auth_client.get("/api/audit?limit=200").json()["events"]
    types = {e["event_type"] for e in events}
    assert {"user_blocked", "user_updated", "department_created"} <= types
    blocked = next(e for e in events if e["event_type"] == "user_blocked")
    assert blocked["target_user_id"] == uid
    assert blocked["actor_role"] == "developer"
    # ФИО в аудите нет
    import json as _json
    text = _json.dumps(events, ensure_ascii=False)
    assert "Аудитов Цель" not in text


def test_password_never_returned_by_users_api(auth_client):
    create_account("secret.holder", "doctor", "Секретов Хранитель")
    text = auth_client.get("/api/users").text.lower()
    assert "argon2" not in text
    assert "password_hash" not in text
    assert "passwordhash" not in text
