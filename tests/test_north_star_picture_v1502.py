"""North-star 2.13 (D1, v1.502.0): a picture never moves the turn off this network.

The owner's decision D1: a picture on a model that cannot see, when that model
is on this network or was picked by name, is never moved to a model that
leaves the network. The offline reading of the picture goes first (the chat's
Windows OCR); else the turn is refused by name with the same "send once"
offer Local only gives. A default-route cloud model that cannot see may still
move to another cloud model the user connected (the money rule unchanged).

Proven through the layer that refuses: the router's capability seam, in BOTH
lanes, and the chat's own attachment preparer for the OCR path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.router import ModelRouter


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


LOCAL = "http://127.0.0.1:11434/v1"
LAN = "http://spark2:8000/v1"


def _router(adapters, default):
    return ModelRouter(
        _Manager(adapters), default_provider=default, event_bus=EventBus(),
        local_policy=lambda: "refuse",
    )


def _picture():
    m = LLMMessage(role="user", content="what is on this form")
    m.images = [{"data_b64": "aGk=", "media_type": "image/png"}]
    return [m]


async def _complete(router, **kw):
    return (await router.complete(system="s", messages=_picture(), tools=[], **kw)).provider


async def _stream(router, **kw):
    return [f async for f in router.stream(system="s", messages=_picture(), tools=[], **kw)]


LANES = [_complete, _stream]


@pytest.mark.parametrize("lane", LANES)
def test_a_picture_on_a_local_model_that_cannot_see_is_refused_by_name_not_sent_to_the_cloud(lane):
    """The finding: with Local only OFF, a local default that cannot see moved
    the picture (a client's scanned form) to the connected cloud model before
    any attempt. Now nothing is sent and the refusal names the cloud model it
    did not use, with the code the page's "Send once" keys on."""
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud}, "ollama")

    with pytest.raises(egress.PictureKeptHere) as caught:
        asyncio.run(lane(r))
    assert cloud.calls == 0 and local.calls == 0
    exc = caught.value
    assert exc.provider == "claude-cli" and exc.primary == "ollama"
    assert exc.payload()["code"] == "local_only"
    assert "Claude" in str(exc) and "Ollama" in str(exc)
    assert "local only" not in str(exc).lower(), "this chat is NOT local only: its own words"
    assert egress.refusal_of(exc) is exc
    from iron_jarvis.providers.router import is_no_failover, is_transient_error

    assert is_no_failover(exc) and not is_transient_error(exc)


@pytest.mark.parametrize("lane", LANES)
def test_a_picture_on_a_model_picked_by_name_never_moves_off_the_network(lane):
    """An explicit pick that cannot see (a cloud model here) is the user's
    choice: the picture is not moved to ANOTHER cloud model either."""
    blind_pick = _Ad("codex-cli", vision=False)
    seeing = _Ad("claude-cli")
    r = _router({"codex-cli": blind_pick, "claude-cli": seeing}, "claude-cli")

    with pytest.raises(egress.PictureKeptHere) as caught:
        asyncio.run(lane(r, provider="codex-cli"))
    assert caught.value.provider == "claude-cli" and caught.value.primary == "codex-cli"
    assert seeing.calls == 0 and blind_pick.calls == 0


@pytest.mark.parametrize("lane", LANES)
def test_a_default_route_cloud_model_that_cannot_see_still_moves_to_a_cloud_one(lane):
    """Unchanged: a cloud primary on the default route (nobody picked it, it
    is not on this network) still moves to a connected model that can see."""
    blind_default = _Ad("codex-cli", vision=False)
    seeing = _Ad("claude-cli")
    r = _router({"codex-cli": blind_default, "claude-cli": seeing}, "codex-cli")

    asyncio.run(lane(r))
    assert seeing.calls == 1 and blind_default.calls == 0


@pytest.mark.parametrize("lane", LANES)
def test_a_local_model_that_cannot_see_moves_to_one_on_this_network_that_can(lane):
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    lan_vision = _Ad("fleet-v", endpoint=LAN, vision=True)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud, "fleet-v": lan_vision}, "ollama")

    asyncio.run(lane(r))
    assert lan_vision.calls == 1 and cloud.calls == 0 and local.calls == 0


@pytest.mark.parametrize("lane", LANES)
def test_with_no_model_anywhere_that_can_see_the_local_model_keeps_the_turn(lane):
    """Nothing to refuse in favour of: the chosen model answers as it can
    (the chat's note says the picture was not seen), exactly as before."""
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("codex-cli", vision=False)
    r = _router({"ollama": local, "codex-cli": cloud}, "ollama")

    asyncio.run(lane(r))
    assert local.calls == 1 and cloud.calls == 0


