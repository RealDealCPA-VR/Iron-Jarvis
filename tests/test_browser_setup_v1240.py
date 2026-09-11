"""v1.240.0, Ship 1 — the time-boxed setup window, and the press it never replaces.

Setup asks a user to press Pair in Jarvis and Allow in Chrome at a moment they
cannot predict, on two different surfaces. The window makes the second of those
two happen on its own and puts the first in front of the user. It does NOT make
the first happen on its own, and this file is mostly about why not.

**A CREDENTIAL IS NEVER MINTED WITHOUT A HUMAN PRESS, ARMED OR NOT.** An earlier
draft of this ship auto-paired any socket whose extension id matched the pinned
add-on's while the window was open. That id is derived from the ``Origin`` header
the client itself writes; ``/browser/ws?pairing=1`` is the one endpoint on the
daemon that needs no credential (``TokenAuthMiddleware`` is a
``BaseHTTPMiddleware`` and cannot see WebSockets at all); and the pinned id is a
public constant compiled into every install. So any process running as the user
could open that socket, claim the add-on's origin, and be handed the token — with
the card reading "Connected", and the real add-on locked out forever afterwards
because the store was already paired. There is no cryptographic repair: any secret
the real add-on can read, a local process running as the user can read too. Only a
human looking at a trusted surface can tell them apart, so the Pair press stays and
``_auto_pair`` is gone. ``test_a_forged_extension_origin_is_never_paired_inside_an_armed_window``
drives the REAL app and pins that closed.

**What the window still does, because neither of these mints anything.** It sends
the add-on the directive that opens the add-on's own site-access page, and it tells
the card a setup is in progress so the Pair button is offered in context instead of
being hunted for.

**Expiry is real, and it is not a wall clock.** A deadline stored as ``time.time()``
is reopened by any backwards clock step — an NTP correction, a laptop resuming
from sleep — and a window the user shut two hours ago is open again with nothing
saying so. Every case here drives ``_monotonic`` through a monkeypatched clock;
NOTHING in this file sleeps or asserts an elapsed duration, because an assertion
that waits out 120 real seconds measures the hardware and goes red on CI.

**The window ends at something the user did.** Its deadline, their Cancel, or their
successful Pair — a window left armed by a completed pairing keeps the card
claiming a setup is running, and a Forget pressed inside the leftover minute drops
back into a setup state nobody asked for twice.

**Auto-grant decides about the socket it is delivered to.** It is a background task
reading one socket's ``host_permission`` and sending to whichever socket is
authoritative when it runs. A D08 replacement in between means it decides about A
and delivers to B; the adoption check is pinned here.

**Auto-grant is not awaited by the reader.** ``request_host_permission`` sends a
directive and awaits the add-on's reply, and the only coroutine that reads that
socket is the pump. Awaiting it inline parks the reader on a frame only the reader
can deliver. The scheduling seam is pinned so a later edit cannot quietly make it
an ``await`` again.

**The access mode is written by ONE writer.** Arming with an access word goes
through the app's own ``PUT /settings`` handler, which validates, journals the
undo and persists atomically. A second writer here would set the field on the live
``Config`` and skip the persist, so the mode the user chose in the setup modal
would be gone at the next boot.

The route-reachability cases drive the REAL ``create_app`` with install auth on,
because this repository shipped a route no install could reach while ninety tests
over a bare ``FastAPI()`` were green.

Every stand-in in this file takes ``*args, **kw``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.identity import (
    PINNED_EXTENSION_ID,
    extension_origin,
)
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import (
    ACCESS_INTERACTIVE,
    ACCESS_OFF,
    ACCESS_READ_ONLY,
    BrowserRuntime,
)
from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import open_db
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import browser as browser_routes

INSTALL_BEARER = "setup-window-install-bearer"
LOOPBACK_ORIGIN = "http://127.0.0.1:8788"

#: An extension id that is a plausible Chrome id and is NOT the pinned one.
#: Built from the id alphabet so nothing here is refused for being malformed —
#: the refusal under test must be about identity, not about shape.
OTHER_EXTENSION_ID = "b" * 32

#: Attempts, and the seconds each waits, for a daemon-side state change. A budget,
#: not a deadline: the assertion that follows names what never happened, and
#: nothing asserts how long it took.
WAIT_ATTEMPTS = 200
WAIT_STEP_S = 0.05

#: How long :func:`frames_for_a_while` listens for a frame it expects NEVER to
#: arrive. Short on purpose and still a budget, not a threshold: every frame those
#: cases would catch is written to the socket inside the connect handler itself,
#: before the pump has read anything, so it is there in milliseconds or not at all.
#: The whole budget is spent on every passing run, so a ten-second one is ten
#: seconds added to CI for each case, forever.
QUIET_BUDGET_S = 2.0


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INSTALL_BEARER}", "Origin": LOOPBACK_ORIGIN}


class FakeClock:
    """A monotonic clock a test drives by hand. Takes ``*args, **kw``."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def __call__(self, *args: Any, **kw: Any) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class Bare:
    """A runtime stand-in that is nothing but a place to hang the deadline."""


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """``routes.browser._monotonic``, replaced by a clock the test moves."""
    fake = FakeClock()
    monkeypatch.setattr(browser_routes, "_monotonic", fake)
    return fake


