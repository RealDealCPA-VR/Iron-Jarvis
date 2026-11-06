"""Grok in the Code view (v1.416.0): ``grok agent --no-leader stdio`` — the
Agent Client Protocol (the same JSON-RPC core OpenCode speaks,
:mod:`hosted.acp`) — with ALL of Grok's own tools, MCP servers, skills,
plugins, hooks, AGENTS.md and config on. The same Grok the user runs in a
terminal; Iron Jarvis only starts it, draws what it does, relays messages and
turns every permission question Grok asks into a card.

WHY ``agent stdio`` (``grok agent --help``: "stdio — Run the agent over
stdio"): it speaks over the process's own stdin/stdout, so it fits the Code
view's process plumbing (``hosted.process``: one child in its own
kill-on-close Job, its tree killed on stop) and opens NO listening port
(``serve`` is the WebSocket server; ``headless`` dials a relay). ``--no-leader``
always: Grok's ``[cli] use_leader`` would otherwise hand the session to a
SHARED leader process that outlives this one and that a tree kill never
reaches.

MEASURED on this PC (grok 1.0.5 -> 1.0.70: the CLI updated ITSELF during the
first run — its own background updater; live, in scratch folders, tiny
one-word prompts on grok-4.5 effort low; masked recordings in
``tests/fixtures/build_app_v1416``):

* ``initialize`` {protocolVersion: 1, clientCapabilities: {fs: false,
  terminal: false}} answers ``agentCapabilities``: ``loadSession: true``,
  ``promptCapabilities.image: true`` (1.0.70; 1.0.5 said false),
  ``mcpCapabilities.http: true``, ``sessionCapabilities`` {list, resume,
  close}, and ``authMethods`` (``cached_token`` = ``~/.grok/auth.json``,
  ``grok.com``). Grok also pushes many ``_x.ai/...`` notifications of its own
  (models, settings, announcements, MCP status — ``_x.ai/mcp/servers_updated``
  carries the user's MCP config, URLs with keys included): NONE is drawn,
  logged or kept here.
* ``session/new`` {cwd, mcpServers: [], _meta} answers ``sessionId`` (a
  UUIDv7), ``models`` {currentModelId, availableModels [{modelId, name,
  _meta: {totalContextTokens, reasoningEffort, reasoningEfforts [{value}]}}]}.
  ``session/set_model`` {sessionId, modelId} switches live
  (``_meta.model.Ok``); effort is ``session/set_config_option`` {configId:
  "reasoning_effort", value} (answers ``configOptions``; ids ``model`` and
  ``reasoning_effort``). ``session/set_mode`` answers ``{}`` for ANY id, even
  a made-up one, and changes nothing (a write still asked after
  ``acceptEdits``) — it is never used.
* PERMISSIONS are the session's ``_meta`` (Grok's documented ACP options):
  ``yoloMode`` (always-approve) and ``autoMode`` (Grok's own safety check).
  The user's own ``[ui] permission_mode`` in ``~/.grok/config.toml`` is the
  default when ``_meta`` says nothing — on THIS PC it is ``always-approve``,
  and a session with no ``_meta`` wrote a file with no card. So every
  session states its posture explicitly (:func:`session_meta`), on
  ``session/new`` AND on ``session/resume`` (measured: ``yoloMode: false``
  on a resume asked again). Grok's own rules (deny > ask > allow, in its
  config files) still apply on top of every posture.
* An ``ask`` arrives as ``session/request_permission`` (Grok's own request
  ids count from 0): an edit offers ``allow-edits-session`` ("Yes, allow all
  edits during this session" — memory only, gone with the process, per
  Grok's docs), ``allow-once``, ``reject-once``; a command offers
  ``always-allow`` ("don't ask again for bash commands") and
  ``reject-always`` — both SAVED by Grok for the project ("Interactive
  approvals ... persist", its docs). Iron Jarvis never picks a saved option:
  Allow for this session is only ``allow-edits-session``, Deny only
  ``reject-once``; a command's card is Allow once only.
* A ``reject-once`` ENDS the turn: the prompt answers ``stopReason:
  "cancelled"`` with ``_meta.cancellationCategory: "PermissionRejected"`` —
  drawn as an ended reply with a notice, never as the user's Stop.
* ``session/prompt`` answers at the turn's end ({stopReason, _meta: {usage:
  {inputTokens, outputTokens, cachedReadTokens, costUsdTicks}, totalTokens}});
  the updates in between are ACP's ``agent_message_chunk``,
  ``agent_thought_chunk``, ``tool_call`` (title = the tool's name, e.g.
  ``write``, ``run_terminal_command``; ``_meta["x.ai/tool"]`` {name, kind}),
  ``tool_call_update`` (``kind`` edit/execute, a ``diff`` block, the command's
  output so far, cumulative), ``available_commands_update``,
  ``session_info_update``. A plain read-only command (``echo``, ``mkdir``) runs
  without a card even in Ask (Grok's own read-only list).
* ``session/cancel`` ends the turn (``cancelled``, ``MidTurnAbort``) — and the
  command it was running KEEPS RUNNING (a ``ping`` outlived the cancel and
  Grok itself); ``_x.ai/terminal/kill`` with the tool call id answered
  "terminal not found". So a Stop that leaves a command running closes the
  process, and the command too: Grok runs it in the Store pwsh, which is
  never in our Job, so its pid (read before the cancel,
  ``HarnessProcess.note_started``) is ended by name (:attr:`close_after_stop`).
* A SECOND ``session/prompt`` while a turn runs is QUEUED: it runs as its own
  turn after the first ends. A note while it works is Grok's own
  ``_x.ai/interject`` {sessionId, text} (its Ctrl+Enter): answered ``{result:
  {status: "queued"}}`` and echoed AT ONCE as ``_x.ai/session/interjection``
  {text}; the model read it at its next step (measured).
* ``session/resume`` {sessionId, cwd, mcpServers, _meta} reopens a saved
  session WITHOUT replaying it (``session/load`` replays the whole
  conversation as updates before it answers); it keeps the session's last
  model. Sessions live in Grok's own folder
  (``<GROK_HOME or ~/.grok>/sessions/<the folder, URL-encoded>/<id>/
  summary.json``: info {id, cwd}, generated_title, created_at, updated_at,
  num_messages); ``grok --resume <id>`` opens one in a terminal.
* Sign-in is Grok's own (``~/.grok/auth.json``, ``grok login``); an
  Iron-Proxy account is its home (``GROK_HOME``), applied to the process
  exactly as a Build terminal's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import words as W
from .acp import AcpDriver, _config, _values, tool_kind as _acp_tool_kind
from .driver import ControlRefused
from .events import clip, make

log = logging.getLogger(__name__)

HARNESS = "grok"

#: preset -> Grok's permission posture (the session's ``_meta``).
MODES: dict[str, str] = {"ask": "ask", "auto": "auto", "full": "full"}
#: The presets Grok cannot honour from here, and why (said in the mode menu).
HIDDEN_MODES: dict[str, str] = {
    "edits": "Grok has no setting over its agent protocol that lets file edits through without asking — "
             "choose Ask first and press Allow for this session on the first edit, or Auto.",
    "plan": "Grok's plan mode cannot be started from here (its agent mode ignores it) — choose Ask first.",
}
#: What each preset does for Grok, in plain words (the mode menu says it).
MODE_ABOUT: dict[str, str] = {
    "ask": "Asks before it changes a file or runs a command; reading, searching and plain read-only "
           "commands run without asking (Grok's own list).",
    "auto": "Grok's own Auto: its safety check lets routine work run, and asks about or blocks the rest.",
    "full": "Runs everything without asking (deny rules in your Grok config still apply).",
}

#: Grok's effort names (``reasoningEfforts``), low to high.
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh")

#: How Iron Jarvis starts Grok: its agent over stdio, never a shared leader.
ARGV: tuple[str, ...] = ("agent", "--no-leader", "stdio")

#: The ONLY option Iron Jarvis's "Allow for this session" may choose: Grok
#: keeps it in memory for the process. Every other "always" option Grok saves.
SESSION_ONLY_OPTIONS: frozenset[str] = frozenset({"allow-edits-session"})

#: A tool row Grok said nothing more about after a Stop.
STOPPED_TOOL = "Stopped when you pressed Stop."
#: xAI prices in USD ticks: 1 USD = 10^10 ticks (``costUsdTicks``).
USD_TICKS = 10_000_000_000
MAX_MODELS = 300

#: When set (non-blank), the ONLY Grok home Iron Jarvis READS for its own
#: listings — saved sessions, the model list, the sign-in file (the test
#: suite points it at an empty folder: a test must never read the developer's
#: real Grok). The Grok PROCESS is not told.
HOME_ENV = "IRONJARVIS_GROK_HOME"

_SIGN_IN_RX = re.compile(r"not (?:logged|signed) in|log ?in required|sign ?in required|grok login|"
                         r"authentication (?:required|failed|error)|\b401\b|unauthori[sz]ed|"
                         r"invalid (?:api )?key|(?:token|session)\b[^.\n]{0,40}\bexpired", re.I)
_LIMIT_RX = re.compile(r"usage limit|rate limit|\b429\b|quota|too many requests|credits?", re.I)
SIGN_IN_FIX = "Press Sign in to open Grok's own sign-in in a Build terminal, then send the message again."
#: v1.439.0: the provider whose Sign in press fixes it (``lib/cliSignIn``'s
#: GROK_SIGN_IN) — the Code view draws that one press.
SIGN_IN_PROVIDER = "grok-cli"
LIMIT_FIX = "Wait until it resets, or choose another model."
OTHER_FIX = "Send the message again. If it happens again, choose another model for this session."


def session_meta(mode: str) -> dict[str, bool]:
    """The session's permission posture, ALWAYS stated (the user's own config
    default would otherwise decide — always-approve on this PC)."""
    posture = MODES.get(mode, "ask")
    return {"yoloMode": posture == "full", "autoMode": posture == "auto"}


def failure_event(said: str) -> dict[str, Any]:
    """The ``error`` event for a failure Grok SAID. v1.439.0: a sign-in failure
    carries ``fix_kind: "sign_in"`` and ``provider: "grok-cli"``, so the Code
    view draws the one Sign in press (it opens Grok's own sign-in screen in a
    Build terminal) — a Grok session has no other account to move to.

    v1.422.0 (B5): a usage limit is the ACCOUNT-LIMIT card
    (``fix_kind: "account_limit"``) like Claude Code's and Codex's, so the
    reply waits for the reset by itself when the words say when
    (``hosted.carry_on``) and a waiting message is held. Its fix stays
    Grok's own (it never moves to another account)."""
    text = str(said or "")
    if _SIGN_IN_RX.search(text):
        return W.error("Grok isn't signed in", SIGN_IN_FIX, text, fix_kind="sign_in", provider=SIGN_IN_PROVIDER)
    if _LIMIT_RX.search(text):
        return W.error("Grok hit its usage limit", LIMIT_FIX, text, fix_kind="account_limit")
    return W.error("Grok's model answered with an error", OTHER_FIX, text)


