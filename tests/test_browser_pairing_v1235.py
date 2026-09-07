"""Browser pairing: the credential is minted once, delivered once, and stored as a hash (v1.235.0, Ship 1).

What this file pins, and the silent failure each assertion catches.

**The restricted state machine (D06A).** An unpaired socket may send exactly one
frame type, ``browser.pairing_ack``. Anything else closes it with 1008. The failure
this catches is the one that makes the pairing step decorative: a socket that can
send ``browser.response`` — or, worse, be handed a command — before the user has
pressed Pair is a browser talking to the daemon with no credential at all. The rule
is asserted against
:meth:`~iron_jarvis.browser.extension_backend.ExtensionBackend.handle_frame`, which
is where the state machine lives, so it holds for every caller including a route
that forgot to check.

**The plaintext token appears exactly once, ever.** ``complete_pairing`` returns
``None``, so ``POST /browser/pair`` physically cannot put the token in a response
body; the token crosses the wire in one ``browser.paired`` frame and in no other
frame; and every column of the persisted row is scanned for it. The failures caught
are each real and each silent: a token in a JSON body lands in browser devtools, in
an HTTP trace and in whatever the user pastes into a bug report; a token in a
database column is readable in the SQLite file, in every backup under
``<home>/backups/``, and in any diagnostics bundle — and pairing would keep working
perfectly, so nothing would ever fail to reveal it.

**Verification refuses a revoked credential, and Forget revokes.** The revoked
filter is asserted through :meth:`~iron_jarvis.browser.pairing.PairingStore.verify`
rather than by listing rows, because a caller that forgets the filter authenticates
a browser the user has already forgotten. Forget also clears pending requests: a
surviving request would let the user press Pair and mint a fresh credential
immediately after asking to forget, which reads as Forget having failed.

**The 5-minute deadline is real, and time is injected.** A pending request past the
deadline is invisible to ``pending()`` and unmintable, and the test moves an injected
clock rather than sleeping. No assertion in this file measures a wall clock: the
deadline is a bound, not a performance claim.

Note on scope: ``/browser/ws`` and ``POST /browser/pair`` belong to the routes lane
and do not exist yet, so this file drives the same state machine and the same store
through their own public surface with a stand-in socket. When the route lands, the
socket-level cases (``browser.pairing_required`` on connect, the 1008 close as
observed by a real client, the pair route's status codes) join
``tests/_fakes/browser_peer.py``'s end-to-end path; the properties asserted here are
the ones a route cannot make true or false on its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.models import BrowserPairing
from iron_jarvis.browser.pairing import PairingStore, token_sha256
from iron_jarvis.browser.service import ACCESS_INTERACTIVE, BrowserRuntime
from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.routes import browser as browser_routes


class FakeSocket:
    """A stand-in for Starlette's ``WebSocket``: records frames, records the close code.

    Deliberately not a mock. ``ExtensionConnection`` only ever calls ``send_json``
    and ``close``, so a real object with those two methods exercises the same code
    the daemon runs, and the frame list is the record every assertion here reads.
    ``dead`` makes a send fail the way a closed socket does, which is how the
    "deliver the token to a browser that has gone" path is driven honestly.
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

    def of_type(self, frame_type: str) -> list[dict]:
        return [frame for frame in self.frames if frame.get("type") == frame_type]


