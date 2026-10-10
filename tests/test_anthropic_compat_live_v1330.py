"""OPT-IN: the Anthropic-compatible adapter against a REAL server (v1.330.0).

Skipped unless ``IJ_LIVE_ANTHROPIC_URL`` is set, so CI and the normal suite
never touch the network. Run it against the user's own LiteLLM proxy on the
DGX Sparks (local hardware on the tailnet, not a cloud API)::

    IJ_LIVE_ANTHROPIC_URL=http://100.66.161.52:4000 \
        uv run pytest -q -s tests/test_anthropic_compat_live_v1330.py

``IJ_LIVE_ANTHROPIC_MODEL`` picks the model (default ``glm``) and
``IJ_LIVE_ANTHROPIC_KEY`` the key (default ``none``: that proxy needs none).
Every request is small (``max_tokens`` 256). It drives the REAL adapter
(``fleet.adapter.adapter_for`` on a node with ``protocol="anthropic"``), the
real model listings (the "Fetch available models" probe, the sampler's probe,
the router's liveness pre-probe) and, for the refusal, the real router with a
node on a dead local port. The model's words vary run to run, so the asserts
check what a prompt pins (a demanded word, a recalled name, a tool call), and
each test prints what came back (``-s``).
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

LIVE_URL = os.environ.get("IJ_LIVE_ANTHROPIC_URL", "").strip()
MODEL = os.environ.get("IJ_LIVE_ANTHROPIC_MODEL", "").strip() or "glm"
KEY = os.environ.get("IJ_LIVE_ANTHROPIC_KEY", "").strip() or "none"

pytestmark = pytest.mark.skipif(
    not LIVE_URL, reason="live server test: set IJ_LIVE_ANTHROPIC_URL to run it"
)

from iron_jarvis.fleet.adapter import adapter_for  # noqa: E402
from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter  # noqa: E402
from iron_jarvis.fleet.models import FleetNode  # noqa: E402
from iron_jarvis.providers.adapters.base import (  # noqa: E402
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
)

TOOL = {
    "name": "get_time",
    "description": "Get the current local time in a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}
ASK_TIME = [LLMMessage(role="user", content="What time is it in Paris? Use the get_time tool.")]


def _node(url: str = LIVE_URL, **kw) -> FleetNode:
    base = dict(
        id="livebox", label="Live box", base_url=url, source="user", routable=True,
        default_model=MODEL, protocol="anthropic",
    )
    base.update(kw)
    return FleetNode(**base)


def _adapter(url: str = LIVE_URL) -> AnthropicFleetAdapter:
    a = adapter_for(_node(url), credential=lambda: KEY)
    assert isinstance(a, AnthropicFleetAdapter)
    a.max_tokens = 256
    return a


def _show(tag: str, r: LLMResponse) -> None:
    print(f"\n[{tag}] " + json.dumps({
        "text": r.text, "thinking": r.thinking[:160], "finish": r.finish_reason,
        "usage": r.usage, "calls": [(c.id, c.name, c.arguments) for c in r.tool_calls],
    }, ensure_ascii=False))


async def _streamed(a, **kw) -> tuple[list[dict], LLMResponse]:
    frames = [f async for f in a.stream(**kw)]
    assert frames and frames[-1]["type"] == "final"
    return frames, frames[-1]["response"]


# --------------------------------------------------------------------------- #
# answers
# --------------------------------------------------------------------------- #
async def test_live_plain_answer():
    r = await _adapter().complete(
        system="", messages=[LLMMessage(role="user", content="Say hi in three words.")], tools=[]
    )
    _show("plain", r)
    assert r.text.strip()
    assert r.finish_reason == "stop"
    assert r.usage["input_tokens"] > 0 and r.usage["output_tokens"] > 0


async def test_live_streamed_answer_arrives_in_pieces_and_matches_its_final():
    frames, final = await _streamed(
        _adapter(), system="",
        messages=[LLMMessage(role="user", content="Count from 1 to 5, digits only.")], tools=[],
    )
    _show("stream", final)
    texts = [f["text"] for f in frames if f["type"] == "text"]
    print(f"[stream] {len(frames)} frames, {len(texts)} text pieces, kinds "
          f"{sorted({f['type'] for f in frames})}")
    # Really streamed: SSE pieces before the final, not one degraded body.
    assert texts and len(frames) >= 3
    assert "".join(texts) == final.text
    assert all(d in final.text for d in "12345")
    assert final.finish_reason == "stop"
    assert final.usage["input_tokens"] > 0 and final.usage["output_tokens"] > 0


async def test_live_system_prompt_is_obeyed():
    r = await _adapter().complete(
        system="Whatever the user says, reply with exactly the single word PINEAPPLE.",
        messages=[LLMMessage(role="user", content="Tell me about the weather.")], tools=[],
    )
    _show("system", r)
    assert "PINEAPPLE" in r.text.upper()


async def test_live_multi_turn_history_is_read():
    hist = [
        LLMMessage(role="user", content="My name is Ada. Remember it."),
        LLMMessage(role="assistant", content="Got it, Ada."),
        LLMMessage(role="user", content="What is my name? Answer with the name only."),
    ]
    r = await _adapter().complete(system="", messages=hist, tools=[])
    _show("multi-turn", r)
    assert "ada" in r.text.lower()


# --------------------------------------------------------------------------- #
# a tool round: call, then the result goes back
# --------------------------------------------------------------------------- #
def _the_call(r: LLMResponse) -> ToolCall:
    assert r.finish_reason == "tool_use", r
    assert [c.name for c in r.tool_calls] == ["get_time"]
    call = r.tool_calls[0]
    assert "paris" in str(call.arguments.get("city", "")).lower()
    assert call.id
    return call


async def test_live_tool_call_round_trip_plain():
    a = _adapter()
    r = await a.complete(system="", messages=ASK_TIME, tools=[TOOL])
    _show("tool call", r)
    call = _the_call(r)
    after = ASK_TIME + [
        LLMMessage(role="assistant", content=r.text, tool_calls=[call]),
        LLMMessage(role="tool", content="14:05", tool_call_id=call.id),
    ]
    r2 = await a.complete(system="", messages=after, tools=[TOOL])
    _show("tool result", r2)
    assert not r2.tool_calls
    assert "14:05" in r2.text or "2:05" in r2.text


async def test_live_tool_call_round_trip_streamed():
    a = _adapter()
    _frames, r = await _streamed(a, system="", messages=ASK_TIME, tools=[TOOL])
    _show("tool call streamed", r)
    call = _the_call(r)
    after = ASK_TIME + [
        LLMMessage(role="assistant", content=r.text, tool_calls=[call]),
        LLMMessage(role="tool", content="14:05", tool_call_id=call.id),
    ]
    _frames2, r2 = await _streamed(a, system="", messages=after, tools=[TOOL])
    _show("tool result streamed", r2)
    assert "14:05" in r2.text or "2:05" in r2.text


# --------------------------------------------------------------------------- #
# the model listings the app reads off this server
# --------------------------------------------------------------------------- #
def test_live_fetch_available_models_lists_the_model():
    from iron_jarvis.daemon.routes.connections import _probe_anthropic_models

    out = _probe_anthropic_models(LIVE_URL, KEY, timeout_s=15.0)
    print(f"\n[fetch models] {json.dumps(out)}")
    assert out["error"] is None and out["protocol"] == "anthropic"
    assert MODEL in out["models"]


def test_live_sampler_probe_reads_the_node_online_with_its_models():
    from iron_jarvis.fleet.probes import probe_node

    snap, _children = probe_node(_node())
    print(f"\n[probe] status={snap.status} models={[m.id for m in snap.models]} "
          f"reason={snap.metrics_reason!r}")
    assert snap.status == "online"
    assert MODEL in [m.id for m in snap.models]


async def test_live_router_liveness_probe_finds_the_server_alive():
    from iron_jarvis.providers.router import ModelRouter

    from iron_jarvis.core.events import EventBus

    router = ModelRouter(SimpleNamespace(), default_provider="mock", event_bus=EventBus())
    a = _adapter()
    print(f"\n[liveness] url={ModelRouter._models_url(a._endpoint)}")
    assert await router._local_liveness(a) is None  # None = not provably dead


# --------------------------------------------------------------------------- #
# the wrong address: refused by name, never another provider
# --------------------------------------------------------------------------- #
def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Cloud(LLMAdapter):
    def __init__(self) -> None:
        self.provider, self.model, self.calls = "cloudy", "big", 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.calls += 1
        return LLMResponse(text="cloud answer", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **_kw):
        self.calls += 1
        yield {"type": "final", "response": LLMResponse(text="cloud answer")}


async def test_live_a_dead_address_refuses_by_its_label_and_never_switches_provider():
    from iron_jarvis.core.events import EventBus
    from iron_jarvis.fleet.registry import FleetRegistry
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.providers.router import ModelRouter

    dead = f"http://127.0.0.1:{_dead_port()}"
    node = _node(dead, label="Dead box", id="deadbox")
    cfg = SimpleNamespace(
        ollama_base_url="", ollama_model="", custom_base_url="", custom_model="",
        fleet_nodes=[node.model_dump()], home=Path("."),
    )
    registry = FleetRegistry(cfg, persist=lambda *a, **k: None)
    mgr = ProviderManager(default_model="m", dynamic_available=registry.reachable)
    cloud = _Cloud()
    mgr.register("cloudy", lambda model=None: cloud)
    assert registry.register_providers(mgr, secret_resolver=lambda name: None) == 1
    bus = EventBus()
    router = ModelRouter(mgr, default_provider="cloudy", event_bus=bus, local_policy=lambda: "refuse")
    msgs = [LLMMessage(role="user", content="hi")]

    with pytest.raises(Exception) as ei:
        await router.complete(provider="fleet-deadbox", system="", messages=msgs, tools=[])
    print(f"\n[refusal complete] {type(ei.value).__name__}: {ei.value}")
    assert "Dead box" in str(ei.value)
    with pytest.raises(Exception) as ei2:
        async for _f in router.stream(provider="fleet-deadbox", system="", messages=msgs, tools=[]):
            pass
    print(f"[refusal stream] {type(ei2.value).__name__}: {ei2.value}")
    assert "Dead box" in str(ei2.value)
    assert cloud.calls == 0  # never handed to another provider
    assert isinstance(ei.value, (ProviderError, RuntimeError))
