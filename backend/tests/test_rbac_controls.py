"""Контрольные проверки перед approve PR: старый импорт, перевод врача,
полная матрица административных endpoints, CLI import-structure.
Все имена вымышленные."""
import json

import pytest

from .conftest import (
    TEST_PASSWORD,
    create_account,
    create_department,
    get_conn,
    login,
    make_patient_payload,
)


# --- 4. Старый импорт (v0/v1) без treatingDoctorUserId --------------------

@pytest.mark.parametrize("wrap", ["v0_bare_array", "v1_document"])
def test_legacy_import_without_doctor_id_becomes_unresolved(auth_client, wrap):
    """Импорт старого формата проходит; записи без ID врача становятся
    unresolved; автоматического сопоставления по ФИО нет; данные не теряются."""
    from backend.app import users
    # Врач с ТЕМ ЖЕ ФИО, что в legacy-тексте записи — соблазн для
    # автосопоставления, которого быть не должно.
    users.create_user(get_conn(), username="same.name", full_name="Совпадаев Врач Полностью",
                      role_id="doctor", password=TEST_PASSWORD)

    record = make_patient_payload(name="Легасиев Импортёр", doctor="Совпадаев Врач Полностью")
    raw = (json.dumps([record], ensure_ascii=False) if wrap == "v0_bare_array"
           else json.dumps({"schemaVersion": 1, "patients": [record]}, ensure_ascii=False))

    r = auth_client.post("/api/import/commit", json={"raw": raw, "mode": "add"},
                         headers=auth_client.csrf)
    assert r.status_code == 200 and r.json()["added"] == 1

    imported = next(p for p in auth_client.get("/api/patients").json()
                    if p["name"] == "Легасиев Импортёр")
    # данные не потеряны
    assert imported["diagnosis"] == record["diagnosis"]
    assert imported["doctor"] == "Совпадаев Врач Полностью"  # legacy-текст сохранён
    # автосопоставления по ФИО не произошло — пациент unresolved
    assert imported["treatingDoctorUserId"] is None
    unresolved = auth_client.get("/api/patients/unresolved").json()["patients"]
    assert any(p["name"] == "Легасиев Импортёр" for p in unresolved)


# --- 3. Перевод врача: scope заведующих пересчитывается немедленно --------

def test_doctor_transfer_moves_head_scope_immediately(client):
    from backend.app import store, users
    from backend.app.models import PatientCreateIn
    dept_a = create_department("Переводное А")
    dept_b = create_department("Переводное Б")
    create_account("tr.head.a", "department_head", "Переводов Зав А", department_ids=[dept_a])
    create_account("tr.head.b", "department_head", "Переводов Зав Б", department_ids=[dept_b])
    doc = create_account("tr.doc", "doctor", "Переводов Врач", department_ids=[dept_a])
    conn = get_conn()
    store.create_patient(conn, PatientCreateIn(**make_patient_payload(name="Переводов Пациент")), doc)

    login(client, "tr.head.a")
    assert {p["name"] for p in client.get("/api/patients").json()} == {"Переводов Пациент"}
    login(client, "tr.head.b")
    assert client.get("/api/patients").json() == []

    # перевод врача: А → Б (текущие связи, история не хранится)
    users.remove_doctor_from_department(conn, doc, dept_a)
    users.add_doctor_to_department(conn, doc, dept_b)

    login(client, "tr.head.a")
    assert client.get("/api/patients").json() == []
    login(client, "tr.head.b")
    assert {p["name"] for p in client.get("/api/patients").json()} == {"Переводов Пациент"}
    # сам врач видит пациента независимо от отделения
    login(client, "tr.doc")
    assert {p["name"] for p in client.get("/api/patients").json()} == {"Переводов Пациент"}


# --- 5. Матрица административных endpoints --------------------------------

NON_DEVELOPER_ROLES = ["deputy_chief_oncology", "caop_head", "department_head", "doctor"]


@pytest.fixture()
def admin_targets(client):
    """Готовые цели для административных вызовов + учётные записи всех ролей."""
    dept = create_department("Админ-Матрица")
    # Отдельные цели: смена роли одной не должна ломать membership-вызовы другой.
    target = create_account("matrix.target", "doctor", "Матрицын Цель", department_ids=[dept])
    doc_target = create_account("matrix.doctarget", "doctor", "Матрицын Врач-Цель")
    create_account("matrix.dev", "developer", "Матрицын Разработчик")
    for role in NON_DEVELOPER_ROLES:
        create_account(
            f"matrix.{role}", role, f"Матрицын {role}",
            department_ids=[dept] if role in ("department_head", "doctor") else None,
        )
    from backend.app import store
    from backend.app.models import PatientCreateIn
    unresolved_pid = store.create_patient(
        get_conn(), PatientCreateIn(**make_patient_payload(name="Матрицын Несопоставленный")), None
    )
    return {"dept": dept, "target": target, "doc_target": doc_target,
            "unresolved_pid": unresolved_pid}


