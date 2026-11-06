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
NO listening port of Iron Jarvis's. OpenCode still runs its internal server
inside the process; with no flag the user's global ``server.hostname`` /
``server.port`` / ``server.mdns`` decide (review, measured: ``"0.0.0.0"``
listened on every interface, unauthenticated), so Iron Jarvis passes
``--hostname 127.0.0.1 --port 0 --mdns=false`` (:data:`ACP_ARGV`; explicit
flags win over the config): this PC only, and never a port the config
names (port 0 takes 4096 first, else a free one — never 8787 / 8788 / 8765).

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

READ for v1.420.0 (C1.10), from the installed opencode 1.18.18 program (its
bundled source: ``Permission.ask`` / ``Permission.reply`` / ``evaluate`` /
``Wildcard.match`` and each tool's ``ask`` call):

* The SAME merge order also NARROWED: a ``"*": "ask"`` of Iron Jarvis's
  landed after a user's ``"bash": "allow"`` and won, so their allowed
  commands became cards. Now no key of ours names a tool a rule of the user's
  names (exactly, or by a wildcard such as ``"web*"``), ``"*"`` is used only
  when every rule of theirs says exactly what it would, and a ``"*"`` of the
  user's own names every tool (we then add nothing but Plan's refusal; before,
  a ``"*": "deny"`` of theirs had the built-in tools named ``ask`` after it).
* A permission NAME is matched as a wildcard WITHOUT regard to case (flags
  ``si``), and a merged key the user already has keeps its PLACE. So Plan's
  ``edit`` refusal goes under a key the user does not have (``EDIT`` when
  they have ``edit``): it is then the last rule for ``edit``, and a user's
  ``"edit": …`` followed by ``"*": "allow"`` no longer undoes it.
* ``task`` asks with ``always: ["*"]``; the helper's own session gets only the
  parent session's folder rules and refusals, and its steps meet the same
  config rules. Iron Jarvis grants ``task`` in Ask first and Auto edits (as its
  own coding sessions grant helpers); every step OF the helper still asks.
  Plan keeps OpenCode's own rule for helpers.
* "always" pushes ``{permission, pattern, allow}`` for each of the request's
  ``always`` patterns onto a list that lives only as long as the PROCESS (the
  ``permission`` table in opencode.db is a different, newer mechanism; it
  stayed empty). ``always`` is ``["*"]`` for edit, webfetch, websearch, task,
  glob, grep, lsp and todowrite; for bash it is the command's prefix, which
  ACP never sends. A mode change starts a new process, so the driver keeps
  each "Allow for this session" (:data:`SESSION_ALWAYS`, per conversation)
  and answers the same question "always" itself in the new process — the
  whole tool for the first group, the exact command for bash, nothing for
  any other tool. ACP has no way to send rules to a running OpenCode, and
  putting them in ``OPENCODE_PERMISSION`` would sit in the user's rules'
  place; answering the request is what OpenCode itself would do.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import words as W
# v1.416.0: the ACP core OpenCode shares with Grok lives in ``hosted.acp``;
# the names below stay importable from here.
from .acp import (  # noqa: F401 — re-exported
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    PROTOCOL_VERSION,
    REQUEST_TIMEOUT_S,
    UPDATE_EVERY_S,
    AcpDriver,
    _base,
    _config,
    _input_line,
    _path_of,
    _read_images,
    _values,
    args_diff,
    content_diff,
    content_text,
    output_of,
    tool_kind,
    tool_title,
)
from .driver import Ask, ControlRefused
from .events import clip, make

log = logging.getLogger(__name__)

