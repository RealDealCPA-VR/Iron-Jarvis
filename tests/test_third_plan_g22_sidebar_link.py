"""Third north-star fix plan, group 22 (survives a dropped connection), the
browser sidebar's link to the daemon: SB1, SB7, SB10, SB12, SB14.

SB1: a sidebar reply kept talking to the add-on connection it STARTED on. When
that connection dropped and came back mid-reply (the browser restarted the
add-on's worker, the daemon-side socket was replaced), the rest of the answer,
any approval card and the end of the reply went nowhere: the panel showed
"running" for ever, a card waited with nobody to answer it, and every later
Send was refused "still working". Now every frame of a running reply is sent
to the connection(s) the sidebar is on at that moment (the connection a panel
last opened on, every open window's, and the add-on's live one), and a panel
that opens while a reply runs is told the truth: running, the conversation
with the live answer so far, and the card that waits, shown again.

SB7: stopping a dictation awaited the speech service ON the add-on socket's
read loop, so every other frame on that connection waited for it. Now the
transcription is its own bounded task; a second start while it runs is
refused in plain words, and cancel drops it.

SB10 (daemon side): every refusal of a Send says it was a Send (``send_refused``)
and from which window, so the panel puts the words back in the box.

SB12 (decision G20): "Start new chats local only" covers the sidebar: its reply
body carries ``local_only`` (the field a new Chat page chat sends), its state
frame says so, and its dictation never goes to a speech service off this
network (the built-in offline model when it is here, else refused in the voice
route's own words; a clip is sent with ``local_only``).

SB14 (daemon side): a dictation the daemon ends at its size limit says so on
its final frame (``limit``).

Driven through the REAL ``PanelTurns`` (and, for SB7, the real app and its
add-on socket); the chat lane and the speech service are stand-ins at their
seams. Waits are events or bounded budgets that fail by name.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import ExtensionConnection
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.core.turns import TURNS
from tests._fakes.panel_harness import RealApp

#: A budget for something that must happen; a miss fails by name.
BUDGET_S = 10.0


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


class _Conn:
    """One add-on connection as the panel sees it. ``closed`` is the real
    connection's own flag; a send on a closed one fails, as on a dead socket."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.frames: list[tuple[str, dict[str, Any]]] = []
        self._seen: dict[str, asyncio.Event] = {}

    async def send(self, frame: dict) -> None:
        if self.closed:
            raise ConnectionError(f"{self.name} is gone")
        event = str(frame.get("event") or "")
        self.frames.append((event, dict(frame.get("payload") or {})))
        self._event(event).set()

    def _event(self, event: str) -> asyncio.Event:
        return self._seen.setdefault(event, asyncio.Event())

    async def wait(self, event: str, what: str) -> None:
        try:
            await asyncio.wait_for(self._event(event).wait(), BUDGET_S)
        except asyncio.TimeoutError:
            raise AssertionError(f"{what} (frames on {self.name}: {[e for e, _ in self.frames]})") from None

    def of(self, event: str) -> list[dict[str, Any]]:
        return [p for e, p in self.frames if e == event]


async def _no_models(conn: Any) -> None:
    """The model list rides every open; these tests have no providers to list."""


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --------------------------------------------------------------------------- #
# SB1: the reply follows the sidebar to its new connection
# --------------------------------------------------------------------------- #


def _card_lane(turns: PanelTurns, cards: list[str], seen_bodies: list[Any]):
    """The chat lane at its seam: words, then an ORDINARY card it waits on
    (the lane's own wait: the shared registry's future), then more words and
    the end. A second question ("again") answers at once."""
    from iron_jarvis.daemon.routes.chat import _approvals

    async def lane(platform, personas, body, **kw):
        seen_bodies.append(body)
        handle = TURNS.register(body.turn_id)
        registry = _approvals(SimpleNamespace(platform=turns.platform))

        async def gen():
            ap_id = ""
            try:
                if body.messages[-1].content == "again":
                    yield _sse("token", {"text": "Second answer."})
                    yield _sse("done", {"reply": "Second answer."})
                    return
                yield _sse("token", {"text": "Part one."})
                ap_id, fut = registry.request("browser_click", {"target": {"name": "Next"}})
                cards.append(ap_id)
                yield _sse("approval", {"id": ap_id, "tool": "browser_click",
                                        "args": {"target": {"name": "Next"}}})
                decision = await asyncio.wait_for(fut, BUDGET_S)
                yield _sse("approval_resolved", {"id": ap_id, "decision": decision})
                yield _sse("token", {"text": " Part two."})
                yield _sse("done", {"reply": "Part one. Part two."})
            finally:
                if ap_id:
                    registry.pop(ap_id)
                TURNS.release(body.turn_id, handle)

        return gen()

    return lane


