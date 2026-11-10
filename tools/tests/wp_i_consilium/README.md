# WP-I I-5A — real schedule and booking regressions

Use a locked Python dev environment and the existing test-only Playwright/axe.
Only localhost FastAPI and disposable synthetic SQLite; no application dependencies.

```sh
python tools/tests/wp_i_consilium/run.py --suite schedule --out /tmp/i5a-schedule
python tools/tests/wp_i_consilium/run.py --suite workflows --out /tmp/i5a-workflows
python tools/tests/wp_i_consilium/run.py --suite zoom --out /tmp/i5a-zoom
```

Set PLAYWRIGHT_MODULE, BROWSER_EXECUTABLE, AXE_SOURCE, WP_H_FULL_CHROMIUM.
The fixture defaults to 1000 seeded patients to test scoped candidates beyond
page one without defeating patient-creation rate limits. It creates only one
additional synthetic patient, plus staff accounts and explicit saved slots.
Future day 2090-11-12 is test data, never an operational schedule.

The workflow uses real concurrent HTTP POST requests with an explicit barrier,
real version conflicts, transfer/cancel, real session/role revocation and reauth.
PATIENT_QUERY_CHANGED and schedule503 are labeled transport injections.
Axe runs against document, with no node exclusions. Native zoom uses a throwaway
Chromium profile and genuine tab zoom, rather than CSS zoom or device emulation.
No clinical decisions are signed by the fixture; review/archive/registry controls
remain in the application and the original backend regressions.

FIX1 loading regressions: `--suite loading` checks delayed genuine200 responses,
blocked old grid actions, hidden dependent aggregates, injected503 and recovery,
separate active form input/focus, real401/role revocation and shared-cookie identity
switch. It uses explicit response barriers, never sleep or transport retries.

## I-5B — decisions and revisions

`run.py --suite decisions --out <outside-worktree>` runs real synthetic FastAPI/Chromium transitions, six-role/theme/viewport axe checks, concurrent review, version conflicts, return/correction/resubmit, immutable signed projection, shared-cookie identity change and explicitly labeled transport faults. No clinical writes are retried automatically.

`run.py --suite decisionzoom --out <outside-worktree>` uses a throwaway Chromium extension/profile for actual tab zoom 100/200/400% on the medical decision dialog (not CSS zoom).

Both require the same PLAYWRIGHT_MODULE, AXE_SOURCE, BROWSER_EXECUTABLE and WP_H_FULL_CHROMIUM variables as the existing suites. No real database, account or browser profile is used.

## I-5C — history, registries and analytics

`run.py --suite records --out <outside-worktree>` verifies six-role UI/API access, exact downloaded XLSX bytes and linked manifests, history/archive versions, favorites, surgical completion, superseded reads and session expiry on a real synthetic FastAPI. `server.py` adds a test-only metadata probe to the existing temporary server; it is never imported by production.

`run.py --suite recordszoom --out <outside-worktree>` verifies actual native Chromium tab zoom through 400% on the signed-history view. Keep outputs distinct from the I-3 and I-5A/B zoom directories.
