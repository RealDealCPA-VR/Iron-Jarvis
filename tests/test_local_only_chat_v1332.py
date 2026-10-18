"""Local only: a chat whose words never leave the user's network (v1.332.0).

The user: "a local toggle in the chat to indicate nothing hits the cloud in a
specific sessions without user permission." The dashboard's ask-first card is
the friendly front door; THIS file pins the lock, which is the server's:

* the predicate (``providers/egress.stays_on_this_network``) — by ADDRESS, not
  by name, fail closed;
* the router (both lanes): a cloud primary is refused before any probe,
  breaker or attempt; the one-message OK pins its provider; no default,
  sideways, capability swap or Auto pick ever leaves the network;
* every path that calls a model WITHOUT the router (one-shot utilities,
  compaction, the panel, sessions) and the chat-shaped routes around a turn
  (follow-ups, thread refs, setup persistence, the 409 / error-frame shape);
* the non-model egress of a turn (web tools, remote memory bases).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter, RouteResult


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _Ad(LLMAdapter):
    """A counting adapter. ``endpoint`` is what the predicate reads (a fake
    with no ``_client`` is never liveness-probed)."""

    def __init__(self, provider, *, endpoint="", vision=True, tools=True, fail=None, model="m"):
        self.provider, self.model = provider, model
        self._endpoint = endpoint
        self.vision, self.tools_ok, self.fail = vision, tools, fail
        self.calls = 0

    def capabilities(self):
        return {"tool_use": self.tools_ok, "vision": self.vision}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        yield {"type": "text", "text": "tok"}
        yield {"type": "final", "response": LLMResponse(text="tok", tool_calls=[], usage={})}


class _Manager:
    def __init__(self, adapters, available=None):
        self.adapters = adapters
        self._available = set(available if available is not None else adapters)

    def available(self, p):
        return p in self._available

    def has_available_api_provider(self):
        return True

    def runtime_provider_names(self):
        return sorted(p for p in self.adapters if p.startswith("fleet-"))

    def get(self, p, m=None):
        return self.adapters[p]


LOCAL = "http://127.0.0.1:11434/v1"
LAN = "http://spark2:8000/v1"


def _router(adapters, default, *, policy="refuse", available=None, **kw):
    return ModelRouter(
        _Manager(adapters, available), default_provider=default, event_bus=EventBus(),
        local_policy=lambda: policy, **kw,
    )


def _msgs(images=False):
    m = LLMMessage(role="user", content="what is in this picture")
    if images:
        m.images = [{"data_b64": "aGk=", "media_type": "image/png"}]
    return [m]


async def _drain(agen):
    return [f async for f in agen]


async def _complete(router, **kw):
    return await router.complete(system="s", messages=kw.pop("messages", _msgs()), tools=[], **kw)


async def _stream(router, **kw):
    return await _drain(router.stream(system="s", messages=kw.pop("messages", _msgs()), tools=[], **kw))


LANES = [_complete, _stream]


# --------------------------------------------------------------------------- #
# 1. The predicate: by address, fail closed.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434", "http://localhost:1234/v1", "http://10.0.0.5:8000",
        "http://172.20.1.1", "http://192.168.1.20:11434", "http://169.254.3.4",
        "http://100.101.102.103:8000", "http://[::1]:8000", "http://[fd12::1]:8000",
        "http://[fe80::1%25eth0]:8000", "http://spark1:8000", "spark2:8000",
        "http://box.local", "http://nas.lan:9000", "http://gpu.home.arpa",
        "http://llm.internal", "http://spark.tail1234.ts.net:8000",
    ],
)
def test_a_private_tailscale_single_label_or_local_name_host_stays_on_this_network(url):
    assert egress.private_host(url), url


@pytest.mark.parametrize(
    "url",
    [
        "https://ollama.com", "https://openrouter.ai/api/v1", "https://api.openai.com/v1",
        "http://8.8.8.8", "http://100.200.0.1", "http://192.0.2.1", "", "not a url at all//",
        "http://example.local.evil.com",
    ],
)
def test_a_public_or_unreadable_host_does_not(url):
    assert not egress.private_host(url), url


def test_a_custom_endpoint_is_judged_by_its_address_never_its_name():
    cfg = SimpleNamespace(custom_base_url="https://ollama.com/v1", ollama_base_url="http://127.0.0.1:11434")
    assert egress.stays_on_this_network("custom", None, cfg) is False
    assert egress.stays_on_this_network("ollama", None, cfg) is True
    cfg.custom_base_url = "https://openrouter.ai/api/v1"
    assert egress.stays_on_this_network("custom", None, cfg) is False


def test_a_cloud_provider_or_an_endpoint_with_no_address_is_off_the_network():
    for p in ("claude-cli", "codex-cli", "grok-cli", "anthropic", "openai", "google", "xai", "openrouter", "auto"):
        assert egress.stays_on_this_network(p) is False, p
    assert egress.stays_on_this_network("ollama", None, SimpleNamespace()) is False
    assert egress.stays_on_this_network("fleet-x") is False
    # opencode-cli is local by construction; the in-process demo sends nothing.
    assert egress.stays_on_this_network("opencode-cli") is True
    assert egress.stays_on_this_network("mock") is True


def test_the_offline_demo_model_never_leaves_the_pc():
    assert egress.stays_on_this_network("mock") is True


def test_the_auto_classifier_runs_only_when_it_stays_on_this_network(app):
    platform = app.state.platform
    classifier = _Ad("claude-cli")
    _cloud_default(app, classifier)
    platform.providers.configure_local(ollama_base_url="http://127.0.0.1:11434")
    platform.config.ollama_base_url = "http://127.0.0.1:11434"
    platform.config.routing_model = "claude-cli:x"
    msgs = [LLMMessage(role="user", content="please explain why the sky is blue in detail")]

    async def route(on):
        with egress.local_only_scope(on):
            return await platform.router._auto_route("s", msgs, [], None)

    decision = asyncio.run(route(True))
    assert classifier.calls == 0
    assert decision is None or egress.stays_on_this_network(
        decision["provider"], platform.providers, platform.config
    )
    # Anti-vacuity: outside the scope the cloud classifier is asked.
    asyncio.run(route(False))
    assert classifier.calls == 1


def test_a_fleet_node_is_read_off_the_adapter_the_manager_builds():
    node_lan = SimpleNamespace(base_url="http://192.168.1.9:8000", label="Spark")
    node_web = SimpleNamespace(base_url="https://llm.example.com", label="Hosted")
    mgr = _Manager({
        "fleet-a": SimpleNamespace(provider="fleet-a", node=node_lan),
        "fleet-b": SimpleNamespace(provider="fleet-b", node=node_web),
    })
    assert egress.stays_on_this_network("fleet-a", mgr) is True
    assert egress.stays_on_this_network("fleet-b", mgr) is False
    assert egress.friendly_name("fleet-a", mgr) == "Spark"


def test_the_refusal_names_the_provider_in_words_and_never_reads_transient():
    from iron_jarvis.providers.router import is_no_failover, is_transient_error

    exc = egress.LocalOnlyRefused("claude-cli", "opus")
    assert str(exc) == (
        "This chat is local only, so nothing was sent to Claude. Pick a local"
        " model, or allow Claude for this one message."
    )
    assert exc.payload() == {
        "detail": str(exc), "code": "local_only", "provider": "claude-cli", "model": "opus",
    }
    assert is_no_failover(exc) and not is_transient_error(exc)


# --------------------------------------------------------------------------- #
# 2. The router, both lanes.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_a_cloud_primary_is_refused_before_any_attempt_or_breaker(lane):
    cloud = _Ad("claude-cli")
    local = _Ad("ollama", endpoint=LOCAL)
    r = _router({"claude-cli": cloud, "ollama": local}, "claude-cli")
    # An OPEN circuit would refuse with "cooldown" if the breaker ran first.
    for _ in range(r.health.threshold):
        r.health.record_failure("claude-cli")

    async def go():
        with egress.local_only_scope(True):
            await lane(r)

    with pytest.raises(egress.LocalOnlyRefused) as caught:
        asyncio.run(go())
    assert caught.value.provider == "claude-cli"
    assert cloud.calls == 0 and local.calls == 0
    # Anti-vacuity: outside the scope the same request reaches the breaker.
    with pytest.raises(ProviderError, match="cooldown"):
        asyncio.run(lane(r))


@pytest.mark.parametrize("lane", LANES)
def test_the_provider_allowed_once_is_called_and_nothing_fails_over_from_it(lane):
    bad = ProviderError("the request was rejected", status_code=400)
    cloud = _Ad("claude-cli", fail=bad)
    local_default = _Ad("fleet-a", endpoint=LAN)
    other_cloud = _Ad("codex-cli")
    adapters = {"claude-cli": cloud, "fleet-a": local_default, "codex-cli": other_cloud}
    r = _router(adapters, "fleet-a")

    async def go():
        with egress.local_only_scope(True, "claude-cli"):
            await lane(r, provider="claude-cli")

    with pytest.raises(ProviderError) as caught:
        asyncio.run(go())
    assert caught.value is bad or "rejected" in str(caught.value)
    assert cloud.calls == 1
    assert local_default.calls == 0 and other_cloud.calls == 0
    # Anti-vacuity: without the OK the default stands in (fallback A).
    cloud.calls = 0
    asyncio.run(lane(r, provider="claude-cli"))
    assert local_default.calls == 1


def test_the_allowed_provider_answers_and_the_route_says_so():
    cloud = _Ad("claude-cli")
    r = _router({"claude-cli": cloud, "fleet-a": _Ad("fleet-a", endpoint=LAN)}, "fleet-a")

    async def go():
        with egress.local_only_scope(True, "claude-cli"):
            route = await _complete(r, provider="claude-cli")
            return route, egress.route_flags(route.provider, r.manager)

    route, flags = asyncio.run(go())
    assert route.provider == "claude-cli" and cloud.calls == 1
    # v1.351.0 (G14, additive): the answering model is a cloud CLI.
    assert flags == {"local_only": True, "allowed_once": True, "on_this_network": False}


@pytest.mark.parametrize("lane", LANES)
def test_a_cloud_default_or_sideways_is_never_tried_when_a_local_primary_fails(lane):
    boom = ProviderError("upstream exploded", status_code=500)
    local = _Ad("ollama", endpoint=LOCAL, fail=boom)
    cloud_default = _Ad("claude-cli")
    cloud_side = _Ad("codex-cli")
    lan = _Ad("fleet-b", endpoint=LAN)
    adapters = {"ollama": local, "claude-cli": cloud_default, "codex-cli": cloud_side, "fleet-b": lan}
    r = _router(adapters, "claude-cli", policy="failover", deadline_s=0.01)

    async def go():
        with egress.local_only_scope(True):
            return await lane(r, provider="ollama")

    asyncio.run(go())
    assert cloud_default.calls == 0 and cloud_side.calls == 0
    assert lan.calls == 1  # a machine on this network may still stand in
    # Anti-vacuity: outside the scope the cloud default stands in (A).
    local.calls = lan.calls = 0
    asyncio.run(lane(r, provider="ollama"))
    assert cloud_default.calls == 1


@pytest.mark.parametrize("lane", LANES)
def test_an_image_on_a_text_only_local_model_names_the_vision_provider_and_calls_no_cloud(lane):
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud}, "ollama")

    async def go():
        with egress.local_only_scope(True):
            await lane(r, messages=_msgs(images=True))

    with pytest.raises(egress.LocalOnlyRefused) as caught:
        asyncio.run(go())
    assert caught.value.provider == "claude-cli"
    assert cloud.calls == 0 and local.calls == 0
    # Anti-vacuity: outside the scope the capability swap goes to the cloud.
    asyncio.run(lane(r, messages=_msgs(images=True)))
    assert cloud.calls == 1


def test_an_image_swaps_to_a_vision_model_on_this_network_when_there_is_one():
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    lan_vision = _Ad("fleet-v", endpoint=LAN, vision=True)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud, "fleet-v": lan_vision}, "ollama")

    async def go():
        with egress.local_only_scope(True):
            return await _complete(r, messages=_msgs(images=True))

    route = asyncio.run(go())
    assert route.provider == "fleet-v" and cloud.calls == 0


def test_the_local_oracle_pick_must_stay_on_this_network_by_address():
    public_custom = _Ad("custom", endpoint="https://ollama.com/v1")
    lan = _Ad("fleet-a", endpoint=LAN)
    r = _router(
        {"custom": public_custom, "fleet-a": lan}, "fleet-a",
        local_oracle=lambda task_class: ("custom", "big"),
    )

    async def go():
        with egress.local_only_scope(True):
            return await _complete(r)

    assert asyncio.run(go()).provider == "fleet-a"
    assert public_custom.calls == 0
    # Anti-vacuity: outside the scope the oracle's pick serves.
    assert asyncio.run(_complete(r)).provider == "custom"


def test_auto_under_the_scope_picks_only_on_this_network():
    cloud = _Ad("claude-cli")
    lan = _Ad("fleet-a", endpoint=LAN)

    async def auto_route(system, messages, tools, task_class):
        return {"provider": "claude-cli", "model": "", "tier": "heavy", "classifier": ""}

    r = _router({"claude-cli": cloud, "fleet-a": lan}, "auto", auto_route=auto_route)

    async def go():
        with egress.local_only_scope(True):
            return await _complete(r)

    assert asyncio.run(go()).provider == "fleet-a"
    assert cloud.calls == 0
    # Anti-vacuity: outside the scope Auto takes the classifier's cloud pick.
    assert asyncio.run(_complete(r)).provider == "claude-cli"


# --------------------------------------------------------------------------- #
# 3. Paths that bypass the router.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _cloud_default(app, adapter=None):
    """Make ``claude-cli`` the (available) default, served by a counting fake."""
    p = app.state.platform
    cloud = adapter or _Ad("claude-cli")
    p.providers.register("claude-cli", lambda m=None: cloud)
    real_available = p.providers.available
    p.providers.available = lambda n: n == "claude-cli" or (n != "claude-cli" and real_available(n))
    p.config.default_provider = "claude-cli"
    p.config.default_model = ""
    return cloud


def test_one_shot_utilities_and_compaction_never_reach_a_cloud_adapter(app):
    d = app.state.d
    cloud = _cloud_default(app)
    msgs = [LLMMessage(role="user", content="x")]

    async def go():
        with egress.local_only_scope(True):
            with pytest.raises(egress.LocalOnlyRefused):
                await d._one_shot_complete("claude-cli", cloud, system="s", messages=msgs)
            with pytest.raises(egress.LocalOnlyRefused):
                d._compaction_complete("claude-cli", "")
            assert d._failover_adapter("mock") == (None, None)

    asyncio.run(go())
    assert cloud.calls == 0
    # Anti-vacuity: outside the scope the same calls reach the cloud fake.
    resp, used, _m = asyncio.run(d._one_shot_complete("claude-cli", cloud, system="s", messages=msgs))
    assert used == "claude-cli" and cloud.calls == 1
    assert d._failover_adapter("mock")[1] == "claude-cli"


def test_remember_keeps_the_excerpt_instead_of_calling_a_cloud_model(app):
    from iron_jarvis.memory import commit

    d = app.state.d
    cloud = _cloud_default(app)

    async def go(on):
        with egress.local_only_scope(on):
            return await commit.distill_or_excerpt(
                d, transcript="we talked about the budget", mode="distill",
                system="distill", subject="conversation",
            )

    out = asyncio.run(go(True))
    assert out.distilled is False
    assert out.note == (
        "This chat is local only and the model is not on this PC, so a"
        " verbatim excerpt was stored, not a distillation."
    )
    assert cloud.calls == 0
    assert asyncio.run(go(False)).distilled is True and cloud.calls == 1


# --------------------------------------------------------------------------- #
# 4. Turn entry points: the saved setting, the 409 and the error frame.
# --------------------------------------------------------------------------- #
def _save_thread(client, setup=None, messages=None, title=""):
    r = client.put(
        "/chat/threads/new",
        json={
            **({"title": title} if title else {}),
            "messages": messages or [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            "setup": setup or {},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_local_only_round_trips_through_a_saved_thread(app):
    from iron_jarvis.daemon.routes.chat import _clean_setup

    client = TestClient(app)
    tid = _save_thread(client, {"local_only": True, "provider": "ollama"})
    got = client.get(f"/chat/threads/{tid}").json()
    assert got["setup"]["local_only"] is True, got["setup"]
    # Stored ONLY as a real true; absent = off.
    assert _clean_setup({"local_only": False}) == ""
    assert _clean_setup({"local_only": "yes"}) == ""
    assert json.loads(_clean_setup({"local_only": True})) == {"local_only": True}


def _sse(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        ev, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if ev:
            out.append((ev, json.loads(data) if data else {}))
    return out


def test_the_saved_threads_local_only_wins_over_a_body_that_says_false(app):
    cloud = _cloud_default(app)
    client = TestClient(app)
    tid = _save_thread(client, {"local_only": True})
    body = {"messages": [{"role": "user", "content": "what is 2+2"}], "thread_id": tid, "local_only": False}
    r = client.post("/chat", json=body)
    assert r.status_code == 409, r.text
    assert r.json() == {
        "detail": egress.LocalOnlyRefused("claude-cli").args[0],
        "code": "local_only", "provider": "claude-cli", "model": "m",
    }
    assert cloud.calls == 0
    # Anti-vacuity: a thread that is not local only reaches the model.
    plain = _save_thread(client, {})
    r = client.post("/chat", json={**body, "thread_id": plain})
    assert r.status_code == 200, r.text
    assert cloud.calls == 1


def test_the_stream_error_frame_carries_code_provider_and_model(app):
    cloud = _cloud_default(app)
    client = TestClient(app)
    r = client.post(
        "/chat/stream",
        json={"messages": [{"role": "user", "content": "hi there"}], "local_only": True},
    )
    assert r.status_code == 200, r.text
    errors = [d for ev, d in _sse(r.text) if ev == "error"]
    assert errors and errors[-1]["code"] == "local_only", r.text
    assert errors[-1]["provider"] == "claude-cli" and errors[-1]["model"] == "m"
    assert "nothing was sent to Claude" in errors[-1]["detail"]
    assert cloud.calls == 0


def test_the_route_object_says_local_only_and_sent_with_your_ok(app):
    cloud = _cloud_default(app)
    client = TestClient(app)
    base = {"messages": [{"role": "user", "content": "hi there"}], "local_only": True}
    r = client.post("/chat", json={**base, "provider": "claude-cli", "allow_cloud_once": "claude-cli"})
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route["local_only"] is True and route["allowed_once"] is True
    assert cloud.calls == 1
    # An OK for a DIFFERENT provider than the turn's choice is ignored.
    r = client.post("/chat", json={**base, "provider": "claude-cli", "allow_cloud_once": "codex-cli"})
    assert r.status_code == 409 and cloud.calls == 1
    # The stream lane carries the same keys.
    r = client.post("/chat/stream", json={**base, "provider": "claude-cli", "allow_cloud_once": "claude-cli"})
    done = [d for ev, d in _sse(r.text) if ev == "done"]
    assert done and done[-1]["route"]["allowed_once"] is True, r.text


def test_the_allow_once_must_name_the_turns_chosen_provider():
    from iron_jarvis.daemon.chat_turn import local_only_scope_for

    platform = SimpleNamespace(engine=None, config=SimpleNamespace(default_provider="claude-cli"))

    def scope(**kw):
        body = SimpleNamespace(local_only=True, thread_id="", provider="", project_id="", **kw)
        return asyncio.run(local_only_scope_for(platform, body))

    assert scope(allow_cloud_once="claude-cli").allowed_once == "claude-cli"
    assert scope(allow_cloud_once="codex-cli").allowed_once == ""
    off = asyncio.run(local_only_scope_for(platform, SimpleNamespace(
        local_only=False, thread_id="", allow_cloud_once="claude-cli")))
    assert off.on is False and off.allowed_once == ""


# --------------------------------------------------------------------------- #
# 5. Routes tied to a chat.
# --------------------------------------------------------------------------- #
class _FollowRouter:
    def __init__(self):
        self.calls = []

    async def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            provider=kw.get("provider") or "ollama", model="m", reason="explicit",
            response=LLMResponse(text='["What else?"]', usage={}),
        )


def test_followups_are_empty_for_a_cloud_reply_in_a_local_only_chat(app):
    router = _FollowRouter()
    app.state.platform.router = router
    client = TestClient(app)
    assert client.put("/settings/values", json={"values": {"chat_followups": True}}).status_code == 200
    convo = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    r = client.post("/chat/followups", json={"messages": convo, "provider": "claude-cli", "local_only": True})
    assert r.status_code == 200 and r.json()["suggestions"] == []
    assert router.calls == []
    # A reply from a model on this network is answered, pinned.
    app.state.platform.providers.configure_local(ollama_base_url="http://127.0.0.1:11434")
    r = client.post("/chat/followups", json={"messages": convo, "provider": "ollama", "local_only": True})
    assert r.json()["suggestions"] == ["What else?"]
    assert router.calls and router.calls[-1].get("pin") is True
    # Anti-vacuity: not local only, the cloud reply gets its suggestions.
    r = client.post("/chat/followups", json={"messages": convo, "provider": "claude-cli"})
    assert r.json()["suggestions"] == ["What else?"]


def test_a_cloud_seat_sits_out_of_the_panel_with_one_plain_line(app):
    cloud = _cloud_default(app)
    client = TestClient(app)
    body = {"message": "@builder how should we lay this out?", "chat_thread_id": "c-lo", "hands": False}
    r = client.post("/chat/panel", json={**body, "local_only": True})
    assert r.status_code == 200, r.text
    entry = [e for e in r.json()["entries"] if e.get("who") == "builtin:builder"][0]
    assert entry["content"] == (
        "builder sat this one out: this chat is local only and builder runs on Claude."
    )
    assert entry["sat_out"] == "local_only"
    assert cloud.calls == 0
    # Anti-vacuity: the same seat speaks when the chat is not local only.
    r = client.post("/chat/panel", json={**body, "chat_thread_id": "c-open"})
    assert r.status_code == 200, r.text
    assert cloud.calls == 1


def test_a_remote_seat_speaks_only_from_a_machine_on_this_network():
    from iron_jarvis.agents.threads import AgentThreads

    d = SimpleNamespace(platform=SimpleNamespace(providers=None, config=SimpleNamespace()))
    seat = {"name": "hermes", "source": "remote", "key": "remote:hermes"}
    lan = SimpleNamespace(base_url="http://192.168.1.40:8642")
    web = SimpleNamespace(base_url="https://agents.example.com")
    with egress.local_only_scope(True):
        assert AgentThreads._local_only_sat_out(seat, seat, d, lan) == ""
        line = AgentThreads._local_only_sat_out(seat, seat, d, web)
    assert line == "hermes sat this one out: this chat is local only and hermes runs on agents.example.com."
    assert AgentThreads._local_only_sat_out(seat, seat, d, web) == ""  # scope off


def test_a_thread_ref_to_a_local_only_chat_keeps_its_text_home_from_a_cloud_turn(app):
    from iron_jarvis.daemon import chat_refs

    client = TestClient(app)
    tid = _save_thread(
        client, {"local_only": True}, title="Budget talk",
        messages=[{"role": "user", "content": "PRIVATE-FIGURES-42"}, {"role": "assistant", "content": "noted"}],
    )
    d = SimpleNamespace(platform=app.state.platform)
    body = SimpleNamespace(thread_refs=[tid], thread_id="", provider="")

    block, receipt = asyncio.run(chat_refs.read_thread_refs(d, body, provider="claude-cli"))
    assert "PRIVATE-FIGURES-42" not in block
    assert "is a local-only chat, so its text stays on this PC." in block
    assert receipt[0]["note"] == chat_refs.NOTE_LOCAL_ONLY
    # Anti-vacuity: a turn that stays on this network reads it.
    block, _ = asyncio.run(chat_refs.read_thread_refs(d, body, provider="mock"))
    assert "PRIVATE-FIGURES-42" in block


def test_compact_refuses_a_cloud_model_with_the_409_shape(app):
    cloud = _cloud_default(app)
    client = TestClient(app)
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 20} for i in range(30)]
    r = client.post("/chat/compact", json={"messages": msgs, "local_only": True})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "local_only" and r.json()["provider"] == "claude-cli"
    assert cloud.calls == 0


# --------------------------------------------------------------------------- #
# 6. Non-model egress in a local-only turn.
# --------------------------------------------------------------------------- #
class _ToolRouter:
    """Records the tool names each call offers; answers with plain text."""

    default_provider = "ollama"

    def __init__(self):
        self.tools = []

    async def complete(self, **kw):
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        return RouteResult(LLMResponse(text="done", tool_calls=[], usage={}), "ollama", "m")


async def _post_lane(platform, body):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    await run_chat_turn(platform, {}, body)


async def _stream_lane(platform, body):
    from iron_jarvis.daemon.routes.chat import stream_chat_turn

    gen = await stream_chat_turn(platform, {}, body)
    async for _chunk in gen:
        pass


@pytest.mark.parametrize("turn", [_post_lane, _stream_lane])
def test_web_tools_are_not_armed_in_a_local_only_turn(app, turn):
    from iron_jarvis.daemon.schemas import ChatBody

    router = _ToolRouter()
    app.state.platform.router = router

    def offered(local_only):
        body = ChatBody(
            messages=[{"role": "user", "content": "look this up on the web for me"}],
            tools=["web_search", "web_fetch", "read_file"], local_only=local_only,
        )
        asyncio.run(turn(app.state.platform, body))
        return router.tools[-1]

    names = offered(True)
    assert "web_search" not in names and "web_fetch" not in names
    assert "read_file" in names
    # Anti-vacuity: the same turn, not local only, arms them.
    names = offered(False)
    assert "web_search" in names and "web_fetch" in names


class _RemoteBase:
    """A memory base that is NOT on this PC (an LTMConnector by duck type)."""

    name = "notion-x"

    def __init__(self):
        self.calls = 0

    def search(self, query, k=5):
        self.calls += 1
        return [{"title": "remote note", "snippet": "budget plan details", "ref": "n1", "source": self.name}]

    def append(self, title, content):  # pragma: no cover - never written
        return "n"


def test_a_remote_memory_base_is_never_queried_by_a_local_only_turn(app):
    from iron_jarvis.daemon.chat_turn import _gather_grounding, _resolve_connectors

    platform = app.state.platform
    remote = _RemoteBase()
    platform.ltm.register(remote)
    d = SimpleNamespace(platform=platform)
    body = SimpleNamespace(connectors=["notion-x"])

    async def ground(on):
        with egress.local_only_scope(on):
            conn = _resolve_connectors(d, body)
            await _gather_grounding(d, body, pid=None, resolved_proj=None, recall_query="budget plan")
            return conn

    tools, memory = asyncio.run(ground(True))
    assert memory == [] and remote.calls == 0
    # Anti-vacuity: outside the scope the base is toggled and searched.
    tools, memory = asyncio.run(ground(False))
    assert memory == ["notion-x"] and remote.calls > 0


# --------------------------------------------------------------------------- #
# 7. Sessions: refused at creation, inherited by children, scoped at run.
# --------------------------------------------------------------------------- #
def test_a_session_escalation_to_a_cloud_provider_is_refused_with_409(app):
    _cloud_default(app)
    client = TestClient(app)
    r = client.post("/sessions", json={"task": "do it", "provider": "claude-cli", "local_only": True, "wait": False})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "local_only" and r.json()["provider"] == "claude-cli"


def test_a_local_only_session_is_stored_inherited_by_children_and_scoped_at_run(app):
    from iron_jarvis.agents.orchestrator import inherited_local_only, session_local_only
    from iron_jarvis.core.models import AgentType

    platform = app.state.platform
    orch = platform.orchestrator
    _cloud_default(app)

    async def go():
        parent = await orch.create_session("t", provider="mock", options={"local_only": True})
        # A child created INSIDE a local-only run inherits it by context…
        with egress.local_only_scope(True):
            child = await orch.create_session("child", AgentType.BUILDER, provider="mock")
            # …and a child on a cloud model is refused at creation.
            with pytest.raises(egress.LocalOnlyRefused):
                await orch.create_session("child", AgentType.BUILDER, provider="claude-cli")
        plain = await orch.create_session("t", provider="mock")
        return parent, child, plain

    parent, child, plain = asyncio.run(go())
    assert session_local_only(parent) and session_local_only(child)
    assert inherited_local_only(parent) is True
    assert not session_local_only(plain) and inherited_local_only(plain) is False

    seen = []

    async def fake_body(session, agent_def, parent_id):
        seen.append(egress.current_scope().on)
        return None

    orch.runtime._run_body = fake_body
    asyncio.run(orch.runtime.run(parent, None))
    asyncio.run(orch.runtime.run(plain, None))
    assert seen == [True, False]
