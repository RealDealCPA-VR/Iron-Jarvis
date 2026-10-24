"""v1.383.0 — the benchmark can RANK (IJ harness part 3, Phase 1): repeats and
the exact model.

Pinned here, each through the real code:

* REPEATS. ``--repeats N`` runs every task N times, every task's repeat 1
  before any repeat 2, each repeat its own run (fingerprint, folder, ledger
  key) so resume and reuse work per repeat. N = 1 IS the one-run case: the
  same folder, fingerprint formula, record and marker as a run with no
  ``--repeats`` at all, and a folder a one-run invocation wrote is reused by
  repeat 1 of any later one. The report gives the mean pass rate, its range,
  and which tasks passed every time / some of the time / never; an unscored
  run leaves its OWN repeat's denominator. The ledger matches every record.
* THE EXACT MODEL. A run that names no model is refused before any task
  starts; the model and effort are passed explicitly to every runner (Claude
  Code ``--model``/``--effort``, Codex ``-m``/``-c model_reasoning_effort``,
  Jarvis the reasoning level only where the pair offers it); every record
  keeps what was passed and what the run reported; the report flags a run
  whose reported model differs; "default" is never a model in a report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import bench as bench_mod
from iron_jarvis.harness_bench.bench import (
    MARKER_NAME,
    MODEL_REQUIRED,
    PROVIDER_REQUIRED,
    RESULT_NAME,
    STARTED_NAME,
    BenchRefused,
    Configuration,
    run_bench,
)
from iron_jarvis.harness_bench.ledger import BudgetLedger, BudgetMismatch, CallEntry, run_key
from iron_jarvis.harness_bench.report import combined_report, config_report, model_matches, summarize
from iron_jarvis.harness_bench.runners import ClaudeRunner, CodexRunner, JarvisRunner, RunOutcome
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_harness_bench_v1363 import FIXTURES, TASKS, _fake_env, _shim, _StubRunner, apply_solution

NAMES = ["bugfix-leap-year", "explain-mystery-functions"]
TIMING = ("started_at", "finished_at", "wall_s")


class _RepeatStub(_StubRunner):
    """The stub runner, also noting WHICH run folder each call worked in."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.folders: list[str] = []

    async def run(self, task, workspace, rollout, *, timeout_s):
        self.folders.append(Path(workspace).parent.name)
        return await super().run(task, workspace, rollout, timeout_s=timeout_s)


def _order(seed: int = 0) -> list[str]:
    from iron_jarvis.harness_bench.catalog import select_tasks

    return [t.id for t in select_tasks(list(TASKS.values()), names=NAMES, seed=seed)]


def _plain(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in TIMING}


# --------------------------------------------------------------------------- #
# Repeats
# --------------------------------------------------------------------------- #


async def test_repeats_one_is_exactly_a_run_without_repeats(tmp_path):
    """The one-run case is unchanged: same folders, same fingerprint formula,
    the same records, markers and report as a run that never heard of
    ``--repeats`` — and no repeat material anywhere."""
    a = await run_bench(runner=_RepeatStub(), home=tmp_path / "a", tasks=list(TASKS.values()), names=NAMES)
    b = await run_bench(runner=_RepeatStub(), home=tmp_path / "b", tasks=list(TASKS.values()), names=NAMES,
                        repeats=1)
    assert a.planned == b.planned == _order()
    assert [_plain(r) for r in a.records] == [_plain(r) for r in b.records]
    for res in (a, b):
        assert sorted(p.name for p in (res.config_dir / "runs").iterdir()) == sorted(NAMES)
        for task_id in NAMES:
            run_dir = res.config_dir / "runs" / task_id
            marker = json.loads((run_dir / MARKER_NAME).read_text(encoding="utf-8"))
            started = json.loads((run_dir / STARTED_NAME).read_text(encoding="utf-8"))
            record = json.loads((run_dir / RESULT_NAME).read_text(encoding="utf-8"))
            assert "repeat" not in marker and "repeat" not in started and "repeat" not in record
            # The one-run fingerprint formula, byte for byte.
            want = hashlib.sha256((res.config.key + ":" + TASKS[task_id].content_hash).encode()).hexdigest()
            assert marker["fingerprint"] == record["fingerprint"] == want
            assert res.config.fingerprint(TASKS[task_id], 1) == want != res.config.fingerprint(TASKS[task_id], 2)
    for res in (a, b):
        report = (res.config_dir / "report.md").read_text(encoding="utf-8")
        assert "**Pass rate 100%**" in report
        assert "repeat" not in report.lower() and "Range" not in report
        combined = (res.config_dir.parent / "report.md").read_text(encoding="utf-8")
        assert "Repeats" not in combined and "Range" not in combined and "across repeats" not in combined


