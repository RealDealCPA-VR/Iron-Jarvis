"""Which network connections Iron Jarvis answers (second review, group 9: F10).

With "Let other computers on my network reach this Iron Jarvis" on, the
desktop starts the daemon listening on EVERY network the PC is on
(``0.0.0.0``) and names this PC's private addresses in
``IRONJARVIS_HOST_ALLOWLIST`` (``desktop/main.js`` ``daemonNetwork``). The only
filter was the Host header, which the CALLER writes: a computer on another
network the PC joined later (a cafe, a hotel) reached the daemon by sending
the allowed name in its Host header.

Now the address a request ARRIVED on (ASGI ``scope["server"]``) decides too:
with the network opened by the setting, a request is served only when it
arrived on a loopback address or on an address in the same allowlist. With the
daemon on loopback only (the setting off, the default), nothing changes.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import HostOriginGuardMiddleware

LAN_IP = "192.168.1.20"
OTHER_NETWORK_IP = "10.0.0.5"  # an address the PC got on another network
TOKEN = "install-token-value"


def _run(scope: dict) -> tuple[bool, list[dict]]:
    """Drive the guard with one scope: (did the app run, what was sent)."""
    reached: list[bool] = []
    sent: list[dict] = []

    async def inner(scope, receive, send):
        reached.append(True)

    async def receive():
        return {"type": "websocket.connect"} if scope["type"] == "websocket" else {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(HostOriginGuardMiddleware(inner)(scope, receive, send))
    return bool(reached), sent


def _scope(server, host_header=f"{LAN_IP}:8787", kind="http"):
    scope = {
        "type": kind,
        "path": "/sessions",
        "method": "GET",
        "headers": [(b"host", host_header.encode())],
        "client": ("192.168.1.30", 51000),
    }
    if server is not ...:
        scope["server"] = server
    return scope


@pytest.fixture
def lan_on(monkeypatch):
    """What the desktop spawns with the setting on."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", LAN_IP)
    monkeypatch.setenv("IRONJARVIS_LAN_ACCESS", "1")


def test_a_request_that_arrived_on_another_network_is_refused_whatever_its_host_header(lan_on):
    ran, sent = _run(_scope((OTHER_NETWORK_IP, 8787)))
    assert not ran, "a request that arrived on an address outside the allowlist reached the app"
    assert sent[0]["status"] == 403


@pytest.mark.parametrize(
    "server",
    [
        (LAN_IP, 8787),
        (f"::ffff:{LAN_IP}", 8787),  # an IPv4-mapped IPv6 local address
        ("127.0.0.1", 8787),
        ("::1", 8787),
        ("::ffff:127.0.0.1", 8787),
        ("testserver", 80),  # Starlette's TestClient, never a real socket address
    ],
)
def test_a_request_that_arrived_on_this_pcs_allowed_addresses_is_served(lan_on, server):
    ran, _ = _run(_scope(server))
    assert ran, server


def test_no_local_address_fails_closed_only_when_the_network_is_open(lan_on, monkeypatch):
    ran, sent = _run(_scope(...))
    assert not ran and sent[0]["status"] == 403
    ran, _ = _run(_scope(None))
    assert not ran
    monkeypatch.delenv("IRONJARVIS_LAN_ACCESS")
    ran, _ = _run(_scope(...))
    assert ran, "loopback only: a missing local address changes nothing"


def test_loopback_only_changes_nothing(monkeypatch):
    """The default (the setting off): no marker, so the arrival address is
    never read — even an allowlist a server deployment set keeps working by
    the Host header alone, exactly as before."""
    monkeypatch.delenv("IRONJARVIS_LAN_ACCESS", raising=False)
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", LAN_IP)
    ran, _ = _run(_scope((OTHER_NETWORK_IP, 8787)))
    assert ran
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST")
    ran, _ = _run(_scope(("127.0.0.1", 8787), host_header="127.0.0.1:8787"))
    assert ran
    ran, sent = _run(_scope(("127.0.0.1", 8787), host_header="evil.example:8787"))
    assert not ran and sent[0]["status"] == 403, "the Host rule itself is unchanged"


def test_a_websocket_that_arrived_on_another_network_is_closed(lan_on):
    ran, sent = _run(_scope((OTHER_NETWORK_IP, 8787), kind="websocket"))
    assert not ran
    assert sent == [{"type": "websocket.close", "code": 1008}]
    ran, _ = _run(_scope((LAN_IP, 8787), kind="websocket"))
    assert ran


def test_a_name_the_user_added_to_the_allowlist_is_compared_as_written(monkeypatch):
    """The desktop KEEPS an allowlist the user set (names beside its
    addresses). A local address is always an address, so only the address
    entries can match it; an IPv6 entry matches in any spelling."""
    monkeypatch.setenv("IRONJARVIS_LAN_ACCESS", "1")
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", f"desk-pc, {LAN_IP}, [fd00::0:5]")
    ran, _ = _run(_scope(("fd00::5", 8787), host_header="desk-pc:8787"))
    assert ran
    ran, _ = _run(_scope((OTHER_NETWORK_IP, 8787), host_header="desk-pc:8787"))
    assert not ran


def test_end_to_end_the_install_token_does_not_open_another_networks_address(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", TOKEN)
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", LAN_IP)
    monkeypatch.setenv("IRONJARVIS_LAN_ACCESS", "1")
    app = create_app(str(tmp_path))
    auth = {"Host": f"{LAN_IP}:8787", "Authorization": f"Bearer {TOKEN}"}
    elsewhere = TestClient(app, base_url=f"http://{OTHER_NETWORK_IP}:8787", client=("10.0.0.9", 51000))
    res = elsewhere.get("/sessions", headers=auth)
    assert res.status_code == 403, res.status_code
    here = TestClient(app, base_url=f"http://{LAN_IP}:8787", client=("192.168.1.30", 51000))
    assert here.get("/sessions", headers=auth).status_code == 200
