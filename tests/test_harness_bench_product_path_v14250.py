"""v1.425.0 (Wave 5 "Fast and accurate", gap C2) — the benchmark measures the
PRODUCT path.

The Jarvis runner used to call the stream lane directly, under "Don't ask"
(``runners.JarvisRunner._run_lane``): a bare lane, not what the owner uses
every day. ``--path product`` (the command line's default since v1.425.0)
runs each task as a REAL Chat › Code coding session: the session made through
``routes/build.create_session_row`` in the task's copy, the task sent as the
user's message through ``routes/build.start_build_turn`` (the function the
``/build/sessions/{id}/turn`` route uses), the turn followed like the screen
follows it, every card answered "Allow once" and every question skipped by the
benchmark, and the time limit the user's Stop.

Pinned here, each through the REAL product code with a scripted model:

* one task end to end through the product path — the route's own function
  started it with the task's prompt, the lane ran it as that coding session
  ("Ask first", its ``build:`` session, its reply record, its own step limit,
  the question cards on), it PASSED and is graded, marked and ledgered; a task
  the agent gets wrong is graded FAILED (never "did not run");
* a card the "Ask first" posture raises is answered ONCE, by the benchmark;
  a question the model asks is SKIPPED by the benchmark and the model told so;
* the time limit is the user's Stop — a model call still in flight is ended
  well inside the minute the runner gives the turn after its Stop;
* nothing the turn started outlives the run (checked by its process id);
* the round's patience waits are kept on the record, and the report says what
  each coding session did (its steps, its finish check, its waits);
* the path is part of the identity: a lane result is never reused for a
  product run, and the lane's own identity is unchanged (its results stay
  reusable); a forced ``--dialect`` is refused on the product path;
* the benchmark leaves the home's settings as it found them (Emergency stop,
  Local only, the step limit set for its own turns only).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.bench import MARKER_NAME, Configuration, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.harness_bench.runners import (
    PATH_LANE,
    PATH_PRODUCT,
    PRODUCT_APPROVAL_MODE,
    JarvisRunner,
)
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ProviderError, ToolCall

FIXTURES = Path(__file__).parent / "fixtures"
SOLUTIONS = FIXTURES / "harness_bench_solutions"
TASKS = {t.id: t for t in load_tasks()}
_PAGINATION_FIX = (SOLUTIONS / "bugfix-pagination" / "pagination.py.sol").read_text(encoding="utf-8")
#: The scripted model's provider and model.
WHO = ("scripted", "script-1")


class _Script:
    """A scripted model on the router's ``stream()``: one move per call — a
    list of tool calls, a reply text, an exception (raised before any frame)
    or ``("hang", seconds)`` (a model call that does not answer)."""

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        n = len(self.calls)
        self.calls.append({"messages": list(messages), "tools": [t["name"] for t in tools or []],
                           "provider": provider, "model": model})
        move = self.moves[min(n, len(self.moves) - 1)]
        if isinstance(move, BaseException):
            raise move
        if isinstance(move, tuple) and move[0] == "hang":
            await asyncio.sleep(move[1])
            move = "Too late."
        usage = {"input_tokens": 40 + n, "output_tokens": 4}
        if isinstance(move, str):
            yield {"type": "text", "text": move}
            resp = LLMResponse(text=move, usage=usage)
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage=usage)
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}


def _wire(platform, monkeypatch, moves: list) -> _Script:
    script = _Script(moves)
    monkeypatch.setattr(platform.router, "stream", script.stream)
    return script


def _spy(monkeypatch) -> dict:
    """Record what the runner hands the ROUTE's own function and what that
    function hands the lane (both still run for real)."""
    from iron_jarvis.daemon.routes import build as B

    seen: dict = {"starts": [], "lanes": []}
    real_start, real_lane = B.start_build_turn, B.stream_chat_turn

    async def start(d, thread_id, text, turn_id, **kw):
        seen["starts"].append({"thread": thread_id, "text": text, "turn": turn_id, **kw})
        return await real_start(d, thread_id, text, turn_id, **kw)

    async def lane(platform, personas, body, **kw):
        seen["lanes"].append({"body": body, **kw})
        return await real_lane(platform, personas, body, **kw)

    monkeypatch.setattr(B, "start_build_turn", start)
    monkeypatch.setattr(B, "stream_chat_turn", lane)
    return seen


def _native_shell(monkeypatch) -> None:
    """The turn's shell runs natively (CI's state: no Docker)."""
    from iron_jarvis.sandbox import shell_tool as ST
    from iron_jarvis.sandbox.native import NativeSandbox

    monkeypatch.setattr(ST.SandboxManager, "get", lambda self: NativeSandbox(self.policy))


def _product(platform, **kw) -> JarvisRunner:
    return JarvisRunner(platform, provider=WHO[0], model=WHO[1], path=PATH_PRODUCT,
                        own_step_limit=kw.pop("own_step_limit", True), **kw)


def _short(task_id: str, seconds: float):
    t = TASKS[task_id]
    return type(t)(**{**t.__dict__, "timeout_s": seconds})


# --------------------------------------------------------------------------- #
# one task, end to end, as a real coding session
# --------------------------------------------------------------------------- #


async def test_a_task_runs_end_to_end_as_a_real_coding_session(tmp_path, monkeypatch):
    from iron_jarvis.harness.sessions import get_row, session_id_for

    platform = build_platform(str(tmp_path / "root"))
    script = _wire(platform, monkeypatch, [
        [ToolCall(id="p1", name="read_file", arguments={"path": "pagination.py"})],
        [ToolCall(id="p2", name="write_file", arguments={"path": "pagination.py", "content": _PAGINATION_FIX})],
        "Fixed where each page starts and how pages are counted.",
    ])
    seen = _spy(monkeypatch)
    before = platform.config.model_dump()
    res = await run_bench(runner=_product(platform), home=platform.config.home,
                          tasks=[TASKS["bugfix-pagination"]])
    rec = res.records[0]
    run_dir = res.config_dir / "runs" / "bugfix-pagination"
    # Graded, marked and ledgered like any run.
    assert rec["passed"] is True, (rec["error"], rec["grade_reason"], rec["grade_output"])
    assert rec["ok_run"] is True and rec["failure_kind"] == "" and rec["ledger_ok"] is True
    assert (run_dir / MARKER_NAME).exists()
    assert rec["files_outside"] == []
    # THE ROUTE'S OWN FUNCTION started it, the task's prompt as the user's
    # message — once.
    (start,) = seen["starts"]
    assert start["text"] == (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert start.get("started_by", "person") == "person" and not start.get("continuation")
    # ... and the lane ran it AS that coding session: the "Ask first"
    # posture, the session's own build: id, a reply record, the question
    # cards on, and the session's own step limit (none: the safety stop).
    (lane,) = seen["lanes"]
    body = lane["body"]
    # (The session stores the DEFAULT posture as nothing — the app's own way
    # — and the lane reads nothing as "Ask first".)
    from iron_jarvis.daemon.chat_turn import normalize_approval_mode

    assert normalize_approval_mode(body.approval_mode) == PRODUCT_APPROVAL_MODE == "approve_for_me"
    assert body.mcp_cards is True and body.thread_id == start["thread"] and body.turn_id == start["turn"]
    assert body.provider == WHO[0] and body.model == WHO[1]
    assert lane["harness"] == "auto" and lane["session_id"] == session_id_for(start["thread"])
    assert lane["reply_record"] is not None and lane["stay_in_chat"] is True
    assert lane["round_budget"] == 1000 and lane["should_stop"]() is False
    # The model was shown the coding session's question tool (the lane path's
    # turn never arms it).
    assert "ask_user" in script.calls[0]["tools"]
    # The session is a real coding session in the home's database, and its
    # reply line is what the record read.
    row = get_row(platform.engine, start["thread"])
    assert row is not None
    lines = json.loads(row.messages_json)
    assert lines[0]["role"] == "user" and lines[0]["turnId"] == start["turn"]
    assert lines[-1]["role"] == "assistant" and lines[-1]["turnId"] == start["turn"]
    cs = rec["extra"]["coding_session"]
    assert cs["thread_id"] == start["thread"] and cs["turn_id"] == start["turn"]
    assert cs["line_saved"] is True and cs["step_limit_rounds"] == 1000
    assert [s["name"] for s in cs["steps"]][:2] == ["read_file", "write_file"]
    assert cs["step_count"] == len(lines[-1]["steps"]) and cs["failed_steps"] == 0
    # The finish check ran (a file changed) and the record says what it found.
    assert cs["finish"]["tests"] == "none" and rec["extra"]["finish"]["tests"] == "none"
    assert rec["model_rounds"] == 4 and rec["tool_calls"] == 2
    # The report says the path and what the session did.
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "jarvis · coding session" in report
    assert "## What each coding session did" in report
    assert "| bugfix-pagination | 2 | 0 | 0 | Checked: no tests found | - | 0 / 0 | none |" in report
    combined = (platform.config.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    assert "| jarvis · coding session | scripted / script-1 |" in combined
    assert "## What running each task as a coding session added" in combined
    # The home's settings are as the benchmark found them (Emergency stop,
    # Local only, the step limit: nothing was changed).
    assert platform.config.model_dump() == before


async def test_a_task_the_agent_gets_wrong_is_graded_failed_on_the_product_path(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))
    _wire(platform, monkeypatch, [
        [ToolCall(id="w", name="write_file", arguments={"path": "pagination.py", "content": "# nothing\n"})],
        "Done.",
    ])
    res = await run_bench(runner=_product(platform), home=platform.config.home,
                          tasks=[TASKS["bugfix-pagination"]])
    rec = res.records[0]
    # The agent had its go: a scored fail, never "did not run".
    assert rec["ok_run"] is True and rec["passed"] is False and rec["unscored_by"] is None
    assert rec["failure_kind"] == "" and rec["ledger_ok"] is True
    assert (res.config_dir / "runs" / "bugfix-pagination" / MARKER_NAME).exists()


# --------------------------------------------------------------------------- #
# cards and questions: answered by the benchmark
# --------------------------------------------------------------------------- #


async def test_a_card_is_answered_once_by_the_benchmark(tmp_path, monkeypatch):
    """"Ask first" cards a command (it is not a read-only one); the benchmark
    answers it "Allow once", names itself as who answered, and the command
    runs — once."""
    _native_shell(monkeypatch)
    platform = build_platform(str(tmp_path / "root"))
    command = f'"{sys.executable}" -c "import pathlib; pathlib.Path(\'ran.txt\').write_text(\'yes\')"'
    _wire(platform, monkeypatch, [
        [ToolCall(id="s1", name="shell", arguments={"command": command})],
        "Ran it.",
    ])
    answers: list[tuple[str, str]] = []
    real = platform.approvals.resolve

    def resolve(approval_id, decision, *, actor="app"):
        answers.append((decision, actor))
        return real(approval_id, decision, actor=actor)

    monkeypatch.setattr(platform.approvals, "resolve", resolve)
    try:
        # (A minute's limit: a card nobody answered would wait for the Stop.)
        res = await run_bench(runner=_product(platform), home=platform.config.home,
                              tasks=[_short("bugfix-leap-year", 60.0)])
    finally:
        procs.end_all()
    rec = res.records[0]
    assert answers == [("once", "bench")], answers
    assert rec["facts"]["asks"] == {"cards": 1, "questions": 0, "by_tool": {"shell": 1}}
    work = res.config_dir / "runs" / "bugfix-leap-year" / "work"
    assert (work / "ran.txt").read_text(encoding="utf-8") == "yes"
    cs = rec["extra"]["coding_session"]
    assert [s["name"] for s in cs["steps"]][0] == "shell" and cs["steps"][0]["ok"] is True


async def test_a_question_the_model_asks_is_skipped_by_the_benchmark(tmp_path, monkeypatch):
    from iron_jarvis.harness.session_tools import SKIPPED

    platform = build_platform(str(tmp_path / "root"))
    script = _wire(platform, monkeypatch, [
        [ToolCall(id="q1", name="ask_user", arguments={"question": "Which file should I change?"})],
        "I decided myself: nothing to change.",
    ])
    # (A minute's limit: a question nobody settled would wait for the Stop.)
    res = await run_bench(runner=_product(platform), home=platform.config.home,
                          tasks=[_short("bugfix-leap-year", 60.0)])
    rec = res.records[0]
    assert rec["facts"]["asks"]["questions"] == 1, rec["facts"]["asks"]
    # The model was told the user skipped it (never left waiting, never told
    # nobody could be asked).
    told = [str(getattr(m, "content", "") or "") for m in script.calls[1]["messages"]]
    assert any(SKIPPED in t for t in told), told[-3:]
    assert rec["ok_run"] is True and rec["passed"] is False


# --------------------------------------------------------------------------- #
# the time limit is the user's Stop
# --------------------------------------------------------------------------- #


async def test_the_time_limit_is_the_users_stop(tmp_path, monkeypatch):
    """A model call that does not answer: the benchmark presses Stop at the
    time limit and the turn ends well inside the minute it would wait before
    cutting the turn off — the agent's time ran out (graded)."""
    platform = build_platform(str(tmp_path / "root"))
    _wire(platform, monkeypatch, [
        [ToolCall(id="l1", name="list_files", arguments={})],
        ("hang", 300.0),
    ])
    t0 = time.monotonic()
    res = await run_bench(runner=_product(platform), home=platform.config.home,
                          tasks=[_short("bugfix-leap-year", 5.0)])
    took = time.monotonic() - t0
    rec = res.records[0]
    assert took < 45.0, took
    # (Whether the first call had answered by then is the runner's speed, not
    # the claim: the time limit is the agent's either way.)
    assert rec["facts"]["timed_out"] is True
    assert rec["failure_kind"] == "agent" and rec["failure_reason"] == "it reached the time limit"
    assert rec["passed"] is False and rec["unscored_by"] is None and rec["ledger_ok"] is True
    assert any("pressed Stop at the 5 s time limit" in n for n in rec["notes"]), rec["notes"]
    assert not any("cut off" in n for n in rec["notes"]), rec["notes"]


