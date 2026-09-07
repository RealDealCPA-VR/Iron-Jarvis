"""The socket under stress, and the history behind one error line (v1.239.0, Ship 5).

Ship 5 adds no capability. It asks whether the two things the Browser page shows a
user in trouble are TRUE: that an unauthenticated socket cannot cost the daemon
anything unbounded, and that the fault the card names is backed by a record rather
than by whatever happened last.

Ships 1-3 had already built most of the first half, and this file leaves that work
alone. What was actually missing is one hole, and it is the shape the whole defence
was written for:

* **The refusal bound is on CONSECUTIVE refusals and one readable frame forgives
  it.** ``/browser/ws?pairing=1`` needs no credential by design, so any local
  process can open one, send nine unreadable frames, send one well-formed
  ``browser.pairing_ack``, and repeat for as long as it likes. The refusal count
  resets, the WARNING budget resets with it -- and the D06A pairing deadline never
  fires either, because the route only consults it on an IDLE tick that a socket
  sending continuously never reaches. Nothing in the daemon ended that loop.
  ``MAX_UNPAIRED_FRAMES`` bounds the LIFETIME of a socket that has not
  authenticated, and nothing forgives it.

The second half is new. ``last_error`` is one string that a successful round trip
deliberately retires -- right for the card's "Last problem" line, and useless for
the question a user with a flaky browser actually asks, which is why it keeps
dropping. ``note_error`` writes both that line and a bounded ledger row; nothing
clears the ledger, a repeat bumps a count instead of taking a row, and
``error_ledger`` hands out copies so the record cannot be edited after the fact.

Scope, and why the flood case is driven twice. The unit cases drive
``ExtensionBackend`` through its own surface, where the state machine lives. The
route case drives the REAL ``/browser/ws`` with the real ``PairingStore``, a real
``BrowserRuntime`` and a real ``Config``, because a bound that the pump never
consults is a bound no attacker meets -- and the pump, not the backend, is what
reads the socket. Both are needed: the unit case says the rule exists, the route
case says a caller reaches it.

No assertion here measures a wall clock. The route case's close code is read on a
bounded reader thread that fails BY NAME, because a ``TestClient`` websocket
receive has no timeout of its own and an unbounded one turns a regression into a
hung release gate.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import (
    CLOSE_PROTOCOL_ERROR,
    MAX_ERROR_DETAIL_CHARS,
    MAX_ERROR_LEDGER,
    MAX_REFUSED_FRAMES,
    MAX_UNPAIRED_FRAMES,
    ExtensionBackend,
    ExtensionConnection,
    clip_detail,
)
from iron_jarvis.browser.identity import PINNED_EXTENSION_ID, extension_origin
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import ACCESS_INTERACTIVE, BrowserRuntime
from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import open_db
from iron_jarvis.daemon.routes import browser as browser_routes

#: Attempts, and the seconds each waits, for a frame or a close code. A budget, not
#: a deadline: the assertion that follows names what never happened, and nothing
#: asserts how long it took.
WAIT_ATTEMPTS = 200
WAIT_STEP_S = 0.05

#: Ticks a test will yield the loop while waiting for another task to reach a point.
#: A budget, not a deadline: the assertion that follows names what never happened.
WAIT_TICKS = 500

#: Seconds any single await here may block before the test fails BY NAME. Generous
#: enough that a loaded CI runner never trips it, finite so a regression that
#: reintroduces a hanging future fails the gate instead of pinning a core.
HANG_GUARD_S = 10.0


class FakeSocket:
    """The two methods ``ExtensionConnection`` calls, and a record of what it sent.

    Not a mock: a real object with ``send_json`` and ``close`` runs exactly the code
    the daemon runs, so nothing asserted here can be true only of a stub.
    """

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def unpaired_connection() -> tuple[ExtensionConnection, FakeSocket]:
    """A restricted socket mid-pairing, as ``_open_pairing`` builds one."""
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id=PINNED_EXTENSION_ID,
        extension_version="1.239.0",
        paired=False,
        pairing_request_id="pair_test",
    )
    return conn, socket


def paired_connection() -> tuple[ExtensionConnection, FakeSocket]:
    """An authenticated socket, as the route builds one from a ``?token=``."""
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id=PINNED_EXTENSION_ID,
        extension_version="1.239.0",
        host_permission=True,
        paired=True,
    )
    return conn, socket


def ack_frame() -> str:
    """The ONE frame type a restricted socket may legitimately send."""
    return json.dumps(P.pairing_ack_frame("pair_test"))


# --------------------------------------------------------------------------- #
# An unauthenticated socket has a lifetime budget
# --------------------------------------------------------------------------- #


async def test_the_interleaved_flood_that_the_refusal_bound_forgives_forever():
    """Nine bad frames and one good one, repeated, used to run without a ceiling.

    This is the exact bypass: ``MAX_REFUSED_FRAMES`` counts CONSECUTIVE refusals
    and ``handle_raw`` resets the count on any readable object, so a caller holding
    no credential at all could write to ``daemon.log`` and burn loop time for as
    long as it cared to. The socket must end.
    """
    backend = ExtensionBackend()
    conn, socket = unpaired_connection()
    backend.register_restricted(conn)

    alive = 0
    for _ in range(MAX_UNPAIRED_FRAMES * 3):
        for _ in range(MAX_REFUSED_FRAMES - 1):
            if not await backend.handle_raw(conn, "{not json"):
                break
        else:
            if await backend.handle_raw(conn, ack_frame()):
                alive += 1
                continue
        break

    assert socket.closed_with == CLOSE_PROTOCOL_ERROR, (
        "an unpaired socket interleaving one readable frame every ninth refusal "
        "was never closed: the consecutive-refusal bound forgives it forever"
    )
    assert conn.frames_seen > MAX_UNPAIRED_FRAMES
    assert alive * MAX_REFUSED_FRAMES < conn.frames_seen
    assert str(MAX_UNPAIRED_FRAMES) in backend.last_error, (
        "the card must be able to say what the limit was"
    )


async def test_the_budget_survives_a_socket_that_only_ever_behaves():
    """A caller sending nothing but well-formed acks is bounded too.

    The refusal counter cannot see this one AT ALL -- every frame parses -- and the
    route's pairing deadline is consulted only on an idle tick, which a socket
    sending continuously never reaches.
    """
    backend = ExtensionBackend()
    conn, socket = unpaired_connection()
    backend.register_restricted(conn)

    for _ in range(MAX_UNPAIRED_FRAMES):
        assert await backend.handle_raw(conn, ack_frame()) is True
    assert socket.closed_with is None, "the bound must not bite before it is reached"

    assert await backend.handle_raw(conn, ack_frame()) is False
    assert socket.closed_with == CLOSE_PROTOCOL_ERROR


async def test_the_flood_close_is_1002_and_never_1008():
    """1008 makes the add-on delete its stored pairing token.

    A SECOND browser -- or any local process -- flooding this endpoint must not cost
    the user, whose own browser is paired, their credential. ``socket.ts``'s
    ``onClose`` reads 1008 as "this credential is refused".
    """
    backend = ExtensionBackend()
    conn, socket = unpaired_connection()
    for _ in range(MAX_UNPAIRED_FRAMES + 1):
        await backend.handle_raw(conn, ack_frame())

    assert socket.closed_with == CLOSE_PROTOCOL_ERROR == 1002
    assert socket.closed_with != 1008


async def test_the_flooding_socket_stops_offering_its_pairing_request():
    """A closed socket must not leave a Pair button pointing at nothing.

    ``restricted_socket`` is what ``POST /browser/pair`` resolves; leaving the entry
    would let the user press Pair and mint a credential for a socket that is gone.
    """
    backend = ExtensionBackend()
    conn, _socket = unpaired_connection()
    backend.register_restricted(conn)
    assert backend.restricted_socket("pair_test") is conn

    for _ in range(MAX_UNPAIRED_FRAMES + 1):
        await backend.handle_raw(conn, ack_frame())

    assert backend.restricted_socket("pair_test") is None


async def test_a_paired_add_on_is_not_bounded_by_the_pairing_budget():
    """The bound is on sockets with NO credential; a working add-on runs for hours.

    Without this case the cheapest way to pass the ones above is a counter on every
    connection, which would drop the user's real browser mid-session after twenty
    events -- a far worse bug than the flood it fixed.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    for _ in range(MAX_UNPAIRED_FRAMES * 5):
        assert await backend.handle_raw(
            conn, json.dumps(P.hello_frame("id", "1.239.0", True))
        )

    assert socket.closed_with is None
    assert backend.connected is True


