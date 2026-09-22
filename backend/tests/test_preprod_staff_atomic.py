"""Pre-production hardening: a rejected staff change leaves nothing behind.

Regression tests for audit lead NV-09: an invalid departmentId was checked
only after the account insert/update had been committed, so the API
answered 422 while the new account or role change stayed in the database
with no audit event.
"""
from .conftest import create_account, create_department, get_conn


def _audit_count(event_type: str, target: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM audit_events WHERE event_type = ?"
    params = [event_type]
    if target:
        sql += " AND target_user_id = ?"
        params.append(target)
    return get_conn().execute(sql, params).fetchone()[0]


def test_create_with_unknown_department_creates_nothing(auth_client):
    r = auth_client.post("/api/users", json={
        "username": "nv.probe", "fullName": "Пробов Тест Тестович",
        "password": "a-perfectly-fine-passphrase", "roleId": "developer",
        "departmentIds": ["does-not-exist"],
    }, headers=auth_client.csrf)
    assert r.status_code == 422
    from backend.app import users
    assert users.get_user_by_username(get_conn(), "nv.probe") is None


def test_update_with_unknown_department_changes_nothing(auth_client):
    dept = create_department("Отделение Тест")
    doc = create_account("doc.one", "doctor", department_ids=[dept])
    r = auth_client.patch(f"/api/users/{doc}", json={
        "roleId": "developer", "status": "blocked", "departmentIds": ["does-not-exist"],
    }, headers=auth_client.csrf)
    assert r.status_code == 422
    row = get_conn().execute("SELECT role_id, status FROM users WHERE id = ?", (doc,)).fetchone()
    assert (row["role_id"], row["status"]) == ("doctor", "active")
    links = get_conn().execute("SELECT department_id FROM user_departments WHERE user_id = ?", (doc,)).fetchall()
    assert [l[0] for l in links] == [dept]


def test_valid_update_is_still_audited(auth_client):
    dept = create_department("Отделение Тест")
    doc = create_account("doc.two", "doctor")
    r = auth_client.patch(f"/api/users/{doc}", json={"departmentIds": [dept]}, headers=auth_client.csrf)
    assert r.status_code == 200
    assert _audit_count("user_updated", doc) == 1
