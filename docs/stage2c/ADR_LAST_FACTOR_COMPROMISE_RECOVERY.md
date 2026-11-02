# ADR: all-factor compromise block and controlled recovery

Status: **PROPOSED — owner approval required; NOT IMPLEMENTED**.
Scope: Stage2C FIX1; baseline afa379dc. No change to WP-F v1, crypto or S1.

## Existing behavior and implemented narrow correction

Ordinary revocation must leave at least one ACTIVE key. FIX1 enforces this under
both existing locks, before reservation/writes. A two-to-one transition retains
ACTIVE account state but degraded redundancy until independent replacement.
A valid remaining key can authenticate; password alone cannot activate factors.

The existing notice-dispute contract already immediately revokes all factors,
sessions and flows, increments epoch and sets SECURITY_REVIEW_HOLD. It is an
available blocking path only for a valid, independently authenticated dispute
bound to a real notice. It must not be repurposed into a made-up notice or used
to silently clear the hold. Ordinary recovery intentionally rejects a held user.

## Proposed separate incident transition

A general compromise report needs a new, narrowly scoped authority proof
`all_factors_compromised`, rather than changing `factor_revocation` semantics.
It must not require use of any suspected key or trust password knowledge alone.
The independently current incident authority must verify employee identity and
report origin through a separately approved process before issuance. Operator
marking, bearer possession or a self-asserted identity are insufficient. Exact
identity verification, authority custody and denial-of-service controls require
owner/infrastructure approval; there is no production issuer today.

Proof proposal: version, incident ID, target, current binding/auth_epoch,
exact current credential-set commitment, all-factor blocking scope, independently
current witness sequence, audience, operation ID, nonce and expiry. Do not put
clinical values, raw challenges, signatures or bearer tokens in audit. Hashes of
restored state alone are not evidence of currentness. No automatic reanchor.

Under existing coordination/write locks: validate S1/current witness, account,
exact proof and scope; durable reservation before writes; atomically revoke ALL
PENDING/ACTIVE factors, retire sessions/assurance/old authentication and lifecycle
flows/grants, increment epoch, record required incident-block event and create
independent notification. Finalize witness before any success response.
Response must contain no medical session, EnrollmentOnly grant or usable factor.
Replays must not allocate grants or repeat state changes. Any incomplete DB/
witness mutation remains fail-closed. Notification failure never delays a valid
compromise block; a dispute remains sticky.

Proposed post-block state: existing ENROLLMENT_REQUIRED plus a durable incident
case linked to the target/epoch. This denotes no medical access and requires the
normal two-of-three current independent operator recovery approvals, fresh
step-up, independent confirmation and a once-only EnrollmentOnly grant. A plain
first-enrollment authorization must continue to reject factor/grant history.
An existing SECURITY_REVIEW_HOLD must NOT be downgraded by this proposal: its
release requires an independently reviewed incident resolution protocol with
separate evidence and approval, not an ordinary recovery request.

## Why this is not implemented in FIX1

This is a new authority-proof and incident/audit contract. Existing recovery
cases do not model incident provenance or release of security-review holds.
Using a recovery case as a substitute would conflate identity recovery with
compromise resolution. Before implementation, decide whether incident provenance
requires an additive technical table/schema v33 or an approved independently
retained case ledger. Do not modify v32/v31 retroactively, use free-form fields
as hidden authority, or change WP-F pending/permit v1. No schema extension or
new authority proof is implemented in this FIX1 commit.

## Approval and test conditions

Approve: incident-authority identity/custody, compromise-report verification,
anti-abuse rules, exact proof/evidence contract, case persistence/audit event,
operator/quorum requirements, incident/notice dispute resolution and recovery
entry, retention and infrastructure. No invented real operators, domains,
expiry or production policies.

Future tests: no remaining-key use; password-only/forged incident denial;
exact target/set/epoch/witness binding; concurrent revoke/incident/recovery;
SIGKILL before/after reserve/DB commit/finalize; EIO/ENOSPC/fsync/audit failure;
expired/replayed proof; stale restored incident/policy; missing authority;
independent delivery failure and dispute; two-of-three recovery only; no
medical session or compatibility downgrade at any intermediate state.

Until approval, production remains disabled. For an actual suspected incident,
stop medical use and follow independently approved operational blocking/recovery;
this synthetic implementation is not a production incident tool.
