"""Pre-production hardening, round 3: no PHI or API key over plain HTTP.

Regression test for preprod-run-3 candidate
backend/app/ai/provider.py:OpenAICompatibleAIProvider:cleartext-non-loopback-base-url:
the provider accepted any scheme, so the bearer key and scoped patient data
could be sent in cleartext to a remote inference host.
"""
import pytest

from backend.app.ai import provider as ai_provider


def _build(base_url, **kw):
    return ai_provider.OpenAICompatibleAIProvider(base_url=base_url, model="m", **kw)


@pytest.mark.parametrize("url", [
    "http://internal-ai-host:1234/v1",
    "http://10.1.2.3:1234/v1",
    "http://ai.example.org/v1",
])
def test_remote_plaintext_provider_is_refused(url):
    with pytest.raises(ai_provider.AIProviderUnavailable):
        _build(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://[::1]:1234/v1",
    "https://internal-ai-host:8443/v1",
])
def test_loopback_and_https_providers_are_accepted(url):
    assert _build(url).base_url == url.rstrip("/")


def test_explicit_override_allows_plaintext(monkeypatch):
    monkeypatch.setattr(ai_provider.config, "AI_ALLOW_INSECURE_TRANSPORT", True)
    # WP-G deliberately retires the insecure override; the historical test ID remains.
    with pytest.raises(ai_provider.AIProviderUnavailable):
        _build("http://internal-ai-host:1234/v1")


def test_unknown_scheme_is_refused():
    with pytest.raises(ai_provider.AIProviderUnavailable):
        _build("file:///etc/passwd")
