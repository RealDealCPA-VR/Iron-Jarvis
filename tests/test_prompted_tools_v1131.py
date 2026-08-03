"""v1.131.0 — prompted tool-calling for text-only local models (Wave 2).

A text-only completer (fleet/custom endpoint, subscription CLI) declares
``tool_use: False`` and could never drive an agentic run — the router rerouted
every tool request off it. ``PromptedToolsAdapter`` wraps the chosen adapter:
tools become a system-prompt contract (one fenced ``tool_call`` JSON block per
turn), the reply is parsed back into a structured ``ToolCall``, malformed
replies get bounded repair rounds, and exhausted repairs degrade to the model's
own raw text — never a fabricated call. The router wraps at its capability
seam, so the chosen model keeps the request instead of being rerouted.
All offline: scripted fake inner adapters, no network.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ToolCall,
)
from iron_jarvis.providers.adapters.prompted_tools import (
    COMPACT_THRESHOLD,
    PromptedToolsAdapter,
    convert_tool_turns,
    render_tools_section,
)
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter, wrap_prompted_tools


class _Scripted(LLMAdapter):
    """Text-only inner adapter that replays a script and records every call."""

    def __init__(self, replies, provider="fleet-local", model="llama3"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[tuple[str, list[LLMMessage], list]] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        text = self._replies.pop(0)
        return LLMResponse(text=text, usage={"input_tokens": 3, "output_tokens": 5})


_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a text file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_folder",
        "description": "List a folder.",
        "input_schema": {"type": "object"},
    },
]

_MSG = [LLMMessage(role="user", content="what's in notes.txt?")]

_FENCED_CALL = (
    "```tool_call\n"
    '{"name": "read_file", "arguments": {"path": "notes.txt"}}\n'
    "```"
)


# --------------------------------------------------------------- (a) render --
async def test_tools_rendered_into_system_prompt_with_contract():
    inner = _Scripted(["plain answer"])
    out = await PromptedToolsAdapter(inner).complete(
        system="You are Iron Jarvis.", messages=_MSG, tools=_TOOLS
    )
    system, _, tools_seen = inner.calls[0]
    # Original system preserved, contract + tools appended.
    assert system.startswith("You are Iron Jarvis.")
    assert "```tool_call" in system
    assert '{"name": "<tool name>", "arguments": { ... }}' in system
    assert "ONE tool call per reply" in system
    assert "read_file" in system and "Read a text file" in system
    assert '"path"' in system  # the JSON schema rides along
    # Few-shot example present.
    assert '{"name": "read_file", "arguments": {"path": "notes.txt"}}' in system
    # The inner is a TEXT-ONLY completer: it must never see the raw specs.
    assert tools_seen == []
    assert out.text == "plain answer"
    assert out.tool_calls == [] and out.finish_reason == "stop"


def test_render_clips_long_descriptions_and_compacts_past_threshold():
    long_desc = "First sentence here. " + ("x" * 2000)
    full = render_tools_section(
        [{"name": "t0", "description": long_desc, "input_schema": {"type": "object"}}]
    )
    assert len(full) < 2500  # description clipped, section bounded
    many = [
        {"name": f"t{i}", "description": long_desc, "input_schema": {"type": "object"}}
        for i in range(COMPACT_THRESHOLD + 1)
    ]
    compact = render_tools_section(many)
    assert "First sentence here." in compact
    assert "JSON Schema" not in compact  # compact mode: name + first sentence only
    assert all(f"- t{i}" in compact for i in range(COMPACT_THRESHOLD + 1))


# ---------------------------------------------------------- (b) fenced call --
async def test_wellformed_fenced_call_parsed_and_text_cleaned():
    inner = _Scripted(["Let me check.\n" + _FENCED_CALL + "\ndone"])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert out.finish_reason == "tool_use"
    assert len(out.tool_calls) == 1
    call = out.tool_calls[0]
    assert call.name == "read_file"
    assert call.arguments == {"path": "notes.txt"}
    assert call.id  # generated, non-empty
    assert "```" not in out.text  # block stripped
    assert "Let me check." in out.text


# ------------------------------------------------------ (c) bare-JSON call ---
async def test_bare_json_fallback_parsed():
    inner = _Scripted(['{"name": "list_folder", "arguments": {}}'])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "list_folder"
    assert out.tool_calls[0].arguments == {}


async def test_bare_json_with_extra_keys_is_a_plain_answer():
    # Only EXACTLY {name, arguments} counts — arbitrary JSON output must not
    # be misread as tool intent.
    inner = _Scripted(['{"name": "Bob", "age": 4}'])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert out.tool_calls == []
    assert out.text == '{"name": "Bob", "age": 4}'
    assert len(inner.calls) == 1  # no repair round for a plain answer


# ------------------------------------------- (c2) adversarial parser edges ---
async def test_nested_markdown_fence_inside_arguments_parses_without_repair():
    # THE canonical agent action: write_file whose content embeds a ``` code
    # block. A non-greedy regex truncates the JSON at the inner fence and fails
    # a fully contract-compliant reply into the repair loop (verifier-found
    # defect); forward json-decoding must take it in ONE round.
    reply = (
        "```tool_call\n"
        '{"name": "read_file", "arguments":'
        ' {"path": "a.md", "note": "```python\\nprint(1)\\n``` and `ls`"}}\n'
        "```"
    )
    inner = _Scripted([reply])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 1  # no repair round burned
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].arguments == {
        "path": "a.md",
        "note": "```python\nprint(1)\n``` and `ls`",
    }
    assert out.text == ""  # the whole block (incl. nested fence) stripped


@pytest.mark.parametrize(
    "fence_open",
    ["```tool_call", "```Tool_Call", "```TOOL_CALL", "``` tool_call"],
)
async def test_fence_tag_case_and_spacing_variants_parse(fence_open):
    # Wrong-case / space-padded fence tags used to fall through as a PLAIN
    # answer — silent tool-intent loss, not even a repair (verifier-found).
    inner = _Scripted(
        [f'{fence_open}\n{{"name": "read_file", "arguments": {{"path": "n"}}}}\n```']
    )
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "read_file"


async def test_wrong_case_tool_name_canonicalized_without_repair():
    inner = _Scripted(['```tool_call\n{"name": "Read_File", "arguments": {"path": "n"}}\n```'])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 1
    assert out.tool_calls[0].name == "read_file"  # canonical registry casing


async def test_double_encoded_arguments_string_accepted():
    # "arguments" as a JSON STRING that decodes to an object — a common
    # small-model slip; a truly non-object string still repairs (pinned by
    # test_unknown_tool_and_non_object_arguments_trigger_repair).
    inner = _Scripted(
        ['```tool_call\n{"name": "read_file", "arguments": "{\\"path\\": \\"n\\"}"}\n```']
    )
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 1
    assert out.tool_calls[0].arguments == {"path": "n"}


async def test_zero_width_padded_bare_json_parses():
    # Zero-width/BOM tokenizer leakage around a bare-JSON call (str.strip()
    # does not remove ​) must not demote it to a plain answer.
    inner = _Scripted(['﻿​{"name": "list_folder", "arguments": {}}​'])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "list_folder"


async def test_megabyte_reply_parses_fast_no_backtracking():
    import time as _time

    reply = ("word " * 200_000) + _FENCED_CALL  # ~1 MB of prose, fence at the end
    inner = _Scripted([reply])
    t0 = _time.perf_counter()
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert _time.perf_counter() - t0 < 2.0  # linear parse, no regex blow-up
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "read_file"


async def test_unclosed_fence_still_flags_intent_and_repairs():
    # Truncated fence (opened, JSON incomplete, never closed) stays a REPAIR,
    # not a silent plain answer — pinned so the raw_decode rewrite can't drift.
    inner = _Scripted(['```tool_call\n{"name": "read_file", "argu', _FENCED_CALL])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 2
    assert "closing" in inner.calls[1][1][-1].content or "not valid JSON" in inner.calls[1][1][-1].content
    assert out.finish_reason == "tool_use"


# --------------------------------------------------------- (d) repair loop ---
async def test_malformed_json_repairs_with_precise_error_then_succeeds():
    bad = '```tool_call\n{"name": "read_file", "arguments": {oops}\n```'
    inner = _Scripted([bad, _FENCED_CALL])
    out = await PromptedToolsAdapter(inner).complete(
        system="sys", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 2
    _, repair_msgs, _ = inner.calls[1]
    # The raw failed reply rides back as an assistant turn...
    assert repair_msgs[-2].role == "assistant"
    assert repair_msgs[-2].content == bad
    # ...and the user turn states the precise error + restates the contract.
    assert repair_msgs[-1].role == "user"
    assert "not valid JSON" in repair_msgs[-1].content
    assert "```tool_call" in repair_msgs[-1].content
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].arguments == {"path": "notes.txt"}
    # Usage aggregates across BOTH inner rounds (both were billed).
    assert out.usage == {"input_tokens": 6, "output_tokens": 10}


async def test_unknown_tool_and_non_object_arguments_trigger_repair():
    inner = _Scripted(
        [
            '```tool_call\n{"name": "write_file", "arguments": {}}\n```',
            '```tool_call\n{"name": "read_file", "arguments": "notes.txt"}\n```',
            _FENCED_CALL,
        ]
    )
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 3
    assert 'unknown tool "write_file"' in inner.calls[1][1][-1].content
    assert '"arguments" must be a JSON object' in inner.calls[2][1][-1].content
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "read_file"


# ----------------------------------------------- (e) honest degradation ------
async def test_two_failed_repairs_degrade_to_plain_text():
    bad = "```tool_call\n{broken\n```"
    inner = _Scripted([bad, bad, "```tool_call\n{still broken\n```"])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=_MSG, tools=_TOOLS
    )
    assert len(inner.calls) == 3  # initial + exactly 2 repair rounds
    assert out.tool_calls == []  # NEVER a fabricated call
    assert out.finish_reason == "stop"
    assert out.text == "```tool_call\n{still broken\n```"  # the model's own words


# ------------------------------------------ (f) tool-turn conversion ---------
async def test_tool_result_turns_converted_for_text_only_inner():
    prior_call = ToolCall(id="tc1", name="read_file", arguments={"path": "notes.txt"})
    history = [
        LLMMessage(role="user", content="what's in notes.txt?"),
        LLMMessage(role="assistant", content="", tool_calls=[prior_call]),
        LLMMessage(role="tool", tool_call_id="tc1", name="read_file", content="milk, eggs"),
    ]
    inner = _Scripted(["It lists milk and eggs."])
    out = await PromptedToolsAdapter(inner).complete(
        system="", messages=history, tools=_TOOLS
    )
    _, sent, _ = inner.calls[0]
    assert [m.role for m in sent] == ["user", "assistant", "user"]
    # The assistant turn re-renders the call it made under the contract...
    assert '"name": "read_file"' in sent[1].content
    assert "```tool_call" in sent[1].content
    # ...and the tool result becomes plain user-visible text.
    assert sent[2].content == 'Tool "read_file" returned:\nmilk, eggs'
    # The CALLER's transcript objects were not mutated (they get replayed to
    # native adapters after failovers and persisted verbatim).
    assert history[1].content == "" and history[1].tool_calls == [prior_call]
    assert history[2].role == "tool" and history[2].content == "milk, eggs"
    assert out.text == "It lists milk and eggs."


def test_convert_tool_turns_leaves_plain_turns_untouched():
    plain = [LLMMessage(role="user", content="hi"), LLMMessage(role="assistant", content="hello")]
    assert convert_tool_turns(plain) == plain


# ----------------------------------------------- (g) no-tools passthrough ----
async def test_no_tools_is_a_passthrough():
    inner = _Scripted(["just text"])
    out = await PromptedToolsAdapter(inner).complete(
        system="my system", messages=_MSG, tools=[]
    )
    system, sent, tools_seen = inner.calls[0]
    assert system == "my system"  # no contract injected
    assert sent == _MSG
    assert tools_seen == []
    assert out.text == "just text"


# ----------------------------------------------------- capabilities ----------
def test_capabilities_report_prompted_mode():
    caps = PromptedToolsAdapter(_Scripted([])).capabilities()
    assert caps["tool_use"] is True
    assert caps["tool_use_mode"] == "prompted"
    assert caps["provider"] == "fleet-local" and caps["model"] == "llama3"
    assert caps["vision"] is False  # inner's vision truth passes through


def test_wrap_decision_is_idempotent_and_skips_native():
    text_only = _Scripted([])
    wrapped = wrap_prompted_tools(text_only)
    assert isinstance(wrapped, PromptedToolsAdapter)
    assert wrap_prompted_tools(wrapped) is wrapped  # never double-wrap

    class _Native(LLMAdapter):
        provider, model = "anthropic", "claude-x"

        async def complete(self, *, system, messages, tools):
            return LLMResponse(text="")

    native = _Native()
    assert wrap_prompted_tools(native) is native


# ------------------------------------------------- (h) wiring, end-to-end ----
def _wired(fake):
    """A REAL ProviderManager + ModelRouter with the fake registered — the
    exact seam production requests travel."""
    manager = ProviderManager()
    manager.register("fleet-local", lambda model=None: fake)
    bus = EventBus()
    events: list = []
    bus.add_handler(lambda e: events.append(e))
    return ModelRouter(manager, "fleet-local", bus), events


async def test_text_only_provider_completes_two_step_tool_loop_via_router():
    inner = _Scripted([_FENCED_CALL, "notes.txt lists milk and eggs."])
    router, events = _wired(inner)

    # Step 1: the tool request lands ON the text-only provider (not rerouted,
    # not mock) and comes back as a structured tool call.
    res1 = await router.complete(
        provider="fleet-local", system="be brief", messages=list(_MSG), tools=_TOOLS
    )
    assert res1.provider == "fleet-local"
    assert res1.response.finish_reason == "tool_use"
    call = res1.response.tool_calls[0]
    assert call.name == "read_file" and call.arguments == {"path": "notes.txt"}
    routed = [e for e in events if e.type == EventType.PROVIDER_ROUTED]
    assert routed and routed[0].payload["reason"] == "prompted-tools"
    assert routed[0].payload["resolved_provider"] == "fleet-local"

    # Step 2: replay the loop transcript (assistant tool_use + tool result),
    # exactly as agents/runtime.py builds it.
    followup = list(_MSG) + [
        LLMMessage(role="assistant", content="", tool_calls=[call]),
        LLMMessage(role="tool", tool_call_id=call.id, name=call.name, content="milk, eggs"),
    ]
    res2 = await router.complete(
        provider="fleet-local", system="be brief", messages=followup, tools=_TOOLS
    )
    assert res2.provider == "fleet-local"
    assert res2.response.finish_reason == "stop"
    assert res2.response.text == "notes.txt lists milk and eggs."
    # The second round's inner transcript carried the converted tool result.
    _, sent, _ = inner.calls[1]
    assert any(
        m.role == "user" and m.content.startswith('Tool "read_file" returned:')
        for m in sent
    )
    # No downgrade to mock at any point — the real local model served both.
    assert not [e for e in events if e.type == EventType.PROVIDER_DOWNGRADED]


async def test_router_stream_serves_prompted_tool_call_single_chunk():
    inner = _Scripted([_FENCED_CALL])
    router, _ = _wired(inner)
    frames = []
    async for f in router.stream(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    ):
        frames.append(f)
    final = next(f for f in frames if f.get("type") == "final")
    assert final["provider"] == "fleet-local"
    assert final["response"].tool_calls[0].name == "read_file"


async def test_agent_runtime_end_to_end_with_prompted_wrapped_provider(
    platform, orchestrator, tmp_path
):
    """The FULL agent runtime (perceive→act loop, real tool registry, real
    workspace) driven by a TEXT-ONLY provider through the router's prompted
    wrap: the scripted model emits a fenced write_file call, the runtime
    executes the real tool, replays the tool turn, and the model answers."""
    from iron_jarvis.core.models import AgentType, SessionStatus
    from pathlib import Path

    inner = _Scripted(
        [
            "```tool_call\n"
            '{"name": "write_file", "arguments": {"path": "RESULT.md",'
            ' "content": "# Iron Jarvis\\nprompted-tools e2e ```code``` fence"}}\n'
            "```",
            "Wrote RESULT.md with the summary.",
        ],
        provider="local-e2e",
        model="llama3",
    )
    platform.providers.register("local-e2e", lambda model=None: inner)

    session = await orchestrator.run(
        "Write RESULT.md summarizing the task.", AgentType.BUILDER, provider="local-e2e"
    )
    assert session.status is SessionStatus.COMPLETED
    result = Path(session.workspace_path) / "RESULT.md"
    assert result.exists()
    # The nested ``` inside the arguments survived the parse intact.
    assert "```code```" in result.read_text(encoding="utf-8")
    transcript = orchestrator.transcript(session.id)
    assert any(t["tool"] == "write_file" and t["ok"] for t in transcript["tools"])
    # The text-only model itself served both rounds (no reroute, no mock).
    assert len(inner.calls) == 2
    assert transcript["runs"][0]["provider"] == "local-e2e"
    # Round 2's inner transcript carried the CONVERTED tool turn.
    _, sent, tools_seen = inner.calls[1]
    assert tools_seen == []  # raw specs never reach the text-only completer
    assert any(m.role == "user" and 'Tool "write_file" returned:' in m.content for m in sent)
    assert not any(m.role == "tool" for m in sent)
    assert transcript["runs"][0]["result"] == "Wrote RESULT.md with the summary."


async def test_vision_reroute_survives_the_wrap():
    # Images still prefer a native vision adapter — the prompted scaffold must
    # not swallow the vision reroute (no prompt makes a text model see).
    class _Vision(LLMAdapter):
        provider, model = "anthropic", "claude-x"

        def __init__(self):
            self.calls = 0

        async def complete(self, *, system, messages, tools):
            self.calls += 1
            return LLMResponse(text="I see a cat.")

    inner = _Scripted(["never called"])
    vision = _Vision()
    # A deterministic credential makes "anthropic" available regardless of the
    # host env; register() then swaps in the fake vision adapter.
    manager = ProviderManager(
        credential_resolver=lambda n: "key" if n == "anthropic" else None
    )
    manager.register("fleet-local", lambda model=None: inner)
    manager.register("anthropic", lambda model=None: vision)
    router = ModelRouter(manager, "anthropic", EventBus())
    msgs = [
        LLMMessage(
            role="user",
            content="what is this?",
            images=[{"data_b64": "aGk=", "media_type": "image/png"}],
        )
    ]
    res = await router.complete(
        provider="fleet-local", system="", messages=msgs, tools=_TOOLS
    )
    assert res.provider == "anthropic"
    assert vision.calls == 1 and inner.calls == []
