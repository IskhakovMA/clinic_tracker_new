# Pilot Runbook

This runbook is for a limited controlled pilot of "Система планирования". It is not a production operations manual and does not replace organisational security, privacy, legal, or incident-response procedures.

## Start

1. Confirm the deployment host and storage are approved for the pilot data classification.
2. Confirm `CLINIC_DEBUG=false`.
3. Confirm `CLINIC_DATA_DIR` points to the intended application data directory.
4. Confirm HTTPS termination is active before setting `CLINIC_HTTPS=true`.
   Settings come only from the process environment (systemd `EnvironmentFile=`,
   `uvicorn --env-file`, or `set -a; . backend/.env; set +a`); the app does not
   read `.env` itself and refuses to start if `backend/.env` holds values the
   process did not receive. After start, check the log line
   `clinic-tracker backend starting: … https=… debug=… env=… data_dir=…`.
5. Run `python -m backend.cli check-db` (read-only). If it reports an older
   schema, follow OPERATIONS.md «Database Operations: Safe Order»
   (backup → backup-verify → `migrate-db --confirm-offline`); the app does not
   upgrade the schema by itself and refuses to start on an older one.
6. Start the FastAPI app by the approved local service manager or command:

```bash
source .venv/bin/activate
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8420
```

Run exactly one process. The request and login limiters keep their counters in
process memory, so the backend refuses to start with `WEB_CONCURRENCY` or
`UVICORN_WORKERS` above 1 and when another process already serves the same data
directory (`CLINIC_DATA_DIR/.clinic-app.lock`). A shared limiter is required
before running several workers.

For production-like pilot deployment, run behind a reverse proxy and do not expose the app directly without approved controls.

### Reverse proxy requirements

The login limiter keys on the client address that uvicorn reports. uvicorn
trusts `X-Forwarded-For` from `127.0.0.1` by default, so the proxy decides
what that address is:

- The proxy must **overwrite** `X-Forwarded-For` with the real peer address
  and must not pass a client-supplied value through. nginx:
  `proxy_set_header X-Forwarded-For $remote_addr;` (not `$http_x_forwarded_for`).
  Caddy: keep `trusted_proxies` empty so client headers are replaced.
- If the proxy sends no `X-Forwarded-For`, every user shares one login
  limiter bucket (`127.0.0.1`) and five failed logins from anyone slow down
  all logins. Always send it.
- Start uvicorn with an explicit trust list:
  `--proxy-headers --forwarded-allow-ips=127.0.0.1`.
- The proxy access log must not record query strings. The application no
  longer sends patient-name search text in URLs (it uses the `X-Search`
  request header, which default access-log formats do not record), but keep
  query strings out of the log anyway. nginx: log `$uri`, not `$request` or
  `$request_uri`. The application also strips query strings from the uvicorn
  access log.
- The proxy must forward `X-Forwarded-Proto` and the public `Host`, and
  `CLINIC_HOST` must be set to the public hostname; otherwise the CSRF
  origin check rejects every state-changing request.
- Limit request bodies at the proxy as well, for example nginx
  `client_max_body_size 16m;` (the application enforces
  `CLINIC_MAX_REQUEST_BODY_BYTES`, default 16 MiB).
- Keep uvicorn bound to `127.0.0.1` so it cannot be reached around the proxy.
- Rate-limit `/api/auth/login` at the proxy (per-IP and a global cap). The
  application keeps a per-attempt audit trail by owner decision, so the proxy
  is the control that bounds unauthenticated attempts and the audit rows they
  produce. nginx: `limit_req_zone $binary_remote_addr zone=login:10m rate=10r/m;`
  plus `limit_req zone=login burst=5 nodelay;` on that location.

### Notification queue limits

The reminder queue is shared by the whole clinic. Admission is capped per
patient and per treating doctor, and the worker claims fairly between doctors
(owner decision, Audit 3; see `NOTIFICATIONS.md`).

```bash
export NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT=50    # default
export NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR=1000   # default
```

Set the per-doctor cap above the normal number of planned events across one
doctor's patients within the scheduling horizon, and below what the worker can
deliver. Watch the `skipped` counter returned by generate-due and the depth of
`status = 'pending'`: rising `skipped` under normal load means the cap is too
low; rising queue depth means it is not.

### Scheduled maintenance

```bash
python -m backend.cli audit-prune --days <retention>   # per retention policy
```

