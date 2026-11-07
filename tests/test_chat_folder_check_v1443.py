"""v1.443.0 — the chat's folder chip asks whether a picked folder can be used.

The user: "there is an option to select a project, but what if i dont want it
to be a specific project - rather just work in a specific folder and that
selection process should be exactly as the folder is selected in build > code."

The chat now has Build › Code's folder chip (Windows' own folder picker, the
in-app tree in a browser). Right after a pick it asks ``POST
/documents/folder-check`` so a folder the chat cannot work in is SAID under the
chip instead of failing at the first file. This file pins the daemon half:

* the answer is ``fs_policy.root_problem`` (the ONE definition the chat lanes'
  ``usable_workspace_root`` reads), as a sentence, or ``None``;
* the check runs OFF the event loop (a writability probe creates a file);
* a network spelling is judged on its TEXT and never looked at (a stat of a
  share is a network connection: the v1.379.0 lesson);
* a check that raises is a sentence, never a 500;
* the dashboard really calls the route (``tests/test_wave6_dead_code_v14390``
  holds every route to a caller; this pins the caller's spelling too).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy
from iron_jarvis.daemon.app import create_app

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "root")))


def _check(client, path: str) -> dict:
    res = client.post("/documents/folder-check", json={"path": path})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_folder_the_chat_can_work_in_has_no_problem(client, tmp_path):
    folder = tmp_path / "Books"
    folder.mkdir()
    assert _check(client, str(folder)) == {"problem": None, "checked": True}


def test_a_missing_folder_is_said_as_a_sentence(client, tmp_path):
    gone = tmp_path / "Nowhere"
    got = _check(client, str(gone))
    assert got["checked"] is True
    assert got["problem"] == f"Folder does not exist on this machine: {gone}"


def test_a_relative_path_is_refused(client):
    got = _check(client, "Books")
    assert got["problem"] == "Folder must be an absolute path"


def test_the_answer_is_root_problems_own_words(client, tmp_path, monkeypatch):
    """One definition: the route says what root_problem says (capitalised)."""
    folder = tmp_path / "Locked"
    folder.mkdir()
    monkeypatch.setattr(
        fs_policy, "root_problem", lambda p, **_: f"folder is not writable by this app: {p}"
    )
    got = _check(client, str(folder))
    assert got["problem"] == f"Folder is not writable by this app: {folder}"


def test_iron_jarvis_own_protected_folder_is_refused(client):
    home = client.app.state.platform.config.home
    secrets = home / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    assert fs_policy.is_protected_path(str(secrets))  # the precondition, not the claim
    got = _check(client, str(secrets))
    assert got["problem"] and got["problem"].startswith("Folder is protected")


def test_the_check_runs_off_the_event_loop(client, tmp_path, monkeypatch):
    folder = tmp_path / "Books"
    folder.mkdir()
    seen: list[bool] = []

    def fake(path, **_):
        try:
            asyncio.get_running_loop()
            seen.append(True)  # a loop is running HERE: the check blocked it
        except RuntimeError:
            seen.append(False)
        return None

    monkeypatch.setattr(fs_policy, "root_problem", fake)
    assert _check(client, str(folder))["problem"] is None
    assert seen == [False]


@pytest.mark.parametrize(
    "spelled",
    [r"\\fileserver\clients", "//fileserver/clients", r"\\?\UNC\fileserver\clients", r"\\.\pipe\x"],
)
def test_a_network_spelling_is_never_looked_at(client, monkeypatch, spelled):
    calls: list[str] = []
    monkeypatch.setattr(fs_policy, "root_problem", lambda p, **_: calls.append(p) or None)
    assert _check(client, spelled) == {"problem": None, "checked": False}
    assert calls == []


def test_a_check_that_raises_is_a_sentence_not_a_500(client, tmp_path, monkeypatch):
    def boom(path, **_):
        raise PermissionError("denied")

    monkeypatch.setattr(fs_policy, "root_problem", boom)
    got = _check(client, str(tmp_path))
    assert got["problem"] == "That folder could not be read: PermissionError"


def test_an_over_long_path_is_refused_by_the_body_bound(client):
    res = client.post("/documents/folder-check", json={"path": "C:\\" + "a" * 5000})
    assert res.status_code == 422


def test_the_chat_page_calls_the_route():
    src = (REPO / "dashboard" / "lib" / "chatFolder.ts").read_text(encoding="utf-8")
    assert '"/documents/folder-check"' in src
