"""Pre-production hardening, round 3: AI chat cannot occupy every worker.

Regression test for preprod-run-3 candidate
backend/app/routes/ai.py:chat_endpoint:unbounded-concurrent-provider-wait: the
rate limiter counted request starts, not requests in flight, so slow provider
calls could hold the shared sync worker pool, and status?refresh=true had no
limit at all.
"""
import threading


def _busy(monkeypatch):
    from backend.app.routes import ai as ai_routes
    sem = threading.BoundedSemaphore(1)
    sem.acquire()
    monkeypatch.setattr(ai_routes, "_ai_slots", sem)
    return ai_routes


def test_chat_is_refused_when_every_provider_slot_is_busy(auth_client, monkeypatch):
    ai_routes = _busy(monkeypatch)
    r = auth_client.post("/api/ai/chat", json={"message": "привет"}, headers=auth_client.csrf)
    assert r.status_code == 503
    assert r.json()["detail"] == ai_routes._AI_BUSY


def test_status_refresh_is_refused_while_cached_status_still_answers(auth_client, monkeypatch):
    _busy(monkeypatch)
    assert auth_client.get("/api/ai/status", params={"refresh": True}).status_code == 503
    assert auth_client.get("/api/ai/status").status_code == 200


def test_slots_are_released_after_each_request(auth_client):
    from backend.app.routes import ai as ai_routes
    for _ in range(3):
        r = auth_client.post("/api/ai/chat", json={"message": "привет"}, headers=auth_client.csrf)
        assert r.json().get("detail") != ai_routes._AI_BUSY
    assert ai_routes._ai_slots.acquire(blocking=False)
    ai_routes._ai_slots.release()


def test_status_refresh_is_rate_limited(auth_client):
    from backend.app import config, security
    security._request_attempts.clear()
    codes = [auth_client.get("/api/ai/status", params={"refresh": True}).status_code
             for _ in range(config.AI_RATE_LIMIT_REQUESTS + 2)]
    assert 429 in codes
