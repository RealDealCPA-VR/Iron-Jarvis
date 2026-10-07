"""Wave 4 (RECOVERY) — the chat backend, v1.312.0.

Two verified findings, pinned through the REAL app (``create_app`` on an
isolated home; the real ``registry.invoke`` with only a tool's ``execute``
stubbed):

1. chat-prep-invisible-unstoppable (contract W4-1, STREAM LANE ONLY).
   ``/chat/stream`` used to finish ALL of its preparation (grounding,
   attachments, a possible compaction model call, tool choice) before it sent
   a single byte. Now the response opens right after the cheap eager checks
   (400 empty messages, 404 unknown skill), the rest of prep runs inside the
   stream body behind Stop checks, and additive ``event: phase`` frames
   (``{"phase": "recalling" | "reading_files" | "summarizing" |
   "choosing_tools"}``) say what it is doing before the first token.

2. allow-for-conversation-eats-arming-slots (contract W4-2, BOTH LANES).
   ``ChatBody.granted_tools`` carries the conversation's grants. A name in it
   is a "conversation" grant for THIS turn ONLY IF the turn armed or
   ask-armed that name by some other path. A grant NEVER arms: the armed-set
   gate in ``registry.invoke`` (``allowed_names``) is unchanged, and the
   anti-vacuity controls below prove a granted-but-unarmed tool is still
   refused there.

HOW THE PREP IS HELD: the lessons hop of the grounding gather
(``platform.learning.apply_to_prompt``, run in a worker thread) parks on a
``threading.Event``. Nothing here asserts wall-clock time. Every wait is for
the thing asserted, with a bound well under the 300 s per-test ceiling, and
the hold has its own safety valve so a red run ends instead of hanging.

Controls marked CONTROL pass on the v1.311.0 code and must keep passing. They
prove that the setup is live (the compaction really runs, the card really
renders, the stub really executes), so the red assertions cannot pass
vacuously.

Drivers: ``TestClient`` buffers the whole SSE body, so it is used only where
nothing has to happen mid-stream. Anything that acts while the turn is
running uses the hand-rolled ASGI driver (the pattern from
``tests/test_chat_turn_stop_v1241.py``), whose ``receive()`` parks and never
disconnects. Any stop observed is therefore the registry's doing.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import inspect
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.daemon.chat_turn as chat_turn
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

#: The four phase names the page maps onto its waiting words (W4-1/W4-3).
PHASES = ("recalling", "reading_files", "summarizing", "choosing_tools")

#: Safety valve on the held grounding hop: how long it parks if the test
#: never releases it. Only a broken run ever reaches it. It is a budget, not
#: an assertion.
_HOLD_VALVE_S = 30.0

#: Bound on "the thing asserted" while the hop is held (response start, a
#: phase frame, a keepalive). Well under the valve and the per-test ceiling.
_WHILE_HELD_S = 5.0

#: A message whose signals arm `shell` at the ASK tier (run + command), the
#: same fixture as tests/test_chat_approvals_v1187.py.
_ASK_MSG = "run the command `echo granted-run` in the terminal for me"


@pytest.fixture(autouse=True)
def _clean_registry():
    """TURNS is process-local, so a leaked id would let one test stop another's turn."""
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _sse_frames(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        event, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            try:
                data = json.loads("\n".join(data_lines))
            except ValueError:
                continue
            out.append((event, data if isinstance(data, dict) else {}))
    return out


def _scope(method: str, path: str, body: bytes) -> dict:
    """A LOOPBACK-host scope. v1.175.0's DNS-rebinding guard rejects anything else."""
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8787"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


async def _asgi(app, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """One buffered in-process request on a SECOND, independent connection."""
    body = json.dumps(payload or {}).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app(_scope(method, path, body), receive, send)
    raw = b"".join(chunks)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw.decode("utf-8", "replace")}


class _LiveStream:
    """``/chat/stream`` consumed AS IT STREAMS, observable from the test.

    Records whether ``http.response.start`` has been sent, every parsed frame,
    and every keepalive chunk, so a test can ask "has the response opened yet?"
    while the turn is still inside its preparation.
    """

    def __init__(self, app, body: dict) -> None:
        self.app = app
        self.raw = json.dumps(body).encode()
        self.status: int | None = None
        self.frames: list[tuple[str, dict]] = []
        self.keepalives = 0
        self.ended = False
        self._buf = ""
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": self.raw, "more_body": False}
            await asyncio.sleep(3600)  # the stream owns the connection
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                self.status = msg["status"]
            elif msg["type"] == "http.response.body":
                chunk = msg.get("body", b"")
                if b": keepalive" in chunk:
                    self.keepalives += 1
                self._buf += chunk.decode("utf-8", "replace")
                while "\n\n" in self._buf:
                    block, self._buf = self._buf.split("\n\n", 1)
                    self.frames.extend(_sse_frames(block + "\n\n"))
                if not msg.get("more_body", False):
                    self.ended = True

        self.task = asyncio.create_task(
            self.app(_scope("POST", "/chat/stream", self.raw), receive, send)
        )

    def kinds(self) -> list[str]:
        return [ev for ev, _ in self.frames]

    def phases(self) -> list[str]:
        return [str(d.get("phase")) for ev, d in self.frames if ev == "phase"]

    async def finish(self, budget: float = 30.0) -> None:
        assert self.task is not None
        await asyncio.wait_for(self.task, budget)


async def _until(pred, what: str, budget: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    while not pred():
        if loop.time() > deadline:
            raise AssertionError(what)
        await asyncio.sleep(0.01)


class _HeldLessons:
    """Parks the grounding gather's lessons hop (a worker thread) until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def apply_to_prompt(self, prompt: str = "", *a: Any, **kw: Any) -> str:
        self.calls += 1
        self.entered.set()
        self.release.wait(_HOLD_VALVE_S)
        return ""


class _CountingRouter:
    """Counts every model call the chat turn makes (stream and complete)."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, **kw: Any):
        self.calls += 1
        yield {"type": "text", "text": "ok"}
        yield {
            "type": "final",
            "response": LLMResponse(text="ok", usage={"input_tokens": 5, "output_tokens": 2}),
            "provider": "mock", "model": "mock",
        }

    async def complete(self, **kw: Any):
        self.calls += 1
        return RouteResult(
            LLMResponse(text="ok", usage={"input_tokens": 5, "output_tokens": 2}),
            "mock", "mock",
        )


class _Summariser:
    """``platform._compaction_complete``: counts the automatic summary's model call."""

    def __init__(self) -> None:
        self.calls = 0

    def factory(self, *a: Any, **k: Any):
        async def complete(system, user):
            self.calls += 1
            return "GOAL:\n- audit the ledger\n", "acme", "acme-1"

        return complete


def _long_history() -> list[dict]:
    """Past the ceiling at a 2,000-token window (tests/test_wave3_chat_lanes_v1311.py)."""
    filler = "audit the ledger carefully, line by line. " * 12
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}: {filler}"}
        for i in range(14)
    ]
    msgs.append({"role": "user", "content": "and now the totals?"})
    return msgs


