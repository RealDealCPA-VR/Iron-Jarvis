"""v1.387.0 — A LOCAL MODEL ENDPOINT GETS A REPLY BUDGET THAT FITS THE WORK.

The benchmark's three failed glm runs ended the same way: every request to the
user's local endpoint (the ``custom`` slot, a LiteLLM proxy serving
glm-5.3-flash) carried ``max_tokens: 4096`` — the OpenAI adapter's default,
which the provider manager never overrode — and a ``write_file`` of a whole
module was cut at the cap. These pins drive the REAL adapter (and, for the
stated limit, the REAL route reader) against a FAKE OpenAI-compatible server
(``tests/fixtures/fake_openai_endpoint_v1387.py``); nothing reaches a real
model or the user's gateway.

Red on the code before v1.387.0 (each says which): the request asked 4,096;
a stated ``max_output_tokens`` was never read; a small known window was
overflowed and refused; a refusal for length was raised instead of retried;
a call cut mid-arguments came back as a RUNNABLE call with ``{}`` arguments.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iron_jarvis.fleet import upstreams
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from tests.fixtures.fake_openai_endpoint_v1387 import (
    FakeEndpoint,
    Reply,
    text_reply,
    write_call_reply,
)

MODEL = "glm-5.3-flash"
WRITE_SPEC = {
    "name": "write_file",
    "description": "Write a file.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}
#: What the user's real gateway states for the glm names (read on
#: 2026-10-24, tests/fixtures/fake_litellm_gateway_v1382.STATED): a window,
#: and NO output limit.
USER_GLM_INFO = {MODEL: {"supports_vision": False, "supports_function_calling": True,
                         "max_input_tokens": 1_000_000}}


@pytest.fixture(autouse=True)
def _forget_learned():
    """The adapter remembers what servers said for the process; each test
    starts clean (and leaves nothing behind for the next)."""
    try:
        from iron_jarvis.providers import reply_budget
    except ImportError:  # the code before v1.387.0 has no such module
        yield
        return
    reply_budget.forget_learned()
    yield
    reply_budget.forget_learned()


def _platform(tmp_path, url: str, *, pins: dict | None = None):
    platform = build_platform(str(tmp_path / "home"))
    platform.config.custom_base_url = url
    platform.config.custom_model = MODEL
    if pins:
        platform.config.model_context_windows = dict(pins)
    platform.providers.configure_local(custom_base_url=url, custom_model=MODEL)
    return platform


def _read_table(platform) -> None:
    """The EXISTING route reader (fleet/upstreams.read_routes) with the node's
    own headers, exactly as the sampler runs it, recorded on the registry."""
    fleet = platform.fleet
    node = fleet._top_level()["custom"]
    fleet.set_routes("custom", upstreams.read_routes(node, headers=fleet.headers_for(node)))


def _msgs(text: str = "write calc.py") -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


async def _ask(platform, *, stream: bool = False, text: str = "write calc.py"):
    adapter = platform.providers.get("custom", MODEL)
    if not stream:
        return await adapter.complete(system="You are a coder.", messages=_msgs(text), tools=[WRITE_SPEC])
    final = None
    async for frame in adapter.stream(system="You are a coder.", messages=_msgs(text), tools=[WRITE_SPEC]):
        if frame.get("type") == "final":
            final = frame["response"]
    return final


# --------------------------------------------------------------------------- #
# 1. the budget — the user's glm endpoint, before and after
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [False, True])
async def test_the_users_glm_endpoint_asks_for_a_budget_that_fits_the_work(tmp_path, stream):
    """RED before v1.387.0: ``max_tokens`` was 4096 on every request. The
    user's proxy states a 1,000,000-token window and no output limit, so the
    default budget (32,768) is asked for in full."""
    with FakeEndpoint(lambda b: text_reply("ok"), model_info=USER_GLM_INFO) as srv:
        platform = _platform(tmp_path, srv.url)
        _read_table(platform)
        await _ask(platform, stream=stream)
        body = srv.chats()[-1]
    assert body["model"] == MODEL
    # Review (moved on purpose): a request that is NOT streamed keeps the old
    # 4,096 (`reply_budget.NON_STREAM_CAP` — it has 60 seconds to arrive
    # whole); the streamed lanes ask the full budget.
    assert body["max_tokens"] == (32_768 if stream else 4_096)
    assert "max_completion_tokens" not in body


@pytest.mark.parametrize("stream", [False, True])
async def test_a_stated_output_limit_is_preferred(tmp_path, stream):
    """RED before v1.387.0 (4096, and the reader kept no output limit): a
    LiteLLM ``model_info.max_output_tokens`` is the model's own limit, read by
    the existing route reader and saved beside the other capabilities."""
    info = {MODEL: {**USER_GLM_INFO[MODEL], "max_output_tokens": 16_384}}
    with FakeEndpoint(lambda b: text_reply("ok"), model_info=info) as srv:
        platform = _platform(tmp_path, srv.url)
        _read_table(platform)
        await _ask(platform, stream=stream)
        # (review: not streamed — at most the old 4,096)
        assert srv.chats()[-1]["max_tokens"] == (16_384 if stream else 4_096)
    # Saved beside the other capabilities — never an upstream address (the
    # entry carries the verdicts only), never a key.
    path = platform.fleet._routes_path
    saved = json.loads(path.read_text("utf-8"))
    row = saved["custom"]["table"]["entries"][0]
    assert row["alias"] == MODEL
    assert row["max_output"] == 16_384 and row["context_window"] == 1_000_000
    assert "api_base" not in json.dumps(saved)


async def test_an_unknown_window_is_planned_as_the_default_and_never_below_the_old_budget(tmp_path):
    """RED before v1.387.0 (4096). Nothing states the window (no /model/info):
    the budget is fitted to the window the lanes plan against when nothing is
    known (``DEFAULT_WINDOW``, 32,000) minus this request — and never below
    the old 4,096."""
    with FakeEndpoint(lambda b: text_reply("ok")) as srv:
        platform = _platform(tmp_path, srv.url)
        await _ask(platform, stream=True)  # review: the streamed lanes' budget
        body = srv.chats()[-1]
        await _ask(platform)  # ...and a request that is not streamed
        not_streamed = srv.chats()[-1]
    prompt = srv.prompt_tokens(body)
    assert 4096 < body["max_tokens"] <= 32_000 - prompt
    assert body["max_tokens"] >= 32_000 - prompt - max(256, prompt // 10) - 64
    assert not_streamed["max_tokens"] == 4_096


async def test_a_small_known_window_asks_only_what_fits(tmp_path):
    """RED before v1.387.0: an 8,192-token window with a ~5k prompt was asked
    for 4,096 more — over the window — and vLLM refused the request. The
    budget now fits the KNOWN window (here a pin) minus the request."""
    big = "word " * 3_600  # ≈ 5,000 tokens of prompt
    with FakeEndpoint(lambda b: text_reply("ok"), context_len=8_192) as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 8_192})
        resp = await _ask(platform, text=big)
        bodies = srv.chats()
    assert resp.text == "ok"
    assert len(bodies) == 1, "one request — it fitted the first time"
    assert srv.prompt_tokens(bodies[0]) + bodies[0]["max_tokens"] <= 8_192
    assert bodies[0]["max_tokens"] >= 1_024


# --------------------------------------------------------------------------- #
# 2. a server that refuses for length: ONE retry with what it says fits
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("refusal", ["vllm_old", "vllm_new", "sglang", "litellm"])
@pytest.mark.parametrize("stream", [False, True])
async def test_a_length_refusal_is_retried_once_with_what_the_server_says_fits(tmp_path, refusal, stream):
    """RED before v1.387.0: a 4,800-token model with a ~1,500-token prompt
    refused the 4,096 budget and the request failed. The server's own words
    name what fits; the adapter retries ONCE with that, then REMEMBERS it — the
    next request fits from the start."""
    prompt_text = "word " * 1_000
    with FakeEndpoint(lambda b: text_reply("ok"), context_len=4_800, refusal=refusal) as srv:
        platform = _platform(tmp_path, srv.url)
        first = await _ask(platform, stream=stream, text=prompt_text)
        bodies = srv.chats()
        assert first.text == "ok"
        assert len(bodies) == 2, [b["max_tokens"] for b in bodies]
        p = srv.prompt_tokens(bodies[1])
        assert bodies[1]["max_tokens"] == 4_800 - p - 16
        # learned for the process: the next request is ONE request that fits
        second = await _ask(platform, stream=stream, text=prompt_text)
        after = srv.chats()[2:]
    assert second.text == "ok"
    assert len(after) == 1
    assert srv.prompt_tokens(after[0]) + after[0]["max_tokens"] <= 4_800
    # ...and the HISTORY PLANNER plans against the window the server named
    # (the lanes never saw the refusal the adapter answered).
    from types import SimpleNamespace

    from iron_jarvis.daemon.chat_turn import _context_window_source

    assert _context_window_source(SimpleNamespace(platform=platform), "custom", MODEL) == (4_800, "learned")


async def test_an_adapter_without_the_platforms_facts_still_remembers_what_the_server_said():
    """A bare manager (no window ladder wired — a CLI path, a fleet double)
    keeps the server's window in the adapter's own memory: the second request
    fits from the start."""
    from iron_jarvis.providers.manager import ProviderManager

    with FakeEndpoint(lambda b: text_reply("ok"), context_len=4_800) as srv:
        mgr = ProviderManager(custom_base_url=srv.url, custom_model=MODEL)
        assert mgr.reply_facts is None
        adapter = mgr.get("custom", MODEL)
        for _ in range(2):
            await adapter.complete(system="s", messages=_msgs("word " * 1_000), tools=[])
        bodies = srv.chats()
    assert len(bodies) == 3, [b["max_tokens"] for b in bodies]  # refused, retried, then fitted
    assert srv.prompt_tokens(bodies[2]) + bodies[2]["max_tokens"] <= 4_800


@pytest.mark.parametrize("stream", [False, True])
async def test_a_budget_refused_in_words_without_a_number_falls_back_to_the_old_budget(tmp_path, stream):
    """NEVER WORSE THAN BEFORE: a server that refuses the bigger budget in
    words that name ``max_tokens`` but no number (nothing to fit to) gets the
    old 4,096 once — and keeps getting it: the next request asks 4,096 from
    the start. (Not red-first: the old code asked 4,096 and was accepted —
    this pins that the new budget cannot make such a server fail.)"""
    with FakeEndpoint(lambda b: text_reply("ok"), output_limit=8_192) as srv:
        platform = _platform(tmp_path, srv.url)
        first = await _ask(platform, stream=stream)
        second = await _ask(platform, stream=stream)
        asked = [b["max_tokens"] for b in srv.chats()]
    assert first.text == "ok" and second.text == "ok"
    if stream:
        assert asked[0] > 8_192 and asked[1:] == [4_096, 4_096], asked
    else:
        # review: a not-streamed request never asked more than 4,096
        assert asked == [4_096, 4_096], asked


async def test_a_prompt_that_itself_does_not_fit_is_raised_for_the_lanes_refit(tmp_path):
    """Unchanged (v1.335.0): when the PROMPT is what overflows, the adapter
    does not shrink the answer into nothing — the refusal is raised as before,
    so the lanes' too-long refit shrinks the history."""
    with FakeEndpoint(lambda b: text_reply("ok"), context_len=1_000) as srv:
        platform = _platform(tmp_path, srv.url)
        with pytest.raises(ProviderError) as caught:
            await _ask(platform, text="word " * 1_500)
        assert len(srv.chats()) == 1
    from iron_jarvis.daemon.chat_turn import is_context_overflow

    assert is_context_overflow(caught.value)


