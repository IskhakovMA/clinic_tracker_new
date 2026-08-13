# Final Pilot Readiness Review

Review date: 2026-08-13

Base commit: `e7342bb3deec786f30923bb5f08fc24cbb3c7c65`

Branch: `audit/pilot-readiness-2026`

Scope: audit-only review of the current "Система планирования" main branch after Analytics V2, OMS policy number, Internal Notification Center, Patient Attention Workflow, and AI Assistant V1 were merged. No production code was changed in this review.

## Executive Summary

The system is **conditionally ready for a limited pilot** on synthetic or de-identified data in a controlled single-instance environment. Core clinical-route planning workflows, RBAC scope enforcement, internal notifications, attention workflow, analytics, data quality, encrypted backup/restore, strict frontend security checks, audit hygiene, and optional AI Assistant V1 boundaries are implemented and covered by automated tests.

The system is **not production-ready for unrestricted real medical data** without organisational and infrastructure controls outside this repository. The main blockers for real-data operation are infrastructure and operations: approved HTTPS hosting, encrypted live storage, backup key custody, formal user provisioning/deactivation, legal/privacy approval, incident handling, and approved internal AI inference infrastructure if AI is enabled.

Recommended decision: **CONDITIONAL GO** for a controlled pilot that starts with synthetic/de-identified data. Real patient data requires the Real-Data Gate below.

## Readiness Decision

Decision: **CONDITIONAL GO**

Rationale:

- Development-ready: **Pass**. Full backend tests, security checks, browser smoke, schema check, and backup drills passed locally and CI is green on `origin/main`.
- Pilot-ready with synthetic/de-identified data: **Conditional pass**. Suitable for a small controlled pilot when deployed single-instance behind approved HTTPS with encrypted storage and named administrators.
- Production-ready with real medical data: **No**. Requires operational, legal/privacy, infrastructure, monitoring, and data-governance work not contained in the repository.

## Pilot Scope

Recommended pilot scope:

- Start with synthetic or de-identified data only.
- Use one controlled department or a small nominated user group.
- Use fixed roles: developer administrator, doctor, department head, and read-only leadership roles.
- Run a time-boxed feedback period with daily backups and explicit rollback.
- Keep outbound notifications in mock/internal mode unless a separate real messaging integration is approved.
- Run AI in Mode A disabled by default, or Mode B only with an approved internal inference endpoint.

## What Is Implemented

### Patients

Implemented: patient CRUD, OMS policy number, search, patient card, stage/history/events, planned and actual dates, checklist, notes, import/export, scoped access.

Limitations: import preview accepts backward-compatible patient payloads and ignores unexpected patient fields instead of rejecting the whole preview. This is useful for legacy import tolerance, but should be documented as compatibility behavior.

### Route

Implemented: stage route, planned dates, actual dates, event add/edit/remove, timeline, future plans, overdue detection.

Limitations: route semantics are operational tracking, not clinical decision support.

### Checklists

Implemented: checklist state per history occurrence, analytics and notifications integrate checklist signals.

Limitations: checklist content is local application logic and should be validated with pilot stakeholders.

### Employees / Structure

Implemented: developers manage users, roles, departments, doctor-department assignment, department heads, user disable/status, password reset through authenticated admin UI/API.

Limitations: CLI first-developer command is interactive and there is no non-interactive CLI reset-password command for an existing user.

### RBAC

Implemented roles: `developer`, `deputy_chief_oncology`, `caop_head`, `department_head`, `doctor`.

Doctor scope is by treating doctor. Department head scope is by managed departments. Leadership roles read within their configured scope/global policy. Write APIs enforce backend permissions.

### Data Quality

Implemented: summary, paginated issues, rule filters, patient deep-link, duplicate grouping, planned/actual/overdue checks, scope protection, 1000-patient smoke coverage.

Limitation: Data Quality is operational data completeness and consistency, not clinical advice.

### Analytics V2

Implemented: `/api/analytics/dashboard`, KPI cards, attention signals, operational signals, stages, profiles, route state, quality, workload, trend, 90-day cap, filters, CSV export with formula injection protection.

Patient-level attention KPI counts patients rather than raw issue rows.

### Notification Center

Implemented: internal notification summary/list, read/unread state, today, overdue, upcoming, checklist, quality, attention, stable keys, no duplicate active notifications, read state separate from condition resolution.

### Outbound Mock Notifications

Implemented: mock provider and notification queue/preferences/templates.

Limitation: real SMS/email/push provider is not implemented and is out of current pilot scope.

