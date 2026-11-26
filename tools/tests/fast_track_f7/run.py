"""Run the F7 real Chromium patient journey on a disposable localhost FastAPI database."""
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
from tools.tests.fast_track_f6.run import BROWSER_ENV, execute, wait_ready  # noqa: E402


def verify(db, result):
    proof = result.pop("privateProof")
    main, replay, duplicate = proof["ids"]
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33
        for patient_id, name in zip(proof["ids"], proof["names"], strict=True):
            patient = conn.execute(
                "SELECT name,treating_doctor_user_id,stage,diagnosis FROM patients WHERE id=?", (patient_id,)
            ).fetchone()
            assert patient == (name, proof["doctor"], "exam", "C34.1")
            assert conn.execute("SELECT count(*) FROM patients WHERE name=?", (name,)).fetchone()[0] == 1
            history = conn.execute(
                "SELECT stage FROM history_entries WHERE patient_id=?", (patient_id,)
            ).fetchall()
            assert history == [("exam",)]
            created = conn.execute(
                "SELECT actor_user_id,result FROM audit_events WHERE event_type='patient_created' AND patient_id=?",
                (patient_id,)
            ).fetchall()
            assert created == [(proof["doctor"], "ok")]
        event = conn.execute(
            "SELECT e.id,e.date,e.time,e.actual_date FROM events e JOIN history_entries h "
            "ON e.history_entry_id=h.id WHERE h.patient_id=?", (main,)
        ).fetchall()
        assert event == [(proof["event"], proof["eventDate"], proof["eventTime"], None)]
        appointment = conn.execute(
            "SELECT id,scheduled_date,scheduled_time,consilium_group,status,created_by_user_id "
            "FROM oncology_consilium_appointments WHERE patient_id=?", (main,)
        ).fetchall()
        assert appointment == [(proof["appointment"], proof["slotDay"], "11:00", "thoracic", "recorded", proof["doctor"])]
        audit = conn.execute(
            "SELECT event_type,actor_user_id,result,detail FROM audit_events "
            "WHERE patient_id=? AND event_type LIKE 'consilium_%'", (main,)
        ).fetchall()
        assert len(audit) == 1
        assert audit[0][:3] == ("consilium_appointment_created", proof["doctor"], "ok")
        assert proof["appointment"] in audit[0][3]
        replay_audits = conn.execute(
            "SELECT patient_id,actor_user_id,result FROM audit_events "
            "WHERE event_type='patient_create_replayed' AND patient_id IN (?,?,?)", (main, replay, duplicate)
        ).fetchall()
        assert replay_audits == [(replay, proof["doctor"], "ok")]
        for read in proof["readHashes"]:
            assert conn.execute(
                "SELECT 1 FROM read_access_manifests WHERE snapshot_sha256=?", (read,)
            ).fetchone() is not None
    result["database"] = {"schema": 33, "createdCards": 3, "createAudits": 3, "replayAudits": 1,
                          "appointmentAudits": 1, "exactReadHashes": len(proof["readHashes"]),
                          "originalRouteAndPlannedEventUnchanged": True, "noUnsolicitedDecisionOrSignature": True}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error("write receipts outside the checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sha = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD^{tree}"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", APP, "status", "--porcelain"]))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="clinic-f7-") as directory:
        scratch = Path(directory).resolve()
        env = {**{key: os.environ[key] for key in BROWSER_ENV if key in os.environ},
               "PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch),
               "LANG": "C.UTF-8", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1",
               "CLINIC_HOST": "127.0.0.1", "CLINIC_HTTPS": "false", "CLINIC_AI_DATA_MODE": "disabled"}
        with (output / "server.local.log").open("wb") as log:
            worker = subprocess.Popen([sys.executable, "-I", "-B", str(HERE.parent / "wp_h/server.py"),
                                       "--count", "100", "--port", str(port)],
                                      env=env, stdout=log, stderr=subprocess.STDOUT, cwd=scratch)
        try:
            db = wait_ready(worker, origin, scratch)
            execute(["node", str(HERE / "journey.cjs")],
                    {**env, "WP_H_ORIGIN": origin, "COUNT": "100", "OUT": str(output)}, output)
            result = verify(db, json.loads((output / "journey.local.json").read_bytes()))
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGINT)
            try:
                clean = worker.wait(timeout=20) == 0
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
                clean = False
        assert not list(scratch.glob("clinic-wph-baseline-*"))
    with socket.socket() as listener:
        listener.settimeout(0.5)
        closed = listener.connect_ex(("127.0.0.1", port)) != 0
    assert clean and closed
    result.update(candidateSha=sha, candidateTree=tree, dirtyCheckout=dirty, syntheticOnly=True,
                  cleanup={"cleanExit": clean, "listenerClosed": closed, "databaseRemoved": not scratch.exists()})
    assert all(result["cleanup"].values())
    result["screenshots"] = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                             for file in sorted(output.glob("f7-*.png"))}
    (output / "public.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print("F7 Chromium PASS with exact medical audit and clean teardown")


if __name__ == "__main__":
    main()
