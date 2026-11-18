"""Real Clinic API/SQLite with synthetic Chromium-signed public WebAuthn vectors.
Trust substitution is TEST-ONLY monkeypatch, not an application configuration.
"""
import base64
import copy
import json
import multiprocessing
import os
import signal
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import pytest
from .test_wp_b_database_safety import backup_key
from .conftest import create_account,get_conn,TEST_PASSWORD,app_stopped

def public_vectors(value):
    # Authenticator bytes are literal integer arrays in the portable fixture.
    if isinstance(value,dict):
        return {k:(base64.urlsafe_b64encode(bytes(v)).rstrip(b'=').decode() if k=='authenticatorData' and isinstance(v,list) else public_vectors(v)) for k,v in value.items()}
    if isinstance(value,list):return [public_vectors(v) for v in value]
    return value

V=public_vectors(json.loads((Path(__file__).parent/'fixtures/mfa_public_vectors.json').read_text()))

@pytest.fixture
def setup_mfa(client,monkeypatch):
    from backend.app import mfa,mfa_webauthn,mfa_continuity
    uid=create_account('mfa.synthetic',must_change_password=False)
    c=get_conn();pairs=V['people']['synthetic.staff'];challenge=mfa_webauthn.decode(V['challenge'])
    c.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,rp_id='localhost',origin=?",(json.dumps([uid]),V['origin']))
    c.execute('DELETE FROM mfa_user_state WHERE user_id=?',(uid,))
    c.execute("INSERT INTO mfa_user_state VALUES(?,?,0,'ACTIVE')",(uid,mfa_webauthn.decode(pairs[0]['user_handle'])))
    for p in pairs:
        _,state=mfa_webauthn.options('localhost',V['origin'],mfa_webauthn.decode(p['user_handle']),challenge,[],True)
        data=mfa_webauthn.registration('localhost',V['origin'],state,p['registration'])
        c.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'ACTIVE')",(data.credential_data.credential_id,uid,bytes(data.credential_data),data.counter,int(data.is_backup_eligible()),int(data.is_backed_up())))
    c.commit()
    monkeypatch.setattr(mfa,'new_challenge',lambda:challenge)
    monkeypatch.setattr(mfa_continuity,'require_trust',lambda *args:None)
    return client,c,uid,pairs,monkeypatch

def login(lab):
    client,c,uid,pairs,mp=lab
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==200,r.text
    assert r.json()['authenticated'] is False and r.json()['stage']=='webauthn'
    assert not client.cookies.get('clinic_session')
    return client.cookies.get('clinic_pre_auth'),client.cookies.get('clinic_pre_auth_csrf')

def finish(lab,body=None):
    client,_,_,pairs,_=lab
    return client.post('/api/auth/mfa/authenticate',json=body or pairs[0]['assertion'],headers={'Origin':V['origin'],'X-CSRF-Token':client.cookies.get('clinic_pre_auth_csrf','')})

def test_real_login_finalize_and_assurance(setup_mfa):
    from backend.app import security
    lab=setup_mfa;client,c,uid,_,_=lab;login(lab);r=finish(lab)
    assert r.status_code==200,r.text
    assert r.json()['authenticated'] and r.json()['user']['id']==uid
    assert client.get('/api/patients').status_code==200
    a=c.execute('SELECT * FROM mfa_session_assurance').fetchone();assert a['method']=='webauthn_uv'
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_login_success'").fetchone()[0]==1
    assert security.validate_session(c,client.cookies['clinic_session'])
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='SUCCEEDED'

@pytest.mark.parametrize('path,method',[('/api/patients','get'),('/api/users','get'),('/api/export','get'),('/api/analytics/export','post'),('/api/ai/chat','post'),('/api/auth/change-password','post'),('/api/auth/activity','post'),('/api/import/commit','post')])
def test_pre_auth_isolation(setup_mfa,path,method):
    lab=setup_mfa;client,c,*_=lab;login(lab)
    response=getattr(client,method)(path,**({'json':{}} if method=='post' else {}))
    assert response.status_code==401,response.text
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==0

