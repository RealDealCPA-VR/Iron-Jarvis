"""One run per hand-off (v1.529.0, owner decision 9 after the third fix plan).

Since group 18 (v1.521.0) every window following a chat reply can act on its
end. When that reply hands the request to an agent, EACH window posted its
own session create, so two pages open on one chat started the agent run
twice: two runs' cost, possibly two sets of changes.

Now the hand-off names the chat turn that handed off (``handed_from_turn``,
the reply's name every hand-off line already carries). The daemon starts at
most ONE run per name: a second request gets the first run back (same id, its
status), never an error and never a second run, so both windows follow the
same run. The name is stored on the session row, so the guard holds across a
restart, and it is taken under a per-name lock, so it holds when the two
requests arrive at the same moment. A request with no name behaves as before.

"Started" is counted at the daemon's one launcher (``spawn_managed``), which
the test replaces with a recorder: no run actually executes here.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ContinueBody, SessionCreate, SpawnBody


TURN = "chat-mh2k1x-5f0c2a9e-1111-4c2b-9a77-0d3f5b6c7e81"


def _record_starts(app) -> list[str]:
    """Replace the launcher with a recorder: every run the daemon starts is
    one id in the list (the coroutine is closed, never run)."""
    started: list[str] = []

    def fake_spawn(session_id, coro):
        coro.close()
        started.append(session_id)
        return object()  # "launched", not parked

    app.state.orchestrator.spawn_managed = fake_spawn
    return started


def _body(turn: str | None = TURN, task: str = "rename the files in my folder") -> dict:
    body = {"task": task, "wait": False, "origin": "chat"}
    if turn is not None:
        body["handed_from_turn"] = turn
    return body


def _rows(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        return list(db.exec(select(Session)).all())


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def test_two_hand_offs_naming_one_turn_start_one_run(app):
    started = _record_starts(app)
    client = TestClient(app)
    first = client.post("/sessions", json=_body())
    second = client.post("/sessions", json=_body())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # The second window gets the FIRST run back, not a run of its own.
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["status"] == first.json()["status"]
    assert started == [first.json()["id"]]
    assert len(_rows(app)) == 1


def test_two_hand_offs_at_the_same_moment_start_one_run(app):
    """Two creates in flight together: the first is held inside the create
    until the second has had every chance to reach it too."""
    started = _record_starts(app)
    orch = app.state.orchestrator
    real_create = orch.create_session
    entered = 0

    async def held_create(*args, **kwargs):
        nonlocal entered
        entered += 1
        # Yield (no clock) until the other request is in here as well, or
        # it plainly cannot be (it waits on the guard).
        for _ in range(500):
            if entered >= 2:
                break
            await asyncio.sleep(0)
        return await real_create(*args, **kwargs)

    orch.create_session = held_create

    async def both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await asyncio.gather(
                c.post("/sessions", json=_body()), c.post("/sessions", json=_body())
            )

    a, b = asyncio.run(both())
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["id"] == b.json()["id"]
    assert started == [a.json()["id"]]
    assert len(_rows(app)) == 1


def test_the_guard_holds_across_a_restart(tmp_path):
    first_app = create_app(str(tmp_path))
    _record_starts(first_app)
    first = TestClient(first_app).post("/sessions", json=_body())
    assert first.status_code == 200, first.text

    # A new daemon on the same home: nothing in memory, only what is stored.
    again = create_app(str(tmp_path))
    started_again = _record_starts(again)
    second = TestClient(again).post("/sessions", json=_body())
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert started_again == []
    assert len(_rows(again)) == 1


def test_no_turn_name_behaves_as_before(app):
    started = _record_starts(app)
    client = TestClient(app)
    a = client.post("/sessions", json=_body(turn=None))
    b = client.post("/sessions", json=_body(turn=None))
    blank = client.post("/sessions", json=_body(turn="   "))
    ids = {a.json()["id"], b.json()["id"], blank.json()["id"]}
    assert len(ids) == 3
    assert started == [a.json()["id"], b.json()["id"], blank.json()["id"]]
    # Nothing is stamped on a run no turn handed off.
    assert all(not (r.handed_from_turn or "") for r in _rows(app))


def test_two_different_turns_start_their_own_runs(app):
    started = _record_starts(app)
    client = TestClient(app)
    a = client.post("/sessions", json=_body(turn=TURN))
    b = client.post("/sessions", json=_body(turn=TURN + "-next"))
    assert a.json()["id"] != b.json()["id"]
    assert len(started) == 2


def test_a_continue_naming_one_turn_starts_one_follow_up(app):
    """The page continues the chat's earlier run when it has one: two windows
    then both post a continue for the same hand-off."""
    started = _record_starts(app)
    client = TestClient(app)
    opener = client.post("/sessions", json=_body(turn=None))
    assert opener.status_code == 200, opener.text
    parent = opener.json()["id"]
    started.clear()
    # The earlier run has finished (the recorder never ran it).
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, parent)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()

    body = {"message": "and now the second folder", "wait": False,
            "handed_from_turn": TURN}
    first = client.post(f"/sessions/{parent}/continue", json=body)
    second = client.post(f"/sessions/{parent}/continue", json=body)
    assert first.status_code == 200, first.text
    # Not the "a continuation is running" refusal: the same follow-up.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"] != parent
    assert started == [first.json()["id"]]


def test_the_turn_name_is_cleaned_at_the_door():
    assert SessionCreate(task="t", handed_from_turn="  chat-1 \n").handed_from_turn == "chat-1"
    assert SessionCreate(task="t", handed_from_turn=None).handed_from_turn == ""
    assert len(SessionCreate(task="t", handed_from_turn="x" * 5000).handed_from_turn) <= 200
    assert SessionCreate(task="t").handed_from_turn == ""
    assert ContinueBody(message="m", handed_from_turn=" chat-2 ").handed_from_turn == "chat-2"


# --------------------------------------------------------------------------- #
# The custom-agent door: a turn that picks one of YOUR agents ("custom:<slug>")
# hands off through POST /agents/{slug}/spawn, and two windows post it twice.
# --------------------------------------------------------------------------- #


def _make_agent(client, name="scout") -> None:
    r = client.post(
        "/agents",
        json={
            "name": name,
            "system_prompt": "You are Scout, a focused helper. Be concise.",
            "tools": ["write_file"],
            "description": "test helper",
        },
    )
    assert r.status_code == 200, r.text


def _spawn_body(turn: str | None = TURN) -> dict:
    body = {"task": "sort the receipts", "wait": False, "origin": "chat"}
    if turn is not None:
        body["handed_from_turn"] = turn
    return body


def test_a_custom_agent_hand_off_posted_twice_starts_one_run(app):
    client = TestClient(app)
    _make_agent(client)
    started = _record_starts(app)
    first = client.post("/agents/scout/spawn", json=_spawn_body())
    second = client.post("/agents/scout/spawn", json=_spawn_body())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["status"] == first.json()["status"]
    assert started == [first.json()["id"]]
    assert len(_rows(app)) == 1


def test_a_custom_agent_hand_off_posted_twice_at_once_starts_one_run(app):
    _make_agent(TestClient(app))
    started = _record_starts(app)
    orch = app.state.orchestrator
    real_create = orch.create_session
    entered = 0

    async def held_create(*args, **kwargs):
        nonlocal entered
        entered += 1
        for _ in range(500):
            if entered >= 2:
                break
            await asyncio.sleep(0)
        return await real_create(*args, **kwargs)

    orch.create_session = held_create

    async def both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await asyncio.gather(
                c.post("/agents/scout/spawn", json=_spawn_body()),
                c.post("/agents/scout/spawn", json=_spawn_body()),
            )

    a, b = asyncio.run(both())
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["id"] == b.json()["id"]
    assert started == [a.json()["id"]]
    assert len(_rows(app)) == 1


def test_a_custom_agent_spawn_with_no_turn_name_behaves_as_before(app):
    client = TestClient(app)
    _make_agent(client)
    started = _record_starts(app)
    a = client.post("/agents/scout/spawn", json=_spawn_body(turn=None))
    b = client.post("/agents/scout/spawn", json=_spawn_body(turn=None))
    assert a.json()["id"] != b.json()["id"]
    assert started == [a.json()["id"], b.json()["id"]]
    assert all(not (r.handed_from_turn or "") for r in _rows(app))


def test_the_spawn_body_cleans_the_turn_name_too():
    assert SpawnBody(task="t", handed_from_turn=" chat-3 ").handed_from_turn == "chat-3"
    assert SpawnBody(task="t").handed_from_turn == ""