# --------------------------------------------------------------------------- #
# Where Grok keeps things (read-only)
# --------------------------------------------------------------------------- #
def default_home(environ: Any = None) -> Path:
    """The Grok home Iron Jarvis reads: :data:`HOME_ENV`, else ``GROK_HOME``,
    else ``~/.grok``."""
    env = os.environ if environ is None else environ
    override = str(env.get(HOME_ENV) or "").strip()
    if override:
        return Path(override)
    home = str(env.get("GROK_HOME") or "").strip()
    return Path(home) if home else Path.home() / ".grok"


def _read_json(path: Path, limit: int = 2_000_000) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _same(a: Any, b: Any) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (TypeError, ValueError):
        return False


_UUID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def disk_sessions(home: str | os.PathLike[str], folder: str, *, since: float | None = None,
                  limit: int = 500) -> list[dict[str, Any]]:
    """BLOCKING: Grok's OWN saved sessions for ``folder`` (each session's
    ``summary.json``, read-only), newest first. The folder is matched by the
    ``cwd`` Grok wrote, never by recomputing its encoded folder name."""
    from ..history.common import title_of

    root = Path(home) / "sessions"
    stamped: list[tuple[float, Path]] = []
    try:
        for d in root.iterdir():
            if not d.is_dir():
                continue
            for s in d.iterdir():
                f = s / "summary.json"
                if not _UUID_RX.match(s.name):
                    continue
                try:
                    stamped.append((f.stat().st_mtime, f))
                except OSError:
                    continue
    except OSError:
        return []
    stamped.sort(key=lambda x: x[0], reverse=True)
    out = []
    for mtime, f in stamped[: max(1, int(limit)) * 4]:
        if since is not None and mtime < since:
            continue
        data = _read_json(f)
        if not isinstance(data, dict):
            continue
        info = data.get("info") if isinstance(data.get("info"), dict) else {}
        sid = str(info.get("id") or f.parent.name)
        if not _UUID_RX.match(sid) or (folder and not _same(info.get("cwd"), folder)):
            continue
        out.append({
            "harness": HARNESS, "id": sid, "project": str(info.get("cwd") or ""),
            "title": title_of(str(data.get("generated_title") or data.get("session_summary") or "")),
            "started": str(data.get("created_at") or ""), "ended": str(data.get("updated_at") or ""),
            "mtime": mtime,
        })
        if len(out) >= limit:
            break
    return out


