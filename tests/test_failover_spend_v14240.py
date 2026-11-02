"""v1.424.0 (the owner's decision D1): FAILOVER NEVER SPENDS MONEY THE USER DID
NOT CHOOSE.

The owner, verbatim: "Failover should remain within the same models provided.
No pay-per-use unless a pay-per-use model was chosen at the onset."

Before this, a call sent to a subscription (Claude Code, Codex, Grok) or to a
local model could, when that provider failed, be answered by an API key that
bills per token: the default-provider fallback (A) went to whatever the
default was, the sideways failover (B) walked ``_FAILOVER_ORDER`` into
``anthropic``/``openai``/``google``/…, the vision reroute picked the first
model that could see, and the one-shot utilities walked the same ladder.

Pinned here, every path, both router lanes:

* ONE definition of "pay per use" (``providers/billing.py``), which
  ``agents/model_team.cost_type`` reads too;
* a subscription pick (and a keyless API a CLI serves) fails over to another
  subscription or a local model — never to a key; with only a key left the
  call fails with its own error plus ONE sentence that says it was not moved;
* a pay-per-use pick still fails over to another key AND to a subscription;
* a local pick fails over to a subscription, never to a key;
* Auto's failover follows the rule from what Auto picked;
* the vision reroute never moves a picture to a key for a pick that is not one;
* the local oracle's swap keeps the user's DEFAULT as the pick;
* the one-shot chain (``daemon/app._one_shot_complete``) and the demo-model
  hop (``_failover_adapter("mock")``);
* both chat lanes say the sentence (``fix``), and an agent run's reason does.
"""

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon import app as app_module
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import billing
from iron_jarvis.providers import failure_words as FW
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter

SENTENCE_OPENAI = (
    "Iron Jarvis didn't switch to OpenAI, which bills per use, because you "
    "didn't choose a pay-per-use model for this."
)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """The real retry code, without its seconds of backoff (the jitter is
    the backoff's factor: 0 makes every wait ``sleep(0)``)."""
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


# --------------------------------------------------------------------------- #
# doubles
# --------------------------------------------------------------------------- #

LOCAL = "http://127.0.0.1:11434/v1/chat/completions"


class _A(LLMAdapter):
    """A provider double: ``fail`` raises on every call; ``endpoint`` is the
    address ``egress`` reads (a local one makes it a model on this network)."""

    def __init__(self, provider, *, fail=None, model="m", endpoint="", vision=True, tools=True):
        self.provider, self.model, self.fail, self.calls = provider, model, fail, 0
        self._endpoint = endpoint
        self._caps = {"tool_use": tools, "vision": vision}

    def capabilities(self):
        return dict(self._caps)

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return LLMResponse(text=f"from {self.provider}", tool_calls=[], usage={})


class _Mgr:
    """A manager double: ``inherit`` maps a keyless API name to the CLI that
    serves it (``ProviderManager.inherited_from``)."""

    def __init__(self, adapters, inherit=None):
        self.adapters = adapters
        self.inherit = dict(inherit or {})

    def available(self, p):
        return p in self.adapters or p in self.inherit

    def get(self, p, model=None):
        if p in self.inherit:
            return self.adapters[self.inherit[p]]
        return self.adapters[p]

    def inherited_from(self, p):
        return self.inherit.get(p)

    def has_available_api_provider(self):
        return True

    def has_available_real_endpoint(self):
        return False

    def runtime_provider_names(self):
        return []


def _busy(p):
    """A transient failure (an overload): the kind a router fails over from."""
    return ProviderError(f"{p}: overloaded", status_code=529)


def _user(images=None):
    return LLMMessage(role="user", content="hi", images=images or [])


async def _run(router, lane, **kw):
    """(provider, reason) of the call, through ``complete()`` or ``stream()``."""
    msgs = kw.pop("messages", [_user()])
    if lane == "complete":
        res = await router.complete(system="", messages=msgs, tools=[], **kw)
        return res.provider, res.reason
    final = None
    async for frame in router.stream(system="", messages=msgs, tools=[], **kw):
        if frame.get("type") == "final":
            final = frame
    assert final is not None
    return final.get("provider"), final.get("reason")


