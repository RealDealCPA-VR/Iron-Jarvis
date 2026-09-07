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

Ship 1 implemented ``status`` / ``list_tabs`` / ``active_tab``; Ship 2 added the READ
tier — ``read_page``, ``get_elements`` and ``screenshot``; Ship 3 fills in the eight
ACTING methods, and with them the rule that makes this the highest-risk ship of the
five: every one of them moves the user's real, logged-in browser, so a wrong click
is not a failed test but an action taken in someone's account.

Three properties of the acting tier are stated here because each is the kind of
thing a later reader "simplifies" away:

* **This module decides no RISK.** :mod:`iron_jarvis.browser.risk` is the single
  door, consulted by the TOOL before it reaches any method here. A service method
  that escalated as well would be a second policy site, and a second site is a
  rule one of the two will eventually skip — silently, because a skipped
  escalation looks exactly like a call that was not sensitive.
* **A resolution is made ONCE and carried** (:class:`ActionTarget`). The risk
  decision, the frame and the result all describe the same element because they
  are all handed the same object; resolving separately would let the card the user
  approved and the frame that was sent name different buttons.
* **A page that moved under an action drops its snapshot** (:meth:`BrowserRuntime._settle`).
  A stale registry is how an element id comes to mean a different element than the
  model saw.

Two properties of the read tier are worth naming here, because both are easy to
"simplify" away:

