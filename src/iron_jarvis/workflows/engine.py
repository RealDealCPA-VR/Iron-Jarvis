"""Workflow Engine (SPEC §24) — with step kinds, failure routing, parallel
groups, and the human gate (v1.121.0).

A workflow is an ordered list of steps. Step ``kind`` decides what a step IS:

  agent   (default) a real agent session on ``task`` — judgment work
  tool    ONE direct tool call (``tool`` + ``args``) — deterministic, zero LLM;
          the trust feature: a mature workflow converges from judgment to
          clockwork exactly where the user wants it
  ask     the human gate: the run PARKS durably (status ``waiting``), the
          question is delivered to the user's destinations, and an answer
          resumes the remaining steps — n8n's approval webhook, as a message
  notify  send a (templated) message to the user's destinations

Each step may also carry ``on_failure`` (halt | retry | skip — halt is the
old behavior), and a ``group``: consecutive steps sharing a group run
CONCURRENTLY. ``{{Step Name}}`` in tasks/args/messages interpolates an earlier
step's output.

TOML authoring shape (matches SPEC §24's example flavour)::

    name = "monthly_close"
    description = "Close the books"

    [[steps]]
    name = "gather"
    agent = "builder"
    task = "collect this month's receipts"
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentType, Project, SessionStatus
from .models import WorkflowRunRecord

#: Context-chaining bounds: each earlier-step summary is clipped, and the whole
#: injected block is capped so a long-running workflow can't blow up the prompt.
_MAX_STEP_SUMMARY = 1500
_MAX_CONTEXT = 4000

#: v1.170.0 bounds: a pre-seeded run input's value, and a tool step's
#: structured ``data`` (serialized JSON) persisted on the run record.
_MAX_INPUT_SUMMARY = 4000
_MAX_STEP_DATA = 8000
#: The origin tag's rule (mirrors daemon/schemas._ORIGIN_RE — the charset AND
#: the {1,64} length). Workflow names come from TOML/user text with no charset
#: restriction, so the stamp is LAUNDERED, not just clipped: a Session origin
#: the HTTP schema's validator would reject could never round-trip through
#: origin-consuming surfaces (the v1.166.0 rule).
_ORIGIN_MAX = 64
_ORIGIN_BAD_RX = re.compile(r"[^A-Za-z0-9:_\-. ]")


def _workflow_origin(name: str) -> str:
    """The Session origin stamp for a workflow-spawned step session: charset-
    laundered (out-of-charset characters become ``_``) and length-clipped."""
    return _ORIGIN_BAD_RX.sub("_", f"workflow:{name}")[:_ORIGIN_MAX]

#: What a step IS (v1.121.0). Unknown values coerce to "agent".
STEP_KINDS: tuple[str, ...] = ("agent", "tool", "ask", "notify")
#: What a failed step does to the run. Unknown values coerce to "halt".
ON_FAILURE: tuple[str, ...] = ("halt", "retry", "skip")


@dataclass
class Step:
    """One unit of work in a workflow (SPEC §24: step + agent + tool)."""

    name: str
    agent: str = "builder"
    task: str = ""
    tool: str | None = None
    #: v1.121.0 — agent | tool | ask | notify (see module docstring).
    kind: str = "agent"
    #: v1.121.0 — halt | retry (one re-attempt) | skip (continue the run).
    on_failure: str = "halt"
    #: v1.121.0 — consecutive steps sharing a group run concurrently.
    group: str | None = None
    #: v1.121.0 — tool-kind arguments; string values are templated.
    args: dict = field(default_factory=dict)
    #: v1.121.0 — notify/ask text (templated). For ask, THE question.
    message: str = ""
    #: v1.170.0 — verified steps: ``{files?: [str], summary_contains?: [str]}``.
    #: Empty dict = no expectations = zero behavior change. Checks run
    #: DETERMINISTICALLY after the step completes (see ``_expect_failure``).
    expect: dict = field(default_factory=dict)


@dataclass
class WorkflowDef:
    """A repeatable process: a named, ordered list of steps (SPEC §24)."""

    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    #: Optional EXPLICIT project pin (context spine): when set, every run is
    #: stamped with it and each step session is grounded in the project's
    #: brief/instructions/knowledge. None = project-agnostic — the globally
    #: active project never leaks into a workflow run.
    project_id: str | None = None


def _agent_type(name: str) -> AgentType:
    """Map a step's ``agent`` string to an :class:`AgentType`, default builder."""
    try:
        return AgentType(name)
    except ValueError:
        return AgentType.BUILDER


def _coerce_expect(raw: Any) -> dict:
    """Coerce a step's ``expect`` declaration to its canonical shape
    (``{files?: [str], summary_contains?: [str]}``), LENIENTLY — old rows and
    garbage shapes load as ``{}`` (no expectations), never raise. Recognized
    keys keep only non-blank string entries; unknown keys are dropped."""
    if not isinstance(raw, dict):
        return {}
    expect: dict = {}
    for key in ("files", "summary_contains"):
        vals = raw.get(key)
        if not isinstance(vals, list):
            continue
        cleaned = [str(v).strip() for v in vals if str(v).strip()]
        if cleaned:
            expect[key] = cleaned
    return expect


