"""WebAuthn MFA core. No production enrollment authority or continuity bypass."""
import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta
from enum import Enum
from fastapi import HTTPException
from fido2.webauthn import AttestedCredentialData
from . import audit, security, policy, session_context, operations_state, mfa_continuity, mfa_webauthn
from .db import transaction

PREAUTH_COOKIE = 'clinic_pre_auth'
PREAUTH_CSRF_COOKIE = 'clinic_pre_auth_csrf'
FLOW_SECONDS = 120
# Admission budgets, not retention: no history is deleted or silently recycled.
MAX_ACTIVE_FLOWS_PER_USER = 2
MAX_FLOWS_PER_USER = 128
MAX_FLOWS_TOTAL = 4096


class NoticeState(str, Enum):
    CREATED='CREATED'
    TRANSMISSION_INITIATED='TRANSMISSION_INITIATED'
    DELIVERY_CONFIRMED='DELIVERY_CONFIRMED'
    EMPLOYEE_ACKNOWLEDGED='EMPLOYEE_ACKNOWLEDGED'
    DISPUTED='DISPUTED'


def deny(code='MFA_REQUIRED',status=403):
    raise HTTPException(status,code)


def load_policy(conn):
    try:
        rows=conn.execute('SELECT * FROM mfa_policy').fetchall()
        if len(rows)!=1:raise ValueError()
        p=dict(rows[0]);cohort=json.loads(p['user_ids'])
        if p['id']!=1 or p['version']!=1 or not isinstance(cohort,list) or any(not isinstance(u,str) or not u for u in cohort) or len(cohort)!=len(set(cohort)):raise ValueError()
        if p['mode']=='stage1_compatibility':
            if cohort or p['rp_id'] is not None or p['origin'] is not None:raise ValueError()
        elif p['mode']=='required_cohort':
            if not cohort:raise ValueError()
            mfa_webauthn.server(p['rp_id'],p['origin'])
        else:raise ValueError()
        p['cohort']=cohort
        p['digest']=hashlib.sha256(json.dumps(dict(rows[0]),sort_keys=True).encode()).hexdigest()
        return p
    except (sqlite3.Error,ValueError,TypeError,KeyError):
        deny('MFA_POLICY_UNAVAILABLE')


def required(conn,user_id):
    return user_id in load_policy(conn)['cohort']


def current(conn,user_id):
    p=load_policy(conn)
    if user_id not in p['cohort']:deny()
    user=policy.load_current_user(conn,user_id)
    row=conn.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    state=conn.execute('SELECT * FROM mfa_user_state WHERE user_id=?',(user_id,)).fetchone()
    if not user or user.status!='active':deny('MFA_ACCOUNT_CHANGED',401)
    if state is None or state['state']!='ACTIVE':deny('MFA_ENROLLMENT_OR_REVIEW_REQUIRED')
    generation=operations_state.require_permit()['generation']
    mfa_continuity.require_trust(conn,user_id,generation)
    return p,row,state,session_context.access_context(user),generation


def initialize_user(conn,user_id):
    """No enrollment/activation: only a random opaque server identity."""
    conn.execute("INSERT INTO mfa_user_state VALUES(?,?,0,'ENROLLMENT_REQUIRED')",
                 (user_id,secrets.token_bytes(32)))


def new_challenge():
    return secrets.token_bytes(32)


def retire_browser_context(conn,user_id,browser_token=None,previous_flow=None):
    """Caller-owned transaction; affect only tokens presented by this browser."""
    if not conn.in_transaction:raise RuntimeError('browser transition requires transaction')
    if previous_flow:
        conn.execute("UPDATE mfa_flows SET state='FAILED' WHERE token_hash=? AND state IN ('PENDING','CLAIMED')",
                     (security.hash_token(previous_flow),))
    if browser_token:
        token_hash=security.hash_token(browser_token)
        old=conn.execute('SELECT user_id FROM sessions WHERE token_hash=?',(token_hash,)).fetchone()
        if old and old['user_id']!=user_id:
            conn.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash,))
            conn.execute("UPDATE mfa_flows SET state='FAILED' WHERE parent_session=? AND state IN ('PENDING','CLAIMED')",(token_hash,))
            # v2 semantics apply to NEW events only. A password-verified B is the
            # initiator, not the owner A of the retired browser session. This does
            # not assert WebAuthn/full-session assurance for B.
            detail=json.dumps({'version':2,'initiator_user_id':user_id,
                'previous_identity_user_id':old['user_id'],'new_identity_user_id':user_id,
                'authentication_level':'password_verified','reason':'identity_switch',
                'outcome':'session_revoked'},sort_keys=True,separators=(',',':'))
            audit.record_required_or_fail(conn,'mfa_browser_session_revoked',
                actor_user_id=user_id,target_user_id=old['user_id'],detail=detail)


