"""The five credentials never substitute for one another (v1.235.0, Ship 1).

This file is the security contract of the Browser capability: plan §12.3's
cross-rejection table, pinned case by case for every route that exists in Ship 1.
The daemon is RCE-by-design (agents run shell, ``/terminals`` spawns a PTY), and
this ship adds a socket that drives the user's REAL, logged-in Chrome. So two
new ways to get in have to stay separate from every old one:

* the **browser pairing token** is accepted at ``/browser/ws`` and NOWHERE else;
  the install bearer is refused *there*, and the pairing token is refused on every
  ordinary route;
* the **pinned extension origin** (``chrome-extension://<pinned id>``) is admitted
  by ``HostOriginGuardMiddleware`` on exactly ``/browser/ws``, and refused on
  every other path, so an add-on the user installed for one purpose cannot reach
  the rest of the daemon.

The silent failure each assertion catches, in order of how expensive it would be:

1. ``test_the_install_bearer_is_refused_at_browser_ws`` — if the socket reused
   ``_ws_token_ok``, the install bearer would open it. That token is on disk in
   ``%APPDATA%/Iron Jarvis/token.txt`` and is handed to every local surface; a
   browser add-on holding it holds the whole daemon. The failure is invisible:
   everything WORKS, just with the wrong credential.
2. ``test_no_credential_and_no_pairing_flag_is_refused`` and
   ``test_the_socket_is_closed_when_install_auth_is_off_and_the_token_is_wrong`` —
   ``_ws_token_ok`` opens
   every socket when ``IRONJARVIS_TOKEN`` is unset, which is the DEFAULT for a
   fresh install. If the pairing verifier inherited that branch, a fresh install
   would let any caller with a loopback origin drive the user's browser, and the
   test suite would be green because the feature would work perfectly.
3. ``test_a_pairing_token_is_not_a_daemon_credential`` — a pairing token that also
   authenticated ordinary routes would turn the one credential we hand to a
   browser extension into a full daemon bearer.
4. ``test_the_pinned_extension_origin_is_refused_on_every_other_path`` — the
   exception is written inside ``__call__``, the only place holding
   ``scope["path"]``. A version that forgot the path test, or used
   ``startswith``, would admit the add-on origin app-wide and nothing would look
   different until an add-on used it.
5. ``test_a_different_extension_id_is_refused_everywhere`` — a
   ``startswith("chrome-extension://")`` check admits EVERY add-on the user has
   installed, which is the one thing a pinned id exists to prevent.
6. ``test_host_ok_still_rejects_a_non_loopback_host`` — the DNS-rebinding guard is
   untouched by this work. It is asserted here because the diff edits the same
   ``__call__``, and an accidental early ``return`` in the origin branch would
   have skipped it.
7. ``test_browser_test_only_ever_sends_a_read_method`` — D25: "Do not make Test
   mutate the page." A diagnostic that clicked or navigated would be the worst
   possible button to press twice, and only a test can keep it read-only.

``/browser/ws`` is registered on a BARE FastAPI app here, with the two real
middlewares added in the real order, because ``daemon/app.py`` is coordinator-
owned and wires ``_routes.browser.register(app, d)`` after this lands — the same
documented arrangement as ``tests/test_helpdocs_v1198.py``. The origin and host
cases that concern OTHER paths run against the real ``create_app`` instead, so
they are asserted against the app the user actually runs.

Every WebSocket assertion goes through ``tests._fakes.browser_peer.BrowserPeer``,
``_refused_ws`` (which only ever observes the handshake), or ``_Reader`` (a daemon
pump thread plus a bounded queue read). None of them performs an unbounded
receive: a ``TestClient`` WebSocket receive has no timeout, so one would hang the
release gate rather than fail it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import ExtensionBackend
from iron_jarvis.browser.identity import (
    EXTENSION_ID_ENV,
    PINNED_EXTENSION_ID,
    extension_origin,
)
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import BrowserRuntime
from iron_jarvis.core.db import open_db
from iron_jarvis.core.logging import (
    QueryCredentialRedactionFilter,
    install_noise_filters,
)
from iron_jarvis.daemon import auth
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import (
    HostOriginGuardMiddleware,
    TokenAuthMiddleware,
    _extension_origin_ok,
    browser_ws_token_ok,
)
from iron_jarvis.daemon.routes import browser as browser_routes

from tests._fakes.browser_peer import BrowserPeer

INSTALL_BEARER = "install-bearer-token-v1235"
#: A plausible pairing token that is NOT the one this install minted.
FOREIGN_PAIRING_TOKEN = "pairing-token-v1235-from-some-other-install"
#: A valid-SHAPE extension id (32 chars, a-p) that is NOT the pinned one.
OTHER_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
LOOPBACK_ORIGIN = "http://127.0.0.1:8788"


class _Config:
    """The one live setting the runtime reads, held by reference like the real Config."""

    def __init__(self, browser_access: str) -> None:
        self.browser_access = browser_access


class _StubDeps:
    """The create_app deps object, reduced to the one field these routes read."""

    def __init__(self, runtime: Any) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class _Bridge:
    """A REAL backend, a REAL ``PairingStore`` on a real database, and one minted token.

    Nothing about the credential is stubbed, deliberately: the property under test is
    which credential a route accepts, and a stubbed verifier would only be asserting
    the stub. The token is minted through the same ``open_request`` -> ``mint`` path
    the Pair button drives, so ``?token=<it>`` is exactly what a paired add-on sends.
    """

    def __init__(self, tmp_path: Any, *, access: str = "interactive", paired: bool = True) -> None:
        self.engine = open_db(tmp_path / "browser-auth.db")
        self.store = PairingStore(self.engine)
        self.backend = ExtensionBackend()
        self.runtime = BrowserRuntime(
            backend=self.backend, config=_Config(access), pairing=self.store
        )
        self.token = ""
        if paired:
            record = self.store.open_request(extension_id=PINNED_EXTENSION_ID)
            self.token = self.store.mint(record.request_id)
            assert self.token and self.token != INSTALL_BEARER

    def app(self) -> FastAPI:
        """A bare app with the REAL middlewares, in the real order, plus these routes.

        ``TokenAuthMiddleware`` is added first and ``HostOriginGuardMiddleware`` last,
        because ``add_middleware`` stacks outermost-last and ``daemon/app.py`` adds the
        guard outermost so a bad Host/Origin is refused before anything else runs.
        """
        app = FastAPI()
        browser_routes.register(app, _StubDeps(self.runtime))

        @app.get("/settings")
        def settings() -> dict[str, Any]:  # a stand-in for "any other path"
            return {"ok": True}

        app.add_middleware(TokenAuthMiddleware)
        app.add_middleware(HostOriginGuardMiddleware)
        return app


def _refused_ws(client: TestClient, url: str, headers: dict[str, str]) -> int:
    """Assert the socket was refused, and return the close code the daemon sent.

    A refusal arrives as ``WebSocketDisconnect`` out of the session's ``__enter__``
    (the daemon closes without accepting). Anything else means the socket OPENED,
    which is the failure this helper exists to name.
    """
    try:
        with client.websocket_connect(url, headers=headers):
            pass
    except WebSocketDisconnect as exc:
        return int(exc.code)
    raise AssertionError(f"{url} accepted a socket it must have refused (headers={headers})")


class _Reader:
    """Bounded reads off a RAW TestClient socket, pumped on a daemon thread.

    A ``TestClient`` WebSocket receive has no timeout, so a receive on the test thread
    HANGS the release gate whenever the daemon fails to send what the test is
    asserting — instead of failing it with a name. ``BrowserPeer`` solves that for the
    frames it owns; this exists only for what the peer does not expose, the raw close
    CODE, and it uses the same shape: a background pump does the blocking read, the
    test thread waits a bounded time and raises a named assertion. The thread is a
    daemon thread so one left blocked can never hold the interpreter open at exit.
    """

    #: The same bound ``tests/_fakes/browser_peer.py`` uses for one frame.
    WAIT_S = 10.0

    def __init__(self, ws: Any) -> None:
        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(ws,), daemon=True)
        self._thread.start()

    def _pump(self, ws: Any) -> None:
        while True:
            try:
                message = ws.receive()
            except Exception:  # the socket is gone; the test reads what arrived
                return
            self._queue.put(message)
            if message.get("type") == "websocket.close":
                return

    def _next(self, what: str) -> dict[str, Any]:
        try:
            return self._queue.get(timeout=self.WAIT_S)
        except queue.Empty:
            raise AssertionError(f"the daemon never sent {what}") from None

    def frame(self, frame_type: str) -> dict[str, Any]:
        """The next frame, asserted to be ``frame_type`` and not a close.

        The raw ASGI message the TestClient yields for a server-sent frame is
        ``{"type": "websocket.send", ...}`` — the app's own direction word, not
        ``websocket.receive``. Matching the wrong one here would turn every frame into
        "expected X, got ...", which reads like a protocol bug in the daemon.
        """
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


@pytest.fixture()
def install_auth(monkeypatch: pytest.MonkeyPatch) -> str:
    """Turn install-bearer auth ON, so a wrong credential is a 401 and not a pass."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_BEARER)
    return INSTALL_BEARER


