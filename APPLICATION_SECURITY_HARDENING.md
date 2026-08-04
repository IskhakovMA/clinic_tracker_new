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
| `POST /api/import/preview` | N/A | Authenticated validation preview only; no database commit |
| `POST /api/migrate/localstorage/preview` | N/A | Authenticated validation preview only; no database commit |

All other write endpoints require `require_csrf`. The regression test
`test_write_endpoint_csrf_inventory_is_explicit` fails if a new write endpoint
is added without CSRF or an explicit N/A entry.

## Abuse Controls

The following application-level per-session controls return `429` with
`Retry-After` when exceeded:

| Area | Endpoints |
|---|---|
| Import | `POST /api/import/preview`, `POST /api/import/commit` |
| Legacy migration | `POST /api/migrate/localstorage/preview`, `POST /api/migrate/localstorage/commit` |
| Data quality | `GET /api/data-quality/summary`, `GET /api/data-quality/issues`, `GET /api/data-quality/patients/{patient_id}` |
| Notifications worker | `POST /api/notifications/generate-due`, `POST /api/notifications/process-due` |
| Analytics export | `POST /api/analytics/export` |
| Staff search | `GET /api/users`, `GET /api/users/doctors` |

These controls are deliberately per-process and do not replace a persistent
or reverse-proxy-backed limiter for multi-process production deployments.
