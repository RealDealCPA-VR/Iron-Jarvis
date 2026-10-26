"""v1.390.0 (the long-jobs plan's Phase 1): THE TIMELINE — the daemon records
WHEN each piece of a coding session's reply ran.

Before this a reply line kept each step's LENGTH only (``_ReplyWatch``
measured the start and dropped it) and the turn's usage as ONE total, so the
user's "where did the 117 minutes and $18 go" had no answer. Now:

* every step on the line carries ``start`` (ms from the reply's start, the
  origin of the line's ``timing.startedAt``; review: the reply's own length
  is ``timing.ms``) and its ``ms``;
* ``calls`` keeps every BILLED model call — start, length, provider, model,
  usage in the receipt's shape (tokens in/out, cache when known, cost,
  list-price flag) — and the call in flight when the reply was stopped;
* the same numbers ride the live frames (a ``model_call`` frame per call,
  ``start`` / ``ms`` on ``tool_call`` and ``helper`` frames), so a page draws
  the running reply, a rejoin and the saved line alike;
* the calls' costs add up to the reply's receipt; every bar lies inside the
  reply; reads that ran together OVERLAP instead of being laid end to end;
* a helper's row says its model and what it used (its own cost — never part
  of the reply's receipt), and its steps carry start and length.

Driven through the REAL app factory and the REAL stream lane; only the model
(the router) is a scripted double that takes time like a model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon import durable_turns as DT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness import timeline as TL
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_build_sessions_v1367 import (
    frames_of,
    messages_of,
    open_turn,
    turn_frames,
    until,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

CLAUDE = ("anthropic", "claude-sonnet-4-5")
#: How long the scripted model takes per call, and the slowed read tool.
MODEL_S = 0.2
READ_S = 0.4
#: The two files the reads take different times over (one round, together).
READ_TIMES = {"app.py": 0.2, "util.py": READ_S}
USAGE = {"input_tokens": 1200, "output_tokens": 300}


class Hang:
    """A move that says ``head`` and then waits for ``gate``."""

    def __init__(self, gate: asyncio.Event, head: str) -> None:
        self.gate, self.head = gate, head


def _is_main(system: str) -> bool:
    return "# IJ harness" in (system or "")


class Timed:
    """A scripted model that takes ``MODEL_S`` per call: ``main`` moves for
    the coding session's reply (a list of ToolCalls or a reply text; past it
    "Done."), ``helper(task, results)`` for a helper's run. ``who`` is what
    the final frame says served it; ``usage`` what each call used."""

    def __init__(self, main: list, *, helper=None, who=CLAUDE, usage=None, gate=None) -> None:
        self.provider, self.model = who
        self.main = list(main)
        self.helper = helper
        self.usage = dict(usage or USAGE)
        self.gate = gate
        self.calls: list[dict[str, Any]] = []

    async def _move(self, system, messages, tools):
        main = _is_main(system)
        results = [str(getattr(m, "content", "") or "") for m in messages or () if getattr(m, "role", "") == "tool"]
        self.calls.append({"main": main, "tools": [t["name"] for t in tools or []]})
        await asyncio.sleep(MODEL_S)
        if main:
            move = self.main.pop(0) if (self.main and tools) else "Done."
        else:
            first = next((str(getattr(m, "content", "") or "") for m in messages or () if getattr(m, "role", "") == "user"), "")
            move = self.helper(first, results) if self.helper else "Helper done."
        if callable(move):
            move = await move()
        return move

    def _resp(self, move) -> LLMResponse:
        if isinstance(move, str):
            return LLMResponse(text=move, usage=dict(self.usage))
        return LLMResponse(text="", tool_calls=list(move), usage=dict(self.usage))

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        move = await self._move(system, messages, tools)
        if isinstance(move, Hang):
            # Words first, then a model that never finishes until released.
            yield {"type": "text", "text": move.head}
            await move.gate.wait()
            move = move.head + "never."
        resp = self._resp(move)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        move = await self._move(system, messages, tools)
        return RouteResult(self._resp(move), self.provider, self.model)


def wire(app, model: Timed) -> Timed:
    app.state.platform.router.stream = model.stream
    app.state.platform.router.complete = model.complete
    return model


def slow_reads(app, monkeypatch) -> None:
    """``read_file`` takes ``READ_TIMES`` per file (two of them in one round
    run TOGETHER, and the shorter one finishes first)."""
    tool = app.state.platform.registry.get("read_file")
    real = tool.execute

    async def slow(*a, **kw):
        args = a[0] if a and isinstance(a[0], dict) else kw.get("args") or {}
        name = Path(str(args.get("path") or args.get("file_path") or "")).name
        await asyncio.sleep(READ_TIMES.get(name, READ_S))
        return await real(*a, **kw)

    monkeypatch.setattr(tool, "execute", slow)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "util.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    return folder


def new_session(app, folder: Path, who=CLAUDE, **setup) -> str:
    return S.create(app.state.platform.engine, str(folder), "", {"provider": who[0], "model": who[1], **setup}).id


async def _turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id}, timeout=60)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def _reply(app, sid: str) -> dict:
    return [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]


def _three_calls_and_reads():
    return [
        [
            ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"}),
            ToolCall(id="r2", name="Read", arguments={"file_path": "util.py"}),
        ],
        [ToolCall(id="w1", name="Write", arguments={"file_path": "made.py", "content": "y = 2\n"})],
        "Read both and made made.py.",
    ]


# --------------------------------------------------------------------------- #
# 1. the saved line: every step's start, every model call, honest numbers
# --------------------------------------------------------------------------- #


async def test_a_build_reply_line_keeps_every_steps_start_and_every_model_call(app, work, monkeypatch):
    sid = new_session(app, work)
    slow_reads(app, monkeypatch)
    model = wire(app, Timed(_three_calls_and_reads()))
    await _turn(app, sid, "read both files and make made.py", "tl-1")
    line = _reply(app, sid)
    main_calls = [c for c in model.calls if c["main"]]
    assert len(main_calls) >= 3  # three scripted calls (+ the finish check's)

    # EVERY billed model call is on the line, in order, with its numbers.
    calls = line["calls"]
    assert len(calls) == len(main_calls)
    for c in calls:
        assert (c["provider"], c["model"]) == CLAUDE
        assert isinstance(c["start"], int) and c["start"] >= 0
        assert isinstance(c["ms"], int) and c["ms"] >= int(MODEL_S * 1000) - 20
        assert c["usage"]["input_tokens"] == USAGE["input_tokens"]
        assert c["usage"]["output_tokens"] == USAGE["output_tokens"]
        assert c["usage"]["cost_usd"] > 0
        assert "list_price_equivalent" not in c["usage"]  # a metered API
        assert "state" not in c  # finished
    starts = [c["start"] for c in calls]
    assert starts == sorted(starts)

    # Every step carries its start; the reads, the write.
    steps = line["steps"]
    names = [s["name"] for s in steps]
    assert names[:3] == ["read_file", "read_file", "write_file"], names
    for s in steps:
        assert isinstance(s["start"], int) and s["start"] >= 0
        assert isinstance(s["ms"], int) and s["ms"] >= 0


async def test_the_calls_add_up_to_the_receipt_and_every_bar_lies_inside_the_reply(app, work, monkeypatch):
    sid = new_session(app, work)
    slow_reads(app, monkeypatch)
    wire(app, Timed(_three_calls_and_reads()))
    await _turn(app, sid, "read both files and make made.py", "tl-2")
    line = _reply(app, sid)
    # COST: the sum of the model calls' costs IS the reply's receipt (to the
    # receipt's own rounding, 6 decimals).
    receipt = line["usage"]
    total = sum(c["usage"]["cost_usd"] for c in line["calls"])
    assert receipt["cost_usd"] > 0
    assert abs(total - receipt["cost_usd"]) <= 1e-6, (total, receipt)
    # Tokens too.
    assert sum(c["usage"]["input_tokens"] for c in line["calls"]) == receipt["input_tokens"]
    assert sum(c["usage"]["output_tokens"] for c in line["calls"]) == receipt["output_tokens"]
    # TIME: every bar lies inside the reply's own measured length (review:
    # `timing.ms`, on the clock every offset is on; the wall-clock stamps
    # beside it tick every 15.6 ms on Windows).
    duration = line["timing"]["ms"]
    assert duration > 0
    for bar in [*line["calls"], *line["steps"]]:
        assert 0 <= bar["start"] and bar["start"] + bar["ms"] <= duration, (bar, duration)
    # The reply's own model and its steps take turns: each call ends before
    # the steps it asked for start, and nothing big hides between them.
    first, second = line["calls"][0], line["calls"][1]
    reads = [s for s in line["steps"] if s["name"] == "read_file"]
    assert first["start"] + first["ms"] <= min(r["start"] for r in reads)
    assert max(r["start"] + r["ms"] for r in reads) <= second["start"] + 5


async def test_reads_that_ran_together_overlap_on_the_axis(app, work, monkeypatch):
    sid = new_session(app, work)
    slow_reads(app, monkeypatch)
    wire(app, Timed(_three_calls_and_reads()))
    await _turn(app, sid, "read both files and make made.py", "tl-3")
    reads = [s for s in _reply(app, sid)["steps"] if s["name"] == "read_file"]
    assert len(reads) == 2
    a, b = reads
    # Each kept its OWN time: the shorter read is not stretched to the
    # pair's (their frames alone finish together, at the longer one's end) ...
    short, long_ = sorted(reads, key=lambda r: r["ms"])
    assert int(READ_TIMES["app.py"] * 1000) - 20 <= short["ms"] < int(READ_TIMES["util.py"] * 1000) - 100, reads
    assert int(READ_TIMES["util.py"] * 1000) - 20 <= long_["ms"] < int(sum(READ_TIMES.values()) * 1000) - 50, reads
    # ... and started together: laid end to end, the second would start
    # where the first ends.
    assert abs(a["start"] - b["start"]) < 100, reads
    assert max(a["start"], b["start"]) < min(a["start"] + a["ms"], b["start"] + b["ms"])


# --------------------------------------------------------------------------- #
# 2. the live frames carry the same numbers
# --------------------------------------------------------------------------- #


async def test_the_live_frames_carry_the_numbers_the_line_keeps(app, work, monkeypatch):
    sid = new_session(app, work)
    slow_reads(app, monkeypatch)
    wire(app, Timed(_three_calls_and_reads()))
    frames = await _turn(app, sid, "read both files and make made.py", "tl-4")
    line = _reply(app, sid)
    live_calls = [d for e, d in frames if e == "model_call"]
    assert [(d["start"], d["ms"], d["provider"], d["model"], d["usage"]) for d in live_calls] == [
        (c["start"], c["ms"], c["provider"], c["model"], c["usage"]) for c in line["calls"]
    ]
    started = {d["id"]: d["start"] for e, d in frames if e == "tool_call" and d.get("status") == "started"}
    finished = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert [(d["start"], d["ms"]) for d in finished] == [(s["start"], s["ms"]) for s in line["steps"]]
    for d in finished:
        assert d["start"] == started[d["id"]]
    # The turn's stored log (what a rejoin replays) holds the same frames.
    stored = [d for e, d in turn_frames(app, "tl-4") if e == "model_call"]
    assert stored == live_calls


# --------------------------------------------------------------------------- #
# 3. a subscription's money is a list-price equivalent; a stopped call is kept
# --------------------------------------------------------------------------- #


async def test_a_subscriptions_calls_are_list_price_like_its_receipt(app, work):
    who = ("claude-cli", "claude-sonnet-4-5")
    sid = new_session(app, work)
    wire(app, Timed([[ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})], "ok"],
                    who=who, usage={"input_tokens": 5000, "output_tokens": 50, "cost_usd": 0.0123}))
    await _turn(app, sid, "read app.py", "tl-5")
    line = _reply(app, sid)
    assert line["usage"]["list_price_equivalent"] is True
    for c in line["calls"]:
        assert c["usage"]["list_price_equivalent"] is True
        assert c["usage"]["cost_usd"] == pytest.approx(0.0123)
    total = sum(c["usage"]["cost_usd"] for c in line["calls"])
    assert abs(total - line["usage"]["cost_usd"]) <= 1e-6


async def test_a_stopped_reply_keeps_the_call_that_was_in_flight(app, work):
    sid = new_session(app, work)
    gate = asyncio.Event()
    wire(app, Timed([[ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})],
                     Hang(gate, "Half an ans")]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "go", "turn_id": "tl-stop"}, drop=drop, timeout=60))
    lg = await until(lambda: app.state.platform.turn_logs.get("tl-stop") if getattr(app.state.platform, "turn_logs", None) else None)
    # Wait for the SECOND call to be in flight: its `round` frame came after
    # the first call's `model_call` frame.
    await until(lambda: [e for e, _ in lg.frames].count("round") >= 2, 30)
    await asyncio.sleep(0.3)
    assert (await _asgi_post(app, "/chat/turns/tl-stop/stop"))[0] == 200
    gate.set()
    await until(lambda: lg.status != "running", 30)
    drop.set()
    await task
    line = await until(lambda: [m for m in messages_of(app, sid) if m["role"] == "assistant"], 30)
    calls = line[-1]["calls"]
    assert [c.get("state") for c in calls] == [None, "stopped"], calls
    assert calls[-1]["ms"] >= 250 and "usage" not in calls[-1]  # an unfinished call is never billed
    # It started where its `round` frame said (the page drew it working from there).
    rounds = [json.loads(d)["start"] for e, d in lg.frames if e == "round"]
    assert calls[-1]["start"] == rounds[-1]
    duration = line[-1]["timing"]["ms"]
    assert calls[-1]["start"] + calls[-1]["ms"] <= duration


# --------------------------------------------------------------------------- #
# 4. helpers: their model, their own usage, their steps on the clock
# --------------------------------------------------------------------------- #


async def test_a_helper_row_says_its_model_its_own_usage_and_its_steps_times(app, work, monkeypatch):
    sid = new_session(app, work, approval_mode="yolo")
    slow_reads(app, monkeypatch)

    def helper(task, results):
        if not results:
            return [ToolCall(id="hr-1", name="read_file", arguments={"path": "util.py"})]
        return "util.py returns 2."

    wire(app, Timed(
        [[ToolCall(id="a1", name="Agent", arguments={"description": "look", "prompt": "What does util.py return?"})],
         "It returns 2."],
        helper=helper,
    ))
    frames = await _turn(app, sid, "ask a helper", "tl-help")
    line = _reply(app, sid)
    [h] = line["helpers"]
    assert (h["provider"], h["model"]) == CLAUDE
    # Its OWN usage: two calls of the helper's run — never part of the reply's.
    assert h["usage"]["input_tokens"] == 2 * USAGE["input_tokens"]
    assert h["usage"]["output_tokens"] == 2 * USAGE["output_tokens"]
    assert h["usage"]["cost_usd"] > 0
    assert line["usage"]["input_tokens"] == USAGE["input_tokens"] * len(line["calls"])
    # Its step: start and length on the reply's clock, inside its own bar.
    [work_step] = h["work"]
    h_start, h_end = h["start"], h["end"]
    duration = line["timing"]["ms"]
    assert 0 <= h_start < h_end <= duration
    assert work_step["ms"] >= int(READ_S * 1000) - 20
    assert h_start <= work_step["start"] and work_step["start"] + work_step["ms"] <= h_end
    # The call that started it is marked (the helper's row stands in for it).
    [delegate] = [s for s in line["steps"] if s.get("helper")]
    assert delegate["name"] in ("delegate", "spawn_agent")
    # Live: the helper's frames carry the same offsets — and its model while
    # it still works (its run's own `meta`), not only once it ended.
    rows = [d for e, d in frames if e == "helper"]
    last = rows[-1]
    assert last["start"] == h_start and last["end"] == h_end and last["usage"] == h["usage"]
    assert any(d.get("model") == CLAUDE[1] and not d.get("ended") for d in rows[:-1])


# --------------------------------------------------------------------------- #
# 5. every other turn is byte-identical; every consumer ignores the new frame
# --------------------------------------------------------------------------- #


async def test_a_chat_turns_frames_are_unchanged(app, work, monkeypatch):
    """Not a harness turn: no ``model_call`` frame, no ``ms`` / ``start`` on a
    step — the chat page's frames are the v1.389.0 frames."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    wire(app, Timed([[ToolCall(id="c1", name="read_file", arguments={"path": str(work / "app.py")})], "ok"]))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="read app.py")], tools=["read_file"])
    gen = await stream_chat_turn(app.state.platform, BUILTIN_PERSONAS, body)
    raw = "".join([c async for c in gen])
    frames = frames_of(raw)
    assert "model_call" not in [e for e, _ in frames]
    for e, d in frames:
        if e == "tool_call":
            assert "ms" not in d and "start" not in d, d


