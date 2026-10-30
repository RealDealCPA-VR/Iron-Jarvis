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
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from . import words as W
from .driver import Ask, AskRefused, ControlRefused, ControlWaiter, Driver, new_ask_id
from .events import DETAIL_CHARS, DIFF_CHARS, INPUT_CHARS, OUTPUT_CHARS, TEXT_CHARS, clip, make, one_line, unified_new_file

HARNESS = "codex"

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
    "plan": "Codex has no plan mode.",
    **({"edits": "Codex cannot run commands in its sandbox on Windows, so edits cannot run on their own "
                 "while commands still ask."} if _WINDOWS else {}),
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

_DECISION_V2 = {"allow": "accept", "allow_session": "acceptForSession", "deny": "decline"}
_DECISION_LEGACY = {"allow": "approved", "allow_session": "approved_for_session", "deny": "denied"}


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

    # -- the user's side --------------------------------------------------------
    async def send_user(self, text: str, files: list[str]) -> None:
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

    def on_exit(self) -> None:
        from .process import HarnessGone

        self._replies.fail_all(HarnessGone("Codex stopped"))

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
            self.emit(make("notice", text="An app asked Codex for input; the Code view cannot answer that yet, "
                                          "so it was declined."))
            asyncio.ensure_future(self._safe(self.respond(rid, {"action": "decline"})))
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
            last = _token_numbers(((p.get("tokenUsage") or {}).get("last")))
            for k in self._usage:
                self._usage[k] += last[k]
        elif method == "error":
            self._on_error(p)
        elif method == "turn/completed":
            self._turn_completed(p.get("turn") or {})
        elif method == "thread/compacted":
            self.emit(make("notice", text="Codex compacted the conversation to make room."))
        elif method == "model/rerouted":
            self.emit(make("notice", text="Codex moved this turn to another model."))
        elif method == "account/rateLimits/updated":
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
        status = str(item.get("status") or "completed")
        failed = status in ("failed", "declined") or (
            t == "commandExecution" and isinstance(item.get("exitCode"), int) and item["exitCode"] != 0
        )
        ev = self._tool_event(item, "failed" if failed else "done")
        if ev is not None:
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
                ev["output"] = clip(out, OUTPUT_CHARS)
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
                      input=clip(item.get("arguments") or "", INPUT_CHARS), status=status)
            if status != "running":
                ev["output"] = clip(item.get("error") or item.get("result") or "", OUTPUT_CHARS)
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
        self.emit(make("usage", **self._usage, list_price=True))
        stop = {"completed": "completed", "interrupted": "interrupted", "failed": "failed"}.get(status, "failed")
        if self._interrupting and stop != "completed":
            stop = "interrupted"
        self._interrupting = False
        self.turn_open = False
        self.emit(make("done", stop=stop))
