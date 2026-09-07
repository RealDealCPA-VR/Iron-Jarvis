"""A running chat turn can be stopped BY NAME, from somewhere else entirely.

THE SILENT FAILURE THIS FILE GUARDS: a Stop button that does nothing.

Until v1.241.0 stopping a streamed turn was CONNECTION-BOUND. The only
cooperative check was ``request.is_disconnected()`` once per tool round
(``routes/chat.py``), and mid-generation stop worked solely because Starlette
cancels the response generator when the HTTP client goes away. That is enough
for the dashboard, which holds the stream in the tab that owns the button —
and it is *nothing at all* for a caller that has no connection to drop. A
browser side panel asks the daemon to run the turn on its behalf
(``docs/BROWSER-SIDEBAR-PLAN.md`` §4, F3), so "hang up" is not a gesture it can
make: its Stop would have rendered, clicked, and changed nothing while the
turn kept generating and kept billing.

So the turn is now addressable: an OPTIONAL caller-chosen ``turn_id`` on
``ChatBody`` registers it in ``core.turns.TURNS`` for its lifetime, and
``POST /chat/turns/{turn_id}/stop`` stops it from any connection or none.
Purely additive — a turn with no ``turn_id`` registers nothing and behaves as
it did before, which the last test here pins frame for frame.

DRIVEN THROUGH A HAND-ROLLED ASGI DRIVER, for the reason
``test_chat_approvals_v1187`` and ``test_chat_stream_cancel_ledger_v1192``
both document: ``TestClient`` AND ``httpx.ASGITransport`` buffer the WHOLE SSE
response, so neither can act on a stream while a round is genuinely in flight.
Here the stop request rides a SECOND, INDEPENDENT in-process connection while
the first one is still streaming — which is the entire point, and the reason
the streaming ``receive()`` below parks on a 3600s sleep instead of returning
``http.disconnect``: if it ever disconnected, the old connection-bound path
would stop the turn and every assertion here would pass for the wrong reason.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

#: Round 1's billed usage — the tokens a stop must not lose from the ledger.
_R1_IN, _R1_OUT = 77, 13

_FIRST = "first-token"
_SECOND = "second-token-after-the-stop"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-local by design (see ``core/turns.py``), so a
    leaked id would let one test stop another's turn."""
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
                out.append((event, json.loads("\n".join(data_lines))))
            except ValueError:
                pass
    return out


