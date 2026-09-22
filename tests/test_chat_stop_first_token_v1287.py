"""Stop reaches a turn whose model has not sent its first word yet (chat-06).

THE SILENT FAILURE THIS FILE GUARDS: the side panel's Stop (and closing the
panel, and ``POST /chat/turns/{id}/stop``) doing nothing while Jarvis is
"thinking". A named turn's Stop only set a flag, and the streaming lane read
that flag once per model FRAME — so while the model had not produced its
first one (a subscription CLI answers in one chunk after the whole reply; a
local model loads into memory first) nothing read it. The turn kept running
and billing while the panel said it had stopped. The chat page never showed
this only because it also drops its connection, which Starlette turns into a
cancel; a panel turn owns no connection to drop.

Every case parks the model BEFORE its first frame of round 2, on a gate that
only a generous budget ever opens, and records HOW the park ended:

* ``"cancelled"`` — Stop reached the model's pending await and cancelled it
  (the fix: the frame is raced against ``TurnHandle.wait_stopped``).
* ``"released"`` — nothing interrupted the model; the budget ran out and it
  answered. That is the old behaviour, and it still ends CANCELLED in the
  ledger, because the per-frame check finally sees the flag — which is why
  the ledger row alone could never tell the two apart.

NO WALL-CLOCK ASSERTIONS: the budget is a safety valve, and every assertion
is about which of those two things happened.

Round 1 always BILLS first (one tool call), so the CANCELLED row asserted
here is a real row holding real tokens, not an empty ledger passing for the
wrong reason.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.core.models import AgentState
from iron_jarvis.core.turns import TURNS, TurnHandle
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_browser_panel_v1242 import (
    _BROWSER_SENTENCE,
    RealApp,
    _wait_for,
)
from tests.test_chat_turn_stop_v1241 import (
    _arm_fake_tool,
    _asgi_post,
    _chat_runs,
    _drive_stream,
)

#: Round 1's billed usage — the tokens a stop must not lose from the ledger.
_R1_IN, _R1_OUT = 61, 19

#: The safety valve: how long the parked model waits before answering on its
#: own. Only the OLD code ever reaches it. A budget, never an assertion.
_PARK_BUDGET_S = 8.0

_LATE = "late-answer-nobody-should-see"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


class ParkedModel:
    """Round 1 bills and calls ``tool``; round 2 PARKS before its first frame.

    ``parked`` is a ``threading.Event`` so either a coroutine (polling) or a
    test thread can wait for the moment the turn is genuinely inside the
    model's first await. ``outcome`` records how the park ended; ``closed``
    records that the generator itself was unwound (the provider's own cleanup
    — closing its HTTP stream, killing a CLI — lives exactly there).
    """

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.parked = threading.Event()
        self.outcome: list[str] = []
        self.closed = threading.Event()
        self._rounds = 0

    async def stream(self, *args: Any, **kw: Any):
        if self._rounds == 0:
            self._rounds += 1
            yield {
                "type": "final",
                "response": LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name=self.tool, arguments={})],
                    usage={"input_tokens": _R1_IN, "output_tokens": _R1_OUT},
                ),
                "provider": "mock", "model": "mock",
            }
            return
        try:
            self.parked.set()
            try:
                await asyncio.sleep(_PARK_BUDGET_S)
            except asyncio.CancelledError:
                self.outcome.append("cancelled")
                raise
            self.outcome.append("released")
            yield {"type": "text", "text": _LATE}
            yield {
                "type": "final",
                "response": LLMResponse(text=_LATE, usage={}),
                "provider": "mock", "model": "mock",
            }
        finally:
            self.closed.set()


async def _until(flag: threading.Event, what: str) -> None:
    for _ in range(int(_PARK_BUDGET_S / 0.01)):
        if flag.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


# --------------------------------------------------------------------------- #
# (1) POST /chat/turns/{id}/stop — from a second connection, before word one.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_stop_route_cancels_a_model_that_has_not_answered_yet(tmp_path):
    """The stop arrives down a SECOND connection while round 2's model is
    parked before its first frame; the streaming connection is never dropped
    (``_drive_stream`` parks its ``receive``), so a connection-bound cancel
    cannot account for anything asserted here."""
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    model = ParkedModel("read_file")
    app.state.platform.router.stream = model.stream
    stop: dict = {}

    async def press_stop_while_parked() -> None:
        await _until(model.parked, "round 2's model never started")
        stop["reply"] = await _asgi_post(app, "/chat/turns/turn-06/stop")

    presser = asyncio.create_task(press_stop_while_parked())
    frames = await _drive_stream(app, {
        "messages": [{"role": "user", "content": "read my notes"}],
        "tools": ["read_file"], "auto_tools": False, "turn_id": "turn-06",
    })
    await presser

    assert stop["reply"] == (200, {"ok": True, "stopped": True}), stop
    assert model.outcome == ["cancelled"], (
        "Stop did not reach the model's first await — it answered on its own"
        f" and only then did the turn stop: {model.outcome}"
    )
    assert model.closed.is_set(), "the model's stream was never unwound"
    kinds = [ev for ev, _ in frames]
    assert kinds.count("round") == 2, kinds          # it DID reach round 2
    assert "done" not in kinds, kinds
    assert _LATE not in [d.get("text") for ev, d in frames if ev == "token"]

    runs = _chat_runs(app.state.platform)
    assert len(runs) == 1, [(r.state, r.input_tokens) for r in runs]
    assert runs[0].state == AgentState.CANCELLED
    assert (runs[0].input_tokens, runs[0].output_tokens) == (_R1_IN, _R1_OUT)
    assert not TURNS.is_running("turn-06")


# --------------------------------------------------------------------------- #
# (2) + (3) THE SIDE PANEL — the reporter's path: its Stop, and closing it.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action", [P.PANEL_ACTION_STOP, P.PANEL_ACTION_CLOSE])
def test_the_panels_stop_and_close_cancel_a_model_that_has_not_answered_yet(
    tmp_path, monkeypatch, action
):
    """Over a real paired socket on the real ``create_app``. The panel runs
    the turn in-process and its Stop / close only ever call ``TURNS.stop`` —
    there is no connection of its own to drop."""
    model = ParkedModel("browser_read_page")
    with RealApp(tmp_path, monkeypatch) as app:
        app.pair()
        app.stub_tools()
        app.platform.router.stream = model.stream

        app.panel.send(P.PANEL_ACTION_SEND, text=_BROWSER_SENTENCE)
        _wait_for(model.parked.is_set, "round 2's model never started")
        app.panel.send(action)
        # Longer than the park budget, so the OLD code fails on the outcome
        # below (it answered on its own) rather than on this wait.
        assert model.closed.wait(_PARK_BUDGET_S * 2), (
            "the model's stream was never unwound"
        )
        _wait_for(lambda: not TURNS.running_ids(), "the panel turn never ended")

        assert model.outcome == ["cancelled"], (
            f"the panel's {action} did not reach the model before its first"
            f" frame — it answered on its own: {model.outcome}"
        )
        texts = [p.get("text") for e, p in app.panel.events()
                 if e == P.PANEL_EVENT_DELTA]
        assert _LATE not in texts, texts
        runs = _chat_runs(app.platform)
        assert len(runs) == 1, [(r.state, r.input_tokens) for r in runs]
        assert runs[0].state == AgentState.CANCELLED
        assert (runs[0].input_tokens, runs[0].output_tokens) == (_R1_IN, _R1_OUT)


# --------------------------------------------------------------------------- #
# (4) THE WAKE-UP ITSELF — a stop from another thread reaches the runner loop.
# --------------------------------------------------------------------------- #
def test_a_stop_from_another_thread_wakes_the_runner_and_an_early_stop_is_kept():
    """``TurnHandle.stop`` is called from the stop route's loop or a panel's,
    never necessarily the runner's; ``asyncio.Event.set`` from a foreign
    thread is not safe, so the wake-up must be marshalled onto the runner's
    loop. And a stop that lands before the runner first parks must not be
    lost in the gap between the flag and the event."""
    handle = TurnHandle("t-wake")

    async def runner() -> str:
        waiter = asyncio.ensure_future(handle.wait_stopped())
        await asyncio.sleep(0)                 # bound and parked
        threading.Thread(target=handle.stop).start()
        done, _ = await asyncio.wait({waiter}, timeout=_PARK_BUDGET_S)
        return "woken" if waiter in done else "never woken"

    assert asyncio.run(runner()) == "woken"

    early = TurnHandle("t-early")
    early.stop()                               # before anything is bound

    async def late_runner() -> str:
        waiter = asyncio.ensure_future(early.wait_stopped())
        done, _ = await asyncio.wait({waiter}, timeout=_PARK_BUDGET_S)
        return "returned" if waiter in done else "parked forever"

    assert asyncio.run(late_runner()) == "returned"


# --------------------------------------------------------------------------- #
# (5) A PUMP THAT DIES ON A BaseException never parks the turn (review fix).
# --------------------------------------------------------------------------- #
def test_a_pump_that_dies_on_a_base_exception_fails_the_turn_instead_of_parking_it():
    """The pump hands its end marker over only on an ordinary return or an
    ``Exception``. A stray ``CancelledError`` raised inside an adapter (a
    future cancelled elsewhere) ends the pump with nothing on the queue — the
    lane must see that and fail the turn, not wait on the queue forever."""
    from iron_jarvis.daemon.routes.chat import _frames_until_stop

    async def frames():
        raise asyncio.CancelledError()
        yield {}  # pragma: no cover — makes this an async generator

    async def lane() -> str:
        handle = TurnHandle("t-pump-dies")
        gen = _frames_until_stop(frames(), handle)
        try:
            await asyncio.wait_for(gen.__anext__(), _PARK_BUDGET_S)
        except RuntimeError as exc:
            return f"failed: {exc}"
        except asyncio.TimeoutError:
            return "parked forever"
        finally:
            await gen.aclose()
        return "yielded"

    assert asyncio.run(lane()) == "failed: the model's stream ended without a result"