LANES = ["complete", "stream"]


# --------------------------------------------------------------------------- #
# 1. ONE definition
# --------------------------------------------------------------------------- #


def test_one_definition_of_pay_per_use(monkeypatch):
    claude, codex = _A("claude-cli"), _A("codex-cli")
    mgr = _Mgr(
        {
            "claude-cli": claude, "codex-cli": codex, "grok-cli": _A("grok-cli"),
            "openai": _A("openai"), "google": _A("google"), "xai": _A("xai"),
            "openrouter": _A("openrouter"),
            "ollama": _A("ollama", endpoint=LOCAL),
            "custom": _A("custom", endpoint="https://api.cloud.example/v1/chat/completions"),
            "mock": _A("mock"),
        },
        inherit={"anthropic": "claude-cli"},
    )
    kind = lambda p, model="": billing.billing_kind(p, mgr, None, model)  # noqa: E731
    assert [kind(p) for p in ("claude-cli", "codex-cli", "grok-cli")] == ["plan"] * 3
    # A keyless API name a signed-in CLI serves is the plan, not the key.
    assert kind("anthropic") == "plan"
    assert [kind(p) for p in ("openai", "google", "xai", "openrouter")] == ["pay"] * 4
    assert kind("ollama") == "free" and kind("mock") == "free"
    # An endpoint whose model leaves this network bills per use.
    assert kind("custom") == "pay"
    # A Claude model that draws pay-as-you-go usage credits bills per use.
    from iron_jarvis.eval import pricing

    monkeypatch.setattr(pricing, "draws_usage_credits", lambda m: m == "credits-model")
    assert kind("claude-cli", "credits-model") == "pay"
    assert kind("claude-cli", "plan-model") == "plan"
    # The model team's cost words read the same definition.
    from iron_jarvis.agents import model_team as MT

    p = types.SimpleNamespace(providers=mgr, config=None)
    assert [MT.cost_type(p, x) for x in ("codex-cli", "openai", "ollama", "anthropic")] == [
        MT.COST_PLAN, MT.COST_PAY, MT.COST_FREE, MT.COST_PLAN,
    ]
    assert MT.cost_type(p, "claude-cli", "credits-model") == MT.COST_PAY


def test_a_reading_that_fails_is_pay_for_a_candidate_and_not_pay_for_a_pick():
    class _Broken:
        def get(self, p):
            raise RuntimeError("boom")

        def inherited_from(self, p):
            raise RuntimeError("boom")

    from iron_jarvis.providers import egress

    real = egress.stays_on_this_network
    try:
        egress.stays_on_this_network = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        assert billing.is_pay_per_use("claude-cli", _Broken(), unsure=True) is True
        assert billing.is_pay_per_use("claude-cli", _Broken(), unsure=False) is False
    finally:
        egress.stays_on_this_network = real


# --------------------------------------------------------------------------- #
# 2. the router: a subscription pick
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("pick", ["claude-cli", "anthropic"])
async def test_a_subscription_pick_fails_over_to_a_subscription_never_a_key(lane, pick):
    """The default is an OpenAI key (fallback (A) used to land there first);
    the pick — Claude Code, or a keyless Anthropic it serves — goes to Codex."""
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    openai, codex = _A("openai"), _A("codex-cli")
    mgr = _Mgr({"claude-cli": claude, "openai": openai, "codex-cli": codex},
               inherit={"anthropic": "claude-cli"})
    router = ModelRouter(mgr, "openai", EventBus())
    served, reason = await _run(router, lane, provider=pick)
    assert served == "codex-cli" and reason == "failover"
    assert openai.calls == 0, "a subscription pick reached a pay-per-use key"


