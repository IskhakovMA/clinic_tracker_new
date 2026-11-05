# WP-I I-3: full patient card

Use the locked Python dev environment and the existing test-only Playwright/axe
packages from tools/tests/wp_h. Set PLAYWRIGHT_MODULE, BROWSER_EXECUTABLE and
AXE_SOURCE if installed elsewhere. No new dependencies are required.

```sh
python tools/tests/wp_i_card/run.py --out /tmp/clinic-i3-card
```

The runner creates a new synthetic FastAPI/SQLite instance. All fixtures are
created through the real API; a chair inherits a card created before the role
change and never receives new creation/export permission. Output must remain
outside the worktree. Never target a real medical installation.

Checks cover all eight tabs, six real roles, both themes and two viewports,
192 axe A/AA states without exclusions, keyboard/detail navigation, no automatic
medical writes on viewing or tab navigation, existing original-version drafts,
true version409 and event-note navigation protection. A31-occurrence card checks
repeated stages and long fields. The previous WP-D–WP-I browser suites, native
Chrome zoom, and all backend/security tests remain separate mandatory gates.

Field PATCH drafts reuse sessionFieldDrafts. Clinical event/note inputs remain
in the current form until explicit save/discard; they are not a new persistent
clinical draft store. Server versions, audit, role/scope and S1 are unchanged.

Additional suites:

```sh
python tools/tests/wp_i_card/run.py --suite errors --out /tmp/clinic-i3-errors
python tools/tests/wp_i_card/run.py --suite zoom --out /tmp/clinic-i3-zoom
```

Zoom requires WP_H_FULL_CHROMIUM and uses a disposable profile/extension.
The errors suite distinguishes actual API validation/conflicts from an injected
503 and loss of the response after an actual committed write. No automatic retry.

FIX1: `--suite clinical` exercises real note success, real event409/future-fact422,
explicit transport503 and response loss after a committed PATCH, typing during
in-flight writes, confirmed-placeholder reuse, peer identity switch and real
object403. Dirty baselines change only after acknowledged success. Conflict or
unknown result blocks resubmission until explicit discard plus fresh GET.
No clinical drafts are added to persistent storage. Read-only route labels do
not promise unavailable actions.
