"""Third north-star plan, group 23 ("never stalls, kept at rest, plain
words"), the coding-session routes: RB6, RB7, RB11, RB13.

* RB6: a "Resume when ready" wait whose carry-on is REFUSED for a lasting
  reason (the folder is gone) ends at once and says why on its card; a
  passing refusal (the session busy with Compact) is tried again, but for a
  day at most — then the wait ends and says so too. Never every minute for
  ever without a word. The real ``resume_due`` and the real start; the
  account check and the account service are stand-ins (no Iron-Proxy).
* RB7: a reply's question that cannot be WRITTEN answers a plain sentence
  (never "no such coding session"), and a wait's carry-on whose question
  cannot be written leaves the wait and its card exactly as they were. The
  write is made to fail inside the real store (a spy on the stamp every
  transcript write takes), so the rollback is the database's own.
* RB11: while the session cannot be written, the reply's grant is tried
  again at most every ``SESSION_WRITE_RETRY_S`` (an injected clock) and the
  log holds ONE full error; a grant still not saved at the end is said on
  the last frame, kept, and written by the next reply's start.
* RB13: a folder whose CHECK fails is refused in one plain sentence: one
  lead-in, no exception name.

No sleeps for time: a clock is injected; ``until`` only waits for a reply
task the real route started.
"""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import sessions as S
from tests.test_build_resume_v1391 import home, work  # noqa: F401 — fixtures
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    asgi_get,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    until,
    wire,
)

ACCOUNT = "acct-a"


def _locked(*_a, **_k):
    raise OperationalError("UPDATE chatthreadrecord", {}, Exception("database is locked"))


def _card() -> dict:
    return {"account": ACCOUNT, "account_name": "Work", "provider": "claude-cli", "vendor": "claude",
            "kind": "usage", "resets_at": "", "turn_id": "bt-limit"}


def _armed(app, sid: str, *, due_at) -> dict:
    """The session on ACCOUNT, its limit card, and a "Resume when ready"
    wait due at ``due_at`` — as the routes store them."""
    wait = {"account": ACCOUNT, "account_name": "Work", "resets_at": "",
            "armed_at": (due_at - timedelta(hours=1)).isoformat(), "next_check_at": due_at.isoformat()}
    S.update(app.state.platform.engine, sid, setup={
        "account": ACCOUNT, "account_name": "Work", "account_limit": _card(), "account_wait": wait,
    })
    return setup_of(app, sid)["account_wait"]


@pytest.fixture
def accounts_on(monkeypatch):
    """Stand-ins for the account service (on) and the account check (usable
    now): the wait loop goes on to the REAL start."""
    monkeypatch.setattr(build_routes, "_svc_of", lambda _p: SimpleNamespace(enabled=True))
    monkeypatch.setattr(build_routes, "check_account", lambda _p, _s: None)


async def _card_view(app, sid: str) -> dict:
    status, view = await asgi_get(app, f"/build/sessions/{sid}")
    assert status == 200, view
    return view.get("account_limit") or {}


# --------------------------------------------------------------------------- #
# RB6: a refused carry-on of "Resume when ready"
# --------------------------------------------------------------------------- #


async def test_a_wait_refused_for_a_lasting_reason_ends_and_says_why(home, work, accounts_on):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    now = utcnow()
    _armed(app, sid, due_at=now - timedelta(minutes=1))
    shutil.rmtree(work)  # the session's folder is gone: no reset brings it back

    assert await build_routes.resume_due(app.state.d, now=now) == []
    setup = setup_of(app, sid)
    assert "account_wait" not in setup, "a wait refused for good was kept, to be tried every minute"
    assert setup.get("account_limit", {}).get("account") == ACCOUNT, "the card stays (Choose account)"
    words = (await _card_view(app, sid)).get("wait_ended") or ""
    assert words.startswith("Iron Jarvis stopped waiting to carry this reply on, because it cannot start: "
                            "This session's folder cannot be used now: "), words
    assert words.endswith("Once that is fixed, press Resume when ready, or send a message."), words
    # Nothing waits any more: the next pass does not try again.
    assert await build_routes.resume_due(app.state.d, now=now + timedelta(minutes=1)) == []
    # Pressing Resume when ready again waits again, and the words go.
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready", {})
    assert status == 200
    assert "wait_ended" not in await _card_view(app, sid)


