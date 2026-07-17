"""Repetitive-work chat fixes: persisted thread setup, final-round tool
honesty, attachment truncation markers, usage kept when a later round fails."""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """Parse an SSE body into (event, data) tuples, skipping keepalive comments."""
    out: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = None
        data: dict | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data))
    return out


def _fake_invoke_ok(counter: dict):
    """A registry.invoke stand-in matching the chat loop's call shape."""

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None):
        counter["n"] += 1
        return ToolResult(ok=True, output="ok")

    return fake_invoke


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


# --- thread setup persistence -------------------------------------------------


def test_thread_setup_round_trip_and_list_flag(tmp_path):
    client = _client(tmp_path)
    r = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "hi"}],
        "setup": {
            "tools": ["read_file", "file_search"],
            "skill": "pixio-skill",
            "workspace_dir": "C:/work/proj",
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "junk": "ignored",           # unknown keys dropped, not stored
        },
    }).json()
    tid = r["id"]
    got = client.get(f"/chat/threads/{tid}").json()["setup"]
    assert got == {
        "tools": ["read_file", "file_search"],
        "skill": "pixio-skill",
        "workspace_dir": "C:/work/proj",
        "provider": "anthropic",
        "model": "claude-opus-4-8",
    }
    # A second, setup-less thread — the list flags exactly the one that has one.
    other = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "plain"}],
    }).json()["id"]
    flags = {t["id"]: t["has_setup"] for t in client.get("/chat/threads").json()["threads"]}
    assert flags[tid] is True and flags[other] is False


def test_thread_setup_caps_tools_and_rejects_non_object(tmp_path):
    client = _client(tmp_path)
    r = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "hi"}],
        "setup": {"tools": ["t1", "t2", 3, "t4", "t5", "t6", "t7", "t8"]},
    }).json()
    got = client.get(f"/chat/threads/{r['id']}").json()["setup"]
    # Non-strings dropped, then capped at the armed-tools MAX (6).
    assert got["tools"] == ["t1", "t2", "t4", "t5", "t6", "t7"]
    # A non-object setup is a client bug, not something to silently store/clear.
    bad = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "hi"}], "setup": "nope",
    })
    assert bad.status_code == 400


def test_thread_setup_survives_autosave_and_clears_on_null(tmp_path):
    client = _client(tmp_path)
    tid = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "hi"}],
        "setup": {"tools": ["read_file"], "provider": "mock"},
    }).json()["id"]
    # A plain autosave (no setup key) must NOT clobber the stored setup.
    client.put(f"/chat/threads/{tid}", json={
        "messages": [{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "hello"}],
    })
    assert client.get(f"/chat/threads/{tid}").json()["setup"] == {
        "tools": ["read_file"], "provider": "mock",
    }
    # An explicit null clears it (same contract as project_id).
    client.put(f"/chat/threads/{tid}", json={
        "messages": [{"role": "user", "content": "hi"}], "setup": None,
    })
    assert client.get(f"/chat/threads/{tid}").json()["setup"] == {}
    flags = {t["id"]: t["has_setup"] for t in client.get("/chat/threads").json()["threads"]}
    assert flags[tid] is False


# --- final-round tool honesty -------------------------------------------------


def test_final_round_tool_calls_not_executed(tmp_path, monkeypatch):
    """A model that wants tools on EVERY round: the 4th completion's calls must
    not execute (no round is left to read their results) — the reply says so."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    completions = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        completions["n"] += 1
        return RouteResult(
            LLMResponse(
                text="thinking…",
                tool_calls=[ToolCall(id=f"c{completions['n']}", name="image_info",
                                     arguments={"path": "x.png"})],
                usage={"input_tokens": 10, "output_tokens": 5},
            ),
            "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    invoked = {"n": 0}
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok(invoked))

    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200
    body = r.json()
    assert completions["n"] == 4     # the round budget was fully used…
    assert invoked["n"] == 3         # …but the 4th round's call did NOT execute
    assert "stopped after 3 tool rounds" in body["reply"]
    assert "1 tool call(s) not executed" in body["reply"]
    # The whole turn's usage (all 4 billed completions) reached the ledger.
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].steps == 4
    assert runs[0].input_tokens == 40 and runs[0].output_tokens == 20


def test_stream_final_round_tool_calls_not_executed(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    completions = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None):
        completions["n"] += 1
        resp = LLMResponse(
            text="thinking…",
            tool_calls=[ToolCall(id=f"c{completions['n']}", name="image_info",
                                 arguments={"path": "x.png"})],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        yield {"type": "text", "text": "thinking…"}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    invoked = {"n": 0}
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok(invoked))

    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    done = next(d for e, d in frames if e == "done")
    assert completions["n"] == 4 and invoked["n"] == 3
    assert "stopped after 3 tool rounds" in done["reply"]
    assert "1 tool call(s) not executed" in done["reply"]


# --- attachment truncation marker ---------------------------------------------


def test_attachment_truncation_marker(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    captured = {}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        captured["system"] = system
        return RouteResult(LLMResponse(text="read it"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)

    big = client.post("/documents/upload", json={
        "filename": "big.txt",
        "content_b64": base64.b64encode(b"x" * 7000).decode(),
    }).json()
    small = client.post("/documents/upload", json={
        "filename": "small.txt",
        "content_b64": base64.b64encode(b"y" * 100).decode(),
    }).json()
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "summarize"}],
        "attachments": [big["path"], small["path"]],
    })
    assert r.status_code == 200
    system = captured["system"]
    # The clipped extract carries an explicit marker with real numbers…
    assert "[attachment truncated: showing 6000 of 7000 chars]" in system
    # …and the small file (fully included) is NOT falsely marked.
    assert system.count("attachment truncated") == 1


# --- usage persisted when a later round fails ---------------------------------


def test_usage_persisted_when_round2_raises(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="image_info",
                                         arguments={"path": "x.png"})],
                    usage={"input_tokens": 111, "output_tokens": 22},
                ),
                "mock", "mock",
            )
        raise RuntimeError("provider fell over")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok({"n": 0}))

    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["image_info"],
    })
    # The client-facing error is unchanged…
    assert r.status_code == 502
    assert "provider fell over" in r.json()["detail"]
    # …but round 1's billed usage reached the ledger anyway, honestly FAILED.
    runs = _chat_runs(platform)
    assert len(runs) == 1
    assert runs[0].input_tokens == 111 and runs[0].output_tokens == 22
    assert runs[0].state.value == "failed"


def test_stream_usage_persisted_when_round2_raises(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None):
        n["i"] += 1
        if n["i"] == 1:
            resp = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="image_info",
                                     arguments={"path": "x.png"})],
                usage={"input_tokens": 7, "output_tokens": 3},
            )
            yield {"type": "final", "response": resp, "provider": "mock",
                   "model": "mock"}
            return
        raise RuntimeError("boom round 2")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok({"n": 0}))

    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    err = next(d for e, d in frames if e == "error")
    assert "boom round 2" in err["detail"]           # honest error frame
    runs = _chat_runs(platform)
    assert len(runs) == 1
    assert runs[0].input_tokens == 7 and runs[0].output_tokens == 3
    assert runs[0].state.value == "failed"