@pytest.fixture()
def bridge(tmp_path) -> _Bridge:
    return _Bridge(tmp_path)


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {INSTALL_BEARER}", "Origin": LOOPBACK_ORIGIN}


# --------------------------------------------------------------------------- #
# 1. Only the pairing token opens /browser/ws
# --------------------------------------------------------------------------- #


def test_the_install_bearer_is_refused_at_browser_ws(install_auth, bridge):
    """The one credential every other surface accepts must not open this socket."""
    with TestClient(bridge.app()) as client:
        code = _refused_ws(
            client, f"/browser/ws?token={INSTALL_BEARER}", {"Origin": extension_origin()}
        )
    assert code == 1008
    assert bridge.backend.connected is False, "a refused socket must never become authoritative"


def test_a_pairing_token_from_another_install_is_refused(install_auth, bridge):
    """The verifier compares against THIS install's stored hash, not a token shape."""
    with TestClient(bridge.app()) as client:
        code = _refused_ws(
            client, f"/browser/ws?token={FOREIGN_PAIRING_TOKEN}", {"Origin": extension_origin()}
        )
    assert code == 1008
    assert bridge.backend.connected is False


def test_a_revoked_pairing_token_is_refused(install_auth, bridge):
    """Forget revokes: the credential that worked a moment ago stops working.

    The row is not deleted, it is stamped ``revoked_at``, so this is the case where a
    verifier that forgot the revoked filter would still authenticate a browser the user
    has explicitly forgotten — and nothing on screen would say so.
    """
    token = bridge.token
    assert bridge.store.revoke_all() == 1
    with TestClient(bridge.app()) as client:
        refused = _refused_ws(client, f"/browser/ws?token={token}", {"Origin": extension_origin()})
    assert refused == 1008


