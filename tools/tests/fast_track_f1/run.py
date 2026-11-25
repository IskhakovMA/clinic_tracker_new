"""Launch the real application on private synthetic SQLite and run Chromium."""
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
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

HOST = "127.0.0.1"
HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]


def wait_ready(worker, origin, fixture):
    opener = build_opener(ProxyHandler({}))
    until = time.monotonic() + 60
    while time.monotonic() < until:
        if worker.poll() is not None:
            raise RuntimeError("Synthetic server failed to start. See the private server log.")
        if fixture.is_file():
            try:
                with opener.open(origin + "/api/ready", timeout=1) as response:
                    if response.status == 200 and json.load(response)["status"] in {"ok", "ready"}:
                        return
            except (URLError, TimeoutError, ValueError):
                pass
        time.sleep(0.15)
    raise RuntimeError("Synthetic server readiness deadline elapsed")


def clinical_digest(db):
    checks = {
        "patients": "SELECT * FROM patients ORDER BY id",
        "history": "SELECT * FROM history_entries ORDER BY id",
        "events": "SELECT * FROM events ORDER BY id",
    }
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        rows = {key: conn.execute(query).fetchall() for key, query in checks.items()}
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def verify_manifest(db, proofs):
    if not proofs:
        raise RuntimeError("Browser did not provide any protected read receipts")
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        authorized = conn.execute(
            "SELECT count(*), count(m.audit_event_id) FROM audit_events a "
            "LEFT JOIN read_access_manifests m ON a.id=m.audit_event_id "
            "WHERE a.event_type='medical_read_release'"
        ).fetchone()
        assert authorized[0] > 0 and authorized[0] == authorized[1]
        for proof in proofs:
            record = conn.execute(
                "SELECT count(*) FROM read_access_manifests m "
                "JOIN audit_events a ON a.id=m.audit_event_id "
                "WHERE a.event_type='medical_read_release' AND m.snapshot_sha256=?",
                (proof["sha256"],),
            ).fetchone()[0]
            assert record >= 1, proof["kind"]
    return dict(matchedExactResponseDigests=len(proofs), authorizedReads=authorized[0],
                authorizedReadManifests=authorized[1], clinicalFixturesUnchanged=True)


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
    dirty = bool(subprocess.check_output(["git", "-C", str(APP), "status", "--porcelain"]))
    with tempfile.TemporaryDirectory(prefix="clinic-f1-") as working:
        scratch = Path(working).resolve()
        clean_env = {"PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch),
                     "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        fixture = scratch / "fixture.json"
        browser_env = {
            **clean_env,
            **{name: os.environ[name] for name in ("PLAYWRIGHT_MODULE", "PLAYWRIGHT_BROWSERS_PATH",
                                                  "BROWSER_EXECUTABLE", "AXE_SOURCE", "F1_SCREENSHOTS") if name in os.environ},
            "F1_FIXTURE": str(fixture), "F1_RECEIPT": str(output / "browser-results.json"),
        }
        db = scratch / "db/clinic.db"
        with (output / "server.local.log").open("wb") as server_log:
            worker = subprocess.Popen(
                [sys.executable, "-I", "-B", str(HERE / "server.py"), "--scratch", str(scratch),
                 "--port", str(port)], cwd=scratch, env=clean_env, stdout=server_log, stderr=subprocess.STDOUT,
            )
        try:
            wait_ready(worker, origin, fixture)
            before = clinical_digest(db)
            with (output / "browser.local.log").open("wb") as browser_log:
                run = subprocess.run(
                    ["node", str(HERE / "browser.cjs")], cwd=scratch, env=browser_env,
                    stdout=browser_log, stderr=subprocess.STDOUT, timeout=180, check=False,
                )
            if run.returncode != 0:
                return run.returncode
            assert before == clinical_digest(db), "Clinical fixture changed during navigation"
            results = json.loads((output / "browser-results.json").read_bytes())
            results["audit"] = verify_manifest(db, results.pop("responseProofs"))
            results.update(candidateSha=sha, dirtyCheckout=dirty, syntheticOnly=True,
                           syntheticPatients=5, schemaVersion=33)
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
    print(f"F1 Chromium: {len(results['checks'])} checks, exact medical release receipts and clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
