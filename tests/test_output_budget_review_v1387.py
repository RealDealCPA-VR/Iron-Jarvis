"""v1.387.0 REVIEW — the reply budget and the cut-off recovery, attacked.

Each test here was RED on the doer's code (or on the merge with master's
Phase 3) before the fix it pins; the file says what each one pins. Driven
through the REAL lanes, the REAL adapters against a fake server
(``tests/fixtures/fake_openai_endpoint_v1387.py``) and the REAL agent runtime;
only the model is scripted. No live model call, no real endpoint.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_cut_off_recovery_v1387 import (  # noqa: F401 — `env` is a fixture
    NOTE_HEAD,
    _body,
    _cut,
    _lane,
    _write,
    env,
)


@pytest.fixture(autouse=True)
def _forget_learned():
    """What servers taught the adapters is process memory: every test starts
    clean and leaves nothing behind."""
    from iron_jarvis.providers import reply_budget

    reply_budget.forget_learned()
    yield
    reply_budget.forget_learned()


# --------------------------------------------------------------------------- #
# 1. THE FINISH CHECK hears the model's ANSWER, never a cut-off reply.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_cut_right_after_the_finish_note_is_not_heard_as_the_answer(env, lane):
    """RED on the doer's code: ``_finish.heard()`` ran BEFORE the cut-off
    branch, so a reply cut off right after the finish note was taken as the
    model's point-by-point answer — an EMPTY one (0 unmet) — and the real
    answer that followed the cut-off note was never heard: the record said
    every point was met while the model had said one was not."""
    platform, ws, wire = env
    model = wire([
        _write("calc.py", "X = 1\n"),
        "calc.py is written.",
        # the finish note's answer is cut off at the output limit …
        _cut(),
        # … and after the cut-off note the model answers it for real
        "1. met: calc.py sets X.\n2. not met: there is no test file yet.",
    ])
    done = await _lane(
        lane, platform,
        _body(ws, "Write calc.py that sets X to 1. Then add a test file for it."),
    )
    assert any(NOTE_HEAD in str(u) for u in model.users()), "the cut-off note was sent"
    record = done.get("finish")
    assert record, done.keys()
    assert record["unmet"] == 1, record
    assert "not met: there is no test file yet" in done["reply"]


# --------------------------------------------------------------------------- #
# 2. A HALF-PARSED CALL IS NEVER RUN — the OpenAI stream's two holes.
# --------------------------------------------------------------------------- #
class _Lines:
    """An httpx-style streamed response: ``aiter_lines`` over SSE lines."""

    def __init__(self, chunks: list) -> None:
        self._lines: list[str] = []
        for c in chunks:
            self._lines.append(c if isinstance(c, str) else f"data: {json.dumps(c)}")
            self._lines.append("")

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _delta(d: dict, finish: "str | None" = None) -> dict:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def _call_chunks(name: str, args: str) -> list[dict]:
    out = [_delta({"tool_calls": [{"index": 0, "id": "call_0", "type": "function",
                                   "function": {"name": name, "arguments": ""}}]})]
    for i in range(0, len(args), 20):
        out.append(_delta({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + 20]}}]}))
    return out


async def _consume(chunks: list):
    from iron_jarvis.providers.adapters.openai import OpenAIAdapter

    adapter = OpenAIAdapter(model="m", api_key="k", base_url="http://box.test/v1/chat/completions",
                            provider_name="custom")
    frames = [f async for f in adapter._consume_chat_stream(_Lines(chunks))]
    return frames[-1]["response"]


HALF_WRITE = '{"path": "calc.py", "content": "class Calc:\n    def add(self, a'


async def test_an_in_stream_error_is_raised_never_parsed_as_the_answer():
    """RED on the doer's code: a LiteLLM proxy that fails MID-STREAM sends
    ``data: {"error": {...}}`` and ends; the chunk has no ``choices`` and was
    skipped, so the half-written call came back as a ``write_file`` with
    ``{}`` arguments and ``finish_reason`` "tool_use" — and a half-written
    TEXT answer read as a finished one. Now it is a ProviderError in the
    server's own words (a 5xx: transient, like any server failure)."""
    from iron_jarvis.providers.adapters.base import ProviderError

    chunks = [
        *_call_chunks("write_file", HALF_WRITE),
        {"error": {"message": "litellm.Timeout: Connection timed out after 600.0 seconds.",
                   "type": "timeout", "code": "408"}},
    ]
    with pytest.raises(ProviderError) as info:
        await _consume(chunks)
    assert "Connection timed out after 600.0 seconds" in str(info.value)
    assert info.value.status_code == 408

    with pytest.raises(ProviderError):
        await _consume([_delta({"content": "The answer is"}),
                        {"error": {"message": "upstream reset", "code": 500}}])


async def test_a_stream_that_ends_inside_a_call_without_a_finish_reason_never_runs_it():
    """RED on the doer's code (its own open point): a stream that stopped in
    the middle of a call's arguments WITHOUT saying why (no finish_reason —
    a dropped proxy connection) was read as "tool_calls" and the half call
    went through the lenient JSON ladder. Now nothing from it runs: it is an
    interrupted answer, raised in plain words."""
    from iron_jarvis.providers.adapters.base import ProviderError

    with pytest.raises(ProviderError) as info:
        await _consume(_call_chunks("write_file", HALF_WRITE))
    assert "write_file" in str(info.value) and "nothing" in str(info.value)


