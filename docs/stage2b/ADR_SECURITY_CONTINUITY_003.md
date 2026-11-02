# ADR-MFA-CONTINUITY-003 — sequence, reservations, provenance and adoption

Version: 3.0 proposal, 2026-11-02. Base: ea805405e59323a3ba0a8fdd5e4c32be461e150a.
**C + S1 is approved as a research direction. WP-F v2 wire/file format, external authority and deployment are NOT approved or implemented.** This ADR refines the supplied ADR_SECURITY_CONTINUITY_V2.md; reference copies are byte-identical in reference/.

## Boundary and deliverable

Production operations_state.py, pending/permit v1, mfa_continuity.py, auth routes, security.py, schema v31, dependencies and WebAuthn crypto are unchanged. Production require_trust still always refuses mandatory MFA. The only production edit is the separate, versioned account-switch audit clarification.

prototype/continuity_lab/model.py is a privileged synthetic state-machine coordinator, not a provider usable by the app. Its SQLite witness is outside the replaceable app directory, excluded from every simulated DB/receipt rollback. It is still on the SAME Mac/filesystem: **no real independent durability, administration, authentication, consensus or anti-root guarantee is established**. There is no network adapter, key or environment switch to plug it into production.

## Non-negotiable infrastructure contract

An external authority must authenticate installation and purpose-specific privileged callers, offer linearizable CAS, durable non-expiring reservations, monotonic sequence and independent latest policy/epoch/revocation/recovery high-water. Its storage/admin/backup/disaster recovery cannot share the application's rollback domain. Fresh authenticated online reads, not a signed old receipt/cache, establish currentness. Split-brain, authority rollback/lost history, ambiguous storage health or unknown provenance deny service; no best-effort fallback, automatic timeout release or copied-sidecar adoption.

An attacker restoring old DB + old receipt while the current authority survives is detected. Restoring the entire universe, including an old authority, can reproduce a previously legitimate state: **this local model cannot detect that**. Custody and freshness of the independent service are deployment prerequisites, not a property of hashing. Real domain, store, provider, keys, operators and RPO/RTO remain UNSET.

## Mutation protocol, tested only in lab

1. Acquire exclusive installation maintenance/auth coordination; validate DB admission independently through WP-F in future integration. Read fresh current authority and locally coherent state/audit frontier. Check installation UUID, current sequence and projection, current policy high-water and receipt binding. Checks precede compatibility versus mandatory policy selection.
2. Validate proposed transition without writing. Reserve at authority with CAS of exact current sequence/projection, unique operation UUID and request digest. Authority durably records PENDING and next sequence. Only one reservation wins. No DB mutation before this acknowledgment. An uncommitted reserve failure leaves the unchanged earlier state usable; an acknowledged reserve blocks all subsequent grants even before any DB write.
3. In a single security DB transaction recheck the input state; write mutation, next sequence and required security audit/frontier. Failures roll back DB while authority remains pending. After DB commit there is still no usable new medical authority.
4. Publish bound local receipt with private exclusive temp creation, file fsync, atomic replace and directory fsync. Visibility after replace is insufficient. Receipt binds installation, before/after digests, operation UUID/kind, reservation nonce and next sequence. No bearer/challenge/medical fields. A failed sync prevents authority finalize.
5. Fresh authority CAS must match the exact complete reservation. Commit new high-water/projection/sequence and COMMITTED operation receipt atomically in authority storage. Only then may the future app validate an independently current matching pair and issue a session. Pending never expires into permission.
6. Lost reply after durable finalize: query the CURRENT authority and immutable operation record. Identical request returns an acknowledgment of its old sequence only, never an old session, enrollment or medical grant. Different body/kind/expected sequence with reused operation UUID refuses. A pending/mismatched/unknown operation stays held. No blind second mutation and no automatic reconciliation from a restored DB.

The model uses FULL SQLite transactions for each store plus real OS receipt fsync calls; that is not atomic commit across two stores. Safety comes from the reservation/grant barrier, accepting unavailability after incomplete operations. A real authority's uncertain commit must be resolved by its authenticated, healthy authoritative read, never by a local file or an exception handler deciding success.

All security-changing paths must participate: factor enrollment/activation/revocation and immutable ownership, counters/BE/BS, password revision/account status/roles/scopes/auth_epoch, policy, durable challenge claim/finalize and session assurance, recovery code use, grants and notices, lifecycle compaction, maintenance. The lab explicitly models factor states/counter/BS, epoch, policy, recovery-use IDs and flows. It does NOT implement recovery, activation or WebAuthn again, nor claim full production field coverage.

## Maintenance C and S1

Only the trusted offline executor, under the shared exclusion protocol, may reserve maintenance origin before changes. The operation name is descriptive, not proof. C needs independently recorded BEGIN, matching before state, approved transformation, successful offline DB verification, unchanged normalized security projection and committed authority END with next sequence/receipt. Model verify and migrate (including no-op) change generation but preserve factor/policy/epoch/recovery/flow content. Journal frontier advances by the known maintenance event. No generic mapping accepting arbitrary changed fields exists.

