# Stage 2B — technical flow lifecycle and security audit contract

2026-11-02; base ea805405. Lab lifecycle only; production v31 allocation/limits and retention unchanged.

## Separate four records

1. Challenge: short-lived, one-use, purpose/user/epoch/policy/parent-bound unpredictable value. Not durable audit content. Claim commits before cryptography. No reuse or return to PENDING after interruption.
2. Technical process row: token/CSRF hashes, challenge/library state, binding and terminal/expiry state. Its lifetime need not equal audit retention; it is not the long-term proof of the employee action.
3. Security event: immutable safe actor/subject/flow technical IDs, operation UUID, outcome, reason, assurance, epoch/purpose, sequence and timestamp. Required event transaction must commit with mutation. No bearer, CSRF, signature, challenge, password revision/hash or medical values.
4. Investigation evidence: correlation IDs, immutable event sequence/frontier, deletion batch evidence, actual current authority provenance and controlled investigation hold. It must survive deletion of transient rows. Receipt/ledger history also needs an approved retention/compaction policy; the model does not relocate unlimited history into a magically bounded witness.

## Lab cleanup sequence

No scheduled task, endpoint or executable production cleanup command is added. A privileged synthetic caller supplies explicit managed UTC eligibility and current time, never a production retention default. The generated plan binds exact sequence/projection/candidates and cutoff. Under the exclusive barrier a fresh matching plan removes only rows expired at the explicit cutoff. Nonexpired rows are excluded; stale plan fails. Expired CLAIMED may be removed only such that a later finalize cannot find/accept it. No grant is recovered from the removed row.

Allocation serial is monotonic and never recycled, including after cleanup. New challenge binds installation, serial and independent random entropy; tests fix entropy to demonstrate serial separation. Missing flow is denied before signature use. Old response for a different new challenge remains invalid. Rollback of an old process row/serial is blocked by independent current witness; cleanup by itself is NOT anti-rollback protection.

The lab appends flow_allocate/claim/finish events with technical flow/subject IDs and outcomes, then cleanup with removed serial IDs/count. Existing events are not deleted. The required audit frontier/length is in the independently committed security state; truncation, even after bypassing lab audit-delete trigger, refuses trust. This is not a digital signature or protection against simultaneous authority compromise.

Controlled recovery is demonstrated at 128 rows/user, plus a separate physical sizing experiment with the exact v31 MFA DDL at 4096 rows (32 synthetic users ×128). The sizing experiment deletes one explicitly selected expired FAILED batch of128 and retains4096 synthetic events. It does not call production authentication or implement the authority protocol. Timing/size depend on synthetic 1KiB library_state and local storage; not a real-user throughput claim.

Cleanup reuses row capacity and SQLite free pages; it need not shrink file size. No VACUUM/secure erasure/physical sanitization guarantee is made. Per-user128/global4096/active2 and TTL120 remain unchanged in production. A persistent shortage still returns fail-closed until an approved lifecycle is integrated.

## Decisions required before cleanup integration

Owner-approved retention and incident/legal hold, trusted eligibility clock, authorization/preview/approval, maximum batch and lock budget, archival/retrievability, security audit and authority receipt retention, monitoring and capacity SLO, anti-rollback continuity availability. Investigations can delay deletion and may exhaust capacity: an explicit availability/security policy is required, not an emergency delete flag. Numeric retention is UNSET. Proposed monotonic anchor/serial storage belongs to the separately approved future schema/protocol; no migration is introduced here.

## mfa_browser_session_revoked — implemented narrow clarification

Only NEW events carry detail.version=2. Existing event type and required transactional audit are preserved. Existing detail TEXT suffices: no table or migration, no UPDATE of historical events.

| Concept | New event representation |
|---|---|
| Initiator | actor_user_id = new password-verified identity B; detail.initiator_user_id = B |
| Retired browser identity | target_user_id = A; detail.previous_identity_user_id = A |
| New identity | detail.new_identity_user_id = B |
| Assurance actually proven | detail.authentication_level = password_verified; NOT WebAuthn/full MFA |
| Reason | detail.reason = identity_switch |
| Result | detail.outcome = session_revoked; outer result retains existing ok |

B here means the account whose first factor was verified by the server, not proof of the physical human B. Possession of A's cookie does not attribute B's action to A. No raw session correlation token is logged. The event commits atomically with retiring only the presented A session and starting B's limited flow (or existing compatibility flow). Wrong password/S1/audit/quota failure rolls back the event; other devices are unaffected. The result describes revocation, not successful B MFA. A downstream login failure is a separate event.

Historical detail-less entries retain the old Stage 2A convention actor=A,target=B. Consumers must version interpretation, never relabel them silently or rewrite history. Audit exports/reporting must communicate that these older rows cannot precisely establish an initiator. The v2 contract is documented here before future rollout.

Real API regressions prove exact metadata/actor/target, absence of bearer/CSRF, historical row byte-equivalence, and S1 refusal. Existing FIX1 rollback/concurrency/privacy assertions remain unmodified in the full suite. No new MFA permissions or session behavior are granted by the audit change.
