# Security Audit V2

Audit-only review for the current `main` branch of "Система планирования".

Date: 2026-08-20
Base commit: `527cba5c194e55ff7b0f4d32cddde2708896c699`
Schema: `8`
Branch: `audit/security-v2`

## Executive Summary

No Critical, High, or Medium application vulnerabilities were confirmed in this pass.

The review found three non-blocking residual risks:

| ID | Severity | Status | Title |
| --- | --- | --- | --- |
| SEC-V2-001 | Low | Accepted Residual | Rate limiting is intentionally in-memory and per process |
| SEC-V2-002 | Informational | Accepted Residual | HTTPS security headers and cookie `Secure` flag depend on `CLINIC_HTTPS` |
| SEC-V2-003 | Informational | Accepted Residual | AI provider egress boundary is controlled by trusted environment configuration |

Strix was run in local/self-hosted mode after Docker Desktop and the local LM Studio endpoint became available. Full-repository, targeted backend, targeted frontend, targeted AI/security, and dynamic localhost scans were attempted, but none completed because the required local model `qwen3-8b` could not complete Strix's first root-agent turns within the available context/stream constraints. No Strix candidate vulnerability was produced, so no new `SEC-V2-*` finding was created from Strix output. The project source was not sent to Strix managed cloud or an external LLM provider.

Production code was not changed and no autofix was used.

## Scope

In scope:

- Local repository: `~/Documents/clinic_tracker_new`
- Synthetic databases and local test clients
- Localhost-only application assumptions
- Own GitHub repository branches
- Authentication, sessions, CSRF, RBAC/BOLA, patient APIs, diagnosis history, Attention, internal notifications, analytics, data quality, import/export, users, backup/restore, audit logging, AI Assistant V1, OK decisions, PGI result, CI, and supply chain controls

Out of scope:

- External production systems
- Hospital infrastructure, real domains, or real IP ranges
- Real patient datasets, real PHI, real OMS values
- Remediation, autofix, AI V2, deployment, demo update

## Authorization

Testing was limited to the local project and synthetic data. No destructive external targeting was performed. No real patient names, phone numbers, policy numbers, diagnoses, backups, or credentials were used.

## Baseline

| Check | Result |
| --- | --- |
| `python -m pytest backend/tests -q` | `336 passed, 1 warning in 75.22s` |
| `CLINIC_DATA_DIR=$(mktemp -d) python -m backend.cli check-db` | OK, schema `8`, FK violations `0`, integrity OK |
| `pip-audit -r backend/requirements.txt` | No known vulnerabilities found |
| Bandit allowlisted runner | reviewed allowlisted findings `16`; unexpected enforced findings `0` |
| Workflow/frontend security/XSS tests | `35 passed, 1 warning` |
| Focused audit evidence suite | `80 passed, 1 warning` |
| JS syntax | Node/nodejs absent; Python `esprima` fallback cannot parse current valid modern JS syntax (`??`) and was not accepted as a syntax result |
| GitHub CI on `main` | Success for run `32356966936`, head `527cba5c194e55ff7b0f4d32cddde2708896c699` |

The warning is external test infrastructure: FastAPI imports Starlette `TestClient`, which emits `StarletteDeprecationWarning` for current `httpx` compatibility guidance.

## Tooling

### Context7

Status: documentation-only review.

Exact dependency versions checked:

- `fastapi==0.141.1`
- `starlette==1.3.1`
- `pydantic==2.10.5`
- `argon2-cffi==23.1.0`
- `argon2-cffi-bindings==25.1.0`
- `uvicorn==0.34.0`
- `httpx==0.28.1`
- `cryptography==50.0.0`

The Context7 CLI/MCP path could not be installed locally because neither `node` nor `nodejs` is installed. Context7 was therefore used as a documentation/version-check source, and no project code or data was submitted to Context7 services.

### Anthropic Cybersecurity Skills

Status: used as community methodology and checklist source.

