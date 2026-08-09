"""A refusal is never attributed to someone who was never asked (v1.154.2).

Reported from a live redaction turn: the tool strip showed ``shell · 1 failed``
with the reason "rejected by user" — and no prompt had appeared. Nothing was
broken about the DECISION: ``shell`` is an ``ask`` tool, the daemon installs
:func:`headless_ask_resolver` (a fixed allowlist with no human attached), and
fail-closed denial is exactly the intended safety behaviour.

The lie was the REASON. ``authorize`` labelled every refusal "rejected by user"
regardless of who — or what — refused, and since that resolver is the only one
this application ever installs, the message was ALWAYS false. It told the user
they had declined something they were never shown, and sent them looking for a
prompt that had never existed.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.models import PermissionMode
from iron_jarvis.tools.permissions import (
    SAFE_HEADLESS_TOOLS,
    PermissionEngine,
    headless_ask_resolver,
)


def _engine(**policy):
    return PermissionEngine(policy or {"shell": "ask"}, ask_resolver=headless_ask_resolver())


# --------------------------------------------------------------------------- #
# (1) THE REPORTED CASE.
# --------------------------------------------------------------------------- #
def test_a_headless_refusal_is_not_blamed_on_the_user():
    decision = _engine().authorize("shell", {})
    assert decision.allowed is False
    assert "rejected by user" not in decision.reason


def test_the_refusal_says_what_actually_happened():
    reason = _engine().authorize("shell", {}).reason
    assert "needs approval" in reason
    assert "nothing here could ask" in reason


def test_the_refusal_says_how_to_allow_it():
    """A dead end is not an error message. Both real routes are named."""
    reason = _engine().authorize("shell", {}).reason
    assert "allow_tools" in reason, "the per-task grant is not mentioned"
    assert "Settings" in reason, "the permanent setting is not mentioned"


# --------------------------------------------------------------------------- #
# (2) THE DECISION ITSELF IS UNCHANGED — this was only ever about wording.
# --------------------------------------------------------------------------- #
def test_shell_is_still_denied():
    """Fail-closed is the point. Reword the refusal, never relax it."""
    assert _engine().authorize("shell", {}).allowed is False


@pytest.mark.parametrize("tool", sorted(SAFE_HEADLESS_TOOLS))
def test_the_safe_allowlist_still_passes(tool):
    assert _engine(**{tool: "ask"}).authorize(tool, {}).allowed is True


def test_an_auto_approval_is_not_credited_to_the_user_either():
    """The symmetric half: nobody approved these, so the record should not say
    a user did."""
    decision = _engine(delegate="ask").authorize("delegate", {})
    assert decision.allowed is True
    assert "user" not in decision.reason


def test_an_explicit_deny_is_unchanged():
    d = _engine(shell="deny").authorize("shell", {})
    assert d.allowed is False and d.reason == "denied by policy"


def test_an_explicit_allow_is_unchanged():
    d = _engine(shell="allow").authorize("shell", {})
    assert d.allowed is True and d.reason == "allowed by policy"


def test_a_session_grant_still_lifts_the_ask():
    d = _engine().authorize("shell", {}, session_allow=["shell"])
    assert d.allowed is True
    assert d.reason == "granted for this task"


def test_no_resolver_at_all_still_reports_headless():
    engine = PermissionEngine({"shell": "ask"}, ask_resolver=None)
    d = engine.authorize("shell", {})
    assert d.allowed is False
    assert "headless" in d.reason


# --------------------------------------------------------------------------- #
# (3) A REAL INTERACTIVE RESOLVER KEEPS THE HONEST WORDING.
# --------------------------------------------------------------------------- #
def test_a_genuine_user_rejection_still_says_so():
    """When someone IS asked and says no, that is exactly what to record — the
    wording change must not erase a real decision."""

    def human_says_no(_tool, _args):
        return False

    engine = PermissionEngine({"shell": "ask"}, ask_resolver=human_says_no)
    d = engine.authorize("shell", {})
    assert d.allowed is False
    assert d.reason == "rejected by user"


def test_a_genuine_user_approval_still_says_so():
    def human_says_yes(_tool, _args):
        return True

    engine = PermissionEngine({"shell": "ask"}, ask_resolver=human_says_yes)
    d = engine.authorize("shell", {})
    assert d.allowed is True
    assert d.reason == "approved by user"


def test_the_headless_resolver_is_marked_non_interactive():
    """The marker is what tells the engine apart from a human. A resolver
    without it is assumed interactive, so an integrator's own approver keeps
    the truthful wording by default."""
    assert getattr(headless_ask_resolver(), "interactive", True) is False


def test_the_mode_is_still_reported_as_ask():
    """The refusal is about the ANSWER, not the policy: the tool is still an
    ``ask`` tool, and a caller offering to fix it needs to know that."""
    assert _engine().authorize("shell", {}).mode is PermissionMode.ASK


def test_the_marker_survives_the_mcp_auto_approve_wrapper(tmp_path):
    """`mcp_auto_approve` wraps the resolver in a new function. Without carrying
    the marker across, flipping an unrelated setting resurrected the false
    "rejected by user" message this release removes."""
    from iron_jarvis.platform import build_platform

    # The flag is read from config AT BUILD TIME, so it has to be on disk
    # before the platform is constructed. Setting it on an already-built
    # platform and rebuilding proves nothing — the first version of this test
    # did exactly that and passed without ever reaching the wrapper.
    (tmp_path / ".ironjarvis").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ironjarvis" / "config.toml").write_text(
        "mcp_auto_approve = true\n", encoding="utf-8"
    )
    platform = build_platform(str(tmp_path), ask_resolver=headless_ask_resolver())
    assert platform.config.mcp_auto_approve is True, "the flag never took effect"

    resolver = platform.permissions._ask_resolver
    assert resolver("mcp_call", {}) is True, "the wrapper is not in place"
    assert getattr(resolver, "interactive", True) is False, (
        "the wrapper lost the non-interactive marker"
    )
    assert platform.permissions.authorize("shell", {}).reason != "rejected by user"
