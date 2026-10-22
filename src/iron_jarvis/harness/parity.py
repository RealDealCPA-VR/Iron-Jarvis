"""Every tool Claude Code and Codex give their models, and what serves it in
Jarvis (v1.364.0, IJ harness Phase 1).

The plan's rule: "every tool Claude Code and Codex give their models, the IJ
harness gives too — the union of both, on Jarvis's real tools", checked
against what the vendors SHIP, not against memory. The inventory of Claude
Code 2.1.306 and Codex 0.157.0 (read from the installed binaries and the
tagged source, 2026-10-21) is ``tests/fixtures/harness_tool_inventory_v1364
.json``; ``tests/test_harness_tools_parity_v1364.py`` loads it and fails when
any tool there has no row here, or a row names a Jarvis tool the registry
does not hold.

Each row is one of three verdicts:

* ``SERVED`` — the Jarvis tools that do the job (the dialect table, Phase 3,
  maps the vendor's names and argument shapes onto them);
* ``LATER`` — needed, and built in a named later phase (the reason says why
  not now);
* ``NOT_NEEDED`` — Jarvis does this another way, or it is not a coding
  harness concern (the reason says which).

``mcp__<pack>__<tool>`` is the naming pattern, not a tool: a connected app's
tools register under exactly that shape.
"""

from __future__ import annotations

from dataclasses import dataclass

SERVED = "served"
LATER = "later"
NOT_NEEDED = "not_needed"
VERDICTS = (SERVED, LATER, NOT_NEEDED)

#: The connected-app naming pattern both vendors use (not a registry name).
APP_TOOL_PATTERN = "mcp__<pack>__<tool>"


@dataclass(frozen=True)
class Parity:
    verdict: str
    jarvis: tuple[str, ...] = ()
    reason: str = ""


def _served(*tools: str, reason: str = "") -> Parity:
    return Parity(SERVED, tuple(tools), reason)


def _later(reason: str) -> Parity:
    return Parity(LATER, (), reason)


def _no(reason: str) -> Parity:
    return Parity(NOT_NEEDED, (), reason)


#: v1.367.0 (Phase 4): plan mode is a Build SESSION mode (``harness/sessions``,
#: ``PATCH /build/sessions/{id} {mode}``): in it a turn arms only the
#: read-only tools, plan_update and exit_plan_mode, whose once-only card is
#: how the user lets the session act.
_PLAN_MODE = (
    "plan mode is the Build session's mode (PATCH /build/sessions/{id} mode=plan): in it a "
    "turn arms only read-only tools and plan_update, and exit_plan_mode presents the plan on "
    "a once-only approval card; Allow switches the session to act mode"
)
_ASK_USER = (
    "ask_user shows the question in the Build conversation (the same side channel as an "
    "app's question) and the turn waits, with no clock, for POST /build/sessions/{id}/answer"
)
_NO_TOOL_SEARCH = (
    "Jarvis arms tools by the sentence (tools/autoselect) and by the session's "
    "armed set; there is no deferred tool loading to search"
)

