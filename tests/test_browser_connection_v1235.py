"""Browser connection replacement: exactly one socket, and no command left hanging (v1.235.0, Ship 1).

D08 says exactly one extension connection may be live, and plan §7 spells out the
five-step replacement sequence — new socket becomes authoritative, old socket is told
``browser.connection_replaced``, **every in-flight command on it fails**, old socket
closes with 1000, new socket is told ``browser.ready``. The plan also names step 3 as
the one most likely to be missed, so it gets the most explicit test in this file.

Why a hung future is the worst available outcome, stated plainly because it is the
thing this file exists to prevent: a pending future that is neither resolved nor
failed means the ``await`` inside the tool never returns. The tool call does not
fail, so nothing is ledgered as failed; the chat turn does not finish, so no reply
arrives; and the user sees a spinner with no error anywhere in the application. That
is strictly worse than an error — an error names the browser and tells the model to
retry. ``test_an_in_flight_command_on_a_replaced_connection_fails_rather_than_hanging``
is therefore bounded by an explicit :func:`asyncio.timeout` that raises a *named*
assertion on expiry, so a regression is a red test in seconds instead of a hung
release gate.

The rest of the pending-future contract, each with its own silent failure:

* **A timeout REMOVES the future**, so a late reply is discarded. Delivering it would
  resolve a waiter that has already given up, and if a request id were ever reused it
  would answer the wrong question with the wrong page's data.
* **Concurrent in-flight commands are required** (§29 of the decision record) and
  nothing serialises them. Two commands answered out of order must each get their own
  answer; a backend that matched replies positionally would pass every single-command
  test and cross the results of two.
* **A dying socket fails its futures too**, with ``BROWSER_NOT_CONNECTED``. A browser
  that closes mid-command must produce an error rather than silence.
* **``MAX_FRAME_BYTES`` is enforced BEFORE ``json.loads``.** The decode runs on the
  daemon's single event loop, so an oversized frame is not a big object — it is every
  request in the app timing out, which the user reads as "Daemon offline" (v1.153.1).
  The spy on ``json.loads`` takes ``*args, **kw`` and calls through, per this
  repository's monkeypatch rule.

Four more properties, each added because a review found the behaviour real and the
pin missing (v1.235.0 fix wave):

* **The disconnect REASON is derived, and exactly one event is published per real
  disconnect.** The word used to be typed at the publish site, so a replaced browser
  published a second, false ``browser.disconnected {reason: "closed"}`` AFTER the new
  socket's ``browser.connected`` — the ledger blaming the user for a browser that was
  replaced, and the last event in the stream saying "not connected" over a working
  browser.
* **``last_error`` is the TRANSPORT's last fault, not the last command's.** It is what
  the card renders as an amber "Last problem: …", and a ``TAB_NOT_FOUND`` — a state
  this service documents as normal — used to leave it there permanently.
* **Access is read LIVE on every call**, driven against a REAL ``Config`` and a REAL
  ``BrowserRuntime``: the whole suite used to stay green with ``access()`` caching its
  first read, because only fakes exercised it.
* **Off means off, and the refusal is the DAEMON's.** A close cannot stop a browser —
  the add-on reconnects a second later — so a credentialled socket arriving while
  Browser access is off is held INERT: never authoritative, its frames discarded, and
  promoted in place if the user switches access back on.

No assertion in this file measures a wall clock. Ordering is asserted by comparing
positions in the recorded frame list, and every wait is bounded by a tick budget or
an ``asyncio.timeout`` whose expiry is reported as a named failure, never as a
duration. The route-level cases add two more bounded waits of the same kind —
:func:`wait_for` polls on the test thread while the daemon's own loop runs, and
:func:`close_code_of` reads a close code on a thread with a budget, because a
``TestClient`` websocket receive has no timeout and an unbounded one turns a
protocol bug into a hung release gate.

Note on scope: most cases here drive
:class:`~iron_jarvis.browser.extension_backend.ExtensionBackend` — which owns the
whole replacement sequence and the whole pending map — through its own public surface
with a stand-in socket, so nothing they assert can be made true or false by a route.
The cases that ARE about the route (``off`` enforcement, the Disconnect directive, the
pairing deadline, which pending ask the Pair button offers) drive the real
``/browser/ws`` with the real :class:`~tests._fakes.browser_peer.BrowserPeer`, a real
``PairingStore`` and a real ``Config``, because each of those defects lived in the
route and a backend-level test cannot see them. Credential decisions stay in
``tests/test_browser_auth_v1235.py``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.identity import PINNED_EXTENSION_ID, extension_origin
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import (
    ACCESS_INTERACTIVE,
    ACCESS_OFF,
    ACCESS_READ_ONLY,
    BrowserRuntime,
    min_access_for,
)
from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import open_db
from iron_jarvis.daemon.routes import browser as browser_routes

from tests._fakes.browser_peer import (
    FRAME_WAIT_ATTEMPTS,
    FRAME_WAIT_STEP_S,
    BrowserPeer,
)

from iron_jarvis.browser.extension_backend import (
    CLOSE_PROTOCOL_ERROR,
    DISCONNECT_REASONS,
    MAX_REFUSED_FRAMES,
    ExtensionBackend,
    ExtensionConnection,
)

#: Ticks a test will yield the loop while waiting for another task to reach a point.
#: A budget, not a deadline: the assertion that follows names what never happened.
#: Nothing asserts how many ticks were actually used.
WAIT_TICKS = 500

#: Seconds any single await in this file may block before the test fails BY NAME.
#: Generous enough that a loaded CI runner never trips it, finite so a regression
#: that reintroduces a hanging future fails the gate instead of pinning a core.
HANG_GUARD_S = 10.0


class FakeSocket:
    """A stand-in for Starlette's ``WebSocket``: records frames, records the close code.

    Not a mock — ``ExtensionConnection`` calls only ``send_json`` and ``close``, so a
    real object with those two methods runs exactly the code the daemon runs. The
    ordered ``frames`` list is what every ordering assertion in this file reads, which
    is why order is asserted by index rather than by timing.
    """

    def __init__(self, *, dead: bool = False) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None
        self.dead = dead

    async def send_json(self, frame: dict) -> None:
        if self.dead:
            raise ConnectionResetError("socket is gone")
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def types(self) -> list[str]:
        return [str(frame.get("type")) for frame in self.frames]

    def of_type(self, frame_type: str) -> list[dict]:
        return [frame for frame in self.frames if frame.get("type") == frame_type]


def paired_connection(**kw: Any) -> tuple[ExtensionConnection, FakeSocket]:
    """An already-authenticated connection, as the route builds one from a ``?token=``."""
    socket = FakeSocket(**kw)
    conn = ExtensionConnection(
        socket,
        extension_id="lgihfomaieifpnemakmpadmggjnoojmm",
        extension_version="1.235.0",
        host_permission=True,
        paired=True,
    )
    return conn, socket


async def wait_until(predicate, what: str) -> None:
    """Yield the loop until ``predicate()`` holds, then return; else fail by name.

    Bounded by :data:`WAIT_TICKS`. The bound exists because an ``asyncio`` handoff has
    no timeout of its own: without it a backend that never sends the frame would hang
    the release gate rather than fail a test, and nobody can tell a deadlock from a
    slow runner.
    """
    for _ in range(WAIT_TICKS):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"never happened: {what}")


# --------------------------------------------------------------------------- #
# The D08 replacement sequence
# --------------------------------------------------------------------------- #


async def test_a_second_authenticated_socket_becomes_authoritative():
    """The newer socket wins, and it wins BEFORE the old one is touched.

    Order matters: ``_conn`` is swapped first so a command racing in behind the
    replacement goes to the live socket, not to the one being closed.
    """
    backend = ExtensionBackend()
    first, first_socket = paired_connection()
    await backend.adopt(first)

    second, second_socket = paired_connection()
    replaced = await backend.adopt(second)

    assert replaced is first
    assert backend.connection is second
    assert backend.connected is True
    assert second_socket.of_type(P.FRAME_READY), "the new socket must be told it is active"
    assert second_socket.closed_with is None
    assert first_socket.closed_with == 1000


async def test_the_replaced_socket_is_told_before_it_is_closed():
    """``browser.connection_replaced`` must arrive on the wire, then the 1000 close.

    Asserted by position in the recorded frame list and by the recorded close code —
    never by timing. A close with no frame first leaves the add-on unable to tell a
    replacement from a crash, so it would reconnect and fight the browser that just
    took over.
    """
    backend = ExtensionBackend()
    first, first_socket = paired_connection()
    await backend.adopt(first)
    second, _second_socket = paired_connection()

    await backend.adopt(second)

    replaced_frames = first_socket.of_type(P.FRAME_CONNECTION_REPLACED)
    assert len(replaced_frames) == 1
    assert replaced_frames[0]["error"]["code"] == BrowserErrorCode.CONNECTION_REPLACED.value
    assert replaced_frames[0]["error"]["message"], "the frame must carry the remedy, not a bare code"
    assert first_socket.types()[-1] == P.FRAME_CONNECTION_REPLACED, (
        "the replacement frame is the last thing sent on the outgoing socket"
    )
    assert first_socket.closed_with == 1000
    assert first.closed is True


async def test_an_in_flight_command_on_a_replaced_connection_fails_rather_than_hanging():
    """THE case (plan §7, step 5). A pending command must fail, not hang.

    Driven honestly: the command is really in flight — the command frame is on the
    wire and the add-on has deliberately not answered — before the replacement
    happens. The await is bounded by :data:`HANG_GUARD_S` and expiry is reported as a
    NAMED assertion, because a hung future here means a tool call that never returns:
    no ledger row, no reply, and a spinner with no error anywhere in the app.
    """
    backend = ExtensionBackend()
    first, first_socket = paired_connection()
    await backend.adopt(first)
    task = asyncio.ensure_future(backend.command(P.METHOD_LIST_TABS))
    await wait_until(
        lambda: bool(first_socket.of_type(P.FRAME_COMMAND)),
        "the command frame reached the browser",
    )
    assert len(first.pending) == 1, "the command must really be awaiting an answer"

    second, _second_socket = paired_connection()
    await backend.adopt(second)

    try:
        async with asyncio.timeout(HANG_GUARD_S):
            with pytest.raises(BrowserError) as caught:
                await task
    except TimeoutError:
        task.cancel()
        raise AssertionError(
            "the in-flight command HUNG across a connection replacement: the future was "
            "neither resolved nor failed, so the tool call would never return"
        ) from None

    assert caught.value.code == BrowserErrorCode.CONNECTION_REPLACED.value
    assert "retry" in caught.value.message.lower(), "the model must be told what to do next"
    assert first.pending == {}, "the replaced connection must hold no futures afterwards"


async def test_several_in_flight_commands_all_fail_on_replacement():
    """Concurrent commands are required, so ALL of them must fail — not just the first."""
    backend = ExtensionBackend()
    first, first_socket = paired_connection()
    await backend.adopt(first)
    tasks = [
        asyncio.ensure_future(backend.command(method))
        for method in (P.METHOD_LIST_TABS, P.METHOD_ACTIVE_TAB, P.METHOD_STATUS)
    ]
    await wait_until(
        lambda: len(first_socket.of_type(P.FRAME_COMMAND)) == 3,
        "all three command frames reached the browser",
    )

    second, _second_socket = paired_connection()
    await backend.adopt(second)

    async with asyncio.timeout(HANG_GUARD_S):
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(results) == 3
    for outcome in results:
        assert isinstance(outcome, BrowserError)
        assert outcome.code == BrowserErrorCode.CONNECTION_REPLACED.value


async def test_the_replacement_publishes_a_disconnect_and_a_connect():
    """The bus tells the story: the old one went (``replaced``), the new one arrived."""
    published: list[tuple[str, dict]] = []

    class RecordingBus:
        async def publish(self, name, payload=None, session_id=None):
            published.append((name, dict(payload or {})))

    backend = ExtensionBackend(event_bus=RecordingBus())
    first, _first_socket = paired_connection()
    await backend.adopt(first)
    second, _second_socket = paired_connection()

    await backend.adopt(second)

    names = [name for name, _ in published]
    assert names.count("browser.connected") == 2
    assert "browser.disconnected" in names
    reason = next(payload for name, payload in published if name == "browser.disconnected")
    assert reason["reason"] == "replaced", "a replacement must not be reported as a plain close"


async def test_re_adopting_the_same_connection_neither_replaces_nor_closes_it():
    """Idempotence: a re-adopt (a reconnect race, a retried handshake) must not self-destruct.

    Without the identity check the socket would be sent ``connection_replaced``, have
    its own in-flight commands failed, and be closed — by adopting itself.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    replaced = await backend.adopt(conn)

    assert replaced is None
    assert conn.closed is False and socket.closed_with is None
    assert socket.of_type(P.FRAME_CONNECTION_REPLACED) == []
    assert backend.connected is True


