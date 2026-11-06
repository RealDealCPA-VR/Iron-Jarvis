"""The Agent Client Protocol in the Code view (v1.416.0): what OpenCode
(``opencode acp``, v1.413.0) and Grok (``grok agent stdio``, v1.416.0) share.

ACP is JSON-RPC 2.0, one JSON object per line on the process's own
stdin/stdout. Both tools speak the same core (measured on this PC: opencode
1.18.18 and grok 1.0.70 — see each tool's module docstring for its own
recordings):

* ``initialize`` -> ``agentCapabilities`` (``loadSession``,
  ``promptCapabilities.image``, ``mcpCapabilities.http``);
* ``session/prompt`` answers only when the turn ends (``stopReason``), and
  everything in between is ``session/update`` notifications:
  ``agent_message_chunk``, ``agent_thought_chunk``, ``tool_call`` /
  ``tool_call_update`` {toolCallId, title, kind, status, rawInput, rawOutput,
  content (``diff`` / ``content`` blocks), locations}, ``plan``,
  ``available_commands_update``;
* ``session/request_permission`` {toolCall, options [{optionId, kind}]} —
  answered ``{outcome: {outcome: "selected", optionId}}`` with an option the
  tool OFFERED for that very request, never one it did not;
* ``session/cancel`` (a notification) stops the turn.

:class:`AcpDriver` is that shared core: requests and their answers, a turn's
prompt(s), permission cards, the update stream drawn as the Code view's
events, and the turn's end. Each tool's own module says the rest — how it is
started, how a session opens and reopens, its modes, models and effort, which
of its options is session-wide, its notes while it works and its usage.
Iron Jarvis is no editor: ``clientCapabilities`` fs/terminal are FALSE and a
tool's ``fs/*`` / ``terminal/*`` request is answered "not handled" (the tool
reads, writes and runs commands with its OWN tools, exactly as in a terminal).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
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

def _hint(command: dict[str, Any]) -> str:
    """v1.439.0: an ACP command's argument hint (``input: {hint}``), or ""."""
    given = command.get("input")
    hint = given.get("hint") if isinstance(given, dict) else None
    return str(hint).strip() if isinstance(hint, str) else ""


#: The ACP version both drivers speak (opencode 1.18.18 and grok 1.0.70 answer 1).
PROTOCOL_VERSION = 1
REQUEST_TIMEOUT_S = 60.0
#: A running command's output is redrawn at most this often.
UPDATE_EVERY_S = 1.0
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 8


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
    return str(args.get("filePath") or args.get("filepath") or args.get("path") or args.get("file_path")
               or args.get("target_file") or "")


def tool_kind(name: str, acp_kind: str = "", by_name: dict[str, str] | None = None) -> str:
    n = str(name or "").lower()
    names = by_name if by_name is not None else _BY_NAME
    if n in names:
        return names[n]
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
        if str(name).lower() in ("list", "ls", "list_dir"):
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


def _config(options: Any, cid: str) -> dict[str, Any]:
    for o in options if isinstance(options, list) else []:
        if isinstance(o, dict) and o.get("id") == cid:
            return o
    return {}


def _values(option: dict[str, Any]) -> list[str]:
    return [str(v.get("value")) for v in option.get("options") or [] if isinstance(v, dict) and v.get("value")]


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


#: v1.414.0: a tool that does not take an HTTP MCP server over ACP.
JARVIS_TOOLS_NOT_TAKEN = (
    "This {label} does not take an MCP server from Iron Jarvis, so the Iron Jarvis "
    "tools you turned on are not in this session."
)


