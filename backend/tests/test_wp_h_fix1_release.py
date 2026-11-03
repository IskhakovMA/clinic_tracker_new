"""Whole-scope release validation, real SQLite/HTTP and explicit synchronization."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from .conftest import create_account, get_conn, make_patient_payload, TEST_PASSWORD


def setup_scope(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    a=create_account('wph.fix1.a','doctor');b=create_account('wph.fix1.b','doctor')
    conn=get_conn();ids=[]
    with conn:
        for name,head in [('А Синтетический','Открытый Синтетический'),('Я Синтетический','Отозванный Синтетический')]:
            pid=store.insert_patient_rows(conn,PatientCreateIn(**make_patient_payload(name=name,head=head,events=[{'date':'2026-12-01','time':'10:00'}])),a,created_by_user_id=None,creation_source='legacy')
            ids.append(pid)
    assert client.post('/api/auth/login',json={'username':'wph.fix1.a','password':TEST_PASSWORD}).status_code==200
    return a,b,ids


def transfer(conn,b,pid):
    with conn:conn.execute('UPDATE patients SET treating_doctor_user_id=?,version=version+1 WHERE id=?',(b,pid))


def count_manifests():
    return get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]


def assert_changed(response,before):
    assert response.status_code==409,response.text
    assert response.json()['code']=='PATIENT_QUERY_CHANGED'
    assert 'patients' not in response.json() and 'page' not in response.json()
    assert 'Синтетический' not in response.text and 'heads' not in response.text
    assert count_manifests()==before


@pytest.mark.parametrize('target',[0,1])
@pytest.mark.parametrize('filtered',[False,True])
def test_page_scope_change_before_release_no_stale_aggregates(client,monkeypatch,target,filtered):
    from backend.app import patient_pages
    a,b,ids=setup_scope(client);original=patient_pages.page;snapshots=[]
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);snapshots.append(result)
        transfer(args[0],b,ids[target]);return result
    monkeypatch.setattr(patient_pages,'page',changed)
    params={'view':'page','limit':'1','sort':'name','include_terminal':'true'}
    if filtered:params['q']='А Синтетический'
    before=count_manifests();response=client.get('/api/patients',params=params);assert_changed(response,before)
    monkeypatch.setattr(patient_pages,'page',original)
    fresh=client.get('/api/patients',params=params);assert fresh.status_code==200
    assert fresh.json()['summary']['total']==1
    if target==1:
        assert 'Отозванный Синтетический' not in fresh.json()['heads']
        if filtered:
            assert snapshots[0]['page']['revision']==fresh.json()['page']['revision']
            assert snapshots[0]['summary']['total']==2
    manifest=get_conn().execute('SELECT * FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
    assert manifest['snapshot_sha256']==hashlib.sha256(fresh.content).hexdigest()
    assert {o['id'] for o in json.loads(manifest['objects_json']) if o['type']=='patient'}=={p['id'] for p in fresh.json()['patients']}


@pytest.mark.parametrize('kind',['events','checklists','patients'])
@pytest.mark.parametrize('target',[0,1])
def test_all_workspaces_validate_scope_beyond_issued_rows(client,monkeypatch,kind,target):
    from backend.app import patient_pages
    a,b,ids=setup_scope(client);original=patient_pages.workspace_rows
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        row=result[kind][0];issued=row.get('patientId',row.get('patient_id',row.get('id')))
        pid=issued if target==0 else next(pid for pid in ids if pid!=issued)
        transfer(args[0],b,pid);return result
    monkeypatch.setattr(patient_pages,'workspace_rows',changed)
    before=count_manifests();response=client.get('/api/patients/workspace/'+kind+'?limit=1');assert_changed(response,before)
    monkeypatch.setattr(patient_pages,'workspace_rows',original)
    fresh=client.get('/api/patients/workspace/'+kind+'?limit=1');assert fresh.status_code==200
    assert fresh.json()['page']['total']==1


@pytest.mark.parametrize('mutation',['role','logout','identity'])
def test_auth_change_has_priority_over_query_change(client,monkeypatch,mutation):
    from backend.app import patient_pages, security
    a,b,ids=setup_scope(client);original=patient_pages.page
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);conn=args[0]
        transfer(conn,b,ids[1])
        with conn:
            if mutation=='role':conn.execute("UPDATE users SET role_id='department_head' WHERE id=?",(a,))
            elif mutation=='logout':conn.execute('DELETE FROM sessions WHERE user_id=?',(a,))
            else:conn.execute('UPDATE sessions SET user_id=? WHERE user_id=?',(b,a))
        return result
    monkeypatch.setattr(patient_pages,'page',changed)
    response=client.get('/api/patients?view=page&limit=1')
    assert response.status_code==(401 if mutation=='logout' else 409),response.text
    if mutation!='logout':assert response.json()['code']=='ACCESS_CONTEXT_CHANGED'
    assert 'PATIENT_QUERY_CHANGED' not in response.text and 'Синтетический' not in response.text


@pytest.mark.parametrize('commit',[False,True])
def test_data_version_guard_covers_gap_without_rejecting_rollback(client,monkeypatch,commit):
    from backend.app import patient_pages,db,config
    a,b,ids=setup_scope(client);original=patient_pages.validate_release
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        other=db.connect_live(config.DB_PATH)
        try:
            other.execute('UPDATE patients SET treating_doctor_user_id=?,version=version+1 WHERE id=?',(b,ids[1]))
            if commit:other.commit()
            else:other.rollback()
        finally:other.close()
    monkeypatch.setattr(patient_pages,'validate_release',changed)
    before=count_manifests();response=client.get('/api/patients?view=page&limit=1')
    if commit:assert_changed(response,before)
    else:assert response.status_code==200 and response.json()['summary']['total']==2


def test_no_write_lock_during_expensive_validation_and_concurrent_transfer(client,monkeypatch):
    from backend.app import patient_pages,db,config
    a,b,ids=setup_scope(client);original=patient_pages.validate_release
    entered=threading.Event();committed=threading.Event()
    def validate(*args,**kwargs):
        # A second writer must commit before revalidation resumes. A write-lock
        # around O(N) validation would deadlock/fail this explicit barrier.
        entered.set();assert committed.wait(10)
        return original(*args,**kwargs)
    def writer():
        assert entered.wait(10)
        conn=db.connect_live(config.DB_PATH)
        try:transfer(conn,b,ids[1])
        finally:conn.close();committed.set()
    monkeypatch.setattr(patient_pages,'validate_release',validate)
    before=count_manifests()
    with ThreadPoolExecutor(1) as pool:
        job=pool.submit(writer);response=client.get('/api/patients?view=page&limit=1');job.result(timeout=10)
    assert_changed(response,before)


@pytest.mark.parametrize('fault',['validation','event','manifest'])
def test_release_fault_never_sends_buffered_data(client,monkeypatch,fault):
    from backend.app import patient_pages
    setup_scope(client);conn=get_conn()
    if fault=='validation':
        def failed(*args,**kwargs):raise OSError('synthetic validation failure')
        monkeypatch.setattr(patient_pages,'validate_release',failed)
    elif fault=='event':conn.executescript("CREATE TRIGGER synthetic_fix1_event_fault BEFORE INSERT ON audit_events WHEN NEW.event_type='medical_read_release' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    else:conn.executescript("CREATE TRIGGER synthetic_fix1_manifest_fault BEFORE INSERT ON read_access_manifests BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    before=count_manifests();response=client.get('/api/patients?view=page&limit=1')
    assert response.status_code==503 and 'Синтетический' not in response.text
    assert count_manifests()==before


@pytest.mark.parametrize('kind',['role','identity'])
def test_context_change_after_validation_is_never_list_retry(client,monkeypatch,kind):
    from backend.app import patient_pages,db,config
    a,b,ids=setup_scope(client);original=patient_pages.validate_release
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        other=db.connect_live(config.DB_PATH)
        try:
            with other:
                if kind=='role':other.execute("UPDATE users SET role_id='department_head' WHERE id=?",(a,))
                else:other.execute('UPDATE sessions SET user_id=? WHERE user_id=?',(b,a))
        finally:other.close()
    monkeypatch.setattr(patient_pages,'validate_release',changed)
    response=client.get('/api/patients?view=page&limit=1')
    assert response.status_code==409 and response.json()['code']=='ACCESS_CONTEXT_CHANGED'
    assert 'Синтетический' not in response.text


def test_equal_totals_and_facets_do_not_hide_membership_swap(client,monkeypatch):
    from backend.app import patient_pages,store
    from backend.app.models import PatientCreateIn
    a,b,ids=setup_scope(client);conn=get_conn()
    with conn:
        replacement=store.insert_patient_rows(conn,PatientCreateIn(**make_patient_payload(name='Я Другой Синтетический',head='Отозванный Синтетический')),b,created_by_user_id=None,creation_source='legacy')
    original=patient_pages.page;old=[]
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);old.append(result)
        with args[0]:
            args[0].execute('UPDATE patients SET treating_doctor_user_id=?,version=version+1 WHERE id=?',(b,ids[1]))
            args[0].execute('UPDATE patients SET treating_doctor_user_id=?,version=version+1 WHERE id=?',(a,replacement))
        return result
    monkeypatch.setattr(patient_pages,'page',changed)
    params={'view':'page','q':'А Синтетический','limit':'1'}
    before=count_manifests();assert_changed(client.get('/api/patients',params=params),before)
    monkeypatch.setattr(patient_pages,'page',original)
    fresh=client.get('/api/patients',params=params).json()
    assert old[0]['summary']==fresh['summary'] and old[0]['heads']==fresh['heads']
    assert old[0]['page']['revision']==fresh['page']['revision']
