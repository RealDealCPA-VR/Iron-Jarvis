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
