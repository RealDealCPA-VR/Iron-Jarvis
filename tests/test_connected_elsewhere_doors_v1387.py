"""Readiness 12B review (G15): the page reads a Connect door by the REAL words
the daemon writes, and matches them against the REAL Directory names.

The lane's reader split a door's names at ", ", " and " and " or " — but a
catalog name may hold those itself: Microsoft 365's card is "Microsoft 365
(Outlook, Calendar, OneDrive)", so "what's on my outlook calendar" gets the
door "Connect Microsoft 365 (Outlook, Calendar, OneDrive) in Settings" and
"check my inbox" the mail choice "Connect Gmail or Microsoft 365 (Outlook,
Calendar, OneDrive) in Settings". Split, those read "Microsoft 365 (Outlook",
"Calendar", "OneDrive)": connecting Microsoft 365 on the Directory never
offered Continue (and a user's own extension named "Calendar" would have been
taken for it). The lane's own pin used an invented label without the
parentheses.

This file drives the REAL stream lane (only the model is scripted; it calls
nothing) for the messages that make each door, reads the REAL Directory
(``GET /connectors``) for the names, and holds both in
``dashboard/__tests__/fixtures/connected-elsewhere-doors-v1387.json`` for the
page's test. Regenerate with ``IJ_REGEN_CONNECTED_DOORS=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iron_jarvis.daemon import doors
from iron_jarvis.daemon.app import create_app
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_durable_chat_turns_v1336 import _get
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "connected-elsewhere-doors-v1387.json"
REGEN = os.environ.get("IJ_REGEN_CONNECTED_DOORS") == "1"

MESSAGES = {
    "outlook": "what's on my outlook calendar today",
    "inbox": "check my inbox for the invoice",
    "three": "search my notion and my google drive and my dropbox for the lease",
}
NAMED = ("microsoft_365", "gmail", "notion", "google_drive", "dropbox", "onedrive")


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_the_real_doors_and_the_real_directory_names(tmp_path):
    real: dict = {"doors": {}, "rows": {}}
    for key, msg in MESSAGES.items():
        app = create_app(str(tmp_path / key))
        app.state.platform.router.stream = _calls_stream([])

        async def nobody(_data):  # the scripted model calls nothing
            raise AssertionError("no card expected")

        done = _done(await _drive_stream(app, _body(msg), nobody))
        apps_doors = [d for d in done["doors"] if d["href"] == doors._APPS_FIX_HREF]
        assert len(apps_doors) == 1, done["doors"]
        real["doors"][key] = apps_doors[0]["label"]
    status, text = await _get(app, "/connectors")
    assert status == 200, text
    for row in json.loads(text)["connectors"]:
        if row.get("id") in NAMED:
            real["rows"][row["id"]] = {k: row.get(k) for k in ("id", "name", "connect_via", "status")}
    assert "(Outlook, Calendar, OneDrive)" in real["doors"]["outlook"], real
    assert set(real["rows"]) == set(NAMED), real["rows"]

    if REGEN:
        FIXTURE.write_text(json.dumps(real, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    held = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert held == real, (
        f"the daemon no longer writes what {FIXTURE.name} holds — regenerate it with "
        "IJ_REGEN_CONNECTED_DOORS=1 if intended, and check the page half"
    )