def test_a_header_borne_credential_cannot_open_the_socket_at_either_layer(install_auth, bridge):
    """An ``Authorization`` header cannot smuggle a credential onto this socket.

    The credential in the header is this install's REAL, VALID pairing token — the
    one thing that does open this socket when it arrives as ``?token=``. That is what
    makes the refusal about WHERE the credential was read from and nothing else: a
    test that put the install bearer in the header would pass even for a daemon that
    read headers happily, because the install bearer is not a pairing token either
    way. (That was this test until v1.235.0's mutation pass caught it.)

    Both layers that could grow a header fallback are asserted, because each one
    fails alone:

    * the ROUTE reads ``ws.query_params`` itself and only consults the verifier when
      a query token is present, so a header fallback added there opens the socket —
      the first half catches that;
    * the VERIFIER (``browser_ws_token_ok``) is the seam a later refactor would hand
      the whole socket to, so the second half drives it directly with a verifier that
      would ACCEPT anything, and asserts the store is never even offered the header
      value. A credential the store never sees cannot have been accepted.
    """
    with TestClient(bridge.app()) as client:
        code = _refused_ws(
            client,
            "/browser/ws",
            {"Origin": extension_origin(), "Authorization": f"Bearer {bridge.token}"},
        )
    assert code == 1008
    assert bridge.backend.connected is False

    class _HeaderOnly:
        query_params: dict[str, str] = {}
        headers = {"authorization": f"Bearer {bridge.token}"}

    offered: list[str] = []

    def _would_accept_anything(candidate: str) -> bool:
        offered.append(candidate)
        return True

    assert asyncio.run(browser_ws_token_ok(_HeaderOnly(), _would_accept_anything)) is False, (
        "the verifier read a credential out of the Authorization header"
    )
    assert offered == [], f"a header credential reached the pairing store: {offered}"


def test_no_credential_and_no_pairing_flag_is_refused(bridge):
    """No token and no ``?pairing=1`` is not a bootstrap; it is an unauthenticated caller."""
    with TestClient(bridge.app()) as client:
        assert _refused_ws(client, "/browser/ws", {"Origin": extension_origin()}) == 1008
    assert bridge.backend.connected is False


def test_the_socket_is_closed_when_install_auth_is_off_and_the_token_is_wrong(
    monkeypatch: pytest.MonkeyPatch, bridge
):
    """There is no "auth disabled" branch on this socket.

    ``_ws_token_ok`` opens every socket when ``IRONJARVIS_TOKEN`` is unset, which is
    the default on a fresh install. If the pairing verifier had inherited that, a fresh
    install would hand the user's logged-in browser to any caller — and every feature
    test would still be green.
    """
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    with TestClient(bridge.app()) as client:
        code = _refused_ws(
            client, "/browser/ws?token=not-the-pairing-token", {"Origin": extension_origin()}
        )
    assert code == 1008


def test_a_valid_pairing_token_opens_the_socket_and_is_told_it_is_active(install_auth, bridge):
    """The credential that IS accepted here, end to end: adopt, then ``browser.ready``."""
    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, token=bridge.token) as peer:
            assert peer.expect_ready()["active"] is True
            assert bridge.backend.connected is True
            assert bridge.backend.connection is not None
    assert bridge.backend.connected is False, (
        "teardown must retire the connection, or a browser that has gone reads as live"
    )


