# Skill coverage — clinic_tracker_new, Audit 3 (preprod-run-3)

Source: `~/Documents/security-references/Anthropic-Cybersecurity-Skills` @ e612f494 (817 skills) plus 13 other installed security skills (Cloudflare `security-audit`, Strix skills, OWASP/GDPR helpers). Every skill's frontmatter (name, description, tags, headings) was read and classified against `skill-coverage/PROJECT-PROFILE.md` (FastAPI + SQLite + vanilla-JS SPA, on-prem, RBAC with patient scope, AES-GCM backups, optional read-only AI assistant, GitHub Actions CI; no cloud/containers/K8s/AD/mobile/OT/email/JWT/OAuth/XML).

Classification was done by 9 read-only triage agents (≈93 skills each); 9 skills they skipped were classified by the orchestrator (marked `orchestrator`). Checks from applicable and partially applicable skills were distributed as leads to the Audit 3 hunters (column *Used by*), whose results are in REPORT.md / findings.json.

## Totals

| Verdict | Count |
|---|---|
| applicable | 42 |
| partially_applicable | 116 |
| not_applicable | 672 |
| **total** | **830** |

## Not-applicable reasons (grouped)

| Group | Skills |
|---|---|
| OT/ICS/IoT/hardware/firmware | 213 |
| Cloud/containers/Kubernetes/serverless | 123 |
| Windows/AD/Kerberos/SMB | 101 |
| Other (governance/program/tooling not applicable to source) | 53 |
| SOC / SIEM / threat intel / IR operations | 52 |
| Network infrastructure / wireless / DNS / BGP | 36 |
| Protocols/tech absent (JWT, OAuth, SAML, XML, WebSocket, GraphQL, SOAP) | 26 |
| Malware analysis / forensics / memory / disk | 24 |
| Email / phishing / social engineering | 17 |
| Mobile apps | 15 |
| Live/external testing prohibited in this pass | 12 |

## Applicable (42)

| Skill | Reason | Attack classes | Used by | Result |
|---|---|---|---|---|
| `bypassing-authentication-with-forced-browsing` | FastAPI routes and static SPA; unauthenticated access to API/admin endpoints can be checked by source review and local TestClient. | forced browsing, missing authentication, broken function-level authorization | r3-h-auth-session / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `conducting-api-security-testing` | REST JSON API with sessions, RBAC, per-patient scope; OWASP API Top 10 checks by source review and local tests (GraphQL/gRPC parts absent). | BOLA/IDOR, broken function-level authorization, mass assignment, excessive data exposure, rate limiting, input validation, business logic | r3-h-admin / r3-h-analytics / r3-h-audit-secrets / r3-h-auth-session / r3-h-import-export / r3-h-notifications / r3-h-patient-logic / r3-h-patients-rbac / r3-h-resource | RBAC/scope review — no finding; scope fails closed; analytics/data-quality review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d; business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc; import/export/migration review — no finding; notification queue/templates review — no finding; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac; staff/department admin review — no finding; run-2 partial-commit closed |
| `detecting-broken-object-property-level-authorization` | The FastAPI/pydantic REST API handles PHI and staff accounts with RBAC; excessive data exposure and mass assignment apply directly. | excessive data exposure, mass assignment, BOPLA | r3-h-admin / r3-h-analytics / r3-h-auth-session / r3-h-import-export / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; analytics/data-quality review — no finding; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d; import/export/migration review — no finding; staff/department admin review — no finding; run-2 partial-commit closed |
| `detecting-supply-chain-attacks-in-ci-cd` | The project uses GitHub Actions CI and Dependabot. Workflows can be reviewed directly for pinning, script injection and permissions. | unpinned actions, expression script injection, excessive workflow permissions, untrusted tool download | r3-h-ai / r3-h-analytics / r3-h-chain / r3-h-import-export / r3-h-notifications / r3-h-patient-logic / r3-h-supply | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; CI/dependency review — no finding; analytics/data-quality review — no finding; business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc; cross-component chain review — no finding; import/export/migration review — no finding; notification queue/templates review — no finding |
| `detecting-typosquatting-packages` | Pinned pip requirements can be checked directly for misspelled or brandjacked PyPI package names. | typosquatting, brandjacking, dependency confusion | r3-h-supply | CI/dependency review — no finding |
| `detecting-typosquatting-packages-in-npm-pypi` | The PyPI dependency list can be checked by name similarity. There is no npm (vanilla JS, no build step). | typosquatting, malicious PyPI packages | r3-h-supply | CI/dependency review — no finding |
| `exploiting-api-injection-vulnerabilities` | The FastAPI JSON API uses raw sqlite3 SQL, and the CLI may call subprocesses. SQL and command injection checks apply. LDAP and NoSQL do not. | SQL injection, OS command injection, SSRF | r3-h-ai / r3-h-analytics / r3-h-import-export / r3-h-notifications | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; import/export/migration review — no finding; notification queue/templates review — no finding |
| `exploiting-broken-function-level-authorization` | Five RBAC roles with admin functions (resets, revocation, export, audit, import) make function-level authorization directly testable. | BFLA, vertical privilege escalation | r3-data-at-rest / r3-h-admin / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; staff/department admin review — no finding; run-2 partial-commit closed |
| `exploiting-excessive-data-exposure-in-api` | The API returns patient PHI and staff records. Responses may carry fields the UI never shows (password hashes, phone numbers, policy numbers). | excessive data exposure, sensitive field leakage | r3-h-analytics | analytics/data-quality review — no finding |
| `exploiting-idor-vulnerabilities` | Per-patient scope filtering with out-of-scope returning 404 is exactly an IDOR boundary across patient, user and notification IDs. | IDOR, horizontal privilege escalation | r3-data-at-rest / r3-h-admin / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; staff/department admin review — no finding; run-2 partial-commit closed |
| `exploiting-mass-assignment-in-rest-apis` | Pydantic create/update models for users and patients could accept role, scope or status fields a caller should not control. | mass assignment, privilege escalation via extra fields | r3-data-at-rest / r3-h-admin | data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; staff/department admin review — no finding; run-2 partial-commit closed |
| `exploiting-race-condition-vulnerabilities` | In-memory rate limiting, session revocation, password changes and the SQLite queue worker create real TOCTOU windows. | TOCTOU, limit bypass, double processing | r3-h-patient-logic | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc |
| `exploiting-sql-injection-vulnerabilities` | Stdlib sqlite3 with hand-written SQL, including a dynamic scope filter and analytics queries. | SQL injection, second-order SQLi | r3-h-notifications | notification queue/templates review — no finding |
| `find-security-vulnerabilities-in-code` | White-box source data-flow review matches the audit's source-review mode; Strix tool itself may not run. | injection, authz flaws, XSS, sensitive data exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-aes-encryption-for-data-at-rest` | The encrypted backups use AES-256-GCM through the cryptography library, so nonce handling, key derivation and storage can be reviewed directly. | nonce reuse, weak key derivation, missing authentication, key exposure | r3-h-backup | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `implementing-api-schema-validation-security` | The FastAPI/pydantic schemas enforce request validation, so strictness, extra fields and limits can be reviewed directly. | mass assignment, input validation bypass, oversized payloads | r3-h-admin | staff/department admin review — no finding; run-2 partial-commit closed |
| `implementing-llm-guardrails-for-security` | Optional AI assistant with tool calling over PHI. Input/output validation, PII leakage and prompt injection checks apply directly. | prompt injection, indirect prompt injection via patient notes, PII/PHI leakage, excessive agency | r3-data-at-rest / r3-h-ai | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700 |
| `implementing-secret-scanning-with-gitleaks` | The repo already runs gitleaks in GitHub Actions. Config, coverage and a local history scan apply directly. | hardcoded secrets, credential leakage in git history | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-secrets-scanning-in-ci-cd` | GitHub Actions CI with gitleaks is present, so CI secret-scanning integration applies directly. | leaked secrets, CI secret exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-semgrep-for-custom-sast-rules` | Custom local Semgrep rules can target FastAPI-specific patterns: scope filters, CSRF dependency, raw SQL, DOM sinks. | SQL injection, missing authorization, missing CSRF, XSS | r3-h-client / r3-h-http-csrf | CSRF/headers/HTTP review — no finding; SPA/DOM/CSP review — no finding |
| `integrating-sast-into-github-actions-pipeline` | GitHub Actions CI exists. Adding or checking CodeQL/Semgrep SAST, permissions and gates applies directly. | undetected code vulnerabilities, CI misconfiguration | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-api-rate-limiting-bypass` | In-memory login/re-auth rate limiting sits behind a reverse proxy, so client-IP derivation and bypass paths need checking. | rate-limit bypass, X-Forwarded-For spoofing, credential brute force, limiter state exhaustion | r3-h-patient-logic / r3-h-resource | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `performing-clickjacking-attack-test` | Authenticated same-origin SPA handling PHI. Anti-framing headers can be checked in source. | clickjacking, UI redressing | r3-h-client / r3-h-http-csrf | CSRF/headers/HTTP review — no finding; SPA/DOM/CSP review — no finding |
| `performing-content-security-policy-bypass` | The SPA relies on a strict CSP. Check its directives and any DOM XSS sinks in app.js that could get around it. | CSP bypass, XSS, DOM XSS, base-uri/form-action gaps | r3-h-client | SPA/DOM/CSP review — no finding |
| `performing-cryptographic-audit-of-application` | The app uses Argon2id passwords, AES-256-GCM backups, session/CSRF token generation and key files, all reviewable in source. | weak algorithms/parameters, nonce reuse, hardcoded keys, insecure randomness, timing-unsafe comparison | r3-h-client | SPA/DOM/CSP review — no finding |
| `performing-csrf-attack-simulation` | Cookie-session auth with SameSite=Lax and a session-bound CSRF header. Enforcement can be verified in source and local tests. | CSRF, login CSRF, method override | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `performing-second-order-sql-injection` | sqlite3 stores user data that is later reused in queries, scope filters, analytics and exports. | second-order SQL injection, SQL injection | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-security-headers-audit` | The app sets strict CSP and cookie attributes itself; HSTS/frame/referrer headers can be verified locally. | missing security headers, clickjacking, cookie misconfiguration | r3-h-client / r3-h-http-csrf | CSRF/headers/HTTP review — no finding; SPA/DOM/CSP review — no finding |
| `performing-sqlite-database-forensics` | A single SQLite WAL file holds PHI; deleted records may be recoverable from freelist or WAL pages. | residual data recovery, PHI remanence | r3-skill-remanence | SQLite remanence review — no finding; secure_delete/VACUUM recorded as hardening |
| `performing-threat-modeling-with-owasp-threat-dragon` | A STRIDE/LINDDUN data-flow threat model fits the app's trust boundaries: browser, proxy, app, SQLite, CLI, AI provider. | STRIDE threats, LINDDUN privacy threats | r3-h-chain / r3-h-obvious | baseline exposure review — no finding; cross-component chain review — no finding |
| `performing-web-application-penetration-test` | The OWASP WSTG methodology applies directly to the FastAPI app and SPA via source review and a local TestClient. | broken access control, IDOR, CSRF, XSS, injection, session management, business logic | r3-h-client / r3-h-http-csrf / r3-h-patient-logic | CSRF/headers/HTTP review — no finding; SPA/DOM/CSP review — no finding; business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc |
| `securing-agentic-ai-tool-invocation` | AI assistant does read-only tool calling scoped to session user; tool-boundary controls apply directly. | excessive agency, tool-call authorization bypass, prompt injection via tool output | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `securing-github-actions-workflows` | Project uses GitHub Actions CI with SHA-pinned actions and gitleaks download; hardening checks apply directly. | CI supply chain compromise, token over-privilege, script injection in workflows | r3-h-chain / r3-h-patient-logic | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc; cross-component chain review — no finding |
| `security-audit` | General codebase security audit workflow directly fits a source-review audit of this FastAPI/CLI project. | general code security review | r3-h-obvious | baseline exposure review — no finding |
| `testing-api-authentication-weaknesses` | FastAPI JSON API with password login, session cookies, rate limiting, and forced password change. | broken authentication, credential stuffing, session management flaws, missing auth on endpoints | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-api-for-broken-object-level-authorization` | Per-patient scope SQL filter and RBAC make BOLA/IDOR the central risk. | BOLA/IDOR | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-api-for-mass-assignment-vulnerability` | Pydantic request models on writable endpoints; role, department, and owner fields can be mass-assigned. | mass assignment | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-for-broken-access-control` | Five-role RBAC with global read-only role and scope filters; function-level and vertical escalation apply. | vertical privilege escalation, BFLA, horizontal access, forced browsing | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-for-business-logic-vulnerabilities` | Clinical workflow states, notification queue, password flows, and import have logic-abuse potential. | workflow bypass, state manipulation, race conditions | r3-h-patient-logic | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc |
| `testing-for-sensitive-data-exposure` | PHI (names, phones, OMS numbers, diagnoses) flows through API, exports, logs, backups, and localStorage. | PII/PHI over-exposure, secrets in source, insecure storage, logging of sensitive data | r3-h-client | SPA/DOM/CSP review — no finding |
| `testing-for-xss-vulnerabilities` | Vanilla-JS SPA renders stored PHI/notes; DOM and stored XSS apply despite CSP. | stored XSS, DOM XSS, CSP bypass | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `validating-backup-integrity-for-recovery` | AES-256-GCM encrypted backup with inspect/verify/restore CLI; integrity validation applies directly. | backup tampering, silent corruption, weak backup crypto | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |

## Partially applicable (116)

