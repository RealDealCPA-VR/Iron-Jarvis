"""Third north-star fix plan, group 17 (part): the gate in agent runs, a
locked app's tools, and the code tools a low turn is shown.

PG1 in an AGENT RUN. A run handed off from chat starts with ``allow_tools``
holding every name the chat ARMED (the chat page sends
``armedNow.concat(extraGrants)``; an app switched on for the chat arms each of
its tools by name, plus ``mcp_call``). Before the fix a HELD-BACK app tool (an
MCP tool that arrived in a later update of its app and can change things,
``Tool.quarantined``) found its own name in that grant: the run did not pause
and the gate's "its own name is in session_allow" exception ran it with no
card. Now the runtime keeps the user's own card answers apart
(``_held_grants`` / ``_held_once``), pauses on such a tool in every posture
whatever the run was handed, and hands the registry those answers as
``card_allow``. Proved through the REAL ``AgentRuntime``, the real registry,
permission engine and approvals registry: only the model and each app tool's
``execute`` are doubles.

PG1 leftover, a LOCKED app (Microsoft 365, ``Tool.locked``). Its card and the
Handbook promise "Jarvis asks you before each use of it". Pinned here in both
chat lanes (a card for every call in the stream lane, offering only Allow
once / Deny; refused in words in the POST lane) and in a run (it never
reaches the app: the tool itself refuses outside a plain chat).

PG3 leftover. A chat turn low from its door is no longer SHOWN the code tools
(``core/trust.CODE_DENY``) it would be refused, and the receipt counts them.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, SessionStatus, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import CHAT_ONLY_APP_REFUSAL, MCPRemoteTool
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import SpendCheck, ToolResult

APP = "notes"
HELD = f"mcp__{APP}__delete_page"  # arrived in the app's update: held back
KEPT = f"mcp__{APP}__update_page"  # was there when the app was connected
ARMED_BY_CHAT = [HELD, KEPT, "mcp_call"]  # what a hand-off from chat carries


# --------------------------------------------------------------------------- #
# Run harness: the real runtime, registry, gate, approvals and ledger.
# --------------------------------------------------------------------------- #
@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    ran: dict[str, list[dict]] = {}
    for remote, held in (("update_page", False), ("delete_page", True)):
        spec = {"name": remote, "inputSchema": {"type": "object", "properties": {}}}
        tool = MCPRemoteTool.from_spec(SimpleNamespace(), APP, spec)
        tool.quarantined = held
        ran[tool.name] = []

        async def _fake(args, ctx, _n=tool.name):
            ran[_n].append(dict(args))
            return ToolResult(ok=True, output=f"{_n} ran")

        tool.execute = _fake  # type: ignore[method-assign]
        platform.registry.register(tool, mcp=True)

    published: list[dict] = []
    answers: list[str] = []
    by_tool: dict[str, "str | None"] = {}
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        kind = str(getattr(type, "value", type))
        published.append({"type": kind, "payload": payload or {}, "session_id": session_id})
        out = await real_publish(type, payload, session_id=session_id, **kw)
        if kind == "approval.requested":
            # Answer the card as the user would, on the next loop turn (the
            # run awaits the answer's future right after this publish). A
            # ``by_tool`` answer of None leaves the card waiting.
            tool_name = str((payload or {}).get("tool"))
            if tool_name in by_tool:
                decision = by_tool[tool_name]
            else:
                decision = answers.pop(0) if answers else "deny"
            if decision is not None:
                ap_id = str((payload or {}).get("approval_id"))
                asyncio.get_running_loop().call_soon(platform.approvals.resolve, ap_id, decision)
        return out

    platform.event_bus.publish = spy
    return SimpleNamespace(
        app=app, platform=platform, runtime=AgentRuntime(platform),
        orch=Orchestrator(platform), ran=ran, published=published, answers=answers,
        by_tool=by_tool,
    )


def _steps(steps: list[list[ToolCall]]):
    """A router stream double: one entry per step, then "Done."."""
    state = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _call(name: str, cid: str) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments={})


async def _run(rt, steps, answers=(), *, origin="chat", allow=ARMED_BY_CHAT,
               approval_mode=None, session=None):
    """Hand a run its tools as a chat hand-off does, run it to the end with
    the given card answers, and return its session."""
    rt.platform.router.stream = _steps(steps)
    rt.answers[:] = list(answers)
    if session is None:
        session = await rt.orch.create_session(
            "tidy up my pages", AgentType.BUILDER, origin=origin,
            allow_tools=list(allow), approval_mode=approval_mode,
        )
    await rt.runtime.run(
        session, AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[HELD, KEPT]),
    )
    await flush_interrupt_notes()
    return session


def _cards(rt, sid: str) -> list[str]:
    return [
        p["payload"].get("tool") for p in rt.published
        if p["type"] == "approval.requested" and p["session_id"] == sid
    ]


def _rows(rt, sid: str) -> list[ToolInvocation]:
    with session_scope(rt.platform.engine) as db:
        rows = list(db.exec(
            select(ToolInvocation).where(ToolInvocation.session_id == sid)
            .order_by(ToolInvocation.created_at)
        ))
        for r in rows:
            db.expunge(r)
        return rows


def _stored(rt, sid: str) -> list[str]:
    with session_scope(rt.platform.engine) as db:
        return json.loads(db.get(SessionRow, sid).allow_tools_json or "[]")


def _settle(rt, sid: str) -> None:
    with session_scope(rt.platform.engine) as db:
        row = db.get(SessionRow, sid)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()


# --------------------------------------------------------------------------- #
# PG1 in an agent run.
# --------------------------------------------------------------------------- #
async def test_a_run_from_chat_pauses_on_a_held_back_tool_whose_name_it_was_handed(rt):
    """(a) + (e): the hand-off carried both app tools' names and ``mcp_call``.
    The original tool runs with no card (control); the held-back one pauses,
    the card says it is new, and the user's No keeps it from running."""
    s = await _run(rt, [[_call(KEPT, "k1"), _call(HELD, "h1")]], ["deny"])
    assert rt.ran[KEPT] == [{}], "control: the app's original tool runs with no card"
    assert _cards(rt, s.id) == [HELD], "the held-back tool ran with no card"
    req = [p["payload"] for p in rt.published if p["type"] == "approval.requested"][0]
    assert req["quarantined"] is True and req["can_always"] is False
    assert rt.ran[HELD] == []
    assert [(r.tool, r.ok) for r in _rows(rt, s.id) if r.tool == HELD] == [(HELD, False)]


