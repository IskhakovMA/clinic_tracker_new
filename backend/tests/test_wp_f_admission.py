"""WP-F: explicit admission, real CLI, crash windows; synthetic databases only."""
import errno
import hashlib
import json
import os
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from .test_wp_b_database_safety import data_dir, backup_key, _cli, _CLI_FAULT, _v20_db
from .conftest import PROJECT_ROOT


def fresh():
    from backend.app import operations_state as ops
    ops.bootstrap_database(confirm_new=True)
    ops.verify_database(confirm_offline=True)
    return ops


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def test_new_installation_requires_bootstrap_then_operator_verification(data_dir):
    from backend.app import db, operations_state as ops, config
    with pytest.raises(ops.AdmissionRequired): db.get_connection()
    assert not config.DB_PATH.exists()
    ops.bootstrap_database(confirm_new=True)
    assert (data_dir/ops.PENDING).exists() and not (data_dir/ops.PERMIT).exists()
    with pytest.raises(ops.AdmissionRequired, match='maintenance_pending'): db.get_connection()
    before = digest(config.DB_PATH)
    ops.verify_database(confirm_offline=True)
    assert digest(config.DB_PATH) == before
    db.get_connection(); db.reset_singleton_for_tests()
    original = (data_dir/ops.PERMIT).read_bytes()
    db.get_connection(); db.reset_singleton_for_tests()
    assert (data_dir/ops.PERMIT).read_bytes() == original


def test_existing_installation_is_not_enrolled_by_start_or_check_db(data_dir):
    from backend.app import db, config, operations_state as ops
    conn = db._connect(config.DB_PATH, enable_wal=False)
    try: db.init_db(conn)
    finally: conn.close()
    before = digest(config.DB_PATH)
    with pytest.raises(ops.AdmissionRequired): db.get_connection()
    assert _cli(data_dir, 'check-db').returncode == 0
    assert not (data_dir/ops.PERMIT).exists()
    assert digest(config.DB_PATH) == before
    assert _cli(data_dir, 'verify-db', '--confirm-offline').returncode == 0
    assert digest(config.DB_PATH) == before
    ops.require_permit()


@pytest.mark.parametrize('damage',['missing_permit','missing_anchor','bad_json','duplicate_json','wrong_generation','replacement','symlink','permissions'])
def test_invalid_admission_is_always_refused(data_dir, damage):
    from backend.app import db, config
    ops = fresh(); permit=data_dir/ops.PERMIT
    if damage == 'missing_permit': permit.unlink()
    elif damage == 'missing_anchor': (data_dir/ops.GENERATION).unlink()
    elif damage == 'bad_json': permit.write_text('{')
    elif damage == 'duplicate_json': permit.write_text('{"version":1,"version":1}')
    elif damage == 'wrong_generation':
        value=json.loads(permit.read_text());value['generation']='0'*32;permit.write_text(json.dumps(value))
    elif damage == 'replacement':
        copied=data_dir/'replacement';shutil.copyfile(config.DB_PATH,copied);copied.chmod(0o600);os.replace(copied,config.DB_PATH)
    elif damage == 'symlink':
        copied=data_dir/'copied-permit';shutil.copyfile(permit,copied);copied.chmod(0o600);permit.unlink();permit.symlink_to(copied)
    else: permit.chmod(0o644)
    with pytest.raises(ops.AdmissionRequired): db.get_connection()
    assert _cli(data_dir,'purge-sessions').returncode == 1


def test_old_permit_cannot_override_pending_or_a_new_generation(data_dir):
    from backend.app import db_guard
    ops=fresh();old=(data_dir/ops.PERMIT).read_bytes()
    with db_guard.exclusive_maintenance('synthetic test'): ops.begin('migrate-db')
    (data_dir/ops.PERMIT).write_bytes(old);(data_dir/ops.PERMIT).chmod(0o600)
    with pytest.raises(ops.AdmissionRequired, match='maintenance_pending'): ops.require_permit()
    # Even accidental loss of pending does not resurrect the old permit.
    (data_dir/ops.PENDING).unlink()
    with pytest.raises(ops.AdmissionRequired, match='generation_mismatch'): ops.require_permit()


