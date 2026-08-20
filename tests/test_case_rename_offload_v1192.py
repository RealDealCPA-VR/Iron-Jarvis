"""v1.192.0 — two defects in ``tools/builtins.py``'s file tools.

FINDING 35 — a case-only rename was IMPOSSIBLE on Windows, and the refusal
lied about why. ``safe_path`` resolves, and ``Path.resolve()`` on Windows
answers with the ON-DISK spelling whenever a case variant already exists, so
``rename_file('2025_w2.pdf', '2025_W2.pdf')`` had its requested casing
destroyed before the tool looked at it; ``Path`` equality then folds case, so
the tool answered "the new name is the same as the old one" — which it is not.
Removing that guard alone would not have helped: ``src.replace(dst)`` would
have been a same-string no-op.

FINDING 44 — ``edit_file`` and ``write_file`` did their filesystem work on the
daemon's single event loop, and BOTH tools' ``capture_undo`` hooks (awaited by
``registry.invoke`` BEFORE ``execute``) read the file synchronously too, so one
edit blocked the loop on the same file twice. ``read_file`` was fixed for
exactly this in v1.153.1; a stall there does not look like a slow tool, it
looks like "Daemon offline" for the whole app (the documented four-hour
outage).

The offload tests assert BOTH halves the way ``test_event_loop_offload_v1175``
does, so deleting a ``to_thread`` cannot leave them green:
  * STRUCTURAL — the blocking call ran on a worker thread, not the loop's.
  * BEHAVIOURAL — the loop kept servicing other work while it ran.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.tools import builtins as builtins_mod
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import EditFileTool, RenameFileTool, WriteFileTool

#: Same shape as the v1175 offload suite: the faked stall must dwarf the tick
#: so an offloaded call yields many ticks and an inline one yields none.
_BLOCK_S = 0.25
_TICK_S = 0.01
_MIN_TICKS = 5


def _ctx(tmp_path: Path) -> ToolContext:
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=config,
        event_bus=None,
        engine=None,
    )


async def _ticks_during(coro):
    """Await ``coro`` while counting how many times the loop got control."""
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    task = asyncio.ensure_future(_ticker())
    try:
        result = await coro
    finally:
        stop = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


#: ``os.path.normcase`` is what ``_case_only_rename`` tests with, so this is
#: exactly the condition under which the case-only branch can be reached at all.
#: The two guard tests below need it: on a case-SENSITIVE filesystem the two
#: spellings are never string-equal under normcase, so the branch is not taken
#: and there is nothing to confirm.
_FOLDS_CASE = os.path.normcase("A") != "A"
_needs_folding = pytest.mark.skipif(
    not _FOLDS_CASE,
    reason="the case-only branch is only reachable where normcase folds",
)


def _on_disk_name(folder: Path, lowered: str) -> str | None:
    """The name as the FILESYSTEM spells it — ``Path.exists`` folds case on
    Windows, so only a listing can prove the rename actually took."""
    for entry in os.listdir(folder):
        if entry.lower() == lowered:
            return entry
    return None


# --------------------------------------------------------------------------- #
# Finding 35 — case-only rename
# --------------------------------------------------------------------------- #

async def test_case_only_rename_actually_changes_the_capitalization(tmp_path):
    """The user's own job: fix the casing of a tax document."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "2025_w2.pdf").write_text("payload", encoding="utf-8")

    result = await RenameFileTool().execute(
        {"path": "2025_w2.pdf", "new_path": "2025_W2.pdf"}, ctx
    )

    assert result.ok is True, result.error
    assert _on_disk_name(ctx.workspace, "2025_w2.pdf") == "2025_W2.pdf"
    # The content survived the two-step, and no temporary name was left behind.
    assert (ctx.workspace / "2025_W2.pdf").read_text(encoding="utf-8") == "payload"
    assert [e for e in os.listdir(ctx.workspace) if "ij-case" in e] == []
    assert result.data["to"].endswith("2025_W2.pdf")