# --------------------------------------------------------------------------- #
# The pending-future map: the whole concurrency mechanism
# --------------------------------------------------------------------------- #


async def test_concurrent_commands_are_answered_out_of_order_and_still_match():
    """Two commands in flight, answered back to front, each get their own result.

    A backend that matched replies positionally rather than by request id would pass
    every single-command test in this suite and silently hand one page's data to the
    other call.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    first = asyncio.ensure_future(backend.command(P.METHOD_LIST_TABS))
    second = asyncio.ensure_future(backend.command(P.METHOD_ACTIVE_TAB))
    await wait_until(
        lambda: len(socket.of_type(P.FRAME_COMMAND)) == 2, "both commands reached the browser"
    )
    sent = socket.of_type(P.FRAME_COMMAND)
    assert sent[0]["method"] == P.METHOD_LIST_TABS and sent[1]["method"] == P.METHOD_ACTIVE_TAB
    assert sent[0]["id"] != sent[1]["id"]
    assert all(frame["id"].startswith(P.REQUEST_ID_PREFIX) for frame in sent)

    await backend.handle_frame(conn, P.response_frame(sent[1]["id"], {"id": 7, "title": "second"}))
    await backend.handle_frame(conn, P.response_frame(sent[0]["id"], {"tabs": [], "count": 0}))

    async with asyncio.timeout(HANG_GUARD_S):
        assert await second == {"id": 7, "title": "second"}
        assert await first == {"tabs": [], "count": 0}


async def test_a_failed_response_raises_the_add_ons_own_code_and_words():
    """The page knows why it failed; that message reaches the model verbatim."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    task = asyncio.ensure_future(backend.command(P.METHOD_ACTIVE_TAB))
    await wait_until(lambda: bool(socket.of_type(P.FRAME_COMMAND)), "the command was sent")
    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]

    await backend.handle_frame(
        conn, P.error_response_frame(request_id, BrowserErrorCode.TAB_NOT_FOUND, tab_id=42)
    )

    async with asyncio.timeout(HANG_GUARD_S):
        with pytest.raises(BrowserError) as caught:
            await task
    assert caught.value.code == BrowserErrorCode.TAB_NOT_FOUND.value
    assert "42" in caught.value.message, "the remedy must name the tab the model asked for"
    assert conn.pending == {}


