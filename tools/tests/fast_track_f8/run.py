"""Verify the F8 Chromium journey on real FastAPI and disposable synthetic SQLite."""
import argparse
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
sys.path.insert(0, str(APP))
from tools.tests.fast_track_f2.run import snapshot, verify_manifest, wait_ready  # noqa: E402
from tools.tests.fast_track_f6.run import BROWSER_ENV, execute  # noqa: E402


def seed_duplicate_group(fixture):
    jar = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))

    def post(path, body, status):
        headers = {"Content-Type": "application/json", "Idempotency-Key": str(uuid4())}
        csrf = next((cookie.value for cookie in jar if cookie.name == "clinic_csrf"), None)
        if csrf:
            headers["X-CSRF-Token"] = csrf
        request = Request(fixture["origin"] + path, json.dumps(body).encode(), headers=headers, method="POST")
        try:
            response = opener.open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == status
            return json.load(response)

    post("/api/auth/login", {"username": fixture["users"]["admin"], "password": fixture["password"]}, 200)
    base = {"priority": "normal", "diagnosis": "C34.1", "stage": "exam", "age": 48,
            "head": "Синтетический заведующий F8", "nosology": "lung34", "policyNumber": "9976543220199911"}
    for key, name, owner in (("duplicateA", "Синтетический Совпадение F8 Альфа", "doctor"),
                             ("duplicateB", "Синтетический Совпадение F8 Бета", "doctor"),
                             ("duplicateForeign", "Синтетический Совпадение F8 Скрыто", "foreign")):
        body = {**base, "name": name, "treatingDoctorUserId": fixture["accountIds"][owner]}
        if key != "duplicateA":
            refused = post("/api/patients", body, 409)
            assert refused["code"] == "POSSIBLE_DUPLICATE"
            body["duplicateConfirmation"] = {"reason": "Синтетическая отдельная карточка F8",
                                             "matchedPatientIds": [item["id"] for item in refused["matches"]]}
        card = post("/api/patients", body, 201)
        fixture["patients"][key] = {"id": card["id"], "name": card["name"]}
    correction = post("/api/patients", {**base, "name": "Синтетический Уточнение Телефона F8",
                                       "policyNumber": "9976543220199977",
                                       "treatingDoctorUserId": fixture["accountIds"]["doctor"]}, 201)
    fixture["patients"]["correction"] = {"id": correction["id"], "name": correction["name"]}
    fixture["correctedPhone"] = "+70000000287"
    post("/api/auth/logout", {}, 200)


def verify_medical_writes(db, before, audit_start, fixture):
    after, _ = snapshot(db)
    patients = fixture["patients"]
    doctor = fixture["accountIds"]["doctor"]
    pid = patients["overdue"]["id"]
    event = patients["overdue"]["eventIds"][0]
    checklist = patients["checklist"]["id"]
    correction = patients["correction"]["id"]
    revoked = patients["revoked"]["id"]
    foreign = fixture["accountIds"]["foreign"]
    admin = fixture["accountIds"]["admin"]
    assert set(before["events"]) == set(after["events"])
    assert set(before["patients"]) == set(after["patients"])
    assert set(before["history"]) == set(after["history"])
    for key, value in before["events"].items():
        if key == event:
            value = {**value, "actual_date": fixture["today"], "matched_plan": 0, "version": value["version"] + 1}
        assert after["events"][key] == value
    for key, value in before["patients"].items():
        if key in (pid, checklist):
            value = {**value, "updated_at": after["patients"][key]["updated_at"]}
        if key == correction:
            value = {**value, "phone": fixture["correctedPhone"], "version": value["version"] + 1,
                     "updated_at": after["patients"][key]["updated_at"]}
        if key == revoked:
            value = {**value, "treating_doctor_user_id": foreign, "doctor": after["patients"][key]["doctor"],
                     "version": value["version"] + 1, "updated_at": after["patients"][key]["updated_at"]}
        assert after["patients"][key] == value
    for key, value in before["history"].items():
        if value["patient_id"] == checklist:
            observed = after["history"][key]["checklist_json"]
            assert json.loads(observed) == {**json.loads(value["checklist_json"]), fixture["item"]: True}
            value = {**value, "checklist_json": observed}
        assert after["history"][key] == value
    assert all(after["facts"][key] == value for key, value in before["facts"].items())
    facts = [value for key, value in after["facts"].items() if key not in before["facts"]]
    assert len(facts) == 1
    fact = facts[0]
    assert (fact["patient_id"], fact["event_id"], fact["actor_user_id"], fact["field"], fact["new_value"]) == (
        pid, event, doctor, "actual_date", fixture["today"])
    assert not fact["old_value"] and fact["source"] == "event_update"
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33
        events = conn.execute(
            "SELECT event_type,actor_user_id,patient_id,result,detail FROM audit_events "
            "WHERE id>? AND event_type IN ('patient_updated','treating_doctor_changed','patient_created','patient_deleted') "
            "ORDER BY id", (audit_start,)
        ).fetchall()
    assert len(events) == 4
    event_audit, check_audit, field_audit, reassigned_audit = events
    assert event_audit[:4] == ("patient_updated", doctor, pid, "ok")
    assert event in event_audit[4] and "action=event_update;field=actualDate" in event_audit[4]
    assert check_audit[:4] == ("patient_updated", doctor, checklist, "ok")
    assert "action=checklist;outcome=changed;checked=1" in check_audit[4]
    assert field_audit == ("patient_updated", doctor, correction, "ok", "field=phone")
    assert reassigned_audit[:4] == ("treating_doctor_changed", admin, revoked, "ok")
    return {"schema": 33, "recordedFacts": 1, "matchedExactWriteAudits": 3, "authorityControlAudits": 1,
            "otherClinicalRowsUnchanged": True,
            "threeSeededDuplicateCardsUnchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error("write outside the checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sha = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", APP, "rev-parse", "HEAD^{tree}"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", APP, "status", "--porcelain"]))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="clinic-f2-") as tmp:
        scratch = Path(tmp).resolve()
        fixture_file = scratch / "fixture.json"
        db = scratch / "db/clinic.db"
        env = {**{key: os.environ[key] for key in BROWSER_ENV if key in os.environ},
               "PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch), "TZ": "UTC",
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        with (output / "server.local.log").open("wb") as log:
            worker = subprocess.Popen([sys.executable, "-I", "-B", str(HERE.parent / "fast_track_f2/server.py"),
                                       "--scratch", str(scratch), "--port", str(port)],
                                      env=env, cwd=scratch, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_ready(worker, origin, fixture_file)
            fixture = json.loads(fixture_file.read_bytes())
            assert fixture["syntheticOnly"] and fixture["schema"] == 33 and fixture["origin"] == origin
            seed_duplicate_group(fixture)
            fixture_file.write_text(json.dumps(fixture, ensure_ascii=False))
            before, audit_start = snapshot(db)
            execute(["node", str(HERE / "journey.cjs")],
                    {**env, "F8_FIXTURE": str(fixture_file), "OUT": str(output)}, output)
            result = json.loads((output / "journey.local.json").read_bytes())
            read_results = verify_manifest(db, result.pop("responseProofs"))
            del read_results["clinicalFixturesUnchanged"]
            result["database"] = verify_medical_writes(db, before, audit_start, fixture)
            result["audit"] = read_results
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
    result["screenshots"] = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                              for file in sorted(output.glob("f8-*.png"))}
    (output / "public.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print("F8 Chromium PASS with exact writes, protected read manifests and clean teardown")


if __name__ == "__main__":
    main()