async def test_allow_once_runs_that_call_and_the_next_call_in_the_run_asks_again(rt):
    """(b)"""
    s = await _run(rt, [[_call(HELD, "h1")], [_call(HELD, "h2")]], ["once", "deny"])
    assert _cards(rt, s.id) == [HELD, HELD], "the second call must ask again"
    assert rt.ran[HELD] == [{}], "Allow once runs exactly the call it was asked about"
    assert [r.ok for r in _rows(rt, s.id) if r.tool == HELD] == [True, False]


async def test_allow_for_this_run_covers_the_run_but_never_the_next_one(rt):
    """(c) "Allow for this run" covers the later call of the SAME run. The
    answer is still written to the row's grant list (the continue's grant for
    every other tool), but that list never lifts a held-back tool: the next
    run, which starts from it, asks again."""
    s = await _run(rt, [[_call(HELD, "h1")], [_call(HELD, "h2")]], ["conversation"])
    assert _cards(rt, s.id) == [HELD]
    assert rt.ran[HELD] == [{}, {}]
    assert HELD in _stored(rt, s.id)

    _settle(rt, s.id)
    follow = await rt.orch.continue_session(s.id, "and the next page")
    assert HELD in _stored(rt, follow.id), "the continue carries the stored grant"
    await _run(rt, [[_call(HELD, "h3")]], ["deny"], session=follow)
    assert _cards(rt, follow.id) == [HELD], "a later run ran the held-back tool unasked"
    assert rt.ran[HELD] == [{}, {}]


async def test_a_run_nobody_can_answer_refuses_it_and_never_runs_it(rt):
    """(d) A run whose door states nobody (an API caller, a delegated child):
    no card can be shown, and the handed names lift nothing."""
    s = await _run(rt, [[_call(HELD, "h1")]], origin=None)
    assert _cards(rt, s.id) == []
    assert rt.ran[HELD] == []
    (row,) = [r for r in _rows(rt, s.id) if r.tool == HELD]
    assert row.ok is False


