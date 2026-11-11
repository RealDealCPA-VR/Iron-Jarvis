"""`tool_script` — the model writes ONE script that calls its tools itself.

Without it, every tool call is a round trip: the model asks, the tool runs,
the whole result is pasted into the model's context, the model reads it and
asks for the next. Forty workbooks is forty model turns and forty results in
the window. With it, the model writes a short Python script; the script calls
the tools, keeps what they return in variables, and only what it ``print()``s
comes back.

THE SCRIPT RUNS IN AN ISOLATED WORKER (``repl/session.py``'s ``isolated``
namespace, ``repl/worker.py``'s ``ENV_ISOLATED``):

* its own process, with an environment that holds no key or token;
* no network, no other program, no registry change, no file written outside
  a private scratch folder, files read only inside the turn's working folder;
* Windows itself holds "no other program", a memory ceiling and "no clipboard
  or desktop" on the process (a Job — ``core.procs.restrict``), so those hold
  even against code that gets around the worker's own checks. The network
  and file rules are the worker's own checks: a guardrail against a careless
  or misled model, not a sandbox against hostile code. Said plainly here
  because the Handbook says it too.

SO THE ONLY WAY A SCRIPT ACTS IS BY CALLING A TOOL, and every such call is
judged here (:meth:`ToolScriptTool._refusal`) and then goes through
``ToolRegistry.invoke`` with the calling turn's own roster, overrides, grants
and deadline (``registry.current_call_scope``) — the same gate, the same
ledger row, the same events as a call the model makes directly. On top of
that gate a script may call ONLY a tool that

* is one of this turn's tools (the roster),
* only reads (``Reversibility.READONLY``),
* would run without asking (its permission resolves to allow, it has no
  spend check that asks, it is not held back for a look),
* and, in a Local only chat, does not reach outside this PC.

Anything else is refused inside the script with a sentence that says to call
it directly, where the user can be asked. A script is never a way around an
approval card.

NO COST THE USER NOTICES. The worker is started ahead of time, the moment the
tool is offered for a turn (:func:`prewarm`), and stays up for the session; a
call crosses one local pipe (well under a millisecond, measured) and then
runs exactly as a direct call does. There is no before/after scan of the
working folder (the script cannot write there).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..core import progress
from .base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.tools.tool_script")

NAME = "tool_script"

#: Seconds a script's OWN work may take (time spent waiting on a tool is the
#: tool's, bounded by the tool's deadline).
DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 300.0
#: The most a whole script may take, its tool calls included (v1.471.0).
#: The registry no longer cuts a script at ONE call's deadline
#: (``Tool.bounds_itself``): forty slow reads, each inside that deadline,
#: were stopped at minute ten. Each call still has that deadline.
MAX_TOTAL_S = 3600.0

#: Tool names listed in the footer and the result data.
_MAX_LISTED = 12

#: Tools that only read but do not declare ``Reversibility.READONLY`` (their
#: class predates the field, or keeps the fail-safe default). Named one by
#: one: each was read before it joined, and each changes nothing. The web
#: ones reach outside, so a Local only chat keeps them out (the gate below).
EXTRA_READERS: frozenset[str] = frozenset({
    "web_fetch", "web_search", "web_extract", "file_search", "image_info",
    "memory_search", "memory_read", "recall", "recall_lessons",
    "guide_search", "guide_read", "app_search", "app_status",
})

#: Read-only by their own declaration, and still never called from a script:
#: each one talks to the user, shows a card, starts a model or another agent,
#: opens a page, or waits. A script has nobody to show anything to.
NOT_FROM_A_SCRIPT: frozenset[str] = frozenset({
    "ask_user", "exit_plan_mode", "consult", "config_secret", "plan_update",
    "page_open", "page_read", "page_screenshot", "web_look", "pane_wait",
    "view_image", "excel_live_look",
})

_NO_OUTPUT_NOTE = (
    "(the script ran and printed nothing. Only what it print()s comes back: "
    "print the answer you need.)"
)
_TRUNCATED_NOTE = (
    "[output TRUNCATED: what you see above is PARTIAL. Print less: a count, "
    "a total, the few rows that matter.]"
)
_RESTARTED_NOTE = (
    "[the script's worker was started again before this ran, so variables "
    "from earlier scripts are GONE. Re-create anything you still need.]"
)


def worker_key(ctx: Any) -> tuple[str, bool]:
    """``(key, once)``: which isolated worker a call's script runs in.

    An agent run or a coding session has an id of its own, and that is the
    key. Every CHAT turn runs under the one id "chat", so keying on it would
    put every chat's script variables in one process: rows a Local only chat
    loaded would be readable by a script in another chat answered by a cloud
    model. A chat is therefore keyed by its saved thread, else by the turn
    (a new chat's first message). With neither (a headless caller), the
    worker is used once and thrown away (``once``)."""
    sid = str(getattr(ctx, "session_id", "") or "")
    if sid != "chat":
        return sid, False
    own = str(getattr(ctx, "thread_id", "") or "") or str(getattr(ctx, "turn_id", "") or "")
    if own:
        return f"chat:{own}", False
    import uuid

    return f"chat:once-{uuid.uuid4().hex}", True


class ToolScriptTool(Tool):
    name = NAME
    permission_key = NAME
    # It can change nothing by itself (see the module docstring) and may call
    # only tools that read, so it is a read.
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # it prints what the tools it called read
    # Each call it makes has the per-call deadline; the script has its own
    # overall bound (MAX_TOTAL_S) instead of one call's.
    bounds_itself = True
    description = (
        "Run ONE short Python script that calls this turn's read-only tools "
        "itself, so many calls take one step and their results never fill "
        "your context. Use it when a tool is needed many times (every file in "
        "a folder, each client, each sheet) or when results must be filtered, "
        "joined or added up before you look at them.\n"
        "In the script: `r = tools.call(\"read_document\", path=p)` (or "
        "`tools.read_document(path=p)`) gives `r.output` (the text) and "
        "`r.data`. A tool that is refused or fails raises `ToolError`, so "
        "wrap a loop's call in try/except and report what failed. "
        "`tools.names` lists what the script may call: tools that only read. "
        "A tool that changes a file, sends something or needs the user's "
        "approval must be called directly, not from a script.\n"
        "ONLY WHAT THE SCRIPT print()s COMES BACK. Print the answer (a "
        "count, a total, the rows that matter), never the raw results.\n"
        "The script runs isolated: no network, no other programs, no file "
        "writes, files read only in the working folder (reach everything "
        "else through a tool). Variables persist between scripts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python. Call tools with tools.call(name, **args); "
                    "print() only what you need to see."
                ),
            },
            "timeout_s": {
                "type": "number",
                "description": (
                    f"Seconds the script's own work may take, not counting the "
                    f"tools it waits on (default {DEFAULT_TIMEOUT_S:g}, max "
                    f"{MAX_TIMEOUT_S:g})."
                ),
            },
        },
        "required": ["code"],
    }

    def __init__(self, repl_registry: Any, tool_registry: Any) -> None:
        self._repl = repl_registry
        self._registry = tool_registry

    # -- which tools a script may call ----------------------------------------

    def _fixed_refusal(self, name: str, tool: Any) -> str:
        """Why *name* can never be called from a script, or "". Facts of the
        tool itself: the same answer for every turn."""
        if tool is None:
            return f"there is no tool named `{name}`"
        if name == NAME:
            return "a script cannot start another script"
        if name in NOT_FROM_A_SCRIPT:
            return (
                f"`{name}` shows the user something or waits for them, so it "
                "cannot be called from a script. Call it directly"
            )
        if (
            name not in EXTRA_READERS
            and getattr(tool, "reversibility", Reversibility.IRREVERSIBLE) != Reversibility.READONLY
        ):
            return (
                f"`{name}` changes something or acts outside this script, so it "
                "cannot be called from a script. Call it directly, where the "
                "user can be asked"
            )
        if getattr(tool, "deadline_exempt", False):
            return f"`{name}` waits for a person or another agent; call it directly"
        if getattr(tool, "quarantined", False):
            return (
                f"`{name}` is new in its app and is held for the user's first "
                "look; call it directly so they can approve it"
            )
        return ""

    def callable_names(self, scope: Any) -> list[str]:
        """The tools a script of THIS call may reach: the turn's roster, cut
        to the ones that pass :meth:`_fixed_refusal`. A caller that named no
        roster offers none (nothing here guesses one)."""
        allowed = getattr(scope, "allowed_names", None)
        if not allowed:
            return []
        out: list[str] = []
        for name in sorted(allowed):
            try:
                tool = self._registry.get(name)
            except Exception:  # noqa: BLE001
                tool = None
            if tool is not None and not self._fixed_refusal(name, tool):
                out.append(name)
        return out

    async def _refusal(
        self, name: str, args: dict[str, Any], scope: Any, ctx: ToolContext
    ) -> str:
        """Why this one call is not run, or "". Fail-closed: a check that
        cannot be made refuses."""
        try:
            tool = self._registry.get(name)
        except Exception:  # noqa: BLE001
            tool = None
        fixed = self._fixed_refusal(name, tool)
        if fixed:
            return fixed
        allowed = getattr(scope, "allowed_names", None)
        if not allowed or name not in allowed:
            return f"`{name}` is not one of this turn's tools"
        try:
            from ..providers import egress

            if egress.current_scope().on and (
                name in egress.INTERNET_TOOLS or egress.reaches_outside(tool, self._registry)
            ):
                return (
                    f"this chat is Local only and `{name}` reaches outside this "
                    "PC. Call it directly, so the user can allow that one call"
                )
            perms = getattr(scope, "perms", None)
            key = tool.perm_key()
            mode = getattr(perms.mode_for(key, getattr(scope, "agent_overrides", None)), "value", "ask")
            if mode == "deny":
                return f"`{name}` is set to never run here"
            if mode != "allow":
                granted = getattr(scope, "session_allow", None) or ()
                if key not in granted and name not in granted:
                    return (
                        f"`{name}` asks the user first. Call it directly, not "
                        "from a script"
                    )
            check = await tool.spend_check(args, ctx)
            if check is not None and getattr(check, "ask", False):
                return (
                    f"`{name}` asks the user before this call. Call it "
                    "directly, not from a script"
                )
        except Exception as exc:  # noqa: BLE001 — cannot tell: do not run it
            log.debug("tool_script gate failed for %s", name, exc_info=True)
            return f"`{name}` could not be checked ({type(exc).__name__}), so it was not run"
        return ""

    # -- execute ---------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        code = str(args.get("code") or "")
        if not code.strip():
            return ToolResult(ok=False, error="code is required")
        try:
            timeout = float(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        if timeout != timeout:  # NaN
            timeout = DEFAULT_TIMEOUT_S
        timeout = min(max(timeout, 1.0), MAX_TIMEOUT_S)
        if self._repl is None:
            return ToolResult(ok=False, error="tool scripts are not available in this build")

        from .registry import current_call_scope
        from ..repl.session import MAX_TOOL_OUTPUT_CHARS

        scope = current_call_scope()
        names = self.callable_names(scope)
        made: dict[str, int] = {}
        refused: dict[str, str] = {}

        async def on_call(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
            name = str(name or "").strip()
            call_args = {
                str(k): v for k, v in (call_args or {}).items() if str(k) != "_store_as"
            }
            why = await self._refusal(name, call_args, scope, ctx)
            if why:
                refused.setdefault(name or "(no name)", why)
                return {"ok": False, "error": f"{why}."}
            made[name] = made.get(name, 0) + 1
            # A script that keeps calling tools is working, not stalled (a
            # coding session's stall watch hears this; a no-op elsewhere).
            progress.touch()
            result = await self._registry.invoke(
                name,
                call_args,
                ctx,
                scope.perms,
                agent_overrides=scope.agent_overrides,
                session_allow=scope.session_allow,
                allowed_names=set(scope.allowed_names),
                deadline_s=scope.deadline_s,
                grants=scope.grants,
                scopes=scope.scopes,
            )
            if not result.ok:
                return {"ok": False, "error": str(result.error or f"{name} failed")}
            output = str(result.output or "")
            if len(output) > MAX_TOOL_OUTPUT_CHARS:
                dropped = len(output) - MAX_TOOL_OUTPUT_CHARS
                output = (
                    output[:MAX_TOOL_OUTPUT_CHARS]
                    + f"\n[{name} output TRUNCATED: the last {dropped} characters "
                    "were left out]"
                )
            return {"ok": True, "output": output, "data": result.data}

        session_id, once = worker_key(ctx)
        workspace = Path(getattr(ctx, "workspace", ".") or ".")
        try:
            async with asyncio.timeout(MAX_TOTAL_S) as bound:
                payload = await self._repl.execute(
                    session_id,
                    code,
                    workspace=workspace,
                    timeout=timeout,
                    isolated=True,
                    tools=names,
                    on_tool_call=on_call,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not bound.expired():
                raise
            return ToolResult(
                ok=False,
                error=(
                    f"the script was still running after {MAX_TOTAL_S / 60:g} minutes "
                    "with its tool calls, so it was stopped and its variables are "
                    "gone. Do the work in smaller scripts."
                ),
                data={"tool_calls": dict(made), "refused": sorted(refused)},
            )
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            return ToolResult(
                ok=False, error=f"the script could not be run: {type(exc).__name__}: {exc}"
            )
        finally:
            if once:
                # Nobody can come back to this worker: end it now, in the
                # background (a cancel must not wait on it).
                try:
                    task = asyncio.get_running_loop().create_task(self._repl.dispose(session_id))
                    _WARMING.add(task)
                    task.add_done_callback(_WARMING.discard)
                except Exception:  # noqa: BLE001
                    pass
        if not isinstance(payload, dict):
            return ToolResult(ok=False, error="the script worker gave no usable answer")
        return self._shape(payload, made, refused)

    @staticmethod
    def _shape(
        payload: dict[str, Any], made: dict[str, int], refused: dict[str, str]
    ) -> ToolResult:
        ok = bool(payload.get("ok"))
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        value = str(payload.get("result") or "")
        error = str(payload.get("error") or "")
        truncated = bool(payload.get("truncated"))
        restarted = bool(payload.get("restarted"))

        parts: list[str] = []
        if restarted:
            parts.append(_RESTARTED_NOTE)
        if stdout.strip():
            parts.append(stdout.rstrip())
        if stderr.strip():
            parts.append("[stderr]\n" + stderr.rstrip())
        if value.strip():
            parts.append("[result] " + value.strip())
        if ok and not (stdout.strip() or stderr.strip() or value.strip()):
            parts.append(_NO_OUTPUT_NOTE)
        if truncated:
            parts.append(_TRUNCATED_NOTE)
        total = sum(made.values())
        if total:
            ranked = sorted(made.items(), key=lambda kv: (-kv[1], kv[0]))
            listed = ", ".join(f"{n} x{c}" for n, c in ranked[:_MAX_LISTED])
            more = len(ranked) - _MAX_LISTED
            parts.append(
                f"[the script made {total} tool call{'s' if total != 1 else ''}: {listed}"
                + (f" and {more} more tools" if more > 0 else "")
                + "]"
            )
        if refused:
            lines = "\n".join(f"- {why}" for why in list(refused.values())[:_MAX_LISTED])
            parts.append("[not run from the script]\n" + lines)
        body = "\n".join(p for p in parts if p).strip()
        data: dict[str, Any] = {
            "truncated": truncated,
            "restarted": restarted,
            "tool_calls": dict(made),
            "refused": sorted(refused),
        }
        if not ok:
            tail = error or stderr or "the script raised, but no traceback was captured"
            return ToolResult(
                ok=False,
                output=body,
                error=(body + "\n" + tail).strip() if body else tail,
                data=data,
            )
        return ToolResult(ok=True, output=body, data=data)


async def adopt_turn_worker(platform: Any, turn_id: str, thread_id: str) -> int:
    """A new chat was saved: its first message's script worker (keyed by the
    TURN, :func:`worker_key`) becomes the chat's, so its variables are there
    for the next message (v1.471.0; they were silently gone). Called by the
    link route. Returns how many workers moved. Never raises."""
    try:
        repl = getattr(platform, "repl", None)
        turn, thread = str(turn_id or "").strip(), str(thread_id or "").strip()
        if repl is None or not hasattr(repl, "rekey") or not turn or not thread:
            return 0
        return int(await repl.rekey(f"chat:{turn}", f"chat:{thread}"))
    except Exception:  # noqa: BLE001 — the next message just starts fresh
        log.debug("tool_script: could not hand turn %s's worker to its chat", turn_id, exc_info=True)
        return 0


#: Warm-up tasks in flight (held so they are not collected mid-start).
_WARMING: "set[asyncio.Task[Any]]" = set()


def prewarm(platform: Any, armed: Any, ctx: Any) -> bool:
    """Start the isolated worker *ctx*'s scripts will run in NOW, when
    ``tool_script`` is one of the turn's tools, so the model's first script
    does not wait for a process to start. Call from the event loop; returns
    at once (the start runs in the background). True when a warm-up was
    started. Never raises."""
    try:
        if NAME not in {str(n) for n in (armed or ())}:
            return False
        repl = getattr(platform, "repl", None)
        if repl is None or not hasattr(repl, "warm"):
            return False
        key, once = worker_key(ctx)
        if once:
            return False  # a worker used once is not worth starting early
        task = asyncio.get_running_loop().create_task(
            repl.warm(key, getattr(ctx, "workspace", "."), isolated=True)
        )
        _WARMING.add(task)
        task.add_done_callback(_WARMING.discard)
        return True
    except Exception:  # noqa: BLE001 — warming is a courtesy
        return False
