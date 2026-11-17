"""Third north-star fix plan, group 18, SB3 (decision G7): the browser
sidebar's conversation is a chat.

THE DEFECT: the sidebar's conversation lived only in the daemon's memory
(``PanelTurns._history``). A restart of Iron Jarvis (every update) forgot it
while the panel in the browser still showed it, so "now the second one" was
answered with no context and nothing said so; and once the panel was closed
the conversation was in no chat list and no history search.

NOW: every exchange is written into a chat the DAEMON writes (the pattern of a
phone chat: ``owner == "daemon"``, written through ``comm/threads.py``),
marked as the browser sidebar's. A restart reads it back on the panel's
``open``; Reset starts a new chat and leaves the old one in the list; a chat
deleted in the chat list starts a fresh one and the panel is told in one
sentence. Nothing treats it as a phone chat: no reply is ever sent anywhere.

Driven through the REAL app, the REAL pairing socket and the REAL chat lane
(``tests/_fakes/panel_harness.py``), with the model as the only double, and a
real database under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.browser.panel_chat import (
    NOT_AVAILABLE,
    OPEN_KEY,
    SIDEBAR_CHANNEL,
    SIDEBAR_DISPLAY,
    SidebarChat,
)
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.providers.adapters.base import LLMResponse
from tests._fakes.panel_harness import RealApp, _headers

_ADDON = Path(__file__).resolve().parents[1] / "extensions" / "chrome" / "src"
PANEL_TS = _ADDON / "sidepanel" / "sidepanel.ts"
_DASH = Path(__file__).resolve().parents[1] / "dashboard"
BANNER_TSX = _DASH / "components" / "chat" / "CommThreadBanner.tsx"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _flatten(messages: list) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", b))
                for b in content
            )
        rows.append({"role": str(role or ""), "content": str(content or "")})
    return rows


def _recording_answers(app: RealApp, answers: list[str]) -> list[list[dict[str, str]]]:
    """A model that answers ``answers`` in order and KEEPS the messages it was handed."""
    seen: list[list[dict[str, str]]] = []
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        seen.append(_flatten(kw.get("messages") or []))
        n = state["n"]
        state["n"] += 1
        text = answers[n] if n < len(answers) else "All done."
        yield {"type": "text", "text": text}
        yield {
            "type": "final",
            "response": LLMResponse(text=text, usage={}),
            "provider": "mock",
            "model": "mock",
        }

    app.platform.router.stream = fake_stream
    return seen


def _convo(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["role"] in ("user", "assistant")]


def _sidebar_chats(app: RealApp) -> list[ChatThreadRecord]:
    with session_scope(app.platform.engine) as db:
        rows = db.exec(
            select(ChatThreadRecord).where(ChatThreadRecord.comm_channel == SIDEBAR_CHANNEL)
        ).all()
        for r in rows:
            db.expunge(r)
    return sorted(rows, key=lambda r: r.created_at)


def _messages(row: ChatThreadRecord) -> list[dict[str, str]]:
    return [
        {"role": m["role"], "content": m["content"]} for m in json.loads(row.messages_json or "[]")
    ]


def _is_open(row: ChatThreadRecord) -> bool:
    setup = json.loads(row.setup_json or "{}") if row.setup_json else {}
    return bool(setup.get(OPEN_KEY))


def _histories(app: RealApp) -> list[dict]:
    return [p for e, p in app.panel.events() if e == P.PANEL_EVENT_HISTORY]


def _open(app: RealApp, **params: Any) -> dict:
    n = len(_histories(app))
    app.panel.send(P.PANEL_ACTION_OPEN, **params)
    return app.panel.wait_for(P.PANEL_EVENT_HISTORY, "open never replayed the conversation", nth=n + 1)


def _ask(app: RealApp, text: str, n: int) -> None:
    app.panel.send(P.PANEL_ACTION_SEND, text=text)
    app.panel.wait_for(P.PANEL_EVENT_DONE, f"message {n} never finished", nth=n)


# --------------------------------------------------------------------------- #
# a. the exchange is a chat: daemon-owned, marked, listed, searchable
# --------------------------------------------------------------------------- #


def test_an_exchange_is_saved_as_a_daemon_owned_sidebar_chat_listed_and_searchable(
    tmp_path, monkeypatch
):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["The zephyrine report has three parts."])
        _ask(app, "How many parts has the zephyrine report?\nsecond line", 1)

        chats = _sidebar_chats(app)
        assert len(chats) == 1, "no chat was saved for the sidebar's exchange"
        chat = chats[0]
        assert chat.owner == "daemon"
        assert chat.comm_channel == SIDEBAR_CHANNEL and chat.comm_display == SIDEBAR_DISPLAY
        assert _is_open(chat)
        assert chat.title == "How many parts has the zephyrine report?"
        # Exactly what the sidebar's conversation holds (what the user read).
        assert _messages(chat) == [
            {"role": "user", "content": "How many parts has the zephyrine report?\nsecond line"},
            {"role": "assistant", "content": "The zephyrine report has three parts."},
        ]
        assert _messages(chat) == _flatten(app.platform.browser.backend.panel_handler.__self__._history)

        listed = app.client.get("/chat/threads", headers=_headers()).json()["threads"]
        row = next((t for t in listed if t["id"] == chat.id), None)
        assert row is not None, "the sidebar's chat is not in the chat list"
        assert row["owner"] == "daemon" and row["comm_channel"] == SIDEBAR_CHANNEL

        hits = app.client.get(
            "/search/history", params={"q": "zephyrine"}, headers=_headers()
        ).json()["hits"]
        assert any(chat.id in json.dumps(h) for h in hits), hits


# --------------------------------------------------------------------------- #
# b. a restart restores the conversation
# --------------------------------------------------------------------------- #


def test_a_restart_restores_the_conversation_for_the_model_and_the_replay(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["Noted: the first link is the report."])
        _ask(app, "Find the links on this page.", 1)

    # A NEW app on the same home: the restart every update does.
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = _recording_answers(app, ["The second one is the notes."])
        replay = _open(app, shown=2)
        assert replay["turns"] == [
            {"role": "user", "text": "Find the links on this page."},
            {"role": "assistant", "text": "Noted: the first link is the report."},
        ], replay
        assert "notice" not in replay, "a restored conversation was called unavailable"
        _ask(app, "Now the second one.", 1)
        rows = _convo(seen[0])
        assert [r["role"] for r in rows] == ["user", "assistant", "user"], rows
        assert "Find the links" in rows[0]["content"]
        assert "the first link is the report" in rows[1]["content"]
        # Still ONE chat: the restored conversation carried on in it.
        chats = _sidebar_chats(app)
        assert len(chats) == 1 and len(_messages(chats[0])) == 4


def test_a_restart_with_nothing_to_restore_says_so_once_when_the_panel_shows_a_conversation(
    tmp_path, monkeypatch
):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["Noted."])
        _ask(app, "Remember 41.", 1)
        chat_id = _sidebar_chats(app)[0].id
        assert app.client.delete(f"/chat/threads/{chat_id}", headers=_headers()).status_code == 200

    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        # A panel with an empty transcript (a fresh one): nothing to explain.
        assert "notice" not in _open(app)
        first = _open(app, shown=2)
        assert first["turns"] == [] and first.get("notice") == NOT_AVAILABLE, first
        # A tab switch posts `open` again: the sentence is not repeated.
        assert "notice" not in _open(app, shown=2)


# --------------------------------------------------------------------------- #
# c. Reset starts a new chat; the old one stays
# --------------------------------------------------------------------------- #


def test_reset_starts_a_new_chat_and_leaves_the_old_one_untouched(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = _recording_answers(app, ["Noted.", "Fresh."])
        _ask(app, "Remember 41.", 1)
        before = _sidebar_chats(app)
        assert len(before) == 1
        n = len(_histories(app))
        app.panel.send(P.PANEL_ACTION_RESET)
        app.panel.wait_for(P.PANEL_EVENT_HISTORY, "reset never answered", nth=n + 1)
        _ask(app, "What number?", 2)

        chats = _sidebar_chats(app)
        assert len(chats) == 2, "the exchange after Reset did not start a new chat"
        old, new = chats
        assert old.id == before[0].id
        assert _messages(old) == _messages(before[0]), "Reset changed the old chat"
        assert not _is_open(old), "the old chat is still the sidebar's conversation"
        assert _is_open(new)
        assert _messages(new) == [
            {"role": "user", "content": "What number?"},
            {"role": "assistant", "content": "Fresh."},
        ]
        assert [r["role"] for r in _convo(seen[1])] == ["user"]

    # And a restart after the Reset restores the NEW conversation, not the old.
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        assert [t["text"] for t in _open(app)["turns"]] == ["What number?", "Fresh."]


def test_a_restart_right_after_reset_restores_nothing(tmp_path, monkeypatch):
    """Reset closes the conversation in the database at once, not at the next
    exchange: a restart in between must not bring the old one back."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["Noted."])
        _ask(app, "Remember 41.", 1)
        n = len(_histories(app))
        app.panel.send(P.PANEL_ACTION_RESET)
        app.panel.wait_for(P.PANEL_EVENT_HISTORY, "reset never answered", nth=n + 1)
        assert not _is_open(_sidebar_chats(app)[0])

    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        replay = _open(app)
        assert replay["turns"] == [] and "notice" not in replay, replay
        assert len(_sidebar_chats(app)) == 1, "Reset removed the old chat from the list"


