"""Real synthetic JSON/CSV/XLSX releases: one atomic original event+manifest."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from .conftest import DEV_USERNAME, get_conn, login, make_patient_payload
from .test_consilium_security import _make_state

CASES = ('json', 'csv', 'chemotherapy', 'goc', 'goc-65-plus')
EVENTS = {'json': 'export', 'csv': 'analytics_report_exported'}


@pytest.fixture
def export_case(client, monkeypatch, request):
    kind = request.param
    monkeypatch.setitem(_make_state.__globals__, 'DECISION', {'treatmentType': 'chemotherapy', 'treatmentScheme': 'Synthetic clinical text', 'goc': True, 'goc65Plus': True})
    aid, patient = _make_state(client, 'signed')
    if kind in ('json', 'csv'):
        login(client, DEV_USERNAME)
        added = client.post('/api/patients', json=make_patient_payload(name='Second Synthetic Person'), headers=client.csrf)
        assert added.status_code == 201, added.text
        patients = [patient, added.json()]
    else:
        patients = [patient]
    def send():
        if kind == 'json':
            return client.get('/api/export', headers=client.csrf)
        if kind == 'csv':
            return client.post('/api/analytics/export', json={'reportType': 'summary', 'detailLevel': 'aggregate'}, headers=client.csrf)
        return client.get('/api/consilium/registries/' + kind + '.xlsx', headers=client.csrf)
    return kind, aid, patients, send


def audit_max():
    return get_conn().execute('SELECT COALESCE(MAX(id),0) FROM audit_events').fetchone()[0]


def assert_release(kind, response, before, patient_ids, appointment=None):
    assert response.status_code == 200, response.text[:200] if kind in ('json', 'csv') else response.status_code
    c = get_conn()
    rows = c.execute('SELECT * FROM audit_events WHERE id>? ORDER BY id', (before,)).fetchall()
    assert len(rows) == 1
    assert rows[0]['event_type'] == EVENTS.get(kind, 'consilium_registry_exported')
    manifests = c.execute('SELECT * FROM read_access_manifests WHERE audit_event_id=?', (rows[0]['id'],)).fetchall()
    assert len(manifests) == 1
    m = manifests[0]
    assert m['snapshot_sha256'] == hashlib.sha256(response.content).hexdigest()
    objects = json.loads(m['objects_json'])
    assert m['object_count'] == len(objects)
    assert {o['id'] for o in objects if o['type'] == 'patient'} == set(patient_ids)
    if appointment:
        assert objects == sorted([{'type': 'appointment', 'id': appointment}, {'type': 'patient', 'id': next(iter(patient_ids))}], key=lambda x:(x['type'], x['id']))
    assert 'attachment; filename=' in response.headers['content-disposition']
    return m


@pytest.mark.parametrize('export_case', CASES, indirect=True)
def test_exact_export_manifest(export_case):
    kind, aid, patients, send = export_case
    before = audit_max(); response = send()
    m = assert_release(kind, response, before, {p['id'] for p in patients}, aid if kind not in ('json', 'csv') else None)
    for p in patients:
        for value in (p['name'], p.get('diagnosis'), p.get('phone'), p.get('policyNumber'), 'Synthetic clinical text'):
            if value:
                assert value not in m['objects_json']
    if kind == 'json':
        from backend.app import export_signing, read_access
        assert export_signing.verify(response.json())[0] == export_signing.VERIFIED
        assert json.loads(m['objects_json']) == read_access.manifest(response.json())
    elif kind == 'csv':
        assert response.content.startswith(b'\xef\xbb\xbf')
        assert response.headers['content-type'].startswith('text/csv')
    else:
        from .test_consilium_security import _sheet, NS
        sheet, _ = _sheet(response.content)
        texts = [x.text for x in sheet.findall('.//m:t', NS)]
        assert patients[0]['name'] in texts
        assert 'Synthetic clinical text' in texts


@pytest.mark.parametrize('export_case', ['json'], indirect=True)
def test_selected_json_ids_preserve_signature_and_exact_objects(export_case, client):
    _, _, patients, _ = export_case
    from backend.app import export_signing, read_access
    before = audit_max()
    response = client.get('/api/export?ids=' + patients[1]['id'], headers=client.csrf)
    m = assert_release('json', response, before, {patients[1]['id']})
    assert [p['id'] for p in response.json()['patients']] == [patients[1]['id']]
    assert export_signing.verify(response.json())[0] == export_signing.VERIFIED
    assert json.loads(m['objects_json']) == read_access.manifest(response.json())


@pytest.mark.parametrize('export_case', CASES, indirect=True)
@pytest.mark.parametrize('table', ['audit_events', 'read_access_manifests'])
def test_atomic_failure_withholds_export(export_case, table, caplog):
    kind, aid, patients, send = export_case
    c = get_conn(); before = audit_max()
    manifests = c.execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]
    # Only fixed test identifiers are interpolated; the exception contains a
    # synthetic sensitive marker to prove that errors/logs do not disclose it.
    sql = {'audit_events': "CREATE TRIGGER synthetic_export_fault BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT,'SyntheticSensitiveFailure'); END",
           'read_access_manifests': "CREATE TRIGGER synthetic_export_fault BEFORE INSERT ON read_access_manifests BEGIN SELECT RAISE(ABORT,'SyntheticSensitiveFailure'); END"}[table]
    c.execute(sql); c.commit()
    response = send()
    assert response.status_code == 503
    assert response.json()['code'] == 'AUDIT_UNAVAILABLE'
    assert 'content-disposition' not in response.headers
    assert audit_max() == before
    assert c.execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0] == manifests
    for marker in [p['name'] for p in patients] + ['SyntheticSensitiveFailure', 'Synthetic clinical text']:
        assert marker not in response.text and marker not in caplog.text
    c.execute('DROP TRIGGER synthetic_export_fault'); c.commit()
    assert_release(kind, send(), before, {p['id'] for p in patients}, aid if kind not in ('json', 'csv') else None)


@pytest.mark.parametrize('export_case', CASES, indirect=True)
@pytest.mark.parametrize('revocation', ['session', 'role'])
def test_revoke_during_preparation_before_release(export_case, monkeypatch, revocation, caplog):
    from backend.app import read_access
    kind, _, patients, send = export_case
    before = audit_max(); entered = Event(); resume = Event()
    original = read_access.persist
    def held(state, route, raw, params, denied):
        if not denied:
            entered.set()
            assert resume.wait(10)
        return original(state, route, raw, params, denied)
    monkeypatch.setattr(read_access, 'persist', held)
    c = get_conn()
    # Actual identity comes from the request state at the exact barrier, not
    # a guessed account or elapsed-time race.
    captured = {}
    def capture(state, *args):
        captured['user'] = state.user.id
        return held(state, *args)
    monkeypatch.setattr(read_access, 'persist', capture)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(send)
        try:
            assert entered.wait(10)
            if revocation == 'session':
                c.execute('DELETE FROM sessions WHERE user_id=?', (captured['user'],))
            else:
                c.execute("UPDATE users SET role_id='deputy_chief_oncology' WHERE id=?", (captured['user'],))
            c.commit()
        finally:
            resume.set()
        response = future.result(timeout=15)
    assert response.status_code == (401 if revocation == 'session' else 409)
    assert 'content-disposition' not in response.headers
    events = c.execute('SELECT event_type FROM audit_events WHERE id>?', (before,)).fetchall()
    assert [r[0] for r in events] == ['medical_read_denied']
    for marker in [p['name'] for p in patients] + ['Synthetic clinical text']:
        assert marker not in response.text and marker not in caplog.text




@pytest.mark.parametrize('export_case', ['json', 'csv'], indirect=True)
def test_patient_moves_out_of_doctor_scope_before_release(export_case, client, monkeypatch):
    from backend.app import read_access
    from .conftest import create_account
    _, _, patients, send = export_case
    doctor = create_account('export.scope', 'doctor')
    other = create_account('export.other', 'doctor')
    c = get_conn()
    for p in patients:
        c.execute('UPDATE patients SET treating_doctor_user_id=? WHERE id=?', (doctor, p['id']))
    c.commit(); assert login(client, 'export.scope').status_code == 200
    before = audit_max(); original = read_access.persist
    def moved(state, route, raw, params, denied):
        if not denied:
            fresh = get_conn()
            fresh.execute('UPDATE patients SET treating_doctor_user_id=? WHERE id=?', (other, patients[0]['id']))
            fresh.commit()
        return original(state, route, raw, params, denied)
    monkeypatch.setattr(read_access, 'persist', moved)
    response = send()
    assert response.status_code == 409
    assert response.json()['code'] == 'ACCESS_CONTEXT_CHANGED'
    assert c.execute('SELECT event_type FROM audit_events WHERE id>?', (before,)).fetchall()[0][0] == 'medical_read_denied'
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE id>? AND event_type IN ('export','analytics_report_exported')", (before,)).fetchone()[0] == 0
    for p in patients:
        assert p['name'] not in response.text


@pytest.mark.parametrize('export_case', CASES, indirect=True)
def test_release_does_not_reselect_export_source(export_case, monkeypatch):
    from backend.app import read_access, store
    from backend.app.analytics import service
    from backend.app.services import consilium
    kind, aid, patients, send = export_case
    original = read_access.persist
    def must_not_reselect(*args, **kwargs):
        raise AssertionError('export source must be captured before serialization')
    def after_serialization(*args):
        for module, name in ((store, 'list_patients'), (service, '_filtered_patient_ids'),
                             (consilium, 'registry_rows'), (consilium, 'goc_registry_rows')):
            monkeypatch.setattr(module, name, must_not_reselect)
        return original(*args)
    monkeypatch.setattr(read_access, 'persist', after_serialization)
    before = audit_max()
    assert_release(kind, send(), before, {p['id'] for p in patients}, aid if kind not in ('json','csv') else None)
