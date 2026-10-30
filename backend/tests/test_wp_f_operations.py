"""WP-F managed encrypted copies, retention and bounded public diagnostics."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import errno
import json
import os
import sqlite3
import subprocess
import sys
import pytest
from .test_wp_b_database_safety import backup_key,data_dir,_cli
from .test_wp_f_admission import fresh,digest
from .conftest import PROJECT_ROOT


def old_backup(monkeypatch,directory,name='scheduled_old.cbk'):
    from backend.app import maintenance,config
    real=maintenance.datetime
    class Past(real):
        @classmethod
        def now(cls,tz=None):return datetime.now(tz or timezone.utc)-timedelta(days=10)
    with monkeypatch.context() as m:
        m.setattr(maintenance,'datetime',Past)
        return maintenance.backup_database(config.DB_PATH,directory/name)


def test_verified_backup_cycle_does_not_revoke_admission(data_dir):
    from backend.app import operations
    ops=fresh();permit=(data_dir/ops.PERMIT).read_bytes()
    result=operations.backup_cycle(data_dir/'backups',replica_directory=data_dir/'replica')
    assert result=={'status':'verified','removed':0,'replica_verified':True}
    local=list((data_dir/'backups').glob('scheduled_*.cbk'));remote=list((data_dir/'replica').glob('scheduled_*.cbk'))
    assert len(local)==len(remote)==1 and digest(local[0])==digest(remote[0])
    assert not local[0].read_bytes().startswith(b'SQLite format 3')
    assert (data_dir/ops.PERMIT).read_bytes()==permit
    ops.require_permit()
    assert operations.backup_status(data_dir/'backups',max_age_hours=1)=={'status':'ok','verified_count':1}


def test_retention_is_explicit_and_only_managed_verified_files_are_rotated(data_dir,monkeypatch):
    from backend.app import operations
    fresh();directory=operations._directory(data_dir/'backups')
    first=old_backup(monkeypatch,directory);manual=old_backup(monkeypatch,directory,'pre_restore_manual.cbk')
    assert operations.backup_cycle(directory)['removed']==0
    assert first.exists() and manual.exists()
    result=operations.backup_cycle(directory,retention_days=1)
    assert result['removed']==1 and not first.exists() and manual.exists()
    remaining=list(directory.glob('scheduled_*.cbk'))
    assert len(remaining)==2
    for copy in remaining:
        from backend.app import maintenance
        assert maintenance.verify_backup(copy)['authenticated'] is True


@pytest.mark.parametrize('fault',['create','verify','sync','replica_copy','corrupt_old'])
def test_backup_failure_never_prunes_last_good_copy(data_dir,monkeypatch,fault):
    from backend.app import operations,maintenance
    ops=fresh();directory=operations._directory(data_dir/'backups');old=old_backup(monkeypatch,directory);before=digest(old)
    if fault=='corrupt_old':
        (directory/'scheduled_corrupt.cbk').write_bytes(b'synthetic-corrupt');(directory/'scheduled_corrupt.cbk').chmod(0o600)
    else:
        def fail(*a,**kw):raise OSError(errno.ENOSPC if fault!='sync' else errno.EIO,'synthetic')
        if fault=='create':monkeypatch.setattr(maintenance,'backup_database',fail)
        elif fault=='verify':monkeypatch.setattr(maintenance,'verify_backup',fail)
        elif fault=='sync':monkeypatch.setattr(operations.operations_state,'_sync_directory',fail)
        else:monkeypatch.setattr(operations.shutil,'copyfileobj',fail)
    with pytest.raises((OSError,maintenance.MaintenanceError)):
        operations.backup_cycle(directory,retention_days=1,replica_directory=data_dir/'replica')
    assert old.exists() and digest(old)==before
    ops.require_permit()


def test_conflicting_operations_refuse_without_database_change(data_dir,tmp_path):
    from backend.app import operations,maintenance,config
    fresh();backup=maintenance.backup_database(config.DB_PATH,tmp_path/'b.cbk');before=digest(config.DB_PATH)
    with operations.serialized():
        assert _cli(data_dir,'backup-cycle','--directory',str(data_dir/'backups')).returncode==1
        assert _cli(data_dir,'restore',str(backup),'--confirm-offline','--yes').returncode==1
        assert _cli(data_dir,'verify-db','--confirm-offline').returncode==1
    assert digest(config.DB_PATH)==before


def test_normal_connection_lease_prevents_alternative_maintenance(data_dir):
    from backend.app import db,db_guard
    fresh();conn=db.get_connection()
    with pytest.raises(db_guard.DatabaseBusyError):
        with db_guard.exclusive_maintenance('synthetic alternative entry'):pass
    db.reset_singleton_for_tests()
    with db_guard.exclusive_maintenance('synthetic alternative entry'):pass


@pytest.mark.parametrize('path',['/api/health','/api/ready'])
def test_public_probes_contain_no_configuration_or_patient_data(auth_client,path):
    response=auth_client.get(path)
    assert response.status_code==200
    assert response.json()=={'status':'alive' if path.endswith('health') else 'ready'}
    assert response.headers['cache-control']=='no-store'
    assert 'clinic.db' not in response.text and '/' not in response.text


@pytest.mark.parametrize('bad',['missing','damaged','old','trigger'])
def test_readiness_safe_failure_and_startup_refusal(data_dir,bad):
    from backend.app import config,health,db
    ops=fresh()
    if bad=='missing':config.DB_PATH.unlink()
    elif bad=='damaged':config.DB_PATH.write_bytes(b'synthetic-not-sqlite')
    else:
        conn=sqlite3.connect(config.DB_PATH)
        if bad=='old':conn.execute('UPDATE schema_version SET version=28')
        else:conn.execute('DROP TRIGGER trg_events_archived_not_deleted')
        conn.commit();conn.close()
    report=health.readiness()
    assert report['status']=='not_ready'
    assert report['reason']=={'missing':'database_unavailable','damaged':'database_check_required','old':'migration_required','trigger':'database_check_required'}[bad]
    assert 'clinic.db' not in json.dumps(report) and str(data_dir) not in json.dumps(report)
    with pytest.raises((ops.AdmissionRequired,db.SchemaVersionError)):health.startup_check()


def test_no_automatic_policy_values(data_dir):
    fresh()
    for args in [('backup-cycle',),('backup-status','--directory',str(data_dir/'backups'))]:assert _cli(data_dir,*args).returncode==2


@pytest.mark.parametrize('age', [float('nan'), float('inf'), 0, -1])
def test_backup_freshness_requires_finite_explicit_policy(data_dir,age):
    from backend.app import operations
    fresh()
    with pytest.raises(ValueError):operations.backup_status(data_dir/'backups',max_age_hours=age)


def test_future_authenticated_backup_time_cannot_satisfy_monitoring(data_dir,monkeypatch):
    from backend.app import operations,maintenance,config
    fresh();directory=operations._directory(data_dir/'backups');real=maintenance.datetime
    class Future(real):
        @classmethod
        def now(cls,tz=None):return datetime.now(tz or timezone.utc)+timedelta(days=1)
    with monkeypatch.context() as m:
        m.setattr(maintenance,'datetime',Future)
        maintenance.backup_database(config.DB_PATH,directory/'scheduled_future.cbk')
    with pytest.raises(ValueError):operations.backup_status(directory,max_age_hours=1)