# --------------------------------------------------------------------------- #
# d. the chat deleted while the sidebar is open
# --------------------------------------------------------------------------- #


def test_the_chat_deleted_meanwhile_starts_a_new_one_and_the_panel_is_told(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = _recording_answers(app, ["Noted: 41.", "Starting over."])
        _ask(app, "Remember 41.", 1)
        old_id = _sidebar_chats(app)[0].id
        r = app.client.delete(f"/chat/threads/{old_id}", headers=_headers())
        assert r.status_code == 200, r.text

        n = len(_histories(app))
        _ask(app, "What number?", 2)
        notices = [h.get("notice") for h in _histories(app)[n:] if h.get("notice")]
        assert notices == [NOT_AVAILABLE], _histories(app)[n:]
        assert not [e for e, _ in app.panel.events() if e == P.PANEL_EVENT_ERROR]
        # The deleted conversation is not carried to the model any more.
        assert [r["role"] for r in _convo(seen[1])] == ["user"], seen[1]
        chats = _sidebar_chats(app)
        assert len(chats) == 1 and chats[0].id != old_id
        assert _messages(chats[0]) == [
            {"role": "user", "content": "What number?"},
            {"role": "assistant", "content": "Starting over."},
        ]


def test_a_chat_that_vanishes_during_the_write_is_minted_again_and_nothing_raises(tmp_path):
    """The race the check before a turn cannot close: deleted while the reply ran."""
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    chat = SidebarChat(CommThreadStore(engine), engine)
    first = chat.save_exchange("", "q1", "a1")
    from iron_jarvis.daemon.routes.chat import delete_thread

    assert delete_thread(engine, first)
    second = chat.save_exchange(first, "q2", "a2")
    assert second and second != first
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, second)
        assert row is not None and row.owner == "daemon"
        assert [m["content"] for m in json.loads(row.messages_json)] == ["q2", "a2"]


