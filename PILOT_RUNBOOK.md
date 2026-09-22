# Pilot Runbook

This runbook is for a limited controlled pilot of "Система планирования". It is not a production operations manual and does not replace organisational security, privacy, legal, or incident-response procedures.

## Start

1. Confirm the deployment host and storage are approved for the pilot data classification.
2. Confirm `CLINIC_DEBUG=false`.
3. Confirm `CLINIC_DATA_DIR` points to the intended application data directory.
4. Confirm HTTPS termination is active before setting `CLINIC_HTTPS=true`.
5. Start the FastAPI app by the approved local service manager or command:

```bash
source .venv/bin/activate
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8420
```

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

## Stop

1. Stop user access at the reverse proxy or maintenance window.
2. Stop the FastAPI process.
3. Stop any notification worker process.
4. Confirm no process is writing to the SQLite DB before restore or filesystem maintenance.

## Health Check

```bash
source .venv/bin/activate
python -m backend.cli check-db
```

Expected:

- `integrity_check: ok`
- `foreign_key_check: 0`
- schema version `6`
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
python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key
```

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

The restore command creates a recovery copy of the previous DB when possible.

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
