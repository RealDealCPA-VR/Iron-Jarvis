"""v1.249.0 (R-02) — what an update is about to interrupt, and what it leaves.

TWO HALVES OF ONE REPORT. "Restart to update" force-killed the daemon inside
0.6 s with no warning, and the busy check it showed first counted Session rows
and workflow runs only — so a chat reply being written (a chat turn runs as
session id "chat" and has NO Session row) and a Build pane with Claude working
in it were ended without ever being mentioned. And every job the restart cut
off was marked FAILED with nothing offering to pick it up.

Driven through the ROUTES, not the helpers: ``GET /system/activity`` is what
the desktop app's dialog reads and ``GET /sessions/interrupted`` is what the
bell and the Overview offer Continue from, so those are what these tests ask.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.core.turns import CHAT_INFLIGHT
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse


def _app(tmp_path):
    return create_app(str(tmp_path))


def _pane(state: str, cli: str | None = None, alive: bool = True) -> dict:
    return {"id": f"term_{state}", "state": state, "agent_cli": cli, "alive": alive}


# --- what is running right now ------------------------------------------------


def test_an_idle_daemon_is_not_busy(tmp_path):
    client = TestClient(_app(tmp_path))
    body = client.get("/system/activity").json()
    assert body["busy"] is False
    assert body["chat_replies"] == 0 and body["busy_panes"] == 0
    assert body["busy_pane_clis"] == []


def test_a_chat_reply_being_written_is_work_in_progress(tmp_path):
    """The counter is the only thing that can know: a chat turn has no row, so
    the old check reported an idle daemon while a reply was being written."""
    client = TestClient(_app(tmp_path))
    assert client.get("/system/activity").json()["chat_replies"] == 0
    with CHAT_INFLIGHT.track():
        body = client.get("/system/activity").json()
        assert body["chat_replies"] == 1
        assert body["busy"] is True, "an update would still have stopped it without asking"
    assert client.get("/system/activity").json()["chat_replies"] == 0


def test_busy_build_panes_are_counted_and_named(tmp_path, monkeypatch):
    app = _app(tmp_path)
    client = TestClient(app)
    monkeypatch.setattr(
        app.state.platform.terminals,
        "list",
        lambda: [
            _pane("working", "claude"),
            _pane("blocked", "codex"),
            _pane("idle", None),
            _pane("working", "claude", alive=False),  # a dead pane is not working
        ],
    )
    body = client.get("/system/activity").json()
    assert body["busy_panes"] == 2
    assert body["busy_pane_clis"] == ["claude", "codex"]
    assert body["busy"] is True


def test_an_unreadable_pane_list_is_none_known_not_a_500(tmp_path, monkeypatch):
    """The dialog is best-effort by contract: a wedged terminal manager must
    not turn the update check into an error the user cannot get past."""
    app = _app(tmp_path)
    client = TestClient(app)

    def boom():
        raise RuntimeError("terminals are wedged")

    monkeypatch.setattr(app.state.platform.terminals, "list", boom)
    r = client.get("/system/activity")
    assert r.status_code == 200
    assert r.json()["busy_panes"] == 0 and r.json()["busy"] is False


@pytest.mark.asyncio
async def test_the_route_reports_a_reply_while_it_streams(tmp_path):
    """END TO END on the real streaming lane: /system/activity answers "1 chat
    reply" WHILE the stream is open, and 0 once it has ended."""
    app = _app(tmp_path)
    release = asyncio.Event()
    seen: dict[str, int] = {}

    async def slow_stream(**kw):
        # Ask the daemon about itself from INSIDE the turn: the answer must
        # already count this reply.
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as probe:
            seen["during"] = (await probe.get("/system/activity")).json()["chat_replies"]
        await release.wait()
        yield {"type": "text", "text": "done"}
        yield {
            "type": "final",
            "response": LLMResponse(text="done"),
            "provider": "mock",
            "model": "mock",
        }

    app.state.platform.router.stream = slow_stream
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        task = asyncio.create_task(
            client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
        )
        # Wait for the PROBE to have run — `seen` is what it records — rather
        # than for a guessed interval (v1.254.1, the same fix as the walk-away
        # test below). With a fixed sleep, a slow worker left `seen` empty and
        # the assertion below died on a KeyError instead of saying what was
        # wrong.
        for _ in range(400):  # <= 8 s
            if "during" in seen:
                break
            await asyncio.sleep(0.02)
        assert "during" in seen, "the stream never started, so nothing was probed"
        release.set()
        res = await task
        assert res.status_code == 200
        assert seen["during"] == 1, "a reply being written was not counted"
        after = (await client.get("/system/activity")).json()
        assert after["chat_replies"] == 0, "the count outlived the reply"