async def test_the_budget_is_spent_before_the_frame_is_parsed():
    """A caller past its budget costs one comparison, not a 512 KB decode.

    The decode runs on the daemon's single event loop (v1.153.1). Proven by handing
    the exhausted socket a frame that CANNOT be parsed or measured without raising:
    an efficiency claim is invisible to an assertion, the refusal to touch it is not.
    """

    class Unreadable(str):
        def __len__(self) -> int:  # noqa: D105 - the assertion IS the behaviour
            raise AssertionError("the frame of an exhausted socket was measured")

        def encode(self, *args: Any, **kw: Any) -> bytes:  # noqa: D102
            raise AssertionError("the frame of an exhausted socket was encoded")

    backend = ExtensionBackend()
    conn, _socket = unpaired_connection()
    for _ in range(MAX_UNPAIRED_FRAMES):
        await backend.handle_raw(conn, ack_frame())

    assert await backend.handle_raw(conn, Unreadable("anything")) is False


async def test_which_of_the_two_bounds_bites_is_decided_by_whether_the_frames_parse():
    """Both bounds are live, they are LAYERED, and neither is redundant.

    A review pass read the lifetime budget as dead code. It is not: the two
    constants catch different traffic, and this case is what says which, because a
    constant whose comment describes a bound the reader never observes is the same
    defect as a bound that is not there. Measured here rather than asserted from
    the comment:

    * frames that never parse are refused, and the socket dies on frame
      ``MAX_REFUSED_FRAMES`` -- the lifetime budget is never reached;
    * frames that DO parse are invisible to the refusal counter (``handle_raw``
      resets it on any readable object), so they run to ``MAX_UNPAIRED_FRAMES``
      and the socket dies on the frame AFTER it.

    Delete either constant's check and one of the two halves below reads forever.
    """
    backend = ExtensionBackend()
    conn, socket = unpaired_connection()
    backend.register_restricted(conn)
    garbage = 0
    while await backend.handle_raw(conn, "{not json"):
        garbage += 1
        assert garbage < MAX_UNPAIRED_FRAMES, (
            "a socket sending nothing but unreadable frames outlived the refusal "
            "bound; MAX_REFUSED_FRAMES says it should not have"
        )
    garbage += 1

    assert garbage == MAX_REFUSED_FRAMES, (
        f"the refusal bound closed the socket on frame {garbage}, and the constant "
        f"says {MAX_REFUSED_FRAMES}"
    )
    assert conn.frames_seen == MAX_REFUSED_FRAMES < MAX_UNPAIRED_FRAMES
    assert socket.closed_with == CLOSE_PROTOCOL_ERROR
    assert str(MAX_REFUSED_FRAMES) in backend.last_error, (
        "the refusal bound must name its own count, not the other bound's"
    )

    backend = ExtensionBackend()
    conn, socket = unpaired_connection()
    backend.register_restricted(conn)
    readable = 0
    while await backend.handle_raw(conn, ack_frame()):
        readable += 1
        assert readable <= MAX_UNPAIRED_FRAMES * 2, "the lifetime budget never bit"
    readable += 1

    assert readable == MAX_UNPAIRED_FRAMES + 1, (
        f"a socket sending only parseable frames was closed on frame {readable}; "
        f"the constant says {MAX_UNPAIRED_FRAMES} are delivered and the next one "
        "closes it"
    )
    assert conn.refused_frames == 0, (
        "not one of these frames was refused, which is exactly why the refusal "
        "bound cannot see this caller"
    )
    assert socket.closed_with == CLOSE_PROTOCOL_ERROR
    assert str(MAX_UNPAIRED_FRAMES) in backend.last_error


