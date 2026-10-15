"""Pre-production hardening, round 3: route history changes are audited.

Regression test for preprod-run-3 candidate audit/unattributed-patient-history-writes:
event add/update/delete, note and checklist changes by an in-scope writer
left no audit row, unlike every sibling patient mutation.
"""
from .conftest import create_account, event_add_body, event_ref, get_conn, login, make_patient_payload


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

    def event_url(idx):
        event_id, version = event_ref(client, pid, 0, idx)
        return f"/api/patients/{pid}/events/{event_id}", version

    def patch_first():
        url, version = event_url(0)
        return client.patch(url, json={"field": "actualDate", "value": "2030-01-10", "version": version}, headers=client.csrf)

    def delete_second():
        url, version = event_url(1)
        return client.delete(f"{url}?version={version}", headers=client.csrf)

    steps = [
        (patch_first, "action=event_update;field=actualDate"),
        (lambda: client.patch(f"{base}/note", json={"note": "секретная заметка"}, headers=client.csrf), "action=note"),
        (lambda: client.patch(f"{base}/checklist", json={"item": "Анализ крови", "checked": True}, headers=client.csrf), "action=checklist"),
        (lambda: client.post(f"{base}/events", json=event_add_body(client, pid), headers=client.csrf), "action=event_add"),
        (delete_second, "action=event_delete"),
    ]
    for call, marker in steps:
        r = call()
        url = marker
        assert r.status_code == 200, (url, r.text)
        rows = _rows(pid)[before:]
        assert rows and marker in (rows[-1]["detail"] or ""), (marker, rows)
        assert rows[-1]["actor_user_id"] == uid
        before += len(rows)

    details = " ".join(r["detail"] or "" for r in _rows(pid))
    assert "секретная" not in details and "Анализ" not in details and "2030-01-10" not in details
