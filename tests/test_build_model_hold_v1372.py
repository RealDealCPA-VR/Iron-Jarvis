"""v1.372.0: a Build session and its helpers stay on the session's MODEL.

The user's decision 4 for IJ harness part 2: "always on the session's own
account and model (never another provider)". v1.368.0 held a turn only while
an Iron-Proxy account was pinned; a session on this PC's own login, a local
model or an API key could still fall back to the default provider, fail over
sideways, or have a picture rerouted to a vision model nobody chose. The
build route now arms a :class:`~iron_jarvis.providers.hold.ProviderHold` for
the turn (and ``harness.helpers.pinned`` again for each helper), and:

* the router raises the provider's own error — no default fallback (A), no
  sideways failover (B), no capability reroute — in BOTH lanes;
* the prompted-tools wrap stays (it keeps the SAME model);
* an AUTO call is never held (the user delegated that choice);
* ``view_image`` asks the session's model, never the vision role's.
"""

from __future__ import annotations

import pytest
from PIL import Image

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import helpers as H
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.images import ViewImageTool
from tests.test_build_accounts_v1368 import Behaviour, isolate, wire_doubles
from tests.test_build_sessions_v1367 import asgi, frames_of, open_turn


class _Overloaded(LLMAdapter):
    def __init__(self, provider="openai", model="gpt-x"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise ProviderError("overloaded", transient=True)

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise ProviderError("overloaded", transient=True)
        yield {}  # pragma: no cover — makes this an async generator


class _Ok(LLMAdapter):
    def __init__(self, provider, model="m", caps=None):
        self.provider, self.model = provider, model
        self.calls = 0
        self.caps = caps

    def capabilities(self):
        return self.caps if self.caps is not None else {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=f"{self.provider} answered", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "text", "text": f"{self.provider} answered"}
        yield {"type": "final", "text": f"{self.provider} answered", "tool_calls": [], "usage": {}}


class _Manager:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, provider):
        return provider in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, provider, model=None):
        return self.adapters[provider]


def _setup(picked=None):
    picked = picked or _Overloaded()
    default = _Ok("anthropic")
    other = _Ok("gemini")
    manager = _Manager({picked.provider: picked, "anthropic": default, "gemini": other})
    # The user's global strict pin is OFF and no account is pinned: only the
    # Build session's hold can keep the call where it is.
    router = ModelRouter(manager, "anthropic", EventBus(), strict_pin=lambda: False)
    return router, picked, default, other


_MSG = [LLMMessage(role="user", content="q")]


@pytest.fixture
def held():
    token = HOLD.set_hold("openai", "gpt-x")
    yield
    HOLD.reset_hold(token)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


# --------------------------------------------------------------------------- #
# the router, both lanes
# --------------------------------------------------------------------------- #


async def test_without_a_hold_an_explicit_pick_fails_over():
    """The control, so the held tests prove something."""
    router, picked, default, other = _setup()
    res = await router.complete(provider="openai", model="gpt-x", system="", messages=_MSG, tools=[])
    assert res.provider != "openai"
    assert default.calls + other.calls == 1


async def test_a_held_call_raises_its_own_error_and_reaches_no_other_provider(held):
    router, picked, default, other = _setup()
    with pytest.raises(ProviderError, match="overloaded"):
        await router.complete(provider="openai", model="gpt-x", system="", messages=_MSG, tools=[])
    assert picked.calls >= 1
    assert default.calls == 0 and other.calls == 0


async def test_a_held_stream_raises_and_reaches_no_other_provider(held):
    router, picked, default, other = _setup()
    with pytest.raises(ProviderError, match="overloaded"):
        async for _ in router.stream(
            provider="openai", model="gpt-x", system="", messages=_MSG, tools=[],
        ):
            pass
    assert picked.calls >= 1
    assert default.calls == 0 and other.calls == 0


