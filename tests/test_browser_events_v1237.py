"""The five browser events: they fire, they say what they document, and they leak nothing (v1.237.0).

D22 and plan section 10.2. Five constants, one bus, no session id — browser facts
are ambient and belong to no conversation — and no second event system anywhere.
What this file protects is narrower and more specific than "the events work":

* **The DOCUMENTED payload is the pin, and the documentation is read from
  ``core/events.py`` rather than retyped here.** Each ``BROWSER_*`` constant in that
  file carries a ``{a, b, c}`` comment above it, which is what a reader of this
  codebase — and the dashboard author consuming the stream — treats as the contract.
  Retyping the key list into a test would let the two drift in exactly the way the
  comment exists to prevent, so :func:`documented_keys` parses the comment and the
  assertions compare against THAT. A key added to the comment and not to the code
  now fails, and so does the reverse.
* **``browser.disconnected`` names its REASON, and the word is derived.** Ship 1
  paid for this once: the outgoing socket's pump reaches its ``finally`` with the
  local default ``"closed"``, so a REPLACED browser published a second, false
  ``browser.disconnected {reason: "closed"}`` after the replacement's
  ``browser.connected``. Any consumer deriving connection state from the stream then
  rendered "not connected" over a working browser. That regression has a home in
  ``tests/test_browser_connection_v1235.py``; it is re-pinned here from the EVENT
  side, because this file is where someone adding a sixth event will look, and
  because the reason vocabulary is closed — a word outside it becomes ``closed``
  rather than inventing a state the card cannot render.
* **No payload carries the pairing token.** The token crosses the wire exactly once,
  in ``deliver_pairing``, which publishes nothing. The pin drives a real pairing,
  with a real token value, and then searches every published payload RECURSIVELY —
  keys and values, at any depth — because these payloads are persisted as
  ``EventRecord`` rows in SQLite, are read back into later prompts, and are included
  in the user's backups. One leak there is permanent.

Nothing here asserts a wall-clock duration, and every stand-in is a real object with
the methods the code under test calls rather than a mock. The bus is a recorder, so
ordering is asserted by comparing positions in a list.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import (
    DISCONNECT_REASONS,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_DOWNLOAD_COMPLETED,
    EVENT_NAVIGATION_COMPLETED,
    EVENT_TAB_ACTIVATED,
    ExtensionBackend,
    ExtensionConnection,
)
from iron_jarvis.core.events import EventType

EVENTS_PY = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "core" / "events.py"

#: The five, in the order plan 10.2 lists them, paired with the ``EventType``
#: attribute whose comment documents each one.
BROWSER_EVENTS: tuple[tuple[str, str], ...] = (
    ("BROWSER_CONNECTED", EVENT_CONNECTED),
    ("BROWSER_DISCONNECTED", EVENT_DISCONNECTED),
    ("BROWSER_TAB_ACTIVATED", EVENT_TAB_ACTIVATED),
    ("BROWSER_NAVIGATION_COMPLETED", EVENT_NAVIGATION_COMPLETED),
    ("BROWSER_DOWNLOAD_COMPLETED", EVENT_DOWNLOAD_COMPLETED),
)

#: A token shaped like the real thing, so a substring search for it is meaningful.
PAIRING_TOKEN = "ijbrowser_5f3c9a1e7d2b48c6a0e9f1b3c7d5a2e4"


def documented_keys(attribute: str) -> set[str]:
    """The payload keys ``core/events.py`` documents for one ``EventType`` constant.

    Read from the file rather than retyped into this test on purpose: the comment IS
    the contract a consumer reads, and a test carrying its own copy of the key list
    lets the comment and the code drift apart while staying green — which is the
    failure the comment convention exists to prevent.

    The comment block is walked UPWARD from the constant's own line and stops at the
    first non-comment line, so a neighbouring constant's payload can never be read as
    this one's. CRLF is normalised once, at the reader.
    """
    source = EVENTS_PY.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    index = next(
        (n for n, line in enumerate(source) if line.strip().startswith(f"{attribute} = ")),
        -1,
    )
    assert index != -1, f"core/events.py declares no {attribute}"
    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and source[cursor].strip().startswith("#"):
        block.insert(0, source[cursor].strip().lstrip("#").strip())
        cursor -= 1
    braced = next((line for line in reversed(block) if "{" in line), "")
    assert braced, f"{attribute} carries no documented payload comment"
    inner = braced[braced.index("{") + 1 : braced.index("}")]
    return {part.strip() for part in inner.split(",") if part.strip()}


class RecordingBus:
    """Every publish, in order. The event stream is the thing under test."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict, Any]] = []

    async def publish(self, name, payload=None, session_id=None):
        self.published.append((name, dict(payload or {}), session_id))

    def of(self, name: str) -> list[dict]:
        return [payload for published, payload, _ in self.published if published == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.published]


