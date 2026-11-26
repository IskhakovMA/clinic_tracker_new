"""Run the F5 analytics integration gate with the same isolated FastAPI server as F2."""
import argparse
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
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
sys.path.insert(0, str(APP))
from tools.tests.fast_track_f2.run import snapshot, verify_manifest, wait_ready  # noqa: E402

HOST = '127.0.0.1'
OVERDUE_EVENT_COUNT = 52


def seed_overdue_page(fixture):
    jar = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))

    def post(path, body, status):
        csrf = next((cookie.value for cookie in jar if cookie.name == 'clinic_csrf'), None)
        headers = {'Content-Type': 'application/json'}
        if csrf:
            headers.update({'X-CSRF-Token': csrf, 'Idempotency-Key': str(uuid4())})
        req = Request(fixture['origin'] + path, json.dumps(body).encode(), headers, method='POST')
        with opener.open(req, timeout=20) as response:
            assert response.status == status
            return json.load(response)

    post('/api/auth/login', {'username': fixture['users']['admin'], 'password': fixture['password']}, 200)
    from datetime import date, timedelta

    today = date.fromisoformat(fixture['today'])
    # More than one server page in the same period. A single patient has
    # many events, so counting unique patients must differ from row totals.
    events = [{"date": str(today - timedelta(days=offset % 15 + 8)), "time": "10:30"}
              for offset in range(OVERDUE_EVENT_COUNT)]
    card = post("/api/patients", {
        "name": "Синтетический Страницы Аналитики", "age": 50, "priority": "normal",
        "stage": "exam", "diagnosis": "C34.1", "nosology": "lung34",
        "head": "Синтетический заведующий F5",
        "treatingDoctorUserId": fixture["accountIds"]["doctor"], "events": events,
    }, 201)
    fixture["patients"]["pagination"] = {"id": card["id"], "name": card["name"]}
    assert len(card["history"][0]["events"]) == OVERDUE_EVENT_COUNT
    post('/api/auth/logout', {}, 200)


