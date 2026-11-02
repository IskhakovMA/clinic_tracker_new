"""Last-factor policy: real lifecycle/SQLite/WebAuthn, synthetic authority only."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from .test_wp_g_stage2c_lifecycle import lab, D
from .test_wp_g_stage2a_fix1 import full_session
from .conftest import app_stopped


def cid(index):
    from backend.app import mfa_webauthn
    return mfa_webauthn.decode(D['factors'][index]['registration']['id'])


def witnessed_session(lab, *, real_login=False, index=1):
    """Coordinate a TEST-ONLY login with the local synthetic witness.

    Stage2C has no production login/witness adapter. Real begin/finish/FIDO2 are
    exercised without rewriting the already recorded challenge or crypto data.
    """
    from backend.app import mfa, mfa_continuity
    with lab.a.exclusive():
        lab.a.start_operation(lab.c)
        reservation=lab.a.reserve(lab.c,'synthetic_fix1_login')
        with lab.mp.context() as mp:
            mp.setattr(mfa_continuity,'require_trust',lambda *args:None)
            if real_login:
                password=lab.c.execute('SELECT password_hash FROM users WHERE id=?',(lab.target,)).fetchone()[0]
                raw,csrf,_=mfa.begin(lab.c,lab.target,password)
                result=mfa.finish(lab.c,raw,csrf,D['origin'],D['factors'][index]['assertions'][1])[:2]
            else:result=full_session(lab.c,lab.target)
        lab.a.commit(lab.c,reservation)
        lab.a.require_current(lab.c)
        return result[0]


def proof(lab,key,token):
    from backend.app import security
    return lab.prove('factor_revocation',{'target':lab.target,'credential':key.hex(),'binding':lab.bind(),'session':security.hash_token(token)})


def revoke(lab,key,token):
    return lab.s.revoke(lab.c,lab.target,key,token,proof(lab,key,token))


def snapshot(lab):
    # Compare every DB row, not only credentials. Never expose secret-bearing SQL.
    database=hashlib.sha256('\n'.join(lab.c.iterdump()).encode()).hexdigest()
    with lab.a.open() as c:
        witness=[c.execute('SELECT * FROM anchor').fetchall(),c.execute('SELECT * FROM used ORDER BY nonce').fetchall()]
    return database,hashlib.sha256(json.dumps(witness).encode()).hexdigest()


def assert_last_refused(lab,token):
    from backend.app import mfa,security
    before=snapshot(lab)
    with pytest.raises(HTTPException) as error:revoke(lab,cid(1),token)
    assert (error.value.status_code,error.value.detail)==(409,'MFA_LAST_ACTIVE_FACTOR_REQUIRES_RECOVERY')
    assert snapshot(lab)==before
    session=lab.c.execute('SELECT * FROM sessions WHERE token_hash=?',(security.hash_token(token),)).fetchone()
    assert session is not None and mfa.session_allowed(lab.c,session)
    assert lab.c.execute('SELECT state FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]=='ACTIVE'
    lab.a.require_current(lab.c)


def test_two_keys_reauthenticate_remaining_then_refuse_last(lab):
    lab.enroll()
    token=witnessed_session(lab)
    assert revoke(lab,cid(0),token)['state']=='REVOKED'
    token=witnessed_session(lab,real_login=True)
    assert_last_refused(lab,token)
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==1
    assert lab.c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_factor_revoked' AND target_user_id=?",(lab.target,)).fetchone()[0]==1
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_notices WHERE target=? AND action='revoke_factor'",(lab.target,)).fetchone()[0]==1
    assert lab.c.execute('SELECT state FROM mfa_credentials WHERE credential_id=?',(cid(0),)).fetchone()[0]=='REVOKED'


@pytest.mark.parametrize('active_count',[1,2])
@pytest.mark.parametrize('lab',['active'],indirect=True)
def test_concurrent_revocations_cannot_remove_all(lab,active_count):
    from backend.app import db,config
    if active_count==1:
        revoke(lab,cid(0),lab.tokens[lab.target])
        token=witnessed_session(lab)
    else:token=lab.tokens[lab.target]
    keys=[cid(1),cid(1)] if active_count==1 else [cid(0),cid(1)]
    jobs=[(key,proof(lab,key,token)) for key in keys]
    before=snapshot(lab);barrier=threading.Barrier(2)
    def submit(job):
        c=db._connect(config.DB_PATH)
        try:
            barrier.wait(timeout=10)
            try:return lab.s.revoke(c,lab.target,job[0],token,job[1])
            except HTTPException as e:return e.status_code,e.detail
        finally:c.close()
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(submit,jobs))
    assert sum(isinstance(r,dict) for r in results)==active_count-1
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==1
    if active_count==1:
        assert results==[(409,'MFA_LAST_ACTIVE_FACTOR_REQUIRES_RECOVERY')]*2
        assert snapshot(lab)==before
    else:assert sum(r==(401,'MFA_FULL_UV_SESSION_REQUIRED') for r in results)==1
    lab.a.require_current(lab.c)


@pytest.mark.parametrize('state',['PENDING','REVOKED'])
@pytest.mark.parametrize('lab',['active'],indirect=True)
def test_pending_and_revoked_do_not_count_as_usable_keys(lab,state):
    from backend.app import mfa_webauthn as w
    revoke(lab,cid(0),lab.tokens[lab.target]);token=witnessed_session(lab)
    if state=='PENDING':
        _,s=w.options('localhost',D['origin'],w.decode(D['handle']),w.decode(D['challenge']),[],True)
        factor=w.registration('localhost',D['origin'],s,D['factors'][2]['registration'])
        lab.mutation(lambda:lab.c.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'PENDING')",(cid(2),lab.target,bytes(factor.credential_data),factor.counter,int(factor.is_backup_eligible()),int(factor.is_backed_up()))))
        assert_last_refused(lab,token)
        assert revoke(lab,cid(2),token)['state']=='REVOKED'
        assert lab.c.execute('SELECT state FROM mfa_credentials WHERE credential_id=?',(cid(2),)).fetchone()[0]=='REVOKED'
    else:
        before=snapshot(lab)
        with pytest.raises(HTTPException) as error:revoke(lab,cid(0),token)
        assert (error.value.status_code,error.value.detail)==(403,'MFA_FACTOR_UNAVAILABLE')
        assert snapshot(lab)==before
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==1


@pytest.mark.parametrize('fault',['authority','audit'])
@pytest.mark.parametrize('lab',['active'],indirect=True)
def test_revoke_authority_and_audit_failure_no_false_success(lab,fault):
    from backend.app import audit
    token=lab.tokens[lab.target];p=proof(lab,cid(0),token)
    if fault=='audit':
        lab.c.executescript("CREATE TRIGGER synthetic_fix1_audit_fault BEFORE INSERT ON audit_events WHEN NEW.event_type='mfa_factor_revoked' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    else:
        lab.mp.setattr(lab.s,'authority',lab.original_authority)
    before=snapshot(lab)
    with pytest.raises(audit.AuditWriteFailed if fault=='audit' else HTTPException):lab.s.revoke(lab.c,lab.target,cid(0),token,p)
    after=snapshot(lab)
    assert after[0]==before[0]
    if fault=='authority':assert after==before
    else:
        with pytest.raises(HTTPException):lab.a.require_current(lab.c)
    assert lab.c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_factor_revoked'").fetchone()[0]==0
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_notices WHERE action='revoke_factor'").fetchone()[0]==0


@pytest.mark.parametrize('change',['role','session'])
@pytest.mark.parametrize('lab',['active'],indirect=True)
def test_session_or_rights_changed_while_waiting_for_coordination(lab,change):
    from backend.app import db,config,security
    token=lab.tokens[lab.target];p=proof(lab,cid(0),token)
    attempted=threading.Event();original=lab.a.exclusive
    @contextmanager
    def waiting():
        attempted.set()
        with original():yield
    def submit():
        c=db._connect(config.DB_PATH)
        try:
            try:lab.s.revoke(c,lab.target,cid(0),token,p)
            except HTTPException as e:return e.status_code,e.detail
        finally:c.close()
    with ThreadPoolExecutor(1) as pool:
        with original():
            lab.mp.setattr(lab.a,'exclusive',waiting)
            future=pool.submit(submit)
            assert attempted.wait(10)
            def prepare(a):
                def apply():
                    if change=='role':lab.c.execute("UPDATE users SET role_id='doctor' WHERE id=?",(lab.target,))
                    else:lab.c.execute('DELETE FROM sessions WHERE token_hash=?',(security.hash_token(token),))
                    return {'state':'synthetic_changed'}
                return apply,lab.target
            lab.s._run_coordinated(lab.c,lab.a,'mfa_synthetic_fix1_changed',prepare)
            before=snapshot(lab)
        assert future.result(timeout=10)==(401,'MFA_FULL_UV_SESSION_REQUIRED')
    assert snapshot(lab)==before
    assert lab.c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==2


@pytest.mark.parametrize('fault',['rollback','generation'])
@pytest.mark.parametrize('lab',['active'],indirect=True)
def test_no_revoke_after_rollback_or_generation_change(lab,tmp_path,fault):
    from backend.app import db,operations_state
    token=lab.tokens[lab.target];p=proof(lab,cid(0),token)
    if fault=='rollback':
        import sqlite3
        path=tmp_path/'old.sqlite';old=sqlite3.connect(path);lab.c.backup(old);old.close()
        revoke(lab,cid(0),token)
        restored=db._connect(path)
        try:
            with pytest.raises(HTTPException):lab.s.revoke(restored,lab.target,cid(1),token,p)
            assert restored.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(lab.target,)).fetchone()[0]==2
        finally:restored.close()
    else:
        from backend.app import config
        with app_stopped():
            operations_state.verify_database(confirm_offline=True)
            c=db._connect(config.DB_PATH)
            try:
                before=hashlib.sha256('\n'.join(c.iterdump()).encode()).hexdigest()
                with pytest.raises(HTTPException):lab.s.revoke(c,lab.target,cid(0),token,p)
                assert hashlib.sha256('\n'.join(c.iterdump()).encode()).hexdigest()==before
            finally:c.close()


def test_compromise_dispute_is_not_silently_converted_to_recovery(lab):
    lab.enroll()
    notice=lab.c.execute('SELECT id FROM mfa_notices WHERE target=? LIMIT 1',(lab.target,)).fetchone()[0]
    p=lab.prove('notice_dispute',{'notice':notice,'target':lab.target,'binding':lab.bind()})
    assert lab.s.dispute(lab.c,notice,p)['medicalSession'] is False
    assert lab.c.execute('SELECT state FROM mfa_user_state WHERE user_id=?',(lab.target,)).fetchone()[0]=='SECURITY_REVIEW_HOLD'
    before=snapshot(lab)
    with pytest.raises(HTTPException):lab.recovery()
    assert snapshot(lab)==before
    assert lab.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(lab.target,)).fetchone()[0]==0
