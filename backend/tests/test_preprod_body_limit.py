"""Pre-production hardening: oversized request bodies are refused early.

Regression test for audit lead NV-11: no layer limited request-body size,
so an unauthenticated client could make the shared process buffer and
JSON-decode an arbitrarily large body before any auth or size check.
"""


def test_oversized_body_with_content_length_gets_413(client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "MAX_REQUEST_BODY_BYTES", 4096)
    big = '{"username": "' + "A" * 10_000 + '", "password": "x"}'
    r = client.post("/api/auth/login", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_oversized_chunked_body_gets_413(client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "MAX_REQUEST_BODY_BYTES", 4096)

    def gen():
        yield b'{"username": "'
        for _ in range(20):
            yield b"A" * 1000
        yield b'", "password": "x"}'

    r = client.post("/api/auth/login", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_normal_body_is_unaffected(client):
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"})
    assert r.status_code == 401


def test_default_limit_fits_largest_legitimate_import():
    from backend.app import config
    assert config.MAX_REQUEST_BODY_BYTES >= 2 * 6_000_000
