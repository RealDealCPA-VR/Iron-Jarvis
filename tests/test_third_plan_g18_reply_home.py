"""Third north-star fix plan, group 18 (daemon side): a reply's home is the
daemon (decision G6; findings CS5, CS6, CS7, RB9 and the daemon side of CS2
and CS10).

Before: only a Chat PAGE wrote a finished reply into its chat. A reply that
ended while no page followed it was in no chat and in no history search until
that chat was opened, and a start more than seven days later deleted its
record (and with it the reply's words); a coding session's unsaved reply was
deleted by age BEFORE the pass that puts it back looked; a new chat's first
reply cost nothing on that chat's usage; deleting a chat left its turn
records; two saves of one turn kept two replies.

Every test here runs the real ``DurableTurns`` / ``TurnLog``, the real
``chat_append`` writer and the real routes through the app factory on a
database in ``tmp_path``; the model is a stand-in stream (no provider). The
grace before a followed reply is written home is a loop timer that a test
ends with ``TurnLogs.home_due_now`` (or sets to 0): no real sleep.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import search_index, session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, ChatThreadRecord, ChatTurnRecord
from iron_jarvis.daemon import chat_append as CA
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_chat_turn_stop_v1241 import _scope
from tests.test_durable_chat_turns_v1336 import _gated_stream, _open, _until

MSG = [{"role": "user", "content": "explain the notes"}]
WHOLE = "first half, second half."


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _req(app, method: str, path: str, payload=None) -> tuple[int, dict]:
    """One buffered in-process request, any method."""
    body = json.dumps(payload).encode() if payload is not None else b""
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {**_scope(path, body), "method": method}
    if "?" in path:
        p, q = path.split("?", 1)
        scope = {**scope, "path": p, "raw_path": p.encode(), "query_string": q.encode()}
    await asyncio.wait_for(app(scope, receive, send), timeout=20)
    raw = b"".join(chunks)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw.decode("utf-8", "replace")}


async def _new_chat(app, turn_id: str, *, extra: "list | None" = None) -> str:
    """The page's at-send save: the question, carrying its turn's name."""
    status, out = await _req(app, "PUT", "/chat/threads/new", {
        "messages": [{"role": "user", "content": "explain the notes", "turnId": turn_id}, *(extra or [])],
    })
    assert status == 200, out
    return out["id"]


def _messages(engine, thread_id: str) -> list[dict]:
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, thread_id)
        return json.loads(r.messages_json or "[]") if r is not None else []


def _replies(engine, thread_id: str, turn_id: str = "") -> list[dict]:
    return [
        m for m in _messages(engine, thread_id)
        if m.get("role") == "assistant" and (not turn_id or m.get("turnId") == turn_id)
    ]


def _record(engine, turn_id: str):
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is not None:
            db.expunge(row)
        return row


def _logs(app) -> "dt.TurnLogs":
    """The platform's turn registry, made the way the routes make it."""
    if getattr(app.state.platform, "turn_logs", None) is None:
        app.state.platform.turn_logs = dt.TurnLogs(app.state.platform.engine)
    return app.state.platform.turn_logs


def _seen(app) -> list:
    got: list = []
    app.state.platform.event_bus.add_handler(
        lambda e: got.append(e.payload) if e.type == EventType.CHAT_THREAD_UPDATED else None
    )
    return got


