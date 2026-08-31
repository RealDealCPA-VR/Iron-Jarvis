"""Agents can drive the Build canvas (v1.217.0).

Build already runs live terminals the user watches, and since this release it
also knows what the agent in each pane is DOING (`terminals/agent_state.py`).
These tools let an agent read and use that: see the panes, read one, open a
sibling, type into it, and wait for a state — the loop a coding agent needs to
hand work to another agent and know when it is stuck.

The shape is adapted from herdr's agent skill, and three of its rules are
copied deliberately because each one is an honesty rule rather than a feature:

* **Refuse to type into a question.** herdr: "It rejects an agent already
  waiting at an approval or question dialog with ``agent_blocked`` before
  sending any input." Answering someone else's approval prompt on their behalf
  is the one thing an agent must never do by accident, and a pane that is
  BLOCKED is by definition showing a decision the user has not made.
* **Say when nothing happened.** herdr returns ``agent_prompt_stalled`` when a
  prompt produces no lifecycle change. A wait that hangs forever is
  indistinguishable from a wait that will never end, and this app's rule is
  that a tool reports what it did rather than leaving the model to infer it.
* **`unknown` is not completion.** Inherited from `agent_state`, and restated
  here because these tools are where a wrong answer becomes an action.

PERMISSIONS. `pane_send` and `pane_spawn` reach the host: one types into a live
PTY (strictly more reach than `shell`, because the shell it types into may
already be authenticated to something) and the other starts a process. Both are
on the DENY FLOOR (`tools/permissions.DENY_FLOOR_TOOLS`) and default to `ask`,
so an agent definition can lower them but never raise them to `allow`. The
read-only three are `allow`, the same tier as `read_file`/`grep`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .base import Reversibility, Tool, ToolContext, ToolResult

#: How long a wait may run before it gives up and says so. A wait with no
#: ceiling is a hung agent run.
_MAX_WAIT_S = 300.0
#: herdr's rule, same number: a prompt sent to a non-working pane must produce
#: an observable change within this window or the tool reports it stalled
#: rather than waiting out the full timeout on a pane that never heard it.
_STALL_S = 5.0
_POLL_S = 0.25


class _PaneTool(Tool):
    """Base for the five: the terminal manager is INJECTED at registration.

    `ToolContext` deliberately carries workspace/session/config/bus/engine and
    not the platform, so a tool that needs a subsystem takes it in its
    constructor — the same shape as `ReplTool(repl_registry)`. It also makes
    these trivially testable against a fake manager.
    """

    def __init__(self, manager: Any) -> None:
        self._mgr = manager

    @property
    def mgr(self) -> Any:
        return self._mgr


def _resolve(mgr, target: str):
    """A pane by id OR by name. Names are what agents actually hold on to;
    the id stays the stable machine handle (herdr: "Agent commands accept
    either a unique live agent name or the pane ID currently hosting it")."""
    if not target:
        return None
    session = mgr.get(target)
    if session is not None:
        return session
    for info in mgr.list():
        if info.get("name") == target:
            return mgr.get(info["id"])
    return None


def _view(session, mgr=None) -> dict[str, Any]:
    info = session.info()
    return {
        "id": info["id"],
        "name": info.get("name"),
        "cwd": info.get("cwd"),
        "agent_cli": info.get("agent_cli"),
        "state": info.get("state"),
        "state_line": info.get("state_line"),
        "alive": info.get("alive"),
    }


class PaneListTool(_PaneTool):
    name = "pane_list"
    permission_key = "pane_list"
    description = (
        "List the Build panes: id, name, folder, which coding CLI is running, "
        "and what it is doing (working / blocked / idle / done / unknown). "
        "'blocked' means it is waiting on a human decision; 'unknown' means we "
        "cannot tell and never means finished."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mgr = self.mgr
        if mgr is None:
            return ToolResult(ok=False, error="the Build module is not available here")
        panes = [_view(mgr.get(i["id"])) for i in mgr.list() if mgr.get(i["id"])]
        if not panes:
            return ToolResult(ok=True, output="No Build panes are open.", data={"panes": []})
        lines = [
            f"{p['name'] or p['id']}  [{p['state']}]"
            f"{' ' + (p['agent_cli'] or 'shell')}"
            f"  {p['cwd']}"
            + (f"\n    {p['state_line']}" if p["state_line"] else "")
            for p in panes
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"panes": panes})


class PaneReadTool(_PaneTool):
    name = "pane_read"
    permission_key = "pane_read"
    description = (
        "Read the recent output of a Build pane (by id or name). Use this to "
        "see what an agent in another pane produced, or why it is blocked."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pane": {"type": "string", "description": "Pane id or name."},
            "lines": {
                "type": "integer",
                "description": "How many trailing lines to return (default 80).",
            },
        },
        "required": ["pane"],
    }
    reversibility = Reversibility.READONLY
    # A terminal shows whatever ran in it — including output from programs this
    # app did not write. Fence it as data, exactly like a file read.
    returns_untrusted_content = True

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mgr = self.mgr
        if mgr is None:
            return ToolResult(ok=False, error="the Build module is not available here")
        session = _resolve(mgr, str(args.get("pane") or ""))
        if session is None:
            return ToolResult(ok=False, error=f"no Build pane named {args.get('pane')!r}")
        want = max(1, min(int(args.get("lines") or 80), 400))
        tail = await asyncio.to_thread(session.output_tail)
        rows = [r for r in tail.splitlines() if r.strip()][-want:]
        act = session.activity()
        return ToolResult(
            ok=True,
            output="\n".join(rows) or "(the pane has printed nothing yet)",
            data={"pane": _view(session), "lines": len(rows), "state": act.state.value},
        )


