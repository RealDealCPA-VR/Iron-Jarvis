"""Corrections in Chat › Code become preference suggestions (v1.414.0).

``routes/build.start_build_turn`` passed ``suggest_preferences=False``, so the
user telling a coding session "stop explaining every step" twice never became
the one-press Keep line a chat gets. Now a turn the USER typed asks for one
(never a turn Iron Jarvis started by itself); the reply line keeps the
question (``suggestion``, a line key) and the session's view folds in its
CURRENT answer from the row (kept / declined / gone) — a coding session's
lines are the daemon's to write, so the page cannot store the answer on them.

The look-back is the chat's own, unchanged: the session's earlier turns count
from the turn's body (as a chat's do) and coding sessions are never read as
"another conversation", so ONE correction can never mint by being counted
twice (from the body and from the session's own stored copy, whose first
line a Compact summary may have dropped). A correction SCOPED to one place in
the code ("in this repo", "in this module", "right now") is never a standing
preference (``corrections._ONE_OFF``). Build › Code (the user's real CLI) is
out of scope: its words never pass through Iron Jarvis's lanes.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import sessions as S
from iron_jarvis.learning import preferences as prefs
from iron_jarvis.learning.corrections import is_correction
from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, new_session, open_turn, until, wire


@pytest.fixture(autouse=True)
def _a_generous_lookback_budget(monkeypatch):
    # The PRODUCT's 2 s bound is wall clock; these tests pin WHAT is suggested.
    monkeypatch.setattr(prefs, "TURN_BUDGET_S", 60.0)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def _turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def _done(frames) -> dict:
    return [d for e, d in frames if e == "done"][-1]


# --------------------------------------------------------------------------- #
# the Keep line in a coding session
# --------------------------------------------------------------------------- #


async def test_a_correction_repeated_in_a_coding_session_is_offered_and_saved_on_the_reply(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([]))
    first = _done(await _turn(app, sid, "no tables please", "pt-1"))
    assert first.get("suggestion") is None
    second = _done(await _turn(app, sid, "stop using tables", "pt-2"))
    sug = second["suggestion"]
    assert sug is not None and sug["count"] == 2, second
    # Said in a coding session: the quote says so.
    assert {q["where"] for q in sug["quotes"]} == {"code"}
    # The reply line keeps the question (a line key the daemon writes).
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"][-1]
    assert reply["suggestion"]["id"] == sug["id"]
    assert "suggestion" in S.LINE_KEYS


async def test_the_sessions_view_says_the_current_answer(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([]))
    await _turn(app, sid, "no tables please", "pv-1")
    sug = _done(await _turn(app, sid, "stop using tables", "pv-2"))["suggestion"]
    client = TestClient(app)

    def shown():
        msgs = client.get(f"/build/sessions/{sid}").json()["messages"]
        return [m for m in msgs if m["role"] == "assistant"][-1]["suggestion"]

    assert "state" not in shown()  # still asking
    r = client.post(f"/memory/preferences/{sug['id']}/keep", json={"text": "Never use tables."})
    assert r.status_code == 200, r.text
    assert shown()["state"] == "kept" and shown()["text"] == "Never use tables."
    # Asked again (the row is deleted) → gone: the line renders nothing.
    assert client.post(f"/memory/preferences/{sug['id']}/ask-again").status_code in (200, 409)
    with session_scope(app.state.platform.engine) as db:
        from iron_jarvis.learning.models import LessonRecord

        row = db.get(LessonRecord, sug["id"])
        if row is not None:
            db.delete(row)
            db.commit()
    assert shown()["state"] == "gone"


async def test_a_declined_suggestion_reads_declined(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([]))
    await _turn(app, sid, "no emoji please", "pd-1")
    sug = _done(await _turn(app, sid, "stop using emojis", "pd-2"))["suggestion"]
    client = TestClient(app)
    assert client.post(f"/memory/preferences/{sug['id']}/decline").status_code == 200
    msgs = client.get(f"/build/sessions/{sid}").json()["messages"]
    assert [m for m in msgs if m["role"] == "assistant"][-1]["suggestion"]["state"] == "declined"


async def test_a_turn_iron_jarvis_starts_by_itself_never_asks(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    wire(app, Script([]))
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(kw)
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    d = app.state.d
    for i, (by, cont) in enumerate((("person", True), ("night", False), ("ci", False), ("person", False))):
        lg = await build_routes.start_build_turn(d, sid, "carry on", f"auto-{i}", continuation=cont,
                                                 started_by=by)
        await until(lambda i=i: len(seen) > i, 20)
        await until(lambda lg=lg: lg.status != "running", 20)
        await asyncio.sleep(0.05)
    assert [kw["suggest_preferences"] for kw in seen] == [False, False, False, True]


# --------------------------------------------------------------------------- #
# one session alone never mints from ONE correction
# --------------------------------------------------------------------------- #


def test_a_coding_sessions_own_stored_lines_are_never_another_conversation(app, work):
    """The trap: the session's row already holds the correction (its question
    is written before the turn runs, and a Compact summary can drop the
    session's first line from the body, so it no longer matches the body).
    Read as 'another conversation', ONE correction would count twice and
    mint. It must not."""
    engine = app.state.platform.engine
    row = S.create(engine, str(work), "a session", {"provider": "mock"})
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, row.id)
        rec.messages_json = json.dumps([
            {"role": "user", "content": "please read the README first"},
            {"role": "assistant", "content": "Read."},
            {"role": "user", "content": "stop using tables"},
        ])
        db.add(rec)
        db.commit()
    body = ChatBody(messages=[ChatMessageBody(role="user", content="stop using tables")], thread_id=row.id)
    assert prefs.suggest_from_turn(engine, body, where="code") is None
    # Control: the same correction in a CHAT does count (the pin is not vacuous).
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(title="a chat", messages_json=json.dumps(
            [{"role": "user", "content": "no tables"}, {"role": "assistant", "content": "Okay."}])))
        db.commit()
    got = prefs.suggest_from_turn(engine, body, where="code")
    assert got is not None and got["count"] == 2


def test_where_follows_the_turns_session():
    assert prefs.where_for_session("build:chat_1") == "code"
    assert prefs.where_for_session("chat") == ""
    assert prefs.where_for_session(None) == ""
    assert "code" in prefs.WHERES


# --------------------------------------------------------------------------- #
# a correction scoped to one place in the code is not a preference
# --------------------------------------------------------------------------- #

CODE_SCOPED_MUST_NOT = [
    "too long in this repo",
    "don't explain everything in this repo",
    "stop apologizing in this project",
    "less verbose in this codebase",
    "too verbose in this module",
    "stop explaining yourself in this package",
    "no caveats in this workspace",
    "no more recaps in this repository",
    "no preamble in this session",
    "keep it short in this conversation",
    "skip the explanation in this case",
    "don't ask me first in this one",
    "stop asking before you edit in this repo",
    "be brief right now",
    "too long today",
    "be terse today",
    "be more concise here",
    "too long here",
    "don't ask permission here",
    "no bullet points in here",
    "less jargon at the moment",
    "too wordy in this crate",
    "skip the preamble throughout this monorepo",
]

STILL_CORRECTIONS = [
    "stop apologizing",
    "too long",
    "don't ask me first",
    "skip the preamble",
    "be more concise",
    "stop explaining everything",
    "you keep asking for permission",
    "no tables please",
    "less jargon",
    "stop using bullet points",
]


@pytest.mark.parametrize("text", CODE_SCOPED_MUST_NOT)
def test_a_code_scoped_correction_is_not_a_standing_preference(text):
    assert is_correction(text) is False, text


@pytest.mark.parametrize("text", STILL_CORRECTIONS)
def test_an_ordinary_correction_still_is_one(text):
    assert is_correction(text) is True, text
