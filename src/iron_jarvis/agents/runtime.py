"""Agent Runtime + lifecycle (§11, §13).

Runs a single agent's perceive→act loop: ask the model (via the router) for the
next action, execute any tool calls (gated by the permission engine), feed
results back, and repeat until the model finalizes or the step budget is spent.
Lifecycle state transitions are persisted and emitted on the event bus.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, AgentType, Session
from ..providers.adapters.base import LLMMessage
from ..tools.base import ToolContext
from . import decompose as _decompose
from .types import AgentDefinition

_TERMINAL = {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}


def is_direct_workspace(config, workspace_path: str | Path | None) -> bool:
    """True when a session runs DIRECTLY in a user-chosen folder (a project's
    in-folder task, created with ``workspace_root=...``), not in a disposable
    dir under ``workspaces_dir``.

    The honest signal: ``create_session`` only ever places a session OUTSIDE the
    managed ``workspaces_dir`` when the caller passed ``workspace_root``, so the
    stored ``Session.workspace_path`` alone answers it — no guessed flag. Shared
    by the runtime (environment prompt) and the orchestrator (rerun) so both
    always agree on which kind of workspace a session has."""
    if not workspace_path:
        return False
    try:
        ws = Path(workspace_path).resolve()
        managed = Path(config.workspaces_dir).resolve()
    except (OSError, ValueError):  # unresolvable path -> the safe default
        return False
    return ws != managed and managed not in ws.parents


#: Cap on tool output fed into the MODEL CONTEXT (the full output still lands in the
#: DB transcript). Without it, a large read/shell/grep result is re-sent on every
#: subsequent step of the loop — O(n^2) token growth at full input price.
_MAX_TOOL_CONTEXT_CHARS = 16000


def _effective(messages, system_prompt: str, summary: str, covers: int):
    """Apply an existing compaction WITHOUT touching the caller's list.

    ``messages[0]`` (the task) always rides at the front verbatim; *covers*
    counts messages after it that the summary has absorbed. Returns
    ``(messages, system)`` for this step only — the loop keeps appending to the
    real list, which stays the run's full history and what gets persisted.
    """
    if not summary or covers <= 0 or not messages:
        return messages, system_prompt
    head, tail = messages[:1], messages[1 + covers :]
    return [*head, *tail], f"{system_prompt}\n\n{summary}"


class AgentRuntime:
    def __init__(self, platform) -> None:
        self.p = platform

    async def _maybe_compact(
        self,
        session,
        *,
        messages,
        system_prompt: str,
        summary: str,
        covers: int,
        futile: bool,
        window,
        sink,
    ):
        """Compact this run's older steps when the window is nearly full.

        Returns the (possibly unchanged) ``(summary, covers)``. Auto-only and
        never asks: there is no human attached to a running agent, and the
        alternative at this fill level is a step that silently loses the
        beginning of the run.

        The summary is checked against this session's EXECUTION LEDGER before it
        is allowed anywhere near the prompt — ``outcome.session_result`` derives
        what the run actually did from ``ToolInvocation`` + ``UndoJournal``, so a
        model claiming a file it never wrote has that claim removed rather than
        fed back to itself as history.

        Every failure path returns the input unchanged: no real model, nothing
        big enough to cover, a provider error, or a summary that survived
        verification empty. The deterministic recap then handles overflow
        exactly as it did in v1.152.0.
        """
        from ..context import compaction as _C

        if futile:
            return summary, covers, futile
        try:
            from ..daemon.chat_turn import _compaction_enabled, _compaction_thresholds
            from ..context.budget import estimate_tokens

            deps = SimpleNamespace(platform=self.p)
            if not _compaction_enabled(deps):
                return summary, covers, futile
            _suggest, auto_at = _compaction_thresholds(deps)

            eff_messages, eff_system = _effective(
                messages, system_prompt, summary, covers
            )
            raw = estimate_tokens(eff_system) + sum(
                estimate_tokens(getattr(m, "content", "") or "") + 4
                for m in eff_messages
            )
            if _C.pressure(raw, int(window or 0)) < auto_at:
                return summary, covers, futile

            pairs, new_covers = _C.agent_coverage(messages, covered=covers)
            if not pairs or new_covers <= covers:
                return summary, covers, futile

            complete = self.p_compaction_complete()
            if complete is None:
                return summary, covers, futile

            paths, tools = _C.ledger_facts(self.p.engine, session.id)
            out = await _C.compact_messages(
                pairs,
                complete=complete,
                ledger_paths=paths,
                ledger_tools=tools,
                trigger="auto",
                # The summary being REPLACED rides along: coverage always starts
                # from the beginning, so without this the new summary would
                # silently drop everything the old one said.
                prior=summary,
            )
            if not out.ok:
                return summary, covers, futile

            # FUTILITY GUARD. When the task alone dominates the window, covering
            # every step still leaves pressure above the ceiling — and the next
            # few steps would each buy another useless model call. Measure the
            # result; if it did not get us under, take this one and stop trying.
            # The planner's trim-and-clip path then handles the overflow, which
            # is honest about what it is doing.
            after_messages, after_system = _effective(
                messages, system_prompt, out.summary, new_covers
            )
            after_raw = estimate_tokens(after_system) + sum(
                estimate_tokens(getattr(m, "content", "") or "") + 4
                for m in after_messages
            )
            futile = _C.pressure(after_raw, int(window or 0)) >= auto_at

            if sink:
                sink.phase(
                    "running",
                    f"compacted {new_covers - covers} earlier message(s) to fit "
                    f"the context window",
                )
            await self.p.event_bus.publish(
                EventType.CONTEXT_COMPACTED,
                {
                    "run_id": getattr(session, "id", ""),
                    "covers": new_covers,
                    "stripped": out.stripped,
                    "trigger": "auto",
                    "provider": out.provider,
                    "model": out.model,
                },
                session_id=session.id,
            )
            return out.summary, new_covers, futile
        except Exception:  # noqa: BLE001 — compaction is an optimisation; a
            # failure must leave the run exactly as it was.
            return summary, covers, futile

    def p_compaction_complete(self):
        """The one-shot completion callable, or None when only the mock exists.

        The daemon builds it (``d._compaction_complete``); the runtime reaches
        it through the platform so a bare ``AgentRuntime`` in a unit test simply
        gets None and skips compaction.
        """
        factory = getattr(self.p, "_compaction_complete", None)
        if factory is None:
            return None
        try:
            return factory()
        except Exception:  # noqa: BLE001
            return None

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
        if getattr(project, "instructions", "").strip():
            lines.append(
                "Project instructions (follow these):\n"
                + project.instructions.strip()[:2000]
            )
        if project.brief.strip():
            lines.append(f"Project brief: {project.brief.strip()[:2000]}")
        if project.root.strip():
            lines.append(f"Project folder: {project.root.strip()}")
        # Project KNOWLEDGE — the whole base when small, else the parts relevant
        # to this task (cosine over the stored vectors). Best-effort.
        try:
            from ..projects.knowledge import ground as _ground

            know = _ground(self.p, session.project_id, session.task)
            if know.strip():
                lines.append("Project knowledge (reference material):\n" + know)
        except Exception:  # noqa: BLE001 — grounding must never break a run
            pass
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

    async def _route_stream(self, **kwargs):
        """FX-01 token-stream passthrough for the perceive->act loop.

        Yields the router's streaming frames -- ``{"type": "text", "text": <delta>}``
        deltas followed by a terminal ``{"type": "final", "response": LLMResponse,
        "provider": str, "model": str}``. When the router has no ``stream`` method
        yet (it is added by the FX-01 central wiring), degrade GRACEFULLY to a single
        text chunk over ``complete()`` so the non-streaming path is exactly preserved
        and the offline suite stays green regardless of merge order. Same kwargs as
        ``router.complete``.
        """
        streamer = getattr(self.p.router, "stream", None)
        if streamer is not None:
            async for ev in streamer(**kwargs):
                yield ev
            return
        route = await self.p.router.complete(**kwargs)
        if route.response.text:
            yield {"type": "text", "text": route.response.text}
        yield {
            "type": "final",
            "response": route.response,
            "provider": route.provider,
            "model": route.model,
        }

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
        # FX-01 side-channel: resolve the ephemeral per-run stream sink (token
        # deltas + live tool frames -> SSE). A no-op when no browser is subscribed,
        # and absent entirely when the platform exposes no stream hub.
        hub = getattr(self.p, "streams", None)
        sink = hub.sink(session.id, run.id) if hub is not None else None

        await self._set_state(run, AgentState.INITIALIZING, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_STARTED,
            {"agent": agent_def.type.value, "run_id": run.id, "task": session.task},
            session_id=session.id,
        )
        await self._set_state(run, AgentState.RUNNING, session.id)

        workspace = Path(session.workspace_path)
        # Per-session tool grant (bundle-approved up front): these perm_keys are
        # treated as allowed for THIS session, so an "ask" tool the user opted
        # into doesn't fail-close in the daemon. Never lifts a hard "deny".
        session_allow: set[str] = set()
        try:
            raw = json.loads(getattr(session, "allow_tools_json", "") or "[]")
            if isinstance(raw, list):
                session_allow = {str(t) for t in raw if t}
        except (ValueError, TypeError):
            session_allow = set()
        messages: list[LLMMessage] = [LLMMessage(role="user", content=session.task)]
        tool_specs = self.p.registry.specs(agent_def.tools)

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
        # THE IDENTITY SPINE (v1.144.0) — the fix for "it communicates
        # differently depending on which model/surface answered". Until now the
        # persona + the user's preferences reached CHAT ONLY: an agent run
        # started from agent_def.system_prompt (the ROLE) and knew nothing about
        # who it was writing for or how they read. Both sections land here, in
        # the same order and with the same text chat uses, so the user reads one
        # Iron Jarvis whether a 14B local model or a cloud model took the work.
        # Bounded + never-raising, exactly like the injections below it.
        try:
            from ..personas.voice import voice_section
            from ..profile import profile_block

            system_prompt += voice_section(self.p)
            _profile = profile_block(self.p)
            if _profile:
                system_prompt += "\n\n" + _profile
        except Exception:  # noqa: BLE001 — identity must never break a run
            pass
        # MEMORY AWARENESS (v1.141.0): every agent type sees a compact index
        # of WHAT memory exists (bases + graph size + recent note TITLES —
        # never content) so it reaches for `recall` instead of assuming
        # ignorance. All types get it — unlike the roster below, it is cheap
        # (local reads only, no network/embedder) and short (≤ ~700 chars).
        # Same precedent as skills + learning: bounded, best-effort, never
        # blocks a run.
        try:
            from ..memory.index_block import memory_index_block

            _mem_index = memory_index_block(self.p, project_id=session.project_id)
            if _mem_index:
                system_prompt += "\n\n" + _mem_index
        except Exception:  # noqa: BLE001 — awareness must never break a run
            pass
        # CAPABILITY ROSTER (v1.139.0): ONLY the agent types that make
        # delegation choices see the roster — the supervisor picks `delegate`
        # targets, the planner assigns work to agents; a builder/reviewer run
        # would just carry the noise. A dynamic agent BASED on one of these
        # types inherits the injection (agent_def.type is its base type).
        # Same precedent as the skills + learning injections above: bounded,
        # best-effort, never blocks a run.
        if agent_def.type in (AgentType.SUPERVISOR, AgentType.PLANNER):
            try:
                from .roster import roster_block

                _roster = roster_block(self.p)
                if _roster:
                    system_prompt += "\n\n" + _roster
            except Exception:  # noqa: BLE001 — the roster must never break a run
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
        # An in-folder project session is the OPPOSITE case — its file tools
        # operate directly in the user's own folder, and the scratch line here
        # contradicted the task text ("you are working directly inside the
        # project folder") — so state whichever is actually true.
        try:
            if is_direct_workspace(self.p.config, session.workspace_path):
                system_prompt += (
                    "\n\n# Environment\n"
                    f"- The user's real home directory: {Path.home()}\n"
                    "- Your file tools (read_file/write_file/list_files) operate "
                    f"directly in the project folder at {workspace} — these ARE "
                    "the user's real files; treat them as real data, not a "
                    "scratch sandbox.\n"
                    "- For the user's OTHER folders/files (Downloads, Documents, "
                    "...) use list_folder / read_document / convert_document "
                    "with ABSOLUTE paths (reads are policy-gated).\n"
                )
            else:
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
        # MEMORY FABRIC: fold in the most relevant snippets from every store
        # (files, notes, memory graph, this project's knowledge, lessons, past
        # sessions) so the run starts already grounded in what the user knows —
        # no explicit `recall` call needed. Bounded, best-effort, never blocks.
        fabric = getattr(self.p, "fabric", None)
        if fabric is not None:
            try:
                grounding = fabric.ground(session.task, project_id=session.project_id)
                if grounding:
                    system_prompt += grounding
            except Exception:  # noqa: BLE001 — grounding must never break a run
                pass

        # v1.132.0 SHORT-HORIZON DECOMPOSITION: a local model served through the
        # prompted-tools scaffold loses the thread over a long flat loop, so a
        # plausibly multi-step task is split into plan → execute → verify →
        # assemble (agents/decompose.py) — reusing this SAME run record, state
        # transitions, event bus, and sink, so the dashboard sees a normal run.
        # Native tool-callers, simple tasks, and the flag-off case keep the flat
        # loop byte-for-byte unchanged; a planner that declines (degenerate/
        # unparseable plan) falls back to it too.
        final_text: str | None = None
        if _decompose.should_decompose(self.p, session):
            final_text = await _decompose.run_decomposed(
                self,
                run,
                session,
                agent_def,
                system_prompt=system_prompt,
                tool_specs=tool_specs,
                session_allow=session_allow,
                sink=sink,
            )
        if final_text is None:
            # The FLAT loop has no planning stage, so it says so plainly rather
            # than leaving the client on a phase-less spinner (v1.149.0). Every
            # run now reports a phase; a surface that shows one is never left
            # guessing which lane it got.
            if sink:
                sink.phase("running", "working the task")
            finished, final_text = await self.perceive_act(
                run,
                session,
                agent_def,
                system_prompt=system_prompt,
                messages=messages,
                tool_specs=tool_specs,
                session_allow=session_allow,
                sink=sink,
                max_steps=self.p.config.max_agent_steps,
            )
            if not finished:
                run.result = "stopped: reached max steps before completion"
                await self._set_state(run, AgentState.FAILED, session.id)
                await self.p.event_bus.publish(
                    EventType.AGENT_COMPLETED,
                    {"run_id": run.id, "ok": False, "result": run.result},
                    session_id=session.id,
                )
                if sink:
                    sink.done(ok=False, result=run.result)
                return run

        run.result = final_text or "(no final message)"
        await self._set_state(run, AgentState.COMPLETED, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_COMPLETED,
            {"run_id": run.id, "ok": True, "result": run.result},
            session_id=session.id,
        )
        if sink:
            sink.done(ok=True, result=run.result)
        return run

    async def perceive_act(
        self,
        run: AgentRun,
        session: Session,
        agent_def: AgentDefinition,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tool_specs: list,
        session_allow: set[str],
        sink,
        max_steps: int,
    ) -> tuple[bool, str]:
        """The per-round route → tool-execute body of the perceive→act loop —
        extracted (v1.132.0) as THE reusable seam so the decomposition
        mini-loops run the exact same routing/streaming/tool machinery as the
        flat loop, never a copy. Returns ``(finished, final_text)``:
        ``finished=False`` means ``max_steps`` rounds were spent before the
        model finalized (the caller decides whether that fails the run — the
        flat path — or just this step — a decomposition mini-loop).
        ``run.steps`` ACCUMULATES across calls, so a decomposed run's rounds
        all count on the one AgentRun record."""
        workspace = Path(session.workspace_path)
        # One persisted context.trimmed event per run, not one per step: a
        # 12-step run over budget would otherwise bury the timeline in twelve
        # identical notices.
        trim_reported = False
        # COMPACTION STATE (v1.153.0). An agent loop has no one to ask mid-run,
        # so unlike chat it never offers the choice — it compacts on its own at
        # the ceiling and reports it. `_cpt_covers` counts messages consumed
        # AFTER index 0: the task is never covered, because a run whose goal
        # survives only as a paraphrase can drift off what it was asked to do.
        _cpt_summary, _cpt_covers, _cpt_futile = "", 0, False
        for _ in range(max_steps):
            # CONTEXT BUDGET (v1.152.0). The transcript grows by an assistant
            # turn plus every tool result on every step, and until now nothing
            # counted the tokens: the only guards were a 16k-char cap per tool
            # result and the step ceiling, which on a 32k local model is tens of
            # thousands of tokens before the system prompt is even added. Chat
            # got this in v1.146.0; this is where context actually gets big.
            #
            # `step_messages` / `step_system` are what THIS call sends; the loop
            # keeps appending to the real `messages` list, so the run's own
            # history (and the DB transcript) is never rewritten by trimming.
            step_messages, step_system = messages, system_prompt
            try:
                from ..context.agent_window import plan_agent_transcript
                from ..daemon.chat_turn import _context_window

                _win = _context_window(
                    SimpleNamespace(platform=self.p),
                    session.provider,
                    session.model,
                )
                # COMPACTION (v1.153.0) runs BEFORE the budget planner: a summary
                # it produces joins the system prompt and shortens the history,
                # both of which the planner then has to price. Everything below
                # works on EFFECTIVE values — the real `messages` list is the
                # run's own history and what gets persisted, and is never
                # rewritten.
                _cpt_summary, _cpt_covers, _cpt_futile = await self._maybe_compact(
                    session,
                    messages=messages,
                    system_prompt=system_prompt,
                    summary=_cpt_summary,
                    covers=_cpt_covers,
                    futile=_cpt_futile,
                    window=_win,
                    sink=sink,
                )
                _eff_messages, _eff_system = _effective(
                    messages, system_prompt, _cpt_summary, _cpt_covers
                )

                _plan = plan_agent_transcript(
                    _eff_messages,
                    window=_win,
                    system_text=_eff_system,
                )
                step_messages, step_system = _plan.messages, _eff_system
                if _plan.recap:
                    # The recap goes in the SYSTEM prompt, not the transcript —
                    # injecting it as a turn would put words in the model's own
                    # mouth that it never said.
                    step_system = f"{_eff_system}\n\n{_plan.recap}"
                if _plan.changed:
                    if _plan.clipped_task:
                        # Say this plainly: the agent is now working from a
                        # TRUNCATED goal, which is a result the user must be
                        # able to distrust. This model is too small for the job.
                        note = (
                            "the task is larger than this model's context "
                            "window and had to be cut — use a bigger model"
                        )
                    elif _plan.dropped_blocks:
                        note = (
                            f"trimmed context to fit ({_plan.dropped_blocks} "
                            f"earlier step(s) condensed)"
                        )
                    else:
                        note = "trimmed older tool output to fit"
                    if sink:  # live, for whoever is watching the run now
                        sink.phase("running", note)
                    if not trim_reported:  # persisted, for whoever reads it later
                        trim_reported = True
                        await self.p.event_bus.publish(
                            EventType.CONTEXT_TRIMMED,
                            {
                                "run_id": run.id,
                                "window": _plan.window,
                                "dropped_blocks": _plan.dropped_blocks,
                                "tools_trimmed": _plan.tools_trimmed,
                                "clipped_task": _plan.clipped_task,
                                "detail": note,
                            },
                            session_id=session.id,
                        )
            except Exception:  # noqa: BLE001 — a budgeting fault must never
                # break a run; the untrimmed transcript is what shipped before.
                step_messages, step_system = messages, system_prompt
            # FX-01: consume the router as a TOKEN STREAM. Text deltas are pushed to
            # the SSE sink the moment they arrive; the terminal ``final`` frame
            # carries the SAME aggregate LLMResponse (+ resolved provider/model) that
            # complete() would have returned, so everything downstream (usage
            # accounting, the tool loop, the persisted result) stays byte-identical.
            route_resp = None
            async for ev in self._route_stream(
                provider=session.provider,
                model=session.model,
                system=step_system,
                messages=step_messages,
                tools=tool_specs,
                session_id=session.id,
                # Task class for the (opt-in) self-tuning router: the agent type.
                task_class=agent_def.type.value,
            ):
                if ev.get("type") == "text":
                    if sink:
                        sink.token_delta(ev["text"])
                elif ev.get("type") == "final":
                    route_resp = ev
            resp = route_resp["response"]
            run.steps += 1
            run.provider, run.model = route_resp["provider"], route_resp["model"]
            if sink and run.steps == 1:
                sink.meta(run.provider, run.model)
            usage = getattr(resp, "usage", None) or {}
            step_in = int(usage.get("input_tokens", 0) or 0)
            step_out = int(usage.get("output_tokens", 0) or 0)
            run.input_tokens += step_in
            run.output_tokens += step_out
            # TX-01 audit: one persisted event PER LLM call so every token is
            # individually replayable on the timeline (the per-run aggregate lives
            # on AgentRun). Best-effort — never let telemetry break a run.
            try:
                from ..eval.pricing import cost_for

                await self.p.event_bus.publish(
                    EventType.LLM_COMPLETED,
                    {
                        "run_id": run.id,
                        "step": run.steps,
                        "provider": run.provider,
                        "model": run.model,
                        "input_tokens": step_in,
                        "output_tokens": step_out,
                        "cost_usd": cost_for(
                            run.provider, run.model, step_in, step_out
                        ),
                        "task_class": agent_def.type.value,
                    },
                    session_id=session.id,
                )
            except Exception:  # noqa: BLE001 — telemetry must never break the loop
                pass

            if not resp.wants_tools:
                self._save(run)
                return True, resp.text

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
                    session_allow=session_allow,
                )

            # FX-01: announce each tool call BEFORE the fan-out, with args redacted
            # (tool.redact_args) so no secret ever reaches the wire. Emitted HERE —
            # not inside the gathered coroutines — so frame order is deterministic.
            if sink:
                for tc in resp.tool_calls:
                    tool = self.p.registry.get(tc.name)
                    safe = tool.redact_args(tc.arguments) if tool else tc.arguments
                    sink.tool_started(tc.id, tc.name, safe)

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
                    # memory/file-search/MCP) as untrusted DATA and scan it for
                    # prompt-injection — consistent with web_search/browse — so a
                    # planted file can't inject instructions into the model context.
                    #
                    # The FAILURE path is fenced too (v1.98.1). It used to be gated
                    # on ``result.ok``, which held only while every such tool wrote
                    # its own error string ("read denied: ..."). MCP breaks that:
                    # an ``isError`` response returns the REMOTE text verbatim as
                    # ``.error``, so gating on ok let attacker-authored content skip
                    # the scan entirely. Both chat loops already fence unconditionally
                    # — this aligns the runtime with them.
                    tool = self.p.registry.get(tc.name)
                    if getattr(tool, "returns_untrusted_content", False):
                        from ..computeruse.safety import (
                            detect_injection,
                            wrap_untrusted,
                        )

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
                if sink:
                    sink.tool_finished(
                        tc.id,
                        tc.name,
                        ok=(
                            result.ok
                            if not isinstance(result, BaseException)
                            else False
                        ),
                        preview=str(content)[:500],
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
            if sink:
                sink.step_end(run.steps)
        # Round budget spent before the model finalized — the CALLER owns what
        # that means (whole-run failure vs. a single step's outcome).
        return False, ""