async def test_a_one_run_folder_is_reused_by_repeat_one_and_repeats_run_in_whole_rounds(tmp_path):
    stub = _RepeatStub()
    first = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, seed=3)
    order = _order(3)
    assert stub.folders == order
    stub.folders.clear()
    again = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, seed=3,
                            repeats=1)
    assert stub.folders == [] and all(r["reused"] for r in again.records)
    stub.folders.clear()
    three = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, seed=3,
                            repeats=3)
    assert three.config_dir == first.config_dir
    # Repeat 1 is the stored one-run result; then EVERY task's repeat 2
    # before any repeat 3, each round in the seed's order.
    assert stub.folders == [f"{t}.r2" for t in order] + [f"{t}.r3" for t in order]
    assert [(r["task_id"], r.get("repeat", 1), r["reused"]) for r in three.records] == (
        [(t, 1, True) for t in order] + [(t, 2, False) for t in order] + [(t, 3, False) for t in order]
    )
    assert three.planned == [run_key(t, k) for k in (1, 2, 3) for t in order]
    # The report counts what THIS invocation reused: repeat 1 only.
    assert "2 reused from an earlier run" in (three.config_dir / "report.md").read_text(encoding="utf-8")
    for t in order:
        marker = json.loads((three.config_dir / "runs" / f"{t}.r2" / MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["repeat"] == 2 and marker["fingerprint"] == three.config.fingerprint(TASKS[t], 2)
    # Resume works PER REPEAT: one repeat's marker gone, only that run again.
    (three.config_dir / "runs" / f"{order[1]}.r2" / MARKER_NAME).unlink()
    stub.folders.clear()
    resumed = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, seed=3,
                              repeats=3)
    assert stub.folders == [f"{order[1]}.r2"]
    assert sum(1 for r in resumed.records if not r["reused"]) == 1


async def test_a_run_cut_short_keeps_whole_repeats_and_names_the_interrupted_one(tmp_path):
    class _Cut(_RepeatStub):
        async def run(self, task, workspace, rollout, *, timeout_s):
            if len(self.folders) == 2:  # the third run: repeat 2's first task
                self.folders.append(Path(workspace).parent.name)
                raise asyncio.CancelledError()
            return await super().run(task, workspace, rollout, timeout_s=timeout_s)

    stub = _Cut()
    with pytest.raises(asyncio.CancelledError):
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, repeats=3)
    order = _order()
    assert stub.folders == [*order, f"{order[0]}.r2"]
    cfg_dir = next(p for p in (tmp_path / "harness-bench").iterdir() if p.is_dir())
    bench_mod.write_config_outputs(cfg_dir)
    records = json.loads((cfg_dir / "results.json").read_text(encoding="utf-8"))["records"]
    got = sorted((r["task_id"], r.get("repeat", 1), bool(r.get("interrupted"))) for r in records)
    assert got == sorted([(order[0], 1, False), (order[1], 1, False), (order[0], 2, True)])
    report = (cfg_dir / "report.md").read_text(encoding="utf-8")
    assert f"| {order[0]} | 2 | " in report and "interrupted" in report


