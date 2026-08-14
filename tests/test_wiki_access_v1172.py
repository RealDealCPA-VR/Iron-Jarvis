"""The app can tell you when it has gone blind (v1.172.0).

From a live user report — "it has no access to the wikis, that makes jarvis
blind as a bat" — four silent-failure seams, each of which turns a working
knowledge source into confident, ungrounded prose:

1. A MOVED VAULT WAS RECREATED EMPTY. ``MarkdownDirConnector.__init__`` ran an
   unconditional ``mkdir(parents=True, exist_ok=True)``, so a user vault that
   moved / was renamed / sat on an offline drive came back as a brand-new
   empty folder and a perfectly healthy connector.
2. THE STATUS LIED. A connector was "connected" when a CONFIG ENTRY existed,
   not when a tool could be called — an MCP wiki whose server failed to start
   at boot reported green while delivering zero tools.
3. ``workflow_list`` REACHED NO AGENT (v1.170.0 shipped it to the registry,
   permissions and auto-arming, but to no list in agents/types.py).
4. THE WORD "WIKI" MATCHED NO AUTO-ARM RULE, so the one phrasing that names a
   knowledge source armed nothing at all.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

from pathlib import Path

from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.connectors.service import _mcp_status
from iron_jarvis.core.models import AgentType
from iron_jarvis.ltm.brain import MarkdownBrainConnector
from iron_jarvis.ltm.obsidian import ObsidianConnector
from iron_jarvis.tools.autoselect import select_auto_tools


# --- 1. refuse, don't recreate -----------------------------------------------


def test_a_vanished_vault_is_reported_never_recreated(tmp_path):
    vault = tmp_path / "MyVault"
    vault.mkdir()
    (vault / "tax-notes.md").write_text("# S-corp election", encoding="utf-8")
    conn = ObsidianConnector(vault)
    assert conn.missing is False
    assert conn.health()["available"] is True

    # The drive goes away / the folder is renamed — the exact live scenario.
    for f in vault.iterdir():
        f.unlink()
    vault.rmdir()
    gone = ObsidianConnector(vault)
    assert not vault.exists(), "the vault was RECREATED — the blindness bug"
    assert gone.missing is True
    health = gone.health()
    assert health["available"] is False
    assert str(vault) in health["detail"]  # names the path it could not find
    assert "moved" in health["detail"] or "not found" in health["detail"]
    assert gone.search("S-corp") == [] or gone.search("S-corp") is not None


def test_the_builtin_brain_is_still_created_on_first_run(tmp_path):
    """The app's OWN store must self-create — only USER paths are protected."""
    home_brain = tmp_path / "state" / "brain"
    conn = MarkdownBrainConnector(home_brain)
    assert home_brain.is_dir(), "the built-in brain must be created on demand"
    assert conn.missing is False


def test_a_user_registered_markdown_source_is_not_recreated(tmp_path):
    missing = tmp_path / "team-wiki"
    conn = MarkdownBrainConnector(missing, create=False)
    assert not missing.exists()
    assert conn.missing is True
    assert conn.health()["available"] is False


# --- 2. connected means REACHABLE --------------------------------------------


def test_mcp_status_has_three_honest_states():
    assert _mcp_status(False, 0)["status"] == "disconnected"
    assert _mcp_status(False, 0)["connected"] is False

    dark = _mcp_status(True, 0)
    assert dark["status"] == "no_tools", (
        "a configured server with zero tools loaded needs its OWN state — a "
        "flat 'connected' is the badge that hid a dark wiki"
    )
    # `connected` stays true on purpose: the user's connection exists and
    # survives restarts (test_connectors::test_restart_survival pins that).
    # The capability truth is status + tools_loaded, not this flag.
    assert dark["connected"] is True
    assert "0 tools are loaded" in dark["detail"]
    assert "restart" in dark["detail"].lower()  # says what actually fixes it

    live = _mcp_status(True, 3)
    assert live["status"] == "connected" and live["connected"] is True
    assert live["detail"] == ""


# --- 3. the tool reaches a real agent ----------------------------------------


def test_workflow_list_reaches_agent_sessions():
    """v1.142.0's lesson: a tool absent from these lists reaches NO session."""
    for agent_type in (AgentType.BUILDER, AgentType.PLANNER):
        tools = get_agent_definition(agent_type).tools
        assert "workflow_create" in tools
        assert "workflow_list" in tools, (
            f"{agent_type.value} can author workflows but cannot see the saved "
            "ones — it will re-invent the user's process every run"
        )


# --- 4. the word people actually use -----------------------------------------


def _armed(message: str) -> list[str]:
    return select_auto_tools(message)


def test_asking_about_the_wiki_arms_the_knowledge_tools():
    for message in (
        "what does our wiki say about the S-corp election?",
        "check the wikis for the onboarding steps",
        "is that in the knowledge base?",
        "what does the runbook say when the daemon won't boot?",
        "look in our internal docs for the retention policy",
        "does the handbook cover PTO carryover?",
        # Found live against a real firm wiki: "firm docs" armed NOTHING
        # before v1.172.1 — the vocabulary has to be the USER's.
        "look in the firm docs for the client template",
        "check the office docs for the engagement letter",
        "what's in the hermes brain about the antique mall?",
        "check the brain for that client's onboarding steps",
    ):
        armed = _armed(message)
        assert "recall" in armed, f"nothing memory-ish armed for: {message!r}"
        assert "ltm_search" in armed or "file_search" in armed, (
            f"a knowledge question armed no SEARCH tool: {message!r} -> {armed}"
        )


def test_the_new_vocabulary_does_not_hijack_unrelated_messages():
    """Precision matters as much as recall — these must NOT arm memory."""
    for message in (
        "write a python docstring for this function",
        "document this endpoint in the README",
        "the doctor appointment is tuesday",
        # Precision guards for the widened list: no possessive, no arming.
        "brain surgery is scheduled for next week",
        "firm handshake",
    ):
        armed = _armed(message)
        assert "recall" not in armed, f"over-armed on: {message!r} -> {armed}"


def test_the_memory_vocabulary_still_fires_on_the_old_wording():
    """No regression: the pre-existing rule keeps its behavior."""
    armed = _armed("what do you know about the Henderson engagement?")
    assert "recall" in armed