async def test_a_scheduled_run_asks_through_the_bell_and_does_not_run_it_unanswered(rt):
    """(d) An unattended door with a channel (the bell and the phone) pauses
    like chat; declined (or unanswered), the tool does not run."""
    s = await _run(rt, [[_call(HELD, "h1")]], ["deny"], origin="schedule:nightly")
    assert _cards(rt, s.id) == [HELD]
    assert rt.ran[HELD] == []


@pytest.mark.parametrize("mode", ["auto", "accept_edits"])
async def test_auto_and_accept_edits_never_pass_a_held_back_tool(rt, mode):
    """(f) An Auto rule hands a call's grant without a card: never for this
    tool, whether the names were handed or not."""
    s = await _run(rt, [[_call(HELD, "h1")]], ["deny"], approval_mode=mode)
    assert _cards(rt, s.id) == [HELD]
    assert rt.ran[HELD] == []
    s2 = await _run(rt, [[_call(HELD, "h2")]], ["deny"], approval_mode=mode, allow=[])
    assert _cards(rt, s2.id) == [HELD]
    assert rt.ran[HELD] == []


async def test_a_free_spend_verdict_never_lifts_a_held_back_tool(rt):
    """(f) A tool whose own check says "free" runs without a pause and with
    that call's grant: never a held-back one. It is carded like any other
    held-back call (before, it was refused with no card at all)."""
    tool = rt.platform.registry.get(HELD)

    async def _free(args, ctx):
        return SpendCheck(ask=False, words="no charge", cost_note="free")

    tool.spend_check = _free  # type: ignore[attr-defined]
    s = await _run(rt, [[_call(HELD, "h1")]], ["once"])
    assert _cards(rt, s.id) == [HELD]
    assert rt.ran[HELD] == [{}]


def test_a_coding_sessions_shared_grant_never_covers_an_app_tool(rt):
    """(f) The shared grant of a coding session's helpers covers only tools
    whose permission key is their own name and that are not held back."""
    from iron_jarvis.harness import session_grants as sg

    sg.forget("t-pg1")
    try:
        sg.note("t-pg1", {HELD, KEPT})
        assert not sg.shareable(rt.platform.registry, HELD)
        assert sg.covers(rt.platform.registry, "t-pg1", "child", {HELD}) is False
    finally:
        sg.forget("t-pg1")


async def test_a_siblings_card_never_answers_for_a_held_back_tool_in_one_step(rt):
    """Both app tools share the ``mcp_call`` key, and a step's calls that
    need the same key used to share ONE card (named after the first call):
    the sibling's "Allow once" ran the held-back tool too. It has its own."""
    rt.by_tool.update({KEPT: "once", HELD: "deny"})
    s = await _run(rt, [[_call(KEPT, "k1"), _call(HELD, "h1")]], allow=[])
    assert sorted(_cards(rt, s.id)) == sorted([KEPT, HELD]), _cards(rt, s.id)
    assert rt.ran[KEPT] == [{}]
    assert rt.ran[HELD] == [], "the sibling's answer ran the held-back tool"


async def test_a_siblings_allow_for_this_run_never_wakes_a_held_back_card(rt):
    """Two cards in one step. The sibling's "Allow for this run" releases
    every parked card of the same permission key: never the held-back
    tool's, which waits for its own answer (given here only after the
    sibling has run, so a release would have come first)."""
    rt.by_tool.update({KEPT: "conversation", HELD: None})
    kept = rt.platform.registry.get(KEPT)
    inner = kept.execute

    async def run_then_answer_the_other_card(args, ctx):
        out = await inner(args, ctx)
        for entry in rt.platform.approvals.pending_for(ctx.session_id):
            if entry.get("tool") == HELD:
                rt.platform.approvals.resolve(str(entry.get("approval_id")), "deny")
        return out

    kept.execute = run_then_answer_the_other_card  # type: ignore[method-assign]
    s = await _run(rt, [[_call(KEPT, "k1"), _call(HELD, "h1")]], allow=[])
    assert rt.ran[KEPT] == [{}]
    assert rt.ran[HELD] == [], "the sibling's answer for this run ran the held-back tool"
    assert sorted(_cards(rt, s.id)) == sorted([KEPT, HELD])