def _scope(path: str, body: bytes, *, token: str = "") -> dict:
    """A LOOPBACK-host POST scope — v1.175.0's DNS-rebinding guard rejects
    anything else, in tests exactly as in production."""
    headers = [
        (b"host", b"127.0.0.1:8787"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


async def _asgi_post(app, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """One buffered in-process request — A SECOND, INDEPENDENT CONNECTION.

    This is not a convenience: the whole claim under test is that a turn can
    be stopped by someone who is not holding the stream, so the stop must
    arrive down a socket the streaming turn knows nothing about.
    """
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

    await app(_scope(path, body), receive, send)
    raw = b"".join(chunks)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw.decode("utf-8", "replace")}


async def _drive_stream(app, body: dict, on_frame=None):
    """Open /chat/stream and consume it AS IT STREAMS, handing every frame to
    *on_frame* (an async callback) while the turn is genuinely running.

    ``receive()`` NEVER disconnects — it parks. Any stop observed here is
    therefore the registry's doing, not the old connection-bound path.
    """
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)          # the stream owns the connection
        return {"type": "http.disconnect"}

    q: asyncio.Queue = asyncio.Queue()

    async def send(msg):
        await q.put(msg)

    task = asyncio.create_task(app(_scope("/chat/stream", raw), receive, send))
    frames: list[tuple[str, dict]] = []
    buf = ""
    try:
        while True:
            get = asyncio.create_task(q.get())
            done, _ = await asyncio.wait(
                {get, task}, return_when=asyncio.FIRST_COMPLETED, timeout=30
            )
            if get in done:
                msg = get.result()
            else:
                get.cancel()
                if task in done:
                    break
                raise AssertionError("stream produced nothing for 30s")
            if msg["type"] == "http.response.start":
                assert msg["status"] == 200, msg
                continue
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for ev, data in _sse_frames(block + "\n\n"):
                    frames.append((ev, data))
                    if on_frame is not None:
                        await on_frame(ev, data)
            if not msg.get("more_body", False):
                break
    finally:
        await task          # propagate any in-app failure honestly
    return frames


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


def _arm_fake_tool(app) -> None:
    """Round 2 only happens if round 1's tool call runs; keep it deterministic
    and off the filesystem."""

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = fake_invoke


def _two_round_stream(gate: asyncio.Event):
    """Round 1 completes (billed, one tool call). Round 2 streams ``_FIRST``,
    then WAITS on *gate* — the window in which the stop lands — and only then
    offers ``_SECOND`` and its ``final``.

    So ``_SECOND`` reaching the client is proof the stop did NOT halt the
    token loop, and its absence is proof it did.
    """
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        if rounds["n"] == 0:
            rounds["n"] += 1
            yield {
                "type": "final",
                "response": LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="read_file",
                                         arguments={"path": "notes.txt"})],
                    usage={"input_tokens": _R1_IN, "output_tokens": _R1_OUT},
                ),
                "provider": "mock", "model": "mock",
            }
        else:
            yield {"type": "text", "text": _FIRST}
            await gate.wait()
            yield {"type": "text", "text": _SECOND}
            yield {
                "type": "final",
                "response": LLMResponse(text=_FIRST + _SECOND, usage={}),
                "provider": "mock", "model": "mock",
            }

    return fake_stream


_ASK = {"messages": [{"role": "user", "content": "read my notes"}],
        "tools": ["read_file"], "auto_tools": False}


def _body(**over) -> dict:
    return {**_ASK, **over}


# --------------------------------------------------------------------------- #
# (1) THE ACCEPTANCE ROW — stopped from a second connection, CANCELLED written.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_turn_is_stopped_from_a_second_connection_and_ledgered_cancelled(
    tmp_path,
):
    """docs/BROWSER-SIDEBAR-PLAN.md §7 row 5, and the whole reason Ship 2 exists.

    The streaming connection is never dropped (see ``_drive_stream``), so the
    pre-v1.241.0 mechanism cannot account for anything asserted here.
    """
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _two_round_stream(gate)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == _FIRST:
            # The turn is running, its connection is wide open, and the stop
            # arrives down a DIFFERENT one.
            assert TURNS.is_running("turn-A"), "the turn never became addressable"
            seen["stop"] = await _asgi_post(app, "/chat/turns/turn-A/stop")
            gate.set()

    frames = await _drive_stream(app, _body(turn_id="turn-A"), on_frame)

    assert seen["stop"] == (200, {"ok": True, "stopped": True}), seen

    kinds = [ev for ev, _ in frames]
    tokens = [d.get("text") for ev, d in frames if ev == "token"]
    assert _FIRST in tokens
    assert _SECOND not in tokens, "the turn kept generating after Stop"
    assert "done" not in kinds, "a stopped turn must not deliver a finished answer"

    # ...and the rounds the provider ALREADY billed are still counted, once,
    # as CANCELLED — through the one `_persist_once` writer.
    runs = _chat_runs(app.state.platform)
    assert len(runs) == 1, [(r.state, r.input_tokens) for r in runs]
    assert runs[0].state == AgentState.CANCELLED
    assert (runs[0].input_tokens, runs[0].output_tokens) == (_R1_IN, _R1_OUT)

    # The runner popped its own id, so a second press is honestly a 404.
    assert not TURNS.is_running("turn-A")
    assert (await _asgi_post(app, "/chat/turns/turn-A/stop"))[0] == 404


