"""Pi in the Code view (v1.411.0): ``pi --mode rpc`` (JSON lines both ways),
with ALL of its own tools, extensions, skills, prompt templates, AGENTS.md /
CLAUDE.md and settings on — the same Pi the user runs in a terminal. Iron
Jarvis only starts it, draws what it does, relays messages, and turns every
question Pi or one of the user's extensions asks into a card.

MEASURED on this PC (pi 1.3.0, live, in scratch folders; the recordings are
``tests/fixtures/build_app_v1411``):

* Records: a command ``{"id", "type", …}`` on stdin is answered by ONE
  ``{"id", "type": "response", "command", "success", "data"|"error"}``; the
  session's events (``agent_start``, ``turn_start``, ``message_start`` /
  ``message_update`` / ``message_end``, ``tool_execution_start`` /
  ``_update`` / ``_end``, ``turn_end``, ``agent_end``, ``agent_settled``,
  ``auto_retry_*``, ``compaction_*``, ``thinking_level_changed`` …) carry no
  id. Stdout is JSON only; Pi's own warnings go to stderr.
* A ``prompt`` is answered at once with ``data.disposition``: ``started`` (a
  run follows; ``agent_settled`` ends it — ``agent_end`` alone does not, a
  retry or a queued follow-up can still come), ``handled`` (an extension
  command such as ``/plan`` ran; no run may follow), ``queued``. A prompt
  while a run is going is REFUSED (``"Agent is already processing…"``), so a
  message during a turn is refused here too, as for the other two tools.
* A provider that answers with an error is NOT a failed response: the
  assistant message ends with ``stopReason: "error"`` and ``errorMessage``
  (measured: a LiteLLM gateway's 400, "The 'gpt-5.5' model is not supported
  when using Codex with a ChatGPT account"), then ``agent_settled``. A
  provider with no sign-in is a FAILED prompt response: ``"No API key found
  for anthropic. Use /login to log into a provider via OAuth or API key."``.
* ``abort`` stops the run: the assistant message ends ``stopReason:
  "aborted"``, then ``agent_settled`` with ``aborted: true``, then the abort's
  own response. The process stays up and takes the next message.
* PERMISSIONS: Pi asks NOTHING before running a tool — that is its own rule
  (the user chose "the tool's own rules, shown as Iron Jarvis cards"). Only an
  extension can ask, through ``extension_ui_request``: ``confirm`` (answered
  ``{"confirmed": true|false}``), ``select`` / ``input`` / ``editor``
  (``{"value": …}``), or ``{"cancelled": true}``. A ``tool_call`` hook's
  confirm arrives AFTER that tool's ``tool_execution_start`` (measured with a
  gate extension: start → confirm → end), so a confirm while ONE tool runs
  is drawn as an approval OF THAT TOOL (its title, command, path, diff); with
  several running (Pi's parallel tool mode starts them all first) nobody can
  tell which, and the card says the dialog's own words. The
  user's own extensions here ask through ``select`` / ``editor`` (plan mode's
  "what next?") and ``confirm`` (subagent's "Run project-local agents?");
  none gates tools by default. ``notify`` becomes a ``notice``; ``setStatus``
  / ``setWidget`` / ``setTitle`` / ``set_editor_text`` are a terminal's
  furniture and are not drawn.
* So the composer offers ONE mode, "Pi's own rules"; the other four are
  ``HIDDEN_MODES`` with the reason. Pi's plan mode is an EXTENSION (``--plan``
  / ``/plan``) whose state the extension restores from the session file and
  turns off by itself when the user runs the plan, so no chip could stay in
  step with it: ``/plan`` stays in the ``/`` list instead.
* Sessions: ``--session-id <id>`` opens that project session and creates it
  when missing (stderr: "No project session found with id …; creating a new
  session with that id"), so the same argv starts AND resumes; ids are
  UUID-shaped (Pi mints UUIDv7). Files live in
  ``~/.pi/agent/sessions/--<folder>--/<timestamp>_<id>.jsonl`` by default,
  but FLAT in a ``sessionDir`` (the setting, a project's own
  ``.pi/settings.json`` over the global one, or
  ``PI_CODING_AGENT_SESSION_DIR``) — header line ``{"type": "session", "id",
  "cwd", "timestamp"}``. In a terminal ``pi --session <id>`` opens it.
* RUNS belong to a message: the one whose prompt is waiting when
  ``agent_start`` comes owns it, and only its ``agent_settled`` ends that
  turn. A run an extension starts by itself (or after the handled grace) is
  nobody's: the session stays busy until it settles.
* ``set_model {provider, modelId}`` applies live (``"Model not found: x/y"``
  refused); ``set_thinking_level`` applies live (``thinking_level_changed``);
  a model without reasoning answers success and stays ``off``.
* Sign-in is Pi's OWN, per provider (``~/.pi/agent/auth.json``, ``/login``):
  Iron-Proxy's accounts are Claude Code / Codex / Grok homes and do not apply.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
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

HARNESS = "pi"

#: The ONE preset Pi has: its own rules (no flag — Pi asks nothing itself).
OWN = "own"
MODES: dict[str, str] = {OWN: ""}
MODE_LABELS: dict[str, str] = {OWN: "Pi's own rules"}
#: The presets Pi does not have, and why (said in the mode menu).
HIDDEN_MODES: dict[str, str] = {
    "ask": "Pi does not ask before it runs a tool. Only an extension of yours can ask, "
           "and its question comes here as a card.",
    "edits": "Pi has no setting that lets edits run while commands still ask.",
    "plan": "Pi's plan mode comes from an extension that switches itself off when you run the plan, "
            "so this menu could not stay in step with it. If you have it, type /plan instead.",
    "full": "Pi already runs every tool without asking (Pi's own rules).",
}

#: Pi's thinking levels (``--thinking`` / ``set_thinking_level``).
EFFORTS: tuple[str, ...] = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

#: How long a command (get_state, set_model …) may take.
REQUEST_TIMEOUT_S = 30.0
#: After a ``handled`` prompt (an extension command), how long a run it
#: starts by itself has to begin before the turn ends.
HANDLED_GRACE_S = 2.0
#: A running command's output is redrawn at most this often.
UPDATE_EVERY_S = 1.0
#: Pictures sent with a message (Pi takes base64 images).
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 8
#: The model list the composer offers (what a Pi process last said).
MAX_MODELS = 200

#: The models a Pi process last listed (``get_available_models``): the
#: harnesses listing shows them without starting anything. Kept on disk by
#: the service (``pi_models.json``) so a restart still has them.
LAST_MODELS: list[dict[str, str]] = []

#: A parent Pi session's markers (Pi sets them for the commands its own
#: shell tools run): a daemon started from inside Pi must not hand them on.
SESSION_MARKERS: tuple[str, ...] = (
    "PI_SESSION_ID", "PI_SESSION_FILE", "PI_CODING_AGENT", "PI_PROVIDER", "PI_MODEL", "PI_REASONING_LEVEL",
)

_SID_TAIL = re.compile(r"_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")

_COMMAND_TOOLS = {"bash", "powershell", "shell"}
_READ_TOOLS = {"read", "ls"}
_SEARCH_TOOLS = {"grep", "find", "glob"}
_WEB_TOOLS = {"web_search", "fetch_content", "source_check", "get_search_content", "web_fetch", "fetch"}
_TASK_TOOLS = {"subagent", "task", "agent"}
_TODO_TOOLS = {"todo", "todos", "todo_write"}

_SIGN_IN_RX = re.compile(r"no api key found|use /login|not logged in|\b401\b|unauthori[sz]ed|"
                         r"invalid api key|authentication (?:failed|error)|oauth.*(?:expired|refresh)", re.I)
_LIMIT_RX = re.compile(r"usage limit|rate limit|\b429\b|quota|too many requests", re.I)
#: Pi's refusal of a prompt while a run is going (agent-session.js).
_BUSY_RX = re.compile(r"already processing", re.I)
#: How many of a select's choices a card shows.
MAX_CHOICES = 20
SIGN_IN_FIX = ("Open Pi in a terminal and type /login to sign in to that model's provider, "
               "or choose another model.")


class PiStillWorking(ControlRefused):
    """A message while Pi runs something no message owns (said in words)."""


def _choice_label(option: Any) -> str:
    """A select choice as the card shows it (and the answer comes back)."""
    return clip(option, 120)
LIMIT_FIX = "Wait until it resets, or choose another model."
OTHER_FIX = "Send the message again. If it happens again, choose another model for this session."


def child_env(env: dict[str, str]) -> dict[str, str]:
    """A Build terminal's environment without a parent Pi session's markers."""
    upper = {m.upper() for m in SESSION_MARKERS}
    return {k: v for k, v in env.items() if k.upper() not in upper}


# --------------------------------------------------------------------------- #
# Where Pi keeps things (read-only)
# --------------------------------------------------------------------------- #
def agent_dir() -> Path:
    """Pi's config folder: ``PI_CODING_AGENT_DIR``, else ``~/.pi/agent``."""
    env = (os.environ.get("PI_CODING_AGENT_DIR") or "").strip()
    return Path(os.path.expanduser(env)) if env else Path.home() / ".pi" / "agent"


