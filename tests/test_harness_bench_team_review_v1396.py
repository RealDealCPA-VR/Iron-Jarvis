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


# --------------------------------------------------------------------------- #
# a member's effort and step limit
# --------------------------------------------------------------------------- #


async def test_a_members_calls_carry_the_runs_effort_where_its_model_takes_one(world, tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session

    res = await _run(world, _runner(world, tmp_path), names=[W.TASK_A])
    rec = res.records[0]
    assert rec["ok_run"] is True and rec["ledger_ok"] is True, rec["error"]
    sent: dict = {}
    for k in world.kwargs:
        sent.setdefault(k["provider"], set()).add(k.get("reasoning", ""))
    # Codex takes a level: the run's --effort reaches every member call.
    assert sent["codex-cli"] == {"medium"}, sent
    # glm-5.3-flash on the custom endpoint takes none: nothing is sent.
    assert sent["custom"] == {""}, sent
    parts = rec["parts"]
    assert parts["codex"]["effort_passed"] == "medium" and parts["glm"]["effort_passed"] == "not offered"
    assert parts["codex"]["max_steps"] == 60 and parts["glm"]["max_steps"] == 60
    # The step limit reached the members' own runs (never the home's 12).
    sessions = [h["session"] for h in rec["helpers"] if h.get("session")]
    assert sessions
    with session_scope(world.platform.engine) as db:
        steps = {db.get(Session, sid).max_steps for sid in sessions}
    assert steps == {60}, steps


async def test_a_team_file_names_a_members_effort_and_steps(world, tmp_path):
    runner = _runner(world, tmp_path, extra_codex='effort = "high"\nmax_steps = 25\n')
    codex = next(m for m in runner.team.members if m.name == "codex")
    assert (runner.member_effort(codex), runner.member_steps(codex)) == ("high", 25)
    assert runner.identity()["member_steps"] == {"glm": 60, "codex": 25}
    assert runner.identity()["member_effort"] == {"glm": "not offered", "codex": "high"}
    for bad, words in (('effort = "max"\n', "effort is one of"), ("max_steps = 0\n", "max_steps is a whole"),
                       ("max_steps = 500\n", "max_steps is a whole"), ('max_steps = "9"\n', "max_steps is a whole")):
        with pytest.raises(BenchRefused, match=words):
            T.load_team_file(N.write_team(tmp_path, extra_codex=bad))


# --------------------------------------------------------------------------- #
# a member that could not do its piece for an infrastructure reason
# --------------------------------------------------------------------------- #


async def test_a_member_at_its_accounts_limit_makes_the_run_did_not_run(world, tmp_path):
    from iron_jarvis.providers.adapters.base import ProviderError

    world.raise_for["codex-cli"] = ProviderError(
        "codex-cli: this ChatGPT account hit its Codex usage limit — try again after it resets. "
        "Codex said: You've hit your usage limit.", status_code=429, transient=False)
    res = await _run(world, _runner(world, tmp_path), names=[W.TASK_A])
    rec = res.records[0]
    # The planner carried on and glm's fix passes the hidden check — but the
    # team never had its reviewer: not a team result.
    assert rec["check_verdict"] is True
    assert rec["passed"] is None and rec["unscored_by"] == "infrastructure", rec
    assert "codex" in rec["failure_reason"] and "usage limit" in rec["failure_reason"]
    # No completion marker: the run is tried again next time.
    again = await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)
    assert any("would run" in line for line in again.plan_lines)


def test_a_member_that_fails_at_its_work_is_graded():
    """Only an infrastructure reason unscores a run: a member that ran and
    did not finish its piece is the team's own result."""
    from iron_jarvis.harness_bench.failures import helper_infra

    rows = [{"member": "codex", "state": "failed", "result": "Stopped at its step limit (60 steps)."},
            {"member": "glm", "state": "done", "result": "the network module is fine"}]
    assert helper_infra(rows) == ""
    rows.append({"member": "glm", "state": "failed",
                 "result": "delegated agent 'glm' crashed: ProviderError: connection refused"})
    assert helper_infra(rows) == "glm could not do its piece (the model server could not be reached)"
    # The run's own plain summary (a member run that ENDED failed rather
    # than raised) is read too.
    busy = [{"member": "codex", "state": "failed",
             "result": "The run failed: Codex is busy. Run it again shortly, or choose another model in Settings."}]
    assert "usage or rate limit" in helper_infra(busy)
    out = [{"member": "codex", "state": "failed",
            "result": "The run failed: Codex isn't signed in. Press Sign in beside Codex in Settings."}]
    assert "not signed in" in helper_infra(out)


