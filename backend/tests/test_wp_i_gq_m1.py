"""GQ-M1 mechanism only; real temporary DB, original write/audit/admission guards."""
from pathlib import Path
import json
import os
import signal
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from .conftest import create_account, create_department, get_conn, login, make_patient_payload, app_stopped


@pytest.fixture
def qlab(auth_client):
    from backend.app import quality_revision as q, users
    c = get_conn(); doctor = create_account('gq.doc', 'doctor'); other = create_account('gq.peer', 'doctor')
    dept = create_department('Синтетическое GQ'); users.add_doctor_to_department(c, doctor, dept)
    r = auth_client.post('/api/patients', headers=auth_client.csrf, json=make_patient_payload(name='Синтетический GQ', phone='+7 900 111-00-01', treatingDoctorUserId=doctor, events=[{'date':'2026-12-02','time':'10:00'}]))
    assert r.status_code == 201, r.text
    patient = r.json(); pid = patient['id']
    return {'client': auth_client, 'conn': c, 'q': q, 'doctor': doctor, 'other': other, 'dept': dept, 'pid': pid,
            'hid':c.execute('SELECT id FROM history_entries WHERE patient_id=?',(pid,)).fetchone()[0],
            'eid':c.execute('SELECT e.id FROM events e JOIN history_entries h ON h.id=e.history_entry_id WHERE h.patient_id=?',(pid,)).fetchone()[0]}


def rev(lab):return lab['q'].require_valid(lab['conn']).revision


def test_bootstrap_and_repeat_init_preserve_state(qlab):
    from backend.app import db, config
    q = qlab['q']; c = qlab['conn']; before = q.require_valid(c, structure=True)
    assert config.SCHEMA_VERSION == 33 and before.layout_version == 2
    assert c.execute('SELECT COUNT(*) FROM quality_revision_state').fetchone()[0] == 1
    db.init_db(c, allow_upgrade=False)
    assert q.require_valid(c) == before
    expected = {n for n,_ in q.trigger_definitions()}; assert len(expected) == 33
    assert expected <= set(db.reference_triggers())
    assert not db.trigger_drift(c)['missing'] and not db.trigger_drift(c)['changed']


@pytest.mark.parametrize('which', ['patient','doctor','department','role','member_delete','member_insert','head_insert','head_delete','event','checklist','history'])
def test_actual_change_and_revision_rollback_are_atomic(qlab, which):
    c=qlab['conn'];before=rev(qlab);p=qlab['pid'];d=qlab['doctor'];dep=qlab['dept'];h=qlab['hid'];e=qlab['eid']
    commands={
      'patient':('UPDATE patients SET name=? WHERE id=?',('Другой синтетический',p)),
      'doctor':('UPDATE patients SET treating_doctor_user_id=? WHERE id=?',(qlab['other'],p)),
      'department':('UPDATE departments SET name=? WHERE id=?',('Синтетическое новое',dep)),
      'role':('UPDATE roles SET label=? WHERE id=?',('Синтетическая метка','doctor')),
      'member_delete':('DELETE FROM user_departments WHERE user_id=? AND department_id=?',(d,dep)),
      'member_insert':('INSERT INTO user_departments VALUES(?,?,?)',(qlab['other'],dep,'2026-10-01')),
      'head_insert':('INSERT INTO department_heads VALUES(?,?,?)',(d,dep,'2026-10-01')),
      'head_delete':('DELETE FROM department_heads WHERE user_id=? AND department_id=?',(d,dep)),
      'event':('UPDATE events SET date=? WHERE id=?',('2026-12-01',e)),
      'checklist':('UPDATE history_entries SET checklist_json=? WHERE id=?',('{"synthetic":true}',h)),
      'history':('INSERT INTO history_entries(id,patient_id,seq,stage,entered_at) VALUES(?,?,1,?,?)',('gq-next-history',p,'appointment','2026-10-02')),
    }
    if which=='head_delete':
        with c:c.execute('INSERT INTO department_heads VALUES(?,?,?)',(d,dep,'2026-10-01'))
        before=rev(qlab)
    sql,args=commands[which];c.execute('BEGIN IMMEDIATE');c.execute(sql,args);assert rev(qlab)>before;c.rollback();assert rev(qlab)==before
    with c:c.execute(sql,args)
    assert rev(qlab)>before


