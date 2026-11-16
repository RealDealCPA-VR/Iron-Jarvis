"""v1.503.0 (north-star fix plan 3.12) — "permission denied" in a tool's own
output is not the owner's No, and a card nobody answered is not one either.

Before: ``chat_turn.refusal_label`` read ANY failure whose text CONTAINED
"permission denied" as the user's refusal. A command whose program printed
those words (a git push over SSH, a database, a locked file) was listed as
declined by the user, got no fix button, and switched the check-again loop
off for the rest of the reply. The registry writes its own refusals with a
prefix (``tools/registry.py``: ``f"{label}: {reason}"``); only that prefix is
read now, as the other labels already were.

And a card that TIMED OUT in the stream lane was refused with the same
"permission denied" label (the footnote said the user declined). It now
carries the agent runtime's own label for it, "paused", and the footnote says
the card was not answered in time.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import REFUSAL_FOOTNOTE_WORDS, refusal_footnotes, refusal_label
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult


# --------------------------------------------------------------------------- #
# The label.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("error", [
    "git push failed: git@example.invalid: permission denied (publickey).",
    "git push failed: git@example.invalid: Permission denied (publickey).",
    "open /srv/data/ledger.db: permission denied",
    "PermissionError: [Errno 13] Permission denied: 'C:/work/out.xlsx'",
    "psql: error: permission denied for table invoices",
    "exit 1\nmkdir: cannot create directory 'x': Permission denied",
])
def test_a_tools_own_permission_denied_is_an_ordinary_failure(error):
    assert refusal_label(error) == ""


@pytest.mark.parametrize("error", [
    "permission denied: you declined this call when asked",
    "permission denied: shell is set to Deny here",
])
def test_the_registrys_own_refusal_keeps_its_label(error):
    assert refusal_label(error) == "permission denied"


def test_a_timed_out_card_has_its_own_true_label_and_words():
    assert refusal_label("paused: the approval request timed out with no answer") == "paused"
    assert "paused" in REFUSAL_FOOTNOTE_WORDS
    line = refusal_footnotes([("shell", "paused")])
    assert "not answered in time" in line, line
    assert "permission denied" not in line and "declined" not in line, line


# --------------------------------------------------------------------------- #
# Both lanes: a tool whose output says "permission denied".
# --------------------------------------------------------------------------- #


class _Scripted(LLMAdapter):
    """A real-model stand-in: round 1 calls *call*, then answers *final*."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, call: tuple[str, dict], final: str) -> None:
        self.call, self.final, self.n = call, final, 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.n += 1
        if self.n == 1:
            return LLMResponse(text="", tool_calls=[ToolCall(id="c1", name=self.call[0], arguments=self.call[1])])
        return LLMResponse(text=self.final)


def _stream_done(client: TestClient, body: dict) -> dict:
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    done = None
    for block in r.text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            done = json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
    assert done is not None, r.text[-1500:]
    return done


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_tool_printing_permission_denied_is_not_listed_as_declined(tmp_path, lane, monkeypatch):
    app = create_app(str(tmp_path))
    model = _Scripted(("image_info", {"path": "x.png"}), "The picture could not be read.")
    app.state.platform.providers.get = lambda prov, m=None, _m=model: _m
    calls: list[str] = []

    async def fake_invoke(name, args, ctx, permissions, overrides=None, **kw):
        calls.append(name)
        return ToolResult(ok=False, error="cannot open x.png: permission denied")

    monkeypatch.setattr(app.state.platform.registry, "invoke", fake_invoke)
    body = {"messages": [{"role": "user", "content": "what size is x.png"}], "tools": ["image_info"]}
    client = TestClient(app)
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
    else:
        out = _stream_done(client, body)
    assert calls == ["image_info"], "anti-vacuity: the call reached the registry"
    assert out["denied_tools"] == [], out["denied_tools"]
    assert "(permission denied)" not in out["reply"], out["reply"]


# --------------------------------------------------------------------------- #
# The stream lane: a card nobody answered.
# --------------------------------------------------------------------------- #


_ASK_MSG = "run the command `echo approved-run` in the terminal for me"


def _shell_call_stream():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None,
                          task_class=None, **_kw):
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="shell",
                                                             arguments={"command": "python -c \"print('approved-run')\""})])
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "It did not run."}
            yield {"type": "final", "response": LLMResponse(text="It did not run."),
                   "provider": "mock", "model": "mock"}

    return fake_stream


def test_stream_a_timed_out_card_is_not_the_users_no(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_routes, "CHAT_ASK_TIMEOUT_S", 0.2)
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.router.stream = _shell_call_stream()
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True})
    assert r.status_code == 200
    frames = []
    for block in r.text.split("\n\n"):
        ev, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            try:
                frames.append((ev, json.loads("\n".join(data))))
            except ValueError:
                pass
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "timeout", "anti-vacuity: the card really timed out"
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is False and "timed out" in finished.get("output", ""), finished
    assert finished["output"].startswith("paused:"), finished["output"]
    done = next(d for ev, d in frames if ev == "done")
    assert done["denied_tools"] == [], done["denied_tools"]
    assert "not answered in time" in done["reply"], done["reply"]
    assert "(permission denied)" not in done["reply"], done["reply"]


def test_a_card_nobody_answered_still_ends_the_check_again_loop():
    """A timed-out card is not the owner's No, but nobody is there to answer
    another one: the check-again loop treats it like a declined call (both
    lanes pass it beside ``denied_tools``), so the reply never waits on a
    second card nobody will see."""
    assert chat_turn.until_done_denied(["write_file"], [("shell", "paused")]) == ["write_file", "shell"]
    assert chat_turn.until_done_denied([], [("shell", "paused")]) == ["shell"]
    assert chat_turn.until_done_denied([], [("shell", "not armed")]) == []
    assert chat_turn.until_done_denied([], []) == []
    # Both lanes hand the loop this list, never `denied_tools` alone.
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"
    for f in (root / "chat_turn.py", root / "routes" / "chat.py"):
        src = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "denied=denied_tools," not in src, f
        assert src.count("until_done_denied(denied_tools, refused_tools)") == 1, f
