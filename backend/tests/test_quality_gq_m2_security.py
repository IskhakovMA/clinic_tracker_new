"""GQ-M2 release invariants on the existing isolated HTTP lab route."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import sqlite3
from threading import Barrier
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    DEV_USERNAME,
    TEST_MOSCOW_NOW,
    app_stopped,
    get_conn,
    login,
    make_patient_payload,
)
from .test_wp_b_database_safety import backup_key
from .test_wp_i_i8a1_quality import lab, _other_write, _safe


class _ReleaseObserver:
    """Forward real SQLite statements and expose deterministic fixture faults."""

    def __init__(self, connection, before_begin, after_manifest, before_commit):
        self.connection = connection
        self.before_begin = before_begin
        self.after_manifest = after_manifest
        self.before_commit = before_commit

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if self.before_begin and sql == 'BEGIN IMMEDIATE':
            assert not self.connection.in_transaction
            self.before_begin()
        result = self.connection.execute(sql, parameters)
        if self.after_manifest and sql.startswith('INSERT INTO read_access_manifests'):
            assert self.connection.in_transaction
            self.after_manifest(self.connection)
        return result

    def commit(self):
        if self.before_commit:
            assert self.connection.in_transaction
            self.before_commit(self.connection)
        self.connection.commit()


def _observe_release(monkeypatch, *, before_begin=None, after_manifest=None, before_commit=None):
    from backend.app import read_access

    @contextmanager
    def observer(connection):
        try:
            yield _ReleaseObserver(connection, before_begin, after_manifest, before_commit)
        finally:
            connection.close()

    monkeypatch.setattr(read_access, 'closing', observer)


def _once(callback):
    pending = True

    def run(*args):
        nonlocal pending
        if pending:
            pending = False
            callback(*args)

    return run


def _release_records():
    """Independent observation of committed records for the lab endpoint."""
    from backend.app import config, db

    connection = db._connect(config.DB_PATH)
    try:
        rows = connection.execute(
            '''SELECT e.id, e.actor_user_id, m.object_count, m.objects_json, m.snapshot_sha256
                 FROM audit_events e
                 LEFT JOIN read_access_manifests m ON m.audit_event_id=e.id
                WHERE e.event_type='medical_read_release'
                  AND e.detail='operation=GET /api/data-quality/_a1-lab'
                ORDER BY e.id'''
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _manifest_patients(record):
    return {item['id'] for item in json.loads(record['objects_json']) if item['type'] == 'patient'}


def _assert_refused(response, status, code=None):
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()['code'] == code
    assert set(response.json()) <= {'detail', 'code'}
    _safe(response)


def _continuation(body):
    return '&limit=1&' + urlencode({
        'snapshot': body['snapshot']['token'],
        'cursor': body['patients']['page']['next_cursor'],
    })


@pytest.mark.parametrize('action,status,code', [
    ('clinical_commit', 409, 'PATIENT_QUERY_CHANGED'),
    ('reassignment', 409, 'PATIENT_QUERY_CHANGED'),
    ('rollback', 200, None),
    ('session_activity', 200, None),
    ('department_removed', 409, 'ACCESS_CONTEXT_CHANGED'),
    ('session_removed', 401, None),
])
def test_continuation_release_lock_edge(lab, monkeypatch, action, status, code):
    from backend.app import quality_revision, security

    assert login(lab['client'], 'a1.doctor').status_code == 200
    head = lab['get']('&limit=1')
    assert head.status_code == 200, head.text
    continuation = _continuation(head.json())
    previous = _release_records()
    initial = quality_revision.require_valid(get_conn())
    patient = sorted(lab['ids'])[-1]
    assert patient != head.json()['patients']['items'][0]['patient_id']
    observed = []

    def at_lock_edge():
        if action == 'reassignment':
            _other_write(
                'UPDATE patients SET treating_doctor_user_id=? WHERE id=?',
                (lab['other'], patient),
            )
        elif action == 'session_activity':
            _other_write(
                'UPDATE sessions SET last_seen_at=? WHERE user_id=?',
                (security.utc_now().isoformat(), lab['doctor']),
            )
        else:
            _other_write(
                'UPDATE patients SET name=? WHERE id=?',
                ('Синтетический Lock Edge', patient),
                commit=action != 'rollback',
            )
            if action == 'department_removed':
                _other_write(
                    'DELETE FROM user_departments WHERE user_id=? AND department_id=?',
                    (lab['doctor'], lab['dept']),
                )
            if action == 'session_removed':
                _other_write('DELETE FROM sessions WHERE user_id=?', (lab['doctor'],))
        observed.append(True)

    _observe_release(monkeypatch, before_begin=_once(at_lock_edge))
    response = lab['get'](continuation)
    assert observed == [True]
    records = _release_records()
    actual = quality_revision.require_valid(get_conn())
    if status == 200:
        assert response.status_code == 200, response.text
        assert actual == initial
        assert response.json()['snapshot']['revision'] == head.json()['snapshot']['revision']
        assert response.json()['patients']['page']['offset'] == 1
        assert records[:-1] == previous
        assert records[-1]['snapshot_sha256'] == hashlib.sha256(response.content).hexdigest()
        assert _manifest_patients(records[-1]) == set(lab['ids'])
    else:
        _assert_refused(response, status, code)
        assert actual.revision > initial.revision
        assert records == previous


@pytest.mark.parametrize('fault', ['source_trigger', 'singleton'])
def test_revision_damage_at_release_lock_edge_fails_closed(lab, monkeypatch, fault):
    from backend.app import config, db, quality_revision

    previous = _release_records()
    observed = []

    def damage_fixture():
        conn = db._connect(config.DB_PATH)
        try:
            with conn:
                if fault == 'source_trigger':
                    conn.execute('DROP TRIGGER trg_gq_patients_update')
                else:
                    # Keep all expected hook definitions and isolate row validity.
                    no_delete = dict(quality_revision.trigger_definitions())['trg_gq_state_no_delete']
                    conn.execute('DROP TRIGGER trg_gq_state_no_delete')
                    conn.execute('DELETE FROM quality_revision_state')
                    conn.execute(no_delete)
            with pytest.raises(quality_revision.QualityRevisionError):
                quality_revision.require_valid(conn, structure=True)
            observed.append(True)
        finally:
            conn.close()

    _observe_release(monkeypatch, before_begin=_once(damage_fixture))
    _assert_refused(lab['get']('&limit=1'), 503)
    assert observed == [True]
    assert _release_records() == previous


@pytest.mark.parametrize('effect,status,code', [
    ('source', 409, 'PATIENT_QUERY_CHANGED'),
    ('role', 409, 'ACCESS_CONTEXT_CHANGED'),
    ('revocation', 401, None),
])
def test_post_manifest_effects_roll_back_release_and_clinical_source(lab, monkeypatch, effect, status, code):
    from backend.app import quality_revision

    assert login(lab['client'], 'a1.doctor').status_code == 200
    db = get_conn()
    initial = quality_revision.require_valid(db)
    patient = lab['ids'][-1]
    original_name = db.execute('SELECT name FROM patients WHERE id=?', (patient,)).fetchone()[0]
    sessions = db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (lab['doctor'],)).fetchone()[0]
    previous = _release_records()
    observed = []

    def inject_fixture_effect(connection):
        event = connection.execute(
            '''SELECT a.event_type FROM read_access_manifests m
                JOIN audit_events a ON a.id=m.audit_event_id ORDER BY a.id DESC LIMIT 1'''
        ).fetchone()
        assert event[0] == 'medical_read_release'
        connection.execute('UPDATE patients SET name=? WHERE id=?', ('Синтетический Fixture Effect', patient))
        assert quality_revision.require_valid(connection).revision > initial.revision
        if effect == 'role':
            connection.execute('UPDATE users SET role_id=? WHERE id=?', ('caop_head', lab['doctor']))
        if effect == 'revocation':
            connection.execute('DELETE FROM sessions WHERE user_id=?', (lab['doctor'],))
        observed.append(True)

    _observe_release(monkeypatch, after_manifest=_once(inject_fixture_effect))
    _assert_refused(lab['get']('&limit=1'), status, code)
    assert observed == [True]
    assert _release_records() == previous
    assert quality_revision.require_valid(db) == initial
    assert db.execute('SELECT name FROM patients WHERE id=?', (patient,)).fetchone()[0] == original_name
    assert db.execute('SELECT role_id FROM users WHERE id=?', (lab['doctor'],)).fetchone()[0] == 'doctor'
    assert db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (lab['doctor'],)).fetchone()[0] == sessions


def test_commit_fault_after_audit_and_manifest_hides_body(lab, monkeypatch):
    previous = _release_records()
    observed = []

    def fail_commit(connection):
        event = connection.execute(
            '''SELECT a.event_type FROM read_access_manifests m
                JOIN audit_events a ON a.id=m.audit_event_id ORDER BY a.id DESC LIMIT 1'''
        ).fetchone()
        assert event[0] == 'medical_read_release'
        observed.append(True)
        raise sqlite3.OperationalError('synthetic release commit failure')

    _observe_release(monkeypatch, before_commit=_once(fail_commit))
    _assert_refused(lab['get']('&limit=1'), 503)
    assert observed == [True]
    assert _release_records() == previous


@pytest.mark.parametrize('change,status,code', [
    ('institution_day', 409, 'PATIENT_QUERY_CHANGED'),
    ('ttl', 410, 'CURSOR_EXPIRED'),
])
def test_final_release_fence_uses_current_day_and_ttl(lab, monkeypatch, change, status, code):
    from backend.app import clock

    if change == 'institution_day':
        current = [TEST_MOSCOW_NOW.replace(hour=23, minute=59, second=59)]
        jump = timedelta(seconds=2)
    else:
        current = [TEST_MOSCOW_NOW]
        jump = timedelta(seconds=lab['qp'].ROOT_TTL + 1)
    monkeypatch.setattr(clock, 'now_moscow', lambda: current[0])
    previous = _release_records()

    def advance(_connection):
        current[0] += jump

    _observe_release(monkeypatch, after_manifest=_once(advance))
    _assert_refused(lab['get']('&limit=1'), status, code)
    assert _release_records() == previous


def _hidden_patient(lab):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    conn = get_conn()
    with conn:
        return store.insert_patient_rows(
            conn,
            PatientCreateIn(**make_patient_payload(name='Синтетический Вне Области', phone='+7 900 733-00-99')),
            lab['other'], created_by_user_id=None, creation_source='legacy',
        )


def test_off_scope_commit_between_requests_preserves_opaque_revision(lab):
    from backend.app import quality_revision

    hidden_id = _hidden_patient(lab)
    assert login(lab['client'], 'a1.doctor').status_code == 200
    first = lab['get']('&limit=1')
    assert first.status_code == 200, first.text
    body = first.json()
    previous_generation = quality_revision.require_valid(get_conn())
    _other_write('UPDATE patients SET name=? WHERE id=?', ('Синтетический Обновленный', hidden_id))
    assert quality_revision.require_valid(get_conn()).revision > previous_generation.revision

    second = lab['get'](_continuation(body))
    assert second.status_code == 200, second.text
    assert second.json()['snapshot']['revision'] == body['snapshot']['revision']
    raw_snapshot = json.dumps(body['snapshot'])
    assert set(body['snapshot']) == {'token', 'revision', 'query_hash', 'computed_at', 'institution_day', 'timezone', 'expires_at'}
    assert previous_generation.incarnation not in raw_snapshot
    assert hidden_id not in second.text
    assert _manifest_patients(_release_records()[-1]) == set(lab['ids'])


def test_two_users_release_distinct_scope_and_manifest(lab, monkeypatch):
    from backend.app.main import app

    hidden_id = _hidden_patient(lab)
    second_client = TestClient(app, base_url='http://127.0.0.1', raise_server_exceptions=False)
    try:
        assert login(second_client, 'a1.doctor').status_code == 200
        previous = _release_records()
        barrier = Barrier(2)
        _observe_release(monkeypatch, before_begin=lambda: barrier.wait(timeout=10))

        def get(requester):
            return requester.get('/api/data-quality/_a1-lab?mode=quality-v1&limit=1')

        with ThreadPoolExecutor(max_workers=2) as pool:
            developer, doctor = list(pool.map(get, (lab['client'], second_client)))
    finally:
        second_client.close()

    assert [developer.status_code, doctor.status_code] == [200, 200]
    assert [developer.json()['totals']['patients'], doctor.json()['totals']['patients']] == [4, 3]
    assert hidden_id not in doctor.text
    records = _release_records()
    assert records[:len(previous)] == previous and len(records) == len(previous) + 2
    proofs = {x['snapshot_sha256']: x for x in records[-2:]}
    doctor_record = proofs[hashlib.sha256(doctor.content).hexdigest()]
    developer_record = proofs[hashlib.sha256(developer.content).hexdigest()]
    assert doctor_record['actor_user_id'] == lab['doctor']
    assert developer_record['actor_user_id'] != lab['doctor']
    assert _manifest_patients(doctor_record) == set(lab['ids'])
    assert _manifest_patients(developer_record) == {*lab['ids'], hidden_id}


def test_quality_scope_proof_does_not_remove_legacy_object_guard(lab, monkeypatch):
    from backend.app import policy

    hidden_id = _hidden_patient(lab)
    assert login(lab['client'], 'a1.doctor').status_code == 200
    calls = []
    original = policy.can_read_patient

    def spy(conn, user, patient_id):
        calls.append(patient_id)
        return original(conn, user, patient_id)

    monkeypatch.setattr(policy, 'can_read_patient', spy)
    modern = lab['get']('&limit=1')
    assert modern.status_code == 200, modern.text
    assert calls == []
    assert _manifest_patients(_release_records()[-1]) == set(lab['ids'])
    assert hidden_id not in modern.text

    permitted = lab['client'].get('/api/data-quality/patients/' + lab['ids'][0])
    refused = lab['client'].get('/api/data-quality/patients/' + hidden_id)
    assert [permitted.status_code, refused.status_code] == [200, 404]
    assert calls.count(lab['ids'][0]) >= 2  # route and release fences both remain
    assert hidden_id in calls


@pytest.mark.parametrize('action', ['reverify', 'restore'])
def test_admission_generation_separates_old_quality_continuation(lab, tmp_path, backup_key, action):
    from backend.app import config, maintenance, operations_state, quality_revision

    first = lab['get']('&limit=1')
    assert first.status_code == 200, first.text
    continuation = _continuation(first.json())
    previous = operations_state.require_permit()
    previous_state = quality_revision.require_valid(get_conn())
    if action == 'restore':
        backup = maintenance.backup_database(config.DB_PATH, tmp_path / 'synthetic-m2.cbk')
        _other_write('UPDATE patients SET name=? WHERE id=?', ('Синтетический Временный', lab['ids'][0]))
    with app_stopped():
        if action == 'restore':
            maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
            with pytest.raises(operations_state.AdmissionRequired):
                operations_state.require_permit()
        operations_state.verify_database(confirm_offline=True)
        assert operations_state.require_permit()['generation'] != previous['generation']

    assert quality_revision.require_valid(get_conn()) == previous_state
    response = lab['get'](continuation)
    if action == 'restore':
        _assert_refused(response, 401)
        assert login(lab['client'], DEV_USERNAME).status_code == 200
        response = lab['get'](continuation)
    _assert_refused(response, 409, 'ACCESS_CONTEXT_CHANGED')
    assert lab['get']('&limit=1').status_code == 200


@pytest.mark.parametrize('change,status,code', [
    ('department_removed', 409, 'ACCESS_CONTEXT_CHANGED'),
    ('session_removed', 401, None),
])
def test_fresh_prepare_access_precedes_continuation_query_conflict(lab, monkeypatch, change, status, code):
    assert login(lab['client'], 'a1.doctor').status_code == 200
    first = lab['get']('&limit=1')
    assert first.status_code == 200, first.text
    previous = _release_records()
    prepare = lab['qp'].prepare
    changed = []

    def with_changed_access(*args):
        if change == 'department_removed':
            _other_write(
                'DELETE FROM user_departments WHERE user_id=? AND department_id=?',
                (lab['doctor'], lab['dept']),
            )
        else:
            _other_write('DELETE FROM sessions WHERE user_id=?', (lab['doctor'],))
        changed.append(True)
        return prepare(*args)

    monkeypatch.setattr(lab['qp'], 'prepare', with_changed_access)
    response = lab['get'](_continuation(first.json()) + '&category=dates')
    _assert_refused(response, status, code)
    assert changed == [True]
    assert _release_records() == previous


def test_quality_release_rejects_manifest_outside_prepared_scope(lab):
    from backend.app import request_security

    outside = _hidden_patient(lab)
    assert login(lab['client'], 'a1.doctor').status_code == 200
    previous = _release_records()
    observed = []

    def wrong_contributor(_connection, _user):
        state = request_security.current.get()
        assert state.scoped_release is not None
        state.aggregate_objects.add(('patient', outside))
        observed.append(True)

    lab['settings']['after'] = wrong_contributor
    response = lab['get']('&limit=1')
    _assert_refused(response, 503, 'QUALITY_READ_UNAVAILABLE')
    assert outside not in response.text
    assert observed == [True]
    assert _release_records() == previous


def test_scope_proof_matches_prior_object_policy_for_all_six_roles(client):
    from backend.app import patient_pages, policy, quality_pages
    from .conftest import create_account
    from .test_wp24d_duplicate_review import _card, _mark, _setup

    ids, first, erroneous, candidate, developer, deputy = _setup(client)
    try:
        assert _mark(deputy, candidate, erroneous).status_code == 200
        other_department = _card(ids['doc.b'], name='Синтетический Другого Отделения')
        chair_patient = _card(ids['chair'], name='Синтетический Консилиума')
        overlap = create_account('m2.head.overlap', 'department_head', department_ids=[ids['d1'], ids['d2']])
        empty = create_account('m2.head.empty', 'department_head')
        global_scope = {first, other_department, chair_patient}
        known = {
            ids['dev']: global_scope,
            ids['deputy']: global_scope,
            ids['caop']: global_scope,
            ids['head.1']: {first},
            ids['doc.a']: {first},
            ids['chair']: {chair_patient},
            overlap: {first, other_department},
            empty: set(),
        }
        conn = get_conn()
        with patient_pages.snapshot(conn):
            candidates = {row[0] for row in conn.execute('SELECT id FROM patients')}
            assert candidates == global_scope | {erroneous}
            for actor, expected in known.items():
                user = policy.load_current_user(conn, actor)
                projection = quality_pages._input(conn, user, 'synthetic-scope-proof')
                old_result = {patient for patient in candidates if policy.can_read_patient(conn, user, patient)}
                assert set(projection.patients) == old_result == expected, user.role_id
    finally:
        developer.close()
        deputy.close()