class PaneSpawnTool(_PaneTool):
    name = "pane_spawn"
    permission_key = "pane_spawn"
    description = (
        "Open a new Build pane (a terminal) in a folder, optionally naming it "
        "so it can be addressed later. Starts a shell; it does not start a "
        "coding agent — send the command for that with pane_send."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Folder to open it in."},
            "name": {
                "type": "string",
                "description": "A short handle for this pane, e.g. 'reviewer'.",
            },
        },
    }
    # A pane is a live process; closing it is the user's call, and this app
    # does not model 'kill the terminal you just opened' as an undo.
    reversibility = Reversibility.IRREVERSIBLE

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mgr = self.mgr
        if mgr is None:
            return ToolResult(ok=False, error="the Build module is not available here")
        name = (args.get("name") or "").strip() or None
        if name and _resolve(mgr, name) is not None:
            return ToolResult(
                ok=False,
                error=f"a Build pane named {name!r} already exists — names must be unique",
            )
        cwd = (args.get("cwd") or "").strip() or getattr(ctx, "workspace", None)
        try:
            session = await asyncio.to_thread(
                mgr.create, str(cwd) if cwd else None, None, 100, 30, name=name
            )
        except RuntimeError as exc:  # the session cap
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(
            ok=True,
            output=f"Opened pane {session.pane_name or session.id} in {session.cwd}.",
            data={"pane": _view(session)},
        )


class PaneSendTool(_PaneTool):
    name = "pane_send"
    permission_key = "pane_send"
    description = (
        "Type a line into a Build pane and press Enter — to run a command, or "
        "to prompt a coding agent running there. REFUSES when that pane is "
        "showing an approval or question: answering a human's decision is not "
        "this tool's job."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pane": {"type": "string", "description": "Pane id or name."},
            "text": {"type": "string", "description": "The line to type."},
            "wait": {
                "type": "boolean",
                "description":
                    "Wait for the pane to settle (idle/done/blocked) and return "
                    "its state and last output. Default false.",
            },
        },
        "required": ["pane", "text"],
    }
    reversibility = Reversibility.IRREVERSIBLE

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mgr = self.mgr
        if mgr is None:
            return ToolResult(ok=False, error="the Build module is not available here")
        session = _resolve(mgr, str(args.get("pane") or ""))
        if session is None:
            return ToolResult(ok=False, error=f"no Build pane named {args.get('pane')!r}")
        if not session.alive:
            return ToolResult(ok=False, error="that pane's shell has exited")

        before = session.activity()
        # THE REFUSAL. A blocked pane is showing a decision the USER has not
        # made; typing into it answers on their behalf, and "y" is one
        # keystroke from a command that was being held for review.
        if before.state.value == "blocked":
            return ToolResult(
                ok=False,
                error=(
                    "that pane is waiting on an approval or question — read it "
                    "and ask the user, rather than answering for them"
                ),
                data={"pane": _view(session), "state": "blocked",
                      "state_line": before.line},
            )

        text = str(args.get("text") or "")
        await asyncio.to_thread(session.write, text + "\r")
        if not args.get("wait"):
            return ToolResult(
                ok=True,
                output=f"Sent to {session.pane_name or session.id}.",
                data={"pane": _view(session), "sent": text},
            )

        settled, stalled = await _wait_for(
            session, {"idle", "done", "blocked"}, _MAX_WAIT_S, watch_stall=True
        )
        act = session.activity()
        if stalled:
            # Not a timeout: nothing observable changed at all, which usually
            # means the pane never received it (wrong pane, a full-screen
            # editor, a shell waiting on its own prompt).
            return ToolResult(
                ok=True,
                output=(
                    f"Sent to {session.pane_name or session.id}, but nothing in the "
                    f"pane changed within {int(_STALL_S)}s — it may not have "
                    f"received it. Current state: {act.state.value}."
                ),
                data={"pane": _view(session), "stalled": True, "state": act.state.value},
            )
        return ToolResult(
            ok=True,
            output=f"{session.pane_name or session.id} is now {act.state.value}."
            + (f"\n{act.line}" if act.line else ""),
            data={"pane": _view(session), "settled": settled, "state": act.state.value},
        )