@pytest.mark.asyncio
async def test_the_count_is_released_when_the_client_walks_away(tmp_path):
    """A reply nobody is listening to must not keep the daemon 'busy' forever —
    that would strand a ready update behind a dialog nobody can satisfy."""
    app = _app(tmp_path)

    async def hanging_stream(**kw):
        await asyncio.sleep(30)
        yield {"type": "final", "response": LLMResponse(text=""), "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = hanging_stream
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        task = asyncio.create_task(
            client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
        )
        # WAIT FOR THE THING THIS ASSERTS (v1.254.1). A fixed 0.2 s sleep is a
        # guess about how quickly the runner gets round to the POST task, and
        # on a loaded CI worker under `-n auto` the guess was wrong: the turn
        # had not yet reached the counter, so this read `0 == 1` and took the
        # v1.254.0 gate red — a test about CANCELLATION failing over
        # scheduling speed. The release side below already polls; this is the
        # same discipline on the arrival side.
        for _ in range(400):  # <= 8 s, far beyond any scheduling delay
            if CHAT_INFLIGHT.count() == 1:
                break
            await asyncio.sleep(0.02)
        assert CHAT_INFLIGHT.count() == 1, "the reply was never counted as in progress"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(100):  # the release runs in the response task's finally
            if CHAT_INFLIGHT.count() == 0:
                break
            await asyncio.sleep(0.02)
        assert CHAT_INFLIGHT.count() == 0, "a cancelled reply stayed 'in progress'"


def test_the_non_stream_lane_counts_its_reply_too(tmp_path):
    """Both lanes, lock-step — the /chat lane is what the phone and the
    one-shot callers use."""
    app = _app(tmp_path)
    client = TestClient(app)
    real = app.state.platform.router.complete
    seen: dict[str, int] = {}

    async def spy(**kw):
        seen["during"] = CHAT_INFLIGHT.count()
        return await real(**kw)

    app.state.platform.router.complete = spy
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert seen["during"] == 1, "a reply being written was not counted"
    assert CHAT_INFLIGHT.count() == 0


# --- what the restart left behind ---------------------------------------------


def _interrupted_row(app, task: str = "rename the Q1 files") -> str:
    """An ACTIVE session, exactly as an update or a crash leaves one behind."""
    with session_scope(app.state.platform.engine) as db:
        s = Session(task=task, status=SessionStatus.ACTIVE)
        db.add(s)
        db.commit()
        return s.id


def _reconcile(app) -> int:
    return app.state.orchestrator.reconcile_interrupted_sessions()


def test_the_boot_reconcile_tags_what_it_interrupted(tmp_path):
    app = _app(tmp_path)
    sid = _interrupted_row(app)
    assert _reconcile(app) == 1
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        assert row.status == SessionStatus.FAILED
        assert row.interrupted_at is not None, "nothing says a restart cut this off"


def test_a_run_that_already_had_a_summary_is_still_tagged(tmp_path):
    """Why the tag exists at all: the summary is written only when the row had
    none, so a job carrying a 'Folder note:' summary looked ordinary and the
    words alone could never say it had been interrupted."""
    app = _app(tmp_path)
    sid = _interrupted_row(app)
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        row.summary = "Folder note: the project folder is not writable"
        db.add(row)
        db.commit()
    _reconcile(app)
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        assert row.interrupted_at is not None
        assert row.summary.startswith("Folder note:"), "an existing summary was clobbered"


@pytest.mark.asyncio
async def test_the_reconcile_announces_once(tmp_path):
    """ONE event per boot, not one per session: fifty interrupted jobs must not
    be fifty rows on the live feed."""
    app = _app(tmp_path)
    for i in range(3):
        _interrupted_row(app, task=f"job {i}")
    _reconcile(app)
    bus = app.state.platform.event_bus
    for _ in range(100):  # published as a task on the running loop
        events = [e for e in bus.history if e.type == "sessions.interrupted"]
        if events:
            break
        await asyncio.sleep(0.02)
    assert len(events) == 1, f"expected one announcement, got {len(events)}"
    assert events[0].payload["count"] == 3
    assert len(events[0].payload["session_ids"]) == 3


def test_the_listing_offers_exactly_what_was_interrupted(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    sid = _interrupted_row(app, task="build the organizer")
    # An ORDINARY failure (nothing to do with a restart) must not be offered:
    # the user never started it expecting to continue it.
    with session_scope(app.state.platform.engine) as db:
        other = Session(task="a plain failure", status=SessionStatus.FAILED)
        db.add(other)
        db.commit()
        other_id = other.id
    _reconcile(app)
    rows = client.get("/sessions/interrupted").json()["sessions"]
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["task"] == "build the organizer"
    assert rows[0]["interrupted_at"]
    assert other_id not in [r["id"] for r in rows]


def test_the_listing_is_not_swallowed_by_the_session_route(tmp_path):
    """/sessions/{session_id} is registered AFTER this one on purpose —
    FastAPI matches in registration order, and the parameter route would
    happily answer 404 for a session called "interrupted"."""
    client = TestClient(_app(tmp_path))
    r = client.get("/sessions/interrupted")
    assert r.status_code == 200 and "sessions" in r.json()


def test_a_week_old_interruption_is_not_still_being_offered(tmp_path):
    """The offer is for work the user still remembers starting."""
    app = _app(tmp_path)
    client = TestClient(app)
    sid = _interrupted_row(app)
    _reconcile(app)
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        row.interrupted_at = utcnow() - timedelta(days=7)
        db.add(row)
        db.commit()
    assert client.get("/sessions/interrupted").json()["sessions"] == []


def test_dismiss_clears_the_offer_and_leaves_the_run_alone(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    sid = _interrupted_row(app)
    _reconcile(app)
    assert client.post(f"/sessions/{sid}/interrupted/dismiss", json={}).status_code == 200
    assert client.get("/sessions/interrupted").json()["sessions"] == []
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        assert row.status == SessionStatus.FAILED, "dismiss changed the run itself"
        assert row.interrupted_at is None
        assert row.task, "dismiss deleted the record"
    assert client.post("/sessions/session_nope/interrupted/dismiss", json={}).status_code == 404


def test_continuing_the_job_clears_the_offer(tmp_path, monkeypatch):
    """Continue goes through the session's OWN continue route, so the offer
    must be cleared there too — or the bell keeps offering work already
    running, and one click becomes two identical jobs."""
    app = _app(tmp_path)
    client = TestClient(app)
    sid = _interrupted_row(app)
    _reconcile(app)
    seen: dict[str, str] = {}

    async def fake_continue(session_id, message, *a, **kw):
        seen["id"] = session_id
        seen["message"] = message
        with session_scope(app.state.platform.engine) as db:
            follow = Session(task="follow-up", status=SessionStatus.ACTIVE)
            db.add(follow)
            db.commit()
            db.refresh(follow)
            return follow

    monkeypatch.setattr(app.state.orchestrator, "continue_session", fake_continue)
    r = client.post(f"/sessions/{sid}/continue", json={"message": "carry on", "wait": False})
    assert r.status_code == 200, r.text
    assert seen["id"] == sid
    assert client.get("/sessions/interrupted").json()["sessions"] == []
    with session_scope(app.state.platform.engine) as db:
        assert db.get(Session, sid).interrupted_at is None


def test_the_listing_survives_a_daemon_that_has_never_been_restarted(tmp_path):
    """ANTI-VACUITY: with nothing interrupted the surfaces render nothing, so
    a listing that always answered [] would pass every test above."""
    app = _app(tmp_path)
    client = TestClient(app)
    _interrupted_row(app)
    assert client.get("/sessions/interrupted").json()["sessions"] == [], (
        "an ACTIVE session was offered as interrupted before any reconcile ran"
    )
    _reconcile(app)
    assert len(client.get("/sessions/interrupted").json()["sessions"]) == 1
