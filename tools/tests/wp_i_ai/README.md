# I-6C: real API AI drawer regressions

Run with Python 3.13 dev environment, PLAYWRIGHT_MODULE, AXE_SOURCE, BROWSER_EXECUTABLE and WP_H_FULL_CHROMIUM (native Chrome, zoom only):

`python tools/tests/wp_i_ai/run.py --suite browser --count 1000 --out /tmp/i6c-browser`

Suites: browser (six real roles, two themes, widths1440/375/320, full-document axe A/AA, keyboard), workflows (actual server fallback, statuses, failures, race fences, clinical draft navigation, fresh object access, reauth/two tabs, actual rate limit), zoom (native Chrome tabs.setZoom100/200/400, temporary profile, both states/themes, full axe).

server.py runs the unchanged FastAPI application against temporary synthetic SQLite. A separate loopback-only synthetic OpenAI-shaped HTTP responder exercises provider discovery, minimized DTO and deterministic response errors. It has no model weights and is not evidence of real LLM quality, external provider readiness, Safari, Firefox or screen-reader testing. The test-only control endpoint never exists in production. Threading events/held responses synchronize races; no sleep/retry/xfail/skip. Transport injections are separately labelled in machine results; actual503/502/403/404/429 are asserted where indicated. No production provider settings/dependencies change.

All three UI payload keys remain message/history/patientContextId. Disabled or unavailable model can still return recognized server fallback200; unknown model-only questions fail503. UI never synthesizes a fallback answer or treats200 as provider availability. Model data is plain text, never trusted HTML. History is memory-only and limited to12 prior completed user/assistant entries. Provider status and dialog have independent read tickets; identity/context/epoch changes fence old results. Patient references use existing clinical navigation guard and fresh object GET. No medical write is issued by AI.

No new backend CAS or cross-request atomic snapshot contract is claimed. Access checks protect each server response and reference navigation, not a permanent authorization lease. Production S1, WebAuthn and schema32 are unchanged.