| Skill | Reason | Attack classes | Used by | Result |
|---|---|---|---|---|
| `analyzing-api-gateway-access-logs` | No API gateway, but its BOLA, brute-force and injection detection ideas fit the app logging and audit log as a checklist. | insufficient logging and monitoring, BOLA/IDOR detection, credential brute force | r3-h-audit-secrets / r3-h-auth-session / r3-h-patients-rbac / r3-h-supply | CI/dependency review — no finding; RBAC/scope review — no finding; scope fails closed; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `analyzing-sbom-for-supply-chain-vulnerabilities` | The NVD API/SBOM tooling can't be used here, but checking pinned pip deps for known CVEs by source review applies. | vulnerable dependencies, software supply chain | r3-h-chain / r3-h-supply | CI/dependency review — no finding; cross-component chain review — no finding |
| `analyzing-supply-chain-malware-artifacts` | Trojanized-update forensics doesn't apply, but its build-pipeline and dependency-integrity checks fit pip and GitHub Actions CI. | compromised build pipeline, dependency confusion, unverified artifacts | r3-h-chain / r3-h-supply | CI/dependency review — no finding; cross-component chain review — no finding |
| `analyzing-web-server-logs-for-intrusion` | No production logs to analyze, but reviewing what the app and uvicorn logs record for attack detection applies. | insufficient logging, sensitive data in logs, brute force detection | r3-h-audit-secrets / r3-h-auth-session / r3-h-supply | CI/dependency review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `api-security-testing` | REST API present, but Strix live/autonomous testing is prohibited; use its endpoint-enumeration checklist via source review. | BOLA, auth bypass, injection, mass assignment | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `application-security-testing` | AppSec asset-selection methodology applies; Strix tooling can't run here. | general appsec | r3-h-obvious | baseline exposure review — no finding |
| `auditing-mcp-servers-for-tool-poisoning` | No MCP server, but the optional AI assistant's tool calling and operator-set base URL share the tool-abuse, SSRF and authorization risks. | LLM tool abuse / excessive agency, SSRF via configurable endpoint, prompt injection via tool output, authorization bypass through tools | r3-h-ai / r3-h-analytics / r3-h-audit-secrets / r3-h-auth-session / r3-h-import-export / r3-h-notifications / r3-h-resource | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d; import/export/migration review — no finding; notification queue/templates review — no finding; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `building-devsecops-pipeline-with-gitlab-ci` | GitLab-specific, but SAST/dependency/secret-scanning gate methodology maps to the repo's GitHub Actions CI. | supply-chain, missing security gates, secret leakage | r3-h-audit-secrets / r3-h-chain / r3-h-supply | CI/dependency review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; cross-component chain review — no finding |
| `building-identity-governance-lifecycle-process` | No IGA platform, but joiner-mover-leaver checks apply to local staff accounts, role changes, deactivation and session revocation. | orphaned accounts, privilege creep, stale sessions | r3-h-admin / r3-h-auth-session / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d; staff/department admin review — no finding; run-2 partial-commit closed |
| `building-role-mining-for-rbac-optimization` | No IGA data to mine, but least-privilege review of the five-role RBAC matrix and permission overlap applies. | excessive privilege, broken access control, role overlap | r3-h-admin / r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed; staff/department admin review — no finding; run-2 partial-commit closed |
| `ci-security-scanning-with-strix` | GitHub Actions CI exists; adding Strix isn't possible here, but CI security-gate coverage can be reviewed. | missing CI security gates | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `conducting-cyber-risk-assessment-with-nist-800-30` | Risk-assessment methodology usable to structure PHI-app threat sources, vulnerabilities, likelihood and impact from source findings. | risk assessment, threat modeling | r3-h-chain | cross-component chain review — no finding |
| `conducting-gdpr-compliance-assessment` | Processes patient PHI (likely Russian 152-FZ, not EU); GDPR Art.32/data-rights methodology usable as privacy checklist. | privacy/compliance gaps | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `conducting-man-in-the-middle-attack-simulation` | Tooling can't run, but transport checks apply: Secure cookies, HTTPS enforcement, HSTS, and outbound urllib TLS for the AI base URL. | MITM, session hijacking over cleartext, TLS validation bypass | r3-h-auth-session | auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `configuring-hsm-for-key-storage` | No HSM, but key-management principles apply to AES-256-GCM backup keygen, key storage and separation from backups. | key exposure, weak key management | r3-h-backup / r3-h-client | SPA/DOM/CSP review — no finding; backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `configuring-tls-1-3-for-secure-communications` | TLS terminates at operator proxy (not in repo); only app-side HTTPS assumptions and deployment docs are reviewable. | weak transport security, cleartext exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `continuous-llm-red-teaming-with-promptfoo` | Optional AI assistant with tool calling; promptfoo can't run against a live LLM, but injection/tool-scope checks apply via source review. | prompt injection, indirect prompt injection via PHI notes, excessive agency, sensitive data disclosure | r3-data-at-rest / r3-h-ai / r3-h-analytics / r3-h-audit-secrets / r3-h-import-export / r3-h-notifications | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; import/export/migration review — no finding; notification queue/templates review — no finding |
| `defending-llms-with-guardrails` | No guardrail stack deployed, but input/output constraint principles apply to the optional AI assistant path. | prompt injection, insecure output handling, sensitive data disclosure | r3-h-ai / r3-h-analytics / r3-h-audit-secrets / r3-h-import-export / r3-h-notifications | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; import/export/migration review — no finding; notification queue/templates review — no finding |
| `detecting-ai-model-prompt-injection-attacks` | Applies only to the optional OpenAI-compatible assistant path; ML classifier tooling not needed, source review of prompt handling applies. | direct prompt injection, system prompt override, tool-call abuse via LLM | r3-h-ai / r3-h-analytics / r3-h-import-export / r3-h-notifications / r3-h-resource | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; import/export/migration review — no finding; notification queue/templates review — no finding; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `detecting-anomalous-authentication-patterns` | UEBA/ML tooling absent, but brute-force, spraying and credential-stuffing defences plus auth audit logging can be reviewed in source. | brute force, password spraying, credential stuffing, account compromise | r3-h-auth-session | auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `detecting-api-enumeration-attacks` | SIEM rules are out of scope, but BOLA/IDOR enumeration surface (sequential patient IDs, 404 vs 403 oracle) is reviewable. | BOLA/IDOR, ID enumeration, response-status oracle | r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed |
| `detecting-aws-credential-exposure-with-trufflehog` | No AWS, but secret-exposure scanning of the repo, CI and config applies (gitleaks already runs in CI). | hardcoded secrets, secrets in git history | r3-h-audit-secrets / r3-h-auth-session / r3-h-supply | CI/dependency review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d |
| `detecting-dependency-confusion` | The PyPI dependency method applies to pip requirements; no private index or npm/Maven. | dependency confusion, package substitution | r3-h-supply | CI/dependency review — no finding |
| `detecting-indirect-prompt-injection` | The assistant's read-only tools return stored free text (patient notes, names) into LLM context, an indirect injection channel; web/PDF/OCR parts are absent. | indirect prompt injection, stored-data-to-LLM injection, data exfiltration via LLM output | r3-h-ai / r3-h-analytics / r3-h-import-export / r3-h-notifications | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56; analytics/data-quality review — no finding; import/export/migration review — no finding; notification queue/templates review — no finding |
| `detecting-insider-data-exfiltration-via-dlp` | DLP tooling absent, but bulk PHI export (CSV/JSON/analytics) by legitimate staff is an in-app exfiltration channel. | insider bulk data export, PHI exfiltration | r3-data-at-rest / r3-h-audit-secrets / r3-h-import-export | audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; import/export/migration review — no finding |
| `detecting-insider-threat-behaviors` | Clinic staff with PHI access are the main insider risk; audit-trail completeness and privilege-abuse controls are reviewable in source. | privilege abuse, unauthorized PHI access, audit tampering | r3-data-at-rest / r3-h-admin / r3-h-audit-secrets / r3-h-auth-session / r3-h-backup / r3-h-patients-rbac / r3-h-resource | RBAC/scope review — no finding; scope fails closed; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; auth/session review — 1 needs_validation (session not bound to credential version), fixed in 86a4a2d; backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac; staff/department admin review — no finding; run-2 partial-commit closed |
| `detecting-shadow-api-endpoints` | No API gateway or traffic to analyse, but source-code mining for undocumented or forgotten FastAPI routes applies. | shadow/zombie API endpoints, undocumented attack surface | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `exploiting-broken-link-hijacking` | Same-origin SPA under strict CSP, so risk is low. Any external script, font or doc link references can still be reviewed in source. | broken link hijacking, external resource takeover | r3-h-resource | resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `exploiting-http-request-smuggling` | Uvicorn behind an nginx/Caddy proxy is a front-end/back-end pair, but the proxy config is not in the repo and live testing is prohibited. | CL/TE desync, proxy header trust | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `exploiting-insecure-deserialization` | Python app with JSON import, localStorage migration and encrypted backups. Only the Python deserialization class applies. | insecure deserialization, unsafe object loading | r3-h-patients-rbac | RBAC/scope review — no finding; scope fails closed |
| `exploiting-prototype-pollution-in-javascript` | No Node.js, but the vanilla-JS SPA merges JSON from the API and localStorage migration data, so client-side pollution is reviewable. | client-side prototype pollution, DOM XSS gadget | r3-h-client | SPA/DOM/CSP review — no finding |
| `exploiting-server-side-request-forgery` | The only outbound HTTP is the AI assistant's urllib call to an operator-configured base URL. Check that users cannot influence the target. | SSRF, outbound request control | r3-h-ai | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56 |
| `exploiting-sql-injection-with-sqlmap` | sqlmap is live-tool driven and cannot run against production. Its checks can be done by source review or a local instance only. | SQL injection | r3-h-notifications | notification queue/templates review — no finding |
| `exploiting-template-injection-vulnerabilities` | The static SPA likely has no server templates, but check for Jinja2 or format-string templating of user data (CSV, notifications, SMS text, AI prompts). | SSTI, format-string injection | r3-h-notifications | notification queue/templates review — no finding |
| `fix-security-vulnerabilities-with-strix` | Remediation guidance (root-cause patching, regression tests) applies to findings; Strix output is absent. | remediation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `generating-and-analyzing-sboms` | No container images. A filesystem or requirements SBOM with CVE correlation still applies to the pip dependencies, run offline or by review. | vulnerable dependencies, supply-chain inventory | r3-h-chain / r3-h-supply | CI/dependency review — no finding; cross-component chain review — no finding |
| `hardening-linux-endpoint-with-cis-benchmark` | Host hardening is operator-managed and not in the repo. File permissions and service-user guidance for the DB, backups and keys can be checked. | insecure file permissions, service privilege | r3-data-at-rest / r3-h-admin / r3-h-backup / r3-h-supply | CI/dependency review — no finding; backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; staff/department admin review — no finding; run-2 partial-commit closed |
| `hunting-credential-stuffing-attacks` | No SIEM or log hunting, but the app's login rate limiting, lockout and auth-event logging are credential-stuffing defences to review. | credential stuffing, password spraying, brute force | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `hunting-for-supply-chain-compromise` | The SIEM hunting part does not apply, but the T1195 threat model does fit the pip dependencies and GitHub Actions CI. | supply-chain compromise, trojanized dependency, CI pipeline tampering | r3-h-backup / r3-h-chain / r3-h-supply | CI/dependency review — no finding; backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; cross-component chain review — no finding |
| `hunting-for-suspicious-scheduled-tasks` | Host scheduled-task hunting is SOC work, but the app's own scheduled jobs (notification worker, purge-sessions, audit-prune cron guidance) can be reviewed for privilege and input handling. | scheduled job abuse, privileged cron execution | r3-h-resource | resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `hunting-for-t1098-account-manipulation` | The AD/SID-history parts do not apply, but account manipulation also covers the app's own role changes, admin resets and audit trail. | account manipulation, privilege escalation via role change, shadow admin | r3-data-at-rest / r3-h-admin | data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700; staff/department admin review — no finding; run-2 partial-commit closed |
| `implementing-api-abuse-detection-with-rate-limiting` | Redis is absent, but the rate-limiting and abuse-detection method applies to the in-memory login, re-auth and API limits. | brute force, credential stuffing, API abuse, rate-limit bypass | r3-h-resource | resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `implementing-api-gateway-security-controls` | The project has no API gateway, but the checklist of controls maps onto the FastAPI middleware and the proxy assumptions. | missing centralized auth, request size abuse, header trust | r3-h-http-csrf / r3-h-resource | CSRF/headers/HTTP review — no finding; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `implementing-api-key-security-controls` | There are no API keys, but key entropy, hashed storage and leak scanning carry over to opaque session and CSRF tokens. | weak token entropy, plaintext token storage, secret leakage | r3-h-audit-secrets / r3-h-backup / r3-h-client | SPA/DOM/CSP review — no finding; audit/secrets review — 1 needs_validation (unaudited history writes), fixed in d277620; backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `implementing-api-rate-limiting-and-throttling` | The throttling algorithms and per-user/per-IP design apply as a checklist to the in-memory limiter. | brute force, resource exhaustion, rate-limit bypass | r3-h-resource | resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `implementing-api-security-posture-management` | The API-SPM platform does not apply, but endpoint inventory and classification can be done by source review. | shadow endpoints, unclassified sensitive data exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-api-security-testing-with-42crunch` | The 42Crunch platform cannot be used here, but its OpenAPI audit ideas can be checked against the generated schema. | weak API contract, missing security definitions | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-code-signing-for-artifacts` | No binaries or packages are released, but backup integrity and CI artifact provenance ideas partly apply. | artifact tampering, unsigned downloads | r3-h-backup | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `implementing-devsecops-security-scanning` | The CI exists, so SAST/SCA gaps can be reviewed; the container and DAST parts do not apply. | unscanned vulnerable dependencies, missing SAST | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-gdpr-data-protection-controls` | The app processes patient PHI, so privacy-by-design, minimization, retention and access logging controls apply as a checklist. | excessive data exposure, missing retention, inadequate audit | r3-skill-remanence | SQLite remanence review — no finding; secure_delete/VACUUM recorded as hardening |
| `implementing-gdpr-data-subject-access-request` | The DSAR tooling is external, but PII discovery and export-scoping checks apply to the patient data export features. | unauthorized data export, incomplete erasure | r3-skill-remanence | SQLite remanence review — no finding; secure_delete/VACUUM recorded as hardening |
| `implementing-github-advanced-security-for-code-scanning` | The repo uses GitHub Actions and Dependabot; CodeQL and secret-scanning coverage can be checked from the configs. | undetected code vulnerabilities, secret leakage, vulnerable dependencies | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-hipaa-security-rule-safeguards` | The app stores ePHI, so the technical safeguards apply (access control, audit, integrity, transmission, authentication); the administrative parts do not. | unauthorized PHI access, missing audit controls, unencrypted ePHI | r3-data-at-rest / r3-h-backup | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700 |
| `implementing-identity-verification-for-zero-trust` | No zero-trust or MFA stack. Password auth, re-auth and session revocation can still be reviewed against continuous-verification principles. | weak authentication assurance, session hijacking, missing MFA | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-immutable-backup-with-restic` | No restic or S3. The app has its own AES-GCM encrypted backup/verify/restore CLI, so integrity and restore-testing principles apply. | backup tampering, backup integrity failure, ransomware recovery gaps | r3-h-backup | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `implementing-iso-27001-information-security-management` | ISMS governance, not app code. Annex A controls (access, crypto, logging) can serve as a checklist. | control gaps, governance gaps | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-just-in-time-access-provisioning` | No PAM/IGA. Standing privileged roles (developer) and admin reset flows can be reviewed for least privilege and expiry. | standing privilege, privilege escalation | r3-data-at-rest | data-at-rest review — no finding; DB/WAL permissions confirmed 0600/0700 |
| `implementing-log-integrity-with-blockchain` | The app has an audit log and an audit-prune CLI. Hash-chain tamper-evidence concepts apply as a review lens. | audit log tampering, repudiation | r3-h-backup / r3-h-chain | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; cross-component chain review — no finding |
| `implementing-pam-for-database-access` | No DBA/PAM stack. Direct access to the single SQLite file and operator CLI access can be reviewed for least privilege and auditing. | direct DB access bypassing app controls, unaudited privileged operations | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-patch-management-workflow` | No WSUS/SCCM fleet. Dependency patching via pinned pip requirements and Dependabot applies. | vulnerable dependencies, outdated components | r3-h-patient-logic | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc |
| `implementing-privileged-session-monitoring` | No PSM tooling. Audit trails for privileged app actions (developer/admin) fit the same objective. | unaudited privileged actions, repudiation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `implementing-ransomware-backup-strategy` | Backup architecture is operator-side. The app's encrypted backup, key isolation and restore verification can be reviewed. | backup compromise, key co-location, unrecoverable backups | r3-h-backup / r3-h-resource | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `implementing-rsa-key-pair-management` | No RSA. Key lifecycle principles (generation, storage, permissions, rotation) apply to the AES-256 backup key. | weak key management, key exposure | r3-h-backup | backup/restore/CLI review — 1 needs_validation (verify decrypt in /tmp), fixed in 4c6fb9c |
| `implementing-runtime-application-self-protection` | No RASP agent. The underlying injection/XSS/RCE sink classes in the Python web app can be reviewed in source. | SQL injection, XSS, command injection | r3-h-client | SPA/DOM/CSP review — no finding |
| `implementing-secrets-management-with-vault` | No Vault. How secrets (backup key, AI API key, bootstrap credentials) are loaded and protected can be reviewed. | hardcoded credentials, secret exposure via env/logs | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `integrating-dast-with-owasp-zap-in-pipeline` | No live target allowed. A ZAP baseline against a local test instance, or equivalent manual checks, fits bounded local testing. | XSS, SQL injection, CSRF, missing security headers, auth/authz flaws | r3-h-client / r3-h-http-csrf / r3-h-obvious | CSRF/headers/HTTP review — no finding; SPA/DOM/CSP review — no finding; baseline exposure review — no finding |
| `migrating-to-post-quantum-cryptography` | No TLS/VPN/SSH endpoints in repo, but a crypto inventory (AES-256-GCM backups, Argon2id) can be built from source review. | weak or legacy cryptography, crypto agility | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `orchestrating-llm-attacks-with-pyrit` | The optional AI assistant does LLM tool calling. PyRIT automation can't run against production, but multi-turn jailbreak and data-leak cases apply locally. | prompt injection, multi-turn jailbreak, LLM data exfiltration via tools, excessive agency | r3-h-ai | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56 |
| `owasp-top-10-testing` | OWASP Top 10:2025 categories apply as a checklist; autonomous live exploitation prohibited. | broken access control, misconfiguration, supply chain, crypto failures, injection, auth failures, logging failures | r3-h-chain / r3-h-obvious | baseline exposure review — no finding; cross-component chain review — no finding |
| `penetration-testing-with-strix` | Web app/codebase in scope, but live autonomous pentest prohibited; bounded local testing on test DB only. | web app exploitation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-access-review-and-certification` | Campaign process doesn't apply, but its role/entitlement checks map onto the app's RBAC and account lifecycle. | excessive privilege, stale accounts, segregation of duties | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-api-fuzzing-with-restler` | FastAPI generates an OpenAPI spec, so bounded local fuzzing against a test instance is possible. Production fuzzing is prohibited. | input validation failures, unhandled exceptions and 500s, state-dependent authorization bugs | r3-h-patient-logic | business-logic review — 1 needs_validation (update/import validation parity), fixed in c17dcdc |
| `performing-api-inventory-and-discovery` | Discovery tooling isn't usable, but enumerating every FastAPI route and its auth dependency by source review applies directly. | shadow/undocumented endpoints, unauthenticated routes, debug/dev endpoints | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-api-security-testing-with-postman` | Postman isn't needed, but the OWASP API Top 10 multi-role method applies to the RBAC API through local tests or source review. | BOLA/IDOR, broken function-level authorization, mass assignment, excessive data exposure, injection | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-blind-ssrf-exploitation` | The only outbound HTTP is urllib to the AI base URL. Check whether any user input can change the destination or redirects. | SSRF, outbound request manipulation | r3-h-ai | AI assistant review — 1 needs_validation (cleartext provider transport), fixed in 7739b56 |
| `performing-container-security-scanning-with-trivy` | No images, but Trivy-style repo/filesystem checks of Python dependency CVEs and committed secrets apply offline or by source review. | vulnerable dependencies, secret exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-cve-prioritization-with-kev-catalog` | Fetching KEV/EPSS isn't allowed, but prioritising CVEs in the pinned dependencies by exploitability is applicable offline. | vulnerable dependencies | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-directory-traversal-testing` | No upload endpoints, but the static file mount and CLI backup/restore/import paths handle filesystem paths. | path traversal, arbitrary file read/write | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-hash-cracking-with-hashcat` | Cracking isn't the goal, but assessing Argon2id cost and password policy for offline-crack resistance applies. | offline password cracking, weak password policy | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-http-parameter-pollution-attack` | No WAF or multi-tier parser mismatch in repo, but duplicate query/body parameter handling in FastAPI filters and scope checks can be reviewed. | HTTP parameter pollution, filter bypass | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-insider-threat-investigation` | Trusted staff can misuse authorized PHI access; the audit trail must be good enough to investigate them. | privilege misuse, insider data exfiltration, audit evasion | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-log-analysis-for-forensic-investigation` | Application and audit logs must support reconstructing incidents without leaking PHI. | insufficient logging, log injection, sensitive data in logs | r3-skill-remanence | SQLite remanence review — no finding; secure_delete/VACUUM recorded as hardening |
| `performing-post-quantum-cryptography-migration` | A crypto inventory applies: AES-256-GCM backups and Argon2id; TLS is at the operator proxy. | weak or legacy cryptography, harvest-now-decrypt-later | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-privacy-impact-assessment` | App processes patient PHI; data-flow mapping, minimization and retention checks apply (GDPR specifics may not). | excessive data exposure, PHI leakage, over-retention | r3-skill-remanence | SQLite remanence review — no finding; secure_delete/VACUUM recorded as hardening |
| `performing-privilege-escalation-assessment` | No target host, but files and CLI paths the app creates can be reviewed in source. | insecure file permissions, local privilege escalation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-privilege-escalation-on-linux` | Only the app-created file permission and secret-file checks apply; OS exploitation is out of scope. | insecure file permissions, secret exposure on disk | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-privileged-account-access-review` | RBAC has privileged roles (developer, deputy chief, caop_head); the least-privilege review applies to app accounts. | excessive privilege, stale accounts, vertical privilege escalation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-privileged-account-discovery` | Discovery of privileged/default accounts applies to app bootstrap and seed code. | default credentials, hidden admin accounts | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-sca-dependency-scanning-with-snyk` | Pip dependencies and Dependabot exist; Snyk cannot run, but equivalent SCA review applies. | vulnerable dependencies, unpinned transitive dependencies | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-service-account-audit` | There is no enterprise IAM, but non-human identities exist (notifications worker, CLI, AI provider credential). | over-privileged service identities, credential exposure | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-service-account-credential-rotation` | Backup encryption keys and the AI provider key need rotation support; there is no AD or cloud. | stale secrets, key rotation gaps | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-ssl-stripping-attack` | HTTPS lives at the proxy, but the app controls HSTS and Secure cookies via CLINIC_HTTPS. | HTTPS downgrade, cookie leakage over HTTP | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `performing-ssl-tls-security-assessment` | The server TLS config is out of repo, but outbound urllib TLS to the AI provider can be reviewed. | disabled certificate verification, plaintext transport | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `performing-ssrf-vulnerability-exploitation` | The only outbound HTTP is to the operator-configured AI base URL; check that users cannot influence it. | SSRF | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-supply-chain-attack-simulation` | Pip requirements and GitHub Actions CI present; typosquatting and dependency-confusion checks can be done by review. | typosquatting, dependency confusion, CI compromise | r3-h-chain | cross-component chain review — no finding |
| `performing-web-application-scanning-with-nikto` | Only its checks apply (default files, exposed docs, headers), verified against source or a local instance. | information disclosure, exposed debug endpoints | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-web-application-vulnerability-triage` | OWASP risk-rating triage is usable for scoring this audit's findings. | finding validation | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `performing-web-cache-deception-attack` | Proxy config is out of repo, but the app's cache headers and path handling decide the exposure. | web cache deception | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `performing-web-cache-poisoning-attack` | No cache in the repo, but unkeyed-header handling (Host, X-Forwarded-*) in the app is reviewable. | web cache poisoning, host header injection | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `prioritizing-vulnerabilities-with-cvss-scoring` | No scanner pipeline, but CVSS scoring can rate audit findings and dependency CVEs consistently. | vulnerability severity rating | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `red-teaming-llms-with-garak` | Optional OpenAI-compatible AI assistant exists; garak probes can't run against production, but its probe categories work as a review checklist. | prompt injection, jailbreak, data leakage via LLM | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `scanning-container-images-with-grype` | No containers, but Grype's SBOM/filesystem CVE matching applies to pip requirements via offline or source review. | vulnerable dependencies | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `scanning-containers-with-trivy-in-cicd` | No containers; the dependency-scanning-in-CI part applies to the GitHub Actions pipeline. | vulnerable dependencies, missing CI security gates | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `scanning-iac-and-images-with-trivy` | No IaC/images; the filesystem/repo scan mode applies to Python deps and secrets. | vulnerable dependencies, committed secrets | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-api-security-with-owasp-top-10` | REST API present (no GraphQL/gRPC); OWASP API Top 10 works as a source-review checklist, but Burp can't run live. | BOLA, broken auth, BOPLA, resource consumption, BFLA, SSRF, misconfiguration | r3-h-obvious / r3-h-resource | baseline exposure review — no finding; resource/DoS review — 1 needs_validation (AI worker-pool occupancy), fixed in ca702ac |
| `testing-cors-misconfiguration` | Same-origin SPA; only need to confirm no permissive CORS middleware exists. | CORS misconfiguration | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `testing-for-host-header-injection` | No password-reset emails, but Host/X-Forwarded headers behind proxy may affect URLs, rate-limit keys, or Secure-cookie logic. | host header injection, forwarded header spoofing | r3-h-http-csrf | CSRF/headers/HTTP review — no finding |
| `testing-for-open-redirect-vulnerabilities` | SPA with login flow; only need to check for redirect/next parameters in server or app.js. | open redirect | r3-h-client | SPA/DOM/CSP review — no finding |
| `testing-for-system-prompt-leakage` | Optional AI assistant exists; relevant only if system prompt contains secrets or scope data. | system prompt leakage | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-for-xss-vulnerabilities-with-burpsuite` | XSS relevant, but Burp live scanning unavailable; do it via source review of app.js. | stored XSS, DOM XSS | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-mobile-api-authentication` | No mobile app, but its session-fixation/token management checks apply to the web session API. | session fixation, insecure session management | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-prompt-injection-in-rag-pipelines` | No RAG/embeddings, but assistant tool results include user-authored patient notes, so indirect injection applies. | indirect prompt injection | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `testing-ransomware-recovery-procedures` | Project has encrypted backup/restore CLI; restore-test portions apply locally, org RTO/RPO parts do not. | backup/restore failure, unsafe restore | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `triaging-vulnerabilities-with-ssvc-framework` | No scanner output, but SSVC decisions can prioritize audit findings and dependency CVEs. | vulnerability prioritization | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |
| `verifying-build-provenance-with-slsa-sigstore` | No signed artifacts are published; relevant only to CI integrity of downloaded tools and pip deps. | supply chain tampering | r3-h-chain | cross-component chain review — no finding |
| `web-app-penetration-testing` | Web app present, but black-box live testing prohibited; allowed only against a local dev server. | web app vulnerabilities | — | Checks duplicated those of another skill in the same attack class (deduplicated before distribution); the class is covered by the units listed in REPORT.md. |

