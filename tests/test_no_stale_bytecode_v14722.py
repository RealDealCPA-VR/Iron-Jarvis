"""A Python command Iron Jarvis runs never reads stale bytecode.

Python trusts a ``.pyc`` whose source has the same whole-second time and the
same size. An agent that runs the tests, changes ``X = 1`` to ``X = 2`` and
runs them again inside one second got the old code: on GitHub's Linux machines
the finish check read "1 != 2" right after the fix (2026-11-11). Both
environments Iron Jarvis gives a command now say "write no bytecode".
"""

from __future__ import annotations

import os
import subprocess
import sys

from iron_jarvis.sandbox import native as N


def test_both_command_environments_write_no_bytecode():
    assert N.scrubbed_env()["PYTHONDONTWRITEBYTECODE"] == "1"
    assert N.build_env({"PATH": os.environ.get("PATH", "")})["PYTHONDONTWRITEBYTECODE"] == "1"


def test_an_edit_inside_the_same_second_is_read_at_once(tmp_path):
    """Run, edit to the same size with the same file time, run again: the
    second run must see the new value. With bytecode written (the control)
    the second run reads the old one."""
    def run(env):
        return subprocess.run(
            [sys.executable, "-c", "import app; print(app.X)"],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
        ).stdout.strip()

    def twice(env):
        src = tmp_path / "app.py"
        for old in tmp_path.glob("__pycache__/*"):
            old.unlink()
        src.write_bytes(b"X = 1\n")
        os.utime(src, (1_700_000_000, 1_700_000_000))
        first = run(env)
        src.write_bytes(b"X = 2\n")
        os.utime(src, (1_700_000_000, 1_700_000_000))
        return first, run(env)

    base = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    assert twice(base) == ("1", "1"), "the control did not reproduce the stale read"
    assert twice({**base, **N.NO_STALE_BYTECODE}) == ("1", "2")
