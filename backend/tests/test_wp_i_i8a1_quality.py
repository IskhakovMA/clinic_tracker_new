"""A1 only: real HTTP release with a synthetic complete fixture builder.

The lab route is created per temporary test app and is never installed by the
production package. No A2 generic selection/pagination or UI is implemented.
"""
import base64
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, Request

from .conftest import create_account, create_department, get_conn, login, make_patient_payload, TEST_PASSWORD


@pytest.fixture
def lab(auth_client):
    from backend.app import db, policy, quality_pages as qp, request_security, store, users
    from backend.app.main import app
    from backend.app.deps import get_db, require_active_password, abuse_rate_limit
    from backend.app.models import PatientCreateIn
    conn = get_conn()
    doctor = create_account('a1.doctor', 'doctor')
    other = create_account('a1.other', 'doctor')
    dept = create_department('Синтетическое отделение A1')
    users.add_doctor_to_department(conn, doctor, dept)
    ids = []
    with conn:
        for i in range(3):
            ids.append(store.insert_patient_rows(conn, PatientCreateIn(**make_patient_payload(
                name=f'Синтетический A1 {i}', phone=f'+7 900 111-00-0{i}',
                events=[{'date': '2026-12-01', 'time': '10:00'}])), doctor,
                created_by_user_id=None, creation_source='legacy'))
    settings = {'mutate': None, 'after': None, 'calls': 0}

    def builder(ctx):
        # Test fixture adapter: no generic A2 filters/group/member implementation.
        # The three known patients have distinct phones/names and no groups.
        def page(rows, total, selector):
            start = ctx.offset if selector == ctx.parsed.selector else 0
            selected = rows[start:start+ctx.parsed.limit]
            end = start+len(selected)
            return {'items': selected, 'page': {'limit': ctx.parsed.limit, 'offset': start,
                    'total': total, 'has_next': end < total,
                    'next_cursor': ctx.cursor_for(selector, end) if end < total else None}}
        rows = []
        for p in ctx.quality['patients']:
            rows.append({'patient_id': p['patient_id'], 'patient_name': p['patient_name'],
                         'treating_doctor': {'user_id': doctor, 'display_name': p['treating_doctor_name']},
                         'departments': {'preview': [{'department_id': dept, 'display_name': 'Синтетическое отделение A1'}], 'total': 1, 'has_more': False},
                         'completeness': {'score': p['completeness']['score'], 'filled': p['completeness']['present'], 'total': p['completeness']['total'], 'missing': p['completeness']['missing']}, 'issue_count': p['issue_count'],
                         'max_severity': p['max_severity'], 'top_issues': p['top_issues'], 'can_edit': p['can_edit']})
        rows.sort(key=lambda p: p['patient_id'])
        result = {'totals': {'patients': len(rows), 'findings': len(ctx.quality['issues']), 'duplicate_groups': 0},
                  'bases': {'scope_summary': 'authorized_scope', 'totals': 'matched_query', 'facets': 'authorized_scope',
                            'groups': 'groups_touching_at_least_one_matched_patient'},
                  'patients': page(rows, len(rows), qp.Selector()),
                  'duplicate_groups': page([], 0, qp.Selector('groups')),
                  'facets': {'doctors': page([{'user_id': doctor, 'display_name': rows[0]['treating_doctor']['display_name'] if rows else 'Синтетический Врач A1', 'patient_count': len(rows)}], 1, qp.Selector('facets', facet='doctors')),
                             'departments': page([{'department_id': dept, 'display_name': 'Синтетическое отделение A1', 'patient_count': len(rows)}], 1, qp.Selector('facets', facet='departments'))},
                  'rules': {'version': 'quality-rules-v1', 'count': 11, 'descriptions': ctx.quality['rules']}}
        if settings['mutate']:
            settings['mutate'](result, ctx)
        return result

    @app.get('/api/data-quality/_a1-lab', dependencies=[Depends(abuse_rate_limit('a1_lab', 60))])
    def probe(request: Request, connection=Depends(get_db), user=Depends(require_active_password)):
        parsed = qp.normalize(request)
        response = qp.prepare(connection, user, parsed, builder)
        settings['calls'] += 1
        if settings['after']:
            settings['after'](connection, user)
        return response

    def request(query='', **kwargs):
        return auth_client.get('/api/data-quality/_a1-lab?mode=quality-v1'+query, **kwargs)

    return {'client': auth_client, 'get': request, 'settings': settings, 'ids': ids,
            'doctor': doctor, 'other': other, 'dept': dept, 'qp': qp, 'builder': builder}


