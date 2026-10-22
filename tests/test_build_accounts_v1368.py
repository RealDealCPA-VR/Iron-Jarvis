"""v1.368.0 (IJ harness Phase 5): a Build session runs on ONE account.

Driven through the REAL app factory and routes against the fake Iron-Proxy
(``tests/fixtures/fake_iron_proxy_v1301.py``, extended with Iron-Proxy
1b1ea71's pinned pick and plan/usage), through the real ``IronProxyService``
and ``IronProxyClient`` — never the user's Iron-Proxy (every test has its own
``IRON_PROXY_DATA_DIR``; conftest gives each worker one too).

This file: the pinned LEASE (``iron_proxy.accounts`` under an
``AccountPin``), ``GET /build/accounts`` (groups, Default, plan, ONE status),
"Make default", and a session's account at create / PATCH. The turns, the
limit card, "Resume when ready" and "Choose account" are in
``test_build_account_turns_v1368.py``; the vendors' usage reports in
``test_build_account_usage_v1368.py``. Helpers here are imported by both.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.iron_proxy.client import IronProxyClient
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError, ToolCall
from tests.test_build_sessions_v1367 import asgi, setup_of

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import (  # noqa: E402
    FEATURES_PHASE5,
    FEATURES_PICK_PROFILE,
    FakeIronProxy,
)

CLAUDE_CLI = ("claude-cli", "claude-sonnet-4-5")


# --------------------------------------------------------------------------- #
# harness (imported by the other v1368 files)
# --------------------------------------------------------------------------- #


def iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


def isolate(tmp_path, monkeypatch):
    """Our OWN Iron-Proxy data dir (the fake writes proxy.json there), no
    bundle to start, no inherited homes or keys, fresh throttles. (A
    generator the other v1368 files' own autouse fixtures delegate to.)"""
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
              "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(BA, "_PLAN_ASKED", {})
    monkeypatch.setattr(accounts, "_NOTED", set())
    previous = accounts.set_client_source(None)
    yield
    accounts.set_client_source(previous)


def make_fake(tmp_path: Path, features=FEATURES_PHASE5) -> FakeIronProxy:
    """Three Claude Code accounts and two Codex accounts, in this order:
    c-work (Max, 38% of 7 days, 12% of 5 hours), c-spare (no plan known, no
    windows), c-gone (needs sign-in); x-plus (Plus, 61% primary), x-team."""
    fake = FakeIronProxy(tmp_path / "ipd", features=list(features) if features is not None else None)
    for pid, provider, title in (
        ("c-work", "anthropic", "Work Max"),
        ("c-spare", "anthropic", "Spare"),
        ("c-gone", "anthropic", "Old login"),
        ("x-plus", "openai", "Codex Plus"),
        ("x-team", "openai", "Codex Team"),
    ):
        fake.add_profile(provider, title, id=pid, home=str(tmp_path / "homes" / pid))
    fake.states["c-work"].update({
        "plan": "max", "planSource": "cli-status",
        "windows": [
            {"kind": "5h", "usedPercent": 12, "windowMins": 300, "resetsAt": iso(3600), "source": "headers"},
            {"kind": "7d", "usedPercent": 38, "windowMins": 10080, "resetsAt": iso(86400), "source": "headers"},
        ],
    })
    fake.states["c-gone"]["status"] = "unauthenticated"
    fake.states["x-plus"].update({
        "plan": "plus", "planSource": "rate-limits",
        "windows": [{"kind": "primary", "usedPercent": 61, "windowMins": 300, "resetsAt": iso(1200),
                     "source": "rate-limits"}],
    })
    return fake.start()


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def enable(app) -> dict:
    status, body = await asgi(app, "POST", "/iron-proxy/enable")
    assert status == 200, body
    assert body["status"]["running"] is True, body
    return body


def picks(fake: FakeIronProxy) -> list[dict[str, str]]:
    """Every ``/iron/pick`` the fake answered, as ``{provider, profileId?,
    pinned?}``."""
    out = []
    for path, query in fake.queries:
        if path == "/iron/pick":
            out.append({k: v[0] for k, v in query.items()})
    return out


def bodies(fake: FakeIronProxy, action: str) -> list[tuple[str, dict]]:
    """``(account id, body)`` of every ``/signal`` or ``/finished`` report."""
    out = []
    for method, path, body in fake.calls:
        parts = path.strip("/").split("/")
        if method == "POST" and len(parts) == 4 and parts[1] == "profiles" and parts[3] == action:
            out.append((parts[2], body))
    return out


class Behaviour:
    """What the doubles below do: ``limit_on`` = accounts whose NEXT call hits
    a 429 (Iron-Proxy is told; under a pin it parks, never rotates);
    ``moves`` = scripted answers for tool-carrying calls (a list of
    ToolCalls, or text), else "Done."."""

    def __init__(self, moves: list | None = None) -> None:
        self.moves = list(moves or [])
        self.limit_on: set[str] = set()
        self.leases: list[tuple[str, str | None]] = []
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.rate_limits: dict[str, Any] | None = None
        self.drop_thinking = False
        self.pins: list[Any] = []
        #: Every provider the router CALLED (before any lease).
        self.attempts: list[str] = []
        #: provider -> how many next calls fail as an overload (529).
        self.overload: dict[str, int] = {}


class AccountDouble(LLMAdapter):
    """A subscription-CLI adapter double that does what the REAL ones do with
    accounts — the SAME ``iron_proxy.accounts`` calls (lease off the loop,
    ``retry_lease`` on a 429, ``report_success_async``) — without a CLI."""

    def __init__(self, provider: str, model: str | None, beh: Behaviour) -> None:
        self.provider = provider
        self.model = model or "subscription"
        self.beh = beh

    async def complete(self, *, system, messages, tools, **kw):
        final = None
        async for frame in self.stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                final = frame["response"]
        return final

    async def stream(self, *, system, messages, tools, **kw):
        self.beh.attempts.append(self.provider)
        if self.beh.overload.get(self.provider):
            self.beh.overload[self.provider] -= 1
            # The PROVIDER is busy (not one account): transient, no lease.
            raise ProviderError(f"{self.provider}: overloaded", status_code=529)
        lease = await asyncio.to_thread(accounts.lease, self.provider)
        pid = lease.profile_id if lease is not None else None
        self.beh.leases.append((self.provider, pid))
        self.beh.pins.append(P.current())
        self.beh.calls.append({
            "provider": self.provider, "account": pid, "model": self.model,
            "messages": list(messages), "tools": [t["name"] for t in tools or []],
        })
        limit_now = bool(self.beh.moves and tools and self.beh.moves[0] == "LIMIT")
        if limit_now:
            self.beh.moves.pop(0)
        if pid is not None and (limit_now or pid in self.beh.limit_on):
            self.beh.limit_on.discard(pid)
            cause = ProviderError(f"{self.provider}: Anthropic is rate-limiting this account", status_code=429)
            await accounts.retry_lease(lease, cause, {"status": 429, "text": "You've hit your limit"}, [])
            raise AssertionError("retry_lease returned under a pin")
        if self.beh.drop_thinking:
            from iron_jarvis.providers.adapters.subprocess_cli import note_thinking_dropped

            note_thinking_dropped()
        move = self.beh.moves.pop(0) if (self.beh.moves and tools) else "Done."
        if move == "FAIL":
            # Not an account problem: a permanent refusal of THIS request.
            raise ProviderError(f"{self.provider}: the request was refused (HTTP 400)", status_code=400)
        usage = {"input_tokens": 3, "output_tokens": 1}
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage=usage)
        else:
            resp = LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage)
        await accounts.report_success_async(
            lease, usage, 5, self.model,
            headers=self.beh.headers or None, rate_limits=self.beh.rate_limits,
        )
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire_doubles(app, monkeypatch, beh: Behaviour) -> Behaviour:
    """``claude-cli`` and ``codex-cli`` on the REAL router are the doubles."""
    mgr = app.state.platform.providers
    mgr.register("claude-cli", lambda model=None: AccountDouble("claude-cli", model, beh))
    mgr.register("codex-cli", lambda model=None: AccountDouble("codex-cli", model, beh))
    real = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: True if name in ("claude-cli", "codex-cli") else real(name))
    return beh


