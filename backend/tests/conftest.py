import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_PASSWORD = "correct-horse-battery"

# Вымышленные сотрудники для тестов — реальные ФИО в fixtures не используются.
DEV_USERNAME = "dev.tester"
DEV_FULL_NAME = "Разработчиков Тест Системович"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with an isolated temp database per test."""
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))

    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import security

    security._failed_attempts.clear()
    security._request_attempts.clear()

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c


def get_conn():
    """Соединение с temp-базой текущего клиента (после старта приложения)."""
    from backend.app.db import get_connection
    return get_connection()


def create_account(
    username: str,
    role_id: str = "developer",
    full_name: str = "Сотрудников Тест Тестович",
    password: str = TEST_PASSWORD,
    department_ids: list | None = None,
    must_change_password: bool = False,
) -> str:
    """Создать учётную запись напрямую в БД (обход API — для стартовых
    условий теста). Возвращает user_id."""
    from backend.app import users
    conn = get_conn()
    uid = users.create_user(
        conn, username=username, full_name=full_name, role_id=role_id,
        password=password, must_change_password=must_change_password,
    )
    for dept_id in department_ids or []:
        if role_id == "department_head":
            conn.execute(
                "INSERT OR IGNORE INTO department_heads (user_id, department_id, created_at) "
                "VALUES (?, ?, ?)", (uid, dept_id, users.now_iso()),
            )
            conn.commit()
        else:
            users.add_doctor_to_department(conn, uid, dept_id)
    return uid


def create_department(name: str) -> str:
    from backend.app import users
    return users.create_department(get_conn(), name)


def login(client, username: str, password: str = TEST_PASSWORD):
    """Вход; при успехе вешает на клиент CSRF-заголовок."""
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        client.csrf = {"X-CSRF-Token": client.cookies.get("clinic_csrf")}
    return r


@pytest.fixture()
def auth_client(client):
    """Client with an active developer session; carries CSRF headers."""
    create_account(DEV_USERNAME, "developer", DEV_FULL_NAME)
    r = login(client, DEV_USERNAME)
    assert r.status_code == 200
    return client


def make_patient_payload(**overrides):
    payload = {
        "name": "Тестов Тест Тестович",
        "age": 50,
        "phone": "+7 (999) 000-00-00",
        "priority": "normal",
        "stage": "exam",
        "diagnosis": "C34.1",
        "nosology": "lung34",
        "diseaseStage": "II",
        "doctor": "Др. Проверяющий",
        "head": "Заведуев Тест Отделенович",
        "complaint": "Жалобы для теста",
        "events": [],
    }
    payload.update(overrides)
    return payload
