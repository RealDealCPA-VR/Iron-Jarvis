"""v1.441.0 integration pins (lanes fix1, fix2, g1-g4 merged).

I1: lane fix1 re-sends Claude Code's plan card (same request_id) with the
then_mode it will REALLY apply after a live mode change; lane fix2 refuses
Enter when the card's thenMode / setsMode is "full". This file writes what
the REAL daemon sends the page in that case (the session read while the card
waits, and the turn's SSE frames) to
``dashboard/__tests__/fixtures/build-code-integration-v1441-0.json``; the
dashboard pin feeds it through the real fold and the real card. Regenerate
with ``IJ_REGEN_BUILD_CODE_INTEGRATION=1``; otherwise the daemon's answer is
checked against the fixture.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-code-integration-v1441-0.json"


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


def _norm(value, sid: str, rid: str):
    """Stable for a fixture: the session id, the card id and every time."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("at", "created_at", "updated_at", "last_active", "started_at", "seq"):
                out[k] = 0 if isinstance(v, (int, float)) else ("" if v is None else "T")
                continue
            out[k] = _norm(v, sid, rid)
        return out
    if isinstance(value, list):
        return [_norm(v, sid, rid) for v in value]
    if isinstance(value, str):
        return value.replace(sid, "SID").replace(rid, "RID")
    return value


async def _plan_card_scenario(tmp_path, work, monkeypatch) -> dict:
    monkeypatch.setenv("IJ_FAKE_CLAUDE_MODES", "1")
    use_fake(monkeypatch, "claude_plan_v1417", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            sid = (await new_session(c, work, mode="ask"))["id"]
            r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "plan"})
            assert r.status_code == 200, r.text
            task = asyncio.ensure_future(
                c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
            rid = await until(lambda: next(iter(svc.live.get(sid).asks), None) if svc.live.get(sid) else None)
            before = (await c.get(f"/build/app/sessions/{sid}")).json()
            for body in ({"mode": "full", "confirm": True}, {"mode": "plan"}):
                r = await c.patch(f"/build/app/sessions/{sid}", json=body)
                assert r.status_code == 200, r.text
            after = (await c.get(f"/build/app/sessions/{sid}")).json()
            r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": rid, "decision": "deny"})
            assert r.status_code == 200, r.text
            resp = await asyncio.wait_for(task, 30)
            stream = frames(resp.text)
    finally:
        await stop_all(app)
    return {k: _norm(v, sid, rid) for k, v in
            {"before": before, "after": after, "stream": stream}.items()}


def _approvals(evs: list) -> list[dict]:
    return [e for e in evs if isinstance(e, dict) and e.get("type") == "approval"]


async def test_the_plan_card_is_resent_with_the_same_id_and_full_access(tmp_path, work, monkeypatch):
    got = await _plan_card_scenario(tmp_path, work, monkeypatch)
    # The card raised in Ask first says "then ask first"; after Full access ->
    # Plan it is re-sent, SAME request id, saying "then full access".
    for part in ("after", "stream"):
        evs = got[part]["events"] if part == "after" else got[part]
        cards = _approvals(evs)
        assert [c.get("then_mode") for c in cards] == ["ask", "full"], (part, cards)
        assert {c["request_id"] for c in cards} == {"RID"}
    assert [c.get("then_mode") for c in _approvals(got["before"]["events"])] == ["ask"]
    assert [p.get("then_mode") for p in got["after"]["pending"]] == ["full"]
    if os.environ.get("IJ_REGEN_BUILD_CODE_INTEGRATION") == "1":
        FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for part in ("before", "after"):
        assert _approvals(saved[part]["events"]) == _approvals(got[part]["events"]), part
        assert saved[part]["pending"] == got[part]["pending"], part
    assert _approvals(saved["stream"]) == _approvals(got["stream"])


# --------------------------------------------------------------------------- #
# I2: the unreadable-marker test no longer depends on the clock ticking
# --------------------------------------------------------------------------- #
async def test_the_unreadable_marker_test_holds_when_the_cut_and_the_marker_share_a_tick(tmp_path, monkeypatch):
    """``test_an_unreadable_marker_never_lets_an_older_cut_carry_on`` failed
    about 1 run in 3: the cut and the rewritten marker landed in the same
    15.6 ms tick. Here both clocks are FROZEN to one instant (the tick at its
    worst); the test must still pass — it steps the marker's clock itself."""
    from datetime import datetime, timezone

    from iron_jarvis.hosted import carry_on as CO
    from iron_jarvis.hosted import events as E
    from tests import test_build_app_carry_followups_v14220 as F

    t = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(E, "now_iso", lambda: t.isoformat())
    monkeypatch.setattr(CO, "now_utc", lambda: t)
    monkeypatch.setattr(CO, "CLOCK", lambda: t)
    app = create_app(str(tmp_path / "fresh"))
    home = Path(app.state.platform.config.home)
    work = tmp_path / "w"
    work.mkdir()
    try:
        await F.test_an_unreadable_marker_never_lets_an_older_cut_carry_on(home, app, work, monkeypatch)
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# I5: Codex Plan with no model known reads the thread's model first
# --------------------------------------------------------------------------- #
def _codex(*, read_answer, mode="plan"):
    """A Codex driver as a code host keeps it across an update from v1.440.0:
    its state had no ``_thread_model`` (the attribute is new), so a restored
    driver knows no model. ``read_answer``: what ``thread/read`` answers (a
    dict), or an Exception to raise."""
    from iron_jarvis.hosted import codex as X

    got: list = []

    async def write(obj):
        got.append({"wrote": obj})
        method = obj.get("method")
        if method == "thread/read":
            if isinstance(read_answer, Exception):
                d._replies.settle(obj["id"], None, read_answer)
            else:
                d._replies.settle(obj["id"], read_answer)
        elif method == "turn/start":
            d._replies.settle(obj["id"], {"turn": {"id": "tu2"}})

    d = X.CodexDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                      on_session_id=lambda s: None, mode=mode)
    d.session_id = "th"
    return d, got