def write_call(cid: str, name: str, text: str) -> ToolCall:
    return ToolCall(id=cid, name="Write", arguments={"file_path": name, "content": text})


# --------------------------------------------------------------------------- #
# 1. the pinned lease
# --------------------------------------------------------------------------- #


def _client_for(fake: FakeIronProxy) -> IronProxyClient:
    c = IronProxyClient(fake.url, fake.token)
    accounts.set_client_source(lambda: c)
    return c


def test_a_pinned_lease_is_a_pinned_pick_of_that_one_account(tmp_path, fake):
    _client_for(fake)
    token = P.set_pin(P.AccountPin(profile_id="c-spare", ij_provider="claude-cli", title="Spare"))
    try:
        got = accounts.lease("claude-cli")
    finally:
        P.reset_pin(token)
    assert got is not None and got.profile_id == "c-spare"
    assert picks(fake)[-1] == {"provider": "anthropic", "lane": "cli", "profileId": "c-spare", "pinned": "1"}
    # Without a pin: today's first-free pick, no id, no pin (chat unchanged).
    accounts.lease("claude-cli")
    assert picks(fake)[-1] == {"provider": "anthropic", "lane": "cli"}


def test_a_parked_pinned_account_is_refused_by_kind_with_its_real_reset_never_another(tmp_path, fake):
    _client_for(fake)
    until = iso(5400)
    fake.states["c-work"].update({"status": "parked", "parkedUntil": until,
                                  "parkedReason": {"kind": "quota-exhausted"}})
    pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    token = P.set_pin(pin)
    try:
        with pytest.raises(ProviderError) as info:
            accounts.lease("claude-cli")
    finally:
        P.reset_pin(token)
    err = info.value
    assert err.no_failover is True and err.transient is False
    assert err.retry_after and 5000 < err.retry_after <= 5400
    words = str(err)
    assert 'The Claude account "Work Max" is at its usage limit until' in words
    assert "choose another account for this session" in words
    # Iron-Proxy's hint ("pick without a profile id") is the WRONG advice here.
    assert "profile id" not in words and "without" not in words
    assert pin.limit == {
        "account": "c-work", "account_name": "Work Max", "provider": "claude-cli",
        "vendor": "claude", "kind": "usage",
        "resets_at": accounts.iso_utc(until), "resumable": True,
    }
    assert [p.get("profileId") for p in picks(fake)] == ["c-work"], "never another account"