Schedule it with the service account, and alert on free space for the volume
that holds `CLINIC_DATA_DIR`. A full volume stops clinical writes and silently
drops later audit events.

## Stop

1. Stop user access at the reverse proxy or maintenance window.
2. Stop the FastAPI process.
3. Stop any notification worker process.
4. Confirm no process is writing to the SQLite DB before restore or filesystem maintenance.
   `restore` and `migrate-db` check this themselves (inter-process locks) and refuse while the
   app, a CLI command or the worker runs; tools outside the app (sqlite3 shell, file sync,
   editors) are not covered — close them.

## Health Check

```bash
source .venv/bin/activate
python -m backend.cli check-db
```

Expected:

- `integrity_check: ok`
- `foreign_key_check: 0`
- schema version equal to the expected value printed next to it
- `Результат: OK`

`check-db` never changes the database (no migration, no audit event, nothing is created
for a missing file). With the app stopped it checks a private copy; with the app running it
reads the live file read-only.
- result `OK`

Application health smoke:

- Login.
- Patient list opens.
- Analytics opens.
- Notification Center opens.
- Data Quality opens.
- If AI is enabled, `/api/ai/status` is visible to authenticated users and a test chat is performed with synthetic data.

## Create Developer

Use the existing first-developer provisioning command:

```bash
source .venv/bin/activate
python -m backend.cli create-developer
```

The command is interactive/no-argument in the current implementation. Do not use default `admin/admin` for pilot or production.

## Create Users

Use the developer/admin UI:

1. Login as a developer.
2. Open "Сотрудники".
3. Create the user.
4. Assign one of the existing roles:
   - `developer`
   - `deputy_chief_oncology`
   - `caop_head`
   - `department_head`
   - `doctor`
5. Assign department membership or department-head responsibility as required.

## Reset Password

Current supported path: developer/admin UI and backend API.

1. Login as developer.
2. Open "Сотрудники".
3. Use reset password for the selected user.
4. Communicate the temporary password through the approved out-of-band procedure.
5. Require the user to change the temporary password at next login when applicable.

Operational gap: there is no current non-interactive CLI `set-password` or `reset-password` command for an existing user.

## Disable User

1. Login as developer.
2. Open "Сотрудники".
3. Disable/archive the user according to the UI.
4. Terminate active sessions if needed.
5. Confirm the user cannot login.

## check-db

```bash
source .venv/bin/activate
python -m backend.cli check-db
```

Run:

- Before pilot start.
- After restore.
- After deployment changes.
- During incident triage.

## Backup

Generate a key file outside Git, outside the data directory, and outside backup storage:

```bash
source .venv/bin/activate
python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key --key-id clinic-pilot-2026
```

The `key_id` is stored in the key file and used by every backup; do not set
`CLINIC_BACKUP_KEY_ID` to a different value.

Configure only at runtime:

```bash
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
```

Create backup:

```bash
python -m backend.cli backup
```

Never commit backup keys, `.env`, DB files, WAL/SHM files, or backup files.

## Backup Verify

```bash
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
python -m backend.cli backup-inspect backend/data/backups/<backup>.cbk
python -m backend.cli backup-verify backend/data/backups/<backup>.cbk
```

`backup-inspect` reads unauthenticated header metadata. Trust the backup only after `backup-verify` succeeds.

## Restore

Restore requires application downtime.

1. Stop FastAPI.
2. Stop notification worker.
3. Confirm no process writes to SQLite.
4. Verify the backup.
5. Restore:

```bash
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
python -m backend.cli restore backend/data/backups/<backup>.cbk --confirm-offline
python -m backend.cli check-db
```

Restore refuses while the app, the worker or a CLI command runs, before any change. It creates
and verifies an encrypted recovery copy of a healthy current DB (`backups/pre_restore_*.cbk`).
A damaged current DB is replaced only with `--replace-damaged`; its files are moved to
`DATA_DIR/quarantine/`, never deleted. Without a verified copy of the only working DB, do not
run `restore`, `migrate-db` or manual file operations on `clinic.db`/WAL/SHM (OPERATIONS.md).

## AI Start/Stop

Mode A, AI disabled:

```bash
export CLINIC_AI_ENABLED=false
```

Mode B, internal AI enabled:

```bash
export CLINIC_AI_ENABLED=true
export CLINIC_AI_BASE_URL=https://internal-ai-host:8443/v1
export CLINIC_AI_MODEL=qwen3-8b
```