def test_the_one_message_ok_is_a_resend_on_the_cloud_model_and_it_goes():
    """The page's "Send to Claude once" re-sends the message on that model
    (``choice: claude-cli::…``): a pick that CAN see keeps the picture."""
    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud}, "ollama")
    assert asyncio.run(_complete(r, provider="claude-cli")) == "claude-cli"
    assert cloud.calls == 1


def test_a_scan_on_a_local_default_that_cannot_see_is_not_sent_and_says_so_in_its_own_words():
    """``documents/ocr`` reads a scanned page through the router on the
    default route. A local default that cannot see used to move every page of
    a client's scan to the cloud model. Now nothing is sent and the note says
    why, in words that do NOT claim the chat is Local only."""
    from iron_jarvis.documents.ocr import _transcribe, picture_kept_ocr_note

    local = _Ad("ollama", endpoint=LOCAL, vision=False)
    cloud = _Ad("claude-cli")
    r = _router({"ollama": local, "claude-cli": cloud}, "ollama")
    pages, note = asyncio.run(_transcribe([b"img"], r, route_kwargs={}, label_pages=False))
    assert pages == [] and cloud.calls == 0 and local.calls == 0
    assert note == picture_kept_ocr_note("Claude", "Ollama")
    assert "NOT read" in note and "nothing was sent" in note
    assert "local only" not in note.lower()


# --------------------------------------------------------------------------- #
# The chat: the offline reading takes the turn.
# --------------------------------------------------------------------------- #
class _Blind:
    provider = "ollama"
    model = "llama"
    _endpoint = LOCAL

    def capabilities(self):
        return {"tool_use": True, "vision": False}


class _Seeing:
    provider = "claude-cli"
    model = "sonnet"

    def capabilities(self):
        return {"tool_use": True, "vision": True}


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path)))


def _fleet(platform, monkeypatch, adapters):
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set(adapters))
    monkeypatch.setattr(platform.providers, "get", lambda n, m=None: adapters[n])


def _default(platform, monkeypatch, provider):
    monkeypatch.setattr(platform.config, "default_provider", provider)
    monkeypatch.setattr(platform.router, "_default_provider", provider)


def _png(tmp_path):
    from PIL import Image

    png = tmp_path / "w2-scan.png"
    Image.new("RGB", (40, 30), (250, 250, 250)).save(png)
    return png


def _body(paths):
    return SimpleNamespace(
        attachments=[str(p) for p in paths],
        messages=[SimpleNamespace(role="user", content="what is on this form")],
    )


async def test_the_offline_reading_takes_the_turn_and_the_picture_stays_home(
    client, tmp_path, monkeypatch
):
    """A local default that cannot see, a cloud model that could: the picture
    is read on this PC and the turn carries the TEXT, not the picture (so the
    router has nothing to move)."""
    from iron_jarvis.daemon.chat_turn import _prepare_attachments
    from iron_jarvis.documents import local_ocr

    platform = client.app.state.platform
    _default(platform, monkeypatch, "ollama")
    _fleet(platform, monkeypatch, {"ollama": _Blind(), "claude-cli": _Seeing()})
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    monkeypatch.setattr(local_ocr, "available", lambda: True)
    monkeypatch.setattr(
        local_ocr, "local_ocr_bytes",
        lambda data, *, deadline_s=local_ocr.PAGE_DEADLINE_S: local_ocr.OcrResult(
            text="Wages 84,120.55", confident=True, number_heavy=True, seconds=0.01
        ),
    )
    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([_png(tmp_path)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert "84,120.55" in block and local_ocr.LOCAL_NOTE in block
    assert images == [], "the picture read on this PC must not ride on to be moved"


async def test_an_unreadable_picture_stays_on_the_message_so_the_router_refuses_by_name(
    client, tmp_path, monkeypatch
):
    from iron_jarvis.daemon.chat_turn import _prepare_attachments

    platform = client.app.state.platform
    _default(platform, monkeypatch, "ollama")
    _fleet(platform, monkeypatch, {"ollama": _Blind(), "claude-cli": _Seeing()})
    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([_png(tmp_path)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert images, "nothing read it here: the router's refusal is what the user sees"
    assert "NOT analyzed" in block


def test_the_vision_note_does_not_count_a_cloud_model_for_a_local_pick(client, monkeypatch):
    from iron_jarvis.daemon.chat_turn import _vision_unavailable_reason

    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"ollama": _Blind(), "claude-cli": _Seeing()})
    d = SimpleNamespace(platform=platform)
    reason = _vision_unavailable_reason(d, "ollama", "")
    assert "NOT seen" in reason and "Claude" in reason
    # A default-route cloud pick still counts the cloud model that can see.
    _default(platform, monkeypatch, "claude-cli")
    assert _vision_unavailable_reason(d, "", "") == ""
