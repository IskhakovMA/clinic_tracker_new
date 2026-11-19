"""Security regressions on disposable A1/A2 FastAPI adapters and synthetic SQLite."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from fastapi import Depends, Request

from .conftest import DEV_USERNAME, create_account, create_department, get_conn, login
from .test_quality_gq_m2_security import _assert_refused, _observe_release, _release_records
from .test_wp23a_scheduling_integrity import _client_for, _parallel
from .test_wp24a_patient_create import _world
from .test_wp24d_duplicate_review import _card
from .test_wp_i_i8a1_quality import _other_write, lab

_A2_PATH = '/api/data-quality/_a2-security-lab'


def _assert_rejects_extra_aggregate(lab, object_type, identity):
    from backend.app import request_security

    assert login(lab['client'], 'a1.doctor').status_code == 200
    normal = lab['get']('&limit=1')
    assert normal.status_code == 200
    before = _release_records()
    manifest_count = get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]
    observed = []

    def supply_unrelated_object(_connection, _user):
        state = request_security.current.get()
        assert state.scoped_release is not None
        state.aggregate_objects.add((object_type, identity))
        observed.append(True)

    lab['settings']['after'] = supply_unrelated_object
    response = lab['get']('&limit=1')
    _assert_refused(response, 503, 'QUALITY_READ_UNAVAILABLE')
    assert identity not in response.text
    assert observed == [True]
    assert _release_records() == before
    assert get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0] == manifest_count


def test_quality_manifest_rejects_foreign_aggregate_user(lab):
    outside = lab['other']
    _card(outside, name='Синтетический Другой Врач')
    _assert_rejects_extra_aggregate(lab, 'user', outside)


def test_quality_manifest_rejects_foreign_aggregate_department(lab):
    from backend.app import users

    outside = create_department('Синтетическое внешнее отделение')
    users.add_doctor_to_department(get_conn(), lab['other'], outside)
    _card(lab['other'], name='Синтетический Внешнего Отделения')
    _assert_rejects_extra_aggregate(lab, 'department_id', outside)


@pytest.fixture
def a2_lab(client):
    from backend.app import quality_builders, quality_pages
    from backend.app.deps import abuse_rate_limit, get_db, require_active_password

    settings = {'after_prepare': None}

    @client.app.get(_A2_PATH, dependencies=[Depends(abuse_rate_limit('i8a2_security_lab', 60))])
    def read_quality(request: Request, conn=Depends(get_db), user=Depends(require_active_password)):
        parsed = quality_pages.normalize(request)
        response = quality_pages.prepare(conn, user, parsed, quality_builders.build)
        if settings['after_prepare'] is not None:
            settings['after_prepare'](conn)
        return response

    return settings


def _read(client, **params):
    return client.get(_A2_PATH, params={'mode': 'quality-v1', **params})


def _a2_releases():
    from backend.app import config, db

    with closing(db._connect(config.DB_PATH)) as connection:
        rows = connection.execute(
            """SELECT e.actor_user_id, m.object_count, m.objects_json, m.snapshot_sha256
               FROM audit_events e LEFT JOIN read_access_manifests m ON m.audit_event_id=e.id
               WHERE e.event_type='medical_read_release' AND e.detail=?
               ORDER BY e.id""",
            ('operation=GET ' + _A2_PATH,),
        ).fetchall()
    return [dict(row) for row in rows]

def _manifest_objects(row):
    objects = json.loads(row['objects_json'])
    pairs = {(item['type'], item['id']) for item in objects}
    assert row['object_count'] == len(objects) == len(pairs)
    return pairs


@pytest.fixture
def a2_scope(a2_lab):
    department = create_department('Синтетическое Отделение Альфа')
    other_department = create_department('Синтетическое Отделение Бета')
    doctor = create_account(
        'a2.security.doctor', 'doctor', 'Синтетический Врач Альфа',
        department_ids=[department],
    )
    other_doctor = create_account(
        'a2.security.other', 'doctor', 'Синтетический Врач Бета',
        department_ids=[other_department],
    )
    patient = _card(doctor, name='Синтетический Пациент Альфа')
    other_patient = _card(doctor, name='Синтетический Пациент Гамма')
    hidden_patient = _card(other_doctor, name='Синтетический Пациент Бета')
    return {
        'doctor': doctor, 'other_doctor': other_doctor,
        'department': department, 'other_department': other_department,
        'patient': patient, 'other_patient': other_patient, 'hidden_patient': hidden_patient,
    }


def test_quality_a2_scope_and_release_manifest_for_all_six_roles(client, a2_lab):
    ids = _world()
    duplicate_policy = '7700000000051001'
    owned = _card(ids['doc.a'], policy=duplicate_policy, name='Синтетический Дубль Альфа')
    same_department = _card(
        ids['doc.a2'], policy=duplicate_policy, name='Синтетический Дубль Гамма',
    )
    other_department = _card(
        ids['doc.b'], policy=duplicate_policy, name='Синтетический Дубль Бета',
    )
    chair_patient = _card(ids['chair'], name='Синтетический Пациент Консилиума')
    create_account('a2.security.empty_head', 'department_head')
    all_patients = {owned, same_department, other_department, chair_patient}
    assignees = {
        owned: ids['doc.a'], same_department: ids['doc.a2'],
        other_department: ids['doc.b'], chair_patient: ids['chair'],
    }
    departments = {owned: ids['d1'], same_department: ids['d1'], other_department: ids['d2']}
    cases = [
        (DEV_USERNAME, ids['dev'], all_patients),
        ('deputy', ids['deputy'], all_patients),
        ('caop', ids['caop'], all_patients),
        ('head.1', ids['head.1'], {owned, same_department}),
        ('doc.a', ids['doc.a'], {owned}),
        ('chair', ids['chair'], {chair_patient}),
        ('a2.security.empty_head', None, set()),
    ]

    for username, actor_id, allowed in cases:
        assert login(client, username).status_code == 200
        before = len(_a2_releases())
        result = _read(client, limit=100)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['scope_summary']['total_patients'] == len(allowed)
        assert body['totals']['patients'] == len(allowed)
        assert {row['patient_id'] for row in body['patients']['items']} == allowed
        assert all(pid not in result.text for pid in all_patients - allowed)
        assert {row['user_id'] for row in body['facets']['doctors']['items']} == {
            assignees[pid] for pid in allowed
        }
        assert {row['department_id'] for row in body['facets']['departments']['items']} == {
            departments[pid] for pid in allowed if pid in departments
        }

        visible_group_size = len(allowed & {owned, same_department, other_department})
        groups = body['duplicate_groups']['items']
        assert len(groups) == int(visible_group_size >= 2)
        expected_objects = (
            {('patient', pid) for pid in allowed}
            | {('user', assignees[pid]) for pid in allowed}
            | {('department_id', departments[pid]) for pid in allowed if pid in departments}
        )
        if groups:
            assert groups[0]['member_total'] == visible_group_size
            assert 'patient_ids' not in groups[0]
            expected_objects.add(('group_id', groups[0]['group_id']))

        releases = _a2_releases()
        assert len(releases) == before + 1
        record = releases[-1]
        if actor_id is not None:
            assert record['actor_user_id'] == actor_id
        assert record['snapshot_sha256'] == hashlib.sha256(result.content).hexdigest()
        assert _manifest_objects(record) == expected_objects


@pytest.mark.parametrize('kind', ['patient_id', 'doctor_user_id', 'department_id'])
def test_quality_a2_outside_filter_indistinguishable_from_unknown(client, a2_scope, kind):
    assert login(client, 'a2.security.doctor').status_code == 200
    hidden = a2_scope[{
        'patient_id': 'hidden_patient', 'doctor_user_id': 'other_doctor',
        'department_id': 'other_department',
    }[kind]]
    selections = []
    for selected in (hidden, 'absent-synthetic-quality-object'):
        result = _read(client, **{kind: selected})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['query']['active_filters'] == [kind]
        assert selected not in result.text
        assert body['totals'] == {'patients': 0, 'findings': 0, 'duplicate_groups': 0}
        assert body['patients']['items'] == []
        assert body['scope_summary']['total_patients'] == 2
        selections.append((body['totals'], body['scope_summary'], body['facets']))
    assert selections[0][:2] == selections[1][:2]
    for facet in ('doctors', 'departments'):
        assert selections[0][2][facet]['items'] == selections[1][2][facet]['items']


def test_quality_a2_foreign_owner_is_same_neutral_404_as_unknown(client, a2_scope):
    assert login(client, 'a2.security.doctor').status_code == 200
    before = _a2_releases()
    results = []
    for owner in (a2_scope['hidden_patient'], 'absent-synthetic-quality-owner'):
        response = _read(client, view='patient_issues', owner_patient_id=owner)
        _assert_refused(response, 404)
        assert owner not in response.text
        results.append(response.content)
    assert results[0] == results[1]
    assert _a2_releases() == before


def test_quality_a2_rejects_unauthenticated_and_password_change_sessions(client, a2_lab):
    _assert_refused(_read(client), 401)
    create_account('a2.security.must_change', must_change_password=True)
    assert login(client, 'a2.security.must_change').status_code == 200
    _assert_refused(_read(client), 403)
    assert _a2_releases() == []


def test_quality_a2_continuation_binds_selector_query_session_and_expiry(client, a2_scope, monkeypatch):
    from backend.app import quality_pages

    assert login(client, 'a2.security.doctor').status_code == 200
    first = _read(client, limit=1)
    assert first.status_code == 200
    body = first.json()
    snapshot = body['snapshot']['token']
    cursor = body['patients']['page']['next_cursor']
    assert cursor
    continuation = {'limit': 1, 'snapshot': snapshot, 'cursor': cursor}
    second = _read(client, **continuation)
    assert second.status_code == 200
    actual = {
        row['patient_id'] for released in (body, second.json())
        for row in released['patients']['items']
    }
    assert actual == {a2_scope['patient'], a2_scope['other_patient']}
    assert second.json()['patients']['page']['has_next'] is False
    before = _a2_releases()

    for kwargs in ({'view': 'facets', 'facet': 'doctors'}, {'category': 'dates'}):
        rejected = _read(client, **continuation, **kwargs)
        _assert_refused(rejected, 409, 'PATIENT_QUERY_CHANGED')
        assert snapshot not in rejected.text and cursor not in rejected.text

    peer = _client_for(client, 'a2.security.other')
    try:
        rejected = _read(peer, **continuation)
        _assert_refused(rejected, 409, 'ACCESS_CONTEXT_CHANGED')
        assert snapshot not in rejected.text and cursor not in rejected.text
    finally:
        peer.close()

    now = quality_pages.clock.now_moscow()
    monkeypatch.setattr(
        quality_pages.clock, 'now_moscow',
        lambda: now + timedelta(seconds=quality_pages.ROOT_TTL + 1),
    )
    rejected = _read(client, **continuation)
    _assert_refused(rejected, 410, 'CURSOR_EXPIRED')
    assert snapshot not in rejected.text and cursor not in rejected.text
    assert _a2_releases() == before


@pytest.mark.parametrize(('change', 'status', 'code'), [
    ('source', 409, 'PATIENT_QUERY_CHANGED'),
    ('department', 409, 'ACCESS_CONTEXT_CHANGED'),
    ('session', 401, None),
])
def test_quality_a2_release_refuses_changed_source_or_access(
    client, a2_scope, a2_lab, change, status, code
):
    assert login(client, 'a2.security.doctor').status_code == 200
    before = _a2_releases()
    called = []

    def changed_after_prepare(_connection):
        _other_write(
            'UPDATE patients SET name=? WHERE id=?',
            ('Синтетический После Снимка', a2_scope['other_patient']),
        )
        if change == 'department':
            _other_write(
                'DELETE FROM user_departments WHERE user_id=? AND department_id=?',
                (a2_scope['doctor'], a2_scope['department']),
            )
        elif change == 'session':
            _other_write('DELETE FROM sessions WHERE user_id=?', (a2_scope['doctor'],))
        called.append(True)

    a2_lab['after_prepare'] = changed_after_prepare
    response = _read(client, limit=1)
    _assert_refused(response, status, code)
    assert called == [True]
    assert a2_scope['patient'] not in response.text
    assert _a2_releases() == before


def test_quality_a2_commit_fault_rolls_back_audit_and_manifest(client, a2_scope, monkeypatch):
    assert login(client, 'a2.security.doctor').status_code == 200
    before = _a2_releases()
    exercised = []

    def fail_commit(connection):
        e = connection.execute(
            """SELECT e.event_type, m.object_count FROM audit_events e
               JOIN read_access_manifests m ON m.audit_event_id=e.id
               ORDER BY e.id DESC LIMIT 1"""
        ).fetchone()
        assert e is not None and e['event_type'] == 'medical_read_release' and e['object_count'] >= 2
        exercised.append(True)
        raise sqlite3.OperationalError('synthetic A2 release commit fault')

    _observe_release(monkeypatch, before_commit=fail_commit)
    _assert_refused(_read(client, limit=1), 503)
    assert exercised == [True]
    assert _a2_releases() == before


def test_quality_a2_concurrent_readers_have_distinct_exact_manifests(client, a2_scope):
    from backend.app import quality_revision

    assert login(client, 'a2.security.doctor').status_code == 200
    peer = _client_for(client, 'a2.security.other')
    revision = quality_revision.require_valid(get_conn())
    try:
        responses = _parallel(lambda: _read(client, limit=1), lambda: _read(peer, limit=1))
        records = {record['actor_user_id']: record for record in _a2_releases()}
        assert len(records) == 2
        expected = [
            (
                a2_scope['doctor'],
                {('patient', a2_scope[name]) for name in ('patient', 'other_patient')}
                | {('user', a2_scope['doctor']), ('department_id', a2_scope['department'])},
            ),
            (
                a2_scope['other_doctor'],
                {
                    ('patient', a2_scope['hidden_patient']), ('user', a2_scope['other_doctor']),
                    ('department_id', a2_scope['other_department']),
                },
            ),
        ]
        for (response, _, _), (actor, pairs) in zip(responses, expected):
            assert response.status_code == 200, response.text
            body = response.json()
            snapshot = body['snapshot']
            assert set(snapshot) == {
                'token', 'revision', 'query_hash', 'computed_at', 'institution_day', 'timezone', 'expires_at'
            }
            assert revision.incarnation not in response.text
            assert records[actor]['snapshot_sha256'] == hashlib.sha256(response.content).hexdigest()
            assert _manifest_objects(records[actor]) == pairs
            assert revision.incarnation not in records[actor]['objects_json']
            assert snapshot['token'] not in records[actor]['objects_json']
    finally:
        peer.close()
