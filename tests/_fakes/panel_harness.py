"""The side panel's test harness (v1.262.0) — LIFTED from
``tests/test_browser_panel_v1242.py`` so a second test file can drive the REAL
app, the REAL pairing socket and the REAL chat lane the same way. The v1242
file keeps its own copy on purpose (its cases are pinned against it); this one
serves ``test_browser_agent_v1262.py``. Every stand-in takes ``*args, **kw``.
No wall-clock assertions: every wait is a bounded budget that fails BY NAME.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.identity import extension_origin
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

INSTALL_BEARER = "panel-install-bearer"
LOOPBACK_ORIGIN = "http://127.0.0.1:8788"

#: Attempts, and the seconds each waits, for something the daemon must do. A
#: BUDGET, not a deadline: every assertion that follows names the thing that
#: never happened, and nothing asserts an elapsed duration.
WAIT_ATTEMPTS = 400
WAIT_STEP_S = 0.02

#: How long a reader listens for a frame it expects NEVER to arrive.
QUIET_BUDGET_S = 2.0

#: A sentence the autoselect pass scores the browser READ tools on, so the
#: two-round doubles below have a tool call a panel turn may actually make.
#: It has to be browser-shaped: a panel turn's ceiling is the ``browser_*``
#: family and nothing else (``browser/panel.py``), so "read my notes" — which
#: this file used before v1.242.0's F1 fix — now arms nothing at all, and a
#: turn with no tools has no round boundary for a stop or a steer to land on.
_BROWSER_SENTENCE = "read the open tab and summarise the page"

_FIRST = "first-piece-of-the-answer"
_SECOND = "second-piece-after-the-stop"

#: Round 1's billed usage — the tokens a stop must not lose from the ledger.
_R1_IN, _R1_OUT = 71, 17


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INSTALL_BEARER}", "Origin": LOOPBACK_ORIGIN}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
class Gate:
    """A release the app loop waits on and the TEST THREAD trips.

    ``asyncio.Event.set()`` is not thread-safe and the test thread is not the
    app's loop thread, so this is a ``threading.Event`` the coroutine polls with
    ``asyncio.sleep`` — which yields the loop, so the socket pump keeps reading
    while a round is parked here. That is the whole point: the stop and the
    steer under test have to arrive WHILE the turn is genuinely mid-flight.

    Bounded, and the bound fails BY NAME rather than parking CI forever.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def release(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        for _ in range(WAIT_ATTEMPTS):
            if self._event.is_set():
                return
            await asyncio.sleep(WAIT_STEP_S)
        raise AssertionError("the test never released the gate")


class PanelSocket:
    """A paired add-on socket with a reader thread, speaking panel frames.

    The reader is a thread because a ``TestClient`` websocket receive has no
    timeout of its own: a case whose claim is "this frame never arrives" would
    otherwise park the gate forever, which is indistinguishable from a slow
    runner.
    """

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.frames: list[dict] = []
        self._q: queue.Queue = queue.Queue()
        self._reader = threading.Thread(
            target=self._read, name="panel-frame-reader", daemon=True
        )
        self._reader.start()

    def _read(self) -> None:
        try:
            while True:
                self._q.put(self.ws.receive_json())
        except Exception:  # noqa: BLE001 — a torn-down app reads as "no more frames"
            self._q.put(None)

    def send(self, action: str, **params: Any) -> None:
        self.ws.send_json(P.panel_frame(action, params))

    def _drain(self) -> None:
        while True:
            try:
                frame = self._q.get_nowait()
            except queue.Empty:
                return
            if frame is None:
                return
            self.frames.append(frame)

    def events(self) -> list[tuple[str, dict]]:
        """Every ``browser.panel_event`` seen so far, as ``(event, payload)``."""
        self._drain()
        return [
            (str(f.get("event") or ""), dict(f.get("payload") or {}))
            for f in self.frames
            if f.get("type") == P.FRAME_PANEL_EVENT
        ]

    def wait_for(self, event: str, what: str, *, nth: int = 1) -> dict:
        """The ``nth`` ``event`` payload, inside a budget. Fails by name."""
        for _ in range(WAIT_ATTEMPTS):
            matches = [p for name, p in self.events() if name == event]
            if len(matches) >= nth:
                return matches[nth - 1]
            try:
                frame = self._q.get(timeout=WAIT_STEP_S)
            except queue.Empty:
                continue
            if frame is None:
                break
            self.frames.append(frame)
        raise AssertionError(f"{what} (events seen: {[e for e, _ in self.events()]})")

    def barrier(self, what: str) -> None:
        """Wait until every frame sent so far has been PROCESSED by the daemon.

        Frames on one socket are ordered, so an ``open`` sent after the frame
        under test is answered only once that frame has been handled. Without
        this a case would race its own release: the turn parks on a gate the
        test trips from another thread, and "did the stop/steer arrive first?"
        would be decided by whichever coroutine the loop happened to resume.
        NOT a sleep and NOT a duration — a round trip.
        """
        seen = len([e for e, _ in self.events() if e == P.PANEL_EVENT_STATE])
        self.send(P.PANEL_ACTION_OPEN)
        self.wait_for(P.PANEL_EVENT_STATE, what, nth=seen + 1)

    def quiet(self) -> list[tuple[str, dict]]:
        """Everything the socket volunteers inside a short budget, then stop.

        For the cases whose claim is that something NEVER arrives.
        """
        deadline = threading.Event()
        threading.Timer(QUIET_BUDGET_S, deadline.set).start()
        while not deadline.is_set():
            try:
                frame = self._q.get(timeout=WAIT_STEP_S)
            except queue.Empty:
                continue
            if frame is None:
                break
            self.frames.append(frame)
        return self.events()


def _wait_for(predicate, what: str) -> None:
    """Poll a daemon-side fact. A budget, not a timing assertion."""
    for _ in range(WAIT_ATTEMPTS):
        if predicate():
            return
        threading.Event().wait(WAIT_STEP_S)
    raise AssertionError(what)


class RealApp:
    """The REAL ``create_app`` with install auth on, and a paired browser.

    Used as a context manager so the socket, the client and the app tear down
    in the order Starlette expects.
    """

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        access: str = "interactive",
    ) -> None:
        monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_BEARER)
        self.app = create_app(str(tmp_path / "home"))
        self.platform = self.app.state.platform
        self.platform.config.browser_access = access
        self._client_cm = TestClient(self.app)
        self._ws_cm: Any = None
        self.client: Any = None
        self.panel: PanelSocket | None = None

    def __enter__(self) -> "RealApp":
        self.client = self._client_cm.__enter__()
        self._ws_cm = self.client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": extension_origin()}
        )
        ws = self._ws_cm.__enter__()
        self.ws = ws
        offer = ws.receive_json()
        assert offer["type"] == P.FRAME_PAIRING_REQUIRED, offer
        self.request_id = offer["request_id"]
        ws.send_json(P.pairing_ack_frame(self.request_id))
        backend = self.platform.browser.backend
        _wait_for(
            lambda: backend.restricted_socket(self.request_id) is not None,
            "the daemon never registered the restricted socket",
        )
        return self

    def pair(self) -> None:
        """The user's Pair press — the one thing no automation may do for them."""
        r = self.client.post(
            "/browser/pair", headers=_headers(), json={"request_id": self.request_id}
        )
        assert r.status_code == 200, r.text
        backend = self.platform.browser.backend
        _wait_for(lambda: backend.connected, "the socket never became authoritative")
        # Drain the handshake frames (browser.paired, browser.ready) before the
        # panel reader starts, so a case reading panel events reads only those.
        for _ in range(2):
            try:
                self.ws.receive_json()
            except Exception:  # noqa: BLE001
                break
        self.panel = PanelSocket(self.ws)

    def __exit__(self, *exc: Any) -> None:
        try:
            self._ws_cm.__exit__(*exc)
        except Exception:  # noqa: BLE001 — a socket the daemon already closed
            pass
        self._client_cm.__exit__(*exc)

    # --- turn doubles --------------------------------------------------

    def one_round(self, text: str, gate: Gate | None = None) -> None:
        """A turn that answers in one round: two deltas, then it is done."""

        async def fake_stream(*args: Any, **kw: Any):
            yield {"type": "text", "text": text}
            if gate is not None:
                await gate.wait()
            yield {
                "type": "final",
                "response": LLMResponse(text=text, usage={}),
                "provider": "mock",
                "model": "mock",
            }

        self.platform.router.stream = fake_stream

    def two_rounds(self, gate: Gate, seen_messages: list | None = None):
        """Round 1 bills, streams ``_FIRST`` and PARKS on the gate, then calls a
        tool; round 2 streams ``_SECOND`` and finishes.

        ``_SECOND`` reaching the panel is proof a stop did NOT halt the turn,
        and its absence is proof it did. ``seen_messages`` records each round's
        message list, which is how a steer injection is proven to have landed
        in the conversation rather than merely been acknowledged.
        """
        rounds = {"n": 0}

        async def fake_stream(*args: Any, **kw: Any):
            if seen_messages is not None:
                seen_messages.append(list(kw.get("messages") or []))
            if rounds["n"] == 0:
                rounds["n"] += 1
                yield {"type": "text", "text": _FIRST}
                await gate.wait()
                yield {
                    "type": "final",
                    "response": LLMResponse(
                        text=_FIRST,
                        tool_calls=[
                            ToolCall(id="c1", name="browser_read_page",
                                     arguments={})
                        ],
                        usage={"input_tokens": _R1_IN, "output_tokens": _R1_OUT},
                    ),
                    "provider": "mock",
                    "model": "mock",
                }
            else:
                yield {"type": "text", "text": _SECOND}
                yield {
                    "type": "final",
                    "response": LLMResponse(text=_SECOND, usage={}),
                    "provider": "mock",
                    "model": "mock",
                }

        self.platform.router.stream = fake_stream
        return fake_stream

    def billed_then_parks(self, gate: Gate) -> None:
        """Round 1 BILLS and calls a tool; round 2 streams ``_FIRST``, PARKS on
        the gate, and only then offers ``_SECOND``.

        The parking window is where the stop lands, and the shape matters: a
        stop before round 1's ``final`` would leave nothing billed and the
        CANCELLED row assertion would pass over an empty ledger for the wrong
        reason. Here the provider has ALREADY charged for round 1 when the stop
        arrives, so the ledger claim is about a real row.
        """
        rounds = {"n": 0}

        async def fake_stream(*args: Any, **kw: Any):
            if rounds["n"] == 0:
                rounds["n"] += 1
                yield {
                    "type": "final",
                    "response": LLMResponse(
                        text="",
                        tool_calls=[
                            ToolCall(id="c1", name="browser_read_page",
                                     arguments={})
                        ],
                        usage={"input_tokens": _R1_IN, "output_tokens": _R1_OUT},
                    ),
                    "provider": "mock",
                    "model": "mock",
                }
            else:
                yield {"type": "text", "text": _FIRST}
                await gate.wait()
                yield {"type": "text", "text": _SECOND}
                yield {
                    "type": "final",
                    "response": LLMResponse(text=_FIRST + _SECOND, usage={}),
                    "provider": "mock",
                    "model": "mock",
                }

        self.platform.router.stream = fake_stream

    def records_the_offer(self) -> list[dict]:
        """Answer nothing, but KEEP what the model was handed.

        The armed list is not observable from outside the turn — the ``done``
        frame reports ``auto_armed`` and the panel does not forward it — so the
        assertion has to be made where the offer is made: ``tools=tool_specs``
        is the kwarg the router receives, and it is literally the menu the
        model may call from.
        """
        seen: list[dict] = []

        async def fake_stream(*args: Any, **kw: Any):
            seen.append(dict(kw))
            yield {"type": "text", "text": "nothing to do."}
            yield {
                "type": "final",
                "response": LLMResponse(text="nothing to do.", usage={}),
                "provider": "mock",
                "model": "mock",
            }

        self.platform.router.stream = fake_stream
        return seen

    def stub_tools(self) -> list[dict]:
        """Keep every tool call deterministic and off the filesystem, and RECORD
        what it was invoked with — the deny path's whole proof is a keyword."""
        calls: list[dict] = []

        async def fake_invoke(*args: Any, **kw: Any):
            calls.append({"args": args, "kw": kw})
            reason = kw.get("deny_reason") or ""
            if reason:
                return ToolResult(ok=False, output="", error=reason)
            return ToolResult(ok=True, output="ok")

        self.platform.registry.invoke = fake_invoke
        return calls


