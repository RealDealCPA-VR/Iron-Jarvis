"""v1.333.0 — every child the daemon starts goes through ONE door (``core.procs``).

Two defects lived at every hand-rolled spawn site:

* ``subprocess.run(timeout=)`` on Windows kills only the direct child and then
  drains the pipes with NO timeout, so a grandchild holding the pipe parked the
  worker thread for its whole life (audit D13).
* After an abrupt daemon death every child (agent CLI, ffmpeg, git) was
  orphaned. Each child now sits in its own kill-on-close Job whose only handle
  the daemon holds.

The pins below count calls and liveness (never wall-clock times), drive the
real door, and use a real Windows Job where the behaviour is Windows-only.
"""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import psutil
import pytest

from iron_jarvis.core import procs

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "iron_jarvis"
WINDOWS = sys.platform == "win32"

#: Liveness caps: how long a poll may wait for something that must happen.
#: They bound a hang; they are never the thing asserted.
_LIVENESS_CAP_S = 20.0


def _wait_until(pred, cap: float = _LIVENESS_CAP_S) -> bool:
    end = time.monotonic() + cap
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _gone(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _force_kill(pid: int | None) -> None:
    if not pid:
        return
    try:
        psutil.Process(pid).kill()
    except Exception:  # noqa: BLE001 — cleanup only
        pass


# --------------------------------------------------------------------------- #
# The bound: a timeout kills the tree ONCE and the drain after it is bounded   #
# --------------------------------------------------------------------------- #


class _HangingPopen:
    """A child whose first communicate times out (the D13 shape)."""

    instances: list["_HangingPopen"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = None
        self.communicate_timeouts: list[float | None] = []
        _HangingPopen.instances.append(self)

    def communicate(self, input=None, timeout=None):
        self.communicate_timeouts.append(timeout)
        if len(self.communicate_timeouts) == 1:
            raise subprocess.TimeoutExpired(self.args[0], timeout)
        self.returncode = 1
        return (b"partial", b"")

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def test_a_timeout_kills_the_tree_once_and_the_second_drain_has_a_timeout(monkeypatch):
    _HangingPopen.instances.clear()
    kills: list[object] = []
    monkeypatch.setattr(procs.subprocess, "Popen", _HangingPopen)
    monkeypatch.setattr(procs, "kill_tree", lambda proc: kills.append(proc))

    res = procs.run(["some-cli", "--slow"], timeout=3)

    proc = _HangingPopen.instances[-1]
    assert len(kills) == 1 and kills[0] is proc
    assert proc.communicate_timeouts[0] == 3
    # THE BOUND: the drain after the kill must never be an unbounded wait.
    assert len(proc.communicate_timeouts) == 2
    assert proc.communicate_timeouts[1] is not None
    assert proc.communicate_timeouts[1] == procs.DRAIN_TIMEOUT_S
    assert res.timed_out is True
    assert res.stdout == b"partial"
    with pytest.raises(subprocess.TimeoutExpired):
        res.check_timeout()


def test_a_finished_child_is_not_killed_and_its_bytes_come_back(monkeypatch):
    kills: list[object] = []
    monkeypatch.setattr(procs, "kill_tree", lambda proc: kills.append(proc))

    res = procs.run([sys.executable, "-c", "import sys; sys.stdout.write('hi')"], timeout=60)

    assert res.returncode == 0
    assert res.stdout == b"hi"
    assert res.timed_out is False
    assert kills == []


def test_a_child_reads_eof_on_stdin_instead_of_inheriting_the_daemons(monkeypatch):
    seen: dict[str, object] = {}
    real = subprocess.Popen

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(procs.subprocess, "Popen", _spy)
    res = procs.run([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=60)

    assert seen["stdin"] == subprocess.DEVNULL
    assert res.stdout.strip() == b"''"


def test_text_decodes_like_a_text_mode_pipe():
    assert procs.text(b"a\r\nb\rc\n", "utf-8") == "a\nb\nc\n"
    assert procs.text(b"caf\xc3\xa9", "utf-8") == "café"
    # One bad byte costs that byte, never the whole stream.
    assert procs.text(b"ok \xff ok", "utf-8") == "ok � ok"
    assert procs.text(None) == ""


# --------------------------------------------------------------------------- #
# Priority: below normal only inside the background lane                       #
# --------------------------------------------------------------------------- #


def test_creationflags_carry_below_normal_inside_the_background_lane_and_not_outside(monkeypatch):
    flags: list[int] = []

    class _Recorder:
        def __init__(self, *args, **kwargs):
            flags.append(int(kwargs.get("creationflags", 0)))

    # Portable: the Windows branch is taken with a recorded Popen and no job.
    monkeypatch.setattr(procs, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(procs._Job, "create", classmethod(lambda cls, **kw: None))
    monkeypatch.setattr(procs.subprocess, "Popen", _Recorder)

    procs.spawn(["child"])
    with procs.background_lane(True):
        procs.spawn(["child"])
    with procs.background_lane(False):
        procs.spawn(["child"])
    procs.spawn(["child"], background=True)

    below = procs.BELOW_NORMAL_PRIORITY_CLASS
    assert [bool(f & below) for f in flags] == [False, True, False, True]
    # Every one of them is headless unless a site asks otherwise.
    assert all(f & procs.CREATE_NO_WINDOW for f in flags)


@pytest.mark.skipif(not WINDOWS, reason="Windows priority classes and Job objects")
def test_a_background_child_and_its_own_children_run_below_normal(normal_priority):
    """The child, its own child, and a grandchild that tries to raise ITSELF
    back to normal all stay below normal: the job holds the class."""
    raise_self = (
        "import psutil;"
        "p = psutil.Process();"
        "p.nice(psutil.NORMAL_PRIORITY_CLASS);"
        "print(int(p.nice()))"
    )
    code = (
        "import subprocess, sys, psutil;"
        f"c = subprocess.run([sys.executable, '-c', {raise_self!r}], capture_output=True, text=True);"
        "print(int(psutil.Process().nice())); print(c.stdout.strip())"
    )
    with procs.background_lane(True):
        bg = procs.run([sys.executable, "-c", code], timeout=120)
    fg = procs.run([sys.executable, "-c", code], timeout=120)

    below = str(int(psutil.BELOW_NORMAL_PRIORITY_CLASS))
    normal = str(int(psutil.NORMAL_PRIORITY_CLASS))
    assert procs.text(bg.stdout).split() == [below, below], procs.text(bg.stderr)
    assert procs.text(fg.stdout).split() == [normal, normal], procs.text(fg.stderr)


# --------------------------------------------------------------------------- #
# Nothing outlives the daemon: a real stand-in parent is killed abruptly       #
# --------------------------------------------------------------------------- #

_STAND_IN = r"""
import subprocess, sys, time
from iron_jarvis.core import procs
p = procs.spawn(["cmd", "/c", "ping -n 60 127.0.0.1"], stdout=subprocess.DEVNULL)
print(p.pid, flush=True)
time.sleep(120)
"""


@pytest.mark.skipif(not WINDOWS, reason="Windows Job objects")
def test_a_grandchild_dies_when_the_daemon_that_started_it_is_killed(tmp_path):
    """The stand-in daemon starts `cmd /c ping` through procs and is then
    TERMINATED (no cleanup code runs, like a crash). The kernel closes its
    job handle and ping, the grandchild, goes with it."""
    script = tmp_path / "stand_in.py"
    script.write_text(_STAND_IN, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC))
    # The BASE interpreter, so the process we kill is the one holding the job
    # (a venv launcher would add a process of its own in between).
    parent = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(script)],
        stdout=subprocess.PIPE, env=env, creationflags=procs.CREATE_NO_WINDOW,
    )
    ping_pid = None
    try:
        cmd_pid = int(parent.stdout.readline().strip())
        found: list[int] = []

        def _ping_running() -> bool:
            try:
                kids = psutil.Process(cmd_pid).children(recursive=True)
            except psutil.NoSuchProcess:
                return False
            found[:] = [k.pid for k in kids if k.name().lower() == "ping.exe"]
            return bool(found)

        assert _wait_until(_ping_running), "ping never started under cmd"
        ping_pid = found[0]

        parent.kill()  # TerminateProcess: the stand-in gets no say
        parent.wait(timeout=30)

        assert _wait_until(lambda: _gone(ping_pid)), "the grandchild outlived its daemon"
        assert _gone(cmd_pid)
    finally:
        _force_kill(parent.pid)
        _force_kill(ping_pid)


def test_a_shell_timeout_kills_the_grandchild_that_holds_the_pipe(tmp_path):
    """The D13 shape for real: the shell's grandchild holds stdout open. After
    the timed-out call returns, that grandchild is gone (not merely reported
    as timed out while it keeps running)."""
    marker = tmp_path / "holder.txt"
    holder = (
        f'"{sys.executable}" -c "import os,time,pathlib;'
        f"pathlib.Path(r'{marker}').write_text(str(os.getpid()));time.sleep(120)\""
    )
    pid = None
    try:
        res = procs.run(holder, shell=True, timeout=3, drain_timeout=30)
        assert res.timed_out is True
        assert marker.exists(), "the holder never started"
        pid = int(marker.read_text().strip())
        assert _wait_until(lambda: _gone(pid)), "the pipe holder outlived the timeout"
    finally:
        _force_kill(pid)


@pytest.mark.skipif(not WINDOWS, reason="Windows Job objects")
def test_a_helper_left_behind_by_a_finished_call_does_not_outlive_the_call(tmp_path):
    """The child exits normally but leaves a helper running that holds no pipe
    (so nothing waited for it). Closing the call's job ends it."""
    marker = tmp_path / "helper.txt"
    helper = (
        "import os,time,pathlib;"
        f"pathlib.Path(r'{marker}').write_text(str(os.getpid()));time.sleep(120)"
    )
    code = (
        "import subprocess, sys, time, pathlib;"
        f"subprocess.Popen([sys.executable, '-c', {helper!r}], stdin=subprocess.DEVNULL,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
        f"[time.sleep(0.05) for _ in range(400) if not pathlib.Path(r'{marker}').exists()]"
    )
    pid = None
    try:
        res = procs.run([sys.executable, "-c", code], timeout=120)
        assert res.timed_out is False and res.returncode == 0
        pid = int(marker.read_text().strip())
        assert _wait_until(lambda: _gone(pid)), "the helper outlived the call that started it"
    finally:
        _force_kill(pid)


@pytest.mark.skipif(not WINDOWS, reason="Windows CREATE_SUSPENDED")
def test_a_child_that_could_not_be_resumed_is_started_plainly(monkeypatch):
    monkeypatch.setattr(procs, "_resume", lambda handle: False)

    proc = procs.spawn(
        [sys.executable, "-c", "print('ran')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, _ = proc.communicate(timeout=60)

    assert out.strip() == b"ran"
    assert procs._job_of(proc) is None


# --------------------------------------------------------------------------- #
# A cancelled await kills the child                                            #
# --------------------------------------------------------------------------- #


async def test_a_cancelled_await_kills_the_child_instead_of_leaving_it_running(tmp_path):
    marker = tmp_path / "pid.txt"
    code = (
        "import os, time, pathlib;"
        f"pathlib.Path(r'{marker}').write_text(str(os.getpid()));"
        "time.sleep(120)"
    )
    task = asyncio.create_task(procs.run_async([sys.executable, "-c", code], timeout=300))
    pid = None
    try:
        for _ in range(int(_LIVENESS_CAP_S / 0.05)):
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        pid = int(marker.read_text().strip())

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await asyncio.to_thread(_wait_until, lambda: _gone(pid)), (
            "the child kept running after its await was cancelled"
        )
    finally:
        _force_kill(pid)


# --------------------------------------------------------------------------- #
# keep_windows: a window the user now has is never killed with its command     #
# --------------------------------------------------------------------------- #


def test_the_sweep_stops_only_members_that_show_the_user_nothing(monkeypatch):
    job = types.SimpleNamespace(member_pids=lambda: [11, 22, 33, 44])
    stopped: list[int] = []
    monkeypatch.setattr(procs, "_pids_with_visible_windows", lambda: {22})
    monkeypatch.setattr(procs, "_is_window_program", lambda pid: pid == 33)
    monkeypatch.setattr(procs, "_terminate_pid", stopped.append)

    assert procs._sweep_windowless(job) == [11, 44]
    assert stopped == [11, 44]


def test_a_keep_windows_kill_sweeps_and_never_terminates_the_whole_job(monkeypatch):
    calls: list[str] = []
    job = procs._Job.__new__(procs._Job)
    job.kill_on_close = False
    job.closed = False
    job.terminate = lambda: calls.append("terminate") or True  # type: ignore[method-assign]
    monkeypatch.setattr(procs, "_sweep_windowless", lambda j: calls.append("sweep") or [])
    monkeypatch.setattr(procs, "_plain_tree_kill", lambda p: calls.append("plain"))
    proc = types.SimpleNamespace(_ij_job=job, kill=lambda: calls.append("kill"))

    procs.kill_tree(proc)

    assert calls == ["sweep"]


@pytest.mark.skipif(not WINDOWS, reason="Windows Job objects and PE subsystems")
def test_a_shell_timeout_spares_a_window_program_and_stops_the_console_ones(tmp_path):
    """`pythonw` is a WINDOW program (GUI subsystem) that shows nothing, which
    is exactly the moment before a real app's window appears. The shell call
    times out; cmd and ping (console programs) are stopped, pythonw is not."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("no pythonw.exe beside this interpreter")
    marker = tmp_path / "gui_pid.txt"
    gui = (
        f'start "" /b "{pythonw}" -c "import os,time,pathlib;'
        f"pathlib.Path(r'{marker}').write_text(str(os.getpid()));time.sleep(90)\""
    )
    gui_pid = None
    try:
        res = procs.run(
            f"{gui} & ping -n 60 127.0.0.1",
            shell=True, timeout=3, no_window=False, keep_windows=True, drain_timeout=2,
        )
        assert res.timed_out is True
        assert _wait_until(lambda: marker.exists() and marker.read_text().strip())
        gui_pid = int(marker.read_text().strip())
        assert not _gone(gui_pid), "a window program was killed with its command"
        assert b"Ping statistics" not in res.stdout
    finally:
        _force_kill(gui_pid)


# --------------------------------------------------------------------------- #
# The migrated sites use the door the way their spec says                      #
# --------------------------------------------------------------------------- #


def test_the_shell_tool_keeps_windows_and_its_console(monkeypatch, tmp_path):
    from iron_jarvis.sandbox.native import NativeSandbox

    seen: dict[str, object] = {}

    def _fake_run(argv, **kw):
        seen.update(kw, argv=argv)
        return procs.Result(0, b"out", b"", args=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(procs, "run", _fake_run)
    res = NativeSandbox().run("echo hi", cwd=tmp_path, timeout=7)

    assert res.stdout == "out"
    assert seen["argv"] == "echo hi"
    assert seen["shell"] is True and seen["timeout"] == 7
    assert seen["keep_windows"] is True and seen["no_window"] is False


def test_a_timed_out_shell_still_reads_as_timed_out(monkeypatch, tmp_path):
    from iron_jarvis.sandbox.native import NativeSandbox

    monkeypatch.setattr(
        procs, "run",
        lambda argv, **kw: procs.Result(1, b"", b"", timed_out=True, args=argv, timeout=2),
    )
    res = NativeSandbox().run("ping", cwd=tmp_path, timeout=2)

    assert res.timed_out is True and res.returncode == -1
    assert res.stderr == "timed out after 2s"


async def test_run_code_and_custom_tools_go_through_the_cancellable_door(monkeypatch, tmp_path):
    from iron_jarvis.core.config import load_config
    from iron_jarvis.core.models import DynamicToolRecord
    from iron_jarvis.tools import runcode
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.dynamic import CommandTool

    calls: list[dict] = []

    async def _fake_async(argv, **kw):
        calls.append(dict(kw, argv=list(argv)))
        return procs.Result(0, b"fine", b"", args=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(procs, "run_async", _fake_async)

    rc, out, err = await runcode._run_captured([sys.executable, "x.py"], tmp_path, 9)
    assert (rc, out, err) == (0, "fine", "")

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1",
        config=load_config(str(tmp_path)), event_bus=None, engine=None,
    )
    tool = CommandTool(DynamicToolRecord(
        name="echoer", description="echo", params_json="[]",
        argv_json=f'["{sys.executable.replace(chr(92), chr(92) * 2)}", "-c", "print(1)"]',
        timeout_seconds=30,
    ))
    result = await tool.execute({}, ctx)
    assert result.ok is True and result.output == "fine"

    assert len(calls) == 2
    for kw in calls:
        assert kw["keep_windows"] is True and kw["no_window"] is False
    assert calls[0]["timeout"] == 9 and calls[1]["timeout"] == 30


async def test_a_timed_out_custom_tool_keeps_its_words(monkeypatch, tmp_path):
    from iron_jarvis.core.config import load_config
    from iron_jarvis.core.models import DynamicToolRecord
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.dynamic import CommandTool

    async def _timed_out(argv, **kw):
        return procs.Result(-1, b"", b"", timed_out=True, args=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(procs, "run_async", _timed_out)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1",
        config=load_config(str(tmp_path)), event_bus=None, engine=None,
    )
    tool = CommandTool(DynamicToolRecord(
        name="slow", description="slow", params_json="[]",
        argv_json=f'["{sys.executable.replace(chr(92), chr(92) * 2)}", "-c", "print(1)"]',
        timeout_seconds=5,
    ))
    result = await tool.execute({}, ctx)
    assert result.ok is False and result.error == "command timed out"


def test_git_has_a_timeout_and_says_so_when_it_runs_out(monkeypatch, tmp_path):
    from iron_jarvis.git import integration

    seen: dict[str, object] = {}

    def _timed_out(argv, **kw):
        seen.update(kw)
        return procs.Result(-1, b"", b"", timed_out=True, args=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(procs, "run", _timed_out)

    with pytest.raises(integration.GitError) as err:
        integration._git(["status"], tmp_path)
    assert "did not finish within 300 s" in str(err.value)
    assert seen["timeout"] == integration.GIT_TIMEOUT_S == 300.0
    assert integration._git_code(["status"], tmp_path) == -1


def test_a_cli_status_probe_timeout_still_raises_timeout_expired(monkeypatch):
    from iron_jarvis.providers import cli_auth

    monkeypatch.setattr(
        procs, "run",
        lambda argv, **kw: procs.Result(-1, b"", b"", timed_out=True, args=argv, timeout=12),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        cli_auth._run_probe(["claude", "auth", "status"])


def test_a_hung_transcode_is_reported_as_a_timeout(monkeypatch, tmp_path):
    from iron_jarvis.creative import service

    monkeypatch.setattr(service, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(
        procs, "run",
        lambda argv, **kw: procs.Result(-1, b"", b"", timed_out=True, args=argv, timeout=5),
    )
    with pytest.raises(RuntimeError, match="ffmpeg timed out transcoding the video"):
        service.transcode_to_playable(tmp_path / "a.mp4", tmp_path / "b.mp4", timeout=5)


# --------------------------------------------------------------------------- #
# Word COM: the WINWORD the conversion started is stopped, never the user's    #
# --------------------------------------------------------------------------- #


class _WordHangs:
    pid = 4242
    returncode = None

    def __init__(self, said: bytes):
        self.said = said
        self.calls = 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=timeout or 1)
        return (self.said, b"")


def _fake_word(monkeypatch, *, name: str, created_at: float) -> list[int]:
    killed: list[int] = []

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return name

        def create_time(self):
            return created_at

        def kill(self):
            killed.append(self.pid)

    monkeypatch.setattr(psutil, "Process", _Proc)
    return killed


@pytest.mark.parametrize(
    ("name", "age_s", "stopped"),
    [
        ("WINWORD.EXE", 0.0, True),      # the Word this conversion started
        ("WINWORD.EXE", 3600.0, False),  # a Word the user had open already
        ("notepad.exe", 0.0, False),     # a recycled pid
    ],
)
def test_a_timed_out_conversion_stops_only_the_word_it_started(
    monkeypatch, tmp_path, name, age_s, stopped
):
    from iron_jarvis.documents import legacy

    monkeypatch.setattr(legacy, "word_available", lambda: True)
    monkeypatch.setattr(procs, "kill_tree", lambda proc: None)
    monkeypatch.setattr(
        legacy.subprocess, "Popen", lambda *a, **k: _WordHangs(b"IJ-WORD-PID: 5555\r\n")
    )
    killed = _fake_word(monkeypatch, name=name, created_at=time.time() - age_s)
    src = tmp_path / "letter.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(legacy.LegacyReadError, match="did not finish"):
        legacy.convert_doc_to_docx(src, tmp_path / "x.docx", timeout=1)

    assert killed == ([5555] if stopped else [])


def test_the_word_script_names_the_word_it_started_before_opening_the_file():
    from iron_jarvis.documents import legacy

    script = legacy._WORD_SCRIPT
    assert "IJ-WORD-PID: " in script and legacy._PID_MARK == "IJ-WORD-PID:"
    # Printed BEFORE the call that can hang, and flushed.
    assert script.index("IJ-WORD-PID") < script.index("$word.Documents.Open")
    assert "[Console]::Out.Flush()" in script


# --------------------------------------------------------------------------- #
# The door is the only door: no new raw spawn in src/                          #
# --------------------------------------------------------------------------- #

#: v1.383.0: the harness benchmark's TASK DATA is not Iron Jarvis code. Each
#: ``harness_bench/tasks/<id>/`` holds a standalone project handed to a coding
#: agent (``repo/``), its hidden check and its setup step — stdlib-only
#: programs that run as CHILDREN the bench starts through ``core.procs``
#: (``grading.run_check``, ``setup_step.run_setup``), with their own
#: ``subprocess`` calls inside (a merge task must start git; a build task must
#: build). Nothing in the app imports them. Only this one subtree is skipped.
TASK_DATA = "harness_bench/tasks/"

#: Files that still start a process WITHOUT ``core.procs``, each for a reason.
#: Adding a file here is a decision; say why.
ALLOWED_RAW_SPAWNS: dict[str, str] = {
    "core/procs.py": "the door itself (plus the plain taskkill fallback)",
    "terminals/backend.py": "Build panes: own ConPTY / kill-on-close Job (the Build lane)",
    "terminals/recipes.py": "Build lane: a bounded --help probe of a CLI the pane launches",
    "mcp/client.py": "MCP packs: long-lived stdio child with its own kill-on-close Job",
    "iron_proxy/service.py": "Iron-Proxy must SURVIVE a daemon crash (re-adopted by the next one)",
    "daemon/cli.py": "terminal commands (dev serve, rollback's git probe), not the daemon",
    "daemon/routes/agents.py": "macOS/Linux 'open' launcher: the app it opens must outlive the call",
    "daemon/routes/documents.py": "macOS/Linux 'open' launcher: the app it opens must outlive the call",
    "providers/claude_models.py": "model-catalog handshake: Popen + bounded tree kill since v1.300.0; not Lane P's file",
    "repl/session.py": "the REPL namespace: a long-lived child with its own kill path (Lane D's file)",
    "tools/builtins.py": "dead ShellTool, shadowed by sandbox/shell_tool (v1.175.0); not Lane P's file",
}

_SPAWN_NAMES = {"run", "Popen", "call", "check_call", "check_output", "getoutput",
                "getstatusoutput"}
_ASYNC_SPAWN_NAMES = {"create_subprocess_exec", "create_subprocess_shell"}


def _raw_spawns(tree: ast.AST) -> list[int]:
    """Line numbers of ``subprocess.<spawn>(...)`` / ``asyncio.create_subprocess_*``
    calls, and of spawn names imported straight out of ``subprocess``."""
    lines: list[int] = []
    imported: set[str] = set()
    # `import subprocess as sp` is the same door under another name.
    modules = {"subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWN_NAMES:
                    imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" and alias.asname:
                    modules.add(alias.asname)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in modules and fn.attr in _SPAWN_NAMES:
                lines.append(node.lineno)
            elif fn.value.id == "asyncio" and fn.attr in _ASYNC_SPAWN_NAMES:
                lines.append(node.lineno)
            elif fn.value.id == "os" and (
                fn.attr in ("system", "popen") or fn.attr.startswith("spawn")
            ):
                # os.startfile stays out on purpose: it hands a file to the
                # shell (the user's app), it does not start a child we own.
                lines.append(node.lineno)
        elif isinstance(fn, ast.Name) and fn.id in imported:
            lines.append(node.lineno)
    return lines


def _is_task_data(rel: str) -> bool:
    return rel.startswith(TASK_DATA)


def _scan(*, task_data: bool = False) -> dict[str, list[int]]:
    """Raw spawns in the app's code (``task_data=True``: in the benchmark's
    task data only — see :data:`TASK_DATA`)."""
    found: dict[str, list[int]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if _is_task_data(rel) != task_data:
            continue
        lines = _raw_spawns(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if lines:
            found[rel] = lines
    return found


def test_no_new_raw_subprocess_spawn_appears_outside_the_allowlist():
    found = _scan()
    # Anti-vacuity: the scanner sees the door's own Popen.
    assert "core/procs.py" in found
    stray = {f: lines for f, lines in found.items() if f not in ALLOWED_RAW_SPAWNS}
    assert stray == {}, (
        "a child is started outside core.procs (use procs.run / run_async / spawn, "
        f"or add the file to ALLOWED_RAW_SPAWNS with a reason): {stray}"
    )


def test_only_the_benchmarks_task_data_is_left_out_of_the_scan():
    """The skip is narrow: it covers the task folders and nothing else (the
    benchmark's own modules, which start every task child through
    ``core.procs``, are scanned like any other), and it is not vacuous: the
    task data really does start children of its own."""
    tasks = PKG / TASK_DATA
    assert tasks.is_dir() and (PKG / "harness_bench" / "grading.py").is_file()
    for rel in ("harness_bench/grading.py", "harness_bench/setup_step.py", "harness_bench/runners.py",
                "harness_bench/tasks_index.py", "core/procs.py"):
        assert not _is_task_data(rel), rel
    assert _is_task_data("harness_bench/tasks/frontier-merge-in-progress/setup/make_history.py")
    skipped = _scan(task_data=True)
    assert skipped and all(f.startswith(TASK_DATA) for f in skipped)
    assert not any(f.startswith(TASK_DATA) for f in _scan())
    assert not any(f.startswith("harness_bench/") for f in _scan()), "the bench's own code spawns raw"
    for module in ("grading.py", "setup_step.py"):
        text = (PKG / "harness_bench" / module).read_text(encoding="utf-8")
        assert "procs.run(" in text, module


def test_every_allowlisted_file_still_spawns_so_the_list_cannot_go_stale():
    found = _scan()
    stale = sorted(f for f in ALLOWED_RAW_SPAWNS if f not in found)
    assert stale == [], f"remove these from ALLOWED_RAW_SPAWNS: {stale}"


def test_the_scanner_flags_each_spawn_shape():
    sample = (
        "import subprocess, asyncio\n"
        "from subprocess import Popen as P\n"
        "subprocess.run(['x'])\n"
        "subprocess.check_output(['x'])\n"
        "P(['x'])\n"
        "asyncio.create_subprocess_exec('x')\n"
        "procs.run(['x'], timeout=1)\n"
        "import subprocess as sp\n"
        "sp.Popen(['x'])\n"
        "os.system('x')\n"
        "os.spawnv(0, 'x', [])\n"
        "os.startfile('x.txt')\n"
    )
    assert sorted(_raw_spawns(ast.parse(sample))) == [3, 4, 5, 6, 9, 10, 11]


# --------------------------------------------------------------------------- #
# Review round: a resume is judged by NT_SUCCESS (never a second spawn)        #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not WINDOWS, reason="Windows CREATE_SUSPENDED")
def test_a_resume_that_answers_an_informational_success_runs_the_command_once(
    monkeypatch, tmp_path
):
    """NtResumeProcess answering a SUCCESS-class status other than 0 resumed
    the child: reading it as a failure killed the RUNNING command and started
    it again, so a command with side effects ran twice."""
    k = procs._kernel()
    real = k.nt.NtResumeProcess

    def _informational(handle):
        real(handle)
        return 0x00000103  # STATUS_PENDING: NT_SUCCESS, not STATUS_SUCCESS

    monkeypatch.setattr(k, "nt", types.SimpleNamespace(NtResumeProcess=_informational))
    spawned: list[object] = []
    real_popen = subprocess.Popen

    def _counting(*args, **kwargs):
        spawned.append(args[0])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(procs.subprocess, "Popen", _counting)
    res = procs.run([sys.executable, "-c", "print('ran')"], timeout=60)

    assert res.returncode == 0 and res.stdout.split() == [b"ran"]
    assert len(spawned) == 1, "the command was started a second time"


# --------------------------------------------------------------------------- #
# Review round (lead decision): a keep-windows call that FINISHED keeps what   #
# it left running until its SESSION ends; a timeout or cancel sweeps at once   #
# --------------------------------------------------------------------------- #

_HELPER = (
    "import os, sys, time, pathlib;"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
    "time.sleep(120)"
)


def _starts_and_exits(*children: tuple[str, Path]) -> str:
    """Python code that starts each (interpreter, marker) child with no pipe
    of ours, waits until every marker exists, and exits normally."""
    lines = ["import subprocess, sys, time, pathlib"]
    for exe, marker in children:
        lines.append(
            f"subprocess.Popen([r'{exe}', '-c', {_HELPER!r}, r'{marker}'],"
            " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )
    markers = ", ".join(f"r'{m}'" for _, m in children)
    lines.append(
        f"[time.sleep(0.05) for _ in range(400)"
        f" if not all(pathlib.Path(m).exists() for m in ({markers},))]"
    )
    return "\n".join(lines)


def _pid_in(marker: Path) -> int:
    assert _wait_until(lambda: marker.exists() and marker.read_text().strip()), marker
    return int(marker.read_text().strip())


@pytest.fixture
def clean_keeper():
    procs.end_all()
    yield
    procs.end_all()


@pytest.mark.skipif(not WINDOWS, reason="Windows Job objects")
def test_a_finished_call_keeps_its_hidden_child_until_its_session_ends(tmp_path, clean_keeper):
    """An agent starts a dev server with the shell and uses it on the next
    step: the call FINISHED, so the hidden child it left must stay alive, and
    it is stopped when THAT session's run ends (another session's end leaves
    it alone)."""
    import gc

    marker = tmp_path / "server.txt"
    pid = None
    try:
        with procs.owned_by("sess-a"):
            res = procs.run(
                [sys.executable, "-c", _starts_and_exits((sys.executable, marker))],
                timeout=120, no_window=False, keep_windows=True,
            )
        assert res.timed_out is False and res.returncode == 0
        pid = _pid_in(marker)
        gc.collect()  # the Popen object is gone; the kept job must not close with it

        assert not _gone(pid), "a finished call's hidden child was stopped with the call"
        assert procs.kept_jobs("sess-a") == 1

        assert procs.end_session("sess-b") == []
        assert not _gone(pid)

        stopped = procs.end_session("sess-a")
        assert pid in stopped
        assert _wait_until(lambda: _gone(pid)), "the session ended and its hidden child lived on"
        assert procs.kept_jobs() == 0
    finally:
        _force_kill(pid)


@pytest.mark.skipif(not WINDOWS, reason="Windows Job objects and PE subsystems")
def test_a_window_program_survives_the_end_of_the_session_that_started_it(
    tmp_path, clean_keeper
):
    """`pythonw` is a WINDOW program: the user's app, as far as the sweep can
    tell. The session ends: the hidden console helper is stopped, the window
    program is not."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("no pythonw.exe beside this interpreter")
    gui_marker, hidden_marker = tmp_path / "gui.txt", tmp_path / "hidden.txt"
    gui_pid = hidden_pid = None
    try:
        with procs.owned_by("sess-gui"):
            procs.run(
                [sys.executable, "-c",
                 _starts_and_exits((str(pythonw), gui_marker), (sys.executable, hidden_marker))],
                timeout=120, no_window=False, keep_windows=True,
            )
        gui_pid, hidden_pid = _pid_in(gui_marker), _pid_in(hidden_marker)

        stopped = procs.end_session("sess-gui")

        assert hidden_pid in stopped and gui_pid not in stopped
        assert _wait_until(lambda: _gone(hidden_pid))
        assert not _gone(gui_pid), "a window program was stopped when its session ended"
    finally:
        _force_kill(gui_pid)
        _force_kill(hidden_pid)


class _QuietProc:
    """A child that finishes at once (for the run() bookkeeping pins)."""

    pid = 4243
    returncode = 0

    def communicate(self, input=None, timeout=None):
        return b"", b""

    def poll(self):
        return 0


@pytest.mark.parametrize(
    ("cancelled", "stop"),
    [(None, False), (lambda: False, False), (lambda: True, True)],
)
def test_only_a_call_the_await_gave_up_on_is_swept_when_its_child_finishes(
    monkeypatch, cancelled, stop
):
    """A child that ends because run_async's cancel killed it looks FINISHED
    to the worker thread; the cancel seam tells the two apart, so a cancelled
    keep-windows call is swept instead of kept."""
    released: list[bool] = []
    monkeypatch.setattr(procs, "spawn", lambda argv, **kw: _QuietProc())
    monkeypatch.setattr(
        procs, "release", lambda proc, *, stop_windowless=False: released.append(stop_windowless)
    )
    kw = {} if cancelled is None else {"cancelled": cancelled}

    procs.run(["x"], timeout=5, keep_windows=True, **kw)

    assert released == [stop]


async def test_run_async_tells_its_worker_when_the_await_was_cancelled(monkeypatch):
    import threading

    seen: dict[str, object] = {}
    started, finish = threading.Event(), threading.Event()

    def _fake_run(argv, *, on_spawn=None, cancelled=None, **kw):
        seen["cancelled"] = cancelled
        on_spawn(None)
        started.set()
        finish.wait(30)
        return procs.Result(0, b"", b"")

    monkeypatch.setattr(procs, "run", _fake_run)
    task = asyncio.create_task(procs.run_async(["x"], timeout=5))
    assert await asyncio.to_thread(started.wait, 30)
    assert seen["cancelled"]() is False

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish.set()

    assert seen["cancelled"]() is True


def test_a_timed_out_call_is_swept_not_kept(monkeypatch):
    released: list[bool] = []
    monkeypatch.setattr(procs.subprocess, "Popen", _HangingPopen)
    monkeypatch.setattr(procs, "kill_tree", lambda proc: None)
    monkeypatch.setattr(
        procs, "release", lambda proc, *, stop_windowless=False: released.append(stop_windowless)
    )

    procs.run(["slow"], timeout=1, keep_windows=True)

    assert released == [True]


class _FakeJob:
    def __init__(self, name: str, log: list[str]):
        self.name, self.log = name, log
        self.kill_on_close = False
        self.closed = False

    def close(self):
        self.closed = True
        self.log.append(f"close {self.name}")


def test_the_keeper_stops_the_oldest_job_past_its_cap(monkeypatch, clean_keeper):
    log: list[str] = []
    monkeypatch.setattr(procs, "KEPT_JOBS_MAX", 2)
    monkeypatch.setattr(procs, "_sweep_windowless", lambda job: log.append(f"sweep {job.name}") or [])
    jobs = [_FakeJob(n, log) for n in ("a", "b", "c")]

    for job in jobs:
        procs._keep("chat", job)

    assert log == ["sweep a", "close a"]
    assert procs.kept_jobs("chat") == 2
    assert procs.end_all() == [] and log[2:] == ["sweep b", "close b", "sweep c", "close c"]


def test_release_hands_a_finished_job_with_hidden_members_to_its_session(monkeypatch, clean_keeper):
    """Portable: what release() does with a keep-windows job, by outcome."""
    log: list[str] = []
    monkeypatch.setattr(procs, "_windowless_members", lambda job: [7])
    monkeypatch.setattr(procs, "_sweep_windowless", lambda job: log.append("sweep") or [7])

    finished = types.SimpleNamespace(_ij_job=_FakeJob("f", log), _ij_owner="sess-1")
    procs.release(finished)
    assert log == [] and procs.kept_jobs("sess-1") == 1
    procs.release(finished)  # a second release never keeps it twice
    assert log == [] and procs.kept_jobs("sess-1") == 1

    ended_early = types.SimpleNamespace(_ij_job=_FakeJob("e", log), _ij_owner="sess-1")
    procs.release(ended_early, stop_windowless=True)
    assert log == ["sweep", "close e"] and procs.kept_jobs("sess-1") == 1

    monkeypatch.setattr(procs, "_windowless_members", lambda job: [])
    nothing_left = types.SimpleNamespace(_ij_job=_FakeJob("n", log), _ij_owner="sess-1")
    procs.release(nothing_left)
    assert log[-1] == "close n" and procs.kept_jobs("sess-1") == 1


async def test_the_shell_run_code_and_custom_tools_name_their_session(monkeypatch, tmp_path):
    """The keeper is keyed by the session the tool call belongs to: each of
    the three keep-windows doors starts its child inside ``owned_by``."""
    from iron_jarvis.core.config import load_config
    from iron_jarvis.core.models import DynamicToolRecord
    from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
    from iron_jarvis.tools import runcode
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.dynamic import CommandTool

    owners: list[str] = []

    def _fake_run(argv, **kw):
        owners.append(procs._OWNER.get())
        return procs.Result(0, b"ok", b"", args=argv, timeout=kw.get("timeout"))

    async def _fake_async(argv, **kw):
        owners.append(procs._OWNER.get())
        return procs.Result(0, b"ok", b"", args=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(procs, "run", _fake_run)
    monkeypatch.setattr(procs, "run_async", _fake_async)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="sess-tools", agent_run_id="r1",
        config=load_config(str(tmp_path)), event_bus=None, engine=None,
    )

    assert (await SandboxedShellTool().execute({"command": "echo hi"}, ctx)).ok is True
    assert (await runcode.RunCodeTool().execute({"language": "python", "code": "print(1)"}, ctx)).ok
    tool = CommandTool(DynamicToolRecord(
        name="echoer", description="echo", params_json="[]",
        argv_json=f'["{sys.executable.replace(chr(92), chr(92) * 2)}", "-c", "print(1)"]',
        timeout_seconds=30,
    ))
    assert (await tool.execute({}, ctx)).ok is True

    assert owners == ["sess-tools"] * 3
    assert procs._OWNER.get() == ""  # the mark ends with the call


def test_claude_teardown_ends_the_job_before_it_joins_the_readers(monkeypatch):
    """A helper the CLI left holding a pipe keeps its reader parked; closing
    the job FIRST ends it, so no join waits out its timeout."""
    from iron_jarvis.providers.adapters.claude_native import transport

    order: list[str] = []

    class _Done:
        stdin = stdout = stderr = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    class _Reader:
        def join(self, timeout=None):
            order.append("join")

        def is_alive(self):
            return False

    monkeypatch.setattr(procs, "release", lambda proc, **kw: order.append("release"))
    res = transport._Resources()
    res.proc = _Done()
    res.threads = [_Reader(), _Reader()]

    res.close()

    assert order == ["release", "join", "join"]
