"""v1.516.0 (second review S4, T9, and the git guard's file checks left open
by group 11): a coding session never stalls the app.

S4. Python's ``re`` cannot be interrupted, and it holds the interpreter lock
while it matches: one pattern that backtracks without end (``(a+)+$`` against
a long run of ``a`` then ``b``) froze the WHOLE daemon, event loop included,
even from a worker thread. The search's time limit was read only between
files, so it never fired. A pattern that can backtrack now runs in its own
short-lived program (``core/procs``), killed at the limit, and the answer says
plainly that the search was stopped, on which file, and why. A plain pattern
(no repeat, no group) still searches in this process, as before.

T9. ``process_start`` worked out its program's environment (stats of the main
folder's ``.venv`` and ``node_modules``) on the event loop. It runs in a worker
now, bounded; past the bound nothing starts and the reply says why.

The git guard's file checks (an existence check for a ``reset`` /
``checkout`` word, the user's git config for aliases) ran on the event loop in
every command tool's check. They run in a worker now, bounded, with the same
answers; past the bound the check fails closed (it asks, or refuses).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as G
from iron_jarvis.harness import process as P
from iron_jarvis.harness import search as S
from iron_jarvis.sandbox import shell_tool as ST
from iron_jarvis.tools import builtins as BI
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import GrepTool

#: A run of ``a`` then ``b``: ``(a+)+$`` tries every split of the run before
#: it gives up (2^29 of them, minutes of matching on this PC).
RUNAWAY_LINE = "a" * 29 + "b\n"
RUNAWAY = "(a+)+$"


def _ctx(ws: Path, session: str = "s-no-stall") -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r",
        config=SimpleNamespace(home=ws.parent, sandbox={"modify_env": "allow"}),
        event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


@pytest.fixture(autouse=True)
def _clean_table():
    yield
    P.end_session(None)


def _touch(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


async def _with_loop_watch(coro):
    """Run ``coro`` while a task notes the longest time the event loop went
    without running it. Returns ``(result, longest gap in seconds)``."""
    gaps: list[float] = []
    done = False

    async def watch():
        last = time.monotonic()
        while not done:
            await asyncio.sleep(0)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(watch())
    try:
        result = await coro
    finally:
        done = True
        await task
    return result, max(gaps or [0.0])


@pytest.fixture
def runaway_tree(ws, monkeypatch):
    """A file that matches at once, then a file the runaway pattern sticks on,
    under a short search limit."""
    monkeypatch.setattr(S, "SEARCH_DEADLINE_S", 0.5)
    monkeypatch.setattr(BI, "_WALK_DEADLINE_S", 0.5)
    # The program's start is allowed on top of the limit (a cold start on a
    # busy machine); the test waits for the kill, never sleeps.
    monkeypatch.setattr(S, "SEARCH_START_S", 5.0, raising=False)
    _touch(ws / "a_first.txt", "aaa\n")
    _touch(ws / "big.txt", RUNAWAY_LINE)
    return ws


# --------------------------------------------------------------------------- #
# S4: one pattern cannot freeze the app
# --------------------------------------------------------------------------- #


def test_s4_a_runaway_pattern_on_one_file_is_cut_short_and_said(runaway_tree):
    """The engine itself: the file it stuck on is named, the match found
    before it is kept. MUTATION: run the pattern in this process again (the
    time limit read only between files) -> no 'still running on big.txt'."""
    opts = S.GrepOptions(pattern=RUNAWAY, output_mode="content")
    report = S.grep(runaway_tree, runaway_tree.resolve(), opts)
    assert "still running on big.txt" in report.walk_truncated, report.walk_truncated
    assert report.entries == ["a_first.txt:1:aaa"]
    text = S.render_grep(report, opts)
    assert "the pattern was still running on big.txt" in text
    assert "This did NOT cover every file" in text


async def test_s4_the_full_grep_never_holds_the_event_loop(runaway_tree):
    """Through the tool, as a coding session calls it. MUTATION: match in a
    worker thread of this process -> the loop stands still for the whole
    match (the interpreter lock is held) -> red."""
    res, gap = await _with_loop_watch(
        GrepTool().execute({"pattern": RUNAWAY, "output_mode": "count"}, _ctx(runaway_tree))
    )
    assert gap < 2.0, f"the event loop stood still for {gap:.1f}s"
    assert res.ok, res.error
    assert "a_first.txt:1" in res.output
    assert "the pattern was still running on big.txt" in res.output
    assert res.data["truncated"] is True


async def test_s4_the_plain_grep_never_holds_the_event_loop(runaway_tree):
    """The original form (pattern and path only) is bounded the same way,
    and its matches before the stuck file are kept."""
    res, gap = await _with_loop_watch(GrepTool().execute({"pattern": RUNAWAY}, _ctx(runaway_tree)))
    assert gap < 2.0, f"the event loop stood still for {gap:.1f}s"
    assert res.ok, res.error
    assert res.output.startswith("a_first.txt:1: aaa")
    assert "[search truncated — the pattern was still running on big.txt" in res.output
    assert res.data["truncated"] is True


def test_s4_a_plain_pattern_still_searches_in_this_process(ws, monkeypatch):
    """No program is started for a pattern that cannot backtrack (a word, a
    class, an anchor): the common search costs what it did."""
    from iron_jarvis.core import procs

    def no_child(*a, **k):
        raise AssertionError("a program was started for a plain pattern")

    monkeypatch.setattr(procs, "run", no_child)
    _touch(ws / "a.py", "def f():\n    return 1\n")
    report = S.grep(ws, ws.resolve(), S.GrepOptions(pattern=r"^def \w", output_mode="count"))
    assert report.entries == ["a.py:1"]


@pytest.mark.parametrize(
    "pattern, plain",
    [
        ("TODO", True),
        (r"^def \w", True),
        (r"foo\(bar\)", True),
        ("a|b", True),
        ("[abc]x", True),
        (RUNAWAY, False),
        ("a+", False),
        ("a*", False),
        ("ab?", False),
        ("a{2,}", False),
        ("(ab)", False),
        (r"(a)\1", False),
        ("x" * 300, False),  # a very long pattern is not judged plain
    ],
)
def test_s4_which_patterns_search_in_this_process(pattern, plain):
    assert S.plain_pattern(pattern) is plain


def test_s4_the_search_program_says_what_it_found_when_it_finishes(ws):
    """A pattern with a repeat that finishes: the same answer as before,
    from the program."""
    _touch(ws / "c.txt", "\n".join(f"l{i}" for i in range(1, 11)) + "\n")
    opts = S.GrepOptions(pattern="^l(3|8)+$", before=1, after=1)
    report = S.grep(ws, ws.resolve(), opts)
    assert report.walk_truncated == ""
    assert report.entries == ["c.txt-2-l2", "c.txt:3:l3", "c.txt-4-l4", "--",
                              "c.txt-7-l7", "c.txt:8:l8", "c.txt-9-l9"]


# --------------------------------------------------------------------------- #
# T9: process_start works out its environment off the loop, bounded
# --------------------------------------------------------------------------- #

PY = f'"{sys.executable}"'


async def test_t9_the_environment_is_worked_out_off_the_event_loop(ws, monkeypatch):
    """MUTATION: call command_env inline again -> same thread -> red."""
    from iron_jarvis.harness import command_env as CE

    seen: list[int] = []

    def spy(modify_env, session_id):
        seen.append(threading.get_ident())
        return None

    monkeypatch.setattr(CE, "command_env", spy)
    res = await P.ProcessStartTool().execute(
        {"command": f"{PY} -c \"print('hi')\"", "until_exit": True, "wait_ms": 20000}, _ctx(ws)
    )
    assert res.ok, res.error
    assert seen and seen[0] != threading.get_ident()


async def test_t9_an_environment_that_does_not_come_back_starts_nothing_and_says_so(ws, monkeypatch):
    """A folder that stops answering (a network drive) while the environment
    is worked out: past the bound nothing is started and the reply says why.
    MUTATION: drop the bound -> the program starts -> red."""
    from iron_jarvis.harness import command_env as CE

    release = threading.Event()

    def stuck(modify_env, session_id):
        release.wait(5)  # a guard against a hung test, not a timing assumption
        return None

    monkeypatch.setattr(CE, "command_env", stuck)
    monkeypatch.setattr(P, "ENV_TIMEOUT_S", 0.2, raising=False)
    try:
        res = await P.ProcessStartTool().execute(
            {"command": f"{PY} -c \"print('hi')\"", "until_exit": True}, _ctx(ws)
        )
    finally:
        release.set()
    assert res.ok is False
    assert "nothing was started" in res.error
    assert P.TABLE.sessions("s-no-stall") == []


# --------------------------------------------------------------------------- #
# The git guard's file checks run off the loop, bounded, same answers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    (root / "app.py").write_bytes(b"x = 1\n")
    return root


@contextlib.contextmanager
def in_folder(folder: Path):
    token = G.set_turn_folder(G.folder_facts(str(folder)))
    try:
        yield
    finally:
        G.reset_turn_folder(token)


@contextlib.contextmanager
def on_branch(folder: Path):
    token = B.set_turn_branch({"repo": str(folder), "worktree": str(folder), "branch": "ij/x",
                               "base": "main", "checkout": str(folder)}, str(folder))
    try:
        yield
    finally:
        B.reset_turn_branch(token)


@pytest.fixture()
def lexists_threads(monkeypatch):
    seen: list[int] = []
    real = os.path.lexists

    def spy(path):
        seen.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr(os.path, "lexists", spy)
    return seen


@pytest.mark.parametrize("tool", ["process_start", "shell"])
async def test_guard_the_checkout_word_is_looked_up_off_the_event_loop(tree, lexists_threads, tool):
    """Same answer (a checkout of a file throws work away and asks), the look
    on a worker thread. MUTATION: call tool_check inline -> red."""
    t = P.ProcessStartTool() if tool == "process_start" else ST.SandboxedShellTool()
    with in_folder(tree):
        lexists_threads.clear()  # arming the folder is the route's own look, off the loop there
        verdict = await t.spend_check({"command": "git checkout app.py"}, _ctx(tree))
        switch = await t.spend_check({"command": "git checkout main"}, _ctx(tree))
    assert verdict is not None and verdict.ask is True and verdict.once_only is True
    assert "throws away changes in your own folder" in verdict.words
    assert switch is None, "a switch to a branch asks nothing, as before"
    assert lexists_threads and threading.get_ident() not in lexists_threads


async def test_guard_a_refusal_on_a_branch_is_judged_off_the_event_loop(tree, lexists_threads):
    """``git reset main`` on a session's branch moves the branch: refused
    before anything runs, the look on a worker thread."""
    with on_branch(tree):
        res = await P.ProcessStartTool().execute({"command": "git reset main"}, _ctx(tree))
    assert res.ok is False and res.error.startswith("Refused: this rewrites the branch's history")
    assert lexists_threads and threading.get_ident() not in lexists_threads


async def test_guard_a_folder_that_does_not_answer_fails_closed(tree, monkeypatch):
    """Past the bound the check asks (it could not tell a file from a
    branch) and the run refuses, in words. MUTATION: drop the bound -> the
    loop waits on the folder and 'git checkout main' asks nothing -> red."""
    release = threading.Event()

    def stuck(path):
        release.wait(5)  # a guard against a hung test, not a timing assumption
        return False

    monkeypatch.setattr(G, "CHECK_TIMEOUT_S", 0.2, raising=False)
    try:
        with in_folder(tree):
            monkeypatch.setattr(os.path, "lexists", stuck)
            verdict = await P.ProcessStartTool().spend_check({"command": "git checkout main"}, _ctx(tree))
        with on_branch(tree):
            res = await P.ProcessStartTool().execute({"command": "git reset main"}, _ctx(tree))
    finally:
        release.set()
    assert verdict is not None and verdict.ask is True and verdict.once_only is True
    assert "could not finish checking this git command" in verdict.words
    assert res.ok is False and "could not finish checking this git command" in res.error


async def test_guard_a_command_without_git_is_judged_where_it_was(tree, monkeypatch):
    """No worker for a command that names no git program (nothing to read on
    disk): the common command's check costs what it did."""
    def no_thread(*a, **k):
        raise AssertionError("a worker thread for a command without git")

    with in_folder(tree):
        monkeypatch.setattr(asyncio, "to_thread", no_thread)
        assert await P.ProcessStartTool().spend_check({"command": "npm test"}, _ctx(tree)) is None


