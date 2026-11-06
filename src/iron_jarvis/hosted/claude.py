"""Claude Code in the Code view (v1.409.0): its stream-json protocol, with ALL
of its own tools on.

MEASURED on this PC (claude 2.1.311, live, in a scratch folder):

* ``--permission-prompts host`` ALONE does not ask the host: every tool that
  needed permission was DENIED ("Claude requested permissions to write …, but
  you haven't granted it yet"). With ``--permission-prompt-tool stdio`` as
  well, each one arrives as a ``control_request`` subtype ``can_use_tool``
  (``tool_name``, ``input``, ``description``, ``permission_suggestions``,
  ``tool_use_id``) and waits for a ``control_response``: ``{"behavior":
  "allow", "updatedInput": …}`` runs it, ``{"behavior": "deny", "message":
  …}`` refuses it (the model reads the message). Both flags are passed.
* ``AskUserQuestion`` arrives the same way (``requires_user_interaction``);
  it is answered by ALLOWING with ``updatedInput`` = the questions plus
  ``"answers": {<question text>: <chosen label>}`` — the tool result then reads
  ``The user answered: "<question>"="<label>"``.
* "Allow for this session" = allow + ``updatedPermissions`` = the request's
  own ``permission_suggestions`` (Claude Code's session-wide allow, e.g.
  ``setMode acceptEdits``). An ask with no suggestion offers no such button.
* ``interrupt``: ``{"subtype": "success", "response": {"still_queued": []}}``,
  then a ``result`` with ``subtype: error_during_execution`` and
  ``terminal_reason: aborted_streaming``; the process stays up and takes the
  next message.
* ``set_permission_mode`` takes ``acceptEdits | auto | bypassPermissions |
  default | dontAsk | plan`` (anything else: an error naming that list);
  ``--permission-mode default`` works as a flag too. ``set_model`` answers
  ``{"model": "claude-haiku-5-5"}`` for ``haiku`` and an error ``Model '…' not
  found`` for an unknown one — both applied live, no restart.
* ``--resume <id>`` keeps the SAME session id (the frames after it carry the
  resumed id), so ``claude --resume <id>`` in a terminal opens the conversation
  this view started, and the other way round.
* A signed-out home answers each message with an assistant frame
  ``error: "authentication_failed"`` and the text ``Not logged in · Please run
  /login``, then a ``result`` with ``is_error: true``.

MEASURED for v1.412.0 (claude 2.1.311, live, haiku):

* ``set_permission_mode bypassPermissions`` on a process started WITHOUT
  ``--allow-dangerously-skip-permissions`` is REFUSED ("Cannot set permission
  mode to bypassPermissions because the session was not launched with
  --dangerously-skip-permissions", ``error_code: bypass_not_launched``) — the
  reason Full access could not be chosen once a session had started. With the
  flag the process still starts in the mode ``--permission-mode`` names (the
  flag only makes Full access AVAILABLE) and every mode switches live,
  ``auto`` included ("Use a model classifier to approve/deny permission
  prompts"; Claude Code calls it "Auto").
* A user message whose ``content`` holds an ``{"type": "image", "source":
  {"type": "base64", "media_type", "data"}}`` block is SEEN (the model read a
  word drawn only in the picture's pixels).
* A user message written WHILE a turn runs is Claude Code's own steer: with a
  client ``uuid`` on each message it says ``command_lifecycle`` {command_uuid,
  state queued|started|completed|cancelled}. During a tool call the note is
  absorbed into the running turn at the next step ("The user sent a new
  message while you were working"); with no tool call left it runs right
  after the turn (a SECOND ``result``). ``interrupt`` with ``cancel_queued:
  true`` cancels a note not yet read (``cancelled``) instead of running it
  after the stop.

READ for v1.417.0 (claude 2.1.316's own bundle, read-only: its control
schema and its ``updatedPermissions`` validator):

* an ``updatedPermissions`` entry is ``{"type": "addRules" | "replaceRules" |
  "removeRules", "rules": [{"toolName", "ruleContent"?}], "behavior": "allow"
  | "deny" | "ask", "destination": "userSettings" | "projectSettings" |
  "localSettings" | "session" | "cliArg"}`` (or ``setMode`` /
  ``addDirectories`` / ``removeDirectories``). ``ruleContent`` is OPTIONAL: a
  rule with only ``toolName`` is the whole tool. Claude Code's OWN suggestion
  for a command is a PREFIX rule (``Bash(npm test *)``, destination
  ``localSettings``), which is why 24 presses of "Allow for this session" did
  not stop 40 command cards in one live session. "Allow all commands this
  session" (:data:`ALLOW_COMMANDS`) answers allow with Iron Jarvis's OWN rule
  (:data:`ALL_COMMANDS_RULE`): the whole ``Bash`` and ``PowerShell`` tools
  (Claude Code on Windows runs commands through either), destination
  ``session`` — never written to a settings file. Claude Code's own
  suggestion stays offered, as "Allow similar commands". (Claude Code's own
  safety checks may still ask about a command its rules never allow alone.)
* ``ExitPlanMode``'s ``input`` carries the plan (``plan``, Markdown): the card
  shows it (:data:`PLAN_CHARS`). A ``deny`` carries the user's own words as
  its ``message`` (the model reads them and stays in plan mode).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from typing import Any

from . import words as W
from .driver import Ask, AskRefused, ControlRefused, ControlWaiter, Driver, new_ask_id
from .events import (
    DETAIL_CHARS,
    DIFF_CHARS,
    FULL_KEY,
    INPUT_CHARS,
    TEXT_CHARS,
    clip,
    clip_tail,
    make,
    now_iso,
    one_line,
    unified_from_hunks,
    unified_new_file,
    unified_replace,
    with_full,
)

HARNESS = "claude-code"

#: v1.417.0 (C1.1): the answer "Allow all commands this session" (beside the
#: route's ``allow`` / ``allow_session`` / ``deny``).
ALLOW_COMMANDS = "allow_commands"
#: The tools whose every call that answer allows (the session's commands).
ALL_COMMANDS_TOOLS: tuple[str, ...] = ("Bash", "PowerShell")
#: Iron Jarvis's OWN ``updatedPermissions`` entry for that answer (the shape
#: claude 2.1.316 validates — see the module doc): the whole tools, for this
#: session only.
ALL_COMMANDS_RULE: dict[str, Any] = {
    "type": "addRules",
    "rules": [{"toolName": t} for t in ALL_COMMANDS_TOOLS],
    "behavior": "allow",
    "destination": "session",
}
#: v1.417.0 review: the modes that never offer (or take) that answer. Full
#: access asks about nothing it could cover; in Auto, claude 2.1.316 calls a
#: whole-tool Bash / PowerShell allow "dangerous ... (bypasses classifier)":
#: it strips it on entering Auto (``stripDangerousPermissionsForAutoMode``)
#: and leaves it out of the rules it matches while the classifier runs, so
#: the press would not do what it says; Plan reads and plans only (and
#: Claude Code strips the same rule when its plan mode uses Auto's
#: classifier).
ALL_COMMANDS_NOT_IN: frozenset[str] = frozenset({"full", "auto", "plan"})
#: v1.417.0 (C4.1): the longest plan an ExitPlanMode card shows.
PLAN_CHARS = 20_000


def all_commands_rule() -> dict[str, Any]:
    """A fresh copy of :data:`ALL_COMMANDS_RULE` (never the shared one)."""
    return {**ALL_COMMANDS_RULE, "rules": [dict(r) for r in ALL_COMMANDS_RULE["rules"]]}

#: The composer's presets -> Claude Code's permission modes (flag and live).
MODES: dict[str, str] = {
    "ask": "default",
    "edits": "acceptEdits",
    "plan": "plan",
    "auto": "auto",
    "full": "bypassPermissions",
}
MODE_OF: dict[str, str] = {v: k for k, v in MODES.items()}
MODE_LABELS: dict[str, str] = {
    "ask": "Ask first",
    "edits": "Auto edits",
    "plan": "Plan",
    "auto": "Auto",
    "full": "Full access",
}
#: v1.412.0: what each of Claude Code's modes does, in plain words (the menu
#: says it under each one).
MODE_ABOUT: dict[str, str] = {
    "ask": "Asks before it changes a file or runs a command.",
    "edits": "Changes files without asking; asks before it runs a command.",
    "plan": "Reads and plans only; changes nothing until you approve its plan.",
    "auto": "Claude Code's own safety check allows or refuses each action without asking you; "
            "a few risky ones still ask.",
    "full": "Runs everything without asking.",
}
#: Every process may be switched to Full access later (the flag only makes it
#: AVAILABLE; the mode still starts as ``--permission-mode`` says).
ALLOW_FULL_FLAG = "--allow-dangerously-skip-permissions"
#: v1.418.0 (C4.3): hook output in the stream (see :meth:`ClaudeDriver.argv`).
INCLUDE_HOOKS_FLAG = "--include-hook-events"
#: Pictures Claude Code takes as image blocks, and the most one may weigh.
IMAGE_TYPES: dict[str, str] = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                               ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 8

#: The `/` list Claude Code last answered ``initialize`` with (this process):
#: the harnesses listing shows it without starting anything.
LAST_COMMANDS: dict[str, list[dict[str, str]]] = {}
#: v1.439.0: the tools whose `/` list depends on the FOLDER (OpenCode reads a
#: project's own commands from it): kept per folder, so one project's commands
#: never show in another project's session.
#: v1.441.0 (review P3): Claude Code too — its ``initialize`` lists the
#: folder's own ``.claude/commands`` and skills beside its built-ins.
FOLDER_COMMANDS: tuple[str, ...] = ("opencode", "claude-code")


def folder_key(folder: Any) -> str:
    """How a folder is spelled as a key (case and separators folded)."""
    text = str(folder or "").strip()
    return os.path.normcase(os.path.normpath(text)) if text else ""


def commands_key(harness: str, folder: Any = "") -> str:
    """The :data:`LAST_COMMANDS` key a tool's list is kept under: the tool,
    or (for :data:`FOLDER_COMMANDS`) the tool and its folder."""
    if harness in FOLDER_COMMANDS:
        return f"{harness}@{folder_key(folder)}"
    return harness

#: How long a control request (initialize, set_model …) may take.
CONTROL_TIMEOUT_S = 30.0
#: v1.412.0: how long a reply's end waits for Claude Code to say it QUEUED a
#: note sent while it worked (it says so at once, measured); past it the note
#: goes back to the box and the reply ends.
HOLD_FOR_NOTE_S = 5.0  # v1.417.0 (C4.7): 15 -> 5; Claude Code says "queued" at once
#: How long a note waits for Claude Code's first ``command_lifecycle`` (the
#: sign it reports when it reads a message) before it is refused.
LIFECYCLE_WAIT_S = 5.0

_COMMAND_TOOLS = {"Bash", "PowerShell", "BashOutput", "KillShell", "KillBash"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
_READ_TOOLS = {"Read", "LS"}
_SEARCH_TOOLS = {"Grep", "Glob"}
_WEB_TOOLS = {"WebFetch", "WebSearch"}
_TASK_TOOLS = {"Task", "Agent"}


#: v1.417.0 review: the background task types that are helper AGENTS (Claude
#: Code 2.1.316's own task table: local_agent and local_workflow are "agent",
#: remote_agent and in_process_teammate are agents too). Everything else — a
#: shell command, a monitor, an MCP task — is a background command.
HELPER_TASK_TYPES = frozenset({"local_agent", "local_workflow", "remote_agent", "in_process_teammate"})


def is_helper_task(kind: str) -> bool:
    kind = str(kind or "")
    return kind in HELPER_TASK_TYPES or "agent" in kind or "teammate" in kind


def tool_kind(name: str) -> str:
    if name in _COMMAND_TOOLS:
        return "command"
    if name in _EDIT_TOOLS:
        return "edit"
    if name == "Write":
        return "write"
    if name in _READ_TOOLS:
        return "read"
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _WEB_TOOLS:
        return "web"
    if name.startswith("mcp__"):
        return "mcp"
    if name in _TASK_TOOLS:
        return "task"
    if name == "TodoWrite":
        return "todo"
    return "other"


def _parent_of(obj: dict[str, Any]) -> str:
    """v1.418.0 (C4.2): the helper step (Task / Agent tool_use id) a frame
    belongs to — Claude Code's ``parent_tool_use_id`` — or ""."""
    return str(obj.get("parent_tool_use_id") or "")