def _setting_session_dir(path: Path, base: Path) -> Path | None:
    """The ``sessionDir`` one Pi settings file names (relative to ``base``,
    as Pi resolves it against the folder it runs in), else None."""
    try:
        if path.is_file() and path.stat().st_size < 1_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("sessionDir") if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                got = Path(os.path.expanduser(value.strip()))
                return got if got.is_absolute() else base / got
    except (OSError, ValueError):
        pass
    return None


def sessions_dir(folder: str | os.PathLike[str] = "") -> Path:
    """Where Pi saves sessions for a process started in ``folder`` — Pi's own
    order (main.js): ``PI_CODING_AGENT_SESSION_DIR``, else the ``sessionDir``
    setting with the PROJECT's ``<folder>/.pi/settings.json`` over the global
    one, else ``<agent dir>/sessions``. BLOCKING (reads settings, bounded)."""
    env = (os.environ.get("PI_CODING_AGENT_SESSION_DIR") or "").strip()
    if env:
        return Path(os.path.expanduser(env))
    base = agent_dir()
    if folder:
        got = _setting_session_dir(Path(folder) / ".pi" / "settings.json", Path(folder))
        if got is not None:
            return got
    got = _setting_session_dir(base / "settings.json", Path(folder) if folder else Path.cwd())
    return got if got is not None else base / "sessions"