def _count(event='medical_read_release'):
    return get_conn().execute('SELECT COUNT(*) FROM audit_events WHERE event_type=?', (event,)).fetchone()[0]


def _other_write(sql, args=(), *, commit=True):
    from backend.app import db, config
    c = db._connect(config.DB_PATH)
    try:
        c.execute('BEGIN IMMEDIATE'); c.execute(sql, args)
        c.commit() if commit else c.rollback()
    finally:
        c.close()


def _safe(response):
    text = response.text
    for value in ['Синтетический', '555', 'private@example.com', 'C34.1', 'Traceback', 'SELECT ', '/Users/']:
        assert value not in text


@pytest.mark.parametrize('view', ['page', 'groups', 'members', 'facets', 'patient_issues'])
def test_public_mode_is_not_activated(auth_client, view):
    suffix = '&facet=doctors' if view == 'facets' else '&owner_patient_id=missing' if view == 'patient_issues' else '&group_id='+'a'*64 if view == 'members' else ''
    r = auth_client.get('/api/data-quality/issues?mode=quality-v1&view='+view+suffix)
    assert r.status_code == 503 and r.json()['code'] == 'QUALITY_NOT_AVAILABLE'
    _safe(r)


@pytest.mark.parametrize('query,status', [
    ('mode=quality-v1&limit=0', 422), ('mode=quality-v1&limit=101', 422),
    ('mode=quality-v1&limit=true', 422), ('mode=quality-v1&limit=1.0', 422),
    ('mode=quality-v1&limit=1&limit=2', 400), ('mode=quality-v1&priority=urgent', 422),
    ('mode=unknown', 422), ('mode=quality-v1&offset=0', 422),
    ('mode=quality-v1&department=private@example.com', 422),
    ('mode=quality-v1&search=private@example.com', 422),
    ('mode=quality-v1&view=members', 422), ('mode=quality-v1&view=facets', 422),
    ('mode=quality-v1&view=patient_issues', 422),
    ('mode=quality-v1&owner_patient_id=private@example.com', 422),
    ('mode=quality-v1&cursor=EXAMPLE', 422),
])
def test_new_validation_is_bounded_and_does_not_echo_pii(auth_client, query, status):
    r = auth_client.get('/api/data-quality/issues?'+query)
    assert r.status_code == status
    _safe(r)


@pytest.mark.parametrize('headers,status', [
    ({'X-Search': 'a'*513}, 422), ({'X-Search': '%FF'}, 422),
    ({'X-Search': '%GG'}, 422), ({'X-Quality-Facet-Search': 'private@example.com'}, 422),
    ([('X-Search', 'x'), ('X-Search', 'y')], 400),
])
def test_new_header_validation_precedes_legacy_input_echo(auth_client, headers, status):
    r = auth_client.get('/api/data-quality/issues?mode=quality-v1', headers=headers)
    assert r.status_code == status
    _safe(r)


def test_public_auth_password_gate_and_quota(client, auth_client, monkeypatch):
    from backend.app import security
    auth_client.cookies.clear()
    assert auth_client.get('/api/data-quality/issues?mode=quality-v1').status_code == 401
    uid = create_account('a1.mustchange', must_change_password=True)
    assert login(client, 'a1.mustchange').status_code == 200
    assert client.get('/api/data-quality/issues?mode=quality-v1').status_code == 403
    assert login(client, 'dev.tester').status_code == 200
    monkeypatch.setattr(security, 'check_request_rate_limit', lambda *a, **k: 1)
    r = client.get('/api/data-quality/issues?mode=quality-v1')
    assert r.status_code == 429 and 'Retry-After' in r.headers


