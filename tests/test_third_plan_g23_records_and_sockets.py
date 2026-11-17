"""Third north-star fix plan, group 23 (part a): a reply that breaks says so
in plain words, the start's passes over stored replies stay off the event
loop, and a terminal connection with no token is held to this PC.

* RB12: a turn whose lane raised OUTSIDE its own error handling put the
  Python exception ("TypeError: ...") in the live stream and in the stored
  record. Now the frame carries a plain sentence in the shape the page draws
  for a failed turn (``detail`` / ``title`` / ``fix``); the log keeps the
  exception.
* CS9: every start read stored turn records on the event loop (the cut-off
  pass and the coding sessions' pass ran inside the lifespan, on the loop's
  own thread). Measured here through the real lifespan with a statement
  listener on the engine: which statements touch the turn records, on which
  thread, and which read the stored frames.
* TM20: with no token, ``_ws_token_ok`` let in every WebSocket — a terminal
  (a shell on this PC) included — from any address, while the HTTP side
  already refused another computer's address (v1.502.0). Now a socket with
  no token is held to this PC by the same rule.

Real SQLite in ``tmp_path``, the real app factory and lifespan; no sleeps,
no network, no provider.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.websockets import WebSocketDisconnect

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord, ChatTurnRecord
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app


# --------------------------------------------------------------------------- #
# 1. RB12: a lane that breaks outside its own error handling
# --------------------------------------------------------------------------- #
async def _breaking_lane():
    yield dt.sse("token", {"text": "first half"})
    raise TypeError("'NoneType' object is not subscriptable")


def _stored_frames(engine, turn_id: str) -> list:
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        return json.loads(row.frames_json or "[]") if row is not None else []


async def test_a_lane_that_breaks_ends_in_plain_words_live_and_on_its_record(tmp_path, caplog):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    logs = dt.TurnLogs(engine)
    lg = logs.start("g23-break", "", _breaking_lane())
    await asyncio.wait_for(lg.task, timeout=10)

    assert lg.status == "error"
    # The live stream's last frame (what a following page reads)...
    last_event, last_data = lg.frames[-1]
    assert last_event == "error"
    live = json.loads(last_data)
    # ...and the stored record's (what a reopened window replays).
    stored = _stored_frames(engine, "g23-break")
    assert stored[-1][0] == "error"
    kept = json.loads(stored[-1][1])
    for frame in (live, kept):
        words = " ".join(str(v) for v in frame.values())
        assert "TypeError" not in words and "NoneType" not in words, frame
        assert frame["detail"] == dt.LANE_BROKE_DETAIL
        # The shape the page draws for a failed turn (lib/failureLine.ts:
        # "title. fix").
        assert frame["title"] == dt.LANE_BROKE_TITLE
        assert frame["fix"] == dt.LANE_BROKE_FIX
    # What it had written is kept beside it.
    assert any(e == "token" and "first half" in d for e, d in stored)
    # The log keeps the exception for whoever reads it.
    assert any("TypeError" in (r.exc_text or "") or (r.exc_info and r.exc_info[0] is TypeError)
               for r in caplog.records), [r.getMessage() for r in caplog.records]


class _OwnWords(RuntimeError):
    """An exception of this package's own wording (its module is ours)."""


_OwnWords.__module__ = "iron_jarvis.tests_stand_in"


async def _lane_with_own_sentence():
    yield dt.sse("token", {"text": "a start"})
    raise _OwnWords("The notes folder was moved while the answer was being written.")


async def test_a_sentence_the_app_wrote_itself_is_kept_as_the_headline(tmp_path):
    app = create_app(str(tmp_path))
    logs = dt.TurnLogs(app.state.platform.engine)
    lg = logs.start("g23-own", "", _lane_with_own_sentence())
    await asyncio.wait_for(lg.task, timeout=10)
    frame = json.loads(lg.frames[-1][1])
    assert frame["title"] == "The notes folder was moved while the answer was being written."
    assert frame["detail"] == "The notes folder was moved while the answer was being written."
    assert frame["fix"] == dt.LANE_BROKE_FIX
    assert lg.status == "error"


# --------------------------------------------------------------------------- #
# 2. CS9: the start's passes over stored turn records
# --------------------------------------------------------------------------- #
_BIG = "x" * 200_000