async def test_a_session_on_the_default_provider_never_fails_over_sideways(held):
    """A session with no provider picked runs on the DEFAULT; held, its
    failure is its own — the sideways chain never runs."""
    picked = _Overloaded()
    other = _Ok("gemini")
    manager = _Manager({"openai": picked, "gemini": other})
    router = ModelRouter(manager, "openai", EventBus(), strict_pin=lambda: False)
    with pytest.raises(ProviderError):
        await router.complete(system="", messages=_MSG, tools=[])
    assert other.calls == 0


async def test_a_picture_is_never_rerouted_to_another_vision_model(held):
    """A model without vision keeps the request (it drops or refuses the
    picture); the router never moves the turn — its code and files with it —
    to a vision model nobody picked."""
    blind = _Ok("openai", "gpt-x", caps={"tool_use": True, "vision": False})
    router, _picked, default, other = _setup(blind)
    pic = [LLMMessage(role="user", content="look", images=[{"data_b64": "AA==", "media_type": "image/png"}])]
    res = await router.complete(provider="openai", model="gpt-x", system="", messages=pic, tools=[])
    assert res.provider == "openai" and blind.calls == 1
    assert default.calls == 0 and other.calls == 0


async def test_a_held_stream_never_reroutes_a_picture_either(held):
    """MIRROR NOTE (lock-step): stream() carries the same seam."""
    blind = _Ok("openai", "gpt-x", caps={"tool_use": True, "vision": False})
    router, _picked, default, other = _setup(blind)
    pic = [LLMMessage(role="user", content="look", images=[{"data_b64": "AA==", "media_type": "image/png"}])]
    async for _ in router.stream(provider="openai", model="gpt-x", system="", messages=pic, tools=[]):
        pass
    assert blind.calls == 1 and default.calls == 0 and other.calls == 0


async def test_without_a_hold_the_picture_is_rerouted():
    """The control for the test above."""
    blind = _Ok("openai", "gpt-x", caps={"tool_use": True, "vision": False})
    router, _picked, default, other = _setup(blind)
    pic = [LLMMessage(role="user", content="look", images=[{"data_b64": "AA==", "media_type": "image/png"}])]
    res = await router.complete(provider="openai", model="gpt-x", system="", messages=pic, tools=[])
    assert res.provider != "openai"


async def test_a_held_text_only_model_keeps_the_prompted_tools_wrap(held):
    """The wrap keeps the request on the SAME model — the hold keeps it."""
    text_only = _Ok("openai", "gpt-x", caps={"tool_use": False, "vision": True})
    router, _picked, default, other = _setup(text_only)
    tools = [{"name": "read_file", "description": "read", "input_schema": {"type": "object"}}]
    res = await router.complete(provider="openai", model="gpt-x", system="", messages=_MSG, tools=tools)
    assert res.provider == "openai" and res.reason == "prompted-tools"
    assert default.calls == 0 and other.calls == 0


def test_an_auto_call_is_never_held(held):
    """Auto is the user's own delegation: the router may still choose."""
    assert router_mod._session_held(auto_selected=True) is False
    assert router_mod._session_held(auto_selected=False) is True


def test_no_hold_outside_a_build_turn():
    assert HOLD.current_hold() is None
    assert router_mod._session_held(auto_selected=False) is False


# --------------------------------------------------------------------------- #
# view_image
# --------------------------------------------------------------------------- #


class _RecordingRouter:
    def __init__(self):
        self.calls: list[dict] = []

    async def complete(self, *, provider=None, model=None, system, messages, tools, session_id=None, **kw):
        self.calls.append({"provider": provider, "model": model})

        class _R:
            response = LLMResponse(text="a red square", tool_calls=[], usage={})

        r = _R()
        r.provider, r.model = provider, model
        return r