def allocation_budget(conn,user_id):
    now=security.utc_now().isoformat()
    active=conn.execute("SELECT COUNT(*) FROM mfa_flows WHERE user_id=? AND state IN ('PENDING','CLAIMED') AND expires_at>?",(user_id,now)).fetchone()[0]
    count=conn.execute('SELECT COUNT(*) FROM mfa_flows WHERE user_id=?',(user_id,)).fetchone()[0]
    total=conn.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]
    if count>=MAX_FLOWS_PER_USER or total>=MAX_FLOWS_TOTAL:
        raise HTTPException(429,'MFA_FLOW_STORAGE_CAPACITY_REACHED')
    if active>=MAX_ACTIVE_FLOWS_PER_USER:
        raise HTTPException(429,'MFA_FLOW_ACTIVE_LIMIT',headers={'Retry-After':str(FLOW_SECONDS)})


def begin(conn,user_id,password_revision,*,purpose='authenticate',parent=None,browser_token=None,previous_flow=None):
    with transaction(conn):
        p,user,state,access,generation=current(conn,user_id)
        if user['password_hash']!=password_revision:deny('MFA_ACCOUNT_CHANGED',401)
        if purpose=='register':
            # First enrollment/bootstrap/independent notices are not implemented.
            # No password-only or admin path may turn this into factor activation.
            parent_row=conn.execute('SELECT * FROM sessions WHERE token_hash=?',(parent,)).fetchone()
            if parent_row is None or parent_row['user_id']!=user_id or not session_allowed(conn,parent_row) or security.remaining_seconds(parent_row)<=0:
                deny('MFA_ENROLLMENT_AUTHORITY_UNAVAILABLE')
        retire_browser_context(conn,user_id,browser_token,previous_flow)
        allocation_budget(conn,user_id)
        factors=conn.execute("SELECT * FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(user_id,)).fetchall()
        if not factors:deny('MFA_ENROLLMENT_OR_REVIEW_REQUIRED')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);challenge=new_challenge()
        opts,library_state=mfa_webauthn.options(p['rp_id'],p['origin'],state['user_handle'],challenge,
            [AttestedCredentialData(f['credential_data']) for f in factors],register=purpose=='register')
        expires=(security.utc_now()+timedelta(seconds=FLOW_SECONDS)).isoformat()
        conn.execute("INSERT INTO mfa_flows VALUES(?,?,?,?,?,'PENDING',?,?,?,?,?,?,NULL,?,?)",
            (security.hash_token(token),user_id,security.hash_token(csrf),challenge,purpose,state['auth_epoch'],password_revision,access,generation,p['digest'],expires,parent,library_state))
        audit.record_required_or_fail(conn,'mfa_pre_auth_created',actor_user_id=user_id)
    return token,csrf,opts


def check_flow(conn,f,*,parent=None):
    p,user,state,access,generation=current(conn,f['user_id'])
    if (security.utc_now().isoformat()>=f['expires_at'] or f['epoch']!=state['auth_epoch']
        or f['password_revision']!=user['password_hash'] or f['access_context']!=access
        or f['generation']!=generation or f['policy_digest']!=p['digest']):deny('MFA_FLOW_EXPIRED_OR_CHANGED',401)
    if f['purpose']=='register':
        if not parent or parent!=f['parent_session']:
            deny('MFA_PARENT_SESSION_MISMATCH',403)
        parent_row=conn.execute('SELECT * FROM sessions WHERE token_hash=?',(parent,)).fetchone()
        if parent_row is None or parent_row['user_id']!=f['user_id'] or not session_allowed(conn,parent_row) or security.remaining_seconds(parent_row)<=0:
            deny('MFA_PARENT_SESSION_CHANGED',401)
    return p,user,state


def claim(conn,token,csrf,origin,*,purpose='authenticate',parent=None):
    with transaction(conn):
        f=conn.execute('SELECT * FROM mfa_flows WHERE token_hash=?',(security.hash_token(token or ''),)).fetchone()
        if f is None:deny('MFA_FLOW_UNAVAILABLE',401)
        if f['purpose']!=purpose:deny('MFA_FLOW_UNAVAILABLE',401)
        if f['state']!='PENDING':deny('MFA_FLOW_UNAVAILABLE',409)
        if not csrf or not secrets.compare_digest(f['csrf_hash'],security.hash_token(csrf)):deny('CSRF validation failed')
        p,_,_=check_flow(conn,f,parent=parent)
        if origin!=p['origin']:deny('MFA_ORIGIN_MISMATCH')
        owner=secrets.token_hex(32)
        conn.execute("UPDATE mfa_flows SET state='CLAIMED',owner=? WHERE token_hash=?",(owner,f['token_hash']))
        audit.record_required_or_fail(conn,'mfa_challenge_claimed',actor_user_id=f['user_id'])
    return dict(f),owner


