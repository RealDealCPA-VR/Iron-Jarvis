"""The dialect table (v1.366.0, IJ harness Phase 3): each model family meets the
tools it was trained on, translated onto Jarvis's REAL tools.

A model works best with the tool names and argument shapes its maker trained
it on. Claude was trained in Claude Code (``Read`` / ``Edit`` / ``Bash``…),
GPT in Codex (``exec_command`` / ``apply_patch`` / ``update_plan``), Qwen in
Qwen Code (``read_file`` with ``file_path`` / ``edit`` / ``run_shell_command``).
This module is the ONE table that says, for each family:

* the tool names and argument shapes the model is SHOWN
  (:class:`DialectTool`: ``name`` + ``input_schema`` + a description in that
  vendor's style);
* how each call maps onto a real Jarvis tool and its arguments
  (:attr:`DialectTool.translate`, a pure function of the model's arguments
  and the turn's armed set);
* the short system-prompt section for that dialect (:attr:`Dialect.prompt`);
* native vs prompted tool calls (:attr:`Dialect.native_calls`: every dialect
  here sends NATIVE tool schemas; whether a provider then needs the prompted
  form is the router's decision, exactly as for any other turn).

TRANSLATION HAPPENS AT THE BOUNDARY. A model's call is mapped to the real tool
and its real arguments BEFORE ``registry.invoke`` — so the armed-set gate
(``allowed_names``), approvals, the ledger, undo, trust and Local only all see
the REAL tool. Nothing here runs a tool or decides a permission. A name the
dialect does not know passes through unchanged, so a hallucinated tool still
meets the registry's own refusal ("not armed" / "unknown tool").

FAMILIES (:data:`FAMILIES`), read off the SERVING provider + model the way
``providers/reasoning.py`` decides reasoning levels:

* ``claude`` — anthropic, claude-cli, an ``anthropic/…`` route, a ``claude-…``
  id → Claude Code's shapes (inventory of Claude Code 2.1.306,
  ``tests/fixtures/harness_tool_inventory_v1364.json``);
* ``gpt`` — openai, codex-cli, an ``openai/…`` route, a gpt/o-series/codex id
  → Codex 0.157.0's shapes (same inventory);
* ``glm`` → the GENERIC dialect, BY MEASUREMENT (:data:`FAMILY_MEASURED`):
  Z.ai tells its users to code in Claude Code (docs.z.ai, read 2026-10-21),
  but on the benchmark glm-5.3-flash passed exactly as many tasks under
  Jarvis's own names with ~58% of the input tokens and ~64% of the time it
  needed under Claude Code's (2026-10-21). Harness² principle 8: a mapping is
  adopted only when it measures at least as well, cost included. Claude
  Code's dialect stays selectable (``harness="claude"``, ``--dialect claude``);
* ``deepseek`` → the CLAUDE dialect: DeepSeek documents running its models in
  Claude Code (api-docs.deepseek.com/guides/anthropic_api, read 2026-10-21) —
  NOT YET MEASURED;
* ``qwen`` → Qwen Code's shapes, read from QwenLM/qwen-code at commit
  13a4fea5 (2026-10-21; ``packages/core/src/tools/tool-names.ts`` and each
  tool's parameter schema — names and shapes only, no code taken). Two names
  differ from older Qwen Code: the editor is ``edit`` (not ``replace``) and
  the content search is ``grep_search`` (not ``search_file_content``) — NOT
  YET MEASURED;
* ``generic`` — anything else: Jarvis's own tool names, exactly as registered.

Every row says what it rests on (:data:`FAMILY_MEASURED`): a benchmark that
chose it, or "not yet measured" (the vendor's own advice, kept until a
benchmark says otherwise).

DECISION (Codex ``exec_command``): it maps to ``process_start`` with
``until_exit`` — Codex's own semantics: a command that ends within
``yield_time_ms`` returns its output and exit code, one still running returns
a session id the model polls or types into with ``write_stdin``. So a dev
server keeps running and a long build is polled, as Codex's model expects.
When ``process_start`` is not armed (low trust keeps it away; the POST lane
never arms an ask-tier tool) the command runs through ``shell`` instead, with
the sandbox and timeout that tool has.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Callable, Iterable

#: Images ``Read`` / qwen ``read_file`` hand to ``view_image`` instead of
#: ``read_file`` (Claude Code's Read shows images; Jarvis's reader is text).
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

#: v1.402.0 (part 4): the ONE line every question tool's description ends
#: with — Claude Code's ``AskUserQuestion``, Codex's ``request_user_input``
#: (which had none) and Jarvis's own ``ask_user`` (``harness/session_tools``).
#: The prompt's whole policy is ``components.ASK_POLICY``; this is its rule
#: in the place a model reads just before it asks.
ASK_TOOL_RULE = (
    "Ask only what is the user's to decide: what they want or what counts as done, "
    "never how to do it."
)

#: Every family the table knows, and the dialect it speaks. A row is either
#: CHOSEN BY A MEASUREMENT (its :data:`FAMILY_MEASURED` entry names the
#: dialect that won) or the vendor's own advice, marked not yet measured.
FAMILY_DIALECT: dict[str, str] = {
    "claude": "claude",
    "gpt": "gpt",
    # MEASURED 2026-10-21 (FAMILY_MEASURED["glm"]): Claude Code's dialect
    # passed no more tasks than Jarvis's own names and cost ~1.7x the input
    # tokens. Do not map glm back to "claude" without a benchmark that says so.
    "glm": "generic",
    "deepseek": "claude",
    "qwen": "qwen",
    "generic": "generic",
}


@dataclass(frozen=True)
class Measurement:
    """The benchmark a family's row rests on (Harness² principle 8: a dialect
    is adopted only when it measures at least as well as the alternative,
    cost included). ``chosen`` is the dialect the numbers picked — the pin
    holds :data:`FAMILY_DIALECT` to it."""

    date: str
    #: What ran: the command, the model and where it ran, the tasks.
    setup: str
    #: ``(dialect, bench setup id, the numbers)`` for each row compared.
    rows: tuple[tuple[str, str, str], ...]
    chosen: str
    why: str


#: Said for a row nothing has measured yet.
NOT_YET_MEASURED = "not yet measured"

#: What each row of :data:`FAMILY_DIALECT` rests on.
FAMILY_MEASURED: dict[str, "Measurement | str"] = {
    "glm": Measurement(
        date="2026-10-21",
        setup=(
            "ironjarvis bench-harness run --runner jarvis --provider custom --model "
            "glm-5.3-flash (the DGX Spark proxy, called by that exact name), all 30 tasks "
            "(core 20, hard 10), Iron Jarvis 1.363.0 + IJ harness Phase 3"
        ),
        rows=(
            ("generic", "8405051e0b9f",
             "28/30 passed (core 20/20, hard 8/10); 8.5 tool calls, 7.9 rounds, 25.3 s, "
             "1,465,803 input tokens per 30 tasks"),
            ("claude", "34e162cf6d13",
             "28/30 passed (core 20/20, hard 8/10); 12.1 tool calls, 10.7 rounds, 39.3 s, "
             "2,542,447 input tokens per 30 tasks"),
            # The review's confirmation, with glm's row switched to generic
            # (--dialect auto, hard tier only, one run): on the same 10 hard
            # tasks the two rows above took 14.5 / 23.5 tool calls, 53.1 /
            # 89.9 s and 931,401 / 1,980,048 input tokens.
            ("generic", "8cd018764235",
             "confirmation, hard tier only: 9/10 passed; 19.9 tool calls, 17.5 rounds, 75.4 s, "
             "1,773,401 input tokens per 10 tasks"),
        ),
        chosen="generic",
        why=(
            "the same pass rate for ~58% of the input tokens and ~64% of the time; "
            "Claude Code's dialect stays selectable"
        ),
    ),
    "deepseek": NOT_YET_MEASURED,
    "qwen": NOT_YET_MEASURED,
    "claude": NOT_YET_MEASURED,
    "gpt": NOT_YET_MEASURED,
    "generic": "the baseline: Jarvis's own tool names",
}

#: Why each family speaks its dialect (the report and the Handbook read it).
FAMILY_EVIDENCE: dict[str, str] = {
    "claude": "Claude was trained in Claude Code (inventory: Claude Code 2.1.306).",
    "gpt": "GPT's coding models were trained in Codex (inventory: Codex 0.157.0).",
    "glm": (
        "Measured (2026-10-21, glm-5.3-flash, 30 tasks): Jarvis's own tool names passed as many "
        "tasks as Claude Code's, which Z.ai recommends, for ~58% of the input tokens."
    ),
    "deepseek": (
        "DeepSeek documents running its models in Claude Code "
        "(api-docs.deepseek.com/guides/anthropic_api, read 2026-10-21). Not yet measured."
    ),
    "qwen": "Qwen Code's own tools (QwenLM/qwen-code @ 13a4fea5, read 2026-10-21). Not yet measured.",
    "generic": "No dialect known: Jarvis's own tool names.",
}


def family_for(provider: str, model: str) -> str:
    """The model FAMILY of a (provider, model) pair: ``claude``, ``gpt``,
    ``glm``, ``qwen``, ``deepseek`` or ``generic``. Never raises."""
    prov = str(provider or "").strip().lower()
    mid = str(model or "").strip().lower()
    vendor, _, rest = mid.partition("/")
    # A routed id ("anthropic/claude-…", "openai/gpt-…") names its vendor.
    if prov in ("anthropic", "claude-cli") or vendor == "anthropic" or mid.startswith("claude"):
        return "claude"
    if rest.startswith("claude"):
        return "claude"
    for family in ("glm", "qwen", "deepseek"):
        if family in mid:
            return family
    if prov in ("openai", "codex-cli") or vendor == "openai":
        return "gpt"
    name = rest if vendor and rest else mid
    if name.startswith(("gpt-", "gpt5", "gpt6", "codex")) or (
        len(name) > 1 and name[0] == "o" and name[1].isdigit()
    ):
        return "gpt"
    return "generic"


# --------------------------------------------------------------------------- #
# the table's shapes
# --------------------------------------------------------------------------- #

Translate = Callable[[dict[str, Any], frozenset], "tuple[str, dict[str, Any]]"]


@dataclass(frozen=True)
class DialectTool:
    """One tool as a dialect shows it.

    ``targets`` are the real Jarvis tools the call may run, the PRIMARY first:
    the tool is offered when its primary — or, for a tool that falls back,
    any — target is armed (:attr:`offered_when`). ``translate(args, armed)``
    returns ``(real_tool, real_args)``; it is pure and never raises.
    ``aliases`` are names the model may use for it (older names the vendor
    retired) that are translated the same way but never shown."""

    name: str
    description: str
    input_schema: dict[str, Any]
    targets: tuple[str, ...]
    translate: Translate
    aliases: tuple[str, ...] = ()
    #: "primary" — offered only when targets[0] is armed; "any" — offered when
    #: any target is armed (a tool with a fallback, like exec_command).
    offered_when: str = "primary"
    #: The targets this tool can REALLY route to for an armed set, when that
    #: is narrower than all of ``targets`` (exec_command reaches the shell
    #: only when process_start is not armed). ``None`` = every target.
    reaches: "Callable[[frozenset], frozenset] | None" = None
    #: A real tool whose own description is appended as "How commands run
    #: here" — the shell's runtime facts (cmd.exe on Windows, no POSIX
    #: tools…) belong to every dialect's command tool, not only to Jarvis's.
    runtime_from: str = ""
    #: The shell language this command tool's model WRITES (v1.386.0):
    #: ``"bash"`` for Claude Code's ``Bash`` — Claude was trained on bash, and
    #: Claude Code runs it in Git for Windows' bash on Windows — so its calls
    #: carry it on their context (``mode.HarnessTurn.ctx_for`` →
    #: ``ToolContext.shell_syntax``) and run in bash there
    #: (``sandbox/git_bash``). "" = the platform's own shell (Codex's
    #: ``exec_command`` and Qwen Code's ``run_shell_command`` keep cmd.exe).
    shell_syntax: str = ""

    def reach(self, armed: Iterable[str]) -> set[str]:
        if self.reaches is None:
            return set(self.targets)
        try:
            return set(self.reaches(frozenset(armed)))
        except Exception:  # noqa: BLE001
            return set(self.targets)

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def is_offered(self, armed: Iterable[str]) -> bool:
        have = set(armed)
        if self.offered_when == "any":
            return any(t in have for t in self.targets)
        return bool(self.targets) and self.targets[0] in have


@dataclass(frozen=True)
class Dialect:
    id: str
    label: str
    #: The vendor's description style the tools are written in.
    description_style: str
    tools: tuple[DialectTool, ...]
    #: Real tool names NEVER offered under their own name in this dialect: a
    #: dialect tool covers them (and a call by the real name still passes
    #: through to the registry, which decides).
    hides: frozenset[str]
    #: The short system-prompt section ({tools} is the offered names).
    prompt: str
    native_calls: bool = True
    source: str = ""
    by_name: dict[str, DialectTool] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, DialectTool] = {}
        for tool in self.tools:
            index[tool.name] = tool
            for alias in tool.aliases:
                index.setdefault(alias, tool)
        object.__setattr__(self, "by_name", index)

    def lookup(self, name: str) -> DialectTool | None:
        """The dialect tool a model's call names: exact name or alias, then the
        same ignoring case (a weak model writing ``bash`` for ``Bash``)."""
        tool = self.by_name.get(name)
        if tool is not None:
            return tool
        low = str(name or "").lower()
        for key, candidate in self.by_name.items():
            if key.lower() == low:
                return candidate
        return None


# --------------------------------------------------------------------------- #
# argument helpers (pure, never raise)
# --------------------------------------------------------------------------- #


def _first(args: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in args and args[key] is not None:
            return args[key]
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _put(out: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        out[key] = value


def _path_of(args: dict[str, Any], *keys: str) -> Any:
    return _first(args, *keys, "path")


def _is_image(path: Any) -> bool:
    try:
        return PurePath(str(path)).suffix.lower() in IMAGE_SUFFIXES
    except (TypeError, ValueError):
        return False


def _ms_to_s(value: Any, *, cap_s: int = 600) -> int | None:
    ms = _int(value)
    if ms is None or ms <= 0:
        return None
    return max(1, min(cap_s, math.ceil(ms / 1000)))


def _clamp_ms(value: Any, default: int, low: int, high: int) -> int:
    ms = _int(value)
    if ms is None:
        ms = default
    return max(low, min(high, ms))


def _process_id(value: Any) -> Any:
    """A process handle from a model: ``p3`` stays ``p3``; a bare number (Codex
    session ids are numbers) becomes ``p<n>``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"p{int(value)}"
    text = str(value or "").strip()
    return f"p{text}" if text.isdigit() else (text or value)


