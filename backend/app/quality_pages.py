"""Quality v1 server foundation. Public reads remain unavailable until A2.

No environment switch, UI flag or query parameter can activate an incomplete
response. prepare() is an internal building block, not a public route provider.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Callable
from urllib.parse import unquote_to_bytes

from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response

from . import clock, operations_state, patient_pages, policy, request_security, security, session_context
from .quality_models import ResponseDTO
from .services import data_quality

MODE = 'quality-v1'
ROOT_TTL = 900
MAX_BODY_BYTES = 1024 * 1024
_MAX_TOKEN = 8192
_MAX_PAYLOAD = 4096
_MAX_INTEGER = 2**63-1
_KEY = secrets.token_bytes(32)  # independent, process-local, never export/session/witness key
_ADAPTER = TypeAdapter(ResponseDTO)
_HEX = re.compile(r'^[a-f0-9]{64}$')
_VIEWS = {'page', 'groups', 'members', 'facets', 'patient_issues'}
_ALLOWED = {'mode', 'view', 'severity', 'category', 'code', 'patient_id', 'doctor_user_id',
            'department_id', 'sort', 'limit', 'cursor', 'snapshot', 'group_id', 'facet', 'owner_patient_id'}
_MESSAGES = {
    'QUALITY_QUERY_AMBIGUOUS': 'Ambiguous quality query',
    'QUALITY_QUERY_INVALID': 'Invalid quality query',
    'CURSOR_EXPIRED': 'Quality cursor expired',
    'PATIENT_QUERY_CHANGED': 'Patient list changed',
    'QUALITY_NOT_AVAILABLE': 'Quality v1 is not available',
    'QUALITY_READ_UNAVAILABLE': 'Quality read unavailable',
}


class QualityError(HTTPException):
    def __init__(self, code, status):
        self.code = code
        super().__init__(status, _MESSAGES[code])


def error(code='QUALITY_QUERY_INVALID', status=422):
    # Only constants; never reflect a request, Pydantic input, SQL or exception.
    raise QualityError(code, status)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _mac(purpose: str, raw: bytes) -> str:
    return hmac.new(_KEY, b'clinic:quality-v1:' + purpose.encode('ascii') + b'\0' + raw, hashlib.sha256).hexdigest()


def _now() -> int:
    return int(clock.now_moscow().timestamp())


@dataclass(frozen=True)
class BaseQuery:
    severity: str | None = None
    category: str | None = None
    code: str | None = None
    patient_id: str | None = None
    doctor_user_id: str | None = None
    department_id: str | None = None
    sort: str = 'severity'
    search: str | None = None

    def digest(self) -> str:
        return _mac('query', _canonical(asdict(self)))

    def echo(self) -> dict:
        values = asdict(self)
        return {'sort': self.sort, 'has_search': bool(self.search),
                'active_filters': [k for k, v in values.items() if k != 'sort' and v is not None]}


@dataclass(frozen=True)
class Selector:
    view: str = 'page'
    group_id: str | None = None
    facet: str | None = None
    owner_patient_id: str | None = None
    facet_search: str | None = None

    def digest(self) -> str:
        return _mac('selector', _canonical(asdict(self)))

    def kind(self) -> str:
        return {'page': 'patients', 'facets': self.facet}.get(self.view, self.view)


@dataclass(frozen=True)
class Normalized:
    query: BaseQuery
    selector: Selector
    limit: int = 50
    snapshot: str | None = None
    cursor: str | None = None


def _search(request: Request, header: str) -> str | None:
    values = request.headers.getlist(header)
    if len(values) > 1:
        error('QUALITY_QUERY_AMBIGUOUS', 400)
    if not values:
        return None
    raw = values[0]
    if len(raw) > 512 or not raw.isascii() or re.search(r'%(?![a-fA-F0-9]{2})', raw):
        error()
    try:
        value = unquote_to_bytes(raw).decode('utf-8', errors='strict').strip()
    except UnicodeError:
        error()
    if len(value) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        error()
    return value.lower() or None


def normalize(request: Request) -> Normalized:
    pairs = list(request.query_params.multi_items())
    if len(pairs) != len({k for k, _ in pairs}):
        error('QUALITY_QUERY_AMBIGUOUS', 400)
    p = dict(pairs)
    if set(p) - _ALLOWED or p.get('mode') != MODE:
        error()
    view = p.get('view', 'page')
    if view not in _VIEWS:
        error()
    enums = {'severity': data_quality.SEVERITIES, 'category': data_quality.QUALITY_CATEGORIES,
             'code': data_quality.QUALITY_RULE_CODES, 'sort': ('severity', 'issues', 'completeness')}
    for key, allowed in enums.items():
        if key in p and p[key] not in allowed:
            error()
    for key in ('patient_id', 'doctor_user_id', 'department_id', 'owner_patient_id'):
        if key in p and (not 1 <= len(p[key]) <= 100 or any(ord(c) < 32 or ord(c) == 127 for c in p[key])):
            error()
    raw = p.get('limit', '50')
    if not re.fullmatch(r'[0-9]{1,3}', raw) or not 1 <= int(raw) <= 100:
        error()
    for key in ('snapshot', 'cursor'):
        if key in p and (not p[key] or len(p[key]) > _MAX_TOKEN or not p[key].isascii()):
            error()
    if 'cursor' in p and 'snapshot' not in p:
        error()
    if ('group_id' in p) != (view == 'members') or 'group_id' in p and not _HEX.fullmatch(p['group_id']):
        error()
    if ('facet' in p) != (view == 'facets') or 'facet' in p and p['facet'] not in {'doctors', 'departments'}:
        error()
    if view == 'patient_issues' and 'owner_patient_id' not in p:
        error()
    if 'owner_patient_id' in p and not (view in {'groups', 'patient_issues'} or view == 'facets' and p.get('facet') == 'departments'):
        error()
    facet_search = _search(request, 'X-Quality-Facet-Search')
    if facet_search is not None and view != 'facets':
        error()
    query = BaseQuery(**{k: p.get(k) for k in ('severity', 'category', 'code', 'patient_id', 'doctor_user_id', 'department_id')},
                      sort=p.get('sort', 'severity'), search=_search(request, 'X-Search'))
    return Normalized(query, Selector(view, p.get('group_id'), p.get('facet'), p.get('owner_patient_id'), facet_search),
                      int(raw), p.get('snapshot'), p.get('cursor'))


def reject_public_mode(request: Request) -> None:
    normalize(request)
    # Deliberate unconditional fail-closed. A2 must replace this in a reviewed
    # code change; there is no runtime activation flag or test-only bypass.
    error('QUALITY_NOT_AVAILABLE', 503)


def _seal(payload: dict, purpose: str) -> str:
    raw = _canonical(payload)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii') + '.' + _mac(purpose, raw)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('nonfinite value')


def _open(raw: str, purpose: str) -> dict:
    try:
        if not isinstance(raw, str) or len(raw) > _MAX_TOKEN or not raw.isascii():
            raise ValueError()
        encoded, signature = raw.split('.')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded) or not _HEX.fullmatch(signature):
            raise ValueError()
        body = base64.b64decode(encoded + '='*(-len(encoded) % 4), altchars=b'-_', validate=True)
        if len(body) > _MAX_PAYLOAD or base64.urlsafe_b64encode(body).rstrip(b'=').decode() != encoded:
            raise ValueError()
        if not hmac.compare_digest(_mac(purpose, body), signature):
            raise ValueError()
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
        keys = {'v', 'api', 'kind', 'q', 'r', 'b', 'iat', 'exp'}
        if purpose == 'cursor':
            keys |= {'s', 'o', 'l'}
        if not isinstance(value, dict) or set(value) != keys or _canonical(value) != body:
            raise ValueError()
        if type(value['v']) is not int or value['v'] != 1 or value['api'] != MODE:
            raise ValueError()
        if purpose == 'snapshot' and value['kind'] != 'snapshot' or purpose == 'cursor' and value['kind'] not in {'patients', 'groups', 'members', 'doctors', 'departments', 'patient_issues'}:
            raise ValueError()
        for key in ('q', 'r', 'b') + (('s',) if purpose == 'cursor' else ()):
            if not isinstance(value[key], str) or not _HEX.fullmatch(value[key]):
                raise ValueError()
        for key in ('iat', 'exp') + (('o', 'l') if purpose == 'cursor' else ()):
            if type(value[key]) is not int or not 0 <= value[key] <= _MAX_INTEGER:
                raise ValueError()
        if value['exp']-value['iat'] != ROOT_TTL or purpose == 'cursor' and not 1 <= value['l'] <= 100:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        error('CURSOR_EXPIRED', 410)


def _binding(user, state) -> str:
    if state is None or not state.token or state.user is None:
        raise HTTPException(401, 'Not authenticated')
    if state.user.id != user.id or state.access != session_context.access_context(user):
        raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
    permit = operations_state.require_permit()
    return _mac('binding', _canonical({'user': user.id, 'session': security.hash_token(state.token),
                                      'access': session_context.access_context(user),
                                      'generation': permit['generation'], 'database': permit['database']}))


def _check_claims(claims: dict, binding: str, query: str):
    if not hmac.compare_digest(claims['b'], binding):
        raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
    if not claims['iat'] <= _now() <= claims['exp']:
        error('CURSOR_EXPIRED', 410)
    if not hmac.compare_digest(claims['q'], query):
        error('PATIENT_QUERY_CHANGED', 409)


@dataclass(frozen=True)
class QualityInput:
    revision: str
    patients: tuple[str, ...]
    doctors: frozenset[str]
    departments: frozenset[str]
    day: str


def _input(conn: sqlite3.Connection, user, binding: str) -> QualityInput:
    """Stream every relevant source record in one caller-owned read snapshot.

    No per-patient version/count shortcut. No medical content is retained in the
    release record. SQL expressions are generated exclusively by server policy.
    """
    cond, args = policy.patient_scope_filter(user)
    patients, doctors, departments = [], set(), set()
    digest = hmac.new(_KEY, b'clinic:quality-v1:revision\0', hashlib.sha256)
    def record(kind, value):
        raw = _canonical({'kind': kind, 'value': value})
        digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
    day = clock.today_moscow().isoformat()
    record('context', {'binding': binding, 'day': day, 'rules': data_quality.QUALITY_RULES,
                       'fields': data_quality.COMPLETENESS_FIELDS, 'required': data_quality.REQUIRED_CHECKLISTS,
                       'stages': data_quality.STAGE_ORDER})
    rows = conn.execute('''SELECT patients.id,name,age,phone,policy_number,diagnosis,nosology,stage,
                           priority,arrival,head,doctor,treating_doctor_user_id,version
                           FROM patients WHERE '''+cond+' ORDER BY patients.id', args)  # nosec B608 - policy-only expression
    for row in rows:
        patients.append(row['id']); record('patient', dict(row))
    rows = conn.execute('''SELECT h.id,h.patient_id,h.seq,h.stage,h.entered_at,h.checklist_json
        FROM history_entries h JOIN patients ON patients.id=h.patient_id WHERE '''+cond+' ORDER BY h.id', args)  # nosec B608 - policy-only expression
    for row in rows:
        record('history', dict(row))
    rows = conn.execute('''SELECT e.id,e.history_entry_id,e.seq,e.date,e.time,e.actual_date,e.actual_time,
        e.no_plan,e.set_at,e.deleted_at,e.version,e.matched_plan FROM events e
        JOIN history_entries h ON h.id=e.history_entry_id JOIN patients ON patients.id=h.patient_id
        WHERE '''+cond+' ORDER BY e.id', args)  # nosec B608 - policy-only expression
    for row in rows:
        record('event', dict(row))
    related = 'SELECT DISTINCT treating_doctor_user_id FROM patients WHERE '+cond
    rows = conn.execute('SELECT id,full_name,role_id,status FROM users WHERE id IN ('+related+') ORDER BY id', args)  # nosec B608 - policy-only expression
    for row in rows:
        doctors.add(row['id']); record('doctor', dict(row))
    rows = conn.execute('''SELECT ud.user_id,ud.department_id,d.name,d.status FROM user_departments ud
        JOIN departments d ON d.id=ud.department_id WHERE ud.user_id IN ('''+related+') ORDER BY ud.user_id,ud.department_id', args)  # nosec B608 - policy-only expression
    for row in rows:
        if row['status'] == 'active':
            departments.add(row['department_id'])
        record('department_link', dict(row))
    return QualityInput(digest.hexdigest(), tuple(patients), frozenset(doctors), frozenset(departments), day)


@dataclass(frozen=True)
class QualityScopedRelease:
    binding: str
    expected: str
    day: str
    issued_at: int
    expires_at: int
    kind: str = MODE


@dataclass(frozen=True)
class QualityContext:
    parsed: Normalized
    source: QualityInput
    quality: dict
    root: dict
    root_token: str
    offset: int
    computed_at: str

    def snapshot(self):
        return {'token': self.root_token, 'revision': self.source.revision, 'query_hash': self.root['q'],
                'computed_at': self.computed_at, 'institution_day': self.source.day,
                'timezone': clock.INSTITUTION_TIMEZONE,
                'expires_at': datetime.fromtimestamp(self.root['exp'], timezone.utc).isoformat()}

    def cursor_for(self, selector: Selector, offset: int, limit: int | None = None):
        size = self.parsed.limit if limit is None else limit
        if type(offset) is not int or not 0 <= offset <= _MAX_INTEGER or type(size) is not int or not 1 <= size <= 100:
            error()
        claims = {**self.root, 'kind': selector.kind(), 's': selector.digest(), 'o': offset, 'l': size}
        return _seal(claims, 'cursor')

    def group_id(self, source_group: dict):
        return _mac('group', _canonical({'binding': self.root['b'], 'members': sorted(source_group['patient_ids']),
                                        'reasons': source_group['reason_codes']}))


@clock.consistent
def prepare(conn: sqlite3.Connection, user, parsed: Normalized, builder: Callable[[QualityContext], dict]) -> Response:
    """Internal A1 integration seam. A2 must supply reviewed complete builders.

    There is intentionally no production route that accepts a builder/provider.
    All synthetic HTTP adapters live in tests or external evidence tooling.
    """
    state = request_security.current.get()
    binding = _binding(user, state); q = parsed.query.digest()
    root = _open(parsed.snapshot, 'snapshot') if parsed.snapshot else None
    cursor = _open(parsed.cursor, 'cursor') if parsed.cursor else None
    for claims in (root, cursor):
        if claims is not None:
            _check_claims(claims, binding, q)
    if cursor is not None:
        if root is None or any(root[k] != cursor[k] for k in ('q', 'r', 'b', 'iat', 'exp')) or cursor['kind'] != parsed.selector.kind() or cursor['s'] != parsed.selector.digest() or cursor['l'] != parsed.limit:
            error('PATIENT_QUERY_CHANGED', 409)
    try:
        with patient_pages.snapshot(conn):
            source = _input(conn, user, binding)
            if root is not None and not hmac.compare_digest(source.revision, root['r']):
                error('PATIENT_QUERY_CHANGED', 409)
            if parsed.selector.owner_patient_id is not None and parsed.selector.owner_patient_id not in source.patients:
                raise HTTPException(404, 'Not found')
            quality = data_quality.evaluate(conn, user, identity_signals=True)
            if root is None:
                issued = _now()
                root = {'v': 1, 'api': MODE, 'kind': 'snapshot', 'q': q, 'r': source.revision,
                        'b': binding, 'iat': issued, 'exp': issued+ROOT_TTL}
            context = QualityContext(parsed, source, quality, root, parsed.snapshot or _seal(root, 'snapshot'),
                                     cursor['o'] if cursor else 0, clock.current_moscow().isoformat())
            groups = {context.group_id(group): group for group in quality['duplicate_groups']}
            if parsed.selector.group_id is not None and parsed.selector.group_id not in groups:
                raise HTTPException(404, 'Not found')
            supplied = builder(context)
            common = {'mode': MODE, 'view': parsed.selector.view, 'snapshot': context.snapshot(),
                      'query': parsed.query.echo(),
                      'scope_summary': {k: v for k, v in quality['summary'].items() if k != 'computed_at'}}
            if not isinstance(supplied, dict) or set(supplied) & set(common):
                error('QUALITY_READ_UNAVAILABLE', 503)
            model = _ADAPTER.validate_python({**supplied, **common}, strict=True)
            payload = model.model_dump(mode='json')
            _validate_source_response(payload, context, groups)
            raw = _canonical(payload)
            if len(raw) > MAX_BODY_BYTES:
                error('QUALITY_READ_UNAVAILABLE', 503)
            # Only technical IDs/digests retained until the coordinator commits.
            request_security.aggregate_patients(source.patients, resources=('/api/data-quality/',))
            if state.scoped_release is not None:
                error('QUALITY_READ_UNAVAILABLE', 503)
            state.scoped_release = QualityScopedRelease(binding, source.revision, source.day, root['iat'], root['exp'])
        return Response(raw, media_type='application/json')
    except (sqlite3.Error, ValueError, TypeError, UnicodeError, ValidationError):
        error('QUALITY_READ_UNAVAILABLE', 503)


def _validate_source_response(payload, context, groups):
    from .read_access import manifest
    for obj in manifest(payload):
        permitted = {'patient': context.source.patients, 'user': context.source.doctors,
                     'department_id': context.source.departments, 'group_id': groups}.get(obj['type'])
        if permitted is not None and obj['id'] not in permitted:
            error('QUALITY_READ_UNAVAILABLE', 503)
    totals = payload['totals']; summary = payload['scope_summary']
    if totals['patients'] > summary['total_patients'] or totals['findings'] > summary['total_issues'] or totals['duplicate_groups'] > summary['possible_duplicate_groups']:
        error('QUALITY_READ_UNAVAILABLE', 503)
    if payload['view'] == 'page':
        if payload['patients']['page']['total'] != totals['patients'] or payload['duplicate_groups']['page']['total'] != totals['duplicate_groups']:
            error('QUALITY_READ_UNAVAILABLE', 503)
    if payload['view'] == 'patient_issues' and (payload['patient']['patient_id'] != context.parsed.selector.owner_patient_id or payload['collection']['page']['total'] != payload['patient']['issue_count']):
        error('QUALITY_READ_UNAVAILABLE', 503)
    if payload['view'] == 'members' and (payload['group']['group_id'] != context.parsed.selector.group_id or payload['collection']['page']['total'] != payload['group']['member_total']):
        error('QUALITY_READ_UNAVAILABLE', 503)
    # A1 validates group source membership/semantics, not A2 selection/paging.
    def walk(value):
        if isinstance(value, dict):
            if 'group_id' in value and 'member_total' in value:
                group = groups[value['group_id']]
                if value['member_total'] != len(group['patient_ids']) or value['reason_codes'] != group['reason_codes'] or value['reason'] != group['reason']:
                    error('QUALITY_READ_UNAVAILABLE', 503)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)


@clock.consistent
def validate_release(conn, user, check: QualityScopedRelease):
    with patient_pages.snapshot(conn):
        actual = _input(conn, user, check.binding)
    if not hmac.compare_digest(actual.revision, check.expected) or actual.day != check.day:
        raise patient_pages.QueryChanged()


def validate_admission(user, state, check: QualityScopedRelease):
    """O(1) checks under the existing lock; never repeat a source scan here."""
    if not hmac.compare_digest(_binding(user, state), check.binding):
        raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
    if not check.issued_at <= _now() <= check.expires_at:
        error('CURSOR_EXPIRED', 410)
    if clock.now_moscow().date().isoformat() != check.day:
        raise patient_pages.QueryChanged()
