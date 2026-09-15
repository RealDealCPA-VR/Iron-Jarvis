"""v1.265.0 — a Pair press never mints a credential for nobody, and never dead-ends.

THE REPORT: "AUTHENTICATION_FAILED: This install is already paired with a browser.
Press Forget on the Browser page in Iron Jarvis before pairing another." on every
Pair press, on a PC where no browser was paired at all.

WHAT THE DAEMON LOG SHOWED (2026-09-14 19:40:00-05). The add-on opened a pairing
socket (A); three seconds later it dropped it and opened another (B) — a
service-worker restart, ordinary. The card kept offering A's request: the socket
route forgot the socket, but A's request lived on in ``PairingStore._pending``,
which knew only the five-minute deadline, and ``_preferred_pending`` offers the
OLDEST request with the pinned id. The user pressed Pair on A. ``complete_pairing``
minted the row FIRST and looked for A's socket SECOND, found none, answered 409 —
and left the row unrevoked with ``last_seen_at`` NULL. From then on ``mint``'s
``self.paired()`` guard refused every press, and the card's Forget — the remedy the
message named — rendered only under Connected.

Three defects, three fixes, each pinned here with a case that was RED on the
untouched tree (the scratch probes of the same session):

1. **A closed pairing socket takes its offer with it.** The route's teardown calls
   ``PairingStore.drop_request`` for an unpaired socket. Driven through the REAL
   ``/browser/ws`` route with a ``TestClient`` socket, because the drop lives in
   the route, not the backend: a version that called ``backend.release`` directly
   passed for the wrong reason.
2. **Check, then mint; and undo a mint that was never delivered.** The request
   must be pending and its socket open BEFORE anything is minted; a delivery that
   fails after the mint revokes the row it minted, in the same call.
3. **A human Pair press replaces an idle pairing.** ``mint(allow_replace=True)``
   stamps every live row revoked in the same transaction that inserts the new one.
   The ONE refusal kept is a browser that is paired AND connected right now
   (``test_browser_pairing_v1235.py::test_pairing_a_second_browser_is_refused_until_forget``).

Nothing here asserts a duration. The one wait — for the route's teardown after the
client closes its socket — is bounded and fails with a name.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import BrowserRuntime
from iron_jarvis.daemon.routes import browser as browser_routes
from tests.test_browser_pairing_v1235 import (
    _ADDON_ORIGIN,
    FakeConfig,
    FakeSocket,
    _BoundedReader,
    _RouteDeps,
    _restricted,
    _runtime,
    open_db,
)


@pytest.fixture
def store(tmp_path):
    return PairingStore(open_db(tmp_path / "orphan.db"))


def _wait_until(predicate, what: str, *, tries: int = 1500) -> None:
    """A bounded wait for another thread's teardown; never a timing assertion.

    Generous on purpose: the bound exists so a daemon that never drops the request
    FAILS WITH A NAME instead of hanging the gate, not to measure how fast the
    teardown is. (With the drop now synchronous and first in the route's
    ``finally``, it has already happened by the time ``TestClient`` hands control
    back — the wait is the safety net, and a 3 s version of it went red once
    under a full dashboard suite plus four pytest workers.)
    """
    for _ in range(tries):
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


# --------------------------------------------------------------------------- #
# 1. A closed pairing socket takes its offer with it (the real route)
# --------------------------------------------------------------------------- #


def test_a_pairing_socket_that_closes_takes_its_offer_with_it(tmp_path):
    """After the add-on's socket closes, its request is not offered and cannot mint.

    The sequence in the log: socket A closes, socket B opens; the card must offer B,
    and a press on A's stale id must be a 404 that mints nothing — not a credential
    for a browser that is not there.
    """
    store = PairingStore(open_db(tmp_path / "route.db"))
    backend = ExtensionBackend()
    runtime = BrowserRuntime(backend=backend, config=FakeConfig(), pairing=store)
    app = FastAPI()
    browser_routes.register(app, _RouteDeps(runtime))

    with TestClient(app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": _ADDON_ORIGIN}
        ) as ws:
            first = _BoundedReader(ws).frame(P.FRAME_PAIRING_REQUIRED)["request_id"]
            offered = client.get("/browser/status").json()["pending_pairing"]
            assert offered and offered["request_id"] == first, "the open socket is offered"

        # The add-on dropped socket A (a service-worker restart). NOTE the harness:
        # TestClient sends the disconnect and then CANCELS the app's scope at once,
        # so the route's teardown may run cancelled — which is exactly why the drop
        # is synchronous and first in that teardown. Bounded wait, never a clock.
        _wait_until(lambda: store.pending(first) is None, "the closed socket's request left the store")
        assert client.get("/browser/status").json()["pending_pairing"] is None, (
            "the card still offers Pair for a socket that has closed"
        )

        # A press on the stale id — the card was mid-poll — mints NOTHING.
        stale = client.post("/browser/pair", json={"request_id": first})
        assert stale.status_code == 404, stale.text
        assert store.paired() is False
        assert store.rows() == []

        # Socket B: the reconnecting add-on is what the card offers now.
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": _ADDON_ORIGIN}
        ) as ws2:
            second = _BoundedReader(ws2).frame(P.FRAME_PAIRING_REQUIRED)["request_id"]
            assert second != first
            offered = client.get("/browser/status").json()["pending_pairing"]
            assert offered and offered["request_id"] == second


# --------------------------------------------------------------------------- #
# 2. Check, then mint; undo a mint that was never delivered
# --------------------------------------------------------------------------- #


async def test_a_press_on_a_socket_that_has_closed_mints_nothing_and_drops_the_offer(store):
    """The pre-check: a closed socket is found BEFORE the mint, so no row exists at all."""
    backend = ExtensionBackend()
    conn, _socket, request_id = _restricted(backend, store)
    await backend.release(conn)  # what the route's teardown does to the backend
    assert store.pending(request_id) is not None, "premise: the store still holds the request"

    with pytest.raises(BrowserError) as caught:
        await _runtime(backend, store).complete_pairing(request_id)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    assert "Pair" in caught.value.message and "side panel" in caught.value.message
    assert store.rows() == [], "nothing may be minted for a socket that is already gone"
    assert store.paired() is False
    assert store.pending(request_id) is None, "the stale request must stop being offered"


async def test_a_delivery_that_fails_after_the_mint_revokes_what_it_minted(store):
    """The compensating half: the socket is open at the check and dies at the send."""
    backend = ExtensionBackend()
    pending = store.open_request(extension_id="lgihfomaieifpnemakmpadmggjnoojmm")
    conn = ExtensionConnection(
        FakeSocket(dead=True), paired=False, pairing_request_id=pending.request_id
    )
    backend.register_restricted(conn)

    with pytest.raises(BrowserError) as caught:
        await _runtime(backend, store).complete_pairing(pending.request_id)

    assert caught.value.code == BrowserErrorCode.BROWSER_NOT_CONNECTED.value
    rows = store.rows()
    assert len(rows) == 1, "the mint happened (the socket was open at the check)"
    assert rows[0]["revoked_at"] is not None, "…and was revoked when delivery failed"
    assert rows[0]["last_seen_at"] is None, "a token nobody received was never used"
    assert store.paired() is False


async def test_the_live_browser_pairs_after_a_refused_stale_press(store):
    """The dead end itself: a stale press must not lock the real browser out."""
    backend = ExtensionBackend()
    conn_a, _sock_a, req_a = _restricted(backend, store)
    await backend.release(conn_a)
    with pytest.raises(BrowserError):
        await _runtime(backend, store).complete_pairing(req_a)

    _conn_b, sock_b, req_b = _restricted(backend, store)
    await _runtime(backend, store).complete_pairing(req_b)

    assert len(sock_b.of_type(P.FRAME_PAIRED)) == 1
    assert store.paired() is True
    assert backend.connected is True


# --------------------------------------------------------------------------- #
# 3. A human Pair press replaces an idle pairing
# --------------------------------------------------------------------------- #


async def test_a_pair_press_replaces_an_idle_pairing(store):
    """A reinstalled add-on, or a second browser while the first is closed: one press."""
    backend = ExtensionBackend()
    runtime = _runtime(backend, store)
    conn_a, sock_a, req_a = _restricted(backend, store)
    await runtime.complete_pairing(req_a)
    first_token = sock_a.of_type(P.FRAME_PAIRED)[0]["token"]
    await backend.release(conn_a)  # the first browser closed; its credential is idle
    assert backend.connected is False and store.paired() is True

    _conn_b, sock_b, req_b = _restricted(backend, store)
    await runtime.complete_pairing(req_b)

    assert len(sock_b.of_type(P.FRAME_PAIRED)) == 1, "the second browser is paired"
    assert store.verify(first_token) is None, "the replaced credential is dead"
    live = [row for row in store.rows() if row["revoked_at"] is None]
    assert len(live) == 1, "exactly one live credential, ever"
    assert backend.connected is True


def test_replace_revokes_and_inserts_in_one_transaction(store):
    """At the store: ``allow_replace`` leaves exactly one live row, and the default still refuses."""
    first = store.mint(store.open_request().request_id)
    assert store.verify(first) is not None

    with pytest.raises(BrowserError) as caught:
        store.mint(store.open_request().request_id)
    assert caught.value.code == BrowserErrorCode.AUTHENTICATION_FAILED.value, (
        "a direct caller of the store must opt in to replacing"
    )

    second = store.mint(store.open_request().request_id, allow_replace=True)
    assert store.verify(first) is None
    assert store.verify(second) is not None
    rows = store.rows()
    assert len(rows) == 2 and sum(row["revoked_at"] is None for row in rows) == 1


def test_revoke_token_revokes_exactly_the_matching_row(store):
    token = store.mint(store.open_request().request_id)

    assert store.revoke_token("") is False
    assert store.revoke_token("not-the-token") is False
    assert store.verify(token) is not None, "a miss revokes nothing"

    assert store.revoke_token(token) is True
    assert store.verify(token) is None
    assert store.revoke_token(token) is False, "already revoked: nothing to stamp"
    assert store.paired() is False


def test_the_message_names_the_remedy_the_card_now_shows():
    """The refusal that remains still says Forget — and Forget is now on the card
    in that state (``dashboard/__tests__/browser-pairing-v1265.test.tsx``)."""
    from iron_jarvis.browser import service as svc
    import inspect

    src = inspect.getsource(svc.BrowserRuntime.complete_pairing)
    assert "Another browser is paired and connected right now" in src
    assert "Press Forget on" in src
    assert "allow_replace=True" in src, "the service must opt in to replacing an idle pairing"
