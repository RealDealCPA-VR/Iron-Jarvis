"""The canonical browser surface, and the runtime that enforces access (plan §5.1, §5.2).

:class:`BrowserService` is the one interface the fourteen browser tools speak to.
``ExtensionBackend`` implements it today over the paired socket — user-facing name
**Your browser** — and ``ManagedBackend``, a Jarvis-owned Chromium profile with its
own cookie jar (user-facing name **Jarvis browser**), is the Phase 2 backend of
D10/D31. ``ManagedBackend`` is *not* implemented and must not be started here. The
only concession the MVP makes to it is the rule that tools call
:class:`BrowserService` methods and never reach into a backend, which is what makes
a second backend a substitution rather than a rewrite of fourteen tools.

:class:`BrowserRuntime` is the object the tools and routes hold, mirroring how
:class:`~iron_jarvis.computeruse.tools.CUContext` is the object computer-use tools
hold. Two properties of it are load-bearing:

* **Access is read LIVE, on every call.** ``PUT /settings`` mutates the *live*
  ``Config`` object (``validate_assignment=True``), so a runtime that cached
  ``browser_access`` at construction would keep answering with the value the daemon
  booted with. The user turning Browser access off and watching a tool still act on
  their bank tab is the precise failure this rereading prevents.
* **Access ``off`` is enforced on the SOCKET as well as at the gate.** Refusing
  commands is not enough on its own: ``PUT /settings`` calls :meth:`disconnect`, and
  the add-on treats the close as ordinary and reconnects about a second later, so the
  card read "Connected" seconds after the user switched the capability off.
  ``/browser/ws`` therefore refuses to make ANY socket authoritative while access is
  off (see ``daemon/routes/browser.py``), and :meth:`disconnect` can additionally send
  ``DIRECTIVE_DISCONNECT`` so a browser the user told to stop stays stopped.
* **Access failure is a refusal, never a downgrade.** ``off`` raises
  ``BROWSER_ACCESS_OFF`` and ``read_only`` raises ``READ_ONLY_MODE`` for anything
  that would change a page. Neither one silently narrows the call to something
  weaker: this repository's standing rule is that an unavailable capability refuses
  and names itself rather than substituting quietly.

Ship 1 implements ``status`` / ``list_tabs`` / ``active_tab``. Every other method
raises :class:`NotImplementedError` naming the ship that fills it in, rather than
returning an empty result — a method that answers ``{}`` reads to a model as "the
page has nothing on it", and this repository has already shipped one bug of exactly
that shape (a truncated listing read as complete, after which the model reported
that a file did not exist).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from ..core.logging import get_logger
from . import protocol as P
from .errors import BrowserError, BrowserErrorCode

logger = get_logger(__name__)

#: The three values of ``config.browser_access`` (D09), weakest first. The order IS
#: the comparison: :meth:`BrowserRuntime.require` indexes this tuple, so adding a
#: level means adding it in the right place and nothing else.
ACCESS_OFF = "off"
ACCESS_READ_ONLY = "read_only"
ACCESS_INTERACTIVE = "interactive"
ACCESS_LEVELS: tuple[str, ...] = (ACCESS_OFF, ACCESS_READ_ONLY, ACCESS_INTERACTIVE)


def min_access_for(method: str) -> str:
    """The weakest ``browser_access`` that may run ``method``.

    Derived from :data:`~iron_jarvis.browser.protocol.READ_METHODS` rather than
    from a second list written here. A method absent from the protocol's read set
    requires ``interactive``, so a method added to the protocol and forgotten here
    fails CLOSED — the fail-safe direction, matching how ``Tool.reversibility``
    and ``risk_class`` default to their strictest values.
    """
    return ACCESS_READ_ONLY if method in P.READ_METHODS else ACCESS_INTERACTIVE


@runtime_checkable
class BrowserService(Protocol):
    """One canonical browser surface. ExtensionBackend today, ManagedBackend later.

    Every method raises :class:`~iron_jarvis.browser.errors.BrowserError` carrying a
    :class:`~iron_jarvis.browser.errors.BrowserErrorCode`, and no method returns a
    bare ``None`` to mean failure. The reason is the sentinel-return lesson this
    repository has paid for more than once: ``None`` is indistinguishable from
    "nothing happened", so a caller that forgets to check reports success. The one
    ``None`` in this protocol — :meth:`active_tab` — means "there is genuinely no
    active tab", which is a fact and not a failure.

    ``type_text`` rather than ``type``: shadowing the builtin inside an
    implementation is how a stray ``type(x)`` becomes a call to the wrong thing. The
    **tool** is still named ``browser_type`` (D02).
    """

    @property
    def connected(self) -> bool: ...

    async def status(self) -> dict[str, Any]: ...

    async def list_tabs(self) -> list[dict[str, Any]]: ...

    async def active_tab(self) -> dict[str, Any] | None: ...

    async def read_page(self, tab_id: int, mode: str, **limits: Any) -> dict[str, Any]: ...

    async def get_elements(
        self, tab_id: int, query: str | None, role: str | None
    ) -> dict[str, Any]: ...

    async def screenshot(self, tab_id: int, *, full_page: bool = False) -> bytes: ...

    async def activate_tab(self, tab_id: int) -> dict[str, Any]: ...

    async def scroll(self, tab_id: int, direction: str, amount: int | None) -> dict[str, Any]: ...

    async def create_tab(self, url: str | None, *, active: bool = True) -> dict[str, Any]: ...

    async def close_tab(self, tab_id: int) -> dict[str, Any]: ...

    async def click(
        self, tab_id: int, target: dict[str, Any], snapshot_id: str | None
    ) -> dict[str, Any]: ...

    async def type_text(
        self,
        tab_id: int,
        target: dict[str, Any],
        text: str,
        *,
        clear: bool,
        press_enter: bool,
        snapshot_id: str | None,
    ) -> dict[str, Any]: ...

    async def press_key(
        self,
        tab_id: int,
        key: str,
        target: dict[str, Any] | None,
        snapshot_id: str | None,
    ) -> dict[str, Any]: ...

    async def navigate(self, tab_id: int, url: str) -> dict[str, Any]: ...


class BrowserRuntime:
    """What the browser tools and the ``/browser/*`` routes hold (plan §5.2).

    Args:
        backend: the :class:`~iron_jarvis.browser.extension_backend.ExtensionBackend`
            holding the one live socket.
        config: the LIVE :class:`~iron_jarvis.core.config.Config`. Held by
            reference, never copied: ``PUT /settings`` mutates this object in place
            and :meth:`access` must see the mutation.
        pairing: the :class:`~iron_jarvis.browser.pairing.PairingStore`. Optional
            only so a test can build a command-only runtime; the ``/browser/*``
            routes need it.
        snapshots: the Ship 2 ``SnapshotCache``. Unused here, accepted now so
            ``platform.py`` — a coordinator file — is written once.
        policy: the SAME :class:`~iron_jarvis.computeruse.policy.ComputerUsePolicy`
            instance computer use uses, so the domain allowlist and the sensitivity
            vocabulary are one configuration and not two (Ship 3).
        approvals: the SAME :class:`~iron_jarvis.computeruse.approvals.ApprovalQueue`
            type, so a browser ask renders as the approval card the user knows (Ship 3).
        artifacts: the :class:`~iron_jarvis.artifacts.store.ArtifactStore` a
            screenshot is saved through (Ship 2).
        router_resolver: ``() -> ModelRouter``, for the nested vision call (Ship 2).
        event_bus: the platform bus. The backend already holds it; kept here so a
            route can publish without reaching through the backend.

    Every argument after ``config`` is keyword-only and optional, and each is
    documented with the ship that first reads it, because ``platform.py`` belongs
    to the coordinator: a constructor that changed shape every ship would make that
    file a merge conflict five times over.
    """

    def __init__(
        self,
        *,
        backend: Any,
        config: Any,
        pairing: Any | None = None,
        snapshots: Any | None = None,
        policy: Any | None = None,
        approvals: Any | None = None,
        artifacts: Any | None = None,
        router_resolver: Any | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self.backend = backend
        self.config = config
        # The transport stamps the live access word onto ``browser.ready`` so the
        # add-on's own panel can name the mode instead of printing a placeholder. It
        # is handed a READER, not a value: the backend holds no policy, and a value
        # captured here would be the stale-setting bug ``access()`` exists to prevent.
        # Installed from this side because ``platform.py`` (coordinator-owned) builds
        # the backend before the runtime that knows how to read the setting.
        try:
            backend.access_reader = self.access
        except Exception:  # noqa: BLE001 — a stand-in backend that refuses the attribute
            logger.debug("browser backend takes no access_reader", exc_info=True)
        self.pairing = pairing
        self.snapshots = snapshots
        self.policy = policy
        self.approvals = approvals
        self.artifacts = artifacts
        self.router_resolver = router_resolver
        self.event_bus = event_bus

    # --- access -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether a paired browser is on the socket right now."""
        return bool(getattr(self.backend, "connected", False))

    def access(self) -> str:
        """``config.browser_access``, read live, normalised, failing closed to ``off``.

        ``getattr`` with an ``off`` default rather than a plain attribute read: the
        ``browser_access`` field is a coordinator edit to ``core/config.py``, and an
        older ``config.toml`` (or a ``Config`` built by an older test helper) simply
        will not have it. Fail-closed is the only safe direction — the alternative
        is a missing setting reading as ``interactive`` and a browser that acts on
        the user's logged-in tabs because a field was absent.

        An unrecognised value is also ``off``, for the same reason: a typo in a
        hand-edited ``config.toml`` must not widen access.
        """
        value = str(getattr(self.config, "browser_access", ACCESS_OFF) or ACCESS_OFF).strip()
        return value if value in ACCESS_LEVELS else ACCESS_OFF

    def require(self, min_access: str = ACCESS_READ_ONLY) -> str:
        """Assert the live access level reaches ``min_access``; return it.

        Raises:
            BrowserError: ``BROWSER_ACCESS_OFF`` when Browser access is off, and
                ``READ_ONLY_MODE`` when it is ``read_only`` and the caller needs
                ``interactive``. Two codes because the remedies differ — one asks
                the user to turn the capability on, the other tells the model that
                reading still works — and a single code would print the wrong
                sentence half the time.
        """
        current = self.access()
        if current == ACCESS_OFF:
            raise BrowserError(BrowserErrorCode.BROWSER_ACCESS_OFF)
        want = min_access if min_access in ACCESS_LEVELS else ACCESS_INTERACTIVE
        if ACCESS_LEVELS.index(current) < ACCESS_LEVELS.index(want):
            raise BrowserError(BrowserErrorCode.READ_ONLY_MODE)
        return current

    async def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Check access for ``method``, then run it on the browser.

        The access check is here and not in the backend on purpose: the backend is
        the transport and holds no policy, so every path to the browser — a tool, a
        route, a later harness — passes this one gate. A second gate written
        elsewhere would be a second policy, and one of them would be wrong.
        """
        self.require(min_access_for(method))
        return await self.backend.command(method, params, timeout_s=timeout_s)

    # --- BrowserService: implemented in Ship 1 ---------------------------

    async def status(self) -> dict[str, Any]:
        """What Iron Jarvis knows about the user's browser. Answers even when off.

        ``browser_get_status`` is the one browser tool that works at every access
        level, mirroring ``computer_use_status``: it is where a model (and the card)
        find out *why* everything else refused, so making it refuse would leave the
        failure unexplainable.

        A live round trip is attempted only when access is on and a browser is
        connected, and its failure degrades to the cached transport view rather than
        raising. A browser that has stopped answering is exactly when this call
        matters most.
        """
        access = self.access()
        view = dict(self.backend.status())
        view["access"] = access
        view["paired"] = await self._paired()
        view.setdefault("tab_count", 0)
        if access == ACCESS_OFF or not self.connected:
            return view
        try:
            live = await self.backend.command(P.METHOD_STATUS)
        except BrowserError as exc:
            view["last_error"] = exc.message
            return view
        for key in ("host_permission", "extension_id", "extension_version", "tab_count"):
            if key in live:
                view[key] = live[key]
        if live.get("active_tab") is not None:
            view["active_tab"] = live["active_tab"]
        return view

    async def list_tabs(self) -> list[dict[str, Any]]:
        """Every open tab, as rows. ``read_only`` is enough.

        Returns the rows only; the ``count`` the tool reports is derived from the
        list, so the two can never disagree. A missing ``tabs`` key answers ``[]``
        rather than raising: the add-on's contract says the key is always present,
        and turning a shape surprise into an empty list keeps the refusal path for
        real refusals.
        """
        self.require(ACCESS_READ_ONLY)
        result = await self.backend.command(P.METHOD_LIST_TABS)
        rows = result.get("tabs")
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def active_tab(self) -> dict[str, Any] | None:
        """The tab the user is looking at, or ``None`` when there genuinely is none.

        ``TAB_NOT_FOUND`` from the add-on becomes ``None`` here — a browser with no
        window open is a state, not a failure, and a tool that raised for it would
        make "what am I looking at?" an error instead of an answer. Every other code
        propagates.
        """
        self.require(ACCESS_READ_ONLY)
        try:
            result = await self.backend.command(P.METHOD_ACTIVE_TAB)
        except BrowserError as exc:
            if exc.code == BrowserErrorCode.TAB_NOT_FOUND.value:
                return None
            raise
        return dict(result) if result else None

    # --- BrowserService: later ships -------------------------------------
    #
    # Each raises NotImplementedError naming its ship. They are declared rather
    # than omitted so ``isinstance(runtime, BrowserService)`` holds from Ship 1 and
    # the protocol is one list in one place; and they raise rather than return an
    # empty result because an empty snapshot reads to a model as an empty page.

    async def read_page(self, tab_id: int, mode: str, **limits: Any) -> dict[str, Any]:
        raise NotImplementedError("read_page lands in Ship 2 (v1.236.0) with snapshot.py")

    async def get_elements(
        self, tab_id: int, query: str | None, role: str | None
    ) -> dict[str, Any]:
        raise NotImplementedError("get_elements lands in Ship 2 (v1.236.0) with snapshot.py")

    async def screenshot(self, tab_id: int, *, full_page: bool = False) -> bytes:
        raise NotImplementedError("screenshot lands in Ship 2 (v1.236.0) with the artifact path")

    async def activate_tab(self, tab_id: int) -> dict[str, Any]:
        raise NotImplementedError("activate_tab lands in Ship 3 (v1.237.0)")

    async def scroll(self, tab_id: int, direction: str, amount: int | None) -> dict[str, Any]:
        raise NotImplementedError("scroll lands in Ship 3 (v1.237.0)")

    async def create_tab(self, url: str | None, *, active: bool = True) -> dict[str, Any]:
        raise NotImplementedError("create_tab lands in Ship 3 (v1.237.0)")

    async def close_tab(self, tab_id: int) -> dict[str, Any]:
        raise NotImplementedError("close_tab lands in Ship 3 (v1.237.0)")

    async def click(
        self, tab_id: int, target: dict[str, Any], snapshot_id: str | None
    ) -> dict[str, Any]:
        raise NotImplementedError("click lands in Ship 3 (v1.237.0) with escalate_browser")

    async def type_text(
        self,
        tab_id: int,
        target: dict[str, Any],
        text: str,
        *,
        clear: bool,
        press_enter: bool,
        snapshot_id: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError("type_text lands in Ship 3 (v1.237.0) with escalate_browser")

    async def press_key(
        self,
        tab_id: int,
        key: str,
        target: dict[str, Any] | None,
        snapshot_id: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError("press_key lands in Ship 3 (v1.237.0) with escalate_browser")

    async def navigate(self, tab_id: int, url: str) -> dict[str, Any]:
        raise NotImplementedError("navigate lands in Ship 3 (v1.237.0) with the domain allowlist")

    # --- pairing and session control, for the /browser/* routes ----------

    async def pending_pairings(self) -> list[dict[str, str]]:
        """Live pending pairing asks for the card, oldest first.

        Wrapped in :func:`asyncio.to_thread` because
        :class:`~iron_jarvis.browser.pairing.PairingStore` is blocking (SQLite plus
        a hash) and this is called from the event loop. The daemon is ONE loop; a
        synchronous DB hop inside a request handler is the v1.153.1 outage, which
        the user experienced as "Daemon offline" rather than as a slow call.
        """
        if self.pairing is None:
            return []
        return await asyncio.to_thread(self.pairing.pending_rows)

    async def complete_pairing(self, request_id: str, *, label: str = "") -> None:
        """Mint the credential and deliver it on the socket that asked (D06A).

        The plaintext token exists only inside this method and the one
        ``browser.paired`` frame it hands to. It is not returned, so
        ``POST /browser/pair`` physically cannot put it in a response body: a token
        in a JSON body lands in browser devtools, in an HTTP trace, and in whatever
        the user pastes into a bug report.

        Raises:
            BrowserError: ``PAIRING_REQUIRED`` for an unknown or expired request
                (route: 404), ``AUTHENTICATION_FAILED`` when this install is already
                paired (route: 409), ``BROWSER_NOT_CONNECTED`` when the socket that
                asked has gone (route: 409 or 404 — it is no longer pairable).
        """
        if self.pairing is None:
            raise BrowserError(BrowserErrorCode.PAIRING_REQUIRED)
        conn = self.backend.restricted_socket(request_id)
        extension_id = conn.extension_id if conn is not None else ""
        token = await asyncio.to_thread(
            self.pairing.mint, request_id, extension_id=extension_id, label=label
        )
        await self.backend.deliver_pairing(request_id, token)

    async def verify_token(self, token: str) -> Any | None:
        """The live pairing a socket's ``?token=`` belongs to, or ``None``.

        The verifier for ``/browser/ws`` and *only* that route (plan §7): the
        install bearer is refused there, and this credential is refused everywhere
        else. Two distinct verifier functions is the point — one shared "is this
        token valid" helper is how a pairing token ends up authenticating
        ``/settings``.
        """
        if self.pairing is None or not token:
            return None
        return await asyncio.to_thread(self.pairing.verify, token)

    async def disconnect(self, *, suspend: bool = False) -> bool:
        """End the live socket and KEEP the credential (plan §7).

        The distinction from :meth:`forget` is the whole reason both exist:
        Disconnect is "stop for now" and the next connect authenticates silently;
        Forget destroys the credential and the next connect starts pairing again.
        Collapsing them would make one of the two buttons a lie.

        Args:
            suspend: whether to ask the add-on to STAY away. Closing the socket alone
                does not stop a browser: the add-on treats any non-1008 close as
                ordinary and reconnects about a second later, so "Disconnect" used to
                be a button that undid itself. With ``suspend`` the daemon first sends
                ``DIRECTIVE_DISCONNECT``, which the add-on answers with a PERSISTED
                suspend, and the browser stays away until the user presses Connect in
                the add-on's own panel.

                ``POST /browser/disconnect`` passes True — a person pressed a button
                that says stop, and the panel offers the inverse. The
                ``browser_access`` switch passes False (the default, and what
                ``app.py``'s ``_arm_browser`` calls): the daemon refuses to make ANY
                socket authoritative while access is off, so enforcement does not
                depend on the add-on obeying anything, and a persisted suspend there
                would leave the user's browser away after they switched access back
                on, with the only remedy hidden in Chrome's popup instead of in Jarvis.

        Returns:
            Whether there was a live socket to end.
        """
        conn = self.backend.connection
        if conn is None:
            return False
        if suspend:
            try:
                await self.backend.directive(P.DIRECTIVE_DISCONNECT)
            except BrowserError as exc:
                # Best effort by design: the add-on may be wedged or already gone,
                # and the close below is the part that must happen regardless.
                logger.debug("browser disconnect directive not delivered: %s", exc.code)
            except Exception:  # noqa: BLE001 — never fail a stop because the stop failed
                logger.debug("browser disconnect directive failed", exc_info=True)
        await self.backend.release(conn, reason="closed", detail="disconnected from Iron Jarvis")
        return True

    async def forget(self) -> int:
        """Revoke every pairing and drop the live socket; returns credentials killed.

        Order matters: revoke first, then close. Closing first would leave a window
        in which the add-on reconnects with a credential that is still valid, and
        the user who just pressed Forget would watch it come back.
        """
        killed = 0
        if self.pairing is not None:
            killed = await asyncio.to_thread(self.pairing.revoke_all)
        conn = self.backend.connection
        if conn is not None:
            await self.backend.release(conn, reason="revoked", detail="pairing forgotten")
        return killed

    async def request_host_permission(self) -> dict[str, Any]:
        """Ask the add-on to open its setup page so the user can grant site access.

        The plan §6 deviation, in one sentence: ``chrome.permissions.request()``
        needs a user gesture and cannot run in a service worker, so the daemon sends
        a directive, the worker opens the bundled ``setup.html``, and one button
        there makes the call. Jarvis cannot make it — Jarvis is a page on another
        origin.

        Raises:
            BrowserError: ``BROWSER_NOT_CONNECTED`` when no browser is on the socket.
        """
        self.require(ACCESS_READ_ONLY)
        return await self.backend.directive(P.DIRECTIVE_REQUEST_HOST_PERMISSIONS)

    async def _paired(self) -> bool:
        if self.pairing is None:
            return False
        try:
            return await asyncio.to_thread(self.pairing.paired)
        except Exception:  # noqa: BLE001 — status must never fail (plan §3.1)
            logger.debug("pairing lookup failed during status", exc_info=True)
            return False


__all__ = [
    "ACCESS_INTERACTIVE",
    "ACCESS_LEVELS",
    "ACCESS_OFF",
    "ACCESS_READ_ONLY",
    "BrowserRuntime",
    "BrowserService",
    "min_access_for",
]