@pytest.mark.parametrize('operation',['restore','migrate-db'])
def test_successful_maintenance_exit_zero_still_requires_operator(data_dir,tmp_path,operation):
    from backend.app import maintenance,config
    ops=fresh();before=digest(config.DB_PATH)
    if operation == 'restore':
        backup=maintenance.backup_database(config.DB_PATH,tmp_path/'valid.cbk')
        result=_cli(data_dir,'restore',str(backup),'--confirm-offline','--yes')
    else: result=_cli(data_dir,'migrate-db','--confirm-offline')
    assert result.returncode == 0,result.stderr
    assert (data_dir/ops.PENDING).exists()
    with pytest.raises(ops.AdmissionRequired): ops.require_permit()
    assert _cli(data_dir,'check-db').returncode == 0
    with pytest.raises(ops.AdmissionRequired): ops.require_permit()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode == 0
    ops.require_permit()
    if operation=='migrate-db': assert digest(config.DB_PATH)==before


@pytest.mark.parametrize('operation',['restore','migrate-db'])
def test_real_cli_exit_three_blocks_next_process(data_dir,tmp_path,operation):
    from backend.app import maintenance,config
    if operation=='restore':
        ops=fresh();backup=maintenance.backup_database(config.DB_PATH,tmp_path/'b.cbk')
        args=['restore',str(backup),'--confirm-offline','--yes']
    else:
        _v20_db(data_dir/'clinic.db').chmod(0o600)
        from backend.app import operations_state as ops
        args=['migrate-db','--confirm-offline']
    result=subprocess.run([sys.executable,'-c',_CLI_FAULT,str(PROJECT_ROOT),*args],env=dict(os.environ),text=True,capture_output=True,timeout=30)
    assert result.returncode == 3,result.stdout+result.stderr
    assert (data_dir/ops.PENDING).exists()
    for command in ['purge-sessions','notifications-run-once','audit-tail']:
        assert _cli(data_dir,command).returncode == 1
    assert _cli(data_dir,'check-db').returncode == 0
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode == 0
    assert _cli(data_dir,'purge-sessions').returncode == 0


def test_error_before_database_change_keeps_pending(data_dir,tmp_path,monkeypatch):
    from backend.app import maintenance,config
    ops=fresh();before=digest(config.DB_PATH)
    backup=maintenance.backup_database(config.DB_PATH,tmp_path/'b.cbk')
    def fail(*a,**kw):raise maintenance.MaintenanceError('synthetic read failure after durable pending')
    monkeypatch.setattr(maintenance,'_decrypt_to_temp',fail)
    with pytest.raises(maintenance.MaintenanceError): maintenance.restore_database(backup,config.DB_PATH,confirm_offline=True)
    assert digest(config.DB_PATH)==before
    with pytest.raises(ops.AdmissionRequired):ops.require_permit()
    ops.verify_database(confirm_offline=True)


@pytest.mark.parametrize('error',[errno.ENOSPC,errno.EACCES,errno.EIO])
def test_failed_pending_creation_never_reaches_database(data_dir,monkeypatch,error):
    from backend.app import maintenance,config
    ops=fresh();before=digest(config.DB_PATH);old=(data_dir/ops.PERMIT).read_bytes();publish=ops._publish
    def fail(name,value):
        if name==ops.PENDING: raise OSError(error,'synthetic')
        return publish(name,value)
    monkeypatch.setattr(ops,'_publish',fail)
    with pytest.raises(maintenance.MaintenanceError):maintenance.migrate_database(config.DB_PATH,confirm_offline=True)
    assert digest(config.DB_PATH)==before and (data_dir/ops.PERMIT).read_bytes()==old
    ops.require_permit()