# --------------------------------------------------------------------------- #
# 3. a call cut mid-arguments is reported, never run
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [False, True])
async def test_a_write_cut_at_the_output_limit_is_reported_not_returned_as_a_call(tmp_path, stream):
    """RED before v1.387.0: the cut ``write_file`` came back in ``tool_calls``
    with ``{}`` arguments (the lenient recovery found nothing), so the lane
    RAN it. Now it is named in ``cut_calls`` and is not a call at all."""
    module = "def f():\n    return 1\n" * 10_000  # ≈ 230k chars: over any budget
    with FakeEndpoint(lambda b: write_call_reply(b, "calc.py", module)) as srv:
        platform = _platform(tmp_path, srv.url)
        resp = await _ask(platform, stream=stream)
    assert resp.finish_reason == "max_tokens"
    assert resp.tool_calls == []
    assert getattr(resp, "cut_calls", None) == ["write_file"]
    assert resp.extra.get("max_tokens") == srv.chats()[-1]["max_tokens"]


async def test_a_complete_call_beside_a_cut_one_still_arrives(tmp_path):
    """Only the call whose arguments never closed is held back; a complete
    call earlier in the same reply is an ordinary call."""
    def reply(body):
        return Reply(
            calls=[("read_file", json.dumps({"path": "a.py"})),
                   ("write_file", '{"path": "b.py", "content": "half')],
            finish="length",
        )

    with FakeEndpoint(reply) as srv:
        platform = _platform(tmp_path, srv.url)
        resp = await _ask(platform, stream=True)
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("read_file", {"path": "a.py"})]
    assert resp.cut_calls == ["write_file"]


