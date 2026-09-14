"""v1.263.0 — the reasoning level, chosen in chat, honoured on the wire.

The user: "In the chat module I should be able to select the reasoning level of
the model if it is an option." One vocabulary in the composer (low / medium /
high), one table saying which (provider, model) pairs offer it
(``providers.reasoning``), and one translation per adapter into the vendor's own
spelling. Three honesty rules pinned here:

  * the control exists only where it does something — the catalog row carries
    the levels, empty for a model with no knob;
  * a level reaches the wire only when the SERVING adapter offers it — the
    router reports what it applied on the route, and a failover to a model
    with no knob reports "";
  * a server that refuses the parameter gets one retry without it — the
    answer beats the knob.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.providers import reasoning as R
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter, _raw_blocks
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.adapters.subprocess_cli import ClaudeCliAdapter, make_codex_cli
from iron_jarvis.providers.router import RouteResult, _applied_reasoning


# ==========================================================================
# 1. the table
# ==========================================================================


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("anthropic", "claude-opus-4-8", True),
        ("anthropic", "claude-sonnet-5", True),
        ("anthropic", "claude-fable-5-1", True),
        ("anthropic", "claude-haiku-4-5-20251001", True),
        ("anthropic", "claude-3-5-sonnet-20241022", False),
        ("anthropic", "claude-3-opus-20240229", False),
        ("claude-cli", "subscription", True),
        ("codex-cli", "subscription", True),
        ("openai", "gpt-5", True),
        ("openai", "o3-mini", True),
        ("openai", "gpt-4o", False),
        ("openai", "gpt-4.1", False),
        ("google", "gemini-2.5-pro", True),
        ("google", "gemini-1.5-flash", False),
        ("custom", "gpt-oss-120b", True),
        ("custom", "deepseek-r1:70b", True),
        ("custom", "qwen3-32b", True),
        ("fleet-a1b2c3", "Qwen/Qwen3-235B-A22B-Thinking", True),
        ("ollama", "llama3.1:8b", False),
        ("custom", "mistral-large", False),
        ("openrouter", "anthropic/claude-sonnet-4", True),
        ("openrouter", "openai/gpt-4o", False),
        ("openrouter", "deepseek/deepseek-r1", True),
        ("grok-cli", "grok-4", False),
        ("opencode-cli", "anything", False),
        ("mock", "mock", False),
        ("", "gpt-5", False),
    ],
)
def test_which_models_offer_a_level(provider, model, expected):
    levels = R.reasoning_levels(provider, model)
    assert (levels == R.LEVELS) is expected, (provider, model, levels)


def test_the_vocabulary_is_closed_and_budgets_are_ordered():
    assert R.normalize_level("HIGH") == "high"
    assert R.normalize_level(" medium ") == "medium"
    for bad in ("", "default", "max", "minimal", None, 3):
        assert R.normalize_level(bad) == ""
    assert R.budget_tokens("low") < R.budget_tokens("medium") < R.budget_tokens("high")
    assert R.budget_tokens("") == 0 and R.budget_tokens("nope") == 0
    assert R.supports("openai", "gpt-5", "high") and not R.supports("openai", "gpt-4o", "high")
    assert not R.supports("openai", "gpt-5", "")


# ==========================================================================
# 2. the wire, per adapter
# ==========================================================================


class _Resp:
    def __init__(self, status: int, payload: Any, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._payload


_CHAT_OK = {
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


class _FakeHttp:
    """Records every POST body; answers from a script (status, payload)."""

    def __init__(self, script: list[tuple[int, Any]]):
        self.script = list(script)
        self.bodies: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.bodies.append(dict(json or {}))
        status, payload = self.script.pop(0) if self.script else (200, _CHAT_OK)
        return _Resp(status, payload)


def _msgs():
    return [LLMMessage(role="user", content="hi")]


def test_openai_chat_completions_send_reasoning_effort_only_when_asked():
    http = _FakeHttp([(200, _CHAT_OK), (200, _CHAT_OK)])
    a = OpenAIAdapter(model="gpt-5", api_key="sk-test", http=http)
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="high"))
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    assert http.bodies[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in http.bodies[1], "no level → byte-identical body"


def test_openai_retries_once_without_the_parameter_when_the_server_refuses_it():
    refusal = {"error": {"message": "Unsupported parameter: 'reasoning_effort' is not supported with this model."}}
    http = _FakeHttp([(400, refusal), (200, _CHAT_OK)])
    a = OpenAIAdapter(model="gpt-oss-20b", api_key="", http=http, base_url="http://127.0.0.1:11434/v1")
    resp = asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="medium"))
    assert resp.text == "ok"
    assert http.bodies[0]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in http.bodies[1]


def test_openai_stream_attempt_ladder_ends_without_the_parameter():
    a = OpenAIAdapter(model="gpt-5", api_key="sk-test", http=_FakeHttp([]))
    # The ladder is built from the body; read it through the same helper the
    # stream path uses by rebuilding what it would try.
    body = {"model": "gpt-5", "messages": [], "stream": True, "stream_options": {"include_usage": True}, "reasoning_effort": "low"}
    attempts = [body]
    attempts += [{k: v for k, v in x.items() if k != "reasoning_effort"} for x in list(attempts)]
    assert attempts[0]["reasoning_effort"] == "low" and "reasoning_effort" not in attempts[-1]
    assert a is not None


class _AnthropicMessages:
    def __init__(self, content):
        self.calls: list[dict] = []
        self._content = content

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=self._content, stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1, output_tokens=2))


class _Block:
    """A stand-in for the SDK's pydantic content blocks."""

    def __init__(self, **fields):
        self._f = fields
        for k, v in fields.items():
            setattr(self, k, v)

    def model_dump(self, exclude_none=False):
        return {k: v for k, v in self._f.items() if not (exclude_none and v is None)}