# --------------------------------------------------------------------------- #
# (2) ANTI-VACUITY — the same turn, unstopped, runs to completion.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_without_the_stop_the_same_turn_finishes(tmp_path):
    """Without this, test (1) would pass just as well against a turn that
    could never reach ``_SECOND`` for some unrelated reason."""
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _two_round_stream(gate)

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == _FIRST:
            gate.set()          # release, but never stop

    frames = await _drive_stream(app, _body(turn_id="turn-B"), on_frame)

    tokens = [d.get("text") for ev, d in frames if ev == "token"]
    assert tokens == [_FIRST, _SECOND], tokens
    done = next(d for ev, d in frames if ev == "done")
    assert done["reply"] == _FIRST + _SECOND
    assert _chat_runs(app.state.platform)[0].state == AgentState.COMPLETED


# --------------------------------------------------------------------------- #
# (3) MID-TOKEN-STREAM — the halt is inside the token loop, not at a boundary.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stop_halts_mid_token_stream_before_the_answer_completes(tmp_path):
    """A round-boundary-only check would let the model finish the whole answer
    and emit ``done``; the user would watch their Stop do nothing until the
    paragraph ended. The stop here lands in round 2 with tokens still to come,
    and NO ``final`` frame is ever consumed for that round.
    """
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _two_round_stream(gate)

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == _FIRST:
            await _asgi_post(app, "/chat/turns/turn-C/stop")
            gate.set()

    frames = await _drive_stream(app, _body(turn_id="turn-C"), on_frame)

    kinds = [ev for ev, _ in frames]
    assert kinds.count("round") == 2, kinds        # it DID reach round 2
    assert "done" not in kinds
    texts = [d.get("text") for ev, d in frames if ev == "token"]
    assert texts == [_FIRST], texts