async def test_the_ledger_matches_every_record_across_repeats(tmp_path):
    class _Varied(_RepeatStub):
        async def run(self, task, workspace, rollout, *, timeout_s):
            self.tokens = (100 + 7 * len(self.folders), 20 + len(self.folders))
            return await super().run(task, workspace, rollout, timeout_s=timeout_s)

    res = await run_bench(runner=_Varied(), home=tmp_path, tasks=list(TASKS.values()), names=NAMES, repeats=3)
    assert all(r["ledger_ok"] is True for r in res.records)
    history = json.loads((res.config_dir / "ledger.json").read_text(encoding="utf-8"))["invocations"][-1]
    keys = [r["task_id"] for r in history["rollouts"]]
    assert keys == res.planned and len(keys) == 6
    by_key = {r["task_id"]: r["totals"] for r in history["rollouts"]}
    for rec in res.records:
        assert by_key[run_key(rec["task_id"], rec.get("repeat", 1))]["tokens_in"] == rec["tokens_in"]

    # One repeat's record that does not add up is flagged — that run only.
    class _LiesOnce(_RepeatStub):
        async def run(self, task, workspace, rollout, *, timeout_s):
            self.lie = 5 if Path(workspace).parent.name.endswith(".r2") else 0
            return await super().run(task, workspace, rollout, timeout_s=timeout_s)

    res = await run_bench(runner=_LiesOnce(harness="h2"), home=tmp_path, tasks=list(TASKS.values()), names=NAMES,
                          repeats=2)
    flagged = sorted(run_key(r["task_id"], r.get("repeat", 1)) for r in res.records if r["ledger_ok"] is False)
    assert flagged == sorted(f"{t}.r2" for t in NAMES)
    for t in NAMES:
        assert not (res.config_dir / "runs" / f"{t}.r2" / MARKER_NAME).exists()
        assert (res.config_dir / "runs" / t / MARKER_NAME).exists()


def test_the_plan_is_checked_run_by_run():
    ledger = BudgetLedger()
    rec = {"tokens_in": 1, "tokens_out": 1, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0}
    for key in ("a", "a.r2"):
        ledger.begin(key, "s").add(CallEntry(tokens_in=1, tokens_out=1))
    ledger.verify_plan(["a", "a.r2"], [], [{"task_id": "a", **rec}, {"task_id": "a", "repeat": 2, **rec}])
    with pytest.raises(BudgetMismatch, match="a.r3 ran 0 times"):
        ledger.verify_plan(["a", "a.r2", "a.r3"], [], [{"task_id": "a", **rec}, {"task_id": "a", "repeat": 2, **rec}])
    with pytest.raises(BudgetMismatch, match="tokens_in"):
        ledger.verify_plan(["a", "a.r2"], [], [{"task_id": "a", **rec}, {"task_id": "a", "repeat": 2, **rec,
                                                                          "tokens_in": 9}])


@pytest.mark.parametrize("bad", [0, 11, -1, 2.5, True, "3"])
async def test_repeats_out_of_range_are_refused_in_plain_words(tmp_path, bad):
    stub = _RepeatStub()
    with pytest.raises(BenchRefused, match=r"^--repeats must be a whole number from 1 to 10\.$"):
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, repeats=bad)
    assert stub.folders == [] and not (tmp_path / "harness-bench").exists()


async def test_a_dry_run_lists_every_repeat(tmp_path):
    stub = _RepeatStub()
    await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES)
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, repeats=2,
                          dry_run=True)
    order = _order()
    assert res.plan_lines[0].endswith(", 2 repeats each:")
    lines = res.plan_lines[1:]
    assert [line.split(":")[1].strip() for line in lines] == ["would reuse the stored result"] * 2 + ["would run"] * 2
    assert [line.split(" [")[0].strip() for line in lines] == order + order
    assert all(" repeat 2:" in line for line in lines[2:])


def _rec(task, repeat, passed, **kw):
    out = {"task_id": task, "category": "bugfix", "tier": "core", "passed": passed, "tool_calls": 2,
           "wall_s": 10.0, "tokens_in": 100, "tokens_out": 10, "cost_metered_usd": 0.0,
           "cost_list_price_usd": 0.01, "model_passed": "sonnet", "effort_passed": "low",
           "model_reported": "claude-sonnet-4-6", **kw}
    if repeat > 1:
        out["repeat"] = repeat
    return out


_THREE = [
    _rec("a", 1, True), _rec("b", 1, True), _rec("c", 1, False), _rec("d", 1, True),
    _rec("a", 2, True), _rec("b", 2, True), _rec("c", 2, False),
    _rec("d", 2, None, unscored_by="infrastructure", grade_reason="did not run: the account's usage limit"),
    _rec("a", 3, True), _rec("b", 3, False), _rec("c", 3, False), _rec("d", 3, False),
]


