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
"""

from __future__ import annotations

import asyncio
import os
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
    unified_from_hunks,
    unified_new_file,
    unified_replace,
)

HARNESS = "claude-code"

#: The composer's presets -> Claude Code's permission modes (flag and live).
MODES: dict[str, str] = {
    "ask": "default",
    "edits": "acceptEdits",
    "plan": "plan",
    "full": "bypassPermissions",
}
MODE_OF: dict[str, str] = {v: k for k, v in MODES.items()}
MODE_LABELS: dict[str, str] = {
    "ask": "Ask first",
    "edits": "Auto edits",
    "plan": "Plan",
    "full": "Full access",
}

#: The `/` list Claude Code last answered ``initialize`` with (this process):
#: the harnesses listing shows it without starting anything.
LAST_COMMANDS: dict[str, list[dict[str, str]]] = {}

#: How long a control request (initialize, set_model …) may take.
CONTROL_TIMEOUT_S = 30.0

_COMMAND_TOOLS = {"Bash", "PowerShell", "BashOutput", "KillShell", "KillBash"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
_READ_TOOLS = {"Read", "LS"}
_SEARCH_TOOLS = {"Grep", "Glob"}
_WEB_TOOLS = {"WebFetch", "WebSearch"}
_TASK_TOOLS = {"Task", "Agent"}


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
        return "Hand a task to a helper"
    if kind == "todo":
        return "Update the plan"
    if name == "AskUserQuestion":
        return "Ask you a question"
    if name == "ExitPlanMode":
        return "Show the plan"
    return f"Use {name}"


def _input_line(inp: dict[str, Any]) -> str:
    import json

    try:
        return clip(json.dumps(inp, ensure_ascii=False, sort_keys=True), INPUT_CHARS)
    except (TypeError, ValueError):
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
        if s.get("type") == "setMode" and MODE_OF.get(str(s.get("mode"))) == "full":
            return []
        out.append(s)
    return out


def suggested_mode(sugg: list[dict[str, Any]]) -> str:
    """The preset an accepted suggestion switches the session to ("" = none)."""
    for s in sugg:
        if s.get("type") == "setMode":
            return MODE_OF.get(str(s.get("mode")), "")
    return ""


def suggestion_words(sugg: list[dict[str, Any]]) -> str:
    """What "Allow for this session" does, in plain words, before the press."""
    parts = []
    for s in sugg:
        kind = s.get("type")
        if kind == "setMode":
            parts.append(f"Allow for this session switches Claude Code to {MODE_LABELS[MODE_OF[str(s['mode'])]]} "
                         "for the rest of this session.")
        elif kind in ("addRules", "replaceRules"):
            rules = []
            for r in s.get("rules") or []:
                if isinstance(r, dict):
                    tool, content = str(r.get("toolName") or ""), str(r.get("ruleContent") or "")
                    rules.append(f"{tool}({content})" if content else tool)
            if rules:
                parts.append("Allow for this session always allows " + ", ".join(clip(r, 80) for r in rules)
                             + " for the rest of this session.")
        elif kind == "addDirectories":
            dirs = [clip(d, 120) for d in s.get("directories") or [] if isinstance(d, str)]
            if dirs:
                parts.append("Allow for this session lets Claude Code work in " + ", ".join(dirs) + ".")
    if sugg and not parts:
        parts.append("Allow for this session saves Claude Code's own permission rule for this session.")
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


class ClaudeDriver(Driver):
    harness = HARNESS

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._controls = ControlWaiter()
        self._n = 0
        self._interrupting = False
        self._errored = False
        #: message id -> stream text items in order / how many are settled.
        self._stream_text: dict[str, list[str]] = {}
        self._settled: dict[str, int] = {}
        self._blocks: dict[int, dict[str, Any]] = {}
        self._msg = ""
        #: tool_use id -> (name, input).
        self._tools: dict[str, tuple[str, dict[str, Any]]] = {}
        #: The account limit the harness reported (``terminals.limit_state``).
        self.limit: Any = None

    # -- the command line ------------------------------------------------------
    def argv(self, *, session_id: str, resume: bool) -> list[str]:
        out = [
            "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompts", "host",
            "--permission-prompt-tool", "stdio",
            "--permission-mode", MODES.get(self.mode, "default"),
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
        self.commands = [
            {"name": str(c.get("name") or ""), "description": clip(c.get("description") or "", 200),
             "argument_hint": clip(c.get("argumentHint") or "", 80)}
            for c in (cmds or []) if isinstance(c, dict) and c.get("name")
        ][:300]
        if self.commands:
            LAST_COMMANDS[HARNESS] = list(self.commands)

    # -- the user's side --------------------------------------------------------
    async def send_user(self, text: str, files: list[str]) -> None:
        body = str(text or "")
        if files:
            listed = "\n".join(f"@{f}" for f in files)
            body = f"{body}\n\nFiles I attached:\n{listed}" if body.strip() else f"Files I attached:\n{listed}"
        self.turn_open = True
        self._errored = False
        self._interrupting = False
        await self.write({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": body}]},
            "parent_tool_use_id": None,
            "session_id": self.session_id,
        })

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:
        await self.write(self.prepare_answer(ask, decision, message, answers))
        self.answered(ask, decision)
        return decision

    def answered(self, ask: Ask, decision: str) -> None:
        """The answer reached Claude Code: a session-wide allow that set a mode
        is now this session's mode."""
        if decision == "allow_session" and ask.data.get("sets_mode"):
            self.mode = ask.data["sets_mode"]

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
            await self.control("interrupt")
        except asyncio.TimeoutError:
            pass

    async def set_mode(self, mode: str) -> str:
        try:
            await self.control("set_permission_mode", mode=MODES[mode])
        except ControlRefused as exc:
            raise ControlRefused(f"Claude Code did not change its mode: {exc}") from None
        self.mode = mode
        return "now"

    async def set_model(self, model: str) -> str:
        try:
            res = await self.control("set_model", model=model)
        except ControlRefused as exc:
            raise ControlRefused(f"Claude Code did not switch models: {exc}") from None
        self.model = model
        if isinstance(res, dict) and res.get("model"):
            self.emit(make("notice", text=f"Claude Code now answers with {clip(res['model'], 80)}."))
        return "now"

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._controls.fail_all(HarnessGone("Claude Code stopped"))

    # -- the harness's side (ON THE LOOP) ---------------------------------------
    def on_message(self, obj: dict[str, Any]) -> None:
        kind = obj.get("type")
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
            self._on_stream(obj.get("event") or {})
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

    def _on_control_request(self, obj: dict[str, Any]) -> None:
        req = obj.get("request") or {}
        rid = obj.get("request_id")
        if req.get("subtype") != "can_use_tool":
            # hook callbacks, MCP messages … Iron Jarvis registered none: say so.
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
        detail = clip(req.get("description") or req.get("decision_reason") or "", DETAIL_CHARS)
        effect = suggestion_words(sugg)
        if effect:
            detail = f"{detail}\n\n{effect}" if detail else effect
        ev = make(
            "approval", request_id=ask_id, kind=akind, title=tool_title(name, inp),
            detail=detail, can_session=bool(sugg),
        )
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
        mode = suggested_mode(sugg)
        if mode:
            data["sets_mode"] = mode
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
        elif sub == "compact_boundary":
            self.emit(make("notice", text="Claude Code compacted the conversation to make room."))
        elif sub == "api_retry":
            n = obj.get("attempt")
            self.emit(make("notice", text=f"Claude Code is retrying the request{f' (attempt {n})' if n else ''}."))

    def _on_stream(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "message_start":
            self._msg = str((ev.get("message") or {}).get("id") or "")
            self._blocks = {}
        elif t == "content_block_start":
            idx = int(ev.get("index") or 0)
            block = ev.get("content_block") or {}
            item = f"{self._msg}:{idx}"
            self._blocks[idx] = {"type": block.get("type"), "item": item}
            if block.get("type") == "text":
                self._stream_text.setdefault(self._msg, []).append(item)
        elif t == "content_block_delta":
            idx = int(ev.get("index") or 0)
            block = self._blocks.get(idx) or {}
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.emit(make("text", item=block.get("item") or f"{self._msg}:{idx}",
                               delta=clip(delta["text"], TEXT_CHARS)))
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                self.emit(make("thinking", item=block.get("item") or f"{self._msg}:{idx}",
                               delta=clip(delta["thinking"], TEXT_CHARS)))

    def _text_item(self, msg: str) -> str:
        items = self._stream_text.get(msg) or []
        k = self._settled.get(msg, 0)
        self._settled[msg] = k + 1
        return items[k] if k < len(items) else f"{msg}:t{k}"

    def _on_assistant(self, obj: dict[str, Any]) -> None:
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        code = obj.get("error") or message.get("error")
        msg = str(message.get("id") or "")
        if code:
            said = _result_text(message.get("content"))
            self._failure(said or str(code))
            return
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text" and str(block.get("text") or "").strip():
                self.emit(make("text_done", item=self._text_item(msg), text=clip(block["text"], TEXT_CHARS)))
            elif bt == "tool_use":
                self._tool_started(block)

    def _tool_started(self, block: dict[str, Any]) -> None:
        tid = str(block.get("id") or "")
        name = str(block.get("name") or "")
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        self._tools[tid] = (name, inp)
        kind = tool_kind(name)
        ev = make("tool", item=tid, kind=kind, name=clip(name, 120), title=tool_title(name, inp),
                  input=_input_line(inp), status="running")
        path = inp.get("file_path") or inp.get("notebook_path")
        if path:
            ev["path"] = clip(path, 500)
        self.emit(ev)
        if kind == "todo":
            self.emit(make("plan", steps=plan_steps(inp)))

    def _on_user(self, obj: dict[str, Any]) -> None:
        if obj.get("isReplay"):
            return
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            return
        meta = obj.get("tool_use_result")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")
            name, inp = self._tools.get(tid, ("", {}))
            failed = bool(block.get("is_error"))
            ev = make("tool", item=tid, kind=tool_kind(name), name=clip(name, 120),
                      title=tool_title(name, inp), input=_input_line(inp),
                      status="failed" if failed else "done",
                      output=clip(_result_text(block.get("content")), OUTPUT_CHARS))
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
            self.emit(ev)

    def _on_rate_limit(self, info: dict[str, Any]) -> None:
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
        from ..terminals.limit_state import PaneLimit, detect_limit

        self._errored = True
        ev = W.failure_for(HARNESS, said)
        if ev.get("fix_kind") == "account_limit":
            self.limit = detect_limit(said) or PaneLimit(line=one_line(said, 300))
        self.emit(ev)

    def _on_result(self, obj: dict[str, Any]) -> None:
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
        self.emit(ev)
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
        self._interrupting = False
        self.turn_open = False
        self.emit(done)
