"""v1.448.0 (Wave A, A3) — Build › Code's Session tab says where the tool's
model answers: ``where = {on_this_network, words}`` on
``GET /build/app/sessions/{id}/overview`` (``hosted/panes.session_view``).

Claude Code, Codex, Grok and Antigravity run their models on the vendor's
service — ``cloud`` whatever the pick. Pi and OpenCode name a
``provider/model`` of their own: a cloud provider's prefix is ``cloud``, a
model server of the user's (ollama, LM Studio, llama.cpp) is judged by Iron
Jarvis's own address for that server through
``providers.egress.stays_on_this_network``, and anything the daemon cannot
place is ``None`` ("not known") — never a guess.
"""

from __future__ import annotations

import types

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import panes as PN
from tests._build_app_helpers_v1409 import client, new_session, stop_all


def _row(harness: str, model: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(folder="", harness=harness, model=model)


def test_the_four_vendor_tools_are_the_cloud_whatever_the_model():
    for harness in ("claude-code", "codex", "grok", "antigravity"):
        assert PN.where_view(_row(harness, "anything")) == {"on_this_network": False, "words": "cloud"}, harness
        # A model that LOOKS local changes nothing: the tool's vendor runs it.
        assert PN.where_view(_row(harness, "ollama/llama3"))["on_this_network"] is False


def test_pi_and_opencode_on_a_cloud_provider_read_cloud():
    assert PN.where_view(_row("pi", "anthropic/claude-sonnet-5")) == {"on_this_network": False, "words": "cloud"}
    assert PN.where_view(_row("pi", "openai-codex/gpt-6-luna"))["on_this_network"] is False
    assert PN.where_view(_row("opencode", "opencode/glm-5.3-flash-free"))["on_this_network"] is False
    assert PN.where_view(_row("opencode", "OpenRouter/some-model"))["on_this_network"] is False


def test_an_unknown_provider_no_model_or_no_platform_is_not_known():
    not_known = {"on_this_network": None, "words": ""}
    assert PN.where_view(_row("pi", "")) == not_known
    assert PN.where_view(_row("pi", "gpt-6")) == not_known  # no provider part
    assert PN.where_view(_row("opencode", "somebody/their-model")) == not_known
    # A model server of the user's with NO platform to ask: not known, never
    # a guess that it is local.
    assert PN.where_view(_row("pi", "ollama/llama3"), None) == not_known
    assert PN.where_view(None) == not_known


def test_a_model_server_of_the_users_is_judged_by_iron_jarviss_own_address():
    here = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="http://127.0.0.1:11434"))
    assert PN.where_view(_row("pi", "ollama/llama3"), here) == {"on_this_network": True, "words": "on this network"}
    assert PN.where_view(_row("opencode", "ollama/qwen3"), here)["on_this_network"] is True
    # An Ollama whose address is NOT on this network reads cloud (the chat
    # receipt's own rule). A3 review: NO address at all is NOT KNOWN — Iron
    # Jarvis's silence says nothing about where Pi's Ollama answers (moved on
    # purpose: it read "cloud" for a fresh install with Pi on a local Ollama).
    away = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="https://ollama.example.com"))
    assert PN.where_view(_row("pi", "ollama/llama3"), away) == {"on_this_network": False, "words": "cloud"}
    none = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url=""))
    assert PN.where_view(_row("pi", "ollama/llama3"), none) == {"on_this_network": None, "words": ""}


def test_where_view_never_raises():
    class Bad:
        @property
        def harness(self):
            raise RuntimeError("boom")

    assert PN.where_view(Bad()) == {"on_this_network": None, "words": ""}
    broken = types.SimpleNamespace(providers=object(), config=types.SimpleNamespace(ollama_base_url=object()))
    out = PN.where_view(_row("pi", "ollama/x"), broken)
    assert out["on_this_network"] in (True, False, None)


def test_session_view_carries_where(tmp_path):
    view = PN.session_view(_row("codex"), [], None, False)
    assert view["where"] == {"on_this_network": False, "words": "cloud"}
    view = PN.session_view(_row("pi", "nobody/x"), [], None, False, platform=None)
    assert view["where"] == {"on_this_network": None, "words": ""}


@pytest.mark.asyncio
async def test_the_overview_route_hands_the_platform_to_the_reading(tmp_path, monkeypatch):
    """A Pi session on the user's own Ollama is judged by Iron Jarvis's
    address for it — which the route can only do by handing the platform
    over; a route that forgets it would answer "not known" for every such
    session."""
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    seen: list = []
    real = PN.where_view

    def spy(row, platform=None):
        seen.append(platform)
        return real(row, platform)

    monkeypatch.setattr(PN, "where_view", spy)
    async with client(app) as c:
        sid = (await new_session(c, folder, harness="codex"))["id"]
        await c.get(f"/build/app/sessions/{sid}/overview")
    await stop_all(app)
    assert seen and seen[-1] is app.state.platform


@pytest.mark.asyncio
async def test_the_overview_route_says_where_a_codex_session_runs(tmp_path):
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    async with client(app) as c:
        sid = (await new_session(c, folder, harness="codex"))["id"]
        body = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
    await stop_all(app)
    assert body["where"] == {"on_this_network": False, "words": "cloud"}