class PaneWaitTool(_PaneTool):
    name = "pane_wait"
    permission_key = "pane_wait"
    description = (
        "Wait until a Build pane reaches a state — 'blocked' (it needs a human), "
        "'idle'/'done' (it finished), or any of them. Returns as soon as it "
        "settles, or says plainly that it timed out."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pane": {"type": "string", "description": "Pane id or name."},
            "until": {
                "type": "string",
                "description":
                    "State to wait for: 'settled' (idle, done or blocked — the "
                    "default), 'blocked', 'idle' or 'done'.",
            },
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["pane"],
    }
    reversibility = Reversibility.READONLY

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mgr = self.mgr
        if mgr is None:
            return ToolResult(ok=False, error="the Build module is not available here")
        session = _resolve(mgr, str(args.get("pane") or ""))
        if session is None:
            return ToolResult(ok=False, error=f"no Build pane named {args.get('pane')!r}")
        until = str(args.get("until") or "settled").lower()
        wanted = (
            {"idle", "done", "blocked"} if until == "settled" else {until}
        )
        timeout = max(1.0, min(float(args.get("timeout_seconds") or 120), _MAX_WAIT_S))
        settled, _ = await _wait_for(session, wanted, timeout, watch_stall=False)
        act = session.activity()
        if not settled:
            return ToolResult(
                ok=True,
                output=(
                    f"{session.pane_name or session.id} did not reach "
                    f"{until} within {int(timeout)}s — it is {act.state.value}."
                ),
                data={"pane": _view(session), "timed_out": True, "state": act.state.value},
            )
        return ToolResult(
            ok=True,
            output=f"{session.pane_name or session.id} is {act.state.value}."
            + (f"\n{act.line}" if act.line else ""),
            data={"pane": _view(session), "state": act.state.value},
        )


async def _wait_for(
    session, wanted: set[str], timeout: float, *, watch_stall: bool
) -> tuple[bool, bool]:
    """Poll the pane's classification until it lands in `wanted`.

    Returns ``(settled, stalled)``. `stalled` is only ever True when
    `watch_stall` is set and NOTHING about the pane changed inside the first
    few seconds — herdr's `agent_prompt_stalled`, which distinguishes "still
    working" from "never heard you".

    Polling rather than an event subscription on purpose: classification is a
    fold over the tail the session already keeps, so a poll costs a string
    scan, and a pane that prints nothing produces no events to subscribe to —
    which is exactly the case a stall detector has to notice.
    """
    deadline = time.monotonic() + timeout
    stall_by = time.monotonic() + _STALL_S
    first = await asyncio.to_thread(lambda: session.activity())
    moved = False
    while time.monotonic() < deadline:
        act = await asyncio.to_thread(lambda: session.activity())
        if act.state.value != first.state.value or act.line != first.line:
            moved = True
        if act.state.value in wanted and (moved or not watch_stall):
            return True, False
        if watch_stall and not moved and time.monotonic() > stall_by:
            return False, True
        await asyncio.sleep(_POLL_S)
    return False, False


PANE_TOOLS: tuple[type[Tool], ...] = (
    PaneListTool,
    PaneReadTool,
    PaneSpawnTool,
    PaneSendTool,
    PaneWaitTool,
)