# --------------------------------------------------------------------------- #
# The window itself
# --------------------------------------------------------------------------- #


def test_a_fresh_runtime_has_no_window(clock):
    """Nothing arms this at boot. The default state is shut."""
    runtime = Bare()

    assert browser_routes._setup_armed(runtime) is False
    assert browser_routes._setup_remaining_s(runtime) == 0.0
    assert browser_routes._setup_view(runtime) == {"armed": False, "expires_in_s": 0}


def test_arming_opens_a_window_of_exactly_the_declared_length(clock):
    """The window is ``SETUP_WINDOW_S``, and the deadline is stored on the runtime."""
    runtime = Bare()

    window = browser_routes._setup_arm(runtime)

    assert window == browser_routes.SETUP_WINDOW_S
    assert browser_routes._setup_armed(runtime) is True
    assert browser_routes._setup_remaining_s(runtime) == pytest.approx(
        browser_routes.SETUP_WINDOW_S
    )
    assert getattr(runtime, browser_routes.SETUP_DEADLINE_ATTR) == pytest.approx(
        clock.now + browser_routes.SETUP_WINDOW_S
    )


def test_the_window_is_two_minutes(clock):
    """Pinned, because it is a security bound and not a tuning knob."""
    assert browser_routes.SETUP_WINDOW_S == 120.0


def test_the_remaining_time_falls_as_the_clock_moves(clock):
    """Time left is computed at the point of use, from the monotonic clock."""
    runtime = Bare()
    browser_routes._setup_arm(runtime)

    clock.advance(30.0)

    assert browser_routes._setup_remaining_s(runtime) == pytest.approx(90.0)
    assert browser_routes._setup_view(runtime) == {"armed": True, "expires_in_s": 90}


def test_the_window_shuts_itself_at_the_deadline(clock):
    """Expiry needs no button and no background timer — only a reader."""
    runtime = Bare()
    browser_routes._setup_arm(runtime)

    clock.advance(browser_routes.SETUP_WINDOW_S)

    assert browser_routes._setup_armed(runtime) is False
    assert browser_routes._setup_view(runtime) == {"armed": False, "expires_in_s": 0}


def test_an_expired_window_never_reopens(clock):
    """Long past the deadline is still shut, not negative and not wrapped."""
    runtime = Bare()
    browser_routes._setup_arm(runtime)

    clock.advance(browser_routes.SETUP_WINDOW_S * 100)

    assert browser_routes._setup_remaining_s(runtime) == 0.0
    assert browser_routes._setup_armed(runtime) is False


def test_expiry_is_checked_on_every_use_not_by_a_timer(clock):
    """No task, no sweeper: the same object answers differently as the clock moves.

    A background timer that failed to be scheduled, was cancelled with its loop or
    raised on a tick would leave the window open forever, and nothing would look
    different from outside.
    """
    runtime = Bare()
    browser_routes._setup_arm(runtime)
    assert browser_routes._setup_armed(runtime) is True

    clock.advance(119.0)
    assert browser_routes._setup_armed(runtime) is True
    clock.advance(2.0)
    assert browser_routes._setup_armed(runtime) is False


def test_the_clock_is_monotonic_not_the_wall_clock(monkeypatch, clock):
    """A backwards wall-clock step must not extend a window by one second.

    Driven the way it really happens: the window is armed, the clock the daemon
    measures on keeps moving forward past the deadline, and ``time.time`` jumps
    two hours BACKWARDS underneath it.
    """
    runtime = Bare()
    browser_routes._setup_arm(runtime)
    clock.advance(browser_routes.SETUP_WINDOW_S + 1.0)

    monkeypatch.setattr(time, "time", lambda *a, **kw: 0.0)

    assert browser_routes._setup_armed(runtime) is False


