"""v1.425.0 (Wave 5, B7) — Build › Code: one session's slow start never holds
another session's message, and a seventh session makes room.

The pool's lock used to be held for the WHOLE start of a coding tool (Claude
Code's handshake can take up to a minute cold), so every other session that
needed a process waited behind it. Now the lock is held only to decide: the
place is reserved for the session (``LiveSession.starting``) and, with every
place taken, the least recently used IDLE session is chosen to stop (the
pool's own order) — never one that works, keeps background work, or is still
starting. When none can stop, the message waits for one to finish (said once)
instead of being refused at once.

Driven through the real routes, the real process plumbing and the recorded
fake Claude Code (``tests/fixtures/fake_harness_v1409.py``); the slow start is
the driver's handshake held on a gate the test opens.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _folder(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.mkdir()
    return p


async def _say(c, sid, turn_id, text="ready?"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


def _slow_start_in(monkeypatch, folder: Path) -> asyncio.Event:
    """The tool's start (its handshake) in ``folder`` waits for the returned
    gate — a tool that takes as long as the test says to start."""
    gate = asyncio.Event()
    real_open = C.ClaudeDriver.open

    async def open_(self, **kw):
        if self.cwd == str(folder):
            await asyncio.wait_for(gate.wait(), 60)
        return await real_open(self, **kw)

    monkeypatch.setattr(C.ClaudeDriver, "open", open_)
    return gate


def _slow_spawn_in(monkeypatch, folder: Path) -> threading.Event:
    """The tool's PROCESS start in ``folder`` waits for the returned gate
    (set it from the test): the session holds its place while it has no
    process yet — the window only the reservation covers."""
    from iron_jarvis.hosted import process as P

    gate = threading.Event()
    real_start = P.HarnessProcess.start

    def start(self, *a, **kw):
        if self.cwd == str(folder):
            gate.wait(60)
        return real_start(self, *a, **kw)

    monkeypatch.setattr(P.HarnessProcess, "start", start)
    return gate


async def test_a_slow_start_never_holds_another_sessions_message(app, tmp_path, monkeypatch):
    """A's tool is starting (held); B's whole message — its own cold start
    included — is answered meanwhile. OLD CODE: B waited for the pool's lock
    that A's start held (red: B's message timed out)."""
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    slow, quick = _folder(tmp_path, "slow"), _folder(tmp_path, "quick")
    gate = _slow_start_in(monkeypatch, slow)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, slow)
        b = await new_session(c, quick)
        ta = asyncio.ensure_future(_say(c, a["id"], "ta"))
        await until(lambda: svc.live.get(a["id"]) and svc.live[a["id"]].starting)
        evs = await asyncio.wait_for(_say(c, b["id"], "tb"), 30)
        assert evs[-1] == {**evs[-1], "type": "done", "stop": "completed"}
        assert not ta.done() and svc.live[a["id"]].starting, "A's start was not still running"
        gate.set()
        evs_a = await asyncio.wait_for(ta, 30)
        assert evs_a[-1]["stop"] == "completed"
        assert not svc.live[a["id"]].starting
    await stop_all(app)


async def test_a_session_still_starting_holds_its_place_and_is_never_stopped(app, tmp_path, monkeypatch):
    """With one place (MAX_LIVE 1) taken by A while its tool STARTS — no
    process yet — B makes no room by stopping A (A works) and waits; once A's
    reply ends, B stops A (now idle) and starts. MUTATION: a starting session
    not counted -> B started beside A in a pool of one (red)."""
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(S, "MAX_LIVE", 1)
    slow, other = _folder(tmp_path, "slow"), _folder(tmp_path, "other")
    gate = _slow_spawn_in(monkeypatch, slow)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, slow)
        b = await new_session(c, other)
        ta = asyncio.ensure_future(_say(c, a["id"], "ta"))
        await until(lambda: svc.live.get(a["id"]) and svc.live[a["id"]].starting)
        tb = asyncio.ensure_future(_say(c, b["id"], "tb"))
        await asyncio.sleep(1.0)
        assert not tb.done(), "B started beside A in a pool of one"
        assert svc.live.get(b["id"]) is None or svc.live[b["id"]].proc is None
        gate.set()
        assert (await asyncio.wait_for(ta, 30))[-1]["stop"] == "completed"
        evs_b = await asyncio.wait_for(tb, 30)
        assert evs_b[-1]["stop"] == "completed"
        assert any(e["type"] == "notice" and e["text"] == S.W.POOL_WAITING for e in evs_b)
        assert svc.live[a["id"]].proc is None and svc.live[b["id"]].running
    await stop_all(app)


async def test_the_seventh_session_makes_room_by_stopping_the_least_recently_used_idle_one(
        app, tmp_path, monkeypatch):
    """Six live sessions, one of them working: the seventh stops the least
    recently used IDLE one (never the working one) and starts — not
    refused."""
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sids = []
        for i in range(S.MAX_LIVE):
            s = await new_session(c, _folder(tmp_path, f"w{i}"))
            await _say(c, s["id"], f"t{i}")
            sids.append(s["id"])
        assert sum(svc.live[s].running for s in sids) == S.MAX_LIVE
        base = svc.clock()
        for i, s in enumerate(sids):
            svc.live[s].last_active = svc.live[s].last_heard = base - 1000 + i
        svc.live[sids[0]].asks["x"] = S.Ask("x", "approval", "Write")  # the oldest is working
        seventh = await new_session(c, _folder(tmp_path, "w7"))
        evs = await _say(c, seventh["id"], "t7")
        assert evs[-1]["stop"] == "completed", evs[-1]
        assert not any(e["type"] == "error" for e in evs)
        assert svc.live[sids[0]].running, "a working session was stopped"
        assert svc.live[sids[1]].proc is None, "the least recently used idle session was not the one stopped"
        assert svc.live[seventh["id"]].running
        svc.live[sids[0]].asks.clear()
    await stop_all(app)


async def test_a_waiting_message_starts_as_soon_as_a_session_finishes(app, tmp_path, monkeypatch):
    """Every place taken by a working session: the message waits (said once)
    and starts the moment that session's reply ENDS — woken by the end, not
    found by a later look (the look-again is set past the test's limit).
    OLD CODE: refused at once ("Six Code-view sessions are already working
    right now"). MUTATION: a reply's end does not wake the waiter (red)."""
    from tests._build_app_helpers_v1409 import FIX

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_deny", log)  # A's reply waits on the user's card
    monkeypatch.setattr(S, "MAX_LIVE", 1)
    monkeypatch.setattr(S, "POOL_RECHECK_S", 60.0)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, _folder(tmp_path, "a"))
        b = await new_session(c, _folder(tmp_path, "b"))
        ta = asyncio.ensure_future(_say(c, a["id"], "ta"))
        await until(lambda: svc.live.get(a["id"]) and svc.live[a["id"]].asks)
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / "claude_modes.json"))  # B's tool answers at once
        tb = asyncio.ensure_future(_say(c, b["id"], "tb"))
        await until(lambda: svc.live.get(b["id"]) and svc.live[b["id"]].turn is not None and any(
            e.get("type") == "notice" and e.get("text") == S.W.POOL_WAITING
            for e in svc.live[b["id"]].turn.events))
        assert not tb.done()
        ask = next(iter(svc.live[a["id"]].asks))
        await c.post(f"/build/app/sessions/{a['id']}/answer", json={"request_id": ask, "decision": "deny"})
        assert (await asyncio.wait_for(ta, 30))[-1]["type"] == "done"
        evs = await asyncio.wait_for(tb, 15)
        assert evs[-1]["stop"] == "completed", evs[-1]
        assert sum(1 for e in evs if e.get("type") == "notice" and e.get("text") == S.W.POOL_WAITING) == 1
        assert svc.live[a["id"]].proc is None and svc.live[b["id"]].running
    await stop_all(app)


