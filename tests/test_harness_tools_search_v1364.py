"""v1.364.0 (IJ harness Phase 1): ``glob`` (Claude Code's Glob) and
``grep``'s full form (Claude Code's Grep).

``grep`` with only ``pattern``/``path`` runs its ORIGINAL code and prints
exactly what it printed; any new argument (output_mode, context lines,
case_insensitive, glob/type filters, head_limit/offset, multiline,
line_numbers — and Claude Code's -A/-B/-C/-i/-n spellings) switches to the
new engine. Both walks are the shared bounded walker (heavy folders pruned,
a file cap, a deadline), off the event loop, and every hole is SAID.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import globmatch as G
from iron_jarvis.harness import search as S
from iron_jarvis.harness.tools import GlobTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import GrepTool


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=ws.parent), event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


def _touch(path: Path, text: str, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


async def _grep(ws, **args):
    return await GrepTool().execute(args, _ctx(ws))


async def _glob(ws, **args):
    return await GlobTool().execute(args, _ctx(ws))


# --------------------------------------------------------------------------- #
# The pattern rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel, pattern, hit",
    [
        ("a.py", "*.py", True),
        ("src/a.py", "*.py", False),  # '*' stays in one folder
        ("src/a.py", "**/*.py", True),
        ("a.py", "**/*.py", True),  # '**/' may be no folder at all
        ("src/x/y/a.ts", "src/**/*.ts", True),
        ("lib/a.ts", "src/**/*.ts", False),
        ("a.tsx", "*.{ts,tsx}", True),
        ("a.js", "*.{ts,tsx}", False),
        ("a1.txt", "a?.txt", True),
        ("ab.txt", "a[!b].txt", False),
        ("ac.txt", "a[!b].txt", True),
        ("src/deep/file", "src/**", True),
    ],
)
def test_glob_reading(rel, pattern, hit):
    assert G.matches_path(rel, pattern) is hit


def test_the_grep_filter_reads_a_bare_pattern_by_name_at_any_depth():
    assert G.matches_filter("deep/down/a.py", "*.py") is True
    assert G.matches_filter("deep/down/a.py", "deep/*.py") is False
    assert G.matches_filter("deep/down/a.py", "deep/**/*.py") is True


@pytest.mark.parametrize("pattern", ["../*.py", "/etc/*", "C:/x/*", "a/../../b", ""])
def test_a_pattern_cannot_climb_out(pattern):
    with pytest.raises(G.GlobError):
        G.normalize_pattern(pattern)


def test_brace_expansion_is_bounded():
    with pytest.raises(G.GlobError, match="more than 64"):
        G.compile_glob("{a,b,c,d}{a,b,c,d}{a,b,c,d}{a,b,c,d}")


# --------------------------------------------------------------------------- #
# glob
# --------------------------------------------------------------------------- #


async def test_glob_lists_newest_first_relative_to_the_workspace(ws):
    now = time.time()
    _touch(ws / "src" / "old.py", "x", now - 300)
    _touch(ws / "src" / "new.py", "x", now - 10)
    _touch(ws / "src" / "mid.py", "x", now - 100)
    _touch(ws / "src" / "note.md", "x", now)
    res = await _glob(ws, pattern="**/*.py")
    assert res.ok and res.output == "src/new.py\nsrc/mid.py\nsrc/old.py"
    sub = await _glob(ws, pattern="*.py", path="src")
    assert sub.output == "src/new.py\nsrc/mid.py\nsrc/old.py", "paths stay workspace-relative"


async def test_glob_caps_at_100_and_says_how_many_more(ws):
    """MUTATION: drop the cap -> 130 lines -> red."""
    for i in range(130):
        _touch(ws / f"f{i:03}.txt", "x")
    res = await _glob(ws, pattern="*.txt")
    lines = res.output.split("\n")
    assert len([ln for ln in lines if ln.endswith(".txt")]) == 100
    assert "[30 more not shown — newest 100 of 130;" in res.output
    assert res.data == {"count": 130, "shown": 100, "truncated": True}


async def test_glob_prunes_heavy_folders_and_reports_nothing_found(ws):
    _touch(ws / "node_modules" / "pkg" / "a.js", "x")
    _touch(ws / ".git" / "objects" / "b.js", "x")
    _touch(ws / "app" / "c.js", "x")
    res = await _glob(ws, pattern="**/*.js")
    assert res.output == "app/c.js"
    none = await _glob(ws, pattern="**/*.rs")
    assert none.ok and none.output == "No files found."


async def test_glob_starts_at_a_literal_folder_even_a_hidden_one(ws):
    _touch(ws / ".github" / "workflows" / "ci.yml", "x")
    res = await _glob(ws, pattern=".github/workflows/*.yml")
    assert res.output == ".github/workflows/ci.yml"


async def test_glob_refusals(ws):
    bad = await _glob(ws, pattern="../*")
    assert bad.ok is False and "climb out" in bad.error
    missing = await _glob(ws, pattern="*", path="nope")
    assert missing.ok is False and "no such folder" in missing.error
    with pytest.raises(PermissionError):
        await _glob(ws, pattern="*", path="../..")


async def test_glob_walks_off_the_event_loop(ws, monkeypatch):
    seen: list[int] = []
    real = S.glob_files

    def spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    import iron_jarvis.harness.tools as T

    monkeypatch.setattr(T, "glob_files", spy)
    _touch(ws / "a.txt", "x")
    assert (await _glob(ws, pattern="*.txt")).ok
    assert seen and seen[0] != threading.get_ident()


# --------------------------------------------------------------------------- #
# grep — the old form is byte-identical
# --------------------------------------------------------------------------- #


async def test_grep_with_pattern_and_path_only_prints_exactly_as_before(ws):
    """The original output: ``rel:N: <stripped line>``, no notes. MUTATION:
    route every call through the new engine -> 'a.py:1:    def f():' -> red."""
    _touch(ws / "a.py", "    def f():\n        return 1\n")
    _touch(ws / "sub" / "b.py", "def g(): pass\n")
    res = await _grep(ws, pattern="def")
    assert res.ok
    assert res.output == "a.py:1: def f():\nsub/b.py:1: def g(): pass"
    assert res.data == {"matches": 2, "truncated": False, "skipped_unreadable": 0, "skipped_oversize": 0}
    only = await _grep(ws, pattern="def", path="sub")
    assert only.output == "sub/b.py:1: def g(): pass"


# --------------------------------------------------------------------------- #
# grep — the full form
# --------------------------------------------------------------------------- #


async def test_content_mode_keeps_indentation_and_numbers_lines(ws):
    _touch(ws / "a.py", "x = 1\n    def f():\n        return x\n")
    res = await _grep(ws, pattern="def", output_mode="content")
    assert res.output == "a.py:2:    def f():"
    plain = await _grep(ws, pattern="def", line_numbers=False)
    assert plain.output == "a.py:    def f():"


async def test_context_lines_and_group_separators(ws):
    _touch(ws / "c.txt", "\n".join(f"l{i}" for i in range(1, 11)) + "\n")
    res = await _grep(ws, pattern="^l(3|8)$", context=1)
    assert res.output == "c.txt-2-l2\nc.txt:3:l3\nc.txt-4-l4\n--\nc.txt-7-l7\nc.txt:8:l8\nc.txt-9-l9"
    # Claude Code's spellings are accepted beside the plain ones.
    after = await _grep(ws, pattern="^l3$", **{"-A": 2})
    assert after.output == "c.txt:3:l3\nc.txt-4-l4\nc.txt-5-l5"
    before = await _grep(ws, pattern="^l3$", before_context=1)
    assert before.output == "c.txt-2-l2\nc.txt:3:l3"


async def test_files_with_matches_is_newest_first_and_count_counts(ws):
    # Names sort the OTHER way from age, so the order proves it is by mtime.
    # MUTATION: sort files_with_matches by name -> red.
    now = time.time()
    _touch(ws / "a_old.txt", "hit\nhit\n", now - 100)
    _touch(ws / "z_new.txt", "hit\n", now)
    _touch(ws / "none.txt", "miss\n", now)
    files = await _grep(ws, pattern="hit", output_mode="files_with_matches")
    assert files.output == "z_new.txt\na_old.txt"
    count = await _grep(ws, pattern="hit", output_mode="count")
    assert count.output == "a_old.txt:2\nz_new.txt:1\n\n3 matches in 2 files."


async def test_case_glob_and_type_filters(ws):
    _touch(ws / "a.py", "TODO one\n")
    _touch(ws / "deep" / "b.py", "todo two\n")
    _touch(ws / "c.md", "todo three\n")
    ci = await _grep(ws, pattern="todo", case_insensitive=True, output_mode="files_with_matches")
    assert set(ci.output.split("\n")) == {"a.py", "deep/b.py", "c.md"}
    py = await _grep(ws, pattern="todo", **{"-i": True}, glob="*.py", output_mode="files_with_matches")
    assert set(py.output.split("\n")) == {"a.py", "deep/b.py"}
    md = await _grep(ws, pattern="todo", type="md")
    assert md.output == "c.md:1:todo three"
    bad = await _grep(ws, pattern="todo", type="cobol")
    assert bad.ok is False and "unknown file type 'cobol'" in bad.error


async def test_head_limit_and_offset_page_and_say_so(ws):
    """MUTATION: ignore head_limit -> all 10 lines -> red."""
    _touch(ws / "n.txt", "".join(f"hit {i}\n" for i in range(1, 11)))
    first = await _grep(ws, pattern="hit", head_limit=3)
    assert first.output.split("\n")[:3] == ["n.txt:1:hit 1", "n.txt:2:hit 2", "n.txt:3:hit 3"]
    assert "[showing lines 1–3 of 10; use offset to see more]" in first.output
    page = await _grep(ws, pattern="hit", head_limit=3, offset=3)
    assert page.output.startswith("n.txt:4:hit 4\nn.txt:5:hit 5\nn.txt:6:hit 6")
    assert "[showing lines 4–6 of 10; use offset to see more]" in page.output
    past = await _grep(ws, pattern="hit", offset=50)
    assert past.output == "[offset 50 is past the end: there are 10 lines]"
    default = await _grep(ws, pattern="hit", output_mode="content")
    assert default.data["entries"] == 10 and default.data["shown"] == 10


async def test_the_default_head_limit_is_250(ws):
    _touch(ws / "n.txt", "".join("hit\n" for _ in range(300)))
    res = await _grep(ws, pattern="hit", output_mode="content")
    assert res.data["shown"] == 250
    assert "[showing lines 1–250 of 300; use offset to see more]" in res.output


async def test_multiline_matches_across_lines(ws):
    _touch(ws / "m.py", "def f(\n    a,\n    b):\n    pass\n")
    single = await _grep(ws, pattern=r"f\(.*b\)", output_mode="count")
    assert single.output == "No matches found."
    multi = await _grep(ws, pattern=r"f\(.*b\)", multiline=True)
    assert multi.output == "m.py:1:def f(\nm.py:2:    a,\nm.py:3:    b):"


async def test_new_form_reports_oversize_and_bad_arguments(ws, monkeypatch):
    monkeypatch.setattr(S, "GREP_MAX_FILE_BYTES", 10)
    _touch(ws / "big.txt", "hit " * 10)
    _touch(ws / "small.txt", "hit\n")
    res = await _grep(ws, pattern="hit", output_mode="files_with_matches")
    assert res.output.startswith("small.txt")
    assert "1 file(s) skipped as oversize" in res.output and res.data["skipped_oversize"] == 1
    for args, words in [
        ({"output_mode": "lines"}, "output_mode must be one of"),
        ({"context": "lots"}, "`context` must be a whole number"),
        ({"head_limit": -1}, "`head_limit` must be at least 0"),
        ({"glob": "../*"}, "bad glob"),
    ]:
        bad = await _grep(ws, pattern="hit", **args)
        assert bad.ok is False and words in bad.error, (args, bad.error)
    regex = await _grep(ws, pattern="(", output_mode="count")
    assert regex.ok is False and "bad regex" in regex.error


async def test_new_grep_searches_off_the_event_loop(ws, monkeypatch):
    seen: list[int] = []
    real = S.grep

    def spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(S, "grep", spy)
    _touch(ws / "a.txt", "hit\n")
    assert (await _grep(ws, pattern="hit", output_mode="count")).ok
    assert seen and seen[0] != threading.get_ident()


async def test_a_truncated_walk_is_said(ws, monkeypatch):
    import iron_jarvis.tools.builtins as B

    monkeypatch.setattr(S, "GREP_MAX_FILES", 2)
    for i in range(5):
        _touch(ws / f"f{i}.txt", "hit\n")
    res = await _grep(ws, pattern="hit", output_mode="count")
    assert "[search truncated — stopped at 2 files." in res.output
    assert res.data["truncated"] is True
    assert B._walk_files  # the shared bounded walker is the one used
