"""v1.374.0 review (IJ harness part 2, Phase 3): COMPACT, held to the record.

The summary Compact keeps is read back on every later turn as an account of
the conversation, so what may corroborate it is only what THOSE TURNS did:

* a path only ANOTHER session (or the chat) touched is removed;
* a path only a REWOUND turn touched is removed (its rows stay in the
  ledger, its lines are gone — the summary would describe work the
  conversation no longer holds);
* a path only a turn AFTER the coverage touched is removed (the summarizer
  never saw that turn; that turn is still sent as it was).

Driven through the REAL app factory and routes; only the model (the router's
``complete``) is the review file's scripted double.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import compact as C
from iron_jarvis.harness import sessions as S
from tests.test_build_compact_v1374 import _PADDING, WINDOW, Writer, wire_writer
from tests.test_build_sessions_v1367 import CLAUDE, asgi, messages_of, new_session, setup_of


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.model_context_windows = {f"{CLAUDE[0]}::{CLAUDE[1]}": WINDOW}
    return a


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


#: Every line and ledger row below is stamped on this clock, minutes apart,
#: so which turn a row belongs to never rides on a 15.6 ms clock tick.
BASE = utcnow() - timedelta(hours=3)


def _at(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _row(engine, session_id: str, path: str, minutes: float) -> None:
    with session_scope(engine) as db:
        db.add(ToolInvocation(
            session_id=session_id, agent_run_id="chat", tool="read_file",
            args_json=json.dumps({"path": path}), reversibility="readonly",
            created_at=BASE + timedelta(minutes=minutes),
        ))
        db.commit()


def _turn(engine, sid: str, tid: str, minutes: float, q: str = "", a: str = "") -> None:
    S.append_lines(engine, sid, [
        {"role": "user", "content": q or f"Question for {tid}?", "turnId": tid, "at": _at(minutes)},
        {"role": "assistant", "content": (a or f"The answer for {tid}.") + _PADDING, "turnId": tid,
         "at": _at(minutes + 1)},
    ])


def _summary(*facts: str) -> str:
    return "\n".join(["GOAL: Keep the build tidy.", "FACTS:", "The answer for t1 was given.", *facts])


async def _compact(app, sid: str, text: str) -> dict:
    wire_writer(app, Writer(text=text))
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/compact", {})
    assert status == 200, body
    return body


# --------------------------------------------------------------------------- #
# the ledger that may back a summary is the covered turns' own
# --------------------------------------------------------------------------- #


async def test_a_path_only_another_session_touched_is_removed(app, work):
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    other = new_session(app, work)["id"]
    for k in range(6):
        _turn(engine, sid, f"t{k + 1}", 10 * k)
    # Inside t2's own time, but ANOTHER session's ledger (and the chat's).
    _row(engine, S.session_id_for(other), "src/other_session_only.py", 10.5)
    _row(engine, "chat", "src/chat_only.py", 10.5)
    # ...and one this session really read in t2: the control.
    _row(engine, S.session_id_for(sid), "src/this_session_read.py", 10.5)
    body = await _compact(app, sid, _summary(
        "src/other_session_only.py was read.",
        "src/chat_only.py was read.",
        "src/this_session_read.py was read.",
    ))
    assert "src/other_session_only.py" in body["stripped_claims"]
    assert "src/chat_only.py" in body["stripped_claims"]
    assert "src/this_session_read.py" in body["summary"]
    assert "other_session_only" not in body["summary"] and "chat_only" not in body["summary"]


async def test_a_path_only_a_rewound_turn_touched_is_removed(app, work):
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    for k in range(3):
        _turn(engine, sid, f"t{k + 1}", 10 * k)
    # t4 READ a file, then the user rewound t4: its lines are gone, its
    # ledger row is not (a read has nothing to put back).
    _turn(engine, sid, "t4", 30)
    _row(engine, S.session_id_for(sid), "src/rewound_only.py", 30.5)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t4"})
    assert status == 200 and body["rewound"] is True, body
    assert not any(m.get("turnId") == "t4" for m in messages_of(app, sid))
    for k in range(5, 11):
        _turn(engine, sid, f"t{k}", 10 * k)
    body = await _compact(app, sid, _summary("src/rewound_only.py was read."))
    assert "src/rewound_only.py" in body["stripped_claims"], body
    assert "rewound_only" not in body["summary"]
    assert "rewound_only" not in setup_of(app, sid)["compaction"]["summary"]


async def test_a_path_only_a_turn_after_the_coverage_touched_is_removed(app, work):
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    for k in range(6):
        _turn(engine, sid, f"t{k + 1}", 10 * k)
    # t5 is one of the newest lines: never covered, never shown to the
    # summarizer — it is still sent as it was.
    _row(engine, S.session_id_for(sid), "src/later_only.py", 40.5)
    body = await _compact(app, sid, _summary("src/later_only.py was read."))
    assert body["through_turn"] == "t3"
    assert "src/later_only.py" in body["stripped_claims"]
    assert "later_only" not in body["summary"]


def test_each_covered_turn_runs_from_its_first_line_to_its_last():
    lines = [
        {"role": "user", "content": "q1", "turnId": "t1", "at": _at(0)},
        {"role": "user", "content": "note", "turnId": "t1", "steer": True, "at": _at(3)},
        {"role": "assistant", "content": "a1", "turnId": "t1", "at": _at(3)},
        {"role": "user", "content": "q2", "turnId": "t2", "at": _at(10)},
        {"role": "assistant", "content": "a2", "turnId": "t2", "at": _at(12)},
        {"role": "assistant", "content": "no stamp", "turnId": "t3"},
    ]
    naive = BASE.replace(tzinfo=None)  # the ledger's own clock (naive UTC)
    assert C.turn_windows(lines) == [
        (naive, naive + timedelta(minutes=3)),
        (naive + timedelta(minutes=10), naive + timedelta(minutes=12)),
    ]
