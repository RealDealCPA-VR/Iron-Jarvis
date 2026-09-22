"""A workspace walk can no longer wedge the daemon (v1.153.1).

Reported as "Daemon offline", and it was not. The daemon was alive and still
listening on 8787 — it simply never answered, because `list_files` did
``base.rglob("*")`` INLINE on the single event loop. Live evidence from the
user's hung install: 84% CPU with the MainThread parked in ``pathlib.is_file``
under ``ListFilesTool.execute``, four hours after the last database write.

Every reported symptom follows from that one cause: the dashboard's fetch times
out and ``lib/api.ts`` maps a dead fetch to "daemon offline"; Retry issues
another request onto the same blocked loop and hangs identically; and the thread
list never loads because that, too, is a request.

THE TEST THAT MATTERS is :func:`test_a_slow_walk_does_not_block_the_event_loop`.
The bounds below are useful, but a bounded walk on the loop is still a walk on
the loop — offloading is what makes a pathological tree cost one request instead
of the whole application.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.tools import builtins as B
from iron_jarvis.tools.base import ToolContext


def _ctx(root: Path) -> ToolContext:
    # These two tools only ever touch `workspace`; the rest of the context is
    # required by the dataclass, not by the code under test.
    return ToolContext(
        workspace=root,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=None,
        engine=None,
    )


def _tree(root: Path, *, files: int = 40, junk: bool = True) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    for i in range(files):
        (root / "src" / f"f{i}.py").write_text(f"# file {i}\nvalue = {i}\n", encoding="utf-8")
    if junk:
        for d in (".git", "node_modules", "__pycache__"):
            heavy = root / d / "deep"
            heavy.mkdir(parents=True, exist_ok=True)
            for i in range(50):
                (heavy / f"junk{i}.py").write_text("x = 1\n", encoding="utf-8")
    return root


_TICK_S = 0.01
_MIN_TICKS = 3
_WAIT_S = 10.0


async def _walk_under_heartbeat(tool, args, root: Path) -> dict:
    """Run *tool* with a blocking walk beside a heartbeat; report what the
    walk saw from the inside.

    NO WALL-CLOCK BAR. This used to return the worst gap between heartbeat
    ticks and assert it stayed under 0.5s, and CI went red at 1.83s on a busy
    runner with the walk correctly offloaded — the bar measured the machine.
    The signals now are structural (the v1226 ``_Ticks`` shape): the stub
    records the thread it ran on and whether it saw a running loop, then
    BLOCKS ITS THREAD until the loop's heartbeat has advanced ``_MIN_TICKS``
    past the value it read on entry. An inline walk runs on the loop thread,
    so the heartbeat cannot tick while it blocks: the wait runs out and the
    delta is exactly 0. An offloaded walk sees the ticks arrive — on a loaded
    runner they arrive later, which costs time, never a false red (wait for
    the thing you assert).
    """
    seen: dict = {"loop_thread": threading.get_ident()}
    ticks = {"n": 0, "target": None, "stop": False}
    reached = threading.Event()

    async def heartbeat():
        while not ticks["stop"]:
            await asyncio.sleep(_TICK_S)
            ticks["n"] += 1
            if ticks["target"] is not None and ticks["n"] >= ticks["target"]:
                reached.set()

    def slow_walk(base, *, limit, deadline_s=B._WALK_DEADLINE_S):
        seen["walk_thread"] = threading.get_ident()
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        before = ticks["n"]
        ticks["target"] = before + _MIN_TICKS
        # Blocking, like a real filesystem stall — and it lasts until the loop
        # proves it is alive, or until _WAIT_S says it never will be.
        reached.wait(_WAIT_S)
        seen["ticks_inside"] = ticks["n"] - before
        return [], ""

    original = B._walk_files
    B._walk_files = slow_walk
    hb = asyncio.create_task(heartbeat())
    try:
        await tool.execute(args, _ctx(root))
    finally:
        B._walk_files = original
        ticks["stop"] = True
        await hb
    return seen


def _assert_offloaded(seen: dict, what: str) -> None:
    assert "walk_thread" in seen, f"{what}: the slow walk never ran"
    assert seen["walk_thread"] != seen["loop_thread"], (
        f"{what}: the walk ran ON the event-loop thread — it is still inline, "
        "and every other request freezes with it"
    )
    assert seen["on_loop"] is False, f"{what}: the walk saw a running loop (inline)"
    assert seen["ticks_inside"] >= _MIN_TICKS, (
        f"{what}: the loop starved during the walk ({seen['ticks_inside']} ticks "
        "inside) — it is back on the event loop"
    )


# --------------------------------------------------------------------------- #
# (1) THE ONE THAT MATTERS: the loop keeps running.
# --------------------------------------------------------------------------- #
def test_a_slow_walk_does_not_block_the_event_loop(tmp_path):
    """A slow filesystem must cost ONE request, not the whole daemon.

    The walk is replaced by a synchronous block — precisely what a huge or
    network-mounted tree feels like from the loop's point of view. The stub
    reports the thread it ran on and how far the heartbeat advanced while it
    blocked (see :func:`_walk_under_heartbeat` for why not a latency bar).
    """
    _tree(tmp_path)
    seen = asyncio.run(_walk_under_heartbeat(B.ListFilesTool(), {"path": "."}, tmp_path))
    _assert_offloaded(seen, "list_files")


def test_grep_also_stays_off_the_event_loop(tmp_path):
    """grep walked the tree AND read every file's full text inline — the same
    defect, with more work attached to it."""
    _tree(tmp_path)
    seen = asyncio.run(
        _walk_under_heartbeat(B.GrepTool(), {"pattern": "value"}, tmp_path)
    )
    _assert_offloaded(seen, "grep")


# --------------------------------------------------------------------------- #
# (2) BOUNDED — and honest about it.
# --------------------------------------------------------------------------- #
def test_a_huge_directory_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_MAX_WALK_ENTRIES", 25)
    _tree(tmp_path, files=200, junk=False)
    res = asyncio.run(B.ListFilesTool().execute({"path": "."}, _ctx(tmp_path)))
    assert res.ok
    assert res.data["count"] == 25
    assert res.data["truncated"] is True


def test_a_truncated_listing_says_so(tmp_path, monkeypatch):
    """Silence here is the dangerous option: the model treats a partial listing
    as the whole directory and reports that a file does not exist."""
    monkeypatch.setattr(B, "_MAX_WALK_ENTRIES", 10)
    _tree(tmp_path, files=100, junk=False)
    res = asyncio.run(B.ListFilesTool().execute({"path": "."}, _ctx(tmp_path)))
    assert "truncated" in res.output
    assert "NOT the whole directory" in res.output


def test_a_complete_listing_makes_no_such_claim(tmp_path):
    _tree(tmp_path, files=5, junk=False)
    res = asyncio.run(B.ListFilesTool().execute({"path": "."}, _ctx(tmp_path)))
    assert "truncated" not in res.output
    assert res.data["truncated"] is False
    assert res.data["count"] == 5


def test_the_walk_respects_a_deadline(tmp_path, monkeypatch):
    """A tree can be slow without being large — a network mount, a spun-down
    disk. The count cap alone would never fire there.

    The CLOCK IS FAKED, and the first version of this test is why: it walked a
    real tree with ``deadline_s=0.0`` and asserted the walk stopped. That
    passed locally and failed on CI, because Windows' ``time.monotonic()`` has
    ~15ms granularity — a small tree finishes inside one tick, elapsed is
    exactly 0.0, and the comparison never fires. Asserting on wall-clock timing
    made the test a coin flip on machine speed. Here time only moves when we
    say so, so this asserts the deadline LOGIC on every machine.
    """
    import time as _time

    ticks = iter([0.0, 0.5, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(_time, "monotonic", lambda: next(ticks, 99.0))

    _tree(tmp_path, files=30, junk=False)
    _paths, truncated = B._walk_files(tmp_path, limit=10_000, deadline_s=10.0)
    assert truncated, "a walk that ran past its deadline must report stopping"
    assert "10s" in truncated


def test_a_walk_inside_its_deadline_reports_nothing(tmp_path, monkeypatch):
    """The other half: a fast walk must not claim it was cut short."""
    import time as _time

    monkeypatch.setattr(_time, "monotonic", lambda: 0.0)
    _tree(tmp_path, files=5, junk=False)
    paths, truncated = B._walk_files(tmp_path, limit=10_000, deadline_s=10.0)
    assert truncated == ""
    assert len(paths) == 5


# --------------------------------------------------------------------------- #
# (3) PRUNED — the usual reason a walk explodes.
# --------------------------------------------------------------------------- #
def test_heavy_directories_are_never_walked(tmp_path):
    _tree(tmp_path, files=5, junk=True)
    res = asyncio.run(B.ListFilesTool().execute({"path": "."}, _ctx(tmp_path)))
    assert "node_modules" not in res.output
    assert ".git" not in res.output
    assert "__pycache__" not in res.output
    assert res.data["count"] == 5


def test_pruning_uses_os_walk_so_it_can_actually_prune(tmp_path):
    """rglob cannot prune — it descends first and filters after, which is the
    expensive half. Asserted through behaviour: a pruned tree must be walked
    without its cost."""
    heavy = tmp_path / "node_modules"
    for i in range(300):
        d = heavy / f"pkg{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.js").write_text("1", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    paths, _ = B._walk_files(tmp_path, limit=10_000)
    # The BEHAVIOUR is the proof: 300 pruned packages contributed nothing. A
    # wall-clock bound here would only add a way for a slow runner to fail.
    assert [p.name for p in paths] == ["app.py"]


# --------------------------------------------------------------------------- #
# (4) grep's own bounds.
# --------------------------------------------------------------------------- #
def test_grep_caps_its_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_MAX_GREP_HITS", 5)
    (tmp_path / "big.txt").write_text("\n".join(["match me"] * 500), encoding="utf-8")
    res = asyncio.run(B.GrepTool().execute({"pattern": "match"}, _ctx(tmp_path)))
    assert res.data["matches"] == 5
    assert "truncated" in res.output


def test_grep_skips_files_too_large_to_scan(tmp_path, monkeypatch):
    """Reading a 300MB log into memory is how a slow search becomes an
    unresponsive application."""
    monkeypatch.setattr(B, "_MAX_GREP_FILE_BYTES", 100)
    (tmp_path / "huge.txt").write_text("needle\n" * 500, encoding="utf-8")
    (tmp_path / "small.txt").write_text("needle\n", encoding="utf-8")
    res = asyncio.run(B.GrepTool().execute({"pattern": "needle"}, _ctx(tmp_path)))
    assert res.data["matches"] == 1
    assert "small.txt" in res.output


def test_grep_still_finds_what_it_should(tmp_path):
    """The bounds must not have broken the feature."""
    _tree(tmp_path, files=6, junk=False)
    res = asyncio.run(B.GrepTool().execute({"pattern": r"value = 3"}, _ctx(tmp_path)))
    assert res.ok and res.data["matches"] == 1
    assert "f3.py" in res.output


def test_listing_still_lists(tmp_path):
    _tree(tmp_path, files=3, junk=False)
    res = asyncio.run(B.ListFilesTool().execute({"path": "."}, _ctx(tmp_path)))
    assert res.ok
    assert "src/f0.py" in res.output and "src/f2.py" in res.output


def test_a_missing_directory_is_still_a_clean_error(tmp_path):
    res = asyncio.run(B.ListFilesTool().execute({"path": "nope"}, _ctx(tmp_path)))
    assert res.ok is False
    assert "no such directory" in (res.error or "")


@pytest.mark.parametrize("tool", ["list_files", "grep"])
def test_neither_tool_calls_rglob_any_more(tool):
    """A guard on the defect itself: rglob cannot prune and cannot be bounded
    mid-iteration, so its return here would reintroduce the freeze."""
    import inspect

    cls = B.ListFilesTool if tool == "list_files" else B.GrepTool
    assert "rglob" not in inspect.getsource(cls)
    assert "to_thread" in inspect.getsource(cls), "the walk must stay off the loop"
