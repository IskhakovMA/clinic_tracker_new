# WP-I registry regression (real synthetic FastAPI)

Use the hash-pinned Python dev environment. The runner starts the existing WP-H
server on a temporary localhost port; it always creates and verifies a new
synthetic database. It never uses a working medical database or browser profile.
No witness/S1 bypass or notification provider is enabled.

Use the existing test-only Playwright/axe dependencies from `tools/tests/wp_h`
(`npm ci --ignore-scripts --prefix tools/tests/wp_h`). Set `PLAYWRIGHT_MODULE`,
`BROWSER_EXECUTABLE`, and `AXE_SOURCE` if the packages/browser are elsewhere.
Outputs, screenshots, and server logs must be outside the worktree:

```sh
python tools/tests/wp_i/run.py --count 100 --out /tmp/clinic-wpi-100
python tools/tests/wp_i/run.py --count 1000 --out /tmp/clinic-wpi-1000
python tools/tests/wp_i/run.py --count 10000 --out /tmp/clinic-wpi-10000
```

Each run checks six server roles, both themes, desktop/mobile, server search
outside page one, supported sort controls, whole-scope summaries versus filtered
totals, explicit filter chips, mobile sheet/menu, detail loading, bounded rows,
no legacy full-list API and no medical writes. Axe A/AA runs without rule/node
exclusions. A held real response verifies loading and stale-response handling;
the synthetic server's release race verifies bounded 409 recovery. Injected
410/503 responses test UI transport failures; they are not evidence of server
audit failure behavior (covered separately by backend tests). An actual API
created XSS name must render as text with no node or execution.

These tests complement, rather than replace, all WP-D–WP-H suites and native
Chrome zoom. Backend registry integration cases: `backend/tests/test_wp_i_registry.py`.
No automatic retry, skip or xfail is used to turn a failed assertion into a pass.
