"""v1.346.1: an MCP stdio server is born SUSPENDED, put in its Job, then resumed.

v1.346.0's Tests gate (a 48-minute runner) went red on
``test_mcp_stdio_transport_v1291::test_a_kill_reaches_the_launchers_wedged_
grandchild_that_holds_the_pipe`` — "gave up waiting for the wedged grandchild
server to die". The client spawned the launcher with a plain ``Popen`` and
assigned it to the kill-on-close Job AFTERWARDS; a child the launcher had
already forked in that window is NOT in the Job, so ``TerminateJobObject``
killed the launcher and left the real server holding the pipe. Under load the
window is wide. ``core/procs.spawn`` fixed this shape in v1.333.0 (create
suspended → assign → resume); the MCP client kept its own raw spawn.

These pin the MECHANISM on every platform through spies (the race itself
cannot be scheduled on purpose), and the behavioural kill test in
``test_mcp_stdio_transport_v1291`` keeps proving the tree dies.

v1.383.1: THIS FILE LEAKED ONE SUSPENDED PROCESS PER RUN. Its spies used to
REPLACE the Windows primitives: ``_resume`` answered True without resuming,
``_win_job_for`` handed back a pretend Job handle and ``_win_terminate_job``
answered True without ending anything. So the sleeper below — really created
with CREATE_SUSPENDED by the real ``Popen`` — never ran an instruction, and
``close()`` believed the "Job" had ended it and never killed it: a
``python -c "import sys, time; time.sleep(30)"`` parked in the Suspended wait
state forever, one per full suite run (57 on the developer's PC after three
days). The spies now RECORD and DELEGATE to the real functions (a failed
resume is still simulated by not resuming — the client must then kill the
never-ran child itself), and every Windows test checks by process id that no
child it started is left running or suspended.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

from iron_jarvis.core import procs as procs_mod
from iron_jarvis.core.procs import CREATE_SUSPENDED
from iron_jarvis.mcp import client as client_mod
from iron_jarvis.terminals import backend as backend_mod

ECHO = [sys.executable, "-c", "import sys, time; time.sleep(30)"]
# The child is a sleeper, not a server: the handshake must give up quickly
# (MCPServerStopped) — the spawn itself is what these tests watch.
HANDSHAKE_S = 1.0
#: How long a closed child may take to disappear. A hang detector, not a speed
#: bar: ``close()`` already waits for its kill, so a clean run reads it at once.
GONE_WITHIN_S = 15.0


class _Spy:
    def __init__(self) -> None:
        self.flags: list[int] = []
        self.order: list[str] = []
        #: Every sleeper the client started. Holding the ``Popen`` keeps its
        #: process handle open, so a pid read below can never be a recycled
        #: one belonging to some other program.
        self.children: list[subprocess.Popen] = []


def _patch(monkeypatch, spy: _Spy, *, resume_ok: bool = True):
    real_popen = subprocess.Popen
    real_job_for = backend_mod._win_job_for
    real_resume = procs_mod._resume

    class SpyPopen(real_popen):  # type: ignore[misc]
        def __init__(self, *a, **kw):
            argv = a[0] if a else kw.get("args")
            # Only the sleeper's own spawns count: close() may run taskkill
            # through subprocess too (the no-Job fallback), and that is not
            # the spawn under test.
            ours = isinstance(argv, (list, tuple)) and list(argv[:3]) == ECHO[:3]
            if ours:
                spy.flags.append(int(kw.get("creationflags", 0) or 0))
                spy.order.append("popen")
            super().__init__(*a, **kw)
            if ours:
                spy.children.append(self)

    monkeypatch.setattr(client_mod.subprocess, "Popen", SpyPopen)

    # Record, then do the real thing: a real kill-on-close Job, a real resume.
    # Faking them is what left a suspended child behind (see the docstring).
    def spy_job_for(handle: int):
        spy.order.append("assign")
        return real_job_for(handle)

    def spy_resume(handle: int) -> bool:
        spy.order.append("resume")
        if not resume_ok:
            # A failed resume, simulated by NOT resuming: the child is still
            # suspended and the client must kill it before its fallback.
            return False
        return real_resume(handle)

    monkeypatch.setattr(backend_mod, "_win_job_for", spy_job_for)
    monkeypatch.setattr(procs_mod, "_resume", spy_resume)
    return spy


def _left_behind(children: list[subprocess.Popen]) -> dict[int, str]:
    """``{pid: status}`` of every started child still alive or SUSPENDED.

    Read by process id through psutil (a suspended Windows process reads
    ``stopped``), waiting up to :data:`GONE_WITHIN_S` for a kill that is still
    landing. Empty means nothing was left behind.
    """
    deadline = time.monotonic() + GONE_WITHIN_S
    while True:
        left: dict[int, str] = {}
        for child in children:
            try:
                left[child.pid] = psutil.Process(child.pid).status()
            except psutil.NoSuchProcess:
                continue
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.05)


def _end(children: list[subprocess.Popen]) -> None:
    """Kill whatever a FAILING run left, so a red test does not leak too."""
    for child in children:
        try:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
        except Exception:  # noqa: BLE001 — already gone
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows Jobs only")
def test_the_server_is_created_suspended_assigned_then_resumed(monkeypatch):
    spy = _patch(monkeypatch, _Spy())
    t = client_mod.StdioTransport(ECHO[0], ECHO[1:], request_timeout=HANDSHAKE_S)
    try:
        with pytest.raises(Exception):
            # The handshake fails (the child is a sleeper, not a server) —
            # the spawn itself is what this test watches.
            t._ensure_started()
    finally:
        t.close()
        _end(spy.children)
    assert spy.flags and spy.flags[0] & CREATE_SUSPENDED, "the launcher was not born suspended"
    # The Job is assigned BEFORE the child runs an instruction.
    assert spy.order[:3] == ["popen", "assign", "resume"], spy.order


@pytest.mark.skipif(os.name != "nt", reason="Windows Jobs only")
def test_a_closed_server_leaves_no_suspended_or_live_child_by_pid(monkeypatch):
    """The v1.383.1 leak, pinned: start, fail the handshake, close — and every
    process the client started is gone, checked by its pid."""
    spy = _patch(monkeypatch, _Spy())
    t = client_mod.StdioTransport(ECHO[0], ECHO[1:], request_timeout=HANDSHAKE_S)
    try:
        try:
            with pytest.raises(Exception):
                t._ensure_started()
        finally:
            t.close()
        assert spy.children, "the sleeper was never started"
        left = _left_behind(spy.children)
        assert not left, f"close() left these children behind (pid: status): {left}"
    finally:
        _end(spy.children)


@pytest.mark.skipif(os.name != "nt", reason="Windows Jobs only")
def test_a_failed_resume_kills_the_suspended_child_and_starts_it_plainly(monkeypatch):
    spy = _patch(monkeypatch, _Spy(), resume_ok=False)
    t = client_mod.StdioTransport(ECHO[0], ECHO[1:], request_timeout=HANDSHAKE_S)
    try:
        try:
            with pytest.raises(Exception):
                t._ensure_started()
            # Two spawns: the suspended one (killed) and the plain fallback.
            assert len(spy.flags) == 2
            assert spy.flags[0] & CREATE_SUSPENDED
            assert not (spy.flags[1] & CREATE_SUSPENDED)
            assert t._job is None, "a plain fallback must not keep a Job it never assigned"
        finally:
            t.close()
        # Neither the never-resumed child nor the plain fallback survives.
        left = _left_behind(spy.children)
        assert not left, f"these children were left behind (pid: status): {left}"
    finally:
        _end(spy.children)


@pytest.mark.skipif(os.name == "nt", reason="POSIX: no Job, never suspended")
def test_posix_spawn_is_unchanged(monkeypatch):
    spy = _patch(monkeypatch, _Spy())
    t = client_mod.StdioTransport(ECHO[0], ECHO[1:], request_timeout=HANDSHAKE_S)
    try:
        with pytest.raises(Exception):
            t._ensure_started()
    finally:
        t.close()
    assert spy.flags == [0]
