"""v1.391.0 (the Phase 1 review's open item): a reply a CRASH cut off keeps
its Timeline.

``harness/sessions.reconcile_interrupted_turns`` wrote the cut-off reply's
line from its stored frames with the words only — no steps, no model calls,
no ``timing`` — although the frames (``ChatTurnRecord.frames_json``) carry
every offset the reply's clock stamped while it ran. The Timeline then said
"Iron Jarvis stopped while answering, so this reply's times were not kept".
The same writer now folds the stored frames through the live writer's own
``_ReplyWatch`` on a clock that reads each frame's offset
(``routes/build.replayed_reply_line``).

Pinned with a REAL crashed turn (the real app, the real lane, a scripted
model held mid-reply, the process's last record kept as it was). The line it
writes is the dashboard's fixture (``build-cutoff-timeline-v1391.json``,
regenerate with ``IJ_REGEN_CUTOFF_TIMELINE=1``), so the page's Timeline is
proven against what the daemon really writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.build import frame_offset, replayed_reply_line
from iron_jarvis.harness import sessions as S
from tests.test_build_resume_v1391 import _cut_off_mid_reply, _reboot
from tests.test_build_sessions_v1367 import messages_of, new_session

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-cutoff-timeline-v1391.json"


def _cut_line(app, sid: str, turn_id: str = "bt-1") -> dict:
    return next(m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == turn_id)


async def test_a_reply_a_crash_cut_off_keeps_its_steps_calls_and_times(tmp_path):
    home = str(tmp_path / "home")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".ironjarvis" / "harness").mkdir(parents=True)
    (work / ".ironjarvis" / "harness" / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid, stop="crash")
    assert not any(m["role"] == "assistant" for m in messages_of(first, sid)), "the crash wrote no reply line"
    with session_scope(first.state.platform.engine) as db:
        stored = json.loads(db.get(ChatTurnRecord, "bt-1").frames_json)
    assert any(frame_offset(e, json.loads(d)) for e, d in stored), "anti-vacuity: the frames carry offsets"

    second = _reboot(home)
    line = _cut_line(second, sid)
    # Still the cut-off reply it always was…
    assert line["interrupted"] is True and line["error"] == durable_turns.INTERRUPTED_DETAIL
    # …and now with what ran, on the reply's own clock.
    steps = line["steps"]
    assert [(s["name"], s["ok"]) for s in steps] == [("write_file", True), ("write_file", True)]
    assert all(isinstance(s["start"], int) and isinstance(s["ms"], int) for s in steps)
    assert steps[0]["start"] <= steps[1]["start"]
    assert [s.get("target") for s in steps] == ["one.txt", "two.txt"]
    calls = line["calls"]
    finished = [c for c in calls if "state" not in c]
    assert len(finished) == 2 and all(c["provider"] == "anthropic" for c in finished)
    assert all("usage" in c for c in finished)
    # The model call the crash cut off is kept as stopped, never billed.
    open_call = [c for c in calls if c.get("state") == "stopped"]
    assert len(open_call) == 1 and "usage" not in open_call[0]
    timing = line["timing"]
    assert set(timing) == {"startedAt", "firstTokenAt", "endedAt", "ms"}
    assert timing["ms"] >= max(s["start"] + s["ms"] for s in steps)
    assert timing["endedAt"] - timing["startedAt"] == timing["ms"]
    # Every bar lies inside the reply.
    for c in calls:
        assert 0 <= c["start"] and c["start"] + c["ms"] <= timing["ms"]

    if os.environ.get("IJ_REGEN_CUTOFF_TIMELINE"):
        FIXTURE.write_text(json.dumps({"line": line}, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                           newline="\n")
    kept = json.loads(FIXTURE.read_text(encoding="utf-8"))["line"]
    # The fixture holds the same SHAPE the daemon writes (times differ per run).
    assert sorted(kept) == sorted(line), "regenerate with IJ_REGEN_CUTOFF_TIMELINE=1"
    assert [s["name"] for s in kept["steps"]] == [s["name"] for s in steps]
    assert len(kept["calls"]) == len(calls)


def test_a_reply_stored_before_the_timeline_keeps_the_line_it_always_had(tmp_path):
    """No offsets in its frames (a record an older app stored): the words,
    interrupted — never a timeline made up of zeros."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    S.append_lines(engine, row.id, [{"role": "user", "content": "go", "turnId": "old-1"}])
    frames = [
        ["round", json.dumps({"round": 0})],
        ["token", json.dumps({"text": "half an answ"})],
        ["tool_call", json.dumps({"id": "c1", "name": "read_file", "status": "started"})],
    ]
    with session_scope(engine) as db:
        db.add(ChatTurnRecord(id="old-1", thread_id=row.id, status="running", frames_json=json.dumps(frames)))
        db.commit()
    assert replayed_reply_line("old-1", frames, started_at=0.0) is None
    durable_turns.reconcile_interrupted_turns(engine)
    S.reconcile_interrupted_turns(engine)
    line = _cut_line(app, row.id, "old-1")
    assert {k: v for k, v in line.items() if k != "at"} == {
        "role": "assistant", "content": "half an answ", "turnId": "old-1",
        "interrupted": True, "error": durable_turns.INTERRUPTED_DETAIL,
    }


def test_the_offsets_are_read_where_the_clock_stamped_them():
    assert frame_offset("round", {"round": 2, "start": 1200}) == 1200
    assert frame_offset("tool_call", {"status": "started", "start": 50}) == 50
    assert frame_offset("tool_call", {"status": "finished", "start": 50, "ms": 30}) == 80
    assert frame_offset("model_call", {"start": 10, "ms": 900}) == 910
    assert frame_offset("helper", {"start": 5, "end": 70}) == 70
    assert frame_offset("helper", {"start": 5, "step": {"status": "finished", "start": 9, "ms": 4}}) == 13
    assert frame_offset("token", {"text": "x"}) is None
    assert frame_offset("round", {"start": True}) is None
    assert frame_offset("round", {"start": -3}) is None
