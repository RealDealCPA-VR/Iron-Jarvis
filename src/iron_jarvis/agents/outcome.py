"""What a session ACTUALLY did (v1.149.0) — read out of the ledger, not the prose.

The report this answers: "agents must stop only describing what they intend to
do." Iron Jarvis was never guessing — every tool call, every file mutation and
every failure has been recorded in ``ToolInvocation`` + ``UndoJournal`` since
TX-01. What was missing is that nothing SUMMARISED it back to the person who
asked; the only thing they saw was the model's own closing paragraph, which is
exactly the part that can claim work it never did.

So this module derives the outcome from the RECORD:

* **files created / changed** come from the undo journal's own descriptor —
  ``file_delete`` means the tool created a file (undo unlinks it), and
  ``file_restore`` means it overwrote one (undo puts the old bytes back). That
  is a fact about a mutation that happened, not a sentence a model wrote.
* **tools used, and which of them FAILED**, come from the invocation rows.
* **errors** are the failed rows' own messages, verbatim.
* **revertable** counts the actions that still have a usable inverse, so the UI
  can offer "revert this task" only when it is genuinely possible.

Nothing here is inferred from the reply text. If the model says it wrote a file
and no write is journaled, this reports no file — and that disagreement is the
entire point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.models import AgentRun, EventRecord, Session, ToolInvocation, UndoJournal
from ..deliverables.scope import LIMITS_LEAD as _LIMITS_LEAD
from ..tools.base import Reversibility

#: Leap 05-c: how the summary of a run that stopped at its rule's kept Limits
#: begins (``reflex/limits.STOP_LEAD``; the runtime writes it, by code).
LIMITS_STOP_LEAD = "Stopped at its limits:"

#: The verdicts ``Session.outcome`` may carry (v1.227.0, audit A5/U1).
#: ``status`` says how the RUN ended; these say whether the JOB was done.
OUTCOME_COMPLETED = "completed"
OUTCOME_WITH_FAILURES = "completed_with_failures"
OUTCOME_NEEDS_YOU = "needs_you"
#: v1.503.0 (north-star 3.11): an UNATTENDED run that completed without a
#: single tool call (see :data:`DID_NOTHING_DOORS`). Nobody was reading its
#: answer as it came, and "completed" with no action is the shape of a run
#: that DESCRIBED the work instead of doing it.
OUTCOME_DID_NOTHING = "did_nothing"
#: Leap 06-a: a REHEARSAL's run that completed. Nothing was changed, so it is
#: never "done", "part failed" or "did nothing"; the bell and every notice
#: stay quiet for it (``comm/notifier.bell_pings``), and its report is built
#: from its rehearsed rows (``tools/rehearsal_runs``).
OUTCOME_REHEARSED = "rehearsed"
OUTCOMES = (
    OUTCOME_COMPLETED, OUTCOME_WITH_FAILURES, OUTCOME_NEEDS_YOU, OUTCOME_DID_NOTHING,
    OUTCOME_REHEARSED,
)

#: The doors (``Session.origin`` is ``<door>:<name>``) whose run is a JOB done
#: with nobody watching, so an empty ledger is not "done" (v1.503.0, 3.11): a
#: schedule, a queued job (assignment), a goal iteration, a rule (reflex) and
#: autonomy.
#: Deliberately NOT here: every attended door (chat, job, project, user, an
#: untagged run a person started) — a run there that simply answered a
#: question did its job, and the person read the answer; a PHONE task (comm)
#: and a task from another computer (remote), whose answer goes straight back
#: to the person or the chat that asked, so a question answered in words IS
#: the job and a bell for it would be noise (lead review: quiet unless the
#: user is needed); a WORKFLOW step, whose text is often the work itself (a
#: summary the next step uses); and ``self_dev``, which a person starts. An
#: allow list, so a new door is "done" until someone decides otherwise.
DID_NOTHING_DOORS = ("schedule", "assignment", "goal", "reflex", "autonomy")


def did_nothing_door(origin: Any) -> bool:
    """Is ``origin`` a door whose run with an empty ledger did nothing?"""
    door = str(origin or "").strip().partition(":")[0].strip().lower()
    return door in DID_NOTHING_DOORS

#: The decision an ``approval.resolved`` event carries when the clock, not the
#: user, answered (``agents/runtime._pause_for_approval``).
_TIMEOUT_DECISION = "timeout"


def _count_unanswered_asks(db, session_id: str) -> int:
    """How many of this session's asks were answered by the CLOCK.

    Counted from the persisted ``approval.resolved`` events with
    ``decision == "timeout"`` — the same rows the bell and the audit timeline
    read, so this number can never disagree with them. The approvals registry
    itself is deliberately in-memory and holds no history (core/approvals.py),
    which is why the event log is the record here.
    """
    count = 0
    rows = db.exec(
        select(EventRecord.payload_json).where(
            EventRecord.type == EventType.APPROVAL_RESOLVED,
            EventRecord.session_id == session_id,
        )
    )
    for raw in rows:
        raw = raw if isinstance(raw, str) else raw[0]
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("decision") == _TIMEOUT_DECISION:
            count += 1
    return count


def derive_outcome(
    result: dict[str, Any], status: Any = None, *, origin: Any = None
) -> str | None:
    """The verdict on the JOB, from a ``session_result`` payload. Pure.

    * ``needs_you`` — any ask for the session resolved ``timeout``. The run
      may read COMPLETED (it ended without an exception) or FAILED; either way
      the job is waiting on the person, and that is the headline.
    * ``completed_with_failures`` — the run completed, nobody was asked and
      left unanswered, and at least one MUTATING tool call failed, or
      (v1.503.0, north-star 3.3) a step of its PLAN failed its check
      (``plan_steps_failed``, read from the run's kept plan).
    * ``did_nothing`` (v1.503.0, 3.11) — the run completed without a single
      tool call and ``origin`` is an unattended door
      (:func:`did_nothing_door`). Without ``origin`` it is never derived.
    * ``completed`` — the run completed and nothing above applies.
    * ``None`` — not found, not finished, or a status (failed with no
      unanswered ask, cancelled) that already tells the truth on its own.

    ``status`` overrides the payload's — the orchestrator derives the verdict
    BEFORE it persists the terminal status, when the row still reads active.
    """
    if not result.get("found"):
        return None
    status_value = status if status is not None else result.get("status")
    status_value = str(getattr(status_value, "value", status_value) or "").lower()
    if result.get("rehearsal"):
        # Leap 06-a: a rehearsal changed nothing and asked nobody; a failed
        # or stopped one says so through its status.
        return OUTCOME_REHEARSED if status_value == "completed" else None
    if int(result.get("unanswered_asks") or 0) > 0:
        return OUTCOME_NEEDS_YOU
    if status_value != "completed":
        return None
    if int(result.get("outside_limits") or 0) > 0:
        # Leap 05-c: it stopped at its rule's kept Limits; the person decides.
        return OUTCOME_NEEDS_YOU
    if int(result.get("tools_failed_mutating") or 0) > 0:
        return OUTCOME_WITH_FAILURES
    if int(result.get("plan_steps_failed") or 0) > 0:
        return OUTCOME_WITH_FAILURES
    if not result.get("tools_used") and did_nothing_door(origin):
        return OUTCOME_DID_NOTHING
    return OUTCOME_COMPLETED


def session_outcome(engine, session_id: str, status: Any = None) -> str | None:
    """``derive_outcome`` over a fresh ledger read — the finalize-time call.
    BLOCKING (SQLite reads); the orchestrator runs it off the loop."""
    return derive_outcome(session_result(engine, session_id), status)


# --- the ONE reading of a finished run (v1.503.0, north-star 3.1) ------------

#: The word for each ending, exactly as every status badge says it
#: (``dashboard/lib/statusWords.ts``: STATUS + OUTCOME).
WORD_DONE = "Done"
WORD_NEEDS_YOU = "Needs you"
WORD_PART_FAILED = "Part failed"
WORD_DID_NOTHING = "Did nothing"
WORD_FAILED = "Failed"
WORD_STOPPED = "Stopped"

_STATUS_WORDS = {
    "completed": WORD_DONE,
    "failed": WORD_FAILED,
    "cancelled": WORD_STOPPED,
}
_OUTCOME_WORDS = {
    OUTCOME_NEEDS_YOU: WORD_NEEDS_YOU,
    OUTCOME_WITH_FAILURES: WORD_PART_FAILED,
    OUTCOME_DID_NOTHING: WORD_DID_NOTHING,
}
#: A failed or stopped run that carries no sentence of its own.
_FAILED_REASON = "The run did not finish."
_STOPPED_REASON = "The run was stopped before it finished."


class RunEnding(NamedTuple):
    """How a finished run REALLY ended (:func:`run_ending`).

    ``status`` — the run's status value ("completed", "failed", …);
    ``outcome`` — the ledger's verdict on the job (``Session.outcome``, None
    when there is none); ``ok`` — the JOB was done: the run completed AND its
    verdict is "completed" (or there is no verdict to read, an older row);
    ``words`` — the badge word every surface says; ``reason`` — one plain
    sentence on why it fell short ("" when ``ok``)."""

    status: str
    outcome: str | None
    ok: bool
    words: str
    reason: str


def _has_limits(session: Any) -> bool:
    """Leap 05-c: does the run's OWN options carry a rule's Limits
    (``reflex/limits.LIMITS_OPTION``)? Pure; never raises."""
    try:
        from ..reflex.limits import LIMITS_OPTION
        from ..scheduling.knobs import session_options

        return isinstance(session_options(session).get(LIMITS_OPTION), dict)
    except Exception:  # noqa: BLE001
        return False


def _stopped_at_limits(session: Any) -> bool:
    """Leap 05-c: did this run STOP at its rule's kept Limits? Its OWN
    options carry the Limits (``reflex/limits.LIMITS_OPTION``, written by the
    rule's door) AND the runtime's stamp that it stopped there
    (``STOPPED_OPTION``, by code). Pure; never raises."""
    try:
        from ..reflex.limits import LIMITS_OPTION, STOPPED_OPTION
        from ..scheduling.knobs import session_options

        opts = session_options(session)
        return isinstance(opts.get(LIMITS_OPTION), dict) and opts.get(STOPPED_OPTION) is True
    except Exception:  # noqa: BLE001
        return False


def run_ending(status_or_session: Any, outcome: Any = None) -> RunEnding:
    """THE one reading of a finished run (v1.503.0, north-star 3.1): a run's
    status says how the RUN ended, ``Session.outcome`` (the ledger's verdict,
    derived at finalize) says whether the JOB was done, and every door that
    acts on a finished run reads both here — a workflow's agent step, a
    queued job, a lead's hand-off, the night shift — never ``status`` alone.

    Takes a session-like object (``.status`` / ``.outcome``) or a status and
    an outcome. Only a COMPLETED run reads its verdict, as the badges do: a
    failed or stopped run already says what happened. The reasons are the
    Schedules row's own sentences (``scheduling/run_outcome``). Pure; never
    raises."""
    if outcome is None and hasattr(status_or_session, "status"):
        outcome = getattr(status_or_session, "outcome", None)
        status = getattr(status_or_session, "status", None)
    else:
        status = status_or_session
    status_value = str(getattr(status, "value", status) or "").strip().lower()
    verdict = str(outcome or "").strip().lower() or None
    try:
        from ..scheduling.run_outcome import (
            DID_NOTHING_LEAD,
            NEEDS_YOU_LEAD,
            PARTIAL_LEAD,
        )
    except Exception:  # noqa: BLE001 — the words are a reading, never the verdict
        NEEDS_YOU_LEAD = PARTIAL_LEAD = DID_NOTHING_LEAD = _FAILED_REASON
    if status_value == "completed":
        if verdict in _OUTCOME_WORDS:
            reason = {
                OUTCOME_NEEDS_YOU: NEEDS_YOU_LEAD,
                OUTCOME_WITH_FAILURES: PARTIAL_LEAD,
                OUTCOME_DID_NOTHING: DID_NOTHING_LEAD,
            }[verdict]
            if verdict == OUTCOME_NEEDS_YOU:
                # Leap 05-c: a run that stopped at its rule's kept Limits
                # needs you for that reason, not for an unanswered ask: its
                # summary is the runtime's own words (by code) and says why.
                # Read only for a run the runtime stamped as stopped there (a
                # model's closing words never become the reason otherwise,
                # not even in a run with Limits that needs you for an ask).
                said = " ".join(str(getattr(status_or_session, "summary", "") or "").split())
                if said.startswith(LIMITS_STOP_LEAD) and _stopped_at_limits(status_or_session):
                    reason = said[:400]
            return RunEnding(status_value, verdict, False, _OUTCOME_WORDS[verdict], reason)
        return RunEnding(status_value, verdict, True, WORD_DONE, "")
    words = _STATUS_WORDS.get(status_value) or (status_value.replace("_", " ").capitalize() or WORD_FAILED)
    reason = _STOPPED_REASON if status_value == "cancelled" else _FAILED_REASON
    return RunEnding(status_value, verdict, False, words, reason)

#: Journal kinds that describe a FILE mutation, and what each one means the
#: tool did. (The inverse is the mirror image: to undo a creation you delete.)
_CREATED_KIND = "file_delete"
#: Multi-file creation envelope (v1.166.0): one row, pre_inline {"paths": [...]}.
_CREATED_MANY_KIND = "files_delete"
_CHANGED_KIND = "file_restore"
#: An ``apply_patch`` call (v1.364.0, IJ harness): one row whose envelope
#: lists the files it created and changed (a deleted file is not a document).
_PATCH_KIND = "files_patch"

#: Cap on each list in the payload. A migration touching 4,000 files must not
#: produce a 4,000-row card (or response); the count stays honest via ``*_total``.
_LIST_CAP = 50

#: Per-error text cap — enough to diagnose, bounded enough for a card.
_ERROR_CHARS = 300


def _tool_row(tool: str, count: int) -> dict[str, Any]:
    """One ``tools_used`` / ``tools_failed`` row. A connected app's tool
    (``mcp__<app>__<tool>``) also carries ``name``, the words the run's
    "Step by step" and the chat footnote use ("Gmail › search messages",
    ``mission.app_tool_display``) — v1.359.0 integrate (lane 9A review): the
    run card's "What it did" said "Used gmail: search messages" (the pack id)
    two lines above "Gmail › search messages". Every other row keeps its exact
    shape ``{tool, count}``; a name that cannot be read is left out."""
    row: dict[str, Any] = {"tool": tool, "count": count}
    if str(tool or "").startswith("mcp__"):
        try:
            from .mission import app_tool_display

            name = app_tool_display(tool)
        except Exception:  # noqa: BLE001 — the dashboard names it by its id
            name = None
        if name:
            row["name"] = name
    return row


def _envelope_path(journal: UndoJournal) -> str:
    """The target path packed into the journal's ``pre_inline`` envelope."""
    try:
        meta = json.loads(journal.pre_inline or "{}")
    except (TypeError, ValueError):
        return ""
    return str(meta.get("path") or "")


def _envelope_paths(journal: UndoJournal) -> list[str]:
    """All target paths in a multi-file ``files_delete`` envelope."""
    try:
        meta = json.loads(journal.pre_inline or "{}")
    except (TypeError, ValueError):
        return []
    paths = meta.get("paths")
    if not isinstance(paths, list):
        return []
    return [str(p) for p in paths if p]


def _patch_paths(journal: UndoJournal) -> tuple[list[str], list[str]]:
    """``(created, changed)`` of an ``apply_patch`` (``files_patch``) row."""
    from ..tools.undo import patch_lists

    try:
        meta = json.loads(journal.pre_inline or "{}")
    except (TypeError, ValueError):
        return [], []
    created, changed, _deleted = patch_lists(meta if isinstance(meta, dict) else {})
    return created, changed


def _rel(path: str, workspace: str) -> str:
    """Workspace-relative when it sits inside one — a card is easier to read as
    ``report.md`` than as a 90-character absolute path. Falls back to the
    absolute path, which is the honest answer for a file outside the workspace
    (and the one the user needs in order to find it)."""
    if not path:
        return ""
    if not workspace:
        return path
    try:
        return str(Path(path).relative_to(Path(workspace)))
    except (ValueError, OSError):
        return path


def _newest_plan(db, session_id: str) -> "tuple[list[Any], list[Any]]":
    """``(plan steps, step results)`` of the session's NEWEST kept plan
    (``RunPlan``, written step by step by ``agents/decompose.execute_plan``),
    as the saved dicts. ``([], [])`` for a run with no plan. Raises when the
    record cannot be read: each caller decides what that costs."""
    from ..core.models import RunPlan

    rows = [
        r for r in db.exec(select(RunPlan).where(RunPlan.session_id == session_id)).all()
        if (r.plan_json or "[]") != "[]"
    ]
    if not rows:
        return [], []
    newest = max(rows, key=lambda r: str(getattr(r, "updated_at", "") or ""))
    plan = json.loads(newest.plan_json or "[]")
    results = json.loads(newest.results_json or "[]")
    return (plan if isinstance(plan, list) else []), (results if isinstance(results, list) else [])


def _plan_steps_failed(db, session_id: str) -> int:
    """How many steps of the session's NEWEST kept plan were attempted and
    failed their check (v1.503.0, north-star 3.3). A step never attempted is
    not counted here: a run that left one ends FAILED itself (the runtime's
    rule). 0 for a run with no plan, or a record that cannot be read."""
    try:
        _plan, results = _newest_plan(db, session_id)
        return sum(
            1 for r in results
            if isinstance(r, dict) and r.get("ok") is False and r.get("attempted", True) is not False
        )
    except Exception:  # noqa: BLE001 — a plan record never costs the result
        return 0


#: A goal on the result card is one line of at most this many characters.
_GOAL_CHARS = 200


def _plan_steps_unchecked(db, session_id: str) -> list[dict[str, Any]]:
    """The steps of the session's NEWEST kept plan that were DONE BUT COULD
    NOT BE CHECKED (the intelligence plan, release 4): the check itself did
    not run, so the step was let through (``decompose.step_unchecked``).
    ``[{"step": 2, "goal": "…"}]`` in plan order (``step`` counts from 1, as
    every screen numbers them). ``[]`` for a run with no plan, none such, or
    a record that cannot be read."""
    try:
        from .decompose import step_unchecked

        plan, results = _newest_plan(db, session_id)
        out: list[dict[str, Any]] = []
        for i, r in enumerate(results):
            if not isinstance(r, dict) or not step_unchecked(r):
                continue
            goal = r.get("goal")
            if not isinstance(goal, str) or not goal.strip():
                step = plan[i] if i < len(plan) and isinstance(plan[i], dict) else {}
                goal = step.get("goal") if isinstance(step.get("goal"), str) else ""
            out.append({"step": i + 1, "goal": " ".join(str(goal).split())[:_GOAL_CHARS]})
        return out
    except Exception:  # noqa: BLE001 — a plan record never costs the result
        return []


def unchecked_words(count: Any) -> str:
    """"1 step could not be checked." / "3 steps could not be checked.", or
    "" for none: the words every reading of a run adds to its result line
    (the result card's headline says the same, ``RunResultCard.
    resultHeadline``). Pure; never raises."""
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n} step{'' if n == 1 else 's'} could not be checked."