async def test_view_image_in_a_build_turn_asks_the_sessions_own_model(tmp_path, monkeypatch, held):
    from iron_jarvis.tools import images as images_mod

    class _Role:
        applied = True
        provider, model = "gemini", "gemini-vision"

    monkeypatch.setattr(images_mod, "resolve_role", lambda *a, **k: _Role())
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "pic.png")
    router = _RecordingRouter()
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r",
                      config=None, event_bus=None, engine=None)
    res = await ViewImageTool(lambda: router).execute({"path": "pic.png"}, ctx)
    assert res.ok, res.error
    assert router.calls == [{"provider": "openai", "model": "gpt-x"}]


async def test_view_image_outside_a_build_turn_keeps_the_vision_role(tmp_path, monkeypatch):
    from iron_jarvis.tools import images as images_mod

    class _Role:
        applied = True
        provider, model = "gemini", "gemini-vision"

    monkeypatch.setattr(images_mod, "resolve_role", lambda *a, **k: _Role())
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "pic.png")
    router = _RecordingRouter()
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="r",
                      config=None, event_bus=None, engine=None)
    res = await ViewImageTool(lambda: router).execute({"path": "pic.png"}, ctx)
    assert res.ok, res.error
    assert router.calls == [{"provider": "gemini", "model": "gemini-vision"}]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_a_helpers_task_is_created_under_the_sessions_hold():
    """Armed EXPLICITLY around the helper's task, so a hop that dropped the
    context cannot free it — with no account pin at all."""
    parent = H.HelperParent(session_id="build:x", folder="", provider="codex-cli", model="gpt-5.5")
    assert HOLD.current_hold() is None
    with H.pinned(parent):
        got = HOLD.current_hold()
    assert got == HOLD.ProviderHold("codex-cli", "gpt-5.5")
    assert HOLD.current_hold() is None


# --------------------------------------------------------------------------- #
# the build turn, through the real route and the real router
# --------------------------------------------------------------------------- #


@pytest.fixture
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


async def test_a_build_turn_without_an_account_is_held_on_its_provider(tmp_path, monkeypatch, _isolated):
    """Iron-Proxy OFF (no account pin): an overload on the session's provider
    is the turn's error. The default provider — up and answering — is never
    called. Before v1.372.0 it answered the turn."""
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    app.state.platform.config.default_provider = "codex-cli"
    beh = wire_doubles(app, monkeypatch, Behaviour(["never"]))
    beh.overload["claude-cli"] = 10
    status, view = await asgi(app, "POST", "/build/sessions", {
        "folder": str(work), "provider": "claude-cli", "model": "claude-sonnet-4-5",
    })
    assert status == 201, view
    status, raw = await open_turn(app, view["id"], {"text": "hi", "turn_id": "t-hold"})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames[-1][0] == "error", frames[-3:]
    assert set(beh.attempts) == {"claude-cli"}, beh.attempts
    # The hold ended with the turn: the same call outside it moves on.
    beh.attempts.clear()
    beh.overload["claude-cli"] = 10
    async for _f in app.state.platform.router.stream(
        provider="claude-cli", model="claude-sonnet-4-5", system="",
        messages=[LLMMessage(role="user", content="x")], tools=[],
    ):
        pass
    assert "codex-cli" in beh.attempts, "outside a Build turn the default is tried (control)"


# --------------------------------------------------------------------------- #
# a one-shot inside a Build turn (compaction and the like)
# --------------------------------------------------------------------------- #

from tests.test_build_account_review_v1368 import one_shot  # noqa: E402,F401 — the fixture


async def test_a_one_shot_under_a_model_hold_never_fails_over(one_shot):  # noqa: F811
    """No account pin, only the hold: the one-shot raises the provider's own
    error and the compaction factory swaps in nothing."""
    from fastapi import HTTPException

    token = HOLD.set_hold("claude-cli", "")
    try:
        with pytest.raises(HTTPException):
            await one_shot.d._one_shot_complete("claude-cli", one_shot.primary, system="s", messages=[])
        assert one_shot.d._compaction_complete("not-a-provider") is None
    finally:
        HOLD.reset_hold(token)
    assert one_shot.primary.calls == 1 and one_shot.stand_in.calls == 0


