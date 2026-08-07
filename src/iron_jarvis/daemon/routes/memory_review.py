"""Memory housekeeping routes (v1.143.0) — the steward's review lane.

The steward ADDS memory on its own (append-only, undoable). Everything it wants
to CHANGE — dedupe, stale, contradiction, merge — comes here as a suggestion and
waits for a click. These four routes serve the Memory page's "Memory
housekeeping" card:

``GET  /memory/review``                  — pending suggestions + steward status.
``POST /memory/review/{id}/approve``     — apply ONE suggestion (and only then).
``POST /memory/review/{id}/dismiss``     — "not this"; the signature is suppressed.
``POST /memory/review/run``              — review now (honest 400 without a model).
``POST /memory/review/reset``            — re-read history from the beginning.

Deliberately the same shapes as ``routes/skill_learning.py``: proposals come
back FLAT carrying ``status``, an unknown id is 404, an already-decided one is
409 (a double-click must not read as "it vanished"), and the manual run is an
honest 400 under the offline mock rather than a fabricated review.

REGISTRATION ORDER: this module registers BEFORE routes/learning.py, which owns
``GET /memory/{layer}/{key}``. Starlette would still resolve these (the method
and segment counts differ), but "a literal path registered after a catch-all in
the same prefix" is exactly the shape that cost us ``/skills/learning`` once —
so it is ordered, and pinned by a test.

Closure-local state is reached through ``d`` (the create_app deps object).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ...core.logging import get_logger
from ...templates import MEMORY_REVIEW_SCHEDULE, MEMORY_REVIEW_TASK

log = get_logger("memory_review")

#: How many recent steward runs the status line may show.
_MAX_RUNS = 5

#: How far back the ledger reconciliation looks for review sessions nothing
#: recorded (see ``_reconcile_unrecorded_reviews``). Bounded on purpose: this
#: runs on a GET, and a weekly job cannot outrun 50 rows between two visits.
_RECONCILE_SCAN = 50

#: Replacement text a proposal carries can be a whole note (the store caps it at
#: 200 KB). The review card only needs enough to preview, and shipping every
#: proposal's full body on one overview call is how a review queue turns into a
#: multi-megabyte response.
_PAYLOAD_TEXT_PREVIEW = 4000

#: The tool an agent session must CALL to file a housekeeping suggestion, and
#: the trusted instruction block that names it.
#:
#: HISTORY, because the shape below only makes sense with it: the steward's own
#: prompt used to end its housekeeping step with "describe it in your final
#: report. Do NOT act on it" — written before this tool existed, and true of
#: DELETING but not of FILING. A session that only writes prose files nothing,
#: ``proposals_raised`` stays 0 forever, and the whole review queue is inert. So
#: this route appended the missing sentence AFTER the steward's untrusted-content
#: fence (our own trusted text, never inside it) and only when the task did not
#: already name the tool — a bridge designed to stop firing by itself the day
#: ``TASK_PREAMBLE`` absorbed it.
#:
#: v1.143.0 IS that day: ``memory/steward.py``'s step 4 now names the tool, so
#: :func:`with_filing_instructions` is a no-op on every prompt the steward
#: builds (pinned by a test). It STAYS because a schedule can carry a custom or
#: older task string — one the steward never composed — and that prompt still
#: needs to be told how to file. The name is resolved FROM the steward so the
#: self-disable can never drift out of sync with the prompt that disables it.
try:  # pragma: no cover - the fallback only fires on a broken memory package
    from ...memory.steward import PROPOSE_TOOL
except Exception:  # noqa: BLE001
    PROPOSE_TOOL = "memory_propose"

FILING_INSTRUCTIONS = (
    "## Filing housekeeping\n\n"
    "When you notice housekeeping — the same fact in two notes, a note the "
    f"facts have moved past, two notes that disagree, notes that want to be one "
    f"— call `{PROPOSE_TOOL}` once per suggestion. That is the ONLY way the user "
    "ever sees it: a suggestion written in your report and not filed with the "
    "tool is lost. Filing changes nothing by itself — each suggestion waits on "
    "the Memory page for the user's approval, which is also why you must still "
    "never delete or rewrite a note yourself."
)


def with_filing_instructions(task: str) -> str:
    """Ensure a review prompt tells the session HOW to file a suggestion.

    SELF-DISABLING: a prompt that already names :data:`PROPOSE_TOOL` is returned
    untouched. Since v1.143.0 the steward's own ``TASK_PREAMBLE`` names it, so
    every prompt this daemon composes takes that branch and nothing is appended
    — the belt-and-braces only fires for a custom/older task string.
    """
    text = str(task or "")
    if not text.strip() or PROPOSE_TOOL in text:
        return text
    return text.rstrip() + "\n\n" + FILING_INSTRUCTIONS + "\n"


def _empty_note(plan: dict[str, Any]) -> str:
    """Why nothing ran, in the user's words.

    The steward's ``reason`` is written for a log line ("no unreviewed
    conversations"); this turns the ones a user can act on into a sentence and
    passes anything unexpected through rather than inventing a cause.
    """
    reason = str(plan.get("reason") or "").strip().lower()
    if plan.get("enabled") is False or "disabled" in reason:
        return "Memory review is switched off, so nothing ran."
    if "unavailable in this build" in reason:
        return (
            "This build can't search your conversation history, so there is "
            "nothing to review from."
        )
    if not reason or "no unreviewed" in reason:
        return (
            "Nothing new to review — there are no conversations since the last "
            "memory review."
        )
    return f"Nothing to review right now — {reason}."


def _decision_error(exc: ValueError) -> HTTPException:
    """Engine ``ValueError`` -> HTTP: unknown proposal 404, already-decided 409."""
    detail = str(exc)
    status = 404 if "no such proposal" in detail else 409
    return HTTPException(status_code=status, detail=detail)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _store():
        """The shared proposal store (built in create_app), or a fresh one.

        The fallback keeps this module green on wiring that predates the ``d``
        field — the store is stateless apart from the engine.
        """
        store = getattr(d, "memory_proposals", None)
        if store is not None:
            return store
        try:
            from ...memory.proposals import MemoryProposalStore
        except ImportError as exc:  # pragma: no cover — module always ships
            raise HTTPException(
                status_code=503, detail=f"memory review unavailable: {exc}"
            )
        store = MemoryProposalStore(
            d.platform.engine,
            ltm=getattr(d.platform, "ltm", None),
            home=getattr(getattr(d.platform, "config", None), "home", None),
        )
        d.memory_proposals = store
        return store

    def _steward():
        """The MemorySteward if this build has one, else None.

        Pair M1 owns ``memory/steward.py``; import it lazily and defensively so
        this lane is green whichever half lands first, and so a steward that
        raises on construction degrades to "no status line" instead of a 500.
        """
        cached = getattr(d, "_memory_steward_cache", "unset")
        if cached != "unset":
            return cached
        steward = getattr(d.platform, "memory_steward", None)
        if steward is None:
            try:
                from ...memory.steward import MemorySteward  # type: ignore
            except Exception:  # noqa: BLE001 — not landed yet / import error
                steward = None
            else:
                try:
                    steward = MemorySteward(d.platform)
                except Exception:  # noqa: BLE001
                    steward = None
        d._memory_steward_cache = steward
        return steward

    def _reconcile_unrecorded_reviews(steward) -> None:
        """Record review sessions that finished OUTSIDE this route's own wrapper.

        ``POST /memory/review/run`` closes its own loop, and as of v1.143.0 so
        does the WEEKLY SCHEDULE: ``platform._dispatch_scheduled`` recognises the
        memory-review schedule by NAME, builds the fire's prompt from
        ``steward.plan()`` and calls ``record_run`` itself. This reconciliation
        is therefore no longer the primary path — it is the SAFETY NET for the
        fires that lane cannot record: a schedule whose ``plan()`` failed (the
        dispatcher then falls back to the durable template prompt and records
        nothing rather than risk the scheduler thread), a fire from a build that
        predates the wiring, or a session the recorder could not reach.

        Rather than reach into the dispatcher a second time, the ledger is
        reconciled from the sessions themselves: any finished session whose
        ORIGIN is a memory review and which has no run row yet gets one, with
        the same READ counts the other two lanes use (``memory/steward.py``'s
        ``count_notes_added`` / ``count_proposals_raised`` — one implementation,
        three callers). ``record_run`` is idempotent per session id, so this can
        only ever add what is missing.

        NO DOUBLE COUNTING, by two independent guards: a session the schedule
        already recorded appears in ``known`` here and is skipped outright, and
        even if it did not, ``record_run`` refuses a second successful row for a
        session id it has already seen. Pinned by
        ``tests/test_steward_schedule.py`` (fire the schedule, then refresh the
        card: exactly ONE run row, one cursor advance).

        Deliberately NOT advancing the review point: a fire that reaches THIS
        function carried the durable template prompt, not a windowed one, so it
        covered no cursor — and inventing a watermark for it would skip history
        nothing had read. (``record_run(ok=True, cursor="")`` carries the
        existing watermark forward rather than blanking it.)

        Scoped to the SCHEDULE origin, and deliberately not to ``memory-review``
        (the manual lane): that lane records itself from the background wrapper,
        a fraction of a second after the session row flips to completed. A card
        refreshed inside that window would otherwise reconcile the run FIRST,
        with no cursor, and ``record_run``'s per-session idempotence would then
        drop the wrapper's real one — a rare refresh silently costing the review
        point it had just earned.

        Bounded, never raises, and silent when there is nothing to do.
        """
        recorder = getattr(steward, "record_run", None)
        lister = getattr(steward, "runs", None)
        if not callable(recorder) or not callable(lister):
            return
        try:
            from sqlmodel import select

            from ...core.db import session_scope
            from ...core.models import Session

            known = {
                str(r.get("session_id") or "")
                for r in (lister(_RECONCILE_SCAN) or [])
                if isinstance(r, dict)
            }
            origin = f"schedule:{MEMORY_REVIEW_SCHEDULE['name']}"
            with session_scope(d.platform.engine) as db:
                rows = list(
                    db.exec(
                        select(Session)
                        .where(Session.origin == origin)
                        .order_by(Session.created_at.desc())
                        .limit(_RECONCILE_SCAN)
                    )
                )
            for row in rows:
                status = getattr(row.status, "value", str(row.status))
                if status == "active" or row.id in known:
                    continue
                recorder(
                    ok=status == "completed",
                    session_id=row.id,
                    notes_added=_count_notes(row.id),
                    proposals_raised=_count_proposals(row.id),
                    outcome=(row.summary or "").strip()[:400] or f"session {status}",
                )
        except Exception:  # noqa: BLE001 — bookkeeping must never break the card
            log.warning("could not reconcile scheduled memory reviews", exc_info=True)

    def _steward_view() -> dict[str, Any]:
        """Last-run / notes-added status, best-effort. Never raises."""
        steward = _steward()
        if steward is None:
            return {"available": False, "stats": {}, "runs": []}
        _reconcile_unrecorded_reviews(steward)
        stats: dict[str, Any] = {}
        runs: list[Any] = []
        getter = getattr(steward, "stats", None)
        if callable(getter):
            try:
                value = getter()
                if isinstance(value, dict):
                    stats = value
            except Exception:  # noqa: BLE001 — a status line must never 500 the card
                stats = {}
        lister = getattr(steward, "runs", None)
        if callable(lister):
            try:
                value = lister()
                runs = [_jsonable(r) for r in list(value or [])[:_MAX_RUNS]]
            except Exception:  # noqa: BLE001
                runs = []
        return {"available": True, "stats": stats, "runs": runs}

    def _proposal_view(p) -> dict[str, Any]:
        """One suggestion, FLAT, with the honesty fields the card needs.

        ``can_apply`` / ``undoable`` / ``base_note`` come from the LIVE base, so
        the UI can say "this base can't be edited from here" BEFORE the user
        clicks, and can promise undo only where undo is real.
        """
        store = _store()
        try:
            capability = store.describe_base(p.base)
        except Exception:  # noqa: BLE001
            capability = {"can_apply": False, "undoable": False, "note": ""}
        payload = p.decoded_payload()
        full_text = str(payload.get("text") or "")
        if len(full_text) > _PAYLOAD_TEXT_PREVIEW:
            payload = {
                **payload,
                "text": full_text[:_PAYLOAD_TEXT_PREVIEW],
                "text_truncated": True,
                "text_length": len(full_text),
            }
        applied = p.decoded_applied()
        return {
            "id": p.id,
            "kind": p.kind,
            "base": p.base,
            "refs": p.decoded_refs(),
            "rationale": p.rationale,
            "suggested_action": p.suggested_action,
            "payload": payload,
            "status": p.status,
            "run_id": p.run_id,
            "applied": applied,
            # True when an earlier approve got PART-WAY and then failed: some
            # notes already changed while the suggestion is still pending. The
            # card has to say so — "nothing has changed yet" would be a lie.
            "partial": bool(applied.get("partial")) and p.status == "pending",
            "can_apply": bool(capability.get("can_apply")),
            "undoable": bool(capability.get("undoable")),
            "base_note": str(capability.get("note") or ""),
            # The CONCRETE effect, taken from the payload rather than from the
            # model's prose: the card shows exactly which notes disappear, so an
            # approval is informed by what will happen and not by what the
            # suggestion says will happen.
            "rewrites": bool(str(payload.get("text") or "").strip()),
            "survivor_ref": str(payload.get("survivor_ref") or ""),
            "remove_refs": [str(r) for r in (payload.get("remove_refs") or [])],
            "removes": len(payload.get("remove_refs") or []),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        }

    def _template_view() -> dict[str, Any]:
        """The opt-in weekly schedule, plus whether it is already installed.

        Nothing here CREATES the schedule — the card POSTs /schedules with this
        payload when the user clicks. A boot never installs it.
        """
        installed = False
        try:
            scheduler = getattr(d.platform, "scheduler", None)
            if scheduler is not None:
                installed = any(
                    t.name == MEMORY_REVIEW_SCHEDULE["name"] for t in scheduler.list()
                )
        except Exception:  # noqa: BLE001 — an unreadable scheduler just means "offer it"
            installed = False
        return {**MEMORY_REVIEW_SCHEDULE, "installed": installed}

    def _real_model_available() -> bool:
        """True when a REAL provider can back a review (the crystallize rule).

        Same gate ``_skill_distill_complete`` applies: the configured default,
        else a cross-provider failover, and never the offline mock — a review
        invented by the mock would write fake facts into long-term memory,
        which is the single worst thing this feature could do.
        """
        from ...providers.adapters.mock import MockLLMAdapter

        config = d.platform.config
        try:
            adapter = d.platform.providers.get(
                config.default_provider, config.default_model
            )
        except Exception:  # noqa: BLE001 — fall through to failover
            adapter = None
        if adapter is not None and not isinstance(adapter, MockLLMAdapter):
            return True
        failover = getattr(d, "_failover_adapter", None)
        if failover is None:  # pragma: no cover — always on the deps object
            return False
        try:
            alternative, _provider = failover("mock")
        except Exception:  # noqa: BLE001
            return False
        return alternative is not None

    # -- the review run (helpers for POST /memory/review/run) ---------------

    def _plan(steward) -> "dict[str, Any] | None":
        """The steward's one-call review plan, or None when it can't be had.

        ``plan()`` is the sanctioned entry point (window + prompt + the exact
        bookkeeping fields ``record_run`` wants). Older/partial stewards fall
        back to ``window()``/``unreviewed()`` + ``build_task``; a steward that
        raises falls all the way through to the durable template prompt.
        """
        if steward is None:
            return None
        planner = getattr(steward, "plan", None)
        if callable(planner):
            try:
                result = planner()
                if isinstance(result, dict):
                    return result
            except Exception:  # noqa: BLE001
                return None
        builder = getattr(steward, "build_task", None)
        window_fn = getattr(steward, "window", None) or getattr(
            steward, "unreviewed", None
        )
        if not callable(builder):
            return None
        try:
            window = window_fn() if callable(window_fn) else []
            built = builder(window)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(built, str):
            return None
        return {"task": built.strip(), "refs": [], "conversations": 0, "docs": 0}

    async def _run_and_record(orchestrator, steward, plan, session_id: str) -> None:
        """Run the review session, then record the run. NEVER raises.

        Counting is READ, never estimated: notes added = the session's own
        successful ``ltm_append`` calls; proposals raised = rows this session
        actually filed. A failed session records a failed run, which by
        construction cannot advance the review cursor.
        """
        ok = False
        outcome = ""
        try:
            done = await orchestrator.run_session(session_id)
            status = getattr(
                getattr(done, "status", ""), "value", str(getattr(done, "status", ""))
            )
            ok = status == "completed"
            outcome = (getattr(done, "summary", "") or "").strip() or f"session {status}"
        except Exception as exc:  # noqa: BLE001 — recorded, not raised
            outcome = f"{type(exc).__name__}: {exc}"
        recorder = getattr(steward, "record_run", None) if steward is not None else None
        if not callable(recorder):
            return
        try:
            recorder(
                ok=ok,
                cursor=str((plan or {}).get("cursor") or ""),
                since=str((plan or {}).get("since") or ""),
                conversations=int((plan or {}).get("conversations") or 0),
                docs=int((plan or {}).get("docs") or 0),
                notes_added=_count_notes(session_id),
                proposals_raised=_count_proposals(session_id),
                outcome=outcome[:400],
                session_id=session_id,
                refs=list((plan or {}).get("refs") or []),
            )
        except Exception:  # noqa: BLE001 — bookkeeping must not break a run
            pass

    def _count_notes(session_id: str) -> int:
        """Successful ``ltm_append`` calls this session made (the ADD lane).

        The implementation MOVED to ``memory/steward.py`` in v1.143.0, when the
        weekly schedule started recording its own run: two copies of "what
        counts as a note this review added" is exactly how the review card and
        the schedule lane would drift into reporting different numbers for the
        same session. Delegated rather than duplicated; still never raises.
        """
        try:
            from ...memory.steward import count_notes_added

            return count_notes_added(d.platform.engine, session_id)
        except Exception:  # noqa: BLE001 — a steward-less build still counts 0
            return 0

    def _count_proposals(session_id: str) -> int:
        """Housekeeping suggestions this session filed (the SUGGEST lane).

        Same move, same reason as :func:`_count_notes`.
        """
        try:
            from ...memory.steward import count_proposals_raised

            return count_proposals_raised(d.platform.engine, session_id)
        except Exception:  # noqa: BLE001
            return 0

    @app.get("/memory/review")
    def memory_review_overview() -> dict[str, Any]:
        """Everything the Memory page's housekeeping card reads in one call.

        Suggestions carry ``status`` (the card filters pending itself), the
        steward block is the compact status line, and ``template`` is the
        opt-in weekly schedule with an ``installed`` flag.
        """
        store = _store()
        proposals = [_proposal_view(p) for p in store.list()]
        return {
            "proposals": proposals,
            "pending": sum(1 for p in proposals if p["status"] == "pending"),
            "stats": store.stats(),
            "steward": _steward_view(),
            "template": _template_view(),
        }

    @app.post("/memory/review/run")
    async def run_memory_review_now() -> dict[str, Any]:
        """Review NOW — opens a real agent session (the v1.119 schedule path).

        With only the offline mock available this is an honest 400, never a
        fabricated review. The session is spawned in the background and the
        route returns its id immediately (POST /workflows/run's convention).
        """
        if not _real_model_available():
            raise HTTPException(
                status_code=400,
                detail=(
                    "connect a model on the Connections page to run a memory "
                    "review — reading your conversations needs a real model"
                ),
            )
        orchestrator = getattr(d, "orchestrator", None)
        if orchestrator is None:  # pragma: no cover — always built by create_app
            raise HTTPException(
                status_code=503, detail="the agent orchestrator is unavailable"
            )
        steward = _steward()
        plan = _plan(steward)
        if plan is not None and not str(plan.get("task") or "").strip():
            # The steward's own contract: an EMPTY window must not fire a
            # session. Asking a model to curate nothing is how memory fills
            # with invented facts — so say so instead.
            return {
                "started": False,
                "session_id": "",
                "task": "",
                "note": _empty_note(plan),
                "steward": True,
            }
        task = with_filing_instructions(
            str((plan or {}).get("task") or "").strip() or MEMORY_REVIEW_TASK
        )

        try:
            session = await orchestrator.create_session(task, origin="memory-review")
        except (PermissionError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Background, like POST /workflows/run: a review reads a lot of history
        # and would otherwise hold the request open for minutes. The wrapper
        # closes the steward's bookkeeping loop — the review CURSOR advances
        # only when the session actually completed (record_run enforces that).
        d._spawn_bg(
            session.id, _run_and_record(orchestrator, steward, plan, session.id)
        )
        return {
            "started": True,
            "session_id": session.id,
            "task": task,
            "steward": steward is not None,
        }

    @app.post("/memory/review/reset")
    def reset_memory_review_point() -> dict[str, Any]:
        """Re-read history from the beginning on the next review.

        The escape hatch for the steward's ONE stated limitation (its DECISION 2):
        the review point is a watermark on a conversation's timestamp, so a
        conversation that HAPPENED earlier but was INDEXED later — every thread
        the boot backfill picks up — sits behind the watermark and is never
        offered. ``stats()["cursor_note"]`` says that in the user's words; this
        is the button next to it. Nothing is deleted: the reset is itself
        recorded as a run, and re-reviewing only ever re-reads.

        Declared ahead of the ``{proposal_id}`` routes out of habit rather than
        need: those carry a fourth segment so nothing can shadow this today, but
        the day someone adds ``POST /memory/review/{id}`` a literal registered
        afterwards would start resolving as a proposal called "reset". Pinned by
        a test either way.
        """
        steward = _steward()
        resetter = getattr(steward, "reset_cursor", None) if steward is not None else None
        if not callable(resetter):
            raise HTTPException(
                status_code=503,
                detail="this build has no memory steward, so there is no review point to reset",
            )
        try:
            run = resetter("", note="review point reset from the Memory page")
        except Exception as exc:  # noqa: BLE001 — never 500 a housekeeping click
            raise HTTPException(status_code=500, detail=f"could not reset: {exc}")
        return {
            "ok": bool((run or {}).get("recorded")),
            "cursor": "",
            "note": (
                "The next review starts from the beginning of your history. "
                "Nothing was deleted — facts already saved stay saved."
            ),
            "steward": _steward_view(),
        }

    @app.post("/memory/review/{proposal_id}/approve")
    def approve_memory_proposal(proposal_id: str) -> dict[str, Any]:
        """Apply ONE suggestion — the only thing that ever changes a note.

        A base this daemon cannot rewrite (Notion, an MCP brain, a cloud base)
        answers 409 with the reason and leaves the suggestion pending: an
        honest refusal, never a silent "approved" that changed nothing.
        """
        try:
            record, result = _store().approve(proposal_id)
        except ValueError as exc:
            raise _decision_error(exc)
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.error or "could not apply")
        return {**_proposal_view(record), "applied": result.to_dict()}

    @app.post("/memory/review/{proposal_id}/dismiss")
    def dismiss_memory_proposal(proposal_id: str) -> dict[str, Any]:
        """Dismiss a suggestion. Its signature is suppressed, so the same
        suggestion never comes back — "not this" sticks."""
        try:
            record = _store().dismiss(proposal_id)
        except ValueError as exc:
            raise _decision_error(exc)
        return _proposal_view(record)


def _jsonable(value: Any) -> Any:
    """A steward run row as plain JSON (it may be a SQLModel, a dict, or text).

    Pair M1 picks the shape; this survives all of them rather than pinning one.
    Nested lists/dicts are walked rather than ``str()``-ed — a run's ``refs``
    list came back as the literal string ``"[]"`` before this, which every
    consumer would have had to parse back out of JSON it was already inside.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001
            try:
                return {str(k): _jsonable(v) for k, v in dump().items()}
            except Exception:  # noqa: BLE001
                return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)