async def test_a_timeout_removes_the_future_so_a_late_reply_is_discarded():
    """The bound is honoured, the future is dropped, and the late answer changes nothing.

    ``timeout_s`` is passed explicitly and tiny; nothing here asserts how long the
    wait took. Delivering the late reply would resolve a waiter that has already given
    up — and with a reused id, answer the wrong question.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    with pytest.raises(BrowserError) as caught:
        await backend.command(P.METHOD_LIST_TABS, timeout_s=0.01)

    assert caught.value.code == BrowserErrorCode.ACTION_TIMEOUT.value
    assert conn.pending == {}, "a timed-out command must not leave a future behind"
    assert backend.last_error, "the timeout must be nameable on GET /browser/status"
    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]
    # The late reply the add-on eventually sends: accepted by the handler, delivered
    # to nobody, and it must not raise.
    await backend.handle_frame(conn, P.response_frame(request_id, {"tabs": [{"id": 1}]}))
    assert conn.pending == {}


async def test_a_response_for_an_unknown_id_is_ignored():
    """A stray or duplicated response must not raise inside the socket handler."""
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    assert await backend.handle_frame(conn, P.response_frame("req_does_not_exist", {"x": 1})) is True


async def test_releasing_a_connection_fails_its_pending_commands():
    """A browser that goes away mid-command produces an error, not silence."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    task = asyncio.ensure_future(backend.command(P.METHOD_STATUS))
    await wait_until(lambda: bool(socket.of_type(P.FRAME_COMMAND)), "the command was sent")

    await backend.release(conn, reason="closed", detail="the browser window closed")

    async with asyncio.timeout(HANG_GUARD_S):
        with pytest.raises(BrowserError) as caught:
            await task
    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert backend.connected is False
    assert backend.connection is None


