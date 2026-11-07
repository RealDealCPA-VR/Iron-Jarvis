"""A3 review (v1.448.0, Wave A) — what the builder's pins did not cover, each
red first on cd15d368:

* ``hosted/panes.where_view``: a Pi / OpenCode model on an Ollama Iron Jarvis
  holds NO address for read "cloud" (a fresh install with Pi on a local
  Ollama); LM Studio and llama.cpp ALWAYS read "cloud" (``egress`` knows no
  such provider, so it failed closed) — both are "not known" now, never a
  guess; Pi's own ``google-antigravity`` and OpenCode's ``zai-coding-plan``
  prefixes are the cloud; a STOPPED session's view carries ``where`` too.
* ``settings/tools.value_label``: a fleet node as ``default_provider`` reads
  as the label the user gave it when the provider manager is handed over,
  and the change tool hands it over.
* MIRROR NOTE: the POST lane's ``config_cards`` carry ``old_label`` /
  ``new_label`` exactly as the stream lane's do.
"""

from __future__ import annotations

import json
import types

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import panes as PN
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.settings import schema
from iron_jarvis.settings import tools as ST
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

NOT_KNOWN = {"on_this_network": None, "words": ""}
CLOUD = {"on_this_network": False, "words": "cloud"}


def _row(harness: str, model: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(folder="", harness=harness, model=model)


# ------------------------------------------------------------------ where


def test_an_ollama_iron_jarvis_has_no_address_for_is_not_known_never_cloud():
    """Iron Jarvis's silence about Ollama says nothing about where Pi's or
    OpenCode's Ollama answers — the old code said "cloud"."""
    nothing = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url=""))
    assert PN.where_view(_row("pi", "ollama/llama3"), nothing) == NOT_KNOWN
    assert PN.where_view(_row("opencode", "ollama/qwen3"), nothing) == NOT_KNOWN
    # A config with no such attribute at all (an older or partial object).
    assert PN.where_view(_row("pi", "ollama/llama3"), types.SimpleNamespace(providers=None, config=None)) == NOT_KNOWN
    # With an address, the receipt's rule decides, as before.
    here = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="http://127.0.0.1:11434"))
    assert PN.where_view(_row("pi", "ollama/llama3"), here)["on_this_network"] is True


@pytest.mark.parametrize("prefix", ["lmstudio", "lm-studio", "llamacpp", "llama.cpp", "llama-cpp", "LMStudio"])
def test_lm_studio_and_llama_cpp_are_not_known_never_cloud(prefix):
    """Iron Jarvis holds no address for these (they are detected, never
    configured), so egress read them as cloud — a false label on a program
    that only ever runs on the user's own machine or network."""
    here = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="http://127.0.0.1:11434"))
    assert PN.where_view(_row("pi", f"{prefix}/qwen3-8b"), here) == NOT_KNOWN
    assert PN.where_view(_row("opencode", f"{prefix}/qwen3-8b"), None) == NOT_KNOWN


def test_the_tools_other_built_in_cloud_providers_read_cloud():
    for harness, model in (
        ("pi", "google-antigravity/gemini-3-pro"),
        ("pi", "google-gemini-cli/gemini-3-flash"),
        ("pi", "github-copilot/gpt-6"),
        ("pi", "kimi-coding/k2"),
        ("opencode", "zai-coding-plan/glm-5"),
        ("opencode", "zhipuai-coding-plan/glm-5"),
        ("opencode", "vercel/v0-2"),
        ("opencode", "llama/llama-4-scout"),  # Meta's hosted API, not llama.cpp
    ):
        assert PN.where_view(_row(harness, model)) == CLOUD, model


def test_a_stopped_session_and_the_old_call_shape_still_answer_where():
    # running=False, no pid: the view still says where a Codex session runs.
    view = PN.session_view(_row("codex"), [], None, False, None)
    assert view["where"] == CLOUD
    # The old four-argument call (a test double that predates `platform`).
    assert PN.session_view(_row("claude-code"), [], None, False)["where"] == CLOUD


# ------------------------------------------------------------ value_label


class _Node:
    def __init__(self, label: str) -> None:
        self.node = types.SimpleNamespace(label=label)


class _Manager:
    def get(self, p: str):
        if p == "fleet-dgx":
            return _Node("DGX Spark")
        raise KeyError(p)


class _BadManager:
    def get(self, p):
        raise RuntimeError("boom")


def test_a_fleet_node_as_the_default_provider_reads_as_its_label():
    row = schema.get("default_provider")
    assert ST.value_label(row, "fleet-dgx", _Manager()) == "DGX Spark"
    # No manager: the id, never a raise (the old answer).
    assert ST.value_label(row, "fleet-dgx") == "fleet-dgx"
    # A manager that knows nothing of it: the id.
    assert ST.value_label(row, "fleet-other", _Manager()) == "fleet-other"
    # A manager that RAISES on every read: still the id, never an error.
    assert ST.value_label(row, "fleet-dgx", _BadManager()) == "fleet-dgx"
    assert ST.value_label(row, "claude-cli", _BadManager()) == "Claude Code"


@pytest.mark.asyncio
async def test_the_change_tool_hands_the_platforms_provider_manager_to_the_label(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.router.stream = _calls_stream([("config_set", {"key": "code_editor", "value": "vscode"})])
    seen: list = []
    real = ST.value_label

    def spy(d, value, manager=None):
        seen.append(manager)
        return real(d, value, manager)

    monkeypatch.setattr(ST, "value_label", spy)

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("open files in VS Code from now on"), never)
    (card,) = _done(frames)["config_cards"]
    assert card["new_label"] == "VS Code"
    assert seen and all(m is platform.providers for m in seen)


# ------------------------------------------------------------- POST lane


def _calls_complete(specs):
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, **kw):
        i = rounds["n"]
        if i < len(specs):
            rounds["n"] += 1
            name, args = specs[i]
            calls = [ToolCall(id=f"c{i}", name=name, arguments=args)]
            return RouteResult(LLMResponse(text="", tool_calls=calls), "mock", "mock")
        return RouteResult(LLMResponse(text="Done."), "mock", "mock")

    return fake_complete


@pytest.mark.asyncio
async def test_the_post_lane_carries_the_labels_like_the_stream_lane(tmp_path):
    """MIRROR NOTE: both lanes go through the one ``config_cards_from_result``;
    the POST lane's answer must carry the names too."""
    app = create_app(str(tmp_path))
    app.state.platform.router.complete = _calls_complete([("config_set", {"key": "code_editor", "value": "vscode"})])
    status, raw = await _asgi_post(app, "/chat", _body("open files in VS Code from now on"))
    assert status == 200, raw
    body = json.loads(raw)
    (card,) = body["config_cards"]
    assert card["key"] == "code_editor" and card["old"] == "auto" and card["new"] == "vscode"
    assert card["old_label"] == "Automatic" and card["new_label"] == "VS Code"
    assert app.state.platform.config.code_editor == "vscode"


@pytest.mark.asyncio
async def test_the_post_lane_gives_a_free_text_row_no_label_keys(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.complete = _calls_complete([("config_set", {"key": "default_model", "value": "qwen3"})])
    status, raw = await _asgi_post(app, "/chat", _body("use the model qwen3 by default"))
    assert status == 200, raw
    (card,) = json.loads(raw)["config_cards"]
    assert card["new"] == "qwen3" and "old_label" not in card and "new_label" not in card