### Patient Attention Workflow

Implemented: create, close, multiple active issues, history, emergency hospitalization reason requiring event date, reason filters, analytics, notifications, RBAC, audit without PHI.

Semantics: emergency hospitalization is a structured operational reason. The system does not diagnose, determine medical urgency, or initiate hospitalization.

### AI Assistant V1

Implemented: optional read-only AI assistant, OpenAI-compatible provider, explicit tool allowlist, strict tool argument schemas, backend-only provider configuration, no stored conversations, no direct SQL/HTTP tools, scope enforced by backend tools.

Limitations: AI inference infrastructure is developer-local unless the organisation provides an approved internal inference server. AI status currently reports configured availability without probing provider health; actual chat fails safely with 503 when provider is offline.

### Audit

Implemented: login/logout, patient writes, import/export, user management, attention create/close, password changes, backup/restore and relevant admin actions. Tests check no PHI/secrets in audit details.

### Backup/Restore

Implemented: encrypted Clinic Backup Format v1 with AES-256-GCM, key file generation, inspect, verify, wrong-key rejection, tamper/truncation rejection, offline restore, recovery copy, `check-db`.

Limitation: encrypted backups do not encrypt the live SQLite DB file. Real-data pilot requires encrypted volume/storage.

### Security Hardening

Implemented: Argon2id, server-side sessions with hashed session tokens, CSRF for write endpoints, strict Pydantic schemas, CSP, no inline handlers/styles, no `innerHTML`, no CDN, RBAC/BOLA/BFLA tests, no API docs in default non-debug mode, no CORS middleware.

### CI/Security Checks

Implemented GitHub jobs: backend-tests, frontend-security, workflow-policy, dependency-audit, sast-bandit, secret-scan.

## End-to-End Results

### Doctor E2E

Synthetic API E2E result: pass.

Evidence:

- Login: 200.
- Patient list: 2 scoped patients.
- Search: returned scoped synthetic patients.
- Open patient: 200.
- OMS field: present and preserved.
- Route/events: visible.
- Patient edit: 200.
- Attention create: 201.
- Attention badge: active.
- Internal Notification Center attention item: total 1.
- Mark notification read: 200.
- Attention remains open after read: true.
- Attention close: 200.
- Notification resolves after close: total 0.
- Analytics dashboard: 200.
- Data Quality issues: 200.
- AI disabled status: controlled unavailable state.
- Logout: 200.

### Department Head E2E

Synthetic API E2E result: pass.

Evidence:

- Department head sees only managed department patients.
- Foreign department patient direct GET returns 404.
- Analytics dashboard returns 200 within scope.
- Data Quality summary returns 200 within scope.

### Read-only / Leadership Roles

Synthetic API E2E result: pass.

Evidence:

- Read-only leadership role can list/read allowed patients: 200.
- Direct create patient blocked: 403.
- Direct attention create blocked: 403.
- AI remains read-only by design.

## Security

Security posture is acceptable for a controlled pilot when deployed behind approved HTTPS with encrypted storage and explicit operational controls.

Headers tested by automated suite:

- Strict CSP without inline scripts/styles/eval.
- `X-Content-Type-Options: nosniff`.
- `Referrer-Policy`.
- `Permissions-Policy`.
- `Cache-Control: no-store` for API.
- `frame-ancestors 'none'`.

Default non-debug mode disables OpenAPI/docs. `CLINIC_DEBUG=true` is required to expose docs.

HTTPS is not optional for real-data pilot. `CLINIC_HTTPS=true` enables secure cookies and HSTS, but must be used only behind actual HTTPS termination.

## Privacy

Pass with pilot constraints.

- Frontend does not know AI API key.
- Provider config is backend-only.
- AI prompts/responses/tool results are not stored in DB/localStorage.
- AI operational logs do not log conversation content.
- AI V1 tools omit policy number.
- Audit tests cover absence of PHI/secret fields.
- Export/import and CSV formula protections are tested.

Internal patient IDs may appear in audit/security events according to current audit policy; this is acceptable as an internal technical identifier but should be covered by log access controls.

## RBAC

RBAC/BOLA/BFLA result: pass in automated tests and synthetic E2E.

Direct ID checks:

- Doctor foreign patient GET: 404.
- Doctor foreign attention GET: 404.
- Doctor foreign notifications GET: 404.
- Department head foreign patient GET: 404.
- Read-only direct writes: 403.
- AI patient context with disabled AI returned 503 before data access; existing AI tests cover foreign patient denial when AI is enabled.

