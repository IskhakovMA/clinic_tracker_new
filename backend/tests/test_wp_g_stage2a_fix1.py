"""Session/flow isolation against the real Clinic ASGI API and synthetic SQLite."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from .conftest import create_account,TEST_PASSWORD
from .test_wp_g_stage2a_mfa import setup_mfa,V


def full_session(c,uid):
    """Verified synthetic initial MFA session; never exposed by an app endpoint."""
    from backend.app import mfa,security,db
    if not mfa.required(c,uid):return security.create_session(c,uid)[:2]
    with db.transaction(c):
        p,user,state,access,generation=mfa.current(c,uid)
        raw,csrf,_=security._insert_session(c,uid)
        factor=c.execute("SELECT credential_id FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(uid,)).fetchone()[0]
        c.execute("INSERT INTO mfa_session_assurance VALUES(?,?,?,?,?,?,?,?,'webauthn_uv')",(security.hash_token(raw),uid,state['auth_epoch'],user['password_hash'],access,generation,p['digest'],factor))
    return raw,csrf


def cookies(client,raw,csrf):
    client.base_url=V['origin'];client.cookies.set('clinic_session',raw,domain='localhost.local',path='/');client.cookies.set('clinic_csrf',csrf,domain='localhost.local',path='/')


def clone(client,raw,csrf=None):
    copy=TestClient(client.app,base_url=V['origin'],raise_server_exceptions=False)
    copy.cookies.set('clinic_session',raw)
    if csrf:copy.cookies.set('clinic_csrf',csrf)
    return copy


def registration(lab):
    client,c,uid,_,_=lab;raw,csrf=full_session(c,uid);cookies(client,raw,csrf)
    r=client.post('/api/auth/mfa/register/options',headers={'X-CSRF-Token':csrf})
    assert r.status_code==200,r.text
    return raw,csrf,client.cookies['clinic_pre_auth'],client.cookies['clinic_pre_auth_csrf']


def submit_registration(client,csrf,flow_csrf):
    return client.post('/api/auth/mfa/register',json=V['people']['operator.a'][0]['registration'],headers={
        'Origin':V['origin'],'X-CSRF-Token':csrf,'X-MFA-CSRF-Token':flow_csrf})


def test_switch_revokes_captured_cookie_only_this_context(setup_mfa):
    client,c,uid,_,_=setup_mfa;other=create_account('switch.a')
    raw,csrf=full_session(c,other);independent,_=full_session(c,other);cookies(client,raw,csrf)
    captured=clone(client,raw);device=clone(client,independent)
    try:
        assert captured.get('/api/patients').status_code==200
        r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
        assert r.status_code==200 and r.json()['stage']=='webauthn'
        assert captured.get('/api/patients').status_code==401
        assert device.get('/api/patients').status_code==200
        assert not client.cookies.get('clinic_session')
    finally:captured.close();device.close()


def test_registration_parent_cookie_mismatch_is_refused(setup_mfa):
    client,c,uid,_,_=setup_mfa;_,_,_,flow_csrf=registration(setup_mfa)
    other=create_account('different.owner');raw,csrf=full_session(c,other);cookies(client,raw,csrf)
    r=submit_registration(client,csrf,flow_csrf)
    assert r.status_code==403,r.text
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='PENDING'").fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='ACTIVE'").fetchone()[0]==2


def test_repeated_correct_password_allocations_are_bounded(setup_mfa,monkeypatch):
    from backend.app import mfa
    client,c,*_=setup_mfa;monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32))
    # Small technical test ceiling exercises the same durable SQL boundary.
    monkeypatch.setattr(mfa,'MAX_FLOWS_PER_USER',3,raising=False)
    statuses=[client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD}).status_code for _ in range(6)]
    assert statuses==[200,200,200,429,429,429]
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==3


@pytest.mark.parametrize('failure',['password','s1','revoke_audit','flow_audit','capacity'])
def test_failed_switch_rolls_back_browser_identity(setup_mfa,failure,monkeypatch):
    from backend.app import mfa,mfa_continuity
    from fastapi import HTTPException
    client,c,uid,_,_=setup_mfa;a=create_account('retained.a');raw,csrf=full_session(c,a);cookies(client,raw,csrf)
    if failure=='s1':
        def hold(*args):raise HTTPException(403,'MFA_SECURITY_REVIEW_HOLD')
        monkeypatch.setattr(mfa_continuity,'require_trust',hold)
    if failure.endswith('audit'):
        event='mfa_browser_session_revoked' if failure=='revoke_audit' else 'mfa_pre_auth_created'
        c.executescript("CREATE TRIGGER injected BEFORE INSERT ON audit_events WHEN NEW.event_type='"+event+"' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    if failure=='capacity':monkeypatch.setattr(mfa,'MAX_FLOWS_TOTAL',0)
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':'wrong' if failure=='password' else TEST_PASSWORD})
    assert r.status_code=={'password':401,'s1':403,'revoke_audit':503,'flow_audit':503,'capacity':429}[failure]
    assert 'stage' not in r.json() and 'publicKey' not in r.json()
    assert client.cookies['clinic_session']==raw
    assert client.get('/api/auth/me').json()['user']['id']==a
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_browser_session_revoked'").fetchone()[0]==0


def test_registration_wrong_context_has_safe_denial_audit(setup_mfa):
    client,c,uid,_,_=setup_mfa;_,_,_,flow_csrf=registration(setup_mfa)
    other=create_account('audited.a');raw,csrf=full_session(c,other);cookies(client,raw,csrf)
    assert submit_registration(client,csrf,flow_csrf).status_code==403
    event=c.execute("SELECT * FROM audit_events WHERE event_type='mfa_registration_parent_denied'").fetchone()
    assert event['actor_user_id']==other and event['target_user_id']==uid
    assert event['detail']=='parent_session_mismatch'
    assert raw not in json.dumps(dict(event)) and flow_csrf not in json.dumps(dict(event))
    assert c.execute("SELECT state FROM mfa_flows").fetchone()[0]=='PENDING'


def test_registration_rejects_different_session_of_same_owner(setup_mfa):
    client,c,uid,_,_=setup_mfa;parent,_,_,flow_csrf=registration(setup_mfa)
    different,csrf=full_session(c,uid);assert different!=parent;cookies(client,different,csrf)
    assert submit_registration(client,csrf,flow_csrf).status_code==403
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='PENDING'").fetchone()[0]==0


def test_session_switch_during_registration_crypto(setup_mfa,monkeypatch):
    from backend.app import mfa_webauthn
    client,c,uid,_,_=setup_mfa;parent,csrf,flow,flow_csrf=registration(setup_mfa)
    a=create_account('switch.target');verifying=threading.Event();release=threading.Event();original=mfa_webauthn.registration
    def verify(*args):
        result=original(*args);verifying.set();assert release.wait(15);return result
    monkeypatch.setattr(mfa_webauthn,'registration',verify)
    pending=clone(client,parent,csrf);pending.cookies.set('clinic_pre_auth',flow)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(submit_registration,pending,csrf,flow_csrf)
            try:
                assert verifying.wait(15)
                r=client.post('/api/auth/login',json={'username':'switch.target','password':TEST_PASSWORD})
                assert r.status_code==200 and r.json()['user']['id']==a
            finally:release.set()
            assert future.result(timeout=15).status_code==401
        assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='PENDING'").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='ACTIVE'").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_registration_parent_denied'").fetchone()[0]==1
    finally:pending.close()


def test_prepared_medical_response_rechecks_revoked_session(setup_mfa,monkeypatch):
    from backend.app import read_access
    client,c,_,_,_=setup_mfa;a=create_account('prepared.a');raw,csrf=full_session(c,a);cookies(client,raw,csrf)
    ready=threading.Event();release=threading.Event();original=read_access.persist
    def persist(*args,**kwargs):
        ready.set();assert release.wait(15);return original(*args,**kwargs)
    monkeypatch.setattr(read_access,'persist',persist)
    pending=clone(client,raw,csrf)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(pending.get,'/api/patients')
            try:
                assert ready.wait(15)
                r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
                assert r.status_code==200 and r.json()['stage']=='webauthn'
            finally:release.set()
            assert future.result(timeout=15).status_code==401
    finally:pending.close()


def test_active_capacity_expires_without_deleting_history(setup_mfa,monkeypatch):
    from backend.app import mfa,security
    client,c,uid,_,_=setup_mfa;monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32))
    tokens=[]
    for _ in range(mfa.MAX_ACTIVE_FLOWS_PER_USER):
        token,csrf,_=mfa.begin(c,uid,c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0]);tokens.append((token,csrf))
    assert len(tokens)==2
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==429 and r.json()['detail']=='MFA_FLOW_ACTIVE_LIMIT'
    now=security.utc_now();monkeypatch.setattr(security,'utc_now',lambda:now+timedelta(seconds=121))
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==200
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==3
    # Expiry frees a live slot, not historical row capacity or replay permission.
    with pytest.raises(HTTPException) as denied:mfa.claim(c,tokens[0][0],tokens[0][1],V['origin'])
    assert denied.value.status_code==401 and denied.value.detail=='MFA_FLOW_EXPIRED_OR_CHANGED'


def test_concurrent_allocations_use_durable_per_user_limit(setup_mfa,monkeypatch):
    from backend.app import mfa,db,config
    client,c,uid,_,_=setup_mfa;monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32));barrier=threading.Barrier(6)
    password_revision=c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0]
    def allocate(_):
        connection=db._connect(config.DB_PATH)
        try:
            barrier.wait()
            try:mfa.begin(connection,uid,password_revision);return 200
            except Exception as e:return getattr(e,'status_code',500)
        finally:connection.close()
    with ThreadPoolExecutor(max_workers=6) as pool:codes=list(pool.map(allocate,range(6)))
    assert sorted(codes)==[200,200,429,429,429,429]
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==2


def fill_rows(c,uid,count):
    import hashlib
    row=dict(c.execute('SELECT * FROM mfa_flows WHERE user_id=?',(uid,)).fetchone());fields=list(row)
    for i in range(count):
        values={**row,'token_hash':hashlib.sha256(('synthetic-archived-flow-'+str(i)).encode()).hexdigest(),'challenge':os.urandom(32),'state':'FAILED'}
        c.execute('INSERT INTO mfa_flows ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',tuple(values[k] for k in fields))
    c.commit()


def test_row_capacity_survives_expiry_and_process_restart(setup_mfa,monkeypatch):
    import subprocess,sys
    from backend.app import mfa,security
    client,c,uid,_,_=setup_mfa;mfa.begin(c,uid,c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0])
    fill_rows(c,uid,mfa.MAX_FLOWS_PER_USER-1)
    now=security.utc_now();monkeypatch.setattr(security,'utc_now',lambda:now+timedelta(days=10))
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==429 and r.json()['detail']=='MFA_FLOW_STORAGE_CAPACITY_REACHED'
    assert 'retry-after' not in r.headers
    script="""import sys,json