async def test_truly_identical_name_is_still_refused(tmp_path):
    """The "same name" error must survive — it is now only said when true."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "notes.txt").write_text("x", encoding="utf-8")

    result = await RenameFileTool().execute(
        {"path": "notes.txt", "new_path": "notes.txt"}, ctx
    )

    assert result.ok is False
    assert "same as the old one" in (result.error or "")


async def test_different_name_still_refuses_to_clobber(tmp_path):
    """The overwrite refusal is untouched for genuinely different names — the
    case-only branch must not have opened a hole in it."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "a.pdf").write_text("first", encoding="utf-8")
    (ctx.workspace / "b.pdf").write_text("second", encoding="utf-8")

    result = await RenameFileTool().execute(
        {"path": "a.pdf", "new_path": "b.pdf"}, ctx
    )

    assert result.ok is False
    assert "already exists" in (result.error or "")
    assert (ctx.workspace / "b.pdf").read_text(encoding="utf-8") == "second"


async def test_case_only_rename_in_a_subfolder_keeps_its_folder(tmp_path):
    """A bare new name keeps the folder (v1.177.2) — and still fixes the case."""
    ctx = _ctx(tmp_path)
    sub = ctx.workspace / "clients"
    sub.mkdir()
    (sub / "k1_acme.pdf").write_text("payload", encoding="utf-8")

    result = await RenameFileTool().execute(
        {"path": "clients/k1_acme.pdf", "new_path": "K1_Acme.pdf"}, ctx
    )

    assert result.ok is True, result.error
    assert _on_disk_name(sub, "k1_acme.pdf") == "K1_Acme.pdf"


async def test_case_only_rename_round_trips_through_revert(tmp_path):
    """THE UNDO CONTRACT HOLDS FOR THE NEW CAPABILITY TOO.

    ``rename_file`` declares ``Reversibility.REVERSIBLE`` and the journal entry
    is one of the three reasons it exists as a tool rather than a shell call
    (``test_bulk_job_repair_v1177::test_rename_is_undoable`` pins the ordinary
    case). Before v1.192.0's ``revert`` override the shared ``file_rename``
    branch resolved the OLD spelling straight back to the NEW on-disk one, so
    ``back.exists()`` was True and undo refused with "2025_W2.pdf already
    exists again" — naming the file being renamed as the obstacle to renaming
    it. A journaled-but-unrevertable mutation on a folder of client tax
    documents is exactly the trust trade the house rules forbid.
    """
    ctx = _ctx(tmp_path)
    (ctx.workspace / "2025_w2.pdf").write_text("payload", encoding="utf-8")
    tool = RenameFileTool()
    args = {"path": "2025_w2.pdf", "new_path": "2025_W2.pdf"}

    undo = await tool.capture_undo(args, ctx)
    assert undo is not None and undo["kind"] == "file_rename"
    forward = await tool.execute(args, ctx)
    assert forward.ok is True, forward.error
    assert _on_disk_name(ctx.workspace, "2025_w2.pdf") == "2025_W2.pdf"

    reverted = await tool.revert(undo, ctx)

    assert reverted.ok is True, reverted.error
    # Only a LISTING can prove this — Path.exists folds case on Windows and
    # would be True either way.
    assert _on_disk_name(ctx.workspace, "2025_w2.pdf") == "2025_w2.pdf"
    assert (ctx.workspace / "2025_w2.pdf").read_text(encoding="utf-8") == "payload"
    assert [e for e in os.listdir(ctx.workspace) if "ij-case" in e] == []


