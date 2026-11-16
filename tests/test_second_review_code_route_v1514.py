"""Second review F2 (v1.514.0, decision E7): "Send coding work to a model
server" had no reader.

The settings ``fleet_code_route_enabled`` / ``fleet_code_target`` /
``fleet_code_task_classes`` were saved from the Model servers page, Settings
and chat, and the page then read "Coding work goes to <server>", while no
router, orchestrator or harness code read them: coding runs kept going to the
default model. Now:

* ONE pure decision, ``providers.router.code_route_decision``, applied where
  a run's model is chosen (``Orchestrator.create_session``: by the time a
  call reaches the router a session always names a provider, so "no model of
  its own" can only be told there). A builder / maintainer / reviewer run
  with NO model of its own takes the target, as if the user had picked it;
  an explicit choice always wins; never a chat turn.
* A target that is set but not available refuses the run BY NAME at its
  first model call (never the default, never another provider), in words
  that say where the work was set to go and where to change it.
* The page's sentence says exactly that, from the same decision and the same
  kinds of work, and says "Not in effect" (and why) when it cannot apply.

Offline: the real orchestrator, runtime and router over ``build_platform``;
only the models are scripted.
"""

from __future__ import annotations

import asyncio
import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.daemon.routes import fleet as fleet_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import (
    CODE_ROUTE_NONE,
    CODE_ROUTE_PLACE,
    CODE_ROUTE_REFUSE,
    CODE_ROUTE_USE,
    CODE_TASK_CLASSES,
    CodeRouteUnavailable,
    code_route_decision,
    code_task_classes,
)

TARGET = "fleet-box"
TARGET_MODEL = "coder-1"


# --------------------------------------------------------------------------- #
# 1. The decision, as a table.
# --------------------------------------------------------------------------- #
class _Mgr:
    def __init__(self, up: set[str]) -> None:
        self.up = up

    def available(self, name: str) -> bool:
        return name in self.up


def _cfg(enabled=True, target=f"{TARGET}:{TARGET_MODEL}", classes=""):
    return SimpleNamespace(
        fleet_code_route_enabled=enabled,
        fleet_code_target=target,
        fleet_code_task_classes=classes,
    )


UP = _Mgr({TARGET, "ollama"})
DOWN = _Mgr(set())


@pytest.mark.parametrize(
    "cfg, task_class, named, mgr, kind, reason",
    [
        (_cfg(), "builder", False, UP, CODE_ROUTE_USE, ""),
        (_cfg(), "maintainer", False, UP, CODE_ROUTE_USE, ""),
        (_cfg(), "reviewer", False, UP, CODE_ROUTE_USE, ""),
        # an explicit choice always wins
        (_cfg(), "builder", True, UP, CODE_ROUTE_NONE, "named"),
        # never a chat turn, never a one-shot or a kind outside the set
        (_cfg(), "chat", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(classes="chat, builder"), "chat", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(), "researcher", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(), "plan", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(), "", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(), None, False, UP, CODE_ROUTE_NONE, "not_coding"),
        # the user's own list decides, known agent types only
        (_cfg(classes="researcher"), "researcher", False, UP, CODE_ROUTE_USE, ""),
        (_cfg(classes="researcher"), "builder", False, UP, CODE_ROUTE_NONE, "not_coding"),
        (_cfg(classes="nonsense"), "builder", False, UP, CODE_ROUTE_NONE, "not_coding"),
        # off / not in effect
        (_cfg(enabled=False), "builder", False, UP, CODE_ROUTE_NONE, "off"),
        (_cfg(target=""), "builder", False, UP, CODE_ROUTE_NONE, "no_target"),
        (_cfg(target=":coder"), "builder", False, UP, CODE_ROUTE_NONE, "unparsed"),
        (_cfg(target="anthropic:claude-opus-4-8"), "builder", False, UP, CODE_ROUTE_NONE, "not_a_server"),
        (_cfg(target="mock:mock-1"), "builder", False, UP, CODE_ROUTE_NONE, "not_a_server"),
        (_cfg(target="fleet-:x"), "builder", False, UP, CODE_ROUTE_NONE, "not_a_server"),
        # the user's own server slots are model servers too
        (_cfg(target="ollama:qwen"), "builder", False, UP, CODE_ROUTE_USE, ""),
        # set, but not available (down, or gone): refused by name, never moved
        (_cfg(), "builder", False, DOWN, CODE_ROUTE_REFUSE, "unavailable"),
        (_cfg(), "builder", False, None, CODE_ROUTE_REFUSE, "unavailable"),
    ],
)
def test_the_decision_table(cfg, task_class, named, mgr, kind, reason):
    route = code_route_decision(cfg, task_class, named=named, manager=mgr)
    assert (route.kind, route.reason) == (kind, reason), route
    if kind != CODE_ROUTE_NONE:
        assert route.provider and route.provider == cfg.fleet_code_target.split(":", 1)[0]


