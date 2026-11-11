"""A tool's serving programs are found where the system-wide socket read is
refused (macOS without root).

There ``psutil.net_connections`` raises AccessDenied. Every read failed, each
Build › Code session read "cannot tell", and the idle stop kept every tool
alive (ten Mac failures in the 2026-11-11 trial). The tool's own programs are
asked one by one instead.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import psutil
import pytest

from iron_jarvis.hosted import programs as P

SERVER = (
    "import socket, sys, time\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)\n"
    "print(s.getsockname()[1], flush=True)\n"
    "time.sleep(120)\n"
)


@pytest.fixture
def server():
    proc = subprocess.Popen([sys.executable, "-c", SERVER], stdout=subprocess.PIPE, text=True)
    try:
        port = int(proc.stdout.readline().strip())
        yield proc, port
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _tree(proc) -> list[int]:
    """The started program and what it started: a virtual environment's
    ``python.exe`` on Windows is a launcher whose CHILD is the real one."""
    me = psutil.Process(proc.pid)
    return [me.pid] + [c.pid for c in me.children(recursive=True)]


def _refused(*a, **k):
    raise psutil.AccessDenied()


def test_a_refused_system_read_answers_not_available(monkeypatch):
    monkeypatch.setattr(psutil, "net_connections", _refused)
    assert P._listening(psutil) is None


def test_one_programs_own_ports_are_read(server):
    proc, port = server
    assert any(port in P._listening_of(psutil, pid) for pid in _tree(proc))
    assert P._listening_of(psutil, os.getpid() + 10_000_000) == set()


def test_the_tools_own_program_is_found_without_the_system_read(server, monkeypatch):
    """This process stands in for the tool: the server is its child."""
    proc, port = server
    monkeypatch.setattr(psutil, "net_connections", _refused)
    started = psutil.Process(proc.pid).create_time() - 1
    got = P.read([("s1", os.getpid(), None, started)])["s1"]
    assert got.known, "a refused system-wide read must not make the session unknown"
    assert len(got.programs) == 1 and got.programs[0].ports == (port,)
    assert got.programs[0].pid in _tree(proc)


def test_a_session_with_nothing_serving_reads_known_and_empty(monkeypatch):
    monkeypatch.setattr(psutil, "net_connections", _refused)
    monkeypatch.setattr(P, "_members", lambda root, job: set())
    got = P.read([("s1", os.getpid(), None, time.time())])["s1"]
    assert got.known and got.programs == []
