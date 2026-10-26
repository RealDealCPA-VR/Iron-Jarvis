"""v1.394.0 (the long-jobs plan's Phase 5, decision 5): ``delegate`` and
``spawn_agent`` honour a team member's model THE SAME WAY, and a member's run
is HELD to it.

Before this release ``spawn_agent`` ignored a custom agent's pinned model (it
ran every custom agent on the parent's), and ``delegate`` handed an agent
pinned to a PROVIDER the parent's model id (another provider's). In a coding
session every custom agent pinned elsewhere was refused as a helper. Now:

* both doors run a pinned agent on its own provider and its own model (that
  provider's default when it names none) — never the parent's model id;
* a TEAM MEMBER's run is held to its model (``providers.hold``): its
  provider's own error, never a fallback or a failover — in a coding session
  (``helpers.pinned``) and outside one (``model_team.member_scope``);
* in a coding session a member is a helper ON ITS OWN MODEL and its own
  account (or its provider's normal pick — never the session's pin); a
  non-member pinned elsewhere and a remote agent are refused as before; under
  Local only a member whose model leaves the network is refused in words, by
  both doors, inside a coding session or not.

Every model call goes through the REAL router to recording adapter doubles,
so "it ran on X" is the router's own record (``provider.routed``) and the
adapters' — never a stubbed router.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents import model_team as T
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType
from iron_jarvis.harness import helpers as H
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import egress
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError
from iron_jarvis.tools.base import ToolContext


class Rec(LLMAdapter):
    """A recording adapter: who was called, with which model, under which
    account pin and model hold. ``fail`` makes every call a TRANSIENT error
    (an overload) — the kind a router would fail over from."""

    def __init__(self, provider: str, model: str | None, log: list, fail: set) -> None:
        self.provider = provider
        self.model = model or "default"
        self.log = log
        self.fail = fail

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    def _note(self) -> None:
        hold = HOLD.current_hold()
        self.log.append({
            "provider": self.provider,
            "model": self.model,
            "pin": P.current(),
            "hold": (hold.provider, hold.model) if hold is not None else None,
        })
        if self.provider in self.fail:
            raise ProviderError(f"{self.provider}: overloaded", status_code=529, transient=True)

    async def complete(self, *, system, messages, tools, **kw):
        self._note()
        return LLMResponse(text=f"{self.provider} did it.", usage={"input_tokens": 2, "output_tokens": 1})

    async def stream(self, *, system, messages, tools, **kw):
        self._note()
        resp = LLMResponse(text=f"{self.provider} did it.", usage={"input_tokens": 2, "output_tokens": 1})
        yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


PROVIDERS = ("google", "claude-cli", "codex-cli", "openai")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path))
    log: list[dict] = []
    fail: set[str] = set()
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Rec(n, model, log, fail))
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: True if name in PROVIDERS else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    # The user's default is a real cloud model, and the strict pin is OFF:
    # only a HOLD can keep a failing pick where it is.
    p.config.default_provider = "google"
    p.config.default_model = "gemini-3.8-flash"
    return p, log, fail, _Routed(p)


class _Routed:
    """The router's own record: every ``provider.routed`` it published."""

    def __init__(self, p) -> None:
        self.p = p

    def __iter__(self):
        return iter([e for e in self.p.event_bus.history if e.type == EventType.PROVIDER_ROUTED])


async def _parent(p) -> str:
    s = await Orchestrator(p).create_session(
        "the parent's task", AgentType.SUPERVISOR, provider="google", model="gemini-3.8-flash"
    )
    return s.id


def _ctx(p, sid: str, tmp_path) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id=sid, agent_run_id="parent-run",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


def _child_rows(p, parent_sid: str):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session

    with session_scope(p.engine) as db:
        rows = [s for s in db.exec(select(Session)) if s.id != parent_sid]
        for r in rows:
            db.expunge(r)
        return rows


def _member(p, name="codex-coder", provider="codex-cli", model="", roles=("code",), **kw):
    return p.agents_registry.register(name, "You code.", [], provider=provider, model=model,
                                      roles=list(roles), **kw)


