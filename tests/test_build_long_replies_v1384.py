"""v1.384.0 — a Build reply has NO fixed step limit.

The user's report: "after quite some time, the app just stopped". A Build
reply (Claude Code, Opus) worked for 117 minutes, met ``build_round_budget``
(60 rounds) and the last round's out-of-rounds instruction made it answer
"Nothing is built yet. I spent this turn reading the code…". Like Claude
Code, a Build reply now works until the model ends its turn or the user
presses Stop:

* ``build_round_budget`` 0 = NO LIMIT, the default; a number is honoured;
  under no limit the safety stop is ``harness.mode.BUILD_SAFETY_ROUNDS``;
* the lane's cap (``MAX_TURN_ROUNDS``) allows it — and every OTHER lane keeps
  exactly the budget it had;
* a reply that meets its limit writes a progress report (never the office
  verdict), carries ``step_limit`` on its done frame and ``stepLimit`` on its
  line (the screen's "Keep going"), and no engine "_Note: stopped after_";
* a Build turn is NEVER stopped for being unwatched (a chat turn still is);
* a 120-round reply stays inside the model's window every round, and never
  sends a ``tool_use`` without its ``tool_result``.

Driven through the REAL app factory; only the model (the router) is scripted.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import mode as M
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    until,
    wire,
)
from tests.test_build_turns_v1367 import Gated, GatedScript


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _reads(n: int, path: str = "app.py") -> list[list[ToolCall]]:
    return [[ToolCall(id=f"r{i}", name="Read", arguments={"file_path": path})] for i in range(n)]


async def _turn(app, sid: str, text: str, turn_id: str, timeout: float = 120.0):
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id}, timeout=timeout)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] in ("done", "error"), frames[-3:]
    return frames


def _ran(app, tool: str) -> int:
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope

    with session_scope(app.state.platform.engine) as db:
        return len([r for r in db.exec(select(ToolInvocation)).all() if r.tool == tool])


# --------------------------------------------------------------------------- #
# 1. no limit by default; a number is honoured; the safety stop
# --------------------------------------------------------------------------- #


async def test_a_build_reply_has_no_step_limit_by_default_and_runs_past_60(app, work):
    """OLD: the default (60) cut this reply at its 60th round."""
    assert app.state.platform.config.build_round_budget == 0
    sid = new_session(app, work)["id"]
    script = wire(app, Script(_reads(75)))
    frames = await _turn(app, sid, "read it all", "lr-default")
    done = dict(frames)["done"]
    assert "step_limit" not in done, "a reply with no limit that finished on its own met no limit"
    with_tools = [c for c in script.calls if c["tools"]]
    assert len(with_tools) == 76, "75 tool rounds and the answer, never cut at 60"
    assert _ran(app, "read_file") == 75
    line = messages_of(app, sid)[-1]
    assert line["role"] == "assistant" and line["content"] == "Done." and "stepLimit" not in line


async def test_the_turn_gets_the_safety_stop_when_there_is_no_limit(app, work, monkeypatch):
    seen: list[int] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(kw["round_budget"])
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    wire(app, Script(["Hi."]))
    sid = new_session(app, work)["id"]
    await _turn(app, sid, "hi", "lr-ceiling")
    assert seen == [M.BUILD_SAFETY_ROUNDS] == [1000]


async def test_a_number_the_user_sets_is_honoured_and_the_reply_says_it_met_it(app, work):
    """OLD: the cut reply read the OFFICE verdict ("write your final answer"),
    its done frame said nothing, and an engine note was glued under it."""
    app.state.platform.config.build_round_budget = 3
    sid = new_session(app, work)["id"]
    script = wire(app, Script(_reads(5)))
    frames = await _turn(app, sid, "keep reading", "lr-three")
    done = dict(frames)["done"]
    assert done["step_limit"] == {"limit": 3}
    with_tools = [c for c in script.calls if c["tools"]]
    assert len(with_tools) == 3
    final = script.calls[-1]
    assert final["tools"] == [], "the last call writes the report, it runs no tools"
    last_user = [m for m in final["messages"] if m.role == "user"][-1].content
    assert "reached this reply's step limit (3 steps)" in last_user
    assert "Keep going" in last_user and "Never say that nothing was done" in last_user
    assert chat_turn.OUT_OF_ROUNDS_INSTRUCTION not in last_user
    line = messages_of(app, sid)[-1]
    assert line["stepLimit"] == {"limit": 3, "safety": False}
    assert "_Note: stopped after" not in line["content"], "no engine note under a harness reply"
    assert done["escalate"] is False


async def test_the_safety_stop_is_said_as_the_safety_stop(app, work, monkeypatch):
    monkeypatch.setattr(M, "BUILD_SAFETY_ROUNDS", 4)
    sid = new_session(app, work)["id"]
    wire(app, Script(_reads(6)))
    frames = await _turn(app, sid, "keep reading", "lr-safety")
    assert dict(frames)["done"]["step_limit"] == {"limit": 4}
    assert messages_of(app, sid)[-1]["stepLimit"] == {"limit": 4, "safety": True}


@pytest.mark.parametrize("value, used, no_limit", [
    (0, 1000, True), (None, 1000, True), ("x", 1000, True),
    (60, 60, False), (500, 500, False), (5000, 1000, False), (-3, 1, False),
])
def test_the_setting_as_a_turn_reads_it(value, used, no_limit):
    class C:
        build_round_budget = value

    assert build_routes.build_step_limit(C()) == (used, no_limit)


def test_the_settings_row_offers_no_limit_and_it_is_the_default(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.settings import schema

    d = schema.get("build_round_budget")
    assert "0 = no limit" in d.help and "1,000" in d.help and "Keep going" in d.help
    for ok in (0, 1, 60, 1000):
        d.check(ok)
    for bad in (-1, 1001):
        with pytest.raises(ValueError):
            d.check(bad)
    client = TestClient(create_app(str(tmp_path / "home")))
    assert client.get("/settings").json()["settings"]["build_round_budget"] == 0
    assert client.put("/settings/values", json={"values": {"build_round_budget": 1000}}).status_code == 200
    assert client.put("/settings/values", json={"values": {"build_round_budget": 1001}}).status_code >= 400


# --------------------------------------------------------------------------- #
# 2. the lane's cap allows the ceiling; every other lane keeps its budget
# --------------------------------------------------------------------------- #


def test_the_lane_cap_allows_the_build_ceiling_exactly():
    assert chat_turn.MAX_TURN_ROUNDS == M.BUILD_SAFETY_ROUNDS == 1000
    assert chat_turn.turn_round_budget(1000) == 1000
    with pytest.raises(ValueError):
        chat_turn.turn_round_budget(1001)


def test_every_other_lane_keeps_exactly_the_budget_it_had():
    import inspect

    from iron_jarvis.agents import runtime
    from iron_jarvis.harness_bench import runners

    # plain chat, office and browser-agent turns (no caller budget)
    assert chat_turn._round_budget(set()) == chat_turn._MAX_TOOL_ROUNDS == 6
    assert chat_turn._round_budget({"write_document"}) == chat_turn._DOC_TOOL_ROUNDS == 12
    assert chat_turn._round_budget({"browser_click"}) == chat_turn._BROWSER_TOOL_ROUNDS == 24
    # a harness caller with no budget, and the benchmark's explicit one
    assert chat_turn.harness_turn_knobs("auto", None, False) == (60, True)
    assert runners.DEFAULT_ROUNDS == 60
    assert chat_turn.harness_turn_knobs("auto", 60, False) == (60, True)
    # the agent runtime sizes its loop from the run's own max_steps — the
    # chat lane's cap never reaches it
    rt = inspect.getsource(runtime)
    assert "MAX_TURN_ROUNDS" not in rt and "turn_round_budget" not in rt and "build_round_budget" not in rt
    # no caller budget: no step_limit key, ever (byte-identical frames)
    assert chat_turn.step_limit_receipt(None, True, 6) is None
    assert chat_turn.step_limit_receipt(60, False, 60) is None
    assert chat_turn.step_limit_receipt(60, True, 60) == {"limit": 60}
    # the office wording is unchanged for every non-harness turn
    assert chat_turn._out_of_rounds_instruction(set()) == chat_turn.OUT_OF_ROUNDS_INSTRUCTION
    assert (chat_turn._out_of_rounds_instruction({"browser_click"})
            == chat_turn.OUT_OF_STEPS_BROWSER_INSTRUCTION)


# --------------------------------------------------------------------------- #
# 3. never stopped for being unwatched (a chat turn still is)
# --------------------------------------------------------------------------- #


async def test_a_build_turn_nobody_watches_for_hours_keeps_working(app, work):
    """OLD: the orphan guard stopped it (status "stopped", the second half
    never streamed)."""
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "first half, ", "second half.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "lr-orphan"}, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("lr-orphan")
    await until(lambda: not lg.watched())
    lg._last_seen -= dt.ORPHAN_STOP_S + 60  # an hour and a minute with nobody looking
    assert lg.unwatched_for() > dt.ORPHAN_STOP_S
    gate.set()
    await until(lambda: lg.status != "running", 30)
    assert lg.status == "done"
    assert messages_of(app, sid)[-1]["content"] == "first half, second half."


async def test_a_chat_turn_nobody_watches_is_still_stopped_by_the_guard(tmp_path):
    from tests.test_durable_chat_turns_v1336 import MSG, _gated_stream, _open

    app = create_app(str(tmp_path / "home"))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "lr-chat-orphan"}, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("lr-chat-orphan")
    await until(lambda: not lg.watched())
    lg._last_seen -= dt.ORPHAN_STOP_S + 60
    gate.set()
    await until(lambda: lg.status != "running", 30)
    assert lg.status == "stopped"


def test_the_build_lane_is_handed_the_never_predicate_and_chat_keeps_the_guard():
    import inspect

    src = inspect.getsource(build_routes.start_build_turn)
    assert "should_stop=_never_orphaned" in src
    assert "stop_predicate" not in src
    assert build_routes._never_orphaned() is False
    from iron_jarvis.daemon.routes import chat as chat_routes

    assert "_durable.stop_predicate" in inspect.getsource(chat_routes)


# --------------------------------------------------------------------------- #
# 4. a 120-round reply stays inside the window and keeps every tool pair
# --------------------------------------------------------------------------- #


def _pairs_intact(messages) -> bool:
    open_ids: set[str] = set()
    for m in messages:
        if m.role == "assistant":
            for tc in m.tool_calls or []:
                open_ids.add(tc.id)
        elif m.role == "tool":
            if m.tool_call_id not in open_ids:
                return False  # a result whose tool_use was dropped
    # every tool_use has its result in the same request
    result_ids = {m.tool_call_id for m in messages if m.role == "tool"}
    return open_ids <= result_ids


async def test_a_120_round_reply_fits_the_window_every_round_and_keeps_its_pairs(app, work):
    window = 24_000
    app.state.platform.config.model_context_windows = {"anthropic::claude-sonnet-4-5": window}
    body = "\n".join(f"line_{i} = {i * 7}  # a fairly ordinary line of python" for i in range(150))
    for i in range(120):
        (work / f"f{i}.py").write_text(body, encoding="utf-8")
    sid = new_session(app, work)["id"]
    moves = [[ToolCall(id=f"r{i}", name="Read", arguments={"file_path": f"f{i}.py"})] for i in range(120)]
    script = wire(app, Script(moves))
    frames = await _turn(app, sid, "read every file", "lr-120", timeout=300)
    assert dict(frames)["done"].get("step_limit") is None
    sent = [c for c in script.calls if c["tools"]]
    assert len(sent) == 121
    raw_total = sum(len(f) for f in [body] * 120)
    assert estimate_tokens(body * 120) > window * 4, "the reads alone are far past the window"
    biggest = 0
    for i, call in enumerate(sent):
        text = call["system"] + "\n" + "\n".join(m.content or "" for m in call["messages"])
        biggest = max(biggest, estimate_tokens(text))
        assert estimate_tokens(text) <= window, f"round {i} sent past the window"
        assert _pairs_intact(call["messages"]), f"round {i} split a tool pair"
        # the question is always there
        assert any(m.role == "user" and m.content == "read every file" for m in call["messages"])
    assert raw_total > 0 and biggest > window // 4, "the turn really filled the window"
    assert _ran(app, "read_file") == 120