async def test_a_wait_refused_for_a_passing_reason_is_tried_again_for_a_day_then_said(  # noqa: F811
        home, work, accounts_on):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    now = utcnow()
    _armed(app, sid, due_at=now - timedelta(minutes=1))
    build_routes._COMPACTING.add(sid)  # Compact holds the session: it passes
    try:
        assert await build_routes.resume_due(app.state.d, now=now) == []
        assert "account_wait" in setup_of(app, sid), "a passing refusal ended the wait"
        later = now + timedelta(seconds=build_routes.WAIT_GIVE_UP_S - 60)
        assert await build_routes.resume_due(app.state.d, now=later) == []
        assert "account_wait" in setup_of(app, sid), "ended before its day"
        assert "wait_ended" not in await _card_view(app, sid)
        # A day after its first refusal it is no longer tried: said instead.
        after = now + timedelta(seconds=build_routes.WAIT_GIVE_UP_S + 1)
        assert await build_routes.resume_due(app.state.d, now=after) == []
    finally:
        build_routes._COMPACTING.discard(sid)
    assert "account_wait" not in setup_of(app, sid), "a refused carry-on was tried for ever"
    assert (await _card_view(app, sid)).get("wait_ended") == build_routes.WAIT_GAVE_UP_WORDS.format(
        why=build_routes.COMPACTING_SENTENCE,
    )


def test_which_refusals_pass():
    passes = build_routes.wait_refusal_passes
    assert passes(HTTPException(409, build_routes.COMPACTING_SENTENCE))
    assert passes(HTTPException(409, build_routes.DELETING_SENTENCE))
    assert passes(HTTPException(503, build_routes.QUESTION_NOT_SAVED))
    assert not passes(HTTPException(409, "This session's folder cannot be used now: folder does not exist"))
    assert not passes(HTTPException(409, build_routes.ARCHIVED_SENTENCE))
    assert not passes(HTTPException(404, "no such coding session"))


# --------------------------------------------------------------------------- #
# RB7: a question that cannot be written
# --------------------------------------------------------------------------- #


async def test_a_message_that_cannot_be_saved_says_so_and_starts_nothing(home, work, monkeypatch):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Wrote it."]))
    monkeypatch.setattr(S, "_next_stamp", _locked)
    status, raw = await open_turn(app, sid, {"text": "write the notes", "turn_id": "q-1"})
    assert status == 503, raw
    assert build_routes.QUESTION_NOT_SAVED in raw and "no such coding session" not in raw, raw
    assert build_routes.QUESTION_NOT_SAVED == (
        "Your message could not be saved to the session, so nothing was started. Try sending it again."
    )
    assert messages_of(app, sid) == []
    assert app.state.platform.turn_logs.get("q-1") is None


async def test_a_message_that_cannot_be_saved_keeps_a_failed_replys_rounds(home, work, monkeypatch):  # noqa: F811
    """RB7: "Continue from where it stopped" is made moot by a NEWER MESSAGE,
    and a message that was never saved is not one. Pins that
    ``RR.drop_failed`` runs only after the question is saved."""
    from iron_jarvis.harness import reply_record as RR
    from tests.test_build_continue_failed_v14220 import _fail_mid_reply

    _patience_on(monkeypatch)
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    engine = app.state.platform.engine
    assert RR.has_failed(engine, "bt-f")
    wire(app, Script(["Something else."]))
    monkeypatch.setattr(S, "_next_stamp", _locked)
    status, raw = await open_turn(app, sid, {"text": "never mind", "turn_id": "bt-n"})
    assert status == 503 and build_routes.QUESTION_NOT_SAVED in raw, raw
    assert RR.has_failed(engine, "bt-f"), "a message that was never saved threw the kept rounds away"


