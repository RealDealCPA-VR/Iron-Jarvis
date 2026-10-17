"""v1.344.0, lane P-escalate — an escalation carries what the chat already did.

Before this, a chat turn that read three files and then escalated handed the
agent only the user's sentence: the agent redid every read. Now, in BOTH
lanes (POST /chat = ``chat_turn.run_chat_turn``; /chat/stream =
``routes.chat.stream_chat_turn``), lock-step:

  P1 a turn that escalates (``escalate_to_agent`` or out of rounds) carries
     ``prior_tool_results`` — ``[{tool, args_summary, ok, output_head}]`` for
     every call that settled, in call order, the args REDACTED (the display
     the frames already use), the output MASKED (``detections.redact.mask``)
     and capped (12 entries / 12,000 chars, newest kept). The key is present
     on every escalation (``[]`` when nothing ran) and absent on a plain
     turn, so the pinned response key set is untouched.
  P2 ``SessionCreate.prior_tool_results`` (and the continue / spawn bodies)
     validates the same shape and refuses past the caps with one plain
     sentence (422); ``create_session`` stores the normalised list under
     ``options["prior_tool_results"]`` (never a secret value, never an empty
     key); a continuation never inherits the parent's list; the runtime
     appends ONE block, "What the chat already did this turn", to the first
     user message right after the task, every entry fenced with
     ``wrap_untrusted``, and a run with nothing to carry is byte-identical.

Harness: a real ``create_app`` platform with fake tools registered (the
v1.342.0 / lane-M shape), every call through the real ``registry.invoke``;
the runtime driven through the real ``Orchestrator`` with the scripted mock
adapter the v1.337.0 resume tests use.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    PRIOR_RESULTS_TITLE,
    normalize_prior_tool_results,
    prior_results_block,
)
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.computeruse.safety import _FENCE_BOTTOM, _FENCE_TOP, wrap_untrusted
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _round_budget,
    cap_prior_tool_results,
    prior_tool_result_entry,
    run_chat_turn,
)
from iron_jarvis.daemon.routes.chat import stream_chat_turn
from iron_jarvis.daemon.schemas import (
    PRIOR_RESULT_ARGS_CHARS,
    PRIOR_RESULT_OUTPUT_CHARS,
    PRIOR_RESULTS_MAX_CHARS,
    PRIOR_RESULTS_MAX_ENTRIES,
    ChatBody,
    ContinueBody,
    SessionCreate,
    SpawnBody,
)
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.scheduling.knobs import session_options
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine

LOOKUP = "notes_lookup_fake"      # armed, allow tier, always succeeds
FAILS = "flaky_lookup_fake"       # armed, allow tier, always fails
TOKEN = "ghp_abcdefghijklmnop1234567890"
FOUND = f"found it; the index was fetched with Authorization: Bearer {TOKEN}"
BOOM = "boom: the upstream index is not reachable"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class _FakeTool(Tool):
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

    def __init__(self, name: str, *, ok: bool = True, text: str = FOUND) -> None:
        self.name = name
        self.description = f"fake {name}"
        self._ok, self._text = ok, text
        self.calls: list[dict] = []

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        if self._ok:
            return ToolResult(ok=True, output=self._text)
        return ToolResult(ok=False, error=self._text)


def _app(tmp_path, *tools: _FakeTool):
    """A real platform with *tools* registered and allowed."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    for t in tools:
        platform.registry.register(t)
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{t.name: "allow" for t in tools}}
    )
    return platform


def _body(*tools: str, text: str = "look it up") -> ChatBody:
    return ChatBody(messages=[{"role": "user", "content": text}],
                    tools=list(tools), approval_mode="yolo")


def _call(cid: str, name: str, **args) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args or {"q": "x"})