# --------------------------------------------------------------------------- #
# The shared driver
# --------------------------------------------------------------------------- #
class AcpDriver(Driver):
    """The ACP core both OpenCode and Grok speak. A subclass says the rest
    (its ``argv``, ``open``, modes, models, effort, notes, usage, words)."""

    #: How a person names the tool (words on cards and notices).
    LABEL = "The coding tool"

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
        #: v1.414.0: ACP ``mcpServers`` entries (Iron Jarvis's own tools for
        #: this session, set by the service when the user turned them on).
        self.mcp_servers: list[dict[str, Any]] = []

    # -- what each tool says ------------------------------------------------------
    def failure_event(self, said: str) -> dict[str, Any]:  # pragma: no cover - per tool
        raise NotImplementedError

    def tool_kind(self, name: str, acp_kind: str = "") -> str:
        return tool_kind(name, acp_kind)

    def tool_title(self, name: str, kind: str, args: dict[str, Any]) -> str:
        return tool_title(name, kind, args)

    def tool_name(self, update: dict[str, Any]) -> str:
        """The tool's NAME from its FIRST record (OpenCode: the title)."""
        return str(update.get("title") or "")

    def session_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The option that allows for the rest of THIS session (only ever one
        the tool offered), or None — the card is then Allow once only."""
        return next((o for o in offered if o.get("kind") == "allow_always"), None)

    def deny_option(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The option a Deny chooses (OpenCode: reject once, else any reject)."""
        pick = next((o for o in offered if o.get("kind") == "reject_once"), None)
        return pick or next((o for o in offered if str(o.get("kind") or "").startswith("reject")), None)

    def take_answer(self, result: dict[str, Any]) -> str:
        """The turn's stop from the prompt's answer (and its usage, kept)."""
        usage = result.get("usage")
        for ours, theirs in (("input", "inputTokens"), ("output", "outputTokens"),
                             ("cache_read", "cachedReadTokens")):
            v = usage.get(theirs) if isinstance(usage, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._usage[ours] = int(v)
        return str(result.get("stopReason") or "end_turn")

    def on_extension(self, method: str, params: dict[str, Any]) -> None:
        """A notification outside ``session/update`` (the tool's own)."""

    def turn_cost(self) -> float | None:
        """The turn's cost the TOOL reported (USD), or None."""
        if self._cost is not None and self._cost_start is not None and self._cost > self._cost_start:
            return round(self._cost - self._cost_start, 6)
        return None

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

    async def initialize(self) -> dict[str, Any]:
        """``initialize`` — Iron Jarvis is not an editor (fs / terminal off).
        Answers the agent's capabilities and keeps what the driver needs."""
        from .. import __version__

        got = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            # The tool reads, writes and runs commands with its OWN tools,
            # exactly as in a terminal.
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "iron-jarvis", "title": "Iron Jarvis", "version": __version__},
        })
        caps = (got or {}).get("agentCapabilities") if isinstance(got, dict) else {}
        caps = caps if isinstance(caps, dict) else {}
        self.can_load = bool(caps.get("loadSession"))
        prompt_caps = caps.get("promptCapabilities") if isinstance(caps.get("promptCapabilities"), dict) else {}
        self.takes_pictures = bool(prompt_caps.get("image"))
        return caps

    def acp_servers(self, caps: dict[str, Any]) -> list[dict[str, Any]]:
        """v1.414.0: Iron Jarvis's own tools for THIS session, given the ACP
        way — only to a tool that says it takes an HTTP MCP server."""
        mcp_caps = caps.get("mcpCapabilities") if isinstance(caps.get("mcpCapabilities"), dict) else {}
        servers = list(self.mcp_servers) if mcp_caps.get("http") else []
        if self.mcp_servers and not servers:
            self.emit(make("notice", text=JARVIS_TOOLS_NOT_TAKEN.format(label=self.LABEL)))
        return servers

    # -- the user's side ----------------------------------------------------------
    async def _prompt_blocks(self, text: str, files: list[str]) -> list[dict[str, Any]]:
        if self.takes_pictures:
            images, others = await asyncio.to_thread(_read_images, files)
            for f in others:
                if os.path.splitext(f)[1].lower() in IMAGE_TYPES:
                    self.emit(make("notice", text=W.PICTURE_AS_PATH.format(
                        name=os.path.basename(f), label=self.LABEL,
                        why=f"over the {MAX_IMAGE_BYTES // (1024 * 1024)} MB or {MAX_IMAGES} pictures sent "
                            "in one message")))
        else:
            images, others = [], list(files)
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_TYPES:
                    self.emit(make("notice", text=W.PICTURE_AS_PATH.format(
                        name=os.path.basename(f), label=self.LABEL,
                        why=f"this {self.LABEL} takes no pictures")))
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

    def after_restore(self) -> None:
        """v1.422.0 (kept across a restart): the turn ends when the tool
        ANSWERS its prompts — the answer settles a waiter that has no kept
        form, so each prompt still open waits again."""
        for rid in list(self._pending):
            if isinstance(rid, int):
                fut = self._replies.make(rid)
                fut.add_done_callback(lambda f, rid=rid: self._prompt_answered(rid, f))

    async def send_user(self, text: str, files: list[str]) -> None:
        blocks = await self._prompt_blocks(text, files)
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        self._stop = ""
        self._usage = {"input": 0, "output": 0, "cache_read": 0}
        self._cost_start = self._cost
        self._pending.clear()
        self.turn_started()
        rid = self._start_prompt(blocks)
        self._message_prompt = rid
        await self.write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": blocks}})

    def turn_started(self) -> None:
        """A new turn: a subclass resets what it keeps per turn."""

    def note_answered(self, ref: str, stop: str, error: BaseException | None) -> None:
        """A note sent as a prompt of its own got its answer (OpenCode)."""

    def _prompt_answered(self, rid: int, fut: asyncio.Future) -> None:
        ref = self._pending.pop(rid, None)
        if fut.cancelled():
            return
        exc = fut.exception()
        result = fut.result() if exc is None else None
        if ref:
            stop = str((result or {}).get("stopReason") or "end_turn") if isinstance(result, dict) else ""
            self.note_answered(ref, stop, exc)
        if rid == self._message_prompt:
            if exc is not None:
                if isinstance(exc, ControlRefused):
                    self._fail(str(exc))
                    self._stop = "failed"
                else:
                    return  # the process went: the service ends the turn
            else:
                stop = self.take_answer(result if isinstance(result, dict) else {})
                self._stop = self.stop_of(stop, result if isinstance(result, dict) else {})
                if stop == "max_tokens":
                    self.emit(make("notice", text="The answer reached the model's output limit."))
                if stop == "refusal":
                    self._fail("The model refused to go on with this request.")
                if self._interrupting and self._stop == "completed":
                    self._stop = "interrupted"
            self._message_prompt = None
        if self._message_prompt is None and not self._pending and self.turn_open:
            self._end(self._stop or "completed")

    def stop_of(self, stop: str, result: dict[str, Any]) -> str:
        """The Code view's stop for the tool's ``stopReason``."""
        return {"end_turn": "completed", "cancelled": "interrupted", "max_tokens": "completed",
                "max_turn_requests": "max_turns", "refusal": "failed"}.get(stop, "completed")

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        """Nothing to keep: the tool's own session-wide allow lives in the tool."""

    def prepare_answer(self, ask: Ask, decision: str, message: str, answers: Any) -> dict[str, Any]:
        """The JSON-RPC answer to ``session/request_permission`` — or
        AskRefused, before anything is sent. Only an option the tool OFFERED
        for this very request is ever chosen."""
        offered = ask.data.get("options") or []
        if decision == "allow_session":
            pick = self.session_option(offered)
            if pick is None:
                raise AskRefused(f"{self.LABEL} offers no session-wide allow for this one — allow it once.")
        elif decision == "deny":
            pick = self.deny_option(offered)
            if pick is None:
                return {"jsonrpc": "2.0", "id": ask.native, "result": {"outcome": {"outcome": "cancelled"}}}
        else:
            pick = next((o for o in offered if o.get("kind") == "allow_once"), None)
        if pick is None:
            raise AskRefused(f"{self.LABEL} did not offer that answer for this one.")
        return {"jsonrpc": "2.0", "id": ask.native,
                "result": {"outcome": {"outcome": "selected", "optionId": pick.get("optionId")}}}

    async def interrupt(self) -> None:
        if not self.session_id:
            return
        self._interrupting = True
        await self.notify("session/cancel", {"sessionId": self.session_id})

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._replies.fail_all(HarnessGone(f"{self.LABEL} stopped"))

    # -- the tool's side (ON THE LOOP) ---------------------------------------------
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
        elif method:
            self.on_extension(str(method), params)

    def _on_request(self, method: str, rid: Any, p: dict[str, Any]) -> None:
        if method == "session/request_permission":
            self._ask(rid, p)
            return
        # fs/* and terminal/*: Iron Jarvis said it is no editor (the tool
        # writes the file itself and only tells an editor it did — measured).
        asyncio.ensure_future(self._respond(rid, error="Iron Jarvis does not handle this request"))

    def _ask(self, rid: Any, p: dict[str, Any]) -> None:
        tc = p.get("toolCall") if isinstance(p.get("toolCall"), dict) else {}
        options = [o for o in p.get("options") or [] if isinstance(o, dict) and o.get("optionId")]
        tid = str(tc.get("toolCallId") or "")
        known = self._tools.get(tid)
        name = known[0] if known else self.tool_name(tc)
        acp_kind = str(tc.get("kind") or (known[1] if known else ""))
        args = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) and tc.get("rawInput") else (
            known[2] if known else {})
        kind = self.tool_kind(name, acp_kind)
        card = {"command": "command", "edit": "edit", "write": "edit", "web": "network"}.get(kind, "tool")
        if acp_kind == "execute":
            card, kind = "command", "command"
        title = self.tool_title(name, kind, args)
        if kind == "other" and tc.get("title"):
            title = one_line(tc.get("title"), 120)
        can_session = self.session_option(options) is not None
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

    def on_usage_update(self, u: dict[str, Any]) -> None:
        """ACP's ``usage_update`` (OpenCode: how full + its cost)."""

    def on_config_update(self, options: Any) -> None:
        """ACP's ``config_option_update`` (a subclass keeps its options)."""

    def _on_update(self, u: dict[str, Any]) -> None:
        kind = u.get("sessionUpdate")
        if kind == "available_commands_update":
            cmds = u.get("availableCommands")
            # v1.439.0: each command keeps its argument hint (ACP's
            # ``input: {hint}``, e.g. OpenCode's /review "commit").
            self.commands = [
                {"name": str(c.get("name") or ""), "description": clip(c.get("description") or "", 200),
                 **({"argument_hint": clip(_hint(c), 80)} if _hint(c) else {})}
                for c in (cmds or []) if isinstance(c, dict) and c.get("name")
            ][:300]
            if self.commands:
                from .claude import LAST_COMMANDS, commands_key

                # v1.439.0: OpenCode's list is its FOLDER's (a project's own
                # commands): kept per folder.
                LAST_COMMANDS[commands_key(self.harness, self.cwd)] = list(self.commands)
            return
        if kind == "usage_update":
            self.on_usage_update(u)
            return
        if kind == "config_option_update":
            self.on_config_update(u.get("configOptions"))
            return
        if self._loading or not self.turn_open:
            # A reopened session's replay, or anything outside a message:
            # what the tool says about the PAST is not drawn again.
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
            # The FIRST record names the tool.
            name = self.tool_name(u)
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
        kind = self.tool_kind(name, acp_kind)
        ev = make("tool", item=tid, kind=kind, name=clip(name, 120), title=self.tool_title(name, kind, args),
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
        self.emit(self.failure_event(said))

    def end_turn_tools(self) -> None:
        """The turn ended: what a tool still running becomes (nothing, by
        default — the tool says each one's end itself)."""
        self._tools.clear()

    def _end(self, stop: str) -> None:
        if not self.turn_open:
            return
        self.turn_open = False
        self._close_text()
        for item in list(self._texts):
            text = self._texts.pop(item)
            if text.strip():
                self.emit(make("text_done", item=item, text=clip(text, TEXT_CHARS)))
        self.end_turn_tools()
        self._interrupting = False
        cost = self.turn_cost()
        if any(self._usage.values()) or cost is not None:
            # Each tool prices a call from its own table; the ACP does not say
            # whether it is a subscription or a pay-per-use key: "the cost the
            # tool reported", never list price.
            usage = make("usage", **self._usage, list_price=False)
            if cost is not None:
                usage["cost_usd"] = cost
                usage["cost_reported_by"] = self.LABEL
            from . import seen as _seen

            self.emit(_seen.attach(usage, self))
        self.emit(make("done", stop=stop))