# --------------------------------------------------------------------------- #
# 1. both doors honour the pin the same way
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_both_doors_run_a_pinned_agent_on_its_own_provider_and_its_own_model(world, tmp_path, door):
    p, log, _fail, events = world
    _member(p, roles=())  # a plain custom agent pinned to Codex, no roles
    sid = await _parent(p)
    args = {"agent_type": "custom:codex-coder", "task": "t"} if door == "delegate" else {
        "agent": "codex-coder", "task": "t"}
    res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    (kid,) = _child_rows(p, sid)
    # Its own provider, and that provider's own default model — never the
    # parent's "gemini-3.8-flash" (another provider's model id), and never the
    # configured default model (the default provider's).
    assert kid.provider == "codex-cli"
    assert kid.model == "default"  # the double's own default model
    assert {c["provider"] for c in log} == {"codex-cli"}
    routed = {e.payload["resolved_provider"] for e in events if e.session_id == kid.id}
    assert routed == {"codex-cli"}


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_an_unpinned_agent_still_inherits_the_parents_model(world, tmp_path, door):
    p, log, _fail, _events = world
    p.agents_registry.register("plain", "p", [], roles=["read"])
    sid = await _parent(p)
    args = {"agent_type": "custom:plain", "task": "t"} if door == "delegate" else {"agent": "plain", "task": "t"}
    res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    (kid,) = _child_rows(p, sid)
    assert (kid.provider, kid.model) == ("google", "gemini-3.8-flash")


# --------------------------------------------------------------------------- #
# 2. a member's run is held to its model (outside a coding session too)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_members_failing_model_is_never_swapped_for_another(world, tmp_path, door):
    p, log, fail, events = world
    _member(p)
    fail.add("codex-cli")
    sid = await _parent(p)
    args = {"agent_type": "custom:codex-coder", "task": "t"} if door == "delegate" else {
        "agent": "codex-coder", "task": "t"}
    res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    assert not res.ok
    assert log and {c["provider"] for c in log} == {"codex-cli"}, log
    assert all(c["hold"] == ("codex-cli", "default") for c in log)
    (kid,) = _child_rows(p, sid)
    assert {e.payload["resolved_provider"] for e in events if e.session_id == kid.id} <= {"codex-cli"}