def test_sb1_a_reply_whose_connection_dropped_finishes_on_the_new_one(monkeypatch):
    import iron_jarvis.daemon.chat_stream as chat_stream

    turns = PanelTurns(SimpleNamespace(browser=None))
    turns._emit_models = _no_models
    cards: list[str] = []
    bodies: list[Any] = []
    monkeypatch.setattr(chat_stream, "stream_chat_turn", _card_lane(turns, cards, bodies))

    async def drive() -> None:
        a, b = _Conn("A"), _Conn("B")
        await turns.handle(a, P.PANEL_ACTION_OPEN, {"window": "win-1"})
        await turns.handle(a, P.PANEL_ACTION_SEND, {"text": "go to the next page", "window": "win-1"})
        task = turns._task
        assert task is not None, "the send started no reply"
        await a.wait(P.PANEL_EVENT_APPROVAL, "the card never reached the first connection")
        card_id = cards[-1]
        assert a.of(P.PANEL_EVENT_APPROVAL)[-1]["id"] == card_id

        # The add-on's connection drops (the browser restarted its worker) and
        # the same sidebar window comes back on a new one.
        a.closed = True
        await turns.handle(b, P.PANEL_ACTION_OPEN, {"window": "win-1"})

        # B is told the truth at once: running, what was said so far, the card.
        assert b.of(P.PANEL_EVENT_STATE)[-1]["running"] is True
        live = b.of(P.PANEL_EVENT_HISTORY)[-1].get("live")
        assert live == {"question": "go to the next page", "answer": "Part one."}, live
        shown = [p["id"] for p in b.of(P.PANEL_EVENT_APPROVAL)]
        assert shown == [card_id], (
            "the card the reply waits on was not shown again on the new connection"
        )

        # Answered from B, the rest of the reply and its end reach B.
        await turns.handle(b, P.PANEL_ACTION_APPROVE, {"id": card_id, "window": "win-1"})
        done, _ = await asyncio.wait({task}, timeout=BUDGET_S)
        assert task in done, "the reply never ended after its card was answered"
        said = "".join(p.get("text", "") for p in b.of(P.PANEL_EVENT_DELTA))
        assert "Part two." in said, f"the rest of the answer went to the dead connection: {b.frames}"
        assert b.of(P.PANEL_EVENT_DONE), "the end of the reply went to the dead connection"
        assert turns._history[-2:] == [
            {"role": "user", "content": "go to the next page"},
            {"role": "assistant", "content": "Part one. Part two."},
        ], turns._history

        # A later Send on the new connection is a new reply, not "still working".
        before = len(b.of(P.PANEL_EVENT_STATE))
        await turns.handle(b, P.PANEL_ACTION_SEND, {"text": "again", "window": "win-1"})
        assert not [p for p in b.of(P.PANEL_EVENT_ERROR) if p.get("send_refused")], b.frames
        assert b.of(P.PANEL_EVENT_STATE)[before]["running"] is True
        assert turns._task is not None
        await asyncio.wait_for(turns._task, BUDGET_S)

    asyncio.run(drive())


