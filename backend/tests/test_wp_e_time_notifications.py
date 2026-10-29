"""WP-E: управляемое время учреждения, безопасные SMS и шаблоны. Только mock."""
from datetime import date, datetime, timedelta, timezone
import pytest
from .conftest import get_conn, make_patient_payload, event_patch


def freeze(monkeypatch, stamp='2026-10-01T11:00:00+03:00'):
    from backend.app import clock
    state = [datetime.fromisoformat(stamp)]
    monkeypatch.setattr(clock, 'now_moscow', lambda: state[0].astimezone(clock.institution_tz()))
    return state


def stand(client, day='2026-10-01', time='12:00'):
    response = client.post('/api/patients', headers=client.csrf, json=make_patient_payload(
        phone='+79990000001', events=[{'date': day, 'time': time}]))
    assert response.status_code == 201, response.text
    patient = response.json()
    response = client.put(f"/api/notifications/patients/{patient['id']}/preferences", headers=client.csrf,
                          json={'smsEnabled': True, 'consentStatus': 'granted'})
    assert response.status_code == 200
    return patient


def recorder():
    from backend.app.notifications.mock_provider import MockNotificationProvider
    class Recording(MockNotificationProvider):
        def __init__(self):
            super().__init__('success'); self.sent = []
        def send(self, context):
            self.sent.append(context)
            return super().send(context)
    return Recording()


@pytest.mark.parametrize('visit,created', [('10:00', 0), ('11:00', 0), ('12:00', 1)])
def test_generation_and_worker_use_moscow_visit_time(auth_client, monkeypatch, visit, created):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    stand(auth_client, time=visit)
    result = auth_client.post('/api/notifications/generate-due', headers=auth_client.csrf)
    assert result.status_code == 200 and result.json()['created'] == created
    provider = recorder()
    processed = n.process_due(get_conn(), provider=provider)
    assert processed['sent'] == created and len(provider.sent) == created
    if created:
        assert '12:00' in provider.sent[0].body
        assert '2026-10-01' in provider.sent[0].body
        row = get_conn().execute('SELECT scheduled_at FROM notifications').fetchone()
        assert row[0] == '2026-09-30T09:00:00+00:00'
        assert n.process_due(get_conn(), provider=provider)['sent'] == 0
        assert len(provider.sent) == 1


@pytest.mark.parametrize('now,created', [('08:59:00', 1), ('09:00:00', 0), ('09:01:00', 0)])
def test_existing_date_only_rule_is_explicitly_nine_moscow(auth_client, monkeypatch, now, created):
    from backend.app.services import notifications as n
    freeze(monkeypatch, '2026-10-01T' + now + '+03:00')
    p = stand(auth_client, time='')
    assert n.event_dt('2026-10-01', '').isoformat() == '2026-10-01T06:00:00+00:00'
    assert n.generate_due_reminders(get_conn())['created'] == created
    assert auth_client.get('/api/patients/' + p['id']).json()['history'][0]['events'][0]['time'] is None


@pytest.mark.parametrize('time', ['bad', '25:00', '10:60'])
def test_invalid_time_does_not_become_a_guessed_visit(time):
    from backend.app.services import notifications as n
    assert n.event_dt('2026-10-01', time) is None