# --------------------------------------------------------------------------- #
# v1.372.0 review: the hold BINDS, it does not only freeze
# --------------------------------------------------------------------------- #


def _bound_router():
    # The session runs on codex-cli; the configured DEFAULT is anthropic.
    session = _Ok("codex-cli", "gpt-5.5")
    default = _Ok("anthropic", "claude-x")
    other = _Ok("gemini", "gemini-vision")
    mgr = _Manager({"codex-cli": session, "anthropic": default, "gemini": other})
    return ModelRouter(mgr, "anthropic", EventBus(), strict_pin=lambda: False), session, default, other


@pytest.fixture
def codex_held():
    token = HOLD.set_hold("codex-cli", "gpt-5.5")
    yield
    HOLD.reset_hold(token)


async def test_a_call_naming_no_provider_is_served_by_the_sessions(codex_held):
    router, session, default, other = _bound_router()
    res = await router.complete(system="", messages=_MSG, tools=[])
    assert res.provider == "codex-cli" and session.calls == 1 and default.calls == 0
    async for _ in router.stream(system="", messages=_MSG, tools=[]):
        pass
    assert session.calls == 2 and default.calls == 0, "stream() binds too (lock-step)"


async def test_a_call_naming_another_provider_is_refused_in_words(codex_held):
    router, session, default, other = _bound_router()
    with pytest.raises(HOLD.HeldProviderRefused) as ei:
        await router.complete(provider="gemini", model="gemini-vision", system="", messages=_MSG, tools=[])
    assert "codex-cli" in str(ei.value) and "gemini" in str(ei.value)
    assert router_mod.is_no_failover(ei.value)
    with pytest.raises(HOLD.HeldProviderRefused):
        async for _ in router.stream(provider="gemini", system="", messages=_MSG, tools=[]):
            pass
    assert session.calls == default.calls == other.calls == 0


async def test_an_alias_of_the_sessions_provider_is_not_refused():
    """A keyless ``anthropic`` served by ``claude-cli`` is the SAME adapter."""
    cli = _Ok("claude-cli", "opus")
    mgr = _Manager({"claude-cli": cli, "anthropic": cli, "gemini": _Ok("gemini")})
    router = ModelRouter(mgr, "gemini", EventBus(), strict_pin=lambda: False)
    token = HOLD.set_hold("anthropic", "")
    try:
        res = await router.complete(provider="claude-cli", system="", messages=_MSG, tools=[])
    finally:
        HOLD.reset_hold(token)
    assert res.provider == "claude-cli" and cli.calls == 1


async def test_a_session_on_auto_binds_and_holds_nothing():
    """Auto is the user's own delegation: a held AUTO session's calls may
    still move (the behavioural twin of test_an_auto_call_is_never_held)."""
    router, picked, default, other = _setup()
    token = HOLD.set_hold("auto", "")
    try:
        res = await router.complete(provider="openai", model="gpt-x", system="", messages=_MSG, tools=[])
    finally:
        HOLD.reset_hold(token)
    assert res.provider != "openai" and default.calls + other.calls == 1


async def test_ocr_of_a_picture_read_in_a_build_turn_uses_the_sessions_model(tmp_path, monkeypatch, codex_held):
    """read_file on a picture (or a scanned PDF / attachment) runs OCR, whose
    route was the vision role, else the DEFAULT."""
    from iron_jarvis.documents import local_ocr
    from iron_jarvis.tools.builtins import ReadFileTool

    monkeypatch.setattr(local_ocr, "read_pages", lambda blobs, nums, config=None: {})
    Image.new("RGB", (64, 64), (255, 255, 255)).save(tmp_path / "diagram.png")
    router, session, default, other = _bound_router()
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r",
                      config=None, event_bus=None, engine=None)
    await ReadFileTool(router_resolver=lambda: router).execute({"path": "diagram.png"}, ctx)
    assert default.calls == 0 and session.calls >= 1


