"""WP-E FIX1: real API mutations and deterministic worker boundaries, only mock."""
from datetime import datetime, timedelta
import sqlite3
import pytest
from .conftest import get_conn, event_patch, card_version, app_stopped, synthetic_export_key
from .test_wp_e_time_notifications import freeze, stand, recorder, legacy_row


def prepared(client, monkeypatch):
    from backend.app.services import notifications as n
    state = freeze(monkeypatch, '2026-10-01T21:00:00+03:00')
    p = stand(client, day='2026-10-02', time='10:00')
    assert n.generate_due_reminders(get_conn())['created'] == 1
    return p, state, recorder()


@pytest.mark.parametrize('day,time', [('2026-10-02','07:00'), ('2026-10-02','13:00'), ('2026-10-03','10:00')])
def test_real_move_has_distinct_provenance_and_keeps_sent_history(auth_client, monkeypatch, day, time):
    from backend.app.services import notifications as n
    p, state, provider = prepared(auth_client, monkeypatch)
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    old = dict(get_conn().execute('SELECT * FROM notifications').fetchone())
    assert old['event_at'] == '2026-10-02T07:00:00+00:00'
    assert event_patch(auth_client, p['id'], 'date', day).status_code == 200
    assert event_patch(auth_client, p['id'], 'time', time).status_code == 200
    assert n.generate_due_reminders(get_conn())['created'] == 1
    assert n.get_notification_raw(get_conn(), old['id']) == old
    state[0] = datetime.fromisoformat(day + 'T' + time + '+03:00') - timedelta(hours=24)
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    assert len(provider.sent) == 2 and time in provider.sent[-1].body
    assert n.generate_due_reminders(get_conn())['created'] == 0
    assert n.process_one(get_conn(), provider) is None


