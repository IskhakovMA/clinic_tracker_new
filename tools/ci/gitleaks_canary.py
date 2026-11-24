"""Negative control for the secret scan: prove that gitleaks still reports secrets.

Runs gitleaks with this repository's own .gitleaks.toml and .gitleaksignore
against throwaway Git repositories in a temporary directory (the repository
itself is never modified) and fails unless every planted secret is reported:

1. a deterministic synthetic API-key fixture in a test file (backend/tests/…) — test code is not
   exempt from scanning, neither in Git history nor in the working tree;
2. the old synthetic export key value committed again at the same path and
   line as the reviewed historical finding — .gitleaksignore entries are bound
   to one commit, so the value is reported in any other commit.

Planted values are generated at run time; nothing secret-looking is stored in
this file. Usage: python tools/ci/gitleaks_canary.py /path/to/gitleaks
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = (".gitleaks.toml", ".gitleaksignore")
FIXTURE_SEED = b"Clinic Tracker CI synthetic canary fixture v1"


def _synthetic_sample() -> str:
    return hashlib.sha256(FIXTURE_SEED).hexdigest()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=canary", "-c", "user.email=canary@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _new_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir()
    _git(repo, "init", "-q")
    for config in CONFIG_FILES:
        shutil.copy(ROOT / config, repo / config)
    (repo / "README.md").write_text("canary\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _expect_leak(
    gitleaks: str, mode: str, repo: Path, path: str, line: int, label: str,
) -> list[str]:
    # Keep the report outside the scanned tree. Do not echo report values or scanner output.
    with tempfile.TemporaryDirectory(prefix="gitleaks-report-") as tmp:
        report = Path(tmp) / "findings.json"
        try:
            result = subprocess.run(
                [gitleaks, mode, ".", "--no-banner", "--no-color", "--redact=100",
                 "--report-format", "json", "--report-path", str(report)],
                cwd=repo, capture_output=True,
            )
            if result.returncode != 1:
                return [f"FAIL {label}: gitleaks {mode} exit={result.returncode}, expected finding"]
            findings = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [f"FAIL {label}: gitleaks {mode} did not produce a readable JSON report"]

    if isinstance(findings, list) and any(
        isinstance(item, dict)
        and item.get("File") == path
        and item.get("RuleID") == "generic-api-key"
        and item.get("StartLine") == line
        for item in findings
    ):
        print(f"ok   {label}: gitleaks {mode} reported {path}:{line}")
        return []
    return [f"FAIL {label}: gitleaks {mode} did not report the expected rule and location"]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    gitleaks = sys.argv[1]
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gitleaks-canary-") as tmp:
        base = Path(tmp)

        # 1. A deterministic mixed-hex fixture with entropy above the pinned rule threshold.
        repo = _new_repo(base, "fresh-key")
        planted = "backend/tests/test_canary_secret.py"
        target = repo / planted
        target.parent.mkdir(parents=True)
        target.write_text(f'CANARY_API_KEY = "{_synthetic_sample()}"\n', encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "plant canary")
        failures += _expect_leak(gitleaks, "git", repo, planted, 1, "synthetic key in history")
        failures += _expect_leak(gitleaks, "dir", repo, planted, 1, "synthetic key in working tree")

        # 2. The old synthetic value at the ignored path and line, in a new commit.
        repo = _new_repo(base, "old-value-new-commit")
        planted = "backend/tests/test_signed_export.py"
        target = repo / planted
        target.parent.mkdir(parents=True)
        old_value = base64.urlsafe_b64encode(b"other-synthetic-export-key-32byt").rstrip(b"=").decode()
        lines = ["# filler\n"] * 25 + [f'OTHER_KEY = "{old_value}"\n']   # line 26, as in the ignored finding
        target.write_text("".join(lines), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "re-commit old synthetic value")
        failures += _expect_leak(gitleaks, "git", repo, planted, 26, "ignored value in another commit")

    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print("gitleaks canary: every planted secret was reported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