def test_a_cut_call_whose_text_hides_an_object_is_still_not_run():
    """The lenient ladder (core/jsonish) would pull the FIRST balanced object
    out of a half-written argument string — never, at the output limit."""
    from iron_jarvis.providers.adapters.openai import OpenAIAdapter

    args = '{"path": "a.py", "content": "x"}, "extra": {"mode": "w"'  # balanced head, cut tail
    data = {"choices": [{"finish_reason": "length", "message": {"content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": args}},
    ]}}]}
    resp = OpenAIAdapter._parse(data)
    assert resp.tool_calls == [] and resp.cut_calls == ["write_file"]
    # The same string NOT at the limit still gets the lenient recovery.
    data["choices"][0]["finish_reason"] = "tool_calls"
    assert OpenAIAdapter._parse(data).tool_calls[0].arguments == {"path": "a.py", "content": "x"}


# --------------------------------------------------------------------------- #
# 4. everything else unchanged: hosted providers byte for byte
# --------------------------------------------------------------------------- #
class _Capture:
    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def post(self, url, headers=None, json=None):  # noqa: A002
        self.bodies.append(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
                              request=httpx.Request("POST", url))


@pytest.mark.parametrize("name", ["openai", "xai", "openrouter"])
async def test_hosted_providers_send_the_same_request_as_before(name):
    """The hosted APIs are not budgeted: their request is byte-identical to
    the one before v1.387.0 (``max_tokens: 4096`` and nothing new)."""
    from iron_jarvis.providers.manager import ProviderManager

    mgr = ProviderManager(credential_resolver=lambda provider: "sk-test")
    adapter = mgr.get(name)
    cap = _Capture()
    adapter._http = cap
    await adapter.complete(system="sys", messages=_msgs("hi"), tools=[WRITE_SPEC])
    expected = {
        "model": adapter.model,
        "max_tokens": 4096,
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {
            "name": "write_file", "description": "Write a file.",
            "parameters": WRITE_SPEC["input_schema"],
        }}],
    }
    assert json.dumps(cap.bodies[0]) == json.dumps(expected)
    assert getattr(adapter, "reply_cap", lambda: None)() is None


