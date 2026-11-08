"""AN review of Wave D lane D2 (v1.449.0) — each pin was red on af303355.

  * ``POST /build/git/init`` writes into the user's folder, so: a refusal after
    ``git init`` takes the new ``.git`` back (an empty folder used to keep an
    empty repository while the words said "no repository was made"); the top
    of a drive and the home folder are refused; a folder inside another
    repository — even one git cannot read — is refused, never nested; a
    folder past the file or size bound is refused; no hook runs (the user's
    ``core.hooksPath``); the user's own git config is never written.
  * A watcher's path on a network location is refused by its TEXT before the
    policy check or the scanner stats it (an SMB connection), at add and edit.
  * A workflow tool step naming an extension's tool is not refused while its
    pack is still starting (or failed): the tool is "not known yet"; it is
    refused when the pack is running and has no such tool.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B


@pytest.fixture()
def folder(tmp_path) -> Path:
    f = tmp_path / "shop"
    f.mkdir()
    (f / "app.py").write_bytes(b"x = 1\n")
    return f


def test_a_refused_first_commit_takes_the_new_repository_back(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(str(empty))
    assert str(exc.value) == B.INIT_NEEDS_FILES
    assert not (empty / ".git").exists()
    # The next press is a fresh try, not "already a repository".
    (empty / "a.txt").write_bytes(b"a\n")
    assert B.init_repo(str(empty))["made"] is True


def test_the_top_of_a_drive_and_the_home_folder_are_refused(folder, monkeypatch):
    from iron_jarvis.core import fs_policy

    monkeypatch.setattr(fs_policy, "root_problem", lambda *_a, **_k: None)
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(Path(folder).anchor)
    assert str(exc.value) == B.INIT_TOP_FOLDER
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(str(folder))
    assert str(exc.value) == B.INIT_TOP_FOLDER
    assert not (folder / ".git").exists()


def test_a_folder_inside_a_repository_git_cannot_read_is_never_nested(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "sub"
    inner.mkdir(parents=True)
    (inner / "a.txt").write_bytes(b"a\n")
    # A broken ``.git`` git refuses to read (inspect says "not a repository").
    (outer / ".git").write_bytes(b"gitdir: nowhere-at-all\n")
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(str(inner))
    assert str(exc.value) == B.INIT_INSIDE_REPO
    assert not (inner / ".git").exists()


def test_a_folder_past_the_bounds_is_refused_and_nothing_is_kept(folder, monkeypatch):
    (folder / "b.py").write_bytes(b"y = 2\n")
    monkeypatch.setattr(B, "MAX_INIT_FILES", 1)
    with pytest.raises(B.GitRefused) as exc:
        B.init_repo(str(folder))
    assert "too much for a first commit" in str(exc.value)
    assert not (folder / ".git").exists()
    monkeypatch.setattr(B, "MAX_INIT_FILES", 10_000)
    monkeypatch.setattr(B, "MAX_INIT_BYTES", 3)
    with pytest.raises(B.GitRefused):
        B.init_repo(str(folder))
    assert not (folder / ".git").exists()


def test_no_hook_runs_and_the_users_git_config_is_never_written(folder, tmp_path, monkeypatch):
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    for hook in ("pre-commit", "post-commit", "commit-msg"):
        (hooks / hook).write_bytes(
            b"#!/bin/sh\necho ran > '" + marker.as_posix().encode() + b"'\n"
        )
    cfg = tmp_path / "global.gitconfig"
    cfg.write_bytes(f"[core]\n\thooksPath = {hooks.as_posix()}\n".encode())
    before = cfg.read_bytes()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    got = B.init_repo(str(folder))
    assert got["made"] is True
    assert not marker.exists(), "a hook ran on the first commit"
    assert cfg.read_bytes() == before
    # No identity was written into the new repository's own config either.
    local = subprocess.run(["git", "config", "--local", "--list"], cwd=str(folder),
                           capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": str(cfg)})
    assert b"user." not in local.stdout


def test_a_network_watch_path_is_refused_by_text_before_any_stat(tmp_path, monkeypatch):
    from iron_jarvis.sentinels import service as S

    with TestClient(create_app(str(tmp_path / "home"))) as client:
        svc = client.app.state.platform.sentinels

        def never(*_a, **_k):
            raise AssertionError("a network path was looked at")

        monkeypatch.setattr(S, "is_protected_path", never)
        monkeypatch.setattr(svc, "scanner", never)
        for path in (r"\\host\share\in", "//host/share/in"):
            with pytest.raises(ValueError) as exc:
                svc.add("w", kind="file", path=path, task="t")
            assert str(exc.value) == S.NETWORK_WATCH
        monkeypatch.undo()
        watched = tmp_path / "in"
        watched.mkdir()
        assert client.post("/sentinels", json={"name": "w", "kind": "file", "path": str(watched),
                                               "task": "t"}).status_code == 200
        monkeypatch.setattr(S, "is_protected_path", never)
        monkeypatch.setattr(svc, "scanner", never)
        resp = client.patch("/sentinels/w", json={"path": r"\\host\share\in"})
        assert resp.status_code == 400 and resp.json()["detail"] == S.NETWORK_WATCH
        monkeypatch.undo()
        assert client.get("/sentinels").json()


def test_an_extension_tool_of_a_starting_pack_is_not_refused(tmp_path, monkeypatch):
    from iron_jarvis.mcp import tools as T

    with TestClient(create_app(str(tmp_path))) as client:
        step = {"name": "Pages", "kind": "tool", "tool": "mcp__wiki__list_pages", "args": {}}
        monkeypatch.setitem(T._LOAD_STATUS, "wiki", {"state": T.STATE_STARTING})
        ok = client.post("/workflows", json={"name": "wf", "steps": [step]})
        assert ok.status_code == 200, ok.text
        monkeypatch.setitem(T._LOAD_STATUS, "wiki", {"state": T.STATE_FAILED, "last_error": "x"})
        assert client.post("/workflows", json={"name": "wf2", "steps": [step]}).status_code == 200
        # A running pack that has no such tool: refused by name.
        monkeypatch.setitem(T._LOAD_STATUS, "wiki", {"state": T.STATE_OK, "last_error": None})
        no = client.post("/workflows", json={"name": "wf3", "steps": [step]})
        assert no.status_code == 422 and "no tool named 'mcp__wiki__list_pages'" in no.json()["detail"]
