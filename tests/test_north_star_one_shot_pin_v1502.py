"""North-star 2.14 (v1.502.0): one-shot helpers honour Strict model pin.

``daemon/app._one_shot_complete`` (terminal assist, the workflow builder,
living documents, skill drafts, compaction, ...) calls the adapter directly
and then walks ``_failover_candidates``. It read ``strict_model_pin``
nowhere, so a caller that NAMED a provider was answered by another one with
the pin on; and a move that did happen published no ``provider.failover``,
so the Activity page and the phone never heard of it.

Driven through the real ``create_app`` deps (the layer that moves the call);
only the adapters are fakes, and the retry ladder is cut to one try so no
test waits on its backoff.
"""

from __future__ import annotations

import asyncio
import functools

import pytest
from fastapi import HTTPException

import iron_jarvis.daemon.app as app_mod
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse, ProviderError


class _Ad(LLMAdapter):
    def __init__(self, provider, *, fail=None):
        self.provider, self.model = provider, "m"
        self.fail = fail
        self.calls = 0

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})


@pytest.fixture()
def app(tmp_path, monkeypatch):
    a = create_app(str(tmp_path))
    monkeypatch.setattr(
        app_mod, "_complete_with_retry",
        functools.partial(app_mod._complete_with_retry, attempts=1),
    )
    return a


def _two(app, monkeypatch, *, default="codex-cli"):
    """``claude-cli`` busy (a transient 529), ``codex-cli`` answering; both
    subscriptions (never pay per use), both available."""
    p = app.state.platform
    busy = _Ad("claude-cli", fail=ProviderError("overloaded", status_code=529))
    other = _Ad("codex-cli")
    p.providers.register("claude-cli", lambda m=None: busy)
    p.providers.register("codex-cli", lambda m=None: other)
    real = p.providers.available
    monkeypatch.setattr(
        p.providers, "available",
        lambda n: n in ("claude-cli", "codex-cli") or real(n),
    )
    monkeypatch.setattr(p.config, "default_provider", default)
    monkeypatch.setattr(p.config, "default_model", "")
    return busy, other


_MSGS = [LLMMessage(role="user", content="x")]


def _failovers(app):
    return [e for e in app.state.platform.event_bus.history if e.type == EventType.PROVIDER_FAILOVER]


def test_with_the_pin_on_a_named_provider_is_never_answered_by_another(app, monkeypatch):
    busy, other = _two(app, monkeypatch)
    monkeypatch.setattr(app.state.platform.config, "strict_model_pin", True)
    d = app.state.d

    with pytest.raises(HTTPException) as caught:
        asyncio.run(d._one_shot_complete("claude-cli", busy, system="s", messages=_MSGS))
    assert busy.calls == 1 and other.calls == 0
    detail = str(caught.value.detail)
    assert "Claude" in detail and "Strict model pin" in detail, detail
    assert _failovers(app) == []


def test_a_named_provider_with_the_pin_off_moves_and_the_move_is_published(app, monkeypatch):
    busy, other = _two(app, monkeypatch)
    d = app.state.d

    resp, used, _m = asyncio.run(d._one_shot_complete("claude-cli", busy, system="s", messages=_MSGS))
    assert used == "codex-cli" and other.calls == 1
    moves = _failovers(app)
    assert len(moves) == 1, "a one-shot that moved published nothing"
    assert moves[0].payload["from"] == "claude-cli" and moves[0].payload["to"] == "codex-cli"
    assert moves[0].payload["reason"]


def test_the_default_route_still_moves_under_the_pin_as_the_router_does(app, monkeypatch):
    """The pin is about a provider the CALLER named; a one-shot on the
    configured default (nobody named it) keeps failing over, and says so."""
    busy, other = _two(app, monkeypatch, default="claude-cli")
    monkeypatch.setattr(app.state.platform.config, "strict_model_pin", True)
    d = app.state.d

    _resp, used, _m = asyncio.run(d._one_shot_complete("claude-cli", busy, system="s", messages=_MSGS))
    assert used == "codex-cli" and len(_failovers(app)) == 1


def test_a_caller_can_say_it_named_the_provider_even_when_it_is_the_default(app, monkeypatch):
    busy, other = _two(app, monkeypatch, default="claude-cli")
    monkeypatch.setattr(app.state.platform.config, "strict_model_pin", True)
    d = app.state.d

    with pytest.raises(HTTPException):
        asyncio.run(d._one_shot_complete(
            "claude-cli", busy, system="s", messages=_MSGS, named=True,
        ))
    assert other.calls == 0
