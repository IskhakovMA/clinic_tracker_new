"""WP-E: единые календарные правила и актуализация центра без повторных записей."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier
import pytest
from .conftest import get_conn, create_account, create_department, make_patient_payload, event_patch, login
from .test_wp_e_time_notifications import freeze


def direct(**overrides):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    return store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(**overrides)), None)


def user(client):
    from backend.app import policy
    return policy.load_current_user(get_conn(), client.get('/api/auth/me').json()['user']['id'])


def due_sets(conn, actor):
    from backend.app.services import data_quality as dq, internal_notifications as inbox
    from backend.app.analytics import service
    from backend.app.analytics.filters import AnalyticsFilters
    from backend.app.ai import tools
    quality = dq.evaluate(conn, actor)
    a = service.overdue(conn, actor, AnalyticsFilters(limit=200))
    b = inbox.list_items(conn, actor, type='overdue', limit=100)
    c = tools.list_overdue_patients(conn, actor, tools.ListOverdueArgs(limit=30))
    return [
        {v['patient_id'] for v in quality['issues'] if v['code']=='overdue_event'},
        {v['patient_id'] for v in a['items']},
        {v['patient_id'] for v in b['items']},
        {v['patientId'] for v in c['patients']},
    ]


@pytest.mark.parametrize('stamp,expected', [('2026-10-01T23:59:59+03:00', False),
                                           ('2026-10-02T00:00:00+03:00', True),
                                           ('2026-10-02T01:00:00+03:00', True)])
@pytest.mark.parametrize('mapped', [False, True])
def test_identical_inputs_agree_at_moscow_midnight_without_nosology(auth_client, monkeypatch, stamp, expected, mapped):
    freeze(monkeypatch, stamp)
    pid = direct(events=[{'date':'2026-10-01','time':'10:00'}])
    if not mapped:
        with get_conn(): get_conn().execute("UPDATE patients SET nosology = '', diagnosis = 'D37.0' WHERE id = ?", (pid,))
    sets = due_sets(get_conn(), user(auth_client))
    assert all(s == ({pid} if expected else set()) for s in sets)
    if expected:
        from backend.app.ai.orchestrator import _today_text
        assert '02.10.2026' in _today_text()


def test_calendar_is_one_snapshot_even_if_clock_crosses_midnight(auth_client, monkeypatch):
    from backend.app import clock
    from backend.app.ai import tools
    from backend.app.analytics import service
    from backend.app.analytics.filters import AnalyticsFilters
    direct(events=[{'date':'2026-10-01','time':'10:00'}])
    moments = [datetime.fromisoformat(s) for s in ['2026-10-01T23:59:59+03:00','2026-10-02T00:00:00+03:00']]
    calls=[]
    def moving():
        value=moments[min(len(calls),1)]; calls.append(value); return value
    monkeypatch.setattr(clock,'now_moscow',moving)
    actor=user(auth_client)
    result=tools.get_today_work(get_conn(),actor,tools.EmptyArgs())
    assert result['date']=='2026-10-01' and result['overduePatients']==[]
    assert len(calls)==1
    calls.clear()
    result=service.summary(get_conn(),actor,AnalyticsFilters())
    assert len(calls)==1
    assert next(c['value'] for c in result['cards'] if c['code']=='patients_with_overdue')==0
    calls.clear()
    from backend.app.services import internal_notifications as inbox
    marked=inbox.mark_all_read(get_conn(),actor)
    assert len(calls)==1
    assert marked['summary']['today_count']==1 and marked['summary']['overdue_count']==0


@pytest.mark.parametrize('kind', ['actual', 'deleted', 'no_plan', 'terminal'])
def test_inactive_and_erroneous_inputs_keep_existing_exclusions(auth_client, monkeypatch, kind):
    from backend.app import store
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    pid=direct(events=[{'date':'2026-10-01','time':'10:00'}])
    event=get_conn().execute('SELECT * FROM events').fetchone()
    if kind=='actual': assert event_patch(auth_client,pid,'actualDate','2026-10-01').status_code==200
    elif kind=='deleted':
        assert auth_client.delete(f"/api/patients/{pid}/events/{event['id']}?version={event['version']}",headers=auth_client.csrf).status_code==200
    elif kind=='no_plan':
        with get_conn():get_conn().execute('UPDATE events SET no_plan=1 WHERE id=?',(event['id'],))
    elif kind=='terminal':
        with get_conn():get_conn().execute("UPDATE patients SET stage='completed' WHERE id=?",(pid,))
    assert all(not s for s in due_sets(get_conn(),user(auth_client)))


def request_conn():
    from backend.app import config, db
    return db._connect(config.DB_PATH)


def warm(actor):
    from backend.app.services import internal_notifications as inbox
    for _ in range(3):
        conn=request_conn()
        try: result=inbox.summary(conn,actor)
        finally:conn.close()
    return result


def test_unchanged_poll_uses_conditions_cache_and_performs_no_writes(auth_client,monkeypatch):
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    direct(events=[{'date':'2026-10-01','time':'10:00'}])
    actor=user(auth_client);before=warm(actor)
    compute=inbox._compute_conditions
    def forbidden(*args):raise AssertionError('unchanged data was recomputed')
    monkeypatch.setattr(inbox,'_compute_conditions',forbidden)
    conn=request_conn();trace=[];conn.set_trace_callback(trace.append);changes=conn.total_changes
    try:
        result=inbox.list_items(conn,actor)
        assert result['summary']['total_count']==before['total_count']
        assert conn.total_changes==changes
        assert not any(s.upper().startswith(('INSERT','UPDATE','DELETE','BEGIN')) for s in trace)
    finally:conn.close()


def test_cache_invalidates_for_other_connection_event_changes_and_new_day(auth_client,monkeypatch):
    from backend.app.services import internal_notifications as inbox
    state=freeze(monkeypatch,'2026-10-01T23:59:00+03:00')
    pid=direct(events=[{'date':'2026-10-01','time':'10:00'}]);actor=user(auth_client)
    assert warm(actor)['overdue_count']==0
    state[0]=datetime.fromisoformat('2026-10-02T00:00:00+03:00')
    conn=request_conn()
    try:assert inbox.summary(conn,actor)['overdue_count']==1
    finally:conn.close()
    assert event_patch(auth_client,pid,'actualDate','2026-10-01').status_code==200
    assert warm(actor)['overdue_count']==0
    assert event_patch(auth_client,pid,'actualDate','').status_code==200
    assert warm(actor)['overdue_count']==1
    assert event_patch(auth_client,pid,'date','2026-10-03').status_code==200
    assert warm(actor)['overdue_count']==0


def test_read_state_and_history_survive_unchanged_polls_and_reactivation(auth_client,monkeypatch):
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    pid=direct(events=[{'date':'2026-10-01','time':'10:00'}]);actor=user(auth_client)
    conn=request_conn()
    try:
        items=inbox.list_items(conn,actor,type='overdue')['items'];item=items[0]
        inbox.mark_read(conn,actor,item['id'])
    finally:conn.close()
    state=get_conn().execute('SELECT * FROM internal_notification_state WHERE id=?',(item['id'],)).fetchone()
    for _ in range(3):warm(actor)
    after=get_conn().execute('SELECT * FROM internal_notification_state WHERE id=?',(item['id'],)).fetchone()
    assert dict(after)==dict(state)
    assert event_patch(auth_client,pid,'actualDate','2026-10-01').status_code==200
    warm(actor)
    assert get_conn().execute('SELECT resolved_at FROM internal_notification_state WHERE id=?',(item['id'],)).fetchone()[0]
    assert event_patch(auth_client,pid,'actualDate','').status_code==200
    warm(actor)
    after=get_conn().execute('SELECT * FROM internal_notification_state WHERE id=?',(item['id'],)).fetchone()
    assert after['read_at']==state['read_at'] and after['first_seen_at']==state['first_seen_at'] and after['resolved_at'] is None


def test_concurrent_polls_do_not_duplicate_state_or_lose_read_action(auth_client,monkeypatch):
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    direct(events=[{'date':'2026-10-01','time':'10:00'}]);actor=user(auth_client)
    gate=Barrier(3)
    def poll(_):
        conn=request_conn()
        try:gate.wait();return inbox.list_items(conn,actor)
        finally:conn.close()
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(poll,range(3)))
    ids={tuple(sorted(v['id'] for v in r['items'])) for r in results}
    assert len(ids)==1
    states=get_conn().execute('SELECT COUNT(*),COUNT(DISTINCT stable_key) FROM internal_notification_state WHERE user_id=?',(actor.id,)).fetchone()
    assert states[0]==states[1]==results[0]['total']
    target=results[0]['items'][0]['id'];gate=Barrier(2)
    def action(read):
        conn=request_conn()
        try:
            gate.wait()
            return inbox.mark_read(conn,actor,target) if read else inbox.summary(conn,actor)
        finally:conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(action,[True,False]))
    assert get_conn().execute('SELECT read_at FROM internal_notification_state WHERE id=?',(target,)).fetchone()[0]


def test_actual_department_and_user_scopes_never_share_cached_conditions(auth_client,monkeypatch):
    from backend.app import policy, users
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    a,b=create_department('Синтетическое А'),create_department('Синтетическое Б')
    d1=create_account('wp.e.doc1','doctor','Синтетический А',department_ids=[a])
    d2=create_account('wp.e.doc2','doctor','Синтетический Б',department_ids=[b])
    head=create_account('wp.e.head','department_head','Синтетический Заведующий',department_ids=[a])
    from backend.app import store
    from backend.app.models import PatientCreateIn
    pa=store.create_patient(get_conn(),PatientCreateIn(**make_patient_payload(name='Карточка А',events=[{'date':'2026-10-01','time':'10:00'}])),d1)
    pb=store.create_patient(get_conn(),PatientCreateIn(**make_patient_payload(name='Карточка Б',events=[{'date':'2026-10-01','time':'10:00'}])),d2)
    assert login(auth_client,'wp.e.head').status_code==200
    first=auth_client.get('/api/internal-notifications').json()
    assert {i['patient_id'] for i in first['items']}=={pa}
    assert login(auth_client,'wp.e.doc2').status_code==200
    second=auth_client.get('/api/internal-notifications').json()
    assert {i['patient_id'] for i in second['items']}=={pb}
    users.set_department_head(get_conn(),a,None);users.set_department_head(get_conn(),b,head)
    assert login(auth_client,'wp.e.head').status_code==200
    third=auth_client.get('/api/internal-notifications').json()
    assert {i['patient_id'] for i in third['items']}=={pb}


def test_erroneous_card_is_removed_from_warm_cache_via_existing_mark_workflow(client, monkeypatch):
    from backend.app import policy
    from backend.app.services import internal_notifications as inbox, data_quality
    from .test_wp24d_duplicate_review import _setup, _mark
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    ids,a,b,cid,dev,deputy=_setup(client)
    actor=policy.load_current_user(get_conn(),ids['dev'])
    warm(actor)
    assert _mark(deputy,cid,b).status_code==200
    conn=request_conn()
    try:
        assert b not in {i['patient_id'] for i in inbox.list_items(conn,actor)['items']}
        assert b not in {p['patient_id'] for p in data_quality.evaluate(conn,actor)['patients']}
    finally:conn.close()


def test_unverified_historical_fact_and_fact_history_are_not_rewritten(auth_client, monkeypatch):
    from .test_wp_c_medical_integrity import _future_doc, FUTURE
    from .conftest import unsigned_import
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    name='Синтетический неподтвержденный факт WP-E'
    response=unsigned_import(auth_client,_future_doc(name),'add')
    assert response.status_code==200 and response.json()['futureFacts']==1
    pid=get_conn().execute('SELECT id FROM patients WHERE name=?',(name,)).fetchone()[0]
    before=auth_client.get('/api/patients/'+pid).json()
    facts=[tuple(row) for row in get_conn().execute('SELECT * FROM patient_event_fact_history')]
    assert before['history'][0]['events'][0]['actualDateUnverified'] is True
    assert all(pid not in values for values in due_sets(get_conn(),user(auth_client)))
    assert auth_client.get('/api/patients/'+pid).json()==before
    assert [tuple(row) for row in get_conn().execute('SELECT * FROM patient_event_fact_history')]==facts


def test_commit_during_calculation_is_not_published_as_a_fresh_cache_entry(auth_client,monkeypatch):
    from threading import Event
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    pid=direct(events=[{'date':'2026-10-01','time':'10:00'}]);actor=user(auth_client)
    arrived,release=Event(),Event();original=inbox._compute_conditions
    def paused(conn,who):
        result=original(conn,who);arrived.set();assert release.wait(20);return result
    monkeypatch.setattr(inbox,'_compute_conditions',paused)
    def calculate():
        conn=request_conn()
        try:return inbox.summary(conn,actor)
        finally:conn.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result=pool.submit(calculate)
        assert arrived.wait(20)
        try:assert event_patch(auth_client,pid,'actualDate','2026-10-01').status_code==200
        finally:release.set()
        assert result.result(timeout=20)['overdue_count']==1  # snapshot at request start
    monkeypatch.setattr(inbox,'_compute_conditions',original)
    assert warm(actor)['overdue_count']==0


def test_uncommitted_caller_snapshot_never_leaks_into_cache(auth_client,monkeypatch):
    from backend.app.services import internal_notifications as inbox
    freeze(monkeypatch,'2026-10-02T01:00:00+03:00')
    direct(events=[{'date':'2026-10-01','time':'10:00'}]);actor=user(auth_client)
    assert warm(actor)['overdue_count']==1
    conn=request_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE events SET date='2026-10-03'")
        assert inbox.preview_summary(conn,actor)['overdue_count']==0
        assert conn.in_transaction
        conn.rollback()
    finally:conn.close()
    assert warm(actor)['overdue_count']==1
