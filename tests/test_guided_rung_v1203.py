"""v1.203.0 (C2) — the strict_json rung gets real: guided decoding.

The envelope-gated UPGRADE of the v1.131.0 prompted-tools seam, ported from
the user's IronCore (``ironcore/core/guided.py``). When a text-only adapter's
capability envelope is MEASURED, CURRENT-generation, and mechanically selects
``strict_json``, the router wraps it in ``GuidedToolsAdapter`` instead of the
fenced ``PromptedToolsAdapter``: the guided fragment rides the system prompt,
``response_format`` (json_schema pinning ``{tool: enum+done, args: object}``)
constrains the server, NO native tools param is offered, and the constrained
reply parses through an exclusive 3-way (call / done / repairable error) that
never raises. One repair round, then an HONEST ladder-down to the proven
fenced contract. The scaffold is protocol, not prose — the raw JSON never
reaches user-visible text, and a successful parse is byte-shaped like the
native path (structured tool_calls, finish_reason "tool_use", text="").

PINNED HARD: native-capable adapters and every non-qualifying profile
(unmeasured, stale-generation, predicate absent, manager without
``capability_profile``) keep today's behavior byte-identical — bending a
frontier run is the catastrophic direction. The generation gate is the
Wave-A reviewer's binding note: strict_json scores measured on the bare
prompt (before constrained decoding existed) must never arm the real rung.

All offline: scripted fake inner adapters, no network.
"""

from __future__ import annotations

import json

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ToolCall,
)
from iron_jarvis.providers.adapters.prompted_tools import PromptedToolsAdapter
from iron_jarvis.providers.guided import (
    DONE,
    GuidedToolsAdapter,
    parse_guided_tool_call,
    profile_supports_guided,
    render_guided_system_fragment,
    tool_call_response_format,
)
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter, wrap_prompted_tools


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _GuidedInner(LLMAdapter):
    """Text-only inner that ACCEPTS the C1 additive kwargs and records every
    call — the openai-compat family's post-C1 shape."""

    def __init__(self, replies, provider="fleet-local", model="qwen3"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[dict] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(
        self,
        *,
        system,
        messages,
        tools,
        response_format=None,
        tool_choice=None,
        extra_body=None,
    ):
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "response_format": response_format,
            }
        )
        return LLMResponse(
            text=self._replies.pop(0), usage={"input_tokens": 3, "output_tokens": 5}
        )


