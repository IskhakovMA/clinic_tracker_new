"""Prepared structured AI boundary. Remote transfer remains disabled.

Aliases are request-memory only, tied to the validated session + access stamp.
No names, persistent IDs, free text or exact clinical values in model DTOs.
"""
import secrets
from .. import policy, security, session_context
from .tools import ToolPermissionError


class AliasScope:
    def __init__(self, conn, token):
        session = security.validate_session(conn, token)
        if session is None:
            raise ToolPermissionError('Session unavailable')
        user = policy.load_current_user(conn, session['user_id'])
        if user is None or user.status != 'active':
            raise ToolPermissionError('Session unavailable')
        self.token, self.user_id = token, user.id
        self.access = session_context.access_context(user)
        self.aliases = {}

    def fresh_user(self, conn):
        session = security.validate_session(conn, self.token)
        user = policy.load_current_user(conn, self.user_id) if session else None
        if session is None or session['user_id'] != self.user_id or user is None or user.status != 'active' or session_context.access_context(user) != self.access:
            self.aliases.clear()
            raise ToolPermissionError('Session unavailable')
        return user

    def reference(self, conn, patient_id):
        user = self.fresh_user(conn)
        if not policy.can_read_patient(conn, user, patient_id):
            raise ToolPermissionError('Patient unavailable')
        alias = next((a for a, identity in self.aliases.items() if identity == patient_id), None)
        if alias is None:
            alias = 'ref_' + secrets.token_urlsafe(18)
            self.aliases[alias] = patient_id
        return {'reference': alias}  # clinical categories need separate approval

    def resolve(self, conn, alias):
        user = self.fresh_user(conn)
        patient_id = self.aliases.get(alias)
        if patient_id is None or not policy.can_read_patient(conn, user, patient_id):
            raise ToolPermissionError('Patient unavailable')
        return patient_id


class MinimizedProvider:
    """Structured-only wire messages; original references stay server-side."""
    def __init__(self, provider, conn, token, patient_id=None):
        self.provider, self.conn = provider, conn
        self.scope = AliasScope(conn, token)
        self.context = self.scope.reference(conn, patient_id) if patient_id else None

    def _references(self, value):
        references = []
        if isinstance(value, dict):
            identity = value.get('patientId')
            if identity is not None:
                references.append(self.scope.reference(self.conn, identity))
            for child in value.values():
                references.extend(self._references(child))
        elif isinstance(value, list):
            for child in value:
                references.extend(self._references(child))
        return references

    def chat(self, *, messages, tools, max_output_tokens):
        import json
        from .provider import AIProviderResult
        self.scope.fresh_user(self.conn)
        # Never forwards arbitrary message/history/system content, tool comments,
        # names, original IDs or clinical fields. No clinical categories approved.
        wire = [{'role': 'system', 'content': 'Read-only structured assistant. Use opaque references only.'},
                {'role': 'user', 'content': json.dumps({'operation': 'summarize', 'context': self.context})}]
        for message in messages:
            if message.get('role') == 'tool':
                try:
                    references = self._references(json.loads(message['content']))
                except (ValueError, TypeError):
                    references = []
                wire.append({'role': 'system', 'content': json.dumps({'references': references})})
        result = self.provider.chat(messages=wire, tools=tools, max_output_tokens=max_output_tokens)
        self.scope.fresh_user(self.conn)
        calls = []
        for call in result.tool_calls:
            copy = {**call, 'function': dict(call.get('function') or {})}
            args = json.loads(copy['function'].get('arguments') or '{}')
            if 'patientId' in args:
                args['patientId'] = self.scope.resolve(self.conn, args['patientId'])
            # Search accepts no free-form query at this unapproved data boundary.
            if 'query' in args:
                args['query'] = ''
            copy['function']['arguments'] = json.dumps(args)
            calls.append(copy)
        return AIProviderResult(content=result.content, tool_calls=calls)