def _wire(app, *, compaction: bool) -> tuple[_HeldLessons, _CountingRouter, _Summariser]:
    platform = app.state.platform
    held, router, summ = _HeldLessons(), _CountingRouter(), _Summariser()
    platform.learning.apply_to_prompt = held.apply_to_prompt
    platform.router.stream = router.stream
    platform.router.complete = router.complete
    if compaction:
        platform._compaction_complete = summ.factory
    return held, router, summ


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


# =========================================================================== #
# W4-1 — the response opens before preparation, and says what it is doing
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_response_opens_and_stays_alive_while_grounding_is_still_running(
    tmp_path, monkeypatch
):
    """While a grounding hop is HELD, the client already has the 200, a
    ``phase: recalling`` frame and the heartbeat. That is how the page can
    drop its 10-minute prep watchdog to the stall rule and say "Recalling
    what's relevant..." instead of a bare "Thinking...".

    RED TODAY: ``stream_chat_turn`` awaits ``chat_stream``'s whole prep before
    any response object exists. ``http.response.start`` is never sent while
    the hop is held, so the first wait below times out.
    """
    monkeypatch.setattr(chat_routes, "_SSE_HEARTBEAT_S", 0.05)
    app = create_app(str(tmp_path))
    held, router, _ = _wire(app, compaction=False)
    live = _LiveStream(app, {
        "messages": [{"role": "user", "content": "hello there"}],
        "turn_id": "w4-open",
    })
    live.start()
    try:
        await _until(held.entered.is_set, "the grounding hop never started",
                     _WHILE_HELD_S * 2)
        await _until(lambda: live.status is not None,
                     "no response was opened while grounding was still running"
                     " (the 200 waits for the whole prep)", _WHILE_HELD_S)
        assert live.status == 200
        await _until(lambda: "recalling" in live.phases(),
                     f"no `phase: recalling` frame while grounding was held: {live.frames}",
                     _WHILE_HELD_S)
        await _until(lambda: live.keepalives > 0,
                     "the heartbeat does not cover preparation", _WHILE_HELD_S)
        assert not held.release.is_set()  # all of the above happened while held
        assert router.calls == 0, "the model was called before grounding finished"
    finally:
        held.release.set()
        await live.finish()
    # Released, the turn completes normally: the eager open changed nothing else.
    assert "done" in live.kinds(), live.kinds()
    assert router.calls >= 1