@pytest.mark.parametrize('field,value',[('userHandle',None),('userHandle','AA'),('signature','AAAA'),('clientDataJSON','AAAA')])
def test_invalid_response_consumes_challenge(setup_mfa,field,value):
    lab=setup_mfa;client,c,_,pairs,_=lab;login(lab);body=copy.deepcopy(pairs[0]['assertion']);body['response'][field]=value
    # Null is allowed for explicit allowCredentials; all non-null handles are bound.
    r=finish(lab,body)
    if field=='userHandle' and value is None:assert r.status_code==200
    else:
        assert r.status_code==401,r.text
        assert finish(lab).status_code==409
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('change',["UPDATE users SET status='blocked'","UPDATE users SET role_id='doctor'","UPDATE mfa_user_state SET auth_epoch=1","UPDATE mfa_credentials SET state='REVOKED'","UPDATE mfa_user_state SET state='SECURITY_REVIEW_HOLD'"])
def test_revoke_before_finalize(setup_mfa,change):
    lab=setup_mfa;client,c,*_=lab;login(lab);c.execute(change);c.commit()
    expected=403 if 'SECURITY_REVIEW_HOLD' in change else 401
    assert finish(lab).status_code==expected
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('stage',['claim','finalize'])
def test_audit_failure_no_session(setup_mfa,stage):
    lab=setup_mfa;client,c,*_=lab;login(lab)
    event='mfa_challenge_claimed' if stage=='claim' else 'mfa_login_success'
    c.executescript("CREATE TRIGGER fail_mfa_audit BEFORE INSERT ON audit_events WHEN NEW.event_type='"+event+"' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    assert finish(lab).status_code==503
    assert not client.cookies.get('clinic_session')
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM mfa_session_assurance').fetchone()[0]==0
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]==('PENDING' if stage=='claim' else 'CLAIMED')

def test_terminate_pre_auth(setup_mfa):
    from backend.app import users
    lab=setup_mfa;_,c,uid,*_=lab;login(lab);users.terminate_sessions(c,uid)
    assert finish(lab).status_code==409

def test_s1_default_not_local_generation(setup_mfa,monkeypatch):
    from backend.app import mfa_continuity
    def blocked(*args):
        from fastapi import HTTPException
        raise HTTPException(403,'MFA_SECURITY_REVIEW_HOLD')
    lab=setup_mfa;client,c,*_=lab;monkeypatch.setattr(mfa_continuity,'require_trust',blocked)
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==403 and r.json()['detail']=='MFA_SECURITY_REVIEW_HOLD'
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

def test_foreign_valid_signature_denied(setup_mfa):
    lab=setup_mfa;_,c,*_=lab;login(lab)
    assert finish(lab,V['people']['operator.a'][0]['assertion']).status_code==401
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('mutation',["DELETE FROM mfa_policy","UPDATE mfa_policy SET user_ids='[1]'","UPDATE mfa_policy SET origin='http://evil.invalid'","UPDATE mfa_policy SET user_ids='[]'"])
def test_policy_corruption_denies_even_legacy(setup_mfa,mutation):
    lab=setup_mfa;client,c,*_=lab;c.execute(mutation);c.commit()
    r=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert r.status_code==403 and not client.cookies.get('clinic_session')

def test_expiry(setup_mfa,monkeypatch):
    from backend.app import security
    lab=setup_mfa;_,c,*_=lab;login(lab);now=security.utc_now();monkeypatch.setattr(security,'utc_now',lambda:now+timedelta(seconds=121))
    assert finish(lab).status_code==401
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('header',['origin','csrf'])
def test_missing_origin_csrf(setup_mfa,header):
    lab=setup_mfa;client,c,_,pairs,_=lab;_,csrf=login(lab)
    headers={'origin':V['origin'],'x-csrf-token':csrf};headers.pop('origin' if header=='origin' else 'x-csrf-token')
    assert client.post('/api/auth/mfa/authenticate',json=pairs[0]['assertion'],headers=headers).status_code==403
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='PENDING'

def test_no_legacy_session_factory_for_required(setup_mfa):
    from backend.app import security
    _,c,uid,*_=setup_mfa
    with pytest.raises(security.CredentialChanged):security.create_session(c,uid)
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

def test_password_change_no_session_reissue(setup_mfa):
    lab=setup_mfa;client,c,*_=lab;login(lab);assert finish(lab).status_code==200
    r=client.post('/api/auth/change-password',json={'currentPassword':TEST_PASSWORD,'newPassword':'New-synthetic-Password-935'},headers={'X-CSRF-Token':client.cookies['clinic_csrf']})
    assert r.status_code==200,r.text
    assert r.json()['reauthenticationRequired']
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert client.get('/api/patients').status_code==401