def result_line(result: Any) -> str:
    """The first line a person reads about a finished run, from its record:
    the badge word (:func:`run_ending`), and for a run that is "Done" with a
    step nobody could check, "Done. 1 step could not be checked." — never a
    plain "Done" over it (the intelligence plan, release 4). ``result`` is a
    :func:`session_result` payload. Pure; never raises."""
    try:
        if not isinstance(result, dict) or not result.get("found"):
            return ""
        ending = run_ending(result.get("status"), result.get("outcome") or "")
        extra = unchecked_words(result.get("plan_steps_unchecked")) if ending.ok else ""
        return f"{ending.words}. {extra}" if extra else ending.words
    except Exception:  # noqa: BLE001 — wording never breaks a caller
        return ""


def _with_team_documents(db, session_id: str, own: list[str]) -> list[str]:
    """``own`` followed by the documents of every worker in the delegation
    tree under ``session_id``, in team order, without duplicates, capped at
    the list cap (v1.503.0, north-star 3.13). A read that fails keeps
    ``own``: the workers' files are an addition, never the result."""
    try:
        from .mission import _team_documents, _walk

        members, _runs = _walk(db, session_id)
        if not members:
            return own
        by_member = _team_documents(db, {m.id: m for m in members})
        merged = list(own)
        for m in members:
            for p in by_member.get(m.id) or []:
                if p not in merged:
                    merged.append(p)
        return merged[:_LIST_CAP]
    except Exception:  # noqa: BLE001
        return own