def test_the_kinds_of_work_are_one_definition():
    assert code_task_classes(_cfg()) == list(CODE_TASK_CLASSES)
    assert code_task_classes(_cfg(classes=" Builder , planner, nonsense,builder")) == [
        "builder", "planner",
    ]


def test_an_availability_check_that_raises_refuses_never_routes_elsewhere():
    class _Boom:
        def available(self, _n):
            raise RuntimeError("no answer")

    route = code_route_decision(_cfg(), "builder", named=False, manager=_Boom())
    assert route.kind == CODE_ROUTE_REFUSE


# --------------------------------------------------------------------------- #
# 2. End to end: a real agent run with scripted providers.
# --------------------------------------------------------------------------- #
class _Calls:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []


def _adapter(name: str, calls: _Calls, model: "str | None"):
    class _Scripted(MockLLMAdapter):
        provider = name

        async def complete(self, *, system, messages, tools, **kw):
            calls.seen.append((name, str(model or "")))
            return LLMResponse(text=f"Done on {name}.", finish_reason="stop")

    a = _Scripted()
    a.provider = name
    a.model = str(model or "")
    return a


def _platform(tmp_path, *, enabled: bool, register_target: bool = True):
    p = build_platform(str(tmp_path))
    # No model server is saved in this root: a registered factory is the
    # whole answer (the sampler's cached verdict is the live app's).
    p.providers._dynamic_available = lambda _name: None
    calls = _Calls()
    p.providers.register("mock", lambda model=None: _adapter("mock", calls, model))
    if register_target:
        p.providers.register(TARGET, lambda model=None: _adapter(TARGET, calls, model))
    p.config.fleet_code_route_enabled = enabled
    p.config.fleet_code_target = f"{TARGET}:{TARGET_MODEL}"
    p.config.fleet_code_task_classes = ""
    return p, calls


def _run(p, **kw):
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("write a short report on the folder", **kw)
        try:
            await orch.run_session(s.id)
        except Exception:  # noqa: BLE001 — a refused run raises after it is settled
            pass
        return orch.get_session(s.id)

    return asyncio.run(go())


def test_a_builder_with_no_model_of_its_own_runs_on_the_target(tmp_path):
    p, calls = _platform(tmp_path, enabled=True)
    s = _run(p, agent_type=AgentType.BUILDER)
    assert calls.seen and {c[0] for c in calls.seen} == {TARGET}, calls.seen
    assert calls.seen[0] == (TARGET, TARGET_MODEL), calls.seen
    assert (s.provider, s.model) == (TARGET, TARGET_MODEL)
    assert s.status == SessionStatus.COMPLETED, s.summary
    assert f'"code_route": "{TARGET}:{TARGET_MODEL}"' in (s.options_json or ""), s.options_json


def test_the_same_run_with_the_setting_off_uses_the_default(tmp_path):
    p, calls = _platform(tmp_path, enabled=False)
    s = _run(p, agent_type=AgentType.BUILDER)
    assert calls.seen and {c[0] for c in calls.seen} == {"mock"}, calls.seen
    assert s.provider == "mock"
    assert "code_route" not in (s.options_json or "")


def test_a_builder_whose_definition_names_a_model_keeps_it(tmp_path):
    """A custom agent's own model reaches create_session as the caller's
    provider/model (the spawn, delegate and assignment doors pass it)."""
    p, calls = _platform(tmp_path, enabled=True)
    s = _run(p, agent_type=AgentType.BUILDER, provider="mock", model="mock-1")
    assert calls.seen and {c[0] for c in calls.seen} == {"mock"}, calls.seen
    assert (s.provider, s.model) == ("mock", "mock-1")


def test_a_kind_of_agent_outside_the_set_is_untouched(tmp_path):
    p, calls = _platform(tmp_path, enabled=True)
    s = _run(p, agent_type=AgentType.RESEARCHER)
    assert {c[0] for c in calls.seen} == {"mock"}, calls.seen
    assert s.provider == "mock"