# --------------------------------------------------------------------------- #
# PG3 leftover: a turn low from its door is not shown the code tools.
# --------------------------------------------------------------------------- #
CODE = ("run_code", "code_run", "repl")


def _tools_spy_stream():
    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        fake_stream.shown.append([t["name"] for t in tools])
        resp = LLMResponse(text="noted")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    fake_stream.shown = []
    return fake_stream


def _done(raw: str) -> dict:
    marker = "event: done\ndata: "
    assert marker in raw, raw[-500:]
    return json.loads(raw.split(marker, 1)[1].split("\n", 1)[0])


@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_a_turn_low_from_its_door_is_not_shown_the_code_tools(tmp_path, monkeypatch, lane):
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.router import RouteResult

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    have = [n for n in CODE if platform.registry.get(n) is not None]
    assert have == list(CODE), "anti-vacuity: the three code tools are registered"
    body = ChatBody(messages=[{"role": "user", "content": "run the script"}], tools=list(CODE))
    shown: list[list[str]] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        shown.append([t["name"] for t in tools])
        return RouteResult(LLMResponse(text="noted"), "mock", "mock")

    async def turn(**kw) -> dict:
        shown.clear()
        if lane == "post":
            monkeypatch.setattr(platform.router, "complete", fake_complete)
            return await run_chat_turn(platform, {}, body, **kw)
        platform.router.stream = _tools_spy_stream()
        raw = "".join([c async for c in await stream_chat_turn(platform, {}, body, **kw)])
        shown.extend(platform.router.stream.shown)
        return _done(raw)

    full = await turn()
    assert set(CODE) <= set(shown[0]), "control: a full turn is shown the three"
    low = await turn(trust="low", trust_reason="started from an inbound message")
    assert not (set(CODE) & set(shown[0])), shown[0]
    assert low["trust_note"] == "low trust: 3 tools kept away", low["trust_note"]
    assert full["trust_note"] is None


def test_a_low_coding_turn_is_not_handed_the_code_tools(tmp_path):
    """The coding plan's own low-trust filter (``harness/mode``) reads the
    same names: a code tool asked for by the message is not armed there."""
    from iron_jarvis.harness import dialects as D
    from iron_jarvis.harness import mode

    platform = create_app(str(tmp_path / "home")).state.platform

    def plan(low: bool):
        return mode.plan_tools(
            platform.registry, platform.permissions, D.CLAUDE, family="claude",
            stream=True, asked=CODE, low_trust=low,
        )

    full = plan(False)
    assert set(CODE) <= full.gate, "control: a full turn is handed them"
    low = plan(True)
    assert not (set(CODE) & low.gate), low.gate


# --------------------------------------------------------------------------- #
# A LOCKED app (Microsoft 365): "Jarvis asks you before each use of it".
# --------------------------------------------------------------------------- #
LOCKED_APP = "mailbox"
LREAD = f"mcp__{LOCKED_APP}__list_mail"  # read-only (readOnlyHint)
LDRAFT = f"mcp__{LOCKED_APP}__create_draft"  # changes something


class _Client:
    """The pack's transport: the one double. ``execute`` is the REAL tool's
    (its own chat-only and Local only refusals run)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name, args):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": f"{name} answered"}]}


def _locked_tools(platform) -> _Client:
    client = _Client()
    for remote, ann in (("list_mail", {"readOnlyHint": True}), ("create_draft", {})):
        spec = {"name": remote, "inputSchema": {"type": "object", "properties": {}},
                "annotations": ann}
        tool = MCPRemoteTool.from_spec(client, LOCKED_APP, spec)
        tool.locked = True  # what mcp/tools.mcp_tools sets for a locked row
        tool.local_only = True  # and a locked row is always kept on own models
        platform.registry.register(tool, mcp=True)
    return client


@pytest.fixture()
def lenv(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from tests.test_third_plan_g17_gate import Model

    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    client = _locked_tools(platform)

    def wire(moves):
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)

    return SimpleNamespace(platform=platform, client=client, wire=wire)


def _lbody(mode: str = "approve_for_me", local_only: bool = True, **kw):
    """A Local only chat on the user's own model, the locked app switched on."""
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody

    return ChatBody(
        messages=[ChatMessageBody(role="user", content="what is in my mail")],
        provider="ollama", model="llama3.1", approval_mode=mode, local_only=local_only,
        connectors=[LOCKED_APP], **kw,
    )


