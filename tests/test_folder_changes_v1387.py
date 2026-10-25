"""THE FOLDER'S WITNESS for the finish check (``harness/folder_changes``).

The part 3 Phase 2 measurement lost ``hard-expression-evaluator`` twice: the
model wrote ``calc.py`` with a bash heredoc, the undo journal showed no file
change, and the finish check never ran. These pins cover the snapshot and the
comparison on their own: what is walked, what is never a change (caches, a
tool's droppings, what ``.gitignore`` ignores), the caps and their honest
fallback, moves, and the carried-on reply's kept snapshot. The lanes are
pinned in ``tests/test_finish_shell_writes_v1387.py``; the replays in
``tests/test_finish_shell_replay_v1387.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iron_jarvis.harness import finish as F
from iron_jarvis.harness import folder_changes as FC


def _put(root: Path, rel: str, data: bytes = b"x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# --------------------------------------------------------------------------- #
# the walk
# --------------------------------------------------------------------------- #


def test_a_snapshot_holds_each_files_size_and_time(tmp_path):
    a = _put(tmp_path, "a.py", b"X = 1\n")
    _put(tmp_path, "pkg/b.txt", b"hello")
    snap = FC.take(tmp_path)
    assert snap.usable and snap.problem == ""
    assert set(snap.files) == {"a.py", "pkg/b.txt"}
    st = a.stat()
    assert snap.files["a.py"] == (st.st_size, st.st_mtime_ns)
    assert snap.root == str(tmp_path.resolve())


def test_the_walk_prunes_what_list_files_prunes_and_caches(tmp_path):
    """list_files' own pruning (its heavy folders and every folder whose name
    starts with "."), plus caches and build output a test run leaves."""
    keep = ["app.py", "src/m.py", ".env", "coverage/index.js"]
    never = [
        "__pycache__/app.cpython-312.pyc", ".pytest_cache/v/cache/lastfailed", ".mypy_cache/x.json",
        ".ruff_cache/x", "node_modules/.cache/x", "node_modules/lib/i.js", ".next/build-manifest.json",
        ".git/HEAD", ".venv/pyvenv.cfg", "venv/x", "dist/out.js", "build/out.js", "target/x",
        "htmlcov/index.html", "pkg.egg-info/PKG-INFO", "stray.pyc", "m.pyo", ".coverage",
        ".coverage.host.123.456", "coverage.xml", "lcov.info", "coverage/lcov.info",
        "coverage/coverage-final.json", "coverage/lcov-report/index.html", "Thumbs.db", ".DS_Store",
        "desktop.ini", ".ironjarvis/harness/finish.toml",
        # a project's own checkers (part 3 Phase 3 runs them after an edit):
        # tsc's build info (written under --noEmit too when the project sets
        # `incremental` / `composite`), eslint's / stylelint's caches, the
        # mypy daemon's status file, mypy's and ruff's cache folders
        "tsconfig.tsbuildinfo", "web/app.tsbuildinfo", ".eslintcache", ".stylelintcache",
        ".dmypy.json", ".mypy_cache/3.12/app.data.json",
    ]
    for rel in keep + never:
        _put(tmp_path, rel)
    snap = FC.take(tmp_path)
    assert sorted(snap.files) == sorted(keep)


def test_a_folder_the_gitignore_ignores_is_never_walked(tmp_path):
    (tmp_path / ".gitignore").write_text("data/\n*.log\n", encoding="utf-8")
    _put(tmp_path, "data/big.bin")
    _put(tmp_path, "run.log")
    _put(tmp_path, "app.py")
    seen: list[str] = []

    class Spy(F.GitIgnore):
        def ignored(self, rel_posix, *, is_dir=False):
            seen.append(f"{rel_posix}{'/' if is_dir else ''}")
            return super().ignored(rel_posix, is_dir=is_dir)

    snap = FC.take(tmp_path, ignore=Spy(tmp_path))
    # The ignored FOLDER is not walked; an ignored FILE is recorded (the
    # comparison leaves it out — only the few differing files are judged).
    assert "data/big.bin" not in snap.files and "run.log" in snap.files
    assert seen == ["data/"]


def test_a_gitignore_dir_rule_matches_a_folder_asked_about_as_one(tmp_path):
    (tmp_path / ".gitignore").write_text("out/\nnotes\n", encoding="utf-8")
    ig = F.GitIgnore(tmp_path)
    assert ig.ignored("out", is_dir=True) is True
    assert ig.ignored("out") is False  # a FILE named out is not matched by `out/`
    assert ig.ignored("out/x.js") is True
    assert ig.ignored("notes", is_dir=True) is True and ig.ignored("notes") is True
    assert ig.ignored("src", is_dir=True) is False


def test_a_folder_with_more_files_than_the_cap_cannot_be_compared(tmp_path):
    for i in range(4):
        _put(tmp_path, f"f{i}.txt")
    snap = FC.take(tmp_path, limit=3)
    assert not snap.usable and snap.files == {}
    assert snap.problem == "this folder has more than 3 files to compare"
    assert FC.take(tmp_path, limit=4).usable


def test_the_caps_are_list_files_own(monkeypatch):
    from iron_jarvis.tools import builtins as B

    assert FC._caps() == (B._MAX_WALK_ENTRIES, B._WALK_DEADLINE_S)
    assert FC._skip_dirs() == B._SKIP_DIRS


def test_a_walk_past_the_deadline_cannot_be_compared(tmp_path):
    _put(tmp_path, "a/x.txt")
    _put(tmp_path, "b/y.txt")
    ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
    snap = FC.take(tmp_path, deadline_s=1.0, clock=lambda: next(ticks))
    assert not snap.usable and snap.files == {}
    assert snap.problem == "reading this folder took longer than 1 s"


def test_a_deadline_of_zero_means_no_time_at_all(tmp_path):
    """`>=`, as list_files: Windows' clock can read 0.0 elapsed for a small
    tree, and `>` would then mean no deadline."""
    _put(tmp_path, "x.txt")
    snap = FC.take(tmp_path, deadline_s=0.0, clock=lambda: 7.0)
    assert snap.problem == "reading this folder took longer than 0 s"


def test_an_unreadable_or_missing_folder_says_so_and_never_raises(tmp_path):
    snap = FC.take(tmp_path / "nope")
    assert not snap.usable and snap.problem == FC.PROBLEM_UNREADABLE
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    assert FC.take(tmp_path / "f.txt").problem == FC.PROBLEM_UNREADABLE


def test_a_protected_folder_is_never_walked(tmp_path, monkeypatch):
    import iron_jarvis.core.fs_policy as P

    monkeypatch.setattr(P, "fs_read_ok", lambda path: (False, "protected"))
    _put(tmp_path, "a.txt")
    assert FC.take(tmp_path).problem == FC.PROBLEM_UNREADABLE


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows folder link")
def test_a_junction_is_never_followed_out_of_the_folder(tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    _put(outside, "secret.txt")
    root = tmp_path / "root"
    _put(root, "a.txt")
    _winapi.CreateJunction(str(outside), str(root / "link"))
    snap = FC.take(root)
    assert sorted(snap.files) == ["a.txt"]


def test_a_subfolder_that_cannot_be_read_is_left_out_of_both_sides(tmp_path, monkeypatch):
    _put(tmp_path, "ok/a.txt")
    _put(tmp_path, "locked/b.txt")
    real = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(FC.os, "scandir", scandir)
    before = FC.take(tmp_path)
    monkeypatch.setattr(FC.os, "scandir", real)
    after = FC.take(tmp_path)
    assert before.skipped == frozenset({"locked"}) and before.usable
    # b.txt was not seen the first time: never "created".
    assert FC.compare(before, after).created == []


# --------------------------------------------------------------------------- #
# the comparison
# --------------------------------------------------------------------------- #


def _snap(files: dict, **kw) -> FC.Snapshot:
    return FC.Snapshot(root="R", files=files, **kw)


def test_created_changed_and_deleted():
    before = _snap({"a.py": (1, 10), "b.py": (2, 20), "c.py": (3, 30)})
    after = _snap({"a.py": (1, 10), "b.py": (2, 21), "d.py": (4, 40)})
    got = FC.compare(before, after)
    assert (got.created, got.changed, got.deleted, got.moved) == (["d.py"], ["b.py"], ["c.py"], [])
    assert got.any and sorted(got.paths()) == ["b.py", "c.py", "d.py"]


def test_a_same_size_change_is_seen_by_its_time_and_a_same_time_change_by_its_size():
    before = _snap({"a": (5, 100), "b": (5, 100)})
    after = _snap({"a": (5, 101), "b": (6, 100)})
    assert FC.compare(before, after).changed == ["a", "b"]


def test_a_move_keeps_name_size_and_time_and_is_neither_made_nor_gone():
    """A watcher sending inbox/a.csv to done/ (os.replace keeps the time):
    listing done/a.csv as made and inbox/a.csv as gone would ask the model to
    put back what the task moved."""
    before = _snap({"inbox/a.csv": (9, 77), "keep.txt": (1, 1)})
    after = _snap({"done/a.csv": (9, 77), "keep.txt": (1, 1)})
    got = FC.compare(before, after)
    assert got.moved == [("inbox/a.csv", "done/a.csv")]
    assert got.created == [] and got.deleted == [] and got.any
    # A different file under the same name is no move.
    got = FC.compare(before, _snap({"done/a.csv": (9, 78), "keep.txt": (1, 1)}))
    assert got.moved == [] and got.created == ["done/a.csv"] and got.deleted == ["inbox/a.csv"]


def test_ignored_files_and_git_are_never_changes(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    before = _snap({})
    after = _snap({"run.log": (1, 1), "sub/x.log": (1, 1), ".git/index": (1, 1), "app.py": (1, 1)})
    got = FC.compare(before, after, F.GitIgnore(tmp_path))
    assert got.created == ["app.py"]


def test_a_snapshot_that_cannot_be_compared_compares_to_nothing():
    whole = _snap({"a": (1, 1)})
    broken = FC.unavailable("R", FC.PROBLEM_TOO_MANY.format(limit=3))
    assert not FC.compare(whole, broken).any and not FC.compare(broken, whole).any


# --------------------------------------------------------------------------- #
# which tools can change a file without the journal
# --------------------------------------------------------------------------- #


def test_every_mutating_tool_is_placed_journaled_or_not():
    """A new tool on the registry's mutating list must be placed: does it
    write the undo journal (the finish check reads it there) or not (the
    folder comparison must count after it)?"""
    from iron_jarvis.tools.registry import _MUTATING_TOOLS

    assert set(_MUTATING_TOOLS) <= FC.UNJOURNALED_TOOLS | FC.JOURNALED_TOOLS, sorted(
        set(_MUTATING_TOOLS) - FC.UNJOURNALED_TOOLS - FC.JOURNALED_TOOLS
    )
    assert not FC.UNJOURNALED_TOOLS & FC.JOURNALED_TOOLS


@pytest.mark.parametrize("name, custom, want", [
    ("shell", (), True), ("process_start", (), True), ("process_write", (), True),
    ("delegate", (), True), ("spawn_agent", (), True), ("mcp__fs__write", (), True),
    ("my_tool", ("my_tool",), True), ("my_tool", (), False),
    ("write_file", (), False), ("apply_patch", (), False), ("read_file", (), False),
    ("process_read", (), False), ("web_fetch", (), False), ("page_click", (), False),
])
def test_which_tools_change_files_without_the_journal(name, custom, want):
    assert FC.unjournaled(name, frozenset(custom)) is want


# --------------------------------------------------------------------------- #
# a carried-on reply's first snapshot
# --------------------------------------------------------------------------- #


def test_a_session_keeps_its_newest_replys_first_snapshot(tmp_path):
    FC.forget_kept()
    try:
        snap = FC.take(tmp_path)
        FC.keep("build:s1", "t1", snap, {"x": b"1"})
        assert FC.kept("build:s1", "t1", tmp_path) == (snap, {"x": b"1"})
        assert FC.kept("build:s1", "t2", tmp_path) is None  # another reply's
        assert FC.kept("build:s2", "t1", tmp_path) is None  # another session's
        assert FC.kept("build:s1", "t1", tmp_path / "other") is None  # another folder
        FC.keep("build:s1", "t2", snap)
        assert FC.kept("build:s1", "t1", tmp_path) is None  # replaced by the newer reply
        FC.keep("build:s1", "", snap)  # no turn id: nothing kept
        assert FC.kept("build:s1", "", tmp_path) is None
    finally:
        FC.forget_kept()


def test_only_a_few_sessions_are_kept(tmp_path):
    FC.forget_kept()
    try:
        snap = FC.take(tmp_path)
        for i in range(FC.MAX_KEPT_REPLIES + 2):
            FC.keep(f"build:s{i}", "t", snap)
        assert FC.kept("build:s0", "t", tmp_path) is None and FC.kept("build:s1", "t", tmp_path) is None
        assert FC.kept(f"build:s{FC.MAX_KEPT_REPLIES + 1}", "t", tmp_path) is not None
    finally:
        FC.forget_kept()


# --------------------------------------------------------------------------- #
# which settled calls open the comparison
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name, ok, data, want", [
    ("shell", True, {"returncode": 0}, True),
    ("shell", False, {"returncode": 1}, True),          # it ran (and failed): it may have written
    ("shell", False, {}, False),                        # the tool refused it before any program ran
    ("shell", False, {"timed_out": True}, True),
    ("process_start", True, {"id": "p1", "running": True}, True),
    ("process_start", False, {}, False),                # never started
    ("process_write", True, {"id": "p1"}, True),
    ("delegate", False, {}, True),                      # a hand-off that failed may have written first
    ("mcp__fs__write_file", True, {}, True),
    ("read_file", True, {}, False),
    ("write_file", True, {}, False),                    # the journal already has it
])
def test_which_settled_calls_open_the_folder_comparison(name, ok, data, want):
    from iron_jarvis.tools.base import ToolResult

    assert F._wrote_unjournaled(name, ToolResult(ok=ok, output="", data=data)) is want