HARNESS = "opencode"

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
#: Reading in every posture but Full access: free, except a ``.env`` file —
#: OpenCode's OWN default asks before it reads one (keys and passwords live
#: there), and a plain ``"read": "allow"`` landed after that default and
#: silently dropped it (v1.413.0 review).
READ_RULE: dict[str, str] = {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"}
_RANK = {"allow": 0, "ask": 1, "deny": 2}

#: v1.420.0 (C1.10): OpenCode's own reach of an "always" answer, read from
#: the 1.18.18 program (each tool's ``ask({permission, patterns, always})``):
#: for these permissions ``always`` is ``["*"]`` — the rest of that tool.
#: ``bash``'s is the command's own prefix (``"git status *"``), worked out by
#: OpenCode and never sent over ACP, so Iron Jarvis keeps only the EXACT
#: command for it; any other tool's reach is not known and is not kept.
ALWAYS_COVERS_ALL: tuple[str, ...] = (
    "edit", "webfetch", "websearch", "task", "glob", "grep", "lsp", "todowrite",
)
#: An OpenCode tool's name -> the permission it asks under (its write, patch
#: and multi-edit ask as ``edit``).
_PERMISSION_OF = {"write": "edit", "multiedit": "edit", "patch": "edit", "apply_patch": "edit"}
#: v1.420.0 (C1.10): the "Allow for this session" answers of each OpenCode
#: conversation (its ``ses_`` id) — ``{permission: {pattern, ...}}``. OpenCode
#: keeps an "always" only for the life of ITS process, and a mode change
#: starts a new process, so the driver of the new process answers the same
#: question the same way itself (never a card the user already answered).
#: Memory only: a restart of Iron Jarvis forgets them, as OpenCode does.
SESSION_ALWAYS: dict[str, dict[str, set[str]]] = {}
MAX_ALWAYS_SESSIONS = 64
MAX_ALWAYS_PER_SESSION = 200
#: The longest command kept word for word (a card shows the first 2,000 chars).
MAX_ALWAYS_COMMAND = 1_900


def permission_of(tool: str) -> str:
    """The permission an OpenCode tool asks under (its own name otherwise)."""
    name = str(tool or "").strip().lower()
    return _PERMISSION_OF.get(name, name)


def remember_always(session_id: str, tool: str, command: str = "") -> bool:
    """Keep an "Allow for this session" answer for the conversation
    ``session_id``. Only what OpenCode's own "always" is KNOWN to cover: a
    whole tool from :data:`ALWAYS_COVERS_ALL`, or one exact command (never a
    command holding ``*`` or ``?``, which OpenCode would read as a wildcard)."""
    if not session_id:
        return False
    perm = permission_of(tool)
    if perm in ALWAYS_COVERS_ALL:
        pattern = "*"
    elif perm == "bash":
        pattern = str(command or "").strip()
        if not pattern or len(pattern) > MAX_ALWAYS_COMMAND or "*" in pattern or "?" in pattern:
            return False
    else:
        return False
    kept = SESSION_ALWAYS.pop(session_id, {})
    SESSION_ALWAYS[session_id] = kept  # newest last
    if sum(len(v) for v in kept.values()) >= MAX_ALWAYS_PER_SESSION:
        return False
    kept.setdefault(perm, set()).add(pattern)
    while len(SESSION_ALWAYS) > MAX_ALWAYS_SESSIONS:
        SESSION_ALWAYS.pop(next(iter(SESSION_ALWAYS)))
    return True


def always_covers(session_id: str, tool: str, command: str = "") -> bool:
    """Did the user already say "Allow for this session" to this very
    question in this conversation?"""
    kept = SESSION_ALWAYS.get(session_id) or {}
    patterns = kept.get(permission_of(tool)) or set()
    if "*" in patterns:
        return True
    return bool(command) and str(command).strip() in patterns


#: OpenCode's effort ("variant") names seen across models — a model's own
#: list (``configOptions.effort``) narrows it when known.
EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

#: An OpenCode session id as Iron Jarvis would ever type it into a terminal.
SESSION_ID_RX = re.compile(r"^ses_[A-Za-z0-9]{10,60}$")

#: How Iron Jarvis starts OpenCode: ACP, its internal server on loopback only.
ACP_ARGV: tuple[str, ...] = ("acp", "--hostname", "127.0.0.1", "--port", "0", "--mdns=false")

#: v1.414.0: an OpenCode that does not take an HTTP MCP server over ACP.
JARVIS_TOOLS_NOT_TAKEN = (
    "This OpenCode does not take an MCP server from Iron Jarvis, so the Iron Jarvis "
    "tools you turned on are not in this session."
)
MAX_MODELS = 300

#: The models an OpenCode process last listed (``configOptions.model``) and
#: the effort levels each was seen to take. Kept on disk by the service.
LAST_MODELS: list[dict[str, Any]] = []

_SIGN_IN_RX = re.compile(r"auth(?:entication)? required|not logged in|opencode auth login|no api key|"
                         r"\b401\b|unauthori[sz]ed|invalid api key|authentication (?:failed|error)|"
                         r"oauth.*(?:expired|refresh)|could not be refreshed", re.I)
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

    from ..providers.opencode import CONFIG_DIR_ENV

    env = os.environ if environ is None else environ
    merged = {**os.environ, **dict(env)}
    # The global files the way Iron Jarvis reads OpenCode's config everywhere
    # (``CONFIG_DIR_ENV``: the daemon's own override, which a child's
    # environment no longer carries).
    out = list(opencode_config_paths(merged))
    # v1.413.0 review: OpenCode merges more than that (its own loader): the
    # global ``config.json`` beside them, ``~/.opencode`` and an admin's
    # managed folder (``%ProgramData%\opencode``) — a deny in any of them
    # must keep this mode's ``"*"`` out. The override names the ONLY global
    # folder (the test suite's isolation).
    out += [p.with_name("config.json") for p in list(out) if p.name == "opencode.json"]
    if not str(merged.get(CONFIG_DIR_ENV) or "").strip():
        home = Path.home()
        out += [home / ".opencode" / "opencode.jsonc", home / ".opencode" / "opencode.json"]
        managed = str(merged.get("OPENCODE_TEST_MANAGED_CONFIG_DIR") or "").strip() or os.path.join(
            str(merged.get("ProgramData") or merged.get("PROGRAMDATA") or "C:\\ProgramData"), "opencode")
        out += [Path(managed) / "opencode.json", Path(managed) / "opencode.jsonc"]
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


def legacy_tool_rules(tools: dict[str, Any]) -> dict[str, str]:
    """OpenCode's own reading of the legacy ``tools`` map (its config loader:
    a true tool is ``allow``, anything else ``deny``)."""
    out: dict[str, str] = {}
    for name, on in tools.items():
        key = "edit" if str(name) in ("write", "edit", "patch") else str(name)
        rule = "allow" if on else "deny"
        if key not in out or _RANK[rule] > _RANK[out[key]]:
            out[key] = rule
    return out


def _rank(value: Any) -> int:
    """How strict a rule is (a pattern map: its strictest pattern; anything
    unreadable counts as the strictest)."""
    if isinstance(value, str):
        return _RANK.get(value.strip().lower(), 2)
    if isinstance(value, dict):
        return max((_rank(v) for v in value.values()), default=0)
    return 2


class _Mixed(str):
    """A rule two sources say DIFFERENTLY (v1.420.0 review): the stricter of
    them, which is never "exactly" anything — which one OpenCode ends up with
    depends on its load order and deep merge, which Iron Jarvis does not
    re-create (a global ``"bash": "ask"`` and a project's ``"bash": "allow"``
    read as ``ask`` here and as ``allow`` in OpenCode)."""


class _MixedMap(dict):
    """A pattern map two sources say differently (see :class:`_Mixed`)."""


def _same_rule(a: Any, b: Any) -> bool:
    if isinstance(a, (_Mixed, _MixedMap)) or isinstance(b, (_Mixed, _MixedMap)):
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_rule(a[k], b[k]) for k in a)
    return False