# --------------------------------------------------------------------------- #
# nothing outlives the run
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job objects keep the child")
async def test_a_program_the_session_left_running_is_ended_after_the_run(tmp_path, monkeypatch):
    import psutil

    from iron_jarvis.harness.sessions import session_id_for

    procs.end_all()
    _native_shell(monkeypatch)
    platform = build_platform(str(tmp_path / "root"))
    seen = _spy(monkeypatch)
    child = (
        "import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; "
        "time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL); pathlib.Path('kid.pid').write_text(str(p.pid))"
    )
    _wire(platform, monkeypatch, [
        [ToolCall(id="s1", name="shell", arguments={"command": f'"{sys.executable}" -c "{child}"'})],
        "Started it.",
    ])
    pid = None
    try:
        res = await run_bench(runner=_product(platform), home=platform.config.home,
                              tasks=[TASKS["bugfix-leap-year"]])
        rec = res.records[0]
        pid = int((res.config_dir / "runs" / "bugfix-leap-year" / "work" / "kid.pid").read_text().strip())
        session = session_id_for(seen["starts"][0]["thread"])
        assert procs.kept_jobs(session) == 0, "the coding session's child is still kept"
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not psutil.pid_exists(pid), "a program the coding session left running outlived the run"
        assert any("left running" in n for n in rec["notes"]), rec["notes"]
    finally:
        procs.end_all()
        if pid:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()