#: Claude Code 2.1.306 — keyed by the inventory's tool names, exactly.
CLAUDE_CODE: dict[str, Parity] = {
    "Read": _served(
        "read_file", "view_image", "read_document",
        reason="read_file takes offset/limit/numbered (cat -n style, 2000-line window, cut reported); "
        "images through view_image; PDFs and office files through read_file/read_document",
    ),
    "Write": _served("write_file"),
    "Edit": _served("edit_file", reason="strict: unique match or refuse, replace_all"),
    "Glob": _served("glob"),
    "Grep": _served("grep", reason="output_mode, context lines, case_insensitive, glob/type, head_limit + offset, multiline"),
    "Bash": _served("shell", "process_start", reason="shell takes timeout_s (max 600) and cwd; a background program is process_start"),
    "PowerShell": _served(
        "shell",
        reason="the shell tool runs cmd.exe on Windows (sh elsewhere), not PowerShell; a PowerShell "
        "command runs through it as `powershell -NoProfile -Command ...` (v1.364.0 review: the "
        "row used to say the shell WAS PowerShell)",
    ),
    "Monitor": _served("process_start", "process_read", reason="a background program's output is read as it arrives (process_read wait_ms)"),
    "TaskStop": _served("process_stop"),
    "NotebookEdit": _served("notebook_edit"),
    "LSP": _no("no language servers are bundled; definitions and references come from grep and read_file"),
    "WebFetch": _served("web_fetch"),
    "WebSearch": _served("web_search"),
    "TodoWrite": _served("plan_update"),
    "TaskCreate": _served("plan_update", reason="one whole-list plan tool covers the four task tools"),
    "TaskUpdate": _served("plan_update", reason="one whole-list plan tool covers the four task tools"),
    "TaskGet": _served("plan_update", reason="the plan is in the conversation: every plan_update returns the whole list"),
    "TaskList": _served("plan_update", reason="the plan is in the conversation: every plan_update returns the whole list"),
    "AskUserQuestion": _served("ask_user", reason=_ASK_USER),
    "EnterPlanMode": _served(
        "exit_plan_mode", "plan_update",
        reason="the USER puts a Build session in plan mode (the session's mode switch), as Claude "
        "Code's own shift+tab does; " + _PLAN_MODE,
    ),
    "ExitPlanMode": _served("exit_plan_mode", reason=_PLAN_MODE),
    "Agent": _served("delegate", "spawn_agent"),
    "SendMessage": _served("message_agent"),
    "ListAgents": _served("list_agents"),
    "SubagentHandback": _no("a helper's run hands its result back to delegate/spawn_agent when it ends; no separate call"),
    "Workflow": _served("workflow_create", "workflow_run", reason="a saved workflow drives agents step by step"),
    "Skill": _served("skill_load", "skill_search"),
    "ToolSearch": _no(_NO_TOOL_SEARCH),
    "WaitForMcpServers": _no("a turn already waits for apps that are still starting (mcp.tools.wait_for_starting_packs, v1.311.0)"),
    "ListMcpResourcesTool": _served("mcp_list_resources"),
    "ReadMcpResourceTool": _served("mcp_read_resource"),
    "ReadMcpResourceDirTool": _served(
        "mcp_list_resources", "mcp_read_resource",
        reason="an app's resource folder is listed with mcp_list_resources and each entry read with mcp_read_resource",
    ),
    "RefreshMcpTools": _served("pack_restart", reason="restarting an app reloads its tools"),
    "mcp__<server>__<tool>": _served(APP_TOOL_PATTERN, reason="a connected app's tools register under the same naming"),
    "EnterWorktree": _no("a build session works in its project folder; self-development's git worktrees are made by the orchestrator, not by the model"),
    "ExitWorktree": _no("a build session works in its project folder; self-development's git worktrees are made by the orchestrator, not by the model"),
    "CronCreate": _served("schedule_create"),
    "CronDelete": _served("schedule_delete"),
    "CronList": _served("app_status", reason="app_status lists this install's schedules (the Schedules page is the full list)"),
    "ScheduleWakeup": _no("a session does not sleep and wake itself; a schedule (schedule_create) starts work at a time"),
    "RemoteTrigger": _no("remote routines are Claude Code's cloud feature; another computer's agent is reached with delegate_remote"),
    "Artifact": _no("publishing a web page is not a coding-harness concern"),
    "PushNotification": _no("not a coding-harness concern (notify sends to the user's own channels when a task needs it)"),
    "SendUserFile": _no("files a tool writes are listed in the reply's changes; there is nothing to send"),
    "ReportFindings": _no("a review's findings are the reply itself"),
    "SendFeedback / ShareOnboardingGuide / EndConversation / Projects / ShowOnboardingRolePicker": _no(
        "Claude product features, not tools a coding model uses"
    ),
}

_SUB_AGENT = "a helper agent is started with spawn_agent (or delegate, which waits for its result)"

