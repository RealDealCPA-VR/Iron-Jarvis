"""v1.404.0 (the Session tab): the programs a coding session started in the
background, and the user's Stop for one.

These pins drive the REAL app factory and a REAL program started by the
session's own ``process_start`` tool under its ``build:<id>`` session id:

* ``GET /build/sessions/{id}/programs`` lists the session's programs newest
  first with exactly the keys the tab reads — never its output, never a pid —
  and never another session's;
* the command is MASKED BEFORE it is cut to one line (a key at the cut must
  not leave half of itself readable);
* ``POST …/programs/{pid}/stop`` stops the program and everything it started,
  keeps it LISTED as stopped, and the session's next ``process_read`` says it
  was stopped (the ``process_stop`` tool forgets it; the user's press must
  not — the model would read "no program");
* an unknown session or program is a 404 in words.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import process as P
from iron_jarvis.harness import sessions as S
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import new_session
from tests.test_harness_tools_process_v1364 import SLEEPER, _FakeProc, _gone

PY = f'"{sys.executable}"'
QUICK = PY + " -c \"print('done')\""
ROW_KEYS = {"id", "command", "running", "left_running", "stopped", "exit_code", "ran_s", "started_s"}


@pytest.fixture(autouse=True)
def _clean_table():
    yield
    P.end_session(None)


@pytest.fixture()
def setup(tmp_path):
    home = str(tmp_path / "home")
    work = tmp_path / "work"
    work.mkdir()
    client = TestClient(create_app(home))
    sid = new_session(client, work)["id"]
    return client, sid, work


def _ctx(work: Path, session_id: str) -> ToolContext:
    return ToolContext(
        workspace=work, session_id=session_id, agent_run_id="r",
        config=SimpleNamespace(home=work.parent, sandbox={"modify_env": "allow"}),
        event_bus=None, engine=None,
    )


def _start(work: Path, session_id: str, command: str, **extra):
    res = asyncio.run(
        P.ProcessStartTool().execute({"command": command, "wait_ms": 10_000, **extra}, _ctx(work, session_id))
    )
    assert res.ok, res.error
    return res.data


def _until(fn, within: float = 15.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    return fn()


def test_the_sessions_programs_are_listed_newest_first_with_only_the_tabs_keys(setup):
    client, sid, work = setup
    first = _start(work, S.session_id_for(sid), QUICK, until_exit=True)
    second = _start(work, S.session_id_for(sid), SLEEPER)
    # Another session's program is never this session's.
    _start(work, S.session_id_for("someone-else"), SLEEPER)

    r = client.get(f"/build/sessions/{sid}/programs")
    assert r.status_code == 200, r.text
    rows = r.json()["programs"]
    assert [row["id"] for row in rows] == [second["id"], first["id"]], "newest first, this session's only"
    for row in rows:
        assert set(row) == ROW_KEYS, "never the output, never a pid"
    running, ended = rows
    assert running["running"] is True and running["stopped"] is False and running["exit_code"] is None
    assert "time.sleep(120)" in running["command"]
    assert ended["running"] is False and ended["stopped"] is False and ended["exit_code"] == 0


def test_the_users_stop_ends_the_program_and_keeps_it_listed_as_stopped(setup):
    client, sid, work = setup
    session = S.session_id_for(sid)
    started = _start(work, session, SLEEPER)
    pid, handle = started["pid"], started["id"]

    r = client.post(f"/build/sessions/{sid}/programs/{handle}/stop")
    assert r.status_code == 200, r.text
    row = r.json()["program"]
    assert row["id"] == handle and row["running"] is False and row["stopped"] is True
    assert row["exit_code"] is None, "a stopped tree's exit code means nothing; it is not shown"
    assert _gone(pid), "the program really ended"

    listed = client.get(f"/build/sessions/{sid}/programs").json()["programs"]
    assert [p["id"] for p in listed] == [handle] and listed[0]["stopped"] is True, "still listed, as stopped"
    # The session's own next read says so (the tool's stop would forget it).
    read = asyncio.run(P.ProcessReadTool().execute({"id": handle}, _ctx(work, session)))
    assert read.ok and "Stopped after" in read.output

    # A second press on a program that already ended changes nothing.
    again = client.post(f"/build/sessions/{sid}/programs/{handle}/stop")
    assert again.status_code == 200 and again.json()["program"]["stopped"] is True


def test_an_unknown_session_or_program_is_a_404_in_words(setup):
    client, sid, work = setup
    other = _start(work, S.session_id_for("someone-else"), SLEEPER)
    assert client.get("/build/sessions/nope/programs").status_code == 404
    r = client.post(f"/build/sessions/{sid}/programs/{other['id']}/stop")
    assert r.status_code == 404, "another session's program is not this session's to stop"
    assert "no longer listed" in r.json()["detail"]
    assert client.post("/build/sessions/nope/programs/p1/stop").status_code == 404
    # ... and the other session's program is still running.
    assert P.TABLE.get(S.session_id_for("someone-else"), other["id"]).running()


def test_a_command_is_masked_before_it_is_cut_to_one_line():
    key = "AKIA" + "ABCDEFGHIJKLMNOP"  # an AWS access key's shape
    # The cut (199 characters) lands seven letters into the key: cut first,
    # and "AKIAABCDEFG" is too short for the mask to see.
    command = "x" * 187 + "\n" + key
    mp = P.ManagedProcess(handle="p1", session_id="s", command=command, cwd=".", proc=_FakeProc())
    shown = P.program_row(mp)["command"]
    assert "ABCDEFG" not in shown, shown
    assert "AKIA***" in shown, shown
    # A shape only the failure mask knows (a Google key), at the cut too.
    command = "x" * 187 + "\n" + "AIza" + "Q" * 35
    mp = P.ManagedProcess(handle="p2", session_id="s", command=command, cwd=".", proc=_FakeProc())
    shown = P.program_row(mp)["command"]
    assert "QQQQ" not in shown and "AIza***" in shown, shown
    assert "\n" not in shown, "one line"
    assert len(shown) <= P.SHOWN_COMMAND_CHARS


def test_the_word_after_a_secret_named_flag_is_masked():
    mp = P.ManagedProcess(handle="p1", session_id="s", command="stripe listen --api-key rk_live_51Hxyz --token tok123abc --port 8080", cwd=".", proc=_FakeProc())
    shown = P.program_row(mp)["command"]
    assert "rk_live_51Hxyz" not in shown and "tok123abc" not in shown, shown
    assert "--port 8080" in shown, "an ordinary flag keeps its value"


def test_a_long_command_is_cut_and_says_so():
    mp = P.ManagedProcess(handle="p1", session_id="s", command="npm run " + "y" * 400, cwd=".", proc=_FakeProc())
    shown = P.program_row(mp)["command"]
    assert len(shown) == P.SHOWN_COMMAND_CHARS and shown.endswith("…")


def test_how_long_a_program_ran_stops_counting_when_it_ends():
    mp = P.ManagedProcess(handle="p1", session_id="s", command="c", cwd=".", proc=_FakeProc(0))
    mp.started_at = 100.0
    mp.ended_at = 112.0
    mp.eof = True
    row = P.program_row(mp, now=500.0)
    assert row["ran_s"] == 12 and row["started_s"] == 400 and row["exit_code"] == 0
    live = P.ManagedProcess(handle="p2", session_id="s", command="c", cwd=".", proc=_FakeProc())
    live.started_at = 100.0
    assert P.program_row(live, now=130.0)["ran_s"] == 30


LEAVER = 'start /b "" ' + PY + ' -c "import time; time.sleep(60)"'


@pytest.mark.skipif(sys.platform != "win32", reason="cmd's start /b")
def test_a_command_that_ended_but_left_its_program_running_is_running_and_stop_ends_it(setup):
    """Review (v1.404.0): ``start /b node server.js`` ends at once and leaves
    the server running. The tab read "Ended (exit 0)" with no Stop, and the
    Stop route did nothing — the server lived on until the session ended."""
    from iron_jarvis.core import procs

    client, sid, work = setup
    session = S.session_id_for(sid)
    started = _start(work, session, LEAVER, until_exit=True)
    mp = P.TABLE.get(session, started["id"])
    assert mp is not None
    _until(lambda: mp.returncode() is not None)
    job = procs._job_of(mp.proc)
    child = _until(lambda: job.member_pids() if job is not None else [pytest.skip("no Job on this PC")])
    assert child, "the program it started runs in its Job"

    def row() -> dict:
        return client.get(f"/build/sessions/{sid}/programs").json()["programs"][0]

    # Past LEFT_GRACE_S after the command ended, the program it left reads
    # as still running.
    seen = _until(lambda: row() if row()["left_running"] else None)
    assert seen and seen["running"] is True and seen["exit_code"] == 0, seen

    r = client.post(f"/build/sessions/{sid}/programs/{started['id']}/stop")
    assert r.status_code == 200, r.text
    stopped = r.json()["program"]
    assert stopped["running"] is False and stopped["left_running"] is False and stopped["stopped"] is True, stopped
    assert all(_gone(pid) for pid in child), "what it started really ended"
    read = asyncio.run(P.ProcessReadTool().execute({"id": started["id"]}, _ctx(work, session)))
    assert read.ok and "Stopped after" in read.output


def test_an_unread_program_says_how_long_it_really_ran(setup):
    """Review (v1.404.0): the end was stamped by the first look, so a short
    run nobody read said "ended after 30 min" when the tab first opened."""
    client, sid, work = setup
    session = S.session_id_for(sid)
    started = _start(work, session, QUICK, wait_ms=0)
    mp = P.TABLE.get(session, started["id"])
    _until(lambda: mp.ended_at is not None)
    assert mp.ended_at is not None, "the reader stamped the end itself"
    assert mp.ended_at - mp.started_at < 15


def test_others_counts_only_what_the_sessions_end_will_stop(monkeypatch):
    """Review (v1.404.0): a window a command opened (an editor) is spared by
    the session's end, so it is not "left running until the session ends"."""
    from iron_jarvis.core import procs

    class _Job:
        def __init__(self, windowless):
            self.windowless = windowless

        def member_pids(self):
            return [1, 2, 3]  # every job has live members; only some are windowless

    kept = [("build:s1", _Job([11])), ("build:s1", _Job([])), ("build:s2", _Job([12]))]
    monkeypatch.setattr(procs, "_KEPT", kept)
    monkeypatch.setattr(procs, "_windowless_members", lambda job: job.windowless)
    assert procs.kept_running("build:s1") == 1
    assert procs.kept_running("build:s2") == 1
    assert procs.kept_running("build:none") == 0