def test_time_is_checked_again_at_provider_boundary(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    clock = freeze(monkeypatch, '2026-10-01T09:00:00+03:00')
    stand(auth_client, time='10:00')
    assert n.generate_due_reminders(get_conn())['created'] == 1
    render = n.render_template
    def crosses_visit(*args, **kwargs):
        body = render(*args, **kwargs)
        clock[0] = datetime.fromisoformat('2026-10-01T10:00:00+03:00')
        return body
    monkeypatch.setattr(n, 'render_template', crosses_visit)
    provider = recorder()
    result = n.process_one(get_conn(), provider)
    assert result['status'] == 'skipped' and result['error_code'] == 'event_passed'
    assert provider.sent == []


@pytest.mark.parametrize('change', ['date', 'actualDate', 'delete', 'noPlan'])
def test_event_changes_after_generation_prevent_old_send(auth_client, monkeypatch, change):
    from backend.app.services import notifications as n
    from backend.app import store
    freeze(monkeypatch)
    p = stand(auth_client, day='2026-10-02', time='10:00')
    assert n.generate_due_reminders(get_conn())['created'] == 1
    event = p['history'][0]['events'][0]
    if change == 'date':
        response = event_patch(auth_client, p['id'], 'date', '2026-10-03')
        assert response.status_code == 200
    elif change == 'actualDate':
        assert event_patch(auth_client, p['id'], 'actualDate', '2026-10-01').status_code == 200
    elif change == 'delete':
        response = auth_client.delete(f"/api/patients/{p['id']}/events/{event['id']}?version={event['version']}", headers=auth_client.csrf)
        assert response.status_code == 200
    else:
        # Синтетическая legacy-отмена плана, не обход проверки в рабочем API.
        with get_conn(): get_conn().execute('UPDATE events SET no_plan = 1 WHERE id = ?', (event['id'],))
    provider = recorder()
    assert n.process_due(get_conn(), provider=provider)['sent'] == 0
    assert provider.sent == []


def legacy_row(client, p):
    from backend.app.services import notifications as n
    event = p['history'][0]['events'][0]
    local_as_utc = datetime.fromisoformat(event['date'] + 'T' + (event['time'] or '09:00') + '+00:00')
    from backend.app import config
    row, reason = n.create_notification(get_conn(), patient_id=p['id'], template_code='appointment_reminder',
        scheduled_at=(local_as_utc-timedelta(hours=config.NOTIFICATION_DEFAULT_REMINDER_HOURS)).isoformat(),
        source_type='event', source_id=event['id'], created_by_user_id=None)
    assert reason is None
    return row


def test_existing_legacy_pending_is_rebuilt_from_source_not_shifted_blindly(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    from backend.app import config
    monkeypatch.setattr(config, 'NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT', 1)
    freeze(monkeypatch)
    p = stand(auth_client, day='2026-10-02', time='10:00')
    old = legacy_row(auth_client, p)
    result = n.generate_due_reminders(get_conn())
    assert result['created'] == 1 and result['cancelled'] == 1
    rows = [dict(row) for row in get_conn().execute('SELECT * FROM notifications ORDER BY created_at')]
    assert rows[0]['id'] == old['id'] and rows[0]['scheduled_at'] == old['scheduled_at']
    assert rows[0]['status'] == 'cancelled'
    assert rows[1]['status'] == 'pending' and rows[1]['scheduled_at'] == '2026-10-01T07:00:00+00:00'
    provider = recorder()
    assert n.process_due(get_conn(), provider=provider)['sent'] == 1
    assert '2026-10-02' in provider.sent[0].body and '10:00' in provider.sent[0].body
    assert n.process_due(get_conn(), provider=provider)['sent'] == 0


@pytest.mark.parametrize('status', ['sent', 'failed', 'cancelled'])
def test_legacy_terminal_history_is_not_rewritten_or_replayed(auth_client, monkeypatch, status):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p = stand(auth_client, day='2026-10-02', time='10:00')
    row = legacy_row(auth_client, p)
    with get_conn():
        get_conn().execute('UPDATE notifications SET status = ? WHERE id = ?', (status, row['id']))
    before = n.get_notification_raw(get_conn(), row['id'])
    result = n.generate_due_reminders(get_conn())
    assert result['created'] == 0 and result['duplicates'] == 1
    assert n.get_notification_raw(get_conn(), row['id']) == before
    assert get_conn().execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 1


def test_legacy_due_row_does_not_send_after_visit(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p = stand(auth_client, time='10:00')
    legacy_row(auth_client, p)
    provider = recorder()
    result = n.process_one(get_conn(), provider)
    assert result['status'] == 'skipped' and result['error_code'] == 'event_passed'
    assert provider.sent == []


BAD_TEMPLATES = ['{date.__class__}', '{date[0]}', '{date!r}', '{date:>100000000}',
                 '{unknown}', '{date:{time}}', '{date:}', '{', '}', '{}', '{0}']
@pytest.mark.parametrize('body', BAD_TEMPLATES)
def test_template_allowlist_is_enforced_on_save_and_render(auth_client, body):
    from backend.app.services import notifications as n
    response = auth_client.put('/api/notifications/templates/custom_reminder', json={'body': 'СИНТЕТИЧЕСКИЙ_МАРКЕР ' + body}, headers=auth_client.csrf)
    assert response.status_code == 422
    assert 'СИНТЕТИЧЕСКИЙ_МАРКЕР' not in response.text and 'Traceback' not in response.text
    with pytest.raises(ValueError): n.render_template(body, scheduled_at='2026-10-01T21:30:00+00:00')


def test_cyrillic_literal_braces_and_bounded_output(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    body = 'Прием {date} в {time}. {{дата}}: {{{date}}}'
    assert n.render_template(body, scheduled_at='2026-10-01T21:30:00+00:00') == 'Прием 2026-10-02 в 00:30. {дата}: {2026-10-02}'
    assert len(n.render_template('{date}'*83, scheduled_at='2026-10-01T21:30:00+00:00')) == 830
    with pytest.raises(ValueError): n.validate_template_body('x'*501)
    monkeypatch.setattr(n, 'MAX_RENDERED_LENGTH', 20)
    with pytest.raises(ValueError): n.render_template('{date}'*3, scheduled_at='2026-10-01T21:30:00+00:00')


def test_previously_saved_bad_template_is_skipped_without_provider_call(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p = stand(auth_client)
    assert n.generate_due_reminders(get_conn())['created'] == 1
    with get_conn():
        get_conn().execute("UPDATE notification_templates SET body = '{date.__class__}' WHERE code = 'appointment_reminder'")
    provider = recorder()
    result = n.process_one(get_conn(), provider)
    assert result['status'] == 'skipped' and result['error_code'] == 'template_invalid'
    assert provider.sent == []


def test_cli_generation_and_worker_observe_the_same_clock(auth_client, monkeypatch, capsys):
    import importlib
    import sys
    # Фикстура переимпортирует backend.* для новой temp-базы: не брать старый
    # атрибут backend.cli, оставшийся после команд предыдущего теста.
    cli = importlib.import_module('backend.cli')
    from backend.app import config
    assert cli.config.DB_PATH == config.DB_PATH
    def command(*args):
        monkeypatch.setattr(sys, 'argv', ['clinic', *args])
        return cli.main()
    freeze(monkeypatch)
    stand(auth_client, time='10:00')
    assert command('notifications-generate') == 0
    assert 'created=0' in capsys.readouterr().out
    stand(auth_client, day='2026-10-02', time='10:00')
    assert command('notifications-generate') == 0
    assert 'created=1' in capsys.readouterr().out
    assert command('notifications-run-once') == 0
    assert 'sent=1' in capsys.readouterr().out
    assert command('notifications-drain', '--limit', '10') == 0
    assert 'sent=0' in capsys.readouterr().out


def test_queue_today_uses_moscow_calendar_day(auth_client, monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch, '2026-10-02T00:30:00+03:00')
    p = stand(auth_client, day='2026-10-03', time='10:00')
    for moment in ['2026-10-01T21:30:00+00:00','2026-10-02T21:30:00+00:00']:
        n.create_notification(get_conn(),patient_id=p['id'],template_code='custom_reminder',scheduled_at=moment,
                              source_type='manual',source_id=moment,created_by_user_id=None)
    response = auth_client.get('/api/notifications/summary')
    assert response.status_code == 200
    assert response.json()['due_today'] == 1


def test_poisoned_old_date_in_claimed_queue_does_not_abort_worker(auth_client,monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p=stand(auth_client)
    assert n.generate_due_reminders(get_conn())['created']==1
    with get_conn():get_conn().execute("UPDATE events SET date='0001-01-01'")
    provider=recorder()
    result=n.process_one(get_conn(),provider)
    assert result['status']=='skipped' and result['error_code']=='event_passed'
    assert provider.sent==[]


def test_real_reschedule_after_sent_gets_new_reminder_without_replaying_history(auth_client,monkeypatch):
    from backend.app.services import notifications as n
    freeze(monkeypatch)
    p=stand(auth_client,day='2026-10-02',time='10:00')
    assert n.generate_due_reminders(get_conn())['created']==1
    provider=recorder()
    assert n.process_due(get_conn(),provider=provider)['sent']==1
    before=dict(get_conn().execute("SELECT * FROM notifications WHERE status='sent'").fetchone())
    assert event_patch(auth_client,p['id'],'date','2026-10-03').status_code==200
    assert n.generate_due_reminders(get_conn())['created']==1
    assert n.get_notification_raw(get_conn(),before['id'])==before
    assert get_conn().execute("SELECT COUNT(*) FROM notifications WHERE status='pending'").fetchone()[0]==1