async def test_ocr_ignores_the_vision_role_in_a_build_turn(tmp_path, monkeypatch, codex_held):
    from types import SimpleNamespace

    from iron_jarvis.documents import local_ocr, ocr

    monkeypatch.setattr(local_ocr, "read_pages", lambda blobs, nums, config=None: {})
    Image.new("RGB", (64, 64), (255, 255, 255)).save(tmp_path / "shot.png")
    router, session, default, other = _bound_router()
    cfg = SimpleNamespace(model_roles={"vision": "gemini:gemini-vision"})
    await ocr.ocr_image(tmp_path / "shot.png", router, config=cfg)
    assert other.calls == 0 and default.calls == 0 and session.calls >= 1


async def test_ocr_on_a_session_model_that_cannot_see_reads_nothing_by_model(tmp_path, monkeypatch):
    from iron_jarvis.documents import local_ocr, ocr

    monkeypatch.setattr(local_ocr, "read_pages", lambda blobs, nums, config=None: {})
    Image.new("RGB", (64, 64), (255, 255, 255)).save(tmp_path / "shot.png")
    blind = _Ok("grok-cli", "grok", caps={"tool_use": True, "vision": False})
    router = ModelRouter(_Manager({"grok-cli": blind, "anthropic": _Ok("anthropic")}), "anthropic",
                         EventBus(), strict_pin=lambda: False)
    token = HOLD.set_hold("grok-cli", "grok")
    try:
        text, note = await ocr.ocr_image(tmp_path / "shot.png", router, config=None)
    finally:
        HOLD.reset_hold(token)
    assert blind.calls == 0 and not text and "cannot read pictures" in note


def test_a_helpers_compaction_is_written_by_the_sessions_model(tmp_path):
    """A helper's run asks for its compaction with NO provider; the factory
    used to resolve the configured default."""
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    asked: list[tuple] = []

    def get(name, model=None, *a, **k):
        asked.append((name, model))
        raise KeyError(name)

    platform.providers.get = get
    token = HOLD.set_hold("codex-cli", "gpt-5.5")
    try:
        app.state.d._compaction_complete()
    finally:
        HOLD.reset_hold(token)
    assert asked and asked[0] == ("codex-cli", "gpt-5.5"), asked


async def test_a_helper_on_the_decomposed_lane_plans_on_the_sessions_model(monkeypatch):
    """A bulk task decomposes; its plan / judge / synthesize ROLES may name
    another provider. Under the hold every role is the session's pair."""
    from types import SimpleNamespace

    from iron_jarvis.agents import decompose

    seen: list = []

    async def plan_task(runtime, run, session, agent_def, llm=None):
        seen.append((getattr(llm, "provider", None), getattr(llm, "model", None)))
        return None

    monkeypatch.setattr(decompose, "plan_task", plan_task)
    router, session_ad, default, other = _bound_router()
    cfg = SimpleNamespace(model_roles={"plan": "anthropic:claude-x"})
    runtime = SimpleNamespace(p=SimpleNamespace(config=cfg, providers=router.manager))
    run = SimpleNamespace(id="r")
    sess = SimpleNamespace(id="s", provider="codex-cli", model="gpt-5.5")
    kw = dict(system_prompt="", tool_specs=[], session_allow=set(), sink=None)
    await decompose.run_decomposed(runtime, run, sess, None, **kw)
    assert seen[-1] == ("anthropic", "claude-x"), "the control: unheld, the role applies"
    token = HOLD.set_hold("codex-cli", "gpt-5.5")
    try:
        await decompose.run_decomposed(runtime, run, sess, None, **kw)
    finally:
        HOLD.reset_hold(token)
    assert seen[-1] == ("codex-cli", "gpt-5.5"), seen


