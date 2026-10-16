"""v1.338.0 — a restored shell started at a stale size starts again at the
pane's real size (found by the update rehearsal).

The saved size is snapshotted every 30 s, so a crash right after a resize (or
a window resized while the app was closed) brings a pane back at the wrong
size. ConPTY then draws the new prompt wrapped at the OLD width, the pane's
terminal does not wrap it, and after the attach's resize every typed key
landed over the prompt. ``TerminalSession.restart_at_size`` starts the
untouched shell again at the size the first attaching pane reports.
"""

from __future__ import annotations

import base64

from iron_jarvis.terminals import modes as M
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager
from iron_jarvis.terminals.session import TerminalSession


def _restored(tmp_path, cols=80, rows=24):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    entry = {
        "id": "term_r", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": cols, "rows": rows,
        "scrollback_b64": base64.b64encode(b"old prompt> ").decode("ascii"),
    }
    return m.restore(entry, backend=FakeBackend())


def test_an_untouched_restored_shell_starts_again_at_the_panes_size(tmp_path):
    s = _restored(tmp_path)
    old = s.backend
    try:
        assert s.restart_at_size(120, 30) is True
        assert s.backend is not old and isinstance(s.backend, FakeBackend)
        assert (s.cols, s.rows) == (120, 30)
        assert (s.backend.cols, s.backend.rows) == (120, 30)
        assert s.backend.is_alive() and not old.is_alive()
        # The pane sees the page turn BEFORE anything the new shell prints.
        assert bytes(s.scrollback_bytes()).endswith(M.fresh_page(30))
    finally:
        s.kill()


def test_the_new_shell_gets_the_same_environment(tmp_path):
    s = _restored(tmp_path)
    seen = {}

    class Recording(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            seen.update(argv=argv, cwd=cwd, env=env)
            super().start(argv, cwd, env, cols, rows)

    s.backend = Recording()
    s.backend.start(["sh"], s.cwd, None, 80, 24)
    try:
        assert s.restart_at_size(100, 40) is True
        assert seen["argv"] == ["sh"] and seen["cwd"] == s.cwd
        assert seen["env"]["IRONJARVIS_PANE_ID"] == "term_r"
    finally:
        s.kill()


def test_a_shell_someone_typed_into_is_never_restarted(tmp_path):
    s = _restored(tmp_path)
    try:
        s.write("dir\n")
        assert s.restart_at_size(120, 30) is False
        assert (s.cols, s.rows) == (80, 24)
    finally:
        s.kill()


def test_the_sessions_own_terminal_answer_is_not_typing(tmp_path):
    s = _restored(tmp_path)
    try:
        s._answer_da1()
        assert s.restart_at_size(120, 30) is True
    finally:
        s.kill()


def test_only_once_and_only_when_the_size_differs(tmp_path):
    s = _restored(tmp_path, cols=120, rows=30)
    try:
        assert s.restart_at_size(120, 30) is False  # already right
        assert s.restart_at_size(100, 30) is True
        assert s.restart_at_size(90, 30) is False  # once per pane
    finally:
        s.kill()


def test_a_pane_that_was_never_restored_is_never_restarted(tmp_path):
    s = TerminalSession(cwd=str(tmp_path), shell="sh", argv=["sh"], backend=FakeBackend()).start()
    try:
        assert s.restart_at_size(120, 30) is False
    finally:
        s.kill()


def test_a_failed_start_keeps_the_old_shell(tmp_path):
    s = _restored(tmp_path)
    old = s.backend

    class Broken(FakeBackend):
        def start(self, *a, **k):
            raise OSError("no console")

    s.backend = old  # restart builds type(old)(); make that type fail
    old.__class__ = type("Old", (FakeBackend,), {"start": Broken.start})
    try:
        assert s.restart_at_size(120, 30) is False
        assert s.backend is old and old.is_alive()
    finally:
        s.kill()