Repository clone was kept outside the project under `/tmp/clinic-security-audit-v2-tools/Anthropic-Cybersecurity-Skills` at commit `4c0b700ac5d280ba46695062077f0fe922ce3602`.

Relevant skills reviewed:

- `conducting-api-security-testing`
- `testing-api-for-broken-object-level-authorization`
- `performing-csrf-attack-simulation`
- `testing-api-for-mass-assignment-vulnerability`
- `implementing-api-abuse-detection-with-rate-limiting`
- `detecting-indirect-prompt-injection`
- `validating-backup-integrity-for-recovery`

Unrelated malware, phishing, AD, cloud exploitation, OT/ICS, and mobile skills were not used.

### Strix

Status: attempted; failed/partial coverage only.

Repository clone was kept outside the project under `/tmp/clinic-security-audit-v2-tools/strix` at commit `6f88b7d7d5b6f57833b73a5384079e229e21aa2f`.

Observed prerequisites:

- Strix CLI: `strix-agent 1.5.3`, installed in temporary venv `/tmp/clinic-strix-v2-venv`
- Docker CLI: `/usr/local/bin/docker`, client `29.5.3`, active context `desktop-linux`
- Docker daemon: available via Docker Desktop `4.77.0`, server engine `29.5.3`
- Strix sandbox image: `ghcr.io/usestrix/strix-sandbox:1.3.0`
- Local LLM endpoint `http://127.0.0.1:1234/v1/models`: available
- Local model: `qwen3-8b`
- LiteLLM model identifier used: `lm_studio/qwen3-8b`
- LLM API base used: `http://127.0.0.1:1234/v1`
- External LLM/cloud mode: not used
- Strix managed cloud: not used
- Strix autofix: not used
- Strix CLI telemetry: logs include `skill_loaded` PostHog/Scarf events; no source, patient data, synthetic credentials, or findings were observed in those sanitized log lines
- Raw run artifacts: kept outside the repository under `/private/tmp/clinic-strix-v2-*`
- Maximum observed local model prompt usage: `41314` tokens requested against `40960` available context in the targeted AI/security attempt

Sanitized Strix evidence: `security-audit/strix-v2-summary.md`.

## Attack Surface

| Surface | Entry points | Identifiers | Write actions | Boundary | Sensitive fields |
| --- | --- | --- | --- | --- | --- |
| Authentication | `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/change-password` | username, session cookie, CSRF token | login, logout, password change | active user/session | password, session token, CSRF token |
| Sessions | Cookie auth, server-side `sessions` table | token hash, user id | create, revoke, purge | active user status re-read every request | raw token never persisted |
| Patient APIs | `/api/patients...` | `patient_id`, history occurrence index, event index | create/update/delete/stage/events | patient scope + role permissions | name, phone, OMS, diagnosis, comments |
| Diagnosis history | patient detail/history | patient id, diagnosis history id | diagnosis changes | patient scope | diagnosis values |
| Attention | `/api/patients/{patient_id}/attention...` | patient id, issue id | create/close issue | patient scope + role permissions | comments, reason context |
| Notification Center | `/api/internal-notifications...` | notification id, stable key | mark read/read all | current-user/role scope | patient references |
| External notifications MVP | `/api/notifications...` | notification id, patient id | create/cancel/retry/generate/process/templates/preferences | role + patient scope | phone, SMS body internally |
| Analytics | `/api/analytics...` | doctor, department, dates, report type | CSV export only | server-side scope filter | aggregate data |
| Data Quality | `/api/data-quality...` | patient id, filters | none | server-side scope filter | finding descriptions/reasons |
| Import/export | `/api/import/*`, `/api/export` | import schema, target ids | commit import | scope validation + role permissions | patient record data |
| Staff/users | `/api/users...` | user id | create/update/reset/terminate | developer/admin permissions | password reset secret only in response |
| Departments | `/api/departments...` | department id, user id | create/update/archive membership | structure admin permissions | staff membership |
| Backup/restore | CLI maintenance | file path, key id | backup, restore | local operator CLI | backup key, encrypted DB |
| AI Assistant | `/api/ai/status`, `/api/ai/chat` | patient context id, tool call args | none | read-only tools + RBAC inside tools | prompt text, patient summaries |
| OK/PGI | patient stage/update APIs | event index, `pgiResult`, `consiliumDecision` | structured field update | patient scope + role permissions | medical decision values |
| Audit/logging | `/api/audit`, runtime logs | audit ids, actor/session hash | audit record append | role permissions | must avoid PHI/secrets |