async def _unwatched_turn(app, body: dict, stream) -> "dt.TurnLog":
    """Start a named turn, drop its window after the first words, let it end
    with NOBODY following it."""
    gate = asyncio.Event()
    app.state.platform.router.stream = stream(gate)
    drop = asyncio.Event()
    await _open(app, body, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get(body["turn_id"])
    assert lg is not None
    await _until(lambda: not lg.watched())
    assert not lg.watched()
    gate.set()
    await _until(lambda: lg.status != "running" and lg.task.done())
    return lg


def _gated(gate):
    return _gated_stream(gate, [])


def _seed_record(engine, turn_id: str, thread_id: str, *, status="done", home="due",
                 frames=None, days_old: float = 0.0) -> None:
    when = utcnow() - timedelta(days=days_old)
    with session_scope(engine) as db:
        db.add(ChatTurnRecord(
            id=turn_id, thread_id=thread_id, status=status, home=home,
            frames_json=json.dumps(frames if frames is not None else [
                ["token", json.dumps({"text": "the kept reply"})],
                ["done", json.dumps({"reply": "the kept reply"})],
            ]),
            started_at=when, updated_at=when,
        ))
        db.commit()


def _seed_chat(engine, messages: list, **cols) -> str:
    rec = ChatThreadRecord(title="notes", messages_json=json.dumps(messages), **cols)
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _boot(home: str) -> None:
    """A real start (the lifespan's own order), then a tidy stop."""
    with TestClient(create_app(home)):
        pass


# --------------------------------------------------------------------------- #
# 1. A turn that ends with nobody following it is in its chat at once (CS5)
# --------------------------------------------------------------------------- #
async def test_a_reply_that_ends_with_nobody_following_is_in_its_chat_and_search_at_once(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    seen = _seen(app)
    tid = await _new_chat(app, "home-1")
    lg = await _unwatched_turn(app, {"messages": MSG, "turn_id": "home-1", "thread_id": tid}, _gated)
    assert lg.status == "done"
    # At once: the reply is in the chat, and no grace is waiting.
    replies = _replies(engine, tid)
    assert len(replies) == 1, replies
    assert not app.state.platform.turn_logs.home_pending("home-1")
    assert replies[0]["content"] == WHOLE
    assert replies[0]["turnId"] == "home-1" and replies[0]["savedBy"] == "daemon"
    assert not replies[0].get("interrupted")
    # ...and in history search, by the same search the page's saves feed.
    assert tid in {h.thread_id for h in search_index(engine).search("second")}
    # The record is marked home; the page heard about it.
    assert _record(engine, "home-1").home == "home"
    await _until(lambda: any(p.get("turn_id") == "home-1" for p in seen))
    assert {"thread_id": tid, "who": "reply", "entries": 2, "turn_id": "home-1"} in seen


async def test_a_phone_chat_is_never_written_by_this_path(tmp_path):
    """A daemon-owned (messaging) chat has ONE writer (``comm/threads.py``)."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed_chat(engine, [{"role": "user", "content": "hi", "turnId": "phone-1"}], owner="daemon")
    await _unwatched_turn(app, {"messages": MSG, "turn_id": "phone-1", "thread_id": tid}, _gated)
    assert _replies(engine, tid) == []
    assert _record(engine, "phone-1").home == "home"  # not ours: never retried


# --------------------------------------------------------------------------- #
# 2. A page follows it: the page's save wins during the grace
# --------------------------------------------------------------------------- #
async def _followed_turn(app, body: dict) -> "dt.TurnLog":
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _gated_stream(gate, [])
    text = await _open(app, body)  # this window follows it to its end
    assert "event: done" in text
    lg = app.state.platform.turn_logs.get(body["turn_id"])
    await _until(lambda: lg.task.done())
    return lg


async def test_a_followed_reply_waits_and_the_pages_own_save_wins(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    logs = _logs(app)
    tid = await _new_chat(app, "page-1")
    lg = await _followed_turn(app, {"messages": MSG, "turn_id": "page-1", "thread_id": tid})
    assert lg.followed_at_end
    # The grace runs: nothing is written yet.
    assert logs.home_pending("page-1")
    assert _replies(engine, tid) == []
    # The page saves its own, richer message for the turn first.
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [
        {"role": "user", "content": "explain the notes", "turnId": "page-1"},
        {"role": "assistant", "content": WHOLE, "turnId": "page-1", "toolsUsed": ["read_file"]},
    ]})
    assert status == 200
    assert await logs.home_due_now("page-1") == ""  # the grace ends: nothing to do
    replies = _replies(engine, tid)
    assert len(replies) == 1 and replies[0].get("savedBy") is None
    assert replies[0]["toolsUsed"] == ["read_file"]
    assert _record(engine, "page-1").home == "home"


async def test_a_followed_reply_no_page_saved_is_written_when_the_grace_ends(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    logs = _logs(app)
    tid = await _new_chat(app, "page-2")
    await _followed_turn(app, {"messages": MSG, "turn_id": "page-2", "thread_id": tid})
    assert logs.home_pending("page-2")
    assert await logs.home_due_now("page-2") == "written"
    assert [m["savedBy"] for m in _replies(engine, tid)] == ["daemon"]
    # The grace is a timer of HOME_GRACE_S that ends by itself (0 here).
    monkeypatch.setattr(dt, "HOME_GRACE_S", 0.0)
    tid2 = await _new_chat(app, "page-3")
    await _followed_turn(app, {"messages": MSG, "turn_id": "page-3", "thread_id": tid2})
    await _until(lambda: _replies(engine, tid2))
    assert [m["content"] for m in _replies(engine, tid2)] == [WHOLE]


# --------------------------------------------------------------------------- #
# 3. A new chat's first reply: home and on the chat's usage once linked (CS6)
# --------------------------------------------------------------------------- #
def _chat_rows(engine) -> list[AgentRun]:
    from sqlmodel import select

    with session_scope(engine) as db:
        rows = list(db.exec(select(AgentRun).where(AgentRun.session_id == "chat")).all())
        for r in rows:
            db.expunge(r)
        return rows


async def test_a_new_chats_first_reply_comes_home_and_counts_once_the_link_arrives(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    # The turn runs and ENDS before the chat has an id.
    await _unwatched_turn(app, {"messages": MSG, "turn_id": "first-1"}, _gated)
    rows = _chat_rows(engine)
    assert len(rows) == 1 and rows[0].thread_id is None
    tid = await _new_chat(app, "first-1")
    status, out = await _req(app, "POST", "/chat/turns/first-1/thread", {"thread_id": tid})
    assert status == 200 and out["linked"] == "kept"
    # The usage row now counts on this chat.
    assert [r.thread_id for r in _chat_rows(engine)] == [tid]
    assert [m["content"] for m in _replies(engine, tid, "first-1")] == [WHOLE]
    assert _record(engine, "first-1").home == "home"
    status, usage = await _req(app, "GET", f"/chat/threads/{tid}/usage")
    assert status == 200 and usage["turns"] == 1


async def test_a_link_made_while_the_turn_runs_names_its_usage_row(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_stream(gate, [])
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "first-2"}, drop=drop, stop_after="first half")
    tid = await _new_chat(app, "first-2")
    status, out = await _req(app, "POST", "/chat/turns/first-2/thread", {"thread_id": tid})
    assert status == 200 and out["linked"] == "running"
    gate.set()
    lg = app.state.platform.turn_logs.get("first-2")
    await _until(lambda: lg.task.done())
    assert [r.thread_id for r in _chat_rows(engine)] == [tid]
    assert [m["content"] for m in _replies(engine, tid)] == [WHOLE]


# --------------------------------------------------------------------------- #
# 4. Start-up: marked records are written home; old ones are not touched
# --------------------------------------------------------------------------- #
def test_at_start_a_marked_finished_reply_not_in_its_chat_is_written_home(tmp_path):
    home = str(tmp_path)
    p = build_platform(home)
    tid = _seed_chat(p.engine, [{"role": "user", "content": "q", "turnId": "boot-1"}])
    _seed_record(p.engine, "boot-1", tid)
    p.engine.dispose()
    _boot(home)
    q = build_platform(home)
    replies = _replies(q.engine, tid)
    assert [(m["content"], m["turnId"], m["savedBy"]) for m in replies] == [
        ("the kept reply", "boot-1", "daemon")
    ]
    assert _record(q.engine, "boot-1").home == "home"
    q.engine.dispose()
    _boot(home)  # once: a second start writes nothing more
    r = build_platform(home)
    assert len(_replies(r.engine, tid)) == 1
    r.engine.dispose()


def test_at_start_a_record_from_an_older_version_is_never_written_again(tmp_path):
    """The hazard: an older version's records (no mark) whose replies the page
    saved WITHOUT a turn name. Nothing may append them again."""
    home = str(tmp_path)
    p = build_platform(home)
    tid = _seed_chat(p.engine, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "the kept reply"},
    ])
    _seed_record(p.engine, "old-1", tid, home=None)
    p.engine.dispose()
    _boot(home)
    q = build_platform(home)
    assert len(_replies(q.engine, tid)) == 1
    q.engine.dispose()


async def test_a_reply_merged_by_continue_is_not_written_again_at_start(tmp_path):
    """The page's Continue merges a reply into the next one, keeping only the
    NEWEST turn's name: the first turn was marked home by the page's save."""
    home = str(tmp_path)
    app = create_app(home)
    engine = app.state.platform.engine
    tid = await _new_chat(app, "cont-a")
    _seed_record(engine, "cont-a", tid)
    _seed_record(engine, "cont-b", tid)
    question = {"role": "user", "content": "explain the notes", "turnId": "cont-a"}
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [
        question, {"role": "assistant", "content": "the kept", "turnId": "cont-a"},
    ]})
    assert status == 200
    assert _record(engine, "cont-a").home == "home"
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [
        question, {"role": "assistant", "content": "the kept reply, carried on", "turnId": "cont-b"},
    ]})
    assert status == 200
    engine.dispose()
    _boot(home)
    q = build_platform(home)
    assert [m["content"] for m in _replies(q.engine, tid)] == ["the kept reply, carried on"]
    q.engine.dispose()


def test_a_reply_kept_inside_a_fork_is_not_written_again_at_start(tmp_path):
    """A Try again keeps the older reply inside a message's ``branch``."""
    home = str(tmp_path)
    p = build_platform(home)
    older = [
        {"role": "user", "content": "q", "turnId": "fork-a"},
        {"role": "assistant", "content": "the kept reply", "turnId": "fork-a"},
    ]
    tid = _seed_chat(p.engine, [
        {"role": "user", "content": "q again", "turnId": "fork-b",
         "branch": {"tails": [older, None]}},
        {"role": "assistant", "content": "a second answer", "turnId": "fork-b"},
    ])
    _seed_record(p.engine, "fork-a", tid)
    p.engine.dispose()
    _boot(home)
    q = build_platform(home)
    assert [m["content"] for m in _replies(q.engine, tid)] == ["a second answer"]
    assert _record(q.engine, "fork-a").home == "home"
    q.engine.dispose()


def test_an_old_record_whose_reply_is_not_home_is_kept_until_it_is(tmp_path, monkeypatch):
    home = str(tmp_path)
    p = build_platform(home)
    tid = _seed_chat(p.engine, [{"role": "user", "content": "q", "turnId": "aged-1"}])
    _seed_record(p.engine, "aged-1", tid, days_old=8)
    # The other old records: home already, chat gone, no chat at all.
    done_tid = _seed_chat(p.engine, [{"role": "user", "content": "q"}])
    _seed_record(p.engine, "aged-home", done_tid, home="home", days_old=8)
    _seed_record(p.engine, "aged-gone", "no-such-chat", days_old=8)
    _seed_record(p.engine, "aged-none", "", days_old=8)
    _seed_record(p.engine, "aged-old", done_tid, home=None, days_old=8)
    p.engine.dispose()
    # A start whose write into the chat fails: the record is KEPT, not aged out.
    monkeypatch.setattr(CA, "append_to_chat_thread",
                        lambda *a, **k: {"ok": False, "why": "could not append: OperationalError"})
    _boot(home)
    q = build_platform(home)
    assert _record(q.engine, "aged-1") is not None
    assert _replies(q.engine, tid) == []
    for gone in ("aged-home", "aged-gone", "aged-none", "aged-old"):
        assert _record(q.engine, gone) is None, gone
    q.engine.dispose()
    # The next start writes it home, THEN removes it (it is home now).
    monkeypatch.undo()
    _boot(home)
    r = build_platform(home)
    assert [m["content"] for m in _replies(r.engine, tid)] == ["the kept reply"]
    assert _record(r.engine, "aged-1") is None
    r.engine.dispose()


# --------------------------------------------------------------------------- #
# 5. One reply per turn: the PUT and the writer
# --------------------------------------------------------------------------- #
Q = {"role": "user", "content": "q", "turnId": "t1"}
PAGE = {"role": "assistant", "content": "page", "turnId": "t1"}
DAEMON = {"role": "assistant", "content": "daemon", "turnId": "t1", "savedBy": "daemon"}


@pytest.mark.parametrize("given, kept", [
    # No turn names anywhere: the very same list.
    ([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
      {"role": "assistant", "content": "b"}], None),
    # The daemon's line and the page's for one turn: the page's wins.
    ([Q, DAEMON, PAGE], [Q, PAGE]),
    ([Q, PAGE, DAEMON], [Q, PAGE]),
    # Two page lines for one turn: the later.
    ([Q, PAGE, {**PAGE, "content": "page again"}], [Q, {**PAGE, "content": "page again"}]),
    # Two daemon lines: the later.
    ([Q, DAEMON, {**DAEMON, "content": "d2"}], [Q, {**DAEMON, "content": "d2"}]),
    # Questions are never dropped, even two with one name; unnamed replies stay.
    ([Q, Q, {"role": "assistant", "content": "x"}, PAGE, {"role": "assistant", "content": "x"}],
     None),
    # Other turns and order are kept.
    ([Q, PAGE, {**Q, "turnId": "t2"}, {**DAEMON, "turnId": "t2"}, DAEMON],
     [Q, PAGE, {**Q, "turnId": "t2"}, {**DAEMON, "turnId": "t2"}]),
    # An agent's, a media job's or a panel's line is matched by its own name.
    ([Q, PAGE, {**PAGE, "sessionId": "s1"}, {**PAGE, "mediaJob": "m1"}, {**PAGE, "panelWho": "x"}],
     None),
])
def test_one_reply_per_turn(given, kept):
    out = CA.one_reply_per_turn([dict(m) for m in given])
    if kept is None:
        assert out == given
    else:
        assert out == kept


async def test_the_put_keeps_one_reply_per_turn_and_both_names(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "t1")
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [Q, DAEMON, PAGE]})
    assert status == 200
    msgs = _messages(engine, tid)
    assert [(m["role"], m["content"], m.get("turnId"), m.get("savedBy")) for m in msgs] == [
        ("user", "q", "t1", None), ("assistant", "page", "t1", None),
    ]
    # savedBy is stored unchanged on a line the page keeps.
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [Q, DAEMON]})
    assert [m.get("savedBy") for m in _replies(engine, tid)] == ["daemon"]


