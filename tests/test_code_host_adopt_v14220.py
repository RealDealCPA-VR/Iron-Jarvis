"""v1.422.0 — Build › Code survives an Iron Jarvis restart (audit gap B3, the
owner's decision of 2026-11-01): the coding tools run in the CODE HOST
(``iron_jarvis/codehost.py``) and a restarted Iron Jarvis carries each
session on (``hosted/adopt.py``).

The daemon is restarted IN-PROCESS here: the old one "dies" without a word
(its link to the host drops and nothing of it reacts), a new one is made on
the same home and runs the lifespan's own Code-view boot steps (ask the host,
reconcile with ``keep``, adopt). The real host and a real fake harness
(``tests/fixtures/fake_harness_v1409.py``, replaying a RECORDED Claude Code
session) run as processes; every one is gone at the end, checked by pid
(``tests/_code_host_helpers_v14220.py``). The real-subprocess version (a
daemon hard-killed and started again) is ``test_code_host_rehearsal_v14220``.

The four promises, one test each, and the fallbacks:

1. an idle session's next message goes to the SAME live tool process — no
   new start, no resume, no "Starting" notice;
2. a reply being written keeps going while Iron Jarvis is down; afterwards
   the window follows the rest of it live, its end is written exactly once
   and nothing already in the log is shown twice;
3. a card the tool had open is shown again (the same card), and answering it
   reaches the tool;
4. background work (a helper agent) is untouched by the restart.

Plus: a QUIT leaves no host and no tool; a restart through ``POST /shutdown``
with ``restart`` keeps them; a reply too long to keep (``overflowed``) falls
back to stopping the tool with its conversation saved.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import adopt as ADOPT
from iron_jarvis.hosted import hostlink as HL
from tests._build_app_helpers_v1409 import client, frames, new_session, until
from tests._code_host_helpers_v14220 import (
    CARD_RID,
    boot,
    config_home,
    crash,
    events_of,
    fake_rows,
    host_session,
    kept,
    mid_reply,
    two_turns,
    use_fake,
    with_card,
    with_helper,
)

@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows"),
              pytest.mark.timeout(240)]


def _starts(log: Path) -> list[dict]:
    return [r for r in fake_rows(log) if "argv" in r]


def _users(log: Path) -> list[dict]:
    return [r["in"] for r in fake_rows(log) if "in" in r and r["in"].get("type") == "user"]


async def _first_app(hosts, tmp_path: Path, monkeypatch, scenario: Path):
    root = tmp_path / "root"
    log = tmp_path / "fake.log"
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, scenario, log)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    app = create_app(str(root))
    return app, root, log, work


# --------------------------------------------------------------------------- #
# 1. an idle session: the same process answers the next message
# --------------------------------------------------------------------------- #
async def test_an_idle_session_answers_its_next_message_on_the_same_process(hosts, tmp_path, monkeypatch):
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, two_turns(tmp_path))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        first = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    assert first[-1]["type"] == "done" and first[-1]["stop"] == "completed", first
    svc = app.state.platform.hosted
    proc = svc.live[sid].proc
    assert isinstance(proc, HL.RemoteProcess) and proc.in_host, "the tool runs in the code host"
    pid = proc.pid
    host = CH.read_record(config_home(root))["pid"]
    assert psutil.Process(pid).ppid() == host, "the tool is the HOST's child, not the daemon's"
    await kept(app, sid)
    crash(app)
    hosts.note()
    assert psutil.pid_exists(pid), "the tool outlived the daemon"

    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    assert svc2 is not None and sid in svc2.live, "the session was carried on"
    live = svc2.live[sid]
    assert live.running and live.proc.pid == pid, "the SAME process, taken back"
    async with client(app2) as c:
        second = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert second[-1]["type"] == "done" and second[-1]["stop"] == "completed", second
    assert "second" in "".join(str(e.get("delta") or e.get("text") or "") for e in second)
    assert not [e for e in second if e["type"] == "notice"], f"no start notice for a live process: {second}"
    assert len(_starts(log)) == 1, "nothing was started again"
    assert len(_users(log)) == 2, "each message reached the one process once"
    assert "--resume" not in _starts(log)[0]["argv"]
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# 2. a reply mid-way: it keeps going, the rest is shown, it ends once
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("when", ["while_it_writes", "after_it_ended", "kept_state_behind"])
async def test_a_reply_being_written_carries_on_and_ends_once(hosts, tmp_path, monkeypatch, when):
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, mid_reply(tmp_path, 4.0))
    home = config_home(root)
    if when == "kept_state_behind":
        # Iron Jarvis dies before it kept the state again: lines ALREADY
        # SHOWN are read again after the restart and must not be shown twice.
        monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 3600.0)
        monkeypatch.setattr(HL, "SNAP_AFTER_WRITE_S", 3600.0)
    c = client(app)
    sid = (await new_session(c, work))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.turn("t1") and any(e.get("delta") == "Hello " for e in svc.turn("t1").events))
    if when != "kept_state_behind":
        await kept(app, sid)
    crash(app)
    monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 1.0)
    monkeypatch.setattr(HL, "SNAP_AFTER_WRITE_S", 0.05)
    follow.cancel()
    await c.aclose()
    if when == "after_it_ended":
        # The whole rest of the reply came while no Iron Jarvis was there.
        await asyncio.sleep(5.5)
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    async with client(app2) as c2:
        if when == "while_it_writes":
            assert svc2.turn("t1") is not None and svc2.turn("t1").running, "the reply is followed again"
        rest = frames((await asyncio.wait_for(c2.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text)
    assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
    said = "".join(str(e.get("delta") or "") for e in rest if e["type"] == "text")
    assert said == "Hello World", f"the window sees the whole reply once: {said!r}"
    logged = [e for e in events_of(home, sid, app2) if e.get("turn") == "t1"]
    dones = [e for e in logged if e["type"] == "done"]
    assert len(dones) == 1 and dones[0]["stop"] == "completed", "its end is written exactly once"
    text = "".join(str(e.get("delta") or "") for e in logged if e["type"] == "text")
    assert text == "Hello World", f"nothing in the log twice: {text!r}"
    assert not [e for e in logged if e["type"] == "error"], "no 'stopped while it worked' for a reply that went on"
    assert len(_starts(log)) == 1
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# 3. a card open across the restart
# --------------------------------------------------------------------------- #
async def test_a_card_open_across_the_restart_is_answerable_and_reaches_the_tool(hosts, tmp_path, monkeypatch):
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, with_card(tmp_path))
    home = config_home(root)
    c = client(app)
    sid = (await new_session(c, work))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.live.get(sid) and svc.live[sid].asks)
    ask_id = next(iter(svc.live[sid].asks))
    await kept(app, sid)
    crash(app)
    follow.cancel()
    await c.aclose()

    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    assert list(svc2.live[sid].asks) == [ask_id], "the same card is open again"
    async with client(app2) as c2:
        pending = (await c2.get(f"/build/app/sessions/{sid}")).json()["pending"]
        assert [p["request_id"] for p in pending] == [ask_id]
        r = await c2.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask_id, "decision": "allow"})
        assert r.status_code == 200, r.text
        rest = frames((await asyncio.wait_for(c2.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text)
    assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
    answers = [r["in"] for r in fake_rows(log) if "in" in r and r["in"].get("type") == "control_response"
               and (r["in"].get("response") or {}).get("request_id") == CARD_RID]
    assert len(answers) == 1 and answers[0]["response"]["response"]["behavior"] == "allow", "the answer reached it once"
    logged = events_of(home, sid, app2)
    assert len([e for e in logged if e["type"] == "approval" and e.get("request_id") == ask_id]) == 1, \
        "the card is drawn once"
    assert len([e for e in logged if e["type"] == "done" and e.get("turn") == "t1"]) == 1
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_a_card_answered_just_before_the_restart_is_not_asked_again(hosts, tmp_path, monkeypatch):
    """The answer reached the tool, but the state kept in the host still shows
    the card open (Iron Jarvis died before it was kept again): the session's
    log has the answer, so the card is NOT reopened and the reply goes on."""
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, with_card(tmp_path))
    home = config_home(root)
    c = client(app)
    sid = (await new_session(c, work))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.live.get(sid) and svc.live[sid].asks)
    await kept(app, sid)
    ask_id = next(iter(svc.live[sid].asks))
    proc = svc.live[sid].proc
    proc.snapshot_source = None  # Iron Jarvis dies before the answered state is kept
    r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask_id, "decision": "allow"})
    assert r.status_code == 200, r.text
    crash(app)
    follow.cancel()
    await c.aclose()
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    assert not svc2.live[sid].asks, "an answered card is not asked again"
    async with client(app2) as c2:
        rest = frames((await asyncio.wait_for(c2.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text)
    assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
    answers = [r["in"] for r in fake_rows(log) if "in" in r and r["in"].get("type") == "control_response"
               and (r["in"].get("response") or {}).get("request_id") == CARD_RID]
    assert len(answers) == 1, "the answer is never sent twice"
    assert len([e for e in events_of(home, sid, app2) if e["type"] == "done" and e.get("turn") == "t1"]) == 1
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# 4. background work is untouched
# --------------------------------------------------------------------------- #
async def test_a_background_helper_keeps_working_across_the_restart(hosts, tmp_path, monkeypatch):
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, with_helper(tmp_path))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        first = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    assert first[-1]["stop"] == "completed"
    svc = app.state.platform.hosted
    assert svc.live[sid].background_tasks == 1
    pid = svc.live[sid].proc.pid
    await kept(app, sid)
    crash(app)
    app2 = await boot(root)
    live = app2.state.platform.hosted.live[sid]
    assert live.proc.pid == pid and live.running
    assert live.background_tasks == 1 and live.background_helpers == 1, "the helper is still counted as working"
    assert live.keeps_working(app2.state.platform.hosted.clock()), "the reaper and the pool leave it be"
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# Quit and restart through the lifespan's own door
# --------------------------------------------------------------------------- #
async def test_a_quit_leaves_no_host_and_no_tool(hosts, tmp_path, monkeypatch):
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, two_turns(tmp_path))
    home = config_home(root)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    host = CH.read_record(home)["pid"]
    tool = app.state.platform.hosted.live[sid].proc.pid
    await ADOPT.shutdown(app.state.platform, keep=bool(getattr(app.state.platform, "code_host_keep", False)))
    await until(lambda: not psutil.pid_exists(host) and not psutil.pid_exists(tool), timeout=20)
    assert CH.read_record(home) is None, "the host's record went with it"
    # The tool was told first — its stdin closed, so it saved and left by
    # itself (exit code 0), before any kill.
    said = CH.log_path(home).read_text(encoding="utf-8")
    assert "exited (code 0)" in said and "stopped (graceful)" in said, said


async def test_shutdown_with_restart_keeps_the_tools_for_the_next_iron_jarvis(hosts, tmp_path, monkeypatch):
    from iron_jarvis.daemon import app as _app_mod

    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, two_turns(tmp_path))
    monkeypatch.setattr(_app_mod, "_graceful_stop", lambda: None)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
        r = await c.post("/shutdown", json={"restart": True})
        assert r.status_code == 200 and r.json()["detail"] == "Iron Jarvis is restarting"
    assert app.state.platform.code_host_keep is True
    pid = app.state.platform.hosted.live[sid].proc.pid
    await ADOPT.shutdown(app.state.platform, keep=app.state.platform.code_host_keep)
    assert psutil.pid_exists(pid), "a restart leaves the tool running"
    app2 = await boot(root)
    assert app2.state.platform.hosted.live[sid].proc.pid == pid
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_shutdown_with_no_body_is_a_quit(tmp_path, monkeypatch):
    from iron_jarvis.daemon import app as _app_mod

    monkeypatch.setattr(_app_mod, "_graceful_stop", lambda: None)
    app = create_app(str(tmp_path / "root"))
    async with client(app) as c:
        r = await c.post("/shutdown")
    assert r.status_code == 200 and r.json()["detail"] == "Iron Jarvis is shutting down"
    assert not getattr(app.state.platform, "code_host_keep", False), "an older desktop's stop is a QUIT"


# --------------------------------------------------------------------------- #
# The fallback: more than the host keeps came while nobody was there
# --------------------------------------------------------------------------- #
async def test_an_overflowed_reply_falls_back_to_a_graceful_stop_and_the_clis_resume(hosts, tmp_path,
                                                                                    monkeypatch):
    monkeypatch.setenv("IRONJARVIS_CODEHOST_MAX_TOOL_BUFFER", "3000")
    parts = ["Hello "] + [{"sleep": 2.0}] + ["x" * 400 for _ in range(30)]
    from tests._code_host_helpers_v14220 import _text_turn, _turn_split, write_scenario

    start, one, two = _turn_split()
    scenario = write_scenario(tmp_path / "long.json", start + _text_turn(one, parts, "long") + two)
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, scenario)
    home = config_home(root)
    c = client(app)
    sid = (await new_session(c, work))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.turn("t1") and any(e.get("delta") == "Hello " for e in svc.turn("t1").events))
    await kept(app, sid)
    pid = svc.live[sid].proc.pid
    crash(app)
    follow.cancel()
    await c.aclose()
    await asyncio.sleep(3.5)  # the long rest arrives while nobody is attached
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    assert sid not in svc2.live or not svc2.live[sid].running, "not carried on from a broken record"
    await until(lambda: not psutil.pid_exists(pid), timeout=20)
    logged = [e for e in events_of(home, sid, app2) if e.get("turn") == "t1"]
    assert logged[-1]["type"] == "done" and logged[-1]["stop"] == "interrupted"
    err = [e for e in logged if e["type"] == "error"][-1]
    assert err["title"] == ADOPT.GAVE_UP_TITLE and "conversation saved" in err["fix"]
    # The next message resumes the SAME conversation with the CLI's own resume.
    async with client(app2) as c2:
        nxt = frames((await asyncio.wait_for(
            c2.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert nxt[-1]["stop"] == "completed", nxt
    starts = [r for r in fake_rows(log) if "argv" in r]
    assert len(starts) == 2 and "--resume" in starts[1]["argv"]
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_no_host_means_the_tool_starts_as_before_and_says_so_once(tmp_path, monkeypatch):
    """The host could not start: the Code view still works (the tool is a
    plain child of the daemon) and the session says, once, that it will stop
    with a restart."""
    from tests._build_app_helpers_v1409 import stop_all

    monkeypatch.setenv(HL.ENV_SWITCH, "on")
    monkeypatch.setattr(HL, "link_for", lambda home, **kw: None)
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, two_turns(tmp_path), log)
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "root"))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        one = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
        two = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    await stop_all(app)
    assert one[-1]["stop"] == "completed" and two[-1]["stop"] == "completed"
    said = [e for e in one + two if e["type"] == "notice" and e.get("text") == HL.NO_HOST_NOTE]
    assert len(said) == 1, "said once per session"
    proc = app.state.platform.hosted.live[sid].proc
    assert proc is None or proc.local is not None


# --------------------------------------------------------------------------- #
# Lines read again rebuild the driver but never act twice
# --------------------------------------------------------------------------- #
async def test_a_line_read_again_after_the_restart_never_writes_twice(hosts, tmp_path, monkeypatch):
    """A line the tool sent that made the driver ANSWER it (a control request
    Iron Jarvis does not handle: refused) is read again after the restart —
    the host kept everything since the last kept state — and must not be
    answered a second time."""
    from tests._code_host_helpers_v14220 import _turn_split, write_scenario

    start, one, two = _turn_split()
    hook = {"out": {"type": "control_request", "request_id": "hook-1",
                    "request": {"subtype": "hook_callback", "callback_id": "cb"}}}
    scenario = write_scenario(tmp_path / "hook.json", start + one[:2] + [hook] + one[2:] + two)
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, scenario)
    # The host keeps only the state right after the start: every line of the
    # first reply is read again after the restart.
    monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 3600.0)
    monkeypatch.setattr(HL, "SNAP_AFTER_WRITE_S", 3600.0)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        first = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    assert first[-1]["stop"] == "completed"
    crash(app)
    monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 1.0)
    monkeypatch.setattr(HL, "SNAP_AFTER_WRITE_S", 0.05)
    app2 = await boot(root)
    async with client(app2) as c2:
        second = frames((await asyncio.wait_for(
            c2.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert second[-1]["stop"] == "completed", second
    refusals = [r["in"] for r in fake_rows(log) if "in" in r and r["in"].get("type") == "control_response"
                and (r["in"].get("response") or {}).get("request_id") == "hook-1"]
    assert len(refusals) == 1, f"answered once, never again on the read-again line: {refusals}"
    logged = events_of(config_home(root), sid, app2)
    assert len([e for e in logged if e["type"] == "done" and e.get("turn") == "t1"]) == 1
    said = [e for e in logged if e["type"] == "notice" and "hooks" in str(e.get("text", ""))]
    assert len(said) == 1, f"what the line said is not said twice: {said}"
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_a_reply_whose_end_was_read_but_never_written_ends_once_after_the_restart(hosts, tmp_path,
                                                                                         monkeypatch):
    """Iron Jarvis read the tool's last line of a reply (its usage is in the
    log) and died before the reply's end was written: after the restart the
    reply ends — once — instead of waiting forever."""
    app, root, log, work = await _first_app(hosts, tmp_path, monkeypatch, two_turns(tmp_path))
    home = config_home(root)
    c = client(app)
    sid = (await new_session(c, work))["id"]
    svc = app.state.platform.hosted

    async def never(*a, **k):  # Iron Jarvis dies before the end is written
        return None

    monkeypatch.setattr(svc, "_end_turn", never)
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}))
    await until(lambda: any(e["type"] == "usage" and e.get("turn") == "t1" for e in events_of(home, sid, app)))
    await kept(app, sid)
    crash(app)
    follow.cancel()
    await c.aclose()
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    await until(lambda: svc2.turn("t1") is None or not svc2.turn("t1").running, timeout=15)
    logged = [e for e in events_of(home, sid, app2) if e.get("turn") == "t1"]
    dones = [e for e in logged if e["type"] == "done"]
    assert len(dones) == 1 and dones[0]["stop"] == "completed", logged
    await ADOPT.shutdown(app2.state.platform, keep=False)
