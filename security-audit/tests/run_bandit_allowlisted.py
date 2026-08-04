from __future__ import annotations

import json
import subprocess
import sys


BANDIT_CMD = [
    "bandit",
    "-r",
    "backend/app",
    "-x",
    "backend/tests",
    "-ll",
    "-ii",
    "-f",
    "json",
    "-q",
]

# Each entry was manually reviewed during SEC-2026-004 remediation. They are
# Bandit B608 false positives caused by internal SQL fragments built from
# allowlisted table/column names, placeholder lists, or policy-generated WHERE
# clauses with values still bound through sqlite parameters. New or moved B608
# findings are intentionally not covered by this allowlist.
ALLOWLIST = {
    ("backend/app/analytics/service.py", 102, "B608"),
    ("backend/app/analytics/service.py", 115, "B608"),
    ("backend/app/analytics/service.py", 128, "B608"),
    ("backend/app/analytics/service.py", 161, "B608"),
    ("backend/app/analytics/service.py", 185, "B608"),
    ("backend/app/analytics/service.py", 374, "B608"),
    ("backend/app/analytics/service.py", 485, "B608"),
    ("backend/app/analytics/service.py", 567, "B608"),
    ("backend/app/analytics/service.py", 608, "B608"),
    ("backend/app/analytics/service.py", 681, "B608"),
    ("backend/app/policy.py", 114, "B608"),
    ("backend/app/routes/staff.py", 105, "B608"),
    ("backend/app/routes/staff.py", 106, "B608"),
    ("backend/app/services/data_quality.py", 206, "B608"),
    ("backend/app/services/data_quality.py", 248, "B608"),
    ("backend/app/services/data_quality.py", 268, "B608"),
    ("backend/app/services/data_quality.py", 284, "B608"),
    ("backend/app/services/notifications.py", 191, "B608"),
    ("backend/app/services/notifications.py", 350, "B608"),
    ("backend/app/services/notifications.py", 359, "B608"),
    ("backend/app/services/notifications.py", 365, "B608"),
    ("backend/app/services/notifications.py", 371, "B608"),
    ("backend/app/services/notifications.py", 456, "B608"),
    ("backend/app/services/notifications.py", 527, "B608"),
    ("backend/app/store.py", 152, "B608"),
    ("backend/app/store.py", 273, "B608"),
    ("backend/app/store.py", 397, "B608"),
    ("backend/app/store.py", 445, "B608"),
    ("backend/app/users.py", 123, "B608"),
    ("backend/app/users.py", 218, "B608"),
    ("backend/app/users.py", 223, "B608"),
    ("backend/app/users.py", 298, "B608"),
    ("backend/app/users.py", 319, "B608"),
    ("backend/app/users.py", 333, "B608"),
}


def _rank(value: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(value.upper(), 0)


def main() -> int:
    proc = subprocess.run(BANDIT_CMD, text=True, capture_output=True, check=False)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode or 1

    unexpected = []
    allowed = 0
    for issue in report.get("results", []):
        key = (issue["filename"], issue["line_number"], issue["test_id"])
        enforce = _rank(issue["issue_severity"]) >= 2 and _rank(issue["issue_confidence"]) >= 2
        if enforce and key not in ALLOWLIST:
            unexpected.append(issue)
        elif key in ALLOWLIST:
            allowed += 1

    if unexpected:
        for issue in unexpected:
            print(
                f"{issue['filename']}:{issue['line_number']} "
                f"{issue['test_id']} {issue['issue_severity']}/{issue['issue_confidence']} "
                f"{issue['issue_text']}",
                file=sys.stderr,
            )
        return 1

    print(f"Bandit completed; reviewed allowlisted findings: {allowed}; unexpected enforced findings: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
