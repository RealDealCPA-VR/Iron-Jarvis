"""An @-agent round says WHY a seat failed, in plain words, and Stop ends it
(v1.420.0, C6.4).

Before: a seat that raised was stored as "<name> couldn't answer: <raw
text>", the chat line read "(no reply)" beside a red name, and nothing could
end a round that hung — the page's Stop did nothing while an @-round ran.

Now: ``agents.threads.seat_error_words`` (the shared failure words, said for a
seat) rides each failed entry as ``error_words`` and is the chat line's
content; ``POST /chat/panel`` takes the page's ``round_id`` and
``POST /chat/panel/{round_id}/stop`` ends that round (``stopped: true``,
nothing appended to the chat). Driven through the real app.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import threads as threads_mod
from iron_jarvis.agents.threads import AgentThreads, seat_error_words
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes
from iron_jarvis.providers.adapters.base import ProviderError

SECRET = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #
def test_a_busy_provider_reads_as_the_shared_words_said_for_a_seat():
    exc = ProviderError(f"claude-cli: upstream overloaded key={SECRET}", status_code=503)
    words = seat_error_words(exc)
    assert words.startswith("Claude Code is busy.")
    # said for a seat: no Retry press, the agent's own card
    assert "Press Retry" not in words and "this chat" not in words
    assert "on its card in Agents" in words
    # never the raw text, never a secret
    assert "overloaded" not in words and SECRET not in words and "503" not in words


def test_an_unknown_failure_still_reads_plainly_and_never_empty():
    words = seat_error_words(RuntimeError("Traceback: KeyError 'x' at line 4"))
    assert words and "KeyError" not in words and "Traceback" not in words
    assert "Ask it again" in words


def test_seat_words_never_raise():
    class Hostile(Exception):
        def __str__(self):  # noqa: D401
            raise RuntimeError("no")

    assert seat_error_words(Hostile())


# --------------------------------------------------------------------------- #
# Through the route
# --------------------------------------------------------------------------- #
def test_a_failed_seat_answers_its_words_and_the_chat_line_carries_them(app, monkeypatch):
    async def boom(self, *a, **kw):
        raise ProviderError(f"codex-cli: HTTP 429 {SECRET}", status_code=429)

    monkeypatch.setattr(AgentThreads, "_speak_local", boom)
    client = TestClient(app)
    made = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "@builder hi"}]})
    tid = made.json()["id"]
    r = client.post(
        "/chat/panel",
        json={"message": "@builder hi", "chat_thread_id": tid, "thread_id": tid, "round_id": "pr_a1"},
    )
    assert r.status_code == 200, r.text
    entry = [e for e in r.json()["entries"] if e.get("who") != "user"][0]
    assert entry["error"]  # the raw record is kept (the Agents page's room)
    assert entry["error_words"].startswith("Codex is busy.")
    assert SECRET not in entry["error_words"]
    line = client.get(f"/chat/threads/{tid}").json()["messages"][-1]
    assert line["panelError"] is True
    assert line["content"] == entry["error_words"]
    assert line["content"] != "(no reply)"


def test_the_line_keeps_no_reply_when_a_failure_has_no_words():
    """An older store's entry (no ``error_words``) keeps the v1.345.0 line."""
    lines = agents_routes.panel_reply_lines(
        {"entries": [{"who": "builtin:a", "content": "", "error": "a couldn't answer: boom"}]}, "r"
    )
    assert lines[0]["content"] == "(no reply)" and lines[0]["panelError"] is True


def test_round_ids_are_plain_tokens_only():
    assert agents_routes.panel_round_id("pr_ab12.c:d-e") == "pr_ab12.c:d-e"
    assert agents_routes.panel_round_id("../x") == ""
    assert agents_routes.panel_round_id("a b") == ""
    assert agents_routes.panel_round_id(None) == ""


def test_stop_on_an_unknown_round_is_a_404(app):
    client = TestClient(app)
    assert client.post("/chat/panel/pr_nope/stop").status_code == 404


def test_stop_ends_a_running_round_and_nothing_is_appended(app, monkeypatch):
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def hang(self, *a, **kw):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "never"

    monkeypatch.setattr(AgentThreads, "_speak_local", hang)
    client = TestClient(app)  # the saved chat, made the ordinary way
    made = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "@builder hi"}]})
    tid = made.json()["id"]

    async def drive():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
            round_req = asyncio.create_task(
                ac.post(
                    "/chat/panel",
                    json={"message": "@builder hi", "thread_id": tid, "round_id": "pr_stop1"},
                    timeout=30,
                )
            )
            await asyncio.wait_for(started.wait(), 10)
            stop = await ac.post("/chat/panel/pr_stop1/stop")
            res = await asyncio.wait_for(round_req, 10)
            again = await ac.post("/chat/panel/pr_stop1/stop")
            return stop, res, again

    stop, res, again = asyncio.run(drive())
    assert stop.status_code == 200 and stop.json()["stopped"] == "pr_stop1"
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stopped"] is True and body["entries"] == []
    assert body["mode"] == "panel"
    assert cancelled == [True]  # the seat's work really ended
    assert again.status_code == 404  # the round is gone from the map
    assert "pr_stop1" not in agents_routes._PANEL_ROUNDS
    msgs = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert len(msgs) == 1  # the question only: a Stop appends nothing


def test_threads_module_exposes_the_one_words_function():
    """The words live in ONE place: the route's line reads the entry's
    ``error_words`` (set by ``run_round``), never a second classifier."""
    import inspect

    src = inspect.getsource(threads_mod.AgentThreads.run_round)
    assert 'entry["error_words"] = seat_error_words(' in src