def test_the_report_gives_the_mean_the_range_and_each_tasks_count():
    s = summarize(_THREE)
    assert s["repeats"] == 3
    # Each repeat's own denominator: repeat 2's unscored run leaves repeat 2's.
    assert [round(v["pass_rate"], 4) for v in s["per_repeat"].values()] == [0.75, round(2 / 3, 4), 0.25]
    assert s["per_repeat"][2]["unscored_infrastructure"] == 1 and s["per_repeat"][2]["scored"] == 3
    assert s["mean_pass_rate"] == pytest.approx((0.75 + 2 / 3 + 0.25) / 3)
    assert s["range"] == (0.25, 0.75)
    assert s["task_groups"] == {"every_time": ["a"], "some_of_the_time": ["b", "d"], "never": ["c"],
                                "not_scored": []}
    config = {"runner": "claude", "provider": "claude", "model": "sonnet", "effort": "low", "key": "k" * 64}
    report = config_report(config, _THREE)
    assert "**Mean pass rate 56%, range 25%–75% over 3 repeats**" in report
    assert "## Across 3 repeats" in report
    assert "| 1 | 75% | 3 / 4 | 0 | 0 | 0 |" in report
    assert "| 2 | 67% | 2 / 3 | 1 | 0 | 0 |" in report
    assert "| 3 | 25% | 1 / 4 | 0 | 0 | 0 |" in report
    assert "- Passed every time (1): a (3/3)" in report
    assert "- Passed some of the time (2): b (2/3), d (1/2 (+1 unscored))" in report
    assert "- Never passed (1): c (0/3)" in report
    assert "| Task | Repeat | Category |" in report and "| a | 3 | bugfix | passed |" in report
    one = [_rec("x", 1, True), _rec("y", 1, False)]
    combined = combined_report([(config, _THREE), ({**config, "model": "opus", "key": "j" * 64}, one)])
    assert "| Repeats | Passed / scored | Pass rate | Range |" in combined
    assert "| 4 | 3 | 6 / 11 | 56% | 25%–75% |" in combined
    assert "| 2 | 1 | 1 / 2 | 50% | one run |" in combined
    assert "## Each task across repeats" in combined and "- Passed every time (1): a (3/3)" in combined


