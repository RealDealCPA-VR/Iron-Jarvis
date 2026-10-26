"""v1.394.0 (the long-jobs plan's Phase 5): YOUR MODEL TEAM.

A custom agent with one or more ROLES is a team member. This file pins:

* the role vocabulary — one place (``agents/model_team.ROLES``) and the
  dashboard's twin (``dashboard/lib/teamRoles.ts``), equal;
* the record: ``roles`` / ``account`` are additive, normalised only by the
  registry, kept by a PATCH that does not send them;
* the routes: ``POST`` / ``PATCH /agents`` take and validate them,
  ``GET /team`` lists members with model, place, cost type and usage (the
  CACHED account snapshot only), and **Make a starter team** previews before
  it creates anything — nothing at all when no model is connected;
* the planner's list: a member's line carries its model, place, cost type,
  roles and usage; every other line is byte-identical; under Local only a
  member (or any custom agent) whose own model leaves the network is not
  listed, and ``list_agents`` agrees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from iron_jarvis.agents import model_team as T
from iron_jarvis.agents.dynamic import DynamicAgentRegistry
from iron_jarvis.agents.roster import RosterEntry, _block_line, roster_block
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import egress
from tests.test_build_sessions_v1367 import asgi

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# the vocabulary and its twin
# --------------------------------------------------------------------------- #


def _ts(name: str) -> str:
    return (ROOT / "dashboard" / "lib" / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_role_vocabulary_is_small_and_its_dashboard_twin_is_equal():
    assert T.ROLE_IDS == ("read", "code", "review", "test")
    pairs = re.findall(r'\{ id: "([a-z]+)", label: "([^"]+)" \}', _ts("teamRoles.ts"))
    assert tuple(pairs) == T.ROLES


def test_the_place_and_cost_words_have_a_dashboard_twin():
    src = _ts("modelTeam.ts")
    for key, words in T.PLACE_WORDS.items():
        assert f'{key}: "{words}"' in src, key
    for key, words in T.COST_WORDS.items():
        assert f'{key}: "{words}"' in src, key


def test_roles_are_cleaned_in_order_and_unknowns_are_named():
    assert T.clean_roles(["Test", "read", "nonsense", "read"]) == ["read", "test"]
    assert T.unknown_roles(["read", "Cook", "cook"]) == ["cook"]
    assert T.clean_roles(None) == [] and T.clean_roles("code") == ["code"]


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def test_roles_and_account_are_stored_and_a_patch_that_omits_them_keeps_them(app):
    reg = app.state.platform.agents_registry
    rec = reg.register("glm", "Reads.", [], provider="ollama", model="qwen3:8b",
                       roles=["code", "read", "bogus"], account="")
    assert json.loads(rec.roles_json) == ["read", "code"]
    assert T.is_member(rec) and T.roles_of(rec) == ["read", "code"]
    # A later register WITHOUT roles keeps them (the job card's _KEEP rule).
    rec2 = reg.register("glm", "Reads more.", [], provider="ollama", model="qwen3:8b")
    assert T.roles_of(rec2) == ["read", "code"]
    rec3 = reg.register("glm", "Reads more.", [], provider="ollama", model="qwen3:8b", roles=[])
    assert not T.is_member(rec3)


def test_an_older_database_gains_the_columns_and_its_rows_read_no_roles_no_account(tmp_path):
    """A database from v1.393.0: the reconciler adds ``roles_json`` /
    ``account`` (NULL on the old row), and only the registry reads them as
    the defaults — the row on disk is left as it is."""
    import sqlite3

    from iron_jarvis.platform import build_platform

    home = tmp_path / ".ironjarvis"
    home.mkdir()
    con = sqlite3.connect(str(home / "ironjarvis.db"))
    con.execute(
        "CREATE TABLE dynamicagentrecord (id VARCHAR PRIMARY KEY, name VARCHAR, "
        "system_prompt VARCHAR, tools_json VARCHAR, base_type VARCHAR, "
        "description VARCHAR, provider VARCHAR, model VARCHAR, created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO dynamicagentrecord VALUES ('dyn_old', 'oldie', 'Old.', '[]', 'builder', "
        "'from before', 'ollama', 'qwen3:8b', '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()
    p = build_platform(str(tmp_path))
    con = sqlite3.connect(str(home / "ironjarvis.db"))
    cols = {r[1] for r in con.execute("PRAGMA table_info(dynamicagentrecord)")}
    raw = con.execute("SELECT roles_json, account FROM dynamicagentrecord WHERE name='oldie'").fetchone()
    con.close()
    assert {"roles_json", "account"} <= cols
    assert raw == (None, None)  # the disk keeps its NULLs
    fresh = DynamicAgentRegistry(p.engine).load().get("oldie")
    assert fresh.roles_json == "[]" and fresh.account == ""
    assert not T.is_member(fresh) and T.member_facts(p, fresh) is None
    with session_scope(p.engine) as db:
        assert db.exec(text("SELECT count(*) FROM dynamicagentrecord")).one()[0] == 1


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


async def test_post_and_patch_agents_take_roles_and_refuse_an_unknown_one(app):
    status, body = await asgi(app, "POST", "/agents", {
        "name": "coder", "system_prompt": "Codes.", "roles": ["code", "review"],
    })
    assert status == 200, body
    status, listing = await asgi(app, "GET", "/agents")
    row = next(r for r in listing["dynamic"] if r["name"] == "coder")
    assert row["roles"] == ["code", "review"] and row["account"] == ""
    status, body = await asgi(app, "PATCH", "/agents/coder", {"roles": ["cook"]})
    assert status == 422
    assert "roles must be some of: read, code, review, test" in body["detail"] and "'cook'" in body["detail"]
    status, body = await asgi(app, "PATCH", "/agents/coder", {"description": "still codes"})
    assert status == 200 and body["roles"] == ["code", "review"]
    status, body = await asgi(app, "PATCH", "/agents/coder", {"roles": []})
    assert status == 200 and body["roles"] == []


class _Svc:
    """A stand-in Iron-Proxy service: only the CACHED snapshot (``enabled`` +
    ``cached_accounts``) — the team never calls anything else."""

    def __init__(self, profiles=None, states=None, enabled=True):
        self.enabled = enabled
        self._cache = (profiles or [], states or {}) if profiles is not None else None
        self.calls: list[str] = []
        #: Every OTHER attribute something asked this service for (a live
        #: call would be one of them): the tests assert it stays empty.
        self.touched: list[str] = []

    def cached_accounts(self):
        self.calls.append("cached_accounts")
        return self._cache

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.touched.append(name)
        raise AttributeError(name)


def _accounts() -> _Svc:
    profiles = [
        {"id": "c-work", "provider": "anthropic", "title": "Work Max", "order": 0, "lane": "cli"},
        {"id": "x-plus", "provider": "openai", "title": "Codex Plus", "order": 0, "lane": "cli"},
        {"id": "x-team", "provider": "openai", "title": "Codex Team", "order": 1, "lane": "cli"},
    ]
    states = {
        "c-work": {"status": "ready", "windows": [
            {"kind": "5h", "usedPercent": 42, "windowMins": 300, "resetsAt": "2999-01-01T00:00:00Z"}]},
        "x-plus": {"status": "ready"},
        "x-team": {"status": "parked", "parkedUntil": "2999-01-01T00:00:00Z",
                   "parkedReason": {"kind": "quota-exhausted"}},
    }
    return _Svc(profiles, states)


async def test_an_account_must_be_one_iron_proxy_lists_and_of_the_agents_vendor(app):
    platform = app.state.platform
    status, body = await asgi(app, "POST", "/agents", {
        "name": "cx", "system_prompt": "x", "provider": "codex-cli", "model": "subscription",
        "roles": ["code"], "account": "x-plus",
    })
    # Iron-Proxy is off here: said, never guessed.
    assert status == 422 and "Iron-Proxy is off or has not listed its accounts" in body["detail"]
    platform.iron_proxy = _accounts()
    status, body = await asgi(app, "POST", "/agents", {
        "name": "cx", "system_prompt": "x", "provider": "codex-cli", "model": "subscription",
        "roles": ["code"], "account": "c-work",
    })
    assert status == 422 and "'Work Max' is a Claude Code account" in body["detail"]
    status, body = await asgi(app, "POST", "/agents", {
        "name": "cx", "system_prompt": "x", "provider": "codex-cli", "model": "subscription",
        "roles": ["code"], "account": "x-plus",
    })
    assert status == 200, body
    # Moving it to another vendor's model while it keeps the account is
    # refused in words — never cleared behind the user's back.
    status, body = await asgi(app, "PATCH", "/agents/cx", {"provider": "claude-cli"})
    # (v1.394.0 review: the words name the page, never the API's field.)
    assert status == 422 and "No account of its own" in body["detail"]
    status, body = await asgi(app, "PATCH", "/agents/cx", {"provider": "claude-cli", "account": ""})
    assert status == 200 and body["account"] == ""


def _local(platform) -> None:
    platform.config.ollama_base_url = "http://127.0.0.1:11434"
    platform.providers.configure_local(
        ollama_base_url="http://127.0.0.1:11434", ollama_model="qwen3:8b",
        custom_base_url="", custom_model="",
    )


async def test_get_team_lists_members_with_model_place_cost_and_cached_usage(app, monkeypatch):
    platform = app.state.platform
    _local(platform)
    platform.iron_proxy = _accounts()
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    reg = platform.agents_registry
    reg.register("reader", "r", [], provider="ollama", model="qwen3:8b", roles=["read"])
    reg.register("claude", "c", [], provider="claude-cli", model="subscription", roles=["code"])
    reg.register("codex", "x", [], provider="codex-cli", model="subscription", roles=["test"],
                 account="x-team")
    reg.register("payg", "p", [], provider="openai", model="gpt-4o", roles=["review"])
    reg.register("not-a-member", "n", [], provider="openai", model="gpt-4o")
    status, body = await asgi(app, "GET", "/team")
    assert status == 200
    assert [r["id"] for r in body["roles"]] == ["read", "code", "review", "test"]
    rows = {m["name"]: m for m in body["members"]}
    assert set(rows) == {"reader", "claude", "codex", "payg"}
    assert (rows["reader"]["place"], rows["reader"]["cost"], rows["reader"]["usage"]) == ("this_pc", "free", None)
    assert (rows["claude"]["place"], rows["claude"]["cost"]) == ("cloud", "plan")
    # No account named: the account Iron-Proxy's normal pick would take now.
    assert rows["claude"]["usage"]["account_name"] == "Work Max"
    assert rows["claude"]["usage"]["percent"] == 42
    # Its own account, at its limit: said — never 0%.
    assert rows["codex"]["account_name"] == "Codex Team"
    assert rows["codex"]["usage"]["status"] == "limit" and "percent" not in rows["codex"]["usage"]
    assert (rows["payg"]["place"], rows["payg"]["cost"], rows["payg"]["usage"]) == ("cloud", "pay", None)
    # Whose accounts each one's account picker lists.
    assert [rows[n]["vendor"] for n in ("reader", "claude", "codex", "payg")] == ["", "claude", "codex", ""]
    # Only the snapshot was read.
    assert set(platform.iron_proxy.calls) == {"cached_accounts"}
    assert platform.iron_proxy.touched == []


def test_usage_words_never_say_zero_for_an_unknown():
    assert T.usage_words("plan", None) == "usage not known yet"
    assert T.usage_words("plan", {"status": "ready"}) == "usage not known yet"
    assert T.usage_words("plan", {"status": "usage", "percent": 61, "window_mins": 300}) == (
        "61% of its 5-hour window used"
    )
    assert T.usage_words("plan", {"status": "limit"}) == "at its usage limit"
    assert T.usage_words("free", None) == "" and T.usage_words("pay", None) == ""


# --------------------------------------------------------------------------- #
# Make a starter team
# --------------------------------------------------------------------------- #


CATALOG = [
    {"provider": "ollama", "model": "qwen3:8b", "available": True, "on_this_network": True},
    {"provider": "ollama", "model": "llama3.1:8b", "available": True, "on_this_network": True},
    {"provider": "ollama", "model": "gone:1b", "available": False, "on_this_network": True},
    {"provider": "claude-cli", "model": "subscription", "available": True, "on_this_network": False},
    {"provider": "claude-cli", "model": "claude-opus-4-8", "available": True, "on_this_network": False},
    {"provider": "codex-cli", "model": "subscription", "available": True, "on_this_network": False},
    {"provider": "openai", "model": "gpt-4o", "available": True, "on_this_network": False},
    {"provider": "openai", "model": "gpt-4o-mini", "available": True, "on_this_network": False},
]


@pytest.fixture()
def catalog(app, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    rows = [dict(r) for r in CATALOG]
    monkeypatch.setattr(connections, "selectable_models", lambda d: [dict(r) for r in rows])
    monkeypatch.setattr(app.state.platform.providers, "inherited_from", lambda name: None)
    _local(app.state.platform)
    return rows


def _custom_agents(app) -> list[str]:
    return [r.name for r in app.state.platform.agents_registry.list()]


async def test_the_starter_preview_offers_local_first_plans_unticked_and_no_pay_per_use(app, catalog):
    status, body = await asgi(app, "GET", "/team/starter")
    assert status == 200
    rows = body["rows"]
    assert [(r["provider"], r["model"], r["group"], r["ticked"]) for r in rows] == [
        ("ollama", "qwen3:8b", "local", True),
        ("ollama", "llama3.1:8b", "local", True),
        ("claude-cli", "subscription", "subscription", False),
        ("codex-cli", "subscription", "subscription", False),
    ]
    assert rows[0]["roles"] == ["read", "code"] and rows[0]["cost"] == "free" and rows[0]["place"] == "this_pc"
    assert rows[2]["cost"] == "plan" and rows[2]["name"] == "claude-code"
    assert body["paid"] is False and body["paid_available"] == 1
    # NOTHING was created by looking.
    assert _custom_agents(app) == []
    status, body = await asgi(app, "GET", "/team/starter?paid=1")
    paid = [r for r in body["rows"] if r["group"] == "paid"]
    assert [(r["provider"], r["ticked"], r["cost"]) for r in paid] == [("openai", False, "pay")]


async def test_create_makes_exactly_the_ticked_members_and_refuses_pay_per_use_without_the_switch(app, catalog):
    _, preview = await asgi(app, "GET", "/team/starter")
    local = preview["rows"][0]
    status, body = await asgi(app, "POST", "/team/starter", {"rows": [
        {"key": local["key"]},
        {"key": "codex-cli|subscription", "name": "my-codex", "roles": ["test"]},
        {"key": "openai|gpt-4o"},  # pay per use, switch OFF: not offered
    ]})
    assert status == 200, body
    assert body["created"] == [local["name"], "my-codex"]
    assert body["skipped"] == [{"key": "openai|gpt-4o", "reason": "That model is not offered for the team right now."}]
    reg = app.state.platform.agents_registry
    made = reg.get(local["name"])
    assert (made.provider, made.model, T.roles_of(made), made.base_type) == ("ollama", "qwen3:8b", ["read", "code"], "builder")
    assert json.loads(made.tools_json) == []  # inherits its base type's tools
    cx = reg.get("my-codex")
    assert (cx.provider, cx.model, T.roles_of(cx)) == ("codex-cli", "subscription", ["test"])
    members = {m["name"] for m in body["team"]["members"]}
    assert members == {local["name"], "my-codex"}
    # A second preview says the model already has a member, unticked.
    _, again = await asgi(app, "GET", "/team/starter")
    first = next(r for r in again["rows"] if r["key"] == local["key"])
    assert first["exists"] == local["name"] and first["ticked"] is False


async def test_nothing_connected_creates_nothing_and_says_so(app, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    monkeypatch.setattr(connections, "selectable_models", lambda d: [])
    status, body = await asgi(app, "GET", "/team/starter")
    assert status == 200 and body["rows"] == [] and body["note"] == T.NOTHING_CONNECTED
    status, body = await asgi(app, "POST", "/team/starter", {"rows": [{"key": "ollama|qwen3:8b"}]})
    assert status == 409 and body["detail"] == T.NOTHING_CONNECTED
    assert _custom_agents(app) == []


async def test_only_pay_per_use_connected_says_turn_the_switch_on(app, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    monkeypatch.setattr(connections, "selectable_models", lambda d: [
        {"provider": "openai", "model": "gpt-4o", "available": True, "on_this_network": False}])
    monkeypatch.setattr(app.state.platform.providers, "inherited_from", lambda name: None)
    _, body = await asgi(app, "GET", "/team/starter")
    assert body["rows"] == [] and "Include pay-per-use models" in body["note"]


# --------------------------------------------------------------------------- #
# the planner's list
# --------------------------------------------------------------------------- #


def _members(app, monkeypatch) -> None:
    platform = app.state.platform
    _local(platform)
    platform.iron_proxy = _accounts()
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    reg = platform.agents_registry
    reg.register("reader", "r", [], description="reads the code", provider="ollama", model="qwen3:8b",
                 roles=["read", "code"])
    reg.register("codex-coder", "x", [], description="writes code", provider="codex-cli",
                 model="subscription", roles=["code", "test"], account="x-plus")
    reg.register("claude-reviewer", "c", [], description="reviews", provider="claude-cli",
                 model="subscription", roles=["review"])
    reg.register("plain", "p", [], description="a plain agent")


def test_a_members_line_carries_its_model_place_cost_roles_and_usage(app, monkeypatch):
    _members(app, monkeypatch)
    block = roster_block(app.state.platform)
    lines = {ln.split(" ")[1]: ln for ln in block.split("\n") if ln.startswith("- ")}
    assert lines["custom:reader"] == (
        "- custom:reader — reads the code [qwen3:8b, this PC, free; roles: read, code] (no runs yet)"
    )
    assert lines["custom:codex-coder"] == (
        "- custom:codex-coder — writes code [Codex, cloud, your plan, usage not known yet; "
        "roles: code, test] (no runs yet)"
    )
    assert lines["custom:claude-reviewer"] == (
        "- custom:claude-reviewer — reviews [Claude Code, cloud, your plan, 42% of its 5-hour "
        "window used; roles: review] (no runs yet)"
    )
    # Everyone else's line is exactly as before.
    assert lines["custom:plain"] == "- custom:plain — a plain agent (no runs yet)"
    assert lines["builder"] == "- builder — hands-on doer for files, shell, and documents (no runs yet)"


def test_a_long_description_is_clamped_but_never_the_facts_or_the_stats():
    entry = RosterEntry("custom:reader", "dynamic", "x" * 400, True, True,
                        {"sessions": 23, "success_rate": 0.87}, team={
                            "roles": ["read"], "provider": "mock", "model": "", "place": "this_pc",
                            "cost": "free", "usage": None, "account": ""})
    line = _block_line(entry, None)
    assert line.endswith("[mock, this PC, free; roles: read] (87% over 23 runs)")
    assert "…" in line and len(line) <= 170


def test_under_local_only_a_cloud_member_is_not_listed_at_all(app, monkeypatch):
    _members(app, monkeypatch)
    app.state.platform.agents_registry.register(
        "cloud-plain", "p", [], description="pinned to the cloud", provider="openai", model="gpt-4o")
    with egress.local_only_scope(True):
        block = roster_block(app.state.platform)
    assert "custom:reader" in block
    for gone in ("codex-coder", "claude-reviewer", "cloud-plain"):
        assert f"custom:{gone}" not in block, block
    assert "custom:plain" in block  # no model of its own: runs on its caller's (local)
    # Outside the scope every one of them is listed.
    block = roster_block(app.state.platform)
    assert "custom:codex-coder" in block and "custom:cloud-plain" in block


def test_the_coding_sessions_team_section_lists_members_only(app, monkeypatch):
    _members(app, monkeypatch)
    block = T.team_block(app.state.platform)
    assert block.startswith(T.TEAM_HEADER + "\n" + T.TEAM_LEAD)
    body = [ln for ln in block.split("\n") if ln.startswith("- ")]
    assert [ln.split(" ")[1] for ln in body] == ["custom:claude-reviewer", "custom:codex-coder", "custom:reader"]
    with egress.local_only_scope(True):
        local = T.team_block(app.state.platform)
    assert [ln.split(" ")[1] for ln in local.split("\n") if ln.startswith("- ")] == ["custom:reader"]


def test_no_team_no_section(app):
    app.state.platform.agents_registry.register("plain", "p", [])
    assert T.team_block(app.state.platform) == ""


async def test_list_agents_names_members_and_hides_cloud_ones_under_local_only(app, monkeypatch):
    from iron_jarvis.tools.base import ToolContext

    _members(app, monkeypatch)
    platform = app.state.platform
    tool = platform.registry.get("list_agents")
    ctx = ToolContext(workspace=Path("."), session_id="s", agent_run_id="r", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    res = await tool.execute({}, ctx)
    assert "- reader (base=builder) [team member: qwen3:8b, this PC, free; roles: read, code]" in res.output
    assert "codex-coder" in res.output
    with egress.local_only_scope(True):
        res = await tool.execute({}, ctx)
    assert "reader" in res.output and "codex-coder" not in res.output and "claude-reviewer" not in res.output


# --------------------------------------------------------------------------- #
# place and cost read the local-only lock's own predicate
# --------------------------------------------------------------------------- #


class _Adapter:
    def __init__(self, provider, endpoint=""):
        self.provider, self._endpoint, self.model = provider, endpoint, ""


class _Mgr:
    def __init__(self, adapters, inherited=None):
        self.adapters, self.inherited = adapters, inherited or {}

    def get(self, provider, model=None):
        return self.adapters[provider]

    def inherited_from(self, name):
        return self.inherited.get(name)


def test_place_and_cost_by_provider():
    mgr = _Mgr({
        "ollama": _Adapter("ollama", "http://127.0.0.1:11434"),
        "fleet-dgx": _Adapter("fleet-dgx", "http://192.168.1.50:4000"),
        "fleet-cloud": _Adapter("fleet-cloud", "https://api.example.com"),
        "codex-cli": _Adapter("codex-cli"),
        "anthropic": _Adapter("anthropic"),
        "openai": _Adapter("openai"),
    }, inherited={"anthropic": "claude-cli"})
    platform = SimpleNamespace(providers=mgr, config=SimpleNamespace(ollama_base_url="", custom_base_url=""))
    facts: dict[str, Any] = {}
    for p in mgr.adapters:
        facts[p] = (T.place_of(platform, p, ""), T.cost_type(platform, p, ""))
    assert facts == {
        "ollama": ("this_pc", "free"),
        "fleet-dgx": ("network", "free"),
        "fleet-cloud": ("cloud", "pay"),
        "codex-cli": ("cloud", "plan"),
        "anthropic": ("cloud", "plan"),  # keyless: served by the Claude Code plan
        "openai": ("cloud", "pay"),
    }
