"""v1.443.0: the browser's downloads are remembered, listed, and safely managed.

The user's words: when the browser pulls a file from a site, "the app should
be aware of where the file was downloaded and have the capability of working
with these files in a safe way and can rename, modify or move them".

Before: a verified ``browser.download_completed`` reached the daemon and was
kept in memory (16 at most, one connection); no tool could list it, and the
file tools are confined to the work folder, so a file in Downloads could not
be renamed or moved at all. Pinned here:

* every verified completion on the bus lands in the kept list (on disk,
  bounded, the query string of the link never kept), and an unverified one
  does not;
* ``downloads_list`` lists them, newest first, says when a file is gone;
* ``download_manage`` renames / moves / copies ONLY a file the list names,
  never overwrites, refuses a share path by its text, refuses a protected or
  unwritable destination, updates the list, and its Undo puts the file back
  (a copy's Undo moves it to the trash — never a delete), refusing when the
  file changed since;
* the tiers and the arming (ask tier, low trust denies it, chat arms it);
* the click result names it, and the route the Settings card reads.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.downloads import DownloadLedger, handler_for, site_of
from iron_jarvis.core import fs_policy
from iron_jarvis.core.config import Config
from iron_jarvis.core.events import Event
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.autoselect import select_ask_tools, select_auto_tools
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.download_tools import (
    DownloadManageTool,
    DownloadsListTool,
    free_name,
    name_problem,
)


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    ledger = DownloadLedger(home)
    cfg = Config(home=home, project_root=tmp_path)
    ctx = ToolContext(
        workspace=work, session_id="chat", agent_run_id="r1", config=cfg,
        event_bus=None, engine=None,
    )
    return home, downloads, work, ledger, ctx


def _download(ledger, downloads: Path, name="statement.pdf", body=b"%PDF-1.4 data", **extra):
    f = downloads / name
    f.write_bytes(body)
    payload = {
        "download_id": extra.pop("download_id", 7),
        "filename": str(f),
        "local_path": str(f),
        "final_url": "https://bank.example.com/stmt/oct.pdf?token=SECRET123",
        "bytes": len(body),
        "mime": "application/pdf",
        "timestamp": "2026-11-07T12:00:00+00:00",
        **extra,
    }
    return ledger.record(payload), f


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# The kept list
# --------------------------------------------------------------------------- #
def test_a_verified_completion_on_the_bus_is_kept_on_disk(world):
    home, downloads, _work, ledger, _ctx = world
    f = downloads / "a.pdf"
    f.write_bytes(b"x")
    on = handler_for(ledger)
    on(Event(type="browser.download_completed", payload={
        "download_id": 1, "local_path": str(f), "final_url": "https://irs.gov/f.pdf?k=SECRET",
    }))
    # A completion the daemon could NOT verify carries no local_path: not kept.
    on(Event(type="browser.download_completed", payload={"download_id": 2, "filename": "b.pdf"}))
    on(Event(type="browser.tab_removed", payload={"local_path": str(f)}))
    rows = DownloadLedger(home).entries()  # a fresh object reads the file
    assert [r["name"] for r in rows] == ["a.pdf"]
    raw = (home / "downloads-seen.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw  # the link's query string is never kept
    assert rows[0]["site"] == "https://irs.gov/f.pdf"


def test_the_same_download_reported_twice_is_one_entry(world):
    _home, downloads, _work, ledger, _ctx = world
    first, _ = _download(ledger, downloads)
    second, _ = _download(ledger, downloads)
    assert first["id"] == second["id"]
    assert len(ledger.entries()) == 1


def test_the_list_is_bounded_and_newest_first(world, monkeypatch):
    from iron_jarvis.browser import downloads as D

    _home, downloads, _work, ledger, _ctx = world
    monkeypatch.setattr(D, "MAX_KEPT", 3)
    for i in range(5):
        _download(ledger, downloads, name=f"f{i}.txt", download_id=i)
    names = [r["name"] for r in ledger.entries()]
    assert names == ["f4.txt", "f3.txt", "f2.txt"]


def test_site_of_never_keeps_a_query_or_a_non_web_address():
    assert site_of("https://x.com:8443/a/b?c=1#d") == "https://x.com:8443/a/b"
    assert site_of("file:///C:/x") == "" and site_of(None) == ""


def test_find_by_id_path_or_unique_name(world):
    _home, downloads, _work, ledger, _ctx = world
    e, f = _download(ledger, downloads)
    assert ledger.find(e["id"])["id"] == e["id"]
    assert ledger.find(str(f).upper())["id"] == e["id"]  # Windows folds case
    assert ledger.find("statement.pdf")["id"] == e["id"]
    assert ledger.find(str(downloads / "other.pdf")) is None


# --------------------------------------------------------------------------- #
# downloads_list
# --------------------------------------------------------------------------- #
def test_downloads_list_lists_newest_first_and_says_when_a_file_is_gone(world):
    _home, downloads, _work, ledger, ctx = world
    _download(ledger, downloads, name="old.pdf", download_id=1)
    _e, f = _download(ledger, downloads, name="new.pdf", download_id=2)
    f.unlink()
    res = _run(DownloadsListTool(ledger).execute({}, ctx))
    assert res.ok
    assert res.output.index("new.pdf") < res.output.index("old.pdf")
    assert "no longer there" in res.output
    assert "from https://bank.example.com/stmt/oct.pdf" in res.output
    only = _run(DownloadsListTool(ledger).execute({"query": "old"}, ctx))
    assert "old.pdf" in only.output and "new.pdf" not in only.output


def test_downloads_list_with_nothing_seen_says_so(world):
    *_rest, ledger, ctx = world
    res = _run(DownloadsListTool(ledger).execute({}, ctx))
    assert res.ok and "has not seen the browser download anything" in res.output


# --------------------------------------------------------------------------- #
# download_manage
# --------------------------------------------------------------------------- #
def _manage(ledger, ctx, **args):
    tool = DownloadManageTool(ledger)
    undo = _run(tool.capture_undo(args, ctx))
    res = _run(tool.execute(args, ctx))
    return tool, undo, res


def test_rename_renames_in_place_updates_the_list_and_undoes(world):
    _home, downloads, _work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    tool, undo, res = _manage(ledger, ctx, download=e["id"], action="rename", to="Oct 2026.pdf")
    assert res.ok, res.error
    assert (downloads / "Oct 2026.pdf").is_file() and not f.exists()
    assert ledger.find(e["id"])["path"] == str(downloads / "Oct 2026.pdf")
    back = _run(tool.revert(undo, ctx))
    assert back.ok, back.error
    assert f.is_file() and not (downloads / "Oct 2026.pdf").exists()
    assert ledger.find(e["id"])["path"] == str(f)


def test_rename_never_overwrites(world):
    _home, downloads, _work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    (downloads / "taken.pdf").write_bytes(b"keep me")
    _t, _u, res = _manage(ledger, ctx, download=e["id"], action="rename", to="taken.pdf")
    assert not res.ok and "already exists" in res.error
    assert (downloads / "taken.pdf").read_bytes() == b"keep me" and f.exists()


def test_rename_takes_a_name_not_a_folder(world):
    _home, downloads, _work, ledger, ctx = world
    e, _f = _download(ledger, downloads)
    _t, _u, res = _manage(ledger, ctx, download=e["id"], action="rename", to="sub/x.pdf")
    assert not res.ok and "move" in res.error


def test_a_file_the_browser_did_not_download_is_refused(world, tmp_path):
    *_rest, ledger, ctx = world
    other = tmp_path / "secret.txt"
    other.write_text("private")
    _t, undo, res = _manage(ledger, ctx, download=str(other), action="copy")
    assert undo is None
    assert not res.ok and "not a file the browser downloaded" in res.error
    assert other.read_text() == "private"


def test_a_share_path_is_refused_by_its_text(world, monkeypatch):
    _home, downloads, _work, ledger, ctx = world
    e, _f = _download(ledger, downloads)
    touched: list = []
    monkeypatch.setattr(Path, "is_dir", lambda self: touched.append(str(self)) or True)
    _t, _u, res = _manage(ledger, ctx, download=e["id"], action="move", to=r"\\evil\share")
    assert not res.ok and "network share" in res.error
    assert not any("evil" in t for t in touched)


def test_move_into_a_folder_numbers_a_taken_name_and_undoes(world, tmp_path):
    _home, downloads, _work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    dest = tmp_path / "Clients"
    dest.mkdir()
    (dest / "statement.pdf").write_bytes(b"already here")
    tool, undo, res = _manage(ledger, ctx, download=e["id"], action="move", to=str(dest))
    assert res.ok, res.error
    moved = dest / "statement (2).pdf"
    assert moved.is_file() and not f.exists()
    assert (dest / "statement.pdf").read_bytes() == b"already here"
    assert "already taken" in res.output
    assert _run(tool.revert(undo, ctx)).ok
    assert f.is_file() and not moved.exists()


def test_copy_with_no_folder_goes_to_the_work_folder_and_undo_trashes_it(world):
    home, downloads, work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    tool, undo, res = _manage(ledger, ctx, download=e["id"], action="copy")
    assert res.ok, res.error
    copy = work / "statement.pdf"
    assert copy.is_file() and f.is_file()  # the download stays as it came
    assert "work folder" in res.output
    back = _run(tool.revert(undo, ctx))
    assert back.ok and "trash" in back.output
    assert not copy.exists() and f.is_file()
    assert list((home / "trash").rglob("statement.pdf"))  # moved, never deleted


def test_undo_refuses_a_file_that_changed_since(world):
    _home, downloads, work, ledger, ctx = world
    e, _f = _download(ledger, downloads)
    tool, undo, res = _manage(ledger, ctx, download=e["id"], action="copy")
    assert res.ok
    (work / "statement.pdf").write_bytes(b"edited by the user afterwards")
    back = _run(tool.revert(undo, ctx))
    assert not back.ok and "changed or moved since" in back.error
    assert (work / "statement.pdf").read_bytes() == b"edited by the user afterwards"


def test_undo_of_a_move_refuses_when_the_old_name_is_taken_again(world, tmp_path):
    _home, downloads, _work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    dest = tmp_path / "Out"
    dest.mkdir()
    tool, undo, res = _manage(ledger, ctx, download=e["id"], action="move", to=str(dest))
    assert res.ok
    f.write_bytes(b"a new download with the same name")
    back = _run(tool.revert(undo, ctx))
    assert not back.ok and "overwrite" in back.error
    assert f.read_bytes() == b"a new download with the same name"


def test_a_protected_destination_is_refused(world, tmp_path):
    home, downloads, _work, ledger, ctx = world
    e, f = _download(ledger, downloads)
    vault = tmp_path / "vaultdir"
    vault.mkdir()
    fs_policy.register_protected_root(vault)
    try:
        _t, _u, res = _manage(ledger, ctx, download=e["id"], action="move", to=str(vault))
    finally:
        fs_policy._PROTECTED_ROOTS.discard(fs_policy._canonical(vault))
    assert not res.ok and f.exists()


def test_name_rules_and_free_name(tmp_path):
    assert name_problem("ok.pdf") is None
    for bad in ("", "a:b.pdf", "con.txt", "trailing.", "x" * 201):
        assert name_problem(bad), bad
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (2).txt").write_text("2")
    assert free_name(tmp_path, "a.txt").name == "a (3).txt"


# --------------------------------------------------------------------------- #
# Tiers, trust, arming, the click line, the route
# --------------------------------------------------------------------------- #
def test_tiers_low_trust_and_registration(tmp_path):
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.platform import build_platform

    perms = Config(home=tmp_path / "h", project_root=tmp_path).permissions
    assert perms["downloads_list"] == "allow" and perms["download_manage"] == "ask"
    assert "download_manage" in LOW_TRUST_DENY and "downloads_list" not in LOW_TRUST_DENY
    platform = build_platform(str(tmp_path))
    assert platform.registry.get("downloads_list") is not None
    assert platform.registry.get("download_manage") is not None
    assert getattr(platform.browser, "downloads", None) is not None


@pytest.mark.parametrize("text", [
    "rename the file I just downloaded to invoice-oct.pdf",
    "move my downloads into the Taxes folder",
    "copy the statement I downloaded into the project",
    "put the PDF that I downloaded from the IRS into Clients",
    "organize my downloads folder",
])
def test_chat_arms_the_list_and_asks_before_changing(text):
    assert "downloads_list" in select_auto_tools(text)
    assert select_ask_tools(text) == ["download_manage"]


@pytest.mark.parametrize("text", [
    "download Python 3.12",
    "click the download button",
    "the download link is broken",
    "can you download the report from the portal",
])
def test_the_software_sense_of_download_arms_neither(text):
    assert "downloads_list" not in select_auto_tools(text)
    assert "download_manage" not in select_ask_tools(text)


def test_the_arming_patterns_stay_linear():
    """The two patterns run on the loop for every chat message: a long run of
    words between an acting verb and a download phrase must stay fast."""
    import subprocess
    import sys

    code = (
        "import time;from iron_jarvis.tools import autoselect as A;"
        "t='rename '+'word '*20000+'my downloads';s=time.perf_counter();"
        "A._DOWNLOAD_ACT_RX.search(t);A._DOWNLOAD_TALK_RX.search('the '*50000);"
        "print(time.perf_counter()-s)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) < 5.0


def test_the_click_result_names_download_manage():
    src = Path(__file__).resolve().parents[1] / "src/iron_jarvis/browser/tools.py"
    text = src.read_text(encoding="utf-8")
    assert "move or copy it with download_manage" in text


def test_the_route_lists_what_the_browser_downloaded(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    assert client.get("/browser/downloads").json() == {"downloads": [], "total": 0}
    f = tmp_path / "x.csv"
    f.write_text("a,b")
    asyncio.run(platform.event_bus.publish(
        "browser.download_completed", {"download_id": 3, "local_path": str(f)}
    ))
    deadline = __import__("time").monotonic() + 10
    while __import__("time").monotonic() < deadline:
        body = client.get("/browser/downloads").json()
        if body["total"]:
            break
    assert body["total"] == 1 and body["downloads"][0]["name"] == "x.csv"
    assert json.dumps(body).count("exists") == 1
