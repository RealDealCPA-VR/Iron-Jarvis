"""v1.383.0 review — every run of the benchmark is worked ALONE.

Repeats are only a measurement when each one is independent. As first built,
an agent worked in ``runs/<task>.r2/work`` — two folders from repeat 1's
finished ``runs/<task>/work`` (and every other finished run of the same
setup), its prompt named that path, and its environment carried the folder the
bench was started from. Pinned here, through the real runners:

* an agent works in a fresh, randomly named folder under the system temp
  (``agent_runs_root()``), OUTSIDE the bench root; repeat 2's agent finds
  nothing of repeat 1's at ``../../<task>/work`` or anywhere beside its own
  folder; the folder is moved into ``runs/<id>/work`` when the run ends, and
  grading, the marker, reuse and the tripwire work on it as before;
* the prompt and the environment an agent is given (a CLI's, and a Jarvis
  turn's Build session) name no path to the bench root (``PWD``/``OLDPWD``
  of the shell the bench was started from are never handed on);
* a write beside the agent's folder is still reported (the outside-folder
  snapshot covers the temporary folder too);
* a run cut off part-way leaves no temporary folder: a cancelled run removes
  its own, and a run whose process was killed has its folder swept at the
  next start — only one whose owner process is gone, never another live
  bench's.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import bench as bench_mod
from iron_jarvis.harness_bench.bench import run_bench
from iron_jarvis.harness_bench.runners import ClaudeRunner, JarvisRunner
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_harness_bench_v1363 import FIXTURES, SOLUTIONS, TASKS, _fake_env, _shim, _StubRunner

TASK = "bugfix-pagination"


def _norm(text: str) -> str:
    return str(text).replace("\\", "/").lower()


def _mentions(value: str, path: Path) -> bool:
    return _norm(path).rstrip("/") in _norm(value)


@pytest.fixture
def agent_parent(tmp_path, monkeypatch):
    """The agents' temporary folders go under this test's own folder (the
    real default is the system temp — pinned below)."""
    parent = tmp_path / "agent-temp"
    monkeypatch.setattr(bench_mod, "AGENT_RUNS_PARENT", parent, raising=False)
    return parent


def _agent_folders(parent: Path) -> list[Path]:
    root = parent / "ij-bench-runs"
    return sorted(p for p in root.iterdir()) if root.is_dir() else []


async def test_repeat_twos_agent_finds_nothing_of_repeat_one_beside_it(tmp_path, monkeypatch, agent_parent):
    _fake_env(monkeypatch, tmp_path, "peek")
    monkeypatch.setenv("FAKE_PEEK_TASK", TASK)
    home = tmp_path / "home"
    # As if the bench were started from a shell standing inside its root.
    monkeypatch.setenv("PWD", str(home / "harness-bench"))
    monkeypatch.setenv("OLDPWD", str(home))
    monkeypatch.setenv("PATH", str(home / "bin") + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("IJ_TEST_POINTER", str(home / "harness-bench" / "notes.txt"))
    # Another bench working at the same time (another setup, say): its agent
    # folder is live, so it is neither swept nor within reach of this one.
    import psutil

    other = agent_parent / "ij-bench-runs" / "concurrent0001"
    (other / "run" / "work").mkdir(parents=True)
    (other / "run" / "work" / "peek-token.txt").write_text("another run's work", encoding="utf-8")
    me = psutil.Process()
    (other / bench_mod.OWNER_NAME).write_text(json.dumps({"pid": me.pid, "started": me.create_time()}),
                                              encoding="utf-8")
    runner = ClaudeRunner(model="sonnet", effort="low",
                          binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py"))
    res = await run_bench(runner=runner, home=home, tasks=list(TASKS.values()), names=[TASK], repeats=2)
    assert [r["passed"] for r in res.records] == [True, True], [r["grade_reason"] for r in res.records]
    for key in (TASK, f"{TASK}.r2"):
        work = res.config_dir / "runs" / key / "work"
        peek = json.loads((work / "peek.json").read_text(encoding="utf-8"))
        assert peek["sibling_work"] is False, f"{key}: ../../{TASK}/work held another run's work"
        assert peek["found"] == [], f"{key}: another run's files sat beside this one: {peek['found']}"
        cwd = Path(peek["cwd"])
        assert not _mentions(peek["cwd"], home), f"{key} worked inside the bench root: {cwd}"
        assert _mentions(peek["cwd"], agent_parent / "ij-bench-runs"), cwd
        assert not _mentions(peek["prompt"], home), "the prompt names the bench root"
        leaked = sorted(k for k, v in peek["env"].items() if _mentions(v, home))
        assert leaked == [], f"{key}: the agent's environment names the bench root in {leaked}"
        assert not {"PWD", "OLDPWD"} & {k.upper() for k in peek["env"]}
        # The folder came home: graded, marked and reusable where it always was.
        assert (work / "peek-token.txt").is_file() and (res.config_dir / "runs" / key / "complete.json").is_file()
    assert _agent_folders(agent_parent) == [other], "a temporary agent folder was left behind"
    again = await run_bench(runner=runner, home=home, tasks=list(TASKS.values()), names=[TASK], repeats=2)
    assert [r["reused"] for r in again.records] == [True, True]


def test_the_default_agent_folder_is_under_the_system_temp():
    import tempfile

    assert bench_mod.AGENT_RUNS_PARENT is None
    root = bench_mod.agent_runs_root()
    # v1.384.1: the parent is the temp folder RESOLVED — on GitHub's runners
    # TEMP is an 8.3 short name (C:\Users\RUNNER~1\...), and the agent is
    # told the full one (tests/test_harness_bench_short_temp_v13841.py).
    assert root.parent == Path(tempfile.gettempdir()).resolve() and root.name == "ij-bench-runs"


async def test_a_cancelled_run_leaves_no_agent_folder(tmp_path, agent_parent):
    class _Cut(_StubRunner):
        async def run(self, task, workspace, rollout, *, timeout_s):
            self.calls.append(task.id)
            assert agent_parent in Path(workspace).parents
            raise asyncio.CancelledError()

    stub = _Cut()
    with pytest.raises(asyncio.CancelledError):
        await run_bench(runner=stub, home=tmp_path / "home", tasks=list(TASKS.values()), names=[TASK])
    assert stub.calls == [TASK]
    assert _agent_folders(agent_parent) == []


async def test_a_failing_setup_leaves_no_agent_folder(tmp_path, agent_parent):
    from tests.test_harness_bench_frontier_v1383 import _setup_task

    task = _setup_task(tmp_path, "import sys\nsys.exit(3)\n")
    res = await run_bench(runner=_StubRunner(), home=tmp_path / "home", tasks=[task])
    assert res.records[0]["unscored_by"] == "infrastructure"
    assert _agent_folders(agent_parent) == []


def _dead_pid() -> tuple[int, float]:
    import psutil

    kid = subprocess.Popen([sys.executable, "-c", "pass"])
    started = psutil.Process(kid.pid).create_time() if psutil.pid_exists(kid.pid) else 0.0
    kid.wait(timeout=30)
    deadline = time.monotonic() + 10
    while psutil.pid_exists(kid.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return kid.pid, started


async def test_a_folder_a_killed_bench_left_is_swept_at_the_next_start_and_only_that_one(tmp_path, agent_parent):
    import psutil

    root = agent_parent / "ij-bench-runs"
    root.mkdir(parents=True)
    dead_pid, dead_start = _dead_pid()
    killed = root / "killed0001"
    (killed / "work").mkdir(parents=True)
    (killed / "work" / "big.txt").write_text("x", encoding="utf-8")
    (killed / bench_mod.OWNER_NAME).write_text(json.dumps({"pid": dead_pid, "started": dead_start}),
                                               encoding="utf-8")
    me = psutil.Process()
    live = root / "live0001"
    (live / "work").mkdir(parents=True)
    (live / bench_mod.OWNER_NAME).write_text(json.dumps({"pid": me.pid, "started": me.create_time()}),
                                             encoding="utf-8")
    # A pid that was reused by another process is not the owner.
    reused = root / "reused0001"
    reused.mkdir()
    (reused / bench_mod.OWNER_NAME).write_text(json.dumps({"pid": me.pid, "started": me.create_time() - 1000}),
                                               encoding="utf-8")
    stranger = root / "notours0001"
    stranger.mkdir()
    await run_bench(runner=_StubRunner(), home=tmp_path / "home", tasks=list(TASKS.values()), names=[TASK])
    left = {p.name for p in _agent_folders(agent_parent)}
    assert "killed0001" not in left and "reused0001" not in left
    assert {"live0001", "notours0001"} <= left


_CLI = "from iron_jarvis.harness_bench.cli import bench_app; bench_app()"


def _owned_by(pid: int) -> list[Path]:
    """The agent folders owned by ``pid`` or one of its descendants (a venv's
    python.exe on Windows is a launcher: the bench runs in its child)."""
    import psutil

    try:
        pids = {pid} | {p.pid for p in psutil.Process(pid).children(recursive=True)}
    except psutil.NoSuchProcess:
        pids = {pid}
    root = bench_mod.agent_runs_root()
    out = []
    for folder in (root.iterdir() if root.is_dir() else []):
        try:
            owner = json.loads((folder / bench_mod.OWNER_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if owner.get("pid") in pids:
            out.append(folder)
    return out


def test_a_killed_runs_agent_folder_is_gone_after_the_next_start(tmp_path):
    """The real thing: the bench process killed while an agent works; its
    temporary folder is left (nothing ran to remove it), and the next start
    sweeps exactly that one."""
    import psutil

    env = {k: v for k, v in os.environ.items() if k not in ("IRONJARVIS_HOME", "ANTHROPIC_API_KEY")}
    env.update({"FAKE_BENCH_SCENARIO": "hang", "FAKE_BENCH_SOLUTION": str(SOLUTIONS / TASK),
                "PYTHONIOENCODING": "utf-8"})
    claude = _shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    root = tmp_path / "bench-root"
    argv = [sys.executable, "-c", _CLI, "run", "--runner", "claude", "--binary", claude, "--model", "sonnet",
            "--root", str(root), "--tasks", TASK]
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(tmp_path))
    try:
        deadline = time.monotonic() + 120
        mine: list[Path] = []
        while time.monotonic() < deadline and not mine:
            mine = [p for p in _owned_by(proc.pid) if any(p.rglob("work"))]
            time.sleep(0.1)
        assert mine, "the run never made its agent folder"
        time.sleep(1.0)
    finally:
        parent = psutil.Process(proc.pid)
        for kid in parent.children(recursive=True):
            kid.kill()
        parent.kill()
        proc.wait(timeout=30)
    assert mine[0].is_dir(), "a killed run's folder should still be there before the next start"
    env["FAKE_BENCH_SCENARIO"] = "solve"
    out = subprocess.run(argv, env=env, capture_output=True, cwd=str(tmp_path), timeout=300)
    assert out.returncode == 0, out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
    assert not mine[0].exists(), "the killed run's agent folder survived the next start"


async def test_a_jarvis_turn_sees_no_path_to_the_bench_root(tmp_path, monkeypatch, agent_parent):
    from iron_jarvis.sandbox.native import build_env

    platform = build_platform(str(tmp_path / "root"))
    home = Path(platform.config.home)
    monkeypatch.setenv("PWD", str(home / "harness-bench"))
    monkeypatch.setenv("OLDPWD", str(home))
    seen: dict = {}

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen["system"] = system
        seen["messages"] = " | ".join(str(getattr(m, "content", m)) for m in messages)
        seen["env"] = build_env()
        resp = LLMResponse(text="Done.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": provider or "x", "model": model or "x"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=2)
    res = await run_bench(runner=runner, home=home, tasks=list(TASKS.values()), names=["bugfix-leap-year"])
    assert seen, "the turn never reached the model"
    bench_root = home / "harness-bench"
    assert not _mentions(seen["messages"], bench_root) and not _mentions(seen["messages"], home), \
        "the turn's messages name the bench root"
    assert not _mentions(seen["system"], bench_root), "the turn's system prompt names the bench root"
    assert not _mentions(seen["system"], home), "the turn's system prompt names the bench's state home"
    leaked = sorted(k for k, v in seen["env"].items() if _mentions(v, home))
    assert leaked == [], f"the Build session's environment names the bench root in {leaked}"
    assert _mentions(seen["messages"], agent_parent / "ij-bench-runs")
    assert os.environ.get("PWD") == str(home / "harness-bench"), "the bench's own environment was not restored"
    assert (res.config_dir / "runs" / "bugfix-leap-year" / "work").is_dir()
    assert _agent_folders(agent_parent) == []
