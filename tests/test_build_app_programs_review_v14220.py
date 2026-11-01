"""v1.422.0 REVIEW of B4 / B6 (an independent reviewer, each pin red first on
the builder's code, then fixed and mutation-checked):

1. "Close Claude Code" stopped a session that was WORKING with no message of
   the user's running: Claude Code writing a reply of its own after its
   helpers ended, a card waiting with no turn open, helper agents still at
   work. The route only looked for a running turn or a claim.
2. A secret value with a space in it after a secret-named flag
   (``--password "my secret phrase"``) reached the row and the notices half
   hidden: the command line was joined into one string first, and the
   masker then hid only its first word.
3. The update warning (``GET /system/activity``) did not count a Build ›
   Code session whose tool runs a program it started (a dev server): an
   update ended it with nothing said. It now counts toward ``busy`` (the
   warning, with plain words) and never toward ``working`` (keep-awake).
4. "Open in terminal" stopped the coding tool — and the dev server it ran —
   with nothing said. A stop that ends programs is now said there too.
5. The shell list said a ``PATH`` folder on a network share is never looked
   in, then asked ``shutil.which`` — which looks in every ``PATH`` folder
   (an SMB connection for a share) and, on Windows, in the daemon's CURRENT
   folder first. The pin spied only ``lexists``.
"""

from __future__ import annotations

import asyncio
import ntpath
import os
import sys

import psutil
import pytest