def test_a_chat_turn_is_untouched(tmp_path):
    p, calls = _platform(tmp_path, enabled=True)

    async def go():
        return await p.router.complete(
            system="be brief", messages=[LLMMessage(role="user", content="hello")],
            tools=[], task_class="chat",
        )

    route = asyncio.run(go())
    assert route.provider == "mock"
    assert {c[0] for c in calls.seen} == {"mock"}, calls.seen


def test_the_target_down_refuses_by_name_and_calls_nothing_else(tmp_path):
    p, calls = _platform(tmp_path, enabled=True, register_target=False)
    s = _run(p, agent_type=AgentType.BUILDER)
    assert calls.seen == [], calls.seen  # not the default, not any other provider
    assert (s.provider, s.model) == (TARGET, TARGET_MODEL)
    assert s.status == SessionStatus.FAILED
    summary = s.summary or ""
    assert f"Coding work is set to go to {TARGET} ({TARGET_MODEL})" in summary, summary
    assert "isn't available right now" in summary, summary
    assert CODE_ROUTE_PLACE in summary and "Model servers page" in summary, summary


def test_the_router_names_the_setting_only_for_the_coding_call(tmp_path):
    p, _calls = _platform(tmp_path, enabled=True, register_target=False)

    async def call(task_class):
        return await p.router.complete(
            provider=TARGET, model=TARGET_MODEL, system="s",
            messages=[LLMMessage(role="user", content="go")], tools=[],
            task_class=task_class,
        )

    with pytest.raises(CodeRouteUnavailable) as coding:
        asyncio.run(call("builder"))
    assert coding.value.failed_provider == TARGET
    assert getattr(coding.value, "no_failover", False) is True
    # Picked by name for anything else: the plain refusal, still by name.
    with pytest.raises(Exception) as other:
        asyncio.run(call("researcher"))
    assert not isinstance(other.value, CodeRouteUnavailable)
    assert "Coding work is set to go" not in str(other.value)


# --------------------------------------------------------------------------- #
# 3. The page says what happens.
# --------------------------------------------------------------------------- #
class _Reg:
    def __init__(self, routable: list[str]) -> None:
        self._nodes = [SimpleNamespace(id=n, label="", tool_use=True) for n in routable]

    def routable_nodes(self):
        return list(self._nodes)

    def nodes(self):
        return list(self._nodes)


class _Sampler:
    def touch(self) -> None:
        pass


def _view(tmp_path, *, enabled=True, target=f"{TARGET}:{TARGET_MODEL}", classes="", up=True):
    p = build_platform(str(tmp_path))
    p.providers._dynamic_available = lambda _name: None
    if up:
        p.providers.register(TARGET, lambda model=None: MockLLMAdapter())
    p.config.fleet_code_route_enabled = enabled
    p.config.fleet_code_target = target
    p.config.fleet_code_task_classes = classes
    d = types.SimpleNamespace(platform=p, fleet=_Reg(["box"]), fleet_sampler=_Sampler())
    app = FastAPI()
    fleet_routes.register(app, d)
    return TestClient(app).get("/fleet/code-route").json(), p


def test_the_page_says_which_agents_use_the_server(tmp_path):
    out, p = _view(tmp_path)
    assert out["in_effect"] is True
    assert out["task_classes"] == code_task_classes(p.config) == list(CODE_TASK_CLASSES)
    assert out["effective"]["why"] == (
        f"Builder, maintainer and reviewer agents with no model of their own use {TARGET} ({TARGET_MODEL})."
    )


def test_the_page_says_a_server_that_is_not_available_refuses_those_runs(tmp_path):
    out, _p = _view(tmp_path, up=False)
    why = out["effective"]["why"]
    assert out["in_effect"] is True and out["effective"]["available"] is False
    assert "not currently available" in why and "refused" in why and CODE_ROUTE_PLACE in why, why


@pytest.mark.parametrize(
    "target, words",
    [
        ("anthropic:claude-opus-4-8", "anthropic is not one of your model servers"),
        ("", "no model server is chosen for coding work"),
        (":coder", "is not written as server:model"),
    ],
)
def test_the_page_says_not_in_effect_and_why(tmp_path, target, words):
    out, _p = _view(tmp_path, target=target)
    why = out["effective"]["why"]
    assert out["in_effect"] is False
    assert why.startswith("Not in effect:") and words in why, why
    assert "Coding work goes to" not in why


def test_the_page_shows_the_kinds_the_router_uses(tmp_path):
    out, p = _view(tmp_path, classes="builder, nonsense")
    assert out["task_classes"] == ["builder"] == code_task_classes(p.config)
    assert out["effective"]["why"].startswith("Builder agents with no model of their own use")
