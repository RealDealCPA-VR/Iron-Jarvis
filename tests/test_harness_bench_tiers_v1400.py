"""v1.400.0 — several tiers in ONE benchmark run (``--tier frontier,team``).

Tonight's plan runs the twelve frontier tasks AND the six team tasks, three
repeats, in three setups. ``--tier`` took one value (``core``, ``hard``,
``frontier``, ``team`` or ``all`` — and ``all`` adds the thirty core and hard
tasks), so two tiers meant two invocations per setup, two plans and two
reports. Pinned here:

* ``--tier frontier,team`` selects exactly the frontier and team tasks, in
  the order ``all`` gives them, however the tiers are typed (``team,frontier``,
  spaces, a repeat);
* an unknown tier in the list, ``all`` beside a tier, or nothing is refused in
  the same words as before (plus how to name several);
* a tier is not part of a setup's identity: the results folder is the same
  for ``--tier frontier`` and ``--tier frontier,team``, and a single tier or
  ``all`` keeps its spelling where a spelling is part of an identity (the
  refine loop's key), while several tiers get ONE spelling whatever their order;
* the plan of a two-tier run lists both tiers' runs, and the stale-run rule
  (per task, never per tier) leaves the other tier's runs alone when a later
  run plans only one tier;
* the report's per-tier table shows both tiers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.harness_bench.catalog import TIERS, TaskError, canonical_tier, select_tasks, tier_names
from tests.test_harness_bench_v1363 import FIXTURES, TASKS, _shim

FRONTIER = sorted(t.id for t in TASKS.values() if t.tier == "frontier")
TEAM = sorted(t.id for t in TASKS.values() if t.tier == "team")


def test_several_tiers_select_each_of_them_in_the_order_all_gives():
    tasks = list(TASKS.values())
    every = [t.id for t in select_tasks(tasks, tier="all", seed=11)]
    both = [t.id for t in select_tasks(tasks, tier="frontier,team", seed=11)]
    assert sorted(both) == sorted(FRONTIER + TEAM) and len(both) == 18
    assert both == [i for i in every if i in set(FRONTIER + TEAM)]
    for spelling in ("team,frontier", " team , frontier ", "frontier,team,team", "frontier,,team", "team,frontier,"):
        assert [t.id for t in select_tasks(tasks, tier=spelling, seed=11)] == both, spelling
    # One tier is exactly what it was.
    for tier in TIERS:
        one = [t.id for t in select_tasks(tasks, tier=tier, seed=11)]
        assert one == [i for i in every if TASKS[i].tier == tier], tier
    # Every tier named is "all".
    assert [t.id for t in select_tasks(tasks, tier=",".join(reversed(TIERS)), seed=11)] == every
    # --max-tasks still cuts after the tiers are chosen.
    assert [t.id for t in select_tasks(tasks, tier="team,frontier", seed=11, max_tasks=4)] == both[:4]


@pytest.mark.parametrize("bad", ["medium", "frontier,medium", "all,team", "team,all", "", " ", ",", "Team"])
def test_a_tier_list_with_a_wrong_name_is_refused(bad):
    with pytest.raises(TaskError, match="--tier must be one of core, hard, frontier, team, all, or several"):
        select_tasks(list(TASKS.values()), tier=bad)
    with pytest.raises(TaskError):
        tier_names(bad)


def test_one_spelling_whatever_the_order_and_single_tiers_keep_theirs():
    assert tier_names("all") == TIERS
    assert tier_names("team, frontier") == ("frontier", "team")
    for tier in (*TIERS, "all"):
        assert canonical_tier(tier) == tier
    assert canonical_tier("team,frontier") == canonical_tier("frontier, team") == "frontier,team"
    assert canonical_tier("medium") == "medium"  # refused later, where the tasks are chosen


def test_the_refine_loops_key_is_the_same_whatever_order_the_tiers_were_typed_in():
    from iron_jarvis.harness_bench.refine import RefineSettings

    one = RefineSettings(provider="claude-cli", model="sonnet", tier="frontier")
    assert one.identity()["tier"] == "frontier"
    every = RefineSettings(provider="claude-cli", model="sonnet")
    assert every.identity()["tier"] == "all"
    a = RefineSettings(provider="claude-cli", model="sonnet", tier="team,frontier")
    b = RefineSettings(provider="claude-cli", model="sonnet", tier="frontier, team")
    assert a.key == b.key and a.identity()["tier"] == "frontier,team"
    assert a.key != one.key


def _dry(tmp_path: Path, *extra: str):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    out = CliRunner().invoke(bench_app, ["run", "--runner", "claude", "--binary", str(claude), "--root", str(root),
                                         "--model", "sonnet", "--dry-run", *extra])
    return out


def test_the_command_line_runs_two_tiers_with_repeats_in_one_dry_run(tmp_path):
    out = _dry(tmp_path, "--tier", "frontier,team", "--repeats", "3")
    assert out.exit_code == 0, out.output
    lines = [line for line in out.output.splitlines() if "would run" in line]
    assert len(lines) == 18 * 3
    assert {line.split(" [")[0].strip() for line in lines} == set(FRONTIER + TEAM)
    assert all(("[frontier, " in line) or ("[team, " in line) for line in lines)
    for k in (1, 2, 3):
        assert sum(f" repeat {k}:" in line for line in lines) == 18, k
    assert "18 task(s), 3 repeats each:" in out.output


def test_a_tier_is_not_part_of_the_setups_identity(tmp_path):
    """The same setup keeps ONE results folder (and so its stored runs and its
    seed) whichever tiers an invocation selects."""

    def folder(*tier):
        out = _dry(tmp_path, "--tier", *tier)
        assert out.exit_code == 0, out.output
        head = [line for line in out.output.splitlines() if "task(s)" in line][0]
        return head.split(" (seed ")[0]

    assert folder("frontier") == folder("frontier,team") == folder("team,frontier") == folder("team") == folder("all")


def test_the_command_line_refuses_a_wrong_tier_list(tmp_path):
    out = _dry(tmp_path, "--tier", "frontier,teams")
    assert out.exit_code == 2
    assert "--tier must be one of core, hard, frontier, team, all, or several tiers joined by commas" in out.output


def test_the_stale_rule_is_per_task_so_a_one_tier_run_leaves_the_other_tier_alone(tmp_path):
    """A two-tier run plans both tiers' runs; a later run of ONE tier with
    fewer repeats marks only that tier's extra repeats stale."""
    from iron_jarvis.harness_bench.bench import PLAN_NAME, _mark_stale
    from iron_jarvis.harness_bench.ledger import run_key

    tasks = list(TASKS.values())
    both = select_tasks(tasks, tier="frontier,team", seed=2)
    planned = [run_key(t.id, k) for k in (1, 2, 3) for t in both]
    assert {p.split(".r")[0] for p in planned} == set(FRONTIER + TEAM)
    records = [{"task_id": t.id, "tier": t.tier, **({"repeat": k} if k > 1 else {})}
               for k in (1, 2, 3) for t in both]
    (tmp_path / PLAN_NAME).write_text(json.dumps({"planned": planned}), encoding="utf-8")
    assert not any(r.get("stale") for r in _mark_stale(tmp_path, [dict(r) for r in records]))
    later = [run_key(t.id, 1) for t in select_tasks(tasks, tier="team", seed=2)]
    (tmp_path / PLAN_NAME).write_text(json.dumps({"planned": later}), encoding="utf-8")
    marked = _mark_stale(tmp_path, [dict(r) for r in records])
    stale = {(r["task_id"], r.get("repeat", 1)) for r in marked if r.get("stale")}
    assert stale == {(t, k) for t in TEAM for k in (2, 3)}


def test_the_report_shows_both_tiers():
    from iron_jarvis.harness_bench.report import config_report, summarize

    records = [{"task_id": t, "tier": "frontier", "category": TASKS[t].category, "passed": i % 2 == 0}
               for i, t in enumerate(FRONTIER)]
    records += [{"task_id": t, "tier": "team", "category": TASKS[t].category, "passed": True} for t in TEAM]
    s = summarize(records)
    assert list(s["per_tier"]) == ["frontier", "team"]
    assert (s["per_tier"]["frontier"]["passed"], s["per_tier"]["frontier"]["scored"]) == (6, 12)
    assert (s["per_tier"]["team"]["passed"], s["per_tier"]["team"]["scored"]) == (6, 6)
    text = config_report({"runner": "claude", "model": "sonnet"}, records)
    by_tier = text.split("## By tier", 1)[1].split("##", 1)[0]
    assert "| frontier | 50% | 6 / 12 |" in by_tier and "| team | 100% | 6 / 6 |" in by_tier