async def test_two_rows_on_one_conversation_never_start_two_processes(app, tmp_path, monkeypatch):
    """A row whose conversation another row is still STARTING is refused (the
    running-process check alone could not see a start in flight)."""
    from iron_jarvis.hosted import store

    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    work = _folder(tmp_path, "w")
    gate = _slow_spawn_in(monkeypatch, work)
    hid = "b82a93b0-5cb0-4a2c-bcd8-617001f2e67c"
    one = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=hid)
    two = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=hid)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        t1 = asyncio.ensure_future(_say(c, one.id, "t1"))
        await until(lambda: svc.live.get(one.id) and svc.live[one.id].starting)
        evs = await asyncio.wait_for(_say(c, two.id, "t2"), 30)
        assert evs[-1]["stop"] == "failed"
        assert any(e["type"] == "error" and e["title"] == S.W.HELD_BY_ROW for e in evs), evs
        gate.set()
        assert (await asyncio.wait_for(t1, 30))[-1]["stop"] == "completed"
    await stop_all(app)


async def test_a_stop_while_the_message_waits_for_a_place_ends_it_and_nothing_starts(app, tmp_path, monkeypatch):
    """Stop pressed while the message waits: the turn ends at once
    ("interrupted"), the wait ends with it, and when a place comes free later
    the message is NOT started (its tool never hears it). MUTATIONS: a reply's
    end does not wake a waiter -> the wait runs on after the Stop (red); the
    wait does not look at its turn at all -> B's tool starts once A finishes
    (red)."""
    from tests._build_app_helpers_v1409 import FIX, starts

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_deny", log)
    monkeypatch.setattr(S, "MAX_LIVE", 1)
    monkeypatch.setattr(S, "POOL_RECHECK_S", 60.0)
    svc = S.service_for(app.state.platform)
    fb = _folder(tmp_path, "b")
    async with client(app) as c:
        a = await new_session(c, _folder(tmp_path, "a"))
        b = await new_session(c, fb)
        ta = asyncio.ensure_future(_say(c, a["id"], "ta"))
        await until(lambda: svc.live.get(a["id"]) and svc.live[a["id"]].asks)
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / "claude_modes.json"))
        tb = asyncio.ensure_future(_say(c, b["id"], "tb"))
        await until(lambda: svc.live.get(b["id"]) and svc.live[b["id"]].turn is not None and any(
            e.get("type") == "notice" and e.get("text") == S.W.POOL_WAITING
            for e in svc.live[b["id"]].turn.events))
        drive = svc.live[b["id"]].turn.task
        r = await c.post(f"/build/app/sessions/{b['id']}/interrupt")
        assert r.status_code == 200, r.text
        evs = await asyncio.wait_for(tb, 10)
        assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
        # ...and the wait itself ends with it (woken by the turn's end, not
        # the next look 60 s later).
        await asyncio.wait_for(asyncio.shield(drive), 5)
        # A place comes free (A's reply ends): B must not start now.
        ask = next(iter(svc.live[a["id"]].asks))
        await c.post(f"/build/app/sessions/{a['id']}/answer", json={"request_id": ask, "decision": "deny"})
        await asyncio.wait_for(ta, 30)
        await asyncio.sleep(1.5)
        assert not any(s.get("cwd") == str(fb) for s in starts(log)), "B's tool was started after its Stop"
        assert svc.live[b["id"]].proc is None and not svc.live[b["id"]].starting
    await stop_all(app)


async def test_a_stop_while_the_tool_starts_never_sends_it_the_message(app, tmp_path, monkeypatch):
    """Stop pressed while the session's tool is still STARTING (no process
    yet, so Stop ends the turn itself): the start finishes, and the message
    is never sent to the tool — it would answer a reply nobody follows.
    MUTATION: no look at the turn before sending (red: the tool got it)."""
    from tests._build_app_helpers_v1409 import sent

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    work = _folder(tmp_path, "w")
    gate = _slow_spawn_in(monkeypatch, work)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, work)
        ta = asyncio.ensure_future(_say(c, a["id"], "ta"))
        await until(lambda: svc.live.get(a["id"]) and svc.live[a["id"]].starting)
        drive = svc.live[a["id"]].turn.task
        r = await c.post(f"/build/app/sessions/{a['id']}/interrupt")
        assert r.status_code == 200, r.text
        evs = await asyncio.wait_for(ta, 10)
        assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
        gate.set()
        await asyncio.wait_for(asyncio.shield(drive), 30)
        assert svc.live[a["id"]].running, "the tool did not finish starting"
        assert not [m for m in sent(log) if m.get("type") == "user"], "the tool was sent a stopped message"
    await stop_all(app)
