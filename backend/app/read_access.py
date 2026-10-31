"""One durable release event + exact manifest of the serialized response.

Response bodies are buffered before release. No medical values enter the manifest.
An event proves authorization to release a snapshot, not receipt by the client.
"""
import hashlib
import json
import sqlite3
import re
from contextlib import closing
from starlette.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from . import audit, config, db, policy, security, session_context, request_security

RELEASE = 'medical_read_release'
DENIED = 'medical_read_denied'
EXPORTS = {'export', 'analytics_report_exported', 'consilium_registry_exported'}


def _id_type(key):
    mapping = {'patientId': 'patient', 'patient_id': 'patient', 'eventId': 'event', 'event_id': 'event',
               'historyEntryId': 'history_entry', 'history_entry_id': 'history_entry',
               'appointmentId': 'appointment', 'appointment_id': 'appointment',
               'slotId': 'slot', 'slot_id': 'slot', 'userId': 'user', 'user_id': 'user'}
    return mapping.get(key, key)


def _object_type(value, kind):
    if 'history' in value and 'name' in value:
        return 'patient'
    if kind == 'history':
        return 'history_entry'
    return {'patients': 'patient', 'appointments': 'appointment', 'events': 'event',
            'slots': 'slot'}.get(kind, kind)


def manifest(payload, path_params=None):
    objects = set()
    def walk(value, kind='response'):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key == 'id' or key.endswith('Id') or key.endswith('_id')) and isinstance(item, (str, int)):
                    objects.add((_object_type(value, kind) if key == 'id' else _id_type(key), str(item)))
                if key == 'patientIds' and isinstance(item, list):
                    objects.update(('patient', str(x)) for x in item if isinstance(x, (str, int)))
                walk(item, key)
        elif isinstance(value, list):
            for item in value:
                walk(item, kind)
    walk(payload)
    for key, value in (path_params or {}).items():
        if key.endswith('_id'):
            objects.add((_id_type(key), str(value)))
    return [{'type': kind, 'id': identity} for kind, identity in sorted(objects)]


def attempted_manifest(params):
    result = []
    for key, value in (params or {}).items():
        if key.endswith('_id'):
            text = str(value)
            canonical = bool(re.fullmatch(r'[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|[0-9]+', text))
            result.append({'type': 'attempted_' + _id_type(key) + ('' if canonical else '_sha256'),
                           'id': text if canonical else hashlib.sha256(text.encode()).hexdigest()})
    return result


def persist(state, route, raw, params, denied):
    token = request_security.current.set(None)
    try:
        return _persist(state, route, raw, params, denied)
    finally:
        request_security.current.reset(token)


def _persist(state, route, raw, params, denied):
    from fastapi import HTTPException
    payload = None if denied or state.export_recorded else json.loads(raw) if raw else None
    objects = attempted_manifest(params) if denied else manifest(payload, params)
    if not denied:
        objects = [{'type': kind, 'id': identity} for kind, identity in sorted(
            {(o['type'], o['id']) for o in objects} | state.aggregate_objects | set(state.export_objects))]
    encoded = json.dumps(objects, ensure_ascii=False, separators=(',', ':'))
    with closing(db.connect_live(config.DB_PATH)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            if not denied:
                session = security.validate_session(conn, state.token, remove_expired=False)
                if session is None:
                    raise HTTPException(status_code=401, detail='Not authenticated')
                fresh = policy.load_current_user(conn, session['user_id'])
                if fresh is None or fresh.status != 'active':
                    raise HTTPException(status_code=401, detail='Not authenticated')
                if session_context.access_context(fresh) != state.access:
                    raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
                if state.export_recorded:
                    allowed = (policy.can_manage_consilium(fresh) if route.startswith('GET /api/consilium/registries/')
                               else policy.can_export(fresh) if route.startswith('GET /api/export') else True)
                    if not allowed:
                        raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
                # A card can leave scope without a user-role/departments change.
                # Recheck only routes governed by card scope; consilium/registry
                # have their own distinct permissions and are not broadened here.
                card_scoped = any(prefix in route for prefix in (
                    '/api/patients', '/api/analytics', '/api/data-quality', '/api/notifications',
                    '/api/internal-notifications', '/api/ai/chat', '/api/export'))
                if card_scoped:
                    for identity in {obj['id'] for obj in objects if obj['type'] == 'patient'}:
                        if not policy.can_read_patient(conn, fresh, identity):
                            raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')

            # The only limit is SQLite's actual storage bound, not an API page cap.
            if len(encoded.encode('utf-8')) + 4096 >= conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH):
                raise audit.AuditWriteFailed()
            actor = state.user
            if denied and actor is None and state.token:
                session = security.validate_session(conn, state.token, remove_expired=False)
                actor = policy.load_current_user(conn, session['user_id']) if session else None
            metadata = dict(state.export_event) if state.export_recorded and not denied else dict(state.denial) if denied and state.denial else {
                'event_type': DENIED if denied else RELEASE,
                'detail': 'operation=' + route,
                **(audit.actor_ctx(actor) if actor is not None else {})}
            audit.record_required_or_fail(conn, result=audit.RESULT_DENIED if denied else audit.RESULT_OK, **metadata)
            event_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('INSERT INTO read_access_manifests (audit_event_id, objects_json, object_count, snapshot_sha256, access_context) VALUES (?, ?, ?, ?, ?)',
                         (event_id, encoded, len(objects), hashlib.sha256(raw).hexdigest(), state.access or ''))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def install(app):
    @app.middleware('http')
    async def audited_release(request, call_next):
        path = request.url.path
        tracked = path.startswith('/api/') and not path.startswith('/api/auth/') and path not in {'/api/health', '/api/ready', '/api/ai/status', '/api/docs', '/api/docs/oauth2-redirect'}
        state = request_security.RequestState(resource=path)
        token = request_security.current.set(state)
        try:
            response = await call_next(request)
            route_obj = request.scope.get('route')
            if not tracked or route_obj is None:
                return response
            denied = response.status_code in {401, 403, 404} or state.denial is not None or getattr(request.state, 'context_refused', False)
            read = request.method == 'GET' or state.export_recorded or path == '/api/ai/chat' or path.endswith('/preview') or path.endswith('/reason')
            if not denied and (not read or not 200 <= response.status_code < 300):
                return response
            if denied and state.denied_recorded:
                return response
            raw = b''.join([chunk async for chunk in response.body_iterator])
            try:
                await run_in_threadpool(persist, state, request.method + ' ' + route_obj.path, raw, request.path_params, denied)
            except Exception as exc:
                from fastapi import HTTPException
                changed = isinstance(exc, session_context.ContextChanged)
                code = 409 if changed else exc.status_code if isinstance(exc, HTTPException) else 503
                if changed or code == 401:
                    try:
                        await run_in_threadpool(persist, state, request.method + ' ' + route_obj.path, b'', request.path_params, True)
                    except Exception:
                        code = 503
                body = {'detail': 'Not authenticated' if code == 401 else 'Read audit unavailable'}
                if code == 503 and state.export_recorded:
                    body['code'] = 'AUDIT_UNAVAILABLE'
                if changed and code == 409:
                    body = {'detail': 'Access changed', 'code': 'ACCESS_CONTEXT_CHANGED'}
                return JSONResponse(body, status_code=code)
            return Response(raw, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        finally:
            request_security.current.reset(token)