def test_a_member_just_after_the_command_ended_is_not_a_program_left_running(monkeypatch):
    """Review (v1.404.0): Windows' console host stays a member of the Job for
    some milliseconds after EVERY command; it read as "what it started still
    runs" with a Stop for one poll."""
    from iron_jarvis.core import procs

    class _Job:
        closed = False

        def member_pids(self):
            return [4242]

    monkeypatch.setattr(procs, "_job_of", lambda proc: _Job())
    mp = P.ManagedProcess(handle="p1", session_id="s", command="c", cwd=".", proc=_FakeProc(0))
    mp.ended_at = time.monotonic() - 0.05
    assert P.program_row(mp)["left_running"] is False
    mp.ended_at = time.monotonic() - (P.LEFT_GRACE_S + 0.5)
    assert P.program_row(mp)["left_running"] is True


def test_a_stop_that_cannot_reach_what_was_left_running_says_so(setup, monkeypatch):
    """Review (v1.404.0): with no Job and the command already gone, the tree
    kill walks from a dead pid and the child lives on — the Stop must not
    claim it."""
    client, sid, _work = setup
    session = S.session_id_for(sid)
    mp = P.ManagedProcess(handle="p77", session_id=session, command="node server.js", cwd=".", proc=_FakeProc(0))
    mp.ended_at = time.monotonic() - 5  # its command ended; the pipe is still held (eof False)
    stops: list[str] = []
    monkeypatch.setattr(P, "_stop", lambda m: stops.append(m.handle) or [])
    P.TABLE.add(mp)
    try:
        assert P.program_row(mp)["left_running"] is True
        r = client.post(f"/build/sessions/{sid}/programs/p77/stop")
        assert r.status_code == 409, r.text
        assert "could not reach" in r.json()["detail"]
        assert stops == ["p77"], "the stop was tried"
        assert mp.stopped is False, "and not claimed"
    finally:
        P.TABLE.remove(mp)


