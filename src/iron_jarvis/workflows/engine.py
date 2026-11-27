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
import itertools
import json
import re
import tempfile
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType, Project, SessionStatus
from .models import WorkflowRunRecord
from .step_outcome import ended_by_session, step_failures

log = get_logger("workflows.engine")

#: Context-chaining bounds: each earlier-step summary is clipped, and the whole
#: injected block is capped so a long-running workflow can't blow up the prompt.
_MAX_STEP_SUMMARY = 1500
_MAX_CONTEXT = 4000

#: Write ordering (v1.323.1, see ``WorkflowEngine._persist``). Process-wide,
#: not per engine: the daemon, a tool and a resume may each hold their own
#: engine for the same run. ``_LAST_SEQ`` is bounded; a run old enough to fall
#: off it has no write in flight.
_WRITE_SEQ = itertools.count()
_WRITE_LOCK = threading.Lock()
_LAST_SEQ: dict[str, int] = {}
_LAST_SEQ_CAP = 4096

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


#: ``WorkflowRunRecord.started_by`` for a run a PERSON started (v1.352.0).
STARTED_BY_PERSON = "person"

#: v1.505.0 (north-star 5.1): a tool step Cancel stopped while it ran.
TOOL_STEP_STOPPED = (
    "Stopped: the run was cancelled while this step was running. "
    "What the step was doing may already have happened."
)

#: v1.505.0 (north-star 5.1): the tool-step calls IN FLIGHT, per run id, as
#: ``(loop, task)`` pairs. Process-wide, not per engine: a person's run drives
#: on the daemon's loop, a schedule's fire under ``asyncio.run`` on the
#: scheduler's thread, and the cancel route builds no engine of its own. A
#: tool step has no session, so the route's ``cancel_session`` never reached
#: one and Cancel waited on the call (``cancel_tool_steps``).
_TOOL_CALLS: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Future]]] = {}
_TOOL_CALLS_LOCK = threading.Lock()


def cancel_tool_steps(run_id: str) -> int:
    """Cancel every tool-step call of *run_id* in flight, each on its OWN loop
    (``call_soon_threadsafe``: the cancel route is a sync handler on a worker
    thread, and a scheduled run's loop is another thread's). The registry's
    own cancel path ledgers the call; the step reads ``cancelled`` and the run
    ends Stopped. Returns how many calls were asked to stop. Never raises."""
    with _TOOL_CALLS_LOCK:
        live = list(_TOOL_CALLS.get(str(run_id or ""), ()))
    asked = 0
    for loop, task in live:
        if task.done():
            continue
        try:
            loop.call_soon_threadsafe(task.cancel)
            asked += 1
        except RuntimeError:  # that loop is closed: nothing left to stop
            pass
    return asked


#: v1.505.0 (north-star 5.3): a failed driver's end, written in its own task
#: (held here so it is never collected before it lands).
_SETTLE_JOBS: set[asyncio.Future] = set()

#: v1.505.0 (north-star 5.3): the database was busy past SQLite's wait.
DRIVER_DB_BUSY = (
    "The run stopped because Iron Jarvis could not save its progress: the "
    "database was busy, perhaps during Compact or a backup. Run it again."
)


def driver_failure_words(exc: BaseException) -> str:
    """The ONE sentence a run whose driver raised ends with (its notes, the
    ``workflow.completed`` notice): the busy database by name, else the run's
    own plain rule (``failure_words.plain_reason``). Never the exception's
    class or its raw text. Never raises."""
    text = str(exc or "").lower()
    if "database is locked" in text or "database is busy" in text:
        return DRIVER_DB_BUSY
    try:
        from ..providers.failure_words import plain_reason

        reason = plain_reason(exc)
    except Exception:  # noqa: BLE001 — wording only
        reason = ""
    return f"The run failed: {reason}" if reason else "The run failed."


def _track_tool_call(run_id: str, loop, task) -> None:
    with _TOOL_CALLS_LOCK:
        _TOOL_CALLS.setdefault(run_id, set()).add((loop, task))


def _untrack_tool_call(run_id: str, loop, task) -> None:
    with _TOOL_CALLS_LOCK:
        calls = _TOOL_CALLS.get(run_id)
        if calls is not None:
            calls.discard((loop, task))
            if not calls:
                _TOOL_CALLS.pop(run_id, None)


#: v1.446.0 (ease-of-use review item 22): a run that FINISHED, but a step set
#: to "skip if it fails" failed on the way. It used to finish "completed", so
#: Run history said Done and the bell stayed quiet; the dashboard's
#: lib/statusWords reads it "Part failed" (amber), the bell rings it, and every
#: reader of a run's status treats it as a finished (terminal) run.
PART_FAILED_STATUS = "completed_with_failures"
#: Every status a run ENDS in (a reader that asks "is this run over?").
TERMINAL_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    PART_FAILED_STATUS,
    "failed",
    "cancelled",
)


def _workflow_origin(name: str) -> str:
    """The Session origin stamp for a workflow-spawned step session: charset-
    laundered (out-of-charset characters become ``_``) and length-clipped."""
    return _ORIGIN_BAD_RX.sub("_", f"workflow:{name}")[:_ORIGIN_MAX]


def workflow_grant_scope_id(name: str) -> str:
    """The ``scope_id`` of a workflow's standing grants (v1.505.0, D10): the
    name as its step runs' origin stamp carries it (laundered, clipped), since
    the runtime reads the scope off that stamp. The workflow's row lists its
    grants by this id."""
    return _workflow_origin(name)[len("workflow:"):]


#: Third plan PG2: the ledger label of a tool step refused by its tool's own
#: check (``ToolInvocation`` row + the ``tool.denied`` event's ``kind``).
OWN_CHECK_LABEL = "not run"


def own_check_refusal_words(step: "Step", check: Any) -> str:
    """The sentence a tool step ends with when its tool's own check asks
    about the call (third plan PG2): what it wanted to do (the check's own
    words), that Iron Jarvis asks every time, and the way that works. A check
    that only an attended run may ask (``attended_only``) is refused in an
    Agent step too, so it names chat alone; any other names an Agent step,
    which asks. Never raises."""
    words = str(getattr(check, "words", "") or "").strip().rstrip(".").strip()
    what = words or f"What the step “{step.name}” does"
    if getattr(check, "attended_only", False):
        way = "Do it from chat, where you are asked."
    else:
        way = "Make it an Agent step, which asks you first, or do it from chat."
    return (
        f"Not done: {what}. Iron Jarvis asks you before this every time, and a "
        f"workflow's Tool call step cannot ask, so the step “{step.name}” did not "
        f"do it. {way}"
    )


def tier_refusal_words(step: "Step", decision: Any) -> str:
    """The sentence a tool step ends with when the tool's permission refuses
    it (third plan PG2, step 3; it said "needs interactive approval and can't
    run headless ... set the tool to auto-approve in Settings" before, which
    Settings refuses for a tool that always asks). Never raises."""
    from ..core.models import PermissionMode

    if getattr(decision, "mode", None) is PermissionMode.DENY:
        return (
            f"Not done: “{step.tool}” is set to Deny, so the step “{step.name}” "
            "cannot use it."
        )
    return (
        f"Not done: “{step.tool}” asks you before it runs, and a workflow's Tool "
        f"call step cannot ask, so the step “{step.name}” did not run it. Make it "
        "an Agent step, which asks you first."
    )

#: What a step IS (v1.121.0). Idea 09 (09-a) adds the two BLOCKS: "if" (two
#: paths, one taken: ``workflows/logic``) and "for_each" (the same steps for
#: every item of a list). An unknown kind is REFUSED by name when a workflow
#: is loaded (:class:`UnknownStepKind`), never turned into an agent step: a
#: block from a newer version would have run as an agent on an empty task.
STEP_KINDS: tuple[str, ...] = ("agent", "tool", "ask", "notify", "if", "for_each")
#: The kinds that hold steps of their own (``validate.BLOCK_KINDS``).
BLOCK_KINDS: tuple[str, ...] = ("if", "for_each")
#: What a failed step does to the run. Unknown values coerce to "halt".
ON_FAILURE: tuple[str, ...] = ("halt", "retry", "skip")

#: Idea 09 bounds (``validate`` refuses a workflow past them at every door
#: that saves, and :meth:`WorkflowEngine.create_record` before a run).
#: D-09.1: how many items one For each works through before it stops.
DEFAULT_MAX_ITEMS = 200
#: The most step runs one workflow run may take, blocks included.
MAX_STEP_RUNS = 2000
#: Light on the PC (north star 7): at most this many items at once.
MAX_PARALLEL = 4
#: How many entries one ``list_folder`` listing shows (its own cap): a For
#: each over a folder works through at most this many files
#: (``validate.FOLDER_ITEMS_CAP``), and a folder that holds more matching
#: entries than one listing shows stops the block with words, never a part
#: of the folder worked through as if it were all of it.
FOLDER_LIST_CAP = 1000
#: What a For each keeps of each item's result (its row, and each step of a
#: finished item): the run record grows with every item, and this bounds it.
_MAX_ITEM_SUMMARY = 300

#: A run that reached :data:`MAX_STEP_RUNS` (said on the step that stopped).
STEP_RUNS_WORDS = (
    f"Stopped: this run reached {MAX_STEP_RUNS:,} steps, the most one run may take. "
    "Split the work into smaller lists, or lower how many items each For each works through."
)


class UnknownStepKind(ValueError):
    """A stored step whose ``kind`` this version does not know. Raised by
    :func:`load_workflow` with a sentence that names the step and the kind."""


def unknown_kind_words(name: str, kind: str) -> str:
    """What a workflow with a step of an unknown kind says, everywhere it is
    loaded (a run, a schedule's fire, a rule's start)."""
    who = f"The step “{name}”" if name.strip() else "A step"
    return (
        f"{who} is a “{kind}” step, which this version of Iron Jarvis does not know, so "
        "the workflow was not run. Open it on the Workflows page and choose what the step is."
    )


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
    #: Idea 09 (09-a), BLOCKS only. ``if``: what it checks,
    #: ``{left, op, right}`` or one level of ``{all|any: [...]}``.
    condition: dict = field(default_factory=dict)
    #: ``if``: the Then path; ``for_each``: the steps run for every item.
    steps: list["Step"] = field(default_factory=list)
    #: ``if``: the Otherwise path.
    else_steps: list["Step"] = field(default_factory=list)
    #: ``for_each``: what it works through, ``{from: "{{Step.data}}"}`` (a
    #: JSON list) or ``{files: {folder, glob}}`` (listed by ``list_folder``).
    over: dict = field(default_factory=dict)
    #: ``for_each``: how many items it works through (D-09.1).
    max_items: int = DEFAULT_MAX_ITEMS
    #: ``for_each``: how many items at once, 1 to :data:`MAX_PARALLEL`.
    parallel: int = 1


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


#: How deep the loader follows blocks inside blocks. ``validate`` refuses
#: past :data:`validate.MAX_NESTING` (3); this only keeps a hand-made row from
#: recursing without end.
_LOAD_DEPTH = 8