def prove_commit(db, before, first_audit_id, fixture, csv_receipt):
    patient = fixture['patients']['overdue']
    event_id = patient['eventIds'][-1]
    after, _ = snapshot(db)
    expected = {key: {key_id: row.copy() for key_id, row in table.items()} for key, table in before.items()}
    event = expected['events'][event_id]
    event.update(actual_date=fixture['today'], matched_plan=0, version=event['version'] + 1)
    expected['patients'][patient['id']]['updated_at'] = after['patients'][patient['id']]['updated_at']
    reassigned = fixture["patients"]["revoked"]["id"]
    prior = expected["patients"][reassigned]
    updated = after["patients"][reassigned]
    prior.update(version=prior["version"] + 1, updated_at=updated["updated_at"], doctor=updated["doctor"],
                 treating_doctor_user_id=fixture["accountIds"]["foreign"])
    event_facts = set(after['facts']) - set(expected['facts'])
    assert len(event_facts) == 1
    fact_id = event_facts.pop()
    fact = after['facts'][fact_id]
    assert fact['patient_id'] == patient['id'] and fact['event_id'] == event_id
    assert fact['field'] == 'actual_date' and not fact['old_value'] and fact['new_value'] == fixture['today']
    assert fact['actor_user_id'] == fixture['accountIds']['doctor'] and fact['source'] == 'event_update'
    expected['facts'][fact_id] = fact
    assert expected == after, 'only the authorized clinical action may alter clinical rows'
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT event_type,actor_user_id,patient_id,result,detail FROM audit_events "
            "WHERE id>? AND event_type IN ('patient_updated','analytics_report_exported','treating_doctor_changed') ORDER BY id",
            (first_audit_id,),
        ).fetchall()
        export_manifests = conn.execute(
            "SELECT m.snapshot_sha256,m.object_count,m.objects_json FROM read_access_manifests m "
            "JOIN audit_events a ON a.id=m.audit_event_id "
            "WHERE a.id>? AND a.event_type='analytics_report_exported'", (first_audit_id,)
        ).fetchall()
    assert len(export_manifests) == 1
    digest, object_count, raw_manifest = export_manifests[0]
    assert digest == csv_receipt["sha256"]
    manifest = json.loads(raw_manifest)
    assert object_count == len(manifest) and object_count > 0
    authorized_patients = {data["id"] for key, data in fixture["patients"].items() if key != "foreign"}
    assert {(obj["type"], obj["id"]) for obj in manifest} == {
        ("patient", patient_id) for patient_id in authorized_patients
    }
    assert len(rows) == 3, "One export, one clinical action, and one authorized reassignment only"
    export, write, scope = rows
    assert scope[:4] == ("treating_doctor_changed", fixture["accountIds"]["admin"], reassigned, "ok")
    assert export[:2] == ("analytics_report_exported", fixture["accountIds"]["head"])
    assert not export[2] and export[3] == "ok"
    assert "report_type=data_quality detail_level=details " in export[4]
    assert "format=csv" in export[4] and "stage=exam" in export[4]
    export_fields = dict(item.split("=", 1) for item in export[4].split())
    assert int(export_fields["row_count"]) == csv_receipt["rows"]
    assert write[:4] == ("patient_updated", fixture["accountIds"]["doctor"], patient["id"], "ok")
    assert "action=event_update;field=actualDate" in write[4] and event_id in write[4]
    return {"matchedWriteAudit": 1, "recordedFacts": 1, "matchedExportAudit": 1, "matchedScopeControlAudit": 1,
            "matchedExactCsvDigest": True, "authorizedCsvManifestObjects": object_count,
            "deniedExportsGeneratedNoAuditOrFile": True, "allOtherClinicalRowsUnchanged": True}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.out.resolve()
    if output == APP or APP in output.parents:
        parser.error('write private outputs outside the source checkout')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        port = listener.getsockname()[1]
    origin = f'http://{HOST}:{port}'
    sha = subprocess.check_output(['git', '-C', APP, 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', APP, 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', '-C', APP, 'status', '--porcelain']))
    with tempfile.TemporaryDirectory(prefix='clinic-f2-') as working:
        scratch = Path(working).resolve()
        clean_env = {'PATH': os.environ['PATH'], 'HOME': str(scratch), 'TMPDIR': str(scratch),
                     'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        fixture_file = scratch / 'fixture.json'
        browser_env = {
            **clean_env,
            **{name: os.environ[name] for name in ('PLAYWRIGHT_MODULE', 'PLAYWRIGHT_BROWSERS_PATH',
                                                  'BROWSER_EXECUTABLE', 'WP_H_FULL_CHROMIUM', 'AXE_SOURCE',
                                                  'F5_SCREENSHOTS') if name in os.environ},
            'F5_FIXTURE': str(fixture_file), 'F5_RECEIPT': str(output / 'browser-results.json'),
        }
        db = scratch / 'db/clinic.db'
        with (output / 'server.local.log').open('wb') as log:
            worker = subprocess.Popen(
                [sys.executable, '-I', '-B', str(HERE.parent / 'fast_track_f2/server.py'),
                 '--scratch', str(scratch), '--port', str(port)],
                cwd=scratch, env=clean_env, stdout=log, stderr=subprocess.STDOUT,
            )
        try:
            wait_ready(worker, origin, fixture_file)
            fixture = json.loads(fixture_file.read_bytes())
            assert fixture['origin'] == origin and fixture['syntheticOnly'] and fixture['schema'] == 33
            seed_overdue_page(fixture)
            fixture_file.write_text(json.dumps(fixture, ensure_ascii=False))
            before, first_audit_id = snapshot(db)
            with (output / 'browser.local.log').open('wb') as log:
                run = subprocess.run(['node', str(HERE / 'browser.cjs')], cwd=scratch, env=browser_env,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=360, check=False)
            if run.returncode:
                return run.returncode
            results = json.loads((output / 'browser-results.json').read_bytes())
            audit = verify_manifest(db, results.pop('responseProofs'))
            del audit['clinicalFixturesUnchanged']
            audit.update(prove_commit(db, before, first_audit_id, fixture, results['csv']))
            results.update(candidateSha=sha, candidateTree=tree, dirtyCheckout=dirty,
                           syntheticOnly=True, syntheticPatients=len(before['patients']), schemaVersion=33, audit=audit)
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
    results['cleanup'] = dict(cleanExit=clean_exit, databaseRemoved=not scratch.exists(), listenerClosed=closed)
    assert all(results['cleanup'].values())
    (output / 'public.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
    print(f"F5 Chromium: {len(results['checks'])} groups; exact read audit and clean shutdown")
    return 0


if __name__ == '__main__':
    sys.exit(main())
