"""v1.433.0 (Wave 1, A14): lowering "Keep activity history" always asks.

``event_retention_days`` was an ``ask`` row — a standing "always" grant could
lift its card — and said it needed a restart. Neither was true to what it
does: the daily sweep (daemon/app._event_prune_loop) re-reads it on every
pass, and core/db.prune_events deletes the ledger (ToolInvocation), the run
records (AgentRun) and the Undo journal past it, so ONE lifted card could wipe
the audit trail. It is ask-floor now (carded every time, never lifted, never
offered "Always") and applies with no restart.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done


def test_the_row_is_protected_and_needs_no_restart():
    d = schema.get("event_retention_days")
    assert d.tier == "ask-floor"
    assert d.tier_for(1) == "ask-floor" and d.tier_for(0) == "ask-floor"
    assert d.restart is False


@pytest.mark.asyncio
async def test_the_ordinary_ask_tool_is_refused_toward_the_protected_one(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    before = cfg.event_retention_days
    app.state.platform.router.stream = _calls_stream([("config_change", {"key": "event_retention_days", "value": "1"})])
    seen: list[str] = []

    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("keep only one day of activity history, change that setting"), approve)
    assert cfg.event_retention_days == before
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False
    assert "config_change_protected" in finished[0]["output"]
    assert _done(frames)["config_cards"] == []


@pytest.mark.asyncio
async def test_the_protected_tool_cards_even_in_dont_ask_and_never_offers_always(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    app.state.platform.router.stream = _calls_stream(
        [("config_change_protected", {"key": "event_retention_days", "value": 30})]
    )
    seen: list[tuple[str, object]] = []

    async def approve(data):
        seen.append((data["tool"], data.get("can_always")))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("keep 30 days of activity history, change that setting", "yolo"), approve)
    assert seen == [("config_change_protected", False)]
    assert cfg.event_retention_days == 30
    (card,) = _done(frames)["config_cards"]
    assert card["restart"] is False