def session_id_of(path: Path) -> str:
    """The session id a file's NAME carries (``<timestamp>_<id>.jsonl``), or ""."""
    m = _SID_TAIL.search(path.stem)
    return m.group(1) if m else ""


def conversation_files(root: Path) -> list[Path]:
    """BLOCKING: Pi's session files under ``root``, in BOTH of Pi's layouts:
    the default ``~/.pi/agent/sessions`` keeps one ``--<folder>--`` subfolder
    per project, while a ``sessionDir`` (setting or
    ``PI_CODING_AGENT_SESSION_DIR``) holds them FLAT (session-manager.js
    ``create``/``findById``) — a flat file is matched to its folder by its
    header's ``cwd`` (:func:`summarize`)."""
    try:
        return [p for pattern in ("*.jsonl", "*/*.jsonl") for p in root.glob(pattern) if session_id_of(p)]
    except OSError:
        return []


def find_session_file(root: Path, hid: str) -> Path | None:
    """BLOCKING: the file holding Pi session ``hid`` under ``root`` (matched
    by NAME against the listing, never joined into a path), else None."""
    for p in conversation_files(root):
        if session_id_of(p) == hid:
            return p
    return None


def has_messages(path: Path | None) -> bool:
    """BLOCKING: does a Pi session file hold a message (then Pi, reopening
    it, restores its last model and thinking level — core/sdk.js)?"""
    if path is None:
        return False
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("type") == "message":
                    return True
    except OSError:
        return False
    return False


#: How much of a session file the summary reads from each end.
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 64 * 1024


def _iso(value: Any) -> str:
    from ..history.common import iso

    return iso(value) or ""


def _user_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def summarize(path: Path) -> dict[str, Any] | None:
    """BLOCKING: one Pi session file's ``{id, project, title, started, ended}``
    from its head and tail (read-only; ``None`` when it cannot be read). The
    title is the session's own name when it has one, else its first message."""
    from ..history.common import parse_line, title_of

    try:
        st = path.stat()
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
            tail = b""
            if st.st_size > HEAD_BYTES:
                fh.seek(max(HEAD_BYTES, st.st_size - TAIL_BYTES))
                tail = fh.read()
    except OSError:
        return None
    rows = [r for r in (parse_line(x) for x in head.splitlines()) if r]
    if not rows or rows[0].get("type") != "session":
        return None
    header = rows[0]
    sid = str(header.get("id") or "") or session_id_of(path)
    if not sid or sid != session_id_of(path):
        return None
    name, first, last_at = "", "", ""
    for r in rows[1:] + [x for x in (parse_line(y) for y in tail.splitlines()[1:]) if x]:
        if r.get("timestamp"):
            last_at = str(r.get("timestamp"))
        if r.get("type") == "session_info" and isinstance(r.get("name"), str):
            name = r["name"]
        if not first and r.get("type") == "message":
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            if msg.get("role") == "user":
                first = _user_text(msg)
    return {
        "harness": HARNESS,
        "id": sid,
        "project": str(header.get("cwd") or ""),
        "title": title_of(name or first),
        "started": _iso(header.get("timestamp")) or _iso(st.st_mtime),
        "ended": _iso(last_at) or _iso(st.st_mtime),
    }


# --------------------------------------------------------------------------- #
# The tools' words
# --------------------------------------------------------------------------- #
def tool_kind(name: str) -> str:
    n = str(name or "").lower()
    if n in _COMMAND_TOOLS:
        return "command"
    if n == "edit":
        return "edit"
    if n == "write":
        return "write"
    if n in _READ_TOOLS:
        return "read"
    if n in _SEARCH_TOOLS:
        return "search"
    if n in _WEB_TOOLS:
        return "web"
    if n.startswith("mcp__") or n.startswith("mcp_"):
        return "mcp"
    if n in _TASK_TOOLS:
        return "task"
    if n in _TODO_TOOLS:
        return "todo"
    return "other"


def _base(path: Any) -> str:
    return os.path.basename(str(path or "").replace("\\", "/")) or str(path or "")


def tool_title(name: str, args: dict[str, Any]) -> str:
    """One line, plain words."""
    kind = tool_kind(name)
    path = args.get("path") or args.get("file_path") or ""
    if kind == "command":
        cmd = one_line(args.get("command"), 90)
        return f"Run {cmd}" if cmd else "Run a command"
    if kind == "edit":
        return f"Edit {_base(path)}" if path else "Edit a file"
    if kind == "write":
        return f"Write {_base(path)}" if path else "Write a file"
    if kind == "read":
        if str(name).lower() == "ls":
            return f"List {_base(path)}" if path else "List the folder"
        return f"Read {_base(path)}" if path else "Read a file"
    if kind == "search":
        pat = one_line(args.get("pattern") or args.get("glob") or args.get("query"), 60)
        return f"Search for {pat}" if pat else "Search the files"
    if kind == "web":
        url = one_line(args.get("url"), 90)
        if url:
            return f"Fetch {url}"
        qs = args.get("queries")
        q = one_line(qs[0] if isinstance(qs, list) and qs else args.get("query"), 80)
        return f"Search the web for {q}" if q else "Search the web"
    if kind == "mcp":
        parts = str(name).split("__")
        return f"Use {parts[1]} › {'__'.join(parts[2:])}" if len(parts) >= 3 else f"Use {name}"
    if kind == "task":
        return "Hand a task to a helper"
    if kind == "todo":
        return "Update the plan"
    if str(name).lower() == "codemode":
        return "Run code that uses other tools"
    if str(name).lower() == "tool_search":
        return "Look for more tools"
    return f"Use {name}" if name else "Use a tool"