def test_legacy_body_bytes_and_findings_total_preserved(lab):
    from backend.app.services import data_quality
    from backend.app import policy
    client = lab['client']; conn = get_conn()
    user = policy.load_current_user(conn, conn.execute("SELECT id FROM users WHERE username='dev.tester'").fetchone()[0])
    expected = data_quality.filter_result(data_quality.evaluate(conn, user, identity_signals=True), limit=1)
    r = client.get('/api/data-quality/issues?limit=1&view=ignored-without-mode')
    assert r.status_code == 200
    # Starlette's existing JSON serialization, including key order/UTF-8 bytes.
    from starlette.responses import JSONResponse
    assert r.content == JSONResponse(expected).body
    assert len(r.json()['issues']) == 1 and len(r.json()['patients']) == 3
    assert r.json()['total'] == len(data_quality.evaluate(conn, user, identity_signals=True)['issues'])


def test_real_scoped_release_manifest_and_source_record(lab):
    r = lab['get']('&limit=1'); assert r.status_code == 200, r.text
    a = r.json(); assert a['patients']['page']['total'] == 3 and len(a['patients']['items']) == 1
    conn = get_conn(); row = conn.execute('SELECT * FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
    assert row['snapshot_sha256'] == hashlib.sha256(r.content).hexdigest()
    objects = json.loads(row['objects_json'])
    assert {x['id'] for x in objects if x['type'] == 'patient'} == set(lab['ids'])
    blob = row['objects_json']; assert 'Синтетический' not in blob and 'C34.1' not in blob
    assert lab['qp'].ROOT_TTL == 900


def test_canonical_query_and_selector_do_not_echo_values(lab):
    from starlette.requests import Request
    def normalized(query, headers=()):
        return lab['qp'].normalize(Request({'type': 'http', 'query_string': query.encode(), 'headers': list(headers)}))
    a = normalized('mode=quality-v1&code=missing_diagnosis&limit=50', [(b'x-search', b'%41%62%43')])
    b = normalized('limit=50&code=missing_diagnosis&mode=quality-v1', [(b'x-search', b'abc')])
    assert a.query == b.query and a.query.digest() == b.query.digest()
    assert a.query.echo() == {'sort': 'severity', 'has_search': True, 'active_filters': ['code', 'search']}
    assert a.selector.digest() != lab['qp'].Selector('groups').digest()


def test_cursor_partition_stable_and_bound_to_query(lab):
    r = lab['get']('&limit=1'); a = r.json(); assert r.status_code == 200
    root = a['snapshot']['token']; cursor = a['patients']['page']['next_cursor']; seen = [a['patients']['items'][0]['patient_id']]
    for _ in range(2):
        r = lab['get']('&limit=1&snapshot='+root+'&cursor='+cursor); assert r.status_code == 200, r.text
        a = r.json(); seen += [p['patient_id'] for p in a['patients']['items']]; cursor = a['patients']['page']['next_cursor']
    assert set(seen) == set(lab['ids']) and len(seen) == len(set(seen)) == 3 and cursor is None
    r = lab['get']('&limit=2&snapshot='+root+'&cursor='+lab['get']('&limit=1').json()['patients']['page']['next_cursor'])
    assert r.status_code == 409 and r.json()['code'] == 'PATIENT_QUERY_CHANGED'; _safe(r)


@pytest.mark.parametrize('fault', ['signature', 'expiry', 'restart', 'wrong-domain', 'bool-offset', 'extra-key', 'noncanonical', 'duplicate-key'])
def test_tokens_fail_closed_without_payload(lab, monkeypatch, fault):
    qp = lab['qp']; a = lab['get']('&limit=1').json(); root = a['snapshot']['token']; cursor = a['patients']['page']['next_cursor']
    payload = qp._open(cursor, 'cursor')
    if fault == 'signature': cursor = cursor[:-1]+('a' if cursor[-1] != 'a' else 'b')
    elif fault == 'expiry': monkeypatch.setattr(qp, '_now', lambda: payload['exp']+1)
    elif fault == 'restart': monkeypatch.setattr(qp, '_KEY', bytes(range(32)))
    elif fault == 'wrong-domain': cursor = qp._seal(payload, 'snapshot')
    elif fault == 'bool-offset': payload['o'] = True; cursor = qp._seal(payload, 'cursor')
    elif fault == 'extra-key': payload['private'] = 'private@example.com'; cursor = qp._seal(payload, 'cursor')
    else:
        raw = json.dumps(payload, indent=1).encode() if fault == 'noncanonical' else (qp._canonical(payload)[:-1]+b',"o":1}')
        cursor = base64.urlsafe_b64encode(raw).rstrip(b'=').decode()+'.'+qp._mac('cursor', raw)
    r = lab['get']('&limit=1&snapshot='+root+'&cursor='+cursor)
    assert r.status_code == 410, r.text
    _safe(r)


def test_another_valid_session_cannot_use_capsule(lab):
    a = lab['get']('&limit=1').json(); other = create_account('a1.peer', 'developer')
    assert login(lab['client'], 'a1.peer').status_code == 200
    r = lab['get']('&limit=1&snapshot='+a['snapshot']['token']+'&cursor='+a['patients']['page']['next_cursor'])
    assert r.status_code == 409 and r.json()['code'] == 'ACCESS_CONTEXT_CHANGED'; _safe(r)


@pytest.mark.parametrize('mutation', ['offpage_name', 'doctor', 'department', 'event', 'stage', 'checklist', 'equal_count_swap'])
def test_whole_input_change_after_serialization_refuses(lab, monkeypatch, mutation):
    from backend.app import read_access
    # A doctor has3 patients; transfer offpage rather than changing actor role.
    assert login(lab['client'], 'a1.doctor').status_code == 200
    pid = sorted(lab['ids'])[-1]
    old = read_access._persist
    def before(state, route, raw, params, denied):
        if not denied and route.endswith('/_a1-lab'):
            if mutation == 'offpage_name': _other_write('UPDATE patients SET name=? WHERE id=?', ('Измененный синтетический', pid))
            elif mutation == 'doctor': _other_write('UPDATE patients SET treating_doctor_user_id=? WHERE id=?', (lab['other'], pid))
            elif mutation == 'department': _other_write('UPDATE departments SET name=? WHERE id=?', ('Измененное синтетическое', lab['dept']))
            elif mutation == 'event': _other_write('UPDATE events SET date=? WHERE history_entry_id IN (SELECT id FROM history_entries WHERE patient_id=?)', ('2026-11-01', pid))
            elif mutation == 'stage': _other_write('UPDATE patients SET stage=? WHERE id=?', ('appointment', pid))
            elif mutation == 'checklist': _other_write('UPDATE history_entries SET checklist_json=? WHERE patient_id=?', ('{"synthetic":true}', pid))
            else:
                # One leaves and one enters, total remains3 but identity differs.
                from backend.app import db, config, store
                from backend.app.models import PatientCreateIn
                c = db._connect(config.DB_PATH)
                try:
                    with c:
                        c.execute('UPDATE patients SET treating_doctor_user_id=? WHERE id=?', (lab['other'], pid))
                        store.insert_patient_rows(c, PatientCreateIn(**make_patient_payload(name='Новый синтетический', phone='+7 900 222-00-99')), lab['doctor'], created_by_user_id=None, creation_source='legacy')
                finally:c.close()
        return old(state, route, raw, params, denied)
    monkeypatch.setattr(read_access, '_persist', before);count = _count()
    r = lab['get']('&limit=1')
    assert r.status_code == 409 and r.json()['code'] == 'PATIENT_QUERY_CHANGED', r.text
    assert _count() == count; _safe(r)


@pytest.mark.parametrize('commit', [True, False])
def test_same_connection_data_version_covers_validation_lock_gap(lab, monkeypatch, commit):
    from backend.app import read_access

    # Keep the original ID. The Quality fence now compares GQ after acquiring
    # the lock. Inject the write after the release connection opens.
    original = read_access.closing
    writes = []

    def before_lock(conn):
        _other_write('UPDATE patients SET name=? WHERE id=?',
                     ('Gap synthetic', lab['ids'][-1]), commit=commit)
        writes.append(commit)
        return original(conn)

    monkeypatch.setattr(read_access, 'closing', before_lock)
    r = lab['get']('&limit=1')
    assert writes == [commit]
    assert r.status_code == (409 if commit else 200), r.text
    if commit:
        assert r.json()['code'] == 'PATIENT_QUERY_CHANGED'
        _safe(r)


@pytest.mark.parametrize('mutation', ['revoke_session', 'role', 'department'])
def test_auth_access_refusal_precedes_ordinary_query_change(lab, monkeypatch, mutation):
    from backend.app import read_access
    old = read_access._persist
    def before(state, route, raw, params, denied):
        if not denied and route.endswith('/_a1-lab'):
            _other_write('UPDATE patients SET name=? WHERE id=?', ('Concurrent synthetic', lab['ids'][-1]))
            if mutation == 'revoke_session':_other_write('DELETE FROM sessions WHERE user_id=?', (state.user.id,))
            elif mutation == 'role':_other_write('UPDATE users SET role_id=? WHERE id=?', ('caop_head', state.user.id))
            else:
                _other_write('UPDATE users SET must_change_password=1 WHERE id=?', (state.user.id,))
        return old(state, route, raw, params, denied)
    monkeypatch.setattr(read_access, '_persist', before)
    r = lab['get']('&limit=1')
    assert r.status_code == (401 if mutation == 'revoke_session' else 409)
    if mutation != 'revoke_session':assert r.json()['code'] == 'ACCESS_CONTEXT_CHANGED'
    _safe(r)


@pytest.mark.parametrize('fault', ['event', 'manifest', 'missing_manifest_row', 'missing_audit_row'])
def test_audit_failure_rolls_back_and_releases_no_body(lab, monkeypatch, fault):
    from types import SimpleNamespace
    from backend.app import audit, db, read_access
    before = _count(); manifests = get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]
    if fault == 'event':
        original = audit.record_required_or_fail
        def broken(conn, kind, **kw):
            if kind == read_access.RELEASE:raise audit.AuditWriteFailed()
            return original(conn, kind, **kw)
        monkeypatch.setattr(audit, 'record_required_or_fail', broken)
    else:
        original = db._connect
        class Broken(db._ClinicConnection):
            def execute(self, sql, *a, **kw):
                if fault == 'missing_audit_row' and sql == 'SELECT last_insert_rowid(), changes()':
                    return SimpleNamespace(fetchone=lambda: (0, 0))
                if sql.startswith('INSERT INTO read_access_manifests'):
                    if fault == 'missing_manifest_row':
                        return SimpleNamespace(rowcount=0)
                    if fault == 'manifest':
                        raise __import__('sqlite3').OperationalError('synthetic manifest failure')
                return super().execute(sql, *a, **kw)
        def connect(*a, **kw):kw['factory'] = Broken;return original(*a, **kw)
        monkeypatch.setattr(db, '_connect', connect)
    r = lab['get']('&limit=1'); assert r.status_code == 503; _safe(r)
    assert _count() == before
    assert get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0] == manifests


