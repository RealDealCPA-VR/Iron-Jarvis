"""v1.187.0 — chat asks, then proceeds.

The two halves of this mechanism predate it: ``PermissionEngine.authorize``
names the interactive session grant as the sanctioned lift for an ``ask``-tier
tool, and ``registry.invoke`` has carried ``deny_reason=`` since v1.155.0 for
"a caller that already asked a human and was refused". Nothing in chat ever
ASKED. A tool that resolved to ``ask`` was silently denied mid-turn, and
``shell``/``repl`` were never even SHOWN to the model, so the ask-then-proceed
experience could not begin.

What must hold, each mutation-proven:

* an ask-tier call PAUSES the stream on an ``approval`` frame, and the user's
  "once" answer lets exactly that call run;
* a "deny" answer refuses the call AND records the refusal — the model is told
  the user declined, and ``denied_tools`` carries it to the reply;
* nobody answering is an honest timeout-deny, never a hang and never a run;
* the ask-tier tools arm VISIBLE-BUT-UNGRANTED in the stream lane only — the
  headless lane serves callers with nobody present to answer, and arming a
  question no one can hear just manufactures denials;
* the route validates its input (400) and an unknown/expired id is a 404 —
  a double-click races the turn's cleanup and must read "already answered".

The interactive tests drive the REAL ``/chat/stream`` endpoint through a
HAND-ROLLED ASGI driver on one event loop — the only way to answer an approval
while the stream is genuinely paused on it. Both ``TestClient`` AND
``httpx.ASGITransport`` buffer the ENTIRE response before handing it over
(measured: each interactive test silently waited out the full 180s approval
timeout and then 404'd its own answer), so any client that "supports
streaming" must be checked for whether it streams *from an ASGI app* before
being trusted here.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import (
    ASK_TIER_TOOLS,
    AUTO_SAFE_TOOLS,
    select_ask_tools,
)

#: A message whose signals arm `shell` at the ask tier (run + command).
_ASK_MSG = "run the command `echo approved-run` in the terminal for me"


def _shell_call_stream(command: str = "echo approved-run"):
    """A router.stream stub: round 0 requests one shell call, round 1 answers."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        fake_stream.systems.append(system)
        fake_stream.tools.append([
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in (tools or [])
        ])
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="shell",
                                     arguments={"command": command})],
            )
            yield {"type": "final", "response": resp,
                   "provider": "mock", "model": "mock"}
        else:
            resp = LLMResponse(text="done.")
            yield {"type": "text", "text": "done."}
            yield {"type": "final", "response": resp,
                   "provider": "mock", "model": "mock"}

    fake_stream.systems = []
    fake_stream.tools = []
    return fake_stream