@pytest.mark.parametrize('point',['before_publish','rename_without_directory_sync','after_durable_publish'])
def test_failed_permit_publication_retains_pending(data_dir,monkeypatch,point):
    from backend.app import db_guard
    ops=fresh()
    with db_guard.exclusive_maintenance('synthetic test'):ops.begin('migrate-db')
    publish=ops._publish;replace=ops.os.replace
    with monkeypatch.context() as m:
        if point=='rename_without_directory_sync':
            def failing(src,dst):
                result=replace(src,dst)
                if Path(dst).name==ops.PERMIT:raise OSError(errno.EIO,'synthetic directory sync boundary')
                return result
            m.setattr(ops.os,'replace',failing)
        else:
            def failing(name,value):
                if name==ops.PERMIT and point=='before_publish':raise OSError(errno.ENOSPC,'synthetic')
                publish(name,value)
                if name==ops.PERMIT:raise OSError(errno.EIO,'synthetic')
            m.setattr(ops,'_publish',failing)
        with pytest.raises(OSError):ops.verify_database(confirm_offline=True)
    assert (data_dir/ops.PENDING).exists()
    with pytest.raises(ops.AdmissionRequired):ops.require_permit()
    ops.verify_database(confirm_offline=True);ops.require_permit()


@pytest.mark.parametrize('damage',['schema','trigger','database','permissions','wal','shm'])
def test_verification_refuses_without_repair(data_dir,damage):
    from backend.app import config,maintenance
    ops=fresh();path=config.DB_PATH
    if damage in {'schema','trigger'}:
        conn=sqlite3.connect(path)
        if damage=='schema':conn.execute('UPDATE schema_version SET version=28')
        else:conn.execute('DROP TRIGGER trg_events_archived_not_deleted')
        conn.commit();conn.close()
    elif damage=='database':path.write_bytes(b'synthetic-not-sqlite')
    elif damage=='permissions':path.chmod(0o644)
    else:
        sidecar=Path(str(path)+('-wal' if damage=='wal' else '-shm'));sidecar.write_bytes(b'synthetic-torn');sidecar.chmod(0o600)
    before={p.name:p.read_bytes() for p in data_dir.glob('clinic.db*')}
    with pytest.raises((ops.AdmissionRequired,maintenance.MaintenanceError)):ops.verify_database(confirm_offline=True)
    assert {p.name:p.read_bytes() for p in data_dir.glob('clinic.db*')}==before
    assert (data_dir/ops.PENDING).exists()


def test_http_without_lifespan_is_not_a_startup_bypass(data_dir):
    fresh()
    from backend.app.main import app
    from fastapi.testclient import TestClient
    # Deliberately no context manager: models uvicorn --lifespan off.
    client=TestClient(app,base_url='http://127.0.0.1')
    try:
        assert client.get('/api/health').json()=={'status':'alive'}
        assert client.get('/api/ready').status_code==503
        for path in ['/','/api/patients','/api/auth/me']:assert client.get(path).status_code==503
    finally:client.close()


def test_no_environment_or_cli_bypass(data_dir,monkeypatch):
    from backend.app import operations_state as ops,db
    monkeypatch.setenv('CLINIC_IGNORE_PERMIT','true');monkeypatch.setenv('CLINIC_MAINTENANCE_BYPASS','1')
    with pytest.raises(ops.AdmissionRequired):db.get_connection()
    assert _cli(data_dir,'verify-db','--skip-checks').returncode==2
    assert _cli(data_dir,'verify-db').returncode==1


_CRASH = r'''
import os,signal,sys
from pathlib import Path
from backend.app import operations_state as ops,maintenance,config
point=sys.argv[1]
def stop():
 print('REACHED',flush=True);signal.pause()
if point=='pending':
 real=ops.begin
 def begin(*a):
  value=real(*a);stop();return value
 ops.begin=begin
elif point=='replace':
 real=maintenance._install_staging
 def install(*a,**kw):
  value=real(*a,**kw);stop();return value
 maintenance._install_staging=install
elif point=='commit':
 real=maintenance.init_db
 def init(conn,*a,**kw):
  live=any(Path(r[2])==config.DB_PATH for r in conn.execute('PRAGMA database_list') if r[2])
  value=real(conn,*a,**kw)
  if live:stop()
  return value
 maintenance.init_db=init
else:
 real=ops._publish
 def publish(name,value):
  real(name,value)
  if name==ops.PERMIT:stop()
 ops._publish=publish
if point=='permit':ops.verify_database(confirm_offline=True)
elif point=='commit':maintenance.migrate_database(config.DB_PATH,confirm_offline=True)
else:maintenance.restore_database(Path(sys.argv[2]),config.DB_PATH,confirm_offline=True)
'''


