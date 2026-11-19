"""Scoped, revision-validated patient queries. Legacy list contract is unchanged.

Only a lightweight projection is scanned; full clinical cards are fetched by ID.
Cursors contain no patient IDs/text and confer no permission. A process restart
invalidates cursors; this is pagination state, never MFA continuity authority.
"""
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
import secrets
import unicodedata
from fastapi import HTTPException
from . import clock, policy, request_security
from .services import patient_attention

_KEY = secrets.token_bytes(32)
_RUSSIAN = {c:i+100 for i,c in enumerate('абвгдеёжзийклмнопрстуфхцчшщъыьэюя')}
SORTS = {'updated-newest','updated-oldest','newest','oldest','attention','first-visit-oldest','first-visit-newest','name'}
STAGES = {'primary-appointment','exam','appointment','consult','treatment-prep','treatment','completed','dispensary-observation','diagnosis-not-confirmed'}
TERMINAL = {'completed','dispensary-observation','diagnosis-not-confirmed'}
DEADLINES = {'overdue','0-3','4-7','7+','no-date'}


def fail(code,status=422):
    raise HTTPException(status,{'code':code,'message':{'PATIENT_QUERY_CHANGED':'Список изменился. Обновите выдачу; фильтры и черновики сохранены.','CURSOR_EXPIRED':'Страница устарела. Начните с первой страницы.'}.get(code,'Некорректные параметры списка пациентов.')})


def fold(text):
    return unicodedata.normalize('NFC',str(text or '')).lower().replace('ё','е')


def russian_key(text):
    # NFC also makes a decomposed е + diaeresis sort as ё. Е precedes Ё.
    return tuple(_RUSSIAN.get(c,ord(c)+1000) if c.isalpha() else ord(c) for c in unicodedata.normalize('NFC',str(text or '')).lower())


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _encode(value):
    body=base64.urlsafe_b64encode(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).rstrip(b'=')
    return body.decode()+'.'+hmac.new(_KEY,body,hashlib.sha256).hexdigest()


def _decode(raw):
    try:
        if not isinstance(raw,str) or len(raw)>2048:raise ValueError()
        body,signature=raw.split('.')
        if not hmac.compare_digest(hmac.new(_KEY,body.encode(),hashlib.sha256).hexdigest(),signature):raise ValueError()
        value=json.loads(base64.b64decode(body+'='*(-len(body)%4),altchars=b'-_',validate=True))
        if set(value)!={'v','query','revision','binding','offset','limit'} or value['v']!=1 or type(value['offset']) is not int or value['offset']<0:raise ValueError()
        return value
    except (ValueError,TypeError,KeyError,UnicodeError):
        fail('CURSOR_EXPIRED',410)


@contextmanager
def snapshot(conn):
    outer=conn.in_transaction
    if not outer:conn.execute('BEGIN')
    try:yield
    finally:
        if not outer:conn.rollback()  # read-only; release snapshot before release-audit write