async def test_the_sessions_after_idle_reflection_is_taken_back(tmp_path, monkeypatch):
    """The library's after-idle reflection is the session's NEXT idle period
    (a model call no run's ledger holds): the run takes it back."""
    from iron_jarvis.harness import library as L

    monkeypatch.setattr(L, "AUTO_REFLECT", True)
    platform = build_platform(str(tmp_path / "root"))
    seen = _spy(monkeypatch)
    _wire(platform, monkeypatch, ["Nothing to do."])
    await run_bench(runner=_product(platform), home=platform.config.home, tasks=[TASKS["bugfix-leap-year"]])
    thread = seen["starts"][0]["thread"]
    assert L.status_of(thread)["due_at"] is None and L.take_stretch(thread) == []


# --------------------------------------------------------------------------- #
# what the record keeps beyond the lane
# --------------------------------------------------------------------------- #


async def test_the_rounds_patience_waits_are_kept_and_reported(tmp_path, monkeypatch):
    from iron_jarvis.harness import patience as P

    waited: list[float] = []

    async def fake_sleep(seconds):
        waited.append(float(seconds))

    monkeypatch.setattr(P, "WAITS_S", P.DEFAULT_WAITS_S)
    monkeypatch.setattr(P, "SLEEP", fake_sleep)
    platform = build_platform(str(tmp_path / "root"))
    _wire(platform, monkeypatch, [
        ProviderError("scripted API error 529: overloaded_error", status_code=529),
        "Nothing to change.",
    ])
    res = await run_bench(runner=_product(platform), home=platform.config.home,
                          tasks=[TASKS["bugfix-leap-year"]])
    rec = res.records[0]
    assert waited == [5.0]
    pat = rec["extra"]["patience"]
    assert pat["waits"] == 1 and pat["seconds"] == 5.0
    assert pat["list"] == [{"attempt": 2, "of": 5, "wait_s": 5.0, "why": "overloaded", "provider": "scripted"}]
    assert rec["ok_run"] is True and rec["ledger_ok"] is True
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "| 1 wait, 5 s |" in report
    combined = (platform.config.home / "harness-bench" / "report.md").read_text(encoding="utf-8")
    assert "1 wait, 5 s |" in combined