async def test_a_whole_call_without_a_finish_reason_still_arrives_unchanged():
    """Unchanged: a server that never sends a finish_reason but closes its
    calls (or writes slightly broken but WHOLE JSON) still gets them run."""
    final = await _consume(_call_chunks("write_file", '{"path": "a.py", "content": "x",}'))
    assert [c.name for c in final.tool_calls] == ["write_file"]
    assert final.tool_calls[0].arguments == {"path": "a.py", "content": "x"}
    assert final.cut_calls == [] and final.finish_reason == "tool_use"


# --------------------------------------------------------------------------- #
# 3. ANTHROPIC-COMPATIBLE, NOT STREAMED: the call the limit stopped in.
# --------------------------------------------------------------------------- #
async def test_a_non_streamed_anthropic_reply_cut_inside_its_last_call_never_runs_it():
    """RED on the doer's code (its own open point): a Messages reply with
    ``stop_reason: "max_tokens"`` whose LAST block is a ``tool_use`` — the
    block the limit stopped the model in; the server handed back whatever
    it had parsed — was run with those (partial) arguments. Now it is a cut
    call, reported by name; a call FOLLOWED by another block was finished and
    still runs."""
    import httpx

    from tests.test_reply_budget_v1387 import WRITE_SPEC, _anthropic_adapter, _msgs

    def reply(content):
        return lambda req, n: httpx.Response(200, json={
            "type": "message", "content": content, "stop_reason": "max_tokens",
            "usage": {"input_tokens": 9, "output_tokens": 4096}})

    half = {"type": "tool_use", "id": "toolu_1", "name": "write_file",
            "input": {"path": "calc.py", "content": "class Calc:\n    def add(self, a"}}
    adapter, _ = _anthropic_adapter(reply([{"type": "text", "text": "Writing it."}, half]))
    final = await adapter.complete(system="s", messages=_msgs("hi"), tools=[WRITE_SPEC])
    assert final.tool_calls == [] and final.cut_calls == ["write_file"]
    assert final.finish_reason == "max_tokens"

    # a STRING input that does not close is cut too, wherever it sits
    stringy = {**half, "input": '{"path": "calc.py", "content": "cla'}
    adapter, _ = _anthropic_adapter(reply([stringy, {"type": "text", "text": "…"}]))
    final = await adapter.complete(system="s", messages=_msgs("hi"), tools=[WRITE_SPEC])
    assert final.tool_calls == [] and final.cut_calls == ["write_file"]

    done = {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "a.py"}}
    adapter, _ = _anthropic_adapter(reply([done, {"type": "text", "text": "and then I ran out"}]))
    final = await adapter.complete(system="s", messages=_msgs("hi"), tools=[WRITE_SPEC])
    assert [c.name for c in final.tool_calls] == ["read_file"] and final.cut_calls == []


# --------------------------------------------------------------------------- #
# 4. A TOOL CALL LEAKED AS TEXT AT THE LIMIT (GLM under vLLM, not streamed).
# --------------------------------------------------------------------------- #
GLM_LEAK = (
    "I'll write the module now.\n"
    "<tool_call>write_file\n<arg_key>path</arg_key>\n<arg_value>calc.py</arg_value>\n"
    "<arg_key>content</arg_key>\n<arg_value>class Calc:\n    def add(self, a"
)
HERMES_LEAK = (
    'Writing it.\n<tool_call>\n{"name": "write_file", "arguments": {"path": "calc.py", '
    '"content": "class Calc:\n    def add(self, a'
)


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("harness", ["generic", None])
@pytest.mark.parametrize("leak", [GLM_LEAK, HERMES_LEAK])
async def test_a_call_leaked_as_text_at_the_limit_is_a_cut_call(env, lane, harness, leak):
    """RED on the doer's code (its own open point): vLLM's GLM/Hermes tool
    parser hands a call it never saw close back as TEXT. At the output limit
    that text is a CALL the model was writing, not a long answer: the doer's
    lanes read it as `truncated` (a plain turn ended with the half call shown
    and a Continue) or, in a harness turn, sent the text-call nudge ("call
    the tool itself") — so the model tried the whole file in ONE call again.
    Now it is a cut call: the note (write in parts), the markup never given
    back to the model or kept in the reply."""
    from iron_jarvis.harness.mode import TEXT_TOOL_CALL_NUDGE

    platform, ws, wire = env
    model = wire([
        LLMResponse(text=leak, finish_reason="max_tokens"),
        _write("calc.py", "X = 1\n"),
        "calc.py is written.",
    ])
    # (a plain chat turn arms what the page sends: a project chat's file tools)
    body = _body(ws) if harness else _body(ws, tools=["read_file", "write_file", "edit_file"])
    done = await _lane(lane, platform, body, harness=harness)
    (note,) = [u for u in model.users() if str(u).startswith(NOTE_HEAD)]
    assert "before its write_file call was complete" in note
    assert TEXT_TOOL_CALL_NUDGE not in model.users()
    shown = [m.content for m in model.calls[1]["messages"] if m.role == "assistant"]
    assert not any("<tool_call>" in str(c) for c in shown), shown
    assert (ws / "calc.py").read_text("utf-8") == "X = 1\n"
    assert done["truncated"] is False
    assert "<tool_call>" not in done["reply"]
    assert done["route"]["cut_off"] == {"times": 1, "recovered": True}


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_turn_that_ends_on_a_leaked_call_keeps_no_markup_in_its_reply(env, lane):
    platform, ws, wire = env
    wire([LLMResponse(text=GLM_LEAK, finish_reason="max_tokens")] * 3)
    done = await _lane(lane, platform, _body(ws))
    assert done["route"]["cut_off"] == {"times": 3, "recovered": False}
    assert "<tool_call>" not in done["reply"] and "<arg_key>" not in done["reply"]
    assert "cut off at its output limit" in done["reply"]


