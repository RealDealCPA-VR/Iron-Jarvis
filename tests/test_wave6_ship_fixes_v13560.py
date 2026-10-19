"""v1.356.0 ship fixes (the wave-6 re-review's open points 1-3).

(2) A SWITCHED-OFF pack never opens run-without-asking. ``mcp_call`` is one
shared permission key, so a pack's own ``auto_approve`` flag lets EVERY pack
run unasked; before this fix a pack the user switched off kept doing that.
The rule lives in ``mcp.tools.pack_grants_auto`` and is read by all three
places that compute it: the boot resolver (``platform.build_platform``), the
connectors service (``auto_approve_on``) and ``GET /mcp/servers``
(``auto_approve_effective``) — each pinned here through the real code, with
a switched-ON control so the pins cannot pass on an empty rule.

(1) and (3) are dashboard words (``wave6-ship-fixes-v13560.test.tsx``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors.service import auto_approve_on
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.tools import pack_grants_auto
from iron_jarvis.tools.permissions import headless_ask_resolver
from tests.fixtures.github_http_mcp_v13550 import FakeGitHub

TOKEN = "xoxb-SHIP6-slack-0123456789-NEVERSHOWN"
NAME = "slack"


@pytest.fixture(autouse=True)
def _fresh_records():
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    mcp_tools_mod.forget_live_client(NAME)


@pytest.fixture
def server():
    with FakeGitHub(TOKEN) as gh:
        yield gh


@pytest.mark.parametrize(
    ("row", "grants"),
    [
        ({"name": "a", "auto_approve": True}, True),
        ({"name": "a", "auto_approve": True, "enabled": True}, True),
        ({"name": "a", "auto_approve": True, "enabled": False}, False),
        ({"name": "a", "auto_approve": True, "enabled": "off"}, False),
        ({"name": "a", "auto_approve": False}, False),
        ({"name": "a"}, False),
        (None, False),
        ("not a row", False),
    ],
)
def test_pack_grants_auto_counts_a_flag_only_while_the_pack_is_on(row, grants):
    assert pack_grants_auto(row) is grants


def _trusted_pack(tmp_path, server) -> TestClient:
    """A real app with one pack the user set to run without asking."""
    c = TestClient(create_app(str(tmp_path)))
    res = c.post(
        "/mcp/servers",
        json={"name": NAME, "transport": "http", "url": server.url, "auth": {"value": TOKEN}},
    )
    assert res.status_code in (200, 201), res.text
    assert c.patch(f"/mcp/servers/{NAME}", json={"auto_approve": True}).status_code == 200
    return c


def test_switching_a_trusted_pack_off_takes_back_run_without_asking(tmp_path, server):
    c = _trusted_pack(tmp_path, server)
    try:
        plat = c.app.state.platform
        # Control: switched ON, its flag opens the shared key everywhere.
        assert c.get("/mcp/servers").json()["auto_approve_effective"] is True
        assert auto_approve_on(plat) is True

        assert c.patch(f"/mcp/servers/{NAME}", json={"enabled": False}).status_code == 200
        body = c.get("/mcp/servers").json()
        assert body["auto_approve_global"] is False
        assert body["auto_approve_effective"] is False, "an Off pack must not let other packs run unasked"
        assert auto_approve_on(plat) is False
        # The row keeps its own flag (switching back on restores it as it was).
        row = next(s for s in body["servers"] if s["name"] == NAME)
        assert row["auto_approve"] is True

        assert c.patch(f"/mcp/servers/{NAME}", json={"enabled": True}).status_code == 200
        assert c.get("/mcp/servers").json()["auto_approve_effective"] is True
    finally:
        mcp_tools_mod.unload_pack_tools(c.app.state.platform.registry, NAME)


def test_the_boot_resolver_never_grants_mcp_call_for_a_switched_off_pack(tmp_path, server):
    from iron_jarvis.platform import build_platform

    c = _trusted_pack(tmp_path, server)
    try:
        assert c.patch(f"/mcp/servers/{NAME}", json={"enabled": False}).status_code == 200
    finally:
        mcp_tools_mod.unload_pack_tools(c.app.state.platform.registry, NAME)
    mcp_tools_mod._LOAD_STATUS.clear()

    off = build_platform(str(tmp_path), ask_resolver=headless_ask_resolver())
    # The headless resolver refuses mcp_call; only the run-without-asking
    # wrapper says yes — and it must not be built for an Off pack's flag.
    assert off.permissions._ask_resolver("mcp_call", {}) is False

    # Control: the same pack switched back on DOES open it at boot.
    c2 = TestClient(create_app(str(tmp_path)))
    try:
        assert c2.patch(f"/mcp/servers/{NAME}", json={"enabled": True}).status_code == 200
    finally:
        mcp_tools_mod.unload_pack_tools(c2.app.state.platform.registry, NAME)
    mcp_tools_mod._LOAD_STATUS.clear()
    on = build_platform(str(tmp_path), ask_resolver=headless_ask_resolver())
    try:
        assert on.permissions._ask_resolver("mcp_call", {}) is True
    finally:
        mcp_tools_mod.unload_pack_tools(on.registry, NAME)
