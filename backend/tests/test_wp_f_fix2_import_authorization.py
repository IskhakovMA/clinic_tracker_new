"""WP-F FIX2: explicit request/SQLite synchronization; synthetic FastAPI only."""
from contextlib import contextmanager
import sqlite3
import threading

import pytest

from .conftest import get_conn
from .test_wp24a_patient_create import _world
from .test_wp24b_idempotency import _file, _event_row, _import, _migrate, _key, _no_import_traces
from .test_wp23a_scheduling_integrity import _client_for


def send(tab, raw, operation, key):
    return _import(tab, raw, key=key) if operation == 'import' else _migrate(tab, raw, key)


def revoke(conn, actor_id, change):
    if change == 'department':
        conn.execute('DELETE FROM department_heads WHERE user_id=?', (actor_id,))
    else:
        conn.execute("UPDATE users SET role_id='consilium_chair' WHERE id=?", (actor_id,))


@pytest.mark.parametrize('phase', ['before_load', 'before_transaction'])
@pytest.mark.parametrize('change', ['department', 'role'])
@pytest.mark.parametrize('operation', ['import', 'migration'])
@pytest.mark.parametrize('with_key', [False, True], ids=['no_key', 'key'])
def test_revocation_at_explicit_boundaries_is_403_without_import(client, monkeypatch, phase, change, operation, with_key):
    from backend.app import policy, importexport, config
    from backend.app.services import import_operations
    ids = _world(); tab = _client_for(client, 'head.1')
    raw = _file(_event_row(name='Синтетик Закрытый FIX2', doctor=ids['doc.a']))
    before = _no_import_traces()
    reached, release, begin_attempted, acquired = (threading.Event() for _ in range(4))
    inspected, executed, reloads = [], [], []
    inspect, commit = importexport.inspect_import, importexport.commit_import
    load, reload, transaction = policy.load_current_user, policy.reload_current_user, import_operations.transaction
    def inspect_spy(*a, **kw):
        inspected.append(True); return inspect(*a, **kw)
    def commit_spy(*a, **kw):
        executed.append(True); return commit(*a, **kw)
    def reload_spy(conn, actor):
        assert conn.in_transaction  # authorization must follow actual BEGIN IMMEDIATE
        fresh = reload(conn, actor); reloads.append(fresh); return fresh
    def load_boundary(conn, user_id):
        if user_id == ids['head.1']:
            reached.set(); assert release.wait(15), 'actor-load gate was not released'
        return load(conn, user_id)
    @contextmanager
    def transaction_boundary(conn):
        reached.set(); assert release.wait(15), 'transaction gate was not released'
        conn.set_trace_callback(lambda sql: begin_attempted.set() if sql.strip().upper() == 'BEGIN IMMEDIATE' else None)
        try:
            with transaction(conn) as inner:
                acquired.set(); yield inner
        finally:
            conn.set_trace_callback(None)
    monkeypatch.setattr(importexport, 'inspect_import', inspect_spy)
    monkeypatch.setattr(importexport, 'commit_import', commit_spy)
    monkeypatch.setattr(policy, 'reload_current_user', reload_spy)
    if phase == 'before_load': monkeypatch.setattr(policy, 'load_current_user', load_boundary)
    else: monkeypatch.setattr(import_operations, 'transaction', transaction_boundary)
    box = {}
    def request():
        try: box['response'] = send(tab, raw, operation, _key() if with_key else None)
        except BaseException as exc: box['error'] = exc
    thread = threading.Thread(target=request); thread.start()
    writer = None
    try:
        assert reached.wait(15), 'request did not reach explicit gate'
        writer = sqlite3.connect(config.DB_PATH, isolation_level=None)
        writer.execute('BEGIN IMMEDIATE'); revoke(writer, ids['head.1'], change)
        if phase == 'before_transaction':
            release.set()
            assert begin_attempted.wait(15), 'request did not attempt SQLite write lock'
            assert not acquired.is_set()  # independent writer still owns the lock
        writer.execute('COMMIT')
    finally:
        if writer is not None: writer.close()
        release.set(); thread.join(20)
    assert not thread.is_alive()
    if 'error' in box: raise box['error']
    response = box['response']
    assert response.status_code == 403 and response.json() == {'detail': 'Forbidden'}
    assert 'Синтетик Закрытый FIX2' not in response.text and 'C34.1' not in response.text
    assert _no_import_traces() == before and not executed
    if phase == 'before_load': assert not inspected and not reloads
    else:
        assert inspected == [True] and acquired.is_set() and len(reloads) == 1
        if change == 'department': assert reloads[0].department_ids == ()
        else: assert reloads == [None]


