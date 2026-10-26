"""v1.391.0 REVIEW (the long-jobs plan's Phase 2): the defects a reviewer found
in "a coding reply carries on by itself after a restart", each pinned by a
test that fails on the doer's code.

Driven like ``test_build_resume_v1391.py``: the real app factory twice on one
home (the "restart"), the real stream lane and tool registry; only the model
(the router) is a scripted double. These pins keep their own fixtures (the
v1.390.0 ruff F811 lesson).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import RunTranscript
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    messages_of,
    new_session,
    open_turn,
    until,
    wire,
)


def _write(cid: str, name: str, content: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="Write", arguments={"file_path": name, "content": content})]


class _Hold:
    """A model call that never answers until ``gate`` is set."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.reached = asyncio.Event()

    async def __call__(self, script):
        self.reached.set()
        await self.gate.wait()
        return "Never said."


def _records(app) -> list[RunTranscript]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(RunTranscript)).all())
        for r in rows:
            db.expunge(r)
        return rows


def _reboot(home: str):
    """The second process: a new app on the same home, its boot reconciles
    run (the lifespan's own order), its resume pass NOT yet."""
    app = create_app(home)
    engine = app.state.platform.engine
    from iron_jarvis.tools.registry import reconcile_interrupted_tool_calls

    reconcile_interrupted_tool_calls(engine)
    durable_turns.reconcile_interrupted_turns(engine)
    S.reconcile_interrupted_turns(engine)
    return app


async def _run_until_held(app, sid: str, moves: list, turn_id: str, text: str):
    """Run a reply through ``moves`` (the last one a :class:`_Hold`), stop the
    app (the update's tidy stop) while the held model call waits."""
    hold = next(m for m in moves if isinstance(m, _Hold))
    wire(app, Script(moves))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": text, "turn_id": turn_id},
                                             drop=drop, timeout=60))
    await asyncio.wait_for(hold.reached.wait(), 30)
    await app.state.platform.turn_logs.shutdown()
    drop.set()
    hold.gate.set()
    await asyncio.wait_for(follower, 30)


async def _resumed(app, sid: str):
    logs = app.state.platform.turn_logs
    new = await until(lambda: next((lg for lg in list(logs._logs.values()) if lg.thread_id == sid), None))
    assert new is not None, "nothing carried the reply on"
    await until(lambda: new.status != "running", 30)
    return new


def _reply_line(app, sid: str, turn_id: str) -> dict:
    return next(m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == turn_id)


@pytest.fixture()
def home(tmp_path) -> str:
    return str(tmp_path / "home")


@pytest.fixture()
def unchecked(tmp_path):
    """A folder whose finish check is OFF (the script stays as written)."""
    folder = tmp_path / "work"
    folder.mkdir()
    harness = folder / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


@pytest.fixture()
def checked(tmp_path):
    """A folder whose finish check is ON (the default): no tests, so the check
    runs nothing — its note lists the files the reply made that the request
    does not name."""
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


# --------------------------------------------------------------------------- #
# 1. the finish check of a resumed reply counts the WHOLE reply, once
# --------------------------------------------------------------------------- #


async def test_a_resumed_reply_is_finish_checked_for_the_files_its_cut_part_made(home, checked):
    """The cut part made one.txt and two.txt; the resumed part makes nothing
    and ends. The finish check must still run, over the WHOLE reply from the
    original question (v1.386.0's carry-on rule) — it used to measure from the
    resumed turn's start, saw no change, and the reply ended unchecked."""
    first = create_app(home)
    sid = new_session(first, checked)["id"]
    await _run_until_held(first, sid, [_write("c1", "one.txt", "1"), _write("c2", "two.txt", "2"), _Hold()],
                          "bt-1", "make some files")

    second = _reboot(home)
    script = wire(second, Script(["Both are there.", "Nothing needed changing."]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    new = await _resumed(second, sid)
    assert new.status == "done", new.frames[-3:]
    assert len(script.calls) == 2, "the finish check never ran for the resumed reply"
    note = script.calls[1]["messages"][-1].content
    assert note.startswith("FINISH CHECK.")
    assert "one.txt" in note and "two.txt" in note, note
    assert "finish" in _reply_line(second, sid, new.turn_id)


async def test_a_reply_already_finish_checked_before_the_stop_is_not_checked_again(home, checked):
    """The cut part ended once, got its ONE finish note, and was answering it
    when the app stopped. The note says it "comes once per reply": the resumed
    part must not get a second one."""
    first = create_app(home)
    sid = new_session(first, checked)["id"]
    await _run_until_held(first, sid, [_write("c1", "one.txt", "1"), "All done.", _Hold()], "bt-1",
                          "make a file")
    kept = json.loads(next(r for r in _records(first) if r.run_id == "build-turn:bt-1").messages_json)
    assert kept[-1]["role"] == "user" and kept[-1]["content"].startswith("FINISH CHECK."), (
        "the cut part had its finish note"
    )

    second = _reboot(home)
    script = wire(second, Script([_write("c2", "two.txt", "2"), "Fixed it: two.txt is there too.", "x"]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    new = await _resumed(second, sid)
    assert new.status == "done", new.frames[-3:]
    sent = [m.content for m in script.calls[-1]["messages"] if m.role == "user"]
    assert sum(1 for c in sent if str(c).startswith("FINISH CHECK.")) == 1, (
        "a second finish note was sent to the resumed reply"
    )
    assert len(script.calls) == 2


# --------------------------------------------------------------------------- #
# 2. a reply waiting on the user through its HELPER stays waiting
# --------------------------------------------------------------------------- #


async def test_a_reply_whose_helper_waits_on_the_user_is_never_carried_on_by_itself(home, tmp_path):
    """A helper's own question (its shell asks) is drawn on its row in the
    Build session: the reply is parked on the user exactly as with its own
    card. The record must say ``waiting`` while it does, so a restart then
    leaves the reply as it is — never carried on (decision 2: "a reply that
    was waiting on you stays waiting"), never a second helper started for a
    question the user had not answered yet."""
    from tests.test_harness_helpers_v1372 import Model
    from tests.test_harness_helpers_v1372 import wire as wire_helpers

    work = tmp_path / "work"
    work.mkdir()
    first = create_app(home)
    sid = new_session(first, work)["id"]  # approve_for_me: the helper's shell asks

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="h-sh", name="shell", arguments={"command": "echo helper-ok"})]
        return "the command printed helper-ok"

    wire_helpers(first, Model(main=[[ToolCall(id="k1", name="Agent", arguments={
        "description": "d", "prompt": "Run echo."})], "ok"], helper=helper))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(first, sid, {"text": "go", "turn_id": "bt-h"},
                                             drop=drop, timeout=60))
    logs = await until(lambda: getattr(first.state.platform, "turn_logs", None), 30)
    await until(lambda: logs.get("bt-h") is not None and any(
        e == "helper" and json.loads(d).get("state") == "waiting" for e, d in logs.get("bt-h").frames), 30)
    rec = await until(lambda: next(
        (r for r in _records(first) if r.run_id == "build-turn:bt-h" and r.waiting), None), 5)
    assert rec is not None, "the helper's question parks the reply: its record must say so"
    await logs.shutdown()
    drop.set()
    await asyncio.wait_for(follower, 30)

    second = _reboot(home)
    script = wire(second, Script(["Never asked."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []


def test_a_helpers_answered_or_ended_question_no_longer_parks_the_reply():
    """The fold: a helper row's ``ask`` parks the reply; ``ask: null`` (it was
    answered) or the helper's end lets it go; the reply's own card still
    parks it on its own."""
    fold = build_routes._asks_after
    asks: set[str] = set()
    helper = lambda **kw: ("helper", json.dumps({"id": "k1", "state": "running", **kw}))  # noqa: E731
    assert fold(asks, [helper()]) is False
    assert fold(asks, [helper(state="waiting", ask={"id": "ap-1", "tool": "shell"})]) is True
    assert fold(asks, [helper(ask=None)]) is False
    assert fold(asks, [helper(state="waiting", ask={"id": "ap-2", "tool": "shell"})]) is True
    assert fold(asks, [helper(state="failed", ended=True)]) is False
    assert fold(asks, [("approval", json.dumps({"id": "ap-3", "tool": "write_file"}))]) is True
    assert fold(asks, [helper(state="waiting", ask={"id": "ap-4"}), ("approval_resolved", json.dumps({"id": "ap-3"}))]) is True
    assert fold(asks, [helper(ask=None)]) is False


# --------------------------------------------------------------------------- #
# 3. a reply cut off AGAIN while it carried on
# --------------------------------------------------------------------------- #


async def _cut_twice(home, work, *, second_moves: list):
    """X is cut off; the boot carries it on as a new turn, which is cut off
    in turn while ``second_moves`` (ending on a :class:`_Hold`) run."""
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _run_until_held(first, sid, [("I will write one.", _write("c1", "one.txt", "1")), _Hold()],
                          "bt-1", "make files")
    second = _reboot(home)
    hold = next(m for m in second_moves if isinstance(m, _Hold))
    wire(second, Script(second_moves))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await asyncio.wait_for(hold.reached.wait(), 30)
    logs = second.state.platform.turn_logs
    turn = next(lg.turn_id for lg in list(logs._logs.values()) if lg.thread_id == sid)
    await logs.shutdown()
    hold.gate.set()
    return sid, turn


async def test_a_second_resume_sends_the_question_once_and_no_fragment_of_the_first_cut(home, unchecked):
    sid, _ = await _cut_twice(home, unchecked, second_moves=[("Now two.", _write("c2", "two.txt", "2")), _Hold()])
    third = _reboot(home)
    script = wire(third, Script(["All there."]))
    assert await build_routes.resume_after_restart(third.state.d) == [sid]
    await until(lambda: script.calls, 30)
    sent = script.calls[0]["messages"]
    users = [m.content for m in sent if m.role == "user"]
    assert users.count("make files") == 1, [m.role + ": " + (m.content or "")[:40] for m in sent]
    # The first cut reply's words ride its own round (with its call), never
    # again as a reply of their own between two copies of the question.
    assert [m for m in sent if m.role == "assistant" and m.content == "I will write one." and not m.tool_calls] == []
    await until(lambda: not third.state.platform.turn_logs.running(), 30)


async def test_a_second_resume_keeps_the_questions_files(home, unchecked):
    """The question's own uploads ride every resume of it (the resume
    question line carries none; the ORIGINAL question does)."""
    notes = unchecked / "notes.txt"
    notes.write_text("the codeword is heron", encoding="utf-8")
    first = create_app(home)
    sid = new_session(first, unchecked)["id"]
    hold0 = _Hold()
    wire(first, Script([_write("c1", "one.txt", "1"), hold0]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(first, sid, {
        "text": "use my notes", "turn_id": "bt-1", "attachments": [str(notes)]}, drop=drop, timeout=60))
    await asyncio.wait_for(hold0.reached.wait(), 30)
    await first.state.platform.turn_logs.shutdown()
    drop.set()
    hold0.gate.set()
    await asyncio.wait_for(follower, 30)

    second = _reboot(home)
    hold = _Hold()
    script2 = wire(second, Script([_write("c2", "two.txt", "2"), hold]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await asyncio.wait_for(hold.reached.wait(), 30)
    assert "heron" in script2.calls[0]["system"], "the first resume reads the question's files"
    await second.state.platform.turn_logs.shutdown()
    hold.gate.set()

    third = _reboot(home)
    script3 = wire(third, Script(["All there."]))
    assert await build_routes.resume_after_restart(third.state.d) == [sid]
    await until(lambda: script3.calls, 30)
    assert "heron" in script3.calls[0]["system"], "the second resume lost the question's files"
    await until(lambda: not third.state.platform.turn_logs.running(), 30)


async def test_a_resumed_reply_cut_before_its_first_save_is_carried_on_again(home, unchecked, monkeypatch):
    """The boot TAKES the cut-off reply's kept conversation when it carries it
    on. A stop that lands while the carrying-on turn is still getting ready
    (grounding, a summary, its folder snapshot — before its own first save)
    must not lose the reply: the next boot carries it on again."""
    first = create_app(home)
    sid = new_session(first, unchecked)["id"]
    await _run_until_held(first, sid, [_write("c1", "one.txt", "1"), _Hold()], "bt-1", "make files")

    second = _reboot(home)
    wire(second, Script(["Never said."]))
    reached = asyncio.Event()
    real_save = RR.ReplyRecord.save

    async def held_save(self, msgs, head):
        reached.set()
        await asyncio.Event().wait()  # the stop lands before the first save
        return await real_save(self, msgs, head)

    monkeypatch.setattr(RR.ReplyRecord, "save", held_save)
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await asyncio.wait_for(reached.wait(), 30)
    await second.state.platform.turn_logs.shutdown()
    monkeypatch.setattr(RR.ReplyRecord, "save", real_save)

    third = _reboot(home)
    script = wire(third, Script(["Carried on."]))
    assert await build_routes.resume_after_restart(third.state.d) == [sid], (
        "the reply was lost: its conversation was taken and nothing kept it"
    )
    await until(lambda: script.calls, 30)
    sent = script.calls[0]["messages"]
    assert [m.tool_call_id for m in sent if m.role == "tool"] == ["c1"]
    assert [m.content for m in sent if m.role == "user"].count("make files") == 1
    await until(lambda: not third.state.platform.turn_logs.running(), 30)


async def test_the_start_itself_refuses_a_resume_once_the_user_sent_a_newer_message(home, unchecked):
    """The boot pass checks for a newer message, then starts the carry-on in
    another step; the user's own message can land between the two. The start
    reads the row it builds the turn from and refuses — nothing is asked of
    the model, nothing is written, and the kept list is not taken."""
    first = create_app(home)
    sid = new_session(first, unchecked)["id"]
    await _run_until_held(first, sid, [_write("c1", "one.txt", "1"), _Hold()], "bt-1", "make files")
    second = _reboot(home)
    engine = second.state.platform.engine
    assert build_routes.resume_refusal(engine, sid, "bt-1", False) == ""
    S.append_lines(engine, sid, [{"role": "user", "content": "do something else", "turnId": "bt-user"}])
    script = wire(second, Script(["Never asked."]))
    with pytest.raises(build_routes.ResumeGone):
        await build_routes.start_build_turn(second.state.d, sid, RR.RESUME_QUESTION, "bt-again",
                                            continuation=True, resume_turn="bt-1")
    assert script.calls == []
    assert not any(m.get("resumedFrom") for m in messages_of(second, sid))
    assert RR.has_record(engine, "bt-1"), "a refused start takes nothing"


async def test_a_resumed_reply_a_crash_cut_off_still_says_it_carried_on(home, unchecked):
    """A crash (no writer of its own) cut the carrying-on reply: the boot
    writes its line from its stored frames — it keeps ``resumedFrom``, so the
    screen still says it carried the first reply on."""
    sid, turn = await _cut_twice(home, unchecked, second_moves=[_write("c2", "two.txt", "2"), _Hold()])
    # What a crash leaves: take back the line the tidy stop just wrote, and
    # put the record back to "running" (the boot reconcile cuts it off).
    from iron_jarvis.core.models import ChatTurnRecord

    probe = create_app(home)
    engine = probe.state.platform.engine
    msgs = [m for m in messages_of(probe, sid) if not (m["role"] == "assistant" and m.get("turnId") == turn)]
    with session_scope(engine) as db:
        row = db.get(S.ChatThreadRecord, sid)
        row.messages_json = json.dumps(msgs)
        db.add(row)
        rec = db.get(ChatTurnRecord, turn)
        rec.status = "running"
        db.add(rec)
        db.commit()
    third = _reboot(home)
    line = _reply_line(third, sid, turn)
    assert line["interrupted"] is True
    assert line.get("resumedFrom") == "bt-1", line
