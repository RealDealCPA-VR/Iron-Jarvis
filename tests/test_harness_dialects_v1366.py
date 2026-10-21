"""v1.366.0 (IJ harness Phase 3): the dialect table — each model family is
shown the tool names and argument shapes it was trained on, and each call is
translated onto Jarvis's REAL tool before ``registry.invoke``.

The shapes are pinned against the SOURCES they claim: the Claude Code and
Codex inventory fixture (Phase 1) for the claude and gpt dialects, and the
names read from QwenLM/qwen-code @ 13a4fea5 for qwen. A translation is a pure
function; every case below is one call a model of that family really makes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.platform import build_platform

INVENTORY = json.loads(
    (Path(__file__).parent / "fixtures" / "harness_tool_inventory_v1364.json").read_text(encoding="utf-8")
)
ALL = frozenset(M.CODING_TOOLS)


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    return build_platform(str(tmp_path_factory.mktemp("dialects")))


# --------------------------------------------------------------------------- #
# Families
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("provider, model, family", [
    ("anthropic", "claude-sonnet-4-5", "claude"),
    ("claude-cli", "", "claude"),
    ("openrouter", "anthropic/claude-opus-4-8", "claude"),
    ("openai", "gpt-5.1", "gpt"),
    ("codex-cli", "gpt-5.6-terra", "gpt"),
    ("custom", "o3-mini", "gpt"),
    ("openrouter", "openai/gpt-oss-120b", "gpt"),
    ("custom", "glm-5.3-flash", "glm"),
    ("fleet-spark", "GLM-5.3-Flash-Think", "glm"),
    ("custom", "qwen3-coder-30b", "qwen"),
    ("openrouter", "qwen/qwen3-coder", "qwen"),
    ("custom", "deepseek-chat", "deepseek"),
    ("ollama", "llama3.1", "generic"),
    ("custom", "mistral-small", "generic"),
    ("", "", "generic"),
])
def test_the_family_is_read_off_the_serving_provider_and_model(provider, model, family):
    assert D.family_for(provider, model) == family


def test_every_family_speaks_a_dialect_and_glm_and_deepseek_speak_claude():
    assert set(D.FAMILY_DIALECT) == {"claude", "gpt", "glm", "qwen", "deepseek", "generic"}
    assert D.FAMILY_DIALECT["glm"] == "claude" and D.FAMILY_DIALECT["deepseek"] == "claude"
    assert D.FAMILY_DIALECT["qwen"] == "qwen" and D.FAMILY_DIALECT["gpt"] == "gpt"
    for family in D.FAMILY_DIALECT:
        assert family in D.FAMILY_EVIDENCE and len(D.FAMILY_EVIDENCE[family]) > 20


def test_auto_takes_the_familys_dialect_and_a_forced_one_wins():
    assert D.resolve("auto", "custom", "glm-5.3-flash") == ("glm", D.CLAUDE)
    assert D.resolve("generic", "custom", "glm-5.3-flash") == ("glm", D.GENERIC)
    assert D.resolve("gpt", "anthropic", "claude-x") == ("claude", D.GPT)


# --------------------------------------------------------------------------- #
# The shapes match their sources
# --------------------------------------------------------------------------- #


def _inventory_params(vendor: str) -> dict[str, set[str]]:
    out = {}
    for tool in INVENTORY[vendor]["tools"]:
        params = tool.get("params")
        props = params.get("properties") if isinstance(params, dict) else None
        out[tool["name"]] = set(props or {})
    return out


def test_the_claude_dialect_is_claude_codes_names_and_arguments():
    params = _inventory_params("claude_code")
    retired = set(INVENTORY["claude_code"]["removed_or_renamed"]["retired names (binary set CPe)"])
    aliases = set(INVENTORY["claude_code"]["removed_or_renamed"]["aliases (binary map g)"])
    legacy = {"MultiEdit", "LS"}  # permission-rule / compat names the models know
    for tool in D.CLAUDE.tools:
        if tool.name in params:
            extra = set(tool.input_schema["properties"]) - params[tool.name]
            assert not extra, (tool.name, extra)
        else:
            assert tool.name in retired | legacy, tool.name
        for alias in tool.aliases:
            assert alias in aliases, alias


def test_the_gpt_dialect_is_codexs_names_and_arguments():
    params = _inventory_params("codex")
    for tool in D.GPT.tools:
        assert tool.name in params, tool.name
        if tool.name == "apply_patch":
            # Codex 0.157.0 sends apply_patch as a FREEFORM grammar tool; over
            # JSON function calling it is the {input} form Codex used before.
            assert set(tool.input_schema["properties"]) == {"input"}
            continue
        extra = set(tool.input_schema["properties"]) - params[tool.name]
        assert not extra, (tool.name, extra)


def test_the_qwen_dialect_is_qwen_codes_names():
    # tool-names.ts @ 13a4fea5 (2026-10-21): the editor is `edit`, the search
    # is `grep_search` — older Qwen Code names (`replace`,
    # `search_file_content`) are not offered.
    assert [t.name for t in D.QWEN.tools] == [
        "read_file", "write_file", "edit", "glob", "grep_search", "list_directory",
        "run_shell_command", "task_stop", "todo_write", "notebook_edit", "web_fetch",
        "read_mcp_resource",
    ]
    assert "13a4fea5" in D.QWEN.source


def test_every_dialect_tool_targets_registered_tools_and_has_a_sound_schema(platform):
    registered = set(platform.registry.names())
    for dialect in D.DIALECTS.values():
        for tool in dialect.tools:
            assert set(tool.targets) <= registered, (dialect.id, tool.name, tool.targets)
            assert set(tool.targets) <= ALL, (dialect.id, tool.name)
            schema = tool.input_schema
            assert schema["type"] == "object" and isinstance(schema["properties"], dict)
            assert set(schema["required"]) <= set(schema["properties"]), (dialect.id, tool.name)
            assert len(tool.description) > 20


def test_the_generic_dialect_is_jarvis_own_names_exactly():
    assert D.GENERIC.tools == () and D.GENERIC.hides == frozenset()
    shown, passthrough = D.offered_tools(D.GENERIC, list(M.CODING_TOOLS))
    assert shown == [] and passthrough == list(M.CODING_TOOLS)
    t = D.translate(D.GENERIC, "read_file", {"path": "a"}, ALL)
    assert (t.real, t.args, t.translated) == ("read_file", {"path": "a"}, False)


# --------------------------------------------------------------------------- #
# Translations — claude
# --------------------------------------------------------------------------- #


def _t(dialect, name, args, armed=ALL):
    t = D.translate(dialect, name, args, armed)
    return t.real, t.args


def test_read_is_a_numbered_read_file_and_an_image_is_view_image():
    assert _t(D.CLAUDE, "Read", {"file_path": "C:/w/a.py"}) == (
        "read_file", {"path": "C:/w/a.py", "numbered": True})
    assert _t(D.CLAUDE, "Read", {"file_path": "a.py", "offset": 0, "limit": 50}) == (
        "read_file", {"path": "a.py", "offset": 1, "limit": 50, "numbered": True})
    assert _t(D.CLAUDE, "Read", {"file_path": "a.py", "offset": 40}) == (
        "read_file", {"path": "a.py", "offset": 40, "numbered": True})
    assert _t(D.CLAUDE, "Read", {"file_path": "shot.PNG"}) == ("view_image", {"path": "shot.PNG"})
    # No view_image armed: the image goes to the reader, which says what it can.
    assert _t(D.CLAUDE, "Read", {"file_path": "shot.png"}, ALL - {"view_image"})[0] == "read_file"


def test_write_edit_and_multiedit_map_their_arguments():
    assert _t(D.CLAUDE, "Write", {"file_path": "a", "content": "x"}) == ("write_file", {"path": "a", "content": "x"})
    assert _t(D.CLAUDE, "Edit", {"file_path": "a", "old_string": "o", "new_string": "n", "replace_all": True}) == (
        "edit_file", {"path": "a", "old": "o", "new": "n", "replace_all": True})
    assert _t(D.CLAUDE, "MultiEdit", {"file_path": "a", "edits": [
        {"old_string": "1", "new_string": "2"}, {"old_string": "3", "new_string": "4", "replace_all": "true"},
    ]}) == ("multi_edit", {"path": "a", "edits": [{"old": "1", "new": "2"}, {"old": "3", "new": "4", "replace_all": True}]})


def test_grep_keeps_claude_codes_default_mode_and_flags():
    assert _t(D.CLAUDE, "Grep", {"pattern": "x"}) == ("grep", {"pattern": "x", "output_mode": "files_with_matches"})
    real, args = _t(D.CLAUDE, "Grep", {
        "pattern": "def ", "path": "src", "glob": "*.py", "type": "py", "output_mode": "content",
        "-A": 2, "-B": 1, "-C": 3, "-n": False, "-i": True, "head_limit": 10, "offset": 5, "multiline": True,
    })
    assert real == "grep" and args == {
        "pattern": "def ", "path": "src", "glob": "*.py", "type": "py", "output_mode": "content",
        "after_context": 2, "before_context": 1, "context": 3, "line_numbers": False,
        "case_insensitive": True, "head_limit": 10, "offset": 5, "multiline": True,
    }


def test_bash_runs_the_shell_in_seconds_or_starts_a_background_program():
    assert _t(D.CLAUDE, "Bash", {"command": "pytest -q", "timeout": 90500, "description": "run"}) == (
        "shell", {"command": "pytest -q", "timeout_s": 91})
    assert _t(D.CLAUDE, "Bash", {"command": "x", "timeout": 9_999_999}) == ("shell", {"command": "x", "timeout_s": 600})
    assert _t(D.CLAUDE, "Bash", {"command": "py -m http.server", "run_in_background": True}) == (
        "process_start", {"command": "py -m http.server"})
    assert _t(D.CLAUDE, "BashOutput", {"bash_id": "p2"}) == ("process_read", {"id": "p2"})
    assert _t(D.CLAUDE, "TaskStop", {"task_id": "3"}) == ("process_stop", {"id": "p3"})
    # A retired name Claude still knows is translated the same way.
    assert _t(D.CLAUDE, "KillShell", {"shell_id": "p1"}) == ("process_stop", {"id": "p1"})


def test_todowrite_webfetch_websearch_notebook_and_resources():
    assert _t(D.CLAUDE, "TodoWrite", {"todos": [
        {"content": "read", "status": "completed", "activeForm": "Reading"},
        {"content": "fix", "status": "in_progress", "activeForm": "Fixing"},
    ]}) == ("plan_update", {"plan": [{"step": "read", "status": "completed"}, {"step": "fix", "status": "in_progress"}]})
    assert _t(D.CLAUDE, "WebFetch", {"url": "https://x.y", "prompt": "find the price"}) == (
        "web_fetch", {"url": "https://x.y"})
    assert _t(D.CLAUDE, "WebSearch", {"query": "rust 2024", "allowed_domains": ["doc.rust-lang.org"],
                                      "blocked_domains": ["spam.com"]}) == (
        "web_search", {"query": "rust 2024 (site:doc.rust-lang.org) -site:spam.com"})
    assert _t(D.CLAUDE, "NotebookEdit", {"notebook_path": "n.ipynb", "cell_id": "c1", "new_source": "1"}) == (
        "notebook_edit", {"path": "n.ipynb", "cell_id": "c1", "new_source": "1"})
    assert _t(D.CLAUDE, "ReadMcpResourceTool", {"server": "gh", "uri": "repo://x"}) == (
        "mcp_read_resource", {"server": "gh", "uri": "repo://x"})


def test_a_weak_models_lower_case_name_still_finds_the_tool():
    assert _t(D.CLAUDE, "bash", {"command": "dir"}) == ("shell", {"command": "dir"})


def test_an_unknown_name_passes_through_for_the_registry_to_refuse():
    t = D.translate(D.CLAUDE, "escalate_to_agent", {"reason": "x"}, ALL)
    assert (t.real, t.args, t.translated) == ("escalate_to_agent", {"reason": "x"}, False)


def test_a_translation_never_raises():
    for dialect in D.DIALECTS.values():
        for tool in dialect.tools:
            for args in ({}, {"file_path": None}, {"timeout": "soon", "edits": "nope", "todos": 3}, None):
                D.translate(dialect, tool.name, args, ALL)


# --------------------------------------------------------------------------- #
# Translations — gpt (Codex)
# --------------------------------------------------------------------------- #


def test_exec_command_is_a_session_with_codexs_wait_and_falls_back_to_the_shell():
    assert _t(D.GPT, "exec_command", {"cmd": "pytest -q", "workdir": "pkg", "yield_time_ms": 20000}) == (
        "process_start", {"command": "pytest -q", "cwd": "pkg", "wait_ms": 20000, "until_exit": True})
    assert _t(D.GPT, "exec_command", {"cmd": "ls"})[1]["wait_ms"] == 10000
    assert _t(D.GPT, "exec_command", {"cmd": "ls", "yield_time_ms": 999_999})[1]["wait_ms"] == 30000
    # process_start not armed (low trust keeps it away): the shell runs it.
    assert _t(D.GPT, "exec_command", {"cmd": "ls"}, ALL - {"process_start"}) == ("shell", {"command": "ls"})
    tool = D.GPT.lookup("exec_command")
    assert tool.reach(ALL) == {"process_start"}
    assert tool.reach(ALL - {"process_start"}) == {"shell"}


def test_write_stdin_types_polls_or_interrupts():
    assert _t(D.GPT, "write_stdin", {"session_id": 2, "chars": "y\n"}) == (
        "process_write", {"id": "p2", "input": "y\n", "wait_ms": 250})
    assert _t(D.GPT, "write_stdin", {"session_id": "p2"}) == ("process_read", {"id": "p2", "wait_ms": 5000})
    assert _t(D.GPT, "write_stdin", {"session_id": 2, "chars": "\u0003"}) == ("process_stop", {"id": "p2"})


def test_apply_patch_update_plan_and_view_image():
    patch = "*** Begin Patch\n*** Add File: a.txt\n+hi\n*** End Patch"
    assert _t(D.GPT, "apply_patch", {"input": patch}) == ("apply_patch", {"patch": patch})
    assert _t(D.GPT, "apply_patch", {"patch": patch}) == ("apply_patch", {"patch": patch})
    assert _t(D.GPT, "update_plan", {"plan": [{"step": "a", "status": "pending"}], "explanation": "e"}) == (
        "plan_update", {"plan": [{"step": "a", "status": "pending"}], "explanation": "e"})
    assert _t(D.GPT, "view_image", {"path": "x.png", "detail": "original"}) == ("view_image", {"path": "x.png"})


# --------------------------------------------------------------------------- #
# Translations — qwen
# --------------------------------------------------------------------------- #


def test_qwen_reads_from_a_zero_based_line_and_greps_case_insensitively():
    assert _t(D.QWEN, "read_file", {"file_path": "a.py", "offset": 0, "limit": 10}) == (
        "read_file", {"path": "a.py", "offset": 1, "limit": 10})
    # Qwen's line 40 (0-based) is Jarvis's line 41 (1-based).
    assert _t(D.QWEN, "read_file", {"file_path": "a.py", "offset": 40}) == ("read_file", {"path": "a.py", "offset": 41})
    assert _t(D.QWEN, "grep_search", {"pattern": "x", "glob": "*.py", "limit": 5}) == (
        "grep", {"output_mode": "content", "case_insensitive": True, "pattern": "x", "glob": "*.py", "head_limit": 5})
    assert _t(D.QWEN, "edit", {"file_path": "a", "old_string": "o", "new_string": "n"}) == (
        "edit_file", {"path": "a", "old": "o", "new": "n"})
    assert _t(D.QWEN, "run_shell_command", {"command": "x", "timeout": 3000, "directory": "sub"}) == (
        "shell", {"command": "x", "timeout_s": 3, "cwd": "sub"})
    assert _t(D.QWEN, "run_shell_command", {"command": "npm run dev", "is_background": True}) == (
        "process_start", {"command": "npm run dev"})
    assert _t(D.QWEN, "todo_write", {"todos": [{"id": "1", "content": "a", "status": "pending"}]}) == (
        "plan_update", {"plan": [{"step": "a", "status": "pending"}]})
    # A Jarvis-shaped call to a same-named tool still works.
    assert _t(D.QWEN, "read_file", {"path": "a.py"}) == ("read_file", {"path": "a.py"})


# --------------------------------------------------------------------------- #
# What is offered, and what that arms
# --------------------------------------------------------------------------- #


def test_a_dialect_hides_the_real_names_its_tools_cover():
    shown, passthrough = D.offered_tools(D.CLAUDE, list(M.CODING_TOOLS))
    names = [t.name for t in shown]
    assert "Read" in names and "Bash" in names and "TodoWrite" in names
    assert "read_file" not in passthrough and "shell" not in passthrough
    assert passthrough == ["apply_patch", "process_write"]


def test_a_dialect_tool_is_offered_only_when_its_target_is_armed():
    shown, _ = D.offered_tools(D.CLAUDE, ["read_file", "write_file"])
    assert [t.name for t in shown] == ["Read", "Write"]
    shown, _ = D.offered_tools(D.GPT, ["shell"])  # exec_command falls back
    assert [t.name for t in shown] == ["exec_command"]


def test_the_armed_set_is_exactly_what_the_shown_tools_reach(platform):
    plan = M.plan_tools(platform.registry, platform.permissions, D.GPT, family="gpt", stream=True)
    # exec_command reaches process_start, so the shell is NOT armed for Codex:
    # a model calling `shell` by a name it was never shown is refused.
    assert "shell" not in plan.gate and "process_start" in plan.gate
    assert "write_file" not in plan.gate and "edit_file" not in plan.gate  # apply_patch covers edits
    low = M.plan_tools(platform.registry, platform.permissions, D.GPT, family="gpt", stream=True, low_trust=True)
    assert "process_start" not in low.gate and "shell" in low.gate


# --------------------------------------------------------------------------- #
# process_start until_exit (Codex's exec_command semantics)
# --------------------------------------------------------------------------- #


async def test_process_start_until_exit_waits_through_a_pause_for_the_end(tmp_path):
    import sys

    from iron_jarvis.core.config import load_config
    from iron_jarvis.harness.process import ProcessStartTool, end_session
    from iron_jarvis.tools.base import ToolContext

    cfg = load_config(str(tmp_path))
    ctx = ToolContext(workspace=tmp_path, session_id="t-until-exit", agent_run_id="r",
                      config=cfg, event_bus=None, engine=None)
    script = tmp_path / "pause.py"
    script.write_text("import time,sys\nprint('a', flush=True)\ntime.sleep(1.0)\nprint('b', flush=True)\n",
                      encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}"'
    try:
        quick = await ProcessStartTool().execute({"command": cmd, "wait_ms": 8000}, ctx)
        waited = await ProcessStartTool().execute({"command": cmd, "wait_ms": 8000, "until_exit": True}, ctx)
    finally:
        end_session("t-until-exit")
    assert quick.ok and waited.ok
    # Without until_exit the start returns when the output pauses; with it,
    # only when the program has ended (or the time is up).
    assert quick.data["running"] is True
    assert waited.data["running"] is False and waited.data["exit_code"] == 0
    assert "b" in waited.output