@pytest.mark.parametrize("lane", LANES)
async def test_with_only_a_paid_key_left_the_call_fails_and_says_so(lane):
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    openai = _A("openai")
    router = ModelRouter(_Mgr({"claude-cli": claude, "openai": openai}), "openai", EventBus())
    with pytest.raises(RuntimeError) as info:
        await _run(router, lane, provider="claude-cli")
    assert openai.calls == 0
    exc = info.value
    # The transient tail's lead is honest: the key was never tried.
    assert str(exc).startswith("every connected model you can use without paying per use")
    assert billing.left_out(exc) == ("openai",)
    words = FW.friendly_error(exc, provider="claude-cli")
    assert words["fix"].endswith(SENTENCE_OPENAI), words
    reason, _tech = FW.run_failure_words(exc, provider="claude-cli")
    assert reason.endswith(SENTENCE_OPENAI), reason


@pytest.mark.parametrize("lane", LANES)
async def test_a_permanent_failure_skips_a_paid_default_and_says_so(lane):
    """Fallback (A) runs for a PERMANENT failure too (no (B)): it must not
    reach a pay-per-use default either."""
    claude = _A("claude-cli", fail=ProviderError("claude-cli: bad request", status_code=400))
    openai = _A("openai")
    router = ModelRouter(_Mgr({"claude-cli": claude, "openai": openai}), "openai", EventBus())
    with pytest.raises(ProviderError) as info:
        await _run(router, lane, provider="claude-cli")
    assert info.value is claude.fail
    assert openai.calls == 0
    assert billing.left_out(info.value) == ("openai",)


@pytest.mark.parametrize("lane", LANES)
async def test_a_failure_with_nothing_left_out_reads_as_before(lane):
    """No pay-per-use candidate was skipped: the old words, no sentence."""
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    router = ModelRouter(_Mgr({"claude-cli": claude}), "claude-cli", EventBus())
    with pytest.raises(RuntimeError) as info:
        await _run(router, lane, provider="claude-cli")
    assert str(info.value).startswith(
        "every connected model is rate-limited or unavailable right now. Wait a minute"
    )
    assert billing.left_out(info.value) == ()
    assert "bills per use" not in FW.friendly_error(info.value)["fix"]


# --------------------------------------------------------------------------- #
# 3. a pay-per-use pick is unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
async def test_a_paid_pick_still_fails_over_to_another_key(lane):
    openai = _A("openai", fail=_busy("openai"))
    google = _A("google")
    router = ModelRouter(_Mgr({"openai": openai, "google": google}), "google", EventBus())
    served, reason = await _run(router, lane, provider="openai")
    assert (served, reason) == ("google", "failover")


@pytest.mark.parametrize("lane", LANES)
async def test_a_paid_pick_still_fails_over_to_a_subscription(lane):
    openai = _A("openai", fail=_busy("openai"))
    codex = _A("codex-cli")
    router = ModelRouter(_Mgr({"openai": openai, "codex-cli": codex}), "openai", EventBus())
    served, reason = await _run(router, lane, provider="openai")
    assert (served, reason) == ("codex-cli", "failover")


# --------------------------------------------------------------------------- #
# 4. a local pick
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
async def test_a_local_pick_fails_over_to_a_subscription_never_a_key(lane):
    """A local box that ANSWERED with an error, under the "failover" policy
    (the default policy refuses outright): Claude Code, never the key."""
    local = _A("ollama", endpoint=LOCAL, fail=ProviderError("ollama: busy", status_code=503))
    openai, claude = _A("openai"), _A("claude-cli")
    router = ModelRouter(
        _Mgr({"ollama": local, "openai": openai, "claude-cli": claude}), "openai", EventBus(),
        local_policy=lambda: "failover",
    )
    served, reason = await _run(router, lane, provider="ollama")
    assert (served, reason) == ("claude-cli", "failover")
    assert openai.calls == 0


@pytest.mark.parametrize("lane", LANES)
async def test_the_local_oracle_keeps_the_users_default_as_the_pick(lane):
    """The oracle moved a DEFAULT call to a local model; the user's pick is
    still their default (an OpenAI key, pay per use), so a failover may use
    a key."""
    local = _A("ollama", endpoint=LOCAL, fail=ProviderError("ollama: busy", status_code=503))
    openai = _A("openai")
    router = ModelRouter(
        _Mgr({"ollama": local, "openai": openai}), "openai", EventBus(),
        local_oracle=lambda task: ("ollama", "m"), local_policy=lambda: "failover",
    )
    served, reason = await _run(router, lane)
    assert local.calls >= 1
    assert (served, reason) == ("openai", "failover")


