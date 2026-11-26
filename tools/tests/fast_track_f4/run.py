"""Run the F4 integration gate with the same isolated FastAPI server as F2."""
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
ROW_COUNT = 52


def seed_schedule(fixture):
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
    events = [{'date': str(today + timedelta(days=offset)), 'time': '11:30' if offset else ''}
              for offset in range(ROW_COUNT)]
    card = post('/api/patients', {
        'name': 'Синтетический Расписание F4', 'age': 50, 'priority': 'normal',
        'stage': 'exam', 'diagnosis': 'C34.1', 'nosology': 'lung34',
        'treatingDoctorUserId': fixture['accountIds']['doctor'],
        'head': 'Синтетический заведующий F4', 'events': events,
    }, 201)
    fixture['patients']['schedule'] = {'id': card['id'], 'name': card['name'],
                                      'eventIds': [event['id'] for event in card['history'][0]['events']]}
    assert len(fixture['patients']['schedule']['eventIds']) == ROW_COUNT
    post('/api/auth/logout', {}, 200)


def prove_commit(db, before, first_audit_id, fixture):
    patient = fixture['patients']['overdue']
    event_id = patient['eventIds'][-1]
    after, _ = snapshot(db)
    expected = {key: {key_id: row.copy() for key_id, row in table.items()} for key, table in before.items()}
    event = expected['events'][event_id]
    event.update(actual_date=fixture['today'], matched_plan=0, version=event['version'] + 1)
    expected['patients'][patient['id']]['updated_at'] = after['patients'][patient['id']]['updated_at']
    revoked = fixture['patients']['revoked']['id']
    prior = expected['patients'][revoked]
    prior.update(version=prior['version'] + 1, updated_at=after['patients'][revoked]['updated_at'],
                 doctor=after['patients'][revoked]['doctor'],
                 treating_doctor_user_id=fixture['accountIds']['foreign'])
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
            "WHERE id>? AND event_type IN ('patient_updated','treating_doctor_changed') ORDER BY id", (first_audit_id,)
        ).fetchall()
    assert len(rows) == 2
    assert rows[1][:4] == ('treating_doctor_changed', fixture['accountIds']['admin'], revoked, 'ok')
    assert rows[0][:4] == ('patient_updated', fixture['accountIds']['doctor'], patient['id'], 'ok')
    assert 'action=event_update;field=actualDate' in rows[0][4] and event_id in rows[0][4]
    with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
        reads = conn.execute(
            "SELECT actor_user_id,patient_id,detail FROM audit_events "
            "WHERE id>? AND event_type='internal_notification_read'", (first_audit_id,)
        ).fetchall()
        assert reads == [(fixture['accountIds']['doctor'], patient['id'], 'type=overdue source_type=event')]
        read_state = conn.execute(
            "SELECT count(*) FROM internal_notification_state WHERE user_id=? AND read_at IS NOT NULL",
            (fixture['accountIds']['doctor'],),
        ).fetchone()[0]
        assert read_state == 1
    return {'matchedWriteAudit': 1, 'recordedFacts': 1, 'matchedAuthorityControlAudit': 1, 'allOtherClinicalRowsUnchanged': True,
            'matchedExplicitNotificationReadAudit': 1, 'persistedReadState': read_state}


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
                                                  'F4_SCREENSHOTS') if name in os.environ},
            'F4_FIXTURE': str(fixture_file), 'F4_RECEIPT': str(output / 'browser-results.json'),
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
            seed_schedule(fixture)
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
            audit.update(prove_commit(db, before, first_audit_id, fixture))
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
    print(f"F4 Chromium: {len(results['checks'])} groups; exact read audit and clean shutdown")
    return 0


if __name__ == '__main__':
    sys.exit(main())