def _admin_calls(client, t):
    """(описание, вызов) для каждого административного endpoint."""
    csrf = client.csrf
    return [
        ("staff: list", lambda: client.get("/api/users")),
        ("staff: create", lambda: client.post("/api/users", json={
            "username": "matrix.new", "fullName": "Матрицын Новый", "roleId": "doctor",
            "password": "matrix-pass-strong-1"}, headers=csrf)),
        ("staff: update/role", lambda: client.patch(
            f"/api/users/{t['target']}", json={"roleId": "department_head"}, headers=csrf)),
        ("staff: reset password", lambda: client.post(
            f"/api/users/{t['target']}/reset-password",
            json={"newPassword": "matrix-pass-strong-2"}, headers=csrf)),
        ("staff: terminate sessions", lambda: client.post(
            f"/api/users/{t['target']}/terminate-sessions", headers=csrf)),
        ("structure: list", lambda: client.get("/api/departments")),
        ("structure: create", lambda: client.post(
            "/api/departments", json={"name": "Матрица-Новое"}, headers=csrf)),
        ("structure: update/head", lambda: client.patch(
            f"/api/departments/{t['dept']}", json={"name": "Матрица-Переименовано"}, headers=csrf)),
        ("membership: add doctor", lambda: client.post(
            f"/api/departments/{t['dept']}/doctors", json={"userId": t["doc_target"]}, headers=csrf)),
        ("membership: remove doctor", lambda: client.delete(
            f"/api/departments/{t['dept']}/doctors/{t['doc_target']}", headers=csrf)),
        ("unresolved: list", lambda: client.get("/api/patients/unresolved")),
        ("unresolved: assign doctor", lambda: client.put(
            f"/api/patients/{t['unresolved_pid']}/treating-doctor",
            json={"userId": t["doc_target"]}, headers=csrf)),
        # архивирование — последним: после него membership-вызовы невозможны
        ("structure: archive", lambda: client.delete(f"/api/departments/{t['dept']}", headers=csrf)),
    ]


@pytest.mark.parametrize("role", NON_DEVELOPER_ROLES)
def test_admin_endpoints_forbidden_for_non_developers(client, admin_targets, role):
    login(client, f"matrix.{role}")
    for label, call in _admin_calls(client, admin_targets):
        status = call().status_code
        # 403 — прямой отказ; 404 — объект вне scope (унифицированный ответ,
        # существование не раскрывается). Успехов быть не должно.
        assert status in (403, 404), f"{role}: {label} -> {status}"


def test_admin_endpoints_allowed_for_developer(client, admin_targets):
    login(client, "matrix.dev")
    for label, call in _admin_calls(client, admin_targets):
        status = call().status_code
        assert status in (200, 201), f"developer: {label} -> {status}"


# --- 6. CLI import-structure ----------------------------------------------

def test_cli_import_structure(client, tmp_path, monkeypatch):
    """Команда существует и создаёт отделения, заведующих и врачей из
    локального JSON; пользователи получают must_change_password."""
    from backend import cli
    from backend.app import users

    structure = {
        "departments": [
            {
                "name": "CLI-Отделение",
                "head": {"username": "cli.head", "fullName": "Клиев Заведующий"},
                "doctors": [
                    {"username": "cli.doc1", "fullName": "Клиев Врач Первый"},
                    {"username": "cli.doc2", "fullName": "Клиев Врач Второй"},
                ],
            }
        ],
        "users": [{"username": "cli.solo", "fullName": "Клиев Одиночный", "role": "doctor"}],
    }
    path = tmp_path / "structure.json"
    path.write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "cli-temp-password-1")
    rc = cli.cmd_import_structure(type("Args", (), {"file": str(path)})())
    assert rc == 0

    conn = get_conn()
    dept = conn.execute("SELECT id FROM departments WHERE name = 'CLI-Отделение'").fetchone()
    assert dept is not None
    head = users.get_user_by_username(conn, "cli.head")
    assert head["role_id"] == "department_head"
    assert bool(head["must_change_password"]) is True
    assert users.managed_department_ids(conn, head["id"]) == [dept["id"]]
    for username in ("cli.doc1", "cli.doc2"):
        doc = users.get_user_by_username(conn, username)
        assert doc["role_id"] == "doctor"
        assert users.doctor_department_ids(conn, doc["id"]) == [dept["id"]]
    assert users.get_user_by_username(conn, "cli.solo") is not None

    # повторный запуск идемпотентен по пользователям и отделениям
    rc = cli.cmd_import_structure(type("Args", (), {"file": str(path)})())
    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM departments WHERE name = 'CLI-Отделение'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM users WHERE username = 'cli.doc1'").fetchone()[0] == 1
