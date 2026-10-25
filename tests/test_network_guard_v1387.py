"""Readiness 12B review: the test network guard (``tests/_fakes/no_network.py``)
trips on the patching that once reached GitHub.

THE BREACH: the first run of the 12B G15 fixture sent one real HTTPS request
to ``api.githubcopilot.com/mcp/`` (GitHub answered 401 to the fake token). Its
stand-in patched ``catalog.get_connector`` only; the Directory's Connect reads
the lookup through ``connectors.service``'s own binding, so the REAL GitHub
card — a remote MCP server — was dialled, and the test stayed green.

These pins replay that OLD patching with the guard on. The guard is installed
ON TOP OF a tripwire: a non-local call that got past the guard would hit the
tripwire, which refuses it and records a leak — so even a broken (or
mutated-away) guard sends nothing anywhere. Loopback goes through the real primitives (the app's own
event loop needs its self-pipe).
"""

from __future__ import annotations

import dataclasses
import json
import socket
import sys
from pathlib import Path

import pytest

from iron_jarvis.connectors import catalog
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import unload_pack_tools
from tests._fakes.no_network import NetworkGuard, NetworkRefused, is_local_host
from tests.test_chat_approvals_v1187 import _asgi_post

STANDIN = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
TOKEN = "ghp_GUARD12B0123456789abcdefNEVERSHOWN"


class _Tripwire:
    """Installed on the socket module BEFORE the guard, so the guard sits on
    top of it: loopback passes through to the true functions; anything else
    that gets past the guard is a LEAK (recorded, refused). A broken or
    mutated-away guard therefore still sends nothing anywhere."""

    def __init__(self) -> None:
        self.leaks: list[str] = []

    def _leak(self, what: str) -> OSError:
        self.leaks.append(what)
        return OSError(f"LEAK past the guard: {what}")

    def install(self, mp: pytest.MonkeyPatch) -> "_Tripwire":
        import ipaddress
        from asyncio import proactor_events, selector_events

        true_gai = socket.getaddrinfo

        def numeric(host) -> bool:
            try:
                ipaddress.ip_address(str(host).strip("[]"))
                return True
            except ValueError:
                return False

        def getaddrinfo(host, port, *a, **k):
            if host is not None and not is_local_host(host) and not numeric(host):
                raise self._leak(f"getaddrinfo {host}")
            return true_gai(host, port, *a, **k)

        def by_name(key):
            true = getattr(socket, key)

            def f(host):
                if not is_local_host(host):
                    raise self._leak(f"{key} {host}")
                return true(host)

            return f

        def connect(key):
            true = getattr(socket.socket, key)

            def f(sock, address):
                if isinstance(address, tuple) and address and not is_local_host(address[0]):
                    raise self._leak(f"{key} {address[0]}")
                return true(sock, address)

            return f

        mp.setattr(socket, "getaddrinfo", getaddrinfo)
        mp.setattr(socket, "gethostbyname", by_name("gethostbyname"))
        mp.setattr(socket, "gethostbyname_ex", by_name("gethostbyname_ex"))
        mp.setattr(socket.socket, "connect", connect("connect"))
        mp.setattr(socket.socket, "connect_ex", connect("connect_ex"))
        for cls in (proactor_events.BaseProactorEventLoop, selector_events.BaseSelectorEventLoop):
            orig = cls.sock_connect

            def make(orig):
                async def sock_connect(loop, sock, address):
                    if isinstance(address, tuple) and address and not is_local_host(address[0]):
                        raise self._leak(f"sock_connect {address[0]}")
                    return await orig(loop, sock, address)

                return sock_connect

            mp.setattr(cls, "sock_connect", make(orig))
        return self


@pytest.fixture
def guarded():
    mp = pytest.MonkeyPatch()
    try:
        trip = _Tripwire().install(mp)  # underneath: catches what the guard misses
        guard = NetworkGuard().install(mp)  # on top: its "real" is the tripwire
        yield guard, trip
    finally:
        mp.undo()


def _github_standin():
    real = catalog.get_connector("github")
    swapped = dataclasses.replace(
        real, command=sys.executable, args=[STANDIN], transport="stdio", url="", auth_field="",
    )
    original = catalog.get_connector

    def lookup(c):
        return swapped if c == "github" else original(c)

    return swapped, lookup