Restore reserves first and enters sticky SECURITY_REVIEW_HOLD before replacement, even if bytes are identical and exit is 0. Crash/EIO/ENOSPC after BEGIN, unknown origin or mismatch also blocks. Repeat verify cannot launder PENDING/S1 into C. Restored revoked factor, used code, old epoch or compatibility-policy mismatches current authority. There is no command to clear hold or auto-transfer trust.

A migration that changes security semantics requires a separate versioned projection mapping and evidence; current lab rejects such changes. A real WP-F CLI migration is NOT integrated here. Existing v1 verify/migrate generation semantics and all historical restoration checks remain covered by the unmodified backend tests.

## Exact future WP-F integration proposal — approval required

Do NOT add optional fields to strict v1 objects. Proposed protocol v2 must be negotiated across CLI, app, workers, DB access, lifespan-off paths and deployment images; downgrade is prohibited using independently current minimum protocol/policy state. Schema version is separate from protocol version.

Proposed strict pending v2 fields: protocol_version, installation_id, generation, operation_id, operation_kind, reservation_id, witness_previous_sequence, before_security_projection_digest, previous_permit_digest. Proposed permit v2 fields: protocol_version, installation_id, generation, schema, database identity, operation_id, reservation_id, witness_committed_sequence, security_projection_digest, security_projection_version, receipt_digest. Exact types/bounds/canonical encoding, state migration and authentication format require review before executable DDL/parser implementation. These names are a contract proposal, not new files accepted by v1.

Pending wins over any permit. All app opens require the correct v2 pair and current external authority, not only startup. A future technical anchor/mutation-outbox table must be explicitly migrated (candidate v32, **not created here**) to bind installation, projection version, witness sequence, monotonic allocation serial, provisional status and operation UUID. Outbox content must not contain medical fields or reusable bearer material. It cannot by itself prove external freshness.

First adoption of existing v1/v31: security hold plus separately approved independent baseline/reproof ceremony; no automatic trust issuance by verify-db. New install needs approved bootstrap and independent authority initialization, absent here. Old installations without a witness keep current Stage 2A behavior; they do not gain C. An old pre-MFA restore must be refused by the independently current deployment/installation gate before accepting compatibility login; backup policy cannot authorize its own downgrade.

Future read/commit/final response checks must reconcile the CURRENT witness and DB under the auth/maintenance coordination boundary. Sensitive sessions are provisional until final commit; old prepared responses retain existing fresh WP-C/WP-D guards. A distributed access/revocation linearization point, multiple workers, authenticated transport, authority health/durability, latency and bounded caching (if any) need a separate integration proof. The current local model returns a policy label only; it never issues medical sessions.

## Crash and recovery matrix

| Point | Lab outcome / required future outcome |
|---|---|
| Reserve before commit fails | No mutation, old confirmed pair remains valid |
| Durable reserve, before DB | PENDING blocks grants after restart |
| DB transaction incomplete | DB rollback; pending still blocks |
| DB commit, before receipt | Changed DB never admitted against pending |
| Receipt file fsync/rename/directory fsync fails | No finalize; visible receipt is not permission |
| Receipt durable, before authority commit | Pending; no automatic retry into C |
| Complete finalize, lost reply/SIGKILL | Fresh matching authority resolves committed state; request retry is ack-only |
| Conflicting/duplicate operation | Single reservation; identical completed UUID ack-only, mismatch denied |
| Restore, even identical bytes | S1; repeat verify cannot clear |
| Old DB and receipts, live current witness | Sequence/projection/high-water deny |
| Witness absent/corrupt/unreachable | Controlled hold, never auto-create empty authority |
| Witness itself rolled back with app | Outside model guarantee; deployment forbidden without independent freshness |

Fault tests use EIO/ENOSPC at controlled before-commit/transition hooks, real monkeypatched os.fsync, and child-process SIGKILL barriers. They are NOT actual disk exhaustion, SQLite VFS-level injected fsync faults, power cuts or remote network/consensus qualification. Receipt publication exercises real files/OS calls; storage hardware honesty is not proven. SQLite documents both atomic commit assumptions and synchronous behavior: [Atomic Commit](https://www.sqlite.org/atomiccommit.html), [PRAGMA synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous).

## Approval gate

Stop before any WP-F format/parser/CLI integration, provider binding, real enrollment/activation or automatic cleanup. Required decisions: independent witness ownership/hosting/recovery, security projection coverage and mappings, adoption/downgrade gate, protocol/version rollout, retention/operation-receipt compaction, availability budget/monitoring and independently qualified Linux/storage/network failure testing. No new work in those directions is authorized by this prototype alone.