def _frames(text: str) -> str:
    return json.dumps([
        ["token", json.dumps({"text": text})],
        ["done", json.dumps({"reply": text})],
    ])


def _seed_many(engine) -> dict[str, str]:
    """A few hundred small finished records (half of them old), a handful of
    large ones, three cut off while running, one old record whose reply never
    reached its chat."""
    now = utcnow()
    old = now - timedelta(days=dt.KEEP_RECORDS_DAYS + 3)
    with session_scope(engine) as db:
        chat = ChatThreadRecord(title="notes", messages_json=json.dumps([
            {"role": "user", "content": "explain the notes", "turnId": "due-old"},
        ]))
        db.add(chat)
        db.commit()
        db.refresh(chat)
        chat_id = chat.id
        for i in range(300):
            when = old if i % 2 else now
            db.add(ChatTurnRecord(
                id=f"small-{i}", thread_id="", status="done", home="home",
                frames_json=_frames(f"reply {i}"), started_at=when, updated_at=when,
            ))
        for i in range(6):
            when = old if i % 2 else now
            db.add(ChatTurnRecord(
                id=f"big-{i}", thread_id="", status="done", home="home",
                frames_json=_frames(_BIG), started_at=when, updated_at=when,
            ))
        for i in range(3):
            db.add(ChatTurnRecord(
                id=f"running-{i}", thread_id="", status="running",
                frames_json=_frames(_BIG), started_at=now, updated_at=now,
            ))
        db.add(ChatTurnRecord(
            id="due-old", thread_id=chat_id, status="done", home="due",
            frames_json=_frames("the kept reply"), started_at=old, updated_at=old,
        ))
        db.commit()
    return {"chat": chat_id}


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _boot_watching(home: str) -> list[dict]:
    """A real start through the lifespan, every statement on the turn
    records written down with whether it ran on the event loop's thread."""
    app = create_app(home)
    engine = app.state.platform.engine
    seen: list[dict] = []

    def _before(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        low = statement.lower()
        if "chatturnrecord" not in low:
            return
        seen.append({
            "sql": " ".join(statement.split())[:2000],
            "loop": _on_loop(),
            "thread": threading.current_thread().name,
            "frames": "chatturnrecord.frames_json" in low and low.lstrip().startswith("select"),
        })

    event.listen(engine, "before_cursor_execute", _before)
    try:
        with TestClient(app):
            pass
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return seen


#: The coding sessions' cut-off pass (``harness/sessions.py``
#: ``reconcile_interrupted_turns``, not this group's file) selects the stored
#: frames of EVERY interrupted record that names a chat, before it knows the
#: session needs one. Fixed in the same release: the old read is pinned by
#: name below, and the start test above refuses any such read.
_SESSIONS_CUTOFF_READ = (
    "select chatturnrecord.id, chatturnrecord.thread_id, chatturnrecord.frames_json"
)


def _booted(tmp_path) -> "tuple[str, list[dict], dict]":
    home = str(tmp_path)
    seed = create_app(home)
    ids = _seed_many(seed.state.platform.engine)
    seed.state.platform.engine.dispose()
    return home, _boot_watching(home), ids


def test_the_start_reads_stored_records_off_the_loop_and_only_the_columns_it_needs(tmp_path):
    home, seen, ids = _booted(tmp_path)
    assert seen, "the start touched no turn record: the listener saw nothing"

    on_loop = [s["sql"] for s in seen if s["loop"]]
    assert not on_loop, "read on the event loop at start: " + " | ".join(on_loop)
    # A SELECT that loads the stored frames is only ever one that names the
    # rows it must rewrite (the ones still marked running, or one record by
    # its id).
    wide = [
        s["sql"] for s in seen
        if s["frames"] and "chatturnrecord.status = ?" not in s["sql"].lower()
        and "chatturnrecord.id = ?" not in s["sql"].lower()
    ]
    assert not wide, "stored frames read for every record: " + " | ".join(wide)
    # Every read over the records is narrowed by the database, never every
    # row brought back to be sorted in Python.
    unfiltered = [
        s["sql"] for s in seen
        if s["sql"].lower().startswith("select") and " where " not in s["sql"].lower()
    ]
    assert not unfiltered, "every record read at start: " + " | ".join(unfiltered)

    # The passes kept their order and their results.
    from sqlmodel import select

    engine = create_app(home).state.platform.engine
    with session_scope(engine) as db:
        rows = {r.id: r for r in db.exec(select(ChatTurnRecord)).all()}
        chat = db.get(ChatThreadRecord, ids["chat"])
        msgs = json.loads(chat.messages_json or "[]")
    for i in range(3):  # cut off while running: marked, frames kept
        r = rows[f"running-{i}"]
        assert r.status == "interrupted"
        frames = json.loads(r.frames_json)
        assert frames[0][0] == "token"
        assert json.loads(frames[-1][1])["detail"] == dt.INTERRUPTED_DETAIL
    # the old reply that never reached its chat was written home, then removed
    assert any(m.get("role") == "assistant" and m.get("turnId") == "due-old" for m in msgs)
    assert "due-old" not in rows
    # old finished records went; new ones stayed
    assert all(f"small-{i}" not in rows for i in range(1, 300, 2))
    assert all(f"small-{i}" in rows for i in range(0, 300, 2))
    assert {"big-0", "big-2", "big-4"} <= set(rows) and not ({"big-1", "big-3", "big-5"} & set(rows))


def test_the_coding_sessions_cut_off_pass_reads_frames_only_for_a_session_that_needs_them(tmp_path):
    _home, seen, _ids = _booted(tmp_path)
    wide = [s["sql"] for s in seen if s["sql"].lower().startswith(_SESSIONS_CUTOFF_READ)]
    assert not wide, " | ".join(wide)
    # Whatever the column order: the pass's read over every interrupted
    # record that names a chat carries no stored frames; they are read one
    # record at a time, by id, for a session that needs them.
    over_all = [
        s["sql"] for s in seen
        if s["frames"] and "chatturnrecord.thread_id !=" in s["sql"].lower()
        and "chatturnrecord.id = ?" not in s["sql"].lower()
    ]
    assert not over_all, " | ".join(over_all)


def test_a_cut_off_pass_still_running_at_its_bound_holds_the_clean_up(tmp_path, monkeypatch):
    """The start goes on past the bound, and nothing is brought home or
    removed while the pass that marks cut-off turns has not finished (the
    order). The pass blocks on an event, never on time; the bound is made
    short so the start does not wait the full minute."""
    from iron_jarvis.daemon import app as app_mod

    release = threading.Event()
    marked: list[str] = []
    tidied: list[str] = []

    def _slow(engine):  # noqa: ANN001, ANN202
        marked.append("started")
        release.wait(30)
        return 0

    monkeypatch.setattr(dt, "reconcile_interrupted_turns", _slow)
    monkeypatch.setattr(dt, "home_and_tidy", lambda *a, **k: tidied.append("ran") or (0, 0))
    monkeypatch.setattr(app_mod, "_TURN_PASS_TIMEOUT_S", 0.2)
    app = create_app(str(tmp_path))
    try:
        with TestClient(app) as c:
            loops = c.get("/diagnostics").json()["background_loops"]
            tidied_while_held = list(tidied)
            release.set()  # the pass ends before the stop waits for its thread
        assert tidied_while_held == []
        assert marked == ["started"]
        assert tidied == [], "old records were tidied before the cut-off pass finished"
        assert loops["reconcile_chat_turns"]["ok"] is False
    finally:
        release.set()


# --------------------------------------------------------------------------- #
# 3. TM20: a WebSocket with no token is held to this PC
# --------------------------------------------------------------------------- #
@pytest.fixture()
def open_app(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    return create_app(str(tmp_path))


def test_with_no_token_a_terminal_socket_from_another_computer_is_refused(open_app, monkeypatch):
    # Through the real route: a socket let in looks its pane up; a refused
    # one is closed before that (1008 either way, so the look-up is the proof).
    looked: list[str] = []

    def _get(term_id):  # noqa: ANN001, ANN202
        looked.append(term_id)
        return None

    monkeypatch.setattr(open_app.state.platform.terminals, "get", _get)
    far = TestClient(open_app, client=("192.168.1.30", 50000))
    with pytest.raises(WebSocketDisconnect) as closed:
        with far.websocket_connect("/terminals/pane-1/ws") as ws:
            ws.receive_text()
    assert closed.value.code == 1008
    assert looked == [], "a socket from another computer reached the terminal"
    # This PC with no token still reaches it (the look-up runs).
    near = TestClient(open_app, client=("127.0.0.1", 50000))
    with pytest.raises(WebSocketDisconnect):
        with near.websocket_connect("/terminals/pane-1/ws") as ws:
            ws.receive_text()
    assert looked == ["pane-1"]


def test_with_no_token_the_socket_check_refuses_another_computers_address(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    from iron_jarvis.daemon.app import _ws_token_ok

    class _Ws:  # the check itself, for every socket route that uses it
        def __init__(self, host: str) -> None:
            self.client = type("C", (), {"host": host, "port": 1})()
            self.query_params: dict = {}

    assert _ws_token_ok(_Ws("192.168.1.30")) is False
    assert _ws_token_ok(_Ws("10.0.0.7")) is False
    assert _ws_token_ok(_Ws("::ffff:192.168.1.30")) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"])
def test_with_no_token_this_pc_still_opens_its_sockets(open_app, host):
    from iron_jarvis.daemon.app import _ws_token_ok

    class _Ws:
        def __init__(self) -> None:
            self.client = type("C", (), {"host": host, "port": 1})()
            self.query_params: dict = {}

    assert _ws_token_ok(_Ws()) is True


def test_with_a_token_the_rule_is_the_token_as_before(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "not-a-real-token-just-a-test-value")
    from iron_jarvis.daemon.app import _ws_token_ok

    class _Ws:
        def __init__(self, host: str, token: str) -> None:
            self.client = type("C", (), {"host": host, "port": 1})()
            self.query_params = {"token": token} if token else {}

    assert _ws_token_ok(_Ws("192.168.1.30", "not-a-real-token-just-a-test-value")) is True
    assert _ws_token_ok(_Ws("127.0.0.1", "")) is False
    assert _ws_token_ok(_Ws("127.0.0.1", "wrong")) is False


# --------------------------------------------------------------------------- #
# 4. RB12's sweep of the same two files: the other places that put a raw
#    exception in front of a person
# --------------------------------------------------------------------------- #
def test_a_living_document_that_fails_to_write_says_so_plainly(tmp_path, monkeypatch):
    from iron_jarvis.core.models import LiveDocRecord
    from iron_jarvis.daemon import app as app_mod
    from iron_jarvis.documents import writers

    def _denied(path, text):  # noqa: ANN001, ANN202
        raise PermissionError(13, "Permission denied", "C:\\reports\\weekly.md")

    monkeypatch.setattr(writers, "write_document", _denied)
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        doc = LiveDocRecord(name="weekly notes", prompt="summarise the week's notes")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    with TestClient(app) as c:
        r = c.post(f"/documents/live/{doc_id}/regenerate")
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == app_mod._LIVEDOC_FAILED
    with session_scope(engine) as db:
        assert db.get(LiveDocRecord, doc_id).last_error == app_mod._LIVEDOC_FAILED


@pytest.mark.parametrize("route, body", [
    ("share", {"mode": "compact", "format": "markdown", "provider": "no-such-model-host"}),
    ("crystallize", {"provider": "no-such-model-host"}),
])
def test_a_chat_one_shot_asked_of_a_model_that_cannot_be_used_names_it_plainly(tmp_path, route, body):
    """The lead's sweep: a shared digest and a workflow drafted from a chat
    answered "provider unavailable: <exception text>". Pins
    ``routes/chat._model_unusable`` at both routes."""
    from iron_jarvis.core.models import ChatThreadRecord

    app = create_app(str(tmp_path))
    with session_scope(app.state.platform.engine) as db:
        row = ChatThreadRecord(title="Notes", messages_json=json.dumps([
            {"role": "user", "content": "tidy the notes folder"},
            {"role": "assistant", "content": "Done: 12 notes sorted by month."},
        ]))
        db.add(row)
        db.commit()
        db.refresh(row)
        thread_id = row.id
    with TestClient(app) as c:
        r = c.post(f"/chat/threads/{thread_id}/{route}", json=body)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Error" not in detail and "provider unavailable" not in detail, detail
    assert "no-such-model-host" in detail and "Pick another model" in detail, detail


def test_a_workflow_asked_of_a_model_that_cannot_be_used_names_it_plainly(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        r = c.post("/workflows/generate", json={
            "description": "tidy the notes folder every morning", "provider": "no-such-model-host",
        })
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Error" not in detail and "provider unavailable" not in detail, detail
    assert "no-such-model-host" in detail, detail