async def test_the_put_keeps_the_200_live_messages_rule_with_turn_names(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "t0")
    msgs = []
    for i in range(130):
        msgs += [{"role": "user", "content": f"q{i}", "turnId": f"t{i}"},
                 {"role": "assistant", "content": f"a{i}", "turnId": f"t{i}"}]
    msgs.append({"role": "assistant", "content": "a129 by the daemon", "turnId": "t129",
                 "savedBy": "daemon"})
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": msgs})
    assert status == 200
    live = _messages(engine, tid)
    assert len(live) == CA.MAX_MESSAGES
    assert live[-1]["content"] == "a129"
    assert CA.older_count(engine, tid) == 260 - CA.MAX_MESSAGES


def test_the_writer_skips_a_second_reply_for_a_turn(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed_chat(engine, [Q, PAGE])
    out = CA.append_to_chat_thread(engine, tid, [DAEMON])
    assert out["ok"] and out["appended"] == 0 and out["skipped"] == 1
    # ...a reply kept inside a fork counts too; a question with the name does not.
    forked = _seed_chat(engine, [{"role": "user", "content": "q2", "branch": {"tails": [[Q, PAGE], None]}}])
    assert CA.append_to_chat_thread(engine, forked, [DAEMON])["appended"] == 0
    asked = _seed_chat(engine, [Q])
    assert CA.append_to_chat_thread(engine, asked, [DAEMON])["appended"] == 1
    assert [m["turnId"] for m in _replies(engine, asked)] == ["t1"]


# --------------------------------------------------------------------------- #
# 6. Deleting a chat removes its turn records (CS7)
# --------------------------------------------------------------------------- #
async def test_deleting_a_chat_removes_its_turn_records(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "del-1")
    await _unwatched_turn(app, {"messages": MSG, "turn_id": "del-1", "thread_id": tid}, _gated)
    assert _record(engine, "del-1") is not None
    assert (await _req(app, "GET", "/chat/turns/del-1/stream"))[0] == 200
    status, _ = await _req(app, "DELETE", f"/chat/threads/{tid}")
    assert status == 200
    assert _record(engine, "del-1") is None
    assert app.state.platform.turn_logs.get("del-1") is None
    assert (await _req(app, "GET", "/chat/turns/del-1/stream"))[0] == 404
    assert (await _req(app, "GET", "/chat/turns/del-1"))[0] == 404


async def test_a_turn_still_running_when_its_chat_is_deleted_leaves_no_record(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "del-2")
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_stream(gate, [])
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "del-2", "thread_id": tid},
                drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("del-2")
    await _until(lambda: _record(engine, "del-2") is not None)
    status, _ = await _req(app, "DELETE", f"/chat/threads/{tid}")
    assert status == 200
    # Fourth plan G27 (owner decision D5): deleting a chat stops its reply,
    # as the user's own stop (it used to run on for up to an hour).
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped" and lg.stop_author == "you"
    gate.set()
    assert _record(engine, "del-2") is None
    assert app.state.platform.turn_logs.get("del-2") is None