@pytest.mark.parametrize('mutation', ['missing_facets', 'hidden_patient', 'unbounded', 'bad_type', 'body_cap', 'second_record'])
def test_incomplete_or_invalid_dto_never_becomes_success(lab, monkeypatch, mutation):
    qp = lab['qp']
    def change(body, ctx):
        if mutation == 'missing_facets':del body['facets']
        elif mutation == 'hidden_patient':body['patients']['items'][0]['patient_id'] = 'not-in-scope'
        elif mutation == 'unbounded':body['patients']['items'] *= 101
        elif mutation == 'bad_type':body['patients']['page']['limit'] = True
        elif mutation == 'second_record':
            from backend.app import request_security
            request_security.current.get().scoped_release = object()
    lab['settings']['mutate'] = change
    if mutation == 'body_cap':monkeypatch.setattr(qp, 'MAX_BODY_BYTES', 100)
    before = _count();r = lab['get']('&limit=1')
    assert r.status_code == 503;_safe(r);assert _count() == before


def test_owner_unknown_and_hidden_are_indistinguishable(lab):
    from backend.app.models import PatientCreateIn
    from backend.app import store
    conn = get_conn()
    with conn:
        hidden = store.insert_patient_rows(conn, PatientCreateIn(**make_patient_payload(name='Скрытый синтетический', phone='+7 900 333-00-99')), lab['other'], created_by_user_id=None, creation_source='legacy')
    assert login(lab['client'], 'a1.doctor').status_code == 200
    for pid in ['unknown', hidden]:
        r = lab['get']('&view=patient_issues&owner_patient_id='+pid)
        assert r.status_code == 404; _safe(r)


