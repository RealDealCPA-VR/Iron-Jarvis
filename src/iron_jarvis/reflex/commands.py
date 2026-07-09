"""The command grammar — operate Iron Jarvis from a phone.

Inbound comm normally turns a message into a free-form supervised session. But a
message that starts with ``/`` is a COMMAND: a fast, deterministic operation —
check status, list/run/cancel workflows, list/query remote agents, list recent
sessions — that replies immediately instead of spinning up an agent.

:meth:`CommandInterpreter.interpret` returns the reply text for a command, or
``None`` when the text is not a command (so the caller falls through to the
normal session path). It never raises — a broken command replies with the error.
"""

from __future__ import annotations

from typing import Any

from ..core.db import session_scope
from ..core.logging import get_logger

log = get_logger("reflex.commands")

_HELP = (
    "Iron Jarvis commands:\n"
    "/status — version, model, live work\n"
    "/workflows — list saved workflows\n"
    "/run <name> — start a workflow\n"
    "/runs — recent workflow runs\n"
    "/cancel <run_id> — stop a running workflow\n"
    "/agents — list remote agents\n"
    "/ask <agent> <task> — ask a remote agent\n"
    "/sessions — recent sessions\n"
    "/help — this list\n"
    "Any other message runs as a normal request."
)


class CommandInterpreter:
    def __init__(self, platform: Any, orchestrator: Any, router: Any) -> None:
        self.p = platform
        self.orch = orchestrator
        self.router = router

    async def interpret(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text.startswith("/"):
            return None
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.lower().strip()
        rest = rest.strip()
        try:
            handler = getattr(self, f"_cmd_{cmd}", None)
            if handler is None:
                return f"Unknown command '/{cmd}'. Send /help for the list."
            return await handler(rest)
        except Exception as exc:  # noqa: BLE001 — a command must never crash comm
            log.exception("command '/%s' failed", cmd)
            return f"'/{cmd}' failed: {type(exc).__name__}: {exc}"

    # -- commands ----------------------------------------------------------
    async def _cmd_help(self, _rest: str) -> str:
        return _HELP

    async def _cmd_status(self, _rest: str) -> str:
        from .. import __version__
        from ..workflows.models import WorkflowRunRecord
        from sqlmodel import select

        cfg = self.p.config
        with session_scope(self.p.engine) as db:
            runs = list(db.exec(select(WorkflowRunRecord)))
        live = sum(1 for r in runs if r.status in ("running", "cancelling"))
        return (
            f"Iron Jarvis v{__version__}\n"
            f"Model: {cfg.default_provider}/{cfg.default_model}\n"
            f"Workflows: {live} running, {len(runs)} total\n"
            f"Sessions: {len(self.orch.list_sessions(limit=200))} recent"
        )

    async def _cmd_workflows(self, _rest: str) -> str:
        from ..workflows.store import WorkflowStore

        rows = WorkflowStore(self.p.engine).list()
        if not rows:
            return "No saved workflows yet."
        return "Saved workflows:\n" + "\n".join(f"• {r.name}" for r in rows)

    async def _cmd_run(self, rest: str) -> str:
        name = rest.strip()
        if not name:
            return "Usage: /run <workflow name>"
        from ..workflows.store import WorkflowStore

        if WorkflowStore(self.p.engine).get(name) is None:
            return f"No saved workflow '{name}'. /workflows to list them."
        res = await self.router.start("workflow", target=name)
        if res.get("ok"):
            return f"Started '{name}' (run {res.get('run_id')}). /runs to track it."
        return f"Couldn't start '{name}': {res.get('error')}"

    async def _cmd_runs(self, _rest: str) -> str:
        from ..workflows.models import WorkflowRunRecord
        from sqlmodel import select

        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(
                    select(WorkflowRunRecord)
                    .order_by(WorkflowRunRecord.started_at.desc())  # type: ignore[attr-defined]
                    .limit(8)
                )
            )
        if not rows:
            return "No workflow runs yet."
        return "Recent runs:\n" + "\n".join(
            f"• {r.workflow_name or r.id[:8]} — {r.status} ({r.id})" for r in rows
        )

    async def _cmd_cancel(self, rest: str) -> str:
        run_id = rest.strip()
        if not run_id:
            return "Usage: /cancel <run_id>"
        from ..workflows.models import WorkflowRunRecord

        with session_scope(self.p.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                return f"No run '{run_id}'."
            if rec.status in ("completed", "failed", "cancelled", "interrupted"):
                return f"Run '{run_id}' already {rec.status}."
            rec.status = "cancelling"
            db.add(rec)
            db.commit()
            current = rec.current_session_id
        if current:
            try:
                self.orch.cancel_session(current)
            except Exception:  # noqa: BLE001 — the status flip is the real signal
                pass
        return f"Cancelling run '{run_id}'."

    async def _cmd_agents(self, _rest: str) -> str:
        from ..agents.remote import RemoteAgentRegistry

        rows = RemoteAgentRegistry(self.p.engine).list()
        if not rows:
            return "No remote agents configured."
        return "Remote agents:\n" + "\n".join(
            f"• {r.name}{'' if r.enabled else ' (disabled)'}" for r in rows
        )

    async def _cmd_ask(self, rest: str) -> str:
        name, _, task = rest.partition(" ")
        name, task = name.strip(), task.strip()
        if not name or not task:
            return "Usage: /ask <agent> <task>"
        from ..agents.remote import RemoteAgentRegistry

        reg = RemoteAgentRegistry(self.p.engine)
        record = reg.get(name)
        if record is None:
            return f"No remote agent '{name}'. /agents to list them."
        res = await reg.run(record, task, self.p.secrets.get)
        if res.get("ok"):
            return (res.get("result") or "(no reply)").strip()
        return f"{name} couldn't answer: {res.get('detail') or 'unknown error'}"

    async def _cmd_sessions(self, _rest: str) -> str:
        rows = self.orch.list_sessions(limit=6)
        if not rows:
            return "No sessions yet."
        lines = []
        for s in rows:
            status = getattr(getattr(s, "status", None), "value", None) or str(
                getattr(s, "status", "")
            )
            lines.append(f"• {(s.task or '')[:50]} — {status}")
        return "Recent sessions:\n" + "\n".join(lines)