async def test_a_command_on_a_dead_socket_refuses_instead_of_waiting():
    """A send that fails is ``BROWSER_NOT_CONNECTED`` at once, with no future left behind."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    socket.dead = True  # the browser went away between the handshake and the call

    with pytest.raises(BrowserError) as caught:
        await backend.command(P.METHOD_LIST_TABS)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert conn.pending == {}


async def test_no_command_is_possible_with_no_connection():
    """The refusal names the browser and tells the user to pair, per D15."""
    backend = ExtensionBackend()

    with pytest.raises(BrowserError) as caught:
        await backend.command(P.METHOD_LIST_TABS)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert "pair" in caught.value.message.lower()


# --------------------------------------------------------------------------- #
# The frame-size cap is enforced BEFORE the JSON decode
# --------------------------------------------------------------------------- #


async def test_an_oversized_frame_is_refused_before_json_loads(monkeypatch):
    """``MAX_FRAME_BYTES`` is measured first; ``json.loads`` is never reached.

    The decode runs on the daemon's single event loop, so parsing an oversized frame
    is not a big object — it is every request in the app timing out while the loop is
    busy, which the user reads as "Daemon offline" (v1.153.1). The spy takes
    ``*args, **kw`` and calls through, per this repository's monkeypatch rule.
    """
    calls: list[int] = []
    real_loads = json.loads

    def spy(*args, **kw):
        calls.append(len(args[0]) if args else 0)
        return real_loads(*args, **kw)

    monkeypatch.setattr(json, "loads", spy)
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    oversized = json.dumps({"type": P.FRAME_RESPONSE, "id": "req_1", "success": True,
                            "result": {"text": "x" * (P.MAX_FRAME_BYTES + 1000)}})
    assert len(oversized.encode("utf-8")) > P.MAX_FRAME_BYTES

    keep = await backend.handle_raw(conn, oversized)

    assert calls == [], "the oversized frame was parsed; the cap must be checked BEFORE json.loads"
    assert keep is True, "an oversized frame is dropped, not a reason to kill the connection"
    assert str(P.MAX_FRAME_BYTES) in backend.last_error
    assert socket.closed_with is None


async def test_a_frame_within_the_cap_is_parsed_and_handled(monkeypatch):
    """The mirror of the case above: a normal frame really does go through the decode."""
    seen: list[str] = []
    real_loads = json.loads

    def spy(*args, **kw):
        seen.append("loads")
        return real_loads(*args, **kw)

    monkeypatch.setattr(json, "loads", spy)
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    task = asyncio.ensure_future(backend.command(P.METHOD_LIST_TABS))
    await wait_until(lambda: bool(socket.of_type(P.FRAME_COMMAND)), "the command was sent")
    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]

    assert await backend.handle_raw(conn, json.dumps(P.response_frame(request_id, {"tabs": []}))) is True

    assert seen, "a normal frame must be parsed"
    async with asyncio.timeout(HANG_GUARD_S):
        assert await task == {"tabs": []}


async def test_a_frame_that_is_not_json_is_named_not_crashed():
    """Garbage from the add-on is recorded and dropped; the socket survives."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    assert await backend.handle_raw(conn, "{not json") is True
    assert "JSON" in backend.last_error
    assert await backend.handle_raw(conn, json.dumps([1, 2, 3])) is True
    assert "object" in backend.last_error
    assert socket.closed_with is None


# --------------------------------------------------------------------------- #
# Hello, events, and the status view the card reads
# --------------------------------------------------------------------------- #


async def test_hello_records_the_add_on_and_re_announces_only_a_changed_fact():
    """``browser.connected`` must not carry a stale ``host_permission``, or repeat itself."""
    published: list[tuple[str, dict]] = []

    class RecordingBus:
        async def publish(self, name, payload=None, session_id=None):
            published.append((name, dict(payload or {})))

    backend = ExtensionBackend(event_bus=RecordingBus())
    socket = FakeSocket()
    conn = ExtensionConnection(socket, paired=True)
    await backend.adopt(conn)
    assert [name for name, _ in published] == ["browser.connected"]

    await backend.handle_frame(
        conn, P.hello_frame("lgihfomaieifpnemakmpadmggjnoojmm", "1.235.0", True)
    )
    assert conn.hello_seen is True
    assert conn.host_permission is True
    assert backend.status()["host_permission"] is True
    assert [name for name, _ in published] == ["browser.connected", "browser.connected"]

    await backend.handle_frame(
        conn, P.hello_frame("lgihfomaieifpnemakmpadmggjnoojmm", "1.235.0", True)
    )
    assert len(published) == 2, "an identical hello must not re-announce the same facts"


async def test_a_tab_activated_event_is_published_and_cached():
    """The cached active tab is what a later prompt names, so no prompt awaits a browser."""
    published: list[tuple[str, dict]] = []

    class RecordingBus:
        async def publish(self, name, payload=None, session_id=None):
            published.append((name, dict(payload or {})))

    backend = ExtensionBackend(event_bus=RecordingBus())
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.handle_frame(
        conn,
        P.event_frame("evt_1", P.EVENT_TAB_ACTIVATED, {"tab_id": 42, "title": "IRS", "url": "https://irs.gov/"}),
    )

    assert ("browser.tab_activated", {"tab_id": 42, "title": "IRS", "url": "https://irs.gov/"}) in published
    assert backend.status()["active_tab"]["title"] == "IRS"


async def test_an_unknown_event_name_is_named_not_published_under_a_guess():
    """An event published under a name nothing subscribes to is invisible; say so instead."""
    published: list[str] = []

    class RecordingBus:
        async def publish(self, name, payload=None, session_id=None):
            published.append(name)

    backend = ExtensionBackend(event_bus=RecordingBus())
    conn, _socket = paired_connection()
    await backend.adopt(conn)
    published.clear()

    await backend.handle_frame(conn, P.event_frame("evt_9", "something_new", {}))

    assert published == []
    assert "something_new" in backend.last_error


async def test_a_bus_failure_never_breaks_the_socket():
    """The v1.229.0 lesson: the call that was meant to RECORD a failure became the failure."""

    class BrokenBus:
        async def publish(self, name, payload=None, session_id=None):
            raise RuntimeError("the bus is wedged")

    backend = ExtensionBackend(event_bus=BrokenBus())
    conn, socket = paired_connection()

    await backend.adopt(conn)

    assert backend.connected is True
    assert socket.of_type(P.FRAME_READY), "the add-on is still told it is active"


async def test_status_never_touches_the_browser():
    """The status view is where the user finds out the add-on is wedged; it cannot wait on it."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    view = backend.status()

    assert socket.of_type(P.FRAME_COMMAND) == [], "status must send no command"
    assert view["connected"] is True
    assert view["extension_id"] == "lgihfomaieifpnemakmpadmggjnoojmm"
    assert view["extension_version"] == "1.235.0"
    assert view["in_flight"] == 0
    assert view["last_error"] is None
    assert backend.status()["connected_at"], "the card shows when this browser connected"


async def test_request_ids_are_unique_across_the_daemon_run():
    """Ids key the pending map, so a repeat would resolve the wrong future."""
    backend = ExtensionBackend()

    minted = [backend.next_request_id() for _ in range(50)]

    assert len(set(minted)) == 50
    assert all(value.startswith(P.REQUEST_ID_PREFIX) for value in minted)
    assert not any(value.startswith(P.EVENT_ID_PREFIX) for value in minted), (
        "a command id must never look like an event id, or a stray event resolves a command"
    )


# --------------------------------------------------------------------------- #
# The disconnect reason is DERIVED, and exactly one event is published
# --------------------------------------------------------------------------- #


class RecordingBus:
    """Every publish, in order. The event stream is the thing under test here."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, name, payload=None, session_id=None):
        self.published.append((name, dict(payload or {})))

    def of(self, name: str) -> list[dict]:
        return [payload for published, payload in self.published if published == name]

    def names(self) -> list[str]:
        return [name for name, _ in self.published]


async def test_a_replaced_socket_publishes_exactly_one_disconnect_and_it_says_replaced():
    """The reason is DERIVED from what happened, not taken from the caller's string.

    The bug this pins, proven end to end before it was fixed: the outgoing socket's
    read loop reaches its ``finally`` with the local default ``reason="closed"`` and
    calls ``release``, so the bus recorded ``browser.connected``,
    ``browser.disconnected reason=replaced``, ``browser.connected`` -- and then a
    SECOND ``browser.disconnected reason=closed``, after the new browser was already
    live. Two lies in one event: the ledger says the user closed a browser that was
    replaced, and the last event in the stream is a disconnect while a browser works,
    so any consumer deriving connection state from events renders "not connected" over
    a working browser.

    Driven the way the route drives it -- ``adopt`` for the new socket, then the old
    socket's own ``release`` from its pump's ``finally`` -- because the duplicate only
    appears in that order.
    """
    bus = RecordingBus()
    backend = ExtensionBackend(event_bus=bus)
    first, _first_socket = paired_connection()
    await backend.adopt(first)
    second, _second_socket = paired_connection()

    await backend.adopt(second)
    # What routes/browser.py::_pump does in its finally for the socket that lost.
    await backend.release(first, reason="closed", detail="")

    disconnects = bus.of("browser.disconnected")
    assert len(disconnects) == 1, (
        f"a replacement published {len(disconnects)} browser.disconnected events; "
        f"exactly one real disconnect happened. Stream: {bus.names()}"
    )
    assert disconnects[0]["reason"] == "replaced"
    assert bus.names()[-1] == "browser.connected", (
        "the last event must not be a disconnect while a browser is live: a consumer "
        "deriving connection state from the stream would render 'not connected'"
    )
    assert backend.connected is True and backend.connection is second


async def test_the_disconnect_reason_survives_being_released_twice():
    """``release`` is called twice on the ordinary path, and must announce once.

    ``POST /browser/disconnect`` releases the live socket, and the pump's ``finally``
    then releases the same object again. The second call must publish nothing -- an
    extra ``browser.disconnected`` is a second entry in the browser action ledger for
    one thing that happened.
    """
    bus = RecordingBus()
    backend = ExtensionBackend(event_bus=bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="revoked", detail="pairing forgotten")
    await backend.release(conn, reason="closed", detail="")

    assert bus.of("browser.disconnected") == [
        {"reason": "revoked", "detail": "pairing forgotten"}
    ], f"one disconnect, with the reason the caller had a right to name. Got {bus.names()}"


async def test_a_socket_that_was_never_authoritative_announces_nothing():
    """No connect was published for it, so no disconnect may be either.

    A restricted pairing socket, and (since access-off enforcement) a credentialled
    socket the daemon refused to activate, both reach ``release`` when they close. A
    ``browser.disconnected`` for a browser that was never reported connected tells the
    card and the ledger that something went away that was never there.
    """
    bus = RecordingBus()
    backend = ExtensionBackend(event_bus=bus)
    socket = FakeSocket()
    unpaired = ExtensionConnection(socket, pairing_request_id="pair_abc")
    backend.register_restricted(unpaired)

    await backend.release(unpaired, reason="closed", detail="the pairing deadline passed")

    assert bus.published == []
    assert backend.restricted_socket("pair_abc") is None, "the offer must be withdrawn"


async def test_an_unknown_reason_word_becomes_closed():
    """The vocabulary is closed, so a typo cannot invent a reason the card cannot read."""
    bus = RecordingBus()
    backend = ExtensionBackend(event_bus=bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="went-away-i-guess", detail="")

    assert bus.of("browser.disconnected")[0]["reason"] == "closed"
    assert all(
        payload.get("reason") in DISCONNECT_REASONS
        for payload in bus.of("browser.disconnected")
    )


# --------------------------------------------------------------------------- #
# last_error is the TRANSPORT's last fault, not the last command's
# --------------------------------------------------------------------------- #


async def test_an_add_on_reported_command_failure_is_not_a_card_problem():
    """``TAB_NOT_FOUND`` must leave the card clean: it is a STATE, not a fault.

    ``BrowserRuntime.active_tab`` documents "a browser with no window open is a state,
    not a failure" and maps this very code to ``None`` -- while the backend was writing
    it into ``last_error``, which ``GET /browser/status`` hands the Your browser card
    and the card renders as an amber "Last problem: ..." that nothing but a reconnect
    cleared. Steady state on a working install was therefore a permanent problem
    banner. The add-on's words are not lost: they ride the raised ``BrowserError`` to
    the tool result the model reads.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    task = asyncio.ensure_future(backend.command(P.METHOD_ACTIVE_TAB))
    await wait_until(lambda: bool(socket.of_type(P.FRAME_COMMAND)), "the command was sent")
    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]

    await backend.handle_frame(
        conn, P.error_response_frame(request_id, BrowserErrorCode.TAB_NOT_FOUND, tab_id=0)
    )

    async with asyncio.timeout(HANG_GUARD_S):
        with pytest.raises(BrowserError) as caught:
            await task
    assert caught.value.code == BrowserErrorCode.TAB_NOT_FOUND.value, (
        "the model must still be told exactly what the page said"
    )
    assert backend.status()["last_error"] is None, (
        "a normal per-command refusal must not leave a permanent amber problem on the card"
    )


async def test_a_delivered_answer_clears_a_stale_transport_problem():
    """"Last problem" means the last thing that went wrong is STILL TRUE.

    A refused frame is a real fault and is recorded. A later command that the browser
    answers proves the transport works again, so the record is retired -- otherwise the
    card keeps accusing a browser that has been fine for hours.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    assert await backend.handle_raw(conn, "{not json") is True
    assert backend.status()["last_error"], "a refused frame is a real fault and is recorded"

    task = asyncio.ensure_future(backend.command(P.METHOD_LIST_TABS))
    await wait_until(lambda: bool(socket.of_type(P.FRAME_COMMAND)), "the command was sent")
    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]
    await backend.handle_frame(conn, P.response_frame(request_id, {"tabs": []}))

    async with asyncio.timeout(HANG_GUARD_S):
        assert await task == {"tabs": []}
    assert backend.status()["last_error"] is None, (
        "a successful round trip retires the transport fault it disproves"
    )