def test_session_hold_after_success(setup_mfa,monkeypatch):
    from backend.app import mfa_continuity
    from fastapi import HTTPException
    lab=setup_mfa;client,_,*_=lab;login(lab);assert finish(lab).status_code==200
    def hold(*args):raise HTTPException(403,'MFA_SECURITY_REVIEW_HOLD')
    monkeypatch.setattr(mfa_continuity,'require_trust',hold)
    assert client.get('/api/patients').status_code==401
    assert client.get('/api/auth/me').json()=={'authenticated':False}

@pytest.mark.parametrize('column,value',[('user_id','another'),('credential_id',b'another'),('credential_data',b'other'),('be',1)])
def test_immutable_factor(setup_mfa,column,value):
    _,c,*_=setup_mfa
    with pytest.raises(sqlite3.IntegrityError):c.execute('UPDATE mfa_credentials SET '+column+'=?',(value,))
    c.rollback()

def test_tombstone(setup_mfa):
    _,c,*_=setup_mfa;c.execute("UPDATE mfa_credentials SET state='REVOKED'");c.commit()
    with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE mfa_credentials SET state='ACTIVE'")
    c.rollback()
    with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM mfa_credentials')
    c.rollback()

def test_claim_concurrency(setup_mfa):
    from backend.app import mfa,db,config
    lab=setup_mfa;_,c,*_=lab;token,csrf=login(lab);barrier=threading.Barrier(2)
    def claim(_):
        conn=db._connect(config.DB_PATH)
        try:
            barrier.wait()
            try:mfa.claim(conn,token,csrf,V['origin']);return 200
            except Exception as e:return getattr(e,'status_code',500)
        finally:conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(claim,range(2)))
    assert sorted(statuses)==[200,409]
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='CLAIMED'
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0




def test_sigkill_durable_claim(setup_mfa):
    from backend.app import config
    lab=setup_mfa;_,c,*_=lab;token,csrf=login(lab)
    from tools.tests.mfa_fault_worker import kill_after_claim
    ctx=multiprocessing.get_context('spawn');a,b=ctx.Pipe();p=ctx.Process(target=kill_after_claim,args=(str(config.DB_PATH),token,csrf,V['origin'],b));p.start()
    try:
        assert a.poll(20) and a.recv()=='durable_claim';p.join(15);assert p.exitcode==-signal.SIGKILL
    finally:
        if p.is_alive():p.kill();p.join()
    assert c.execute('SELECT state FROM mfa_flows').fetchone()[0]=='CLAIMED'
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert finish(lab).status_code==409

@pytest.mark.parametrize('change',["UPDATE users SET role_id='doctor'","UPDATE users SET status='blocked'","UPDATE mfa_credentials SET state='REVOKED'","UPDATE mfa_user_state SET auth_epoch=auth_epoch+1"])
def test_revocation_during_crypto_before_write_lock(setup_mfa,change,monkeypatch):
    from backend.app import mfa_webauthn
    lab=setup_mfa;_,c,*_=lab;login(lab);original=mfa_webauthn.assertion
    def verify(*args):
        result=original(*args);c.execute(change);c.commit();return result
    monkeypatch.setattr(mfa_webauthn,'assertion',verify)
    assert finish(lab).status_code==(409 if 'mfa_credentials' in change else 401)
    assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('case',['normal','bad_uv','bad_up','bad_signature','counter_low','be_changed','invalid_bs'])
def test_rp_adapter_signed_cases(client,case):
    from backend.app import mfa_webauthn as w
    handle=w.decode(V['user']);challenge=w.decode(V['challenge'])
    _,state=w.options('localhost',V['origin'],handle,challenge,[],True)
    data=w.registration('localhost',V['origin'],state,V['registration'])
    cred=data.credential_data
    stored={'credential_id':cred.credential_id,'credential_data':bytes(cred),'counter':data.counter,'be':data.is_backup_eligible()}
    _,state=w.options('localhost',V['origin'],handle,challenge,[cred])
    if case=='normal':assert w.assertion('localhost',V['origin'],state,V['vectors'][case],stored,handle)
    else:
        with pytest.raises(ValueError):w.assertion('localhost',V['origin'],state,V['vectors'][case],stored,handle)