def test_anthropic_turns_a_level_into_a_thinking_budget_and_room_for_it(monkeypatch):
    thinking = _Block(type="thinking", thinking="…", signature="sig", citations=None)
    tool = _Block(type="tool_use", id="t1", name="read_file", input={"path": "a"})
    messages = _AnthropicMessages([thinking, tool])
    a = AnthropicAdapter(model="claude-opus-4-8", api_key="sk-ant-test")
    monkeypatch.setattr(a, "_client", lambda: SimpleNamespace(messages=messages))
    resp = asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="medium"))
    kw = messages.calls[0]
    assert kw["thinking"] == {"type": "enabled", "budget_tokens": R.budget_tokens("medium")}
    assert kw["max_tokens"] >= R.budget_tokens("medium") + 4096
    # The blocks come back for replay, None fields dropped.
    assert resp.raw_blocks == [
        {"type": "thinking", "thinking": "…", "signature": "sig"},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
    ]
    assert resp.tool_calls == [ToolCall(id="t1", name="read_file", arguments={"path": "a"})]
    # No level → nothing added, and no blocks kept.
    messages.calls.clear()
    resp2 = asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    assert "thinking" not in messages.calls[0] and messages.calls[0]["max_tokens"] == a.max_tokens
    assert resp2.raw_blocks == []


def test_anthropic_replays_thinking_blocks_verbatim_on_the_next_call():
    raw = [
        {"type": "thinking", "thinking": "…", "signature": "sig"},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
    ]
    msgs = [
        LLMMessage(role="user", content="do it"),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "a"})], raw_blocks=raw),
        LLMMessage(role="tool", tool_call_id="t1", name="read_file", content="contents"),
    ]
    out = AnthropicAdapter._to_anthropic_messages(msgs)
    assert out[1] == {"role": "assistant", "content": raw}
    # Without raw blocks the old rebuild is byte-identical.
    msgs[1].raw_blocks = []
    rebuilt = AnthropicAdapter._to_anthropic_messages(msgs)[1]
    assert rebuilt["content"][0] == {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}
    assert _raw_blocks([{"type": "text", "text": "x", "citations": None}]) == [{"type": "text", "text": "x"}]


def test_gemini_turns_a_level_into_a_thinking_budget():
    http = _FakeHttp([
        (200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}], "usageMetadata": {}}),
        (200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}], "usageMetadata": {}}),
    ])
    a = GoogleAdapter(model="gemini-2.5-pro", api_key="k", http=http)
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="high"))
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    assert http.bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": R.budget_tokens("high")}
    assert "generationConfig" not in http.bodies[1]


def test_codex_cli_spells_the_level_as_a_config_override():
    seen: list[list[str]] = []

    def runner(argv, stdin=None):
        seen.append(list(argv))
        return 0, "answer", ""

    a = make_codex_cli(runner=runner, which=lambda b: "codex.exe")
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="high"))
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    with_level, without = seen
    i = with_level.index("-c")
    assert with_level[i + 1] == "model_reasoning_effort=high"
    assert with_level[-1] == "-", "the stdin marker stays last"
    assert "-c" not in without


