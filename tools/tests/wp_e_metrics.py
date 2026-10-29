"""Ручной воспроизводимый замер AUD-31 на TestClient и новой синтетической базе.

WP_E_METRICS_JSON=/absolute/result.json python -m pytest tools/tests/wp_e_metrics.py -q -s
Не входит в обычную коллекцию backend/tests. Исходный SHA проверяется этим же
сценарием из каталога его git archive с соответствующим PYTHONPATH.
"""
import json
import os
from datetime import date, datetime
from pathlib import Path
import time

pytest_plugins = ['backend.tests.conftest']
from backend.tests.conftest import get_conn, make_patient_payload


def test_metrics(auth_client, monkeypatch):
    from backend.app import clock, config, db, deps, store
    from backend.app.models import PatientCreateIn
    from backend.app.services import internal_notifications as inbox
    output = Path(os.environ['WP_E_METRICS_JSON'])
    assert output.is_absolute(), 'WP_E_METRICS_JSON must be an absolute path'
    monkeypatch.setattr(clock, 'now_moscow', lambda: datetime.fromisoformat('2026-10-02T01:00:00+03:00'))
    # Исходная версия еще не использует clock, но получает тот же календарный день.
    monkeypatch.setattr(inbox, 'today_local', lambda: date(2026, 10, 2))
    store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(
        name='Синтетический 0', events=[{'date': '2026-10-01', 'time': '10:00'}])), None)
    conn = db._connect(config.DB_PATH)
    try:
        tables = [('patients', None), ('history_entries', 'patient_id'), ('events', 'history_entry_id')]
        samples = {table: dict(conn.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone()) for table, _ in tables}
        with conn:
            for table, ref in tables:
                columns = list(samples[table]); values = []
                for index in range(1, 4000):
                    row = samples[table].copy(); row['id'] = f'probe_{table}_{index}'
                    if table == 'patients': row['name'] = f'Синтетический {index}'
                    if ref: row[ref] = f'probe_{"patients" if table == "history_entries" else "history_entries"}_{index}'
                    values.append([row[column] for column in columns])
                conn.executemany('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', values)
    finally:
        conn.close()
    trace, changes = [], []
    real_connect = deps._connect

    class Traced:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, key): return getattr(self.connection, key)
        def __enter__(self): self.connection.__enter__(); return self
        def __exit__(self, *args): return self.connection.__exit__(*args)
        def close(self):
            changes.append(self.connection.total_changes)
            self.connection.close()

    def traced_connect(path):
        connection = real_connect(path)
        connection.set_trace_callback(lambda sql: trace.append((time.perf_counter(), sql)))
        return Traced(connection)

    monkeypatch.setattr(deps, '_connect', traced_connect)
    results = []
    for kind in ['cold', 'repeat1', 'repeat2']:
        trace.clear(); changes.clear()
        get_conn().set_trace_callback(lambda sql: trace.append((time.perf_counter(), sql)))
        started = time.perf_counter()
        response = auth_client.get('/api/internal-notifications/summary')
        elapsed = time.perf_counter() - started
        get_conn().set_trace_callback(None)
        assert response.status_code == 200, response.text
        writing, begin, is_write = [], None, False
        for stamp, sql in trace:
            if sql.upper().startswith('BEGIN'): begin = stamp; is_write = 'IMMEDIATE' in sql.upper()
            if 'INTERNAL_NOTIFICATION_STATE' in sql.upper() and sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                is_write = True
            if sql.upper().startswith('COMMIT') and begin is not None:
                if is_write: writing.append(stamp - begin)
                begin, is_write = None, False
        results.append(dict(kind=kind, patients=4000, events=4000, conditions=response.json()['total_count'],
                            sql_statements=len(trace), changed_rows=sum(changes), response_seconds=elapsed,
                            write_transaction_seconds=sum(writing)))
    output.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))