async def test_a_late_reply_with_no_waiter_clears_nothing():
    """Only a DELIVERED answer is evidence; a reply nobody was waiting for is not.

    The distinction matters because the late reply arrives after our own timeout --
    exactly the fault the card is showing.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)
    with pytest.raises(BrowserError):
        await backend.command(P.METHOD_LIST_TABS, timeout_s=0.01)
    timed_out = backend.last_error
    assert timed_out

    request_id = socket.of_type(P.FRAME_COMMAND)[0]["id"]
    await backend.handle_frame(conn, P.response_frame(request_id, {"tabs": [{"id": 1}]}))

    assert backend.last_error == timed_out


# --------------------------------------------------------------------------- #
# Refused frames are counted, and the recorded close code is the one SENT
# --------------------------------------------------------------------------- #


async def test_a_stream_of_unreadable_frames_closes_the_socket():
    """One bad frame is a bug on the other side; a stream of them is a log-flood.

    ``/browser/ws?pairing=1`` needs no credential at all, so any local process can
    open one and write a WARNING to ``daemon.log`` per frame at line rate. The socket
    survives the first refusals (an add-on with one bad frame must not lose its
    connection) and closes at ``MAX_REFUSED_FRAMES`` with 1002 -- NOT 1008, which the
    add-on reads as "this credential is refused" and answers by deleting its stored
    pairing token, making a garbage frame cost the user a re-pair.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    for _ in range(MAX_REFUSED_FRAMES - 1):
        assert await backend.handle_raw(conn, "{not json") is True
    assert socket.closed_with is None, "the socket must survive a handful of bad frames"

    assert await backend.handle_raw(conn, "{not json") is False
    assert socket.closed_with == CLOSE_PROTOCOL_ERROR
    assert conn.close_code == CLOSE_PROTOCOL_ERROR
    assert str(MAX_REFUSED_FRAMES) in backend.last_error, (
        "the card must be able to say how many frames were refused"
    )


async def test_one_readable_frame_forgives_the_refusals_before_it():
    """The bound is on CONSECUTIVE refusals: a working add-on is never accumulating."""
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    for _ in range(MAX_REFUSED_FRAMES - 1):
        await backend.handle_raw(conn, "{not json")
    assert (
        await backend.handle_raw(conn, json.dumps(P.hello_frame("id", "1.235.0", True))) is True
    )
    for _ in range(MAX_REFUSED_FRAMES - 1):
        assert await backend.handle_raw(conn, "{not json") is True

    assert socket.closed_with is None
    assert conn.refused_frames == MAX_REFUSED_FRAMES - 1


class UnencodableStr(str):
    """A ``str`` that refuses to be encoded, so a copy is a NAMED failure.

    The only honest way to assert "measured without being copied": an efficiency claim
    is invisible to an assertion, but the encode itself is observable.
    """

    def encode(self, *args, **kw):  # noqa: D102 - the assertion IS the behaviour
        raise AssertionError(
            "the oversized frame was encoded before it was measured: with uvicorn's "
            "16 MB ws_max_size that copy runs on the daemon's single event loop"
        )