# --------------------------------------------------------------------------- #
# The connection error ledger
# --------------------------------------------------------------------------- #


async def test_a_success_retires_the_error_line_and_never_the_history():
    """The whole reason the ledger exists.

    ``last_error`` means "the last thing that went wrong is still true", so a
    working round trip clears it -- and with only that one string, a browser that
    fails, recovers, and fails again leaves nothing behind for the user to show
    anyone. Driven through a REAL command round trip, because the clearing lives on
    the success branch of the response handler and a test that called ``note_error``
    and then read the list back would never reach it.
    """
    backend = ExtensionBackend()
    conn, socket = paired_connection()
    await backend.adopt(conn)

    await backend.handle_raw(conn, "{not json")
    assert "not JSON" in backend.last_error

    task = asyncio.ensure_future(
        backend.command(P.METHOD_LIST_TABS, timeout_s=HANG_GUARD_S)
    )
    sent: dict[str, Any] | None = None
    for _ in range(WAIT_TICKS):
        sent = next(
            (f for f in socket.frames if f.get("type") == P.FRAME_COMMAND), None
        )
        if sent is not None:
            break
        await asyncio.sleep(0)
    assert sent is not None, "the command frame never reached the socket"
    await backend.handle_frame(conn, P.response_frame(str(sent["id"]), {"tabs": []}))
    async with asyncio.timeout(HANG_GUARD_S):
        assert await task == {"tabs": []}

    assert backend.last_error == "", (
        "a successful round trip retires the line it disproves"
    )
    assert any("not JSON" in row["detail"] for row in backend.error_ledger()), (
        "and with it the only record that anything had gone wrong"
    )