def _plan_items(items: Any, text_key: str) -> Any:
    if not isinstance(items, list):
        return items
    out = []
    for item in items:
        if isinstance(item, dict):
            step = _first(item, text_key, "step", "content", "subject")
            out.append({"step": step if step is not None else "", "status": item.get("status", "pending")})
        else:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# translations — each returns (real tool, real args)
# --------------------------------------------------------------------------- #


def _read(numbered: bool, zero_based: bool) -> Translate:
    def go(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
        path = _path_of(args, "file_path", "absolute_path")
        if _is_image(path) and "view_image" in armed:
            return "view_image", {"path": path}
        out: dict[str, Any] = {}
        _put(out, "path", path)
        offset = _int(args.get("offset"))
        if offset is not None:
            out["offset"] = max(1, offset + 1 if zero_based else offset)
        _put(out, "limit", _int(args.get("limit")))
        if numbered:
            out["numbered"] = True
        return "read_file", out

    return go


def _write(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", _path_of(args, "file_path", "absolute_path"))
    _put(out, "content", args.get("content"))
    return "write_file", out


def _edit(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", _path_of(args, "file_path"))
    _put(out, "old", _first(args, "old_string", "old"))
    _put(out, "new", _first(args, "new_string", "new"))
    _put(out, "replace_all", _bool(args.get("replace_all")))
    return "edit_file", out


def _multi_edit(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", _path_of(args, "file_path"))
    edits = args.get("edits")
    if isinstance(edits, list):
        mapped = []
        for e in edits:
            if isinstance(e, dict):
                item: dict[str, Any] = {}
                _put(item, "old", _first(e, "old_string", "old"))
                _put(item, "new", _first(e, "new_string", "new"))
                _put(item, "replace_all", _bool(e.get("replace_all")))
                mapped.append(item)
            else:
                mapped.append(e)
        edits = mapped
    _put(out, "edits", edits)
    return "multi_edit", out


def _glob(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "pattern", args.get("pattern"))
    _put(out, "path", _first(args, "path", "dir_path"))
    return "glob", out


def _claude_grep(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    for key in ("pattern", "path", "glob", "type"):
        _put(out, key, args.get(key))
    # Claude Code's default mode is files_with_matches (Jarvis's is content).
    mode = args.get("output_mode")
    out["output_mode"] = mode if mode in ("content", "files_with_matches", "count") else "files_with_matches"
    _put(out, "after_context", _int(args.get("-A")))
    _put(out, "before_context", _int(args.get("-B")))
    _put(out, "context", _int(_first(args, "-C", "context")))
    _put(out, "line_numbers", _bool(args.get("-n")))
    _put(out, "case_insensitive", _bool(args.get("-i")))
    _put(out, "head_limit", _int(args.get("head_limit")))
    _put(out, "offset", _int(args.get("offset")))
    _put(out, "multiline", _bool(args.get("multiline")))
    return "grep", out


def _qwen_grep(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {"output_mode": "content", "case_insensitive": True}
    for key in ("pattern", "path", "glob"):
        _put(out, key, args.get(key))
    _put(out, "head_limit", _int(_first(args, "limit", "head_limit")))
    return "grep", out


def _list(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", _first(args, "path", "dir_path"))
    return "list_files", out


def _shell_or_background(background_key: str, cwd_key: str) -> Translate:
    def go(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
        command = _first(args, "command", "cmd")
        cwd = args.get(cwd_key)
        if _bool(args.get(background_key)):
            out: dict[str, Any] = {}
            _put(out, "command", command)
            _put(out, "cwd", cwd)
            return "process_start", out
        out = {}
        _put(out, "command", command)
        _put(out, "timeout_s", _ms_to_s(args.get("timeout")))
        _put(out, "cwd", cwd)
        return "shell", out

    return go


def _process_read(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "id", _process_id(_first(args, "bash_id", "shell_id", "task_id", "id")))
    return "process_read", out


def _process_stop(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "id", _process_id(_first(args, "task_id", "shell_id", "bash_id", "id")))
    return "process_stop", out


def _todo(text_key: str) -> Translate:
    def go(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
        out: dict[str, Any] = {}
        _put(out, "plan", _plan_items(_first(args, "todos", "plan"), text_key))
        _put(out, "explanation", args.get("explanation"))
        return "plan_update", out

    return go


def _notebook(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", _first(args, "notebook_path", "path"))
    for key in ("cell_id", "new_source", "cell_type", "edit_mode", "cell_index"):
        _put(out, key, args.get(key))
    return "notebook_edit", out


def _web_fetch(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    # Claude Code / Qwen Code run `prompt` over the page with a small model;
    # Jarvis returns the page's text and the model reads it itself.
    out: dict[str, Any] = {}
    _put(out, "url", args.get("url"))
    return "web_fetch", out


def _web_search(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    query = str(args.get("query") or "").strip()
    allowed = [str(d).strip() for d in (args.get("allowed_domains") or []) if str(d).strip()]
    blocked = [str(d).strip() for d in (args.get("blocked_domains") or []) if str(d).strip()]
    if allowed:
        query += " (" + " OR ".join(f"site:{d}" for d in allowed) + ")"
    if blocked:
        query += "".join(f" -site:{d}" for d in blocked)
    return "web_search", {"query": query} if query else {}


def _mcp_list(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "server", _first(args, "server", "server_name"))
    return "mcp_list_resources", out


def _mcp_read(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "server", _first(args, "server", "server_name"))
    _put(out, "uri", args.get("uri"))
    return "mcp_read_resource", out


def _exec_command(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    command = _first(args, "cmd", "command")
    workdir = _first(args, "workdir", "cwd")
    if "process_start" in armed or "shell" not in armed:
        out: dict[str, Any] = {}
        _put(out, "command", command)
        _put(out, "cwd", workdir)
        out["wait_ms"] = _clamp_ms(args.get("yield_time_ms"), 10_000, 250, 30_000)
        out["until_exit"] = True
        return "process_start", out
    out = {}
    _put(out, "command", command)
    _put(out, "cwd", workdir)
    return "shell", out


def _write_stdin(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    handle = _process_id(_first(args, "session_id", "id"))
    chars = args.get("chars")
    if chars == "\u0003":  # Ctrl+C: Codex models interrupt a session this way
        return "process_stop", {"id": handle}
    if isinstance(chars, str) and chars:
        return "process_write", {
            "id": handle, "input": chars,
            "wait_ms": _clamp_ms(args.get("yield_time_ms"), 250, 0, 30_000),
        }
    return "process_read", {"id": handle, "wait_ms": _clamp_ms(args.get("yield_time_ms"), 5_000, 0, 30_000)}


def _apply_patch(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "patch", _first(args, "input", "patch"))
    return "apply_patch", out


def _update_plan(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "plan", _plan_items(args.get("plan"), "step"))
    _put(out, "explanation", args.get("explanation"))
    return "plan_update", out


def _view_image(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "path", args.get("path"))
    return "view_image", out


def _questions(args: dict[str, Any]) -> Any:
    """A vendor's question list in ``ask_user``'s shape (v1.367.0): Claude
    Code's ``multiSelect`` → ``multi``; Codex's ``id`` / ``header`` kept;
    Codex's async ``title`` → ``question`` (options may be plain strings)."""
    raw = args.get("questions")
    if not isinstance(raw, list):
        return raw
    out = []
    for q in raw:
        if not isinstance(q, dict):
            out.append(q)
            continue
        item: dict[str, Any] = {}
        _put(item, "id", q.get("id"))
        _put(item, "header", q.get("header"))
        _put(item, "question", _first(q, "question", "title"))
        _put(item, "options", q.get("options"))
        _put(item, "multi", _bool(_first(q, "multiSelect", "multi_select", "multi")))
        out.append(item)
    return out


def _ask_user(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    if "questions" in args:
        _put(out, "questions", _questions(args))
    else:
        for key in ("question", "options", "multi"):
            _put(out, key, args.get(key))
    return "ask_user", out


def _exit_plan(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {}
    _put(out, "plan", args.get("plan"))
    return "exit_plan_mode", out


# --------------------------------------------------------------------------- #
# v1.372.0 (IJ harness part 2, Phase 1): helper agents and skills
# --------------------------------------------------------------------------- #

#: A vendor's helper type → the Jarvis helper that does that kind of work.
#: Claude Code's built-in subagents (``general-purpose``, ``Explore``,
#: ``Plan``) and Codex's roles (``default``, ``worker``, ``explorer``).
#: ``Plan`` maps to the researcher, never Jarvis's planner: the planner hands
#: work out itself, and a helper that can start helpers is refused by both
#: doors (the anti-fork-bomb rule). A name not in the table is passed on as
#: written — a builtin type or a custom agent the roster knows; anything else
#: the door resolves to the builder, as it always has.
HELPER_TYPES: dict[str, str] = {
    "": "builder",
    "general-purpose": "builder",
    "general": "builder",
    "default": "builder",
    "worker": "builder",
    "explore": "researcher",
    "explorer": "researcher",
    "research": "researcher",
    "plan": "researcher",
    "review": "reviewer",
    "code-reviewer": "reviewer",
}


def _helper_type(value: Any) -> str:
    raw = " ".join(str(value or "").split())
    return HELPER_TYPES.get(raw.lower(), raw)


def _helper_task(*parts: Any) -> str:
    """The helper's instruction: the vendor's short label (Claude Code's
    ``description``) as a heading over the prompt, when both are given."""
    texts = [str(p).strip() for p in parts if isinstance(p, str) and str(p).strip()]
    if len(texts) >= 2 and texts[0] not in texts[1]:
        return f"{texts[0]}\n\n{texts[1]}"
    return texts[-1] if texts else ""


def _claude_agent(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    """Claude Code's ``Agent`` / ``Task`` → ``delegate`` (it waits for the
    helper's report); ``run_in_background: true`` → ``spawn_agent`` when that
    is armed (it waits too: a helper's report always comes back in the same
    call here). ``model`` / ``effort`` are not used: a helper runs on the
    session's own model."""
    kind = _helper_type(args.get("subagent_type"))
    task = _helper_task(args.get("description"), _first(args, "prompt", "task"))
    if _bool(args.get("run_in_background")) and "spawn_agent" in armed:
        # spawn_agent knows a custom agent by its bare name ("scout"), never
        # the roster's "custom:scout" (review: that read "unknown agent").
        if kind.lower().startswith("custom:"):
            kind = kind.split(":", 1)[1].strip() or kind
        out: dict[str, Any] = {"agent": kind}
        _put(out, "task", task or None)
        return "spawn_agent", out
    out = {"agent_type": kind}
    _put(out, "task", task or None)
    return "delegate", out


def _codex_items_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    texts = []
    for item in items:
        if isinstance(item, dict) and str(item.get("type") or "text") == "text" and item.get("text"):
            texts.append(str(item["text"]))
    return "\n".join(texts)


def _codex_spawn(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    """Codex's ``spawn_agent`` (v1 ``message`` / ``items``, v2 ``task_name``
    + ``message``) → ``delegate``: the helper's report comes back in the same
    call, so there is nothing to wait for afterwards. ``model`` /
    ``reasoning_effort`` / ``fork_context`` are not used here."""
    kind = _helper_type(args.get("agent_type"))
    message = _first(args, "message", "task") or _codex_items_text(args.get("items"))
    task = _helper_task(args.get("task_name"), message)
    out: dict[str, Any] = {"agent_type": kind}
    _put(out, "task", task or None)
    return "delegate", out


def _skill(args: dict[str, Any], armed: frozenset) -> tuple[str, dict[str, Any]]:
    """Claude Code's ``Skill`` → ``skill_load`` (a skill from the user's
    library). A skill of THIS folder (``.ironjarvis/harness/skills``) is read
    from its SKILL.md instead — :meth:`mode.HarnessTurn.translate_calls`
    knows the folder's skills; this table does not."""
    out: dict[str, Any] = {}
    _put(out, "name", _first(args, "skill", "name"))
    return "skill_load", out


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_N = {"type": "number"}


def _d(text: str, base: dict[str, Any]) -> dict[str, Any]:
    return {**base, "description": text}


_STATUS = {"type": "string", "enum": ["pending", "in_progress", "completed"]}

# --------------------------------------------------------------------------- #
# CLAUDE — Claude Code 2.1.306's shapes
# --------------------------------------------------------------------------- #

_CLAUDE_TOOLS: tuple[DialectTool, ...] = (
    DialectTool(
        "Read",
        "Reads a file from the session folder. file_path may be absolute or relative to the "
        "folder. By default it reads up to 2000 lines from the start; pass offset (the line to "
        "start at, 1 = first) and limit for a range of a long file. Lines come back cat -n style, "
        "numbered from 1. PDFs and office documents come back as their text; an image (PNG, JPG, "
        "GIF, WebP) is described by a vision model. Read a file before you Edit it.",
        _obj({
            "file_path": _d("The path to the file to read", _S),
            "offset": _d("The line number to start reading from (1 = the first line)", _I),
            "limit": _d("The number of lines to read", _I),
        }, ["file_path"]),
        ("read_file", "view_image"), _read(numbered=True, zero_based=False),
    ),
    DialectTool(
        "Write",
        "Writes a file in the session folder, creating it or replacing all of its content. "
        "Prefer Edit to change part of an existing file.",
        _obj({
            "file_path": _d("The path to the file to write", _S),
            "content": _d("The content to write to the file", _S),
        }, ["file_path", "content"]),
        ("write_file",), _write,
    ),
    DialectTool(
        "Edit",
        "Performs an exact string replacement in a file. old_string must match the file exactly "
        "once, indentation included: the edit is refused when the text is missing or appears more "
        "than once (add surrounding lines to make it unique, or set replace_all). Line endings "
        "are kept.",
        _obj({
            "file_path": _d("The path to the file to modify", _S),
            "old_string": _d("The text to replace", _S),
            "new_string": _d("The text to replace it with (must be different from old_string)", _S),
            "replace_all": _d("Replace all occurrences of old_string (default false)", _B),
        }, ["file_path", "old_string", "new_string"]),
        ("edit_file",), _edit,
    ),
    DialectTool(
        "MultiEdit",
        "Makes several exact edits to ONE file in a single call. Each edit is applied to the "
        "result of the ones before it, with Edit's rule (old_string must match exactly once unless "
        "replace_all). All or nothing: if any edit fails, the file is not changed.",
        _obj({
            "file_path": _d("The path to the file to modify", _S),
            "edits": {
                "type": "array",
                "description": "Edits to apply in order",
                "items": _obj({
                    "old_string": _S, "new_string": _S, "replace_all": _B,
                }, ["old_string", "new_string"]),
            },
        }, ["file_path", "edits"]),
        ("multi_edit",), _multi_edit,
    ),
    DialectTool(
        "Glob",
        "Fast file pattern matching ('**/*.py', 'src/**/*.ts'). Returns matching paths, newest "
        "first, at most 100. Use it to find files by name instead of a shell command.",
        _obj({
            "pattern": _d("The glob pattern to match files against", _S),
            "path": _d("The folder to search in (default: the session folder)", _S),
        }, ["pattern"]),
        ("glob",), _glob,
    ),
    DialectTool(
        "Grep",
        "Searches file contents with a regular expression. output_mode: files_with_matches "
        "(default, paths), content (matching lines; -A/-B/-C context, -n line numbers) or count. "
        "Filter files with glob ('*.js') or type ('py'). -i ignores case; head_limit and offset "
        "page the results; multiline lets a pattern span lines. Use Grep, not grep or rg in Bash.",
        _obj({
            "pattern": _d("The regular expression pattern to search for in file contents", _S),
            "path": _d("File or folder to search in (default: the session folder)", _S),
            "glob": _d('Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}")', _S),
            "type": _d("File type to search (e.g. js, py, rust, go, java)", _S),
            "output_mode": {
                "type": "string", "enum": ["content", "files_with_matches", "count"],
                "description": 'Defaults to "files_with_matches".',
            },
            "-A": _d("Lines after each match (content mode)", _N),
            "-B": _d("Lines before each match (content mode)", _N),
            "-C": _d("Lines before and after each match (content mode)", _N),
            "-n": _d("Show line numbers (content mode). Defaults to true.", _B),
            "-i": _d("Case insensitive search", _B),
            "head_limit": _d("Show at most this many lines/entries (default 250)", _N),
            "offset": _d("Skip this many lines/entries first", _N),
            "multiline": _d("Let the pattern span lines", _B),
        }, ["pattern"]),
        ("grep",), _claude_grep,
    ),
    DialectTool(
        "LS",
        "Lists the files in a folder of the session (default: the session folder itself).",
        _obj({"path": _d("The folder to list", _S)}, []),
        ("list_files",), _list,
    ),
    DialectTool(
        "Bash",
        "Runs a command in the session folder and returns its output. Every call starts in the "
        "session folder again: a cd lasts for that command only ('cd sub && pytest'). timeout is in "
        "milliseconds (max 600000). For a program that keeps running (a dev server, a watcher) "
        "set run_in_background: true; read its output with BashOutput and stop it with TaskStop.",
        _obj({
            "command": _d("The command to execute", _S),
            "timeout": _d("Optional timeout in milliseconds (max 600000)", _N),
            "description": _d("What this command does, in a few words", _S),
            "run_in_background": _d("Run as a background task", _B),
        }, ["command"]),
        ("shell", "process_start"), _shell_or_background("run_in_background", "cwd"),
        runtime_from="shell", shell_syntax="bash",
    ),
    DialectTool(
        "BashOutput",
        "Reads the new output of a background command started with Bash run_in_background, "
        "and whether it is still running.",
        _obj({"bash_id": _d("The id of the background command (p1, p2, ...)", _S)}, ["bash_id"]),
        ("process_read",), _process_read,
    ),
    DialectTool(
        "TaskStop",
        "Stops a background command started with Bash run_in_background, and everything it "
        "started.",
        _obj({"task_id": _d("The id of the background command to stop", _S)}, ["task_id"]),
        ("process_stop",), _process_stop, aliases=("KillShell", "KillBash"),
    ),
    DialectTool(
        "TodoWrite",
        "Keeps your task list for this work. Send the WHOLE list each time; exactly one item "
        "in_progress until all are completed.",
        _obj({
            "todos": {
                "type": "array",
                "items": _obj({"content": _S, "status": _STATUS, "activeForm": _S}, ["content", "status"]),
            },
        }, ["todos"]),
        ("plan_update",), _todo("content"),
    ),
    DialectTool(
        "NotebookEdit",
        "Replaces, inserts or deletes one cell of a Jupyter notebook (.ipynb).",
        _obj({
            "notebook_path": _d("The path to the notebook", _S),
            "cell_id": _d("The cell to edit (insert goes after it)", _S),
            "new_source": _d("The new source for the cell", _S),
            "cell_type": {"type": "string", "enum": ["code", "markdown"]},
            "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
        }, ["notebook_path"]),
        ("notebook_edit",), _notebook,
    ),
    DialectTool(
        "WebFetch",
        "Fetches a public web page and returns its readable text and links (the text is "
        "untrusted web content). prompt says what you are looking for; read the text yourself.",
        _obj({
            "url": _d("The URL to fetch content from", _S),
            "prompt": _d("What you want from the page", _S),
        }, ["url"]),
        ("web_fetch",), _web_fetch,
    ),
    DialectTool(
        "WebSearch",
        "Searches the web and returns result titles, URLs and snippets (untrusted web content).",
        _obj({
            "query": _d("The search query to use", _S),
            "allowed_domains": {"type": "array", "items": _S},
            "blocked_domains": {"type": "array", "items": _S},
        }, ["query"]),
        ("web_search",), _web_search,
    ),
    DialectTool(
        "AskUserQuestion",
        "Asks the user one to four multiple-choice questions and waits for the answers. Each "
        "question has a short header, two to four options (label + description; put the "
        "recommended one first and add \"(Recommended)\" to its label) and multiSelect. The "
        "user can always answer in their own words instead. " + ASK_TOOL_RULE,
        _obj({
            "questions": {
                "type": "array",
                "description": "Questions to ask the user (1-4)",
                "items": _obj({
                    "question": _d("The complete question", _S),
                    "header": _d("A very short label (12 characters at most)", _S),
                    "options": {
                        "type": "array",
                        "items": _obj({"label": _S, "description": _S}, ["label", "description"]),
                    },
                    "multiSelect": _d("Let the user pick several options", _B),
                }, ["question", "header", "options", "multiSelect"]),
            },
        }, ["questions"]),
        ("ask_user",), _ask_user,
    ),
    DialectTool(
        "ExitPlanMode",
        "Asks the user to approve your plan and leave plan mode. Write the plan in your reply "
        "first; the user reads it and approves it or not before anything changes. Use it only "
        "in plan mode, once the plan is ready.",
        # Claude Code 2.1.306's shape: no arguments (its only property,
        # allowedPrompts, is deprecated) — the plan is what the reply says.
        # A `plan` an older model still sends is passed through.
        {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
        ("exit_plan_mode",), _exit_plan,
    ),
    DialectTool(
        "ListMcpResourcesTool",
        "Lists the resources the user's connected apps offer.",
        _obj({"server": _d("Only this app", _S)}, []),
        ("mcp_list_resources",), _mcp_list,
    ),
    DialectTool(
        "ReadMcpResourceTool",
        "Reads one resource a connected app offers.",
        _obj({"server": _S, "uri": _S}, ["server", "uri"]),
        ("mcp_read_resource",), _mcp_read,
    ),
    # v1.372.0 (IJ harness part 2, Phase 1): helper agents and skills.
    DialectTool(
        "Agent",
        "Starts a helper agent for a self-contained part of the work: it works in this session's "
        "folder, on this session's model and account, with its own context, and returns its "
        "final report in this call. Several Agent calls in ONE message run at the same time (at "
        "most 3 at once; more wait for a free slot) — use that to search different parts of the "
        "code in parallel. Give each a complete prompt: it cannot see this conversation. "
        "subagent_type: general-purpose (can change files, default), Explore (reads and "
        "searches), or a name from list_agents — a member of the user's model team (listed "
        "under 'Your model team') works on its OWN model instead. To let Iron Jarvis pick the "
        "team member, give subagent_type role:read, role:code, role:review or role:test (a "
        "local member first when its record for that role is good). model and effort are not "
        "used here.",
        _obj({
            "description": _d("A short (3-5 word) description of the task", _S),
            "prompt": _d("The task for the agent to perform", _S),
            "subagent_type": _d("The type of specialized agent to use for this task", _S),
            "model": {"type": "string", "description": "Not used: helpers run on this session's model"},
            "run_in_background": _d("Accepted; the report still comes back in this call", _B),
        }, ["description", "prompt"]),
        ("delegate", "spawn_agent"), _claude_agent, aliases=("Task",),
        reaches=lambda armed: frozenset(t for t in ("delegate", "spawn_agent") if t in armed),
    ),
    DialectTool(
        "Skill",
        "Loads a skill — a playbook for a kind of task — by name and returns its instructions. "
        "Use it when a skill fits the task: the skills listed for this folder, or one "
        "skill_search finds in the user's skill library. Do not guess names.",
        _obj({
            "skill": _d("The name of a skill (from this folder's list or skill_search)", _S),
            "args": _d("Optional arguments for the skill", _S),
        }, ["skill"]),
        ("skill_load",), _skill,
    ),
)

CLAUDE = Dialect(
    id="claude",
    label="Claude Code",
    description_style="claude-code",
    tools=_CLAUDE_TOOLS,
    hides=frozenset({
        "read_file", "write_file", "edit_file", "multi_edit", "glob", "grep", "list_files",
        "shell", "process_start", "process_read", "process_stop", "plan_update",
        "notebook_edit", "web_fetch", "web_search", "view_image", "mcp_list_resources",
        "mcp_read_resource", "ask_user", "exit_plan_mode",
        # v1.372.0: Agent / Task and Skill cover these.
        "delegate", "spawn_agent", "skill_load",
    }),
    prompt=(
        "Your tools use Claude Code's names and shapes: {tools}. Read a file before you Edit "
        "it, and copy old_string exactly; use Glob and Grep to find code rather than shell "
        "commands; on multi-step work keep TodoWrite current."
    ),
    source="Claude Code 2.1.306 (tests/fixtures/harness_tool_inventory_v1364.json)",
)

# --------------------------------------------------------------------------- #
# GPT — Codex 0.157.0's shapes
# --------------------------------------------------------------------------- #

_APPLY_PATCH_TEXT = (
    "Edits files with a patch. input is the whole patch:\n"
    "*** Begin Patch\n"
    "*** Update File: path/to/file.py\n"
    "@@ a line near the change\n"
    " unchanged context line\n"
    "-removed line\n"
    "+added line\n"
    "*** End Patch\n"
    "Other hunks: '*** Add File: <path>' followed by '+' lines; '*** Delete File: <path>'; "
    "'*** Move to: <new path>' after an Update header. Paths are relative to the session "
    "folder. All or nothing: if one hunk does not fit, nothing is written."
)

_GPT_TOOLS: tuple[DialectTool, ...] = (
    DialectTool(
        "exec_command",
        "Runs a command in the session folder. It returns the output and exit code when the "
        "command ends within yield_time_ms (default 10000, at most 30000); a command still "
        "running then returns a session id (p1, p2, ...) — poll it or type into it with "
        "write_stdin.",
        _obj({
            "cmd": _d("Shell command to execute.", _S),
            "workdir": _d("Working directory, inside the session folder (default: the folder).", _S),
            "yield_time_ms": _d("How long to wait before returning (default 10000).", _N),
            "max_output_tokens": _d("Output token budget (advisory).", _N),
        }, ["cmd"]),
        ("process_start", "shell"), _exec_command, offered_when="any", runtime_from="shell",
        reaches=lambda armed: (
            frozenset({"process_start"}) if ("process_start" in armed or "shell" not in armed)
            else frozenset({"shell"})
        ),
    ),
    DialectTool(
        "write_stdin",
        "Writes characters to a running exec_command session and returns its recent output; "
        "empty chars just polls; chars \"\\u0003\" (Ctrl+C) stops it.",
        _obj({
            "session_id": _d("The session id exec_command returned (p1, or 1).", _S),
            "chars": _d("Characters to write; empty polls without writing.", _S),
            "yield_time_ms": _d("How long to wait for output.", _N),
        }, ["session_id"]),
        ("process_read", "process_write", "process_stop"), _write_stdin,
    ),
    DialectTool(
        "apply_patch", _APPLY_PATCH_TEXT,
        _obj({"input": _d("The entire patch, from *** Begin Patch to *** End Patch.", _S)}, ["input"]),
        ("apply_patch",), _apply_patch,
    ),
    DialectTool(
        "update_plan",
        "Updates the task plan: a list of steps, each with a status (pending, in_progress, "
        "completed); at most one in_progress at a time.",
        _obj({
            "explanation": _S,
            "plan": {"type": "array", "items": _obj({"step": _S, "status": _STATUS}, ["step", "status"])},
        }, ["plan"]),
        ("plan_update",), _update_plan,
    ),
    DialectTool(
        "view_image",
        "Looks at a local image file with a vision model and describes it.",
        _obj({"path": _d("Local filesystem path to an image file.", _S)}, ["path"]),
        ("view_image",), _view_image,
    ),
    DialectTool(
        "request_user_input",
        "Request user input for one to three short questions and wait for the response. Each "
        "question has an id, a short header, the question and 2-3 options (label + "
        "description; the recommended one first with \"(Recommended)\" in its label). The "
        "user can always answer in their own words instead. " + ASK_TOOL_RULE,
        _obj({
            "questions": {
                "type": "array",
                "description": "Questions to show the user. Prefer 1 and do not exceed 3",
                "items": _obj({
                    "id": _d("Stable identifier for mapping answers (snake_case).", _S),
                    "header": _d("Short header label shown in the UI (12 or fewer chars).", _S),
                    "question": _d("Single-sentence prompt shown to the user.", _S),
                    "options": {
                        "type": "array",
                        "items": _obj({"label": _S, "description": _S}, ["label", "description"]),
                    },
                }, ["id", "header", "question", "options"]),
            },
        }, ["questions"]),
        ("ask_user",), _ask_user, aliases=("request_user_input_async",),
    ),
    DialectTool(
        "list_mcp_resources",
        "Lists resources provided by the user's connected apps (MCP servers).",
        _obj({"server": _d("Only this server.", _S)}, []),
        ("mcp_list_resources",), _mcp_list,
    ),
    DialectTool(
        "read_mcp_resource",
        "Reads a specific resource from a connected app given the server name and URI.",
        _obj({"server": _S, "uri": _S}, ["server", "uri"]),
        ("mcp_read_resource",), _mcp_read,
    ),
    # v1.372.0 (IJ harness part 2, Phase 1): Codex's multi-agent spawn.
    DialectTool(
        "spawn_agent",
        "Spawn a sub-agent for a well-scoped task. It works in this session's folder, on this "
        "session's model and account, and this call returns its final report when it is done — "
        "there is nothing to wait for or close afterwards. Several spawn_agent calls in one "
        "message run at the same time (at most 3 at once; more wait for a free slot). The "
        "sub-agent cannot see this conversation: put everything it needs in message. "
        "agent_type: default or worker (can change files), explorer (reads and searches), or a "
        "name from list_agents — a member of the user's model team (listed under 'Your model "
        "team') works on its OWN model instead. To let Iron Jarvis pick the team member, give "
        "agent_type role:read, role:code, role:review or role:test.",
        # Codex 0.157.0's multi_agent_v1.spawn_agent arguments that mean
        # something here (v2's task_name is still accepted by the translation).
        _obj({
            "message": _d("Initial plain-text task for the new agent.", _S),
            "agent_type": _d("Agent type for the new agent (default, worker, explorer).", _S),
        }, ["message"]),
        ("delegate",), _codex_spawn,
    ),
)

GPT = Dialect(
    id="gpt",
    label="Codex",
    description_style="codex",
    tools=_GPT_TOOLS,
    hides=frozenset({
        "shell", "process_start", "process_read", "process_write", "process_stop",
        "write_file", "edit_file", "multi_edit", "apply_patch", "plan_update", "view_image",
        "mcp_list_resources", "mcp_read_resource", "ask_user",
        # v1.372.0: Codex's spawn_agent covers delegate (and takes the real
        # spawn_agent's name, so the real tool is not shown under it).
        "delegate", "spawn_agent",
    }),
    prompt=(
        "Your tools use Codex's names and shapes: {tools}. Edit files with apply_patch. Read and "
        "search the folder with read_file, glob and grep rather than shell commands. "
        "exec_command returns a session id for a command that is still running; poll it, type "
        "into it or stop it with write_stdin."
    ),
    source="Codex 0.157.0 (tests/fixtures/harness_tool_inventory_v1364.json)",
)

# --------------------------------------------------------------------------- #
# QWEN — Qwen Code's shapes (QwenLM/qwen-code @ 13a4fea5, 2026-10-21)
# --------------------------------------------------------------------------- #

_QWEN_TOOLS: tuple[DialectTool, ...] = (
    DialectTool(
        "read_file",
        "Reads a file from the session folder. file_path may be absolute or relative to the "
        "folder. For a long text file pass offset (the 0-based line to start at) and limit. "
        "PDFs and office documents come back as their text; an image is described by a vision "
        "model.",
        _obj({
            "file_path": _d("The path to the file to read.", _S),
            "offset": _d("Optional: the 0-based line number to start reading from.", _I),
            "limit": _d("Optional: maximum number of lines to read.", _I),
        }, ["file_path"]),
        ("read_file", "view_image"), _read(numbered=False, zero_based=True),
    ),
    DialectTool(
        "write_file",
        "Writes content to a file in the session folder, creating it or replacing it.",
        _obj({"file_path": _S, "content": _S}, ["file_path", "content"]),
        ("write_file",), _write,
    ),
    DialectTool(
        "edit",
        "Replaces text within a file. old_string must match exactly once (whitespace and "
        "indentation included) unless replace_all is true.",
        _obj({
            "file_path": _S, "old_string": _S, "new_string": _S,
            "replace_all": _d("Replace every occurrence of old_string.", _B),
        }, ["file_path", "old_string", "new_string"]),
        ("edit_file",), _edit,
    ),
    DialectTool(
        "glob",
        "Finds files matching a glob pattern ('**/*.py'), newest first.",
        _obj({"pattern": _S, "path": _d("The folder to search in (optional).", _S)}, ["pattern"]),
        ("glob",), _glob,
    ),
    DialectTool(
        "grep_search",
        "Searches file contents for a regular expression (case-insensitive). Filter files with "
        "glob ('*.js'); limit caps the matching lines returned.",
        _obj({
            "pattern": _S,
            "glob": _d('Glob pattern to filter files (e.g. "*.js").', _S),
            "path": _d("File or folder to search in (default: the session folder).", _S),
            "limit": _d("Limit output to the first N matching lines.", _I),
        }, ["pattern"]),
        ("grep",), _qwen_grep,
    ),
    DialectTool(
        "list_directory",
        "Lists the files in a folder of the session.",
        _obj({"path": _S}, ["path"]),
        ("list_files",), _list,
    ),
    DialectTool(
        "run_shell_command",
        "Runs a shell command in the session folder (or `directory` inside it). timeout is in "
        "milliseconds (max 600000). Set is_background: true for a program that keeps running (a "
        "dev server, a watcher); read its output with process_read and stop it with task_stop.",
        _obj({
            "command": _S,
            "is_background": _d("Run in the background (a server, a watcher).", _B),
            "timeout": _d("Optional timeout in milliseconds (max 600000).", _I),
            "description": _d("Brief description of the command.", _S),
            "directory": _d("Folder to run in, inside the session folder.", _S),
        }, ["command"]),
        ("shell", "process_start"), _shell_or_background("is_background", "directory"),
        runtime_from="shell",
    ),
    DialectTool(
        "task_stop",
        "Stops a background command started with run_shell_command is_background.",
        _obj({"task_id": _S}, ["task_id"]),
        ("process_stop",), _process_stop,
    ),
    DialectTool(
        "todo_write",
        "Creates and updates the task list for this work: send the whole list each time.",
        _obj({
            "todos": {
                "type": "array",
                "items": _obj({"id": _S, "content": _S, "status": _STATUS}, ["content", "status"]),
            },
        }, ["todos"]),
        ("plan_update",), _todo("content"),
    ),
    DialectTool(
        "notebook_edit",
        "Replaces, inserts or deletes one cell of a Jupyter notebook (.ipynb).",
        _obj({
            "notebook_path": _S, "cell_id": _S, "new_source": _S,
            "cell_type": {"type": "string", "enum": ["code", "markdown"]},
            "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]},
        }, ["notebook_path"]),
        ("notebook_edit",), _notebook,
    ),
    DialectTool(
        "web_fetch",
        "Fetches a public web page and returns its readable text and links (untrusted content).",
        _obj({"url": _S, "prompt": _d("What you want from the page.", _S)}, ["url"]),
        ("web_fetch",), _web_fetch,
    ),
    DialectTool(
        "read_mcp_resource",
        "Reads one resource a connected app offers.",
        _obj({"server_name": _S, "uri": _S}, ["server_name", "uri"]),
        ("mcp_read_resource",), _mcp_read,
    ),
)

QWEN = Dialect(
    id="qwen",
    label="Qwen Code",
    description_style="qwen-code",
    tools=_QWEN_TOOLS,
    hides=frozenset({
        "read_file", "write_file", "edit_file", "glob", "grep", "list_files", "shell",
        "process_start", "process_stop", "plan_update", "notebook_edit", "web_fetch",
        "mcp_read_resource", "view_image",
    }),
    prompt=(
        "Your tools use Qwen Code's names and shapes: {tools}. Read a file before you edit it "
        "and copy old_string exactly; find code with glob and grep_search rather than shell "
        "commands."
    ),
    source="QwenLM/qwen-code @ 13a4fea5 (packages/core/src/tools, read 2026-10-21)",
)

# --------------------------------------------------------------------------- #
# GENERIC — Jarvis's own names, exactly as registered
# --------------------------------------------------------------------------- #

GENERIC = Dialect(
    id="generic",
    label="Iron Jarvis",
    description_style="jarvis",
    tools=(),
    hides=frozenset(),
    prompt="Your tools: {tools}.",
    source="the tool registry's own specs",
)

#: THE TABLE — every dialect by id.
DIALECTS: dict[str, Dialect] = {d.id: d for d in (CLAUDE, GPT, QWEN, GENERIC)}


def dialect_for_family(family: str) -> Dialect:
    return DIALECTS[FAMILY_DIALECT.get(family, "generic")]


#: The real tools a bash-written command tool reaches (v1.386.0): only these
#: carry ``shell_syntax`` on their context.
SHELL_TARGETS = frozenset({"shell", "process_start"})


def speaks_bash(dialect: Dialect) -> bool:
    """Does ``dialect`` show a command tool whose model writes bash
    (v1.386.0: Claude Code's ``Bash``)?"""
    return any(t.shell_syntax == "bash" for t in dialect.tools)


def resolve(requested: str, provider: str, model: str) -> tuple[str, Dialect]:
    """``(family, dialect)`` for a turn. ``requested`` is ``"auto"`` (the
    family's own dialect) or a dialect id that is forced (``"generic"``
    reproduces Jarvis's own names on any model)."""
    family = family_for(provider, model)
    if requested and requested != "auto":
        return family, DIALECTS[requested]
    return family, dialect_for_family(family)


# --------------------------------------------------------------------------- #
# what the model is shown, and what its call becomes
# --------------------------------------------------------------------------- #


def offered_tools(dialect: Dialect, armed: Iterable[str]) -> tuple[list[DialectTool], list[str]]:
    """``(dialect tools offered, real tools offered under their own name)`` for
    an armed set. A dialect tool is offered when its target is armed; a real
    armed tool keeps its own name unless the dialect hides it or a dialect tool
    already uses that name."""
    have = list(dict.fromkeys(armed))
    dialect_tools = [t for t in dialect.tools if t.is_offered(have)]
    taken = {t.name for t in dialect_tools}
    passthrough = [n for n in have if n not in dialect.hides and n not in taken]
    return dialect_tools, passthrough


def reachable(
    dialect_tools: Iterable[DialectTool], passthrough: Iterable[str], armed: Iterable[str] = (),
) -> set[str]:
    """Every real tool what the model was shown can reach — the turn's armed
    set (the registry's ``allowed_names`` gate is exactly this). A real tool
    no shown tool reaches is not armed at all: the roster is the contract
    (v1.227.0), so a model calling a hidden name it was never shown meets the
    registry's "not armed" refusal."""
    have = list(armed)
    out = set(passthrough)
    for tool in dialect_tools:
        out.update(tool.reach(have))
    return out


@dataclass(frozen=True)
class Translated:
    """A model's call after the boundary: the REAL tool and arguments, and the
    name the model used (``called_as`` == ``real`` when nothing translated)."""

    real: str
    args: dict[str, Any]
    called_as: str

    @property
    def translated(self) -> bool:
        return self.real != self.called_as


def translate(dialect: Dialect, name: str, args: Any, armed: Iterable[str]) -> Translated:
    """Map one call. A name the dialect does not know passes through
    unchanged (the registry then decides — an unknown or unarmed name is
    refused there, ledgered). Never raises: a translation that fails passes
    the call through as written."""
    tool = dialect.lookup(name)
    raw = args if isinstance(args, dict) else {}
    if tool is None:
        return Translated(real=name, args=raw, called_as=name)
    try:
        real, real_args = tool.translate(dict(raw), frozenset(armed))
    except Exception:  # noqa: BLE001 — a bad argument must never crash the lane
        return Translated(real=name, args=raw, called_as=name)
    return Translated(real=real, args=real_args, called_as=name)