async def test_a_continue_that_cannot_be_saved_puts_the_kept_rounds_back(home, work, monkeypatch):  # noqa: F811
    """RB7: the Continue TAKES the failed reply's kept rounds (they move to
    the new reply's key) before its question is saved; when the question
    cannot be saved the rounds go back under the failed reply's key, so the
    same Continue works once the session can be written. Pins
    ``_not_started``'s move back: without it the second Continue is a 409."""
    from iron_jarvis.harness import reply_record as RR
    from tests.test_build_continue_failed_v14220 import _fail_mid_reply, _write

    _patience_on(monkeypatch)
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    engine = app.state.platform.engine
    wire(app, Script([_write("c3", "three.txt", "3"), "All three made."]))
    with monkeypatch.context() as broken:
        broken.setattr(S, "_next_stamp", _locked)
        status, raw = await open_turn(app, sid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-c"})
        assert status == 503 and build_routes.QUESTION_NOT_SAVED in raw, raw
    assert RR.has_failed(engine, "bt-f"), "the kept rounds were not put back"
    assert not any(m.get("turnId") == "bt-c" for m in messages_of(app, sid))
    status, raw = await open_turn(app, sid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-c2"}, timeout=60)
    assert status == 200 and "event: done" in raw, raw[-400:]
    assert (work / "three.txt").read_text(encoding="utf-8") == "3"


def _patience_on(monkeypatch) -> None:
    """A failing model is tried the product's five times, with no waiting
    (``test_build_continue_failed_v14220``'s own autouse fixture)."""
    from iron_jarvis.harness import patience as P

    async def at_once(_seconds):
        return None

    monkeypatch.setattr(P, "WAITS_S", P.DEFAULT_WAITS_S)
    monkeypatch.setattr(P, "SLEEP", at_once)


async def test_a_session_that_is_gone_is_still_no_such_session(home, work, monkeypatch):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Wrote it."]))
    monkeypatch.setattr(S, "append_lines", lambda *a, **k: {"ok": False, "updated_at": None, "count": 0})
    status, raw = await open_turn(app, sid, {"text": "write the notes", "turn_id": "q-2"})
    assert status == 404 and "no such coding session" in raw, raw


async def test_a_carry_on_whose_question_cannot_be_saved_keeps_the_wait_and_its_card(  # noqa: F811
        home, work, monkeypatch):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Carried on."]))
    # The account lookup is a stand-in (no Iron-Proxy): the start goes on.
    monkeypatch.setattr(build_routes, "turn_account", lambda *_a: (None, ""))
    wait = _armed(app, sid, due_at=utcnow())
    before = setup_of(app, sid)
    monkeypatch.setattr(S, "_next_stamp", _locked)
    with pytest.raises(HTTPException) as refused:
        await build_routes.start_build_turn(app.state.d, sid, build_routes.CONTINUE_TEXT, "bt-w1",
                                            continuation=True, resume_of=wait, started_by="wait")
    assert refused.value.status_code == 503
    assert refused.value.detail == build_routes.QUESTION_NOT_SAVED
    after = setup_of(app, sid)
    assert after.get("account_wait") == before["account_wait"], "the wait was removed before the question"
    assert after.get("account_limit") == before["account_limit"], "the card was removed before the question"
    assert messages_of(app, sid) == []
    # Once the session can be written again, the same wait carries on.
    monkeypatch.undo()
    monkeypatch.setattr(build_routes, "turn_account", lambda *_a: (None, ""))
    lg = await build_routes.start_build_turn(app.state.d, sid, build_routes.CONTINUE_TEXT, "bt-w2",
                                             continuation=True, resume_of=wait, started_by="wait")
    await until(lambda: lg.status != "running")
    assert "account_wait" not in setup_of(app, sid)
    assert [m["turnId"] for m in messages_of(app, sid) if m["role"] == "user"] == ["bt-w2"]


# --------------------------------------------------------------------------- #
# RB11: a session that cannot be written while a reply runs
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _grant_frames(clock: _Clock, *, then_tokens: int, advance_at: "int | None" = None):
    async def frames():
        yield durable_turns.sse("token", {"text": "Running the tests."})
        yield durable_turns.sse("approval_resolved", {"id": "ap-1", "decision": "conversation", "tool": "shell"})
        for k in range(then_tokens):
            if advance_at is not None and k == advance_at:
                clock.t += build_routes.SESSION_WRITE_RETRY_S
            yield durable_turns.sse("token", {"text": "."})
        yield durable_turns.sse("done", {"reply": "Ran them."})

    return frames()


async def test_a_grant_that_cannot_be_saved_is_tried_sparingly_said_and_kept(  # noqa: F811
        home, work, monkeypatch, caplog):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    platform = app.state.platform
    clock = _Clock()
    monkeypatch.setattr(build_routes, "_session_clock", clock)
    tries: list[int] = []

    def locked_grant(*_a, **_k):
        tries.append(1)
        _locked()

    monkeypatch.setattr(S, "add_granted_tools", locked_grant)
    build_routes._UNSAVED_GRANTS.pop(sid, None)
    with caplog.at_level(logging.WARNING):
        chunks = [c async for c in build_routes.recorded_turn(
            platform.engine, sid, "g-1", _grant_frames(clock, then_tokens=40), platform=platform,
        )]
    # Once at the answer, once more at the end — never once per frame.
    assert len(tries) == 2, len(tries)
    full = [r for r in caplog.records if r.exc_info and "grant" in r.getMessage()]
    assert len(full) == 1, [r.getMessage() for r in caplog.records]
    done = dict(frames_of("".join(chunks)))["done"]
    assert build_routes.GRANT_NOT_SAVED in str(done.get("not_saved") or ""), done
    assert build_routes._UNSAVED_GRANTS.get(sid) == {"shell"}

    # The next reply runs with it and writes it on the session.
    monkeypatch.undo()
    wire(app, Script(["Done."]))
    status, raw = await open_turn(app, sid, {"text": "run them again", "turn_id": "g-2"})
    assert status == 200, raw
    assert "shell" in (setup_of(app, sid).get("granted_tools") or [])
    assert sid not in build_routes._UNSAVED_GRANTS


async def test_a_grant_is_tried_again_after_the_pause_and_then_saved(home, work, monkeypatch):  # noqa: F811
    app = create_app(home)
    sid = new_session(app, work)["id"]
    platform = app.state.platform
    clock = _Clock()
    monkeypatch.setattr(build_routes, "_session_clock", clock)
    real = S.add_granted_tools
    tries: list[int] = []

    def locked_once(engine, thread_id, names):
        tries.append(1)
        if len(tries) == 1:
            _locked()
        return real(engine, thread_id, names)

    monkeypatch.setattr(S, "add_granted_tools", locked_once)
    chunks = [c async for c in build_routes.recorded_turn(
        platform.engine, sid, "g-3", _grant_frames(clock, then_tokens=10, advance_at=5), platform=platform,
    )]
    assert len(tries) == 2, len(tries)
    assert "shell" in (setup_of(app, sid).get("granted_tools") or [])
    done = dict(frames_of("".join(chunks)))["done"]
    assert not done.get("not_saved"), done
    assert sid not in build_routes._UNSAVED_GRANTS


# --------------------------------------------------------------------------- #
# RB13: a folder check that fails
# --------------------------------------------------------------------------- #


async def test_a_folder_whose_check_fails_is_refused_in_one_plain_sentence(home, work, monkeypatch):  # noqa: F811
    from iron_jarvis.core import fs_policy

    def unreadable(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(fs_policy, "root_problem", unreadable)
    app = create_app(home)
    status, body = await asgi(app, "POST", "/build/sessions",
                              {"folder": str(work), "provider": "anthropic", "model": "claude-sonnet-4-5"})
    assert status == 400, body
    detail = body["detail"]
    assert detail == "This folder cannot be used: " + S.FOLDER_CHECK_FAILED, detail
    assert "PermissionError" not in detail and detail.count("cannot be used") == 1, detail
