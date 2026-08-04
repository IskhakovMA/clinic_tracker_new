# Security Findings

## SEC-2026-001: OpenAPI schema remains exposed when debug docs are disabled

Severity: Medium

Confidence: High

Affected component: FastAPI application metadata.

Affected endpoint: `GET /openapi.json`.

CWE: CWE-200.

OWASP: API3:2023 - Broken Object Property Level Authorization / excessive
information exposure; Security Misconfiguration.

Source skill: `skills/conducting-api-security-testing/SKILL.md`.

Description: `docs_url` is disabled when `CLINIC_DEBUG=false`, but `openapi_url`
is not set to `None`. FastAPI therefore exposes `/openapi.json` in the default
production-like configuration.

Prerequisites: Network access to the application origin.

Local reproduction:

```text
TestClient GET /api/docs -> 404
TestClient GET /redoc -> 404
TestClient GET /openapi.json -> 200 application/json
```

Evidence: `backend/app/main.py` sets `docs_url="/api/docs" if config.DEBUG else
None` but leaves `openapi_url` default. Local TestClient confirmed status 200
for `/openapi.json` with `debug=False`.

Impact: Unauthenticated callers can enumerate route names, schemas, and field
names. This does not directly expose patient data, but it reduces attacker
effort.

Existing controls: Interactive docs are disabled outside debug; auth is still
required for protected API data.

Remediation: Set `openapi_url="/openapi.json" if config.DEBUG else None` or
serve the schema only to developer role in production.

Regression test recommendation: Extend docs-disabled test to assert
`/openapi.json` returns 404 when debug is false.

Exploitability: Low to medium; easy to access, metadata only.

Data sensitivity: API structure only.

Residual risk: Low after disabling production schema exposure.

## SEC-2026-002: Logout endpoint is state-changing but does not require CSRF

Severity: Low

Confidence: High

Affected component: Authentication/session lifecycle.

Affected endpoint: `POST /api/auth/logout`.

CWE: CWE-352.

OWASP: Broken Access Control / CSRF.

Source skill: `skills/conducting-api-security-testing/SKILL.md`.

Description: Most write endpoints use `Depends(require_csrf)`, but logout does
not. A cross-site request could terminate a victim session if browser SameSite
rules permit the request.

Prerequisites: Victim has an active session and browser sends cookies on the
cross-site POST.

Evidence: `backend/app/routes/auth.py` decorates logout as `@router.post("/logout")`
without CSRF dependency.

Impact: Session disruption/log out. No data write or PHI disclosure was found.

Existing controls: Cookie `SameSite=Lax` reduces common cross-site POST cookie
delivery; session tokens are HttpOnly and server-side.

Remediation: Require CSRF on logout, or document an explicit acceptable logout
CSRF policy and use a safe frontend flow.

Regression test recommendation: POST logout without `x-csrf-token` should return
403 if remediated.

Exploitability: Low.

Data sensitivity: No direct PHI exposure.

Residual risk: Very low after CSRF enforcement.

## SEC-2026-003: Rate limiting is login-focused and per-process only

Severity: Medium

Confidence: High

Affected component: Abuse controls.

Affected endpoints: login has partial protection; import, export, analytics,
data quality, notification worker trigger, staff search, and backup/restore CLI
have no comparable HTTP rate limiter.

CWE: CWE-770.

OWASP: API4:2023 - Unrestricted Resource Consumption.

Source skill: `skills/implementing-api-rate-limiting-and-throttling/SKILL.md`.

Description: Login rate limiting exists but is stored in process memory. Other
expensive or bulk endpoints rely on bounded payloads, pagination, and RBAC, but
not request throttling.

Prerequisites: Authenticated user for heavy endpoints; repeated local requests.

Evidence: `backend/app/security.py` contains `_failed_attempts` and comments
that it is in-memory/per-process. No route-level throttling middleware or
persistent limiter was found.

Impact: Controlled but repeated requests could consume CPU/IO. Restart clears
login counters. Proxy/IP spoofing behavior is not hardened for distributed
deployment.

Existing controls: Import max size and max records, pagination limits, bounded
worker batch, login 429 with `Retry-After`.

Remediation: Add persistent or deployment-level rate limiting for login and
heavy endpoints; document trusted proxy behavior.

Regression test recommendation: Bounded 429 tests for login, analytics export,
data-quality issues, notification worker trigger, and import.

Exploitability: Medium in exposed deployments, lower on VPN/local deployments.

Data sensitivity: Availability of clinical workflow.

Residual risk: Medium until deployment controls exist.

## SEC-2026-004: Repository lacks visible CI workflows and branch protection baseline

Severity: High

Confidence: Medium

Affected component: GitHub repository controls.

Affected endpoints/actions: GitHub `main` branch governance.

CWE: CWE-693.

OWASP: CI/CD security misconfiguration.

Source skills: `skills/securing-github-actions-workflows/SKILL.md`,
`skills/integrating-sast-into-github-actions-pipeline/SKILL.md`,
`skills/implementing-github-advanced-security-for-code-scanning/SKILL.md`.