@pytest.mark.asyncio
async def test_a_stop_during_preparation_ends_the_turn_with_no_model_call(tmp_path):
    """The page's Stop now also POSTs ``/chat/turns/{id}/stop``. Pressed while
    grounding is held, it must end the turn before any LATER prep step makes
    a model call. Here that later step is the automatic compaction's
    summary, the paid call the finding names. Ends like a Stop today: no
    ``round``/``token``/``done``/``error`` frame, nothing billed, and the id is
    released.

    RED TODAY: prep never checks Stop. Once the hop is released, the
    compaction runs and calls its model (``summariser.calls == 1``), and only
    gen()'s round-top check ends the turn.
    """
    app = create_app(str(tmp_path))
    status, body = await _asgi(app, "PUT", "/settings",
                               {"values": {"model_context_windows": {"mock": 2000}}})
    assert status == 200, body
    held, router, summ = _wire(app, compaction=True)
    live = _LiveStream(app, {"messages": _long_history(), "turn_id": "w4-prep-stop"})
    live.start()
    try:
        await _until(held.entered.is_set, "the grounding hop never started",
                     _WHILE_HELD_S * 2)
        stop = await _asgi(app, "POST", "/chat/turns/w4-prep-stop/stop")
        assert stop == (200, {"ok": True, "stopped": True}), stop
    finally:
        held.release.set()
        await live.finish()

    assert summ.calls == 0, (
        "a Stop pressed during grounding still ran the automatic compaction's"
        f" model call ({summ.calls})"
    )
    assert router.calls == 0, f"the chat model was called after Stop ({router.calls})"
    kinds = live.kinds()
    for k in ("round", "token", "done", "error"):
        assert k not in kinds, f"a stopped prep emitted `{k}`: {kinds}"
    assert "summarizing" not in live.phases(), live.phases()
    assert "choosing_tools" not in live.phases(), live.phases()
    assert _chat_runs(app.state.platform) == [], "a stopped prep billed a row"
    assert not TURNS.is_running("w4-prep-stop")


@pytest.mark.asyncio
async def test_control_without_a_stop_the_held_turn_compacts_and_answers(tmp_path):
    """CONTROL (passes today): the same held setup with no Stop. The
    compaction's model call really happens once and the turn answers, so
    ``summariser.calls == 0`` above can only mean the Stop prevented it."""
    app = create_app(str(tmp_path))
    status, body = await _asgi(app, "PUT", "/settings",
                               {"values": {"model_context_windows": {"mock": 2000}}})
    assert status == 200, body
    held, router, summ = _wire(app, compaction=True)
    live = _LiveStream(app, {"messages": _long_history(), "turn_id": "w4-prep-go"})
    live.start()
    try:
        await _until(held.entered.is_set, "the grounding hop never started",
                     _WHILE_HELD_S * 2)
    finally:
        held.release.set()
        await live.finish()
    assert summ.calls == 1, summ.calls
    assert router.calls >= 1
    done = [d for ev, d in live.frames if ev == "done"]
    assert done and done[-1]["context"].get("compacted") is True, done


