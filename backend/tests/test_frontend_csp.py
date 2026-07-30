"""Static guarantees about the frontend that the CSP depends on.

These read the shipped files rather than a browser, so a regression that
reintroduces an inline handler or an innerHTML sink fails in CI rather than
silently relying on 'unsafe-inline' being re-added later.
"""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HTML = PROJECT_ROOT / "clinic-v2.html"
JS = PROJECT_ROOT / "clinic-v2.js"

# on<name>= as an *attribute*: preceded by whitespace, followed by a quote.
INLINE_HANDLER_RE = re.compile(r"""\son[a-z]+\s*=\s*["']""")
# Same pattern inside JS template literals / strings.
NON_HANDLER_ATTRS = {"once"}  # e.g. `once=` is not an event handler


def _inline_handler_hits(text: str) -> list[str]:
    hits = []
    for match in INLINE_HANDLER_RE.finditer(text):
        attr = match.group(0).strip().split("=")[0]
        if attr.lower() in NON_HANDLER_ATTRS:
            continue
        hits.append(text[max(0, match.start() - 60):match.end() + 20])
    return hits


def test_static_html_has_no_inline_handlers():
    hits = _inline_handler_hits(HTML.read_text(encoding="utf-8"))
    assert hits == [], f"inline handlers left in clinic-v2.html: {hits[:3]}"


def test_js_templates_do_not_emit_inline_handlers():
    hits = _inline_handler_hits(JS.read_text(encoding="utf-8"))
    assert hits == [], f"inline handlers emitted from clinic-v2.js: {hits[:3]}"


def test_no_innerhtml_sinks():
    for path in (HTML, JS):
        text = path.read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in text, f"{sink} present in {path.name}"


def test_no_dynamic_code_evaluation():
    js = JS.read_text(encoding="utf-8")
    assert "eval(" not in js
    assert "new Function" not in js
    # setTimeout/setInterval with a string body is an eval in disguise.
    assert not re.search(r"set(Timeout|Interval)\s*\(\s*['\"]", js)


def test_escape_js_attr_helper_is_gone():
    """It existed only to smuggle values into inline handler attributes."""
    for path in (HTML, JS):
        assert "escapeJsAttr" not in path.read_text(encoding="utf-8")


def test_medical_data_not_placed_in_data_attributes():
    js = JS.read_text(encoding="utf-8")
    for field in ("p.name", "p.diagnosis", "p.complaint", "p.doctor", "p.head", "p.phone"):
        assert f"dataset: {{ {field}" not in js
        assert f"data-name=\"${{{field}}}\"" not in js


def test_localstorage_limited_to_theme_and_migration():
    """Medical data must not be written to localStorage; only the UI theme
    and the one-time legacy migration read may touch it."""
    js = JS.read_text(encoding="utf-8")
    calls = re.findall(r"localStorage\.(getItem|setItem|removeItem)\(\s*'([^']+)'", js)
    keys = {key for _op, key in calls}
    assert keys <= {"theme", "patients", "user"}, keys
    # 'patients'/'user' may only be read or removed (migration path), never written.
    for op, key in calls:
        if key in ("patients", "user"):
            assert op in ("getItem", "removeItem"), f"{op} on localStorage['{key}'] is not allowed"


@pytest.mark.parametrize("forbidden", [
    "unsafe-eval",
    "script-src 'unsafe-inline'",
    "script-src-attr 'unsafe-inline'",
])
def test_csp_forbids_inline_script(client, forbidden):
    csp = client.get("/").headers["content-security-policy"]
    assert forbidden not in csp


def test_csp_shape():
    from backend.app.main import CSP
    assert "script-src 'self'" in CSP
    assert "script-src-attr 'none'" in CSP
    assert "object-src 'none'" in CSP
    assert "base-uri 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP
    assert "form-action 'self'" in CSP
    assert "default-src 'self'" in CSP


def test_frontend_files_are_served(client):
    assert client.get("/").status_code == 200
    js_response = client.get("/clinic-v2.js")
    assert js_response.status_code == 200
    assert "const app = {" in js_response.text
    # The page must load its script from a same-origin file, not inline.
    assert '<script src="clinic-v2.js"></script>' in client.get("/").text