def test_only_the_local_slots_and_fleet_nodes_are_budgeted(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    pm = platform.providers
    assert pm.reply_cap("custom") == 32_768
    assert pm.reply_cap("ollama") == 32_768
    for hosted in ("openai", "anthropic", "google", "xai", "openrouter", "claude-cli",
                   "codex-cli", "mock", "auto", ""):
        assert pm.reply_cap(hosted) is None, hosted


# --------------------------------------------------------------------------- #
# 5. the planner and the adapter agree
# --------------------------------------------------------------------------- #
def _conversation(n: int = 240, size: int = 900) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "word " * (size // 5)}
        for i in range(n)
    ]


@pytest.mark.parametrize("window", [2_048, 4_096, 8_192, 16_000, 32_000, 131_072, 1_000_000])
@pytest.mark.parametrize("cap", [1_024, 4_096, 16_384, 32_768, 65_536])
def test_the_planners_answer_reserve_and_the_adapters_ask_agree(window, cap):
    """THE AGREEMENT, swept: for every window and cap, the planner holds back
    ``reserve_for`` for the answer (never more than the adapter could ask
    for), and on any transcript the planner fitted the adapter's ask (1)
    never overflows the window and (2) is at least that reserve, less only
    the slack kept for the estimate."""
    from iron_jarvis.context import plan_history
    from iron_jarvis.context.budget import OUTPUT_RESERVE_MIN
    from iron_jarvis.providers.reply_budget import ask_for, prompt_slack, reserve_for

    reserve = reserve_for(window, cap)
    assert reserve <= max(cap, OUTPUT_RESERVE_MIN)
    for n in (2, 40, 240):  # fits, near the edge, far over
        plan = plan_history(_conversation(n), window=window, system_text="S" * 2_000, reserve=reserve)
        p = plan.used_tokens
        if plan.clipped_last:
            continue  # the question alone does not fit: the lanes' refit's job
        assert p <= window - reserve, (n, p, window, reserve)
        ask = ask_for(cap, window, p)
        assert p + ask <= window, (n, p, ask, window)
        assert ask >= min(cap, reserve) - prompt_slack(p), (n, p, ask, reserve)


def test_the_hosted_planner_reserve_is_unchanged(tmp_path):
    """A hosted provider's turn is planned exactly as before: no reserve is
    passed, so ``output_reserve`` (≤ 4,096) answers."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.chat_turn import _answer_reserve, _reserve_kw

    platform = build_platform(str(tmp_path / "home"))
    d = SimpleNamespace(platform=platform)
    for provider in ("anthropic", "openai", "claude-cli", "codex-cli", "mock", "auto"):
        assert _answer_reserve(d, provider, "m", 200_000) is None, provider
        assert _reserve_kw(d, provider, "m", 200_000) == {}
    assert _answer_reserve(d, "custom", MODEL, 200_000) == 32_768
    assert _answer_reserve(d, "custom", MODEL, 16_000) == 4_000


async def test_a_planned_long_chat_on_a_known_window_fits_the_first_time(tmp_path):
    """END TO END through the real chat lane, the real router and the real
    adapter: a long conversation on a 16,000-token local model (pinned) whose
    server enforces that window. RED before v1.387.0: the planner held back
    4,000 while the adapter asked 4,096 on top of a transcript it did not
    fully count (tool definitions, the wire's own keys), so the server refused
    it and the lane had to shrink and resend. Now the ONE request fits."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    history = [
        ChatMessageBody(role=m["role"], content=m["content"]) for m in _conversation(60, 1_400)
    ]
    history.append(ChatMessageBody(role="user", content="and now, in one line: what did we decide?"))
    with FakeEndpoint(lambda b: text_reply("We decided to ship."), context_len=16_000) as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 16_000})
        out = await run_chat_turn(
            platform, BUILTIN_PERSONAS,
            ChatBody(messages=history, provider="custom", model=MODEL),
        )
        bodies = srv.chats()
    assert out["reply"].startswith("We decided to ship.")
    assert len(bodies) == 1, [(srv.prompt_tokens(b), b["max_tokens"]) for b in bodies]
    assert srv.prompt_tokens(bodies[0]) + bodies[0]["max_tokens"] <= 16_000