def test_the_real_monotonic_seam_is_time_monotonic(monkeypatch):
    """``_monotonic`` really reads ``time.monotonic`` — not merely something."""
    monkeypatch.setattr(time, "monotonic", lambda *a, **kw: 4242.0)

    assert browser_routes._monotonic() == 4242.0


def test_disarming_shuts_an_open_window(clock):
    runtime = Bare()
    browser_routes._setup_arm(runtime)

    browser_routes._setup_disarm(runtime)

    assert browser_routes._setup_armed(runtime) is False


def test_disarming_a_shut_window_is_harmless(clock):
    """Cancel is the safe direction, so it must never fail."""
    runtime = Bare()

    browser_routes._setup_disarm(runtime)
    browser_routes._setup_disarm(runtime)

    assert browser_routes._setup_armed(runtime) is False


def test_an_unreadable_deadline_counts_as_shut(clock):
    """Fails CLOSED: the dangerous direction of this flag is 'armed'."""
    runtime = Bare()
    setattr(runtime, browser_routes.SETUP_DEADLINE_ATTR, "soon")

    assert browser_routes._setup_armed(runtime) is False
    assert browser_routes._setup_view(runtime) == {"armed": False, "expires_in_s": 0}


def test_a_runtime_that_refuses_the_attribute_counts_as_shut(clock):
    """A stand-in runtime that raises on attribute access is not an open window."""

    class Angry:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("no attributes here")

    assert browser_routes._setup_armed(Angry()) is False


def test_expires_in_s_rounds_up_so_armed_is_never_zero_seconds(clock):
    """'Armed, 0 seconds left' would read as a bug to anyone rendering a countdown."""
    runtime = Bare()
    browser_routes._setup_arm(runtime)
    clock.advance(browser_routes.SETUP_WINDOW_S - 0.25)

    view = browser_routes._setup_view(runtime)

    assert view["armed"] is True
    assert view["expires_in_s"] == 1


# --------------------------------------------------------------------------- #
# The gate that must never come back
# --------------------------------------------------------------------------- #


def test_the_module_mints_no_credential_of_its_own():
    """There is no ``_auto_pair`` and no ``_pinned_socket``, and there must not be.

    Both existed to hand a socket the pairing token because its ``Origin`` header
    named the pinned extension id. The client writes that header, the socket that
    carries it needs no credential to open, and the id is public material in every
    install — so the pair of them amounted to "mint a credential for whoever asked
    first". This is a NAME-LEVEL pin, deliberately: the behavioural pin is
    ``test_a_forged_extension_origin_is_never_paired_inside_an_armed_window``, and
    this one exists so that reintroducing the helper is a conscious act rather than
    something a refactor can do quietly.
    """
    assert not hasattr(browser_routes, "_auto_pair")
    assert not hasattr(browser_routes, "_pinned_socket")


def test_completing_a_pairing_shuts_the_window(clock):
    """The window ends at something the user did, and Pair is one of those things.

    THE SILENT FAILURE: a window left armed by a successful pairing keeps the card
    claiming a setup is in progress for the rest of its two minutes, and a Forget
    pressed inside that leftover minute drops the user back into a setup state they
    asked for once, not twice.
    """
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    assert browser_routes._setup_armed(runtime) is True

    browser_routes._setup_disarm(runtime)

    assert browser_routes._setup_armed(runtime) is False


def test_arming_again_re_bases_the_deadline_from_now(clock):
    """Pressing Set up a second time buys another full window, and says so.

    A user who pressed Set up, went to find Chrome's extensions page and came back
    to an expired window needs longer, and pressing the button again is how they
    ask. The route docstring once claimed the window "is not extended by use" while
    this line re-based it every call — a bound described one way and implemented
    another is a bound nobody can reason about, so the words now match this.
    """
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    clock.advance(browser_routes.SETUP_WINDOW_S - 5.0)
    assert browser_routes._setup_remaining_s(runtime) == pytest.approx(5.0)

    browser_routes._setup_arm(runtime)

    assert browser_routes._setup_remaining_s(runtime) == pytest.approx(
        browser_routes.SETUP_WINDOW_S
    )


# --------------------------------------------------------------------------- #
# Auto-grant, as a decision
# --------------------------------------------------------------------------- #


