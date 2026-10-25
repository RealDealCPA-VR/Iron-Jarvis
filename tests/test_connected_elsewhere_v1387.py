"""Readiness 12B (G15, the daemon half): what the page reads when an app the
chat offered to connect is connected SOMEWHERE ELSE.

The page half (``dashboard/lib/connectedElsewhere.ts``, the chat page) reads
two things the daemon sends — the newest reply's offer (its Connect door, or
an app's key card from ``app_connect``) and the Directory's own list (``GET
/connectors``: a card's ``status``) — and offers the key card's Continue once
the app reads "connected". This file drives the REAL daemon through each way
in the brief names, and holds what it sends in
``dashboard/__tests__/fixtures/connected-elsewhere-v1387.json`` for the page's
test to feed through the REAL chat page:

* GitHub: the chat showed its key card; the user connected it on the
  Directory instead (``POST /connectors/github/connect`` — the Directory's own
  Connect, launching a REAL local child that stands in for GitHub's server).
* Google Drive: the chat's Connect door; the user signed in through the
  browser (the OAuth callback's write, ``ConnectionRegistry._upsert`` — the
  one step a test cannot do for real without Google).
* Pixio: the chat showed its key card; the user saved the key in Settings ›
  Connections (``POST /connections/pixio/key``, that page's own save — it
  stores the key and checks nothing against Pixio, so no network).

Only the model is scripted. Regenerate with ``IJ_REGEN_CONNECTED_ELSEWHERE=1``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path

import pytest

from iron_jarvis.connectors import catalog
from iron_jarvis.daemon import doors
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import unload_pack_tools
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_durable_chat_turns_v1336 import _get
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

#: Readiness 12B review: no pin of this lane ever leaves this machine — the
#: guard refuses and NAMES any host a missed stand-in would have dialled
#: (tests/_fakes/no_network.py).
pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "connected-elsewhere-v1387.json"
REGEN = os.environ.get("IJ_REGEN_CONNECTED_ELSEWHERE") == "1"
STANDIN = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
TOKEN = "ghp_ELSEWHERE12B0123456789abcdefNEVER"
PIXIO_KEY = "pk_test_ELSEWHERE12B_NEVERSHOWN"
DONE_KEYS = ("reply", "doors", "config_cards", "tools_used")


@pytest.fixture
def local_github(monkeypatch):
    """GitHub's card launched as a REAL local child — EVERY binding of the
    lookup (the catalog's, the service's the Directory's Connect calls, and
    the package's the route checks), so nothing ever reaches GitHub."""
    import iron_jarvis.connectors as connectors_pkg
    from iron_jarvis.connectors import service

    real = catalog.get_connector("github")
    swapped = dataclasses.replace(
        real, command=sys.executable, args=[STANDIN], transport="stdio", url="", auth_field="",
    )
    original = catalog.get_connector

    def lookup(c):
        return swapped if c == "github" else original(c)

    for mod in (catalog, service, connectors_pkg):
        monkeypatch.setattr(mod, "get_connector", lookup)
    assert service.get_connector("github").url == "" and connectors_pkg.get_connector("github").transport == "stdio"
    return swapped


async def _approve_all(app):
    async def approve(data):
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


async def _connectors(app, cid: str) -> dict:
    status, text = await _get(app, "/connectors")
    assert status == 200, text
    rows = [r for r in json.loads(text)["connectors"] if r.get("id") == cid or r.get("catalog_id") == cid]
    assert rows, f"no Directory card for {cid}"
    keep = ("id", "catalog_id", "name", "provider", "connect_via", "status", "enabled", "tools_loaded", "connected")
    return {k: rows[0][k] for k in keep if k in rows[0]}


def _stable(done: dict) -> dict:
    out = {k: done.get(k) for k in DONE_KEYS}
    for c in out.get("config_cards") or []:
        # Ids minted per call: held by their shape, not their value.
        for key, fixed in (("request_id", "seq_fixed"), ("change_id", "rec_fixed")):
            if key in c:
                c[key] = fixed
    return out


def _hold(key: str, real: dict) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data[key] = real
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    assert key in data, f"run IJ_REGEN_CONNECTED_ELSEWHERE=1 once: the fixture has no {key!r}"
    assert data[key] == real, (
        f"the daemon no longer sends what {FIXTURE.name}[{key!r}] says — regenerate it if intended, "
        "and update the dashboard half"
    )


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_github_card_then_connected_on_the_directory(tmp_path, local_github):
    app = create_app(str(tmp_path))
    p = app.state.platform
    try:
        p.router.stream = _calls_stream([("app_connect", {"app": "github"})])
        frames = await _drive_stream(app, _body("list my open PRs on GitHub"), await _approve_all(app))
        done = _done(frames)
        assert "app_connect" in done["tools_used"]
        [card] = [c for c in done["config_cards"] if c["kind"] == "secret"]
        assert card["name"] == "app.github"
        before = await _connectors(app, "github")
        assert before["status"] != "connected", before

        # The user connects GitHub on the Directory (not on the chat's card).
        status, raw = await _asgi_post(
            app, "/connectors/github/connect",
            {"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}},
        )
        assert status == 200, raw
        after = await _connectors(app, "github")
        assert after["status"] == "connected" and after["tools_loaded"] == 1, after
        assert TOKEN not in json.dumps([done, before, after])
        _hold("github", {"done": _stable(done), "before": before, "after": after})
    finally:
        unload_pack_tools(p.registry, "github")


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_drive_door_then_signed_in_through_the_browser(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([])
    frames = await _drive_stream(app, _body("search my Google Drive for the lease"), await _approve_all(app))
    done = _done(frames)
    assert any(d["href"] == doors._APPS_FIX_HREF for d in done["doors"]), done["doors"]
    before = await _connectors(app, "google_drive")
    assert before["status"] != "connected", before
    # What the OAuth callback writes once the user signs in in the browser.
    p.connections._upsert("google_drive", method="oauth", status="connected", account="")
    after = await _connectors(app, "google_drive")
    assert after["status"] == "connected", after
    _hold("drive", {"done": _stable(done), "before": before, "after": after})


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_pixio_card_then_key_saved_in_settings(tmp_path, monkeypatch):
    # A PIXIO_API_KEY in the environment counts as connected (registry.status
    # honours it); this PC may have one — the test must not read it.
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("app_connect", {"app": "pixio"})])
    frames = await _drive_stream(app, _body("make me a picture of a lighthouse with pixio"), await _approve_all(app))
    done = _done(frames)
    assert "app_connect" in done["tools_used"]
    assert [c["name"] for c in done["config_cards"] if c["kind"] == "secret"] == ["connection.pixio"]
    before = await _connectors(app, "pixio")
    assert before["status"] != "connected", before
    # The key saved on Settings › Connections (that page's own save route; it
    # stores the key — nothing is sent to Pixio).
    status, raw = await _asgi_post(app, "/connections/pixio/key", {"key": PIXIO_KEY})
    assert status == 200, raw
    assert PIXIO_KEY not in raw.decode()
    after = await _connectors(app, "pixio")
    assert after["status"] == "connected", after
    assert PIXIO_KEY not in json.dumps([done, before, after])
    _hold("pixio", {"done": _stable(done), "before": before, "after": after})


def test_the_page_points_at_the_directory_the_doors_use():
    """The page reads a Connect door by the address the daemon gives every
    one (``doors._APPS_FIX_HREF``) and by its words (``doors.connect_label``)."""
    src = (ROOT / "dashboard" / "lib" / "connectedElsewhere.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r'export const APPS_DOOR_HREF = "([^"]+)";', src)
    assert m and m.group(1) == doors._APPS_FIX_HREF
    for names, label in [
        (["Google Drive"], doors.connect_label(["Google Drive"])),
        (["Dropbox", "Google Drive"], doors.connect_label(["Dropbox", "Google Drive"])),
    ]:
        assert label == "Connect " + " and ".join(names) + " in Settings"
    assert 'const DOOR_RE = /^Connect (.+) in Settings$/;' in src