@pytest.mark.parametrize('who', ['head.1', 'head_none', 'doc.a', 'doctor_none', 'dev.tester'])
@pytest.mark.parametrize('operation', ['import', 'migration'])
@pytest.mark.parametrize('with_key', [False, True], ids=['no_key', 'key'])
def test_current_role_matrix_preserves_permitted_imports(client, who, operation, with_key):
    ids = _world()
    login = 'head.1' if who == 'head_none' else 'doc.a' if who == 'doctor_none' else who
    if who == 'head_none': get_conn().execute('DELETE FROM department_heads WHERE user_id=?', (ids['head.1'],))
    if who == 'doctor_none': get_conn().execute('DELETE FROM user_departments WHERE user_id=?', (ids['doc.a'],))
    get_conn().commit()
    tab = _client_for(client, login)
    raw = _file(_event_row(name='Синтетик Разрешенный FIX2', doctor=ids['doc.a']))
    before = _no_import_traces(); response = send(tab, raw, operation, _key() if with_key else None)
    if who == 'head_none':
        assert response.status_code == 403 and response.json() == {'detail': 'Forbidden'}
        assert 'Синтетик Разрешенный FIX2' not in response.text and _no_import_traces() == before
    else:
        assert response.status_code == 200 and response.json()['added'] == 1
        row = get_conn().execute('SELECT name, treating_doctor_user_id, created_by_user_id FROM patients').fetchone()
        actor_id = ids['dev'] if who == 'dev.tester' else ids['doc.a'] if who == 'doctor_none' else ids[who]
        assert tuple(row) == ('Синтетик Разрешенный FIX2', ids['doc.a'], actor_id)
        after = _no_import_traces()
        assert after['patients'] == before['patients'] + 1
        assert after['files'] == before['files'] + 1 and after['keys'] == before['keys'] + int(with_key)


@pytest.mark.parametrize('path', ['/api/import/preview', '/api/migrate/localstorage/preview'])
def test_head_without_departments_is_denied_before_preview_validation(client, monkeypatch, path):
    from backend.app import importexport
    ids = _world(); get_conn().execute('DELETE FROM department_heads WHERE user_id=?', (ids['head.1'],)); get_conn().commit()
    tab = _client_for(client, 'head.1'); before = _no_import_traces()
    def forbidden(*a, **kw): raise AssertionError('unauthorized request reached document validation')
    monkeypatch.setattr(importexport, 'inspect_import', forbidden)
    response = tab.post(path, json={'raw': 'private-medical-marker'}, headers=tab.csrf)
    assert response.status_code == 403 and response.json() == {'detail': 'Forbidden'}
    assert 'private-medical-marker' not in response.text and _no_import_traces() == before


@pytest.mark.parametrize('who', ['head.1', 'head_none', 'doc.a', 'doctor_none', 'dev.tester'])
def test_import_modes_keep_existing_role_boundaries(client, who):
    from backend.app import policy
    ids = _world()
    if who == 'head_none': get_conn().execute('DELETE FROM department_heads WHERE user_id=?', (ids['head.1'],))
    if who == 'doctor_none': get_conn().execute('DELETE FROM user_departments WHERE user_id=?', (ids['doc.a'],))
    get_conn().commit()
    actor_id = ids['dev'] if who == 'dev.tester' else ids['head.1'] if who == 'head_none' else ids['doc.a'] if who == 'doctor_none' else ids[who]
    user = policy.load_current_user(get_conn(), actor_id)
    assert [policy.can_import(user, mode) for mode in ['add', 'update', 'replace']] == (
        [True, True, True] if who == 'dev.tester' else [False, False, False] if who == 'head_none' else [True, False, False])
