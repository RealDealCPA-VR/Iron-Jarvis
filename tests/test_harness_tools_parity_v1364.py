"""v1.364.0 (IJ harness Phase 1): every tool Claude Code and Codex give their
models is SERVED by a Jarvis tool, planned for a LATER phase, or NOT NEEDED
with a reason — checked against the vendors' real inventory, not memory.

The inventory (``tests/fixtures/harness_tool_inventory_v1364.json``) was read
from the installed Claude Code 2.1.306 binary and docs and from Codex
0.157.0's tagged source on 2026-10-21. ``harness/parity.py`` holds the
verdicts. Then every registration a new built-in owes (CLAUDE.md): a
permission row, the deny floor, LOW_TRUST_DENY, the egress class, the
registry's mutating list, the run-log words (Python + dashboard twins), the
card words (card == activity log == desktop toast), the object schema, no
roster and no automatic arming.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from iron_jarvis.agents import mission
from iron_jarvis.agents.types import AgentType, get_agent_definition
from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.detections.ledger import COMMAND_TOOLS, FILE_WRITE_TOOLS, to_events
from iron_jarvis.harness import parity
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import egress
from iron_jarvis.tools import registry as R
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools
from iron_jarvis.tools.base import Reversibility
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS, PermissionEngine

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = json.loads((ROOT / "tests" / "fixtures" / "harness_tool_inventory_v1364.json").read_text(encoding="utf-8"))
NEW = parity.PHASE_1_TOOLS
ASKS = ("process_start", "process_write")
EDITORS = ("multi_edit", "apply_patch", "notebook_edit")


@pytest.fixture(scope="module")
def platform():
    return build_platform(tempfile.mkdtemp())


# --------------------------------------------------------------------------- #
# Parity with the inventory
# --------------------------------------------------------------------------- #


def test_the_inventory_fixture_is_the_researched_one():
    assert INVENTORY["claude_code"]["version"].startswith("2.1.306")
    assert "0.157.0" in INVENTORY["codex"]["version"]
    assert len(INVENTORY["claude_code"]["tools"]) == 46
    assert len(INVENTORY["codex"]["tools"]) == 43


@pytest.mark.parametrize("vendor", ["claude_code", "codex"])
def test_every_inventory_tool_has_a_verdict(vendor):
    """MUTATION: delete a row from parity.CLAUDE_CODE -> red."""
    names = [t["name"] for t in INVENTORY[vendor]["tools"]]
    rows = parity.table()[vendor]
    missing = [n for n in names if n not in rows]
    assert missing == [], f"{vendor} tools with no verdict: {missing}"
    stale = sorted(set(rows) - set(names))
    assert stale == [], f"rows for tools the inventory does not list: {stale}"


def test_every_verdict_is_complete():
    for vendor, rows in parity.table().items():
        for name, row in rows.items():
            assert row.verdict in parity.VERDICTS, (vendor, name)
            if row.verdict == parity.SERVED:
                assert row.jarvis, (vendor, name)
            else:
                assert len(row.reason) > 20, (vendor, name, "a not-served row says why")


def test_every_served_tool_is_registered(platform):
    """A SERVED row naming a tool the registry does not hold is a claim the
    app cannot back."""
    registered = set(platform.registry.names())
    missing = sorted(parity.served_tools() - registered)
    assert missing == [], missing


def test_the_plans_named_capabilities_are_served_by_the_new_tools():
    cc, cx = parity.CLAUDE_CODE, parity.CODEX
    assert cc["Edit"].jarvis == ("edit_file",)
    assert cc["Glob"].jarvis == ("glob",)
    assert cc["NotebookEdit"].jarvis == ("notebook_edit",)
    assert cc["TodoWrite"].jarvis == ("plan_update",) and cx["update_plan"].jarvis == ("plan_update",)
    assert cx["apply_patch"].jarvis == ("apply_patch",)
    assert set(cx["write_stdin"].jarvis) == {"process_write", "process_read"}
    assert cc["ReadMcpResourceTool"].jarvis == ("mcp_read_resource",)
    # v1.367.0 (Phase 4, moved on purpose): the LATER rows are served now.
    assert cc["AskUserQuestion"].jarvis == ("ask_user",) and cx["request_user_input"].jarvis == ("ask_user",)
    assert cc["ExitPlanMode"].jarvis == ("exit_plan_mode",)
    assert cc["EnterPlanMode"].verdict == parity.SERVED
    assert not [n for rows in parity.table().values() for n, r in rows.items() if r.verdict == parity.LATER]


# --------------------------------------------------------------------------- #
# Registered, schematised, never armed by themselves
# --------------------------------------------------------------------------- #


def test_the_new_tools_are_registered_with_object_schemas(platform):
    for name in NEW:
        tool = platform.registry.get(name)
        assert tool is not None, name
        schema = tool.spec()["input_schema"]
        assert schema.get("type") == "object" and schema.get("properties"), name
        assert "parameters" not in vars(type(tool)), name


def test_no_roster_carries_them_and_no_chat_rule_arms_them():
    for agent_type in AgentType:
        assert not (set(get_agent_definition(agent_type).tools) & set(NEW)), agent_type
    assert not (set(NEW) & AUTO_SAFE_TOOLS)
    for sentence in (
        "apply this patch to the repo", "glob for every python file", "start the dev server and watch it",
        "update the plan", "edit the notebook cell", "list the resources from my apps",
    ):
        assert not (set(select_auto_tools(sentence)) & set(NEW)), sentence


# --------------------------------------------------------------------------- #
# Permissions, floor, trust, egress, mutation
# --------------------------------------------------------------------------- #


def test_every_new_tool_has_a_permission_row_at_its_tier(platform):
    perms = default_permissions()
    for name in NEW:
        key = platform.registry.get(name).perm_key()
        assert key in perms, name
    for name in ASKS:
        assert perms[name] == "ask"
    for name in set(NEW) - set(ASKS):
        assert perms[name] == "allow", name
    assert perms["shell"] == "ask", "the control: process_start sits at shell's tier"


def test_start_and_write_sit_on_shells_deny_floor():
    """MUTATION: drop process_start from DENY_FLOOR_TOOLS -> an agent
    definition's allow override is honoured -> red."""
    engine = PermissionEngine(default_permissions())
    for name in ASKS:
        assert name in DENY_FLOOR_TOOLS
        assert engine.mode_for(name, {name: "allow"}).value == "ask"
    assert "shell" in DENY_FLOOR_TOOLS
    assert "process_read" not in DENY_FLOOR_TOOLS and "process_stop" not in DENY_FLOOR_TOOLS


