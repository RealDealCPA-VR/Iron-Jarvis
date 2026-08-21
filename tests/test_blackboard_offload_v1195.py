"""v1.195.0 — the blackboard tools must not run SQLite on the event loop.

THE MEASURED SHAPE OF THIS BUG. ``tools/registry.invoke`` awaits
``tool.execute`` with no thread offload, so whatever a tool does synchronously
inside its ``async def`` runs on the daemon's ONE event loop. All three
blackboard tools called ``board_id_for`` / ``name_for`` / ``list`` / ``roster``
/ ``post`` inline — while the SAME ``roster()`` is deliberately offloaded one
layer up in ``agents/runtime.teammates_block`` (whose docstring measured it at
up to 47ms), and while the sibling substrate from the same release
(``worklist/tools.py``) wraps every store call. Cost scales with the
department: 2.5ms at 3 children, 14.8ms at 40, 35.9ms at 100 — and a tool runs
once per model step, far more often than prompt assembly.

That freeze does not look like a freeze; it looks like "Daemon offline"
(``lib/api.ts`` maps a dead fetch to status 0 and Retry lands another request on
the same blocked loop). See ``tests/test_event_loop_offload_v1175.py``, whose
two-halves shape this file follows:

  * STRUCTURAL — the store call ran on a worker thread, not the loop's thread.
  * BEHAVIOURAL — the loop actually serviced other work while it ran.

Part B is the N+1 underneath ``roster()``: ``_seed_runs`` fetched up to 200 rows
and then called ``resolve_board_id`` once per row, EACH opening its own
``session_scope``. It is now one walk in one session — and every membership
answer is pinned here against a verbatim copy of the old implementation, because
a fast roster that disagrees with the old one is worse than the N+1 it replaced.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from sqlmodel import select

import iron_jarvis.blackboard.store as store_mod
from iron_jarvis.blackboard import BlackboardStore, resolve_board_id
from iron_jarvis.blackboard.tools import (
    BlackboardPostTool,
    BlackboardReadTool,
    MessageAgentTool,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType
from iron_jarvis.tools.base import ToolContext

#: How long each faked store call blocks, and how often the watcher ticks. The
#: sleep must dwarf the tick so an offloaded call yields many ticks while an
#: inline one yields none. (Same constants' role as v1.175.0's.)
_BLOCK_S = 0.08
_TICK_S = 0.01
_MIN_TICKS = 5

#: Every store method the three tools reach. Wrapping ALL of them is what stops
#: this file from passing on a partial fix: one inline call is one main-thread
#: entry in the record.
_STORE_CALLS = (
    "board_id_for",
    "name_for",
    "list",
    "post",
    "roster",
    "resolve_addressee",
)


# --- helpers --------------------------------------------------------------


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


class _Recorder:
    """Wraps every store method with "record my thread, then block"."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, str]] = []
        for name in _STORE_CALLS:
            self._wrap(monkeypatch, name)

    def _wrap(self, monkeypatch, name: str) -> None:
        real = getattr(BlackboardStore, name)

        def wrapper(inner_self, *args, **kwargs):  # noqa: ANN001
            self.calls.append((name, threading.current_thread().name))
            time.sleep(_BLOCK_S)
            return real(inner_self, *args, **kwargs)

        monkeypatch.setattr(BlackboardStore, name, wrapper)

    @property
    def names(self) -> set[str]:
        return {name for name, _ in self.calls}

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def assert_all_offloaded(self, expected: set[str]) -> None:
        main = threading.main_thread().name
        assert self.names == expected, f"unexpected store calls: {self.calls}"
        on_loop = [c for c in self.calls if c[1] == main]
        assert not on_loop, f"store call(s) ran on the event loop thread: {on_loop}"