def succeeded_tools(engine, session_id: str, run_id: str | None = None) -> list[str]:
    """The tools whose calls SUCCEEDED in run ``run_id`` of the session (every
    run of it when ``run_id`` is empty), one name per call, oldest first —
    the facts a run's closing words are judged on (v1.503.0, north-star 3.7:
    a declined or failed ``write_file`` is not a save). An undo is not the
    run's work. BLOCKING (one indexed read); ``[]`` when it cannot be read,
    which is the direction that checks a claim rather than waves it through."""
    try:
        with session_scope(engine) as db:
            stmt = select(ToolInvocation.tool).where(
                ToolInvocation.session_id == session_id,
                ToolInvocation.ok == True,  # noqa: E712 — SQL comparison
                ToolInvocation.undo_of.is_(None),  # type: ignore[union-attr]
            )
            if run_id:
                stmt = stmt.where(ToolInvocation.agent_run_id == run_id)
            stmt = stmt.order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
            return [str(t or "") for t in db.exec(stmt).all() if t]
    except Exception:  # noqa: BLE001
        return []


#: Newest action lines a receipt carries (v1.351.0, G18).
RECEIPT_ACTIVITY_MAX = 20

#: v1.361.0: the persisted events that prove a run's request went OUT to a
#: model. The router publishes ``provider.routed`` right before it calls an
#: adapter (after every refusal it can make first: not connected, local only,
#: an open circuit, a dead local box), ``provider.failover`` on a
#: substitute's first frame, and the runtime one ``llm.completed`` per
#: answered step.
DISPATCH_EVENTS = (
    EventType.PROVIDER_ROUTED,
    EventType.PROVIDER_FAILOVER,
    EventType.LLM_COMPLETED,
)