#: Codex 0.157.0 — keyed by the inventory's tool names, exactly.
CODEX: dict[str, Parity] = {
    "exec_command": _served("shell", "process_start", reason="a command that ends: shell; one that keeps running: process_start"),
    "write_stdin": _served("process_write", "process_read"),
    "apply_patch": _served("apply_patch", reason="the V4A format and matching ladder; all or nothing; line endings kept"),
    "update_plan": _served("plan_update"),
    "view_image": _served("view_image"),
    "web_search": _served("web_search"),
    "web.run": _served("web_fetch", "web_search"),
    "list_mcp_resources": _served("mcp_list_resources"),
    "list_mcp_resource_templates": _no(
        "a template fills in a URI pattern; Jarvis lists concrete resources (mcp_list_resources) "
        "and reads any URI the model knows (mcp_read_resource)"
    ),
    "read_mcp_resource": _served("mcp_read_resource"),
    "mcp__<server>__<tool>": _served(APP_TOOL_PATTERN, reason="a connected app's tools register under the same naming"),
    "tool_search": _no(_NO_TOOL_SEARCH),
    "request_user_input": _served("ask_user", reason=_ASK_USER),
    "request_permissions": _no("Jarvis asks on the approval card for every ask-tier call; a model never grants itself permissions"),
    "exec (code mode)": _no("code mode wraps Codex's own tools in a script; Jarvis offers the tools directly, and repl for scripted work over stored results"),
    "wait (code mode)": _no("code mode wraps Codex's own tools in a script; Jarvis offers the tools directly"),
    "multi_agent_v1.spawn_agent": _served("spawn_agent", "delegate", reason=_SUB_AGENT),
    "multi_agent_v1.send_input": _served("message_agent"),
    "multi_agent_v1.resume_agent": _served("delegate", reason="a finished helper is given more work by delegating to it again"),
    "multi_agent_v1.wait_agent": _served("delegate", reason="delegate waits for the helper's result"),
    "multi_agent_v1.close_agent": _no("a helper's run ends by itself (its step budget) or with the user's Cancel"),
    "collaboration.spawn_agent (v2)": _served("spawn_agent", "delegate", reason=_SUB_AGENT),
    "collaboration.send_message (v2)": _served("message_agent"),
    "collaboration.followup_task (v2)": _served("delegate", reason="a follow-up is another delegated task"),
    "collaboration.wait_agent (v2)": _served("delegate", reason="delegate waits for the helper's result"),
    "collaboration.interrupt_agent (v2)": _no("a helper's run is stopped with the user's Cancel, which reaches it through the parent"),
    "collaboration.list_agents (v2)": _served("list_agents"),
    "clock.curr_time": _no(
        "v1.366.0 (Phase 3): a harness turn's system prompt carries today's date "
        "(harness/mode.HarnessTurn.block); a task that needs the time of day prints it with the "
        "shell"
    ),
    "clock.sleep": _no("a model waits on a program with process_read's wait_ms; there is no bare sleep"),
    "send_message_to_user_async": _no("the reply streams to the user as it is written"),
    "request_user_input_async": _served(
        "ask_user",
        reason="the build turn is attended, so the question is asked and waited for in the "
        "conversation (" + _ASK_USER + ")",
    ),
    "get_context_remaining": _no("Jarvis budgets the transcript to the model's window itself (context.plan_history, compaction)"),
    "new_context": _no("Jarvis compacts an overflowing transcript itself (context/compaction)"),
    "wait_for_environment": _no("there is no remote environment to wait for; the session runs on this PC"),
    "list_available_plugins_to_install / request_plugin_install": _no(
        "extensions are added by the user (Settings › Tools); app_connect connects an app from chat"
    ),
    "get_goal": _served("goal_list"),
    "create_goal": _served("goal_add"),
    "update_goal": _served("goal_update"),
    "memories.list / memories.read / memories.search / memories.add_ad_hoc_note": _served(
        "memory_search", "memory_read", "memory_write", "recall",
    ),
    "skills.list / skills.read": _served("skill_search", "skill_load"),
    "image_gen.imagegen": _served("generate_media"),
    "history.* (list_windows, list_items, read_item, search_contents) / notes.* (list_files_by_prefix, read_file, search_contents, append_to_file, write_file)": _served(
        "history_search", "notebook",
        reason="past conversations through history_search; an agent's own notes through notebook",
    ),
    "agent message board (create_channel, get_channels, list_threads, search_posts, read_thread, read_post, subscribe, unsubscribe, post)": _served(
        "blackboard_post", "blackboard_read", "message_agent",
    ),
}

#: Every Jarvis tool Phase 4 added (v1.367.0) — on no roster, armed only by a
#: Build session's turn (harness/mode.plan_tools).
PHASE_4_TOOLS: tuple[str, ...] = ("ask_user", "exit_plan_mode")

#: Every Jarvis tool Phase 1 added (v1.364.0) — none is on an agent roster
#: or auto-armed in chat; the IJ harness session arms them by name.
PHASE_1_TOOLS: tuple[str, ...] = (
    "multi_edit",
    "apply_patch",
    "glob",
    "plan_update",
    "notebook_edit",
    "mcp_list_resources",
    "mcp_read_resource",
    "process_start",
    "process_read",
    "process_write",
    "process_stop",
)


def table() -> dict[str, dict[str, Parity]]:
    """Both tables, by vendor (the inventory's keys)."""
    return {"claude_code": CLAUDE_CODE, "codex": CODEX}


def served_tools() -> set[str]:
    """Every Jarvis tool name a SERVED row names (the app-tool pattern left
    out)."""
    out: set[str] = set()
    for rows in table().values():
        for row in rows.values():
            if row.verdict == SERVED:
                out |= {t for t in row.jarvis if t != APP_TOOL_PATTERN}
    return out
