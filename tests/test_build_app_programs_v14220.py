"""v1.422.0 (audit gap B4, fix plan C4.7): Build › Code's idle stop no longer
kills a program the coding tool started and that SERVES (a dev server), and
the session the user looks at gets 60 minutes instead of 15.

Real processes, real trees: a tiny listening program
(``tests/fixtures/listen_server_v14220.py``) is put in the fake tool's OWN
kill-on-close Job (what a command the tool runs is: a member no member may
leave), or started as a real child of a pretend tool (the parent-link path).
Every process a test starts is ended in a ``finally`` and checked gone BY PID.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.core import procs
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import programs as PG
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake

SERVER = Path(__file__).parent / "fixtures" / "listen_server_v14220.py"
#: The real interpreter: a venv's python.exe can be a launcher that starts the
#: real one as ITS child (uv's), and the pid the test holds would not be the
#: program that listens.
PYTHON = getattr(sys, "_base_executable", "") or sys.executable
#: A pretend key on the server's command line: the row must never show it.
SECRET = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Code view's Job is a Windows Job")


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def started():
    """Every process a test starts: ended at the end, and checked gone."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    for pid in pids:
        assert not _alive(pid), f"a process the test started ({pid}) is still running"


def _alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        p.wait(timeout=10)
    except psutil.NoSuchProcess:
        return False
    except psutil.TimeoutExpired:
        return True
    return False