## Threat Model

Primary attacker models:

- Unauthenticated caller attempting credential stuffing, login enumeration, debug discovery, or state-changing requests.
- Authenticated doctor attempting horizontal access to another doctor's patient, attention issue, notification, analytics aggregate, or export target.
- Department head attempting to expand scope outside assigned departments.
- Read-only global role attempting direct write API calls.
- Developer/admin misusing staff or structure endpoints outside intended admin flows.
- Malicious import file attempting mass assignment, malformed nested JSON, oversized payload, or cross-scope assignment.
- Malicious patient/timeline/attention text attempting prompt injection against the AI Assistant.
- Misconfigured production deployment missing HTTPS/proxy controls.

## Authentication

Controls observed:

- Argon2 password hashing via `argon2-cffi`.
- Personal user accounts; legacy shared admin password is removed.
- Server-side sessions with only SHA-256 token hash persisted.
- Session validity re-checks active user status on every request.
- Logout is POST-only, CSRF-protected, invalidates the server-side session, and clears cookies.
- Login responses use generic invalid-credential messaging.
- Login rate limiting exists per client/username bucket and per client bucket.

No confirmed authentication bypass was found.

## RBAC / BOLA

Priority BOLA/IDOR areas reviewed:

- `patient_id`
- history occurrence/event indexes
- Attention issue id
- internal notification id
- employee/user id
- department id
- analytics doctor/department filters
- export target ids
- AI patient context/tool args

Evidence:

- Route inventory shows protected patient write endpoints use `require_active_password`, scope checks in route/service code, and `require_csrf`.
- `backend/tests/test_rbac_matrix.py` covers doctor-vs-doctor, department head scope, read-only write denial, import scope, export filtering, and foreign patient 404 parity.
- `backend/tests/test_ai_assistant.py` covers AI tool scope and foreign patient denial.
- `backend/tests/test_doctor_feedback_clarifications.py` covers PGI foreign patient and read-only write denial.

No confirmed BOLA/IDOR finding was reproduced.

## CSRF

Controls observed:

- `require_csrf` checks header token against the current server-side session.
- For write methods, Origin/Referer mismatches are rejected.
- `SameSite=Lax` cookies are used.
- State-changing routes are POST/PUT/PATCH/DELETE.
- Preview endpoints are explicitly documented/tested as side-effect-free.

Route inventory found 44 state-changing API route/method pairs. All state-changing cookie-auth endpoints have explicit CSRF except:

- `POST /api/auth/login`: credential-based session creation, not cookie-authenticated.
- `POST /api/import/preview`: side-effect-free preview.
- `POST /api/migrate/localstorage/preview`: side-effect-free preview.

Evidence: `backend/tests/test_application_security_hardening.py::test_write_endpoint_csrf_inventory_is_explicit` and logout CSRF tests.

No confirmed CSRF gap was found.

## Injection

Reviewed surfaces:

- patient fields and comments;
- diagnosis, OK, PGI;
- Attention comments;
- employee data;
- import JSON;
- search/filter/query parameters;
- analytics export;
- frontend DOM sinks.

Controls observed:

- Request models inherit `StrictRequestModel` with Pydantic `extra="forbid"`.
- Import nested models reject unknown fields.
- SQL access uses sqlite parameter binding in reviewed routes/services.
- CSP disallows inline scripts/styles and eval.
- Frontend security tests reject `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `new Function`, inline handlers, and inline styles.
- XSS API tests exist for event-handler payloads.

No confirmed SQL injection, mass-assignment, stored/reflected/DOM XSS, or HTML injection finding was reproduced.

## Import / Export

Controls observed:

- Import preview and commit validate document structure and accepted schema versions.
- Import commit is CSRF-protected and role/scope checked.
- Import cannot assign patients outside the actor's scope.
- Export filters selected ids through the same server-side patient scope as list APIs.
- Export omits live UI `attention` summary.
- Analytics CSV export is aggregate-oriented and audited without PHI.

No confirmed import/export scope leakage was found.

## Route Business Logic

Reviewed Doctor Feedback v8 behaviors:

- repeated appointment/exam cycles;
- terminal route behavior;
- diagnosis-not-confirmed;
- dispensary observation;
- structured OK decisions;
- structured PGI result;
- legacy `consilium_code` / `pgi_code` compatibility.

Evidence: `backend/tests/test_doctor_feedback_clarifications.py` and full backend suite.

No confirmed business-logic bypass was found.

## Attention

Controls observed:

- Create/close endpoints are CSRF-protected.
- Patient scope is checked before issue operations.
- Re-closing an issue is tested.
- Audit events avoid PHI.
- Read-only roles receive backend 403 for write attempts.

No confirmed Attention workflow vulnerability was reproduced.

## Notifications

Controls observed:

- Internal notification read/read-all endpoints require CSRF and active session.
- Notification listing/summary are scoped.
- External notification MVP remains mock-provider only and network-free.
- Real SMS provider is absent.

No confirmed notification scope leakage was reproduced.

## Analytics

Controls observed:

- Analytics filters are applied inside current patient scope.
- Doctor/department filters cannot expand visibility.
- Export endpoint requires CSRF and abuse rate limit.
- Report audit detail is technical and does not include PHI.

No confirmed aggregate authorization bypass was found.

## Data Quality

Controls observed:

- Data Quality endpoints are read-only and rate limited.
- Issues, duplicate groups, patient detail quality, and summary are scoped server-side.
- Duplicate grouping/reasons are deterministic from previous hardening.

No confirmed cross-scope patient id leakage was found.

## OK / PGI

Controls observed:

- `consiliumDecision` is structured enum data on applicable events.
- `pgiResult` is structured and does not auto-close or auto-route a patient.
- Legacy free-text `consiliumCode`/`pgiCode` is preserved for compatibility but not used as active route authority.
- Audit logs event names but not OK/PGI values.
- Read-only write attempts are denied by backend 403.
- Foreign patient write attempts are hidden with 404 where appropriate.

No confirmed v8-specific OK/PGI vulnerability was found.

## AI Assistant

Controls observed:

- Disabled by default.
- `/api/ai/chat` requires active session, CSRF, and abuse rate limit.
- No write tools are exposed.
- Tool allowlist is server-side.
- Tool argument schemas are strict.
- Tool execution checks RBAC/patient scope server-side.
- Prompt/response content is not persisted.
- AI tables are not created.
- Policy number is excluded from AI tool results.
- Prompt injection text from patient fields/attention is treated as untrusted data in prompts and tests.

No confirmed AI read/write escalation was reproduced through backend/tool tests.

## AI Provider Boundary

Controls observed:

- API key is backend-only and hidden from status responses.
- Provider is disabled unless enabled by env.
- Redirects are blocked by `_NoRedirectHandler`.
- Timeout is bounded.
- Provider response size is capped.
- Malformed provider responses become controlled errors.

Residual: see `SEC-V2-003`.

## Privacy / Logging / Audit

Controls observed:

- Audit actor uses session-token hash-derived actor, not raw token.
- Login/password/session events avoid password/hash/token output.
- Import/export/analytics audit details are counts and technical parameters.
- OK/PGI values are not written to audit.
- AI logs success metadata only: user id, role, elapsed, tool call count.
- Unhandled exception responses are generic.

No confirmed PHI leakage into audit logs was found from reviewed code/tests.

## Backup / Cryptography

Controls observed:

- Backup uses SQLite backup API for consistency.
- Backup format uses AES-256-GCM.
- Header is size bounded and has a strict field set.
- Key material is not printed.
- Key file permissions are checked.
- Restore requires explicit offline confirmation.
- Restore verifies backup and creates pre-restore recovery backup.
- Symlink and tracked-repository output protections exist.
- Wrong key, tamper, truncation, bad schema, bad nonce, huge header, FK-corrupt backup, and non-SQLite plaintext are tested.

No confirmed backup/crypto vulnerability was reproduced.

## Rate Limiting

Controls observed:

- Login rate limit by client + username bucket and client bucket.
- Import preview/commit rate limits.
- Analytics export rate limit.
- Data Quality rate limit.
- AI chat rate limit.
- Notification worker endpoints have abuse limits.
- Proxy spoofing headers do not alter current rate-limit identity in tests.
- Bucket count is bounded/pruned.

Residual: see `SEC-V2-001`.

## Security Headers

Controls observed:

- CSP: `default-src 'self'`, no inline scripts/styles, no eval, `frame-ancestors 'none'`, `form-action 'self'`.
- `X-Content-Type-Options: nosniff`.
- `Referrer-Policy: strict-origin-when-cross-origin`.
- `Permissions-Policy` denies camera/microphone/geolocation.
- API responses receive `Cache-Control: no-store`.
- Trusted hosts are limited to configured host, `127.0.0.1`, and `localhost`.
- OpenAPI/docs are registered only when `CLINIC_DEBUG=true`.

Residual: see `SEC-V2-002`.

## Supply Chain

Controls observed:

- Runtime dependencies in `backend/requirements.txt` are pinned.
- Dev/audit tools are pinned in requirements or workflow steps.
- `pip-audit` returns no known vulnerabilities.
- GitHub Actions workflow uses SHA-pinned `actions/checkout` and `actions/setup-python/setup-node`.
- Workflow permissions are `contents: read`.
- No `pull_request_target` workflow is used.
- Checkout uses `persist-credentials: false`.
- Gitleaks is downloaded with pinned version and SHA-256 verification.

No confirmed supply-chain finding was identified.

## CI/CD

GitHub workflow jobs reviewed:

- `backend-tests`
- `frontend-security`
- `workflow-policy`
- `dependency-audit`
- `sast-bandit`
- `secret-scan`

Latest `main` CI run for base commit `527cba5c194e55ff7b0f4d32cddde2708896c699` completed successfully.

No CI policy vulnerability was confirmed.

## Strix Results

Strix white-box scan:

- Command mode: `--non-interactive --scan-mode quick --scope-mode full --max-turns 20 --max-budget 0.25`
- Target: `/Users/maksutishakov/Documents/clinic_tracker_new`
- Run id: `clinic-tracker-new_354f`
- Status: failed
- Failure: `ContextWindowExceededError`; LM Studio returned `request (41306 tokens) exceeds the available context size (40960 tokens)`
- SARIF results: `0`
- Accepted Strix findings: `0`

Strix dynamic localhost scan:

- Command mode: `--non-interactive --scan-mode quick --max-turns 20 --max-budget 0.25`
- Synthetic target: `http://127.0.0.1:8431`
- Sandbox target mapping: `http://host.docker.internal:8431`
- Synthetic DB path: `/tmp/clinic-strix-dynamic-data.*`
- Synthetic roles: developer, doctor A, doctor B, department head A, department head B, read-only
- Run id: `host-docker-internal-8431_6385`
- Status: interrupted after prolonged idle/no useful first model response
- Evidence: Strix log showed `model stream produced no event for 300s; abandoning the turn`, retry, then manual interruption while waiting for model response
- SARIF results: `0`
- Accepted Strix findings: `0`

