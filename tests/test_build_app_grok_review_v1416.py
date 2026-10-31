"""v1.416.0 review — Grok's Stop ends the command it left running, and NOTHING
ELSE: never a process it did not start, never a window it opened, and never
anything for the four tools before it (their stop is exactly what it was).

Every process here is a real one this test starts (a Python sleeper); the
parent links psutil reads are replaced only to stage a REUSED pid, which
Windows makes routine and no test can wait for."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.hosted import process as PR

PYTHONW = Path(sys.base_prefix) / "pythonw.exe"


def _sleeper(exe: str | os.PathLike[str] = sys.executable) -> subprocess.Popen:
    return subprocess.Popen([str(exe), "-c", "import time; time.sleep(60)"])


def _ctime(p: subprocess.Popen) -> float:
    return psutil.Process(p.pid).create_time()


@pytest.fixture()
def procs_started():
    started: list[subprocess.Popen] = []
    yield started
    for p in started:
        if p.poll() is None:
            p.kill()


def test_descendants_never_follow_a_reused_parent_pid(monkeypatch, procs_started):
    """Windows keeps a process's parent pid after the parent is gone, and hands
    the pid out again. Staged: the user's own process U was started by a
    parent that died; a command Grok ran later got that parent's pid. U's link
    then points at Grok's command — but U is OLDER than it, so it is not its
    child and must never be read (nor ended) as something Grok started."""
    root = _sleeper()
    procs_started.append(root)
    time.sleep(0.05)
    users_own = _sleeper()
    procs_started.append(users_own)
    time.sleep(0.05)
    groks_cmd = _sleeper()
    procs_started.append(groks_cmd)
    assert _ctime(root) < _ctime(users_own) < _ctime(groks_cmd)
    links = {groks_cmd.pid: root.pid, users_own.pid: groks_cmd.pid}
    monkeypatch.setattr(psutil, "_ppid_map", lambda: dict(links))
    got = [pid for pid, _ in PR.descendants(root.pid)]
    assert groks_cmd.pid in got, "what the tool really started is read"
    assert users_own.pid not in got, "an older process behind a reused pid is never the tool's"


def test_kill_left_needs_the_same_start_time_exactly(procs_started):
    p = _sleeper()
    procs_started.append(p)
    made = _ctime(p)
    assert PR.kill_left([(p.pid, made + 0.3)]) == [], "another start time is another process"
    assert p.poll() is None
    assert PR.kill_left([(p.pid, made)]) == [p.pid]
    assert p.wait(timeout=10) is not None


@pytest.mark.skipif(not PYTHONW.is_file(), reason="needs a Windows-GUI program (pythonw.exe)")
def test_kill_left_spares_a_window_program_the_tool_opened(procs_started):
    """The app's own rule (v1.333.0, v1.404.0): a window a command opened is
    the user's now — Notepad on Windows 11 is a Store program, outside the
    Job, and may hold unsaved work."""
    gui = _sleeper(PYTHONW)
    procs_started.append(gui)
    console = _sleeper()
    procs_started.append(console)
    ended = PR.kill_left([(gui.pid, _ctime(gui)), (console.pid, _ctime(console))])
    assert ended == [console.pid]
    assert gui.poll() is None, "a window program is never ended by the stop's sweep"


def _stop_with_escaped(tmp_path, monkeypatch, escaped, **kw) -> None:
    made = _ctime(escaped)
    monkeypatch.setattr(PR, "descendants", lambda pid: [(escaped.pid, made)])
    hp = PR.HarnessProcess([sys.executable, "-c", "import sys; sys.stdin.read()"], env=dict(os.environ),
                           cwd=str(tmp_path), name="t", **kw)
    loop = asyncio.new_event_loop()
    try:
        hp.start(loop, lambda m: None, lambda c: None)
        hp.stop()
    finally:
        loop.close()


def test_the_other_tools_stop_is_unchanged(tmp_path, monkeypatch, procs_started):
    """Claude Code, Codex, Pi and OpenCode stop exactly as before: their Job
    and tree kill, nothing read or ended outside it."""
    escaped = _sleeper()
    procs_started.append(escaped)
    _stop_with_escaped(tmp_path, monkeypatch, escaped)
    time.sleep(0.3)
    assert escaped.poll() is None


def test_a_tool_that_asks_for_it_ends_what_it_left_outside_its_job(tmp_path, monkeypatch, procs_started):
    escaped = _sleeper()
    procs_started.append(escaped)
    _stop_with_escaped(tmp_path, monkeypatch, escaped, ends_left_behind=True)
    assert escaped.wait(timeout=10) is not None


async def test_only_groks_process_ends_what_it_left_outside_its_job(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import service as S
    from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all
    from tests.test_build_app_grok_v1416 import EFFORT, MODEL, use_fake_grok

    made: list[PR.HarnessProcess] = []

    class Recording(PR.HarnessProcess):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    monkeypatch.setattr(S, "HarnessProcess", Recording)
    work = tmp_path / "work"
    work.mkdir()
    use_fake_grok(monkeypatch, "g_read", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="grok", model=MODEL, effort=EFFORT)
        frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                              json={"text": "do it", "turn_id": "t"}), 30)).text)
    await stop_all(app)
    assert made and all(p.ends_left_behind for p in made)


async def test_stop_while_a_card_waits_is_the_users_stop_not_a_decline(tmp_path, monkeypatch):
    """A Stop answers a waiting card "deny" first (Grok's ``reject-once``),
    and Grok ends its reply on a reject AT ONCE (PermissionRejected) — before
    the cancel that follows. That reply was ended by the user's Stop: it ends
    "interrupted", never "completed" with "You declined, so Grok stopped"."""
    import time as _t

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import service as S
    from iron_jarvis.hosted import words as W
    from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until
    from tests.test_build_app_grok_v1416 import EFFORT, MODEL, use_fake_grok

    note = PR.HarnessProcess.note_started

    def slow_note(self):
        _t.sleep(0.4)  # what Grok started is read (a process walk) while its reject lands
        return note(self)

    monkeypatch.setattr(PR.HarnessProcess, "note_started", slow_note)
    work = tmp_path / "work"
    work.mkdir()
    use_fake_grok(monkeypatch, "g_cmd_deny", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="grok", model=MODEL, effort=EFFORT)
        svc = S.service_for(app.state.platform)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn",
                                            json={"text": "do it", "turn_id": "t"}))
        await until(lambda: s["id"] in svc.live and svc.live[s["id"]].asks)
        r = await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    notices = [e.get("text") for e in evs if e["type"] == "notice"]
    assert W.GROK_DECLINED_ENDS not in notices, "the user pressed Stop; they did not decline"
    assert evs[-1]["stop"] == "interrupted", evs[-1]