def _mixed(value: Any) -> Any:
    if isinstance(value, str):
        return _Mixed(value)
    if isinstance(value, dict):
        return _MixedMap(value)
    return value  # unreadable: never "exactly" anything already


def _merge_rules(into: dict[str, Any], rules: Any) -> str | None:
    """Fold one source's ``permission`` into ``into``; a string rule for
    EVERYTHING is returned (it decides alone). A tool two sources rule
    differently keeps the stricter rule, MARKED as mixed (:class:`_Mixed`)."""
    if isinstance(rules, str):
        return rules
    if isinstance(rules, dict):
        for k, v in rules.items():
            key = str(k)
            if key not in into:
                into[key] = v
            elif not _same_rule(into[key], v):
                into[key] = _mixed(v if _rank(v) > _rank(into[key]) else into[key])
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
        if isinstance(data, dict) and isinstance(data.get("tools"), dict):
            # v1.413.0 review: OpenCode's older ``"tools": {"bash": false}``
            # becomes a rule placed BEFORE the permission object: a deny of
            # the user's own all the same (write / edit / patch -> edit).
            _merge_rules(found, legacy_tool_rules(data["tools"]))
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


def _wild(name: str, pattern: str) -> bool:
    """OpenCode's own wildcard match (its ``Wildcard.match``, read from the
    1.18.18 program): ``\\`` reads as ``/``, ``*`` is any run, ``?`` one
    character, a trailing ``" *"`` also matches the bare word, and case does
    not matter (flags ``si``)."""
    text = str(name).replace("\\", "/")
    rx = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), str(pattern).replace("\\", "/"))
    rx = rx.replace("*", ".*").replace("?", ".")
    if rx.endswith(" .*"):
        rx = rx[:-3] + "( .*)?"
    return re.match("^" + rx + "$", text, re.S | re.I) is not None


