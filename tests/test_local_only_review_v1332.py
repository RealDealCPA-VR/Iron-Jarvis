"""Local only, the review round (v1.332.0).

Each test pins a way a local-only chat's words could still leave the user's
network that the first cut left open, or a refusal branch it never drove:

* a bare NUMBER host (``134744072`` is 8.8.8.8 to the resolver) read as a
  single-label LAN name;
* the embedder: grounding embeds the question with the configured Ollama
  embedder, wherever its address points;
* a scan the vision model off this network would have read: the note the
  answering model relays must say what happened, not "connect a vision model";
* the one-message OK for the provider the capability refusal NAMED, and for
  "Auto" itself, must actually let that message through;
* recall and ``history_search`` handing a local-only chat's text to work that
  leaves this network;
* the chat's "summary sheet" batch (``POST /documents/batch``);
* the stream wrapper's refusal frame (``_honest_failures``).
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
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.router import ModelRouter
from iron_jarvis.tools.base import ToolResult


class _Ad(LLMAdapter):
    def __init__(self, provider, *, endpoint="", vision=True, tools=True, model="m"):
        self.provider, self.model = provider, model
        self._endpoint = endpoint
        self.vision, self.tools_ok = vision, tools
        self.calls = 0

    def capabilities(self):
        return {"tool_use": self.tools_ok, "vision": self.vision}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "text", "text": "tok"}
        yield {"type": "final", "response": LLMResponse(text="tok", tool_calls=[], usage={})}


class _Manager:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, p):
        return p in self.adapters

    def has_available_api_provider(self):
        return True

    def runtime_provider_names(self):
        return sorted(p for p in self.adapters if p.startswith("fleet-"))

    def get(self, p, m=None):
        return self.adapters[p]


def _router(adapters, default, **kw):
    return ModelRouter(
        _Manager(adapters), default_provider=default, event_bus=EventBus(),
        local_policy=lambda: "refuse", **kw,
    )


def _image_msgs():
    m = LLMMessage(role="user", content="what is in this picture")
    m.images = [{"data_b64": "aGk=", "media_type": "image/png"}]
    return [m]


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url", ["http://134744072:8000", "http://0x08080808/v1", "http://010.0.0.1", "http://0x"],
)
def test_a_bare_number_host_is_judged_as_the_address_it_spells(url):
    assert egress.private_host(url) is False, url


def test_a_bare_number_that_spells_a_private_address_stays_on_this_network():
    assert egress.private_host("http://2130706433:11434") is True  # 127.0.0.1
    assert egress.private_host("http://0xc0a80114") is True  # 192.168.1.20


# --------------------------------------------------------------------------- #
# The embedder
# --------------------------------------------------------------------------- #
class _Http:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None):
        self.posts.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {"embedding": [0.1, 0.2]})


def test_the_embedder_never_sends_a_local_only_turns_text_off_this_network():
    from iron_jarvis.memory.embeddings import EmbedderError, OllamaEmbedder

    http = _Http()
    public = OllamaEmbedder("https://embed.example.com/v1", http=http)
    with egress.local_only_scope(True):
        with pytest.raises(EmbedderError):
            public.embed("the client's figures")
    assert http.posts == []
    # A server on this network still embeds under the scope…
    lan = OllamaEmbedder("http://192.168.1.20:11434", http=http)
    with egress.local_only_scope(True):
        assert lan.embed("x") == [0.1, 0.2]
    # …and outside the scope the public one is called as before.
    assert public.embed("x") == [0.1, 0.2]
    assert len(http.posts) == 2


# --------------------------------------------------------------------------- #
# A scan the off-network vision model would have read
# --------------------------------------------------------------------------- #
def test_a_refused_scan_says_it_was_not_read_and_why():
    from iron_jarvis.documents.ocr import _transcribe, local_only_ocr_note

    class _Refusing:
        manager = None

        async def complete(self, **kw):
            raise egress.LocalOnlyRefused("google", "gemini-x")

    pages, note = asyncio.run(_transcribe([b"img"], _Refusing(), route_kwargs={}, label_pages=False))
    assert pages == []
    assert note == local_only_ocr_note("Gemini")
    assert "NOT read" in note and "nothing was sent" in note
    assert "connect a vision model" not in note


# --------------------------------------------------------------------------- #
# The one-message OK reaches what it was given for
# --------------------------------------------------------------------------- #
def test_allowing_the_vision_provider_the_refusal_named_lets_that_message_swap_to_it():
    local = _Ad("ollama", endpoint="http://127.0.0.1:11434/v1", vision=False)
    cloud = _Ad("claude-cli")
    other = _Ad("codex-cli")
    r = _router({"ollama": local, "claude-cli": cloud, "codex-cli": other}, "ollama")

    async def go(once):
        with egress.local_only_scope(True, once):
            return await r.complete(system="s", messages=_image_msgs(), tools=[])

    route = asyncio.run(go("claude-cli"))
    assert route.provider == "claude-cli" and cloud.calls == 1
    # An OK for some OTHER provider never widens the swap.
    with pytest.raises(egress.LocalOnlyRefused):
        asyncio.run(go("codex-cli"))
    assert other.calls == 0 and cloud.calls == 1


@pytest.mark.parametrize("lane", ["complete", "stream"])
def test_an_auto_turn_the_user_allowed_as_auto_is_answered_by_autos_pick(lane):
    cloud = _Ad("claude-cli")

    async def auto_route(system, messages, tools, task_class):
        return {"provider": "claude-cli", "model": "", "tier": "heavy", "classifier": ""}

    r = _router({"claude-cli": cloud}, "auto", auto_route=auto_route)

    async def go(once):
        with egress.local_only_scope(True, once):
            if lane == "complete":
                return (await r.complete(
                    system="s", messages=[LLMMessage(role="user", content="hi")], tools=[],
                )).provider
            frames = [f async for f in r.stream(
                system="s", messages=[LLMMessage(role="user", content="hi")], tools=[])]
            return frames[-1].get("provider")

    assert asyncio.run(go("auto")) == "claude-cli"
    assert cloud.calls == 1
    # Without the OK, Auto under the scope refuses the cloud by name.
    with pytest.raises(egress.LocalOnlyRefused):
        asyncio.run(go(""))
    assert cloud.calls == 1


# --------------------------------------------------------------------------- #
# Recall and history search
# --------------------------------------------------------------------------- #
def _save(client, setup, text):
    r = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": text}], "setup": setup},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _hit(tid, text):
    from iron_jarvis.search.models import SearchHit

    return SearchHit(kind="chat", ref=tid, thread_id=tid, title="t", snippet=text, score=1.0)


class _Index:
    mode = "fts"

    def __init__(self, hits, engine=None):
        self.hits, self.engine = hits, engine

    def search(self, query, **kw):
        return list(self.hits)


def test_recall_never_hands_a_local_only_chat_to_work_that_leaves_this_network(app):
    client = TestClient(app)
    platform = app.state.platform
    lo = _save(client, {"local_only": True}, "PRIVATE-FIGURES-42")
    plain = _save(client, {}, "public talk")
    fabric = platform.fabric
    fabric._search = _Index([_hit(lo, "PRIVATE-FIGURES-42"), _hit(plain, "public talk")])

    def refs(**scope):
        if scope:
            with egress.local_only_scope(**scope):
                return [h.ref for h in fabric._chats("figures", 5)]
        return [h.ref for h in fabric._chats("figures", 5)]

    assert refs() == [plain]  # a turn that is not local only
    assert refs(on=True, allowed_once="claude-cli") == [plain]  # sent with the OK
    assert refs(on=True) == [lo, plain]  # a local-only turn recalls it


def test_history_search_leaves_a_local_only_chat_out_of_work_that_leaves_this_network(app):
    from iron_jarvis.search.tools import HistorySearchTool

    client = TestClient(app)
    platform = app.state.platform
    lo = _save(client, {"local_only": True}, "PRIVATE-FIGURES-42")
    plain = _save(client, {}, "public talk")
    tool = HistorySearchTool(_Index(
        [_hit(lo, "PRIVATE-FIGURES-42"), _hit(plain, "public talk")], engine=platform.engine,
    ))
    ctx = SimpleNamespace()

    async def run(on):
        with egress.local_only_scope(on):
            return await tool.execute({"query": "figures"}, ctx)

    out = asyncio.run(run(False))
    assert "PRIVATE-FIGURES-42" not in out.output and out.data["count"] == 1
    out = asyncio.run(run(True))
    assert "PRIVATE-FIGURES-42" in out.output and out.data["count"] == 2


# --------------------------------------------------------------------------- #
# The summary-sheet batch
# --------------------------------------------------------------------------- #
def test_a_local_only_chats_batch_on_a_cloud_default_is_refused_before_any_document(app, tmp_path):
    platform = app.state.platform
    platform.config.default_provider = "claude-cli"
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("client figures", encoding="utf-8")
    seen = []

    async def fake_invoke(name, args, ctx, perms, grants=None, **kw):
        seen.append(egress.current_scope().on)
        return ToolResult(ok=True, output="done", data={})

    platform.registry.invoke = fake_invoke
    client = TestClient(app)
    body = {"folder": str(folder), "output": "xlsx"}
    r = client.post("/documents/batch", json={**body, "local_only": True})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "local_only" and r.json()["provider"] == "claude-cli"
    assert seen == []
    # The SAVED chat's setting counts too (a body that leaves the flag out).
    tid = _save(client, {"local_only": True}, "hi")
    r = client.post("/documents/batch", json={**body, "thread_id": tid})
    assert r.status_code == 409 and seen == []
    # On a model on this network it runs, under the scope.
    platform.config.default_provider = "mock"
    r = client.post("/documents/batch", json={**body, "local_only": True})
    assert r.status_code == 200, r.text
    assert seen == [True]
    # Anti-vacuity: not local only, the cloud default runs (outside the scope).
    platform.config.default_provider = "claude-cli"
    r = client.post("/documents/batch", json=body)
    assert r.status_code == 200 and seen == [True, False]


# --------------------------------------------------------------------------- #
# The stream wrapper's refusal frame
# --------------------------------------------------------------------------- #
def test_a_refusal_that_escapes_the_stream_is_one_error_frame_with_its_code():
    from iron_jarvis.daemon.routes.chat import _honest_failures

    async def frames():
        raise egress.LocalOnlyRefused("claude-cli", "opus")
        yield ""  # pragma: no cover - makes this an async generator

    async def go():
        return [c async for c in _honest_failures(frames())]

    out = asyncio.run(go())
    assert len(out) == 1 and out[0].startswith("event: error\n")
    data = json.loads(out[0].split("data: ", 1)[1])
    assert data == egress.LocalOnlyRefused("claude-cli", "opus").payload()
