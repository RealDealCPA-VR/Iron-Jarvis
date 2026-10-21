"""v1.364.0 (IJ harness Phase 1): ``apply_patch`` — Codex's V4A patch format,
multi-file, ALL OR NOTHING, confined to the workspace, one undo entry per
file it touched.

The grammar and the matching ladder are Codex 0.157.0's (recorded in
``tests/fixtures/harness_tool_inventory_v1364.json``): a heredoc wrapper is
tolerated, ``@@ <ctx>`` finds a line first, old lines are matched exactly,
then ignoring trailing whitespace, then both ends, then with Unicode
punctuation normalised; ``*** End of File`` anchors to the end. Every hunk is
checked against the files BEFORE any is written; a hunk that does not fit
writes nothing and names the hunk. Line endings are kept (a deliberate
difference from Codex's default LF rewrite).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents.outcome import session_result
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, ToolInvocation, UndoJournal
from iron_jarvis.harness import patch as P
from iron_jarvis.harness import tools as T
from iron_jarvis.harness.tools import ApplyPatchTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.undo import PATCH_UNDO_KIND, RevertConflict, read_envelope


def _ctx(ws: Path, home: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=home), event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _patch(*body: str) -> str:
    return "\n".join(["*** Begin Patch", *body, "*** End Patch"]) + "\n"


async def _apply(ws, home, text):
    return await ApplyPatchTool().execute({"patch": text}, _ctx(ws, home))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, words",
    [
        ("hello\n*** End Patch", "The first line of the patch must be '*** Begin Patch'"),
        ("*** Begin Patch\n*** Add File: a\n+x", "The last line of the patch must be '*** End Patch'"),
        (_patch("*** Frobnicate: a"), "is not a valid hunk header"),
        (_patch("*** Update File: a.txt", "@@", "?bad"), "Unexpected line found in update hunk"),
        (_patch("*** Update File: a.txt"), "Update file hunk for path 'a.txt' is empty"),
        (_patch(), "the patch has no hunks"),
        ("", "the patch is empty"),
    ],
)
def test_parse_errors_are_codex_words(text, words):
    with pytest.raises(P.PatchError) as err:
        P.parse_patch(text)
    assert words in str(err.value)


def test_a_heredoc_wrapper_and_padding_are_tolerated():
    text = "apply_patch <<'EOF'\n  *** Begin Patch  \n*** Add File: a.txt\n+hi\n*** End Patch\nEOF\n"
    hunks = P.parse_patch(text)
    assert [(h.kind, h.path, h.contents) for h in hunks] == [("add", "a.txt", "hi\n")]


def test_the_inventory_example_parses():
    inv = json.loads((Path(__file__).parent / "fixtures" / "harness_tool_inventory_v1364.json").read_text(encoding="utf-8"))
    hunks = P.parse_patch(inv["codex"]["apply_patch_example"])
    assert [(h.kind, h.path, h.move_to) for h in hunks] == [
        ("add", "path/add.py", None),
        ("delete", "path/delete.py", None),
        ("update", "path/update.py", "path/update2.py"),
    ]
    chunk = hunks[2].chunks[0]
    assert chunk.context == "def f():" and chunk.old_lines == ["    pass"] and chunk.new_lines == ["    return 123"]


# --------------------------------------------------------------------------- #
# The matching ladder
# --------------------------------------------------------------------------- #


def _update(path: str, *lines: str) -> P.Hunk:
    return P.parse_patch(_patch(f"*** Update File: {path}", *lines))[0]


def test_exact_then_trailing_whitespace_then_both_ends_then_punctuation():
    text = "a = 1   \n    b = 2\nprint(\u201chi\u201d \u2014 ok)\n"
    # rung 2: the file has trailing spaces the patch does not
    out = P.apply_update(text, _update("f", "@@", "-a = 1", "+a = 10"))
    assert out.startswith("a = 10\n")
    # rung 3: the patch lost the indentation
    out = P.apply_update(text, _update("f", "@@", "-b = 2", "+    b = 20"))
    assert "    b = 20\n" in out
    # rung 4: ASCII quotes and dash in the patch, typographic in the file
    out = P.apply_update(text, _update("f", "@@", '-print("hi" - ok)', '+print("bye")'))
    assert out.endswith('print("bye")\n')


def test_a_context_header_moves_the_search_past_it():
    text = "def a():\n    return 1\n\ndef b():\n    return 1\n"
    out = P.apply_update(text, _update("f", "@@ def b():", "-    return 1", "+    return 2"))
    assert out == "def a():\n    return 1\n\ndef b():\n    return 2\n"
    with pytest.raises(P.PatchError, match="Failed to find context 'def c\\(\\):'"):
        P.apply_update(text, _update("f", "@@ def c():", "-    return 1", "+    return 2"))


def test_end_of_file_anchors_to_the_last_lines():
    text = "x\ny\nx\n"
    out = P.apply_update(text, _update("f", "@@", "-x", "+z", "*** End of File"))
    assert out == "x\ny\nz\n"


def test_a_pure_addition_appends_and_a_missing_block_is_named():
    out = P.apply_update("a\n", _update("f", "@@", "+b"))
    assert out == "a\nb\n"
    with pytest.raises(P.PatchError) as err:
        P.apply_update("a\n", _update("f", "@@", "-zzz", "+q"))
    assert "chunk 1: Failed to find expected lines in f:\nzzz" in str(err.value)


# --------------------------------------------------------------------------- #
# The tool: many files, all or nothing
# --------------------------------------------------------------------------- #


async def test_add_update_delete_move_in_one_patch(ws, home):
    (ws / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (ws / "old.txt").write_text("bye\n", encoding="utf-8")
    (ws / "mv.txt").write_text("one\ntwo\n", encoding="utf-8")
    res = await _apply(ws, home, _patch(
        "*** Add File: pkg/new.py",
        "+print('hi')",
        "*** Update File: a.py",
        "@@ def f():",
        "-    return 1",
        "+    return 2",
        "*** Delete File: old.txt",
        "*** Update File: mv.txt",
        "*** Move to: moved/mv.txt",
        "@@",
        "-two",
        "+TWO",
    ))
    assert res.ok, res.error
    assert res.output.split("\n")[:5] == [
        "Success. Updated the following files:",
        "A pkg/new.py",
        "M a.py",
        "D old.txt",
        "M moved/mv.txt (moved from mv.txt)",
    ]
    assert res.output.endswith("Check: all 2 changed code files parse.")
    assert (ws / "pkg/new.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert (ws / "a.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"
    assert not (ws / "old.txt").exists() and not (ws / "mv.txt").exists()
    assert (ws / "moved/mv.txt").read_text(encoding="utf-8") == "one\nTWO\n"
    assert sorted(res.data["created"]) == ["moved/mv.txt", "pkg/new.py"]
    assert sorted(res.data["deleted"]) == ["mv.txt", "old.txt"]
    assert res.data["changed"] == ["a.py"]


async def test_one_bad_hunk_writes_nothing_and_names_the_hunk(ws, home):
    """MUTATION: write each hunk as it is applied (no up-front plan) -> the
    first file changes although hunk 2 failed -> red."""
    (ws / "a.txt").write_bytes(b"keep\n")
    (ws / "b.txt").write_bytes(b"b\n")
    res = await _apply(ws, home, _patch(
        "*** Update File: a.txt", "@@", "-keep", "+changed",
        "*** Add File: c.txt", "+new",
        "*** Update File: b.txt", "@@", "-not there", "+x",
    ))
    assert res.ok is False
    assert res.error.startswith("apply_patch verification failed — nothing was changed.")
    assert "hunk 3 (*** Update File: b.txt), chunk 1: Failed to find expected lines in b.txt" in res.error
    assert (ws / "a.txt").read_bytes() == b"keep\n"
    assert not (ws / "c.txt").exists()


@pytest.mark.parametrize(
    "hunk",
    [
        ("*** Add File: ../escape.txt", "+x"),
        ("*** Delete File: ../../etc/passwd",),
        ("*** Update File: a.txt", "*** Move to: ../out.txt", "@@", "-a", "+b"),
    ],
)
async def test_a_path_outside_the_workspace_is_refused_and_nothing_is_written(ws, home, hunk):
    """MUTATION: resolve paths without ``safe_path`` -> the write escapes ->
    red."""
    (ws / "a.txt").write_bytes(b"a\n")
    res = await _apply(ws, home, _patch("*** Add File: inside.txt", "+fine", *hunk))
    assert res.ok is False and "is outside the session's folder" in res.error
    assert not (ws / "inside.txt").exists()
    assert not (ws.parent / "escape.txt").exists() and not (ws.parent / "out.txt").exists()
    assert (ws / "a.txt").read_bytes() == b"a\n"


async def test_an_absolute_path_inside_the_workspace_works(ws, home):
    target = (ws / "abs.txt").resolve()
    res = await _apply(ws, home, _patch(f"*** Add File: {target}", "+ok"))
    assert res.ok and target.read_text(encoding="utf-8") == "ok\n"


async def test_refusals_say_what_is_wrong(ws, home):
    (ws / "here.txt").write_text("x\n", encoding="utf-8")
    (ws / "there.txt").write_text("y\n", encoding="utf-8")
    missing = await _apply(ws, home, _patch("*** Update File: nope.txt", "@@", "-a", "+b"))
    assert "'nope.txt' does not exist" in missing.error
    gone = await _apply(ws, home, _patch("*** Delete File: nope.txt"))
    assert "cannot be deleted" in gone.error
    clash = await _apply(ws, home, _patch("*** Update File: here.txt", "*** Move to: there.txt", "@@", "-x", "+z"))
    assert "a file already exists there" in clash.error
    (ws / "bin.dat").write_bytes(b"\x00\x01")
    binary = await _apply(ws, home, _patch("*** Update File: bin.dat", "@@", "-a", "+b"))
    assert "binary file" in binary.error
    assert (ws / "here.txt").read_text(encoding="utf-8") == "x\n"


async def test_crlf_files_stay_crlf(ws, home):
    (ws / "w.txt").write_bytes(b"one\r\ntwo\r\n")
    res = await _apply(ws, home, _patch("*** Update File: w.txt", "@@", "-two", "+2"))
    assert res.ok
    assert (ws / "w.txt").read_bytes() == b"one\r\n2\r\n"


async def test_the_patch_is_planned_and_written_off_the_loop(ws, home, monkeypatch):
    seen: list[int] = []
    real = T._plan_patch

    def spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(T, "_plan_patch", spy)
    assert (await _apply(ws, home, _patch("*** Add File: x.txt", "+x"))).ok
    assert seen and all(t != threading.get_ident() for t in seen)


# --------------------------------------------------------------------------- #
# Undo: one entry per file, all or nothing
# --------------------------------------------------------------------------- #


async def test_the_undo_puts_back_every_file_the_patch_touched(ws, home):
    (ws / "a.txt").write_bytes(b"a\n")
    (ws / "gone.txt").write_bytes(b"x" * 20000)  # a before-image too big to inline
    (ws / "mv.txt").write_bytes(b"m\n")
    text = _patch(
        "*** Add File: new.txt", "+n",
        "*** Update File: a.txt", "@@", "-a", "+A",
        "*** Delete File: gone.txt",
        "*** Update File: mv.txt", "*** Move to: sub/mv.txt", "@@", "-m", "+M",
    )
    tool = ApplyPatchTool()
    ctx = _ctx(ws, home)
    undo = await tool.capture_undo({"patch": text}, ctx)
    assert undo["kind"] == PATCH_UNDO_KIND
    meta = read_envelope(undo)
    by_path = {e["path"]: e for e in meta["files"]}
    assert set(by_path) == {"new.txt", "a.txt", "gone.txt", "mv.txt", "sub/mv.txt"}, "one entry per file"
    assert by_path["gone.txt"]["ref"] and by_path["gone.txt"]["post_sha256"] is None
    assert sorted(meta["created"]) == ["new.txt", "sub/mv.txt"]
    assert meta["changed"] == ["a.txt"] and sorted(meta["deleted"]) == ["gone.txt", "mv.txt"]
    assert (await tool.execute({"patch": text}, ctx)).ok
    back = await tool.revert(undo, ctx)
    assert back.ok, back.error
    assert (ws / "a.txt").read_bytes() == b"a\n"
    assert (ws / "gone.txt").read_bytes() == b"x" * 20000
    assert (ws / "mv.txt").read_bytes() == b"m\n"
    assert not (ws / "new.txt").exists() and not (ws / "sub/mv.txt").exists()
    # The big before-image's blob is consumed.
    assert not (home / "undo" / by_path["gone.txt"]["ref"]).exists()


async def test_the_undo_refuses_when_any_file_changed_since_and_changes_none(ws, home):
    """MUTATION: restore file by file without checking ALL hashes first ->
    a.txt is restored before the drift on b.txt is found -> red."""
    (ws / "a.txt").write_bytes(b"a\n")
    (ws / "b.txt").write_bytes(b"b\n")
    text = _patch("*** Update File: a.txt", "@@", "-a", "+A", "*** Update File: b.txt", "@@", "-b", "+B")
    tool = ApplyPatchTool()
    ctx = _ctx(ws, home)
    undo = await tool.capture_undo({"patch": text}, ctx)
    assert (await tool.execute({"patch": text}, ctx)).ok
    (ws / "b.txt").write_bytes(b"edited by the user\n")
    with pytest.raises(RevertConflict, match="b.txt changed since the patch"):
        await tool.revert(undo, ctx)
    assert (ws / "a.txt").read_bytes() == b"A\n", "nothing was undone"


async def test_through_the_registry_the_journal_row_and_the_receipt(platform, ws):
    """The REAL registry journals ONE files_patch row; the session receipt
    (agents/outcome) lists the created and changed files from it."""
    sid = "s-patch"
    with session_scope(platform.engine) as db:
        db.add(Session(id=sid, task="patch", workspace_path=str(ws)))
        db.commit()
    ctx = ToolContext(
        workspace=ws, session_id=sid, agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    (ws / "a.txt").write_text("a\n", encoding="utf-8")
    res = await platform.registry.invoke(
        "apply_patch",
        {"patch": _patch("*** Add File: made.txt", "+m", "*** Update File: a.txt", "@@", "-a", "+A")},
        ctx, platform.permissions,
    )
    assert res.ok, res.error
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(UndoJournal).where(UndoJournal.session_id == sid)))
        invs = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == sid)))
    assert len(rows) == 1 and rows[0].kind == PATCH_UNDO_KIND
    assert invs[0].tool == "apply_patch" and invs[0].ok
    result = session_result(platform.engine, sid)
    assert result["files_created"] == ["made.txt"]
    assert result["files_changed"] == ["a.txt"]
    # The other two readers of the journal agree (one rule, four readers):
    # the mission view's batched read and the project world's created names.
    from iron_jarvis.agents.mission import _team_documents
    from iron_jarvis.projects.world import _created_names

    with session_scope(platform.engine) as db:
        row = db.get(Session, sid)
        team = _team_documents(db, {sid: row})
        names = _created_names(db, [sid])
    assert team[sid] == [str(f) for f in result["documents"]]
    assert names[sid] == ["made.txt"]


async def test_the_retention_prune_reclaims_a_patchs_per_file_blobs(platform, ws):
    """``core.db.prune_events`` deleted only the blobs named in the journal's
    ``pre_ref`` column; a patch keeps one blob PER FILE inside its envelope.
    MUTATION: drop the files_patch read in prune_events -> the before-image
    (prior file content) is stranded on disk -> red."""
    from datetime import timedelta

    from iron_jarvis.core.db import prune_events
    from iron_jarvis.core.ids import utcnow

    ctx = ToolContext(
        workspace=ws, session_id="s-prune", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    (ws / "big.txt").write_bytes(b"secret line\n" * 2000)  # over the inline size: a blob
    res = await platform.registry.invoke(
        "apply_patch", {"patch": _patch("*** Delete File: big.txt")}, ctx, platform.permissions
    )
    assert res.ok, res.error
    with session_scope(platform.engine) as db:
        row = db.exec(select(UndoJournal).where(UndoJournal.session_id == "s-prune")).one()
        ref = read_envelope({"pre_inline": row.pre_inline})["files"][0]["ref"]
        row.created_at = utcnow() - timedelta(days=400)
        db.add(row)
        db.commit()
    blob = Path(platform.config.home) / "undo" / ref
    assert blob.exists()
    prune_events(platform.engine, 30)
    assert not blob.exists()


async def test_the_chats_changes_view_reads_a_patch_file_by_file(platform, ws):
    """``POST /chat/changes`` (chat_changes.collect_changes): each file of a
    patch has its own row — created, modified with a diff, deleted — read
    from that file's own entry in the one journal row."""
    from datetime import datetime, timedelta, timezone

    from iron_jarvis.daemon.routes.chat_changes import collect_changes

    ctx = ToolContext(
        workspace=ws, session_id="chat", agent_run_id="",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    (ws / "keep.txt").write_bytes(b"one\ntwo\n")
    (ws / "drop.txt").write_bytes(b"bye\n")
    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    res = await platform.registry.invoke(
        "apply_patch",
        {"patch": _patch(
            "*** Add File: born.txt", "+hello",
            "*** Update File: keep.txt", "@@", "-two", "+TWO",
            "*** Delete File: drop.txt",
        )},
        ctx, platform.permissions,
    )
    assert res.ok, res.error
    out = collect_changes(platform.engine, platform.config.home, since, None, [])
    rows = {c["rel"]: c for c in out["changes"]}
    assert set(rows) == {"born.txt", "keep.txt", "drop.txt"}
    assert rows["born.txt"]["status"] == "created" and rows["born.txt"]["added"] == 1
    assert rows["keep.txt"]["status"] == "modified"
    assert "-two" in rows["keep.txt"]["diff"] and "+TWO" in rows["keep.txt"]["diff"]
    assert rows["keep.txt"]["changed_since"] is False
    assert rows["drop.txt"]["status"] == "deleted" and rows["drop.txt"]["changed_since"] is False
