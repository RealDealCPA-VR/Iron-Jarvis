"""Harness gaps, fix 1: a Build coding session's reply that is RUNNING is seen
outside Build.

The v1.388.0 re-audit (0g, lanes A-B and F-K): an IJ harness reply that ran
for an hour left Running now saying "Nothing is running right now.", the
status orb idle and the home line counting nothing — a Build turn has no
Session row, and ``GET /chat/threads`` leaves Build sessions out on purpose —
while "Install when idle" (``CHAT_INFLIGHT``) already knew it ran.

ONE truth now: the durable turn registry (``durable_turns.running_replies`` /
``reply_counts``), read in memory. These pins drive a REAL Build turn through
the real app factory (a scripted model held mid-turn) and read every surface
that says what runs:

* ``GET /chat/turns/running`` lists it (surface "build", its session's
  title), and ``waiting`` while it is parked on a card;
* ``/health``'s ``replies`` counts it (the orb reads that);
* ``GET /ui/status-line`` (the home line) and the system map's status
  ("Working on N tasks") count it; the Chats star no longer counts a Build
  reply as a chat, the Build star does;
* when the turn ends, all of it goes back to nothing.

And the cost: the running-replies read never loads a message body.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon import durable_turns as D
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi_get,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def _held_turn(app, sid: str, turn_id: str, text: str = "Add a cart page"):
    """Start a Build turn whose model waits on ``gate``; returns (gate, task)."""
    gate = asyncio.Event()

    async def hold(_script):
        await gate.wait()
        return "Added the cart page."

    script = wire(app, Script([hold]))
    task = asyncio.create_task(open_turn(app, sid, {"text": text, "turn_id": turn_id}))
    await until(lambda: turn_log(app, turn_id) is not None and script.calls, 30)
    return gate, task


def _map_node(body: dict, key: str) -> dict:
    for area in body.get("areas") or []:
        for star in area.get("nodes") or []:
            if star.get("key") == key:
                return star
    raise AssertionError(f"no {key} star")


async def test_a_running_build_reply_is_listed_counted_and_named_outside_build(app, work):
    sid = new_session(app, work, title="Add a cart")["id"]
    gate, task = await _held_turn(app, sid, "hg-run")

    status, body = await asgi_get(app, "/chat/turns/running")
    assert status == 200, body
    rows = [r for r in body["replies"] if r["turn_id"] == "hg-run"]
    assert len(rows) == 1, body
    row = rows[0]
    assert row["surface"] == "build" and row["thread_id"] == sid
    assert row["title"] == "Add a cart", "named by its session's title"
    assert row["waiting"] is False
    assert row["started_at"].endswith("Z"), "UTC, said so"

    # The orb's count rides the one shared /health poll.
    status, health = await asgi_get(app, "/health")
    # v1.414.0 (moved on purpose): /health also counts Build › Code replies.
    assert status == 200 and health["replies"] == {"chat": 0, "build": 1, "build_app": 0}, health.get("replies")

    # The home line and Everything › Status count it.
    status, line = await asgi_get(app, "/ui/status-line")
    assert status == 200 and line["running"] == 1, line
    status, smap = await asgi_get(app, "/ui/system-map")
    assert status == 200 and smap["status"]["running"] == 1, smap.get("status")
    # A Build session is not a chat (v1.367.0): its reply lights the Build
    # star, never the Chats star.
    assert _map_node(smap, "chats")["live"] == 0
    assert _map_node(smap, "build")["live"] == 1
    assert "1 coding session is answering now." in _map_node(smap, "build")["detail"]

    gate.set()
    await task
    lg = turn_log(app, "hg-run")
    await until(lambda: lg.status != "running", 30)

    status, body = await asgi_get(app, "/chat/turns/running")
    assert [r for r in body["replies"] if r["turn_id"] == "hg-run"] == []
    status, health = await asgi_get(app, "/health")
    assert health["replies"] == {"chat": 0, "build": 0, "build_app": 0}
    status, line = await asgi_get(app, "/ui/status-line")
    assert line["running"] == 0


async def test_a_build_reply_parked_on_a_card_reads_waiting(app, work):
    sid = new_session(app, work, mode="plan", title="Plan the cart")["id"]
    wire(app, Script([("Plan: 1. add it", [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Ready."]))
    task = asyncio.create_task(open_turn(app, sid, {"text": "plan it", "turn_id": "hg-wait"}))
    card = (await until(lambda: [d for e, d in turn_frames(app, "hg-wait") if e == "approval"], 30))[0]

    status, body = await asgi_get(app, "/chat/turns/running")
    [row] = [r for r in body["replies"] if r["turn_id"] == "hg-wait"]
    assert row["surface"] == "build" and row["waiting"] is True, row

    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    lg = turn_log(app, "hg-wait")
    await until(lambda: lg.status != "running", 30)
    task.cancel()
    status, body = await asgi_get(app, "/chat/turns/running")
    assert [r for r in body["replies"] if r["turn_id"] == "hg-wait"] == []


def test_the_registry_helpers_read_memory_only_and_never_raise():
    """``running_replies`` / ``reply_counts`` read the in-memory logs; a
    chat's reply is a chat, a Build turn's is "build"; no logs reads empty."""
    assert D.running_replies(None) == []
    assert D.reply_counts(None) == {"chat": 0, "build": 0}

    class _Log:
        def __init__(self, tid, thread, surface, asks=()):
            self.turn_id, self.thread_id, self.surface = tid, thread, surface
            self.started_at = D.utcnow()
            self._asked = set(asks)

        def open_asks(self, pending):
            return [(a, "write_file", "") for a in self._asked if a in pending]

    class _Logs:
        def __init__(self, logs):
            self._l = logs

        def running(self):
            return list(self._l)

    logs = _Logs([_Log("a", "t-chat", ""), _Log("b", "t-build", "build"), _Log("c", "", "", asks=["ap1"])])
    rows = D.running_replies(logs, {"t-build": {"running": True, "waiting": True}}, ["ap1"])
    by = {r["turn_id"]: r for r in rows}
    assert by["a"]["surface"] == "chat" and by["a"]["waiting"] is False
    assert by["b"]["surface"] == "build" and by["b"]["waiting"] is True
    assert by["c"]["waiting"] is True, "an approval card still in the registry is waiting"
    assert D.reply_counts(logs) == {"chat": 2, "build": 1}

    class _Broken:
        def running(self):
            raise RuntimeError("boom")

    assert D.running_replies(_Broken()) == []
    assert D.reply_counts(_Broken()) == {"chat": 0, "build": 0}


async def test_the_titles_read_never_loads_a_message_body(app, work, monkeypatch):
    """The cost rule: a poll of running replies reads the title COLUMN only
    — ``messages_json`` (a long session's whole transcript) is never loaded."""
    sid = new_session(app, work, title="Add a cart")["id"]
    gate, task = await _held_turn(app, sid, "hg-cost")
    seen: list[str] = []
    from sqlalchemy import event

    engine = app.state.platform.engine

    def _record(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert D.reply_titles(engine, [sid]) == {sid: "Add a cart"}
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    gate.set()
    await task
    assert seen and all("messages_json" not in s for s in seen), seen