class MovableClock:
    """A clock a test advances, so the deadline is driven without sleeping."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class FakeConfig:
    """The one live setting the runtime reads, held by reference like the real Config."""

    def __init__(self, browser_access: str = ACCESS_INTERACTIVE) -> None:
        self.browser_access = browser_access


@pytest.fixture
def engine(tmp_path):
    return open_db(tmp_path / "pairing.db")


@pytest.fixture
def store(engine):
    return PairingStore(engine)


def _restricted(backend: ExtensionBackend, store: PairingStore) -> tuple[ExtensionConnection, FakeSocket, str]:
    """An unpaired socket registered against a fresh pending request."""
    pending = store.open_request(extension_id="lgihfomaieifpnemakmpadmggjnoojmm")
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id=pending.extension_id,
        paired=False,
        pairing_request_id=pending.request_id,
    )
    backend.register_restricted(conn)
    return conn, socket, pending.request_id


def _runtime(backend: ExtensionBackend, store: PairingStore) -> BrowserRuntime:
    return BrowserRuntime(backend=backend, config=FakeConfig(), pairing=store)


#: The pinned add-on's Origin, in the shape the route reads the extension id from.
#: The ORIGIN GUARD is not in play here (that is
#: ``tests/test_browser_auth_v1235.py``'s subject and this app carries no
#: middleware); this header exists so the route learns an extension id the way a
#: real handshake gives it one.
_ADDON_ORIGIN = "chrome-extension://lgihfomaieifpnemakmpadmggjnoojmm"


class _RouteDeps:
    """The ``create_app`` deps object, reduced to the one field these routes read.

    ``/browser/ws`` is registered on a BARE FastAPI app because ``daemon/app.py`` is
    coordinator-owned — the same documented arrangement
    ``tests/test_browser_auth_v1235.py`` uses.
    """

    def __init__(self, runtime: BrowserRuntime) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class _BoundedReader:
    """Bounded reads off a raw ``TestClient`` socket, pumped on a daemon thread.

    A ``TestClient`` WebSocket receive has no timeout, so reading on the test thread
    HANGS the release gate whenever the daemon fails to send what is being asserted —
    which is precisely the failure a missing deadline branch produces. A background
    pump does the blocking read; the test thread waits a bounded time and raises a
    NAMED assertion. ``BrowserPeer`` covers the frames it owns, but not the raw close
    CODE, which is what the deadline case has to see. The thread is a daemon thread, so
    one left blocked can never hold the interpreter open at exit.
    """

    WAIT_S = 10.0

    def __init__(self, ws) -> None:
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(ws,), daemon=True)
        self._thread.start()

    def _pump(self, ws) -> None:
        while True:
            try:
                message = ws.receive()
            except Exception:  # the socket is gone; the test reads what arrived
                return
            self._queue.put(message)
            if message.get("type") == "websocket.close":
                return

    def _next(self, what: str) -> dict:
        try:
            return self._queue.get(timeout=self.WAIT_S)
        except queue.Empty:
            raise AssertionError(f"the daemon never sent {what}") from None

    def frame(self, frame_type: str) -> dict:
        message = self._next(frame_type)
        if message.get("type") == "websocket.close":
            raise AssertionError(
                f"expected {frame_type}, the socket closed with {message.get('code')}"
            )
        frame = json.loads(message.get("text") or "{}")
        assert frame.get("type") == frame_type, f"expected {frame_type}, got {frame!r}"
        return frame

    def close_code(self, what: str, *, limit: int = 10) -> int:
        """The close code, having skipped at most ``limit`` earlier frames."""
        for _ in range(limit):
            message = self._next(what)
            if message.get("type") == "websocket.close":
                return int(message.get("code", 0))
        raise AssertionError(f"the socket sent {limit} frames without closing ({what})")


# --------------------------------------------------------------------------- #
# The restricted state machine
# --------------------------------------------------------------------------- #


async def test_an_unpaired_socket_may_send_only_the_pairing_ack(store):
    """``browser.pairing_ack`` is accepted; the socket stays open and unpaired."""
    backend = ExtensionBackend()
    conn, socket, request_id = _restricted(backend, store)

    keep = await backend.handle_frame(conn, P.pairing_ack_frame(request_id))

    assert keep is True
    assert conn.pairing_acked is True
    assert conn.restricted is True, "acking a pairing offer must not authenticate the socket"
    assert socket.closed_with is None
    assert backend.connected is False, "an unpaired browser must never read as connected"


@pytest.mark.parametrize(
    "frame",
    [
        {"type": P.FRAME_RESPONSE, "id": "req_1", "success": True, "result": {}},
        {"type": P.FRAME_HELLO, "extension_id": "x", "extension_version": "1", "host_permission": True},
        {"type": P.FRAME_EVENT, "id": "evt_1", "event": P.EVENT_TAB_ACTIVATED, "payload": {}},
        {"type": "browser.something_new"},
        {},
    ],
)
async def test_any_other_frame_on_a_restricted_socket_closes_it_1008(store, frame):
    """D06A: a frame of any other type closes the socket with 1008, from the state machine.

    Parametrised over a response, a hello, an event, an unknown type and a frame
    with no type at all, because "only pairing frames pass" has to hold for the
    frames an attacker would actually try, not just for a nonsense string.
    """
    backend = ExtensionBackend()
    conn, socket, request_id = _restricted(backend, store)

    keep = await backend.handle_frame(conn, frame)

    assert keep is False, "the handler must tell the route to stop reading this socket"
    assert socket.closed_with == 1008
    assert conn.closed is True
    assert backend.restricted_socket(request_id) is None, "a closed socket stays pairable to nobody"
    assert backend.last_error, "the refusal must be nameable on GET /browser/status"


async def test_a_restricted_socket_cannot_run_a_command(store):
    """A registered pending socket is not authoritative, so no command can reach it.

    This case owns ``backend.command`` only. The D06A rule about what an unpaired
    socket may SEND lives in ``handle_frame`` and is pinned by
    ::test_any_other_frame_on_a_restricted_socket_closes_it_1008 (red on all five
    params when that block is deleted) — recorded here because v1.235.0's mutation
    pass looked for it in this test, found it green, and reported a hole that is not
    one.
    """
    backend = ExtensionBackend()
    _conn, socket, _request_id = _restricted(backend, store)

    with pytest.raises(BrowserError) as caught:
        await backend.command(P.METHOD_LIST_TABS)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert socket.of_type(P.FRAME_COMMAND) == [], "no command frame may reach an unpaired browser"


# --------------------------------------------------------------------------- #
# The token: minted once, delivered once, never stored, never returned
# --------------------------------------------------------------------------- #


async def test_pairing_delivers_the_token_on_the_pending_socket_exactly_once(store):
    """One ``browser.paired`` frame carries the token; the socket then becomes authoritative."""
    backend = ExtensionBackend()
    conn, socket, request_id = _restricted(backend, store)
    runtime = _runtime(backend, store)

    returned = await runtime.complete_pairing(request_id, label="Chrome on VR-DESKTOP")

    assert returned is None, (
        "complete_pairing must not return the token: a value a route can read is a "
        "value a route will put in a response body"
    )
    paired_frames = socket.of_type(P.FRAME_PAIRED)
    assert len(paired_frames) == 1, "the plaintext token crosses the wire exactly once"
    token = paired_frames[0]["token"]
    assert token, "browser.paired carried no token"
    assert conn.paired is True and conn.restricted is False
    assert backend.connected is True
    assert socket.of_type(P.FRAME_READY), "the newly paired socket is told it is active (D08 step 4)"
    assert token not in str(
        [frame for frame in socket.frames if frame.get("type") != P.FRAME_PAIRED]
    ), "no frame other than browser.paired may carry the token"


async def test_only_the_sha256_is_persisted(engine, store):
    """Every column of the row is scanned: the plaintext must be nowhere in the database."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    with session_scope(engine) as db:
        rows = list(db.exec(select(BrowserPairing)))
        assert len(rows) == 1
        row = rows[0]
        values = row.model_dump()

    assert row.token_sha256 == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert row.token_sha256 == token_sha256(token)
    leaked = [name for name, value in values.items() if isinstance(value, str) and token in value]
    assert leaked == [], f"the plaintext pairing token is stored in {leaked}"
    assert store.verify(token) is not None, "the stored hash must still authenticate the token"