def _input_line(args: Any) -> str:
    try:
        return clip(json.dumps(args, ensure_ascii=False, sort_keys=True), INPUT_CHARS)
    except (TypeError, ValueError):
        return ""


def edit_diff(name: str, args: dict[str, Any]) -> str:
    """The change a write / edit asks for, as a unified diff (before it ran)."""
    path = str(args.get("path") or "")
    if str(name).lower() == "write":
        return unified_new_file(path, str(args.get("content") or ""))
    if str(name).lower() == "edit":
        edits = args.get("edits")
        if not isinstance(edits, list):
            edits = [{"oldText": args.get("oldText") or args.get("old_string"),
                      "newText": args.get("newText") or args.get("new_string")}]
        parts = [
            unified_replace(path, str(e.get("oldText") or ""), str(e.get("newText") or ""))
            for e in edits if isinstance(e, dict)
        ]
        return "\n".join(p for p in parts if p)
    return ""


def result_text(result: Any) -> str:
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            return "\n".join(
                str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str):
            return content
    if isinstance(result, str):
        return result
    return ""


def result_patch(result: Any) -> str:
    """The unified diff Pi's own edit tool reports (``details.patch``)."""
    details = result.get("details") if isinstance(result, dict) else None
    patch = details.get("patch") if isinstance(details, dict) else None
    return str(patch) if isinstance(patch, str) else ""


def failure_event(said: str) -> dict[str, Any]:
    """The ``error`` event for a failure Pi SAID. Pi's sign-in is its own (per
    provider, ``/login``) and it has no accounts to move between, so neither
    gets the other tools' presses."""
    text = str(said or "")
    if _SIGN_IN_RX.search(text):
        return W.error("Pi isn't signed in for this model", SIGN_IN_FIX, text, fix_kind="sign_in")
    if _LIMIT_RX.search(text):
        return W.error("Pi's model hit its usage limit", LIMIT_FIX, text)
    return W.error("Pi's model answered with an error", OTHER_FIX, text)


def model_rows(models: Any) -> list[dict[str, str]]:
    """``get_available_models`` -> the composer's rows (``provider/id``)."""
    out: list[dict[str, str]] = []
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        provider, mid = str(m.get("provider") or "").strip(), str(m.get("id") or "").strip()
        if not provider or not mid:
            continue
        name = str(m.get("name") or mid).strip()
        row: dict[str, Any] = {"id": clip(f"{provider}/{mid}", 120), "label": clip(f"{name} ({provider})", 120)}
        if m.get("reasoning") is False:
            # v1.412.0 review: Pi says this model has no reasoning — it takes
            # no thinking level (the effort chip hides for it).
            row["efforts"] = []
        out.append(row)
        if len(out) >= MAX_MODELS:
            break
    return out


