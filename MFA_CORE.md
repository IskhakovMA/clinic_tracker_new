# WP-G Stage 2A — local MFA core, activation blocked

Base: fd7e6ec784caa1053e2df644ac35eaa9aac8bbc6. Schema: v31. No production activation, real account enrollment, recovery service, external witness or WP-F v1 format change is part of this stage.

## Compatibility and admission

The explicit initial policy is `stage1_compatibility`: it preserves Stage 1 password sessions without inventing MFA assurance. `required_cohort` exists for isolated synthetic fixtures. Missing, malformed or unknown policy denies authentication. A current v31 database never repairs a missing policy row back to compatibility during initialization.

For mandatory users, password login creates only a short-lived HttpOnly pre-auth cookie and separate CSRF. The WebAuthn endpoint has an explicit pre-auth CSRF dependency, repeated inside the durable claim transaction. Challenge claims precede cryptography. Finalize rechecks identity, password/access/epoch/policy/generation/factor under write lock, then atomically writes required audit, counter/BS, session assurance and session. Password change, admin reset, role/scope revocation and session termination do not bypass MFA. The frontend stays on its existing locked/login path until complete authentication, then uses the existing server-first workspace/draft reconciliation.

`mfa_continuity.require_trust` always denies in application code. No environment/CLI flag can allow it. Synthetic tests alone substitute this boundary. This deliberately blocks real mandatory MFA before an approved bootstrap and independent current security authority exist. Matching local generation or a restored hash does not grant trust. `mfa_continuity.SecurityMutation` specifies an interface for all factor, counter/BS, password/authority/epoch/policy, flow/session, recovery/notice and maintenance mutations; no external ledger is implemented.

**Compatibility is not rollback proof.** Before any real rollout, independently current policy/high-water state must gate the choice of compatibility versus mandatory MFA itself, including a restore of an old pre-MFA backup. A policy stored only in that backup cannot prove that MFA was never mandatory. This remains a release blocker; the Stage 2A compatibility path must not be used as a production downgrade/recovery mechanism. No option C or WP-F v2 is implemented.

## Factors and enrollment

Handles are random opaque 32-byte values. Library-verified credentials have immutable owner/ID/key data/BE; revoked rows remain tombstones. The RP adapter checks ID/rawId, returned non-null userHandle, exact RP/origin, cross-origin exclusion, UP/UV, BE/BS and counter policy. A null handle is allowed only with explicit allowCredentials and server owner binding. No custom WebAuthn cryptography is used.

An already MFA-authenticated user can prepare and submit a new registration bound to the parent session. The result is **PENDING only**. No activation endpoint exists. Proof of possession and independently confirmed notification are required before future activation. First enrollment/bootstrap and full recovery are intentionally unavailable. Two credentials from one virtual authenticator do not establish independent factor availability.

## Offline migration and restores

v30 → v31 is explicit through existing offline migrate-db and subsequent verify-db. The five technical tables add no medical columns, rewrite no clinical history and do not mark legacy sessions verified. The approved v30 schema is included in the exact restore catalog; historical source versions remain matched exactly. WP-F admission and MFA security assurance are distinct.

An actual v31 CTBK restore regression proves that an intact recovered ACTIVE credential does not authorize a medical session: after database verify the real S1 boundary still refuses. Historical pre-MFA restore compatibility remains before-rollout behavior, subject to the independent policy gate required above before activation.

## Reproduction and evidence

Use a fresh Python 3.13 environment with backend/requirements-dev.txt (hash checking is mandatory). New regressions are backend/tests/test_wp_g_stage2a_mfa.py. Public synthetic vectors originated in Chromium virtual authenticators; signed authenticator bytes are literal byte arrays in the JSON fixture, not secrets. No scanner exclusions were added.

The localhost-only tools/tests/wp_g_mfa_synthetic_server.py explicitly substitutes synthetic trust and seeds temporary accounts. It is a test entrypoint, never included by backend.app.main. tools/tests/wp_g_mfa_browser.cjs exercises the actual Clinic frontend/API and a PENDING registration. Existing WP-D/FIX1/FIX2 and other browser scripts remain applicable. No physical-key, Safari/Firefox or native Linux runtime qualification is implied by local macOS and Linux target-install checks.

Final report, exact commit SHA, complete diff and raw evidence are delivered outside the worktree as WP_G_STAGE2A_IMPLEMENTATION_REPORT.md and the review package. No automatic activation instructions are provided.

References: [python-fido2 server API](https://developers.yubico.com/python-fido2/API_Documentation/autoapi/fido2/server/index.html), [WebAuthn Level 3](https://www.w3.org/TR/2026/REC-webauthn-3-20260825/).
