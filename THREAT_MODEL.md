# Threat Model

Method: STRIDE-oriented white-box model for the local FastAPI + SQLite
application at base commit `6019436b967ac26b1b106f445c4f83921ea6cb18`.

## Assets

Patient records, accounts, password hashes, server-side sessions, CSRF tokens,
roles, department structure, audit events, SQLite database, WAL/SHM files,
backups, CSV exports, notification queue, analytics aggregates, configuration,
source code, GitHub repository, CI/CD baseline, patient mobile-access links
(`patient_portal_access`, including the patient email address), future SMS
provider boundary, and future local AI-server boundary.

## Trust Boundaries

Browser user interface, static frontend assets, FastAPI process, SQLite,
filesystem, CLI operator, notification worker, MockNotificationProvider,
reverse proxy/TLS termination, administrator/operator, GitHub Actions,
dependency ecosystem, future real SMS provider, and future local AI server.

## Threats

| ID | STRIDE | Asset | Actor | Preconditions | Attack path | Existing control | Residual risk | Status | Recommendation | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| TM-001 | Spoofing | Sessions | Remote user | Stolen cookie | Replay `clinic_session` | Server-side opaque token, DB stores hash only, TTL | Cookie theft remains impactful until expiry | PARTIAL | Enforce HTTPS/Secure in production and consider idle/absolute split | `backend/app/security.py`, tests `test_session_lifecycle.py` |
| TM-002 | Spoofing | CSRF token | Malicious site | Victim authenticated | Cross-site write with cookies | CSRF header tied to server-side session on protected writes, including logout | Browser token theft remains impactful | PASS | Preserve route inventory and logout CSRF regression tests | `backend/app/deps.py`, SEC-2026-002 |
| TM-003 | Tampering | Patient records | Doctor/head | Object ID substitution | Change path/body/query IDs | `policy.can_read_patient` and `can_write_patient`; scope SQL | Coverage depends on every endpoint using policy | PASS | Keep route-level tests for every new endpoint | `backend/app/policy.py`, `backend/tests/test_rbac_matrix.py` |
| TM-004 | Tampering | Import data | Scoped user | Crafted import JSON | Assign patients to foreign doctor | Import doctor resolution checks role scope and strict request schemas reject unknown fields | Import/export compatibility keeps explicit read-only export fields accepted | PASS | Keep strict-schema tests on new request models | SEC-2026-005 |
| TM-005 | Repudiation | Audit log | Authenticated user | User submits actor/timestamp fields | Attempt to forge audit metadata | Audit context computed server-side | Retention pruning is manual | PASS | Add retention schedule/monitoring for production | `backend/app/audit.py`, tests `test_audit.py` |
| TM-006 | Information Disclosure | API metadata | Unauthenticated user | Production default config | GET `/openapi.json` | `/api/docs`, `/redoc`, and `/openapi.json` disabled outside debug | Debug mode intentionally exposes docs for local development | PASS | Keep production docs/OpenAPI regression tests | SEC-2026-001 |
| TM-007 | Information Disclosure | PHI in frontend | XSS actor | Stored payload rendered | DOM injection | Strict CSP, no `innerHTML`, no inline handlers/styles | Browser smoke was bounded, not exhaustive DAST | PASS | Preserve CSP regression tests | `backend/tests/test_frontend_csp.py` |
| TM-008 | Information Disclosure | SQLite/backups | Local filesystem actor | Disk access | Copy DB/backup/WAL | DB outside static route; encrypted portable backups; chmod 0600 | Live DB/WAL/SHM, swap, crash dumps and snapshots still rely on encrypted volume | PARTIAL | Require disk encryption; keep backup-key procedure tested | SEC-2026-006 |
| TM-008A | Tampering | Backup restore | Local filesystem actor | Modify backup file | Restore altered or truncated backup | Clinic Backup Format v1, AES-256-GCM tag, authenticated header, parser bounds, SQLite integrity/FK/schema checks, offline exclusive lock | Operator can still choose the wrong valid backup | PASS | Keep wrong-key/tamper/truncation/active-lock regression tests and manual drills | SEC-2026-006 |
| TM-008B | Information Disclosure | Backup key | Local operator mistake | Put key in Git/history/backup dir | Decrypt stolen backups | Key read from env or 0600 key file; keygen writes file without printing key; no CLI `--key`; key not stored in backup/audit | Production key custody is procedural | PARTIAL | Use two-person offline key backup and rotation runbook | SEC-2026-006 |
| TM-008C | Information Disclosure | Temporary plaintext | Host/filesystem | Backup/restore temp snapshot | Recover deleted temp file from SSD/snapshot | Private temp dir, cleanup on success/failure, 0600 temp files | Secure physical deletion is not guaranteed | PARTIAL | Use encrypted filesystem/volume for data and temp directories | SEC-2026-006 |
| TM-009 | Denial of Service | Auth | Remote user | Repeated login failures | Brute force or lockout | Per-host/per-login in-memory backoff with hashed username subject and capped buckets | Restarts reset state; no shared multi-process limiter | PARTIAL | Add persistent/proxy-aware rate limiter | SEC-2026-003 |
| TM-010 | Denial of Service | Expensive endpoints | Authenticated user | Repeated analytics/export/DQ calls | CPU/IO pressure | Pagination, bounded imports, and capped per-process rate limits on selected heavy/search endpoints | Multi-process deployments still need persistent/proxy controls | PARTIAL | Add deployment-level or persistent limiter before broad exposure | SEC-2026-003 |
| TM-011 | Elevation of Privilege | Admin functions | Low role | Call staff/department endpoints | BFLA | Developer-only dependencies and tests | CI not enforced on PRs | PASS | Require branch protection and CI | SEC-2026-004 |
| TM-012 | Tampering | Repository | Maintainer token | Direct push to main | Bypass PR review | Git history available | No branch protection/workflows visible | FAIL | Enable protected main, required reviews/checks | SEC-2026-004 |
| TM-013 | Information Disclosure | Notification data | Scoped user | Queue/detail enumeration | Guess notification ID | Scoped queries and 404 on inaccessible ID | Future real provider delivery ambiguity remains | PASS | Add provider idempotency before real SMS | `NOTIFICATIONS.md`, tests `test_notifications.py` |
| TM-014 | Information Disclosure | Analytics | Scoped user | Filter foreign doctor/department | Aggregate hidden patients | Scope before aggregation and pagination | No external BI integration yet | PASS | Keep aggregate scope tests on new reports | `backend/tests/test_analytics.py` |
| TM-015 | Spoofing/Tampering | Future AI | Future integration | Local AI endpoint added | Prompt/data leakage | Out of scope; no AI code present | Future boundary unimplemented | N/A | Threat-model before implementation | Source inventory |
| TM-016 | Information disclosure | Patient mobile-access links | Staff member with patient-write rights | Authenticated session, write access to at least one patient | Probe `PUT /api/patients/{id}/portal-access` with a guessed address: 409 means the address belongs to some card, 200 means it does not — including cards outside the actor's scope | `UNIQUE` kept for integrity; rate limit `portal_access_link` 10/min per user and host; `portal_access_conflict` audit event on every conflict; response names neither patient nor address | One bit per probe still leaks to an authenticated, audited, rate-limited actor | PARTIAL | Revisit if a patient-facing eligibility endpoint is added in Stage 2 — there the same signal would be reachable unauthenticated and must not be | `backend/app/services/portal_access.py`, `backend/tests/test_portal_access.py` |
| TM-017 | Elevation of privilege | Patient mobile-access links | Doctor or read-only role | Authenticated session | Attempt to grant mobile access outside the intended role set | `can_manage_portal_access` restricted to developer/department_head and re-checked per object; `can_revoke_portal_access` = `can_write_patient`; out-of-scope patient returns 404 | None identified; covered by negative tests across all five roles | OK | Keep the role check in `policy.py`, not in the route | `backend/app/policy.py`, `backend/tests/test_portal_access.py` |

## Future Boundaries

Patient authentication (Stage 2) is not implemented: there is no OTP, no patient
session, no TOTP and no enrollment. The mobile application is not connected to
this backend and declares no network permission. The eligibility check that
Stage 2 will expose to unauthenticated callers is the point where the existence
oracle described in TM-016 becomes materially more dangerous, and it must be
designed so that the response does not depend on whether the address is known.

Real SMS provider and local AI server are not active. They must be reviewed in
separate stages before implementation. For SMS, the notable future risk is:
provider accepts SMS, worker crashes before saving `sent`, then the lease
recovery may resend. This is not exploitable against the current MockProvider.
