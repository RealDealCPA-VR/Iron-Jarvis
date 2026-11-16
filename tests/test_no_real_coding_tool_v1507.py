"""v1.507.0 — the suite never starts a real coding tool.

Two tests of the hand-off undid their stand-in before their service had
stopped, and on a PC with Claude Code installed a start still in flight
launched the owner's real ``claude`` (four of them, 2026-11-16). The guard
(``tests/_real_tool_guard.py``, put in place for the whole session by
``conftest``) answers "not installed" for any real program.
"""

from __future__ import annotations

import sys
from pathlib import Path

from iron_jarvis.hosted import process as P
from iron_jarvis.hosted import service as S
from tests import _real_tool_guard as G


def test_the_service_never_finds_a_real_coding_tool_in_the_suite():
    """With no stand-in in place, every coding tool reads "not installed" or
    is a test's own program — on GitHub (nothing installed) and on a PC with
    the real tools on PATH alike. Without the guard this is red on any PC
    that has one installed."""
    assert getattr(S.binary_for, "__wrapped__", None) is not None, "the session guard is not in place"
    roots = G.safe_roots()
    for harness in P.BINARIES:
        argv = S.binary_for(harness)
        assert argv is None or not G.is_real_program(argv[0], roots), (harness, argv)


def test_a_real_program_reads_as_not_installed(tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    # No folder is a safe one here, so the file counts as an installed program.
    assert G.guard(lambda h: [str(exe)], roots=())("claude-code") is None


def test_a_stand_in_under_a_test_folder_passes(tmp_path):
    exe = tmp_path / "fake-claude.exe"
    exe.write_bytes(b"")
    assert G.guard(lambda h: [str(exe)])("claude-code") == [str(exe)]
    assert G.guard(lambda h: [str(exe)], roots=(tmp_path.resolve(),))("claude-code") == [str(exe)]


def test_a_path_that_does_not_exist_and_no_answer_pass(tmp_path):
    missing = str(tmp_path / "nowhere" / "codex.exe")
    assert G.guard(lambda h: [missing], roots=())("codex") == [missing]
    assert G.guard(lambda h: None, roots=())("codex") is None


def test_the_python_running_the_suite_is_a_real_program():
    """The check itself: this interpreter exists and is in no safe folder
    when none is given, so the guard would refuse it."""
    assert G.is_real_program(sys.executable, ())
    assert not G.is_real_program(str(Path(sys.executable).parent / "no-such-program.exe"), ())
