"""v1.364.0 (IJ harness Phase 1): ``edit_file`` is STRICT, ``multi_edit``
is all or nothing, and a written code file carries one syntax-check line.

THE DEFECT: ``edit_file`` replaced the FIRST occurrence of ``old``, so an
edit whose text appeared twice landed in whichever came first and reported
"edited". Claude Code's ``Edit`` refuses: the text must occur exactly once,
or ``replace_all`` says every occurrence is meant. Now so does ``edit_file``
— zero occurrences name the nearest line, several say how many.

Also fixed by the shared engine (``harness/textedit``): an edit keeps the
file's own line endings and BOM (``write_text`` turned an LF file into CRLF on
Windows), and its undo restores the exact bytes (RAW mode).

Existing callers that relied on first-occurrence: none in the source tree
(every caller passes text that occurs once); the pins that read edit_file's
output/data (``test_chat_edit_documents_v1329``, ``test_tools``,
``test_self_dev``, ``test_workspace_writability_v1228``) still pass
unchanged — a unique edit's output and data are byte-identical.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.harness import verify
from iron_jarvis.harness.tools import MultiEditTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import EditFileTool, WriteFileTool
from iron_jarvis.tools.undo import RevertConflict, read_envelope


def _ctx(ws: Path, home: Path | None = None) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=home or ws.parent), event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


async def _edit(ws, **args):
    return await EditFileTool().execute(args, _ctx(ws))


# --------------------------------------------------------------------------- #
# edit_file — strict
# --------------------------------------------------------------------------- #


async def test_a_unique_edit_keeps_its_exact_output_and_data(ws):
    (ws / "n.txt").write_bytes(b"alpha\nbeta\n")
    res = await _edit(ws, path="n.txt", old="beta", new="gamma")
    assert res.ok, res.error
    assert res.output == "edited n.txt"
    assert res.data == {"path": "n.txt", "abs_path": str((ws / "n.txt").resolve())}
    assert (ws / "n.txt").read_bytes() == b"alpha\ngamma\n"


async def test_text_that_appears_twice_is_refused_and_nothing_changes(ws):
    """THE DEFECT. MUTATION: drop the ``count > 1`` refusal (first-occurrence
    replace) -> the file changes -> red."""
    (ws / "n.txt").write_bytes(b"x = 1\nx = 1\n")
    res = await _edit(ws, path="n.txt", old="x = 1", new="x = 2")
    assert res.ok is False
    assert "occurs 2 times in n.txt" in res.error
    assert "replace_all: true" in res.error
    assert (ws / "n.txt").read_bytes() == b"x = 1\nx = 1\n"


async def test_missing_text_is_refused_naming_the_nearest_line(ws):
    (ws / "n.py").write_text("def f():\n    retrun 1\n", encoding="utf-8")
    res = await _edit(ws, path="n.py", old="    return 1", new="    return 2")
    assert res.ok is False
    assert "`old` text was not found in n.py" in res.error
    assert "line 2: 'retrun 1'" in res.error
    assert (ws / "n.py").read_text(encoding="utf-8") == "def f():\n    retrun 1\n"


async def test_replace_all_changes_every_occurrence_and_says_how_many(ws):
    (ws / "n.txt").write_bytes(b"a b a b a\n")
    res = await _edit(ws, path="n.txt", old="a", new="z", replace_all=True)
    assert res.ok
    assert res.output == "edited n.txt (3 replacements)"
    assert res.data["replacements"] == 3
    assert (ws / "n.txt").read_bytes() == b"z b z b z\n"
    # A model may send the flag as text.
    (ws / "m.txt").write_bytes(b"q q\n")
    assert (await _edit(ws, path="m.txt", old="q", new="w", replace_all="true")).ok


@pytest.mark.parametrize(
    "args, words",
    [
        ({"old": "", "new": "x"}, "`old` is empty"),
        ({"old": "same", "new": "same"}, "`old` and `new` are the same"),
        ({"old": "a", "new": "b", "replace_all": "perhaps"}, "`replace_all` must be true or false"),
    ],
)
async def test_an_edit_that_means_nothing_is_refused(ws, args, words):
    (ws / "n.txt").write_text("same a\n", encoding="utf-8")
    res = await _edit(ws, path="n.txt", **args)
    assert res.ok is False and words in res.error
    assert (ws / "n.txt").read_text(encoding="utf-8") == "same a\n"


async def test_line_endings_are_kept_lf_and_crlf(ws):
    """``write_text`` turned an LF file into CRLF on Windows. MUTATION:
    write with ``path.write_text(new_text)`` -> the LF file changes -> red
    on Windows (and the CRLF file loses its \\r everywhere else)."""
    (ws / "lf.txt").write_bytes(b"one\ntwo\nthree\n")
    (ws / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert (await _edit(ws, path="lf.txt", old="two", new="2")).ok
    assert (await _edit(ws, path="crlf.txt", old="two\nthree", new="2\n3")).ok
    assert (ws / "lf.txt").read_bytes() == b"one\n2\nthree\n"
    assert (ws / "crlf.txt").read_bytes() == b"one\r\n2\r\n3\r\n"


async def test_a_bom_is_kept_and_the_first_line_still_matches(ws):
    (ws / "b.txt").write_bytes(b"\xef\xbb\xbfhead\nbody\n")
    res = await _edit(ws, path="b.txt", old="head", new="HEAD")
    assert res.ok, res.error
    assert (ws / "b.txt").read_bytes() == b"\xef\xbb\xbfHEAD\nbody\n"


async def test_a_file_that_is_not_utf8_text_is_refused_in_words(ws):
    (ws / "latin.txt").write_bytes(b"caf\xe9\n")
    res = await _edit(ws, path="latin.txt", old="caf", new="tea")
    assert res.ok is False and "is not UTF-8 text" in res.error
    (ws / "bin.dat").write_bytes(b"\x00\x01ab")
    res = await _edit(ws, path="bin.dat", old="ab", new="cd")
    assert res.ok is False and "binary file" in res.error


async def test_the_undo_restores_the_exact_bytes_and_refuses_on_drift(tmp_path, ws):
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = EditFileTool()
    (ws / "lf.txt").write_bytes(b"keep\nchange me\nkeep\n")
    args = {"path": "lf.txt", "old": "change me", "new": "changed"}
    undo = await tool.capture_undo(args, ctx)
    assert undo["kind"] == "file_restore" and read_envelope(undo)["mode"] == "raw"
    assert (await tool.execute(args, ctx)).ok
    back = await tool.revert(undo, ctx)
    assert back.ok, back.error
    assert (ws / "lf.txt").read_bytes() == b"keep\nchange me\nkeep\n"
    # Drift: someone changed the file after the edit -> the undo refuses.
    undo2 = await tool.capture_undo(args, ctx)
    assert (await tool.execute(args, ctx)).ok
    (ws / "lf.txt").write_bytes(b"someone else\n")
    with pytest.raises(RevertConflict):
        await tool.revert(undo2, ctx)


async def test_an_ambiguous_edit_journals_nothing(platform, ws):
    """Through the REAL registry: a refused edit leaves no UndoJournal row."""
    ctx = ToolContext(
        workspace=ws, session_id="s-amb", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    (ws / "n.txt").write_text("dup\ndup\n", encoding="utf-8")
    res = await platform.registry.invoke(
        "edit_file", {"path": "n.txt", "old": "dup", "new": "x"}, ctx, platform.permissions
    )
    assert res.ok is False
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(UndoJournal).where(UndoJournal.session_id == "s-amb")))
    assert rows == []
    ok = await platform.registry.invoke(
        "edit_file", {"path": "n.txt", "old": "dup", "new": "x", "replace_all": True}, ctx, platform.permissions
    )
    assert ok.ok
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(UndoJournal).where(UndoJournal.session_id == "s-amb")))
    assert len(rows) == 1 and rows[0].kind == "file_restore"


# --------------------------------------------------------------------------- #
# multi_edit — all or nothing, one undo
# --------------------------------------------------------------------------- #


async def test_multi_edit_applies_in_order_on_the_result_so_far(ws):
    (ws / "m.py").write_text("def f():\n    return 1\n\nx = f()\n", encoding="utf-8")
    res = await MultiEditTool().execute(
        {"path": "m.py", "edits": [
            {"old": "def f", "new": "def g"},
            {"old": "x = f()", "new": "x = g()"},
            {"old": "return 1", "new": "return 2"},
        ]},
        _ctx(ws),
    )
    assert res.ok, res.error
    assert res.output.startswith("edited m.py (3 edits, 3 replacements)")
    assert (ws / "m.py").read_text(encoding="utf-8") == "def g():\n    return 2\n\nx = g()\n"


async def test_multi_edit_is_all_or_nothing_and_names_the_failing_edit(ws):
    """MUTATION: write after each edit instead of once at the end -> edit 1
    lands though edit 2 failed -> red."""
    original = b"alpha\nbeta\n"
    (ws / "m.txt").write_bytes(original)
    res = await MultiEditTool().execute(
        {"path": "m.txt", "edits": [{"old": "alpha", "new": "A"}, {"old": "missing", "new": "M"}]},
        _ctx(ws),
    )
    assert res.ok is False
    assert res.error.startswith("edit 2 of 2: `old` text was not found in m.txt")
    assert "Nothing was changed" in res.error
    assert (ws / "m.txt").read_bytes() == original


async def test_multi_edit_one_undo_restores_the_whole_call(tmp_path, ws):
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    (ws / "m.txt").write_bytes(b"a\nb\nc\n")
    args = {"path": "m.txt", "edits": json.dumps([{"old": "a", "new": "1"}, {"old": "c", "new": "3"}])}
    tool = MultiEditTool()
    undo = await tool.capture_undo(args, ctx)
    assert undo is not None and undo["kind"] == "file_restore"
    assert (await tool.execute(args, ctx)).ok
    assert (ws / "m.txt").read_bytes() == b"1\nb\n3\n"
    assert (await tool.revert(undo, ctx)).ok
    assert (ws / "m.txt").read_bytes() == b"a\nb\nc\n"


@pytest.mark.parametrize(
    "edits, words",
    [
        ([], "`edits` is empty"),
        ("not json", "`edits` must be a list"),
        ([{"old": "a"}], "edit 1 must be an object with `old` and `new`"),
        ([{"old": "a", "new": "b"}] * 101, "at most 100 per call"),
    ],
)
async def test_multi_edit_refuses_a_bad_edit_list(ws, edits, words):
    (ws / "m.txt").write_text("a\n", encoding="utf-8")
    res = await MultiEditTool().execute({"path": "m.txt", "edits": edits}, _ctx(ws))
    assert res.ok is False and words in res.error


async def test_multi_edit_outside_the_workspace_is_refused_by_the_registry(platform, ws):
    ctx = ToolContext(
        workspace=ws, session_id="s-esc", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    res = await platform.registry.invoke(
        "multi_edit", {"path": "../x.txt", "edits": [{"old": "a", "new": "b"}]}, ctx, platform.permissions
    )
    assert res.ok is False and "escapes the session workspace" in res.error


# --------------------------------------------------------------------------- #
# Verify after edit
# --------------------------------------------------------------------------- #


async def test_a_python_edit_carries_the_check_line(ws):
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    ok = await _edit(ws, path="a.py", old="x = 1", new="x = 2")
    assert ok.output == "edited a.py\nCheck: the file parses."
    assert ok.data["check"] == "Check: the file parses."
    bad = await _edit(ws, path="a.py", old="x = 2", new="x = (")
    assert bad.ok is True, "a check never fails the write"
    assert bad.output.startswith("edited a.py\nCheck: SyntaxError at line 1:")
    assert (ws / "a.py").read_text(encoding="utf-8") == "x = (\n"


async def test_a_text_file_gets_no_check_line(ws):
    (ws / "a.txt").write_text("x\n", encoding="utf-8")
    res = await _edit(ws, path="a.txt", old="x", new="y")
    assert res.output == "edited a.txt" and "check" not in res.data


async def test_write_file_checks_json_and_toml(ws):
    w = WriteFileTool()
    res = await w.execute({"path": "c.json", "content": '{"a": 1,}'}, _ctx(ws))
    assert res.ok and "Check: JSON error at line 1" in res.output
    res = await w.execute({"path": "c.toml", "content": "a = [\n"}, _ctx(ws))
    assert res.ok and res.output.splitlines()[-1].startswith("Check: TOML error:")
    res = await w.execute({"path": "ok.json", "content": "[1, 2]"}, _ctx(ws))
    assert res.output == "wrote 6 bytes to ok.json\nCheck: the file parses."
    plain = await w.execute({"path": "n.md", "content": "# hi"}, _ctx(ws))
    assert plain.output == "wrote 4 bytes to n.md" and "check" not in plain.data


async def test_a_check_that_raises_never_fails_the_write(ws, monkeypatch):
    """MUTATION: drop the try/except in ``verify.check_line`` -> the edit
    raises -> red."""

    def boom(_path):
        raise RuntimeError("checker broke")

    monkeypatch.setattr(verify, "check_file", boom)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    res = await _edit(ws, path="a.py", old="x = 1", new="x = 2")
    assert res.ok and res.output == "edited a.py"


async def test_the_check_runs_off_the_event_loop(ws, monkeypatch):
    seen: list[int] = []
    real = verify.check_file

    def spy(path):
        seen.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr(verify, "check_file", spy)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert (await _edit(ws, path="a.py", old="x = 1", new="x = 3")).ok
    assert seen and seen[0] != threading.get_ident()


def test_a_big_file_is_not_checked(ws):
    # v1.367.0 (moved on purpose): a PYTHON file now stops at
    # MAX_PY_CHECK_BYTES with no line at all (tests/test_build_sessions_v1367
    # pins it); the 2 MB line is every other checked type's.
    (ws / "big.json").write_bytes(b" " * (verify.MAX_CHECK_BYTES + 1))
    assert verify.check_file(ws / "big.json") == "Check: skipped — the file is over 2 MB"
    assert verify.check_file(ws / "x.txt") is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_node_checks_javascript(ws):
    (ws / "ok.js").write_text("const a = 1;\n", encoding="utf-8")
    (ws / "bad.js").write_text("const a = ;\n", encoding="utf-8")
    assert verify.check_file(ws / "ok.js") == verify.PARSES
    assert verify.check_file(ws / "bad.js").startswith("Check: SyntaxError at line 1")


def test_without_node_a_js_file_gets_no_line(ws, monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda _name: None)
    (ws / "ok.js").write_text("const a = 1;\n", encoding="utf-8")
    assert verify.check_file(ws / "ok.js") is None


def test_the_python_check_writes_no_pyc(ws):
    (ws / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert verify.check_file(ws / "m.py") == verify.PARSES
    assert not (ws / "__pycache__").exists()