from iron_jarvis.hosted import programs as PG
from iron_jarvis.hosted import service as S
from iron_jarvis.terminals import shells as SH
from tests._build_app_helpers_v1409 import client, new_session, stop_all, until, use_fake
from tests.test_desktop_reliability_v1249 import _describe, requires_node
from tests.test_build_app_programs_v14220 import _alive, _gone_soon, _notices, _say, _serve_in_tool, _step

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Code view's Job is a Windows Job")


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def started():
    """Every process a test starts: ended at the end, and checked gone BY PID."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    for pid in pids:
        assert not _alive(pid), f"a process the test started ({pid}) is still running"


# --------------------------------------------------------------------------- #
# 1. Close never stops a tool that is working on its own
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("how", ["self_reply", "card", "helpers"])
async def test_close_is_refused_while_the_tool_works_with_no_message_running(app, work, tmp_path, monkeypatch, how):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            assert live.turn is None or not live.turn.running
            if how == "self_reply":
                live.driver._alone = True  # Claude Code woke itself to write a reply
                assert live.replying_alone(svc.clock())
            elif how == "card":
                live.asks["x"] = S.Ask("x", "approval", "Bash")  # a card with no turn open
            else:
                live.driver._tasks["a1"] = "local_agent"  # a helper agent still at work
                assert live.helpers_working(svc.clock())
            proc = live.proc
            r = await c.post(f"/build/app/sessions/{s['id']}/close")
            assert r.status_code == 409, f"Close stopped a tool that was working ({how}): {r.text}"
            assert "Claude Code" in r.json()["detail"]
            assert live.proc is proc and not proc.stopped
            live.asks.clear()
            live.driver._alone = False
            live.driver._tasks.clear()
    finally:
        await stop_all(app)


async def test_close_still_closes_a_session_kept_for_a_background_command(app, work, tmp_path, monkeypatch, started):
    # A dev server Claude Code started as a BACKGROUND command (a task that is
    # not a helper) is exactly what the Close press is for.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            live.driver._alone = False  # a step with no turn open reads as a reply of its own
            live.driver._tasks["b1"] = "local_bash"
            pid, _port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            r = await c.post(f"/build/app/sessions/{s['id']}/close")
            assert r.status_code == 200, r.text
            assert r.json()["closed"] is True and live.proc is None and _gone_soon(pid)
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# 2. A secret with a space never shows half hidden
# --------------------------------------------------------------------------- #
class _P:
    def __init__(self, argv):
        self._argv = argv

    def cmdline(self):
        return list(self._argv)

    def name(self):
        return "node.exe"


@pytest.mark.parametrize("argv, secret", [
    (["node", "server.js", "--password", "my secret phrase"], "phrase"),
    (["node", "server.js", "--token", "abc def ghi"], "ghi"),
    (["node", "server.js", "--api-key", 'say "hi" there'], "there"),
    (["node", "server.js", "--password=my secret phrase"], "phrase"),
    (["node", "server.js", "DB_PASSWORD=open sesame now"], "sesame"),
])
def test_a_secret_value_with_spaces_is_hidden_whole(argv, secret):
    shown = PG._command(psutil, _P(argv))
    assert secret not in shown, shown
    assert shown.startswith("node server.js "), shown
    # Ordinary words with spaces are still shown, kept together.
    assert PG._command(psutil, _P(["node", "serve.js", "--title", "My App"])) == 'node serve.js --title "My App"'


# --------------------------------------------------------------------------- #
# 3. The update warning counts a session that runs a program it started
# --------------------------------------------------------------------------- #
async def test_the_update_warning_counts_a_session_running_a_program_and_keep_awake_does_not(
        app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            act = (await c.get("/system/activity")).json()
            assert act["busy"] is False and act.get("code_serving", 0) == 0, act
            _step(live)
            live.driver._alone = False  # a step with no turn open reads as a reply of its own
            pid, _port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            # Long before the idle stop would look (the reply just ended).
            act = (await c.get("/system/activity")).json()
            assert act["busy"] is True, f"an update would end the dev server with nothing said: {act}"
            assert act["code_serving"] == 1 and act["code_serving_tools"] == ["Claude Code"], act
            assert act["code_replies"] == 0
            assert act["working"] is False, "a dev server waiting for requests kept the PC awake"
            # Kept past the idle stop: still counted.
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == []
            act = (await c.get("/system/activity")).json()
            assert act["busy"] is True and act["code_serving"] == 1, act
            # The program ends: nothing is left to warn about.
            psutil.Process(pid).kill()
            assert _gone_soon(pid)
            act = (await c.get("/system/activity")).json()
            assert act["busy"] is False and act["code_serving"] == 0, act
    finally:
        await stop_all(app)


async def test_a_session_counted_as_a_reply_is_not_counted_twice(app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            await _serve_in_tool(live, tmp_path / "srv.txt", started)
            live.driver._tasks["a1"] = "local_agent"  # helpers at work: it is a reply row
            act = (await c.get("/system/activity")).json()
            assert act["code_replies"] == 1 and act["code_serving"] == 0, act
            live.driver._tasks.clear()
    finally:
        await stop_all(app)


async def test_an_unanswered_read_falls_back_to_what_the_reaper_kept(app, work, tmp_path, monkeypatch):
    # The read is bounded and never stacked: when it cannot answer, the
    # warning counts what the idle check last kept the session for.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            live.driver._alone = False
            live.keeping = PG.Reading(True, [PG.Program(1, 1.0, (3000,), "node server.js")])
            assert PG._READING.acquire(blocking=False)
            try:
                act = (await c.get("/system/activity")).json()
            finally:
                PG._READING.release()
            assert act["busy"] is True and act["code_serving"] == 1, act
    finally:
        await stop_all(app)


@requires_node
def test_the_desktop_warning_names_the_sessions_running_programs(tmp_path):
    said = _describe(
        [
            {"code_serving": 1, "code_serving_tools": ["Claude Code"], "busy": True},
            {"code_serving": 2, "code_serving_tools": ["Codex", "Pi", "Codex"], "busy": True},
            {"code_replies": 1, "code_tools": ["Codex"], "code_serving": 1, "code_serving_tools": ["Pi"],
             "busy": True},
        ],
        tmp_path,
    )
    assert said[0] == "1 coding session in Build › Code running a program it started (Claude Code)"
    assert said[1] == "2 coding sessions in Build › Code running programs they started (Codex, Pi)"
    assert said[2] == ("1 coding session in Build › Code working (Codex) · "
                       "1 coding session in Build › Code running a program it started (Pi)")


# --------------------------------------------------------------------------- #
# 4. "Open in terminal" says what it stopped
# --------------------------------------------------------------------------- #
async def test_open_in_terminal_says_the_programs_it_stopped(app, work, tmp_path, monkeypatch, started):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)

    class _Pane:
        id = "term_test"

    launched: list = []
    monkeypatch.setattr(svc, "_launch_terminal", lambda row, hid: launched.append(hid) or _Pane())
    from iron_jarvis.hosted import store

    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
            live = svc.live[s["id"]]
            _step(live)
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            r = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
            assert r.status_code == 200, r.text
            assert launched and live.proc is None and _gone_soon(pid)
            notes = await _notices(c, s["id"])
            said = [n for n in notes if "to open this conversation in a terminal" in n]
            assert said and f"(port {port})" in said[0], notes
            row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert f"(port {port})" in row["stopped_note"]
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# 5. The shell list never looks in a share, nor in the current folder
# --------------------------------------------------------------------------- #
def test_no_shell_lookup_touches_a_network_share(tmp_path, monkeypatch):
    pf = tmp_path / "ProgramFiles"
    pf.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramW6432", str(pf))
    monkeypatch.setenv("PATH", os.pathsep.join(["\\\\nohost.invalid\\share\\bin", str(tmp_path / "x")]))
    seen: list[str] = []

    def spy(real):
        def probe(p, *a, **k):
            seen.append(str(p))
            return real(p, *a, **k)
        return probe

    for name in ("exists", "lexists", "isfile", "isdir"):
        monkeypatch.setattr(ntpath, name, spy(getattr(ntpath, name)))
    monkeypatch.setattr(os, "stat", spy(os.stat))
    monkeypatch.setattr(os, "access", spy(os.access))
    SH.available_shells()
    assert not [p for p in seen if p.startswith(("\\\\", "//"))], seen


def test_a_pwsh_in_the_current_folder_is_never_the_default(tmp_path, monkeypatch):
    # Windows' own command lookup reads the current folder first; a pwsh.exe
    # dropped there must never become every new terminal's shell.
    pf = tmp_path / "ProgramFiles"
    pf.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramW6432", str(pf))
    # A RELATIVE PATH folder (".") is read against the current folder too.
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(tmp_path / "nothing-here")]))
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    here = tmp_path / "here"
    here.mkdir()
    (here / "pwsh.exe").write_bytes(b"MZ")
    monkeypatch.chdir(here)
    assert [s["name"] for s in SH.available_shells()] == ["powershell", "cmd"]


async def test_continue_on_another_account_says_the_programs_it_stopped(app, work, tmp_path, monkeypatch, started):
    import json

    from iron_jarvis.hosted import store
    from tests.test_build_app_pool_v1409 import _Client, _profiles, _Svc

    use_fake(monkeypatch, "claude_limit", tmp_path / "fake.log")
    h1, _h2, profiles = _profiles(tmp_path)
    fake = _Client(profiles, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1), "unset": []}
    hid = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec), harness_session_id=hid)
    conv = h1 / "projects" / "C--work-demo" / f"{hid}.jsonl"
    conv.parent.mkdir(parents=True)
    conv.write_text('{"type":"user"}\n', encoding="utf-8")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            await _say(c, row.id)
            live = svc.live[row.id]
            assert live.running, "the limit ended the fake tool (this pin needs it alive)"
            _step(live)
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
            assert r.status_code == 200, r.text
            assert live.proc is None and _gone_soon(pid)
            notes = await _notices(c, row.id)
            said = [n for n in notes if "to move this conversation to another account" in n]
            assert said and f"(port {port})" in said[0], notes
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# 6. The read never waits behind the daemon's shared pool
# --------------------------------------------------------------------------- #
async def test_the_read_never_queues_behind_the_shared_pool(app, work, tmp_path, monkeypatch, started):
    # Every offloaded job of the daemon shares the loop's default pool. A read
    # queued there waited out its bound, read "unknown" minute after minute,
    # and after UNREAD_KEEPS_MAX of those the idle stop ended the dev server
    # it never saw — silently. The pool is held busy here: the read answers.
    import threading
    from concurrent.futures import ThreadPoolExecutor

    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(PG, "READ_TIMEOUT_S", 2.0)
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            _pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            gate = threading.Event()
            held = loop.run_in_executor(None, gate.wait, 30)
            try:
                got = (await PG.read_many([live]))[live.sid]
            finally:
                gate.set()
                await held
            assert got.known, "the read waited behind the shared pool and answered nothing"
            assert [p.ports for p in got.programs] == [(port,)]
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# 7. A session that starts working DURING the read is never stopped
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("how", ["card", "message"])
async def test_a_session_that_starts_working_during_the_read_is_not_stopped(app, work, tmp_path, monkeypatch, how):
    # The tree read takes real time off the loop (up to its bound). A card
    # that opens, or a message that comes, meanwhile: the reaper looks again
    # before it stops anything — a stop now would cut that work off.
    import threading

    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    real_read = PG.read
    reading, go = threading.Event(), threading.Event()

    def slow_read(specs):
        reading.set()
        go.wait(10)
        return real_read(specs)

    monkeypatch.setattr(PG, "read", slow_read)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            live.driver._alone = False
            late = max(live.last_active, live.last_heard) + S.IDLE_S + 1
            task = asyncio.ensure_future(svc.reap_once(now=late))
            await until(reading.is_set)
            if how == "card":
                live.asks["x"] = S.Ask("x", "approval", "Bash")
            else:
                live.last_active = late - 5  # a message came (``_drive`` stamps it)
            go.set()
            assert await task == [], f"the idle stop cut off work that began during its read ({how})"
            assert live.proc is not None
            live.asks.clear()
    finally:
        go.set()
        await stop_all(app)


# --------------------------------------------------------------------------- #
# 8. The builder's guarantees a mutation showed unpinned
# --------------------------------------------------------------------------- #
async def test_the_pool_stops_the_session_the_user_looks_at_after_the_others(app, work, tmp_path, monkeypatch):
    # "A looked-at one next-to-last": with no programs anywhere, the session
    # in front outlives an idle one even when it is the OLDER of the two.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(S, "MAX_LIVE", 2)
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            front = await new_session(c, work)
            other = await new_session(c, work)
            third = await new_session(c, work)
            await _say(c, front["id"], "tf")  # the OLDEST: the old rule stopped it first
            await _say(c, other["id"], "to")
            await c.post(f"/build/app/sessions/{front['id']}/seen")
            await _say(c, third["id"], "tt")
            assert svc.live[front["id"]].proc is not None, "the pool stopped the session the user is looking at"
            assert svc.live[other["id"]].proc is None
    finally:
        await stop_all(app)


async def test_a_program_started_between_two_steps_still_counts(app, work, tmp_path, monkeypatch, started):
    # "The FIRST step of the current process": a later step never moves the
    # mark, or a dev server started before the tool's NEXT step reads as one
    # of its own helpers and dies at the idle stop.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(PG, "STARTED_SLACK_S", 0.0)
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live, "tu1", "npm run dev")
            first = live.first_step_at
            pid, port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            await asyncio.sleep(0.05)
            _step(live, "tu2", "curl http://127.0.0.1:3000")  # it checks the server it started
            live.driver._alone = False
            assert live.first_step_at == first, "a later step moved the tool's first step"
            assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [], \
                "a dev server started between two steps was not counted"
            view = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert [p["ports"] for p in view["programs"]] == [[port]]
            assert psutil.pid_exists(pid)
    finally:
        await stop_all(app)


async def test_the_keep_line_goes_once_the_session_is_not_at_risk(app, work, tmp_path, monkeypatch, started):
    # Kept for a dev server, then the user works in it again (and the server
    # ended meanwhile): the row stops saying it keeps the session open.
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            s = await new_session(c, work)
            await _say(c, s["id"])
            live = svc.live[s["id"]]
            _step(live)
            live.driver._alone = False
            pid, _port = await _serve_in_tool(live, tmp_path / "srv.txt", started)
            late = max(live.last_active, live.last_heard) + S.IDLE_S + 1
            assert await svc.reap_once(now=late) == []
            assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["keep_note"]
            psutil.Process(pid).kill()
            assert _gone_soon(pid)
            live.last_active = late  # a message came and its reply ended
            assert await svc.reap_once(now=late + 60) == []
            row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
            assert row["keep_note"] == "" and row["programs"] == [], "the row still says a stopped program keeps it open"
    finally:
        await stop_all(app)