The assistant sends patient names and comments, and the API key when set, to
this address. Outside loopback (`127.0.0.1`, `localhost`) only `https://` is
accepted; with `http://` the assistant refuses to start. If the inference host
has no TLS, either run it on the application host (loopback) or put it behind a
TLS terminator or tunnel. `CLINIC_AI_ALLOW_INSECURE_TRANSPORT=true` overrides
this and must be used only on a channel that is isolated by other means.

Optional:

```bash
export CLINIC_AI_API_KEY=<backend-only secret>
export CLINIC_AI_TIMEOUT_SECONDS=45
export CLINIC_AI_RATE_LIMIT_REQUESTS=30
export CLINIC_AI_RATE_LIMIT_WINDOW_SECONDS=300
```

Do not expose the AI inference endpoint to user browsers. Do not use public cloud AI for real patient data unless separately approved.

## AI Unavailable

Expected behavior:

- Core patient workflows continue.
- Analytics continues.
- Notification Center continues.
- Attention Workflow continues.
- AI chat returns a controlled error.

Operator action:

1. Confirm whether pilot is in Mode A or Mode B.
2. If Mode B, check internal inference server.
3. If unresolved, disable AI and continue pilot without AI.
4. Do not block core patient workflow on AI availability.

## Common Errors

### Login Fails

- Confirm username.
- Confirm user is active.
- Check whether password reset is required.
- Check rate limiting if repeated failures occurred.

### CSRF Error

- Refresh the page and login again.
- Confirm requests are same-origin through the approved reverse proxy.

### Backup Key Error

- Confirm `CLINIC_BACKUP_KEY_FILE` path.
- Confirm file permissions are owner-only.
- Confirm the key matches the backup `key_id`.
- If key is lost, encrypted backups cannot be recovered.

### AI Chat 503

- AI disabled or provider unavailable.
- Continue without AI or restart approved inference server.

### Import Error

- Use import preview first.
- Check schema version.
- Check size and structure.
- Do not import real data in synthetic pilot.

## Logs

Log access should be restricted to operators.

Logs must not contain:

- passwords
- session tokens
- CSRF tokens
- backup key material
- AI API key
- patient comments
- AI conversation content
- policy numbers
- notification bodies
- patient names, including search text in request URLs
- request query strings

Set a retention period for application and proxy logs and restrict their
readers to the same operators who may read the database.

Technical patient IDs may appear in audit/security logs according to current policy.

## Rollback

If the pilot stops:

1. Stop the application.
2. Preserve encrypted backup according to approved policy.
3. Revoke/disable pilot users.
4. Preserve or delete pilot dataset according to approved data policy.
5. Retain audit logs according to policy.
6. Restore prior workflow outside the application.
7. Document the reason for rollback and final data disposition.

## Emergency Contacts Placeholder

Fill before pilot:

- Technical owner:
- Security/privacy contact:
- Infrastructure contact:
- Clinical operations contact:
- Incident coordinator:

Do not put personal contact details in Git unless approved.

## Pre-Pilot Server Checks

Run through this list once before the pilot starts; each item is outside the
repository and cannot be verified from source.

1. Proxy overwrites `X-Forwarded-For`; uvicorn started with
   `--proxy-headers --forwarded-allow-ips=127.0.0.1`.
2. Proxy rate-limits `/api/auth/login` (per-IP and global).
3. `CLINIC_HTTPS=true`, TLS terminated at the proxy, `CLINIC_HOST` set to the
   public hostname, security headers preserved end to end.
4. `CLINIC_DEBUG=false` (no `/api/docs`, no `/openapi.json`).
5. Proxy access log records no query strings; `client_max_body_size 16m`.
6. AI assistant: disabled, or `CLINIC_AI_BASE_URL` is `https://` or loopback;
   `CLINIC_AI_MAX_CONCURRENT_REQUESTS` sized for the host.
7. `CLINIC_DATA_DIR` and the temporary directory used by `backup-verify`
   (the application data directory) are on the same encrypted volume.
8. Backup key stored outside the repository and outside `CLINIC_DATA_DIR`,
   mode `0600`; restores tested from a copy.
9. `audit-prune` scheduled; free-space alerting configured for the data volume.
10. Notification queue limits reviewed against the clinic's real volume, and
    queue depth monitored.

