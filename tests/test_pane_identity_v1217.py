"""A pane's identity is REAL: it reaches the shell, and it survives (v1.217.0).

The pane-state classifier (`tests/test_build_panes_v1217.py`) proves what a
pane's output MEANS. This file proves the other half of the same wave -- that a
pane has a name and an identity a caller can actually set, that the identity
lands in the process environment rather than in a dict nobody reads, and that
none of it evaporates on the next daemon restart.

It exists because the first cut of this wave shipped the identity as prose. The
manager set `session.pane_env_extra` AFTER spawning the shell, with a comment
claiming the values were "applied to anything the pane starts next" -- and
nothing applied them. `pane_env()` had no caller anywhere in the codebase, so
IRONJARVIS_BUILD reached no shell, no CLI, and no skill. Every unit test was
green, because every unit test asked the dict what it held rather than asking
the pane what it exported. So these tests read the ENVIRONMENT THE BACKEND WAS
HANDED, which is the only place the answer is load-bearing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend


class _RecordingBackend(FakeBackend):
    """A FakeBackend that keeps the environment it was started with."""

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture(autouse=True)
def _fake_default_backend(monkeypatch):
    """restore()/rehydrate() spawn through `default_backend` -- no real shells."""
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: _RecordingBackend()
    )


# --------------------------------------------------------------------------- #
# The identity reaches the process
# --------------------------------------------------------------------------- #
def test_the_shell_is_started_with_the_pane_identity(tmp_path):
    """The whole point: a CLI launched in here can tell it is inside Build.

    Asserted against the env the BACKEND received, not against
    `session.pane_env()`. The bug this replaces passed a `pane_env()` check
    while handing the shell nothing.
    """
    backend = _RecordingBackend()
    m = TerminalManager()
    s = m.create(cwd=str(tmp_path), backend=backend, name="builder", agent_cli="claude")

    assert backend.env is not None, "the shell must be given an environment"
    assert backend.env["IRONJARVIS_BUILD"] == "1"
    assert backend.env["IRONJARVIS_PANE_ID"] == s.id
    assert backend.env["IRONJARVIS_PANE_CWD"] == str(tmp_path)
    assert backend.env["IRONJARVIS_PANE_NAME"] == "builder"
    assert backend.env["IRONJARVIS_PANE_CLI"] == "claude"


def test_the_pane_id_in_the_environment_is_the_id_the_api_returns(tmp_path):
    """An agent reads IRONJARVIS_PANE_ID and addresses that pane by it.

    The id therefore has to be minted BEFORE the spawn. Handing the shell one
    id and the API another would make the variable worse than absent -- it
    would point at a pane that does not exist.
    """
    backend = _RecordingBackend()
    m = TerminalManager()
    s = m.create(cwd=str(tmp_path), backend=backend)
    assert backend.env["IRONJARVIS_PANE_ID"] == s.id
    assert m.get(s.id) is s


def test_the_shell_keeps_the_rest_of_its_environment(tmp_path):
    """The identity is ADDED, never substituted for the environment.

    The backends replace the child environment outright when handed one, so a
    merge that returned only the pane variables would spawn a shell with no
    PATH -- every command in it failing for a reason no one would connect to a
    naming feature.
    """
    backend = _RecordingBackend()
    m = TerminalManager()
    m.create(cwd=str(tmp_path), backend=backend, env={"PATH": "/somewhere", "X": "1"})
    assert backend.env["PATH"] == "/somewhere"
    assert backend.env["X"] == "1"
    assert backend.env["IRONJARVIS_BUILD"] == "1"


def test_an_unnamed_pane_exports_no_empty_name(tmp_path):
    """Absent beats empty: a skill testing the variable gets a clean answer."""
    backend = _RecordingBackend()
    m = TerminalManager()
    m.create(cwd=str(tmp_path), backend=backend)
    assert "IRONJARVIS_PANE_NAME" not in backend.env
    assert "IRONJARVIS_PANE_CLI" not in backend.env
    assert backend.env["IRONJARVIS_BUILD"] == "1"


# --------------------------------------------------------------------------- #
# The identity survives a restart
# --------------------------------------------------------------------------- #
def test_a_named_pane_is_still_named_after_a_daemon_restart(tmp_path):
    """A rename that lasts until the next update is not a name.

    Agents address panes BY NAME, and a daemon restart is exactly when an
    agent is most likely to be looking for the pane it was told about.
    """
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), name="builder", agent_cli="claude")
    m1.snapshot()

    m2 = TerminalManager(state_path=sp)  # the daemon after a restart
    assert m2.rehydrate() == 1
    restored = m2.get(s.id)
    assert restored is not None
    assert restored.pane_name == "builder"
    assert restored.agent_cli == "claude"


def test_a_restored_pane_exports_its_identity_too(tmp_path):
    """The restored shell is a NEW process, so it needs the variables again.

    Restoring the name into the dataclass but spawning the replacement shell
    without the environment would leave a pane that LOOKS named in the UI and
    is anonymous to anything running inside it.
    """
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), name="tester", agent_cli="codex")
    m1.snapshot()

    m2 = TerminalManager(state_path=sp)
    m2.rehydrate()
    restored = m2.get(s.id)
    env = restored.backend.env
    assert env["IRONJARVIS_BUILD"] == "1"
    assert env["IRONJARVIS_PANE_ID"] == s.id
    assert env["IRONJARVIS_PANE_NAME"] == "tester"
    assert env["IRONJARVIS_PANE_CLI"] == "codex"


# --------------------------------------------------------------------------- #
# PATCH /terminals/{id} — the way in from the product
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: _RecordingBackend()
    )
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        yield c


def _make(client, **body):
    r = client.post("/terminals", json={"cwd": None, **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_pane_can_be_renamed_over_the_api(client):
    """Without this route the `name` field is settable only at creation -- and
    the Build page's New terminal button does not ask for one, so no user could
    ever name a pane."""
    pane = _make(client)
    assert pane["name"] is None

    r = client.patch(f"/terminals/{pane['id']}", json={"name": "builder"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "builder"

    listed = client.get("/terminals/activity").json()["panes"]
    assert [p["name"] for p in listed if p["id"] == pane["id"]] == ["builder"]


def test_recording_a_launch_does_not_erase_the_name(client):
    """PARTIAL, not a re-POST of the create body.

    The browser types a CLI's launch command into an already-running shell, so
    it reports `agent_cli` in a SEPARATE call from the rename -- and the remote
    agent registry already proved what a re-post does to the fields it does not
    carry (it destroyed a credential the user could not retype).
    """
    pane = _make(client, name="builder")
    r = client.patch(f"/terminals/{pane['id']}", json={"agent_cli": "claude"})
    assert r.status_code == 200
    assert r.json()["name"] == "builder"
    assert r.json()["agent_cli"] == "claude"

    r = client.patch(f"/terminals/{pane['id']}", json={"name": "renamed"})
    assert r.json()["agent_cli"] == "claude", "a rename must not drop the CLI"


def test_an_empty_string_clears_a_field_and_omission_leaves_it(client):
    pane = _make(client, name="builder", agent_cli="claude")
    assert client.patch(f"/terminals/{pane['id']}", json={}).json()["name"] == "builder"
    cleared = client.patch(f"/terminals/{pane['id']}", json={"name": ""}).json()
    assert cleared["name"] is None
    assert cleared["agent_cli"] == "claude"


def test_a_rename_updates_what_the_pane_exports_next(client):
    """A pane renamed after launch should not keep announcing its old name to
    whatever it starts next."""
    from iron_jarvis.daemon import app as _app  # noqa: F401

    pane = _make(client, name="old")
    client.patch(f"/terminals/{pane['id']}", json={"name": "new"})
    r = client.get("/terminals").json()
    entry = next(p for p in r["terminals"] if p["id"] == pane["id"])
    assert entry["name"] == "new"


def test_patching_a_pane_that_is_gone_is_a_404_not_a_silent_ok(client):
    assert client.patch("/terminals/term_nope", json={"name": "x"}).status_code == 404
