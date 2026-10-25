"""v1.384.0 REVIEW — long Build replies: what the doer's pins did not reach.

* A reply line COUNTS every step it took. It kept only the first
  ``MAX_STEPS`` (60), so a long reply's work line under-reported ("read 54
  files" for 70) — and with no step limit, long replies are the default now.
  The first ``MAX_STEPS`` keep their detail as before; the rest are kept
  compact (name, outcome, time, target — never their detail), up to
  ``MAX_COUNTED_STEPS``.
* A WHOLE tagged replay of a turn that is still running says when the turn
  STARTED (``resume.started_at``, ms), so a page that comes back after a
  reload counts "Working for" from the turn's start, not from the rejoin.
* The harness guidance is TRUE for every dialect and mode: a planning turn
  (no editors) is never told to make changes one at a time, and no dialect
  is told its reads run at the same time unless they do (GPT reads through
  exec_command, which runs one call after another).
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, new_session, open_turn, wire


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    for i in range(80):
        (folder / f"f{i}.py").write_text(f"x = {i}\n", encoding="utf-8")
    return folder


async def test_a_long_replys_line_counts_every_step_it_took(app, work):
    """OLD: the line kept 60 steps of 75, so its work line said 60."""
    sid = new_session(app, work)["id"]
    moves = [[ToolCall(id=f"r{i}", name="Read", arguments={"file_path": f"f{i}.py"})] for i in range(75)]
    wire(app, Script(moves))
    status, raw = await open_turn(app, sid, {"text": "read them all", "turn_id": "rv-steps"}, timeout=120)
    assert status == 200, raw
    assert frames_of(raw)[-1][0] == "done"
    steps = messages_of(app, sid)[-1]["steps"]
    assert len(steps) == 75, "every step is on the line"
    assert all(s["name"] == "read_file" and s["ok"] is True for s in steps)
    # The first MAX_STEPS keep their detail (as before); later ones are kept
    # compact — but each still says what it was done to.
    assert all("detail" in s for s in steps[: build_routes.MAX_STEPS])
    assert not any("detail" in s for s in steps[build_routes.MAX_STEPS:])
    assert steps[74]["target"] == "f74.py"


def test_the_line_keeps_at_most_max_counted_steps():
    w = build_routes._ReplyWatch()
    n = build_routes.MAX_COUNTED_STEPS + 25
    for i in range(n):
        w.see("tool_call", json.dumps({"id": f"c{i}", "name": "grep", "status": "started", "args": {"pattern": "x"}}))
        w.see("tool_call", json.dumps({"id": f"c{i}", "name": "grep", "status": "finished", "ok": True,
                                       "detail": {"input": "x", "output": "y"}}))
    assert len(w.steps) == build_routes.MAX_COUNTED_STEPS
    assert sum(1 for s in w.steps if "detail" in s) == build_routes.MAX_STEPS
    # Bounded in size too: a compact step is a few dozen bytes.
    compact = json.dumps(w.steps[build_routes.MAX_STEPS:])
    assert len(compact) < 80 * (build_routes.MAX_COUNTED_STEPS - build_routes.MAX_STEPS)



# --------------------------------------------------------------------------- #
# a reload's whole replay says when the turn started
# --------------------------------------------------------------------------- #


def test_a_whole_replay_of_a_running_turn_says_when_it_started():
    lg = dt.TurnLog("t")
    lg.add(dt.sse("token", {"text": "abc"}))
    started_ms = int(lg.started_at.timestamp() * 1000)
    head = lg.tagged_head(None)
    first = head.split("\n\n", 1)[0]
    data = json.loads(first.split("data: ", 1)[1])
    assert data == {"resumed": False, "running": True, "started_at": started_ms}
    # A position the log holds: the page has its own start already.
    data = json.loads(lg.tagged_head("0:1").split("\n\n", 1)[0].split("data: ", 1)[1])
    assert data == {"resumed": True, "running": True}
    # A finished turn shows no clock: no start.
    lg.status = "done"
    data = json.loads(lg.tagged_head(None).split("\n\n", 1)[0].split("data: ", 1)[1])
    assert data == {"resumed": False, "running": False}
    # A record replay (a finished turn this process no longer holds): as before.
    assert json.loads(dt.tagged_replay([["token", '{"text": "a"}']]).split("\n\n", 1)[0].split("data: ", 1)[1]) == {
        "resumed": False, "running": False,
    }


# --------------------------------------------------------------------------- #
# the guidance is TRUE for every dialect and every mode
# --------------------------------------------------------------------------- #

from iron_jarvis.daemon import chat_turn  # noqa: E402
from iron_jarvis.harness import components as C  # noqa: E402
from iron_jarvis.harness import dialects as D  # noqa: E402

BATCH = "together in ONE reply"
INCREMENTAL = "make a change and check it"
NOT_ALL = "before your first change"


def _plan_text() -> str:
    return C.plan_mode_text(exit_name="ExitPlanMode", plan_name="TodoWrite")


@pytest.mark.parametrize("dialect", sorted(D.DIALECTS))
def test_a_planning_turn_is_never_told_to_make_changes(dialect):
    """OLD: plan mode (no editors) was told "make a change and check it
    before you read for the next one" and "do not read the whole codebase
    before your first change" — beside "Plan only: do not change files"."""
    text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id=dialect, mode=_plan_text()))
    assert "Plan only" in text
    assert INCREMENTAL not in text and NOT_ALL not in text
    assert BATCH in text, "reading several files in one reply still holds while planning"
    # An act-mode turn keeps all three.
    act = C.render(C.compose(dialect_section="Your tools: x.", dialect_id=dialect))
    assert INCREMENTAL in act and NOT_ALL in act and BATCH in act


def _reads_run_together(dialect_id: str) -> bool:
    """Does this dialect read and search through tools the lane runs at the
    same time (``chat_turn.CONCURRENT_READ_TOOLS``)? The generic dialect
    shows Jarvis's own tools (read_file, grep, glob); a dialect with no
    file reader of its own reads through a COMMAND (GPT's exec_command:
    shell / process_start), and commands run one after another."""
    dialect = D.DIALECTS[dialect_id]
    if not dialect.tools:
        return True
    readers = {t.targets[0] for t in dialect.tools if t.targets} & {"read_file", "grep", "glob"}
    return bool(readers) and readers <= set(chat_turn.CONCURRENT_READ_TOOLS)


def test_the_guidance_claims_nothing_a_dialect_cannot_keep():
    """OLD: every dialect was told its independent reads "run at the same
    time" — the GPT dialect reads through exec_command (shell /
    process_start), which runs one call after another."""
    for dialect in sorted(D.DIALECTS):
        text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id=dialect))
        if "run at the same time" in text.split("## Tools\n", 1)[1].split("\n## ", 1)[0]:
            assert _reads_run_together(dialect), f"{dialect}: told its reads run together; they do not"