@pytest.mark.parametrize('value', ['синтетический gq','Синтетическии GQ','Синтетический GQ ё','Синтетический GQ е','Синтетический GQ е\u0308','',None])
def test_binary_null_and_unicode_projection(qlab, value):
    c=qlab['conn'];before=rev(qlab)
    # Nullable phone preserves NULL vs empty; no policy normalization in hooks.
    with c:c.execute('UPDATE patients SET phone=? WHERE id=?',(value,qlab['pid']))
    assert rev(qlab)>before
    same=rev(qlab)
    with c:c.execute('UPDATE patients SET phone=? WHERE id=?',(value,qlab['pid']))
    assert rev(qlab)==same


@pytest.mark.parametrize('which', ['patient_touch','note','last_login','audit','session_activity','inbox','template'])
def test_excluded_writers_do_not_increment(qlab, which):
    from backend.app import store, users, audit, security
    c=qlab['conn'];before=rev(qlab)
    if which=='patient_touch':
        with c:store.touch_patient(c,qlab['pid'])
    elif which=='note':
        with c:c.execute('UPDATE history_entries SET note=? WHERE id=?',('Синтетическая заметка',qlab['hid']))
    elif which=='last_login':users.touch_last_login(c,qlab['doctor'])
    elif which=='audit':
        c.execute('BEGIN IMMEDIATE')
        with c:audit.record_required_or_fail(c,'synthetic_gq_audit',detail='test_only')
    elif which=='session_activity':
        security.create_session(c,qlab['doctor'])
        with c:
            assert c.execute("UPDATE sessions SET last_seen_at='2026-01-01T00:00:00+00:00' WHERE user_id=?",(qlab['doctor'],)).rowcount>0
        assert c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(qlab['doctor'],)).fetchone()[0]>0
    elif which=='inbox':
        with c:
            c.execute("INSERT INTO internal_notification_state(id,user_id,stable_key,patient_id,notification_type,source_type,source_id,first_seen_at,last_seen_at) VALUES('gq-inbox',?,'gq-key',?,'data_quality','data_quality','synthetic','2026-01-01','2026-01-01')",(qlab['doctor'],qlab['pid']))
            assert c.execute("UPDATE internal_notification_state SET read_at='2026-01-02' WHERE id='gq-inbox'").rowcount==1
    else:
        with c:c.execute('UPDATE notification_templates SET body=body,version=version+1')
    assert rev(qlab)==before
    r=qlab['client'].get('/api/data-quality/summary');assert r.status_code==200
    assert rev(qlab)==before


@pytest.mark.parametrize('policy', ['IGNORE','REPLACE'])
@pytest.mark.parametrize('fault', ['missing','overflow','bad_type'])
def test_outer_conflict_cannot_suppress_counter_failure(qlab, policy, fault):
    c=qlab['conn'];q=qlab['q'];original=q.require_valid(c);name=c.execute('SELECT name FROM patients WHERE id=?',(qlab['pid'],)).fetchone()[0]
    c.execute('DROP TRIGGER trg_gq_state_forward');c.execute('DROP TRIGGER trg_gq_state_no_delete')
    if fault=='missing':c.execute('DELETE FROM quality_revision_state')
    else:
        c.execute('PRAGMA ignore_check_constraints=ON');c.execute('UPDATE quality_revision_state SET revision=?',(q.MAX_REVISION if fault=='overflow' else 'broken',));c.execute('PRAGMA ignore_check_constraints=OFF')
    c.commit()
    for n,sql in q.trigger_definitions():
        if n in {'trg_gq_state_forward','trg_gq_state_no_delete'}:c.execute(sql)
    c.commit()
    with pytest.raises(q.QualityRevisionError,match='quality_revision_unavailable'):
        with c:c.execute(f'UPDATE OR {policy} patients SET name=? WHERE id=?',('Must not persist',qlab['pid']))
    assert c.execute('SELECT name FROM patients WHERE id=?',(qlab['pid'],)).fetchone()[0]==name
    assert qlab['client'].get('/api/patients').status_code==503


