"""v1.125.0 — an explicitly picked text-only CLI serves its own chat turns.

Live-hit 2026-07-31: picking the Codex subscription in chat ALWAYS came back
served by a different provider. Cause: `codex exec` is a text-only completer
(capabilities tool_use=False) and every chat turn carries the exit-tool specs
since the one-surface merge — so capability routing rerouted codex-cli on
every single turn. The honest fix: an EXPLICIT text-only pick serves the turn
text-only (no armed tools, no exit tools) on the chosen CLI; default/auto
routes keep full capability routing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


class _FakeCodex:
    provider = "codex-cli"
    model = "subscription"

    def __init__(self) -> None:
        self.seen_tools: list | None = None

    def capabilities(self):
        return {
            "provider": "codex-cli",
            "model": "subscription",
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.seen_tools = tools
        return LLMResponse(text="codex says hi")


def _wire_fake_codex(client, monkeypatch) -> _FakeCodex:
    platform = client.app.state.platform
    fake = _FakeCodex()
    real_get = platform.providers.get
    monkeypatch.setattr(
        platform.providers,
        "get",
        lambda p, m=None: fake if p == "codex-cli" else real_get(p, m),
    )
    real_avail = platform.providers.available
    monkeypatch.setattr(
        platform.providers,
        "available",
        lambda n: True if n == "codex-cli" else real_avail(n),
    )
    return fake


def test_explicit_text_only_pick_serves_that_cli(client, monkeypatch):
    fake = _wire_fake_codex(client, monkeypatch)
    out = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "provider": "codex-cli"},
    ).json()
    assert out["provider"] == "codex-cli"  # NOT rerouted
    assert out["reply"] == "codex says hi"
    # The text-only pass: no armed tools AND no exit-tool specs were sent —
    # a text-only adapter offered tools would silently stall the loop.
    assert fake.seen_tools == []


def test_text_only_pick_notes_explicitly_armed_tools(client, monkeypatch):
    _wire_fake_codex(client, monkeypatch)
    out = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "list my folder"}],
            "provider": "codex-cli",
            "tools": ["list_folder"],
        },
    ).json()
    assert out["provider"] == "codex-cli"
    assert "can't run tools" in out["reply"]  # honest, not silent


def test_default_route_keeps_the_exit_tools(client, monkeypatch):
    # No explicit pick: the exit tools still ride (the one-surface contract) —
    # the text-only pass must never leak onto default/auto routes.
    seen = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def complete(*, system, messages, tools):
            seen["tools"] = [t.get("name") for t in tools]
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = complete
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert "escalate_to_agent" in seen["tools"]
    assert "workflow_draft" in seen["tools"]