def test_a_quoted_secret_after_a_secret_flag_is_hidden_whole():
    mp = P.ManagedProcess(handle="p1", session_id="s", command='deploy --token "abc def ghi" --region us', cwd=".", proc=_FakeProc())
    shown = P.program_row(mp)["command"]
    assert "abc" not in shown and "def" not in shown and "ghi" not in shown, shown
    assert "--region us" in shown


def test_the_route_says_how_many_programs_commands_left_running(setup, monkeypatch):
    from iron_jarvis.core import procs

    client, sid, _work = setup
    seen = []
    monkeypatch.setattr(procs, "kept_running", lambda session: seen.append(session) or 2)
    body = client.get(f"/build/sessions/{sid}/programs").json()
    assert body["others"] == 2 and seen == [S.session_id_for(sid)]


def test_a_job_held_program_is_stopped_by_the_jobs_kill_even_before_its_pipe_closes(setup, monkeypatch):
    """A Job's terminate reaches every member; the reader may simply not have
    seen the pipe close yet. That Stop is real and is not refused."""
    from iron_jarvis.core import procs

    client, sid, _work = setup
    session = S.session_id_for(sid)

    class _Job:
        closed = False

        def member_pids(self):
            return [4242]

    monkeypatch.setattr(procs, "_job_of", lambda proc: _Job())
    monkeypatch.setattr(P, "_stop", lambda m: [])
    mp = P.ManagedProcess(handle="p78", session_id=session, command="node server.js", cwd=".", proc=_FakeProc(0))
    mp.ended_at = time.monotonic() - 5
    P.TABLE.add(mp)
    try:
        r = client.post(f"/build/sessions/{sid}/programs/p78/stop")
        assert r.status_code == 200, r.text
        assert r.json()["program"]["stopped"] is True
    finally:
        P.TABLE.remove(mp)