def reached_a_model(db, session_id: str, provider: str) -> bool:
    """Whether this session's work was SENT to a model (v1.361.0).

    ``on_this_network`` says where a message went; a run the router refused
    before sending anything (an unconnected cloud provider) went nowhere, and
    "cloud" there tells the user their message left the network when nothing
    did. The AgentRun row is saved before the first model call, so "a run
    exists" is not the signal: the record is — a step a model answered (or its
    tokens), a tool call a model asked for, or the router's own dispatch
    events. The demo model runs in-process and is never refused, so it counts
    as reached. Each read is indexed; any fault answers True, which keeps the
    pre-v1.361.0 judgement rather than hiding a real one."""
    if provider == "mock":
        return True
    try:
        for run in db.exec(select(AgentRun).where(AgentRun.session_id == session_id)):
            if (
                int(getattr(run, "steps", 0) or 0) > 0
                or int(getattr(run, "input_tokens", 0) or 0) > 0
                or int(getattr(run, "output_tokens", 0) or 0) > 0
            ):
                return True
        if db.exec(
            select(ToolInvocation.id).where(ToolInvocation.session_id == session_id).limit(1)
        ).first() is not None:
            return True
        return (
            db.exec(
                select(EventRecord.id)
                .where(EventRecord.session_id == session_id)
                .where(EventRecord.type.in_(DISPATCH_EVENTS))  # type: ignore[attr-defined]
                .limit(1)
            ).first()
            is not None
        )
    except Exception:  # noqa: BLE001 — a receipt line, never the result
        return True


