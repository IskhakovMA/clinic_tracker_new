"""S1 boundary. No provider/flag can silently approve recovered security state.

Stage 2A deliberately has no production trust issuer. Tests substitute this
boundary in-process for synthetic installations, never through configuration.
Future continuity requires a separately approved WP-F protocol/authority.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from fastapi import HTTPException


class Mutation(str, Enum):
    ENROLL = 'enroll'
    ACTIVATE = 'activate'
    REVOKE = 'revoke'
    COUNTER = 'counter'
    PASSWORD = 'password'
    AUTHORITY = 'authority'
    EPOCH = 'epoch'
    POLICY = 'policy'
    CHALLENGE_CLAIM = 'challenge_claim'
    SESSION = 'session'
    RECOVERY = 'recovery'
    NOTICE = 'notice'
    MAINTENANCE = 'maintenance'


@dataclass(frozen=True)
class SecurityMutation:
    operation_id: str
    kind: Mutation
    user_id: str
    expected_sequence: int
    before_digest: str
    after_digest: str
    generation: str


class ContinuityAuthority(Protocol):
    def require_current(self, mutation: SecurityMutation) -> None: ...
    def reserve(self, mutation: SecurityMutation) -> str: ...
    def commit(self, reservation: str) -> None: ...


def require_trust(conn, user_id: str, generation: str) -> None:
    # Matching a local generation/hash is NOT proof of freshness or provenance.
    raise HTTPException(403, 'MFA_SECURITY_REVIEW_HOLD')
