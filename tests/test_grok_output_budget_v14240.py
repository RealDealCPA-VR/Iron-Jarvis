"""v1.424.0 (audit D7): Grok as a chat provider — no fixed 4,096-token output
cap, and a reply cut at its output limit says so.

THE CAP IS THE GROK CLI'S OWN. ``tests/fixtures/grok_cli_requests_v14240.json``
was written by the REAL installed grok CLI (``IJ_REGEN_GROK_REQUESTS=1``, this
PC only): a scratch ``GROK_HOME`` whose config points a model at a stand-in
Responses server on this PC, which records each request body (no model call,
no sign-in, auto-update off). With ``max_completion_tokens`` unset the CLI's
request carries NO ``max_output_tokens``; set, it carries that number. The
built-in models' entries in the CLI's own model list leave it unset.

A CUT REPLY: the Responses stream of an answer that ran out of output tokens
ends with ``response.incomplete`` (``incomplete_details.reason:
max_output_tokens``). It used to raise; it is an answer with
``finish_reason="max_tokens"`` now (the chat's ``truncated`` + Continue), and
a tool call cut mid-arguments is named in ``cut_calls`` and never run.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters import grok_cli as G
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.reply_budget import cut_off_round
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

FIXTURE = Path(__file__).parent / "fixtures" / "grok_cli_requests_v14240.json"
SESSION = {"token": "tok-test", "base_url": "https://cli-chat-proxy.grok.com/v1",
           "expires_at": "2999-01-01T00:00:00Z", "version": "1.0.70"}

#: The two configs the CLI was run with (the stand-in's port is filled in).
CONFIGS: dict[str, str] = {
    "unset": '[cli]\nauto_update = false\n\n[model.fake]\nmodel = "fake-model"\n'
             'base_url = "http://127.0.0.1:{port}/v1"\nname = "Fake"\napi_key = "dummy-not-a-key"\n'
             'api_backend = "responses"\n',
    "set": '[cli]\nauto_update = false\n\n[model.fake]\nmodel = "fake-model"\n'
           'base_url = "http://127.0.0.1:{port}/v1"\nname = "Fake"\napi_key = "dummy-not-a-key"\n'
           'api_backend = "responses"\nmax_completion_tokens = 1234\n',
}


# --------------------------------------------------------------------------- #
# Regeneration: the REAL grok CLI's requests (this PC only)
# --------------------------------------------------------------------------- #
class _Recorder(http.server.BaseHTTPRequestHandler):
    bodies: list[dict[str, Any]] = []

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "fake-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        # Only the request's SHAPE is kept (never the prompt, tools or ids).
        type(self).bodies.append({k: req[k] for k in ("model", "max_output_tokens", "stream", "store")
                                  if k in req})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        usage = {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6,
                 "input_tokens_details": {"cached_tokens": 0},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        resp = {"id": "resp_1", "object": "response", "status": "completed", "model": req.get("model"),
                "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "pong", "annotations": []}]}],
                "usage": usage}
        for evt in ({"type": "response.output_text.delta", "delta": "pong", "item_id": "m1",
                     "output_index": 0, "content_index": 0},
                    {"type": "response.completed", "response": resp}):
            self.wfile.write(("data: " + json.dumps(evt) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _run_cli(tmp: Path, config: str) -> list[dict[str, Any]]:
    exe = shutil.which("grok")
    assert exe, "the grok CLI is not installed"
    _Recorder.bodies = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        home, work = tmp / "home", tmp / "work"
        home.mkdir(parents=True)
        work.mkdir()
        (home / "config.toml").write_bytes(config.format(port=port).encode("utf-8"))
        env = {**os.environ, "GROK_HOME": str(home), "GROK_DISABLE_AUTOUPDATER": "1"}
        subprocess.run([exe, "-p", "say pong", "-m", "fake", "--output-format", "json", "--no-auto-update"],
                       cwd=str(work), env=env, capture_output=True, timeout=120)
    finally:
        srv.shutdown()
    return [b for b in _Recorder.bodies if b.get("model") == "fake-model"]


def _builtin_caps() -> dict[str, Any]:
    """The CLI's own model list on this PC: each built-in model's
    ``max_completion_tokens`` (model ids and numbers only)."""
    cache = json.loads((Path.home() / ".grok" / "models_cache.json").read_text(encoding="utf-8"))
    return {mid: (entry.get("info") or {}).get("max_completion_tokens")
            for mid, entry in sorted(cache["models"].items())}


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> dict[str, Any]:
    if os.environ.get("IJ_REGEN_GROK_REQUESTS") == "1":
        tmp = tmp_path_factory.mktemp("grok-regen")
        version = subprocess.run([shutil.which("grok") or "grok", "--version"], capture_output=True,
                                 text=True, timeout=60).stdout.strip()
        data = {"grok": version.split(" (")[0], "builtin_caps": _builtin_caps(),
                "requests": {name: _run_cli(tmp / name, cfg) for name, cfg in CONFIGS.items()}}
        FIXTURE.write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Fakes: the proxy, answering a canned SSE body (post) or stream (stream)
# --------------------------------------------------------------------------- #
def _sse(events: list[dict[str, Any]]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    async def aiter_lines(self):
        for line in self.text.split("\n"):
            yield line

    async def aread(self):
        return self.text.encode()


class FakeProxy:
    def __init__(self, text: str) -> None:
        self._text = text
        self.bodies: list[dict[str, Any]] = []

    async def post(self, url, *, headers=None, json=None):
        self.bodies.append(json or {})
        return _Resp(self._text)

    @asynccontextmanager
    async def stream(self, method, url, *, headers=None, json=None):
        self.bodies.append(json or {})
        yield _Resp(self._text)


def _whole_text() -> str:
    return _sse([{"type": "response.output_text.delta", "delta": "Hi"},
                 {"type": "response.completed", "response": {
                     "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 1},
                     "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]}}])


def _cut_text(text: str = "The first half of the ans") -> str:
    return _sse([{"type": "response.output_text.delta", "delta": text},
                 {"type": "response.incomplete", "response": {
                     "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                     "usage": {"input_tokens": 9, "output_tokens": 7},
                     "output": [{"type": "message", "status": "incomplete",
                                 "content": [{"type": "output_text", "text": text}]}]}}])


def _cut_call() -> str:
    whole = {"type": "function_call", "status": "completed", "call_id": "c1", "name": "read_file",
             "arguments": json.dumps({"path": "a.py"})}
    half = {"type": "function_call", "status": "incomplete", "call_id": "c2", "name": "write_file",
            "arguments": '{"path": "big.py", "content": "def main():\\n    pri'}
    return _sse([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {}, "output": [{"type": "message", "content": [{"type": "output_text", "text": "Writing it."}]},
                                whole, half]}}])


def _adapter(proxy: FakeProxy, model: str = "grok-4.7", **kw) -> G.GrokCliAdapter:
    return G.GrokCliAdapter(model=model, http=proxy, session_provider=lambda: dict(SESSION), **kw)


def _complete(adapter, tools=None):
    return asyncio.run(adapter.complete(system="", messages=[LLMMessage("user", "hi")], tools=tools or []))


def _home(tmp_path: Path, config: str = "", cache: dict | None = None) -> Path:
    home = tmp_path / "grokhome"
    home.mkdir(parents=True, exist_ok=True)
    if config:
        (home / "config.toml").write_bytes(config.encode("utf-8"))
    if cache is not None:
        (home / "models_cache.json").write_bytes(json.dumps(cache).encode("utf-8"))
    return home


# --------------------------------------------------------------------------- #
# 1. The cap is the Grok CLI's own
# --------------------------------------------------------------------------- #
def test_the_grok_cli_sends_a_cap_only_when_its_config_sets_one(recorded):
    assert recorded["grok"].startswith("grok 1.0")
    unset, cap_set = recorded["requests"]["unset"], recorded["requests"]["set"]
    assert unset and cap_set
    assert all("max_output_tokens" not in b for b in unset)
    assert all(b.get("max_output_tokens") == 1234 for b in cap_set)
    # The CLI's own built-in models leave it unset.
    assert recorded["builtin_caps"] and all(v is None for v in recorded["builtin_caps"].values())


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_iron_jarvis_sends_what_the_grok_cli_sends(recorded, name, tmp_path, monkeypatch):
    """The same config in the Grok home the adapter reads: the same cap, or
    none — byte for byte the CLI's choice."""
    monkeypatch.setenv("GROK_HOME", str(_home(tmp_path, CONFIGS[name].format(port=9))))
    proxy = FakeProxy(_whole_text())
    _complete(_adapter(proxy, model="fake-model"))
    want = recorded["requests"][name][0]
    assert ("max_output_tokens" in proxy.bodies[0]) == ("max_output_tokens" in want)
    assert proxy.bodies[0].get("max_output_tokens") == want.get("max_output_tokens")


