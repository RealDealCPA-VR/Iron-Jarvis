"""Second review, finding A1 (decision E2, v1.510.0): removing an account never
deletes its conversations.

``DELETE /iron-proxy/accounts/{id}`` used to ask the account manager to delete
the profile, which removes the account's home folder
(``<data dir>/cli-homes/<provider>/<id>``) for good: its sign-in and every
conversation kept on it. Now the route MOVES that folder into Iron Jarvis's own
trash first, and only then asks for the profile to be deleted; it refuses while
a coding session or a terminal runs on the account, and refuses (deleting
nothing) when the folder cannot be moved.

Driven through the REAL app factory against the in-process fake account
manager (``tests/fixtures/fake_iron_proxy_v1301.py``) and real temp folders.
``IRON_PROXY_DATA_DIR`` is this test's own folder; the real ``%APPDATA%`` and
``~/.iron-proxy`` are never touched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FakeIronProxy  # noqa: E402

CONVERSATION = "projects/my-folder/one-conversation.jsonl"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", FakeBackend)
    yield


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd").start()
    yield f
    f.stop()


@pytest.fixture
def c(proj, fake):
    with TestClient(create_app(str(proj))) as client:
        r = client.post("/iron-proxy/enable")
        assert r.status_code == 200 and r.json()["status"]["running"] is True, r.text
        yield client


def _fill(home: Path) -> None:
    """A home folder as a signed-in CLI leaves it: a sign-in file and a
    conversation (plain words, nothing shaped like a credential)."""
    (home / "projects" / "my-folder").mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text('{"note": "test-token-value"}', encoding="utf-8")
    (home / CONVERSATION).write_text('{"text": "a report"}\n', encoding="utf-8")


def _watch_deletes(fake) -> list[dict]:
    """Record every profile DELETE the account manager receives, and whether
    the account's home was still in place at that moment."""
    seen: list[dict] = []
    real = fake.handle

    def handle(method, path, headers, body):
        if method == "DELETE" and path.startswith("/iron/profiles/"):
            pid = path.rsplit("/", 1)[-1]
            prof = fake.profiles.get(pid) or {}
            home = (prof.get("cli") or {}).get("home")
            seen.append({"id": pid, "home_there": bool(home) and Path(home).exists()})
        return real(method, path, headers, body)

    fake.handle = handle  # type: ignore[method-assign]
    return seen


def _add(c, title: str = "Work") -> dict:
    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": title})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# the move
# --------------------------------------------------------------------------- #
def test_remove_moves_the_home_to_the_trash_before_the_profile_is_deleted(c, fake, proj, tmp_path):
    acct = _add(c)
    home = Path(acct["home"])
    assert home.is_relative_to(tmp_path / "ipd" / "cli-homes")
    _fill(home)
    deletes = _watch_deletes(fake)

    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 200, r.text
    # The profile delete was asked AFTER the move (the home was already gone).
    # Before v1.510.0 the account manager got the delete with the home in
    # place, and the real one removes it recursively.
    assert deletes == [{"id": acct["id"], "home_there": False}]
    body = r.json()
    assert body["ok"] is True

    # The folder is in Iron Jarvis's trash, with its files.
    moved = Path(body["trash"])
    trash_root = proj / ".ironjarvis" / "trash"
    assert moved.is_relative_to(trash_root), body
    assert (moved / CONVERSATION).read_text(encoding="utf-8") == '{"text": "a report"}\n'
    assert (moved / "settings.json").is_file()
    assert not home.exists()
    assert acct["id"] not in fake.profiles

    # The answer says where it went and how long it stays.
    words = body["words"]
    assert words.startswith('Removed "Work". Its sign-in and conversations were moved to ')
    assert str(moved) in words
    assert "Delete permanently" in words and "Settings › Maintenance" in words


def test_an_adopted_account_keeps_this_pcs_own_login(c, fake, tmp_path):
    real_home = tmp_path / "user" / ".claude"
    _fill(real_home)
    prof = fake.add_profile("anthropic", "This PC", home=str(real_home), adopted=True)
    deletes = _watch_deletes(fake)

    r = c.delete(f"/iron-proxy/accounts/{prof['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trash"] == ""
    assert (real_home / CONVERSATION).is_file() and (real_home / "settings.json").is_file()
    assert deletes == [{"id": prof["id"], "home_there": True}]
    assert "this PC's own login" in body["words"]
    assert not (tmp_path / "proj" / ".ironjarvis" / "trash").exists()