def test_an_unreadable_database_on_restore_is_said_and_the_panel_carries_on(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        turns = app.platform.browser.backend.panel_handler.__self__

        def broken(*a: Any, **kw: Any):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(turns._sidebar_chat(), "current", broken)
        replay = _open(app, shown=4)
        assert replay["turns"] == [] and replay.get("notice") == NOT_AVAILABLE
        _recording_answers(app, ["Fine."])
        _ask(app, "Carry on.", 1)
        assert not [e for e, _ in app.panel.events() if e == P.PANEL_EVENT_ERROR]


# --------------------------------------------------------------------------- #
# e. the write happens BEFORE the terminal frame leaves
# --------------------------------------------------------------------------- #


class _DbAtFrameConn:
    """A socket stand-in that records, with every frame, what the DATABASE held."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.frames: list[tuple[dict, list[str]]] = []

    async def send(self, frame: dict) -> None:
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(ChatThreadRecord).where(ChatThreadRecord.comm_channel == SIDEBAR_CHANNEL)
            ).all()
            stored = [m["content"] for r in rows for m in json.loads(r.messages_json or "[]")]
        self.frames.append((frame, stored))

    def at(self, event: str) -> list[str]:
        for frame, stored in self.frames:
            if frame.get("event") == event:
                return stored
        raise AssertionError(f"no {event} frame; sent: {[f.get('event') for f, _ in self.frames]}")


@pytest.mark.parametrize("exit_path", ["done", "stopped", "failed"])
def test_the_chat_is_written_before_the_terminal_frame_leaves(tmp_path, exit_path: str):
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    turns = PanelTurns(SimpleNamespace(browser=None, engine=engine, event_bus=None))
    conn = _DbAtFrameConn(engine)

    if exit_path == "done":
        async def drive():
            turns._turn_text, turns._remembered, turns._answer = "q?", False, []
            await turns._translate(conn, "token", {"text": "half "})
            await turns._translate(conn, "token", {"text": "answer"})
            await turns._translate(conn, "done", {"reply": "half answer"})

        asyncio.run(drive())
        assert conn.at("done") == ["q?", "half answer"]
        return

    async def stopped_lane(*a, **kw):
        async def gen():
            yield "event: token\ndata: {\"text\": \"half\"}\n\n"

        return gen()

    async def failing_lane(*a, **kw):
        async def gen():
            yield "event: token\ndata: {\"text\": \"half\"}\n\n"
            raise RuntimeError("provider fell over")

        return gen()

    import iron_jarvis.daemon.chat_stream as chat_stream

    original = chat_stream.stream_chat_turn
    chat_stream.stream_chat_turn = stopped_lane if exit_path == "stopped" else failing_lane
    try:
        async def drive():
            turns._conn = conn
            turns._turn_text, turns._remembered, turns._answer = "q?", False, []
            await turns._run(conn, "q?", "panel_test")

        asyncio.run(drive())
    finally:
        chat_stream.stream_chat_turn = original
    terminal = "done" if exit_path == "stopped" else "error"
    assert conn.at(terminal) == ["q?", "half"]


def test_a_panel_with_no_database_keeps_its_conversation_in_memory_as_before():
    """A stand-in platform with no engine (the unit tests' shape): nothing raises."""
    turns = PanelTurns(SimpleNamespace(browser=None))
    assert turns._sidebar_chat() is None


# --------------------------------------------------------------------------- #
# f. nothing treats the sidebar's chat as a phone chat
# --------------------------------------------------------------------------- #


def test_the_sidebar_chat_is_never_sent_anywhere(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["Noted."])
        _ask(app, "Remember 41.", 1)
        chat = _sidebar_chats(app)[0]
        store = app.app.state.comm_thread_store
        # No phone line is bound to it: the reply fan-out has nowhere to send.
        assert store.thread_channel(chat.id) is None
        sent: list[Any] = []
        notifier = app.platform.notifier
        for name in notifier.channels():
            ch = notifier.get(name)
            monkeypatch.setattr(ch, "send", lambda *a, **kw: sent.append(a), raising=False)
        # The Chat page's composer for a daemon-owned chat posts here: refused,
        # nothing appended, nothing sent.
        r = app.client.post(
            f"/comm/threads/{chat.id}/send", json={"text": "hello"}, headers=_headers()
        )
        assert r.status_code == 409, r.text
        assert sent == []
        assert len(_messages(_sidebar_chats(app)[0])) == 2
        # The destinations list is the phone's, never the sidebar's.
        names = [c.get("name") for c in app.client.get("/comm/channels", headers=_headers()).json()["channels"]]
        assert SIDEBAR_CHANNEL not in names


# --------------------------------------------------------------------------- #
# the add-on and the Chat page's banner
# --------------------------------------------------------------------------- #


def test_the_panel_says_how_many_messages_it_shows_and_paints_the_notice():
    ts = _src(PANEL_TS)
    assert "function shownMessages" in ts
    open_params = ts[ts.index("export function openParams"):]
    open_params = open_params[: open_params.index("\n}\n")]
    assert "shownMessages()" in open_params and "shown" in open_params
    paint = ts[ts.index("function paintHistory"):]
    paint = paint[: paint.index("\n}\n")]
    notice_at = paint.index('payload["notice"]')
    assert 'say("tool", notice)' in paint
    # The sentence is painted BEFORE the "transcript already holds it" return.
    assert notice_at < paint.index('querySelector(".turn, .work")')


def test_the_banner_does_not_promise_a_phone_for_the_sidebars_chat():
    tsx = _src(BANNER_TSX)
    assert f'"{SIDEBAR_CHANNEL}"' in tsx
    assert "from the browser sidebar" in tsx
