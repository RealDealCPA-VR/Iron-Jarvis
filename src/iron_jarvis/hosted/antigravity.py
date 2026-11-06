"""Google Antigravity in the Code view (v1.439.0): ``agy acp`` — the Agent
Client Protocol (the JSON-RPC core OpenCode and Grok speak, :mod:`hosted.acp`)
— with ALL of Antigravity's own tools, skills, MCP servers and settings on.
The same Antigravity the user runs in a terminal; Iron Jarvis only starts it,
draws what it does, relays messages and turns every question Antigravity asks
into a card.

NOT AN IRON JARVIS PROVIDER: Antigravity is no row of
``providers/subscription_clis.py`` — it is no model provider Iron Jarvis calls
and no Iron-Proxy vendor (Iron-Proxy holds no Antigravity accounts). It signs
in by ITSELF (its own Google sign-in, ``agy`` in a terminal), like Pi and
OpenCode; an account named at create is refused in words.

MEASURED on this PC (agy 1.5.2, Google's own signed release, run only from a
scratch folder with a scratch ``USERPROFILE``/``HOME``, ``--disable-telemetry``,
every model call answered by a LOCAL stand-in Gemini API —
``GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:<port>`` — so nothing reached
Google and no sign-in was used; masked recordings in
``tests/fixtures/build_app_v1439``):

* ``agy acp [--dangerously-skip-permissions] [--log-file F]
  [--disable-telemetry]``: newline JSON-RPC on stdio, the folder is the
  process's working folder. With ``--log-file`` its own log goes THERE (none
  is written under ``~/.gemini/antigravity-cli/log``) — Iron Jarvis gives each
  session ``<home>/hosted/<sid>/agy-acp.log`` and reads a failure's reason from
  it (:func:`log_reason`).
* ``initialize`` answers ``agentCapabilities`` {loadSession,
  promptCapabilities {image: true}, sessionCapabilities {resume, close, load,
  list}} (no ``mcpCapabilities``: Iron Jarvis's own tools are not taken, said
  once) and ``authMethods`` (oauth-personal, oauth-business, gemini-api-key,
  agent-platform).
* SIGN-IN: at start the server tries a SILENT sign-in with the method it
  last used (its log: "Silent sign-in failed; session requests fail with
  auth_required until the client signs in"). With none ``session/new``
  answers ``{code: -32000, message: "Authentication required"}`` — said as
  "Antigravity isn't signed in": run ``agy`` in a terminal and sign in there.
  Even an API key in the environment (``GEMINI_API_KEY``) is not used until a
  client calls ``authenticate {methodId: "gemini-api-key"}`` — and Antigravity
  then KEEPS that choice (``cache/acp-auth-state.json``
  ``{"methodId": "gemini-api-key"}``, measured) for every later start. So Iron
  Jarvis NEVER calls ``authenticate``: choosing a sign-in method is the user's,
  in Antigravity's own screen.
* ``session/new`` {cwd, mcpServers} answers ``sessionId`` (a UUID),
  ``models`` {availableModels [{modelId "<base>-<level>"}], currentModelId},
  ``modes`` (default / accept-edits / plan) and ``configOptions`` [model
  (select of BASE ids), thought_level (select — PER MODEL: 3.1 Pro low/high,
  3.8 Flash low/medium/high), mode]. ``session/set_config_option {configId,
  value}`` answers ``{configOptions}`` (+ a ``config_option_update``) — a new
  model brings ITS levels. ``session/set_mode`` answers ``{}``; the mode
  applies from the next prompt (``current_mode_update`` comes with it).
  Reopened, a session is back on Antigravity's default model (measured on
  load and resume): Iron Jarvis sets the session's own model again.
* PERMISSIONS: a command asks ``session/request_permission`` with ONLY
  ``allow`` (allow_once) / ``deny`` (reject_once) — no "for this session"
  answer, so every card is Allow once / Deny. In Antigravity's default mode
  with no settings of the user's, ``write_file`` / ``edit_file`` ran WITHOUT a
  card, inside and outside the folder; only the user's own
  ``toolPermission: "strict"`` (Antigravity's settings) makes a write ask.
  Iron Jarvis never writes that file. Plan mode is NOT read-only (under strict
  it asked before a write and wrote after Allow).
* QUESTIONS: the model's ``ask_question`` arrives as ``elicitation/create``
  {mode: form, requestedSchema {properties {q1: {title, oneOf [{const,
  title}]}, q1_other: {title: "Other"}}}}; answered ``{action: "accept",
  content: {q1: "<const>"}}`` (the model read "A1: Red"), a typed answer as
  ``q1_other``, Skip as ``{action: "decline"}``. LEFT UNANSWERED (an error)
  Antigravity prints the question as text and ENDS the turn — so it is always
  answered (Stop answers decline).
* NOTES WHILE IT WORKS (differs from OpenCode): a second ``session/prompt``
  during a run makes the FIRST prompt answer ``{stopReason: end_turn}`` AT
  ONCE (no usage) while the turn goes on; only the LATEST prompt's answer
  ends the turn (with its usage). The note is read when Antigravity echoes it
  (``user_message_chunk``). ``session/cancel`` (a notification) makes the
  running prompt answer ``cancelled`` at once.
* FAILURES ARE TEXT: a failed model call is an ``agent_message_chunk``
  "\\n⚠ Agent execution terminated due to error.\\n" and then ``end_turn`` —
  drawn as a FAILED reply naming Antigravity, the reason read from the last
  error line of the session's own log (masked). A retry is
  "\\n⚠ API error (attempt N): …" — a notice, never reply text.
* The prompt's answer carries ``usage`` {inputTokens, outputTokens,
  cachedReadTokens, …} — no cost, no window. ``session_info_update {title}``
  names the conversation (used for a session that has no title).
* SAVED SESSIONS: ``~/.gemini/antigravity-cli/conversation_summaries.db``
  (SQLite, read here read-only): ``conversation_summaries`` (conversation_id,
  title, preview, last_modified_time, workspace_uris — a JSON list of
  ``file:///C:/...`` URIs —, nesting_depth: 0 = a top-level conversation).
  ``session/resume`` reopens one WITHOUT replay (``session/load`` replays it
  as updates first, hidden here). A terminal reopens one with
  ``agy --conversation <id>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import words as W
from .acp import AcpDriver, _config, _values, tool_kind as _acp_tool_kind
from .driver import Ask, AskRefused, ControlRefused, new_ask_id
from .events import (
    clip,
    make,
    one_line,
)

log = logging.getLogger(__name__)

HARNESS = "antigravity"
LABEL = "Antigravity"
#: The program (``agy.exe``; Google's installer puts it in
#: ``%LOCALAPPDATA%\agy\bin``).
PROGRAM = "agy"

#: Antigravity's own mode, shown as "Antigravity's own rules".
OWN = "own"
#: preset -> Antigravity's ACP mode id (``session/set_mode``). Full access is
#: its default mode on a process started with ``--dangerously-skip-permissions``.
MODES: dict[str, str] = {OWN: "default", "edits": "accept-edits", "plan": "plan", "full": "default"}
MODE_LABELS: dict[str, str] = {OWN: "Antigravity's own rules"}
MODE_ABOUT: dict[str, str] = {
    OWN: ("Asks before it runs a command. File changes follow your own Antigravity permission settings — "
          "by default it changes files without asking, inside and outside the folder."),
    "edits": "Changes files in the folder without asking; asks before it runs a command.",
    "plan": "Plans first, then works. Not read-only: it can still change files.",
    "full": "Runs everything without asking.",
}
HIDDEN_MODES: dict[str, str] = {
    "ask": ("Antigravity asks before every file change only when your own Antigravity settings say "
            "toolPermission \"strict\" — Iron Jarvis never changes Antigravity's settings."),
    "auto": "Antigravity's auto mode is not offered over its agent protocol.",
}
#: Full access: started with this flag (a new process before the next message).
FULL_FLAG = "--dangerously-skip-permissions"

#: Thinking levels, low to high (Antigravity names them per model).
EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")
MAX_MODELS = 200

#: The per-session log Antigravity writes (``--log-file``).
LOG_NAME = "agy-acp.log"
#: A log is read from its end, at most this many bytes.
LOG_TAIL_BYTES = 256 * 1024

#: When set (non-blank), the ONLY Antigravity folder Iron Jarvis READS for its
#: own listings (saved sessions) — the test suite points it at an empty
#: folder: a test must never read the developer's real ``~/.gemini``. The
#: Antigravity PROCESS is not told.
HOME_ENV = "IRONJARVIS_ANTIGRAVITY_HOME"

#: A tool row Antigravity said no more about (by Stop / by the reply's end).
STOPPED_TOOL = "Stopped when you pressed Stop."
UNREPORTED_TOOL = "Antigravity did not report this step's result."

#: Antigravity's own words for a failed reply and a retry (measured).
TERMINATED_RX = re.compile(r"^\s*⚠\s*Agent execution terminated due to error\.?\s*$")
API_ERROR_RX = re.compile(r"^\s*⚠\s*API error \(attempt (\d+)\):?\s*(.*)$")
AUTH_REQUIRED_RX = re.compile(r"authentication required", re.I)

_SIGN_IN_RX = re.compile(r"not logged in|authentication required|unauthenticated|sign ?in required|"
                         r"log ?in required|\b401\b|unauthori[sz]ed", re.I)
_REFUSED_RX = re.compile(r"api key not valid|api_key_invalid|permission_denied|\b403\b|invalid (?:api )?key", re.I)
_LIMIT_RX = re.compile(r"\b429\b|resource_exhausted|quota|rate limit|too many requests|usage limit", re.I)
SIGN_IN_FIX = "Open a Build terminal, run agy and sign in there, then send the message again."
LIMIT_FIX = "Wait until it resets, or choose another model."
OTHER_FIX = "Send the message again. If it happens again, choose another model for this session."


def failure_event(said: str) -> dict[str, Any]:
    """The ``error`` event for a failure Antigravity SAID (or its log did). No
    press: Antigravity signs in in its own screen and has no other account."""
    text = str(said or "")
    if _REFUSED_RX.search(text):
        return W.error("Antigravity's sign-in was refused", SIGN_IN_FIX, text)
    if _SIGN_IN_RX.search(text):
        return W.error("Antigravity isn't signed in", SIGN_IN_FIX, text)
    if _LIMIT_RX.search(text):
        return W.error("Antigravity hit its usage limit", LIMIT_FIX, text)
    return W.error("Antigravity's model answered with an error", OTHER_FIX, text)


# --------------------------------------------------------------------------- #
# Its own log (read-only)
# --------------------------------------------------------------------------- #
_GLOG_RX = re.compile(r"^E\d{4} [\d:.]+\s+\d+\s+[^\]]+\]\s*(.*)$")
#: An error line about the MODEL CALL (the rest of a log's E lines are about
#: a missing project file, plugins, experiments — never the reply).
_REASON_RX = re.compile(r"executor error|generating and executing|Error \d{3}|RESOURCE_EXHAUSTED|"
                        r"PERMISSION_DENIED|UNAUTHENTICATED|API key|quota", re.I)


def log_reason(path: str | os.PathLike[str] | None) -> str:
    """BLOCKING (one bounded read of the END of a small file): the reason a
    reply failed, from the session's own Antigravity log — its last error line
    about the model call, the log's own prefix dropped. "" when none."""
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    found = ""
    for line in text.splitlines():
        m = _GLOG_RX.match(line)
        if m and _REASON_RX.search(m.group(1)):
            found = m.group(1).strip()
    return clip(found, 1200) if found else ""