@pytest.mark.parametrize("state, kind, says", [
    ({"status": "unauthenticated"}, "signin", "needs to sign in again"),
    ({"enabled": False}, "disabled", "is turned off on the Iron-Proxy card"),
    (None, "missing", "no longer exists in Iron-Proxy"),
])
def test_every_other_pinned_refusal_is_by_kind(tmp_path, fake, state, kind, says):
    _client_for(fake)
    pid = "c-spare"
    if state is None:
        del fake.profiles[pid]
    elif "enabled" in state:
        fake.profiles[pid]["enabled"] = False
    else:
        fake.states[pid].update(state)
    pin = P.AccountPin(profile_id=pid, ij_provider="claude-cli", title="Spare")
    token = P.set_pin(pin)
    try:
        with pytest.raises(ProviderError) as info:
            accounts.lease("claude-cli")
    finally:
        P.reset_pin(token)
    assert info.value.no_failover is True
    assert says in str(info.value) and "profile id" not in str(info.value)
    assert pin.limit["kind"] == kind and pin.limit["resumable"] is (kind != "missing")


def test_an_iron_proxy_without_the_pinned_pick_is_never_asked(tmp_path):
    """An older Iron-Proxy IGNORES pinned=1 and would answer the next ready
    account: it is not asked at all — "needs an update", never a guess."""
    old = make_fake(tmp_path, features=FEATURES_PICK_PROFILE)
    try:
        _client_for(old)
        token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
        try:
            with pytest.raises(ProviderError) as info:
                accounts.lease("claude-cli")
        finally:
            P.reset_pin(token)
        assert str(info.value).endswith(accounts.PINNED_TOO_OLD) and info.value.no_failover is True
        assert picks(old) == []
    finally:
        old.stop()


def test_a_pinned_lease_never_lends_another_providers_account(tmp_path, fake):
    _client_for(fake)
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
    try:
        for other in ("codex-cli", "grok-cli"):
            with pytest.raises(ProviderError) as info:
                accounts.lease(other)
            assert info.value.no_failover is True
            assert 'runs on the Claude account "Work Max"' in str(info.value)
    finally:
        P.reset_pin(token)
    assert picks(fake) == []


def test_an_answer_naming_another_account_is_refused(tmp_path, fake):
    """An Iron-Proxy that answered with a different account (a bug, an old
    build) never lends it: nothing runs."""
    _client_for(fake)
    fake.pinned_lenient = True
    real = fake.pick_profile

    def wrong(provider, lane, pid):
        out = real(provider, lane, pid)
        return {**out, "profile": {**out["profile"], "id": "c-spare"}}

    fake.pick_profile = wrong  # type: ignore[method-assign]
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
    try:
        with pytest.raises(ProviderError) as info:
            accounts.lease("claude-cli")
    finally:
        P.reset_pin(token)
    assert "answered with a different Claude account" in str(info.value)


async def test_a_limit_mid_call_is_reported_and_never_rotated(tmp_path, fake):
    """retry_lease under a pin: Iron-Proxy is told (it parks), the facts land
    on the pin, the call ends in the session's words — and NO next account is
    leased (unpinned, the same report hands back the next account)."""
    _client_for(fake)
    pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    token = P.set_pin(pin)
    try:
        lease = accounts.lease("claude-cli")
        cause = ProviderError("429", status_code=429)
        with pytest.raises(ProviderError) as info:
            await accounts.retry_lease(lease, cause, {"status": 429, "text": "limit"}, [])
    finally:
        P.reset_pin(token)
    assert info.value.no_failover is True and "is at its request limit until" in str(info.value)
    assert pin.limit["kind"] == "rate" and pin.limit["resets_at"]
    assert fake.states["c-work"]["status"] == "parked"
    assert [p.get("profileId") for p in picks(fake)] == ["c-work"], "no second pick"
    # The same report UNPINNED rotates (chat's v1.301.0 behaviour, unchanged).
    fake.states["c-work"].update({"status": "ready"})
    fake.states["c-work"].pop("parkedUntil", None)
    lease = accounts.lease("claude-cli")
    nxt = await accounts.retry_lease(lease, ProviderError("429", status_code=429), {"status": 429}, [])
    assert nxt.profile_id == "c-spare"


async def test_a_limit_after_text_streamed_keeps_its_words_and_still_parks(tmp_path, fake):
    _client_for(fake)
    pin = P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    token = P.set_pin(pin)
    try:
        lease = accounts.lease("claude-cli")
        cause = ProviderError("claude-cli: the reply was cut off", status_code=429)
        with pytest.raises(ProviderError) as info:
            await accounts.retry_lease(lease, cause, {"status": 429}, [], retry_ok=False)
    finally:
        P.reset_pin(token)
    assert info.value is cause and cause.no_failover is True
    assert pin.limit is not None and pin.limit["kind"] == "rate"


