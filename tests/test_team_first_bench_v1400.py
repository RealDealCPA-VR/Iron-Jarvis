"""v1.400.0 — TEAM-FIRST in the benchmark.

The user's plan for the night of 2026-10-27: three setups — Sonnet alone, the
team as today, and the team with "Hand work to my team first" on — in one
``--root``, and a report that answers the question for EACH team setup.

* The team file takes ``team_first = true|false`` under ``[planner]``
  (default false). The runner sets ``config.team_first`` to it for that
  configuration's runs, in the process only, and puts it back when the
  invocation ends — never a config.toml, never the user's home.
* It is part of the IDENTITY only when true: a file without the key (every
  team folder made before v1.400.0) and ``team_first = false`` are the same
  team; ``true`` is a new folder.
* The report names a team-first setup ``jarvis-team (team-first)``, pairs
  BOTH team setups with the one planner-alone row, answers for each, keeps who
  did what per setup, and says first — per team setup — in how many runs the
  planner handed at least one piece to a member.
* THE PROOF: a scripted planner (``tests/_fakes/bench_team_v1396``) that
  hands pieces out ONLY when the team-first words are in its system prompt —
  as the live planner did not — so the plain team reads "0 of 2" and
  team-first "2 of 2", through the real bench, the real chat lane and the real
  report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.harness_bench import team as T
from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench.bench import BenchRefused, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.ledger import PLANNER
from iron_jarvis.harness_bench.report import combined_report
from iron_jarvis.harness_bench.runners import JarvisRunner
from iron_jarvis.platform import build_platform
from tests._fakes import bench_team_v1396 as W

TASKS = load_tasks()
BOTH = [W.TASK_A, W.TASK_B]


def _toml(team_first: "bool | str | None") -> str:
    text = W.team_toml()
    if team_first is None:
        return text
    value = team_first if isinstance(team_first, str) else ("true" if team_first else "false")
    return text.replace("[planner]\n", f"[planner]\nteam_first = {value}\n", 1)


def _spec(tmp_path: Path, team_first: "bool | str | None" = None, name: str = "team.toml") -> T.TeamSpec:
    path = tmp_path / name
    path.write_text(_toml(team_first), encoding="utf-8")
    return T.load_team_file(path)


# --------------------------------------------------------------------------- #
# the file and the identity
# --------------------------------------------------------------------------- #


def test_the_file_takes_team_first_true_or_false_and_nothing_else(tmp_path):
    assert _spec(tmp_path).team_first is False
    assert _spec(tmp_path, False).team_first is False
    assert _spec(tmp_path, True).team_first is True
    for bad in ('"true"', "1", '"yes"'):
        with pytest.raises(BenchRefused, match=r"team_first is true or false \(without quotes\)"):
            _spec(tmp_path, bad)


def test_team_first_is_in_the_identity_only_when_true(tmp_path):
    absent, off, on = _spec(tmp_path, None, "a.toml"), _spec(tmp_path, False, "b.toml"), _spec(tmp_path, True, "c.toml")
    # A file without the key — every team folder made before v1.400.0 — is
    # exactly the team it was: the same identity dict, the same key.
    assert set(absent.identity()) == {"planner", "members"}
    assert absent.identity() == off.identity() and absent.key == off.key
    assert on.identity() == {**off.identity(), "team_first": True}
    assert on.key != off.key


async def test_team_first_true_is_a_new_folder_and_false_is_the_same_one(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    W.wire(platform, monkeypatch)
    home = platform.config.home

    async def folder(spec):
        runner = T.JarvisTeamRunner(platform, team=spec, round_budget=12)
        res = await run_bench(runner=runner, home=home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
        return res.config_dir, res.plan_lines

    absent, absent_lines = await folder(_spec(tmp_path, None, "a.toml"))
    off, _ = await folder(_spec(tmp_path, False, "b.toml"))
    on, on_lines = await folder(_spec(tmp_path, True, "c.toml"))
    assert absent == off
    assert on != off
    # A dry run says which it is, in the team's description.
    assert "  " + T.TEAM_FIRST_OFF_LINE in absent_lines
    assert "  " + T.TEAM_FIRST_ON_LINE in on_lines
    assert T.TEAM_FIRST_ON_LINE == ("Team-first: on (the planner is told to hand reading, test runs, first "
                                    "drafts and reviews to the team by role).")
    # Nothing was switched on by a dry run.
    assert platform.config.team_first is False


def test_the_runner_sets_the_files_value_for_its_runs_and_puts_it_back(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    cfg = platform.config
    toml = Path(cfg.home) / "config.toml"
    before = toml.read_text(encoding="utf-8") if toml.exists() else None
    runner = T.JarvisTeamRunner(platform, team=_spec(tmp_path, True))
    assert cfg.team_first is False
    runner.prepare()
    try:
        assert cfg.team_first is True
        assert MT.team_first_on(platform) is True
    finally:
        runner.close()
    assert cfg.team_first is False
    # In the process only: the home's config.toml is untouched.
    assert (toml.read_text(encoding="utf-8") if toml.exists() else None) == before
    # The other way round: a home where it was on runs a plain team with it
    # OFF (the identity says off), and gets it back after.
    cfg.team_first = True
    plain = T.JarvisTeamRunner(platform, team=_spec(tmp_path, False, "off.toml"))
    plain.prepare()
    try:
        assert cfg.team_first is False
    finally:
        plain.close()
    assert cfg.team_first is True
    # A close without a prepare changes nothing.
    T.JarvisTeamRunner(platform, team=_spec(tmp_path, False, "x.toml")).close()
    assert cfg.team_first is True


def test_close_puts_it_back_even_when_removing_a_member_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    runner = T.JarvisTeamRunner(platform, team=_spec(tmp_path, True))
    runner.prepare()
    assert platform.config.team_first is True

    def boom(name):
        raise OSError("the registry is gone")

    monkeypatch.setattr(platform.agents_registry, "remove", boom)
    with pytest.raises(OSError):
        runner.close()
    assert platform.config.team_first is False


# --------------------------------------------------------------------------- #
# the report: two team setups and one planner-alone row
# --------------------------------------------------------------------------- #


def _cfg(runner: str, key: str, *, team_first: bool = False) -> dict:
    harness = {"team": {"planner": {}, "members": [], **({"team_first": True} if team_first else {})}} \
        if runner == "jarvis-team" else {}
    return {"runner": runner, "provider": "claude-cli", "model": "sonnet", "effort": "medium", "key": key,
            "version": "1.400.0", "harness": harness}


def _planner_part(cost):
    return {"vendor": "claude", "provider": "claude-cli", "model": "sonnet", "cost_list_price_usd": cost,
            "cost_metered_usd": 0.0, "tokens_in": 100, "tokens_out": 10, "model_calls": 5, "pieces": {}}


def _member(worked: bool):
    return {"vendor": "local", "provider": "fleet-spark", "model": "glm", "cost_list_price_usd": 0.0,
            "cost_metered_usd": 0.0, "tokens_in": 500 if worked else 0, "tokens_out": 50 if worked else 0,
            "model_calls": 2 if worked else 0, "pieces": {"code": 1} if worked else {}}


def _run(task: str, passed: bool, cost: float, *, worked=None, **extra):
    parts = {PLANNER: _planner_part(cost)}
    helpers = []
    if worked is not None:
        parts["glm"] = _member(worked)
        if worked:
            helpers = [{"id": "h1", "member": "glm", "role": "code", "state": "done", "reason": "local: good record"}]
    return {"task_id": task, "tier": "frontier", "category": "bugfix", "passed": passed, "ok_run": True,
            "parts": parts, "helpers": helpers, "tool_calls": 3, "model_rounds": 2, "wall_s": 10.0,
            "tokens_in": 100, "tokens_out": 10, "cost_metered_usd": 0.0, "cost_list_price_usd": cost, **extra}


def _three_setups():
    team = (_cfg("jarvis-team", "aaaaaaaaaaaa1111"), [_run("a", True, 0.29, worked=False),
                                                      _run("b", True, 0.29, worked=False)])
    first = (_cfg("jarvis-team", "bbbbbbbbbbbb2222", team_first=True),
             [_run("a", True, 0.10, worked=True), _run("b", True, 0.12, worked=True),
              # a run that never got a fair go: counted apart, not in "of M"
              _run("c", None, 0.0, worked=False, unscored_by="infrastructure")])
    alone = (_cfg("jarvis", "cccccccccccc3333"), [_run("a", True, 0.30), _run("b", True, 0.30)])
    return team, first, alone


def test_runner_label_tells_the_two_team_setups_apart():
    team, first, alone = _three_setups()
    assert TRP.runner_label(team[0]) == "jarvis-team"
    assert TRP.runner_label(first[0]) == "jarvis-team (team-first)"
    assert TRP.runner_label(alone[0]) == "jarvis"
    # A configuration saved before v1.400.0 (no such key) is a plain team.
    old = {"runner": "jarvis-team", "harness": {"team": {"planner": {}, "members": []}}}
    assert TRP.is_team_first(old) is False and TRP.runner_label(old) == "jarvis-team"
    assert TRP.is_team_first({"runner": "jarvis", "harness": {"team": {"team_first": True}}}) is False


def test_handed_to_a_member_is_counted_per_setup():
    team, first, _alone = _three_setups()
    assert TRP.handed_count(team[1]) == (0, 2, 0)
    assert TRP.handed_count(first[1]) == (2, 2, 1)
    assert TRP.handed_line(team[1]) == "The planner handed at least one piece to a team member in 0 of 2 runs."
    assert TRP.handed_line(first[1]) == (
        "The planner handed at least one piece to a team member in 2 of 2 runs (1 did not run).")
    assert TRP.handed_line([]) == ""


def test_the_combined_report_answers_for_each_team_setup_against_the_same_alone_row():
    team, first, alone = _three_setups()
    # find_alone pairs BOTH team setups with the one planner-alone row (same
    # planner provider, model and effort).
    pairs = [team, first, alone]
    assert TRP.find_alone(*team, pairs)[0] is alone[0]
    assert TRP.find_alone(*first, pairs)[0] is alone[0]
    report = combined_report([team, first, alone])
    # Each setup its own row, the two teams told apart.
    rows = [ln for ln in report.split("\n## ", 1)[0].splitlines() if ln.startswith("| jarvis")]
    assert [ln.split(" | ")[0] for ln in rows] == ["| jarvis-team", "| jarvis-team (team-first)", "| jarvis"]
    section = report.split("## The team beside one model", 1)[1]
    plain_part, first_part = section.split("**Team bbbbbbbbbbbb, team-first**", 1)
    assert "**Team aaaaaaaaaaaa**" in plain_part
    # Both team rows (and the alone row) in each team's table.
    for part in (plain_part, first_part):
        table = [ln for ln in part.splitlines() if ln.startswith("| ") and "| jarvis" in ln]
        assert [ln.split(" | ")[1] for ln in table][:3] in (
            ["jarvis-team", "jarvis-team (team-first)", "jarvis"],
            ["jarvis-team (team-first)", "jarvis-team", "jarvis"])
    # The first number to look for, per setup, before its answer.
    assert ("**The team at work:** The planner handed at least one piece to a team member in 0 of 2 runs."
            in plain_part)
    assert ("**The team at work:** The planner handed at least one piece to a team member in 2 of 2 runs "
            "(1 did not run).") in first_part
    # Each answered against the same alone row.
    assert f"**The answer:** {TRP.NO_MEMBER_WORK}" in plain_part
    assert ("**The answer:** Yes on these runs: as many tasks, with less Claude list-price. "
            "The team passed 2 of 2, Claude alone 2 of 2") in first_part
    # Who did what, per setup: only the team-first setup's member worked.
    assert "| glm | fleet-spark / glm | code 2 |" in first_part
    assert "| glm | fleet-spark / glm | - |" in plain_part
    assert plain_part.index("The team at work") < plain_part.index("**The answer:**")


def test_a_setups_own_report_says_the_number_too():
    from iron_jarvis.harness_bench.report import config_report

    _team, first, alone = _three_setups()
    text = config_report(first[0], first[1])
    assert text.splitlines()[0] == "# Harness benchmark: jarvis-team (team-first) · claude-cli / sonnet"
    assert ("## The team at work\n\nThe planner handed at least one piece to a team member in 2 of 2 runs "
            "(1 did not run).") in text
    # A single-model setup's report has no such section.
    assert "The team at work" not in config_report(alone[0], alone[1])


# --------------------------------------------------------------------------- #
# THE PROOF, end to end
# --------------------------------------------------------------------------- #


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    w = W.wire(platform, monkeypatch)
    # The planner hands pieces out ONLY when told to work as a team first.
    w.team_words = MT.TEAM_FIRST_LEAD
    w.platform = platform
    w.home = platform.config.home
    return w


async def test_three_setups_in_one_root_the_planner_works_as_a_team_only_when_told(world, tmp_path):
    plain = await run_bench(runner=T.JarvisTeamRunner(world.platform, team=_spec(tmp_path, None), round_budget=12),
                            home=world.home, tasks=TASKS, names=BOTH, seed=0)
    assert world.platform.config.team_first is False
    first = await run_bench(
        runner=T.JarvisTeamRunner(world.platform, team=_spec(tmp_path, True, "first.toml"), round_budget=12),
        home=world.home, tasks=TASKS, names=BOTH, seed=0)
    # Put back after the invocation: the planner-alone row runs with it off.
    assert world.platform.config.team_first is False
    alone = await run_bench(
        runner=JarvisRunner(world.platform, provider=W.PLANNER[0], model=W.PLANNER[1], round_budget=12),
        home=world.home, tasks=TASKS, names=BOTH, seed=0)
    assert len({plain.config_dir, first.config_dir, alone.config_dir}) == 3

    # The plain team: shown the team, handed it nothing (the live night's shape).
    assert [r["helpers"] for r in plain.records] == [[], []]
    assert not any(TRP.handed_to_a_member(r) for r in plain.records)
    # Team-first: every run handed pieces to members by role.
    assert all(TRP.handed_to_a_member(r) for r in first.records)
    by = {r["task_id"]: r for r in first.records}
    assert {h["member"] for h in by[W.TASK_A]["helpers"]} == {"glm", "codex"}
    assert by[W.TASK_A]["passed"] is True
    for rec in [*plain.records, *first.records, *alone.records]:
        assert rec["ok_run"] is True and rec["ledger_ok"] is True, rec.get("error")

    combined = (world.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    section = combined.split("## The team beside one model", 1)[1]
    first_key = first.config_dir.name.rsplit("-", 1)[1]
    plain_key = plain.config_dir.name.rsplit("-", 1)[1]
    head = combined.split("\n## ", 1)[0]
    assert "| jarvis-team (team-first) |" in head and "| jarvis-team |" in head and "| jarvis |" in head
    plain_part = section.split(f"**Team {plain_key}**", 1)[1].split("**Team ", 1)[0]
    first_part = section.split(f"**Team {first_key}, team-first**", 1)[1].split("**Team ", 1)[0]
    assert "The planner handed at least one piece to a team member in 0 of 2 runs." in plain_part
    assert "The planner handed at least one piece to a team member in 2 of 2 runs." in first_part
    assert f"**The answer:** {TRP.NO_MEMBER_WORK}" in plain_part
    assert "**The answer:** Yes on these runs: as many tasks, with less Claude list-price." in first_part
    # Who did what stays per setup.
    assert "| glm | fleet-spark / glm-5.3-flash | code 2 |" in first_part
    # v1.401.0 (moved on purpose): team-first also hands each reply that
    # changed files to the reviewer at the finish (harness/finish) — task A
    # (glm changed recur/rules.py) got a second review; task B changed
    # nothing, so it got none. The planner's own two + the finish's one.
    assert "| codex | codex-cli / gpt-6-astra | review 3 |" in first_part
    assert "code 2" not in plain_part and "review 2" not in plain_part
    # Each setup's own report says its number.
    first_report = (first.config_dir / "report.md").read_text(encoding="utf-8")
    assert "# Harness benchmark: jarvis-team (team-first) · claude-cli / claude-opus-4-8" in first_report
    assert "in 2 of 2 runs." in first_report


def test_the_command_line_says_the_number_after_a_team_setup(tmp_path, monkeypatch):
    """``bench-harness run --runner jarvis-team --team <file> --with-alone``:
    the team-first team, then its planner alone on the same platform (with
    the setting back off), and the first number printed for the team."""
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench import bench as bench_mod
    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    real = bench_mod.make_runner
    made = []

    def make_runner(*a, **kw):
        runner = real(*a, **kw)
        w = W.wire(runner.platform, monkeypatch)
        w.team_words = MT.TEAM_FIRST_LEAD
        made.append(runner)
        return runner

    monkeypatch.setattr(bench_mod, "make_runner", make_runner)
    team = tmp_path / "team.toml"
    team.write_text(_toml(True), encoding="utf-8")
    root = tmp_path / "bench-root"
    out = CliRunner().invoke(bench_app, [
        "run", "--runner", "jarvis-team", "--root", str(root), "--tier", "frontier", "--tasks", W.TASK_A,
        "--team", str(team), "--rounds", "12", "--with-alone",
    ])
    assert out.exit_code == 0, out.output
    lines = out.output.splitlines()
    said = [ln for ln in lines if ln.startswith("The planner handed")]
    # Once: the team's; the planner alone gets no such line.
    # v1.401.0 (moved on purpose): the finish check's own review is said apart
    # (never counted as the planner's piece — team_report review).
    assert said == ["The planner handed at least one piece to a team member in 1 of 1 run. Iron Jarvis's own "
                    "finish check handed the change to the team's reviewer in 1 of 1 run."], out.output
    assert lines.index(said[0]) < next(i for i, ln in enumerate(lines) if "jarvis-claude-cli-" in ln)
    (runner,) = made
    assert runner.platform.config.team_first is False
