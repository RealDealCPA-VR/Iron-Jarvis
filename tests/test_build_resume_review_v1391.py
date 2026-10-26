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


# --------------------------------------------------------------------------- #
# 3b. a command, a patch and a program cut off while they ran
# --------------------------------------------------------------------------- #

_GPT = ("codex-cli", "gpt-5.5")
_CLAUDE = ("anthropic", "claude-sonnet-4-5")
_PATCH = "*** Begin Patch\n*** Add File: p.txt\n+patched\n*** End Patch\n"


@pytest.mark.parametrize("who,call,real", [
    (_CLAUDE, ToolCall(id="k2", name="Bash", arguments={"command": "echo hi"}), ("shell",)),
    (_GPT, ToolCall(id="k2", name="apply_patch", arguments={"input": _PATCH}), ("apply_patch",)),
    (_GPT, ToolCall(id="k2", name="exec_command", arguments={"cmd": "echo hi"}), ("process_start", "shell")),
], ids=["claude-Bash", "codex-apply_patch", "codex-exec_command"])
async def test_a_command_a_patch_or_a_program_cut_off_mid_run_is_never_run_again_by_the_app(
        home, unchecked, monkeypatch, who, call, real):
    """Judged by the REAL tool behind the dialect's name: each "may have
    landed, check first" — and the app itself runs nothing again."""
    from iron_jarvis.tools.registry import INTERRUPTED_MAY_HAVE_LANDED

    first = create_app(home)
    sid = new_session(first, unchecked, provider=who[0], model=who[1], approval_mode="yolo")["id"]
    running = asyncio.Event()
    ran: list[str] = []
    originals = {}
    for name in real:
        tool = first.state.platform.registry.get(name)
        if tool is None:
            continue
        originals[name] = tool.execute

        async def slow(args, ctx, _name=name):
            ran.append(_name)
            running.set()
            await asyncio.Event().wait()  # cut off while it runs

        monkeypatch.setattr(tool, "execute", slow)
    wire(first, Script([[call], "x"], who=who))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(first, sid, {"text": "do it", "turn_id": "bt-k"},
                                             drop=drop, timeout=60))
    await asyncio.wait_for(running.wait(), 30)
    kept = json.loads(next(r for r in _records(first) if r.run_id == "build-turn:bt-k").messages_json)
    # The real tool rides beside the model's name when they differ.
    assert kept[-1]["tool_calls"][0]["name"] == call.name
    assert kept[-1]["tool_calls"][0].get("tool", call.name) in real
    await first.state.platform.turn_logs.shutdown()
    drop.set()
    await asyncio.wait_for(follower, 30)
    assert len(ran) == 1

    second = _reboot(home)
    for name in real:
        tool = second.state.platform.registry.get(name)
        if tool is not None:
            async def never(args, ctx, _name=name):
                ran.append(_name)
                raise AssertionError("the app ran a cut-off call again")

            monkeypatch.setattr(tool, "execute", never)
    script = wire(second, Script(["Checked: it had landed."], who=who))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await until(lambda: script.calls, 30)
    sent = script.calls[0]["messages"]
    cut = next(m for m in sent if m.role == "tool" and m.tool_call_id == "k2")
    assert cut.content == INTERRUPTED_MAY_HAVE_LANDED and cut.name == call.name
    await until(lambda: not second.state.platform.turn_logs.running(), 30)
    assert len(ran) == 1, ran


# --------------------------------------------------------------------------- #
# 5. a LONG reply (the kind this exists for) keeps its newest rounds
# --------------------------------------------------------------------------- #