async def test_rounds_set_the_sessions_step_limit_for_the_benchmarks_turns_only(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))
    seen = _spy(monkeypatch)
    _wire(platform, monkeypatch, [[ToolCall(id="l", name="list_files", arguments={})]])
    runner = _product(platform, own_step_limit=False, round_budget=3)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=[TASKS["bugfix-leap-year"]])
    rec = res.records[0]
    assert seen["lanes"][0]["round_budget"] == 3
    # Put back at once (the turn reads it as it starts).
    assert platform.config.build_round_budget == 0
    # A turn that used every step it was given: the agent's own limit, graded.
    assert rec["facts"]["cut_off"] is True and rec["failure_reason"] == "it used every round it had"
    assert rec["extra"]["coding_session"]["step_limit"]["limit"] == 3


# --------------------------------------------------------------------------- #
# the path is part of the identity
# --------------------------------------------------------------------------- #


def test_the_path_is_part_of_the_identity_and_the_lanes_is_unchanged(tmp_path):
    platform = build_platform(str(tmp_path / "root"))
    lane = JarvisRunner(platform, provider="p", model="m").identity()
    # The lane's identity is exactly what it was (no path key): its stored
    # results stay reusable.
    assert "path" not in lane and lane["approval_mode"] == "yolo" and lane["round_budget"] == 60
    assert JarvisRunner(platform, provider="p", model="m", path=PATH_LANE).identity() == lane
    own = JarvisRunner(platform, provider="p", model="m", path=PATH_PRODUCT, own_step_limit=True).identity()
    set_ = JarvisRunner(platform, provider="p", model="m", path=PATH_PRODUCT, round_budget=12).identity()
    assert own["path"] == set_["path"] == PATH_PRODUCT
    assert own["approval_mode"] == set_["approval_mode"] == "approve_for_me"
    assert own["round_budget"] == 1000 and own["no_step_limit"] is True and own["step_limit_from"] == "session"
    assert set_["round_budget"] == 12 and "no_step_limit" not in set_ and set_["step_limit_from"] == "bench"
    assert len(own["product_sha256"]) == 64
    assert "ask_user" in own["tools"] and "ask_user" not in lane["tools"]
    keys = {Configuration("jarvis", "p", "m", h).key for h in (lane, own, set_)}
    assert len(keys) == 3
    # A dialect is forced on the lane only; a candidate layer is the lane's.
    with pytest.raises(ValueError, match="--dialect applies to --path lane"):
        JarvisRunner(platform, provider="p", model="m", path=PATH_PRODUCT, dialect="generic")
    with pytest.raises(ValueError, match="path must be one of"):
        JarvisRunner(platform, provider="p", model="m", path="sideways")
    with pytest.raises(ValueError, match="runs on the lane path only"):
        JarvisRunner(platform, provider="p", model="m", path=PATH_PRODUCT, layer_dir=tmp_path)