def _lcall(name: str, cid: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name=name, arguments={})]


@pytest.mark.parametrize("name", [LREAD, LDRAFT])
async def test_stream_lane_asks_before_each_use_of_a_locked_app_switched_on(lenv, name):
    """The app is switched on for the chat: each call of each of its tools
    (reading included — the promise is "each use of it") shows a card, and
    the card offers only Allow once / Deny."""
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_lcall(name, "m1"), _lcall(name, "m2")])
    out = await _stream(lenv.platform, _lbody(), ["once", "deny"])
    assert [c["tool"] for c in out["cards"]] == [name, name], out["cards"]
    assert lenv.client.calls == [name.rsplit("__", 1)[1]], "Allow once ran exactly one call"
    assert all(c.get("once_only") is True for c in out["cards"]), out["cards"]
    assert all(c.get("can_always") is False for c in out["cards"]), out["cards"]


async def test_stream_lane_a_conversation_answer_never_covers_the_next_use(lenv):
    """"Each use" means each use: a wider answer is read as once."""
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_lcall(LREAD, "m1"), _lcall(LREAD, "m2")])
    out = await _stream(lenv.platform, _lbody(), ["conversation", "deny"])
    assert [c["tool"] for c in out["cards"]] == [LREAD, LREAD], out["cards"]
    assert lenv.client.calls == ["list_mail"]


async def test_stream_lane_dont_ask_still_asks_before_a_locked_apps_use(lenv):
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_lcall(LREAD, "m1")])
    out = await _stream(lenv.platform, _lbody(mode="yolo"), ["deny"])
    assert [c["tool"] for c in out["cards"]] == [LREAD]
    assert lenv.client.calls == []


@pytest.mark.parametrize("name", [LREAD, LDRAFT])
async def test_post_lane_refuses_a_locked_apps_tool_in_words(lenv, name):
    """The POST lane (an API caller, the phone) cannot ask: it never runs a
    locked app's tool, and says why."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    lenv.wire([_lcall(name, "m1")])
    await run_chat_turn(lenv.platform, BUILTIN_PERSONAS, _lbody())
    await flush_interrupt_notes()
    assert lenv.client.calls == [], "a locked app's tool ran with nobody asked"
    with session_scope(lenv.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == name]
    if rows:  # refused through the ledger, in words
        assert rows[-1].ok is False and rows[-1].output, rows[-1].output


async def test_a_run_handed_off_from_chat_never_uses_a_locked_app(rt):
    """The Handbook: never "in an agent's task". A run whose hand-off carried
    the locked app's names and ``mcp_call`` never reaches the app: the tool
    itself refuses outside a plain chat, in words (no card for a call that
    could not run anyway)."""
    client = _locked_tools(rt.platform)
    rt.platform.router.stream = _steps([[_call(LREAD, "m1"), _call(LDRAFT, "m2")]])
    s = await rt.orch.create_session(
        "what is in my mail", AgentType.BUILDER, origin="chat",
        allow_tools=[LREAD, LDRAFT, "mcp_call"],
    )
    await rt.runtime.run(
        s, AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[LREAD, LDRAFT]),
    )
    assert client.calls == []
    rows = {r.tool: r for r in _rows(rt, s.id)}
    for name in (LREAD, LDRAFT):
        assert rows[name].ok is False
        assert CHAT_ONLY_APP_REFUSAL.format(app=LOCKED_APP) in (rows[name].output or ""), rows[name].output


async def test_a_locked_app_outside_a_local_only_chat_does_nothing(lenv):
    """With Local only off no card is needed: the app is never used — the
    tool refuses itself in words before it reaches the app."""
    from iron_jarvis.mcp.tools import LOCAL_ONLY_APP_REFUSAL
    from tests.test_third_plan_g17_gate import _stream

    lenv.wire([_lcall(LREAD, "m1")])
    await _stream(lenv.platform, _lbody(local_only=False), [])
    assert lenv.client.calls == []
    with session_scope(lenv.platform.engine) as db:
        (row,) = [r for r in db.exec(select(ToolInvocation)) if r.tool == LREAD]
    assert row.ok is False
    assert LOCAL_ONLY_APP_REFUSAL.format(app=LOCKED_APP) in (row.output or ""), row.output
