"""Bandit gate with a reviewed allowlist keyed by code fingerprint, not by line.

Every enforced finding (severity and confidence >= MEDIUM) fails the gate
unless the exact flagged statement was reviewed and recorded in
security-audit/bandit-allowlist.json. An entry is bound to the file, the
Bandit test id and a SHA-256 of the flagged source lines with whitespace
normalised, so:

* code that only moves (lines inserted above it, re-indented) keeps matching;
* any change to the flagged statement itself, a new statement, or the same
  statement in another file is unexpected and needs a fresh review;
* an entry that no longer matches any finding is reported as stale and fails
  the gate, so the list cannot silently accumulate dead exceptions;
* files Bandit could not analyse ("errors" in its report) fail the gate.

B608 is not disabled anywhere; the scan thresholds are unchanged (-ll -ii).

Usage:
  python security-audit/tests/run_bandit_allowlisted.py              # CI gate
  python security-audit/tests/run_bandit_allowlisted.py --self-test  # negative control with real Bandit
  python security-audit/tests/run_bandit_allowlisted.py --candidates # print entries for unexpected findings (review first!)
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / "security-audit" / "bandit-allowlist.json"
BANDIT_ARGS = ["-ll", "-ii", "-f", "json", "-q"]
BANDIT_CMD = ["bandit", "-r", "backend/app", "-x", "backend/tests", *BANDIT_ARGS]


def _rank(value: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(str(value).upper(), 0)


def enforced(issue: dict) -> bool:
    return _rank(issue["issue_severity"]) >= 2 and _rank(issue["issue_confidence"]) >= 2


def statement(issue: dict, root: Path) -> str:
    """The flagged source lines, whitespace-normalised (independent of position)."""
    lines = (root / issue["filename"]).read_text(encoding="utf-8").splitlines()
    rng = issue.get("line_range") or [issue["line_number"]]
    first, last = min(rng), max(rng)
    text = " ".join(line.strip() for line in lines[first - 1:last])
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(issue: dict, root: Path) -> str:
    payload = f"{issue['test_id']}\n{issue['filename']}\n{statement(issue, root)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["entries"]
    for entry in entries:
        missing = {"file", "test_id", "fingerprint", "statement", "reason"} - set(entry)
        if missing:
            raise ValueError(f"allowlist entry without {sorted(missing)}: {entry}")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["fingerprint"]):
            raise ValueError(f"allowlist fingerprint must be sha256 hex: {entry}")
        if len(entry["reason"].strip()) < 20:
            raise ValueError(f"allowlist entry needs a concrete review reason: {entry}")
    return entries


def evaluate(report: dict, allowlist: list[dict], root: Path) -> dict:
    """Pure gate decision. Returns unexpected findings, stale entries, errors."""
    keys = {(e["file"], e["test_id"], e["fingerprint"]) for e in allowlist}
    matched: set[tuple[str, str, str]] = set()
    unexpected: list[dict] = []
    allowed = 0
    for issue in report.get("results", []):
        if not enforced(issue):
            continue
        key = (issue["filename"], issue["test_id"], fingerprint(issue, root))
        if key in keys:
            matched.add(key)
            allowed += 1
        else:
            unexpected.append(issue)
    stale = [e for e in allowlist if (e["file"], e["test_id"], e["fingerprint"]) not in matched]
    return {"unexpected": unexpected, "stale": stale, "errors": list(report.get("errors") or []),
            "allowed": allowed}


def _run_bandit(cmd: list[str], cwd: Path) -> dict:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode or 1)


def _report_failures(result: dict) -> int:
    for issue in result["unexpected"]:
        print(f"{issue['filename']}:{issue['line_number']} {issue['test_id']} "
              f"{issue['issue_severity']}/{issue['issue_confidence']} {issue['issue_text']}", file=sys.stderr)
    for entry in result["stale"]:
        print(f"stale allowlist entry (no longer matches any finding): {entry['file']} "
              f"{entry['test_id']} {entry['fingerprint'][:12]}…", file=sys.stderr)
    for error in result["errors"]:
        print(f"bandit could not analyse: {error}", file=sys.stderr)
    return 1 if (result["unexpected"] or result["stale"] or result["errors"]) else 0


def main() -> int:
    report = _run_bandit(BANDIT_CMD, ROOT)
    result = evaluate(report, load_allowlist(), ROOT)
    if _report_failures(result):
        return 1
    print(f"Bandit completed; reviewed allowlisted findings: {result['allowed']}; "
          "unexpected enforced findings: 0; stale entries: 0; analysis errors: 0")
    return 0


def candidates() -> int:
    report = _run_bandit(BANDIT_CMD, ROOT)
    result = evaluate(report, load_allowlist(), ROOT)
    out = [{"file": i["filename"], "test_id": i["test_id"], "fingerprint": fingerprint(i, ROOT),
            "statement": statement(i, ROOT)[:300], "reason": "TODO: concrete review reason"}
           for i in result["unexpected"]]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


SAFE_QUERY = '''
def count_active(conn):
    return conn.execute(f"SELECT COUNT(*) FROM patients WHERE {ACTIVE_SQL}").fetchone()[0]
'''
INJECTABLE_QUERY = '''
def find_by_name(conn, name):
    return conn.execute(f"SELECT * FROM patients WHERE name = '{name}'").fetchall()
'''
EDITED_SAFE_QUERY = '''
def count_active(conn, extra):
    return conn.execute(f"SELECT COUNT(*) FROM patients WHERE {ACTIVE_SQL} AND {extra}").fetchone()[0]
'''


def self_test() -> int:
    """Negative control with the real Bandit binary on throwaway files."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="bandit-gate-") as tmp:
        root = Path(tmp)
        module = root / "pkg" / "queries.py"
        module.parent.mkdir()
        cmd = ["bandit", "-r", "pkg", *BANDIT_ARGS]

        def scan(source: str) -> dict:
            module.write_text("ACTIVE_SQL = 'deleted_at IS NULL'\n" + source, encoding="utf-8")
            return _run_bandit(cmd, root)

        base = scan(SAFE_QUERY)
        flagged = [i for i in base["results"] if enforced(i)]
        if len(flagged) != 1 or flagged[0]["test_id"] != "B608":
            return _fail([f"baseline: expected one B608 finding, got {[(i['test_id'], i['line_number']) for i in flagged]}"])
        allowlist = [{"file": flagged[0]["filename"], "test_id": "B608",
                      "fingerprint": fingerprint(flagged[0], root),
                      "statement": statement(flagged[0], root), "reason": "self-test: constant condition only"}]

        cases = (
            ("reviewed statement passes", SAFE_QUERY, 0),
            ("reviewed statement moved 40 lines down passes", "\n" * 40 + SAFE_QUERY, 0),
            ("new injectable query is reported", SAFE_QUERY + INJECTABLE_QUERY, 1),
            ("edited reviewed statement is reported (and its entry is stale)", EDITED_SAFE_QUERY, 1),
        )
        for label, source, expected in cases:
            result = evaluate(scan(source), allowlist, root)
            code = 1 if (result["unexpected"] or result["stale"] or result["errors"]) else 0
            status = "ok  " if code == expected else "FAIL"
            print(f"{status} {label}: gate exit {code} (expected {expected}); "
                  f"unexpected={[i['line_number'] for i in result['unexpected']]} stale={len(result['stale'])}")
            if code != expected:
                failures.append(label)
            if label.startswith("new injectable") and not any(
                    "name = '{name}'" in statement(i, root) for i in result["unexpected"]):
                failures.append("injectable query was not the reported finding")
    return _fail(failures) if failures else 0


def _fail(messages: list[str]) -> int:
    for message in messages:
        print(f"self-test failure: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--self-test":
        raise SystemExit(self_test())
    if arg == "--candidates":
        raise SystemExit(candidates())
    raise SystemExit(main())