## Backup/Recovery

Backup drill result: pass.

Evidence on schema v6 synthetic DB:

- Key generation: pass.
- Encrypted backup create: pass.
- Inspect: pass.
- Verify: pass, schema 6, patient count 1.
- Wrong key rejection: command exited non-zero.
- Tamper rejection: command exited non-zero.
- Truncation rejection: command exited non-zero.
- Offline restore: pass.
- Recovery copy: created by restore command.
- Restored `check-db`: integrity ok, FK violations 0, schema 6.
- OMS preserved: `7700000000099999`.
- Attention preserved: open issue preserved.
- Internal notification read state preserved in API drill: `read_at` row count 1 after restore.

Operational note: backup key loss makes encrypted backups unrecoverable.

## AI Safety

Prior live smoke on PR #23 and this review support AI as optional pilot capability.

Verified previously with LM Studio/Qwen:

- Endpoint: `http://127.0.0.1:1234/v1`.
- Model: `qwen3-8b`.
- Real tool calling.
- Multi-turn follow-up.
- Patient timeline.
- Read-only boundary.
- Medical safety refusal.
- Prompt injection resistance.
- RBAC foreign-patient boundary.
- No malformed tool calls.

This review did not require a full repeat because LM Studio can be unavailable. AI disabled/offline behavior was retested:

- `CLINIC_AI_ENABLED=false`: core patients/analytics/notifications/attention work; `/api/ai/chat` returns 503.
- AI enabled with provider offline: core workflows work; `/api/ai/chat` returns 503.

Finding: `/api/ai/status` reports configured availability without probing the provider. See `PILOT-2026-004`.

## AI Infrastructure

Development architecture:

```text
Mac
  -> LM Studio
  -> qwen3-8b
```

Recommended pilot architecture:

```text
User browser
  -> HTTPS reverse proxy
  -> FastAPI / RBAC / AI Tools
  -> Internal-only network
  -> Dedicated AI inference server
  -> Qwen / OpenAI-compatible inference
```

AI server may be a separate GPU server, preferred. The same physical host may be acceptable for a small controlled pilot if resources and isolation are sufficient. For real data, the inference provider must be inside approved infrastructure with restricted network access and no browser-accessible endpoint.

## Operations

Operational readiness is partial. `PILOT_RUNBOOK.md` provides the current start/stop/backup/restore/user-management/AI-unavailable procedures.

The biggest operational gaps are outside code: hosting, HTTPS, encrypted live storage, backup key custody, log access control, incident handling, and legal/privacy approval.

## Performance

No production load test was performed. Existing automated tests include:

- Data Quality 1000-patient smoke without per-patient N+1 behavior.
- Internal Notification Center 1000-patient smoke.
- Analytics query-count coverage for bulk data-quality evaluation.

Pilot consequence: acceptable for a small single-instance pilot, but not proof of production scalability.

## Browser/UX

Chrome smoke result: pass.

Screenshots:

- `docs/pilot-readiness/screenshots/01-doctor-workspace.png`
- `docs/pilot-readiness/screenshots/02-analytics.png`
- `docs/pilot-readiness/screenshots/03-notification-center.png`
- `docs/pilot-readiness/screenshots/04-data-quality.png`
- `docs/pilot-readiness/screenshots/05-attention-patient-card.png`
- `docs/pilot-readiness/screenshots/06-ai-disabled-drawer.png`

Responsive smoke:

- 1440: horizontal scroll false.
- 1024: horizontal scroll false.
- 768: horizontal scroll false.
- 375: horizontal scroll false.
- 375 zoom 125: horizontal scroll false.
- 375 zoom 150: horizontal scroll false.
- 375 zoom 200: horizontal scroll false.

Page JS errors: 0. Page network failures: 0.

Headless Chrome stderr produced external Google/GCM/Crashpad messages unrelated to application page errors.

Other browsers were not tested in this review.

## Known Limitations

- SQLite is used as live DB.
- Live DB encryption depends on OS/volume/infrastructure encryption.
- Rate limiting is in-memory and per-process.
- GitHub required-check enforcement/rulesets were not independently verified beyond CI success.
- Trusted proxy behavior must be validated in deployment.
- AI inference is developer-local unless pilot infrastructure supplies an approved internal server.
- Real SMS/email/push is not implemented.
- `create-developer` CLI is interactive; no CLI `set-password` or `reset-password` command exists for existing users.
- AI provider health is not probed by `/api/ai/status`.
- Import compatibility path accepts unknown legacy patient fields during preview.

