"""Synthetic-only localhost benchmark; no app source or real DB modifications."""
import argparse,contextvars,hashlib,json,os,sys,tempfile,time,threading
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--count',type=int,required=True);p.add_argument('--port',type=int,required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root))
with tempfile.TemporaryDirectory(prefix='clinic-wph-baseline-') as td:
 import base64,secrets
 os.environ.update(CLINIC_EXPORT_SIGNING_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode(),CLINIC_EXPORT_SIGNING_KEY_ID='synthetic-wph',CLINIC_DATA_DIR=td,CLINIC_ENV='test',CLINIC_DEBUG='false',NOTIFICATION_PROVIDER='mock',CLINIC_AI_MODE='disabled')
 from backend.app import db,users,store,operations_state,security
 from backend.app.models import PatientCreateIn
 operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
 c=db.get_connection();password='Synthetic-WPH-Research-991!'
 def account(name,role,full):return users.create_user(c,username=name,full_name=full,role_id=role,password=password,must_change_password=False)
 dev=account('wph.dev','developer','Синтетический Разработчик');head=account('wph.head','department_head','Синтетический Заведующий')
 dept=users.create_department(c,'Синтетическое отделение');other=users.create_department(c,'Другое синтетическое отделение')
 docs=[account('wph.doc'+str(i),'doctor','Синтетический Врач '+str(i)) for i in range(10)]
 hidden=account('wph.hidden','doctor','Другой Синтетический Врач')
 for uid in docs:users.add_doctor_to_department(c,uid,dept)
 users.add_doctor_to_department(c,hidden,other)
 c.execute('INSERT INTO department_heads VALUES(?,?,?)',(head,dept,users.now_iso()));c.commit()
 own=args.count//5;visible=args.count*4//5;assignments=[docs[0]]*own
 weights=[max(1,(visible-own)*i//45) for i in range(1,10)]
 for i,n in enumerate(weights,1):assignments.extend([docs[i]]*n)
 assignments=assignments[:visible]
 assignments.extend([docs[-1]]*(visible-len(assignments)));assignments.extend([hidden]*(args.count-len(assignments)))
 started=time.perf_counter()
 with c:
  for i,uid in enumerate(assignments):
   data=PatientCreateIn(name=f'Синтетический Пациент {i:05}',age=45,priority='normal',stage='exam',diagnosis='C34.1',nosology='lung34',head='Синтетический Заведующий',events=[{'date':'2026-12-01','time':'10:00'}])
   store.insert_patient_rows(c,data,uid,created_by_user_id=dev,creation_source='form')
 print(json.dumps({'seeded':args.count,'seconds':time.perf_counter()-started,'synthetic_only':True}),flush=True)
 c.close();db.reset_singleton_for_tests()
 from backend.app.main import app
 from fastapi import Request
 samples=[];lock=threading.Lock();bucket=contextvars.ContextVar('research_sql',default=None);original=db._connect
 from backend.app import patient_pages,read_access,policy
 faults={'mode':'none','serial':0}
 @app.post('/synthetic/wph/release-race')
 def arm_release_race(body:dict):
  assert body.get('mode') in {'none','once','always','technical-once','technical-always'}
  with lock:faults['mode']=body['mode']
  return {'syntheticOnly':True}
 original_page=patient_pages.page
 def racing_page(*a,**kw):
  result=original_page(*a,**kw)
  with lock:
   mode=faults['mode'];faults['serial']+=1;serial=faults['serial']
   if mode=='once':faults['mode']='none'
  if mode in {'once','always'}:
   cond,scope_args=policy.patient_scope_filter(a[1])
   pid=a[0].execute('SELECT id FROM patients WHERE '+cond+' ORDER BY id DESC LIMIT 1',scope_args).fetchone()[0]
   with a[0]:a[0].execute('UPDATE patients SET head=?,version=version+1 WHERE id=?',('Синтетическая гонка '+str(serial),pid))
  return result
 patient_pages.page=racing_page
 original_validation=getattr(patient_pages,'validate_release',None)
 if original_validation is not None:
  def timed_validation(*a,**kw):
   start=time.perf_counter()
   try:
    result=original_validation(*a,**kw)
    with lock:
     mode=faults['mode']
     if mode=='technical-once':faults['mode']='none'
    if mode in {'technical-once','technical-always'}:
     before=a[0].execute('PRAGMA data_version').fetchone()[0]
     # Same technical table written by auth/activity; no patient/scope change.
     other_conn=original(Path(td)/'clinic.db')
     try:
      with other_conn:other_conn.execute('UPDATE sessions SET last_seen_at=?',(security.utc_now().isoformat(),))
     finally:other_conn.close()
     x=bucket.get()
     if x is not None:x['technical_commit']={'table':'sessions','before':before,'after':a[0].execute('PRAGMA data_version').fetchone()[0]}
    return result
   finally:
    x=bucket.get()
    if x is not None:x['validation_seconds']=x.get('validation_seconds',0)+time.perf_counter()-start
  patient_pages.validate_release=timed_validation
 original_persist=read_access._persist
 def timed_persist(*a,**kw):
  x=bucket.get();start=time.perf_counter()
  if x is not None:x['_release']=True
  try:return original_persist(*a,**kw)
  finally:
   if x is not None:x['release_seconds']=time.perf_counter()-start;x['_release']=False
 read_access._persist=timed_persist
 def trace(sql):
  x=bucket.get()
  if x is not None:
   x['sql']+=1
   text=sql.strip().upper()
   if x.get('_release') and text=='BEGIN IMMEDIATE':x['_write_start']=time.perf_counter()
   if text in {'COMMIT','ROLLBACK'} and '_write_start' in x:
    x['audit_write_seconds']=x.get('audit_write_seconds',0)+time.perf_counter()-x.pop('_write_start')
   if sql.lstrip().upper().startswith(('SELECT','WITH')):x['select']+=1
 measured={}
 def connect(*a,**kw):
  parent=kw.get('factory',db._ClinicConnection)
  if parent not in measured:
   class Measured(parent):
    def __init__(self,*args,**kwargs):
     super().__init__(*args,**kwargs);self.set_trace_callback(trace)
   measured[parent]=Measured
  kw['factory']=measured[parent]
  return original(*a,**kw)
 db._connect=connect
 # deps imported _connect directly; instrument that alias as well.
 from backend.app import deps
 deps._connect=connect
 @app.get('/synthetic/wph/metrics')
 def metrics():
  con=original(Path(td)/'clinic.db')
  try:
   row=con.execute('SELECT COUNT(*),COALESCE(SUM(length(objects_json)),0),COALESCE(SUM(object_count),0) FROM read_access_manifests').fetchone()
   return {'samples':samples,'audit':{'manifests':row[0],'json_bytes':row[1],'objects':row[2]}}
  finally:con.close()
 class Measure:
  def __init__(self,inner):self.inner=inner
  async def __call__(self,scope,receive,send):
   if scope['type']!='http' or scope['path']!='/api/patients' or scope['method']!='GET':return await self.inner(scope,receive,send)
   x={'sql':0,'select':0,'bytes':0,'sha256':None,'status':None};h=hashlib.sha256();start=time.perf_counter();token=bucket.set(x)
   async def sent(message):
    if message['type']=='http.response.start':x['status']=message['status']
    if message['type']=='http.response.body':body=message.get('body',b'');x['bytes']+=len(body);h.update(body)
    await send(message)
   try:await self.inner(scope,receive,sent)
   finally:
    bucket.reset(token);x['seconds']=time.perf_counter()-start;x['sha256']=h.hexdigest()
    con=original(Path(td)/'clinic.db')
    try:
     row=con.execute('SELECT object_count,length(objects_json),snapshot_sha256 FROM read_access_manifests WHERE snapshot_sha256=? ORDER BY audit_event_id DESC LIMIT 1',(x['sha256'],)).fetchone()
     if row:x['audit_objects']=row[0];x['audit_json_bytes']=row[1];x['audit_hash_matches']=row[2]==x['sha256']
    finally:con.close()
    with lock:samples.append(x)
 import uvicorn
 uvicorn.run(Measure(app),host='127.0.0.1',port=args.port,log_level='info')