def _with_parent(ev: dict[str, Any], parent: str) -> dict[str, Any]:
    if parent:
        ev["parent"] = clip(parent, 200)
    return ev


def _base(path: Any) -> str:
    return os.path.basename(str(path or "").replace("\\", "/")) or str(path or "")


def _mcp_name(name: str) -> str:
    parts = name.split("__")
    if len(parts) >= 3:
        return f"{parts[1]} › {'__'.join(parts[2:])}"
    return name


def tool_title(name: str, inp: dict[str, Any]) -> str:
    """One line, plain words."""
    kind = tool_kind(name)
    path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    if kind == "command":
        # v1.418.0 (C4.4): Claude Code's Bash / PowerShell input carries the
        # model's own short description of the command — the title; the
        # command itself goes under it (:func:`tool_subtitle`). A cut title
        # ends in "…".
        desc = one_line(inp.get("description"), 90)
        if desc:
            return desc
        cmd = one_line(inp.get("command"), 90)
        return f"Run {cmd}" if cmd else "Run a command"
    if kind == "edit":
        return f"Edit {_base(path)}" if path else "Edit a file"
    if kind == "write":
        return f"Write {_base(path)}" if path else "Write a file"
    if kind == "read":
        return f"Read {_base(path)}" if path else "Read a file"
    if kind == "search":
        pat = one_line(inp.get("pattern"), 60)
        return f"Search for {pat}" if pat else "Search the files"
    if kind == "web":
        if name == "WebSearch":
            q = one_line(inp.get("query"), 80)
            return f"Search the web for {q}" if q else "Search the web"
        url = one_line(inp.get("url"), 90)
        return f"Fetch {url}" if url else "Fetch a web page"
    if kind == "mcp":
        return f"Use {_mcp_name(name)}"
    if kind == "task":
        what = one_line(inp.get("description"), 90)
        return W.HELPER_TITLE.format(what=what) if what else W.HELPER_TITLE_PLAIN
    if kind == "todo":
        return "Update the plan"
    if name == "AskUserQuestion":
        return "Ask you a question"
    if name == "ExitPlanMode":
        return "Show the plan"
    return f"Use {name}"


def tool_subtitle(name: str, inp: dict[str, Any]) -> str:
    """v1.418.0 (C4.4): the second line of a command whose title is its
    description — the command itself ("" when the title is the command)."""
    if tool_kind(name) == "command" and one_line(inp.get("description"), 90):
        return one_line(inp.get("command"), 300)
    return ""


