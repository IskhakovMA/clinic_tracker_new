"""Exercise the production Quality reader's state transitions in Node.js."""

from pathlib import Path
import re
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_wp_i_i8a3_quality_reader_state():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the Quality reader state tests"
    result = subprocess.run(
        [node, "--test", "tools/tests/wp_i_quality/state.test.cjs"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wp_i_i8a3_quality_asset_is_served_under_the_existing_csp(client):
    home = client.get("/")
    assert home.status_code == 200
    assert re.search(r'<script src="/static/quality-v1\.js(?:\?[^"]*)?"></script>', home.text)

    asset = client.get("/static/quality-v1.js")
    assert asset.status_code == 200
    assert asset.headers["content-type"].split(";", 1)[0] == "application/javascript"
    assert asset.content == (PROJECT_ROOT / "static" / "quality-v1.js").read_bytes()
    assert asset.headers["x-content-type-options"] == "nosniff"
    csp = asset.headers["content-security-policy"]
    assert csp == home.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "script-src-attr 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp

    script = asset.text
    assert all(sink not in script for sink in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
        "new Function", "localStorage", "sessionStorage",
    ))
    assert not re.search(r"\beval\s*\(", script)
    assert client.get("/static/unknown-quality-asset.js").status_code == 404