async def test_an_oversized_text_frame_is_measured_without_being_encoded():
    """The cheap upper bound first: N characters is at least N UTF-8 bytes.

    Starlette hands text frames through as ``str``, and encoding before comparing made
    the loop build the whole copy of a frame it was about to refuse -- a smaller
    version of the v1.153.1 shape the cap exists to prevent.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    keep = await backend.handle_raw(conn, UnencodableStr("x" * (P.MAX_FRAME_BYTES + 1)))

    assert keep is True, "an oversized frame is dropped, not a reason to kill the connection"
    assert str(P.MAX_FRAME_BYTES) in backend.last_error
    assert socket.closed_with is None


async def test_the_recorded_close_code_is_the_one_the_daemon_SENT():
    """A policy close followed by the teardown's 1000 must still read as the policy code.

    The pairing-deadline and protocol-violation paths close with 1008, and the route's
    ``finally`` then calls ``release``, which closes with 1000. Recording the second,
    never-sent code makes the first diagnostic that surfaces this field report a
    protocol-violating add-on as a clean disconnect.
    """
    backend = ExtensionBackend()
    socket = FakeSocket()
    conn = ExtensionConnection(socket, pairing_request_id="pair_xyz")

    await conn.close(1008)
    await backend.release(conn, reason="closed", detail="the pairing deadline passed")

    assert conn.close_code == 1008
    assert socket.closed_with == 1008, "the second close must not reach the socket either"


# --------------------------------------------------------------------------- #
# One awaiting path: a directive is a command's twin and must not drift
# --------------------------------------------------------------------------- #


async def test_a_directive_in_flight_on_a_replaced_connection_fails_too():
    """``directive`` shares the pending map, so it shares the D08 guarantee.

    It was a near-verbatim copy of ``command`` -- 35 lines that had to stay in lock
    step, with nothing exercising this half at all, while it carries the
    host-permission grant and Disconnect. Both now go through one ``_await_response``,
    and this is the case that proves the shared path is really shared.
    """
    backend = ExtensionBackend()
    first, first_socket = paired_connection()
    await backend.adopt(first)
    task = asyncio.ensure_future(backend.directive(P.DIRECTIVE_REQUEST_HOST_PERMISSIONS))
    await wait_until(
        lambda: bool(first_socket.of_type(P.FRAME_DIRECTIVE)), "the directive frame was sent"
    )
    assert len(first.pending) == 1, "the directive must really be awaiting an answer"

    second, _second_socket = paired_connection()
    await backend.adopt(second)

    async with asyncio.timeout(HANG_GUARD_S):
        with pytest.raises(BrowserError) as caught:
            await task
    assert caught.value.code == BrowserErrorCode.CONNECTION_REPLACED.value
    assert first.pending == {}


async def test_a_directive_timeout_names_the_directive_not_a_method():
    """The two callers keep their own words for a timeout, on the one shared path."""
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    with pytest.raises(BrowserError) as caught:
        await backend.directive(P.DIRECTIVE_DISCONNECT, timeout_s=0.01)

    assert caught.value.code == BrowserErrorCode.ACTION_TIMEOUT.value
    assert "directive" in backend.last_error and P.DIRECTIVE_DISCONNECT in backend.last_error


async def test_a_directive_needs_a_live_paired_socket():
    """No socket, no directive: the same refusal ``command`` gives, from one place."""
    backend = ExtensionBackend()

    with pytest.raises(BrowserError) as caught:
        await backend.directive(P.DIRECTIVE_DISCONNECT)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value


# --------------------------------------------------------------------------- #
# Access is read LIVE on every call -- the load-bearing property of plan 5.2
# --------------------------------------------------------------------------- #
#
# Driven against a REAL ``Config`` and a REAL ``BrowserRuntime``. It had no pin at
# all: the whole browser suite stayed green with ``BrowserRuntime.access`` caching its
# first read, because the only tests that exercised access drove a fake runtime with
# its own ``_access`` attribute, and the two that built a real runtime never changed
# the setting. A doer who "optimises" the getattr into a value set in ``__init__``
# ships a green gate and a browser that keeps reading the user's tabs until the daemon
# restarts.


class SpyBackend:
    """A transport that records what reached it. The GATE is what is under test.

    Not a stand-in for the protocol: the assertions below are about which calls cross
    into the transport at all, and about the code raised when one does not, so a real
    backend would only add a socket to the test without adding a property to it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.access_reader: Any = None
        self.connection: Any = None

    @property
    def connected(self) -> bool:
        return True

    async def command(self, method, params=None, *, timeout_s=None):
        self.calls.append(method)
        return {"tabs": []}

    async def directive(self, action, params=None, *, timeout_s=None):
        self.calls.append(f"directive:{action}")
        return {}

    def status(self) -> dict[str, Any]:
        return {"connected": True, "last_error": None}


def _real_runtime(tmp_path) -> tuple[Any, SpyBackend, Any]:
    """A real ``BrowserRuntime`` over a real ``Config`` read from ``tmp_path``."""
    config = load_config(tmp_path)
    backend = SpyBackend()
    return BrowserRuntime(backend=backend, config=config), backend, config


async def test_browser_access_defaults_to_off_on_a_real_config(tmp_path):
    """A fresh install refuses: the capability is opt-in (D09)."""
    runtime, backend, _config = _real_runtime(tmp_path)

    assert runtime.access() == ACCESS_OFF
    with pytest.raises(BrowserError) as caught:
        await runtime.list_tabs()
    assert caught.value.code == BrowserErrorCode.BROWSER_ACCESS_OFF.value
    assert backend.calls == [], "nothing may cross to the browser while access is off"


async def test_access_is_read_live_off_the_config_object(tmp_path):
    """The NEXT call sees a setting changed on the live object, in both directions.

    ``PUT /settings`` mutates this very object (``validate_assignment=True``), so the
    read must happen per call. The first read comes BEFORE the mutation deliberately:
    a runtime that cached its first answer would pass a test that only read after.
    """
    runtime, backend, config = _real_runtime(tmp_path)
    assert runtime.access() == ACCESS_OFF  # populate any cache a mutation might add

    config.browser_access = ACCESS_INTERACTIVE
    assert runtime.access() == ACCESS_INTERACTIVE
    assert await runtime.list_tabs() == []
    assert backend.calls == [P.METHOD_LIST_TABS], "the gate must let the call through now"

    config.browser_access = ACCESS_OFF
    assert runtime.access() == ACCESS_OFF
    with pytest.raises(BrowserError) as caught:
        await runtime.list_tabs()
    assert caught.value.code == BrowserErrorCode.BROWSER_ACCESS_OFF.value
    assert backend.calls == [P.METHOD_LIST_TABS], (
        "the capability the user just switched off must stop at once, not at the next "
        "daemon restart"
    )


async def test_read_only_refuses_an_acting_method_live(tmp_path):
    """``read_only`` reads and refuses to act, and the change lands on the next call."""
    runtime, backend, config = _real_runtime(tmp_path)
    config.browser_access = ACCESS_INTERACTIVE
    assert runtime.require(min_access_for("navigate")) == ACCESS_INTERACTIVE

    config.browser_access = ACCESS_READ_ONLY

    assert runtime.require(min_access_for(P.METHOD_LIST_TABS)) == ACCESS_READ_ONLY
    with pytest.raises(BrowserError) as caught:
        await runtime.command("navigate", {"tab_id": 1, "url": "https://example.com/"})
    assert caught.value.code == BrowserErrorCode.READ_ONLY_MODE.value
    assert "navigate" not in backend.calls


