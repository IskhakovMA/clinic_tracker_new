"""Pre-production hardening, round 3: route history changes are audited.

Regression test for preprod-run-3 candidate audit/unattributed-patient-history-writes:
event add/update/delete, note and checklist changes by an in-scope writer
left no audit row, unlike every sibling patient mutation.
"""
from .conftest import create_account, get_conn, login, make_patient_payload


def _rows(pid):
    return [dict(r) for r in get_conn().execute(
        "SELECT event_type, detail, actor_user_id FROM audit_events WHERE patient_id = ? ORDER BY id", (pid,))]


def test_history_writes_leave_attributed_audit_rows_without_values(client):
    uid = create_account("doc.audit", "doctor")
    assert login(client, "doc.audit").status_code == 200
    payload = make_patient_payload(events=[{"date": "2030-01-10", "time": "10:00"}])
    pid = client.post("/api/patients", json=payload, headers=client.csrf).json()["id"]
    before = len(_rows(pid))
    base = f"/api/patients/{pid}/history/0"

    steps = [
        ("patch", f"{base}/events/0", {"field": "actualDate", "value": "2030-01-10"}, "action=event_update;field=actualDate"),
        ("patch", f"{base}/note", {"note": "секретная заметка"}, "action=note"),
        ("patch", f"{base}/checklist", {"item": "Анализ крови", "checked": True}, "action=checklist"),
        ("post", f"{base}/events", None, "action=event_add"),
        ("delete", f"{base}/events/1", None, "action=event_delete"),
    ]
    for method, url, body, marker in steps:
        kwargs = {"headers": client.csrf}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 200, (url, r.text)
        rows = _rows(pid)[before:]
        assert rows and marker in (rows[-1]["detail"] or ""), (marker, rows)
        assert rows[-1]["actor_user_id"] == uid
        before += len(rows)

    details = " ".join(r["detail"] or "" for r in _rows(pid))
    assert "секретная" not in details and "Анализ" not in details and "2030-01-10" not in details