# --------------------------------------------------------------------------- #
# 5. THE BOUND IS ON CUTS IN A ROW — a recovered cut is not a strike.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_three_recovered_cuts_in_one_long_reply_do_not_end_it(env, lane):
    """RED on the doer's code: the notes were counted per TURN, so a long
    Build reply (no step limit) whose model hit the limit on three different
    files — and recovered each time, writing it in parts — was ENDED at the
    third cut with "cut off … 3 times in a row" (they were not in a row).
    The bound is now on cuts IN A ROW (nothing but cuts between them); a
    turn still ends at a generous total so a model that never stops being
    cut cannot run forever."""
    platform, ws, wire = env
    model = wire([
        _cut(cut_calls=["write_file"]), _write("a.py", "A = 1\n", "w1"),
        _cut(cut_calls=["write_file"]), _write("b.py", "B = 1\n", "w2"),
        _cut(cut_calls=["write_file"]), _write("c.py", "C = 1\n", "w3"),
        "All three are written.",
    ])
    done = await _lane(lane, platform, _body(ws))
    notes = [m for m in model.calls[-1]["messages"]
             if m.role == "user" and str(m.content).startswith(NOTE_HEAD)]
    assert len(notes) == 3
    for name in ("a.py", "b.py", "c.py"):
        assert (ws / name).exists(), name
    assert done["reply"].startswith("All three are written.")
    assert done["route"]["cut_off"] == {"times": 3, "recovered": True}


def test_an_agent_run_counts_cuts_in_a_row_too(tmp_path):
    from tests.test_cut_off_recovery_v1387 import _agent_run

    def w(path, cid):
        return LLMResponse(tool_calls=[ToolCall(id=cid, name="write_file",
                                                arguments={"path": path, "content": "x"})],
                           finish_reason="tool_use")

    session, seen = _agent_run(tmp_path, [
        _cut(cut_calls=["write_file"]), w("a.txt", "w1"),
        _cut(cut_calls=["write_file"]), w("b.txt", "w2"),
        _cut(cut_calls=["write_file"]), w("c.txt", "w3"),
        LLMResponse(text="Wrote a, b and c."),
    ])
    assert (session.summary or "").startswith("Wrote a, b and c."), session.summary


def test_the_honest_end_says_in_a_row_only_when_they_were():
    from iron_jarvis.providers.reply_budget import cut_off_end_text

    assert "3 times in a row" in cut_off_end_text(["write_file"], 3)
    total = cut_off_end_text(["write_file"], 8, in_a_row=False)
    assert "8 times in this reply" in total and "in a row" not in total


# --------------------------------------------------------------------------- #
# 6. A PICTURE IS NOT 100,000 TOKENS OF TEXT.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [False, True])
async def test_a_picture_does_not_shrink_the_reply_below_the_old_budget(tmp_path, stream):
    """RED on the doer's code: the request estimate counted a picture's
    base64 as TEXT (a 300 KB photo ≈ 100k "tokens"), so on a KNOWN 32k
    window a question about one photo asked for the 1,024-token floor —
    a QUARTER of the 4,096 every local request had before. A server counts
    a picture as a bounded number of tokens; so does the estimate now."""
    import base64

    from iron_jarvis.providers.adapters.base import LLMMessage
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, text_reply
    from tests.test_reply_budget_v1387 import MODEL, _platform

    photo = base64.b64encode(bytes(range(256)) * 1200).decode()  # ~400 KB of base64
    msgs = [LLMMessage(role="user", content="what is in this photo?",
                       images=[{"data_b64": photo, "media_type": "image/png"}])]
    with FakeEndpoint(lambda b: text_reply("a cat")) as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 32_000})
        adapter = platform.providers.get("custom", MODEL)
        if stream:
            async for _ in adapter.stream(system="s", messages=msgs, tools=[]):
                pass
        else:
            await adapter.complete(system="s", messages=msgs, tools=[])
        body = srv.chats()[-1]
    assert body["max_tokens"] >= 4_096, body["max_tokens"]
    assert body["max_tokens"] <= 32_000


