"""v1.396.0 review of Phase 7 (MEASURED): what would make tonight's team run
fail, stall, lie or waste the windows.

Tonight's team (``tests/_fakes/bench_tonight_v1396``): planner claude-cli /
sonnet; ``glm`` on the CUSTOM endpoint (a real loopback gateway that wants a
key); ``codex`` on codex-cli. Scripted models only — no CLI, no network
beyond that loopback gateway.

* THE CHECKS BEFORE A NIGHT: a typo in a model, a provider nobody has, a
  gateway that is down or wants a key the home lacks, a CLI the last check
  said is signed out — each refused in one plain sentence BEFORE the first
  task (a dry run and a real run alike), never 72 "did not run" at 11 PM;
  what cannot be known without starting a CLI is SAID, never guessed;
* A MEMBER'S effort and step limit: the run's ``--effort`` reaches a member
  whose model takes a level; a member gets the planner's round budget, not
  the home's 12 steps; both recorded per part and part of the setup;
* A MEMBER that could not do its piece for an infrastructure reason (its
  account's limit, a sign-in, an unreachable server) makes the run "did not
  run", never a team failure;
* THE ANSWER leads with the pass counts and never hides what the team spent
  beyond the planner's subscription (Codex list price, local tokens);
* the report says plainly that Iron-Proxy was off (no usage numbers: the
  80% rule never applied), and gives the router's record per member and role
  beside the hidden check's verdict on the same runs, by task;
* Codex readings are ordered by when Codex said them, not when a stale copy
  rode a later answer;
* the refine ledger still refuses a real mismatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import team as T
from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench import usage as U
from iron_jarvis.harness_bench.bench import BenchRefused, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.ledger import PLANNER
from iron_jarvis.platform import build_platform
from tests._fakes import bench_team_v1396 as W
from tests._fakes import bench_tonight_v1396 as N

TASKS = load_tasks()


@pytest.fixture
def gateway():
    g = N.Gateway()
    try:
        yield g
    finally:
        g.close()


@pytest.fixture
def world(tmp_path, monkeypatch, gateway):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    w = N.wire(platform, monkeypatch, gateway=gateway)
    w.platform = platform
    w.home = platform.config.home
    w.gateway = gateway
    return w


def _runner(world, tmp_path, **kw) -> T.JarvisTeamRunner:
    effort = kw.pop("effort", "medium")
    rounds = kw.pop("rounds", 60)
    return T.JarvisTeamRunner(world.platform, team=T.load_team_file(N.write_team(tmp_path, **kw)),
                              round_budget=rounds, effort=effort)


async def _run(world, runner, *, names, repeats=1, dry_run=False):
    return await run_bench(runner=runner, home=world.home, tasks=TASKS, names=names, seed=0,
                           repeats=repeats, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# the checks before a night
# --------------------------------------------------------------------------- #


async def test_a_typo_in_a_members_model_is_refused_before_the_first_task(world, tmp_path):
    runner = _runner(world, tmp_path, local_model="glm-5.3-flahs")
    for dry in (True, False):
        with pytest.raises(BenchRefused) as got:
            await _run(world, runner, names=[W.TASK_A], dry_run=dry)
        words = str(got.value)
        assert "glm" in words and "glm-5.3-flahs" in words and "glm-5.3-flash" in words, words
    # Nothing was run: no model call, no member made.
    assert world.calls == [] and not world.platform.agents_registry.list()


async def test_a_gateway_that_wants_a_key_the_home_lacks_is_refused(world, tmp_path):
    world.platform.secrets.delete("custom_api_key")
    with pytest.raises(BenchRefused, match="asks for a key"):
        await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)
    # The gateway was asked once, with no key (never the user's real one).
    assert world.gateway.seen and not any(had for _p, had in world.gateway.seen)


async def test_a_gateway_that_is_down_is_refused_in_words(world, tmp_path):
    world.gateway.close()
    with pytest.raises(BenchRefused, match="did not answer"):
        await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)


async def test_no_custom_address_in_the_home_is_refused(world, tmp_path):
    N.point_custom(world.platform, "")
    with pytest.raises(BenchRefused, match="no address"):
        await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)


async def test_an_unknown_provider_is_refused(world, tmp_path):
    with pytest.raises(BenchRefused, match="codex-cl.*not a provider"):
        await _run(world, _runner(world, tmp_path, codex_provider="codex-cl"), names=[W.TASK_A], dry_run=True)


async def test_a_cli_the_last_check_said_is_signed_out_is_refused(world, tmp_path, monkeypatch):
    mgr = world.platform.providers
    patched = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: False if name == "codex-cli" else patched(name))
    monkeypatch.setattr(mgr, "_cli_binary_present", lambda binary: True)
    with pytest.raises(BenchRefused, match="Codex.*signed out"):
        await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)


async def test_a_planner_model_claude_does_not_name_is_refused(world, tmp_path):
    with pytest.raises(BenchRefused, match="sonet"):
        await _run(world, _runner(world, tmp_path, planner_model="sonet"), names=[W.TASK_A], dry_run=True)


async def test_a_dry_run_says_what_it_checked_and_what_it_cannot_know(world, tmp_path):
    from iron_jarvis.providers import cli_auth

    res = await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)
    text = "\n".join(res.plan_lines)
    assert world.calls == []
    # The gateway answered with the key, and lists the member's model.
    assert world.gateway.seen and all(had for _p, had in world.gateway.seen)
    assert f"glm: the custom endpoint (127.0.0.1:{world.gateway.port}) answered and lists glm-5.3-flash" in text
    # Iron Jarvis never starts a CLI to ask: the sign-in is said as not known.
    assert cli_auth.DEFAULT_PROBE.status("claude") is None
    assert "whether Claude Code is signed in is not known yet" in text
    assert "whether Codex is signed in is not known yet" in text
    # Iron-Proxy off: said plainly, with what it means for the router.
    assert "Iron-Proxy is off" in text and "80%" in text
    # Each member's effort and step limit.
    assert "glm: effort not offered" in text and "at most 60 steps a piece" in text
    assert "codex: effort medium" in text


async def test_a_role_nobody_holds_is_said(world, tmp_path):
    runner = _runner(world, tmp_path)
    runner.team = T.TeamSpec(runner.team.planner_provider, runner.team.planner_model,
                             tuple(m for m in runner.team.members if m.name == "codex"))
    res = await _run(world, runner, names=[W.TASK_A], dry_run=True)
    assert "No member holds the role(s) read, test" in "\n".join(res.plan_lines)
