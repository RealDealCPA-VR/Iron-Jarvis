"""A listing of files names the folders that are empty (v1.490.3).

Seen live: asked to "move all the PDFs from Downloads into the Facturen 2026
folder" (a folder that was there, and empty), Jarvis listed the files, did not
see the folder in the listing and told the user it did not exist. A listing
that leaves a folder out reads as "not there", so `list_files` now says which
folders it found with nothing in them.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.tools import builtins as B
from iron_jarvis.tools.base import ToolContext


@pytest.fixture()
def desk(tmp_path):
    platform = build_platform(tmp_path / "home")
    ws = tmp_path / "work"
    (ws / "Downloads").mkdir(parents=True)
    (ws / "Downloads" / "bill.pdf").write_bytes(b"x")
    (ws / "Facturen 2026").mkdir()
    return platform, ws


def _list(platform, ws: Path, **args):
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="x", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    return asyncio.run(platform.registry.get("list_files").execute(args, ctx))


def test_an_empty_folder_is_named(desk):
    platform, ws = desk
    r = _list(platform, ws)
    assert r.ok
    assert r.output.splitlines()[0] == "Downloads/bill.pdf"
    assert r.output.endswith("[empty folders, nothing in them: Facturen 2026/]")
    assert r.data == {"count": 1, "truncated": False, "empty_folders": ["Facturen 2026/"]}


def test_with_no_empty_folder_the_listing_is_as_it_was(desk):
    platform, ws = desk
    (ws / "Facturen 2026" / "inv.pdf").write_bytes(b"x")
    r = _list(platform, ws)
    assert r.output == "Downloads/bill.pdf\nFacturen 2026/inv.pdf"
    assert r.data["empty_folders"] == []


def test_a_folder_of_only_empty_folders_says_so(tmp_path):
    platform = build_platform(tmp_path / "home")
    ws = tmp_path / "work"
    (ws / "a" / "b").mkdir(parents=True)
    r = _list(platform, ws)
    # "a" holds a folder, so it is not empty; "a/b" is.
    assert r.output == "[empty folders, nothing in them: a/b/]"
    # The folder that was asked about is never called empty by its own listing.
    assert _list(platform, ws, path="a/b").output == ""


def test_hidden_and_heavy_folders_are_never_named(desk):
    platform, ws = desk
    (ws / ".cache").mkdir()
    (ws / "node_modules").mkdir()
    (ws / "app" / "node_modules").mkdir(parents=True)  # "app" holds a folder: not empty
    r = _list(platform, ws)
    assert r.data["empty_folders"] == ["Facturen 2026/"]


def test_the_list_of_empty_folders_is_bounded_and_says_so(desk):
    platform, ws = desk
    for i in range(B._MAX_EMPTY_DIRS + 5):
        (ws / f"e{i:03d}").mkdir()
    r = _list(platform, ws)
    assert len(r.data["empty_folders"]) == B._MAX_EMPTY_DIRS
    assert r.output.endswith("(and possibly more)]")


def test_other_callers_of_the_walk_are_unchanged(desk):
    """Only a caller that asks for the empty folders gets them: the walk's
    answer keeps its two parts."""
    _platform, ws = desk
    paths, truncated = B._walk_files(ws, limit=100)
    assert [p.name for p in paths] == ["bill.pdf"] and truncated == ""