def _input_json(inp: dict[str, Any]) -> str:
    """The whole input as one JSON line (unmasked, uncut: :func:`clip` or
    :func:`with_full` does both)."""
    try:
        return json.dumps(inp, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def _input_line(inp: dict[str, Any]) -> str:
    return clip(_input_json(inp), INPUT_CHARS)


#: v1.418.0 (C4.2): a tool row whose input is still being written is updated
#: at most this often (seconds): ≤ 4 a second.
PARTIAL_EVERY_S = 0.25
#: How much of an input being written the driver keeps to read its fields
#: (file path, command, description …: they come first) and to show its end.
PARTIAL_HEAD_CHARS = 4_000
PARTIAL_TAIL_CHARS = 2_000
_PARTIAL_FIELD = re.compile(
    r'"(file_path|notebook_path|path|command|description|pattern|url|query|subagent_type)"\s*:\s*"((?:[^"\\]|\\.)*)'
)


def partial_input(head: str) -> dict[str, Any]:
    """The plain fields an input still being written already holds (a
    string value may be cut — what came so far)."""
    out: dict[str, Any] = {}
    for m in _PARTIAL_FIELD.finditer(head or ""):
        raw = m.group(2)
        if raw.endswith("\\") and not raw.endswith("\\\\"):
            raw = raw[:-1]
        try:
            out.setdefault(m.group(1), json.loads(f'"{raw}"'))
        except ValueError:
            out.setdefault(m.group(1), raw)
    return out


#: v1.418.0 (C4.5): the most of a file read back to find where an edit landed.
LINE_READ_BYTES = 2 * 1024 * 1024


def line_of(path: str, needle: str) -> int | None:
    """BLOCKING: the line (1-based) where ``needle`` starts in the file at
    ``path`` — read at most :data:`LINE_READ_BYTES` — else None."""
    if not path or not needle:
        return None
    # v1.418.0 review: a model-written path is judged by its SPELLING before
    # anything touches the disk — opening ``\\host\share\x`` (or a link that
    # leads there) is an SMB connection that hands out a sign-in hash (the
    # v1.379.0 lesson); links are walked one hop at a time, never resolved.
    from ..harness import code_editor as _ce

    if _ce._network_or_device(path):
        return None
    try:
        path = _ce._walk_links(path)
    except (_ce.EditorRefusal, OSError, ValueError):
        return None
    if _ce._network_or_device(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(LINE_READ_BYTES + 1)
    except OSError:
        return None
    if len(data) > LINE_READ_BYTES:
        return None
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")
    at = text.find(needle.replace("\r\n", "\n"))
    return text.count("\n", 0, at) + 1 if at >= 0 else None


def _edit_needle(name: str, inp: dict[str, Any]) -> str:
    """What an Edit / MultiEdit left in the file, to find its line by."""
    if name == "Edit":
        return str(inp.get("new_string") or "")
    if name == "MultiEdit":
        for e in inp.get("edits") or []:
            if isinstance(e, dict) and str(e.get("new_string") or ""):
                return str(e["new_string"])
    return ""


def _edit_diff(name: str, inp: dict[str, Any]) -> str:
    path = str(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "Write":
        return unified_new_file(path, str(inp.get("content") or ""))
    if name == "Edit":
        return unified_replace(path, str(inp.get("old_string") or ""), str(inp.get("new_string") or ""))
    if name == "MultiEdit":
        parts = [
            unified_replace(path, str(e.get("old_string") or ""), str(e.get("new_string") or ""))
            for e in inp.get("edits") or [] if isinstance(e, dict)
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _first_words(text: Any, cap: int = 300) -> str:
    return one_line(text, cap)


def system_notice(obj: dict[str, Any]) -> str:
    """v1.418.0 (C4.3): what one of Claude Code's ``system`` messages says, in
    its own words ("" = nothing worth a line: ``requesting``, progress ticks,
    bookkeeping). ``init``, ``compact_boundary``, ``api_retry`` and the
    ``compacting`` status are the driver's own (they need its state).

    READ from claude 2.1.316's own bundle (read-only): ``hook_response``
    {hook_name, hook_event, output, stdout, stderr, exit_code?, outcome:
    success|error|cancelled}; ``stop_hook_summary`` {preventedContinuation,
    stopReason, hookErrors}; ``task_started`` {description, is_backgrounded,
    tool_use_id}; ``task_notification`` {status, summary, tool_use_id};
    ``notification`` {text}; ``informational`` / ``local_command`` /
    ``model_fallback`` / ``model_refusal_fallback`` / ``model_consent_fallback``
    {content}; ``permission_denied`` {tool_name, message, decision_reason};
    ``api_error`` {error, retryAttempt, maxRetries}."""
    sub = str(obj.get("subtype") or "")
    if sub == "hook_response":
        outcome = str(obj.get("outcome") or "success")
        said = _first_words(obj.get("output") or obj.get("stdout") or obj.get("stderr") or "")
        if outcome == "success" and not said:
            return ""
        return W.SYS_HOOK.format(
            name=clip(obj.get("hook_name") or "a hook", 80), event=clip(obj.get("hook_event") or "?", 60),
            outcome=W.HOOK_OUTCOMES.get(outcome, clip(outcome, 40)), said=f": {said}" if said else ".",
        )
    if sub == "stop_hook_summary":
        if not obj.get("preventedContinuation"):
            return ""
        why = _first_words(obj.get("stopReason") or "")
        return W.SYS_STOP_HOOK.format(why=f": {why}" if why else ".")
    if sub == "task_started":
        if not obj.get("is_backgrounded") or obj.get("tool_use_id"):
            # A helper step already has its own row (C4.2).
            return ""
        return W.SYS_TASK_STARTED.format(what=_first_words(obj.get("description") or "a task", 200))
    if sub == "task_notification":
        words = W.SYS_TASK_DONE.get(str(obj.get("status") or ""))
        summary = _first_words(obj.get("summary") or obj.get("reason") or "", 300)
        if not words or not summary:
            return ""
        return words.format(summary=summary)
    if sub in ("notification",):
        return _first_words(obj.get("text"), 500)
    if sub in ("informational", "local_command", "model_fallback", "model_refusal_fallback", "model_consent_fallback"):
        text = re.sub(r"</?[a-z][a-z0-9-]*>", " ", str(obj.get("content") or ""))
        return _first_words(text, 500)
    if sub == "permission_denied":
        why = _first_words(obj.get("message") or obj.get("decision_reason") or "", 300)
        tool = clip(obj.get("tool_name") or "an action", 80)
        return W.SYS_REFUSED.format(tool=tool, why=why) if why else ""
    if sub == "api_error":
        n, most = obj.get("retryAttempt"), obj.get("maxRetries")
        attempt = f" (attempt {n} of {most})" if n and most else f" (attempt {n})" if n else ""
        error = obj.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type") or ""
        return W.SYS_API_ERROR.format(error=_first_words(error or "an error", 160), attempt=attempt)
    return ""


def control_refused_words(req: dict[str, Any]) -> str:
    """v1.418.0 (C4.3): a control request Iron Jarvis refuses, said."""
    sub = str(req.get("subtype") or "")
    what = W.CONTROL_ASKS.get(sub) or W.CONTROL_ASK_OTHER.format(subtype=clip(sub or "?", 60))
    return W.CONTROL_REFUSED.format(what=what)


def session_suggestions(raw: Any) -> list[dict[str, Any]]:
    """Claude Code's ``permission_suggestions`` the card may offer as "Allow
    for this session" — NEVER one that switches on bypassPermissions (Full
    access is two presses of its own, never a side effect of an allow)."""
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        if s.get("type") == "setMode" and s.get("mode") not in MODE_OF:
            continue
        if s.get("type") == "setMode" and MODE_OF.get(str(s.get("mode"))) in ("full", "auto"):
            # v1.412.0 review: nor Claude Code's own Auto (its classifier runs
            # actions without asking) — both are chosen in the mode chip.
            return []
        out.append(s)
    return out


def suggested_mode(sugg: list[dict[str, Any]]) -> str:
    """The preset an accepted suggestion switches the session to ("" = none)."""
    for s in sugg:
        if s.get("type") == "setMode":
            return MODE_OF.get(str(s.get("mode")), "")
    return ""


def suggestion_words(sugg: list[dict[str, Any]], button: str = "Allow for this session") -> str:
    """What "Allow for this session" does, in plain words, before the press.
    ``button`` (v1.417.0): the words the card's own button says — "Allow
    similar commands" when "Allow all commands this session" is beside it."""
    parts = []
    for s in sugg:
        kind = s.get("type")
        if kind == "setMode":
            parts.append(f"{button} switches Claude Code to {MODE_LABELS[MODE_OF[str(s['mode'])]]} "
                         "for the rest of this session.")
        elif kind in ("addRules", "replaceRules"):
            rules = []
            for r in s.get("rules") or []:
                if isinstance(r, dict):
                    tool, content = str(r.get("toolName") or ""), str(r.get("ruleContent") or "")
                    rules.append(f"{tool}({content})" if content else tool)
            if rules:
                parts.append(f"{button} always allows " + ", ".join(clip(r, 80) for r in rules)
                             + " for the rest of this session.")
        elif kind == "addDirectories":
            dirs = [clip(d, 120) for d in s.get("directories") or [] if isinstance(d, str)]
            if dirs:
                parts.append(f"{button} lets Claude Code work in " + ", ".join(dirs) + ".")
    if sugg and not parts:
        parts.append(f"{button} saves Claude Code's own permission rule for this session.")
    return " ".join(parts)


def plan_steps(inp: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for t in inp.get("todos") or []:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "pending")
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        out.append({"text": clip(t.get("content") or t.get("activeForm") or "", 300), "status": status})
    return out


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def questions_of(inp: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for q in inp.get("questions") or []:
        if not isinstance(q, dict):
            continue
        row: dict[str, Any] = {
            "question": clip(q.get("question") or "", 500),
            "options": [
                {"label": clip(o.get("label") or "", 120),
                 **({"description": clip(o.get("description"), 300)} if o.get("description") else {})}
                for o in q.get("options") or [] if isinstance(o, dict)
            ],
            "multi": bool(q.get("multiSelect")),
        }
        if q.get("header"):
            row["header"] = clip(q.get("header"), 60)
        out.append(row)
    return out


def answers_for(inp: dict[str, Any], answers: Any) -> dict[str, str]:
    """``{question text: "label"}`` (several labels joined with ", ") from
    ``answers`` = a list aligned with the questions, or a dict keyed by the
    question text or its index."""
    qs = [q for q in inp.get("questions") or [] if isinstance(q, dict)]
    out: dict[str, str] = {}
    for i, q in enumerate(qs):
        text = str(q.get("question") or "")
        if isinstance(answers, list):
            got = answers[i] if i < len(answers) else None
        elif isinstance(answers, dict):
            got = answers.get(text, answers.get(str(i)))
        else:
            got = None
        if got is None:
            continue
        if isinstance(got, list):
            value = ", ".join(str(g) for g in got if str(g).strip())
        else:
            value = str(got)
        if value.strip():
            out[text] = value
    if not out:
        raise AskRefused("Choose an answer for the question first.")
    return out


def user_content(text: str, files: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """BLOCKING (reads the pictures): a user message's ``content`` and the
    notes the user should read. Each picture Claude Code takes (png, jpeg,
    gif, webp; at most :data:`MAX_IMAGE_BYTES`, :data:`MAX_IMAGES` of them)
    rides as an image block AND is listed by its path (so the model can also
    use the file); any other file is listed by its path (Claude Code reads
    files itself). A picture that cannot go as a picture is said — never
    dropped without a word."""
    body = str(text or "")
    images: list[dict[str, Any]] = []
    pictures: list[str] = []
    others: list[str] = []
    notes: list[str] = []
    for f in files:
        mime = IMAGE_TYPES.get(os.path.splitext(f)[1].lower())
        if mime is None:
            others.append(f)
            continue
        name = os.path.basename(f)
        try:
            size = os.path.getsize(f)
        except OSError:
            size = -1
        if size < 0 or size > MAX_IMAGE_BYTES or len(images) >= MAX_IMAGES:
            why = (f"more than {MAX_IMAGES} pictures in one message" if len(images) >= MAX_IMAGES and size >= 0
                   else "larger than the 5 MB Claude Code takes as a picture" if size > MAX_IMAGE_BYTES
                   else "could not be read")
            notes.append(W.PICTURE_AS_PATH.format(name=name, why=why, label=W.label(HARNESS)))
            others.append(f)
            continue
        try:
            with open(f, "rb") as fh:
                data = base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            notes.append(W.PICTURE_AS_PATH.format(name=name, why="could not be read", label=W.label(HARNESS)))
            others.append(f)
            continue
        images.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        pictures.append(f)
    parts = [body] if body.strip() else []
    if pictures:
        parts.append("Pictures I attached (each is also shown to you):\n" + "\n".join(pictures))
    if others:
        parts.append("Files I attached:\n" + "\n".join(f"@{f}" for f in others))
    content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(parts) or body}]
    return content + images, notes


class ClaudeDriver(Driver):
    harness = HARNESS

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._controls = ControlWaiter()
        self._n = 0
        #: v1.439.0: the mode Claude Code goes back to when its plan is
        #: approved without a mode of its own (its ExitPlanMode returns to
        #: ``prePlanMode ?? "default"``, read from 2.1.316's bundle): the
        #: mode the session left for Plan, "ask" for a process started in
        #: Plan, "" when not known (Auto: Claude Code may fall back to
        #: default). Only the plan card's words read it.
        self.pre_plan_mode = ""
        self._interrupting = False
        self._errored = False
        #: message id -> stream text items in order / how many are settled.
        self._stream_text: dict[str, list[str]] = {}
        self._settled: dict[str, int] = {}
        #: v1.418.0 (C4.2): per stream ("" = the main conversation, else the
        #: helper step it runs inside): its message id and its blocks.
        self._streams: dict[str, dict[str, Any]] = {}
        #: message ids whose blocks came as stream deltas (their thinking is
        #: never said twice).
        self._streamed: set[str] = set()
        #: message id -> thinking blocks said from whole messages so far.
        self._thought: dict[str, int] = {}
        #: tool_use id -> (name, input).
        self._tools: dict[str, tuple[str, dict[str, Any]]] = {}
        #: tool_use id -> the helper step it ran inside ("" = none).
        self._tool_parent: dict[str, str] = {}
        #: v1.418.0 (C4.3): a compaction said "compacting" already.
        self._compacting = False
        #: v1.418.0 review: the edits' line reads in flight (asyncio holds a
        #: task only weakly: one nobody holds can be collected unrun).
        self._line_tasks: set[asyncio.Task] = set()
        #: The account limit the harness reported (``terminals.limit_state``).
        self.limit: Any = None
        #: v1.412.0: the uuid each message went out with -> the note's ref
        #: ("" = the turn's own message), and whether Claude Code says
        #: ``command_lifecycle`` at all (an older one does not: no notes).
        self._uuids: dict[str, str] = {}
        self._lifecycle = False
        #: The uuids Claude Code said it QUEUED (it will run them).
        self._queued: set[str] = set()
        #: A reply's end held back while a note waits to run (see _on_result).
        self._held: dict[str, Any] | None = None
        self._hold_timer: asyncio.TimerHandle | None = None
        #: v1.414.0 (a USER BUG): the uuid of the turn's OWN message and
        #: whether Claude Code has STARTED on it. A `result` that lands before
        #: that belongs to earlier work (a background task's notice, measured
        #: live: two results with 0 tokens in and out came first) and must not
        #: end the reply — the answer to the user's message came after it.
        self._turn_mid = ""
        self._turn_started = False
        #: v1.417.0 (a USER BUG): the background tasks Claude Code says are
        #: open (``system`` ``task_started`` .. ``task_notification``; read
        #: from the 2.1.316 binary): helper agents and background commands
        #: that outlive the reply which started them. task_id -> task_type.
        self._tasks: dict[str, str] = {}
        #: v1.417.0: Claude Code is writing a reply NO message of the user's
        #: owns — it woke itself when a background task ended (measured:
        #: task_notification -> a second system init -> a new assistant reply
        #: -> result). Set by the notice and by any reply frame with no turn
        #: open; cleared by that reply's result, a new message, the exit.
        self._alone = False

    #: v1.422.0 (kept across a restart): the per-tool maps grow for a whole
    #: session; only the newest entries can still be asked about (a step's
    #: input is read again only when its result comes — a few are ever open),
    #: and an input's text is kept to what the log keeps of it anyway (a
    #: Write of a whole file would otherwise ride every state kept).
    STATE_DICT_CAPS = {"_tools": 50, "_tool_parent": 400}
    STATE_TEXT_CAPS = {"_tools": 24_000}

    def after_restore(self) -> None:
        """v1.422.0: a reply's end held back for a note (``_held``) waits on a
        timer, which has no kept form: it is armed again."""
        if self._held is not None:
            self._arm_hold()

    @property
    def background_tasks(self) -> int:
        return len(self._tasks)

    @property
    def background_helpers(self) -> int:
        """v1.417.0: the open background tasks that are helper AGENTS (a
        background command — a dev server — is not a helper). v1.417.0
        review: Claude Code's own task table calls ``local_workflow`` an agent
        too, and an agent team's member (``in_process_teammate``) is a helper
        (task types read from the 2.1.316 binary). Read from a worker thread
        (``GET /chat/turns/running``) while the loop changes the table: a copy
        is taken in one step, never walked live."""
        return sum(1 for kind in self._tasks.copy().values() if is_helper_task(kind))

    @property
    def replying_alone(self) -> bool:
        """v1.417.0: a reply of Claude Code's own is being written (see
        ``_alone``)."""
        return self._alone and not self.turn_open

    # -- the command line ------------------------------------------------------
    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        if self.mode == "plan":
            # Started in Plan: Claude Code has no mode to go back to, so an
            # approved plan leaves it in its default (Ask first).
            self.pre_plan_mode = "ask"
        out = [
            "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages",
            # v1.418.0 (C4.3): every hook's output in the stream (without it
            # Claude Code reports only SessionStart / Setup hooks — read from
            # claude 2.1.316's own bundle: "Include all hook lifecycle events
            # in the output stream").
            INCLUDE_HOOKS_FLAG,
            "--permission-prompts", "host",
            "--permission-prompt-tool", "stdio",
            "--permission-mode", MODES.get(self.mode, "default"),
            # v1.412.0: Full access can be CHOSEN later in the session (the
            # mode chip's two presses); without this Claude Code refuses the
            # live switch (`bypass_not_launched`). It switches nothing on.
            ALLOW_FULL_FLAG,
        ]
        if self.model:
            out += ["--model", self.model]
        if self.effort:
            out += ["--effort", self.effort]
        out += ["--resume", session_id] if resume else ["--session-id", session_id]
        return out

    # -- control requests -------------------------------------------------------
    def _rid(self, what: str) -> str:
        self._n += 1
        return f"ij-{what}-{self._n}"

    async def control(self, subtype: str, **fields: Any) -> dict[str, Any]:
        rid = self._rid(subtype)
        fut = self._controls.make(rid)
        await self.write({"type": "control_request", "request_id": rid, "request": {"subtype": subtype, **fields}})
        return await asyncio.wait_for(fut, timeout=CONTROL_TIMEOUT_S)

    async def open(self, *, session_id: str, resume: bool) -> None:
        self.session_id = session_id
        try:
            res = await self.control("initialize")
        except Exception:  # noqa: BLE001 — the conversation still works without it
            return
        cmds = res.get("commands") if isinstance(res, dict) else None
        listed = [c for c in (cmds or []) if isinstance(c, dict) and c.get("name")][:300]
        self.commands = [
            {"name": str(c.get("name") or ""), "description": clip(c.get("description") or "", 200),
             "argument_hint": clip(c.get("argumentHint") or "", 80)}
            for c in listed
        ]
        if self.commands:
            # v1.441.0 (review P3): kept per FOLDER — the list holds the
            # folder's own commands and skills beside Claude Code's (MEASURED
            # on claude 2.1.316, ``initialize`` in a scratch folder with a
            # ``.claude/commands/projfoo.md``: ``{name: "projfoo", description:
            # "… (project)"}`` with no ``builtin``, and ``builtin: true`` on
            # each of Claude Code's own, bundled skills included). The bare key
            # keeps only the ``builtin`` rows (the same in every folder); an
            # older Claude Code that marks none keeps its whole list there.
            LAST_COMMANDS[commands_key(HARNESS, self.cwd)] = list(self.commands)
            marked = any("builtin" in c for c in listed)
            LAST_COMMANDS[HARNESS] = [row for row, c in zip(self.commands, listed)
                                      if c.get("builtin") is True or not marked]

    # -- the user's side --------------------------------------------------------
    async def _user_message(self, text: str, files: list[str], ref: str) -> dict[str, Any]:
        """One stream-json user message: the words, each picture as an image
        block (read off the loop), any other file by its path — and a uuid,
        so Claude Code says when it read it (``command_lifecycle``)."""
        content, notes = await asyncio.to_thread(user_content, text, files)
        for note in notes:
            self.emit(make("notice", text=note))
        mid = str(uuid.uuid4())
        self._uuids[mid] = ref
        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": self.session_id,
            "uuid": mid,
        }

    async def send_user(self, text: str, files: list[str]) -> None:
        msg = await self._user_message(text, files, "")
        self._turn_mid = str(msg.get("uuid") or "")
        self._turn_started = False
        self.turn_open = True
        self._alone = False
        self._errored = False
        self._interrupting = False
        await self.write(msg)

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """v1.412.0: Claude Code's own steer — a user message written while
        the turn runs. It reads it at the next step of this turn, or runs it
        right after (the turn stays open until it has)."""
        if not self.turn_open:
            raise ControlRefused(W.STEER_TOO_LATE.format(label=W.label(HARNESS)))
        for _ in range(int(LIFECYCLE_WAIT_S / 0.05)):
            # It says "queued" for the turn's own message at once (measured);
            # a note typed in that first instant waits for it.
            if self._lifecycle or not self.turn_open:
                break
            await asyncio.sleep(0.05)
        if not self._lifecycle:
            # An older Claude Code that never says when it read a message: the
            # note could run as a turn nobody follows.
            raise ControlRefused(W.STEER_UNSUPPORTED.format(label=W.label(HARNESS)))
        msg = await self._user_message(text, files, ref)
        if not self.turn_open:
            # v1.412.0 review: the reply ended while the note was prepared (a
            # picture read off the loop): written now, Claude Code would run
            # it as a turn nobody follows while the box gets it back.
            self._uuids.pop(str(msg.get("uuid") or ""), None)
            raise ControlRefused(W.STEER_TOO_LATE.format(label=W.label(HARNESS)))
        # Marked BEFORE the write: a `result` that lands during it holds the
        # reply's end for this note (see _on_result).
        self.steers[ref] = "sent"
        await self.write(msg)
        return "step"

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        """The answer reached Claude Code: a session-wide allow that set a mode
        is now this session's mode. v1.439.0 (review): so is the mode an
        approved plan goes back to (Claude Code leaves Plan for it)."""
        if decision == "allow_session" and ask.data.get("sets_mode"):
            self.mode = ask.data["sets_mode"]
            self.pre_plan_mode = ""
        else:
            back = self.plan_exit_mode(ask, decision)
            if back:
                self.mode = back
                self.pre_plan_mode = ""

    def plan_exit_mode(self, ask: Ask, decision: str) -> str:
        """v1.441.0 (review P1): the mode an approved plan leaves the session
        in NOW — "" when it leaves none. READ from claude 2.1.316's own bundle
        (ExitPlanModeV2Tool): Claude Code goes back to its ``prePlanMode``
        only while it is STILL in plan mode; a mode the user picked on the
        chip while the card waited is already Claude Code's and stays. So the
        mode is read at the answer, never the one the card was raised with
        (the card's could put the row back in a mode the user had left —
        Full access included)."""
        if decision not in ("allow", "allow_session") or not ask.data.get("leaves_plan_to"):
            return ""
        if decision == "allow_session" and ask.data.get("sets_mode"):
            return ""  # the button names its own mode (``sets_mode``)
        if self.mode != "plan":
            return ""
        # "" (it came from Auto, the v1.439.0 trade-off): the chip stays on
        # Plan, as when the card was raised.
        back = self.pre_plan_mode
        return back if back in MODE_LABELS and back != "plan" else ""

    def _plan_then(self) -> tuple[str, str]:
        """The plan card's ``(then_mode, leaves_plan_to)`` for the mode the
        driver is in NOW ("" = not known / none) — the ONE rule the card is
        raised with and (v1.441.0 review) re-sent with when the mode moves
        while it waits, so its words and :meth:`plan_exit_mode` agree."""
        then = self.pre_plan_mode if self.mode == "plan" else self.mode
        if then not in MODE_LABELS or then == "plan":
            return "", ""
        # v1.439.0 (review): Allow once leaves Plan for it, so the driver
        # and the session row follow (``answered``).
        return then, (then if self.mode == "plan" else "")

    def refresh_ask(self, ask: Ask) -> "dict[str, Any] | None":
        """v1.441.0 (review of lane fix1): the mode moved while ``ask``
        waited — the plan card's new event when its words change (the
        caller re-sends it; the page replaces the card by its id), else
        None. Its ``leaves_plan_to`` follows too."""
        if ask.tool != "ExitPlanMode" or ask.kind != "approval":
            return None
        then, leaves = self._plan_then()
        if leaves:
            ask.data["leaves_plan_to"] = leaves
        else:
            ask.data.pop("leaves_plan_to", None)
        if (ask.event.get("then_mode") or "") == then:
            return None
        ev = dict(ask.event)
        if then:
            ev["then_mode"] = then
        else:
            ev.pop("then_mode", None)
        ask.event = ev
        return ev

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        """The ``control_response`` for the user's answer — or AskRefused,
        before anything is sent."""
        inp = dict(ask.data.get("input") or {})
        if ask.kind == "question":
            if decision == "deny":
                resp: dict[str, Any] = {"behavior": "deny", "message": message or "The user chose not to answer."}
            else:
                inp["answers"] = answers_for(inp, answers)
                resp = {"behavior": "allow", "updatedInput": inp}
        elif decision == "deny":
            resp = {"behavior": "deny", "message": (message or "The user said no to this.")[:2000]}
        elif decision == ALLOW_COMMANDS:
            # v1.417.0 (C1.1): Iron Jarvis's own whole-tool rule for the
            # session — never Claude Code's per-prefix suggestion.
            if not ask.data.get("all_commands") or self.mode in ALL_COMMANDS_NOT_IN:
                # (review) also when the mode moved to Auto / Plan while the
                # card waited: Claude Code would not keep the rule there.
                raise AskRefused(W.ALL_COMMANDS_NOT_HERE.format(label=W.label(HARNESS)))
            resp = {"behavior": "allow", "updatedInput": inp,
                    "updatedPermissions": [all_commands_rule()]}
        elif decision == "allow_session":
            sugg = ask.data.get("suggestions") or []
            if not sugg:
                raise AskRefused("Claude Code offers no session-wide allow for this one — allow it once.")
            resp = {"behavior": "allow", "updatedInput": inp, "updatedPermissions": sugg}
        else:
            resp = {"behavior": "allow", "updatedInput": inp}
        return {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": ask.native, "response": resp},
        }

    async def interrupt(self) -> None:
        self._interrupting = True
        try:
            # v1.412.0: a note not read yet is CANCELLED with the stop (it
            # would otherwise run on its own after it) and goes back to the
            # box; `cancelled` names it.
            res = await self.control("interrupt", cancel_queued=True)
        except asyncio.TimeoutError:
            return
        for mid in (res.get("cancelled") if isinstance(res, dict) else None) or []:
            ref = self._uuids.get(str(mid))
            if ref and self.steers.get(ref) == "sent":
                self.steers[ref] = "cancelled"
        if self._held is not None and not any(s == "sent" for s in self.steers.values()):
            # The reply had ended and only waited for a note the stop just
            # cancelled: nothing more will come from Claude Code.
            self._finish(make("done", stop="interrupted"))

    async def set_mode(self, mode: str) -> str:
        try:
            await self.control("set_permission_mode", mode=MODES[mode])
        except ControlRefused as exc:
            raise ControlRefused(f"Claude Code did not change its mode: {exc}") from None
        if mode == "plan" and self.mode != "plan":
            self.pre_plan_mode = "" if self.mode == "auto" else self.mode
        elif mode != "plan":
            self.pre_plan_mode = ""
        self.mode = mode
        return "now"

    #: v1.439.0 (review): off while a typed ``/model`` sets the model — that
    #: path says the switch itself, in one line (never two).
    say_model_switch = True

    async def set_model(self, model: str) -> str:
        try:
            res = await self.control("set_model", model=model)
        except ControlRefused as exc:
            raise ControlRefused(f"Claude Code did not switch models: {exc}") from None
        self.model = model
        if self.say_model_switch and isinstance(res, dict) and res.get("model"):
            self.emit(make("notice", text=f"Claude Code now answers with {clip(res['model'], 80)}."))
        return "now"

    async def rename(self, title: str) -> None:
        """v1.439.0: Claude Code's own ``rename_session`` control request —
        READ from claude 2.1.316's own schema: ``{subtype: "rename_session",
        title, source?: "remote" | "host", session_id?}``; ``host`` is a
        rename the user made in the hosting application, which Claude Code
        counts as a user rename (``claude --resume`` shows it)."""
        name = str(title or "").strip()
        if not name or not self.session_id:
            return
        await self.control("rename_session", title=name, source="host", session_id=self.session_id)

    async def context_breakdown(self) -> dict[str, Any] | None:
        """v1.415.0: Claude Code's OWN ``/context`` — its ``get_context_usage``
        control request (measured, claude 2.1.316: answered from what the
        session holds, with NO model call, signed in or not): every part, its
        tokens, the automatic-summary mark, the instruction files and the
        extension tools. The service asks only while no reply runs."""
        from ..context.breakdown import claude_breakdown

        # v1.415.0 review: ``detail: "summary"`` — Claude Code's default is
        # "full", which (its own schema, claude 2.1.316) "counts each category
        # with the token-count API": the conversation goes to Anthropic once
        # per category on every read, through a queue Claude Code serialises
        # and waits up to 300 s for at teardown. "summary" answers "from the
        # last response's usage and local estimates" — the same shape, no
        # network (measured, signed out: 0.5 s).
        try:
            res = await self.control("get_context_usage", detail="summary")
        except Exception:  # noqa: BLE001 — a stopped tool or a refusal: no reading
            return None
        return claude_breakdown(res)

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._controls.fail_all(HarnessGone("Claude Code stopped"))
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None
        self._held = None
        self._tasks.clear()
        self._alone = False

    # -- the harness's side (ON THE LOOP) ---------------------------------------
    def on_message(self, obj: dict[str, Any]) -> None:
        kind = obj.get("type")
        if kind in ("stream_event", "assistant") and not self.turn_open:
            # A reply no message owns (Claude Code woke itself).
            self._alone = True
        if kind == "control_response":
            r = obj.get("response") or {}
            rid = r.get("request_id")
            if r.get("subtype") == "error":
                self._controls.settle(rid, error=ControlRefused(clip(r.get("error") or "refused", 300)))
            else:
                self._controls.settle(rid, r.get("response") or {})
            return
        if kind == "control_request":
            self._on_control_request(obj)
            return
        if kind == "system":
            self._on_system(obj)
            return
        if kind == "stream_event":
            self._on_stream(obj.get("event") or {}, _parent_of(obj))
            return
        if kind == "assistant":
            self._on_assistant(obj)
            return
        if kind == "user":
            self._on_user(obj)
            return
        if kind == "rate_limit_event":
            self._on_rate_limit(obj.get("rate_limit_info") or {})
            return
        if kind == "result":
            self._on_result(obj)
            return
        if kind == "command_lifecycle":
            self._on_lifecycle(obj)

    def _on_lifecycle(self, obj: dict[str, Any]) -> None:
        """v1.412.0: Claude Code says what became of each message it was sent
        (the client's uuid). A note it STARTED (absorbed into the turn, or run
        right after it) was read; one it CANCELLED goes back to the box."""
        mid = str(obj.get("command_uuid") or obj.get("uuid") or "")
        if mid not in self._uuids:
            return
        self._lifecycle = True
        ref = self._uuids[mid]
        state = str(obj.get("state") or "")
        if state == "queued":
            self._queued.add(mid)
        if not ref:
            if mid == self._turn_mid:
                if state in ("started", "completed"):
                    self._turn_started = True
                elif state == "cancelled" and not self._turn_started and self.turn_open:
                    # Dropped before it ever ran (a Stop with the message still
                    # waiting): no result will come for it.
                    self._finish(make("done", stop="interrupted"))
            return
        if state in ("started", "completed"):
            self.note_read(ref)
        elif state == "cancelled" and self.steers.get(ref) == "sent":
            self.steers[ref] = "cancelled"
            if self._held is not None and not any(s == "sent" for s in self.steers.values()):
                # v1.412.0 review: the reply only waited for this note, and
                # Claude Code dropped it: the reply ends now (not 15 s later).
                self._finish(self._held)

    def _on_control_request(self, obj: dict[str, Any]) -> None:
        req = obj.get("request") or {}
        rid = obj.get("request_id")
        if req.get("subtype") != "can_use_tool":
            # hook callbacks, MCP messages … Iron Jarvis registered none: say so
            # — to Claude Code, and (v1.418.0, C4.3) on the screen, with what
            # it asked.
            self.emit(make("notice", text=control_refused_words(req)))
            asyncio.ensure_future(self._refuse_control(rid, "Iron Jarvis does not handle this request"))
            return
        name = str(req.get("tool_name") or "")
        inp = req.get("input") if isinstance(req.get("input"), dict) else {}
        sugg = session_suggestions(req.get("permission_suggestions"))
        ask_id = new_ask_id()
        if name == "AskUserQuestion":
            ev = make("question", request_id=ask_id, questions=questions_of(inp))
            self.raise_ask(Ask(ask_id, "question", name, rid, {"input": inp}, ev, ev["at"]))
            return
        kind = tool_kind(name)
        akind = {"command": "command", "edit": "edit", "write": "edit", "web": "network"}.get(kind, "tool")
        # v1.417.0 (C1.1): a command card offers "Allow all commands this
        # session" (Iron Jarvis's own whole-tool rule), never in Full access,
        # Auto or Plan (:data:`ALL_COMMANDS_NOT_IN`).
        all_commands = akind == "command" and name in ALL_COMMANDS_TOOLS and self.mode not in ALL_COMMANDS_NOT_IN
        detail = clip(req.get("description") or req.get("decision_reason") or "", DETAIL_CHARS)
        title = W.PLAN_CARD_TITLE if name == "ExitPlanMode" else tool_title(name, inp)
        if detail.strip() == title.strip():
            # v1.418.0 (C4.4): a command's title IS its description now —
            # never said twice on the card.
            detail = ""
        # v1.439.0: on the plan card the session-wide allow names the mode it
        # leaves the session in, so that mode switch is not said again.
        plan_sets = suggested_mode(sugg) if name == "ExitPlanMode" else ""
        leaves_plan_to = ""
        if plan_sets == "plan":
            plan_sets = ""
        said = [x for x in sugg if x.get("type") != "setMode"] if plan_sets else sugg
        effect = suggestion_words(said, W.ALLOW_SIMILAR if all_commands else "Allow for this session") if said else ""
        if effect:
            detail = f"{detail}\n\n{effect}" if detail else effect
        ev = make(
            "approval", request_id=ask_id, kind=akind, title=title,
            detail=detail, can_session=bool(sugg),
        )
        if all_commands:
            ev["can_all_commands"] = True
        if name == "ExitPlanMode" and str(inp.get("plan") or "").strip():
            # v1.417.0 (C4.1): the plan itself, read before the answer.
            ev["plan"] = clip(inp.get("plan"), PLAN_CHARS)
        if name == "ExitPlanMode":
            # v1.439.0: the mode each allow leaves the session in, so the
            # card names it ("Approve, then ask first" / "Approve and edit
            # freely"); a key is absent when it is not known.
            then, leaves_plan_to = self._plan_then()
            if then:
                ev["then_mode"] = then
            if plan_sets:
                ev["sets_mode"] = plan_sets
        if not sugg:
            # No session-wide allow offered: the card answers this one call.
            ev["once_only"] = True
        if akind == "command":
            ev["command"] = clip(inp.get("command") or "", 2000)
        path = inp.get("file_path") or inp.get("notebook_path")
        if path:
            ev["path"] = clip(path, 500)
        if akind == "edit":
            diff = _edit_diff(name, inp)
            if diff:
                ev["diff"] = clip(diff, DIFF_CHARS)
        if name.startswith("mcp__") or akind == "tool":
            ev["tool"] = clip(name, 120)
        data: dict[str, Any] = {"input": inp, "suggestions": sugg}
        if all_commands:
            data["all_commands"] = True
        mode = suggested_mode(sugg)
        if mode:
            data["sets_mode"] = mode
        if leaves_plan_to:
            data["leaves_plan_to"] = leaves_plan_to
        self.raise_ask(Ask(ask_id, "approval", name, rid, data, ev, ev["at"], bool(sugg)))

    async def _refuse_control(self, rid: Any, why: str) -> None:
        try:
            await self.write({"type": "control_response",
                              "response": {"subtype": "error", "request_id": rid, "error": why}})
        except Exception:  # noqa: BLE001 — a gone process: nothing to answer
            pass

    def _on_system(self, obj: dict[str, Any]) -> None:
        sub = obj.get("subtype")
        if sub == "init":
            sid = str(obj.get("session_id") or "")
            if sid:
                self.session_id = sid
                self.on_session_id(sid)
            mode = MODE_OF.get(str(obj.get("permissionMode") or ""), self.mode)
            self.emit(make(
                "session", harness=HARNESS, harness_session_id=sid,
                model=clip(obj.get("model") or self.model, 120), cwd=clip(obj.get("cwd") or self.cwd, 500),
                mode=mode,
            ))
            # v1.426.0 (E4): its own MCP servers, as it reported them (measured
            # on 2.1.316: ``mcp_servers: [{name, status, source}]``); a frame
            # without the key says nothing (never "no servers").
            from .tool_setup import claude_servers, note_servers

            note_servers(self, claude_servers(obj.get("mcp_servers")))
        elif sub == "task_started":
            tid = str(obj.get("task_id") or "")
            if tid:
                self._tasks[tid] = str(obj.get("task_type") or "")
            # v1.418.0 (C4.3): the start is said too (a backgrounded task with
            # no row of its own) — the bookkeeping above never swallows it.
            text = system_notice(obj)
            if text:
                self.emit(make("notice", text=text))
        elif sub == "task_notification":
            self._tasks.pop(str(obj.get("task_id") or ""), None)
            if not self.turn_open:
                # Claude Code wakes itself to report what the task did.
                self._alone = True
            text = system_notice(obj)
            if text:
                self.emit(make("notice", text=text))
        elif sub == "compact_boundary":
            self._compacting = False
            self.emit(make("notice", text="Claude Code compacted the conversation to make room."))
        elif sub == "api_retry":
            n = obj.get("attempt")
            self.emit(make("notice", text=f"Claude Code is retrying the request{f' (attempt {n})' if n else ''}."))
        elif sub == "status":
            # v1.418.0 (C4.3): ``compacting`` comes every 30 s while it lasts
            # (claude 2.1.316's bundle): said once; a failed one says why.
            status = obj.get("status")
            if status == "compacting":
                if not self._compacting:
                    self._compacting = True
                    self.emit(make("notice", text=W.SYS_COMPACTING))
            elif status is None or status == "":
                self._compacting = False
                if obj.get("compact_result") == "failed":
                    why = one_line(obj.get("compact_error") or "no reason given", 300)
                    self.emit(make("notice", text=W.SYS_COMPACT_FAILED.format(why=why)))
        else:
            text = system_notice(obj)
            if text:
                self.emit(make("notice", text=text))

    def _stream(self, parent: str) -> dict[str, Any]:
        return self._streams.setdefault(parent, {"msg": "", "blocks": {}})

    def _on_stream(self, ev: dict[str, Any], parent: str = "") -> None:
        st = self._stream(parent)
        t = ev.get("type")
        if t == "message_start":
            st["msg"] = str((ev.get("message") or {}).get("id") or "")
            st["blocks"] = {}
            if st["msg"]:
                self._streamed.add(st["msg"])
        elif t == "content_block_start":
            idx = int(ev.get("index") or 0)
            block = ev.get("content_block") or {}
            msg = st["msg"]
            item = f"{msg}:{idx}"
            st["blocks"][idx] = {"type": block.get("type"), "item": item}
            if block.get("type") == "text":
                self._stream_text.setdefault(msg, []).append(item)
            elif block.get("type") == "tool_use":
                # v1.418.0 (C4.2): the step's row from the moment the model
                # starts writing it, not once the whole input has arrived.
                tid = str(block.get("id") or "")
                name = str(block.get("name") or "")
                st["blocks"][idx].update(tool=tid, name=name, head="", tail="", n=0, sent=-1.0, dirty=False)
                if tid and tid not in self._tools:
                    self._partial_tool(st["blocks"][idx], parent)
        elif t == "content_block_delta":
            idx = int(ev.get("index") or 0)
            block = st["blocks"].get(idx) or {}
            item = block.get("item") or f"{st['msg']}:{idx}"
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.emit(_with_parent(make("text", item=item, delta=clip(delta["text"], TEXT_CHARS)), parent))
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                self.emit(_with_parent(make("thinking", item=item, delta=clip(delta["thinking"], TEXT_CHARS)), parent))
            elif delta.get("type") == "input_json_delta" and block.get("tool"):
                piece = str(delta.get("partial_json") or "")
                if not piece:
                    return
                if len(block["head"]) < PARTIAL_HEAD_CHARS:
                    block["head"] = (block["head"] + piece)[:PARTIAL_HEAD_CHARS]
                block["tail"] = (block["tail"] + piece)[-PARTIAL_TAIL_CHARS:]
                block["n"] += len(piece)
                block["dirty"] = True
                if block["tool"] not in self._tools and time.monotonic() - block["sent"] >= PARTIAL_EVERY_S:
                    self._partial_tool(block, parent)
        elif t == "content_block_stop":
            idx = int(ev.get("index") or 0)
            block = st["blocks"].get(idx) or {}
            if block.get("tool") and block.get("dirty") and block["tool"] not in self._tools:
                # The last words written since the previous update.
                self._partial_tool(block, parent)

    def _partial_tool(self, block: dict[str, Any], parent: str) -> None:
        """v1.418.0 (C4.2): a running row for a step whose input is still
        being written: the title from the fields that arrived (a path, a
        command, a description …), the newest part of the input, how much
        has been written, ``partial: true``."""
        name = str(block.get("name") or "")
        tid = str(block.get("tool") or "")
        fields = partial_input(block.get("head") or "")
        ev = make("tool", item=tid, kind=tool_kind(name), name=clip(name, 120), title=tool_title(name, fields),
                  input=clip_tail(block.get("tail") or "", INPUT_CHARS), status="running", partial=True)
        if block.get("n"):
            ev["input_chars"] = int(block["n"])
        sub = tool_subtitle(name, fields)
        if sub:
            ev["subtitle"] = sub
        path = fields.get("file_path") or fields.get("notebook_path")
        if path:
            ev["path"] = clip(path, 500)
        block["sent"] = time.monotonic()
        block["dirty"] = False
        self.emit(_with_parent(ev, parent))

    def _text_item(self, msg: str) -> str:
        items = self._stream_text.get(msg) or []
        k = self._settled.get(msg, 0)
        self._settled[msg] = k + 1
        return items[k] if k < len(items) else f"{msg}:t{k}"

    def _on_assistant(self, obj: dict[str, Any]) -> None:
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        code = obj.get("error") or message.get("error")
        msg = str(message.get("id") or "")
        parent = _parent_of(obj)
        if code:
            said = _result_text(message.get("content"))
            self._failure(said or str(code))
            return
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text" and str(block.get("text") or "").strip():
                self.emit(_with_parent(
                    make("text_done", item=self._text_item(msg), text=clip(block["text"], TEXT_CHARS)), parent))
            elif bt == "thinking" and msg not in self._streamed and str(block.get("thinking") or "").strip():
                # v1.418.0 (C4.2): a helper's messages come whole (no stream
                # deltas, measured on claude 2.1.316): its thinking is said
                # from the message itself.
                k = self._thought.get(msg, 0)
                self._thought[msg] = k + 1
                self.emit(_with_parent(
                    make("thinking", item=f"{msg}:k{k}", delta=clip(block["thinking"], TEXT_CHARS)), parent))
            elif bt == "tool_use":
                self._tool_started(block, parent)

    def _tool_started(self, block: dict[str, Any], parent: str = "") -> None:
        tid = str(block.get("id") or "")
        name = str(block.get("name") or "")
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        self._tools[tid] = (name, inp)
        self._tool_parent[tid] = parent
        kind = tool_kind(name)
        ev = make("tool", item=tid, kind=kind, name=clip(name, 120), title=tool_title(name, inp),
                  input=_input_line(inp), status="running")
        sub = tool_subtitle(name, inp)
        if sub:
            ev["subtitle"] = sub
        path = inp.get("file_path") or inp.get("notebook_path")
        if path:
            ev["path"] = clip(path, 500)
        self.emit(_with_parent(ev, parent))
        if kind == "todo" and not parent:
            # A helper's own checklist is not the conversation's plan.
            self.emit(make("plan", steps=plan_steps(inp)))

    def _on_user(self, obj: dict[str, Any]) -> None:
        if obj.get("isReplay"):
            return
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            return
        meta = obj.get("tool_use_result")
        parent = _parent_of(obj)
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")
            name, inp = self._tools.get(tid, ("", {}))
            failed = bool(block.get("is_error"))
            kind = tool_kind(name)
            ev = make("tool", item=tid, kind=kind, name=clip(name, 120),
                      title=tool_title(name, inp), input="",
                      status="failed" if failed else "done", output="")
            # v1.418.0 (C4.4): the screen gets the first part; the log keeps
            # up to 20,000 characters of each, read back on a press.
            with_full(ev, input_text=_input_json(inp), output_text=_result_text(block.get("content")))
            sub = tool_subtitle(name, inp)
            if sub:
                ev["subtitle"] = sub
            path = inp.get("file_path") or inp.get("notebook_path")
            if path:
                ev["path"] = clip(path, 500)
            if not failed and isinstance(meta, dict):
                diff = ""
                if isinstance(meta.get("structuredPatch"), list) and meta["structuredPatch"]:
                    diff = unified_from_hunks(str(meta.get("filePath") or path or ""), meta["structuredPatch"])
                    hunk = meta["structuredPatch"][0]
                    if isinstance(hunk, dict) and hunk.get("newStart"):
                        ev["line"] = int(hunk["newStart"])
                elif meta.get("type") == "create" and meta.get("content") is not None:
                    diff = unified_new_file(str(meta.get("filePath") or path or ""), str(meta["content"]))
                if diff:
                    ev["diff"] = clip(diff, DIFF_CHARS)
            later_line = ""
            if not failed and kind in ("edit", "write") and "diff" not in ev:
                # v1.418.0 (C4.5): Claude Code sends no ``tool_use_result``
                # with this step (every edit in Full access in the live log; a
                # helper's edits, measured) — the change is read off the
                # step's own input, and its line off the file as it is now.
                diff = _edit_diff(name, inp)
                if diff:
                    ev["diff"] = clip(diff, DIFF_CHARS)
                    if name == "Write":
                        ev["line"] = 1
                    else:
                        later_line = _edit_needle(name, inp)
            self.emit(_with_parent(ev, parent or self._tool_parent.get(tid, "")))
            if later_line and path:
                self._find_line(ev, str(path), later_line)

    def _find_line(self, ev: dict[str, Any], path: str, needle: str) -> None:
        """v1.418.0 (C4.5): read (off the loop, bounded) where an edit landed
        and send the step again with its ``line``."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (a unit test of the parser): no line
            return
        full = path if os.path.isabs(path) or not self.cwd else os.path.join(self.cwd, path)
        base = {k: v for k, v in ev.items() if k not in (FULL_KEY, "at")}

        async def run() -> None:
            try:
                line = await asyncio.to_thread(line_of, full, needle)
            except Exception:  # noqa: BLE001 — no line is no harm
                return
            if line:
                self.emit({"type": "tool", "at": now_iso(), **{k: v for k, v in base.items() if k != "type"},
                           "line": line})

        task = loop.create_task(run())
        self._line_tasks.add(task)
        task.add_done_callback(self._line_tasks.discard)

    def _on_rate_limit(self, info: dict[str, Any]) -> None:
        from . import seen as _seen  # v1.414.0: the Usage tab's windows

        _seen.note_windows(self, _seen.claude_windows(info))
        status = str(info.get("status") or "")
        if status in ("", "allowed", "allowed_warning"):
            return
        from datetime import datetime, timezone

        from ..terminals.limit_state import PaneLimit

        window = str(info.get("rateLimitType") or "usage").replace("_", " ").replace("five hour", "5-hour")
        reset_at = None
        at = info.get("resetsAt")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
            reset_at = datetime.fromtimestamp(float(at), tz=timezone.utc)
        line = f"You've hit your {window} limit"
        self.limit = PaneLimit(line=line, kind="session" if "hour" in window else "usage", reset_at=reset_at)
        self.emit(make("notice", text=f"Claude Code says this account reached its {window} limit."))

    def _failure(self, said: str) -> None:
        from dataclasses import replace

        from ..terminals.limit_state import PaneLimit, detect_limit

        self._errored = True
        ev = W.failure_for(HARNESS, said)
        if ev.get("fix_kind") == "account_limit":
            prior = getattr(self.limit, "reset_at", None)
            found = detect_limit(said) or PaneLimit(line=one_line(said, 300))
            if found.reset_at is None and prior is not None:
                # v1.422.0 (B5): the words named no reset, but Claude Code's own
                # ``rate_limit_event`` did (exact, to the second) — kept, so
                # the reply can wait for it by itself.
                found = replace(found, reset_at=prior)
            self.limit = found
        self.emit(ev)

    def _on_result(self, obj: dict[str, Any]) -> None:
        if self.turn_open and self._lifecycle and self._turn_mid and not self._turn_started and not self._interrupting:
            # v1.414.0: Claude Code has not started on the user's message yet,
            # so this result ends earlier work, not this reply.
            return
        if not self.turn_open:
            # The end of a reply of Claude Code's own.
            self._alone = False
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}

        def num(key: str) -> int:
            v = usage.get(key)
            return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

        cost = obj.get("total_cost_usd")
        ev = make(
            "usage",
            input=num("input_tokens") + num("cache_read_input_tokens") + num("cache_creation_input_tokens"),
            output=num("output_tokens"),
            cache_read=num("cache_read_input_tokens"),
            list_price=True,
        )
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            ev["cost_usd"] = round(float(cost), 6)
        from . import seen as _seen  # v1.414.0: windows + how full, as seen

        _seen.note_context(self, _seen.claude_context(obj))
        self.emit(_seen.attach(ev, self))
        sub = str(obj.get("subtype") or "")
        reason = str(obj.get("terminal_reason") or "")
        if self._interrupting or reason.startswith("aborted"):
            stop = "interrupted"
        elif sub == "error_max_turns":
            stop = "max_turns"
        elif obj.get("is_error") or self._errored or sub.startswith("error"):
            stop = "failed"
            if not self._errored and obj.get("result"):
                self._failure(str(obj.get("result")))
        else:
            stop = "completed"
        done = make("done", stop=stop)
        if stop == "completed" and obj.get("result"):
            done["result"] = clip(obj["result"], 4000)
        if stop != "interrupted" and any(s == "sent" for s in self.steers.values()):
            # v1.412.0: a note the user sent while it worked has not been read
            # yet — Claude Code runs it right after this result, as part of the
            # same reply: the turn stays open until it has (or, if Claude Code
            # never even queued it, until HOLD_FOR_NOTE_S says so).
            self._held = done
            self._errored = False
            self._arm_hold()
            return
        self._finish(done)

    def _finish(self, done: dict[str, Any]) -> None:
        self._held = None
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None
        self._interrupting = False
        self.turn_open = False
        self.emit(done)

    def _arm_hold(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (a unit test of the parser): nothing to wait for
            return
        self._hold_timer = loop.call_later(HOLD_FOR_NOTE_S, self._hold_expired)

    def _hold_expired(self) -> None:
        """Claude Code never said it queued the note: the reply ends, and the
        note goes back to the box (unread) rather than hold the turn forever."""
        self._hold_timer = None
        if self._held is None:
            return
        if any(self.steers.get(self._uuids.get(m, ""), "") == "sent" for m in self._queued):
            self._arm_hold()  # queued: it will run — keep waiting for it
            return
        self._finish(self._held)