class FakeSocket:
    """The two methods ``ExtensionConnection`` calls, and a record of what it sent."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class FakeBackend:
    """Just the one attribute the adoption check reads. Takes ``*args, **kw``."""

    def __init__(self) -> None:
        self.connection: Any = None


class SpyRuntime:
    """A runtime that records the calls auto-grant may make."""

    def __init__(self) -> None:
        self.grants = 0
        self.grant_error: Exception | None = None
        self.backend = FakeBackend()

    async def request_host_permission(self, *args: Any, **kw: Any) -> dict[str, Any]:
        self.grants += 1
        if self.grant_error is not None:
            raise self.grant_error
        return {"ok": True}


# --------------------------------------------------------------------------- #
# Auto-grant, as a decision
# --------------------------------------------------------------------------- #


def _adopted(runtime: SpyRuntime, host_permission: bool = False) -> ExtensionConnection:
    """A paired socket that IS the runtime's authoritative one.

    The adoption is set here rather than assumed, because "this socket is the one
    the directive will reach" is exactly the fact ``_auto_grant`` checks.
    """
    conn = ExtensionConnection(
        FakeSocket(),
        extension_id=PINNED_EXTENSION_ID,
        host_permission=host_permission,
        paired=True,
    )
    runtime.backend.connection = conn
    return conn


async def test_auto_grant_asks_when_the_socket_reports_no_permission(clock):
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)

    assert await browser_routes._auto_grant(runtime, _adopted(runtime)) is True
    assert runtime.grants == 1


async def test_auto_grant_stays_quiet_when_the_permission_is_already_held(clock):
    """Opening a setup tab at a user who already granted is noise, not help."""
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)

    assert await browser_routes._auto_grant(runtime, _adopted(runtime, host_permission=True)) is False
    assert runtime.grants == 0


async def test_auto_grant_never_happens_with_the_window_shut(clock):
    runtime = SpyRuntime()

    assert await browser_routes._auto_grant(runtime, _adopted(runtime)) is False
    assert runtime.grants == 0


async def test_auto_grant_never_happens_after_the_window_expires(clock):
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    clock.advance(browser_routes.SETUP_WINDOW_S + 0.001)

    assert await browser_routes._auto_grant(runtime, _adopted(runtime)) is False
    assert runtime.grants == 0


async def test_auto_grant_decides_once_per_socket(clock):
    """A directive per frame would open a tab per frame."""
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    conn = _adopted(runtime)

    first = await browser_routes._auto_grant(runtime, conn)
    second = await browser_routes._auto_grant(runtime, conn)
    third = await browser_routes._auto_grant(runtime, conn)

    assert (first, second, third) == (True, False, False)
    assert runtime.grants == 1


async def test_a_failed_grant_never_drops_the_connection(clock):
    runtime = SpyRuntime()
    runtime.grant_error = BrowserError(BrowserErrorCode.BROWSER_NOT_CONNECTED)
    browser_routes._setup_arm(runtime)

    assert await browser_routes._auto_grant(runtime, _adopted(runtime)) is False


async def test_a_replaced_socket_never_has_its_grant_delivered_to_the_new_one(clock):
    """D08: the socket it decided about must be the socket it sends to.

    THE SILENT FAILURE: this runs as a background task, so a replacement browser
    can be adopted between scheduling and running. It read socket A's
    ``host_permission`` — false — but ``request_host_permission`` sends to whatever
    socket is authoritative NOW. Without this check the user's newly connected
    browser, which may already hold the grant, gets a setup page opened in its face,
    and socket A, the one that actually needed asking, is never asked.
    """
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    scheduled_for = _adopted(runtime)
    replacement = _adopted(runtime)  # adopts the replacement in its place
    assert runtime.backend.connection is replacement

    assert await browser_routes._auto_grant(runtime, scheduled_for) is False
    assert runtime.grants == 0, "the directive must not have gone to the other socket"


async def test_an_unreadable_backend_sends_no_grant(clock):
    """Not sending a convenience is the safe direction when nothing can be checked."""
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    conn = _adopted(runtime)

    class _Exploding:
        @property
        def connection(self) -> Any:
            raise RuntimeError("the backend fell over")

    runtime.backend = _Exploding()

    assert await browser_routes._auto_grant(runtime, conn) is False
    assert runtime.grants == 0


async def test_the_grant_is_scheduled_not_awaited_by_the_reader(clock):
    """The reader must not park on a reply only the reader can deliver.

    ``request_host_permission`` awaits the add-on's response to its directive, and
    the pump is the only coroutine reading that socket. This pins the seam: the
    scheduler returns a task and has NOT run the grant by the time it returns.
    """
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    conn = _adopted(runtime)

    task = browser_routes._schedule_auto_grant(runtime, conn)

    assert task is not None
    assert runtime.grants == 0, "the grant must not have run before the scheduler returned"
    await task
    assert runtime.grants == 1


async def test_the_scheduled_task_is_held_by_a_strong_reference(clock):
    """A task nothing refers to can be collected mid-await, on some runs only."""
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    conn = _adopted(runtime)

    task = browser_routes._schedule_auto_grant(runtime, conn)

    assert getattr(conn, browser_routes._AUTO_GRANT_TASK_ATTR, None) is task
    await task


async def test_scheduling_twice_runs_the_grant_once(clock):
    runtime = SpyRuntime()
    browser_routes._setup_arm(runtime)
    conn = _adopted(runtime)

    first = browser_routes._schedule_auto_grant(runtime, conn)
    await first
    second = browser_routes._schedule_auto_grant(runtime, conn)

    assert second is None
    assert runtime.grants == 1


# --------------------------------------------------------------------------- #
# The real socket, over the real route
# --------------------------------------------------------------------------- #


class _Deps:
    """The create_app deps object, reduced to the one field these routes read."""

    def __init__(self, runtime: Any) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class SocketApp:
    """The real ``/browser/ws`` over a real backend, ``PairingStore`` and ``Config``.

    No middleware: the credential decisions belong to
    ``tests/test_browser_auth_v1235.py``. What these cases assert is what the pump
    and the socket handler do with a socket they have accepted.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.config = load_config(tmp_path)
        self.config.browser_access = ACCESS_INTERACTIVE
        self.engine = open_db(tmp_path / "browser-setup.db")
        self.store = PairingStore(self.engine)
        self.backend = ExtensionBackend()
        self.runtime = BrowserRuntime(
            backend=self.backend, config=self.config, pairing=self.store
        )
        self.app = FastAPI()
        browser_routes.register(self.app, _Deps(self.runtime))