async def test_a_long_conversation_on_a_large_window_still_leaves_a_full_answer(tmp_path):
    """The other half of the agreement, END TO END: on a 131,072-token model a
    conversation far longer than the window is planned with the reply
    budget's reserve (32,768 — the cap), so the request still asks for a full
    answer and fits. With the classic 4,096 reserve the planner filled the
    window and the fitted ask collapsed to a few thousand tokens. (RED before
    v1.387.0 too: the ask was the fixed 4,096.)"""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    history = [
        # 120 long turns (the planner keeps at most 60, ~3k tokens each):
        # far more than the window holds.
        ChatMessageBody(role=m["role"], content=m["content"]) for m in _conversation(120, 11_500)
    ]
    history.append(ChatMessageBody(role="user", content="sum it up"))
    from iron_jarvis.daemon.chat_stream import stream_chat_turn

    with FakeEndpoint(lambda b: text_reply("Summary."), context_len=131_072) as srv:
        platform = _platform(tmp_path, srv.url, pins={f"custom::{MODEL}": 131_072})
        # review: the STREAMED lane — the one that asks the full budget
        gen = await stream_chat_turn(
            platform, BUILTIN_PERSONAS, ChatBody(messages=history, provider="custom", model=MODEL),
        )
        async for _ in gen:
            pass
        (body,) = srv.chats()
        # ...and the POST lane: not streamed, so it asks 4,096 — out of the
        # SAME planned room (one rule for both lanes; the extra room covers
        # the adapter's estimate slack, which on a long prompt is larger than
        # 4,096 — with the classic reserve the ask collapsed to 1,024)
        await run_chat_turn(
            platform, BUILTIN_PERSONAS, ChatBody(messages=history, provider="custom", model=MODEL),
        )
        post = srv.chats()[-1]
    p = srv.prompt_tokens(body)
    assert p > 80_000, "the conversation was planned up to the window"
    assert p + body["max_tokens"] <= 131_072
    assert body["max_tokens"] >= 32_768 - max(256, p // 10)
    assert post["max_tokens"] == 4_096
    assert srv.prompt_tokens(post) + 4_096 <= 131_072


# --------------------------------------------------------------------------- #
# 6. fleet nodes: both protocols are local endpoints the user owns
# --------------------------------------------------------------------------- #
def _fleet(node_kw: dict):
    from pathlib import Path
    from types import SimpleNamespace

    from iron_jarvis.fleet.models import FleetNode
    from iron_jarvis.fleet.registry import FleetRegistry
    from iron_jarvis.providers.manager import ProviderManager

    base = dict(id="box", label="Box", source="user", routable=True, default_model=MODEL)
    base.update(node_kw)
    node = FleetNode(**base)
    cfg = SimpleNamespace(ollama_base_url="", ollama_model="", custom_base_url="", custom_model="",
                          fleet_nodes=[node.model_dump()], home=Path("."))
    registry = FleetRegistry(cfg, persist=lambda *a, **k: None)
    mgr = ProviderManager(default_model="m", dynamic_available=registry.reachable)
    registry.register_providers(mgr, secret_resolver=lambda name: None)
    return mgr


async def test_an_openai_fleet_node_is_budgeted():
    """RED before v1.387.0 (4096 on every fleet node)."""
    with FakeEndpoint(lambda b: text_reply("ok")) as srv:
        mgr = _fleet({"base_url": srv.url})
        adapter = mgr.get("fleet-box", MODEL)
        async for _ in adapter.stream(system="s", messages=_msgs("hi"), tools=[WRITE_SPEC]):
            pass
        body = srv.chats()[-1]
    assert body["max_tokens"] > 4_096
    assert mgr.reply_cap("fleet-box", MODEL) == 32_768
    # review: what a not-streamed request plans for (`NON_STREAM_CAP`)
    assert mgr.reply_cap("fleet-box", MODEL, stream=False) == 4_096


def _anthropic_adapter(respond):
    from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter
    from iron_jarvis.fleet.models import FleetNode

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request, len(seen))

    node = FleetNode(id="abox", label="A", base_url="http://abox.test:4000", source="user",
                     routable=True, default_model=MODEL, protocol="anthropic")
    adapter = AnthropicFleetAdapter(
        node=node, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return adapter, seen


def _anthropic_sse(events: list[dict]) -> httpx.Response:
    data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=data.encode(), headers={"content-type": "text/event-stream"})