# --------------------------------------------------------------------------- #
# (3b) THE ROUND-TOP SEAM — the one `request.is_disconnected()` used to own.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stop_during_a_tool_call_prevents_the_next_round_starting(tmp_path):
    """The predicate must be consulted WHERE THE DISCONNECT CHECK WAS, too.

    A stop pressed while a tool is running has no token loop to land in — the
    turn is parked in ``registry.invoke``. The round-TOP check is what stops
    it, and it stops it BEFORE round 2's ``round`` frame; a lane that only
    checked inside the token loop would announce a round it had already been
    told not to run.

    HONESTY, pinned here on purpose: the tool that was already executing
    still RAN and still returned. Stop prevents the NEXT round, not the call
    in flight (v1.228.0) — the ``tool_call`` finished frame below is that
    fact, asserted rather than hidden.
    """
    app = create_app(str(tmp_path))
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _two_round_stream(gate)
    ran = []

    async def stopping_invoke(name, args, ctx, permissions, overrides=None, *,
                              session_allow=None, **kw):
        ran.append(name)
        await _asgi_post(app, "/chat/turns/turn-F/stop")
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = stopping_invoke

    frames = await _drive_stream(app, _body(turn_id="turn-F"))

    kinds = [ev for ev, _ in frames]
    assert ran == ["read_file"], ran
    assert kinds.count("round") == 1, (
        f"round 2 was announced after the stop: {kinds}"
    )
    assert "done" not in kinds
    finished = next(d for ev, d in frames
                    if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is True, "the in-flight tool call was not aborted"
    assert _chat_runs(app.state.platform)[0].state == AgentState.CANCELLED


# --------------------------------------------------------------------------- #
# (4) AN UNKNOWN OR FINISHED ID IS A 404, NEVER A SILENT SUCCESS.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unknown_turn_id_is_a_404(tmp_path):
    """"I stopped it" from a route that stopped nothing is the exact lie this
    ship exists to remove."""
    app = create_app(str(tmp_path))
    status, payload = await _asgi_post(app, "/chat/turns/never-existed/stop")
    assert status == 404, (status, payload)
    assert "never-existed" in str(payload.get("detail", ""))


@pytest.mark.asyncio
async def test_a_finished_turn_id_is_a_404(tmp_path):
    """The runner pops its own id in a ``finally``; a click that races the end
    of the turn must read as "already finished"."""
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _two_round_stream(gate)

    frames = await _drive_stream(app, _body(turn_id="turn-D"))
    assert any(ev == "done" for ev, _ in frames), "the turn did not finish"

    assert not TURNS.is_running("turn-D")
    status, _ = await _asgi_post(app, "/chat/turns/turn-D/stop")
    assert status == 404


# --------------------------------------------------------------------------- #
# (5) NO turn_id ⇒ EXACTLY TODAY'S BEHAVIOUR.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_no_turn_id_registers_nothing_and_streams_identically(tmp_path):
    """The change is purely additive or it is not shippable: the dashboard
    sends no ``turn_id`` and must be untouched, frame kinds and ``done``
    payload included."""
    app_a = create_app(str(tmp_path / "a"))
    app_b = create_app(str(tmp_path / "b"))
    gates = {}
    for key, app in (("a", app_a), ("b", app_b)):
        _arm_fake_tool(app)
        gates[key] = asyncio.Event()
        app.state.platform.router.stream = _two_round_stream(gates[key])

    # WHAT WAS REGISTERED *WHILE THE TURN WAS RUNNING* — checking after it has
    # finished proves nothing, because the runner releases its id on the way
    # out and a server-minted id would look exactly the same by then.
    live: dict[str, list[str]] = {}

    def _watch(key):
        async def on_frame(ev, data):
            if ev == "token" and data.get("text") == _FIRST:
                live[key] = TURNS.running_ids()
                gates[key].set()
        return on_frame

    with_id = await _drive_stream(app_a, _body(turn_id="turn-E"), _watch("a"))
    without = await _drive_stream(app_b, _body(), _watch("b"))

    assert live["a"] == ["turn-E"], live
    assert live["b"] == [], (
        f"an unnamed turn registered {live['b']} — the daemon must never mint "
        f"a turn_id: an id the caller did not choose has nobody to use it"
    )
    assert TURNS.running_ids() == []

    assert [ev for ev, _ in with_id] == [ev for ev, _ in without]
    done_a = next(d for ev, d in with_id if ev == "done")
    done_b = next(d for ev, d in without if ev == "done")
    assert sorted(done_a) == sorted(done_b)
    assert done_a["reply"] == done_b["reply"] == _FIRST + _SECOND
    # ...and no `turn_id` leaked into the terminal frame: the SSE contract did
    # not move, so no client has to learn a new key.
    assert "turn_id" not in done_a


# --------------------------------------------------------------------------- #
# (6) REACHABLE THROUGH THE REAL create_app(), WITH THE REAL BEARER.
#     This repo shipped a route no packaged install could reach while 90 tests
#     built on a bare FastAPI() were green (v1.238.0). Not again.
# --------------------------------------------------------------------------- #
def test_the_stop_route_is_reachable_and_bearer_guarded_in_a_real_install(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "install-secret")
    client = TestClient(create_app(str(tmp_path)))

    # No bearer: refused by TokenAuthMiddleware. The id is deliberately a REAL
    # running one, so a 401 cannot be the 404 wearing a different number — and
    # so a future exemption for /chat/turns/* fails right here.
    TURNS.register("turn-live")
    r = client.post("/chat/turns/turn-live/stop")
    assert r.status_code == 401, r.text
    assert TURNS.is_running("turn-live"), "an unauthenticated call stopped it"

    hdr = {"Authorization": "Bearer install-secret"}
    r = client.post("/chat/turns/turn-live/stop", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "stopped": True}

    r = client.post("/chat/turns/no-such-turn/stop", headers=hdr)
    assert r.status_code == 404, r.text


def test_the_stop_route_is_not_on_any_auth_exemption_list():
    """``turn_id`` is a name the caller chose, not a credential. Exempting the
    route would make a guessable string enough to interrupt somebody's work."""
    from iron_jarvis.daemon.auth import _is_exempt

    assert not _is_exempt("/chat/turns/turn-live/stop")
    assert not _is_exempt("/chat/stream")