def test_the_identity_moves_with_the_product_paths_own_code(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench import runners as R

    platform = build_platform(str(tmp_path / "root"))
    before = _product(platform).identity()
    lane_before = JarvisRunner(platform, provider="p", model="m").identity()
    real = R._module_bytes

    def changed(mod):
        out = real(mod)
        return out + b"# changed" if getattr(mod, "__name__", "").endswith("routes.build") else out

    monkeypatch.setattr(R, "_module_bytes", changed)
    assert _product(platform).identity()["product_sha256"] != before["product_sha256"]
    # The lane never read the build route: its identity stays.
    assert JarvisRunner(platform, provider="p", model="m").identity() == lane_before


async def test_a_lane_result_is_never_reused_for_a_product_run(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "root"))
    _wire(platform, monkeypatch, ["Nothing to change."])
    tasks = [TASKS["bugfix-leap-year"]]
    lane = await run_bench(runner=JarvisRunner(platform, provider=WHO[0], model=WHO[1]),
                           home=platform.config.home, tasks=tasks)
    product = await run_bench(runner=_product(platform), home=platform.config.home, tasks=tasks)
    assert lane.config_dir != product.config_dir
    assert product.records[0]["reused"] is False
    assert "coding_session" in product.records[0]["extra"]
    assert "coding_session" not in lane.records[0]["extra"]
    # The lane setup, run again, reuses ITS result.
    again = await run_bench(runner=JarvisRunner(platform, provider=WHO[0], model=WHO[1]),
                            home=platform.config.home, tasks=tasks)
    assert again.records[0]["reused"] is True


