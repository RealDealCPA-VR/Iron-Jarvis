"""A guard that keeps a test OFF THE NETWORK (readiness 12B review).

WHY: the first run of the 12B G15 fixture sent ONE real HTTPS request to
``api.githubcopilot.com/mcp/`` with a fake token (GitHub answered 401). The
stand-in patched ``catalog.get_connector`` but the Directory's Connect read
the same function through ANOTHER binding (``connectors.service``), so the
real catalog card — a remote MCP server — was dialled. A green test said
nothing about it.

THE GUARD refuses every connection that is not to this machine (loopback) and
every DNS lookup of a name other than ``localhost`` — the lookup itself leaves
the machine — at every door Python has for it:

* ``socket.getaddrinfo`` / ``gethostbyname`` / ``gethostbyname_ex`` (what
  ``socket.create_connection``, httpx, urllib and asyncio resolve through);
* ``socket.socket.connect`` / ``connect_ex`` (sync clients, the selector
  event loop, ``ssl``);
* the event loops' ``sock_connect`` (the Windows proactor connects with
  ``ConnectEx``, never through ``socket.connect``).

A refused attempt raises :class:`NetworkRefused` (an ``OSError``, so the code
under test sees what a dead network looks like) AND is RECORDED — code that
swallows the error (a pack that "didn't start") cannot hide it: the
``no_network`` fixture (tests/conftest.py) fails the test at teardown naming
every host it tried to reach.

Loopback stays open (the in-process test servers, asyncio's self-pipe, a local
MCP child's own sockets). Child processes are NOT covered — a stand-in child
must itself be local.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any, Callable

#: Names that resolve to this machine without asking anyone.
LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


class NetworkRefused(ConnectionRefusedError):
    """A test tried to leave this machine; the guard refused it."""


def _strip(host: Any) -> str:
    h = host.decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else str(host or "")
    h = h.strip().strip("[]")
    # An IPv6 zone id ("fe80::1%eth0") is not part of the address.
    return h.split("%", 1)[0] if ":" in h else h


def is_local_host(host: Any) -> bool:
    """True for a loopback address or a local name; False for anything a
    lookup or a connection would take off this machine."""
    h = _strip(host).lower().rstrip(".")
    if not h:
        return True  # "" / None: the wildcard (bind-style), never a remote peer
    if h in LOCAL_NAMES or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


def _is_numeric(host: Any) -> bool:
    try:
        ipaddress.ip_address(_strip(host))
        return True
    except ValueError:
        return False


class NetworkGuard:
    """Install with :meth:`install` (a ``pytest.MonkeyPatch``), read
    :attr:`attempts` / :meth:`report` after. ``real`` lets a test put a
    TRIPWIRE where the real primitives would be (the guard's own proof), so
    even a broken guard cannot reach anyone."""

    def __init__(self, real: "dict[str, Callable[..., Any]] | None" = None) -> None:
        self.attempts: list[str] = []
        self._lock = threading.Lock()
        self.real: dict[str, Callable[..., Any]] = {
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
        }
        self.real.update(real or {})

    # -- the record ------------------------------------------------------------
    def _refuse(self, what: str) -> NetworkRefused:
        with self._lock:
            if what not in self.attempts:
                self.attempts.append(what)
        return NetworkRefused(f"the test network guard refused {what} (tests never leave this machine)")

    def report(self) -> str:
        """"" when nothing tried to leave; else one sentence naming each host."""
        if not self.attempts:
            return ""
        return (
            "this test tried to reach the network — "
            + "; ".join(self.attempts)
            + " — a stand-in missed a binding (patch every module that holds the lookup) "
            "or a real service is being called"
        )

    # -- the doors -------------------------------------------------------------
    def _check_address(self, family: Any, address: Any, how: str) -> None:
        if family == getattr(socket, "AF_UNIX", object()):
            return
        if isinstance(address, tuple) and address:
            host, port = address[0], (address[1] if len(address) > 1 else "")
            if not is_local_host(host):
                raise self._refuse(f"{how} {_strip(host)}:{port}")

    def install(self, monkeypatch: Any) -> "NetworkGuard":
        guard = self
        real = self.real

        def getaddrinfo(host, port, *args, **kwargs):
            if host is not None and not is_local_host(host) and not _is_numeric(host):
                raise guard._refuse(f"a DNS lookup of {_strip(host)} (port {port})")
            return real["getaddrinfo"](host, port, *args, **kwargs)

        def gethostbyname(host):
            if not is_local_host(host) and not _is_numeric(host):
                raise guard._refuse(f"a DNS lookup of {_strip(host)}")
            return real["gethostbyname"](host)

        def gethostbyname_ex(host):
            if not is_local_host(host) and not _is_numeric(host):
                raise guard._refuse(f"a DNS lookup of {_strip(host)}")
            return real["gethostbyname_ex"](host)

        def connect(sock, address):
            guard._check_address(sock.family, address, "a connection to")
            return real["connect"](sock, address)

        def connect_ex(sock, address):
            guard._check_address(sock.family, address, "a connection to")
            return real["connect_ex"](sock, address)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
        monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

        # The event loops connect through their own path (the Windows
        # proactor uses ConnectEx, never socket.connect).
        from asyncio import proactor_events, selector_events

        for cls in (proactor_events.BaseProactorEventLoop, selector_events.BaseSelectorEventLoop):
            orig = cls.sock_connect

            def make(orig):
                async def sock_connect(loop, sock, address):
                    guard._check_address(sock.family, address, "a connection to")
                    return await orig(loop, sock, address)

                return sock_connect

            monkeypatch.setattr(cls, "sock_connect", make(orig))
        return self


__all__ = ["NetworkGuard", "NetworkRefused", "is_local_host", "LOCAL_NAMES"]
