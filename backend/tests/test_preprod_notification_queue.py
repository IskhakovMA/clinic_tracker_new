"""Pre-production hardening: manual SMS queue volume is bounded.

Regression tests for audit lead NV-13: POST /api/notifications had no rate
limit, no scheduling horizon and no per-patient cap, so one writer could
fill the shared queue and push other doctors' reminders back.
"""
from datetime import datetime, timedelta, timezone

from .test_notifications import _create_patient_direct, _grant, _manual


def _at(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def test_manual_create_rejects_far_future_schedule(auth_client):
    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid, _at(days=400)).status_code == 422
    assert _manual(auth_client, pid, "9999-12-31T23:59:59-23:59").status_code == 422
    assert _manual(auth_client, pid, _at(days=30)).status_code == 201


def test_manual_pending_rows_are_capped_per_patient(auth_client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "NOTIFICATION_MAX_PENDING_MANUAL_PER_PATIENT", 5)
    pid = _create_patient_direct()
    _grant(auth_client, pid)
    codes = [_manual(auth_client, pid, _at(hours=1, microseconds=i)).status_code for i in range(7)]
    assert codes[:5] == [201] * 5
    assert codes[5:] == [422, 422]


def test_manual_create_is_rate_limited(auth_client):
    pid = _create_patient_direct()
    _grant(auth_client, pid)
    codes = [_manual(auth_client, pid, _at(hours=1, microseconds=i)).status_code for i in range(40)]
    assert 429 in codes
