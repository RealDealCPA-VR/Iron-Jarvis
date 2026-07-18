"""Model discovery must track endpoint RE-POINTS (live-hit 2026-07-18).

The cache was keyed by provider name only, so saving a different custom/local
endpoint URL kept serving the PREVIOUS endpoint's model list for the full TTL
— a freshly added endpoint showed stale models in every picker."""

from __future__ import annotations

from iron_jarvis.providers import discovery


def test_repointed_custom_endpoint_misses_the_old_cache(monkeypatch):
    discovery.clear_cache()
    calls: list[str] = []

    def fake_models(base_url, key):
        calls.append(base_url)
        return ["alpha-model"] if "8081" in base_url else ["beta-model"]

    monkeypatch.setattr(discovery, "_openai_compatible_models", fake_models)
    a = discovery.discover_models("custom", lambda: "", base_url="http://h:8081/v1")
    assert a == ["alpha-model"]
    # Re-point to a DIFFERENT server: must probe it, not serve the cache.
    b = discovery.discover_models("custom", lambda: "", base_url="http://h:9090/v1")
    assert b == ["beta-model"]
    assert calls == ["http://h:8081/v1", "http://h:9090/v1"]
    # Same URL again inside the TTL: served from cache, no re-probe.
    assert discovery.discover_models("custom", lambda: "", base_url="http://h:8081/v1") == ["alpha-model"]
    assert len(calls) == 2
    discovery.clear_cache()


def test_ollama_repoint_also_keyed_by_url(monkeypatch):
    discovery.clear_cache()
    monkeypatch.setattr(discovery, "_ollama_models", lambda base: [f"m-{base[-4:]}"])
    assert discovery.discover_models("ollama", lambda: "", base_url="http://a:1111") == ["m-1111"]
    assert discovery.discover_models("ollama", lambda: "", base_url="http://b:2222") == ["m-2222"]
    discovery.clear_cache()
