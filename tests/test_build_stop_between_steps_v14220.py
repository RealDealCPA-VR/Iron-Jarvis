"""v1.422.0 review (Wave 2 "Never lose work", B2): a step the reply has NOT
started yet when the user stops it is never started.

THE DEFECT (found in review). ``harness/stall.run_step`` races a running step
against the turn's Stop and cancels it after ``STOP_GRACE_S``. But a round's
writers run one after another (``chat_turn._run_tool_round``), and nothing
looked for a Stop BETWEEN them: when the user stopped the reply while the
first call ran, that call was cancelled — and the NEXT call of the same round
was then STARTED anyway. ``run_step`` found the Stop already set, gave the new
step its three seconds of grace, and only then cancelled it: a file write (or
an edit, a commit, a ``rm``) finishes well inside three seconds, so it ran to
the end AFTER the user pressed Stop, and a command ran for three seconds.

Now a step whose turn is already stopped when it would begin is not started
at all: the registry is never called (no ledger row: nothing ran), and the
model's result for it says the user stopped the reply before it started.

Driven through the REAL app factory, the real stream lane, the real tool
registry and a real command (``DOCKER_HOST`` pointed nowhere, as on CI); only
the model is a scripted double.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import process as PR
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from sqlmodel import select
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_build_stall_v1392 import _alive, _hang, _pid

GONE_WITHIN_S = 20


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    harness = folder / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


def _rows(app, sid: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all()
                if r.session_id == f"build:{sid}"]
        for r in rows:
            db.expunge(r)
        return rows


async def test_the_next_call_of_a_stopped_round_is_never_started(app, work, tmp_path):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    hang_pid = tmp_path / "hang.pid"
    after = work / "after.txt"
    script = wire(app, Script([
        [
            ToolCall(id="c1", name="Bash", arguments={"command": _hang(hang_pid)}),
            ToolCall(id="c2", name="Write", arguments={"file_path": str(after), "content": "written after Stop"}),
        ],
        "Never asked.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "slow thing, then a file", "turn_id": "bt-between"},
                                         drop=drop, timeout=120))
    hung = 0
    try:
        hung = await _pid(hang_pid)
        status, _ = await asgi(app, "POST", "/chat/turns/bt-between/stop", {})
        assert status == 200
        assert await until(lambda: not _alive(hung), GONE_WITHIN_S), "Stop left the running command alive"
        lg = turn_log(app, "bt-between")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
        PR.end_session(f"build:{sid}")
    assert not hung or not _alive(hung)
    # THE CALL AFTER THE STOPPED ONE NEVER RAN: no file, no ledger row.
    assert not after.exists(), "a write the user stopped before it began was carried out"
    assert not [r for r in _rows(app, sid) if r.tool == "write_file"]
    assert len(script.calls) == 1
    # Its step says so (never that it worked).
    frames = turn_frames(app, "bt-between")
    done = [d for e, d in frames if e == "tool_call" and d.get("id") == "c2" and d.get("status") == "finished"]
    assert done and done[-1].get("ok") is False, done
    assert "before it started" in str(done[-1].get("output") or "")


async def test_run_step_never_starts_a_step_of_a_turn_already_stopped():
    from iron_jarvis.core.turns import TURNS

    handle = TURNS.register("bt-pre")
    ran = asyncio.Event()

    async def make():
        ran.set()
        return "ran"

    try:
        TURNS.stop("bt-pre")
        out = await asyncio.wait_for(stall.run_step(
            "bt-pre", "c1", "write_file", make, lambda: "step-words",
            turn_stopped=lambda: "turn-words", not_started=lambda: "not-started",
        ), 10)
        assert out == "not-started"
        await asyncio.sleep(0)
        assert not ran.is_set(), "the step was started after the turn was stopped"
        assert stall.running_steps("bt-pre") == []
    finally:
        TURNS.release("bt-pre", handle)


async def test_a_step_that_waits_on_a_person_still_starts_after_a_stop():
    """``race=False`` (``ask_user``): it ends on Stop in its own words — it is
    started as before, never answered "not started" by the race."""
    from iron_jarvis.core.turns import TURNS

    handle = TURNS.register("bt-pre-person")

    async def make():
        return "own words"

    try:
        TURNS.stop("bt-pre-person")
        out = await asyncio.wait_for(stall.run_step(
            "bt-pre-person", "c1", "ask_user", make, lambda: "step-words",
            turn_stopped=lambda: "turn-words", not_started=lambda: "not-started", race=False,
        ), 10)
        assert out == "own words"
    finally:
        TURNS.release("bt-pre-person", handle)