async def test_a_repeated_fault_bumps_a_count_instead_of_taking_a_row():
    """Otherwise ten refused frames are ten identical rows and the window holds one."""
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    for _ in range(MAX_REFUSED_FRAMES - 1):
        await backend.handle_raw(conn, "{not json")

    ledger = backend.error_ledger()
    assert len(ledger) == 1, f"the same fault took {len(ledger)} rows"
    assert ledger[0]["count"] == MAX_REFUSED_FRAMES - 1
    assert ledger[0]["at"], "a ledger row without a time is not a record"


async def test_the_ledger_is_bounded_and_keeps_the_newest():
    """A bound, and the right half of the window: the fault that just happened."""
    backend = ExtensionBackend()
    for n in range(MAX_ERROR_LEDGER * 2):
        backend.note_error(f"distinct fault {n}")

    ledger = backend.error_ledger()
    assert len(ledger) == MAX_ERROR_LEDGER
    assert ledger[-1]["detail"] == f"distinct fault {MAX_ERROR_LEDGER * 2 - 1}"


async def test_an_unauthenticated_caller_cannot_size_the_string_the_daemon_keeps():
    """The ledger bounds how many rows it keeps; this bounds how big one can be.

    ``/browser/ws?pairing=1`` needs no credential by design, and ``handle_frame``
    quotes the frame's ``type`` back into the fault it records. Uncapped, that made
    the retained string a length the CALLER chose: twenty rows of a quarter-megabyte
    frame type is megabytes pinned for the life of the connection, the newest of
    them rendered on the card and in the Overview's browser row, and the same bytes
    written to ``daemon.log``. This is the Ship-1 pending-pairings bound applied to
    the size of a row instead of the number of them.

    Driven through ``handle_raw`` with a frame just inside ``MAX_FRAME_BYTES``, so
    the size check lets it past and the quoting path actually runs -- an oversized
    frame is refused unread and would prove nothing.
    """
    backend = ExtensionBackend()
    conn, _socket = unpaired_connection()
    backend.register_restricted(conn)

    filler = "A" * (P.MAX_FRAME_BYTES - 100)
    frame = json.dumps({"type": filler})
    assert len(frame) < P.MAX_FRAME_BYTES, "the frame must survive the size check"
    await backend.handle_raw(conn, frame)

    assert backend.last_error, "the fault was not recorded at all"
    assert len(backend.last_error) <= MAX_ERROR_DETAIL_CHARS, (
        f"the caller sized last_error at {len(backend.last_error)} characters, and "
        "that string is what the card and the Overview's browser row render"
    )
    rows = backend.error_ledger()
    assert rows, "the fault reached no ledger row"
    assert max(len(row["detail"]) for row in rows) <= MAX_ERROR_DETAIL_CHARS
    assert filler not in backend.last_error


