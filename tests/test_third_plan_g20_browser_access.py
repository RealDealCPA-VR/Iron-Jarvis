"""Third north-star plan, group 20 (BR1, BR3): a forgotten browser stays forgotten.

BR1. With Browser access OFF, a paired browser that reconnects is held WAITING
(accepted, told ``browser.ready {active: false}``, never authoritative). Forget
revoked the pairing and closed only the LIVE connection, of which there is none
while access is off, and answered ``{"forgotten": true}``. Switching access back
on then promoted the waiting socket without checking its credential again, so
the browser the user had just forgotten was connected and usable with no
pairing. Two fixes, both pinned here: Forget closes every connection of the
browser, live or waiting; and a waiting connection is promoted only after its
credential verifies against the pairings as they are NOW.

BR3. A Pair press that lands while access is off made that browser the live one
anyway (``deliver_pairing`` adopted it). Now the pairing completes (the browser
is remembered) and its connection waits like any other while access is off;
switching access on promotes it through the same verified path.

Every case drives the REAL ``/browser/ws`` route, the real ``ExtensionBackend``,
the real ``BrowserRuntime`` and a real ``PairingStore``, with the repository's
fake add-on (``tests/_fakes/browser_peer.BrowserPeer``) on the socket. The peer's
``commands`` list is the recording stand-in for the browser: a command that
reaches it is a command run in the user's browser. Waits are bounded and named;
nothing asserts a duration. The credential is one the real store minted.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.identity import PINNED_EXTENSION_ID
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import ACCESS_INTERACTIVE, ACCESS_OFF, BrowserRuntime
from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import open_db
from iron_jarvis.daemon.routes import browser as browser_routes

from tests._fakes.browser_peer import FRAME_WAIT_ATTEMPTS, FRAME_WAIT_STEP_S, BrowserPeer


class _Deps:
    """The create_app deps object, reduced to the one field these routes read."""

    def __init__(self, runtime: Any) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class _App:
    """The real socket route over a real backend, store, runtime and Config."""

    def __init__(self, tmp_path, *, access: str = ACCESS_OFF, paired: bool = True) -> None:
        self.config = load_config(tmp_path)
        self.config.browser_access = access
        self.engine = open_db(tmp_path / "g20-browser.db")
        self.store = PairingStore(self.engine)
        self.backend = ExtensionBackend()
        self.runtime = BrowserRuntime(backend=self.backend, config=self.config, pairing=self.store)
        self.token = ""
        if paired:
            record = self.store.open_request(extension_id=PINNED_EXTENSION_ID)
            self.token = self.store.mint(record.request_id)
        self.app = FastAPI()
        browser_routes.register(self.app, _Deps(self.runtime))


def _wait_for(predicate, what: str) -> None:
    """Poll on the test thread while the daemon's loop runs; fail by name."""
    for _ in range(FRAME_WAIT_ATTEMPTS):
        if predicate():
            return
        time.sleep(FRAME_WAIT_STEP_S)
    raise AssertionError(f"never happened: {what}")


def _socket_closed(peer: BrowserPeer) -> bool:
    """Whether the daemon closed the peer's socket (its reader thread has ended)."""
    pump = peer._pump
    return pump is not None and not pump.is_alive()


def _list_tabs_refused(client: TestClient, runtime: BrowserRuntime) -> str:
    """Run a real browser read; return the refusal code, or fail if it ran."""
    try:
        client.portal.call(runtime.list_tabs)
    except BrowserError as exc:
        return exc.code
    raise AssertionError("a browser command ran, so a browser is live")


# --------------------------------------------------------------------------- #
# BR1: Forget closes the waiting connection too
# --------------------------------------------------------------------------- #


def test_forget_with_access_off_closes_the_waiting_browser_and_it_stays_gone(tmp_path):
    app = _App(tmp_path, access=ACCESS_OFF)
    with TestClient(app.app) as client:
        peer = BrowserPeer(client, token=app.token)
        with peer:
            assert peer.expect_ready()["active"] is False, "access is off: the socket waits"
            assert app.backend.connection is None

            answer = client.post("/browser/forget")

            assert answer.status_code == 200
            assert answer.json() == {"forgotten": True}
            assert app.store.paired() is False, "the pairing is removed"
            _wait_for(
                lambda: _socket_closed(peer),
                "Forget closed the forgotten browser's WAITING connection",
            )

            app.config.browser_access = ACCESS_INTERACTIVE

            assert app.backend.connected is False
            assert app.backend.connection is None
            assert client.get("/browser/status").json()["connected"] is False
            assert _list_tabs_refused(client, app.runtime) == (
                BrowserErrorCode.BROWSER_NOT_CONNECTED.value
            )
            assert peer.commands == [], "nothing may be done in a forgotten browser"
            ready = [f for f in peer.frames if f.get("type") == P.FRAME_READY]
            assert all(f.get("active") is False for f in ready), (
                "the forgotten browser must never be told it is the live one"
            )