def load_workflow(data: dict) -> WorkflowDef:
    """Build a :class:`WorkflowDef` from a parsed mapping (e.g. TOML/JSON)."""
    steps: list[Step] = []
    for raw in data.get("steps", []) or []:
        kind = str(raw.get("kind") or "agent").strip().lower()
        on_failure = str(raw.get("on_failure") or "halt").strip().lower()
        args = raw.get("args")
        steps.append(
            Step(
                name=str(raw.get("name", "")),
                agent=str(raw.get("agent", "builder")),
                task=str(raw.get("task", "")),
                tool=raw.get("tool"),
                kind=kind if kind in STEP_KINDS else "agent",
                on_failure=on_failure if on_failure in ON_FAILURE else "halt",
                group=(str(raw.get("group") or "").strip() or None),
                args=args if isinstance(args, dict) else {},
                message=str(raw.get("message", "")),
                expect=_coerce_expect(raw.get("expect")),
            )
        )
    return WorkflowDef(
        name=str(data.get("name", "")),
        steps=steps,
        description=str(data.get("description", "")),
        # Optional explicit project pin — absent/empty both mean unpinned.
        project_id=(str(data.get("project_id") or "").strip() or None),
    )


def step_to_dict(s: Step) -> dict:
    """Serialize a step COMPLETELY — the run record persists this so a parked
    run can be resumed from the database alone after a restart."""
    return {
        "name": s.name,
        "agent": s.agent,
        "task": s.task,
        "tool": s.tool,
        "kind": s.kind,
        "on_failure": s.on_failure,
        "group": s.group,
        "args": s.args,
        "message": s.message,
        "expect": s.expect,
    }


def _read_toml_text(path_or_str: str | Path) -> str:
    """Return TOML text from either a filesystem path or a literal string."""
    if isinstance(path_or_str, Path):
        return path_or_str.read_text(encoding="utf-8")
    text = str(path_or_str)
    # Treat the argument as a path only if it actually points at a file; a
    # multi-line TOML string would raise on Path.is_file (guarded below).
    try:
        candidate = Path(text)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except (OSError, ValueError):
        pass
    return text


def load_workflow_toml(path_or_str: str | Path) -> WorkflowDef:
    """Load a workflow from a ``.toml`` file path or a raw TOML string."""
    data = tomllib.loads(_read_toml_text(path_or_str))
    return load_workflow(data)


_TEMPLATE_RX = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def render_template(text: str, outputs: dict[str, Any]) -> str:
    """Replace ``{{Step Name}}`` with that step's recorded summary/output.

    ``{{Step Name.data}}`` (v1.170.0) resolves to that step's bounded
    structured ``data`` — the serialized JSON a tool step recorded — so a
    downstream step can hand off structure, not just prose. An output whose
    literal name IS ``Step Name.data`` always wins the reference (the
    reserved-name spirit of the ``{{Trigger}}`` guard: the def owns its names).

    Unknown references render as an empty string rather than leaking the
    braces into an agent prompt or tool argument. Values are clipped so one
    verbose step can't blow up a downstream prompt.
    """
    def _sub(m: re.Match) -> str:
        ref = m.group(1).strip()
        out = outputs.get(ref)
        if isinstance(out, dict):
            return str(out.get("summary") or "")[:_MAX_STEP_SUMMARY]
        if ref.endswith(".data"):
            base = outputs.get(ref[: -len(".data")].strip())
            if isinstance(base, dict):
                val = base.get("data")
                if isinstance(val, str):
                    return val[:_MAX_STEP_DATA]
                if val is not None:
                    try:
                        return json.dumps(val, ensure_ascii=False, default=str)[
                            :_MAX_STEP_DATA
                        ]
                    except (TypeError, ValueError):
                        return ""
        return ""

    return _TEMPLATE_RX.sub(_sub, text or "")


def _render_args(args: dict, outputs: dict[str, Any]) -> dict:
    """Template every string value in a tool step's args (one level of nesting
    is plenty for tool schemas; deeper structures pass through untouched)."""
    rendered: dict = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            rendered[k] = render_template(v, outputs)
        elif isinstance(v, list):
            rendered[k] = [
                render_template(x, outputs) if isinstance(x, str) else x for x in v
            ]
        else:
            rendered[k] = v
    return rendered


def seed_inputs(
    workflow: WorkflowDef, inputs: dict[str, str] | None
) -> dict[str, dict]:
    """Turn run *inputs* into pre-seeded outputs (v1.170.0, contract 5).

    Each input becomes ``{status: "completed", summary: <value clipped>,
    kind: "input"}`` under its given name, so ``{{name}}`` templating just
    works with zero new machinery. A name colliding with a REAL step raises an
    honest ``ValueError`` (the ``{{Trigger}}`` guard lesson: a pre-seeded
    output would mark that step completed before it ran) — unlike the
    synthetic Trigger, an explicit input silently stepping aside would drop
    data the caller deliberately provided.
    """
    seeded: dict[str, dict] = {}
    for name, value in (inputs or {}).items():
        key = str(name).strip()
        if not key:
            raise ValueError("input names must be non-empty")
        if any(s.name == key for s in workflow.steps):
            raise ValueError(
                f"input '{key}' collides with a step name — the pre-seeded "
                f"output would mark that step already completed; rename the "
                f"input or the step"
            )
        seeded[key] = {
            "status": "completed",
            "summary": str(value)[:_MAX_INPUT_SUMMARY],
            "kind": "input",
        }
    return seeded


class _AskPark(Exception):
    """Raised inside the step loop when an ``ask`` step fires: the run must
    PARK (durably) instead of finishing. Carries what the record needs."""

    def __init__(self, index: int, step: str, question: str) -> None:
        super().__init__(question)
        self.index = index
        self.step = step
        self.question = question