async def test_the_control_a_non_member_pinned_agent_still_fails_over_as_before(world, tmp_path):
    """Nothing changed for an agent that is not on the team: a failing pick
    falls back to the default model exactly as it did (the user's own
    strict-pin setting is what holds those)."""
    p, log, fail, _events = world
    _member(p, roles=())
    fail.add("codex-cli")
    sid = await _parent(p)
    res = await p.registry.get("delegate").execute(
        {"agent_type": "custom:codex-coder", "task": "t"}, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    assert "google" in {c["provider"] for c in log}
    assert all(c["hold"] is None for c in log)


async def test_a_members_own_account_pin_rides_its_run_and_no_pin_means_the_normal_pick(world, tmp_path, monkeypatch):
    from iron_jarvis.harness import build_accounts as BA

    p, log, _fail, _events = world
    monkeypatch.setattr(BA, "find", lambda svc, account: {"provider": "codex-cli", "name": "Codex Plus"}
                        if account == "x-plus" else None)
    _member(p, name="cx-own", account="x-plus")
    _member(p, name="cx-pick")
    sid = await _parent(p)
    session_pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    token = P.set_pin(session_pin)
    try:
        r1 = await p.registry.get("delegate").execute({"agent_type": "custom:cx-own", "task": "t"},
                                                      _ctx(p, sid, tmp_path))
        n1 = len(log)
        r2 = await p.registry.get("spawn_agent").execute({"agent": "cx-pick", "task": "t"},
                                                         _ctx(p, sid, tmp_path))
    finally:
        P.reset_pin(token)
    assert r1.ok and r2.ok
    own, pick = log[:n1], log[n1:]
    assert own and all(c["pin"] is not None and c["pin"].profile_id == "x-plus" for c in own)
    # No account named: its provider's normal pick — never the caller's pin.
    assert pick and all(c["pin"] is None for c in pick)


# --------------------------------------------------------------------------- #
# 3. Local only, outside a coding session
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_local_only_run_refuses_a_cloud_member_in_words_before_any_session(world, tmp_path, door):
    p, log, _fail, _events = world
    _member(p)
    sid = await _parent(p)
    args = {"agent_type": "custom:codex-coder", "task": "t"} if door == "delegate" else {
        "agent": "codex-coder", "task": "t"}
    with egress.local_only_scope(True):
        res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    assert not res.ok
    assert "this work is Local only" in res.error and "codex-coder" in res.error
    assert _child_rows(p, sid) == [] and log == []


# --------------------------------------------------------------------------- #
# 4. a coding session's helper (the harness parent)
# --------------------------------------------------------------------------- #


def _parent_for_turn(work, *, pin=None, local_only=False) -> H.HelperParent:
    return H.HelperParent(session_id="build:t1", folder=str(work), provider="claude-cli",
                          model="claude-sonnet-4-5", pin=pin, local_only=local_only)


def test_target_refusal_lets_a_member_through_and_keeps_every_other_refusal(world, tmp_path):
    p, *_ = world
    parent = _parent_for_turn(tmp_path)
    member = _member(p)
    plain = p.agents_registry.register("scout", "s", [], provider="openai", model="gpt-5.1")
    assert H.target_refusal(parent, raw="custom:codex-coder", record=member, platform=p) == ""
    # A non-member pinned elsewhere: the v1.372.0 words, unchanged.
    words = H.target_refusal(parent, raw="custom:scout", record=plain, platform=p)
    assert "session's own model" in words and "openai/gpt-5.1" in words
    assert "another computer" in H.target_refusal(parent, raw="remote:hermes")
    # Local only: a member whose model leaves the network is refused in words.
    lo = _parent_for_turn(tmp_path, local_only=True)
    assert "this work is Local only" in H.target_refusal(lo, raw="custom:codex-coder", record=member, platform=p)


def test_for_member_swaps_the_model_and_the_account_and_nothing_else(world, tmp_path):
    p, *_ = world
    member = _member(p)
    plain = p.agents_registry.register("plain", "p", [], roles=["read"])  # no model of its own
    session_pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli")
    parent = _parent_for_turn(tmp_path, pin=session_pin)
    got = H.for_member(parent, p, member)
    assert (got.provider, got.model, got.pin, got.own_model) == ("codex-cli", "default", None, True)
    assert (got.folder, got.session_id) == (parent.folder, parent.session_id)
    assert H.for_member(parent, p, plain) is parent
    assert H.for_member(None, p, member) is None


def test_pinned_never_leaves_the_sessions_pin_on_a_member_without_an_account(world, tmp_path):
    p, *_ = world
    session_pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli")
    member_parent = H.for_member(_parent_for_turn(tmp_path, pin=session_pin), p, _member(p))
    token = P.set_pin(session_pin)
    try:
        with H.pinned(member_parent):
            assert P.current() is None
            assert HOLD.current_hold() == HOLD.ProviderHold("codex-cli", "default")
        with H.pinned(_parent_for_turn(tmp_path, pin=session_pin)):
            assert P.current() is session_pin
            assert HOLD.current_hold() == HOLD.ProviderHold("claude-cli", "claude-sonnet-4-5")
    finally:
        P.reset_pin(token)


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_coding_sessions_helper_runs_on_its_members_model_and_never_the_sessions_pin(
        world, tmp_path, door):
    p, log, _fail, events = world
    _member(p)
    session_pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    parent = _parent_for_turn(tmp_path, pin=session_pin)
    ctx = ToolContext(workspace=tmp_path, session_id="build:t1", agent_run_id="build:t1",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    args = {"agent_type": "custom:codex-coder", "task": "t"} if door == "delegate" else {
        "agent": "codex-coder", "task": "t"}
    token = P.set_pin(session_pin)
    try:
        with H.helper_call(parent, "c1", door, args, None):
            res = await p.registry.get(door).execute(args, ctx)
    finally:
        P.reset_pin(token)
    assert res.ok, res.error
    assert log and all(c["provider"] == "codex-cli" and c["pin"] is None for c in log), log
    assert all(c["hold"] == ("codex-cli", "default") for c in log)
    (kid,) = _child_rows(p, "")
    assert kid.provider == "codex-cli"
    assert {e.payload["resolved_provider"] for e in events if e.session_id == kid.id} == {"codex-cli"}


async def test_a_coding_sessions_non_member_helper_still_runs_on_the_sessions_model(world, tmp_path):
    """The control: a builtin helper keeps the session's model and pin."""
    p, log, _fail, _events = world
    session_pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli")
    parent = _parent_for_turn(tmp_path, pin=session_pin)
    ctx = ToolContext(workspace=tmp_path, session_id="build:t1", agent_run_id="build:t1",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    with H.helper_call(parent, "c1", "delegate", {"task": "t"}, None):
        res = await p.registry.get("delegate").execute({"agent_type": "researcher", "task": "t"}, ctx)
    assert res.ok, res.error
    assert log and all(c["provider"] == "claude-cli" and c["pin"] is session_pin for c in log)


def test_a_member_that_inherits_its_tools_is_handed_the_turns_tools_like_its_base(world, tmp_path):
    p, *_ = world
    member = _member(p)
    plain = p.agents_registry.register("plain", "p", [], provider="codex-cli")  # not a member
    explicit = p.agents_registry.register("picky", "p", ["read_file"], provider="codex-cli", roles=["read"])
    assert T.helper_is_custom(member) is False
    assert T.helper_is_custom(plain) is True and T.helper_is_custom(explicit) is True
    parent = H.HelperParent(session_id="build:t1", folder=str(tmp_path), provider="codex-cli", model="",
                            tools=("read_file", "write_file", "apply_patch", "glob"))
    d = H.helper_definition(parent, p.agents_registry.definition("codex-coder"),
                            custom=T.helper_is_custom(member))
    assert set(d.tools) == {"read_file", "write_file", "apply_patch", "glob"}