def _whole_or(value: Any, default: int) -> int:
    """*value* as a whole number, else *default* (the loader is lenient;
    ``validate`` refuses a value out of range at save and before a run)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return default


def _load_steps(raw_steps: Any, depth: int = 0) -> list[Step]:
    if depth > _LOAD_DEPTH:
        raise ValueError("blocks go at most 3 deep (an If or a For each inside another)")
    steps: list[Step] = []
    for raw in raw_steps or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "agent").strip().lower() or "agent"
        if kind not in STEP_KINDS:
            # Idea 09 (09-a): REFUSED by name, never coerced to an agent step.
            raise UnknownStepKind(unknown_kind_words(str(raw.get("name") or ""), str(raw.get("kind"))))
        on_failure = str(raw.get("on_failure") or "halt").strip().lower()
        args = raw.get("args")
        step = Step(
            name=str(raw.get("name", "")),
            agent=str(raw.get("agent", "builder")),
            task=str(raw.get("task", "")),
            tool=raw.get("tool"),
            kind=kind,
            on_failure=on_failure if on_failure in ON_FAILURE else "halt",
            group=(str(raw.get("group") or "").strip() or None),
            args=args if isinstance(args, dict) else {},
            message=str(raw.get("message", "")),
            expect=_coerce_expect(raw.get("expect")),
        )
        if kind in BLOCK_KINDS:
            cond = raw.get("condition")
            over = raw.get("over")
            step.condition = cond if isinstance(cond, dict) else {}
            step.over = over if isinstance(over, dict) else {}
            step.steps = _load_steps(raw.get("steps"), depth + 1)
            step.else_steps = _load_steps(raw.get("else_steps"), depth + 1)
            step.max_items = _whole_or(raw.get("max_items"), DEFAULT_MAX_ITEMS)
            step.parallel = _whole_or(raw.get("parallel"), 1)
        steps.append(step)
    return steps


def load_workflow(data: dict) -> WorkflowDef:
    """Build a :class:`WorkflowDef` from a parsed mapping (e.g. TOML/JSON).

    Raises :class:`UnknownStepKind` (a ``ValueError``) for a step whose kind
    this version does not know, with the sentence that says so (idea 09)."""
    return WorkflowDef(
        name=str(data.get("name", "")),
        steps=_load_steps(data.get("steps", []) or []),
        description=str(data.get("description", "")),
        # Optional explicit project pin — absent/empty both mean unpinned.
        project_id=(str(data.get("project_id") or "").strip() or None),
    )


def step_to_dict(s: Step) -> dict:
    """Serialize a step COMPLETELY — the run record persists this so a parked
    run can be resumed from the database alone after a restart. A block
    (idea 09) carries its condition or list, its limits and the steps inside
    it; every other step is the same ten fields as before."""
    out = {
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
    if s.kind in BLOCK_KINDS:
        out["condition"] = s.condition
        out["over"] = s.over
        out["steps"] = [step_to_dict(x) for x in s.steps]
        out["else_steps"] = [step_to_dict(x) for x in s.else_steps]
        out["max_items"] = s.max_items
        out["parallel"] = s.parallel
    return out


def step_at(workflow: "WorkflowDef", at: Any) -> Step:
    """The step a parked ask's locator names (idea 09): ``[2]`` is the third
    step, ``[2, "steps", 0, "else_steps", 1]`` the second step of the
    Otherwise path of the If that is the first step inside it. Raises
    ``ValueError`` when the locator names no step."""
    bad = ValueError("this run's parked step could not be reconstructed")
    if not isinstance(at, list) or not at or len(at) % 2 != 1:
        raise bad
    steps = workflow.steps
    step: Step | None = None
    for pos, part in enumerate(at):
        if pos % 2 == 0:
            if isinstance(part, bool) or not isinstance(part, int) or not 0 <= part < len(steps):
                raise bad
            step = steps[part]
        else:
            if step is None or part not in ("steps", "else_steps"):
                raise bad
            steps = step.steps if part == "steps" else step.else_steps
    if step is None:
        raise bad
    return step


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


def _refusal_words(exc: BaseException) -> str:
    """The run's plain sentence for a provider's SAFETY REFUSAL
    (``failure_words.plain_reason``), or "" when *exc* is not one. Never
    raises (v1.378.0 review)."""
    try:
        from ..providers.failure_words import refusal_reason

        return refusal_reason(exc)
    except Exception:  # noqa: BLE001 — wording only
        return ""


#: The ``kind`` of the synthetic output a rule's signal seeds (``{{Trigger}}``,
#: ``reflex/router._run_workflow``): the signal's own words — an email body,
#: a webhook body — written by someone outside.
TRIGGER_KIND = "trigger"

_FENCE_MARKER = re.compile(r"\[(\s*(?:END\s+)?UNTRUSTED\s+CONTENT)", re.IGNORECASE)


def _fenced_outside(value: str) -> str:
    """Outside words as a fenced untrusted block that they cannot close."""
    from ..computeruse.safety import wrap_untrusted

    return "\n" + wrap_untrusted(_FENCE_MARKER.sub(r"(\1", value or "")) + "\n" if value else ""


def _from_outside(out: dict) -> bool:
    """An output whose words someone outside wrote: a signal's own words
    (``{{Trigger}}``), or a For each's item that came from a folder's file
    names or from such words (idea 09)."""
    return out.get("kind") == TRIGGER_KIND or bool(out.get("outside"))


class UnreadableData(ValueError):
    """A step's stored JSON could not be read (it was clipped to the record's
    bound, :data:`_MAX_STEP_DATA`), so no field can be read from it."""


#: A field that is not in the data (``read_field``).
MISSING = object()


def field_ref(ref: str, outputs: "dict[str, Any]") -> "tuple[str, dict, list[str]] | None":
    """``(base name, base output, path)`` for ``{{Step.data.a.b}}`` and, inside
    a For each, ``{{item.a}}``; None for any other reference (idea 09)."""
    if ".data." in ref:
        base, rest = ref.split(".data.", 1)
        out = outputs.get(base.strip())
        if isinstance(out, dict):
            return base.strip(), out, [p.strip() for p in rest.split(".")]
        return None
    head, sep, rest = ref.partition(".")
    if sep and head.strip() == "item":
        out = outputs.get("item")
        if isinstance(out, dict):
            return "item", out, [p.strip() for p in rest.split(".")]
    return None


def read_field(out: dict, path: "list[str]") -> Any:
    """The value at *path* in *out*'s data, :data:`MISSING` when it is not
    there. Raises :class:`UnreadableData` when the stored JSON cannot be read
    (it was clipped)."""
    data = out.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            raise UnreadableData(out.get("data", "")[:40]) from None
    for seg in path:
        if isinstance(data, dict) and seg in data:
            data = data[seg]
        elif isinstance(data, list) and seg.isdigit() and int(seg) < len(data):
            data = data[int(seg)]
        else:
            return MISSING
    return data


def value_text(value: Any) -> str:
    """A field's value as the text a step is given."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def signal_trust(outputs: dict[str, Any]) -> dict[str, str]:
    """``{"trust": "low", "trust_reason": …}`` when the run was started by an
    outside signal the router marked low (its ``Trigger`` output carries it),
    else ``{}`` — so every other run's ``create_session`` call is unchanged."""
    trig = outputs.get("Trigger") if isinstance(outputs, dict) else None
    if isinstance(trig, dict) and trig.get("kind") == TRIGGER_KIND and trig.get("trust") == "low":
        return {"trust": "low", "trust_reason": str(trig.get("trust_reason") or "started by an outside signal")}
    return {}


def render_template(text: str, outputs: dict[str, Any], *, fence_outside: bool = False) -> str:
    """Replace ``{{Step Name}}`` with that step's recorded summary/output.

    ``fence_outside`` (v1.502.0, north-star 2.8): for an AGENT step's task —
    the ``{{Trigger}}`` output (a signal's own words, :data:`TRIGGER_KIND`)
    goes in as a fenced untrusted block, defanged, as the session action's
    ``{body}`` does (``reflex/router._render``). Off for a tool step's
    arguments, a notify message and a question: those are values handed to a
    tool or shown to the user, never a prompt, and a fence would corrupt them.

    ``{{Step Name.data}}`` (v1.170.0) resolves to that step's bounded
    structured ``data`` — the serialized JSON a tool step recorded — so a
    downstream step can hand off structure, not just prose. An output whose
    literal name IS ``Step Name.data`` always wins the reference (the
    reserved-name spirit of the ``{{Trigger}}`` guard: the def owns its names).

    Unknown references render as an empty string rather than leaking the
    braces into an agent prompt or tool argument. Values are clipped so one
    verbose step can't blow up a downstream prompt.

    A step that FAILED (and was skipped, so the run reached the reference)
    renders its bare ref as ``[step <name> failed: <summary>]`` (v1.231.0,
    AE10): its summary is an error message, and handing that to the next
    step as if it were the step's output let a downstream tool run on a
    quota error presented as content. ``{{name.data}}`` of a failed step
    stays the empty string — a failed tool records no ``data`` (v1.170.0).

    Idea 09 (09-a): ``{{Step.data.field}}`` is ONE field of the step's
    stored JSON, a dotted path (``{{Read.data.totals.grand}}``; a number
    picks from a list, counting from 0), and inside a For each ``{{item}}``,
    ``{{item.name}}`` and ``{{index}}`` are the item it is working on. A
    field that is not there renders "" here; the engine stops the step first
    (``checks.missing_values``), and a step whose stored JSON was clipped
    stops with "the data was too large to read a field from"
    (``checks.unreadable_values``). An item that came from outside (a file's
    name, a list from ``{{Trigger}}``) is fenced like ``{{Trigger}}``.
    """
    def _sub(m: re.Match) -> str:
        ref = m.group(1).strip()
        out = outputs.get(ref)
        if isinstance(out, dict):
            summary = str(out.get("summary") or "")
            if out.get("status") == "failed":
                return f"[step {ref} failed: {summary}]"[:_MAX_STEP_SUMMARY]
            if fence_outside and _from_outside(out):
                return _fenced_outside(summary[:_MAX_STEP_SUMMARY])
            return summary[:_MAX_STEP_SUMMARY]
        field = field_ref(ref, outputs)
        if field is not None:
            _base, base_out, path = field
            if base_out.get("status") == "failed":
                return ""
            try:
                value = read_field(base_out, path)
            except UnreadableData:
                return ""
            if value is MISSING:
                return ""
            text = value_text(value)[:_MAX_STEP_DATA]
            if fence_outside and _from_outside(base_out):
                return _fenced_outside(text)
            return text
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

    def __init__(self, index: int, step: str, question: str, at: "list | None" = None) -> None:
        super().__init__(question)
        self.index = index
        self.step = step
        self.question = question
        #: Idea 09: an ask INSIDE a block — ``index`` is then the block's
        #: place in the workflow, ``step`` the ask's path-qualified output
        #: key, and ``at`` the locator of its definition (:func:`step_at`).
        self.at = at


@dataclass
class _RunCtx:
    """What every step of one drive shares (idea 09: the block runner reads
    it, so a step inside a block runs exactly as a step outside one)."""

    orch: Any
    run_id: str
    workflow: "WorkflowDef"
    outputs: dict
    session_ids: list
    workspace_root: str | None
    tool_workspace: Any
    person_started: bool
    completed_chain: Any


@dataclass
class _Frame:
    """One list of steps being run: the workflow's own (``prefix`` "") or the
    steps inside a block, whose outputs are kept under ``prefix`` (``Each
    file/3/``, ``Check/then/``) so the "a finished step is never run again"
    rule makes a resume walk the same path. ``at`` locates the list in the
    definition (:func:`step_at`), ``extras`` are the names a For each gives
    its steps (``item``, ``index``)."""

    prefix: str
    steps: list
    at: list
    top_index: int | None = None
    top_name: str = ""
    extras: dict = field(default_factory=dict)
    parent: "_Frame | None" = None


def _scope_view(outputs: dict, frame: "_Frame") -> dict:
    """The values a step inside *frame* reads its ``{{names}}`` from: the run's
    own outputs, then, from the outermost block in, each block's item and the
    steps of its own list by their plain names (the nearest wins)."""
    chain: list[_Frame] = []
    f: _Frame | None = frame
    while f is not None and f.prefix:
        chain.append(f)
        f = f.parent
    view = dict(outputs)
    for f in reversed(chain):
        view.update(f.extras)
        for s in f.steps:
            out = outputs.get(f.prefix + s.name)
            if isinstance(out, dict):
                view[s.name] = out
    return view


