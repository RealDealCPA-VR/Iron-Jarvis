"""CONSULT BUDGET EVICTION (v1.195.0) — the cap must not evict the run using it.

`consult` spends a real provider call without opening a session, so the
delegation depth cap cannot see it and :data:`_MAX_CONSULTS_PER_RUN` is the ONLY
thing bounding an "ask everyone about everything" loop. That ceiling is a
per-key counter held in a map bounded at :data:`_MAX_TRACKED_RUNS`, and until
v1.195.0 the map evicted its OLDEST-INSERTED key: re-assigning an existing dict
key does not move it, so a run that kept consulting stayed pinned at the front
of the eviction queue for its whole life. Evicting a counter is a budget RESET,
so under enough churn the guard silently stopped guarding the one caller it was
watching most closely.

What this file pins:

1. A run's budget still caps at ``_MAX_CONSULTS_PER_RUN`` even when the tracked
   map churns far past ``_MAX_TRACKED_RUNS`` WHILE that run is still asking.
2. The ad-hoc (run-less) lane's rolling window is untouched — it still rolls
   over after ``_ADHOC_WINDOW_S`` so chat can never be locked out permanently,
   and a real run's counter still does NOT expire on time.
3. ``_COLLECTION_NOUNS`` carries no duplicate literal. Asserted on the SOURCE
   line, because a frozenset cannot show one.

Offline: real platform, real DB, the deterministic mock provider.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.agents import decompose
from iron_jarvis.agents.consult_tool import (
    _ADHOC_WINDOW_S,
    _MAX_CONSULTS_PER_RUN,
    _MAX_TRACKED_RUNS,
    ConsultTool,
)
from iron_jarvis.agents.decompose import _COLLECTION_NOUNS
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType, Session
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path / "home"))


def _ctx(platform, session_id: str, agent_run_id: str = "run_1") -> ToolContext:
    return ToolContext(
        workspace=platform.config.workspaces_dir,
        session_id=session_id,
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def _caller(platform, agent_type=AgentType.BUILDER) -> Session:
    return await Orchestrator(platform).create_session("the parent task", agent_type)


# --------------------------------------------------------------------------- #
# 1. churn past the map bound must not refund a LIVE run's budget
# --------------------------------------------------------------------------- #
async def test_a_live_runs_budget_survives_churn_past_the_tracked_map_bound(platform):
    """The live run consults periodically WHILE hundreds of sibling runs start.

    The interleaving is the whole point: eviction on oldest-INSERTION cannot
    tell "started long ago" from "finished long ago", so the run that had been
    asking the longest was the first one refunded. The sibling runs are charged
    through ``_charge`` rather than ``execute`` because they exist only to fill
    the map — spending ~700 mock completions to prove an eviction order would
    buy nothing — while the run under test goes through the REAL tool door, so
    what is asserted is the refusal a caller actually receives.
    """
    caller = await _caller(platform)
    tool = ConsultTool(platform)
    live = _ctx(platform, caller.id, agent_run_id="run_live")

    accepted = 0
    refused = 0
    # One question every 100 sibling runs, across ~1.4x the map bound. The gap
    # is far below _MAX_TRACKED_RUNS, so a map aged on USE always still holds
    # this run; a map aged on INSERTION drops it at ~512 and hands back a fresh
    # counter — which is the bug, observable as extra ACCEPTED consults.
    for i in range(_MAX_TRACKED_RUNS + 200):
        tool._charge(_ctx(platform, caller.id, agent_run_id=f"run_churn_{i}"))
        if i % 100:
            continue
        res = await tool.execute({"agent": "reviewer", "question": f"q{i}"}, live)
        if res.ok:
            accepted += 1
        else:
            refused += 1
            assert str(_MAX_CONSULTS_PER_RUN) in (res.error or "")

    # Eight attempts, six of them inside the budget: the cap held across the
    # entire churn instead of silently rearming mid-run.
    assert accepted + refused == 8
    assert accepted == _MAX_CONSULTS_PER_RUN
    assert refused == 8 - _MAX_CONSULTS_PER_RUN

    # The counter is still THERE and still spent — it was never evicted.
    key, is_run = tool._budget_key(live)
    assert (key, is_run) == ("run_live", True)
    assert tool._consults[key][0] == _MAX_CONSULTS_PER_RUN
    # …and the bound the eviction exists to enforce is still honoured.
    assert len(tool._consults) <= _MAX_TRACKED_RUNS


async def test_the_map_still_evicts_and_evicts_the_least_recently_charged(platform):
    """Bounding the map is not optional: this tool instance lives as long as the
    daemon, so an unbounded counter map is a leak of one entry per run. The
    change moves WHICH key goes, never WHETHER one goes."""
    tool = ConsultTool(platform)
    hot = _ctx(platform, "s", agent_run_id="run_hot")
    tool._charge(hot)

    for i in range(_MAX_TRACKED_RUNS + 5):
        tool._charge(_ctx(platform, "s", agent_run_id=f"run_cold_{i}"))
        if i % 50 == 0:
            tool._charge(hot)

    assert len(tool._consults) == _MAX_TRACKED_RUNS
    assert "run_hot" in tool._consults  # kept: recently charged
    assert "run_cold_0" not in tool._consults  # dropped: cold and old


# --------------------------------------------------------------------------- #
# 2. the ad-hoc rolling window is preserved EXACTLY
# --------------------------------------------------------------------------- #
async def test_the_adhoc_rolling_window_still_rolls_over(platform):
    """A chat session's id is the literal string "chat" for the app's whole
    life, so a never-expiring counter would refuse every consult forever after
    the sixth — a permanent lockout from a guard meant to bound a loop. Touching
    the key on charge must not disturb that: the window's origin is `started`,
    and re-stamping it on each charge would slide the window forward forever."""
    caller = await _caller(platform)
    tool = ConsultTool(platform)
    ctx = _ctx(platform, caller.id, agent_run_id="")  # chat-shaped: no run

    for _ in range(_MAX_CONSULTS_PER_RUN):
        assert (
            await tool.execute({"agent": "reviewer", "question": "q"}, ctx)
        ).ok is True
    assert (
        await tool.execute({"agent": "reviewer", "question": "q"}, ctx)
    ).ok is False

    key, is_run = tool._budget_key(ctx)
    assert is_run is False
    count, started = tool._consults[key]
    # `started` is the FIRST charge in this window, not the latest one — the
    # counter has been re-charged six times since and it must not have moved.
    assert count == _MAX_CONSULTS_PER_RUN
    tool._consults[key] = (count, started - _ADHOC_WINDOW_S - 1)

    assert (
        await tool.execute({"agent": "reviewer", "question": "q"}, ctx)
    ).ok is True
    # The window RESTARTED rather than resuming: one spent, five left.
    assert tool._consults[key][0] == 1


async def test_a_real_runs_counter_still_never_expires_on_time(platform):
    """The other half of the same contract. An ``AgentRun`` is finite, so its
    counter is allowed to be permanent — only the run-less lane rolls over. An
    aged `started` must NOT refund a run, or the cap becomes a five-minute
    speed bump for exactly the caller it was written for."""
    tool = ConsultTool(platform)
    ctx = _ctx(platform, "s", agent_run_id="run_old")

    for _ in range(_MAX_CONSULTS_PER_RUN):
        assert tool._charge(ctx) == ""
    count, started = tool._consults["run_old"]
    tool._consults["run_old"] = (count, started - _ADHOC_WINDOW_S * 10)

    assert "consult limit reached" in tool._charge(ctx)


# --------------------------------------------------------------------------- #
# 3. the curated noun list carries no duplicate
# --------------------------------------------------------------------------- #
def test_collection_nouns_has_no_duplicate_literal():
    """A hand-curated list, so a repeated entry is a lost entry: the duplicate
    most likely displaced a noun somebody meant to add. The frozenset collapses
    it silently, so the assertion has to be made against the SOURCE."""
    source = Path(decompose.__file__).read_text(encoding="utf-8")
    block = re.search(
        r"_COLLECTION_NOUNS\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)", source, re.S
    )
    assert block is not None, "the _COLLECTION_NOUNS literal moved — retarget this"
    literals = re.findall(r'"([^"]*)"', block.group(1))

    duplicates = sorted({w for w in literals if literals.count(w) > 1})
    assert duplicates == [], f"repeated in the source literal: {duplicates}"
    # Nothing was lost while removing it: the set is exactly the source list.
    assert len(literals) == len(_COLLECTION_NOUNS)
    assert sorted(literals) == sorted(_COLLECTION_NOUNS)
    # The removed entry was a DUPLICATE, not a member — "photo" still matches.
    assert "photo" in _COLLECTION_NOUNS and "photos" in _COLLECTION_NOUNS