async def test_an_unreadable_access_setting_fails_closed(tmp_path):
    """A config object with no such field is ``off``, never the widest level.

    An older ``config.toml`` (or an older test helper) simply has no
    ``browser_access``. Fail-closed is the only safe direction: the alternative is a
    missing setting reading as ``interactive`` on the box that holds tax documents.
    """
    backend = SpyBackend()
    runtime = BrowserRuntime(backend=backend, config=object())

    assert runtime.access() == ACCESS_OFF
    with pytest.raises(BrowserError):
        await runtime.list_tabs()


async def test_the_ready_frame_carries_the_live_access_word(tmp_path):
    """``browser.ready`` stamps the mode, read at SEND time (plan 5.2 + the popup).

    The add-on's own panel had a dead "Access" row because the daemon never told it
    the mode. The value is the same string ``GET /browser/status`` reports, and it is
    read when the frame is built -- a socket that connects after the user changed the
    setting must not be handed the value the daemon booted with.
    """
    config = load_config(tmp_path)
    backend = ExtensionBackend()
    BrowserRuntime(backend=backend, config=config)  # installs the access reader
    conn, socket = paired_connection()

    config.browser_access = ACCESS_READ_ONLY
    await backend.adopt(conn)
    first = socket.of_type(P.FRAME_READY)[-1]

    assert first["active"] is True
    assert first["access"] == ACCESS_READ_ONLY

    config.browser_access = ACCESS_INTERACTIVE
    later, later_socket = paired_connection()
    await backend.adopt(later)

    assert later_socket.of_type(P.FRAME_READY)[-1]["access"] == ACCESS_INTERACTIVE


