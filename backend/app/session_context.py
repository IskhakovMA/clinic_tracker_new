"""Предусловия контекста UI, не учетные данные и не источник полномочий."""
import hashlib
import json

from . import policy

USER_HEADER = "X-Clinic-User"
ACCESS_HEADER = "X-Clinic-Access"


def access_context(user: policy.CurrentUser) -> str:
    payload = {"id": user.id, "role": user.role_id, "scope": user.scope_strategy,
               "departments": sorted(user.department_ids), "status": user.status,
               "mustChangePassword": user.must_change_password,
               "permissions": policy.permissions_payload(user)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ContextChanged(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