def test_a_pinned_refusal_never_carries_iron_proxys_hint(tmp_path, fake):
    """Iron-Proxy 1b1ea71 answers a parked CHOSEN account with a hint to
    "pick without a profile id" — take the next account. Inside a Build
    session the user chooses; the refusal is Iron Jarvis's own sentence. The
    fake answers with the vendored bundle's hint (checked present there)."""
    bundle = (Path(__file__).resolve().parents[1] / "desktop" / "vendor" / "iron-proxy"
              / "iron-proxy.mjs").read_text(encoding="utf-8")
    assert "or pick without a profile id so the next ready ${profile.provider} account is used." in bundle
    _client_for(fake)
    fake.states["c-work"].update({"status": "parked", "parkedUntil": iso(600),
                                  "parkedReason": {"kind": "rate-limit"}})
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
    try:
        with pytest.raises(ProviderError) as info:
            accounts.lease("claude-cli")
    finally:
        P.reset_pin(token)
    words = str(info.value)
    assert "profile id" not in words and "next ready" not in words and "is at its request limit" in words


# --------------------------------------------------------------------------- #
# 2. GET /build/accounts
# --------------------------------------------------------------------------- #


async def test_the_account_list_groups_names_plans_and_one_status_each(app, fake):
    await enable(app)
    fake.states["c-spare"].update({"status": "parked", "parkedUntil": iso(4000),
                                   "parkedReason": {"kind": "rate-limit"}})
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    status, body = await asgi(app, "GET", "/build/accounts")
    assert status == 200
    assert body["iron_proxy"] == {"enabled": True, "running": True, "needs_update": False, "note": ""}
    claude = {r["id"]: r for r in body["claude"]}
    codex = {r["id"]: r for r in body["codex"]}
    assert list(claude) == ["c-work", "c-spare", "c-gone"] and list(codex) == ["x-plus", "x-team"]
    work = claude["c-work"]
    assert work["name"] == "Work Max" and work["default"] is True and work["vendor"] == "claude"
    assert work["vendor_label"] == "Claude Code" and work["provider"] == "claude-cli"
    assert work["plan"] == "max" and work["plan_label"] == "Max"
    # The window NEAREST its limit, Iron-Proxy's numbers only.
    assert work["status"] == "usage"
    assert work["usage"]["percent"] == 38 and work["usage"]["window"] == "7 days"
    assert work["usage"]["window_mins"] == 10080 and work["usage"]["resets_at"].endswith("Z")
    spare = claude["c-spare"]
    assert spare["status"] == "limit" and spare["limit_kind"] == "rate" and spare["resets_at"]
    assert spare["plan"] is None and "usage" not in spare and spare["default"] is False
    assert claude["c-gone"]["status"] == "needs_sign_in"
    assert codex["x-plus"]["plan_label"] == "Plus" and codex["x-plus"]["usage"]["percent"] == 61
    assert codex["x-plus"]["default"] is True
    # No percent when the vendor sent none: Ready, never a guess.
    assert codex["x-team"]["status"] == "ready" and "usage" not in codex["x-team"]
    assert codex["x-team"]["plan"] is None
    # Never a home, never a token.
    assert "home" not in json.dumps(body) and fake.token not in json.dumps(body)


async def test_the_list_reads_only_the_cached_snapshot(app, fake):
    await enable(app)
    fake.queries.clear()
    fake.calls.clear()
    status, _ = await asgi(app, "GET", "/build/accounts")
    assert status == 200
    hit = {path for path, _ in fake.queries}
    assert not hit & {"/iron/profiles", "/iron/states", "/iron/discover", "/iron/usage"}, hit


def _refreshed(fake) -> list[str]:
    return [b.get("id") for m, p, b in fake.calls if p == "/iron/refresh"]