@pytest.mark.parametrize('what',['origin','rp','challenge','handle','owner'])
def test_rp_binding(client,what):
    from backend.app import mfa_webauthn as w
    handle=w.decode(V['user']);challenge=w.decode(V['challenge'])
    _,state=w.options('localhost',V['origin'],handle,challenge,[],True);data=w.registration('localhost',V['origin'],state,V['registration'])
    stored={'credential_id':data.credential_data.credential_id,'credential_data':bytes(data.credential_data),'counter':data.counter,'be':data.is_backup_eligible()}
    origin=V['origin'] if what!='origin' else 'http://localhost:1';rp='localhost' if what!='rp' else 'different.invalid'
    _,state=w.options('localhost',V['origin'],handle,challenge if what!='challenge' else bytes(32),[data.credential_data])
    response=copy.deepcopy(V['vectors']['normal'])
    if what=='handle':response['response']['userHandle']='AA'
    if what=='owner':stored['credential_id']=b'foreign'
    with pytest.raises(ValueError):w.assertion(rp,origin,state,response,stored,handle)


def test_admin_reset_retains_mfa_and_revokes_session(setup_mfa):
    from backend.app import users,security
    lab=setup_mfa;client,c,uid,*_=lab;login(lab);assert finish(lab).status_code==200
    old=client.cookies['clinic_session']
    # The same service used by staff reset; an administrator does not activate or remove factors.
    users.reset_password(c,uid,'New-synthetic-password-975')
    assert security.validate_session(c,old) is None
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='ACTIVE'").fetchone()[0]==2
    from backend.app import mfa
    assert mfa.required(c,uid)
    with pytest.raises(security.CredentialChanged):security.create_session(c,uid)


def test_no_first_enrollment_password_only(client):
    uid=create_account('unenrolled.synthetic')
    c=get_conn();c.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,origin=?,rp_id='localhost'",(json.dumps([uid]),V['origin']));c.commit()
    r=client.post('/api/auth/login',json={'username':'unenrolled.synthetic','password':TEST_PASSWORD})
    assert r.status_code==403 and c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert client.post('/api/auth/mfa/register',json={}).status_code==401


def test_registration_options_requires_full_mfa(setup_mfa):
    lab=setup_mfa;client,c,*_=lab;login(lab)
    assert client.post('/api/auth/mfa/register/options').status_code==401
    assert finish(lab).status_code==200
    # Use a fresh random challenge for the separate registration ceremony.
    from backend.app import mfa
    lab[-1].setattr(mfa,'new_challenge',lambda:os.urandom(32))
    response=client.post('/api/auth/mfa/register/options',headers={'X-CSRF-Token':client.cookies['clinic_csrf']})
    assert response.status_code==200,response.text
    assert c.execute("SELECT COUNT(*) FROM mfa_flows WHERE purpose='register'").fetchone()[0]==1
    assert c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='ACTIVE'").fetchone()[0]==2


def test_default_continuity_has_no_trust_bypass(client):
    from backend.app import mfa_continuity
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as refused:mfa_continuity.require_trust(get_conn(),'synthetic','same-generation')
    assert refused.value.status_code==403 and refused.value.detail=='MFA_SECURITY_REVIEW_HOLD'


def test_server_assigns_opaque_handles_without_activation(client):
    a=create_account('opaque.one');b=create_account('opaque.two');c=get_conn()
    rows=c.execute('SELECT * FROM mfa_user_state WHERE user_id IN (?,?)',(a,b)).fetchall()
    assert len(rows)==2 and rows[0]['user_handle']!=rows[1]['user_handle']
    assert all(len(r['user_handle'])==32 and r['state']=='ENROLLMENT_REQUIRED' for r in rows)
    assert c.execute('SELECT COUNT(*) FROM mfa_session_assurance').fetchone()[0]==0