async def test_background_work_an_event_handler_starts_never_carries_the_hold():
    """A helper's run is finalized INSIDE the turn; a sync handler that hops
    work onto the loop (the skill-distill sweep) carried the session's hold
    and account pin into it."""
    import asyncio

    from iron_jarvis.iron_proxy import pin as P

    loop = asyncio.get_running_loop()
    bus = EventBus()
    seen: list = []
    done = asyncio.Event()

    async def sweep():
        seen.append((HOLD.current_hold(), P.current()))
        done.set()

    def handler(event):
        if event.type == "session.completed":
            asyncio.run_coroutine_threadsafe(sweep(), loop)

    bus.add_handler(handler)
    token = HOLD.set_hold("codex-cli", "gpt-5.5")
    ptoken = P.set_pin(P.AccountPin(profile_id="x-plus", ij_provider="codex-cli"))
    try:
        await bus.publish("session.completed", {}, session_id="child")
        assert HOLD.current_hold() is not None and P.current() is not None, "the turn keeps both"
    finally:
        P.reset_pin(ptoken)
        HOLD.reset_hold(token)
    await asyncio.wait_for(done.wait(), 5)
    assert seen == [(None, None)], seen


async def test_view_image_on_a_session_model_that_cannot_see_says_so(tmp_path):
    blind = _Ok("grok-cli", "grok", caps={"tool_use": True, "vision": False})

    class _BlindRouter(_RecordingRouter):
        manager = _Manager({"grok-cli": blind})

    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "pic.png")
    router = _BlindRouter()
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r",
                      config=None, event_bus=None, engine=None)
    token = HOLD.set_hold("grok-cli", "grok")
    try:
        res = await ViewImageTool(lambda: router).execute({"path": "pic.png"}, ctx)
    finally:
        HOLD.reset_hold(token)
    assert not res.ok and "cannot look at pictures" in res.error
    assert router.calls == []


async def test_a_held_local_model_that_never_answered_gets_the_plain_refusal():
    """The v1.162.0 refusal (and its banner event) still comes first."""
    import httpx

    class _Dead(LLMAdapter):
        provider, model = "ollama", "qwen"

        async def complete(self, *, system, messages, tools, **kw):
            raise httpx.ConnectError("All connection attempts failed")

        async def stream(self, *, system, messages, tools, **kw):
            raise httpx.ConnectError("All connection attempts failed")
            yield {}  # pragma: no cover

    async def run(held: bool, lane: str):
        bus = EventBus()
        router = ModelRouter(_Manager({"ollama": _Dead(), "anthropic": _Ok("anthropic")}),
                             "anthropic", bus, strict_pin=lambda: False)
        token = HOLD.set_hold("ollama", "qwen") if held else None
        try:
            with pytest.raises(Exception) as ei:
                if lane == "complete":
                    await router.complete(provider="ollama", model="qwen", system="", messages=_MSG, tools=[])
                else:
                    async for _ in router.stream(provider="ollama", model="qwen", system="",
                                                 messages=_MSG, tools=[]):
                        pass
        finally:
            if token is not None:
                HOLD.reset_hold(token)
        return type(ei.value), "provider.downgraded" in [e.type for e in bus.history]

    for lane in ("complete", "stream"):
        unheld = await run(False, lane)
        held = await run(True, lane)
        assert held == unheld and held[1], (lane, held, unheld)


def test_a_one_shot_in_a_session_on_auto_may_still_fail_over():
    """``_pin_armed`` agrees with the router: a hold on Auto holds nothing."""
    from iron_jarvis.daemon.app import _pin_armed

    for provider, armed in (("auto", False), ("codex-cli", True)):
        token = HOLD.set_hold(provider, "")
        try:
            assert _pin_armed() is armed, provider
        finally:
            HOLD.reset_hold(token)
    assert _pin_armed() is False


# --------------------------------------------------------------------------- #
# v1.372.0 re-review: the hold names the model the turn REALLY runs on
# --------------------------------------------------------------------------- #