def test_phase_frames_name_each_stage_in_order_before_the_first_round(tmp_path):
    """A turn with an attachment past the compaction ceiling walks all four
    stages. The phases arrive in prep order, all before the first ``round``,
    each as ``{"phase": <name>}``.

    RED TODAY: no ``phase`` frame exists.
    """
    client = TestClient(create_app(str(tmp_path)))
    r = client.put("/settings", json={"values": {"model_context_windows": {"mock": 2000}}})
    assert r.status_code == 200, r.text
    platform = client.app.state.platform
    router, summ = _CountingRouter(), _Summariser()
    platform.router.stream = router.stream
    platform.router.complete = router.complete
    platform._compaction_complete = summ.factory
    up = client.post("/documents/upload", json={
        "filename": "notes.txt",
        "content_b64": base64.b64encode(b"W4-NOTE-1312").decode(),
    }).json()

    r = client.post("/chat/stream", json={
        "messages": _long_history(), "attachments": [up["path"]],
    })
    assert r.status_code == 200, r.text
    frames = _sse_frames(r.text)
    kinds = [ev for ev, _ in frames]
    assert summ.calls == 1, "setup: the automatic compaction must actually run"
    phases = [d.get("phase") for ev, d in frames if ev == "phase"]
    assert phases == list(PHASES), f"phases={phases} kinds={kinds}"
    assert "round" in kinds, kinds
    last_phase = max(i for i, k in enumerate(kinds) if k == "phase")
    assert last_phase < kinds.index("round"), kinds
    assert "done" in kinds


def test_a_plain_turn_claims_no_file_reading_and_no_summary(tmp_path):
    """No attachments and no compaction: only the stages that really run.
    "Reading your files..." or "Summarizing..." on a plain "hello" would be the
    page lying about what the daemon is doing.

    RED TODAY: no ``phase`` frame exists, so the list is [].
    """
    client = TestClient(create_app(str(tmp_path)))
    router = _CountingRouter()
    client.app.state.platform.router.stream = router.stream
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "hello there"}],
    })
    assert r.status_code == 200, r.text
    frames = _sse_frames(r.text)
    phases = [d.get("phase") for ev, d in frames if ev == "phase"]
    assert phases == ["recalling", "choosing_tools"], phases
    assert "done" in [ev for ev, _ in frames]


@pytest.mark.asyncio
async def test_control_the_eager_checks_still_land_as_status_codes_and_release_the_turn(
    tmp_path,
):
    """CONTROL (passes today): moving prep into the stream body must keep the
    400 (empty messages) and 404 (unknown skill) as real status codes. A
    named turn that never ran must not stay registered: a later stop is an
    honest 404, never a 200 against nothing."""
    app = create_app(str(tmp_path))
    status, _ = await _asgi(app, "POST", "/chat/stream",
                           {"messages": [], "turn_id": "w4-eager-400"})
    assert status == 400
    assert not TURNS.is_running("w4-eager-400")
    status, body = await _asgi(app, "POST", "/chat/stream", {
        "messages": [{"role": "user", "content": "hi"}],
        "skill": "no-such-skill-w4", "turn_id": "w4-eager-404",
    })
    assert status == 404, body
    assert "no such skill" in json.dumps(body)
    assert not TURNS.is_running("w4-eager-404")
    stop = await _asgi(app, "POST", "/chat/turns/w4-eager-404/stop")
    assert stop[0] == 404, stop


# =========================================================================== #
# W4-2 — granted_tools: a grant for THIS turn, never an arming
# =========================================================================== #
def _stub_execute(platform, monkeypatch, name: str) -> list[dict]:
    """Stub ONLY ``execute`` on the registered tool: the real
    ``registry.invoke`` (armed-set gate, permission engine, ledger) still runs."""
    tool = platform.registry.get(name)
    assert tool is not None, f"{name} is not registered"
    ran: list[dict] = []

    async def fake_execute(args, ctx):
        ran.append(dict(args or {}))
        return ToolResult(ok=True, output=f"RAN-{name}")

    monkeypatch.setattr(tool, "execute", fake_execute)
    return ran


