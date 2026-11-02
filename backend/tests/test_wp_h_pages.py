"""WP-H page contract on real ASGI/SQLite; legacy security assertions remain."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from .conftest import get_conn,make_patient_payload,create_account


def seed(count=65,*,names=None,doctor=None):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    c=get_conn();ids=[]
    with c:
        for i in range(count):
            body=PatientCreateIn(**make_patient_payload(events=[{'date':'2026-12-01','time':'10:00'}],name=(names[i] if names else f'Синтетический {i:04}'),treatingDoctorUserId=doctor))
            ids.append(store.insert_patient_rows(c,body,doctor,created_by_user_id=None,creation_source='legacy'))
    return ids


def page(client,**params):
    return client.get('/api/patients',params={'view':'page',**params})


def test_legacy_array_and_page_do_not_materialize_registry(auth_client,monkeypatch):
    from backend.app import store
    ids=seed()
    assert len(auth_client.get('/api/patients').json())==65
    monkeypatch.setattr(store,'patient_to_dict',lambda *a:pytest.fail('page must not fetch full clinical cards'))
    r=page(auth_client,include_terminal='true');assert r.status_code==200,r.text
    body=r.json();assert len(body['patients'])==50 and body['page']['total']==65
    assert not any('history' in row or 'phone' in row or 'policyNumber' in row for row in body['patients'])
    next_page=page(auth_client,include_terminal='true',cursor=body['page']['nextCursor']).json()
    assert len(next_page['patients'])==15 and not next_page['page']['hasNext']
    assert {r['id'] for r in body['patients']}|{r['id'] for r in next_page['patients']}==set(ids)
    assert not {r['id'] for r in body['patients']}&{r['id'] for r in next_page['patients']}


def test_exact_page_manifest_and_response_hash(auth_client):
    seed(75)
    response=page(auth_client);assert response.status_code==200
    body=response.json();c=get_conn()
    row=c.execute('SELECT * FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
    assert row['snapshot_sha256']==hashlib.sha256(response.content).hexdigest()
    objects=json.loads(row['objects_json'])
    assert {o['id'] for o in objects if o['type']=='patient'}=={p['id'] for p in body['patients']}
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='medical_read_release'").fetchone()[0]==1


@pytest.mark.parametrize('params',[
    {'limit':'0'},{'limit':'101'},{'limit':'-1'},{'limit':'1e100'},
    {'limit':'9'*5000},{'limit':'1.0'},{'cursor':'broken'},{'cursor':'x'*2049},
    {'sort':'untrusted'},{'q':'x'*201},{'stage':'untrusted'}, {'deadline':'8+'},
    {'include_terminal':'anything'},{'unknown':'value'}, {'attention_reason':'untrusted'},
])
def test_invalid_parameters_controlled(auth_client,params):
    seed(1)
    r=page(auth_client,**params);assert r.status_code in {410,422}
    assert 'Traceback' not in r.text and 'Синтетический' not in r.text


def test_search_beyond_first_page_and_literal_wildcards(auth_client):
    seed(70,names=[f'А Пациент {i:03}' for i in range(69)]+['Я Синтетический 100%_'])
    first=page(auth_client,sort='name').json();assert all(p['name']!='Я Синтетический 100%_' for p in first['patients'])
    for q in ['Я Синтетический','100%_']:
        found=page(auth_client,q=q).json();assert len(found['patients'])==1 and found['page']['total']==1


def test_russian_order_e_yo_unicode_and_stable_ties(auth_client):
    names=['Ёжов Синтетический','Ежов Синтетический','Жуков Синтетический','Е\u0308жов Синтетический','Ежов Синтетический','ёжов Синтетический']
    seed(6,names=names)
    rows=page(auth_client,sort='name').json()['patients']
    import unicodedata
    assert [unicodedata.normalize('NFC',r['name'])[0].lower() for r in rows]==['е','е','ё','ё','ё','ж']
    assert [r['id'] for r in rows[:2]]==sorted(r['id'] for r in rows[:2])
    from backend.app.patient_pages import russian_key
    assert russian_key('Ёжов')==russian_key('Е\u0308жов')
    assert len(page(auth_client,q='Ежов').json()['patients'])==5


def test_changed_dataset_refuses_old_cursor_without_success_manifest(auth_client):
    seed()
    first=page(auth_client).json();before=get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]
    barrier=threading.Barrier(2)
    def change():
        from backend.app import db,config
        c=db._connect(config.DB_PATH)
        try:
            barrier.wait(timeout=10)
            c.execute("UPDATE patients SET name='Обновленная синтетическая карточка',version=version+1 WHERE id=?",(first['patients'][0]['id'],));c.commit()
        finally:c.close()
    with ThreadPoolExecutor(1) as pool:
        job=pool.submit(change);barrier.wait(timeout=10);job.result(timeout=10)
    r=page(auth_client,cursor=first['page']['nextCursor']);assert r.status_code==409 and 'PATIENT_QUERY_CHANGED' in r.text
    assert get_conn().execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]==before
    fresh=page(auth_client).json();assert fresh['page']['total']==65


def test_cursor_bound_to_query_and_key(auth_client,monkeypatch):
    from backend.app import patient_pages
    seed()
    cursor=page(auth_client).json()['page']['nextCursor']
    assert page(auth_client,cursor=cursor,q='Другой запрос').status_code==409
    monkeypatch.setattr(patient_pages,'_KEY',bytes(range(32)))
    assert page(auth_client,cursor=cursor).status_code==410


def test_manifest_write_fault_releases_no_cards(auth_client):
    seed(2);c=get_conn()
    c.executescript("CREATE TRIGGER synthetic_page_audit_fault BEFORE INSERT ON read_access_manifests BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    r=page(auth_client);assert r.status_code==503 and 'Синтетический' not in r.text
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='medical_read_release'").fetchone()[0]==0


def test_fresh_scope_checked_after_serialization(auth_client,monkeypatch):
    from backend.app import patient_pages
    seed(2);original=patient_pages.page
    def change(*args,**kwargs):
        result=original(*args,**kwargs);c=get_conn();c.execute("UPDATE users SET role_id='doctor' WHERE id=?",(args[1].id,));c.commit();return result
    monkeypatch.setattr(patient_pages,'page',change)
    r=page(auth_client);assert r.status_code in {401,409} and 'Синтетический' not in r.text


def test_head_options_actual_source_and_legacy_preserved(auth_client):
    from backend.app import users
    c=get_conn();dept=users.create_department(c,'Синтетическое отделение');head=create_account('wph.head','department_head',full_name='Актуальный Синтетический Заведующий',department_ids=[dept])
    ids=seed(1)
    options=auth_client.get('/api/patients/head-options');assert options.status_code==200
    assert any(h['userId']==head and h['name']=='Актуальный Синтетический Заведующий' for h in options.json()['heads'])
    legacy=auth_client.get('/api/patients/'+ids[0]).json()['head']
    assert legacy!='Актуальный Синтетический Заведующий'
    assert auth_client.get('/api/patients/'+ids[0]).json()['head']==legacy


@pytest.mark.parametrize('role',['doctor','department_head'])
def test_page_scope_before_search_limit_totals_and_facets(client,role):
    from backend.app import users
    from .conftest import TEST_PASSWORD
    c=get_conn();a=users.create_department(c,'Синтетическое свое');b=users.create_department(c,'Синтетическое чужое')
    own_doc=create_account('wph.own','doctor',department_ids=[a]);foreign_doc=create_account('wph.other','doctor',department_ids=[b])
    user=own_doc if role=='doctor' else create_account('wph.manager','department_head',department_ids=[a])
    own=seed(63,doctor=own_doc);foreign=seed(20,doctor=foreign_doc,names=['А ЧУЖОЙ Синтетический '+str(i) for i in range(20)])
    r=client.post('/api/auth/login',json={'username':'wph.own' if role=='doctor' else 'wph.manager','password':TEST_PASSWORD});assert r.status_code==200
    r=page(client,sort='name');assert r.status_code==200
    data=r.json();assert data['page']['total']==data['summary']['total']==63
    assert len(data['patients'])==50 and {p['id'] for p in data['patients']}<=set(own)
    # Changes to an invisible card cannot invalidate a scoped cursor or reveal its count.
    c.execute("UPDATE patients SET name='Измененный ЧУЖОЙ',version=version+1 WHERE id=?",(foreign[0],));c.commit()
    next_page=page(client,sort='name',cursor=data['page']['nextCursor'])
    assert next_page.status_code==200 and next_page.json()['page']['total']==63
    assert {p['id'] for p in next_page.json()['patients']}<=set(own)
    foreign_head=create_account('wph.foreign.head','department_head',full_name='ЧУЖОЙ Синтетический Заведующий',department_ids=[b])
    choices=client.get('/api/patients/head-options')
    assert choices.status_code==200 and foreign_head not in choices.text and 'ЧУЖОЙ' not in choices.text
    assert not any(pid in r.text for pid in foreign) and 'ЧУЖОЙ' not in r.text
    assert page(client,q='ЧУЖОЙ').json()['page']['total']==0
    resolved=client.get('/api/patients/resolve',params={'ids':','.join([own[0],foreign[0]])}).json()
    assert [p['id'] for p in resolved['patients']]==[own[0]]


def test_workspace_calendar_and_candidates_bounded_manifests(auth_client):
    seed(65)
    for kind in ['events','patients']:
        r=auth_client.get('/api/patients/workspace/'+kind);assert r.status_code==200,r.text
        body=r.json();assert len(body[kind])<=50 and body['page']['total']>=65
        manifest=get_conn().execute('SELECT * FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
        assert manifest['snapshot_sha256']==hashlib.sha256(r.content).hexdigest()
        ids={o['id'] for o in json.loads(manifest['objects_json']) if o['type']=='patient'}
        assert ids=={row['patientId'] if kind=='events' else row['id'] for row in body[kind]}
    assert auth_client.get('/api/patients/workspace/checklists').status_code==200


def test_full_filter_export_signed_single_event_and_manifest(auth_client):
    ids=seed(65);p=page(auth_client,include_terminal='true',sort='name').json()
    query={'view':'page','include_terminal':'true','sort':'name','limit':'50'}
    r=auth_client.post('/api/export/selection',json={'query':query,'revision':p['page']['revision']},headers=auth_client.csrf)
    assert r.status_code==200,r.text
    from backend.app import export_signing
    body=r.json();assert {p['id'] for p in body['patients']}==set(ids)
    # Signature validation uses the same existing signed export reader.
    verified=export_signing.verify(body)
    assert verified[0]==export_signing.VERIFIED
    c=get_conn();events=c.execute("SELECT id FROM audit_events WHERE event_type='export'").fetchall();assert len(events)==1
    manifest=c.execute('SELECT * FROM read_access_manifests WHERE audit_event_id=?',(events[0][0],)).fetchone()
    assert manifest is not None and manifest['snapshot_sha256']==hashlib.sha256(r.content).hexdigest()
    assert 'attachment;' in r.headers['content-disposition']
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='medical_read_release'").fetchone()[0]==1  # page only
    seed(1)
    assert auth_client.post('/api/export/selection',json={'query':query,'revision':p['page']['revision']},headers=auth_client.csrf).status_code==409
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='export'").fetchone()[0]==1


@pytest.mark.parametrize('fault',['event','manifest'])
def test_filter_export_audit_fault_atomic_no_file(auth_client,fault):
    seed(2);body=page(auth_client,include_terminal='true',sort='name').json();c=get_conn()
    if fault=='event':c.executescript("CREATE TRIGGER synthetic_export_event_fault BEFORE INSERT ON audit_events WHEN NEW.event_type='export' BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    else:c.executescript("CREATE TRIGGER synthetic_export_manifest_fault BEFORE INSERT ON read_access_manifests BEGIN SELECT RAISE(ABORT,'synthetic'); END;")
    r=auth_client.post('/api/export/selection',json={'query':{'view':'page','include_terminal':'true','sort':'name'},'revision':body['page']['revision']},headers=auth_client.csrf)
    assert r.status_code==503 and 'Синтетический' not in r.text
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='export'").fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM read_access_manifests').fetchone()[0]==1


def test_filter_export_csrf_and_duplicate_parameters(auth_client):
    seed(1);body=page(auth_client).json()
    assert auth_client.post('/api/export/selection',json={'query':{},'revision':body['page']['revision']}).status_code==403
    assert auth_client.get('/api/patients?view=page&limit=50&limit=100').status_code==422
    assert auth_client.get('/api/patients/workspace/events?group=general').status_code==422


def test_all_deadline_filters_use_institution_day(auth_client,monkeypatch):
    from backend.app import clock
    from datetime import datetime
    monkeypatch.setattr(clock,'now_moscow',lambda:datetime.fromisoformat('2026-11-02T12:00:00+03:00'))
    ids=seed(7);dates=['2026-11-01','2026-11-02','2026-11-05','2026-11-06','2026-11-09','2026-11-10',None];c=get_conn()
    for pid,value in zip(ids,dates):c.execute('UPDATE events SET date=?,version=version+1 WHERE history_entry_id IN (SELECT id FROM history_entries WHERE patient_id=?)',(value,pid))
    c.commit()
    for code,indexes in [('overdue',[0]),('0-3',[1,2]),('4-7',[3,4]),('7+',[5]),('no-date',[6])]:
        found=page(auth_client,deadline=code).json()
        assert {r['id'] for r in found['patients']}=={ids[i] for i in indexes}


def test_attention_reason_none_is_reason_specific(auth_client):
    ids=seed(3)
    for pid,reason in zip(ids,['other','organizational_issue']):
        r=auth_client.post('/api/patients/'+pid+'/attention',json={'reasonCode':reason,'comment':'Синтетическое пояснение'},headers=auth_client.csrf);assert r.status_code==201,r.text
    none=page(auth_client,attention='none',attention_reason='other').json()
    assert {r['id'] for r in none['patients']}==set(ids[1:])
    active=page(auth_client,attention='active',attention_reason='other').json()
    assert [r['id'] for r in active['patients']]==[ids[0]]


def test_workload_default_desc_preserves_explicit_asc(auth_client):
    a=create_account('wph.busy','doctor');b=create_account('wph.quiet','doctor')
    seed(9,doctor=a);seed(2,doctor=b)
    params={'date_from':'2000-01-01','date_to':'2099-01-01'}
    desc=auth_client.get('/api/analytics/workload',params=params);assert desc.status_code==200
    assert [r['active_patients'] for r in desc.json()['rows']]==[9,2]
    assert {r['doctor_user_id'] for r in desc.json()['rows']}=={a,b}
    asc=auth_client.get('/api/analytics/workload',params={**params,'order':'asc'})
    assert [r['active_patients'] for r in asc.json()['rows']]==[2,9]


def test_workspace_includes_completed_checklists_without_changing_analytics(auth_client):
    from backend.app.analytics import service
    from backend.app.services import data_quality
    ids=seed(2);c=get_conn()
    required=data_quality.REQUIRED_CHECKLISTS['lung34']['exam']
    assert required
    c.execute("UPDATE history_entries SET stage='exam',checklist_json=? WHERE patient_id=?",(json.dumps(dict.fromkeys(required,True)),ids[0]))
    c.execute("UPDATE history_entries SET stage='exam',checklist_json='{}' WHERE patient_id=?",(ids[1],));c.commit()
    body=auth_client.get('/api/patients/workspace/checklists').json()
    assert body['page']['total']==2
    assert [r['patient_id'] for r in body['checklists']]==[ids[1],ids[0]]
    assert body['checklists'][1]['completed_required']==len(required)
    assert {r['patient_id'] for r in service._checklist_state(c,set(ids))['patients']}=={ids[1]}


def test_filter_export_fresh_permission_before_release(auth_client,monkeypatch):
    from backend.app.routes import importexport
    seed(2);body=page(auth_client,include_terminal='true',sort='name').json()
    original=importexport._export_selected
    def revoked(conn,user,selected):
        conn.execute("UPDATE users SET role_id='doctor' WHERE id=?",(user.id,));conn.commit()
        return original(conn,user,selected)
    monkeypatch.setattr(importexport,'_export_selected',revoked)
    response=auth_client.post('/api/export/selection',json={'query':{'view':'page','include_terminal':'true','sort':'name'},'revision':body['page']['revision']},headers=auth_client.csrf)
    assert response.status_code in {401,403,409}
    assert 'Синтетический' not in response.text and 'signature' not in response.text


def test_explicit_maximum_page_100_and_complete_partition(auth_client):
    ids=seed(125)
    first=page(auth_client,limit='100').json()
    assert len(first['patients'])==100 and first['page']['limit']==100
    second=page(auth_client,limit='100',cursor=first['page']['nextCursor']).json()
    assert len(second['patients'])==25 and second['page']['offset']==100
    received=[p['id'] for p in first['patients']+second['patients']]
    assert len(received)==len(set(received))==125 and set(received)==set(ids)
    assert second['page']['nextCursor'] is None


def test_resolve_preserves_existing_non_uuid_ids(auth_client,monkeypatch):
    from backend.app import store
    original=store.new_id
    first=True
    def synthetic_legacy_id():
        nonlocal first
        if first:
            first=False
            return 'legacy-patient-001'
        return original()
    monkeypatch.setattr(store,'new_id',synthetic_legacy_id)
    ids=seed(1)
    assert ids==['legacy-patient-001']
    response=auth_client.get('/api/patients/resolve',params={'ids':ids[0]})
    assert response.status_code==200
    assert [p['id'] for p in response.json()['patients']]==ids
    assert auth_client.get('/api/patients/resolve',params={'ids':'x'*101}).status_code==422