def _recording(app):
    """Wire a scripted model that records the hold each turn call sees."""
    from tests.test_harness_helpers_v1372 import Model, wire

    model = Model(main=[], helper=None)
    seen: list = []
    real = model.stream

    async def stream(**kw):
        seen.append((kw.get("provider"), HOLD.current_hold()))
        async for f in real(**kw):
            yield f

    model.stream = stream
    wire(app, model)
    return seen


async def test_a_session_made_with_only_a_folder_is_held_to_the_default(tmp_path, _isolated):
    """The dashboard's New session posts ONLY the folder: no provider. A hold
    on "" binds nothing, so the hold names the configured default."""
    from iron_jarvis.harness import sessions as S

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    platform = app.state.platform
    platform.config.default_provider, platform.config.default_model = "anthropic", "claude-x"
    row = S.create(platform.engine, str(work), "", {})
    seen = _recording(app)
    status, raw = await open_turn(app, row.id, {"text": "hi", "turn_id": "rr-a"})
    assert status == 200 and seen, raw
    assert seen[0][1] == HOLD.ProviderHold("anthropic", "claude-x"), seen


async def test_a_session_in_a_project_folder_is_held_to_the_projects_model(tmp_path, _isolated):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Project
    from iron_jarvis.harness import sessions as S

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    platform = app.state.platform
    platform.config.default_provider = "anthropic"
    with session_scope(platform.engine) as db:
        db.add(Project(name="P", root=str(work), default_provider="gemini", default_model="g-1"))
        db.commit()
    row = S.create(platform.engine, str(work), "", {})
    seen = _recording(app)
    status, raw = await open_turn(app, row.id, {"text": "hi", "turn_id": "rr-b"})
    assert status == 200 and seen, raw
    turn_provider, hold = seen[0]
    assert turn_provider == "gemini" and hold == HOLD.ProviderHold("gemini", "g-1"), seen


def test_the_turn_pair_follows_the_lanes_own_order():
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes.build import _turn_pair

    platform = SimpleNamespace(config=SimpleNamespace(default_provider="auto", default_model=""))
    project = SimpleNamespace(default_provider="gemini", default_model="g-1")
    body = SimpleNamespace(provider="codex-cli", model="gpt-5.5")
    assert _turn_pair(platform, body, project) == ("codex-cli", "gpt-5.5")
    assert _turn_pair(platform, SimpleNamespace(provider="", model=""), project) == ("gemini", "g-1")
    # A default on Auto stays Auto: the user delegated the choice.
    assert _turn_pair(platform, SimpleNamespace(provider="", model=""), None) == ("auto", "")


async def test_an_auto_call_inside_a_session_on_a_concrete_model_is_that_models(codex_held):
    router, session, default, other = _bound_router()
    res = await router.complete(provider="auto", system="", messages=_MSG, tools=[])
    assert res.provider == "codex-cli" and default.calls == other.calls == 0


def test_a_custom_agent_on_the_sessions_model_by_an_alias_is_not_refused():
    """The model picker stores a keyless ``anthropic`` for an agent on a
    Claude subscription; the session's served name is ``claude-cli``."""
    from types import SimpleNamespace

    from iron_jarvis.harness import helpers as H

    cli = _Ok("claude-cli", "claude-sonnet-4-5")
    manager = _Manager({"claude-cli": cli, "anthropic": cli, "openai": _Ok("openai")})
    parent = H.HelperParent(session_id="build:x", folder="", provider="claude-cli",
                            model="claude-sonnet-4-5")
    alias = SimpleNamespace(provider="anthropic", model="claude-sonnet-4-5")
    assert H.target_refusal(parent, raw="scout", record=alias, manager=manager) == ""
    other = SimpleNamespace(provider="openai", model="gpt-5.5")
    assert "set to use openai" in H.target_refusal(parent, raw="scout", record=other, manager=manager)
