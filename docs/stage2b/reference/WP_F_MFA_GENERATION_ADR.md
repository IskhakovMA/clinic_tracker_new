# ADR-S1 — MFA trust and the existing WP-F generation protocol

Status: **proposed for independent review, not approved or implemented**.
Base: fd7e6ec784caa1053e2df644ac35eaa9aac8bbc6. Lab evidence: four real CLI experiments in `tests/test_generation.py` and `evidence/generation-*.json`, `restore-security-rollback.json`.

## Established behavior

- Repeated `verify-db --confirm-offline` with no restore and no pending calls begin("verify-db") and creates a fresh generation.
- `migrate-db --confirm-offline` creates generation before checking whether schema v30 is already current. The no-op still revokes permit and needs verify.
- A real synthetic v29→v30 migration also changes generation and needs verify.
- A planned backup did not change generation.
- Real encrypted CTBK restore creates pending/new generation; readiness fails until explicit verification. After verification a restored companion security snapshot contains a formerly revoked factor, a previously consumed recovery-code and old session/flow rows. S1 blocks all these uses.

Existing v1 JSON shapes are strict: pending has version/generation/operation; final permit has version/generation/schema/database. After successful verification pending is removed. Final permit does not carry operation provenance. The known operation set is restore/migrate-db/bootstrap/verify-db. Adding fields/operations silently would violate existing readers and compatibility. The prototype does not modify any of these.

## Architectural problem

DB health admission and authenticator trust are different decisions. A generation-mismatch hold is fail-closed, but it blocks medical login after ordinary reverify/no-op migration too. This can hold all admins at once; proposing that an already-held admin simply approve recovery online is circular. A root/operator can edit a database in any design; this is not the threat MFA claims to eliminate.

The prototype has **no carry-forward/retrust command**. Initial fixture creation binds a newly empty synthetic store to the then-current valid permit; it is not a command for updating an existing store. `Engine.initialize()` cannot reopen/retrust an existing schema.

## Options requiring owner approval

### A. Keep v1 and conservative S1

Every generation change holds authenticator trust. Before app access resumes, an approved physical offline authority performs independent security-state review; unknown revocations require individual reproof/rebinding. Restore invalidates recovered sessions, flows and recovery codes regardless of health verification. Prior factor continuity cannot be inferred from the restored DB alone.

Consequence: RTO includes custody/notice/identity work for the affected staff, potentially the whole institution after a routine verification. No realistic duration can be assigned without actual staffing and device inventory. A safe migration/reverify could use independently trustworthy before/after evidence, but automatic copying of generation is not a verification.

### B. Keep v1 plus independently current security ledger

Separate non-rollback revocation/code/epoch authority permits distinguishing current authenticator trust from a restored clinical backup. This is a new dependency: storage, owner, authenticated updates, fsync/publication, key custody, capacity, synchronization, off-host disaster recovery, stale-ledger detection and reconciliation are unapproved. If its freshness is unknown after host loss, fail closed.

A file merely copied alongside the same old DB is not an independent ledger. A signed old ledger is still old. No store/address/provider or implementation has been created.

### C. Separately version WP-F operation provenance / approved continuity proof

A reviewed v2 protocol could preserve operation lineage and a trustworthy pre-maintenance security-state anchor, allowing a verified unchanged security-state migration/reverify to retain trust. It requires a new compatibility and crash matrix, explicit pending/permit publication/fault handling, old installations/backups/adoption rules and independent review. Operation name alone cannot recover missing post-backup revocations, so real restore still requires B or reproof under A.

This is an architectural proposal only. No extra sidecar, format field, operation name or bypass has been added. Do not overload existing verify-db to issue medical/MFA authority.

## Required acceptance before production

Approve option, recovery authority when all admins are held, RTO expectations, bootstrap/three-operator availability and independent notification. Prove failure modes with synthetic power/crash/publication tests under the chosen protocol. Preserve WP-C medical integrity and existing WP-F fail-closed admission. No full MFA implementation should start with this issue silently unresolved.