def test_no_expensive_validation_under_write_lock(lab, monkeypatch):
    qp = lab['qp'];previous = qp._input;observed=[]
    def measured(conn, user, binding):
        # A separate writer can acquire/release the SQLite lock while hashing.
        _other_write('UPDATE patients SET name=name WHERE id=?', (lab['ids'][0],), commit=False)
        observed.append(True)
        return previous(conn, user, binding)
    monkeypatch.setattr(qp, '_input', measured)
    assert lab['get']('&limit=1').status_code == 200
    assert observed == [True]


def test_two_concurrent_releases_cover_audit_only_commits(lab, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from backend.app import read_access

    original = read_access._persist
    barrier = Barrier(2)

    def together(state, route, raw, params, denied):
        if not denied and route.endswith('/_a1-lab'):
            barrier.wait(timeout=10)
        return original(state, route, raw, params, denied)

    monkeypatch.setattr(read_access, '_persist', together)
    before = _count()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: lab['get']('&limit=1'), range(2)))

    assert [r.status_code for r in responses] == [200, 200]
    assert _count() == before + 2
    hashes = {hashlib.sha256(r.content).hexdigest() for r in responses}
    manifests = get_conn().execute(
        'SELECT snapshot_sha256 FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 2'
    ).fetchall()
    assert len(manifests) == 2
    assert {row[0] for row in manifests} == hashes


@pytest.mark.parametrize('model,payload', [
    ('Page', {'limit': True, 'offset': 0, 'total': 0, 'has_next': False, 'next_cursor': None}),
    ('Page', {'limit': 1, 'offset': False, 'total': 0, 'has_next': False, 'next_cursor': None}),
    ('Page', {'limit': 1, 'offset': 0, 'total': -1, 'has_next': False, 'next_cursor': None}),
    ('Page', {'limit': 1.0, 'offset': 0, 'total': 0, 'has_next': False, 'next_cursor': None}),
    ('Completeness', {'score': 100, 'filled': 6, 'total': 6.0, 'missing': []}),
    ('Completeness', {'score': 100, 'filled': 6, 'total': 6, 'missing': [], 'unexpected': 'text'}),
    ('GroupRow', {'group_id': 'a'*64, 'reason_codes': ['phone'], 'reason': 'same phone', 'member_total': 2, 'matching_member_total': 1, 'manual_review_required': 1}),
])
def test_dto_rejects_coercion_and_unknown_fields(client, model, payload):
    from backend.app import quality_models
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        getattr(quality_models, model).model_validate(payload, strict=True)