def test_no_fixed_4096_cap_without_a_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(_home(tmp_path)))
    proxy = FakeProxy(_whole_text())
    res = _complete(_adapter(proxy))
    assert "max_output_tokens" not in proxy.bodies[0]
    assert "max_tokens" not in res.extra
    # The stream path builds the same request.
    asyncio.run(_drain(_adapter(proxy).stream(system="", messages=[LLMMessage("user", "hi")], tools=[])))
    assert "max_output_tokens" not in proxy.bodies[-1]


def test_the_clis_own_order_model_then_server_list_then_global(tmp_path):
    cache = {"models": {"grok-4.7": {"info": {"id": "grok-4.7", "model": "grok-4.7",
                                              "max_completion_tokens": 30000}},
                        "grok-4.5": {"info": {"id": "grok-4.5", "max_completion_tokens": None}}}}
    home = _home(tmp_path, '[models]\nmax_completion_tokens = 8000\n\n'
                           '[model."grok-4.7"]\nmax_completion_tokens = 60000\n'
                           '[model.mine]\nmodel = "grok-4.6"\nmax_completion_tokens = 12000\n', cache)
    assert G.output_cap("grok-4.7", home) == 60000  # the user's own model entry
    assert G.output_cap("grok-4.6", home) == 12000  # matched by its `model`
    assert G.output_cap("grok-4.5", home) == 8000  # the server left it unset -> [models]
    home2 = _home(tmp_path / "b", "", cache)
    assert G.output_cap("grok-4.7", home2) == 30000  # the server's own list
    assert G.output_cap("grok-4.5", home2) is None  # nothing set anywhere: no cap


