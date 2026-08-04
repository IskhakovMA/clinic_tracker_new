# Security Baseline Audit

## Executive Summary

Audit branch: `audit/security-baseline-2026`.

Base commit: `6019436b967ac26b1b106f445c4f83921ea6cb18`.

Reference repository commit:
`e612f4944c55d306bb75565022442d7da8cf2b9a`.

The application has a meaningful security baseline for a local/VPN-style
FastAPI + SQLite clinical workflow tool: server-side sessions, hashed session
tokens, Argon2 password hashing, CSRF on most writes, strict CSP, no CORS
middleware, centralized patient-scope policy, scoped analytics/data-quality
aggregation, bounded imports, no real SMS provider, and no AI integration.

The audit does not mark the project production-ready. The largest confirmed
gap is repository governance: no GitHub workflows were present and branch
protection was not visible for `main`. Application-level findings are mostly
metadata exposure, hardening, and deployment assumptions rather than confirmed
PHI disclosure or privilege escalation.

Pilot readiness: CONDITIONALLY READY.

Rationale: no Critical findings were confirmed. One High finding affects
repository/CI governance. Medium findings have clear compensating controls for
a controlled pilot but should be addressed before broader production exposure.

## Scope

- FastAPI routes and dependencies.
- Authentication, sessions, CSRF, RBAC, BOLA/IDOR, BFLA.
- Patient CRUD, import/export, data quality, notifications, analytics.
- Audit logging, SQLite, backup/restore CLI, static frontend, CSP.
- Dependencies and read-only GitHub repository baseline.

## Exclusions

No production testing, real PHI, real phones, real SMS, AI, scheduler,
destructive testing, uncontrolled fuzzing, GitHub settings changes, production
code fixes, or PR merge.

## Methodology

The audit used source review, route inventory, existing local tests, bounded
local TestClient checks, grep-based static review, `pip-audit`, `check-db`, JS
syntax fallback through `vm.Script`, read-only GitHub API checks, and selected
checklist structure from the external skills listed in `SECURITY_TEST_PLAN.md`.

## Tools

| Tool/check | Result |
|---|---|
| `.venv/bin/python -m pytest backend/tests -q` | `249 passed, 1 warning in 44.91s` |
| `.venv/bin/pip-audit -r backend/requirements.txt` | No known vulnerabilities found |
| `.venv/bin/python -m backend.cli check-db` | OK, schema 3, FK violations 0 |
| system `node`/`nodejs` | Not installed |
| Node-backed `vm.Script` | `static/app.js` syntax passed |
| `bandit` | Not installed |
| `semgrep` | Not installed |
| `gitleaks` | Not installed |
| `detect-secrets` | Not installed |
| GitHub workflows API | `total_count: 0` |
| GitHub branch protection API | 404 for `main` |

## Attack Surface Inventory

FastAPI route decorators found: 63.

Write route decorators found: 35.

Routes with explicit `Depends(require_csrf)`: 31.

The four non-GET routes without explicit CSRF are login, logout, import preview,
and migration preview. Login and preview endpoints are not ordinary authenticated
mutations. Logout is recorded as finding `SEC-2026-002`.

Conditional OpenAPI route: `/openapi.json` is present and returned 200 in
`DEBUG=false`; finding `SEC-2026-001`.

Post-audit hardening update: `fix/application-security-hardening` increases
explicit CSRF-protected write routes to 32/35 by protecting logout. The three
remaining POST routes without CSRF are login, import preview and migration
preview, each documented as N/A for cookie-authenticated stored-data mutation.
`/openapi.json` is now disabled outside debug.

### Endpoint Matrix

The detailed machine-readable control matrix is in
`security-audit/control-matrix.csv`.

Summary by area:

| Area | Auth | Authorization/scope | CSRF | Result |
|---|---|---|---|---|
| Static `/`, `/static/{filename}` | Public | Allowlisted files | N/A | PASS |
| Auth login/me | Login public, me session-aware | User status checked | Login N/A | PASS |
| Auth logout | Session required | Revokes current validated session | Required | PASS |
| Password change | Session | Own account | Required | PASS |
| Patient CRUD/history/plans | Required | Patient scope and write policy | Required on writes | PASS |
| Unresolved patients | Required | Developer/global read-only view; developer assignment | Required on assignment | PASS |
| Staff/users | Required | Developer only | Required on writes | PASS |
| Departments | Required | Developer only | Required on writes | PASS |
| Import preview/commit | Required | Role and assignment scope | Commit required | PASS |
| Export patients | Required | Role and patient scope | N/A GET export | PASS |
| Migration preview/commit | Required | Developer and empty-server controls | Commit required | PASS |
| Audit log | Required | Developer only | N/A read-only | PASS |
| Data quality | Required | Patient scope before findings | N/A read-only | PASS |
| Notifications | Required | Patient scope/developer worker | Required on writes | PASS |
| Analytics | Required | Scope before aggregation/details/export | Export required | PASS |

## Control Counts

PASS: 31

PARTIAL: 10

FAIL: 0

N/A: 6

NOT VERIFIED: 8

## Findings by Severity

Critical: 0

High: 1

Medium: 3

Low: 2

Informational: 1

## Key Results

Auth audit: PASS/PARTIAL. Passwords use Argon2 via `PasswordHasher`; login
enumeration is reduced by uniform 401; failed login is rate-limited; no
plaintext passwords or password hashes are returned by APIs. Production cookie
`Secure` depends on `CLINIC_HTTPS`.