def test_successive_moves_and_return_to_sent_schedule_do_not_replay(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    p, _, provider = prepared(auth_client, monkeypatch)
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    for time in ['07:00', '13:00', '16:00']:
        assert event_patch(auth_client, p['id'], 'time', time).status_code == 200
        assert n.generate_due_reminders(get_conn())['created'] == 1
        assert n.process_one(get_conn(), provider)['status'] == 'sent'
    assert event_patch(auth_client, p['id'], 'time', '10:00').status_code == 200
    outcome = n.generate_due_reminders(get_conn())
    assert outcome['created'] == 0 and outcome['duplicates'] == 1
    assert len(provider.sent) == 4
    assert get_conn().execute("SELECT COUNT(*) FROM notifications WHERE status = 'sent'").fetchone()[0] == 4


def test_return_to_system_cancelled_schedule_is_allowed(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    p, _, provider = prepared(auth_client, monkeypatch)
    for time in ['07:00', '10:00']:
        assert event_patch(auth_client, p['id'], 'time', time).status_code == 200
        assert n.generate_due_reminders(get_conn())['created'] == 1
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    assert len(provider.sent) == 1
    assert get_conn().execute("SELECT COUNT(*) FROM notifications WHERE status='cancelled' AND error_code='event_rescheduled'").fetchone()[0] == 2


@pytest.mark.parametrize('boundary', ['before_claim', 'after_claim', 'between_reads', 'after_second_read', 'provider_factory'])
def test_move_at_worker_boundary_never_reuses_claimed_schedule(auth_client, monkeypatch, boundary):
    from backend.app.services import notifications as n
    p, state, provider = prepared(auth_client, monkeypatch)
    def move():
        assert event_patch(auth_client, p['id'], 'time', '17:00').status_code == 200
    claim, read, render = n.claim_next, n._current_event_time, n.render_template
    if boundary == 'before_claim':
        move()
    elif boundary == 'after_claim':
        def intercept(conn):
            row = claim(conn); move(); return row
        monkeypatch.setattr(n, 'claim_next', intercept)
    elif boundary == 'between_reads':
        def intercept(*a, **kw):
            body = render(*a, **kw); move(); return body
        monkeypatch.setattr(n, 'render_template', intercept)
    elif boundary == 'after_second_read':
        calls = []
        def intercept(*a):
            result = read(*a); calls.append(1)
            if len(calls) == 2: move()
            return result
        monkeypatch.setattr(n, '_current_event_time', intercept)
    else:
        def factory(): move(); return provider
        monkeypatch.setattr(n, 'provider_from_config', factory)
    result = n.process_one(get_conn(), None if boundary == 'provider_factory' else provider)
    assert result is None if boundary == 'before_claim' else result['error_code'] == 'event_rescheduled'
    assert provider.sent == []
    monkeypatch.setattr(n, 'claim_next', claim)
    monkeypatch.setattr(n, '_current_event_time', read)
    monkeypatch.setattr(n, 'render_template', render)
    assert n.generate_due_reminders(get_conn())['created'] == 1
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    assert len(provider.sent) == 1 and '17:00' in provider.sent[0].body
    assert n.generate_due_reminders(get_conn())['created'] == 0
    assert n.process_one(get_conn(), provider) is None


@pytest.mark.parametrize('change', ['phone', 'empty', 'invalid', 'consent'])
@pytest.mark.parametrize('boundary', ['render', 'last_event_read'])
def test_final_recipient_and_consent_share_current_snapshot(auth_client, monkeypatch, change, boundary, caplog):
    from backend.app.services import notifications as n
    p, _, provider = prepared(auth_client, monkeypatch)
    def mutate():
        if change == 'consent':
            r = auth_client.put(f"/api/notifications/patients/{p['id']}/preferences", headers=auth_client.csrf,
                               json={'smsEnabled': False, 'consentStatus':'revoked'})
        else:
            value = {'phone': '+79990000002', 'empty': '', 'invalid': '123'}[change]
            r = auth_client.patch(f"/api/patients/{p['id']}", headers=auth_client.csrf,
                                  json={'field':'phone', 'value':value, 'expectedVersion':card_version(p['id'])})
        assert r.status_code == 200, r.text
    if boundary == 'render':
        render = n.render_template
        def intercept(*a, **kw):
            result = render(*a, **kw); mutate(); return result
        monkeypatch.setattr(n, 'render_template', intercept)
    else:
        read, calls = n._current_event_time, []
        def intercept(*a):
            result = read(*a); calls.append(1)
            if len(calls) == 2: mutate()
            return result
        monkeypatch.setattr(n, '_current_event_time', intercept)
    result = n.process_one(get_conn(), provider)
    if change == 'phone':
        assert result['status'] == 'sent'
        assert [c.recipient for c in provider.sent] == ['79990000002']
    else:
        assert result['status'] == 'skipped' and provider.sent == []
        assert result['error_code'] == {'empty':'missing_phone', 'invalid':'invalid_phone', 'consent':'no_consent'}[change]
    errors = get_conn().execute('SELECT error_code, error_message_safe FROM notifications').fetchall()
    assert all('+7999' not in str(tuple(r)) and 'C34' not in str(tuple(r)) for r in errors)
    assert '+79990000001' not in caplog.text and '+79990000002' not in caplog.text


@pytest.mark.parametrize('status', ['sent', 'failed', 'cancelled'])
@pytest.mark.parametrize('formula', ['legacy', 'unmarked_canonical'])
def test_unverified_terminal_rows_stay_immutable_and_are_not_replayed(auth_client, monkeypatch, status, formula):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p = stand(auth_client, day='2026-10-02', time='10:00')
    if formula == 'legacy':
        row = legacy_row(auth_client, p)
    else:
        assert n.generate_due_reminders(get_conn())['created'] == 1
        row = dict(get_conn().execute('SELECT * FROM notifications').fetchone())
    with get_conn():
        get_conn().execute('UPDATE notifications SET status=?, event_at=NULL, idempotency_key=? WHERE id=?',
                           (status, row['idempotency_key'].removeprefix('event_msk_v1:'), row['id']))
    before = n.get_notification_raw(get_conn(), row['id'])
    result = n.generate_due_reminders(get_conn())
    assert result['created'] == 0 and result['duplicates'] == 1
    assert n.get_notification_raw(get_conn(), row['id']) == before


def test_unverified_claim_is_refused_then_pending_is_rebuilt(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch, '2026-10-01T21:00:00+03:00')
    p = stand(auth_client, day='2026-10-02', time='10:00')
    old = legacy_row(auth_client, p)
    provider = recorder()
    assert n.process_one(get_conn(), provider)['error_code'] == 'event_schedule_unverified'
    assert provider.sent == []
    assert n.generate_due_reminders(get_conn())['created'] == 1
    assert n.process_one(get_conn(), provider)['status'] == 'sent'
    assert len(provider.sent) == 1 and '10:00' in provider.sent[0].body
    assert n.get_notification_raw(get_conn(), old['id'])['scheduled_at'] == old['scheduled_at']


def test_v28_upgrade_is_explicit_atomic_and_keeps_old_rows(auth_client, monkeypatch, tmp_path):
    from backend.app import db
    from backend.app.services import notifications as n
    p, _, _ = prepared(auth_client, monkeypatch)
    path = tmp_path / 'synthetic-v28.sqlite'
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    get_conn().backup(conn)
    with conn:
        conn.execute('DROP INDEX idx_notifications_idempotency_active')
        conn.execute('ALTER TABLE notifications DROP COLUMN event_at')
        conn.execute("UPDATE notifications SET idempotency_key = REPLACE(idempotency_key, 'event_msk_v1:', '')")
        conn.execute('UPDATE schema_version SET version=28')
        conn.execute("CREATE UNIQUE INDEX idx_notifications_idempotency_active ON notifications(patient_id,source_type,source_id,template_code,scheduled_at) WHERE status IN ('pending','processing','sent')")
    before = [tuple(r) for r in conn.execute('SELECT * FROM notifications')]
    schema = conn.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    try:
        with pytest.raises(db.SchemaUpgradeRequiredError): db.init_db(conn, allow_upgrade=False)
        assert [tuple(r) for r in conn.execute('SELECT sql FROM sqlite_master ORDER BY name')] == [tuple(r) for r in schema]
        migrate = db.migrate_v28_to_v29
        def fail(c): migrate(c); raise RuntimeError('synthetic rollback')
        monkeypatch.setattr(db, 'migrate_v28_to_v29', fail)
        with pytest.raises(RuntimeError, match='synthetic rollback'): db.init_db(conn)
        assert 'event_at' not in db._table_columns(conn, 'notifications')
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 28
        monkeypatch.setattr(db, 'migrate_v28_to_v29', migrate)
        db.init_db(conn)
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 29
        assert [tuple(r)[:-1] for r in conn.execute('SELECT * FROM notifications')] == before
        assert conn.execute('SELECT event_at FROM notifications').fetchone()[0] is None
        db.init_db(conn)
        assert [tuple(r)[:-1] for r in conn.execute('SELECT * FROM notifications')] == before
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()


@pytest.mark.parametrize('phase', ['after_claim', 'after_second_read'])
@pytest.mark.parametrize('change,reason', [('fact','event_inactive'), ('delete','event_removed'),
                                        ('no_plan','event_inactive'), ('closed','event_inactive'), ('passed','event_passed')])
def test_late_inactive_event_is_not_delivered(auth_client, monkeypatch, phase, change, reason):
    from backend.app.services import notifications as n
    from .conftest import event_delete
    p, state, provider = prepared(auth_client, monkeypatch)
    def mutate():
        if change == 'fact':
            assert event_patch(auth_client, p['id'], 'actualDate', '2026-10-01').status_code == 200
        elif change == 'delete':
            assert event_delete(auth_client, p['id']).status_code == 200
        elif change == 'passed':
            state[0] = datetime.fromisoformat('2026-10-02T10:00:00+03:00')
        else:
            # Legacy inactive/terminal source fixtures: no production write bypass.
            with get_conn():
                if change == 'no_plan':
                    get_conn().execute('UPDATE events SET no_plan=1 WHERE id=?', (p['history'][0]['events'][0]['id'],))
                else:
                    get_conn().execute("UPDATE patients SET stage='completed' WHERE id=?", (p['id'],))
    if phase == 'after_claim':
        claim = n.claim_next
        def intercept(conn): row = claim(conn); mutate(); return row
        monkeypatch.setattr(n, 'claim_next', intercept)
    else:
        read, calls = n._current_event_time, []
        def intercept(*a):
            result = read(*a); calls.append(1)
            if len(calls) == 2: mutate()
            return result
        monkeypatch.setattr(n, '_current_event_time', intercept)
    assert n.process_one(get_conn(), provider)['error_code'] == reason
    assert provider.sent == []


def test_two_workers_claim_once_without_write_lock_during_provider(auth_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    from backend.app import config
    from backend.app.services import notifications as n
    prepared(auth_client, monkeypatch)
    start, entered, release, empty = Barrier(2), Event(), Event(), Event()
    provider = recorder()
    send = provider.send
    def blocked(context):
        entered.set()
        assert release.wait(10), 'test synchronization timeout'
        return send(context)
    provider.send = blocked
    def worker():
        conn = sqlite3.connect(config.DB_PATH, timeout=10); conn.row_factory = sqlite3.Row
        try:
            start.wait(timeout=10)
            result = n.process_one(conn, provider)
            if result is None: empty.set()
            return result
        finally: conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        try:
            assert entered.wait(10), 'provider not entered'
            assert empty.wait(10), 'other worker did not complete its claim'
            probe = sqlite3.connect(config.DB_PATH, timeout=0)
            try: probe.execute('BEGIN IMMEDIATE'); probe.rollback()
            finally: probe.close()
        finally: release.set()
        results = [f.result(timeout=10) for f in futures]
    assert sum(r is None for r in results) == 1
    assert sum(r is not None and r['status'] == 'sent' for r in results) == 1
    assert len(provider.sent) == 1


def test_exact_v28_backup_validates_and_restores_to_v29(auth_client, monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from backend.app import config, maintenance
    monkeypatch.setenv('CLINIC_BACKUP_KEY', synthetic_export_key('wp-e-fix1-backup'.ljust(32, '_')))
    monkeypatch.delenv('CLINIC_BACKUP_KEY_FILE', raising=False)
    prepared(auth_client, monkeypatch)
    plain = tmp_path / 'v28.sqlite'
    conn = sqlite3.connect(plain); conn.row_factory = sqlite3.Row
    try:
        get_conn().backup(conn)
        with conn:
            conn.execute('DROP INDEX idx_notifications_idempotency_active')
            conn.execute('ALTER TABLE notifications DROP COLUMN event_at')
            conn.execute("UPDATE notifications SET idempotency_key = REPLACE(idempotency_key, 'event_msk_v1:', '')")
            conn.execute('UPDATE schema_version SET version=28')
            conn.execute("CREATE UNIQUE INDEX idx_notifications_idempotency_active ON notifications(patient_id,source_type,source_id,template_code,scheduled_at) WHERE status IN ('pending','processing','sent')")
        original = dict(conn.execute('SELECT * FROM notifications').fetchone())
    finally: conn.close()
    assert maintenance.identify_backup_build(plain, 28) == 'wpe-aa0cfd2'
    key = maintenance._load_active_key()
    nonce = b'\x04' * maintenance.NONCE_BYTES
    raw = plain.read_bytes()
    header = maintenance._build_header(key=key, nonce=nonce, ciphertext_len=len(raw)+16, schema_version=28)
    hb = maintenance._header_bytes(header)
    backup = tmp_path / 'v28.cbk'
    maintenance._write_container(backup, hb, AESGCM(key.key).encrypt(nonce, raw, maintenance._aad(hb)))
    validated = maintenance.validate_backup(backup)
    assert (validated['source_build'], validated['migrated_from_schema_version']) == ('wpe-aa0cfd2', 28)
    with app_stopped():
        result = maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
        assert result['migrated_from_schema_version'] == 28
        restored = sqlite3.connect(config.DB_PATH); restored.row_factory = sqlite3.Row
        try:
            assert restored.execute('SELECT version FROM schema_version').fetchone()[0] == 29
            assert dict(restored.execute('SELECT * FROM notifications').fetchone()) == {**original, 'event_at':None}
            assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert restored.execute('PRAGMA foreign_key_check').fetchall() == []
        finally: restored.close()
