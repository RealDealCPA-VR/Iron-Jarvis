"""v1.400.3 — the Notifications page offers EVERY alert the daemon sends.

The per-destination checkboxes ("Send this destination") on Settings ›
Connections › Notifications are the list a destination's ``events`` is saved
from. A destination with no list gets every alert; ticking a smaller set saves
exactly the ticked keys. The night shift's summary (``build.night_summary``,
v1.399.0) was in the daemon's alert set but had no box, so ticking any smaller
set saved a list without it and the phone's one message a night stopped with
no word said. The page's list and ``DEFAULT_ALERT_EVENTS`` are now one set,
pinned here, so the next alert the daemon learns cannot go missing the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS
from iron_jarvis.core.events import EventType

PAGE = Path(__file__).resolve().parents[1] / "dashboard" / "components" / "settings" / "pages" / "NotificationsPage.tsx"


def _page_event_keys() -> list[str]:
    # The CI runner checks files out with CRLF (v1.232.1): normalise first.
    text = PAGE.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("const EVENT_LABELS")
    block = text[start:text.index("\n];", start)]
    return re.findall(r'\{\s*key:\s*"([^"]+)"', block)


def test_the_page_offers_exactly_the_alerts_the_daemon_sends():
    keys = _page_event_keys()
    assert len(keys) == len(set(keys)), keys
    assert set(keys) == set(DEFAULT_ALERT_EVENTS), (
        f"missing on the page: {sorted(set(DEFAULT_ALERT_EVENTS) - set(keys))}; "
        f"on the page but never sent: {sorted(set(keys) - set(DEFAULT_ALERT_EVENTS))}"
    )


def test_the_night_summary_has_its_own_box():
    assert EventType.BUILD_NIGHT_SUMMARY in _page_event_keys()


def test_a_narrowed_destination_still_gets_the_night_summary(tmp_path):
    """Untick one box (as the page does) and save: the saved list still holds
    the night summary, and the daemon accepts every key the page sends."""
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    client = TestClient(app)
    narrowed = [k for k in _page_event_keys() if k != "skill.proposal_created"]
    r = client.post("/comm/channels", json={
        "name": "phone", "type": "discord",
        "config": {"webhook_url": "https://discord.com/api/webhooks/1/x", "events": narrowed},
    })
    assert r.status_code == 200, r.text
    saved = app.state.d.platform.config.comm["channels"]["phone"]["events"]
    assert EventType.BUILD_NIGHT_SUMMARY in saved
    assert "skill.proposal_created" not in saved