def projection(conn,user):
    cond,args=policy.patient_scope_filter(user)
    # cond is exclusively the server policy expression, never user input.
    rows=conn.execute(f'''SELECT p.id,p.name,p.age,p.priority,p.stage,p.diagnosis,p.nosology,
        p.arrival,p.updated_at,p.version,p.head,p.doctor,p.policy_number,
        p.treating_doctor_user_id,u.full_name AS treating_name,
        (SELECT e.date FROM history_entries h LEFT JOIN events e ON e.history_entry_id=h.id AND e.deleted_at IS NULL
         WHERE h.patient_id=p.id ORDER BY h.seq DESC,e.seq DESC LIMIT 1) AS deadline,
        (SELECT e.actual_date FROM history_entries h LEFT JOIN events e ON e.history_entry_id=h.id AND e.deleted_at IS NULL
         WHERE h.patient_id=p.id ORDER BY h.seq DESC,e.seq DESC LIMIT 1) AS current_fact,
        (SELECT e.actual_date || 'T' || COALESCE(NULLIF(e.actual_time,''),'00:00') FROM history_entries h JOIN events e ON e.history_entry_id=h.id AND e.deleted_at IS NULL
         WHERE h.patient_id=p.id AND h.stage='primary-appointment' AND e.actual_date IS NOT NULL AND e.actual_date!=''
         ORDER BY h.seq,e.seq LIMIT 1) AS first_visit
        FROM patients p LEFT JOIN users u ON u.id=p.treating_doctor_user_id
        WHERE {cond.replace('patients.', 'p.')} ORDER BY p.id''',args).fetchall()  # nosec B608 - policy-only SQL
    attention=patient_attention.bulk_summary(conn,[r['id'] for r in rows])
    today=clock.today_moscow()
    result=[]
    for r in rows:
        try:days=(date.fromisoformat(r['deadline'])-today).days if r['deadline'] else None
        except ValueError:days=None
        result.append({'id':r['id'],'name':r['name'],'age':r['age'] or 0,'priority':r['priority'],
            'stage':r['stage'],'diagnosis':r['diagnosis'] or '', 'nosology':r['nosology'] or '',
            'arrival':r['arrival'],'updatedAt':r['updated_at'],'version':r['version'],'head':r['head'] or '',
            'doctor':r['doctor'] or '', 'treatingDoctorName':r['treating_name'], 'treatingDoctorUserId':r['treating_doctor_user_id'],
            'attention':attention.get(r['id'],{'active':False,'activeCount':0,'primaryReasonCode':None,'primaryReasonLabel':''}),
            'deadlineDays':days,'firstVisitAt':r['first_visit'],'currentFact':bool(r['current_fact']),
            '_searchPolicy':r['policy_number'] or ''})
    return result


def normalized(params):
    allowed={'view','q','stage','head','deadline','sort','attention','attention_reason','include_terminal','limit','cursor'}
    if set(params)-allowed:fail('PATIENT_QUERY_INVALID')
    p={k:params.get(k,'') for k in ['q','stage','head','deadline','attention','attention_reason']}
    p['sort']=params.get('sort','updated-newest');p['include_terminal']=params.get('include_terminal','false')
    if any(not isinstance(v,str) for v in p.values()) or len(p['q'])>200 or len(p['head'])>200:fail('PATIENT_QUERY_INVALID')
    if p['sort'] not in SORTS or p['stage'] and p['stage'] not in STAGES or p['deadline'] and p['deadline'] not in DEADLINES:fail('PATIENT_QUERY_INVALID')
    if p['attention'] not in {'','active','none'} or p['include_terminal'] not in {'false','true'}:fail('PATIENT_QUERY_INVALID')
    if p['attention_reason'] and p['attention_reason'] not in patient_attention.REASON_LABELS:fail('PATIENT_QUERY_INVALID')
    return p


def timestamp(value):
    try:
        moment=datetime.fromisoformat(value)
        if moment.tzinfo is None:moment=moment.replace(tzinfo=clock.institution_tz())
        return moment.timestamp()
    except (ValueError,TypeError,OverflowError):return None


def filtered(rows,p,conn=None):
    q=fold(p['q']);result=[]
    reason_ids=None
    if p['attention_reason']:
        reason_ids=patient_attention.active_patient_ids(conn,{r['id'] for r in rows},p['attention_reason'])
    for r in rows:
        if p['include_terminal']=='false' and not p['stage'] and not q and r['stage'] in TERMINAL:continue
        if p['stage'] and r['stage']!=p['stage'] or p['head'] and r['head']!=p['head']:continue
        if q and not any(q in fold(r[k]) for k in ['name','_searchPolicy','diagnosis','doctor','head']):continue
        active=r['id'] in reason_ids if reason_ids is not None else r['attention']['active']
        if (p['attention']=='active' or p['attention_reason']) and p['attention']!='none' and not active:continue
        if p['attention']=='none' and active:continue
        days=r['deadlineDays'];d=p['deadline']
        if d and not {'no-date':days is None,'overdue':days is not None and days<0,'0-3':days is not None and 0<=days<=3,'4-7':days is not None and 4<=days<=7,'7+':days is not None and days>7}[d]:continue
        result.append(r)
    # Stable secondary name/id for ties, then stable primary sort.
    result.sort(key=lambda r:(russian_key(r['name']),r['id']))
    sort=p['sort']
    if sort!='name':
        if sort=='attention':key=lambda r:r['attention']['activeCount']
        elif sort.startswith('first-visit'):key=lambda r:timestamp(r['firstVisitAt']) if timestamp(r['firstVisitAt']) is not None else (float('inf') if sort.endswith('oldest') else float('-inf'))
        elif sort.startswith('updated'):key=lambda r:timestamp(r['updatedAt'] or r['arrival']) or 0
        else:key=lambda r:timestamp(r['arrival']) or 0
        result.sort(key=key,reverse=sort in {'attention','updated-newest','newest','first-visit-newest'})
    return result


