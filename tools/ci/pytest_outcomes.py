"""pytest plugin: record what a CI shard collected and what each test's outcome was.

Enabled with `-p tools.ci.pytest_outcomes` and the environment variable
PYTEST_OUTCOMES_FILE (path of a JSON file to write). The file holds the node
ids collected in this run and one final outcome per executed test, so that
tools/ci/pytest_shards.py verify can prove that the shards together ran every
collected test exactly once, and that nothing was skipped or deselected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_collected: list[str] = []
_outcomes: dict[str, str] = {}


def pytest_collection_finish(session):
    _collected.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
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
        "exitstatus": int(exitstatus),
    }, ensure_ascii=False, indent=0), encoding="utf-8")