async def test_a_reply_past_the_cap_keeps_its_newest_rounds_and_still_carries_on(home, unchecked, monkeypatch):
    """The doer's list was dropped WHOLE once it passed the cap — and the cap
    is what a two-hour reply reaches (120 reads of up to 32,000 chars), so
    exactly the replies this feature exists for fell back to Retry from the
    beginning. Now the list keeps its question and its newest whole rounds,
    with a note saying how many tool calls are left out; the reply carries
    on, and no finished step runs again."""
    from iron_jarvis.agents import transcript

    first = create_app(home)
    sid = new_session(first, unchecked)["id"]
    monkeypatch.setattr(transcript, "MAX_TRANSCRIPT_CHARS", 3_500)
    big = "x" * 2_000
    await _run_until_held(first, sid, [_write("c1", "a.txt", big), _write("c2", "b.txt", big),
                                       _write("c3", "c.txt", big), _Hold()], "bt-1", "make three big files")
    kept = json.loads(next(r for r in _records(first) if r.run_id == "build-turn:bt-1").messages_json)
    assert len(json.dumps(kept)) <= 3_500
    assert kept[0]["content"] == "make three big files"
    assert kept[1]["role"] == "user" and "earliest 2 tool calls" in kept[1]["content"]
    assert [(r["role"], [c["id"] for c in r.get("tool_calls", [])], r.get("tool_call_id")) for r in kept[2:]] == [
        ("assistant", ["c3"], None), ("tool", [], "c3"),
    ]

    second = _reboot(home)
    script = wire(second, Script(["All three are there."]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    new = await _resumed(second, sid)
    assert new.status == "done", new.frames[-3:]
    sent = script.calls[0]["messages"]
    assert [m.tool_call_id for m in sent if m.role == "tool"] == ["c3"]
    assert any("earliest 2 tool calls" in (m.content or "") for m in sent if m.role == "user")
    from iron_jarvis.core.models import ToolInvocation

    with session_scope(second.state.platform.engine) as db:
        writes = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == "write_file"]
    assert len(writes) == 3, "no finished step ran again"


def test_a_kept_list_cut_to_the_cap_never_splits_a_round():
    """Units, never messages: an assistant turn and EVERY tool result it
    asked for stay together, and the newest round is kept whole or nothing
    is."""
    rows = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "Read", "arguments": {}},
                                                             {"id": "b", "name": "Read", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "a", "content": "A" * 300},
        {"role": "tool", "tool_call_id": "b", "content": "B" * 300},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "Read", "arguments": {}},
                                                             {"id": "d", "name": "Read", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c", "content": "C" * 300},
        {"role": "tool", "tool_call_id": "d", "content": "D" * 300},
    ]
    whole = json.dumps(rows)
    assert RR._fit(rows, len(whole)) == whole
    for cap in range(200, len(whole)):
        text = RR._fit(rows, cap)
        if text is None:
            continue
        kept = json.loads(text)
        assert len(text) <= cap and kept[0]["content"] == "q"
        asked = {c["id"] for r in kept for c in r.get("tool_calls", [])}
        answered = {r["tool_call_id"] for r in kept if r["role"] == "tool"}
        assert asked == answered, (cap, asked, answered)
    newest_alone = json.dumps([rows[0], {"role": "user", "content": RR.LEFT_OUT_NOTE.format(n=2)}, *rows[4:]])
    assert RR._fit(rows, len(newest_alone)) == newest_alone
    assert RR._fit(rows, len(newest_alone) - 1) is None, "the newest round alone does not fit: nothing"


# --------------------------------------------------------------------------- #
# 4. the sleep blocker holds for work that RUNS, never for work parked on you
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not on PATH")
def test_the_blocker_reads_working_never_busy_when_the_daemon_says_it(tmp_path):
    """Decision 1: "only while a coding reply, a mission or an agent run is
    working". The desktop's blocker read ``busy``, which also counts work
    parked on the user (a card, a Build terminal's question), so a laptop left
    with a card open never slept. It reads ``working`` now; an older daemon
    without the key is read by ``busy``."""
    from tests.test_keep_awake_v1391 import _BLOCKER, _decl, _fn, _run

    script = "\n".join([
        _decl("KEEP_AWAKE_MISSES_RELEASE"), _decl("KEEP_AWAKE_TYPE"),
        _fn("keepAwakeDecision"), _fn("applyKeepAwake"), _BLOCKER,
        """
const state = { id: null, misses: 0 };
const out = [];
function step(activity) {
  const d = keepAwakeDecision(activity, state);
  state.misses = d.misses;
  applyKeepAwake(d.want, blocker, state);
  out.push(state.id !== null);
}
step({ busy: true, working: true, keep_awake: true });    // a reply works
step({ busy: true, working: false, keep_awake: true });   // it waits on a card: let go
step({ busy: true, working: false, keep_awake: true });   // still waiting
step({ busy: true, working: true, keep_awake: false });   // the setting off
step({ busy: true, keep_awake: true });                   // an older daemon: busy decides
console.log(JSON.stringify({ out }));
""",
    ])
    assert _run(script, tmp_path, "keep_awake_working")["out"] == [True, False, False, False, True]


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not on PATH")
def test_the_tray_menu_is_rebuilt_only_when_the_blocker_changes(tmp_path):
    """The check runs every 15 s; the tray menu is rebuilt only when the
    blocker is taken or let go, never on every check (the doer's pins did not
    say so: a tick that always rebuilt stayed green)."""
    from tests.test_keep_awake_v1391 import _BLOCKER, _decl, _fn, _run

    script = "\n".join([
        _decl("KEEP_AWAKE_MISSES_RELEASE"), _decl("KEEP_AWAKE_TYPE"), _BLOCKER,
        """
const ACTIVITY_PROBE_TIMEOUT_MS = 1;
let isQuitting = false;
let answer = null;
function probeDaemonActivity() { return Promise.resolve(answer); }
let refreshes = 0;
function refreshTrayMenu() { refreshes += 1; }
function desktopLog() {}
const powerSaveBlocker = blocker;
const keepAwakeState = { id: null, misses: 0 };
""",
        _fn("keepAwakeDecision"), _fn("applyKeepAwake"), _fn("keepAwakeTick"),
        """
const flush = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  const seen = [];
  for (const a of [
    { busy: true, working: true, keep_awake: true },
    { busy: true, working: true, keep_awake: true },
    { busy: true, working: true, keep_awake: true },
    { busy: false, working: false, keep_awake: true },
    { busy: false, working: false, keep_awake: true },
  ]) { answer = a; keepAwakeTick(); await flush(); seen.push(refreshes); }
  console.log(JSON.stringify({ seen }));
})();
""",
    ])
    assert _run(script, tmp_path, "tray_refresh")["seen"] == [1, 1, 1, 2, 2]


