"""Synthetic test-only witness/channel. Never imported by application startup.
Local SQLite outside the restored DB simulates an independent administrative
boundary; same-machine storage is NOT production anti-rollback infrastructure.
HMAC authenticates test receipts, not a real human/channel/device attestation.
"""
import hashlib
import fcntl
import os
import hmac
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def projection(conn):
    def value(v):return {'blob_sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v
    queries={
        'mfa_policy':'SELECT * FROM mfa_policy',
        'mfa_user_state':'SELECT * FROM mfa_user_state',
        'mfa_credentials':'SELECT * FROM mfa_credentials',
        'mfa_flows':'SELECT * FROM mfa_flows',
        'mfa_session_assurance':'SELECT * FROM mfa_session_assurance',
        'mfa_recovery_operators':'SELECT * FROM mfa_recovery_operators',
        'mfa_notices':'SELECT * FROM mfa_notices',
        'mfa_recovery_cases':'SELECT * FROM mfa_recovery_cases',
        'mfa_approvals':'SELECT * FROM mfa_approvals',
        'mfa_enrollment_grants':'SELECT * FROM mfa_enrollment_grants',
        'mfa_factor_evidence':'SELECT * FROM mfa_factor_evidence',
        'mfa_lifecycle_challenges':'SELECT * FROM mfa_lifecycle_challenges',
        'mfa_notice_receipts':'SELECT * FROM mfa_notice_receipts',
    }
    names=sorted(queries)
    from backend.app import operations_state
    out={'wp_f_generation':operations_state.require_permit()['generation']}
    for name in names:
        if not name.replace('_','').isalnum():raise ValueError('table_name')
        rows=[[value(v) for v in r] for r in conn.execute(queries[name])]
        out[name]=sorted(rows,key=lambda r:canonical(r))
    out['users']=[tuple(r) for r in conn.execute('SELECT id,password_hash,role_id,status,must_change_password FROM users ORDER BY id')]
    out['sessions']=[[value(v) for v in r] for r in conn.execute('SELECT token_hash,user_id,csrf_token,created_at,expires_at FROM sessions ORDER BY token_hash')]
    out['user_departments']=sorted([list(r) for r in conn.execute('SELECT * FROM user_departments')],key=lambda r:canonical(r))
    out['department_heads']=sorted([list(r) for r in conn.execute('SELECT * FROM department_heads')],key=lambda r:canonical(r))
    out['mfa_audit']=[tuple(r) for r in conn.execute("SELECT id,event_type,actor_user_id,target_user_id,result,detail FROM audit_events WHERE event_type LIKE 'mfa_%' ORDER BY id")]
    return hashlib.sha256(canonical(out)).hexdigest()


class SyntheticAuthority:
    limits={'grant':300,'notice':600,'case':600,'challenge':120,
            'active_challenges':2,'subject_challenges':128,'total_challenges':4096}

    def __init__(self,path,conn,clock):
        self.path=Path(path);self.clock=clock;self.key=secrets.token_bytes(32)
        self.local=threading.local();self.hook=lambda point:None
        fd=self.path.open('xb');fd.close()  # new synthetic fixture only
        with self.open() as c:
            c.execute('CREATE TABLE anchor(id INTEGER PRIMARY KEY,status TEXT,sequence INTEGER,digest TEXT)')
            c.execute('CREATE TABLE used(nonce TEXT PRIMARY KEY)')
            c.execute("INSERT INTO anchor VALUES(1,'CURRENT',0,?)",(projection(conn),))

    @contextmanager
    def open(self):
        c=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=10)
        c.execute('PRAGMA synchronous=FULL')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()

    @contextmanager
    def exclusive(self):
        fd=os.open(self.path.with_suffix('.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

    @staticmethod
    def refuse():
        from fastapi import HTTPException
        raise HTTPException(403,'MFA_SYNTHETIC_AUTHORITY_HOLD')

    def require_current(self,conn):
        try:
            with self.open() as c:row=c.execute('SELECT status,sequence,digest FROM anchor WHERE id=1').fetchone()
            if row is None or row[0]!='CURRENT' or row[2]!=projection(conn):self.refuse()
        except (sqlite3.Error,OSError,ValueError,TypeError):self.refuse()

    def start_operation(self,conn):
        self.require_current(conn);self.local.evidence=[]

    def make_proof(self,kind,binding,**claims):
        body={'kind':kind,'binding':binding,'nonce':secrets.token_hex(16),
              'expires':(self.clock()+timedelta(seconds=120)).isoformat(),
              'issuer':'synthetic-independent-channel',**claims}
        return {'body':body,'mac':hmac.new(self.key,canonical(body),hashlib.sha256).hexdigest()}

    def verify(self,kind,proof,expected):
        try:
            body=proof['body']
            if (not hmac.compare_digest(proof['mac'],hmac.new(self.key,canonical(body),hashlib.sha256).hexdigest())
                    or body['issuer']!='synthetic-independent-channel' or body['kind']!=kind
                    or body['binding']!=expected or body['expires']<=self.clock().isoformat()
                    or not isinstance(body['nonce'],str)):
                self.refuse()
            with self.open() as c:
                if c.execute('SELECT 1 FROM used WHERE nonce=?',(body['nonce'],)).fetchone():self.refuse()
            if kind=='recovery_approval' and body.get('assurance')!='webauthn_uv_stepup':self.refuse()
            if kind in {'enrollment_recipient','notice_acknowledge','notice_dispute'} and body.get('assurance')!='independent_identity':self.refuse()
            self.local.evidence.append(body['nonce'])
            return body
        except (KeyError,TypeError,ValueError):self.refuse()

    def reserve(self,conn,event):
        with self.open() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status,sequence,digest FROM anchor WHERE id=1').fetchone()
            if row[0]!='CURRENT' or row[2]!=projection(conn):self.refuse()
            for nonce in self.local.evidence:c.execute('INSERT INTO used VALUES(?)',(nonce,))
            c.execute("UPDATE anchor SET status='PENDING',sequence=sequence+1 WHERE id=1")
            self.checkpoint('before_witness_reserve_commit')
        return row[1]+1

    def commit(self,conn,reservation):
        with self.open() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status,sequence FROM anchor WHERE id=1').fetchone()
            if row!=('PENDING',reservation):self.refuse()
            from prototype.continuity_lab.model import publish
            publish(self.path.with_suffix('.receipt.json'),{'sequence':reservation,'digest':projection(conn)})
            c.execute("UPDATE anchor SET status='CURRENT',digest=? WHERE id=1",(projection(conn),))
            self.checkpoint('before_witness_finalize_commit')
        self.local.evidence=[]
        self.checkpoint('after_witness_finalize_commit')

    @classmethod
    def reopen(cls,path,key,clock):
        # Existing synthetic witness, never initialization or re-trust.
        obj=object.__new__(cls);obj.path=Path(path);obj.key=key;obj.clock=clock
        obj.local=threading.local();obj.hook=lambda point:None
        return obj

    def checkpoint(self,point):
        self.hook(point)
