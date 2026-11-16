"""v1.510.0 (second north-star review, T4): a patch over several files keeps
its Undo and its saved copies when it is cut off, and never leaves a file cut
short.

``tools/undo.settle_unfinished`` dropped a ``files_patch`` inverse (and
deleted its before-copies) for EVERY call that did not succeed, before it
looked at the files: a Stop, the time limit or a crash while the worker thread
was still writing left changed and deleted files with no Undo, and the boot's
settling deleted the copies that could put them back. ``_write_plan`` and the
patch's revert also wrote each file in place (``write_bytes`` truncates first).

Everything goes through the real registry, the real ``/undo`` route and the
real boot step. The worker thread is held at an exact write by a hook on
``Path.write_bytes`` (events, never a sleep).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.harness.tools as harness_tools
import iron_jarvis.tools.registry as registry_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

FILLER = "a line that is the same in every file\n" * 300  # > 8 KB: a before-copy blob
NAMES = ("a.txt", "b.txt", "c.txt")
SESSION = "s-1510-patch"


def _before(name: str) -> bytes:
    return (f"{name} alpha\n" + FILLER).encode("utf-8")


def _after(name: str) -> bytes:
    return (f"{name} ALPHA\n" + FILLER).encode("utf-8")


PATCH = "\n".join([
    "*** Begin Patch",
    *[line for n in NAMES for line in (f"*** Update File: {n}", "@@", f"-{n} alpha", f"+{n} ALPHA")],
    "*** End Patch",
]) + "\n"


class _WriteHook:
    """Holds the worker thread at ONE file's write (the file itself or the
    hidden temp it is saved through): before it, after it, or half way
    through it (``partial``: half the bytes are on disk, as at a crash)."""

    def __init__(self, name: str, *, when: str) -> None:
        self.name = name
        self.when = when  # "before" | "after" | "partial"
        self.fired = False
        self.started = threading.Event()
        self.release = threading.Event()

    def install(self, monkeypatch) -> "_WriteHook":
        real = Path.write_bytes
        hook = self

        def write_bytes(path, data):
            name = Path(path).name
            if hook.fired or not (name == hook.name or name.startswith(f".{hook.name}.tmp-")):
                return real(path, data)
            hook.fired = True
            if hook.when == "partial":
                real(path, bytes(data)[: len(data) // 2])
                hook.started.set()
                hook.release.wait(10)
                return real(path, data)
            if hook.when == "before":
                hook.started.set()
                hook.release.wait(10)
            n = real(path, data)
            if hook.when == "after":
                hook.started.set()
                hook.release.wait(10)
            return n

        monkeypatch.setattr(Path, "write_bytes", write_bytes)
        return self


def _setup(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    perms = PermissionEngine({**p.config.permissions, "apply_patch": "allow"})
    p.permissions = perms  # the revert asks the platform's own engine (perform_undo)
    ws = tmp_path / "ws"
    ws.mkdir()
    for n in NAMES:
        (ws / n).write_bytes(_before(n))
    ctx = ToolContext(
        workspace=ws, session_id=SESSION, agent_run_id="r-1510",
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


def _contents(ws) -> dict[str, bytes]:
    return {n: (ws / n).read_bytes() for n in NAMES}


async def _until(fn, seconds=5.0):
    # Polls a condition (never asserts a duration).
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        await asyncio.sleep(0.01)
    return fn()


async def _stop_at(hook, p, perms, ctx):
    """Start the patch, wait until the hook holds its thread, press Stop."""
    task = asyncio.create_task(p.registry.invoke("apply_patch", {"patch": PATCH}, ctx, perms))
    assert await asyncio.to_thread(hook.started.wait, 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return await _until(lambda: _rows(p.engine, ToolInvocation))


async def _die_at(hook, p, perms, ctx, monkeypatch):
    """As :func:`_stop_at`, but the process 'dies': the cancel's own ledger
    row is never written, only the intent and its journal row remain."""

    async def _dead(*a, **k):
        return None

    monkeypatch.setattr(p.registry, "_ledger_interrupted", _dead)
    task = asyncio.create_task(p.registry.invoke("apply_patch", {"patch": PATCH}, ctx, perms))
    assert await asyncio.to_thread(hook.started.wait, 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _rows(p.engine, ToolInvocation) == []


# --------------------------------------------------------------------------- #
# Stop part way: the Undo is kept and puts back all three
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_patch_stopped_after_its_second_write_keeps_an_undo_for_all_three(tmp_path, monkeypatch):
    p, perms, ws, ctx = _setup(tmp_path)
    hook = _WriteHook("b.txt", when="after").install(monkeypatch)
    rows = await _stop_at(hook, p, perms, ctx)
    assert rows[0].ok is False
    (j,) = _rows(p.engine, UndoJournal)
    assert j.kind == "files_patch" and json.loads(j.pre_inline).get("interrupted") is True
    assert len(_blobs(p)) == 3, "every before-copy is kept"
    # The worker thread runs on and lands the third file after the row.
    hook.release.set()
    assert await _until(lambda: (ws / "c.txt").read_bytes() == _after("c.txt"))
    client = _client(p)
    listed = client.get("/undo", params={"session_id": SESSION}).json()["actions"]
    assert [a["action_id"] for a in listed] == [rows[0].id] and listed[0]["undoable"] is True
    r = client.post(f"/undo/{rows[0].id}")
    assert r.status_code == 200, r.text
    assert _contents(ws) == {n: _before(n) for n in NAMES}


@pytest.mark.asyncio
async def test_undo_of_a_stopped_patch_keeps_a_newer_edit_in_the_trash(tmp_path, monkeypatch):
    """An interrupted patch has no proven after-state: a file that is neither
    as it was nor as the patch meant it is kept in the app's trash, never
    overwritten blind."""
    p, perms, ws, ctx = _setup(tmp_path)
    hook = _WriteHook("b.txt", when="after").install(monkeypatch)
    rows = await _stop_at(hook, p, perms, ctx)
    hook.release.set()
    assert await _until(lambda: (ws / "c.txt").read_bytes() == _after("c.txt"))
    (ws / "b.txt").write_bytes(b"the user's own later edit\n")
    r = _client(p).post(f"/undo/{rows[0].id}")
    assert r.status_code == 200, r.text
    assert _contents(ws) == {n: _before(n) for n in NAMES}
    kept = list((Path(p.config.home) / "trash").rglob("b.txt*"))
    assert [k.read_bytes() for k in kept] == [b"the user's own later edit\n"]


@pytest.mark.asyncio
async def test_a_patch_stopped_before_any_write_changes_nothing_and_leaves_nothing_at_boot(tmp_path, monkeypatch):
    """Stopped before its first write: Undo says there is nothing to undo
    yet and touches nothing (the worker may still write, so the row stays);
    the next boot, every worker gone, finds every file as it was and removes
    the row and every saved copy."""
    p, perms, ws, ctx = _setup(tmp_path)
    hook = _WriteHook("a.txt", when="before").install(monkeypatch)
    rows = await _stop_at(hook, p, perms, ctx)
    r = _client(p).post(f"/undo/{rows[0].id}")
    assert r.status_code == 409, r.text
    assert "nothing to undo yet" in r.json()["detail"]
    assert _contents(ws) == {n: _before(n) for n in NAMES}
    registry_mod.reconcile_interrupted_tool_calls(p.engine)
    assert _rows(p.engine, UndoJournal) == []
    assert _blobs(p) == []
    hook.release.set()


# --------------------------------------------------------------------------- #
# a crash: the boot keeps what can put a changed file back
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_boot_keeps_the_copies_of_a_patch_that_changed_a_file(tmp_path, monkeypatch):
    p, perms, ws, ctx = _setup(tmp_path)
    # Held as b.txt's write starts: a.txt is written and swapped, b and c not.
    hook = _WriteHook("b.txt", when="before").install(monkeypatch)
    await _die_at(hook, p, perms, ctx, monkeypatch)
    assert (ws / "a.txt").read_bytes() == _after("a.txt")
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 1
    (row,) = _rows(p.engine, ToolInvocation)
    (j,) = _rows(p.engine, UndoJournal)
    assert j.action_id == row.id and "interrupted" not in json.loads(j.pre_inline)
    assert len(_blobs(p)) == 3, "the before-copies of the changed file are kept"
    r = _client(p).post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert _contents(ws) == {n: _before(n) for n in NAMES}
    hook.release.set()


@pytest.mark.asyncio
async def test_a_crash_half_way_through_a_write_never_leaves_that_file_cut_short(tmp_path, monkeypatch):
    """The process dies with half of b.txt's new bytes written. Each file is
    saved beside and swapped (``tools/base.atomic_write_bytes``): b.txt is
    still whole. MUTATION: write the target in place -> b.txt is cut -> red."""
    p, perms, ws, ctx = _setup(tmp_path)
    hook = _WriteHook("b.txt", when="partial").install(monkeypatch)
    await _die_at(hook, p, perms, ctx, monkeypatch)
    # The process is gone here: what is on disk is what the user is left with.
    assert (ws / "a.txt").read_bytes() == _after("a.txt")
    assert (ws / "b.txt").read_bytes() == _before("b.txt"), "b.txt was cut short"
    assert (ws / "c.txt").read_bytes() == _before("c.txt")
    assert registry_mod.reconcile_interrupted_tool_calls(p.engine) == 1
    (row,) = _rows(p.engine, ToolInvocation)
    r = _client(p).post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert _contents(ws) == {n: _before(n) for n in NAMES}
    hook.release.set()


# --------------------------------------------------------------------------- #
# unchanged: a patch that fails cleanly is rolled back and keeps no Undo
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_patch_that_fails_and_rolls_back_keeps_no_undo_and_no_copies(tmp_path, monkeypatch):
    p, perms, ws, ctx = _setup(tmp_path)
    real = Path.write_bytes

    def refuse_c(path, data):
        if Path(path).name == "c.txt" or Path(path).name.startswith(".c.txt.tmp-"):
            raise OSError("the disk is full")
        return real(path, data)

    monkeypatch.setattr(Path, "write_bytes", refuse_c)
    res = await p.registry.invoke("apply_patch", {"patch": PATCH}, ctx, perms)
    assert res.ok is False and "put back" in (res.error or "")
    assert _contents(ws) == {n: _before(n) for n in NAMES}
    assert _rows(p.engine, UndoJournal) == []
    assert _blobs(p) == []


# --------------------------------------------------------------------------- #
# every write of the patch and of its Undo goes through the atomic helper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_patch_and_its_undo_write_through_the_atomic_helper(tmp_path, monkeypatch):
    p, perms, ws, ctx = _setup(tmp_path)
    seen: list[str] = []
    real = harness_tools.atomic_write_bytes

    def spy(path, data):
        seen.append(Path(path).name)
        return real(path, data)

    monkeypatch.setattr(harness_tools, "atomic_write_bytes", spy)
    res = await p.registry.invoke("apply_patch", {"patch": PATCH}, ctx, perms)
    assert res.ok, res.error
    assert seen == list(NAMES)
    (row,) = _rows(p.engine, ToolInvocation)
    seen.clear()
    r = _client(p).post(f"/undo/{row.id}")
    assert r.status_code == 200, r.text
    assert sorted(seen) == list(NAMES)
    assert _contents(ws) == {n: _before(n) for n in NAMES}
