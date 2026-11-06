"""Codex in the Code view (v1.409.0): ``codex app-server`` (stdio JSON-RPC),
with ALL of its own tools on, NOT ephemeral (``codex resume <id>`` opens the
same thread), and NONE of ``codex_native.protocol.BUILTINS_OFF``.

MEASURED on this PC (codex 0.157.0, Windows 11, live, in scratch folders):

* ``sandbox: "workspace-write"`` and ``"read-only"`` CANNOT RUN A COMMAND
  here: the sandboxed spawn fails (``CreateProcessAsUserW failed: 5 (Access is
  denied.)`` / ``workspace-write sandbox has no writable roots``), even after
  the user allowed it, and a file write under workspace-write asks again as
  "command failed; retry without sandbox?". Only ``danger-full-access`` runs.
* ``approvalPolicy: "untrusted"`` with ``danger-full-access`` ASKS before every
  command (``item/commandExecution/requestApproval``) and every file change
  (``item/fileChange/requestApproval``); an allowed one then runs. That is the
  "Ask first" preset: the user's card is the gate, since the sandbox cannot be.
* ``on-request`` with ``danger-full-access`` asked NOTHING (the model never
  needs to escalate) — that is Full access in all but name, so it is not
  offered as "Auto edits". Codex has no "edits run, commands ask" setting that
  works here, so **Auto edits and Plan are not offered for Codex on Windows**.
  Elsewhere (not measured) Ask first is ``untrusted`` + ``workspace-write`` and
  Auto edits ``on-request`` + ``workspace-write``.
* Full access = ``never`` + ``danger-full-access`` (asks nothing).
* A decision is ``accept`` / ``acceptForSession`` / ``decline`` (v2) or
  ``approved`` / ``approved_for_session`` / ``denied`` (the legacy
  ``execCommandApproval`` / ``applyPatchApproval``).
* ``turn/interrupt`` answers ``{}`` and the turn completes with ``status:
  "interrupted"``. ``thread/resume`` keeps the thread id.
* A signed-out home: ``error`` notifications with ``willRetry: true`` and
  ``httpStatusCode: 401`` five times, then one with ``willRetry: false`` and
  the turn ``failed``.

MEASURED for v1.412.0 (codex 0.157.0, live; ``generate-json-schema``):

* ``turn/steer`` {threadId, expectedTurnId, input, clientUserMessageId}
  answers ``{turnId}`` and the note joins the RUNNING turn: a ``userMessage``
  item whose ``clientId`` is the one sent appears at the next step, and the
  model followed it in that turn's answer. A turn that already ended refuses
  it (``expectedTurnId`` no longer matches).
* ``localImage`` {path} input is SEEN (the model read a word drawn only in a
  PNG's pixels). The approval policy is a per-TURN setting (``turn/start``):
  a mode chosen mid-turn applies from the next message.

READ for v1.414.0 (fix), from codex 0.157.0's own ``app-server
generate-json-schema`` (``McpServerElicitationRequestParams`` / ``…Response``)
and its source at tag ``rust-v0.157.0`` (``core/src/mcp_tool_call.rs``,
``protocol/src/mcp_approval_meta.rs``) — found LIVE first: Codex with the
Memory box ticked asked before ``memory_search`` and the old code declined it:

* Before an MCP tool call under ``untrusted`` (Ask first) Codex sends
  ``mcpServer/elicitation/request`` {threadId, turnId, serverName, mode:
  "form", message: 'Allow the <server> MCP server to run tool "<tool>"?',
  requestedSchema: {type: object, properties: {}}, _meta:
  {codex_approval_kind: "mcp_tool_call", persist?: "session" | "always" |
  ["session", "always"], tool_title?, tool_description?, tool_params?,
  tool_params_display?}}. The tool's own ``mcpToolCall`` item has started
  BEFORE the request (``notify_mcp_tool_call_started`` runs first).
* It reads ``{"action": "accept"}`` as allow once, ``accept`` with
  ``_meta: {persist: "session"}`` as allow for this session, ``persist:
  "always"`` as a change to Codex's OWN config (never sent from here), and
  ``{"action": "decline"}`` as "user rejected MCP tool call".
* Full access (``never`` + full disk) never asks
  (``mcp_permission_prompt_is_auto_approved``).
* Anything else on that method (a server's own form with fields, a page to
  open) is not a yes/no question: declined, said in words.

READ for v1.418.0 (C4.2), from codex 0.157.0's own ``app-server
generate-json-schema`` (``ServerNotification.json``,
``v2/CommandExecutionOutputDeltaNotification.json``): a running command's
output arrives as ``item/commandExecution/outputDelta`` {threadId, turnId,
itemId, delta: string} before the item completes with its whole
``aggregatedOutput``. The row shows the newest part as it arrives
(``partial: true``, ≤ 4 updates a second); the completed item's output
replaces it. (``item/fileChange/outputDelta`` is in the schema too, marked
deprecated: "The server no longer emits this notification".)

READ for v1.420.0 (C1.9: is there an "edits run, commands ask" posture on
Windows?), from codex 0.157.0's own ``app-server generate-json-schema
--experimental`` (``v2/TurnStartParams.json``: ``AskForApproval``,
``SandboxPolicy``, ``ApprovalsReviewer``, ``permissions``,
``collaborationMode``; ``FileChangeRequestApprovalParams``,
``CommandExecutionRequestApprovalParams``) and ``codex --help`` — no model
call:

* ``approvalPolicy`` is ``untrusted`` | ``on-request`` | ``never`` (``codex
  --help`` lists only the last two for ``-a``) or ``{granular:
  {sandbox_approval, rules, mcp_elicitations, request_permissions?,
  skill_approval?}}``. NONE of them names file changes apart from commands:
  ``granular`` only switches whole KINDS of prompt off (a sandbox escape, a
  rule's prompt, a tool server's question), on top of on-request.
* ``sandboxPolicy`` is ``dangerFullAccess`` | ``readOnly`` |
  ``workspaceWrite`` | ``externalSandbox``; ``permissions`` (a named profile,
  ``permissionProfile/list``) picks a sandbox shape, never when to ask. So on
  Windows, where only full disk access can run a command (measured above),
  ``on-request`` asks nothing at all: nothing is blocked, so nothing needs to
  be asked (measured above: it asked NOTHING) — "Auto edits" would be Full
  access by another name, and ``untrusted`` asks before every file change
  (``item/fileChange/requestApproval`` {threadId, turnId, itemId, reason?,
  grantRoot?}) AND every command. Codex itself has no such posture here.
* Not built (a decision for the user): Iron Jarvis could answer Codex's
  ``item/fileChange/requestApproval`` itself under ``untrusted`` (the paths
  are on the ``fileChange`` item that starts before it), so edits inside the
  folder run while commands still ask. That is Iron Jarvis's own rule, not
  Codex's; it would need the same folder and protected-file checks as Chat's
  Accept edits (``core/auto_approve.sensitive_kind``).
* ``collaborationMode`` {mode: ``plan`` | ``default``} exists, marked
  EXPERIMENTAL, so Plan is still not offered for Codex.

So the mode table is unchanged; the reasons in :data:`HIDDEN_MODES` say the
real cause in plain words.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
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
    clip_tail,
    make,
    one_line,
    unified_new_file,
    with_full,
)

log = logging.getLogger(__name__)

HARNESS = "codex"
#: v1.418.0 (C4.2): a running command's output arriving
#: (``item/commandExecution/outputDelta``) updates its row at most this often
#: (seconds) — ≤ 4 a second; the row shows the newest part.
OUTPUT_EVERY_S = 0.25
#: How much of a running command's output the driver keeps to show.
OUTPUT_TAIL_CHARS = 4_000

_WINDOWS = sys.platform == "win32"

#: preset -> (approvalPolicy, sandbox). See the module doc for why.
MODES: dict[str, tuple[str, str]] = (
    {"ask": ("untrusted", "danger-full-access"), "full": ("never", "danger-full-access")}
    if _WINDOWS else
    {
        "ask": ("untrusted", "workspace-write"),
        "edits": ("on-request", "workspace-write"),
        "full": ("never", "danger-full-access"),
    }
)
#: Presets not offered for Codex here, and why.
HIDDEN_MODES: dict[str, str] = {
    # v1.420.0 (C1.9): the real causes, read from Codex's own settings list.
    "plan": "Codex's own plan mode is marked experimental in this version, so it is not offered here yet.",
    **({"edits": "On Windows, Codex can run commands only with its own file protection switched off, and "
                 "then it asks either before every file change and every command, or before none of them. "
                 "It has no setting where edits run on their own while commands still ask."}
       if _WINDOWS else {}),
}
#: v1.412.0: what each preset does, in plain words (the mode menu says it).
MODE_ABOUT: dict[str, str] = {
    "ask": "Asks before every command and every file change.",
    "edits": "Changes files in this folder without asking; asks before it runs a command.",
    "full": "Runs everything without asking.",
}
_SANDBOX_POLICY = {
    "danger-full-access": {"type": "dangerFullAccess"},
    "workspace-write": {"type": "workspaceWrite"},
    "read-only": {"type": "readOnly"},
}

REQUEST_TIMEOUT_S = 60.0
#: v1.426.0 (E4): how long the background ``skills/list`` (Codex's own `/`
#: menu: its skills, ``$<name>``) may take. It is asked once per process,
#: never awaited by the session's start: the conversation never waits on it.
SKILLS_WAIT_S = 15.0

_DECISION_V2 = {"allow": "accept", "allow_session": "acceptForSession", "deny": "decline"}
_DECISION_LEGACY = {"allow": "approved", "allow_session": "approved_for_session", "deny": "denied"}
#: v1.417.0: the requests that ask to run a command.
_COMMAND_METHODS = ("item/commandExecution/requestApproval", "execCommandApproval")


def _base(path: Any) -> str:
    return os.path.basename(str(path or "").replace("\\", "/")) or str(path or "")


def _command_of(item: dict[str, Any]) -> str:
    acts = item.get("commandActions") or []
    for a in acts:
        if isinstance(a, dict) and a.get("command"):
            return str(a["command"])
    cmd = item.get("command")
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def _change_rows(changes: Any) -> list[tuple[str, str, str]]:
    """``[(path, kind add|update|delete, unified diff)]``."""
    out = []
    if isinstance(changes, dict):  # legacy: path -> change
        changes = [{"path": p, **(c if isinstance(c, dict) else {})} for p, c in changes.items()]
    for c in changes or []:
        if not isinstance(c, dict):
            continue
        path = str(c.get("path") or "")
        kind = c.get("kind")
        kind = str((kind or {}).get("type") if isinstance(kind, dict) else (kind or c.get("type") or "update"))
        if kind == "add":
            diff = unified_new_file(path, str(c.get("diff") or c.get("content") or ""))
        elif kind == "delete":
            diff = f"--- a/{path.replace(chr(92), '/')}\n+++ /dev/null"
        else:
            diff = str(c.get("diff") or c.get("unified_diff") or "")
        out.append((path, kind, diff))
    return out


def offers_session(params: dict[str, Any]) -> bool:
    """Does this command request offer ``acceptForSession``? Only when its
    ``availableDecisions`` names it (the recorded request did not)."""
    offered = params.get("availableDecisions")
    if not isinstance(offered, list):
        return False
    return any(d == "acceptForSession" for d in offered)


#: v1.414.0 (fix): Codex's mark on an MCP tool-call approval
#: (``codex_protocol::mcp_approval_meta``).
MCP_APPROVAL_KIND_KEY = "codex_approval_kind"
MCP_APPROVAL_KIND = "mcp_tool_call"
MCP_PERSIST_KEY = "persist"
MCP_PERSIST_SESSION = "session"
_MCP_TOOL_IN_MESSAGE = re.compile(r'run tool "([^"]{1,200})"')


def mcp_tool_approval(p: dict[str, Any]) -> dict[str, Any] | None:
    """``{server, tool, args, can_session, title}`` when ``p`` (an
    ``mcpServer/elicitation/request``) is Codex asking before an MCP tool
    call — a ``form`` with NOTHING to fill and ``_meta.codex_approval_kind ==
    "mcp_tool_call"`` — else None (a server's own form: not a yes/no)."""
    meta = p.get("_meta")
    if not isinstance(meta, dict) or meta.get(MCP_APPROVAL_KIND_KEY) != MCP_APPROVAL_KIND:
        return None
    if p.get("mode") != "form":
        return None
    schema = p.get("requestedSchema")
    if not isinstance(schema, dict) or schema.get("properties"):
        return None
    persist = meta.get(MCP_PERSIST_KEY)
    offers = persist == MCP_PERSIST_SESSION or (isinstance(persist, list) and MCP_PERSIST_SESSION in persist)
    args = meta.get("tool_params")
    found = _MCP_TOOL_IN_MESSAGE.search(str(p.get("message") or ""))
    return {"server": str(p.get("serverName") or ""), "tool": found.group(1) if found else "",
            "args": args if isinstance(args, dict) else {}, "can_session": offers,
            "title": str(meta.get("tool_title") or "")}


def _mcp_input(args: Any) -> str:
    """An MCP call's arguments as JSON (Codex sends an object; a Python
    repr of it read "{'query': …}" on the step — live check)."""
    if isinstance(args, (dict, list)):
        import json

        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    return str(args or "")


def _mcp_output(item: dict[str, Any]) -> str:
    """An ``mcpToolCall`` item's result in words: Codex's own error message,
    else the text of the result's content blocks (never a Python dict)."""
    err = item.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")
    if err:
        return str(err)
    res = item.get("result")
    if isinstance(res, dict):
        texts = [str(b.get("text") or "") for b in res.get("content") or [] if isinstance(b, dict)]
        if any(texts):
            return "\n".join(t for t in texts if t)
        if res.get("structuredContent") is not None:
            import json

            return json.dumps(res["structuredContent"], ensure_ascii=False)
        return ""
    return str(res or "")


def _token_numbers(raw: Any) -> dict[str, int]:
    def n(k: str) -> int:
        v = (raw or {}).get(k) if isinstance(raw, dict) else None
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    return {"input": n("inputTokens"), "output": n("outputTokens"), "cache_read": n("cachedInputTokens")}


def _error_info(error: Any) -> tuple[str, int | None]:
    info = (error or {}).get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, str):
        return info, None
    if isinstance(info, dict) and info:
        key = next(iter(info))
        inner = info.get(key)
        status = inner.get("httpStatusCode") if isinstance(inner, dict) else None
        return key, status if isinstance(status, int) else None
    return "", None


