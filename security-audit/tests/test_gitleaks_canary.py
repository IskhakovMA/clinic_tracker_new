import json
import math
import re
import subprocess
from collections import Counter
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


spec = spec_from_file_location(
    "gitleaks_canary", Path(__file__).resolve().parents[2] / "tools/ci/gitleaks_canary.py",
)
assert spec is not None and spec.loader is not None
canary = module_from_spec(spec)
spec.loader.exec_module(canary)

TEST_PATH = "backend/tests/test_canary_secret.py"
DETECTION = {"File": TEST_PATH, "RuleID": "generic-api-key", "StartLine": 1}


def test_synthetic_fixture_is_stable_and_has_detector_shape():
    value = canary._synthetic_sample()
    assert value == canary._synthetic_sample()
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert re.search(r"\d", value) and re.search(r"[a-f]", value)
    probabilities = [count / len(value) for count in Counter(value).values()]
    assert -sum(p * math.log2(p) for p in probabilities) > 3.5


@pytest.mark.parametrize(
    ("code", "findings", "detected"),
    [
        (1, [DETECTION], True),
        (0, [DETECTION], False),
        (2, [DETECTION], False),
        (1, None, False),
        (1, "malformed", False),
        (1, DETECTION, False),
        (1, [{**DETECTION, "File": "elsewhere.py"}], False),
        (1, [{**DETECTION, "RuleID": "other-rule"}], False),
        (1, [{**DETECTION, "StartLine": 2}], False),
    ],
)
def test_canary_requires_a_real_finding_at_the_expected_location(
    monkeypatch, tmp_path, code, findings, detected,
):
    def fake_scan(args, *, cwd, capture_output):
        assert cwd == tmp_path and capture_output and "--redact=100" in args
        report = Path(args[args.index("--report-path") + 1])
        assert not report.is_relative_to(cwd)
        if findings is not None:
            report.write_text(findings if isinstance(findings, str) else json.dumps(findings))
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(canary.subprocess, "run", fake_scan)
    result = canary._expect_leak("gitleaks", "git", tmp_path, TEST_PATH, 1, "test control")
    assert (result == []) is detected