async def test_the_whole_ledger_is_bounded_in_BYTES_and_not_only_in_ROWS():
    """Twenty rows of an uncapped detail is still a caller-chosen amount of memory.

    The row bound alone is not the defence: what a flooder pays for is
    ``MAX_ERROR_LEDGER`` times whatever it can make one detail weigh. Each frame
    here carries a DISTINCT prefix so every one takes its own row rather than
    bumping a count -- the worst case for retention, not the average one.
    """
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    for n in range(MAX_ERROR_LEDGER * 2):
        await backend.handle_raw(conn, json.dumps({"type": f"{n}-" + "A" * 8000}))

    rows = backend.error_ledger()
    retained = sum(len(row["detail"]) for row in rows)
    assert len(rows) == MAX_ERROR_LEDGER
    assert retained <= MAX_ERROR_LEDGER * MAX_ERROR_DETAIL_CHARS, (
        f"{retained} bytes of a caller's own text are pinned in daemon memory for "
        "the life of the connection; nothing clears this ledger"
    )


async def test_the_cap_is_invisible_to_a_real_fault_sentence():
    """A cap set low enough to truncate the daemon's own sentences is a new bug.

    The bound exists to stop a caller sizing the string, not to stop the transport
    explaining itself: the flood-close sentence is the longest this module writes,
    and the user reads it on the card. It must arrive whole, ellipsis-free.
    """
    backend = ExtensionBackend()
    conn, _socket = unpaired_connection()
    backend.register_restricted(conn)
    for _ in range(MAX_UNPAIRED_FRAMES + 1):
        await backend.handle_raw(conn, ack_frame())

    assert backend.last_error.endswith("so the connection was closed"), (
        f"the cap truncated the daemon's own explanation: {backend.last_error!r}"
    )
    assert str(MAX_UNPAIRED_FRAMES) in backend.last_error
    assert "..." not in backend.last_error


async def test_note_error_clips_whatever_it_is_handed():
    """The retention bound, at the ONE place that bounds it.

    Every consumer of the ledger and of ``last_error`` is downstream of this
    method, so a caller added later is covered without knowing the rule exists --
    which is the whole reason the clip is here and not only at the call sites.
    """
    backend = ExtensionBackend()
    backend.note_error("B" * (MAX_ERROR_DETAIL_CHARS * 20))

    assert len(backend.last_error) == MAX_ERROR_DETAIL_CHARS
    assert len(backend.error_ledger()[0]["detail"]) == MAX_ERROR_DETAIL_CHARS


async def test_the_quoting_sites_clip_before_the_string_is_ever_built():
    """Not the same claim as the one above, and it needs its own spy.

    ``note_error`` bounds what is RETAINED; it cannot stop a 512 KB f-string being
    assembled on the daemon's single event loop first, and building one per frame
    is the smaller relative of the v1.153.1 shape this module refuses oversized
    frames to avoid. The only way to see that is to watch what the call site HANDS
    ``note_error``, so the spy records its argument and the clip inside is bypassed.
    """
    backend = ExtensionBackend()
    conn, _socket = unpaired_connection()
    backend.register_restricted(conn)
    handed: list[str] = []

    def spy(*args: Any, **kw: Any) -> None:
        handed.append(str(args[0] if args else kw.get("detail", "")))

    backend.note_error = spy  # type: ignore[method-assign]
    await backend.handle_raw(
        conn, json.dumps({"type": "C" * (P.MAX_FRAME_BYTES - 100)})
    )

    assert handed, "the unpaired-frame refusal recorded nothing at all"
    assert len(handed[0]) <= MAX_ERROR_DETAIL_CHARS * 2, (
        f"the call site built a {len(handed[0])} character sentence on the event "
        "loop out of content the caller chose, and only then clipped it"
    )


def test_clip_detail_keeps_the_bound_it_advertises():
    """The helper both the retention path and the quoting sites depend on."""
    assert clip_detail("short") == "short"
    assert clip_detail("A" * MAX_ERROR_DETAIL_CHARS) == "A" * MAX_ERROR_DETAIL_CHARS
    clipped = clip_detail("A" * (MAX_ERROR_DETAIL_CHARS * 10))
    assert len(clipped) == MAX_ERROR_DETAIL_CHARS
    assert clipped.endswith("...")
    assert clip_detail(None) == ""


