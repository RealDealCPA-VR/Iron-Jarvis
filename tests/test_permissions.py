from __future__ import annotations

from iron_jarvis.core.models import PermissionMode
from iron_jarvis.tools.permissions import PermissionEngine


def test_allow_and_deny():
    eng = PermissionEngine({"write_file": "allow", "delete_file": "deny"})
    assert eng.authorize("write_file", {}).allowed
    assert not eng.authorize("delete_file", {}).allowed


def test_ask_is_fail_closed_without_resolver():
    eng = PermissionEngine({"shell": "ask"})
    decision = eng.authorize("shell", {})
    assert decision.mode is PermissionMode.ASK
    assert not decision.allowed


def test_ask_with_resolver():
    eng = PermissionEngine({"shell": "ask"}, ask_resolver=lambda name, args: True)
    assert eng.authorize("shell", {}).allowed

    eng_no = PermissionEngine({"shell": "ask"}, ask_resolver=lambda name, args: False)
    assert not eng_no.authorize("shell", {}).allowed


def test_unknown_tool_defaults_to_ask_and_denies():
    eng = PermissionEngine({})
    decision = eng.authorize("mystery_tool", {})
    assert decision.mode is PermissionMode.ASK
    assert not decision.allowed


def test_agent_override_takes_precedence_for_non_floor_tools():
    # Non-floor tools keep the original precedence: an agent override outranks the
    # base policy (here raising an "ask" tool to "allow").
    eng = PermissionEngine({"secret_set": "ask"})
    assert eng.authorize("secret_set", {}, {"secret_set": "allow"}).allowed


def test_agent_override_cannot_raise_deny_floor_to_allow():
    # F1/F2 deny-floor: an agent-definition override must NEVER raise a
    # host-touching tool to "allow". A base "ask" floor tool overridden to "allow"
    # is dropped back to the (fail-closed) base — not allowed without a resolver.
    eng = PermissionEngine({"shell": "ask"})
    decision = eng.authorize("shell", {}, {"shell": "allow"})
    assert decision.mode is PermissionMode.ASK
    assert not decision.allowed
    # A base DENY floor tool stays denied regardless of the override.
    eng_deny = PermissionEngine({"browser_use": "deny"})
    assert not eng_deny.authorize("browser_use", {}, {"browser_use": "allow"}).allowed
    # web_action isn't even in the base policy → the raise attempt fails closed.
    eng_bare = PermissionEngine({})
    d = eng_bare.authorize("web_action", {}, {"web_action": "allow"})
    assert d.mode is PermissionMode.ASK and not d.allowed


def test_agent_override_may_still_lower_a_floor_tool():
    # The floor only blocks RAISING to allow; an override may keep/lower to
    # ask/deny. Here a resolver would say yes, but the override lowers to deny.
    eng = PermissionEngine({"shell": "ask"}, ask_resolver=lambda name, args: True)
    assert not eng.authorize("shell", {}, {"shell": "deny"}).allowed


def test_floor_tool_grantable_via_session_allow():
    # The sanctioned per-task grant path still lifts an "ask" floor tool for one
    # task (this is what chat/task arming should route floor tools through)...
    eng = PermissionEngine({"shell": "ask"})
    assert eng.authorize("shell", {}, session_allow=["shell"]).allowed
    # ...but a base DENY is a hard floor even session_allow can't lift.
    eng_deny = PermissionEngine({"browser_use": "deny"})
    assert not eng_deny.authorize(
        "browser_use", {}, session_allow=["browser_use"]
    ).allowed
