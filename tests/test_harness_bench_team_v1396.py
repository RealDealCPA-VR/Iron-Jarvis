"""v1.396.0 — the long-jobs plan's Phase 7: MEASURED.

A benchmark row for the MODEL TEAM beside the single-model rows, through the
real bench code with SCRIPTED models only (``tests/_fakes/bench_team_v1396``:
a claude-cli planner whose answers carry the vendor's usage headers, a Codex
reviewer whose answers carry its account snapshot, a local fleet member) —
no real CLI, no network:

* THE PROOF: two frontier tasks × two repeats for the team and for "Claude
  alone" (the scripted planner alone, in the same home); the team passes a
  task Claude alone fails and the other way round, and the report counts it;
  the report row carries exact AND measured usage; the answer line says only
  what the numbers show; the ledger adds up for runs of three models;
* a window that resets inside the span is "not comparable"; a run with no
  reading is "not known";
* a changed team is a new folder; the existing runners' identities and
  fingerprints are unchanged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import runners as R
from iron_jarvis.harness_bench import team as T
from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench import usage as U
from iron_jarvis.harness_bench.bench import BenchRefused, Configuration, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.ledger import (
    PLANNER,
    BudgetLedger,
    BudgetMismatch,
    CallEntry,
    part_differences,
)
from iron_jarvis.harness_bench.runners import JarvisRunner
from iron_jarvis.platform import build_platform
from tests._fakes import bench_team_v1396 as W

TASKS = load_tasks()
BOTH = [W.TASK_A, W.TASK_B]


def _spec(tmp_path: Path, **kw) -> T.TeamSpec:
    path = tmp_path / "team.toml"
    path.write_text(W.team_toml(**kw), encoding="utf-8")
    return T.load_team_file(path)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    w = W.wire(platform, monkeypatch)
    w.platform = platform
    w.home = platform.config.home
    return w


async def _team(world, tmp_path, *, repeats=2, names=BOTH, **kw):
    runner = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path, **kw), round_budget=12)
    return await run_bench(runner=runner, home=world.home, tasks=TASKS, names=names, seed=0, repeats=repeats)


async def _alone(world, *, repeats=2, names=BOTH):
    runner = JarvisRunner(world.platform, provider=W.PLANNER[0], model=W.PLANNER[1], round_budget=12)
    return await run_bench(runner=runner, home=world.home, tasks=TASKS, names=names, seed=0, repeats=repeats)


def _by(res):
    return {(r["task_id"], int(r.get("repeat") or 1)): r for r in res.records}


# --------------------------------------------------------------------------- #
# THE PROOF
# --------------------------------------------------------------------------- #


async def test_the_team_row_beside_claude_alone_answers_the_question(world, tmp_path):
    team = await _team(world, tmp_path)
    # The team's members are gone from the home once it ended: the next row
    # (Claude alone, in the SAME home) sees no team.
    assert not [r for r in world.platform.agents_registry.list() if r.name in ("glm", "codex")]
    alone = await _alone(world)

    t, a = _by(team), _by(alone)
    # The scripts: the team passes A and fails B; Claude alone the reverse.
    for k in (1, 2):
        assert t[(W.TASK_A, k)]["passed"] is True, t[(W.TASK_A, k)]["grade_output"]
        assert t[(W.TASK_B, k)]["passed"] is False
        assert a[(W.TASK_A, k)]["passed"] is False
        assert a[(W.TASK_B, k)]["passed"] is True, a[(W.TASK_B, k)]["grade_reason"]
    # Every run's numbers add up: each part against the calls its own model
    # made, and the totals against the parts (three providers in one run).
    for rec in [*team.records, *alone.records]:
        assert rec["ledger_ok"] is True, rec["error"]
        assert rec["ok_run"] is True, rec["error"]

    # --- what each team run records ------------------------------------- #
    rec = t[(W.TASK_A, 1)]
    parts = rec["parts"]
    assert set(parts) == {PLANNER, "glm", "codex"}
    assert (parts[PLANNER]["provider"], parts[PLANNER]["model"]) == W.PLANNER
    assert (parts["glm"]["provider"], parts["glm"]["model"]) == W.LOCAL
    assert (parts["codex"]["provider"], parts["codex"]["model"]) == W.CODEX
    assert parts["glm"]["pieces"] == {"code": 1} and parts["codex"]["pieces"] == {"review": 1}
    assert parts["glm"]["vendor"] == "local" and parts["codex"]["vendor"] == "codex"
    assert parts[PLANNER]["vendor"] == "claude"
    # Tokens: the planner's three calls (two tool rounds + the finish check's
    # round), the local member's two, the reviewer's one.
    assert (parts["glm"]["tokens_in"], parts["glm"]["tokens_out"], parts["glm"]["model_calls"]) == (1400, 160, 2)
    assert (parts["codex"]["tokens_in"], parts["codex"]["model_calls"]) == (300, 1)
    assert parts[PLANNER]["model_calls"] == 4
    assert parts[PLANNER]["tokens_in"] == 4 * 400
    # Money: list-price equivalents (both subscriptions), free local, nothing billed.
    assert parts[PLANNER]["cost_list_price_usd"] == pytest.approx(4 * 0.005)
    assert parts["codex"]["cost_list_price_usd"] == pytest.approx(0.002)
    assert parts["glm"]["cost_list_price_usd"] == 0.0 and parts["glm"]["cost_metered_usd"] == 0.0
    assert rec["cost_metered_usd"] == 0.0
    assert rec["tokens_in"] == sum(p["tokens_in"] for p in parts.values())
    assert rec["cost_list_price_usd"] == pytest.approx(sum(p["cost_list_price_usd"] for p in parts.values()))
    # Who did what: the router's own reasons, from the helper rows.
    helpers = {h["id"]: h for h in rec["helpers"]}
    assert helpers["p-a1"]["member"] == "glm" and helpers["p-a1"]["role"] == "code"
    assert helpers["p-a1"]["reason"].startswith("local:")
    assert helpers["p-a2"]["member"] == "codex" and helpers["p-a2"]["role"] == "review"
    assert helpers["p-a2"]["reason"]
    # Per account: the vendor's windows, first and last reading, from the
    # calls' own answers (Iron-Proxy is off here: this PC's own sign-in).
    claude = rec["accounts"][f"{W.PLANNER[0]}:{U.THIS_PC}"]
    assert claude["vendor"] == "claude" and claude["calls"] == 4
    five = claude["windows"]["5h"]
    assert five["readings"] == 4 and five["one_period"] is True and five["window_mins"] == 300
    assert five["last"]["percent"] > five["first"]["percent"]
    assert set(claude["windows"]) == {"5h", "7d"}
    codex = rec["accounts"][f"{W.CODEX[0]}:{U.THIS_PC}"]
    assert set(codex["windows"]) == {"primary", "secondary"} and codex["windows"]["primary"]["readings"] == 1
    # The local member reports no window, so it has no account row.
    assert not [k for k in rec["accounts"] if k.startswith(W.LOCAL[0])]
    # The router's record after the run, per member and role.
    tr = rec["team_records"]
    assert tr["members"]["glm"]["code"]["runs"] >= 1 and "read" in tr["members"]["glm"]
    assert tr["thresholds"]["min_runs"] == 3 and tr["thresholds"]["new_work_max_percent"] == 80.0

    # --- the report --------------------------------------------------------- #
    combined = (world.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    section = combined.split("## The team beside one model", 1)[1]
    assert "| jarvis-team |" in section and "| jarvis |" in section
    # The answer, computed here from the records themselves.
    team_lp = sum(r["parts"][PLANNER]["cost_list_price_usd"] for r in team.records)
    alone_lp = sum(r["cost_list_price_usd"] for r in alone.records)
    pct = round((alone_lp - team_lp) / alone_lp * 100)
    # (v1.396.0 review: the pass comparison leads, and what the team used
    # beyond Claude is said beside the Claude list price.)
    codex_lp = sum(r["parts"]["codex"]["cost_list_price_usd"] for r in team.records)
    local_in = sum(r["parts"]["glm"]["tokens_in"] for r in team.records)
    local_out = sum(r["parts"]["glm"]["tokens_out"] for r in team.records)
    assert (f"**The answer:** Yes on these runs: as many tasks, with less Claude list-price. "
            f"The team passed 2 of 4, Claude alone 2 of 4 (the 4 runs both made and both were scored on). "
            f"Claude list-price: ${team_lp:.4f} for the team vs ${alone_lp:.4f} alone ({pct}% less). "
            f"Beyond Claude, the team also used ${codex_lp:.4f} of Codex list-price; "
            f"{local_in:,} tokens in / {local_out:,} out on local models.") in section
    # It counts the task each passes that the other fails.
    assert f"- {W.TASK_A}: 2/2 / 0/2" in section and f"- {W.TASK_B}: 0/2 / 2/2" in section
    # Exact AND measured usage on the team's row, beside its pass rate per
    # repeat, mean and range.
    team_row = next(line for line in section.splitlines() if line.startswith("| ") and "| jarvis-team |" in line)
    assert "| 2 | 50% (50%–50%) | 1/2, 1/2 | 2 / 4 |" in team_row
    exact_claude = sum(r["parts"][PLANNER]["tokens_in"] for r in team.records)
    assert f"| {exact_claude:,} in / " in team_row and f"${team_lp:.4f} list price" in team_row
    first5 = min(r["accounts"][f"{W.PLANNER[0]}:{U.THIS_PC}"]["windows"]["5h"]["first"]["percent"]
                 for r in team.records)
    last5 = max(r["accounts"][f"{W.PLANNER[0]}:{U.THIS_PC}"]["windows"]["5h"]["last"]["percent"]
                for r in team.records)
    assert f"5-hour window +{round(last5 - first5, 1):g} points ({first5}% to {last5}%" in team_row
    codex_key = f"{W.CODEX[0]}:{U.THIS_PC}"
    p_first = min(r["accounts"][codex_key]["windows"]["primary"]["first"]["percent"] for r in team.records)
    p_last = max(r["accounts"][codex_key]["windows"]["primary"]["last"]["percent"] for r in team.records)
    assert f"5-hour window +{round(p_last - p_first, 1):g} points ({p_first}% to {p_last}%, 4 readings)" in team_row
    alone_row = next(line for line in section.splitlines() if line.startswith("| ") and "| jarvis |" in line)
    assert "| not used |" in alone_row  # Claude alone never called Codex
    # Who did what and the router's record, the numbers to tune from — the
    # record as it stood after the NEWEST run (all four pieces of each).
    assert "| glm | fleet-spark / glm-5.3-flash | code 4 |" in section
    assert "| codex | codex-cli / gpt-6-astra | review 4 |" in section
    assert '- glm (code): "local: no record yet for coding" × 1' in section
    assert "| glm | code | 4 | 4 | 0 | good (4 of 4 passed) |" in section
    assert "| codex | review | 4 | 4 | 0 | good (4 of 4 passed) |" in section
    assert "| glm | read | 0 | 0 | 0 | no record yet |" in section
    assert "a record is judged after 3 pieces" in section
    # Each setup's own report: the per-task groups count right.
    t_report = (team.config_dir / "report.md").read_text(encoding="utf-8")
    assert f"- Passed every time (1): {W.TASK_A} (2/2)" in t_report
    assert f"- Never passed (1): {W.TASK_B} (0/2)" in t_report
    assert "## Who did what" in t_report and "## What the team router learned" in t_report
    a_report = (alone.config_dir / "report.md").read_text(encoding="utf-8")
    assert f"- Passed every time (1): {W.TASK_B} (2/2)" in a_report
    assert "default" not in combined.lower().replace("defaults", "")


async def test_a_window_that_resets_inside_the_span_is_not_comparable(world, tmp_path):
    first = await _alone(world, repeats=1, names=[W.TASK_B])
    assert first.records[0]["ledger_ok"] is True
    world.claude.reset()  # the 5-hour window resets between the two runs
    second = await _alone(world, repeats=2, names=[W.TASK_B])
    report = (second.config_dir / "report.md").read_text(encoding="utf-8")
    measured = TRP.measured_usage(json.loads((second.config_dir / "results.json").read_text())["records"])
    wins = {w["window"]: w["result"] for w in measured["claude"][0]["windows"]}
    assert wins["5h"]["state"] == "not_comparable" and "reset in between" in wins["5h"]["why"]
    # ...while the weekly window, which did not reset, is measured.
    assert wins["7d"]["state"] == "measured" and wins["7d"]["points"] > 0
    assert "5-hour window not comparable: the window reset in between" in report
    assert "weekly window +" in report


async def test_a_run_with_no_reading_is_not_known_never_zero(world, tmp_path):
    world.claude.silent = True
    res = await _alone(world, repeats=1, names=[W.TASK_B])
    rec = res.records[0]
    assert rec["accounts"][f"{W.PLANNER[0]}:{U.THIS_PC}"]["windows"] == {}
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    usage_row = next(line for line in report.splitlines() if line.startswith("| Claude |"))
    assert "not known: no reading came" in usage_row
    assert "0%" not in usage_row and "+0" not in usage_row
    # A run that recorded no accounts at all — Claude Code's own CLI, or a
    # Jarvis run from before v1.396.0 — used Claude and is "not known" too.
    cli = {"runner": "claude", "provider": "claude", "model": "sonnet"}
    cli_run = {"task_id": "a", "passed": True, "tokens_in": 10, "tokens_out": 2, "model_calls": 1,
               "cost_metered_usd": 0.0, "cost_list_price_usd": 0.01}
    u = TRP.summarize_usage(cli, [cli_run])
    assert TRP.measured_words(u["measured"], "claude", u["vendors"]) == "not known: no reading came"
    assert TRP.measured_words(u["measured"], "codex", u["vendors"]) == "not used"
    assert TRP.result_words(U.measured_span([])) == "not known: no reading came"


async def test_a_changed_team_is_a_new_folder_never_a_reused_record(world, tmp_path):
    team = await _team(world, tmp_path, repeats=1, names=[W.TASK_A])
    assert team.records[0]["passed"] is True
    again = await _team(world, tmp_path, repeats=1, names=[W.TASK_A])
    assert again.config_dir == team.config_dir and again.records[0]["reused"] is True
    # The same runner settings (the round budget _team gives), only the team
    # changed: another folder, nothing reused.
    changed = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path, local_model="glm-5.3-air"), round_budget=12)
    dry = await run_bench(runner=changed, home=world.home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
    assert dry.config_dir != team.config_dir
    assert any("would run" in line for line in dry.plan_lines)
    roles = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path, codex_roles='["review", "test"]'),
                               round_budget=12)
    other = await run_bench(runner=roles, home=world.home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
    assert other.config_dir not in (team.config_dir, dry.config_dir)
    same = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path), round_budget=12)
    reread = await run_bench(runner=same, home=world.home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
    assert reread.config_dir == team.config_dir
    assert any("would reuse the stored result" in line for line in reread.plan_lines)
    # The members' step limit is part of the team's setup too (v1.396.0
    # review: a member's own — the planner's round budget unless the file
    # names one — never the home's ``max_agent_steps``).
    path = tmp_path / "steps.toml"
    path.write_text(W.team_toml().replace('roles = ["read", "code"]\n', 'roles = ["read", "code"]\nmax_steps = 30\n'),
                    encoding="utf-8")
    capped = T.JarvisTeamRunner(world.platform, team=T.load_team_file(path), round_budget=12)
    longer = await run_bench(runner=capped, home=world.home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
    assert longer.config_dir not in (team.config_dir, dry.config_dir, other.config_dir)
    steps = world.platform.config.max_agent_steps
    world.platform.config.max_agent_steps = steps + 8
    try:
        home_steps = await run_bench(runner=same, home=world.home, tasks=TASKS, names=[W.TASK_A], dry_run=True)
    finally:
        world.platform.config.max_agent_steps = steps
    assert home_steps.config_dir == team.config_dir


async def test_a_dry_run_lists_the_team_and_the_tasks_and_calls_nothing(world, tmp_path):
    runner = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path))
    res = await run_bench(runner=runner, home=world.home, tasks=TASKS, names=BOTH, dry_run=True, repeats=2)
    text = "\n".join(res.plan_lines)
    assert "Team: planner claude-cli / claude-opus-4-8; 2 member(s):" in text
    assert "  glm: fleet-spark / glm-5.3-flash, roles read, code" in text
    assert "  codex: codex-cli / gpt-6-astra, roles review" in text
    assert f"  {W.TASK_A} [frontier, bugfix] repeat 2: would run" in text
    assert world.calls == []  # no model was called
    assert not world.platform.agents_registry.list()  # nothing was made
    assert not (res.config_dir / "config.json").exists()


# --------------------------------------------------------------------------- #
# the ledger for runs of several models
# --------------------------------------------------------------------------- #


def _call(who: str, tin: int, listed: float, provider: str = "claude-cli") -> CallEntry:
    return CallEntry(provider=provider, model="m", tokens_in=tin, tokens_out=1, tokens_cached=0,
                     cost_metered_usd=0.0, cost_list_price_usd=listed, who=who)


def _part(tin: int, tout: int, listed: float, receipts: int = 1) -> dict:
    return {"tokens_in": tin, "tokens_out": tout, "cost_metered_usd": 0.0, "cost_list_price_usd": listed,
            "receipts": receipts}


def test_each_part_must_equal_the_calls_its_own_model_made():
    calls = [_call(PLANNER, 10, 0.01), _call(PLANNER, 5, 0.005), _call("glm", 7, 0.0, "fleet-spark")]
    good = {PLANNER: _part(15, 2, 0.015), "glm": _part(7, 1, 0.0)}
    assert part_differences(good, calls) == []
    # A part that claims the other's tokens: named.
    swapped = {PLANNER: _part(16, 2, 0.015), "glm": _part(6, 1, 0.0)}
    diff = part_differences(swapped, calls)
    assert any(d.startswith("planner: tokens_in") for d in diff) and any(d.startswith("glm: tokens_in") for d in diff)
    # A call by a member no part carries: named.
    assert "model calls by glm that no part of the run carries" in part_differences(
        {PLANNER: _part(15, 2, 0.015)}, calls)


def test_money_holds_within_each_receipts_rounding_and_tokens_exactly():
    calls = [_call(PLANNER, 10, 0.0000004), _call(PLANNER, 10, 0.0000004)]
    # Two receipts, each half a millionth away from its calls: within the rule.
    assert part_differences({PLANNER: _part(20, 2, 0.0000018, receipts=2)}, calls) == []
    # A missing call is far outside it.
    assert part_differences({PLANNER: _part(20, 2, 0.05, receipts=2)}, calls)
    # A token is never rounded.
    assert part_differences({PLANNER: _part(21, 2, 0.0000008, receipts=2)}, calls)


def test_a_run_whose_parts_do_not_add_up_raises():
    ledger = BudgetLedger()
    roll = ledger.begin("t", "jarvis-team")
    roll.add(_call(PLANNER, 10, 0.01))
    roll.add(_call("glm", 4, 0.0, "fleet-spark"))
    record = {"tokens_in": 14, "tokens_out": 2, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.01,
              "parts": {PLANNER: _part(10, 1, 0.01), "glm": _part(4, 1, 0.0)}}
    ledger.verify_record(record, roll)
    roll2 = ledger.begin("t2", "jarvis-team")
    roll2.add(_call(PLANNER, 10, 0.01))
    roll2.add(_call("glm", 4, 0.0, "fleet-spark"))
    # The totals match the ledger, but the planner's part took the member's tokens.
    bad = {**record, "parts": {PLANNER: _part(14, 2, 0.01), "glm": _part(0, 0, 0.0)}}
    with pytest.raises(BudgetMismatch, match="planner: tokens_in"):
        ledger.verify_record(bad, roll2)


def test_a_record_of_several_receipts_may_sit_one_rounding_per_receipt_away():
    """The planner's receipt rounds to the millionth, each helper's to the
    billionth (the existing rule, per receipt): a run of three receipts may
    sit up to three halves of a millionth from its calls — never more."""
    calls = [_call(PLANNER, 1, 0.1000004), _call("glm", 1, 0.0, "fleet-spark"),
             _call("codex", 1, 0.0200004, "codex-cli")]
    parts = {PLANNER: _part(1, 1, 0.1), "glm": _part(1, 1, 0.0), "codex": _part(1, 1, 0.02)}
    record = {"tokens_in": 3, "tokens_out": 3, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.12,
              "parts": parts}
    ledger = BudgetLedger()
    roll = ledger.begin("t", "jarvis-team")
    for c in calls:
        roll.add(c)
    ledger.verify_record(record, roll)  # 8e-7 off over three receipts: within
    roll2 = ledger.begin("t2", "jarvis-team")
    for c in calls:
        roll2.add(c)
    far = {**record, "cost_list_price_usd": 0.1200020,
           "parts": {**parts, PLANNER: _part(1, 1, 0.1000016)}}
    with pytest.raises(BudgetMismatch):
        ledger.verify_record(far, roll2)


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


def test_the_command_line_runs_a_team_from_its_file(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench import bench as bench_mod
    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    # v1.396.0 review: the planner and members are checked before anything
    # is listed — the scripted world's models stand in for the real ones.
    real = bench_mod.make_runner

    def make_runner(*a, **kw):
        runner = real(*a, **kw)
        W.wire(runner.platform, monkeypatch)
        return runner

    monkeypatch.setattr(bench_mod, "make_runner", make_runner)
    team = tmp_path / "team.toml"
    team.write_text(W.team_toml(), encoding="utf-8")
    root = tmp_path / "bench-root"
    base = ["run", "--runner", "jarvis-team", "--root", str(root), "--tier", "frontier", "--tasks", W.TASK_A]
    out = CliRunner().invoke(bench_app, [*base, "--team", str(team), "--repeats", "3", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "Team: planner claude-cli / claude-opus-4-8; 2 member(s):" in out.output
    assert f"{W.TASK_A} [frontier, bugfix] repeat 3: would run" in out.output
    assert "jarvis-team-claude-cli-claude-opus-4-8-" in out.output
    out = CliRunner().invoke(bench_app, base)
    assert out.exit_code == 2 and "Name the team file with --team" in out.output
    out = CliRunner().invoke(bench_app, [*base, "--team", str(team), "--model", "sonnet"])
    assert out.exit_code == 2 and "--provider and --model do not apply to --runner jarvis-team" in out.output
    plain = ["run", "--runner", "jarvis", "--provider", "claude-cli", "--model", "sonnet", "--root", str(root)]
    out = CliRunner().invoke(bench_app, [*plain, "--team", str(team), "--dry-run"])
    assert out.exit_code == 2 and "--team applies to --runner jarvis-team only." in out.output
    out = CliRunner().invoke(bench_app, [*base, "--team", str(tmp_path / "missing.toml"), "--dry-run"])
    assert out.exit_code == 2 and "could not be read" in out.output


# --------------------------------------------------------------------------- #
# readings
# --------------------------------------------------------------------------- #


def test_readings_are_read_as_iron_proxy_reads_them_and_never_invented():
    got = U.readings_from({"ratelimit_headers": {
        "Anthropic-Ratelimit-Unified-5h-Utilization": "0.4237",
        "anthropic-ratelimit-unified-5h-reset": "1900003600",
        "anthropic-ratelimit-unified-7d-utilization": "9",  # 900%: past Iron-Proxy's bound
        "anthropic-ratelimit-unified-7d-reset": "2030-01-01T00:00:00Z",
    }}, at=5.0)
    assert got == [{"window": "5h", "window_mins": 300, "percent": 42.4,
                    "resets_at": "2030-03-17T18:46:40Z", "at": 5.0}]
    codex = U.readings_from({"rate_limits": {"primary": {"usedPercent": 61, "windowDurationMins": 300,
                                                         "resetsAt": 1900000000},
                                             "secondary": {"usedPercent": "x"}}}, at=1.0)
    assert codex == [{"window": "primary", "window_mins": 300, "percent": 61.0,
                      "resets_at": "2030-03-17T17:46:40Z", "at": 1.0}]
    assert U.readings_from({}, at=1.0) == [] and U.readings_from(None, at=1.0) == []


def test_an_unpinned_call_under_iron_proxy_names_no_account():
    """Which account a call ran on is read, never guessed: the pin's; this
    PC's own sign-in when Iron-Proxy is off or holds no account of that
    vendor; NOT KNOWN when Iron-Proxy picked one per call."""
    from types import SimpleNamespace

    from iron_jarvis.iron_proxy import pin as P
    from tests._fakes.team_world_v1395 import FakeSvc

    off = SimpleNamespace(iron_proxy=SimpleNamespace(enabled=False))
    assert R.call_account(off, "claude-cli") == (U.THIS_PC, U.THIS_PC_WORDS)
    assert R.call_account(off, "fleet-spark") == ("", "")
    svc = FakeSvc()
    svc.add("c-work", "anthropic", "Work Max", percent=12)
    on = SimpleNamespace(iron_proxy=svc)
    assert R.call_account(on, "claude-cli") == ("", "")  # Iron-Proxy picks per call: not known
    assert R.call_account(on, "codex-cli") == (U.THIS_PC, U.THIS_PC_WORDS)  # it holds no Codex account
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
    try:
        assert R.call_account(on, "claude-cli") == ("c-work", "Work Max")
        # A Claude pin says nothing about a Codex call.
        assert R.call_account(on, "codex-cli") == (U.THIS_PC, U.THIS_PC_WORDS)
    finally:
        P.reset_pin(token)


def test_a_window_that_resets_inside_one_run_says_so():
    """A run whose own readings straddle a reset (the reset instant moved)
    is not one period — and a span holding it is not comparable."""
    readings = [
        {"window": "5h", "window_mins": 300, "percent": 92.0, "resets_at": "2030-01-01T05:00:00Z", "at": 10.0},
        {"window": "5h", "window_mins": 300, "percent": 93.5, "resets_at": "2030-01-01T05:00:00Z", "at": 11.0},
        {"window": "5h", "window_mins": 300, "percent": 1.5, "resets_at": "2030-01-01T10:00:00Z", "at": 12.0},
    ]
    summary = U.window_summary(readings)
    assert summary["one_period"] is False and "reset in between" in summary["why"]
    assert (summary["first"]["percent"], summary["last"]["percent"], summary["readings"]) == (92.0, 1.5, 3)
    span = U.measured_span([summary])
    assert span["state"] == "not_comparable" and "reset in between" in span["why"]
    calm = U.window_summary(readings[:2])
    assert calm["one_period"] is True and U.measured_span([calm])["points"] == 1.5


def test_a_span_is_measured_only_inside_one_window_period():
    def run(first, last, reset="2030-01-01T05:00:00Z", at=0):
        return {"first": {"at": U.iso(1e9 + at), "percent": first, "resets_at": reset},
                "last": {"at": U.iso(1e9 + at + 1), "percent": last, "resets_at": reset},
                "readings": 2, "one_period": True}
    assert U.measured_span([run(10, 12, at=0), run(12.5, 15, at=10)])["points"] == 5.0
    moved = U.measured_span([run(10, 12, at=0), run(1, 3, reset="2030-01-01T10:00:00Z", at=10)])
    assert moved["state"] == "not_comparable" and "reset in between" in moved["why"]
    dropped = U.measured_span([run(10, 12, at=0), run(4, 6, at=10)])
    assert dropped["state"] == "not_comparable" and "went down" in dropped["why"]
    unsaid = U.measured_span([run(10, 12, reset=None)])
    assert unsaid["state"] == "not_comparable" and "did not say" in unsaid["why"]
    assert U.measured_span([])["state"] == "not_known"


def test_claude_alone_is_the_planners_own_setup_with_the_same_effort():
    team_cfg = {"runner": "jarvis-team", "provider": "claude-cli", "model": "m", "effort": "medium"}
    rec = {"task_id": "a", "passed": True}
    high = ({"runner": "jarvis", "provider": "claude-cli", "model": "m", "effort": "high", "created_at": "2"},
            [rec, {**rec, "repeat": 2}])
    medium = ({"runner": "jarvis", "provider": "claude-cli", "model": "m", "effort": "medium", "created_at": "1"},
              [rec])
    other = ({"runner": "jarvis", "provider": "codex-cli", "model": "m", "effort": "medium"}, [rec])
    cli = ({"runner": "claude", "provider": "claude", "model": "m", "effort": "medium"}, [rec])
    got = TRP.find_alone(team_cfg, [rec, {**rec, "repeat": 2}], [high, medium, other, cli])
    assert got == medium  # the same effort wins over more runs in common
    assert TRP.find_alone(team_cfg, [rec], [other, cli]) is None


def test_a_run_whose_account_carried_no_reading_is_said_beside_the_span():
    w = {"window": "5h", "window_mins": 300, "readings": 2, "one_period": True,
         "first": {"at": U.iso(1e9), "t": 1e9, "percent": 10.0, "resets_at": "2030-01-01T05:00:00Z"},
         "last": {"at": U.iso(1e9 + 5), "t": 1e9 + 5, "percent": 12.0, "resets_at": "2030-01-01T05:00:00Z"}}
    acct = {"vendor": "claude", "account": U.THIS_PC, "name": U.THIS_PC_WORDS, "calls": 2}
    records = [{"task_id": "a", "started_at": "1", "accounts": {"k": {**acct, "windows": {}}}},
               {"task_id": "b", "started_at": "2", "accounts": {"k": {**acct, "windows": {"5h": w}}}}]
    words = TRP.measured_words(TRP.measured_usage(records), "claude", {"claude": {}})
    assert words == ("5-hour window +2 points (10.0% to 12.0%, 2 readings) "
                     "(1 run(s) on this account carried no reading)")


def test_the_answer_says_not_enough_runs_when_the_numbers_cannot_answer():
    team_cfg = {"runner": "jarvis-team", "provider": "claude-cli", "model": "m"}
    alone_cfg = {"runner": "jarvis", "provider": "claude-cli", "model": "m"}
    # (v1.399.2: the team hands a piece to a member, or the answer is
    # "No answer" before any other verdict — test_team_report_no_member_work.)
    rec = {"task_id": "a", "passed": True, "cost_list_price_usd": 0.1, "tokens_in": 1, "tokens_out": 1,
           "parts": {PLANNER: {"vendor": "claude", "cost_list_price_usd": 0.1, "tokens_in": 1, "tokens_out": 1,
                               "model_calls": 1}},
           "helpers": [{"member": "glm", "role": "code", "state": "done"}]}
    assert TRP.answer((team_cfg, [rec]), None).startswith("not enough runs to say")
    other = {**rec, "task_id": "b"}
    assert TRP.answer((team_cfg, [rec]), (alone_cfg, [other])).startswith("not enough runs to say")
    unscored = {**rec, "passed": None}
    assert TRP.answer((team_cfg, [rec]), (alone_cfg, [unscored])).startswith("not enough runs to say")
    # An unknown list price is said, never compared.
    unknown = {**rec, "parts": {PLANNER: {**rec["parts"][PLANNER], "cost_list_price_usd": None}}}
    words = TRP.answer((team_cfg, [rec]), (alone_cfg, [unknown]))
    assert words.startswith("Not known: the team passed as many tasks") and "not known for every run" in words
    assert "The team passed 1 of 1, Claude alone 1 of 1" in words
    assert "%" not in words


def test_the_answer_counts_only_runs_both_setups_had_a_fair_go_at():
    """A run one side did not run (an account limit, say) is left out of the
    pass counts AND the list price: its near-zero usage would otherwise read
    as the team using less."""
    team_cfg = {"runner": "jarvis-team", "provider": "claude-cli", "model": "m"}
    alone_cfg = {"runner": "jarvis", "provider": "claude-cli", "model": "m"}

    def rec(task, passed, cost):
        # (v1.399.2: a member took a piece in each run — see the pin above.)
        return {"task_id": task, "passed": passed, "tokens_in": 1, "tokens_out": 1, "cost_list_price_usd": cost,
                "parts": {PLANNER: {"vendor": "claude", "cost_list_price_usd": cost, "tokens_in": 1,
                                    "tokens_out": 1, "model_calls": 1}},
                "helpers": [{"member": "glm", "role": "code", "state": "done"}]}

    team = [rec("a", True, 0.1), rec("b", None, 0.0)]  # b did not run for the team
    alone = [rec("a", True, 0.2), rec("b", True, 0.5)]
    assert TRP.answer((team_cfg, team), (alone_cfg, alone)) == (
        "Yes on these runs: as many tasks, with less Claude list-price. "
        "The team passed 1 of 1, Claude alone 1 of 1 (the 1 run both made and both were scored on). "
        "Claude list-price: $0.1000 for the team vs $0.2000 alone (50% less).")


# --------------------------------------------------------------------------- #
# the team file
# --------------------------------------------------------------------------- #


def test_a_team_file_names_the_planner_and_the_members(tmp_path):
    spec = _spec(tmp_path)
    assert (spec.planner_provider, spec.planner_model) == W.PLANNER
    assert [(m.name, m.provider, m.model, m.roles) for m in spec.members] == [
        ("glm", "fleet-spark", "glm-5.3-flash", ("read", "code")),
        ("codex", "codex-cli", "gpt-6-astra", ("review",)),
    ]


@pytest.mark.parametrize("text,words", [
    ("[planner]\nprovider='claude-cli'\nmodel='default'\n[[members]]\nname='a'\nprovider='p'\nmodel='m'\nroles=['code']\n",
     "names no particular model"),
    ("[planner]\nprovider='claude-cli'\nmodel='m'\n", "at least one [[members]]"),
    ("[planner]\nprovider='claude-cli'\nmodel='m'\n[[members]]\nname='a'\nprovider='p'\nmodel='m'\nroles=['fly']\n",
     "roles Iron Jarvis does not know: fly"),
    ("[planner]\nprovider='claude-cli'\nmodel='m'\n[[members]]\nname='builder'\nprovider='p'\nmodel='m'\nroles=['code']\n",
     "one of Iron Jarvis's own agents"),
    ("[planner]\nprovider='claude-cli'\nmodel='m'\n[[members]]\nname='a'\nprovider='p'\nmodel='m'\nroles=['code']\n"
     "[[members]]\nname='a'\nprovider='p'\nmodel='m'\nroles=['code']\n", "names 'a' twice"),
    ("[planner]\nprovider='claude-cli'\nmodel='m'\nmodle='x'\n[[members]]\nname='a'\nprovider='p'\nmodel='m'\nroles=['code']\n",
     "keys it does not know: modle"),
    ("[planner\n", "is not valid TOML"),
])
def test_a_team_file_that_is_not_a_team_is_refused_in_words(tmp_path, text, words):
    path = tmp_path / "team.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BenchRefused, match=re.escape(words)):
        T.load_team_file(path)


def test_an_account_the_benchmarks_iron_proxy_does_not_hold_is_refused(world, tmp_path):
    path = tmp_path / "team.toml"
    path.write_text(W.team_toml() + 'account = "x-plus"\n', encoding="utf-8")
    runner = T.JarvisTeamRunner(world.platform, team=T.load_team_file(path))
    with pytest.raises(BenchRefused, match="Iron-Proxy is not on in the benchmark's home"):
        runner.check_ready()


# --------------------------------------------------------------------------- #
# one home, two rows: the team never leaks into the single-model row
# --------------------------------------------------------------------------- #


async def test_a_single_model_row_refuses_a_home_with_someone_elses_team(world, tmp_path):
    world.platform.agents_registry.register("mine", "You help me.", [], provider="fleet-spark",
                                             model="glm-5.3-flash", roles=["code"])
    with pytest.raises(BenchRefused, match=r"model-team members \(mine\)"):
        await _alone(world, repeats=1, names=[W.TASK_B])
    runner = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path))
    with pytest.raises(BenchRefused, match=r"does not make \(mine\)"):
        runner.check_ready()


async def test_members_a_stopped_team_run_left_behind_are_removed_before_a_single_model_row(world, tmp_path):
    runner = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path))
    runner.prepare()  # ...and the process was stopped before close()
    assert {r.name for r in world.platform.agents_registry.list()} >= {"glm", "codex"}
    res = await _alone(world, repeats=1, names=[W.TASK_B])
    assert not world.platform.agents_registry.list()
    rec = res.records[0]
    assert set(rec["parts"]) == {PLANNER} and rec["helpers"] == []


# --------------------------------------------------------------------------- #
# the existing runners are unchanged
# --------------------------------------------------------------------------- #

#: The Jarvis runner's identity keys before v1.396.0 (no effort, no layer, a
#: dialect with no bash shell): the team's are these plus its own three.
JARVIS_KEYS = {
    "lane_sha256", "harness_sha256", "dialect", "dialect_requested", "family", "harness_fingerprint",
    "tools_sha256", "tools", "persona", "persona_sha256", "prompt_sha256", "profile_sha256",
    "round_budget", "approval_mode", "session",
}


def test_the_configuration_key_and_fingerprint_formula_is_unchanged():
    """The key a configuration had at v1.395.0 (written down then): a stored
    results folder of any earlier setup keeps its name and is reused."""
    config = Configuration(runner="jarvis", provider="scripted", model="s",
                           harness={"lane_sha256": "a" * 64, "round_budget": 60}, version="9.9.9")
    assert config.key == "386a41e7f8c0442efbeda1ccf5edf88a859fe8127709a897cc426c77b2d00b2e"
    assert config.folder_name == "jarvis-scripted-s-386a41e7f8c0"
    task = next(t for t in TASKS if t.id == W.TASK_A)
    assert config.fingerprint(task) == R._sha(config.key + ":" + task.content_hash)
    assert config.fingerprint(task, 2) == R._sha(config.key + ":" + task.content_hash + ":repeat:2")


def test_the_jarvis_runners_identity_is_unchanged_and_the_team_only_adds_to_it(world, tmp_path):
    plain = JarvisRunner(world.platform, provider="scripted", model="s").identity()
    assert set(plain) == JARVIS_KEYS
    team = T.JarvisTeamRunner(world.platform, team=_spec(tmp_path))
    ident = team.identity()
    assert set(ident) - set(JarvisRunner(world.platform, provider=W.PLANNER[0], model=W.PLANNER[1]).identity()) == {
        "team", "router", "member_prompt_sha256", "member_steps", "member_effort"}
    # v1.396.0 review: each member's own step limit (the planner's round
    # budget) and the level its calls send (no effort asked here).
    assert ident["member_steps"] == {"glm": team.round_budget, "codex": team.round_budget}
    assert ident["member_effort"] == {"glm": None, "codex": None}
    assert ident["team"] == {
        "planner": {"provider": "claude-cli", "model": "claude-opus-4-8"},
        "members": [
            {"name": "codex", "provider": "codex-cli", "model": "gpt-6-astra", "roles": ["review"], "description": ""},
            {"name": "glm", "provider": "fleet-spark", "model": "glm-5.3-flash", "roles": ["code", "read"],
             "description": ""},
        ],
    }
    assert ident["router"]["thresholds"] == {"min_runs": 3, "good_rate": 0.7, "fail_streak": 2,
                                             "record_window": 20, "piece_fails": 2,
                                             "new_work_max_percent": 80.0}
    # Making the members does not change the identity (a dry run and a real
    # run compute the same key).
    before = team.identity()
    team.prepare()
    try:
        assert team.identity() == before
    finally:
        team.close()


def test_the_cli_runners_identities_are_unchanged(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench.runners import ClaudeRunner, CodexRunner

    monkeypatch.setattr(R, "cli_version", lambda binary: "9.9.9 (fake)")
    claude = ClaudeRunner(model="sonnet", binary="claude").identity()
    assert set(claude) == {"cli_version", "argv", "prompt_sha256", "billing"}
    assert claude["argv"] == ["-p", "--safe-mode", "--output-format", "stream-json", "--verbose",
                              "--permission-mode", "bypassPermissions", "--no-session-persistence",
                              "--model", "sonnet"]
    codex = CodexRunner(model="gpt-6-astra", binary="codex")
    monkeypatch.setattr(codex, "billing", lambda: ("list_price", "test"))
    assert set(codex.identity()) == {"cli_version", "argv", "prompt_sha256", "billing"}


async def test_an_older_record_is_reused_and_reads_as_not_known(world, tmp_path):
    first = await _alone(world, repeats=1, names=[W.TASK_B])
    run_dir = first.config_dir / "runs" / W.TASK_B
    record = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    for key in ("parts", "accounts", "helpers", "team_records"):
        record.pop(key, None)  # what a run written before v1.396.0 holds
    (run_dir / "result.json").write_text(json.dumps(record), encoding="utf-8")
    calls = len(world.calls)
    again = await _alone(world, repeats=1, names=[W.TASK_B])
    assert again.records[0]["reused"] is True and len(world.calls) == calls
    report = (again.config_dir / "report.md").read_text(encoding="utf-8")
    assert "## Usage" not in report  # nothing to say about an older run's windows


def test_one_command_makes_the_team_row_the_planner_alone_row_and_the_answer(tmp_path, monkeypatch):
    """The live run is ONE command: ``--with-alone`` runs the planner alone
    after the team, on the same home and settings, and the report answers."""
    from typer.testing import CliRunner

    from iron_jarvis.core.config import resolve_home
    from iron_jarvis.harness_bench import bench as bench_mod
    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    real = bench_mod.make_runner
    wired: list = []

    def make_runner(*a, **kw):
        runner = real(*a, **kw)
        wired.append(W.wire(runner.platform, monkeypatch))
        return runner

    monkeypatch.setattr(bench_mod, "make_runner", make_runner)
    team = tmp_path / "team.toml"
    team.write_text(W.team_toml(), encoding="utf-8")
    root = tmp_path / "bench-root"
    base = ["run", "--runner", "jarvis-team", "--team", str(team), "--root", str(root), "--tier", "frontier",
            "--tasks", W.TASK_B, "--seed", "0", "--with-alone"]
    dry = CliRunner().invoke(bench_app, [*base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "jarvis-team-claude-cli-claude-opus-4-8-" in dry.output
    assert "jarvis-claude-cli-claude-opus-4-8-" in dry.output
    assert dry.output.count(f"  {W.TASK_B} [frontier, trap]: would run") == 2
    assert wired[-1].calls == []
    out = CliRunner().invoke(bench_app, base)
    assert out.exit_code == 0, out.output
    # The team's row first (its members made, then gone), the planner alone after.
    assert out.output.index("jarvis-team-claude-cli") < out.output.index("jarvis-claude-cli-claude-opus-4-8-")
    report = (resolve_home(root) / "harness-bench" / "report.md").read_text(encoding="utf-8")
    # (v1.396.0 review: a team that passed fewer gets no "less" headline.)
    assert "**The answer:** No: the team passed fewer tasks. The team passed 0 of 1, Claude alone 1 of 1" in report
    assert "% less)." in report
    plain = ["run", "--runner", "jarvis", "--provider", "claude-cli", "--model", "m", "--root", str(root)]
    refused = CliRunner().invoke(bench_app, [*plain, "--with-alone", "--dry-run"])
    assert refused.exit_code == 2 and "--with-alone applies to --runner jarvis-team only." in refused.output
