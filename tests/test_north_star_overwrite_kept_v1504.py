"""v1.504.0 (north-star 4.5): three tools stop overwriting silently.

``convert_document`` (asked for nothing: config tier "allow"), ``image_convert``
and ``image_resize`` (whose DEFAULT is to overwrite its own source) replaced an
existing file with no before-copy and said nothing about it: the user's earlier
file was in no journal and no trash. Now each keeps the replaced file the way
``rename_file`` does (a copy in the app's trash, and the answer says where), and
the change can be undone through the ordinary journal (``capture_undo``).

Undo is proven through the real registry and the real ``/undo`` route.
``tests/test_created_paths_v1166.py`` still pins that an overwrite reports no
created path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture()
def env(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "convert_document": "allow", "images": "allow"}
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s-1504-kept", agent_run_id="r-1504",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=p))
    return p, ws, ctx, TestClient(app)


def _png(path: Path, size=(64, 32), colour=(255, 0, 0, 255)) -> bytes:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, colour).save(path, format="PNG")
    return path.read_bytes()


def _last(p) -> ToolInvocation:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation)).all())
        db.expunge(rows[-1])
        return rows[-1]


def _journal(p, action_id: str):
    with session_scope(p.engine) as db:
        return db.get(UndoJournal, action_id)


def _trash_copies(p, name: str) -> list[Path]:
    return sorted((Path(p.config.home) / "trash").rglob(name))


async def _invoke(p, ctx, name, args):
    return await p.registry.invoke(name, args, ctx, p.permissions)


@pytest.mark.parametrize("name", ["convert_document", "image_convert", "image_resize"])
def test_the_three_tools_now_journal_an_inverse(env, name):
    p, *_ = env
    assert p.registry.get(name).reversibility == Reversibility.REVERSIBLE


@pytest.mark.asyncio
async def test_convert_document_over_an_existing_file_keeps_it_and_undoes(env):
    p, ws, ctx, client = env
    (ws / "notes.txt").write_text("plain text from the source", encoding="utf-8")
    out = ws / "out" / "notes.md"
    out.parent.mkdir()
    out.write_text("THE USER'S OWN EARLIER NOTES", encoding="utf-8")
    before = out.read_bytes()
    res = await _invoke(p, ctx, "convert_document", {"source": "notes.txt", "target": "out/notes.md"})
    assert res.ok, res.error
    assert res.created_paths is None  # an overwrite is not a creation
    (kept,) = _trash_copies(p, "notes.md")
    assert kept.read_bytes() == before
    assert "replaced" in res.output and str(kept) in res.output
    assert res.data["replaced_kept"] == str(kept)
    row = _last(p)
    assert _journal(p, row.id).kind == "file_restore"
    r = client.post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert out.read_bytes() == before


@pytest.mark.asyncio
async def test_convert_document_to_a_new_file_says_nothing_was_replaced_and_undoes(env):
    p, ws, ctx, client = env
    (ws / "notes.txt").write_text("plain text", encoding="utf-8")
    res = await _invoke(p, ctx, "convert_document", {"source": "notes.txt", "target": "new.md"})
    assert res.ok, res.error
    assert "replaced" not in res.output and "replaced_kept" not in res.data
    assert res.created_paths == [str((ws / "new.md").resolve())]
    assert _trash_copies(p, "new.md") == []
    r = client.post(f"/undo/{_last(p).id}")
    assert r.status_code == 200, r.text
    assert not (ws / "new.md").exists()


@pytest.mark.asyncio
async def test_image_convert_over_an_existing_file_keeps_it_and_undoes(env):
    p, ws, ctx, client = env
    _png(ws / "pic.png")
    before = _png(ws / "out" / "pic.gif", colour=(0, 0, 255, 255))
    res = await _invoke(p, ctx, "image_convert", {"source": "pic.png", "target": "out/pic.gif"})
    assert res.ok, res.error
    assert res.created_paths is None
    (kept,) = _trash_copies(p, "pic.gif")
    assert kept.read_bytes() == before
    assert "replaced" in res.output and str(kept) in res.output
    r = client.post(f"/undo/{_last(p).id}")
    assert r.status_code == 200, r.text
    assert (ws / "out" / "pic.gif").read_bytes() == before


@pytest.mark.asyncio
async def test_image_resize_in_place_keeps_the_original_and_undoes(env):
    """The default overwrites the SOURCE: the user's original image."""
    p, ws, ctx, client = env
    before = _png(ws / "photo.png", size=(200, 100))
    res = await _invoke(p, ctx, "image_resize", {"source": "photo.png", "max_width": 20})
    assert res.ok, res.error
    assert res.created_paths is None
    assert (ws / "photo.png").read_bytes() != before
    (kept,) = _trash_copies(p, "photo.png")
    assert kept.read_bytes() == before
    assert "replaced" in res.output and str(kept) in res.output
    r = client.post(f"/undo/{_last(p).id}")
    assert r.status_code == 200, r.text
    assert (ws / "photo.png").read_bytes() == before


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_kept_changes_nothing(env, monkeypatch):
    """No silent overwrite: if the earlier file cannot be kept, nothing is."""
    import iron_jarvis.tools.builtins as builtins_mod

    p, ws, ctx, _client = env
    before = _png(ws / "photo.png", size=(200, 100))

    def refuse(*a, **k):
        raise OSError("the trash folder is not writable")

    monkeypatch.setattr(builtins_mod, "_copy_to_trash", refuse)
    res = await _invoke(p, ctx, "image_resize", {"source": "photo.png", "max_width": 20})
    assert res.ok is False
    assert "nothing was changed" in (res.error or "")
    assert (ws / "photo.png").read_bytes() == before