class _WS:
    """The websocket under a real ``ExtensionConnection``: what it was sent."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)

    async def close(self, code: int = 1000) -> None:
        return None

    def events(self) -> list[str]:
        return [str(f.get("event") or "") for f in self.sent if f.get("type") == P.FRAME_PANEL_EVENT]


def test_sb1_frames_follow_the_live_connection_even_before_a_panel_reopens(monkeypatch):
    """An older add-on never re-opens after a reconnect: the reply's frames
    still go to the connection the browser backend holds now."""
    import iron_jarvis.daemon.chat_stream as chat_stream

    gate: dict[str, asyncio.Event] = {}

    async def lane(platform, personas, body, **kw):
        handle = TURNS.register(body.turn_id)

        async def gen():
            try:
                yield _sse("token", {"text": "Before."})
                await asyncio.wait_for(gate["go"].wait(), BUDGET_S)
                yield _sse("token", {"text": " After."})
                yield _sse("done", {"reply": "Before. After."})
            finally:
                TURNS.release(body.turn_id, handle)

        return gen()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", lane)

    async def drive() -> None:
        gate["go"] = asyncio.Event()
        old_ws, new_ws = _WS(), _WS()
        old = ExtensionConnection(old_ws, paired=True)
        new = ExtensionConnection(new_ws, paired=True)
        backend = SimpleNamespace(connection=old)
        turns = PanelTurns(SimpleNamespace(browser=SimpleNamespace(backend=backend)))
        await turns.handle(old, P.PANEL_ACTION_SEND, {"text": "read this"})
        task = turns._task
        for _ in range(200):
            if P.PANEL_EVENT_DELTA in old_ws.events():
                break
            await asyncio.sleep(0)
        # The socket is replaced: the old one closed, the backend holds the new.
        await old.close()
        backend.connection = new
        gate["go"].set()
        await asyncio.wait_for(task, BUDGET_S)
        assert P.PANEL_EVENT_DELTA in new_ws.events(), new_ws.events()
        assert P.PANEL_EVENT_DONE in new_ws.events(), (
            f"the end of the reply never reached the live connection: {new_ws.events()}"
        )

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB10 (daemon side): a refused Send says it was refused, and for which window
# --------------------------------------------------------------------------- #


def test_sb10_every_refusal_of_a_send_is_marked_with_its_window():
    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn = _Conn("A")
        # Still working on the last message.
        hold = asyncio.get_running_loop().create_future()
        turns._task = asyncio.ensure_future(hold)
        try:
            await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "and this", "window": "win-9"})
        finally:
            hold.cancel()
        err = conn.of(P.PANEL_EVENT_ERROR)[-1]
        assert err.get("send_refused") is True and err.get("window") == "win-9", err
        # Too many messages in a minute.
        turns._task = None
        turns._over_budget = lambda: "Too many messages in a minute."
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "more", "window": "win-9"})
        err = conn.of(P.PANEL_EVENT_ERROR)[-1]
        assert err.get("reason") == "rate_limited" and err.get("send_refused") is True, err
        assert err.get("window") == "win-9", err

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB12: "Start new chats local only" covers the sidebar (decision G20)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("setting", [True, False])
def test_sb12_the_sidebar_reply_runs_local_only_with_the_setting(monkeypatch, setting):
    import iron_jarvis.daemon.chat_stream as chat_stream

    bodies: list[Any] = []

    async def lane(platform, personas, body, **kw):
        bodies.append(body)

        async def gen():
            yield _sse("done", {"reply": "ok"})

        return gen()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", lane)

    async def drive() -> None:
        platform = SimpleNamespace(browser=None, config=SimpleNamespace(chat_new_local_only=setting))
        turns = PanelTurns(platform)
        turns._emit_models = _no_models
        conn = _Conn("A")
        await turns.handle(conn, P.PANEL_ACTION_OPEN, {})
        assert conn.of(P.PANEL_EVENT_STATE)[-1].get("local_only") is setting
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "summarise the page"})
        await asyncio.wait_for(turns._task, BUDGET_S)
        assert bodies and bodies[-1].local_only is setting, (
            "the sidebar's reply did not carry the new-chats Local only setting"
        )

    asyncio.run(drive())


class _FakeRecognizer:
    def __init__(self, model, rate):
        self.fed = 0

    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 — Vosk's own name
        return False

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"partial": "hel"})

    def Result(self) -> str:  # noqa: N802
        return json.dumps({"text": ""})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": "hello there"})


def _voice_turns(*, local_only: bool, backend: tuple[str, str, str] | None, vosk: bool) -> PanelTurns:
    platform = SimpleNamespace(
        browser=None,
        config=SimpleNamespace(chat_new_local_only=local_only, voice_transcribe_model=""),
    )
    deps = SimpleNamespace(
        platform=platform,
        _voice_backend=lambda: backend,
        _vosk_model_path=(lambda: r"C:\models\vosk") if vosk else (lambda: None),
        _vosk_model=lambda: object(),
    )
    return PanelTurns(platform, deps=deps)


def test_sb12_dictation_never_goes_to_a_hosted_service_under_local_only(monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(KaldiRecognizer=_FakeRecognizer))
    handed: list[dict] = []

    async def fake_transcribe(d, *, audio_b64, mime="audio/webm", language="", **kw):
        handed.append(kw)
        return {"text": "words", "backend": "custom"}

    monkeypatch.setattr(voice_routes, "transcribe_clip", fake_transcribe)
    hosted = ("openai", "https://api.example/v1/audio/transcriptions", "k")

    async def drive() -> None:
        # Only a hosted service set up: refused before a word is recorded, in
        # the voice route's own words.
        turns = _voice_turns(local_only=True, backend=hosted, vosk=False)
        conn = _Conn("A")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        errors = conn.of(P.PANEL_EVENT_ERROR)
        assert errors, f"a hosted speech service was started under Local only: {conn.frames}"
        err = errors[-1]
        assert err["reason"] == "voice_unavailable", err
        assert err["text"] == str(voice_routes.SpeechKeptHere("openai", "OpenAI")), err
        assert turns._voice_session is None and not conn.of(P.PANEL_EVENT_TRANSCRIPT)

        # The built-in offline model is here: it is used instead.
        turns = _voice_turns(local_only=True, backend=hosted, vosk=True)
        conn = _Conn("B")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        started = conn.of(P.PANEL_EVENT_TRANSCRIPT)[-1]
        assert started["listening"] is True and started["backend"] == "local", started

        # The setting off: the service the owner picked is used, as before.
        turns = _voice_turns(local_only=False, backend=hosted, vosk=True)
        conn = _Conn("C")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        assert conn.of(P.PANEL_EVENT_TRANSCRIPT)[-1]["backend"] == "openai"

        # A speech server on this network is kept, and the clip is sent with
        # Local only (the route's own check runs again on the way out).
        turns = _voice_turns(
            local_only=True, backend=("custom", "http://127.0.0.1:9/v1/audio/transcriptions", ""),
            vosk=False,
        )
        conn = _Conn("D")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        assert conn.of(P.PANEL_EVENT_TRANSCRIPT)[-1]["backend"] == "custom"
        pcm = base64.b64encode(b"\x01\x00" * 400).decode("ascii")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "chunk", "pcm_b64": pcm})
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "stop"})
        await asyncio.wait_for(turns._voice_task, BUDGET_S)
        assert handed == [{"local_only": True}], handed
        assert conn.of(P.PANEL_EVENT_TRANSCRIPT)[-1]["text"] == "words"

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB7: the transcription never holds the add-on's connection
# --------------------------------------------------------------------------- #


def _pcm(n_samples: int) -> str:
    return base64.b64encode((1000).to_bytes(2, "little", signed=True) * n_samples).decode("ascii")


def test_sb7_a_dictation_being_written_down_does_not_hold_the_connection(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    release = threading.Event()
    asked = threading.Event()

    async def slow_service(d, *, audio_b64, mime="audio/webm", language="", **kw):
        asked.set()
        # Held until the TEST releases it (well past the reader's budget).
        for _ in range(1500):
            if release.is_set():
                return {"text": "the words", "backend": "openai"}
            await asyncio.sleep(0.02)
        raise AssertionError("the test never released the speech service")

    monkeypatch.setattr(voice_routes, "transcribe_clip", slow_service)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        deps = app.platform.browser.backend.panel_handler.__self__.deps
        monkeypatch.setattr(deps, "_voice_backend", lambda: ("openai", "u", "k"))
        monkeypatch.setattr(deps, "_vosk_model_path", lambda: None)
        try:
            app.panel.send(P.PANEL_ACTION_VOICE, op="start")
            app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "start was not acknowledged")
            app.panel.send(P.PANEL_ACTION_VOICE, op="chunk", pcm_b64=_pcm(400))
            app.panel.send(P.PANEL_ACTION_VOICE, op="stop")
            assert asked.wait(BUDGET_S), "stop never asked the speech service"
            # While the service is still writing it down, the connection serves
            # the next frame (here the panel's own open; a page action's answer
            # rides the same loop).
            app.panel.barrier(
                "the add-on's connection waited for the transcription: the next frame was not served"
            )
            # A second dictation while the first is being written down is
            # refused in plain words; nothing races the first into the box.
            app.panel.send(P.PANEL_ACTION_VOICE, op="start")
            busy = app.panel.wait_for(P.PANEL_EVENT_ERROR, "a second start was not refused")
            assert busy == {"text": panel_mod.VOICE_BUSY, "reason": "voice_busy"}, busy
            assert [p for e, p in app.panel.events() if e == P.PANEL_EVENT_TRANSCRIPT and p.get("final")] == []
        finally:
            release.set()
        final = app.panel.wait_for(P.PANEL_EVENT_TRANSCRIPT, "the words never came back", nth=2)
        assert final == {"text": "the words", "partial": "", "final": True, "backend": "openai"}, final


def test_sb7_cancel_drops_a_transcription_still_running(monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    async def drive() -> None:
        never = asyncio.Event()

        async def slow_service(d, **kw):
            await never.wait()
            return {"text": "late words"}

        monkeypatch.setattr(voice_routes, "transcribe_clip", slow_service)
        turns = _voice_turns(local_only=False, backend=("openai", "u", "k"), vosk=False)
        conn = _Conn("A")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "chunk", "pcm_b64": _pcm(100)})
        try:
            await asyncio.wait_for(
                turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "stop"}), BUDGET_S
            )
        except asyncio.TimeoutError:
            raise AssertionError(
                "stop waited for the speech service: the add-on's connection is held"
            ) from None
        task = turns._voice_task
        assert task is not None and not task.done()
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "cancel"})
        await asyncio.wait({task}, timeout=BUDGET_S)
        assert task.cancelled(), "cancel left the transcription running"
        assert [p for p in conn.of(P.PANEL_EVENT_TRANSCRIPT) if p.get("final")] == []

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB14 (daemon side): a dictation ended at its size limit says so
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["stream", "clip"])
def test_sb14_a_dictation_ended_at_its_size_limit_says_so(monkeypatch, mode):
    from iron_jarvis.daemon.routes import voice as voice_routes

    monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(KaldiRecognizer=_FakeRecognizer))

    async def service(d, **kw):
        return {"text": "what was heard", "backend": "openai"}

    monkeypatch.setattr(voice_routes, "transcribe_clip", service)

    async def drive() -> None:
        turns = _voice_turns(
            local_only=False,
            backend=None if mode == "stream" else ("openai", "u", "k"),
            vosk=mode == "stream",
        )
        turns.VOICE_MAX_SESSION_BYTES = 1000
        conn = _Conn("A")
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "chunk", "pcm_b64": _pcm(600)})
        if getattr(turns, "_voice_task", None) is not None:
            await asyncio.wait_for(turns._voice_task, BUDGET_S)
        final = [p for p in conn.of(P.PANEL_EVENT_TRANSCRIPT) if p.get("final")]
        assert len(final) == 1 and final[0].get("limit") is True, conn.frames
        assert turns._voice_session is None
        # A dictation the person stopped carries no such mark.
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "start"})
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "chunk", "pcm_b64": _pcm(10)})
        await turns.handle(conn, P.PANEL_ACTION_VOICE, {"op": "stop"})
        if getattr(turns, "_voice_task", None) is not None:
            await asyncio.wait_for(turns._voice_task, BUDGET_S)
        final = [p for p in conn.of(P.PANEL_EVENT_TRANSCRIPT) if p.get("final")]
        assert len(final) == 2 and "limit" not in final[1], final

    asyncio.run(drive())