* **The target tab is resolved, and refused, BEFORE the command is sent.**
  :meth:`BrowserRuntime.resolve_page_tab` is one call for one question ("which page
  am I about to read?"), and §9.7's unsupported-page check lives inside it. That
  check cannot happen in the page at all — the content script is never injected
  into a ``chrome://`` tab — so a daemon that sent the command anyway would return
  an injection failure naming nothing the model can correct.
* **A snapshot is re-bounded on arrival and then CACHED.** The cache is
  authoritative for exactly one thing, which ``snapshot_id`` was most recent for a
  tab (§9.3), and :meth:`BrowserRuntime.invalidate_snapshot` drops it when the
  add-on reports a navigation, so a stale id fails as ``STALE_SNAPSHOT`` with a
  remedy instead of naming a page that no longer exists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.logging import get_logger
from . import protocol as P
from .errors import BrowserError, BrowserErrorCode
from .screenshot import decode_capture
from .snapshot import PageSnapshot, SnapshotLimits, normalise_mode

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


def _as_tab_id(value: Any) -> int | None:
    """``value`` as a tab id, or ``None`` when it is not one.

    Strict about ``bool`` (``True`` is an ``int`` in Python and would resolve to
    tab 1) and about a float that is not whole, and accepting of the digit STRING
    a JSON-ish model sends — the registry's schema check passes ``{"tab_id": "42"}``
    only when the model declared it a string, and refusing to read an id we can
    read unambiguously would be pedantry the user pays for. Everything else is
    ``None``, which the callers turn into ``TAB_NOT_FOUND`` naming the value.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def _role_name_match(
    snapshot: PageSnapshot, target: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The snapshot row a ``{"role", "name"}`` target names, or ``None``.

    **This is the other half of the sensitive-field gate.** Before it existed,
    only ``{"element_id"}`` resolved to a row, so a password typed through
    ``{"role": "textbox", "name": "Password"}`` — the second of the three forms
    the tool's own schema advertises — reached
    :func:`~iron_jarvis.browser.risk.browser_risk_decision` with
    ``target_element=None``. Both arms that catch a credential field then went
    dark: ``classify``'s ``type``/``autocomplete`` reading (which needs a
    :class:`~iron_jarvis.computeruse.base.Page` row) and the D13B ``sensitive``
    override. The call was allowed with no card, on the user's real bank login,
    while the identical call by ``element_id`` correctly asked. The daemon was
    holding the row the whole time.

    Matching is the content script's own rule (``resolveTarget`` in
    ``content/actions.ts``): role and name compared case-folded and whole, never
    by substring. Two readers of one rule, so the row this resolves is the row the
    page will act on.

    Advisory, and deliberately so: a miss returns ``None`` rather than raising.
    The live page may hold a control this snapshot never carried — ``summary``
    mode carries no registry at all, and a cap may have dropped the row — and
    refusing daemon-side would break a call the page can serve. A miss costs
    nothing: :meth:`BrowserRuntime.prepare_action` then falls back to
    :func:`~iron_jarvis.browser.protocol.target_label`, so the words the model
    named are still scanned by the escalation vocabulary.

    An AMBIGUOUS name (two rows with the same role and name) takes the STRICTEST
    candidate — a ``sensitive`` row over a plain one. The page refuses ambiguity
    outright, so this choice never selects what gets clicked; it only decides what
    the risk door is shown, and there the safest reading of "we are not sure which
    of these it is" is the dangerous one.
    """
    row = dict(target or {})
    role = str(row.get("role") or "").strip().casefold()
    name = str(row.get("name") or "").strip().casefold()
    if not role or not name:
        return None
    matches = [
        dict(candidate)
        for candidate in snapshot.elements
        if str(candidate.get("role") or "").strip().casefold() == role
        and str(candidate.get("name") or "").strip().casefold() == name
    ]
    if not matches:
        return None
    matches.sort(key=lambda candidate: not bool(candidate.get("sensitive")))
    return matches[0]


@dataclass(frozen=True)
class ActionTarget:
    """What one acting call resolved to, decided ONCE and carried to every step.

    Ship 3's acting path has three consumers of the same resolution and they must
    not resolve separately:

    * the RISK decision (:func:`~iron_jarvis.browser.risk.browser_risk_decision`)
      needs the target's accessible NAME and its snapshot row, because that is
      what the escalation vocabulary and the sensitive-field arms read;
    * the FRAME builder needs the normalised target, the tab id and the snapshot
      id;
    * the RESULT needs the tab's title and URL, which D24 requires in the ledger
      row so a browser action is reconstructible months later.

    Resolving three times would spend three round trips, and — the reason this is
    a value object rather than a convenience — the three answers could DIFFER. A
    page mutates between calls; a risk decision made against the element the
    first resolution found, then sent as a frame naming the element the second
    one found, is an approval card the user answered about a different button.
    One resolution, passed along, makes that impossible to express.

    Frozen, and ``target``/``element`` are copies taken at construction: a caller
    that mutated the target after the risk decision was made would be editing the
    thing the user approved.
    """

    #: The resolved tab row (id, title, url, status), from :meth:`BrowserRuntime.resolve_page_tab`.
    tab: dict[str, Any] = field(default_factory=dict)
    #: The tab id actually used. Echoed in every result (section 8.6).
    tab_id: int | None = None
    #: The snapshot this action is judged against, resolved to the tab's newest
    #: when the caller named none. ``""`` for the methods that address no element.
    snapshot_id: str = ""
    #: The normalised, exactly-one-form target, or ``None`` for a method that
    #: addresses no element.
    target: dict[str, Any] | None = None
    #: The snapshot's element row for that target, when one was resolved. Its
    #: ``type``/``autocomplete``/``sensitive`` are what catch a password or card
    #: field whose visible name says neither.
    element: dict[str, Any] | None = None
    #: The RESOLVED accessible name — the words the user would read on the
    #: control. The one field the escalation vocabulary is scanned against, which
    #: is why an ``element_id`` target is preferred over a css one: a selector
    #: resolves to a node and names nothing a classifier can read.
    label: str = ""

    @property
    def page_url(self) -> str:
        """The acted-on page's URL, for the classifier's ``Page`` and for D24."""
        return str(self.tab.get("url") or "")

    @property
    def page_title(self) -> str:
        """The acted-on page's title. Untrusted page text — fence before printing."""
        return str(self.tab.get("title") or "")

    def target_ref(self, echoed: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """``{element_id, role, name}`` — what was ACTUALLY acted on (section 10.4).

        The PAGE's echo wins over what the model asked for, because they differ
        precisely when a role+name or css target matched something other than what
        the model pictured — and that difference is the only evidence a later
        reader of the ledger has. Falling back to the request would erase it.
        """
        row = dict(echoed or {})
        if row.get("element_id") or row.get("role") or row.get("name"):
            return {
                "element_id": str(row.get("element_id") or ""),
                "role": str(row.get("role") or ""),
                "name": str(row.get("name") or ""),
            }
        asked = dict(self.target or {})
        return {
            "element_id": str(asked.get("element_id") or ""),
            "role": str(asked.get("role") or ""),
            "name": str(asked.get("name") or self.label or ""),
        }


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

    # The three read-tier methods take ``tab_id=None`` — plan §5.1 sketches them
    # with a required ``int`` while §8.6 makes ``tab_id`` "optional everywhere,
    # omitted means the active tab". The sections disagree; this follows §8.6,
    # because that is the one a model reads and the one the tool schemas publish,
    # and a protocol that promises a narrower call than the implementation accepts
    # is a protocol a second backend would be written against wrongly.
    async def read_page(
        self, tab_id: int | None = None, mode: str = P.DEFAULT_SNAPSHOT_MODE, **limits: Any
    ) -> dict[str, Any]: ...

    async def get_elements(
        self,
        tab_id: int | None = None,
        query: str | None = None,
        role: str | None = None,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]: ...

    async def screenshot(self, tab_id: int | None = None, *, full_page: bool = False) -> bytes: ...

    # THE ACTING TIER IS NOT ON THIS SURFACE, AND THAT IS THE PROTECTION.
    #
    # Plan §5.1 sketches eight acting methods here, and six of them were written:
    # ``click``, ``type_text``, ``press_key``, ``navigate``, ``create_tab`` and
    # ``close_tab``, each reaching the socket with NO risk decision, and each
    # without a single caller anywhere in ``src/`` or the routes. That is a second
    # door onto the user's real, logged-in browser, standing open because nothing
    # walks through it YET — and the module comment above ``prepare_action``
    # promises that ``browser/risk.py`` is the single door, which was true only
    # for as long as every caller happened to be a tool. Ship 4 adds an outward
    # MCP harness; the next author wiring a route or a handler to
    # ``runtime.click(...)`` would have got a click on the user's bank with no
    # Decision, no card and a ledger row that never mentioned risk, and every test
    # in this ship would have stayed green.
    #
    # They are gone. The acting path is ``_ActingTool.execute``
    # (``browser/tools.py``), which is final, calls
    # :func:`~iron_jarvis.browser.risk.browser_risk_decision` exactly once, routes
    # a ``requires_approval`` verdict to the approval queue, and only then sends
    # the frame through :meth:`BrowserRuntime.command`. Routing these six through
    # the risk door INSTEAD would have been the other available fix and is worse:
    # it makes a second policy site, and two policy sites are how one of them
    # comes to be skipped.
    #
    # ``activate_tab`` and ``scroll`` stay. They commit nothing — their risk
    # verdict is "allowed by policy" at rule 1 of §8.2 unconditionally, whichever
    # caller makes it — so there is no gate for a second caller to get around, and
    # a route that brings a tab to the front needs no tool. Every method that can
    # change a page, open one, or destroy one is reached through the tool layer or
    # not at all.

    async def activate_tab(self, tab_id: int) -> dict[str, Any]: ...

    async def scroll(
        self,
        tab_id: Any = None,
        direction: str = P.SCROLL_DOWN,
        amount: int | None = None,
    ) -> dict[str, Any]: ...


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
        approval_resolver: Any | None = None,
    ) -> None:
        #: THE USER'S OWN REQUEST TEXT FOR THIS TURN, or ``""`` (Q03, plan §9.5).
        #:
        #: Set per turn by whichever lane is driving, and read through exactly one
        #: helper, :func:`~iron_jarvis.browser.risk.request_text_of`. It answers
        #: Q03 point 5: after a page has tripped the injection detector, a
        #: state-changing call on that tab requires approval unless the target's
        #: accessible name appears in what the USER asked for — the difference
        #: between "click Sign in, as I asked" and "click the button the page told
        #: you to click".
        #:
        #: Empty FAILS CLOSED (every state-changing call on a flagged tab asks),
        #: which is the safe direction but a noisy one.
        #:
        #: NOTHING SETS IT AS OF v1.237.0, AND THAT IS A DECISION, not an oversight.
        #: The obvious wiring — each chat lane assigning the user's message here at
        #: the top of a turn — is a RACE, because the runtime is one object built at
        #: boot and shared by every conversation. Two turns in flight and one
        #: conversation's words decide whether the OTHER conversation's click is
        #: allowed. The failure is silent and it fails OPEN: turn B says "click Sign
        #: in", turn A's flagged page offers a "Sign in" button, and A's click stops
        #: asking. Trading an extra approval card for that is not a trade worth
        #: making, and a noisy prompt on a page that already tripped the injection
        #: detector is close to free — flagged pages are rare, and a card on one is
        #: arguably the right outcome anyway.
        #:
        #: The fix, when it is worth it, is a per-turn carrier (the request text on
        #: ``ToolContext``, which is already built per call) rather than a mutable
        #: field on a process-wide singleton. Recorded in ``docs/TODO.md``. Until
        #: then this stays "" and ``request_text_of`` reads it through one helper, so
        #: there is exactly one place to change.
        self.request_text: str = ""
        #: Optional synchronous approval decider, mirroring
        #: ``CUContext.approval_resolver``. Absent (the production default), a
        #: browser call that needs approval returns PENDING and the user answers
        #: the card they already know; the model's next identical call consumes
        #: that approval. Present, it decides in-process — which is what lets a
        #: test drive the approved AND the refused branch without a dashboard.
        self.approval_resolver = approval_resolver
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
        # Same arrangement for the Ship 2 snapshot cache: the TRANSPORT learns that a
        # page navigated (that event only reaches the socket) and the RUNTIME holds
        # the cache, so the transport is handed one callable and knows nothing about
        # snapshots. Without it the cache would keep handing out a snapshot_id for a
        # page that has already been replaced — which fails safe as STALE_SNAPSHOT on
        # the next action, but holds the old page's text for as long as the tab lives.
        try:
            backend.snapshot_invalidator = self.invalidate_snapshot
        except Exception:  # noqa: BLE001
            logger.debug("browser backend takes no snapshot_invalidator", exc_info=True)
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

    # --- BrowserService: the READ tier (Ship 2) --------------------------

    async def resolve_page_tab(self, tab_id: Any = None) -> dict[str, Any]:
        """The tab row a page-reading call will run against, or raise (§8.6, §9.7).

        Two jobs in one call, because they answer one question — *which page am I
        about to read?*

        * **Resolve ``tab_id``.** ``None`` means the tab the user is looking at, so
          a model never needs two calls to read what is in front of them (§8.6),
          and the id it resolved to is echoed in every result.
        * **Refuse a page Chrome closes to add-ons, BEFORE the command is sent.**
          §9.7 says this check is daemon-side and it has to be: the content script
          is never injected into a ``chrome://`` tab, so the failure would
          otherwise arrive as a bare injection error naming nothing the model can
          correct — and ``UNSUPPORTED_PAGE``'s remedy ("ask the user to switch to a
          normal tab") is the whole value of the code.

        A row reporting ``supported: false`` is refused even when its URL reads as
        ordinary, because the add-on is the authority on what it can reach; the
        message then says "These pages" rather than inventing a scheme the URL does
        not have.

        Raises:
            BrowserError: ``TAB_NOT_FOUND`` for an id that is not open (and for a
                non-integer id, which is the same fact about the same argument),
                ``UNSUPPORTED_PAGE`` for a page closed to add-ons, plus whatever
                the metadata call itself raised.
        """
        self.require(ACCESS_READ_ONLY)
        wanted = _as_tab_id(tab_id)
        if tab_id is not None and wanted is None:
            raise BrowserError(BrowserErrorCode.TAB_NOT_FOUND, tab_id=tab_id)
        if wanted is None:
            row = await self.active_tab()
            if row is None:
                raise BrowserError(BrowserErrorCode.TAB_NOT_FOUND, tab_id="active")
        else:
            rows = await self.list_tabs()
            row = next((r for r in rows if _as_tab_id(r.get("id")) == wanted), None)
            if row is None:
                raise BrowserError(BrowserErrorCode.TAB_NOT_FOUND, tab_id=wanted)
        scheme = P.unsupported_page_scheme(str(row.get("url") or ""))
        if not scheme and row.get("supported") is False:
            scheme = "These"
        if scheme:
            raise BrowserError(BrowserErrorCode.UNSUPPORTED_PAGE, scheme=scheme)
        return dict(row)

    async def read_page_snapshot(
        self,
        tab_id: Any = None,
        mode: Any = P.DEFAULT_SNAPSHOT_MODE,
        **limits: Any,
    ) -> PageSnapshot:
        """Read one page, re-bound it, cache it, and return the SNAPSHOT OBJECT.

        The object rather than the dict, because the tool layer needs the model:
        :meth:`~iron_jarvis.browser.snapshot.PageSnapshot.truncation_text`,
        :meth:`~iron_jarvis.browser.snapshot.PageSnapshot.mode_note` and the Q03
        warning are formatted by the snapshot module so the wording is identical
        everywhere a page is rendered. Rebuilding a snapshot from
        :meth:`~iron_jarvis.browser.snapshot.PageSnapshot.to_dict` would re-run the
        cap checks against rows that are already trimmed and silently lose every
        truncation line — the exact "a short page reads as a whole one" failure the
        re-enforcement exists to prevent.

        The effective limits are resolved HERE and sent on the wire, so the content
        script trims to numbers the daemon knows: a page trimmed to a cap the daemon
        never heard of is a page whose truncation nobody can report. A caller may
        only narrow them (``SnapshotLimits.for_mode``).

        Raises:
            UnknownSnapshotMode: for a mode outside the three. Deliberately not a
                ``BROWSER_*`` code: nothing about the browser failed, and the tool
                layer turns it into the registry's own "needs one of: ..." wording.
            BrowserError: every browser-side failure, including
                ``UNSUPPORTED_PAGE`` from :meth:`resolve_page_tab`.
        """
        resolved_mode = normalise_mode(mode)
        row = await self.resolve_page_tab(tab_id)
        caps = SnapshotLimits.for_mode(
            resolved_mode,
            max_chars=limits.get("max_chars"),
            max_elements=limits.get("max_elements"),
        )
        params = P.read_page_params(_as_tab_id(row.get("id")), resolved_mode, caps.to_params())
        result = await self.command(P.METHOD_READ_PAGE, params)
        snapshot = PageSnapshot.from_result(
            result,
            tab_id=_as_tab_id(row.get("id")),
            mode=resolved_mode,
            limits=caps,
        )
        return self._cache_snapshot(snapshot)

    async def read_page(
        self,
        tab_id: Any = None,
        mode: Any = P.DEFAULT_SNAPSHOT_MODE,
        **limits: Any,
    ) -> dict[str, Any]:
        """The ``browser_read_page`` result payload — §9.1's snapshot, as a dict."""
        snapshot = await self.read_page_snapshot(tab_id, mode, **limits)
        return snapshot.to_dict()

    async def get_elements(
        self,
        tab_id: Any = None,
        query: str | None = None,
        role: str | None = None,
        *,
        limit: Any = None,
    ) -> dict[str, Any]:
        """A filtered slice of one fresh snapshot's element registry (§8.6).

        The reply is turned into a :class:`~iron_jarvis.browser.snapshot.PageSnapshot`
        before anything is returned, and that is not ceremony: it puts these element
        rows through EXACTLY the path ``read_page``'s take — the D13B whitelist (a
        row arriving with a typed ``value`` has the key dropped, never copied), the
        accessible-name cap, and the Q03 scan over the names. A second, looser path
        here would be a second place a scrubbing miss in the page could become a
        value in the ledger, and it would be the quieter of the two.

        **The cache is only written for a COMPLETE registry** — no ``query``, no
        ``role``, nothing dropped by a limit. A partial roster cached as the tab's
        newest snapshot would make
        :func:`~iron_jarvis.browser.snapshot.element_problem` answer
        ``ELEMENT_NOT_FOUND`` for an element that is genuinely on the page: a
        confident lie. Not caching costs a ``STALE_SNAPSHOT`` whose remedy is "call
        browser_read_page", which is recoverable and true.

        The result adds ``tab_id``, ``url`` and ``title`` to §8.6's five keys, per
        that section's own shared convention ("every result carries ``tab_id``,
        ``url``, ``title`` and ``page_version``"), plus ``security`` and
        ``complete``. Those are daemon-side facts ABOUT a reply, so they live on the
        tool result and not in ``ElementsResult``, which is the wire shape the
        add-on fills in.
        """
        row = await self.resolve_page_tab(tab_id)
        params = P.get_elements_params(
            _as_tab_id(row.get("id")), query=query, role=role, limit=limit
        )
        result = await self.command(P.METHOD_GET_ELEMENTS, params)
        payload = dict(result)
        payload.setdefault("tab_id", _as_tab_id(row.get("id")))
        payload.setdefault("title", row.get("title") or "")
        payload.setdefault("url", row.get("url") or "")
        # An elements reply is a snapshot with no page text: interactive is the mode
        # that carries a registry, and the text cap is irrelevant to rows.
        payload["mode"] = P.MODE_INTERACTIVE
        payload["text"] = ""
        snapshot = PageSnapshot.from_result(
            payload, tab_id=_as_tab_id(row.get("id")), mode=P.MODE_INTERACTIVE
        )
        narrowed = bool(params.get("query") or params.get("role"))
        # Three independent signals that this list is not the whole registry, ORed:
        # the add-on's own flag, a cap the daemon re-enforced on arrival, and the
        # add-on's own COUNT exceeding the rows it actually sent. The third exists
        # because the first is the one an add-on version can forget — and a short
        # list presented as complete is how a model comes to tell the user a button
        # is not on the page.
        reported = result.get("count")
        undercount = isinstance(reported, int) and reported > len(snapshot.elements)
        truncated = bool(result.get("truncated")) or snapshot.truncated or undercount
        complete = not narrowed and not truncated
        if complete:
            self._cache_snapshot(snapshot)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "page_version": snapshot.page_version,
            "tab_id": snapshot.tab_id,
            "url": snapshot.url,
            "title": snapshot.title,
            "elements": [dict(element) for element in snapshot.elements],
            "count": len(snapshot.elements),
            "truncated": truncated,
            "complete": complete,
            "security": dict(snapshot.security) if snapshot.security else None,
        }

    async def screenshot_capture(
        self, tab_id: Any = None, *, full_page: bool = False
    ) -> dict[str, Any]:
        """The raw ``screenshot`` reply plus the tab context the artifact needs.

        Returns the add-on's own payload (``tab_id``, ``media_type``, ``data_b64``)
        with ``tab_url`` and ``tab_title`` added, because
        :func:`~iron_jarvis.browser.screenshot.capture_for_tool` names the artifact
        ``browser/<tab-host>/<label>-<n>`` (§10.1) and the host is not in the reply.
        Read here, where the tab has just been resolved, rather than by a second
        round trip from the tool.

        The bytes are NOT decoded here: one decoder
        (:func:`~iron_jarvis.browser.screenshot.decode_capture`) runs inside the
        single ``to_thread`` hop that also writes the file, so that CPU work stays
        on the far side of the loop.
        """
        row = await self.resolve_page_tab(tab_id)
        params = P.screenshot_params(_as_tab_id(row.get("id")), full_page=bool(full_page))
        result = await self.command(P.METHOD_SCREENSHOT, params)
        payload = dict(result)
        payload.setdefault("tab_id", _as_tab_id(row.get("id")))
        payload["tab_url"] = str(row.get("url") or "")
        payload["tab_title"] = str(row.get("title") or "")
        # KEEP THE ADD-ON'S ANSWER. ``full_page`` here is what was CAPTURED, and
        # the add-on always answers false because ``captureVisibleTab`` photographs
        # the viewport; reaching past the fold means scrolling the user's page,
        # which is an action and belongs to the acting tools. This line used to
        # assign ``bool(full_page)`` — the REQUEST — so the add-on's honest flag
        # was overwritten with the model's wish and the tool reported a whole page
        # it had never received. The request is kept beside it, under its own name,
        # so the tool can say that the two disagree.
        payload["full_page"] = bool(result.get("full_page"))
        payload["full_page_requested"] = bool(full_page)
        return payload

    async def screenshot(self, tab_id: Any = None, *, full_page: bool = False) -> bytes:
        """The capture as image bytes, per :class:`BrowserService`.

        The bytes, not the artifact: persisting and describing a capture is
        :mod:`iron_jarvis.browser.screenshot`'s job, and ``browser_screenshot``
        calls that module directly. This method exists because the service protocol
        declares it, and because a later caller (a route, a harness) may want the
        image without an artifact.

        Raises:
            BrowserError: ``EXTENSION_ERROR`` when the reply carries no usable image.
        """
        capture = await self.screenshot_capture(tab_id, full_page=full_page)
        png, _media_type, _tab = decode_capture(capture)
        return png

    # --- the snapshot cache ----------------------------------------------

    def _cache_snapshot(self, snapshot: PageSnapshot) -> PageSnapshot:
        """Store ``snapshot`` as the tab's newest, and return it.

        A cache that refuses the write must not fail the read: the page has already
        been read, and the only consequence of a missed write is that a later action
        answers ``STALE_SNAPSHOT`` — recoverable, with the right remedy. Losing the
        page the user asked for would not be.
        """
        cache = self.snapshots
        if cache is None:
            return snapshot
        try:
            return cache.put(snapshot)
        except Exception:  # noqa: BLE001 — a cache miss must not lose a read page
            logger.debug("browser snapshot not cached", exc_info=True)
            return snapshot

    def invalidate_snapshot(self, tab_id: Any = None) -> None:
        """Forget the cached snapshot for ``tab_id``; ``None`` forgets every tab.

        Installed on the backend as ``snapshot_invalidator`` (see :meth:`__init__`)
        and called when the add-on reports a NAVIGATION on a tab, and when the
        socket goes away. The cache is authoritative for one thing only — which
        ``snapshot_id`` was most recent for a tab (§9.3) — and after a navigation
        that id names a page that no longer exists. Dropping it turns the next
        action into ``STALE_SNAPSHOT`` with the remedy "call browser_read_page",
        instead of an id the page will refuse for a reason the daemon cannot
        explain. It also stops the daemon holding a dead page's text, and the
        password-field metadata around it, for longer than the page lived.

        Synchronous and cheap — dictionary work, no filesystem, no DB — so it is
        safe to call from the socket handler with nothing to offload, and it never
        raises into it.
        """
        cache = self.snapshots
        if cache is None:
            return
        try:
            if tab_id is None:
                cache.clear()
            else:
                cache.forget(tab_id)
        except Exception:  # noqa: BLE001 — never break a socket over a cache
            logger.debug("browser snapshot cache not invalidated", exc_info=True)

    # --- BrowserService: the ACTING tier (Ship 3) ------------------------
    #
    # The RESOLUTION every acting call needs (:meth:`prepare_action`,
    # :meth:`_settle`) plus the two methods that commit nothing
    # (:meth:`activate_tab`, :meth:`scroll`). The six that change a page, open a
    # tab or destroy one are NOT here: they are sent by ``_ActingTool.execute``,
    # the one path that consults the risk door first. See the comment on
    # :class:`BrowserService` for why they were removed rather than gated here.
    #
    # What remains obeys the same four rules the eight did. They are stated once,
    # here, because the bodies below are deliberately short and a rule restated in
    # each of them is a rule that will be spelled differently in one:
    #
    # 1. **``interactive`` is asserted BEFORE any command is sent.** ``command``
    #    would refuse an acting method on its own (``min_access_for``), but only
    #    after :meth:`resolve_page_tab` had already spent a ``list_tabs`` round
    #    trip on a call that was never going to run. Asserting first also means
    #    the READ_ONLY_MODE refusal names the acting tool the model called rather
    #    than arriving out of a metadata read it never asked for.
    # 2. **The tab is resolved daemon-side**, so ``tab_id`` is optional
    #    everywhere section 8.6 says it is, the id actually used is echoed in the
    #    result, and a page Chrome closes to add-ons is refused UNSUPPORTED_PAGE
    #    before the frame exists (section 9.7).
    # 3. **Element-addressed calls pass the snapshot gate**
    #    (:meth:`~iron_jarvis.browser.snapshot.SnapshotCache.check`) and then SEND
    #    the snapshot id they were judged against, so the page can make the
    #    ``page_version`` comparison only it can make (section 9.3). Two halves of
    #    one check, each answered where the evidence is: the daemon knows which
    #    snapshot it handed out, the page knows whether the node is still there.
    # 4. **A result that says the page moved invalidates the cache**
    #    (:meth:`_settle`). A stale registry is how an element id comes to mean a
    #    different element than the model saw, and the cost of an unnecessary
    #    re-read is one cheap call while the cost of a kept one is a click on the
    #    wrong control.
    #
    # None of them decides RISK. That is :mod:`iron_jarvis.browser.risk`'s single
    # door, called by the tool layer before it reaches any of these — a service
    # method that escalated as well would be a second policy site, and one of the
    # two would eventually be skipped.

    async def prepare_action(
        self,
        tab_id: Any = None,
        *,
        target: Mapping[str, Any] | None = None,
        snapshot_id: Any = None,
        need_snapshot: bool = True,
    ) -> ActionTarget:
        """Everything an acting tool must know BEFORE it decides risk (8.2, 9.3).

        The tool layer cannot ask
        :func:`~iron_jarvis.browser.risk.browser_risk_decision` anything useful
        until the target has a NAME: the escalation vocabulary is scanned against
        the resolved element's accessible name, and an ``element_id`` names
        nothing until a snapshot resolves it. So this method does the resolution,
        in the order the failures should be reported, and hands back one object.

        BOTH resolvable forms are resolved, not only the preferred one. An
        ``element_id`` is looked up in the roster; a ``{"role", "name"}`` target is
        matched against the same rows by :func:`_role_name_match`. That second
        lookup is the sensitive-field gate for two of the three documented forms:
        without it a password typed by role and name reached the risk door with no
        element, and every arm that reads ``type``/``autocomplete``/``sensitive``
        went dark while the identical call by ``element_id`` asked. A ``css``
        target resolves to nothing here — the daemon cannot run a selector — and
        the risk door fails closed on a target it could not read.

        Order is load-bearing:

        1. access — an acting call at ``read_only`` is refused here, before a
           metadata read is spent on it;
        2. the tab — ``TAB_NOT_FOUND`` / ``UNSUPPORTED_PAGE``, daemon-side;
        3. the target's SHAPE — :func:`~iron_jarvis.browser.protocol.normalise_target`
           refuses two forms with a message naming the conflict, and it runs
           before the snapshot gate so "you sent two targets" is not reported as
           "your snapshot is stale";
        4. the snapshot gate — ``STALE_SNAPSHOT`` for a tab nobody has read (or a
           snapshot id this tab did not hand out) and ``ELEMENT_NOT_FOUND`` for an
           id absent from the roster we hold.

        ``need_snapshot`` is False for the four LOCAL_UI methods and for
        ``navigate``: none of them addresses an element, and requiring a snapshot
        to open a URL would make "go to example.com" impossible without first
        reading a page the user never asked about. It is True for click, type and
        press_key even when the target is absent, which is section 8.6's rule
        taken literally — acting on a page nobody has read is acting blind, and
        the refusal carries a one-call remedy.

        Raises:
            BrowserError: every failure above, each with its D15 remedy.
        """
        self.require(ACCESS_INTERACTIVE)
        row = await self.resolve_page_tab(tab_id)
        resolved_tab = _as_tab_id(row.get("id"))
        clean: dict[str, Any] | None = None
        element_id = ""
        if target is not None:
            clean = dict(P.normalise_target(target))
            element_id = str(clean.get("element_id") or "")
        wanted = str(snapshot_id or "").strip()
        used = ""
        element: dict[str, Any] | None = None
        if need_snapshot:
            cache = self.snapshots
            if cache is None:
                # No cache at all is not "no snapshot needed": it is a runtime
                # built without the object that makes an element id mean
                # something. Refusing with the one code whose remedy is "call
                # browser_read_page" keeps a half-wired install honest, rather
                # than letting it act on ids nothing can validate.
                raise BrowserError(BrowserErrorCode.STALE_SNAPSHOT)
            used = cache.check(resolved_tab, wanted or None, None, element_id or None)
            cached = cache.get(resolved_tab)
            if cached is not None:
                element = (
                    cached.element(element_id)
                    if element_id
                    else _role_name_match(cached, clean)
                )
        elif wanted:
            used = wanted
        # The LABEL the escalation vocabulary is scanned against. The resolved
        # accessible name when we have one; otherwise whatever the model named,
        # which is ``P.target_label``'s pre-resolution fallback and is empty for
        # an element_id or a css target. Never the selector text: a css string is
        # page-authored and names nothing a user would read off a button.
        label = str((element or {}).get("name") or (element or {}).get("text") or "")
        if not label and clean is not None:
            label = P.target_label(clean)
        return ActionTarget(
            tab=dict(row),
            tab_id=resolved_tab,
            snapshot_id=used,
            target=clean,
            element=element,
            label=label,
        )

    def _settle(self, plan: ActionTarget, result: Mapping[str, Any]) -> dict[str, Any]:
        """Fold the tab context into an acting result, and drop a moved page's snapshot.

        Two jobs, both about one fact — *is the snapshot the model holds still
        true?*

        * **Report the tab.** Section 8.6's shared convention: every result
          carries ``tab_id``, ``url``, ``title`` and ``page_version``. The add-on
          fills what it knows; the daemon fills the rest from the tab it
          resolved, so a result never omits the id it acted on.
        * **Invalidate on movement.** ``navigated`` true, or a ``page_version``
          past the one the cached snapshot was taken at, both mean the registry
          the model is holding no longer describes the page. Dropping the cache
          entry turns the NEXT call into ``STALE_SNAPSHOT`` — whose remedy is one
          call — instead of an element id that silently resolves to whatever now
          occupies that slot. That is the failure this ship must not have: a
          click landing on a different control than the model saw.

        A MISSING ``page_version`` does not invalidate. The add-on omits it on
        results where the page did not move (``activate_tab``), and treating
        absence as movement would throw away every snapshot on every harmless
        call — which reads as working caution and is actually a cache that never
        holds anything.
        """
        payload = dict(result)
        payload.setdefault("tab_id", plan.tab_id)
        payload.setdefault("url", plan.tab.get("url") or "")
        payload.setdefault("title", plan.tab.get("title") or "")
        moved = bool(payload.get("navigated"))
        version = payload.get("page_version")
        if not moved and version is not None and self.snapshots is not None:
            try:
                cached = self.snapshots.get(plan.tab_id)
            except Exception:  # noqa: BLE001 — a cache read must not fail an action
                cached = None
            moved = cached is not None and cached.is_stale_for(version)
        # A plan with no tab id is ``create_tab``, which resolved no existing tab.
        # ``invalidate_snapshot(None)`` means "forget EVERY tab" — the right
        # meaning when a browser disconnects and a catastrophic one here, where it
        # would throw away every other tab's snapshot because one new tab opened.
        if moved and plan.tab_id is not None:
            self.invalidate_snapshot(plan.tab_id)
            payload["snapshot_invalidated"] = True
            return payload
        payload["snapshot_invalidated"] = False
        return payload

    async def activate_tab(self, tab_id: int) -> dict[str, Any]:
        """Bring one tab to the front. LOCAL_UI: the user's view moves, no page does.

        ``need_snapshot=False`` — nothing inside the page is addressed — and the
        tab is resolved through the same daemon-side path as every other acting
        call, so activating a closed tab is ``TAB_NOT_FOUND`` with the remedy
        naming ``browser_list_tabs`` rather than a bare add-on error.
        """
        plan = await self.prepare_action(tab_id, need_snapshot=False)
        result = await self.command(
            P.METHOD_ACTIVATE_TAB, dict(P.activate_tab_params(plan.tab_id))
        )
        return self._settle(plan, result)

    async def scroll(
        self,
        tab_id: Any = None,
        direction: str = P.SCROLL_DOWN,
        amount: int | None = None,
    ) -> dict[str, Any]:
        """Scroll one tab. LOCAL_UI, and yet it can invalidate a snapshot.

        Scrolling an infinite list loads more of it, which adds interactive nodes
        and bumps ``page_version``; :meth:`_settle` therefore drops the tab's
        cached snapshot exactly as it would after a click. A model that scrolled
        and then reused an element id from before the scroll is answered with a
        remedy instead of acting on whatever now holds that id.

        ``direction`` is validated by
        :func:`~iron_jarvis.browser.protocol.scroll_params`, which raises rather
        than putting an unknown word on the wire — the content script compares the
        raw string, so an unrecognised direction would scroll nowhere and answer
        success, which a model reads as "done".
        """
        plan = await self.prepare_action(tab_id, need_snapshot=False)
        params = P.scroll_params(plan.tab_id, direction=direction, amount=amount)
        result = await self.command(P.METHOD_SCROLL, dict(params))
        return self._settle(plan, result)

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
    "ActionTarget",
    "BrowserRuntime",
    "BrowserService",
    "min_access_for",
]
