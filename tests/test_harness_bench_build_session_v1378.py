"""v1.378.0 (IJ harness part 2, Phase 7) — a benchmark turn runs AS A BUILD
SESSION.

Phase 7 reruns the 30-task benchmark "with the new tools armed". The Jarvis
runner already armed the whole coding set (``harness/mode.CODING_TOOLS``:
helpers, skills, the agent's browser), but it ran every turn under the chat
lane's ``"chat"`` session — so the shell had the SCRUBBED environment and was
described to the model as one ("no python … no POSIX tools"), and v1.377.0's
"the agent's terminal finds your tools" (``harness/command_env``: a
``build:<id>`` call gets the user's PATH and home) never reached a benchmark
turn; nothing held the model for a helper either. A Build session does all
three (``routes/build.start_build_turn``: ``session_id=build:<id>``, the
held provider + model). These pins hold the runner to the same:

* the turn's tool calls run under a fresh ``build:`` session — the shell's
  command gets the Build environment (the user's home is in it; the scrubbed
  one has none), and the program it leaves running is ended with THAT session;
* the model is held for the turn (``providers.hold``) and the hold is gone
  after it;
* the configuration's identity names it, and the shell the model is shown is
  the Build session's description.

Nothing graded changes: the same prompt, the same copy, the same checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness.command_env import build_shell_description, is_build_call
from iron_jarvis.harness_bench.bench import run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.runners import JarvisRunner
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.hold import current_hold

#: A command that writes WHICH home-folder variables it received (names only,
#: never a value): the Build environment keeps the user's home, the scrubbed
#: one has none.
_HOME_KEYS = (
    "import os,json,pathlib; pathlib.Path('home_keys.json').write_text(json.dumps("
    "sorted(k for k in os.environ if k.upper() in ('USERPROFILE','HOME'))))"
)


def _wrap_lane(monkeypatch, seen: dict) -> None:
    """Record the keyword arguments the runner hands the REAL stream lane."""
    from iron_jarvis.daemon import chat_stream

    real = chat_stream.stream_chat_turn

    async def wrapped(platform, personas, body, **kw):
        seen.setdefault("calls", []).append(dict(kw))
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(chat_stream, "stream_chat_turn", wrapped)


async def test_a_bench_turn_runs_as_a_build_session_with_the_model_held(tmp_path, monkeypatch):
    procs.end_all()
    platform = build_platform(str(tmp_path / "root"))
    seen: dict = {}
    holds: list = []
    shell_words: list[str] = []
    _wrap_lane(monkeypatch, seen)

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        hold = current_hold()
        holds.append((hold.provider, hold.model) if hold is not None else None)
        shell_words.extend(t.get("description", "") for t in tools or [] if t.get("name") == "shell")
        if len(holds) == 1:
            resp = LLMResponse(text="", usage={"input_tokens": 5, "output_tokens": 1}, tool_calls=[
                ToolCall(id="s1", name="shell", arguments={"command": f'"{sys.executable}" -c "{_HOME_KEYS}"'})])
        else:
            resp = LLMResponse(text="Done.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    try:
        res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(),
                              names=["bugfix-leap-year"])
    finally:
        procs.end_all()
    rec = res.records[0]
    assert rec["tool_calls"] == 1, rec

    # The turn's tool calls ran under a Build session of their own.
    session = seen["calls"][0].get("session_id")
    assert session and is_build_call(session), seen["calls"][0]
    # ... so its command got the Build environment: the user's home folder
    # (the scrubbed environment carries none).
    work = res.config_dir / "runs" / "bugfix-leap-year" / "work"
    keys = json.loads((work / "home_keys.json").read_text(encoding="utf-8"))
    assert keys, "the command ran with the scrubbed environment, not a Build session's"
    # ... and the model was shown the Build session's shell.
    assert shell_words and shell_words[0] == build_shell_description(), shell_words[:1]

    # The model was held for every call of the turn, and the hold ends with it.
    assert holds and all(h == ("scripted", "s") for h in holds), holds
    assert current_hold() is None


def test_the_identity_names_the_build_session_and_its_shell(tmp_path):
    platform = build_platform(str(tmp_path / "root"))
    runner = JarvisRunner(platform, provider="custom", model="glm-5.3-flash")
    ident = runner.identity()
    assert ident["session"] == "build"
    shell = [s for s in runner.harness().specs(platform.registry) if s["name"] == "shell"]
    assert shell and shell[0]["description"] == build_shell_description()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job objects keep the child")
async def test_a_program_the_turn_left_running_is_ended_with_its_build_session(tmp_path, monkeypatch):
    """The program a turn's shell leaves running is kept under the turn's OWN
    session (now ``build:<id>``) until that session ends; the runner ends
    exactly that session after the run."""
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
    calls: list[int] = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            resp = LLMResponse(text="", usage={"input_tokens": 5, "output_tokens": 1}, tool_calls=[
                ToolCall(id="s1", name="shell", arguments={"command": f'"{sys.executable}" -c "{child}"'})])
        else:
            resp = LLMResponse(text="Started it.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    monkeypatch.setattr(platform.router, "stream", stream)
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    pid = None
    try:
        res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(),
                              names=["bugfix-leap-year"])
        rec = res.records[0]
        session = seen["calls"][0].get("session_id")
        assert session and is_build_call(session)
        pid_file = res.config_dir / "runs" / "bugfix-leap-year" / "work" / "kid.pid"
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert procs.kept_jobs(session) == 0, "the turn's hidden child is still kept for its session"
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not psutil.pid_exists(pid), "a program the turn left running outlived the run"
        assert any("left running" in n for n in rec["notes"]), rec["notes"]
    finally:
        procs.end_all()
        if pid:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
