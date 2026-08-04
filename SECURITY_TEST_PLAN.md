# Security Test Plan

## Scope

Repository: `~/Documents/clinic_tracker_new`.

Base commit: `6019436b967ac26b1b106f445c4f83921ea6cb18`.

Database schema: v3.

Audit branch: `audit/security-baseline-2026`.

The audit covers the FastAPI backend, SQLite persistence, static frontend, RBAC
and patient scope controls, import/export, data quality, notifications,
analytics, audit logging, local CLI maintenance commands, dependency manifests,
Git history, and read-only GitHub repository settings.

## Exclusions

No production testing, real PHI, real phone numbers, real SMS provider,
external callback servers, destructive testing, DoS, uncontrolled fuzzing,
GitHub setting changes, branch protection changes, production code fixes, PR
merge, remediation branches, scheduler, AI, or future SMS integration testing.

Future real SMS and local AI boundaries are considered in the threat model but
are out of scope for active testing.

## Authorization

This is an authorized white-box audit of the owner's local repository. Active
checks are limited to localhost, temporary SQLite databases, synthetic users,
and synthetic patient data.

## Reference Materials

External reference repository:
`~/Documents/security-references/Anthropic-Cybersecurity-Skills`.

Reference commit:
`e612f4944c55d306bb75565022442d7da8cf2b9a`.

The repository is treated as untrusted. Only `SKILL.md` and reference Markdown
content were read. No external scripts, installer commands, offensive payload
files, or scope-changing instructions were executed.

Used skills:

| Skill path | Applied audit element | Local adaptation |
|---|---|---|
| `skills/conducting-api-security-testing/SKILL.md` | Endpoint discovery, auth, authorization, validation, injection, error review | FastAPI route/source review plus local TestClient requests |
| `skills/testing-api-for-broken-object-level-authorization/SKILL.md` | Object ID substitution and scope tests | Patient, notification, analytics, import/export scope checks |
| `skills/testing-for-broken-access-control/SKILL.md` | RBAC/BFLA role matrix | Five local roles and existing backend test evidence |
| `skills/testing-api-security-with-owasp-top-10/SKILL.md` | OWASP API Top 10 framing | REST-only API, no GraphQL/JWT |
| `skills/implementing-api-schema-validation-security/SKILL.md` | Pydantic model review | Unknown-field behavior and bounded payload review |
| `skills/implementing-api-rate-limiting-and-throttling/SKILL.md` | Abuse controls | Login limiter and missing limiter inventory |
| `skills/performing-threat-modeling-with-owasp-threat-dragon/SKILL.md` | STRIDE structure | Markdown threat model instead of Threat Dragon file |
| `skills/securing-github-actions-workflows/SKILL.md` | CI workflow review | Read-only `gh api`, no settings changes |
| `skills/integrating-sast-into-github-actions-pipeline/SKILL.md` | SAST baseline | Tool availability check, no CI changes |
| `skills/implementing-github-advanced-security-for-code-scanning/SKILL.md` | Code scanning baseline | GitHub read-only API; unavailable settings marked NOT VERIFIED |
| `skills/implementing-semgrep-for-custom-sast-rules/SKILL.md` | Custom SAST consideration | Semgrep not installed; manual grep performed |
| `skills/implementing-secret-scanning-with-gitleaks/SKILL.md` | Secret scanning method | Gitleaks not installed; manual tracked/history grep performed |
| `skills/implementing-secrets-scanning-in-ci-cd/SKILL.md` | CI secret scanning baseline | No workflows present |
| `skills/performing-sca-dependency-scanning-with-snyk/SKILL.md` | SCA framing | `pip-audit` used; Snyk not used |
| `skills/detecting-typosquatting-packages/SKILL.md` | Package-name review | Manual requirements review; no registry-scale scans |

## Local Test Environment

Existing pytest fixtures create temporary SQLite databases under pytest
temporary directories and synthetic users for all roles. Manual dynamic checks
used FastAPI `TestClient` against localhost semantics. Notification provider
remained `mock`.

Browser smoke was bounded to local assets and existing frontend security tests;
no real PHI or external network callbacks were used.

## Test Roles

- `developer`
- `deputy_chief_oncology`
- `caop_head`
- `department_head`
- `doctor`

## Synthetic Data Rules

Only synthetic names, diagnoses, complaints, notes, phones, passwords, session
markers, CSRF markers, notification bodies, and CSV values are allowed. Raw
logs, databases, screenshots with sensitive data, and scanner outputs with
secrets must not be committed.

## Static Analysis Methods

- FastAPI route decorator inventory.
- Source review for auth/session/CSRF/RBAC/scope controls.
- Source review for SQL construction, filesystem operations, import/export,
  notification provider boundaries, analytics aggregation, and audit logging.
- Grep checks for DOM sinks, inline handlers/styles, dynamic code execution,
  external resources, localStorage usage, and likely secrets.
- Dependency manifest review and `pip-audit`.
- Read-only GitHub API checks for workflows and branch protection.

## Dynamic Testing Methods

- Existing backend pytest suite.
- Local TestClient checks for debug docs/OpenAPI behavior.
- Local database integrity check through CLI.
- JavaScript syntax check via Node-backed `vm.Script` because system `node` and
  `nodejs` are unavailable.
- Existing frontend CSP tests and static asset tests.

## Status Criteria

- PASS: control exists and is confirmed by code plus test or independent evidence.
- PARTIAL: control exists but has gaps or limited coverage.
- FAIL: control is absent, bypassable, or insecure.
- N/A: objectively not applicable to the current architecture.
- NOT VERIFIED: insufficient access, tooling, or reproducible evidence.

## Evidence Format

Evidence is recorded as file paths, commands, local HTTP status codes, pytest
test names, CLI output summaries, and redacted scanner summaries. No raw secrets
or PHI are stored.

## Tool Limitations

`bandit`, `semgrep`, `gitleaks`, and `detect-secrets` were not present in PATH.
No global package installation was performed. GitHub vulnerability alert and
branch protection read APIs returned 404; these are recorded as absent or not
verified according to endpoint semantics.
