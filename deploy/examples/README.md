# Inactive deployment examples

These `.in` files are templates, not installed units or an approved deployment.
Every `@...@` placeholder needs owner review. No scheduler is enabled, no RPO,
RTO, backup-retention duration, external storage or operator is selected.

The backend uses `python -m backend.server`, loopback binding and application-owned
proxy trust. Configure the real proxy separately: overwrite X-Forwarded-For with
one verified peer address, overwrite X-Forwarded-Proto, preserve the public Host,
X-Clinic-User and X-Clinic-Access, restrict direct backend access, omit query
strings from access logs. TLS/certificates/proxy setup are outside this task.

`readiness` is a diagnostic preflight, not an admission command. An operator runs
bootstrap/verify offline manually according to OPERATIONS.md. A service restart,
cron invocation or reboot cannot issue permission. RestartPreventExitStatus is
only a supplementary supervisor setting; the durable application gate is required
and also protects entrypoints outside systemd.

Choose ONE scheduler. Fill/approve paths, account, protected environment, backup
cadence, monitoring thresholds, storage and responsibilities before installation.
Backup templates intentionally omit `--retain-days` and `--replica-directory`.
Cleanup removes only already-expired sessions/idempotency keys. Audit pruning is
not scheduled here and keeps its existing explicit retention/protected events.

A second writable directory on the same server/account is NOT an off-host or
immutable backup. Independent storage/account and verified recovery remain owner
operations. No SMTP, email API, SMS gateway or provider integration is included.