def test_the_verifier_never_raises_into_the_handshake():
    """A verifier that raises must be a 1008 close, not an unhandled error.

    FastAPI's exception handlers are HTTP-only, so an exception escaping a WebSocket
    handshake is not a policy close — it is the trap ``token_matches`` was rewritten
    for, one credential over. Driven with ``asyncio.run`` rather than an async test,
    because this repository's suite carries no asyncio plugin.
    """

    class _WS:
        query_params = {"token": "anything"}

    def _explodes(candidate: str) -> bool:
        raise RuntimeError("the pairing store is mid-migration")

    assert asyncio.run(browser_ws_token_ok(_WS(), _explodes)) is False

    async def _async_hit(candidate: str) -> object:
        # The REAL verifier is async and answers with the pairing ROW it matched,
        # not with True — an identity test would refuse every valid token.
        return {"id": "bpair_1"}

    assert asyncio.run(browser_ws_token_ok(_WS(), _async_hit)) is True

    async def _async_miss(candidate: str) -> object | None:
        return None

    assert asyncio.run(browser_ws_token_ok(_WS(), _async_miss)) is False

    class _NoToken:
        query_params: dict[str, str] = {}

    assert asyncio.run(browser_ws_token_ok(_NoToken(), lambda c: True)) is False, (
        "an absent token must fail closed without consulting the store"
    )


def test_the_verifier_reads_the_query_string_only():
    """A credential in a HEADER is not offered to the pairing store at all.

    Asserted directly on the helper, not only through the route: the route has its own
    query-string read, so a header fallback added HERE would be dead code today and a
    live hole the day the route is refactored to hand the whole socket over. The store
    is the witness — if it is never consulted, no header value can have been accepted.
    """

    class _HeaderOnly:
        query_params: dict[str, str] = {}
        headers = {"authorization": f"Bearer {FOREIGN_PAIRING_TOKEN}"}

    offered: list[str] = []

    def _verify(candidate: str) -> bool:
        offered.append(candidate)
        return True

    assert asyncio.run(browser_ws_token_ok(_HeaderOnly(), _verify)) is False
    assert offered == [], f"a header credential reached the pairing store: {offered}"


def test_the_credential_is_delivered_on_the_socket_and_never_in_a_body(install_auth, tmp_path):
    """The whole lifecycle of the one credential this feature mints, in one test.

    ``BrowserPeer.pair`` runs the real D06A handshake and asserts for itself that
    ``POST /browser/pair`` leaked nothing long enough to be a token; this adds the
    three properties that make the credential a credential:

    * it WORKS — the same plaintext opens a fresh socket, so what was delivered is
      what the store verifies;
    * **Disconnect keeps it** — the pair of buttons is only honest if one of them
      does not do the other's job;
    * **Forget revokes it** — and the token that worked one line earlier is refused,
      which is the only way to know Forget did more than change a label.
    """
    bridge = _Bridge(tmp_path, paired=False)
    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, headers=_bearer()) as peer:
            token = peer.pair()
            peer.expect_ready()
        assert token, "the pairing frame carried no token"

        with BrowserPeer(client, token=token, headers=_bearer()) as peer:
            peer.expect_ready()
            assert client.post("/browser/disconnect", headers=_bearer()).json() == {
                "disconnected": True
            }
        assert bridge.store.paired() is True, "Disconnect must not revoke the credential"

        with BrowserPeer(client, token=token, headers=_bearer()) as peer:
            peer.expect_ready()  # it still authenticates, which is what Disconnect means

        assert client.post("/browser/forget", headers=_bearer()).json() == {"forgotten": True}
        assert bridge.store.paired() is False
        assert _refused_ws(client, f"/browser/ws?token={token}", {"Origin": extension_origin()}) == 1008


# --------------------------------------------------------------------------- #
# 2. A pairing token is not a daemon credential
# --------------------------------------------------------------------------- #


def test_a_pairing_token_is_not_a_daemon_credential(tmp_path, install_auth):
    """Presented as a bearer OR as ``?token=``, a pairing token authenticates nothing."""
    bridge = _Bridge(tmp_path)
    with TestClient(create_app(str(tmp_path / "app"))) as client:
        header = client.get(
            "/settings",
            headers={"Authorization": f"Bearer {bridge.token}", "Origin": LOOPBACK_ORIGIN},
        )
        query = client.get(f"/settings?token={bridge.token}", headers={"Origin": LOOPBACK_ORIGIN})
        good = client.get("/settings", headers=_bearer())
    assert header.status_code == 401, header.text
    assert query.status_code == 401, query.text
    assert good.status_code == 200, (
        "the install bearer must still work — otherwise the two 401s above prove nothing"
    )


