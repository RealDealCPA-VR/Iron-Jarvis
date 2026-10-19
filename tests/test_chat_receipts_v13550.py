"""v1.355.0 lane 5A — the chat's receipts, round two (G29, G55, G62, G64).

What a new user must be able to read off a reply, each through the REAL
daemon (real app factory, real lanes, only the model call faked):

* G55 — when the model refused a long chat as too long and the daemon left
  older messages out to retry it, the reply SAYS how many
  (``refit: {"dropped": n}`` on the POST response and the done frame,
  lock-step); a turn that fit says nothing (``refit: null``).
* G64 — the cards the user ALLOWED this turn ride the done frame as
  ``approved: [{tool, decision, count?}]``; a refusal is not an approval;
  the POST lane (which never cards) answers ``approved: []``.
* G29 — a failed turn's error carries the ONE press that fixes it:
  ``fix_kind: "sign_in"`` + the CLI for a signed-out subscription CLI,
  ``fix_kind: "key"`` + the provider for a refused key, nothing otherwise.
* G62 — a remote that took a task with message-back OFF says plainly that
  its answer will not come back here (and which switch fixes it); one that
  can message back still says it will report back here.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.chat_turn as ct
from iron_jarvis.agents.threads import NO_REPLY_BACK_MARK, no_reply_back_line
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ProviderError, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_chat_approvals_v1187 import _ASK_MSG, _asgi_post, _drive_stream
from tests.test_failure_words_v13540 import OPENROUTER_401, _failing_router, _parse_sse


@pytest.fixture(autouse=True)
def _forget_learned():
    ct._LEARNED_WINDOWS.clear()
    yield
    ct._LEARNED_WINDOWS.clear()


def _history(n: int, size: int = 4000) -> list[dict]:
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * size})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * size})
    msgs.append({"role": "user", "content": "THE QUESTION"})
    return msgs


def _chars(messages) -> int:
    return sum(len(getattr(m, "content", "") or "") for m in messages)


# --------------------------------------------------------------------------- #
# G55 — leaving out older messages to fit is said on the reply.
# --------------------------------------------------------------------------- #
def test_post_lane_says_how_many_older_messages_it_left_out(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    sent: list[int] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        sent.append(len(messages))
        if _chars(messages) > 12_000:
            raise RuntimeError("This model's maximum context length is 6000 tokens.")
        return RouteResult(
            provider="mock", model="m",
            response=LLMResponse(text="fits now", finish_reason="stop"),
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": _history(6)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "fits now"
    assert len(sent) == 2
    # The number on the reply IS the number of older messages the retry did
    # not send (no tool rounds here, so nothing else was trimmed).
    assert body["refit"] == {"dropped": sent[0] - sent[1]}
    assert body["refit"]["dropped"] > 0


def test_stream_lane_says_it_on_the_done_frame(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    sent: list[int] = []

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        sent.append(len(messages))
        if _chars(messages) > 12_000:
            raise RuntimeError("prompt is too long: 9000 tokens > 6000 maximum")
        yield {"type": "text", "text": "streamed fine"}
        yield {"type": "final", "response": LLMResponse(text="streamed fine", finish_reason="stop"),
               "provider": "mock", "model": "m"}

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    r = client.post("/chat/stream", json={"messages": _history(6)})
    [done] = [d for ev, d in _parse_sse(r.text) if ev == "done"]
    assert len(sent) == 2
    assert done["refit"] == {"dropped": sent[0] - sent[1]}
    assert done["refit"]["dropped"] > 0


def test_a_turn_that_fit_says_nothing_on_either_lane(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    msgs = [{"role": "user", "content": "hello"}]
    post = client.post("/chat", json={"messages": msgs}).json()
    assert post["refit"] is None
    r = client.post("/chat/stream", json={"messages": msgs})
    [done] = [d for ev, d in _parse_sse(r.text) if ev == "done"]
    # ALWAYS present (null, never absent) — the two lanes cannot drift.
    assert "refit" in done and done["refit"] is None


def test_refit_receipt_reads_only_a_real_refit():
    assert ct.refit_receipt({}) is None
    assert ct.refit_receipt(None) is None
    assert ct.refit_receipt({"dropped": 12}) == {"dropped": 12}
    # A retry that only trimmed the turn's own tool output still happened.
    assert ct.refit_receipt({"dropped": 0}) == {"dropped": 0}


# --------------------------------------------------------------------------- #
# G64 — a chat reply records the approvals you gave.
# --------------------------------------------------------------------------- #
def _two_separate_shell_calls():
    """Two rounds, one shell call each — so a "once" answer cards twice."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        if rounds["n"] < 2:
            rounds["n"] += 1
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c{rounds['n']}", name="shell",
                         arguments={"command": f"echo call-{rounds['n']}"}),
            ])
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "done."}
            yield {"type": "final", "response": LLMResponse(text="done."),
                   "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_after_allowing_two_actions_the_reply_lists_both(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _two_separate_shell_calls()
    answers = iter(["once", "conversation"])

    async def allow(data):
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": next(answers)}
        )
        assert status == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, allow
    )
    asked = [d for ev, d in frames if ev == "approval"]
    assert len(asked) == 2, "each call must have been asked about"
    done = next(d for ev, d in frames if ev == "done")
    assert done["approved"] == [
        {"tool": "shell", "decision": "once"},
        {"tool": "shell", "decision": "conversation"},
    ]
    # Names only — never the command the user approved.
    assert "echo" not in json.dumps(done["approved"])