def finish(conn,token,csrf,origin,response):
    f,owner=claim(conn,token,csrf,origin)
    # Claim is durable BEFORE any parsing/signature verification. Any interruption
    # consumes this attempt; a second request must start a new password flow.
    try:
        p,_,state=check_flow(conn,f)
        cid=mfa_webauthn.decode(response['id'])
        factor=conn.execute("SELECT * FROM mfa_credentials WHERE credential_id=? AND user_id=? AND state='ACTIVE'",(cid,f['user_id'])).fetchone()
        if factor is None:raise ValueError('owner')
        result=mfa_webauthn.assertion(p['rp_id'],p['origin'],f['library_state'],response,factor,state['user_handle'])
    except (ValueError,TypeError,KeyError):
        with transaction(conn):
            conn.execute("UPDATE mfa_flows SET state='FAILED' WHERE token_hash=? AND owner=?",(f['token_hash'],owner))
            audit.record_required_or_fail(conn,'mfa_assertion_denied',target_user_id=f['user_id'],result=audit.RESULT_DENIED)
        deny('MFA_VERIFICATION_FAILED',401)
    with transaction(conn):
        p,user,state=check_flow(conn,f)
        fresh=conn.execute('SELECT * FROM mfa_flows WHERE token_hash=?',(f['token_hash'],)).fetchone()
        factor_now=conn.execute('SELECT * FROM mfa_credentials WHERE credential_id=?',(cid,)).fetchone()
        if fresh['state']!='CLAIMED' or fresh['owner']!=owner:deny('MFA_FLOW_UNAVAILABLE',409)
        if factor_now['state']!='ACTIVE' or factor_now['counter']!=factor['counter']:deny('MFA_FACTOR_CHANGED',409)
        conn.execute('UPDATE mfa_credentials SET counter=?,bs=? WHERE credential_id=?',(result.counter,int(result.is_backed_up()),cid))
        raw,session_csrf,expires=security._insert_session(conn,f['user_id'])
        conn.execute("INSERT INTO mfa_session_assurance VALUES(?,?,?,?,?,?,?,?,'webauthn_uv')",
            (security.hash_token(raw),f['user_id'],state['auth_epoch'],user['password_hash'],f['access_context'],f['generation'],p['digest'],cid))
        audit.record_required_or_fail(conn,'mfa_login_success',actor_user_id=f['user_id'])
        conn.execute("UPDATE mfa_flows SET state='SUCCEEDED' WHERE token_hash=?",(f['token_hash'],))
    return raw,session_csrf,expires


def session_allowed(conn,session):
    try:
        if not required(conn,session['user_id']):return True
        p,user,state,access,generation=current(conn,session['user_id'])
        a=conn.execute('SELECT a.*,c.state AS factor_state,c.user_id AS factor_owner FROM mfa_session_assurance a JOIN mfa_credentials c ON c.credential_id=a.credential_id WHERE token_hash=?',(session['token_hash'],)).fetchone()
        return bool(a and a['user_id']==session['user_id'] and a['epoch']==state['auth_epoch']
            and a['password_revision']==user['password_hash'] and a['access_context']==access
            and a['generation']==generation and a['policy_digest']==p['digest']
            and a['factor_state']=='ACTIVE' and a['factor_owner']==session['user_id'] and a['method']=='webauthn_uv')
    except HTTPException:return False


def finish_registration(conn,token,csrf,origin,response,*,parent=None):
    try:
        return _finish_registration(conn,token,csrf,origin,response,parent=parent)
    except HTTPException as exc:
        if exc.detail in {'MFA_PARENT_SESSION_MISMATCH','MFA_PARENT_SESSION_CHANGED'}:
            with transaction(conn):
                f=conn.execute('SELECT user_id FROM mfa_flows WHERE token_hash=?',(security.hash_token(token or ''),)).fetchone()
                actor=conn.execute('SELECT user_id FROM sessions WHERE token_hash=?',(parent or '',)).fetchone()
                audit.record_required_or_fail(conn,'mfa_registration_parent_denied',
                    actor_user_id=actor[0] if actor else None,target_user_id=f[0] if f else None,
                    result=audit.RESULT_DENIED,detail='parent_session_mismatch')
        raise


def _finish_registration(conn,token,csrf,origin,response,*,parent):
    f,owner=claim(conn,token,csrf,origin,purpose='register',parent=parent)
    try:
        p,_,_=check_flow(conn,f,parent=parent)
        data=mfa_webauthn.registration(p['rp_id'],p['origin'],f['library_state'],response)
    except (ValueError,TypeError,KeyError):
        deny('MFA_VERIFICATION_FAILED',401)
    with transaction(conn):
        check_flow(conn,f,parent=parent)
        fresh=conn.execute('SELECT * FROM mfa_flows WHERE token_hash=?',(f['token_hash'],)).fetchone()
        if fresh['state']!='CLAIMED' or fresh['owner']!=owner:deny('MFA_FLOW_UNAVAILABLE',409)
        if conn.execute('SELECT 1 FROM mfa_credentials WHERE credential_id=?',(data.credential_data.credential_id,)).fetchone():
            deny('MFA_CREDENTIAL_ALREADY_KNOWN',409)
        conn.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'PENDING')",(data.credential_data.credential_id,
            f['user_id'],bytes(data.credential_data),data.counter,int(data.is_backup_eligible()),int(data.is_backed_up())))
        audit.record_required_or_fail(conn,'mfa_factor_registered_pending',actor_user_id=f['user_id'])
        conn.execute("UPDATE mfa_flows SET state='SUCCEEDED' WHERE token_hash=?",(f['token_hash'],))
    return {'state':'PENDING','activationBlocked':'INDEPENDENT_NOTIFICATION_AND_POSSESSION_REQUIRED'}