class WorkflowEngine:
    """Runs :class:`WorkflowDef`s step-by-step via the Orchestrator (SPEC §24).

    Execution is ASYNC: :meth:`create_record` persists a ``running`` record up
    front (so a crash mid-run leaves a trace and the HTTP request returns at
    once), then :meth:`run_record` drives the steps IN THE BACKGROUND, updating
    the same record after each one. :meth:`run` (create + await) is kept for the
    synchronous callers (scheduling, the CLI, tests).

    ``orchestrator`` is the SHARED daemon orchestrator when the daemon spawns a
    run — the cancel route reaches the currently-running step session through it.
    Synchronous callers pass nothing and get a throwaway per-run one.
    """

    def __init__(self, platform, orchestrator=None) -> None:
        self.platform = platform
        self.orchestrator = orchestrator

    @staticmethod
    def _has_runnable_steps(workflow: WorkflowDef) -> bool:
        """A workflow is runnable only if it has at least one non-empty step —
        an empty plan must NOT report 'completed' (it masked mis-configuration)."""
        steps = workflow.steps or []
        return any(
            (s.name or "").strip() or (s.task or "").strip() for s in steps
        )

    def create_record(
        self, workflow: WorkflowDef, inputs: dict[str, str] | None = None
    ) -> WorkflowRunRecord:
        """Persist a fresh ``running`` record for *workflow* and return it.

        Raises ``ValueError`` for a zero-/empty-step workflow (the route turns
        that into a 400), and for an *input* whose name collides with a step
        (contract 5 — same 400 path). ``started_at`` is stamped HERE (at the
        true start), not at the end. Inputs are seeded into ``outputs_json`` at
        creation so the record is honest from its first read. The record is
        refreshed so it stays usable once detached.
        """
        if not self._has_runnable_steps(workflow):
            raise ValueError("workflow has no steps")
        seeded = seed_inputs(workflow, inputs)
        # FULL step serialization (v1.121.0): a parked run must be resumable
        # from the database alone, so the record carries everything.
        steps_meta = [step_to_dict(s) for s in workflow.steps]
        record = WorkflowRunRecord(
            workflow_name=workflow.name,
            status="running",
            # Workflows are their own module — a run is NOT tagged to whatever
            # project is globally active; it carries a project ONLY when the def
            # itself is explicitly pinned to one (None otherwise).
            project_id=workflow.project_id,
            steps_json=dumps(steps_meta),
            session_ids_json="[]",
            outputs_json=dumps(seeded) if seeded else "{}",
        )
        with session_scope(self.platform.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # un-expire attrs so the detached record stays usable
        return record

    async def run(self, workflow: WorkflowDef) -> WorkflowRunRecord:
        """Create the record AND run it to completion (synchronous callers)."""
        record = self.create_record(workflow)
        return await self.run_record(record, workflow)

    @staticmethod
    def _batches(steps: list[Step], start: int) -> list[list[int]]:
        """Split step indices (from *start*) into execution batches:
        consecutive steps sharing a non-empty ``group`` run concurrently;
        everything else is a singleton. ``ask`` steps are ALWAYS solo — a
        human gate inside a parallel batch has no coherent semantics."""
        batches: list[list[int]] = []
        i = start
        n = len(steps)
        while i < n:
            s = steps[i]
            if s.group and s.kind != "ask":
                j = i
                batch = []
                while j < n and steps[j].group == s.group and steps[j].kind != "ask":
                    batch.append(j)
                    j += 1
                batches.append(batch)
                i = j
            else:
                batches.append([i])
                i += 1
        return batches

    async def run_record(
        self,
        record: WorkflowRunRecord,
        workflow: WorkflowDef,
        start_index: int = 0,
        outputs: dict[str, Any] | None = None,
        session_ids: list[str] | None = None,
        inputs: dict[str, str] | None = None,
    ) -> WorkflowRunRecord:
        """Drive *workflow*'s steps, updating *record* in place as it goes.

        After each step: ``outputs[name] = {session_id?, status, summary, …}``.
        A failed step consults its ``on_failure`` (halt stops the run — the old
        behavior — retry re-attempts once, skip continues); an ``ask`` step
        PARKS the run as ``waiting`` until POST /workflows/runs/{id}/answer.
        A cancel (status flips to ``cancelling`` in the DB, checked before every
        batch, plus the in-flight session is cancelled) stops it ``cancelled``.
        Each later agent step's task is enriched with prior steps' summaries.
        ``inputs`` (contract 5) seed pre-completed outputs so ``{{name}}``
        templating resolves them; an explicit ``outputs`` entry wins a key
        collision (the resume path re-plays outputs that already contain the
        seeds — re-seeding must not overwrite later truth).

        A step whose recorded output is ALREADY ``completed`` is never
        re-executed (v1.170.0): its output stands and no step events fire.
        That is what makes resume honest — re-running a completed notify
        re-messages the user, a completed tool re-writes files, a completed
        agent re-spawns a session re-doing finished work. Safe for every
        caller: ``seed_inputs`` rejects step-name collisions, the reflex
        Trigger seed steps aside on collision, and ``resume_after_answer``
        starts past the answered ask, so a pending step can carry a
        pre-completed output ONLY on a resume replaying recorded truth.
        """
        # Lazy import: the orchestrator pulls in the agent runtime, which would
        # create an import cycle if imported at module load time.
        from ..agents.orchestrator import Orchestrator

        orch = self.orchestrator or Orchestrator(self.platform)
        run_id = record.id
        # Resolve the pinned project's folder ONCE for the whole run (None when
        # unpinned, or when the folder is missing on disk — see the helper).
        workspace_root = self._project_workspace_root(workflow.project_id)
        # Tool steps share ONE workspace per run (not one per step/retry): a
        # write_document -> read_file chain must see its own files, and a per-
        # step mkdtemp litters %TEMP% forever. Lazy so agent-only runs make none.
        _tool_ws: dict[str, str] = {}

        def tool_workspace() -> str:
            if workspace_root:
                return workspace_root
            if "d" not in _tool_ws:
                _tool_ws["d"] = tempfile.mkdtemp(prefix=f"ijwf-{run_id[:12]}-")
            return _tool_ws["d"]

        steps = list(workflow.steps)
        session_ids = list(session_ids or [])
        outputs = {**seed_inputs(workflow, inputs), **dict(outputs or {})}
        final_status = "completed"

        if outputs:
            # Resume fidelity (v1.170.0): persist the merged seeds (inputs, a
            # reflex {{Trigger}}, caller outputs) BEFORE the first batch.
            # outputs_json was otherwise first written only after batch 1
            # settled, so a daemon death during the first step lost any
            # in-memory-only seed and the resumed run silently rendered
            # {{Trigger}} as '' — different behavior from the original run.
            self._update_record(run_id, outputs=outputs)

        def _completed_chain() -> list[tuple[str, str]]:
            chain = []
            for s in steps:
                o = outputs.get(s.name)
                if isinstance(o, dict) and o.get("status") == "completed":
                    chain.append((s.name, str(o.get("summary") or "")))
            return chain

        for batch in self._batches(steps, start_index):
            # Cancellation is cooperative: re-read the authoritative status
            # the cancel route wrote before starting each batch.
            current = self._get_record(run_id)
            if current is not None and current.status == "cancelling":
                final_status = "cancelled"
                for s in (steps[i] for i in range(batch[0], len(steps))):
                    outputs.setdefault(s.name, {"status": "skipped"})
                break

            # Already-completed steps are NOT re-run (see docstring): keep
            # their recorded output, fire no events, execute only the rest.
            pending = [
                i
                for i in batch
                if not (
                    isinstance(outputs.get(steps[i].name), dict)
                    and outputs[steps[i].name].get("status") == "completed"
                )
            ]
            if not pending:
                continue

            results = await asyncio.gather(
                *(
                    self._exec_step(
                        orch, run_id, workflow, steps[i], i, len(steps),
                        outputs, _completed_chain(), workspace_root,
                        session_ids, tool_workspace,
                    )
                    for i in pending
                ),
                return_exceptions=True,
            )

            batch_failed = False
            batch_cancelled = False
            ask: _AskPark | None = None
            for i, res in zip(pending, results):
                step = steps[i]
                if isinstance(res, _AskPark):
                    ask = res
                    continue
                if isinstance(res, asyncio.CancelledError):
                    batch_cancelled = True
                    outputs.setdefault(step.name, {"status": "cancelled"})
                    continue
                if isinstance(res, BaseException):
                    # An unexpected executor crash is a failed step.
                    outputs[step.name] = {
                        "status": "failed",
                        "summary": f"{type(res).__name__}: {res}",
                        "kind": step.kind,
                    }
                    res = outputs[step.name]
                outputs[step.name] = res
                st = res.get("status")
                if st == "cancelled":
                    # Whether via the run's cancel route or an out-of-band
                    # session cancel, a cancelled step must cancel the RUN —
                    # continuing past the hole (and finalizing "completed")
                    # would silently drop a middle step's work.
                    batch_cancelled = True
                elif st == "failed" and not res.get("handled"):
                    batch_failed = True

            self._update_record(
                run_id,
                current_session_id=None,
                session_ids=session_ids,
                outputs=outputs,
            )

            if ask is not None:
                latest = self._get_record(run_id)
                if latest is not None and latest.status == "cancelling":
                    # A cancel landed while the ask batch executed — honor
                    # it instead of parking (and notifying) a dead run.
                    final_status = "cancelled"
                    for s in (steps[i] for i in range(batch[0], len(steps))):
                        outputs.setdefault(s.name, {"status": "skipped"})
                    break
                # PARK (v1.121.0): the run waits durably for the user.
                self._update_record(
                    run_id,
                    status="waiting",
                    waiting_json=dumps(
                        {"index": ask.index, "step": ask.step, "question": ask.question}
                    ),
                )
                await self.platform.event_bus.publish(
                    "workflow.waiting",
                    {
                        "run_id": run_id,
                        "workflow": workflow.name,
                        "step": ask.step,
                        "question": ask.question,
                    },
                )
                self._deliver(
                    f"Workflow “{workflow.name}” needs you: {ask.question} "
                    f"— answer it in chat or on the Workflows page."
                )
                refreshed = self._get_record(run_id)
                return refreshed if refreshed is not None else record

            if batch_cancelled:
                final_status = "cancelled"
                for s in (steps[i] for i in range(batch[-1] + 1, len(steps))):
                    outputs.setdefault(s.name, {"status": "skipped"})
                break
            if batch_failed:
                # A halting failure stops the workflow: the rest never ran.
                final_status = "failed"
                for s in (steps[i] for i in range(batch[-1] + 1, len(steps))):
                    outputs.setdefault(s.name, {"status": "skipped"})
                break

        # A cancel that arrived during the final batch has no later pre-batch
        # check to catch it — re-read so "cancelling" can never finalize as a
        # success.
        latest = self._get_record(run_id)
        if latest is not None and latest.status == "cancelling":
            final_status = "cancelled"
        final = self._update_record(
            run_id,
            status=final_status,
            current_session_id=None,
            session_ids=session_ids,
            outputs=outputs,
            finished_at=utcnow(),
            waiting_json="",
        )
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_COMPLETED,
            {
                "workflow": workflow.name,
                "status": final_status,
                "run_id": run_id,
                "sessions": session_ids,
            },
        )
        return final if final is not None else record

    async def resume_after_answer(
        self, record: WorkflowRunRecord, answer: str
    ) -> WorkflowRunRecord:
        """Resume a ``waiting`` run: fold the user's answer into the parked
        ``ask`` step's output and drive the remaining steps. Everything is
        rebuilt from the database — a parked run survives daemon restarts.

        Contract 8 reaches the human gate too (v1.170.0): an ``expect``
        declared on the ask step runs against the FOLDED answer output —
        ``summary_contains`` gates the answer text ("User answered: …");
        ``files`` fails honestly via the not-an-agent/tool branch (an ask
        produces no files). ``on_failure`` routes as usual: ``skip`` continues
        with the failure visible, ``retry`` re-parks the question ONCE (a
        re-attempt of an ask is asking again — tracked durably in
        ``waiting_json.expect_retries``), ``halt`` fails the run.
        """
        try:
            waiting = json.loads(record.waiting_json or "{}")
        except (TypeError, ValueError):
            waiting = {}
        idx = int(waiting.get("index", -1))
        step_name = str(waiting.get("step") or "")
        steps_raw = json.loads(record.steps_json or "[]")
        wf = load_workflow(
            {
                "name": record.workflow_name,
                "steps": steps_raw,
                "project_id": record.project_id,
            }
        )
        if idx < 0 or idx >= len(wf.steps):
            raise ValueError("this run's parked step could not be reconstructed")
        step = wf.steps[idx]
        name = step_name or step.name
        outputs = record and json.loads(record.outputs_json or "{}") or {}
        folded: dict = {
            "status": "completed",
            "summary": f"User answered: {answer}",
            "kind": "ask",
        }
        session_ids = json.loads(record.session_ids_json or "[]")
        latest = self._get_record(record.id)
        if latest is not None and latest.status in ("cancelling", "cancelled"):
            # The user cancelled between answering and the resume starting —
            # the answer must not resurrect the run.
            if latest.status == "cancelling":
                self._update_record(
                    record.id, status="cancelled", finished_at=utcnow(), waiting_json=""
                )
            return latest
        if step.expect:
            problem = self._expect_failure(step, folded)
            if problem is not None:
                retries = int(waiting.get("expect_retries") or 0)
                if step.on_failure == "retry" and retries < 1:
                    # ONE re-attempt of an ask is asking AGAIN: re-park with
                    # the same question, the attempt tracked durably so a
                    # second unsatisfying answer fails instead of looping.
                    question = str(waiting.get("question") or "") or (
                        f"Continue past “{name}”?"
                    )
                    self._update_record(
                        record.id,
                        status="waiting",
                        waiting_json=dumps(
                            {
                                "index": idx,
                                "step": name,
                                "question": question,
                                "expect_retries": retries + 1,
                            }
                        ),
                    )
                    await self.platform.event_bus.publish(
                        "workflow.waiting",
                        {
                            "run_id": record.id,
                            "workflow": record.workflow_name,
                            "step": name,
                            "question": question,
                        },
                    )
                    self._deliver(
                        f"Workflow “{record.workflow_name}” needs you again — "
                        f"the answer didn't satisfy the gate ({problem}). "
                        f"{question}"
                    )
                    refreshed = self._get_record(record.id)
                    return refreshed if refreshed is not None else record
                folded = {
                    "status": "failed",
                    "summary": f"expectation failed: {problem}",
                    "kind": "ask",
                    "expect_failed": problem,
                }
                if step.on_failure == "skip":
                    # Visible failure, run continues — the usual skip contract.
                    folded["handled"] = "skipped"
        outputs[name] = folded
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_COMPLETED,
            {
                "run_id": record.id,
                "workflow": record.workflow_name,
                "step": name,
                "index": idx,
                "total": len(wf.steps),
                "agent": "",
                "kind": "ask",
                "session_id": "",
                "status": str(folded.get("status") or ""),
                "summary": str(folded.get("summary") or "")[:_MAX_STEP_SUMMARY],
            },
        )
        if folded.get("status") == "failed" and not folded.get("handled"):
            # halt (or an exhausted retry): the run fails, the tail never ran.
            for s in wf.steps[idx + 1 :]:
                outputs.setdefault(s.name, {"status": "skipped"})
            final = self._update_record(
                record.id,
                status="failed",
                current_session_id=None,
                outputs=outputs,
                finished_at=utcnow(),
                waiting_json="",
            )
            await self.platform.event_bus.publish(
                EventType.WORKFLOW_COMPLETED,
                {
                    "workflow": record.workflow_name,
                    "status": "failed",
                    "run_id": record.id,
                    "sessions": session_ids,
                },
            )
            return final if final is not None else record
        self._update_record(
            record.id, status="running", outputs=outputs, waiting_json=""
        )
        return await self.run_record(
            record, wf, start_index=idx + 1, outputs=outputs, session_ids=session_ids
        )

    def rebuild_run(
        self, record: WorkflowRunRecord
    ) -> tuple[WorkflowDef, dict, list[str], int]:
        """Rebuild a run's def + progress from the database record ALONE
        (v1.170.0, contract 4): ``(workflow, outputs, session_ids, start)``,
        where ``start`` is the first step index whose output is not
        ``completed`` — the resume point. ``len(steps)`` when everything
        already completed (resuming then just finalizes honestly). Raises
        ``ValueError`` when the record carries no reconstructable steps.
        """
        try:
            steps_raw = json.loads(record.steps_json or "[]")
        except (TypeError, ValueError):
            steps_raw = []
        wf = load_workflow(
            {
                "name": record.workflow_name,
                "steps": steps_raw,
                "project_id": record.project_id,
            }
        )
        if not wf.steps:
            raise ValueError("this run's steps could not be reconstructed")
        try:
            outputs = json.loads(record.outputs_json or "{}")
        except (TypeError, ValueError):
            outputs = {}
        if not isinstance(outputs, dict):
            outputs = {}
        try:
            session_ids = json.loads(record.session_ids_json or "[]")
        except (TypeError, ValueError):
            session_ids = []
        if not isinstance(session_ids, list):
            session_ids = []
        start = next(
            (
                i
                for i, s in enumerate(wf.steps)
                if not (
                    isinstance(outputs.get(s.name), dict)
                    and outputs[s.name].get("status") == "completed"
                )
            ),
            len(wf.steps),
        )
        return wf, outputs, session_ids, start

    async def resume_interrupted(self, record: WorkflowRunRecord) -> WorkflowRunRecord:
        """Resume an ``interrupted`` run from its first non-completed step —
        the ONE engine call the resume route spawns (v1.170.0, contract 4).

        The CALLER owns the atomic ``interrupted -> resuming`` claim (the
        answer route's double-submit lesson); this method re-reads the record
        so a cancel landing between the claim and the resume is honored, flips
        the row back to ``running`` (clearing the reconcile-stamped
        ``finished_at`` — a running row must not look finished), and drives the
        remaining steps. Completed steps are NOT re-run; skipped/failed ones
        get their chance (a halt's tail was skipped only because the run
        stopped). Raises the same ``ValueError`` as :meth:`rebuild_run` for an
        unreconstructable record.
        """
        wf, outputs, session_ids, start = self.rebuild_run(record)
        latest = self._get_record(record.id)
        if latest is not None and latest.status in ("cancelling", "cancelled"):
            # A cancel must never be resurrected by a queued resume.
            if latest.status == "cancelling":
                self._update_record(
                    record.id,
                    status="cancelled",
                    finished_at=utcnow(),
                    waiting_json="",
                )
            return self._get_record(record.id) or latest
        self._update_record(
            record.id, status="running", finished_at=None, waiting_json=""
        )
        return await self.run_record(
            record, wf, start_index=start, outputs=outputs, session_ids=session_ids
        )

    async def _exec_step(
        self,
        orch,
        run_id: str,
        workflow: WorkflowDef,
        step: Step,
        idx: int,
        total: int,
        outputs: dict[str, Any],
        completed: list[tuple[str, str]],
        workspace_root: str | None,
        session_ids: list[str],
        tool_workspace,
    ) -> dict:
        """Execute ONE step per its kind; returns its output dict. Raises
        :class:`_AskPark` for ask steps and lets CancelledError propagate."""
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_STARTED,
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "step": step.name,
                "index": idx,
                "total": total,
                "agent": step.agent if step.kind == "agent" else "",
                "kind": step.kind,
                "session_id": "",
            },
        )

        if step.kind == "ask":
            question = render_template(
                step.message or step.task, outputs
            ).strip() or f"Continue past “{step.name}”?"
            raise _AskPark(idx, step.name, question)

        attempts = 2 if step.on_failure == "retry" else 1
        out: dict = {}
        for attempt in range(attempts):
            if step.kind == "tool":
                out = await self._run_tool_step(
                    run_id, step, outputs, workspace_root, tool_workspace
                )
            elif step.kind == "notify":
                out = self._run_notify_step(step, outputs)
            else:
                out = await self._run_agent_step(
                    orch, run_id, workflow, step, outputs, completed,
                    workspace_root, session_ids,
                )
            # Verified steps (v1.170.0, contract 8): expectations run INSIDE
            # the attempt loop so a failed expectation routes through
            # on_failure exactly like any other failure (retry re-attempts,
            # skip continues, halt stops the run).
            if out.get("status") == "completed" and step.expect:
                problem = self._expect_failure(step, out)
                if problem is not None:
                    out = {
                        **out,
                        "status": "failed",
                        "summary": f"expectation failed: {problem}",
                        "expect_failed": problem,
                    }
            if out.get("status") in ("completed", "cancelled"):
                break  # never "retry" a CANCELLED attempt — the user said stop
        if out.get("status") == "failed" and step.on_failure == "skip":
            # The failure stays VISIBLE on the step, but the run continues.
            out["handled"] = "skipped"

        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_COMPLETED,
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "step": step.name,
                "index": idx,
                "total": total,
                "agent": step.agent if step.kind == "agent" else "",
                "kind": step.kind,
                "session_id": str(out.get("session_id") or ""),
                "status": str(out.get("status") or ""),
                "summary": str(out.get("summary") or "")[:_MAX_STEP_SUMMARY],
            },
        )
        return out

    async def _run_agent_step(
        self, orch, run_id, workflow, step, outputs, completed,
        workspace_root, session_ids,
    ) -> dict:
        task_text = render_template(step.task, outputs) + self._context_block(completed)
        # The def's explicit pin (None for unpinned workflows) grounds each
        # step in the project — its instructions/knowledge inject at run
        # time — and, when the project has a valid folder, runs the step
        # directly IN that folder so deliverables land where the user expects.
        session = await orch.create_session(
            task_text,
            _agent_type(step.agent),
            provider=None,
            project_id=workflow.project_id,
            workspace_root=workspace_root,
            # TX-01 provenance (v1.170.0, contract 6): the audit timeline can
            # answer "which workflow started this session?" — laundered to the
            # origin rule's charset and clipped to its 64-char cap.
            origin=_workflow_origin(workflow.name),
        )
        session_ids.append(session.id)
        # Record the live session id BEFORE running, so a cancel arriving
        # mid-step can find and stop it.
        self._update_record(
            run_id,
            current_session_id=session.id,
            session_ids=session_ids,
            outputs=outputs,
        )
        task = asyncio.ensure_future(orch.run_session(session.id))
        orch.register_running(session.id, task)
        try:
            session = await task
        except asyncio.CancelledError:
            return {
                "session_id": session.id,
                "status": "cancelled",
                "summary": "",
                "tool": step.tool,
                "kind": "agent",
            }
        status = (
            "completed" if session.status is SessionStatus.COMPLETED else "failed"
        )
        return {
            "session_id": session.id,
            "status": status,
            "summary": session.summary,
            "tool": step.tool,
            "kind": "agent",
        }

    async def _run_tool_step(
        self,
        run_id: str,
        step: Step,
        outputs: dict,
        workspace_root: str | None,
        tool_workspace,
    ) -> dict:
        """ONE deterministic tool call — no LLM, no session. Same permission
        engine as every other tool invocation (a workflow must not be a
        permission bypass)."""
        from ..tools.base import ToolContext

        if not step.tool:
            return {
                "status": "failed",
                "summary": "tool step has no 'tool' name",
                "kind": "tool",
            }
        from ..tools.base import Reversibility

        tool_impl = self.platform.registry.get(step.tool)
        if tool_impl is None:
            return {
                "status": "failed",
                "summary": f"unknown tool '{step.tool}'",
                "tool": step.tool,
                "kind": "tool",
            }
        rendered = _render_args(step.args, outputs)
        # GRANT POLICY (v1.121.0 security review): a STORED workflow def is
        # NOT interactive consent — defs can be authored by agents
        # (workflow_create) and fired headless by schedules, so blanket
        # session_allow here would let planted content lift ask-mode host
        # tools (shell, run_code, mcp_call…) with no human in the loop.
        # Policy: allow-tier tools run on their own permission; ask-mode tools
        # self-grant ONLY when READONLY (list_folder, view_image, …) — keyed
        # by perm_key, the lesson chat already learned (grouped tools share
        # one key; a name-keyed grant silently denies them); everything else
        # refuses with the honest next step.
        base = self.platform.permissions.authorize(tool_impl.perm_key(), rendered, None)
        grant = None
        if not base.allowed:
            if getattr(tool_impl, "reversibility", None) is Reversibility.READONLY:
                grant = [tool_impl.perm_key()]
            else:
                return {
                    "status": "failed",
                    "summary": (
                        f"tool '{step.tool}' needs interactive approval and can't "
                        f"run headless in a workflow — use an agent step (which "
                        f"asks) or set the tool to auto-approve in Settings"
                    ),
                    "tool": step.tool,
                    "kind": "tool",
                }
        ctx = ToolContext(
            workspace=Path(workspace_root)
            if workspace_root
            else Path(tool_workspace()),
            session_id=run_id,
            agent_run_id=run_id,
            config=self.platform.config,
            event_bus=self.platform.event_bus,
            engine=self.platform.engine,
        )
        try:
            result = await self.platform.registry.invoke(
                step.tool,
                rendered,
                ctx,
                self.platform.permissions,
                None,
                session_allow=grant,
            )
        except Exception as exc:  # noqa: BLE001 — a crashed tool is a failed step
            return {
                "status": "failed",
                "summary": f"{type(exc).__name__}: {exc}",
                "tool": step.tool,
                "kind": "tool",
            }
        out = {
            "status": "completed" if result.ok else "failed",
            "summary": (result.output if result.ok else (result.error or "tool error"))[
                :_MAX_STEP_SUMMARY
            ],
            "tool": step.tool,
            "kind": "tool",
        }
        # Bounded structured data (v1.170.0, contract 7): persisted so
        # ``{{Step Name.data}}`` can hand structure to a later step. SUCCESSFUL
        # calls only — a failed tool's payload feeding downstream templating as
        # if trustworthy matches the failed-tools-excluded convention
        # (v1.165.0). Stored as the serialized JSON string, clipped; a clip
        # can truncate mid-JSON, which consumers must treat as opaque text.
        if result.ok and result.data:
            try:
                serialized = json.dumps(result.data, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                serialized = ""
            if serialized:
                out["data"] = serialized[:_MAX_STEP_DATA]
        if result.ok and result.created_paths:
            # Absolute paths (the v1.153.2 say-WHERE rule); feeds the contract-8
            # file expectations and the run record's honesty about outputs.
            out["created_paths"] = [str(p) for p in result.created_paths][:50]
        return out

    def _run_notify_step(self, step: Step, outputs: dict) -> dict:
        message = render_template(step.message or step.task, outputs).strip()
        if not message:
            return {
                "status": "failed",
                "summary": "notify step has no message",
                "kind": "notify",
            }
        ok, detail = self._deliver(message)
        # A notify step's ENTIRE job is delivery — a swallowed failure would
        # show a green run whose message never arrived (and on_failure could
        # never route for this kind).
        if not ok:
            return {
                "status": "failed",
                "summary": f"delivery failed: {detail}"[:_MAX_STEP_SUMMARY],
                "kind": "notify",
            }
        return {"status": "completed", "summary": f"notified: {message[:200]}", "kind": "notify"}

    # ------------------------------------------------------------------ #
    # verified steps (v1.170.0, contract 8)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _norm_path(p: str) -> str:
        """Normalize a path for expectation matching: forward slashes, no
        leading/trailing separators, casefolded (Windows paths compare
        case-insensitively; on a case-sensitive filesystem this stays
        deterministic, merely lenient)."""
        return str(p).replace("\\", "/").strip().strip("/").casefold()

    def _expect_file_candidates(self, step: Step, out: dict) -> tuple[list[str], str]:
        """The files a completed step verifiably produced, plus a human name
        for WHERE that truth came from (the failure detail must say why).

        Agent steps read the ledger-derived created/changed lists
        (``agents/outcome.session_result`` — ToolInvocation + UndoJournal,
        workspace-relative). Tool steps read the result's ``created_paths``
        (absolute) plus path-shaped values in its bounded ``data``. Both are
        deterministic re-reads of recorded truth — never the step's prose.
        """
        if step.kind == "agent":
            sid = str(out.get("session_id") or "")
            if not sid:
                return [], "the step session's ledger (no session was recorded)"
            # Lazy import — outcome pulls agent models; engine must stay
            # importable without the agents package fully loaded (and tests
            # monkeypatch the module attribute).
            from ..agents import outcome as _outcome

            res = _outcome.session_result(self.platform.engine, sid)
            cands = list(res.get("files_created") or []) + list(
                res.get("files_changed") or []
            )
            return (
                [str(c) for c in cands],
                "the session's ledger-recorded created/changed files",
            )
        cands = [str(p) for p in (out.get("created_paths") or [])]
        raw = out.get("data")
        if isinstance(raw, str) and raw:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = None  # clipped mid-JSON — opaque text, no candidates
            if isinstance(data, dict):
                for key in (
                    "path",
                    "abs_path",
                    "file",
                    "files",
                    "paths",
                    "created_paths",
                    "documents",
                ):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        cands.append(val)
                    elif isinstance(val, list):
                        cands.extend(
                            str(v) for v in val if isinstance(v, str) and v.strip()
                        )
        return cands, "the tool result's created paths/data"

    def _expect_failure(self, step: Step, out: dict) -> str | None:
        """Check a COMPLETED step's declared expectations DETERMINISTICALLY.

        Returns None when every expectation holds, else a detail naming
        exactly which expectation failed and why. ``files`` match
        workspace-relative (exact or path-suffix, normalized);
        ``summary_contains`` is a case-insensitive substring check on the
        step's recorded summary. Defensive throughout — a checker crash
        masquerading as a step failure would be a lie about the step.
        """
        expect = step.expect if isinstance(step.expect, dict) else {}
        wanted = [str(f) for f in (expect.get("files") or []) if str(f).strip()]
        if wanted:
            if step.kind not in ("agent", "tool"):
                return (
                    f"'files' expectations need an agent or tool step — a "
                    f"'{step.kind}' step produces no files"
                )
            try:
                candidates, source = self._expect_file_candidates(step, out)
            except Exception as exc:  # noqa: BLE001 — a crashed checker must
                # not masquerade as a step failure (e.g. a DB error inside
                # outcome.session_result): name the check honestly instead.
                return (
                    f"could not verify 'files' expectations: "
                    f"{type(exc).__name__}: {exc} (the step itself completed)"
                )
            norm = [self._norm_path(c) for c in candidates]
            for f in wanted:
                want = self._norm_path(f)
                if not any(c == want or c.endswith("/" + want) for c in norm):
                    seen = ", ".join(sorted(candidates)[:8]) or "none"
                    return f"expected file '{f}' was not found in {source} (saw: {seen})"
        for needle in expect.get("summary_contains") or []:
            text = str(needle)
            if text and text.casefold() not in str(out.get("summary") or "").casefold():
                return f"step summary does not contain '{text}'"
        return None

    def _deliver(self, message: str) -> tuple[bool, str]:
        """Send to EVERY destination (schedule-delivery semantics — a workflow
        speaking to nobody is indistinguishable from a broken one). Returns
        (any_delivered, detail); never raises. Messages are capped below
        Telegram's 4096-char limit so one verbose step can't kill delivery."""
        try:
            notifier = self.platform.notifier
            msg = message if len(message) <= 3500 else message[:3500] + "…"
            results = notifier.notify(msg, list(notifier.channels()))
            oks = [k for k, v in (results or {}).items() if v.get("ok")]
            if oks:
                return True, f"delivered to {', '.join(sorted(oks))}"
            details = "; ".join(
                f"{k}: {v.get('detail', 'failed')}" for k, v in (results or {}).items()
            )
            return False, details or "no destinations configured"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _context_block(completed: list[tuple[str, str]]) -> str:
        """Build the '# Context from earlier steps' block from prior COMPLETED
        steps' summaries — each clipped, the whole thing capped, most-recent
        steps kept when the budget is tight (then re-ordered chronologically)."""
        if not completed:
            return ""
        parts: list[str] = []
        total = 0
        for name, summary in reversed(completed):  # newest first — they win the budget
            block = f"\n## {name}\n{(summary or '')[:_MAX_STEP_SUMMARY]}"
            if total + len(block) > _MAX_CONTEXT:
                break
            parts.append(block)
            total += len(block)
        if not parts:
            return ""
        parts.reverse()  # present oldest -> newest
        return "\n\n# Context from earlier steps" + "".join(parts)

    def _project_workspace_root(self, project_id: str | None) -> str | None:
        """Return the pinned project's folder for step sessions, or None.

        Mirrors the project-task route's validation: the root must be set AND be
        an existing directory. A moved/deleted folder returns None so the step
        degrades to a normal per-session workspace instead of failing the run —
        the pin's project context still applies; only the folder is skipped.
        """
        if not project_id:
            return None
        with session_scope(self.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None or not (project.root or "").strip():
            return None
        root = Path(project.root)
        return str(root) if root.is_dir() else None

    def _get_record(self, run_id: str) -> WorkflowRunRecord | None:
        with session_scope(self.platform.engine) as db:
            return db.get(WorkflowRunRecord, run_id)

    def _update_record(self, run_id: str, **fields: Any) -> WorkflowRunRecord | None:
        """Apply the given fields to the record and persist. ``session_ids`` and
        ``outputs`` are JSON-encoded; other keys map straight onto the column."""
        with session_scope(self.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                return None
            if "status" in fields:
                rec.status = fields["status"]
            if "current_session_id" in fields:
                rec.current_session_id = fields["current_session_id"]
            if "session_ids" in fields:
                rec.session_ids_json = dumps(fields["session_ids"])
            if "outputs" in fields:
                rec.outputs_json = dumps(fields["outputs"])
            if "finished_at" in fields:
                rec.finished_at = fields["finished_at"]
            if "waiting_json" in fields:
                rec.waiting_json = fields["waiting_json"]
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec
