"""v1.394.0 review (the long-jobs plan's Phase 5): what the doer's pins did not
catch, each one RED on the doer's code first.

ACCOUNTS (decision 4 + decision 5, v1.393.0's groups). A coding session runs on
ONE account the user picked and never "first free, rotating"
(``routes/build.turn_account``: "that would land on an account nobody
picked"); work moves to another account only within a group the user made.
The doer let a team member with no account of its own take Iron-Proxy's
UNPINNED normal pick inside a coding session, so:

* a member of the SAME vendor as the session ran on another account than the
  one the session is on (a Claude member in a session on "Spare" leased
  "Work Max", the first free one);
* a member of another vendor ignored the session's group (a session on a work
  account handed its piece to whichever Codex account was first);
* and an unpinned pick ROTATES at a limit — a helper's conversation moving to
  another account by itself, outside any group.

Now a member with no account of its own runs on the session's account (same
vendor), else an account of the session's GROUP (refused in words when the
group has none of that vendor), else the vendor's first usable account —
PINNED, never rotating.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents import model_team as T
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers import router as router_mod
from tests.test_build_accounts_v1368 import enable, isolate, make_fake, picks
from tests.test_model_team_live_v1394 import (
    CLAUDE,
    World,
    agent_call,
    routed_by_session,
    run_turn,
    wire,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _session(app, work, **setup) -> str:
    base = {"provider": CLAUDE[0], "model": CLAUDE[1], "approval_mode": "yolo"}
    base.update(setup)
    return S.create(app.state.platform.engine, str(work), "", base).id


def _helper_calls(world, provider):
    return [c for c in world.calls if not c["main"] and c["provider"] == provider]


# --------------------------------------------------------------------------- #
# 1. the SAME vendor: the session's own account, never the first free one
# --------------------------------------------------------------------------- #


async def test_a_same_vendor_member_runs_on_the_sessions_own_account(app, fake, work, monkeypatch):
    """The session runs on "Spare" (the user's pick). A Claude Code member with
    no account of its own must lease "Spare" too — the doer's code took
    Iron-Proxy's normal pick, which is "Work Max" (the first free one)."""
    await enable(app)
    world = World([[agent_call("h-rev", "claude-reviewer", "Review app.py.")], "Done."])
    wire(app, monkeypatch, world)
    app.state.platform.agents_registry.register(
        "claude-reviewer", "You review.", [], provider="claude-cli", model="subscription",
        roles=["review"])
    sid = _session(app, work, account="c-spare", account_name="Spare")
    fake.queries.clear()
    await run_turn(app, sid, "Have it reviewed.", "rev-1")
    main = [c for c in world.calls if c["main"]]
    helper = _helper_calls(world, "claude-cli")
    assert main and {c["account"] for c in main} == {"c-spare"}
    assert helper, world.calls
    # The helper's own model calls went to the session's account, pinned.
    assert {c["account"] for c in helper} == {"c-spare"}, [c["account"] for c in helper]
    claude_picks = [q for q in picks(fake) if q["provider"] == "anthropic"]
    assert claude_picks and all(
        q.get("profileId") == "c-spare" and q.get("pinned") == "1" for q in claude_picks
    ), claude_picks


# --------------------------------------------------------------------------- #
# 2. another vendor: the session's GROUP, pinned; none in the group = refused
# --------------------------------------------------------------------------- #


async def test_another_vendors_member_stays_in_the_sessions_group(app, fake, work, monkeypatch):
    """The session runs on "Work Max", grouped "work" with "Codex Team". A
    Codex member with no account of its own must run on "Codex Team" — the
    doer's code took the normal pick, "Codex Plus" (first in order), an
    account outside the group (work content to another account)."""
    await enable(app)
    app.state.platform.config.account_groups = {"work": ["c-work", "x-team"]}
    world = World([[agent_call("h-code", "codex-coder", "Write util.py.")], "Done."])
    wire(app, monkeypatch, world)
    app.state.platform.agents_registry.register(
        "codex-coder", "You code.", [], provider="codex-cli", model="subscription", roles=["code"])
    sid = _session(app, work)
    fake.queries.clear()
    await run_turn(app, sid, "Write it.", "grp-1")
    coder = _helper_calls(world, "codex-cli")
    assert coder and {c["account"] for c in coder} == {"x-team"}, [c["account"] for c in coder]
    codex_picks = [q for q in picks(fake) if q["provider"] == "openai"]
    assert codex_picks and all(
        q.get("profileId") == "x-team" and q.get("pinned") == "1" for q in codex_picks
    ), codex_picks


async def test_a_group_with_no_account_of_the_members_vendor_refuses_before_any_session(
        app, fake, work, monkeypatch):
    await enable(app)
    app.state.platform.config.account_groups = {"work": ["c-work", "c-spare"]}
    world = World([[agent_call("h-code", "codex-coder", "Write util.py.")], "Done."])
    wire(app, monkeypatch, world)
    app.state.platform.agents_registry.register(
        "codex-coder", "You code.", [], provider="codex-cli", model="subscription", roles=["code"])
    sid = _session(app, work)
    frames = await run_turn(app, sid, "Write it.", "grp-2")
    ends = {d["id"]: d for e, d in frames if e == "helper" and d.get("ended")}
    assert ends["h-code"]["state"] == "failed"
    words = ends["h-code"]["result"]
    assert "'work'" in words and "Codex" in words and "Model team" in words, words
    assert not ends["h-code"].get("session")
    # Nothing reached Codex at all.
    assert not _helper_calls(world, "codex-cli")
    assert not [q for q in picks(fake) if q["provider"] == "openai"]
    assert "codex-cli" not in set().union(*routed_by_session(app).values())


async def test_an_ungrouped_member_of_another_vendor_is_pinned_and_never_rotates(
        app, fake, work, monkeypatch):
    """No groups: the member takes its vendor's first usable account — PINNED.
    An unpinned pick rotates to the next account at a limit (the helper's
    conversation moving by itself, outside any group)."""
    await enable(app)
    world = World([[agent_call("h-code", "codex-coder", "Write util.py.")], "Done."])
    wire(app, monkeypatch, world)
    app.state.platform.agents_registry.register(
        "codex-coder", "You code.", [], provider="codex-cli", model="subscription", roles=["code"])
    sid = _session(app, work)
    fake.queries.clear()
    await run_turn(app, sid, "Write it.", "nogrp-1")
    codex_picks = [q for q in picks(fake) if q["provider"] == "openai"]
    assert codex_picks and all(
        q.get("profileId") == "x-plus" and q.get("pinned") == "1" for q in codex_picks
    ), codex_picks


# --------------------------------------------------------------------------- #
# 3. the pure rule (no app): member_account
# --------------------------------------------------------------------------- #


class _Svc:
    enabled = True

    def __init__(self, profiles, states):
        self._cache = (profiles, states)

    def cached_accounts(self):
        return self._cache


def _profile(pid, provider, order):
    return {"id": pid, "provider": provider, "title": pid.upper(), "order": order, "lane": "cli"}


class _Platform:
    def __init__(self, groups=None):
        from types import SimpleNamespace

        profiles = [
            _profile("c-work", "anthropic", 1), _profile("c-spare", "anthropic", 2),
            _profile("x-plus", "openai", 3), _profile("x-team", "openai", 4),
        ]
        states = {p["id"]: {"status": "ready"} for p in profiles}
        self.iron_proxy = _Svc(profiles, states)
        self.config = SimpleNamespace(account_groups=groups or {}, default_provider="")
        self.providers = None


def _rec(provider="codex-cli", account=""):
    from types import SimpleNamespace

    return SimpleNamespace(name="m", provider=provider, model="subscription",
                           account=account, roles_json='["code"]')


def test_member_account_rule_table():
    p = _Platform()
    work = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    # Same vendor: THE SESSION'S OWN pin object (its facts land on it).
    pin, why = T.member_account(p, _rec("claude-cli"), work, coding=True)
    assert pin is work and why == ""
    # Explicit account: that one, whatever the session is on.
    pin, why = T.member_account(p, _rec("codex-cli", account="x-team"), work, coding=True)
    assert pin.profile_id == "x-team" and why == ""
    # Another vendor, no group: the vendor's first usable account, pinned.
    pin, why = T.member_account(p, _rec("codex-cli"), work, coding=True)
    assert pin.profile_id == "x-plus" and pin.ij_provider == "codex-cli" and why == ""
    # Another vendor, the session's group: the group's account of that vendor.
    g = _Platform({"work": ["c-work", "x-team"]})
    pin, why = T.member_account(g, _rec("codex-cli"), work, coding=True)
    assert pin.profile_id == "x-team" and why == ""
    # A group with none of that vendor: refused in words, no pin.
    n = _Platform({"work": ["c-work", "c-spare"]})
    pin, why = T.member_account(n, _rec("codex-cli"), work, coding=True)
    assert pin is None and "'work'" in why and "Codex" in why
    # Outside a coding session with no caller pin: the normal pick (None).
    assert T.member_account(p, _rec("codex-cli"), None, coding=False) == (None, "")
    # Not a subscription model: no account at all.
    assert T.member_account(p, _rec("ollama"), work, coding=True) == (None, "")


def test_for_member_carries_the_sessions_pin_for_the_same_vendor(tmp_path):
    p = _Platform()
    work = P.AccountPin(profile_id="c-work", ij_provider="claude-cli")
    parent = H.HelperParent(session_id="build:t1", folder=str(tmp_path), provider="claude-cli",
                            model="claude-sonnet-4-5", pin=work)
    got = H.for_member(parent, p, _rec("claude-cli"))
    assert got.pin is work and got.own_model is True
    assert H.target_refusal(parent, raw="custom:m", record=_rec("claude-cli"), platform=p) == ""
    grouped = _Platform({"work": ["c-work", "c-spare"]})
    words = H.target_refusal(parent, raw="custom:m", record=_rec("codex-cli"), platform=grouped)
    assert "'work'" in words


def test_the_planners_usage_is_the_account_the_member_would_run_on():
    """In a coding session on "c-spare" a Claude member with no account runs
    on c-spare — so the planner's usage words are c-spare's, never the first
    free account's (c-work, which is 90% used here)."""
    p = _Platform()
    p.iron_proxy._cache[1]["c-work"] = {"status": "ready", "windows": [
        {"kind": "5h", "usedPercent": 90, "windowMins": 300}]}
    p.config.default_provider = ""
    rec = _rec("claude-cli")
    # Outside any pinned work: the vendor's first usable account (c-work).
    assert T.member_facts(p, rec)["usage"]["account"] == "c-work"
    token = P.set_pin(P.AccountPin(profile_id="c-spare", ij_provider="claude-cli", title="SPARE"))
    try:
        facts = T.member_facts(p, rec)
    finally:
        P.reset_pin(token)
    assert facts["usage"]["account"] == "c-spare", facts["usage"]
    assert "90%" not in T.facts_words(p, facts)


# --------------------------------------------------------------------------- #
# 4. outside a coding session: a PINNED caller's account rule, refused at the door
# --------------------------------------------------------------------------- #


@pytest.fixture
def doors(tmp_path, monkeypatch):
    from tests.test_model_team_doors_v1394 import PROVIDERS, Rec

    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path / "doors"))
    log: list = []
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Rec(n, model, log, set()))
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: True if name in PROVIDERS else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    p.config.default_provider = "google"
    p.config.default_model = "gemini-3.8-flash"
    p.iron_proxy = _Platform().iron_proxy
    return p, log


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_pinned_callers_group_refuses_a_member_at_the_door_before_any_session(
        doors, tmp_path, door):
    from tests.test_model_team_doors_v1394 import _child_rows, _ctx, _parent

    p, log = doors
    p.config.account_groups = {"work": ["c-work", "c-spare"]}
    p.agents_registry.register("cx", "You code.", [], provider="codex-cli", model="subscription",
                               roles=["code"])
    sid = await _parent(p)
    args = {"agent_type": "custom:cx", "task": "t"} if door == "delegate" else {"agent": "cx", "task": "t"}
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="WORK"))
    try:
        res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    finally:
        P.reset_pin(token)
    assert not res.ok and "'work'" in res.error and "Codex" in res.error, res.error
    assert _child_rows(p, sid) == [] and log == []


@pytest.fixture
def failing(doors):
    """The doors world, where a provider in ``fail`` answers every call with a
    TRANSIENT overload (the kind a router fails over from)."""
    from tests.test_model_team_doors_v1394 import PROVIDERS, Rec

    p, log = doors
    fail: set = set()
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Rec(n, model, log, fail))
    return p, log, fail


# --------------------------------------------------------------------------- #
# 5. consult: the third door onto a teammate (it was not fixed with the two)
# --------------------------------------------------------------------------- #


async def test_consult_asks_a_provider_pinned_agent_on_that_providers_own_model(doors, tmp_path):
    """``consult`` handed an agent pinned to a PROVIDER the caller's model id
    (another provider's) — the very bug the doer fixed in delegate and spawn."""
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log = doors
    p.agents_registry.register("cx", "You code.", [], provider="codex-cli", model="")
    sid = await _parent(p)
    res = await p.registry.get("consult").execute(
        {"agent": "custom:cx", "question": "Is this right?"}, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    assert [(c["provider"], c["model"]) for c in log] == [("codex-cli", "default")], log


async def test_consult_holds_a_member_to_its_own_model(failing, tmp_path):
    """Decision 5: a member's model is held — a consult of a member whose
    model fails says so; it is never answered by another provider."""
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log, fail = failing
    p.agents_registry.register("cx", "You code.", [], provider="codex-cli", model="", roles=["review"])
    fail.add("codex-cli")
    sid = await _parent(p)
    from iron_jarvis.providers.adapters.base import ProviderError

    with pytest.raises(ProviderError):  # the registry turns it into a failed call
        await p.registry.get("consult").execute(
            {"agent": "custom:cx", "question": "Is this right?"}, _ctx(p, sid, tmp_path))
    assert log and {c["provider"] for c in log} == {"codex-cli"}, log


# --------------------------------------------------------------------------- #
# 6. a member's OWN hold never reaches the teammate it hands work to
# --------------------------------------------------------------------------- #


async def test_a_members_run_can_consult_a_teammate_on_another_model(doors, tmp_path):
    """Inside a member's run (outside a coding session) its model is held. A
    consult of a teammate pinned to another model was refused by that hold,
    in a coding session's words ("This Build session runs on codex-cli") —
    though no coding session was involved. The teammate answers on its own
    model, as for any other caller."""
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log = doors
    member = p.agents_registry.register("cx", "You code.", [], provider="codex-cli", roles=["code"])
    p.agents_registry.register("gm", "You check.", [], provider="google", model="gemini-3.8-flash")
    sid = await _parent(p)
    with T.member_scope(p, member):
        res = await p.registry.get("consult").execute(
            {"agent": "custom:gm", "question": "Is this right?"}, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    assert [c["provider"] for c in log] == ["google"]
    assert log[0]["hold"] is None


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_members_run_can_hand_work_to_a_teammate_on_another_model(doors, tmp_path, door):
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log = doors
    member = p.agents_registry.register("cx", "You code.", [], provider="codex-cli", roles=["code"])
    p.agents_registry.register("gm", "You check.", [], provider="google", model="gemini-3.8-flash")
    sid = await _parent(p)
    args = {"agent_type": "custom:gm", "task": "t"} if door == "delegate" else {"agent": "gm", "task": "t"}
    with T.member_scope(p, member):
        res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    assert res.ok, res.error
    assert log and {c["provider"] for c in log} == {"google"}, log
    assert all(c["hold"] is None for c in log)


async def test_a_coding_sessions_hold_is_never_lifted_for_a_teammate(doors, tmp_path):
    """The control: a coding session's own hold (not a member's) stays — a
    consult naming another provider there is still refused."""
    from iron_jarvis.providers import hold as HOLD
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log = doors
    p.agents_registry.register("gm", "You check.", [], provider="google", model="gemini-3.8-flash")
    sid = await _parent(p)
    token = HOLD.set_hold("codex-cli", "default")
    try:
        with pytest.raises(HOLD.HeldProviderRefused):
            await p.registry.get("consult").execute(
                {"agent": "custom:gm", "question": "Is this right?"}, _ctx(p, sid, tmp_path))
    finally:
        HOLD.reset_hold(token)
    assert log == []


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_same_vendor_member_takes_the_pinned_callers_own_account(doors, tmp_path, door):
    from tests.test_model_team_doors_v1394 import _ctx, _parent

    p, log = doors
    p.agents_registry.register("cl", "You review.", [], provider="claude-cli", model="subscription",
                               roles=["review"])
    sid = await _parent(p)
    args = {"agent_type": "custom:cl", "task": "t"} if door == "delegate" else {"agent": "cl", "task": "t"}
    spare = P.AccountPin(profile_id="c-spare", ij_provider="claude-cli", title="SPARE")
    token = P.set_pin(spare)
    try:
        res = await p.registry.get(door).execute(args, _ctx(p, sid, tmp_path))
    finally:
        P.reset_pin(token)
    assert res.ok, res.error
    assert log and all(c["provider"] == "claude-cli" and c["pin"] is spare for c in log), log


# --------------------------------------------------------------------------- #
# 7. Make a starter team: what it offers
# --------------------------------------------------------------------------- #


def _local_rows(*models):
    return [{"provider": "ollama", "model": m, "available": True, "on_this_network": True} for m in models]


def test_the_starter_never_offers_an_embedding_model_as_a_member():
    """Ollama lists every model it holds — an embedding model (this app's own
    embedder runs on one) cannot chat, so it is never a team member."""
    p = _Platform()
    catalog = _local_rows("qwen3:8b", "nomic-embed-text:latest", "mxbai-embed-large", "bge-m3")
    rows = T.starter_preview(p, catalog)["rows"]
    assert [r["model"] for r in rows] == ["qwen3:8b"], [r["model"] for r in rows]


def test_a_fleet_with_many_local_models_ticks_only_a_few_by_default():
    p = _Platform()
    models = [f"model-{i}:7b" for i in range(12)]
    rows = T.starter_preview(p, _local_rows(*models))["rows"]
    assert len(rows) == 12  # every one is offered…
    ticked = [r["model"] for r in rows if r["ticked"]]
    assert ticked == models[: T.STARTER_TICKED_LOCAL]  # …only the first few ticked
    assert 1 <= T.STARTER_TICKED_LOCAL <= 3


# --------------------------------------------------------------------------- #
# 8. the routes: the demo model is never a member; a same-vendor model change
# --------------------------------------------------------------------------- #


@pytest.fixture
def plain_app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "plain-home"))


async def test_the_demo_model_is_never_a_team_member(plain_app):
    from tests.test_build_sessions_v1367 import asgi

    status, body = await asgi(plain_app, "POST", "/agents", {
        "name": "fake", "system_prompt": "x", "provider": "mock", "roles": ["read"]})
    assert status == 422 and "demo model" in body["detail"], body
    status, body = await asgi(plain_app, "POST", "/agents", {
        "name": "fake", "system_prompt": "x", "provider": "mock"})
    assert status == 200, body
    status, body = await asgi(plain_app, "PATCH", "/agents/fake", {"roles": ["read"]})
    assert status == 422 and "demo model" in body["detail"], body
    status, body = await asgi(plain_app, "POST", "/agents", {
        "name": "real", "system_prompt": "x", "provider": "ollama", "model": "qwen3:8b", "roles": ["read"]})
    assert status == 200, body
    status, body = await asgi(plain_app, "PATCH", "/agents/real", {"provider": "mock", "model": ""})
    assert status == 422 and "demo model" in body["detail"], body
    assert T.roles_of(plain_app.state.platform.agents_registry.get("fake")) == []


async def test_a_members_model_changes_within_its_vendor_while_iron_proxy_is_off(plain_app):
    """A Codex member with an account of its own moves to another Codex model:
    the account still applies (same vendor), so nothing is refused — the
    doer's check refused EVERY provider change while Iron-Proxy was off or
    not read yet, in words that named the API ("account: \\"\\"")."""
    from tests.test_build_sessions_v1367 import asgi

    reg = plain_app.state.platform.agents_registry
    reg.register("cx", "x", [], provider="codex-cli", model="subscription", roles=["code"], account="x-plus")
    status, body = await asgi(plain_app, "PATCH", "/agents/cx", {"provider": "codex-cli", "model": "gpt-5.5"})
    assert status == 200, body
    assert (body["provider"], body["model"], body["account"]) == ("codex-cli", "gpt-5.5", "x-plus")
    # Another vendor's model with the account kept: refused in plain words.
    status, body = await asgi(plain_app, "PATCH", "/agents/cx", {"provider": "claude-cli", "model": ""})
    assert status == 422, body
    assert "account: \\\"\\\"" not in body["detail"] and 'account: ""' not in body["detail"]
    assert "No account of its own" in body["detail"] and "Model team" in body["detail"]


async def test_get_team_is_a_status_view_that_starts_no_cli_sign_in_check(plain_app, monkeypatch):
    """Settings › Model team says whether each member's model is connected.
    Like /health (v1.333.0) it reads CLI sign-in verdicts through
    ``cli_auth.cached_verdicts`` — a status view never starts a
    ``claude auth status`` refresh of its own."""
    from iron_jarvis.providers import cli_auth
    from tests.test_build_sessions_v1367 import asgi

    seen: list[bool] = []
    mgr = plain_app.state.platform.providers
    monkeypatch.setattr(mgr, "available", lambda name: seen.append(cli_auth._CACHED_ONLY.get()) or True)
    plain_app.state.platform.agents_registry.register(
        "cl", "x", [], provider="claude-cli", model="subscription", roles=["review"])
    status, body = await asgi(plain_app, "GET", "/team")
    assert status == 200 and body["members"][0]["available"] is True
    assert seen and all(seen), seen


# --------------------------------------------------------------------------- #
# 9. the planner's line: a long name is never cut
# --------------------------------------------------------------------------- #


def test_a_long_member_name_is_never_cut_when_its_facts_are_long():
    from iron_jarvis.agents.roster import RosterEntry, _block_line

    name = "custom:" + "quarterly-close-reconciliation-reader-for-clients"
    entry = RosterEntry(name, "dynamic", "reads the ledgers and the bank feeds", True, True,
                        {"sessions": 23, "success_rate": 0.87}, team={
                            "roles": ["read", "code", "review", "test"], "provider": "ollama",
                            "model": "qwen3-coder-30b-a3b-instruct-q4_k_m-128k-context-long",
                            "place": "network", "cost": "free", "usage": None, "account": ""})
    line = _block_line(entry, None)
    assert line.startswith(name + " ") or line.startswith(name + " —"), line
    assert line.endswith("(87% over 23 runs)")
