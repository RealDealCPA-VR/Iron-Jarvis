"""v1.424.0 (review of decision D1): the holes the independent review found
in "failover never spends money the user did not choose".

1. THE VISION NOTE MIRRORS THE MONEY RULE. ``chat_turn.
   _vision_unavailable_reason`` says "this image was NOT seen" only when
   neither the pick nor ANY connected provider can see it — it "mirrors
   ``_first_capable``'s filter". After D1 the router's vision reroute never
   moves a picture to a pay-per-use key for a pick that is not one, so a
   local (or plan) pick whose only seeing provider is an OpenAI key keeps the
   picture and the model never sees it. The note still counted the key as
   "a provider that can see", said nothing, and the Windows OCR fallback that
   runs when the note fires (C-05) never ran: the image went unseen in
   silence. The note now leaves out a pay-per-use provider exactly when the
   router does and says, in the one D1 sentence, that it was not sent there.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _vision_unavailable_reason

LOCAL = "http://127.0.0.1:11434/v1/chat/completions"


class _Ad:
    def __init__(self, provider, *, vision, endpoint="", model="m"):
        self.provider, self.model, self._endpoint = provider, model, endpoint
        self._vision = vision

    def capabilities(self):
        return {"tool_use": True, "vision": self._vision}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    p = TestClient(create_app(str(tmp_path))).app.state.platform
    # No keyless inheritance here: an "openai" is a real key.
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    return p


def _fleet(platform, monkeypatch, adapters):
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set(adapters))
    monkeypatch.setattr(platform.providers, "get", lambda n, m=None: adapters[n])


def test_a_local_pick_whose_only_seeing_model_is_a_paid_key_is_told_the_image_was_not_seen(
    platform, monkeypatch,
):
    _fleet(platform, monkeypatch, {
        "ollama": _Ad("ollama", vision=False, endpoint=LOCAL),
        "openai": _Ad("openai", vision=True),
    })
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "")
    assert "NOT seen" in reason, reason
    assert "didn't switch to OpenAI, which bills per use" in reason, reason


def test_a_subscription_that_can_see_still_silences_the_note(platform, monkeypatch):
    """The control: the router reroutes a local pick's picture to Claude Code
    (a plan), so no note — a warning there would be the lie."""
    _fleet(platform, monkeypatch, {
        "ollama": _Ad("ollama", vision=False, endpoint=LOCAL),
        "openai": _Ad("openai", vision=True),
        "claude-cli": _Ad("claude-cli", vision=True),
    })
    assert _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "") == ""


def test_a_paid_pick_may_still_send_the_picture_to_a_paid_key(platform, monkeypatch):
    """The control: a pay-per-use pick may move to another key, so the key
    that can see silences the note, exactly as before D1."""
    _fleet(platform, monkeypatch, {
        "xai": _Ad("xai", vision=False),
        "openai": _Ad("openai", vision=True),
    })
    assert _vision_unavailable_reason(SimpleNamespace(platform=platform), "xai", "") == ""


def test_nothing_left_out_keeps_the_old_words(platform, monkeypatch):
    _fleet(platform, monkeypatch, {"ollama": _Ad("ollama", vision=False, endpoint=LOCAL)})
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "")
    assert reason.startswith(
        "the model answering this turn cannot accept images and no connected provider can (ollama)"
    ), reason
    assert "bills per use" not in reason


# --------------------------------------------------------------------------- #
# 2. a refusal never says "connect a model" in silence when one IS connected
# --------------------------------------------------------------------------- #
#
# D1 made ``_failover_adapter("mock")`` (the hop a background utility makes
# when the default is the demo model) leave every pay-per-use key out — the
# owner's rule. But the refusals at the end of that hop still said only
# "connect a model on the Connections page …" / "no real model connected":
# a user with an OpenAI key connected was told to connect a model, and went
# to a page that showed it connected. Each now carries the one D1 sentence
# naming the key it did not use (``d._paid_only_note``); byte-identical when
# nothing was left out.

SENTENCE_ANTHROPIC = (
    "Iron Jarvis didn't switch to Anthropic, which bills per use, because you "
    "didn't choose a pay-per-use model for this."
)


class _Key:
    provider, model, _endpoint = "anthropic", "claude-x", ""

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, **kw):  # pragma: no cover — never reached
        raise AssertionError("a pay-per-use key the user never chose was called")


@pytest.fixture
def demo_default_with_a_key(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    assert platform.config.default_provider == "mock"
    key = _Key()
    real_get = platform.providers.get
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    monkeypatch.setattr(
        platform.providers, "available", lambda name, *a, **k: name in ("anthropic", "mock")
    )
    monkeypatch.setattr(
        platform.providers, "get",
        lambda name="", model=None, *a, **k: key if name == "anthropic" else real_get(name, model),
    )
    return app


def test_the_workflow_builder_names_the_key_it_did_not_use(demo_default_with_a_key):
    r = TestClient(demo_default_with_a_key).post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"].endswith(SENTENCE_ANTHROPIC), r.json()


def test_the_lesson_distill_note_names_the_key_it_did_not_use(demo_default_with_a_key):
    r = TestClient(demo_default_with_a_key).post("/lessons/compact", json={"distill": True})
    if r.status_code == 404:  # pragma: no cover — the route moved
        pytest.skip("no /lessons/compact route")
    assert r.status_code == 200, r.text
    assert r.json().get("note", "").endswith(SENTENCE_ANTHROPIC), r.json()


def test_nothing_left_out_keeps_the_old_refusal(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    monkeypatch.setattr(platform.providers, "available", lambda name, *a, **k: name == "mock")
    r = TestClient(app).post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 400
    assert r.json()["detail"] == "connect a model on the Connections page to build workflows"


# --------------------------------------------------------------------------- #
# 3. every branch of an agent run's reason says it (two were unpinned)
# --------------------------------------------------------------------------- #
#
# ``failure_words.run_failure_words`` (a run's "The run failed: …") has three
# branches — classified words, a sentence already worded for a person, the
# generic title — and the D1 sentence was appended to all three, but only the
# first was pinned: dropping it from the other two left every test green.

from iron_jarvis.core.events import EventBus  # noqa: E402
from iron_jarvis.providers import billing  # noqa: E402
from iron_jarvis.providers import failure_words as FW  # noqa: E402
from iron_jarvis.providers.adapters.base import LLMAdapter, ProviderError  # noqa: E402
from iron_jarvis.providers.router import ModelRouter  # noqa: E402

SENTENCE_OPENAI = (
    "Iron Jarvis didn't switch to OpenAI, which bills per use, because you "
    "didn't choose a pay-per-use model for this."
)


class _Fails(LLMAdapter):
    def __init__(self, provider, exc=None):
        self.provider, self.model, self.exc, self.calls, self._endpoint = provider, "m", exc, 0, ""

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        from iron_jarvis.providers.adapters.base import LLMResponse

        return LLMResponse(text="ok", tool_calls=[], usage={})


class _M:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, p):
        return p in self.adapters

    def get(self, p, model=None):
        return self.adapters[p]

    def inherited_from(self, p):
        return None

    def has_available_api_provider(self):
        return True

    def has_available_real_endpoint(self):
        return False

    def runtime_provider_names(self):
        return []


async def test_the_generic_reason_of_a_run_says_it_was_not_moved():
    """A failure no rule words (no status, no known phrase): the generic
    title + fix (``_run_generic_words``)."""
    weird = ProviderError("claude-cli: something odd happened")
    assert FW.classify(weird, provider="claude-cli", surface=FW.SURFACE_RUN) is None
    openai = _Fails("openai")
    router = ModelRouter(_M({"claude-cli": _Fails("claude-cli", weird), "openai": openai}),
                         "openai", EventBus())
    from iron_jarvis.providers.adapters.base import LLMMessage

    with pytest.raises(ProviderError) as info:
        await router.complete(provider="claude-cli", system="", tools=[],
                              messages=[LLMMessage(role="user", content="hi")])
    assert openai.calls == 0 and billing.left_out(info.value) == ("openai",)
    reason, _tech = FW.run_failure_words(info.value, provider="claude-cli")
    assert reason.endswith(SENTENCE_OPENAI), reason


async def test_a_worded_reason_of_a_run_says_it_was_not_moved():
    """Auto picked Claude Code and its accounts are parked (a sentence worded
    for the user, ``no_failover``): Auto may substitute, but never with a key
    for a plan pick — the run's reason is that sentence plus the D1 one."""
    parked = ProviderError("claude-cli: Every Claude Code account is resting until 3 PM.")
    parked.no_failover = True
    openai = _Fails("openai")

    async def auto_route(system, messages, tools, task_class):
        return {"provider": "claude-cli", "model": None, "tier": "t"}

    router = ModelRouter(_M({"claude-cli": _Fails("claude-cli", parked), "openai": openai}),
                         "auto", EventBus(), auto_route=auto_route)
    from iron_jarvis.providers.adapters.base import LLMMessage

    with pytest.raises(Exception) as info:
        await router.complete(system="", tools=[], messages=[LLMMessage(role="user", content="hi")])
    assert openai.calls == 0
    reason, _tech = FW.run_failure_words(info.value, provider="claude-cli")
    assert reason.startswith("Every Claude Code account is resting until 3 PM."), reason
    assert reason.endswith(SENTENCE_OPENAI), reason


def test_a_fleet_node_on_this_network_is_free_and_one_outside_it_pays():
    """The brief's list of "not pay per use", checked for the one kind the
    builder's definition test did not drive: a fleet node, judged by its
    ADDRESS (``egress.stays_on_this_network`` reads the node's base_url)."""

    class _Node:
        def __init__(self, url):
            self.base_url = url

    class _FleetAd:
        def __init__(self, provider, url):
            self.provider, self.model, self.node = provider, "glm", _Node(url)

    adapters = {
        "fleet-dgx": _FleetAd("fleet-dgx", "http://192.168.1.40:4000/v1"),
        "fleet-cloud": _FleetAd("fleet-cloud", "https://api.example.com/v1"),
    }

    class _Mgr:
        def get(self, p, model=None):
            return adapters[p]

        def inherited_from(self, p):
            return None

    mgr = _Mgr()
    assert billing.billing_kind("fleet-dgx", mgr, None, "glm") == "free"
    assert billing.billing_kind("fleet-cloud", mgr, None, "glm") == "pay"
    assert billing.adapter_pays(adapters["fleet-dgx"], mgr) is False
    assert billing.adapter_pays(adapters["fleet-cloud"], mgr) is True