# --------------------------------------------------------------------------- #
# The packaged app serves the search program without loading the daemon
# --------------------------------------------------------------------------- #


def test_s4_the_frozen_entry_serves_the_search_program(tmp_path):
    """``ironjarvis.exe search-worker`` is the frozen door to
    ``harness/search.main`` (the entry's fast path, like the OCR and Excel
    children): it answers a request and loads none of the daemon."""
    import json

    from tests.test_entry_fast_paths_v1333 import _load_entry, _run_entry

    assert _load_entry().SEARCH_WORKER_SUBCOMMAND == S.WORKER_SUBCOMMAND == "search-worker"
    _touch(tmp_path / "w" / "a.txt", "one\ntwo\n")
    request = {"kind": "grep", "base": str(tmp_path / "w"), "root": str(tmp_path / "w"),
               "opts": {"pattern": "t(w)+o", "output_mode": "count"},
               "limits": {"deadline_s": 10.0, "max_files": 100, "max_file_bytes": 1000}}
    proc, got = _run_entry(["search-worker"], tmp_path / "report.json", stdin=json.dumps(request).encode())
    assert got == {"code": 0, "heavy": []}, proc.stderr.decode("utf-8", "replace")[-800:]
    records = [json.loads(x) for x in proc.stdout.decode("ascii").splitlines() if x.strip()]
    assert {"found": ["a.txt", records[1]["found"][1], [2], 1, []]} in records
    assert records[-1] == {"end": ["", 0, 0]}


