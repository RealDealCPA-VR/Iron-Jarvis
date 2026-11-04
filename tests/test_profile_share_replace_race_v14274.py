"""v1.427.4 — a PUT /profile/share never fails because the status file was
open for a moment (the Release gate's red test, a real race).

``test_profile_share_pi_opencode_v1414::test_opencode_writes_its_xdg_agents_md``
answered 500 on the release runner: "[WinError 5] Access is denied" replacing
``profile-share.json``. The debounce timer (``ProfileShare._run``) read that
file OUTSIDE the lock every write holds, so a PUT's ``os.replace`` met a file
another thread had open — and Windows refuses to replace an open file. Two
fixes, each pinned:

* the timer reads the status file under the same lock as every write;
* a replace Windows refuses for a moment (a backup or an indexer reading the
  file) is tried again for about a second, re-checking ``expect`` each time;
  a file held longer still raises, and the temp file is removed.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.profile import share

WINDOWS = os.name == "nt"


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to replace an open file")
def test_a_replace_refused_for_a_moment_is_tried_again(tmp_path):
    path = tmp_path / "profile-share.json"
    path.write_bytes(b"old")
    held = open(path, "rb")  # noqa: SIM115 — a reader holding it, as the timer did
    release = threading.Timer(0.3, held.close)
    release.start()
    try:
        share._atomic_write(path, b"new")
    finally:
        release.join()
        held.close()
    assert path.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["profile-share.json"], "no temp file left"


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to replace an open file")
def test_a_file_held_past_the_retries_still_says_so_and_leaves_no_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(share, "REPLACE_TRIES", 3)
    monkeypatch.setattr(share, "REPLACE_WAIT_S", 0.01)
    path = tmp_path / "profile-share.json"
    path.write_bytes(b"old")
    with open(path, "rb"):
        with pytest.raises(PermissionError):
            share._atomic_write(path, b"new")
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["profile-share.json"]


def test_a_retry_checks_the_users_file_again_before_each_replace(tmp_path, monkeypatch):
    """An edit saved between two tries is never overwritten."""
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"mine")
    calls = {"n": 0}
    real_replace = os.replace

    def refuse_once_then_edit(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            Path(dst).write_bytes(b"edited meanwhile")
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(share.os, "replace", refuse_once_then_edit)
    monkeypatch.setattr(share, "REPLACE_WAIT_S", 0.0)
    with pytest.raises(share.FileChanged):
        share._atomic_write(path, b"jarvis", expect=b"mine")
    assert path.read_bytes() == b"edited meanwhile"
    assert [p.name for p in tmp_path.iterdir()] == ["AGENTS.md"]


def test_the_timer_reads_the_status_file_under_the_write_lock(tmp_path, monkeypatch):
    svc = share.ProfileShare(SimpleNamespace(home=str(tmp_path)), engine=None, register=False)
    owned: list[bool] = []

    def load():
        owned.append(svc._lock._is_owned())
        return {"files": {}}

    monkeypatch.setattr(svc, "_load", load)
    monkeypatch.setattr(svc, "is_on", lambda cli: False)
    svc._run()
    assert owned == [True], "the timer read the status file outside the lock"