async def test_a_backend_with_no_access_reader_omits_the_word_rather_than_guessing():
    """No reader, no key: the add-on then says the mode is unknown.

    Sending ``"off"`` on a hunch would tell an add-on its user had switched the
    capability off, and ``""`` would print a blank where a word belongs.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()

    await backend.adopt(conn)

    assert "access" not in socket.of_type(P.FRAME_READY)[-1]


# --------------------------------------------------------------------------- #
# OFF MEANS OFF: the route refuses to make any socket authoritative
# --------------------------------------------------------------------------- #
#
# "Moving Browser access to off drops the live socket" was true for about one second.
# ``BrowserRuntime.disconnect`` closed with 1000, the add-on treats any non-1008 close
# as ordinary and retries with a 1 s floor, and ``/browser/ws`` consulted nothing -- so
# the card read "Connected" again seconds after the user switched the capability off,
# and Disconnect was a button that undid itself. These cases drive the REAL route.


class _Deps:
    """The create_app deps object, reduced to the one field these routes read."""

    def __init__(self, runtime: Any) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class SocketApp:
    """The real ``/browser/ws`` over a real backend, ``PairingStore`` and ``Config``.

    No middleware: the credential decisions belong to
    ``tests/test_browser_auth_v1235.py``, and what these cases assert is what the
    ROUTE does with a socket it has already accepted. Everything else is real,
    including the minted token, because a stubbed store would only assert the stub.
    """

    def __init__(self, tmp_path, *, access: str = ACCESS_INTERACTIVE, bus: Any = None) -> None:
        self.config = load_config(tmp_path)
        self.config.browser_access = access
        self.engine = open_db(tmp_path / "browser-connection.db")
        self.store = PairingStore(self.engine)
        self.backend = ExtensionBackend(event_bus=bus)
        self.runtime = BrowserRuntime(
            backend=self.backend, config=self.config, pairing=self.store
        )
        record = self.store.open_request(extension_id=PINNED_EXTENSION_ID)
        self.token = self.store.mint(record.request_id)
        self.app = FastAPI()
        browser_routes.register(self.app, _Deps(self.runtime))

    def peer(self, client: Any, **kw: Any) -> BrowserPeer:
        return BrowserPeer(client, token=self.token, **kw)


def wait_for(predicate, what: str) -> None:
    """Poll ``predicate`` on the TEST thread while the daemon's own loop runs.

    Bounded by the peer's frame budget for the same reason every wait in this file is:
    the assertion must name what never happened instead of hanging the release gate.
    Nothing asserts how long it took.
    """
    for _ in range(FRAME_WAIT_ATTEMPTS):
        if predicate():
            return
        time.sleep(FRAME_WAIT_STEP_S)
    raise AssertionError(f"never happened: {what}")


def close_code_of(ws: Any, what: str) -> int:
    """The code the daemon closed an ACCEPTED socket with, read on a bounded reader.

    A ``TestClient`` websocket receive has no timeout of its own, so the read runs on
    a thread with a budget: a daemon that never closes must fail this test by name
    rather than park the gate forever.
    """
    seen: list[int] = []

    def read() -> None:
        try:
            ws.receive_json()
        except WebSocketDisconnect as exc:
            seen.append(int(exc.code))
        except Exception:  # noqa: BLE001 - a torn-down app reads as "no close code"
            pass

    reader = threading.Thread(target=read, name="close-code-reader", daemon=True)
    reader.start()
    reader.join(FRAME_WAIT_ATTEMPTS * FRAME_WAIT_STEP_S)
    if not seen:
        raise AssertionError(f"the daemon never closed the socket: {what}")
    return seen[0]


def test_a_reconnect_while_access_is_off_never_becomes_authoritative(tmp_path):
    """THE case. A browser that comes back while access is off gets nothing.

    The add-on holds a valid credential and reconnects a second after the switch, so
    the refusal has to be the daemon's: the socket is accepted, told
    ``browser.ready {active: false, access: "off"}`` in the protocol's own words, and
    held inert. ``backend.connection`` stays empty, so nothing can command it and
    ``GET /browser/status`` reports what the user asked for.
    """
    bus = RecordingBus()
    app = SocketApp(tmp_path, access=ACCESS_OFF, bus=bus)
    with TestClient(app.app) as client:
        with app.peer(client) as peer:
            ready = peer.expect_ready()

            assert ready["active"] is False, (
                "a socket the daemon refuses to activate must not be told it is active"
            )
            assert ready["access"] == ACCESS_OFF, "the add-on is told WHY, not left to guess"
            assert app.backend.connection is None, (
                "an inert socket must never hold _conn: a command would reach the "
                "user's browser while the capability that authorises it is off"
            )
            assert app.backend.connected is False
            status = client.get("/browser/status").json()
            assert status["connected"] is False and status["access"] == ACCESS_OFF
            assert "browser.connected" not in bus.names(), (
                "nothing connected, so the bus must not say a browser did"
            )


def test_an_inert_socket_publishes_no_browser_events(tmp_path):
    """Access off means no page data enters Iron Jarvis, not even ambiently.

    A ``browser.event`` from a held socket would cache the user's active tab and
    publish their page title on the bus -- reading their browser, which is exactly what
    the switch withdrew. Frames from an inert socket are read and discarded.
    """
    bus = RecordingBus()
    app = SocketApp(tmp_path, access=ACCESS_OFF, bus=bus)
    with TestClient(app.app) as client:
        with app.peer(client) as peer:
            peer.expect_ready()

            peer.send(
                P.event_frame(
                    "evt_1",
                    P.EVENT_TAB_ACTIVATED,
                    {"tab_id": 9, "title": "Chase Bank", "url": "https://chase.com/"},
                )
            )
            # The frame must have been READ before the assertion means anything, and a
            # discarded frame leaves no trace to wait for -- so wait for the NEXT thing
            # the read loop does. Turning access on promotes the socket, and the loop
            # only reaches that idle tick after consuming the frame that was already
            # delivered to it.
            app.config.browser_access = ACCESS_INTERACTIVE
            peer.expect_frame(P.FRAME_READY, what="the promotion that follows the discard")

    assert app.backend.active_tab is None, "an inert socket must not seed the active tab"
    assert bus.of("browser.tab_activated") == []
    assert app.backend.status()["active_tab"] is None


def test_turning_access_back_on_promotes_the_held_socket(tmp_path):
    """The inert hold is not a punishment: switching access on activates that socket.

    Read live on every tick, so the user does not have to go into Chrome's popup to
    get their browser back after changing their mind.
    """
    app = SocketApp(tmp_path, access=ACCESS_OFF)
    with TestClient(app.app) as client:
        with app.peer(client) as peer:
            assert peer.expect_ready()["active"] is False

            app.config.browser_access = ACCESS_INTERACTIVE

            promoted = peer.expect_frame(P.FRAME_READY, what="a second browser.ready")
            assert promoted["active"] is True
            assert promoted["access"] == ACCESS_INTERACTIVE
            wait_for(lambda: app.backend.connected, "the promoted socket became authoritative")
            assert client.get("/browser/status").json()["connected"] is True


def test_the_disconnect_button_asks_the_add_on_to_STAY_away(tmp_path):
    """``POST /browser/disconnect`` sends the directive the add-on implements.

    Closing alone is a button that undoes itself: the add-on treats a 1000 close as
    ordinary and reconnects with a 1 s backoff floor. ``DIRECTIVE_DISCONNECT`` exists
    on both sides (the add-on answers it with a PERSISTED suspend) and no Python had
    ever sent it.
    """
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with app.peer(client) as peer:
            peer.expect_ready()
            wait_for(lambda: app.backend.connected, "the socket became authoritative")

            answer = client.post("/browser/disconnect")

            assert answer.json() == {"disconnected": True}
            wait_for(
                lambda: [action for action, _ in peer.directives] == [P.DIRECTIVE_DISCONNECT],
                f"the disconnect directive reached the add-on (saw {peer.directives})",
            )
            assert app.backend.connection is None
            assert client.get("/browser/status").json()["connected"] is False


def test_the_access_switch_drops_the_socket_without_suspending_the_add_on(tmp_path):
    """What ``app.py``'s ``_arm_browser`` calls, and why it differs from the button.

    A person pressing Disconnect gets a persisted suspend, with the inverse offered in
    the add-on's own panel. The access switch must NOT: the daemon refuses every
    reconnect while access is off anyway, and a suspend there would leave the browser
    away after the user switched access back on, with the only remedy hidden in Chrome.
    """
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with app.peer(client) as peer:
            peer.expect_ready()
            wait_for(lambda: app.backend.connected, "the socket became authoritative")

            app.config.browser_access = ACCESS_OFF
            assert client.portal.call(app.runtime.disconnect) is True

            wait_for(lambda: not app.backend.connected, "the live socket was dropped")
            assert peer.directives == [], "the switch must not suspend the add-on"


# --------------------------------------------------------------------------- #
# The pairing deadline is enforced ON THE SOCKET, by this route
# --------------------------------------------------------------------------- #


def test_the_pairing_deadline_closes_the_abandoned_socket(tmp_path):
    """D06A on the wire: an abandoned unpaired socket is closed with 1008.

    The deadline was real and pinned NOWHERE: every deadline test asserted that the
    STORE retires a record, and replacing the route's enforcement branch with a bare
    ``continue`` left all 181 browser tests green. What that regression ships is an
    unauthenticated socket held open for the life of the daemon and a Pair button
    offered for a browser that is gone -- the two things D06A exists to prevent.
    """
    app = SocketApp(tmp_path)
    app.store.deadline_s = 0.0  # the store owns the clock; nothing here sleeps on one
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            offer = ws.receive_json()
            assert offer["type"] == P.FRAME_PAIRING_REQUIRED

            code = close_code_of(ws, "an abandoned pairing socket was never closed")

        assert code == 1008
        assert client.get("/browser/status").json()["pending_pairing"] is None, (
            "the card must stop offering Pair for a browser that has gone"
        )


def test_the_pair_offer_prefers_the_pinned_add_on_over_an_anonymous_asker(tmp_path):
    """Oldest-first alone hands the credential to whoever asked FIRST.

    ``/browser/ws?pairing=1`` is the one endpoint that needs no credential, so any
    local process can hold an offer open -- and one that reconnects every second is
    always the oldest. The row whose origin identified it as the pinned add-on wins.
    """
    app = SocketApp(tmp_path)
    impostor = app.store.open_request(extension_id="")
    real = app.store.open_request(extension_id=PINNED_EXTENSION_ID)
    assert [row["request_id"] for row in app.store.pending_rows()][0] == impostor.request_id

    with TestClient(app.app) as client:
        offered = client.get("/browser/status").json()["pending_pairing"]

    assert offered is not None
    assert offered["request_id"] == real.request_id, (
        "the Pair button must not offer a request that did not come from the add-on"
    )
    assert offered["extension_id"] == PINNED_EXTENSION_ID, (
        "the card renders this, so the user can see whom they are approving"
    )


def test_a_replacement_over_the_real_route_publishes_one_disconnect(tmp_path):
    """The same property, end to end, because the duplicate was born in the ROUTE.

    The false second event came from ``_pump``'s ``finally`` on the socket that lost --
    a path no backend-level test drives. Two real sockets, one recording bus: the
    stream must read connected, disconnected(replaced), connected, and nothing after.

    The old socket is closed EXPLICITLY before the assertion, and that close is the
    synchronisation: ``BrowserPeer.close`` tears down the ``TestClient`` websocket
    session, which does not return until the daemon's handler coroutine — and so its
    ``finally`` — has finished. Waiting for the duplicate NOT to arrive would be a
    timing assertion; waiting for the handler to end is a fact.
    """
    bus = RecordingBus()
    app = SocketApp(tmp_path, bus=bus)
    with TestClient(app.app) as client:
        first = app.peer(client).connect()
        first.expect_ready()
        wait_for(lambda: app.backend.connected, "the first socket became authoritative")
        second = app.peer(client).connect()
        try:
            second.expect_ready()
            first.expect_connection_replaced()
            first.close()

            assert bus.of("browser.disconnected") == [
                {"reason": "replaced", "detail": "a newer browser connection authenticated"}
            ], f"one disconnect, and it says replaced. Stream: {bus.names()}"
            assert bus.names()[-1] == "browser.connected", (
                "the last event must not be a disconnect while a browser is live"
            )
            assert client.get("/browser/status").json()["connected"] is True
        finally:
            second.close()