## Findings

### PILOT-2026-001

Severity: High

Status: Open

Subject: Real-data pilot requires approved infrastructure and organisational controls.

Evidence: Repository provides secure app controls and encrypted backups, but hosting, HTTPS termination, encrypted live storage, legal/privacy approval, incident response, retention, and operational access policy are outside the repository.

Impact: Real medical data could be exposed or mishandled if run from developer-local HTTP or unapproved storage.

Pilot consequence: Synthetic/de-identified pilot can proceed; real patient data cannot proceed until Real-Data Gate is satisfied.

Recommendation: Complete Real-Data Gate before any real patient data.

### PILOT-2026-002

Severity: Medium

Status: Open

Subject: Rate limiter is in-memory and per-process.

Evidence: `backend/app/security.py` stores login/request buckets in process dictionaries; config caps bucket counts.

Impact: Limits do not coordinate across multiple application processes/hosts and reset on restart.

Pilot consequence: Acceptable for a single-instance controlled pilot; not sufficient for multi-instance production.

Recommendation: Use reverse-proxy/shared limiter for pilot perimeter; consider Redis/shared backend before multi-instance deployment.

### PILOT-2026-003

Severity: High

Status: Open

Subject: Live SQLite DB encryption depends on infrastructure.

Evidence: Application encrypts backups, not the live SQLite DB file. README documents encrypted storage requirement.

Impact: Data at rest in the live DB is only protected if the host volume/storage is encrypted and access-controlled.

Pilot consequence: Synthetic/de-identified pilot can proceed. Real-data pilot requires approved encrypted storage.

Recommendation: Deploy on encrypted volume/storage with restricted filesystem access and backup key separation.

### PILOT-2026-004

Severity: Low

Status: Open

Subject: AI status endpoint does not probe provider health.

Evidence: With `CLINIC_AI_ENABLED=true`, offline `CLINIC_AI_BASE_URL`, and model set, `/api/ai/status` returned enabled/available while `/api/ai/chat` failed safely with 503.

Impact: Users may see AI as available until the first failed chat.

Pilot consequence: Non-blocking because AI is optional and core workflows continue.

Recommendation: Add provider health probe or clearer "configured, not health-checked" status in a separate remediation PR.

### PILOT-2026-005

Severity: Medium

Status: Open

Subject: No non-interactive CLI password reset for existing users.

Evidence: `backend/cli.py` contains `create-developer` but no `set-password` or `reset-password` CLI subcommand. Admin UI/API has `/api/users/{user_id}/reset-password`.

Impact: If administrator UI access is lost, recovery may require direct DB/service intervention.

Pilot consequence: Operational inconvenience for pilot; not a security blocker if administrator account custody is managed.

Recommendation: Define admin account recovery procedure; consider a protected CLI reset command in a separate operations-hardening PR.

### PILOT-2026-006

Severity: Low

Status: Open

Subject: Import preview accepts unknown patient fields for compatibility.

Evidence: Synthetic import preview with schema v6 patient plus `unexpected` field returned 200.

Impact: Unknown input fields are ignored during preview rather than rejected, which may surprise operators expecting strict request semantics everywhere.

Pilot consequence: Non-blocking; import remains bounded and preview-only. Requires operator documentation.

Recommendation: Document import compatibility behavior or add a warning count for ignored fields in a future import UX PR.

### PILOT-2026-007

Severity: Informational

Status: Pass

Subject: Core application can run without AI.

Evidence: With `CLINIC_AI_ENABLED=false`, patients, analytics, notifications, attention and data quality endpoints returned controlled success; AI chat returned 503.

Impact: AI is not a critical dependency.

Pilot consequence: Supports Mode A pilot without AI.

Recommendation: Default pilot to AI disabled unless approved inference infrastructure is ready.

### PILOT-2026-008

Severity: Informational

Status: Pass

Subject: Encrypted backup/restore v6 preserves operational data.

Evidence: Backup drill verified AEAD, wrong-key/tamper/truncation rejection, offline restore, `check-db`, OMS preservation, Attention preservation, and internal notification read-state preservation.

Impact: Backup/recovery mechanism is ready for controlled pilot drills.

Pilot consequence: Proceed with daily backup drill and documented key custody.

Recommendation: Store key outside repository/data/backups and test restore before pilot start.

### PILOT-2026-009

Severity: Informational

Status: Partial

Subject: Browser compatibility.

Evidence: Google Chrome headless smoke passed. Edge/Safari/Firefox were not tested in this review.

