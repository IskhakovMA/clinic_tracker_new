"""Pre-production hardening: rate limiters keep their own accounting.

Regression tests for audit leads NV-16 (every request limiter call pruned
all buckets with the caller's window, shrinking the 300 s AI chat budget
to about 60 s), NV-12 (a concurrent login burst from one host got past
the limiter because failures were recorded only after Argon2) and NV-17
(every rate-limited login wrote an audit row).
"""
import threading

from .conftest import create_account


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def test_short_window_limiter_does_not_prune_long_window_history(monkeypatch):
    from backend.app import security
    clock = _Clock()
    monkeypatch.setattr(security.time, "monotonic", clock)
    with security._rate_limit_lock:
        security._request_attempts.clear()

    for _ in range(30):
        assert security.check_request_rate_limit("ai_chat:u1:h", 30, 300) == 0
    assert security.check_request_rate_limit("ai_chat:u1:h", 30, 300) > 0

    clock.now += 61
    assert security.check_request_rate_limit("ai_chat:u1:h", 30, 300) > 0
    assert security.check_request_rate_limit("data_quality_summary:u1:h", 60, 60) == 0
    assert security.check_request_rate_limit("ai_chat:u1:h", 30, 300) > 0

    clock.now += 240
    assert security.check_request_rate_limit("ai_chat:u1:h", 30, 300) == 0


def test_concurrent_login_burst_is_bounded_per_host(client, monkeypatch):
    from backend.app import config, security
    calls = []
    lock = threading.Lock()
    real_verify = security.verify_password

    def counting_verify(plain, password_hash):
        with lock:
            calls.append(1)
        return real_verify(plain, password_hash)

    monkeypatch.setattr(security, "verify_password", counting_verify)
    create_account("doc.burst", "doctor")
    n = config.LOGIN_MAX_ATTEMPTS + 5
    barrier = threading.Barrier(n)
    statuses = []

    def attempt():
        barrier.wait()
        r = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"})
        with lock:
            statuses.append(r.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) <= config.LOGIN_MAX_ATTEMPTS, (len(calls), statuses)
    assert statuses.count(429) >= n - config.LOGIN_MAX_ATTEMPTS


def test_successful_login_still_works_and_resets(client):
    from backend.app import config
    create_account("doc.ok", "doctor")
    for _ in range(config.LOGIN_MAX_ATTEMPTS - 1):
        assert client.post("/api/auth/login", json={"username": "doc.ok", "password": "wrong-password"}).status_code == 401
    from .conftest import TEST_PASSWORD
    assert client.post("/api/auth/login", json={"username": "doc.ok", "password": TEST_PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "doc.ok", "password": TEST_PASSWORD}).status_code == 200


def test_rate_limited_logins_are_audited_once_per_backoff(client):
    from backend.app import config, db
    for _ in range(config.LOGIN_MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"})
    for _ in range(20):
        assert client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"}).status_code == 429
    count = db.get_connection().execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'login_rate_limited'"
    ).fetchone()[0]
    assert 1 <= count <= 2
