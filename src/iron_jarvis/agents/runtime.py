"""Agent Runtime + lifecycle (§11, §13).

Runs a single agent's perceive→act loop: ask the model (via the router) for the
next action, execute any tool calls (gated by the permission engine), feed
results back, and repeat until the model finalizes or the step budget is spent.
Lifecycle state transitions are persisted and emitted on the event bus.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, Session
from ..providers.adapters.base import LLMMessage
from ..tools.base import ToolContext
from .types import AgentDefinition

_TERMINAL = {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}

#: Cap on tool output fed into the MODEL CONTEXT (the full output still lands in the
#: DB transcript). Without it, a large read/shell/grep result is re-sent on every
#: subsequent step of the loop — O(n^2) token growth at full input price.
_MAX_TOOL_CONTEXT_CHARS = 16000


class AgentRuntime:
    def __init__(self, platform) -> None:
        self.p = platform

    def _save(self, run: AgentRun) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(run)
            db.commit()

    def _project_context(self, session: Session) -> str:
        """The context-spine block for a project-tagged session: the project's
        brief + the last few sibling sessions' outcomes (bounded)."""
        from sqlmodel import select

        from ..core.models import Project

        with session_scope(self.p.engine) as db:
            project = db.get(Project, session.project_id)
            if project is None:
                return ""
            siblings = list(
                db.exec(
                    select(Session)
                    .where(
                        Session.project_id == session.project_id,
                        Session.id != session.id,
                    )
                    .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                    .limit(5)
                )
            )
        lines = [
            "\n\n# Project context",
            f"You are working within the user's project: {project.name}",
        ]
        if project.brief.strip():
            lines.append(f"Project brief: {project.brief.strip()[:2000]}")
        if project.root.strip():
            lines.append(f"Project folder: {project.root.strip()}")
        recent = [
            f"- [{s.status.value}] {s.task[:80]}: {(s.summary or '(no summary)')[:160]}"
            for s in siblings
        ]
        if recent:
            lines.append("Recent activity in this project (newest first):")
            lines.extend(recent)
        lines.append(
            "Use this context when relevant; stay consistent with prior work in the project."
        )
        return "\n".join(lines)

    async def _set_state(
        self, run: AgentRun, state: AgentState, session_id: str
    ) -> None:
        prev = run.state
        run.state = state
        if state in _TERMINAL:
            run.finished_at = utcnow()
        self._save(run)
        await self.p.event_bus.publish(
            EventType.AGENT_STATE_CHANGED,
            {"run_id": run.id, "from": prev.value, "to": state.value},
            session_id=session_id,
        )

    async def run(
        self,
        session: Session,
        agent_def: AgentDefinition,
        parent_id: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            session_id=session.id,
            parent_id=parent_id,
            agent_type=agent_def.type,
            provider=session.provider,
            model=session.model,
            state=AgentState.CREATED,
        )
        self._save(run)

        await self._set_state(run, AgentState.INITIALIZING, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_STARTED,
            {"agent": agent_def.type.value, "run_id": run.id, "task": session.task},
            session_id=session.id,
        )
        await self._set_state(run, AgentState.RUNNING, session.id)

        workspace = Path(session.workspace_path)
        messages: list[LLMMessage] = [LLMMessage(role="user", content=session.task)]
        tool_specs = self.p.registry.specs(agent_def.tools)
        final_text = ""

        system_prompt = agent_def.system_prompt
        # Auto-inject any configured default skills (§23) into the prompt.
        default_skills = getattr(self.p.config, "default_skills", None)
        if default_skills:
            try:
                system_prompt = self.p.skills.inject(system_prompt, default_skills)
            except Exception:
                pass
        # Self-correction: fold accumulated lessons + user preferences into the
        # system prompt so every run is a little smarter than the last.
        learning = getattr(self.p, "learning", None)
        if learning is not None:
            try:
                system_prompt = learning.apply_to_prompt(system_prompt)
            except Exception:  # never block a run on the learning layer
                pass
        # CONTEXT SPINE: a session tagged into a project carries the project's
        # brief + recent activity, so chat/terminals/workflows share one thread
        # of "what the user is working on". Bounded; never blocks a run.
        if session.project_id:
            try:
                system_prompt += self._project_context(session)
            except Exception:  # noqa: BLE001 — the spine must never break a run
                pass
        # ENVIRONMENT: agents kept mistaking the scratch workspace for the
        # user's real files ("list my Downloads" -> listing an empty sandbox,
        # burning tokens). Spell out the split + the real home directory.
        try:
            system_prompt += (
                "\n\n# Environment\n"
                f"- The user's real home directory: {Path.home()}\n"
                "- Your file tools (read_file/write_file/list_files) operate in a "
                "SCRATCH workspace — it is NOT where the user's files live.\n"
                "- For the user's REAL folders/files (Downloads, Documents, ...) "
                "use list_folder / read_document / convert_document with "
                "ABSOLUTE paths (reads are policy-gated).\n"
            )
        except Exception:  # noqa: BLE001
            pass

        for step in range(self.p.config.max_agent_steps):
            route = await self.p.router.complete(
                provider=session.provider,
                model=session.model,
                system=system_prompt,
                messages=messages,
                tools=tool_specs,
                session_id=session.id,
                # Task class for the (opt-in) self-tuning router: the agent type.
                task_class=agent_def.type.value,
            )
            resp = route.response
            run.steps = step + 1
            run.provider, run.model = route.provider, route.model
            usage = getattr(resp, "usage", None) or {}
            run.input_tokens += int(usage.get("input_tokens", 0) or 0)
            run.output_tokens += int(usage.get("output_tokens", 0) or 0)

            if not resp.wants_tools:
                final_text = resp.text
                self._save(run)
                break

            messages.append(
                LLMMessage(
                    role="assistant", content=resp.text, tool_calls=resp.tool_calls
                )
            )
            ctx = ToolContext(
                workspace=workspace,
                session_id=session.id,
                agent_run_id=run.id,
                config=self.p.config,
                event_bus=self.p.event_bus,
                engine=self.p.engine,
            )
            # Run the turn's tool calls as a TEAM: gather them concurrently so
            # multiple delegate/blackboard calls execute at once. registry.invoke
            # opens its own session_scope per call (no shared Session across the
            # coroutines), records its own ToolInvocation, and publishes its own
            # event — so concurrency is safe. ``return_exceptions=True`` isolates a
            # failure/denial in one call so it never cancels its siblings. Results
            # are then appended in the ORIGINAL call order (the model maps tool
            # results to calls positionally), keeping behavior deterministic.
            async def _invoke(tc):
                return await self.p.registry.invoke(
                    tc.name,
                    tc.arguments,
                    ctx,
                    self.p.permissions,
                    agent_def.permission_overrides,
                )

            results = await asyncio.gather(
                *(_invoke(tc) for tc in resp.tool_calls),
                return_exceptions=True,
            )
            for tc, result in zip(resp.tool_calls, results):
                if isinstance(result, asyncio.CancelledError):
                    # Cooperative cancellation (user stopped the run) must still
                    # unwind — never swallow it into a tool result.
                    raise result
                if isinstance(result, BaseException):
                    # registry.invoke already traps tool exceptions, so this only
                    # fires for an error OUTSIDE the tool (e.g. the event bus); turn
                    # it into this call's error result without aborting the siblings.
                    content = f"{type(result).__name__}: {result}"
                else:
                    content = result.output if result.ok else (result.error or "error")
                    # Fence externally-sourced tool output (documents/PDF/notes/
                    # memory/file-search) as untrusted DATA and scan it for
                    # prompt-injection — consistent with web_search/browse — so a
                    # planted file can't inject instructions into the model context.
                    tool = self.p.registry.get(tc.name)
                    if result.ok and getattr(tool, "returns_untrusted_content", False):
                        from ..computeruse.safety import detect_injection, wrap_untrusted

                        inj = detect_injection(content)
                        content = wrap_untrusted(
                            f"[content withheld — suspected {inj['category']}: {inj['reason']}]"
                            if inj["flagged"]
                            else content
                        )
                if len(content) > _MAX_TOOL_CONTEXT_CHARS:
                    dropped = len(content) - _MAX_TOOL_CONTEXT_CHARS
                    content = (
                        content[:_MAX_TOOL_CONTEXT_CHARS]
                        + f"\n[... truncated {dropped} chars — full output in the transcript]"
                    )
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=content,
                    )
                )
            self._save(run)
        else:
            run.result = "stopped: reached max steps before completion"
            await self._set_state(run, AgentState.FAILED, session.id)
            await self.p.event_bus.publish(
                EventType.AGENT_COMPLETED,
                {"run_id": run.id, "ok": False, "result": run.result},
                session_id=session.id,
            )
            return run

        run.result = final_text or "(no final message)"
        await self._set_state(run, AgentState.COMPLETED, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_COMPLETED,
            {"run_id": run.id, "ok": True, "result": run.result},
            session_id=session.id,
        )
        return run