def has_messages(home: str | os.PathLike[str], hid: str) -> bool:
    """BLOCKING: does Grok's saved session ``hid`` hold a message? Then Grok,
    reopening it, keeps the model it last used (measured)."""
    if not hid or not _UUID_RX.match(hid):
        return False
    root = Path(home) / "sessions"
    try:
        for d in root.iterdir():
            data = _read_json(d / hid / "summary.json")
            if isinstance(data, dict):
                n = data.get("num_messages")
                return isinstance(n, int) and not isinstance(n, bool) and n > 0
    except OSError:
        return False
    return False


def signed_in(home: str | os.PathLike[str] | None = None) -> bool | None:
    """BLOCKING-light (one small file read, never a process): is there a Grok
    sign-in in ``home``? True / False, None when it cannot be read. The token
    itself is never kept or returned."""
    from ..providers.cli_detect import _grok_session_entry

    path = Path(home if home is not None else default_home()) / "auth.json"
    if not path.exists():
        return False
    data = _read_json(path, limit=1_000_000)
    if data is None:
        return None
    try:
        entry = _grok_session_entry(data)
    except Exception:  # noqa: BLE001 — a format change: not known
        return None
    return bool(entry and entry.get("key"))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
#: The models a Grok process last listed and the model it started on.
LAST_MODELS: list[dict[str, Any]] = []
LAST_DEFAULT: list[str] = []


