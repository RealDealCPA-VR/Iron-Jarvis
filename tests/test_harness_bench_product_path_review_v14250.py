"""v1.425.0 (review) — the product path's two holes, each red on the
builder's code first.

1. THE GLOBAL SETTINGS FOLDER IS NEVER THE BENCH HOME. ``--root <your user
   folder>`` resolved the bench home to ``~/.ironjarvis`` — the folder whose
   ``config.toml`` every Iron Jarvis process layers in (``core/config.
   global_config_path``), the packaged app's included. The product path makes
   real coding sessions there and the benchmark answers every card "Allow
   once", so a settings change a task's model asked for would have landed in
   the owner's own settings. Refused like the app's own folder.

2. A BENCHMARK TURN IS NEVER CARRIED ON BY A DAEMON. A coding-session turn
   cut off (the time limit's Stop did not end it within the grace, the
   follow's last bound, or the whole run cancelled) ended "interrupted", and a
   turn cut off keeps its reply record (``harness/reply_record``) — exactly
   what the boot's resume pass (``routes/build.resume_after_restart``) carries
   on by itself, a model call nobody asked for. The run drops it, through the
   record's own writer (so a save still in flight cannot land after the
   drop), however the run ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.bench import BenchRefused, resolve_bench_home, run_bench
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests import test_harness_bench_product_path_v14250 as P

# --------------------------------------------------------------------------- #
# 1. ~/.ironjarvis is never the bench home
# --------------------------------------------------------------------------- #


def test_the_global_settings_folder_is_never_the_bench_home(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    user = tmp_path / "user"
    user.mkdir()
    # (Path.home() is where every process reads its global settings from:
    # ``core/config.global_config_path``.)
    monkeypatch.setattr(Path, "home", lambda: user)
    with pytest.raises(BenchRefused, match="settings every Iron Jarvis"):
        resolve_bench_home(str(user))
    # A scratch folder beside it is fine.
    scratch = tmp_path / "scratch"
    assert resolve_bench_home(str(scratch)) == (scratch / ".ironjarvis").resolve()


def test_the_command_line_refuses_the_global_settings_folder(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user)
    out = CliRunner().invoke(bench_app, [
        "run", "--runner", "jarvis", "--provider", "custom", "--model", "glm-5.3-flash",
        "--root", str(user), "--dry-run", "--tasks", "bugfix-leap-year",
    ])
    assert out.exit_code == 2, out.output
    assert "settings every Iron Jarvis" in out.output
    # Nothing was made there (no database, no results folder).
    assert not (user / ".ironjarvis").exists()


# --------------------------------------------------------------------------- #
# 2. nothing a daemon would carry on
# --------------------------------------------------------------------------- #


async def _wait_gone(pid: int, seconds: float = 20.0) -> bool:
    import psutil

    deadline = time.monotonic() + seconds
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return not psutil.pid_exists(pid)


def _sleeper_command() -> str:
    return (f'"{sys.executable}" -c "import os,pathlib,time; '
            f"pathlib.Path('me.pid').write_text(str(os.getpid())); time.sleep(120)\"")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job objects own the command's tree")
async def test_a_turn_cut_off_after_its_stop_leaves_nothing_a_daemon_would_carry_on(tmp_path, monkeypatch):
    """The time limit's Stop does not end the running step (it does not end
    by itself inside its grace), so the turn is cut off a grace later. The
    run then leaves: no reply record the boot would resume, no turn still
    registered, no step still running, and no program."""
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.daemon.routes import build as B
    from iron_jarvis.harness import reply_record as RR
    from iron_jarvis.harness import stall
    from iron_jarvis.harness_bench import runners as R

    procs.end_all()
    P._native_shell(monkeypatch)
    monkeypatch.setattr(R, "_GRACE_S", 1.0)
    # A step that does not end by itself after the Stop for longer than the
    # grace (so the BENCHMARK cuts the turn off, not the Stop).
    monkeypatch.setattr(stall, "STOP_GRACE_S", 120.0)
    platform = build_platform(str(tmp_path / "root"))
    seen = P._spy(monkeypatch)
    P._wire(platform, monkeypatch, [
        [ToolCall(id="s1", name="shell", arguments={"command": _sleeper_command()})],
        "Done.",
    ])
    # The drop is SLOW (the record's writer is busy): the run must still have
    # it done before it goes on — read at the run's next step, its programs'
    # end (what a later task, or the next run in this home, would meet).
    real_drop = RR.drop

    def slow_drop(engine, turn_id):
        time.sleep(0.6)
        real_drop(engine, turn_id)

    monkeypatch.setattr(RR, "drop", slow_drop)
    held_at_end: list[bool] = []
    real_end = R.procs.end_session

    def end_session(session_id):
        if seen["starts"] and session_id.endswith(seen["starts"][0]["thread"]):
            held_at_end.append(RR.has_record(platform.engine, seen["starts"][0]["turn"]))
        return real_end(session_id)

    monkeypatch.setattr(R.procs, "end_session", end_session)
    pid = None
    try:
        res = await run_bench(runner=P._product(platform), home=platform.config.home,
                              tasks=[P._short("bugfix-leap-year", 4.0)])
        rec = res.records[0]
        turn, thread = seen["starts"][0]["turn"], seen["starts"][0]["thread"]
        # It really was cut off (the path this pin is about).
        assert any("cut off" in n for n in rec["notes"]), rec["notes"]
        assert rec["facts"]["timed_out"] is True and rec["failure_kind"] == "agent"
        # NOTHING THE BOOT WOULD CARRY ON BY ITSELF — already gone when the
        # run ended the session's programs.
        assert held_at_end and held_at_end[0] is False, held_at_end
        assert not RR.has_record(platform.engine, turn)
        assert all(k["turn_id"] != turn for k in RR.kept(platform.engine))
        # No turn left behind.
        lg = platform.turn_logs._logs.get(turn)
        assert lg is not None and lg.task is not None and lg.task.done()
        assert TURNS.stop(turn) is False
        assert thread not in B._STARTING
        assert stall.running_steps(turn) == []
        pid = int((res.config_dir / "runs" / "bugfix-leap-year" / "work" / "me.pid").read_text().strip())
        assert await _wait_gone(pid), "the step's program outlived the run"
    finally:
        procs.end_all()
        if pid:
            import psutil

            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()


async def test_a_finished_product_turn_keeps_no_reply_record(tmp_path, monkeypatch):
    """A turn that ENDED keeps none either (the turn's own end forgets it;
    pinned here so the run's drop never has to be the only door)."""
    from iron_jarvis.harness import reply_record as RR

    platform = build_platform(str(tmp_path / "root"))
    seen = P._spy(monkeypatch)
    P._wire(platform, monkeypatch, ["Nothing to change."])
    await run_bench(runner=P._product(platform), home=platform.config.home, tasks=[P.TASKS["bugfix-leap-year"]])
    turn = seen["starts"][0]["turn"]
    assert not RR.has_record(platform.engine, turn)


async def test_a_run_cancelled_mid_turn_leaves_nothing_a_daemon_would_carry_on(tmp_path, monkeypatch):
    """The whole run is cancelled while its turn waits on the model (Ctrl+C,
    the benchmark's own stop): the turn is stopped and its reply record
    dropped once the turn has unwound — never left for a boot to resume."""
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.harness import reply_record as RR

    platform = build_platform(str(tmp_path / "root"))
    seen = P._spy(monkeypatch)
    script = P._wire(platform, monkeypatch, [
        [ToolCall(id="l1", name="list_files", arguments={})],
        ("hang", 300.0),
    ])
    runner = P._product(platform)
    task = asyncio.ensure_future(run_bench(runner=runner, home=platform.config.home,
                                           tasks=[P.TASKS["bugfix-leap-year"]]))
    # Wait until the turn is on its SECOND model call: its first round (and
    # the top of the second) was saved, so a reply record exists.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and len(script.calls) < 2:
        await asyncio.sleep(0.05)
    assert len(script.calls) >= 2, "the turn never reached its second model call"
    turn = seen["starts"][0]["turn"]
    assert RR.has_record(platform.engine, turn), "the turn never saved its record (nothing to test)"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    lg = platform.turn_logs._logs[turn]
    with contextlib.suppress(Exception):
        await asyncio.wait({lg.task}, timeout=10)
    assert lg.task.done()
    assert TURNS.stop(turn) is False
    # The drop rides the record's own writer: wait for it to be done.
    await asyncio.get_running_loop().run_in_executor(RR._writer(), lambda: None)
    assert not RR.has_record(platform.engine, turn)


async def test_an_error_in_the_benchmarks_own_reading_still_stops_the_turn(tmp_path, monkeypatch):
    """A fault in the benchmark's own frame reading (a bug, never the turn's)
    is recorded on the run — and the turn it was following is stopped, never
    left running on its own behind a run that has already been graded (the
    lane path closes its turn with the generator; the product path's turn is
    its own task)."""
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.harness_bench import runners as R

    platform = build_platform(str(tmp_path / "root"))
    seen = P._spy(monkeypatch)
    P._wire(platform, monkeypatch, [
        [ToolCall(id="l1", name="list_files", arguments={})],
        ("hang", 300.0),
    ])
    real_see = R._TurnReading.see

    def see(self, event, data):
        if event == "tool_call" and data.get("status") == "finished":
            raise RuntimeError("a fault in the benchmark's reading")
        return real_see(self, event, data)

    monkeypatch.setattr(R._TurnReading, "see", see)
    res = await run_bench(runner=P._product(platform), home=platform.config.home,
                          tasks=[P.TASKS["bugfix-leap-year"]])
    rec = res.records[0]
    assert "a fault in the benchmark's reading" in rec["error"], rec["error"]
    turn = seen["starts"][0]["turn"]
    lg = platform.turn_logs._logs[turn]
    assert lg.task.done(), "the turn kept running after its run was over"
    assert TURNS.stop(turn) is False


def test_the_sessions_account_is_the_bench_homes_own_view(tmp_path):
    """The account a product-path session is made with comes from the bench
    home's OWN account manager (``platform.iron_proxy``, its cached list,
    never a call): off — the default in a scratch home — means NO account
    (this PC's own sign-in, exactly the lane path's); on, the vendor's
    Default exactly as New session picks it."""
    from types import SimpleNamespace

    from iron_jarvis.harness_bench.runners import JarvisRunner, PATH_PRODUCT

    platform = build_platform(str(tmp_path / "root"))
    assert getattr(platform.config, "iron_proxy_enabled", False) is False
    runner = JarvisRunner(platform, provider="claude-cli", model="claude-opus-5-5", path=PATH_PRODUCT,
                          own_step_limit=True)
    setup = runner.session_setup()
    assert "account" not in setup and setup["provider"] == "claude-cli"
    # The home's own manager, switched on, with one Claude Code account.
    profiles = [{"id": "acc-work", "title": "Work", "provider": "anthropic", "lane": "cli", "order": 0}]
    platform.iron_proxy = SimpleNamespace(enabled=True, cached_accounts=lambda: (profiles, {}))
    setup = runner.session_setup()
    assert setup["account"] == "acc-work" and setup["account_name"] == "Work"