def _fill_receipt(out: dict[str, Any], db, session: Session, manager: Any, config: Any) -> None:
    """The receipt half of :func:`session_result` (v1.351.0, G18), filled in
    place. Each key is its own try — a failed read leaves that key at its
    default and never costs the rest of the result. Lazy imports: ``mission``
    imports this module, and ``egress`` is the providers layer."""
    from .mission import activity_lines, display_name, route_note_for
    from .roster import session_roster_name

    who = display_name(session_roster_name(session) or "")
    try:
        out["route_note"] = route_note_for(db, session.id, who)
    except Exception:  # noqa: BLE001 — a receipt line, never the result
        out["route_note"] = ""
    try:
        provider = out.get("provider") or ""
        # v1.361.0: judged only when the work reached a model — a run the
        # router refused before sending anything says nowhere (None).
        reached = bool(provider) and reached_a_model(db, session.id, provider)
        # Readiness 12A (0f.3): the verdict itself rides the receipt, so the
        # run page can say where a run went that a model's dispatch reached
        # and that then failed before any step, token or tool call (its
        # transcript alone shows nothing, and the page said nothing while
        # this receipt said "cloud"). An older daemon sends no key, and the
        # page then keeps reading its transcript only.
        out["reached_a_model"] = reached
        if reached:
            from ..providers.egress import stays_on_this_network

            out["on_this_network"] = bool(
                stays_on_this_network(provider, manager, config, str(out.get("model") or ""))
            )
    except Exception:  # noqa: BLE001
        out["on_this_network"] = None
    try:
        out["activity"] = activity_lines(
            db, [session.id], {session.id: who}, newest=RECEIPT_ACTIVITY_MAX
        )
    except Exception:  # noqa: BLE001
        out["activity"] = []


