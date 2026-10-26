"""v1.392.0 review (decision 8): the session's optional cap, the defects the
reviewer found in the doer's cut, each pinned red first.

1. A PATCH that was PARTLY wrong was half-kept in silence: ``{"minutes": 30,
   "usd": 0}`` saved a 30-minute cap and dropped the dollar limit the user
   typed, ``{"minutes": 2.5}`` became 2 minutes and ``{"minutes": 30,
   "hours": 2}`` lost the hours — all answered 200. A cap the PATCH cannot
   keep exactly as sent is now the 400 sentence, and nothing changes.
2. A reply carried on after a restart counted the time Iron Jarvis was NOT
   running: the cap measured from the original question's wall-clock time,
   so a reply cut off ten minutes in by a crash the night before was capped
   the moment the next boot carried it on ("This reply stopped at this
   session's 1-hour time limit") — Phase 2 exists to let it carry on. The
   carried-on reply now counts the time its earlier part(s) RAN (their lines'
   own ``timing.ms``) plus its own.

The wall clock a cap reads is ``harness.reply_cap.WALL``, moved by the test.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import reply_cap as RC
from tests.test_build_resume_v1391 import _cut_off_mid_reply, _reboot, _write
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    messages_of,
    new_session,
    setup_of,
    until,
    wire,
)


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


# --------------------------------------------------------------------------- #
# 1. a cap is kept exactly as sent, or refused
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cap", [
    {"minutes": 30, "usd": 0},          # the dollar limit is out of range
    {"minutes": 30, "usd": "5"},        # a dollar limit as text
    {"minutes": 2.5},                   # not a whole number of minutes
    {"minutes": 30, "hours": 2},        # a key the cap does not have
    {"minutes": True},                  # a switch is not a number
    {"usd": 12_000},                    # past the most a cap may be
    {},                                 # nothing at all
])
async def test_a_cap_the_patch_cannot_keep_exactly_is_refused_and_nothing_changes(app, work, cap):
    sid = new_session(app, work)["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 45, "usd": 3}})
    assert status == 200
    status, out = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": cap})
    assert status == 400, (cap, out)
    assert out["detail"] == build_routes.CAP_REFUSED
    assert setup_of(app, sid)["cap"] == {"minutes": 45, "usd": 3.0}


async def test_a_whole_cap_is_kept_as_sent(app, work):
    sid = new_session(app, work)["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30.0, "usd": 1.5}})
    assert status == 200 and view["setup"]["cap"] == {"minutes": 30, "usd": 1.5}


def test_the_strict_reading_is_the_whitelists_on_a_good_cap():
    for raw in ({"minutes": 1}, {"usd": 0.01}, {"minutes": 10_080, "usd": 10_000}, {"usd": 2.345}):
        assert RC.strict(raw) == RC.clean(raw)
    assert RC.strict({"minutes": 30, "usd": 0}) is None
    assert RC.strict(None) is None and RC.strict([]) is None


# --------------------------------------------------------------------------- #
# 2. a carried-on reply counts the time it RAN, never the time Iron Jarvis
#    was off
# --------------------------------------------------------------------------- #


def _set_ran_ms(app, sid: str, turn_id: str, ms: int) -> None:
    """The cut-off line's own running time (its ``timing.ms``)."""
    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatThreadRecord, sid)
        msgs = json.loads(row.messages_json or "[]")
        hit = [m for m in msgs if m.get("role") == "assistant" and m.get("turnId") == turn_id]
        assert hit, "the cut-off reply wrote no line"
        timing = dict(hit[-1].get("timing") or {})
        timing["ms"] = ms
        hit[-1]["timing"] = timing
        row.messages_json = json.dumps(msgs)
        db.add(row)
        db.commit()


async def _resume(home: str, sid: str, *, ran_ms: "int | None" = None) -> dict:
    second = _reboot(home)
    if ran_ms is not None:
        # The boot wrote the cut-off reply's line (with the time it ran).
        _set_ran_ms(second, sid, "bt-1", ran_ms)
    wire(second, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    carried = await build_routes.resume_after_restart(second.state.d)
    assert carried == [sid]
    logs = second.state.platform.turn_logs
    new = await until(lambda: next((lg for lg in list(logs._logs.values()) if lg.thread_id == sid), None))
    assert await until(lambda: new.status != "running", 30)
    return dict((e, json.loads(d or "{}")) for e, d in new.frames)["done"]


def _asked_at(app, sid: str) -> datetime:
    asked = next(m for m in messages_of(app, sid) if m["role"] == "user" and m.get("turnId") == "bt-1")
    at = datetime.fromisoformat(asked["at"].replace("Z", "+00:00"))
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


async def test_a_night_with_the_app_off_does_not_count_against_the_time_cap(tmp_path, work, monkeypatch):
    """The reply ran a few seconds, the app was off for ten hours, the cap is
    30 minutes: the carried-on reply goes on and writes its third file."""
    home = str(tmp_path / "home2")
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await asgi(first, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30}})
    await _cut_off_mid_reply(first, sid)
    at = _asked_at(first, sid)
    monkeypatch.setattr(RC, "WALL", lambda: at.timestamp() + 10 * 3600)
    done = await _resume(home, sid)
    assert "cap" not in done, "the hours the app was off were counted as the reply's time"
    assert (work / "three.txt").exists()


async def test_the_time_its_earlier_part_ran_still_counts(tmp_path, work, monkeypatch):
    """The cut-off part RAN 31 minutes (its line says so): the carried-on
    reply is at its 30-minute cap at once."""
    home = str(tmp_path / "home3")
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await asgi(first, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30}})
    await _cut_off_mid_reply(first, sid)
    at = _asked_at(first, sid)
    monkeypatch.setattr(RC, "WALL", lambda: at.timestamp() + 10 * 3600)
    done = await _resume(home, sid, ran_ms=31 * 60 * 1000)
    assert done["cap"] == {"kind": "time", "limit": 30}
    assert not (work / "three.txt").exists()


def test_what_the_earlier_parts_ran_is_read_off_their_lines():
    msgs = [
        {"role": "user", "content": "q", "turnId": "t1"},
        {"role": "assistant", "turnId": "t1", "interrupted": True, "timing": {"ms": 600_000}},
        {"role": "user", "content": "carry on", "turnId": "t2", "continuation": True, "resumedFrom": "t1"},
        {"role": "assistant", "turnId": "t2", "interrupted": True,
         "timing": {"startedAt": 1_000_000, "endedAt": 1_120_000}},
        {"role": "assistant", "turnId": "t3", "timing": {"ms": True}},
        {"role": "assistant", "turnId": "t4", "timing": "junk"},
    ]
    assert build_routes.ran_since(msgs, 0) == pytest.approx(720.0)