@dataclass(frozen=True)
class ScopedRelease:
    kind: str
    params: dict
    expected: str


class QueryChanged(Exception):
    """Ordinary scoped dataset change; never an identity/access refusal."""


def _remember(kind, params, fingerprint):
    state = request_security.current.get()
    if state is not None:
        state.scoped_release = ScopedRelease(kind, dict(params), fingerprint)


def _summary(rows):
    week=clock.current_moscow()-timedelta(days=7)
    def is_new(row):
        try:return datetime.fromisoformat(row['arrival']).astimezone(clock.institution_tz())>=week
        except (ValueError,TypeError):return False
    return {'total':len(rows),'fresh':sum(is_new(r) for r in rows),
            'inTreatment':sum(r['stage'] in {'treatment','treatment-prep'} for r in rows),
            'done':sum(r['stage']=='completed' for r in rows),
            'unresolved':sum(not r['treatingDoctorUserId'] for r in rows)}


def _page_fingerprint(rows, selected):
    # Includes the WHOLE scope: filtered revision alone omits summary/facets.
    return digest({'day': clock.today_moscow().isoformat(), 'scope': rows,
                   'selected': [r['id'] for r in selected], 'summary': _summary(rows)})


def _workspace_fingerprint(conn, user, rows):
    cond, args = policy.patient_scope_filter(user)
    ids = [r[0] for r in conn.execute('SELECT id FROM patients WHERE '+cond+' ORDER BY id', args)]  # nosec B608 - server policy only
    return digest({'day': clock.today_moscow().isoformat(), 'scope': ids, 'rows': rows})


@clock.consistent
def validate_release(conn, user, check):
    # Called on the release connection, in a read snapshot OUTSIDE the audit
    # write lock. No retained connection, medical rows, or trust sidecar.
    with snapshot(conn):
        if check.kind == 'page':
            rows = projection(conn, user)
            selected = filtered(rows, normalized(check.params), conn)
            actual = _page_fingerprint(rows, selected)
        else:
            rows = _workspace_rows(conn, user, check.params, check.kind)
            actual = _workspace_fingerprint(conn, user, rows)
    if not hmac.compare_digest(actual, check.expected):
        raise QueryChanged()


def public(row):
    return {k:v for k,v in row.items() if not k.startswith('_')}


@clock.consistent
def page(conn,user,params,binding):
    p=normalized(params)
    try:
        raw=params.get('limit','50')
        if len(str(raw))>3 or isinstance(raw,bool) or not str(raw).isascii() or not str(raw).isdigit():raise ValueError()
        limit=int(raw)
        if not 1<=limit<=100:raise ValueError()
    except (ValueError,TypeError):fail('PATIENT_LIMIT_INVALID')
    cursor=_decode(params['cursor']) if params.get('cursor') else None
    query=digest(p)
    if cursor and (cursor['query']!=query or cursor['limit']!=limit or cursor['binding']!=binding):fail('PATIENT_QUERY_CHANGED',409)
    with snapshot(conn):
        rows=projection(conn,user);selected=filtered(rows,p,conn)
        revision=digest({'day':clock.today_moscow().isoformat(),'rows':selected})
        if cursor and cursor['revision']!=revision:fail('PATIENT_QUERY_CHANGED',409)
        offset=cursor['offset'] if cursor else 0
        if offset>len(selected):fail('PATIENT_QUERY_CHANGED',409)
        next_offset=offset+limit
        token=_encode({'v':1,'query':query,'revision':revision,'binding':binding,'offset':next_offset,'limit':limit}) if next_offset<len(selected) else None
        stats=_summary(rows)
        _remember('page', params, _page_fingerprint(rows, selected))
        return {'patients':[public(r) for r in selected[offset:next_offset]],'page':{'limit':limit,'offset':offset,'total':len(selected),'hasNext':token is not None,'nextCursor':token,'revision':revision},
            'summary':stats,'heads':sorted({r['head'] for r in rows if r['head']},key=russian_key)}