def _local_chain(outputs: dict, frame: "_Frame") -> list[tuple[str, str]]:
    """The finished steps of *frame* and the blocks around it, by plain name
    (the agent step's "Context from earlier steps" inside a block)."""
    chain: list[_Frame] = []
    f: _Frame | None = frame
    while f is not None and f.prefix:
        chain.append(f)
        f = f.parent
    out: list[tuple[str, str]] = []
    for f in reversed(chain):
        for s in f.steps:
            o = outputs.get(f.prefix + s.name)
            if isinstance(o, dict) and o.get("status") == "completed":
                out.append((s.name, str(o.get("summary") or "")))
    return out


def _done(out: Any) -> bool:
    return isinstance(out, dict) and out.get("status") == "completed"


def _leaf_runs(outputs: dict) -> int:
    """How many step runs a run's record already holds (a resume counts them
    toward :data:`MAX_STEP_RUNS`)."""
    n = 0
    for o in (outputs or {}).values():
        if (
            isinstance(o, dict)
            and o.get("kind") in ("agent", "tool", "ask", "notify")
            and o.get("status") in ("completed", "failed", "cancelled")
        ):
            n += 1
    return n


def _item_text(item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("path"), str):
        return item["path"]
    if isinstance(item, str):
        return item
    return value_text(item)


def _item_label(item: Any, n: int) -> str:
    if isinstance(item, dict):
        for key in ("name", "title", "label", "id"):
            v = item.get(key)
            if isinstance(v, (str, int, float)) and str(v).strip():
                return str(v).strip()[:80]
        return f"Item {n}"
    text = " ".join(_item_text(item).split())
    return (text[:77] + "…") if len(text) > 80 else (text or f"Item {n}")


def _item_extras(item: Any, n: int, outside: bool) -> dict:
    """``{{item}}`` (the file's path, the text, or the element as JSON),
    ``{{item.field}}`` and ``{{index}}`` (counting from 1)."""
    entry: dict = {
        "status": "completed",
        "summary": _item_text(item)[:_MAX_STEP_DATA],
        "data": item,
        "kind": "item",
    }
    if outside:
        entry["outside"] = True
    return {"item": entry, "index": {"status": "completed", "summary": str(n), "kind": "item"}}


def settle_open_blocks(outputs: dict, final_status: str) -> bool:
    """A run that ENDED with a block still open (stopped while it worked, or
    while it waited on an ask inside it): the block reads "cancelled" (or
    "skipped"), never "running", and its stored list goes. Returns whether
    anything changed. Pure."""
    changed = False
    for out in list((outputs or {}).values()):
        if (
            isinstance(out, dict)
            and out.get("status") == "running"
            and out.get("kind") in BLOCK_KINDS
        ):
            out["status"] = "cancelled" if final_status == "cancelled" else "skipped"
            out.pop("list", None)
            changed = True
    return changed


def rollup_words(done: int, failed: int, not_run: int, total: int) -> str:
    """A For each's line: "14 of 14 done", "13 of 14 done, 1 failed", "6 of 14
    done, 1 failed, 7 not run"."""
    if total == 0:
        return "Nothing to work through: the list was empty."
    words = f"{done} of {total} done"
    if failed:
        words += f", {failed} failed"
    if not_run:
        words += f", {not_run} not run"
    return words


def items_bound_words(name: str, limit: int, found: int, done: int, *, folder: bool = False) -> str:
    """D-09.1: a For each given more items than it may work through stops at
    the next one and says so. Over a folder the limit goes up to
    :data:`FOLDER_LIST_CAP`, the most one listing shows."""
    way = (
        f"raise its limit (up to {FOLDER_LIST_CAP:,} for a folder) or split the folder."
        if folder
        else "raise its limit (up to 2,000) or split the list."
    )
    return (
        f"Stopped at item {limit + 1:,} of {found:,}: “{name}” works through at most "
        f"{limit:,} items in one run, and {done:,} of those are done. To go further, " + way
    )


