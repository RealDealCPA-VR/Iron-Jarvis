"""v1.402.0 — the sidebar's allow-everything switch never answers a RISK card.

Found by the autonomy-metrics review (pre-existing since v1.276.0/v1.270.0):
the browser risk door's card (a payment or password field, a destructive word,
a flagged page — ``PanelTurns.resolve_risk_ask``) was added to the same
``_offered`` set the switch resolves wholesale, so turning "allow everything"
on while such a card waited APPROVED it. The switch now skips those cards
(``_floor_offered``); an ordinary card it still answers.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.platform import build_platform


class _Conn:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []


@pytest.mark.asyncio
async def test_turning_allow_everything_on_leaves_a_waiting_risk_card_alone(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    panel = PanelTurns(p)
    conn = _Conn()

    async def emit(c, event, payload):
        conn.frames.append((event, dict(payload)))

    monkeypatch.setattr(panel, "emit", emit)
    monkeypatch.setattr(panel, "_set_auto_allow", lambda on: bool(on))
    monkeypatch.setattr(panel, "_state", lambda *a, **k: {})
    # A running turn of THIS panel (the risk door answers only its own turn).
    hold = asyncio.get_running_loop().create_future()
    panel._task = asyncio.ensure_future(hold)
    panel._conn = conn
    panel._turn_id = "turn-1"
    registry = _approvals(SimpleNamespace(platform=p))

    # An ordinary card this panel offered...
    plain_id, plain_fut = registry.request("browser_click", {"target": {"name": "Next"}})
    panel._offered.add(plain_id)
    # ...and a RISK card waiting on the person.
    risk = asyncio.ensure_future(panel.resolve_risk_ask(
        SimpleNamespace(reason="this is a payment field"), SimpleNamespace(turn_id="turn-1"),
        name="browser_type", args={"target": {"name": "Card number"}},
    ))
    for _ in range(50):
        if any(e == "browser.panel_event" or "floor" in d for e, d in conn.frames):
            break
        await asyncio.sleep(0.01)
    assert any(d.get("floor") for _e, d in conn.frames), "the risk card was never put up"

    await panel._auto_allow(conn, {"on": True})
    await asyncio.sleep(0.05)
    assert plain_fut.done() and plain_fut.result() == "once", "the ordinary card was not answered"
    assert not risk.done(), "turning the switch on approved a RISK card"

    risk.cancel()
    assert await risk is False  # a stopped risk card is a refusal, never an Allow
    hold.cancel()
