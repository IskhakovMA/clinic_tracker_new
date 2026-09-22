"""Pre-production hardening, round 2: patient-name search stays out of URLs.

Regression tests for preprod-run-2 lead NV2-11: the SPA sent patient-name
search text as ?search= on GET requests, so any reverse proxy with a
default access-log format could record patient names.
"""
from pathlib import Path
from urllib.parse import quote

from .conftest import PROJECT_ROOT


def test_spa_never_puts_search_text_in_query_strings():
    js = (Path(PROJECT_ROOT) / "static" / "app.js").read_text(encoding="utf-8")
    assert "params.set('search'" not in js
    assert "X-Search" in js


def test_search_query_parameter_is_rejected_and_header_is_used(auth_client):
    from .test_notifications import _create_patient_direct, _grant, _manual
    pid = _create_patient_direct(name="Искомов Пациент")
    other = _create_patient_direct(name="Другой Пациент")
    for p in (pid, other):
        _grant(auth_client, p)
        assert _manual(auth_client, p).status_code == 201
    hdr = {"X-Search": quote("Искомов")}
    for path in ("/api/notifications", "/api/data-quality/issues", "/api/internal-notifications"):
        assert auth_client.get(f"{path}?search=x").status_code == 422, path
        assert auth_client.get(path, headers=hdr).status_code == 200, path
    body = auth_client.get("/api/notifications", headers=hdr).json()
    assert body["total"] == 1
    assert auth_client.get("/api/notifications", headers={"X-Search": quote("Я" * 121)}).status_code == 422
