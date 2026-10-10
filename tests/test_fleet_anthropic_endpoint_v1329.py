"""An Anthropic-compatible custom endpoint can be ADDED and actually ANSWERS (v1.329.0, H3).

Before this, "Fetch available models" could list an Anthropic-only server's
models, but a saved endpoint always chatted the OpenAI way
(``POST /v1/chat/completions`` + Bearer), so a picked model never answered.
Now a fleet node carries ``protocol`` and ``fleet/adapter.adapter_for`` picks
``AnthropicFleetAdapter`` (``POST /v1/messages``, ``x-api-key`` +
``anthropic-version``) for ``anthropic``.

Every server here is FAKE: an ``httpx.MockTransport`` handler, no network.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from iron_jarvis.fleet import anthropic_compat
from iron_jarvis.fleet.adapter import FleetAdapter, adapter_for
from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.probes import probe_node
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
)

KEY = "sk-fake-anthropic-compat-0123456789"
BASE = "http://anthropic-box.test:4000"


def _node(**kw) -> FleetNode:
    base = dict(
        id="box",
        label="Box",
        base_url=BASE,
        source="user",
        routable=True,
        default_model="claude-compat-1",
        api_key_name="endpoint_box_key",
        protocol="anthropic",
    )
    base.update(kw)
    return FleetNode(**base)


class FakeServer:
    """Records every request; answers with whatever the test queued."""

    def __init__(self, respond) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=False)

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content.decode("utf-8"))


def _adapter(server: FakeServer, *, key: str | None = KEY, node: FleetNode | None = None):
    return AnthropicFleetAdapter(
        node=node or _node(),
        credential=(lambda: key) if key is not None else None,
        http=server.client(),
    )


def _reply(**kw) -> dict:
    out = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-compat-1",
        "content": [{"type": "text", "text": "Hello from the box."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 5},
    }
    out.update(kw)
    return out


def _sse(events: list[dict]) -> bytes:
    lines = []
    for ev in events:
        lines.append(f"event: {ev['type']}")
        lines.append(f"data: {json.dumps(ev)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _stream_events(*, tool: bool = False) -> list[dict]:
    evs = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_s",
                "type": "message",
                "role": "assistant",
                "content": [],
                "usage": {"input_tokens": 20, "output_tokens": 1, "cache_read_input_tokens": 4},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo."}},
        {"type": "content_block_stop", "index": 0},
    ]
    if tool:
        evs += [
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_9", "name": "read_file", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path": "no'},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": 'tes.md"}'},
            },
            {"type": "content_block_stop", "index": 1},
        ]
    evs += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]
    return evs


def _sse_response(events: list[dict], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=_sse(events), headers={"content-type": "text/event-stream"}
    )


TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
]


# --------------------------------------------------------------------------- #
# the wire: request shape, a plain reply, usage
# --------------------------------------------------------------------------- #
async def test_complete_posts_messages_api_with_x_api_key_and_maps_the_reply():
    server = FakeServer(lambda r: httpx.Response(200, json=_reply(
        usage={"input_tokens": 11, "output_tokens": 5, "cache_read_input_tokens": 3,
               "cache_creation_input_tokens": 2},
    )))
    adapter = _adapter(server)
    history = [
        LLMMessage(role="user", content="Read notes.md"),
        LLMMessage(
            role="assistant",
            content="Reading it.",
            tool_calls=[ToolCall(id="toolu_1", name="read_file", arguments={"path": "notes.md"})],
        ),
        LLMMessage(role="tool", content="line one", tool_call_id="toolu_1", name="read_file"),
        LLMMessage(role="user", content="Now summarise it."),
    ]
    res = await adapter.complete(system="Be brief.", messages=history, tools=TOOLS)

    req = server.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{BASE}/v1/messages"
    assert req.headers["x-api-key"] == KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in {k.lower() for k in req.headers}
    body = server.body()
    assert body["system"] == "Be brief."
    assert body["model"] == "claude-compat-1"
    assert body["max_tokens"] > 0
    assert "stream" not in body
    assert body["tools"] == TOOLS
    # The tool turn: tool_use on the assistant, tool_result + the next user
    # line merged into ONE user turn (strict servers refuse two in a row).
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][1] == {
        "type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "notes.md"},
    }
    tail = body["messages"][2]["content"]
    assert tail[0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "line one"}
    assert tail[1] == {"type": "text", "text": "Now summarise it."}

    assert res.text == "Hello from the box."
    assert res.finish_reason == "stop"
    # input_tokens is the WHOLE prompt (the Messages API counts cache outside it)
    assert res.usage["input_tokens"] == 16
    assert res.usage["output_tokens"] == 5
    assert res.usage["cache_read_input_tokens"] == 3
    assert res.usage["cache_creation_input_tokens"] == 2


async def test_complete_reads_tool_use_blocks():
    server = FakeServer(lambda r: httpx.Response(200, json=_reply(
        content=[
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "toolu_7", "name": "read_file", "input": {"path": "a.txt"}},
        ],
        stop_reason="tool_use",
    )))
    res = await _adapter(server).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=TOOLS)
    assert res.finish_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in res.tool_calls] == [("toolu_7", "read_file", {"path": "a.txt"})]
    assert "system" not in server.body()  # empty system is left out, not sent as ""


async def test_keyless_node_sends_no_key_header():
    server = FakeServer(lambda r: httpx.Response(200, json=_reply()))
    await _adapter(server, key=None).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert "x-api-key" not in server.requests[0].headers


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
async def _collect(agen) -> list[dict]:
    return [f async for f in agen]


async def test_stream_yields_text_deltas_then_a_final_with_usage():
    server = FakeServer(lambda r: _sse_response(_stream_events()))
    frames = await _collect(_adapter(server).stream(
        system="s", messages=[LLMMessage(role="user", content="hi")], tools=[],
    ))
    assert server.body()["stream"] is True
    assert [f["text"] for f in frames if f["type"] == "text"] == ["Hel", "lo."]
    final = frames[-1]
    assert final["type"] == "final"
    resp: LLMResponse = final["response"]
    assert resp.text == "Hello."
    assert resp.finish_reason == "stop"
    assert resp.usage["input_tokens"] == 24  # 20 + 4 cache reads
    assert resp.usage["output_tokens"] == 9  # message_delta's cumulative count wins


async def test_stream_assembles_a_tool_call_from_input_json_deltas():
    server = FakeServer(lambda r: _sse_response(_stream_events(tool=True)))
    frames = await _collect(_adapter(server).stream(
        system="", messages=[LLMMessage(role="user", content="read it")], tools=TOOLS,
    ))
    resp = frames[-1]["response"]
    assert resp.finish_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in resp.tool_calls] == [
        ("toolu_9", "read_file", {"path": "notes.md"})
    ]


async def test_stream_that_ends_before_the_reply_is_finished_raises_interrupted():
    from iron_jarvis.providers.router import local_failure_kind

    cut = _stream_events()[:4]  # no message_delta, no message_stop
    server = FakeServer(lambda r: _sse_response(cut))
    with pytest.raises(httpx.RemoteProtocolError) as ei:
        await _collect(_adapter(server).stream(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[],
        ))
    assert local_failure_kind(ei.value) == "interrupted"
    assert "fleet-box" in str(ei.value)


async def test_stream_error_event_is_typed_like_its_http_twin():
    events = _stream_events()[:2] + [
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    ]
    server = FakeServer(lambda r: _sse_response(events))
    with pytest.raises(ProviderError) as ei:
        await _collect(_adapter(server).stream(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[],
        ))
    assert ei.value.status_code == 529 and ei.value.transient is True


async def test_stream_falls_back_to_one_json_body_when_the_server_ignores_stream():
    server = FakeServer(lambda r: httpx.Response(200, json=_reply()))
    frames = await _collect(_adapter(server).stream(
        system="", messages=[LLMMessage(role="user", content="hi")], tools=[],
    ))
    assert [f["type"] for f in frames] == ["text", "final"]
    assert frames[-1]["response"].text == "Hello from the box."


# --------------------------------------------------------------------------- #
# errors, typed the OpenAI adapter's way
# --------------------------------------------------------------------------- #
async def test_401_is_a_permanent_typed_error_and_never_echoes_the_key():
    def refuse(_r):
        return httpx.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error",
                                              "message": f"invalid x-api-key {KEY}"}},
        )

    server = FakeServer(refuse)
    for call in ("complete", "stream"):
        with pytest.raises(ProviderError) as ei:
            if call == "complete":
                await _adapter(server).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
            else:
                await _collect(_adapter(server).stream(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))
        assert ei.value.status_code == 401
        assert ei.value.transient is False
        assert "fleet-box" in str(ei.value)
        assert KEY not in str(ei.value)
        assert "invalid x-api-key" in str(ei.value)


async def test_429_carries_retry_after_and_is_transient():
    server = FakeServer(lambda r: httpx.Response(
        429, headers={"retry-after": "7"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    ))
    with pytest.raises(ProviderError) as ei:
        await _adapter(server).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert ei.value.status_code == 429
    assert ei.value.retry_after == 7
    assert ei.value.transient is True


@pytest.mark.parametrize("status", [500, 503, 529])
async def test_5xx_is_transient(status):
    server = FakeServer(lambda r: httpx.Response(status, text="upstream down"))
    with pytest.raises(ProviderError) as ei:
        await _collect(_adapter(server).stream(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))
    assert ei.value.status_code == status and ei.value.transient is True


async def test_timeout_stays_the_native_httpx_type_the_router_reads_as_timeout():
    from iron_jarvis.providers.router import local_failure_kind

    def slow(_r):
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(httpx.ReadTimeout) as ei:
        await _adapter(FakeServer(slow)).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert local_failure_kind(ei.value) == "timeout"


async def test_a_reply_that_is_not_json_is_named_not_parsed_into_a_blank_answer():
    server = FakeServer(lambda r: httpx.Response(200, text="<html>login page</html>"))
    with pytest.raises(ProviderError) as ei:
        await _adapter(server).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert "not with JSON" in str(ei.value)
    assert ei.value.transient is False
    server2 = FakeServer(lambda r: httpx.Response(200, json={"choices": []}))  # OpenAI shape
    with pytest.raises(ProviderError) as ei2:
        await _adapter(server2).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert "Messages API" in str(ei2.value)


async def test_a_redirect_is_not_followed_so_the_key_stays_put():
    server = FakeServer(lambda r: httpx.Response(307, headers={"location": "http://elsewhere.test/v1/messages"}))
    with pytest.raises(ProviderError) as ei:
        await _adapter(server).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert len(server.requests) == 1  # never followed
    assert "redirect" in str(ei.value)


async def test_an_account_sign_in_token_is_refused_before_any_request():
    server = FakeServer(lambda r: httpx.Response(200, json=_reply()))
    with pytest.raises(ProviderError):
        await _adapter(server, key="sk-ant-oat01-abc").complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    assert server.requests == []


async def test_the_key_never_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    ok = FakeServer(lambda r: _sse_response(_stream_events()))
    await _collect(_adapter(ok).stream(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))
    await _adapter(FakeServer(lambda r: httpx.Response(200, json=_reply()))).complete(
        system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )
    bad = FakeServer(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    with pytest.raises(ProviderError) as ei:
        await _adapter(bad).complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    logging.getLogger("iron_jarvis").exception("relay of the failure: %s", ei.value)
    assert caplog.records  # something WAS logged, so the check is not vacuous
    assert KEY not in caplog.text


# --------------------------------------------------------------------------- #
# picking the adapter by protocol
# --------------------------------------------------------------------------- #
def test_adapter_for_picks_by_protocol_and_both_are_honest_about_capabilities():
    a = adapter_for(_node())
    o = adapter_for(_node(protocol="openai"))
    assert isinstance(a, AnthropicFleetAdapter) and not isinstance(a, FleetAdapter)
    assert isinstance(o, FleetAdapter)
    assert a.provider == o.provider == "fleet-box"
    assert a.capabilities()["tool_use"] is False  # unverified = not capable
    assert adapter_for(_node(tool_use=True)).capabilities()["tool_use"] is True


def _registry(*nodes: FleetNode) -> FleetRegistry:
    cfg = SimpleNamespace(
        ollama_base_url="", ollama_model="", custom_base_url="", custom_model="",
        fleet_nodes=[n.model_dump() for n in nodes], home=Path("."),
    )
    return FleetRegistry(cfg, persist=lambda *a, **k: None)


class _Cloud(LLMAdapter):
    def __init__(self) -> None:
        self.provider, self.model = "cloudy", "big"
        self.calls = 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.calls += 1
        return LLMResponse(text="cloud answer", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **_kw):
        self.calls += 1
        yield {"type": "final", "response": LLMResponse(text="cloud answer")}


@pytest.fixture
def _no_sleep(monkeypatch):
    import iron_jarvis.providers.router as rmod

    async def _sleep(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _sleep)


def _routed(monkeypatch, respond):
    """A real ProviderManager + ModelRouter over a registry holding one
    anthropic node, with the adapter's HTTP client swapped for a fake."""
    from iron_jarvis.core.events import EventBus
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.providers.router import ModelRouter

    server = FakeServer(respond)
    monkeypatch.setattr(anthropic_compat, "_new_client", server.client)
    registry = _registry(_node())
    mgr = ProviderManager(default_model="m", dynamic_available=registry.reachable)
    cloud = _Cloud()
    mgr.register("cloudy", lambda model=None: cloud)
    assert registry.register_providers(mgr, secret_resolver=lambda name: KEY if name == "endpoint_box_key" else None) == 1
    router = ModelRouter(mgr, default_provider="cloudy", event_bus=EventBus(), local_policy=lambda: "refuse")
    return server, router, cloud


