"""WP-A release gates: policy and unit tests for the CI gate tooling.

These run in the workflow-policy job (PyYAML + pytest only, standard library
tooling). They pin the gate design so that it cannot be weakened unnoticed:
triggers, the aggregating release-gate job, complete backend test sharding, the
negative controls for gitleaks and Bandit, and the narrow exception files.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shards = _load_module("wp_a_pytest_shards", ROOT / "tools" / "ci" / "pytest_shards.py")
gate = _load_module("wp_a_bandit_gate", ROOT / "security-audit" / "tests" / "run_bandit_allowlisted.py")


def _ci() -> dict:
    return yaml.load(CI.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _run_steps(job: dict) -> list[str]:
    return [step.get("run", "") for step in job.get("steps", [])]


# ------------------------------------------------------------------ triggers and gate

def test_ci_runs_on_protected_integration_fix_feature_branches_prs_and_manually():
    on = _ci()["on"]
    assert {"main", "integration/**", "fix/**", "feature/**"} <= set(on["push"]["branches"])
    assert {"main", "integration/**"} <= set(on["pull_request"]["branches"])
    assert "workflow_dispatch" in on
    assert "pull_request_target" not in CI.read_text(encoding="utf-8")


def test_release_gate_requires_every_other_job_and_always_runs():
    jobs = _ci()["jobs"]
    gate_job = jobs["release-gate"]
    assert set(gate_job["needs"]) == set(jobs) - {"release-gate"}
    assert gate_job["if"] == "always()"
    script = "\n".join(_run_steps(gate_job))
    assert 'data["result"] != "success"' in script and "sys.exit(1)" in script


def test_backend_shards_cover_the_declared_count_and_are_verified():
    data = _ci()
    count = int(data["env"]["BACKEND_TEST_SHARDS"])
    jobs = data["jobs"]
    shard_job = jobs["backend-tests"]
    assert [int(x) for x in shard_job["strategy"]["matrix"]["shard"]] == list(range(1, count + 1))
    assert shard_job["strategy"]["fail-fast"] == "false"
    plan_job = jobs["backend-test-plan"]
    assert any("pytest_shards.py collect" in run for run in _run_steps(plan_job))
    assert set(plan_job["outputs"]) == {"count", "digest"}
    shard_run = "\n".join(_run_steps(shard_job))
    assert "--expect-count" in shard_run and "--expect-digest" in shard_run and "--planned-out" in shard_run
    assert "pytest_shards.py verify-shard" in shard_run
    assert 'if [ "${#files[@]}" -eq 0 ]' in shard_run and "exit 1" in shard_run, "empty shard must fail"
    verify_job = jobs["backend-tests-verify"]
    assert set(verify_job["needs"]) == {"backend-test-plan", "backend-tests"} and verify_job["if"] == "always()"
    verify_run = "\n".join(_run_steps(verify_job))
    assert '"$PLAN_RESULT" != "success"' in verify_run and '"$SHARDS_RESULT" != "success"' in verify_run


def test_gates_do_not_depend_on_artifact_storage():
    # The account's artifact quota was exhausted on the first GitHub run; a release gate
    # must not be blocked (or bypassed) by storage outside the repository's control.
    text = CI.read_text(encoding="utf-8")
    assert "upload-artifact" not in text and "download-artifact" not in text


def test_no_job_narrows_or_short_circuits_the_backend_suite():
    forbidden = re.compile(r"(^|\s)(-x|--exitfirst|-k|--ignore(-glob)?=?|--deselect|--lf|--last-failed|-m|--maxfail)(\s|=|$)")
    for name, job in _ci()["jobs"].items():
        for run in _run_steps(job):
            for line in run.splitlines():
                if re.search(r"(^|\s)pytest(\s|$)", line):
                    args = re.split(r"(?:^|\s)pytest(?:\s|$)", line, maxsplit=1)[1]
                    assert not forbidden.search(args), f"{name}: forbidden pytest option in {line!r}"


def test_negative_controls_run_before_the_real_scans():
    jobs = _ci()["jobs"]
    secret = _run_steps(jobs["secret-scan"])
    canary = next(i for i, run in enumerate(secret) if "gitleaks_canary.py" in run)
    first_scan = next(i for i, run in enumerate(secret) if " dir ." in run)
    assert canary < first_scan
    sast = _run_steps(jobs["sast-bandit"])
    assert sast.index("python security-audit/tests/run_bandit_allowlisted.py --self-test") < \
        sast.index("python security-audit/tests/run_bandit_allowlisted.py")


# ------------------------------------------------------------------ shard planner and verification

def _ids(spec: dict[str, int]) -> list[str]:
    return [f"backend/tests/{f}::test_{i}" for f, n in spec.items() for i in range(n)]


def test_shard_plan_is_a_deterministic_partition_of_files():
    ids = _ids({"test_a.py": 50, "test_b.py": 30, "test_c.py": 20, "test_d.py": 20, "test_e.py": 5})
    plan = shards.plan(ids, 3)
    assert plan == shards.plan(list(reversed(ids)), 3)          # order-independent
    files = [f for bucket in plan for f in bucket]
    assert sorted(files) == sorted({i.split("::")[0] for i in ids}) and len(files) == len(set(files))
    loads = [sum(1 for i in ids if i.split("::")[0] in bucket) for bucket in plan]
    assert max(loads) - min(loads) <= 25


def _result(ids, outcome="passed", exitstatus=0):
    return {"collected": list(ids), "outcomes": {i: outcome for i in ids}, "runs": {i: 1 for i in ids},
            "exitstatus": exitstatus}


def test_verify_accepts_complete_passing_shards():
    ids = _ids({"test_a.py": 3, "test_b.py": 2})
    assert shards.verify(ids, {"s1": _result(ids[:3]), "s2": _result(ids[3:])}, expected_count=5) == []


@pytest.mark.parametrize("label,results,needle", [
    ("missing test", lambda ids: {"s1": _result(ids[:4])}, "not collected by any shard"),
    ("duplicate", lambda ids: {"s1": _result(ids[:3]), "s2": _result(ids[2:])}, "more than one shard"),
    ("skipped", lambda ids: {"s1": _result(ids[:3]), "s2": _result(ids[3:], "skipped")}, "'skipped'"),
    ("failed", lambda ids: {"s1": _result(ids[:3]), "s2": _result(ids[3:], "failed", 1)}, "'failed'"),
    ("not executed", lambda ids: {"s1": _result(ids[:3]),
                                  "s2": {"collected": ids[3:], "outcomes": {}, "exitstatus": 0}}, "no outcome"),
    ("extra test", lambda ids: {"s1": _result(ids + ["backend/tests/test_z.py::test_0"])}, "absent from the full"),
])
def test_verify_rejects_incomplete_or_unclean_runs(label, results, needle):
    ids = _ids({"test_a.py": 3, "test_b.py": 2})
    problems = shards.verify(ids, results(ids))
    assert any(needle in p for p in problems), (label, problems)


def test_partition_check_rejects_missing_duplicate_and_empty_shards():
    ids = _ids({"test_a.py": 3, "test_b.py": 2, "test_c.py": 1})
    assert shards.check_partition(ids, shards.plan(ids, 3)) == []
    a, b, c = "backend/tests/test_a.py", "backend/tests/test_b.py", "backend/tests/test_c.py"
    assert any("cover exactly" in p for p in shards.check_partition(ids, [[a], [b]]))
    assert any("more than one shard" in p for p in shards.check_partition(ids, [[a, b], [b, c]]))
    assert any("no test files" in p for p in shards.check_partition(ids, [[a, b, c], []]))


def test_digest_is_order_independent_and_content_sensitive():
    ids = _ids({"test_a.py": 3})
    assert shards.digest(ids) == shards.digest(list(reversed(ids)))
    assert shards.digest(ids) != shards.digest(ids[:-1])


def test_verify_shard_accepts_exactly_the_planned_passing_tests():
    planned = _ids({"test_a.py": 3})
    assert shards.verify_shard(planned, _result(planned)) == []


@pytest.mark.parametrize("label,result,needle", [
    ("missing", lambda ids: _result(ids[:2]), "not collected by the shard"),
    ("extra", lambda ids: _result(ids + ["backend/tests/test_z.py::test_0"]), "not planned for it"),
    ("not executed", lambda ids: {**_result(ids), "outcomes": dict.fromkeys(ids[:2], "passed")}, "no outcome"),
    # Review of 356f587: these were accepted before the strict set checks.
    ("foreign passing outcome", lambda ids: {**_result(ids), "outcomes": {**dict.fromkeys(ids, "passed"),
                                             "backend/tests/test_x.py::test_ghost": "passed"}}, "not planned for this shard"),
    ("duplicate collection", lambda ids: {**_result(ids), "collected": ids + ids[:1]}, "collected more than once"),
    ("executed twice", lambda ids: {**_result(ids), "runs": {**dict.fromkeys(ids, 1), ids[0]: 2}}, "not executed exactly once"),
    ("execution count missing", lambda ids: {**_result(ids), "runs": dict.fromkeys(ids[:2], 1)}, "execution counts do not match"),
    ("no execution counts", lambda ids: {k: v for k, v in _result(ids).items() if k != "runs"}, "no execution counts"),
    ("exit status missing", lambda ids: {**_result(ids), "exitstatus": None}, "expected 0"),
    ("skipped", lambda ids: _result(ids, "skipped"), "'skipped'"),
    ("error", lambda ids: _result(ids, "error", 1), "'error'"),
    ("exit status", lambda ids: _result(ids, "passed", 5), "exit status 5"),
])
def test_verify_shard_rejects_incomplete_or_unclean_shards(label, result, needle):
    planned = _ids({"test_a.py": 3})
    problems = shards.verify_shard(planned, result(planned))
    assert any(needle in p for p in problems), (label, problems)


def test_verify_shard_rejects_duplicate_ids_in_the_plan():
    planned = _ids({"test_a.py": 2})
    assert any("duplicate id" in p for p in shards.verify_shard(planned + planned[:1], _result(planned)))


def test_whole_suite_verify_rejects_foreign_outcomes_and_repeated_execution():
    ids = _ids({"test_a.py": 3})
    ghost = {**_result(ids), "outcomes": {**dict.fromkeys(ids, "passed"), "backend/tests/test_x.py::test_ghost": "passed"}}
    assert any("absent from the full collection" in p for p in shards.verify(ids, {"s1": ghost}))
    twice = {**_result(ids), "runs": {**dict.fromkeys(ids, 1), ids[0]: 2}}
    assert any("not executed exactly once" in p for p in shards.verify(ids, {"s1": twice}))


def test_outcome_plugin_counts_every_real_execution(tmp_path):
    """Real pytest: the same file passed twice (--keep-duplicates) runs its test twice;
    the plugin must report 2 executions, so verify-shard rejects it."""
    import os
    import subprocess
    (tmp_path / "test_dup.py").write_text("def test_once():\n    assert True\n", encoding="utf-8")
    out = tmp_path / "outcomes.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTEST_OUTCOMES_FILE=str(out), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tools.ci.pytest_outcomes",
                           "--keep-duplicates", "test_dup.py", "test_dup.py"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["runs"] == {"test_dup.py::test_once": 2}
    problems = shards.verify_shard(["test_dup.py::test_once"], result)
    assert any("not executed exactly once" in p for p in problems)
    assert any("collected more than once" in p for p in problems)


def test_verify_checks_the_expected_total():
    ids = _ids({"test_a.py": 2})
    assert any("expected 3" in p for p in shards.verify(ids, {"s": _result(ids)}, expected_count=3))


# ------------------------------------------------------------------ Bandit fingerprints

def _issue(path: str, line: int, rng=None, test_id="B608", sev="MEDIUM", conf="MEDIUM"):
    return {"filename": path, "line_number": line, "line_range": rng or [line], "test_id": test_id,
            "issue_severity": sev, "issue_confidence": conf, "issue_text": "Possible SQL injection"}


SAFE = 'rows = conn.execute(f"SELECT 1 FROM t WHERE {CONST}").fetchall()\n'


def test_fingerprint_survives_moved_code_and_detects_edited_code(tmp_path):
    (tmp_path / "m.py").write_text(SAFE, encoding="utf-8")
    original = gate.fingerprint(_issue("m.py", 1), tmp_path)
    (tmp_path / "m.py").write_text("\n" * 37 + "    " + SAFE, encoding="utf-8")       # moved and re-indented
    assert gate.fingerprint(_issue("m.py", 38), tmp_path) == original
    (tmp_path / "m.py").write_text(SAFE.replace("{CONST}", "{CONST} AND {user}"), encoding="utf-8")
    assert gate.fingerprint(_issue("m.py", 1), tmp_path) != original


def test_gate_reports_unexpected_stale_and_analysis_errors(tmp_path):
    (tmp_path / "m.py").write_text(SAFE + 'conn.execute(f"SELECT * FROM t WHERE name = \'{name}\'")\n', encoding="utf-8")
    reviewed = {"file": "m.py", "test_id": "B608", "fingerprint": gate.fingerprint(_issue("m.py", 1), tmp_path),
                "statement": "x", "reason": "constant condition only, reviewed"}
    stale = dict(reviewed, fingerprint="0" * 64)
    report = {"results": [_issue("m.py", 1), _issue("m.py", 2), _issue("m.py", 2, sev="LOW")],
              "errors": [{"filename": "broken.py", "reason": "syntax error"}]}
    result = gate.evaluate(report, [reviewed, stale], tmp_path)
    assert [i["line_number"] for i in result["unexpected"]] == [2]          # LOW severity is not enforced
    assert result["stale"] == [stale] and result["allowed"] == 1 and len(result["errors"]) == 1


def test_bandit_allowlist_entries_are_reviewed_and_unique():
    entries = gate.load_allowlist()
    assert entries, "allowlist must not be empty while reviewed findings exist"
    keys = [(e["file"], e["test_id"], e["fingerprint"]) for e in entries]
    assert len(keys) == len(set(keys))
    assert {e["test_id"] for e in entries} == {"B608"}
    assert all("line" not in e for e in entries), "entries must not be keyed by line number"
    source = (ROOT / "security-audit" / "tests" / "run_bandit_allowlisted.py").read_text(encoding="utf-8")
    assert '"-ll", "-ii"' in source and "--skip" not in source and "B608" not in source.split("BANDIT_ARGS")[1].split("\n")[0]


# ------------------------------------------------------------------ gitleaks exceptions

def test_gitleaksignore_holds_only_commit_bound_test_fingerprints():
    lines = [line for line in (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    assert 0 < len(lines) <= 2
    for line in lines:
        assert re.fullmatch(r"[0-9a-f]{40}:backend/tests/[\w./-]+\.py:generic-api-key:\d+", line), line


def test_gitleaks_config_keeps_default_rules_and_no_path_wide_allowlist():
    import tomllib
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True
    assert "allowlist" not in config and "allowlists" not in config          # no global allowlists
    for rule in config.get("rules", []):
        assert set(rule) <= {"id", "allowlists"}, f"rule {rule.get('id')} must only extend a default rule"
        for allow in rule.get("allowlists", []):
            # Each exception is bound to one file AND an exact value shape — never a directory.
            assert allow.get("condition") == "AND" and allow.get("regexes"), allow
            for path in allow.get("paths", []):
                assert path.startswith("^") and path.endswith("$") and "\\." in path, path


def test_synthetic_export_keys_are_not_stored_as_literals():
    for label in ("test-only-export-signing-key-32b", "other-synthetic-export-key-32byt"):
        value = base64.urlsafe_b64encode(label.encode()).rstrip(b"=").decode()
        for path in (ROOT / "backend").rglob("*.py"):
            assert value not in path.read_text(encoding="utf-8"), f"{path} stores a key-like literal"