def test_the_command_line_runs_repeats_and_refuses_a_bad_count(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    record = _fake_env(monkeypatch, tmp_path, "solve")
    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    base = ["run", "--runner", "claude", "--binary", claude, "--root", str(root), "--model", "sonnet",
            "--tasks", "bugfix-pagination"]
    for bad in ("0", "11"):
        out = CliRunner().invoke(bench_app, [*base, "--repeats", bad])
        assert out.exit_code == 2 and "--repeats must be a whole number from 1 to 10." in out.output
    assert not record.exists()
    out = CliRunner().invoke(bench_app, [*base, "--repeats", "2", "--effort", "low"])
    assert out.exit_code == 0, out.output
    assert "2 of 2 scored runs passed across 2 repeats; mean pass rate 100%, range 100%–100%" in out.output
    cfg_dir = next(p for p in (root / ".ironjarvis" / "harness-bench").iterdir() if p.is_dir())
    assert sorted(p.name for p in (cfg_dir / "runs").iterdir()) == ["bugfix-pagination", "bugfix-pagination.r2"]
    assert "## Across 2 repeats" in (cfg_dir / "report.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The exact model
# --------------------------------------------------------------------------- #


async def test_a_run_without_a_model_is_refused_before_any_task_starts(tmp_path):
    stub = _RepeatStub()
    stub.model = ""
    with pytest.raises(BenchRefused) as caught:
        await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES)
    assert str(caught.value) == MODEL_REQUIRED == "Name the model with --model, so the record says which one ran."
    assert stub.folders == [] and not (tmp_path / "harness-bench").exists()
    jarvis_like = _RepeatStub()
    jarvis_like.name, jarvis_like.provider = "jarvis", ""
    with pytest.raises(BenchRefused, match="Name the provider with --provider"):
        await run_bench(runner=jarvis_like, home=tmp_path, tasks=list(TASKS.values()), names=NAMES)
    assert jarvis_like.folders == [] and str(PROVIDER_REQUIRED).startswith("Name the provider")
    # A dry run may list without one.
    res = await run_bench(runner=stub, home=tmp_path, tasks=list(TASKS.values()), names=NAMES, dry_run=True)
    assert res.plan_lines and stub.folders == []


def test_the_command_line_refuses_a_run_with_no_model_but_lists_one(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    record = _fake_env(monkeypatch, tmp_path, "solve")
    root = str(tmp_path / "bench-root")
    for name, script in (("claude", "fake_claude_bench_v1363.py"), ("codex", "fake_codex_bench_v1363.py")):
        binary = _shim(tmp_path, name, FIXTURES / script)
        out = CliRunner().invoke(bench_app, ["run", "--runner", name, "--binary", binary, "--root", root,
                                             "--tasks", "bugfix-pagination"])
        assert out.exit_code == 2 and out.output.strip() == MODEL_REQUIRED, out.output
        out = CliRunner().invoke(bench_app, ["run", "--runner", name, "--binary", binary, "--root", root,
                                             "--tasks", "bugfix-pagination", "--dry-run"])
        assert out.exit_code == 0 and "would run" in out.output, out.output
    out = CliRunner().invoke(bench_app, ["run", "--runner", "jarvis", "--model", "m", "--root", root])
    assert out.exit_code == 2 and out.output.strip() == PROVIDER_REQUIRED
    out = CliRunner().invoke(bench_app, ["run", "--runner", "claude", "--model", "m", "--effort", "max",
                                         "--root", root])
    assert out.exit_code == 2 and out.output.strip() == "--effort must be one of low, medium, high."
    assert not record.exists(), "no task may start before the refusal"


async def test_claude_code_is_given_the_model_and_effort_and_its_reported_model_is_kept(tmp_path, monkeypatch):
    record = _fake_env(monkeypatch, tmp_path, "solve")
    runner = ClaudeRunner(model="sonnet", effort="low",
                          binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()),
                          names=["bugfix-pagination"])
    rec = res.records[0]
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "low"
    assert (rec["model_passed"], rec["effort_passed"], rec["model_reported"]) == (
        "sonnet", "low", "claude-sonnet-4-6")
    assert res.config.effort == "low" and res.config.identity()["effort"] == "low"
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Model as passed: claude / sonnet, effort low. Model the runs reported: claude-sonnet-4-6." in report
    assert "reported a different model" not in report
    combined = (res.config_dir.parent / "report.md").read_text(encoding="utf-8")
    assert "| claude | claude / sonnet | low | claude-sonnet-4-6 | none |" in combined

    # A run that answered on ANOTHER model is flagged, run by run.
    monkeypatch.setenv("FAKE_BENCH_REPORTED_MODEL", "claude-opus-4-6")
    runner = ClaudeRunner(model="sonnet", effort="high",
                          binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()),
                          names=["bugfix-pagination"])
    assert res.records[0]["model_reported"] == "claude-opus-4-6"
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "**1 run(s) reported a different model than the one passed**" in report
    assert "| bugfix-pagination | bugfix | passed |" in report
    assert "reported model claude-opus-4-6, not sonnet" in report