async def test_a_wrong_token_does_not_authenticate(store):
    """The hash comparison is the gate: a near-miss token is refused."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    assert store.verify(token[:-1] + ("a" if token[-1] != "a" else "b")) is None
    assert store.verify("") is None
    assert store.verify(token_sha256(token)) is None, (
        "the stored hash must not authenticate as if it were the token itself"
    )


async def test_verify_stamps_last_seen_without_touching_the_secret(store):
    """A successful verify records liveness, so the card needs no second store."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    assert store.rows()[0]["last_seen_at"] is None
    row = store.verify(token)

    assert row is not None and row.last_seen_at is not None
    assert store.rows()[0]["last_seen_at"] is not None
    assert "token_sha256" not in store.rows()[0], (
        "rows() feeds a response body; the hash is not the token but it is not payload either"
    )


async def test_pairing_a_second_browser_is_refused_until_forget(store):
    """The already-paired refusal is its own code, so the route can answer 409.

    A shared code with the stale-request case would print the wrong sentence on the
    card half the time: "press Pair again" and "press Forget first" are different
    instructions.
    """
    backend = ExtensionBackend()
    _first, socket, first_request = _restricted(backend, store)
    runtime = _runtime(backend, store)
    await runtime.complete_pairing(first_request)

    _second, _socket2, second_request = _restricted(backend, store)
    with pytest.raises(BrowserError) as caught:
        await runtime.complete_pairing(second_request)

    assert caught.value.code == BrowserErrorCode.AUTHENTICATION_FAILED.value
    assert "Forget" in caught.value.message
    assert len(socket.of_type(P.FRAME_PAIRED)) == 1, "the refused attempt minted nothing"