def model_rows(models: Any) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
    """``session/new``'s ``models`` -> (the composer's rows — each with the
    effort levels THAT model takes, low to high — the current model, each
    model's context window)."""
    m = models if isinstance(models, dict) else {}
    rows: list[dict[str, Any]] = []
    windows: dict[str, int] = {}
    for v in m.get("availableModels") or []:
        if not isinstance(v, dict) or not v.get("modelId"):
            continue
        mid = clip(v.get("modelId"), 120)
        meta = v.get("_meta") if isinstance(v.get("_meta"), dict) else {}
        levels = [str(e.get("value")) for e in meta.get("reasoningEfforts") or []
                  if isinstance(e, dict) and e.get("value")]
        row: dict[str, Any] = {"id": mid, "label": clip(v.get("name") or mid, 120),
                               "efforts": [x for x in EFFORTS if x in levels]}
        rows.append(row)
        win = meta.get("totalContextTokens")
        if isinstance(win, int) and not isinstance(win, bool) and win > 0:
            windows[mid] = win
        if len(rows) >= MAX_MODELS:
            break
    return rows, clip(m.get("currentModelId") or "", 120), windows


def save_models(path: Path, rows: list[dict[str, Any]], default: str = "") -> None:
    """BLOCKING: the model list a Grok process said, for the next listing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(json.dumps({"models": rows[:MAX_MODELS], "default": default},
                                   ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep Grok's model list", exc_info=True)


def cached_models(path: Path | None, home: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """BLOCKING-light: ``(the models Grok last listed, its default)`` —
    memory, else Iron Jarvis's own copy, else the list Grok keeps itself
    (``models_cache.json``: names only, efforts not known). Never a start."""
    if LAST_MODELS:
        return list(LAST_MODELS), (LAST_DEFAULT[0] if LAST_DEFAULT else "")
    data = _read_json(path, 4_000_000) if path is not None else None
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        out = []
        for r in data["models"]:
            if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]:
                row: dict[str, Any] = {"id": clip(r["id"], 120), "label": clip(r.get("label") or r["id"], 120)}
                if isinstance(r.get("efforts"), list):
                    row["efforts"] = [str(e) for e in r["efforts"] if isinstance(e, str)][:12]
                out.append(row)
        return out[:MAX_MODELS], clip(str(data.get("default") or ""), 120)
    cache = _read_json((home or default_home()) / "models_cache.json", 8_000_000)
    models = cache.get("models") if isinstance(cache, dict) else None
    out = []
    for mid, entry in (models.items() if isinstance(models, dict) else []):
        info = entry.get("info") if isinstance(entry, dict) and isinstance(entry.get("info"), dict) else {}
        ident = str(info.get("id") or mid)
        if ident:
            out.append({"id": clip(ident, 120), "label": clip(info.get("name") or ident, 120)})
        if len(out) >= MAX_MODELS:
            break
    return out, ""


# --------------------------------------------------------------------------- #
# The tools' words
# --------------------------------------------------------------------------- #
#: Grok's own tool names (``_meta["x.ai/tool"].name``) -> the Code view's kinds.
_BY_NAME = {
    "write": "write", "run_terminal_command": "command", "read_file": "read", "list_dir": "read",
    "grep": "search", "glob": "search", "search_replace": "edit", "edit": "edit", "edit_file": "edit",
    "multi_edit": "edit", "web_search": "web", "web_fetch": "web", "todo_write": "todo", "task": "task",
}
_BY_GROK_KIND = {"write": "write", "execute": "command", "read": "read", "edit": "edit", "search": "search",
                 "fetch": "web", "delete": "edit", "move": "edit"}


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
class GrokDriver(AcpDriver):
    harness = HARNESS
    LABEL = "Grok"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        #: Where the service keeps the model list (set before ``open``).
        self.catalog_path: Path | None = None
        self._windows: dict[str, int] = {}
        self._ticks = 0
        #: Notes sent while it works, in order: (ref, text), until Grok echoes them.
        self._notes: list[tuple[str, str]] = []
        #: v1.416.0: a Stop that left a command of Grok's running — the service
        #: closes the process and ends the command (read before the cancel)
        #: before the turn's done.
        self.close_after_stop = False

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        # The folder is the process's own working folder; the session is
        # opened (or reopened) over the protocol, never by an argument.
        return list(ARGV)

    def failure_event(self, said: str) -> dict[str, Any]:
        ev = failure_event(said)
        if ev.get("fix_kind") == "account_limit":
            # v1.422.0 (B5): the limit, as the other tools keep it (its reset,
            # when the words say one: ``hosted.carry_on.reset_from``).
            from ..terminals.limit_state import PaneLimit
            from .events import one_line

            self.limit = PaneLimit(line=one_line(said, 300))
        return ev

    # -- the session --------------------------------------------------------------
    async def open(self, *, session_id: str, resume: bool) -> None:
        caps = await self.initialize()
        servers = self.acp_servers(caps)
        meta = session_meta(self.mode)
        sess_caps = caps.get("sessionCapabilities") if isinstance(caps.get("sessionCapabilities"), dict) else {}
        if resume and session_id:
            params = {"sessionId": session_id, "cwd": self.cwd, "mcpServers": servers, "_meta": meta}
            if "resume" in sess_caps:
                # Reopens WITHOUT replaying the conversation (measured).
                res = await self.request("session/resume", params)
            elif self.can_load:
                self._loading = True
                try:
                    res = await self.request("session/load", params)
                finally:
                    self._loading = False
            else:
                raise ControlRefused("this Grok cannot reopen a saved session")
            sid = session_id
        else:
            res = await self.request("session/new", {"cwd": self.cwd, "mcpServers": servers, "_meta": meta})
            sid = str((res or {}).get("sessionId") or "") if isinstance(res, dict) else ""
        if not sid:
            raise ControlRefused("Grok did not open a session")
        self.session_id = sid
        self.on_session_id(sid)
        self._take_models((res or {}).get("models") if isinstance(res, dict) else None, first=not resume)
        if self.model and self.model != self._current("model"):
            await self._set_model(self.model)
        if self.effort and self.effort != self._current("effort"):
            if self.effort in _values(_config(self.options, "effort")):
                await self._set_effort(self.effort)
            else:
                self.emit(make("notice", text=W.GROK_NO_SUCH_EFFORT.format(effort=self.effort)))
        self.emit(make("session", harness=HARNESS, harness_session_id=sid,
                       model=clip(self._current("model") or self.model, 120), cwd=clip(self.cwd, 500),
                       mode=self.mode))

    def _current(self, cid: str) -> str:
        return str(_config(self.options, cid).get("currentValue") or "")

    def _take_models(self, models: Any, *, first: bool = False) -> None:
        rows, current, windows = model_rows(models)
        if not rows:
            return
        self._windows = windows
        cur = next((r for r in rows if r["id"] == current), None)
        effort_now = ""
        for v in (models or {}).get("availableModels") or []:
            if isinstance(v, dict) and v.get("modelId") == current and isinstance(v.get("_meta"), dict):
                effort_now = str(v["_meta"].get("reasoningEffort") or "")
        self.options = [
            {"id": "model", "currentValue": current,
             "options": [{"value": r["id"], "name": r["label"]} for r in rows]},
            {"id": "effort", "currentValue": effort_now,
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
        """Grok's ``config_option_update``: ``model`` and ``reasoning_effort``."""
        for o in options if isinstance(options, list) else []:
            if not isinstance(o, dict):
                continue
            cid = {"model": "model", "reasoning_effort": "effort"}.get(str(o.get("id") or ""))
            if cid is None:
                continue
            values = [{"value": str(v.get("value"))} for v in o.get("options") or []
                      if isinstance(v, dict) and v.get("value")]
            if cid == "effort":
                values = [v for v in values if v["value"] in EFFORTS]
            mine = _config(self.options, cid)
            if mine:
                mine["currentValue"] = str(o.get("currentValue") or "")
                if cid == "effort":
                    mine["options"] = values
            else:
                self.options.append({"id": cid, "currentValue": str(o.get("currentValue") or ""),
                                     "options": values})

    async def _set_model(self, model: str) -> None:
        try:
            got = await self.request("session/set_model", {"sessionId": self.session_id, "modelId": model})
        except ControlRefused as exc:
            raise ControlRefused(f"Grok refused that model: {exc}") from None
        meta = (got or {}).get("_meta") if isinstance(got, dict) else None
        said = (meta or {}).get("model") if isinstance(meta, dict) else None
        if isinstance(said, dict) and "Err" in said:
            raise ControlRefused(f"Grok refused that model: {clip(said.get('Err'), 200)}")
        opt = _config(self.options, "model")
        if opt:
            opt["currentValue"] = model
        # The new model's own levels (its row says them).
        row = next((r for r in LAST_MODELS if r.get("id") == model), None)
        eff = _config(self.options, "effort")
        if eff and row is not None:
            eff["options"] = [{"value": e} for e in row.get("efforts") or []]

    async def _set_effort(self, effort: str) -> None:
        try:
            got = await self.request("session/set_config_option",
                                     {"sessionId": self.session_id, "configId": "reasoning_effort",
                                      "value": effort})
        except ControlRefused as exc:
            raise ControlRefused(f"Grok refused that effort: {exc}") from None
        self.on_config_update((got or {}).get("configOptions") if isinstance(got, dict) else None)
        eff = _config(self.options, "effort")
        if eff:
            eff["currentValue"] = effort

    # -- the tools' words -------------------------------------------------------------
    def tool_name(self, update: dict[str, Any]) -> str:
        meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
        tool = meta.get("x.ai/tool") if isinstance(meta.get("x.ai/tool"), dict) else {}
        return str(tool.get("name") or update.get("title") or "")

    def tool_kind(self, name: str, acp_kind: str = "") -> str:
        n = str(name or "").lower()
        if n in _BY_NAME:
            return _BY_NAME[n]
        if acp_kind in _BY_GROK_KIND:
            return _BY_GROK_KIND[acp_kind]
        if "__" in n:
            return "mcp"
        return _acp_tool_kind(name, acp_kind, by_name={})

    def session_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Only Grok's "allow all edits during this session" (memory only) —
        never an option Grok SAVES for the project."""
        return next((o for o in offered if o.get("optionId") in SESSION_ONLY_OPTIONS
                     and o.get("kind") == "allow_always"), None)

    def deny_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        """``reject-once`` only: Grok SAVES "No, and don't ask again"."""
        return next((o for o in offered if o.get("kind") == "reject_once"), None)

    # -- the user's side ----------------------------------------------------------
    def turn_started(self) -> None:
        self._ticks = 0
        self._notes.clear()
        self.close_after_stop = False

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """A note while it works: Grok's own interjection (``_x.ai/interject``,
        its Ctrl+Enter) — read at its next step. A second ``session/prompt``
        would only be QUEUED as a turn of its own (measured)."""
        if not (self.turn_open and self._message_prompt is not None):
            raise ControlRefused(W.STEER_TOO_LATE.format(label=self.LABEL))
        body = str(text or "")
        if files:
            listed = "\n".join(files)
            body = f"{body}\n\nFiles I attached:\n{listed}" if body.strip() else f"Files I attached:\n{listed}"
        # Grok echoes the note BEFORE this request's answer is read here: it is
        # registered first.
        self.steers[ref] = "sent"
        self._notes.append((ref, body))
        try:
            got = await self.request("_x.ai/interject", {"sessionId": self.session_id, "text": body})
        except ControlRefused as exc:
            self._notes[:] = [n for n in self._notes if n[0] != ref]
            self.steers.pop(ref, None)
            if "not found" in str(exc).lower():
                raise ControlRefused(W.STEER_UNSUPPORTED.format(label=self.LABEL)) from None
            raise ControlRefused(f"Grok did not take the note: {clip(str(exc), 200)}") from None
        except BaseException:
            self._notes[:] = [n for n in self._notes if n[0] != ref]
            self.steers.pop(ref, None)
            raise
        status = ((got or {}).get("result") or {}).get("status") if isinstance(got, dict) else None
        if status not in (None, "queued", "accepted", "ok"):
            self._notes[:] = [n for n in self._notes if n[0] != ref]
            self.steers.pop(ref, None)
            raise ControlRefused(f"Grok did not take the note ({clip(status, 60)}).")
        return "step"

    def stopping(self) -> None:
        """v1.416.0 review: the user pressed Stop. Called BEFORE a waiting card
        is answered "deny" for it: Grok ends its reply on that reject at once
        (``PermissionRejected``), before the cancel that follows — and that
        reply was ended by the Stop, never by a decline."""
        self._interrupting = True

    async def set_mode(self, mode: str) -> str:
        """A mode is the session's own ``_meta``, given when Grok opens (or
        reopens) it — so a change is a NEW process before the next message
        (``restart_needed``), which reopens the same conversation."""
        if MODES.get(mode, "ask") != MODES.get(self.mode, "ask"):
            self.restart_needed = True
        self.mode = mode
        return "next" if self.restart_needed or self.turn_open else "now"

    async def set_model(self, model: str) -> str:
        values = _values(_config(self.options, "model"))
        if values and model not in values:
            raise ControlRefused("Grok does not list that model — choose one from its list.")
        await self._set_model(model)
        self.model = model
        return "next" if self.turn_open else "now"

    async def set_effort(self, effort: str) -> str:
        if effort:
            offered = _values(_config(self.options, "effort"))
            if offered and effort not in offered:
                raise ControlRefused(f'This Grok model takes {", ".join(offered)} — not "{effort}".')
            await self._set_effort(effort)
        self.effort = effort
        return ("next" if self.turn_open else "now") if effort else "next"

    #: Set when a change can only apply to a NEW process.
    restart_needed = False
    #: v1.416.0: the service reads what Grok started BEFORE a Stop is sent —
    #: Grok's cancel ends the shell (``pwsh -Command …``, the Store pwsh: never
    #: a member of the Job) and leaves the command in it running with no
    #: parent (measured), where the stop's own read no longer finds it.
    notes_started_on_stop = True

    # -- Grok's side (ON THE LOOP) -----------------------------------------------------
    def on_extension(self, method: str, params: dict[str, Any]) -> None:
        # Only the two things the Code view needs; every other ``_x.ai/...``
        # notification (some carry the user's MCP config) is dropped unread.
        if method == "_x.ai/session/interjection":
            text = str(params.get("text") or "")
            for i, (ref, sent) in enumerate(self._notes):
                if sent == text:
                    del self._notes[i]
                    self.note_read(ref)
                    break
        elif method == "_x.ai/session_notification":
            u = params.get("update") if isinstance(params.get("update"), dict) else {}
            if u.get("sessionUpdate") == "model_changed":
                for cid, key in (("model", "model_id"), ("effort", "reasoning_effort")):
                    opt = _config(self.options, cid)
                    if opt and u.get(key):
                        opt["currentValue"] = str(u.get(key))

    def take_answer(self, result: dict[str, Any]) -> str:
        meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        for ours, theirs in (("input", "inputTokens"), ("output", "outputTokens"),
                             ("cache_read", "cachedReadTokens")):
            v = usage.get(theirs)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._usage[ours] = int(v)
        ticks = usage.get("costUsdTicks")
        if isinstance(ticks, (int, float)) and not isinstance(ticks, bool) and ticks > 0:
            self._ticks = int(ticks)
        used = meta.get("totalTokens")
        window = self._windows.get(str(meta.get("modelId") or self._current("model")))
        if isinstance(used, int) and not isinstance(used, bool) and used > 0 and window:
            from . import seen as _seen

            _seen.note_context(self, (used, window))
        return str(result.get("stopReason") or "end_turn")

    def stop_of(self, stop: str, result: dict[str, Any]) -> str:
        meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        if stop == "cancelled" and not self._interrupting \
                and meta.get("cancellationCategory") == "PermissionRejected":
            # A Deny ends Grok's reply (measured) — not the user's Stop.
            self.emit(make("notice", text=W.GROK_DECLINED_ENDS))
            return "completed"
        return super().stop_of(stop, result)

    def turn_cost(self) -> float | None:
        return round(self._ticks / USD_TICKS, 6) if self._ticks > 0 else None

    def end_turn_tools(self) -> None:
        """Grok's cancel says nothing more about a tool that was running: each
        is drawn as stopped, and a COMMAND among them keeps running (measured)
        — the service then closes Grok and ends the command it noted."""
        from .acp import _input_line

        if self._interrupting:
            for tid, (name, acp_kind, args) in list(self._tools.items()):
                kind = self.tool_kind(name, acp_kind)
                if kind == "command":
                    self.close_after_stop = True
                self.emit(make("tool", item=tid, kind=kind, name=clip(name, 120),
                               title=self.tool_title(name, kind, args), input=_input_line(args),
                               status="failed", output=STOPPED_TOOL))
        self._tools.clear()