def test_the_command_line_runs_the_product_path_by_default(tmp_path):
    from typer.testing import CliRunner

    from iron_jarvis.harness_bench.cli import bench_app

    root = tmp_path / "bench-root"
    base = ["run", "--runner", "jarvis", "--provider", "custom", "--model", "glm-5.3-flash",
            "--root", str(root), "--dry-run", "--tasks", "bugfix-leap-year"]
    folder = lambda out: next(line for line in out.output.splitlines() if line.startswith("jarvis-"))  # noqa: E731
    default = CliRunner().invoke(bench_app, base)
    assert default.exit_code == 0, default.output
    assert ("Each task runs as a coding session (the product path): \"Ask first\", every card answered "
            "\"Allow once\" by the benchmark, no step limit (the session's own).") in default.output
    product = CliRunner().invoke(bench_app, [*base, "--path", "product"])
    lane = CliRunner().invoke(bench_app, [*base, "--path", "lane"])
    rounds = CliRunner().invoke(bench_app, [*base, "--rounds", "12"])
    assert product.exit_code == lane.exit_code == rounds.exit_code == 0, (product.output, lane.output)
    assert folder(default) == folder(product) != folder(lane)
    assert folder(rounds) != folder(default)
    assert "Each task runs on the bare chat lane (the lane path): \"Don't ask\", at most 60 rounds." in lane.output
    assert "at most 12 steps." in rounds.output
    forced = CliRunner().invoke(bench_app, [*base, "--dialect", "generic"])
    assert forced.exit_code == 2 and "--dialect applies to --path lane" in forced.output
    bad = CliRunner().invoke(bench_app, [*base, "--path", "sideways"])
    assert bad.exit_code == 2 and "--path must be one of: product, lane" in bad.output
    cli = CliRunner().invoke(bench_app, ["run", "--runner", "claude", "--model", "sonnet", "--root", str(root),
                                         "--dry-run", "--path", "lane"])
    assert cli.exit_code == 2 and "--path applies to the jarvis and jarvis-team runners only." in cli.output
    refine = CliRunner().invoke(bench_app, [*base, "--refine", "--path", "product"])
    assert refine.exit_code == 2 and "--refine runs on --path lane" in refine.output