class _LegacyInner(LLMAdapter):
    """Text-only inner with the PRE-C1 strict signature — no response_format.
    The guided wrapper must ladder down to the fenced contract for it (a
    constraint nothing enforces is not a rung)."""

    def __init__(self, replies, provider="fleet-local", model="old"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[tuple] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        return LLMResponse(
            text=self._replies.pop(0), usage={"input_tokens": 3, "output_tokens": 5}
        )


class _NativeAdapter(LLMAdapter):
    """A natively tool-capable adapter with the STRICT pre-C1 signature: if
    the router ever passed it a guided kwarg or wrapped it, this test file
    goes red — the frontier path stays byte-identical."""

    provider, model = "fleet-local", "big-native"

    def __init__(self):
        self.calls: list[tuple] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        return LLMResponse(text="native answer")


class _EnvProfile(CapabilityProfile):
    """CapabilityProfile + the generation predicate the C1/C4 plumbing lands
    in parallel. Overriding here keeps this file's gating deterministic no
    matter what semantics the real method ships with."""

    def __init__(self, *args, current=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def is_current_generation(self):
        return self._current


class _NoGenProfile:
    """Measured, strict_json-selecting — but WITHOUT is_current_generation.
    Absence must read as NOT current (the fail-closed getattr-guard)."""

    def is_measured(self):
        return True

    def select_tool_protocol(self):
        return "strict_json"


def _strict_profile(current=True):
    return _EnvProfile(
        model_id="qwen3",
        provider="fleet-local",
        source="probed",
        probed_at="2026-08-23T00:00:00+00:00",
        tool_protocols={"native": 0.50, "strict_json": 0.95},
        current=current,
    )


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

_CALL_JSON = '{"tool": "read_file", "args": {"path": "notes.txt"}}'
_DONE_JSON = '{"tool": "done", "args": {"message": "All set."}}'
_FENCED_CALL = (
    "```tool_call\n"
    '{"name": "read_file", "arguments": {"path": "notes.txt"}}\n'
    "```"
)


# --------------------------------------------------------------- (a) schema --
def test_response_format_pins_tool_enum_plus_done_and_args_object():
    rf = tool_call_response_format(_TOOLS)
    assert rf["type"] == "json_schema"
    js = rf["json_schema"]
    assert js["name"] == "iron_jarvis_tool_call"
    assert js["strict"] is True
    schema = js["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["tool"]["enum"] == ["read_file", "list_folder", "done"]
    assert schema["properties"]["args"] == {"type": "object"}
    assert schema["required"] == ["tool", "args"]
    assert schema["additionalProperties"] is False


def test_hostile_tool_names_are_data_not_string_built():
    hostile = 'evil"], "x": ["done'  # would break any string-built schema
    rf = tool_call_response_format(
        [{"name": hostile, "input_schema": {"type": "object"}}]
    )
    enum = rf["json_schema"]["schema"]["properties"]["tool"]["enum"]
    assert enum == [hostile, DONE]  # the exact literal name, unescaped, as data
    # And the whole object survives a JSON round-trip byte-faithfully.
    assert json.loads(json.dumps(rf)) == rf


def test_openai_style_nesting_tolerated_and_done_never_duplicated():
    specs = [
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        {"name": "done"},  # a tool literally named "done" must not double up
        "junk",  # non-dict entries are skipped, never raise
    ]
    enum = tool_call_response_format(specs)["json_schema"]["schema"]["properties"][
        "tool"
    ]["enum"]
    assert enum == ["read_file", "done"]


def test_empty_tools_enum_is_done_only():
    enum = tool_call_response_format([])["json_schema"]["schema"]["properties"][
        "tool"
    ]["enum"]
    assert enum == ["done"]


def test_fragment_teaches_protocol_catalog_and_both_examples():
    frag = render_guided_system_fragment(_TOOLS)
    assert "guided JSON" in frag
    assert '{"tool": "<name>", "args": {<arguments>}}' in frag
    assert "read_file" in frag and "Read a text file" in frag
    assert "path: string, required" in frag  # params reach the prose catalog
    assert '{"tool": "done", "args": {"message":' in frag
    # The guided protocol never mentions the fenced contract.
    assert "```tool_call" not in frag


# ---------------------------------------------------- (b) 3-way parse battery
def test_parse_valid_call_with_deterministic_id():
    p = parse_guided_tool_call(_CALL_JSON)
    assert p.error is None and p.done is False
    assert p.call is not None
    assert p.call.name == "read_file"
    assert p.call.arguments == {"path": "notes.txt"}
    assert p.call.id.startswith("gd-")
    # Deterministic: same reply, same id; different reply, different id.
    assert parse_guided_tool_call(_CALL_JSON).call.id == p.call.id
    other = parse_guided_tool_call('{"tool": "list_folder", "args": {}}')
    assert other.call.id != p.call.id


def test_parse_done_finishes_the_turn():
    p = parse_guided_tool_call(_DONE_JSON)
    assert p.done is True
    assert p.message == "All set."
    assert p.call is None and p.error is None


def test_parse_recovers_object_wrapped_in_stray_prose():
    # A server that ignored response_format but still emitted ONE object.
    p = parse_guided_tool_call(f"Sure thing!\n{_CALL_JSON}\nHope that helps.")
    assert p.call is not None and p.call.name == "read_file"


def test_parse_malformed_json_is_a_repairable_error_never_a_raise():
    p = parse_guided_tool_call('{"tool": "read_file", "args": {oops')
    assert p.call is None and p.done is False
    assert p.error is not None and "not a valid" in p.error
    assert p.text == '{"tool": "read_file", "args": {oops'  # raw preserved


def test_parse_non_string_tool_errors():
    p = parse_guided_tool_call('{"tool": 3, "args": {}}')
    assert p.error is not None and '"tool"' in p.error


def test_parse_unknown_tool_names_the_available_tools():
    p = parse_guided_tool_call(
        '{"tool": "write_file", "args": {}}', known={"read_file", "list_folder"}
    )
    assert p.call is None
    assert 'unknown tool "write_file"' in p.error
    assert "read_file" in p.error and "list_folder" in p.error


def test_parse_wrong_case_tool_name_canonicalized():
    p = parse_guided_tool_call(
        '{"tool": "Read_File", "args": {"path": "n"}}', known={"read_file"}
    )
    assert p.error is None
    assert p.call.name == "read_file"


def test_parse_args_not_object_errors():
    p = parse_guided_tool_call('{"tool": "read_file", "args": "notes.txt"}')
    assert p.call is None
    assert '"args"' in p.error


def test_parse_never_raises_on_hostile_input():
    hostile = [
        "",
        "   ",
        "[]",
        "{}",
        "null",
        '{"tool": ""}',
        '{"tool": "done"}',  # done without args → clean finish, empty message
        '{"tool": "done", "args": null}',
        '{"args": {}}',
        "{" * 5000,
        "\x00﻿",
        '{"tool": "x", "args": {}} {"tool": "y", "args": {}}',  # two objects
    ]
    for text in hostile:
        p = parse_guided_tool_call(text, known={"read_file"})
        assert p is not None  # no exception is the contract
    done = parse_guided_tool_call('{"tool": "done"}')
    assert done.done is True and done.message == ""


# ------------------------------------------------ (c) the guided wrapper -----
async def test_guided_success_synthesizes_native_shape_and_suppresses_scaffold():
    inner = _GuidedInner([_CALL_JSON])
    out = await GuidedToolsAdapter(inner).complete(
        system="You are Iron Jarvis.", messages=list(_MSG), tools=_TOOLS
    )
    # The native shape, indistinguishable downstream.
    assert out.finish_reason == "tool_use"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "read_file"
    assert out.tool_calls[0].arguments == {"path": "notes.txt"}
    # SCAFFOLD SUPPRESSION: the raw JSON protocol never reaches visible text.
    assert out.text == ""
    rec = inner.calls[0]
    # Server-side constraint ON the call, NO native tools param.
    assert rec["tools"] == []
    assert rec["response_format"]["type"] == "json_schema"
    # Guided fragment (not the fenced contract) rides the system prompt.
    assert rec["system"].startswith("You are Iron Jarvis.")
    assert "guided JSON" in rec["system"]
    assert "```tool_call" not in rec["system"]


async def test_guided_done_returns_only_the_message():
    inner = _GuidedInner([_DONE_JSON])
    out = await GuidedToolsAdapter(inner).complete(
        system="", messages=list(_MSG), tools=_TOOLS
    )
    assert out.text == "All set."
    assert out.tool_calls == []
    assert out.finish_reason == "stop"
    assert "{" not in out.text  # the JSON envelope stays out of the transcript


async def test_parse_failure_gets_one_repair_with_the_error_fed_back():
    inner = _GuidedInner(["utter garbage", _CALL_JSON])
    out = await GuidedToolsAdapter(inner).complete(
        system="sys", messages=list(_MSG), tools=_TOOLS
    )
    assert len(inner.calls) == 2
    repair_msgs = inner.calls[1]["messages"]
    assert repair_msgs[-2].role == "assistant"
    assert repair_msgs[-2].content == "utter garbage"
    assert repair_msgs[-1].role == "user"
    assert "invalid" in repair_msgs[-1].content
    assert "not a valid tool-call JSON object" in repair_msgs[-1].content
    # The constraint is re-applied on the repair round too.
    assert inner.calls[1]["response_format"]["type"] == "json_schema"
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].arguments == {"path": "notes.txt"}
    # Both billed rounds reach the accounting.
    assert out.usage == {"input_tokens": 6, "output_tokens": 10}


async def test_two_parse_failures_ladder_down_to_the_fenced_contract():
    # Rounds 1-2 guided (both unusable), round 3 is the FENCED path re-asking
    # the same request — the proven prompted-tools contract, not a fabrication.
    inner = _GuidedInner(["bad one", "bad two", _FENCED_CALL])
    out = await GuidedToolsAdapter(inner).complete(
        system="sys", messages=list(_MSG), tools=_TOOLS
    )
    assert len(inner.calls) == 3
    fenced = inner.calls[2]
    assert "```tool_call" in fenced["system"]  # the fenced contract took over
    assert "guided JSON" not in fenced["system"]  # rungs never stack prompts
    assert fenced["response_format"] is None  # no constraint on the floor
    assert fenced["tools"] == []  # still a text-only completer
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "read_file"
    # The fenced parser produced the call (its id vocabulary, not gd-).
    assert out.tool_calls[0].id.startswith("ptc_")
    # Every billed round — guided AND fenced — reaches the accounting.
    assert out.usage == {"input_tokens": 9, "output_tokens": 15}


async def test_ladder_down_can_still_degrade_to_plain_text_honestly():
    # Guided fails twice, then the fenced path sees a PLAIN answer — the
    # model's own words come back, never a fabricated call.
    inner = _GuidedInner(["bad", "worse", "I cannot use tools, sorry."])
    out = await GuidedToolsAdapter(inner).complete(
        system="", messages=list(_MSG), tools=_TOOLS
    )
    assert len(inner.calls) == 3
    assert out.tool_calls == []
    assert out.finish_reason == "stop"
    assert out.text == "I cannot use tools, sorry."


async def test_inner_without_response_format_kwarg_goes_straight_to_fenced():
    inner = _LegacyInner([_FENCED_CALL])
    out = await GuidedToolsAdapter(inner).complete(
        system="", messages=list(_MSG), tools=_TOOLS
    )
    # ONE call, on the fenced contract — no TypeError, no fake "guided" round.
    assert len(inner.calls) == 1
    assert "```tool_call" in inner.calls[0][0]
    assert out.finish_reason == "tool_use"
    assert out.tool_calls[0].name == "read_file"


async def test_no_tools_is_an_invisible_passthrough():
    inner = _GuidedInner(["just text"])
    out = await GuidedToolsAdapter(inner).complete(
        system="my system", messages=list(_MSG), tools=[]
    )
    rec = inner.calls[0]
    assert rec["system"] == "my system"  # nothing injected
    assert rec["tools"] == []
    assert rec["response_format"] is None
    assert out.text == "just text"


async def test_transcript_replayed_in_the_guided_protocols_own_words():
    prior = ToolCall(id="tc1", name="read_file", arguments={"path": "notes.txt"})
    history = [
        LLMMessage(role="user", content="what's in notes.txt?"),
        LLMMessage(role="assistant", content="", tool_calls=[prior]),
        LLMMessage(role="tool", tool_call_id="tc1", name="read_file", content="milk, eggs"),
    ]
    inner = _GuidedInner([_DONE_JSON])
    out = await GuidedToolsAdapter(inner).complete(
        system="", messages=history, tools=_TOOLS
    )
    sent = inner.calls[0]["messages"]
    assert [m.role for m in sent] == ["user", "assistant", "user"]
    # The assistant turn re-renders as the GUIDED object, not the fenced block.
    assert '{"tool":"read_file","args":{"path":"notes.txt"}}' in sent[1].content
    assert "```tool_call" not in sent[1].content
    assert sent[2].content == 'Tool "read_file" returned:\nmilk, eggs'
    # Caller's transcript objects untouched (replayed to native after failover).
    assert history[1].content == "" and history[1].tool_calls == [prior]
    assert history[2].role == "tool" and history[2].content == "milk, eggs"
    assert out.text == "All set."


async def test_caller_knobs_accepted_and_guided_schema_supersedes():
    """The C1 uniformity contract: GuidedToolsAdapter.complete declares the
    three knobs like every adapter. Honest semantics while the rung is
    engaged: the caller's response_format is SUPERSEDED by the guided schema
    (two body-level constraints cannot both hold), tool_choice steers nothing
    (inner gets tools=[]), and extra_body is NOT forwarded — the openai-compat
    family merges it LAST, so forwarding would let a caller silently replace
    the constraint this wrapper promised was in force."""
    inner = _GuidedInner([_CALL_JSON])
    out = await GuidedToolsAdapter(inner).complete(
        system="",
        messages=list(_MSG),
        tools=_TOOLS,
        response_format={"type": "json_object"},  # superseded
        tool_choice="required",  # ignored (no native tools in play)
        extra_body={"response_format": {"type": "text"}},  # not forwarded
    )
    assert out.finish_reason == "tool_use"
    rf = inner.calls[0]["response_format"]
    assert rf["json_schema"]["name"] == "iron_jarvis_tool_call"  # OURS won
    # And the fenced fallback still takes the knobs without error (its own
    # contract: accepted, deliberately dropped).
    legacy = _LegacyInner([_FENCED_CALL])
    out2 = await GuidedToolsAdapter(legacy).complete(
        system="",
        messages=list(_MSG),
        tools=_TOOLS,
        response_format={"type": "json_object"},
        tool_choice="required",
        extra_body={"x": 1},
    )
    assert out2.finish_reason == "tool_use"
    assert len(legacy.calls) == 1  # straight to fenced, no TypeError


def test_capabilities_still_report_prompted_mode():
    """agents/decompose.py keys its engage decision on tool_use_mode ==
    'prompted'; the guided rung is an upgrade of the same scaffolded class,
    not a new capability, so the string must not drift."""
    caps = GuidedToolsAdapter(_GuidedInner([])).capabilities()
    assert caps["tool_use"] is True
    assert caps["tool_use_mode"] == "prompted"


# ------------------------------------------------- (d) the envelope gate -----
def test_profile_gate_requires_measured_current_and_strict_json():
    assert profile_supports_guided(_strict_profile()) is True
    # Unmeasured floor → no.
    assert profile_supports_guided(CapabilityProfile(model_id="m")) is False
    # Stale generation → no (the Wave-A binding note).
    assert profile_supports_guided(_strict_profile(current=False)) is False
    # Predicate ABSENT → not current, fail-closed.
    assert profile_supports_guided(_NoGenProfile()) is False
    # Measured but the ladder selects native → the native rung, not guided.
    native = _strict_profile()
    native.tool_protocols = {"native": 0.99, "strict_json": 0.99}
    assert profile_supports_guided(native) is False
    # Measured but NOTHING clears a bar → the floor, not guided.
    weak = _strict_profile()
    weak.tool_protocols = {"native": 0.2, "strict_json": 0.2}
    assert profile_supports_guided(weak) is False
    # None / hostile objects answer False, never raise.
    assert profile_supports_guided(None) is False

    class _Explodes:
        def is_measured(self):
            raise RuntimeError("boom")

    assert profile_supports_guided(_Explodes()) is False


def test_profile_gate_composes_with_the_real_generation_plumbing():
    """No overrides: the REAL probe_generation field (landing with C1/C4). A
    gen-CURRENT measured strict_json profile arms the rung; a gen-1 profile —
    every Wave-A measurement, scored on the bare prompt before constrained
    decoding existed — does not (the binding reviewer note, end to end).
    Skipped, not failed, while the parallel generation plumbing is in flight —
    the gate's fail-closed behavior WITHOUT it is pinned separately above."""
    import dataclasses

    from iron_jarvis.envelope import profile as profile_mod

    CURRENT_PROBE_GENERATION = getattr(profile_mod, "CURRENT_PROBE_GENERATION", None)
    field_names = {f.name for f in dataclasses.fields(CapabilityProfile)}
    if CURRENT_PROBE_GENERATION is None or "probe_generation" not in field_names:
        import pytest

        pytest.skip("generation plumbing (C1/C4) not landed yet")

    kw = dict(
        model_id="qwen3",
        provider="fleet-local",
        source="probed",
        probed_at="2026-08-23T00:00:00+00:00",
        tool_protocols={"native": 0.50, "strict_json": 0.95},
    )
    current = CapabilityProfile(probe_generation=CURRENT_PROBE_GENERATION, **kw)
    assert profile_supports_guided(current) is True
    stale = CapabilityProfile(probe_generation=1, **kw)
    assert profile_supports_guided(stale) is False


# ------------------------------------------- (e) router engagement gating ----
def _wired(fake, profile=None, default="fleet-local"):
    """The test_prompted_tools_v1131 idiom: a REAL ProviderManager + router,
    the fake registered as a runtime provider; optionally shadow the manager's
    cached envelope read with a scripted profile."""
    manager = ProviderManager()
    manager.register("fleet-local", lambda model=None: fake)
    if profile is not None:
        manager.capability_profile = lambda p, m: profile
    bus = EventBus()
    events: list = []
    bus.add_handler(lambda e: events.append(e))
    return ModelRouter(manager, default, bus), events


async def test_measured_strict_json_profile_engages_guided_via_router():
    inner = _GuidedInner([_CALL_JSON])
    router, events = _wired(inner, profile=_strict_profile())
    res = await router.complete(
        provider="fleet-local", system="be brief", messages=list(_MSG), tools=_TOOLS
    )
    # The chosen model kept the request; the disclosure class stays quiet.
    assert res.provider == "fleet-local"
    assert res.reason == "prompted-tools"
    assert res.response.finish_reason == "tool_use"
    assert res.response.tool_calls[0].name == "read_file"
    rec = inner.calls[0]
    assert rec["tools"] == []  # never native specs
    assert rec["response_format"]["type"] == "json_schema"  # the real rung
    assert "guided JSON" in rec["system"]
    routed = [e for e in events if e.type == EventType.PROVIDER_ROUTED]
    assert routed and routed[0].payload["reason"] == "prompted-tools"


async def test_native_capable_adapter_is_byte_identical_even_with_strict_profile():
    """THE FRONTIER PIN. A tool_use-capable adapter never enters the wrap
    seam: raw tools, raw system, strict pre-C1 signature (any guided kwarg
    would TypeError), no wrapper."""
    native = _NativeAdapter()
    router, _ = _wired(native, profile=_strict_profile())
    res = await router.complete(
        provider="fleet-local", system="be brief", messages=list(_MSG), tools=_TOOLS
    )
    assert res.reason == "explicit"
    assert res.response.text == "native answer"
    system, _, tools_seen = native.calls[0]
    assert tools_seen == _TOOLS  # native specs, untouched
    assert system == "be brief"  # no fragment of either rung
    assert "guided JSON" not in system and "```tool_call" not in system


async def test_unmeasured_profile_keeps_the_fenced_path():
    inner = _GuidedInner([_FENCED_CALL])
    router, _ = _wired(inner, profile=CapabilityProfile(model_id="qwen3"))
    res = await router.complete(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    )
    rec = inner.calls[0]
    assert "```tool_call" in rec["system"]  # today's fenced contract
    assert rec["response_format"] is None  # no constraint invented
    assert res.reason == "prompted-tools"
    assert res.response.tool_calls[0].name == "read_file"


async def test_stale_generation_profile_keeps_the_fenced_path():
    """The binding Wave-A reviewer note, at the seam: a strict_json score
    from a pre-constrained-decoding probe generation must not arm the rung."""
    inner = _GuidedInner([_FENCED_CALL])
    router, _ = _wired(inner, profile=_strict_profile(current=False))
    res = await router.complete(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    )
    assert "```tool_call" in inner.calls[0]["system"]
    assert inner.calls[0]["response_format"] is None
    assert res.response.finish_reason == "tool_use"


async def test_absent_generation_predicate_keeps_the_fenced_path():
    inner = _GuidedInner([_FENCED_CALL])
    router, _ = _wired(inner, profile=_NoGenProfile())
    await router.complete(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    )
    assert "```tool_call" in inner.calls[0]["system"]
    assert inner.calls[0]["response_format"] is None


async def test_manager_without_capability_profile_keeps_the_fenced_path():
    """Bare test managers (and any manager predating the envelope) have no
    capability_profile — the getattr guard must land on the fenced rung."""

    class _BareMgr:
        def __init__(self, adapter):
            self._a = adapter

        def available(self, p):
            return p == self._a.provider

        def has_available_api_provider(self):
            return False

        def get(self, p, m=None):
            return self._a

    inner = _GuidedInner([_FENCED_CALL])
    router = ModelRouter(_BareMgr(inner), "fleet-local", EventBus())
    res = await router.complete(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    )
    assert "```tool_call" in inner.calls[0]["system"]
    assert inner.calls[0]["response_format"] is None
    assert res.response.tool_calls[0].name == "read_file"


def test_wrap_decision_is_idempotent_across_both_rungs():
    inner = _GuidedInner([])
    router, _ = _wired(inner, profile=_strict_profile())
    wrapped = router._wrap_for_tools(inner)
    assert isinstance(wrapped, GuidedToolsAdapter)
    # Never double-wrap — in either direction, through either entry point.
    assert router._wrap_for_tools(wrapped) is wrapped
    assert wrap_prompted_tools(wrapped) is wrapped  # guided IS prompted-class
    assert isinstance(wrapped, PromptedToolsAdapter)

    native = _NativeAdapter()
    assert router._wrap_for_tools(native) is native  # native never wrapped


async def test_stream_lane_engages_guided_identically_and_leaks_no_scaffold():
    """MIRROR (lock-step rule): the stream seam takes the same envelope-gated
    wrap. The wrapper streams via the base single-chunk default, and because
    the parsed call carries text="" NO text frame exists — the raw JSON never
    reaches the lane the user watches token by token."""
    inner = _GuidedInner([_CALL_JSON])
    router, _ = _wired(inner, profile=_strict_profile())
    frames = []
    async for f in router.stream(
        provider="fleet-local", system="", messages=list(_MSG), tools=_TOOLS
    ):
        frames.append(f)
    final = next(f for f in frames if f.get("type") == "final")
    assert final["provider"] == "fleet-local"
    assert final["reason"] == "prompted-tools"
    assert final["response"].tool_calls[0].name == "read_file"
    assert inner.calls[0]["response_format"]["type"] == "json_schema"
    text_frames = [f for f in frames if f.get("type") == "text"]
    assert text_frames == []  # scaffold suppressed from the visible stream