def test_health_is_token_exempt_by_design_so_no_credential_is_consulted(tmp_path, install_auth):
    """``/health`` answers a pairing token — and that is not a leak.

    Recorded here rather than asserted the other way round, because the plan's test
    list says "a pairing token is refused on /health and /settings" and that is NOT
    what this daemon does: ``/health`` is in ``auth._EXEMPT_EXACT`` so liveness probes
    and ``ironjarvis status`` work with no credential at all, and it therefore consults
    no token from anybody. The security property that matters — a pairing token is not
    a daemon credential — is pinned on ``/settings`` above. If ``/health`` ever stops
    being exempt, this test fails and points at the sentence that has to change.
    """
    with TestClient(create_app(str(tmp_path))) as client:
        anonymous = client.get("/health", headers={"Origin": LOOPBACK_ORIGIN})
        with_pairing = client.get(
            "/health",
            headers={
                "Authorization": f"Bearer {FOREIGN_PAIRING_TOKEN}",
                "Origin": LOOPBACK_ORIGIN,
            },
        )
    assert anonymous.status_code == 200, anonymous.text
    assert with_pairing.status_code == anonymous.status_code, (
        "/health is exempt, so a credential must change nothing about its answer"
    )
    assert "/health" in auth._EXEMPT_EXACT


def test_the_install_bearer_is_required_on_the_browser_http_routes(install_auth, bridge):
    """The HTTP half of the contract's third row: ordinary routes want the bearer."""
    with TestClient(bridge.app()) as client:
        anonymous = client.get("/browser/status", headers={"Origin": LOOPBACK_ORIGIN})
        with_pairing_token = client.get(
            f"/browser/status?token={bridge.token}", headers={"Origin": LOOPBACK_ORIGIN}
        )
        authorised = client.get("/browser/status", headers=_bearer())
    assert anonymous.status_code == 401, anonymous.text
    assert with_pairing_token.status_code == 401, (
        "the pairing token must not authenticate the card's own HTTP routes"
    )
    assert authorised.status_code == 200, authorised.text
    body = authorised.json()
    assert body["connected"] is False and body["paired"] is True


# --------------------------------------------------------------------------- #
# 3. The pinned extension origin, and only on /browser/ws
# --------------------------------------------------------------------------- #


def test_the_pinned_extension_origin_is_accepted_at_browser_ws(install_auth, tmp_path):
    """The add-on's own Origin opens the pairing socket. No other non-loopback one does."""
    bridge = _Bridge(tmp_path, paired=False)
    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, origin=extension_origin()) as peer:
            required = peer.expect_frame(P.FRAME_PAIRING_REQUIRED, what="browser.pairing_required")
            conn = bridge.backend.restricted_socket(required["request_id"])
            assert conn is not None, "the unpaired socket must be registered, not adopted"
            # A pairing socket never sends browser.hello — it had no token when it
            # opened — so the Origin header is the ONLY place this id can come from.
            # Without it every first pairing row would be written with no extension id.
            assert conn.extension_id == PINNED_EXTENSION_ID
            assert bridge.backend.connected is False, (
                "an unpaired socket must not read as connected anywhere"
            )
    assert required["request_id"].startswith(P.PAIRING_ID_PREFIX)


def test_a_loopback_origin_still_reaches_browser_ws(install_auth, tmp_path):
    """The exception ADDS one origin; it does not replace the loopback rule.

    The dashboard is served from ``127.0.0.1:8788``, so the ordinary rule must still
    apply on this path or every local caller loses it.
    """
    bridge = _Bridge(tmp_path, paired=False)
    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, origin=LOOPBACK_ORIGIN) as peer:
            peer.expect_frame(P.FRAME_PAIRING_REQUIRED, what="browser.pairing_required")


def test_the_pinned_extension_origin_is_refused_on_every_other_path(tmp_path, install_auth):
    """Constructed exactly as the reviewer's checklist asks: /settings, pinned Origin, 403."""
    origin = extension_origin()
    with TestClient(create_app(str(tmp_path))) as client:
        settings = client.get(
            "/settings", headers={"Origin": origin, "Authorization": f"Bearer {INSTALL_BEARER}"}
        )
        health = client.get("/health", headers={"Origin": origin})
    assert settings.status_code == 403, settings.text
    assert settings.json()["detail"] == "origin not allowed"
    assert health.status_code == 403, (
        "the guard is outermost, so even a token-exempt path refuses a bad origin"
    )