@pytest.mark.parametrize('sql', [
 'DELETE FROM quality_revision_state',
 'UPDATE quality_revision_state SET revision=0',
 'UPDATE OR IGNORE quality_revision_state SET revision=revision',
 'UPDATE OR REPLACE quality_revision_state SET incarnation=incarnation||\'a\'',
 'INSERT OR IGNORE INTO quality_revision_state VALUES(1,2,\'00000000000000000000000000000000\',0)',
 'INSERT OR REPLACE INTO quality_revision_state SELECT * FROM quality_revision_state',
])
def test_metadata_cannot_reset_delete_or_replace(qlab, sql):
    before=qlab['q'].require_valid(qlab['conn'])
    with pytest.raises(sqlite3.IntegrityError,match='quality_revision_immutable'):
        with qlab['conn']:qlab['conn'].execute(sql)
    assert qlab['q'].require_valid(qlab['conn'])==before


def test_ignore_upsert_replace_and_multirow(qlab):
    c=qlab['conn'];before=rev(qlab)
    with c:c.execute("INSERT OR IGNORE INTO roles VALUES('doctor','ignored','global')")
    assert rev(qlab)==before
    with c:c.execute("INSERT INTO roles VALUES('doctor','Новое имя','by_treating_doctor') ON CONFLICT(id) DO UPDATE SET label=excluded.label")
    assert rev(qlab)==before+1
    n=rev(qlab)
    with c:c.execute("INSERT OR REPLACE INTO roles VALUES('gq_unused','Синтетическая','global_readonly')")
    assert rev(qlab)>n
    n=rev(qlab);count=c.execute('SELECT COUNT(*) FROM roles').fetchone()[0]
    with c:c.execute("UPDATE roles SET label=label||'!'")
    assert rev(qlab)==n+count
    n=rev(qlab)
    with c:c.execute("DELETE FROM roles WHERE id='gq_unused'")
    assert rev(qlab)==n+1


def test_source_hook_ddl_drift_is_refused_not_healed(qlab):
    from backend.app import db, config, health, maintenance
    c=qlab['conn'];c.execute('DROP TRIGGER trg_gq_patients_update');c.commit()
    assert health.readiness()['status']=='not_ready'
    with pytest.raises(db.SchemaDriftError):db.connect_live(config.DB_PATH)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='trg_gq_patients_update'").fetchone()
    assert not maintenance.check_database(config.DB_PATH,read_only=True)['ok']