def test_v30_offline_migration_preserves_medical_and_no_assurance(client,backup_key):
    from backend.app import db,config,operations_state,mfa_schema
    c=get_conn()
    c.execute("INSERT INTO patients(id,name,priority,stage,arrival,created_at,diagnosis) VALUES('mfa-migration-p','Синтетический','normal','exam','2026-01-01','2026-01-01','C34.1')")
    c.execute("INSERT INTO history_entries(id,patient_id,seq,stage,entered_at) VALUES('mfa-migration-h','mfa-migration-p',0,'exam','2026-01-01')")
    c.execute("INSERT INTO events(id,history_entry_id,seq,date) VALUES('mfa-migration-e','mfa-migration-h',0,'2026-01-02')")
    c.commit()
    expected={table:[tuple(r) for r in c.execute('SELECT * FROM '+table)] for table in ('patients','events','history_entries')}
    with app_stopped():
        # Build the exact approved v30 shape, not a permissive version label.
        raw=db._connect(config.DB_PATH)
        from backend.app.mfa_lifecycle_schema import TABLES as LIFECYCLE_TABLES
        for table in reversed(LIFECYCLE_TABLES):raw.execute('DROP TABLE '+table)
        for table in reversed(mfa_schema.TABLES):raw.execute('DROP TABLE '+table)
        from .conftest import strip_gq_v33
        strip_gq_v33(raw)
        raw.execute('UPDATE schema_version SET version=30');raw.commit();raw.close()
        from backend.app import maintenance
        assert maintenance.identify_backup_build(config.DB_PATH,30)=='wpg-fd7e6ec'
        with pytest.raises(db.SchemaUpgradeRequiredError):db.connect_live(config.DB_PATH)
        maintenance.migrate_database(config.DB_PATH,confirm_offline=True)
        with pytest.raises(operations_state.AdmissionRequired):operations_state.require_permit()
        operations_state.verify_database(confirm_offline=True)
        raw=db._connect(config.DB_PATH)
        try:
            assert raw.execute('SELECT version FROM schema_version').fetchone()[0]== 33
            assert raw.execute('SELECT mode FROM mfa_policy').fetchone()[0]=='stage1_compatibility'
            assert raw.execute('SELECT COUNT(*) FROM mfa_session_assurance').fetchone()[0]==0
            for table,rows in expected.items():assert [tuple(r) for r in raw.execute('SELECT * FROM '+table)]==rows
        finally:raw.close()


def test_assurance_cannot_borrow_other_owner_factor(setup_mfa):
    from backend.app import mfa_webauthn as w
    lab=setup_mfa;client,c,*_=lab;login(lab);assert finish(lab).status_code==200
    other=create_account('other.owner');pair=V['people']['operator.a'][0]
    _,state=w.options('localhost',V['origin'],w.decode(pair['user_handle']),w.decode(V['challenge']),[],True)
    data=w.registration('localhost',V['origin'],state,pair['registration'])
    c.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'ACTIVE')",(data.credential_data.credential_id,other,bytes(data.credential_data),data.counter,int(data.is_backup_eligible()),int(data.is_backed_up())))
    # Corrupt a technical binding to prove validation checks the credential's owner too.
    c.execute('UPDATE mfa_session_assurance SET credential_id=?',(data.credential_data.credential_id,));c.commit()
    assert client.get('/api/patients').status_code==401


def test_real_v31_restore_does_not_admit_recovered_factor(client,backup_key,tmp_path):
    from backend.app import mfa_webauthn as w,db,config,maintenance,operations_state
    uid=create_account('restore.mfa');c=get_conn();pair=V['people']['synthetic.staff'][0]
    c.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,origin=?,rp_id='localhost'",(json.dumps([uid]),V['origin']))
    c.execute("UPDATE mfa_user_state SET state='ACTIVE' WHERE user_id=?",(uid,))
    _,state=w.options('localhost',V['origin'],w.decode(pair['user_handle']),w.decode(V['challenge']),[],True)
    data=w.registration('localhost',V['origin'],state,pair['registration'])
    c.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'ACTIVE')",(data.credential_data.credential_id,uid,bytes(data.credential_data),data.counter,int(data.is_backup_eligible()),int(data.is_backed_up())));c.commit()
    backup=tmp_path/'mfa-v31.cbk';maintenance.backup_database(config.DB_PATH,backup)
    old=operations_state.require_permit()['generation']
    with app_stopped():
        maintenance.restore_database(backup,config.DB_PATH,confirm_offline=True)
        operations_state.verify_database(confirm_offline=True)
        assert operations_state.require_permit()['generation']!=old
        raw=db._connect(config.DB_PATH)
        try:assert raw.execute("SELECT COUNT(*) FROM mfa_credentials WHERE state='ACTIVE'").fetchone()[0]==1
        finally:raw.close()
    response=client.post('/api/auth/login',json={'username':'restore.mfa','password':TEST_PASSWORD})
    assert response.status_code==403 and response.json()['detail']=='MFA_SECURITY_REVIEW_HOLD'
    assert not client.cookies.get('clinic_session')