async def test_pairing_a_socket_that_has_gone_refuses_instead_of_minting_silently(store):
    """A dead socket must not consume a request and leave a credential behind.

    The failure this catches: a minted credential no browser ever received, with the
    card reporting Paired while the add-on keeps asking to pair.
    """
    backend = ExtensionBackend()
    pending = store.open_request()
    conn = ExtensionConnection(FakeSocket(dead=True), paired=False, pairing_request_id=pending.request_id)
    backend.register_restricted(conn)

    with pytest.raises(BrowserError) as caught:
        await _runtime(backend, store).complete_pairing(pending.request_id)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert backend.connected is False


# --------------------------------------------------------------------------- #
# The 5-minute deadline
# --------------------------------------------------------------------------- #


async def test_the_pairing_deadline_retires_an_abandoned_request(engine):
    """Past the deadline a request is invisible, unmintable, and reported as expired.

    The clock is injected and advanced; nothing sleeps and nothing asserts elapsed
    time. Without the deadline the card would offer a Pair button forever for a
    socket that closed minutes ago.
    """
    clock = MovableClock()
    store = PairingStore(engine, clock=clock)
    backend = ExtensionBackend()
    _conn, _socket, request_id = _restricted(backend, store)

    assert store.pending(request_id) is not None
    clock.advance(P.PAIRING_DEADLINE_S + 1)

    assert store.pending(request_id) is None, "an expired request must not be readable"
    assert store.pending_rows() == [], "an expired request must not reach the card"
    with pytest.raises(BrowserError) as caught:
        await _runtime(backend, store).complete_pairing(request_id)
    assert caught.value.code == BrowserErrorCode.PAIRING_REQUIRED.value


async def test_expired_returns_the_records_so_the_route_can_close_their_sockets(engine):
    """``expired()`` drops and hands back the dead requests, once."""
    clock = MovableClock()
    store = PairingStore(engine, clock=clock)
    first = store.open_request()
    clock.advance(P.PAIRING_DEADLINE_S + 1)
    fresh = store.open_request()

    dead = store.expired()

    assert [record.request_id for record in dead] == [first.request_id]
    assert store.expired() == [], "a retired request is not reported twice"
    assert store.pending(fresh.request_id) is not None, "a live request survives the sweep"


def test_the_pairing_deadline_closes_the_socket_at_the_route(tmp_path):
    """The deadline is enforced where the SOCKET is: the route's read pump closes it.

    Everything else in this section is store-level — a record retires, ``expired()``
    hands it out — and none of it touches a socket. That gap was real: v1.235.0's
    mutation pass replaced the ``if await _pairing_lapsed(...): await conn.close(1008)``
    branch in ``daemon/routes/browser.py::_pump`` with a bare ``continue`` and all 181
    browser tests stayed GREEN. What that mutation ships is exactly what D06A exists to
    prevent: an UNAUTHENTICATED socket held open for the life of the daemon, and a Pair
    button offered for a browser that closed minutes ago — so pressing Pair mints a
    real credential and hands it to whoever is still holding that socket.

    ``deadline_s`` is shortened on the store (its own seam) instead of sleeping out the
    real five minutes. Nothing here asserts how long anything took: the reads are
    bounded so a daemon that never closes the socket FAILS with a name instead of
    hanging the release gate, which is what an unbounded ``TestClient`` receive would
    do — and that is the very failure this mutation causes.
    """
    engine = open_db(tmp_path / "deadline.db")
    store = PairingStore(engine)
    store.deadline_s = 0.2
    backend = ExtensionBackend()
    runtime = BrowserRuntime(backend=backend, config=FakeConfig(), pairing=store)
    app = FastAPI()
    browser_routes.register(app, _RouteDeps(runtime))

    with TestClient(app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": _ADDON_ORIGIN}
        ) as ws:
            reader = _BoundedReader(ws)
            required = reader.frame(P.FRAME_PAIRING_REQUIRED)
            request_id = required["request_id"]
            assert backend.restricted_socket(request_id) is not None

            assert reader.close_code("the 1008 close on the pairing deadline") == 1008

        offered = client.get("/browser/status").json()

    assert offered["pending_pairing"] is None, (
        "the card still offers Pair for a socket the daemon has closed"
    )
    assert offered["connected"] is False
    assert backend.restricted_socket(request_id) is None, (
        "a socket closed on the deadline must not stay pairable"
    )


