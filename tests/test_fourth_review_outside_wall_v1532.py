"""Fourth review, group 24, OT1: another app is told nothing that belongs to a
client, waiting or provisional ones included.

``work_search`` (Claude desktop, Codex, Claude Code over the connector) asks
``block.work_block(outside=True)``. Its wall is ``wall.outside_and_map``: a
wall with no folder and no words, marked ``outside``, and ``may_tell``'s rule
2b refuses such an asker every row that has an owner of ANY kind (a confirmed
client, a waiting name's home, a folder under a Clients-type folder, a file
or a named segment that proves one). For an outside asker, named attribution
also knows the WAITING names, so a waiting client's work in an unowned folder
that only its title names is not told either.

The owner's own chats keep today's answers (``test_clients_wall_v1499``,
L229 and L374-375): the same calls without ``outside`` are unchanged.

Runs on the real record, the real wall and the real ``/mcp`` door. Every
name and folder is made up.
"""

# ruff: noqa: F811 — the scene fixtures are imported from test_clients_wall_v1499
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import new_id
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import keys, outside, reported
from iron_jarvis.workmap.clients import TurnWall, names, store, wall
from iron_jarvis.workmap.index import work_map
from tests.test_clients_wall_v1499 import (  # noqa: F401 — fixtures by name
    DOWNLOADS,
    JONES,
    LEE,
    _block,
    _chat,
    firm,
    scene,
    use_map,
)
from tests.test_jarvis_connector_v1528 import INSTALL, Door, _connect, _install, _key


# --------------------------------------------------------------------------- #
# The wall itself, in the firm scene
# --------------------------------------------------------------------------- #
def test_the_outside_wall_is_marked_and_fails_closed(firm, monkeypatch):
    turn, cmap = wall.outside_and_map(firm.engine)
    assert turn.outside is True and turn.ok is True and turn.owner is None and turn.folder_key == ""
    assert cmap is firm.cmap
    # An unreadable map answers no to everything.
    monkeypatch.setattr(store, "load_map", lambda engine: (_ for _ in ()).throw(RuntimeError("x")))
    closed, _cmap = wall.outside_and_map(firm.engine)
    assert closed.outside is True and closed.ok is False
    assert wall.may_tell(closed, store.EMPTY_MAP, folder=DOWNLOADS, title="Bank letter template") is False
    # The owner's own walls are not outside.
    assert TurnWall().outside is False and wall.turn_wall(firm.engine, "", "").outside is False


def test_another_app_is_never_told_a_waiting_or_clients_folder_work(firm):
    # A waiting client's work in an unowned folder, named only by its title.
    _chat(firm, "Jones invoice follow up", DOWNLOADS, hours_ago=9)
    asks = [
        dict(folder=JONES),                                 # a waiting name's home
        dict(query="supplier list clean up"),               # its work, by words
        dict(query="Jones"),                                # its name
        dict(folder=LEE),                                   # a Clients child nobody answered about
        dict(query="engagement letter draft"),
        dict(folder=DOWNLOADS),                             # an unowned folder
        dict(query="invoice follow up"),
    ]
    for ask in asks:
        told = _block(firm, outside=True, list_folder=bool(ask.get("folder")), **ask)
        for title in ("Supplier list clean up", "Engagement letter draft", "Jones invoice follow up"):
            assert title not in told, (ask, title, told)
    # Work that belongs to nobody is still told to another app.
    told = _block(firm, folder=DOWNLOADS, outside=True)
    assert "Bank letter template" in told, told


def test_the_owners_own_chats_keep_todays_answers(firm):
    _chat(firm, "Jones invoice follow up", DOWNLOADS, hours_ago=9)
    # A plain chat with no folder keeps the Phase 1 rule for a waiting name.
    assert "Supplier list clean up" in _block(firm, query="open the supplier list we cleaned up")
    # A chat in the waiting home is told its own work.
    assert "Supplier list clean up" in _block(firm, folder=JONES)
    # A chat in the Clients child is told its own work.
    assert "Engagement letter draft" in _block(firm, folder=LEE)
    # The owner's Downloads chat is told the title that names a waiting name.
    told = _block(firm, folder=DOWNLOADS)
    assert "Jones invoice follow up" in told and "Bank letter template" in told, told


def test_rule_2b_refuses_every_kind_of_owner_and_only_for_an_outside_asker(firm):
    out, cmap = wall.outside_and_map(firm.engine)
    mine = wall.turn_wall(firm.engine, "", "")
    rows = [
        dict(folder=JONES, title="x"),                              # waiting home
        dict(folder=LEE, title="x"),                                # provisional Clients child
        dict(folder=DOWNLOADS, title="Jones invoice"),              # waiting name, by title only
        dict(folder=DOWNLOADS, title="letters", files=[JONES + r"\b.pdf"]),  # a waiting home's file
    ]
    for row in rows:
        assert wall.may_tell(out, cmap, **row) is False, row
        assert wall.may_tell(mine, cmap, **row) is True, row  # the Phase 1 rule, unchanged
    assert wall.may_tell(out, cmap, folder=DOWNLOADS, title="Bank letter template") is True


