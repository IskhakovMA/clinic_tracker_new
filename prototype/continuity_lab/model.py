"""Synthetic C+S1 coordinator, not a production witness or MFA provider.

The witness directory is outside the replaceable application directory. Both
are LOCAL SQLite files in this lab: independence/consensus/authenticated network
freshness are assumptions of the proposed external service, NOT proven here.
No application imports, WP-F files, keys, WebAuthn crypto or medical sessions.
"""
from __future__ import annotations

import fcntl
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from contextlib import contextmanager


class Hold(Exception):
    """Safe reason codes only."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def quiet(_point):
    pass

def challenge_entropy():
    return secrets.token_hex(32)


class Store:
    def __init__(self, path):
        self.path = Path(path)

    def open(self):
        # Never create a missing authority as a side effect of access.
        c = sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw', uri=True,
                            isolation_level=None, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        return c

    @contextmanager
    def tx(self):
        c = self.open()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def read(self):
        with self.tx() as c:
            return json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])

    @staticmethod
    def write(c, value):
        c.execute('UPDATE state SET body=? WHERE id=1', (canonical(value).decode(),))


def create_store(path, value, audit=False):
    # New synthetic fixture ONLY, never an adoption/retrust operation.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    c = sqlite3.connect(path)
    try:
        c.execute('PRAGMA synchronous=FULL')
        c.execute('CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL)')
        c.execute('INSERT INTO state VALUES(1,?)', (canonical(value).decode(),))
        if audit:
            c.execute('CREATE TABLE audit(id INTEGER PRIMARY KEY,event TEXT NOT NULL,operation_id TEXT NOT NULL,sequence INTEGER NOT NULL,detail TEXT NOT NULL)')
            c.execute("CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable_audit'); END")
            c.execute("CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'retained_audit'); END")
        c.commit()
    finally:
        c.close()


def publish(path, value, hook=quiet):
    """Visibility is NOT durability: file fsync, replace AND directory fsync."""
    path = Path(path)
    temp = path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(canonical(value))
            f.flush()
            hook('receipt.before_file_fsync')
            os.fsync(f.fileno())
        hook('receipt.after_file_fsync')
        os.replace(temp, path)
        hook('receipt.after_rename')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            hook('receipt.before_directory_fsync')
            os.fsync(directory)
        finally:
            os.close(directory)
        hook('receipt.after_directory_fsync')
    finally:
        temp.unlink(missing_ok=True)


class Witness(Store):
    """CAS + non-expiring reservation. No timeout-based unlock or S1 clearing."""
    def reserve(self, before, kind, operation_id, hook, request_digest=None):
        with self.tx() as c:
            w = json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if (w['status'] != 'CURRENT' or w['sequence'] != before['sequence']
                    or w['installation'] != before['installation']
                    or w['digest'] != digest(before)):
                raise Hold('WITNESS_NOT_CURRENT')
            if c.execute('SELECT 1 FROM operations WHERE id=?',(operation_id,)).fetchone():
                raise Hold('OPERATION_ALREADY_RESERVED')
            c.execute("INSERT INTO operations VALUES(?,?,?,?,'PENDING')",
                      (operation_id,kind,request_digest or '',w['sequence']+1))
            w.update(status='PENDING', operation_id=operation_id, kind=kind,
                     nonce=secrets.token_hex(32), next_sequence=w['sequence']+1)
            self.write(c, w)
            hook('witness.reserve.before_commit')
        hook('witness.after_reserve')
        return w

    def finalize(self, reservation, after, hook):
        with self.tx() as c:
            w = json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if w != reservation:
                raise Hold('RESERVATION_CHANGED')
            if (after['sequence'] != w['next_sequence']
                    or after['installation'] != w['installation']
                    or (w['ever_required'] and after['policy'] != 'required')
                    or after['epoch'] < w['epoch']
                    or not set(w['revoked']).issubset(after['revoked'])
                    or not set(w['used_recovery']).issubset(after['used_recovery'])):
                raise Hold('SECURITY_HIGH_WATER_REGRESSION')
            w.update(status='CURRENT', sequence=after['sequence'], digest=digest(after),
                     generation=after['generation'], ever_required=w['ever_required'] or after['policy']=='required',
                     epoch=after['epoch'], revoked=after['revoked'], used_recovery=after['used_recovery'])
            w.pop('next_sequence')
            self.write(c, w)
            c.execute("UPDATE operations SET status='COMMITTED' WHERE id=?",(w['operation_id'],))
            hook('witness.finalize.before_commit')
        hook('witness.after_finalize')


class Lab:
    KINDS = {'register', 'activate', 'revoke', 'epoch', 'policy', 'counter_bs',
             'recovery_use', 'flow_allocate', 'flow_claim', 'flow_finish', 'flow_cleanup'}

    def __init__(self, root, witness_root, hook=quiet):
        self.root = Path(root)
        self.witness_root = Path(witness_root)
        if self.witness_root == self.root or self.root in self.witness_root.parents:
            raise ValueError('witness must be outside replaceable restore set')
        self.db = Store(self.root/'security.sqlite')
        self.witness = Witness(self.witness_root/'witness.sqlite')
        self.hook = hook

    @classmethod
    def fixture(cls, root, witness_root):
        lab = cls(root, witness_root)
        state = {'installation':secrets.token_hex(16), 'sequence':0,
                 'generation':secrets.token_hex(16), 'policy':'compatibility',
                 'epoch':0, 'factors':{}, 'revoked':[], 'used_recovery':[],
                 'allocation_serial':0, 'flows':{}, 'audit_length':0, 'audit_tip':digest([])}
        create_store(lab.db.path, state, audit=True)
        create_store(lab.witness.path, {'installation':state['installation'],
            'sequence':0, 'digest':digest(state), 'status':'CURRENT',
            'generation':state['generation'], 'epoch':0, 'ever_required':False,
            'revoked':[], 'used_recovery':[]})
        with lab.witness.tx() as c:
            c.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,request_digest TEXT NOT NULL,sequence INTEGER NOT NULL,status TEXT NOT NULL)')
        return lab

    @contextmanager
    def exclusive(self):
        # Model of mandatory maintenance/auth barrier, NOT WP-F integration.
        fd = os.open(self.root/'coordinator.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def current(self):
        # Must precede policy routing INCLUDING compatibility, not just MFA current().
        try:
            with self.db.tx() as c:
                s = json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
                rows = c.execute('SELECT event,operation_id,sequence,detail FROM audit ORDER BY id').fetchall()
                tip = digest([])
                for row in rows:tip = digest([tip,list(row)])
                if len(rows) != s['audit_length'] or tip != s['audit_tip']:
                    raise Hold('SECURITY_AUDIT_NOT_CURRENT')
            w = self.witness.read()
            if (w['status'] != 'CURRENT' or s['installation'] != w['installation']
                    or s['sequence'] != w['sequence'] or digest(s) != w['digest']
                    or (w['ever_required'] and s['policy'] != 'required')):
                raise Hold('SECURITY_REVIEW_HOLD')
            if s['sequence']:
                receipt = json.loads((self.root/'receipt.json').read_bytes())
                if (receipt['sequence'] != w['sequence'] or receipt['nonce'] != w['nonce']
                        or receipt['operation_id'] != w['operation_id']
                        or receipt['after_digest'] != w['digest']):
                    raise Hold('RECEIPT_NOT_CURRENT')
            return s
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise Hold('WITNESS_OR_STATE_UNAVAILABLE') from exc

    def policy_gate(self):
        # Returns ONLY a lab policy label, never a session/medical capability.
        with self.exclusive():
            return self.current()['policy']

    def _transition(self, kind, transform, detail=None, *, operation_id=None, request_digest=None, expected_sequence=None):
        if kind not in self.KINDS | {'verify', 'migrate'}:
            raise Hold('UNRECOGNIZED_TRANSITION')
        with self.exclusive():
            before = self.current()
            if operation_id is not None:
                with self.witness.tx() as c:
                    old = c.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
                if old:
                    if old['kind'] != kind or old['request_digest'] != request_digest or old['status'] != 'COMMITTED':
                        raise Hold('OPERATION_REPLAY_MISMATCH')
                    return {'acknowledged_sequence':old['sequence'],'replayed':True}
            if expected_sequence is not None and expected_sequence != before['sequence']:
                raise Hold('SEQUENCE_CONFLICT')
            after = copy.deepcopy(before)
            transform(after)  # validation has no writes and precedes reservation
            operation_id = operation_id or secrets.token_hex(16)
            r = self.witness.reserve(before, kind, operation_id, self.hook, request_digest)
            with self.db.tx() as c:
                fresh = json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
                if fresh != before:
                    raise Hold('DATABASE_CHANGED')
                after['sequence'] = r['next_sequence']
                event_detail = canonical(detail or {}).decode()
                after['audit_length'] += 1
                after['audit_tip'] = digest([before['audit_tip'],[kind,operation_id,after['sequence'],event_detail]])
                self.db.write(c, after)
                c.execute('INSERT INTO audit(event,operation_id,sequence,detail) VALUES(?,?,?,?)',
                          (kind, operation_id, after['sequence'], event_detail))
                self.hook('database.before_commit')
            self.hook('database.after_commit')
            receipt = {'installation':after['installation'], 'sequence':after['sequence'],
                       'operation_id':operation_id, 'kind':kind, 'nonce':r['nonce'],
                       'before_digest':digest(before), 'after_digest':digest(after)}
            publish(self.root/'receipt.json', receipt, self.hook)
            # The fresh CAS is the authority, never the cached receipt alone.
            self.witness.finalize(r, after, self.hook)
            return after

    def change(self, kind, value, *, operation_id=None, expected_sequence=None):
        def apply(s):
            if kind == 'register':
                if value in s['factors'] or value in s['revoked']:
                    raise Hold('KNOWN_FACTOR')
                s['factors'][value] = {'state':'PENDING', 'counter':0, 'bs':False}
            elif kind == 'activate':
                if s['factors'][value]['state'] != 'PENDING':
                    raise Hold('FACTOR_NOT_PENDING')
                s['factors'][value]['state'] = 'ACTIVE'
            elif kind == 'revoke':
                s['factors'][value]['state'] = 'REVOKED'
                s['revoked'] = sorted(set(s['revoked']) | {value})
                s['epoch'] += 1
            elif kind == 'policy':
                if value != 'required':
                    raise Hold('DOWNGRADE_REQUIRES_REVIEW')
                s['policy'] = value
            elif kind == 'epoch':
                s['epoch'] += 1
            elif kind == 'counter_bs':
                factor, counter, bs = value
                f = s['factors'][factor]
                if f['state'] != 'ACTIVE' or type(counter) is not int or counter <= f['counter'] or type(bs) is not bool:
                    raise Hold('INVALID_FACTOR_MUTATION')
                f.update(counter=counter, bs=bs)
            elif kind == 'recovery_use':
                if value in s['used_recovery']:
                    raise Hold('RECOVERY_ALREADY_USED')
                s['used_recovery'].append(value)
            else:
                raise Hold('UNRECOGNIZED_TRANSITION')
        request_digest = digest({'kind':kind,'value':value,'expected_sequence':expected_sequence})
        event_detail = {'subject_id':'synthetic-staff','assurance':'synthetic_fixture', 'outcome':'committed'}
        if kind in {'register','activate','revoke','counter_bs'}:
            event_detail['factor_id'] = value[0] if kind=='counter_bs' else value
        return self._transition(kind, apply, event_detail, operation_id=operation_id,
                                request_digest=request_digest,expected_sequence=expected_sequence)

    def maintain(self, operation, *, security_transform=None):
        if operation not in {'verify', 'migrate', 'restore'}:
            raise Hold('UNKNOWN_MAINTENANCE')
        if operation == 'restore':
            # Trusted executor marks authority BEFORE replacing even identical DB.
            with self.exclusive():
                before = self.current()
                self.witness.reserve(before, 'restore', secrets.token_hex(16), self.hook)
                with self.witness.tx() as c:
                    w = json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
                    w['status'] = 'SECURITY_REVIEW_HOLD'
                    self.witness.write(c, w)
                self.hook('restore.before_replace')
            raise Hold('RESTORE_REQUIRES_REVIEW')
        def transform(s):
            projection = copy.deepcopy({k:v for k,v in s.items() if k not in {'sequence','generation'}})
            if security_transform is not None:
                security_transform(s)
            if projection != {k:v for k,v in s.items() if k not in {'sequence','generation'}}:
                raise Hold('MAINTENANCE_SECURITY_CHANGED')
            s['generation'] = secrets.token_hex(16)
        return self._transition(operation, transform)

    def allocate(self, now, ttl=120, *, user_id='synthetic-staff'):
        if type(now) is not int or type(ttl) is not int or ttl <= 0:
            raise ValueError('synthetic UTC seconds required')
        result = {}
        event_detail = {}
        def transform(s):
            if sum(f['owner']==user_id and f['state'] in {'PENDING','CLAIMED'} and f['expires']>now for f in s['flows'].values()) >= 2:
                raise Hold('ACTIVE_CAPACITY')
            if len(s['flows']) >= 4096 or sum(f['owner']==user_id for f in s['flows'].values()) >= 128:
                raise Hold('ROW_CAPACITY')
            s['allocation_serial'] += 1
            serial = s['allocation_serial']
            # Serial is never recycled, including after cleanup. Not WebAuthn crypto.
            challenge = hashlib.sha256(canonical([s['installation'], serial, challenge_entropy()])).hexdigest()
            fid = str(serial)
            s['flows'][fid] = {'state':'PENDING','owner':user_id,'serial':serial,'challenge':challenge,
                'expires':now+ttl,'epoch':s['epoch'],'policy':s['policy']}
            result.update(id=fid, challenge=challenge)
            event_detail.update(flow_id=fid,subject_id=user_id,purpose='authenticate',epoch=s['epoch'],outcome='created')
        self._transition('flow_allocate', transform, event_detail)
        return result

    def flow(self, fid, challenge, now, finish=False):
        event_detail = {}
        def transform(s):
            f = s['flows'].get(fid)
            expected = 'CLAIMED' if finish else 'PENDING'
            if (not f or f['state'] != expected or f['challenge'] != challenge
                    or f['expires'] <= now or f['epoch'] != s['epoch'] or f['policy'] != s['policy']):
                raise Hold('FLOW_NOT_CURRENT')
            f['state'] = 'SUCCEEDED' if finish else 'CLAIMED'
            event_detail.update(flow_id=fid,subject_id=f['owner'],purpose='authenticate',epoch=f['epoch'],outcome=f['state'])
        return self._transition('flow_finish' if finish else 'flow_claim', transform, event_detail)

    def cleanup_plan(self, *, eligible_before, now):
        if type(eligible_before) is not int or type(now) is not int or eligible_before > now:
            raise ValueError('explicit eligibility boundary required')
        with self.exclusive():
            s = self.current()
            ids = sorted(fid for fid,f in s['flows'].items() if f['expires'] <= eligible_before)
            return {'sequence':s['sequence'],'before_digest':digest(s), 'ids':ids,
                    'eligible_before':eligible_before, 'now':now}

    def cleanup(self, plan):
        # Synthetic explicit caller approval; no CLI, retention default or scheduler.
        def transform(s):
            if s['sequence'] != plan['sequence'] or digest(s) != plan['before_digest']:
                raise Hold('CLEANUP_PLAN_STALE')
            expected = sorted(fid for fid,f in s['flows'].items() if f['expires']<=plan['eligible_before'])
            if expected != plan['ids'] or plan['eligible_before'] > plan['now']:
                raise Hold('CLEANUP_PLAN_INVALID')
            for fid in plan['ids']:
                del s['flows'][fid]
        return self._transition('flow_cleanup', transform,
                                {'removed_count':len(plan['ids']), 'flow_serials':plan['ids']})