async def test_verify_compares_digests_in_constant_time(store, monkeypatch):
    """``hmac.compare_digest`` is what ``verify`` compares with — asserted by CALL.

    Timing is the property and timing is exactly what a test must never measure: a
    wall-clock assertion here would be measuring the CI runner, not the comparison.
    So the instrument is the call itself. Changing that line to ``==`` left all 181
    browser tests green in v1.235.0's mutation pass; it leaves this one red.

    Both halves of the same line are asserted, because either alone is a wrong pin:
    that the comparison really happens (a hit and a miss), and that both sides arrive
    as HEX ASCII bytes — ``compare_digest`` raises ``TypeError`` on a non-ASCII ``str``
    (the v1.176.0 lesson), and a token pasted off a query string is arbitrary text, so
    hashing FIRST is what keeps the refusal a refusal instead of a 500. The spy takes
    ``*args, **kw`` and calls through, per this repository's monkeypatch rule.
    """
    import hmac as hmac_module

    pairs: list[tuple[object, object]] = []
    real_compare = hmac_module.compare_digest

    def spy(*args, **kw):
        if len(args) == 2:
            pairs.append((args[0], args[1]))
        return real_compare(*args, **kw)

    monkeypatch.setattr(hmac_module, "compare_digest", spy)
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    # ``complete_pairing`` returns None by construction; the plaintext crosses the
    # wire exactly once, in the ``browser.paired`` frame.
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]
    pairs.clear()

    assert store.verify(token) is not None, "the minted token must still verify"
    assert pairs, "verify did not compare with hmac.compare_digest"
    stored, candidate = pairs[-1]
    assert isinstance(stored, bytes) and isinstance(candidate, bytes), (
        "compare_digest was handed str, which raises TypeError on non-ASCII input"
    )
    assert candidate == token_sha256(token).encode("ascii")
    assert stored == candidate, "the row that matched must be the row that was compared"

    pairs.clear()
    assert store.verify("not-the-token") is None
    assert pairs, "a MISS must be compared too, not short-circuited before the digest"
    assert pairs[-1][1] == token_sha256("not-the-token").encode("ascii")
    assert pairs[-1][0] != pairs[-1][1]


async def test_a_request_id_is_prefixed_and_unguessable(store):
    """The id is the pairing offer's handle; it is minted here, not by the browser."""
    first = store.open_request()
    second = store.open_request()

    assert first.request_id.startswith(P.PAIRING_ID_PREFIX)
    assert first.request_id != second.request_id
    assert len(first.request_id) > len(P.PAIRING_ID_PREFIX) + 8


# --------------------------------------------------------------------------- #
# Disconnect, Forget, revocation
# --------------------------------------------------------------------------- #


async def test_disconnect_ends_the_socket_and_keeps_the_credential(store):
    """Disconnect is "stop for now": the next connect authenticates silently."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    runtime = _runtime(backend, store)
    await runtime.complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    assert await runtime.disconnect() is True

    assert backend.connected is False
    assert socket.closed_with == 1000
    assert await runtime.verify_token(token) is not None, (
        "Disconnect must not destroy the credential — that is what Forget is for"
    )
    assert await runtime.disconnect() is False, "there is nothing left to disconnect"


async def test_forget_revokes_the_credential_and_drops_the_socket(store):
    """Forget destroys the credential; a revoked token never authenticates again."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    runtime = _runtime(backend, store)
    await runtime.complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    killed = await runtime.forget()

    assert killed == 1
    assert await runtime.verify_token(token) is None, "a revoked token must be refused"
    assert store.paired() is False
    assert backend.connected is False and socket.closed_with is not None
    assert store.rows()[0]["revoked_at"] is not None, (
        "the row is stamped, not deleted: 'this browser was forgotten' stays answerable"
    )


