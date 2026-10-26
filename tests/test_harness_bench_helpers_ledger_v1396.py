"""v1.396.0 — a single-model Jarvis run that starts a helper ADDS UP.

Before v1.396.0 the Jarvis runner's record carried only the turn's own done
receipt, while the ledger counted every model call the platform's router made
— a helper's included (it runs on the same router). The first run whose
planner started a helper recorded ``ledger_ok: False`` and left no completion
marker, so it would run again on every invocation and never be reused. The
record now carries the helper's share (its session row's receipt) and the
identity holds.

Only APIs older than v1.396.0 are imported, so this pin runs against the
code before it, and goes red there.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness_bench.bench import MARKER_NAME, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.runners import JarvisRunner
from iron_jarvis.platform import build_platform
from tests._fakes import bench_team_v1396 as W


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    w = W.wire(platform, monkeypatch)
    w.platform = platform
    return w


async def test_a_single_model_run_that_starts_a_helper_adds_up(world):
    world.alone_helper = True
    runner = JarvisRunner(world.platform, provider=W.PLANNER[0], model=W.PLANNER[1], round_budget=8)
    res = await run_bench(runner=runner, home=world.platform.config.home, tasks=load_tasks(),
                          names=[W.TASK_A], seed=0)
    rec = res.records[0]
    claude_calls = [c for c in world.calls if c["provider"] == W.PLANNER[0]]
    # The planner's calls and its helper's ONE call, each 900 tokens in.
    assert rec["model_calls"] == len(claude_calls) >= 3
    assert rec["ledger_ok"] is True, rec["error"]
    assert rec["tokens_in"] == 900 * len(claude_calls)
    assert rec["cost_list_price_usd"] == pytest.approx(0.0125 * len(claude_calls))
    # A run whose numbers add up is MARKED, so the next invocation reuses it.
    assert (res.config_dir / "runs" / W.TASK_A / MARKER_NAME).exists()
