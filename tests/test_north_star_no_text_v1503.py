"""v1.503.0 (north-star fix plan 3.8) — "no tool ran" is said only when none
ran.

``chat_turn._no_text_reply`` (both chat lanes) is the reply when the model
wrote nothing even after being asked once. It used to branch on the LAST
tool's OUTPUT TEXT: a call that changes things and prints nothing (a shell
move, a delete, a new folder, a rename) left that text empty, and the reply
said "The model returned an empty answer — no tool ran and nothing was
written. Press Retry" — untrue, and a Retry would run the change again.

Now it branches on the calls this reply made (succeeded and failed): it says
what ran in plain words and that it returned nothing, and after a step that
may change something it never says a bare "Press Retry". The agent runtime's
twin (``agents/runtime._no_final_text_result``) was already honest.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.change_claims import claimed_change_note
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

NO_TOOL = "no tool ran"


def test_a_change_that_printed_nothing_is_never_no_tool_ran():
    out = chat_turn._no_text_reply(["rename_file"], "")
    assert NO_TOOL not in out, out
    assert out.startswith(chat_turn.NO_TEXT_LEAD), out
    assert "rename or move a file" in out, out  # what ran, in the card's words
    assert "returned nothing" in out, out
    # A retry would rename again: never the bare "Press Retry".
    assert "Press Retry, or pick" not in out, out
    assert "Check what changed" in out, out


def test_a_command_that_printed_nothing_says_it_ran():
    out = chat_turn._no_text_reply(["shell", "shell"], "   ")
    assert NO_TOOL not in out and "run a command" in out, out
    assert out.count("run a command") == 1, out
    assert "Check what changed" in out, out


def test_a_read_that_returned_nothing_may_be_retried():
    """Only reads ran: asking again repeats nothing, so Retry stays."""
    out = chat_turn._no_text_reply(["list_files"], "")
    assert NO_TOOL not in out and "look in a folder" in out, out
    assert "Press Retry, or pick a different model." in out, out


def test_a_call_that_failed_is_named_and_is_not_no_tool_ran():
    out = chat_turn._no_text_reply([], "", failed=["write_file"])
    assert NO_TOOL not in out, out
    assert "did not succeed" in out and "save a file" in out, out


def test_a_success_and_a_failure_are_both_said():
    out = chat_turn._no_text_reply(["list_files"], "", failed=["rename_file"])
    assert "look in a folder" in out and "rename or move a file" in out, out
    assert "did not succeed" in out, out
    assert "Check what changed" in out, out  # the failed rename may have run part-way


def test_nothing_at_all_keeps_the_old_words_and_a_tool_output_keeps_its_quote():
    assert chat_turn._no_text_reply([], "") == (
        "The model returned an empty answer — no tool ran and nothing was "
        "written. Press Retry, or pick a different model."
    )
    out = chat_turn._no_text_reply(["image_info"], "PNG 2x2")
    # v1.516.0 (second review L8): the step in the card's words, not its name.
    assert out.endswith("PNG 2x2") and "It ran one step: read a file." in out
    assert "image_info" not in out


def test_the_claim_notes_never_judge_the_apps_own_words():
    """The new words start with the lead the claim checks skip (it quotes a
    tool, never the model's claim)."""
    out = chat_turn._no_text_reply(["config_set"], "")
    assert claimed_change_note(out, [], [], reply_local_only=True) == ""
    assert chat_turn._claimed_write_note(out, []) == ""


# --------------------------------------------------------------------------- #
# Both lanes, a real-model stand-in: the call runs, prints nothing, and the
# model writes nothing twice (its answer and the one nudge).
# --------------------------------------------------------------------------- #


class _Silent(LLMAdapter):
    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, call: tuple[str, dict]) -> None:
        self.call, self.n = call, 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.n += 1
        if self.n == 1:
            return LLMResponse(text="", tool_calls=[ToolCall(id="c1", name=self.call[0], arguments=self.call[1])])
        return LLMResponse(text="")


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


def _lane(tmp_path, monkeypatch, lane, *, ok: bool):
    app = create_app(str(tmp_path))
    model = _Silent(("image_info", {"path": "x.png"}))
    app.state.platform.providers.get = lambda prov, m=None, _m=model: _m
    calls: list[str] = []

    async def fake_invoke(name, args, ctx, permissions, overrides=None, **kw):
        calls.append(name)
        return ToolResult(ok=True, output="") if ok else ToolResult(ok=False, error="boom: it broke")

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
    return out


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_call_that_ran_and_printed_nothing_is_said(tmp_path, monkeypatch, lane):
    out = _lane(tmp_path, monkeypatch, lane, ok=True)
    assert out["tools_used"] == ["image_info"], out["tools_used"]
    assert NO_TOOL not in out["reply"], out["reply"]
    assert out["reply"].startswith(chat_turn.NO_TEXT_LEAD), out["reply"]
    assert "returned nothing" in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_call_that_failed_is_said(tmp_path, monkeypatch, lane):
    out = _lane(tmp_path, monkeypatch, lane, ok=False)
    assert out["tools_used"] == [], out["tools_used"]
    assert NO_TOOL not in out["reply"], out["reply"]
    assert "did not succeed" in out["reply"], out["reply"]
