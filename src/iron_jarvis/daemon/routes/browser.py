"""Browser Bridge routes: the add-on's socket, and the Your browser card's HTTP surface.

``/browser/ws`` is the only WebSocket in this app that does NOT accept the install
bearer token (D06, plan §12.3). It accepts exactly one credential — the browser
pairing token — and exactly one non-loopback origin, the pinned add-on's
(``auth._extension_origin_ok``). Everything else closes with 1008.

This module is deliberately THIN. The protocol state machine, the frame size cap,
the pending-future map and the D08 replacement sequence all live in
:class:`~iron_jarvis.browser.extension_backend.ExtensionBackend`, and the
credential lives in :class:`~iron_jarvis.browser.pairing.PairingStore`; the routes
reach both through :class:`~iron_jarvis.browser.service.BrowserRuntime`. That split
is not tidiness: a route that re-implemented the restricted-frame rule, or the size
check, would be a SECOND policy, and one of the two would eventually be wrong
without anything looking different (the "one definition" rule this repository
learned at v1.231.0). So what is genuinely this module's, and nowhere else:

* **Guarding the credential BEFORE ``accept()``**, the same discipline as
  ``/events`` (``routes/system.py``): a socket that is accepted and then closed has
  already completed a handshake with a caller we had decided to refuse.
* **Driving the read loop**, and racing it against the pairing deadline with
  ``asyncio.wait(..., FIRST_COMPLETED)`` — again copying ``/events``. Without the
  receiver in that race, an add-on that goes away while the user has not pressed
  Pair leaves this coroutine parked and the card offering a Pair button for a
  browser that is gone.
* **Enforcing the pairing deadline on the socket** (D06A). ``PairingStore`` owns
  the clock and hands back expired records from ``expired()``; only the route holds
  the socket, so only the route can close it with 1008.
* **Retiring the connection in a ``finally``**, so a dropped socket fails its
  in-flight futures instead of leaving a tool call hanging forever.
* **Refusing to make ANY socket authoritative while ``browser_access`` is off.**
  "Off drops the live socket" is a promise a close cannot keep on its own: the
  add-on treats every non-1008 close as ordinary and reconnects about a second
  later, and 1008 would make it delete its stored pairing token. So a
  token-bearing socket that arrives while access is off is accepted, told
  ``browser.ready {active: false, access: "off"}``, and held INERT — never
  ``_conn``, so nothing can command it, ``GET /browser/status`` says not
  connected, and its frames are read and discarded rather than caching a tab or
  publishing a page title. The decision is re-read on every idle tick, so
  switching access back on promotes that same socket instead of making the user
  reconnect. The refusal is the DAEMON's: an add-on that chose to reconnect
  anyway changes nothing, which is the difference between enforcement and a
  client being polite.

Two things this module reads that the card contract depends on, and their reasons:

* ``GET /browser/status`` reads ``backend.status()`` and the pairing store, NOT
  ``runtime.status()``. The runtime's version makes a live round trip to the
  browser, which is right for the ``browser_get_status`` TOOL and wrong for a card
  that polls: a wedged add-on would make every poll wait out the 15-second command
  timeout, and the page the user opened to find out that their browser is wedged
  would be the page that hangs. The plan says this route never fails; a route that
  takes 15 seconds has failed.
* ``POST /browser/test`` calls ``runtime.active_tab()`` — a READ method, and the
  only method this route may ever send (D25: "Do not make Test mutate the page").
  ``TEST_METHOD`` names it so the read-only property is a thing a test asserts
  against ``protocol.READ_METHODS`` rather than a promise in a docstring.

The plaintext pairing token never passes through this module: ``complete_pairing``
mints it and hands it to the one ``browser.paired`` frame, and
``POST /browser/pair`` answers ``{"paired": true}``.

Moved-into-routes convention: closure-local state is reached through ``d`` (see the
deps object built in create_app). ``d.platform.browser`` is a coordinator edit to
``platform.py`` and every access here is guarded, so the daemon boots and every
route degrades honestly while that field does not yet exist.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..auth import browser_ws_token_ok
from ...browser import protocol as P
from ...browser.errors import BrowserError, BrowserErrorCode, browser_error
from ...browser.extension_backend import ExtensionConnection
from ...browser.identity import pinned_extension_id
from ...browser.service import ACCESS_OFF

logger = logging.getLogger("iron_jarvis.browser")

#: Fallback pairing deadline, used only when the store does not carry its own
#: ``deadline_s``. ``PairingStore`` is the source of truth (a test shortens it
#: there rather than sleeping); this exists so a stand-in store cannot make the
#: socket immortal. Read at call time, so it is also a monkeypatch seam — the same
#: shape ``_MAX_UPLOAD_BYTES`` has on ``daemon/app.py``.
_PAIRING_DEADLINE_S = P.PAIRING_DEADLINE_S

#: How long the unpaired read waits before re-checking the deadline. A SLICE, not
#: the deadline itself: the pairing future is resolved by another task
#: (``POST /browser/pair`` → ``deliver_pairing``), so a single wait of the whole
#: deadline would still be parked when the socket became authoritative and would
#: then close a PAIRED browser with 1008. Nothing asserts this duration.
_PAIRING_POLL_S = 0.25

#: How long a socket held INERT (Browser access is off) waits before re-reading the
#: access level. A SLICE, like ``_PAIRING_POLL_S``: the setting is changed by another
#: task (``PUT /settings``), and nothing asserts this duration.
_ACCESS_POLL_S = 0.25

#: The ONE method ``POST /browser/test`` may send, and a member of
#: ``protocol.READ_METHODS`` — asserted by test, because a diagnostic that
#: navigated or clicked would be the worst possible button to press twice (D25).
TEST_METHOD = P.METHOD_ACTIVE_TAB

#: The Origin prefix an add-on sends. Used only to learn the extension id before
#: ``browser.hello`` arrives; the ORIGIN itself is authorised by
#: ``auth._extension_origin_ok`` against the pinned id, never here.
_EXTENSION_ORIGIN_PREFIX = "chrome-extension://"


class PairBody(BaseModel):
    """``POST /browser/pair`` — the pending request id the card is offering.

    Defined here rather than in ``daemon/schemas.py`` because that module is
    coordinator-owned; ``routes/fleet.py`` and ``routes/projects.py`` already keep
    local request models this way.
    """

    request_id: str = ""


def _runtime(d) -> Any:
    """``d.platform.browser``, or ``None`` while that field does not exist yet.

    ``Platform.browser`` is added by the coordinator's edit to ``platform.py``.
    Until then — and on any install where the browser package failed to build —
    these routes must still answer: ``GET /browser/status`` is documented never to
    fail, and the card polls it on every visit. A missing runtime is "not
    connected", never a 500 the page renders as "daemon offline".
    """
    platform = getattr(d, "platform", None)
    return getattr(platform, "browser", None)


def _disconnected_status() -> dict[str, Any]:
    """The status shape for "there is no browser here", in one place."""
    return {
        "connected": False,
        "access": "off",
        "host_permission": False,
        "extension_id": "",
        # The id the daemon EXPECTS, as opposed to ``extension_id`` above, which is
        # whatever is connected right now (empty when nothing is). Public material,
        # and the card needs it precisely when nothing is connected: a pairing
        # request arrives from an unauthenticated socket, and this is the only fact
        # that distinguishes the real add-on from anything else on the machine.
        "expected_extension_id": pinned_extension_id(),
        "active_tab": None,
        "pending_pairing": None,
        "paired": False,
        "last_error": None,
    }


def _refuse(code: BrowserErrorCode | str, status: int, message: str = "") -> HTTPException:
    """An HTTPException whose ``detail`` is a STRING carrying the code and remedy.

    A dict detail would render as ``[object Object]``: ``lib/api.ts``'s
    ``flattenDetail`` only walks a LIST, and ``String({})`` is what every other
    shape becomes. So the code is prefixed into the sentence instead — the card
    shows a remedy, and the code stays greppable in a bug report.

    The code comes from ``browser_error``, never from ``str(code)``:
    ``BrowserErrorCode`` is a ``str`` mixin whose ``str()`` is still
    ``"BrowserErrorCode.EXTENSION_ERROR"``, which is exactly the leak that enum's
    own docstring warns about.
    """
    envelope = browser_error(code)
    return HTTPException(
        status_code=status, detail=f"{envelope['code']}: {message or envelope['message']}"
    )


def _error_body(code: BrowserErrorCode | str, elapsed_ms: int, message: str = "") -> dict[str, Any]:
    """The ``POST /browser/test`` failure body: ok false, with a code and a remedy."""
    envelope = browser_error(code)
    return {
        "ok": False,
        "detail": message or envelope["message"],
        "code": envelope["code"],
        "round_trip_ms": elapsed_ms,
        "active_tab": None,
    }


def _origin_extension_id(ws: WebSocket) -> str:
    """The add-on's id from the Origin header, or ``""``.

    Only to give the backend (and so the pairing row) an id before
    ``browser.hello`` arrives — a pairing socket never sends hello at all, because
    it had no token when it opened, so without this every first pairing row would
    be recorded with an empty ``extension_id``. It authorises NOTHING: the origin
    was already checked against the pinned id by ``HostOriginGuardMiddleware``,
    which is the only place that decision may be made.
    """
    origin = (ws.headers.get("origin") or "").strip().rstrip("/")
    if not origin.lower().startswith(_EXTENSION_ORIGIN_PREFIX):
        return ""
    return origin[len(_EXTENSION_ORIGIN_PREFIX) :]


def _access_off(runtime: Any) -> bool:
    """Whether Browser access is off, read LIVE and failing closed.

    Read on every call for the reason ``BrowserRuntime.access`` is: ``PUT /settings``
    mutates the live ``Config``, and a socket decision made from a value cached at
    boot is a capability the user switched off that keeps working until a restart. A
    runtime that cannot answer counts as off — the fail-safe direction.
    """
    try:
        return str(runtime.access() or ACCESS_OFF).strip() == ACCESS_OFF
    except Exception:  # noqa: BLE001 — an unreadable setting must not widen access
        logger.debug("browser access unreadable; treating it as off", exc_info=True)
        return True


def _preferred_pending(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """The pending pairing ask the card should offer, or ``None``.

    Oldest-first is the store's order and it is the wrong choice ALONE: a local
    process that reopens a pairing socket every second is always the oldest offer, so
    a user who has just loaded the real add-on and presses Pair would hand the
    credential to whatever asked first. A request whose ``extension_id`` is the pinned
    add-on's therefore wins over one that has no id (any local process can open a
    pairing socket — ``/browser/ws?pairing=1`` is the one endpoint that needs no
    credential), and among equals the oldest still wins so the offer is stable across
    polls. The card renders the id beside the ask, because preferring the right row is
    not the same as telling the user which row they are approving.
    """
    if not rows:
        return None
    pinned = pinned_extension_id()
    for row in rows:
        if str(row.get("extension_id") or "") == pinned:
            return row
    return rows[0]


def _deadline_s(store: Any) -> float:
    """The pairing deadline, from the store that owns it."""
    try:
        return float(getattr(store, "deadline_s", _PAIRING_DEADLINE_S))
    except (TypeError, ValueError):
        return float(_PAIRING_DEADLINE_S)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    # ----------------------------------------------------------------- socket

    async def _open_pairing(runtime: Any, conn: ExtensionConnection) -> bool:
        """Register an unpaired socket and offer it a request id. False if it failed.

        ``open_request`` runs in a thread for the same reason every other
        ``PairingStore`` call does: the store is documented blocking (a lock, and a
        prune), and the daemon is ONE loop.
        """
        store = getattr(runtime, "pairing", None)
        if store is None:
            return False
        record = await asyncio.to_thread(
            lambda: store.open_request(extension_id=conn.extension_id)
        )
        if record is None:
            # The registry is full (PairingStore.MAX_PENDING_PAIRINGS). This socket
            # needed no credential to get here, so a flood must cost the daemon one
            # refusal and nothing else: close THIS socket and leave every pairing
            # already in flight — including the user's real browser — untouched.
            return False
        conn.pairing_request_id = record.request_id
        runtime.backend.register_restricted(conn)
        return await conn.send(P.pairing_required_frame(record.request_id))

    async def _pairing_lapsed(runtime: Any, conn: ExtensionConnection, expires_at: float) -> bool:
        """Whether this unpaired socket has run out of time (D06A).

        Two signals, both needed. ``PairingStore.expired()`` is the store's own
        verdict and the reason it retires records instead of deleting them — it is
        drained here so the retired list stays bounded. But ``expired()`` hands each
        record out exactly ONCE, so a second socket's tick can drain the record this
        socket was waiting for; the absolute ``expires_at`` computed at open time is
        the backstop that makes the close independent of who drained what.

        Neither signal fires for a request that was CONSUMED by pairing: ``mint``
        drops it from the registry without retiring it, so a socket mid-pairing is
        never mistaken for an abandoned one.
        """
        store = getattr(runtime, "pairing", None)
        if store is not None:
            try:
                retired = await asyncio.to_thread(store.expired)
            except Exception:  # noqa: BLE001 — a store failure must not kill the socket
                logger.debug("pairing sweep failed", exc_info=True)
                retired = []
            if any(record.request_id == conn.pairing_request_id for record in retired):
                return True
        return asyncio.get_running_loop().time() >= expires_at

    async def _pump(
        ws: WebSocket, runtime: Any, conn: ExtensionConnection, *, inert: bool = False
    ) -> None:
        """Read frames until the socket ends, then retire the connection.

        Every inbound frame goes to ``backend.handle_raw``, which measures BEFORE it
        parses (the 512 KB cap, on the single event loop — v1.153.1) and enforces the
        restricted-frame rule from inside the state machine. A ``False`` from it
        means the socket has already been closed and must not be read again.

        ``inert`` is a credentialled socket the daemon refuses to make authoritative
        because Browser access is OFF. Its frames are read and DISCARDED — never
        handed to ``handle_raw`` — because a ``browser.event`` from it would cache the
        user's active tab and publish their page's title on the bus while the
        capability that authorises reading their browser is switched off. The access
        level is re-read on every idle tick, so switching it back on promotes this
        socket in place instead of making the user reconnect.
        """
        backend = runtime.backend
        loop = asyncio.get_running_loop()
        expires_at = loop.time() + _deadline_s(getattr(runtime, "pairing", None))
        reason, detail = "closed", ""
        recv = asyncio.ensure_future(ws.receive())
        try:
            while True:
                if inert:
                    done, _ = await asyncio.wait(
                        {recv}, timeout=_ACCESS_POLL_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        if _access_off(runtime):
                            continue
                        # The user turned Browser access back on: this socket may
                        # become the authoritative one now, and adopt() sends the
                        # browser.ready that says so.
                        await backend.adopt(conn)
                        inert = False
                        continue
                    try:
                        message = recv.result()
                    except (WebSocketDisconnect, RuntimeError):
                        break
                    if message.get("type") == "websocket.disconnect":
                        break
                    recv = asyncio.ensure_future(ws.receive())
                    continue
                if conn.paired:
                    done, _ = await asyncio.wait({recv}, return_when=asyncio.FIRST_COMPLETED)
                else:
                    done, _ = await asyncio.wait(
                        {recv}, timeout=_PAIRING_POLL_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        if await _pairing_lapsed(runtime, conn, expires_at):
                            await conn.close(1008)
                            reason, detail = "closed", "the pairing deadline passed"
                            break
                        continue
                try:
                    message = recv.result()
                except (WebSocketDisconnect, RuntimeError):
                    break
                if message.get("type") == "websocket.disconnect":
                    break
                raw = message.get("text")
                if raw is None:
                    raw = message.get("bytes") or b""
                if not await backend.handle_raw(conn, raw):
                    # The backend closed it: a restricted socket sent something
                    # other than its pairing ack. Its own last_error already says
                    # which frame type, so nothing is added here.
                    reason, detail = "closed", "the browser broke the pairing protocol"
                    break
                recv = asyncio.ensure_future(ws.receive())
        finally:
            recv.cancel()
            try:
                # In a finally, always: a socket that dies mid-command must fail its
                # in-flight futures rather than leave a tool call awaiting forever.
                await backend.release(conn, reason=reason, detail=detail)
            except Exception:  # noqa: BLE001 — teardown must not raise into ASGI
                logger.debug("browser connection release failed", exc_info=True)

    @app.websocket("/browser/ws")
    async def browser_ws(ws: WebSocket) -> None:
        """The add-on's one socket. A pairing token, or ``?pairing=1``; nothing else.

        The install bearer is refused here by construction: ``browser_ws_token_ok``
        consults the pairing store and never ``IRONJARVIS_TOKEN``, and it reads the
        query string only — so an ``Authorization`` header cannot smuggle a
        credential in either (§12.3).
        """
        runtime = _runtime(d)
        if runtime is None or getattr(runtime, "backend", None) is None:
            # Close before accept rather than accept and then fail every frame.
            await _close(ws, 1008)
            return
        token = (ws.query_params.get("token") or "").strip()
        wants_pairing = (ws.query_params.get("pairing") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if token:
            if not await browser_ws_token_ok(ws, runtime.verify_token):
                await _close(ws, 1008)
                return
        elif not wants_pairing:
            # Fail closed. Neither a token nor the pairing flag is not a bootstrap
            # attempt, it is an unauthenticated caller.
            await _close(ws, 1008)
            return

        await ws.accept()
        conn = ExtensionConnection(
            ws, extension_id=_origin_extension_id(ws), paired=bool(token)
        )
        inert = False
        if token:
            if _access_off(runtime):
                # OFF MEANS OFF, AND THE REFUSAL IS THE DAEMON'S. Closing the socket
                # is not enough: the add-on treats any non-1008 close as ordinary and
                # reconnects about a second later, and 1008 would make it DELETE its
                # pairing token (socket.ts onClose) — so a browser whose user has
                # switched Browser access off would otherwise be back, authoritative,
                # in one second, with the card reading "Connected". This socket is
                # therefore never adopted: it holds no _conn, so `backend.connected`
                # is False, GET /browser/status says not connected, and every command
                # refuses at BrowserRuntime.require. The add-on is TOLD, in the
                # protocol's own words — browser.ready {active: false, access: "off"}
                # — instead of being left to guess from a close code, and its frames
                # are discarded while it waits. Nothing here trusts the client.
                inert = True
                await conn.send(runtime.backend.ready_frame(active=False))
            else:
                # adopt() sends browser.ready and performs the D08 replacement,
                # including failing every command in flight on the outgoing socket.
                await runtime.backend.adopt(conn)
        elif not await _open_pairing(runtime, conn):
            await _close(ws, 1008)
            return
        await _pump(ws, runtime, conn, inert=inert)

    # ------------------------------------------------------------------ HTTP

    @app.get("/browser/status")
    async def browser_status() -> dict[str, Any]:
        """Everything the Your browser card renders. NEVER fails, and never waits.

        Reads the backend's CACHED view plus the pairing store; it does not command
        the browser. See the module docstring: the live round trip belongs to the
        ``browser_get_status`` tool, and a card poll that waits out a 15-second
        command timeout is the page the user opened to diagnose a wedged browser
        hanging on the wedged browser.
        """
        runtime = _runtime(d)
        if runtime is None:
            return _disconnected_status()
        answer = _disconnected_status()
        try:
            view = dict(runtime.backend.status())
            answer.update(
                {
                    "connected": bool(view.get("connected")),
                    "access": str(runtime.access() or "off"),
                    "host_permission": bool(view.get("host_permission")),
                    "extension_id": str(view.get("extension_id") or ""),
                    "active_tab": view.get("active_tab") or None,
                    "last_error": view.get("last_error") or None,
                }
            )
        except Exception:
            logger.debug("browser transport status degraded", exc_info=True)
        try:
            rows = await runtime.pending_pairings()
            answer["pending_pairing"] = _preferred_pending(rows)
        except Exception:
            logger.debug("pending pairing lookup degraded", exc_info=True)
        try:
            store = getattr(runtime, "pairing", None)
            answer["paired"] = bool(store is not None and await asyncio.to_thread(store.paired))
        except Exception:
            logger.debug("pairing lookup degraded", exc_info=True)
        return answer

    @app.post("/browser/pair")
    async def browser_pair(body: PairBody) -> dict[str, bool]:
        """Approve a pending browser: mint the credential and deliver it on ITS socket.

        The response body is ``{"paired": true}`` and carries no token, ever — and
        it cannot, because ``complete_pairing`` does not return one. A token in a
        JSON body lands in browser devtools, in every HTTP trace, and in whatever
        the user pastes into a bug report.

        The code-to-status mapping is the plan's (§3.1) and each code exists so the
        card can say the right thing: a stale Pair button (404) and a browser that
        is already paired (409) need different words.
        """
        runtime = _runtime(d)
        if runtime is None:
            raise _refuse(
                BrowserErrorCode.EXTENSION_ERROR, 503, "the browser bridge is unavailable"
            )
        try:
            await runtime.complete_pairing((body.request_id or "").strip())
        except BrowserError as exc:
            status = 404 if exc.code == BrowserErrorCode.PAIRING_REQUIRED.value else 409
            raise _refuse(exc.code, status, exc.message) from exc
        return {"paired": True}

    @app.post("/browser/disconnect")
    async def browser_disconnect() -> dict[str, bool]:
        """End the live socket and KEEP the credential. Never fails.

        The pair with Forget below: Disconnect is "stop for now", Forget ends the
        relationship. Collapsing them would make one of the two buttons a lie.
        """
        runtime = _runtime(d)
        if runtime is None:
            return {"disconnected": False}
        try:
            # suspend=True: a person pressed a button that says stop, so the add-on
            # is asked to STAY away rather than reconnect a second later and undo it.
            # The add-on's own panel offers the inverse (Connect).
            return {"disconnected": bool(await runtime.disconnect(suspend=True))}
        except Exception:
            logger.debug("POST /browser/disconnect found nothing to close", exc_info=True)
            return {"disconnected": False}

    @app.post("/browser/forget")
    async def browser_forget() -> dict[str, bool]:
        """Revoke every pairing, then drop the socket. Never fails.

        ``forgotten`` is whether a credential actually died, not whether the button
        was pressed: reporting true with nothing revoked would tell the user their
        browser was forgotten while the add-on kept reconnecting silently.
        """
        runtime = _runtime(d)
        if runtime is None:
            return {"forgotten": False}
        try:
            return {"forgotten": bool(await runtime.forget())}
        except Exception:
            logger.debug("POST /browser/forget found no pairing to revoke", exc_info=True)
            return {"forgotten": False}

    @app.post("/browser/test")
    async def browser_test() -> dict[str, Any]:
        """A READ-ONLY round trip to the user's browser, reported honestly (D25).

        ``round_trip_ms`` is a REPORTED measurement, never a threshold: nothing
        asserts it here or in a test, and a slow browser is still a working one.
        """
        loop_start = asyncio.get_running_loop().time()

        def _ms() -> int:
            return int((asyncio.get_running_loop().time() - loop_start) * 1000)

        runtime = _runtime(d)
        if runtime is None:
            return _error_body(BrowserErrorCode.BROWSER_NOT_CONNECTED, _ms())
        try:
            # active_tab() applies the access gate (BROWSER_ACCESS_OFF before any
            # frame is sent) and maps "no window open" to None rather than an error.
            tab = await runtime.active_tab()
        except BrowserError as exc:
            return _error_body(exc.code, _ms(), exc.message)
        except Exception as exc:  # noqa: BLE001 — a diagnostic must never 500
            return _error_body(
                BrowserErrorCode.EXTENSION_ERROR, _ms(), f"{type(exc).__name__}: {exc}"
            )
        return {
            "ok": True,
            "detail": (
                "Round-trip OK — active tab received."
                if tab
                else "Round-trip OK — your browser reported no active tab."
            ),
            "code": "",
            "round_trip_ms": _ms(),
            "active_tab": dict(tab) if tab else None,
        }

    @app.post("/browser/request-host-permission")
    async def browser_request_host_permission() -> dict[str, bool]:
        """Ask the add-on to open its setup page so the user can grant site access.

        Jarvis cannot make the grant itself: ``chrome.permissions.request()`` needs
        a user gesture and cannot run in a service worker, and Jarvis is a page on
        another origin (plan §6, DEVIATION 1). So ``{"requested": true}`` means "the
        browser was asked", never "the grant was given" — the grant arrives later as
        a status change, and claiming otherwise would be a lie the card renders as
        success.
        """
        runtime = _runtime(d)
        if runtime is None:
            raise _refuse(BrowserErrorCode.BROWSER_NOT_CONNECTED, 409)
        try:
            await runtime.request_host_permission()
        except BrowserError as exc:
            raise _refuse(exc.code, 409, exc.message) from exc
        except Exception as exc:  # noqa: BLE001 — name the browser's failure, not a traceback
            raise _refuse(
                BrowserErrorCode.EXTENSION_ERROR, 409, f"{type(exc).__name__}: {exc}"
            ) from exc
        return {"requested": True}


async def _close(ws: WebSocket, code: int = 1000) -> None:
    """Close a socket without ever raising into the handler.

    A close on a socket the peer already dropped raises, and an exception out of a
    WebSocket handler is not a policy close — it is an unhandled error, because
    FastAPI's exception handlers are HTTP-only.
    """
    try:
        await ws.close(code=code)
    except Exception:
        logger.debug("browser socket was already gone at close(%s)", code, exc_info=True)