def _one_call_stream(name: str, args: dict):
    """router.stream: round 0 calls ``name``; round 1 answers. Records tools shown."""
    state = {"n": 0, "shown": []}

    async def fake_stream(*, system, messages, tools, **kw):
        state["shown"].append([
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in (tools or [])
        ])
        if state["n"] == 0:
            state["n"] += 1
            yield {"type": "final", "response": LLMResponse(
                text="", tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
                usage={"input_tokens": 3, "output_tokens": 2},
            ), "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "done."}
            yield {"type": "final", "response": LLMResponse(
                text="done.", usage={"input_tokens": 3, "output_tokens": 2},
            ), "provider": "mock", "model": "mock"}

    return fake_stream, state


def _one_call_complete(name: str, args: dict):
    state = {"n": 0, "shown": [], "messages": []}

    async def fake_complete(*, system, messages, tools, **kw):
        state["messages"].append(list(messages))
        state["shown"].append([
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in (tools or [])
        ])
        if state["n"] == 0:
            state["n"] += 1
            return RouteResult(LLMResponse(
                text="", tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
            ), "mock", "mock")
        return RouteResult(LLMResponse(text="done."), "mock", "mock")

    return fake_complete, state


def _stream_turn(tmp_path, monkeypatch, *, payload: dict, tool: str, args: dict):
    monkeypatch.setattr(chat_routes, "CHAT_ASK_TIMEOUT_S", 0.3)
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ran = _stub_execute(platform, monkeypatch, tool)
    fake, state = _one_call_stream(tool, args)
    platform.router.stream = fake
    r = client.post("/chat/stream", json=payload)
    assert r.status_code == 200, r.text
    frames = _sse_frames(r.text)
    return frames, ran, state


def _finished(frames, tool: str) -> dict:
    return next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("name", tool) == tool
    )


@pytest.mark.parametrize("granted", [True, False], ids=["granted", "control-ungranted"])
def test_a_granted_ask_tier_tool_runs_with_no_card(tmp_path, monkeypatch, granted):
    """The user answered "this conversation" for ``shell`` earlier, so the
    page sends ``granted_tools: ["shell"]``. This turn ask-arms shell from the
    sentence, so the grant applies: the call runs through the REAL
    ``registry.invoke`` with no approval card.

    RED TODAY (granted): ``granted_tools`` is ignored. The card renders, nobody
    answers, it times out, and ``execute`` never runs.
    CONTROL (ungranted, passes today): the same turn without the grant still
    cards. This proves the card is live and the stub cannot run on its own.
    """
    payload = {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}
    if granted:
        payload["granted_tools"] = ["shell"]
    frames, ran, state = _stream_turn(
        tmp_path, monkeypatch, payload=payload,
        tool="shell", args={"command": "echo granted-run"},
    )
    kinds = [ev for ev, _ in frames]
    assert "shell" in state["shown"][0], "setup: shell must be ask-armed by the sentence"
    done = next(d for ev, d in frames if ev == "done")
    if granted:
        assert "approval" not in kinds, "a conversation grant still asked again"
        assert ran == [{"command": "echo granted-run"}], ran
        assert _finished(frames, "shell")["ok"] is True
        assert "shell" in (done.get("tools_used") or [])
        assert "shell" not in (done.get("denied_tools") or [])
    else:
        assert "approval" in kinds, kinds
        assert ran == [], "an ungranted ask-tier call ran without an answer"


@pytest.mark.parametrize("granted", [True, False], ids=["granted", "control-ungranted"])
def test_strict_mode_does_not_recard_a_tool_granted_for_the_conversation(
    tmp_path, monkeypatch, granted
):
    """Under "always ask", an armed ``write_file`` cards until a card grants it
    for the conversation. That grant now arrives as ``granted_tools`` (the
    ``card_grants`` half of W4-2).

    RED TODAY (granted): the card renders again, times out, and nothing is written.
    CONTROL (ungranted, passes today): strict mode still cards the write.
    """
    payload = {
        "messages": [{"role": "user", "content": "write a note file for me"}],
        "tools": ["write_file"], "approval_mode": "always_ask",
    }
    if granted:
        payload["granted_tools"] = ["write_file"]
    frames, ran, _ = _stream_turn(
        tmp_path, monkeypatch, payload=payload,
        tool="write_file", args={"path": "note.txt", "content": "w4"},
    )
    kinds = [ev for ev, _ in frames]
    if granted:
        assert "approval" not in kinds, "strict mode re-carded a conversation grant"
        assert len(ran) == 1, ran
    else:
        assert "approval" in kinds, kinds
        assert ran == []