async def test_codex_is_given_the_model_and_effort_and_never_invents_a_model(tmp_path, monkeypatch):
    record = _fake_env(monkeypatch, tmp_path, "solve")
    monkeypatch.delenv("FAKE_CODEX_REPORTS_MODEL", raising=False)
    runner = CodexRunner(model="gpt-6-astra", effort="low",
                         binary=_shim(tmp_path, "codex", FIXTURES / "fake_codex_bench_v1363.py"))
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()),
                          names=["bugfix-pagination"])
    rec = res.records[0]
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"
    setting = argv[argv.index("-c") + 1]
    # Codex parses the value as TOML: it must be a TOML string.
    assert tomllib.loads(setting) == {"model_reasoning_effort": "low"}
    assert (rec["model_passed"], rec["effort_passed"], rec["model_reported"]) == ("gpt-6-astra", "low", None)
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Model the runs reported: model not reported." in report
    # When its events DO name a model, it is kept as reported.
    monkeypatch.setenv("FAKE_CODEX_REPORTS_MODEL", "gpt-6-astra-2026-10-01")
    res = await run_bench(runner=CodexRunner(model="gpt-6-astra", effort="medium", binary=runner.binary),
                          home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    assert res.records[0]["model_reported"] == "gpt-6-astra-2026-10-01"


async def test_the_effort_and_model_are_part_of_the_setup(tmp_path, monkeypatch):
    _fake_env(monkeypatch, tmp_path, "solve")
    binary = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    dirs = set()
    for model, effort in (("sonnet", "low"), ("sonnet", "high"), ("opus", "low")):
        res = await run_bench(runner=ClaudeRunner(model=model, effort=effort, binary=binary),
                              home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
        dirs.add(res.config_dir)
    assert len(dirs) == 3
    base = Configuration(runner="r", provider="p", model="m", harness={}, version="1")
    assert "effort" not in base.identity()  # a setup that never had one keeps its key
    assert len({base.key, Configuration("r", "p", "m", {}, "1", effort="low").key,
                Configuration("r", "p", "m", {}, "1", effort="high").key}) == 3


async def test_jarvis_sends_the_reasoning_level_only_where_the_pair_offers_it(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))
    seen: list[dict] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen.append(dict(kw))
        resp = LLMResponse(text="Done.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": provider or "x", "model": model or "x",
               "reasoning": kw.get("reasoning", "")}

    monkeypatch.setattr(platform.router, "stream", stream)
    offered = JarvisRunner(platform, provider="anthropic", model="claude-sonnet-4-5", effort="low")
    res = await run_bench(runner=offered, home=platform.config.home, tasks=list(TASKS.values()),
                          names=["bugfix-leap-year"])
    rec = res.records[0]
    assert seen and seen[0].get("reasoning") == "low"
    assert (rec["model_passed"], rec["effort_passed"], rec["model_reported"], rec["effort_reported"]) == (
        "claude-sonnet-4-5", "low", "claude-sonnet-4-5", "low")
    assert offered.identity()["reasoning"] == "low"
    seen.clear()
    plain = JarvisRunner(platform, provider="scripted", model="s", effort="low")
    res = await run_bench(runner=plain, home=platform.config.home, tasks=list(TASKS.values()),
                          names=["bugfix-leap-year"])
    rec = res.records[0]
    assert seen and "reasoning" not in seen[0]
    assert rec["effort_passed"] == "not offered" and rec["effort_reported"] is None
    assert plain.identity()["reasoning"] == "not offered"
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "Model as passed: scripted / s, effort not offered." in report
    # No effort asked (the refine loop): nothing sent, and no key in the identity.
    assert "reasoning" not in JarvisRunner(platform, provider="anthropic", model="claude-sonnet-4-5").identity()


def test_default_is_never_a_model_in_a_report():
    old = {"runner": "codex", "provider": "codex", "model": "", "key": "k" * 64}
    records = [{"task_id": "a", "category": "bugfix", "passed": True}]
    for text in (combined_report([(old, records)]), config_report(old, records)):
        assert "default" not in text
        assert "model not named" in text


@pytest.mark.parametrize("passed,reported,same", [
    ("sonnet", "claude-sonnet-4-6", True),
    ("opus", "claude-opus-4-6[1m]", True),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", True),
    ("gpt-6-astra", "openai/gpt-6-astra", True),
    ("sonnet", "claude-opus-4-6", False),
    ("claude-sonnet-4-5", "claude-sonnet-4-6", False),
    ("gpt-6-astra", "gpt-6-sol", False),
    ("sonnet", None, True),
])
def test_a_reported_model_matches_the_one_passed_by_name_alias_or_snapshot(passed, reported, same):
    assert model_matches(passed, reported) is same


def test_a_run_outcome_knows_no_model_until_one_is_reported():
    out = RunOutcome()
    assert out.model_reported is None and out.effort_reported is None
    assert re.search(r"\bdefault\b", json.dumps(out.to_dict())) is None