def test_the_estimate_counts_a_picture_as_a_bounded_number_of_tokens():
    from iron_jarvis.providers.reply_budget import IMAGE_TOKENS, request_tokens

    data = "A" * 400_000
    openai_wire = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}]}]
    anthropic_wire = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}]}]
    for wire in (openai_wire, anthropic_wire):
        n = request_tokens("", wire, None)
        assert IMAGE_TOKENS <= n <= IMAGE_TOKENS + 200, n


# --------------------------------------------------------------------------- #
# 7. THE RETRY READS THE WHOLE REFUSAL, not the 300 characters a screen shows.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [False, True])
async def test_a_refusal_whose_numbers_sit_past_the_display_cut_is_still_read(tmp_path, monkeypatch, stream):
    """RED on the doer's code: the retry parsed `_error_detail(resp)`, cut at
    300 characters for DISPLAY. A proxy that stacks its own prefixes in
    front of vLLM's words pushes the window past that cut; the refusal then
    read as "budget refused, no number" and the endpoint was answered with —
    and REMEMBERED at — the old 4,096 for the rest of the process. Now the
    retry reads the whole refusal and asks exactly what the server says
    fits."""
    from tests.fixtures import fake_openai_endpoint_v1387 as fake
    from tests.test_reply_budget_v1387 import MODEL, _ask, _platform

    long_words = (
        "litellm.ContextWindowExceededError: litellm.BadRequestError: Hosted_vllmException - "
        "Error code: 400 - {{'object': 'error', 'message': \"Received Model Group=glm-5.3-flash, "
        "deployment=hosted_vllm/zai-org/GLM-5.3-Flash-FP8 on spark-a (Available Model Group "
        "Fallbacks=None, context_window_fallbacks=None) — 'max_tokens' or 'max_completion_tokens' "
        "is too large: {asked}. This model's maximum context length is {ctx} tokens and your "
        "request has {prompt} input tokens ({asked} > {ctx} - {prompt}).\", 'code': 400}}"
    )
    assert long_words.index("maximum context length") > 300
    monkeypatch.setitem(fake.REFUSALS, "litellm_long", long_words)
    from iron_jarvis.providers import reply_budget

    ctx = 24_000 if stream else 3_000  # (not streamed: it asks at most 4,096)
    with fake.FakeEndpoint(lambda b: fake.text_reply("ok"), context_len=ctx,
                           refusal="litellm_long") as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 200_000})
        await _ask(platform, stream=stream)
        bodies = srv.chats()
    assert bodies[0]["max_tokens"] == (32_768 if stream else 4_096)
    second = bodies[1]["max_tokens"]
    assert srv.prompt_tokens(bodies[1]) + second <= ctx
    assert second == ctx - srv.prompt_tokens(bodies[1]) - 16
    assert reply_budget.learned(srv.url, MODEL).get("output_cap") is None


# --------------------------------------------------------------------------- #
# 8. THE COST OF A BIGGER BUDGET — what bounds one model call.
# --------------------------------------------------------------------------- #
class _SlowServer:
    """An OpenAI-compatible server whose answer RUNS AWAY: one token every
    0.1 s for a minute (a looping generation under a 32,768-token budget).
    It records when the client went away (its write fails)."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self
        self.started = threading.Event()
        self.gone = threading.Event()
        self.asked: list[int] = []

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                return

            def do_GET(self):  # the router's liveness probe
                data = json.dumps({"object": "list", "data": [{"id": "glm-5.3-flash"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                import time

                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.asked.append(int(body.get("max_tokens") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                outer.started.set()
                try:
                    for _ in range(600):
                        chunk = "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "la "},
                                                                   "finish_reason": None}]}) + "\n\n"
                        raw = chunk.encode()
                        self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    outer.gone.set()

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._srv.daemon_threads = True
        import threading as _t

        self._thread = _t.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()

    @property
    def url(self) -> str:
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}"


async def test_stop_ends_a_runaway_streamed_reply_at_once_and_closes_the_connection(tmp_path):
    """THE BOUND THAT MATTERS for the streamed lanes: a reply that runs away
    under the 32,768-token budget (~10 minutes at ~55 tokens a second) is
    ended by Stop AT ONCE — the turn ends and the HTTP stream to the model
    server is closed, which is what makes vLLM (and a proxy relaying a
    stream) stop generating. Pinned so a later change cannot turn Stop into
    "the turn ends but the model keeps writing"."""
    import asyncio
    import time

    from iron_jarvis.daemon.chat_stream import TURNS, stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from tests.test_reply_budget_v1387 import MODEL, _platform

    with _SlowServer() as srv:
        platform = _platform(tmp_path, srv.url)
        body = ChatBody(messages=[ChatMessageBody(role="user", content="sing")],
                        provider="custom", model=MODEL, turn_id="runaway-1")
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        stopped_at = None
        t0 = time.monotonic()

        async def consume():
            nonlocal stopped_at
            async for chunk in gen:
                if "event: token" in chunk and stopped_at is None:
                    stopped_at = time.monotonic()
                    TURNS.stop("runaway-1")

        await asyncio.wait_for(consume(), timeout=20)
        ended = time.monotonic()
        assert stopped_at is not None, "no token arrived"
        assert ended - stopped_at < 3.0, ended - stopped_at
        assert len(srv.asked) == 1 and srv.asked[0] > 4_096, srv.asked  # the full (fitted) budget
        assert await asyncio.to_thread(srv.gone.wait, 10), "the server never saw the client go"
        assert time.monotonic() - t0 < 25


async def test_a_not_streamed_request_keeps_the_old_budget_and_the_reason_is_pinned(tmp_path):
    """THE REVIEW'S DECISION, pinned: a request that is NOT streamed (the
    POST chat lane — the phone, API callers — and the one-shot utilities)
    asks at most the old 4,096 (``NON_STREAM_CAP``). Its answer arrives whole
    or not at all inside the client's 60-second read timeout (pinned below,
    so a change to it makes this decision visible): ~3,300 tokens at the
    daily driver's ~55 a second. With the doer's 32,768 a timed-out request
    only ran longer on the server (a proxy keeps generating for a client that
    left) — and the router tries such a timeout three times. RED on the
    doer's code: 32,768."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.providers.reply_budget import NON_STREAM_CAP
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, text_reply
    from tests.test_reply_budget_v1387 import MODEL, USER_GLM_INFO, _platform, _read_table

    assert NON_STREAM_CAP == 4_096
    with FakeEndpoint(lambda b: text_reply("ok"), model_info=USER_GLM_INFO) as srv:
        platform = _platform(tmp_path, srv.url)
        _read_table(platform)
        await run_chat_turn(platform, BUILTIN_PERSONAS, ChatBody(
            messages=[ChatMessageBody(role="user", content="hi")], provider="custom", model=MODEL))
        assert srv.chats()[-1]["max_tokens"] == 4_096
        adapter = platform.providers.get("custom", MODEL)
        assert adapter._client().timeout.read == 60.0


