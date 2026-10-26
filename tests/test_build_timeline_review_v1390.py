"""v1.390.0 REVIEW of the Timeline (the long-jobs plan's Phase 1): defects the
first cut left, each pinned red on the merged doer branch first.

1. THE REPLY'S WALL-CLOCK WINDOW. The first cut moved the reply line's
   ``timing.endedAt`` (and ``firstTokenAt``) onto the reply's precise clock
   (the wall-clock start plus a ``perf_counter`` offset). But ``timing`` is an
   EPOCH window that another reader compares with wall-clock rows: a coding
   reply's "what this reply changed" (``paneTurnWindow`` → ``POST
   /chat/changes`` with ``since``/``until``) reads the session's journal rows,
   which are stamped by the wall clock. When the wall clock steps forward
   while a reply works (a time sync after the PC wakes, a corrected clock),
   every change after the step falls outside the window and the reply's own
   Changes row loses it. ``timing`` keeps the wall clock again; the precise
   length rides beside it as ``timing.ms`` (the Timeline's axis).
2. ``timing.ms`` also tells a reply written since the Timeline from an older
   one, so a reply that ended before it did anything is never said to have
   been "made before Iron Jarvis kept the time of each step".
3. The finish check's own test run (``ij-finish-N``) and a project check
   (``ij-check-N``) are steps Iron Jarvis ran, not the model: the reply line
   and the live frames mark them (``check``), so the Timeline tells them
   apart from the reply's own work.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import time
from pathlib import Path

import pytest

from iron_jarvis.core import ids as _ids
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import checkers as CK
from iron_jarvis.harness import finish as F
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_timeline_v1390 import Timed, _reply, _turn, new_session, wire
from tests.test_chat_turn_stop_v1241 import _asgi_post


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "util.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    return folder


#: How far the wall clock steps forward in the middle of the reply.
STEP_S = 6.0


def _iso(ms: int) -> str:
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).isoformat()


def _step_the_wall_clock(monkeypatch) -> list[float]:
    """The wall clock (``time.time`` and the database's ``utcnow``) reads
    ``jump[0]`` seconds ahead once a test sets it — a forward step, as a time
    sync makes. The precise clocks (``perf_counter``, ``monotonic``) do not
    move: they never do."""
    jump = [0.0]
    real_time = time.time
    real_datetime = _ids.datetime

    class _Stepped(real_datetime):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return real_datetime.now(tz) + _dt.timedelta(seconds=jump[0])

    monkeypatch.setattr(_ids, "datetime", _Stepped)
    monkeypatch.setattr(time, "time", lambda: real_time() + jump[0])
    return jump


# --------------------------------------------------------------------------- #
# 1. the reply's window is the wall clock's
# --------------------------------------------------------------------------- #


async def test_the_reply_window_still_holds_its_change_after_the_wall_clock_steps_forward(app, work, monkeypatch):
    sid = new_session(app, work, approval_mode="yolo")
    jump = _step_the_wall_clock(monkeypatch)
    tool = app.state.platform.registry.get("write_file")
    real = tool.execute

    async def stepped(*a, **kw):
        # The clock steps while the reply works — before the write's journal
        # row is stamped.
        jump[0] = STEP_S
        return await real(*a, **kw)

    monkeypatch.setattr(tool, "execute", stepped)
    wire(app, Timed([
        [ToolCall(id="w1", name="Write", arguments={"file_path": "made.py", "content": "y = 2\n"})],
        "Made made.py.",
    ]))
    await _turn(app, sid, "make made.py", "tlr-step")
    line = _reply(app, sid)
    timing = line["timing"]
    # The window the coding session asks with (paneTurnWindow → turnWindow):
    # the reply's own timing, as epoch ms, sent as ISO stamps.
    status, body = await _asgi_post(app, "/chat/changes", {
        "since": _iso(timing["startedAt"]),
        "until": _iso(timing["endedAt"]),
        "paths": [str(work / "made.py")],
        "session_id": f"build:{sid}",
    })
    assert status == 200, body
    names = [Path(c["path"]).name for c in body.get("changes") or []]
    assert names == ["made.py"], (body, timing)
    # The reply's own (precise) length rides beside the wall-clock window:
    # the reply took well under a second of real time, whatever the wall
    # clock says.
    assert isinstance(timing["ms"], int) and 0 < timing["ms"] < STEP_S * 1000
    assert timing["endedAt"] - timing["startedAt"] >= STEP_S * 1000 - 50


async def test_the_first_word_is_stamped_by_the_wall_clock_too(app, work, monkeypatch):
    sid = new_session(app, work)
    jump = _step_the_wall_clock(monkeypatch)
    tool = app.state.platform.registry.get("read_file")
    real = tool.execute

    async def stepped(*a, **kw):
        jump[0] = STEP_S
        return await real(*a, **kw)

    monkeypatch.setattr(tool, "execute", stepped)
    wire(app, Timed([[ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})], "Read it."]))
    await _turn(app, sid, "read app.py", "tlr-first")
    timing = _reply(app, sid)["timing"]
    # The first word came after the step: on the wall clock it is past it.
    assert timing["firstTokenAt"] - timing["startedAt"] >= STEP_S * 1000 - 50, timing
    assert timing["startedAt"] <= timing["firstTokenAt"] <= timing["endedAt"]


async def test_every_bar_lies_inside_the_replys_own_length(app, work, monkeypatch):
    sid = new_session(app, work)
    wire(app, Timed([
        [ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"}),
         ToolCall(id="r2", name="Read", arguments={"file_path": "util.py"})],
        "Read both.",
    ]))
    await _turn(app, sid, "read both", "tlr-inside")
    line = _reply(app, sid)
    length = line["timing"]["ms"]
    for bar in [*line["calls"], *line["steps"]]:
        assert 0 <= bar["start"] and bar["start"] + bar["ms"] <= length, (bar, length)


# --------------------------------------------------------------------------- #
# 3. the checks Iron Jarvis ran are told apart from the model's own work
# --------------------------------------------------------------------------- #

TEST_CMD = "python -m unittest -q test_mini"
PASSING = (
    "import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n"
    "    def test_x(self):\n        self.assertEqual(app.X, 2)\n"
)


async def test_the_finish_checks_own_test_run_is_marked_on_the_line_and_the_frames(app, work):
    (work / "app.py").write_text("X = 1\n", encoding="utf-8")
    (work / "test_mini.py").write_text(PASSING, encoding="utf-8")
    sid = new_session(app, work, approval_mode="yolo")
    wire(app, Timed([
        [ToolCall(id="w1", name="Write", arguments={"file_path": "app.py", "content": "X = 2\n"})],
        [ToolCall(id="b1", name="Bash", arguments={"command": TEST_CMD})],
        "Set X to 2; the tests pass.",
        "1. met: done.",
    ]))
    frames = await _turn(app, sid, "set X to 2 in app.py and run the tests", "tlr-fin")
    line = _reply(app, sid)
    ids = [d["id"] for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert any(str(i).startswith(F.CALL_ID_PREFIX) for i in ids), ids
    # The line: the model's own two steps unmarked, the check's run marked.
    marks = [(s["name"], s.get("check")) for s in line["steps"]]
    assert marks == [("write_file", None), ("shell", None), ("shell", "finish")], marks
    # The live frames say the same (started and finished), so the running
    # Timeline and the saved one agree.
    for e, d in frames:
        if e == "tool_call":
            want = "finish" if str(d["id"]).startswith(F.CALL_ID_PREFIX) else None
            assert d.get("check") == want, d


def test_a_project_checks_run_is_marked_too():
    from iron_jarvis.daemon.routes.build import _ReplyWatch

    watch = _ReplyWatch()
    cid = f"{CK.CALL_ID_PREFIX}1"
    started = watch.see("tool_call", json.dumps({"id": cid, "name": "shell", "status": "started",
                                                 "args": {"command": "ruff check made.py"}}))
    finished = watch.see("tool_call", json.dumps({"id": cid, "name": "shell", "status": "finished", "ok": True}))
    own = watch.see("tool_call", json.dumps({"id": "c9", "name": "shell", "status": "started",
                                             "args": {"command": "ruff check made.py"}}))
    assert started["check"] == "project" and finished["check"] == "project"
    assert "check" not in own
    assert watch.steps[0]["check"] == "project"


async def test_a_reply_that_never_got_going_still_carries_its_length(app, work, monkeypatch):
    """An error before the model was first asked leaves a reply line with no
    model call and no step — and ``timing.ms`` says it is a line written
    since the Timeline (the page then says it ended before any work, never
    that it was made before the Timeline)."""
    sid = new_session(app, work)

    async def broken(*_a, **_kw):
        raise RuntimeError("the folder could not be read")

    from iron_jarvis.daemon.routes import chat as CR

    monkeypatch.setattr(CR, "_gather_grounding", broken)
    wire(app, Timed(["never asked"]))
    from tests.test_build_sessions_v1367 import frames_of, open_turn

    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "tlr-early"}, timeout=60)
    assert status == 200, raw
    assert frames_of(raw)[-1][0] == "error", frames_of(raw)[-3:]
    line = _reply(app, sid)
    assert "calls" not in line and not any("start" in s for s in line.get("steps") or [])
    assert isinstance(line["timing"].get("ms"), int)
    await asyncio.sleep(0)


def test_a_reply_a_crash_cut_off_is_rebuilt_with_no_times(app, work):
    """The boot's rebuild of a reply cut off mid-way keeps what was streamed,
    marked interrupted, and NO ``timing`` — the one line the reply's own
    writer did not write. The Timeline reads exactly that to say "Iron Jarvis
    stopped while answering" (lib/buildTimeline ``noTimelineReason``), never
    "made before Iron Jarvis kept the time of each step"."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatTurnRecord
    from iron_jarvis.harness import sessions as S

    sid = new_session(app, work)
    S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "go", "turnId": "tlr-cut"}])
    frames = [
        ["round", json.dumps({"round": 0, "start": 4})],
        ["token", json.dumps({"text": "Half"})],
        ["model_call", json.dumps({"ms": 5, "provider": "p", "model": "m", "usage": {}, "start": 4})],
    ]
    with session_scope(app.state.platform.engine) as db:
        db.add(ChatTurnRecord(id="tlr-cut", thread_id=sid, status="interrupted", frames_json=json.dumps(frames)))
        db.commit()
    assert S.reconcile_interrupted_turns(app.state.platform.engine) == 1
    line = _reply(app, sid)
    assert line["interrupted"] is True and "timing" not in line