# --------------------------------------------------------------------------- #
# 5. Auto
# --------------------------------------------------------------------------- #


def _auto(pick):
    async def route(system, messages, tools, task_class):
        return {"provider": pick, "model": None, "tier": "t"}

    return route


@pytest.mark.parametrize("lane", LANES)
async def test_auto_follows_the_rule_from_what_it_picked(lane):
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    openai = _A("openai")
    router = ModelRouter(_Mgr({"claude-cli": claude, "openai": openai}), "auto", EventBus(),
                         auto_route=_auto("claude-cli"))
    with pytest.raises(RuntimeError) as info:
        await _run(router, lane)
    assert openai.calls == 0 and billing.left_out(info.value) == ("openai",)


@pytest.mark.parametrize("lane", LANES)
async def test_an_auto_pick_that_bills_per_use_may_move_to_another_key(lane):
    openai = _A("openai", fail=_busy("openai"))
    google = _A("google")
    router = ModelRouter(_Mgr({"openai": openai, "google": google}), "auto", EventBus(),
                         auto_route=_auto("openai"))
    served, reason = await _run(router, lane)
    assert (served, reason) == ("google", "failover")


# --------------------------------------------------------------------------- #
# 6. the vision reroute
# --------------------------------------------------------------------------- #

PICTURE = [{"data_b64": "AAAA", "media_type": "image/png"}]


@pytest.mark.parametrize("lane", LANES)
async def test_a_picture_is_never_rerouted_to_a_paid_key(lane):
    """A local model that cannot see: the picture goes to Claude Code (a
    subscription), never to the OpenAI key — even though the key is the
    default and is tried first."""
    local = _A("ollama", endpoint=LOCAL, vision=False)
    openai, claude = _A("openai"), _A("claude-cli")
    router = ModelRouter(_Mgr({"ollama": local, "openai": openai, "claude-cli": claude}),
                         "openai", EventBus())
    served, reason = await _run(router, lane, provider="ollama", messages=[_user(PICTURE)])
    assert (served, reason) == ("claude-cli", "failover")
    assert openai.calls == 0


@pytest.mark.parametrize("lane", LANES)
async def test_with_only_a_paid_key_able_to_see_the_pick_keeps_the_picture(lane):
    local = _A("ollama", endpoint=LOCAL, vision=False)
    openai = _A("openai")
    router = ModelRouter(_Mgr({"ollama": local, "openai": openai}), "openai", EventBus())
    served, _reason = await _run(router, lane, provider="ollama", messages=[_user(PICTURE)])
    assert served == "ollama" and openai.calls == 0


@pytest.mark.parametrize("lane", LANES)
async def test_a_paid_pick_that_cannot_see_still_moves_a_picture_to_a_key(lane):
    xai = _A("xai", vision=False)
    openai = _A("openai")
    router = ModelRouter(_Mgr({"xai": xai, "openai": openai}), "openai", EventBus())
    served, reason = await _run(router, lane, provider="xai", messages=[_user(PICTURE)])
    assert (served, reason) == ("openai", "failover")


# --------------------------------------------------------------------------- #
# 7. the one-shot chain (daemon/app.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def one_shot(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    adapters: dict[str, _A] = {}

    async def once(adapter, *, system, messages, tools, attempts=3):
        return await adapter.complete(system=system, messages=messages, tools=tools)

    monkeypatch.setattr(app_module, "_complete_with_retry", once)
    monkeypatch.setattr(platform.providers, "available", lambda name, *a, **k: name in adapters)
    monkeypatch.setattr(platform.providers, "get", lambda name="", model=None, *a, **k: adapters[name])
    # No keyless inheritance on this box: an "anthropic" here is a real key.
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    return types.SimpleNamespace(d=app.state.d, adapters=adapters, platform=platform, app=app)