async def test_the_router_reaches_an_anthropic_node_through_its_own_adapter(monkeypatch, _no_sleep):
    def respond(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":  # the router's liveness pre-probe
            return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error"}})
        return httpx.Response(200, json=_reply())

    server, router, cloud = _routed(monkeypatch, respond)
    res = await router.complete(
        provider="fleet-box", system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )
    assert res.response.text == "Hello from the box."
    assert res.provider == "fleet-box"
    posts = [r for r in server.requests if r.method == "POST"]
    assert posts and posts[0].url.path == "/v1/messages"
    assert posts[0].headers["x-api-key"] == KEY  # the vault key, resolved at request time
    assert cloud.calls == 0

    frames = [f async for f in router.stream(
        provider="fleet-box", system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )]
    assert frames[-1]["type"] == "final"


async def test_a_failing_anthropic_node_refuses_by_name_and_never_fails_over(monkeypatch, _no_sleep):
    server, router, cloud = _routed(
        monkeypatch, lambda r: httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
    )
    with pytest.raises(ProviderError) as ei:
        await router.complete(
            provider="fleet-box", system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    assert "fleet-box" in str(ei.value)
    assert cloud.calls == 0  # never handed to another provider


# --------------------------------------------------------------------------- #
# the sampler's probe asks the Anthropic way
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status: int, payload=None) -> None:
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_probe_reads_a_keyed_anthropic_node_as_reachable_not_offline():
    seen = []

    def get(url, headers=None):
        seen.append((url, headers))
        return _Resp(401, {"type": "error"})

    snap, _ = probe_node(_node(), get=get)
    assert snap.status == "online"
    assert seen[0][0] == f"{BASE}/v1/models"
    assert seen[0][1]["anthropic-version"] == "2023-06-01"
    assert "key" in snap.metrics_reason


def test_probe_lists_models_and_reports_a_dead_node_offline():
    snap, _ = probe_node(_node(), get=lambda url, headers=None: _Resp(200, {"data": [{"id": "claude-compat-1"}]}))
    assert [m.id for m in snap.models] == ["claude-compat-1"]

    def dead(url, headers=None):
        raise httpx.ConnectError("refused")

    snap, _ = probe_node(_node(), get=dead)
    assert snap.status == "offline"


# --------------------------------------------------------------------------- #
# the record: persisted, round-tripped through the routes, validated
# --------------------------------------------------------------------------- #
def test_a_row_saved_before_the_field_existed_loads_as_openai():
    row = _node().model_dump()
    row.pop("protocol")
    reg = _registry()
    reg.config.fleet_nodes = [row]
    assert reg.get("box").protocol == "openai"


def test_registry_refuses_an_unknown_protocol(tmp_path):
    reg = _registry(_node())
    with pytest.raises(ValueError):
        reg.update("box", protocol="grpc")
    with pytest.raises(Exception):
        reg.add(_node(id="other", protocol="grpc"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path)))