async def test_an_unknown_plan_is_read_in_the_background(app, fake):
    """c-spare (Claude, no plan): Iron-Proxy's own status check reads it, in
    the background; an account needing sign-in is NEVER re-checked (v1.303.3);
    a Codex account has no status-command plan."""
    from tests.test_build_sessions_v1367 import until

    await enable(app)
    fake.plan_on_refresh["c-spare"] = "pro"
    await asgi(app, "GET", "/build/accounts")
    assert await until(lambda: _refreshed(fake) == ["c-spare"]), _refreshed(fake)
    row: dict = {}
    for _ in range(100):  # the re-check's answer lands in the snapshot
        status, body = await asgi(app, "GET", "/build/accounts")
        row = {r["id"]: r for r in body["claude"]}["c-spare"]
        if row["plan"]:
            break
        await asyncio.sleep(0.05)
    assert row["plan"] == "pro" and row["plan_label"] == "Pro"
    assert _refreshed(fake) == ["c-spare"], "never the signed-out account, never a Codex one"


async def test_a_plan_iron_proxy_cannot_learn_is_asked_at_most_every_ten_minutes(app, fake, monkeypatch):
    """A status check that names no plan (an account with none) must not
    make every look at the list run the CLI's status command again: one ask
    per account per 10 minutes (the service's own 10 s throttle is off here,
    so only ours holds)."""
    from iron_jarvis.iron_proxy import service as svc_mod
    from tests.test_build_sessions_v1367 import until

    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)
    await enable(app)
    await asgi(app, "GET", "/build/accounts")
    assert await until(lambda: _refreshed(fake) == ["c-spare"]), _refreshed(fake)
    for _ in range(3):
        await asgi(app, "GET", "/build/accounts")
    await asyncio.sleep(0.3)
    assert _refreshed(fake) == ["c-spare"], "asked again within ten minutes"


async def test_a_turned_off_account_is_not_in_the_list(app, fake):
    fake.profiles["x-team"]["enabled"] = False
    await enable(app)
    status, body = await asgi(app, "GET", "/build/accounts")
    assert [r["id"] for r in body["codex"]] == ["x-plus"]


async def test_iron_proxy_off_or_too_old_is_said_never_guessed(tmp_path, app):
    status, body = await asgi(app, "GET", "/build/accounts")
    assert status == 200 and body["claude"] == [] and body["codex"] == []
    assert body["iron_proxy"]["enabled"] is False and body["iron_proxy"]["note"] == BA.OFF_NOTE
    old = make_fake(tmp_path, features=FEATURES_PICK_PROFILE)
    try:
        await enable(app)
        status, body = await asgi(app, "GET", "/build/accounts")
        assert body["iron_proxy"]["needs_update"] is True and body["iron_proxy"]["note"] == BA.UPDATE_NOTE
    finally:
        old.stop()


async def test_make_default_reorders_through_iron_proxy(app, fake):
    await enable(app)
    status, body = await asgi(app, "POST", "/build/accounts/c-spare/default")
    assert status == 200, body
    reorder = [b for m, p, b in fake.calls if p == "/iron/profiles/reorder"]
    assert reorder == [{"provider": "anthropic", "ids": ["c-spare", "c-work", "c-gone"]}]
    assert [r["id"] for r in body["claude"] if r["default"]] == ["c-spare"]
    status, body = await asgi(app, "POST", "/build/accounts/nope/default")
    assert status == 404 and "profile" not in body["detail"].lower()


# --------------------------------------------------------------------------- #
# 3. a session's account at create and on PATCH
# --------------------------------------------------------------------------- #


async def _new(app, work, **extra) -> dict:
    body = {"folder": str(work), "provider": CLAUDE_CLI[0], "model": CLAUDE_CLI[1], **extra}
    status, view = await asgi(app, "POST", "/build/sessions", body)
    assert status == 201, view
    return view


