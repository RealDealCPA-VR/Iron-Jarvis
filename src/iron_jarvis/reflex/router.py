"""The Reflex Router — turn an inbound signal into a running action.

Given a matched :class:`ReflexRule`, it starts the bound work: a saved workflow,
a remote agent, or a supervised session. The *creating* step is synchronous (so
a caller/test immediately has a run-record or session id), while the long-running
part is launched in the background via the injected ``spawn_bg`` (the daemon's
task launcher) — a webhook POST never blocks on a multi-minute run.

Safety: a rule exists only because the user made it, and every launched action
still flows through the normal orchestrator + permission engine, so a remote
signal gets no more power than a local one. Nothing here bypasses a gate.
"""

from __future__ import annotations

import json as _json
from typing import Any, Callable

from ..core.events import EventType
from ..core.logging import get_logger
from ..core.models import AgentType
from .models import ReflexRule
from .store import ReflexStore

log = get_logger("reflex")

#: A launcher: ``(session_id, coro) -> Task``. The daemon passes ``_spawn_bg``.
SpawnBg = Callable[[str, Any], Any]


def _render(template: str, context: dict[str, str]) -> str:
    """Fill {body}/{text}/{slug} placeholders — plain replace, never raises."""
    out = template or ""
    for key in ("body", "text", "slug"):
        out = out.replace("{" + key + "}", context.get(key, ""))
    return out.strip()


class ReflexRouter:
    def __init__(
        self, platform: Any, orchestrator: Any, spawn_bg: SpawnBg | None = None
    ) -> None:
        self.p = platform
        self.orch = orchestrator
        self.spawn_bg = spawn_bg
        self.store = ReflexStore(platform.engine)

    # -- signal entry points ----------------------------------------------
    async def on_webhook(self, slug: str, body: Any) -> list[dict[str, Any]]:
        """Fire every enabled rule bound to this webhook slug."""
        rules = self.store.matching_webhook(slug)
        if not rules:
            return []
        context = {
            "slug": slug,
            "body": _json.dumps(body)[:2000] if not isinstance(body, str) else body[:2000],
            "text": _text_of(body),
        }
        return [await self.execute(r, context) for r in rules]

    async def on_comm(self, text: str) -> list[dict[str, Any]]:
        """Fire every enabled comm rule whose keyword matches this message."""
        rules = self.store.matching_comm(text)
        context = {"text": text[:2000], "body": text[:2000], "slug": ""}
        return [await self.execute(r, context) for r in rules]

    async def start(self, action: str, *, target: str = "", task: str = "") -> dict[str, Any]:
        """Fire an action MANUALLY (e.g. a ``/run`` command) without a stored
        rule — same execution path, no persistence."""
        rule = ReflexRule(
            name=f"manual:{action}",
            source="comm",
            match="",
            action=action,
            target=target,
            task_template=task,
        )
        return await self.execute(rule, {"text": task, "body": task, "slug": ""})

    # -- execution ---------------------------------------------------------
    async def execute(self, rule: ReflexRule, context: dict[str, str]) -> dict[str, Any]:
        """Launch the rule's bound action; return a small result dict. Never
        raises — a bad rule reports an error and the others still fire."""
        try:
            if rule.action == "workflow":
                result = self._run_workflow(rule)
            elif rule.action == "remote_agent":
                result = self._run_remote(rule, context)
            elif rule.action == "session":
                result = await self._run_session(rule, context)
            else:
                result = {"ok": False, "error": f"unknown action '{rule.action}'"}
        except Exception as exc:  # noqa: BLE001 — one bad rule never breaks the rest
            log.exception("reflex rule %r failed", rule.name)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if result.get("ok"):
            self.store.mark_fired(rule.id)
        await self._publish(rule, result)
        return {"rule": rule.name, "rule_id": rule.id, **result}

    def _run_workflow(self, rule: ReflexRule) -> dict[str, Any]:
        from ..workflows.engine import WorkflowEngine, load_workflow
        from ..workflows.store import WorkflowStore

        rec = WorkflowStore(self.p.engine).get(rule.target)
        if rec is None:
            return {"ok": False, "error": f"no saved workflow '{rule.target}'"}
        wf = load_workflow({"name": rec.name, "steps": _json.loads(rec.steps_json or "[]")})
        engine = WorkflowEngine(self.p, self.orch)
        run = engine.create_record(wf)  # synchronous: persists a run record now
        self._launch(engine.run_record(run, wf), run.id)
        return {"ok": True, "kind": "workflow", "workflow": rec.name, "run_id": run.id}

    def _run_remote(self, rule: ReflexRule, context: dict[str, str]) -> dict[str, Any]:
        from ..agents.remote import RemoteAgentRegistry

        reg = RemoteAgentRegistry(self.p.engine)
        record = reg.get(rule.target)
        if record is None:
            return {"ok": False, "error": f"no remote agent '{rule.target}'"}
        task = _render(rule.task_template, context) or _default_task(rule, context)
        self._launch(
            reg.run(record, task, self.p.secrets.get), f"reflex-remote-{rule.id}"
        )
        return {"ok": True, "kind": "remote_agent", "agent": rule.target}

    async def _run_session(self, rule: ReflexRule, context: dict[str, str]) -> dict[str, Any]:
        task = _render(rule.task_template, context) or _default_task(rule, context)
        session = await self.orch.create_session(task, AgentType.SUPERVISOR)
        self._launch(self.orch.run_session(session.id), session.id)
        return {"ok": True, "kind": "session", "session_id": session.id}

    # -- helpers -----------------------------------------------------------
    def _launch(self, coro: Any, session_id: str) -> Any:
        if self.spawn_bg is not None:
            return self.spawn_bg(session_id, coro)
        import asyncio

        return asyncio.ensure_future(coro)

    async def _publish(self, rule: ReflexRule, result: dict[str, Any]) -> None:
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(
                EventType.REFLEX_FIRED,
                {
                    "rule": rule.name,
                    "source": rule.source,
                    "match": rule.match,
                    "action": rule.action,
                    "ok": bool(result.get("ok")),
                    "detail": result.get("error") or result.get("kind", ""),
                },
            )
        except Exception:  # noqa: BLE001 — the bus must never block a reflex
            pass


def _text_of(body: Any) -> str:
    """Best-effort human text from a webhook body for {text} interpolation."""
    if isinstance(body, str):
        return body[:2000]
    if isinstance(body, dict):
        for key in ("text", "message", "body", "content", "title"):
            v = body.get(key)
            if isinstance(v, str) and v.strip():
                return v[:2000]
    return ""


def _default_task(rule: ReflexRule, context: dict[str, str]) -> str:
    signal = context.get("text") or context.get("body") or ""
    where = f"{rule.source}" + (f" '{rule.match}'" if rule.match else "")
    if signal:
        return f"An inbound {where} signal fired the '{rule.name}' reflex:\n\n{signal}"
    return f"The '{rule.name}' reflex fired from {where}. Decide what to do and act."