## Not applicable (672)

| Skill | Subdomain | Reason |
|---|---|---|
| `abusing-dpapi-for-credential-access` | red-teaming | No Windows hosts, DPAPI, or Active Directory; this is a Python/SQLite web app. |
| `abusing-shadow-credentials-for-privesc` | red-teaming | No Active Directory, Kerberos, or PKINIT. |
| `achieving-cmmc-level-2-compliance` | compliance-governance | DoD CUI/DFARS compliance program; the clinic PHI app is outside that regime. This is governance, not a source-review method. |
| `acquiring-disk-image-with-dd-and-dcfldd` | digital-forensics | Forensic disk acquisition, not app code review. No incident host is in scope. |
| `analyzing-active-directory-acl-abuse` | identity-security | No Active Directory or LDAP. |
| `analyzing-android-malware-with-apktool` | malware-analysis | No mobile or native Android code; malware analysis. |
| `analyzing-apt-group-with-mitre-navigator` | threat-intelligence | Threat-intel ATT&CK mapping, not app code. |
| `analyzing-azure-activity-logs-for-threats` | security-operations | No Azure or cloud provider. |
| `analyzing-bootkit-and-rootkit-samples` | malware-analysis | Firmware/bootkit malware analysis; no firmware or malware in scope. |
| `analyzing-browser-forensics-with-hindsight` | digital-forensics | Browser forensics on user profiles in DFIR, not source review. |
| `analyzing-campaign-attribution-evidence` | threat-intelligence | Threat-actor attribution, not app code. |
| `analyzing-certificate-transparency-for-phishing` | threat-intelligence | External CT/domain monitoring; no network scanning allowed. TLS is terminated by an operator proxy outside the repo. |
| `analyzing-cloud-storage-access-patterns` | cloud-security | No S3, GCS, Azure Blob, or cloud audit logs. |
| `analyzing-cobalt-strike-beacon-configuration` | malware-analysis | Malware/C2 analysis; no malware in scope. |
| `analyzing-cobaltstrike-malleable-c2-profiles` | malware-analysis | C2 profile analysis; no malware in scope. |
| `analyzing-command-and-control-communication` | malware-analysis | Malware C2 protocol reverse engineering; no malware in scope. |
| `analyzing-cyber-kill-chain` | threat-intelligence | Post-incident intrusion analysis framework. No incident exists to analyze. |
| `analyzing-disk-image-with-autopsy` | digital-forensics | Disk-image forensics, not source review. |
| `analyzing-dns-logs-for-exfiltration` | soc-operations | SOC/SIEM DNS log analysis, not app code. |
| `analyzing-docker-container-forensics` | digital-forensics | No Docker or containers. |
| `analyzing-email-headers-for-phishing-investigation` | digital-forensics | The app sends no email; this is phishing forensics. |
| `analyzing-ethereum-smart-contract-vulnerabilities` | blockchain-security | No blockchain or Solidity. |
| `analyzing-golang-malware-with-ghidra` | malware-analysis | Go binary malware reverse engineering; the project is Python and has no malware. |
| `analyzing-heap-spray-exploitation` | malware-analysis | Memory-dump exploit forensics; no native code or memory dumps. |
| `analyzing-indicators-of-compromise` | threat-intelligence | IOC triage using external intel services; SOC/CTI work, not app code. |
| `analyzing-ios-app-security-with-objection` | mobile-security | No iOS or mobile native app. |
| `analyzing-kubernetes-audit-logs` | container-security | No Kubernetes. |
| `analyzing-linux-audit-logs-for-intrusion` | incident-response | Host auditd intrusion analysis; the host is operator-managed and outside the repo. |
| `analyzing-linux-elf-malware` | malware-analysis | ELF malware analysis; no binaries or malware. |
| `analyzing-linux-kernel-rootkits` | digital-forensics | Kernel rootkit memory forensics; not app code. |
| `analyzing-linux-system-artifacts` | digital-forensics | Forensics on a compromised Linux host, not source review. |
| `analyzing-lnk-file-and-jump-list-artifacts` | digital-forensics | No Windows hosts; this is Windows forensics. |
| `analyzing-macro-malware-in-office-documents` | malware-analysis | No Office document handling or file uploads. |
| `analyzing-malicious-pdf-with-peepdf` | malware-analysis | No PDF processing or file uploads. |
| `analyzing-malicious-url-with-urlscan` | phishing-defense | External URL scanning service, which the audit prohibits; phishing defense. |
| `analyzing-malware-behavior-with-cuckoo-sandbox` | malware-analysis | Malware sandboxing; no malware in scope. |
| `analyzing-malware-family-relationships-with-malpedia` | threat-intelligence | Malware threat intel; not app code. |
| `analyzing-malware-persistence-with-autoruns` | malware-analysis | Windows persistence forensics; no Windows hosts. |
| `analyzing-malware-sandbox-evasion-techniques` | malware-analysis | Malware anti-analysis research; no malware in scope. |
| `analyzing-memory-dumps-with-volatility` | malware-analysis | RAM forensics; not app source review. |
| `analyzing-memory-forensics-with-lime-and-volatility` | security-operations | Linux memory acquisition for incident response; not app code. |
| `analyzing-mft-for-deleted-file-recovery` | digital-forensics | NTFS forensics; no Windows hosts. |
| `analyzing-network-covert-channels-in-malware` | malware-analysis | Malware covert-channel network forensics; not app code. |
| `analyzing-network-flow-data-with-netflow` | network-security | NetFlow analysis; no network devices or flow data. |
| `analyzing-network-packets-with-scapy` | network-security | Packet crafting and sniffing; network probing is prohibited and there is no custom protocol. |
| `analyzing-network-traffic-for-incidents` | incident-response | PCAP incident analysis; SOC/IR work, not app code. |
| `analyzing-network-traffic-of-malware` | malware-analysis | Malware traffic analysis; no malware in scope. |
| `analyzing-network-traffic-with-wireshark` | network-security | Packet capture of network segments; out of scope for source review. |
| `analyzing-office365-audit-logs-for-compromise` | cloud-security | No Office 365 or Microsoft Graph. |
| `analyzing-outlook-pst-for-email-forensics` | digital-forensics | No email or Outlook data; forensics. |
| `analyzing-packed-malware-with-upx-unpacker` | malware-analysis | Binary unpacking; no malware or binaries. |
| `analyzing-pdf-malware-with-pdfid` | malware-analysis | No PDF handling or uploads. |
| `analyzing-persistence-mechanisms-in-linux` | threat-hunting | Host persistence hunting on a compromised system; the operator host is outside the repo. |
| `analyzing-powershell-empire-artifacts` | threat-hunting | No Windows or PowerShell. |
| `analyzing-powershell-script-block-logging` | security-operations | No Windows or PowerShell. |
| `analyzing-prefetch-files-for-execution-history` | digital-forensics | Windows forensics; no Windows hosts. |
| `analyzing-ransomware-encryption-mechanisms` | malware-analysis | Ransomware cryptanalysis for malware samples. The AES-GCM backup crypto is better covered by dedicated crypto-audit skills. |
| `analyzing-ransomware-leak-site-intelligence` | threat-intelligence | Tor leak-site threat intel; not app code. |
| `analyzing-ransomware-network-indicators` | threat-hunting | Zeek/NetFlow threat hunting; SOC operations. |
| `analyzing-ransomware-payment-wallets` | ransomware-defense | Blockchain payment tracing; no crypto payments. |
| `analyzing-security-logs-with-splunk` | incident-response | Splunk SIEM investigation; SOC operations, not app code. |
| `analyzing-slack-space-and-file-system-artifacts` | digital-forensics | NTFS forensics; no Windows hosts. |
| `analyzing-threat-actor-ttps-with-mitre-attack` | threat-intelligence | CTI ATT&CK mapping of actors; not app code. |
| `analyzing-threat-actor-ttps-with-mitre-navigator` | threat-intelligence | APT TTP Navigator layers; threat intel, not app code. |
| `analyzing-threat-intelligence-feeds` | threat-intelligence | CTI feed ingestion; not app code. |
| `analyzing-threat-landscape-with-misp` | threat-intelligence | MISP threat-intel reporting; not app code. |
| `analyzing-tls-certificate-transparency-logs` | security-operations | External CT monitoring; TLS is handled by an operator proxy outside the repo, and no external queries are allowed. |
| `analyzing-typosquatting-domains-with-dnstwist` | threat-intelligence | Lookalike-domain monitoring over DNS; external network activity is prohibited and it isn't about app code. |
| `analyzing-uefi-bootkit-persistence` | firmware-security | No firmware or UEFI in scope. |
| `analyzing-usb-device-connection-history` | digital-forensics | Windows USB forensics; no Windows hosts. |
| `analyzing-windows-amcache-artifacts` | digital-forensics | Windows forensics; no Windows hosts. |
| `analyzing-windows-event-logs-in-splunk` | soc-operations | No Windows or AD; SOC/SIEM operations. |
| `analyzing-windows-lnk-files-for-artifacts` | digital-forensics | Windows forensics; no Windows hosts. |
| `analyzing-windows-prefetch-with-python` | digital-forensics | Windows forensics; no Windows hosts. |
| `analyzing-windows-registry-for-artifacts` | digital-forensics | Windows registry forensics; no Windows hosts. |
| `analyzing-windows-shellbag-artifacts` | digital-forensics | Windows forensics; no Windows hosts. |
| `assessing-vector-and-embedding-weaknesses` | ai-security | No vector store, embeddings, or RAG. The AI assistant only uses read-only tool calling. |
| `attacking-entra-id-with-roadtools` | identity-access-management | No Entra ID, Azure AD, or tokens. |
| `attacking-oauth-with-device-code-phishing` | identity-access-management | No OAuth or Entra ID. |
| `auditing-aws-s3-bucket-permissions` | cloud-security | No AWS or S3. |
| `auditing-azure-active-directory-configuration` | cloud-security | No Azure AD or Entra ID. |
| `auditing-cloud-with-cis-benchmarks` | cloud-security | No cloud provider. |
| `auditing-entra-id-with-aadinternals` | identity-access-management | No Entra ID, AD FS, or SAML. |
| `auditing-foundry-smart-contract-security` | blockchain-security | No Solidity, Foundry, or blockchain. |
| `auditing-gcp-iam-permissions` | cloud-security | No GCP or cloud IAM. |
| `auditing-kubernetes-cluster-rbac` | cloud-security | No Kubernetes. |
| `auditing-kubernetes-rbac-privilege-escalation` | container-security | No Kubernetes. |
| `auditing-terraform-infrastructure-for-security` | cloud-security | No Terraform or other IaC in the repository; deployment is a single uvicorn process behind an operator proxy. |
| `auditing-tls-certificate-transparency-logs` | threat-intelligence | CT-log monitoring for owned domains; no deployed domain in scope, live/external querying prohibited. |
| `auditing-uefi-firmware-with-chipsec` | hardware-firmware-security | No hardware/firmware in scope; Python web app only. |
| `automating-ioc-enrichment` | threat-intelligence | SOC/TIP IOC enrichment operations, not app code. |
| `benchmarking-kubernetes-with-kube-bench` | container-security | No Kubernetes. |
| `building-adversary-infrastructure-tracking-system` | threat-intelligence | Threat-intel C2 infrastructure tracking; not app code. |
| `building-attack-pattern-library-from-cti-reports` | threat-intelligence | CTI report parsing/ATT&CK library building; not app code. |
| `building-automated-malware-submission-pipeline` | soc-operations | SOC malware sandbox pipeline; no file uploads or malware handling. |
| `building-c2-infrastructure-with-sliver-framework` | red-teaming | Red-team C2 deployment; unrelated to app. |
| `building-c2-redirector-infrastructure` | red-teaming | Red-team C2 redirectors; reverse proxy config not in repo and not C2. |
| `building-cloud-siem-with-sentinel` | cloud-security | No cloud provider; SIEM operations, not app code. |
| `building-detection-rule-with-splunk-spl` | soc-operations | SOC/SIEM Splunk detection rules, not app code. |
| `building-detection-rules-with-sigma` | soc-operations | SIEM detection rule authoring, not app code. |
| `building-identity-federation-with-saml-azure-ad` | identity-access-management | No SAML/SSO/Azure AD/Entra. |
| `building-incident-response-dashboard` | soc-operations | SOC IR dashboards in Splunk/Elastic/Grafana; not app code. |
| `building-incident-response-playbook` | incident-response | Organizational IR program documentation, not source-reviewable app behavior. |
| `building-incident-timeline-with-timesketch` | incident-response | DFIR timeline tooling; no incident artifacts in scope. |
| `building-ioc-defanging-and-sharing-pipeline` | threat-intelligence | Threat-intel IOC sharing pipeline; not app code. |
| `building-ioc-enrichment-pipeline-with-opencti` | threat-intelligence | OpenCTI threat-intel platform; not app code. |
| `building-malware-incident-communication-template` | incident-response | Incident communication templates; process, not app code. |
| `building-patch-tuesday-response-process` | vulnerability-management | Microsoft/Windows patch management; no Windows hosts. |
| `building-phishing-reporting-button-workflow` | phishing-defense | Email client phishing reporting; no email in product. |
| `building-ransomware-playbook-with-cisa-framework` | ransomware-defense | Organizational ransomware IR playbook; not app code. |
| `building-red-team-c2-infrastructure-with-havoc` | red-teaming | Red-team C2 framework deployment; unrelated. |
| `building-soc-escalation-matrix` | soc-operations | SOC operations process, not app code. |
| `building-soc-metrics-and-kpi-tracking` | soc-operations | SOC metrics/KPI operations, not app code. |
| `building-soc-playbook-for-ransomware` | soc-operations | SOC ransomware playbook; not app code. |
| `building-super-timelines-with-plaso` | digital-forensics | Disk/host forensics tooling; no forensic images in scope. |
| `building-threat-actor-profile-from-osint` | threat-intelligence | Threat-actor OSINT profiling; not app code. |
| `building-threat-feed-aggregation-with-misp` | threat-intelligence | MISP threat-feed deployment; not app code. |
| `building-threat-hunt-hypothesis-framework` | threat-hunting | EDR/SIEM threat hunting; no such tooling. |
| `building-threat-intelligence-enrichment-in-splunk` | soc-operations | Splunk SIEM enrichment; not app code. |
| `building-threat-intelligence-feed-integration` | soc-operations | STIX/TAXII SIEM feed integration; not app code. |
| `building-threat-intelligence-platform` | threat-intelligence | TIP architecture (MISP/OpenCTI); not app code. |
| `building-vulnerability-aging-and-sla-tracking` | vulnerability-management | Vulnerability-management program metrics; not app code. |
| `building-vulnerability-dashboard-with-defectdojo` | vulnerability-management | DefectDojo deployment; no scanner-aggregation infrastructure. |
| `building-vulnerability-exception-tracking-system` | vulnerability-management | Risk-acceptance governance process; not app code. |
| `building-vulnerability-scanning-workflow` | soc-operations | Nessus/Qualys infrastructure scanning; network scanning prohibited, no infra. |
| `codex-ppt` | extra | Presentation generation, not security. |
| `coercing-authentication-with-coercer-petitpotam` | red-teaming | No Active Directory/Windows/NTLM. |
| `collecting-indicators-of-compromise` | incident-response | IR IOC collection/sharing; not app code. |
| `collecting-open-source-intelligence` | threat-intelligence | External threat-actor OSINT; not app code, external recon prohibited. |
| `collecting-threat-intelligence-with-misp` | threat-intelligence | MISP threat-intel collection; not app code. |
| `collecting-volatile-evidence-from-compromised-host` | incident-response | Host DFIR evidence collection; no compromised host in scope. |
| `conducting-cloud-incident-response` | incident-response | No cloud provider (AWS/Azure/GCP). |
| `conducting-cloud-penetration-testing` | cloud-security | No cloud provider/IAM. |
| `conducting-domain-persistence-with-dcsync` | red-teaming | No Active Directory. |
| `conducting-external-reconnaissance-with-osint` | penetration-testing | No reachable deployment; external reconnaissance prohibited by audit constraints. |
| `conducting-full-scope-red-team-engagement` | red-teaming | Full-scope enterprise red team; live testing prohibited, source review only. |
| `conducting-internal-network-penetration-test` | penetration-testing | No corporate network/AD; Responder/Impacket-based lateral movement irrelevant. |
| `conducting-internal-reconnaissance-with-bloodhound-ce` | red-teaming | No Active Directory/Entra ID. |
| `conducting-malware-incident-response` | incident-response | Endpoint malware IR; no malware in scope. |
| `conducting-memory-forensics-with-volatility` | incident-response | RAM forensics; no memory dumps in scope. |
| `conducting-mobile-app-penetration-test` | penetration-testing | No mobile native apps. |
| `conducting-network-penetration-test` | penetration-testing | Network scanning/exploitation prohibited; single localhost-bound service, no network infrastructure. |
| `conducting-pass-the-ticket-attack` | red-teaming | No Kerberos/Active Directory. |
| `conducting-phishing-incident-response` | incident-response | Email phishing IR; no email in product. |
| `conducting-post-incident-lessons-learned` | incident-response | Post-incident review process; not app code. |
| `conducting-social-engineering-penetration-test` | penetration-testing | Human-targeted campaigns; out of source-review scope. |
| `conducting-social-engineering-pretext-call` | red-teaming | Vishing assessment; out of source-review scope. |
| `conducting-spearphishing-simulation-campaign` | red-teaming | Phishing simulation infrastructure; out of scope. |
| `conducting-wireless-network-penetration-test` | penetration-testing | No wireless. |
| `configuring-active-directory-tiered-model` | identity-access-management | No Active Directory. |
| `configuring-aws-verified-access-for-ztna` | zero-trust-architecture | No AWS/zero-trust products. |
| `configuring-certificate-authority-with-openssl` | cryptography | No PKI/CA or mTLS; TLS terminated by operator proxy. |
| `configuring-host-based-intrusion-detection` | endpoint-security | Host HIDS (OSSEC/Wazuh/AIDE) is operator infrastructure outside the repo; no app code to review. |
| `configuring-identity-aware-proxy-with-google-iap` | zero-trust-architecture | No GCP/IAP. |
| `configuring-ldap-security-hardening` | identity-access-management | No LDAP/directory services. |
| `configuring-microsegmentation-for-zero-trust` | zero-trust-architecture | No NSX/Illumio/Calico or multi-workload network. |
| `configuring-multi-factor-authentication-with-duo` | identity-access-management | No Duo/MFA integration; password-only auth (MFA absence noted elsewhere as design gap). |
| `configuring-network-segmentation-with-vlans` | network-security | No network devices/VLANs. |
| `configuring-oauth2-authorization-flow` | identity-access-management | No OAuth/OIDC. |
| `configuring-pfsense-firewall-rules` | network-security | No firewall devices. |
| `configuring-snort-ids-for-intrusion-detection` | network-security | No network IDS tooling. |
| `configuring-suricata-for-network-monitoring` | network-security | No network IDS/IPS tooling. |
| `configuring-windows-defender-advanced-settings` | endpoint-security | No Windows hosts/EDR. |
| `configuring-windows-event-logging-for-detection` | endpoint-security | No Windows hosts. |
| `configuring-zscaler-private-access-for-ztna` | zero-trust-architecture | No Zscaler/ZTNA products. |
| `containing-active-breach` | incident-response | Active breach containment operations; no incident in scope. |
| `correlating-security-events-in-qradar` | soc-operations | QRadar SIEM operations, not app code. |
| `correlating-threat-campaigns` | threat-intelligence | Threat-intel campaign correlation; not app code. |
| `deobfuscating-javascript-malware` | malware-analysis | Malware analysis; first-party vanilla JS with no third-party scripts, no samples in scope. |
| `deobfuscating-powershell-obfuscated-malware` | malware-analysis | No PowerShell/Windows or malware samples. |
| `deploying-active-directory-honeytokens` | deception-technology | No Active Directory. |
| `deploying-cloud-deception-with-decoy-resources` | deception-technology | No AWS/Azure/GCP resources or cloud IAM; cloud honey-resources have nothing to attach to. |
| `deploying-cloudflare-access-for-zero-trust` | zero-trust-architecture | No Cloudflare/zero-trust product; the reverse proxy is operator-managed and its config is not in the repo. |
| `deploying-decoy-files-for-ransomware-detection` | ransomware-defense | Endpoint ransomware canary deployment is host operations work, not app code. |
| `deploying-edr-agent-with-crowdstrike` | endpoint-security | No EDR tooling; deploying endpoint agents is outside the app source. |
| `deploying-honeytokens-and-canarytokens` | deception-technology | Deploying deception artifacts is an operations task; no cloud keys, kubeconfigs or email alerting in the project. |
| `deploying-osquery-for-endpoint-monitoring` | endpoint-security | Endpoint monitoring deployment, not app code; no fleet or host tooling in scope. |
| `deploying-palo-alto-prisma-access-zero-trust` | zero-trust-architecture | No SASE/VPN/zero-trust products. |
| `deploying-ransomware-canary-files` | ransomware-defense | Host-level ransomware canary monitoring, not app source review. |
| `deploying-software-defined-perimeter` | zero-trust-architecture | No SDP, SPA or mTLS infrastructure. |
| `deploying-tailscale-for-zero-trust-vpn` | zero-trust-architecture | No VPN or mesh networking in the project. |
| `designing-adversary-engagement-with-mitre-engage` | deception-technology | Planning deception operations; not app code or source-review checks. |
| `detecting-anomalies-in-industrial-control-systems` | ot-ics-security | No OT/ICS. |
| `detecting-arp-poisoning-in-network-traffic` | network-security | Network-layer ARP detection; no network devices or captures in scope. |
| `detecting-attacks-on-historian-servers` | ot-ics-security | No OT/ICS historian servers. |
| `detecting-attacks-on-scada-systems` | ot-ics-security | No SCADA/ICS. |
| `detecting-aws-cloudtrail-anomalies` | cloud-security | No AWS or CloudTrail. |
| `detecting-aws-guardduty-findings-automation` | cloud-security | No AWS/GuardDuty. |
| `detecting-aws-iam-privilege-escalation` | cloud-security | No cloud IAM. |
| `detecting-azure-lateral-movement` | cloud-security | No Azure/Entra ID. |
| `detecting-azure-service-principal-abuse` | cloud-security | No Azure/Entra ID. |
| `detecting-azure-storage-account-misconfigurations` | cloud-security | No Azure storage. |
| `detecting-beaconing-patterns-with-zeek` | security-operations | Network C2 hunting with Zeek is SOC work, not app code. |
| `detecting-bluetooth-low-energy-attacks` | wireless-security | No wireless/BLE. |
| `detecting-business-email-compromise` | phishing-defense | No email sending or mail system. |
| `detecting-business-email-compromise-with-ai` | phishing-defense | No email system. |
| `detecting-cloud-threats-with-guardduty` | cloud-security | No AWS. |
| `detecting-command-and-control-over-dns` | network-security | Network SOC detection; not app code. |
| `detecting-compromised-cloud-credentials` | cloud-security | No cloud accounts. |
| `detecting-container-drift-at-runtime` | container-security | No containers. |
| `detecting-container-escape-attempts` | container-security | No containers. |
| `detecting-container-escape-with-falco-rules` | container-security | No containers. |
| `detecting-container-runtime-threats-with-falco` | container-security | No containers/Kubernetes. |
| `detecting-credential-dumping-techniques` | threat-detection | No Windows hosts or Active Directory. |
| `detecting-cryptomining-in-cloud` | cloud-security | No cloud. |
| `detecting-data-and-model-poisoning` | ai-security | No ML training or model pipeline; the assistant only calls an external API. |
| `detecting-dcsync-attack-in-active-directory` | threat-hunting | No Active Directory. |
| `detecting-deepfake-audio-in-vishing-attacks` | social-engineering-defense | No voice/audio processing. |
| `detecting-dll-sideloading-attacks` | threat-hunting | No Windows hosts. |
| `detecting-dnp3-protocol-anomalies` | ot-ics-security | No OT/ICS. |
| `detecting-dns-exfiltration-with-dns-query-analysis` | network-security | Network SOC detection; not app code. |
| `detecting-email-account-compromise` | incident-response | No O365/Workspace email. |
| `detecting-email-forwarding-rules-attack` | threat-hunting | No email system. |
| `detecting-entra-offensive-tools-in-graph-logs` | soc-operations | No Entra ID; SOC operations. |
| `detecting-evasion-techniques-in-endpoint-logs` | endpoint-security | Endpoint telemetry analysis; no EDR/SIEM in scope. |
| `detecting-exfiltration-over-dns-with-zeek` | network-security | Network SOC detection; not app code. |
| `detecting-fileless-attacks-on-endpoints` | endpoint-security | Endpoint malware detection; not app code. |
| `detecting-fileless-malware-techniques` | malware-analysis | Malware analysis; no Windows/PowerShell. |
| `detecting-golden-ticket-attacks-in-kerberos-logs` | threat-hunting | No Kerberos/Active Directory. |
| `detecting-golden-ticket-forgery` | threat-detection | No Kerberos/Active Directory. |
| `detecting-insider-threat-with-ueba` | threat-detection | Needs an Elasticsearch/UEBA platform; SOC operations, not app code. |
| `detecting-kerberoasting-attacks` | threat-hunting | No Kerberos/Active Directory. |
| `detecting-lateral-movement-in-network` | network-security | Single-process local app; network SOC work. |
| `detecting-lateral-movement-with-splunk` | threat-hunting | No Windows or Splunk; SOC operations. |
| `detecting-lateral-movement-with-zeek` | network-security | No SMB/Kerberos network; SOC operations. |
| `detecting-living-off-the-land-attacks` | threat-detection | No Windows hosts. |
| `detecting-living-off-the-land-with-lolbas` | threat-detection | No Windows hosts. |
| `detecting-malicious-npm-packages` | supply-chain-security | No npm or JS build step; vanilla JS only. |
| `detecting-malicious-scheduled-tasks-with-sysmon` | threat-hunting | No Windows hosts. |
| `detecting-mimikatz-execution-patterns` | threat-hunting | No Windows or Active Directory. |
| `detecting-misconfigured-azure-storage` | cloud-security | No Azure. |
| `detecting-mobile-malware-behavior` | mobile-security | No mobile apps. |
| `detecting-modbus-command-injection-attacks` | ot-ics-security | No OT/ICS. |
| `detecting-modbus-protocol-anomalies` | ot-ics-security | No OT/ICS. |
| `detecting-model-extraction-attacks` | ai-security | No proprietary model is served; the assistant proxies an external API. |
| `detecting-network-anomalies-with-zeek` | network-security | Network monitoring deployment; SOC operations. |
| `detecting-network-scanning-with-ids-signatures` | network-security | IDS operations; no network devices in scope. |
| `detecting-ntlm-relay-with-event-correlation` | threat-hunting | No NTLM/Windows. |
| `detecting-oauth-token-theft` | cloud-security | No OAuth/Entra ID; session-cookie theft is covered by other web skills. |
| `detecting-pass-the-hash-attacks` | threat-hunting | No NTLM/Windows. |
| `detecting-pass-the-ticket-attacks` | threat-detection | No Kerberos. |
| `detecting-port-scanning-with-fail2ban` | network-security | Host/network ops on the operator machine; app binds 127.0.0.1 and rate limiting is in-app. |
| `detecting-privilege-escalation-attempts` | threat-hunting | OS-level privesc hunting (UAC, sudo, kernel); app-level RBAC is covered by access-control skills. |
| `detecting-privilege-escalation-in-kubernetes-pods` | container-security | No Kubernetes. |
| `detecting-process-hollowing-technique` | threat-hunting | Malware/EDR analysis; not app code. |
| `detecting-process-injection-techniques` | malware-analysis | Malware analysis; not app code. |
| `detecting-qr-code-phishing-with-email-security` | phishing-defense | No email or QR handling. |
| `detecting-ransomware-encryption-behavior` | ransomware-defense | Endpoint monitoring; not app code. |
| `detecting-ransomware-precursors-in-network` | ransomware-defense | Network SOC detection; not app code. |
| `detecting-rdp-brute-force-attacks` | threat-detection | No RDP/Windows. |
| `detecting-rootkit-activity` | malware-analysis | Host forensics; not app code. |
| `detecting-s3-data-exfiltration-attempts` | cloud-security | No S3/AWS. |
| `detecting-secure-boot-bypass` | hardware-firmware-security | No hardware or firmware scope. |
| `detecting-serverless-function-injection` | cloud-security | No serverless. |
| `detecting-service-account-abuse` | threat-hunting | Aimed at AD/cloud service accounts via EDR/SIEM; the app has no service accounts or API keys. |
| `detecting-shadow-it-cloud-usage` | cloud-security | SOC analysis of proxy, DNS and netflow logs for SaaS usage. Not application code, and no such logs exist. |
| `detecting-spearphishing-with-email-gateway` | phishing-defense | Configures an email security gateway. The app sends and receives no email. |
| `detecting-sql-injection-via-waf-logs` | security-operations | No WAF or WAF logs in scope. SQL injection itself is covered by the source-review SQLi skills. |
| `detecting-stuxnet-style-attacks` | ot-ics-security | No ICS, OT or PLC. |
| `detecting-suspicious-oauth-application-consent` | cloud-security | No Azure AD, Entra ID or OAuth. |
| `detecting-suspicious-powershell-execution` | threat-hunting | Endpoint EDR hunting on Windows hosts. Not app code. |
| `detecting-t1003-credential-dumping-with-edr` | threat-hunting | Windows LSASS/SAM credential-dumping detection with EDR. No Windows hosts or EDR. |
| `detecting-t1055-process-injection-with-sysmon` | threat-hunting | Sysmon process-injection detection. No Windows hosts or SOC tooling. |
| `detecting-t1548-abuse-elevation-control-mechanism` | threat-hunting | Host-level UAC and sudo/setuid abuse detection. SOC telemetry, not app code. |
| `detecting-wmi-persistence` | threat-hunting | Windows WMI persistence detection. No Windows hosts. |
| `emulating-cloud-attacks-with-stratus-red-team` | cloud-security | No AWS, Azure, GCP or Kubernetes. |
| `enumerating-cloud-with-cloudfox` | cloud-security | No cloud IAM (AWS/Azure). |
| `eradicating-malware-from-infected-systems` | incident-response | Incident-response malware removal on hosts. Not app code. |
| `escaping-containers-to-host` | container-security | No Docker, containers or Kubernetes. |
| `evaluating-threat-intelligence-platforms` | threat-intelligence | Threat-intelligence platform procurement. Not app code. |
| `executing-active-directory-attack-simulation` | penetration-testing | No Active Directory. |
| `executing-nist-rmf-authorization-to-operate` | compliance-governance | Federal RMF/ATO governance process. It yields no source-review checks for this app. |
| `executing-phishing-simulation-campaign` | penetration-testing | Social-engineering campaign against people. Out of scope for source review. |
| `executing-red-team-engagement-planning` | red-teaming | Engagement planning and rules of engagement. Live testing is prohibited and there are no code checks. |
| `executing-red-team-exercise` | penetration-testing | Live C2-based adversary emulation. Prohibited here and not source review. |
| `exploiting-active-directory-certificate-services-esc1` | red-teaming | No Active Directory or AD CS. |
| `exploiting-active-directory-with-bloodhound` | red-teaming | No Active Directory. |
| `exploiting-adcs-with-certipy` | red-teaming | No Active Directory or AD CS. |
| `exploiting-aws-with-pacu` | cloud-security | No AWS account or cloud IAM. |
| `exploiting-bgp-hijacking-vulnerabilities` | network-security | No routing infrastructure; BGP is out of scope. |
| `exploiting-constrained-delegation-abuse` | red-teaming | No Kerberos or Active Directory. |
| `exploiting-deeplink-vulnerabilities` | mobile-security | No mobile native apps. |
| `exploiting-insecure-data-storage-in-mobile` | mobile-security | No mobile apps. |
| `exploiting-ipv6-vulnerabilities` | network-security | Network-layer IPv6 attacks. No network devices in scope. |
| `exploiting-jwt-algorithm-confusion-attack` | api-security | No JWT. Sessions use opaque tokens. |
| `exploiting-kerberoasting-with-impacket` | red-teaming | No Kerberos or Active Directory. |
| `exploiting-ms17-010-eternalblue-vulnerability` | red-teaming | No Windows or SMB. |
| `exploiting-nopac-cve-2021-42278-42287` | red-teaming | No Active Directory. |
| `exploiting-nosql-injection-vulnerabilities` | web-application-security | No NoSQL database. It uses SQLite only. |
| `exploiting-oauth-misconfiguration` | web-application-security | No OAuth, OIDC or SSO. |
| `exploiting-smb-vulnerabilities-with-metasploit` | network-security | No SMB or Windows. |
| `exploiting-type-juggling-vulnerabilities` | web-application-security | PHP loose-comparison attacks. The app is Python. |
| `exploiting-vulnerabilities-with-metasploit-framework` | vulnerability-management | Live exploitation framework. Prohibited here and no matching tech. |
| `exploiting-websocket-vulnerabilities` | web-application-security | No WebSockets. |
| `exploiting-zerologon-vulnerability-cve-2020-1472` | red-teaming | No Active Directory or Netlogon. |
| `extracting-browser-history-artifacts` | digital-forensics | Browser forensics. Not app code. |
| `extracting-config-from-agent-tesla-rat` | malware-analysis | Malware analysis. No malware in scope. |
| `extracting-credentials-from-memory-dump` | digital-forensics | Windows memory forensics. |
| `extracting-iocs-from-malware-samples` | malware-analysis | Malware analysis. No malware in scope. |
| `extracting-memory-artifacts-with-rekall` | security-operations | Memory forensics. Not app code. |
| `extracting-windows-event-logs-artifacts` | digital-forensics | Windows EVTX forensics. No Windows hosts. |
| `find-skills` | extra | Skill discovery utility, not security methodology. |
| `fleet-hunting-with-velociraptor` | threat-hunting | Endpoint fleet DFIR tooling. Not app code. |
| `generating-forensic-timelines-with-hayabusa` | digital-forensics | Windows EVTX timelining. |
| `generating-threat-intelligence-reports` | threat-intelligence | Threat-intelligence reporting. Not app code. |
| `hardening-docker-containers-for-production` | container-security | No Docker or containers. |
| `hardening-docker-daemon-configuration` | container-security | No Docker. |
| `hardening-windows-endpoint-with-cis-benchmark` | endpoint-security | No Windows hosts. |
| `hunting-advanced-persistent-threats` | threat-intelligence | SOC threat hunting. Not app code. |
| `hunting-bootkits-in-efi-system-partition` | hardware-firmware-security | Firmware/EFI forensics. |
| `hunting-evtx-with-chainsaw` | threat-hunting | Windows EVTX hunting. |
| `hunting-for-anomalous-powershell-execution` | threat-hunting | PowerShell/Windows hunting. |
| `hunting-for-beaconing-with-frequency-analysis` | threat-hunting | Network C2 traffic hunting. SOC operations. |
| `hunting-for-cobalt-strike-beacons` | threat-hunting | C2 detection. SOC operations. |
| `hunting-for-command-and-control-beaconing` | threat-hunting | Network C2 hunting. SOC operations. |
| `hunting-for-data-exfiltration-indicators` | threat-hunting | Zeek/Suricata telemetry hunting. SOC operations, not app code. |
| `hunting-for-data-staging-before-exfiltration` | threat-hunting | EDR/Sysmon telemetry hunting. SOC operations. |
| `hunting-for-dcom-lateral-movement` | threat-hunting | Windows DCOM. No Windows hosts. |
| `hunting-for-dcsync-attacks` | threat-hunting | No Active Directory. |
| `hunting-for-defense-evasion-via-timestomping` | threat-hunting | NTFS MFT forensics. |
| `hunting-for-dns-based-persistence` | threat-hunting | DNS zone and subdomain management is not in the repo. |
| `hunting-for-dns-tunneling-with-zeek` | threat-hunting | Zeek DNS log hunting. SOC operations. |
| `hunting-for-domain-fronting-c2-traffic` | threat-hunting | Proxy log C2 hunting. SOC operations. |
| `hunting-for-lateral-movement-via-wmi` | threat-hunting | Windows WMI. No Windows hosts. |
| `hunting-for-living-off-the-cloud-techniques` | threat-hunting | No cloud or SaaS platforms. |
| `hunting-for-living-off-the-land-binaries` | threat-hunting | Endpoint LOLBin hunting. SOC operations. |
| `hunting-for-lolbins-execution-in-endpoint-logs` | threat-hunting | Endpoint log hunting. SOC operations. |
| `hunting-for-ntlm-relay-attacks` | threat-hunting | No Windows, NTLM or Active Directory. |
| `hunting-for-persistence-mechanisms-in-windows` | threat-hunting | Windows endpoint threat hunting with SIEM/EDR; the project has no Windows hosts and no SIEM, and this is not app code. |
| `hunting-for-persistence-via-wmi-subscriptions` | threat-hunting | WMI persistence exists only on Windows; the project has no Windows hosts or EDR telemetry. |
| `hunting-for-process-injection-techniques` | threat-hunting | Hunts Sysmon/EDR process injection on Windows; the project has no Windows endpoints or EDR. |
| `hunting-for-registry-persistence-mechanisms` | threat-hunting | The Windows registry is absent; the project is a Python web app. |
| `hunting-for-registry-run-key-persistence` | threat-hunting | Hunts Windows Run keys via Sysmon logs; the project has no Windows hosts. |
| `hunting-for-scheduled-task-persistence` | threat-hunting | Hunts Windows scheduled tasks with SIEM/EDR queries; the project has no Windows hosts or SIEM. |
| `hunting-for-shadow-copy-deletion` | threat-hunting | Volume Shadow Copy is a Windows feature and this is SOC hunting; neither applies to the app code. |
| `hunting-for-spearphishing-indicators` | threat-hunting | The app sends no email; this is SOC hunting over email logs. |
| `hunting-for-startup-folder-persistence` | threat-hunting | Windows startup folders are absent; the project has no Windows hosts. |
| `hunting-for-unusual-network-connections` | threat-hunting | C2 hunting over SIEM/EDR network telemetry is SOC operations, not app code. |
| `hunting-for-unusual-service-installations` | threat-hunting | Windows service installation (Event ID 7045) does not exist here; the project has no Windows hosts. |
| `hunting-for-webshell-activity` | threat-hunting | The app has no upload endpoints, writes no files into the web root, and this skill hunts on live servers. |
| `hunting-saas-sso-token-abuse` | soc-operations | Entra ID SigninLogs and OAuth/PRT tokens are absent; the project has no SSO or OAuth. |
| `implementing-alert-fatigue-reduction` | soc-operations | SOC alert tuning is operations work, not app code. |
| `implementing-anti-phishing-training-program` | phishing-defense | This is an organizational awareness program, not something checkable in software. |
| `implementing-anti-ransomware-group-policy` | ransomware-defense | Windows Group Policy and AppLocker are absent; the project has no Windows hosts. |
| `implementing-api-threat-protection-with-apigee` | api-security | The project has no Apigee, OAuth or XML. |
| `implementing-application-whitelisting-with-applocker` | endpoint-security | Windows AppLocker is absent. |
| `implementing-aqua-security-for-container-scanning` | devsecops | The project has no containers or images. |
| `implementing-attack-path-analysis-with-xm-cyber` | vulnerability-management | This is a commercial exposure-management platform for enterprise networks, not app code. |
| `implementing-attack-surface-management` | offensive-security | External scanning with Shodan/nuclei is prohibited here, and no production deployment is reachable. |
| `implementing-aws-config-rules-for-compliance` | cloud-security | The project has no AWS. |
| `implementing-aws-iam-permission-boundaries` | identity-access-management | The project has no cloud IAM. |
| `implementing-aws-macie-for-data-classification` | cloud-security | The project has no AWS or S3. |
| `implementing-aws-nitro-enclave-security` | cloud-security | The project has no AWS. |
| `implementing-aws-security-hub` | cloud-security | The project has no AWS. |
| `implementing-aws-security-hub-compliance` | cloud-security | The project has no AWS. |
| `implementing-azure-ad-privileged-identity-management` | identity-access-management | The project has no Entra ID or Azure. |
| `implementing-azure-defender-for-cloud` | cloud-security | The project has no Azure. |
| `implementing-beyondcorp-zero-trust-access-model` | zero-trust-architecture | The project has no GCP IAP or zero-trust product. |
| `implementing-bgp-security-with-rpki` | network-security | The project has no network routing infrastructure. |
| `implementing-browser-isolation-for-zero-trust` | network-security | The project has no browser-isolation or zero-trust product. |
| `implementing-canary-tokens-for-network-intrusion` | security-operations | This is network deception infrastructure, not app code. |
| `implementing-cisa-zero-trust-maturity-model` | zero-trust-architecture | This is an organizational ZT maturity assessment; the project has no zero-trust infrastructure. |
| `implementing-cloud-dlp-for-data-protection` | cloud-security | The project has no cloud DLP services. |
| `implementing-cloud-security-posture-management` | cloud-security | The project has no cloud provider. |
| `implementing-cloud-trail-log-analysis` | cloud-security | The project has no AWS CloudTrail. |
| `implementing-cloud-vulnerability-posture-management` | vulnerability-management | The project has no cloud provider. |
| `implementing-cloud-waf-rules` | cloud-security | The project has no cloud WAF; the proxy is operator-managed and outside the repo. |
| `implementing-cloud-workload-protection` | cloud-security | The project has no cloud workloads. |
| `implementing-conditional-access-policies-azure-ad` | identity-access-management | The project has no Entra ID. |
| `implementing-conduit-security-for-ot-remote-access` | ot-ics-security | The project has no OT/ICS. |
| `implementing-container-image-minimal-base-with-distroless` | container-security | The project has no containers. |
| `implementing-container-network-policies-with-calico` | container-security | The project has no Kubernetes. |
| `implementing-continuous-security-validation-with-bas` | vulnerability-management | BAS platforms are enterprise SOC tooling, and live testing is prohibited. |
| `implementing-data-loss-prevention-with-microsoft-purview` | data-protection | The project has no Microsoft 365 or Purview. |
| `implementing-ddos-mitigation-with-cloudflare` | network-security | The project has no Cloudflare; the proxy is operator-managed and outside the repo. |
| `implementing-deception-based-detection-with-canarytoken` | deception-technology | This deploys Thinkst Canary through an external API; it is deception ops, not app code. |
| `implementing-delinea-secret-server-for-pam` | identity-access-management | The project has no Delinea or PAM product. |
| `implementing-device-posture-assessment-in-zero-trust` | zero-trust-architecture | The project has no EDR or zero-trust device posture integration. |
| `implementing-diamond-model-analysis` | threat-intelligence | This is a threat-intel intrusion analysis framework, not app code. |
| `implementing-digital-signatures-with-ed25519` | cryptography | The project uses no digital signatures; backups rely on AEAD, not signing. |
| `implementing-disk-encryption-with-bitlocker` | endpoint-security | BitLocker is Windows-only; the project has no Windows hosts. |
| `implementing-dmarc-dkim-spf-email-security` | phishing-defense | The app sends no email. |
| `implementing-dragos-platform-for-ot-monitoring` | ot-ics-security | The project has no OT/ICS. |
| `implementing-ebpf-security-monitoring` | security-operations | Host/kernel runtime monitoring is operations work, not app code. |
| `implementing-email-sandboxing-with-proofpoint` | phishing-defense | The project has no email handling. |
| `implementing-end-to-end-encryption-for-messaging` | cryptography | The project has no E2E messaging; notifications are server-side and SMS is a mock. |
| `implementing-endpoint-detection-with-wazuh` | security-operations | SIEM/XDR deployment is SOC tooling, not app code. |
| `implementing-endpoint-dlp-controls` | endpoint-security | Endpoint DLP agents are absent; this is not app code. |
| `implementing-envelope-encryption-with-aws-kms` | cryptography | The project has no AWS KMS; backups use a local key file. |
| `implementing-epss-score-for-vulnerability-prioritization` | vulnerability-management | This is vulnerability-program prioritization through an external API, which is prohibited here. |
| `implementing-file-integrity-monitoring-with-aide` | endpoint-security | Host FIM is operator infrastructure outside the repo. |
| `implementing-fuzz-testing-in-cicd-with-aflplusplus` | devsecops | AFL++ targets C/C++ binaries; the project is pure Python with no native code. |
| `implementing-gcp-binary-authorization` | cloud-security | The project has no GCP or containers. |
| `implementing-gcp-organization-policy-constraints` | cloud-security | The project has no GCP. |
| `implementing-gcp-vpc-firewall-rules` | cloud-security | The project has no GCP. |
| `implementing-google-workspace-admin-security` | identity-access-management | The project has no Google Workspace. |
| `implementing-google-workspace-phishing-protection` | phishing-defense | The project has no Google Workspace. |
| `implementing-google-workspace-sso-configuration` | identity-access-management | The project has no SAML/SSO. |
| `implementing-hardware-security-key-authentication` | identity-and-access-management | The project has no FIDO2/WebAuthn; authentication is password-only. |
| `implementing-hashicorp-vault-dynamic-secrets` | identity-access-management | The project has no Vault, database server or cloud credentials; it uses a local SQLite file. |
| `implementing-honeypot-for-ransomware-detection` | ransomware-defense | Ransomware deception infrastructure is not app code. |
| `implementing-honeytokens-for-breach-detection` | security-operations | Deploying honeytokens is detection operations; there is no existing app code to review. |
| `implementing-ics-firewall-with-tofino` | ot-ics-security | The project has no OT/ICS. |
| `implementing-identity-governance-with-sailpoint` | identity-access-management | The project has no SailPoint or enterprise IGA; roles are managed inside the app. |
| `implementing-iec-62443-security-zones` | ot-ics-security | No OT/ICS or industrial networks. |
| `implementing-image-provenance-verification-with-cosign` | container-security | No container images, Kubernetes or registry. |
| `implementing-infrastructure-as-code-security-scanning` | devsecops | No Terraform, CloudFormation, Kubernetes manifests or Helm. |
| `implementing-jwt-signing-and-verification` | cryptography | No JWT; sessions use opaque server-side cookies. |
| `implementing-kubernetes-network-policy-with-calico` | container-security | No Kubernetes. |
| `implementing-kubernetes-pod-security-standards` | container-security | No Kubernetes. |
| `implementing-log-forwarding-with-fluentd` | security-operations | No log aggregation pipeline; SOC infrastructure, not app code. |
| `implementing-memory-protection-with-dep-aslr` | endpoint-security | Pure Python app; no native code or endpoint hardening in scope. |
| `implementing-microsegmentation-with-guardicore` | zero-trust-architecture | No data-center network segmentation product. |
| `implementing-mimecast-targeted-attack-protection` | phishing-defense | No email sending or email gateway. |
| `implementing-mitre-attack-coverage-mapping` | soc-operations | SOC detection coverage work, not app code. |
| `implementing-mobile-application-management` | mobile-security | No mobile apps. |
| `implementing-mtls-for-zero-trust-services` | security-operations | No service-to-service mTLS; single process behind a reverse proxy. |
| `implementing-nerc-cip-compliance-controls` | ot-ics-security | No bulk electric systems or OT. |
| `implementing-network-access-control` | network-security | No 802.1X/RADIUS or network devices. |
| `implementing-network-access-control-with-cisco-ise` | network-security | No Cisco ISE, network devices or Active Directory. |
| `implementing-network-deception-with-honeypots` | deception-technology | Network deception infrastructure, not app code. |
| `implementing-network-intrusion-prevention-with-suricata` | network-security | No IDS/IPS or network infrastructure in repo. |
| `implementing-network-policies-for-kubernetes` | container-security | No Kubernetes. |
| `implementing-network-segmentation-for-ot` | ot-ics-security | No OT/ICS. |
| `implementing-network-segmentation-with-firewall-zones` | network-security | No firewall or network config in repo; app binds to 127.0.0.1. |
| `implementing-network-traffic-analysis-with-arkime` | network-security | No packet capture infrastructure; SOC tooling. |
| `implementing-network-traffic-baselining` | network-security | No NetFlow data; SOC analytics, not app code. |
| `implementing-next-generation-firewall-with-palo-alto` | network-security | No network devices. |
| `implementing-opa-gatekeeper-for-policy-enforcement` | container-security | No Kubernetes. |
| `implementing-ot-incident-response-playbook` | ot-ics-security | No OT/ICS. |
| `implementing-ot-network-traffic-analysis-with-nozomi` | ot-ics-security | No OT/ICS. |
| `implementing-passwordless-auth-with-microsoft-entra` | identity-access-management | No Microsoft Entra or SSO. |
| `implementing-passwordless-authentication-with-fido2` | identity-access-management | No WebAuthn/FIDO2; username+password only. |
| `implementing-patch-management-for-ot-systems` | ot-ics-security | No OT/ICS. |
| `implementing-pci-dss-compliance-controls` | compliance-governance | No payments or cardholder data. |
| `implementing-pod-security-admission-controller` | container-security | No Kubernetes. |
| `implementing-policy-as-code-with-open-policy-agent` | devsecops | No OPA, Kubernetes or deployment pipeline to gate. |
| `implementing-privileged-access-management-with-cyberark` | identity-access-management | No enterprise PAM or CyberArk. |
| `implementing-privileged-access-workstation` | identity-and-access-management | Endpoint/admin workstation hardening, not app code. |
| `implementing-proofpoint-email-security-gateway` | phishing-defense | No email infrastructure. |
| `implementing-purdue-model-network-segmentation` | ot-ics-security | No OT/ICS. |
| `implementing-ransomware-kill-switch-detection` | ransomware-defense | Malware analysis; no malware in scope. |
| `implementing-rapid7-insightvm-for-scanning` | vulnerability-management | Enterprise scanner deployment; external scanning prohibited. |
| `implementing-rbac-hardening-for-kubernetes` | container-security | No Kubernetes. |
| `implementing-runtime-security-with-tetragon` | container-security | No Kubernetes or eBPF runtime. |
| `implementing-saml-sso-with-okta` | identity-access-management | No SAML/SSO. |
| `implementing-scim-provisioning-with-okta` | identity-access-management | No SCIM or IdP provisioning. |
| `implementing-security-chaos-engineering` | security-operations | SOC control validation with boto3/EDR; no cloud or SOC. |
| `implementing-security-information-sharing-with-stix2` | threat-intelligence | Threat intel sharing, not app code. |
| `implementing-security-monitoring-with-datadog` | security-operations | No Datadog or SIEM. |
| `implementing-siem-correlation-rules-for-apt` | security-operations | Windows event SIEM correlation; SOC operations. |
| `implementing-siem-use-case-tuning` | security-operations | SOC SIEM tuning, not app code. |
| `implementing-siem-use-cases-for-detection` | soc-operations | SOC SIEM operations, not app code. |
| `implementing-sigstore-for-software-signing` | supply-chain-security | No released artifacts or container images to sign. |
| `implementing-soar-automation-with-phantom` | soc-operations | SOAR/SOC operations. |
| `implementing-soar-playbook-for-phishing` | security-operations | SOAR/SOC operations. |
| `implementing-soar-playbook-with-palo-alto-xsoar` | soc-operations | SOAR/SOC operations. |
| `implementing-stix-taxii-feed-integration` | threat-intelligence | Threat intel feeds, not app code. |
| `implementing-supply-chain-security-with-in-toto` | container-security | No container build pipeline or admission control. |
| `implementing-syslog-centralization-with-rsyslog` | security-operations | No syslog infrastructure in repo. |
| `implementing-taxii-server-with-opentaxii` | threat-intelligence | Threat intel server, not app code. |
| `implementing-threat-intelligence-lifecycle-management` | threat-intelligence | CTI program management, not app code. |
| `implementing-threat-modeling-with-mitre-attack` | soc-operations | SOC detection-coverage threat modeling, not app code. |
| `implementing-ticketing-system-for-incidents` | soc-operations | SOC ticketing operations. |
| `implementing-usb-device-control-policy` | endpoint-security | Endpoint device control, not app code. |
| `implementing-velociraptor-for-ir-collection` | incident-response | Endpoint DFIR tooling. |
| `implementing-vulnerability-management-with-greenbone` | vulnerability-management | Network vuln scanner; external scanning prohibited. |
| `implementing-vulnerability-remediation-sla` | vulnerability-management | Organizational vuln-management program, not app code. |
| `implementing-vulnerability-sla-breach-alerting` | vulnerability-management | Vuln SLA tracking system, not app code. |
| `implementing-web-application-logging-with-modsecurity` | web-application-security | No WAF; reverse proxy config is not in repo. |
| `implementing-zero-knowledge-proof-for-authentication` | cryptography | No ZKP auth; Argon2id password auth only. |
| `implementing-zero-standing-privilege-with-cyberark` | identity-access-management | No cloud IAM or CyberArk. |
| `implementing-zero-trust-dns-with-nextdns` | zero-trust-architecture | DNS resolver infrastructure, not app code. |
| `implementing-zero-trust-for-saas-applications` | zero-trust-architecture | No SaaS/CASB/OAuth governance. |
| `implementing-zero-trust-in-cloud` | cloud-security | No cloud provider. |
| `implementing-zero-trust-network-access` | cloud-security | No cloud or ZTNA product. |
| `implementing-zero-trust-network-access-with-zscaler` | zero-trust-architecture | No Zscaler or VPN/ZTNA product. |
| `implementing-zero-trust-with-beyondcorp` | zero-trust | No GCP or IAP. |
| `implementing-zero-trust-with-hashicorp-boundary` | zero-trust-architecture | No HashiCorp Boundary or infrastructure access proxy. |
| `intercepting-mobile-traffic-with-burpsuite` | mobile-security | No mobile or native apps; live traffic interception is not allowed. The web API is covered by other web/API skills. |
| `investigating-insider-threat-indicators` | soc-operations | SOC/SIEM/DLP/HR-data investigation work, not application code review. |
| `investigating-phishing-email-incident` | soc-operations | No email sending or receiving. This is SOC incident response work. |
| `investigating-ransomware-attack-artifacts` | digital-forensics | Forensics of a compromised host, not application source review. |
| `managed-pentesting-with-strix` | extra | Requires cloud upload to app.strix.ai; uploads and external services prohibited. |
| `managing-cloud-identity-with-okta` | cloud-security | No Okta, SSO, OAuth/OIDC/SAML or cloud IdP. Login is local username and password. |
| `managing-intelligence-lifecycle` | threat-intelligence | Running a CTI programme, not app code. |
| `managing-third-party-vendor-risk` | compliance-governance | Organisational TPRM questionnaire programme. Dependency risk is covered by supply-chain skills. |
| `mapping-attack-paths-with-bloodhound-ce` | red-teaming | No Active Directory or Entra ID. |
| `mapping-mitre-attack-techniques` | threat-intelligence | Maps detection coverage for SOC/CTI, not app code checks. |
| `modeling-threats-with-opencti` | threat-intelligence | Running a CTI platform (OpenCTI in Docker), not app code. |
| `monitoring-darkweb-sources` | threat-intelligence | External threat-intel monitoring. External services are not allowed. |
| `monitoring-scada-modbus-traffic-anomalies` | ot-security | No OT/ICS/Modbus. |
| `moving-laterally-with-netexec` | penetration-testing | No SMB/WinRM/LDAP or Windows hosts. |
| `operating-havoc-c2` | red-teaming | Red-team C2 infrastructure, not app review. |
| `operating-sliver-c2` | red-teaming | Red-team C2 infrastructure, not app review. |
| `operationalizing-misp-threat-feeds` | threat-intelligence | Running a MISP threat-intel platform, not app code. |
| `parsing-artifacts-with-eric-zimmerman-tools` | digital-forensics | Windows forensic artifacts. No Windows hosts in scope. |
| `performing-access-recertification-with-saviynt` | identity-access-management | Saviynt IGA platform. No enterprise identity platform. |
| `performing-active-directory-bloodhound-analysis` | red-teaming | No Active Directory. |
| `performing-active-directory-compromise-investigation` | incident-response | No Active Directory or Kerberos. |
| `performing-active-directory-forest-trust-attack` | red-team | No Active Directory. |
| `performing-active-directory-penetration-test` | penetration-testing | No Active Directory. |
| `performing-active-directory-vulnerability-assessment` | vulnerability-management | No Active Directory. |
| `performing-adversary-in-the-middle-phishing-detection` | phishing-defense | Detection from Azure AD/IdP sign-in logs. No IdP, MFA or SOC tooling. |
| `performing-agentless-vulnerability-scanning` | vulnerability-management | Network/cloud infrastructure scanning. Live scanning is prohibited. |
| `performing-ai-driven-osint-correlation` | threat-intelligence | OSINT on people and domains, not app code. |
| `performing-alert-triage-with-elastic-siem` | soc-operations | SOC/SIEM operations, not app code. |
| `performing-android-app-static-analysis-with-mobsf` | mobile-security | No Android or mobile app. |
| `performing-arp-spoofing-attack-simulation` | network-security | Network-layer attack. No network devices in scope. |
| `performing-asset-criticality-scoring-for-vulns` | vulnerability-management | Enterprise vulnerability-management programme, not app code. |
| `performing-authenticated-scan-with-openvas` | vulnerability-management | Credentialed host scanning of infrastructure is prohibited and out of scope. |
| `performing-authenticated-vulnerability-scan` | vulnerability-management | Credentialed host scanning (SSH/SMB/WinRM). No infrastructure in scope. |
| `performing-automated-malware-analysis-with-cape` | malware-analysis | Malware sandbox. No malware in scope. |
| `performing-aws-account-enumeration-with-scout-suite` | cloud-security | No AWS or cloud. |
| `performing-aws-privilege-escalation-assessment` | cloud-security | No AWS or cloud IAM. |
| `performing-bandwidth-throttling-attack-simulation` | network-security | Network degradation lab testing. No network infrastructure in scope. |
| `performing-binary-exploitation-analysis` | offensive-security | No compiled ELF targets. The app is pure Python. |
| `performing-bluetooth-security-assessment` | wireless-security | No Bluetooth or wireless. |
| `performing-brand-monitoring-for-impersonation` | threat-intelligence | External brand/threat-intel monitoring, not app code. |
| `performing-cloud-asset-inventory-with-cartography` | cloud-security | No cloud. |
| `performing-cloud-forensics-investigation` | digital-forensics | No cloud. |
| `performing-cloud-forensics-with-aws-cloudtrail` | cloud-security | No AWS. |
| `performing-cloud-incident-containment-procedures` | incident-response | No cloud. |
| `performing-cloud-log-forensics-with-athena` | cloud-security | No AWS. |
| `performing-cloud-native-forensics-with-falco` | cloud-security | No containers or Kubernetes. |
| `performing-cloud-native-threat-hunting-with-aws-detective` | cloud-security | No AWS. |
| `performing-cloud-penetration-testing-with-pacu` | cloud-security | No AWS. |
| `performing-cloud-storage-forensic-acquisition` | digital-forensics | No cloud storage services. |
| `performing-container-escape-detection` | container-security | No containers or Kubernetes. |
| `performing-container-image-hardening` | devsecops | No Docker or container images. |
| `performing-credential-access-with-lazagne` | red-teaming | Endpoint post-exploitation credential dumping, not app review. |
| `performing-dark-web-monitoring-for-threats` | threat-intelligence | External threat-intel monitoring, not app code. |
| `performing-deception-technology-deployment` | soc-operations | SOC deception infrastructure, not app code. |
| `performing-disk-forensics-investigation` | incident-response | Host disk forensics, not app review. |
| `performing-dmarc-policy-enforcement-rollout` | phishing-defense | No email sending. |
| `performing-dns-enumeration-and-zone-transfer` | network-security | DNS reconnaissance. No DNS infrastructure in scope and live probing is prohibited. |
| `performing-dns-tunneling-detection` | security-operations | Network traffic monitoring, not app code. |
| `performing-docker-bench-security-assessment` | container-security | No Docker. |
| `performing-dynamic-analysis-of-android-app` | mobile-security | No Android app. |
| `performing-dynamic-analysis-with-any-run` | malware-analysis | Malware sandbox, and an external service. |
| `performing-endpoint-forensics-investigation` | endpoint-security | Endpoint forensics, not app review. |
| `performing-endpoint-vulnerability-remediation` | endpoint-security | Fleet endpoint patching, not app code. |
| `performing-entitlement-review-with-sailpoint-iiq` | identity-access-management | SailPoint IGA platform. No enterprise identity platform. |
| `performing-external-network-penetration-test` | penetration-testing | External infrastructure pentest. Live/production testing is prohibited. |
| `performing-false-positive-reduction-in-siem` | soc-operations | SIEM tuning, not app code. |
| `performing-file-carving-with-foremost` | digital-forensics | Disk image forensic carving, not app review. |
| `performing-firmware-extraction-with-binwalk` | firmware-analysis | No firmware. |
| `performing-firmware-malware-analysis` | malware-analysis | No firmware or malware. |
| `performing-fuzzing-with-aflplusplus` | application-security | AFL++ targets compiled binaries. The app is pure Python with no native code of its own. |
| `performing-gcp-penetration-testing-with-gcpbucketbrute` | cloud-security | No GCP. |
| `performing-gcp-security-assessment-with-forseti` | cloud-security | No GCP. |
| `performing-graphql-depth-limit-attack` | api-security | No GraphQL. |
| `performing-graphql-introspection-attack` | api-security | No GraphQL. |
| `performing-graphql-security-assessment` | web-application-security | No GraphQL. |
| `performing-hardware-security-module-integration` | cryptography | No HSM or PKCS#11. Backup keys are file-based. |
| `performing-ics-asset-discovery-with-claroty` | ot-ics-security | No OT/ICS. |
| `performing-indicator-lifecycle-management` | threat-intelligence | CTI/SOC indicator management with SIEM/IDS watchlists; not application code. |
| `performing-initial-access-with-evilginx3` | red-teaming | Red-team phishing infrastructure; no MFA/SSO; live phishing is prohibited by audit constraints. |
| `performing-ioc-enrichment-automation` | soc-operations | SOC IOC enrichment via external threat-intel APIs; not app code. |
| `performing-ios-app-security-assessment` | mobile-security | No iOS or mobile native app. |
| `performing-iot-security-assessment` | penetration-testing | No IoT devices, hardware or firmware. |
| `performing-ip-reputation-analysis-with-shodan` | threat-intelligence | External Shodan threat intel; no external scanning allowed. |
| `performing-jwt-none-algorithm-attack` | api-security | No JWT; sessions use opaque server-side cookies. |
| `performing-kerberoasting-attack` | red-teaming | No Active Directory or Kerberos. |
| `performing-kubernetes-cis-benchmark-with-kube-bench` | container-security | No Kubernetes. |
| `performing-kubernetes-etcd-security-assessment` | container-security | No Kubernetes or etcd. |
| `performing-kubernetes-penetration-testing` | container-security | No Kubernetes. |
| `performing-lateral-movement-detection` | soc-operations | Windows event correlation in a SOC; no Windows hosts or SIEM. |
| `performing-lateral-movement-with-wmiexec` | red-teaming | No Windows networks or WMI. |
| `performing-linux-log-forensics-investigation` | digital-forensics | Host log forensics (auth.log, journald, auditd) on an incident host; no host is in scope. |
| `performing-log-source-onboarding-in-siem` | soc-operations | SIEM onboarding is SOC operations, not app code. |
| `performing-malware-hash-enrichment-with-virustotal` | threat-intelligence | No malware analysis; external services prohibited. |
| `performing-malware-ioc-extraction` | threat-intelligence | No malware samples. |
| `performing-malware-persistence-investigation` | digital-forensics | Host malware persistence forensics; not app code. |
| `performing-malware-triage-with-yara` | malware-analysis | No malware triage in scope. |
| `performing-memory-forensics-with-volatility3` | digital-forensics | RAM dump forensics; no memory images in scope. |
| `performing-memory-forensics-with-volatility3-plugins` | malware-analysis | RAM dump forensics; no memory images in scope. |
| `performing-mobile-app-certificate-pinning-bypass` | mobile-security | No mobile apps. |
| `performing-mobile-device-forensics-with-cellebrite` | digital-forensics | No mobile devices. |
| `performing-network-forensics-with-wireshark` | digital-forensics | PCAP forensics; no captures and no network probing allowed. |
| `performing-network-packet-capture-analysis` | digital-forensics | PCAP forensics; not app code. |
| `performing-network-traffic-analysis-with-tshark` | network-security | Network monitoring tooling; not app code. |
| `performing-network-traffic-analysis-with-zeek` | network-security | Network monitoring sensor deployment; not app code. |
| `performing-nist-csf-maturity-assessment` | compliance-governance | Organization-wide governance maturity assessment, not source-level review. |
| `performing-oauth-scope-minimization-review` | identity-access-management | No OAuth, OIDC or third-party app integrations. |
| `performing-oil-gas-cybersecurity-assessment` | ot-ics-security | No OT/ICS. |
| `performing-open-source-intelligence-gathering` | red-teaming | External OSINT recon; live and external activity prohibited. |
| `performing-osint-with-spiderfoot` | threat-intelligence | External OSINT scanning prohibited; not app code. |
| `performing-ot-network-security-assessment` | ot-ics-security | No OT/ICS networks. |
| `performing-ot-vulnerability-assessment-with-claroty` | ot-ics-security | No OT/ICS. |
| `performing-ot-vulnerability-scanning-safely` | ot-ics-security | No OT/ICS. |
| `performing-packet-injection-attack` | network-security | Network packet crafting against firewalls/IDS; no network devices in scope. |
| `performing-paste-site-monitoring-for-credentials` | threat-intelligence | External paste-site monitoring; not app code. |
| `performing-phishing-simulation-with-gophish` | phishing-defense | Phishing awareness campaigns; no email sending in app. |
| `performing-physical-intrusion-assessment` | red-teaming | Physical security testing; not app code. |
| `performing-plc-firmware-security-analysis` | ot-ics-security | No PLC or firmware. |
| `performing-power-grid-cybersecurity-assessment` | ot-ics-security | No OT/ICS or grid systems. |
| `performing-purple-team-atomic-testing` | purple-team | Endpoint ATT&CK emulation for detection validation; no EDR/SIEM. |
| `performing-purple-team-exercise` | soc-operations | SOC red/blue coordination; not app code. |
| `performing-ransomware-response` | incident-response | Incident response operations; backup encryption is covered by crypto and backup skills. |
| `performing-ransomware-tabletop-exercise` | ransomware-defense | Organizational tabletop exercise, not app code. |
| `performing-red-team-phishing-with-gophish` | security-operations | Phishing campaigns; not app code. |
| `performing-red-team-with-covenant` | red-team | C2 framework operations; not app code. |
| `performing-s7comm-protocol-security-analysis` | ot-ics-security | No Siemens PLC or OT protocols. |
| `performing-scada-hmi-security-assessment` | ot-ics-security | No SCADA/HMI. |
| `performing-serverless-function-security-review` | cloud-security | No serverless or cloud. |
| `performing-soap-web-service-security-testing` | api-security | No SOAP or XML parsing. |
| `performing-soc-tabletop-exercise` | soc-operations | SOC operations exercise. |
| `performing-soc2-type2-audit-preparation` | governance-risk-compliance | Organizational compliance evidence from cloud providers; not app source review. |
| `performing-ssl-certificate-lifecycle-management` | cryptography | TLS certificates are managed at an operator reverse proxy that is not in the repo. |
| `performing-ssl-tls-inspection-configuration` | network-security | Firewall/proxy TLS inspection; no network devices. |
| `performing-static-malware-analysis-with-pe-studio` | malware-analysis | No Windows PE samples. |
| `performing-steganography-detection` | digital-forensics | No media file handling. |
| `performing-subdomain-enumeration-with-subfinder` | web-application-security | External recon prohibited; no domain in scope. |
| `performing-thick-client-application-penetration-test` | penetration-testing | No desktop thick client; the SPA runs in a browser. |
| `performing-threat-emulation-with-atomic-red-team` | threat-intelligence | Endpoint ATT&CK emulation; not app code. |
| `performing-threat-hunting-with-elastic-siem` | soc-operations | SIEM threat hunting; no SIEM. |
| `performing-threat-hunting-with-yara-rules` | threat-hunting | Malware hunting; not app code. |
| `performing-threat-intelligence-sharing-with-misp` | threat-intelligence | CTI sharing platform; not app code. |
| `performing-threat-landscape-assessment-for-sector` | threat-intelligence | Sector CTI reporting, not source review. |
| `performing-timeline-reconstruction-with-plaso` | digital-forensics | Forensic super-timelines of host images; not app code. |
| `performing-user-behavior-analytics` | soc-operations | UEBA is SOC/SIEM operations. |
| `performing-vlan-hopping-attack` | network-security | No network switches or VLANs. |
| `performing-vulnerability-scanning-with-nessus` | penetration-testing | Network infrastructure scanner; live scanning prohibited. |
| `performing-web-application-firewall-bypass` | web-application-security | No WAF in the repo or scope. |
| `performing-wifi-password-cracking-with-aircrack` | network-security | No wireless infrastructure in scope; project is a localhost web app. |
| `performing-windows-artifact-analysis-with-eric-zimmerman-tools` | digital-forensics | Windows host forensics; no Windows hosts or incident artifacts in scope. |
| `performing-wireless-network-penetration-test` | penetration-testing | No wireless networks in scope. |
| `performing-wireless-security-assessment-with-kismet` | network-security | No wireless networks in scope. |
| `performing-yara-rule-development-for-detection` | malware-analysis | Malware detection rule authoring; no malware samples, not app code. |
| `post-exploiting-microsoft-graph-with-graphrunner` | identity-access-management | No Microsoft 365/Entra ID or OAuth tenant. |
| `processing-stix-taxii-feeds` | threat-intelligence | Threat-intel feed processing; no CTI platform in project. |
| `profiling-threat-actor-groups` | threat-intelligence | CTI actor profiling, not source review of app code. |
| `recovering-deleted-files-with-photorec` | digital-forensics | Disk forensics file carving; no disk images in scope. |
| `recovering-from-ransomware-attack` | ransomware-defense | Organizational IR recovery operations, not app code; backup features covered by other skills. |
| `relaying-ntlm-for-adcs-esc8` | red-teaming | No Active Directory, NTLM, or AD CS. |
| `remediating-s3-bucket-misconfiguration` | cloud-security | No AWS/S3; backups written to local filesystem. |
| `reverse-engineering-android-malware-with-jadx` | malware-analysis | No Android apps or malware. |
| `reverse-engineering-dotnet-malware-with-dnspy` | malware-analysis | No .NET code or malware. |
| `reverse-engineering-ios-app-with-frida` | mobile-security | No mobile native apps. |
| `reverse-engineering-malware-with-ghidra` | malware-analysis | Binary malware RE; project is Python source. |
| `reverse-engineering-ransomware-encryption-routine` | malware-analysis | Ransomware binary analysis; no malware in scope. |
| `reverse-engineering-rust-malware` | malware-analysis | No Rust binaries or malware. |
| `scanning-docker-images-with-trivy` | container-security | No Docker images. |
| `scanning-infrastructure-with-nessus` | vulnerability-management | Network vulnerability scanning of infrastructure; live scanning prohibited and none in scope. |
| `scanning-kubernetes-manifests-with-kubesec` | container-security | No Kubernetes. |
| `scanning-network-with-nmap-advanced` | network-security | Network recon; live probing prohibited, single localhost service. |
| `securing-api-gateway-with-aws-waf` | cloud-security | No AWS API Gateway or WAF. |
| `securing-aws-iam-permissions` | cloud-security | No cloud IAM. |
| `securing-aws-lambda-execution-roles` | cloud-security | No serverless/AWS. |
| `securing-azure-with-microsoft-defender` | cloud-security | No Azure/cloud. |
| `securing-container-registry-images` | cloud-security | No container registry. |
| `securing-container-registry-with-harbor` | container-security | No container registry. |
| `securing-helm-chart-deployments` | container-security | No Helm/Kubernetes. |
| `securing-historian-server-in-ot-environment` | ot-ics-security | No OT/ICS. |
| `securing-kubernetes-on-cloud` | cloud-security | No Kubernetes or cloud. |
| `securing-remote-access-to-ot-environment` | ot-ics-security | No OT/ICS. |
| `securing-serverless-functions` | cloud-security | No serverless. |
| `testing-android-intents-for-vulnerabilities` | mobile-security | No Android apps. |
| `testing-for-email-header-injection` | web-application-security | No email sending functionality. |
| `testing-for-json-web-token-vulnerabilities` | web-application-security | No JWT; opaque session cookies. |
| `testing-for-xml-injection-vulnerabilities` | web-application-security | No XML parsing. |
| `testing-for-xxe-injection-vulnerabilities` | web-application-security | No XML parsing. |
| `testing-jwt-token-security` | web-application-security | No JWT. |
| `testing-oauth2-implementation-flaws` | api-security | No OAuth/OIDC. |
| `testing-websocket-api-security` | api-security | No WebSockets. |
| `tracking-threat-actor-infrastructure` | threat-intelligence | CTI infrastructure tracking, not app code. |
| `triaging-security-alerts-in-splunk` | soc-operations | SOC/SIEM operations, no Splunk. |
| `triaging-security-incident` | incident-response | IR triage process, not app code review. |
| `triaging-security-incident-with-ir-playbook` | incident-response | SOC/IR playbook operations with SIEM, not app code. |
| `triaging-windows-with-kape` | digital-forensics | No Windows hosts. |
| `validating-tpm-measured-boot-attestation` | hardware-firmware-security | Hardware/firmware attestation; none in scope. |