@pytest.mark.parametrize("extra", [{}, {"output_mode": "count"}])
async def test_s4_a_search_program_that_cannot_start_is_said_in_words(ws, monkeypatch, extra):
    """Both forms: no crash, no silent "No matches", a plain sentence."""
    monkeypatch.setattr(S, "worker_argv", lambda: [str(ws / "no-such-program.exe")])
    _touch(ws / "a.txt", "aaa\n")
    res = await GrepTool().execute({"pattern": RUNAWAY, **extra}, _ctx(ws))
    assert res.ok is False
    assert res.error == "the search could not run: its search program did not start. " + S.SIMPLER_WORDS


# --------------------------------------------------------------------------- #
# Lead's review of S4: ordinary patterns stay in this process
# --------------------------------------------------------------------------- #
# The search program costs about half a second per search, and most patterns
# a model writes carry a repeat. A pattern with no group is matched here as
# long as no line is long enough to cost too much; the rest go to the program.


@pytest.mark.parametrize(
    ("pattern", "limit"),
    [
        ("def grep", -1),                    # plain: any line
        (r"def \w+_bounded", 7071),          # one repeat
        ("class .*Error", 7071),
        (r"import .* from .*", 368),         # two repeats
        (r"a\(b\)+", 7071),                  # escaped brackets are not a group
        (r"https?://x", 25_000_000),         # an optional piece only doubles
        (r"\s*=\s*\w+", 0),                  # three repeats: the program
        ("(a+)+$", 0),                       # a group: the program
        ("(ab|cd)", 0),
        ("x" * 250 + "+", 0),                # too long to judge
    ],
)
def test_s4_how_long_a_line_this_process_may_match(pattern, limit):
    assert S.in_process_line_limit(pattern) == limit
    assert S.in_process_line_limit(pattern, multiline=True) == (-1 if limit == -1 else 0)