def _wait_for(predicate, what: str) -> None:
    """Poll a daemon-side fact. A budget, not a timing assertion."""
    for _ in range(WAIT_ATTEMPTS):
        if predicate():
            return
        time.sleep(WAIT_STEP_S)
    raise AssertionError(what)


def frames_until(ws: Any, kind: str, what: str, *, limit: int = 8) -> list[dict]:
    """Frames off a TestClient socket, read on a BOUNDED reader thread.

    A ``TestClient`` websocket receive has no timeout of its own. Every negative
    case in this file is a mutation away from a socket that never sends the frame
    the assertion is waiting for, and an unbounded read there does not fail the
    gate — it parks it forever, which is indistinguishable from a slow runner. So
    the read runs on a thread with a join budget and the test fails BY NAME.

    A budget is not a timing assertion: nothing here asserts how long anything took.
    """
    seen: list[dict] = []

    def read() -> None:
        try:
            for _ in range(limit):
                frame = ws.receive_json()
                seen.append(frame)
                if frame.get("type") == kind:
                    return
        except Exception:  # noqa: BLE001 - a torn-down app reads as "no more frames"
            pass

    reader = threading.Thread(target=read, name="setup-frame-reader", daemon=True)
    reader.start()
    reader.join(WAIT_ATTEMPTS * WAIT_STEP_S)
    if not any(f.get("type") == kind for f in seen):
        raise AssertionError(f"{what} (frames seen: {[f.get('type') for f in seen]})")
    return seen


def frames_for_a_while(ws: Any, *, limit: int = 4) -> list[dict]:
    """Every frame the socket volunteers inside a read budget, then stop.

    The companion to :func:`frames_until` for cases whose claim is that a frame
    NEVER arrives. A plain ``receive_json`` on a ``TestClient`` socket has no
    timeout, so waiting for a frame that is correctly absent parks the gate forever
    rather than passing it. This reads on a bounded thread and returns what it got.

    A budget is not a timing assertion: nothing here asserts how long anything took.
    """
    seen: list[dict] = []

    def read() -> None:
        try:
            for _ in range(limit):
                seen.append(ws.receive_json())
        except Exception:  # noqa: BLE001 - a torn-down app reads as "no more frames"
            pass

    reader = threading.Thread(target=read, name="setup-quiet-reader", daemon=True)
    reader.start()
    reader.join(QUIET_BUDGET_S)
    return seen