def test_the_origin_exception_is_path_exact_and_origin_exact(monkeypatch: pytest.MonkeyPatch):
    """The predicate itself: one path, one whole origin, read live.

    Unit-level because the shapes that would be wrong are shapes an end-to-end test
    cannot easily construct: a path PREFIX match would admit ``/browser/ws-anything``,
    and an origin prefix match would admit every add-on the user has installed.
    """
    pinned = extension_origin()
    assert _extension_origin_ok("/browser/ws", pinned) is True
    assert _extension_origin_ok("/browser/ws", pinned + "/") is True  # trailing slash
    assert _extension_origin_ok("/browser/ws-anything", pinned) is False
    assert _extension_origin_ok("/browser/wsx", pinned) is False
    assert _extension_origin_ok("/settings", pinned) is False
    assert _extension_origin_ok("/browser/ws", "") is False
    assert _extension_origin_ok("/browser/ws", extension_origin(OTHER_EXTENSION_ID)) is False
    assert _extension_origin_ok("/browser/ws", "chrome-extension://") is False

    # Read live: an install that overrides the pinned id moves the admitted origin with
    # it, rather than refusing every connection with no explanation.
    monkeypatch.setenv(EXTENSION_ID_ENV, OTHER_EXTENSION_ID)
    assert _extension_origin_ok("/browser/ws", extension_origin(OTHER_EXTENSION_ID)) is True
    assert _extension_origin_ok("/browser/ws", pinned) is False


def test_a_different_extension_id_is_refused_everywhere(tmp_path, install_auth, bridge):
    """Another add-on's origin is refused at ``/browser/ws`` AND on ordinary routes."""
    other = extension_origin(OTHER_EXTENSION_ID)
    with TestClient(bridge.app()) as client:
        assert _refused_ws(client, "/browser/ws?pairing=1", {"Origin": other}) == 1008
    assert bridge.backend.connected is False
    with TestClient(create_app(str(tmp_path / "app"))) as client:
        refused = client.get(
            "/settings", headers={"Origin": other, "Authorization": f"Bearer {INSTALL_BEARER}"}
        )
    assert refused.status_code == 403, refused.text


# --------------------------------------------------------------------------- #
# 4. The guard this diff edits was not weakened
# --------------------------------------------------------------------------- #


def test_host_ok_still_rejects_a_non_loopback_host(tmp_path, install_auth, bridge):
    """The DNS-rebinding guard is untouched, on HTTP and on the new socket.

    The origin exception was added inside the same ``__call__``; an early return in the
    wrong place would have skipped the Host check entirely, and a rebinding attack
    looks exactly like a working request until it isn't.
    """
    with TestClient(create_app(str(tmp_path / "app"))) as client:
        blocked = client.get("/health", headers={"Host": "attacker.example.com"})
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"] == "host not allowed"

    with TestClient(bridge.app()) as client:
        code = _refused_ws(
            client,
            "/browser/ws?pairing=1",
            {"Host": "attacker.example.com", "Origin": extension_origin()},
        )
    assert code == 1008
    assert bridge.backend.connected is False


def test_a_frame_other_than_the_pairing_ack_closes_an_unpaired_socket(install_auth, tmp_path):
    """The restricted state is a state machine: refusal is by frame TYPE.

    An unpaired socket is UNAUTHENTICATED. If it could send ``browser.response``, it
    could resolve a pending command future for a browser that never ran the command — a
    fabricated answer, from the one place in this feature that has no credential at all.
    """
    bridge = _Bridge(tmp_path, paired=False)
    with TestClient(bridge.app()) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            reader = _Reader(ws)
            reader.frame(P.FRAME_PAIRING_REQUIRED)
            ws.send_json({"id": "req_1", "type": P.FRAME_RESPONSE, "success": True, "result": {}})
            assert reader.close_code("the 1008 close after a forbidden frame") == 1008
    assert bridge.backend.connected is False, "an unpaired socket must never become authoritative"


def test_an_oversized_frame_is_refused_unread_and_the_socket_keeps_reading(
    install_auth, tmp_path
):
    """An oversized frame is dropped, named, and does NOT cost the browser its link.

    The frame sent here is a VALID ``browser.pairing_ack`` with padding after it, so
    the daemon has every reason to act on it and refuses anyway: ``last_error`` names
    the limit, and ``pairing_acked`` is still False. Then the SAME socket sends the
    same ack without the padding and it is honoured — which is the behaviour this case
    exists for. One malformed frame from the add-on (a page that returned a giant
    document, the common cause) must not drop the user's browser link; on a paired
    socket that would be Jarvis losing the browser mid-turn, and on this unpaired one
    it would abort a pairing the user is in the middle of approving.

    The ORDERING claim — that the byte count is taken BEFORE ``json.loads``, because
    the decode runs on the daemon's single event loop and a 40 MB parse there is every
    request in the app timing out (v1.153.1) — is NOT asserted here, and this test's
    name no longer says it is. It cannot be asserted from out here: with the cap moved
    after the parse, the frame is still refused before dispatch, so every observable
    below is identical. It is pinned where a spy can see it, in
    ``tests/test_browser_connection_v1235.py::test_an_oversized_frame_is_refused_before_json_loads``,
    which patches ``json.loads`` with a ``*args, **kw`` spy on a unit-level backend —
    a patch that cannot be done safely here, with a live TestClient and a daemon
    thread parsing JSON of their own.

    An unpaired caller can keep sending oversized frames until the pairing deadline;
    each is refused unread and named in ``last_error``, and the deadline is what ends
    it (``tests/test_browser_pairing_v1235.py::test_the_pairing_deadline_closes_the_socket_at_the_route``).
    Recorded here so the behaviour is a decision on the record rather than a surprise.
    """
    bridge = _Bridge(tmp_path, paired=False)
    with TestClient(bridge.app()) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            reader = _Reader(ws)
            required = reader.frame(P.FRAME_PAIRING_REQUIRED)
            conn = bridge.backend.restricted_socket(required["request_id"])
            assert conn is not None
            ack = dict(P.pairing_ack_frame(required["request_id"]))
            oversized = dict(ack)
            oversized["padding"] = "x" * (P.MAX_FRAME_BYTES + 1)
            ws.send_json(oversized)
            _wait_for(
                lambda: "refused unread" in bridge.backend.last_error,
                "the oversized frame to be refused unread",
            )
            assert str(P.MAX_FRAME_BYTES) in bridge.backend.last_error, (
                "the refusal must name the limit, or the user cannot act on it"
            )
            assert conn.pairing_acked is False, "an oversized frame must not be acted on"

            # ... and the socket is still being read: the same ack, within the cap.
            ws.send_json(ack)
            _wait_for(
                lambda: conn.pairing_acked is True,
                "the in-cap pairing ack to be honoured after the oversized one",
            )
            assert conn.closed is False, "one oversized frame must not close the socket"
    assert bridge.backend.connected is False