# --------------------------------------------------------------------------- #
# 7. RB9: a coding reply older than seven days is still put back at start
# --------------------------------------------------------------------------- #
def test_an_old_unsaved_coding_reply_is_put_back_at_start_before_any_clean_up(tmp_path):
    from iron_jarvis.harness import sessions as S

    home = str(tmp_path / "home")
    folder = tmp_path / "work"
    folder.mkdir()
    p = build_platform(home)
    row = S.create(p.engine, str(folder), "", {})
    S.append_lines(p.engine, row.id, [{"role": "user", "content": "write it", "turnId": "code-1"}])
    _seed_record(p.engine, "code-1", row.id, days_old=8, frames=[
        ["token", json.dumps({"text": "Wrote it."})],
        ["done", json.dumps({"reply": "Wrote it."})],
    ])
    # A coding reply that DID reach its session: its old record goes.
    S.append_lines(p.engine, row.id, [
        {"role": "user", "content": "and this", "turnId": "code-2"},
        {"role": "assistant", "content": "Done.", "turnId": "code-2"},
    ])
    _seed_record(p.engine, "code-2", row.id, days_old=8)
    p.engine.dispose()
    _boot(home)
    q = build_platform(home)
    back = [m for m in _messages(q.engine, row.id)
            if m.get("role") == "assistant" and m.get("turnId") == "code-1"]
    assert len(back) == 1 and back[0]["content"] == "Wrote it.", back
    assert _record(q.engine, "code-2") is None
    q.engine.dispose()


