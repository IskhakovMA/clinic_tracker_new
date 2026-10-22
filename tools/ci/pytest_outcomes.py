"""pytest plugin: record what a CI shard collected and what each test's outcome was.

Enabled with `-p tools.ci.pytest_outcomes` and the environment variable
PYTEST_OUTCOMES_FILE (path of a JSON file to write). The file holds the node
ids collected in this run, one final outcome per executed test and how many
times each test was actually executed (one setup phase per execution), so that
tools/ci/pytest_shards.py can prove that every planned test ran exactly once,
passed, and that nothing was skipped, deselected or repeated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_collected: list[str] = []
_outcomes: dict[str, str] = {}
_runs: dict[str, int] = {}


def pytest_collection_finish(session):
    _collected.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    if report.when == "setup":                       # every execution of a test starts with setup
        _runs[report.nodeid] = _runs.get(report.nodeid, 0) + 1
    previous = _outcomes.get(report.nodeid)
    if report.failed:
        outcome = "failed" if report.when == "call" else "error"
    elif report.skipped:
        outcome = "skipped"
    elif report.when == "call":
        outcome = "passed"
    else:
        return                       # setup/teardown passed: keep the call result
    if previous in ("failed", "error"):
        return                       # a teardown error must not hide a failure, and vice versa
    if previous == "skipped" and outcome == "passed":
        return
    _outcomes[report.nodeid] = outcome


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("PYTEST_OUTCOMES_FILE")
    if not path:
        return
    Path(path).write_text(json.dumps({
        "collected": _collected,
        "outcomes": _outcomes,
        "runs": _runs,
        "exitstatus": int(exitstatus),
    }, ensure_ascii=False, indent=0), encoding="utf-8")
