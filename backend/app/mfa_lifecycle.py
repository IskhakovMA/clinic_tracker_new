"""Disabled-by-default v32 server lifecycle. No production authority/channel exists.

All mutations require independently current authority, not a caller-supplied issuer
string. Tests replace authority() in process; no environment/CLI bypass exists.
No function issues a medical session or resets S1. No retention/cleanup is added.
"""
import hashlib
import json
import secrets
from datetime import timedelta
from fastapi import HTTPException
from fido2.webauthn import AttestedCredentialData
from . import audit, mfa, mfa_continuity, mfa_webauthn, operations_state, policy, security, users


def deny(code='MFA_LIFECYCLE_UNAVAILABLE',status=403):
    raise HTTPException(status,code)


def authority(conn):
    # This deliberately executes even for a restored compatibility policy.
    mfa_continuity.require_trust(conn,'lifecycle',operations_state.require_permit()['generation'])
    deny('MFA_LIFECYCLE_AUTHORITY_UNAVAILABLE')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def binding(conn,uid):
    u=policy.load_current_user(conn,uid)
    row=conn.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()
    state=conn.execute('SELECT * FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()
    if not u or u.status!='active' or state is None:deny('MFA_SUBJECT_UNAVAILABLE')
    if state['state']=='SECURITY_REVIEW_HOLD':deny('MFA_SECURITY_REVIEW_HOLD')
    return fingerprint({'user':uid,'epoch':state['auth_epoch'],'password_revision':row[0],
        'access':mfa.session_context.access_context(u),'policy':mfa.load_policy(conn)['digest'],
        'generation':operations_state.require_permit()['generation']})


def expires(a,name):
    # Durations are authority-policy inputs, not new production retention defaults.
    try:seconds=a.limits[name]
    except (KeyError,TypeError,AttributeError):deny('MFA_AUTHORITY_POLICY_INVALID')
    if type(seconds) is not int or seconds<=0:deny('MFA_AUTHORITY_POLICY_INVALID')
    try:return (security.utc_now()+timedelta(seconds=seconds)).isoformat()
    except (OverflowError,ValueError):deny('MFA_AUTHORITY_POLICY_INVALID')


def live(row):
    if row is None or row['expires']<=security.utc_now().isoformat():deny('MFA_STATE_EXPIRED',401)
    return row


def _proof(a,kind,proof,expected):
    # Adapter must authenticate origin, freshness, exact binding and anti-replay.
    claims=a.verify(kind,proof,expected)
    if not isinstance(claims,dict):deny('MFA_EVIDENCE_INVALID')
    return claims


def _run(conn,event,prepare):
    """Validate under write lock; reserve BEFORE writes; audit is atomic with DB.
    A failed reserved operation holds authority, never silently reconciles it.
    """
    a=authority(conn)
    with a.exclusive():
        return _run_coordinated(conn,a,event,prepare)


def _run_coordinated(conn,a,event,prepare):
    if conn.in_transaction:raise RuntimeError('lifecycle requires an outermost transaction')
    reservation=None
    conn.execute('BEGIN IMMEDIATE')
    try:
        a.start_operation(conn)
        for name in ('grant','notice','case','challenge'):expires(a,name)
        prepared=prepare(a)
        apply,target=prepared[:2]
        actor=prepared[2] if len(prepared)>2 else None
        reservation=a.reserve(conn,event)
        result=apply()
        metadata={k:v for k,v in result.items() if k in {'state','case','notice','flow','credential','scope','possession','reauthenticationRequired'}}
        metadata.update(version=1,authentication_level='webauthn_uv' if actor else 'independent_authority')
        audit.record_required_or_fail(conn,event,actor_user_id=actor,target_user_id=target,
                                     detail=json.dumps(metadata,sort_keys=True,separators=(',',':')))
        a.checkpoint('before_database_commit')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    a.checkpoint('after_database_commit')
    a.commit(conn,reservation)
    a.require_current(conn)
    return result


def _notice(conn,a,target,action):
    n=secrets.token_hex(16)
    conn.execute("INSERT INTO mfa_notices VALUES(?,?,?,?,?,'CREATED',NULL)",
                 (n,target,action,binding(conn,target),expires(a,'notice')))
    return n


def _grant(conn,a,target,case=None):
    raw=secrets.token_urlsafe(32)
    conn.execute("INSERT INTO mfa_enrollment_grants VALUES(?,?,?,?,'ISSUED','EnrollmentOnly',NULL,?)",
                 (security.hash_token(raw),target,binding(conn,target),expires(a,'grant'),case))
    return {'grant':raw,'scope':'EnrollmentOnly','medicalSession':False,**({'case':case} if case else {})}


def authorize_enrollment(conn,target,proof):
    def prepare(a):
        expected={'target':target,'binding':binding(conn,target),'action':'first_enrollment'}
        _proof(a,'enrollment_authorization',proof,expected)
        if (conn.execute("SELECT 1 FROM mfa_credentials WHERE user_id=? AND state IN ('ACTIVE','REVOKED')",(target,)).fetchone()
                or conn.execute('SELECT 1 FROM mfa_enrollment_grants WHERE target=?',(target,)).fetchone()):
            deny('MFA_RECOVERY_OR_FULL_SESSION_REQUIRED')
        return lambda:_grant(conn,a,target),target
    return _run(conn,'mfa_enrollment_authorized',prepare)


def redeem(conn,raw,proof):
    h=security.hash_token(raw)
    def prepare(a):
        g=live(conn.execute('SELECT * FROM mfa_enrollment_grants WHERE token_hash=?',(h,)).fetchone())
        if g['state']!='ISSUED' or g['binding']!=binding(conn,g['target']):deny('MFA_GRANT_NOT_CURRENT',401)
        _proof(a,'enrollment_recipient',proof,{'grant':h,'target':g['target'],'binding':g['binding']})
        csrf=secrets.token_urlsafe(32)
        def apply():
            conn.execute("UPDATE mfa_enrollment_grants SET state='REDEEMED',csrf_hash=? WHERE token_hash=?",(security.hash_token(csrf),h))
            return {'csrf':csrf,'scope':'EnrollmentOnly','medicalSession':False}
        return apply,g['target']
    return _run(conn,'mfa_enrollment_redeemed',prepare)


def _limited(conn,raw,csrf):
    g=live(conn.execute('SELECT * FROM mfa_enrollment_grants WHERE token_hash=?',(security.hash_token(raw),)).fetchone())
    if (g['state']!='REDEEMED' or not csrf or not secrets.compare_digest(g['csrf_hash'] or '',security.hash_token(csrf))
            or g['binding']!=binding(conn,g['target'])):deny('MFA_GRANT_NOT_CURRENT',401)
    return g


def options(conn,raw,csrf,*,credential=None):
    def prepare(a):
        g=_limited(conn,raw,csrf);uid=g['target'];p=mfa.load_policy(conn)
        if uid not in p['cohort']:deny('MFA_POLICY_NOT_ENROLLED')
        handle=conn.execute('SELECT user_handle FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()[0]
        pending=conn.execute("SELECT COUNT(*) FROM mfa_lifecycle_challenges WHERE target=? AND state IN ('PENDING','CLAIMED') AND expires>?",(uid,security.utc_now().isoformat())).fetchone()[0]
        total=conn.execute('SELECT COUNT(*) FROM mfa_lifecycle_challenges').fetchone()[0]
        per_user=conn.execute('SELECT COUNT(*) FROM mfa_lifecycle_challenges WHERE target=?',(uid,)).fetchone()[0]
        limits=[a.limits.get(k) for k in ('active_challenges','subject_challenges','total_challenges')]
        if any(type(v) is not int or v<=0 for v in limits):deny('MFA_AUTHORITY_POLICY_INVALID')
        if pending>=limits[0] or per_user>=limits[1] or total>=limits[2]:deny('MFA_LIFECYCLE_CAPACITY',429)
        stored=conn.execute('SELECT * FROM mfa_credentials WHERE credential_id=? AND user_id=?',(credential,uid)).fetchone() if credential else None
        if credential and (stored is None or stored['state'] not in {'PENDING','ACTIVE'}):deny('MFA_PENDING_FACTOR_REQUIRED')
        if credential and not conn.execute('SELECT 1 FROM mfa_factor_evidence WHERE credential_id=? AND target=?',(credential,uid)).fetchone():deny('MFA_FACTOR_AUTHORITY_MISSING')
        creds=[AttestedCredentialData(r[0]) for r in conn.execute('SELECT credential_data FROM mfa_credentials WHERE user_id=?',(uid,))] if credential is None else [AttestedCredentialData(stored['credential_data'])]
        opts,state=mfa_webauthn.options(p['rp_id'],p['origin'],handle,mfa.new_challenge(),creds,register=credential is None)
        fid=secrets.token_hex(16);purpose='register' if credential is None else 'possession'
        def apply():
            conn.execute("INSERT INTO mfa_lifecycle_challenges VALUES(?,?,?,?,?,?,?,?,'PENDING')",
                (fid,g['token_hash'],uid,g['binding'],purpose,credential,state,expires(a,'challenge')))
            return {'flow':fid,'options':opts,'scope':'EnrollmentOnly'}
        return apply,uid
    return _run(conn,'mfa_lifecycle_challenge_created',prepare)


def _claim(conn,raw,csrf,fid,origin):
    def prepare(a):
        g=_limited(conn,raw,csrf)
        f=live(conn.execute('SELECT * FROM mfa_lifecycle_challenges WHERE id=?',(fid,)).fetchone())
        if f['grant_hash']!=g['token_hash'] or f['target']!=g['target'] or f['binding']!=g['binding']:deny('MFA_FLOW_BINDING',403)
        if f['state']!='PENDING':deny('MFA_FLOW_CONSUMED',409)
        if origin!=mfa.load_policy(conn)['origin']:deny('MFA_ORIGIN_MISMATCH')
        def apply():
            conn.execute("UPDATE mfa_lifecycle_challenges SET state='CLAIMED' WHERE id=?",(fid,))
            return {**dict(f),'flow':fid}
        return apply,g['target']
    return _run(conn,'mfa_lifecycle_challenge_claimed',prepare)


def finish(conn,raw,csrf,fid,origin,response,proof=None):
    f=_claim(conn,raw,csrf,fid,origin)  # durable BEFORE untrusted crypto/parsing
    try:
        p=mfa.load_policy(conn);uid=f['target']
        if f['purpose']=='register':
            verified=mfa_webauthn.registration(p['rp_id'],p['origin'],f['library_state'],response)
        else:
            stored=conn.execute('SELECT * FROM mfa_credentials WHERE credential_id=? AND user_id=?',(f['credential_id'],uid)).fetchone()
            if stored is None or stored['state'] not in {'PENDING','ACTIVE'}:deny('MFA_PENDING_FACTOR_REQUIRED')
            handle=conn.execute('SELECT user_handle FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()[0]
            verified=mfa_webauthn.assertion(p['rp_id'],p['origin'],f['library_state'],response,stored,handle)
    except (ValueError,KeyError,TypeError):
        def failed(a):
            def apply():
                conn.execute("UPDATE mfa_lifecycle_challenges SET state='FAILED' WHERE id=?",(fid,))
                return {'state':'FAILED'}
            return apply,f['target']
        _run(conn,'mfa_factor_verification_failed',failed)
        deny('MFA_VERIFICATION_FAILED',401)
    def prepare(a):
        g=_limited(conn,raw,csrf)
        fresh=live(conn.execute('SELECT * FROM mfa_lifecycle_challenges WHERE id=?',(fid,)).fetchone())
        if fresh['state']!='CLAIMED' or fresh['binding']!=g['binding'] or fresh['grant_hash']!=g['token_hash']:deny('MFA_FLOW_CHANGED',409)
        if f['purpose']=='register':
            cid=verified.credential_data.credential_id
            if conn.execute('SELECT 1 FROM mfa_credentials WHERE credential_id=?',(cid,)).fetchone():deny('MFA_CREDENTIAL_ALREADY_KNOWN',409)
            def apply():
                conn.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'PENDING')",(cid,uid,bytes(verified.credential_data),verified.counter,int(verified.is_backup_eligible()),int(verified.is_backed_up())))
                n=_notice(conn,a,uid,'first_factor' if not conn.execute("SELECT 1 FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(uid,)).fetchone() else 'add_factor')
                conn.execute('INSERT INTO mfa_factor_evidence VALUES(?,?,?,0,NULL,?)',(cid,uid,g['binding'],n))
                conn.execute("UPDATE mfa_lifecycle_challenges SET state='SUCCEEDED' WHERE id=?",(fid,))
                return {'credential':mfa_webauthn.decode(response['id']).hex(),'notice':n,'state':'PENDING'}
        else:
            cid=f['credential_id']
            evidence=_proof(a,'factor_independence',proof,{'target':uid,'credential':cid.hex(),'binding':g['binding']})
            factor_now=conn.execute('SELECT * FROM mfa_credentials WHERE credential_id=? AND user_id=?',(cid,uid)).fetchone()
            if factor_now is None or factor_now['state'] not in {'PENDING','ACTIVE'} or factor_now['counter']!=stored['counter']:
                deny('MFA_FACTOR_CHANGED',409)
            domain=evidence.get('domain')
            if not isinstance(domain,str) or not domain or len(domain)>128:deny('MFA_INDEPENDENCE_UNVERIFIED')
            if conn.execute("SELECT 1 FROM mfa_factor_evidence e JOIN mfa_credentials c ON c.credential_id=e.credential_id WHERE e.target=? AND e.domain=? AND e.credential_id!=? AND c.state!='REVOKED'",(uid,domain,cid)).fetchone():deny('MFA_FACTORS_NOT_INDEPENDENT')
            def apply():
                conn.execute('UPDATE mfa_credentials SET counter=?,bs=? WHERE credential_id=?',(verified.counter,int(verified.is_backed_up()),cid))
                old=conn.execute('SELECT * FROM mfa_factor_evidence WHERE credential_id=?',(cid,)).fetchone()
                if old is None:deny('MFA_FACTOR_AUTHORITY_MISSING')
                notice=old['notice_id'] if old['binding']==g['binding'] else _notice(conn,a,uid,'add_factor')
                conn.execute('UPDATE mfa_factor_evidence SET possessed=1,domain=?,binding=?,notice_id=? WHERE credential_id=?',(domain,g['binding'],notice,cid))
                conn.execute("UPDATE mfa_lifecycle_challenges SET state='SUCCEEDED' WHERE id=?",(fid,))
                return {'state':'PENDING','credential':cid.hex(),'possession':True,'medicalSession':False}
        return apply,uid
    return _run(conn,'mfa_factor_evidence_recorded',prepare)


def notice_transition(conn,nid,action,proof):
    sequence={'transmit':('CREATED','TRANSMISSION_INITIATED'),
              'deliver':('TRANSMISSION_INITIATED','DELIVERY_CONFIRMED'),
              'acknowledge':('DELIVERY_CONFIRMED','EMPLOYEE_ACKNOWLEDGED')}
    def prepare(a):
        n=live(conn.execute('SELECT * FROM mfa_notices WHERE id=?',(nid,)).fetchone())
        if action not in sequence:deny('MFA_NOTICE_ACTION')
        before,after=sequence[action]
        if n['state']!=before or n['binding']!=binding(conn,n['target']):deny('MFA_NOTICE_NOT_CURRENT')
        expected={'notice':nid,'target':n['target'],'action':n['action'],'binding':n['binding']}
        if action!='transmit':expected['dispatch']=n['dispatch_id']
        if action=='acknowledge':
            receipt_row=conn.execute('SELECT id FROM mfa_notice_receipts WHERE notice_id=?',(nid,)).fetchone()
            if receipt_row is None:deny('MFA_NOTICE_RECEIPT_INVALID')
            expected['receipt']=receipt_row[0]
        claims=_proof(a,'notice_'+action,proof,expected)
        receipt=claims.get('receipt')
        if action=='deliver' and (not isinstance(receipt,str) or not receipt or conn.execute('SELECT 1 FROM mfa_notice_receipts WHERE id=?',(receipt,)).fetchone()):deny('MFA_NOTICE_RECEIPT_INVALID')
        def apply():
            if action=='deliver':conn.execute('INSERT INTO mfa_notice_receipts VALUES(?,?,?)',(receipt,nid,fingerprint(proof)))
            dispatch=secrets.token_hex(16) if action=='transmit' else n['dispatch_id']
            conn.execute('UPDATE mfa_notices SET state=?,dispatch_id=? WHERE id=?',(after,dispatch,nid))
            return {'state':after,'notice':nid}
        return apply,n['target']
    return _run(conn,'mfa_notice_'+action,prepare)


def _confirmed(conn,nid,uid):
    n=live(conn.execute('SELECT * FROM mfa_notices WHERE id=?',(nid,)).fetchone())
    if n['target']!=uid or n['binding']!=binding(conn,uid) or n['state']!='EMPLOYEE_ACKNOWLEDGED' or not conn.execute('SELECT 1 FROM mfa_notice_receipts WHERE notice_id=?',(nid,)).fetchone():deny('MFA_NOTICE_CONFIRMATION_REQUIRED')


def activate(conn,raw,csrf,proof):
    def prepare(a):
        g=_limited(conn,raw,csrf);uid=g['target']
        _proof(a,'activation',proof,{'target':uid,'binding':g['binding'],'grant':g['token_hash']})
        rows=conn.execute("SELECT e.*,c.state FROM mfa_factor_evidence e JOIN mfa_credentials c ON c.credential_id=e.credential_id WHERE e.target=? AND c.state IN ('PENDING','ACTIVE')",(uid,)).fetchall()
        valid=[]
        for f in rows:
            if f['binding']==g['binding'] and f['possessed']==1 and f['domain']:
                _confirmed(conn,f['notice_id'],uid);valid.append(f)
        if len({f['domain'] for f in valid})<2:deny('MFA_TWO_INDEPENDENT_FACTORS_REQUIRED')
        def apply():
            # New usable factor authority invalidates earlier sessions/cases too.
            users.terminate_sessions(conn,uid,commit=False)
            for f in valid:conn.execute("UPDATE mfa_credentials SET state='ACTIVE' WHERE credential_id=?",(f['credential_id'],))
            conn.execute("UPDATE mfa_user_state SET state='ACTIVE' WHERE user_id=?",(uid,))
            conn.execute("UPDATE mfa_enrollment_grants SET state='REVOKED' WHERE target=?",(uid,))
            conn.execute("UPDATE mfa_lifecycle_challenges SET state='FAILED' WHERE target=? AND state IN ('PENDING','CLAIMED')",(uid,))
            return {'state':'ACTIVE','medicalSession':False,'reauthenticationRequired':True}
        return apply,uid
    return _run(conn,'mfa_factors_activated',prepare)


def _full(conn,token,expected_user=None):
    row=conn.execute('SELECT * FROM sessions WHERE token_hash=?',(security.hash_token(token),)).fetchone()
    if row is None or (expected_user and row['user_id']!=expected_user) or security.remaining_seconds(row)<=0 or not mfa.required(conn,row['user_id']) or not mfa.session_allowed(conn,row):deny('MFA_FULL_UV_SESSION_REQUIRED',401)
    return row


def _invalidate(conn,uid,state):
    users.terminate_sessions(conn,uid,commit=False)  # epoch++, all old sessions/flows
    conn.execute("UPDATE mfa_credentials SET state='REVOKED' WHERE user_id=?",(uid,))
    conn.execute('UPDATE mfa_user_state SET state=? WHERE user_id=?',(state,uid))
    conn.execute("UPDATE mfa_enrollment_grants SET state='REVOKED' WHERE target=?",(uid,))
    conn.execute("UPDATE mfa_lifecycle_challenges SET state='FAILED' WHERE target=? AND state IN ('PENDING','CLAIMED')",(uid,))


def request_recovery(conn,target,proof):
    def prepare(a):
        b=binding(conn,target);_proof(a,'recovery_identity',proof,{'target':target,'binding':b})
        case=secrets.token_hex(16)
        def apply():
            n=_notice(conn,a,target,'recovery')
            conn.execute("INSERT INTO mfa_recovery_cases VALUES(?,?,?,?,?,'PENDING')",(case,target,b,expires(a,'case'),n))
            return {'case':case,'notice':n,'medicalSession':False}
        return apply,target
    return _run(conn,'mfa_recovery_requested',prepare)


def _operator(conn,uid):
    rows=conn.execute('SELECT * FROM mfa_recovery_operators').fetchall()
    if (len(rows)!=3 or len({r['domain'] for r in rows})!=3
            or any(not isinstance(r['domain'],str) or not r['domain'] or len(r['domain'])>128
                   or type(r['revision']) is not int or r['revision']<0 for r in rows)):
        deny('MFA_RECOVERY_QUORUM_UNAVAILABLE')
    row=next((r for r in rows if r['user_id']==uid and r['active']==1),None)
    if row is None:deny('MFA_OPERATOR_NOT_AUTHORIZED')
    binding(conn,uid)
    return row


def approve(conn,case_id,token,proof):
    def prepare(a):
        case=live(conn.execute('SELECT * FROM mfa_recovery_cases WHERE id=?',(case_id,)).fetchone())
        target=case['target'];b=binding(conn,target)
        if case['state']!='PENDING' or case['binding']!=b:deny('MFA_RECOVERY_CASE_NOT_CURRENT')
        session=_full(conn,token);actor=session['user_id']
        if actor==target:deny('MFA_RECOVERY_SELF_APPROVAL')
        operator=_operator(conn,actor);ab=binding(conn,actor)
        _confirmed(conn,case['notice_id'],target)
        _proof(a,'recovery_approval',proof,{'case':case_id,'target':target,'binding':b,'actor':actor,'actor_binding':ab,'session':session['token_hash']})
        approvals=conn.execute('SELECT * FROM mfa_approvals WHERE case_id=?',(case_id,)).fetchall()
        if any(v['actor']==actor for v in approvals):deny('MFA_RECOVERY_DUPLICATE_APPROVAL',409)
        for v in approvals:
            op=_operator(conn,v['actor'])
            previous=conn.execute('SELECT * FROM sessions WHERE token_hash=?',(v['session_hash'],)).fetchone()
            if (v['actor']==target or v['actor_binding']!=binding(conn,v['actor']) or op['revision']!=v['operator_revision']
                    or previous is None or previous['user_id']!=v['actor'] or security.remaining_seconds(previous)<=0 or not mfa.session_allowed(conn,previous)):
                deny('MFA_RECOVERY_APPROVER_CHANGED')
        def apply():
            conn.execute('INSERT INTO mfa_approvals VALUES(?,?,?,?,?)',(case_id,actor,ab,session['token_hash'],operator['revision']))
            if not approvals:return {'state':'PENDING','case':case_id,'approvals':1,'medicalSession':False}
            a.checkpoint('before_recovery_grant')
            _invalidate(conn,target,'ENROLLMENT_REQUIRED')
            result=_grant(conn,a,target,case_id)
            conn.execute("UPDATE mfa_recovery_cases SET state='ENROLLMENT_ONLY' WHERE id=?",(case_id,))
            return result
        return apply,target,actor
    return _run(conn,'mfa_recovery_approval',prepare)


def dispute(conn,nid,proof):
    def prepare(a):
        n=conn.execute('SELECT * FROM mfa_notices WHERE id=?',(nid,)).fetchone()
        if n is None or n['state']=='DISPUTED':deny('MFA_NOTICE_UNAVAILABLE')
        _proof(a,'notice_dispute',proof,{'notice':nid,'target':n['target'],'binding':binding(conn,n['target'])})
        def apply():
            conn.execute("UPDATE mfa_notices SET state='DISPUTED' WHERE id=?",(nid,))
            conn.execute("UPDATE mfa_recovery_cases SET state='DISPUTED' WHERE notice_id=?",(nid,))
            _invalidate(conn,n['target'],'SECURITY_REVIEW_HOLD')
            return {'state':'DISPUTED','notice':nid,'medicalSession':False}
        return apply,n['target']
    return _run(conn,'mfa_notice_disputed',prepare)


def revoke(conn,target,credential,token,proof):
    def prepare(a):
        session=_full(conn,token,target)
        _proof(a,'factor_revocation',proof,{'target':target,'credential':credential.hex(),'binding':binding(conn,target),'session':session['token_hash']})
        f=conn.execute('SELECT state FROM mfa_credentials WHERE credential_id=? AND user_id=?',(credential,target)).fetchone()
        if not f or f['state']=='REVOKED':deny('MFA_FACTOR_UNAVAILABLE')
        def apply():
            # Immediate revocation is not delayed for notification.
            conn.execute("UPDATE mfa_credentials SET state='REVOKED' WHERE credential_id=?",(credential,))
            users.terminate_sessions(conn,target,commit=False)
            conn.execute("UPDATE mfa_enrollment_grants SET state='REVOKED' WHERE target=?",(target,))
            n=_notice(conn,a,target,'revoke_factor')
            return {'state':'REVOKED','notice':n,'reauthenticationRequired':True}
        return apply,target,target
    return _run(conn,'mfa_factor_revoked',prepare)


def authorize_additional(conn,target,token,proof):
    def prepare(a):
        session=_full(conn,token,target)
        _proof(a,'additional_enrollment',proof,{'target':target,'binding':binding(conn,target),'session':session['token_hash']})
        return lambda:_grant(conn,a,target),target,target
    return _run(conn,'mfa_additional_enrollment_authorized',prepare)


def review_pending(conn,raw,csrf,credential,proof):
    """Explicit authority review of a pre-v32 PENDING key; never resurrect revoked.
    Existing registration alone is not possession or delivery evidence.
    """
    def prepare(a):
        g=_limited(conn,raw,csrf);uid=g['target']
        f=conn.execute('SELECT * FROM mfa_credentials WHERE credential_id=? AND user_id=?',(credential,uid)).fetchone()
        if f is None or f['state']!='PENDING':deny('MFA_PENDING_FACTOR_REQUIRED')
        if conn.execute('SELECT 1 FROM mfa_factor_evidence WHERE credential_id=?',(credential,)).fetchone():deny('MFA_FACTOR_ALREADY_REVIEWED',409)
        _proof(a,'pending_factor_review',proof,{'target':uid,'credential':credential.hex(),'binding':g['binding'],'grant':g['token_hash']})
        def apply():
            n=_notice(conn,a,uid,'add_factor')
            conn.execute('INSERT INTO mfa_factor_evidence VALUES(?,?,?,0,NULL,?)',(credential,uid,g['binding'],n))
            return {'notice':n,'state':'PENDING','medicalSession':False}
        return apply,uid
    return _run(conn,'mfa_pending_factor_reviewed',prepare)
