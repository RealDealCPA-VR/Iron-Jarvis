"""Review of lane P's @-round Stop (v1.420.0, C6.4).

The page used to abort its wait the moment Stop was pressed. When the round
had ALREADY answered — the daemon was appending its replies to the chat — the
stop found no running round (404), the replies landed in the saved chat, and
the page wrote "Stopped." beside them: two contradicting lines, and the reply
never shown in the open window. The page now lets the answer land when the
daemon's stop says the round already ended (``lib/panelWords.panelRoundEnded``,
keyed on the daemon's own 404 words — pinned equal here).

This file proves the daemon half of that contract: a stop that arrives while a
round's replies are being appended is a 404, the round answers normally (not
``stopped``), the chat holds the reply exactly once, and the chat stays
writable after (the append ran to its end; nothing held a lock).
"""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def test_the_ended_round_words_are_the_pages_constant(app):
    ts = (ROOT / "dashboard" / "lib" / "panelWords.ts").read_text(encoding="utf-8")
    m = re.search(r'PANEL_ROUND_ENDED_DETAIL = "([^"]+)"', ts)
    assert m, "the page's constant is gone"
    client_detail = m.group(1)
    r = TestClient(app).post("/chat/panel/pr_nope/stop")
    assert r.status_code == 404 and r.json()["detail"] == client_detail


def test_a_stop_during_the_append_is_a_404_and_the_reply_lands_once(app, monkeypatch):
    async def answer(self, *a, **kw):
        return "Here is the brief."

    monkeypatch.setattr(AgentThreads, "_speak_local", answer)
    appending = threading.Event()
    release = threading.Event()
    real = agents_routes.append_to_chat_thread

    def slow_append(*a, **kw):
        appending.set()
        release.wait(10)
        return real(*a, **kw)

    monkeypatch.setattr(agents_routes, "append_to_chat_thread", slow_append)
    client = TestClient(app)
    made = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "@builder hi"}]})
    tid = made.json()["id"]

    async def drive():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
            round_req = asyncio.create_task(
                ac.post(
                    "/chat/panel",
                    json={"message": "@builder hi", "thread_id": tid, "round_id": "pr_late1"},
                    timeout=30,
                )
            )
            await asyncio.wait_for(asyncio.to_thread(appending.wait, 10), 15)
            stop = await ac.post("/chat/panel/pr_late1/stop")
            release.set()
            res = await asyncio.wait_for(round_req, 15)
            return stop, res

    stop, res = asyncio.run(drive())
    assert stop.status_code == 404, stop.text
    assert res.status_code == 200, res.text
    body = res.json()
    assert not body.get("stopped")
    assert body.get("appended") is True
    msgs = client.get(f"/chat/threads/{tid}").json()["messages"]
    contents = [m["content"] for m in msgs]
    assert contents.count("Here is the brief.") == 1, contents
    assert "Stopped." not in contents
    # the chat stays writable: the append ran to its end
    monkeypatch.setattr(agents_routes, "append_to_chat_thread", real)
    again = client.post(
        "/chat/panel", json={"message": "@builder more", "thread_id": tid, "round_id": "pr_late2"}
    )
    assert again.status_code == 200 and again.json().get("appended") is True