def _users(tool: str, rules: dict[str, Any]) -> bool:
    """Does a rule of the user's own name ``tool`` (exactly or by a wildcard)?"""
    return any(_wild(tool, k) for k in rules)


def _fresh_key(name: str, taken: Any) -> str:
    """A key for ``name`` that is NOT one of the user's (``taken``), so
    OpenCode appends it AFTER every key of theirs (a key they already have
    keeps its place). OpenCode matches permission names without regard to
    case, so ``EDIT`` is the same tool as ``edit`` — and a new key."""
    for key in (name, name.upper(), name.capitalize()):
        if key not in taken:
            return key
    for combo in itertools.product(*[(c.lower(), c.upper()) for c in name]):
        key = "".join(combo)
        if key not in taken:
            return key
    return name  # pragma: no cover - every spelling taken


def _equal_to(value: Any, rank: int) -> bool:
    """Is a rule exactly ``rank`` for everything it names (a pattern map:
    every pattern)? An empty map names nothing. A rule the user's sources
    say differently is never exactly anything (:class:`_Mixed`)."""
    if isinstance(value, (_Mixed, _MixedMap)):
        return False
    if isinstance(value, str):
        return _RANK.get(value.strip().lower(), -1) == rank
    if isinstance(value, dict):
        return all(_equal_to(v, rank) for v in value.values())
    return False


def permission_env(mode: str, rules: dict[str, Any] | str) -> tuple[dict[str, str] | None, list[str]]:
    """``(OPENCODE_PERMISSION for this mode, the tools whose rule stays the
    user's own)``. NEVER CHANGES a rule of the user's (v1.420.0, C1.10; it
    used to only avoid WIDENING one): a tool the user names — exactly or by a
    wildcard — is left out (OpenCode puts our keys after theirs and the last
    match wins, so a key of ours would override theirs, a ``"bash": "allow"``
    as much as a ``"bash": "deny"``), and ``"*"`` is used only when every rule
    of theirs already says exactly what ``"*"`` would. A ``"*"`` of the user's
    own names every tool: then nothing is named (``{}``) but Plan's refusal.
    ``task`` (a helper of OpenCode's) runs without a card in Ask first and
    Auto edits, as Iron Jarvis's own helpers do: the helper's own steps still
    ask. ``None``: the user has ONE rule for everything — it decides alone."""
    if isinstance(rules, str):
        return None, ["*"]
    posture, agent = MODES.get(mode, MODES["ask"])
    star = _POSTURE_STAR[posture]
    out: dict[str, Any] = {}
    # (Plan with an ``edit`` rule of the user's no longer keeps "*" out: the
    # refusal below is appended after every rule, ours and theirs.)
    if all(_equal_to(v, _RANK[star]) for v in rules.values()):
        out["*"] = star
    else:
        for name in ACTING_TOOLS:
            if not _users(name, rules):
                out[name] = star
    if posture != "full":
        for name in READ_ONLY_TOOLS:
            if not _users(name, rules):
                out[name] = dict(READ_RULE) if name == "read" else "allow"
        if posture == "edits" and not _users("edit", rules):
            out["edit"] = "allow"
        if agent != "plan" and not _users("task", rules):
            # C1.10: a helper starts without a card. OpenCode gives the helper
            # only the parent's folder rules and refusals, so each of ITS steps
            # still meets this mode's rules (and asks).
            out["task"] = "allow"
    if agent == "plan":
        # v1.413.0 review: OpenCode puts the config's rules (and so these)
        # AFTER its plan agent's own ``edit`` deny, so a ``"*": "ask"`` here
        # turned Plan's refusal into a card. The refusal is the LAST rule
        # naming ``edit``: a key the user does not have, so it is appended
        # after all of theirs (C1.10: a user's ``"edit"`` key followed by a
        # ``"*": "allow"`` of theirs used to undo it).
        out.pop("edit", None)
        out[_fresh_key("edit", rules)] = "deny"
    kept = sorted(k for k in rules if not (agent == "plan" and _wild("edit", k) and k != "*"))
    if "*" in out:
        # Every rule of theirs says exactly what our "*" says: nothing of
        # theirs changes, and none is "kept" in a way that differs.
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
    if perm:
        # v1.420.0 review: OpenCode reads ONE ``OPENCODE_PERMISSION``, so an
        # object of the user's own already there is kept, in its place, with
        # ours after it (``user_rules`` read it: no key of ours names a tool of
        # theirs). Replacing it dropped their rules (a deny of theirs fell to
        # OpenCode's default allow).
        try:
            theirs = json.loads(str(env.get("OPENCODE_PERMISSION") or "").strip() or "null")
        except ValueError:
            theirs = None
        if isinstance(theirs, dict):
            perm = {**theirs, **perm}
        out["OPENCODE_PERMISSION"] = json.dumps(perm, separators=(",", ":"))
    if "*" in kept:
        # A "*" of the user's own decides for every tool (Plan still refuses edits).
        plan = MODES.get(mode, MODES["ask"])[1] == "plan"
        return out, (W.OPENCODE_RULES_DECIDE_PLAN if plan else W.OPENCODE_RULES_DECIDE)
    if kept:
        return out, W.OPENCODE_RULES_KEPT.format(tools=", ".join(kept[:12]))
    if "*" not in perm:
        return out, W.OPENCODE_UNNAMED_UNASKED
    return out, ""