def slice_rows(rows,params,binding,kind,query):
    try:
        raw=params.get('limit','50')
        if not str(raw).isascii() or not str(raw).isdigit() or len(str(raw))>3:raise ValueError()
        limit=int(raw)
        if not 1<=limit<=100:raise ValueError()
    except (TypeError,ValueError):fail('PATIENT_LIMIT_INVALID')
    revision=digest({'day':clock.today_moscow().isoformat(),'rows':rows})
    q=digest({'kind':kind,'query':query})
    cursor=_decode(params['cursor']) if params.get('cursor') else None
    if cursor and (cursor['query']!=q or cursor['revision']!=revision or cursor['binding']!=binding or cursor['limit']!=limit):fail('PATIENT_QUERY_CHANGED',409)
    offset=cursor['offset'] if cursor else 0
    if offset>len(rows):fail('PATIENT_QUERY_CHANGED',409)
    end=offset+limit
    token=_encode({'v':1,'query':q,'revision':revision,'binding':binding,'offset':end,'limit':limit}) if end<len(rows) else None
    return {kind:rows[offset:end],'page':{'offset':offset,'limit':limit,'total':len(rows),'hasNext':bool(token),'nextCursor':token,'revision':revision}}


@clock.consistent
def workspace_rows(conn,user,params,binding,kind):
    if set(params)-{'limit','cursor','q','group'}:fail('PATIENT_QUERY_INVALID')
    if kind!='patients' and 'group' in params:fail('PATIENT_QUERY_INVALID')
    q=params.get('q','');group=params.get('group','')
    if len(q)>200 or len(group)>100:fail('PATIENT_QUERY_INVALID')
    with snapshot(conn):
        rows = _workspace_rows(conn,user,params,kind)
        result = slice_rows(rows,params,binding,kind,{'q':q,'group':group})
        _remember(kind, params, _workspace_fingerprint(conn,user,rows))
        return result


def _workspace_rows(conn,user,params,kind):
    q=params.get('q','');group=params.get('group','')
    cond,args=policy.patient_scope_filter(user)
    if kind=='events':
        rows=[dict(r) for r in conn.execute('''SELECT e.id,p.id AS patientId,p.name AS patientName,
            p.head,h.id AS historyEntryId,h.stage,e.date,e.time FROM events e
            JOIN history_entries h ON h.id=e.history_entry_id JOIN patients p ON p.id=h.patient_id
            WHERE e.deleted_at IS NULL AND e.date IS NOT NULL AND e.date!='' AND (e.actual_date IS NULL OR e.actual_date='') AND '''+cond.replace('patients.','p.')+''' ORDER BY e.date,e.time,p.id,h.seq,e.seq,e.id''',args)]  # nosec B608 - server policy only
    elif kind=='checklists':
        from .analytics.service import _checklist_state
        ids={r[0] for r in conn.execute('SELECT id FROM patients WHERE '+cond,args)}  # nosec B608 - server policy only
        rows=_checklist_state(conn,ids,include_completed=True)['patients']
        rows.sort(key=lambda r:(r['completed_required']/r['total_required'] if r['total_required'] else 1,russian_key(r['patient_name']),r['patient_id']))
    else:
        from .services import consilium
        if not policy.can_book_consilium(user):fail('PATIENT_CANDIDATES_FORBIDDEN',403)
        if group and group not in consilium.GROUP_LABELS:fail('PATIENT_QUERY_INVALID')
        blocked=set(consilium.booked_patient_ids(conn,user))
        rows=[public(r) for r in projection(conn,user) if r['id'] not in blocked and (not group or consilium.group_for_diagnosis(r['diagnosis'])==group)]
        rows.sort(key=lambda r:(russian_key(r['name']),r['id']))
    if q:rows=[r for r in rows if fold(q) in fold(r.get('patientName',r.get('patient_name',r.get('name',''))))]
    return rows
