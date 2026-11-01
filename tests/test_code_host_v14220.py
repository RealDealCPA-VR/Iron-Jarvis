"""v1.422.0 — the CODE HOST itself (``iron_jarvis/codehost.py``) and the pieces
that make a Build › Code session survive a restart (``hosted/hostlink.py``,
``hosted/adopt.py``, ``Driver.state`` / ``restore``).

Process tests start a REAL host; every one is told to quit at the end and
checked gone by pid (``tests/_code_host_helpers_v14220.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.hosted import adopt as ADOPT
from iron_jarvis.hosted import driver as D
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import history as HIST
from iron_jarvis.hosted import hostlink as HL
from tests._code_host_helpers_v14220 import alive, host_session

win = pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows")


@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


SLEEPER = "import sys, time\nfor line in sys.stdin:\n    sys.stdout.write(line); sys.stdout.flush()\n"


def _echo(tmp: Path) -> list[str]:
    path = tmp / "echo_tool.py"
    path.write_text(SLEEPER, encoding="utf-8")
    return [sys.executable, str(path)]


def _start_tool(home: Path, tmp: Path) -> tuple[HL.RemoteProcess, list[dict]]:
    """A tool in the host, through the daemon's own door (no loop needed for
    a start: lines wait for one)."""
    got: list[dict] = []
    loop = asyncio.new_event_loop()
    proc = HL.RemoteProcess(_echo(tmp), env=dict(os.environ), cwd=str(tmp), name="t", home=home, sid="s1")
    proc.start(loop, got.append, lambda code: None)
    return proc, got


# --------------------------------------------------------------------------- #
# It is never in the daemon's tree
# --------------------------------------------------------------------------- #
@win
@pytest.mark.timeout(120)
def test_the_host_outlives_a_tree_kill_of_the_daemon_that_started_it(hosts, tmp_path):
    """The desktop's restart is ``taskkill /pid <daemon> /T /F`` — a kill of
    the daemon's whole TREE. The host is started through a launcher that
    exits at once, so it is not in that tree: it and its tool survive."""
    home = hosts.home(tmp_path / "home")
    script = (
        "import sys, os, time, asyncio\n"
        "from iron_jarvis.hosted import hostlink as HL\n"
        "lk = HL.link_for(sys.argv[1], start=True)\n"
        "p = HL.RemoteProcess([sys.executable, sys.argv[2]], env=dict(os.environ), cwd=sys.argv[1], name='t',\n"
        "                     home=sys.argv[1], sid='s1')\n"
        "p.start(asyncio.new_event_loop(), lambda o: None, lambda c: None)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    tool = _echo(tmp_path)[1]
    daemon = subprocess.Popen([sys.executable, "-c", script, str(home), tool], stdout=subprocess.PIPE,
                              cwd=str(Path(__file__).parents[1]), env={**os.environ, HL.ENV_SWITCH: "on"})
    try:
        tool_pid = int(daemon.stdout.readline().strip())
        hosts.pids.add(tool_pid)
        rec = CH.read_record(home)
        hosts.note()
        assert rec is not None and psutil.Process(rec["pid"]).ppid() != daemon.pid
        subprocess.run(["taskkill", "/pid", str(daemon.pid), "/T", "/F"], capture_output=True, timeout=30)
        daemon.wait(timeout=30)
        time.sleep(1.0)
        assert alive(rec["pid"]), "the host outlived the daemon's tree kill"
        assert alive(tool_pid), "and so did its tool"
    finally:
        if daemon.poll() is None:
            daemon.kill()


@win
def test_the_host_keeps_its_launcher_s_pid_from_being_reused(hosts, tmp_path):
    """The launcher hands the host an inherited handle to ITSELF, kept open
    for the host's life: Windows never gives a pid out again while a handle
    to its process is open, so no later child of the daemon can take the
    launcher's number and pull the host into the daemon's tree."""
    home = hosts.home(tmp_path / "home")
    lk = HL.link_for(home, start=True)
    assert lk is not None
    hosts.note()
    assert "holding its launcher" in CH.log_path(home).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The orphan rules
# --------------------------------------------------------------------------- #
class _T:
    def __init__(self, exited: bool = False, working: bool = False) -> None:
        self.exited = exited
        self._working = working

    def working(self, now: float) -> bool:
        return self._working


def test_an_orphan_host_goes_by_itself_and_never_runs_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(CH, "EMPTY_EXIT_S", 60.0)
    monkeypatch.setattr(CH, "ORPHAN_GRACE_S", 600.0)
    monkeypatch.setattr(CH, "ORPHAN_MAX_S", 3 * 3600.0)
    host = CH.Host(tmp_path)
    t0 = 1000.0
    host.detached_since = t0
    # holding nothing: gone after a minute
    assert host.why_exit(t0 + 30) == ""
    assert "holds no running tool" in host.why_exit(t0 + 61)
    # a tool still working: stays past the grace …
    host.tools = {"a": _T(working=True)}
    assert host.why_exit(t0 + 601) == ""
    # … but never past the hard bound
    assert "hard bound" in host.why_exit(t0 + 3 * 3600 + 1)
    # nothing working: gone after the grace, not before
    host.tools = {"a": _T(working=False)}
    assert host.why_exit(t0 + 599) == ""
    assert "nothing it holds is working" in host.why_exit(t0 + 601)
    # an exited tool holds nothing
    host.tools = {"a": _T(exited=True)}
    assert "holds no running tool" in host.why_exit(t0 + 61)
    # a daemon attached: never
    host.conn = object()  # type: ignore[assignment]
    assert host.why_exit(t0 + 10 * 3600) == ""


def test_a_tool_is_working_while_it_speaks_or_while_the_daemon_said_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(CH, "ORPHAN_QUIET_S", 600.0)
    monkeypatch.setattr(CH, "ORPHAN_BUSY_QUIET_S", 7200.0)
    t = CH.Tool(CH.Host(tmp_path), "x", "s", ["x"], {}, "", False)
    t.last_out = 0.0
    assert t.working(599) and not t.working(601)
    t.busy = True
    assert t.working(3600) and not t.working(7201)
    t.exited = True
    assert not t.working(1)


@win
@pytest.mark.timeout(120)
def test_an_orphan_host_stops_its_quiet_tool_gracefully_and_exits(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_CODEHOST_ORPHAN_GRACE_S", "1")
    monkeypatch.setenv("IRONJARVIS_CODEHOST_ORPHAN_QUIET_S", "1")
    monkeypatch.setenv("IRONJARVIS_CODEHOST_TICK_S", "0.2")
    home = hosts.home(tmp_path / "home")
    proc, _ = _start_tool(home, tmp_path)
    hosts.note()
    hosts.pids.add(proc.pid)
    host = CH.read_record(home)["pid"]
    with HL._REG_LOCK:
        lk = HL._LINKS.pop(HL._key(home))
    lk.close()  # the daemon is gone and never comes back
    end = time.monotonic() + 30
    while time.monotonic() < end and (alive(host) or alive(proc.pid)):
        time.sleep(0.2)
    assert not alive(host) and not alive(proc.pid), "an orphan with nothing working goes, with its tool"
    said = CH.log_path(home).read_text(encoding="utf-8")
    assert "nothing it holds is working" in said and "stopped (graceful)" in said, said


@win
@pytest.mark.timeout(120)
def test_an_orphan_host_with_a_busy_tool_still_goes_at_the_hard_bound(hosts, tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_CODEHOST_ORPHAN_GRACE_S", "1")
    monkeypatch.setenv("IRONJARVIS_CODEHOST_ORPHAN_MAX_S", "3")
    monkeypatch.setenv("IRONJARVIS_CODEHOST_TICK_S", "0.2")
    home = hosts.home(tmp_path / "home")
    chatty = tmp_path / "chatty.py"
    chatty.write_text("import time\nwhile True:\n    print('{\"x\": 1}', flush=True)\n    time.sleep(0.2)\n",
                      encoding="utf-8")
    loop = asyncio.new_event_loop()
    proc = HL.RemoteProcess([sys.executable, str(chatty)], env=dict(os.environ), cwd=str(tmp_path), name="t",
                            home=home, sid="s1")
    proc.start(loop, lambda o: None, lambda c: None)
    hosts.note()
    hosts.pids.add(proc.pid)
    host = CH.read_record(home)["pid"]
    with HL._REG_LOCK:
        lk = HL._LINKS.pop(HL._key(home))
    lk.close()
    time.sleep(1.5)
    assert alive(host), "a working tool keeps an orphan host past the grace"
    end = time.monotonic() + 30
    while time.monotonic() < end and alive(host):
        time.sleep(0.2)
    assert not alive(host) and not alive(proc.pid)
    assert "hard bound" in CH.log_path(home).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Who may talk to it
# --------------------------------------------------------------------------- #
def _raw(home: Path, token: str, protocol: int = CH.PROTOCOL):
    rec = CH.read_record(home)
    sock = socket.create_connection(("127.0.0.1", rec["port"]), timeout=10)
    CH.send_frame(sock, {"t": "hello", "token": token, "protocol": protocol, "version": "x"})
    rfile = sock.makefile("rb")
    return sock, rfile


@win
def test_a_connection_without_the_token_is_refused(hosts, tmp_path):
    home = hosts.home(tmp_path / "home")
    assert HL.link_for(home, start=True) is not None
    hosts.note()
    sock, rfile = _raw(home, "not-the-token")
    try:
        header, _ = CH.read_frame(rfile)
        assert header == {"t": "refused", "why": "token"}
        assert CH.read_frame(rfile) is None, "and closed"
    finally:
        sock.close()


@win
def test_a_host_of_another_protocol_takes_only_quit(hosts, tmp_path):
    home = hosts.home(tmp_path / "home")
    assert HL.link_for(home, start=True) is not None
    hosts.note()
    rec = CH.read_record(home)
    sock, rfile = _raw(home, rec["token"], protocol=CH.PROTOCOL + 99)
    try:
        assert CH.read_frame(rfile)[0]["t"] == "welcome"
        CH.send_frame(sock, {"t": "start", "re": 1, "tool": "x", "argv": ["x"], "env": {}})
        assert CH.read_frame(rfile)[0] == {"t": "re", "re": 1, "ok": False, "error": "protocol"}
        CH.send_frame(sock, {"t": "quit", "re": 2})
        assert CH.read_frame(rfile)[0]["ok"] is True
    finally:
        sock.close()
    end = time.monotonic() + 15
    while time.monotonic() < end and alive(rec["pid"]):
        time.sleep(0.1)
    assert not alive(rec["pid"])


@win
def test_only_one_host_runs_per_home(hosts, tmp_path):
    home = hosts.home(tmp_path / "home")
    assert HL.link_for(home, start=True) is not None
    hosts.note()
    first = CH.read_record(home)["pid"]
    second = subprocess.run(CH.host_argv(home), capture_output=True, timeout=60)
    assert second.returncode == 0 and CH.read_record(home)["pid"] == first, "a second host gives way"
    assert "another code host already runs" in CH.log_path(home).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The daemon's side of the state
# --------------------------------------------------------------------------- #
def _claude(**kw):
    from iron_jarvis.hosted.claude import ClaudeDriver

    async def write(obj):
        pass

    return ClaudeDriver(write=write, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                        mode="ask", **kw)


def test_a_drivers_state_comes_back_whole_and_bounded():
    d = _claude()
    d.session_id = "sid-1"
    d.turn_open = True
    d._turn_mid = "u-1"
    d._uuids = {"u-1": ""}
    d._tasks = {"bg": "local_agent"}
    d._streamed = {"m1", "m2"}
    d._tools = {f"t{i}": ("Write", {"file_path": f"f{i}"}) for i in range(260)}
    d.steers = {"n1": "sent"}
    kept = json.loads(json.dumps(d.state()))
    assert "_controls" not in kept and "_write" not in kept and "emit" not in kept, "no live objects kept"
    assert len(kept["_tools"]) == 200, "a map that grows for the whole session keeps its newest 200"
    e = _claude()
    e.restore(kept)
    assert (e.session_id, e.turn_open, e._turn_mid, e._tasks, e._streamed) == (
        "sid-1", True, "u-1", {"bg": "local_agent"}, {"m1", "m2"})
    assert e._tools["t259"] == ("Write", {"file_path": "f259"}) and "t0" not in e._tools
    assert e.background_tasks == 1 and e.steers == {"n1": "sent"}
    assert e._controls is not d._controls, "a fresh waiter, never a copied one"


def test_int_keyed_maps_and_tuples_keep_their_types():
    raw = {1: "a", 2: ("x", [3, {"k": {4, 5}}])}
    assert D.decode_value(json.loads(json.dumps(D.encode_value(raw)))) == raw


def test_a_cards_id_is_the_lines_own_so_a_line_read_again_raises_the_same_card():
    token = D.ASK_SEED.set(["abcd1234-7", 0])
    try:
        first = [D.new_ask_id(), D.new_ask_id()]
    finally:
        D.ASK_SEED.reset(token)
    token = D.ASK_SEED.set(["abcd1234-7", 0])
    try:
        again = [D.new_ask_id(), D.new_ask_id()]
    finally:
        D.ASK_SEED.reset(token)
    assert first == again == ["ask_abcd1234-7_1", "ask_abcd1234-7_2"]
    assert D.new_ask_id() != D.new_ask_id(), "with no line, ids stay random"


def test_a_merged_row_names_the_newest_line_and_a_window_never_sees_it():
    rows: list[dict] = []
    E.coalesce_into(rows, {"type": "text", "item": "m", "delta": "Hel", "turn": "t", "htool": "x", "hseq": 4})
    E.coalesce_into(rows, {"type": "text", "item": "m", "delta": "lo", "turn": "t", "htool": "x", "hseq": 9})
    assert rows == [{"type": "text", "item": "m", "delta": "Hello", "turn": "t", "htool": "x", "hseq": 9}]
    assert HIST.public(rows[0]) == {"type": "text", "item": "m", "delta": "Hello", "turn": "t"}


def test_the_logs_copy_is_stamped_and_the_watchers_dict_keeps_no_kept_texts():
    ev = {"type": "tool", "item": "i", E.FULL_KEY: {"input": "x" * 10}}
    out = ADOPT.stamped(ev, ("tool9", 12))
    assert out["htool"] == "tool9" and out["hseq"] == 12 and out[E.FULL_KEY] == {"input": "x" * 10}
    assert E.FULL_KEY not in ev and "hseq" not in ev, "the watchers' event is untouched"


def test_what_was_shown_is_read_from_the_end_of_the_log(tmp_path):
    d = E.session_dir(tmp_path, "s1")
    d.mkdir(parents=True)
    rows = [
        {"type": "user", "turn": "t1", "htool": "A", "hseq": 0},
        {"type": "approval", "request_id": "ask_1", "turn": "t1", "htool": "A", "hseq": 5},
        {"type": "resolved", "request_id": "ask_1", "decision": "allow", "turn": "t1", "htool": "A", "hseq": 5},
        {"type": "text", "delta": "x", "turn": "t1", "htool": "A", "hseq": 11},
        {"type": "text", "delta": "y", "turn": "t1", "htool": "OTHER", "hseq": 99},
    ]
    (d / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    facts = ADOPT.log_facts(tmp_path, "s1", "A")
    assert facts == {"shown": 11, "resolved": {"ask_1": "allow"}, "open_turn": "t1"}
    with open(d / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "done", "turn": "t1", "stop": "completed"}) + "\n")
    assert ADOPT.log_facts(tmp_path, "s1", "A")["open_turn"] == ""


def test_the_boot_reconcile_leaves_the_kept_sessions_alone(tmp_path):
    from iron_jarvis.hosted.service import reconcile_cut_turns

    for sid in ("kept", "cut"):
        d = E.session_dir(tmp_path, sid)
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(json.dumps({"type": "user", "turn": "t1", "text": "x"}) + "\n",
                                        encoding="utf-8")
    assert reconcile_cut_turns(tmp_path, ["kept"]) == 1
    kept = (E.session_dir(tmp_path, "kept") / "events.jsonl").read_text(encoding="utf-8")
    cut = (E.session_dir(tmp_path, "cut") / "events.jsonl").read_text(encoding="utf-8")
    assert '"done"' not in kept and '"stop": "interrupted"' in cut


# --------------------------------------------------------------------------- #
# Each driver re-arms the waits that have no JSON form
# --------------------------------------------------------------------------- #
def _drv(cls, emitted):
    async def write(obj):
        pass

    return cls(write=write, emit=emitted.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")


async def test_an_acp_reply_still_ends_on_its_prompts_answer_after_a_restore():
    """OpenCode / Grok end a reply when the tool ANSWERS the prompt — a
    waiter with no JSON form, re-armed by ``after_restore``."""
    from iron_jarvis.hosted.opencode import OpenCodeDriver

    before: list[dict] = []
    d = _drv(OpenCodeDriver, before)
    d.turn_open = True
    rid = d._start_prompt([{"type": "text", "text": "hi"}])
    d._message_prompt = rid
    kept = json.loads(json.dumps(d.state()))
    after: list[dict] = []
    e = _drv(OpenCodeDriver, after)
    e.restore(kept)
    e.on_message({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    await asyncio.sleep(0)
    assert [x for x in after if x["type"] == "done"] == [{**after[-1], "type": "done", "stop": "completed"}]


async def test_a_pi_reply_still_hears_its_prompts_answer_after_a_restore():
    from iron_jarvis.hosted.pi import PiDriver

    d = _drv(PiDriver, [])
    d.turn_open = True
    d._waiting_run = True
    d._n = 3
    d._prompt_rid = "ij-prompt-3"
    kept = json.loads(json.dumps(d.state()))
    after: list[dict] = []
    e = _drv(PiDriver, after)
    e.restore(kept)
    e.on_message({"type": "response", "id": "ij-prompt-3", "success": False, "error": "No API key for model"})
    await asyncio.sleep(0)
    assert after and after[-1]["type"] == "done" and after[-1]["stop"] == "failed"


async def test_codex_learns_its_turns_id_from_an_answer_that_came_after_a_restore():
    from iron_jarvis.hosted.codex import CodexDriver

    d = _drv(CodexDriver, [])
    d.turn_open = True
    d._n = 7
    kept = json.loads(json.dumps(d.state()))
    e = _drv(CodexDriver, [])
    e.restore(kept)
    e.on_message({"jsonrpc": "2.0", "id": 7, "result": {"turn": {"id": "turn-77"}}})
    await asyncio.sleep(0)
    assert e.turn_id == "turn-77"


async def test_a_claude_reply_held_for_a_note_is_held_again_after_a_restore():
    d = _claude()
    d.turn_open = True
    d._held = {"type": "done", "stop": "completed"}
    kept = json.loads(json.dumps(d.state()))
    e = _claude()
    e.restore(kept)
    assert e._hold_timer is not None, "the held end waits on a timer again"
    e._hold_timer.cancel()


# --------------------------------------------------------------------------- #
# The frozen exe's fast path
# --------------------------------------------------------------------------- #
def test_the_frozen_entry_serves_codehost_without_the_cli(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parents[1] / "packaging"))
    try:
        import ironjarvis_entry as entry
    finally:
        sys.path.pop(0)
    calls: list[list[str]] = []
    monkeypatch.setattr(CH, "main", lambda argv=None: calls.append(list(argv)) or 0)
    assert entry._fast_path(["codehost", "C:\\home"]) == 0
    assert entry._fast_path(["codehost", "--launch", "C:\\home"]) == 0
    assert calls == [["C:\\home"], ["--launch", "C:\\home"]]
    assert entry._fast_path(["codehost", "--help"]) is None, "an option is the CLI's to answer"
    assert entry._fast_path(["codehost"]) is None


def test_the_hidden_subcommand_runs_the_same_module():
    from typer.testing import CliRunner

    from iron_jarvis.daemon.cli import app

    calls: list[list[str]] = []
    real = CH.main
    CH.main = lambda argv=None: calls.append(list(argv)) or 0  # type: ignore[assignment]
    try:
        r = CliRunner().invoke(app, ["codehost", "--launch", "C:\\home"])
    finally:
        CH.main = real  # type: ignore[assignment]
    assert r.exit_code == 0 and calls == [["--launch", "C:\\home"]]


def test_the_host_is_started_by_the_same_program_as_the_daemon(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert CH.host_argv("H", launch=True) == [sys.executable, "codehost", "--launch", "H"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert CH.host_argv("H") == [sys.executable, "-m", "iron_jarvis.codehost", "H"]
