"""Request-local security state; never credentials supplied by the caller."""
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class RequestState:
    aggregate_objects: set = field(default_factory=set)
    resource: str = ""
    token: str | None = None
    user: object = None
    access: str | None = None
    denied_recorded: bool = False
    denial: dict | None = None
    export_recorded: bool = False
    export_event: dict | None = None
    export_objects: tuple = ()
    protect_commit: bool = False
    patient_id: str | None = None
    write_request: bool = False


current: ContextVar[RequestState | None] = ContextVar('clinic_request_security', default=None)


def require_live_session(conn, *, check_access=False, check_object=False):
    state = current.get()
    if state is None or not state.protect_commit or not state.token:
        return
    from . import security
    from fastapi import HTTPException
    if security.validate_session(conn, state.token, remove_expired=False) is None:
        raise HTTPException(status_code=401, detail='Not authenticated')
    if check_access and state.user is not None and state.patient_id is not None and state.resource.startswith(('/api/patients', '/api/notifications')) and '/portal-access' not in state.resource:
        from . import policy, session_context
        fresh = policy.load_current_user(conn, state.user.id)
        if fresh is None or fresh.status != 'active':
            raise HTTPException(status_code=401, detail='Not authenticated')
        if session_context.access_context(fresh) != state.access:
            raise session_context.ContextChanged('ACCESS_CONTEXT_CHANGED')
        if check_object and state.write_request and state.patient_id and not policy.can_write_patient(conn, fresh, state.patient_id):
            raise HTTPException(status_code=404, detail='Patient not found')


def aggregate_patients(ids, *, resources):
    state = current.get()
    if state is not None and any(state.resource.startswith(prefix) for prefix in resources):
        state.aggregate_objects.update(('patient', str(identity)) for identity in ids)
