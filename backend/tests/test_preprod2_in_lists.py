"""Pre-production hardening, round 2: no scope-wide statement binds one
variable per writer-controlled item.

Regression tests for preprod-run-2 lead NV2-08 (variant): internal
notifications and analytics built IN / NOT IN lists with one bound
variable per condition or per scoped patient.
"""
import sqlite3

from .conftest import get_conn, make_patient_payload


def _low_limit_conn():
    from backend.app import config, db
    conn = db._connect(config.DB_PATH)
    conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 40)
    return conn


def _developer(conn):
    from backend.app import policy
    uid = conn.execute("SELECT id FROM users WHERE role_id = 'developer'").fetchone()[0]
    return policy.load_current_user(conn, uid)


def test_internal_notifications_survive_many_conditions(auth_client, monkeypatch):
    from backend.app import config, store
    from backend.app.models import EventIn, PatientCreateIn
    from backend.app.services import internal_notifications
    monkeypatch.setattr(config, "MAX_EVENTS_PER_HISTORY_ENTRY", 200)
    store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(
        stage="exam", events=[{"date": "2020-01-01"}] * 60)), None)
    conn = _low_limit_conn()
    user = _developer(conn)
    s = internal_notifications.summary(conn, user)
    assert s["total"] >= 60 if "total" in s else True
    internal_notifications.mark_all_read(conn, user)
    conn.close()


def test_analytics_survives_scope_larger_than_variable_limit(auth_client):
    from backend.app import store
    from backend.app.analytics import service
    from backend.app.analytics.filters import AnalyticsFilters
    from backend.app.models import PatientCreateIn
    for i in range(60):
        store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(name=f"Аналитиков {i}")), None)
    conn = _low_limit_conn()
    result = service.summary(conn, _developer(conn), AnalyticsFilters())
    assert isinstance(result, dict)
    service.dashboard(conn, _developer(conn), AnalyticsFilters())
    conn.close()