def folder_unlisted_words(folder: str, total: int, shown: int, capped: bool) -> str:
    """A folder a For each could not list in full: it stops before the first
    item, so no part of the folder is worked through as if it were all."""
    if capped:
        return (
            f"could not list every file in {folder}: it holds more than {total:,} "
            "entries, or it was too slow to read in one go, so nothing was worked "
            "through. Split it into smaller folders, or narrow the names it looks for."
        )
    return (
        f"could not list every file in {folder}: {total:,} entries match and one "
        f"listing shows {shown:,}, so nothing was worked through. Split it into "
        "smaller folders, or narrow the names it looks for."
    )


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
        #: Idea 09: step runs taken so far, per run id (:data:`MAX_STEP_RUNS`).
        self._step_runs: dict[str, int] = {}

    @staticmethod
    def _has_runnable_steps(workflow: WorkflowDef) -> bool:
        """A workflow is runnable only if it has at least one non-empty step —
        an empty plan must NOT report 'completed' (it masked mis-configuration)."""
        steps = workflow.steps or []
        return any(
            (s.name or "").strip() or (s.task or "").strip() for s in steps
        )

    def create_record(
        self,
        workflow: WorkflowDef,
        inputs: dict[str, str] | None = None,
        *,
        started_by: str = "",
        chat_thread_id: str = "",
    ) -> WorkflowRunRecord:
        """Persist a fresh ``running`` record for *workflow* and return it.

        Raises ``ValueError`` for a zero-/empty-step workflow (the route turns
        that into a 400), and for an *input* whose name collides with a step
        (contract 5 — same 400 path). ``started_at`` is stamped HERE (at the
        true start), not at the end. Inputs are seeded into ``outputs_json`` at
        creation so the record is honest from its first read. The record is
        refreshed so it stays usable once detached.

        ``started_by`` (v1.352.0): ``STARTED_BY_PERSON`` when a person pressed
        Run — the record carries it, so the run's agent steps (and a resume
        of it) are admitted under Emergency stop. Every other door leaves it
        ``""``.
        """
        if not self._has_runnable_steps(workflow):
            raise ValueError("workflow has no steps")
        seeded = seed_inputs(workflow, inputs)
        # FULL step serialization (v1.121.0): a parked run must be resumable
        # from the database alone, so the record carries everything.
        steps_meta = [step_to_dict(s) for s in workflow.steps]
        # Idea 09 (09-a): a block that would run wrongly (no condition,
        # nothing to work through, too deep, a limit out of range, an ask in
        # a For each that works on several items at once) is refused before
        # the run exists, whichever door started it. Only what a block can
        # get wrong: an older workflow is refused for nothing new.
        from .validate import BlockRefused
        from .validate import problems as _problems

        found = _problems(steps_meta, structure_only=True)
        if found:
            raise BlockRefused(f"This workflow cannot run: {found[0]}.")
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
            # Leap 05-b: a run a RULE started names it ("rule:<source>:<id>",
            # ``triggers/starts.rule_started_by``), so its end never starts
            # that rule again and a run_ended rule's run starts no other.
            # Only the person's mark admits a run under Emergency stop.
            started_by=(
                STARTED_BY_PERSON if started_by == STARTED_BY_PERSON
                else started_by[:120] if str(started_by or "").startswith("rule:")
                else ""
            ),
            # Idea 39 (39-a): run from a chat, its end is reported there.
            chat_thread_id=str(chat_thread_id or "").strip(),
        )
        with session_scope(self.platform.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # un-expire attrs so the detached record stays usable
        return record

    async def run(self, workflow: WorkflowDef, *, started_by: str = "") -> WorkflowRunRecord:
        """Create the record AND run it to completion (synchronous callers).
        ``started_by`` as :meth:`create_record` (the CLI passes a person)."""
        record = self.create_record(workflow, started_by=started_by)
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
        # Idea 09: a block (If, For each) is ALWAYS solo too (``validate``
        # refuses a group on one; this keeps an unchecked def honest).
        solo = ("ask", *BLOCK_KINDS)
        while i < n:
            s = steps[i]
            if s.group and s.kind not in solo:
                j = i
                batch = []
                while j < n and steps[j].group == s.group and steps[j].kind not in solo:
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
        """Drive *workflow*'s steps (:meth:`_drive_record`); a driver that
        RAISES outside a step ends the run Failed with the reason before the
        exception goes on (v1.505.0, :meth:`_settled_on_raise`)."""
        return await self._settled_on_raise(
            record.id,
            workflow.name,
            self._drive_record(
                record, workflow, start_index, outputs, session_ids, inputs
            ),
        )

    async def _settled_on_raise(self, run_id: str, workflow_name: str, coro):
        """Await *coro*; when it raises, end the run's row honestly, then
        re-raise (v1.505.0, north-star 5.3).

        A step that raises is already a failed step (``_exec_step``); what was
        left was the DRIVER itself: a record write or read ("database is
        locked" once SQLite's busy wait runs out during Compact or a backup),
        the folder look-up, a publish. The only handler was the orchestrator's
        done-callback log line, so the row read "running" (or "resuming") until
        the next start-up. Now the row ends ``failed`` (``cancelled`` when the
        user had asked it to stop) with the reason in its notes, and
        ``workflow.completed`` is published, so the bell, the Schedules row and
        the run page all hear it. Off the loop and shielded from a cancel.
        A cancellation is NOT settled here: a restart's cancel leaves the row
        for the start-up reconcile, which marks it resumable. Settled ONCE per
        exception (an answer's resume drives :meth:`run_record` inside it)."""
        try:
            return await coro
        except Exception as exc:
            if not getattr(exc, "_ij_workflow_settled", False):
                job = asyncio.ensure_future(
                    self._settle_raised(run_id, workflow_name, exc)
                )
                _SETTLE_JOBS.add(job)
                job.add_done_callback(_SETTLE_JOBS.discard)
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    pass  # the job still lands; the exception still goes on
                except Exception:  # noqa: BLE001 — never masks the driver's own fault
                    pass
                try:
                    exc._ij_workflow_settled = True  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 — a slotted exception: settled twice at worst
                    pass
            raise

    async def _settle_raised(self, run_id: str, workflow_name: str, exc: BaseException) -> None:
        reason = driver_failure_words(exc)
        seq = next(_WRITE_SEQ)
        ended: str | None = None
        for _attempt in range(3):  # each attempt waits out SQLite's busy timeout
            try:
                ended = await asyncio.to_thread(self._write_driver_end, run_id, reason, seq)
                break
            except Exception:  # noqa: BLE001 — still busy: try again, then log
                ended = None
        else:
            log.error("workflow run %s: its driver failed and the end was not saved", run_id)
            return
        if not ended:
            return  # the row had already ended (or is parked): it is honest
        try:
            await self.platform.event_bus.publish(
                EventType.WORKFLOW_COMPLETED,
                {
                    "workflow": workflow_name,
                    "status": ended,
                    "run_id": run_id,
                    "sessions": [],
                    "failed_session": "",
                    "reason": reason,
                    **(await self._chat_payload(run_id)),
                },
            )
        except Exception:  # noqa: BLE001 — the row is right; the notice is best effort
            log.debug("workflow.completed for a failed driver not published", exc_info=True)
        await self._report_end(run_id, workflow_name, ended, reason=reason)

    def _write_driver_end(self, run_id: str, reason: str, seq: int) -> str:
        """End a row its driver left live (blocking: callers hop off the loop).
        ``running``/``resuming`` -> ``failed``, ``cancelling`` -> ``cancelled``,
        the reason appended to the notes; any other status (finished, parked)
        is left as it is and ``""`` is returned."""
        with _WRITE_LOCK:
            with session_scope(self.platform.engine) as db:
                rec = db.get(WorkflowRunRecord, run_id)
                if rec is None or rec.status not in ("running", "resuming", "cancelling"):
                    return ""
                ended = "cancelled" if rec.status == "cancelling" else "failed"
                try:
                    notes = json.loads(rec.notes_json or "[]")
                except (TypeError, ValueError):
                    notes = []
                if not isinstance(notes, list):
                    notes = []
                rec.status = ended
                rec.notes_json = dumps([*notes, reason])
                rec.current_session_id = None
                rec.waiting_json = ""
                rec.finished_at = utcnow()
                db.add(rec)
                db.commit()
            if seq > _LAST_SEQ.get(run_id, -1):
                _LAST_SEQ[run_id] = seq
            return ended

    async def _drive_record(
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
        # v1.352.0: a run a PERSON started (the record says so, so a resume of
        # it says so too) hands that to create_session for its agent steps —
        # Emergency stop admits them ("work you start keeps going").
        person_started = (getattr(record, "started_by", "") or "") == STARTED_BY_PERSON
        # Resolve the pinned project's folder ONCE for the whole run (None when
        # unpinned, or when the folder is missing on disk — see the helper).
        # v1.505.0 (5.4): a SELECT and a disk check — off the loop.
        workspace_root, folder_note = await asyncio.to_thread(
            self._project_folder, workflow.project_id
        )
        # Said ON THE RECORD, before the first step, so a run the user opens
        # mid-way already explains where its files are going — and written
        # UNCONDITIONALLY (v1.231.0, AE16): a resume whose folder is back
        # must clear the original run's "no folder" note, or the record keeps
        # telling the user this resume's files went to a scratch dir.
        await self._persist(run_id, notes=[folder_note] if folder_note else [])
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
            await self._persist(run_id, outputs=outputs)

        def _completed_chain() -> list[tuple[str, str]]:
            chain = []
            for s in steps:
                o = outputs.get(s.name)
                if isinstance(o, dict) and o.get("status") == "completed":
                    chain.append((s.name, str(o.get("summary") or "")))
            return chain

        # Idea 09 (09-a): every step goes through ``_exec_any``; a step of the
        # workflow's own list runs exactly as before, a block runs the steps
        # inside it through the same executor (``_run_list``).
        ctx = _RunCtx(
            orch=orch, run_id=run_id, workflow=workflow, outputs=outputs,
            session_ids=session_ids, workspace_root=workspace_root,
            tool_workspace=tool_workspace, person_started=person_started,
            completed_chain=_completed_chain,
        )
        top = _Frame(prefix="", steps=steps, at=[])
        self._step_runs[run_id] = _leaf_runs(outputs)

        for batch in self._batches(steps, start_index):
            # Cancellation is cooperative: re-read the authoritative status
            # the cancel route wrote before starting each batch.
            # v1.505.0 (5.4): every record read in the run hops off the loop.
            current = await asyncio.to_thread(self._get_record, run_id)
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
                *(self._exec_any(ctx, top, i) for i in pending),
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

            await self._persist(
                run_id,
                current_session_id=None,
                session_ids=session_ids,
                outputs=outputs,
            )

            if ask is not None:
                latest = await asyncio.to_thread(self._get_record, run_id)
                if latest is not None and latest.status == "cancelling":
                    # A cancel landed while the ask batch executed — honor
                    # it instead of parking (and notifying) a dead run.
                    final_status = "cancelled"
                    for s in (steps[i] for i in range(batch[0], len(steps))):
                        outputs.setdefault(s.name, {"status": "skipped"})
                    break
                # PARK (v1.121.0): the run waits durably for the user. Idea 09:
                # an ask inside a block adds where its definition is (``at``),
                # and ``step`` is its path-qualified key.
                await self._persist(
                    run_id,
                    status="waiting",
                    waiting_json=dumps(
                        {
                            "index": ask.index,
                            "step": ask.step,
                            "question": ask.question,
                            **({"at": ask.at} if ask.at else {}),
                        }
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
                await asyncio.to_thread(  # v1.226.0: network send off the loop
                    self._deliver,
                    f"Workflow “{workflow.name}” needs you: {ask.question} "
                    f"— answer it in chat or on the Workflows page.",
                    # v1.360.0 integrate (lane 10A note g): on this PC the
                    # bell counts and pings the parked question (its waiting
                    # row), so "This PC" stays quiet — one notice per ask.
                    skip_desktop=True,
                    # Readiness 12B (0e.4): a destination the ANSWERABLE
                    # question of THIS run reached (comm/prompts, the phone)
                    # gets that one line only — this one said the same
                    # question again. Where it did not go out, this line does.
                    skip_asked=run_id,
                )
                refreshed = await asyncio.to_thread(self._get_record, run_id)
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
        latest = await asyncio.to_thread(self._get_record, run_id)
        if latest is not None and latest.status == "cancelling":
            final_status = "cancelled"
        # Idea 09: a block the run ended inside (a cancel while it was parked
        # or working) never reads "running" on a finished run.
        settle_open_blocks(outputs, final_status)
        self._step_runs.pop(run_id, None)
        # v1.446.0 (review item 22): it finished, but a step it was told to
        # skip past failed — say so, never "completed".
        _halted, skipped_failures = step_failures((s.name for s in steps), outputs)
        if final_status == "completed" and skipped_failures:
            final_status = PART_FAILED_STATUS
        final = await self._persist(
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
                # v1.361.0 (lane 11A): the run of the step that ENDED a failed
                # run ("" when that step had no run of its own, or the run did
                # not fail). Only that step's notice stands in for this one:
                # an earlier attempt that failed and was retried, or a step
                # the run went on past, is another outcome (step_outcome).
                # v1.446.0 (review item 22): for a run that finished with a
                # skipped failure, the run of the FIRST such step ("" for a
                # tool step: no bell row of its own, so the workflow's row is
                # its one notice).
                "failed_session": (
                    ended_by_session((s.name for s in steps), outputs)
                    if final_status == "failed"
                    else str(skipped_failures[0][1].get("session_id") or "")
                    if final_status == PART_FAILED_STATUS
                    else ""
                ),
                # v1.446.0 (review item 22): the steps it went on past, by
                # NAME (at most five) — the bell's row says which.
                **(
                    {"failed_steps": [name for name, _ in skipped_failures[:5]]}
                    if final_status == PART_FAILED_STATUS
                    else {}
                ),
                # Idea 39 (39-a): a run from a chat names it, so the bell and
                # the tray open that chat (absent for every other run).
                **(await self._chat_payload(run_id, final)),
            },
        )
        await self._report_end(
            run_id, workflow.name, final_status,
            steps=[s.name for s in steps], outputs=outputs,
        )
        return final if final is not None else record

    async def _chat_payload(self, run_id: str, rec: Any = None) -> dict[str, str]:
        """Idea 39 (39-a): ``{"chat_thread_id": id}`` for a run set up from a
        chat (``workflow.completed`` then opens that chat in the bell and the
        tray), ``{}`` for any other run, so its payload is as before. Never
        raises."""
        try:
            from ..agents.chat_delivery import clean_chat_thread_id

            if rec is None:
                rec = await asyncio.to_thread(self._get_record, run_id)
            chat = clean_chat_thread_id(getattr(rec, "chat_thread_id", "") or "")
            return {"chat_thread_id": chat} if chat else {}
        except Exception:  # noqa: BLE001 — the notice opens the Workflows page
            return {}

    async def _report_end(
        self,
        run_id: str,
        workflow_name: str,
        status: str,
        *,
        steps: "list[str] | None" = None,
        outputs: "dict[str, Any] | None" = None,
        reason: str = "",
    ) -> None:
        """Idea 39 (39-a): a workflow run from a chat writes ONE report there
        when it ends: how it ended (the run's own status), the step that ended
        a failed run, else the last step's summary. A run the user stopped
        writes nothing. Never raises."""
        try:
            from ..agents import chat_delivery as _cd
            from ..agents import outcome as _oc

            rec = await asyncio.to_thread(self._get_record, run_id)
            chat = _cd.clean_chat_thread_id(getattr(rec, "chat_thread_id", "") or "")
            if not chat or status in ("cancelled", "cancelling"):
                return
            names = list(steps or [])
            outs = outputs if isinstance(outputs, dict) else {}
            halted, skipped = step_failures(names, outs)
            if status == "completed":
                word, lead = _oc.WORD_DONE, ""
            elif status == PART_FAILED_STATUS:
                word = _oc.WORD_PART_FAILED
                lead = "Some of its steps failed: " + ", ".join(
                    f"“{n}”" for n, _ in skipped[:5]) + "."
            else:
                word = _oc.WORD_FAILED
                lead = (f"The step “{halted[0]}” failed." if halted
                        else (reason or "The run did not finish."))
            body = ""
            if halted is not None:
                body = str(halted[1].get("summary") or "")
            else:
                for n in reversed(names):
                    out = outs.get(n)
                    if isinstance(out, dict) and str(out.get("summary") or "").strip():
                        body = str(out.get("summary") or "")
                        break
            report = {
                "kind": "workflow", "name": workflow_name, "id": run_id, "href": "/workflows",
                "outcome": word, "reason": lead, "words": body, "key": f"workflow:{run_id}",
            }
            # Review (39-a, round 3): a step that ran at low trust (it read
            # flagged content; a later step may carry its words) marks the
            # report, so the chat stays out of the recap pass.
            try:
                ran = json.loads(getattr(rec, "session_ids_json", "") or "[]")
            except (TypeError, ValueError):
                ran = []
            ran = (ran if isinstance(ran, list) else []) + [
                o.get("session_id") for o in outs.values() if isinstance(o, dict)
            ]
            receipt = await asyncio.to_thread(_cd.low_trust_receipt, self.platform.engine, ran)
            if receipt is not None:
                report["receipt"] = receipt
            await asyncio.to_thread(_cd.report_to_chat, self.platform, chat, report)
        except Exception:  # noqa: BLE001 — the run's own record stands
            log.warning("workflow run %s could not report to its chat", run_id, exc_info=True)

    async def resume_after_answer(
        self, record: WorkflowRunRecord, answer: str
    ) -> WorkflowRunRecord:
        """:meth:`_resume_after_answer`, ended honestly when it raises
        (v1.505.0, :meth:`_settled_on_raise`)."""
        return await self._settled_on_raise(
            record.id,
            record.workflow_name,
            self._resume_after_answer(record, answer),
        )

    async def _resume_after_answer(
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
        # Idea 09 (09-a): an ask inside a block names where its definition is
        # (``at``); its answer is folded under its path-qualified key and the
        # run is driven again from the BLOCK, which walks its stored list and
        # its taken path past every step already finished.
        at = waiting.get("at") if isinstance(waiting.get("at"), list) else None
        if idx < 0 or idx >= len(wf.steps):
            raise ValueError("this run's parked step could not be reconstructed")
        step = step_at(wf, at) if at else wf.steps[idx]
        name = step_name or step.name
        outputs = record and json.loads(record.outputs_json or "{}") or {}
        folded: dict = {
            "status": "completed",
            "summary": f"User answered: {answer}",
            "kind": "ask",
        }
        session_ids = json.loads(record.session_ids_json or "[]")
        latest = await asyncio.to_thread(self._get_record, record.id)
        if latest is not None and latest.status in ("cancelling", "cancelled"):
            # The user cancelled between answering and the resume starting —
            # the answer must not resurrect the run.
            if latest.status == "cancelling":
                await self._persist(
                    record.id, status="cancelled", finished_at=utcnow(), waiting_json=""
                )
            return latest
        if step.expect:
            problem = self._expect_failure(step, folded)
            await self._note_expect_check(record.id, name, problem)  # release 5: the record of checks
            if problem is not None:
                retries = int(waiting.get("expect_retries") or 0)
                if step.on_failure == "retry" and retries < 1:
                    # ONE re-attempt of an ask is asking AGAIN: re-park with
                    # the same question, the attempt tracked durably so a
                    # second unsatisfying answer fails instead of looping.
                    question = str(waiting.get("question") or "") or (
                        f"Continue past “{name}”?"
                    )
                    await self._persist(
                        record.id,
                        status="waiting",
                        waiting_json=dumps(
                            {
                                "index": idx,
                                "step": name,
                                "question": question,
                                "expect_retries": retries + 1,
                                **({"at": at} if at else {}),
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
                    await asyncio.to_thread(  # v1.226.0: network send off the loop
                        self._deliver,
                        f"Workflow “{record.workflow_name}” needs you again — "
                        f"the answer didn't satisfy the gate ({problem}). "
                        f"{question}",
                    )
                    refreshed = await asyncio.to_thread(self._get_record, record.id)
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
        if at and folded.get("status") == "failed":
            # The block reads a folded failure as the ask's result (it never
            # asks again) and routes it by the ask's own "if this fails".
            folded["folded"] = True
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
                **({"block": wf.steps[idx].name} if at else {}),
            },
        )
        if at:
            await self._persist(
                record.id, status="running", outputs=outputs, waiting_json=""
            )
            return await self.run_record(
                record, wf, start_index=idx, outputs=outputs, session_ids=session_ids
            )
        if folded.get("status") == "failed" and not folded.get("handled"):
            # halt (or an exhausted retry): the run fails, the tail never ran.
            for s in wf.steps[idx + 1 :]:
                outputs.setdefault(s.name, {"status": "skipped"})
            final = await self._persist(
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
                    # v1.361.0 (lane 11A): the ask step that ended it has no
                    # run of its own (see run_record's completion).
                    "failed_session": ended_by_session(
                        (s.name for s in wf.steps), outputs
                    ),
                    **(await self._chat_payload(record.id, final)),
                },
            )
            await self._report_end(
                record.id, record.workflow_name, "failed",
                steps=[s.name for s in wf.steps], outputs=outputs,
            )
            return final if final is not None else record
        await self._persist(
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
        """:meth:`_resume_interrupted`, ended honestly when it raises
        (v1.505.0, :meth:`_settled_on_raise`)."""
        return await self._settled_on_raise(
            record.id, record.workflow_name, self._resume_interrupted(record)
        )

    async def _resume_interrupted(self, record: WorkflowRunRecord) -> WorkflowRunRecord:
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
        latest = await asyncio.to_thread(self._get_record, record.id)
        if latest is not None and latest.status in ("cancelling", "cancelled"):
            # A cancel must never be resurrected by a queued resume.
            if latest.status == "cancelling":
                await self._persist(
                    record.id,
                    status="cancelled",
                    finished_at=utcnow(),
                    waiting_json="",
                )
            return await asyncio.to_thread(self._get_record, record.id) or latest
        await self._persist(
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
        *,
        person_started: bool = False,
        key: "str | None" = None,
        view: "dict | None" = None,
        at: "list | None" = None,
        park_index: "int | None" = None,
        block: str = "",
    ) -> dict:
        """Execute ONE step per its kind; returns its output dict. Raises
        :class:`_AskPark` for ask steps and lets CancelledError propagate.

        Idea 09 (09-a): a step INSIDE a block passes ``key`` (its output's
        path-qualified name, ``Each file/3/Read total``), ``view`` (the values
        it reads its ``{{names}}`` from: ``_scope_view``), ``at`` and
        ``park_index`` (where an ask parks) and ``block`` (the workflow's step
        it sits in, on its events). With none of them the step runs exactly as
        before."""
        key = key or step.name
        scope = view if view is not None else outputs
        in_block = {"block": block} if block else {}
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_STARTED,
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "step": key,
                "index": idx,
                "total": total,
                "agent": step.agent if step.kind == "agent" else "",
                "kind": step.kind,
                "session_id": "",
                **in_block,
            },
        )

        # A VALUE NOTHING GAVE (the intelligence plan, release 3): a name in
        # this step that matches no step that has run, no input and no signal
        # used to be filled in as an empty text without a word, and the step
        # ran on it. The step now stops BEFORE it runs and says which name
        # (``checks.missing_values``); its own ``on_failure`` then decides
        # what the run does, as for any failed step. Never retried: the
        # second try would find the same nothing.
        from .checks import missing_values, no_value_words, unreadable_values, unreadable_words

        absent = missing_values(step, scope)
        # Idea 09: a field read from a step's stored JSON that was clipped
        # cannot be read; the step stops and says so, never runs on "".
        unreadable = [] if absent else unreadable_values(step, scope)

        if step.kind == "ask" and not absent and not unreadable:
            question = render_template(
                step.message or step.task, scope
            ).strip() or f"Continue past “{step.name}”?"
            raise _AskPark(
                idx if park_index is None else park_index, key, question,
                at=at if view is not None else None,
            )

        attempts = 2 if step.on_failure == "retry" else 1
        out: dict = {}
        if absent:
            attempts = 0
            out = {
                "status": "failed",
                "summary": no_value_words(absent),
                "kind": step.kind,
                "no_value_for": absent,
            }
        elif unreadable:
            attempts = 0
            out = {
                "status": "failed",
                "summary": unreadable_words(unreadable),
                "kind": step.kind,
                "no_value_for": unreadable,
            }
        for attempt in range(attempts):
            if step.kind == "tool":
                out = await self._run_tool_step(
                    run_id, step, scope, workspace_root, tool_workspace
                )
            elif step.kind == "notify":
                # v1.226.0: delivery is a serial Slack/Telegram/SMTP round-trip
                # (15s timeout per channel) — off the loop, or a slow webhook
                # parks the whole daemon (/health included) for the duration.
                out = await asyncio.to_thread(self._run_notify_step, step, scope)
            else:
                out = await self._run_agent_step(
                    orch, run_id, workflow, step, outputs, completed,
                    workspace_root, session_ids,
                    person_started=person_started,
                    view=view,
                )
            # Verified steps (v1.170.0, contract 8): expectations run INSIDE
            # the attempt loop so a failed expectation routes through
            # on_failure exactly like any other failure (retry re-attempts,
            # skip continues, halt stops the run).
            if out.get("status") == "completed" and step.expect:
                problem = self._expect_failure(step, out)
                await self._note_expect_check(run_id, step.name, problem)  # release 5: the record of checks
                if problem is not None:
                    out = {
                        **out,
                        "status": "failed",
                        "summary": f"expectation failed: {problem}",
                        "expect_failed": problem,
                    }
            if out.get("status") in ("completed", "cancelled"):
                break  # never "retry" a CANCELLED attempt — the user said stop
            if out.get("refused"):
                # v1.378.0 review: never re-send a task the provider's safety
                # check DECLINED — the same task to the same model is declined
                # again (the router never retries one either).
                break
        if out.get("status") == "failed" and step.on_failure == "skip":
            # The failure stays VISIBLE on the step, but the run continues.
            out["handled"] = "skipped"

        # Persist THIS step's output as it completes (v1.231.0, AE4), not
        # only after the whole batch settles: a daemon death while a parallel
        # sibling is still running used to drop every finished member, so
        # Resume re-ran them — re-delivering a notify, re-writing files.
        outputs[key] = out
        await self._persist(run_id, session_ids=session_ids, outputs=outputs)

        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_COMPLETED,
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "step": key,
                "index": idx,
                "total": total,
                "agent": step.agent if step.kind == "agent" else "",
                "kind": step.kind,
                "session_id": str(out.get("session_id") or ""),
                "status": str(out.get("status") or ""),
                "summary": str(out.get("summary") or "")[:_MAX_STEP_SUMMARY],
                **in_block,
            },
        )
        return out

    # ------------------------------------------------------------------ #
    # blocks: "If" and "For each" (idea 09, phase 09-a)
    # ------------------------------------------------------------------ #

    async def _exec_any(self, ctx: _RunCtx, frame: _Frame, i: int) -> dict:
        """Run step *i* of *frame*'s list, whatever it is: a block through
        :meth:`_exec_block`, any other step through :meth:`_exec_step`. A
        step of the workflow's own list (``frame.prefix`` "") is called
        exactly as before blocks existed."""
        step = frame.steps[i]
        key = frame.prefix + step.name
        prior = ctx.outputs.get(key)
        if isinstance(prior, dict) and prior.get("folded"):
            # An ask inside a block whose answer failed its check: the answer
            # is the step's result; it is never asked again.
            return prior
        if step.kind in BLOCK_KINDS:
            return await self._exec_block(ctx, frame, i)
        nested = bool(frame.prefix)
        if nested and self._step_runs.get(ctx.run_id, 0) >= MAX_STEP_RUNS:
            out = {"status": "failed", "summary": STEP_RUNS_WORDS, "kind": step.kind, "bound": True}
            ctx.outputs[key] = out
            await self._persist(ctx.run_id, outputs=ctx.outputs)
            return out
        self._step_runs[ctx.run_id] = self._step_runs.get(ctx.run_id, 0) + 1
        if not nested:
            return await self._exec_step(
                ctx.orch, ctx.run_id, ctx.workflow, step, i, len(frame.steps),
                ctx.outputs, ctx.completed_chain(), ctx.workspace_root,
                ctx.session_ids, ctx.tool_workspace,
                person_started=ctx.person_started,
            )
        return await self._exec_step(
            ctx.orch, ctx.run_id, ctx.workflow, step, i, len(frame.steps),
            ctx.outputs, ctx.completed_chain() + _local_chain(ctx.outputs, frame),
            ctx.workspace_root, ctx.session_ids, ctx.tool_workspace,
            person_started=ctx.person_started,
            key=key, view=_scope_view(ctx.outputs, frame), at=[*frame.at, i],
            park_index=frame.top_index, block=frame.top_name,
        )

    async def _cancelling(self, run_id: str) -> bool:
        current = await asyncio.to_thread(self._get_record, run_id)
        return current is not None and current.status == "cancelling"

    async def _run_list(self, ctx: _RunCtx, frame: _Frame) -> dict:
        """Run the steps inside a block, in order (a parallel group runs
        together, as in the workflow's own list). Returns ``{"status":
        completed|failed|cancelled, "failed": (name, output) | None,
        "handled": [(name, output), …]}``. An ask inside PARKS the run: its
        :class:`_AskPark` goes up to the driver. Cancel is read before each
        group of steps, as the driver reads it."""
        steps = frame.steps
        handled: list[tuple[str, dict]] = []
        for batch in self._batches(steps, 0):
            if await self._cancelling(ctx.run_id):
                for s in steps[batch[0]:]:
                    ctx.outputs.setdefault(frame.prefix + s.name, {"status": "skipped"})
                await self._persist(ctx.run_id, outputs=ctx.outputs)
                return {"status": "cancelled", "failed": None, "handled": handled}
            pending = [j for j in batch if not _done(ctx.outputs.get(frame.prefix + steps[j].name))]
            if not pending:
                continue
            results = await asyncio.gather(
                *(self._exec_any(ctx, frame, j) for j in pending), return_exceptions=True
            )
            ask: _AskPark | None = None
            cancelled = False
            halt: tuple[str, dict] | None = None
            for j, res in zip(pending, results):
                k = frame.prefix + steps[j].name
                if isinstance(res, _AskPark):
                    ask = res
                    continue
                if isinstance(res, asyncio.CancelledError):
                    cancelled = True
                    ctx.outputs.setdefault(k, {"status": "cancelled"})
                    continue
                if isinstance(res, BaseException):
                    res = {"status": "failed", "summary": f"{type(res).__name__}: {res}", "kind": steps[j].kind}
                    ctx.outputs[k] = res
                st = res.get("status")
                if st == "cancelled":
                    cancelled = True
                elif st == "failed":
                    if res.get("handled") and not res.get("bound"):
                        handled.append((steps[j].name, res))
                    elif halt is None:
                        halt = (steps[j].name, res)
            if ask is not None:
                raise ask
            if cancelled or halt is not None:
                for s in steps[batch[-1] + 1:]:
                    ctx.outputs.setdefault(frame.prefix + s.name, {"status": "skipped"})
                await self._persist(ctx.run_id, outputs=ctx.outputs)
                if cancelled:
                    return {"status": "cancelled", "failed": None, "handled": handled}
                return {"status": "failed", "failed": halt, "handled": handled}
        return {"status": "completed", "failed": None, "handled": handled}

    async def _exec_block(self, ctx: _RunCtx, frame: _Frame, i: int) -> dict:
        """Run one If or For each and record its own output (a rollup: what
        it decided, or how many items were done). Its "if this fails"
        decides what a failure of the block itself does to the list it is
        in: stop it (halt), or go on with the failure visible (skip). A
        limit it reached always stops the run."""
        step = frame.steps[i]
        key = frame.prefix + step.name
        top_index = frame.top_index if frame.prefix else i
        top_name = frame.top_name or step.name
        at = [*frame.at, i]
        in_block = {"block": frame.top_name} if frame.top_name else {}
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_STARTED,
            {
                "run_id": ctx.run_id, "workflow": ctx.workflow.name, "step": key,
                "index": i, "total": len(frame.steps), "agent": "", "kind": step.kind,
                "session_id": "", **in_block,
            },
        )
        if step.kind == "for_each":
            out = await self._exec_for_each(ctx, frame, step, key, at, top_index, top_name)
        else:
            out = await self._exec_if(ctx, frame, step, key, at, top_index, top_name)
        if (
            out.get("status") == "failed"
            and not out.get("handled")
            and not out.get("bound")
            and step.on_failure == "skip"
        ):
            out["handled"] = "skipped"
        ctx.outputs[key] = out
        await self._persist(ctx.run_id, session_ids=ctx.session_ids, outputs=ctx.outputs)
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_STEP_COMPLETED,
            {
                "run_id": ctx.run_id, "workflow": ctx.workflow.name, "step": key,
                "index": i, "total": len(frame.steps), "agent": "", "kind": step.kind,
                "session_id": str(out.get("session_id") or ""),
                "status": str(out.get("status") or ""),
                "summary": str(out.get("summary") or "")[:_MAX_STEP_SUMMARY],
                **in_block,
            },
        )
        return out

    async def _exec_if(
        self, ctx: _RunCtx, frame: _Frame, step: Step, key: str, at: list,
        top_index: int, top_name: str,
    ) -> dict:
        """Check the condition ONCE (no model: ``workflows/logic``), record
        which path it took, mark the other path's steps skipped, run the
        taken one. A resume reads the recorded path: it never decides
        again."""
        from . import logic
        from .checks import missing_values, no_value_words, unreadable_values, unreadable_words

        outputs = ctx.outputs
        prior = outputs.get(key) if isinstance(outputs.get(key), dict) else {}
        # Still working, or finished failed (a resume reaches it again when
        # its failure was skipped past): the lane it took stands either way.
        took = prior.get("took") if prior.get("status") in ("running", "failed") else None
        words = str(prior.get("took_words") or prior.get("summary") or "")
        if took not in ("then", "else"):
            view = _scope_view(outputs, frame)
            absent = missing_values(step, view)
            if absent:
                return {"status": "failed", "summary": no_value_words(absent), "kind": "if", "no_value_for": absent}
            bad = unreadable_values(step, view)
            if bad:
                return {"status": "failed", "summary": unreadable_words(bad), "kind": "if", "no_value_for": bad}
            try:
                held, said = logic.explain(step.condition, lambda t: render_template(t, view))
            except logic.ConditionError as exc:
                return {
                    "status": "failed",
                    "summary": f"The If “{step.name}” could not decide: {exc}.",
                    "kind": "if",
                }
            took = "then" if held else "else"
            words = f"{'Then' if held else 'Otherwise'}: {said}."
            outputs[key] = {"status": "running", "kind": "if", "took": took, "summary": words}
            await self._persist(ctx.run_id, outputs=outputs)
        taken = step.steps if took == "then" else step.else_steps
        other, other_seg = (step.else_steps, "else") if took == "then" else (step.steps, "then")
        for s in other:
            outputs.setdefault(f"{key}/{other_seg}/{s.name}", {"status": "skipped", "kind": s.kind})
        res: dict = {"status": "completed", "failed": None, "handled": []}
        if taken:
            child = _Frame(
                prefix=f"{key}/{took}/", steps=taken,
                at=[*at, "steps" if took == "then" else "else_steps"],
                top_index=top_index, top_name=top_name, parent=frame,
            )
            res = await self._run_list(ctx, child)
        out: dict = {"status": "completed", "kind": "if", "took": took, "summary": words}
        if res["status"] == "cancelled":
            out["status"] = "cancelled"
        elif res["status"] == "failed" and res.get("failed"):
            name, failed = res["failed"]
            out.update(
                status="failed", took_words=words,
                summary=f"{words} “{name}” failed: {failed.get('summary') or 'it failed'}"[:_MAX_STEP_SUMMARY],
            )
            if failed.get("bound"):
                out["bound"] = True
            if failed.get("kind") == "agent" and failed.get("session_id"):
                out["session_id"] = failed["session_id"]
        elif res.get("handled"):
            name, failed = res["handled"][0]
            out.update(
                status="failed", handled="skipped", took_words=words,
                summary=f"{words} “{name}” failed and was skipped: {failed.get('summary') or ''}"[:_MAX_STEP_SUMMARY],
            )
        return out

    async def _items_for(
        self, ctx: _RunCtx, step: Step, view: dict
    ) -> "tuple[list, int, bool, str | None]":
        """``(items, found, outside, problem)``: the list a For each works
        through, how many there were in all, whether their words came from
        outside, or the sentence it stops with. A folder is listed through
        the registry's own ``list_folder`` (its permission and read rules
        apply, and the listing is in the ledger), files only, by name."""
        from .checks import missing_values, no_value_words, unreadable_values, unreadable_words

        over = step.over if isinstance(step.over, dict) else {}
        limit = max(1, int(step.max_items or DEFAULT_MAX_ITEMS))
        absent = missing_values(step, view)
        if absent:
            return [], 0, False, no_value_words(absent)
        files = over.get("files")
        if isinstance(files, dict):
            folder = render_template(str(files.get("folder") or ""), view).strip()
            pattern = render_template(str(files.get("glob") or ""), view).strip() or "*"
            listing = Step(
                name=step.name, kind="tool", tool="list_folder",
                args={"path": folder, "pattern": pattern, "limit": FOLDER_LIST_CAP},
            )
            kept: dict = {}
            out = await self._run_tool_step(
                ctx.run_id, listing, {}, ctx.workspace_root, ctx.tool_workspace, keep=kept
            )
            result = kept.get("result")
            if out.get("status") != "completed" or result is None:
                return [], 0, True, (
                    f"could not list the folder {folder}: {out.get('summary') or 'it could not be read'}"
                )
            data = result.data if isinstance(result.data, dict) else {}
            entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
            base = Path(str(data.get("path") or folder))
            items = sorted(
                (
                    {"name": str(e.get("name")), "path": str(base / str(e.get("name"))), "size": int(e.get("size") or 0)}
                    for e in entries
                    if not e.get("dir") and e.get("name")
                ),
                key=lambda it: it["name"].casefold(),
            )
            listed = int(data.get("total") or 0)
            beyond = max(0, listed - len(entries))
            found = len(items) + beyond
            # The listing is the whole folder only when it was neither cut
            # short (a scan that hit its cap or its deadline) nor showed fewer
            # entries than match. Otherwise nothing is worked through,
            # whatever the limit: a partial listing is not the first files by
            # name (`list_folder` keeps the largest), so even "the first 200"
            # of it would skip files that come earlier.
            if data.get("scan_capped") or beyond:
                return [], 0, True, folder_unlisted_words(
                    str(base), listed, len(entries), bool(data.get("scan_capped")),
                )
            return items[:limit], found, True, None
        source = over.get("from")
        if not isinstance(source, str) or not source.strip():
            return [], 0, False, "there is no list to work through"
        bad = unreadable_values(step, view)
        if bad:
            return [], 0, False, unreadable_words(bad)
        text = render_template(source, view).strip()
        if len(text) >= _MAX_STEP_DATA:
            return [], 0, False, (
                f"the list was too large to read (more than {_MAX_STEP_DATA:,} characters)"
            )
        try:
            parsed = json.loads(text) if text else None
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, list):
            shown = " ".join(text.split())[:80] or "nothing"
            return [], 0, False, f"{source.strip()} is not a list to work through (it gave {shown})"
        # The items are outside words when any value the list is made from
        # is: the reference as written (a step named with a dot owns its
        # name), and the output it reads from, found as the save check finds
        # it (``validate._ref_base``: ``item.sub`` reads ``item``, an outer
        # For each's item, so a nested For each over a field of an item from
        # {{Trigger}} or a folder is fenced too).
        from .validate import _ref_base

        outside = False
        for m in _TEMPLATE_RX.finditer(source):
            ref = m.group(1).strip()
            for name in (ref, _ref_base(ref)):
                base_out = view.get(name)
                if isinstance(base_out, dict) and _from_outside(base_out):
                    outside = True
        return parsed[:limit], len(parsed), outside, None

    async def _exec_for_each(
        self, ctx: _RunCtx, frame: _Frame, step: Step, key: str, at: list,
        top_index: int, top_name: str,
    ) -> dict:
        """Work through a list. The list is written into the block's own
        output BEFORE the first item runs, so a resume walks the same list
        even if the folder changed; each item's steps are kept under
        ``<block>/<n>/`` and a finished item is never run again. A failed
        item follows the block's "if this fails": stop (halt), or go on with
        the next item (skip: the run ends "Part failed"). D-09.1: at most
        ``max_items`` (200) items; the next one stops the run and says so.

        A For each that FINISHED failed keeps its list: a resume that reaches
        it again (a failure it skipped past, or one inside an If's lane that
        was skipped past) gets its own result back, and never lists the
        folder again, so no item's result lands on another item."""
        outputs = ctx.outputs
        prior = outputs.get(key) if isinstance(outputs.get(key), dict) else {}
        if prior.get("status") == "failed" and isinstance(prior.get("list"), list):
            return prior
        if not (prior.get("status") == "running" and isinstance(prior.get("list"), list)):
            items, found, outside, problem = await self._items_for(
                ctx, step, _scope_view(outputs, frame)
            )
            if problem:
                return {
                    "status": "failed",
                    "summary": f"“{step.name}” could not start: {problem}"[:_MAX_STEP_SUMMARY],
                    "kind": "for_each", "done": 0, "failed": 0, "skipped": 0, "total": 0, "data": [],
                }
            # While it works its line counts only what is settled: "6 of 14
            # done", never the items still to come as "not run".
            prior = {
                "status": "running", "kind": "for_each", "summary": rollup_words(0, 0, 0, len(items)),
                "list": items, "found": found, "outside": outside,
                "done": 0, "failed": 0, "skipped": len(items), "total": len(items), "data": [],
            }
            outputs[key] = prior
            await self._persist(ctx.run_id, outputs=outputs)
        items = list(prior["list"])
        found = int(prior.get("found") or len(items))
        outside = bool(prior.get("outside"))
        rows: dict[int, dict] = {}
        for r in prior.get("data") or []:
            if isinstance(r, dict) and isinstance(r.get("n"), int):
                rows[r["n"]] = r
        limit = max(1, int(step.max_items or DEFAULT_MAX_ITEMS))
        parallel = max(1, min(int(step.parallel or 1), MAX_PARALLEL))
        total = len(items)

        def counts() -> tuple[int, int, int]:
            done = sum(1 for r in rows.values() if r.get("status") == "done")
            failed = sum(1 for r in rows.values() if r.get("status") == "failed")
            return done, failed, total - done - failed

        async def note_progress() -> None:
            done, failed, not_run = counts()
            prior.update(
                done=done, failed=failed, skipped=not_run,
                summary=rollup_words(done, failed, 0, total),
                data=[rows[n] for n in sorted(rows)],
            )
            outputs[key] = prior
            await self._persist(ctx.run_id, outputs=outputs)

        first_failure: list[tuple[int, str, dict]] = []

        async def one(n: int, item: Any) -> dict:
            prefix = f"{key}/{n}/"
            child = _Frame(
                prefix=prefix, steps=step.steps, at=[*at, "steps"],
                top_index=top_index, top_name=top_name,
                extras=_item_extras(item, n, outside), parent=frame,
            )
            res = await self._run_list(ctx, child)
            label = _item_label(item, n)
            if res["status"] == "cancelled":
                rows[n] = {"n": n, "label": label, "status": "stopped", "summary": "Stopped."}
            else:
                bad = res.get("failed") or (res["handled"][0] if res.get("handled") else None)
                if bad is not None:
                    name, failed = bad
                    rows[n] = {
                        "n": n, "label": label, "status": "failed",
                        "summary": f"“{name}” failed: {failed.get('summary') or ''}"[:_MAX_ITEM_SUMMARY],
                        # What stopped it, so a resume that finds this row
                        # (and a block that halts) says it the same way.
                        "step": name, "why": str(failed.get("summary") or "")[:_MAX_ITEM_SUMMARY],
                    }
                    if res.get("failed") is not None:
                        # The item's failure was not skipped past inside it:
                        # it stops a block set to stop (a limit, any block).
                        # An inner step's own skip leaves the row without it.
                        rows[n]["halts"] = True
                        if failed.get("bound"):
                            rows[n]["bound"] = True
                    first_failure.append((n, name, failed))
                else:
                    last = ""
                    for s in reversed(step.steps):
                        o = outputs.get(prefix + s.name)
                        if isinstance(o, dict) and o.get("status") == "completed" and str(o.get("summary") or "").strip():
                            last = str(o.get("summary") or "")
                            break
                    rows[n] = {"n": n, "label": label, "status": "done", "summary": last[:_MAX_ITEM_SUMMARY]}
                # The item is settled: keep only a short line of each of its
                # steps (the record grows with every item; this bounds it).
                for k in [k for k in outputs if k.startswith(prefix)]:
                    o = outputs[k]
                    if isinstance(o, dict):
                        small = {
                            "status": o.get("status"),
                            "summary": str(o.get("summary") or "")[:_MAX_ITEM_SUMMARY],
                        }
                        for keep in ("kind", "handled", "took", "session_id", "bound", "folded"):
                            if o.get(keep) not in (None, ""):
                                small[keep] = o[keep]
                        outputs[k] = small
            await note_progress()
            return res

        pending = [(n, it) for n, it in enumerate(items, 1) if rows.get(n, {}).get("status") not in ("done", "failed")]
        stop: tuple[int, dict] | None = None
        cancelled = False
        # A block that stopped at a failed item stays stopped there: a resume
        # that finds the row that stopped it runs no item after it (an item
        # before it that was under way, several at once, is finished). Only a
        # row that STOPPED the block counts: one whose failure an inner step
        # skipped past ("halts" unset) was gone past on purpose, and a
        # limit reached inside an item stops even a block set to skip.
        def stopped_here(row: dict) -> bool:
            return row.get("status") == "failed" and bool(
                row.get("bound") or (row.get("halts") and step.on_failure != "skip")
            )

        halted_at = next((n for n in sorted(rows) if stopped_here(rows[n])), None)
        if halted_at is not None:
            row = rows[halted_at]
            pending = [(n, it) for n, it in pending if n < halted_at]
            why = {"summary": str(row.get("why") or row.get("summary") or "")}
            if row.get("bound"):
                why["bound"] = True
            stop = (halted_at, why)
            first_failure.append((halted_at, str(row.get("step") or step.name), {}))
        if parallel == 1:
            for n, item in pending:
                if await self._cancelling(ctx.run_id):
                    cancelled = True
                    break
                res = await one(n, item)
                if res["status"] == "cancelled":
                    cancelled = True
                    break
                bad = res.get("failed")
                if bad is not None and (bad[1].get("bound") or step.on_failure != "skip"):
                    stop = (n, bad[1])
                    break
        else:
            gate = asyncio.Semaphore(parallel)
            halted = asyncio.Event()

            async def guarded(n: int, item: Any) -> None:
                nonlocal stop, cancelled
                async with gate:
                    if halted.is_set():
                        return
                    if await self._cancelling(ctx.run_id):
                        cancelled = True
                        halted.set()
                        return
                    try:
                        res = await one(n, item)
                    except _AskPark:
                        # ``validate`` refuses an ask here; an unchecked def
                        # still never parks half a batch of items.
                        res = {"status": "failed", "failed": (step.name, {
                            "status": "failed",
                            "summary": "a For each that works on several items at once cannot ask you",
                        }), "handled": []}
                        rows[n] = {"n": n, "label": _item_label(item, n), "status": "failed",
                                   "summary": res["failed"][1]["summary"], "halts": True}
                        await note_progress()
                    if res["status"] == "cancelled":
                        cancelled = True
                        halted.set()
                        return
                    bad = res.get("failed")
                    if bad is not None and (bad[1].get("bound") or step.on_failure != "skip"):
                        if stop is None:
                            stop = (n, bad[1])
                        halted.set()

            await asyncio.gather(*(guarded(n, it) for n, it in pending))

        done, failed, not_run = counts()
        out: dict = {
            "status": "completed", "kind": "for_each",
            "summary": rollup_words(done, failed, not_run, total),
            "done": done, "failed": failed, "skipped": not_run, "total": total,
            "data": [rows[n] for n in sorted(rows)],
        }
        if outside:
            out["outside"] = True
        if cancelled:
            out["status"] = "cancelled"
            return out
        # Every way out from here but "completed" is "failed": the list stays
        # with it (see the docstring), and goes once the block is done.
        out["list"], out["found"] = items, found
        if stop is not None:
            n, bad = stop
            name = next((f[1] for f in first_failure if f[0] == n), step.name)
            out.update(
                status="failed",
                summary=(
                    f"{out['summary']}. Item {n} ({_item_label(items[n - 1], n)}) failed at "
                    f"“{name}”: {bad.get('summary') or 'it failed'}"
                )[:_MAX_STEP_SUMMARY],
            )
            if bad.get("bound"):
                out["bound"] = True
            if bad.get("kind") == "agent" and bad.get("session_id"):
                out["session_id"] = bad["session_id"]
            return out
        if found > limit:
            over = step.over if isinstance(step.over, dict) else {}
            out.update(
                status="failed", bound=True,
                summary=items_bound_words(
                    step.name, limit, found, done, folder=isinstance(over.get("files"), dict),
                ),
            )
            return out
        if failed:
            first = min(first_failure, key=lambda f: f[0]) if first_failure else None
            out.update(status="failed", handled="skipped")
            if first is not None and first[2].get("kind") == "agent" and first[2].get("session_id"):
                out["session_id"] = first[2]["session_id"]
            return out
        out.pop("list")
        out.pop("found")
        return out

    async def _run_agent_step(
        self, orch, run_id, workflow, step, outputs, completed,
        workspace_root, session_ids, *, person_started: bool = False,
        view: "dict | None" = None,
    ) -> dict:
        # Idea 09: a step inside a block reads its names from ``view`` (its
        # item, its own list's steps); the RECORD is still ``outputs``.
        scope = view if view is not None else outputs
        # v1.502.0 (north-star 2.8): a signal's words go in FENCED, and a run
        # an outside signal started runs its agent steps at LOW trust.
        task_text = render_template(step.task, scope, fence_outside=True) + self._context_block(completed)
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
            # v1.352.0: passed ONLY for a person's run, so every other door's
            # call (and a test double's create_session) is byte-identical.
            **({"started_by_person": True} if person_started else {}),
            # v1.502.0: passed ONLY for a signal the router marked low.
            **signal_trust(scope),
        )
        session_ids.append(session.id)
        # Record the live session id BEFORE running, so a cancel arriving
        # mid-step can find and stop it.
        await self._persist(
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
        except Exception as exc:  # noqa: BLE001 — a crashed session is a failed step
            # Mirrors the tool branch (v1.231.0, AE3): ``run_session``
            # re-raises provider/DB blow-ups after finalizing the session, and
            # letting that escape the attempt loop skipped retries and
            # on_failure entirely, fired no step event, and halted the run.
            refused = _refusal_words(exc)
            if refused:
                # v1.378.0 review: a SAFETY REFUSAL is said in the run's
                # plain words (never "ProviderRefusal: … stop_reason …"), and
                # marked so the attempt loop never re-sends it.
                return {
                    "session_id": session.id,
                    "status": "failed",
                    "summary": refused,
                    "refused": True,
                    "tool": step.tool,
                    "kind": "agent",
                }
            return {
                "session_id": session.id,
                "status": "failed",
                # Readiness 12A review (G28): the run's OWN plain sentence
                # (what its run page says), never "ProviderError: codex-cli:
                # CLI exited 1: …" on the Workflows page, the scheduled
                # workflow's Schedules row and the phone.
                "summary": await self._failed_run_words(orch, session, exc),
                "tool": step.tool,
                "kind": "agent",
            }
        # v1.503.0 (north-star 3.2): the step reads the run's TRUE ending —
        # its status AND the ledger's verdict (``agents/outcome.run_ending``).
        # A run that completed while its ask went unanswered (``needs_you``)
        # or a change failed (``completed_with_failures``) did not do the
        # step's work: it is a FAILED step, so ``on_failure`` routes it and a
        # later step never runs on work that was never done. Its summary
        # opens with why, in the Schedules row's words, so the run record,
        # the scheduled workflow's row and the phone all say it.
        from ..agents.outcome import run_ending

        ending = run_ending(session.status, getattr(session, "outcome", None))
        summary = session.summary
        if session.status is SessionStatus.COMPLETED and not ending.ok:
            own = str(summary or "").strip()
            summary = f"{ending.reason} {own}" if own else ending.reason
        return {
            "session_id": session.id,
            "status": "completed" if ending.ok else "failed",
            "summary": summary,
            "tool": step.tool,
            "kind": "agent",
            # Additive: the verdict itself (None for a run with none).
            "outcome": ending.outcome,
        }

    async def _failed_run_words(self, orch, session, exc: BaseException) -> str:
        """A failed agent step's summary when its run RAISED (readiness 12A
        review, G28): the run's own summary as ``Orchestrator._finalize_failed``
        wrote it ("The run failed: Codex couldn't finish this task. …" — the
        raw text folded on its run page), else the same rule applied here
        (``failure_words.plain_reason``). Never the exception's class or its
        raw text. The row is read off the loop. Never raises."""
        try:
            get = getattr(orch, "get_session", None)
            if callable(get):
                row = await asyncio.to_thread(get, session.id)
                status = getattr(row, "status", None)
                status = getattr(status, "value", status)
                summary = str(getattr(row, "summary", "") or "").strip()
                if status == "failed" and summary:
                    return summary
        except Exception:  # noqa: BLE001 — the rule below still words it
            pass
        try:
            from ..providers.failure_words import plain_reason

            reason = plain_reason(
                exc,
                manager=getattr(self.platform, "providers", None),
                provider=str(getattr(session, "provider", "") or ""),
            )
        except Exception:  # noqa: BLE001 — wording only
            reason = ""
        return f"The run failed: {reason}" if reason else "The run failed."

    async def _run_tool_step(
        self,
        run_id: str,
        step: Step,
        outputs: dict,
        workspace_root: str | None,
        tool_workspace,
        *,
        keep: "dict | None" = None,
    ) -> dict:
        """ONE deterministic tool call — no LLM, no session. Same permission
        engine as every other tool invocation (a workflow must not be a
        permission bypass). ``keep`` (idea 09): the call's own result is put
        in ``keep["result"]`` (a For each reads a folder's whole listing,
        which the step's clipped ``data`` would cut short)."""
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
                # Third plan PG2 (step 3): in the app's words. Never "set the
                # tool to auto-approve": a floor tool cannot be, and a stored
                # workflow is not the place to lift a tool's ask.
                return {
                    "status": "failed",
                    "summary": tier_refusal_words(step, base),
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
        # THE TOOL'S OWN CHECK (third plan PG2, decision K2). The tier above
        # is not the whole gate: a tool may ask about THIS call every time
        # (an outbound webhook, a memory save the profile wants asked, a
        # write into Git's own files). Read through the one helper both chat
        # lanes and the agent runtime call; a check that says ask is refused
        # in words through the registry's deny seam, so the ledger records
        # the refusal and the tool never runs. If the registry ever asks the
        # check itself, it sees this ``deny_reason`` first: one refusal.
        refusal = await self._own_check_refusal(step, rendered, ctx)
        if refusal:
            try:
                await self.platform.registry.invoke(
                    step.tool,
                    rendered,
                    ctx,
                    self.platform.permissions,
                    None,
                    deny_reason=refusal,
                    deny_label=OWN_CHECK_LABEL,
                )
            except Exception:  # noqa: BLE001 — the ledger row is best effort; the step still fails
                log.warning("workflow tool step %s: could not record the refusal", step.name, exc_info=True)
            return {
                "status": "failed",
                "summary": refusal[:_MAX_STEP_SUMMARY],
                "tool": step.tool,
                "kind": "tool",
            }
        # v1.505.0 (north-star 5.1): the SAME per-call deadline an agent run
        # and both chat lanes pass (``config.tool_call_timeout_s``, read live),
        # so a call that never answers is a failed step in the registry's
        # words, never a run (or a schedule's thread) held for ever. And the
        # call is its OWN task, registered under the run id, so Cancel can
        # stop it mid-call (``cancel_tool_steps``); the registry's cancel
        # path ledgers it.
        from ..agents.runtime import _tool_deadline

        loop = asyncio.get_running_loop()
        call = asyncio.ensure_future(
            self.platform.registry.invoke(
                step.tool,
                rendered,
                ctx,
                self.platform.permissions,
                None,
                session_allow=grant,
                deadline_s=_tool_deadline(self.platform.config),
            )
        )
        _track_tool_call(run_id, loop, call)
        try:
            result = await call
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise  # the RUN itself is being stopped (a restart): unwind
            return {
                "status": "cancelled",
                "summary": TOOL_STEP_STOPPED,
                "tool": step.tool,
                "kind": "tool",
            }
        except Exception as exc:  # noqa: BLE001 — a crashed tool is a failed step
            return {
                "status": "failed",
                "summary": f"{type(exc).__name__}: {exc}",
                "tool": step.tool,
                "kind": "tool",
            }
        finally:
            _untrack_tool_call(run_id, loop, call)
        if keep is not None:
            keep["result"] = result
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

    async def _own_check_refusal(self, step: Step, rendered: dict, ctx) -> str:
        """The refusal sentence when the tool's OWN check asks about this call
        (third plan PG2), else "". ``tools/base.spend_checks_for`` is the one
        reader (the tool's ``spend_check``, plus Ask first's guard for Git's
        and Iron Jarvis's own files on the file tools); no posture rides
        along, so a check marked "Don't ask skips it" still applies (a
        workflow has no "Don't ask"). A verdict that does not ask changes
        nothing here: the tier above decided, as before.

        No standing approval covers a call its own check asks about: in every
        lane a check that asks is answered one call at a time (the runtime's
        ``can_always`` needs no check, the stream card's ``not _spend_card``),
        so "Always for this workflow" (D10) never lifts it, and a tool step
        has no way to wait for an answer. Refused, never parked."""
        from types import SimpleNamespace

        from ..tools.base import spend_checks_for

        call = SimpleNamespace(name=step.tool, arguments=rendered)
        checks = await spend_checks_for(self.platform.registry, [call], ctx)
        check = checks.get(id(call))
        if check is None or not getattr(check, "ask", False):
            return ""
        return own_check_refusal_words(step, check)

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

    async def _note_expect_check(self, run_id: Any, step_name: Any, problem: "str | None") -> None:
        """The intelligence plan, release 5: a step's stated expectation, as
        ONE row of the record of checks (``checks/existing.workflow_check``:
        held, did not hold, or could not be looked for), under the workflow
        run. What the step and the run do with ``problem`` is unchanged. Off
        the loop; never raises."""
        try:
            from ..checks import existing as _existing
            from ..checks import record as _checks

            row = _existing.workflow_check(step_name, problem, run_id=str(run_id or ""))
            if row is not None and str(run_id or ""):
                await _checks.note(self.platform.engine, [row], run_id=str(run_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a record is never worth a failed step
            get_logger(__name__).debug("a workflow step's check was not recorded", exc_info=True)

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

    def _deliver(
        self, message: str, *, skip_desktop: bool = False, skip_asked: str = ""
    ) -> tuple[bool, str]:
        """Send to EVERY destination (schedule-delivery semantics — a workflow
        speaking to nobody is indistinguishable from a broken one). Returns
        (any_delivered, detail); never raises. Messages are capped below
        Telegram's 4096-char limit so one verbose step can't kill delivery.

        ``skip_desktop`` (v1.360.0 integrate): leave out "This PC" — for a
        parked run's question, which the bell already counts and pings on
        this PC. A notify STEP keeps every destination: it is the user's own
        message, and nothing else says it.

        ``skip_asked`` (readiness 12B, 0e.4): a parked run's id — leave out
        every destination the ANSWERABLE copy of that run's question REACHED
        (``comm.prompts.question_destinations`` — the phone's own chat), so
        the phone hears the question ONCE, in the copy it can answer; a
        destination it did not reach keeps this line (review)."""
        try:
            notifier = self.platform.notifier
            msg = message if len(message) <= 3500 else message[:3500] + "…"
            targets = list(notifier.channels())
            if skip_desktop:
                without = getattr(notifier, "without_desktop", None)
                if callable(without):
                    targets = list(without(targets))
            if skip_asked:
                from ..comm.prompts import question_destinations

                asked = question_destinations(notifier, skip_asked)
                targets = [t for t in targets if t not in asked]
            if (skip_desktop or skip_asked) and not targets:
                # An EMPTY list would fall back to the default channel
                # (Notifier._targets) — "This PC" again. Nothing else to
                # send to: the bell (and the phone's answerable copy) say it.
                return False, "no destination besides This PC (the bell says it)"
            results = notifier.notify(msg, targets)
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
        return self._project_folder(project_id)[0]

    def _project_folder(self, project_id: str | None) -> tuple[str | None, str | None]:
        """``(folder, note)`` for a pinned run (v1.225.0). The folder is the
        same answer :meth:`_project_workspace_root` gives; the note is the
        sentence the run record now carries when that answer is a silent
        degradation — the project row is gone, or its folder is set but
        missing on disk — so "it ran but nothing landed in my folder" has a
        reason the user can read on the run instead of a mystery."""
        if not project_id:
            return None, None
        with session_scope(self.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None:
            return None, (
                f"this workflow is pinned to project {project_id}, which no longer "
                "exists — its steps ran ungrounded, in a scratch workspace; unpin "
                "or re-pin the workflow on the Workflows page"
            )
        root_raw = (project.root or "").strip()
        if not root_raw:
            return None, None  # a project without a folder: by design, no note
        root = Path(root_raw)
        if root.is_dir():
            return str(root), None
        return None, (
            f"project “{project.name}” has no folder at {root_raw} any more — its "
            "steps ran in a scratch workspace, NOT in the project folder; update "
            "the folder on the project page and run again"
        )

    async def _persist(self, run_id: str, **fields: Any) -> WorkflowRunRecord | None:
        """Write *fields* off the loop, in the ORDER the run asked (v1.323.1).

        Each write is stamped here, on the loop, in the order the run asked.
        Worker threads used to commit in whatever order they got the database:
        a write held up by SQLite's busy back-off could commit AFTER a newer
        one and put back an older ``outputs`` — on CI a crashed run's late
        ``{Tell}`` landed after the resume's final write and wiped the
        finished ``Work`` from the record, which is exactly what Resume reads
        (the AE4 promise). Now a write whose stamp is older than one already
        committed for the run keeps its other fields but drops ``outputs`` and
        ``session_ids`` (see :meth:`_update_record`)."""
        seq = next(_WRITE_SEQ)
        return await asyncio.to_thread(self._update_record, run_id, _seq=seq, **fields)

    def _get_record(self, run_id: str) -> WorkflowRunRecord | None:
        with session_scope(self.platform.engine) as db:
            return db.get(WorkflowRunRecord, run_id)

    def _update_record(
        self, run_id: str, _seq: int | None = None, **fields: Any
    ) -> WorkflowRunRecord | None:
        """Apply the given fields to the record and persist. ``session_ids`` and
        ``outputs`` are JSON-encoded; other keys map straight onto the column.

        SYNC on purpose; every caller in the async run/resume paths hops it
        through ``asyncio.to_thread`` (v1.226.0): a write that lands while
        another writer holds the file (Settings → Compact runs VACUUM; a big
        import) waits up to busy_timeout — 30s — and inline that wait parked
        the daemon's one event loop, /health included ("Daemon offline").

        ``_seq`` comes from :meth:`_persist`; see there for the ordering rule.
        The lock makes check-commit-record one step, so two writes can never
        both pass the check and then commit in the wrong order."""
        with _WRITE_LOCK:
            if _seq is not None:
                if _seq < _LAST_SEQ.get(run_id, -1):
                    fields.pop("outputs", None)
                    fields.pop("session_ids", None)
                else:
                    _LAST_SEQ[run_id] = _seq
                    while len(_LAST_SEQ) > _LAST_SEQ_CAP:
                        _LAST_SEQ.pop(next(iter(_LAST_SEQ)))
            return self._write_fields(run_id, fields)

    def _write_fields(self, run_id: str, fields: dict) -> WorkflowRunRecord | None:
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
                # Shallow snapshot: this runs OFF the loop while a parallel
                # sibling on the loop may add its own key (v1.231.0, AE4).
                rec.outputs_json = dumps(dict(fields["outputs"]))
            if "finished_at" in fields:
                rec.finished_at = fields["finished_at"]
            if "waiting_json" in fields:
                rec.waiting_json = fields["waiting_json"]
            if "notes" in fields:
                rec.notes_json = dumps(list(fields["notes"]))
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec
