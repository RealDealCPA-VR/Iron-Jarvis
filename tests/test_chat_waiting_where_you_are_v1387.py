"""Readiness 12B (G05, the daemon half): a chat waiting for your OK is seen
WHERE YOU ARE — on another page of the same window too.

THE GAP (re-verified on v1.386.2): the chat page never ends its stream when the
user moves to another page of the same window (Next keeps the fetch reading,
so the reply still lands and is saved). The daemon therefore read the turn as
FOLLOWED, ``waiting_unwatched`` skipped it, ``GET /chat/approvals/pending``
listed nothing, and the bell — on the page the user was looking at — said
nothing; only the sidebar's dot did. The route's own docstring admitted it
("moving to another page in the same window … still reads as followed").

THE FIX: the chat page names itself (the ``X-IJ-Viewer`` header on its
stream) and,
when it leaves the screen, says so (``POST /chat/turns/{id}/away``). The turn
keeps running and keeps its follower (the orphan guard is unchanged); the
listing reads ``TurnLog.looked_at()`` — a window SHOWING the turn — instead of
"someone follows it". A second window that shows the same turn keeps it off
the list (one notice: its card is in front of the user).

Driven through the real app factory and the real chat stream lane (only the
model is scripted: it asks for one shell call, which cards).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from tests.test_chat_approvals_v1187 import _ASK_MSG, _asgi_post, _shell_call_stream
from tests.test_durable_chat_turns_v1336 import _get, _until

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "chat-away-v1387.json"
REGEN = os.environ.get("IJ_REGEN_CHAT_AWAY") == "1"
TURN = "turn-12b"
THREAD = "th-12b"


#: The header a chat page names itself with (``routes/chat.VIEWER_HEADER``;
#: the dashboard's ``lib/chatAway.VIEWER_HEADER``).
VIEWER_HEADER = "X-IJ-Viewer"


def _scope(method: str, path: str, viewer: str, body: bytes) -> dict:
    headers = [
        (b"host", b"127.0.0.1:8787"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if viewer:
        headers.append((VIEWER_HEADER.lower().encode(), viewer.encode()))
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": headers,
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


class _Follower:
    """One window following a turn over the real endpoint (the POST that
    started it, or a GET rejoin), dropped on demand."""

    def __init__(self, app, method: str, path: str, viewer: str = "", body: dict | None = None):
        self.app, self.method, self.path, self.viewer = app, method, path, viewer
        self.raw = json.dumps(body).encode() if body is not None else b""
        self.drop = asyncio.Event()
        self.got: list[str] = []
        self.task: asyncio.Task | None = None

    async def open(self) -> "_Follower":
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": self.raw, "more_body": False}
            await self.drop.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                self.got.append(msg.get("body", b"").decode("utf-8", "replace"))

        self.task = asyncio.create_task(
            self.app(_scope(self.method, self.path, self.viewer, self.raw), receive, send)
        )
        return self

    def approval(self) -> dict:
        for block in "".join(self.got).split("\n\n"):
            lines = block.strip().splitlines()
            if lines and lines[0] == "event: approval":
                return json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
        return {}

    async def close(self) -> None:
        self.drop.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=20)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass


async def _pending_chats(app) -> list[dict]:
    status, text = await _get(app, "/chat/approvals/pending")
    assert status == 200, text
    return [r for r in json.loads(text)["approvals"] if r.get("kind") == "chat"]


async def _parked(app, viewer: str) -> tuple[_Follower, dt.TurnLog, dict]:
    """A chat page (named ``viewer``) started a turn that is parked on its
    approval card, the card on screen."""
    app.state.platform.router.stream = _shell_call_stream("echo approved-run")
    win = await _Follower(app, "POST", "/chat/stream", viewer, {
        "messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
        "turn_id": TURN, "thread_id": THREAD,
    }).open()
    card = await _until(win.approval, seconds=20)
    assert card and card["tool"] == "shell", "".join(win.got)[-1500:]
    lg = app.state.platform.turn_logs.get(TURN)
    assert lg is not None and lg.watched()
    return win, lg, card


async def _cleanup(app, *wins: _Follower) -> None:
    for w in wins:
        await w.close()
    lg = app.state.platform.turn_logs.get(TURN)
    if lg is not None and lg.task is not None and not lg.task.done():
        lg.task.cancel()


def _hold(real: dict) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data.update(real)
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    for k, v in real.items():
        assert data.get(k) == v, (
            f"the daemon no longer answers what {FIXTURE.name}[{k!r}] says — regenerate it with "
            "IJ_REGEN_CHAT_AWAY=1 if intended, and update the dashboard half"
        )


# --------------------------------------------------------------------------- #
# The acceptance.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_chat_page_left_for_another_page_is_listed_while_it_still_follows(tmp_path):
    app = create_app(str(tmp_path))
    win, lg, card = await _parked(app, "view-A")
    try:
        # On the chat page, the card IS the ask: nothing is listed.
        assert await _pending_chats(app) == []

        # The user goes to another page of the same window: the page's fetch
        # keeps reading (the reply will still land), and it says it left.
        status, raw = await _asgi_post(app, f"/chat/turns/{TURN}/away", {"viewer": "view-A"})
        assert status == 200, raw
        assert json.loads(raw) == {"away": True}

        rows = await _pending_chats(app)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["id"] == f"chat:{THREAD}" and row["thread_id"] == THREAD and row["tool"] == "shell"
        assert "args" not in row  # names only, as the listing always was
        # The page still FOLLOWS: the turn is not an orphan, and still runs.
        assert lg.watched() and lg.unwatched_for() == 0.0 and lg.status == "running"
        _hold({
            "stream_header": VIEWER_HEADER,
            "away_path": "/chat/turns/{turn_id}/away",
            "away_body": {"viewer": "view-A"},
            "away_answer": json.loads(raw),
            "row": {**row, "requested_at": "2026-10-25T09:00:00Z"},
        })

        # Answered in the chat (once reopened) -> it leaves the list.
        status, _ = await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"})
        assert status == 200
        assert await _until_async(lambda: _pending_chats(app), lambda rows: rows == [])
    finally:
        await _cleanup(app, win)


async def _until_async(read, ok, tries: int = 200) -> bool:
    for _ in range(tries):
        if ok(await read()):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_window_still_showing_the_turn_keeps_it_off_the_list(tmp_path):
    """One notice: a second window (a pop-out, a reopened chat page) that
    SHOWS the turn has the card in front of the user — the bell stays quiet.
    When it goes too, the row comes back."""
    app = create_app(str(tmp_path))
    win, lg, _card = await _parked(app, "view-A")
    other = await _Follower(app, "GET", f"/chat/turns/{TURN}/stream", "view-B").open()
    try:
        assert await _until(lambda: len(lg._followers) == 2)
        status, _ = await _asgi_post(app, f"/chat/turns/{TURN}/away", {"viewer": "view-A"})
        assert status == 200
        assert await _pending_chats(app) == [], "a window still shows the card"

        await other.close()  # the second window goes too
        assert await _until(lambda: len(lg._followers) == 1)
        rows = await _pending_chats(app)
        assert [r["id"] for r in rows] == [f"chat:{THREAD}"], rows
    finally:
        await _cleanup(app, win, other)


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_page_that_rejoined_and_left_too_is_listed(tmp_path):
    """The chat page that REJOINED the turn (a reopened chat: ``GET
    /chat/turns/{id}/stream``) names itself the same way, so when it leaves
    the screen too, nobody is showing the card and the row comes back."""
    app = create_app(str(tmp_path))
    win, lg, _card = await _parked(app, "view-A")
    other = await _Follower(app, "GET", f"/chat/turns/{TURN}/stream", "view-B").open()
    try:
        assert await _until(lambda: len(lg._followers) == 2)
        for v in ("view-A", "view-B"):
            status, raw = await _asgi_post(app, f"/chat/turns/{TURN}/away", {"viewer": v})
            assert status == 200 and json.loads(raw) == {"away": True}, (v, raw)
        rows = await _pending_chats(app)
        assert [r["id"] for r in rows] == [f"chat:{THREAD}"], rows
        assert lg.watched(), "both pages still follow: the reply still lands"
    finally:
        await _cleanup(app, win, other)


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_follower_that_named_no_viewer_still_counts_as_looking(tmp_path):
    """Every other surface that follows a chat turn (an older page, the Build
    pane chat, the sidebar) names no viewer and keeps the old rule."""
    app = create_app(str(tmp_path))
    win, lg, _card = await _parked(app, "view-A")
    plain = await _Follower(app, "GET", f"/chat/turns/{TURN}/stream").open()
    try:
        assert await _until(lambda: len(lg._followers) == 2)
        await _asgi_post(app, f"/chat/turns/{TURN}/away", {"viewer": "view-A"})
        assert await _pending_chats(app) == []
    finally:
        await _cleanup(app, win, plain)


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_away_is_said_only_by_the_page_that_follows(tmp_path):
    app = create_app(str(tmp_path))
    win, _lg, _card = await _parked(app, "view-A")
    try:
        # A viewer that is not following this turn changes nothing.
        status, raw = await _asgi_post(app, f"/chat/turns/{TURN}/away", {"viewer": "view-Z"})
        assert status == 200 and json.loads(raw) == {"away": False}
        assert await _pending_chats(app) == []
        # No viewer: refused. An unknown turn: 404 (never a silent success).
        assert (await _asgi_post(app, f"/chat/turns/{TURN}/away", {}))[0] == 400
        assert (await _asgi_post(app, "/chat/turns/nope/away", {"viewer": "view-A"}))[0] == 404
    finally:
        await _cleanup(app, win)


# --------------------------------------------------------------------------- #
# The bookkeeping, on the real TurnLog.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_turnlog_forgets_a_viewer_when_its_follower_ends():
    lg = dt.TurnLog("t", "th")
    gen = lg.follow(viewer="v1")
    first = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    assert lg.looked_at() and lg.watched()
    assert lg.mark_away("v1") is True
    assert not lg.looked_at() and lg.watched()
    assert lg.mark_away("") is False and lg.mark_away("v9") is False
    first.cancel()
    try:
        await first
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await gen.aclose()
    assert not lg.watched() and not lg._viewers and not lg._away


def test_the_header_is_one_name_on_both_sides():
    from iron_jarvis.daemon.routes import chat as chat_routes

    assert chat_routes.VIEWER_HEADER == VIEWER_HEADER.lower()
    src = (ROOT / "dashboard" / "lib" / "chatAway.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert f'export const VIEWER_HEADER = "{VIEWER_HEADER}";' in src


def test_a_double_without_the_reader_keeps_the_old_rule():
    class Old:
        thread_id, turn_id, started_at, surface = "th", "t", None, ""

        def watched(self):
            return False

        def open_asks(self, pending):
            return [("ap", "shell", "2026-10-25T09:00:00Z")]

    class Logs:
        def running(self):
            return [Old()]

    rows = dt.waiting_unwatched(Logs(), {}, ["ap"])
    assert [r["id"] for r in rows] == ["chat:th"]
