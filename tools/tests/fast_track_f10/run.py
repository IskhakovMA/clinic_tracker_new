"""Run the F10 audit Chromium journey on a disposable localhost fixture."""
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
from tools.tests.fast_track_f2.run import snapshot, wait_ready  # noqa: E402
from tools.tests.fast_track_f6.run import BROWSER_ENV, execute  # noqa: E402


def seed_history(database, fixture):
    """Add ordinary synthetic records, without manufacturing any manifests."""
    from backend.app import audit

    with sqlite3.connect(database) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for _ in range(225):
            audit.record_required(conn, audit.LOGIN_SUCCESS,
                                  actor_user_id=fixture["accountIds"]["admin"], actor_role="developer")


def verify_medical_write(database, before, first_audit, fixture):
    after, _ = snapshot(database)
    patient = fixture["patients"]["overdue"]["id"]
    old = before["patients"][patient]
    revised = after["patients"][patient]
    assert revised["complaint"] == "F10_PRIVATE_SYNTHETIC_COMPLAINT"
    assert revised["version"] == old["version"] + 1
    before["patients"][patient] = {**old, "complaint": revised["complaint"], "version": revised["version"],
                                   "updated_at": revised["updated_at"]}
    assert after == before
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT event_type,detail FROM audit_events WHERE id>? "
            "AND event_type IN ('patient_created','patient_updated','patient_deleted','treating_doctor_changed')",
            (first_audit,),
        ).fetchall()
    assert rows == [("patient_updated", "field=complaint")]
    return {"versionedClinicalWrites": 1, "otherMedicalRowsUnchanged": True}


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
            seed_history(database, fixture)
            before, first_audit = snapshot(database)
            execute(["node", str(HERE / "journey.cjs")],
                    {**env, "F10_FIXTURE": str(fixture_path), "OUT": str(output)}, output)
            result = json.loads((output / "journey.local.json").read_bytes())
            result["database"] = verify_medical_write(database, before, first_audit, fixture)
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
                             for image in sorted(output.glob("f10-*.png"))}
    (output / "public.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"F10 Chromium PASS: {len(result['checks'])} groups")


if __name__ == "__main__":
    main()
