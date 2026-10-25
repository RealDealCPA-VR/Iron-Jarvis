"""Jarvis on Codex is told that its tools DO change files (v1.385.1).

The v1.385.0 benchmark (Jarvis on Codex, gpt-6-astra, medium) failed 26 of
65 scored runs, and every one of the 26 ended the same way: "The working
folder is read-only in this session, so I couldn't apply the fix", with a
patch pasted for the user. Iron Jarvis starts every Codex thread READ-ONLY on
purpose: that posture is what keeps Codex's OWN built-in tools from acting,
and Iron Jarvis carries out the model's function tools itself. But Codex
shows the model its sandbox, and the model read "read-only" as "I cannot
change files".

The posture is unchanged and still confirmed before any turn. The model is
now told, in the thread's instructions, who carries out its tools, but only
when it has tools (a text-only turn has none, and saying its tools change
files would be untrue).
"""

from __future__ import annotations

from iron_jarvis.providers.adapters.codex_native import protocol as P

SPECS = [{"name": "edit_file", "description": "Edit a file", "inputSchema": {"type": "object"}}]


def test_a_turn_with_tools_is_told_its_tools_change_files():
    params = P.thread_start_params(
        base_instructions="You are Jarvis.", specs=SPECS, cwd="C:\\work", model="gpt-6-astra", mcp_servers=[],
    )
    text = params["baseInstructions"]
    assert text.startswith("You are Jarvis.\n\n")
    assert P.SANDBOX_NOTE in text
    note = P.SANDBOX_NOTE
    assert "only to Codex's own built-in tools" in note
    assert "really do read, create, change and delete files in the working folder" in note
    assert "never tell the user the folder is read-only" in note


def test_the_posture_itself_is_unchanged():
    params = P.thread_start_params(
        base_instructions="You are Jarvis.", specs=SPECS, cwd="C:\\work", model=None, mcp_servers=[],
    )
    assert params["sandbox"] == "read-only" and params["approvalPolicy"] == "untrusted"
    assert params["environments"] == [] and params["ephemeral"] is True
    # and a thread that did not confirm read-only is still refused
    assert P.start_problem({"sandbox": {"type": "workspaceWrite"}, "approvalPolicy": "untrusted"})


def test_a_turn_without_tools_gets_no_note():
    params = P.thread_start_params(
        base_instructions="You are Jarvis.", specs=[], cwd="C:\\work", model=None, mcp_servers=[],
    )
    assert params["baseInstructions"] == "You are Jarvis."
    params = P.thread_start_params(base_instructions="", specs=[], cwd="C:\\work", model=None, mcp_servers=[])
    assert "baseInstructions" not in params


def test_tools_with_no_instructions_still_get_the_note():
    params = P.thread_start_params(base_instructions="  ", specs=SPECS, cwd="C:\\work", model=None, mcp_servers=[])
    assert params["baseInstructions"] == P.SANDBOX_NOTE