def test_an_old_coding_record_still_needed_is_never_removed_by_age(tmp_path, monkeypatch):
    """The 'not home yet' rule, for a session: when putting it back fails at
    a start, the record survives the clean-up and the next start puts it back."""
    from iron_jarvis.harness import sessions as S

    home = str(tmp_path / "home")
    folder = tmp_path / "work"
    folder.mkdir()
    p = build_platform(home)
    row = S.create(p.engine, str(folder), "", {})
    S.append_lines(p.engine, row.id, [{"role": "user", "content": "write it", "turnId": "code-3"}])
    _seed_record(p.engine, "code-3", row.id, days_old=8, frames=[
        ["token", json.dumps({"text": "Wrote it."})],
        ["done", json.dumps({"reply": "Wrote it."})],
    ])
    p.engine.dispose()
    real = S.append_lines
    monkeypatch.setattr(S, "append_lines", lambda *a, **k: {"ok": False, "updated_at": None, "count": 0})
    _boot(home)
    q = build_platform(home)
    assert _record(q.engine, "code-3") is not None
    q.engine.dispose()
    monkeypatch.setattr(S, "append_lines", real)
    _boot(home)
    r = build_platform(home)
    assert [m["content"] for m in _messages(r.engine, row.id)
            if m.get("role") == "assistant" and m.get("turnId") == "code-3"] == ["Wrote it."]
    r.engine.dispose()


