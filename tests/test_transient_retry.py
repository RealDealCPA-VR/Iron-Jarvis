"""Rate-limit resilience for one-shot agent utilities (terminal->workflow etc)."""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import (
    _complete_with_retry,
    _is_transient_provider_error,
    _provider_error_http,
)


class _Flaky:
    """Fails with a 429 n times, then succeeds."""

    def __init__(self, failures: int, error: str = "Error code: 429 - rate_limit_error"):
        self.failures = failures
        self.error = error
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.error)
        return "ok"


@pytest.mark.asyncio
async def test_retries_transient_429_then_succeeds(monkeypatch):
    import iron_jarvis.daemon.app as appmod

    async def _no_sleep(_):  # keep the test instant
        return None

    monkeypatch.setattr(appmod.asyncio, "sleep", _no_sleep)
    flaky = _Flaky(failures=2)
    out = await _complete_with_retry(flaky, system="", messages=[], tools=[])
    assert out == "ok"
    assert flaky.calls == 3


@pytest.mark.asyncio
async def test_non_transient_raises_immediately():
    flaky = _Flaky(failures=5, error="401 invalid_api_key")
    with pytest.raises(RuntimeError):
        await _complete_with_retry(flaky, system="", messages=[], tools=[])
    assert flaky.calls == 1  # no retries for auth errors


@pytest.mark.asyncio
async def test_exhausted_transient_raises(monkeypatch):
    import iron_jarvis.daemon.app as appmod

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(appmod.asyncio, "sleep", _no_sleep)
    flaky = _Flaky(failures=99)
    with pytest.raises(RuntimeError):
        await _complete_with_retry(flaky, system="", messages=[], tools=[])
    assert flaky.calls == 3  # attempts cap


def test_transient_classifier():
    assert _is_transient_provider_error(RuntimeError("Error code: 429 - {'type': 'rate_limit_error'}"))
    assert _is_transient_provider_error(RuntimeError("overloaded_error"))
    assert not _is_transient_provider_error(RuntimeError("model not found"))


def test_http_mapping():
    assert _provider_error_http(RuntimeError("429 rate_limit_error")).status_code == 429
    assert _provider_error_http(RuntimeError("model not found")).status_code == 502


def test_workflow_generate_fails_over_to_another_provider(tmp_path, monkeypatch):
    """A rate-limited provider fails over to the OTHER connected provider."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    import iron_jarvis.daemon.app as appmod

    # Make retries instant.
    _orig_sleep = appmod.asyncio.sleep

    async def _fast(_d):
        await _orig_sleep(0)

    monkeypatch.setattr(appmod.asyncio, "sleep", _fast)

    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform

    class _RateLimited:
        provider, model = "anthropic", "claude-x"

        async def complete(self, *, system, messages, tools):
            raise RuntimeError("Error code: 429 - rate_limit_error")

    class _Works:
        provider, model = "openai", "gpt-ok"

        async def complete(self, *, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse

            return LLMResponse(
                text='{"name":"from-failover","description":"d","steps":'
                '[{"name":"s1","agent":"builder","task":"do it","tool":null}]}',
                tool_calls=[],
                usage={},
            )

    monkeypatch.setattr(
        platform.providers,
        "get",
        lambda p, m=None: _RateLimited() if p == "anthropic" else _Works(),
    )
    monkeypatch.setattr(
        platform.providers, "available", lambda p: p in {"anthropic", "openai"}
    )

    r = client.post(
        "/workflows/generate",
        json={"description": "x", "provider": "anthropic", "model": "claude-x"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "from-failover"