Additional targeted Strix attempts after the broad runs:

| Attempt | Target | Mode | Run id | Status | Evidence | SARIF results |
| --- | --- | --- | --- | --- | --- | --- |
| Targeted backend | `/Users/maksutishakov/Documents/clinic_tracker_new/backend` | quick, non-interactive, full scope, max turns 10, max budget 0.15 | `backend_62f1` | failed | `ContextWindowExceededError`; `request (41251 tokens) exceeds the available context size (40960 tokens)` | `0` |
| Targeted frontend | temp copy with only `index.html` and `static/` | quick, non-interactive, full scope, max turns 10, max budget 0.15 | `clinic-strix-frontend-target-202_fd12` | failed | `ContextWindowExceededError`; `request (41298 tokens) exceeds the available context size (40960 tokens)` | `0` |
| Targeted AI/security | temp copy of AI/auth/security/policy/scope route files only | quick, non-interactive, full scope, max turns 10, max budget 0.15 | `clinic-strix-ai-security-target_c9f7` | failed | `ContextWindowExceededError`; `request (41314 tokens) exceeds the available context size (40960 tokens)` | `0` |
| Final lightweight dynamic | synthetic localhost app on `127.0.0.1:8431`, sandbox target `host.docker.internal:8431` | quick, non-interactive, max turns 8, max budget 0.10 | `host-docker-internal-8431_8c7b` | interrupted | first model stream produced no event for 300s, retry started, then manually interrupted | `0` |

