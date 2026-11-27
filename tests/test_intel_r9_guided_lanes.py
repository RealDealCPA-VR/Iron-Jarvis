"""The intelligence plan, release 9: the guided form, through BOTH chat lanes.

Through the real app, the real lanes and the real router; only the model is a
stand-in (a model server on this PC that nobody measured). Every
tool-carrying reply records whether it came back usable; a model that
stumbled five times running is asked in the guided form — the same model —
and the reply carries one quiet line saying so. Nothing here sleeps.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.envelope.outcomes import STUMBLE_RUN, OutcomeLedger
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse

LANES = ("/chat", "/chat/stream")
MODEL = "small:3b"


class _OwnModel(LLMAdapter):
    """A model server on this PC: answers natively (the default capability),
    with no endpoint for the liveness probe to call."""

    provider = "ollama"

    def __init__(self, model: str | None = None, *, script: list[LLMResponse] | None = None) -> None:
        self.model = model or MODEL
        self.seen: list[dict] = []
        self.script = script or [LLMResponse(text="Here is what I found.")]

    async def complete(self, *, system, messages, tools, **kw):
        self.seen.append({"system": system, "tools": list(tools or [])})
        return self.script[min(len(self.seen), len(self.script)) - 1]


def _app(tmp_path, adapter: _OwnModel):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    # A port nothing listens on: the stand-in has no endpoint of its own, so
    # nothing is ever called there; the address only makes Ollama "connected".
    platform.config.ollama_base_url = "http://127.0.0.1:9"
    platform.providers.configure_local(ollama_base_url="http://127.0.0.1:9", ollama_model=MODEL)
    platform.providers.register("ollama", lambda model=None: adapter)
    return app


def _ask(app, lane: str, thread: str) -> str:
    body = {
        "messages": [{"role": "user", "content": "look in the notes folder"}],
        "thread_id": thread, "tools": ["read_file", "list_files"],
        "provider": "ollama", "model": MODEL,
    }
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text
    if lane == "/chat":
        return r.json()["reply"]
    frames = []
    for block in r.text.split(chr(10) * 2):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            frames.append(json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: "))))
    assert frames, r.text[-2000:]
    return frames[-1]["reply"]


def _stumble(app) -> None:
    for _ in range(STUMBLE_RUN):
        app.state.platform.providers.note_reply_form("ollama", MODEL, "native", False)


@pytest.mark.parametrize("lane", LANES)
def test_a_stumbling_model_is_guided_and_the_reply_says_so(tmp_path, lane):
    model = _OwnModel()
    app = _app(tmp_path, model)
    _stumble(app)
    reply = _ask(app, lane, f"t-guided-{lane}")
    assert chat_turn.GUIDED_LINE in reply
    assert reply.count(chat_turn.GUIDED_LINE) == 1
    # The same model answered, in the more guided form: no native tool specs,
    # the tool contract written into its instructions instead.
    assert model.seen and all(call["tools"] == [] for call in model.seen)
    assert "tool_call" in model.seen[0]["system"]


@pytest.mark.parametrize("lane", LANES)
def test_a_model_that_is_not_stumbling_gets_no_line_and_its_own_tool_calls(tmp_path, lane):
    model = _OwnModel()
    app = _app(tmp_path, model)
    reply = _ask(app, lane, f"t-plain-{lane}")
    assert chat_turn.GUIDED_LINE not in reply
    assert model.seen and model.seen[0]["tools"]  # native specs offered


@pytest.mark.parametrize("lane", LANES)
def test_every_tool_reply_records_whether_it_came_back_usable(tmp_path, lane):
    # A small model that WROTE its tool call as text instead of calling it.
    as_text = LLMResponse(text='{"name": "read_file", "arguments": {"path": "notes.txt"}}')
    model = _OwnModel(script=[as_text])
    app = _app(tmp_path, model)
    _ask(app, lane, f"t-record-{lane}")
    home = app.state.platform.config.home
    ledger = OutcomeLedger.load(home, "ollama", MODEL)
    counter = ledger.tool_protocols["native"]
    assert counter.failures >= 1 and counter.failures == counter.attempts
    assert ledger.fail_run == counter.failures


def test_a_cloud_model_is_never_recorded_or_guided(tmp_path):
    app = create_app(str(tmp_path / "home"))
    pm = app.state.platform.providers
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("openai", "gpt-x", "native", False)
    assert pm.reply_guided("openai", "gpt-x") is False
