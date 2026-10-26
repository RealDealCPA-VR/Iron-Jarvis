"""v1.391.0 (the long-jobs plan's Phase 2, decision 2): a CODING SESSION's
reply that a restart or a crash cut off WHILE IT WORKED carries on by itself,
from its last finished step.

Before this, the model's own record of a Build reply (its tool calls and their
results) lived only in the stream lane's memory: a restart left the reply's
words and a Retry that asked the question again FROM THE BEGINNING. Now a
Build turn keeps that record (``harness/reply_record``, the agent-run
transcript store keyed ``build-turn:<turn id>``), and the boot's one pass
(``routes/build.resume_after_restart``) starts a carry-on turn of the same
session from it.

Driven through the REAL app factory (two of them on one home: the "restart"),
the real stream lane and the real tool registry; only the model (the router)
is a scripted double. A real daemon in a subprocess, stopped the two ways the
user's PC stops it, is ``test_update_rehearsal_v1338.py``'s coding reply.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord, RunTranscript, ToolInvocation
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    until,
    wire,
)


def _write(cid: str, name: str, content: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="Write", arguments={"file_path": name, "content": content})]


class Hold:
    """A model call that never answers until ``gate`` is set (the app stops
    while the model is working)."""

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


def _kept_list(app, turn_id: str) -> list[dict]:
    with session_scope(app.state.platform.engine) as db:
        row = db.get(RunTranscript, RR.run_id_for(turn_id))
        return json.loads(row.messages_json) if row is not None else []


def _ledger(app, tool: str = "write_file") -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all()
                if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def _events(app, kind: str) -> list[dict]:
    return [e.payload for e in app.state.platform.event_bus.history if e.type == kind]


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


async def _cut_off_mid_reply(app, sid: str, turn_id: str = "bt-1", *, stop: str = "shutdown"):
    """Run a reply that wrote one.txt and two.txt and is ASKING THE MODEL for
    its third step when the app stops (``shutdown``: the update's tidy stop;
    ``crash``: the process is gone — its turn log was last written to disk
    and nothing else ran)."""
    hold = Hold()
    wire(app, Script([_write("c1", "one.txt", "1"), _write("c2", "two.txt", "2"), hold]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "make three files", "turn_id": turn_id},
                                             drop=drop, timeout=60))
    await asyncio.wait_for(hold.reached.wait(), 30)
    logs = app.state.platform.turn_logs
    if stop == "shutdown":
        await logs.shutdown()
    else:
        lg = logs.get(turn_id)
        engine = app.state.platform.engine

        def on_disk():
            with session_scope(engine) as db:
                rec = db.get(ChatTurnRecord, turn_id)
                frames = json.loads(rec.frames_json) if rec is not None else []
            return frames if len(frames) == len(lg.frames) else None

        # What a crash leaves: the record as it was last written by ITSELF
        # (v1.391.0: a reply gone quiet still writes its last frames within
        # the interval — never `persist_now` here), nothing more. The live
        # task is then torn down without its own end.
        stored = json.dumps(await until(on_disk, 10))
        assert stored != "null", "the record never caught up with the reply's last frames"
        lg.task.cancel()
        with pytest.raises(BaseException):
            await lg.task
        with session_scope(app.state.platform.engine) as db:
            rec = db.get(ChatTurnRecord, turn_id)
            rec.status, rec.frames_json = "running", stored
            db.add(rec)
            db.commit()
        # The crash also leaves the reply line unwritten: take back what the
        # torn-down writer just wrote, so the boot's rebuild is what we test.
        msgs = [m for m in messages_of(app, sid) if not (m["role"] == "assistant" and m.get("turnId") == turn_id)]
        with session_scope(app.state.platform.engine) as db:
            row = db.get(S.ChatThreadRecord, sid)
            row.messages_json = json.dumps(msgs)
            db.add(row)
            db.commit()
    drop.set()
    hold.gate.set()
    await asyncio.wait_for(follower, 30)


@pytest.fixture()
def home(tmp_path) -> str:
    return str(tmp_path / "home")


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    # The finish check runs nothing here (no tests to run, the files are the
    # request's), but turn it off so the script stays three steps long.
    harness = folder / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- #
# 2. a coding reply keeps its own conversation
# --------------------------------------------------------------------------- #


async def test_a_reply_cut_off_keeps_its_own_conversation_ending_on_complete_rounds(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _cut_off_mid_reply(app, sid)
    kept = _kept_list(app, "bt-1")
    # The question first, then each round as ONE unit (the call and its
    # result), in the store's shape; the model's own names (Claude's Write).
    assert [(r["role"], r.get("tool_call_id"), [c["id"] for c in r.get("tool_calls", [])]) for r in kept] == [
        ("user", None, []),
        ("assistant", None, ["c1"]), ("tool", "c1", []),
        ("assistant", None, ["c2"]), ("tool", "c2", []),
    ]
    assert kept[0]["content"] == "make three files"
    assert kept[1]["tool_calls"][0]["name"] == "Write"
    # The REAL tool rides beside the model's name.
    assert kept[1]["tool_calls"][0]["tool"] == "write_file"
    rec = next(r for r in _records(app) if r.run_id == "build-turn:bt-1")
    assert rec.session_id == f"build:{sid}" and not rec.waiting


async def test_a_reply_that_ends_keeps_nothing(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script([_write("c1", "one.txt", "1"), "All done."]))
    status, raw = await open_turn(app, sid, {"text": "make one file", "turn_id": "bt-done"})
    assert status == 200 and "event: done" in raw
    assert [r.run_id for r in _records(app)] == [], "a finished reply's list is deleted"


async def test_a_reply_stopped_by_the_user_keeps_nothing(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    hold = Hold()
    wire(app, Script([_write("c1", "one.txt", "1"), hold]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "x", "turn_id": "bt-stop"}, drop=drop))
    await asyncio.wait_for(hold.reached.wait(), 30)
    assert RR.has_record(app.state.platform.engine, "bt-stop")
    status, _ = await asyncio.wait_for(asgi(app, "POST", "/chat/turns/bt-stop/stop", {}), 30)
    assert status == 200
    hold.gate.set()
    await until(lambda: app.state.platform.turn_logs.get("bt-stop").status != "running")
    drop.set()
    await asyncio.wait_for(follower, 30)
    await until(lambda: not RR.has_record(app.state.platform.engine, "bt-stop"))
    assert not RR.has_record(app.state.platform.engine, "bt-stop")


# --------------------------------------------------------------------------- #
# 3. it carries on by itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stop", ["shutdown", "crash"])
async def test_a_working_reply_carries_on_by_itself_from_its_last_round(home, work, stop):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid, stop=stop)
    assert (work / "one.txt").read_text() == "1" and (work / "two.txt").read_text() == "2"
    assert len(_ledger(first)) == 2

    second = _reboot(home)
    script = wire(second, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    carried = await build_routes.resume_after_restart(second.state.d)
    assert carried == [sid]
    logs = second.state.platform.turn_logs
    new = await until(lambda: next((lg for lg in logs.running() if lg.thread_id == sid), None)
                      or next((lg for lg in list(logs._logs.values()) if lg.thread_id == sid), None))
    await until(lambda: new.status != "running", 30)
    assert new.status == "done", new.frames[-3:]

    # The model was shown ITS OWN conversation: the question, both finished
    # rounds exactly as they were (never run again), then the resume note.
    sent = script.calls[0]["messages"]
    tail = [(m.role, m.tool_call_id or "", [c.id for c in m.tool_calls]) for m in sent[-6:]]
    assert tail == [
        ("user", "", []),
        ("assistant", "", ["c1"]), ("tool", "c1", []),
        ("assistant", "", ["c2"]), ("tool", "c2", []),
        ("user", "", []),
    ]
    assert sent[-6].content == "make three files"
    assert sent[-1].content == RR.RESUME_NOTE
    # No finished step ran again: one ledger row per file, three in all.
    rows = _ledger(second)
    paths = sorted(json.loads(r.args_json).get("path", "") for r in rows)
    assert len(rows) == 3 and [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in paths] == [
        "one.txt", "three.txt", "two.txt",
    ]
    assert (work / "three.txt").read_text() == "3"

    # The transcript: the cut-off reply, the hidden carry-on question, the
    # reply that carried it on — both saying so.
    msgs = messages_of(second, sid)
    cut = next(m for m in msgs if m["role"] == "assistant" and m["turnId"] == "bt-1")
    assert cut["interrupted"] is True and cut["error"] == durable_turns.INTERRUPTED_DETAIL
    q = next(m for m in msgs if m["role"] == "user" and m.get("resumedFrom") == "bt-1")
    assert q["continuation"] is True and q["content"] == RR.RESUME_QUESTION
    reply = next(m for m in msgs if m["role"] == "assistant" and m["turnId"] == new.turn_id)
    assert reply["resumedFrom"] == "bt-1" and "All three are there." in reply["content"]
    # Both kept lists are gone: the old one was taken, the new one ended.
    assert [r.run_id for r in _records(second)] == []
    # The bell was told: one `build.resumed` naming the reply it carried on.
    told = _events(second, "build.resumed")
    assert [(e["thread_id"], e["resumed_from"], e["reason"], e["turn_id"]) for e in told] == [
        (sid, "bt-1", "restart", new.turn_id),
    ]


@pytest.mark.parametrize("kind", ["write", "read"])
async def test_a_call_cut_off_while_it_ran_is_answered_and_never_run_again_blindly(home, work, monkeypatch, kind):
    """The app stops while a tool RUNS: the kept list ends on the turn that
    asked for it, and the resumed model is told it was cut off — in the
    store's own words, judged by the REAL tool behind the model's name
    (Claude's ``Write`` is ``write_file``: "may have landed, check first";
    its ``Read`` is ``read_file``, which only reads: "safe to run again")."""
    from iron_jarvis.tools.registry import INTERRUPTED_MAY_HAVE_LANDED, INTERRUPTED_SAFE

    first = create_app(home)
    sid = new_session(first, work)["id"]
    (work / "two.txt").write_text("old", encoding="utf-8")
    tool = first.state.platform.registry.get("write_file" if kind == "write" else "read_file")
    real = tool.execute
    running = asyncio.Event()

    async def slow(args, ctx):
        if str(args.get("path", "")).endswith("two.txt"):
            running.set()
            await asyncio.Event().wait()  # cut off while it runs
        return await real(args, ctx)

    monkeypatch.setattr(tool, "execute", slow)
    second_call = (_write("c2", "two.txt", "2") if kind == "write"
                   else [ToolCall(id="c2", name="Read", arguments={"file_path": "two.txt"})])
    wire(first, Script([_write("c1", "one.txt", "1"), second_call, "x"]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(first, sid, {"text": "make files", "turn_id": "bt-cut"},
                                             drop=drop, timeout=60))
    await asyncio.wait_for(running.wait(), 30)
    kept = _kept_list(first, "bt-cut")
    assert kept[-1]["role"] == "assistant" and kept[-1]["tool_calls"][0]["id"] == "c2", (
        "the round's calls are kept BEFORE they run"
    )
    await first.state.platform.turn_logs.shutdown()
    drop.set()
    await asyncio.wait_for(follower, 30)
    monkeypatch.setattr(tool, "execute", real)

    second = _reboot(home)
    script = wire(second, Script([_write("c3", "three.txt", "3"), "Done here."]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await until(lambda: script.calls, 30)
    sent = script.calls[0]["messages"]
    cut = next(m for m in sent if m.role == "tool" and m.tool_call_id == "c2")
    assert cut.content == (INTERRUPTED_MAY_HAVE_LANDED if kind == "write" else INTERRUPTED_SAFE)
    assert sent[-1].content == RR.RESUME_NOTE
    await until(lambda: not second.state.platform.turn_logs.running(), 30)


async def test_a_resumed_reply_keeps_its_whole_conversation_for_the_next_restart(home, work):
    """Stopped AGAIN while it carried on: the resumed reply's own kept list is
    the WHOLE turn — the question, the rounds it was given, the resume note
    and its own rounds — so the next boot carries it on once more, from there,
    and no finished step runs a second time."""
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)

    second = _reboot(home)
    hold = Hold()
    wire(second, Script([_write("c3", "three.txt", "3"), hold]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await asyncio.wait_for(hold.reached.wait(), 30)
    logs = second.state.platform.turn_logs
    new_turn = next(lg.turn_id for lg in logs.running() if lg.thread_id == sid)
    kept = _kept_list(second, new_turn)
    assert [(r["role"], [c["id"] for c in r.get("tool_calls", [])], r.get("tool_call_id")) for r in kept] == [
        ("user", [], None),
        ("assistant", ["c1"], None), ("tool", [], "c1"),
        ("assistant", ["c2"], None), ("tool", [], "c2"),
        ("user", [], None),
        ("assistant", ["c3"], None), ("tool", [], "c3"),
    ]
    assert kept[0]["content"] == "make three files" and kept[5]["content"] == RR.RESUME_NOTE
    await logs.shutdown()
    hold.gate.set()

    third = _reboot(home)
    script = wire(third, Script(["All three are there."]))
    assert await build_routes.resume_after_restart(third.state.d) == [sid]
    await until(lambda: script.calls, 30)
    tail = [(m.role, m.tool_call_id or "") for m in script.calls[0]["messages"][-9:]]
    assert tail == [("user", ""), ("assistant", ""), ("tool", "c1"), ("assistant", ""), ("tool", "c2"),
                    ("user", ""), ("assistant", ""), ("tool", "c3"), ("user", "")]
    await until(lambda: not third.state.platform.turn_logs.running(), 30)
    assert len(_ledger(third)) == 3, "each file written once across three boots"
    carried = [m["resumedFrom"] for m in messages_of(third, sid) if m["role"] == "user" and m.get("resumedFrom")]
    assert carried == ["bt-1", new_turn]


async def test_a_reply_waiting_on_you_is_never_carried_on_by_itself(home, work):
    first = create_app(home)
    sid = new_session(first, work, approval_mode="always_ask")["id"]
    wire(first, Script([_write("c1", "one.txt", "1"), "x"]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(first, sid, {"text": "make one", "turn_id": "bt-wait"},
                                             drop=drop, timeout=60))
    logs = await until(lambda: getattr(first.state.platform, "turn_logs", None), 30)
    await until(lambda: logs.get("bt-wait") is not None
                and any(e == "approval" for e, _ in logs.get("bt-wait").frames), 30)
    rec = await until(lambda: next(
        (r for r in _records(first) if r.run_id == "build-turn:bt-wait" and r.waiting), None,
    ))
    assert rec is not None, "a card parks the reply: its record says so"
    await logs.shutdown()
    drop.set()
    await asyncio.wait_for(follower, 30)

    second = _reboot(home)
    script = wire(second, Script(["Never asked."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []
    msgs = messages_of(second, sid)
    assert not any(m.get("continuation") for m in msgs), "nothing carried it on"
    cut = next(m for m in msgs if m["role"] == "assistant" and m["turnId"] == "bt-wait")
    assert cut["interrupted"] is True  # it keeps v1.389.0's Retry
    assert [r.run_id for r in _records(second)] == [], "its kept list is dropped"
    assert not (work / "one.txt").exists()


async def test_an_answered_card_is_working_again(home, work):
    app = create_app(home)
    sid = new_session(app, work, approval_mode="always_ask")["id"]
    hold = Hold()
    wire(app, Script([_write("c1", "one.txt", "1"), hold]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "make one", "turn_id": "bt-ans"},
                                             drop=drop, timeout=60))
    logs = await until(lambda: getattr(app.state.platform, "turn_logs", None), 30)

    def card():
        lg = logs.get("bt-ans")
        return next((json.loads(d) for e, d in (lg.frames if lg else []) if e == "approval"), None)

    asked = await until(card, 30)
    assert await until(lambda: any(r.waiting for r in _records(app)))
    status, _ = await asgi(app, "POST", f"/chat/approvals/{asked['id']}", {"decision": "once"})
    assert status == 200
    await asyncio.wait_for(hold.reached.wait(), 30)
    rec = next(r for r in _records(app) if r.run_id == "build-turn:bt-ans")
    assert not rec.waiting, "answered: the reply works again (a stop now would carry it on)"
    await logs.shutdown()
    drop.set()
    hold.gate.set()
    await asyncio.wait_for(follower, 30)


async def test_two_resumes_of_one_turn_start_exactly_one(home, work):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    second = _reboot(home)
    hold = Hold()
    wire(second, Script([hold]))
    a, b = await asyncio.gather(
        build_routes.resume_after_restart(second.state.d),
        build_routes.resume_after_restart(second.state.d),
    )
    assert sorted(a + b) == [sid]
    await asyncio.wait_for(hold.reached.wait(), 30)
    msgs = messages_of(second, sid)
    assert len([m for m in msgs if m["role"] == "user" and m.get("resumedFrom") == "bt-1"]) == 1
    # A third pass (a boot that runs again) finds nothing to take.
    assert await build_routes.resume_after_restart(second.state.d) == []
    hold.gate.set()
    await second.state.platform.turn_logs.shutdown()


def test_the_take_is_one_statement_exactly_one_wins(home, work):
    app = create_app(home)
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(RunTranscript(run_id=RR.run_id_for("t-x"), session_id="build:s", messages_json="[]"))
        db.add(RunTranscript(run_id=RR.run_id_for("t-w"), session_id="build:s", messages_json="[]",
                             waiting=True))
        db.commit()
    assert RR.claim(engine, "t-x") is True
    assert RR.claim(engine, "t-x") is False
    assert RR.claim(engine, "t-w") is False, "a list parked on the user is never taken"


@pytest.mark.parametrize("change", ["newer message", "archived", "account wait", "account card", "no list"])
async def test_a_cut_off_reply_is_not_carried_on_when(home, work, change):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    engine = first.state.platform.engine
    if change == "newer message":
        S.append_lines(engine, sid, [{"role": "user", "content": "do something else", "turnId": "bt-user"}])
    elif change == "archived":
        from iron_jarvis.daemon.routes.chat import set_thread_archived

        set_thread_archived(engine, sid, True)
    elif change == "account wait":
        S.update(engine, sid, setup={"account_wait": {"account": "a", "armed_at": "x"}})
    elif change == "account card":
        S.update(engine, sid, setup={"account_limit": {"account": "a", "kind": "limit"}})
    else:
        RR.drop(engine, "bt-1")
    second = _reboot(home)
    script = wire(second, Script(["Never asked."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []
    assert not any(m.get("resumedFrom") for m in messages_of(second, sid))
    assert [r.run_id for r in _records(second)] == [], "the list a boot cannot use is dropped"


async def test_the_resumed_turn_is_the_sessions_own_turn(home, work, monkeypatch):
    """It goes through `start_build_turn` — the session's folder, Local only,
    its model hold — never a second path."""
    from iron_jarvis.providers.hold import current_hold

    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    S.update(first.state.platform.engine, sid, setup={"local_only": True})
    second = _reboot(home)
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append({"body": body, **kw})
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    held: list = []

    async def answer(script):
        held.append(current_hold())
        return "Carried on."

    wire(second, Script([answer]))
    await build_routes.resume_after_restart(second.state.d)
    await until(lambda: held, 30)
    kw = seen[0]
    assert kw["harness"] == "auto" and kw["session_id"] == f"build:{sid}" and kw["stay_in_chat"] is True
    body = kw["body"]
    assert body.workspace_dir == str(work) and body.local_only is True and body.thread_id == sid
    # The planned history ends with the cut-off QUESTION; its rounds ride the record.
    assert body.messages[-1].content == "make three files"
    assert kw["reply_record"].resumed_from == "bt-1" and kw["reply_record"].resume_tail
    assert held[0] is not None and held[0].provider == "anthropic"
    await until(lambda: not second.state.platform.turn_logs.running(), 30)


async def test_a_list_over_its_cap_is_not_kept_and_the_reply_keeps_retry(home, work, monkeypatch):
    from iron_jarvis.agents import transcript

    first = create_app(home)
    sid = new_session(first, work)["id"]
    monkeypatch.setattr(transcript, "MAX_TRANSCRIPT_CHARS", 400)
    await _cut_off_mid_reply(first, sid)
    assert not RR.has_record(first.state.platform.engine, "bt-1"), (
        "a list over its cap is not kept — and the shorter copy before it is gone too"
    )
    second = _reboot(home)
    script = wire(second, Script(["Never asked."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []
    cut = next(m for m in messages_of(second, sid) if m["role"] == "assistant" and m["turnId"] == "bt-1")
    assert cut["error"] == durable_turns.INTERRUPTED_DETAIL


async def test_a_kept_list_a_boot_could_not_use_goes_after_a_week(home):
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow

    app = create_app(home)
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(RunTranscript(run_id=RR.run_id_for("old"), session_id="build:gone", messages_json="[]",
                             updated_at=utcnow() - timedelta(days=RR.KEEP_DAYS + 1)))
        db.add(RunTranscript(run_id="run_agent", session_id="sess", messages_json="[]",
                             updated_at=utcnow() - timedelta(days=30)))
        db.commit()
    await build_routes.resume_after_restart(app.state.d)
    assert [r.run_id for r in _records(app)] == ["run_agent"], "an agent run's list is never touched"


# --------------------------------------------------------------------------- #
# the save itself
# --------------------------------------------------------------------------- #


async def test_a_save_keeps_the_question_on_marks_images_and_writes_once(home, monkeypatch):
    from iron_jarvis.providers.adapters.base import LLMMessage

    app = create_app(home)
    engine = app.state.platform.engine
    rec = RR.ReplyRecord(engine, "s1", "t1")
    msgs = [
        LLMMessage(role="user", content="earlier"),
        LLMMessage(role="assistant", content="earlier answer"),
        LLMMessage(role="user", content="look", images=[{"data_b64": "AAAA", "media_type": "image/png"}]),
    ]
    writes: list[int] = []
    real = RR._write

    def counted(*a):
        writes.append(1)
        return real(*a)

    monkeypatch.setattr(RR, "_write", counted)
    await rec.save(msgs, 3)
    await rec.save(msgs, 3)  # nothing changed: not written again
    assert len(writes) == 1
    kept = _kept_list(app, "t1")
    assert [r["role"] for r in kept] == ["user"], "the history before the question is the session's"
    assert kept[0]["content"] == "look\n[an image was attached here]"
    assert "AAAA" not in json.dumps(kept)


# --------------------------------------------------------------------------- #
# never for a turn that is not a coding session's
# --------------------------------------------------------------------------- #


async def test_a_chat_turn_the_benchmark_and_the_post_lane_keep_nothing(home, work):
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    app = create_app(home)
    platform = app.state.platform
    (work / "x.txt").write_text("hi", encoding="utf-8")
    read = [ToolCall(id="r1", name="read_file", arguments={"path": str(work / "x.txt")})]
    wire(app, Script([read, read, read, read]))
    body = ChatBody(messages=[ChatMessageBody(role="user", content="read x")], tools=["read_file"],
                    workspace_dir=str(work), turn_id="chat-1")
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
    async for _ in gen:
        pass
    # The benchmark's shape: a harness turn under a build: session, no record.
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body.model_copy(update={"turn_id": "bench-1"}),
                                 harness="auto", session_id="build:bench-x", round_budget=5,
                                 stay_in_chat=True)
    async for _ in gen:
        pass
    await run_chat_turn(platform, BUILTIN_PERSONAS, body.model_copy(update={"turn_id": ""}))
    assert _records(app) == []


def test_only_the_coding_session_route_hands_the_lane_a_record():
    """The keyword is the one switch, and only `routes/build` sets it."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
    hits = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*.py")
        if re.search(r"reply_record=(?!reply_record\b|None\b)", p.read_text(encoding="utf-8"))
    )
    assert hits == ["daemon/routes/build.py"], hits
