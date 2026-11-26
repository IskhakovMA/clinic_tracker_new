"""Collect exact-revision F6 Chromium receipts from disposable FastAPI fixtures."""
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

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
LEGACY = ("schedule", "workflows", "loading", "decisions", "records", "zoom", "decisionzoom", "recordszoom")
BROWSER_ENV = ("PLAYWRIGHT_MODULE", "PLAYWRIGHT_BROWSERS_PATH", "BROWSER_EXECUTABLE", "WP_H_FULL_CHROMIUM", "AXE_SOURCE")


def execute(command, env, folder, timeout=540):
    with (folder / "browser.local.log").open("wb") as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True, cwd=APP)


def wait_ready(worker, origin, scratch):
    opener = build_opener(ProxyHandler({}))
    until = time.monotonic() + 90
    while time.monotonic() < until:
        if worker.poll() is not None:
            raise RuntimeError("Synthetic FastAPI exited before startup")
        dbs = list(scratch.glob("clinic-wph-baseline-*/clinic.db"))
        if len(dbs) == 1:
            try:
                with opener.open(origin + "/api/ready", timeout=1) as response:
                    if response.status == 200 and json.load(response)["status"] in {"ok", "ready"}:
                        return dbs[0]
            except (URLError, TimeoutError, ValueError):
                pass
        time.sleep(0.1)
    raise RuntimeError("Synthetic FastAPI readiness deadline exceeded")


def verify_journey(db, result):
    proof = result.pop("privateProof")
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33
        record = conn.execute(
            "SELECT patient_id,status,scheduled_time,version,revision_count,treatment_type,signed_at "
            "FROM oncology_consilium_appointments WHERE id=?", (proof["id"],)
        ).fetchone()
        assert record is not None
        assert tuple(record)[:6] == (proof["patient"], "signed", "11:00", proof["version"], 1, "surgery")
        assert record["signed_at"]
        rounds = conn.execute(
            "SELECT round,returned_by_user_id,comment FROM consilium_revision_history WHERE appointment_id=?",
            (proof["id"],),
        ).fetchall()
        assert len(rounds) == 1 and tuple(rounds[0]) == (1, proof["chair"], proof["returnComment"])
        mutations = conn.execute(
            "SELECT event_type,actor_user_id,result,detail FROM audit_events "
            "WHERE patient_id=? AND event_type LIKE 'consilium_%' ORDER BY id", (proof["patient"],),
        ).fetchall()
        expected = [("consilium_appointment_created", proof["doctor"]),
                    ("consilium_appointment_updated", proof["doctor"]),
                    ("consilium_review_started", proof["chair"]),
                    ("consilium_returned_for_revision", proof["chair"]),
                    ("consilium_appointment_updated", proof["doctor"]),
                    ("consilium_resubmitted", proof["doctor"]),
                    ("consilium_review_started", proof["chair"]),
                    ("consilium_signed", proof["chair"])]
        assert [(row["event_type"], row["actor_user_id"]) for row in mutations] == expected
        assert all(row["result"] == "ok" for row in mutations)
        assert all(proof["id"] in row["detail"] for row in mutations if row["event_type"] in {
            "consilium_appointment_created", "consilium_appointment_updated", "consilium_resubmitted",
        })
        for response_sha in proof["readHashes"]:
            assert conn.execute(
                "SELECT 1 FROM read_access_manifests WHERE snapshot_sha256=?", (response_sha,),
            ).fetchone() is not None
        assert conn.execute("SELECT stage FROM patients WHERE id=?", (proof["patient"],)).fetchone()[0] == proof["stage"]
    result["database"] = {"schema": 33, "signatureCount": 1, "revisionRounds": 1, "exactWrites": len(mutations),
                          "readManifestHashes": len(proof["readHashes"]), "unchangedPatientStage": True}
    return result


def journey(folder, environment):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    scratch = Path(environment["TMPDIR"])
    with (folder / "server.local.log").open("wb") as log:
        worker = subprocess.Popen([sys.executable, "-I", "-B", str(HERE.parent / "wp_h/server.py"),
                                  "--count", "100", "--port", str(port)],
                                  env=environment, stdout=log, stderr=subprocess.STDOUT, cwd=scratch)
        try:
            db = wait_ready(worker, origin, scratch)
            execute(["node", str(HERE / "journey.cjs")],
                    {**environment, "WP_H_ORIGIN": origin, "COUNT": "100", "OUT": str(folder)}, folder)
            result = verify_journey(db, json.loads((folder / "journey.local.json").read_bytes()))
        finally:
            if worker.poll() is None:
                worker.send_signal(signal.SIGINT)
            try:
                clean = worker.wait(timeout=20) == 0
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
                clean = False
    with socket.socket() as listener:
        listener.settimeout(0.5)
        closed = listener.connect_ex(("127.0.0.1", port)) != 0
    assert clean and closed and not list(scratch.glob("clinic-wph-baseline-*"))
    result["cleanup"] = {"cleanExit": clean, "listenerClosed": closed, "databaseRemoved": True}
    return result


def public_legacy_result(folder, suite):
    result_file = folder / "results.json"
    if result_file.is_file():
        raw = json.loads(result_file.read_bytes())
    else:
        # The checked-in native zoom suites emit a single JSON line on stdout.
        zoom = (folder / "card.log").read_text().splitlines()
        raw = next(json.loads(line) for line in zoom if line.startswith("{"))
        assert raw["nativeChromeZoom"] and raw["temporaryProfile"]
        return {"suite": suite, "nativeZoom": len(raw["samples"]), "exit": 0}
    assert all(not state.get("violations") for state in raw.get("axe", ()))
    exports = [{key: item[key] for key in ("kind", "sha256", "bytes")} for item in raw.get("xlsx", ())]
    return {"suite": suite, "checks": len(raw["checks"]), "axe": len(raw.get("axe", ())), "xlsx": exports, "exit": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=("journey", *LEGACY))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error("write artifacts outside the checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sha = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD^{tree}"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", APP, "status", "--porcelain"]))
    with tempfile.TemporaryDirectory(prefix="clinic-f6-") as temporary:
        scratch = Path(temporary).resolve()
        browser_env = {key: os.environ[key] for key in BROWSER_ENV if key in os.environ}
        env = {**browser_env, "PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch),
               "LANG": "C.UTF-8", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1",
               "CLINIC_HOST": "127.0.0.1", "CLINIC_HTTPS": "false", "CLINIC_AI_DATA_MODE": "disabled"}
        if args.suite == "journey":
            receipt = journey(output, env)
        else:
            execute([sys.executable, "-B", str(HERE.parent / "wp_i_consilium/run.py"),
                     "--suite", args.suite, "--out", str(output)], env, output, timeout=540)
            receipt = public_legacy_result(output, args.suite)
        assert not list(scratch.glob("clinic-wph-baseline-*"))
    receipt.update(candidateSha=sha, candidateTree=tree, dirtyCheckout=dirty, syntheticOnly=True)
    receipt["screenshots"] = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                              for file in sorted(output.glob("f6-*.png"))}
    (output / "public.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(f"F6 {args.suite} Chromium PASS")


if __name__ == "__main__":
    main()