@pytest.mark.parametrize("tools", [False, True])
async def test_a_plain_chat_turn_and_a_tool_turn_get_the_same_budget(tmp_path, tools):
    """THE REVIEW'S OTHER DECISION, pinned: the streamed budget is the SAME
    for a plain chat turn as for a turn with tools (no separate, lower chat
    default). Evidence: a streamed reply is bounded by Stop (pinned above)
    and the 60-second idle read timeout; a reasoning model (glm) spends its
    thinking from the same budget, so a lower chat budget cuts the ANSWER of
    a hard question, not just a runaway; and a chat turn with tools (a
    project chat writing a file, office documents) needs the room Build
    does."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, text_reply
    from tests.test_reply_budget_v1387 import MODEL, USER_GLM_INFO, _platform, _read_table

    with FakeEndpoint(lambda b: text_reply("ok"), model_info=USER_GLM_INFO) as srv:
        platform = _platform(tmp_path, srv.url)
        _read_table(platform)
        body = ChatBody(messages=[ChatMessageBody(role="user", content="hi")], provider="custom",
                        model=MODEL, **({"tools": ["read_file", "write_file"]} if tools else {}))
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        async for _ in gen:
            pass
        sent = srv.chats()[-1]
    assert bool(sent.get("tools")) or not tools
    assert sent["max_tokens"] == 32_768


# --------------------------------------------------------------------------- #
# 9. A NAME THE USER'S PROXY SENDS TO THE CLOUD IS A HOSTED MODEL.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [False, True])
async def test_a_name_the_proxy_routes_to_a_cloud_service_keeps_the_old_request(tmp_path, stream):
    """RED on the doer's code: the budget keyed on the PROVIDER slot, so a
    name the user's LiteLLM proxy sends on to a cloud service (the user's
    proxy routes ``frontier`` to OpenRouter — v1.362.0's route table) asked
    the 32,768 of a local model. That is a hosted model — the scope the
    budget was never for — and OpenRouter checks a request's credit against
    its WHOLE ``max_tokens`` (a 402 "fewer max_tokens", which no retry
    answers). Such a name keeps the request it had: 4,096, and the planner's
    classic reserve. A local name on the same proxy keeps the budget."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.chat_turn import _answer_reserve
    from iron_jarvis.fleet.upstreams import RouteTable, Upstream
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, text_reply
    from tests.test_reply_budget_v1387 import MODEL, _platform

    with FakeEndpoint(lambda b: text_reply("ok"), models=(MODEL, "frontier-x")) as srv:
        platform = _platform(tmp_path, srv.url)
        platform.fleet.set_routes("custom", RouteTable(entries=(
            Upstream(alias=MODEL, model=f"hosted_vllm/{MODEL}", place="local",
                     context_window=1_000_000),
            Upstream(alias="frontier-x", model="openrouter/some/model", place="cloud",
                     context_window=1_000_000),
        ), read_at=0.0))
        for name in ("frontier-x", MODEL):
            adapter = platform.providers.get("custom", name)
            if stream:
                async for _ in adapter.stream(system="s", messages=[], tools=[]):
                    pass
            else:
                await adapter.complete(system="s", messages=[], tools=[])
        cloud_body, local_body = srv.chats()
    assert cloud_body["model"] == "frontier-x" and cloud_body["max_tokens"] == 4_096
    assert local_body["max_tokens"] == (32_768 if stream else 4_096)
    d = SimpleNamespace(platform=platform)
    from iron_jarvis.context.budget import output_reserve

    assert _answer_reserve(d, "custom", "frontier-x", 1_000_000) == output_reserve(1_000_000)
    assert _answer_reserve(d, "custom", MODEL, 1_000_000) == 32_768


