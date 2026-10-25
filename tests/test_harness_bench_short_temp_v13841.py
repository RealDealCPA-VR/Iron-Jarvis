"""The agent's folder is named by its FULL path, even when TEMP is a short
8.3 name (v1.384.1).

v1.384.0's Tests and Release gates both failed one benchmark pin,
``test_the_claude_runner_runs_headless_in_the_copy``: GitHub's Windows
runners set TEMP to ``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp``, so the agent
folder (made under the system temp, outside the bench root) was named by
that short form in the agent's prompt, while the same folder resolved to its
long form everywhere else. One folder with two names: the prompt, the
recorded working folder and every comparison disagreed. This PC's TEMP is a
long path, which is why the pin passed here.

``agent_runs_root`` now resolves the parent it is made in, so the agent sees
one name. These pins make a short-named temp folder on this PC and check the
root and a whole Claude Code run through it.
"""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
from pathlib import Path

import pytest

from iron_jarvis.harness_bench import bench as bench_mod
from iron_jarvis.harness_bench.bench import run_bench
from iron_jarvis.harness_bench.runners import ClaudeRunner
from tests.test_harness_bench_v1363 import FIXTURES, TASKS, _fake_env, _shim

LONG_NAME = "Long Temp Folder For The Short Name Test"


def _short_path(path: Path) -> str:
    """The 8.3 short form of an existing folder, or "" when there is none."""
    if sys.platform != "win32":
        return ""
    get = ctypes.windll.kernel32.GetShortPathNameW
    buf = ctypes.create_unicode_buffer(1024)
    n = get(str(path), buf, len(buf))
    if not n or n >= len(buf):
        return ""
    return buf.value


@pytest.fixture
def short_temp(tmp_path, monkeypatch) -> tuple[Path, str]:
    long = tmp_path / LONG_NAME
    long.mkdir()
    short = _short_path(long)
    if not short or "~" not in short:
        pytest.skip("this volume makes no 8.3 short names")
    monkeypatch.setattr(tempfile, "tempdir", short)
    monkeypatch.setattr(bench_mod, "AGENT_RUNS_PARENT", None)
    return long, short


def test_the_agent_runs_root_is_named_by_its_full_path(short_temp):
    long, short = short_temp
    assert tempfile.gettempdir() == short  # the runner's situation
    root = bench_mod.agent_runs_root()
    assert "~" not in str(root), root
    assert root == long.resolve() / bench_mod.AGENT_RUNS_DIRNAME


async def test_a_claude_run_under_a_short_temp_names_one_folder(short_temp, tmp_path, monkeypatch):
    """The exact v1.384.0 gate failure, reproduced: the prompt names the
    folder the agent really works in, by the same name."""
    record = _fake_env(monkeypatch, tmp_path, "solve")
    runner = ClaudeRunner(
        model="claude-sonnet-4-5", binary=_shim(tmp_path, "claude", FIXTURES / "fake_claude_bench_v1363.py")
    )
    res = await run_bench(runner=runner, home=tmp_path / "home", tasks=list(TASKS.values()), names=["bugfix-pagination"])
    rec = res.records[0]
    assert rec["ok_run"] is True and rec["passed"] is True, (rec["error"], rec["grade_reason"])
    seen = json.loads(record.read_text(encoding="utf-8"))
    worked = Path(seen["cwd"]).resolve()
    assert "~" not in seen["prompt"]
    assert str(worked) in seen["prompt"]
