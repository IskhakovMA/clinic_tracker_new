"""Pre-production hardening: audit details cannot be forged via filter ids.

Regression tests for audit lead NV-18: analytics export copied
department_id/doctor_user_id query strings verbatim into the space-
delimited audit detail, and the CLI audit-tail printed control bytes raw.
"""


def _export(client, query: str):
    return client.post(
        f"/api/analytics/export?{query}",
        json={"reportType": "summary", "detailLevel": "aggregate"},
        headers=client.csrf,
    )


def test_analytics_filter_ids_must_be_plain_ids(auth_client):
    r = _export(auth_client, "department_id=x%20row_count%3D0%0AFAKE%1B%5B2K")
    assert r.status_code == 422
    r = _export(auth_client, "doctor_user_id=y%0D")
    assert r.status_code == 422
    assert _export(auth_client, "department_id=abc123def").status_code == 200


def test_audit_tail_neutralises_control_characters(auth_client, capsys):
    import argparse
    import importlib
    import sys
    import backend
    from backend.app import audit, db
    cli = importlib.import_module("backend.cli")
    try:
        audit.record(db.get_connection(), audit.EXPORT, detail="records=1\nFAKE login_success\x1b[2K\r")
        cli.cmd_audit_tail(argparse.Namespace(limit=5))
    finally:
        # Do not leave a backend.cli bound to this test's modules behind.
        sys.modules.pop("backend.cli", None)
        if hasattr(backend, "cli"):
            delattr(backend, "cli")
    out = capsys.readouterr().out
    body = out.split("\n\nВсего событий")[0]
    assert "\x1b" not in body and "\r" not in body
    assert "\nFAKE" not in body