# --------------------------------------------------------------------------- #
# 10. WHO IS BUDGETED — through the real manager and the real adapters.
# --------------------------------------------------------------------------- #
async def test_every_local_kind_is_budgeted_on_the_wire(tmp_path):
    """The custom slot, the ollama slot and fleet nodes of BOTH protocols,
    built by the REAL platform/manager/registry and streamed against a fake
    server on this machine: every one asks more than the old 4,096."""
    import httpx

    from iron_jarvis.fleet.models import FleetNode
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, text_reply
    from tests.test_reply_budget_v1387 import MODEL, _platform

    with FakeEndpoint(lambda b: text_reply("ok")) as srv:
        platform = _platform(tmp_path, srv.url)
        platform.config.ollama_base_url = srv.url
        platform.providers.configure_local(  # (one call: an omitted slot is reset)
            custom_base_url=srv.url, custom_model=MODEL, ollama_base_url=srv.url, ollama_model=MODEL,
        )
        platform.fleet.add(FleetNode(id="box", label="Box", base_url=srv.url, source="user",
                                     routable=True, default_model=MODEL))
        platform.fleet.register_providers(platform.providers, secret_resolver=lambda n: None)
        for name in ("custom", "ollama", "fleet-box"):
            async for _ in platform.providers.get(name, MODEL).stream(system="s", messages=[], tools=[]):
                pass
        asked = [b["max_tokens"] for b in srv.chats()]
    assert len(asked) == 3 and all(a > 4_096 for a in asked), asked

    # the Anthropic-protocol node through the same registry
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn", "usage": {}})

    node = FleetNode(id="abox", label="A", base_url="http://127.0.0.1:9", source="user",
                     routable=True, default_model=MODEL, protocol="anthropic")
    platform.fleet.add(node)
    platform.fleet.register_providers(platform.providers, secret_resolver=lambda n: None)
    adapter = platform.providers.get("fleet-abox", MODEL)
    adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter._key = lambda: ""
    async for _ in adapter.stream(system="s", messages=[], tools=[]):
        pass
    assert seen and seen[0]["max_tokens"] > 4_096, seen


@pytest.mark.parametrize("name", ["openai", "xai", "openrouter"])
async def test_a_hosted_providers_streamed_request_is_byte_for_byte_the_old_one(name):
    """The doer pinned the hosted providers' NOT-streamed body; the STREAMED
    one (what the chat page sends) is the same as before v1.387.0 too."""
    import httpx

    from iron_jarvis.providers.manager import ProviderManager

    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        sse = ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "hi"},
                                                   "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    mgr = ProviderManager(credential_resolver=lambda provider: "sk-test")
    adapter = mgr.get(name)
    adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async for _ in adapter.stream(system="sys", messages=[], tools=[]):
        pass
    body = json.loads(seen[0])
    assert body == {
        "model": adapter.model, "max_tokens": 4096,
        "messages": [{"role": "system", "content": "sys"}],
        "stream": True, "stream_options": {"include_usage": True},
    }
    assert getattr(adapter, "reply_cap", lambda: None)() is None


# --------------------------------------------------------------------------- #
# 11. EVERY REFUSAL WORDING THE RETRY MEETS.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("words", "asked", "raw", "expect"), [
    # vLLM (older): the window and the prompt
    ("This model's maximum context length is 32768 tokens. However, you requested 40000 tokens "
     "(8000 in the messages, 32000 in the completion).", 32_000, 7_000, 32_768 - 8_000 - 16),
    # vLLM (newer) — names BOTH fields; never the field-rename swap
    ("'max_tokens' or 'max_completion_tokens' is too large: 32000. This model's maximum context "
     "length is 32768 tokens and your request has 8000 input tokens (32000 > 32768 - 8000).",
     32_000, 7_000, 32_768 - 8_000 - 16),
    # SGLang
    ("Requested token count exceeds the model's maximum context length of 32768 tokens. You "
     "requested a total of 40000 tokens: 8000 tokens from the input messages and 32000 tokens "
     "for the completion.", 32_000, 7_000, 32_768 - 8_000 - 16),
    # TGI / Together (the window is "inputs + max_new_tokens <= N")
    ("Input validation error: `inputs` tokens + `max_new_tokens` must be <= 4096. Given: 3000 "
     "`inputs` tokens and 32000 `max_new_tokens`", 32_000, 2_500, 4_096 - 3_000 - 16),
    # an OpenAI-shaped output limit (a hosted model behind a custom slot)
    ("max_tokens is too large: 32768. This model supports at most 16384 completion tokens, "
     "whereas you provided 32768.", 32_768, 500, 16_384),
    # Groq-shaped output limit
    ("max_tokens must be less than or equal to 8192, the maximum value for max_tokens is less "
     "than the context_window for this model", 32_768, 500, 8_192),
    # DeepSeek-shaped output range (review: read, not the 4,096 fallback)
    ("Invalid max_tokens value, the valid range of max_tokens is [1, 8192]", 32_768, 500, 8_192),
    # a budget refused with NO number: the old 4,096 (never worse than before)
    ("Invalid value for 'max_tokens': the value is too big for this model.", 32_768, 500, 4_096),
    # PROMPT-only refusals — the history is the problem: never a smaller answer
    ("This model's maximum context length is 32768 tokens. However, your request has 40000 input "
     "tokens. Please reduce the length of the input messages.", 4_096, 39_000, None),
    ("the request exceeds the available context size, try increasing it", 4_096, 9_000, None),  # llama.cpp
    ("Trying to keep the first 9000 tokens when context the overflows. However, the model is loaded "
     "with context length of only 8192 tokens, which is not enough.", 4_096, 9_000, None),  # LM Studio
    # the prompt nearly fills the window: no answer worth asking — raised for the refit
    ("This model's maximum context length is 8192 tokens. However, you requested 12000 tokens "
     "(7900 in the messages, 4100 in the completion).", 4_100, 7_800, None),
])
def test_the_retry_reads_every_wording(words, asked, raw, expect):
    from iron_jarvis.providers.reply_budget import ReplyBudget

    budget = ReplyBudget("custom", "http://box.test/v1/chat/completions")
    assert budget.retry_ask("m", words, asked, raw) == expect


