"""v1.349.0, lane B1d — the daemon sends a MASKED copy of what each step did.

agent-elements item #1, daemon half: every step in the chat can be opened to
show its input and its output, even in a chat reopened next week. Before this
the finished ``tool_call`` frame carried only ``output`` (``str(content)
[:2000]``) — UNMASKED and still wrapped in the untrusted fence — and the
started frame the redacted ``args``. Now every FINISHED frame also carries

    "detail": {"input":  the redacted args as one JSON line, masked, <= 300,
               "output": the UNFENCED output, masked BEFORE the cut, <= 1500}

built by ``chat_turn.step_detail`` (beside ``prior_tool_result_entry``, the
escalation row's helper — the two can never disagree, pinned below):

  * the /chat/stream lane, BOTH finished sites (the lane's own repeated-call
    refusal and every call that went through the registry, refusals too);
  * the agent runtime's session stream (``RunSink.tool_finished`` from
    ``AgentRuntime.perceive_act``) — whose output may be fenced AND cut at the
    16,000-char context cap, so the fence is still taken off there.

The old ``output`` key is unchanged byte for byte, and POST /chat (no frames)
gains no key. Harness: the REAL ``create_app`` platform, the real registry,
the real stream lane and the real runtime; only the model is scripted.
"""

from __future__ import annotations

import json
from typing import Any

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.computeruse.safety import _FENCE_BOTTOM, _FENCE_TOP, wrap_untrusted
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.core.streams import StreamHub
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    STEP_DETAIL_INPUT_CHARS,
    STEP_DETAIL_OUTPUT_CHARS,
    prior_tool_result_entry,
    run_chat_turn,
    step_detail,
)
from iron_jarvis.daemon.routes.chat import stream_chat_turn
from iron_jarvis.daemon.schemas import PRIOR_RESULT_ARGS_CHARS, PRIOR_RESULT_OUTPUT_CHARS, ChatBody
from iron_jarvis.detections.redact import mask
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine

READER = "secret_reader_fake"   # armed, untrusted content, answers a secret
FAILS = "flaky_lookup_fake"     # armed, always fails (with a secret in the error)
OTHER = "other_lookup_fake"     # registered, NOT armed
TOKEN = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"
CARD = "4111111111111111"       # a value only the TOOL's redact_args hides
RAW = f"page says: key {TOKEN}\nAuthorization: Bearer x\nmore text " + "z" * 3000
BOOM = "boom: upstream answered password=hunter2"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class _FakeTool(Tool):
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

    def __init__(self, name: str, *, ok: bool = True, text: str = RAW,
                 untrusted: bool = False) -> None:
        self.name = name
        self.description = f"fake {name}"
        self._ok, self._text = ok, text
        self.returns_untrusted_content = untrusted
        self.calls: list[dict] = []

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        # A tool that hides one of its own arguments: the detail must carry
        # what THIS says, never the raw argument (``mask`` alone would not
        # recognise a card number).
        out = dict(args)
        if "card" in out:
            out["card"] = "[card hidden]"
        return out

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        if self._ok:
            return ToolResult(ok=True, output=self._text)
        return ToolResult(ok=False, error=self._text)


def _app(tmp_path, *tools: _FakeTool):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    for t in tools:
        platform.registry.register(t)
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{t.name: "allow" for t in tools}}
    )
    return platform


def _body(*tools: str) -> ChatBody:
    return ChatBody(messages=[{"role": "user", "content": "look it up"}],
                    tools=list(tools), approval_mode="yolo")


def _call(cid: str, name: str, **args) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args or {"q": "x"})


def _calls(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(tool_calls=list(calls), finish_reason="tool_use")


def _script(rounds: list[LLMResponse]):
    state = {"n": 0}

    def _next():
        i = min(state["n"], len(rounds) - 1)
        state["n"] += 1
        return rounds[i]

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None, **kw):
        return RouteResult(_next(), "mock", "mock")

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        yield {"type": "final", "response": _next(), "provider": "mock", "model": "mock"}

    return fake_complete, fake_stream


