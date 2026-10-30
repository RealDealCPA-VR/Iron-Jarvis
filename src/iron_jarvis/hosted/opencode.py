"""OpenCode in the Code view (v1.413.0): ``opencode acp`` — the Agent Client
Protocol (JSON-RPC 2.0, one JSON object per line on stdin/stdout) — with ALL
of OpenCode's own tools, agents, plugins, MCP servers, AGENTS.md and config
on. The same OpenCode the user runs in a terminal; Iron Jarvis only starts
it, draws what it does, relays messages and turns every permission question
OpenCode asks into a card.

WHY ACP AND NOT ``opencode serve``: both are OpenCode's own long-lived,
machine-readable ways in (``opencode --help``: "acp — start ACP (Agent Client
Protocol) server"; "serve — starts a headless opencode server"). ACP speaks
over the process's own stdin/stdout, so it fits the Code view's process
plumbing (``hosted.process``: one child, its tree killed on stop) and opens
NO listening port at all. OpenCode still runs its internal server inside
the process on a port it picks (``--port`` default 0 = any free port the
system hands out, on ``--hostname`` default 127.0.0.1); Iron Jarvis passes
neither, so it is never 8787 / 8788 / 8765 (those are taken, and a free
port the system hands out is an ephemeral one) and nothing of it is reached
from outside this PC.

MEASURED on this PC (opencode 1.18.18, live, in scratch folders; masked
recordings in ``tests/fixtures/build_app_v1413``):

* ``initialize`` {protocolVersion: 1, clientCapabilities: {fs: false,
  terminal: false}} answers ``agentCapabilities.loadSession: true``,
  ``promptCapabilities.image: true`` and ``authMethods`` ("Run `opencode auth
  login` in the terminal"). ``session/new`` {cwd, mcpServers: []} answers
  ``sessionId`` (``ses_…``) and ``configOptions``: ``model`` (every model
  OpenCode lists, ``provider/model``), ``effort`` (the CURRENT model's
  variants, e.g. low/high/max; absent for a model with none) and ``mode``
  (OpenCode's agents: ``build``, ``plan``). ``session/set_config_option``
  {sessionId, configId, value} changes one live and answers the new list.
  ``mcpServers: []`` adds none: the user's own MCP servers from their config
  still load.
* ``session/prompt`` {sessionId, prompt: [text | image{data, mimeType}]}
  answers only when the turn ends: ``{stopReason: end_turn | cancelled |
  max_tokens | max_turn_requests | refusal, usage: {inputTokens,
  outputTokens, cachedReadTokens, thoughtTokens}}``. Everything in between is
  ``session/update`` notifications: ``agent_message_chunk`` {messageId,
  content}, ``agent_thought_chunk``, ``tool_call`` / ``tool_call_update``
  {toolCallId, title, kind, status pending|in_progress|completed|failed,
  rawInput, rawOutput, content, locations}, ``plan`` {entries},
  ``usage_update`` {used, size, cost: {amount, currency}},
  ``available_commands_update``. A provider that fails is an ERROR answer to
  the prompt (``-32603 "Internal error: …"``).
* PERMISSIONS: OpenCode's own default runs every tool without asking. Its
  ``permission`` config (``"*"`` plus per-tool keys, each ``allow`` | ``ask``
  | ``deny``) is read from the environment variable ``OPENCODE_PERMISSION``
  (JSON) as well, MERGED OVER the user's config files: that is how each
  Code-view mode is applied, per process — the user's config is never
  written. An ``ask`` arrives as ``session/request_permission`` {toolCall:
  {title, kind, rawInput, content: [diff]}, options: [once allow_once,
  always allow_always, reject reject_once]}; the answer is ``{outcome:
  {outcome: "selected", optionId}}``. "always" covers the rest of that
  process (nothing was written to OpenCode's ``permission`` table).
* MERGE ORDER WIDENS: OpenCode reads its rules last-match-wins in key order,
  and a key merged in from the environment lands AFTER the config's keys. A
  project's ``"bash": "deny"`` with ``OPENCODE_PERMISSION={"*": "ask"}``
  ASKED (measured) — the deny was lost. So :func:`permission_env` never sets
  a key the user's own config sets, and uses ``"*"`` only when no rule of the
  user's is stricter than it.
* ``session/cancel`` (a notification) stops the turn: the running tool ends
  ``failed`` ("Tool execution aborted") and the prompt answers ``stopReason:
  "cancelled"``. The process stays up.
* A SECOND ``session/prompt`` while a turn runs JOINS that turn: OpenCode's
  loop reads it at its next step and both prompts answer together, with the
  same result, when the loop ends (measured: a note sent during a running
  ``ping`` changed the answer). That is the Code view's note while it works.
* ``session/load`` {sessionId, cwd, mcpServers} reopens a saved session
  after the process was stopped: it REPLAYS the whole conversation as
  ``session/update`` notifications BEFORE it answers (not drawn again here),
  and keeps the session's last model and effort.
* Pictures: an ``image`` block was read (a word drawn only in a PNG's pixels,
  opencode/glm-5.3-flash-free and mimo-v2.6-flash-free).
* Sessions live in OpenCode's own database
  (``<XDG_DATA_HOME or ~/.local/share>/opencode/opencode.db``, table
  ``session``: id, directory, title, time_updated, parent_id,
  time_archived); ``opencode --session <id>`` opens one in a terminal.
* Sign-in is OpenCode's own (``opencode auth login``, per provider): no
  Iron-Proxy account applies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from . import words as W
from .driver import Ask, AskRefused, ControlRefused, ControlWaiter, Driver, new_ask_id
from .events import (
    DETAIL_CHARS,
    DIFF_CHARS,
    INPUT_CHARS,
    OUTPUT_CHARS,
    TEXT_CHARS,
    clip,
    make,
    one_line,
    unified_new_file,
    unified_replace,
)

log = logging.getLogger(__name__)

HARNESS = "opencode"

#: The ACP version this driver speaks (opencode 1.18.18 answers 1).
PROTOCOL_VERSION = 1

#: preset -> (permission posture, OpenCode agent).
MODES: dict[str, tuple[str, str]] = {
    "ask": ("ask", "build"),
    "edits": ("edits", "build"),
    "plan": ("ask", "plan"),
    "full": ("full", "build"),
}
#: The presets OpenCode does not have, and why (said in the mode menu).
HIDDEN_MODES: dict[str, str] = {
    "auto": "OpenCode has no mode that decides each step for you — choose Ask first or Auto edits.",
}
#: What each preset does for OpenCode, in plain words (the mode menu says it).
MODE_ABOUT: dict[str, str] = {
    "ask": "Asks before it changes a file, runs a command or uses the web; reads without asking.",
    "edits": "Changes files without asking; asks before it runs a command or uses the web.",
    "plan": "OpenCode's plan agent: it looks and plans, and changes no file.",
    "full": "Runs everything without asking.",
}

#: The tools every posture lets run without a card (they only read).
READ_ONLY_TOOLS: tuple[str, ...] = ("read", "glob", "grep", "list", "lsp", "todoread", "todowrite", "skill")
#: OpenCode's tools that change or reach out, named one by one when ``"*"``
#: cannot be used (a rule of the user's is stricter than it).
ACTING_TOOLS: tuple[str, ...] = (
    "edit", "bash", "webfetch", "websearch", "codesearch", "task", "external_directory", "doom_loop",
)
_POSTURE_STAR = {"ask": "ask", "edits": "ask", "full": "allow"}
_RANK = {"allow": 0, "ask": 1, "deny": 2}

#: OpenCode's effort ("variant") names seen across models — a model's own
#: list (``configOptions.effort``) narrows it when known.
EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

#: An OpenCode session id as Iron Jarvis would ever type it into a terminal.
SESSION_ID_RX = re.compile(r"^ses_[A-Za-z0-9]{10,60}$")

REQUEST_TIMEOUT_S = 60.0
#: A running command's output is redrawn at most this often.
UPDATE_EVERY_S = 1.0
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 8
MAX_MODELS = 300

#: The models an OpenCode process last listed (``configOptions.model``) and
#: the effort levels each was seen to take. Kept on disk by the service.
LAST_MODELS: list[dict[str, Any]] = []

_SIGN_IN_RX = re.compile(r"auth(?:entication)? required|not logged in|opencode auth login|no api key|"
                         r"\b401\b|unauthori[sz]ed|invalid api key|authentication (?:failed|error)", re.I)
_LIMIT_RX = re.compile(r"usage limit|rate limit|\b429\b|quota|too many requests", re.I)
SIGN_IN_FIX = "Open a Build terminal and run opencode auth login, or choose another model."
LIMIT_FIX = "Wait until it resets, or choose another model."
OTHER_FIX = "Send the message again. If it happens again, choose another model for this session."


# --------------------------------------------------------------------------- #
# Where OpenCode keeps things (read-only)
# --------------------------------------------------------------------------- #
#: When set (non-blank), the ONLY folder OpenCode's own sessions are read from
#: for the Code view's listings (the test suite points it at an empty folder,
#: as it does ``providers.opencode.CONFIG_DIR_ENV``: a test must never read the
#: developer's real OpenCode database). The OpenCode PROCESS is not told.
DATA_DIR_ENV = "IRONJARVIS_OPENCODE_DATA_DIR"


def data_dir(environ: Any = None) -> Path:
    """OpenCode's data folder: :data:`DATA_DIR_ENV` when set, else
    ``$XDG_DATA_HOME/opencode``, else ``~/.local/share/opencode`` (its
    database and its sign-ins)."""
    env = os.environ if environ is None else environ
    override = str(env.get(DATA_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    xdg = str(env.get("XDG_DATA_HOME") or "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


def _connect_ro(db: Path) -> sqlite3.Connection | None:
    """A READ-ONLY connection to OpenCode's database (``mode=ro``: nothing is
    ever written; a short timeout so a busy writer never holds a listing)."""
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1.0)
        con.execute("PRAGMA query_only = 1")
        return con
    except sqlite3.Error:
        return None


def disk_sessions(home: str | os.PathLike[str], folder: str, *, since: float | None = None,
                  limit: int = 500) -> list[dict[str, Any]]:
    """BLOCKING: OpenCode's OWN saved top-level sessions for ``folder`` (read
    from its database, read-only), newest first. ``since``: only sessions
    updated at or after that time (epoch seconds)."""
    from ..history.common import iso, title_of

    con = _connect_ro(Path(home) / "opencode.db")
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT id, directory, title, time_created, time_updated FROM session "
            "WHERE parent_id IS NULL AND time_archived IS NULL ORDER BY time_updated DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    want = os.path.normcase(os.path.normpath(str(folder))) if folder else ""
    for sid, directory, title, created, updated in rows:
        if not isinstance(sid, str) or not SESSION_ID_RX.match(sid):
            continue
        try:
            here = os.path.normcase(os.path.normpath(str(directory or "")))
        except (TypeError, ValueError):
            continue
        if want and here != want:
            continue
        if since is not None and isinstance(updated, (int, float)) and updated / 1000.0 < since:
            continue
        out.append({
            "harness": HARNESS, "id": sid, "project": str(directory or ""),
            "title": title_of(str(title or "")),
            "started": iso(created) or "", "ended": iso(updated) or "",
            "mtime": (updated / 1000.0) if isinstance(updated, (int, float)) else 0,
        })
    return out


def has_messages(home: str | os.PathLike[str], hid: str) -> bool:
    """BLOCKING: does OpenCode's saved session ``hid`` hold a message? Then
    OpenCode, reopening it, keeps the model and effort it last used."""
    if not hid:
        return False
    con = _connect_ro(Path(home) / "opencode.db")
    if con is None:
        return False
    try:
        return con.execute("SELECT 1 FROM message WHERE session_id = ? LIMIT 1", (hid,)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# The user's own permission rules (read-only) and each mode's environment
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Any:
    from ..providers.opencode import _strip_jsonc

    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        raw = _strip_jsonc(path.read_text(encoding="utf-8"))
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)
        return json.loads(raw)
    except (OSError, ValueError):
        return None


def config_files(folder: str, environ: Any = None) -> list[Path]:
    """Every config file OpenCode merges for a process started in
    ``folder``: the global ones (``providers.opencode.opencode_config_paths``),
    ``OPENCODE_CONFIG``, ``OPENCODE_CONFIG_DIR`` and the project's own
    ``opencode.json(c)`` / ``.opencode/opencode.json(c)`` in the folder and
    every folder above it."""
    from ..providers.opencode import opencode_config_paths

    env = os.environ if environ is None else environ
    # The global files the way Iron Jarvis reads OpenCode's config everywhere
    # (``CONFIG_DIR_ENV``: the daemon's own override, which a child's
    # environment no longer carries).
    out = list(opencode_config_paths({**os.environ, **dict(env)}))
    one = str(env.get("OPENCODE_CONFIG") or "").strip()
    if one:
        out.append(Path(one))
    cdir = str(env.get("OPENCODE_CONFIG_DIR") or "").strip()
    if cdir:
        out += [Path(cdir) / "opencode.jsonc", Path(cdir) / "opencode.json"]
    if folder:
        here = Path(folder)
        for _ in range(40):
            out += [here / "opencode.jsonc", here / "opencode.json",
                    here / ".opencode" / "opencode.jsonc", here / ".opencode" / "opencode.json"]
            if here.parent == here:
                break
            here = here.parent
    return out


def _rank(value: Any) -> int:
    """How strict a rule is (a pattern map: its strictest pattern; anything
    unreadable counts as the strictest)."""
    if isinstance(value, str):
        return _RANK.get(value.strip().lower(), 2)
    if isinstance(value, dict):
        return max((_rank(v) for v in value.values()), default=0)
    return 2


def _merge_rules(into: dict[str, Any], rules: Any) -> str | None:
    """Fold one source's ``permission`` into ``into``; a string rule for
    EVERYTHING is returned (it decides alone)."""
    if isinstance(rules, str):
        return rules
    if isinstance(rules, dict):
        for k, v in rules.items():
            key = str(k)
            if key not in into or _rank(v) > _rank(into[key]):
                into[key] = v
    return None


def user_rules(folder: str, environ: Any = None) -> dict[str, Any] | str:
    """BLOCKING: the user's OWN OpenCode permission rules for a process in
    ``folder`` — every config file (read-only), ``OPENCODE_CONFIG_CONTENT``
    and an ``OPENCODE_PERMISSION`` already in the environment — as ``{tool:
    rule}`` (the stricter of two wins), or a single string rule."""
    env = os.environ if environ is None else environ
    found: dict[str, Any] = {}
    whole: list[str] = []
    for path in config_files(folder, env):
        data = _read_json(path)
        if isinstance(data, dict) and "permission" in data:
            got = _merge_rules(found, data["permission"])
            if got is not None:
                whole.append(got)
    for name in ("OPENCODE_CONFIG_CONTENT",):
        raw = str(env.get(name) or "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if isinstance(data, dict) and "permission" in data:
                got = _merge_rules(found, data["permission"])
                if got is not None:
                    whole.append(got)
    raw = str(env.get("OPENCODE_PERMISSION") or "").strip()
    if raw:
        try:
            got = _merge_rules(found, json.loads(raw))
        except ValueError:
            got = None
        if got is not None:
            whole.append(got)
    if whole:
        return max(whole, key=_rank)
    return found


def permission_env(mode: str, rules: dict[str, Any] | str) -> tuple[dict[str, str] | None, list[str]]:
    """``(OPENCODE_PERMISSION for this mode, the tools whose rule stays the
    user's own)``. NEVER WIDENS a rule of the user's: a key their config sets
    is left out (OpenCode would put ours after theirs, and the last one wins),
    and ``"*"`` is used only when no rule of theirs is stricter than it.
    ``None``: the user has ONE rule for everything — it decides alone."""
    if isinstance(rules, str):
        return None, ["*"]
    posture = MODES.get(mode, MODES["ask"])[0]
    star = _POSTURE_STAR[posture]
    out: dict[str, str] = {}
    if all(_rank(v) <= _RANK[star] for v in rules.values()):
        out["*"] = star
    else:
        for name in ACTING_TOOLS:
            if name not in rules:
                out[name] = star
    if posture != "full":
        for name in READ_ONLY_TOOLS:
            if name not in rules:
                out[name] = "allow"
        if posture == "edits" and "edit" not in rules:
            out["edit"] = "allow"
    kept = sorted(k for k in rules if k != "*" or "*" not in out)
    if "*" in out:
        # Our "*" lands after the user's own keys and wins over them: every
        # rule of theirs is looser than it (or equal), so nothing is widened,
        # and none of theirs is "kept" as written.
        kept = []
    return out, kept


def child_env(env: dict[str, str], mode: str, folder: str) -> tuple[dict[str, str], str]:
    """BLOCKING: a Build terminal's environment with this mode's permission
    rules for OpenCode, and the notice to give when a rule of the user's own
    stays as they wrote it ("" = none)."""
    out = dict(env)
    rules = user_rules(folder, out)
    perm, kept = permission_env(mode, rules)
    if perm is None:
        return out, W.OPENCODE_RULES_DECIDE
    out["OPENCODE_PERMISSION"] = json.dumps(perm, separators=(",", ":"))
    if kept:
        return out, W.OPENCODE_RULES_KEPT.format(tools=", ".join(kept[:12]))
    return out, ""


# --------------------------------------------------------------------------- #
# The tools' words
# --------------------------------------------------------------------------- #
_BY_NAME = {
    "bash": "command", "shell": "command", "edit": "edit", "multiedit": "edit", "patch": "edit",
    "apply_patch": "edit", "write": "write", "read": "read", "list": "read", "ls": "read",
    "glob": "search", "grep": "search", "codesearch": "search", "webfetch": "web", "websearch": "web",
    "task": "task", "todowrite": "todo", "todoread": "todo",
}
_BY_KIND = {"execute": "command", "edit": "edit", "delete": "edit", "move": "edit", "read": "read",
            "search": "search", "fetch": "web"}


def _base(path: Any) -> str:
    return os.path.basename(str(path or "").replace("\\", "/")) or str(path or "")


def _path_of(args: dict[str, Any]) -> str:
    return str(args.get("filePath") or args.get("filepath") or args.get("path") or args.get("file_path") or "")


def tool_kind(name: str, acp_kind: str = "") -> str:
    n = str(name or "").lower()
    if n in _BY_NAME:
        return _BY_NAME[n]
    if acp_kind in _BY_KIND:
        return _BY_KIND[acp_kind]
    if "_" in n and acp_kind == "other":
        return "mcp"
    return "other"


def tool_title(name: str, kind: str, args: dict[str, Any]) -> str:
    path = _path_of(args)
    if kind == "command":
        cmd = one_line(args.get("command"), 90)
        return f"Run {cmd}" if cmd else "Run a command"
    if kind == "edit":
        return f"Edit {_base(path)}" if path else "Edit a file"
    if kind == "write":
        return f"Write {_base(path)}" if path else "Write a file"
    if kind == "read":
        if str(name).lower() in ("list", "ls"):
            return f"List {_base(path)}" if path else "List the folder"
        return f"Read {_base(path)}" if path else "Read a file"
    if kind == "search":
        pat = one_line(args.get("pattern") or args.get("query"), 60)
        return f"Search for {pat}" if pat else "Search the files"
    if kind == "web":
        url = one_line(args.get("url"), 90)
        if url:
            return f"Fetch {url}"
        q = one_line(args.get("query"), 80)
        return f"Search the web for {q}" if q else "Search the web"
    if kind == "task":
        return "Hand a task to a helper"
    if kind == "todo":
        return "Update the plan"
    if kind == "mcp":
        return f"Use {name}"
    return f"Use {name}" if name else "Use a tool"


def _input_line(args: Any) -> str:
    try:
        return clip(json.dumps(args, ensure_ascii=False, sort_keys=True), INPUT_CHARS)
    except (TypeError, ValueError):
        return ""


def content_diff(content: Any) -> str:
    """The unified diff of ACP ``diff`` content blocks (``oldText`` empty or
    absent = a new file)."""
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "diff":
            continue
        path = str(block.get("path") or "")
        old = block.get("oldText")
        new = str(block.get("newText") or "")
        parts.append(unified_new_file(path, new) if not old else unified_replace(path, str(old), new))
    return "\n".join(p for p in parts if p)


def args_diff(name: str, args: dict[str, Any]) -> str:
    """The change an edit / write asks for, from its own input."""
    n = str(name or "").lower()
    path = _path_of(args)
    if not path:
        return ""  # nothing to show yet (a tool's first record has no input)
    if n == "write":
        return unified_new_file(path, str(args.get("content") or ""))
    if n == "edit":
        return unified_replace(path, str(args.get("oldString") or ""), str(args.get("newString") or ""))
    if isinstance(args.get("diff"), str):
        return str(args["diff"])
    return ""


def content_text(content: Any) -> str:
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "content":
            inner = block.get("content") if isinstance(block.get("content"), dict) else {}
            if inner.get("type") == "text":
                out.append(str(inner.get("text") or ""))
    return "\n".join(out)


def output_of(update: dict[str, Any]) -> str:
    raw = update.get("rawOutput") if isinstance(update.get("rawOutput"), dict) else {}
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    for value in (raw.get("output"), raw.get("error"), content_text(update.get("content")), meta.get("output")):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def failure_event(said: str) -> dict[str, Any]:
    """The ``error`` event for a failure OpenCode SAID. Its sign-in is its own
    (``opencode auth login``) and it has no accounts to move between."""
    text = str(said or "")
    if _SIGN_IN_RX.search(text):
        return W.error("OpenCode isn't signed in for this model", SIGN_IN_FIX, text, fix_kind="sign_in")
    if _LIMIT_RX.search(text):
        return W.error("OpenCode's model hit its usage limit", LIMIT_FIX, text)
    return W.error("OpenCode's model answered with an error", OTHER_FIX, text)


def _config(options: Any, cid: str) -> dict[str, Any]:
    for o in options if isinstance(options, list) else []:
        if isinstance(o, dict) and o.get("id") == cid:
            return o
    return {}


def _values(option: dict[str, Any]) -> list[str]:
    return [str(v.get("value")) for v in option.get("options") or [] if isinstance(v, dict) and v.get("value")]


def model_rows(options: Any, known: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """``configOptions`` -> the composer's model rows; the CURRENT model's
    effort levels are kept on its row (``efforts``; ``[]`` = none), and the
    levels other models were seen to take before are kept too."""
    model = _config(options, "model")
    current = str(model.get("currentValue") or "")
    effort = _config(options, "effort")
    seen = {str(r.get("id")): r for r in known or [] if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    for v in model.get("options") or []:
        if not isinstance(v, dict) or not v.get("value"):
            continue
        mid = clip(v.get("value"), 120)
        row: dict[str, Any] = {"id": mid, "label": clip(v.get("name") or mid, 120)}
        if mid == current:
            row["efforts"] = _values(effort) if effort else []
        elif isinstance((seen.get(mid) or {}).get("efforts"), list):
            row["efforts"] = list(seen[mid]["efforts"])
        out.append(row)
        if len(out) >= MAX_MODELS:
            break
    return out


def save_models(path: Path, rows: list[dict[str, Any]], default: str = "") -> None:
    """BLOCKING: the model list an OpenCode process said, for the next listing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(json.dumps({"models": rows[:MAX_MODELS], "default": default},
                                   ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep OpenCode's model list", exc_info=True)


#: The model OpenCode starts a NEW session on (the first ``currentValue`` seen).
LAST_DEFAULT: list[str] = []


def cached_models(path: Path | None) -> tuple[list[dict[str, Any]], str]:
    """BLOCKING-light: ``(what an OpenCode process last listed, its default
    model)`` — memory, else disk."""
    if LAST_MODELS:
        return list(LAST_MODELS), (LAST_DEFAULT[0] if LAST_DEFAULT else "")
    if path is None:
        return [], ""
    try:
        if path.is_file() and path.stat().st_size < 4_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("models") if isinstance(data, dict) else None
            out = []
            for r in rows or []:
                if not (isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("id")):
                    continue
                row: dict[str, Any] = {"id": clip(r["id"], 120), "label": clip(r.get("label") or r["id"], 120)}
                if isinstance(r.get("efforts"), list):
                    row["efforts"] = [str(e) for e in r["efforts"] if isinstance(e, str)][:12]
                out.append(row)
            default = str(data.get("default") or "") if isinstance(data, dict) else ""
            return out[:MAX_MODELS], clip(default, 120)
    except (OSError, ValueError):
        pass
    return [], ""


def _read_images(files: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """BLOCKING: pictures as ACP image blocks (bounded); other files listed."""
    images: list[dict[str, Any]] = []
    others: list[str] = []
    for f in files:
        mime = IMAGE_TYPES.get(os.path.splitext(f)[1].lower())
        if mime and len(images) < MAX_IMAGES:
            try:
                if os.path.getsize(f) <= MAX_IMAGE_BYTES:
                    with open(f, "rb") as fh:
                        images.append({"type": "image", "data": base64.b64encode(fh.read()).decode("ascii"),
                                       "mimeType": mime})
                    continue
            except OSError:
                pass
        others.append(f)
    return images, others


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
class OpenCodeDriver(Driver):
    harness = HARNESS

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._replies = ControlWaiter()
        self._n = 0
        self._tag = uuid.uuid4().hex[:8]
        #: True while ``session/load`` replays the saved conversation.
        self._loading = False
        self._errored = False
        self._interrupting = False
        #: The prompt ids of the open turn: the message's, then each note's.
        self._message_prompt: int | None = None
        self._pending: dict[int, str] = {}
        self._stop: str = ""
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._cost: float | None = None
        self._cost_start: float | None = None
        #: toolCallId -> (name, acp kind, input) of a tool that has not ended.
        self._tools: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._updated: dict[str, float] = {}
        #: The text of each answer item, in order (its text_done at its end).
        self._texts: dict[str, str] = {}
        self._text_open = ""
        self.options: list[dict[str, Any]] = []
        self.can_load = False
        self.takes_pictures = True
        #: Where the service keeps the model list (set before ``open``).
        self.catalog_path: Path | None = None
        #: A notice to say once the session is open (a rule of the user's own).
        self.rules_note = ""

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        # The folder is the process's own working folder (never an argument:
        # npm's opencode.cmd runs through cmd.exe, which reads & and % in one).
        # Never --pure (it switches the user's own plugins off).
        return ["acp"]

    # -- JSON-RPC ---------------------------------------------------------------
    def _next(self) -> int:
        self._n += 1
        return self._n

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      timeout: float | None = REQUEST_TIMEOUT_S) -> Any:
        rid = self._next()
        fut = self._replies.make(rid)
        await self.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        if timeout is None:
            return await fut
        return await asyncio.wait_for(fut, timeout=timeout)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self.write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(self, rid: Any, result: Any = None, error: str = "") -> None:
        if error:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": error}}
        else:
            msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        try:
            await self.write(msg)
        except Exception:  # noqa: BLE001 — a gone process: nothing to answer
            pass

    # -- the session --------------------------------------------------------------
    async def open(self, *, session_id: str, resume: bool) -> None:
        from .. import __version__

        got = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            # Iron Jarvis is not an editor: OpenCode reads, writes and runs
            # commands with its OWN tools, exactly as in a terminal.
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "iron-jarvis", "title": "Iron Jarvis", "version": __version__},
        })
        caps = (got or {}).get("agentCapabilities") if isinstance(got, dict) else {}
        caps = caps if isinstance(caps, dict) else {}
        self.can_load = bool(caps.get("loadSession"))
        prompt_caps = caps.get("promptCapabilities") if isinstance(caps.get("promptCapabilities"), dict) else {}
        self.takes_pictures = bool(prompt_caps.get("image"))
        if resume and session_id:
            if not self.can_load:
                raise ControlRefused("this OpenCode cannot reopen a saved session")
            self._loading = True
            try:
                res = await self.request("session/load", {"sessionId": session_id, "cwd": self.cwd,
                                                          "mcpServers": []})
            finally:
                self._loading = False
            sid = session_id
        else:
            res = await self.request("session/new", {"cwd": self.cwd, "mcpServers": []})
            sid = str((res or {}).get("sessionId") or "") if isinstance(res, dict) else ""
        if not sid:
            raise ControlRefused("OpenCode did not open a session")
        self.session_id = sid
        self.on_session_id(sid)
        self._take_options((res or {}).get("configOptions") if isinstance(res, dict) else None, first=not resume)
        # The choices of this session: each only when it differs (a reopened
        # conversation keeps its own unless the user chose another).
        if self.model and self.model != self._current("model"):
            await self._set_option("model", self.model)
        agent = MODES.get(self.mode, MODES["ask"])[1]
        if self._current("mode") and self._current("mode") != agent:
            await self._set_option("mode", agent)
        if self.effort and self.effort != self._current("effort"):
            if self.effort in _values(_config(self.options, "effort")):
                await self._set_option("effort", self.effort)
            else:
                self.emit(make("notice", text=W.OPENCODE_NO_SUCH_EFFORT.format(effort=self.effort)))
        self.emit(make("session", harness=HARNESS, harness_session_id=sid,
                       model=clip(self._current("model") or self.model, 120), cwd=clip(self.cwd, 500),
                       mode=self.mode))
        if self.rules_note:
            self.emit(make("notice", text=clip(self.rules_note, 500)))

    def _current(self, cid: str) -> str:
        return str(_config(self.options, cid).get("currentValue") or "")

    def _take_options(self, options: Any, *, first: bool = False) -> None:
        if not isinstance(options, list):
            return
        self.options = [o for o in options if isinstance(o, dict)]
        rows = model_rows(self.options, LAST_MODELS)
        if rows:
            LAST_MODELS[:] = rows
            if first and not LAST_DEFAULT:
                LAST_DEFAULT[:] = [self._current("model")]
            if self.catalog_path is not None:
                path, default = self.catalog_path, (LAST_DEFAULT[0] if LAST_DEFAULT else "")
                try:
                    asyncio.get_running_loop().run_in_executor(None, save_models, path, list(rows), default)
                except RuntimeError:
                    pass

    async def _set_option(self, cid: str, value: str) -> None:
        try:
            got = await self.request("session/set_config_option",
                                     {"sessionId": self.session_id, "configId": cid, "value": value})
        except ControlRefused as exc:
            raise ControlRefused(f"OpenCode refused that {cid}: {exc}") from None
        self._take_options((got or {}).get("configOptions") if isinstance(got, dict) else None)

    # -- the user's side ----------------------------------------------------------
    async def _prompt_blocks(self, text: str, files: list[str]) -> list[dict[str, Any]]:
        if self.takes_pictures:
            images, others = await asyncio.to_thread(_read_images, files)
            for f in others:
                if os.path.splitext(f)[1].lower() in IMAGE_TYPES:
                    self.emit(make("notice", text=W.PICTURE_AS_PATH.format(
                        name=os.path.basename(f), label="OpenCode",
                        why=f"over the {MAX_IMAGE_BYTES // (1024 * 1024)} MB or {MAX_IMAGES} pictures sent "
                            "in one message")))
        else:
            images, others = [], list(files)
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_TYPES:
                    self.emit(make("notice", text=W.PICTURE_AS_PATH.format(
                        name=os.path.basename(f), label="OpenCode", why="this OpenCode takes no pictures")))
        body = str(text or "")
        if others:
            listed = "\n".join(others)
            body = f"{body}\n\nFiles I attached:\n{listed}" if body.strip() else f"Files I attached:\n{listed}"
        return [{"type": "text", "text": body}, *images]

    def _start_prompt(self, blocks: list[dict[str, Any]], ref: str = "") -> int:
        rid = self._next()
        fut = self._replies.make(rid)
        self._pending[rid] = ref
        fut.add_done_callback(lambda f, rid=rid: self._prompt_answered(rid, f))
        return rid

    async def send_user(self, text: str, files: list[str]) -> None:
        blocks = await self._prompt_blocks(text, files)
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        self._stop = ""
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._cost_start = self._cost
        self._pending.clear()
        rid = self._start_prompt(blocks)
        self._message_prompt = rid
        await self.write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": blocks}})

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """A note while it works: a SECOND ``session/prompt`` — OpenCode's loop
        reads it at its next step and answers it with the same turn (measured).
        The turn ends only when every prompt of it has its answer, so a note
        can never start a run nobody follows."""
        if not (self.turn_open and self._message_prompt in self._pending):
            raise ControlRefused(W.STEER_TOO_LATE.format(label="OpenCode"))
        blocks = await self._prompt_blocks(text, files)
        if not (self.turn_open and self._message_prompt in self._pending):
            # The turn ended while the note's pictures were read: sent now, it
            # would start a run of its own while the box gets it back.
            raise ControlRefused(W.STEER_TOO_LATE.format(label="OpenCode"))
        self.steers[ref] = "sent"
        rid = self._start_prompt(blocks, ref)
        try:
            await self.write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                              "params": {"sessionId": self.session_id, "prompt": blocks}})
        except BaseException:
            self._pending.pop(rid, None)
            self._replies.settle(rid, error=ControlRefused("not sent"))
            self.steers.pop(ref, None)
            raise
        return "step"

    def _prompt_answered(self, rid: int, fut: asyncio.Future) -> None:
        ref = self._pending.pop(rid, None)
        if fut.cancelled():
            return
        exc = fut.exception()
        result = fut.result() if exc is None else None
        stop = str((result or {}).get("stopReason") or "end_turn") if isinstance(result, dict) else ""
        if ref:
            if exc is None and stop != "cancelled":
                self.note_read(ref)
            elif exc is None and self.steers.get(ref) == "sent":
                # Stopped before OpenCode's loop came to it: the note is in the
                # conversation all the same (OpenCode keeps every prompt it
                # took), so it is never handed back to be sent twice.
                self.steers[ref] = "read"
                self.emit(make("notice", text=W.OPENCODE_NOTE_KEPT))
        if rid == self._message_prompt:
            if exc is not None:
                if isinstance(exc, ControlRefused):
                    self._fail(str(exc))
                    self._stop = "failed"
                else:
                    return  # the process went: the service ends the turn
            else:
                usage = (result or {}).get("usage") if isinstance(result, dict) else None
                for ours, theirs in (("input", "inputTokens"), ("output", "outputTokens"),
                                     ("cache_read", "cachedReadTokens")):
                    v = (usage or {}).get(theirs) if isinstance(usage, dict) else None
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        self._usage[ours] = int(v)
                self._stop = {"end_turn": "completed", "cancelled": "interrupted", "max_tokens": "completed",
                              "max_turn_requests": "max_turns", "refusal": "failed"}.get(stop, "completed")
                if stop == "max_tokens":
                    self.emit(make("notice", text="The answer reached the model's output limit."))
                if stop == "refusal":
                    self._fail("The model refused to go on with this request.")
                if self._interrupting and self._stop == "completed":
                    self._stop = "interrupted"
            self._message_prompt = None
        if self._message_prompt is None and not self._pending and self.turn_open:
            self._end(self._stop or "completed")

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        """Nothing to keep: OpenCode's "always" lives in OpenCode."""

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        """The JSON-RPC answer to ``session/request_permission`` — or
        AskRefused, before anything is sent. Only an option OpenCode OFFERED
        for this very request is ever chosen."""
        want = {"allow": "allow_once", "allow_session": "allow_always", "deny": "reject_once"}[decision]
        offered = ask.data.get("options") or []
        pick = next((o for o in offered if o.get("kind") == want), None)
        if pick is None and decision == "deny":
            pick = next((o for o in offered if str(o.get("kind") or "").startswith("reject")), None)
        if pick is None:
            if decision == "allow_session":
                raise AskRefused("OpenCode offers no session-wide allow for this one — allow it once.")
            if decision == "deny":
                return {"jsonrpc": "2.0", "id": ask.native, "result": {"outcome": {"outcome": "cancelled"}}}
            raise AskRefused("OpenCode did not offer that answer for this one.")
        return {"jsonrpc": "2.0", "id": ask.native,
                "result": {"outcome": {"outcome": "selected", "optionId": pick.get("optionId")}}}

    async def interrupt(self) -> None:
        if not self.session_id:
            return
        self._interrupting = True
        await self.notify("session/cancel", {"sessionId": self.session_id})

    async def set_mode(self, mode: str) -> str:
        """The AGENT (build / plan) changes live; the permission posture is the
        process's environment, so a posture change needs a restart before the
        next message (``restart_needed``)."""
        old = MODES.get(self.mode, MODES["ask"])
        new = MODES.get(mode, MODES["ask"])
        if new[1] != old[1] and self.session_id:
            await self._set_option("mode", new[1])
        self.mode = mode
        if new[0] != old[0]:
            self.restart_needed = True
            return "next"
        return "next" if self.turn_open else "now"

    async def set_model(self, model: str) -> str:
        values = _values(_config(self.options, "model"))
        if values and model not in values:
            raise ControlRefused("OpenCode does not list that model — choose one from its list.")
        await self._set_option("model", model)
        self.model = model
        return "next" if self.turn_open else "now"

    async def set_effort(self, effort: str) -> str:
        if effort:
            offered = _values(_config(self.options, "effort"))
            if offered and effort not in offered:
                raise ControlRefused(f'This OpenCode model takes {", ".join(offered)} — not "{effort}".')
            await self._set_option("effort", effort)
        self.effort = effort
        return ("next" if self.turn_open else "now") if effort else "next"

    #: Set when a change can only apply to a NEW process (the service restarts
    #: it before the next message).
    restart_needed = False

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._replies.fail_all(HarnessGone("OpenCode stopped"))

    # -- OpenCode's side (ON THE LOOP) ---------------------------------------------
    def on_message(self, obj: dict[str, Any]) -> None:
        method = obj.get("method")
        if method is None and "id" in obj:  # an answer to our request
            if "error" in obj:
                err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
                self._replies.settle(obj["id"], error=ControlRefused(clip(err.get("message") or "refused", 600)))
            else:
                self._replies.settle(obj["id"], obj.get("result"))
            return
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        if method and "id" in obj:
            self._on_request(str(method), obj["id"], params)
            return
        if method == "session/update":
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            self._on_update(update)

    def _on_request(self, method: str, rid: Any, p: dict[str, Any]) -> None:
        if method == "session/request_permission":
            self._ask(rid, p)
            return
        # fs/* and terminal/*: Iron Jarvis said it is no editor (OpenCode wrote
        # the file itself and only tells an editor it did — measured).
        asyncio.ensure_future(self._respond(rid, error="Iron Jarvis does not handle this request"))

    def _ask(self, rid: Any, p: dict[str, Any]) -> None:
        tc = p.get("toolCall") if isinstance(p.get("toolCall"), dict) else {}
        options = [o for o in p.get("options") or [] if isinstance(o, dict) and o.get("optionId")]
        tid = str(tc.get("toolCallId") or "")
        known = self._tools.get(tid)
        name = known[0] if known else str(tc.get("title") or "")
        acp_kind = str(tc.get("kind") or (known[1] if known else ""))
        args = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) and tc.get("rawInput") else (
            known[2] if known else {})
        kind = tool_kind(name, acp_kind)
        card = {"command": "command", "edit": "edit", "write": "edit", "web": "network"}.get(kind, "tool")
        if acp_kind == "execute":
            card, kind = "command", "command"
        title = tool_title(name, kind, args)
        if kind == "other" and tc.get("title"):
            title = one_line(tc.get("title"), 120)
        can_session = any(o.get("kind") == "allow_always" for o in options)
        ask_id = new_ask_id()
        ev = make("approval", request_id=ask_id, kind=card, title=title,
                  detail=clip(tc.get("title") or "", DETAIL_CHARS), can_session=can_session, tool=clip(name, 120))
        if kind == "command":
            ev["command"] = clip(args.get("command") or "", 2000)
        path = _path_of(args) or next(
            (str(loc.get("path")) for loc in tc.get("locations") or [] if isinstance(loc, dict) and loc.get("path")), "")
        if path:
            ev["path"] = clip(path, 500)
        diff = content_diff(tc.get("content")) or (args_diff(name, args) if kind in ("edit", "write") else "")
        if diff:
            ev["diff"] = clip(diff, DIFF_CHARS)
        if not can_session:
            ev["once_only"] = True
        data = {"options": [{"optionId": o.get("optionId"), "kind": o.get("kind")} for o in options]}
        self.raise_ask(Ask(ask_id, "approval", name or "tool", rid, data, ev, ev["at"], can_session))

    def _on_update(self, u: dict[str, Any]) -> None:
        kind = u.get("sessionUpdate")
        if kind == "available_commands_update":
            cmds = u.get("availableCommands")
            self.commands = [
                {"name": str(c.get("name") or ""), "description": clip(c.get("description") or "", 200)}
                for c in (cmds or []) if isinstance(c, dict) and c.get("name")
            ][:300]
            if self.commands:
                from .claude import LAST_COMMANDS

                LAST_COMMANDS[HARNESS] = list(self.commands)
            return
        if kind == "usage_update":
            cost = u.get("cost") if isinstance(u.get("cost"), dict) else {}
            amount = cost.get("amount")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                self._cost = float(amount)
            return
        if kind == "config_option_update":
            self._take_options(u.get("configOptions"))
            return
        if self._loading or not self.turn_open:
            # A reopened session's replay, or anything outside a message:
            # what OpenCode says about the PAST is not drawn again.
            return
        if kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text") if isinstance(u.get("content"), dict) else None
            if text:
                item = f"{self._tag}-{u.get('messageId') or 'm'}"
                self._close_text(except_item=item)
                self._text_open = item
                self._texts[item] = (self._texts.get(item, "") + str(text))[-TEXT_CHARS:]
                self.emit(make("text", item=item, delta=clip(text, TEXT_CHARS)))
        elif kind == "agent_thought_chunk":
            text = (u.get("content") or {}).get("text") if isinstance(u.get("content"), dict) else None
            if text:
                self.emit(make("thinking", item=f"{self._tag}-t-{u.get('messageId') or 'm'}",
                               delta=clip(text, TEXT_CHARS)))
        elif kind == "tool_call":
            self._close_text()
            self._tool_update(u, first=True)
        elif kind == "tool_call_update":
            self._tool_update(u, first=False)
        elif kind == "plan":
            steps = []
            for e in u.get("entries") or []:
                if isinstance(e, dict):
                    st = {"in_progress": "in_progress", "completed": "completed"}.get(str(e.get("status")), "pending")
                    steps.append({"text": clip(e.get("content") or "", 300), "status": st})
            self.emit(make("plan", steps=steps))

    def _close_text(self, except_item: str = "") -> None:
        item = self._text_open
        if item and item != except_item:
            text = self._texts.pop(item, "")
            if text.strip():
                self.emit(make("text_done", item=item, text=clip(text, TEXT_CHARS)))
            self._text_open = ""

    def _tool_update(self, u: dict[str, Any], *, first: bool) -> None:
        tid = str(u.get("toolCallId") or "")
        if not tid:
            return
        known = self._tools.get(tid)
        if first or known is None:
            # The FIRST record names the tool (its title is the tool's name).
            name = str(u.get("title") or "")
            acp_kind = str(u.get("kind") or "")
            args: dict[str, Any] = {}
        else:
            name, acp_kind, args = known
        if isinstance(u.get("rawInput"), dict) and u["rawInput"]:
            args = u["rawInput"]
        acp_kind = str(u.get("kind") or acp_kind)
        status = str(u.get("status") or "pending")
        self._tools[tid] = (name, acp_kind, args)
        ours = {"completed": "done", "failed": "failed"}.get(status, "running")
        if ours == "running" and not first:
            now = asyncio.get_running_loop().time()
            if known is not None and known[2] and now - self._updated.get(tid, -UPDATE_EVERY_S) < UPDATE_EVERY_S:
                return  # a command's running output: at most one row update a second
            self._updated[tid] = now
        kind = tool_kind(name, acp_kind)
        ev = make("tool", item=tid, kind=kind, name=clip(name, 120), title=tool_title(name, kind, args),
                  input=_input_line(args), status=ours)
        path = _path_of(args)
        if path and kind in ("read", "edit", "write"):
            ev["path"] = clip(path, 500)
        offset = args.get("offset")
        if kind == "read" and isinstance(offset, int) and not isinstance(offset, bool) and offset > 0:
            ev["line"] = offset
        if kind in ("edit", "write"):
            diff = content_diff(u.get("content")) or args_diff(name, args)
            if diff:
                ev["diff"] = clip(diff, DIFF_CHARS)
        out = output_of(u)
        if out and (ours != "running" or kind == "command"):
            ev["output"] = clip(out[-OUTPUT_CHARS * 2:], OUTPUT_CHARS)
        if ours != "running":
            self._tools.pop(tid, None)
            self._updated.pop(tid, None)
        self.emit(ev)

    # -- the end of a turn --
    def _fail(self, said: str) -> None:
        if self._errored:
            return
        self._errored = True
        self.emit(failure_event(said))

    def _end(self, stop: str) -> None:
        if not self.turn_open:
            return
        self.turn_open = False
        self._close_text()
        for item in list(self._texts):
            text = self._texts.pop(item)
            if text.strip():
                self.emit(make("text_done", item=item, text=clip(text, TEXT_CHARS)))
        self._tools.clear()
        self._interrupting = False
        if any(self._usage.values()) or (self._cost is not None and self._cost_start is not None
                                         and self._cost > self._cost_start):
            # OpenCode prices each call from its own model table (a free model
            # says 0); the ACP does not say whether it is a subscription or a
            # pay-per-use key: "the cost OpenCode reported", never list price.
            usage = make("usage", **self._usage, list_price=False)
            if self._cost is not None and self._cost_start is not None and self._cost > self._cost_start:
                usage["cost_usd"] = round(self._cost - self._cost_start, 6)
                usage["cost_reported_by"] = "OpenCode"
            self.emit(usage)
        self.emit(make("done", stop=stop))