def test_the_take_never_moves_a_list_parked_on_the_user_and_the_pass_refuses_one(home):
    """Both layers, each on its own: the take (moving the list to the
    carrying-on turn) refuses a ``waiting`` list, and the pass's own refusal
    says why before any take is tried."""
    app = create_app(home)
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(RunTranscript(run_id=RR.run_id_for("t-w"), session_id="build:s", messages_json="[]",
                             waiting=True))
        db.add(RunTranscript(run_id=RR.run_id_for("t-x"), session_id="build:s", messages_json="[]"))
        db.commit()
    assert RR.claim(engine, "t-w", "t-new") is False
    assert RR.has_record(engine, "t-w") and not RR.has_record(engine, "t-new")
    assert RR.claim(engine, "t-x", "t-new") is True
    assert not RR.has_record(engine, "t-x") and RR.has_record(engine, "t-new")
    assert RR.claim(engine, "t-x", "t-other") is False, "taken once"
    assert build_routes.resume_refusal(engine, "s", "t-w", True) == (
        "it was waiting on you when Iron Jarvis stopped"
    )


def _activity(app) -> dict:
    from fastapi.testclient import TestClient

    return TestClient(app).get("/system/activity").json()


def test_a_build_terminal_waiting_on_its_question_is_busy_but_not_working(home, monkeypatch):
    app = create_app(home)
    panes = {"state": "blocked"}
    monkeypatch.setattr(app.state.platform.terminals, "list",
                        lambda: [{"id": "p1", "alive": True, "state": panes["state"], "agent_cli": "claude"}])
    body = _activity(app)
    assert body["busy"] is True and body["busy_panes"] == 1, "the update's warning still names it"
    assert body["working"] is False, "a coding tool showing its question is parked on the user"
    panes["state"] = "working"
    assert _activity(app)["working"] is True


async def test_an_attended_run_on_its_card_is_parked_and_an_unattended_one_still_works(home):
    """An attended run's ask waits for the person with no clock: parked. An
    UNATTENDED run's ask expires by itself and its run goes on — still work
    (letting the PC sleep through the clock would suspend the run for the
    night)."""
    from iron_jarvis.core.models import Session, SessionStatus

    app = create_app(home)
    platform = app.state.platform
    with session_scope(platform.engine) as db:
        db.add(Session(id="s-att", task="t", origin="job:mission", status=SessionStatus.ACTIVE))
        db.commit()
    ask, fut = platform.approvals.request("write_file", {"path": "a"}, session_id="s-att")
    body = await asyncio.to_thread(_activity, app)
    assert body["busy"] is True and body["working"] is False
    with session_scope(platform.engine) as db:
        row = db.get(Session, "s-att")
        row.origin = "schedule:nightly"
        db.add(row)
        db.commit()
    assert (await asyncio.to_thread(_activity, app))["working"] is True
    platform.approvals.resolve(ask, "deny")


async def test_a_coding_reply_that_works_holds_the_pc_awake(home, unchecked):
    app = create_app(home)
    sid = new_session(app, unchecked)["id"]
    hold = _Hold()
    wire(app, Script([hold]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "x", "turn_id": "bt-w"}, drop=drop, timeout=60))
    await asyncio.wait_for(hold.reached.wait(), 30)
    body = await asyncio.to_thread(_activity, app)
    assert body["busy"] is True and body["working"] is True
    await app.state.platform.turn_logs.shutdown()
    drop.set()
    hold.gate.set()
    await asyncio.wait_for(follower, 30)


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
