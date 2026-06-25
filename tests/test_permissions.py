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


def test_agent_override_takes_precedence():
    eng = PermissionEngine({"shell": "deny"})
    assert eng.authorize("shell", {}, {"shell": "allow"}).allowed