async def test_a_new_session_starts_on_the_vendors_first_usable_account(app, fake, work):
    await enable(app)
    view = await _new(app, work)
    assert view["account"]["id"] == "c-work" and view["account"]["status"] == "usage"
    assert view["account_vendor"] == "claude" and view["setup"]["provider"] == "claude-cli"
    # The first in order is parked: the first USABLE one.
    fake.states["c-work"].update({"status": "parked", "parkedUntil": iso(600)})
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    view = await _new(app, work)
    assert view["account"]["id"] == "c-spare"
    # A Codex model starts on the Codex Default.
    view = await _new(app, work, provider="codex-cli", model="")
    assert view["account"]["id"] == "x-plus" and view["account_vendor"] == "codex"
    # An account asked for by id.
    view = await _new(app, work, account="x-team", provider="claude-cli")
    assert view["account"]["id"] == "x-team" and view["setup"]["provider"] == "codex-cli"
    assert "model" not in view["setup"], "a Codex account gets the Codex default model"


async def test_the_accounts_vendor_is_read_off_the_event_loop(app, fake, work, monkeypatch):
    """A keyless anthropic/openai's vendor is read through provider
    availability (CLI presence, sign-in): never on the daemon's one loop."""
    import threading

    await enable(app)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = BA.vendor_of

    def spy(platform, provider):
        seen.append(threading.get_ident())
        return real(platform, provider)

    monkeypatch.setattr(BA, "vendor_of", spy)
    sid = (await _new(app, work))["id"]
    await _new(app, work, account="x-plus")
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/account", {"account": "x-team"})
    assert status == 200
    assert seen and loop_thread not in seen, "the vendor was read on the event loop"


async def test_with_iron_proxy_off_a_session_is_exactly_phase_4(app, work):
    view = await _new(app, work)
    assert view["account"] is None and view["account_limit"] is None and view["account_wait"] is None
    assert view["setup"] == {"provider": "claude-cli", "model": "claude-sonnet-4-5"}
    status, body = await asgi(app, "POST", "/build/sessions", {
        "folder": str(work), "provider": "claude-cli", "account": "c-work",
    })
    assert status == 409 and body["detail"] == "Iron-Proxy is off; turn it on in Connections first."


async def test_a_local_model_or_an_api_key_has_no_account(app, fake, work):
    await enable(app)
    view = await _new(app, work, provider="ollama", model="qwen3")
    assert view["account"] is None and view["account_vendor"] == ""


async def test_patch_account_moves_vendor_and_model_by_the_rule(app, fake, work):
    await enable(app)
    sid = (await _new(app, work))["id"]
    # Another Claude account keeps the model.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-spare"})
    assert status == 200, view
    assert view["account"]["id"] == "c-spare" and view["setup"]["model"] == "claude-sonnet-4-5"
    # A Codex account switches the model to the Codex default...
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "x-plus"})
    assert view["setup"]["provider"] == "codex-cli" and "model" not in view["setup"]
    # ...or to the GPT model the user picked with it.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "x-team", "model": "gpt-5.5"})
    assert view["setup"]["model"] == "gpt-5.5" and view["account"]["id"] == "x-team"
    # Back to Claude: that vendor's default model.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-work"})
    assert view["setup"]["provider"] == "claude-cli" and "model" not in view["setup"]
    # "" = no account (this PC's login): Phase 4.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": ""})
    assert view["account"] is None and "account" not in setup_of(app, sid)


async def test_patch_refuses_an_unknown_or_turned_off_account_in_words(app, fake, work):
    await enable(app)
    sid = (await _new(app, work))["id"]
    status, body = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "nope"})
    assert status == 400 and body["detail"].startswith("There is no Claude Code or Codex account")
    fake.profiles["c-spare"]["enabled"] = False
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    status, body = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-spare"})
    assert status == 409 and "turned off" in body["detail"]
    for word in ("profile id", "daemon", "hint"):
        assert word not in body["detail"].lower()


async def test_a_model_pick_on_another_vendor_moves_to_its_default_account(app, fake, work):
    await enable(app)
    sid = (await _new(app, work))["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"provider": "codex-cli", "model": "gpt-5.5"})
    assert view["account"]["id"] == "x-plus"
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"provider": "ollama", "model": "qwen3"})
    assert view["account"] is None and view["account_vendor"] == ""
