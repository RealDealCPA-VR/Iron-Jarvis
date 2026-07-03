"""Terminals survive a daemon restart / app update: snapshot + rehydrate."""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend


@pytest.fixture(autouse=True)
def _fake_default_backend(monkeypatch):
    # rehydrate()/restore() spawn via default_backend — make that a FakeBackend
    # so no real shells are launched in the test.
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )


def test_create_writes_a_snapshot(tmp_path):
    sp = tmp_path / "terminals.json"
    m = TerminalManager(state_path=sp)
    m.create(cwd=str(tmp_path))
    assert sp.is_file()


def test_rehydrate_restores_same_id_cwd_and_scrollback(tmp_path):
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path))
    s._tail = bytearray(b"prior session history\r\n$ ")
    m1.snapshot()

    # A brand-new manager = the daemon after a restart/update.
    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate() == 1
    restored = m2.get(s.id)
    assert restored is not None
    assert restored.id == s.id                     # same id -> UI layout matches
    assert restored.cwd == str(tmp_path)           # same directory
    assert restored.alive                          # a fresh, live shell
    assert bytes(restored.scrollback_bytes()) == b"prior session history\r\n$ "


def test_closed_terminals_do_not_come_back(tmp_path):
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    keep = m1.create(cwd=str(tmp_path))
    gone = m1.create(cwd=str(tmp_path))
    m1.kill(gone.id)  # closing persists the removal

    m2 = TerminalManager(state_path=sp)
    m2.rehydrate()
    assert m2.get(keep.id) is not None
    assert m2.get(gone.id) is None


def test_rehydrate_without_snapshot_is_noop(tmp_path):
    m = TerminalManager(state_path=tmp_path / "nope.json")
    assert m.rehydrate() == 0


def test_missing_cwd_falls_back_to_home(tmp_path):
    sp = tmp_path / "terminals.json"
    m = TerminalManager(state_path=sp)
    entry = {"id": "term_x", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path / "deleted"), "cols": 80, "rows": 24}
    restored = m.restore(entry)
    assert restored is not None
    assert restored.cwd == str(Path.home())
