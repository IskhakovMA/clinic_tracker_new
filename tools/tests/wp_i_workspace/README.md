# I-6B synthetic workspace browser gate

Use a fresh temporary FastAPI/SQLite server through:

```
python tools/tests/wp_i_workspace/run.py --suite browser --count 1000 --out /absolute/evidence/browser
python tools/tests/wp_i_workspace/run.py --suite workflows --count 1000 --out /absolute/evidence/workflows
python tools/tests/wp_i_workspace/run.py --suite zoom --count 1000 --out /absolute/evidence/zoom
```

Browser: six real roles (chair menus remain hidden), five visible-role workspaces in both themes at1440/375/320px; full-document axe A/AA, no node exclusions; real server rows <=50, zero legacy full-list and medical mutation requests. `I6B_BASELINE=1` is only a screenshot capture on the accepted I6A commit, not a corrected-code acceptance gate.

Workflows: server search beyond first page; ordinary second page separately from actual stale-cursor409 after event mutation; bounded repeated409/410; actual read-audit503 versus labeled transport faults; held old200 and detached old handlers; completed current checklists; read-only previous repeated-stage marks and optional items; mouse/keyboard clinical cancel/confirm; exact event/history ID from object GET; actual clinical commit with held acknowledgment; screen switch; real401/reauth; object-scope transfer before object GET; shared-cookie identity switch; ACCESS_CONTEXT_CHANGED is not a harmless retry. Progress is persisted even if a later assertion fails. No sleeps, retries or weakened historical assertions.

Zoom: native Chrome extension tab zoom in a disposable profile, not CSS transform or viewport-only simulation. Both lists and themes at100/200/400%, full-document axe and overflow checks. Requires `WP_H_FULL_CHROMIUM` and `AXE_SOURCE`, as existing I6A gates do.

API contract: events includes only dated planned events without fact, including historical stages; `head` is displayed as a department head, not invented treating doctor. Checklist workspace includes current/latest required progress, including completed entries. Optional items and historical stored marks are shown only after the scoped patient object read. Historical category labels use the current reference; no historical reference version is invented. No new filters, aggregates, hidden all-page downloads, week grid, backend contracts or medical writes on view/navigation.