DEAD = "http://127.0.0.1:9"  # a closed port: detection and the first probe fail fast


def test_protocol_round_trips_through_the_routes_and_is_persisted(client, tmp_path):
    import tomllib

    r = client.post(
        "/fleet/nodes",
        json={"base_url": DEAD, "label": "Anth box", "routable": True,
              "default_model": "claude-compat-1", "protocol": "Anthropic"},
    )
    assert r.status_code == 200, r.text
    node = r.json()["node"]
    assert node["protocol"] == "anthropic"
    nid = node["id"]

    listed = {row["node"]["id"]: row["node"] for row in client.get("/fleet/snapshot?refresh=1").json()["nodes"]}
    assert listed[nid]["protocol"] == "anthropic"

    providers = client.app.state.platform.providers
    assert isinstance(providers.get(f"fleet-{nid}", None), AnthropicFleetAdapter)

    home = client.app.state.platform.config.home
    raw = tomllib.loads((Path(home) / "config.toml").read_text("utf-8"))
    saved = {row["id"]: row for row in raw["fleet_nodes"]}
    assert saved[nid]["protocol"] == "anthropic"

    r = client.patch(f"/fleet/nodes/{nid}", json={"protocol": "openai"})
    assert r.status_code == 200, r.text
    assert r.json()["node"]["protocol"] == "openai"
    assert isinstance(providers.get(f"fleet-{nid}", None), FleetAdapter)

    # A node added without the field is an OpenAI node, as before.
    r = client.post("/fleet/nodes", json={"base_url": DEAD, "label": "plain", "routable": True})
    assert r.json()["node"]["protocol"] == "openai"


def test_an_invalid_protocol_is_refused_in_plain_words(client):
    r = client.post("/fleet/nodes", json={"base_url": DEAD, "protocol": "grpc"})
    assert r.status_code == 400
    assert r.json()["detail"] == "The protocol must be openai or anthropic."
    ok = client.post("/fleet/nodes", json={"base_url": DEAD, "label": "x"}).json()["node"]
    r = client.patch(f"/fleet/nodes/{ok['id']}", json={"protocol": "grpc"})
    assert r.status_code == 400
    # An Anthropic node needs a real http(s) address (no scheme guessing).
    r = client.post("/fleet/nodes", json={"base_url": "anthropic-box:4000", "protocol": "anthropic"})
    assert r.status_code == 400
    assert "http://" in r.json()["detail"]