# --------------------------------------------------------------------------- #
# the team, on the product path
# --------------------------------------------------------------------------- #


async def test_a_team_runs_as_a_coding_session_with_its_team_on_the_session(tmp_path, monkeypatch):
    """``team = true`` on the product path: the session's team rides the
    coding session's OWN setup (the way a Build session keeps it, and the way
    ``start_build_turn`` arms it), the planner leads, the piece goes to the
    worker and the change to the reviewer."""
    from iron_jarvis.harness import team as HT
    from iron_jarvis.harness.sessions import get_row
    from iron_jarvis.harness_bench import team as T
    from iron_jarvis.harness_bench import team_report as TRP
    from tests._fakes import bench_team_v1396 as W
    from tests.test_session_team_bench_v1403 import _spec

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    world = W.wire(platform, monkeypatch)
    seen = _spy(monkeypatch)
    systems: list[str] = []

    def script(setup: str, task: str) -> list:
        return [W.agent("p-a1", "code", "Fix recur/rules.py so a monthly series never drifts or skips a month."),
                "The team fixed recur/rules.py."] if task == W.TASK_A else ["Done."]

    real_stream = W.Double.stream

    async def stream(self, *, system, messages, tools, **kw):
        if self.provider == W.PLANNER[0]:
            systems.append(system or "")
        async for frame in real_stream(self, system=system, messages=messages, tools=tools, **kw):
            yield frame

    monkeypatch.setattr(W, "planner_script", script)
    monkeypatch.setattr(W.Double, "stream", stream)
    runner = T.JarvisTeamRunner(platform, team=_spec(tmp_path, True), round_budget=12,
                                path=PATH_PRODUCT, own_step_limit=True)
    assert runner.identity()["path"] == PATH_PRODUCT
    res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(), names=[W.TASK_A], seed=0)
    (rec,) = res.records
    assert rec["ok_run"] is True, rec.get("error")
    assert rec["passed"] is True
    # The session's own setup carries the team (not a scope the bench armed).
    row = get_row(platform.engine, seen["starts"][0]["thread"])
    setup = json.loads(row.setup_json)
    assert HT.from_setup(setup) == HT.SessionTeam(workers=("glm",), reviewer="codex")
    assert systems and HT.TEAM_BRIEF_HEADER in systems[0]
    by_member = {(h["member"], h["role"]) for h in rec["helpers"]}
    assert ("glm", "code") in by_member and ("codex", "review") in by_member
    assert TRP.handed_to_a_member(rec)
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "jarvis-team · lead (locked) · coding session" in report
    assert world.calls


