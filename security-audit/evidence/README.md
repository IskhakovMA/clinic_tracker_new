# Evidence README

This directory intentionally contains only summarized, non-sensitive evidence.
Raw logs, databases, screenshots with PHI, scanner reports containing secrets,
temporary SQLite files, and browser profiles must not be committed.

## Commands Executed

```text
git status --short
git branch --show-current
git fetch origin --prune
git switch main
git pull --ff-only origin main
git rev-parse HEAD
git rev-parse origin/main
git log -5 --oneline
```

Result: clean `main`, local and remote at
`6019436b967ac26b1b106f445c4f83921ea6cb18`.

```text
git -C ~/Documents/security-references/Anthropic-Cybersecurity-Skills checkout --detach e612f4944c55d306bb75565022442d7da8cf2b9a
git -C ~/Documents/security-references/Anthropic-Cybersecurity-Skills rev-parse HEAD
```

Result: `e612f4944c55d306bb75565022442d7da8cf2b9a`.

```text
.venv/bin/python -m pytest backend/tests -q
```

Result: `249 passed, 1 warning in 44.91s`. Warning:
`StarletteDeprecationWarning` from FastAPI TestClient dependency.

```text
.venv/bin/pip-audit -r backend/requirements.txt
```

Result: `No known vulnerabilities found`.

```text
.venv/bin/python -m backend.cli check-db
```

Result: integrity `ok`, foreign key violations `0`, schema version `3`.

```text
which node || true
node --version || true
which nodejs || true
nodejs --version || true
```

Result: system `node` and `nodejs` not found.

JavaScript syntax fallback:

```javascript
var fsSecAudit = await import('node:fs');
var vmSecAudit = await import('node:vm');
var secAuditPath = 'static/app.js';
new vmSecAudit.Script(fsSecAudit.readFileSync(secAuditPath, 'utf8'), { filename: secAuditPath });
```

Result: syntax check passed.

OpenAPI dynamic check:

```text
GET /api/docs -> 404
GET /redoc -> 404
GET /openapi.json -> 200
```

GitHub read-only checks:

```text
gh api repos/applehooked-cmd/clinic_tracker_new/actions/workflows
```

Result: `{"total_count":0,"workflows":[]}`.

```text
gh api repos/applehooked-cmd/clinic_tracker_new/branches/main/protection
```

Result: HTTP 404.

## Scanner Availability

`bandit`, `semgrep`, `gitleaks`, and `detect-secrets` were not available in
PATH. No global installation was performed.

## Browser Evidence

Temporary environment:

- Separate SQLite directory: `mktemp -d /tmp/clinic-security-audit-browser.XXXXXX`.
- Synthetic user: `auditdev`.
- `NOTIFICATION_PROVIDER=mock`.
- Local server: `http://127.0.0.1:8765`.
- Temporary database directory removed after the smoke run.

Playwright package was available, but bundled Chromium was not installed. The
smoke used installed system Google Chrome:

```text
executablePath: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

Result:

- 375 px: title loaded, product name present, horizontal scroll `false`.
- 768 px: title loaded, product name present, horizontal scroll `false`.
- 1024 px: title loaded, product name present, horizontal scroll `false`.
- 1440 px: title loaded, product name present, horizontal scroll `false`.
- Synthetic login succeeded and authenticated UI loaded.
- Browser console errors: `0`.
- Page errors: `0`.
- Unexpected 4xx/5xx during smoke: `0` after excluding expected pre-login
  `/api/auth/me` unauthenticated state.