def _no_program(monkeypatch):
    from iron_jarvis.core import procs

    def no_child(*a, **k):
        raise AssertionError("the search program was started for an ordinary pattern")

    monkeypatch.setattr(procs, "run", no_child)


async def test_s4_an_ordinary_repeat_on_short_lines_starts_no_program(ws, monkeypatch):
    """Both forms of grep. MUTATION: make ``in_process_line_limit`` answer 0
    for a repeat (16A's first rule) -> the program is started -> red."""
    _no_program(monkeypatch)
    _touch(ws / "a.py", "class ParseError(Exception):\n    pass\nimport os from somewhere\n")
    report = S.grep(ws, ws.resolve(), S.GrepOptions(pattern="class .*Error", output_mode="content"))
    assert report.entries == ["a.py:1:class ParseError(Exception):"]
    assert report.walk_truncated == ""
    two = S.grep(ws, ws.resolve(), S.GrepOptions(pattern=r"import .* from .*", output_mode="count"))
    assert two.entries == ["a.py:1"]
    res = await GrepTool().execute({"pattern": r"class \w+Error"}, _ctx(ws))
    assert res.ok and res.output.startswith("a.py:1: class ParseError(Exception):")


async def test_s4_one_long_line_sends_the_whole_search_to_the_program(ws, monkeypatch):
    """A line longer than this process may match is never matched here: the
    program does the whole search, and nothing found before it is counted
    twice. MUTATION: drop the length check in ``_scan`` -> no program is
    started -> red."""
    from iron_jarvis.core import procs

    started: list[list[str]] = []
    real_run = procs.run

    def run(argv, **kw):
        started.append(list(argv))
        return real_run(argv, **kw)

    monkeypatch.setattr(procs, "run", run)
    _touch(ws / "a_first.txt", "class EarlyError:\n")
    _touch(ws / "z_long.txt", "class " + "x" * 9000 + "Error\n")
    report = S.grep(ws, ws.resolve(), S.GrepOptions(pattern="class .*Error", output_mode="count"))
    assert len(started) == 1 and started[0] == S.worker_argv()
    assert report.entries == ["a_first.txt:1", "z_long.txt:1"]  # each file once
    assert report.files_matched == 2 and report.walk_truncated == ""
    started.clear()
    res = await GrepTool().execute({"pattern": "class .*Error"}, _ctx(ws))
    assert len(started) == 1
    assert res.ok and res.data["matches"] == 2