def test_another_app_is_never_told_local_only_work_whatever_the_scope_says(firm, monkeypatch):
    _chat(firm, "Private budget notes", DOWNLOADS, hours_ago=9, local_only=True)
    # A turn that stays on this network may recall Local only work ...
    monkeypatch.setattr(B, "_withhold_local_only", lambda: False)
    assert "Private budget notes" in _block(firm, folder=DOWNLOADS)
    # ... another app never may, whatever scope its call carries.
    told = _block(firm, folder=DOWNLOADS, outside=True)
    assert "Private budget notes" not in told and "Bank letter template" in told, told


# --------------------------------------------------------------------------- #
# Through the real /mcp door
# --------------------------------------------------------------------------- #
@pytest.fixture()
def door_scene(tmp_path, monkeypatch):
    """``test_jarvis_connector_v1528``'s scene (the real app factory with the
    install bearer set)."""
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    other = tmp_path / "other-apps"
    monkeypatch.setattr(apps, "TEST_ROOT", str(other))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setattr(outside, "READS_DISK", True)
    monkeypatch.setattr(outside, "wall_clock", time.time)
    temp = tmp_path / "pc-temp"
    temp.mkdir()
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(outside, "SCRATCH_MARKERS", ())
    outside.forget_cache()
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "Parser"
    folder.mkdir()

    class Scene:
        pass

    s = Scene()
    s.app, s.platform = app, app.state.platform
    s.client = TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    s.engine = s.platform.engine
    s.tmp, s.folder = tmp_path, folder
    s.claude_dir = other / "Claude"
    s.wm = work_map(s.engine, s.platform.config.home)
    yield s
    outside.forget_cache()


def _door(scene) -> Door:
    _install(scene, "claude-desktop")
    assert _connect(scene, "claude-desktop").status_code == 200
    return Door(scene, _key(scene, "claude-desktop"))


def _reported(scene, folder, title: str) -> None:
    reported.record(scene.engine, "claude-desktop", {"title": title, "folders": [str(folder)],
                                                      "summary": "Did the work."})
    scene.wm.refresh(force=True)


def _waiting(scene, name: str, home) -> None:
    """A name Iron Jarvis noticed, waiting for the owner's answer."""
    from iron_jarvis.workmap.models import WorkClient

    row = WorkClient(
        id=new_id("client"), name=name, name_key=names.name_key(name), signature=names.signature(name),
        status="proposed", how="noticed", strength=9,
        evidence_json=json.dumps({"rule": "under_clients", "work": 2, "folders": 1, "spellings": [],
                                  "homes": [{"folder": str(home), "key": keys.folder_key(str(home)),
                                             "work": 2, "last_at": None, "local": False}]}),
    )
    with session_scope(scene.engine) as db:
        db.add(row)
        db.commit()
    store.bump_version()


def _text(door, args) -> str:
    r = door.call("work_search", args)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["isError"] is False
    return r.json()["result"]["content"][0]["text"]


def test_work_search_never_tells_another_app_a_waiting_or_provisional_clients_work(door_scene):
    s = door_scene
    door = _door(s)
    smith = s.tmp / "Clients" / "Smith"  # under a Clients folder, no client row: provisional
    smith.mkdir(parents=True)
    jones = s.tmp / "Work" / "Jones"
    jones.mkdir(parents=True)
    downloads = s.tmp / "Downloads"
    downloads.mkdir()
    _waiting(s, "Jones", jones)
    _reported(s, smith, "Smith engagement letter")
    _reported(s, jones, "Supplier list clean up")
    _reported(s, downloads, "Jones invoice follow up")
    _reported(s, downloads, "Bank letter template")
    for args in ({"folder": str(smith)}, {"query": "Smith"}, {"query": "Smith engagement letter"},
                 {"folder": str(jones)}, {"query": "Jones"}, {"query": "supplier list"},
                 {"folder": str(downloads)}, {"query": "invoice follow up"}):
        text = _text(door, args)
        for title in ("Smith engagement letter", "Supplier list clean up", "Jones invoice follow up"):
            assert title not in text, (args, title, text)
    # Work that belongs to nobody is still told.
    assert "Bank letter template" in _text(door, {"folder": str(downloads)})
    # The owner, in Iron Jarvis, is still told each folder's own work.
    assert "Smith engagement letter" in B.work_block(s.platform, folder=str(smith))
    assert "Supplier list clean up" in B.work_block(s.platform, folder=str(jones))
    assert "Jones invoice follow up" in B.work_block(s.platform, folder=str(downloads))