def session_result(
    engine,
    session_id: str,
    *,
    receipt: bool = False,
    manager: Any = None,
    config: Any = None,
    team: bool = False,
) -> dict[str, Any]:
    """The honest outcome of one session.

    ``team=True`` (v1.503.0, north-star 3.13): a TEAM job's receipt — the
    files its workers (the delegation tree under this session,
    ``mission._walk``) made are added to ``documents`` after the session's
    own, the mission screen's reading (``mission._team_documents``). The chat
    delivery and a paired computer's reply use it, so a lead that handed
    the writing to a worker never delivers a receipt without that file. The
    verdict and every other key stay this session's own; a solo run reads
    the same as without it.

    Read-only and defensive — this feeds a UI card and an SSE frame, so a
    half-written session or a missing row must degrade to a smaller report, never
    raise. An unknown session id returns ``{"found": False}``.

    v1.351.0 (G18): every result names WHO ran it and ON WHAT — ``provider``
    and ``model`` (the Session row; the orchestrator writes the SERVED pair
    when a run ends), ``agent_name`` (the roster name a door resolved, e.g. a
    custom agent; ``None`` when none was) and ``agent_type``. With
    ``receipt=True`` (``GET /sessions/{id}/result`` — the reads a receipt
    needs, kept off the finalize/coach/compaction callers) it also carries
    ``route_note`` (one sentence when the run's model failed over or was
    refused, else ``""`` — the mission screen's words), ``on_this_network``
    (``egress.stays_on_this_network`` for the run's provider; ``manager`` /
    ``config`` let it read a local endpoint's URL; ``None`` when there is no
    provider to judge, or (v1.361.0) when the work never reached a model —
    :func:`reached_a_model`) and ``activity`` (the newest
    ``RECEIPT_ACTIVITY_MAX`` plain action lines, oldest first, in the mission
    screen's words: ``{at, who, session_id, text, tone}``). Each of the three
    is its own try: a failed read costs that key's value, never the result.
    """
    out: dict[str, Any] = {
        "found": False,
        "session_id": session_id,
        "status": "",
        "task": "",
        "summary": "",
        "steps": 0,
        "tools_used": [],
        "tools_failed": [],
        "files_created": [],
        "files_changed": [],
        "errors": [],
        "revertable": 0,
        "reverted": 0,
        "duration_s": None,
        # v1.227.0 (A5/U1): the verdict on the JOB and the count of asks the
        # clock answered — see ``derive_outcome``.
        "outcome": None,
        "unanswered_asks": 0,
        # v1.298.0: the TRUST posture the run ended under and why — so the
        # card/SSE frame can show a chip. "full" / "" for every row from
        # before the columns existed (additive, never absent).
        "trust": "full",
        "trust_reason": "",
        # v1.351.0 (G18): who ran it, on what (additive, never absent).
        "provider": "",
        "model": "",
        "agent_name": None,
        "agent_type": "",
    }
    if receipt:
        out["route_note"] = ""
        out["on_this_network"] = None
        out["reached_a_model"] = False
        out["activity"] = []
    try:
        with session_scope(engine) as db:
            session = db.get(Session, session_id)
            if session is None:
                return out
            out["found"] = True
            out["status"] = getattr(session.status, "value", str(session.status))
            out["provider"] = str(getattr(session, "provider", "") or "")
            out["model"] = str(getattr(session, "model", "") or "")
            out["agent_name"] = (
                " ".join(str(getattr(session, "agent_name", "") or "").split()) or None
            )
            agent_type = getattr(session, "agent_type", "")
            out["agent_type"] = str(getattr(agent_type, "value", agent_type) or "")
            if receipt:
                _fill_receipt(out, db, session, manager, config)
            out["trust"] = (
                "low" if str(getattr(session, "trust", "") or "").lower() == "low" else "full"
            )
            out["trust_reason"] = (
                str(getattr(session, "trust_reason", "") or "") if out["trust"] == "low" else ""
            )
            out["task"] = (session.task or "")[:400]
            out["summary"] = (session.summary or "")[:2000]
            # Leap 06-a: a rehearsal's run (its own options say so). The key
            # is there only for one, so every other result is unchanged.
            from ..tools.rehearsal import rehearsal_of as _rehearsal_of

            if _rehearsal_of(session):
                out["rehearsal"] = True
            workspace = session.workspace_path or ""
            stored_outcome = getattr(session, "outcome", None) or None
            out["unanswered_asks"] = _count_unanswered_asks(db, session_id)

            runs = list(
                db.exec(select(AgentRun).where(AgentRun.session_id == session_id))
            )
            out["steps"] = sum(int(getattr(r, "steps", 0) or 0) for r in runs)

            invocations = list(
                db.exec(
                    select(ToolInvocation)
                    .where(ToolInvocation.session_id == session_id)
                    .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
                )
            )
            journals = {
                j.action_id: j
                for j in db.exec(
                    select(UndoJournal).where(
                        UndoJournal.action_id.in_(  # type: ignore[union-attr]
                            [i.id for i in invocations] or [""]
                        )
                    )
                )
            }

            used: dict[str, int] = {}
            failed: dict[str, int] = {}
            errors: list[dict[str, str]] = []
            created: list[str] = []
            changed: list[str] = []
            revertable = 0
            already_reverted = 0
            mutating_failed = 0
            outside_limits = 0
            # Leap 05-c review: only a run that HAS Limits can be refused at
            # them; in any other run a failed call whose words merely begin
            # so (an app's own error text is data) is an ordinary failure.
            has_limits = _has_limits(session)
            # v1.465.0: whether the LAST call that changes something failed
            # (None = the run made no such call). A coding helper's verdict
            # reads this: see Orchestrator._settle_finished_run.
            last_change_ok: "bool | None" = None

            rehearsed = 0
            for inv in invocations:
                # An undo is itself a ledger row; counting it as work the agent
                # did would double-report the action it reversed.
                if inv.undo_of:
                    continue
                # Leap 06-a: a rehearsed row is what the run WOULD have done —
                # never a tool it used, a failure, a file made or changed.
                if getattr(inv, "rehearsed", False):
                    rehearsed += 1
                    continue
                name = inv.tool or "(unknown)"
                used[name] = used.get(name, 0) + 1
                if (inv.reversibility or "").lower() != Reversibility.READONLY.value:
                    last_change_ok = bool(inv.ok)
                if not inv.ok:
                    failed[name] = failed.get(name, 0) + 1
                    # A failed READ is a detour; a failed WRITE (or a denied
                    # one) is work the job needed and did not get. The
                    # registry stamps every row with the tool's declared
                    # reversibility, so "not readonly" is the honest line —
                    # an unstamped legacy row counts as mutating (the
                    # direction that never calls a half-done job complete).
                    if (inv.reversibility or "").lower() != Reversibility.READONLY.value:
                        mutating_failed += 1
                    # Leap 05-c: refused at a rule's kept Limits (the
                    # registry's own words begin so): the job needs the user.
                    if has_limits and str(inv.output or "").startswith(_LIMITS_LEAD):
                        outside_limits += 1
                    if len(errors) < _LIST_CAP:
                        errors.append(
                            {"tool": name, "error": (inv.output or "")[:_ERROR_CHARS]}
                        )
                if inv.undone_at is not None:
                    # The action happened AND was later reverted. Both are true,
                    # and the card must say both: the file list still names what
                    # was written (it was), while this count lets the UI add
                    # "already reverted" instead of offering the button again.
                    already_reverted += 1
                journal = journals.get(inv.id)
                if journal is None:
                    continue
                if (
                    journal.reversible
                    and inv.undone_at is None
                    and (inv.reversibility or "").lower()
                    != Reversibility.IRREVERSIBLE.value
                ):
                    revertable += 1
                if journal.kind == _CREATED_MANY_KIND:
                    for p in _envelope_paths(journal):
                        rp = _rel(p, workspace)
                        if rp and rp not in created:
                            created.append(rp)
                    continue
                if journal.kind == _PATCH_KIND:
                    made, edited = _patch_paths(journal)
                    for p in made:
                        rp = _rel(p, workspace)
                        if rp and rp not in created:
                            created.append(rp)
                    for p in edited:
                        rp = _rel(p, workspace)
                        if rp and rp not in changed:
                            changed.append(rp)
                    continue
                path = _rel(_envelope_path(journal), workspace)
                if not path:
                    continue
                if journal.kind == _CREATED_KIND and path not in created:
                    created.append(path)
                elif journal.kind == _CHANGED_KIND and path not in changed:
                    changed.append(path)

            # A file both created AND edited in one session is a CREATION — that
            # is what the user needs to know about it; listing it twice reads as
            # two separate things happening.
            changed = [p for p in changed if p not in created]

            out["tools_used"] = [
                _tool_row(t, n) for t, n in sorted(used.items())
            ][:_LIST_CAP]
            out["tools_failed"] = [
                _tool_row(t, n) for t, n in sorted(failed.items())
            ][:_LIST_CAP]
            out["files_created_total"] = len(created)
            out["files_changed_total"] = len(changed)
            out["files_created"] = created[:_LIST_CAP]
            out["files_changed"] = changed[:_LIST_CAP]
            # ABSOLUTE paths for the same files (v1.155.0), under the key the
            # chat preview already consumes. The lists above are
            # workspace-relative because that is what reads well in a report,
            # but a client cannot open or download a relative path — and an
            # agent session's workspace is a folder no user would guess. Same
            # rule as the tools themselves since v1.153.2: say WHERE, in full.
            out["documents"] = [
                str((Path(workspace) / p).resolve())
                for p in (created + changed)[:_LIST_CAP]
            ] if workspace else []
            if team:
                out["documents"] = _with_team_documents(db, session_id, out["documents"])
            out["errors"] = errors
            if rehearsed:
                # Leap 06-a: how many of its rows say what it WOULD have done.
                out["rehearsed"] = rehearsed
            out["revertable"] = revertable
            out["reverted"] = already_reverted
            out["tools_failed_mutating"] = mutating_failed
            if outside_limits:
                # Leap 05-c: the key is there only for such a run.
                out["outside_limits"] = outside_limits
            out["ended_on_failed_change"] = last_change_ok is False
            # v1.503.0 (north-star 3.3): the steps of the run's kept PLAN
            # that were attempted and failed their check (a decomposed run;
            # 0 for every other) — a fact on the record, so the verdict and
            # every later reading of it agree.
            out["plan_steps_failed"] = _plan_steps_failed(db, session_id)
            # The intelligence plan, release 4: the steps that were done but
            # whose check did not run. A fact on the record too, so the
            # result line never reads as a plain "Done" over them.
            _unchecked = _plan_steps_unchecked(db, session_id)
            out["plan_steps_unchecked"] = len(_unchecked)
            out["unchecked_steps"] = _unchecked[:_LIST_CAP]
            # The stored verdict wins (it was derived at finalize with the
            # status the run actually ended in); a row from before the column
            # existed gets the same derivation live, so an old card is not
            # left blank where a new one would say "needs you".
            out["outcome"] = stored_outcome or derive_outcome(
                out, origin=getattr(session, "origin", None)
            )
            try:
                # ``finished_at`` is only set once the session ends, so a running
                # session honestly reports no duration rather than a growing one.
                if session.created_at and session.finished_at:
                    out["duration_s"] = max(
                        0.0, (session.finished_at - session.created_at).total_seconds()
                    )
            except Exception:  # noqa: BLE001 — a clock oddity costs the duration only
                out["duration_s"] = None
    except Exception:  # noqa: BLE001 — a result card must never break a turn
        return out
    if receipt and (out.get("files_created_total") or out.get("files_changed_total")):
        # The intelligence plan, release 5: WHAT IRON JARVIS CHECKED on the
        # files this run made, read from the one record of checks
        # (``checks/turn.run_payload``: the line, its tone, the list). The
        # RECEIPT read only (the result card and the chat's delivered line):
        # never the finalize, coach or compaction readers, and never the
        # answer posted back to another computer. Present ONLY when the run
        # made or changed a file and the record holds a check of one. The
        # card's own list goes with it: a file on the card that no check of
        # THIS run covers (a team lead's card lists its workers' documents)
        # is said on the line as not checked, never left to read as covered.
        try:
            from ..checks.turn import run_payload

            checked = run_payload(engine, session_id, out.get("documents"))
            if checked is not None:
                out["checked"] = checked
        except Exception:  # noqa: BLE001 — a receipt line, never the result
            pass
    return out


