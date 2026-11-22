"""R2.15 (phase 27-a), decision D-27.2: before and after pictures are KEPT with
the session — the newest 40 replies — never pruned by the page screenshots'
own count, moved to the app's trash when the session is deleted, and their
record never holds an address's query string. A kept picture is served only
from its own reply's folder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import evidence as E
from iron_jarvis.harness import page_shot as PS

DEV = "http://localhost:5173/settings/billing?token=s3cr3t-value&next=%2Fhome#frag"


def _keep(home, thread, turn, item="after", png=b"PIC", address=DEV):
    return E._save(home, thread, turn, item, png, address, at=E._now())


def test_pictures_outlive_fifty_newer_page_screenshots(tmp_path):
    home = tmp_path / "home"
    _keep(home, "build:s1", "turn-a", "before", b"B")
    _keep(home, "build:s1", "turn-a", "after", b"A")
    ctx = SimpleNamespace(session_id="build:s1")
    for n in range(PS.KEEP_SHOTS + 5):
        PS._save_picture(b"shot-%d" % n, PS.shots_folder(home, ctx), "http://localhost:5173/")
    assert len(list(PS.shots_folder(home, ctx).glob("page-*"))) == PS.KEEP_SHOTS
    assert E.picture_file(home, "build:s1", "turn-a", "before").read_bytes() == b"B"
    assert E.picture_file(home, "build:s1", "turn-a", "after").read_bytes() == b"A"


def test_a_session_keeps_the_pictures_of_its_newest_40_replies(tmp_path):
    home = tmp_path / "home"
    root = E.session_folder(home, "build:s1")
    for n in range(E.KEEP_REPLIES + 2):
        _keep(home, "build:s1", f"turn-{n:02d}")
        # Saved one after another: give each its own time, oldest first.
        mark = root / f"turn-{n:02d}" / "evidence.json"
        os.utime(mark, ns=(1_700_000_000_000_000_000 + n * 1_000_000_000,) * 2)
    removed = E.prune(home, "build:s1")
    assert removed == []  # the save already pruned; nothing more to do
    kept = sorted(p.name for p in root.iterdir())
    assert len(kept) == E.KEEP_REPLIES
    assert "turn-00" not in kept and "turn-01" not in kept
    assert "turn-41" in kept and "turn-02" in kept
    # Another session's pictures are never counted against this one.
    _keep(home, "build:s2", "turn-x")
    assert E.picture_file(home, "build:s2", "turn-x", "after") is not None
    assert len(list(root.iterdir())) == E.KEEP_REPLIES


def test_the_query_string_and_fragment_are_never_stored(tmp_path):
    home = tmp_path / "home"
    item = _keep(home, "build:s1", "turn-q")
    assert item["where"] == "localhost:5173/settings/billing"
    text = (E.reply_folder(home, "build:s1", "turn-q") / "evidence.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["where"] == "localhost:5173/settings/billing"
    for leak in ("s3cr3t", "token", "next=", "?", "#frag", "%2F"):
        assert leak not in text, leak
    assert data["items"][0]["sha256"] and data["items"][0]["file"].endswith("after.png")


def test_a_picture_is_found_only_in_its_own_replys_folder(tmp_path):
    home = tmp_path / "home"
    _keep(home, "build:s1", "turn-1")
    assert E.picture_file(home, "build:s1", "turn-1", "after") is not None
    # Files a bad id could reach if it were not refused: another reply's
    # folder whose name a cleaned id would match, and a picture one folder up.
    _keep(home, "build:s1", "turn1")
    (E.session_folder(home, "build:s1") / "after.png").write_bytes(b"planted")
    (E.session_folder(home, "build:s1") / "turn-1" / "evidence.json.png").write_bytes(b"planted")
    for turn, item in (("turn-1", "evidence.json"), ("turn-1", "../after"), ("..", "after"), (".", "after"),
                       (".turn-1", "after"), ("turn-1.", "after"), ("turn-1/../turn-1", "after"),
                       ("turn 1", "after"), ("", "after")):
        assert E.picture_file(home, "build:s1", turn, item) is None, (turn, item)
    assert E.picture_file(home, "build:other", "turn-1", "after") is None
    # The turn route's own ids (letters, digits, '-', '_', '.') all have a folder.
    _keep(home, "build:s1", "chat-m3k9.v2_x")
    assert E.picture_file(home, "build:s1", "chat-m3k9.v2_x", "after") is not None


def test_the_saved_record_is_whitelisted():
    raw = {"kind": "pictures", "screen": True, "note": "n", "extra": "x", "items": [
        {"id": "before", "label": "X", "where": "localhost:5173/a", "at": "t", "sha": "a" * 40, "file": "C:/x"},
        {"id": "evil", "label": "Evil"},
    ]}
    rec = E.saved_record(raw)
    assert rec == {"kind": "pictures", "screen": True, "note": "n", "items": [
        {"id": "before", "label": "Before", "where": "localhost:5173/a", "at": "t", "sha": "a" * 16},
    ]}
    assert E.saved_record({"kind": "pictures", "items": []}) is None  # never marked
    assert E.saved_record("pictures") is None


async def test_deleting_the_session_moves_its_pictures_to_the_trash(tmp_path):
    from iron_jarvis.daemon.app import create_app

    from tests.test_build_sessions_v1367 import asgi, new_session

    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    home = Path(app.state.platform.config.home)
    sid = new_session(app, work)["id"]
    _keep(home, sid, "turn-1", "before", b"B")
    _keep(home, sid, "turn-1", "after", b"A")
    folder = E.session_folder(home, sid)
    assert folder.is_dir()
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 200, body
    assert not folder.exists()
    moved = list((home / "trash").glob(f"*/{E.DIRNAME}/{folder.name}/turn-1/before.png"))
    assert len(moved) == 1 and moved[0].read_bytes() == b"B"  # moved, never deleted


async def test_a_kept_picture_is_served_and_a_gone_one_is_said(tmp_path):
    from iron_jarvis.daemon.app import create_app

    from tests.test_build_sessions_v1367 import asgi, new_session

    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    home = Path(app.state.platform.config.home)
    sid = new_session(app, work)["id"]
    _keep(home, sid, "turn-1", "after", b"PIC-after")
    status, body = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/turn-1/after")
    assert status == 200 and body == "PIC-after"
    status, body = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/turn-1/before")
    assert status == 404 and body["detail"] == "This picture is no longer kept."
    status, body = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/turn-1/evidence.json")
    assert status == 404
    status, body = await asgi(app, "GET", f"/build/sessions/nope/evidence/turn-1/after")
    assert status == 404


def test_the_automatic_setting_is_offered_once_and_never_once_chosen(tmp_path):
    cfg = SimpleNamespace(home=tmp_path / "home", reply_pictures=None)
    assert E.offer_due(cfg) is True
    E.mark_offered(cfg.home)
    assert E.offer_due(cfg) is False
    fresh = SimpleNamespace(home=tmp_path / "home2", reply_pictures=False)
    assert E.offer_due(fresh) is False  # chosen (off): never offered
    fresh.reply_pictures = True
    assert E.offer_due(fresh) is False


def test_the_setting_is_a_row_with_undo_that_starts_not_set(tmp_path):
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    row = schema.get("reply_pictures")
    assert row is not None and row.type == "bool" and row.tier == "allow" and row.unset_ok
    assert "invisible browser" in row.help and "twice" in row.help  # names its cost
    base = {"home": str(tmp_path / "home"), "project_root": str(tmp_path)}
    assert Config(**base).reply_pictures is None
    assert Config(**base, reply_pictures="yes please").reply_pictures is None  # a hand edit never stops a boot
    assert Config(**base, reply_pictures=True).reply_pictures is True