async def test_the_ledger_hands_out_copies():
    """The one structure here whose value is that it was not edited afterwards."""
    backend = ExtensionBackend()
    backend.note_error("the browser stopped answering")

    rows = backend.error_ledger()
    rows[0]["detail"] = "nothing happened"

    assert backend.error_ledger()[0]["detail"] == "the browser stopped answering"


async def test_an_empty_fault_is_not_a_row():
    """A blank string is not a fault, and a ledger padded with them says nothing."""
    backend = ExtensionBackend()
    backend.note_error("")
    backend.note_error("   ")

    assert backend.error_ledger() == []
    assert backend.last_error == ""


async def test_a_disconnect_that_carries_a_reason_is_recorded():
    """The one fault that passes through no frame handler.

    A browser that drops repeatedly is the case the ledger is for, and the drop
    itself is the evidence.
    """
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="error", detail="the socket died mid-command")

    details = [row["detail"] for row in backend.error_ledger()]
    assert any("the socket died mid-command" in d for d in details)
    assert any("error" in d for d in details), "the reason belongs in the record too"


async def test_an_ordinary_disconnect_is_not_recorded_twice():
    """``release`` runs twice for one socket on the ordinary path.

    Once from ``POST /browser/disconnect`` and again from the pump's ``finally``.
    Two rows for one event would make the ledger read as two drops.
    """
    backend = ExtensionBackend()
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="revoked", detail="the user pressed Forget")
    await backend.release(conn, reason="closed", detail="the socket ended")

    rows = [row for row in backend.error_ledger() if "disconnected" in row["detail"]]
    assert len(rows) == 1, f"one disconnect produced {len(rows)} rows"
    assert rows[0]["count"] == 1


async def test_the_transport_status_carries_the_history_it_kept():
    """The status ROUTE decides what to forward; the transport's duty is to have it."""
    backend = ExtensionBackend()
    backend.note_error("your browser sent a frame that is not JSON")

    view = backend.status()

    assert view["last_error"] == "your browser sent a frame that is not JSON"
    assert [row["detail"] for row in view["recent_errors"]] == [
        "your browser sent a frame that is not JSON"
    ]


def test_the_transport_says_where_a_user_reads_the_ledger():
    """A record kept and never forwarded is work shipped to nobody.

    ``recent_errors`` is offered on ``status()`` and TODAY no user surface reads it:
    ``GET /browser/status`` copies six named keys and this is not one of them, the
    card renders ``last_error`` alone, and the doctor's browser row reads
    ``last_error`` too. That is a route-level fix and the route is another lane's
    file this wave, so what this module owes the next reader is not to describe the
    ledger as something a user can see. The source pin is the honest form: the
    module must name the surface the ledger has to reach, so the reachability is a
    stated obligation rather than an assumption -- the trap this repo has been bitten
    by before (a green suite over a feature no user can reach).
    """
    module = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "iron_jarvis"
        / "browser"
        / "extension_backend.py"
    )
    # CRLF normalised at the READER, so the pin holds on either checkout.
    source = module.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))

    assert 'status()["recent_errors"]' in source, (
        "the module docstring must name the key the ledger is offered on"
    )
    assert "GET /browser/status" in source, (
        "and the surface that has to forward it before any user reads it"
    )
    assert "kept and" + chr(10) + "not forwarded is work shipped to nobody" in source, (
        "the docstring must not leave a reader believing the ledger already "
        "answers the user's question"
    )


async def test_a_broken_clock_cannot_break_a_fault_path():
    """A ledger row is diagnostics; recording one must never become the failure.

    The v1.229.0 lesson one layer down: the code that RECORDS a problem ran inside
    every ``except`` branch the daemon has, and an exception from it replaced a
    handled failure with an unhandled one.
    """

    def angry_clock(*args: Any, **kw: Any):
        raise RuntimeError("no clock here")

    backend = ExtensionBackend(clock=angry_clock)
    backend.note_error("the browser stopped answering")

    assert backend.last_error == "the browser stopped answering"
    assert backend.error_ledger()[0]["at"] == ""


