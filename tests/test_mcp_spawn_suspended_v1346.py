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
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from iron_jarvis.core import procs as procs_mod
from iron_jarvis.core.procs import CREATE_SUSPENDED
from iron_jarvis.mcp import client as client_mod
from iron_jarvis.terminals import backend as backend_mod

ECHO = [sys.executable, "-c", "import sys, time; time.sleep(30)"]
# The child is a sleeper, not a server: the handshake must give up quickly
# (MCPServerStopped) — the spawn itself is what these tests watch.
HANDSHAKE_S = 1.0


class _Spy:
    def __init__(self) -> None:
        self.flags: list[int] = []
        self.order: list[str] = []


def _patch(monkeypatch, spy: _Spy, *, resume_ok: bool = True):
    real_popen = subprocess.Popen

    class SpyPopen(real_popen):  # type: ignore[misc]
        def __init__(self, *a, **kw):
            argv = a[0] if a else kw.get("args")
            # Only the sleeper's own spawns count: close() may run taskkill
            # through subprocess too (the no-Job fallback), and that is not
            # the spawn under test.
            if isinstance(argv, (list, tuple)) and list(argv[:3]) == ECHO[:3]:
                spy.flags.append(int(kw.get("creationflags", 0) or 0))
                spy.order.append("popen")
            super().__init__(*a, **kw)

    monkeypatch.setattr(client_mod.subprocess, "Popen", SpyPopen)

    def fake_job_for(handle: int):
        spy.order.append("assign")
        return 4242  # a pretend Job handle

    def fake_resume(handle: int) -> bool:
        spy.order.append("resume")
        return resume_ok

    monkeypatch.setattr(backend_mod, "_win_job_for", fake_job_for)
    monkeypatch.setattr(backend_mod, "_win_terminate_job", lambda job: True)
    monkeypatch.setattr(procs_mod, "_resume", fake_resume)
    return spy


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
    assert spy.flags and spy.flags[0] & CREATE_SUSPENDED, "the launcher was not born suspended"
    # The Job is assigned BEFORE the child runs an instruction.
    assert spy.order[:3] == ["popen", "assign", "resume"], spy.order


@pytest.mark.skipif(os.name != "nt", reason="Windows Jobs only")
def test_a_failed_resume_kills_the_suspended_child_and_starts_it_plainly(monkeypatch):
    spy = _patch(monkeypatch, _Spy(), resume_ok=False)
    t = client_mod.StdioTransport(ECHO[0], ECHO[1:], request_timeout=HANDSHAKE_S)
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
