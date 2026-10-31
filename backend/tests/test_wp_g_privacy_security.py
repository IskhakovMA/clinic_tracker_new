"""WP-G phase 1: real synthetic API/DB, controlled UTC and explicit races."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from .conftest import create_account, get_conn, login, make_patient_payload


def card(client):
    r=client.post('/api/patients',json=make_patient_payload(),headers=client.csrf)
    assert r.status_code==201,r.text
    return r.json()


def latest_manifest():
    return get_conn().execute('SELECT m.*, a.event_type, a.actor_user_id FROM read_access_manifests m JOIN audit_events a ON a.id=m.audit_event_id ORDER BY audit_event_id DESC LIMIT 1').fetchone()


@pytest.mark.parametrize('listing',[False,True])
def test_exact_release_snapshot(auth_client,listing):
    p=card(auth_client)
    r=auth_client.get('/api/patients' if listing else '/api/patients/'+p['id'])
    assert r.status_code==200
    row=latest_manifest(); objects=json.loads(row['objects_json'])
    assert {'type':'patient','id':p['id']} in objects
    assert row['snapshot_sha256']==hashlib.sha256(r.content).hexdigest()
    assert row['object_count']==len(objects)
    assert p['name'] not in row['objects_json'] and p['diagnosis'] not in row['objects_json']
    assert row['event_type']=='medical_read_release' and row['actor_user_id']


def test_release_audit_failure_blocks_body_and_rolls_back(auth_client):
    p=card(auth_client); c=get_conn()
    n=c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
    c.execute("CREATE TRIGGER synthetic_read_failure BEFORE INSERT ON read_access_manifests BEGIN SELECT RAISE(ABORT,'synthetic'); END");c.commit()
    r=auth_client.get('/api/patients/'+p['id'])
    assert r.status_code==503
    assert p['name'] not in r.text and p['diagnosis'] not in r.text
    assert c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]==n
    assert c.execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]==0


def test_manifest_is_append_only_and_not_pruned(auth_client):
    from backend.app import audit
    card(auth_client);auth_client.get('/api/patients');c=get_conn()
    row=latest_manifest()
    for sql in ('UPDATE read_access_manifests SET object_count=0','DELETE FROM read_access_manifests'):
        with pytest.raises(Exception):
            c.execute(sql)
        c.rollback()
    audit.prune(c,retention_days=30)
    assert c.execute('SELECT COUNT(*) FROM read_access_manifests WHERE audit_event_id=?',(row['audit_event_id'],)).fetchone()[0]==1


def test_denial_recorded_once_without_foreign_card_disclosure(auth_client):
    p=card(auth_client);create_account('g.doctor',role_id='doctor');login(auth_client,'g.doctor')
    c=get_conn();n=c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
    r=auth_client.get('/api/patients/'+p['id'])
    assert r.status_code==404 and p['name'] not in r.text
    assert c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]==n+1
    row=latest_manifest();assert row['event_type']=='medical_read_denied'
    assert json.loads(row['objects_json'])==[{'type':'attempted_patient','id':p['id']}]
    assert auth_client.get('/api/audit').status_code==403
    assert auth_client.get('/api/audit/'+str(row['audit_event_id'])+'/manifest').status_code==403


def test_existing_export_has_no_second_read_event(auth_client):
    patient=card(auth_client);c=get_conn()
    n=c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
    r=auth_client.get('/api/export',headers=auth_client.csrf)
    assert r.status_code==200
    assert c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]==n+1
    assert c.execute('SELECT event_type FROM audit_events ORDER BY id DESC LIMIT 1').fetchone()[0]=='export'
    row = latest_manifest()
    assert row['event_type'] == 'export'
    assert {'type': 'patient', 'id': patient['id']} in json.loads(row['objects_json'])
    assert row['snapshot_sha256'] == hashlib.sha256(r.content).hexdigest()
    assert c.execute('SELECT COUNT(*) FROM read_access_manifests WHERE audit_event_id=?', (row['audit_event_id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('seconds,status',[(899,200),(900,401),(901,401)])
def test_idle_exact_boundary_no_resurrection(auth_client,monkeypatch,seconds,status):
    from backend.app import security
    row=get_conn().execute('SELECT * FROM sessions').fetchone()
    now=datetime.fromisoformat(row['last_seen_at'])+timedelta(seconds=seconds)
    monkeypatch.setattr(security,'utc_now',lambda:now)
    assert auth_client.get('/api/patients').status_code==status
    if status==401:
        assert auth_client.post('/api/auth/activity',json={},headers=auth_client.csrf).status_code==401


def test_polling_does_not_touch_but_interaction_does(auth_client,monkeypatch):
    from backend.app import security
    c=get_conn();row=c.execute('SELECT * FROM sessions').fetchone();start=datetime.fromisoformat(row['last_seen_at'])
    monkeypatch.setattr(security,'utc_now',lambda:start+timedelta(minutes=14))
    for path in ('/api/health','/api/ai/status','/api/auth/me','/api/internal-notifications/summary'):
        assert auth_client.get(path).status_code==200
    assert c.execute('SELECT last_seen_at FROM sessions').fetchone()[0]==row['last_seen_at']
    r=auth_client.post('/api/auth/activity',json={},headers=auth_client.csrf);assert r.status_code==200
    assert datetime.fromisoformat(r.json()['idleDeadline'])==start+timedelta(minutes=29)
    assert c.execute('SELECT expires_at FROM sessions').fetchone()[0]==row['expires_at']


def test_absolute_ttl_and_clock_rollback_fail_closed(auth_client,monkeypatch):
    from backend.app import security
    row=get_conn().execute('SELECT * FROM sessions').fetchone()
    monkeypatch.setattr(security,'utc_now',lambda:datetime.fromisoformat(row['created_at'])-timedelta(seconds=1))
    assert auth_client.get('/api/patients').status_code==401


def test_expiry_during_medical_transaction_rolls_back(auth_client,monkeypatch):
    from backend.app import security,store
    p=card(auth_client);c=get_conn();row=c.execute('SELECT * FROM sessions').fetchone()
    start=datetime.fromisoformat(row['last_seen_at']);clock=[start+timedelta(seconds=899)]
    monkeypatch.setattr(security,'utc_now',lambda:clock[0])
    # Deterministic direct transaction probe using the actual request connection.
    from backend.app import request_security,db
    state=request_security.RequestState(token=auth_client.cookies.get('clinic_session'),protect_commit=True)
    token=request_security.current.set(state)
    try:
        with pytest.raises(Exception) as exc:
            with db.transaction(c):
                c.execute('UPDATE patients SET complaint=? WHERE id=?',('synthetic-new',p['id']))
                clock[0]=start+timedelta(seconds=900)
        assert getattr(exc.value,'status_code',None)==401
    finally:request_security.current.reset(token)
    assert c.execute('SELECT complaint FROM patients WHERE id=?',(p['id'],)).fetchone()[0]==p['complaint']


def test_revoked_context_after_snapshot_is_not_released(auth_client,monkeypatch):
    from backend.app import read_access,users
    p=card(auth_client);original=read_access._persist
    def revoke(state,*args):
        users.terminate_sessions(get_conn(),state.user.id)
        return original(state,*args)
    monkeypatch.setattr(read_access,'_persist',revoke)
    r=auth_client.get('/api/patients/'+p['id'])
    assert r.status_code==401 and p['name'] not in r.text


@pytest.mark.parametrize('password',['abcdefgh','123456789012345','passwordpassword','парольпарольпароль'])
def test_new_password_policy_rejects_local_blocklist(client,password):
    from backend.app import security
    with pytest.raises(security.WeakPasswordError):security.validate_password_strength(password)


def test_long_phrase_and_legacy_login(client):
    from backend.app import security,users
    from .conftest import create_account
    security.validate_password_strength('Длинная фраза с пробелами без классов')
    for trivial in (' ' * 15, '1' * 20, 'я' * 15):
        with pytest.raises(security.WeakPasswordError):security.validate_password_strength(trivial)
    uid=create_account('legacy.g')
    # Historical hash remains usable; new policy is applied only to create/change.
    get_conn().execute('UPDATE users SET password_hash=? WHERE id=?',(security.hash_password('oldshort'),uid));get_conn().commit()
    assert login(client,'legacy.g','oldshort').status_code==200
    c=get_conn();c.execute("UPDATE users SET status='blocked' WHERE id=?",(uid,));c.commit()
    assert client.get('/api/patients').status_code==401
    assert c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(uid,)).fetchone()[0]==0
    c.execute("UPDATE users SET status='active' WHERE id=?",(uid,));c.commit()
    assert client.get('/api/patients').status_code==401  # the deleted token cannot revive



def test_error_logs_do_not_include_exception_text(auth_client,monkeypatch,caplog):
    from backend.app import store
    p=card(auth_client)
    def fail(*a,**k):raise ValueError('SYNTHETIC-PHI-LOG-MARKER')
    monkeypatch.setattr(store,'patient_to_dict',fail)
    r=auth_client.get('/api/patients/'+p['id'])
    assert r.status_code==500
    assert 'SYNTHETIC-PHI-LOG-MARKER' not in caplog.text+r.text


def test_aliases_are_session_scope_bound_and_opaque(auth_client):
    from backend.app.ai.dto import AliasScope
    from backend.app.ai.tools import ToolPermissionError
    from backend.app import users
    p=card(auth_client);c=get_conn();scope=AliasScope(c,auth_client.cookies.get('clinic_session'))
    dto=scope.reference(c,p['id']);text=json.dumps(dto)
    assert p['id'] not in text and p['name'] not in text and p['diagnosis'] not in text
    assert scope.resolve(c,dto['reference'])==p['id']
    users.terminate_sessions(c,scope.user_id)
    with pytest.raises(ToolPermissionError):scope.resolve(c,dto['reference'])
    assert scope.aliases=={}


def test_aggregate_manifest_uses_actual_contributing_ids(auth_client):
    patient=card(auth_client)
    for path in ('/api/analytics/summary','/api/data-quality/summary'):
        response=auth_client.get(path);assert response.status_code==200
        row=latest_manifest()
        assert {'type':'patient','id':patient['id']} in json.loads(row['objects_json'])
        assert len(row['access_context'])==64


@pytest.mark.parametrize('expire',[False,True])
def test_audit_waits_for_writer_and_rechecks_expiry(auth_client,monkeypatch,expire):
    from backend.app import read_access,config,security
    import sqlite3
    patient=card(auth_client)
    seen=datetime.fromisoformat(get_conn().execute('SELECT last_seen_at FROM sessions').fetchone()[0])
    clock=[seen+timedelta(seconds=1)];monkeypatch.setattr(security,'utc_now',lambda:clock[0])
    entered=Event();original=read_access._persist
    def waiting(*args):
        entered.set();return original(*args)
    monkeypatch.setattr(read_access,'_persist',waiting)
    writer=sqlite3.connect(config.DB_PATH);writer.execute('BEGIN IMMEDIATE')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(auth_client.get,'/api/patients/'+patient['id'])
            try:
                assert entered.wait(5)
                assert not future.done()
                if expire:clock[0]=seen+timedelta(seconds=900)
            finally:writer.commit()
            response=future.result(timeout=10)
        assert response.status_code==(401 if expire else 200)
        if expire:assert patient['name'] not in response.text
        else:assert latest_manifest()['snapshot_sha256']==hashlib.sha256(response.content).hexdigest()
    finally:writer.close()


def test_asgi_error_and_path_logs_are_sanitized(client,caplog):
    import logging,sys
    try:
        raise ValueError('SYNTHETIC-ASGI-PHI-MARKER')
    except ValueError:
        logging.getLogger('uvicorn.error').error('Exception in ASGI application',exc_info=sys.exc_info())
    assert 'SYNTHETIC-ASGI-PHI-MARKER' not in caplog.text
    assert 'ValueError' in caplog.text
    from .test_preprod_access_log import _access_record
    record=_access_record('/api/patients/SYNTHETIC-PATH-PHI-MARKER?search=another')
    for f in logging.getLogger('uvicorn.access').filters:f.filter(record)
    assert 'SYNTHETIC-PATH-PHI-MARKER' not in record.getMessage()


@pytest.mark.parametrize('operation',['create','reset','own','forced'])
def test_password_policy_on_real_api_paths(auth_client,operation):
    from .conftest import TEST_PASSWORD
    if operation=='create':
        body={'username':'policy.g','fullName':'Синтетический Сотрудник','roleId':'doctor',
              'departmentIds':[],'mustChangePassword':False,'password':'short'}
        assert auth_client.post('/api/users',json=body,headers=auth_client.csrf).status_code==422
        body['password']='длинная фраза без требований к классам'
        assert auth_client.post('/api/users',json=body,headers=auth_client.csrf).status_code==201
    elif operation=='reset':
        uid=create_account('reset.g')
        path='/api/users/'+uid+'/reset-password'
        assert auth_client.post(path,json={'newPassword':'short','mustChangePassword':True},headers=auth_client.csrf).status_code==422
        assert auth_client.post(path,json={'newPassword':'длинный временный пароль','mustChangePassword':True},headers=auth_client.csrf).status_code==200
    else:
        if operation=='forced':
            create_account('forced.g',must_change_password=True);assert login(auth_client,'forced.g').status_code==200
            assert auth_client.get('/api/patients').status_code==403
        response=auth_client.post('/api/auth/change-password',json={'currentPassword':TEST_PASSWORD,'newPassword':'short'},headers=auth_client.csrf)
        assert response.status_code==422 and '15' in response.text
        response=auth_client.post('/api/auth/change-password',json={'currentPassword':TEST_PASSWORD,'newPassword':'длинная новая фраза без классов'},headers=auth_client.csrf)
        assert response.status_code==200
        assert auth_client.get('/api/patients').status_code==200


def test_minimized_wire_excludes_raw_medical_fields(auth_client):
    from backend.app.ai.dto import MinimizedProvider
    from backend.app.ai.provider import AIProviderResult
    p=card(auth_client);sent=[]
    class Wire:
        def chat(self,**kwargs):
            sent.append(kwargs)
            return AIProviderResult(content='synthetic',tool_calls=[])
    provider=MinimizedProvider(Wire(),get_conn(),auth_client.cookies.get('clinic_session'),p['id'])
    provider.chat(messages=[{'role':'user','content':p['name']+' '+p['diagnosis']},
                            {'role':'tool','content':json.dumps({'patientId':p['id'],'name':p['name'],'comment':'FREE-TEXT-MARKER','phone':p['phone'],'diagnosis':p['diagnosis']})}],tools=[],max_output_tokens=128)
    payload=json.dumps(sent,ensure_ascii=False)
    assert p['id'] not in payload and p['name'] not in payload and p['diagnosis'] not in payload
    assert p['phone'] not in payload and 'FREE-TEXT-MARKER' not in payload
    assert 'ref_' in payload


def test_access_revocation_while_waiting_for_medical_write(auth_client,monkeypatch):
    from backend.app import db,config,request_security
    import sqlite3
    patient=card(auth_client);entered=Event();original=db._ClinicConnection.execute
    def before_begin(self,sql,parameters=()):
        state=request_security.current.get()
        if sql.strip().upper()=='BEGIN IMMEDIATE' and state is not None and state.protect_commit:
            entered.set()
        return original(self,sql,parameters)
    monkeypatch.setattr(db._ClinicConnection,'execute',before_begin)
    writer=sqlite3.connect(config.DB_PATH);writer.execute('BEGIN IMMEDIATE')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(auth_client.patch,'/api/patients/'+patient['id'],json={'field':'complaint','value':'must-not-persist','expectedVersion':patient['version']},headers=auth_client.csrf)
            try:
                assert entered.wait(5)
                writer.execute("UPDATE users SET role_id='caop_head' WHERE username='dev.tester'")
            finally:writer.commit()
            response=future.result(timeout=10)
        assert response.status_code==409 and response.json()['code']=='ACCESS_CONTEXT_CHANGED'
        assert get_conn().execute('SELECT complaint FROM patients WHERE id=?',(patient['id'],)).fetchone()[0]==patient['complaint']
    finally:writer.close()


@pytest.mark.parametrize('mode',['read','write'])
def test_patient_reassignment_without_user_stamp_change_is_refused(auth_client,monkeypatch,mode):
    from backend.app import db,config,read_access,request_security
    import sqlite3
    first=create_account('scope.first.g',role_id='doctor');second=create_account('scope.second.g',role_id='doctor')
    r=auth_client.post('/api/patients',json=make_patient_payload(treatingDoctorUserId=first),headers=auth_client.csrf)
    assert r.status_code==201;patient=r.json()
    assert login(auth_client,'scope.first.g').status_code==200
    if mode=='read':
        original=read_access._persist
        def moved(*args):
            c=get_conn();c.execute('UPDATE patients SET treating_doctor_user_id=? WHERE id=?',(second,patient['id']));c.commit()
            return original(*args)
        monkeypatch.setattr(read_access,'_persist',moved)
        response=auth_client.get('/api/patients/'+patient['id'])
        assert response.status_code==409 and patient['name'] not in response.text
    else:
        entered=Event();original=db._ClinicConnection.execute
        def waiting(self,sql,parameters=()):
            state=request_security.current.get()
            if sql.strip().upper()=='BEGIN IMMEDIATE' and state is not None and state.protect_commit:entered.set()
            return original(self,sql,parameters)
        monkeypatch.setattr(db._ClinicConnection,'execute',waiting)
        writer=sqlite3.connect(config.DB_PATH);writer.execute('BEGIN IMMEDIATE')
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(auth_client.patch,'/api/patients/'+patient['id'],json={'field':'complaint','value':'must-not-save','expectedVersion':patient['version']},headers=auth_client.csrf)
                try:
                    assert entered.wait(5)
                    writer.execute('UPDATE patients SET treating_doctor_user_id=? WHERE id=?',(second,patient['id']))
                finally:writer.commit()
                response=future.result(timeout=10)
            assert response.status_code==404
        finally:writer.close()
    assert get_conn().execute('SELECT complaint FROM patients WHERE id=?',(patient['id'],)).fetchone()[0]==patient['complaint']
