"""v1.427.4 (a USER BUG, the rest of v1.427.3) — Chat › Code and chat never
move work onto an account signed in to the SAME Claude account.

v1.427.3 taught Build › Code's Continue that two Iron-Proxy accounts can be one
Claude login (one usage limit). Three other places still chose "the next
account" without asking:

* Chat › Code's group move at a limit (``build_accounts.takeover``) — it now
  skips such an account, and runs off the event loop (it reads small files);
* Chat › Code's "Choose account" on the limit card — a same-login pick is
  refused in words (409, ``same_login``); with ``force`` an account Iron-Proxy
  marked as at its limit has the mark cleared first (never one that must sign
  in, never the session's own);
* chat's own rotation (``iron_proxy.accounts.lease`` / ``retry_lease``) — the
  next pick that shares the limited account's login is passed over for the
  next one that does not (picked by id), or refused in words; the passed-over
  account is never told to Iron-Proxy as limited (it made no call).

Throwaway account homes only (never a real one); no network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import claude_identity as CI
from iron_jarvis.providers.adapters.base import ProviderError
from tests.test_build_sessions_v1367 import asgi

ORG_A = "11111111-1111-4111-8111-111111111111"
ACC_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "22222222-2222-4222-8222-222222222222"
ACC_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LATER = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    CI.forget()
    fake_home = tmp_path / "userhome"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(accounts, "_NOTED", set())
    previous = accounts.set_client_source(None)
    yield
    accounts.set_client_source(previous)
    CI.forget()


def _sign_in(home: Path, org: str, acc: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"organizationUuid": org, "accountUuid": acc}}), encoding="utf-8",
    )


def _profiles(tmp_path, *, other: bool = True) -> list[dict]:
    """Work (at a limit), Clone (Work's own login), Other (its own) — in
    Iron-Proxy's order."""
    h1, h2, h3 = tmp_path / "acc1", tmp_path / "acc2", tmp_path / "acc3"
    _sign_in(h1, ORG_A, ACC_A)
    _sign_in(h2, ORG_A, ACC_A)
    _sign_in(h3, ORG_B, ACC_B)
    rows = [
        {"id": "p1", "provider": "anthropic", "lane": "cli", "title": "Work", "order": 0, "cli": {"home": str(h1)}},
        {"id": "p2", "provider": "anthropic", "lane": "cli", "title": "Clone", "order": 1, "cli": {"home": str(h2)}},
        {"id": "p3", "provider": "anthropic", "lane": "cli", "title": "Other", "order": 2, "cli": {"home": str(h3)}},
    ]
    return rows if other else rows[:2]


def _limited(kind: str = "rate-limit") -> dict:
    return {"status": "parked", "parkedUntil": LATER, "parkedReason": {"kind": kind}}


class _Client:
    """Iron-Proxy as the lease code sees it: picks the first account that is
    not parked (or the one asked for by id), parks on a limit signal."""

    features = ["executor-v1", "pick-profile", "pick-pinned"]

    def __init__(self, profiles, states):
        self._p = profiles
        self._s = dict(states)
        self.signals: list[str] = []
        self.picks: list[tuple[str | None, bool]] = []
        self.unparked: list[str] = []

    def profiles(self):
        return self._p

    def states(self):
        return self._s

    def _ready(self, pid):
        st = self._s.get(pid) or {}
        return st.get("status") not in ("parked", "unauthenticated", "disabled")

    def pick(self, provider, lane="cli", profile_id=None, pinned=False):
        self.picks.append((profile_id, pinned))
        mine = [p for p in self._p if p["provider"] == provider]
        prof = next((p for p in mine if p["id"] == profile_id), None) if profile_id else \
            next((p for p in mine if self._ready(p["id"])), None)
        if prof is None:
            from iron_jarvis.iron_proxy.client import IronProxyError

            raise IronProxyError("ALL_PROFILES_EXHAUSTED", "Every account is parked.", status=429,
                                 details={"resetAt": LATER})
        return {"profile": {"id": prof["id"], "title": prof["title"], "provider": provider},
                "env": {"set": {"CLAUDE_CONFIG_DIR": prof["cli"]["home"]}, "unset": []}}

    def signal(self, pid, status=None, headers=None, text=None):
        self.signals.append(pid)
        self._s[pid] = _limited()
        return {"parked": True, "signal": {"kind": "rate-limit", "resetAt": LATER}, "state": {}}

    def finished(self, *a, **k):
        return {"ok": True}

    def unpark(self, pid):
        self.unparked.append(pid)
        self._s[pid] = {"status": "ready"}
        return {"ok": True}


class _Svc:
    """The daemon's Iron-Proxy service as the Chat › Code routes read it."""

    enabled = True

    def __init__(self, client):
        self.client = client
        self.refreshed = 0

    def cached_accounts(self):
        return self.client.profiles(), self.client.states()

    def lease_client(self, timeout_s=None):
        return self.client

    def refresh_accounts(self):
        self.refreshed += 1

    def has_feature(self, name):
        return name in self.client.features

    def check(self):
        return True


def _lease_for(prof: dict) -> accounts.Lease:
    spec = accounts.PROVIDERS["claude-cli"]
    return accounts.make_lease(spec, prof["id"], prof["title"], prof["cli"]["home"], [])


# --------------------------------------------------------------------------- #
# 1. Chat › Code's group move
# --------------------------------------------------------------------------- #
def test_the_group_move_skips_an_account_with_the_limited_ones_login(tmp_path):
    profs = _profiles(tmp_path)
    svc = _Svc(_Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p3": {"status": "ready"}}))
    groups = {"Work": ["p1", "p2", "p3"]}
    target = BA.takeover(svc, groups, "p1")
    assert target is not None and target["id"] == "p3", "Clone shares Work's limit; Other does not"
    # With only the clone in the group, nothing takes over: the reply waits.
    assert BA.takeover(svc, {"Work": ["p1", "p2"]}, "p1") is None


def test_an_account_whose_login_is_not_known_is_never_read_as_the_same(tmp_path):
    profs = _profiles(tmp_path)
    (Path(profs[1]["cli"]["home"]) / ".claude.json").unlink()
    svc = _Svc(_Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p3": {"status": "ready"}}))
    snap = BA.snapshot(svc)
    assert BA.shares_login(snap, "p1", "p2") is False
    assert BA.takeover(svc, {"Work": ["p1", "p2"]}, "p1")["id"] == "p2"


def test_the_group_move_reads_its_files_off_the_event_loop():
    src = Path(build_routes.__file__).read_text(encoding="utf-8")
    assert "await asyncio.to_thread(BA.takeover, svc, groups, account)" in src
    carry = Path(build_routes.__file__).parents[2].joinpath("hosted", "carry_on.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(\n                    BA.takeover," in carry


# --------------------------------------------------------------------------- #
# 2. "Choose account" on Chat › Code's limit card
# --------------------------------------------------------------------------- #
def test_choosing_a_same_login_account_to_carry_on_is_refused_in_words(tmp_path):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": _limited(), "p2": _limited(), "p3": {"status": "ready"}})
    platform = SimpleNamespace(iron_proxy=_Svc(client))
    setup = {"account": "p1", "account_name": "Work"}
    problem = build_routes.continue_problem(platform, setup, "p2", force=True)
    assert problem is not None and problem["code"] == "same_login"
    assert '"Clone" is signed in to the same Claude account as "Work"' in problem["detail"]
    assert CI.SAME_LOGIN_FIX in problem["detail"]
    assert client.unparked == [], "nothing is cleared for an account that shares the limit"
    # Another login: no problem (and without force nothing is touched).
    assert build_routes.continue_problem(platform, setup, "p3") is None
    # The session's own account: never refused, never cleared.
    assert build_routes.continue_problem(platform, setup, "p1", force=True) is None
    assert client.unparked == []


def test_force_clears_the_limit_mark_first_but_never_for_a_sign_in(tmp_path):
    profs = _profiles(tmp_path)
    client = _Client(profs, {
        "p1": _limited(), "p2": {"status": "ready"},
        "p3": _limited("quota-exhausted"),
    })
    svc = _Svc(client)
    platform = SimpleNamespace(iron_proxy=svc)
    setup = {"account": "p1", "account_name": "Work"}
    assert build_routes.continue_problem(platform, setup, "p3") is None
    assert client.unparked == [], "no force: the mark stays"
    assert build_routes.continue_problem(platform, setup, "p3", force=True) is None
    assert client.unparked == ["p3"] and svc.refreshed == 1
    # A sign-in is not a limit: never cleared by force.
    client._s["p3"] = {"status": "unauthenticated"}
    assert build_routes.continue_problem(platform, setup, "p3", force=True) is None
    assert client.unparked == ["p3"]


def test_a_refused_clear_is_said_in_words(tmp_path):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": _limited(), "p3": _limited()})

    def refuse(pid):
        raise RuntimeError("down")

    client.unpark = refuse
    platform = SimpleNamespace(iron_proxy=_Svc(client))
    problem = build_routes.continue_problem(platform, {"account": "p1"}, "p3", force=True)
    assert problem is not None and problem["detail"].startswith('The account manager would not let "Other" be tried again')


async def test_the_route_refuses_before_anything_moves(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p3": {"status": "ready"}})
    app = create_app(str(tmp_path / "home"))
    platform = app.state.d.platform
    platform.iron_proxy = _Svc(client)
    work = tmp_path / "work"
    work.mkdir()
    status, view = await asgi(app, "POST", "/build/sessions",
                              {"folder": str(work), "provider": "claude-cli", "model": "claude-sonnet-4-5"})
    assert status == 201, view
    sid = view["id"]
    await asyncio.to_thread(S.update, platform.engine, sid,
                            setup={"account": "p1", "account_name": "Work", "provider": "claude-cli"})
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/account",
                              {"account": "p2", "continue": True, "force": True})
    assert status == 409, body
    assert body["code"] == "same_login" and '"Clone"' in body["detail"]
    row = await asyncio.to_thread(S.get_row, platform.engine, sid)
    assert json.loads(row.setup_json or "{}").get("account") == "p1", "the session did not move"
    assert client.unparked == [] and client.signals == []


# --------------------------------------------------------------------------- #
# 3. chat's own rotation
# --------------------------------------------------------------------------- #
def test_a_fresh_pick_of_a_limited_accounts_twin_takes_the_next_login(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p3": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: (client.profiles(), client.states()))
    got = accounts.lease("claude-cli")
    assert got is not None and got.profile_id == "p3"
    assert client.picks == [(None, False), ("p3", False)], "Iron-Proxy's own pick, then Other by id"
    assert client.signals == [], "Clone made no call: it is never reported"


def test_a_second_twin_further_down_is_passed_over_too(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    h4 = tmp_path / "acc4"
    _sign_in(h4, ORG_A, ACC_A)
    profs.insert(2, {"id": "p4", "provider": "anthropic", "lane": "cli", "title": "Clone 2", "order": 1,
                     "cli": {"home": str(h4)}})
    client = _Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p4": {"status": "ready"},
                             "p3": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: (client.profiles(), client.states()))
    assert accounts.lease("claude-cli").profile_id == "p3"
    assert ("p4", False) not in client.picks


def test_with_no_other_login_a_fresh_pick_is_refused_in_words(tmp_path, monkeypatch):
    profs = _profiles(tmp_path, other=False)
    client = _Client(profs, {"p1": _limited(), "p2": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: (client.profiles(), client.states()))
    with pytest.raises(ProviderError) as info:
        accounts.lease("claude-cli")
    err = info.value
    assert getattr(err, "no_failover", False) is True, "never another provider"
    assert '"Work" is at its request limit until' in str(err)
    assert '"Clone" is signed in to the same Claude account as "Work"' in str(err)
    assert CI.SAME_LOGIN_FIX in str(err)
    assert err.retry_after and err.retry_after > 0
    assert client.signals == []


def test_a_fresh_pick_with_nothing_parked_is_iron_proxys_own(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: (client.profiles(), client.states()))
    assert accounts.lease("claude-cli").profile_id == "p1"
    assert client.picks == [(None, False)]
    # A sign-in is not a limit: its twin is still picked.
    client._s = {"p1": {"status": "parked", "parkedUntil": LATER, "parkedReason": {"kind": "auth-expired"}}}
    assert accounts.lease("claude-cli").profile_id == "p2"


def test_an_iron_proxy_that_cannot_pick_by_id_keeps_its_own_pick(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": _limited(), "p2": {"status": "ready"}, "p3": {"status": "ready"}})
    client.features = ["executor-v1"]
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: (client.profiles(), client.states()))
    assert accounts.lease("claude-cli").profile_id == "p2", "today's behaviour, unchanged"


async def test_a_retry_after_a_limit_passes_over_the_same_login(tmp_path, monkeypatch):
    profs = _profiles(tmp_path)
    client = _Client(profs, {"p1": {"status": "ready"}, "p2": {"status": "ready"}, "p3": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    # The daemon's snapshot lags behind the report (it shows Work ready): the
    # retry itself knows Work just met its limit.
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: None)
    current = _lease_for(profs[0])
    tried: list[str] = []
    cause = ProviderError("claude-cli: 429 rate limited", status_code=429)
    nxt = await accounts.retry_lease(current, cause, {"status": 429, "text": "rate limited"}, tried)
    assert nxt.profile_id == "p3"
    assert client.signals == ["p1"], "only the account that made the call is reported"


async def test_a_retry_with_no_other_login_is_refused_in_words(tmp_path, monkeypatch):
    profs = _profiles(tmp_path, other=False)
    client = _Client(profs, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    accounts.set_client_source(lambda: client)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: None)
    current = _lease_for(profs[0])
    cause = ProviderError("claude-cli: 429 rate limited", status_code=429)
    with pytest.raises(ProviderError) as info:
        await accounts.retry_lease(current, cause, {"status": 429, "text": "rate limited"}, [])
    assert getattr(info.value, "no_failover", False) is True
    assert '"Clone" is signed in to the same Claude account as "Work"' in str(info.value)
    assert '"Work" is at its request limit until' in str(info.value)
    assert client.signals == ["p1"], "Clone is never parked by a guess"


async def test_codex_accounts_are_untouched(tmp_path, monkeypatch):
    # Two Codex accounts are never read as one Claude login (no .claude.json rule there).
    h1, h2 = tmp_path / "x1", tmp_path / "x2"
    _sign_in(h1, ORG_A, ACC_A)
    _sign_in(h2, ORG_A, ACC_A)
    spec = accounts.PROVIDERS["codex-cli"]
    assert accounts._shares_claude_login(spec, str(h1), str(h2)) is False