Important interpretation:

- These are real Strix execution attempts with Docker and local LM Studio, but not completed scans.
- `0` SARIF results is not evidence that the app has zero vulnerabilities; it only means Strix produced no candidate finding before failure/interruption.
- No new `SEC-V2-*` finding was created from Strix because there was no candidate to validate.
- The local model was not changed because the audit instructions forbade automatic switch to cloud/frontier models.
- Under the current Strix 1.5.3 prompt/tooling profile and LM Studio `qwen3-8b` context/stream behavior, `qwen3-8b` is not considered usable for future Strix scans of this project without changing the model/context setup or Strix prompt profile.

## Findings

### SEC-V2-001: Rate limiting is intentionally in-memory and per process

Severity: Low
Status: Accepted Residual
Source: Multiple: Manual, Cybersecurity Skill
Affected component: `backend/app/security.py`, login/import/export/data-quality/AI/notification abuse limits

Preconditions:

- Deployment runs multiple backend worker processes, restarts frequently, or is exposed to higher-volume untrusted traffic.

Reproduction:

1. Inspect `backend/app/security.py`.
2. Observe `_failed_attempts` and `_request_attempts` are process-local dictionaries.
3. Observe source comment: "In-memory, per-process. Resets on restart".

Evidence:

- `check_request_rate_limit` and login limiter are bounded and locked, but state is local process memory.
- `backend/tests/test_application_security_hardening.py` verifies rate-limit behavior and bounded buckets.

Impact:

- In a multi-worker or publicly exposed deployment, an attacker may distribute attempts across workers or wait for process restart to reset counters.

Existing control:

- Current deployment model is local-first/single-process.
- Buckets are bounded and pruned.
- Login rate limit combines host and username-derived buckets.
- Sensitive endpoints have endpoint-level rate limits.

Recommendation:

- Before multi-worker/public deployment, add a deployment-level or persistent limiter such as reverse proxy/API gateway/Redis-backed sliding window.

Regression test recommendation:

- Add integration tests for the chosen persistent limiter once production deployment topology is defined.

### SEC-V2-002: HTTPS security headers and cookie `Secure` flag depend on `CLINIC_HTTPS`

Severity: Informational
Status: Accepted Residual
Source: Multiple: Manual, Context7 documentation review
Affected component: `backend/app/config.py`, `backend/app/main.py`, `backend/app/routes/auth.py`

Preconditions:

- The application is served over HTTPS, directly or behind a TLS-terminating proxy, but `CLINIC_HTTPS` is left false.

Reproduction:

1. Inspect `config.HTTPS`.
2. Inspect `_set_auth_cookies`, where cookie `secure=config.HTTPS`.
3. Inspect `security_headers`, where HSTS is emitted only when `config.HTTPS` is true.

Evidence:

- Local HTTP mode correctly keeps `Secure` false.
- HTTPS production mode requires explicit env configuration.

Impact:

- Misconfigured HTTPS deployment may omit HSTS and Secure cookie attributes.