@pytest.mark.asyncio
async def test_a_refusal_is_not_an_approval(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _two_separate_shell_calls()
    answers = iter(["deny", "once"])

    async def answer(data):
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": next(answers)}
        )
        assert status == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, answer
    )
    done = next(d for ev, d in frames if ev == "done")
    assert done["approved"] == [{"tool": "shell", "decision": "once"}]
    assert "shell" in done["denied_tools"]


def test_approved_entry_keeps_only_answers_that_let_a_call_run():
    assert ct.approved_entry("write_file", "once") == {"tool": "write_file", "decision": "once"}
    assert ct.approved_entry("browser_click", "tab") == {"tool": "browser_click", "decision": "tab"}
    assert ct.approved_entry("shell", "always")["decision"] == "always"
    assert ct.approved_entry("write_file", "once", 3) == {
        "tool": "write_file", "decision": "once", "count": 3,
    }
    for refused in ("deny", "timeout", "", "maybe"):
        assert ct.approved_entry("shell", refused) is None, refused
    assert ct.approved_entry("", "once") is None


def test_the_post_lane_never_cards_so_it_lists_none(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    body = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    assert body["approved"] == []


# --------------------------------------------------------------------------- #
# G29 — the failure carries the press that fixes it.
# --------------------------------------------------------------------------- #
def test_a_signed_out_claude_code_turn_carries_the_sign_in_press(tmp_path, monkeypatch):
    from iron_jarvis.providers.adapters.claude_native.transport import signed_out_message

    exc = RuntimeError(signed_out_message("Not logged in · Please run /login", probe=False))
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, exc)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["title"] == "Claude Code isn't signed in"
    assert err["fix_kind"] == "sign_in" and err["fix_provider"] == "claude-cli"


def test_a_refused_key_carries_the_key_link_on_both_lanes(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, ProviderError(OPENROUTER_401, status_code=401))
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert (err["fix_kind"], err["fix_provider"]) == ("key", "openrouter")
    post = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert post.status_code == 502
    assert (post.json()["fix_kind"], post.json()["fix_provider"]) == ("key", "openrouter")


def test_a_failure_no_press_fixes_carries_no_press(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(
        client.app.state.platform, monkeypatch,
        ProviderError("openrouter API error 503: overloaded", status_code=503),
    )
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["title"]  # the headline is still there
    assert "fix_kind" not in err and "fix_provider" not in err


def test_a_cli_that_refused_the_sign_in_is_a_sign_in_press_not_a_key():
    exc = ProviderError("codex-cli API error 401: unauthorized", status_code=401)
    assert ct.fix_action(exc) == {"fix_kind": "sign_in", "fix_provider": "codex-cli"}
    assert ct.fix_action(RuntimeError("boom")) == {}


# --------------------------------------------------------------------------- #
# G62 — a remote with message-back off says its answer will not come back.
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _accepting_remote(monkeypatch):
    async def fake_post(self, url, json=None, headers=None, **kw):
        return _FakeResp(202, None, "queued")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _add_remote(client, name="hermes"):
    r = client.post(
        "/agents/remote",
        json={"name": name, "base_url": "http://192.168.1.50:8080/run", "kind": "http-task",
              "token": "t", "model": "m"},
    )
    assert r.status_code == 200, r.text


def test_message_back_off_says_plainly_the_answer_will_not_come_back(tmp_path, monkeypatch):
    _accepting_remote(monkeypatch)
    client = TestClient(create_app(str(tmp_path)))
    _add_remote(client)
    saved = client.put(
        "/chat/threads/new", json={"messages": [{"role": "user", "content": "@hermes research"}]}
    ).json()
    body = client.post(
        "/chat/panel",
        json={"message": "@hermes research", "chat_thread_id": saved["id"], "thread_id": saved["id"]},
    ).json()
    entry = body["entries"][-1]
    assert entry["content"] == no_reply_back_line("hermes")
    assert NO_REPLY_BACK_MARK in entry["content"]
    assert "Let it message back" in entry["content"]
    assert entry["pending"] is True and entry["reply_back"] is False
    assert "will report back" not in entry["content"]
    # The line the daemon appended to the chat says the same words.
    line = client.get(f"/chat/threads/{saved['id']}").json()["messages"][-1]
    assert NO_REPLY_BACK_MARK in line["content"]


def test_message_back_on_still_says_it_will_report_back_here(tmp_path, monkeypatch):
    _accepting_remote(monkeypatch)
    client = TestClient(create_app(str(tmp_path)))
    _add_remote(client)
    on = client.post("/agents/remote/hermes/inbound/enable", json={})
    assert on.status_code == 200, on.text
    body = client.post(
        "/chat/panel", json={"message": "@hermes research", "chat_thread_id": "c1"}
    ).json()
    entry = body["entries"][-1]
    assert "will report back here" in entry["content"]
    assert NO_REPLY_BACK_MARK not in entry["content"]
    assert "reply_back" not in entry