async def test_forget_also_clears_pending_requests(store):
    """A surviving Pair button after Forget would mint a fresh credential at once."""
    backend = ExtensionBackend()
    _conn, _socket, request_id = _restricted(backend, store)

    await _runtime(backend, store).forget()

    assert store.pending(request_id) is None
    assert store.pending_rows() == []


async def test_pairing_after_forget_starts_over_and_works(store):
    """The whole point of Forget: the next pair is a clean one."""
    backend = ExtensionBackend()
    _conn, first_socket, first_request = _restricted(backend, store)
    runtime = _runtime(backend, store)
    await runtime.complete_pairing(first_request)
    first_token = first_socket.of_type(P.FRAME_PAIRED)[0]["token"]
    await runtime.forget()

    _conn2, second_socket, second_request = _restricted(backend, store)
    await runtime.complete_pairing(second_request)
    second_token = second_socket.of_type(P.FRAME_PAIRED)[0]["token"]

    assert second_token != first_token
    assert await runtime.verify_token(second_token) is not None
    assert await runtime.verify_token(first_token) is None


async def test_revoke_is_per_credential_and_idempotent(engine, store):
    """``revoke`` is per-credential; ``revoke_all`` is Forget. They are not the same call."""
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    runtime = _runtime(backend, store)
    await runtime.complete_pairing(request_id)
    kept = socket.of_type(P.FRAME_PAIRED)[0]["token"]
    doomed_id = store.rows()[0]["id"]

    assert store.revoke(str(doomed_id)) is True
    assert store.revoke(str(doomed_id)) is False, "revoking twice must not report success twice"
    assert store.verify(kept) is None
    assert store.revoke("bpair_nothing") is False


# --------------------------------------------------------------------------- #
# The token never reaches a log line or an event payload
# --------------------------------------------------------------------------- #


async def test_pairing_publishes_no_credential_on_the_event_bus(store):
    """``browser.connected`` carries the extension id, which is public — and nothing else.

    An event payload is persisted in ``eventrecord`` and streamed to every open
    dashboard socket, so a token here would be broadcast and durable at once.
    """
    published: list[tuple[str, dict]] = []

    class RecordingBus:
        async def publish(self, name, payload=None, session_id=None):
            published.append((name, dict(payload or {})))

    backend = ExtensionBackend(event_bus=RecordingBus())
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    assert published, "pairing a browser must be announced on the existing bus (D22)"
    assert any(name == "browser.connected" for name, _ in published)
    for name, payload in published:
        assert token not in str(payload), f"{name} carried the pairing token"


async def test_pairing_logs_no_credential(store, caplog):
    """Nothing in the pairing path formats the token into a log record, at any level."""
    caplog.set_level("DEBUG")
    backend = ExtensionBackend()
    _conn, socket, request_id = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(request_id)
    token = socket.of_type(P.FRAME_PAIRED)[0]["token"]

    for record in caplog.records:
        assert token not in record.getMessage(), f"{record.name} logged the pairing token"


async def test_the_store_is_blocking_and_the_runtime_offloads_it(store, monkeypatch):
    """Every pairing call from the loop goes through ``asyncio.to_thread``.

    The daemon is ONE event loop. A synchronous SQLite hop inside a socket handler
    is the v1.153.1 outage, which the user experienced as "Daemon offline" — every
    request timing out — rather than as a slow call. The spy takes ``*args, **kw``
    and calls through, per this repository's monkeypatch rule.
    """
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy(func, *args, **kw):
        calls.append(getattr(func, "__name__", str(func)))
        return await real_to_thread(func, *args, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    backend = ExtensionBackend()
    _conn, _socket, request_id = _restricted(backend, store)
    runtime = _runtime(backend, store)

    await runtime.pending_pairings()
    await runtime.complete_pairing(request_id)
    await runtime.verify_token("nope")
    await runtime.forget()

    assert "pending_rows" in calls
    assert "mint" in calls, "minting a credential must not run a DB write on the event loop"
    assert "verify" in calls
    assert "revoke_all" in calls
