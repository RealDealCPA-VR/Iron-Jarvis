"""Read-only web retrieval permission tier (§20). Fully offline.

Confirmed finding: researcher agents and scheduled workflow steps could NEVER
web-search — ``web_search`` sat in the ``ask`` tier, and ``ask`` with no
resolver (headless) fail-closes to deny. Worse, first boot persisted
``web_search = "ask"`` into live installs' config.toml, so a config-default
change alone would not heal them.

These tests prove the fix (``READ_ONLY_WEB_TOOLS`` in tools/permissions.py):

* ``web_search`` + ``web_fetch`` resolve to ALLOW with NO resolver present —
  against the real shipped defaults AND a simulated stale live config.
* Nothing else loosened: write/exec/computeruse tools (shell, secret_set,
  web_action, browser_use, mcp_call, tool_create) keep their gates exactly.
* An explicitly user-DENIED ``web_search`` stays denied — deny always wins,
  through every lift path (session grant, resolver, agent override).
* The real daemon wiring (``headless_ask_resolver``) and the full
  registry.invoke plumbing behave the same end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.core.models import PermissionMode
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import (
    DENY_FLOOR_TOOLS,
    READ_ONLY_WEB_TOOLS,
    PermissionEngine,
    headless_ask_resolver,
)
from iron_jarvis.tools.registry import ToolRegistry
from iron_jarvis.tools.websearch import WebSearchTool

# --- headless allow: the fix ------------------------------------------------


def test_web_search_allowed_headless_with_shipped_defaults():
    # The REAL shipped policy, NO resolver — exactly the researcher/scheduled
    # workflow situation. web_search must resolve allow, not fail-close.
    eng = PermissionEngine(default_permissions())
    decision = eng.authorize("web_search", {"query": "q"})
    assert decision.allowed
    assert decision.mode is PermissionMode.ALLOW
    assert decision.reason == "allowed by policy"


def test_web_fetch_allowed_headless_with_shipped_defaults():
    # web_fetch has no entry in the base policy at all (unknown key). The
    # fail-closed unknown->ask default must NOT apply to a read-only web tool.
    eng = PermissionEngine(default_permissions())
    decision = eng.authorize("web_fetch", {"url": "https://example.com"})
    assert decision.allowed
    assert decision.mode is PermissionMode.ALLOW


def test_stale_persisted_ask_from_live_config_still_allows():
    # First boot wrote ``web_search = "ask"`` into live installs' config.toml
    # (write_default_config dumps the full default_permissions). The engine —
    # not just a fresh default — must upgrade that stale ask to allow.
    eng = PermissionEngine({"web_search": "ask", "web_fetch": "ask"})
    assert eng.authorize("web_search", {}).allowed
    assert eng.authorize("web_fetch", {}).allowed


def test_daemon_headless_resolver_wiring_allows_web_search():
    # Exactly what daemon/app.py + cli.py wire: headless_ask_resolver(). Web
    # retrieval is allowed by POLICY (the resolver is never consulted for it),
    # and the resolver's own allowlist still excludes shell.
    eng = PermissionEngine(default_permissions(), ask_resolver=headless_ask_resolver())
    assert eng.authorize("web_search", {}).allowed
    assert not eng.authorize("shell", {"cmd": "rm -rf /"}).allowed


# --- nothing else loosened --------------------------------------------------


def test_write_and_exec_tiers_still_fail_closed_headless():
    # Ask-tier host/write/action tools stay fail-closed with no resolver.
    eng = PermissionEngine(default_permissions())
    for tool in ("shell", "secret_set", "web_action", "mcp_call", "tool_create"):
        decision = eng.authorize(tool, {})
        assert not decision.allowed, f"{tool} must stay fail-closed headless"
        assert decision.mode is PermissionMode.ASK
        assert "no resolver" in decision.reason
    # And the deny-tier computer-control capability stays hard-denied.
    assert not eng.authorize("browser_use", {}).allowed


def test_unknown_non_web_tool_still_fails_closed():
    # The unknown->ask fail-closed default is untouched for everything else.
    eng = PermissionEngine(default_permissions())
    decision = eng.authorize("mystery_tool", {})
    assert decision.mode is PermissionMode.ASK
    assert not decision.allowed


def test_read_only_web_tier_is_disjoint_from_deny_floor():
    # Invariant guard: a tool can't be both allow-by-default and deny-floored.
    assert not (READ_ONLY_WEB_TOOLS & DENY_FLOOR_TOOLS)


# --- an explicit user deny always wins --------------------------------------


def test_user_denied_web_search_stays_denied():
    # A user denial is stored as ``web_search = "deny"`` in config.toml's
    # [permissions] table -> Config.permissions -> the engine's base policy
    # (platform.py builds PermissionEngine(config.permissions, ...)).
    eng = PermissionEngine({"web_search": "deny", "web_fetch": "deny"})
    decision = eng.authorize("web_search", {})
    assert not decision.allowed
    assert decision.mode is PermissionMode.DENY
    assert not eng.authorize("web_fetch", {}).allowed


def test_user_deny_survives_every_lift_path():
    # A hard deny is never lifted: not by a per-session grant, not by a
    # would-approve resolver (never consulted at DENY), not by an
    # agent-definition override lowering to deny elsewhere raising here.
    eng = PermissionEngine(
        {"web_search": "deny"}, ask_resolver=lambda name, args: True
    )
    assert not eng.authorize("web_search", {}, session_allow=["web_search"]).allowed
    # An agent-definition deny override on an otherwise-allowed web tool also
    # holds (lowering is always permitted; the upgrade only touches ASK).
    eng_default = PermissionEngine(default_permissions())
    assert not eng_default.authorize(
        "web_search", {}, {"web_search": "deny"}
    ).allowed


# --- end-to-end through the registry (the runtime's invoke path) ------------

_DDG_ONE_RESULT = """
<html><body>
<div class="result"><h2 class="result__title">
  <a rel="nofollow" class="result__a" href="https://www.python.org/">Python</a>
</h2>
<a class="result__snippet" href="x">The official home of Python.</a></div>
</body></html>
"""


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "whp.db"))
    init_db(engine)
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


async def test_headless_registry_invoke_web_search_succeeds(ctx):
    # The exact runtime plumbing (registry.invoke -> perms.authorize -> execute)
    # with the shipped defaults and NO resolver — offline via an injected fetch.
    registry = ToolRegistry()
    tool = WebSearchTool(http_get=lambda url, params: _FakeResp(_DDG_ONE_RESULT))
    registry.register(tool)
    perms = PermissionEngine(default_permissions())

    res = await registry.invoke("web_search", {"query": "python"}, ctx, perms)
    assert res.ok
    assert res.data["count"] == 1


async def test_headless_registry_invoke_respects_user_deny(ctx):
    registry = ToolRegistry()
    tool = WebSearchTool(http_get=lambda url, params: _FakeResp(_DDG_ONE_RESULT))
    registry.register(tool)
    perms = PermissionEngine({"web_search": "deny"})

    res = await registry.invoke("web_search", {"query": "python"}, ctx, perms)
    assert not res.ok
    assert "permission denied" in (res.error or "")
