"""v1.378.0 review (IJ harness part 2, Phase 7) — a benchmark turn's Build
session is ended on EVERY way out of the run, a Stop included.

Since v1.378.0 each benchmark task turn runs under its own fresh
``build:bench-<hex>`` session, and the runner ends exactly that session after
the turn (``procs.end_session``). The doer's pin covers a run that finishes.
A run that is CANCELLED part-way (Ctrl+C on ``bench-harness run``, or anything
that cancels the task awaiting it) left that call behind: the
``CancelledError`` went straight past it, so a program the turn's shell had
left running stayed kept under a session nothing would ever end again — under
the old shared ``"chat"`` session the next run's end at least swept it; with
one session per run nothing does. Measured on the old shape: the kept job is
still there after the cancel, and the program is alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness.command_env import is_build_call
from iron_jarvis.harness_bench.bench import run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.runners import JarvisRunner
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.hold import current_hold


def _wrap_lane(monkeypatch, seen: dict) -> None:
    from iron_jarvis.daemon import chat_stream

    real = chat_stream.stream_chat_turn

    async def wrapped(platform, personas, body, **kw):
        seen.setdefault("calls", []).append(dict(kw))
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(chat_stream, "stream_chat_turn", wrapped)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job objects keep the child")
async def test_a_cancelled_run_still_ends_its_build_session(tmp_path, monkeypatch):
    import psutil

    procs.end_all()
    platform = build_platform(str(tmp_path / "root"))
    seen: dict = {}
    _wrap_lane(monkeypatch, seen)
    child = (
        "import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; "
        "time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL); pathlib.Path('kid.pid').write_text(str(p.pid))"
    )
    second_call = asyncio.Event()
    calls: list[int] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            resp = LLMResponse(text="", usage={"input_tokens": 5, "output_tokens": 1}, tool_calls=[
                ToolCall(id="s1", name="shell", arguments={"command": f'"{sys.executable}" -c "{child}"'})])
            yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}
            return
        # The model's next answer never comes: the user stops the run here.
        second_call.set()
        await asyncio.sleep(120)
        yield {"type": "final", "response": LLMResponse(text="late"), "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    pid = None
    task = asyncio.create_task(run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(),
                                         names=["bugfix-leap-year"]))
    try:
        await asyncio.wait_for(second_call.wait(), 60)
        session = seen["calls"][0].get("session_id")
        assert session and is_build_call(session)
        pid_files = list((tmp_path / "root").rglob("kid.pid"))
        assert len(pid_files) == 1, pid_files
        pid = int(pid_files[0].read_text(encoding="utf-8").strip())
        # Precondition: the program is kept for the turn's session.
        assert procs.kept_jobs(session) == 1
        assert psutil.pid_exists(pid)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The cancelled run ended its session: nothing is kept for it ...
        assert procs.kept_jobs(session) == 0, "a cancelled run left its Build session's program kept"
        # ... and the program it left running is gone.
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not psutil.pid_exists(pid), "a program the cancelled turn left running outlived the run"
        # The model hold did not outlive the run either.
        assert current_hold() is None
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        procs.end_all()
        if pid:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