def test_a_window_learned_on_one_endpoint_is_never_used_for_another():
    from iron_jarvis.providers.reply_budget import ReplyBudget

    a = ReplyBudget("fleet-a", "http://a.test/v1/chat/completions")
    b = ReplyBudget("fleet-b", "http://b.test/v1/chat/completions")
    a.retry_ask("m", "This model's maximum context length is 6000 tokens. However, you requested "
                "33000 tokens (1000 in the messages, 32000 in the completion).", 32_000, 900)
    assert a.window("m") == 6_000
    assert b.window("m") is None and b.cap("m") == 32_768
    assert a.window("other-model") is None


# --------------------------------------------------------------------------- #
# 12. claude-cli: a call cut at the limit never becomes a runnable call.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("closed", [True, False])
def test_claude_clis_relay_never_hands_on_a_cut_call(closed):
    """Evidence for the report (not red-first: nothing changed here). A
    claude-cli answer cut inside a tool call's streamed input: the relay's
    Capture either cannot parse the block it closes (a ValueError — the
    relay's handler records it as a failure) or never sees it close
    (``complete`` False) — `transport.assemble` then raises
    `upstream_failure`: the turn fails honestly; the half call is never
    run. (The CLI keeps its own output limit; the reply budget does not
    reach it.)"""
    from iron_jarvis.providers.adapters.claude_native.admission import Capture

    events = [
        {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 5}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__ij__write_file", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"path": "calc.py", "content": "class C'}},
        *([{"type": "content_block_stop", "index": 0}] if closed else []),
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 32000}},
        {"type": "message_stop"},
    ]
    data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
    capture = Capture()
    if closed:
        with pytest.raises(ValueError):
            capture.feed(data)
    else:
        capture.feed(data)
        assert capture.complete is False


# --------------------------------------------------------------------------- #
# 13. The rules the doer's pins left unchecked (each mutation survived them).
# --------------------------------------------------------------------------- #
def test_litellms_legacy_max_tokens_is_an_output_limit_only_when_it_differs_from_the_input_one():
    """LiteLLM's ``max_tokens`` is the OUTPUT limit when a provider states one
    and a copy of the INPUT limit otherwise: equal to ``max_input_tokens``
    it says nothing about the output. ``max_output_tokens`` wins over it;
    two deployments of one name keep the smaller limit."""
    from iron_jarvis.fleet.upstreams import parse_model_info

    def entry(name, **info):
        return {"model_name": name, "litellm_params": {"model": f"hosted_vllm/{name}",
                                                       "api_base": "http://10.0.0.5:8000/v1"},
                "model_info": info}

    table = parse_model_info({"data": [
        entry("same", max_input_tokens=131_072, max_tokens=131_072),
        entry("legacy", max_input_tokens=131_072, max_tokens=8_192),
        entry("both", max_input_tokens=131_072, max_tokens=8_192, max_output_tokens=16_384),
        entry("pair", max_output_tokens=16_384),
        entry("pair", max_output_tokens=8_000),
    ]})
    caps = {e.alias: e.max_output for e in table.entries}
    assert caps == {"same": None, "legacy": 8_192, "both": 16_384, "pair": 8_000}


