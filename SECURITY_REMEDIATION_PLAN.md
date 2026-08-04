# Security Remediation Plan

This document tracks the audit findings and the remediation stage that owns
each item. The Application Security Hardening stage addresses only
application-level controls; backup encryption and GitHub repository settings
remain separate work.

## P0 - Critical

No Critical findings were confirmed.

## P1 - High

### SEC-2026-004: Repository lacks visible CI workflows and branch protection baseline

Expected change: Add branch protection for `main`, required PR reviews,
required status checks, no routine admin bypass, and CI workflows for pytest,
pip-audit, JS syntax/CSP checks, SAST, and secret scanning.

Current remediation branch status: PARTIAL. CI/security workflows, Dependabot
configuration, CODEOWNERS, SECURITY.md, PR template and a post-merge settings
plan are added. Branch protection, required checks and GitHub-native security
features still require a separate confirmed settings step after merge.

Regression tests: GitHub checks must appear on every PR and block merge on
failure.

Migration risk: None to application data.

Deployment risk: Medium operational risk because stricter repository controls
can block emergency changes until workflows are stable.

Recommended PR: `fix/github-security-baseline`, followed by a separate
post-merge repository settings step.

## P2 - Medium

### SEC-2026-001: OpenAPI schema remains exposed when debug docs are disabled

Expected change: Disable `openapi_url` outside debug or gate it by developer
auth.

Application Security Hardening status: REMEDIATED. `openapi_url` is disabled
when `CLINIC_DEBUG=false`.

Regression tests: `/api/docs`, `/redoc`, and `/openapi.json` return 404 when
`CLINIC_DEBUG=false`.

Migration risk: None.

Deployment risk: Low.

Recommended PR: `fix/application-security-hardening`.

### SEC-2026-003: Rate limiting is login-focused and per-process only

Expected change: Add persistent or reverse-proxy-backed throttling for login and
heavy endpoints; document trusted proxy behavior.

Application Security Hardening status: PARTIAL. Authenticated heavy/search
endpoints now have bounded in-memory 429/`Retry-After` controls. This does not
replace deployment-level or persistent throttling for multi-process production.

Regression tests: 429/Retry-After tests for login, analytics export, import,
data quality, notification worker trigger, and staff search.

Migration risk: Possible new table/cache if implemented in SQLite.

Deployment risk: Medium because limits can affect operators.

Recommended PR: `fix/application-security-hardening` for application bounds;
persistent/deployment limiter remains a later operational hardening item.

### SEC-2026-006: PHI at rest relies on filesystem and disk controls

Expected change: Formalize encrypted disk/backups for pilot. If backups leave
the host, add encrypted backup packaging and key handling.

Application Security Hardening status: OUT OF SCOPE. Backup/database encryption
is intentionally not included in this PR.

Regression tests: Backup permission tests, backup encryption verification if
implemented.

Migration risk: Medium if database encryption is introduced.

Deployment risk: Medium due key management.

Recommended PR: `chore/encrypted-backup-policy`.

## P3 - Low/Hardening

### SEC-2026-002: Logout endpoint is state-changing but does not require CSRF

Expected change: Add CSRF dependency to logout or explicitly document acceptable
logout-CSRF behavior.

Application Security Hardening status: REMEDIATED. Logout requires an active
session and matching session-bound CSRF token before revoking the session and
clearing session/CSRF cookies.

Regression tests: Logout without CSRF returns 403 if fixed.

Migration risk: None.

Deployment risk: Low; frontend already can send CSRF.

Recommended PR: `fix/application-security-hardening`.

### SEC-2026-005: Request schemas generally ignore unknown JSON fields

Expected change: Add `extra="forbid"` to request/import Pydantic models.

Application Security Hardening status: REMEDIATED. Production request schemas
now reject unknown JSON fields with strict Pydantic models. Known read-only
export fields remain explicitly accepted for import/export roundtrip
compatibility and are ignored for server-authoritative writes.

Regression tests: Unknown privilege/audit/provider fields return 422 across
representative endpoints.

Migration risk: Low to medium; clients that send stale fields will fail.

Deployment risk: Low after frontend verification.

Recommended PR: `fix/application-security-hardening`.

### SEC-2026-007: GitHub Advanced Security and vulnerability-alert settings were not verifiable

Expected change: Repository owner verifies and documents Dependabot alerts,
dependency graph, secret scanning, push protection, and code scanning.

Application Security Hardening status: OUT OF SCOPE. No GitHub settings are
changed in this PR.

Regression tests: Read-only GitHub API checks or captured repository settings.

Migration risk: None.

Deployment risk: None.

Recommended PR: `docs/github-security-settings`.
