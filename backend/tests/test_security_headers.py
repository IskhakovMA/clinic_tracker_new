EXPECTED_ON_ALL = {
    "content-security-policy",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
}


def test_headers_on_html(client):
    r = client.get("/")
    assert r.status_code == 200
    for h in EXPECTED_ON_ALL:
        assert h in r.headers, f"missing {h} on /"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp
    # script elements must NOT be allowed inline; only attr handlers (documented)
    assert "script-src 'self';" in csp or csp.split("script-src ")[1].startswith("'self'")


def test_headers_on_api(client):
    r = client.get("/api/auth/me")
    for h in EXPECTED_ON_ALL:
        assert h in r.headers, f"missing {h} on API"
    assert r.headers.get("cache-control") == "no-store"


def test_no_hsts_on_plain_http(client):
    r = client.get("/")
    assert "strict-transport-security" not in r.headers


def test_x_content_type_options_value(client):
    assert client.get("/").headers["x-content-type-options"] == "nosniff"


def test_untrusted_host_rejected(client):
    r = client.get("/", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_docs_disabled_outside_debug(client):
    assert client.get("/api/docs").status_code == 404