def test_a_turn_log_keeps_exact_resume_positions_with_model_call_frames():
    """durable_turns.TurnLog stores the new frame like any other frame with
    no text: it is never merged, and every resume position replays the rest
    exactly (the v1.384.0 property — whose own pins, test_build_resume_v1384,
    now record real Build turns that carry these frames)."""
    lg = DT.TurnLog("t-res", "th")
    chunks = [
        DT.sse("round", {"round": 0}),
        DT.sse("token", {"text": "Rea"}),
        DT.sse("token", {"text": "ding."}),
        DT.sse("model_call", {"ms": 200, "provider": "anthropic", "model": "m", "usage": {"input_tokens": 1}, "start": 3}),
        DT.sse("model_call", {"ms": 100, "provider": "anthropic", "model": "m", "usage": {"input_tokens": 1}, "start": 220}),
        DT.sse("token", {"text": "Done"}),
    ]
    for c in chunks:
        lg.add(c)
    assert [e for e, _ in lg.frames] == ["round", "token", "model_call", "model_call", "token"]
    assert DT.position_of(lg.frames, 2) == "2:0" and DT.position_of(lg.frames, 3) == "3:0"
    # Right after the first token frame: the two calls and the last token.
    tail = lg.frames_after("1:8")
    assert [(p, e) for p, e, _d in tail] == [("2:0", "model_call"), ("3:0", "model_call"), ("4:4", "token")]
    # Mid-word: the rest of the word, then everything after it.
    tail = lg.frames_after("1:3")
    assert json.loads(tail[0][2])["text"] == "ding." and [e for _p, e, _d in tail[1:]] == ["model_call", "model_call", "token"]
    # A frame with no text takes no character offset.
    assert lg.frames_after("2:1") is None