# --------------------------------------------------------------------------- #
# 8. GET /chat/turns/{id} (CS10)
# --------------------------------------------------------------------------- #
async def test_one_turn_by_its_name_live_from_its_record_and_unknown(tmp_path):
    app = create_app(str(tmp_path))
    tid = await _new_chat(app, "look-1")
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_stream(gate, [])
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "look-1", "thread_id": tid},
                drop=drop, stop_after="first half")
    status, live = await _req(app, "GET", "/chat/turns/look-1")
    assert status == 200
    assert set(live) == {"turn_id", "status", "running", "started_at", "thread_id"}
    assert (live["turn_id"], live["status"], live["running"], live["thread_id"]) == (
        "look-1", "running", True, tid)
    assert live["started_at"].endswith("Z")
    # The listing beside it still answers on its own path.
    status, running = await _req(app, "GET", "/chat/turns/running")
    assert status == 200 and any(r["turn_id"] == "look-1" for r in running["replies"])
    gate.set()
    logs = _logs(app)
    lg = logs.get("look-1")
    await _until(lambda: lg.task.done())
    assert (await _req(app, "GET", "/chat/turns/look-1"))[1]["status"] == "done"
    # Known only from its record (as after a restart): 200, not running.
    logs._logs.pop("look-1")
    status, rec = await _req(app, "GET", "/chat/turns/look-1")
    assert status == 200
    assert (rec["status"], rec["running"], rec["thread_id"]) == ("done", False, tid)
    assert (await _req(app, "GET", "/chat/turns/never-1"))[0] == 404