async def test_undo_of_an_ordinary_rename_still_refuses_to_clobber(tmp_path):
    """The revert override must not open a hole in the existing refusal: a
    genuinely different name whose old spelling was taken again still falls
    through to ``revert_workspace_file`` and is refused."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "old.pdf").write_text("x", encoding="utf-8")
    tool = RenameFileTool()
    args = {"path": "old.pdf", "new_path": "new.pdf"}

    undo = await tool.capture_undo(args, ctx)
    await tool.execute(args, ctx)
    (ctx.workspace / "old.pdf").write_text("something else", encoding="utf-8")

    reverted = await tool.revert(undo, ctx)

    assert reverted.ok is False
    assert "already exists" in (reverted.error or "")
    assert (ctx.workspace / "old.pdf").read_text(encoding="utf-8") == "something else"


@_needs_folding
async def test_case_variant_that_is_a_different_file_is_still_refused(
    tmp_path, monkeypatch
):
    """A NORMCASE MATCH ALONE MAY NOT DISARM THE CLOBBER REFUSAL.

    ``os.path.normcase`` folds on Windows whether or not the DIRECTORY does, and
    NTFS carries per-directory case sensitivity (as does a Samba/NAS share
    mounted with ``case sensitive = yes``). There ``foo.pdf`` and ``FOO.pdf`` are
    two distinct files, the string test still says "same entry", and the
    case-only branch would rename straight over the other one with no
    ``overwrite=true`` — a silent clobber of a client's document, which is the
    single thing this tool's refusal exists to prevent.

    The condition cannot be created on the NTFS volume the suite runs on, so it
    is driven through the seam the guard consults: ``os.path.samefile``. Both
    halves are asserted, because a guard that simply refuses everything would
    re-break finding 35.
    """
    ctx = _ctx(tmp_path)
    (ctx.workspace / "foo.pdf").write_text("original", encoding="utf-8")
    tool = RenameFileTool()
    args = {"path": "foo.pdf", "new_path": "FOO.pdf"}

    # The pair is NOT one on-disk entry: refuse, exactly as for any other name.
    monkeypatch.setattr(builtins_mod.os.path, "samefile", lambda a, b: False)
    refused = await tool.execute(dict(args), ctx)

    assert refused.ok is False
    assert "already exists" in (refused.error or "")
    # Nothing moved, nothing was overwritten, no temp name left behind.
    assert _on_disk_name(ctx.workspace, "foo.pdf") == "foo.pdf"
    assert (ctx.workspace / "foo.pdf").read_text(encoding="utf-8") == "original"
    assert [e for e in os.listdir(ctx.workspace) if "ij-case" in e] == []

    # ...and with the filesystem telling the truth, finding 35 still works.
    monkeypatch.undo()
    allowed = await tool.execute(dict(args), ctx)

    assert allowed.ok is True, allowed.error
    assert _on_disk_name(ctx.workspace, "foo.pdf") == "FOO.pdf"
    assert (ctx.workspace / "FOO.pdf").read_text(encoding="utf-8") == "original"


@_needs_folding
async def test_undo_will_not_overwrite_a_case_variant_that_is_a_different_file(
    tmp_path, monkeypatch
):
    """THE MIRROR OF THE ABOVE, and the worse half: the revert override skips
    the ``back.exists()`` clobber guard on purpose, so an unconfirmed match
    would DESTROY a genuinely different file during an UNDO — the one operation
    a user runs precisely because they expect nothing to be lost. A non-identical
    ``back`` must fall through to ``revert_workspace_file`` and be refused."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "2025_w2.pdf").write_text("payload", encoding="utf-8")
    tool = RenameFileTool()
    args = {"path": "2025_w2.pdf", "new_path": "2025_W2.pdf"}
    undo = await tool.capture_undo(args, ctx)
    forward = await tool.execute(args, ctx)
    assert forward.ok is True, forward.error

    monkeypatch.setattr(builtins_mod.os.path, "samefile", lambda a, b: False)
    refused = await tool.revert(undo, ctx)

    assert refused.ok is False
    assert "already exists" in (refused.error or "")
    assert _on_disk_name(ctx.workspace, "2025_w2.pdf") == "2025_W2.pdf"
    assert (ctx.workspace / "2025_W2.pdf").read_text(encoding="utf-8") == "payload"
    assert [e for e in os.listdir(ctx.workspace) if "ij-case" in e] == []

    # The real undo still round-trips (the guard did not break the contract).
    monkeypatch.undo()
    allowed = await tool.revert(undo, ctx)

    assert allowed.ok is True, allowed.error
    assert _on_disk_name(ctx.workspace, "2025_w2.pdf") == "2025_w2.pdf"


# --------------------------------------------------------------------------- #
# Finding 44 — the file tools must not block the loop
# --------------------------------------------------------------------------- #