def _frames(raw: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs."""
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


def _scope(path: str, body: bytes) -> dict:
    """An HTTP POST scope with a LOOPBACK host — v1.175.0's DNS-rebinding
    guard rejects anything else, in tests exactly as in production."""
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8787"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


async def _asgi_post(app, path: str, payload: dict) -> tuple[int, bytes]:
    """One buffered in-process request (the approval answers ride this)."""
    body = json.dumps(payload).encode()
    sent_req = {"done": False}

    async def receive():
        if not sent_req["done"]:
            sent_req["done"] = True
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
    return status, b"".join(chunks)


async def _drive_stream(app, body: dict, decide):
    """Open /chat/stream and consume it AS IT STREAMS, handing each approval
    frame to *decide* (an async callback receiving the frame's data) while the
    turn is genuinely paused on it. Returns every parsed frame."""
    raw = json.dumps(body).encode()
    sent_req = {"done": False}

    async def receive():
        if not sent_req["done"]:
            sent_req["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        # The stream owns the connection until it finishes.
        await asyncio.sleep(3600)
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
                {get, task}, return_when=asyncio.FIRST_COMPLETED, timeout=60
            )
            if get in done:
                msg = get.result()
            else:
                get.cancel()
                if task in done:
                    break  # app finished and the queue is drained
                raise AssertionError("stream produced nothing for 60s")
            if msg["type"] == "http.response.start":
                assert msg["status"] == 200, msg
                continue
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for ev, data in _frames(block + "\n\n"):
                    frames.append((ev, data))
                    if ev == "approval":
                        await decide(data)
            if not msg.get("more_body", False):
                break
    finally:
        await task  # propagate any in-app failure honestly
    return frames


# --------------------------------------------------------------------------- #
# The interactive paths, end to end over the REAL endpoint.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_allow_once_pauses_then_runs_exactly_that_call(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _shell_call_stream()

    async def approve(data):
        assert data["tool"] == "shell"
        # The user sees the exact command before deciding — approving a call
        # you cannot read is not a decision.
        assert data["args"]["command"] == "echo approved-run"
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "once"}
        )
        assert status == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, approve
    )

    kinds = [ev for ev, _ in frames]
    assert "approval" in kinds, "the turn never paused to ask"
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "once"
    # The PAUSE precedes the run: approval before the started tool frame.
    assert kinds.index("approval") < kinds.index("tool_call")
    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is True, finished
    assert "approved-run" in finished.get("output", "")
    done = next(d for ev, d in frames if ev == "done")
    assert "shell" in (done.get("tools_used") or [])
    assert "shell" not in (done.get("denied_tools") or [])


@pytest.mark.asyncio
async def test_deny_refuses_and_the_refusal_is_the_users(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _shell_call_stream()

    async def deny(data):
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "deny"}
        )
        assert status == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, deny
    )

    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is False
    # The model reads WHO declined — "permission denied" plus the user's
    # decision, so it does not retry the exact call the user just refused.
    assert "permission denied" in finished.get("output", "")
    assert "declined" in finished.get("output", "")
    done = next(d for ev, d in frames if ev == "done")
    assert "shell" in (done.get("denied_tools") or [])
    assert "shell" not in (done.get("tools_used") or [])


@pytest.mark.asyncio
async def test_conversation_grant_covers_the_rest_of_the_turn(tmp_path):
    """One answer, two calls: 'conversation' widens armed_grant, so the second
    shell call in the SAME turn runs without a second card."""
    app = create_app(str(tmp_path))
    rounds = {"n": 0}

    async def two_call_stream(*, provider=None, model=None, system, messages,
                              tools, session_id=None, task_class=None):
        if rounds["n"] < 2:
            rounds["n"] += 1
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c{rounds['n']}", name="shell",
                         arguments={"command": f"echo call-{rounds['n']}"}),
            ])
            yield {"type": "final", "response": resp,
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "final", "response": LLMResponse(text="done."),
                   "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = two_call_stream

    answered = []

    async def grant_conversation(data):
        answered.append(data["id"])
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "conversation"}
        )
        assert status == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True},
        grant_conversation,
    )

    assert len(answered) == 1, "the second call must not re-ask"
    ran = [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("ok")
    ]
    assert len(ran) == 2, ran


def test_timeout_denies_honestly_instead_of_hanging(tmp_path, monkeypatch):
    """Nobody answers: the wait ends, the call is refused with the timeout
    named, and the stream COMPLETES. Patched on routes.chat — that module
    imported the name, so patching the source module would change nothing."""
    monkeypatch.setattr(chat_routes, "APPROVAL_TIMEOUT_S", 0.2)
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.router.stream = _shell_call_stream()

    r = client.post(
        "/chat/stream", json={"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}
    )
    assert r.status_code == 200
    frames = _frames(r.text)
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "timeout"
    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is False
    assert "timed out" in finished.get("output", "")


# --------------------------------------------------------------------------- #
# Arming: visible in the stream lane, ungranted, and headless-lane silent.
# --------------------------------------------------------------------------- #


def test_ask_tier_arms_in_the_stream_lane_prompt_and_specs(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_routes, "APPROVAL_TIMEOUT_S", 0.2)
    client = TestClient(create_app(str(tmp_path)))
    fake = _shell_call_stream()
    client.app.state.platform.router.stream = fake

    client.post(
        "/chat/stream", json={"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}
    )

    assert "shell" in fake.tools[0], "the model never saw the verb"
    assert "APPROVAL-GATED" in fake.systems[0]
    assert "will PAUSE this turn" in fake.systems[0]


def test_the_headless_lane_neither_arms_nor_asks(tmp_path, monkeypatch):
    """POST /chat serves callers with nobody present (the comm poller, the
    phone). The same message that arms shell on the stream lane must arm
    nothing here — a question no one can hear is just a manufactured denial."""
    client = TestClient(create_app(str(tmp_path)))
    seen: dict = {}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        seen["system"] = system
        seen["tools"] = [
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in (tools or [])
        ]
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    monkeypatch.setattr(client.app.state.platform.router, "complete", fake_complete)

    r = client.post("/chat", json={"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True})
    assert r.status_code == 200
    assert "shell" not in seen["tools"]
    assert "APPROVAL-GATED" not in seen["system"]


def test_ask_tier_never_leaks_into_the_auto_allow_set():
    """The two vocabularies have opposite security meaning: one becomes
    grants, the other becomes questions. A tool in both would be silently
    granted by the auto-arm path and the card would never appear."""
    assert not (ASK_TIER_TOOLS & AUTO_SAFE_TOOLS)
    assert "shell" in ASK_TIER_TOOLS and "repl" in ASK_TIER_TOOLS


def test_select_ask_tools_needs_a_host_signal_not_an_errand():
    # The host named: arms.
    assert "shell" in select_ask_tools("run the script deploy.ps1 for me")
    assert "shell" in select_ask_tools("git pull and install the deps")
    assert "shell" in select_ask_tools("open a powershell and check the path")
    # Everyday office words: never arms — "run" alone is an errand.
    assert select_ask_tools("run through the numbers on this return") == []
    assert select_ask_tools("can you start the letter to the IRS") == []
    assert select_ask_tools("summarize the attached 1099") == []


# --------------------------------------------------------------------------- #
# The route's contract.
# --------------------------------------------------------------------------- #


def test_the_approvals_route_validates_and_404s_the_expired(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    # Unknown id — expired, answered, or invented: "already answered", 404.
    r = client.post("/chat/approvals/apr_nope", json={"decision": "once"})
    assert r.status_code == 404
    # A decision outside the vocabulary is the caller's bug, said plainly.
    r = client.post("/chat/approvals/apr_nope", json={"decision": "always"})
    assert r.status_code == 400
    assert "once" in r.json()["detail"]