# --------------------------------------------------------------------------- #
# The tools' words
# --------------------------------------------------------------------------- #
def failure_event(said: str) -> dict[str, Any]:
    """The ``error`` event for a failure OpenCode SAID. Its sign-in is its own
    (``opencode auth login``) and it has no accounts to move between."""
    text = str(said or "")
    if _SIGN_IN_RX.search(text):
        return W.error("OpenCode isn't signed in for this model", SIGN_IN_FIX, text, fix_kind="sign_in")
    if _LIMIT_RX.search(text):
        return W.error("OpenCode's model hit its usage limit", LIMIT_FIX, text)
    return W.error("OpenCode's model answered with an error", OTHER_FIX, text)




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
    return grouped(out)


def model_group(model_id: Any) -> str:
    """v1.439.0: a model's provider, as OpenCode's own picker groups it (the
    part of ``provider/model`` before the ``/``); "" when it names none."""
    head, sep, _rest = str(model_id or "").partition("/")
    return clip(head, 80) if sep and head else ""


def grouped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """v1.439.0: each row with its ``group`` (its provider), sorted by it —
    stable, so OpenCode's own order stays inside each provider."""
    out = []
    for r in rows:
        row = dict(r)
        group = model_group(row.get("id"))
        if group:
            row["group"] = group
        else:
            row.pop("group", None)
        out.append(row)
    out.sort(key=lambda r: str(r.get("group") or "").lower())
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
            return grouped(out[:MAX_MODELS]), clip(default, 120)
    except (OSError, ValueError):
        pass
    return [], ""


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
class OpenCodeDriver(AcpDriver):
    harness = HARNESS
    LABEL = "OpenCode"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        #: Where the service keeps the model list (set before ``open``).
        self.catalog_path: Path | None = None
        #: A notice to say once the session is open (a rule of the user's own).
        self.rules_note = ""

    def failure_event(self, said: str) -> dict[str, Any]:
        return failure_event(said)

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        # The folder is the process's own working folder (never an argument:
        # npm's opencode.cmd runs through cmd.exe, which reads & and % in one).
        # Never --pure (it switches the user's own plugins off).
        # v1.413.0 review: OpenCode runs an HTTP server inside this process,
        # and with no flag the user's global ``server.hostname`` /
        # ``server.port`` / ``server.mdns`` decide (measured: "0.0.0.0"
        # listened on every interface, unauthenticated). Explicit flags win
        # over the config: this PC only, a port the system hands out.
        return list(ACP_ARGV)

    # -- the session --------------------------------------------------------------
    async def open(self, *, session_id: str, resume: bool) -> None:
        caps = await self.initialize()
        # v1.414.0: Iron Jarvis's own tools for THIS session (``hosted/
        # jarvis_tools``), given the ACP way — only to an OpenCode that says
        # it takes an HTTP MCP server (``mcpCapabilities.http``).
        servers = self.acp_servers(caps)
        if resume and session_id:
            if not self.can_load:
                raise ControlRefused("this OpenCode cannot reopen a saved session")
            self._loading = True
            try:
                res = await self.request("session/load", {"sessionId": session_id, "cwd": self.cwd,
                                                          "mcpServers": servers})
            finally:
                self._loading = False
            sid = session_id
        else:
            res = await self.request("session/new", {"cwd": self.cwd, "mcpServers": servers})
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

    def on_config_update(self, options: Any) -> None:
        self._take_options(options)

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

    def note_answered(self, ref: str, stop: str, error: BaseException | None) -> None:
        if error is None and stop != "cancelled":
            self.note_read(ref)
        elif error is None and self.steers.get(ref) == "sent":
            # Stopped before OpenCode's loop came to it: the note is in the
            # conversation all the same (OpenCode keeps every prompt it
            # took), so it is never handed back to be sent twice.
            self.steers[ref] = "read"
            self.emit(make("notice", text=W.OPENCODE_NOTE_KEPT))

    async def set_mode(self, mode: str) -> str:
        """Every mode is the process's own environment — the permission posture
        and (v1.413.0 review) Plan's edit refusal (``permission_env``) — so any
        change is a NEW process before the next message (``restart_needed``),
        where ``open`` sets the agent. Nothing switches live: a running reply
        goes on under the mode it started with, as the notice says (switching
        only the agent live put OpenCode's plan agent on the old process's
        rules, where an edit was a card, not a refusal)."""
        if MODES.get(mode, MODES["ask"]) != MODES.get(self.mode, MODES["ask"]):
            self.restart_needed = True
        self.mode = mode
        return "next" if self.restart_needed or self.turn_open else "now"

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

    # -- "Allow for this session", across a new process (v1.420.0, C1.10) ---------
    def _always_key(self) -> str:
        """The conversation's key in :data:`SESSION_ALWAYS`, scoped to the Iron
        Jarvis home that started this OpenCode (v1.420.0 review: the map is the
        whole process's, and a conversation of another home — the test suite
        starts one app per test on the same recorded ``ses_`` ids — took the
        answer and never showed its own card)."""
        if not self.session_id:
            return ""
        home = str(Path(self.catalog_path).parent) if self.catalog_path is not None else ""
        return f"{home}\x00{self.session_id}" if home else self.session_id

    def answered(self, ask: Ask, decision: str) -> None:
        if decision == "allow_session":
            remember_always(self._always_key(), ask.tool, str(ask.event.get("command") or ""))

    def _ask(self, rid: Any, p: dict[str, Any]) -> None:
        """A question the user already answered "Allow for this session" in
        this conversation — before a mode change started a new OpenCode — is
        answered the same way here (OpenCode's own "always", which then covers
        the rest of this process exactly as before). Anything else is a card."""
        tc = p.get("toolCall") if isinstance(p.get("toolCall"), dict) else {}
        known = self._tools.get(str(tc.get("toolCallId") or ""))
        # The tool's NAME comes only from its first record: a request with
        # none (its title is the command) is never matched.
        name = known[0] if known else ""
        args = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) and tc.get("rawInput") else (
            known[2] if known else {})
        command = str(args.get("command") or "") if isinstance(args, dict) else ""
        offered = [o for o in p.get("options") or [] if isinstance(o, dict) and o.get("optionId")]
        pick = self.session_option(offered)
        if name and pick is not None and always_covers(self._always_key(), name, command):
            asyncio.ensure_future(self.write({"jsonrpc": "2.0", "id": rid, "result": {
                "outcome": {"outcome": "selected", "optionId": pick.get("optionId")}}}))
            if not self._always_said:
                self._always_said = True
                self.emit(make("notice", text=W.OPENCODE_ALWAYS_CARRIED))
            return
        super()._ask(rid, p)

    #: The notice that an earlier answer still applies is said once a process.
    _always_said = False

    # -- OpenCode's side (ON THE LOOP) ---------------------------------------------
    def on_usage_update(self, u: dict[str, Any]) -> None:
        from . import seen as _seen  # v1.414.0: how full, as OpenCode says

        _seen.note_context(self, _seen.opencode_context(u))
        cost = u.get("cost") if isinstance(u.get("cost"), dict) else {}
        amount = cost.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            self._cost = float(amount)
