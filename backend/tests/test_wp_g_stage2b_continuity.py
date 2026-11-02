"""Isolated lab protocol regressions and narrow real API audit/S1 checks."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from prototype.continuity_lab.model import Lab, Hold, digest


@pytest.fixture
def lab(tmp_path):
    return Lab.fixture(tmp_path/'replaceable', tmp_path/'independent_authority_simulation')


def test_all_security_mutations_and_high_water(lab):
    for kind,value in [('register','factor-1'),('activate','factor-1'),
                       ('policy','required'),('counter_bs',('factor-1',1,True)),
                       ('epoch',None),('recovery_use','synthetic-code-id'),('revoke','factor-1')]:
        previous=lab.current()['sequence']
        s=lab.change(kind,value)
        assert s['sequence']==previous+1
        assert lab.current()==s
    w=lab.witness.read()
    assert w['ever_required'] and w['revoked']==['factor-1']
    assert w['used_recovery']==['synthetic-code-id'] and w['epoch']==2
    assert lab.policy_gate()=='required'
    with pytest.raises(Hold,match='DOWNGRADE'):lab.change('policy','compatibility')
    with pytest.raises(Hold,match='KNOWN_FACTOR'):lab.change('register','factor-1')
    with pytest.raises(Hold,match='ALREADY_USED'):lab.change('recovery_use','synthetic-code-id')
    assert lab.current()==s


@pytest.mark.parametrize('operation',['verify','migrate','migrate'])
def test_c_requires_current_independent_reservation(lab,operation):
    lab.change('policy','required')
    before=lab.current()
    after=lab.maintain(operation)
    assert after['generation']!=before['generation']
    assert after['sequence']==before['sequence']+1
    for key in before:
        if key not in {'sequence','generation','audit_length','audit_tip'}:assert after[key]==before[key]
    assert lab.current()==after
    assert lab.witness.read()['kind']==operation
    # Reverify is another protocol operation, not clearing/rewriting a hold.
    assert lab.maintain('verify')['sequence']==after['sequence']+1


@pytest.mark.parametrize('operation',['verify','migrate'])
def test_security_change_not_laundered_as_maintenance(lab,operation):
    before=lab.current()
    with pytest.raises(Hold,match='SECURITY_CHANGED'):
        lab.maintain(operation,security_transform=lambda s:s.update(epoch=99))
    assert lab.current()==before  # no mutation or reservation was started


@pytest.mark.parametrize('point',['restore.before_replace','witness.after_reserve'])
def test_restore_even_identical_requires_sticky_s1(lab,point):
    def stop(p):
        if p==point:raise OSError(errno.EIO,'synthetic')
    lab.hook=stop
    with pytest.raises((OSError,Hold)):lab.maintain('restore')
    lab.hook=lambda p:None
    for _ in range(2):
        with pytest.raises(Hold):lab.maintain('verify')
        with pytest.raises(Hold):lab.policy_gate()


@pytest.mark.parametrize('mutation',[('revoke','factor'),('recovery_use','used-code'),
                                      ('epoch',None),('policy','required'),
                                      ('counter_bs',('factor',1,True))])
def test_old_database_and_old_sidecars_are_not_current(lab,mutation):
    lab.change('register','factor');lab.change('activate','factor')
    old=lab.db.path.read_bytes();receipt=(lab.root/'receipt.json').read_bytes()
    lab.change(*mutation)
    current=lab.witness.read()
    lab.db.path.write_bytes(old);(lab.root/'receipt.json').write_bytes(receipt)
    assert lab.witness.read()==current
    with pytest.raises(Hold):lab.policy_gate()
    with pytest.raises(Hold):lab.maintain('verify')


@pytest.mark.parametrize('what',['missing','corrupt','old_receipt','wrong_installation'])
def test_no_witness_or_mismatched_receipt_never_compatibility(lab,what):
    lab.change('policy','required')
    if what=='missing':lab.witness.path.unlink()
    if what=='corrupt':lab.witness.path.write_bytes(b'not-sqlite')
    if what=='old_receipt':(lab.root/'receipt.json').write_text('{}')
    if what=='wrong_installation':
        s=lab.db.read();s['installation']='another'
        with lab.db.tx() as c:lab.db.write(c,s)
    with pytest.raises(Hold):lab.policy_gate()
    assert not (lab.witness.path.exists() and lab.witness.path.stat().st_size==0)


POINTS=['witness.reserve.before_commit','witness.after_reserve',
        'database.before_commit','database.after_commit',
        'receipt.before_file_fsync','receipt.after_file_fsync','receipt.after_rename',
        'receipt.before_directory_fsync','receipt.after_directory_fsync',
        'witness.finalize.before_commit']


@pytest.mark.parametrize('point',POINTS)
@pytest.mark.parametrize('error',[errno.EIO,errno.ENOSPC])
def test_fault_between_stores_does_not_issue_trust(lab,point,error):
    before=lab.current()
    def fail(p):
        if p==point:raise OSError(error,'synthetic')
    lab.hook=fail
    with pytest.raises(OSError):lab.change('policy','required')
    restarted=Lab(lab.root,lab.witness_root)
    if point=='witness.reserve.before_commit':
        assert restarted.current()==before  # no DB change, transaction rolled back
    else:
        assert restarted.witness.read()['status']=='PENDING'
        with pytest.raises(Hold):restarted.policy_gate()
        with pytest.raises(Hold):restarted.maintain('verify')


@pytest.mark.parametrize('directory',[False,True])
def test_real_os_fsync_failure_never_publishes_permission(lab,monkeypatch,directory):
    import prototype.continuity_lab.model as model
    original=model.os.fsync
    import stat
    def fail(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode)==directory:
            raise OSError(errno.EIO,'synthetic fsync')
        return original(fd)
    monkeypatch.setattr(model.os,'fsync',fail)
    with pytest.raises(OSError):lab.change('policy','required')
    with pytest.raises(Hold):Lab(lab.root,lab.witness_root).policy_gate()
    assert lab.witness.read()['status']=='PENDING'



@pytest.mark.parametrize('point',['witness.after_reserve','database.before_commit',
    'database.after_commit','receipt.after_rename','receipt.after_directory_fsync',
    'witness.after_finalize'])
def test_sigkill_restart(point,lab):
    from prototype.continuity_lab.worker import killed_mutation
    ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe()
    p=ctx.Process(target=killed_mutation,args=(lab.root,lab.witness_root,point,child))
    p.start();child.close()
    try:
        assert parent.poll(20),'child did not reach barrier'
        assert parent.recv()==point
        os.kill(p.pid,signal.SIGKILL);p.join(10)
        assert p.exitcode==-signal.SIGKILL
    finally:
        if p.is_alive():p.kill();p.join(10)
        parent.close()
    reopened=Lab(lab.root,lab.witness_root)
    if point=='witness.after_finalize':
        # Lost reply after a complete durable commit can be resolved by FRESH read.
        assert reopened.current()['policy']=='required'
    else:
        with pytest.raises(Hold):reopened.policy_gate()
        with pytest.raises(Hold):reopened.maintain('verify')


def test_two_reservations_cas_and_no_timeout_unlock(lab):
    before=lab.current();barrier=threading.Barrier(2)
    def reserve(i):
        barrier.wait()
        try:return lab.witness.reserve(before,'epoch',str(i),lambda p:None)
        except Hold:return None
    with ThreadPoolExecutor(2) as pool:out=list(pool.map(reserve,[1,2]))
    assert sum(r is not None for r in out)==1
    reservation=next(r for r in out if r)
    assert lab.witness.read()==reservation
    with pytest.raises(Hold):Lab(lab.root,lab.witness_root).policy_gate()


def test_concurrent_mutations_have_unique_monotonic_commits(lab):
    barrier=threading.Barrier(4)
    def change(_):
        barrier.wait()
        return Lab(lab.root,lab.witness_root).change('epoch',None)['sequence']
    with ThreadPoolExecutor(4) as pool:sequences=list(pool.map(change,range(4)))
    assert sorted(sequences)==[1,2,3,4]
    assert lab.current()['epoch']==4
    with lab.db.tx() as c:
        assert c.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==4


def test_repeated_finalize_cannot_issue_second_sequence(lab):
    before=lab.current()
    reservation=lab.witness.reserve(before,'epoch','synthetic-operation',lambda p:None)
    altered=dict(reservation,nonce='wrong')
    with pytest.raises(Hold):lab.witness.finalize(altered,before,lambda p:None)
    assert lab.witness.read()==reservation


def test_explicit_cleanup_retains_audit_and_prevents_replay(lab):
    f=lab.allocate(1000)
    lab.flow(f['id'],f['challenge'],1001)
    lab.flow(f['id'],f['challenge'],1002,finish=True)
    with lab.db.tx() as c:before=[tuple(r) for r in c.execute('SELECT * FROM audit')]
    plan=lab.cleanup_plan(eligible_before=1120,now=2000)
    lab.cleanup(plan)
    assert lab.current()['flows']=={}
    assert lab.current()['allocation_serial']==1
    with lab.db.tx() as c:
        rows=[tuple(r) for r in c.execute('SELECT * FROM audit')]
        assert rows[:-1]==before
        assert json.loads(rows[-1][-1])=={'flow_serials':['1'],'removed_count':1}
        with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM audit')
    with pytest.raises(Hold):lab.flow(f['id'],f['challenge'],1001)
    new=lab.allocate(2001)
    assert new['id']!=f['id'] and new['challenge']!=f['challenge']
    with pytest.raises(Hold):lab.flow(new['id'],f['challenge'],2002)
    assert f['challenge'] not in json.dumps(rows)


def test_expired_claim_cleanup_blocks_inflight_finalize(lab):
    f=lab.allocate(1000);lab.flow(f['id'],f['challenge'],1001)
    lab.cleanup(lab.cleanup_plan(eligible_before=1120,now=1120))
    with pytest.raises(Hold):lab.flow(f['id'],f['challenge'],1002,finish=True)
    assert lab.current()['flows']=={}


def test_live_flows_and_stale_plan_not_deleted(lab):
    f=lab.allocate(1000)
    plan=lab.cleanup_plan(eligible_before=1001,now=1001)
    assert plan['ids']==[]
    lab.change('epoch',None)
    with pytest.raises(Hold,match='PLAN_STALE'):lab.cleanup(plan)
    assert f['id'] in lab.current()['flows']
    with pytest.raises(ValueError):lab.cleanup_plan(eligible_before=1002,now=1001)


def test_capacity_can_be_reclaimed_without_approving_retention(lab):
    for n in range(128):lab.allocate(n*121)
    before=lab.current()
    with pytest.raises(Hold,match='ROW_CAPACITY'):lab.allocate(128*121)
    assert lab.current()==before
    plan=lab.cleanup_plan(eligible_before=128*121,now=128*121)
    assert len(plan['ids'])==128
    lab.cleanup(plan)
    for n in range(4):lab.allocate((129+n)*121)
    assert len(lab.current()['flows'])==4
    assert lab.current()['allocation_serial']==132
    with lab.db.tx() as c:assert c.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==133


def test_frozen_rng_does_not_recycle_challenge(lab,monkeypatch):
    import prototype.continuity_lab.model as model
    monkeypatch.setattr(model,'challenge_entropy',lambda:'0'*64)
    f=lab.allocate(1000)
    lab.cleanup(lab.cleanup_plan(eligible_before=1120,now=1120))
    other=lab.allocate(1200)
    assert other['challenge']!=f['challenge'] and other['id']!=f['id']


def test_cleanup_does_not_make_restored_used_flow_current(lab):
    f=lab.allocate(1000)
    old=lab.db.path.read_bytes();receipt=(lab.root/'receipt.json').read_bytes()
    lab.flow(f['id'],f['challenge'],1001)
    lab.cleanup(lab.cleanup_plan(eligible_before=1120,now=2000))
    lab.db.path.write_bytes(old);(lab.root/'receipt.json').write_bytes(receipt)
    with pytest.raises(Hold):lab.flow(f['id'],f['challenge'],1001)


def test_security_snapshot_change_not_hidden_by_same_generation(lab):
    before=lab.current()
    with lab.db.tx() as c:
        s=dict(before,epoch=99);lab.db.write(c,s)
    assert lab.db.read()['generation']==before['generation']
    with pytest.raises(Hold):lab.policy_gate()


def test_lab_rejects_witness_inside_restore_set(tmp_path):
    with pytest.raises(ValueError):Lab(tmp_path,tmp_path/'sidecar')


def test_account_switch_audit_v2_real_api(setup_mfa):
    # setup_mfa is imported below as a fixture only, not its test functions.
    from .conftest import create_account,TEST_PASSWORD
    from .test_wp_g_stage2a_fix1 import full_session,cookies
    client,c,b,_,_=setup_mfa
    a=create_account('stage2b.previous')
    raw,csrf=full_session(c,a);cookies(client,raw,csrf)
    c.execute("INSERT INTO audit_events(created_at,event_type,result,actor_user_id,target_user_id) VALUES('2026-01-01','mfa_browser_session_revoked','ok',?,?)",(a,b))
    c.commit()  # fixture row is durable before the independent HTTP writer
    historic=dict(c.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 1').fetchone())
    response=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert response.status_code==200 and response.json()['stage']=='webauthn'
    row=dict(c.execute("SELECT * FROM audit_events WHERE event_type='mfa_browser_session_revoked' ORDER BY id DESC LIMIT 1").fetchone())
    assert row['actor_user_id']==b and row['target_user_id']==a
    assert json.loads(row['detail'])=={'version':2,'initiator_user_id':b,
        'previous_identity_user_id':a,'new_identity_user_id':b,
        'authentication_level':'password_verified','reason':'identity_switch','outcome':'session_revoked'}
    assert row['result']=='ok'
    assert raw not in json.dumps(row) and csrf not in json.dumps(row)
    assert dict(c.execute('SELECT * FROM audit_events WHERE id=?',(historic['id'],)).fetchone())==historic


def test_production_s1_still_denies_without_witness(setup_mfa,monkeypatch):
    from .conftest import TEST_PASSWORD
    from backend.app import mfa_continuity
    from fastapi import HTTPException
    # Fixture only substitutes trust for positive core tests. Restore real boundary.
    import importlib
    real=importlib.reload(mfa_continuity).require_trust
    monkeypatch.setattr(mfa_continuity,'require_trust',real)
    client,c,uid,_,_=setup_mfa
    response=client.post('/api/auth/login',json={'username':'mfa.synthetic','password':TEST_PASSWORD})
    assert response.status_code==403
    assert response.json()['detail']=='MFA_SECURITY_REVIEW_HOLD'
    assert not client.cookies.get('clinic_session')
    assert c.execute('SELECT COUNT(*) FROM mfa_session_assurance').fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==0
    with pytest.raises(HTTPException):mfa_continuity.require_trust(c,uid,'synthetic')


from .test_wp_g_stage2a_mfa import setup_mfa  # noqa: E402


def test_retry_after_committed_lost_reply_is_ack_only(lab):
    def lost_reply(point):
        if point=='witness.after_finalize':raise OSError(errno.EIO,'synthetic lost reply')
    lab.hook=lost_reply
    with pytest.raises(OSError):lab.change('epoch',None,operation_id='request-1',expected_sequence=0)
    lab.hook=lambda p:None
    assert lab.current()['epoch']==1
    lab.change('epoch',None)
    assert lab.change('epoch',None,operation_id='request-1',expected_sequence=0)=={'acknowledged_sequence':1,'replayed':True}
    assert lab.current()['epoch']==2
    with pytest.raises(Hold):lab.change('policy','required',operation_id='request-1',expected_sequence=0)


def test_concurrent_identical_request_is_deduplicated(lab):
    barrier=threading.Barrier(2)
    def submit(_):
        barrier.wait()
        return Lab(lab.root,lab.witness_root).change('epoch',None,operation_id='one-request',expected_sequence=0)
    with ThreadPoolExecutor(2) as pool:out=list(pool.map(submit,[1,2]))
    assert sum(v.get('replayed',False) for v in out)==1
    assert lab.current()['epoch']==1 and lab.current()['sequence']==1


def test_maintenance_nested_projection_change_is_not_equal(lab):
    lab.change('register','factor')
    with pytest.raises(Hold,match='SECURITY_CHANGED'):
        lab.maintain('migrate',security_transform=lambda s:s['factors']['factor'].update(state='ACTIVE'))
    assert lab.current()['factors']['factor']['state']=='PENDING'


def test_security_audit_cannot_be_silently_truncated(lab):
    f=lab.allocate(1000)
    lab.flow(f['id'],f['challenge'],1001)
    lab.cleanup(lab.cleanup_plan(eligible_before=1120,now=1120))
    with lab.db.tx() as c:
        events=[json.loads(r[0]) for r in c.execute('SELECT detail FROM audit ORDER BY id')]
        assert events[0]['flow_id']==events[1]['flow_id']=='1'
        assert events[0]['subject_id']==events[1]['subject_id']=='synthetic-staff'
        assert f['challenge'] not in json.dumps(events)
        c.execute('DROP TRIGGER audit_no_delete')
        c.execute('DELETE FROM audit WHERE id=1')
    with pytest.raises(Hold,match='AUDIT_NOT_CURRENT'):lab.policy_gate()
