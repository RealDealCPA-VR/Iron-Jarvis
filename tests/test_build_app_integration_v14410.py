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
