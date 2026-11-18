"""Real v32 server transitions; synthetic authority/public Chromium vectors only."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from fastapi import HTTPException
from .conftest import create_account,get_conn,TEST_PASSWORD,app_stopped
from .test_wp_g_stage2a_mfa import public_vectors,V
from .test_wp_g_stage2a_fix1 import full_session
from tools.tests.mfa_stage2c_authority import SyntheticAuthority

D=public_vectors(json.loads((Path(__file__).parent/'fixtures/mfa_stage2c_public_vectors.json').read_text()))


class ServiceLab:
    def __init__(self,client,c,target,operators,tokens,a,service,monkeypatch):
        self.client=client;self.c=c;self.target=target;self.ops=operators;self.tokens=tokens;self.a=a;self.s=service;self.mp=monkeypatch
    def prove(self,kind,binding,**claims):
        if kind in {'notice_acknowledge','notice_dispute','enrollment_recipient'}:claims.setdefault('assurance','independent_identity')
        if kind=='recovery_approval':claims.setdefault('assurance','webauthn_uv_stepup')
        return self.a.make_proof(kind,binding,**claims)
    def bind(self,u=None):return self.s.binding(self.c,u or self.target)
    def grant(self):
        p=self.prove('enrollment_authorization',{'target':self.target,'binding':self.bind(),'action':'first_enrollment'})
        raw=self.s.authorize_enrollment(self.c,self.target,p)['grant']
        return self.redeem(raw)
    def redeem(self,raw):
        from backend.app import security
        p=self.prove('enrollment_recipient',{'grant':security.hash_token(raw),'target':self.target,'binding':self.bind()})
        return raw,self.s.redeem(self.c,raw,p)['csrf']
    def register(self,g,index):
        f=self.s.options(self.c,*g)
        r=self.s.finish(self.c,*g,f['flow'],D['origin'],D['factors'][index]['registration'])
        return bytes.fromhex(r['credential']),r['notice']
    def possess(self,g,cid,index,domain=None,counter=0):
        f=self.s.options(self.c,*g,credential=cid)
        p=self.prove('factor_independence',{'target':self.target,'credential':cid.hex(),'binding':self.bind()},domain=domain or 'synthetic-device-'+str(index))
        return self.s.finish(self.c,*g,f['flow'],D['origin'],D['factors'][index]['assertions'][counter],p)
    def notice_proof(self,nid,action,**claims):
        n=self.c.execute('SELECT * FROM mfa_notices WHERE id=?',(nid,)).fetchone()
        binding={'notice':nid,'target':n['target'],'action':n['action'],'binding':n['binding']}
        if action!='transmit':binding['dispatch']=n['dispatch_id']
        if action=='acknowledge':binding['receipt']=self.c.execute('SELECT id FROM mfa_notice_receipts WHERE notice_id=?',(nid,)).fetchone()[0]
        return self.prove('notice_'+action,binding,**claims)
    def confirm(self,nid):
        for action in ('transmit','deliver','acknowledge'):
            kwargs={'receipt':'synthetic-receipt-'+nid} if action=='deliver' else {}
            self.s.notice_transition(self.c,nid,action,self.notice_proof(nid,action,**kwargs))
    def activate(self,g):
        from backend.app import security
        p=self.prove('activation',{'target':self.target,'binding':self.bind(),'grant':security.hash_token(g[0])})
        return self.s.activate(self.c,*g,p)
    def enroll(self):
        g=self.grant()
        for i in range(2):
            cid,n=self.register(g,i);self.possess(g,cid,i);self.confirm(n)
        return g,self.activate(g)
    def recovery(self,target=None):
        target=target or self.target
        p=self.prove('recovery_identity',{'target':target,'binding':self.bind(target)})
        case=self.s.request_recovery(self.c,target,p);self.confirm(case['notice']);return case
    def approval_proof(self,case,operator,token=None):
        from backend.app import security
        row=self.c.execute('SELECT * FROM mfa_recovery_cases WHERE id=?',(case,)).fetchone()
        return self.prove('recovery_approval',{'case':case,'target':row['target'],'binding':row['binding'],'actor':operator,'actor_binding':self.bind(operator),'session':security.hash_token(token or self.tokens[operator])})
    def approve(self,case,operator):
        return self.s.approve(self.c,case,self.tokens[operator],self.approval_proof(case,operator))
    def mutation(self,fn):
        # Authorized test-only mutation through the same durable reservation gate.
        def prepare(a):
            def apply():fn();return {'state':'synthetic'}
            return apply,self.target
        return self.s._run(self.c,'mfa_synthetic_test_mutation',prepare)


@pytest.fixture
def lab(client,tmp_path,monkeypatch,request):
    from backend.app import mfa,mfa_continuity,mfa_lifecycle as s,mfa_webauthn as w,security
    target=create_account('stage2c.synthetic')
    c=get_conn();ops=[create_account('stage2c.operator.'+str(i)) for i in range(3)]
    c.execute('DELETE FROM mfa_user_state WHERE user_id=?',(target,))
    active=getattr(request,'param','fresh')=='active'
    c.execute('INSERT INTO mfa_user_state VALUES(?,?,0,?)',(target,w.decode(D['handle']),'ACTIVE' if active else 'ENROLLMENT_REQUIRED'))
    for i,uid in enumerate(ops):
        pairs=V['people'][['operator.a','operator.b','operator.c'][i]]
        c.execute('DELETE FROM mfa_user_state WHERE user_id=?',(uid,))
        c.execute("INSERT INTO mfa_user_state VALUES(?,?,0,'ACTIVE')",(uid,w.decode(pairs[0]['user_handle'])))
        for pair in pairs:
            _,state=w.options('localhost',V['origin'],w.decode(pair['user_handle']),w.decode(V['challenge']),[],True)
            d=w.registration('localhost',V['origin'],state,pair['registration'])
            c.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'ACTIVE')",(d.credential_data.credential_id,uid,bytes(d.credential_data),d.counter,int(d.is_backup_eligible()),int(d.is_backed_up())))
        c.execute('INSERT INTO mfa_recovery_operators VALUES(?,?,0,1)',(uid,'operator-domain-'+str(i)))
    c.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,rp_id='localhost',origin=?",(json.dumps([target,*ops]),D['origin']))
    if active or getattr(request,'param','fresh')=='legacy_pending':
        for i in range(2):
            _,state=w.options('localhost',D['origin'],w.decode(D['handle']),w.decode(D['challenge']),[],True)
            d=w.registration('localhost',D['origin'],state,D['factors'][i]['registration'])
            cid=d.credential_data.credential_id
            c.execute('INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,?)',(cid,target,bytes(d.credential_data),0,int(d.is_backup_eligible()),int(d.is_backed_up()),'ACTIVE' if active else 'PENDING'))
    c.commit()
    monkeypatch.setattr(mfa_continuity,'require_trust',lambda *args:None)
    tokens={uid:full_session(c,uid)[0] for uid in ops}
    if active:tokens[target]=full_session(c,target)[0]
    a=SyntheticAuthority((tmp_path/'synthetic-witness.sqlite').resolve(),c,security.utc_now)
    original=s.authority
    monkeypatch.setattr(s,'authority',lambda conn:a)
    monkeypatch.setattr(mfa_continuity,'require_trust',lambda conn,*args:a.require_current(conn))
    monkeypatch.setattr(mfa,'new_challenge',lambda:w.decode(D['challenge']))
    result=ServiceLab(client,c,target,ops,tokens,a,s,monkeypatch);result.original_authority=original
    return result


def test_first_two_factor_enrollment_no_medical_session(lab):
    from backend.app import security
    g,result=lab.enroll()
    assert result=={'state':'ACTIVE','medicalSession':False,'reauthenticationRequired':True}
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==2
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE token_hash=?',(security.hash_token(g[0]),)).fetchone()[0]==0
    with pytest.raises(HTTPException):lab.s.options(lab.c,*g)
    with pytest.raises(HTTPException):lab.redeem(g[0])


@pytest.mark.parametrize('condition',['password_only','one_factor','no_possession','no_delivery','no_employee_ack','same_device','wrong_origin','wrong_handle','bad_signature'])
def test_enrollment_guards(lab,condition):
    if condition=='password_only':
        with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,{'issuer':'synthetic-independent-channel'})
        return
    g=lab.grant();cid,n=lab.register(g,0)
    if condition=='wrong_origin':
        f=lab.s.options(lab.c,*g,credential=cid)
        with pytest.raises(HTTPException):lab.s.finish(lab.c,*g,f['flow'],'https://invalid.local',D['factors'][0]['assertions'][0])
        return
    if condition in {'wrong_handle','bad_signature'}:
        f=lab.s.options(lab.c,*g,credential=cid);response=copy.deepcopy(D['factors'][0]['assertions'][0])
        if condition=='wrong_handle':response['response']['userHandle']='AAAAAAAA'
        else:response['response']['signature']='AAAA'
        with pytest.raises(HTTPException):lab.s.finish(lab.c,*g,f['flow'],D['origin'],response)
        assert lab.c.execute('SELECT state FROM mfa_lifecycle_challenges WHERE id=?',(f['flow'],)).fetchone()[0]=='FAILED'
        return
    if condition!='no_possession':lab.possess(g,cid,0)
    if condition not in {'no_delivery','no_employee_ack'}:lab.confirm(n)
    elif condition=='no_employee_ack':
        lab.s.notice_transition(lab.c,n,'transmit',lab.notice_proof(n,'transmit'))
        lab.s.notice_transition(lab.c,n,'deliver',lab.notice_proof(n,'deliver',receipt='receipt-one'))
    if condition!='one_factor':
        other,n2=lab.register(g,1)
        if condition=='same_device':
            with pytest.raises(HTTPException):lab.possess(g,other,1,domain='synthetic-device-0')
        else:lab.possess(g,other,1);lab.confirm(n2)
    with pytest.raises(HTTPException):lab.activate(g)
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==0


@pytest.mark.parametrize('target',['staff',0,1,2])
@pytest.mark.parametrize('reverse',[False,True])
def test_three_operator_recovery_every_target(lab,target,reverse):
    uid=lab.target if target=='staff' else lab.ops[target]
    case=lab.recovery(uid)
    eligible=[u for u in lab.ops if u!=uid];eligible=eligible[::-1] if reverse else eligible
    epoch=lab.c.execute('SELECT auth_epoch FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()[0]
    assert lab.approve(case['case'],eligible[0])['state']=='PENDING'
    grant=lab.approve(case['case'],eligible[1]);assert grant['scope']=='EnrollmentOnly' and grant['medicalSession'] is False
    assert lab.c.execute('SELECT auth_epoch FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()[0]==epoch+1
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(uid,)).fetchone()[0]==0
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(uid,)).fetchone()[0]==0
    with pytest.raises(HTTPException):lab.approve(case['case'],eligible[0])


@pytest.mark.parametrize('attack',['self','duplicate','wrong_case','wrong_target','wrong_session','revoked_operator','role_changed','no_two_operators','no_stepup','expired','missing_notice'])
def test_recovery_refuses_changed_or_substituted_authority(lab,attack):
    from backend.app import users,security
    target=lab.ops[0] if attack=='self' else lab.target
    case=lab.recovery(target);actor=lab.ops[0] if attack=='self' else lab.ops[1]
    if attack=='duplicate':
        lab.approve(case['case'],actor)
    elif attack in {'revoked_operator','role_changed'}:
        lab.approve(case['case'],lab.ops[0])
        def change():
            if attack=='revoked_operator':lab.c.execute('UPDATE mfa_recovery_operators SET active=0 WHERE user_id=?',(lab.ops[0],))
            else:lab.c.execute("UPDATE users SET role_id='doctor' WHERE id=?",(lab.ops[0],))
        lab.mutation(change)
    elif attack=='no_two_operators':
        def disable():lab.c.execute('UPDATE mfa_recovery_operators SET active=0 WHERE user_id IN (?,?)',(lab.ops[1],lab.ops[2]))
        lab.mutation(disable);lab.approve(case['case'],lab.ops[0])
    elif attack=='expired':
        now=security.utc_now();lab.mp.setattr(security,'utc_now',lambda:now+timedelta(hours=1));lab.a.clock=security.utc_now
    elif attack=='missing_notice':
        # Legitimate second case, without confirmed independent notice.
        p=lab.prove('recovery_identity',{'target':target,'binding':lab.bind(target)})
        case=lab.s.request_recovery(lab.c,target,p)
    proof=lab.approval_proof(case['case'],actor)
    if attack in {'wrong_case','wrong_target','wrong_session','no_stepup'}:
        body=copy.deepcopy(proof['body']);body.pop('nonce');body.pop('expires');body.pop('issuer');kind=body.pop('kind');bind=body.pop('binding')
        if attack=='no_stepup':body['assurance']='password_only'
        else:bind[{'wrong_case':'case','wrong_target':'target','wrong_session':'session'}[attack]]='wrong'
        proof=lab.prove(kind,bind,**body)
    with pytest.raises(HTTPException):lab.s.approve(lab.c,case['case'],lab.tokens[actor],proof)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0


def test_notification_states_receipt_origin_replay_and_dispute(lab):
    g=lab.grant();cid,n=lab.register(g,0)
    assert lab.c.execute('SELECT state FROM mfa_notices WHERE id=?',(n,)).fetchone()[0]=='CREATED'
    with pytest.raises(HTTPException):lab.s.notice_transition(lab.c,n,'deliver',{'issuer':'synthetic-independent-channel'})
    lab.confirm(n)
    assert lab.c.execute('SELECT state FROM mfa_notices WHERE id=?',(n,)).fetchone()[0]=='EMPLOYEE_ACKNOWLEDGED'
    p=lab.prove('notice_dispute',{'notice':n,'target':lab.target,'binding':lab.bind()})
    lab.s.dispute(lab.c,n,p)
    assert lab.c.execute('SELECT state FROM mfa_notices WHERE id=?',(n,)).fetchone()[0]=='DISPUTED'
    assert lab.c.execute('SELECT state FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]=='SECURITY_REVIEW_HOLD'
    with pytest.raises(HTTPException):lab.s.options(lab.c,*g)
    with pytest.raises(HTTPException):lab.s.notice_transition(lab.c,n,'transmit',lab.notice_proof(n,'transmit'))


def test_recovery_grant_redeem_is_one_time(lab):
    case=lab.recovery();lab.approve(case['case'],lab.ops[0]);raw=lab.approve(case['case'],lab.ops[1])['grant']
    g=lab.redeem(raw)
    with pytest.raises(HTTPException):lab.redeem(raw)
    assert lab.s.options(lab.c,*g)['scope']=='EnrollmentOnly'
    lab.client.cookies.clear();lab.client.cookies.set('clinic_session',raw)
    for path in ('/api/patients','/api/users','/api/export'):
        assert lab.client.get(path).status_code==401


def test_lifecycle_authority_unavailable_is_fail_closed(lab):
    lab.mp.setattr(lab.s,'authority',lab.original_authority)
    with pytest.raises(HTTPException) as error:lab.s.authorize_enrollment(lab.c,lab.target,{})
    assert error.value.status_code==403
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0


@pytest.mark.parametrize('point',['before_recovery_grant','before_database_commit','after_database_commit','before_witness_finalize_commit'])
def test_fault_holds_without_medical_grant(lab,point):
    case=lab.recovery();lab.approve(case['case'],lab.ops[0])
    proof=lab.approval_proof(case['case'],lab.ops[1])
    def fail(p):
        if p==point:raise OSError(5,'synthetic EIO')
    lab.a.hook=fail
    with pytest.raises(OSError):lab.s.approve(lab.c,case['case'],lab.tokens[lab.ops[1]],proof)
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0
    lab.a.hook=lambda p:None
    with pytest.raises(HTTPException):lab.a.require_current(lab.c)


def test_required_audit_rolls_back_recovery_grant(lab):
    case=lab.recovery();lab.approve(case['case'],lab.ops[0])
    lab.c.executescript("CREATE TRIGGER synthetic_audit_fault BEFORE INSERT ON audit_events WHEN NEW.event_type='mfa_recovery_approval' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    proof=lab.approval_proof(case['case'],lab.ops[1])
    from backend.app.audit import AuditWriteFailed
    with pytest.raises(AuditWriteFailed):lab.s.approve(lab.c,case['case'],lab.tokens[lab.ops[1]],proof)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_approvals').fetchone()[0]==1


def test_old_security_database_policy_and_grant_rollback_fail(lab,tmp_path):
    from backend.app import db
    snapshot=tmp_path/'old.sqlite';old=sqlite3.connect(snapshot);lab.c.backup(old);old.close()
    g=lab.grant()
    # Copy state into a separate synthetic connection; witness stays current.
    old=db._connect(snapshot)
    try:
        with pytest.raises(HTTPException):lab.a.require_current(old)
    finally:old.close()
    def downgrade():lab.c.execute("UPDATE mfa_policy SET mode='stage1_compatibility',user_ids='[]',rp_id=NULL,origin=NULL")
    # Out-of-protocol policy mutation cannot grant lifecycle permission.
    downgrade();lab.c.commit()
    with pytest.raises(HTTPException):lab.s.options(lab.c,*g)


@pytest.mark.parametrize('state',['missing','malformed'])
def test_damaged_witness_refuses_without_repair(lab,state):
    if state=='missing':lab.a.path.unlink()
    else:lab.a.path.write_bytes(b'not a database')
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,{})
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0


def test_no_reenrollment_of_revoked_credential(lab):
    g,result=lab.enroll()
    case=lab.recovery();lab.approve(case['case'],lab.ops[0]);raw=lab.approve(case['case'],lab.ops[1])['grant'];g=lab.redeem(raw)
    f=lab.s.options(lab.c,*g)
    with pytest.raises(HTTPException) as error:lab.s.finish(lab.c,*g,f['flow'],D['origin'],D['factors'][0]['registration'])
    assert error.value.status_code==409
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='REVOKED'",(lab.target,)).fetchone()[0]==2


def test_admin_password_reset_invalidates_enrollment_grant(lab):
    from backend.app import users
    g=lab.grant()
    def reset():users.reset_password(lab.c,lab.target,'Different-synthetic-password-991')
    # users.reset_password owns transactions; represent its real mutation first,
    # then current authority must refuse rather than adopting it automatically.
    reset()
    with pytest.raises(HTTPException):lab.s.options(lab.c,*g)
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==0


@pytest.mark.parametrize('same_actor',[False,True])
def test_concurrent_approvals_one_grant(lab,same_actor):
    from backend.app import db,config
    case=lab.recovery();barrier=threading.Barrier(3)
    actors=[lab.ops[0]]*3 if same_actor else lab.ops
    jobs=[(u,lab.approval_proof(case['case'],u)) for u in actors]
    def submit(job):
        c=db._connect(config.DB_PATH)
        try:
            barrier.wait()
            try:return lab.s.approve(c,case['case'],lab.tokens[job[0]],job[1])
            except HTTPException as e:return e.status_code
        finally:c.close()
    with ThreadPoolExecutor(3) as pool:results=list(pool.map(submit,jobs))
    assert sum(isinstance(r,dict) and 'grant' in r for r in results)==(0 if same_actor else 1)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==(0 if same_actor else 1)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_approvals').fetchone()[0]==(1 if same_actor else 2)


def test_concurrent_registration_does_not_duplicate_credential(lab):
    from backend.app import db,config
    g=lab.grant();flows=[lab.s.options(lab.c,*g)['flow'] for _ in range(2)]
    barrier=threading.Barrier(2)
    def submit(fid):
        c=db._connect(config.DB_PATH)
        try:
            barrier.wait()
            try:return lab.s.finish(c,*g,fid,D['origin'],D['factors'][0]['registration'])
            except HTTPException as e:return e.status_code
        finally:c.close()
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(submit,flows))
    assert sum(isinstance(r,dict) for r in results)==1
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_factor_evidence').fetchone()[0]==1


@pytest.mark.parametrize('point',['before_recovery_grant','after_database_commit','after_witness_finalize_commit'])
def test_sigkill_lifecycle_durable_boundary(lab,point):
    import multiprocessing,signal
    from backend.app import config
    from tools.tests.mfa_stage2c_worker import approve_and_pause
    case=lab.recovery();lab.approve(case['case'],lab.ops[0])
    proof=lab.approval_proof(case['case'],lab.ops[1])
    ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe()
    p=ctx.Process(target=approve_and_pause,args=(config.DATA_DIR,lab.a.path,lab.a.key,case['case'],lab.tokens[lab.ops[1]],proof,point,child))
    p.start();child.close()
    try:
        assert parent.poll(20),'barrier not reached'
        assert parent.recv()==point
        os.kill(p.pid,signal.SIGKILL);p.join(10);assert p.exitcode==-signal.SIGKILL
    finally:
        if p.is_alive():p.kill();p.join(10)
        parent.close()
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0
    count=lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]
    assert count==(0 if point=='before_recovery_grant' else 1)
    if point=='after_witness_finalize_commit':
        lab.a.require_current(lab.c)  # committed hash only, no lost bearer recovery
    else:
        with pytest.raises(HTTPException):lab.a.require_current(lab.c)


def test_real_receipt_fsync_failure_keeps_hold(lab,monkeypatch):
    import prototype.continuity_lab.model as model
    def fail(fd):raise OSError(28,'synthetic ENOSPC')
    monkeypatch.setattr(model.os,'fsync',fail)
    proof=lab.prove('enrollment_authorization',{'target':lab.target,'binding':lab.bind(),'action':'first_enrollment'})
    with pytest.raises(OSError):lab.s.authorize_enrollment(lab.c,lab.target,proof)
    with pytest.raises(HTTPException):lab.a.require_current(lab.c)
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0


def test_receipt_or_employee_proof_cannot_be_transferred(lab):
    g=lab.grant();_,n1=lab.register(g,0);_,n2=lab.register(g,1)
    lab.s.notice_transition(lab.c,n1,'transmit',lab.notice_proof(n1,'transmit'))
    proof=lab.notice_proof(n1,'deliver',receipt='shared-receipt')
    lab.s.notice_transition(lab.c,n2,'transmit',lab.notice_proof(n2,'transmit'))
    with pytest.raises(HTTPException):lab.s.notice_transition(lab.c,n2,'deliver',proof)
    lab.s.notice_transition(lab.c,n1,'deliver',proof)
    with pytest.raises(HTTPException):lab.s.notice_transition(lab.c,n2,'deliver',lab.notice_proof(n2,'deliver',receipt='shared-receipt'))
    ack=lab.notice_proof(n1,'acknowledge')
    with pytest.raises(HTTPException):lab.s.notice_transition(lab.c,n2,'acknowledge',ack)


def test_completed_recovery_reenrolls_new_keys_only(lab):
    lab.enroll()
    case=lab.recovery();lab.approve(case['case'],lab.ops[0]);grant=lab.approve(case['case'],lab.ops[1])
    g=lab.redeem(grant['grant'])
    for i in (2,3):
        cid,n=lab.register(g,i);lab.possess(g,cid,i);lab.confirm(n)
    assert lab.activate(g)['state']=='ACTIVE'
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='REVOKED'",(lab.target,)).fetchone()[0]==2
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==2


def test_lost_one_factor_replaced_with_new_credential(lab):
    from backend.app import mfa,security
    lab.enroll()
    def synthetic_session():
        # TEST ONLY: same initial UV-session fixture technique as Stage2A.
        p,user,state,access,generation=mfa.current(lab.c,lab.target)
        cid=next(r[0] for r in lab.c.execute("SELECT credential_id FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)))
        def prepare(a):
            def apply():
                raw,csrf,_=security._insert_session(lab.c,lab.target)
                lab.c.execute("INSERT INTO mfa_session_assurance VALUES(?,?,?,?,?,?,?,?,'webauthn_uv')",(security.hash_token(raw),lab.target,state['auth_epoch'],user['password_hash'],access,generation,p['digest'],cid))
                return {'test_session':raw}
            return apply,lab.target
        return lab.s._run(lab.c,'mfa_synthetic_session_fixture',prepare)['test_session']
    token=synthetic_session()
    from backend.app import mfa_webauthn as w
    old=w.decode(D['factors'][0]['registration']['id'])
    proof=lab.prove('factor_revocation',{'target':lab.target,'credential':old.hex(),'binding':lab.bind(),'session':security.hash_token(token)})
    result=lab.s.revoke(lab.c,lab.target,old,token,proof)
    assert result['state']=='REVOKED'
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0
    token=synthetic_session()
    proof=lab.prove('additional_enrollment',{'target':lab.target,'binding':lab.bind(),'session':security.hash_token(token)})
    raw=lab.s.authorize_additional(lab.c,lab.target,token,proof)['grant'];g=lab.redeem(raw)
    remaining=w.decode(D['factors'][1]['registration']['id'])
    lab.possess(g,remaining,1,counter=1)
    notice=lab.c.execute('SELECT notice_id FROM mfa_factor_evidence WHERE credential_id=?',(remaining,)).fetchone()[0];lab.confirm(notice)
    new,n=lab.register(g,2);lab.possess(g,new,2);lab.confirm(n)
    assert lab.activate(g)['state']=='ACTIVE'
    assert lab.c.execute('SELECT state FROM mfa_credentials WHERE credential_id=?',(old,)).fetchone()[0]=='REVOKED'
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==2


def test_sqlite_insert_fault_rolls_back_limited_grant(lab):
    lab.c.executescript("CREATE TRIGGER synthetic_grant_fault BEFORE INSERT ON mfa_enrollment_grants BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    p=lab.prove('enrollment_authorization',{'target':lab.target,'binding':lab.bind(),'action':'first_enrollment'})
    with pytest.raises(sqlite3.IntegrityError):lab.s.authorize_enrollment(lab.c,lab.target,p)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0
    with pytest.raises(HTTPException):lab.a.require_current(lab.c)


def test_authority_policy_extreme_duration_refused_before_write(lab):
    lab.a.limits={**lab.a.limits,'grant':10**100}
    p=lab.prove('enrollment_authorization',{'target':lab.target,'binding':lab.bind(),'action':'first_enrollment'})
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,p)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==0


def test_explicit_v31_to_v32_migration_preserves_clinical_and_offline_admission(data_dir,backup_key):
    from backend.app import db,config,operations_state,maintenance
    from backend.app.mfa_lifecycle_schema import TABLES
    from .test_wp_b_database_safety import _cli
    c=db._connect(config.DB_PATH);db.init_db(c)
    c.execute("INSERT INTO patients(id,name,priority,stage,arrival,created_at,diagnosis) VALUES('stage2c-p','Synthetic','normal','exam','2026-01-01','2026-01-01','C34.1')")
    c.execute("INSERT INTO history_entries(id,patient_id,seq,stage,entered_at,note) VALUES('stage2c-h','stage2c-p',0,'exam','2026-01-01','synthetic')")
    c.execute("INSERT INTO events(id,history_entry_id,seq,date) VALUES('stage2c-e','stage2c-h',0,'2026-01-01')")
    before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('patients','history_entries','events','audit_events')}
    for table in reversed(TABLES):c.execute('DROP TABLE '+table)
    from .conftest import strip_gq_v33
    strip_gq_v33(c)
    c.execute('UPDATE schema_version SET version=31');c.commit();c.close()
    assert maintenance.identify_backup_build(config.DB_PATH,31)=='wpg2b-be4adab'
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==1
    result=_cli(data_dir,'migrate-db','--confirm-offline');assert result.returncode==0
    assert (data_dir/operations_state.PENDING).exists()
    with pytest.raises(operations_state.AdmissionRequired):operations_state.require_permit()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    c=db.connect_live(config.DB_PATH)
    try:
        assert c.execute('SELECT version FROM schema_version').fetchone()[0]==config.SCHEMA_VERSION== 33
        for t,rows in before.items():
            # CLI adds its own maintenance audit, never edits any previous record.
            current=[tuple(r) for r in c.execute('SELECT * FROM '+t)]
            assert current[:len(rows)]==rows
        for t in TABLES:assert c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==0
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:c.close()


from .test_wp_b_database_safety import data_dir,backup_key


def test_grant_revoked_during_registration_crypto(lab,monkeypatch):
    from backend.app import mfa_webauthn as w,db,config
    g=lab.grant();f=lab.s.options(lab.c,*g);entered=threading.Event();release=threading.Event();original=w.registration
    def wait(*args):
        result=original(*args);entered.set();assert release.wait(15);return result
    monkeypatch.setattr(w,'registration',wait)
    def register():
        c=db._connect(config.DB_PATH)
        try:
            try:lab.s.finish(c,*g,f['flow'],D['origin'],D['factors'][0]['registration']);return 200
            except HTTPException as e:return e.status_code
        finally:c.close()
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(register)
        try:
            assert entered.wait(15)
            lab.mutation(lambda:lab.c.execute("UPDATE mfa_enrollment_grants SET state='REVOKED' WHERE target=?",(lab.target,)))
        finally:release.set()
        assert future.result(timeout=15)==401
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_credentials WHERE user_id=?',(lab.target,)).fetchone()[0]==0


def test_signed_evidence_is_one_use_and_tampering_denied(lab):
    expected={'target':lab.target,'binding':lab.bind(),'action':'first_enrollment'}
    proof=lab.prove('enrollment_authorization',expected)
    tampered=copy.deepcopy(proof);tampered['body']['binding']['target']=lab.ops[0]
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,tampered)
    lab.s.authorize_enrollment(lab.c,lab.target,proof)
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,proof)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==1


def test_pending_credential_cannot_activate_with_password_only(lab):
    g=lab.grant();lab.register(g,0)
    with pytest.raises(HTTPException):lab.s.activate(lab.c,*g,{'issuer':'synthetic-independent-channel','password':TEST_PASSWORD})
    assert lab.c.execute("SELECT state FROM mfa_user_state WHERE user_id=?",(lab.target,)).fetchone()[0]=='ENROLLMENT_REQUIRED'


def test_dispute_hold_cannot_be_cleared_by_new_enrollment_or_recovery(lab):
    g=lab.grant();_,notice=lab.register(g,0)
    p=lab.prove('notice_dispute',{'notice':notice,'target':lab.target,'binding':lab.bind()})
    lab.s.dispute(lab.c,notice,p)
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,{})
    with pytest.raises(HTTPException):lab.s.request_recovery(lab.c,lab.target,{})
    assert lab.c.execute('SELECT state FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]=='SECURITY_REVIEW_HOLD'


@pytest.mark.parametrize('lab',['legacy_pending'],indirect=True)
def test_stage2a_pending_needs_explicit_review_possession_and_notice(lab):
    from backend.app import mfa_webauthn as w,security
    g=lab.grant()
    for i in range(2):
        cid=w.decode(D['factors'][i]['registration']['id'])
        with pytest.raises(HTTPException):lab.s.options(lab.c,*g,credential=cid)
        expected={'target':lab.target,'credential':cid.hex(),'binding':lab.bind(),'grant':security.hash_token(g[0])}
        result=lab.s.review_pending(lab.c,*g,cid,lab.prove('pending_factor_review',expected))
        with pytest.raises(HTTPException):lab.activate(g)
        lab.possess(g,cid,i);lab.confirm(result['notice'])
    assert lab.activate(g)['state']=='ACTIVE'


def test_first_enrollment_proof_cannot_bypass_recovery_history(lab):
    lab.enroll()
    lab.mutation(lambda:lab.s._invalidate(lab.c,lab.target,'ENROLLMENT_REQUIRED'))
    before=lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]
    proof=lab.prove('enrollment_authorization',{'target':lab.target,'binding':lab.bind(),'action':'first_enrollment'})
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(lab.c,lab.target,proof)
    assert lab.c.execute('SELECT COUNT(*) FROM mfa_enrollment_grants').fetchone()[0]==before
    case=lab.recovery();lab.approve(case['case'],lab.ops[0]);result=lab.approve(case['case'],lab.ops[1])
    assert result['scope']=='EnrollmentOnly' and result['medicalSession'] is False


def test_activation_invalidates_earlier_recovery_case(lab):
    case=lab.recovery();lab.approve(case['case'],lab.ops[0])
    before=lab.c.execute('SELECT auth_epoch FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]
    lab.enroll()
    assert lab.c.execute('SELECT auth_epoch FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]==before+1
    with pytest.raises(HTTPException):lab.approve(case['case'],lab.ops[1])
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_enrollment_grants WHERE state='ISSUED'").fetchone()[0]==0


def test_generation_change_never_reanchors_lifecycle_authority(lab):
    from backend.app import operations_state
    before=operations_state.require_permit()['generation']
    with app_stopped():operations_state.verify_database(confirm_offline=True)
    after=operations_state.require_permit()['generation']
    assert after!=before
    c=get_conn()
    with pytest.raises(HTTPException):lab.a.require_current(c)
    with pytest.raises(HTTPException):lab.s.authorize_enrollment(c,lab.target,{})


def test_recovery_and_notice_audit_have_exact_safe_correlations(lab):
    case=lab.recovery();lab.approve(case['case'],lab.ops[0]);grant=lab.approve(case['case'],lab.ops[1])
    events=lab.c.execute("SELECT * FROM audit_events WHERE event_type='mfa_recovery_approval' ORDER BY id").fetchall()
    assert len(events)==2
    for e in events:
        assert e['actor_user_id'] in lab.ops and e['target_user_id']==lab.target
        assert json.loads(e['detail'])['case']==case['case']
        assert grant['grant'] not in e['detail']
    rows=lab.c.execute("SELECT detail FROM audit_events WHERE event_type IN ('mfa_notice_transmit','mfa_notice_deliver','mfa_notice_acknowledge')").fetchall()
    assert len(rows)==3 and all(json.loads(r[0])['notice']==case['notice'] for r in rows)
