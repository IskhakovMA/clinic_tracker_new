from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
ACTION_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
LOCAL_ACTION = re.compile(r"^\./\.github/actions/")
FORBIDDEN_RUN_PATTERNS = (
    "${{ github.event.",
    "curl | sh",
    "curl -fsSL | sh",
    "wget | sh",
    "wget -qO- | sh",
)


class UniqueKeyLoader(yaml.BaseLoader):
    pass


def _construct_mapping(loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise AssertionError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=UniqueKeyLoader)
    assert isinstance(data, dict), f"{path} must contain a YAML mapping"
    return data


def _walk_steps(data: Any):
    if isinstance(data, dict):
        if "uses" in data or "run" in data:
            yield data
        for value in data.values():
            yield from _walk_steps(value)
    elif isinstance(data, list):
        for value in data:
            yield from _walk_steps(value)


def test_workflows_exist_and_use_safe_triggers():
    files = _workflow_files()
    assert files, "at least one workflow is required"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "pull_request_target" not in text
        data = _load(path)
        triggers = data.get("on")
        assert isinstance(triggers, dict), f"{path}: triggers must be explicit mapping"
        assert set(triggers) <= {"pull_request", "push", "workflow_dispatch"}


def test_minimal_permissions_and_runner_policy():
    for path in _workflow_files():
        data = _load(path)
        assert data.get("permissions") == {"contents": "read"}, f"{path}: top-level permissions must be contents: read"
        assert "concurrency" in data, f"{path}: concurrency is required"
        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"{path}: jobs mapping is required"
        for job_name, job in jobs.items():
            assert job.get("runs-on") != "self-hosted", f"{path}:{job_name}: self-hosted runners are forbidden"
            assert "self-hosted" not in str(job.get("runs-on")), f"{path}:{job_name}: self-hosted runner label is forbidden"
            assert "timeout-minutes" in job, f"{path}:{job_name}: timeout-minutes is required"
            assert "container" not in job, f"{path}:{job_name}: container images are not used in this baseline"
            permissions = job.get("permissions")
            if permissions is not None:
                assert permissions == {"contents": "read"}, f"{path}:{job_name}: job permissions may not exceed contents: read"


def test_external_actions_are_pinned_to_full_sha_and_checkout_is_hardened():
    for path in _workflow_files():
        data = _load(path)
        for step in _walk_steps(data):
            uses = step.get("uses")
            if not uses:
                continue
            if LOCAL_ACTION.match(uses):
                continue
            assert ACTION_REF.match(uses), f"{path}: external action must use full 40-character SHA: {uses}"
            if uses.startswith("actions/checkout@"):
                opts = step.get("with") or {}
                assert opts.get("persist-credentials") == "false", f"{path}: checkout must disable persisted credentials"
                assert opts.get("fetch-depth") in {"0", "1"}, f"{path}: checkout must set bounded fetch-depth"


def test_shell_steps_do_not_use_untrusted_github_context_or_secrets():
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        assert "secrets." not in text, f"{path}: workflows must not consume repository secrets"
        for pattern in FORBIDDEN_RUN_PATTERNS:
            assert pattern not in text, f"{path}: forbidden shell pattern {pattern}"
        data = _load(path)
        for step in _walk_steps(data):
            run = step.get("run")
            if run:
                assert "${{ github.event." not in run, f"{path}: run step uses untrusted event context"