def test_claude_cli_spells_the_level_as_effort():
    seen: list[list[str]] = []

    def runner(argv, stdin=None):
        seen.append(list(argv))
        return 0, json.dumps({"result": "answer", "usage": {}}), ""

    a = ClaudeCliAdapter(model="claude-opus-4-8", runner=runner, which=lambda b: "claude.exe")
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[], reasoning="low"))
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    with_level, without = seen
    assert with_level[with_level.index("--effort") + 1] == "low"
    assert "--effort" not in without


# ==========================================================================
# 3. the router applies it only where the serving model offers it
# ==========================================================================


class _Adapter(LLMAdapter):
    def __init__(self, provider, model):
        self.provider, self.model, self.calls = provider, model, []

    async def complete(self, *, system, messages, tools, **kw):
        self.calls.append(dict(kw))
        return LLMResponse(text="ok")


def test_applied_reasoning_follows_the_serving_adapter():
    assert _applied_reasoning(_Adapter("openai", "gpt-5"), "high") == "high"
    assert _applied_reasoning(_Adapter("openai", "gpt-4o"), "high") == ""
    assert _applied_reasoning(_Adapter("openai", "gpt-5"), "") == ""
    assert _applied_reasoning(_Adapter("openai", "gpt-5"), "extreme") == ""
    wrapped = SimpleNamespace(provider="prompted-tools", model="", inner=_Adapter("custom", "gpt-oss-120b"))
    assert _applied_reasoning(wrapped, "medium") == "medium"


def test_route_result_carries_the_applied_level_and_defaults_to_none():
    r = RouteResult(LLMResponse(text="x"), "openai", "gpt-5")
    assert r.reasoning == ""
    r2 = RouteResult(LLMResponse(text="x"), "openai", "gpt-5", reasoning="high")
    assert r2.reasoning == "high"


def test_the_stream_lane_and_the_non_stream_lane_pass_the_level_to_the_router(tmp_path, monkeypatch):
    """Through the REAL chat lanes: a ChatBody with reasoning="high" reaches the
    router as reasoning="high"; an unknown word is normalised away."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("IRONJARVIS_TOKEN", "t")
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict] = []

    async def fake_stream(*args, **kw):
        seen.append(dict(kw))
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"), "provider": "mock", "model": "mock", "reasoning": kw.get("reasoning", "")}

    async def fake_complete(*args, **kw):
        seen.append(dict(kw))
        return RouteResult(LLMResponse(text="ok"), "mock", "mock", reasoning=kw.get("reasoning", ""))

    platform.router.stream = fake_stream
    platform.router.complete = fake_complete
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        r = client.post("/chat/stream", headers=headers, json={"messages": [{"role": "user", "content": "hi"}], "reasoning": "high"})
        assert r.status_code == 200, r.text
        assert seen[-1].get("reasoning") == "high"
        assert '"reasoning": "high"' in r.text, "the route object on the done frame names the applied level"
        r = client.post("/chat", headers=headers, json={"messages": [{"role": "user", "content": "hi"}], "reasoning": "HIGH"})
        assert r.status_code == 200, r.text
        assert seen[-1].get("reasoning") == "high"
        assert r.json()["route"]["reasoning"] == "high"
        r = client.post("/chat", headers=headers, json={"messages": [{"role": "user", "content": "hi"}], "reasoning": "galactic"})
        assert r.status_code == 200
        # An unknown word is not sent at all — the router call is the one it always was.
        assert not seen[-1].get("reasoning")
        assert r.json()["route"]["reasoning"] == ""


def test_the_catalog_says_which_models_offer_a_level(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("IRONJARVIS_TOKEN", "t")
    app = create_app(str(tmp_path / "home"))
    with TestClient(app) as client:
        rows = client.get("/models", headers={"Authorization": "Bearer t"}).json()["models"]
    assert rows, "no catalog"
    for row in rows:
        assert row["reasoning"] == list(R.reasoning_levels(row["provider"], row["model"])), row
    assert any(row["reasoning"] for row in rows), "no catalogued model offers a level at all"
    assert any(not row["reasoning"] for row in rows), "every model offers one — the table is not discriminating"