# --------------------------------------------------------------------------- #
# Where Antigravity keeps things (read-only)
# --------------------------------------------------------------------------- #
def default_home(environ: Any = None) -> Path:
    """The Antigravity folder Iron Jarvis reads: :data:`HOME_ENV`, else
    ``~/.gemini/antigravity-cli``."""
    env = os.environ if environ is None else environ
    override = str(env.get(HOME_ENV) or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "antigravity-cli"


def _same(a: Any, b: Any) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (TypeError, ValueError):
        return False


def uri_folder(uri: Any) -> str:
    """A ``file:///C:/...`` workspace URI as a folder path ("" otherwise)."""
    text = str(uri or "")
    if not text.lower().startswith("file:"):
        return ""
    path = unquote(text[5:])
    while path.startswith("/"):
        path = path[1:]
    if not re.match(r"^[A-Za-z]:", path) and os.name != "nt":
        path = "/" + path
    return path


def _epoch(stamp: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_UUID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _connect_ro(db: Path) -> sqlite3.Connection | None:
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
    """BLOCKING: Antigravity's OWN saved top-level conversations for
    ``folder`` (its summaries database, read-only), newest first. The folder
    is matched by the workspace URIs Antigravity wrote."""
    from ..history.common import title_of

    con = _connect_ro(Path(home) / "conversation_summaries.db")
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT conversation_id, title, preview, last_modified_time, last_user_input_time, workspace_uris "
            "FROM conversation_summaries WHERE nesting_depth = 0 ORDER BY last_modified_time DESC LIMIT ?",
            (max(1, int(limit)) * 4,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for cid, title, preview, modified, last_input, uris in rows:
        sid = str(cid or "")
        if not _UUID_RX.match(sid):
            continue
        mtime = _epoch(modified)
        if since is not None and mtime < since:
            continue
        try:
            listed = json.loads(uris or "[]")
        except ValueError:
            listed = []
        places = [uri_folder(u) for u in (listed if isinstance(listed, list) else [])]
        places = [p for p in places if p]
        if folder and not any(_same(p, folder) for p in places):
            continue
        out.append({
            "harness": HARNESS, "id": sid, "project": places[0] if places else "",
            "title": title_of(str(title or preview or "")),
            "started": str(last_input or ""), "ended": str(modified or ""), "mtime": mtime,
        })
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
#: The models an Antigravity process last listed and the model it started on.
LAST_MODELS: list[dict[str, Any]] = []
LAST_DEFAULT: list[str] = []


def _ordered(levels: list[str]) -> list[str]:
    known = [x for x in EFFORTS if x in levels]
    return known + [x for x in levels if x not in known]


def model_rows(config: Any, models: Any = None) -> tuple[list[dict[str, Any]], str, str]:
    """``configOptions`` (+ ``models``) -> (the composer's rows — each BASE
    model with the thinking levels THAT model takes —, the current model, the
    current level). A model's levels are read from ``availableModels`` (its
    ``<base>-<level>`` ids, measured to match ``thought_level``'s options);
    the current model's from ``thought_level`` itself."""
    model_opt = _config(config, "model")
    level_opt = _config(config, "thought_level")
    current = str(model_opt.get("currentValue") or "")
    level = str(level_opt.get("currentValue") or "")
    listed = [str(v.get("modelId") or "") for v in ((models or {}).get("availableModels") or [])
              if isinstance(v, dict)] if isinstance(models, dict) else []
    rows: list[dict[str, Any]] = []
    for v in model_opt.get("options") or []:
        if not isinstance(v, dict) or not v.get("value"):
            continue
        base = str(v.get("value"))
        levels = [mid[len(base) + 1:] for mid in listed if mid.startswith(base + "-")]
        if base == current and level_opt:
            levels = _values(level_opt) or levels
        rows.append({"id": clip(base, 120), "label": clip(v.get("name") or base, 120),
                     "efforts": _ordered([x for x in levels if x])[:12]})
        if len(rows) >= MAX_MODELS:
            break
    return rows, clip(current, 120), level


def save_models(path: Path, rows: list[dict[str, Any]], default: str = "") -> None:
    """BLOCKING: the model list an Antigravity process said, for the next listing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(json.dumps({"models": rows[:MAX_MODELS], "default": default},
                                   ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep Antigravity's model list", exc_info=True)


def cached_models(path: Path | None) -> tuple[list[dict[str, Any]], str]:
    """BLOCKING-light: ``(the models Antigravity last listed, its default)`` —
    memory, else Iron Jarvis's own copy. Never a start."""
    if LAST_MODELS:
        return list(LAST_MODELS), (LAST_DEFAULT[0] if LAST_DEFAULT else "")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else None
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return [], ""
    out = []
    for r in data["models"]:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]:
            row: dict[str, Any] = {"id": clip(r["id"], 120), "label": clip(r.get("label") or r["id"], 120)}
            if isinstance(r.get("efforts"), list):
                row["efforts"] = [str(e) for e in r["efforts"] if isinstance(e, str)][:12]
            out.append(row)
    return out[:MAX_MODELS], clip(str(data.get("default") or ""), 120)


# --------------------------------------------------------------------------- #
# Questions (``elicitation/create``)
# --------------------------------------------------------------------------- #
_Q_KEY = re.compile(r"^q(\d+)$")


def form_questions(schema: Any) -> list[dict[str, Any]]:
    """Antigravity's question form -> ``[{key, question, options [{label,
    const}], other}]`` in its own order (``q1``, ``q2``…)."""
    props = (schema or {}).get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    keys = sorted((k for k in props if _Q_KEY.match(str(k))), key=lambda k: int(_Q_KEY.match(k).group(1)))
    out = []
    for key in keys[:12]:
        prop = props.get(key) if isinstance(props.get(key), dict) else {}
        options = [{"label": clip(o.get("title") or o.get("const") or "", 120), "const": str(o.get("const"))}
                   for o in prop.get("oneOf") or [] if isinstance(o, dict) and o.get("const") is not None][:20]
        other = f"{key}_other" if isinstance(props.get(f"{key}_other"), dict) else ""
        out.append({"key": key, "question": clip(prop.get("title") or "", 500), "options": options,
                    "other": other})
    return out


def form_answer(questions: list[dict[str, Any]], answers: Any) -> dict[str, str]:
    """The card's answers -> the form's ``content`` (a choice's ``const``; a
    typed answer in ``qN_other``). AskRefused when nothing usable came."""
    content: dict[str, str] = {}
    for i, q in enumerate(questions):
        text = str(q.get("question") or "")
        if isinstance(answers, list):
            got = answers[i] if i < len(answers) else None
        elif isinstance(answers, dict):
            got = answers.get(text, answers.get(str(i), answers.get(q.get("key"))))
            if got is None and len(questions) == 1 and len(answers) == 1:
                got = next(iter(answers.values()))
        elif isinstance(answers, str) and len(questions) == 1:
            got = answers
        else:
            got = None
        if isinstance(got, list):
            got = got[0] if got else None
        value = str(got or "").strip()
        if not value:
            continue
        pick = next((o for o in q.get("options") or [] if o.get("label") == value or o.get("const") == value), None)
        if pick is not None:
            content[q["key"]] = pick["const"]
        elif q.get("other"):
            content[q["other"]] = value[:2000]
        else:
            raise AskRefused("Choose one of the answers Antigravity offered.")
    if not content:
        raise AskRefused("Choose an answer for the question first.")
    return content


# --------------------------------------------------------------------------- #
# The tools' words
# --------------------------------------------------------------------------- #
#: Antigravity's own tool names (measured from its requests) -> the view's kinds.
_BY_NAME = {
    "run_command": "command", "read_file": "read", "view_file": "read", "list_dir": "read",
    "write_file": "write", "edit_file": "edit", "search_web": "web", "read_url_content": "web",
    "grep_search": "search", "find_by_name": "search", "invoke_subagent": "task", "define_subagent": "task",
    "manage_subagents": "task", "manage_task": "todo", "ask_question": "other", "send_message": "other",
    "schedule": "other",
}


def _fix_path(path: Any) -> str:
    """Antigravity writes a Windows path both as ``C:/x`` and ``/C:/x``."""
    text = str(path or "")
    if re.match(r"^/[A-Za-z]:[/\\]", text):
        text = text[1:]
    return text


def normalized(update: dict[str, Any]) -> dict[str, Any]:
    """A tool record with Antigravity's own argument names said the shared
    way (``command``, ``path``) and an edit's diff made from its OWN input
    (its ``diff`` content block gives a new file an extra blank line)."""
    u = dict(update)
    raw = u.get("rawInput") if isinstance(u.get("rawInput"), dict) else (
        u.get("args") if isinstance(u.get("args"), dict) else None)
    if not raw:
        return u
    args = dict(raw)
    if isinstance(args.get("CommandLine"), str):
        args["command"] = args["CommandLine"]
    if isinstance(args.get("Path"), str):
        args["path"] = _fix_path(args["Path"])
    name = str(u.get("name") or "").lower()
    path = args.get("path") or ""
    blocks = [b for b in u.get("content") or [] if isinstance(b, dict) and b.get("type") != "diff"]
    if path and name == "write_file" and isinstance(args.get("Content"), str):
        blocks.insert(0, {"type": "diff", "path": path, "newText": args["Content"]})
        u["content"] = blocks
    elif path and name == "edit_file" and isinstance(args.get("Target"), str):
        blocks.insert(0, {"type": "diff", "path": path, "oldText": args["Target"],
                          "newText": str(args.get("Replacement") or "")})
        u["content"] = blocks
    if isinstance(u.get("locations"), list):
        u["locations"] = [{**loc, "path": _fix_path(loc.get("path"))} for loc in u["locations"]
                          if isinstance(loc, dict)]
    u["rawInput"] = args
    return u


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
class AntigravityDriver(AcpDriver):
    harness = HARNESS
    LABEL = LABEL

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        #: Where the service keeps the model list (set before ``open``).
        self.catalog_path: Path | None = None
        #: The session's own log (``--log-file``), set by the service.
        self.log_path: str = ""
        #: The service's way to name an untitled session (Antigravity's title).
        self.on_title: Any = None
        #: The prompt whose answer ends the turn: the message's, then each note's.
        self._latest: int | None = None
        #: Notes sent while it works, in order: (ref, text), until echoed.
        self._notes: list[tuple[str, str]] = []
        #: Antigravity said the reply failed (its text); its log is read next.
        self._failed_said = ""
        self._fail_read: asyncio.Future | None = None
        #: Set when a change can only apply to a NEW process (Full access).
        self.restart_needed = False
        #: The user declined a card this turn (Antigravity then ends its reply).
        self._declined = False

    STATE_SKIP = AcpDriver.STATE_SKIP | frozenset({"on_title", "_fail_read"})

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        # The folder is the process's own working folder; the session is
        # opened (or reopened) over the protocol, never by an argument.
        out = ["acp", "--disable-telemetry"]
        if self.log_path:
            out += ["--log-file", self.log_path]
        if self.mode == "full":
            out.append(FULL_FLAG)
        return out

    def failure_event(self, said: str) -> dict[str, Any]:
        return failure_event(said)

    def client_capabilities(self) -> dict[str, Any]:
        """Iron Jarvis answers Antigravity's question FORM (measured: without
        ``elicitation`` declared, Antigravity prints the question as text and
        ends the turn)."""
        return {**super().client_capabilities(), "elicitation": {"form": {}}}

    # -- the session --------------------------------------------------------------
    async def _new_or_resume(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return await self.request(method, params)
        except ControlRefused as exc:
            if AUTH_REQUIRED_RX.search(str(exc)):
                # Never ``authenticate`` from here: Antigravity KEEPS the
                # method chosen (measured), which would change its sign-in.
                raise ControlRefused(W.ANTIGRAVITY_SIGN_IN) from None
            raise

    async def open(self, *, session_id: str, resume: bool) -> None:
        caps = await self.initialize()
        servers = self.acp_servers(caps)
        sess_caps = caps.get("sessionCapabilities") if isinstance(caps.get("sessionCapabilities"), dict) else {}
        params: dict[str, Any] = {"cwd": self.cwd, "mcpServers": servers}
        if resume and session_id:
            params["sessionId"] = session_id
            if "resume" in sess_caps:
                # Reopens WITHOUT replaying the conversation (measured).
                res = await self._new_or_resume("session/resume", params)
            elif self.can_load:
                self._loading = True
                try:
                    res = await self._new_or_resume("session/load", params)
                finally:
                    self._loading = False
            else:
                raise ControlRefused("this Antigravity cannot reopen a saved session")
            sid = session_id
        else:
            res = await self._new_or_resume("session/new", params)
            sid = str((res or {}).get("sessionId") or "") if isinstance(res, dict) else ""
        if not sid:
            raise ControlRefused("Antigravity did not open a session")
        self.session_id = sid
        self.on_session_id(sid)
        res = res if isinstance(res, dict) else {}
        self._take_options(res.get("configOptions"), res.get("models"), first=not resume)
        if self.model and self.model != self._current("model"):
            await self._set_model(self.model)
        if self.effort and self.effort != self._current("effort"):
            if self.effort in _values(_config(self.options, "effort")):
                await self._set_effort(self.effort)
            else:
                self.emit(make("notice", text=W.ANTIGRAVITY_NO_SUCH_EFFORT.format(effort=self.effort)))
        wanted = MODES.get(self.mode, "default")
        if wanted != "default":
            await self.request("session/set_mode", {"sessionId": sid, "modeId": wanted})
        self.emit(make("session", harness=HARNESS, harness_session_id=sid,
                       model=clip(self._current("model") or self.model, 120), cwd=clip(self.cwd, 500),
                       mode=self.mode))

    def _current(self, cid: str) -> str:
        return str(_config(self.options, cid).get("currentValue") or "")

    def _take_options(self, config: Any, models: Any = None, *, first: bool = False) -> None:
        rows, current, level = model_rows(config, models)
        if not rows:
            return
        if models is None and LAST_MODELS:
            # A config update says only the CURRENT model's levels: the other
            # rows keep what the full list said.
            known = {r["id"]: r for r in LAST_MODELS}
            for r in rows:
                if r["id"] != current and r["id"] in known and not r["efforts"]:
                    r["efforts"] = list(known[r["id"]].get("efforts") or [])
        cur = next((r for r in rows if r["id"] == current), None)
        self.options = [
            {"id": "model", "currentValue": current,
             "options": [{"value": r["id"], "name": r["label"]} for r in rows]},
            {"id": "effort", "currentValue": level,
             "options": [{"value": e} for e in ((cur or {}).get("efforts") or [])]},
        ]
        LAST_MODELS[:] = rows
        if first and not LAST_DEFAULT:
            LAST_DEFAULT[:] = [current]
        if self.catalog_path is not None:
            path, default = self.catalog_path, (LAST_DEFAULT[0] if LAST_DEFAULT else "")
            try:
                asyncio.get_running_loop().run_in_executor(None, save_models, path, list(rows), default)
            except RuntimeError:
                pass

    def on_config_update(self, options: Any) -> None:
        """``config_option_update``: the model and ITS thinking levels."""
        self._take_options(options)

    async def _set_model(self, model: str) -> None:
        try:
            got = await self.request("session/set_config_option",
                                     {"sessionId": self.session_id, "configId": "model", "value": model})
        except ControlRefused as exc:
            raise ControlRefused(f"Antigravity refused that model: {exc}") from None
        self._take_options((got or {}).get("configOptions") if isinstance(got, dict) else None)
        opt = _config(self.options, "model")
        if opt:
            opt["currentValue"] = model

    async def _set_effort(self, effort: str) -> None:
        try:
            got = await self.request("session/set_config_option",
                                     {"sessionId": self.session_id, "configId": "thought_level", "value": effort})
        except ControlRefused as exc:
            raise ControlRefused(f"Antigravity refused that thinking level: {exc}") from None
        self._take_options((got or {}).get("configOptions") if isinstance(got, dict) else None)
        eff = _config(self.options, "effort")
        if eff:
            eff["currentValue"] = effort

    # -- the tools' words -------------------------------------------------------------
    def tool_name(self, update: dict[str, Any]) -> str:
        return str(update.get("name") or update.get("title") or "")

    def tool_kind(self, name: str, acp_kind: str = "") -> str:
        n = str(name or "").lower()
        if n in _BY_NAME:
            return _BY_NAME[n]
        if "__" in n or n.startswith("mcp_"):
            return "mcp"  # a tool of one of the user's own MCP servers
        # Antigravity's own other tools are snake_case and "other" — never MCP.
        return _acp_tool_kind(name, acp_kind if acp_kind != "other" else "", by_name={})

    def tool_title(self, name: str, kind: str, args: dict[str, Any]) -> str:
        if str(name or "").lower() == "ask_question":
            return "Ask you a question"
        if str(name or "").lower() == "invoke_subagent":
            return "Hand a task to a helper"
        from .acp import tool_title as _title

        return _title(name, kind, args)

    def session_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Antigravity offers no "for this session" answer (measured): every
        card is Allow once."""
        return None

    def deny_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next((o for o in offered if o.get("kind") == "reject_once"), None)

    def _ask(self, rid: Any, p: dict[str, Any]) -> None:
        if isinstance(p.get("toolCall"), dict):
            p = {**p, "toolCall": normalized(p["toolCall"])}
        super()._ask(rid, p)

    def _tool_update(self, u: dict[str, Any], *, first: bool) -> None:
        super()._tool_update(normalized(u), first=first)

    # -- questions --------------------------------------------------------------------
    def _on_request(self, method: str, rid: Any, p: dict[str, Any]) -> None:
        if method == "elicitation/create":
            self._question(rid, p)
            return
        super()._on_request(method, rid, p)

    def _question(self, rid: Any, p: dict[str, Any]) -> None:
        qs = form_questions(p.get("requestedSchema"))
        if not qs:
            asyncio.ensure_future(self._respond(rid, {"action": "decline"}))
            return
        rows = []
        for q in qs:
            row: dict[str, Any] = {"question": q["question"] or clip(p.get("message") or "", 500),
                                   "options": [{"label": o["label"]} for o in q["options"]], "multi": False}
            if not q["other"]:
                row["free_text"] = False
            rows.append(row)
        ask_id = new_ask_id()
        ev = make("question", request_id=ask_id, questions=rows)
        self.raise_ask(Ask(ask_id, "question", "ask_question", rid, {"questions": qs}, ev, ev["at"], False))

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        if ask.kind == "question":
            if decision == "deny":
                return {"jsonrpc": "2.0", "id": ask.native, "result": {"action": "decline"}}
            content = form_answer(ask.data.get("questions") or [], answers)
            return {"jsonrpc": "2.0", "id": ask.native, "result": {"action": "accept", "content": content}}
        return super().prepare_answer(ask, decision, message, answers)

    # -- the user's side ----------------------------------------------------------
    def answered(self, ask: Ask, decision: str) -> None:
        if ask.kind == "approval" and decision == "deny" and not self._interrupting:
            self._declined = True

    def turn_started(self) -> None:
        self._declined = False
        self._notes.clear()
        self._failed_said = ""
        self._fail_read = None

    async def send_user(self, text: str, files: list[str]) -> None:
        blocks = await self._prompt_blocks(text, files)
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        self._stop = ""
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._pending.clear()
        self.turn_started()
        rid = self._start_prompt(blocks)
        # Named the turn's LAST prompt before it is written: an answer that
        # comes back during the write must end the turn.
        self._message_prompt = self._latest = rid
        await self.write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": blocks}})

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """A note while it works: a second ``session/prompt`` (measured: it
        joins the running turn; the first prompt answers at once and the turn
        ends with the note's answer). Read when Antigravity echoes it."""
        if not (self.turn_open and self._latest is not None):
            raise ControlRefused(W.STEER_TOO_LATE.format(label=self.LABEL))
        blocks = await self._prompt_blocks(text, files)
        if not (self.turn_open and self._latest is not None):
            raise ControlRefused(W.STEER_TOO_LATE.format(label=self.LABEL))
        self.steers[ref] = "sent"
        self._notes.append((ref, _words(blocks[0].get("text"))))
        rid = self._start_prompt(blocks, ref)
        self._latest = rid
        await self.write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": blocks}})
        return "step"

    def stopping(self) -> None:
        """The user pressed Stop: a card answered "deny" for it is the Stop's,
        never a decline of the user's."""
        self._interrupting = True

    async def set_mode(self, mode: str) -> str:
        """Own rules / Auto edits / Plan change live (``session/set_mode``,
        from the next prompt — measured); Full access is a flag, so going to
        or from it is a NEW process before the next message."""
        if (mode == "full") != (self.mode == "full"):
            self.restart_needed = True
            self.mode = mode
            return "next"
        if self.session_id and MODES.get(mode) != MODES.get(self.mode):
            await self.request("session/set_mode", {"sessionId": self.session_id,
                                                    "modeId": MODES.get(mode, "default")})
        self.mode = mode
        return "next" if self.turn_open else "now"

    async def set_model(self, model: str) -> str:
        values = _values(_config(self.options, "model"))
        if values and model not in values:
            raise ControlRefused("Antigravity does not list that model — choose one from its list.")
        await self._set_model(model)
        self.model = model
        if self.effort and self.effort not in _values(_config(self.options, "effort")):
            self.emit(make("notice", text=W.ANTIGRAVITY_NO_SUCH_EFFORT.format(effort=self.effort)))
            self.effort = ""
        return "next" if self.turn_open else "now"

    async def set_effort(self, effort: str) -> str:
        if effort:
            offered = _values(_config(self.options, "effort"))
            if offered and effort not in offered:
                raise ControlRefused(f'This Antigravity model takes {", ".join(offered)} — not "{effort}".')
            await self._set_effort(effort)
        self.effort = effort
        return ("next" if self.turn_open else "now") if effort else "next"

    # -- Antigravity's side (ON THE LOOP) -------------------------------------------------
    def _on_update(self, u: dict[str, Any]) -> None:
        kind = u.get("sessionUpdate")
        if kind == "session_info_update":
            title = one_line(u.get("title"), 120)
            if title and callable(self.on_title):
                self.on_title(title)
            return
        if kind == "user_message_chunk":
            if self._loading or not self.turn_open:
                return
            said = _words((u.get("content") or {}).get("text") if isinstance(u.get("content"), dict) else "")
            for i, (ref, sent) in enumerate(self._notes):
                if said and sent and (said == sent or said.startswith(sent) or sent.startswith(said)):
                    del self._notes[i]
                    self.note_read(ref)
                    break
            return
        if kind == "agent_message_chunk" and not self._loading and self.turn_open:
            content = u.get("content") if isinstance(u.get("content"), dict) else {}
            text = content.get("text")
            if isinstance(text, str) and "⚠" in text:
                kept = self._said_failures(text)
                if not kept.strip():
                    return
                u = {**u, "content": {**content, "text": kept}}
        super()._on_update(u)

    def _said_failures(self, text: str) -> str:
        """Antigravity's own failure lines out of a reply's text: a retry is a
        notice, the end of a failed reply is remembered (the reply FAILS);
        anything else stays the reply's text."""
        kept = []
        for line in text.split("\n"):
            m = API_ERROR_RX.match(line)
            if m:
                detail = one_line(m.group(2), 300)
                said = f"Antigravity is trying the model again (attempt {m.group(1)})"
                self.emit(make("notice", text=f"{said}: {detail}" if detail else f"{said}."))
                continue
            if TERMINATED_RX.match(line):
                self._failed_said = line.strip()
                if self._fail_read is None and self.log_path:
                    self._fail_read = asyncio.ensure_future(asyncio.to_thread(log_reason, self.log_path))
                continue
            kept.append(line)
        return "\n".join(kept)

    def _prompt_answered(self, rid: int, fut: asyncio.Future) -> None:
        self._pending.pop(rid, None)
        if fut.cancelled():
            return
        exc = fut.exception()
        result = fut.result() if exc is None else None
        if rid != self._latest:
            # A prompt a later note superseded: answered end_turn at once, no
            # usage (measured) — the turn goes on.
            if rid == self._message_prompt:
                self._message_prompt = None
            return
        self._message_prompt = None
        if exc is not None:
            if isinstance(exc, ControlRefused):
                self._fail(str(exc))
                self._stop = "failed"
            else:
                return  # the process went: the service ends the turn
        else:
            data = result if isinstance(result, dict) else {}
            stop = self.take_answer(data)
            self._stop = self.stop_of(stop, data)
            if stop == "max_tokens":
                self.emit(make("notice", text="The answer reached the model's output limit."))
            if self._interrupting and self._stop == "completed":
                self._stop = "interrupted"
        if self._failed_said and self._stop != "interrupted":
            self._stop = "failed"
        if self._declined and self._stop == "completed":
            # A Deny ENDS Antigravity's reply (measured: no model call after
            # it) — said, so a short reply never reads as finished work.
            self.emit(make("notice", text=W.ANTIGRAVITY_DECLINED_ENDS))
        self._latest = None
        self._pending.clear()
        if not self.turn_open:
            return
        stop = self._stop or "completed"
        if self._failed_said and stop == "failed" and not self._errored:
            read = self._fail_read
            if read is not None and not read.done():
                read.add_done_callback(lambda f, stop=stop: self._fail_then_end(f, stop))
                return
            self._fail_then_end(read, stop)
            return
        self._end(stop)

    def _fail_then_end(self, read: asyncio.Future | None, stop: str) -> None:
        reason = ""
        if read is not None and read.done() and not read.cancelled() and read.exception() is None:
            reason = str(read.result() or "")
        self._fail(reason or self._failed_said)
        self._end(stop)

    def end_turn_tools(self) -> None:
        """A step Antigravity said no more about by the turn's end: a Stop's is
        drawn stopped; otherwise (measured in Full access: a command's row got
        no update after it started) it is drawn ended, saying its result was
        not reported — a row never spins after its reply ended."""
        from .acp import _input_line

        for tid, (name, acp_kind, args) in list(self._tools.items()):
            kind = self.tool_kind(name, acp_kind)
            self.emit(make("tool", item=tid, kind=kind, name=clip(name, 120),
                           title=self.tool_title(name, kind, args), input=_input_line(args),
                           status="failed" if self._interrupting else "done",
                           output=STOPPED_TOOL if self._interrupting else UNREPORTED_TOOL))
        self._tools.clear()

    def after_restore(self) -> None:
        super().after_restore()
        self._fail_read = None


def _words(text: Any) -> str:
    return " ".join(str(text or "").split())
