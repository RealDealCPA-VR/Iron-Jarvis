"""v1.502.0 (north-star plan 2.9): "Run app tools without asking" never lifts
a LOCKED app.

THE DEFECT. With ``mcp_auto_approve`` on (or any pack's own auto-approve
flag), ``platform.build_platform`` wraps the ask resolver so EVERY
``mcp_call`` question is answered yes — every pack shares that one key. A
``locked`` pack (Microsoft 365) refuses its own auto-approve flag
(``pack_grants_auto``, the 409 ``LOCKED_ASKS``) and its card says it asks
before each use, but the global switch, or ANOTHER pack's flag, still opened
it.

THE RULE. The wrapped resolver answers False for a tool whose pack is locked,
read from the same in-flight seam the quarantine reads
(``mcp/tools._ASKING_ABOUT``, set by the tool's own ``perm_key``). Proved
through the real ``registry.invoke`` -> ``PermissionEngine.authorize`` -> the
platform's resolver; only the pack's transport is an in-memory double.
"""

from __future__ import annotations

from iron_jarvis.core.config import resolve_home
from iron_jarvis.mcp.tools import mcp_tools
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.permissions import headless_ask_resolver
from tests.test_mcp_quarantine_v1299 import ECHO, _fake_cfg, _invoke


def _platform(tmp_path, *, toml: str):
    root = tmp_path / "root"
    root.mkdir()
    home = resolve_home(root)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(toml, encoding="utf-8")
    platform = build_platform(str(root), ask_resolver=headless_ask_resolver())
    for cfg in (
        {**_fake_cfg("plainpack", [ECHO])},
        {**_fake_cfg("lockedpack", [ECHO]), "locked": "ms365"},
    ):
        for tool in mcp_tools([cfg], record=False):
            platform.registry.register(tool)
    return platform


def test_the_global_switch_runs_an_ordinary_pack_but_never_a_locked_one(tmp_path):
    platform = _platform(tmp_path, toml="mcp_auto_approve = true\n")
    assert platform.registry.get("mcp__lockedpack__echo").locked is True
    # Control: the switch is on, so an ordinary pack's tool runs unasked.
    ok = _invoke(platform, tmp_path, "mcp__plainpack__echo", {"text": "hi"})
    assert ok.ok is True, ok.error
    # The locked pack's tool: the RESOLVER says no (not the tool's own
    # chat-only refusal, which would come after a yes).
    held = _invoke(platform, tmp_path, "mcp__lockedpack__echo", {"text": "hi"})
    assert held.ok is False
    assert held.error.startswith("permission denied"), held.error
    # The seam is clean afterwards: a bare mcp_call question keeps the switch.
    assert platform.permissions.authorize("mcp_call", {}).allowed is True


def test_with_the_switch_off_nothing_changes(tmp_path):
    platform = _platform(tmp_path, toml="mcp_auto_approve = false\n")
    held = _invoke(platform, tmp_path, "mcp__plainpack__echo", {"text": "hi"})
    assert held.ok is False and held.error.startswith("permission denied"), held.error