#: The pictures Codex takes as ``localImage`` input (measured: it read a word
#: drawn only in a PNG's pixels).
PICTURE_EXTS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def user_input(text: str, files: list[str]) -> list[dict[str, Any]]:
    """A message's ``input`` items: the words, each picture as a
    ``localImage`` (Codex reads the file itself), any other file by path."""
    items: list[dict[str, Any]] = []
    others: list[str] = []
    for f in files:
        if os.path.splitext(f)[1].lower() in PICTURE_EXTS:
            items.append({"type": "localImage", "path": f})
        else:
            others.append(f)
    body = str(text or "")
    if others:
        listed = "\n".join(others)
        body = f"{body}\n\nFiles I attached:\n{listed}" if body.strip() else f"Files I attached:\n{listed}"
    items.insert(0, {"type": "text", "text": body})
    return items


#: v1.439.0: the Codex notifications said as one notice line each — shapes
#: READ from codex 0.157.0's own ``codex app-server generate-json-schema``
#: (``ServerNotification``): ``configWarning`` {summary, details?, path?,
#: range?}, ``warning`` {message, threadId?}, ``deprecationNotice`` {summary,
#: details?}, ``hook/completed`` {threadId, turnId?, run: HookRunSummary
#: {eventName, status: running|completed|failed|blocked|stopped,
#: statusMessage?, entries: [{kind: warning|stop|feedback|context|error,
#: text}], …}}.
CODEX_NOTICES: tuple[str, ...] = ("configWarning", "warning", "deprecationNotice", "hook/completed")
#: A hook run's status, as a person says it ("completed" only with a warning
#: or an error entry; otherwise it is quiet).
HOOK_STATUS_WORDS: dict[str, str] = {"failed": "failed", "blocked": "blocked the step",
                                     "stopped": "stopped the turn", "completed": "ran"}