async def test_a_one_shot_subscription_pick_never_reaches_a_paid_key(one_shot):
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    openai = _A("openai")
    one_shot.adapters.update({"claude-cli": claude, "openai": openai})
    with pytest.raises(HTTPException) as info:
        await one_shot.d._one_shot_complete("claude-cli", claude, system="s", messages=[])
    assert openai.calls == 0
    assert info.value.detail.endswith(SENTENCE_OPENAI), info.value.detail
    # With a subscription to go to, it goes there.
    codex = _A("codex-cli")
    one_shot.adapters["codex-cli"] = codex
    resp, used, _m = await one_shot.d._one_shot_complete("claude-cli", claude, system="s", messages=[])
    assert used == "codex-cli" and resp.text == "from codex-cli" and openai.calls == 0


async def test_a_one_shot_paid_pick_still_fails_over_to_a_key(one_shot):
    anthropic = _A("anthropic", fail=_busy("anthropic"))
    openai = _A("openai")
    one_shot.adapters.update({"anthropic": anthropic, "openai": openai})
    resp, used, _m = await one_shot.d._one_shot_complete("anthropic", anthropic, system="s", messages=[])
    assert used == "openai" and openai.calls == 1


def test_a_demo_model_default_never_hops_to_a_paid_key(one_shot):
    """The background utilities (compaction, lesson distill, the workflow
    builder …) whose default is the demo model hop to a REAL model through
    ``_failover_adapter("mock")``: never to a key the user never chose."""
    assert one_shot.platform.config.default_provider == "mock"
    one_shot.adapters["anthropic"] = _A("anthropic")
    assert one_shot.d._failover_adapter("mock") == (None, None)
    r = TestClient(one_shot.app).post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 400 and one_shot.adapters["anthropic"].calls == 0
    # A subscription is fine.
    one_shot.adapters["claude-cli"] = codex_like = _A("claude-cli")
    adapter, name = one_shot.d._failover_adapter("mock")
    assert adapter is codex_like and name == "claude-cli"


# --------------------------------------------------------------------------- #
# 8. both chat lanes say the sentence (lock-step)
# --------------------------------------------------------------------------- #


def _router_with_only_a_key_left():
    claude = _A("claude-cli", fail=_busy("claude-cli"))
    openai = _A("openai")
    return ModelRouter(_Mgr({"claude-cli": claude, "openai": openai}), "openai", EventBus()), openai


def test_the_post_lane_says_it_was_not_moved_to_a_paid_key(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    router, openai = _router_with_only_a_key_left()
    monkeypatch.setattr(client.app.state.platform.router, "complete", router.complete)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "q"}],
                                   "provider": "claude-cli"})
    assert r.status_code == 502, r.text
    assert r.json()["fix"].endswith(SENTENCE_OPENAI), r.json()
    assert openai.calls == 0


def test_the_stream_lane_says_it_was_not_moved_to_a_paid_key(tmp_path, monkeypatch):
    import json

    client = TestClient(create_app(str(tmp_path)))
    router, openai = _router_with_only_a_key_left()
    monkeypatch.setattr(client.app.state.platform.router, "stream", router.stream)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "q"}],
                                          "provider": "claude-cli"})
    errs = [
        json.loads(b.split("data:", 1)[1])
        for b in r.text.split("\n\n")
        if b.startswith("event: error")
    ]
    assert errs, r.text[-800:]
    assert errs[-1]["fix"].endswith(SENTENCE_OPENAI), errs[-1]
    assert openai.calls == 0


def test_the_sentence_names_every_left_out_key_by_its_plain_name():
    assert billing.not_moved_sentence(["openai", "google"]) == (
        "Iron Jarvis didn't switch to OpenAI or Google Gemini, which bill per use, "
        "because you didn't choose a pay-per-use model for this."
    )
    assert billing.not_moved_sentence(["openai", "openai"]) == SENTENCE_OPENAI
    assert billing.not_moved_sentence([]) == ""