def _slow(record: dict[str, str], key: str, real):
    """Wrap a ``Path`` method so it records its thread and stalls like real I/O."""

    def _wrapper(self, *args, **kwargs):
        record[key] = threading.current_thread().name
        time.sleep(_BLOCK_S)
        return real(self, *args, **kwargs)

    return _wrapper


async def test_edit_file_reads_and_writes_off_the_event_loop(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "doc.txt").write_text("hello world", encoding="utf-8")
    seen: dict[str, str] = {}
    monkeypatch.setattr(Path, "read_text", _slow(seen, "read", Path.read_text))
    monkeypatch.setattr(Path, "write_text", _slow(seen, "write", Path.write_text))

    result, ticks = await _ticks_during(
        EditFileTool().execute(
            {"path": "doc.txt", "old": "world", "new": "there"}, ctx
        )
    )

    assert result.ok is True, result.error
    assert seen.get("read") and seen["read"] != threading.main_thread().name
    assert seen.get("write") and seen["write"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"
    assert (ctx.workspace / "doc.txt").read_text(encoding="utf-8") == "hello there"


async def test_write_file_writes_off_the_event_loop(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen: dict[str, str] = {}
    monkeypatch.setattr(Path, "write_text", _slow(seen, "write", Path.write_text))

    result, ticks = await _ticks_during(
        WriteFileTool().execute({"path": "out/new.txt", "content": "body"}, ctx)
    )

    assert result.ok is True, result.error
    assert seen.get("write") and seen["write"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"
    assert (ctx.workspace / "out" / "new.txt").read_text(encoding="utf-8") == "body"


async def test_edit_file_stat_walk_runs_off_the_event_loop(tmp_path, monkeypatch):
    """THE STAT WALK IS THE THIRD BLOCKING SITE, and the one that stalls
    longest: ``unreadable_reason`` does is_file/is_dir/iterdir, which on an
    unhydrated OneDrive path means waiting for the cloud. The read/write pins
    above cannot see it — they monkeypatch ``Path.read_text``/``write_text``
    only, so an inline ``unreadable_reason(...)`` leaves all of them green."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "doc.txt").write_text("hello world", encoding="utf-8")
    seen: dict[str, str] = {}
    real = builtins_mod.unreadable_reason

    def _slow_reason(target, raw):
        seen["reason"] = threading.current_thread().name
        time.sleep(_BLOCK_S)
        return real(target, raw)

    monkeypatch.setattr(builtins_mod, "unreadable_reason", _slow_reason)

    result, ticks = await _ticks_during(
        EditFileTool().execute(
            {"path": "doc.txt", "old": "world", "new": "there"}, ctx
        )
    )

    assert result.ok is True, result.error
    assert seen.get("reason") and seen["reason"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_write_file_capture_undo_runs_off_the_event_loop(tmp_path, monkeypatch):
    """The hook ``registry.invoke`` awaits BEFORE execute reads the whole prior
    file — on the loop that was the first of two freezes per write."""
    ctx = _ctx(tmp_path)
    (ctx.workspace / "doc.txt").write_text("prior", encoding="utf-8")
    seen: dict[str, str] = {}
    monkeypatch.setattr(Path, "read_text", _slow(seen, "read", Path.read_text))

    undo, ticks = await _ticks_during(
        WriteFileTool().capture_undo({"path": "doc.txt", "content": "next"}, ctx)
    )

    assert undo is not None and undo["kind"] == "file_restore"
    assert seen.get("read") and seen["read"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_edit_file_capture_undo_runs_off_the_event_loop(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "doc.txt").write_text("hello world", encoding="utf-8")
    seen: dict[str, str] = {}
    monkeypatch.setattr(Path, "read_text", _slow(seen, "read", Path.read_text))

    undo, ticks = await _ticks_during(
        EditFileTool().capture_undo(
            {"path": "doc.txt", "old": "world", "new": "there"}, ctx
        )
    )

    assert undo is not None and undo["kind"] == "file_restore"
    assert seen.get("read") and seen["read"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"
