"""Exercise two isolated F11 installations with real Chromium and synthetic fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import ExitStack

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
sys.path.insert(0, str(APP))
from tools.tests.fast_track_f2.run import snapshot, wait_ready  # noqa: E402
from tools.tests.fast_track_f6.run import BROWSER_ENV, execute  # noqa: E402


def git_output(*args):
    return subprocess.check_output(["git", "-C", APP, *args], text=True).strip()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def stop(worker):
    if worker.poll() is None:
        worker.send_signal(signal.SIGINT)
    try:
        return worker.wait(timeout=20) == 0
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=5)
        return False


def verify_database(source_db, target_db, source_before, target_before, first_audit, export_sha, revision_target):
    after_source, _ = snapshot(source_db)
    after_target, _ = snapshot(target_db)
    old = source_before["patients"][revision_target]
    fresh = after_source["patients"][revision_target]
    source_before["patients"][revision_target] = {
        **old, "name": old["name"] + " обновлен",
        "updated_at": fresh["updated_at"], "version": old["version"] + 1,
    }
    assert source_before == after_source
    with sqlite3.connect(f"{source_db.as_uri()}?mode=ro", uri=True) as conn:
        matching_audit = conn.execute(
            "SELECT COUNT(*) FROM audit_events a JOIN read_access_manifests m ON m.audit_event_id = a.id "
            "WHERE a.event_type='export' AND a.result='ok' AND m.snapshot_sha256=?",
            (export_sha,),
        ).fetchone()[0]
        assert matching_audit == 1, "Exact download must have a committed audit and manifest"
    for table, rows in target_before.items():
        assert all(after_target[table].get(key) == row for key, row in rows.items()), table
    assert len(after_target["patients"]) == len(target_before["patients"]) + 1
    assert len(after_target["history"]) == len(target_before["history"]) + 1
    assert len(after_target["events"]) == len(target_before["events"]) + 1
    with sqlite3.connect(f"{target_db.as_uri()}?mode=ro", uri=True) as conn:
        events = conn.execute(
            "SELECT event_type, result FROM audit_events WHERE id>? AND result='ok' AND event_type IN "
            "('import_committed', 'import_replayed') ORDER BY id", (first_audit,),
        ).fetchall()
        assert events == [("import_committed", "ok"), ("import_replayed", "ok")], events
    return {"expectedImportWrites": 1, "existingMedicalRowsUnchanged": True,
            "expectedSourceVersionedWrites": 1, "otherSourceMedicalRowsUnchanged": True,
            "newHistory": 1, "newEvents": 1,
            "exactExportAuditManifest": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error("write evidence outside the checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    meta = dict(candidateSha=git_output("rev-parse", "HEAD"),
                candidateTree=git_output("rev-parse", "HEAD^{tree}"),
                dirtyCheckout=bool(git_output("status", "--porcelain")), syntheticOnly=True)
    ports = []
    stopped = []
    with tempfile.TemporaryDirectory(prefix="clinic-f11-", dir="/tmp") as outer:
        scratch = Path(outer).resolve()
        servers = {}
        env = {**{name: os.environ[name] for name in BROWSER_ENV if name in os.environ},
               "PATH": os.environ["PATH"], "TMPDIR": str(scratch), "TZ": "UTC", "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        key = scratch / "signing-key"
        key.write_text(secrets.token_hex(32))
        with ExitStack() as stack:
            for name in ("source", "target"):
                directory = scratch / f"clinic-f11-{name}"
                directory.mkdir(mode=0o700)
                port = free_port()
                ports.append(port)
                origin = f"http://127.0.0.1:{port}"
                fixture = directory / "fixture.json"
                with (output / f"{name}.local.log").open("wb") as log:
                    worker = subprocess.Popen(
                        [sys.executable, "-I", "-B", str(HERE / "server.py"),
                         "--scratch", str(directory), "--port", str(port),
                         *(["--source"] if name == "source" else [])],
                        env={**env, "CLINIC_EXPORT_SIGNING_KEY_FILE": str(key),
                             "CLINIC_EXPORT_SIGNING_KEY_ID": "synthetic-f11"},
                        cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                    )
                stack.callback(lambda process=worker: stopped.append(stop(process)))
                wait_ready(worker, origin, fixture)
                data = json.loads(fixture.read_bytes())
                assert data["syntheticOnly"] and data["schema"] == 33 and data["origin"] == origin
                servers[name] = (fixture, directory / "db/clinic.db")
            source, source_db = servers["source"]
            target, target_db = servers["target"]
            source_before, _ = snapshot(source_db)
            target_before, first_audit = snapshot(target_db)
            execute(["node", str(HERE / "journey.cjs")],
                    {**env, "F11_SOURCE": str(source), "F11_TARGET": str(target),
                     "F11_SIGNING_FILE": str(key), "OUT": str(output)}, output)
            result = json.loads((output / "journey.local.json").read_bytes())
            result["database"] = verify_database(
                source_db, target_db, source_before, target_before, first_audit, result["exportSha256"],
                json.loads(source.read_bytes())["revisionTarget"]["id"],
            )
    closed = []
    for port in ports:
        with socket.socket() as listener:
            listener.settimeout(0.5)
            closed.append(listener.connect_ex(("127.0.0.1", port)) != 0)
    result.update(**meta, cleanup={"serversStopped": len(stopped) == 2 and all(stopped),
                                   "listenersClosed": len(closed) == 2 and all(closed),
                                   "databasesAndKeyRemoved": not scratch.exists()})
    assert all(result["cleanup"].values())
    result["screenshots"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in sorted(output.glob("f11-*.png"))}
    (output / "public.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"F11 Chromium PASS: {len(result['checks'])} groups")


if __name__ == "__main__":
    main()
