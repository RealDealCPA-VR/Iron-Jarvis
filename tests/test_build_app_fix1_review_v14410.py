"""v1.441.0 review of lane fix1 — the plan card's words and the mode an
approval applies AGREE.

Lane fix1 made the mode an approved plan goes back to be read at the ANSWER
(``ClaudeDriver.plan_exit_mode``), but the card's ``then_mode`` ("Approve,
then ask first") was still the one the card was raised with. A chip moved
while the card waited then made the label say one mode while another was
applied — and the bad way round: Ask first -> Plan (card: "then ask first")
-> Full access -> Plan (Claude Code's prePlanMode is now bypassPermissions)
-> "Approve, then ask first" put the session in FULL ACCESS, and Enter (lane
fix2 refuses Enter only for ``thenMode == "full"``) could press it.

Now a mode change while a plan card waits re-sends the card with
``then_mode`` worked out by the SAME rule the card is raised with, so the
label, Enter's refusal and the mode applied are one fact. Driven through the
real service and the fake Claude Code (``IJ_FAKE_CLAUDE_MODES=1``).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, new_session, pending, stop_all, until, use_fake

import pytest


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _mode_body(mode: str) -> dict:
    return {"mode": mode, "confirm": True} if mode == "full" else {"mode": mode}


async def _card_label_and_mode(app, work, tmp_path, monkeypatch, *, start: str, picks: list[str]):
    """(the then_mode the card shows when answered, the mode Allow once applied)."""
    monkeypatch.setenv("IJ_FAKE_CLAUDE_MODES", "1")
    use_fake(monkeypatch, "claude_plan_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, **_mode_body(start)))["id"]
        if start != "plan":
            r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "plan"})
            assert r.status_code == 200, r.text
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        rid = await until(lambda: next(iter(svc.live.get(sid).asks), None) if svc.live.get(sid) else None)
        for mode in picks:
            r = await c.patch(f"/build/app/sessions/{sid}", json=_mode_body(mode))
            assert r.status_code == 200, r.text
        cards = [p for p in await pending(c, sid) if p.get("request_id") == rid]
        assert len(cards) == 1, cards
        shown = cards[0].get("then_mode", "")
        # The live stream says the same: the newest approval event for the card.
        evs = [e for e in svc.live[sid].log.read()[0] if e.get("type") == "approval" and e.get("request_id") == rid]
        assert evs and evs[-1].get("then_mode", "") == shown, (evs, shown)
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": rid, "decision": "allow"})
        assert r.status_code == 200, r.text
        await asyncio.wait_for(task, 30)
        row = store.get(app.state.platform.engine, sid)
    await stop_all(app)
    return shown, row.mode


async def test_full_access_then_plan_again_says_full_access_on_the_card(app, work, tmp_path, monkeypatch):
    shown, applied = await _card_label_and_mode(app, work, tmp_path, monkeypatch,
                                                start="ask", picks=["full", "plan"])
    assert applied == "full"
    assert shown == "full", "the card still said 'then ask first' while Allow put the session in Full access"


async def test_a_chip_moved_off_plan_is_what_the_card_says(app, work, tmp_path, monkeypatch):
    shown, applied = await _card_label_and_mode(app, work, tmp_path, monkeypatch,
                                                start="full", picks=["ask"])
    assert (shown, applied) == ("ask", "ask")


async def test_no_pick_keeps_the_card_as_raised(app, work, tmp_path, monkeypatch):
    """Started in Plan: Claude Code goes back to its default (Ask first)."""
    shown, applied = await _card_label_and_mode(app, work, tmp_path, monkeypatch, start="plan", picks=[])
    assert (shown, applied) == ("ask", "ask")


async def test_plan_to_edits_and_back_to_plan_says_edits(app, work, tmp_path, monkeypatch):
    """Plan -> Accept edits -> Plan while the card waits: Claude Code's
    prePlanMode is acceptEdits now, so the card says it and Allow applies it."""
    shown, applied = await _card_label_and_mode(app, work, tmp_path, monkeypatch,
                                                start="plan", picks=["edits", "plan"])
    assert (shown, applied) == ("edits", "edits")


async def test_full_access_picked_while_the_card_waits_is_named_before_the_press(app, work, tmp_path, monkeypatch):
    """The user picks Full access (two presses) while the plan card waits: the
    card now says "then full access" (so Enter refuses it, lane fix2) and
    Allow keeps Full access — the mode the user chose, named before the press.
    (A process STARTED in Full access then moved to Plan before its first
    message starts in Plan: Claude Code then goes back to its default, so the
    card names Ask first — test_no_pick_keeps_the_card_as_raised.)"""
    shown, applied = await _card_label_and_mode(app, work, tmp_path, monkeypatch, start="plan", picks=["full"])
    assert (shown, applied) == ("full", "full")


async def test_two_renames_while_stopped_tell_claude_code_the_last_one_once(app, work, tmp_path, monkeypatch):
    """P4 (review): two renames while the tool is stopped — the tool hears
    only the newest name, once, at its next start."""
    from tests._build_app_helpers_v1409 import sent

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        for title in ("Fix the cart", "Fix the cart and the totals"):
            r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": title})
            assert r.status_code == 200
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})

        def renames():
            return [m["request"]["title"] for m in sent(log) if m.get("type") == "control_request"
                    and (m.get("request") or {}).get("subtype") == "rename_session"]

        await until(renames)
        await asyncio.sleep(0.3)
        assert renames() == ["Fix the cart and the totals"]
    await stop_all(app)


# --------------------------------------------------------------------------- #
# A5 (review): an Antigravity that says it DOES take Iron Jarvis's tools
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("says_http", [True, False])
async def test_a_box_ticked_before_antigravity_said_it_takes_tools_restarts_it_once_it_does(
        tmp_path, work, monkeypatch, says_http):
    """Antigravity starts as "takes none" (agy 1.5.2, measured), so a start
    mints no connection. A NEWER Antigravity that then says
    ``mcpCapabilities.http`` turns the chip on — this process got no tools,
    so the next message starts it again with them (else the chip would read
    "Memory: on" over a process that has none). One that says nothing (1.5.2,
    or an older one) is never restarted for it."""
    from iron_jarvis.hosted import antigravity as A
    from iron_jarvis.hosted import jarvis_tools as JT
    from tests.test_build_app_antigravity_v1439 import use_fake as use_agy

    saved = dict(JT._TAKES)
    # v1.447.0 (moved on purpose): Antigravity no longer starts as "takes
    # none" by default; this test keeps its point (one an earlier start said
    # takes none, which a newer agy then contradicts) by saying so first.
    JT._TAKES["antigravity"] = False
    orig = A.AntigravityDriver.acp_servers

    def acp_servers(self, caps):
        return orig(self, {"mcpCapabilities": {"http": True}} if says_http else caps)

    monkeypatch.setattr(A.AntigravityDriver, "acp_servers", acp_servers)
    use_agy(monkeypatch, "a_reply", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            sid = (await new_session(c, work, harness="antigravity"))["id"]
            # Ticked in an earlier run of Iron Jarvis (the chip was shown then).
            store.update(app.state.platform.engine, sid, capabilities_json='{"memory": true}')
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn",
                                              json={"text": "hi", "turn_id": "t1"}), 30)
            assert r.status_code == 200, r.text
            assert JT.takes("antigravity") is says_http
            assert svc.live[sid].restart is says_http
    finally:
        JT._TAKES.clear()
        JT._TAKES.update(saved)
        await stop_all(app)
