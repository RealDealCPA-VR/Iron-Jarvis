"""Two places where Local only was narrower in words than in code.

1. ``recall`` (the tool) searched EVERY memory base in a Local only chat, a
   remote one included: the query is the chat's own words, and a remote base
   (a cloud drive, Notion, an HTTP retrieval service) is sent them. The
   reply's own grounding and ``ltm_search`` have searched only the bases on
   this PC since v1.332.0 (``egress.local_ltm``); this tool did not.

2. The page links a new chat's first reply to the chat it saved, and the
   apps that reply read are then held Local only on the chat. The pending
   holds are handed over once; with one awaited write per app, a request
   cancelled between two writes left the later holds applied nowhere.

Both are driven through the real tool and the real route handler. Nothing
sleeps: the stand-ins block on events.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.recall import RecallTool
from iron_jarvis.providers import egress
from iron_jarvis.tools.base import ToolContext


class _RemoteBase:
    """A memory base that is NOT on this PC (an LTMConnector by duck type)."""

    name = "notion-x"

    def __init__(self):
        self.queries: list[str] = []

    def search(self, query, k=5):
        self.queries.append(query)
        return [{"title": "remote note", "snippet": "budget plan details", "ref": "n1", "source": self.name}]

    def append(self, title, content):  # pragma: no cover - never written
        return "n"


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _ctx(platform, tmp_path) -> ToolContext:
    ws = Path(tmp_path) / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )


def _recall(platform, tmp_path, *, local_only: bool, allowed_once: str = ""):
    tool = platform.registry.get("recall")
    assert isinstance(tool, RecallTool), "the registered recall tool is the one under test"

    async def run():
        with egress.local_only_scope(local_only, allowed_once):
            return await tool.execute({"query": "budget plan", "sources": ["notes"]}, _ctx(platform, tmp_path))

    return asyncio.run(run())


def test_recall_in_a_local_only_chat_never_asks_a_remote_memory_base(app, tmp_path):
    platform = app.state.platform
    remote = _RemoteBase()
    platform.ltm.register(remote)

    result = _recall(platform, tmp_path, local_only=True)

    assert remote.queries == [], "a Local only chat's words were sent to a memory base off this PC"
    assert result.ok
    assert "remote note" not in (result.output or "")
    assert all(h.get("ref") != "n1" for h in (result.data or {}).get("results", []))


def test_recall_outside_local_only_still_searches_every_base(app, tmp_path):
    """Anti-vacuity: the same call, not Local only, reaches the remote base."""
    platform = app.state.platform
    remote = _RemoteBase()
    platform.ltm.register(remote)

    result = _recall(platform, tmp_path, local_only=False)

    # (The base is asked the whole query first, then its words one by one.)
    assert remote.queries and remote.queries[0] == "budget plan"
    assert result.ok and "remote note" in (result.output or "")


def test_a_cloud_model_allowed_for_one_message_does_not_open_remote_bases_to_recall(app, tmp_path):
    """Allowing one cloud MODEL for a message is not sending the chat's words
    to a memory base: the scope is still on."""
    platform = app.state.platform
    remote = _RemoteBase()
    platform.ltm.register(remote)

    _recall(platform, tmp_path, local_only=True, allowed_once="anthropic")

    assert remote.queries == []


# --------------------------------------------------------------------------- #
# The chat link: every hold the first reply took is written, cancelled or not.
# --------------------------------------------------------------------------- #
def _holds_of(engine, thread_id: str) -> list[str]:
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, thread_id)
        setup = json.loads(row.setup_json or "{}") if row is not None else {}
    return egress.clean_holds(setup.get(egress.HOLDS_KEY))


def test_a_link_cancelled_between_two_holds_still_writes_every_hold(app, monkeypatch):
    engine = app.state.platform.engine
    client = TestClient(app)
    tid = client.put("/chat/threads/new", json={"title": "the chat", "messages": []}).json()["id"]
    turn = "turn-link-holds"
    assert egress.note_pending_hold(turn, "ms365") == "" and egress.note_pending_hold(turn, "gmail") == ""
    assert _holds_of(engine, tid) == []
    link = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/chat/turns/{turn_id}/thread"
                and "POST" in getattr(r, "methods", ()))

    real = egress.hold_local_only
    first_done, release, all_done = threading.Event(), threading.Event(), threading.Event()
    written: list[str] = []

    def slow_hold(engine_, thread_id, app_id):
        ok = real(engine_, thread_id, app_id)
        written.append(app_id)
        if len(written) == 1:
            first_done.set()
            release.wait(60)  # the request is cancelled while the first write is the only one made
        else:
            all_done.set()
        return ok

    monkeypatch.setattr(egress, "hold_local_only", slow_hold)

    async def main() -> None:
        task = asyncio.ensure_future(link(turn, {"thread_id": tid}))
        try:
            assert await asyncio.to_thread(first_done.wait, 60), "the link never wrote its first hold"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        assert await asyncio.to_thread(all_done.wait, 60), "the second hold was never written"

    asyncio.run(main())
    assert written == ["gmail", "ms365"]
    assert _holds_of(engine, tid) == ["gmail", "ms365"], "a cancelled link left a hold applied nowhere"
    assert egress.pending_holds_of(turn) == []


def test_a_link_cancelled_before_the_write_has_started_still_writes_every_hold(app, monkeypatch):
    """The holds are handed over (taken off the waiting list) and the request
    is cancelled while their one write is still QUEUED behind other work: a
    write that had not started yet must not be dropped with the request."""
    from concurrent.futures import ThreadPoolExecutor

    engine = app.state.platform.engine
    client = TestClient(app)
    tid = client.put("/chat/threads/new", json={"title": "the chat", "messages": []}).json()["id"]
    turn = "turn-link-queued"
    egress.note_pending_hold(turn, "ms365")
    egress.note_pending_hold(turn, "gmail")
    link = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/chat/turns/{turn_id}/thread"
                and "POST" in getattr(r, "methods", ()))
    real_take = egress.link_turn_holds
    busy = threading.Event()  # holds the one worker thread until the test lets go

    async def main() -> None:
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        handed = asyncio.Event()

        def take(turn_id, thread_id):
            apps = real_take(turn_id, thread_id)
            # From here the only worker is busy, so the write the route asks
            # for next waits in the queue.
            loop.run_in_executor(None, busy.wait, 60)
            handed.set()
            return apps

        monkeypatch.setattr(egress, "link_turn_holds", take)
        task = asyncio.ensure_future(link(turn, {"thread_id": tid}))
        try:
            await asyncio.wait_for(handed.wait(), 60)  # the route is now at its await of the write
            assert egress.pending_holds_of(turn) == [] and _holds_of(engine, tid) == []
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            busy.set()
        await loop.run_in_executor(None, lambda: None)  # behind the write in the one-thread queue

    asyncio.run(main())
    assert _holds_of(engine, tid) == ["gmail", "ms365"], "a queued write was dropped with the cancelled request"


def test_a_link_that_is_not_cancelled_writes_every_hold_before_it_answers(app):
    engine = app.state.platform.engine
    client = TestClient(app)
    tid = client.put("/chat/threads/new", json={"title": "the chat", "messages": []}).json()["id"]
    turn = "turn-link-plain"
    egress.note_pending_hold(turn, "ms365")
    egress.note_pending_hold(turn, "gmail")

    res = client.post(f"/chat/turns/{turn}/thread", json={"thread_id": tid})

    assert res.status_code == 200
    assert _holds_of(engine, tid) == ["gmail", "ms365"]