async def _connect_on_the_directory(tmp_path):
    app = create_app(str(tmp_path))
    try:
        status, raw = await _asgi_post(
            app, "/connectors/github/connect", {"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}},
        )
    finally:
        unload_pack_tools(app.state.platform.registry, "github")
    return status, raw


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_the_old_patching_trips_the_guard_and_names_github(tmp_path, monkeypatch, guarded):
    """The doer's FIRST stand-in: only ``catalog.get_connector`` swapped. The
    Directory's Connect reads ``connectors.service.get_connector`` — the real
    card — and dials GitHub's remote server. The guard refuses it and names
    the host; nothing reached the tripwire underneath."""
    guard, trip = guarded
    _swapped, lookup = _github_standin()
    monkeypatch.setattr(catalog, "get_connector", lookup)  # the OLD patching: one binding

    await _connect_on_the_directory(tmp_path)

    assert trip.leaks == [], f"a call got PAST the guard (the tripwire held it): {trip.leaks}"
    assert any("api.githubcopilot.com" in a for a in guard.attempts), guard.attempts
    assert "api.githubcopilot.com" in guard.report()


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_every_binding_patched_reaches_nothing(tmp_path, monkeypatch, guarded):
    """The control: the stand-in on EVERY binding (the doer's fix) launches
    the local child and the guard records nothing."""
    import iron_jarvis.connectors as connectors_pkg
    from iron_jarvis.connectors import service

    guard, trip = guarded
    _swapped, lookup = _github_standin()
    for mod in (catalog, service, connectors_pkg):
        monkeypatch.setattr(mod, "get_connector", lookup)

    status, raw = await _connect_on_the_directory(tmp_path)

    assert status == 200, raw
    assert guard.attempts == [] and trip.leaks == [], (guard.attempts, trip.leaks)
    assert TOKEN not in (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else json.dumps(raw))


def test_the_guard_refuses_each_door_and_records_it(guarded):
    guard, trip = guarded
    with pytest.raises(NetworkRefused):
        socket.getaddrinfo("api.githubcopilot.com", 443)
    with pytest.raises(NetworkRefused):
        socket.gethostbyname("example.com")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkRefused):
            s.connect(("140.82.112.21", 443))  # numeric: the connect door decides
        with pytest.raises(NetworkRefused):
            s.connect_ex(("8.8.8.8", 53))
    finally:
        s.close()
    assert [a.split(" (")[0] for a in guard.attempts] == [
        "a DNS lookup of api.githubcopilot.com",
        "a DNS lookup of example.com",
        "a connection to 140.82.112.21:443",
        "a connection to 8.8.8.8:53",
    ], guard.attempts
    assert trip.leaks == []


@pytest.mark.asyncio
async def test_the_event_loops_own_connect_is_guarded_too(guarded):
    """The Windows proactor connects with ConnectEx, never socket.connect —
    the loop's ``sock_connect`` is a door of its own."""
    import asyncio

    guard, trip = guarded
    loop = asyncio.get_running_loop()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    try:
        with pytest.raises(NetworkRefused):
            await loop.sock_connect(s, ("140.82.112.21", 443))
    finally:
        s.close()
    assert guard.attempts == ["a connection to 140.82.112.21:443"]
    assert trip.leaks == []


@pytest.mark.asyncio
async def test_loopback_stays_open(guarded):
    import asyncio

    guard, _trip = guarded

    async def handle(reader, writer):
        writer.write(b"hi")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("localhost", port)
        assert await reader.read(2) == b"hi"
        writer.close()

        def sync_read() -> bytes:  # a sync client, off the loop that serves it
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                return c.recv(2)

        assert await asyncio.to_thread(sync_read) == b"hi"
    finally:
        server.close()
        await server.wait_closed()
    assert guard.attempts == []


def test_the_report_names_each_host_once():
    g = NetworkGuard()
    assert g.report() == ""
    g._refuse("a DNS lookup of api.githubcopilot.com (port 443)")
    g._refuse("a DNS lookup of api.githubcopilot.com (port 443)")
    assert g.report().count("api.githubcopilot.com") == 1
    assert "missed a binding" in g.report()


@pytest.mark.parametrize(
    "host, local",
    [
        ("localhost", True), ("127.0.0.1", True), ("127.8.9.1", True), ("::1", True),
        ("[::1]", True), ("::ffff:127.0.0.1", True), ("api.localhost", True), ("", True),
        ("api.githubcopilot.com", False), ("10.0.0.5", False), ("192.168.1.2", False),
        ("100.66.161.52", False), ("8.8.8.8", False), ("localhost.example.com", False),
    ],
)
def test_only_this_machine_is_local(host, local):
    assert is_local_host(host) is local


def test_the_fixture_fails_a_test_that_reached_out():
    """The ``no_network`` fixture (tests/conftest.py) FAILS at teardown,
    naming the host, even when the code under test swallowed the refusal —
    a pack that "didn't start" never hides a breach again."""
    import os

    from tests import conftest

    if os.environ.get("IJ_TEST_NO_NETWORK") == "1":
        pytest.skip("the session guard is on: it would hold this test's own lookup")
    true_gai = socket.getaddrinfo
    gen = conftest.no_network.__wrapped__()
    guard = next(gen)
    try:
        socket.getaddrinfo("api.githubcopilot.com", 443)
    except OSError:
        pass  # the code under test swallowed it
    with pytest.raises(pytest.fail.Exception) as failed:
        next(gen)
    assert "api.githubcopilot.com" in str(failed.value), failed.value
    assert guard.attempts
    # The guard is gone after it: the next test starts clean.
    assert socket.getaddrinfo is true_gai


def test_the_12b_files_carry_the_guard():
    """Every pin of this lane runs with the guard on."""
    root = Path(__file__).parent
    for name in (
        "test_phone_one_line_v1387.py", "test_phone_apps_honest_v1387.py",
        "test_chat_waiting_where_you_are_v1387.py", "test_connected_elsewhere_v1387.py",
    ):
        src = (root / name).read_text(encoding="utf-8")
        assert 'pytestmark = pytest.mark.usefixtures("no_network")' in src, name