def test_a_stated_limit_is_taken_up_to_the_ceiling_and_an_unknown_window_never_asks_below_the_old_budget():
    from iron_jarvis.providers.reply_budget import (
        DEFAULT_REPLY_TOKENS,
        REPLY_CEILING,
        ask_for,
        cap_for,
    )

    assert cap_for(None) == DEFAULT_REPLY_TOKENS == 32_768
    assert cap_for(200_000) == REPLY_CEILING == 65_536
    assert cap_for(16_384) == 16_384
    assert cap_for(200_000, learned=12_000) == 12_000
    # an UNKNOWN window with a prompt that would leave little room: the old 4,096
    assert ask_for(32_768, None, 30_000) == 4_096
    # a KNOWN window is honoured below it (down to 1,024)
    assert ask_for(32_768, 32_000, 29_000) == 1_024


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_reply_that_keeps_being_cut_ends_at_the_total_even_with_progress_between(env, lane):
    """The total bound (CUT_OFF_TOTAL_MAX): a model that is cut, recovers,
    and is cut again on every file still reaches an end — each cut can cost
    a whole budget of generation — and the end says "in this reply", not
    "in a row"."""
    from iron_jarvis.providers.reply_budget import CUT_OFF_TOTAL_MAX

    platform, ws, wire = env
    script: list = []
    for i in range(CUT_OFF_TOTAL_MAX + 2):
        script += [_cut(cut_calls=["write_file"]), _write(f"f{i}.py", "X = 1\n", f"w{i}")]
    model = wire(script + ["never reached"])
    done = await _lane(lane, platform, _body(ws), round_budget=60)
    assert done["route"]["cut_off"] == {"times": CUT_OFF_TOTAL_MAX + 1, "recovered": False}
    assert f"cut off at its output limit {CUT_OFF_TOTAL_MAX + 1} times in this reply" in done["reply"]
    assert "in a row" not in done["reply"]
    assert (ws / f"f{CUT_OFF_TOTAL_MAX - 1}.py").exists()
    assert not (ws / f"f{CUT_OFF_TOTAL_MAX}.py").exists()
    assert all(c["tools"] for c in model.calls)


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_long_answer_that_mentions_a_tool_call_tag_is_not_taken_for_a_leak(env, lane):
    """The leak rule's guard: an unclosed ``<tool_call>`` counts as a call
    only when what follows it names a tool THIS turn offered — a long answer
    ABOUT the format, cut at the limit, keeps v1.323.0's `truncated` +
    Continue (and its words)."""
    platform, ws, wire = env
    answer = ("GLM writes its calls like <tool_call>name, then <arg_key>… and so on. "
              "Here is the long explanation, part one …")
    model = wire([LLMResponse(text=answer, finish_reason="max_tokens")])
    # (a plain chat turn with file tools armed: a harness turn has its own,
    # older answer to text that LOOKS like a call — the text-call nudge)
    done = await _lane(lane, platform, _body(
        ws, "explain how GLM writes tool calls", tools=["read_file", "write_file", "edit_file"],
    ), harness=None)
    assert not any(str(u).startswith(NOTE_HEAD) for u in model.users())
    assert done["truncated"] is True
    assert "cut_off" not in done["route"]
    assert done["reply"].startswith("GLM writes its calls like <tool_call>name")


# --------------------------------------------------------------------------- #
# 14. THE HOSTED ANTHROPIC API: a call the limit stopped in never runs.
# --------------------------------------------------------------------------- #
def _anthropic_cut_sse(name: str = "write_file") -> bytes:
    events = [
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Writing it."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": name, "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {
            "type": "input_json_delta", "partial_json": '{"path": "calc.py", "content": "class Calc:\\n    def add'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens", "stop_sequence": None},
         "usage": {"output_tokens": 4096}},
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.mark.parametrize("stream", [False, True])
async def test_the_hosted_anthropic_api_never_runs_a_call_cut_at_the_limit(monkeypatch, stream):
    """RED before this review (not the doer's: the hosted adapter was out of
    its scope, but the rule "a half-parsed call is NEVER run" is the lanes'
    and they are provider-agnostic). The SDK hands back a tool_use the limit
    stopped in with a PARTIALLY PARSED input (`{"path": "calc.py", "content":
    "class Calc:\n    def add"}`), and the lanes ran it — half a file. Now
    the last block of a reply stopped at max_tokens, when a tool call, is a
    cut call (reported by name; the lanes' recovery takes over). The request
    is unchanged."""
    import httpx
    from anthropic import AsyncAnthropic

    from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
    from iron_jarvis.providers.adapters.base import LLMMessage

    def handler(request: httpx.Request) -> httpx.Response:
        if stream:
            return httpx.Response(200, content=_anthropic_cut_sse(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "Writing it."},
                        {"type": "tool_use", "id": "toolu_1", "name": "write_file",
                         "input": {"path": "calc.py", "content": "class Calc:\n    def add"}}],
            "stop_reason": "max_tokens", "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 4096}})

    a = AnthropicAdapter(model="claude-sonnet-4-5", api_key="sk-ant-test")
    client = AsyncAnthropic(api_key="sk-ant-test",
                            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(a, "_client", lambda: client)
    spec = {"name": "write_file", "description": "w", "input_schema": {"type": "object"}}
    msgs = [LLMMessage(role="user", content="write calc.py")]
    if stream:
        final = None
        async for frame in a.stream(system="", messages=msgs, tools=[spec]):
            if frame["type"] == "final":
                final = frame["response"]
    else:
        final = await a.complete(system="", messages=msgs, tools=[spec])
    assert final.finish_reason == "max_tokens"
    assert final.tool_calls == [] and final.cut_calls == ["write_file"]
    assert final.text == "Writing it."