Impact: Unknown browser-specific UI issues may remain.

Pilot consequence: Recommend Chrome as supported pilot browser.

Recommendation: Add Edge smoke if Edge is part of pilot environment.

### PILOT-2026-010

Severity: Medium

Status: Open

Subject: GitHub branch/ruleset enforcement was not independently verified.

Evidence: CI is green on main, but repository ruleset/required-check enforcement depends on GitHub settings/plan/access outside code review.

Impact: Checks may not be technically enforced for all merges if branch protection is not enabled.

Pilot consequence: Non-blocking for local pilot readiness, but governance gap for team workflow.

Recommendation: Repository owner should verify branch protection/ruleset enforcement.

## Blocking Findings

For synthetic/de-identified limited pilot: no code-level blockers found.

For real patient data: `PILOT-2026-001` and `PILOT-2026-003` are blockers until organisational/infrastructure controls are satisfied.

## Non-blocking Findings

`PILOT-2026-002`, `PILOT-2026-004`, `PILOT-2026-005`, `PILOT-2026-006`, `PILOT-2026-009`, and `PILOT-2026-010` are non-blocking for a synthetic/de-identified single-instance pilot, but should be tracked.

## Real-Data Gate

Before real patient data, the organisation must confirm:

- Approved hosting location.
- HTTPS reverse proxy or approved TLS termination.
- `CLINIC_HTTPS=true` only behind actual HTTPS.
- Encrypted volume/storage for live DB.
- Access control for host, DB file, backups, and logs.
- Backup key custody, backup storage, and key recovery/rotation procedure.
- User provisioning and deactivation procedure.
- Named administrator and emergency access procedure.
- Legal/privacy approval and documented lawful basis.
- Incident handling and escalation process.
- Retention and deletion policy.
- Audit log access and retention policy.
- AI infrastructure approval if AI is enabled.
- No unapproved external data transfer.

This is not a legal conclusion; legal/privacy approval is outside the repository.

## Recommended Pilot Architecture

```text
Internal users
  -> HTTPS
  -> Reverse proxy
  -> FastAPI application
  -> Encrypted storage
  -> SQLite application DB

FastAPI application
  -> Internal-only network
  -> Optional AI inference server
  -> Qwen / OpenAI-compatible inference
```

## Required Actions Before Pilot

- Choose Mode A without AI or Mode B with approved internal AI.
- Deploy behind HTTPS.
- Set `CLINIC_DEBUG=false`.
- Set production `CLINIC_DATA_DIR`.
- Confirm encrypted storage.
- Generate and custody backup key.
- Create first developer and named pilot users.
- Run `python -m backend.cli check-db`.
- Run backup, verify, and restore drill on synthetic data.
- Confirm Chrome as supported browser.
- Confirm pilot feedback and rollback process.

## Required Actions Before Real Patient Data

- Complete Real-Data Gate.
- Verify no default credentials.
- Verify backup key custody and restore operator training.
- Verify AI endpoint is internal-only or keep AI disabled.
- Verify log access controls.
- Verify repository branch protection/ruleset enforcement.
- Confirm legal/privacy/incident/retention approvals.

## Post-Pilot Roadmap

- Shared rate limiter for multi-instance deployments.
- Provider health check for AI status.
- Non-interactive protected CLI password reset.
- Optional import ignored-field warnings.
- Formal Edge/Safari smoke matrix if needed.
- Operational monitoring and alerting.
- PostgreSQL assessment before higher concurrency or multi-instance production.
- Real outbound notification provider as a separate approved integration.

## Automated Verification

- `python -m pytest backend/tests -q`: `318 passed, 1 warning`.
- `python -m backend.cli check-db`: schema 6, FK violations 0, integrity OK.
- `pip-audit -r backend/requirements.txt`: no known vulnerabilities found.
- Bandit allowlisted runner: reviewed 16 allowlisted findings, unexpected enforced findings 0.
- Workflow policy + frontend CSP/security headers subset: 37 passed, 1 warning.
- JS syntax: system `node`/`nodejs` unavailable; `node_repl` `vm.Script` fallback parsed `static/app.js` successfully.
- Local `gitleaks`: unavailable; GitHub `secret-scan` check is SUCCESS on main.
- GitHub CI main run `31704495671`: SUCCESS for head `e7342bb3deec786f30923bb5f08fc24cbb3c7c65`.

## Final Decision

**CONDITIONAL GO** for a controlled limited pilot on synthetic or de-identified data.

**NO-GO for real patient data** until the Real-Data Gate is completed.