# --------------------------------------------------------------------------- #
# the report: Iron-Proxy off, the router's record beside the hidden check
# --------------------------------------------------------------------------- #


async def test_the_report_says_the_router_had_no_usage_numbers_and_shows_pieces_beside_the_hidden_check(
        world, tmp_path):
    res = await _run(world, _runner(world, tmp_path), names=[W.TASK_A, W.TASK_B])
    for rec in res.records:
        assert rec["team_records"]["usage_numbers"] is False
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert ("In every run the router had no usage numbers (Iron-Proxy was off in the benchmark's home), so its "
            "rule that keeps new work off an account at 80% of a window never applied") in report
    # Per member and role: pieces, their state, and the hidden check's
    # verdict on the same runs, by task (glm wrote A's fix, which passes; B's
    # piece changed nothing, which fails).
    assert (f"| glm | code | 2 | 2 | 0 | 2 | 1 / 2 | {W.TASK_A}, {W.TASK_B} |") in report, report
    assert (f"| codex | review | 2 | 2 | 0 | 2 | 1 / 2 | {W.TASK_A}, {W.TASK_B} |") in report
    combined = (world.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    assert "never applied" in combined and "| glm | code | 2 |" in combined


# --------------------------------------------------------------------------- #
# one task crashing never stops the night
# --------------------------------------------------------------------------- #


async def test_a_task_the_bench_itself_crashes_on_never_stops_the_rest(world, tmp_path, monkeypatch):
    """A crash in the bench's own step for one run (here: grading raises, as
    a file a leftover program holds would) used to end the whole invocation
    — at 3 AM, the rest of the night. Now that run is "did not run" (no
    marker: tried again next time), the next task runs, the members are
    still removed at the end and the ledger still adds up."""
    from iron_jarvis.harness_bench import bench as bench_mod

    real = bench_mod.grade

    def grade(task, work, out):
        if task.id == W.TASK_A:
            raise PermissionError(13, "The process cannot access the file", str(work / "held.txt"))
        return real(task, work, out)

    monkeypatch.setattr(bench_mod, "grade", grade)
    res = await _run(world, _runner(world, tmp_path), names=[W.TASK_A, W.TASK_B])
    by = {r["task_id"]: r for r in res.records}
    assert set(by) == {W.TASK_A, W.TASK_B}
    crashed = by[W.TASK_A]
    assert crashed["passed"] is None and crashed["unscored_by"] == "infrastructure"
    assert crashed["failure_reason"].startswith("the benchmark itself failed (PermissionError"), crashed
    assert crashed["ledger_ok"] is True
    assert by[W.TASK_B]["passed"] is False and by[W.TASK_B]["ledger_ok"] is True  # it ran and was graded
    assert not [r for r in world.platform.agents_registry.list() if r.name in ("glm", "codex")]
    # The crashed run is tried again next time; the graded one is reused.
    monkeypatch.setattr(bench_mod, "grade", real)
    again = await _run(world, _runner(world, tmp_path), names=[W.TASK_A, W.TASK_B], dry_run=True)
    text = "\n".join(again.plan_lines)
    assert f"{W.TASK_A} [frontier, bugfix]: would run" in text
    assert f"{W.TASK_B} [frontier, trap]: would reuse the stored result" in text


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows's limit on a program's folder")
async def test_a_root_too_deep_for_a_check_to_start_in_is_refused_before_any_task(world, tmp_path):
    """Found by this review running the bench's own tests under a deep TEMP:
    a run's grading folder past Windows's limit for a program's working folder
    made every hidden check "could not start" ([WinError 267]) — a whole night
    unscored. A root that deep is refused before the first task, in words;
    nothing is made (the folder need not even exist)."""
    from iron_jarvis.harness_bench.bench import MAX_RUN_DIR_CHARS

    deep = tmp_path / ("d" * max(1, 230 - len(str(tmp_path))))
    with pytest.raises(BenchRefused, match="past the 248 Windows allows"):
        await run_bench(runner=_runner(world, tmp_path), home=deep, tasks=TASKS, names=[W.TASK_A], seed=0,
                        repeats=3, dry_run=True)
    assert not deep.exists() and world.calls == []
    assert MAX_RUN_DIR_CHARS == 248
    # The world's own home (a normal depth) is fine.
    ok = await _run(world, _runner(world, tmp_path), names=[W.TASK_A], dry_run=True)
    assert ok.plan_lines


# --------------------------------------------------------------------------- #
# the refine ledger still refuses a real mismatch
# --------------------------------------------------------------------------- #


def test_the_refine_ledger_still_catches_a_real_mismatch():
    """v1.396.0 let a record of several receipts sit one rounding per receipt
    away from its calls. The refine loop's ledger reads that count too: a
    token off by one, or money past the receipts' own rounding, is still a
    problem — and a member that did nothing widens nothing."""
    from iron_jarvis.harness_bench.ledger import CallEntry, Rollout, receipts_of
    from iron_jarvis.harness_bench.refine import RefineLedger

    def ledger(record: dict) -> list[str]:
        led = RefineLedger({"proposals": 1, "probe_runs": 1})
        stage = led.stage("baseline", "x")
        roll = Rollout(task_id="a", runner="jarvis")
        roll.add(CallEntry(provider="claude-cli", model="m", tokens_in=100, tokens_out=10, tokens_cached=0,
                           cost_metered_usd=0.0, cost_list_price_usd=0.1, complete=True, who=PLANNER))
        stage.rollouts.append(roll)
        stage.records.append(record)
        return led.problems()

    three = {PLANNER: {"receipts": 1}, "glm": {"receipts": 1}, "codex": {"receipts": 1}}
    idle = {PLANNER: {"receipts": 1}, "glm": {"receipts": 0}, "codex": {"receipts": 0}}
    assert receipts_of({"parts": three}) == 3 and receipts_of({"parts": idle}) == 1
    good = {"tokens_in": 100, "tokens_out": 10, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.1}
    assert ledger({**good, "parts": three}) == []
    # Within three receipts' rounding: fine; past it: a problem.
    assert ledger({**good, "cost_list_price_usd": 0.1 + 1.2e-6, "parts": three}) == []
    assert ledger({**good, "cost_list_price_usd": 0.1 + 1.2e-6, "parts": idle})
    assert ledger({**good, "cost_list_price_usd": 0.1 + 2e-6, "parts": three})
    # A token off by one is a problem however many receipts.
    assert any("tokens_in" in p for p in ledger({**good, "tokens_in": 101, "parts": three}))


# --------------------------------------------------------------------------- #
# Codex readings in the order Codex said them
# --------------------------------------------------------------------------- #


def test_an_older_codex_snapshot_on_a_later_answer_is_not_a_window_going_down():
    """Codex's snapshot is the one its SESSION last read: a parked session's
    older copy can ride an answer that arrives after a newer session's
    reading. Ordered by the answer's arrival it read as the window going down
    ("not comparable"); stamped with when it was seen it is the earlier
    reading it is."""
    reset = 1_900_010_000

    def snap(pct: int, seen: int) -> dict:
        return {"rate_limits": {"planType": "plus", "observedAt": seen,
                                "primary": {"usedPercent": pct, "windowDurationMins": 300, "resetsAt": reset}}}

    fresh = U.readings_from(snap(42, 1_900_000_100), at=1_900_000_101.0)
    stale = U.readings_from(snap(40, 1_900_000_050), at=1_900_000_200.0)  # arrived later, seen earlier
    assert stale[0]["at"] == 1_900_000_050.0 and fresh[0]["at"] == 1_900_000_100.0
    summary = U.window_summary(fresh + stale)
    assert summary["one_period"] is True, summary["why"]
    assert (summary["first"]["percent"], summary["last"]["percent"]) == (40.0, 42.0)
    # A stamp that cannot be believed (from the future, or a month old) is
    # not used: the arrival time stands.
    future = U.readings_from(snap(40, 1_900_009_000), at=1_900_000_200.0)
    assert future[0]["at"] == 1_900_000_200.0
    ancient = U.readings_from(snap(40, 1_800_000_000), at=1_900_000_200.0)
    assert ancient[0]["at"] == 1_900_000_200.0


# --------------------------------------------------------------------------- #
# the answer
# --------------------------------------------------------------------------- #

TEAM_CFG = {"runner": "jarvis-team", "provider": "claude-cli", "model": "sonnet"}
ALONE_CFG = {"runner": "jarvis", "provider": "claude-cli", "model": "sonnet"}


def _rec(task: str, passed, claude: float, *, codex: float = 0.0, local: int = 0) -> dict:
    parts = {PLANNER: {"vendor": "claude", "cost_list_price_usd": claude, "cost_metered_usd": 0.0,
                       "tokens_in": 100, "tokens_out": 10, "model_calls": 2}}
    if codex:
        parts["codex"] = {"vendor": "codex", "cost_list_price_usd": codex, "cost_metered_usd": 0.0,
                          "tokens_in": 50, "tokens_out": 5, "model_calls": 1}
    if local:
        parts["glm"] = {"vendor": "local", "cost_list_price_usd": 0.0, "cost_metered_usd": 0.0,
                        "tokens_in": local, "tokens_out": local // 10, "model_calls": 1}
    return {"task_id": task, "passed": passed, "parts": parts}


def test_a_team_that_passes_fewer_never_gets_a_less_usage_headline():
    team = [_rec("a", True, 0.10, codex=0.02, local=700), _rec("b", False, 0.10, codex=0.02, local=700)]
    alone = [_rec("a", True, 0.30), _rec("b", True, 0.30)]
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, alone))
    # The pass comparison leads, and says no.
    assert words.startswith("No: the team passed fewer tasks. The team passed 1 of 2, Claude alone 2 of 2"), words
    assert "Yes" not in words
    # The Claude list price is said (not as a win), and what the team spent
    # beyond Claude is impossible to miss.
    assert "Claude list-price: $0.2000 for the team vs $0.6000 alone (67% less)." in words
    assert "Beyond Claude, the team also used $0.0400 of Codex list-price; 1,400 tokens in / 140 out on local models." \
        in words