class FakeSocket:
    """A real object with the two methods ``ExtensionConnection`` calls. Not a mock."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def paired_connection(**kw: Any) -> tuple[ExtensionConnection, FakeSocket]:
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id="lgihfomaieifpnemakmpadmggjnoojmm",
        extension_version="1.237.0",
        host_permission=True,
        paired=True,
        **kw,
    )
    return conn, socket


def backend_with(bus: RecordingBus, *, access: str = "interactive") -> ExtensionBackend:
    """A backend wired the way ``BrowserRuntime`` wires the real one.

    The access READER matters: ``browser.connected`` documents an ``access`` key, and
    a backend built with no reader omits it — which is right (saying "off" on a hunch
    would tell every consumer the user had switched the capability off) and is not
    the shape the running daemon has.
    """
    return ExtensionBackend(event_bus=bus, access_reader=lambda: access)


def strings_in(value: Any) -> list[str]:
    """Every string anywhere inside ``value``, keys included."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key))
            found.extend(strings_in(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(strings_in(item))
    else:
        found.append(str(value))
    return found


# --------------------------------------------------------------------------- #
# The five exist, and are the five
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("attribute,name", BROWSER_EVENTS)
def test_each_event_is_a_real_event_type_constant(attribute: str, name: str):
    """The module publishes under ``EventType``'s own name, not a literal it typed.

    ``extension_backend`` reads each constant through ``getattr`` with the literal as
    a fallback, so that it can publish the right NAME whether or not the coordinator's
    ``core/events.py`` edit has landed. The fallback is a build-order accommodation,
    not a second source of truth — once the constant exists, the two must agree, or
    the daemon publishes under a name nothing subscribes to and the event is
    invisible.
    """
    assert getattr(EventType, attribute) == name


def test_the_names_follow_the_domain_past_tense_convention():
    for _attribute, name in BROWSER_EVENTS:
        assert name.startswith("browser."), f"{name} is not in the browser domain"
        assert re.fullmatch(r"browser\.[a-z_]+", name), f"{name} is not <domain>.<verb>"


# --------------------------------------------------------------------------- #
# They fire, with the payloads they document
# --------------------------------------------------------------------------- #


async def test_browser_connected_fires_with_its_documented_payload():
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()

    await backend.adopt(conn)

    payload = bus.of("browser.connected")[0]
    missing = documented_keys("BROWSER_CONNECTED") - set(payload)
    assert not missing, (
        f"core/events.py documents {sorted(missing)} on browser.connected and the "
        "daemon does not publish them; a consumer reads that comment as the contract"
    )
    assert payload["extension_id"] == "lgihfomaieifpnemakmpadmggjnoojmm"
    assert payload["host_permission"] is True
    assert payload["access"] == "interactive"


async def test_the_access_word_is_omitted_rather_than_guessed():
    """A backend with no access reader must not report ``off``.

    ``off`` on a hunch tells every consumer the user switched the capability off,
    which is the same class of lie as the empty-string tab title Ship 1 replaced
    with ``null``.
    """
    bus = RecordingBus()
    backend = ExtensionBackend(event_bus=bus)
    conn, _socket = paired_connection()

    await backend.adopt(conn)

    assert "access" not in bus.of("browser.connected")[0]


async def test_the_two_connected_publish_sites_describe_the_browser_the_same_way():
    """``adopt`` publishes one; a contradicting ``hello`` publishes another.

    They held separate copies of the payload dict, so a key added to one was a key
    the other silently lacked — and a consumer reading the stream saw the same
    browser described two different ways.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    socket = FakeSocket()
    conn = ExtensionConnection(socket, paired=True)
    await backend.adopt(conn)

    await backend.handle_frame(
        conn, P.hello_frame("lgihfomaieifpnemakmpadmggjnoojmm", "1.237.0", True)
    )

    announcements = bus.of("browser.connected")
    assert len(announcements) == 2, f"expected adoption then hello, got {bus.names()}"
    assert set(announcements[0]) == set(announcements[1])


async def test_browser_disconnected_fires_with_its_documented_payload():
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="revoked", detail="pairing forgotten")

    payload = bus.of("browser.disconnected")[0]
    assert not documented_keys("BROWSER_DISCONNECTED") - set(payload)
    assert payload == {"reason": "revoked", "detail": "pairing forgotten"}


async def test_browser_tab_activated_fires_with_its_documented_payload():
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.handle_frame(
        conn,
        P.event_frame(
            "evt_1",
            P.EVENT_TAB_ACTIVATED,
            {"tab_id": 42, "title": "IRS", "url": "https://irs.gov/"},
        ),
    )

    payload = bus.of("browser.tab_activated")[0]
    missing = documented_keys("BROWSER_TAB_ACTIVATED") - set(payload)
    assert not missing, f"the add-on's tab_activated did not survive with {sorted(missing)}"
    assert payload["tab_id"] == 42 and payload["title"] == "IRS"


async def test_browser_navigation_completed_fires_and_survives_the_daemons_merge():
    """The daemon caches and invalidates around this one; the PAYLOAD passes through.

    ``_handle_event`` merges the navigation onto its active-tab cache and drops the
    stale keys the new document did not restate. That is about the CACHE. What the
    bus sees must still be what the add-on reported, or a consumer of the stream and
    the ambient block disagree about the same navigation.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)
    reported = {"tab_id": 42, "url": "https://irs.gov/forms", "title": "Forms", "page_version": 3}

    await backend.handle_frame(
        conn, P.event_frame("evt_2", P.EVENT_NAVIGATION_COMPLETED, dict(reported))
    )

    assert bus.of("browser.navigation_completed") == [reported]


async def test_browser_download_completed_fires_with_its_documented_payload():
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.handle_frame(
        conn,
        P.event_frame(
            "evt_3",
            P.EVENT_DOWNLOAD_COMPLETED,
            {
                "download_id": 7,
                "filename": "/home/vr/Downloads/statement.pdf",
                "source_url": "https://bank.example/statement",
                "tab_id": 42,
                "bytes": 51_200,
                "mime": "application/pdf",
            },
        ),
    )

    payload = bus.of("browser.download_completed")[0]
    missing = documented_keys("BROWSER_DOWNLOAD_COMPLETED") - set(payload)
    assert not missing, (
        f"core/events.py documents {sorted(missing)} on browser.download_completed "
        "and the daemon does not publish them"
    )
    assert payload["local_path"] == "/home/vr/Downloads/statement.pdf", (
        "local_path is the DAEMON's word for a path it verified; without it the file "
        "tools have nothing to open"
    )


async def test_all_five_fire_in_one_browser_lifetime():
    """One connection, one of everything — the pin that catches a route never wired.

    Each case above drives its own event; this one proves they coexist, in order, on
    ONE bus (D22: no second browser-only event system), and that nothing publishes a
    session id — browser facts are ambient and belong to no conversation.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()

    await backend.adopt(conn)
    await backend.handle_frame(
        conn, P.event_frame("evt_1", P.EVENT_TAB_ACTIVATED, {"tab_id": 1, "title": "a", "url": "u"})
    )
    await backend.handle_frame(
        conn,
        P.event_frame(
            "evt_2",
            P.EVENT_NAVIGATION_COMPLETED,
            {"tab_id": 1, "url": "u2", "title": "b", "page_version": 2},
        ),
    )
    await backend.handle_frame(
        conn,
        P.event_frame(
            "evt_3",
            P.EVENT_DOWNLOAD_COMPLETED,
            {"download_id": 1, "filename": "/tmp/a.pdf", "source_url": "u"},
        ),
    )
    await backend.release(conn, reason="closed", detail="")

    assert bus.names() == [
        EVENT_CONNECTED,
        EVENT_TAB_ACTIVATED,
        EVENT_NAVIGATION_COMPLETED,
        EVENT_DOWNLOAD_COMPLETED,
        EVENT_DISCONNECTED,
    ], f"one browser lifetime did not produce all five events in order: {bus.names()}"
    assert set(bus.names()) == {name for _attribute, name in BROWSER_EVENTS}
    assert all(session_id is None for _n, _p, session_id in bus.published), (
        "a browser event carried a session id. These are AMBIENT facts about a "
        "browser; tagging one to a conversation makes it disappear from every other"
    )


# --------------------------------------------------------------------------- #
# The disconnect reason
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reason", DISCONNECT_REASONS)
async def test_every_reason_in_the_vocabulary_survives_to_the_event(reason: str):
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason=reason, detail="")

    assert bus.of("browser.disconnected")[0]["reason"] == reason


async def test_a_word_outside_the_vocabulary_becomes_closed():
    """The vocabulary is closed, so a typo cannot invent a state the card cannot read."""
    bus = RecordingBus()
    backend = backend_with(bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)

    await backend.release(conn, reason="went-away-i-guess", detail="")

    assert bus.of("browser.disconnected")[0]["reason"] == "closed"


async def test_a_replaced_browser_is_never_reported_as_one_the_user_closed():
    """Ship 1's regression, re-pinned from the EVENT side. Do not let it come back.

    The outgoing socket's own pump calls ``release`` with the local default
    ``"closed"`` moments after the replacement announced itself. Taking the caller's
    word produced a SECOND, false ``browser.disconnected {reason: "closed"}`` after
    the new socket's ``browser.connected`` — so the ledger blamed the user for a
    browser that was replaced, and the last event in the stream said "not connected"
    over a browser that was working.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    first, _first_socket = paired_connection()
    await backend.adopt(first)
    second, _second_socket = paired_connection()

    await backend.adopt(second)
    await backend.release(first, reason="closed", detail="")

    disconnects = bus.of("browser.disconnected")
    assert len(disconnects) == 1, (
        f"a replacement published {len(disconnects)} disconnects for one real "
        f"disconnect. Stream: {bus.names()}"
    )
    assert disconnects[0]["reason"] == "replaced"
    assert disconnects[0]["detail"], "a replaced browser must say what replaced it"
    assert bus.names()[-1] == "browser.connected"


async def test_every_disconnect_reason_the_daemon_can_publish_is_in_the_vocabulary():
    """The word is DERIVED, never a free-form string typed at a publish site.

    The same rule ``providers/router.failure_reason`` exists for: a reason typed at
    the call site is a reason that will eventually be wrong, and this one is read as
    the authoritative account of why a browser went away.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    for reason in ("closed", "replaced", "revoked", "error", "", "nonsense"):
        conn, _socket = paired_connection()
        await backend.adopt(conn)
        await backend.release(conn, reason=reason, detail="")

    published = bus.of("browser.disconnected")
    assert published, "no disconnect was published at all"
    assert all(payload["reason"] in DISCONNECT_REASONS for payload in published)


# --------------------------------------------------------------------------- #
# No payload carries a credential
# --------------------------------------------------------------------------- #


async def test_no_event_payload_carries_the_pairing_token():
    """These payloads are persisted, backed up and read back. One leak is permanent.

    Driven through a REAL pairing — ``deliver_pairing`` is the token's one appearance
    on the wire — and then searched recursively, keys and values at any depth, rather
    than by checking for a key literally named ``token``.
    """
    bus = RecordingBus()
    backend = backend_with(bus)
    socket = FakeSocket()
    restricted = ExtensionConnection(socket, pairing_request_id="pair_abc")
    backend.register_restricted(restricted)

    await backend.deliver_pairing("pair_abc", PAIRING_TOKEN)
    await backend.handle_frame(
        restricted,
        P.event_frame(
            "evt_1",
            P.EVENT_DOWNLOAD_COMPLETED,
            {"download_id": 1, "filename": "/tmp/a.pdf", "source_url": "u"},
        ),
    )
    await backend.release(restricted, reason="closed", detail="")

    assert any(name == "browser.connected" for name, _p, _s in bus.published), (
        "the pairing never adopted the socket, so this pin proved nothing"
    )
    for name, payload, _session in bus.published:
        for text in strings_in(payload):
            assert PAIRING_TOKEN not in text, f"{name} carries the pairing token"
            assert "token" not in text.lower(), (
                f"{name} carries a token-shaped key or value: {text!r}"
            )
    # And the token DID cross the socket, so the search above was looking at a
    # lifetime where a leak was possible rather than at one where nothing happened.
    assert any(PAIRING_TOKEN in str(frame) for frame in socket.frames), (
        "the token never reached the add-on; the pairing frame is where it belongs"
    )
