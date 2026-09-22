# Application Security Hardening

Branch: `fix/application-security-hardening`

Base: `2f7a29a84963e81e81064178e69c3fd45564bcb3`

Schema version: `3`

This stage addresses application-level findings from the security audit. It
does not include backup/database encryption, GitHub settings enforcement, real
SMS, scheduler, email, push notifications, AI, or new user-facing features.

## Finding Scope

| Finding ID | Title | Severity | Affected code | Recommended remediation | In this PR | Reason |
|---|---|---:|---|---|---|---|
| SEC-2026-001 | OpenAPI schema remains exposed when debug docs are disabled | Medium | `backend/app/main.py` | Disable `openapi_url` outside debug or gate it by developer auth | Yes | Application metadata exposure; fixed by disabling `/openapi.json` when `CLINIC_DEBUG=false` |
| SEC-2026-002 | Logout endpoint is state-changing but does not require CSRF | Low | `backend/app/routes/auth.py`, `backend/app/deps.py` | Require CSRF on logout | Yes | Confirmed application CSRF finding; logout now requires active session and matching session CSRF |
| SEC-2026-003 | Rate limiting is login-focused and per-process only | Medium | `backend/app/security.py`, selected heavy/search routes | Add persistent or deployment-level throttling for login and heavy endpoints | Partial | This PR adds bounded per-process controls without schema migration; persistent/proxy limiter remains operational work |
| SEC-2026-004 | Repository lacks visible CI workflows and branch protection baseline | High | `.github/*`, repository settings | Enable protected main and required checks | No | GitHub enforcement/settings are explicitly outside this application hardening PR |
| SEC-2026-005 | Request schemas generally ignore unknown JSON fields | Low | `backend/app/models.py`, `backend/app/analytics/schemas.py` | Use strict Pydantic request schemas with `extra="forbid"` | Yes | Application input validation hardening; known v3 import/export compatibility fields remain explicit |
| SEC-2026-006 | PHI at rest relies on filesystem and disk controls, not application encryption | Medium | SQLite DB and backups | Disk encryption or encrypted backup packaging/key handling | No | Backup/database encryption is explicitly outside this PR |
| SEC-2026-007 | GitHub Advanced Security and vulnerability-alert settings were not verifiable | Informational | GitHub repository settings | Owner verifies Dependabot alerts, dependency graph, secret scanning, push protection, code scanning | No | GitHub settings are explicitly outside this application hardening PR |

## Write Endpoint Inventory

Actual FastAPI route inventory contains 35 `POST`/`PUT`/`PATCH`/`DELETE`
endpoints. Explicit CSRF is present on 32. The three N/A endpoints are:

| Endpoint | CSRF status | Reason |
|---|---|---|
| `POST /api/auth/login` | N/A | Session creation uses credentials and does not rely on an existing cookie-authenticated session |
| `POST /api/import/preview` | N/A | Authenticated validation preview only; no SQLite write, session mutation, file creation, notification creation, or import commit |
| `POST /api/migrate/localstorage/preview` | N/A | Authenticated validation preview only; no SQLite write, session mutation, file creation, notification creation, or migration commit |

All other write endpoints require `require_csrf`. The regression test
`test_write_endpoint_csrf_inventory_is_explicit` fails if a new write endpoint
is added without CSRF or an explicit N/A entry.

## Abuse Controls

The following application-level per-session controls return `429` with
`Retry-After` when exceeded:

| Area | Endpoints | Limit | Window | Key |
|---|---|---:|---:|---|
| Login | `POST /api/auth/login` | 5 failed attempts before backoff | 15 minutes | Peer host + SHA-256 normalized username; peer host aggregate |
| Import | `POST /api/import/preview`, `POST /api/import/commit` | 20 preview, 10 commit | 60 seconds | Authenticated user ID + peer host |
| Legacy migration | `POST /api/migrate/localstorage/preview`, `POST /api/migrate/localstorage/commit` | 20 preview, 10 commit | 60 seconds | Authenticated user ID + peer host |
| Data quality | `GET /api/data-quality/summary`, `GET /api/data-quality/issues`, `GET /api/data-quality/patients/{patient_id}` | 60 summary/issues, 120 patient detail | 60 seconds | Authenticated user ID + peer host |
| Notifications worker | `POST /api/notifications/generate-due`, `POST /api/notifications/process-due` | 30 | 60 seconds | Authenticated user ID + peer host |
| Analytics export | `POST /api/analytics/export` | 20 | 60 seconds | Authenticated user ID + peer host |
| Staff search | `GET /api/users`, `GET /api/users/doctors` | 60 | 60 seconds | Authenticated user ID + peer host |

These controls are deliberately per-process and do not replace a persistent
or reverse-proxy-backed limiter for multi-process production deployments.
Buckets are pruned on access, capped by `LOGIN_RATE_LIMIT_MAX_BUCKETS` and
`REQUEST_RATE_LIMIT_MAX_BUCKETS`, protected by an in-process lock, and keyed
without PHI. Login username subjects are SHA-256 hashes of normalized usernames.
The application reads the client address from `request.client.host`. uvicorn
rewrites that from `X-Forwarded-For` when the direct peer is in
`--forwarded-allow-ips` (default `127.0.0.1`), so behind the documented
localhost reverse proxy the proxy must overwrite `X-Forwarded-For` with the
real peer (see PILOT_RUNBOOK.md, "Reverse proxy requirements"). Login attempts
are reserved under the limiter lock before password verification, so a
concurrent burst cannot pass the limit.

## Strict Request Schemas

External production request schemas with `extra="forbid"`: 27/27.

Response schemas and internal DTOs remain regular `BaseModel` classes because
they are generated by the server and are not client mass-assignment surfaces.
Data quality has no JSON request body. Backup/restore has no HTTP endpoint; it
is CLI-only.

Legacy/v3 import compatibility fields are explicit and bounded:

| Schema | Field | Reason |
|---|---|---|
| `EventIn` | `setAt` | v3 export roundtrip; ignored for authoritative event timestamp writes |
| `ImportPatientIn` | `events` | legacy localStorage/create-payload root event list |
| `ImportPatientIn` | `treatingDoctorName` | v3 export display field; ignored for assignment decisions |
| `ImportDocument` | `generatedAt` | v3 export metadata roundtrip |

No request schema uses `extra="allow"`.

## Production Configuration Gap Matrix

| Control | Status | Notes |
|---|---|---|
| Invalid timezone fail-fast | PARTIAL | Analytics falls back to UTC when `APP_TIMEZONE` is invalid; this PR documents the gap but does not change startup behavior |
| Unknown notification provider fail-closed | IMPLEMENTED | Worker paths call `ensure_known_provider()` and fail closed unless provider is `mock` |
| Production Secure cookie validation | PARTIAL | Cookies use `Secure` when `CLINIC_HTTPS=true`; startup does not prove the reverse proxy is actually HTTPS |
| Allowed hosts | IMPLEMENTED | `TrustedHostMiddleware` allows configured host, `127.0.0.1`, and `localhost` |
| Trusted proxy allowlist | NOT IMPLEMENTED | Proxy IP headers are ignored; no trusted proxy parsing exists |
| HSTS only for HTTPS | IMPLEMENTED | HSTS is sent only when `CLINIC_HTTPS=true` |
| Debug false default | IMPLEMENTED | `CLINIC_DEBUG` defaults to false |
| Generic production 500 responses | IMPLEMENTED | Unhandled exceptions return a generic JSON 500 body while logging server-side |