def test_a_home_outside_cli_homes_is_left_where_it_is(c, fake, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "claude-home"
    _fill(elsewhere)
    prof = fake.add_profile("anthropic", "Elsewhere", home=str(elsewhere))

    r = c.delete(f"/iron-proxy/accounts/{prof['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["trash"] == ""
    assert (elsewhere / CONVERSATION).is_file()
    assert prof["id"] not in fake.profiles


def test_a_home_shared_with_another_account_stays(c, fake, tmp_path):
    shared = tmp_path / "ipd" / "cli-homes" / "anthropic" / "shared-home"
    _fill(shared)
    one = fake.add_profile("anthropic", "One", home=str(shared))
    fake.add_profile("anthropic", "Two", home=str(shared))

    r = c.delete(f"/iron-proxy/accounts/{one['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["trash"] == ""
    assert (shared / CONVERSATION).is_file()
    assert "Another account uses the same folder" in r.json()["words"]


# --------------------------------------------------------------------------- #
# a failed move refuses
# --------------------------------------------------------------------------- #
def test_a_failed_move_keeps_the_account_and_says_what_to_close(c, fake, monkeypatch):
    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    deletes = _watch_deletes(fake)

    def in_use(src, dst):
        raise PermissionError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr("iron_jarvis.daemon.routes.iron_proxy._move_folder", in_use)
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail.startswith('Nothing was removed: a file in the folder of "Work" is in use.')
    assert "Close" in detail and "being used" not in detail
    assert deletes == []
    assert acct["id"] in fake.profiles
    assert (home / CONVERSATION).is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to move a folder holding an open file")
def test_a_file_really_open_in_the_home_refuses_on_windows(c, fake):
    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    deletes = _watch_deletes(fake)
    with open(home / CONVERSATION, "rb"):
        r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 409, r.text
    assert "is in use" in r.json()["detail"]
    assert deletes == [] and acct["id"] in fake.profiles
    assert (home / CONVERSATION).is_file()


def test_a_profile_delete_that_fails_puts_the_folder_back(c, fake):
    from fake_iron_proxy_v1301 import FakeError

    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    real = fake.handle

    def refuse(method, path, headers, body):
        if method == "DELETE":
            raise FakeError("INTERNAL", "The account manager could not delete it.", status=500)
        return real(method, path, headers, body)

    fake.handle = refuse  # type: ignore[method-assign]
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 502, r.text
    assert (home / CONVERSATION).is_file()
    assert acct["id"] in fake.profiles


# --------------------------------------------------------------------------- #
# in use: refused, named
# --------------------------------------------------------------------------- #
def test_a_running_coding_session_on_the_account_refuses(c, fake):
    from iron_jarvis.hosted.service import LiveSession, service_for

    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    deletes = _watch_deletes(fake)
    hosted = service_for(c.app.state.platform)
    live = LiveSession("s-test", "claude", None)  # type: ignore[arg-type]
    live.account = acct["id"]
    live.title = "Fix the report page"
    live.proc = SimpleNamespace(alive=True)
    hosted.live["s-test"] = live
    try:
        r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    finally:
        hosted.live.pop("s-test", None)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        'The account "Work" is in use by the coding session "Fix the report page". '
        "Close it first, then remove the account."
    )
    assert deletes == [] and (home / CONVERSATION).is_file()

    # The same session stopped: removal goes ahead.
    live.proc = SimpleNamespace(alive=False)
    hosted.live["s-test"] = live
    try:
        r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    finally:
        hosted.live.pop("s-test", None)
    assert r.status_code == 200, r.text


def test_a_terminal_on_the_account_refuses(c, fake):
    acct = _add(c)
    _fill(Path(acct["home"]))
    deletes = _watch_deletes(fake)
    terms = c.app.state.platform.terminals
    pane = terms.create(None, None, 100, 30, env={}, name="Claude on Work")
    pane.accounts = {"anthropic": {"id": acct["id"], "title": "Work", "source": "iron-proxy"}}
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        'The account "Work" is in use by the terminal "Claude on Work". '
        "Close it first, then remove the account."
    )
    assert deletes == []
    pane.kill()
    assert c.delete(f"/iron-proxy/accounts/{acct['id']}").status_code == 200


def test_an_open_sign_in_terminal_refuses_too(c, fake):
    acct = _add(c)
    deletes = _watch_deletes(fake)
    r = c.post(f"/iron-proxy/accounts/{acct['id']}/signin")
    assert r.status_code == 200, r.text
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 409, r.text
    assert 'the terminal "Sign in: Work"' in r.json()["detail"]
    assert deletes == []


def test_an_unknown_account_is_404_and_nothing_moves(c, fake):
    deletes = _watch_deletes(fake)
    r = c.delete("/iron-proxy/accounts/prof_nope")
    assert r.status_code == 404
    assert deletes == []


# --------------------------------------------------------------------------- #
# Lead review: a folder on another drive than Iron Jarvis's home
# --------------------------------------------------------------------------- #
def test_a_folder_on_another_drive_is_copied_whole_before_the_profile_is_deleted(
    c, fake, proj, monkeypatch
):
    """A rename cannot cross drives. The account must still be removable, and
    its conversations must still be kept: a full copy goes to the trash FIRST,
    and only then is the account manager asked to delete the profile."""
    import errno

    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    deletes = _watch_deletes(fake)

    def other_drive(src, dst):
        raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")

    monkeypatch.setattr("iron_jarvis.daemon.routes.iron_proxy._move_folder", other_drive)
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    kept = Path(body["trash"])
    assert kept.is_relative_to(proj / ".ironjarvis" / "trash")
    assert (kept / CONVERSATION).read_text(encoding="utf-8") == '{"text": "a report"}\n'
    assert (kept / "settings.json").is_file()
    # The delete was asked once, AFTER the copy existed (the original is the
    # account manager's to remove, so it was still in place then).
    assert deletes == [{"id": acct["id"], "home_there": True}]
    assert acct["id"] not in fake.profiles


def test_a_copy_that_fails_part_way_leaves_nothing_behind_and_removes_nothing(
    c, fake, proj, monkeypatch
):
    import errno

    acct = _add(c)
    home = Path(acct["home"])
    _fill(home)
    deletes = _watch_deletes(fake)

    def other_drive(src, dst):
        raise OSError(errno.EXDEV, "another drive")

    def half_copy(src, dst, **kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "settings.json").write_text("half", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("iron_jarvis.daemon.routes.iron_proxy._move_folder", other_drive)
    monkeypatch.setattr("iron_jarvis.daemon.routes.iron_proxy.shutil.copytree", half_copy)
    r = c.delete(f"/iron-proxy/accounts/{acct['id']}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith('Nothing was removed: the folder of "Work" could not be moved')
    assert deletes == [] and acct["id"] in fake.profiles
    assert (home / CONVERSATION).is_file()
    trash = proj / ".ironjarvis" / "trash"
    assert not [p for p in trash.rglob("settings.json")], "a half copy was left in the trash"
