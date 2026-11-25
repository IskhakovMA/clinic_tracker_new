"""Run Sprint F2 in real Chromium against an isolated localhost FastAPI fixture."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile

# Reuse the F1 readiness and exact read-audit receipt verifier without changing F1.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fast_track_f1"))
from run import wait_ready, verify_manifest  # noqa: E402

HOST = "127.0.0.1"
HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]


def snapshot(db):
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            "patients": conn.execute("SELECT * FROM patients ORDER BY id").fetchall(),
            "history": conn.execute("SELECT * FROM history_entries ORDER BY id").fetchall(),
            "events": conn.execute("SELECT * FROM events ORDER BY id").fetchall(),
            "facts": conn.execute("SELECT * FROM patient_event_fact_history ORDER BY id").fetchall(),
        }
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        last_audit_id = conn.execute("SELECT max(id) FROM audit_events").fetchone()[0]
    return {key: {row["id"]: dict(row) for row in value} for key, value in rows.items()}, last_audit_id


def validate_mutations(db, before, first_audit_id, fixture):
    after, _ = snapshot(db)
    patients, actors = fixture["patients"], fixture["accountIds"]
    first_event, second_event = patients["overdue"]["eventIds"]
    patient = patients["overdue"]["id"]
    checklist = patients["checklist"]["id"]
    revoked = patients["revoked"]["id"]
    assert set(before["events"]) == set(after["events"])
    for key, row in before["events"].items():
        if key in {first_event, second_event}:
            row = {**row, "actual_date": fixture["today"], "matched_plan": 0, "version": row["version"] + 1}
        assert after["events"][key] == row, "Only the two explicit event completions may change events"
    assert set(before["patients"]) == set(after["patients"])
    for key, row in before["patients"].items():
        updated = after["patients"][key]
        if key in {patient, checklist, revoked}:
            update_count = int(key == revoked)
            row = {**row, "version": row["version"] + update_count, "updated_at": updated["updated_at"]}
        if key == revoked:
            row = {**row, "treating_doctor_user_id": actors["foreign"], "doctor": updated["doctor"]}
        assert updated == row, "No other patient fields may change"
    assert set(before["history"]) == set(after["history"])
    for key, row in before["history"].items():
        updated = after["history"][key]
        if row["patient_id"] == checklist:
            expected = {**json.loads(row["checklist_json"]), fixture["item"]: True}
            assert json.loads(updated["checklist_json"]) == expected
            row = {**row, "checklist_json": updated["checklist_json"]}
        assert updated == row, "No implicit stage/history changes or draft writes"
    assert all(after["facts"][key] == row for key, row in before["facts"].items())
    added_facts = [row for key, row in after["facts"].items() if key not in before["facts"]]
    assert {row["event_id"] for row in added_facts} == {first_event, second_event}
    assert len(added_facts) == 2
    for row in added_facts:
        assert row["patient_id"] == patient and not row["old_value"] and row["new_value"] == fixture["today"]
        assert row["field"] == "actual_date" and row["source"] == "event_update"
        assert row["actor_user_id"] == (actors["doctor"] if row["event_id"] == first_event else actors["admin"])
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT event_type,actor_user_id,patient_id,result,detail FROM audit_events "
            "WHERE id>? AND event_type IN ('patient_updated','treating_doctor_changed') ORDER BY id",
            (first_audit_id,),
        ).fetchall()
    assert len(rows) == 4, "Exactly four explicit clinical or authority mutations must be audited"
    one, two, three, four = rows
    fact_event = "action=event_update;field=actualDate"
    assert one[:4] == ("patient_updated", actors["doctor"], patient, "ok") and fact_event in one[4] and first_event in one[4]
    assert two[:4] == ("patient_updated", actors["doctor"], checklist, "ok") and "action=checklist;outcome=changed;checked=1" in two[4]
    assert three[:4] == ("patient_updated", actors["admin"], patient, "ok") and fact_event in three[4] and second_event in three[4]
    assert four[:4] == ("treating_doctor_changed", actors["admin"], revoked, "ok")
    return {"explicitBrowserClinicalWrites": 2, "authorizedControlMutations": 2,
            "recordedFacts": len(added_facts), "matchedWriteAudits": len(rows), "allOtherClinicalRowsUnchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if APP in output.parents or APP == output:
        parser.error("write private outputs outside the source checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        port = listener.getsockname()[1]
    origin = f"http://{HOST}:{port}"
    sha = subprocess.check_output(["git", "-C", str(APP), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(APP), "rev-parse", "HEAD^{tree}"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(APP), "status", "--porcelain"]))
    with tempfile.TemporaryDirectory(prefix="clinic-f2-") as working:
        scratch = Path(working).resolve()
        clean_env = {"PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch),
                     "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        fixture_file = scratch / "fixture.json"
        browser_env = {
            **clean_env,
            **{name: os.environ[name] for name in ("PLAYWRIGHT_MODULE", "PLAYWRIGHT_BROWSERS_PATH",
                                                  "BROWSER_EXECUTABLE", "WP_H_FULL_CHROMIUM", "AXE_SOURCE",
                                                  "F2_SCREENSHOTS") if name in os.environ},
            "F2_FIXTURE": str(fixture_file), "F2_RECEIPT": str(output / "browser-results.json"),
        }
        db = scratch / "db/clinic.db"
        with (output / "server.local.log").open("wb") as server_log:
            worker = subprocess.Popen(
                [sys.executable, "-I", "-B", str(HERE / "server.py"), "--scratch", str(scratch), "--port", str(port)],
                cwd=scratch, env=clean_env, stdout=server_log, stderr=subprocess.STDOUT,
            )
        try:
            wait_ready(worker, origin, fixture_file)
            fixture = json.loads(fixture_file.read_bytes())
            before, first_audit = snapshot(db)
            with (output / "browser.local.log").open("wb") as browser_log:
                run = subprocess.run(
                    ["node", str(HERE / "browser.cjs")], cwd=scratch, env=browser_env,
                    stdout=browser_log, stderr=subprocess.STDOUT, timeout=420, check=False,
                )
            if run.returncode != 0:
                return run.returncode
            results = json.loads((output / "browser-results.json").read_bytes())
            audit = verify_manifest(db, results.pop("responseProofs"))
            del audit["clinicalFixturesUnchanged"]
            results["audit"] = audit | validate_mutations(db, before, first_audit, fixture)
            results.update(candidateSha=sha, candidateTree=tree, dirtyCheckout=dirty,
                           syntheticOnly=True, syntheticPatients=len(fixture["patients"]), schemaVersion=fixture["schema"])
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGINT)
            try:
                clean_exit = worker.wait(timeout=15) == 0
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
                clean_exit = False
    with socket.socket() as listener:
        listener.settimeout(0.5)
        closed = listener.connect_ex((HOST, port)) != 0
    results["cleanup"] = dict(cleanExit=clean_exit, databaseRemoved=not scratch.exists(), listenerClosed=closed)
    assert all(results["cleanup"].values())
    (output / "public.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"F2 Chromium: {len(results['checks'])} groups; authorized audit and clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