def test_a_team_that_passes_as_many_with_less_claude_says_yes_and_what_else_it_used():
    team = [_rec("a", True, 0.10, codex=0.02), _rec("b", True, 0.10, codex=0.02)]
    alone = [_rec("a", True, 0.30), _rec("b", True, 0.30)]
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, alone))
    assert words.startswith("Yes on these runs: as many tasks, with less Claude list-price."), words
    assert "Beyond Claude, the team also used $0.0400 of Codex list-price." in words
    # As many tasks but MORE Claude: no.
    dearer = [_rec("a", True, 0.40), _rec("b", True, 0.40)]
    assert TRP.answer((TEAM_CFG, dearer), (ALONE_CFG, alone)).startswith(
        "No: as many tasks, but not less Claude list-price.")
    # A Codex list price the team's runs could not read is said as unknown.
    blind = [_rec("a", True, 0.10, codex=0.02)]
    blind[0]["parts"]["codex"]["cost_list_price_usd"] = None
    assert "an unknown amount of Codex list-price" in TRP.answer((TEAM_CFG, blind), (ALONE_CFG, alone[:1]))


@pytest.mark.parametrize("error", [
    # The real words each adapter raises at an account's limit
    # (claude_native.transport.upstream_failure, codex_native.protocol.turn_failure).
    "claude-cli: Anthropic is rate-limiting this account right now — try again shortly. Anthropic said: x",
    "codex-cli: this ChatGPT account hit its Codex usage limit — try again after it resets. Codex said: x",
    "Claude Code: You've hit your session limit · resets 3:45am",
])
def test_the_planners_own_limit_at_3am_is_did_not_run(error):
    from iron_jarvis.harness_bench.failures import INFRASTRUCTURE, classify_failure

    got = classify_failure("jarvis-team", False, {"acted": True}, error)
    assert got.kind == INFRASTRUCTURE and "limit" in got.reason