def test_control_a_grant_never_arms_a_tool_on_the_stream_lane(tmp_path, monkeypatch):
    """ANTI-VACUITY (passes today, must keep passing): ``shell`` is granted
    but nothing armed it this turn ("hello there" carries no host signal). The
    model is not shown it, and a call to it anyway is refused by the
    registry's armed-set gate. It is not carded (no Allow on a call that
    cannot run) and it is not executed."""
    frames, ran, state = _stream_turn(
        tmp_path, monkeypatch,
        payload={
            "messages": [{"role": "user", "content": "hello there"}],
            # read_file is armed so the turn HAS a tool loop: with nothing
            # armed the lane never invokes any call, and the refusal below
            # would pass without the armed-set gate ever being asked.
            "tools": ["read_file"], "auto_tools": True, "granted_tools": ["shell"],
        },
        tool="shell", args={"command": "echo should-not-run"},
    )
    assert "read_file" in state["shown"][0], "setup: the turn must have armed tools"
    assert "shell" not in state["shown"][0], "a grant armed the tool"
    assert "approval" not in [ev for ev, _ in frames]
    assert ran == [], "a granted-but-unarmed tool executed"
    fin = _finished(frames, "shell")
    assert fin["ok"] is False
    assert "not one of this agent's tools" in fin.get("output", ""), fin


def test_control_a_grant_never_arms_a_tool_on_the_post_lane(tmp_path, monkeypatch):
    """ANTI-VACUITY, POST /chat (passes today, must keep passing): the same
    rule in the headless lane, through its real ``registry.invoke``."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ran = _stub_execute(platform, monkeypatch, "shell")
    fake, state = _one_call_complete("shell", {"command": "echo should-not-run"})
    monkeypatch.setattr(platform.router, "complete", fake)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello there"}],
        # read_file armed so the call reaches registry.invoke (see the stream twin).
        "tools": ["read_file"], "auto_tools": True, "granted_tools": ["shell"],
    })
    assert r.status_code == 200, r.text
    assert "read_file" in state["shown"][0], "setup: the turn must have armed tools"
    assert "shell" not in state["shown"][0]
    assert ran == []
    assert "shell" not in (r.json().get("tools_used") or [])
    # The model's next round reads the registry's refusal: the call reached
    # the armed-set gate and was refused there (not skipped before it).
    tool_msgs = [
        str(getattr(m, "content", "")) for m in state["messages"][-1]
        if getattr(m, "role", "") == "tool"
    ]
    assert any("not one of this agent's tools" in t for t in tool_msgs), tool_msgs


def test_control_the_post_lane_runs_an_armed_granted_tool(tmp_path, monkeypatch):
    """CONTROL (passes today): in POST /chat every armed tool is already its
    own grant, so ``granted_tools`` must not narrow anything there. Also
    proves the POST stub path is live, so the refusal above means something."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ran = _stub_execute(platform, monkeypatch, "shell")
    fake, _ = _one_call_complete("shell", {"command": "echo armed-run"})
    monkeypatch.setattr(platform.router, "complete", fake)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "run it"}],
        "tools": ["shell"], "granted_tools": ["shell"],
    })
    assert r.status_code == 200, r.text
    assert ran == [{"command": "echo armed-run"}], ran
    assert "shell" in (r.json().get("tools_used") or [])


def test_chat_body_carries_granted_tools_uncapped_and_defaulted():
    """W4-2 wire contract: optional, default empty, and UNCAPPED (the
    conversation's grants are not limited by the 6-tool arming cap).

    RED TODAY: ChatBody has no such field; pydantic drops the key.
    """
    names = [f"tool_{i}" for i in range(40)]
    body = ChatBody(messages=[{"role": "user", "content": "x"}], granted_tools=names)
    assert getattr(body, "granted_tools", None) == names
    bare = ChatBody(messages=[{"role": "user", "content": "x"}])
    assert getattr(bare, "granted_tools", None) == []