def _wait_for(predicate, what: str, *, attempts: int = 200, step_s: float = 0.05) -> None:
    """Poll ``predicate`` on the test thread, bounded, then raise a named assertion.

    The daemon acts on a frame in its own task, so there is no signal to await from
    here; the same bounded-poll shape ``tests/_fakes/browser_peer.py`` uses is what
    keeps a missing outcome a NAMED failure instead of a hung release gate. Nothing
    asserts how long it took.
    """
    for _ in range(attempts):
        if predicate():
            return
        time.sleep(step_s)
    raise AssertionError(f"timed out waiting for {what}")


# --------------------------------------------------------------------------- #
# 5. POST /browser/test never mutates the page (D25)
# --------------------------------------------------------------------------- #


def test_browser_test_only_ever_sends_a_read_method(install_auth, bridge):
    """Test is a diagnostic, so its one method is a member of ``READ_METHODS``.

    Driven against the real backend and the real add-on peer, so the assertion is what
    actually crossed the socket, not what a stub recorded.
    """
    assert browser_routes.TEST_METHOD in P.READ_METHODS
    assert browser_routes.TEST_METHOD not in P.LOCAL_UI_METHODS
    assert browser_routes.TEST_METHOD not in P.PAGE_ACTION_METHODS

    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, token=bridge.token, headers=_bearer()) as peer:
            peer.expect_ready()
            answer = client.post("/browser/test", headers=_bearer())
            assert [method for method, _ in peer.commands] == [browser_routes.TEST_METHOD], (
                f"Test sent something other than its one read method: {peer.commands}"
            )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ok"] is True
    assert body["active_tab"]["title"] == "Example Domain"
    assert isinstance(body["round_trip_ms"], int)


def test_browser_test_refuses_before_touching_the_browser_when_access_is_off(
    install_auth, tmp_path
):
    """Access off means Jarvis does not speak to the browser, not even to diagnose it."""
    bridge = _Bridge(tmp_path, access="off")
    with TestClient(bridge.app()) as client:
        with BrowserPeer(client, token=bridge.token, headers=_bearer()) as peer:
            peer.expect_ready()
            body = client.post("/browser/test", headers=_bearer()).json()
            assert peer.commands == [], "no command may cross the socket while access is off"
    assert body["ok"] is False
    assert body["code"] == "BROWSER_ACCESS_OFF"


def test_request_host_permission_refuses_when_no_browser_is_connected(install_auth, bridge):
    """No socket, no directive: the card is told to connect a browser, not that it asked."""
    with TestClient(bridge.app()) as client:
        answer = client.post("/browser/request-host-permission", headers=_bearer())
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"].startswith("BROWSER_NOT_CONNECTED: ")


# --------------------------------------------------------------------------- #
# 6. The credential never reaches the log file (D06A)
# --------------------------------------------------------------------------- #


