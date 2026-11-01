"""v1.418.1 review: what the give-up must still keep.

Two defects the independent review found in the first cut:

* SHUTDOWN. ``kill_all`` sets ``_closing`` under ``_lock``, RELEASES it, and
  only then writes the shutdown snapshot (which reads ``_restoring`` and
  ``_restore_failed`` under ``_lock`` again). A late shell that started in
  that gap went through ``_finish_late_restore``, which POPPED the pane from
  both tables before closing its shell — so the shutdown snapshot no longer
  carried the pane and the next start had nothing to reopen. The v1.407.0
  promise ("its snapshot entry is kept below as read") broke for every pane
  that was still reopening, given up, or refused behind a stuck shell when
  the app closed.
* ORDER. A pane given up later (the one holding the Store-shell lock, at the
  minute) landed AFTER the ones refused behind it (at 15 s), so the saved
  index and ``restore_failed.ids`` reordered the user's panes; the next start
  restores (and the rail shows) them in that new order.

Same stand-ins as ``test_restore_give_up_v14181``: a backend whose ``start``
blocks on an Event the test controls, time through ``TerminalManager._now``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend, ShellStartStuck

from .test_restore_give_up_v14181 import (
    STUCK,
    _Gate,
    _manager,
    _past_give_up,
    _saved_ids,
    _until,
)


def _finish_seen(m: TerminalManager) -> threading.Event:
    """An Event set each time a late restore has FINISHED (returned)."""
    done = threading.Event()
    real = m._finish_late_restore

    def wrapped(*a, **kw):
        try:
            return real(*a, **kw)
        finally:
            done.set()

    m._finish_late_restore = wrapped  # type: ignore[method-assign]
    # A late finisher that persists waits on the snapshot lock the shutdown
    # holds: by then it has done everything it does to the tables.
    real_persist = m._persist

    def persist(*a, **kw):
        done.set()
        return real_persist(*a, **kw)

    m._persist = persist  # type: ignore[method-assign]
    return done


def _written_by_the_shutdown(path: Path) -> None:
    """The shutdown snapshot (inline) really wrote the file — never a pass
    read off the boot's own snapshot."""
    saved = json.loads(path.read_text(encoding="utf-8"))["terminals"]
    assert "scrollback_b64" in saved[0], "the shutdown snapshot did not write"


def _late_lands_between_closing_and_the_shutdown_snapshot(m, gate, done) -> None:
    """Drive the REAL kill_all, with the late shell starting in the gap after
    ``_closing`` is set and before the shutdown snapshot reads the tables."""
    real_snapshot = m.snapshot

    def snapshot(*a, **kw):
        if kw.get("inline"):
            gate.release.set()
            assert done.wait(15), "the late restore never finished"
        return real_snapshot(*a, **kw)

    m.snapshot = snapshot  # type: ignore[method-assign]
    m.kill_all()


def test_a_given_up_pane_whose_shell_starts_during_shutdown_stays_saved(tmp_path, monkeypatch):
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", STUCK])
    ok_id, stuck_id = (e["id"] for e in entries)
    try:
        assert m.rehydrate() == 1
        _past_give_up(clock)
        assert m.restore_failed()["ids"] == [stuck_id]
        done = _finish_seen(m)
        _late_lands_between_closing_and_the_shutdown_snapshot(m, gate, done)
        # The late shell was closed (shutdown began), never registered…
        assert m.get(stuck_id) is None
        # …and the pane is still in the shutdown snapshot.
        _written_by_the_shutdown(m.state_path)
        assert _saved_ids(m.state_path) == [ok_id, stuck_id]
    finally:
        gate.release.set()


def test_a_pane_still_reopening_whose_shell_starts_during_shutdown_stays_saved(
    tmp_path, monkeypatch
):
    """The same gap before the give-up (the v1.407.0 promise itself)."""
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", STUCK])
    ok_id, stuck_id = (e["id"] for e in entries)
    try:
        assert m.rehydrate() == 1
        assert m.restoring_ids() == [stuck_id]
        done = _finish_seen(m)
        _late_lands_between_closing_and_the_shutdown_snapshot(m, gate, done)
        assert m.get(stuck_id) is None
        _written_by_the_shutdown(m.state_path)
        assert _saved_ids(m.state_path) == [ok_id, stuck_id]
    finally:
        gate.release.set()


def test_a_late_refusal_behind_a_stuck_shell_during_shutdown_stays_saved(tmp_path, monkeypatch):
    gate = _Gate()

    class _LateBusy(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            if argv and argv[0] == STUCK:
                gate.entered.set()
                gate.release.wait(60)
                raise ShellStartStuck("PowerShell is still starting another terminal")
            return super().start(argv, cwd, env, cols, rows)

    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", STUCK])
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: _LateBusy())
    ok_id, stuck_id = (e["id"] for e in entries)
    try:
        assert m.rehydrate() == 1
        done = _finish_seen(m)
        _late_lands_between_closing_and_the_shutdown_snapshot(m, gate, done)
        _written_by_the_shutdown(m.state_path)
        assert _saved_ids(m.state_path) == [ok_id, stuck_id]
    finally:
        gate.release.set()


def test_panes_given_up_at_different_times_keep_the_users_order(tmp_path, monkeypatch):
    """The first Store shell holds the lock (given up at the minute); the two
    behind it are refused at 15 s. Saved order and the note's ids stay the
    user's order, not the order they were given up in."""
    release = threading.Event()
    refuse = threading.Event()

    class _Store(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            if argv and argv[0] == "a":  # the one Windows never returns
                release.wait(60)
            else:  # behind it: refused once the bound passes
                refuse.wait(60)
                raise ShellStartStuck("PowerShell is still starting another terminal")
            return super().start(argv, cwd, env, cols, rows)

    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["a", "b", "c"])
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: _Store())
    ids = [e["id"] for e in entries]
    try:
        m.rehydrate()
        assert m.restoring_ids() == ids
        refuse.set()  # b and c are refused now, after the boot's wait
        assert _until(lambda: len(m._restore_failed) == 2)
        _past_give_up(clock)  # and a at the minute
        note = m.restore_failed()
        assert note is not None
        assert note["ids"] == ids
        m.snapshot()
        assert _saved_ids(m.state_path) == ids
    finally:
        release.set()
        refuse.set()
        m.kill_all()
