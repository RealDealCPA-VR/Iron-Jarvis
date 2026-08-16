"""v1.178.0 — every registered tool has a PERMISSION, or says why not.

THE SECOND HALF OF THE SAME DISEASE. `tests/test_roster_coverage_v1178.py`
pins that a capability reaches an agent's roster. This pins that it can then be
CALLED. Both were found the same way — by a live job failing — and the second
was hiding under the first:

* `worklist_add/next/done/status` (v1.177.0) — the durable checkpointing built
  to make a 26-file rename survivable. No permission entry, so the engine
  fail-closed them to "ask", and `headless_ask_resolver` auto-approves only
  `delegate`/`spawn_agent`. Every agent run was DENIED all four. The measured
  run showed ZERO worklist calls and the planner took the blame.
* `rename_file` (v1.177.2) — the tool built for that exact job. Same.
* `view_image` (v1.174.0) — put on every roster as "eyes for any agent",
  denied in every headless run for want of one line.

An absent key is not a neutral default here. It reads as "ask", which is
correct and safe for a human at a keyboard and means DENIED for the lane that
does the work. So absence must be a DECISION, written down, not an oversight —
which is exactly what this test enforces.
"""

from __future__ import annotations

import tempfile

import pytest

from iron_jarvis.core.config import default_permissions
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.permissions import (
    DENY_FLOOR_TOOLS,
    SAFE_HEADLESS_TOOLS,
    PermissionEngine,
)

#: Permission keys deliberately left ABSENT, each with the reason. Absent means
#: the engine fail-closes to "ask" — a human is asked, a headless run is denied.
#: Adding a tool here is a decision to say "this must never run unattended".
_ABSENT_BY_DESIGN = {
    # v1.170.0 pinned this: an entry would make the Settings display imply a
    # configured choice nobody made. See test_workflow_tools_v1170.
    "workflow_run": "P3 pinned design — listing is allowed, running asks",
    # Sends the task (and its context) to a machine that is not this one. The
    # local `delegate` is already "ask"; going off-box is not less than that.
    "delegate_remote": "leaves this machine",
    # Costs real money per call.
    "pixio": "paid media generation",
    # Computer use is opt-in by design (README: gated, DOM-first, human
    # approval for risky actions).
    "web_look": "computer-use surface, opt-in by design",
}

#: Tools whose capability is the reason they ask. Pinned so a future "make the
#: agent smoother" pass cannot quietly promote one to allow.
_MUST_NEVER_BE_ALLOW = DENY_FLOOR_TOOLS | {"tool_create", "tool_delete", "create_agent"}


@pytest.fixture(scope="module")
def platform():
    return build_platform(tempfile.mkdtemp())


def _perm_key(registry, name: str) -> str:
    tool = registry._tools.get(name)  # noqa: SLF001 — the registry's own map
    return tool.perm_key() if hasattr(tool, "perm_key") else name


def test_every_registered_tool_has_a_permission_or_a_documented_reason(platform):
    """The test that would have caught all three incidents above."""
    perms = default_permissions()
    gaps = []
    for name in sorted(platform.registry.names()):
        key = _perm_key(platform.registry, name)
        if key in perms or key in _ABSENT_BY_DESIGN:
            continue
        # A custom:* tool is created at runtime under its own key and defaults
        # to ask on purpose (agents/tool_create); it is not a shipped tool.
        if key.startswith("custom:") or key.startswith("mcp"):
            continue
        gaps.append(f"{name} (permission key {key!r})")
    assert not gaps, (
        "these tools are registered and reachable, and have NO permission "
        "entry - so the engine fail-closes them to 'ask', which for a HEADLESS "
        "agent run means DENIED. That is how the worklist (v1.177.0), "
        "rename_file (v1.177.2) and view_image (v1.174.0) each shipped unable "
        "to run in the lane they were built for. Either give the tool an "
        "entry in core/config.default_permissions, or add its key to "
        "_ABSENT_BY_DESIGN here with the reason it must ask.\n  "
        + "\n  ".join(gaps)
    )


def test_the_capabilities_the_folder_job_needs_are_actually_callable(platform):
    """END TO END on the permission side: the acceptance job's tools resolve to
    ALLOW, so a headless run can call them. Named individually because each one
    was, at some point, silently denied."""
    engine = PermissionEngine(default_permissions())
    needed = [
        "read_file",        # always worked — the control
        "rename_file",      # v1.177.2, denied until v1.178.0
        "worklist_add",     # v1.177.0, denied until v1.178.0
        "worklist_next",
        "worklist_done",
        "worklist_status",
        "images",           # view_image's key; v1.174.0, denied until v1.178.0
        "batch_documents",
        "list_folder",
        "read_document",
        "extract_pdf",
    ]
    denied = [
        name
        for name in needed
        if not engine.authorize(name, {}).allowed
    ]
    assert not denied, f"the folder job cannot call: {denied}"


def test_a_headless_run_can_reach_them(platform):
    """The specific mechanism that turned 'ask' into 'denied'. `ask` is the
    right answer for a person at a keyboard; this asserts the job's tools do
    not depend on one being there."""
    from iron_jarvis.tools.permissions import headless_ask_resolver

    resolver = headless_ask_resolver()
    engine = PermissionEngine(default_permissions())
    for name in ("rename_file", "worklist_next", "images", "batch_documents"):
        decision = engine.authorize(name, {})
        assert decision.allowed, f"{name} is not allowed outright"
        # ...and it never had to fall through to the resolver, which would have
        # said no: only delegate/spawn_agent are auto-approved headless.
        assert not resolver(name, {}), (
            f"{name} is in SAFE_HEADLESS_TOOLS - if that is intended, this "
            "test's reasoning needs updating"
        )
    assert SAFE_HEADLESS_TOOLS == frozenset({"delegate", "spawn_agent"})


def test_the_dangerous_tools_did_not_get_swept_up(platform):
    """This wave grants a lot. Pin the floor so 'make the agent smoother' can
    never quietly include shell."""
    perms = default_permissions()
    for name in sorted(_MUST_NEVER_BE_ALLOW):
        assert perms.get(name, "ask") != "allow", f"{name} was promoted to allow"


def test_granting_a_tool_is_visible_in_the_settings_table(platform):
    """A permission the user cannot see is one they cannot revoke. Every key we
    granted this wave is a real entry (not an absence), so it renders."""
    perms = default_permissions()
    for name in (
        "rename_file",
        "worklist_add",
        "worklist_next",
        "worklist_done",
        "worklist_status",
        "images",
        "batch_documents",
        "convert_document",
        "list_folder",
    ):
        assert name in perms, f"{name} is granted by absence, not by an entry"