class _Capture(logging.Handler):
    """Formats every record it is handed, so an assertion reads what a FILE would.

    Asserting on ``record.msg`` would miss a token that arrives through
    ``record.args``, which is exactly the shape uvicorn uses — so the pin has to run
    the formatter, the same way the desktop's file handler does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _through_logger(name: str, msg: str, *args: Any) -> list[str]:
    """Emit one record on a real uvicorn logger, with the real filters installed.

    The filter is asserted through ``logging.getLogger(name).handle`` rather than by
    calling ``filter()`` directly, so the pin also covers the WIRING: a filter that
    exists but is not attached to ``uvicorn.error`` redacts nothing, and that logger
    is where the WebSocket handshake line is written.
    """
    logger = logging.getLogger(name)
    install_noise_filters()
    capture = _Capture()
    capture.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(capture)
    previous, logger.propagate = logger.propagate, False
    level, forced = logger.level, logging.INFO
    logger.setLevel(forced)
    try:
        logger.handle(
            logging.LogRecord(name, logging.INFO, __file__, 1, msg, args or None, None)
        )
    finally:
        logger.removeHandler(capture)
        logger.propagate = previous
        logger.setLevel(level)
    return capture.lines


def test_a_pairing_token_on_the_socket_url_never_reaches_the_log(tmp_path):
    """uvicorn logs the whole path-with-query; the token in it is redacted first.

    This is the leak D06A forbids, verified on the user's OWN ``daemon.log`` before it
    was closed: uvicorn writes ``'%s - "WebSocket %s" [accepted]'`` with
    ``get_path_with_query_string(scope)`` on ``uvicorn.error``, and the desktop tees
    that logger into ``%APPDATA%/Iron Jarvis/logs/daemon.log`` — a 5 MB rotation any
    local process can read WITHOUT holding a credential, and the file a user pastes
    into a bug report. A WebSocket handshake cannot carry an ``Authorization`` header,
    so the token has nowhere to ride but the URL; the redaction therefore has to
    happen at the logger, which is what this asserts.

    Holding that token, a caller opens ``/browser/ws``, D08 makes the NEWER socket
    authoritative, and the user's real Chrome is replaced by an impostor answering
    ``browser_list_tabs`` for their browser. The same line leaks the INSTALL BEARER on
    ``/events?token=``, which is why both are asserted here.
    """
    bridge = _Bridge(tmp_path)
    token = bridge.token
    assert token

    accepted = _through_logger(
        "uvicorn.error", '%s - "WebSocket %s" [accepted]', "127.0.0.1:52341",
        f"/browser/ws?token={token}",
    )
    assert accepted, "the record never reached a handler"
    assert token not in accepted[0], f"the pairing token is in the log line: {accepted[0]}"
    assert "token=REDACTED" in accepted[0], accepted[0]
    assert "/browser/ws" in accepted[0], "the PATH must survive — the line still has to be useful"

    events = _through_logger(
        "uvicorn.access", '%s - "%s %s HTTP/%s" %d', "127.0.0.1:52342", "GET",
        f"/events?token={INSTALL_BEARER}", "1.1", 101,
    )
    assert events, "the record never reached a handler"
    assert INSTALL_BEARER not in events[0], f"the install bearer is in the log line: {events[0]}"
    assert "token=REDACTED" in events[0], events[0]


def test_a_log_line_with_no_credential_is_left_exactly_as_it_was():
    """Redaction that also rewrote ordinary lines would be a new bug, not a fix.

    The daemon's log is how the user diagnoses a browser that will not connect, so a
    filter that ate query strings wholesale would trade a leak for a blind spot.
    """
    line = _through_logger(
        "uvicorn.access", '%s - "%s %s HTTP/%s" %d', "127.0.0.1:52343", "GET",
        "/browser/status?since=5&project=taxes", "1.1", 200,
    )
    assert line == ['127.0.0.1:52343 - "GET /browser/status?since=5&project=taxes HTTP/1.1" 200']
    assert "REDACTED" not in line[0]


def test_the_redaction_filter_never_raises_on_a_record_it_did_not_expect():
    """A filter that raises aborts the CALLER's log call — inside an except branch.

    Every ``logger.exception`` in the daemon runs through this filter (v1.229.0), so a
    ``TypeError`` here would swallow the traceback that was being reported and, in a
    ``finally``, could take down the request that was already failing. Three records
    that do not look like uvicorn's are pushed through: non-string args, a non-string
    ``msg``, and a hostile args tuple that raises the moment it is iterated.
    """
    f = QueryCredentialRedactionFilter()

    odd = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "%s %s", (None, 5), None)
    assert f.filter(odd) is True
    assert odd.getMessage() == "None 5"

    class _NotAString:
        def __str__(self) -> str:
            return "an object with no query string"

    weird = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, _NotAString(), None, None)
    assert f.filter(weird) is True
    assert "no query string" in weird.getMessage()

    class _Hostile(tuple):
        def __iter__(self):
            raise RuntimeError("this tuple refuses to be read")

    hostile = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, "%s", _Hostile(("?token=leaked",)), None
    )
    assert f.filter(hostile) is True, "a record that cannot be redacted is still not a crash"
    rendered = hostile.getMessage()
    assert "leaked" not in rendered, (
        "a record the filter could not redact must not print its content"
    )
    assert "could not be redacted" in rendered, rendered