Existing control:

- Safe local default is documented.
- HSTS is not sent on plain HTTP, avoiding local breakage.
- App binds to localhost by default.

Recommendation:

- Production runbook must require `CLINIC_HTTPS=true` whenever the app is reachable via HTTPS, including behind a reverse proxy.
- Add deployment smoke checking `Set-Cookie; Secure` and HSTS under HTTPS mode.

Regression test recommendation:

- Keep existing header tests and add deployment-level verification once production topology is fixed.

### SEC-V2-003: AI provider egress boundary is controlled by trusted environment configuration

Severity: Informational
Status: Accepted Residual
Source: Multiple: Manual, Cybersecurity Skill
Affected component: `backend/app/config.py`, `backend/app/ai/provider.py`

Preconditions:

- Operator enables AI with `CLINIC_AI_ENABLED=true` and points `CLINIC_AI_BASE_URL` to a remote/untrusted OpenAI-compatible provider.

Reproduction:

1. Inspect `AI_BASE_URL` in `backend/app/config.py`.
2. Inspect `OpenAICompatibleAIProvider.chat`, which sends prompt/tool context to `${base_url}/chat/completions`.
3. Verify redirects are disabled, timeout is bounded, and response size is capped.

Evidence:

- AI is disabled by default.
- Provider status hides base URL, model, and API key.
- Tool results omit OMS/policy number in tests.

Impact:

- If an operator configures an external provider, AI prompt/tool context may leave the local trust boundary.

Existing control:

- Disabled by default.
- No write tools.
- No arbitrary HTTP tools.
- No raw SQL tools.
- Server-side RBAC in tool execution.
- No prompt/response persistence.
- Redirects disabled and size/time limits enforced.

Recommendation:

- Production policy should explicitly approve allowed AI provider endpoints.
- Prefer local provider for PHI-adjacent use.
- Consider config allowlisting if production enables non-local AI.

Regression test recommendation:

- Add provider-boundary tests once production AI provider policy is approved.

## Accepted Residual Risks

- `SEC-V2-001`: accepted for current local-first/single-process deployment.
- `SEC-V2-002`: accepted because local HTTP mode must remain usable; production must set `CLINIC_HTTPS=true`.
- `SEC-V2-003`: accepted because AI is disabled by default and provider selection is an operator-controlled deployment decision.

## Recommendations

1. Keep application remediation separate from this audit branch.
2. Before public or multi-worker deployment, replace or augment process-local rate limiting.
3. Add a production deployment checklist for HTTPS/proxy/header validation.
4. Formalize AI provider policy before enabling AI outside localhost.
5. Re-run Strix with a model/context configuration capable of completing the root-agent prompt, or with an explicitly approved alternative model/provider.
6. Keep `node --check static/app.js` in CI; local machine currently lacks Node.

## Remediation Priority

| Priority | Item |
| --- | --- |
| P1 | No Critical/High/Medium confirmed issues require immediate hotfix. |
| P2 | Persistent/deployment-level rate limiting before multi-worker/public exposure. |
| P2 | Production HTTPS/proxy/header smoke checks before pilot deployment. |
| P3 | AI provider endpoint allowlist/policy before non-local AI provider use. |
| P3 | Re-run Strix with a capable local or explicitly approved model; current `qwen3-8b` attempt did not complete. |

## Retest Plan

1. Re-run baseline: backend tests, check-db on clean synthetic DB, pip-audit, Bandit, workflow-policy, frontend security/XSS.
2. Re-run route inventory and verify all state-changing endpoints are CSRF-covered or side-effect-free.
3. Re-run BOLA/IDOR tests for doctor, department head, read-only, and developer.
4. Re-run strict schema/mass-assignment tests on all request models.
5. Re-run AI backend/tool security tests with synthetic prompt-injection data.
6. Re-run backup tamper/verify/restore tests on synthetic schema 8 database.
7. Re-run Strix against the local repository and localhost synthetic app with a model/context setup that completes the first root-agent turns, then manually validate every candidate finding.