@pytest.mark.parametrize('point',['pending','replace','commit','permit'])
def test_sigkill_durable_state_survives_next_process(data_dir,tmp_path,point):
    from backend.app import config,maintenance,operations_state as ops,db_guard
    if point=='commit':_v20_db(config.DB_PATH).chmod(0o600);backup=tmp_path/'unused'
    else:
        fresh();backup=maintenance.backup_database(config.DB_PATH,tmp_path/'b.cbk')
        if point=='permit':
            with db_guard.exclusive_maintenance('synthetic test'):ops.begin('migrate-db')
    process=subprocess.Popen([sys.executable,'-c',_CRASH,point,str(backup)],cwd=PROJECT_ROOT,env=dict(os.environ),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert select.select([process.stdout],[],[],20)[0], 'child did not reach checkpoint'
        assert process.stdout.readline().strip()=='REACHED'
        process.kill();assert process.wait(timeout=10)==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stdout.close();process.stderr.close()
    with pytest.raises(ops.AdmissionRequired):ops.require_permit()
    assert _cli(data_dir,'purge-sessions').returncode==1
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    assert _cli(data_dir,'purge-sessions').returncode==0


@pytest.mark.parametrize('record,kind', [('pending','file'),('pending','directory'),('permit','file'),('permit','directory')])
def test_actual_fsync_failures_respect_publication_barriers(data_dir,tmp_path,monkeypatch,record,kind):
    import stat
    from backend.app import operations_state as ops,db_guard,maintenance,config
    fresh();before=digest(config.DB_PATH);publish=ops._publish;fsync=ops.os.fsync;active=[None]
    if record=='permit':
        with db_guard.exclusive_maintenance('synthetic test'):ops.begin('migrate-db')
    def tracked(name,value):
        active[0]=name
        try:return publish(name,value)
        finally:active[0]=None
    def fail(fd):
        expected=ops.PENDING if record=='pending' else ops.PERMIT
        is_directory=stat.S_ISDIR(os.fstat(fd).st_mode)
        if active[0]==expected and is_directory==(kind=='directory'):raise OSError(errno.EIO,'synthetic fsync failure')
        return fsync(fd)
    with monkeypatch.context() as m:
        m.setattr(ops,'_publish',tracked);m.setattr(ops.os,'fsync',fail)
        if record=='pending':
            with pytest.raises(maintenance.MaintenanceError):maintenance.migrate_database(config.DB_PATH,confirm_offline=True)
        else:
            with pytest.raises(OSError):ops.verify_database(confirm_offline=True)
    assert digest(config.DB_PATH)==before
    if record=='pending' and kind=='file':
        assert not (data_dir/ops.PENDING).exists()
        ops.require_permit()  # no durable intent and NO DB mutation occurred
    else:
        with pytest.raises(ops.AdmissionRequired):ops.require_permit()
        if record=='permit' and kind=='directory':assert (data_dir/ops.PERMIT).exists()  # visible rename != durable permit
        # Explicit operator recovery after fixing I/O; never an automatic retry.
        if record=='pending':maintenance.migrate_database(config.DB_PATH,confirm_offline=True)
        ops.verify_database(confirm_offline=True)
        ops.require_permit()


def test_cached_http_database_path_is_not_a_permit_bypass(auth_client):
    from backend.app import config,operations_state as ops,deps,db
    assert auth_client.get('/api/patients').status_code==200
    permit=config.DATA_DIR/ops.PERMIT;permit.write_text('{')
    response=auth_client.get('/api/patients')
    assert response.status_code==503 and response.json()=={'error':'Service unavailable'}
    with pytest.raises(ops.AdmissionRequired):next(deps.get_db())
    with pytest.raises(ops.AdmissionRequired):db.get_connection()
