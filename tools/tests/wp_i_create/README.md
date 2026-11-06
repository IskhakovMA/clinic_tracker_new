# WP-I I-4 real creation page

Run with the locked Python dev environment and existing test-only Playwright/axe.
No application dependency changes. Only synthetic temporary FastAPI/SQLite.

```sh
python tools/tests/wp_i_create/run.py --out /tmp/clinic-i4-create
python tools/tests/wp_i_create/run.py --suite workflows --out /tmp/clinic-i4-workflows
python tools/tests/wp_i_create/run.py --suite zoom --out /tmp/clinic-i4-zoom
```

Set PLAYWRIGHT_MODULE, BROWSER_EXECUTABLE, AXE_SOURCE; native zoom also uses
WP_H_FULL_CHROMIUM in a disposable profile.
Creation uses real server-generated IDs, existing bounded idempotent transport
retry (same key/body), then explicit same-key recovery if both responses are lost.
No new policy, browser medical storage or external service is added.
Transport403/429/503 are explicitly injected. 401, validation422, duplicate409,
creation, quality, duplicate registry and portal actions use the real backend.