@pytest.mark.parametrize("bad", ["0", "-5", "true", '"8192"', "8192.5"])
def test_a_value_that_is_not_a_positive_whole_number_sets_no_cap(tmp_path, bad):
    home = _home(tmp_path, f"[models]\nmax_completion_tokens = {bad}\n")
    assert G.output_cap("grok-4.7", home) is None


def test_a_broken_config_sets_no_cap_and_never_raises(tmp_path):
    home = _home(tmp_path, "[models\nthis is not toml")
    (home / "models_cache.json").write_bytes(b"{not json")
    assert G.output_cap("grok-4.7", home) is None


def test_a_leased_account_reads_its_own_grok_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(_home(tmp_path, "[models]\nmax_completion_tokens = 5000\n")))
    other = _home(tmp_path / "acct", "[models]\nmax_completion_tokens = 7000\n")
    adapter = _adapter(FakeProxy(_whole_text()))
    assert adapter._cap_for(None) == 5000
    assert adapter._cap_for(SimpleNamespace(home=str(other))) == 7000


def test_a_number_the_caller_passes_is_sent_and_stamped(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(_home(tmp_path, "[models]\nmax_completion_tokens = 5000\n")))
    proxy = FakeProxy(_whole_text())
    res = _complete(_adapter(proxy, max_tokens=20000))
    assert proxy.bodies[0]["max_output_tokens"] == 20000
    assert res.extra["max_tokens"] == 20000


# --------------------------------------------------------------------------- #
# 2. A cut reply says so
# --------------------------------------------------------------------------- #
async def _drain(agen) -> list[dict]:
    return [f async for f in agen]