# --------------------------------------------------------------------------- #
# BR1, second door: promotion verifies the credential as the pairings are NOW
# --------------------------------------------------------------------------- #


def test_a_waiting_browser_whose_pairing_was_removed_elsewhere_is_not_promoted(tmp_path):
    app = _App(tmp_path, access=ACCESS_OFF)
    with TestClient(app.app) as client:
        peer = BrowserPeer(client, token=app.token)
        with peer:
            assert peer.expect_ready()["active"] is False

            # Removed by a route that is NOT Forget: one row revoked by id.
            (row,) = app.store.rows()
            assert app.store.revoke(str(row["id"])) is True

            app.config.browser_access = ACCESS_INTERACTIVE

            _wait_for(
                lambda: _socket_closed(peer) or app.backend.connected,
                "the waiting browser was either refused or promoted",
            )
            assert app.backend.connected is False, (
                "a waiting browser whose pairing is gone was promoted without a pairing"
            )
            assert _socket_closed(peer), "the refused waiting socket is closed"
            assert _list_tabs_refused(client, app.runtime) == (
                BrowserErrorCode.BROWSER_NOT_CONNECTED.value
            )
            assert peer.commands == []


def test_a_still_paired_waiting_browser_is_promoted_when_access_is_switched_on(tmp_path):
    """The control: the re-check refuses only a credential that no longer verifies."""
    app = _App(tmp_path, access=ACCESS_OFF)
    with TestClient(app.app) as client:
        peer = BrowserPeer(client, token=app.token)
        with peer:
            assert peer.expect_ready()["active"] is False

            app.config.browser_access = ACCESS_INTERACTIVE

            promoted = peer.expect_frame(P.FRAME_READY, what="the promotion's browser.ready")
            assert promoted["active"] is True
            _wait_for(lambda: app.backend.connected, "the waiting browser became live")
            tabs = peer.serve(app.runtime.list_tabs)
            assert tabs and peer.commands[-1][0] == P.METHOD_LIST_TABS


# --------------------------------------------------------------------------- #
# BR3: Pair while access is off remembers the browser and leaves it waiting
# --------------------------------------------------------------------------- #


def test_pair_while_access_is_off_leaves_the_connection_waiting(tmp_path):
    app = _App(tmp_path, access=ACCESS_OFF, paired=False)
    with TestClient(app.app) as client:
        peer = BrowserPeer(client)
        with peer:
            peer.pair()

            assert app.store.paired() is True, "the pairing completes: the browser is remembered"
            ready = peer.expect_ready()
            assert ready["active"] is False, (
                "a browser paired while access is off must not be told it is the live one"
            )
            assert app.backend.connected is False
            assert app.backend.connection is None
            assert client.get("/browser/status").json()["connected"] is False
            assert _list_tabs_refused(client, app.runtime) == (
                BrowserErrorCode.BROWSER_ACCESS_OFF.value
            )
            assert peer.commands == []

            # Switching access on promotes it through the verified path.
            app.config.browser_access = ACCESS_INTERACTIVE
            promoted = peer.expect_frame(P.FRAME_READY, what="the promotion's browser.ready")
            assert promoted["active"] is True
            _wait_for(lambda: app.backend.connected, "the paired waiting browser became live")


# --------------------------------------------------------------------------- #
# Forget says so plainly when a connection could not be closed
# --------------------------------------------------------------------------- #


class _FakeSocket:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        return None


@pytest.mark.asyncio
async def test_forget_says_plainly_when_a_waiting_connection_could_not_be_closed(tmp_path):
    engine = open_db(tmp_path / "g20-forget.db")
    store = PairingStore(engine)
    record = store.open_request(extension_id=PINNED_EXTENSION_ID)
    token = store.mint(record.request_id)
    config = load_config(tmp_path)
    config.browser_access = ACCESS_OFF
    backend = ExtensionBackend()
    runtime = BrowserRuntime(backend=backend, config=config, pairing=store)
    conn = ExtensionConnection(_FakeSocket(), extension_id=PINNED_EXTENSION_ID, paired=True)
    backend.hold_waiting(conn, token)

    async def _release_fails(target, **kw):  # the close itself fails
        raise OSError("the socket could not be closed")

    backend.release = _release_fails  # type: ignore[method-assign]

    with pytest.raises(BrowserError) as caught:
        await runtime.forget()

    message = caught.value.message
    assert "could not" in message.lower() and "browser" in message.lower()
    assert "OSError" not in message and "release" not in message
    assert store.paired() is False, "the credential is still revoked first"