async def test_an_anthropic_fleet_node_is_budgeted_and_reports_a_cut_call():
    """RED before v1.387.0: ``max_tokens`` 4096, and a ``tool_use`` whose
    streamed input stopped at the limit came back as a call with ``{}``."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "write_file", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"path": "calc.py", "content": "class Calc'}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 4096}},
        {"type": "message_stop"},
    ]
    adapter, seen = _anthropic_adapter(lambda req, n: _anthropic_sse(events))
    final = None
    async for frame in adapter.stream(system="s", messages=_msgs("hi"), tools=[WRITE_SPEC]):
        if frame["type"] == "final":
            final = frame["response"]
    body = json.loads(seen[0].content)
    assert body["max_tokens"] > 4_096
    assert final.finish_reason == "max_tokens"
    assert final.tool_calls == [] and final.cut_calls == ["write_file"]
    assert final.extra["max_tokens"] == body["max_tokens"]


@pytest.mark.parametrize("stream", [False, True])
async def test_an_anthropic_fleet_node_retries_a_length_refusal_once(stream):
    # (review: a not-streamed request asks at most 4,096 — its refusal names
    # a prompt that leaves less than that)
    inp = 1_000 if stream else 3_000
    words = ("This model's maximum context length is 6000 tokens. However, you requested "
             f"{inp + (32000 if stream else 4096)} tokens ({inp} in the messages, "
             f"{32000 if stream else 4096} in the completion).")

    def respond(req, n):
        if n == 1:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": words}})
        if stream:
            return _anthropic_sse([
                {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                {"type": "message_stop"},
            ])
        return httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 9, "output_tokens": 1}})

    adapter, seen = _anthropic_adapter(respond)
    if stream:
        final = None
        async for frame in adapter.stream(system="s", messages=_msgs("hi"), tools=[]):
            if frame["type"] == "final":
                final = frame["response"]
    else:
        final = await adapter.complete(system="s", messages=_msgs("hi"), tools=[])
    assert final.text == "ok"
    assert len(seen) == 2
    assert json.loads(seen[1].content)["max_tokens"] == 6_000 - inp - 16