def _ctx(platform, run_id: str, session_id: str) -> ToolContext:
    return ToolContext(
        workspace=platform.config.workspaces_dir,
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _department(platform, *child_types: AgentType) -> tuple[str, str, list[str]]:
    """A root run + one child run per type (same shape as the v1.193.0 tests)."""
    with session_scope(platform.engine) as db:
        root = AgentRun(session_id="dept-root", parent_id=None)
        root.agent_type = AgentType.SUPERVISOR
        db.add(root)
        db.commit()
        db.refresh(root)
        children = []
        for index, agent_type in enumerate(child_types):
            child = AgentRun(session_id=f"child-{index}", parent_id=root.id)
            child.agent_type = agent_type
            db.add(child)
            children.append(child)
        db.commit()
        for child in children:
            db.refresh(child)
        return "dept-root", root.id, [c.id for c in children]


def _reference_seed_runs(store: BlackboardStore, board_id: str) -> list[str]:
    """The PRE-v1.195.0 ``_seed_runs``, verbatim.

    Kept here on purpose: the collapse of the N+1 is only allowed if it answers
    identically, so the new walk is asserted against the old one rather than
    against a hand-written expectation that could encode the same mistake twice.
    """
    if board_id in store_mod._LEDGER_BOARDS:
        return []
    with session_scope(store.engine) as db:
        rows = list(
            db.exec(
                select(AgentRun)
                .where(AgentRun.session_id == board_id)
                .order_by(AgentRun.created_at, AgentRun.id)
                .limit(store_mod._MAX_ROSTER_RUNS)
            )
        )
        candidates = [(r.id, r.session_id) for r in rows]
    return [
        run_id
        for run_id, session_id in candidates
        if resolve_board_id(store.engine, session_id, run_id) == board_id
    ]


def _add_runs(platform, specs: list[dict[str, Any]]) -> dict[str, str]:
    """Insert runs by nickname; ``parent`` may name an earlier nickname."""
    made: dict[str, str] = {}
    with session_scope(platform.engine) as db:
        for spec in specs:
            run = AgentRun(
                session_id=spec["session"],
                parent_id=made.get(spec.get("parent", ""), None),
            )
            run.agent_type = spec.get("type", AgentType.BUILDER)
            db.add(run)
            db.commit()
            db.refresh(run)
            made[spec["name"]] = run.id
    return made


# --- PART A: nothing blocking on the event loop ---------------------------


async def test_blackboard_read_runs_every_store_call_off_the_loop(
    platform, monkeypatch
):
    """`blackboard_read` is the hot one: four SQLite reads including the
    department walk, once per model step."""
    _board, _root_id, (builder_id, _researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    rec = _Recorder(monkeypatch)
    tool = BlackboardReadTool(platform.blackboard)

    result, ticks = await _ticks_during(
        tool.execute({}, _ctx(platform, builder_id, "child-0"))
    )

    assert result.ok, result.error
    rec.assert_all_offloaded({"board_id_for", "name_for", "list", "roster"})
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_blackboard_post_runs_every_store_call_off_the_loop(
    platform, monkeypatch
):
    """Including the directed path — resolution AND the write."""
    board, _root_id, (builder_id, _researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    rec = _Recorder(monkeypatch)
    tool = BlackboardPostTool(platform.blackboard)

    result, ticks = await _ticks_during(
        tool.execute(
            {"text": "the K-1 totals reconcile", "to_agent": "researcher"},
            _ctx(platform, builder_id, "child-0"),
        )
    )

    assert result.ok, result.error
    rec.assert_all_offloaded(
        {"board_id_for", "roster", "resolve_addressee", "name_for", "post"}
    )
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"
    # ...and the row really was written by the worker thread.
    assert [r.text for r in platform.blackboard.list(board)] == [
        "the K-1 totals reconcile"
    ]


async def test_message_agent_runs_every_store_call_off_the_loop(
    platform, monkeypatch
):
    board, _root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    rec = _Recorder(monkeypatch)
    tool = MessageAgentTool(platform.blackboard)

    result, ticks = await _ticks_during(
        tool.execute(
            {"to_agent": "researcher", "text": "cross-check depreciation"},
            _ctx(platform, builder_id, "child-0"),
        )
    )

    assert result.ok, result.error
    assert result.data["to_agent"] == researcher_id
    rec.assert_all_offloaded(
        {"board_id_for", "roster", "resolve_addressee", "name_for", "post"}
    )
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"
    assert len(platform.blackboard.list(board)) == 1


async def test_recipient_resolution_is_one_hop_and_one_department_walk(
    platform, monkeypatch
):
    """`_resolve_recipient` is offloaded as ONE unit.

    Its docstring's single-fetch property — the roster is fetched once and
    reused for BOTH the resolution and the refusal text — has to survive the
    thread hop. Offloading its two store calls separately is the edit that would
    quietly break it, so the refusal path (the one that renders the roster) is
    the case asserted here.
    """
    _board, _root_id, (builder_id, _researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    rec = _Recorder(monkeypatch)
    tool = MessageAgentTool(platform.blackboard)

    result, ticks = await _ticks_during(
        tool.execute(
            {"to_agent": "tax-reader", "text": "check this"},
            _ctx(platform, builder_id, "child-0"),
        )
    )

    assert not result.ok
    assert "tax-reader" in (result.error or "")
    assert rec.count("roster") == 1, f"department walked twice: {rec.calls}"
    assert rec.count("resolve_addressee") == 1
    rec.assert_all_offloaded({"board_id_for", "roster", "resolve_addressee"})
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


# --- PART B: the roster's N+1 --------------------------------------------


def _multi_run_board(platform) -> tuple[str, dict[str, str]]:
    """A department whose ROOT SESSION holds many runs (a continued session),
    plus one run that merely lives in that session while belonging to another
    department. That mix is what ``_seed_runs`` exists to separate."""
    specs: list[dict[str, Any]] = [
        {"name": "root", "session": "dept-root", "type": AgentType.SUPERVISOR},
    ]
    for index in range(10):
        specs.append(
            {"name": f"kid{index}", "session": "dept-root", "parent": "root"}
        )
    # A foreign chain: same session row, but its root is another department.
    specs.append({"name": "other-root", "session": "other-dept"})
    specs.append(
        {"name": "foreign", "session": "dept-root", "parent": "other-root"}
    )
    return "dept-root", _add_runs(platform, specs)


def test_seed_runs_opens_one_session_instead_of_one_per_run(platform, monkeypatch):
    """The N+1: 12 candidate rows used to mean 1 + 12 ``session_scope``s, each a
    fresh connection re-reading the same handful of ancestors."""
    board, _ids = _multi_run_board(platform)
    opened: list[int] = []
    real_scope = store_mod.session_scope

    def counting_scope(engine):
        opened.append(1)
        return real_scope(engine)

    monkeypatch.setattr(store_mod, "session_scope", counting_scope)
    seeds = platform.blackboard._seed_runs(board)

    assert len(seeds) == 11  # root + 10 kids; the foreign chain is excluded
    assert len(opened) == 1, f"still an N+1: {len(opened)} sessions opened"


def test_seed_runs_matches_the_old_walk_exactly(platform):
    """Membership AND order, against the verbatim old implementation."""
    board, ids = _multi_run_board(platform)
    seeds = platform.blackboard._seed_runs(board)

    assert seeds == _reference_seed_runs(platform.blackboard, board)
    assert ids["foreign"] not in seeds
    assert ids["root"] in seeds
    assert all(ids[f"kid{i}"] in seeds for i in range(10))
    # An empty board and another department are unchanged too.
    assert platform.blackboard._seed_runs("nobody-here") == []
    assert platform.blackboard._seed_runs("other-dept") == _reference_seed_runs(
        platform.blackboard, "other-dept"
    )


def test_seed_runs_still_skips_the_ledger_boards(platform):
    """``_LEDGER_BOARDS`` holds accounting rows, not teammates. Seeding from
    them fills the permanent global "chat" board with phantom members."""
    _add_runs(
        platform,
        [{"name": f"turn{i}", "session": "chat"} for i in range(5)],
    )
    assert platform.blackboard._seed_runs("chat") == []
    assert platform.blackboard.roster("chat") == []


def test_seed_runs_keeps_the_max_roster_runs_bound(platform, monkeypatch):
    """The bound is a runaway-delegation guard; the faster walk must not have
    quietly removed it. Same query, so the CAPPED SET is identical too."""
    board, _ids = _multi_run_board(platform)
    monkeypatch.setattr(store_mod, "_MAX_ROSTER_RUNS", 4)

    seeds = platform.blackboard._seed_runs(board)
    assert len(seeds) <= 4
    assert seeds == _reference_seed_runs(platform.blackboard, board)


def test_seed_runs_survives_a_parent_cycle_identically(platform):
    """A cycle is the one case where the answer depends on where the walk
    STARTED, which is why chain memoisation is skipped for cyclic walks. Two
    candidates sitting on one cycle would be the way to notice a wrong cache."""
    ids = _add_runs(
        platform,
        [
            {"name": "a", "session": "cyc"},
            {"name": "b", "session": "cyc", "parent": "a"},
            {"name": "c", "session": "other", "parent": "b"},
        ],
    )
    with session_scope(platform.engine) as db:
        run_a = db.get(AgentRun, ids["a"])
        run_a.parent_id = ids["c"]  # a -> c -> b -> a
        db.add(run_a)
        db.commit()

    assert platform.blackboard._seed_runs("cyc") == _reference_seed_runs(
        platform.blackboard, "cyc"
    )
    # And it terminates rather than spinning on the cycle.
    assert isinstance(platform.blackboard.roster("cyc"), list)


def test_roster_contents_are_unchanged(platform):
    """Handles, states and post counts, through the real department shape."""
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )
    platform.blackboard.post(board, builder_id, "found X", author_name="builder")
    platform.blackboard.post(board, builder_id, "and Y", author_name="builder")
    roster = {r["agent_run_id"]: r for r in platform.blackboard.roster(board)}

    assert set(roster) == {root_id, builder_id, researcher_id}
    assert roster[root_id]["handle"] == "supervisor"
    assert roster[builder_id]["handle"] == "builder"
    assert roster[builder_id]["posts"] == 2
    assert roster[researcher_id]["handle"] == "researcher"
    assert roster[researcher_id]["posts"] == 0  # never spoke, still addressable


# --- behaviour: unchanged by either half ----------------------------------


async def test_post_read_and_name_addressing_are_unchanged(platform):
    """End to end through the registry the model actually calls."""
    board, root_id, (builder_id, researcher_id) = _department(
        platform, AgentType.BUILDER, AgentType.RESEARCHER
    )

    posted = await platform.registry.invoke(
        "blackboard_post",
        {"text": "depreciation looks off"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert posted.ok, posted.error
    assert posted.data["board_id"] == board
    assert posted.data["author_name"] == "builder"

    sent = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "researcher", "text": "pull the 1099 totals"},
        _ctx(platform, builder_id, "child-0"),
        platform.permissions,
    )
    assert sent.ok, sent.error
    assert sent.data["to_agent"] == researcher_id
    assert sent.data["to_name"] == "researcher"

    read = await platform.registry.invoke(
        "blackboard_read",
        {},
        _ctx(platform, researcher_id, "child-1"),
        platform.permissions,
    )
    assert [r["text"] for r in read.data["records"]] == [
        "depreciation looks off",
        "pull the 1099 totals",
    ]
    assert read.data["you_name"] == "researcher"
    # The roster still reaches the model as text it can address.
    assert f"builder={builder_id}" in read.output
    assert f"supervisor={root_id}" in read.output

    to_me = await platform.registry.invoke(
        "blackboard_read",
        {"to_me": True},
        _ctx(platform, researcher_id, "child-1"),
        platform.permissions,
    )
    assert [r["text"] for r in to_me.data["records"]] == ["pull the 1099 totals"]


async def test_refusals_are_unchanged(platform):
    """An unknown name still bounces WITH the roster; an ambiguous one still
    refuses instead of picking; neither writes a row."""
    board, root_id, (builder_a, builder_b) = _department(
        platform, AgentType.BUILDER, AgentType.BUILDER
    )

    unknown = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "tax-reader", "text": "check this"},
        _ctx(platform, builder_a, "child-0"),
        platform.permissions,
    )
    assert not unknown.ok
    assert f"supervisor={root_id}" in (unknown.error or "")
    assert builder_a not in (unknown.error or "")  # never your own candidate

    ambiguous = await platform.registry.invoke(
        "message_agent",
        {"to_agent": "builder", "text": "you, specifically"},
        _ctx(platform, root_id, "dept-root"),
        platform.permissions,
    )
    assert not ambiguous.ok
    assert "ambiguous" in (ambiguous.error or "").lower()
    assert builder_a in (ambiguous.error or "")
    assert builder_b in (ambiguous.error or "")

    bad_direction = await platform.registry.invoke(
        "blackboard_post",
        {"text": "note", "to_agent": "nobody-here"},
        _ctx(platform, builder_a, "child-0"),
        platform.permissions,
    )
    assert not bad_direction.ok and "nobody-here" in (bad_direction.error or "")

    assert platform.blackboard.list(board) == []  # not one silent row


@pytest.mark.parametrize("missing", ["text", "to_agent"])
async def test_required_args_still_refuse_before_touching_the_store(
    platform, monkeypatch, missing
):
    """The cheap refusals must stay cheap — no thread hop, no query."""
    rec = _Recorder(monkeypatch)
    args = {"to_agent": "researcher", "text": "hi"}
    args.pop(missing)
    result = await MessageAgentTool(platform.blackboard).execute(
        args, _ctx(platform, "run-x", "sess-x")
    )
    assert not result.ok and f"`{missing}` is required" in (result.error or "")
    assert rec.calls == []
