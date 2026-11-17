"""Owner decision 8 after the third fix plan: the tool gate reads the LOCK.

A locked app (Microsoft 365 today) promises "Jarvis asks you before each use
of it". Until now that rested on every locked app ALSO being kept on the
user's own models (``local_only``): the chat's Local only card was what asked,
and nothing in the gate read the lock apart from the "Run app tools without
asking" switch. A locked app that was NOT kept on the user's own models would
have run, with the app switched on for a chat, with no card at all.

Now the gate holds a locked app's tool exactly as it holds a held-back
(quarantined) one: no blanket name grant, no "for this run" grant on the
shared key, no arming, no ``allow`` override, no standing grant and no
approval level lifts it; only a card answered about THAT tool does
(``card_allow``). With nobody to ask it is refused in words.

Proved through the REAL ``registry.invoke`` -> ``PermissionEngine.authorize``
-> the platform's resolver, with the app built by the real loader
(``mcp/tools.mcp_tools`` from a ``locked`` row). Only the app's transport is an
in-memory double.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from iron_jarvis.core import auto_approve as A
from iron_jarvis.core.config import resolve_home
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import args_hash
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.mcp.tools import mcp_tools
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.permissions import asks_each_use, headless_ask_resolver
from tests.test_mcp_quarantine_v1299 import ECHO, LIST_ITEMS, _ctx, _fake_cfg

LOCKED = "lockedapp"
PLAIN = "plainapp"
NAME = f"mcp__{LOCKED}__echo"
READ = f"mcp__{LOCKED}__list_items"
SIBLING = f"mcp__{PLAIN}__echo"


def _platform(tmp_path, toml: str = ""):
    root = tmp_path / "root"
    root.mkdir()
    home = resolve_home(root)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(toml, encoding="utf-8")
    platform = build_platform(str(root), ask_resolver=headless_ask_resolver())
    # A LOCKED row that is NOT kept on the user's own models: the case the
    # old gate never asked about (the catalog ties the two today).
    for cfg in (
        {**_fake_cfg(LOCKED, [ECHO, LIST_ITEMS]), "locked": "ms365"},
        _fake_cfg(PLAIN, [ECHO]),
    ):
        for tool in mcp_tools([cfg], record=False):
            platform.registry.register(tool, mcp=True)
    tool = platform.registry.get(NAME)
    assert tool.locked is True and tool.local_only is False and tool.quarantined is False
    return platform


def _run(platform, tmp_path, name: str = NAME, **kw):
    return asyncio.run(
        platform.registry.invoke(name, {"text": "hi"}, _ctx(platform, tmp_path), platform.permissions, **kw)
    )


def _armed(*names: str) -> dict:
    """What a chat lane hands the gate with the app switched on: each of its
    tools armed by name (an ``allow`` override and a session grant), plus the
    shared key."""
    armed = {*names, "mcp_call"}
    return {
        "agent_overrides": {n: "allow" for n in armed},
        "session_allow": set(armed),
        "allowed_names": set(armed),
    }


@pytest.fixture()
def platform(tmp_path):
    return _platform(tmp_path)


# --------------------------------------------------------------------------- #
# The chat-shaped call: the app switched on, no card about this call.
# --------------------------------------------------------------------------- #
def test_a_chat_with_the_locked_app_switched_on_never_runs_it_without_a_card(platform, tmp_path):
    out = _run(platform, tmp_path, **_armed(NAME, READ), card_allow=frozenset())
    assert out.ok is False, "a locked app's tool ran with the app only switched on"
    assert out.error.startswith("permission denied"), out.error
    # Refused THROUGH the ledger, so the outcome says nothing ran.
    with session_scope(platform.engine) as db:
        (row,) = [r for r in db.exec(select(ToolInvocation)) if r.tool == NAME]
    assert row.ok is False


def test_reading_counts_as_a_use_too(platform, tmp_path):
    out = _run(platform, tmp_path, READ, **_armed(NAME, READ), card_allow=frozenset())
    assert out.ok is False, out.output


def test_the_card_answered_about_that_call_runs_it(platform, tmp_path):
    out = _run(platform, tmp_path, **_armed(NAME, READ), card_allow=frozenset({NAME}))
    assert out.ok is True, out.error
    assert out.output.startswith("echo:")


@pytest.mark.parametrize(
    "answers",
    [frozenset({"mcp_call"}), frozenset({SIBLING, "mcp_call"}), frozenset({READ})],
    ids=["the-shared-key", "another-apps-card", "a-sibling-tools-card"],
)
def test_no_other_answer_lifts_it(platform, tmp_path, answers):
    out = _run(platform, tmp_path, **_armed(NAME, READ), card_allow=answers)
    assert out.ok is False, f"{sorted(answers)} ran the locked app's tool"


# --------------------------------------------------------------------------- #
# Blanket answers: a run's grant on the shared key, an allow, a standing grant.
# --------------------------------------------------------------------------- #
def test_an_allow_on_the_shared_key_and_a_run_grant_on_it_lift_nothing(platform, tmp_path):
    """An agent's definition allowing app tools, or "Allow for this run" on
    another app's card (the shared key ``mcp_call``), is not an answer about
    THIS tool."""
    out = _run(
        platform, tmp_path,
        agent_overrides={"mcp_call": "allow"}, session_allow={"mcp_call"},
    )
    assert out.ok is False, "a blanket allow on the shared key ran a locked app's tool"


def test_a_standing_grant_never_lifts_it(platform, tmp_path):
    store = platform.grants
    store.create("chat", "chat", NAME, args_hash(NAME, {"text": "hi"}), "echo hi")
    out = _run(platform, tmp_path, grants=store, scopes=[("chat", "chat")], card_allow=frozenset())
    assert out.ok is False, "a standing grant ran a locked app's tool"
    # Control: the same grant for an ordinary app's tool lifts it.
    store.create("chat", "chat", SIBLING, args_hash(SIBLING, {"text": "hi"}), "echo hi")
    ok = _run(platform, tmp_path, SIBLING, grants=store, scopes=[("chat", "chat")])
    assert ok.ok is True, ok.error


def test_the_run_app_tools_without_asking_switch_never_lifts_it(tmp_path):
    platform = _platform(tmp_path, toml="mcp_auto_approve = true\n")
    assert _run(platform, tmp_path, SIBLING).ok is True, "control: the switch runs an ordinary app"
    out = _run(platform, tmp_path, session_allow={"mcp_call"})
    assert out.ok is False
    # The seam is clean afterwards: a bare question keeps the switch.
    assert platform.permissions.authorize("mcp_call", {}).allowed is True


# --------------------------------------------------------------------------- #
# Nobody to ask: refused in words.
# --------------------------------------------------------------------------- #
def test_with_nobody_to_ask_it_is_refused_in_words(platform, tmp_path):
    """An unattended caller (a schedule's run, a workflow step) that hands no
    card answers: the gate asks the resolver, which has nobody to ask."""
    out = _run(platform, tmp_path, agent_overrides={"mcp_call": "allow"}, session_allow={"mcp_call"})
    assert out.ok is False
    assert "needs approval and nothing here could ask you" in out.error, out.error


# --------------------------------------------------------------------------- #
# The one predicate, and the approval levels.
# --------------------------------------------------------------------------- #
def test_one_predicate_names_every_tool_that_asks_before_each_use(platform):
    assert asks_each_use(platform.registry.get(NAME)) is True
    assert asks_each_use(platform.registry.get(READ)) is True
    assert asks_each_use(platform.registry.get(SIBLING)) is False
    sibling = platform.registry.get(SIBLING)
    sibling.quarantined = True
    try:
        assert asks_each_use(sibling) is True
    finally:
        sibling.quarantined = False
    assert asks_each_use(None) is False


@pytest.mark.parametrize("mode", [A.ACCEPT_EDITS, A.AUTO, A.ASK_FIRST])
def test_no_approval_level_passes_a_locked_apps_tool(platform, mode):
    """Accept edits and Auto keep the card for a locked app's tool, reading
    included, and say why in plain words."""
    v = A.classify(READ, {}, mode=mode, tool=platform.registry.get(READ))
    assert v.kind == A.ASK and v.rule == "locked", v
    assert A.rule_words("locked") == A.ASK_RULES["locked"]
    assert "asks before each use" in A.post_lane_refusal(v)
