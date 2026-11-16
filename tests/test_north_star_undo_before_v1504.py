"""v1.504.0 (north-star 4.1): the Undo is written BEFORE the change.

``tools/undo.py`` said the inverse is journaled before the mutation runs; the
registry wrote the ``UndoJournal`` row only after a SUCCESSFUL return. So Stop
mid-write, the per-call deadline, a save that failed half-way, or a crash left
a changed file with no Undo (the registry's own comment: the worker thread
keeps running and the write lands). Now:

* the inverse is written with the intent, before ``execute``;
* it is kept on every outcome that is not provably clean, and dropped only
  when the target's hash proves nothing changed;
* a before-copy no row refers to any more is reclaimed;
* ``/undo`` puts an interrupted write back, and never loses what is there.

Everything goes through the real registry and the real ``/undo`` route.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.tools.registry as registry_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolIntent, ToolInvocation, UndoJournal
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext, ToolResult, safe_path
from iron_jarvis.tools.builtins import WriteFileTool
from iron_jarvis.tools.permissions import PermissionEngine

BIG = ("the client's original ledger line\n" * 400)  # > 8 KB: a before-copy blob
NEW = "the new text\n" * 10


class _Gate:
    """A write in a worker thread that waits for the test, like a slow save."""

    def __init__(self, *, write_first: bool) -> None:
        self.write_first = write_first
        self.started = threading.Event()
        self.release = threading.Event()


class _SlowWrite(WriteFileTool):
    """``write_file``'s own capture and revert; its write runs in a worker
    thread the test holds (``write_first``: the bytes land before it waits)."""

    name = "slow_write_v1504"
    gate: _Gate

    async def execute(self, args, ctx):
        path = safe_path(ctx.workspace, args["path"])
        gate = self.gate

        def work() -> None:
            if gate.write_first:
                path.write_text(args["content"], encoding="utf-8")
            gate.started.set()
            gate.release.wait(10)
            if not gate.write_first:
                path.write_text(args["content"], encoding="utf-8")

        await asyncio.to_thread(work)
        return ToolResult(ok=True, output=f"wrote {args['path']}")


class _HalfSave(WriteFileTool):
    """A save that fails part-way and says so (a writer that saves in place)."""

    name = "half_save_v1504"

    async def execute(self, args, ctx):
        path = safe_path(ctx.workspace, args["path"])
        path.write_text(args["content"][:5], encoding="utf-8")
        return ToolResult(ok=False, error="the disk is full")


class _FailsClean(WriteFileTool):
    """A call that fails before it touches the file."""

    name = "fails_clean_v1504"

    async def execute(self, args, ctx):
        return ToolResult(ok=False, error="refused before writing")


def _setup(tmp_path, *tools):
    p = build_platform(str(tmp_path / "home"))
    for t in tools:
        p.registry.register(t)
    perms = PermissionEngine(
        {**p.config.permissions, "write_file": "allow", **{t.name: "allow" for t in tools}}
    )
    # The tool-backed revert asks the platform's own engine (perform_undo).
    p.permissions = perms
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s-1504-undo", agent_run_id="r-1504",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    return p, perms, ws, ctx


def _client(p) -> TestClient:
    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=p))
    return TestClient(app)


def _rows(engine, model):
    with session_scope(engine) as db:
        out = list(db.exec(select(model)).all())
        for r in out:
            db.expunge(r)
        return out


def _blobs(p) -> list[str]:
    d = Path(p.config.home) / "undo"
    return sorted(x.name for x in d.iterdir()) if d.is_dir() else []


async def _until(fn, seconds=5.0):
    # Polls a condition (never asserts a duration).
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        await asyncio.sleep(0.01)
    return fn()


# --------------------------------------------------------------------------- #
# the order: the inverse is there while the call runs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_inverse_is_journaled_with_the_intent_before_execute(tmp_path):
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=False)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    (ws / "ledger.txt").write_text(BIG, encoding="utf-8")
    task = asyncio.create_task(p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    intents = _rows(p.engine, ToolIntent)
    journal = _rows(p.engine, UndoJournal)
    assert len(intents) == 1
    # Written BEFORE execute, under the intent's id, with the before-copy.
    assert [j.action_id for j in journal] == [intents[0].id]
    assert journal[0].kind == "file_restore" and journal[0].pre_ref
    assert json.loads(journal[0].pre_inline)["workspace"] == str(ws.resolve())
    tool.gate.release.set()
    res = await task
    assert res.ok
    # The finished row replaced it: still ONE row, now final.
    journal = _rows(p.engine, UndoJournal)
    assert [j.action_id for j in journal] == [intents[0].id]
    assert _rows(p.engine, ToolInvocation)[0].id == intents[0].id


# --------------------------------------------------------------------------- #
# Stop mid-write: the worker thread's write lands after the row
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stop_mid_write_keeps_an_undo_that_puts_the_file_back(tmp_path):
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=False)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    task = asyncio.create_task(p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await _until(lambda: _rows(p.engine, ToolInvocation))
    assert rows[0].ok is False and "may have landed" in rows[0].output
    # The write lands AFTER the row (the worker thread kept running).
    tool.gate.release.set()
    assert await _until(lambda: target.exists() and target.read_text(encoding="utf-8") == NEW)
    client = _client(p)
    listed = client.get("/undo", params={"session_id": "s-1504-undo"}).json()["actions"]
    assert [a["action_id"] for a in listed] == [rows[0].id] and listed[0]["undoable"] is True
    r = client.post(f"/undo/{rows[0].id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG


@pytest.mark.asyncio
async def test_stop_before_the_write_landed_is_refused_as_nothing_changed_yet(tmp_path):
    """Cut off before the bytes landed: the Undo says so, touches nothing, and
    keeps the row (the worker may still write); once it has, Undo works."""
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=False)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    task = asyncio.create_task(p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await _until(lambda: _rows(p.engine, ToolInvocation))
    client = _client(p)
    r = client.post(f"/undo/{rows[0].id}")
    assert r.status_code == 409, r.text
    assert "nothing to undo yet" in r.json()["detail"]
    assert target.read_text(encoding="utf-8") == BIG
    tool.gate.release.set()  # the parked worker now writes NEW
    assert await _until(lambda: target.read_text(encoding="utf-8") == NEW)
    r = client.post(f"/undo/{rows[0].id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG


@pytest.mark.asyncio
async def test_stop_then_a_newer_edit_is_kept_in_the_trash_not_lost(tmp_path):
    """An interrupted row has no exact after-hash: an Undo that cannot prove
    the file is the call's own keeps the current one in the app's trash."""
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=True)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    task = asyncio.create_task(p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await _until(lambda: _rows(p.engine, ToolInvocation))
    tool.gate.release.set()
    target.write_text("the user's own later edit\n", encoding="utf-8")
    r = _client(p).post(f"/undo/{rows[0].id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG
    kept = [x for x in (Path(p.config.home) / "trash").rglob("ledger.txt*")]
    assert [x.read_text(encoding="utf-8") for x in kept] == ["the user's own later edit\n"]
    assert str(kept[0]) in r.json()["output"]


# --------------------------------------------------------------------------- #
# the deadline
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_deadline_mid_write_keeps_an_undo(tmp_path):
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=True)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    res = await p.registry.invoke(
        tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms, deadline_s=0.05
    )
    assert res.ok is False and "did not finish within" in (res.error or "")
    tool.gate.release.set()
    action = _rows(p.engine, ToolInvocation)[0].id
    r = _client(p).post(f"/undo/{action}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG


# --------------------------------------------------------------------------- #
# a save that fails part-way / a failure that changed nothing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_save_that_fails_part_way_keeps_its_undo(tmp_path):
    tool = _HalfSave()
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    res = await p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms)
    assert res.ok is False
    assert target.read_text(encoding="utf-8") == NEW[:5]  # the damage is real
    (row,) = _rows(p.engine, ToolInvocation)
    (j,) = _rows(p.engine, UndoJournal)
    assert j.action_id == row.id
    client = _client(p)
    r = client.post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG


@pytest.mark.asyncio
async def test_a_kept_failed_row_still_refuses_to_clobber_a_newer_edit(tmp_path):
    """The kept row's after-hash is what the failure LEFT, so the hash guard
    still protects an edit made since."""
    tool = _HalfSave()
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    await p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms)
    target.write_text("repaired by hand\n", encoding="utf-8")
    (row,) = _rows(p.engine, ToolInvocation)
    r = _client(p).post(f"/undo/{row.id}")
    assert r.status_code == 409
    assert target.read_text(encoding="utf-8") == "repaired by hand\n"


@pytest.mark.asyncio
async def test_a_failure_that_changed_nothing_keeps_no_undo_and_no_before_copy(tmp_path):
    tool = _FailsClean()
    p, perms, ws, ctx = _setup(tmp_path, tool)
    (ws / "ledger.txt").write_text(BIG, encoding="utf-8")
    res = await p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms)
    assert res.ok is False
    assert _rows(p.engine, UndoJournal) == []
    # The >8 KB before-copy was written to <home>/undo/ by the capture; with
    # no row to refer to it, it is reclaimed at once.
    assert _blobs(p) == []


@pytest.mark.asyncio
async def test_an_intent_write_that_lands_late_never_brings_back_a_dropped_inverse(tmp_path):
    """The intent write is bounded and may land AFTER the finished row (SQLite
    busy): it must not re-create the inverse the finished call dropped."""
    from iron_jarvis.core.models import PermissionMode

    tool = _FailsClean()
    p, perms, ws, ctx = _setup(tmp_path, tool)
    (ws / "ledger.txt").write_text("small", encoding="utf-8")
    await p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms)
    (row,) = _rows(p.engine, ToolInvocation)
    late = await tool.capture_undo({"path": "ledger.txt", "content": NEW}, ctx)
    await p.registry._write_intent(
        ctx, row.id, tool.name, {}, PermissionMode.ALLOW, "reversible", tool, "", undo=late
    )
    assert _rows(p.engine, UndoJournal) == []
    assert _rows(p.engine, ToolIntent) == []


# --------------------------------------------------------------------------- #
# a crash: the next boot settles what the dead process left
# --------------------------------------------------------------------------- #


async def _die_mid_call(tool, p, perms, ctx, args, monkeypatch):
    """Run until the tool is inside its write, then 'die': the cancel's own
    ledger row is never written (as when the process is killed)."""

    async def _dead(*a, **k):
        return None

    monkeypatch.setattr(p.registry, "_ledger_interrupted", _dead)
    task = asyncio.create_task(p.registry.invoke(tool.name, args, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _rows(p.engine, ToolInvocation) == []  # nothing but the intent


@pytest.mark.asyncio
async def test_a_crash_after_the_write_landed_leaves_an_undo_at_the_next_boot(tmp_path, monkeypatch):
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=True)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    target = ws / "ledger.txt"
    target.write_text(BIG, encoding="utf-8")
    await _die_mid_call(tool, p, perms, ctx, {"path": "ledger.txt", "content": NEW}, monkeypatch)
    tool.gate.release.set()
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 1
    (row,) = _rows(p.engine, ToolInvocation)
    assert row.output == registry_mod.INTERRUPTED_MAY_HAVE_LANDED
    (j,) = _rows(p.engine, UndoJournal)
    assert j.action_id == row.id and "interrupted" not in json.loads(j.pre_inline)
    r = _client(p).post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert target.read_text(encoding="utf-8") == BIG


@pytest.mark.asyncio
async def test_a_crash_before_the_write_landed_leaves_no_undo_and_no_before_copy(tmp_path, monkeypatch):
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=False)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    (ws / "ledger.txt").write_text(BIG, encoding="utf-8")
    await _die_mid_call(tool, p, perms, ctx, {"path": "ledger.txt", "content": NEW}, monkeypatch)
    assert len(_blobs(p)) == 1
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 1
    assert _rows(p.engine, UndoJournal) == []
    assert _blobs(p) == []
    tool.gate.release.set()


@pytest.mark.asyncio
async def test_a_stopped_call_is_settled_by_the_next_boot(tmp_path):
    """A row kept as 'interrupted' (Stop, the deadline) is checked once every
    worker of that process is gone: a change gets an exact after-hash, no
    change loses its row and its before-copy."""
    tool = _SlowWrite()
    tool.gate = _Gate(write_first=False)
    p, perms, ws, ctx = _setup(tmp_path, tool)
    (ws / "ledger.txt").write_text(BIG, encoding="utf-8")
    task = asyncio.create_task(p.registry.invoke(tool.name, {"path": "ledger.txt", "content": NEW}, ctx, perms))
    await asyncio.to_thread(tool.gate.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _until(lambda: _rows(p.engine, ToolInvocation))
    (j,) = _rows(p.engine, UndoJournal)
    assert json.loads(j.pre_inline).get("interrupted") is True
    registry_mod.reconcile_interrupted_tool_calls(p.engine)
    assert _rows(p.engine, UndoJournal) == [] and _blobs(p) == []
    tool.gate.release.set()


# --------------------------------------------------------------------------- #
# an orphaned before-copy
# --------------------------------------------------------------------------- #


def test_a_before_copy_no_row_refers_to_is_reclaimed_at_boot(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    store = Path(p.config.home) / "undo"
    store.mkdir(parents=True, exist_ok=True)
    orphan = store / "undo_orphan-0123456789abcdef.bin"
    kept = store / "undo_kept-0123456789abcdef.bin"
    in_patch = store / "undo_patch-0123456789abcdef.bin"
    fresh = store / "undo_fresh-0123456789abcdef.bin"
    for f in (orphan, kept, in_patch, fresh):
        f.write_bytes(b"x" * 10)
    with session_scope(p.engine) as db:
        db.add(UndoJournal(action_id="tool_a", session_id="s", tool="write_file",
                           kind="file_restore", pre_ref=kept.name, pre_inline="{}"))
        db.add(UndoJournal(action_id="tool_b", session_id="s", tool="apply_patch",
                           kind="files_patch", pre_inline=json.dumps(
                               {"files": [{"path": "a", "ref": in_patch.name}]})))
        db.commit()
    now = time.time()
    old = now - 3600
    for f in (orphan, kept, in_patch):
        os.utime(f, (old, old))
    # ``fresh`` may belong to a capture still on its way to its row.
    reclaimed = registry_mod.reclaim_orphan_preimages(p.engine, p.config.home, now=now)
    assert reclaimed == 1
    assert sorted(x.name for x in store.iterdir()) == sorted([kept.name, in_patch.name, fresh.name])


# --------------------------------------------------------------------------- #
# unchanged: success, and a failed capture is never journaled as created
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_successful_write_has_one_final_row_and_its_undo_works(tmp_path):
    p, perms, ws, ctx = _setup(tmp_path)
    target = ws / "a.txt"
    target.write_text("before", encoding="utf-8")
    res = await p.registry.invoke("write_file", {"path": "a.txt", "content": "after"}, ctx, perms)
    assert res.ok
    (j,) = _rows(p.engine, UndoJournal)
    assert j.post_sha256 and "interrupted" not in json.loads(j.pre_inline)
    r = _client(p).post(f"/undo/{j.action_id}")
    assert r.status_code == 200 and target.read_text(encoding="utf-8") == "before"
