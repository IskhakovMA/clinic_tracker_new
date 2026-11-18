"""Storage limits and explicit v29→v30 admission on synthetic databases."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from .test_wp_b_database_safety import data_dir,backup_key,_cli


@pytest.mark.parametrize('secure',[0,1])
def test_secure_delete_and_wal_are_distinct(tmp_path,secure):
    path=tmp_path/'residue.db';c=sqlite3.connect(path)
    c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0')
    c.execute('PRAGMA secure_delete='+str(secure));c.execute('CREATE TABLE synthetic (value TEXT)')
    marker=b'SYNTHETIC-STORAGE-MARKER'
    c.execute('INSERT INTO synthetic VALUES (?)',(marker.decode()*100,));c.commit()
    c.execute('PRAGMA wal_checkpoint(FULL)');c.execute('DELETE FROM synthetic');c.commit()
    assert c.execute('SELECT COUNT(*) FROM synthetic').fetchone()[0]==0
    assert marker in path.read_bytes()  # main file not checkpointed yet
    assert marker in Path(str(path)+'-wal').read_bytes()  # older WAL frame still exists
    c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert Path(str(path)+'-wal').stat().st_size==0
    assert (marker in path.read_bytes()) is (not bool(secure))
    c.execute('VACUUM');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()
    assert marker not in path.read_bytes()  # logical test only, no SSD erasure claim


def test_explicit_v29_migration_requires_operator_and_preserves_medical_data(data_dir):
    from backend.app import db,config,operations_state,maintenance
    c=db._connect(config.DB_PATH);db.init_db(c)
    c.execute("INSERT INTO patients (id,name,priority,stage,arrival,created_at,diagnosis,complaint) VALUES ('v29-patient','Синтетическая миграция','normal','exam','2026-01-01','2026-01-01','C34.1','synthetic clinical marker')")
    c.execute("INSERT INTO history_entries (id,patient_id,seq,stage,entered_at,note) VALUES ('v29-history','v29-patient',0,'exam','2026-01-01','synthetic history marker')")
    c.execute("INSERT INTO events (id,history_entry_id,seq,date) VALUES ('v29-event','v29-history',0,'2026-01-02')")
    expected={table:[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY id')] for table in ('patients','history_entries','events')}
    from .conftest import strip_gq_v33
    strip_gq_v33(c)
    # Remove only v31 technical storage when reconstructing the historical v29 fixture.
    from backend.app.mfa_lifecycle_schema import TABLES as LIFECYCLE_TABLES
    for table in reversed(LIFECYCLE_TABLES):
        c.execute('DROP TABLE IF EXISTS '+table)
    from backend.app.mfa_schema import TABLES
    for table in reversed(TABLES):
        c.execute('DROP TABLE '+table)
    c.execute('DROP TRIGGER trg_read_manifest_immutable');c.execute('DROP TRIGGER trg_read_manifest_retained')
    c.execute('DROP TABLE read_access_manifests');c.execute('UPDATE schema_version SET version=29');c.commit();c.close()
    assert maintenance.identify_backup_build(config.DB_PATH,29)=='wpf-4042eba'
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==1
    result=_cli(data_dir,'migrate-db','--confirm-offline');assert result.returncode==0,result.stderr+result.stdout
    assert (data_dir/operations_state.PENDING).exists()
    with pytest.raises(operations_state.AdmissionRequired):operations_state.require_permit()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    operations_state.require_permit()
    c=db.connect_live(config.DB_PATH)
    assert c.execute('SELECT version FROM schema_version').fetchone()[0]==config.SCHEMA_VERSION
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='read_access_manifests'").fetchone()[0]==1
    assert {table:[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY id')] for table in expected}==expected
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert list(data_dir.glob('backups/pre_migrate_v29_*.cbk'))
    assert not db.trigger_drift(c)['missing'];c.close()


def test_backup_temp_cleanup_and_sigkill_residue(data_dir,tmp_path,monkeypatch):
    from backend.app import db,config,maintenance,operations_state
    operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
    dest=tmp_path/'ok.cbk';maintenance.backup_database(config.DB_PATH,dest)
    assert not list(data_dir.glob('clinic-backup-*'))
    env={**os.environ,'CLINIC_DATA_DIR':str(data_dir)}
    script='''import os,signal
from backend.app import maintenance,config
def killed(*a,**k):os.kill(os.getpid(),signal.SIGKILL)
maintenance._write_container=killed
maintenance.backup_database(config.DB_PATH,config.DATA_DIR/'killed.cbk')
'''
    child=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True)
    assert child.returncode==-signal.SIGKILL,child.stderr
    leftovers=list(data_dir.glob('clinic-backup-*'))
    assert len(leftovers)==1
    snapshot=leftovers[0]/'snapshot.sqlite'
    assert snapshot.exists() and snapshot.stat().st_mode & 0o077==0
    assert leftovers[0].stat().st_mode & 0o077==0
    assert not (data_dir/'killed.cbk').exists()
    # Intentional crash residue is evidence; pytest owns/removes the synthetic dir.
    operations_state.require_permit()  # backup itself never revokes admission