def test_the_conversation_grant_helper_only_grants_what_the_turn_armed(tmp_path):
    """INTERFACE: ONE shared helper decides the grant for BOTH lanes, so the
    two lanes cannot drift: ``chat_turn._conversation_grants(registry,
    granted, turn_tools) -> set[str]``. It returns every granted name that is
    in ``turn_tools`` together with that tool's ``perm_key()``, and nothing
    else.

    RED TODAY: the helper does not exist.
    """
    helper = getattr(chat_turn, "_conversation_grants", None)
    assert callable(helper), "chat_turn._conversation_grants is missing"
    reg = create_app(str(tmp_path)).state.platform.registry
    got = helper(reg, ["shell", "web_search", "no_such_tool", ""], {"shell", "read_file"})
    assert "shell" in got
    assert reg.get("shell").perm_key() in got
    assert "web_search" not in got, "granted but not armed this turn"
    assert "read_file" not in got, "armed but never granted"
    assert "no_such_tool" not in got and "" not in got
    assert helper(reg, [], {"shell"}) == set()
    assert helper(reg, None, {"shell"}) == set()
    # Grouped tools authorize on perm_key(), which differs from the name.
    grouped = next((n for n in sorted(reg.names()) if reg.get(n).perm_key() != n), None)
    if grouped is not None:
        assert reg.get(grouped).perm_key() in helper(reg, [grouped], {grouped})


def _calls_in(path: Path, func: str) -> set[str]:
    src = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    )
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_both_lanes_apply_the_grant_through_the_one_helper():
    """LOCK-STEP PIN: ``run_chat_turn`` (POST /chat) and ``chat_stream``
    (/chat/stream) each call ``_conversation_grants``. In the POST lane the
    grant is not observable through behaviour (every armed tool is already
    its own grant there), so this pin is what keeps that lane from silently
    dropping the field. Mutation check: delete either call and this goes red.

    RED TODAY: neither lane calls it.
    """
    post_src = Path(inspect.getsourcefile(chat_turn))
    stream_src = Path(inspect.getsourcefile(chat_routes))
    assert "_conversation_grants" in _calls_in(post_src, "run_chat_turn"), "POST /chat lane"
    assert "_conversation_grants" in _calls_in(stream_src, "chat_stream"), "/chat/stream lane"


def _grant_sinks(path: Path, func: str) -> set[str]:
    """Names the ``_conversation_grants`` result is UNIONED into inside
    ``func`` — directly (``x |= {... _conversation_grants(...) ...}`` /
    ``x.update(...)``) or through one local it was first bound to
    (``_g = {...}; x |= _g``). A call whose result is discarded or bound to
    a name nothing reads sinks nowhere."""
    src = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    )

    def _has_call(node: ast.AST) -> bool:
        return any(
            isinstance(c, ast.Call)
            and getattr(c.func, "id", getattr(c.func, "attr", "")) == "_conversation_grants"
            for c in ast.walk(node)
        )

    tainted: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and _has_call(n.value):
            tainted |= {t.id for t in n.targets if isinstance(t, ast.Name)}

    def _carries(node: ast.AST) -> bool:
        return _has_call(node) or any(
            isinstance(x, ast.Name) and x.id in tainted for x in ast.walk(node)
        )

    sinks: set[str] = set()
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.AugAssign)
            and isinstance(n.op, ast.BitOr)
            and isinstance(n.target, ast.Name)
            and _carries(n.value)
        ):
            sinks.add(n.target.id)
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "update"
            and isinstance(n.func.value, ast.Name)
            and any(_carries(a) for a in n.args)
        ):
            sinks.add(n.func.value.id)
    return sinks


def test_both_lanes_union_the_grant_into_the_grant_sets_and_nowhere_else():
    """Tightens the lock-step pin above (review M6): a lane that CALLS the
    helper and discards the result (``_unused = {...}``) kept that pin green,
    and in the POST lane no behaviour shows it. So follow the result: it must
    be unioned into ``armed_grant`` in both lanes and into ``card_grants`` in
    the stream lane (strict mode), and never into an ARMING set — a grant
    never arms."""
    post_src = Path(inspect.getsourcefile(chat_turn))
    stream_src = Path(inspect.getsourcefile(chat_routes))
    post = _grant_sinks(post_src, "run_chat_turn")
    stream = _grant_sinks(stream_src, "chat_stream")
    assert "armed_grant" in post, f"POST /chat lane drops the grant: sinks={post}"
    assert {"armed_grant", "card_grants"} <= stream, f"/chat/stream sinks={stream}"
    arming = {"armed", "ask_armed", "tool_specs", "allowed_names", "allowed"}
    assert not (post & arming), f"POST lane arms from a grant: {post & arming}"
    assert not (stream & arming), f"stream lane arms from a grant: {stream & arming}"