def test_a_team_is_answered_against_the_planner_alone_on_its_own_path():
    """The report's answer pairs a team with the planner alone on the SAME
    path first (a team run as coding sessions is never answered against the
    bare lane when a coding-session row is there), and says so when only the
    other path's row exists."""
    from iron_jarvis.harness_bench import team_report as TRP

    team = {"runner": "jarvis-team", "provider": "p", "model": "m", "effort": "medium", "version": "1",
            "key": "team0000000000", "harness": {"team": {}, "path": PATH_PRODUCT}}
    lane = {"runner": "jarvis", "provider": "p", "model": "m", "effort": "medium", "version": "1",
            "key": "lane0000000000", "created_at": "2026-11-02T10:00:00Z", "harness": {}}
    product = {**lane, "key": "prod0000000000", "created_at": "2026-11-01T10:00:00Z",
               "harness": {"path": PATH_PRODUCT}}
    recs = [{"task_id": "t", "passed": True}]
    # The lane row is newer and shares as many runs: the product row still wins.
    found = TRP.find_alone(team, recs, [(lane, recs), (product, recs)])
    assert found is not None and found[0]["key"] == product["key"]
    assert TRP.run_path(lane) == PATH_LANE and TRP.run_path(product) == PATH_PRODUCT
    assert TRP.run_path({"runner": "claude"}) == ""
    assert TRP.compares_line(team, product).endswith("Iron Jarvis 1).")
    only_lane = TRP.compares_line(team, lane)
    assert "which ran each task on the bare chat lane (this team as a coding session)" in only_lane
    assert TRP.runner_label(lane) == "jarvis" and TRP.runner_label(product) == "jarvis · coding session"


async def test_a_coding_session_sees_no_path_to_the_bench_root(tmp_path, monkeypatch):
    """Everything the bench guarantees stays: the coding session's prompt and
    its commands' environment name no path to the bench root or its state
    home (``hidden_environ`` around the whole turn), and the work is moved
    home afterwards."""
    import os

    from iron_jarvis.harness_bench import bench as bench_mod
    from iron_jarvis.sandbox.native import build_env

    parent = tmp_path / "agent-temp"
    monkeypatch.setattr(bench_mod, "AGENT_RUNS_PARENT", parent, raising=False)
    platform = build_platform(str(tmp_path / "root"))
    home = Path(platform.config.home)
    monkeypatch.setenv("PWD", str(home / "harness-bench"))
    monkeypatch.setenv("OLDPWD", str(home))
    seen: dict = {}

    def _mentions(value: str, path: Path) -> bool:
        return str(path).replace("\\", "/").lower().rstrip("/") in str(value).replace("\\", "/").lower()

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen["system"] = system
        seen["messages"] = " | ".join(str(getattr(m, "content", m)) for m in messages)
        seen["env"] = build_env()
        resp = LLMResponse(text="Done.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    monkeypatch.setattr(platform.router, "stream", stream)
    res = await run_bench(runner=_product(platform), home=home, tasks=[TASKS["bugfix-leap-year"]])
    assert seen, "the turn never reached the model"
    bench_root = home / "harness-bench"
    assert not _mentions(seen["messages"], bench_root) and not _mentions(seen["messages"], home)
    assert not _mentions(seen["system"], bench_root) and not _mentions(seen["system"], home)
    leaked = sorted(k for k, v in seen["env"].items() if _mentions(v, home))
    assert leaked == [], f"the coding session's environment names the bench root in {leaked}"
    assert _mentions(seen["messages"], parent / "ij-bench-runs")
    assert os.environ.get("PWD") == str(home / "harness-bench"), "the bench's own environment was not restored"
    assert (res.config_dir / "runs" / "bugfix-leap-year" / "work").is_dir()
    assert res.records[0]["extra"]["coding_session"]["line_saved"] is True