def test_an_answer_cut_at_the_limit_is_an_answer_that_says_so():
    res = _complete(_adapter(FakeProxy(_cut_text())))
    assert res.text == "The first half of the ans"
    assert res.finish_reason == "max_tokens" and res.cut_calls == [] and not res.tool_calls
    assert res.usage == {"input_tokens": 9, "output_tokens": 7}


def test_a_completed_event_with_an_incomplete_response_says_so_too():
    body = _sse([{"type": "response.completed", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "half"}]}]}}])
    res = _complete(_adapter(FakeProxy(body)))
    assert res.text == "half" and res.finish_reason == "max_tokens"


def test_a_tool_call_cut_mid_arguments_is_named_and_never_run():
    res = _complete(_adapter(FakeProxy(_cut_call())), tools=[{"name": "write_file"}, {"name": "read_file"}])
    assert [c.name for c in res.tool_calls] == ["read_file"]  # whole: runs
    assert res.cut_calls == ["write_file"]  # half-written: named, never run
    assert res.finish_reason == "max_tokens"
    assert cut_off_round(res, has_tools=True, tool_names=["write_file", "read_file"]) == ["write_file"]


def test_unclosed_arguments_are_a_cut_whatever_the_item_says():
    body = _sse([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {},
        "output": [{"type": "function_call", "call_id": "c9", "name": "write_file",
                    "arguments": '{"path": "a.py", "content": "x'}]}}])
    res = _complete(_adapter(FakeProxy(body)), tools=[{"name": "write_file"}])
    assert res.tool_calls == [] and res.cut_calls == ["write_file"]


def test_another_incomplete_reason_is_still_an_honest_error():
    body = _sse([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []}}])
    with pytest.raises(RuntimeError, match="response.completed"):
        _complete(_adapter(FakeProxy(body)))


def test_a_whole_answer_is_unchanged():
    res = _complete(_adapter(FakeProxy(_whole_text())))
    assert res.text == "Hi" and res.finish_reason == "stop" and res.cut_calls == []


def test_the_stream_says_it_was_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(_home(tmp_path, "[models]\nmax_completion_tokens = 900\n")))
    proxy = FakeProxy(_cut_text())
    frames = asyncio.run(_drain(_adapter(proxy).stream(
        system="", messages=[LLMMessage("user", "hi")], tools=[])))
    assert proxy.bodies[-1]["max_output_tokens"] == 900  # the stream asks with the same cap
    assert [f["text"] for f in frames if f["type"] == "text"] == ["The first half of the ans"]
    final = frames[-1]["response"]
    assert final.finish_reason == "max_tokens" and final.extra["max_tokens"] == 900


# --------------------------------------------------------------------------- #
# 3. The chat offers Continue (both lanes, the real Grok parser)
# --------------------------------------------------------------------------- #
_ASK = {"messages": [{"role": "user", "content": "write it all"}]}


def _done(text: str) -> dict:
    frames = [json.loads(b.split("data:", 1)[1]) for b in text.split("\n\n") if b.startswith("event: done\n")]
    assert frames, text[-800:]
    return frames[-1]


def test_both_chat_lanes_say_a_cut_grok_reply_was_cut(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path / "home")))
    platform = client.app.state.platform

    async def grok_stream(**kw):
        async for frame in _adapter(FakeProxy(_cut_text())).stream(
                system="", messages=[LLMMessage("user", "hi")], tools=[]):
            if frame.get("type") == "final":
                frame = {**frame, "provider": "grok-cli", "model": "grok-4.7"}
            yield frame

    async def grok_complete(**kw):
        resp = await _adapter(FakeProxy(_cut_text())).complete(
            system="", messages=[LLMMessage("user", "hi")], tools=[])
        return RouteResult(resp, "grok-cli", "grok-4.7")

    async def _invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    monkeypatch.setattr(platform.router, "stream", grok_stream)
    monkeypatch.setattr(platform.router, "complete", grok_complete)
    monkeypatch.setattr(platform.registry, "invoke", _invoke)
    done = _done(client.post("/chat/stream", json=_ASK).text)
    assert done["truncated"] is True and done["reply"].startswith("The first half")
    body = client.post("/chat", json=_ASK).json()
    assert body["truncated"] is True and body["reply"].startswith("The first half")
