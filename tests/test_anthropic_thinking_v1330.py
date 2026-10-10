"""v1.330.0 - a picked reasoning level reaches the newer Claude models in the
spelling they accept.

Anthropic's docs (read 2026-10-10, platform.claude.com/docs/en/build-with-claude/
extended-thinking): "Claude 4.7 and later models do not support it [thinking
type "enabled" with budget_tokens] and reject requests that use it, returning a
400 error. On Claude 4.5 and earlier models that support thinking, extended
thinking is the only available thinking mode." The mapping: "remove
`budget_tokens`, set `thinking: {type: "adaptive"}`, and control reasoning
depth with `output_config: {effort: ...}`".

Offline: a recorded expectation of the exact request body per family, for
complete() AND stream() (the chat page uses the stream). The older family's
body is byte-identical to v1.263.0's.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from iron_jarvis.providers import reasoning as R
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage


class _Messages:
    """Records every kwarg the adapter hands the SDK, both entry points."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def _msg(self):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    async def create(self, **kw):
        self.calls.append(("create", kw))
        return self._msg()

    def stream(self, **kw):
        self.calls.append(("stream", kw))
        outer = self

        class _S:
            def __aiter__(self):
                async def gen():
                    if False:  # pragma: no cover - no events needed
                        yield None

                return gen()

            async def get_final_message(self):
                return outer._msg()

        @asynccontextmanager
        async def cm():
            yield _S()

        return cm()


def _adapter(monkeypatch, model: str) -> tuple[AnthropicAdapter, _Messages]:
    msgs = _Messages()
    a = AnthropicAdapter(model=model, api_key="sk-ant-test")
    monkeypatch.setattr(a, "_client", lambda: SimpleNamespace(messages=msgs))
    return a, msgs


def _user() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="hi")]


async def _both(a: AnthropicAdapter, level: str) -> None:
    await a.complete(system="", messages=_user(), tools=[], reasoning=level)
    async for _ in a.stream(system="", messages=_user(), tools=[], reasoning=level):
        pass


def _thinking_part(kw: dict) -> dict:
    """The part of the request a reasoning level decides."""
    return {k: kw[k] for k in ("thinking", "output_config", "max_tokens") if k in kw}


# The recorded expectations. max_tokens is the v1.263.0 room in BOTH families:
# max(4096, budget + 4096), so a level never shrinks the answer's room.
ADAPTIVE_BODY = {
    "low": {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "low"},
        "max_tokens": 6144,
    },
    "medium": {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "medium"},
        "max_tokens": 12288,
    },
    "high": {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "high"},
        "max_tokens": 28672,
    },
}
BUDGET_BODY = {
    "low": {"thinking": {"type": "enabled", "budget_tokens": 2048}, "max_tokens": 6144},
    "medium": {"thinking": {"type": "enabled", "budget_tokens": 8192}, "max_tokens": 12288},
    "high": {"thinking": {"type": "enabled", "budget_tokens": 24576}, "max_tokens": 28672},
}

#: Claude 4.6 and later: the docs' per-model table lists "adaptive" for each.
ADAPTIVE_MODELS = [
    "claude-opus-4-8",
    "claude-opus-4-8[1m]",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-mythos-5",
    "claude-mythos-5-1",
    "claude-mythos-preview",
    # A Claude model newer than this table: every model since 4.7 rejects
    # a budget, so the adaptive spelling is the one that can work.
    "claude-opus-6",
]
#: Claude 4.5 and earlier: "Extended only" (and 3.7 Sonnet, the first).
BUDGET_MODELS = [
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-5",
    "claude-opus-4-5-20251101",
    "claude-opus-4-1",
    "claude-opus-4-1-20250805",
    "claude-opus-4",
    "claude-opus-4-0",
    "claude-sonnet-4",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-0",
    "claude-3-7-sonnet",
    "claude-3-7-sonnet-20250219",
    "claude-3-7-sonnet-latest",
]


@pytest.mark.parametrize("model", ADAPTIVE_MODELS)
@pytest.mark.parametrize("level", list(R.LEVELS))
def test_newer_claude_gets_adaptive_thinking_and_effort(monkeypatch, model, level):
    a, msgs = _adapter(monkeypatch, model)
    asyncio.run(_both(a, level))
    assert [name for name, _ in msgs.calls] == ["create", "stream"]
    for name, kw in msgs.calls:
        assert _thinking_part(kw) == ADAPTIVE_BODY[level], (name, model)
        # A budget would be a 400 on these models; sampling params too.
        assert "budget_tokens" not in kw["thinking"]
        for banned in ("temperature", "top_p", "top_k", "tool_choice"):
            assert banned not in kw, (name, banned)


