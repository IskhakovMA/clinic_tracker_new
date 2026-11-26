"""Run the F9 administrative Chromium journey on a disposable localhost fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
sys.path.insert(0, str(APP))
from tools.tests.fast_track_f2.run import snapshot, verify_manifest, wait_ready  # noqa: E402
from tools.tests.fast_track_f6.run import BROWSER_ENV, execute  # noqa: E402


def verify_writes(database, before, first_audit, fixture, targets):
    after, _ = snapshot(database)
    patient = fixture["patients"]["revoked"]["id"]
    actors = fixture["accountIds"]
    expected = dict(before["patients"][patient])
    expected.update(treating_doctor_user_id=actors["foreign"], version=expected["version"] + 1,
                    doctor=after["patients"][patient]["doctor"], updated_at=after["patients"][patient]["updated_at"])
    before["patients"][patient] = expected
    assert after == before, "The explicit physician reassignment is the sole authorized clinical change"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33
        events = conn.execute(
            "SELECT event_type,actor_user_id,patient_id,result,target_user_id,target_department_id,detail "
            "FROM audit_events WHERE id>? ORDER BY id", (first_audit,)
        ).fetchall()
    admin = actors["admin"]

    def one(event, *, user=None, department=None, patient_id=None, detail=None):
        matches = [row for row in events if row[:4] == (event, admin, patient_id, "ok")]
        matches = [row for row in matches if (user is None or row[4] == user) and
                   (department is None or row[5] == department) and (detail is None or row[6] == detail)]
        assert len(matches) == 1, event

    one("department_created", department=targets["department"])
    one("user_created", user=targets["head"])
    one("head_assigned", user=targets["head"], department=targets["department"])
    one("membership_changed", user=actors["doctor"], department=targets["department"], detail="added")
    one("membership_changed", user=actors["doctor"], detail="removed")
    one("treating_doctor_changed", patient_id=patient)
    one("sessions_terminated", user=actors["doctor"])
    one("password_reset", user=targets["head"])
    one("user_blocked", user=actors["doctor"])
    one("user_deactivated", user=actors["doctor"])
    one("role_changed", user=targets["head"])
    one("department_updated", department=targets["department"], detail="archived")
    assert [row[0] for row in events if row[0] in {
        "patient_updated", "patient_created", "patient_deleted", "treating_doctor_changed"
    } and row[3] == "ok"] == ["treating_doctor_changed"]
    return {"schema": 33, "expectedRequiredAuditEvents": 12, "explicitTreatingDoctorChanges": 1,
            "unchangedOtherMedicalRows": True, "unusedAutomaticClinicalWrites": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error("write evidence outside the checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sha = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD^{tree}"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", APP, "status", "--porcelain"]))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="clinic-f2-", dir="/tmp") as directory:
        scratch = Path(directory).resolve()
        database = scratch / "db/clinic.db"
        fixture_path = scratch / "fixture.json"
        env = {**{name: os.environ[name] for name in BROWSER_ENV if name in os.environ},
               "PATH": os.environ["PATH"], "TMPDIR": str(scratch), "TZ": "UTC",
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        with (output / "server.local.log").open("wb") as log:
            worker = subprocess.Popen(
                [sys.executable, "-I", "-B", str(HERE.parent / "fast_track_f2/server.py"),
                 "--scratch", str(scratch), "--port", str(port)],
                env=env, cwd=scratch, stdout=log, stderr=subprocess.STDOUT,
            )
        try:
            wait_ready(worker, origin, fixture_path)
            fixture = json.loads(fixture_path.read_bytes())
            assert fixture["syntheticOnly"] and fixture["origin"] == origin and fixture["schema"] == 33
            before, first_audit = snapshot(database)
            execute(["node", str(HERE / "journey.cjs")],
                    {**env, "F9_FIXTURE": str(fixture_path), "OUT": str(output)}, output)
            result = json.loads((output / "journey.local.json").read_bytes())
            result["database"] = verify_writes(database, before, first_audit, fixture, result.pop("auditTargets"))
            result["audit"] = verify_manifest(database, result.pop("responseProofs"))
            del result["audit"]["clinicalFixturesUnchanged"]
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGINT)
            try:
                clean = worker.wait(timeout=20) == 0
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
                clean = False
    with socket.socket() as listener:
        listener.settimeout(0.5)
        closed = listener.connect_ex(("127.0.0.1", port)) != 0
    result.update(candidateSha=sha, candidateTree=tree, dirtyCheckout=dirty, syntheticOnly=True,
                  cleanup={"cleanExit": clean, "listenerClosed": closed, "databaseRemoved": not scratch.exists()})
    assert all(result["cleanup"].values())
    result["screenshots"] = {image.name: hashlib.sha256(image.read_bytes()).hexdigest()
                             for image in sorted(output.glob("f9-*.png"))}
    (output / "public.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"F9 Chromium PASS: {len(result['checks'])} groups and exact audit")


if __name__ == "__main__":
    main()