#: How many file names a "kept" line names before "and N more".
KEPT_NAMES_MAX = 5


def _files_note(result: Any, one: str, many: str) -> str:
    try:
        if not isinstance(result, dict):
            return ""
        names: list[str] = []
        for key in ("files_created", "files_changed"):
            for p in result.get(key) or []:
                name = Path(str(p)).name.strip()
                if name and name not in names:
                    names.append(name)
        total = int(result.get("files_created_total") or 0) + int(
            result.get("files_changed_total") or 0
        )
        total = max(total, len(names))
        if not names:
            return ""
        shown = ", ".join(names[:KEPT_NAMES_MAX])
        more = total - min(len(names), KEPT_NAMES_MAX)
        tail = f" (and {more} more)" if more > 0 else ""
        return f" {one if total == 1 else many}: {shown}{tail}."
    except Exception:  # noqa: BLE001 — wording never breaks a finalize
        return ""


def wrote_files_note(result: Any) -> str:
    """" Files it wrote: report.md, q3.xlsx." for a run that FINISHED — the
    phone's reply names its files (readiness 0j.5 item 18). Names only."""
    return _files_note(result, "The file it wrote", "Files it wrote")


def kept_files_note(result: Any) -> str:
    """Readiness 0j.5 item 17: what a run that did NOT finish leaves behind,
    said in its own words — " The files it wrote are kept: report.md,
    notes.txt (and 2 more)." — read off the undo journal's record of the run
    (``files_created`` + ``files_changed``, which :func:`session_result`
    derives; a failed tool's writes are never counted). ``""`` when the run
    wrote nothing, or ``result`` is not a ledger card. Leading space: it is
    appended to a summary. Names only (``Path.name``); never raises."""
    return _files_note(result, "The file it wrote is kept", "The files it wrote are kept")


def did_nothing(result: dict[str, Any]) -> bool:
    """True when a session completed without running a single tool.

    Worth calling out explicitly rather than rendering an empty card: "completed"
    with no actions is exactly the shape of an agent that DESCRIBED the work
    instead of doing it, which is the report this whole wave answers.
    """
    return bool(result.get("found")) and not result.get("tools_used")