Session audit: PASS/PARTIAL. Opaque server-side sessions store only token hash;
login creates a fresh session; password reset/role/status changes terminate
sessions; expiration and purge exist. Idle and absolute timeout are effectively
the same configured TTL, and HTTPS deployment controls are environmental.

CSRF audit: PASS after application hardening. Authenticated writes require
`x-csrf-token` matching the server-side session, including logout. Login and
preview-only import/migration routes are documented as CSRF N/A.

RBAC audit: PASS. Developer, global read-only roles, department head, and doctor
rules are centralized in `policy.py` and covered by `test_rbac_matrix.py`,
`test_rbac_controls.py`, `test_notifications.py`, and `test_analytics.py`.

BOLA/IDOR audit: PASS. Patient, notification, data-quality, analytics, import,
and export flows apply scope before object access or aggregation. Inaccessible
patient and notification IDs use 404/empty-list contracts.

BFLA audit: PASS. Staff, department, audit, template update, retry, and worker
trigger operations are developer-only.

Mass assignment audit: PASS after application hardening. Request/import models
reject unknown JSON fields. Explicit read-only export fields are accepted only
for import/export roundtrip compatibility and are ignored for authoritative
writes.

Injection audit: PASS/PARTIAL. SQL uses parameterized queries and allowlisted
sort/order literals in reviewed paths. No URL fetch/XML parsing was found, so
SSRF/XXE are N/A. Command/path injection is mostly CLI/operator-controlled with
SQLite validation and backup API; symlink/race hardening was not exhaustively
verified.

Import/export audit: PASS. Imports are bounded, validated, atomic, scoped, and
audited with counts only. Patient export is scoped. Analytics CSV applies
formula-injection protection and default minimization.

XSS/DOM audit: PASS. CSP forbids inline script/style/eval; frontend tests assert
no `innerHTML`, inline handlers/styles, or external resources. XSS payload tests
round-trip as text.

Headers/CSP audit: PASS/PARTIAL. CSP, nosniff, referrer policy, permissions
policy, no-store on `/api/*`, and HSTS when `CLINIC_HTTPS=true` are present.
HSTS is environmental.

CORS audit: PASS. No CORS middleware was installed; wildcard CORS tests pass.

Logging/PHI audit: PASS/PARTIAL. Audit tests verify secrets/medical data are not
stored in audit events. General stdout/stderr/browser-console PHI checks were
bounded, not exhaustive.

SQLite/filesystem audit: PASS/PARTIAL. FK, integrity, schema, indexes, backup
validation, static non-exposure, and `.gitignore` coverage exist. Encryption at
rest relies on deployment.

Backup/restore audit: PASS/PARTIAL. SQLite backup API, validation, atomic
replace, and permission restriction exist. Encrypted backups are not implemented.

Notification audit: PASS. Consent, phone validation, template allowlist,
idempotency, retry/lease recovery, MockProvider no network, developer worker,
scope, read-only restrictions, and no-PHI audit are covered by tests.

Analytics audit: PASS. Scope before aggregation/pagination/details/export,
CSV injection protection, no phone/SMS body/medical fields by default, timezone
handling, and no doctor ranking are covered by tests and source review.

Rate limiting audit: PARTIAL. Login has a real in-memory limiter; selected
heavy/search endpoints now have bounded in-memory limits with 429 and
`Retry-After`. Multi-process production still needs persistent or reverse-proxy
throttling.

Dependency audit: PASS/PARTIAL. `pip-audit` found no known vulnerabilities.
Dependencies are pinned in `requirements.txt`, and a pinned `dependency-audit`
CI job was added. Hash-pinned Python application installs are still not used.

SAST: PARTIAL. A pinned Bandit CI job was added for `backend/app`. Current B608
findings were manually reviewed as false positives and are allowed only by exact
path, line and test ID; new Medium/High findings fail the job.

Secret scan current tree: PARTIAL. A pinned Gitleaks CI job scans the working
tree with redacted output. Local Gitleaks 8.28.0 found no leaks.

Secret scan Git history: PARTIAL. The same Gitleaks CI job scans Git history
using `fetch-depth: 0`. Local Gitleaks 8.28.0 scanned 34 commits with no leaks.

GitHub Actions audit: PARTIAL. Least-privilege workflows were added for pytest,
temporary DB check, JS/CSP checks, workflow policy, pip-audit, Bandit and
Gitleaks. Protection settings and required checks are not applied yet.

Branch protection audit: NOT VERIFIED. Protection API returned 404 for `main`;
this may mean no protection or insufficient access. No settings were changed.

Production configuration audit: PARTIAL. Safe defaults exist for debug false,
no CORS, trusted hosts, mock notifications, and check-db. Production HTTPS/HSTS,
disk encryption, monitoring, log rotation, RPO/RTO, and reverse-proxy trust are
deployment responsibilities.

## Residual Risks

Top risks:

1. GitHub CI baseline is added, but branch protection and required checks are
not applied yet.
2. Heavy endpoint throttling is still per-process and not persistent.
3. Production OpenAPI exposure is fixed, but debug mode intentionally exposes
the schema for local development.
4. App-level DB/backup encryption absent.
5. Backup/database encryption remains separate work.
6. Strict request schemas and logout CSRF are covered by regression tests.
7. GitHub Advanced Security settings not verified.
8. SAST/secret scanners unavailable locally.
9. Production controls rely on operator deployment checklist.
10. Future real SMS provider needs delivery idempotency design before use.

## Readiness Conclusion

CONDITIONALLY READY for a controlled pilot behind trusted network controls,
encrypted host storage, operator procedures, and manual local checks.

NOT READY for broad production exposure until repository governance, production
OpenAPI policy, abuse controls, and backup/encryption posture are addressed.