# --------------------------------------------------------------------------- #
# The same flood, over the REAL route
# --------------------------------------------------------------------------- #


class _Deps:
    """The create_app deps object, reduced to the one field these routes read."""

    def __init__(self, runtime: Any) -> None:
        self.platform = type("_Platform", (), {"browser": runtime})()


class SocketApp:
    """The real ``/browser/ws`` over a real backend, ``PairingStore`` and ``Config``.

    No middleware: the credential decisions belong to
    ``tests/test_browser_auth_v1235.py``. What this case asserts is what the PUMP
    does with a socket it has already accepted, and the pump is the half a
    backend-level test cannot see.
    """

    def __init__(self, tmp_path) -> None:
        self.config = load_config(tmp_path)
        self.config.browser_access = ACCESS_INTERACTIVE
        self.engine = open_db(tmp_path / "browser-socket.db")
        self.store = PairingStore(self.engine)
        self.backend = ExtensionBackend()
        self.runtime = BrowserRuntime(
            backend=self.backend, config=self.config, pairing=self.store
        )
        self.app = FastAPI()
        browser_routes.register(self.app, _Deps(self.runtime))


def close_code_of(ws: Any, what: str) -> int:
    """The code the daemon closed an ACCEPTED socket with, read on a bounded reader.

    A ``TestClient`` websocket receive has no timeout of its own, so the read runs on
    a thread with a budget: a daemon that never closes fails this test BY NAME rather
    than parking the release gate forever.
    """
    seen: list[int] = []

    def read() -> None:
        try:
            while True:
                ws.receive_json()
        except WebSocketDisconnect as exc:
            seen.append(int(exc.code))
        except Exception:  # noqa: BLE001 - a torn-down app reads as "no close code"
            pass

    reader = threading.Thread(target=read, name="close-code-reader", daemon=True)
    reader.start()
    reader.join(WAIT_ATTEMPTS * WAIT_STEP_S)
    if not seen:
        raise AssertionError(f"the daemon never closed the socket: {what}")
    return seen[0]


def test_the_real_route_ends_an_unauthenticated_flood(tmp_path):
    """A caller reaches the bound through the pump, not only through the backend.

    Driven over the real ``/browser/ws?pairing=1`` -- the one endpoint that needs no
    credential -- with the real pairing store. The interleaving is the point: nine
    unreadable frames, then one readable ack, repeated. Without the lifetime budget
    the daemon reads this forever and the reader below fails by name.
    """
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            offer = ws.receive_json()
            assert offer["type"] == P.FRAME_PAIRING_REQUIRED
            request_id = offer["request_id"]

            try:
                for _ in range(MAX_UNPAIRED_FRAMES):
                    for _ in range(MAX_REFUSED_FRAMES - 1):
                        ws.send_text("{not json")
                    ws.send_json(P.pairing_ack_frame(request_id))
            except Exception:  # noqa: BLE001 - the daemon closing mid-flood is the pass
                pass

            code = close_code_of(ws, "an unauthenticated flood ran without a ceiling")

    assert code == CLOSE_PROTOCOL_ERROR
    assert app.backend.restricted_socket(request_id) is None
    assert str(MAX_UNPAIRED_FRAMES) in app.backend.last_error


def test_the_real_route_still_pairs_a_browser_that_behaves(tmp_path):
    """The ceiling must not be the attack.

    A bound that ended the ordinary handshake would be a worse bug than the flood,
    and the handshake is exactly one frame inside the budget.
    """
    app = SocketApp(tmp_path)
    with TestClient(app.app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        ) as ws:
            offer = ws.receive_json()
            request_id = offer["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))

            for _ in range(WAIT_ATTEMPTS):
                if app.backend.restricted_socket(request_id) is not None:
                    break
                time.sleep(WAIT_STEP_S)

            assert app.backend.restricted_socket(request_id) is not None, (
                "the socket that asked to pair must still be there when the user "
                "presses Pair"
            )


if __name__ == "__main__":  # pragma: no cover - convenience only
    pytest.main([__file__])