def test_the_pinned_addon_inside_an_armed_window_still_waits_for_a_press(tmp_path):
    """The real add-on, the window WIDE OPEN, and still nothing is minted.

    This is the inverse of what Ship 1 first shipped. The socket is offered a
    pairing and gets no token, because being the pinned add-on is a claim the
    socket makes about itself in a header. The window's job is to put the Pair
    button in front of the user, not to press it for them.

    The ack is the barrier: once the daemon has recorded it, the socket handler has
    long since finished deciding, so "not paired" is a settled fact rather than a
    race this test happened to win.
    """
    app = SocketApp(tmp_path)
    browser_routes._setup_arm(app.runtime)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            offer = ws.receive_json()
            assert offer["type"] == P.FRAME_PAIRING_REQUIRED
            assert "token" not in offer
            request_id = offer["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))

            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            assert app.store.paired() is False, "nothing may pair without a press"
            assert app.backend.connected is False
            assert conn.paired is False


def test_the_same_connection_outside_the_window_is_treated_identically(tmp_path):
    """Armed and unarmed are the SAME for pairing, which is the point of the fix.

    A difference here would be a difference an unauthenticated caller can create
    for itself, by opening its socket at the moment the window happens to be open.
    """
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            offer = ws.receive_json()
            assert offer["type"] == P.FRAME_PAIRING_REQUIRED
            request_id = offer["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))

            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            assert app.store.paired() is False, "nothing may pair without a press"
            assert app.backend.connected is False
            assert conn.paired is False


def test_an_unpinned_extension_inside_an_armed_window_is_no_different(tmp_path):
    """The window is not a licence for anything that connects, pinned id or not."""
    app = SocketApp(tmp_path)
    browser_routes._setup_arm(app.runtime)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1",
            headers={"Origin": extension_origin(OTHER_EXTENSION_ID)},
        ) as ws:
            offer = ws.receive_json()
            assert offer["type"] == P.FRAME_PAIRING_REQUIRED
            request_id = offer["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))
            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            assert app.store.paired() is False
            assert app.backend.connected is False


def test_pressing_pair_shuts_the_setup_window_over_the_real_route(tmp_path):
    """The press ends the setup, so the window ends with it.

    THE SILENT FAILURE: the window would otherwise run on for the rest of its two
    minutes after the job it was opened for is done — the card keeps saying a setup
    is in progress, and a Forget pressed in that leftover minute re-enters a state
    the user asked for once.
    """
    app = SocketApp(tmp_path)
    browser_routes._setup_arm(app.runtime)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            request_id = ws.receive_json()["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))
            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            r = client.post("/browser/pair", json={"request_id": request_id})
            assert r.status_code == 200, r.text
            assert app.store.paired() is True

            assert client.get("/browser/status").json()["setup"] == {
                "armed": False,
                "expires_in_s": 0,
            }


def test_an_unarmed_pairing_socket_is_asked_for_nothing(tmp_path):
    """No window, no directive — the user is not interrupted by an unasked-for tab."""
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            request_id = ws.receive_json()["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))
            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            # READ THE CLIENT SIDE. ``conn.ws`` here is a real Starlette socket
            # with no ``frames`` list, so an assertion over ``getattr(conn.ws,
            # "frames", [])`` is an assertion over ``[]`` — it passes with a
            # directive going out, which is exactly the bug it claims to catch.
            after = frames_for_a_while(ws, limit=2)

            assert not any(f.get("type") == P.FRAME_DIRECTIVE for f in after), (
                f"an unpaired socket was sent a directive: {after}"
            )


def test_an_armed_pairing_socket_is_asked_for_nothing_either(tmp_path):
    """The window changes nothing about a socket that has not been paired yet.

    Auto-grant belongs to an ADOPTED browser — one whose user pressed Pair. Sending
    the directive to a socket that has only asked would be Jarvis opening a tab in
    a browser the user has not yet said belongs to them.
    """
    app = SocketApp(tmp_path)
    browser_routes._setup_arm(app.runtime)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            request_id = ws.receive_json()["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))
            conn = app.backend.restricted_socket(request_id)
            assert conn is not None
            _wait_for(lambda: conn.pairing_acked, "the daemon never read the ack")

            # READ THE CLIENT SIDE. ``conn.ws`` here is a real Starlette socket
            # with no ``frames`` list, so an assertion over ``getattr(conn.ws,
            # "frames", [])`` is an assertion over ``[]`` — it passes with a
            # directive going out, which is exactly the bug it claims to catch.
            after = frames_for_a_while(ws, limit=2)

            assert not any(f.get("type") == P.FRAME_DIRECTIVE for f in after), (
                f"an unpaired socket was sent a directive: {after}"
            )


# --------------------------------------------------------------------------- #
# The routes, over the REAL app
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The REAL app, with the install bearer the desktop app uses."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_BEARER)
    app = create_app(str(tmp_path / "home"))
    with TestClient(app) as c:
        yield c


def test_a_forged_extension_origin_is_never_paired_inside_an_armed_window(client):
    """THE ATTACK THIS CLOSES, in plain words.

    Anything already running on this machine as the user — a script, a downloaded
    binary, a page's helper process — can open ``ws://127.0.0.1:8787/browser/ws?pairing=1``
    without any credential at all, because that is the one endpoint that bootstraps
    a credential and ``TokenAuthMiddleware`` is a ``BaseHTTPMiddleware`` that cannot
    see WebSockets. It can then set ``Origin: chrome-extension://<pinned id>`` — the
    pinned id is not a secret; it is a constant compiled into every copy of Jarvis
    that ships, and ``GET /browser/status`` prints it. So it can present itself as
    the real add-on perfectly, because "being the real add-on" was only ever a
    string the client chose to send.

    If the setup window auto-paired on the strength of that string, the first thing
    to open a socket in the two minutes after the user pressed Set up would be
    handed the browser token — and would then hold it, because ``complete_pairing``
    refuses a second pairing. The user's real add-on would be permanently locked
    out while the card read "Connected", and nothing anywhere would say which
    browser was on the other end.

    So: the window is armed on the REAL app, the forged socket connects, and it
    gets a pairing OFFER and nothing else. No ``browser.paired`` frame, no token in
    any frame, and ``GET /browser/status`` does not read paired. The remedy is the
    Pair press, which happens on a surface only the human can see.
    """
    armed = client.post("/browser/setup/arm", headers=_headers(), json={})
    assert armed.status_code == 200, armed.text
    assert armed.json()["armed"] is True

    with client.websocket_connect(
        "/browser/ws?pairing=1",
        headers={"Origin": f"chrome-extension://{PINNED_EXTENSION_ID}"},
    ) as ws:
        seen = frames_for_a_while(ws, limit=4)

    kinds = [f.get("type") for f in seen]
    assert P.FRAME_PAIRING_REQUIRED in kinds, f"the socket was not even offered: {kinds}"
    assert P.FRAME_PAIRED not in kinds, (
        f"a forged origin was handed a pairing inside the setup window: {seen}"
    )
    assert not any(f.get("token") for f in seen), (
        f"a credential reached a socket that presented none: {seen}"
    )
    assert client.get("/browser/status", headers=_headers()).json()["paired"] is False


def test_the_arm_route_is_reachable_on_a_real_install(client):
    """A bare app tests the route; this tests the product.

    This repository shipped a route no install could reach while ninety tests over
    a bare ``FastAPI()`` were green.
    """
    r = client.post("/browser/setup/arm", headers=_headers(), json={})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["armed"] is True
    assert body["expires_in_s"] == int(browser_routes.SETUP_WINDOW_S)
    assert "addon_dir" in body


def test_the_arm_route_names_the_folder_the_status_route_names(client):
    """ONE resolver. A second 'where is the add-on' is the failure this repo pays for."""
    armed = client.post("/browser/setup/arm", headers=_headers(), json={}).json()
    status = client.get("/browser/status", headers=_headers()).json()

    assert armed["addon_dir"] == status["addon_dir"]


def test_arming_sets_the_access_mode_through_the_settings_writer(client):
    """The mode the modal chose is PERSISTED, not merely set on the live config."""
    r = client.post("/browser/setup/arm", headers=_headers(), json={"access": "read_only"})

    assert r.status_code == 200, r.text
    assert client.get("/settings", headers=_headers()).json()["settings"][
        "browser_access"
    ] == ACCESS_READ_ONLY
    assert client.get("/browser/status", headers=_headers()).json()["access"] == (
        ACCESS_READ_ONLY
    )


def test_arming_can_choose_interactive(client):
    client.post("/browser/setup/arm", headers=_headers(), json={"access": "interactive"})

    assert client.get("/settings", headers=_headers()).json()["settings"][
        "browser_access"
    ] == ACCESS_INTERACTIVE


def test_arming_without_an_access_word_changes_no_setting(client):
    """Set up must not switch a capability on that the user never chose."""
    before = client.get("/settings", headers=_headers()).json()["settings"][
        "browser_access"
    ]
    assert before == ACCESS_OFF

    client.post("/browser/setup/arm", headers=_headers(), json={})

    assert client.get("/settings", headers=_headers()).json()["settings"][
        "browser_access"
    ] == ACCESS_OFF


def test_an_invalid_access_word_is_refused_by_the_settings_validator(client):
    """The validation is the setting's own — not a second copy living here."""
    r = client.post("/browser/setup/arm", headers=_headers(), json={"access": "everything"})

    assert r.status_code == 400, r.text


def test_status_carries_the_setup_window(client):
    """The card's countdown reads one key, and it is present before anything is armed."""
    before = client.get("/browser/status", headers=_headers()).json()
    assert before["setup"] == {"armed": False, "expires_in_s": 0}

    client.post("/browser/setup/arm", headers=_headers(), json={})

    after = client.get("/browser/status", headers=_headers()).json()["setup"]
    assert after["armed"] is True
    assert isinstance(after["expires_in_s"], int)
    assert 0 < after["expires_in_s"] <= int(browser_routes.SETUP_WINDOW_S)


def test_the_countdown_never_reads_longer_than_the_window(monkeypatch):
    """v1.246.1: the release gate went red on ``assert 121 <= 120``. The
    deadline is ``monotonic() + 120.0``; on a clock that has not ticked since
    the arm (Windows resolution ~16 ms), ``(m + 120.0) - m`` can come back a
    hair above 120 in floating point, and rounding up said 121. That happens
    only when ``m + 120`` crosses a power of two (the sum then rounds at the
    coarser spacing) — a 120-second stretch below 2048 s of uptime, about when
    a CI runner reaches this test. Driven with a clock value that really
    overshoots, found here rather than hard-coded."""
    from types import SimpleNamespace

    window = browser_routes.SETUP_WINDOW_S
    m = next(
        v for v in (2048.0 - window + i * 0.000123 for i in range(1_000_000))
        if (v + window) - v > window
    )
    monkeypatch.setattr(browser_routes, "_monotonic", lambda: m)
    runtime = SimpleNamespace()
    browser_routes._setup_arm(runtime)
    assert browser_routes._setup_view(runtime) == {
        "armed": True, "expires_in_s": int(window),
    }
    # The other edge: a window with a nanosecond left is still armed, and an
    # armed window never reads 0 seconds.
    deadline = getattr(runtime, browser_routes.SETUP_DEADLINE_ATTR)
    monkeypatch.setattr(browser_routes, "_monotonic", lambda: deadline - 1e-9)
    assert browser_routes._setup_view(runtime) == {"armed": True, "expires_in_s": 1}


def test_the_disarm_route_shuts_the_window_on_a_real_install(client):
    client.post("/browser/setup/arm", headers=_headers(), json={})

    r = client.post("/browser/setup/disarm", headers=_headers())

    assert r.status_code == 200, r.text
    assert r.json() == {"armed": False}
    assert client.get("/browser/status", headers=_headers()).json()["setup"] == {
        "armed": False,
        "expires_in_s": 0,
    }


def test_disarm_never_fails_even_with_nothing_armed(client):
    r = client.post("/browser/setup/disarm", headers=_headers())

    assert r.status_code == 200, r.text
    assert r.json() == {"armed": False}


def test_both_setup_paths_are_declared_and_really_served(client):
    """Declared in ``SERVED_PATHS`` AND read back off the app's own route table."""
    assert "/browser/setup/arm" in browser_routes.SERVED_PATHS
    assert "/browser/setup/disarm" in browser_routes.SERVED_PATHS
    served = browser_routes.served_paths()
    assert "/browser/setup/arm" in served
    assert "/browser/setup/disarm" in served


def test_the_setup_routes_refuse_an_unauthenticated_caller(client):
    """The install bearer guards these like every other route on this app."""
    r = client.post("/browser/setup/arm", json={})

    assert r.status_code in (401, 403), r.text


async def test_arming_without_a_browser_runtime_refuses_honestly():
    """No bridge, no window — and the caller is told, rather than being lied to."""
    app = FastAPI()
    browser_routes.register(app, _Deps(None))
    with TestClient(app) as client:
        r = client.post("/browser/setup/arm", json={})

    assert r.status_code == 503, r.text


def test_arming_with_an_access_word_and_no_settings_writer_refuses():
    """Rather than writing the setting a second way, it says it cannot.

    A bare app has no ``PUT /settings``; a route that fell back to setting the
    field on the live config here would skip the persist and lose the user's
    choice at the next boot with nothing to show for it.
    """
    app = FastAPI()

    class _R:
        pass

    browser_routes.register(app, _Deps(_R()))
    with TestClient(app) as client:
        r = client.post("/browser/setup/arm", json={"access": "read_only"})

    assert r.status_code == 503, r.text


if __name__ == "__main__":  # pragma: no cover - convenience only
    pytest.main([__file__])