Description: Read-only GitHub API reported zero workflows. Branch protection
API for `main` returned 404. PR #8 also reported no checks. This means local
checks are not enforced by GitHub before merge.

Prerequisites: Maintainer access or compromised maintainer token.

Evidence:

```text
gh api repos/applehooked-cmd/clinic_tracker_new/actions/workflows -> {"total_count":0}
gh api repos/applehooked-cmd/clinic_tracker_new/branches/main/protection -> 404
```

Impact: Direct pushes, force pushes, or unreviewed merges may be possible
depending on repository settings not visible via protection. Security tests,
SCA, SAST, and secret scanning are not enforced in CI.

Existing controls: Manual local checks and PR discipline.

Remediation: Add protected `main`, required PR reviews, required status checks,
least-privilege workflow permissions, dependency/SAST/secret scans, and no admin
bypass for normal merges.

Regression test recommendation: Repository governance checklist and CI status
checks on every PR.

Exploitability: Medium; depends on GitHub permissions.

Data sensitivity: Source code, release integrity, future secrets.

Residual risk: High until repository controls are enforced.

## SEC-2026-005: Request schemas generally ignore unknown JSON fields

Severity: Low

Confidence: Medium

Affected component: Pydantic input validation.

Affected endpoints: JSON request bodies using default Pydantic `BaseModel`
behavior.

CWE: CWE-915.

OWASP: API3/API6 - Mass assignment and unsafe consumption of APIs.

Source skill: `skills/implementing-api-schema-validation-security/SKILL.md`.

Description: The code uses allowlisted model fields and server-side authority
for IDs, audit, roles, timestamps, and provider status. However, Pydantic's
default behavior ignores unknown fields unless `extra="forbid"` is configured.
Privilege fields such as `role`, `actor`, `created_by`, or provider state are
not assigned by reviewed code, but silently ignored extras reduce validation
strictness and can hide client bugs or future mass-assignment regressions.

Prerequisites: Authenticated caller sends extra JSON fields.

Evidence: `backend/app/models.py` request models do not declare
`model_config = ConfigDict(extra="forbid")`.

Impact: No current privilege escalation confirmed. Future model changes may
accidentally make ignored fields meaningful.

Existing controls: Literal allowlists, bounded field lengths, server-generated
IDs/timestamps, policy checks, import dangerous-key rejection for prototype
pollution keys.

Remediation: Add strict `extra="forbid"` to request/import models and update
frontend/tests accordingly.

Regression test recommendation: Per-endpoint tests that unknown privilege fields
return 422.

Exploitability: Low currently.

Data sensitivity: Depends on future field additions.

Residual risk: Low after strict schemas.

## SEC-2026-006: PHI at rest relies on filesystem and disk controls, not application encryption

Severity: Medium

Confidence: High

Affected component: SQLite database and backups.

Affected paths: `backend/data/clinic.db`, WAL/SHM, `backend/data/backups/*.db`.

CWE: CWE-311.

OWASP: Cryptographic Failures.

Source skill: `skills/conducting-api-security-testing/SKILL.md`.

Description: The application stores medical data in SQLite and backups without
application-level encryption. Documentation requires disk encryption and backups
are chmod 0600, but app-level encryption, backup encryption, and key management
are not implemented.

Prerequisites: Local filesystem access, backup file disclosure, or compromised
host account.

Evidence: `backend/app/maintenance.py` creates SQLite backups and restricts
permissions to 0600; README states disk encryption is required and app-level DB
encryption is not implemented.

Impact: If host filesystem controls fail, patient data may be readable from DB,
WAL/SHM, or backups.

Existing controls: DB/backups excluded from Git and static serving; backup
validation; restricted file permissions; README deployment requirement for disk
encryption.

Remediation: For pilot, enforce full-disk encryption operationally. Consider
encrypted backup archives or SQLCipher/key management if deployment assumptions
require portable encrypted backups.

Regression test recommendation: File permission tests plus deployment checklist
verification.

Exploitability: Depends on host compromise/local access.

Data sensitivity: High, includes medical and contact data.

Residual risk: Medium under single-host encrypted-disk deployment, higher if
backups leave the host unencrypted.

## SEC-2026-007: GitHub Advanced Security and vulnerability-alert settings were not verifiable

Severity: Informational

Confidence: Medium

Affected component: GitHub repository security visibility.

Source skills: `skills/implementing-github-advanced-security-for-code-scanning/SKILL.md`,
`skills/implementing-secret-scanning-with-gitleaks/SKILL.md`.

Description: Read-only API calls for vulnerability alerts returned 404. For a
private repository this can mean disabled feature, plan limitation, or
insufficient API visibility. The audit cannot mark dependency graph, secret
scanning, push protection, or code scanning as PASS.

Evidence: `gh api repos/applehooked-cmd/clinic_tracker_new/vulnerability-alerts -i`
returned HTTP 404.

Impact: Repository security features may be absent or simply not visible to the
current token.

Remediation: Repository owner should verify Dependabot alerts, dependency graph,
secret scanning, push protection, and code scanning in GitHub settings.

Regression test recommendation: Document screenshots/settings export or automate
read-only checks with a token that can read security settings.
