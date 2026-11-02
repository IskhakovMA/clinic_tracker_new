# WP-H synthetic browser and performance evidence

Run from the repository root using the hash-locked Python dev environment.
No production database, browser profile, MFA trust bypass or real notification
provider is used. `server.py` always creates a temporary data directory,
bootstraps it and performs the required offline verification. Public synthetic
passwords in this fixture are not operational secrets.

Install the separate exact test dependencies:

```sh
npm ci --ignore-scripts --prefix tools/tests/wp_h
```

Set `PLAYWRIGHT_MODULE` to the absolute `tools/tests/wp_h/node_modules/playwright`
path (or let Node use that local installation), and `BROWSER_EXECUTABLE` to the
installed headless Chromium executable if required by the environment. For native
zoom, `WP_H_FULL_CHROMIUM` must identify a full Chrome for Testing executable,
not headless-shell. Download/install browsers explicitly as appropriate for the
machine; no installed browser binaries or virtual environments belong in Git.

```sh
python tools/tests/wp_h/run.py browser --out /tmp/clinic-wph-browser
python tools/tests/wp_h/run.py drafts --out /tmp/clinic-wph-drafts
python tools/tests/wp_h/run.py zoom --out /tmp/clinic-wph-zoom
python tools/tests/wp_h/run.py benchmark --out /tmp/clinic-wph-metrics
```

`browser` verifies exact pages, full-scope search, a deliberately held stale
response, a real dataset revision conflict, selected-card preservation, a signed
full-filter export, scoped doctor/head totals, keyboard focus, mobile inert menu
and axe WCAG A/AA on list/data/overview in both themes. CSS transitions finish
before contrast measurements; no contrast rules or nodes are excluded.

`drafts` verifies real 401 reconciliation plus stale-page 409, separate original
version drafts, no automatic write and a manual version conflict. `zoom` uses
Chrome's tabs.setZoom/getZoom in a disposable profile for 100/200/400 percent.
`benchmark` samples 100/1000/10000 cards for three roles, includes release audit
and SQL tracing, and checks up to three additional pages for bounded DOM/detail
cache, unique IDs and exact response/manifest hashes. It is not a production SLA,
full traversal of 10000 rows, heap peak measurement or native Linux benchmark.

The existing WP-D/E/F/G browser suites remain mandatory separate regressions.
Backend tests are in `backend/tests/test_wp_h_pages.py`; full-suite collection and
exact-once outcome verification use `tools/ci/pytest_shards.py` and
`tools.ci.pytest_outcomes`, unchanged from WP-A.
