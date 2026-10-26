"""v1.392.0 review (the long-jobs plan's Phase 3, never stalls silently): the
defects the reviewer found in the doer's cut, each pinned red first.

1. The turn's progress mark (``core/progress``) rode into EVERY event
   handler's context copy, like the hold and the account pin did before
   v1.372.0's review: work a handler started from a coding reply's event
   (a reflex rule's run, the skill-distill sweep) reset that reply's stall
   clock with its own output. ``core/events._drop_build_turn_scope`` drops
   it now.
2. A model that said nothing for ten minutes was never NAMED: the watch read
   the provider off a ``meta`` frame no code emits, so every such notice said
   "the model's answer". The watch reads the turn's held model (and every
   model call the reply makes) now.

Every interval is read through ``harness.stall.CLOCK`` (moved by the test).
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from iron_jarvis.core import procs, progress
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(stall, "CLOCK", c)
    monkeypatch.setattr(stall, "TICK_S", 0.02)
    return c


def _stalls(app, turn: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn) if e == "stall"]


# --------------------------------------------------------------------------- #
# 1. the progress mark never rides into an event handler's work
# --------------------------------------------------------------------------- #


async def test_an_event_handler_never_carries_the_turns_progress_mark():
    """A handler runs in a COPY of the publisher's context (``to_thread``);
    the turn's hold, pin and command rule are dropped there (v1.372.0 /
    v1.381.0) — and now its progress mark: a reflex run or a sweep a handler
    starts is not the reply, and its output must never move the reply's
    stall clock."""
    bus = EventBus()
    seen: list[object] = []
    bus.add_handler(lambda event: seen.append(progress.current()))
    marks: list[int] = []
    with progress.scope(lambda: marks.append(1)):
        await bus.publish(EventType.SESSION_COMPLETED, {"x": 1}, session_id="s1")
        # The turn itself keeps its mark.
        assert progress.current() is not None
    assert await until(lambda: seen, 10)
    assert seen == [None], "an event handler ran with the coding reply's progress mark"


async def test_a_single_dispatched_handler_never_carries_it_either():
    bus = EventBus()
    seen: list[object] = []

    def handler(event):
        seen.append(progress.current())

    with progress.scope(lambda: None):
        await EventBus._dispatch(handler, object())
    assert seen == [None]


# --------------------------------------------------------------------------- #
# 2. a silent model is named
# --------------------------------------------------------------------------- #


async def test_a_model_that_says_nothing_for_ten_minutes_is_named_by_the_sessions_model(app, work, clock):
    """The session runs on Anthropic (the test's CLAUDE pair): ten minutes of
    silence before its first word names it — no ``meta`` frame exists."""
    gate = asyncio.Event()
    sid = new_session(app, work, approval_mode="yolo")["id"]

    async def hold(_script):
        await gate.wait()
        return "Finally."

    wire(app, Script([hold]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "think", "turn_id": "bt-named"}, drop=drop))
    try:
        assert await until(lambda: any(e == "round" for e, _ in turn_frames(app, "bt-named")), 30)
        clock.advance(601)
        got = await until(lambda: _stalls(app, "bt-named"), 30)
        assert got, "no stall notice"
        assert got[0]["words"] == "No progress for 10 minutes — waiting on Anthropic's answer"
        gate.set()
        lg = turn_log(app, "bt-named")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        gate.set()
        drop.set()
        await task


def test_the_watch_names_the_model_that_answered_the_reply_so_far(clock):
    """A ``model_call`` frame (the Timeline's) names who answered; the next
    silence waits on THAT model."""
    w = stall.StallWatch(provider="codex-cli")
    clock.advance(601)
    assert w.check()["words"].endswith("waiting on Codex's answer")
    w.saw("event: model_call\ndata: " + json.dumps({"provider": "claude-cli", "model": "opus"}) + "\n\n")
    assert w.ended() == {"over": True}
    clock.advance(601)
    assert w.check()["words"].endswith("waiting on Claude Code's answer")


# --------------------------------------------------------------------------- #
# 4. every NEW sentence a person reads says "coding session"
# --------------------------------------------------------------------------- #
#
# v1.389.0 named the view "Coding sessions" (dashboard lib/buildWords
# CODING_SESSION); "Build session" stays only where an EXISTING fixture holds
# it equal (VOCABULARY.md). Phase 3's bell headline, tray notice and phone
# words were new and said "Build session".


def _fixture_case(name: str) -> dict:
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "dashboard" / "__tests__" / "fixtures" / "one-notice-v13600.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return next(c for c in cases if c["case"] == name)


def test_the_stall_notice_and_the_phone_say_coding_session():
    from iron_jarvis.comm import notifier as N

    assert N.CODING_SESSION == "coding session"
    assert N.BUILD_STALLED_HEADLINE == "Your coding session has stopped making progress"
    stalled = _fixture_case("build-turn-stalled")
    assert stalled["title"] == N.BUILD_STALLED_HEADLINE
    assert N.format_event(stalled["event"]).startswith("Your coding session has stopped making progress — ")
    payload = {"approval_id": "apr_x", "tool": "shell", "surface": "build", "title": "Fix the nav bar",
               "thread_id": "bs1", "timeout_s": 0}
    assert N.build_ask_words(payload).startswith("Your coding session “Fix the nav bar” is waiting for your OK to ")
    assert N.build_ask_words({**payload, "title": ""}).startswith("Your coding session is waiting")
    line = N.format_event({"type": "approval.requested", "payload": payload})
    for said in (line, N.format_event(stalled["event"]), N.build_ask_words(payload)):
        assert "Build session" not in said, said


def test_the_tray_says_coding_session_too():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "desktop" / "main.js").read_text(encoding="utf-8")
    block = src[src.index('if (entry.type === "build.turn_stalled")'):src.index('if (entry.type === "build.turn_finished")')]
    assert 'title: "Your coding session has stopped making progress"' in block
    assert "Build session" not in block.split("title:", 1)[1].split("\n", 1)[0]


# --------------------------------------------------------------------------- #
# 7. Stop the step inside a batch of reads that run together
# --------------------------------------------------------------------------- #


async def test_stop_the_step_in_a_batch_of_reads_keeps_every_result_and_its_order(
    app, work, clock, monkeypatch,
):
    """Two reads run TOGETHER (v1.311.0's batch); one hangs and is stopped:
    the other's real result is kept, and the model is sent BOTH tool results,
    each after the call that asked for it, in the order it called them — the
    unit rule — the stopped one in plain words."""
    app.state.platform.config.tool_call_timeout_s = 0
    (work / "slow.txt").write_text("never read\n", encoding="utf-8")
    (work / "fast.txt").write_text("fast words\n", encoding="utf-8")
    tool = app.state.platform.registry.get("read_file")
    real = tool.execute
    entered = asyncio.Event()

    async def execute(args, ctx):
        if str(args.get("path") or args.get("file_path") or "").endswith("slow.txt"):
            entered.set()
            await asyncio.Event().wait()
        return await real(args, ctx)

    monkeypatch.setattr(tool, "execute", execute)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    script = wire(app, Script([
        [
            ToolCall(id="r1", name="Read", arguments={"file_path": str(work / "slow.txt")}),
            ToolCall(id="r2", name="Read", arguments={"file_path": str(work / "fast.txt")}),
        ],
        "Read what I could.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "read both", "turn_id": "bt-batch-read"},
                                         drop=drop, timeout=120))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        # The fast read finished while the slow one hangs.
        assert await until(lambda: [d for e, d in turn_frames(app, "bt-batch-read")
                                    if e == "tool_call" and d.get("id") == "r2" and d.get("status") == "finished"]
                           or stall.running_steps("bt-batch-read") == ["r1"], 30)
        clock.advance(601)
        got = await until(lambda: _stalls(app, "bt-batch-read"), 30)
        assert got and got[0]["step"] == {"id": "r1", "name": "read_file"}
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "r1"})
        assert status == 200 and out["step"] is True, out
        lg = turn_log(app, "bt-batch-read")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
    done = dict(turn_frames(app, "bt-batch-read"))["done"]
    assert done["reply"] == "Read what I could."
    sent = script.calls[1]["messages"]
    tool_msgs = [(m.tool_call_id, m.content) for m in sent if getattr(m, "role", "") == "tool"]
    assert [t for t, _ in tool_msgs] == ["r1", "r2"]
    assert "stopped by the user" in tool_msgs[0][1]
    assert "fast words" in tool_msgs[1][1]
    for i, m in enumerate(sent):
        if getattr(m, "role", "") == "tool":
            asked = [p for p in sent[:i] if getattr(p, "role", "") == "assistant"
                     and any(getattr(c, "id", "") == m.tool_call_id for c in (getattr(p, "tool_calls", None) or []))]
            assert asked, "a tool result was sent without the call it answers"


# --------------------------------------------------------------------------- #
# 6. every other caller of core/procs runs exactly as before
# --------------------------------------------------------------------------- #


def test_a_command_outside_a_watched_turn_or_fed_input_takes_the_old_path(monkeypatch):
    """Only a coding reply's command (progress armed) with nothing to feed it
    reads its output piece by piece; every other caller is ``communicate`` /
    ``wait`` as it always was."""
    moving: list[str] = []
    monkeypatch.setattr(procs, "_communicate_moving", lambda *a, **k: moving.append("run") or (_ for _ in ()).throw(
        AssertionError("the piece-by-piece read ran outside a watched turn")))
    monkeypatch.setattr(procs, "_wait_moving", lambda *a, **k: moving.append("files") or (_ for _ in ()).throw(
        AssertionError("the file watch ran outside a watched turn")))
    cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read() or 'none')"]
    assert procs.run(cmd, timeout=60).stdout.strip() == b"none"
    assert procs.run_to_files(cmd, timeout=60).stdout.strip() == b"none"
    with progress.scope(lambda: None):
        # Fed input: communicate writes it (the piece-by-piece read has no stdin).
        assert procs.run(cmd, timeout=60, input=b"fed").stdout.strip() == b"fed"
    assert moving == []


# --------------------------------------------------------------------------- #
# 5. the summary mid-reply is written under the reply's hold and Local only
# --------------------------------------------------------------------------- #


async def test_the_summary_is_asked_of_the_replys_own_model_pinned_held_and_local_only(app, work):
    """The summarizer's call goes through the router PINNED (no failover),
    inside the turn's model hold (the session's pair, which binds every call)
    and its Local only scope — the claims the module doc makes, read off the
    context the call really runs in."""
    from iron_jarvis.providers import egress
    from iron_jarvis.providers.hold import current_hold
    from tests.test_build_reply_summary_v1392 import LongReply, _run, _wire

    seen: list[dict] = []

    class Spied(LongReply):
        async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None,
                           task_class=None, **kw):
            hold = current_hold()
            seen.append({
                "pin": kw.get("pin"), "provider": provider, "model": model,
                "hold": (hold.provider, hold.model) if hold is not None else None,
                "local_only": egress.LOCAL_ONLY.get().on,
            })
            return await super().complete(provider=provider, model=model, system=system, messages=messages,
                                          tools=tools, task_class=task_class, **kw)

    local = ("ollama", "qwen3-coder")
    script = _wire(app, Spied(app, rounds=120, who=local))
    sid = new_session(app, work, approval_mode="yolo", provider=local[0], model=local[1])["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True})
    assert status == 200, view
    await _run(app, sid, "bt-sum-held")
    assert script.summaries, "the reply never summarized itself"
    summ = [s for s in seen if s["pin"] is not None]
    assert summ and all(s["pin"] is True for s in summ), seen[:3]
    assert all((s["provider"], s["model"]) == local for s in summ)
    assert all(s["hold"] == local for s in summ), "the summary ran outside the turn's model hold"
    assert all(s["local_only"] is True for s in summ), "the summary ran outside the turn's Local only"


# --------------------------------------------------------------------------- #
# 3. a step its own deadline ends is not announced as a stall
# --------------------------------------------------------------------------- #
#
# Every tool call of a coding reply runs under the registry's deadline
# (`config.tool_call_timeout_s`, 600 s by default — the stall window). The
# watch ticked from the `started` frame and the deadline from a moment later,
# so a hung step was announced (bell, tray) a few milliseconds before its own
# deadline ended it — a notice whose "Stop the step" could only answer "That
# step had already finished". The watch now holds a notice for a step whose
# deadline ends it at the window, and says it only when the step outlives its
# deadline (something the deadline could not stop).


def _started(cid: str, name: str = "shell", args: dict | None = None) -> str:
    frame = {"id": cid, "name": name, "status": "started", "args": args or {"command": "pytest"}}
    return "event: tool_call\ndata: " + json.dumps(frame) + "\n\n"


def test_a_step_its_deadline_ends_at_the_window_is_not_announced_until_it_outlives_it(clock):
    w = stall.StallWatch(step_deadline=lambda name: 600.0)
    w.saw(_started("c1"))
    clock.advance(601)
    assert w.check() is None, "a step its own deadline is ending was announced as a stall"
    clock.advance(stall.DEADLINE_GRACE_S)
    notice = w.check()
    assert notice and notice["step"] == {"id": "c1", "name": "shell"}


def test_a_deadline_longer_than_the_window_does_not_hold_the_notice(clock):
    w = stall.StallWatch(step_deadline=lambda name: 3600.0)
    w.saw(_started("c1"))
    clock.advance(601)
    assert w.check() is not None


def test_no_deadline_and_the_model_are_announced_at_the_window(clock):
    w = stall.StallWatch(step_deadline=lambda name: None)
    w.saw(_started("c1"))
    clock.advance(601)
    assert w.check() is not None
    m = stall.StallWatch(step_deadline=lambda name: 600.0)
    clock.advance(601)
    assert m.check() is not None  # no step runs: the model is what it waits on


def test_a_resolver_that_raises_never_holds_a_notice(clock):
    def boom(name):
        raise RuntimeError("x")

    w = stall.StallWatch(step_deadline=boom)
    w.saw(_started("c1"))
    clock.advance(601)
    assert w.check() is not None


def _hang(pidfile) -> str:
    return (
        f'"{sys.executable}" -c "import os,time,pathlib; '
        f"pathlib.Path('{pidfile.as_posix()}').write_text(str(os.getpid())); time.sleep(600)\""
    )


async def test_a_hung_step_under_the_default_deadline_is_said_only_once_it_outlives_it(
    app, work, tmp_path, clock, monkeypatch,
):
    """End to end on the real app, default `tool_call_timeout_s` (600): ten
    minutes and one second of a hung command is not yet a stall (its deadline
    ends it then); past the deadline and its grace it is."""
    assert app.state.platform.config.tool_call_timeout_s == 600
    seen = {"n": 0}
    real = stall.StallWatch.check

    def counted(self):
        seen["n"] += 1
        return real(self)

    monkeypatch.setattr(stall.StallWatch, "check", counted)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    pidfile = tmp_path / "hang.pid"
    wire(app, Script([[ToolCall(id="c1", name="Bash", arguments={"command": _hang(pidfile)})], "Moved on."]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-dl"}, drop=drop, timeout=120))
    try:
        assert await until(lambda: pidfile.exists() and pidfile.read_text(encoding="utf-8").strip(), 60)
        clock.advance(601)
        start = seen["n"]
        assert await until(lambda: seen["n"] >= start + 5, 30)
        assert _stalls(app, "bt-dl") == [], "announced a step its own deadline is about to end"
        clock.advance(stall.DEADLINE_GRACE_S)
        got = await until(lambda: _stalls(app, "bt-dl"), 30)
        assert got and got[0]["step"]["id"] == "c1"
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "c1"})
        assert status == 200, out
        lg = turn_log(app, "bt-dl")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
