"""Synthetic v31 table sizing/query/delete experiment; no production cleanup API.
Uses real v31 DDL, temporary SQLite and synthetic technical fields only.
"""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import statistics
import tempfile
import time
from backend.app.mfa_schema import SQL


def measure():
    with tempfile.TemporaryDirectory(prefix='clinic-stage2b-capacity-') as td:
        path=Path(td)/'synthetic.sqlite'
        c=sqlite3.connect(path)
        try:
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('PRAGMA synchronous=FULL')
            c.executescript('CREATE TABLE users(id TEXT PRIMARY KEY); CREATE TABLE sessions(token_hash TEXT PRIMARY KEY);')
            c.executescript(SQL)
            for n in range(32):
                u=f'synthetic-{n}'
                c.execute('INSERT INTO users VALUES(?)',(u,))
                c.execute("INSERT INTO mfa_user_state VALUES(?,?,0,'ACTIVE')",(u,secrets.token_bytes(32)))
            c.execute('CREATE TABLE lab_events(flow_id TEXT PRIMARY KEY,event TEXT NOT NULL)')
            base=path.stat().st_size
            start=time.perf_counter()
            rows=[]
            for n in range(4096):
                token=hashlib.sha256(('synthetic-flow-'+str(n)).encode()).hexdigest()
                rows.append((token,f'synthetic-{n//128}','f'*64,secrets.token_bytes(32),
                    'authenticate','FAILED',0,'synthetic-password-revision','synthetic-access',
                    'a'*32,'b'*64,'2020-01-01T00:00:00+00:00',None,None,
                    json.dumps({'synthetic_library_state':'x'*1024})))
            c.executemany('INSERT INTO mfa_flows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
            c.executemany("INSERT INTO lab_events VALUES(?,'synthetic_flow_closed')",[(r[0],) for r in rows])
            c.commit()
            insert_ms=(time.perf_counter()-start)*1000
            loaded=path.stat().st_size
            times=[]
            for _ in range(200):
                start=time.perf_counter()
                assert c.execute('SELECT COUNT(*) FROM mfa_flows WHERE user_id=?',('synthetic-0',)).fetchone()[0]==128
                assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==4096
                times.append((time.perf_counter()-start)*1000)
            selected=[r[0] for r in rows[:128]]
            start=time.perf_counter()
            c.execute('BEGIN IMMEDIATE')
            # Explicit synthetic boundary, not a retention recommendation.
            c.executemany("DELETE FROM mfa_flows WHERE token_hash=? AND state='FAILED' AND expires_at<=?",[(i,'2020-01-01T00:00:00+00:00') for i in selected])
            c.commit()
            delete_ms=(time.perf_counter()-start)*1000
            assert c.execute('SELECT COUNT(*) FROM mfa_flows').fetchone()[0]==3968
            assert c.execute('SELECT COUNT(*) FROM lab_events').fetchone()[0]==4096
            assert c.execute('SELECT COUNT(*) FROM mfa_flows WHERE user_id=?',('synthetic-0',)).fetchone()[0]==0
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            return {'synthetic_only':True,'real_v31_mfa_ddl':True,'users':32,'rows':4096,
                'rows_per_user':128,'base_file_bytes':base,'loaded_file_bytes':loaded,
                'growth_bytes_per_flow_including_lab_event':(loaded-base)/4096,
                'bulk_insert_ms':insert_ms,'budget_query_pair_median_ms':statistics.median(times),
                'budget_query_pair_max_ms':max(times),'query_samples':len(times),
                'explicit_batch_rows_removed':128,'batch_delete_ms':delete_ms,'remaining_flows':3968,
                'retained_lab_events':4096,'freelist_pages':c.execute('PRAGMA freelist_count').fetchone()[0],
                'post_delete_file_bytes':path.stat().st_size,'automatic_cleanup_enabled':False,
                'note':'Physical bytes need not shrink; freelist pages are reusable. Not a whole MFA service throughput/SLA benchmark.'}
        finally:c.close()


if __name__=='__main__':
    print(json.dumps(measure(),indent=2))
