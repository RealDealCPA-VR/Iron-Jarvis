"""v1.433.0 (Wave 1, A17): every tool can be seen and tuned on the
Permissions screen.

``web_look``, ``pixio`` (the permission of every pixio_* tool) and
``delegate_remote`` had no row in ``core.config.default_permissions``: the
engine fail-closed them to "ask", so they asked every time and the
Permissions screen (which renders that table) could not show or change them.
``web_look`` is the read-only sibling of browse / web_extract (all three still
need computer use switched on), so it is "allow" and sits on the same agent
rosters; pixio and delegate_remote keep "ask" — exactly what their absence
meant — but are now rows a user can tune. The read-only tools say so
(READONLY), like the other read tools; pixio_status saves a file, so it does not.
"""

from __future__ import annotations

import tempfile

import pytest

from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.models import AgentType, PermissionMode
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture(scope="module")
def platform():
    return build_platform(tempfile.mkdtemp(prefix="ij-wave1-perms-"))


def test_the_rows_exist_with_their_tiers():
    perms = default_permissions()
    assert perms["web_look"] == "allow"
    assert perms["pixio"] == "ask"
    assert perms["delegate_remote"] == "ask"


def test_the_engine_resolves_them_from_the_rows():
    engine = PermissionEngine(default_permissions())
    assert engine.mode_for("web_look") == PermissionMode.ALLOW
    assert engine.mode_for("pixio") == PermissionMode.ASK
    assert engine.mode_for("delegate_remote") == PermissionMode.ASK
    # A user's own choice now has a row to change.
    tuned = PermissionEngine({**default_permissions(), "pixio": "deny", "web_look": "ask"})
    assert tuned.mode_for("pixio") == PermissionMode.DENY
    assert tuned.mode_for("web_look") == PermissionMode.ASK


def test_every_registered_tool_resolves_to_a_row(platform):
    perms = default_permissions()
    for name in ("web_look", "pixio_models", "pixio_params", "pixio_status", "pixio_generate",
                 "pixio_upload", "delegate_remote"):
        tool = platform.registry.get(name)
        assert tool is not None, name
        assert tool.perm_key() in perms, (name, tool.perm_key())


def test_the_read_tools_say_they_only_read(platform):
    for name in ("web_look", "pixio_models", "pixio_params"):
        assert platform.registry.get(name).reversibility == Reversibility.READONLY, name
    # The ones that make, send or SAVE something do not (v1.433.0 review:
    # pixio_status saves the finished result into the run's folder).
    for name in ("pixio_generate", "pixio_upload", "pixio_status"):
        assert platform.registry.get(name).reversibility != Reversibility.READONLY, name


def test_web_look_reaches_the_agents_that_browse(platform):
    for agent_type in (AgentType.RESEARCHER, AgentType.AUTOMATION):
        definition = get_agent_definition(agent_type)
        tools = list(definition.tools)
        assert {"browse", "web_extract", "web_look"} <= set(tools), agent_type
        advertised = {s.get("name") for s in platform.registry.specs(tools)}
        assert "web_look" in advertised, agent_type
