"""v1.259.0 — the add-on says WHICH browser it is; the daemon keeps and repeats it.

Plain words: Chrome and Edge load the same add-on with the same identity, so
Iron Jarvis could not tell an Edge user from a Chrome one — and every setup
step was written for Chrome. Now ``browser.hello`` may carry
``browser: {name, version}``; the daemon records it, the Browser card prints it,
and the doctor names the browser actually installed on this PC.

Every claim here is about the DAEMON's behaviour, driven through the same
``adopt`` + ``handle_frame`` path the socket route uses. Absence is "unknown",
never a guess; a hostile value is bounded; nothing here touches a browser.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.daemon.app import create_app
from iron_jarvis.onboarding.doctor import _browser_label

PINNED = "lgihfomaieifpnemakmpadmggjnoojmm"


class FakeSocket:
    """``ExtensionConnection`` calls only ``send_json`` and ``close``."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def paired() -> tuple[ExtensionConnection, FakeSocket]:
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id=PINNED,
        extension_version="1.259.0",
        host_permission=True,
        paired=True,
    )
    return conn, socket


def hello(**extra: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": P.FRAME_HELLO,
        "extension_id": PINNED,
        "extension_version": "1.259.0",
        "host_permission": True,
    }
    frame.update(extra)
    return frame


async def test_hello_records_the_browser_and_status_and_payload_both_say_it():
    backend = ExtensionBackend()
    conn, _ = paired()
    await backend.adopt(conn)

    await backend.handle_frame(
        conn, hello(browser={"name": "Microsoft Edge", "version": "153"})
    )

    view = backend.status()
    assert view["browser_name"] == "Microsoft Edge"
    assert view["browser_version"] == "153"
    # ONE definition of the connected payload — the event stream describes the
    # same browser the status view does.
    payload = backend.connected_payload(conn)
    assert payload["browser_name"] == "Microsoft Edge"
    assert payload["browser_version"] == "153"


async def test_a_hello_without_the_key_is_unknown_never_chrome():
    backend = ExtensionBackend()
    conn, _ = paired()
    await backend.adopt(conn)

    await backend.handle_frame(conn, hello())

    view = backend.status()
    assert view["browser_name"] == ""
    assert view["browser_version"] == ""


async def test_a_later_hello_without_the_key_keeps_what_was_known():
    """An older add-on reloaded mid-session must not blank a fact already learned."""
    backend = ExtensionBackend()
    conn, _ = paired()
    await backend.adopt(conn)
    await backend.handle_frame(conn, hello(browser={"name": "Microsoft Edge", "version": "153"}))

    await backend.handle_frame(conn, hello())  # re-announce, no browser key

    assert backend.status()["browser_name"] == "Microsoft Edge"


async def test_a_hostile_browser_value_is_bounded_and_a_wrong_shape_is_ignored():
    backend = ExtensionBackend()
    conn, _ = paired()
    await backend.adopt(conn)

    await backend.handle_frame(conn, hello(browser="Microsoft Edge"))  # not a dict
    assert backend.status()["browser_name"] == ""

    await backend.handle_frame(conn, hello(browser={"name": "x" * 500, "version": "y" * 500}))
    view = backend.status()
    assert len(view["browser_name"]) == 64
    assert len(view["browser_version"]) == 32

    await backend.handle_frame(conn, hello(browser={"name": "   ", "version": "1"}))  # blank name
    assert view["browser_name"] == "x" * 64, "a blank name does not overwrite a known one"


def test_the_status_route_spells_the_keys_in_the_disconnected_shape(tmp_path):
    """A key that exists only when connected makes every reader write a second branch."""
    client = TestClient(create_app(str(tmp_path)))
    body = client.get("/browser/status").json()
    assert body["connected"] is False
    assert body["browser_name"] == ""
    assert body["browser_version"] == ""


def test_the_doctor_names_the_browser_it_found():
    assert _browser_label(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") == "Microsoft Edge"
    assert _browser_label(r"C:\Program Files\Google\Chrome\Application\chrome.exe") == "Google Chrome"
    assert _browser_label("/usr/bin/google-chrome-stable") == "Google Chrome"
    assert _browser_label("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge") == "Microsoft Edge"
    assert _browser_label("/usr/bin/chromium") == "Chromium-based browser"
    assert _browser_label("") == "Chromium-based browser"