def test_s4_in_this_process_the_time_limit_is_read_inside_a_file(ws, monkeypatch):
    """One big file of short lines: the limit is read between lines, so the
    search ends inside the file and says so (a file cut that way is not
    reported as searched). MUTATION: read the limit between files only ->
    every line is matched and nothing is said -> red."""
    _no_program(monkeypatch)
    _touch(ws / "big.txt", "".join(f"row {i} xError\n" for i in range(2000)))
    clock = [0.0]

    class _Time:
        @staticmethod
        def monotonic():
            clock[0] += 0.01  # every look at the clock is 10 ms later
            return clock[0]

    monkeypatch.setattr(S, "time", _Time)
    report = S.grep(ws, ws.resolve(), S.GrepOptions(pattern=r"row \d+ xError", output_mode="count"),
                    deadline_s=0.1)
    assert report.walk_truncated == "stopped after 0.1s"
    assert report.entries == []


# --------------------------------------------------------------------------- #
# Lead's review: a glob cannot freeze the app either (found with S4)
# --------------------------------------------------------------------------- #


def test_glob_a_run_of_deep_parts_is_one_and_too_many_are_refused_in_words():
    """A glob becomes a ``re`` pattern, matched with the interpreter lock
    held: eight ``**/`` in a row against a 40-folder path took over five
    seconds. A run of them means what one means, so it is matched as one.
    MUTATION: keep every ``**`` -> the pattern is no longer ``**/x`` -> red."""
    from iron_jarvis.harness import globmatch as GM

    assert GM.normalize_pattern("**/" * 8 + "x") == "**/x"
    assert GM.normalize_pattern("src/**/**/*.py") == "src/**/*.py"
    deep = "/".join(["a"] * 40) + "/y"
    assert GM.matches_path(deep, "**/" * 8 + "x") is False
    assert GM.matches_path("/".join(["a"] * 40) + "/x", "**/" * 8 + "x") is True
    # Past the counts: refused, in words, before anything is matched.
    with pytest.raises(GM.GlobError, match=r"uses '\*\*' more than 5 times"):
        GM.normalize_pattern("/".join(["**", "a"] * 6))
    with pytest.raises(GM.GlobError, match=r"uses '\*' more than 4 times"):
        GM.normalize_pattern("src/*a*a*a*a*b")
    # Ordinary patterns are untouched; each choice in braces is its own name.
    for ok in ("**/*.py", "src/**/test_*.py", "*.{a,b}", "{*.a,*.b,*.c,*.d,*.e}", "a/**/b/**/c/*.ts",
               "*-*-*-*.log"):
        assert GM.normalize_pattern(ok) == ok


async def test_glob_too_many_stars_is_said_by_the_search_tools(ws):
    _touch(ws / "a.py", "x = 1\n")
    res = await GrepTool().execute({"pattern": "x", "glob": "*a*a*a*a*a*b"}, _ctx(ws))
    assert res.ok is False
    assert "bad glob" in res.error and "use fewer" in res.error