# --------------------------------------------------------------------------- #
# 9. Stopped, cut off and failed endings write what the page would have saved
# --------------------------------------------------------------------------- #
def _f(event, data):
    return [event, json.dumps(data)]


@pytest.mark.parametrize("frames, status, want", [
    ([_f("token", {"text": "Hello "}), _f("thinking", {"text": "hmm"}),
      _f("tool_call", {"id": "c", "name": "read_file"}), _f("token", {"text": "there."}),
      _f("done", {"reply": "Hello there."})], "done", ("Hello there.", False)),
    ([_f("token", {"text": "streamed"}), _f("done", {"reply": ""})], "done", ("streamed", False)),
    ([_f("done", {"reply": ""})], "done", ("(no response)", False)),
    ([_f("token", {"text": "wrong words"}), _f("reset", {"reason": "look_first"}),
      _f("token", {"text": "right"}), _f("done", {"reply": ""})], "done", ("right", False)),
    ([_f("token", {"text": "abcdef"}), _f("reset", {"drop": 3}), _f("token", {"text": "XY"}),
      _f("error", {"detail": "x"})], "error", ("abcXY", True)),
    ([_f("token", {"text": "half an ans"}), _f("error", {"detail": "boom"})], "error",
     ("half an ans", True)),
    ([_f("error", {"detail": "boom"})], "error", None),
    ([_f("token", {"text": "  "}), _f("error", {"detail": "boom"})], "error", None),
    ([_f("token", {"text": "so far"})], "stopped", ("so far", True)),
    ([_f("round", {"round": 0})], "stopped", None),
    ([_f("token", {"text": "cut"}), _f("error", {"detail": dt.INTERRUPTED_DETAIL})], "interrupted",
     ("cut", True)),
    ([_f("thinking", {"text": "only thought"})], "interrupted", None),
])
def test_the_reply_line_is_what_the_page_would_have_saved(frames, status, want):
    line = dt.reply_line("t9", frames, status)
    if want is None:
        assert line is None
        return
    content, cut = want
    assert line["content"] == content
    assert bool(line.get("interrupted")) is cut
    assert line["turnId"] == "t9" and line["savedBy"] == "daemon" and line["role"] == "assistant"


def test_a_failure_that_kept_work_writes_its_sentence_and_documents():
    line = dt.reply_line("t9", [_f("error", {
        "detail": "boom", "kept_note": "Two files were written before it failed.",
        "documents": ["C:/work/a.txt"],
    })], "error")
    assert line["content"] == "Two files were written before it failed."
    assert line["documents"] == ["C:/work/a.txt"] and not line.get("interrupted")


async def test_a_stopped_reply_nobody_follows_is_written_with_what_was_said(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "stop-1")
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_stream(gate, [])
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "stop-1", "thread_id": tid},
                drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("stop-1")
    await _until(lambda: not lg.watched())
    assert (await _req(app, "POST", "/chat/turns/stop-1/stop"))[0] == 200
    gate.set()
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped"
    replies = _replies(engine, tid)
    assert [(m["content"], m.get("interrupted")) for m in replies] == [("first half,", True)]


async def test_a_failure_before_any_words_puts_no_empty_reply_in_the_chat(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "fail-1")

    def failing(gate):
        async def stream(**kw):
            await gate.wait()
            raise RuntimeError("the model is down")
            yield {}  # pragma: no cover — makes this a generator

        return stream

    gate = asyncio.Event()
    app.state.platform.router.stream = failing(gate)
    drop = asyncio.Event()
    window = asyncio.create_task(
        _open(app, {"messages": MSG, "turn_id": "fail-1", "thread_id": tid}, drop=drop)
    )
    logs = await _until(lambda: getattr(app.state.platform, "turn_logs", None))
    lg = await _until(lambda: logs.get("fail-1"))
    drop.set()  # the window goes before the turn fails
    await window
    await _until(lambda: not lg.watched())
    gate.set()
    await _until(lambda: lg is not None and lg.task.done())
    assert lg.status == "error", lg.frames[-1:]
    assert _replies(engine, tid) == []
    assert _record(engine, "fail-1").home == "home"  # nothing to write: settled
