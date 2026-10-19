"""v1.352.0, wave 2 lane D (G27): what Jarvis can reach is written down.

Settings › Permissions & ledger opens with "What Jarvis can reach". Two of
its facts live only in the daemon, so ``GET /settings/access``
(``daemon/routes/access_summary.py``) hands them over:

* ``reads`` — what ``core/fs_policy`` enforces: unrestricted on a desktop
  install (only the key/record folders are refused, and no approval level
  cards a read), or limited to ``IRONJARVIS_FS_ALLOWLIST``'s folders when an
  install sets one. The route's ``limited`` must AGREE with the gate the file
  tools really call (``fs_read_ok``) — pinned by asking the gate.
* ``writes.chat_files_dir`` — where a chat outside a project keeps its folder
  (``config.chat_files_dir``).

Driven through the REAL app factory (real middleware, real platform). Until
the integrator registers the module in ``create_app`` this file registers it
on the factory's own app the same way (``register(app, app.state.d)``); once
it is registered, that step is skipped and the shipped wiring is what runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import access_summary


def _client(tmp_path: Path) -> tuple[TestClient, object]:
    app = create_app(str(tmp_path))
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/settings/access" not in paths:
        access_summary.register(app, app.state.d)
    return TestClient(app), app.state.d


@pytest.fixture
def no_allowlist(monkeypatch):
    monkeypatch.delenv(access_summary.ALLOWLIST_ENV, raising=False)


def test_a_desktop_install_reads_anywhere_and_the_gate_agrees(tmp_path, no_allowlist):
    client, d = _client(tmp_path)
    body = client.get("/settings/access").json()
    assert body["reads"] == {"limited": False, "folders": []}
    # The gate the file tools call reads a file far outside every workspace.
    outside = tmp_path / "elsewhere" / "taxes.pdf"
    ok, why = fs_policy.fs_read_ok(outside)
    assert ok, why
    # ...and still refuses the folder holding the keys (the "never read" line).
    secrets = Path(d.platform.config.home) / "secrets" / "x.key"
    assert fs_policy.fs_read_ok(secrets)[0] is False


def test_an_allowlist_is_reported_with_its_folders_and_the_gate_agrees(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv(access_summary.ALLOWLIST_ENV, str(shared))
    client, _d = _client(tmp_path)
    body = client.get("/settings/access").json()
    assert body["reads"]["limited"] is True
    assert body["reads"]["folders"] == [str(p) for p in fs_policy.allowlist_roots()]
    assert Path(body["reads"]["folders"][0]) == shared.resolve()
    # The gate reads inside the listed folder and refuses outside it.
    assert fs_policy.fs_read_ok(shared / "a.txt")[0] is True
    assert fs_policy.fs_read_ok(tmp_path / "elsewhere.txt")[0] is False


def test_a_blank_allowlist_is_not_a_limit(tmp_path, monkeypatch):
    monkeypatch.setenv(access_summary.ALLOWLIST_ENV, "   ")
    client, _d = _client(tmp_path)
    assert client.get("/settings/access").json()["reads"]["limited"] is False
    # The gate treats a blank value the same way.
    assert fs_policy.fs_path_allowed(tmp_path / "x.txt") is True


def test_the_chat_folder_is_the_one_the_app_really_uses(tmp_path, no_allowlist):
    client, d = _client(tmp_path)
    body = client.get("/settings/access").json()
    assert body["writes"]["chat_files_dir"] == str(d.platform.config.chat_files_dir)
    # The "Chat files folder" setting moves it, and the route follows.
    d.platform.config.chat_files_root = str(tmp_path / "My chats")
    body = client.get("/settings/access").json()
    assert body["writes"]["chat_files_dir"] == str(tmp_path / "My chats")


def test_the_route_never_fails_a_settings_page(no_allowlist):
    class Broken:
        @property
        def chat_files_dir(self):
            raise OSError("no Documents folder")

    facts = access_summary.access_facts(Broken())
    assert facts == {"reads": {"limited": False, "folders": []}, "writes": {"chat_files_dir": ""}}
    # No config at all (a bare platform) is not a crash either.
    assert access_summary.access_facts(None)["writes"]["chat_files_dir"] == ""


def _strings(value):
    """Every string inside a parsed JSON answer."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_no_protected_path_rides_the_answer(tmp_path, no_allowlist):
    client, d = _client(tmp_path)
    # Compare against the PARSED answer: the raw JSON text escapes every
    # Windows backslash, so a substring check on it could never match a path
    # (review v1.352.0: a protected root planted in the answer stayed green).
    body = client.get("/settings/access").json()
    home = str(Path(d.platform.config.home))
    roots = [str(r) for r in fs_policy.protected_roots()] + [os.path.join(home, "secrets")]
    assert fs_policy.protected_roots(), "anti-vacuity: the app registered its protected roots"
    for text in _strings(body):
        for root in roots:
            assert root.lower() not in text.lower(), f"{root!r} rode the answer"