def _calls(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(tool_calls=list(calls), finish_reason="tool_use")


def _escalate(reason: str = "this needs the full agent") -> LLMResponse:
    return _calls(_call("e1", "escalate_to_agent", reason=reason))


def _script(rounds: list[LLMResponse]):
    """Round i answers ``rounds[i]``; the last one repeats."""
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


def _done(frames: list[tuple[str, dict]]) -> dict:
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1, [ev for ev, _ in frames]
    return done[0]


async def _run_post(platform, body: ChatBody, rounds: list[LLMResponse]) -> dict:
    complete, _ = _script(rounds)
    platform.router.complete = complete
    return await run_chat_turn(platform, {}, body)


async def _run_stream(platform, body: ChatBody, rounds: list[LLMResponse]) -> dict:
    _, stream = _script(rounds)
    platform.router.stream = stream
    gen = await stream_chat_turn(platform, {}, body)
    return _done(_frames("".join([c async for c in gen])))


LANES = [("post", _run_post), ("stream", _run_stream)]


# --------------------------------------------------------------------------- #
# the helpers: the row, the mask, the cap
# --------------------------------------------------------------------------- #


def test_an_entry_masks_a_planted_secret_in_args_and_output_and_strips_the_chat_fence():
    fenced = wrap_untrusted(f"page text with a key: token={TOKEN} and more")
    row = prior_tool_result_entry(
        "web_fetch", {"url": "https://me:hunter2@x.io/a", "q": "x"}, True, fenced,
    )
    assert row["tool"] == "web_fetch"
    assert row["ok"] is True
    # the args: one JSON line, keys sorted, the password masked
    assert row["args_summary"] == '{"q": "x", "url": "https://me:***@x.io/a"}'
    # the output: the chat's fence stripped (the runtime fences once), the
    # token masked
    assert row["output_head"] == "page text with a key: token=*** and more"
    assert _FENCE_TOP not in row["output_head"]
    assert TOKEN not in json.dumps(row) and "hunter2" not in json.dumps(row)
    # unfenced text passes through; a failure keeps its error text
    err = prior_tool_result_entry(FAILS, {"q": "x"}, False, BOOM)
    assert err == {"tool": FAILS, "args_summary": '{"q": "x"}', "ok": False, "output_head": BOOM}


def test_an_entry_is_cut_to_the_per_row_caps_after_masking():
    row = prior_tool_result_entry(
        "t", {"q": "y" * 1000}, True, f"Authorization: Bearer {TOKEN} " + "z" * 5000,
    )
    assert len(row["args_summary"]) == PRIOR_RESULT_ARGS_CHARS == 200
    assert len(row["output_head"]) == PRIOR_RESULT_OUTPUT_CHARS == 1500
    assert row["output_head"].startswith("Authorization: Bearer ***")
    assert TOKEN not in row["output_head"]


def test_the_cap_keeps_the_newest_rows_and_the_total_budget():
    assert PRIOR_RESULTS_MAX_ENTRIES == 12 and PRIOR_RESULTS_MAX_CHARS == 12_000
    rows = [{"tool": f"t{i}", "args_summary": "", "ok": True, "output_head": "o"} for i in range(20)]
    kept = cap_prior_tool_results(rows)
    assert [r["tool"] for r in kept] == [f"t{i}" for i in range(8, 20)]
    # the char budget: 1,501-char rows fit 7 (7 * 1,501 <= 12,000 < 8 * 1,501), newest kept
    big = [{"tool": "t", "args_summary": "", "ok": True, "output_head": str(i) * 1500}
           for i in range(10)]
    kept = cap_prior_tool_results(big)
    assert [r["output_head"][0] for r in kept] == [str(i) for i in range(3, 10)]
    assert sum(len(r["tool"]) + len(r["output_head"]) for r in kept) <= PRIOR_RESULTS_MAX_CHARS
    # junk rows are dropped; the normaliser masks on its own too
    assert normalize_prior_tool_results("nope") == []
    assert normalize_prior_tool_results([{"ok": True}, 5, None]) == []
    only = normalize_prior_tool_results([{"tool": " x ", "output_head": f"sk-{TOKEN[4:]}x"}])
    assert only == [{"tool": "x", "args_summary": "", "ok": False, "output_head": "sk-***"}]


# --------------------------------------------------------------------------- #
# P1 — both lanes carry the turn's outcomes on an escalation, and only then
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane,run", LANES)
async def test_an_escalation_carries_every_settled_call_masked_and_in_order(tmp_path, lane, run):
    ok_tool, bad_tool = _FakeTool(LOOKUP), _FakeTool(FAILS, ok=False, text=BOOM)
    platform = _app(tmp_path, ok_tool, bad_tool)
    rounds = [
        _calls(_call("c1", LOOKUP, q="notes"), _call("c2", FAILS, q="x")),
        _calls(_call("c3", FAILS, q="x")),
        _calls(_call("c4", FAILS, q="x")),   # 3rd identical failure: refused, not run
        _escalate("needs the full agent"),
    ]
    out = await run(platform, _body(LOOKUP, FAILS), rounds)
    assert out["escalate"] is True
    assert out["escalate_reason"] == "needs the full agent"
    rows = out["prior_tool_results"]
    assert [(r["tool"], r["ok"]) for r in rows] == [
        (LOOKUP, True), (FAILS, False), (FAILS, False), (FAILS, False),
    ]
    assert rows[0]["args_summary"] == '{"q": "notes"}'
    assert rows[0]["output_head"] == "found it; the index was fetched with Authorization: Bearer ***"
    assert rows[1]["output_head"] == BOOM
    # the 2nd failure's row is the BARE outcome: the repeat note is the
    # model-facing tool message's, never the carried row's (both lanes)
    assert rows[2]["output_head"] == BOOM, lane
    # the refused third call is an outcome too — the refusal sentence, not a run
    assert rows[3]["output_head"].startswith("NOT RUN:")
    assert len(bad_tool.calls) == 2, lane
    assert set(rows[0]) == {"tool", "args_summary", "ok", "output_head"}
    # the planted token reaches NO carried row (the reply itself may quote
    # the tool's own output to the user, as it always has)
    assert TOKEN not in json.dumps(rows), lane
    assert "Bearer ***" in rows[0]["output_head"]


@pytest.mark.parametrize("lane,run", LANES)
async def test_a_plain_turn_has_no_key_and_an_empty_escalation_carries_an_empty_list(tmp_path, lane, run):
    platform = _app(tmp_path, _FakeTool(LOOKUP))
    plain = await run(platform, _body(LOOKUP), [
        _calls(_call("c1", LOOKUP)), LLMResponse(text="Here it is.", finish_reason="stop"),
    ])
    assert plain["escalate"] is False
    assert "prior_tool_results" not in plain, lane
    empty = await run(platform, _body(LOOKUP), [_escalate()])
    assert empty["escalate"] is True
    assert empty["prior_tool_results"] == [], lane


@pytest.mark.parametrize("lane,run", LANES)
async def test_an_out_of_rounds_escalation_carries_the_rounds_that_ran(tmp_path, lane, run):
    tool = _FakeTool(LOOKUP)
    platform = _app(tmp_path, tool)
    budget = _round_budget([LOOKUP])
    out = await run(platform, _body(LOOKUP), [_calls(_call("c1", LOOKUP, q="again"))])
    assert out["escalate"] is True
    assert "more steps" in out["escalate_reason"]
    rows = out["prior_tool_results"]
    # the last round's calls are not executed (v1.247.0), so one fewer row
    assert len(rows) == budget - 1 == len(tool.calls), lane
    assert all(r["tool"] == LOOKUP and r["ok"] for r in rows)


# --------------------------------------------------------------------------- #
# P2 — the session body validates the shape and the caps
# --------------------------------------------------------------------------- #


def _row(i: int = 0, **over) -> dict:
    return {"tool": f"t{i}", "args_summary": "{}", "ok": True, "output_head": "o", **over}


def test_session_create_accepts_the_shape_and_refuses_past_the_caps():
    body = SessionCreate(task="go", prior_tool_results=[_row(), _row(1, ok=False, output_head="")])
    assert [r.model_dump() for r in body.prior_tool_results] == [
        _row(), _row(1, ok=False, output_head=""),
    ]
    assert SessionCreate(task="go").prior_tool_results is None
    assert SessionCreate(task="go", prior_tool_results=[]).prior_tool_results == []
    with pytest.raises(ValidationError, match="more than 12 entries"):
        SessionCreate(task="go", prior_tool_results=[_row(i) for i in range(13)])
    with pytest.raises(ValidationError, match="output_head is longer than 1500"):
        SessionCreate(task="go", prior_tool_results=[_row(output_head="x" * 1501)])
    with pytest.raises(ValidationError, match="args_summary is longer than 200"):
        SessionCreate(task="go", prior_tool_results=[_row(args_summary="x" * 201)])
    with pytest.raises(ValidationError, match="longer than 12000 characters in all"):
        SessionCreate(task="go", prior_tool_results=[_row(i, output_head="x" * 1500) for i in range(9)])
    with pytest.raises(ValidationError, match="needs a tool name"):
        SessionCreate(task="go", prior_tool_results=[_row(tool="  ")])
    # the other two doors the page sends the list to take the same shape
    assert ContinueBody(message="m", prior_tool_results=[_row()]).prior_tool_results[0].tool == "t0"
    assert SpawnBody(task="t", prior_tool_results=[_row()]).prior_tool_results[0].tool == "t0"
    with pytest.raises(ValidationError, match="more than 12 entries"):
        ContinueBody(message="m", prior_tool_results=[_row(i) for i in range(13)])


def test_post_sessions_answers_422_in_one_plain_sentence_past_the_cap(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        res = client.post("/sessions", json={
            "task": "go", "wait": False,
            "prior_tool_results": [_row(i) for i in range(13)],
        })
    assert res.status_code == 422
    assert "more than 12 entries" in res.text


# --------------------------------------------------------------------------- #
# P2 — the orchestrator stores it under options; a continuation never inherits
# --------------------------------------------------------------------------- #


async def test_create_session_stores_the_normalised_list_and_never_an_empty_key(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    rows = [{"tool": LOOKUP, "args_summary": '{"q": "x"}', "ok": True,
             "output_head": f"found; Authorization: Bearer {TOKEN}"}]
    s = await orch.create_session("tidy", AgentType.BUILDER, prior_tool_results=rows)
    stored = session_options(s)["prior_tool_results"]
    assert stored == [{"tool": LOOKUP, "args_summary": '{"q": "x"}', "ok": True,
                       "output_head": "found; Authorization: Bearer ***"}]
    assert TOKEN not in (s.options_json or "")
    # a door may put the rows in options instead; same normaliser, same row
    s2 = await orch.create_session("tidy", AgentType.BUILDER, options={"prior_tool_results": rows})
    assert session_options(s2)["prior_tool_results"] == stored
    # nothing to carry = no key at all (byte-identical to before)
    s3 = await orch.create_session("tidy", AgentType.BUILDER)
    assert "prior_tool_results" not in session_options(s3)
    s4 = await orch.create_session("tidy", AgentType.BUILDER, prior_tool_results=[])
    assert "prior_tool_results" not in session_options(s4)
    # a continuation drops the parent's list and may carry its own (a
    # parent must be finished first: a continue refuses a busy workspace)
    with session_scope(p.engine) as db:
        for sid in (s.id, s2.id):
            row = db.get(Session, sid)
            row.status = SessionStatus.COMPLETED
            db.add(row)
        db.commit()
    with_own = await orch.continue_session(
        s.id, "and now this", prior_tool_results=[{"tool": "later", "ok": False}],
    )
    assert session_options(with_own)["prior_tool_results"] == [
        {"tool": "later", "args_summary": "", "ok": False, "output_head": ""},
    ]
    # (a second continuation of `s` would be refused while the first is
    # ACTIVE in its workspace, so the no-list case continues `s2`, which
    # stored the same list)
    without = await orch.continue_session(s2.id, "and this")
    assert "prior_tool_results" not in session_options(without)


# --------------------------------------------------------------------------- #
# P2 — the runtime shows the block once, fenced, right after the task
# --------------------------------------------------------------------------- #


class _Recording(MockLLMAdapter):
    seen: list[list[LLMMessage]] = []

    async def complete(self, *, system, messages, tools, **kw):
        _Recording.seen.append(list(messages))
        return await super().complete(system=system, messages=messages, tools=tools, **kw)


DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["read_file"])


async def _first_message(p, orch, session) -> LLMMessage:
    _Recording.seen = []
    p.providers.register(
        "mock", lambda model=None: _Recording(script=[LLMResponse(text="done", finish_reason="stop")])
    )
    await asyncio.wait_for(orch.run_session(session.id, DEFN), timeout=30)
    assert _Recording.seen, "the model was never called"
    first = _Recording.seen[0][0]
    assert first.role == "user"
    return first


async def test_the_runtime_appends_one_fenced_block_right_after_the_task(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    rows = [
        {"tool": LOOKUP, "args_summary": '{"q": "notes"}', "ok": True,
         "output_head": f"found it with token={TOKEN}"},
        {"tool": FAILS, "args_summary": '{"q": "x"}', "ok": False, "output_head": BOOM},
    ]
    s = await orch.create_session("say hi", AgentType.BUILDER, prior_tool_results=rows)
    first = await _first_message(p, orch, s)
    block = prior_results_block(session_options(s)["prior_tool_results"])
    assert first.content == "say hi\n\n" + block
    assert first.content.startswith("say hi\n\n# " + PRIOR_RESULTS_TITLE + "\n")
    # one block, every entry fenced as data by the runtime's own fence helper
    assert first.content.count(PRIOR_RESULTS_TITLE) == 1
    assert first.content.count(_FENCE_TOP) == 2 == first.content.count(_FENCE_BOTTOM)
    assert wrap_untrusted(f"call 1: {LOOKUP} {{\"q\": \"notes\"}}\nresult: ok\nfound it with token=***") in first.content
    assert wrap_untrusted(f"call 2: {FAILS} {{\"q\": \"x\"}}\nresult: failed\n{BOOM}") in first.content
    assert TOKEN not in first.content
    # the system prompt is not where it goes (prompt caching): only the message
    assert all(PRIOR_RESULTS_TITLE not in str(m.content) for m in _Recording.seen[0][1:])


async def test_a_run_with_nothing_to_carry_keeps_its_first_message_byte_identical(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("say hi", AgentType.BUILDER)
    first = await _first_message(p, orch, s)
    assert first.content == "say hi"
    assert PRIOR_RESULTS_TITLE not in first.content and _FENCE_TOP not in first.content
    assert prior_results_block(None) == "" == prior_results_block([])
    assert prior_results_block("junk") == ""


def test_the_block_names_the_title_the_lanes_and_the_page_agree_on():
    assert PRIOR_RESULTS_TITLE == "What the chat already did this turn"
    block = prior_results_block([{"tool": "t", "args_summary": "", "ok": True, "output_head": "o"}])
    assert block.startswith("# " + PRIOR_RESULTS_TITLE)
    # the new copy the model reads has no em-dash (the wave's rule)
    for line in block.split("\n"):
        if line.startswith("#") or line.startswith("The chat ran"):
            assert "—" not in line, line
    assert wrap_untrusted("call 1: t\nresult: ok\no") in block
