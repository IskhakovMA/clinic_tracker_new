# Stage 2C server lifecycle — disabled production authority

Base be4adab72f85852bdd0786d55baa300704366197. Explicit additive schema v32.
This stage does NOT enable production MFA, public enrollment/recovery routes,
bootstrap, real channels, witness integration or WP-F v2. WP-F v1 is unchanged.

## Server component and admission

backend/app/mfa_lifecycle.py implements real SQLite transitions using existing
python-fido2/RP adapter. All operations enter authority() before any mutation,
including compatibility mode. The default calls the unchanged S1 require_trust
and then refuses even if that boundary were independently opened. No factory,
environment variable, CLI or administrator checkbox installs an authority.
The component intentionally has no production issuance router/UI. Future HTTP,
CSRF/cookie delivery, step-up, witness and identity/channel adapters need their
own approved integration. Existing Stage2A authentication/registration stays.

The protocol contract requires independently current start/reserve/finalize,
exclusive coordination through the DB commit/finalize gap, authenticated exact
binding evidence, single-use nonces and explicit duration/capacity policy.
Production values are UNSET. New test-only SyntheticAuthority has HMAC receipts,
a local current SQLite ledger outside the restored DB, and OS flock. It is NOT
an independent remote authority: root/whole-host rollback, real device/person
independence, network/consensus/storage recovery are not proven. It is imported
only by tests/tools and cannot be selected by application configuration.

Validate before reserve under the write barrier, reserve before first writes,
then DB mutation+required audit atomically, then authority commit/current check.
Failure after reservation leaves pending/hold, not an automatic reconciliation.
No response/grant is returned until finalization; a lost grant bearer after a
successful commit cannot be extracted from its stored hash. A new controlled
case/revocation is needed. No operation resets a security-review hold. The synthetic anchor includes the
current WP-F generation; even ordinary reverify cannot automatically transfer
its trust. This is conservative S1, not an implementation of WP-F v2/C.

## Enrollment and factor lifecycle

A per-person authenticated independent authorization creates one ISSUED token
(hash only stored), scope exactly EnrollmentOnly. Fresh independent recipient
proof redeems once into a CSRF-bound limited flow. First-enrollment authorization
refuses any ACTIVE/REVOKED factor history or prior enrollment grant: a valid
first-enrollment proof cannot replace two-operator recovery or reissue a lost
grant automatically. This is not clinic_session;
there is no sessions/assurance insert and no medical/admin/export access.

Registration creates PENDING only. A separate, durable-claimed WebAuthn UV
assertion proves possession of the exact owner/credential/user_handle/current
binding. A separate authenticated qualification attests an independent domain;
a user-supplied device label or two credentials is insufficient. Physical domain
qualification is an unresolved external authority requirement.

Each factor needs a fresh independently confirmed notice. Controlled activation
requires two distinct qualified domains, possession, current grant/epoch/password/
role/scope/policy/generation and confirmed notices, plus activation authority.
Activation increments auth_epoch and retires sessions/flows as well as grants,
so earlier recovery cases/approvals cannot target the newly activated authority.
A new password+WebAuthn login would be required, never a
medical session from activation. Existing Stage2A PENDING factors can be reviewed
only through explicit pending_factor_review authority, then the SAME possession,
independence and notice requirements. No automatic backfill or grandfathering.
Known/revoked credential IDs remain tombstones and cannot be registered again.
Fresh keys after recovery use new IDs; user_handle is unchanged.

For one lost factor, a current full UV session plus independent authorization
may create an additional limited grant. Remaining factors must be re-proved and
notified against the current epoch before activation of the new pair. Revocation
is immediate, terminates sessions/flows, increments epoch and invalidates old
grants. Delivery does not delay revocation. Old factors are never un-revoked.

## Recovery

Three explicitly designated independent operators, two different current votes.
Developer RBAC alone does not confer recovery rights. No operator assignments
are seeded by migration. Each vote is bound to exact case/target/epoch/policy/
generation/actor/parent session and a fresh authority-verified UV step-up proof.
The server also requires current full MFA assurance. A future real authority
must actually validate step-up; synthetic signed claims are not that production
implementation. Self/duplicate votes and stale role/status/assignment/session
fail. One unavailable operator still leaves two; loss of two blocks recovery.

Under the exclusive final transaction recheck BOTH approvers, then epoch++, old
credentials REVOKED, sessions/assurance/flows/grants invalidated and one limited
grant. All audit writes are mandatory. Recovery of A needs B+C. Concurrent votes
produce one grant. No emergency password, MFA-disable flag, online bootstrap or
automatic hold clearing exists. Administrative password reset cannot create a
lifecycle grant; its epoch/password/session changes invalidate old evidence.

## Independent notices

CREATED -> TRANSMISSION_INITIATED -> DELIVERY_CONFIRMED -> EMPLOYEE_ACKNOWLEDGED.
Authenticated receipt binds notice/target/action/binding/dispatch; acknowledgment
also binds the exact receipt and independent recipient identity. Nonces/receipt
IDs are one-use. A DB row, administrator mark or string issuer is not delivery.
DISPUTED is sticky, increments epoch, revokes authority and sets per-user
SECURITY_REVIEW_HOLD. Neither ordinary enrollment nor recovery clears that hold.
No medical values, bearer/CSRF, WebAuthn signature/challenge or contact data enter
security events or notice metadata. Receipt evidence is stored as a digest only.
Actual channel/contact custody and independent employee identity are UNSET.

## Migration and compatibility

v31 DDL is unchanged. migrate_v31_to_v32 adds eight technical tables/triggers,
seeds NO authority/operator/factor/grant/receipt. Production schema is32 only
through the existing explicit offline migration procedure: encrypted verified
pre-migration backup, durable pending, then operator offline verify. The v31
build be4adab reference is appended to the exact restore catalog. Historical
fixtures remove ONLY v32 additions before reconstructing old schemas; current
version assertions remain exact32. No medical data/history backfill or rewrite.

No automatic cleanup or retention is implemented. Existing budget principles
remain, and challenge limits come from the required authority-policy contract;
experimental durations/limits live only in SyntheticAuthority. Sustainable
retention/quotas for grants/cases/notices/receipts/audit/authority remain an
activation blocker. No real schedule or storage policy is selected.

## Qualification and remaining blockers

Synthetic tests exercise the backend service and real fido2 signatures; browser
harness uses ephemeral FastAPI/SQLite/virtual authenticators and a test-only
issuer. It does not prove physical independence or real notification delivery.
The full application security projection/mutation coverage, independently
current policy gate before ordinary compatibility login, trusted witness,
WP-F/deployment adoption, real HTTP/step-up/recipient delivery, three operators
and offline ceremony, HTTPS origin/RP ID, retention, RPO/RTO and monitoring all
require separate approval. A restored compatibility policy is NOT made safe by
restored SQLite or this disabled server component. Production S1 stays closed.