#: A deprecation about how Iron Jarvis ITSELF calls Codex (measured on codex
#: 0.157.0 at every ``thread/resume``: "Full-history hydration is deprecated
#: for paginated threads; use `excludeTurns: true` …") — nothing the person
#: can act on: logged, never shown.
OWN_DEPRECATIONS: tuple[str, ...] = ("excludeTurns", "Full-history hydration")
#: The entry kinds a hook that otherwise ran well is worth a line for.
HOOK_SAYS_KINDS: tuple[str, ...] = ("error", "warning", "stop")


def _said(*parts: Any, cap: int = 400) -> str:
    """Codex's own words, joined (masked, one line, cut)."""
    words = [one_line(x, cap) for x in parts if isinstance(x, str) and x.strip()]
    words = [w if w[-1] in ".!?…" else w + "." for w in words if w]
    return one_line(" ".join(words), cap) if words else ""


def codex_notice(method: str, p: Any, thread: str = "") -> str:
    """v1.439.0: one of :data:`CODEX_NOTICES` as one notice line ("" = nothing
    worth a line). A warning or hook of ANOTHER thread (a sub-agent's) is
    not this conversation's."""
    p = p if isinstance(p, dict) else {}
    other = str(p.get("threadId") or "")
    if other and thread and other != thread:
        return ""
    if method == "configWarning":
        said = _said(p.get("summary"), p.get("details"))
        if not said:
            return ""
        where = one_line(p.get("path") or "", 200)
        return f"Codex found a problem in its settings{f' ({where})' if where else ''}: {said}"
    if method == "warning":
        said = _said(p.get("message"))
        return f"Codex warns: {said}" if said else ""
    if method == "deprecationNotice":
        if any(m in str(p.get("summary") or "") for m in OWN_DEPRECATIONS):
            log.info("Codex deprecation about how Iron Jarvis calls it: %s", one_line(p.get("summary"), 200))
            return ""
        said = _said(p.get("summary"), p.get("details"))
        return f"Codex says something is going away: {said}" if said else ""
    if method == "hook/completed":
        run = p.get("run") if isinstance(p.get("run"), dict) else {}
        status = str(run.get("status") or "")
        entries = [e for e in run.get("entries") or [] if isinstance(e, dict)]
        loud = [e for e in entries if str(e.get("kind") or "") in HOOK_SAYS_KINDS]
        if status == "completed" and not loud:
            return ""
        if status not in HOOK_STATUS_WORDS:
            return ""
        said = _said(run.get("statusMessage"), *(e.get("text") for e in loud[:2]))
        event = one_line(run.get("eventName") or "", 60)
        return (f"Codex's {event + ' ' if event else ''}hook {HOOK_STATUS_WORDS[status]}"
                f"{': ' + said if said else '.'}")
    return ""


#: v1.441.0 (g1): how often the background terminals are read again while any
#: runs, at most how many are listed, and the words.
TERMINALS_POLL_S = 30.0
TERMINALS_MAX = 50
TERMINAL_ALREADY_ENDED = "That command already ended."
SIGNED_IN_WORDS = "Signed in to {name}. Codex starts it again now."
SIGN_IN_FAILED_WORDS = "Signing in to {name} did not finish: {why}"


def terminal_rows(result: Any) -> list[dict[str, Any]]:
    """``thread/backgroundTerminals/list``'s answer (``{data: [{itemId,
    processId, command, cwd, osPid?, cpuPercent?, rssKb?}], nextCursor}``) as
    the Session tab's rows — the command shown the way a program's line is
    (a secret-named flag's value hidden, known shapes masked, then cut)."""
    from ..harness.process import _shown_command

    rows: list[dict[str, Any]] = []
    data = result.get("data") if isinstance(result, dict) else None
    for t in data or []:
        if not isinstance(t, dict):
            continue
        pid = str(t.get("processId") or "").strip()
        if not pid or len(pid) > 40:
            continue
        rows.append({"id": pid, "title": _shown_command(str(t.get("command") or "")) or "A command",
                     "kind": "command", "can_stop": True})
        if len(rows) >= TERMINALS_MAX:
            break
    return rows


def safe_sign_in_url(raw: Any) -> str:
    """The sign-in address Codex gave, only when it is one a browser may open:
    http(s), a host, no user name or password in it, not absurdly long."""
    from urllib.parse import urlsplit

    url = str(raw or "").strip()
    if not url or len(url) > 4_000 or any(ch.isspace() for ch in url):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return ""
    return url


class CodexDriver(Driver):
    harness = HARNESS

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._replies = ControlWaiter()
        self._n = 0
        self.turn_id = ""
        self._items: dict[str, dict[str, Any]] = {}
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._errored = False
        self._interrupting = False
        self._retry_said = False
        self.limit: Any = None
        #: v1.418.0 (C4.2): item id -> {"tail", "n", "sent", "dirty"} of a
        #: running command's output.
        self._out: dict[str, dict[str, Any]] = {}
        #: v1.426.0 (E4): the background reads this driver started (a task
        #: is kept here until it ends, never dropped by the garbage collector).
        self._bg: set[asyncio.Task] = set()
        #: v1.441.0 (g1): Codex's own background terminals (commands the model
        #: left running — ``thread/backgroundTerminals/list``) as last read:
        #: the rows, and how many (the reaper's ``background_tasks``).
        self._terminals: list[dict[str, Any]] = []
        self._terminal_count = 0
        #: The read that follows them while any runs (not kept: re-armed).
        self._terminal_poll: asyncio.Task | None = None
        #: v1.441.0 review: the ids of terminal reads in flight — their answers
        #: are Iron Jarvis's own, never "the tool was heard from".
        self._quiet_ids: set[int] = set()

    # -- v1.441.0 (g1): the Session tab's controls --------------------------------
    #: Codex's own ``mcpServer/oauth/login`` (``hosted/session_controls``).
    mcp_actions: frozenset[str] = frozenset({"sign_in"})

    def quiet_frame(self, obj: dict[str, Any]) -> bool:
        return "method" not in obj and obj.get("id") in self._quiet_ids

    @property
    def background_tasks(self) -> int:
        """v1.441.0 (g1): the background terminals Codex still runs — the
        service's reaper and pool keep a session that has any (the v1.418.0
        ``keeps_working`` rule), as Claude Code's open tasks do."""
        return int(self._terminal_count or 0)

    def _arm_terminal_poll(self) -> None:
        """Read the terminals now and every :data:`TERMINALS_POLL_S` while any
        runs (Codex says nothing when one ends by itself — measured). Never
        while a turn is open (the newest request must stay the turn's — see
        :meth:`after_restore`). One at a time."""
        if self._terminal_poll is not None and not self._terminal_poll.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def follow() -> None:
            while True:
                if not self.turn_open:
                    rows = await self._read_terminals()
                    if rows is None or not rows:
                        return
                await asyncio.sleep(TERMINALS_POLL_S)

        task = loop.create_task(follow())
        self._terminal_poll = task
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _read_terminals(self) -> list[dict[str, Any]] | None:
        """``thread/backgroundTerminals/list {threadId}`` (experimental; this
        driver asks for the experimental API) as rows. None = not read."""
        if not self.session_id:
            return None
        rid = self._n + 1  # request() takes exactly this id, before any await
        self._quiet_ids.add(rid)
        try:
            res = await self.request("thread/backgroundTerminals/list",
                                     {"threadId": self.session_id, "limit": TERMINALS_MAX})
        except Exception:  # noqa: BLE001 — a gone process or an older Codex: not known
            return None
        finally:
            self._quiet_ids.discard(rid)
        rows = terminal_rows(res)
        self._terminals = rows
        self._terminal_count = len(rows)
        return rows

    async def background_work(self) -> list[dict[str, Any]] | None:
        """v1.441.0 (g1): Codex's background terminals, each one its own
        ``thread/backgroundTerminals/terminate`` stops. While a turn runs the
        last reading is answered (no request is written mid-turn)."""
        if self.turn_open:
            return [dict(r) for r in self._terminals]
        rows = await self._read_terminals()
        if rows is None:
            return [dict(r) for r in self._terminals] if self._terminals else None
        if rows:
            self._arm_terminal_poll()
        return rows

    async def stop_background(self, work_id: str) -> str:
        """``thread/backgroundTerminals/terminate {threadId, processId}`` —
        MEASURED on 0.157.0 (a stand-in model server had the model start
        ``ping -n 120`` with ``exec_command``): ``{"terminated": true}`` and the
        list is empty after; an unknown id is an error ("invalid background
        terminal process id"). Answers the command."""
        pid = str(work_id or "")
        known = next((r for r in self._terminals if r.get("id") == pid), None)
        try:
            res = await self.request("thread/backgroundTerminals/terminate",
                                     {"threadId": self.session_id, "processId": pid})
        except ControlRefused as exc:
            if "invalid background terminal" in str(exc).lower():
                # v1.441.0 review: Codex's own words for a stale id (measured).
                raise ControlRefused(TERMINAL_ALREADY_ENDED) from None
            raise ControlRefused(f"Codex did not stop it: {exc}") from None
        if not (isinstance(res, dict) and res.get("terminated")):
            raise ControlRefused(TERMINAL_ALREADY_ENDED)
        self._terminals = [r for r in self._terminals if r.get("id") != pid]
        self._terminal_count = len(self._terminals)
        if not self.turn_open:
            await self._read_terminals()
        return str((known or {}).get("title") or "A command")

    async def mcp_action(self, server: str, action: str) -> dict[str, Any]:
        """v1.441.0 (g1): ``mcpServer/oauth/login {name, threadId}`` — MEASURED
        on 0.157.0 against a stand-in OAuth server (scratch ``CODEX_HOME``,
        ``mcp_oauth_credentials_store = "file"``): it answers
        ``{authorizationUrl}`` and waits on a loopback callback of its own;
        once the browser comes back, ``mcpServer/oauthLogin/completed {name,
        success, error}`` and Codex starts the server again in the same thread
        (``startupStatus`` starting -> ready). A stdio server is refused
        ("only supported for streamable HTTP servers"). Iron Jarvis only hands
        the address to the user's browser — it never signs in."""
        if action != "sign_in":
            raise ControlRefused("Codex cannot do that to one of its MCP servers here.")
        try:
            res = await self.request("mcpServer/oauth/login",
                                     {"name": str(server or ""), "threadId": self.session_id or None})
        except ControlRefused as exc:
            raise ControlRefused(f"Codex could not start signing in to {clip(server, 120)}: {exc}") from None
        url = safe_sign_in_url(res.get("authorizationUrl") if isinstance(res, dict) else None)
        if not url:
            raise ControlRefused("Codex did not give a sign-in page Iron Jarvis can open.")
        return {"url": url}

    def after_restore(self) -> None:
        """v1.422.0 (kept across a restart): ``turn/start``'s answer names
        the turn (Stop and a note need its id) and settles a waiter that has
        no kept form — while a turn is open, the newest request waits again,
        and an answer naming a turn is taken."""
        if self._terminal_count:
            self._arm_terminal_poll()
        if not (self.turn_open and self._n):
            return
        fut = self._replies.make(self._n)

        def took(f: asyncio.Future) -> None:
            if f.cancelled() or f.exception() is not None:
                return
            res = f.result()
            turn = res.get("turn") if isinstance(res, dict) else None
            if isinstance(turn, dict) and turn.get("id"):
                self.turn_id = str(turn["id"])

        fut.add_done_callback(took)

    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        return ["app-server", "--listen", "stdio://"]

    # -- JSON-RPC ---------------------------------------------------------------
    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._n += 1
        rid = self._n
        fut = self._replies.make(rid)
        await self.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT_S)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self.write(msg)

    async def respond(self, rid: Any, result: dict[str, Any]) -> None:
        await self.write({"jsonrpc": "2.0", "id": rid, "result": result})

    async def respond_error(self, rid: Any, message: str) -> None:
        await self.write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": message}})

    def _policy(self) -> tuple[str, str]:
        return MODES.get(self.mode) or MODES["ask"]

    async def open(self, *, session_id: str, resume: bool) -> None:
        from .. import __version__

        await self.request("initialize", {
            "clientInfo": {"name": "iron-jarvis", "title": "Iron Jarvis", "version": __version__},
            "capabilities": {"experimentalApi": True},
        })
        await self.notify("initialized")
        approval, sandbox = self._policy()
        params: dict[str, Any] = {"cwd": self.cwd, "approvalPolicy": approval, "sandbox": sandbox}
        if self.model:
            params["model"] = self.model
        if resume and session_id:
            res = await self.request("thread/resume", {"threadId": session_id, **params})
        else:
            res = await self.request("thread/start", {**params, "ephemeral": False})
        thread = (res or {}).get("thread") if isinstance(res, dict) else None
        tid = str((thread or {}).get("id") or session_id or "")
        if not tid:
            raise ControlRefused("Codex did not start a thread")
        self.session_id = tid
        self.on_session_id(tid)
        self.emit(make(
            "session", harness=HARNESS, harness_session_id=tid,
            model=clip((res or {}).get("model") or (thread or {}).get("model") or self.model, 120),
            cwd=clip(self.cwd, 500), mode=self.mode,
        ))
        await self._read_skills()

    # -- v1.426.0 (E4): Codex's own `/` menu ------------------------------------
    async def _read_skills(self) -> None:
        """Codex's own invocable items: its compact, and its SKILLS
        (``skills/list`` for this folder — what codex 0.157.0 really reads;
        its old ``prompts/*.md`` are not read any more, measured). The request
        is WRITTEN here, before any turn's (so the newest request id stays a
        turn's, which a restart relies on), and its answer is taken in the
        background: the session never waits on it, and no answer leaves the
        compact row alone. Never raises."""
        from .claude import LAST_COMMANDS
        from .tool_setup import CODEX_COMPACT_ROW

        self.commands = [dict(CODEX_COMPACT_ROW)]
        self._n += 1
        rid = self._n
        fut = self._replies.make(rid)
        try:
            await self.write({"jsonrpc": "2.0", "id": rid, "method": "skills/list",
                              "params": {"cwds": [self.cwd]} if self.cwd else {}})
        except Exception:  # noqa: BLE001 — a gone process: no menu beyond compact
            self._replies.settle(rid)
            return

        async def take() -> None:
            from .tool_setup import codex_skill_rows

            try:
                res = await asyncio.wait_for(fut, timeout=SKILLS_WAIT_S)
            except BaseException:  # noqa: BLE001 — no answer: the compact row stays
                self._replies.settle(rid)  # drop the waiter (a late answer finds none)
                return
            self.commands = [dict(CODEX_COMPACT_ROW), *codex_skill_rows(res)]
            LAST_COMMANDS[HARNESS] = list(self.commands)

        task = asyncio.get_running_loop().create_task(take())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # -- the user's side --------------------------------------------------------
    async def send_user(self, text: str, files: list[str]) -> None:
        from .tool_setup import CODEX_COMPACT_TEXT

        if text.strip() == CODEX_COMPACT_TEXT and not files:
            # v1.426.0 (E4): Codex's OWN compact (measured on 0.157.0:
            # ``thread/compact/start`` answers ``{}`` and runs as a turn of its
            # own — turn/started, a contextCompaction item, turn/completed).
            # The turn's id comes from ``turn/started`` (below).
            self.turn_open = True
            self._errored = False
            self._interrupting = False
            self._retry_said = False
            self._usage = {"input": 0, "output": 0, "cache_read": 0}
            await self.request("thread/compact/start", {"threadId": self.session_id})
            return
        items = user_input(text, files)
        approval, sandbox = self._policy()
        params: dict[str, Any] = {
            "threadId": self.session_id, "input": items, "approvalPolicy": approval,
            "sandboxPolicy": _SANDBOX_POLICY.get(sandbox, {"type": "dangerFullAccess"}),
        }
        if self.model:
            params["model"] = self.model
        if self.effort:
            params["effort"] = self.effort
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        self._retry_said = False
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        res = await self.request("turn/start", params)
        turn = (res or {}).get("turn") if isinstance(res, dict) else None
        self.turn_id = str((turn or {}).get("id") or self.turn_id)

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """v1.412.0: Codex's own ``turn/steer`` — the note joins the RUNNING
        turn (its ``clientUserMessageId`` is the note's ref; Codex says it read
        it with a ``userMessage`` item carrying that ``clientId``, measured)."""
        if not (self.turn_open and self.session_id and self.turn_id):
            raise ControlRefused(W.STEER_TOO_LATE.format(label=W.label(HARNESS)))
        self.steers[ref] = "sent"
        try:
            await self.request("turn/steer", {
                "threadId": self.session_id, "expectedTurnId": self.turn_id,
                "input": user_input(text, files), "clientUserMessageId": ref,
            })
        except ControlRefused as exc:
            # Codex had finished this turn (its expectedTurnId no longer
            # matches): nothing reached it.
            self.steers.pop(ref, None)
            raise ControlRefused(W.STEER_TOO_LATE.format(label=W.label(HARNESS))) from exc
        except BaseException:
            self.steers.pop(ref, None)
            raise
        return "step"

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        """Nothing to keep: Codex's session allow lives in Codex."""

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        """The JSON-RPC response for the user's answer — or AskRefused,
        before anything is sent."""
        method = ask.data.get("method")
        if decision == "allow_commands":
            # v1.417.0 (C1.1): Codex has no whole-tool rule of its own; the
            # nearest it offers is its session-wide accept for THIS command
            # (``acceptForSession``) — taken only when the request lists it.
            if method not in _COMMAND_METHODS or not ask.can_session:
                raise AskRefused(W.ALL_COMMANDS_NOT_OFFERED.format(label=W.label(HARNESS)))
            decision = "allow_session"
        if decision == "allow_session" and not ask.can_session:
            raise AskRefused("Codex offers no session-wide allow for this one — allow it once.")
        if method in ("execCommandApproval", "applyPatchApproval"):
            result: dict[str, Any] = {"decision": _DECISION_LEGACY[decision]}
        elif method == "item/permissions/requestApproval":
            if decision == "deny":
                result = {"permissions": {}}
            else:
                result = {
                    "permissions": ask.data.get("permissions") or {},
                    "scope": "session" if decision == "allow_session" else "turn",
                }
        elif method == "mcpServer/elicitation/request":
            # Never "always": that writes Codex's own config for good.
            if decision == "deny":
                result = {"action": "decline"}
            elif decision == "allow_session":
                result = {"action": "accept", "_meta": {MCP_PERSIST_KEY: MCP_PERSIST_SESSION}}
            else:
                result = {"action": "accept"}
        elif method == "item/tool/requestUserInput":
            if decision == "deny":
                result = {"answers": {}}
            else:
                result = {"answers": self._user_input_answers(ask, answers)}
        else:
            result = {"decision": _DECISION_V2[decision]}
        return {"jsonrpc": "2.0", "id": ask.native, "result": result}

    def _user_input_answers(self, ask: Ask, answers: Any) -> dict[str, Any]:
        qs = ask.data.get("questions") or []
        out: dict[str, Any] = {}
        for i, q in enumerate(qs):
            qid = str(q.get("id") or i)
            text = str(q.get("question") or "")
            if isinstance(answers, list):
                got = answers[i] if i < len(answers) else None
            elif isinstance(answers, dict):
                got = answers.get(qid, answers.get(text, answers.get(str(i))))
            else:
                got = None
            if got is None:
                continue
            vals = [str(g) for g in got] if isinstance(got, list) else [str(got)]
            vals = [v for v in vals if v.strip()]
            if vals:
                out[qid] = {"answers": vals}
        if not out:
            raise AskRefused("Choose an answer for the question first.")
        return out

    async def interrupt(self) -> None:
        if not (self.session_id and self.turn_id):
            return
        self._interrupting = True
        try:
            await self.request("turn/interrupt", {"threadId": self.session_id, "turnId": self.turn_id})
        except asyncio.TimeoutError:
            pass

    async def set_mode(self, mode: str) -> str:
        self.mode = mode
        return "next"

    async def set_model(self, model: str) -> str:
        self.model = model
        return "next"

    async def rename(self, title: str) -> None:
        """v1.439.0: Codex's own ``thread/name/set`` {threadId, name} (codex
        0.157.0's schema: ThreadSetNameParams) — the name ``codex resume``
        and Codex's own picker show."""
        name = str(title or "").strip()
        if not name or not self.session_id:
            return
        await self.request("thread/name/set", {"threadId": self.session_id, "name": name})

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._replies.fail_all(HarnessGone("Codex stopped"))
        # v1.441.0 (g1): its background terminals ended with it.
        self._terminals = []
        self._terminal_count = 0

    # -- the harness's side (ON THE LOOP) ---------------------------------------
    def on_message(self, obj: dict[str, Any]) -> None:
        method = obj.get("method")
        if method is None and "id" in obj:  # a reply to our request
            if "error" in obj:
                err = obj.get("error") or {}
                self._replies.settle(obj["id"], error=ControlRefused(clip(err.get("message") or "refused", 300)))
            else:
                self._replies.settle(obj["id"], obj.get("result") or {})
            return
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        if method and "id" in obj:
            self._on_server_request(str(method), obj["id"], params)
            return
        if method:
            self._on_notification(str(method), params)

    def _on_server_request(self, method: str, rid: Any, p: dict[str, Any]) -> None:
        ask_id = new_ask_id()
        if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            item = self._items.get(str(p.get("itemId") or "")) or {}
            command = _command_of(p) or _command_of(item)
            network = isinstance(p.get("networkApprovalContext"), dict)
            # "Allow for this session" ONLY when Codex offers it for THIS
            # request (`availableDecisions`, v2); the legacy request has no list.
            can_session = offers_session(p) if method != "execCommandApproval" else True
            ev = make("approval", request_id=ask_id, kind="network" if network else "command",
                      title=f"Run {one_line(command, 90)}" if command else "Run a command",
                      detail=clip(p.get("reason") or "", DETAIL_CHARS), command=clip(command, 2000),
                      can_session=can_session)
            if not can_session:
                ev["once_only"] = True
            self.raise_ask(Ask(ask_id, "approval", "shell", rid, {"method": method}, ev, ev["at"], can_session))
            return
        if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            item = self._items.get(str(p.get("itemId") or "")) or {}
            rows = _change_rows(p.get("fileChanges") if method == "applyPatchApproval" else item.get("changes"))
            path = rows[0][0] if rows else ""
            kinds = {r[1] for r in rows}
            title = (f"Create {_base(path)}" if kinds == {"add"} else f"Change {_base(path)}") if path else "Change files"
            if len(rows) > 1:
                title = f"Change {len(rows)} files"
            ev = make("approval", request_id=ask_id, kind="edit", title=title,
                      detail=clip(p.get("reason") or "", DETAIL_CHARS), can_session=True)
            if path:
                ev["path"] = clip(path, 500)
            diff = "\n".join(r[2] for r in rows if r[2])
            if diff:
                ev["diff"] = clip(diff, DIFF_CHARS)
            self.raise_ask(Ask(ask_id, "approval", "apply_patch", rid, {"method": method}, ev, ev["at"], True))
            return
        if method == "item/permissions/requestApproval":
            ev = make("approval", request_id=ask_id, kind="other", title="Codex asks for more access",
                      detail=clip(p.get("reason") or str(p.get("permissions") or ""), DETAIL_CHARS),
                      can_session=True)
            self.raise_ask(Ask(ask_id, "approval", "permissions", rid,
                               {"method": method, "permissions": p.get("permissions") or {}}, ev, ev["at"], True))
            return
        if method == "item/tool/requestUserInput":
            raw = [q for q in p.get("questions") or [] if isinstance(q, dict)]
            qs = []
            for q in raw:
                row: dict[str, Any] = {
                    "question": clip(q.get("question") or "", 500),
                    "options": [{"label": clip(o.get("label") or "", 120),
                                 **({"description": clip(o.get("description"), 300)} if o.get("description") else {})}
                                for o in q.get("options") or [] if isinstance(o, dict)],
                    "multi": False,
                }
                if q.get("header"):
                    row["header"] = clip(q.get("header"), 60)
                qs.append(row)
            ev = make("question", request_id=ask_id, questions=qs)
            self.raise_ask(Ask(ask_id, "question", "request_user_input", rid,
                               {"method": method, "questions": raw}, ev, ev["at"]))
            return
        if method == "mcpServer/elicitation/request":
            call = mcp_tool_approval(p)
            if call is None:
                server = one_line(p.get("serverName"), 60) or "An app"
                self.emit(make("notice", text=W.MCP_FORM_DECLINED.format(server=server)))
                asyncio.ensure_future(self._safe(self.respond(rid, {"action": "decline"})))
                return
            # v1.414.0 (fix): Codex asking before an MCP tool call is a card.
            # The tool's own item started first: it names the tool and the
            # arguments when Codex's message and _meta do not.
            server, tool, args = call["server"], call["tool"], call["args"]
            item = next((it for it in reversed(list(self._items.values()))
                         if it.get("type") == "mcpToolCall" and str(it.get("server") or "") == server
                         and (not tool or it.get("tool") == tool)), None)
            if item is not None:
                tool = tool or str(item.get("tool") or "")
                if not args and isinstance(item.get("arguments"), dict):
                    args = item["arguments"]
            tool = tool or call["title"]
            name = f"{server}.{tool}" if tool else server
            can_session = bool(call["can_session"])
            ev = make("approval", request_id=ask_id, kind="tool", title=W.mcp_call_title(server, tool, args),
                      detail=clip(W.mcp_args_lines(args), DETAIL_CHARS), can_session=can_session,
                      tool=clip(name, 120))
            if not can_session:
                ev["once_only"] = True
            self.raise_ask(Ask(ask_id, "approval", clip(name, 120) or "mcp", rid, {"method": method}, ev,
                               ev["at"], can_session))
            return
        asyncio.ensure_future(self._safe(self.respond_error(rid, "Iron Jarvis does not handle this request")))

    async def _safe(self, coro: Any) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 — a gone process: nothing to answer
            pass

    def _on_notification(self, method: str, p: dict[str, Any]) -> None:
        if method == "item/started":
            self._item_started(p.get("item") or {})
        elif method == "item/completed":
            self._item_completed(p.get("item") or {})
        elif method == "item/agentMessage/delta":
            if p.get("delta"):
                self.emit(make("text", item=str(p.get("itemId") or ""), delta=clip(p["delta"], TEXT_CHARS)))
        elif method == "item/commandExecution/outputDelta":
            self._output_delta(str(p.get("itemId") or ""), str(p.get("delta") or ""))
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            if p.get("delta"):
                self.emit(make("thinking", item=str(p.get("itemId") or ""), delta=clip(p["delta"], TEXT_CHARS)))
        elif method == "turn/plan/updated":
            steps = []
            for s in p.get("plan") or []:
                if isinstance(s, dict):
                    st = {"inProgress": "in_progress", "completed": "completed"}.get(str(s.get("status")), "pending")
                    steps.append({"text": clip(s.get("step") or "", 300), "status": st})
            self.emit(make("plan", steps=steps))
        elif method == "thread/tokenUsage/updated":
            from . import seen as _seen  # v1.414.0: how full, as Codex says

            _seen.note_context(self, _seen.codex_context(p.get("tokenUsage")))
            last = _token_numbers(((p.get("tokenUsage") or {}).get("last")))
            for k in self._usage:
                self._usage[k] += last[k]
        elif method == "error":
            self._on_error(p)
        elif method == "turn/completed":
            self._turn_completed(p.get("turn") or {})
        elif method == "turn/started":
            # v1.426.0 (E4): a compaction's turn has no ``turn/start`` answer to
            # name it (Stop needs its id); for any other turn it is the same id.
            # v1.426.0 review (lane B): only THIS session's thread — Codex has
            # sub-agent threads, and a turn of another thread must never become
            # the turn Stop and a note are sent to (a frame naming no thread is
            # taken, as before).
            turn = p.get("turn")
            thread = str(p.get("threadId") or "")
            if (self.turn_open and isinstance(turn, dict) and turn.get("id")
                    and (not thread or not self.session_id or thread == self.session_id)):
                self.turn_id = str(turn["id"])
        elif method == "mcpServer/oauthLogin/completed":
            # v1.441.0 (g1): a sign-in started from the Session tab ended.
            thread = str(p.get("threadId") or "")
            if not thread or not self.session_id or thread == self.session_id:
                name = clip(str(p.get("name") or "the server"), 120)
                if p.get("success"):
                    self.emit(make("notice", text=SIGNED_IN_WORDS.format(name=name)))
                else:
                    why = one_line(p.get("error") or "no reason given", 300)
                    self.emit(make("notice", text=SIGN_IN_FAILED_WORDS.format(name=name, why=why)))
        elif method == "mcpServer/startupStatus/updated":
            # v1.426.0 (E4): its own MCP servers starting, as it says them.
            from .tool_setup import codex_update, note_servers

            note_servers(self, codex_update(self.mcp_servers, p))
        elif method == "thread/compacted":
            self.emit(make("notice", text="Codex compacted the conversation to make room."))
        elif method == "model/rerouted":
            self.emit(make("notice", text="Codex moved this turn to another model."))
        elif method in CODEX_NOTICES:
            # v1.439.0: Codex's own warnings and a hook that did not go well,
            # one masked line each (a hook that ran and said nothing: none).
            text = codex_notice(method, p, self.session_id)
            if text:
                self.emit(make("notice", text=text))
        elif method == "account/rateLimits/updated":
            from . import seen as _seen  # v1.414.0: the Usage tab's windows

            _seen.note_windows(self, *_seen.codex_windows(p.get("rateLimits")))
            reached = ((p.get("rateLimits") or {}).get("rateLimitReachedType"))
            if reached:
                self.emit(make("notice", text=f"Codex says this account reached its {clip(reached, 40)} limit."))

    def _item_started(self, item: dict[str, Any]) -> None:
        iid = str(item.get("id") or "")
        t = item.get("type")
        if t == "userMessage":
            # v1.412.0: a note joined the turn (Codex echoes its clientId).
            ref = str(item.get("clientId") or "")
            if ref:
                self.note_read(ref)
            return
        self._items[iid] = item
        ev = self._tool_event(item, "running")
        if ev is not None:
            self.emit(ev)

    def _item_completed(self, item: dict[str, Any]) -> None:
        iid = str(item.get("id") or "")
        t = item.get("type")
        if t == "agentMessage":
            if str(item.get("text") or "").strip():
                self.emit(make("text_done", item=iid, text=clip(item["text"], TEXT_CHARS)))
            return
        if t == "contextCompaction":
            self.emit(make("notice", text="Codex compacted the conversation to make room."))
            return
        self._items[iid] = item
        self._out.pop(iid, None)
        status = str(item.get("status") or "completed")
        failed = status in ("failed", "declined") or (
            t == "commandExecution" and isinstance(item.get("exitCode"), int) and item["exitCode"] != 0
        )
        ev = self._tool_event(item, "failed" if failed else "done")
        if ev is not None:
            self.emit(ev)

    def _output_delta(self, iid: str, delta: str) -> None:
        """v1.418.0 (C4.2): a running command's output as it arrives — its
        row updated with the newest part (``partial``), ≤ 4 times a second."""
        if not iid or not delta:
            return
        item = self._items.get(iid)
        if not isinstance(item, dict) or item.get("type") != "commandExecution":
            return
        st = self._out.setdefault(iid, {"tail": "", "n": 0, "sent": None, "dirty": False})
        st["tail"] = (st["tail"] + delta)[-OUTPUT_TAIL_CHARS:]
        st["n"] += len(delta)
        st["dirty"] = True
        now = time.monotonic()
        if st["sent"] is not None and now - st["sent"] < OUTPUT_EVERY_S:
            return
        ev = self._tool_event(item, "running")
        if ev is None:
            return
        ev["output"] = clip_tail(st["tail"], OUTPUT_CHARS)
        ev["partial"] = True
        st["sent"] = now
        st["dirty"] = False
        self.emit(ev)

    def _tool_event(self, item: dict[str, Any], status: str) -> dict[str, Any] | None:
        iid = str(item.get("id") or "")
        t = item.get("type")
        if t == "commandExecution":
            cmd = _command_of(item)
            ev = make("tool", item=iid, kind="command", name="shell",
                      title=f"Run {one_line(cmd, 90)}" if cmd else "Run a command",
                      input=clip(cmd, INPUT_CHARS), status=status)
            if status != "running":
                out = str(item.get("aggregatedOutput") or "")
                if item.get("status") == "declined":
                    out = out or "The user said no to this."
                # v1.418.0 (C4.4): the first part on screen, the rest kept.
                with_full(ev, input_text=cmd, output_text=out)
            return ev
        if t == "fileChange":
            rows = _change_rows(item.get("changes"))
            path = rows[0][0] if rows else ""
            kinds = {r[1] for r in rows}
            kind = "write" if kinds == {"add"} else "edit"
            title = (("Write " if kind == "write" else "Edit ") + _base(path)) if path else "Change files"
            ev = make("tool", item=iid, kind=kind, name="apply_patch", title=title,
                      input=clip(", ".join(r[0] for r in rows), INPUT_CHARS), status=status)
            if path:
                ev["path"] = clip(path, 500)
            diff = "\n".join(r[2] for r in rows if r[2])
            if diff:
                ev["diff"] = clip(diff, DIFF_CHARS)
            if status == "failed" and item.get("status") == "declined":
                ev["output"] = "The user said no to this."
            return ev
        if t == "mcpToolCall":
            name = f"{item.get('server') or ''}.{item.get('tool') or ''}"
            ev = make("tool", item=iid, kind="mcp", name=clip(name, 120),
                      title=f"Use {item.get('server') or 'an app'} › {item.get('tool') or 'a tool'}",
                      input=clip(_mcp_input(item.get("arguments")), INPUT_CHARS), status=status)
            if status != "running":
                with_full(ev, input_text=_mcp_input(item.get("arguments")), output_text=_mcp_output(item))
            return ev
        if t == "webSearch":
            q = one_line(item.get("query"), 80)
            return make("tool", item=iid, kind="web", name="web_search",
                        title=f"Search the web for {q}" if q else "Search the web",
                        input=clip(item.get("query") or "", INPUT_CHARS), status=status)
        if t == "collabAgentToolCall":
            return make("tool", item=iid, kind="task", name=clip(item.get("tool") or "agent", 120),
                        title="Hand a task to a helper", input=clip(item.get("prompt") or "", INPUT_CHARS),
                        status=status)
        return None

    def _on_error(self, p: dict[str, Any]) -> None:
        err = p.get("error") or {}
        said = str(err.get("message") or "")
        detail = str(err.get("additionalDetails") or "")
        kind, status = _error_info(err)
        if p.get("willRetry"):
            # Once per turn: a signed-out home retries ten times (measured).
            if not self._retry_said:
                self._retry_said = True
                self.emit(make("notice", text=f"Codex lost its connection and is retrying ({one_line(said, 80)})."))
            return
        self._fail(said + (f" ({detail})" if detail else ""), kind, status)

    def _fail(self, said: str, kind: str, status: int | None) -> None:
        from ..terminals.limit_state import PaneLimit

        if self._errored:
            return
        self._errored = True
        if kind == "unauthorized" or status == 401 or "401 Unauthorized" in said:
            self.emit(W.sign_in_error(HARNESS, said))
        elif kind in ("usageLimitExceeded", "rateLimitExceeded"):
            self.limit = PaneLimit(line=one_line(said, 300))
            self.emit(W.limit_error(HARNESS, said))
        else:
            self.emit(W.failure_for(HARNESS, said))

    def _turn_completed(self, turn: dict[str, Any]) -> None:
        status = str(turn.get("status") or "completed")
        if status == "failed" and not self._errored:
            err = turn.get("error") or {}
            kind, code = _error_info(err)
            self._fail(str(err.get("message") or "Codex could not finish"), kind, code)
        from . import seen as _seen

        self.emit(_seen.attach(make("usage", **self._usage, list_price=True), self))
        stop = {"completed": "completed", "interrupted": "interrupted", "failed": "failed"}.get(status, "failed")
        if self._interrupting and stop != "completed":
            stop = "interrupted"
        self._interrupting = False
        self.turn_open = False
        self.emit(make("done", stop=stop))
        # v1.441.0 (g1): a command the model left running is a background
        # terminal now — read them (and follow them while any runs).
        self._arm_terminal_poll()