def _gone_soon(pid: int, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


async def _say(c, sid, turn_id="t1", text="ready?"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


def _step(live, tid="tu1", command="npm run dev"):
    """The tool ran a command (Claude Code's own frame): its first step."""
    live.driver.on_message({"type": "assistant", "message": {
        "id": f"m_{tid}", "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}],
    }})


async def _wait_port(out: Path) -> tuple[int, int]:
    await until(lambda: out.exists() and out.read_text(encoding="utf-8").strip())
    pid, port = out.read_text(encoding="utf-8").split()
    return int(pid), int(port)


async def _serve_in_tool(live, out: Path, started: list[int]) -> tuple[int, int]:
    """A listening program in the tool's OWN Job (born now)."""
    # Born suspended, put in the Job, then let run (a process joins a Job
    # only by itself — never what it already started: the v1.346.1 rule).
    proc = subprocess.Popen(  # noqa: S603 — a test fixture
        [PYTHON, str(SERVER), str(out), "--api-key", SECRET],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | procs.CREATE_SUSPENDED,
    )
    started.append(proc.pid)
    job = procs._job_of(live.proc.proc)
    assigned = job is not None and job.assign(int(proc._handle))
    assert procs._resume(int(proc._handle)), "could not let the program run"
    assert job is not None, "the Code view's tool runs in no Job"
    assert assigned, "could not put the program in the tool's Job"
    pid, port = await _wait_port(out)
    assert pid == proc.pid
    return pid, port


async def _notices(c, sid) -> list[str]:
    """The notices in the session's own conversation, as the Code view reads
    them (the log's file is written behind, in batches)."""
    evs = (await c.get(f"/build/app/sessions/{sid}")).json()["events"]
    return [e["text"] for e in evs if e.get("type") == "notice"]


# --------------------------------------------------------------------------- #
# B4: a program the tool started that serves keeps the session
# --------------------------------------------------------------------------- #
async def test_a_listening_program_the_tool_started_keeps_it_and_its_end_frees_it(
        app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            assert live.first_step_proc is live.proc and live.first_step_at
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            late = svc.clock() + S.IDLE_S + 1
            assert await svc.reap_once(now=late) == [], "the idle stop killed a dev server the tool started"
            assert live.proc is not None and psutil.pid_exists(pid)
            view = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert [p["ports"] for p in view["programs"]] == [[port]]
            assert view["programs"][0]["command"].startswith("python.exe listen_server_v14220.py srv.txt --api-key ")
            assert SECRET not in view["programs"][0]["command"], "a key on the command line reached the row"
            assert SECRET not in view["keep_note"]
            assert view["keep_note"].startswith(f"Keeping Claude Code open: it runs a program on port {port} (")
            assert "Close Claude Code" in view["keep_note"], "the row does not say how to stop it"
            assert "pid" not in str(view["programs"]) and str(pid) not in str(view["programs"])
            # The program ends: the next look stops the session like any idle one.
            psutil.Process(pid).kill()
            assert _gone_soon(pid)
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [s["id"]]
            assert live.stopped_note == "", "a stop that ended no program was said as if it had"
    finally:
        await stop_all(app)


async def test_past_the_bound_it_is_stopped_and_what_it_ran_is_said_everywhere(
        app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            quiet_from = max(live.last_active, live.last_heard)
            assert await svc.reap_once(now=quiet_from + PG.PROGRAM_KEEP_MAX_S - 60) == []
            assert await svc.reap_once(now=quiet_from + PG.PROGRAM_KEEP_MAX_S + 1) == [s["id"]]
            assert live.proc is None
            assert _gone_soon(pid), "the stop did not end the tool's tree"
            # 1. the session's own conversation says it at once
            notes = await _notices(c, s["id"])
            said = [n for n in notes if n.startswith("Iron Jarvis closed Claude Code after 8 hours")]
            assert said, notes
            assert f"(port {port})" in said[0] and "listen_server_v14220.py" in said[0]
            assert SECRET not in said[0]
            # 2. the row says it
            row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert row["stopped_note"] == said[0] and row["programs"] == [] and row["keep_note"] == ""
            # 3. the next start names it again, then the row forgets it
            evs = await _say(c, s["id"], "t2")
            start = next(e["text"] for e in evs if e.get("type") == "notice")
            assert start.startswith("Resuming Claude Code after 8 hours idle…"), start
            assert f"(port {port})" in start and "which stopped with it" in start
            row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert row["stopped_note"] == ""
    finally:
        await stop_all(app)


async def test_a_listening_process_started_before_the_tool_acted_is_its_own(
        app, work, tmp_path, monkeypatch, started):
    # An MCP server starts while the tool comes up, before its model is asked
    # anything — and one CAN listen (the QuickBooks pack on 8765, measured).
    # It never holds the session.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(PG, "STARTED_SLACK_S", 0.0)
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            pid, _port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [s["id"]], "no step yet: nothing it ran"
            await _say(c, s["id"], "t2")
            live = svc.live[s["id"]]
            pid2, _ = await _serve_in_tool(live, tmp_path / "srv2.txt", started)
            await asyncio.sleep(0.05)
            _step(live)  # the tool acts only AFTER that program was there
            assert psutil.Process(pid2).create_time() < live.first_step_at
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [s["id"]]
            assert live.stopped_note == ""
            assert _gone_soon(pid) and _gone_soon(pid2)
    finally:
        await stop_all(app)


async def test_the_pool_stops_a_session_with_programs_last_and_says_what_it_ended(
        app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(S, "MAX_LIVE", 2)
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            a = await new_session(c, work)
            b = await new_session(c, work)
            cc = await new_session(c, work)
            await _say(c, a["id"], "ta")  # the OLDEST: the old rule stopped it first
            _step(svc.live[a["id"]])
            pid, port = await _serve_in_tool(svc.live[a["id"]], tmp_path / "srv.txt", started)
            await _say(c, b["id"], "tb")
            await _say(c, cc["id"], "tc")
            assert svc.live[a["id"]].running, "the pool stopped the session running a dev server first"
            assert svc.live[b["id"]].proc is None
            # Only the session with the program is left to stop: it goes, SAID.
            for sid in (cc["id"],):
                svc.live[sid].asks["x"] = S.Ask("x", "approval", "Write")
            await _say(c, b["id"], "tb2")
            assert svc.live[a["id"]].proc is None and _gone_soon(pid)
            notes = await _notices(c, a["id"])
            said = [n for n in notes if "to make room for another session" in n]
            assert said and f"(port {port})" in said[0], notes
            svc.live[cc["id"]].asks.clear()
    finally:
        await stop_all(app)


async def test_a_restart_for_new_settings_says_what_it_stopped(app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
            assert r.status_code == 200, r.text
            assert live.restart, "an effort change no longer restarts Claude Code (this pin needs another way)"
            evs = await _say(c, s["id"], "t2")
            said = [e["text"] for e in evs if e.get("type") == "notice"
                    and e["text"].startswith("Iron Jarvis restarted Claude Code for your new settings.")]
            assert said and f"(port {port})" in said[0], [e for e in evs if e.get("type") == "notice"]
            assert _gone_soon(pid)
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# C4.7: the session the user looks at: 60 minutes, not 15
# --------------------------------------------------------------------------- #
async def test_the_session_in_front_gets_sixty_minutes_and_others_fifteen(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            front = await new_session(c, work)
            other = await new_session(c, work)
            await _say(c, front["id"], "tf")
            await _say(c, other["id"], "to")
            t = {"now": time.monotonic()}
            svc.clock = lambda: t["now"]
            for sid in (front["id"], other["id"]):
                svc.live[sid].last_active = svc.live[sid].last_heard = t["now"]
            # The Code view shows `front`: reading it is a look.
            t["now"] += S.IDLE_S + 1
            await c.get(f"/build/app/sessions/{front['id']}")
            assert await svc.reap_once() == [other["id"]], "the session in front was stopped at 15 minutes"
            # Its beat each minute is a look too (no read needed).
            t["now"] += PG.LOOK_FRESH_S + 1
            assert (await c.post(f"/build/app/sessions/{front['id']}/seen")).json() == {"ok": True}
            assert await svc.reap_once() == [], "the beat did not keep the session in front"
            # Still shown at 59 minutes: kept; past 60: stopped, and said so.
            t["now"] = svc.live[front["id"]].last_active + PG.LOOKED_AT_IDLE_S - 60
            await c.post(f"/build/app/sessions/{front['id']}/seen")
            assert await svc.reap_once() == []
            t["now"] += 61
            await c.post(f"/build/app/sessions/{front['id']}/seen")
            assert await svc.reap_once() == [front["id"]]
            evs = await _say(c, front["id"], "tf2")
            assert next(e["text"] for e in evs if e.get("type") == "notice") == \
                "Resuming Claude Code after 60 minutes idle…"
    finally:
        await stop_all(app)


async def test_a_look_counts_only_while_fresh(app, work, tmp_path, monkeypatch):
    # The window hidden, closed or on another page stops beating: within
    # LOOK_FRESH_S the session is back on the 15-minute rule.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            t = {"now": time.monotonic()}
            svc.clock = lambda: t["now"]
            live = svc.live[s["id"]]
            live.last_active = live.last_heard = t["now"]
            await c.post(f"/build/app/sessions/{s['id']}/seen")
            t["now"] += PG.LOOK_FRESH_S + S.IDLE_S
            assert await svc.reap_once() == [s["id"]]
    finally:
        await stop_all(app)


async def test_the_beat_and_close_routes(app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            assert (await c.post("/build/app/sessions/nope/seen")).status_code == 404
            assert (await c.post("/build/app/sessions/nope/close")).status_code == 404
            s = await new_session(c, work)
            assert (await c.post(f"/build/app/sessions/{s['id']}/close")).json() == {"ok": True, "closed": False}
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == []
            r = (await c.post(f"/build/app/sessions/{s['id']}/close")).json()
            assert r["closed"] is True and any(f"(port {port})" in w for w in r["stopped"])
            assert live.proc is None and _gone_soon(pid), "Close did not stop the tool and its program"
            row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert row["stopped_note"] == "" and row["programs"] == []
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# The tree reading itself (a real parent link, no Job)
# --------------------------------------------------------------------------- #
def test_a_real_child_by_parent_link_counts_and_the_tools_own_port_never(tmp_path, started):
    own, child = tmp_path / "tool.txt", tmp_path / "child.txt"
    t0 = time.time()
    tool = subprocess.Popen([PYTHON, str(SERVER), "--child", str(own), str(child)],  # noqa: S603
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    started.append(tool.pid)
    end = time.monotonic() + 30
    while not (child.exists() and own.exists()) and time.monotonic() < end:
        time.sleep(0.05)
    tool_pid, tool_port = (int(x) for x in own.read_text(encoding="utf-8").split())
    child_pid, child_port = (int(x) for x in child.read_text(encoding="utf-8").split())
    started.append(child_pid)
    assert tool_pid == tool.pid
    got = PG.read([("s1", tool.pid, None, t0)])["s1"]
    assert got.known
    assert [(p.pid, p.ports) for p in got.programs] == [(child_pid, (child_port,))], \
        "the tool's own listening port counted, or its child was missed"
    late = PG.read([("s1", tool.pid, None, time.time() + 5)])["s1"]
    assert late.known and late.programs == [], "a program older than the tool's first step counted"
    assert PG.read([("s1", None, None, t0)])["s1"].programs == []


# --------------------------------------------------------------------------- #
# The rules, alone
# --------------------------------------------------------------------------- #
class _Live:
    def __init__(self):
        self.sid = "s"
        self.looked_at = float("-inf")
        self.keeping = None
        self.unread = 0


def test_a_read_that_did_not_answer_keeps_a_few_minutes_then_the_old_rule():
    lv = _Live()
    unknown = PG.Reading(False)
    for _ in range(PG.UNREAD_KEEPS_MAX):
        assert PG.decide(lv, unknown, quiet=S.IDLE_S + 1, now=0.0, base=S.IDLE_S).keep
    assert not PG.decide(lv, unknown, quiet=S.IDLE_S + 1, now=0.0, base=S.IDLE_S).keep
    assert not PG.decide(lv, None, quiet=PG.PROGRAM_KEEP_MAX_S + 1, now=0.0, base=S.IDLE_S).keep


def test_the_words_name_each_program_on_its_own_line():
    progs = [PG.Program(1, 1.0, (3000,), "node server.js"), PG.Program(2, 2.0, (8000, 8001), "python -m http.server")]
    words = PG.stopped_words("Codex", progs, why="idle", after_s=S.IDLE_S)
    assert words == ("Iron Jarvis closed Codex after 15 minutes with nothing to do. The programs it ran stopped "
                     "with it:\n- node server.js (port 3000)\n- python -m http.server (ports 8000 and 8001)")
    assert PG.stopped_words("Codex", [], why="idle") == ""
    keep = PG.keep_words("Codex", progs)
    assert keep.startswith("Keeping Codex open: it runs 2 programs (node server.js (port 3000); ")
    many = [PG.Program(i, float(i), (3000 + i,), f"p{i}") for i in range(PG.SHOWN_MAX + 2)]
    assert PG.stopped_words("Pi", many, why="evicted").endswith("\n- and 2 more")