@pytest.mark.parametrize("model", BUDGET_MODELS)
@pytest.mark.parametrize("level", list(R.LEVELS))
def test_older_claude_keeps_the_v1263_budget_byte_for_byte(monkeypatch, model, level):
    a, msgs = _adapter(monkeypatch, model)
    asyncio.run(_both(a, level))
    for name, kw in msgs.calls:
        assert _thinking_part(kw) == BUDGET_BODY[level], (name, model)
        assert "output_config" not in kw, (name, model)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-haiku-4-5"])
def test_no_level_sends_nothing_in_either_family(monkeypatch, model):
    a, msgs = _adapter(monkeypatch, model)
    asyncio.run(_both(a, ""))
    for name, kw in msgs.calls:
        assert "thinking" not in kw and "output_config" not in kw, (name, model)
        assert kw["max_tokens"] == a.max_tokens


def test_the_family_table_is_closed_and_matches_the_adapter():
    # Every budget id resolves through the same bare-id rule the defaults use.
    for model in BUDGET_MODELS:
        assert R.anthropic_thinking_mode(model) == R.BUDGET, model
    for model in ADAPTIVE_MODELS:
        assert R.anthropic_thinking_mode(model) == R.ADAPTIVE, model
    # Each budget-only id is a model that offers levels (no dead rows).
    for bare in R._ANTHROPIC_BUDGET_THINKING:
        assert R.reasoning_levels("anthropic", bare) == R.LEVELS, bare


def test_the_real_sdk_puts_both_fields_on_the_wire(monkeypatch):
    """Through the installed anthropic SDK (no network: a mock transport),
    so a field the SDK would drop or rename cannot pass as a kwarg."""
    import json

    import httpx
    from anthropic import AsyncAnthropic

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    for model, level in (("claude-opus-5-5", "low"), ("claude-haiku-4-5", "low")):
        a = AnthropicAdapter(model=model, api_key="sk-ant-test")
        client = AsyncAnthropic(
            api_key="sk-ant-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        monkeypatch.setattr(a, "_client", lambda c=client: c)
        asyncio.run(a.complete(system="", messages=_user(), tools=[], reasoning=level))
    adaptive, budget = bodies
    assert adaptive["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert adaptive["output_config"] == {"effort": "low"}
    assert adaptive["max_tokens"] == 6144
    assert budget["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "output_config" not in budget and budget["max_tokens"] == 6144


def test_the_level_on_the_wire_is_the_level_the_receipt_names(monkeypatch):
    """Receipt rule: the route reports the picked level; the adaptive body
    must carry that SAME word, not a translation."""
    for level in R.LEVELS:
        a, msgs = _adapter(monkeypatch, "claude-sonnet-5-5")
        asyncio.run(a.complete(system="", messages=_user(), tools=[], reasoning=level))
        assert msgs.calls[0][1]["output_config"] == {"effort": level}


# --------------------------------------------------------------------------- #
# GPT-6: OpenAI documents reasoning.effort for every gpt-6 id it lists
# (developers.openai.com/api/docs/models/<id>, read 2026-10-10).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol", "GPT-6-Sol"])
def test_gpt6_offers_the_three_levels(model):
    assert R.reasoning_levels("openai", model) == R.LEVELS
    assert R.reasoning_levels("openrouter", f"openai/{model}") == R.LEVELS
    # A LiteLLM / fleet endpoint serving it gets the control too, like gpt-5.
    assert R.reasoning_levels("custom", model) == R.LEVELS


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-6-sol", "medium"),
        ("gpt-6-luna", "medium"),
        ("gpt-6.1-sol", "medium"),
        # Its page lists the levels with no "(default)" marker: unknown.
        ("gpt-6-astra", ""),
    ],
)
def test_gpt6_documented_defaults(model, expected):
    assert R.reasoning_default("openai", model) == expected


def test_gpt6_pattern_does_not_swallow_neighbours():
    assert R.reasoning_levels("openai", "gpt-60") == ()
    assert R.reasoning_levels("openai", "gpt-4o") == ()


def test_gpt6_level_reaches_the_chat_completions_body():
    """The OpenAI adapter needs no GPT-6 special case: it forwards any
    offered level as `reasoning_effort` (v1.263.0)."""
    import json

    import httpx

    from iron_jarvis.providers.adapters.openai import OpenAIAdapter

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    a = OpenAIAdapter(
        model="gpt-6-sol",
        api_key="sk-test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    asyncio.run(a.complete(system="", messages=_user(), tools=[], reasoning="high"))
    assert bodies and bodies[0]["reasoning_effort"] == "high"
