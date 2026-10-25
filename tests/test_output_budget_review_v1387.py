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

    with fake.FakeEndpoint(lambda b: fake.text_reply("ok"), context_len=24_000,
                           refusal="litellm_long") as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 200_000})
        await _ask(platform, stream=stream)
        bodies = srv.chats()
    assert bodies[0]["max_tokens"] == 32_768
    second = bodies[1]["max_tokens"]
    assert second > 4_096 and srv.prompt_tokens(bodies[1]) + second <= 24_000
    assert reply_budget.learned(srv.url, MODEL).get("output_cap") != 4_096
