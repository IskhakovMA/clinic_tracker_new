"""Pre-production hardening, round 2: staff changes are atomic with their links.

Regression test for preprod-run-2 lead NV2-13: a department archived
concurrently between validation and linking left the role/status change
committed without its audit record while the API returned 422.
"""
import sqlite3

from .conftest import create_account, create_department, get_conn


def _archive_on_second_connection(dept_id):
    from backend.app import config, db
    c2 = db._connect(config.DB_PATH)
    c2.execute("PRAGMA busy_timeout = 100")
    try:
        with c2:
            c2.execute("UPDATE departments SET status = 'archived' WHERE id = ?", (dept_id,))
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c2.close()


def test_concurrent_archive_cannot_leave_unaudited_role_change(auth_client, monkeypatch):
    from backend.app import users
    dept = create_department("Отд-Гонка")
    uid = create_account("doc.race", "doctor", department_ids=[dept])
    real = users.update_user_fields
    archived = []

    def racing(conn, user_id, **kw):
        real(conn, user_id, **kw)
        archived.append(_archive_on_second_connection(dept))

    monkeypatch.setattr(users, "update_user_fields", racing)
    r = auth_client.patch(f"/api/users/{uid}", json={"roleId": "department_head", "departmentIds": [dept]},
                          headers=auth_client.csrf)
    conn = get_conn()
    role = conn.execute("SELECT role_id FROM users WHERE id = ?", (uid,)).fetchone()[0]
    audited = conn.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'role_changed' AND target_user_id = ?",
                           (uid,)).fetchone()[0]
    if r.status_code == 200:
        assert role == "department_head" and audited == 1
    else:
        assert role == "doctor" and audited == 0, (r.status_code, role, audited, archived)
