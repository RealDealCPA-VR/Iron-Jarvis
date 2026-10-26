"""v1.392.0 (the long-jobs plan's Phase 3, decision 8): AN OPTIONAL CAP on a
coding session's reply — OFF by default.

A session's ⋯ menu sets a cap on a reply's time (minutes) and/or list-price
cost (dollars), kept on the session's setup (``cap``, ``_clean_setup``'s
whitelist). At the cap the reply ends EXACTLY the step-limit way: no tool runs
again, one tool-less progress report worded for the cap, ``cap`` on the done
frame, ``capMet`` on the reply line, and the page's Keep going. A reply
carried on after a restart (v1.391.0) counts from its ORIGINAL question.
Off is byte-identical: the lane is not even handed a cap.

The wall clock a cap reads is ``harness.reply_cap.WALL``, moved by the test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes.chat import _clean_setup
from iron_jarvis.harness import reply_cap as RC
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_build_resume_v1391 import _cut_off_mid_reply, _reboot, _write
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    turn_frames,
    until,
    wire,
)


class Wall:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


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


@pytest.fixture()
def wall(monkeypatch):
    w = Wall()
    monkeypatch.setattr(RC, "WALL", w)
    return w


def _spy_lane(monkeypatch) -> list[dict]:
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(kw)
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


async def _run(app, sid: str, turn: str, text: str = "build it") -> dict:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn}, timeout=120)
    assert status == 200, raw
    frames = turn_frames(app, turn)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames[-1][1]


def _last_reply(app, sid: str) -> dict:
    return [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]


# --------------------------------------------------------------------------- #
# the setting
# --------------------------------------------------------------------------- #


async def test_the_cap_is_set_in_the_session_kept_by_its_whitelist_and_removed_by_null(app, work):
    sid = new_session(app, work)["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30, "usd": 2.5}})
    assert status == 200 and view["setup"]["cap"] == {"minutes": 30, "usd": 2.5}
    assert setup_of(app, sid)["cap"] == {"minutes": 30, "usd": 2.5}
    # Junk is a 400 in words, and nothing changes.
    status, out = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 0, "usd": -1}})
    assert status == 400 and "minutes" in out["detail"]
    assert setup_of(app, sid)["cap"] == {"minutes": 30, "usd": 2.5}
    # One of the two alone.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"usd": 5}})
    assert status == 200 and view["setup"]["cap"] == {"usd": 5.0}
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": None})
    assert status == 200 and "cap" not in view["setup"] and "cap" not in setup_of(app, sid)


def test_the_whitelist_keeps_only_a_real_cap():
    import json

    assert json.loads(_clean_setup({"cap": {"minutes": 45.4, "usd": 1.234, "x": 1}}))["cap"] == {
        "minutes": 45, "usd": 1.23,
    }
    for junk in ({"minutes": True}, {"usd": "5"}, {"minutes": 99999}, {"usd": 0}, [], "30", None):
        assert _clean_setup({"cap": junk}) == ""


# --------------------------------------------------------------------------- #
# the ending
# --------------------------------------------------------------------------- #


async def test_at_the_time_cap_the_reply_ends_with_a_progress_report_and_keep_going(app, work, wall):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30}})
    assert status == 200

    async def late(_script):
        wall.advance(31 * 60)  # this round took the reply past its 30 minutes
        return _write("c2", "two.txt", "2")

    script = wire(app, Script([_write("c1", "one.txt", "1"), late, _write("c3", "three.txt", "3")]))
    done = await _run(app, sid, "bt-cap")
    # No tool ran after the cap: two files, the third never asked for.
    assert (work / "one.txt").exists() and (work / "two.txt").exists() and not (work / "three.txt").exists()
    assert done["cap"] == {"kind": "time", "limit": 30} and "step_limit" not in done
    # The last call was the tool-less progress report, told it met the cap.
    last = script.calls[-1]
    assert last["tools"] == []
    told = last["messages"][-1].content
    assert told == RC.instruction({"kind": "time", "limit": 30})
    assert "time limit (30 minutes)" in told and "Keep going" in told
    line = _last_reply(app, sid)
    assert line["capMet"] == {"kind": "time", "limit": 30} and "stepLimit" not in line


async def test_at_the_cost_cap_the_reply_ends_the_same_way(app, work, wall):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"usd": 0.05}})
    assert status == 200

    class Priced(Script):
        async def stream(self, **kw):
            async for frame in super().stream(**kw):
                if frame.get("type") == "final":
                    r = frame["response"]
                    frame = {**frame, "response": LLMResponse(
                        text=r.text, tool_calls=r.tool_calls,
                        usage={"input_tokens": 3, "output_tokens": 1, "cost_usd": 0.02},
                    )}
                yield frame

    script = wire(app, Priced([_write(f"c{n}", f"f{n}.txt", str(n)) for n in range(1, 8)]))
    done = await _run(app, sid, "bt-cost")
    assert done["cap"] == {"kind": "cost", "limit": 0.05}
    # 0.02 a call: the cap is met after the third call — three files.
    assert sorted(p.name for p in work.glob("f*.txt")) == ["f1.txt", "f2.txt", "f3.txt"]
    assert "cost limit ($0.05 of list-price cost)" in script.calls[-1]["messages"][-1].content
    assert _last_reply(app, sid)["capMet"] == {"kind": "cost", "limit": 0.05}


async def test_off_the_lane_is_never_handed_a_cap_and_the_reply_is_as_before(app, work, wall, monkeypatch):
    seen = _spy_lane(monkeypatch)
    sid = new_session(app, work, approval_mode="yolo")["id"]

    async def late(_script):
        wall.advance(10 * 24 * 3600)
        return _write("c2", "two.txt", "2")

    wire(app, Script([_write("c1", "one.txt", "1"), late, "Both written."]))
    done = await _run(app, sid, "bt-off")
    assert "reply_cap" not in seen[0]
    assert "cap" not in done and done["reply"] == "Both written."
    assert "capMet" not in _last_reply(app, sid)


async def test_keep_going_after_a_cap_is_a_new_reply_with_its_own_clock(app, work, wall):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30}})

    async def late(_script):
        wall.advance(31 * 60)
        return _write("c1", "one.txt", "1")

    wire(app, Script([late, _write("c2", "two.txt", "2")]))
    first = await _run(app, sid, "bt-k1")
    assert first["cap"]["kind"] == "time"
    wire(app, Script([_write("c2", "two.txt", "2"), "Carried on."]))
    second = await _run(app, sid, "bt-k2", text="continue")
    assert "cap" not in second and second["reply"] == "Carried on." and (work / "two.txt").exists()


# --------------------------------------------------------------------------- #
# a reply carried on after a restart counts from its original question
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("past_cap", [True, False])
async def test_a_resumed_reply_counts_from_its_original_question(tmp_path, work, monkeypatch, past_cap):
    home = str(tmp_path / "home2")
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await asgi(first, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30}})
    await _cut_off_mid_reply(first, sid)
    asked = next(m for m in messages_of(first, sid) if m["role"] == "user" and m.get("turnId") == "bt-1")
    at = datetime.fromisoformat(asked["at"].replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    # The restart comes 31 minutes after the ORIGINAL question (or 5).
    monkeypatch.setattr(RC, "WALL", lambda: at.timestamp() + (31 if past_cap else 5) * 60)
    second = _reboot(home)
    wire(second, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    carried = await build_routes.resume_after_restart(second.state.d)
    assert carried == [sid]
    logs = second.state.platform.turn_logs
    new = await until(lambda: next((lg for lg in list(logs._logs.values()) if lg.thread_id == sid), None))
    assert await until(lambda: new.status != "running", 30)
    done = dict((e, __import__("json").loads(d or "{}")) for e, d in new.frames)["done"]
    if past_cap:
        assert done["cap"] == {"kind": "time", "limit": 30}
        assert not (work / "three.txt").exists()
    else:
        assert "cap" not in done and (work / "three.txt").exists()


def test_a_resumed_reply_counts_what_its_cut_off_part_already_cost():
    msgs = [
        {"role": "user", "content": "q", "turnId": "t1"},
        {"role": "assistant", "turnId": "t1", "interrupted": True,
         "calls": [{"usage": {"cost_usd": 0.4}}, {"usage": {"cost_usd": 0.1}}, {"usage": {}}]},
        {"role": "user", "content": "carry on", "turnId": "t2", "continuation": True, "resumedFrom": "t1"},
        {"role": "assistant", "turnId": "t2", "interrupted": True, "usage": {"cost_usd": 0.25}},
    ]
    assert build_routes.spent_since(msgs, 0) == pytest.approx(0.75)
    cap = RC.ReplyCap(usd=1.0, started_at=0.0, spent_usd=0.75)
    assert cap.met(0.2) is None and cap.met(0.25) == {"kind": "cost", "limit": 1.0}