def test_low_trust_keeps_away_starting_and_typing_but_not_stopping():
    for name in ASKS:
        assert name in LOW_TRUST_DENY
    # A run tainted mid-way must still be able to stop what it started.
    assert "process_stop" not in LOW_TRUST_DENY and "process_read" not in LOW_TRUST_DENY
    for name in (*EDITORS, "glob", "plan_update"):
        assert name not in LOW_TRUST_DENY, "edit_file's own posture"


def test_the_egress_class_of_each(platform):
    assert set(ASKS) <= egress.RUNS_COMMANDS_TOOLS
    reg = platform.registry
    for name in ASKS + ("mcp_read_resource",):
        assert egress.reaches_outside(reg.get(name), reg) is True, name
    for name in ("process_read", "process_stop", "glob", "plan_update", "mcp_list_resources", *EDITORS):
        assert egress.reaches_outside(reg.get(name), reg) is False, name


def test_the_writers_invalidate_the_read_cache():
    for name in (*EDITORS, *ASKS):
        assert name in R._MUTATING_TOOLS, name


def test_reversibility_and_replay(platform):
    for name in EDITORS:
        assert platform.registry.get(name).reversibility is Reversibility.REVERSIBLE, name
    for name in ("glob", "plan_update", "mcp_list_resources", "mcp_read_resource", "process_read"):
        assert platform.registry.get(name).reversibility is Reversibility.READONLY, name
    for name in ASKS:
        assert platform.registry.get(name).reversibility is Reversibility.IRREVERSIBLE, name


# --------------------------------------------------------------------------- #
# Words: the run log, the card, the desktop toast, the low-trust hover
# --------------------------------------------------------------------------- #


def _ts(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_new_tool_has_run_log_words_in_python_and_the_dashboard():
    worded = set(mission._RUN_TOOL_WORDS) | set(mission._STEP_KIND) | set(mission._TOOL_WORDS)
    for name in NEW:
        assert name in worded, name
        line = mission.tool_line(name, {}, True)
        assert "_" not in line and not line.startswith("used "), (name, line)
        assert not mission.do_words(name).startswith("use "), name
    for did, _noun in mission._RUN_TOOL_WORDS.values():
        assert did.split(" ", 1)[0] in mission._PRESENT
    run_words = _ts("dashboard/lib/runWords.ts")
    kinds = _ts("dashboard/lib/workTarget.ts")
    for name in NEW:
        assert re.search(rf"^\s+{name}: \{{ did:", run_words, re.M) or re.search(rf"^\s+{name}: \"", kinds, re.M), name


def test_the_card_words_are_one_table_in_three_places():
    toolwords = _ts("dashboard/lib/toolWords.ts")
    desktop = _ts("desktop/main.js")
    fixture = json.loads(_ts("dashboard/__tests__/fixtures/desktop-tool-words-v13580.json"))["rows"]
    for name in (*ASKS, "mcp_read_resource"):
        words = mission.ASK_WORDS[name]
        assert f'  {name}: "{words}",' in toolwords, name
        assert f'  {name}: "{words}",' in desktop, name
        assert {"tool": name, "kind": "table", "card": words, "desktop": words} in fixture, name


def test_the_low_trust_hover_names_programs():
    words = _ts("dashboard/lib/trustWords.ts")
    assert 'words: "programs",\n    tools: ["process_start", "process_write"],' in words
    assert "can't start programs that keep running" in words


# --------------------------------------------------------------------------- #
# Safety checks see them
# --------------------------------------------------------------------------- #


def test_the_safety_checks_read_their_commands_and_paths():
    from iron_jarvis.core.models import ToolInvocation

    assert COMMAND_TOOLS["process_start"] == ("command",)
    assert COMMAND_TOOLS["process_write"] == ("input",)
    assert set(EDITORS) <= FILE_WRITE_TOOLS
    start = ToolInvocation(id="t1", session_id="s", agent_run_id="r", tool="process_start",
                           args_json=json.dumps({"command": "npm run dev"}), ok=True, output="")
    ev = to_events(start)
    assert ev[0]["action"] == "command.executed" and ev[0]["command"] == "npm run dev"
    patch = "*** Begin Patch\n*** Add File: a.py\n+x\n*** Update File: b.py\n*** Move to: c.py\n@@\n-y\n+z\n*** End Patch"
    row = ToolInvocation(id="t2", session_id="s", agent_run_id="r", tool="apply_patch",
                         args_json=json.dumps({"patch": patch}), ok=True, output="")
    paths = [e["path"] for e in to_events(row)]
    assert paths == ["a.py", "b.py", "c.py"] and all(e["action"] == "file.write" for e in to_events(row))
