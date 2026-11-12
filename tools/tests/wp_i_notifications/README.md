# WP-I I-6A notifications browser regressions

Synthetic localhost FastAPI and a disposable SQLite database. No working medical
DB, external provider or real SMS/email is used. The server wrapper installs its
single read-audit fault only in this test process; production imports are unchanged.

Use the existing locked developer Python environment, Playwright, Chromium and
axe-core already provisioned for WP-H. Set `PLAYWRIGHT_MODULE`, `BROWSER_EXECUTABLE`,
`AXE_SOURCE`; native zoom also needs `WP_H_FULL_CHROMIUM` (full Chromium, not its
headless shell). No new npm dependencies are required.

From the repository root:

```sh
python tools/tests/wp_i_notifications/run.py --count 100 --suite browser --out /tmp/i6-browser
python tools/tests/wp_i_notifications/run.py --count 100 --suite workflows --out /tmp/i6-workflows
python tools/tests/wp_i_notifications/run.py --count 100 --suite retry --out /tmp/i6-retry
python tools/tests/wp_i_notifications/run.py --count 100 --suite zoom --out /tmp/i6-zoom
```

- `browser`: six roles, authoritative unread, mark/read-all races, a second context,
  grouping, server paging/search, fresh patient navigation, stale responses,
  actual read-audit503 and session401, separate transport injections,
  both themes at1440/375/320 and full-document axe WCAG A/AA.
- `workflows`: consent/grant/revoke; one create on double activation; institution
  Moscow→UTC; commit followed by lost response; explicit reconciliation without
  retry; old-patient response; object-scope404, finite actual rate-limit429;
  patient layout/axe; explicit developer mock queue processing.
- `retry`: `permanent_failure` mock provider, actual failed→pending, one developer
  retry under duplicate activation. Provider result is not real delivery.
- `zoom`: Chrome `tabs.setZoom` in a throwaway extension/profile at100/200/400%,
  both themes, width1280/640/320 and DPR1/2/4. No CSS zoom or existing user profile.

Tests record real server denials separately from delayed/aborted/injected network
responses. Assertions retain server guards: inaccessible objects return404;
non-developer retry returns403; already-cancelled returns409. Successful UI reads
perform no automatic notification writes. Fixtures contain only synthetic users,
phone numbers and patient names. Browser and server close in `finally`; temporary
DB/profile is removed. Screenshots are synthetic evidence, never personal data.

Baseline screenshots can be reproduced with `I6_BASELINE=1` while serving the
approved I-5C commit15952849; the baseline branch of `browser.cjs` only captures
screens. All real assertions run without that variable. This does not certify
physical mobile devices, Safari/Firefox or a screen reader.

## I-6A FIX1: scoped failures and protected clinical navigation

`python tools/tests/wp_i_notifications/run.py --suite fix1 --count 1000 --out /absolute/evidence/fix1`

The additional suite uses real FastAPI and temporary synthetic SQLite data. It tests independent summary/inbox/outgoing/patient error boundaries, retained reminder/template fields through explicit GET verification, real validation failures, and a committed creation whose response is deliberately lost. Transport injections are listed separately from actual server responses in `results.json`. A pending write disables creation; an uncertain result requires an explicit server read, with no automatic write replay.

Clinical navigation tests use the existing note guard, mouse and keyboard cancellation, one scoped object GET on confirmation (outside the loaded registry page), blocked navigation during a real clinical write, and newer input during a delayed object read. They also test verified same-user reauthentication, a shared-cookie identity switch, object-access revocation, six full-document axe A/AA theme/viewport combinations, and retained-field screenshots. Existing clinical-event and zoom suites remain separate regression gates.

`before_fix1.cjs` is an explicitly diagnostic reproduction for the preserved, unmodified I-6A source, not an acceptance suite for corrected code. It confirms the original input loss and guard bypass. Run it with the evidence runner against the approved baseline copy. No production credentials or databases are used. Failure logs from test development are preserved separately from final results.

## I-6A FIX2: field-specific template drafts

`python tools/tests/wp_i_notifications/run.py --suite fix2 --count 1000 --out /absolute/evidence/fix2`

Two independent authenticated sessions edit two real server templates. The suite verifies field-specific original baselines, fresh untouched body/enabled values, same-template conflicts, explicit discard/cancel, body-only and enabled-only PUT, no PUT for an unchanged template, a pre-save freshness read, typing during that read, actual422, labeled transport503, lost response after a real committed PUT, reauth, permission revocation and identity switch. Full-document axe A/AA and screenshots cover both themes at1440/375/320px. The existing native zoom suite remains required.

Conflict drafts remain visible separately from server values; saving is blocked until the operator explicitly discards the conflicting draft and re-enters intended changes. Only actual changed fields are sent through the existing optional-field PUT contract. Freshness reads detect observed changes; they are **not server CAS**. A concurrent same-field write between GET and PUT can still race, and enabled-only changes have no monotonic server version. No new concurrency contract is claimed or added.

An explicit response barrier also verifies that a newer outgoing GET fences an older pre-save200. A real enabled-only update committed between the freshness GET and body-only PUT is preserved and must not become an invented local draft. Role revocation and verified reauth remove the old editor. These checks preserve the accepted FIX1 and consilium synchronization tests unchanged.
