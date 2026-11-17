"""Third north-star fix plan, group 19 ("Stop is written down"), the coding
side: RB1 (decision G9), RB2, RB5, RB8 and NA8.

- RB1: a reply the user STOPPED, whose wind-down Iron Jarvis closing cut off,
  is never carried on by the next start. Its line says it was stopped, and
  "Continue from where it stopped" (its rounds kept under the key only the
  user's press reads) is the user's own press.
- RB2: a reply that FINISHED never gets an account's limit card, nor the wait
  that would later carry finished work on, for a limit a side call left on
  the turn's shared account pin.
- RB5: a reply whose end was seen and saved is that end whatever cancels it
  after; and the start-up pass never carries on a reply the session already
  holds as ended.
- RB8 / NA8: Delete holds the session for its whole length, so nothing Iron
  Jarvis starts by itself — and no attempts turn's last write — lands in a
  session being deleted.

Driven through the REAL app factory, the real build routes, the real reply
watch (``routes/build.recorded_turn``) and the real start-up resume pass;
only the models are scripted doubles. Nothing here reaches a real model or
service, and no value is shaped like a real credential.

THE STOP MARK (group 19 contract, point 1) is written by the STOP part of this
group (``ChatTurnRecord.stop_asked_at``, read by ``durable_turns.stop_asked``).
Until that is in the tree, :func:`stop_mark` stands in for the reader (a set
of turn ids); once it is, the mark is written on the record's own column and
the real reader answers.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy.pin import AccountPin
from iron_jarvis.providers.adapters.base import LLMMessage
from tests.test_build_resume_v1391 import (  # noqa: F401 — fixtures
    _cut_off_mid_reply,
    _reboot,
    _write,
    home,
    work,
)
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

GIT = shutil.which("git")


@pytest.fixture()
def stop_mark(monkeypatch):
    """``mark(app, turn_id)``: the user pressed Stop on that reply — written
    the way the contract stores it."""
    real = hasattr(ChatTurnRecord, "stop_asked_at") and hasattr(durable_turns, "stop_asked")
    asked: set[str] = set()
    if not real:
        monkeypatch.setattr(durable_turns, "stop_asked", lambda engine, turn_id: str(turn_id) in asked,
                            raising=False)

    def mark(app, turn_id: str) -> None:
        if not real:
            asked.add(turn_id)
            return
        engine = app.state.platform.engine
        writer = getattr(durable_turns, "write_stop_mark", None)
        if writer is not None:  # the stop route's own write (it makes a record not yet written)
            assert writer(engine, turn_id, utcnow())
            return
        with session_scope(engine) as db:
            rec = db.get(ChatTurnRecord, turn_id)
            assert rec is not None, "the reply has a record"
            rec.stop_asked_at = utcnow()
            db.add(rec)
            db.commit()

    return mark


def _line(app, sid: str, turn_id: str) -> dict:
    return next(m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == turn_id)


# --------------------------------------------------------------------------- #
# RB1 (decision G9): a stopped reply is never carried on by itself
# --------------------------------------------------------------------------- #


async def test_a_reply_you_stopped_is_shown_stopped_and_continued_only_by_your_press(home, work, stop_mark):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    # Stop is pressed (and written down), and Iron Jarvis closes before the
    # reply has wound down: the shutdown cuts the reply watch off.
    await _cut_off_mid_reply(first, sid)
    stop_mark(first, "bt-1")

    second = _reboot(home)
    script = wire(second, Script(["Never said."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == [], "the stopped reply's steps were run again by itself"
    assert not any(m.get("continuation") for m in messages_of(second, sid)), "nothing carried it on"

    # Its line says it was stopped — the user's own press, never a restart.
    line = _line(second, sid, "bt-1")
    assert line["error"] == S.STOPPED_LINE
    assert line["interrupted"] is True and line["canContinue"] is True
    engine = second.state.platform.engine
    assert not RR.has_record(engine, "bt-1"), "the start-up pass can never see it again"
    assert RR.has_failed(engine, "bt-1"), "its rounds are kept for the user's press"
    # The page reads the same line.
    status, view = await asgi(second, "GET", f"/build/sessions/{sid}")
    assert status == 200
    shown = next(m for m in view["messages"] if m["role"] == "assistant" and m.get("turnId") == "bt-1")
    assert shown["error"] == S.STOPPED_LINE and shown["canContinue"] is True

    # The user's own press: "Continue from where it stopped".
    script = wire(second, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    status, raw = await open_turn(second, sid, {"text": "", "continue_turn": "bt-1", "turn_id": "bt-go"},
                                  timeout=60)
    assert status == 200 and "event: done" in raw, raw[-400:]
    sent = script.calls[0]["messages"]
    assert sent[-1].content == build_routes.STOPPED_RESUME_NOTE
    assert [m.tool_call_id for m in sent if m.role == "tool"] == ["c1", "c2"], "it goes on from its own rounds"
    q = next(m for m in messages_of(second, sid) if m["role"] == "user" and m.get("continuedFrom") == "bt-1")
    assert q["content"] == build_routes.STOPPED_RESUME_QUESTION and q["continuation"] is True
    assert (work / "three.txt").read_text() == "3"


async def test_a_reply_you_stopped_with_no_round_kept_offers_retry_alone(home, work, stop_mark, monkeypatch):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    stop_mark(first, "bt-1")
    # Only its question was kept (stopped before its first round was saved).
    with session_scope(first.state.platform.engine) as db:
        row = db.get(RR.RunTranscript, RR.run_id_for("bt-1"))
        row.messages_json = json.dumps(json.loads(row.messages_json)[:1])
        db.add(row)
        db.commit()

    second = _reboot(home)
    script = wire(second, Script(["Never said."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []
    line = _line(second, sid, "bt-1")
    assert line["error"] == S.STOPPED_LINE and "canContinue" not in line
    engine = second.state.platform.engine
    assert not RR.has_record(engine, "bt-1") and not RR.has_failed(engine, "bt-1")


async def test_a_reply_cut_off_by_a_restart_with_no_stop_is_still_carried_on(home, work, stop_mark):
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    stop_mark(first, "bt-other")  # another reply's stop is not this one's

    second = _reboot(home)
    script = wire(second, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    assert await build_routes.resume_after_restart(second.state.d) == [sid]
    await until(lambda: script.calls, 30)
    assert script.calls[0]["messages"][-1].content == RR.RESUME_NOTE
    logs = second.state.platform.turn_logs
    await until(lambda: not logs.running(), 30)
    assert _line(second, sid, "bt-1")["error"] == durable_turns.INTERRUPTED_DETAIL


def test_the_next_turns_model_reads_a_stopped_line_as_cut_off_never_as_an_error():
    note = S.reply_end_note({"role": "assistant", "content": "", "interrupted": True, "error": S.STOPPED_LINE})
    assert S.REPLY_CUT_OFF in note and S.REPLY_ERROR not in note


# --------------------------------------------------------------------------- #
# RB2: a limit a side call left on the pin never reaches a reply that finished
# --------------------------------------------------------------------------- #


def _pin(limit: bool = True) -> AccountPin:
    pin = AccountPin(profile_id="acct-a", ij_provider="claude-cli", title="Work")
    pin.leased.append("acct-a")  # the reply's own calls were served
    if limit:
        pin.limit = {
            "account": "acct-a", "account_name": "Work", "provider": "claude-cli", "vendor": "claude",
            "kind": "usage", "resets_at": None, "resumable": True,
        }
    return pin


async def _watch(app, sid: str, turn_id: str, end: tuple[str, dict], pin: AccountPin) -> list[str]:
    async def frames():
        yield durable_turns.sse("token", {"text": "Finished the notes."})
        yield durable_turns.sse(*end)

    platform = app.state.platform
    return [c async for c in build_routes.recorded_turn(platform.engine, sid, turn_id, frames(),
                                                        pin=pin, platform=platform)]


async def test_a_reply_that_finished_never_gets_a_limit_card_or_a_wait(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    S.update(app.state.platform.engine, sid, setup={"account": "acct-a"})
    # A side call (the finish check's reviewer, a helper, a summary) met the
    # account's limit and left it on the shared pin; the reply itself ended.
    chunks = await _watch(app, sid, "bt-ok", ("done", {"reply": "Finished the notes."}), _pin())
    raw = "".join(chunks)
    assert "event: account_limit" not in raw
    setup = setup_of(app, sid)
    assert not setup.get("account_limit"), "a limit card on a reply that finished"
    assert not setup.get("account_wait"), "a wait armed that would carry finished work on"
    line = _line(app, sid, "bt-ok")
    assert "accountLimit" not in line and line["content"] == "Finished the notes."


async def test_a_reply_that_ended_on_its_limit_still_gets_its_card_and_wait(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    S.update(app.state.platform.engine, sid, setup={"account": "acct-a"})
    chunks = await _watch(app, sid, "bt-lim", ("error", {"detail": "The account reached its limit."}), _pin())
    assert any("event: account_limit" in c for c in chunks)
    setup = setup_of(app, sid)
    assert setup["account_limit"]["turn_id"] == "bt-lim"
    assert setup["account_wait"]["auto"] is True


async def test_a_reply_you_stopped_never_arms_the_wait_by_itself(home, work, stop_mark):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    S.update(app.state.platform.engine, sid, setup={"account": "acct-a"})
    stop_mark(app, "bt-stopped-lim")
    await _watch(app, sid, "bt-stopped-lim", ("error", {"detail": "The account reached its limit."}), _pin())
    setup = setup_of(app, sid)
    assert setup["account_limit"]["turn_id"] == "bt-stopped-lim", "the card is still shown"
    assert not setup.get("account_wait"), "a stopped reply's wait armed by itself"


# --------------------------------------------------------------------------- #
# RB5: a reply that finished and was saved is never carried on again
# --------------------------------------------------------------------------- #


async def test_a_cancel_after_the_saved_end_leaves_no_kept_copy(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    platform = app.state.platform
    engine = platform.engine
    S.append_lines(engine, sid, [{"role": "user", "content": "write the notes", "turnId": "bt-gap"}])
    record = RR.ReplyRecord(engine, sid, "bt-gap")
    await record.save([LLMMessage(role="user", content="write the notes"),
                       LLMMessage(role="assistant", content="Finished the notes.")], 1)
    assert RR.has_record(engine, "bt-gap")
    closing = asyncio.Event()

    async def frames():
        yield durable_turns.sse("token", {"text": "Finished the notes."})
        yield durable_turns.sse("done", {"reply": "Finished the notes."})
        await closing.wait()  # the lane is still closing when the app stops

    seen: list[str] = []

    async def drive():
        async for chunk in build_routes.recorded_turn(engine, sid, "bt-gap", frames(),
                                                      platform=platform, record=record):
            seen.append(chunk)

    task = asyncio.create_task(drive())
    assert await until(lambda: any("event: done" in c for c in seen), 30)
    task.cancel()  # the shutdown, in the moment after the save
    with pytest.raises(asyncio.CancelledError):
        await task
    line = _line(app, sid, "bt-gap")
    assert line["content"] == "Finished the notes." and "interrupted" not in line
    assert not RR.has_record(engine, "bt-gap"), "a finished reply kept a copy the next start carries on"


async def test_the_start_up_pass_never_carries_on_a_reply_the_session_holds_as_finished(home, work, monkeypatch):
    first = create_app(home)
    sid = new_session(first, work)["id"]

    async def lost(self) -> None:  # the app is gone before the kept copy is removed
        return None

    monkeypatch.setattr(RR.ReplyRecord, "forget", lost)
    wire(first, Script([_write("c1", "one.txt", "1"), "All done."]))
    status, raw = await open_turn(first, sid, {"text": "make one file", "turn_id": "bt-fin"})
    assert status == 200 and "event: done" in raw
    engine = first.state.platform.engine
    def on_disk() -> str:
        with session_scope(engine) as db:
            rec = db.get(ChatTurnRecord, "bt-fin")
            return str(rec.status) if rec is not None else ""

    assert await until(lambda: on_disk() == "done", 30)
    assert RR.has_record(engine, "bt-fin")
    with session_scope(engine) as db:  # the crash: its record was never closed
        rec = db.get(ChatTurnRecord, "bt-fin")
        rec.status = "running"
        db.add(rec)
        db.commit()
    monkeypatch.undo()

    second = _reboot(home)
    script = wire(second, Script(["Never said."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == [], "a finished reply was carried on again"
    assert not RR.has_record(second.state.platform.engine, "bt-fin")
    assert len([m for m in messages_of(second, sid) if m["role"] == "assistant"]) == 1


# --------------------------------------------------------------------------- #
# RB8: Delete holds the session while it moves folders
# --------------------------------------------------------------------------- #


async def test_nothing_starts_by_itself_in_a_session_being_deleted(home, work, monkeypatch):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["Never said."]))
    reached, gate = threading.Event(), threading.Event()
    real = A.discard_for_delete

    def slow(platform, row):  # moving the attempts' folders takes a while
        reached.set()
        gate.wait(60)
        return real(platform, row)

    monkeypatch.setattr(A, "discard_for_delete", slow)
    deleting = asyncio.create_task(asgi(app, "DELETE", f"/build/sessions/{sid}"))
    try:
        assert await until(reached.is_set, 30)
        with pytest.raises(HTTPException) as refused:
            await build_routes.start_build_turn(app.state.d, sid, "Carry on.", "bt-self",
                                                continuation=True, started_by="wait")
        assert refused.value.status_code == 409
        assert refused.value.detail == build_routes.DELETING_SENTENCE
    finally:
        gate.set()
        status, out = await asyncio.wait_for(deleting, 60)
    assert status == 200, out
    assert script.calls == []
    assert S.get_row(app.state.platform.engine, sid) is None


async def test_a_refused_delete_leaves_the_session_usable(home, work, monkeypatch):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    monkeypatch.setattr(A, "discard_for_delete", lambda platform, row: "A folder could not be moved.")
    status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409 and "The session was not deleted." in out["detail"], out
    assert sid not in build_routes._STARTING and sid not in build_routes._DELETING
    wire(app, Script(["Here you are."]))
    status, raw = await open_turn(app, sid, {"text": "hello", "turn_id": "bt-after"})
    assert status == 200 and "event: done" in raw


# --------------------------------------------------------------------------- #
# NA8: deleting a session right after its attempts end
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(GIT is None, reason="git is not installed")
async def test_delete_waits_for_the_attempts_last_write_so_their_folders_go_with_it(tmp_path, monkeypatch):
    from tests.test_build_accounts_v1368 import enable, isolate, make_fake
    from tests.test_build_attempts_v1397 import (
        World,
        attempts_turn,
        card_of,
        failing_script,
        make_calc_repo,
        passing_script,
        wire_models,
    )
    from tests.test_build_attempts_v1397 import new_session as new_git_session
    from tests.test_build_git_v1375 import git, no_real_gh

    no_real_gh(monkeypatch)
    isolation = isolate(tmp_path, monkeypatch)
    next(isolation)
    fake = make_fake(tmp_path)
    try:
        app = create_app(str(tmp_path / "home"))
        repo = make_calc_repo(tmp_path / "proj")
        await enable(app)
        wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
        sid = (await new_git_session(app, repo, branch=False))["id"]

        # The attempts have ENDED; the turn is still adding up what they
        # spent before it writes their card on the reply.
        reached, gate = asyncio.Event(), asyncio.Event()
        real = A._keep_spend
        calls = {"n": 0}

        async def held(platform, plan):
            calls["n"] += 1
            if calls["n"] == 1:
                reached.set()
                await gate.wait()
            return await real(platform, plan)

        monkeypatch.setattr(A, "_keep_spend", held)
        turn = asyncio.create_task(attempts_turn(app, sid, "Try this with Codex and the local model: fix add.",
                                                 "na8-1"))
        try:
            await asyncio.wait_for(reached.wait(), 120)
            status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
        finally:
            gate.set()
        card = card_of(await asyncio.wait_for(turn, 120))
        folders = [Path(it["folder"]) for it in card["items"] if it.get("folder")]
        assert folders, card
        assert status == 409, (
            f"deleted while the attempts' card was not yet written: {out}; "
            f"left behind: {[str(f) for f in folders if f.exists()]}"
        )
        assert out["detail"] == build_routes.RUNNING_SENTENCE
        assert S.get_row(app.state.platform.engine, sid) is not None

        # Once the reply is written, Delete takes the attempts' folders to the
        # trash with it (their branches stay, as for the session's own).
        status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
        assert status == 200, out
        assert not any(f.exists() for f in folders), [str(f) for f in folders if f.exists()]
        for it in card["items"]:
            if it.get("branch"):
                assert git("branch", "--list", it["branch"], cwd=repo).strip(), (
                    "the attempts' branches stay (their commits are kept)"
                )
    finally:
        fake.stop()
        with pytest.raises(StopIteration):
            next(isolation)
