# Stage 2B isolated continuity lab

Research only. No application startup imports this directory. No service is installed, no external witness bound, no WP-F v1 field changed. The two local SQLite directories simulate two administrative rollback domains; this does NOT make same-machine storage independent.

Use Python 3.13 and the unchanged hash-pinned backend/requirements-dev.txt. Tests: `python -m pytest backend/tests/test_wp_g_stage2b_continuity.py -q`. Measurements: `python -m prototype.continuity_lab.measure` (creates and removes only its own TemporaryDirectory).

Lab.fixture creates a NEW synthetic installation only, with exclusive creation. It cannot adopt an existing installation. Lab.current verifies sequence, projection, policy high-water, receipt and audit frontier; policy_gate returns a string, never a medical grant. change/maintain/flow/cleanup are privileged in-process models with synthetic inputs, not HTTP endpoints or production policy enforcement. No WebAuthn crypto, real factor activation, recovery ceremony or issuance of MFA sessions is implemented here.

Tests cover coordinated mutations, controlled EIO/ENOSPC, actual receipt fsync failures, SIGKILL, idempotent acknowledgments, concurrency, stale DB/sidecars, missing/corrupt witness, policy downgrade and explicit lifecycle compaction. The real application audit and S1 tests use the existing synthetic FastAPI fixture; other lab tests are independent SQLite models. See docs/stage2b/ADR_SECURITY_CONTINUITY_003.md for exact boundaries, stopped integration and required owner decisions.

measure.py is an executable benchmark with assert-based measurement invariants, not security admission code. Bandit flags its seven B101 LOW assertions in the unfiltered supplemental scan; no production gate/rule is weakened and the model itself has no assert-based access checks. Running Python with -O would remove those measurement checks, so reproduce measurement with ordinary Python as shown.