def test_the_boot_reconcile_rebuilds_the_same_words_with_model_call_frames(app, work):
    """An interrupted record's text is rebuilt from its tokens; the new frame
    changes nothing (sessions.reconcile_interrupted_turns)."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatTurnRecord

    sid = new_session(app, work)
    S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "go", "turnId": "tl-rec"}])
    frames = [
        ["round", json.dumps({"round": 0})],
        ["token", json.dumps({"text": "Half "})],
        ["model_call", json.dumps({"ms": 5, "provider": "p", "model": "m", "usage": {}, "start": 1})],
        ["token", json.dumps({"text": "done"})],
    ]
    with session_scope(app.state.platform.engine) as db:
        db.add(ChatTurnRecord(id="tl-rec", thread_id=sid, status="interrupted", frames_json=json.dumps(frames)))
        db.commit()
    assert S.reconcile_interrupted_turns(app.state.platform.engine) == 1
    reply = _reply(app, sid)
    assert reply["content"] == "Half done" and reply["interrupted"] is True


async def test_the_browser_sidebar_says_nothing_for_the_new_frame():
    from iron_jarvis.browser.panel import PanelTurns

    said: list = []
    turns = PanelTurns.__new__(PanelTurns)

    async def emit(conn, event, data):
        said.append((event, data))

    turns.emit = emit  # type: ignore[method-assign]
    await PanelTurns._translate(turns, None, "model_call", {"ms": 1, "usage": {}})
    assert said == []


def test_the_line_store_keeps_calls_and_the_clock_never_runs_backwards():
    assert "calls" in S.LINE_KEYS
    t = [100.0]
    clock = TL.ReplyClock(100.0, now=lambda: t[0])
    t[0] = 100.5
    start = clock.step_started("x")
    t[0] = 101.0
    # A lane's measure longer than the time it could have taken is clamped.
    assert clock.step_finished("x", 9_999) == (start, 500)
    # A model_call that claims more than the whole reply starts at 0.
    entry = clock.call_seen({"ms": 50_000, "provider": "p", "model": "m", "usage": {}})
    assert entry["start"] == 0
    assert TL.call_usage("anthropic", "claude-sonnet-4-5", {"input_tokens": 10, "output_tokens": 2})["cost_usd"] > 0
    # A model nobody prices has no cost — never a made-up 0.
    assert "cost_usd" not in TL.call_usage("custom", "glm", {"input_tokens": 10, "output_tokens": 2})


def _covered(bars: list[tuple[int, int]]) -> int:
    """How much of the axis the bars cover (overlaps counted once)."""
    total, cur = 0, None
    for a, b in sorted(bars):
        if cur is None:
            cur = [a, b]
        elif a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            total += cur[1] - cur[0]
            cur = [a, b]
    return total + (cur[1] - cur[0] if cur else 0)


async def test_two_helpers_and_twenty_steps_add_up_to_the_reply(app, work, monkeypatch):
    """The plan's done-when, on a scripted model: a reply with two helpers and
    twenty tool calls — its bars (the model's calls, its steps, its helpers,
    and the time it spent getting ready before its first call, which the
    reply's card names) add up to the reply's measured time within one
    second, and its calls' costs to its receipt."""
    sid = new_session(app, work, approval_mode="yolo")
    slow_reads(app, monkeypatch)
    for i in range(16):
        (work / f"m{i}.py").write_text(f"x = {i}\n", encoding="utf-8")

    def helper(task, results):
        if not results:
            return [ToolCall(id=f"h-{abs(hash(task)) % 1000}", name="read_file", arguments={"path": "util.py"})]
        return "done."

    reads = [ToolCall(id=f"r{i}", name="Read", arguments={"file_path": f"m{i}.py"}) for i in range(16)]
    wire(app, Timed(
        [
            reads[:8],
            reads[8:],
            [
                ToolCall(id="a1", name="Agent", arguments={"description": "one", "prompt": "Part one."}),
                ToolCall(id="a2", name="Agent", arguments={"description": "two", "prompt": "Part two."}),
            ],
            [ToolCall(id="w1", name="Write", arguments={"file_path": "out.py", "content": "y = 1\n"}),
             ToolCall(id="w2", name="Write", arguments={"file_path": "out2.py", "content": "y = 2\n"})],
            "All twenty done.",
        ],
        helper=helper,
    ))
    await _turn(app, sid, "twenty steps and two helpers", "tl-20")
    line = _reply(app, sid)
    duration = line["timing"]["ms"]
    steps = [s for s in line["steps"] if not s.get("helper")]
    assert len(line["steps"]) >= 20 and len(line["helpers"]) == 2
    bars = (
        [(c["start"], c["start"] + c["ms"]) for c in line["calls"]]
        + [(s["start"], s["start"] + s["ms"]) for s in steps]
        + [(h["start"], h["end"]) for h in line["helpers"]]
    )
    for a, b in bars:
        assert 0 <= a <= b <= duration
    getting_ready = line["calls"][0]["start"]
    assert duration - _covered(bars + [(0, getting_ready)]) < 1000, (duration, _covered(bars))
    total = sum(c["usage"]["cost_usd"] for c in line["calls"])
    assert abs(total - line["usage"]["cost_usd"]) <= 1e-6


# --------------------------------------------------------------------------- #
# 6. the dashboard's fixture: a REAL recorded reply (line + its frames)
# --------------------------------------------------------------------------- #

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-timeline-v1390.json"


def _shape(line: dict, frames: list) -> dict:
    """The KEYS a reply line and its frames carry (times differ per run)."""
    return {
        "line": sorted(k for k in line if k not in ("at",)),
        "step": sorted({k for s in line.get("steps") or [] for k in s}),
        "call": sorted({k for c in line.get("calls") or [] for k in c}),
        "call_usage": sorted({k for c in line.get("calls") or [] for k in (c.get("usage") or {})}),
        "helper": sorted({k for h in line.get("helpers") or [] for k in h}),
        "helper_step": sorted({k for h in line.get("helpers") or [] for w in h.get("work") or [] for k in w}),
        "events": sorted({e for e, _d in frames}),
    }


async def test_the_dashboards_fixture_is_a_real_recorded_reply(app, work, monkeypatch):
    """dashboard/__tests__/fixtures/build-timeline-v1390.json holds a REAL
    recorded coding reply — the model's calls, two reads that ran together,
    a helper and a write — as the session saved it (`line`) and as the turn's
    log stored its frames (`frames`). The dashboard draws the live fold of
    the frames and the saved line as ONE picture. Regenerate with
    IJ_REGEN_BUILD_TIMELINE=1; otherwise a fresh recording must carry the
    fixture's shape (the times differ per run)."""
    import os

    sid = new_session(app, work, approval_mode="yolo")
    slow_reads(app, monkeypatch)

    def helper(task, results):
        if not results:
            return [ToolCall(id="hr-1", name="read_file", arguments={"path": "util.py"})]
        return "util.py returns 2."

    wire(app, Timed(
        [
            [
                ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"}),
                ToolCall(id="r2", name="Read", arguments={"file_path": "util.py"}),
            ],
            [ToolCall(id="a1", name="Agent", arguments={"description": "look", "prompt": "What does util.py return?"})],
            [ToolCall(id="w1", name="Write", arguments={"file_path": "made.py", "content": "y = 2\n"})],
            "Read both, asked a helper, and made made.py.",
        ],
        helper=helper,
    ))
    await _turn(app, sid, "read both files, ask a helper, make made.py", "tl-fx")
    msgs = messages_of(app, sid)
    question = [m for m in msgs if m.get("role") == "user"][-1]
    line = _reply(app, sid)
    frames = [[e, json.loads(d)] for e, d in app.state.platform.turn_logs.get("tl-fx").frames]
    # The round frames carry the call's start (the page draws it working).
    rounds = [d for e, d in frames if e == "round"]
    calls_ = [d for e, d in frames if e == "model_call"]
    assert rounds and all(isinstance(r.get("start"), int) for r in rounds)
    assert rounds[0]["start"] <= calls_[0]["start"]
    if os.environ.get("IJ_REGEN_BUILD_TIMELINE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps({
            "_doc": "v1.390.0: a REAL recorded coding reply (tests/test_build_timeline_v1390.py): "
                    "question + line = the session's saved lines; frames = the turn log's stored "
                    "frames (what a rejoin replays). Regenerate with IJ_REGEN_BUILD_TIMELINE=1.",
            "question": question,
            "line": line,
            "frames": frames,
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _shape(fx["line"], fx["frames"]) == _shape(line, frames)