from backend.app import mfa,mfa_continuity,db,config
from fastapi import HTTPException
mfa_continuity.require_trust=lambda *args:None
c=db._connect(config.DB_PATH)
u=sys.argv[1]
try:
 mfa.begin(c,u,c.execute('SELECT password_hash FROM users WHERE id=?',(u,)).fetchone()[0])
 print(json.dumps({'status':200}))
except HTTPException as e: print(json.dumps({'status':e.status_code,'code':e.detail,'rows':c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]}))
finally:c.close()
"""
    result=subprocess.run([sys.executable,'-c',script,uid],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)=={'status':429,'code':'MFA_FLOW_STORAGE_CAPACITY_REACHED','rows':128}
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==128


def test_global_capacity_and_legacy_oversize_fail_without_cleanup(setup_mfa,monkeypatch):
    from backend.app import mfa
    client,c,uid,_,_=setup_mfa;mfa.begin(c,uid,c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0])
    fill_rows(c,uid,3);monkeypatch.setattr(mfa,'MAX_FLOWS_TOTAL',3)
    before=[tuple(r) for r in c.execute('SELECT * FROM mfa_flows ORDER BY token_hash')]
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==429
    assert [tuple(r) for r in c.execute('SELECT * FROM mfa_flows ORDER BY token_hash')]==before


def test_registration_denial_audit_failure_is_closed(setup_mfa):
    client,c,_,_,_=setup_mfa;_,_,_,flow_csrf=registration(setup_mfa)
    a=create_account('denial.audit.a');raw,csrf=full_session(c,a);cookies(client,raw,csrf)
    c.executescript("CREATE TRIGGER injected_denial BEFORE INSERT ON audit_events WHEN NEW.event_type='mfa_registration_parent_denied' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    assert submit_registration(client,csrf,flow_csrf).status_code==503
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='PENDING'").fetchone()[0]==0
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='PENDING'


def test_claimed_attempt_occupies_live_slot(setup_mfa,monkeypatch):
    from backend.app import mfa
    _,c,uid,_,_=setup_mfa;monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32))
    revision=c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0]
    token,csrf,_=mfa.begin(c,uid,revision);mfa.claim(c,token,csrf,V['origin']);mfa.begin(c,uid,revision)
    with pytest.raises(HTTPException) as denied:mfa.begin(c,uid,revision)
    assert denied.value.status_code==429 and denied.value.detail=='MFA_FLOW_ACTIVE_LIMIT'
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==2


def test_concurrent_global_last_slot(setup_mfa,monkeypatch):
    from backend.app import mfa,db,config
    _,c,uid,_,_=setup_mfa;monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32))
    revision=c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0]
    mfa.begin(c,uid,revision);fill_rows(c,uid,1)
    monkeypatch.setattr(mfa,'MAX_FLOWS_TOTAL',3);monkeypatch.setattr(mfa,'MAX_ACTIVE_FLOWS_PER_USER',10)
    barrier=threading.Barrier(4)
    def allocate(_):
        conn=db._connect(config.DB_PATH)
        try:
            barrier.wait()
            try:mfa.begin(conn,uid,revision);return 200
            except Exception as e:return getattr(e,'status_code',500)
        finally:conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:codes=list(pool.map(allocate,range(4)))
    assert sorted(codes)==[200,429,429,429]
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==3


def test_failed_new_login_preserves_existing_registration_flow(setup_mfa,monkeypatch):
    from backend.app import mfa
    client,c,_,_,_=setup_mfa;parent,csrf,flow,flow_csrf=registration(setup_mfa)
    monkeypatch.setattr(mfa,'new_challenge',lambda:os.urandom(32))
    c.executescript("CREATE TRIGGER injected_flow BEFORE INSERT ON audit_events WHEN NEW.event_type='mfa_pre_auth_created' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    response=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert response.status_code==503
    assert client.cookies['clinic_session']==parent and client.cookies['clinic_pre_auth']==flow
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==1
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='PENDING'