def _frames(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        event, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            try:
                out.append((event, json.loads("\n".join(data_lines))))
            except ValueError:
                pass
    return out


async def _run_stream(platform, body: ChatBody, rounds: list[LLMResponse]):
    _, stream = _script(rounds)
    platform.router.stream = stream
    gen = await stream_chat_turn(platform, {}, body)
    frames = _frames("".join([c async for c in gen]))
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1, [ev for ev, _ in frames]
    return done[0], frames


def _tool_frames(frames, status: str) -> list[dict]:
    return [d for ev, d in frames
            if ev == "tool_call" and d.get("status") == status and d.get("name") != "escalate_to_agent"]


def _expected_input(safe_args: dict) -> str:
    return mask(json.dumps(safe_args, ensure_ascii=False, sort_keys=True))[:STEP_DETAIL_INPUT_CHARS]


# --------------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------------- #


def test_the_caps_are_the_brief_s_and_the_output_cap_is_the_escalation_row_s():
    assert STEP_DETAIL_INPUT_CHARS == 300
    assert STEP_DETAIL_OUTPUT_CHARS == PRIOR_RESULT_OUTPUT_CHARS == 1500


def test_a_detail_is_masked_unfenced_and_capped():
    d = step_detail(READER, {"q": "password=hunter2 " + "y" * 400}, wrap_untrusted(RAW))
    assert set(d) == {"input", "output"}
    assert len(d["input"]) == 300 and d["input"].startswith('{"q": "password=*** yyy')
    assert "hunter2" not in d["input"]
    assert len(d["output"]) == 1500
    assert d["output"].startswith("page says: key sk-ant-***\nAuthorization: Bearer ***\nmore text")
    assert TOKEN not in d["output"] and "Bearer x" not in d["output"]
    assert _FENCE_TOP not in d["output"] and _FENCE_BOTTOM not in d["output"]
    # a call with no arguments has no input to show; plain text passes through
    assert step_detail("list_files", {}, "a.txt") == {"input": "", "output": "a.txt"}
    assert step_detail("list_files", None, None) == {"input": "", "output": ""}


def test_the_output_is_masked_before_it_is_cut():
    # The token starts 9 chars before the cap. Cut first, the 5 characters
    # after "ghp_" would be too short for the token rule (6+) and LEAK;
    # masked first, the whole token is *** and fits.
    content = "a" * 1490 + " ghp_SECRET9876543210"
    d = step_detail("t", {"q": "x"}, content)
    assert d["output"] == "a" * 1490 + " ghp_***"
    assert "SECRE" not in d["output"]
    cut_first = mask(content[:STEP_DETAIL_OUTPUT_CHARS])
    assert "SECRE" in cut_first, "anti-vacuity: cutting first WOULD leak"


def test_a_detail_never_disagrees_with_the_escalation_row():
    cases = [
        ({"q": "notes", "url": "https://me:hunter2@x.io/a"}, wrap_untrusted(RAW)),
        ({"q": "y" * 1000}, f"Authorization: Bearer {TOKEN} " + "z" * 5000),
        ({"q": "x"}, BOOM),
        ({"q": "x"}, wrap_untrusted("")),
    ]
    for args, content in cases:
        d = step_detail("t", args, content)
        row = prior_tool_result_entry("t", args, True, content)
        assert d["output"] == row["output_head"], content[:40]
        assert d["input"][:PRIOR_RESULT_ARGS_CHARS] == row["args_summary"], args


def test_a_runtime_output_fenced_then_cut_or_noted_still_loses_its_fence():
    # The agent runtime fences, then cuts at its 16,000-char context cap
    # (the closing fence is gone) or appends its repeat note AFTER the fence.
    cut = wrap_untrusted(RAW + "q" * 20000)[:16000] + "\n[... truncated 9 chars — full output in the transcript]"
    d = step_detail(READER, {"q": "x"}, cut)
    assert d["output"].startswith("page says: key sk-ant-***\nAuthorization: Bearer ***")
    assert _FENCE_TOP not in d["output"] and TOKEN not in d["output"]
    noted = wrap_untrusted(BOOM) + "\n\n[repeat — this is failure 2 in a row]"
    d = step_detail(FAILS, {"q": "x"}, noted)
    assert d["output"] == "boom: upstream answered password=***\n\n[repeat — this is failure 2 in a row]"


def test_a_detail_never_raises():
    class _Loud:
        def __str__(self) -> str:
            raise RuntimeError("no")

    assert step_detail("t", {"q": "x"}, _Loud()) == {"input": "", "output": ""}
    # an argument json cannot dump falls back to repr, never a failed turn
    assert step_detail("t", {1j: "x"}, "ok")["output"] == "ok"


# --------------------------------------------------------------------------- #
# the /chat/stream lane — the real lane, both finished sites
# --------------------------------------------------------------------------- #


async def test_a_finished_frame_carries_the_masked_unfenced_detail_and_keeps_its_output(tmp_path):
    reader = _FakeTool(READER, untrusted=True)
    platform = _app(tmp_path, reader)
    args = {"q": "password=hunter2 " + "y" * 400, "card": CARD}
    done, frames = await _run_stream(platform, _body(READER), [
        _calls(_call("c1", READER, **args)),
        LLMResponse(text="Read it."),
    ])
    assert reader.calls == [args], "anti-vacuity: the tool really ran"
    [started] = _tool_frames(frames, "started")
    [finished] = _tool_frames(frames, "finished")
    assert finished["ok"] is True
    # the old key, byte for byte: the FENCED, UNMASKED head (unchanged)
    assert finished["output"] == wrap_untrusted(RAW)[:2000]
    assert TOKEN in finished["output"]
    # the new key: the started frame's REDACTED args, masked, capped
    detail = finished["detail"]
    assert set(detail) == {"input", "output"}
    assert started["args"]["card"] == "[card hidden]"
    assert detail["input"] == _expected_input(started["args"])
    assert len(detail["input"]) == 300
    assert CARD not in detail["input"] and "hunter2" not in detail["input"]
    assert '"card": "[card hidden]"' in detail["input"]
    # …and the output unfenced, masked, capped
    assert len(detail["output"]) == 1500
    assert detail["output"].startswith("page says: key sk-ant-***\nAuthorization: Bearer ***\nmore text")
    assert TOKEN not in detail["output"] and "Bearer x" not in detail["output"]
    assert _FENCE_TOP not in detail["output"] and _FENCE_BOTTOM not in detail["output"]
    assert TOKEN not in json.dumps(detail) and CARD not in json.dumps(detail)
    assert done["reply"].startswith("Read it.")


async def test_a_refused_call_carries_detail_too_on_both_sites(tmp_path):
    bad, armed, other = _FakeTool(FAILS, ok=False, text=BOOM), _FakeTool(READER), _FakeTool(OTHER)
    platform = _app(tmp_path, bad, armed, other)
    _, frames = await _run_stream(platform, _body(FAILS, READER), [
        _calls(_call("c1", FAILS)),
        _calls(_call("c2", FAILS)),
        _calls(_call("c3", FAILS)),            # 3rd identical failure: the LANE refuses it
        _calls(_call("c4", OTHER, q="y")),      # not armed: the REGISTRY refuses it
        LLMResponse(text="I could not look that up."),
    ])
    assert len(bad.calls) == 2 and other.calls == [], "anti-vacuity: c3 and c4 never ran"
    finished = {d["id"]: d for d in _tool_frames(frames, "finished")}
    assert set(finished) == {"c1", "c2", "c3", "c4"}
    for cid in ("c1", "c2"):
        f = finished[cid]
        assert f["ok"] is False
        assert f["output"] == BOOM, "the old key keeps the bare, unmasked error"
        assert f["detail"] == {"input": '{"q": "x"}', "output": "boom: upstream answered password=***"}
    # the lane's own refusal (the `refused` plan site)
    c3 = finished["c3"]
    assert c3["ok"] is False and c3["output"].startswith("NOT RUN:")
    assert c3["detail"] == {"input": '{"q": "x"}', "output": c3["output"]}
    # the registry's refusal (the ordinary site)
    c4 = finished["c4"]
    assert c4["ok"] is False and c4["output"].startswith("not armed:")
    assert c4["detail"] == {"input": '{"q": "y"}', "output": mask(c4["output"])[:1500]}


async def test_a_step_s_detail_matches_its_escalation_row(tmp_path):
    reader, bad = _FakeTool(READER, untrusted=True), _FakeTool(FAILS, ok=False, text=BOOM)
    platform = _app(tmp_path, reader, bad)
    done, frames = await _run_stream(platform, _body(READER, FAILS), [
        _calls(_call("c1", READER, q="notes", card=CARD), _call("c2", FAILS)),
        _calls(_call("e1", "escalate_to_agent", reason="needs the full agent")),
    ])
    assert done["escalate"] is True
    rows = done["prior_tool_results"]
    finished = _tool_frames(frames, "finished")
    assert [f["id"] for f in finished] == ["c1", "c2"]
    assert len(rows) == 2
    for f, row in zip(finished, rows):
        assert f["detail"]["output"] == row["output_head"], f["id"]
        assert f["detail"]["input"][:PRIOR_RESULT_ARGS_CHARS] == row["args_summary"], f["id"]


async def test_the_post_lane_gains_no_detail_key(tmp_path):
    reader = _FakeTool(READER, untrusted=True)
    platform = _app(tmp_path, reader)
    complete, _ = _script([_calls(_call("c1", READER)), LLMResponse(text="Read it.")])
    platform.router.complete = complete
    out = await run_chat_turn(platform, {}, _body(READER))
    assert reader.calls == [{"q": "x"}], "anti-vacuity: the POST lane ran the tool"
    assert "detail" not in out


# --------------------------------------------------------------------------- #
# the agent runtime's session stream
# --------------------------------------------------------------------------- #


def test_the_sink_adds_detail_only_when_given():
    hub = StreamHub()
    q = hub.subscribe("s1")
    sink = hub.sink("s1", "r1")
    sink.tool_finished("c1", "t", ok=True, preview="p")
    assert q.get_nowait()["data"] == {
        "id": "c1", "name": "t", "status": "finished", "ok": True, "output": "p",
    }
    sink.tool_finished("c2", "t", ok=False, preview="p", detail={"input": "i", "output": "o"})
    assert q.get_nowait()["data"]["detail"] == {"input": "i", "output": "o"}


class _Scripted(LLMAdapter):
    """A natively tool-capable scripted adapter: round i answers replies[i]."""

    provider = "native-b1d"
    model = "m1"

    def __init__(self, replies: list) -> None:
        self._replies = list(replies)

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": False}

    async def complete(self, *, system, messages, tools, **kw):
        r = self._replies.pop(0)
        if isinstance(r, LLMResponse):
            return r
        return LLMResponse(text=r, usage={"input_tokens": 2, "output_tokens": 3})


async def test_the_runtime_s_finished_frames_carry_the_same_masked_detail(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator

    reader = _FakeTool(READER, untrusted=True)
    big = _FakeTool("big_reader_fake", untrusted=True, text=RAW + "q" * 20000)
    platform = _app(tmp_path, reader, big)
    adapter = _Scripted([
        _calls(_call("c1", READER, q="notes", card=CARD), _call("c2", "big_reader_fake")),
        "done",
    ])
    platform.providers.register(adapter.provider, lambda model=None: adapter)
    session = await Orchestrator(platform).create_session(
        "read the notes", AgentType.BUILDER, provider=adapter.provider,
    )
    run = AgentRun(session_id=session.id, agent_type=AgentType.BUILDER,
                   provider=session.provider, model=session.model, state=AgentState.RUNNING)
    hub = StreamHub()
    q = hub.subscribe(session.id)
    finished_ok, _ = await AgentRuntime(platform).perceive_act(
        run, session, get_agent_definition(AgentType.BUILDER),
        system_prompt="sys", messages=[LLMMessage(role="user", content="read the notes")],
        tool_specs=platform.registry.specs([READER, "big_reader_fake"]),
        session_allow=set(), sink=hub.sink(session.id, run.id), max_steps=3,
    )
    assert finished_ok is True
    assert reader.calls and big.calls, "anti-vacuity: both tools ran in the runtime"
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    started = {f["data"]["id"]: f["data"] for f in frames
               if f["event"] == "tool_call" and f["data"]["status"] == "started"}
    finished = {f["data"]["id"]: f["data"] for f in frames
                if f["event"] == "tool_call" and f["data"]["status"] == "finished"}
    assert set(finished) == {"c1", "c2"}
    c1, c2 = finished["c1"], finished["c2"]
    # the old preview is unchanged: the fenced, unmasked head
    assert c1["output"] == wrap_untrusted(RAW)[:500] and TOKEN in c1["output"]
    # the detail: the started frame's redacted args, the output unfenced + masked
    assert started["c1"]["args"]["card"] == "[card hidden]"
    assert c1["detail"]["input"] == _expected_input(started["c1"]["args"])
    assert CARD not in c1["detail"]["input"]
    assert c1["detail"]["output"] == step_detail(READER, {}, wrap_untrusted(RAW))["output"]
    assert c1["detail"]["output"].startswith("page says: key sk-ant-***\nAuthorization: Bearer ***")
    # the big one was fenced AND cut at the context cap: still no fence, masked, capped
    assert c2["detail"]["input"] == '{"q": "x"}'
    assert c2["detail"]["output"].startswith("page says: key sk-ant-***")
    assert len(c2["detail"]["output"]) == 1500
    for f in (c1, c2):
        assert TOKEN not in json.dumps(f["detail"]), f["id"]
        assert _FENCE_TOP not in f["detail"]["output"], f["id"]