def split_model(model: str) -> tuple[str, str]:
    """``provider/id`` -> (provider, id); an id with no provider -> ("", id)."""
    text = str(model or "").strip()
    if "/" in text:
        provider, mid = text.split("/", 1)
        return provider.strip(), mid.strip()
    return "", text


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
class PiDriver(Driver):
    harness = HARNESS

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._replies = ControlWaiter()
        self._n = 0
        self._interrupting = False
        self._errored = False
        #: The assistant message the stream is in (one per ``message_start``).
        self._msg = 0
        self._tag = uuid.uuid4().hex[:8]
        #: toolCallId -> (name, args), for a tool that started and has not ended.
        self._running: dict[str, tuple[str, dict[str, Any]]] = {}
        self._updated: dict[str, float] = {}
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._cost = 0.0
        self._last_error = ""
        self._retry_said = False
        self._waiting_run = False
        self._handled: asyncio.TimerHandle | None = None
        #: The message the user sent last (each ``send_user`` counts one).
        self._turn_n = 0
        #: A Pi run is going (``agent_start`` .. ``agent_settled``), and the
        #: message it belongs to (None = no message: an extension started it
        #: by itself, or it began after its message's turn had ended).
        self._run_open = False
        self._run_owner: int | None = None
        #: Pi's dialog id -> Iron Jarvis's ask id (a dialog Pi timed out goes).
        self._dialogs: dict[str, str] = {}
        #: The dialogs raised while a run was going: Pi resolves them by itself
        #: when that run ends or is aborted (rpc-mode.js, the dialog's signal).
        self._run_dialogs: set[str] = set()
        #: The service's way to close a card nobody can answer any more.
        self.forget_ask: Any = None
        #: Where the service keeps the model list (set before ``open``).
        self.catalog_path: Path | None = None

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        out = ["--mode", "rpc", "--session-id", session_id]
        if self.model:
            out += ["--model", self.model]
        if self.effort:
            out += ["--thinking", self.effort]
        return out

    # -- commands -----------------------------------------------------------------
    async def request(self, kind: str, **fields: Any) -> Any:
        """One command; its ``data`` — or ControlRefused with Pi's own words."""
        self._n += 1
        rid = f"ij-{kind}-{self._n}"
        fut = self._replies.make(rid)
        await self.write({"id": rid, "type": kind, **fields})
        return await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT_S)

    async def open(self, *, session_id: str, resume: bool) -> None:
        state = await self.request("get_state")
        state = state if isinstance(state, dict) else {}
        sid = str(state.get("sessionId") or session_id or "")
        if not sid:
            raise ControlRefused("Pi did not open a session")
        self.session_id = sid
        self.on_session_id(sid)
        model = state.get("model") if isinstance(state.get("model"), dict) else {}
        shown = f"{model.get('provider')}/{model.get('id')}" if model.get("id") else self.model
        self.emit(make("session", harness=HARNESS, harness_session_id=sid, model=clip(shown, 120),
                       cwd=clip(self.cwd, 500), mode=self.mode))
        # The `/` list and the model list: a failure here never fails a start.
        try:
            got = await self.request("get_commands")
            cmds = got.get("commands") if isinstance(got, dict) else None
            self.commands = [
                {"name": str(c.get("name") or ""), "description": clip(c.get("description") or "", 200)}
                for c in (cmds or []) if isinstance(c, dict) and c.get("name")
            ][:300]
            if self.commands:
                from .claude import LAST_COMMANDS

                LAST_COMMANDS[HARNESS] = list(self.commands)
        except Exception:  # noqa: BLE001
            log.debug("pi did not list its commands", exc_info=True)
        try:
            got = await self.request("get_available_models")
            rows = model_rows(got.get("models") if isinstance(got, dict) else None)
            if rows:
                LAST_MODELS[:] = rows
                if self.catalog_path is not None:
                    await asyncio.to_thread(save_models, self.catalog_path, rows)
        except Exception:  # noqa: BLE001
            log.debug("pi did not list its models", exc_info=True)

    # -- the user's side ----------------------------------------------------------
    @property
    def busy_alone(self) -> bool:
        """Pi is running something no open message owns (an extension started
        it, or it outlived its message): the session is busy, and a new
        message would be refused by Pi ("Agent is already processing")."""
        return self._run_open and (self._run_owner is None or not self.turn_open or self._run_owner != self._turn_n)

    async def send_user(self, text: str, files: list[str]) -> None:
        if self.busy_alone:
            raise PiStillWorking(W.PI_STILL_WORKING)
        images, others = await asyncio.to_thread(_read_images, files)
        body = str(text or "")
        if others:
            listed = "\n".join(others)
            body = f"{body}\n\nFiles I attached:\n{listed}" if body.strip() else f"Files I attached:\n{listed}"
        if self.busy_alone:  # it began while the files were read
            raise PiStillWorking(W.PI_STILL_WORKING)
        self._turn_n += 1
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        self._retry_said = False
        self._last_error = ""
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._cost = 0.0
        self._waiting_run = True
        msg: dict[str, Any] = {"message": body}
        if images:
            msg["images"] = images
        # The prompt's answer is read when it comes, never waited on here: an
        # extension command answers only after ITS dialog was answered (measured
        # with `/rpc-input`), which may take the user any time.
        self._n += 1
        rid = f"ij-prompt-{self._n}"
        fut = self._replies.make(rid)
        fut.add_done_callback(self._prompt_answered)
        await self.write({"id": rid, "type": "prompt", **msg})

    def _prompt_answered(self, fut: asyncio.Future) -> None:
        if fut.cancelled() or not self.turn_open:
            return
        exc = fut.exception()
        if isinstance(exc, ControlRefused):
            # Refused before it was accepted (a provider with no sign-in):
            # nothing ran, the turn ends here.
            self._waiting_run = False
            if _BUSY_RX.search(str(exc)):
                # Pi was already running something an extension started: our
                # own words, never "the model answered with an error".
                if not self._errored:
                    self._errored = True
                    self.emit(W.error(W.PI_STILL_WORKING_TITLE, W.PI_STILL_WORKING_FIX, str(exc)))
            else:
                self._fail(str(exc))
            self._end("failed")
            return
        if exc is not None:
            return  # the process went: the service ends the turn
        got = fut.result()
        disposition = str(got.get("disposition") or "started") if isinstance(got, dict) else "started"
        if disposition == "handled" and self._waiting_run:
            # An extension command ran (`/plan`, `/goal` …): it may start a
            # run of its own; if none begins, the turn is over.
            loop = asyncio.get_running_loop()
            self._handled = loop.call_later(HANDLED_GRACE_S, self._handled_without_run)

    def _handled_without_run(self) -> None:
        self._handled = None
        if self.turn_open and self._waiting_run:
            self._waiting_run = False
            # A Stop on an extension command's dialog: the command ended
            # because of it, so the turn was interrupted, not completed.
            self._end("interrupted" if self._interrupting else "completed")

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        self._dialogs.pop(str(ask.native), None)
        self._run_dialogs.discard(str(ask.native))

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        """The ``extension_ui_response`` for the user's answer — or AskRefused,
        before anything is sent."""
        if decision == "allow_session":
            raise AskRefused("Pi offers no session-wide allow — allow it once.")
        method = ask.data.get("method")
        out: dict[str, Any] = {"type": "extension_ui_response", "id": ask.native}
        if method == "confirm":
            out["confirmed"] = decision == "allow"
            return out
        if decision == "deny":
            out["cancelled"] = True
            return out
        value = _answer_value(ask, answers)
        if value is None:
            raise AskRefused("Choose an answer for the question first.")
        options = [str(o) for o in ask.data.get("options") or []]
        if method == "select" and options:
            # A select takes one of ITS choices only (the extension compares
            # the value with them); typed words are refused before Pi sees them.
            # The card shows each choice as `_choice_label` drew it.
            shown = [_choice_label(o) for o in options[:MAX_CHOICES]]
            if value in shown:
                value = options[shown.index(value)]
            elif value not in options:
                raise AskRefused("Choose one of the answers Pi offered.")
        out["value"] = value
        return out

    async def interrupt(self) -> None:
        self._interrupting = True
        try:
            await self.request("abort")
        except (asyncio.TimeoutError, ControlRefused):
            pass

    async def _abort_again(self) -> None:
        try:
            await self.request("abort")
        except Exception:  # noqa: BLE001 — a gone process ends the turn anyway
            pass

    async def set_mode(self, mode: str) -> str:
        self.mode = mode
        return "now"

    async def set_model(self, model: str) -> str:
        provider, mid = split_model(model)
        if not provider or not mid:
            raise ControlRefused("Name the model as provider/model, the way Pi lists it.")
        await self.request("set_model", provider=provider, modelId=mid)
        self.model = model
        return "now"

    async def set_effort(self, effort: str) -> str:
        if effort:
            await self.request("set_thinking_level", level=effort)
        self.effort = effort
        return "now" if effort else "next"

    def on_exit(self) -> None:
        from .process import HarnessGone

        if self._handled is not None:
            self._handled.cancel()
            self._handled = None
        self._replies.fail_all(HarnessGone("Pi stopped"))

    # -- Pi's side (ON THE LOOP) ----------------------------------------------------
    def on_message(self, obj: dict[str, Any]) -> None:
        kind = obj.get("type")
        if kind == "response":
            rid = obj.get("id")
            if obj.get("success") is False:
                self._replies.settle(rid, error=ControlRefused(clip(obj.get("error") or "refused", 600)))
            else:
                self._replies.settle(rid, obj.get("data") if "data" in obj else {})
            return
        if kind == "extension_ui_request":
            self._on_ui(obj)
            return
        if kind == "agent_start":
            self._run_open = True
            if self.turn_open and self._waiting_run:
                # The run the user's message started (or its extension command).
                self._run_owner = self._turn_n
                self._waiting_run = False
                if self._handled is not None:
                    self._handled.cancel()
                    self._handled = None
                if self._interrupting:
                    # A Stop that came while Pi was still preparing the prompt
                    # (extension input handlers, sign-in check): Pi's abort
                    # stops an ACTIVE run only, so it was lost — say it again
                    # now that the run is active.
                    asyncio.ensure_future(self._abort_again())
            else:
                # Nobody's message: an extension started it by itself, or it
                # began after its message's turn had ended.
                self._run_owner = None
            return
        if kind == "message_start":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if msg.get("role") == "assistant":
                self._msg += 1
            return
        if kind == "message_update":
            self._on_update(obj.get("assistantMessageEvent") or {})
            return
        if kind == "message_end":
            self._on_message_end(obj.get("message") if isinstance(obj.get("message"), dict) else {})
            return
        if kind == "tool_execution_start":
            self._tool_started(obj)
            return
        if kind == "tool_execution_update":
            self._tool_update(obj)
            return
        if kind == "tool_execution_end":
            self._tool_ended(obj)
            return
        if kind == "agent_settled":
            self._settled(bool(obj.get("aborted")))
            return
        if kind == "auto_retry_start":
            if not self._retry_said:
                self._retry_said = True
                said = one_line(obj.get("errorMessage"), 80)
                self.emit(make("notice", text=f"Pi is trying again{f' ({said})' if said else ''}."))
            return
        if kind == "compaction_end" and obj.get("result"):
            self.emit(make("notice", text="Pi compacted the conversation to make room."))
            return
        if kind == "extension_error":
            where = _base(obj.get("extensionPath"))
            self.emit(make("notice", text=clip(f"A Pi extension failed ({where}): {one_line(obj.get('error'), 200)}",
                                               500)))
            return

    def _item(self, idx: Any) -> str:
        # Pi names no message id on the wire: this process's own tag keeps a
        # later process's first answer from joining an earlier turn's row.
        return f"{self._tag}-m{self._msg}:{idx if isinstance(idx, int) else 0}"

    def _on_update(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "text_delta" and ev.get("delta"):
            self.emit(make("text", item=self._item(ev.get("contentIndex")), delta=clip(ev["delta"], TEXT_CHARS)))
        elif t == "text_end" and str(ev.get("content") or "").strip():
            self.emit(make("text_done", item=self._item(ev.get("contentIndex")), text=clip(ev["content"], TEXT_CHARS)))
        elif t == "thinking_delta" and ev.get("delta"):
            self.emit(make("thinking", item=self._item(ev.get("contentIndex")), delta=clip(ev["delta"], TEXT_CHARS)))

    def _on_message_end(self, msg: dict[str, Any]) -> None:
        if msg.get("role") != "assistant":
            return
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        for ours, theirs in (("input", "input"), ("output", "output"), ("cache_read", "cacheRead")):
            v = usage.get(theirs)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._usage[ours] += int(v)
        cost = (usage.get("cost") or {}).get("total") if isinstance(usage.get("cost"), dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self._cost += float(cost)
        stop = msg.get("stopReason")
        if stop == "error":
            self._last_error = str(msg.get("errorMessage") or "Pi's model answered with an error")
        elif stop in ("stop", "toolUse"):
            self._last_error = ""
        elif stop == "length":
            self.emit(make("notice", text="The answer reached the model's output limit."))

    # -- tools --
    def _tool_started(self, obj: dict[str, Any]) -> None:
        tid = str(obj.get("toolCallId") or "")
        name = str(obj.get("toolName") or "")
        args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        self._running[tid] = (name, args)
        self.emit(self._tool_event(tid, name, args, "running"))

    def _tool_update(self, obj: dict[str, Any]) -> None:
        tid = str(obj.get("toolCallId") or "")
        name, args = self._running.get(tid) or (str(obj.get("toolName") or ""), {})
        out = result_text(obj.get("partialResult"))
        now = asyncio.get_running_loop().time()
        if now - self._updated.get(tid, -UPDATE_EVERY_S) < UPDATE_EVERY_S:
            return  # a command's running output: at most one row update a second
        if out.strip():
            self._updated[tid] = now
            ev = self._tool_event(tid, name, args, "running")
            ev["output"] = clip(out[-OUTPUT_CHARS * 2:], OUTPUT_CHARS)
            self.emit(ev)

    def _tool_ended(self, obj: dict[str, Any]) -> None:
        tid = str(obj.get("toolCallId") or "")
        name, args = self._running.pop(tid, None) or (str(obj.get("toolName") or ""), {})
        self._updated.pop(tid, None)
        result = obj.get("result")
        failed = bool(obj.get("isError"))
        ev = self._tool_event(tid, name, args, "failed" if failed else "done")
        ev["output"] = clip(result_text(result), OUTPUT_CHARS)
        patch = result_patch(result)
        if patch:
            ev["diff"] = clip(patch, DIFF_CHARS)
        self.emit(ev)

    def _tool_event(self, tid: str, name: str, args: dict[str, Any], status: str) -> dict[str, Any]:
        kind = tool_kind(name)
        ev = make("tool", item=tid, kind=kind, name=clip(name, 120), title=tool_title(name, args),
                  input=_input_line(args), status=status)
        path = args.get("path") or args.get("file_path")
        if path and kind in ("read", "edit", "write"):
            ev["path"] = clip(path, 500)
        offset = args.get("offset")
        if kind == "read" and isinstance(offset, int) and not isinstance(offset, bool) and offset > 0:
            ev["line"] = offset
        if kind in ("edit", "write") and status == "running":
            diff = edit_diff(name, args)
            if diff:
                ev["diff"] = clip(diff, DIFF_CHARS)
        return ev

    # -- questions --
    def _on_ui(self, obj: dict[str, Any]) -> None:
        method = str(obj.get("method") or "")
        native = str(obj.get("id") or "")
        if method == "notify":
            text = one_line(obj.get("message"), 400)
            if text:
                self.emit(make("notice", text=clip(f"Pi: {text}", 500)))
            return
        if method not in ("confirm", "select", "input", "editor") or not native:
            return  # setStatus / setWidget / setTitle / set_editor_text: a terminal's furniture
        ask_id = new_ask_id()
        title = one_line(obj.get("title"), 300)
        running = self._current_tool()
        if method == "confirm":
            detail = "\n".join(x for x in (str(obj.get("title") or ""), str(obj.get("message") or "")) if x.strip())
            if running is not None:
                tid, (name, args) = running
                kind = tool_kind(name)
                ev = make("approval", request_id=ask_id,
                          kind={"command": "command", "edit": "edit", "write": "edit", "web": "network"}.get(kind, "tool"),
                          title=tool_title(name, args), detail=clip(detail, DETAIL_CHARS), can_session=False,
                          tool=clip(name, 120))
                if kind == "command":
                    ev["command"] = clip(args.get("command") or "", 2000)
                if args.get("path"):
                    ev["path"] = clip(args.get("path"), 500)
                diff = edit_diff(name, args) if kind in ("edit", "write") else ""
                if diff:
                    ev["diff"] = clip(diff, DIFF_CHARS)
                tool = name or "extension"
            else:
                ev = make("approval", request_id=ask_id, kind="other", title=title,
                          detail=clip(obj.get("message") or "", DETAIL_CHARS), can_session=False)
                tool = "extension"
            # An extension's confirm asks every time (it offers no session-wide
            # allow): Enter never allows it, a click does.
            ev["once_only"] = True
            data = {"method": method}
        else:
            options = [o for o in obj.get("options") or [] if isinstance(o, str)] if method == "select" else []
            question = title or ("Choose one" if method == "select" else "Type an answer")
            row: dict[str, Any] = {
                "question": clip(question, 500),
                "options": [{"label": _choice_label(o)} for o in options[:MAX_CHOICES]],
                "multi": False,
            }
            if method == "select" and options:
                # One of Pi's choices only: the card offers no typed answer.
                row["free_text"] = False
            hint = obj.get("placeholder") if method == "input" else None
            if method == "editor" and str(obj.get("prefill") or "").strip():
                row["options"] = [{"label": "Keep the text as it is", "description": clip(obj.get("prefill"), 300)}]
            if isinstance(hint, str) and hint.strip():
                row["header"] = clip(hint, 60)
            ev = make("question", request_id=ask_id, questions=[row])
            tool = "question"
            data = {"method": method, "question": row["question"], "options": options,
                    "prefill": str(obj.get("prefill") or "")}
        self._dialogs[native] = ask_id
        if self._run_open:
            self._run_dialogs.add(native)
        self.raise_ask(Ask(ask_id, "approval" if method == "confirm" else "question", tool, native, data,
                           ev, ev["at"], False))
        timeout = obj.get("timeout")
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
            # Pi answers a timed-out dialog by itself: the card goes then.
            asyncio.get_running_loop().call_later(float(timeout) / 1000.0, self._dialog_timed_out, native)

    def _dialog_timed_out(self, native: str) -> None:
        self._run_dialogs.discard(native)
        ask_id = self._dialogs.pop(native, None)
        if ask_id and callable(self.forget_ask):
            self.forget_ask(ask_id)

    def _close_dialogs(self, *, every: bool) -> None:
        """Cards Pi stopped waiting for: the dialogs of a run that ended (Pi
        resolves them on the run's own abort signal), or — ``every`` — all of
        them (an aborted run). A card nobody can answer must not stay up."""
        for native in list(self._dialogs):
            if every or native in self._run_dialogs:
                self._dialog_timed_out(native)
        self._run_dialogs.clear()

    def _current_tool(self) -> tuple[str, tuple[str, dict[str, Any]]] | None:
        """The ONE tool that is running, else None. In Pi's parallel tool mode
        every ``tool_execution_start`` comes before any runs, so with several
        open nobody can tell which one a dialog is about."""
        if len(self._running) != 1:
            return None
        tid = next(iter(self._running))
        return tid, self._running[tid]

    # -- the end of a turn --
    def _fail(self, said: str) -> None:
        if self._errored:
            return
        self._errored = True
        self.emit(failure_event(said))

    def _settled(self, aborted: bool) -> None:
        was_open, owner = self._run_open, self._run_owner
        self._run_open, self._run_owner = False, None
        # Pi resolved the dialogs of the run that ended by itself.
        self._close_dialogs(every=aborted)
        if not self.turn_open:
            return
        if was_open and owner != self._turn_n:
            return  # a run no open message owns: it ends no turn
        self._waiting_run = False
        if self._interrupting or aborted:
            self._end("interrupted")
        elif self._last_error:
            self._fail(self._last_error)
            self._end("failed")
        else:
            self._end("completed")

    def _end(self, stop: str) -> None:
        if not self.turn_open:
            return
        self.turn_open = False
        self._interrupting = False
        self._running.clear()
        if self._handled is not None:
            self._handled.cancel()
            self._handled = None
        if any(self._usage.values()) or self._cost:
            # Pi prices every call from its own model table, whether the
            # provider is a subscription (OAuth) or a pay-per-use key, and the
            # RPC says neither: the cost is "what Pi reported" — never called
            # list price (it may be real spend) nor spend (it may not be).
            usage = make("usage", **self._usage, list_price=False)
            if self._cost:
                usage["cost_usd"] = round(self._cost, 6)
                usage["cost_reported_by"] = "Pi"
            self.emit(usage)
        self.emit(make("done", stop=stop))


def _answer_value(ask: Ask, answers: Any) -> str | None:
    """The one answer a select / input / editor takes (the card's answer keyed
    by its question, a list's first entry, or a plain string)."""
    q = str(ask.data.get("question") or "")
    got: Any = None
    if isinstance(answers, dict):
        got = answers.get(q, answers.get("0"))
        if got is None and len(answers) == 1:
            got = next(iter(answers.values()))
    elif isinstance(answers, list):
        got = answers[0] if answers else None
    elif isinstance(answers, str):
        got = answers
    if isinstance(got, list):
        got = got[0] if got else None
    if got is None or not str(got).strip():
        return None
    value = str(got)
    if ask.data.get("method") == "editor" and value == "Keep the text as it is":
        return str(ask.data.get("prefill") or "")
    return value


def _read_images(files: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """BLOCKING: pictures as Pi's base64 images (bounded); other files listed."""
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
# The model list kept on disk
# --------------------------------------------------------------------------- #
def save_models(path: Path, rows: list[dict[str, str]]) -> None:
    """BLOCKING: the model list a Pi process said, for the next listing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(json.dumps({"models": rows[:MAX_MODELS]}, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep Pi's model list", exc_info=True)


def cached_models(path: Path | None) -> list[dict[str, str]]:
    """BLOCKING-light: what a Pi process last listed (memory, else disk)."""
    if LAST_MODELS:
        return list(LAST_MODELS)
    if path is None:
        return []
    try:
        if path.is_file() and path.stat().st_size < 2_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("models") if isinstance(data, dict) else None
            out = []
            for r in (rows or []):
                if not (isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("id")):
                    continue
                row: dict[str, Any] = {"id": clip(r.get("id"), 120), "label": clip(r.get("label") or r.get("id"), 120)}
                if r.get("efforts") == []:
                    row["efforts"] = []
                out.append(row)
            return out[:MAX_MODELS]
    except (OSError, ValueError):
        pass
    return []