@pytest.mark.parametrize('phase', ['before_commit','after_commit'])
def test_sigkill_keeps_source_and_counter_together(qlab, phase):
    from backend.app import config
    c=qlab['conn'];before=rev(qlab);name=c.execute('SELECT name FROM patients WHERE id=?',(qlab['pid'],)).fetchone()[0]
    code='''import sys,sqlite3,json
c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA foreign_keys=ON');c.execute('BEGIN IMMEDIATE')
c.execute('UPDATE patients SET name=? WHERE id=?',('Crash synthetic',sys.argv[2]))
if sys.argv[3]=='after_commit':c.commit()
print(json.dumps({'ready':True,'phase':sys.argv[3]}),flush=True)
sys.stdin.read()
'''
    child=subprocess.Popen([sys.executable,'-c',code,str(config.DB_PATH),qlab['pid'],phase],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:ack=pool.submit(child.stdout.readline).result(timeout=10)
        assert json.loads(ack)=={'ready':True,'phase':phase}
        os.kill(child.pid,signal.SIGKILL);assert child.wait(timeout=10)==-signal.SIGKILL
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdout.close();child.stdin.close()
    changed=c.execute('SELECT name FROM patients WHERE id=?',(qlab['pid'],)).fetchone()[0]
    if phase=='before_commit':assert changed==name and rev(qlab)==before
    else:assert changed=='Crash synthetic' and rev(qlab)>before


def test_http_clinical_writes_and_relationships(qlab):
    c=qlab['conn'];cl=qlab['client'];before=rev(qlab)
    p=cl.get('/api/patients/'+qlab['pid']).json()
    r=cl.patch('/api/patients/'+qlab['pid'],headers=cl.csrf,json={'field':'name','value':'Новое синтетическое','expectedVersion':p['version']})
    assert r.status_code==200,r.text;assert rev(qlab)>before
    n=rev(qlab);r=cl.patch('/api/users/'+qlab['doctor'],headers=cl.csrf,json={'departmentIds':[]})
    assert r.status_code==200,r.text;assert rev(qlab)>n
    n=rev(qlab);r=cl.get('/api/data-quality/issues?mode=quality-v1');assert r.status_code==503
    assert rev(qlab)==n

# Offline fixtures belong to this new module; all CLI calls use tmp_path/data.
from .test_wp_b_database_safety import data_dir, backup_key, _cli

V32 = Path(__file__).parent/'fixtures/gq_v32_synthetic.sql'


def _load_v32(directory):
    path=directory/'clinic.db';c=sqlite3.connect(path);c.executescript(V32.read_text());c.close();path.chmod(0o600);return path


def test_real_cli_v32_migration_backup_restore_and_generation(data_dir, tmp_path):
    from backend.app import config, db, operations_state, maintenance, quality_revision as q
    path=_load_v32(data_dir)
    c=sqlite3.connect(path)
    with c:c.execute("INSERT INTO patients(id,name,priority,stage,arrival,created_at) VALUES('m1-legacy','Синтетический v32','normal','exam','2026-01-01','2026-01-01')")
    before=c.execute('SELECT * FROM patients').fetchall();c.close()
    assert maintenance.identify_backup_build(path,32)=='wp-i-a1-bacaf72-v32'
    r=_cli(data_dir,'migrate-db','--confirm-offline');assert r.returncode==0,r.stderr+r.stdout
    assert (data_dir/operations_state.PENDING).exists()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    first=operations_state.require_permit();assert first['version']==1 and first['schema']==33
    c=db.connect_live(path);state=q.require_valid(c);assert [tuple(x) for x in c.execute('SELECT * FROM patients')]==before;c.close()
    backup=tmp_path/'gq.cbk';maintenance.backup_database(path,backup)
    c=db.connect_live(path);assert q.require_valid(c)==state;c.close()
    assert _cli(data_dir,'migrate-db','--confirm-offline').returncode==0
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    second=operations_state.require_permit();assert second['generation']!=first['generation']
    c=db.connect_live(path);assert q.require_valid(c)==state
    with c:c.execute("UPDATE patients SET name='Измененный синтетический' WHERE id='m1-legacy'")
    assert q.require_valid(c).revision>state.revision;c.close()
    result=maintenance.restore_database(backup,path,confirm_offline=True);assert result['schema_version']==33 and result['restored_from']==backup.name
    assert (data_dir/operations_state.PENDING).exists()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    third=operations_state.require_permit();assert third['generation']!=second['generation']
    c=db.connect_live(path);assert q.require_valid(c)==state
    assert [tuple(x) for x in c.execute('SELECT * FROM patients')]==before;c.close()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    assert operations_state.require_permit()['generation']!=third['generation']


@pytest.mark.parametrize('fault', ['ddl','trigger','metadata'])
def test_low_level_migration_fault_rolls_back_whole_schema(data_dir, monkeypatch, fault):
    from backend.app import db, quality_revision as q
    path=_load_v32(data_dir);c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    db.reference_triggers()  # Prime the valid reference before injected failure.
    before=[tuple(x) for x in c.execute('SELECT * FROM roles')]
    if fault=='ddl':monkeypatch.setattr(q,'TABLE_SQL','CREATE TABLE invalid SQL')
    elif fault=='trigger':
        old=q.trigger_definitions();monkeypatch.setattr(q,'trigger_definitions',lambda:old[:1]+(('bad','CREATE TRIGGER bad SQL'),))
    else:monkeypatch.setattr(q.secrets,'token_hex',lambda n:'not-a-valid-incarnation')
    with pytest.raises(sqlite3.Error):db.init_db(c)
    assert not c.in_transaction
    assert c.execute('SELECT version FROM schema_version').fetchone()[0]==32
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='quality_revision_state'").fetchone()
    assert [tuple(x) for x in c.execute('SELECT * FROM roles')]==before;c.close()


@pytest.mark.parametrize('fault',['missing','type','layout'])
def test_real_verify_cli_invalid_state_no_traceback_then_offline_recovery(data_dir,fault):
    from backend.app import db,config,operations_state,quality_revision as q
    operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
    c=db._connect(config.DB_PATH);saved=c.execute('SELECT * FROM quality_revision_state').fetchone();c.execute('DROP TRIGGER trg_gq_state_forward');c.execute('DROP TRIGGER trg_gq_state_no_delete')
    if fault=='missing':c.execute('DELETE FROM quality_revision_state')
    else:
        c.execute('PRAGMA ignore_check_constraints=ON');c.execute('UPDATE quality_revision_state SET '+('revision' if fault=='type' else 'layout_version')+'=?',('broken' if fault=='type' else 99,));c.execute('PRAGMA ignore_check_constraints=OFF')
    c.commit()
    for name,sql in q.trigger_definitions():
        if name in {'trg_gq_state_forward','trg_gq_state_no_delete'}:c.execute(sql)
    c.commit();c.close()
    result=_cli(data_dir,'verify-db','--confirm-offline');assert result.returncode==1
    assert 'Traceback' not in result.stdout+result.stderr
    assert (data_dir/operations_state.PENDING).exists()
    with pytest.raises(operations_state.AdmissionRequired):operations_state.require_permit()
    # Explicit synthetic repair models operator investigation, not a new command.
    c=db._connect(config.DB_PATH)
    for name,_ in q.trigger_definitions():
        if name.startswith('trg_gq_state_'):c.execute('DROP TRIGGER '+name)
    c.execute('DELETE FROM quality_revision_state');c.execute('INSERT INTO quality_revision_state VALUES(?,?,?,?)',tuple(saved))
    for name,sql in q.trigger_definitions():
        if name.startswith('trg_gq_state_'):c.execute(sql)
    c.commit();c.close()
    assert _cli(data_dir,'verify-db','--confirm-offline').returncode==0
    operations_state.require_permit()


def test_erroneous_projection_and_registry_only_writes(client):
    from .test_wp24d_duplicate_review import _setup,_mark,_unmark,_candidate_for,_technical
    from backend.app import quality_revision as q
    ids,a,b,cid,dev,deputy=_setup(client);c=get_conn();before=q.require_valid(c)
    # Registry review is not a source for computed Quality duplicate groups.
    r=_technical(dev,cid,'start');assert r.status_code==200,r.text
    assert q.require_valid(c)==before
    r=_mark(deputy,cid,b);assert r.status_code==200,r.text
    after=q.require_valid(c);assert after.revision>before.revision
    assert c.execute('SELECT 1 FROM patient_erroneous_state WHERE patient_id=?',(b,)).fetchone()
    mark=c.execute('SELECT mark_id FROM patient_erroneous_state WHERE patient_id=?',(b,)).fetchone()[0]
    r=_unmark(deputy,b,mark);assert r.status_code==200,r.text
    assert q.require_valid(c).revision>after.revision
    assert not c.execute('SELECT 1 FROM patient_erroneous_state WHERE patient_id=?',(b,)).fetchone()


def test_http_import_stage_event_and_checklist_are_sources(qlab):
    from .conftest import unsigned_import,event_patch
    cl=qlab['client'];c=qlab['conn'];q=qlab['q'];before=rev(qlab)
    payload=make_patient_payload(name='Импорт синтетический GQ',phone='+7 900 222-00-99',events=[{'date':'2026-12-04','time':'11:00'}]);payload.pop('policyNumber')
    r=unsigned_import(cl,json.dumps({'schemaVersion':1,'patients':[payload]},ensure_ascii=False),'add');assert r.status_code==200,r.text
    assert rev(qlab)>before
    before=rev(qlab);r=event_patch(cl,qlab['pid'],'date','2026-12-03');assert r.status_code==200,r.text
    assert rev(qlab)>before
    before=rev(qlab)
    r=cl.patch('/api/patients/'+qlab['pid']+'/history/0/checklist',headers=cl.csrf,json={'item':'Общий анализ крови','checked':True,'expected':False})
    assert r.status_code==200,r.text;assert rev(qlab)>before
    before=rev(qlab);r=cl.post('/api/patients/'+qlab['pid']+'/stage',headers=cl.csrf,json={'newStage':'appointment','expectedStage':'exam','expectedHistoryEntryId':qlab['hid']})
    assert r.status_code==200,r.text;assert rev(qlab)>before


def test_real_structure_cli_direct_link_and_auth_housekeeping(data_dir,tmp_path):
    from backend.app import db,config,operations_state,quality_revision as q
    from .conftest import TEST_PASSWORD
    operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
    c=db.connect_live(config.DB_PATH);before=q.require_valid(c);c.close()
    f=tmp_path/'structure.json';f.write_text(json.dumps({'departments':[{'name':'Синтетическое CLI','head':{'username':'gq.cli.head','fullName':'Синтетический Заведующий Тестович','password':TEST_PASSWORD},'doctors':[{'username':'gq.cli.doc','fullName':'Синтетический Врач Тестович','password':TEST_PASSWORD}]}]},ensure_ascii=False))
    r=_cli(data_dir,'import-structure',str(f));assert r.returncode==0
    c=db.connect_live(config.DB_PATH);assert q.require_valid(c).revision>before.revision
    assert c.execute('SELECT COUNT(*) FROM department_heads').fetchone()[0]==1
    assert c.execute('SELECT COUNT(*) FROM user_departments').fetchone()[0]==1
    after=q.require_valid(c);c.close()
    r=_cli(data_dir,'purge-sessions');assert r.returncode==0
    c=db.connect_live(config.DB_PATH);assert q.require_valid(c)==after;c.close()


def test_existing_history_guard_is_preserved(qlab):
    c=qlab['conn'];before=rev(qlab)
    with pytest.raises(sqlite3.IntegrityError):
        with c:c.execute('UPDATE history_entries SET entered_at=? WHERE id=?',('2026-01-01',qlab['hid']))
    assert rev(qlab)==before


@pytest.mark.parametrize('operation',['patient_create','patient_delete','event_update','event_delete','event_restore','staff','department'])
def test_service_source_and_revision_rollback_on_required_audit_failure(qlab,operation):
    from backend.app import store,users,db,models,policy
    from backend.app.services import patient_create
    from .conftest import create_empty_card
    c=qlab['conn'];p=qlab['pid'];e=qlab['eid'];actor=c.execute("SELECT id FROM users WHERE role_id='developer'").fetchone()[0]
    if operation=='patient_delete':p=create_empty_card(qlab['client'],name='Пустой Синтетический GQ',policyNumber='')
    if operation=='event_restore':store.delete_event(c,p,e,1,actor)
    before=rev(qlab)
    tables=('patients','history_entries','events','users','departments','audit_events')
    snapshot=lambda:{t:[tuple(x) for x in c.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}
    original=snapshot()
    class SyntheticAuditFailure(Exception):pass
    def fail(*a,**kw):
        assert rev(qlab)>before
        raise SyntheticAuditFailure('synthetic required audit failure')
    with pytest.raises(SyntheticAuditFailure):
        if operation=='patient_create':
            with db.transaction(c):
                body=models.PatientCreateIn(**make_patient_payload(name='Откат Создания GQ'))
                patient_create._create_locked(c,policy.load_current_user(c,actor),body)
                fail()
        elif operation=='patient_delete':store.delete_patient(c,p,audit_hook=fail)
        elif operation=='event_update':store.update_event(c,p,e,'date','2026-12-05',1,audit_hook=fail)
        elif operation=='event_delete':store.delete_event(c,p,e,1,actor,audit_hook=fail)
        elif operation=='event_restore':store.restore_event(c,p,e,2,audit_hook=fail)
        else:
            with db.transaction(c):
                if operation=='staff':users.update_user_fields(c,qlab['doctor'],full_name='Синтетический Измененный Врач',commit=False)
                else:c.execute("UPDATE departments SET status='archived' WHERE id=?",(qlab['dept'],))
                fail()
    assert rev(qlab)==before and snapshot()==original


def test_foreign_key_cascade_counts_each_source_and_rolls_back(qlab):
    from .conftest import create_empty_card
    c=qlab['conn'];p=create_empty_card(qlab['client'],name='Пустой Каскад Синтетический',policyNumber='')
    before=rev(qlab);assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
    assert c.execute('SELECT COUNT(*) FROM history_entries WHERE patient_id=?',(p,)).fetchone()[0]==1
    # Existing medical guard allows only a genuinely empty card and its empty initial history.
    c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM patients WHERE id=?',(p,))
    assert rev(qlab)==before+2
    assert not c.execute('SELECT 1 FROM history_entries WHERE patient_id=?',(p,)).fetchone()
    c.rollback();assert rev(qlab)==before
    assert c.execute('SELECT 1 FROM patients WHERE id=?',(p,)).fetchone()
    with c:c.execute('DELETE FROM patients WHERE id=?',(p,))
    assert rev(qlab)==before+2


def test_cached_http_connection_cannot_bypass_missing_source_hook(qlab):
    cl=qlab['client'];c=qlab['conn'];p=qlab['pid'];before=cl.get('/api/patients/'+p).json()
    c.execute('DROP TRIGGER trg_gq_patients_update');c.commit()
    r=cl.patch('/api/patients/'+p,headers=cl.csrf,json={'field':'name','value':'Forbidden synthetic','expectedVersion':before['version']})
    assert r.status_code==503
    assert c.execute('SELECT name FROM patients WHERE id=?',(p,)).fetchone()[0]==before['name']
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='trg_gq_patients_update'").fetchone()


def test_counter_failure_during_http_write_is_503_and_rolls_back(qlab):
    cl=qlab['client'];c=qlab['conn'];p=qlab['pid'];before=cl.get('/api/patients/'+p).json();revision=rev(qlab)
    c.execute("CREATE TRIGGER synthetic_counter_fault BEFORE UPDATE ON quality_revision_state BEGIN SELECT RAISE(ABORT,'quality_revision_unavailable'); END");c.commit()
    r=cl.patch('/api/patients/'+p,headers=cl.csrf,json={'field':'name','value':'Forbidden synthetic','expectedVersion':before['version']})
    assert r.status_code==503 and 'Forbidden synthetic' not in r.text
    assert rev(qlab)==revision and c.execute('SELECT name FROM patients WHERE id=?',(p,)).fetchone()[0]==before['name']