def _sent(got: list, method: str) -> list[dict]:
    return [g["wrote"] for g in got if isinstance(g, dict) and g.get("wrote", {}).get("method") == method]


async def test_codex_plan_with_no_model_known_asks_the_thread_and_plans():
    """Measured on codex 0.157.0 (scratch CODEX_HOME, no model call):
    ``thread/read {threadId, includeTurns: false}`` answers ``thread.model``
    (and ``reasoningEffort``)."""
    d, got = _codex(read_answer={"thread": {"id": "th", "model": "gpt-6-astra", "reasoningEffort": "high"}})
    await d.send_user("plan it", [])
    reads = _sent(got, "thread/read")
    assert reads and reads[0]["params"] == {"threadId": "th", "includeTurns": False}
    start = _sent(got, "turn/start")[0]["params"]
    assert start["collaborationMode"] == {"mode": "plan", "settings": {
        "model": "gpt-6-astra", "reasoning_effort": "high", "developer_instructions": None}}
    assert [m for m in got if isinstance(m, dict) and m.get("wrote", {}).get("method")][-1]["wrote"]["method"] \
        == "turn/start", "the turn's request stays the newest (a restart re-waits on it)"
    assert not [e for e in got if isinstance(e, dict) and e.get("type") == "notice"]


async def test_codex_plan_whose_model_cannot_be_read_says_so():
    from iron_jarvis.hosted import words as W
    from iron_jarvis.hosted.driver import ControlRefused

    d, got = _codex(read_answer=ControlRefused("no such method"))
    await d.send_user("plan it", [])
    start = _sent(got, "turn/start")[0]["params"]
    assert "collaborationMode" not in start
    notes = [e["text"] for e in got if isinstance(e, dict) and e.get("type") == "notice"]
    assert notes == [W.CODEX_PLAN_NO_MODEL]


async def test_leaving_plan_with_no_model_known_says_codex_may_still_plan():
    from iron_jarvis.hosted import words as W

    d, got = _codex(read_answer={"thread": {"id": "th"}}, mode="ask")
    d._collab = "plan"
    await d.send_user("now do it", [])
    assert "collaborationMode" not in _sent(got, "turn/start")[0]["params"]
    notes = [e["text"] for e in got if isinstance(e, dict) and e.get("type") == "notice"]
    assert notes == [W.CODEX_LEAVE_PLAN_NO_MODEL]


async def test_a_session_that_never_planned_reads_nothing():
    d, got = _codex(read_answer={"thread": {"id": "th", "model": "m"}}, mode="ask")
    await d.send_user("hello", [])
    assert _sent(got, "thread/read") == []
    assert "collaborationMode" not in _sent(got, "turn/start")[0]["params"]
