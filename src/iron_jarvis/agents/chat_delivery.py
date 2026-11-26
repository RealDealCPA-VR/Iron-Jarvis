"""An agent session's reply comes home to the chat it was asked in
(v1.345.0, lane T — "an @ reply comes home").

THE BUG this exists for (the user's install, 2026-10-16): a hand-off the chat
page opened (``POST /sessions`` / ``/agents/{slug}/spawn`` /
``/sessions/{id}/continue``) was finished by the PAGE: ``finalize()`` read
the session, composed the assistant line and ``PUT`` the whole array over
whatever chat was open. Leave for another chat while the run worked and the
reply either landed in the wrong conversation or never appeared at all.

Now every door the page opens from chat carries ``chat_thread_id`` (stored on
the row as ``options["chat_thread_id"]``), and EVERY terminal path of such a
session — ``run_session``'s success, ``_finalize_failed``,
``_finalize_cancelled`` and the boot reconcile's interrupted outcome —
appends ONE line to that chat through the daemon's one writer
(``daemon.chat_append.append_to_chat_thread``), idempotent by ``sessionId``.
The page then RE-READS the thread and renders what the daemon wrote (its
composing code stays as the fallback for an older daemon).

This module is the PURE half: it says which chat a session belongs to, who
the line is attributed to, and what the line looks like — MIRRORING the
page's own ``finalize()`` (dashboard/app/chat/page.tsx) exactly, so the
integrator can pin that a daemon-appended line and a page-composed line are
equal for the same session:

* ``content`` — the page's words: on ``completed`` the summary (trimmed) or
  ``"(no response)"``; otherwise the summary or (v1.352.0, G16) the plain
  sentence for HOW it ended — :data:`STOPPED_SENTENCE` for a run the user
  stopped (``cancelled``), :data:`FAILED_SENTENCE` for any other end;
* ``sessionId`` — the page's ``fromSession`` under the daemon's key (the
  page's ``lib/chatReread.adoptDaemonLines`` stands one in for the other);
* ``receipt`` — the page's ``runResult``: the ``GET /sessions/{id}/result``
  payload (``agents/outcome.session_result``), same adoption rule. Since
  v1.352.0 (G19) it is that route's RECEIPT read (``receipt=True``: the
  route note and the plain action lines ride along), so the card and the
  settled helper-agent row say the same whichever of the two wrote the
  line; ``on_this_network`` is kept only when the caller passed the
  provider manager it needs (judged without the endpoint settings a local
  model would read as cloud — absent beats wrong);
* ``panelWho`` — the participant key the page stores on a hand-off reply
  (``awaitingWhoRef``): ``builtin:<agent type>`` for a builtin,
  ``dynamic:<name>`` for a custom agent (the page's ``sessionTargetOf`` maps
  ``dynamic:`` back to ``custom:``), ``remote:<name>`` for a remote;
* ``agentType`` and ``documents`` (the result's absolute paths, only when
  there are any) — additive, read by nothing the page composes but kept so
  the line says where the files are without a second read.

The blocking half (``deliver_session_line``) reads the ledger and appends;
callers hop it off the loop (``asyncio.to_thread``) — the finalizers already
run their DB work there. It never raises: a delivery miss is logged, the run
is the point.
"""

from __future__ import annotations

import threading
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

#: The run option the chat thread id rides under (``Session.options_json``).
CHAT_THREAD_OPTION = "chat_thread_id"

#: The page's fallback words when a run the user STOPPED (``cancelled``)
#: left no summary (v1.352.0, G16: a stop is not a failure, so it no longer
#: reads like one — the page's ``finalize()`` says the same, pinned).
STOPPED_SENTENCE = "The agent was stopped before it finished."
#: The same, for a run stopped while it still waited in the queue (v1.359.0,
#: wave 9: the orchestrator's summary for that stop; it read "Cancelled while
#: queued (never started).").
STOPPED_BEFORE_START_SENTENCE = "The agent was stopped before it started."

#: The page's fallback words when a run that FAILED (or ended any other way
#: short of ``completed``) left no summary (v1.352.0, G16).
FAILED_SENTENCE = "The agent could not finish this. Try again or open the run."

#: The page's fallback words for a completed run with no summary.
NO_RESPONSE = "(no response)"


def clean_chat_thread_id(raw: object) -> str:
    """One trimmed id or "" — never a non-string, never whitespace."""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def chat_thread_of(session: Any) -> str:
    """The chat thread a session was asked in (``options.chat_thread_id``),
    or "" for a run no chat is waiting on. Reads the row's options through
    the ONE reader (``scheduling.knobs.session_options``); junk reads "".
    """
    try:
        from ..scheduling.knobs import session_options

        options = session_options(session)
    except Exception:  # noqa: BLE001 — a junk column is "no chat", never a crash
        return ""
    if not isinstance(options, dict):
        return ""
    return clean_chat_thread_id(options.get(CHAT_THREAD_OPTION))


def participant_key_for(session: Any) -> str:
    """The participant key the chat page attributes a hand-off reply to —
    the same ``<source>:<name>`` ``POST /chat/panel`` answers as ``who`` for
    ``mode: "session"`` (``components/agents/identity.participantKey``).

    A session credited to a custom agent (``agent_name`` ``custom:<slug>``)
    is ``dynamic:<slug>``; a remote stays ``remote:<name>``; everything else
    is the builtin ``builtin:<agent type>``.
    """
    name = str(getattr(session, "agent_name", "") or "").strip()
    low = name.lower()
    if low.startswith("custom:"):
        slug = name[len("custom:"):].strip()
        if slug:
            return f"dynamic:{slug}"
    if low.startswith("remote:"):
        remote = name[len("remote:"):].strip()
        if remote:
            return f"remote:{remote}"
    agent_type = getattr(session, "agent_type", None)
    type_value = str(getattr(agent_type, "value", agent_type) or "").strip()
    return f"builtin:{type_value or 'builder'}"


def _status_value(session: Any) -> str:
    status = getattr(session, "status", None)
    return str(getattr(status, "value", status) or "").strip().lower()


def content_for_session(session: Any) -> str:
    """The reply text the page's ``finalize()`` shows for this session."""
    status = _status_value(session)
    summary = str(getattr(session, "summary", "") or "").strip()
    if status == "completed":
        return summary or NO_RESPONSE
    if status == "cancelled":
        return summary or STOPPED_SENTENCE
    return summary or FAILED_SENTENCE


def line_for_session(session: Any, result: Any, who: str, *, now: Any = None) -> dict[str, Any]:
    """The ONE chat line a finished session leaves in the chat it was asked
    in — the page's ``finalize()`` message shape under the daemon's keys
    (see the module doc). ``result`` is ``session_result``'s dict (or None /
    a non-dict, when the ledger could not be read — then no receipt and no
    documents, exactly as the page composes when ``/result`` fails).

    Idea 39 (39-a): a run its SOURCE set up from a chat (a schedule's fire)
    carries ``options.report_from``; its line is then that source's REPORT
    (:func:`report_line_for_session`), never an agent's reply.
    """
    source = report_from_of(session)
    if source is not None:
        return report_line_for_session(session, result, source, now=now)
    line: dict[str, Any] = {
        "role": "assistant",
        "content": content_for_session(session),
        "sessionId": str(getattr(session, "id", "") or ""),
    }
    agent_type = getattr(session, "agent_type", None)
    type_value = str(getattr(agent_type, "value", agent_type) or "").strip()
    if type_value:
        line["agentType"] = type_value
    who = str(who or "").strip()
    if who:
        line["panelWho"] = who
    if isinstance(result, dict) and result.get("found"):
        line["receipt"] = result
        docs = [str(p) for p in (result.get("documents") or []) if p]
        if docs:
            line["documents"] = docs
    return line


def delivery_result(
    engine: Any, session_id: str, *, manager: Any = None, config: Any = None
) -> dict[str, Any]:
    """The ledger card a delivered line carries (v1.352.0, G19): the RECEIPT
    read ``GET /sessions/{id}/result`` answers (``receipt=True``), so the
    line's card names the route note and the plain action lines the page's
    own read would have. ``on_this_network`` needs the provider ``manager``
    to read a local endpoint's address; without one it is DROPPED (a local
    model judged with no settings reads as cloud — absent beats wrong).
    BLOCKING. Raises what ``session_result`` raises (the caller catches)."""
    from .outcome import session_result

    # v1.503.0 (north-star 3.13): a team job's receipt carries the files its
    # workers made (``team=True``); a solo run reads exactly as before.
    result = session_result(
        engine, session_id, receipt=True, manager=manager, config=config, team=True
    )
    if manager is None and isinstance(result, dict):
        result.pop("on_this_network", None)
    return result


def low_trust_receipt(engine: Any, session_ids: Any) -> "dict[str, Any] | None":
    """The receipt a report of SEVERAL runs carries (idea 39, 39-a, third
    review: a workflow's steps): ``{"found": True, "trust": "low",
    "trust_reason": ...}`` when any of ``session_ids`` ran at low trust (it
    read flagged content, or an outside signal started it), else None. The
    recap reads it (``workmap.recap._low_trust``), so a report whose words may
    carry such content keeps the chat out of the recap pass. It names no
    ``session_id``, so the page draws no run card for it
    (``chatReread.looksLikeRunResult``). BLOCKING (SQLite); never raises."""
    ids = [str(s).strip() for s in (session_ids or []) if isinstance(s, str) and s.strip()]
    if not ids:
        return None
    try:
        from ..core.db import session_scope
        from ..core.models import Session

        with session_scope(engine) as db:
            for sid in dict.fromkeys(ids):
                row = db.get(Session, sid)
                if row is not None and str(getattr(row, "trust", "") or "").lower() == "low":
                    reason = str(getattr(row, "trust_reason", "") or "").strip()
                    return {"found": True, "trust": "low", "trust_reason": reason}
    except Exception:  # noqa: BLE001 — an unreadable run is treated as low
        log.warning("could not read the trust of runs %s", ids[:5], exc_info=True)
        return {"found": True, "trust": "low", "trust_reason": ""}
    return None


def deliver_session_line(
    engine: Any,
    session: Any,
    *,
    event_bus: Any = None,
    manager: Any = None,
    config: Any = None,
) -> dict[str, Any]:
    """Append the session's line to the chat it was asked in. BLOCKING
    (SQLite reads + the append) — call it off the loop. Returns
    ``append_to_chat_thread``'s answer, or ``{"ok": False, "why": ...}``
    when the session carries no chat or the delivery faulted. Never raises.
    ``manager`` / ``config`` (optional) let the receipt judge whether the
    run's model stayed on this network (:func:`delivery_result`).
    """
    thread_id = chat_thread_of(session)
    if not thread_id:
        return {"ok": False, "updated_at": None, "why": "no chat", "appended": 0, "skipped": 0}
    source = report_from_of(session)
    if source is not None and source.get("by") == REPORT_BY_SOURCE:
        # Idea 39 (39-a): a goal's try carries its chat only so its asks show
        # there; the goal writes ONE report after its checks, this run none.
        return {"ok": True, "updated_at": None, "why": "its source reports", "appended": 0, "skipped": 0}
    try:
        # Lazy, by import DIRECTION: ``daemon/`` imports the agent layer, so
        # a module-level import here would be the platform layer importing
        # the daemon (the inversion v1.185.0 removed) — the same reason
        # ``run_session`` imports ``_claimed_write_note`` inside the function.
        from ..daemon import chat_append as _chat_append

        try:
            result = delivery_result(
                engine, str(getattr(session, "id", "") or ""), manager=manager, config=config
            )
        except Exception:  # noqa: BLE001 — the line still lands without its card
            log.exception("ledger read failed before chat delivery for %s", getattr(session, "id", ""))
            result = None
        who = participant_key_for(session)
        line = line_for_session(session, result, who)
        if source is not None:
            # Idea 39 (39-a, review): Stop reporting pressed while this run was
            # going (or the record pointed at another chat since) holds its
            # report back: the record's own field is read NOW, not the
            # options the run was started with.
            kind = str(source.get("kind") or "")
            rid = str(source.get("id") or "")
            if kind in REPORT_TARGET_KINDS and rid and report_target_of(engine, kind, rid) != thread_id:
                return {"ok": True, "updated_at": None, "why": "it stopped reporting here",
                        "appended": 0, "skipped": 1}
            return _write_report(engine, event_bus, thread_id, line, source)
        return _chat_append.append_to_chat_thread(
            engine, thread_id, [line], event_bus=event_bus, who=who
        )
    except Exception as exc:  # noqa: BLE001 — a delivery miss must never fail a finalize
        log.exception("chat delivery failed for session %s", getattr(session, "id", ""))
        return {
            "ok": False,
            "updated_at": None,
            "why": f"could not deliver: {type(exc).__name__}",
            "appended": 0,
            "skipped": 0,
        }


def remote_report_text(message: str, computer: str, ending: Any) -> str:
    """The words a report from another Iron Jarvis lands with — in the room,
    the chat it was asked in and the phone (v1.514.0, second review F4).

    ``ending`` is ``threads.report_ending``'s reading of the report. Empty (an
    older Iron Jarvis, a message that is not a report, a junk outcome): the
    message exactly as it came. Otherwise, when the run did not end as plain
    done, the line OPENS with how it ended, in that computer's own sentence
    (``outcome_words``, else ``outcome.run_ending``'s for the outcome word),
    said once even though that computer's message already opens with it; and
    it ENDS by saying who answered: "Answered by <model> on <computer>." ("Tried
    …" for a run that failed or was stopped; "the demo model" for the demo).
    Every word but ours is that computer's, and it stays inside the agent's
    fenced line when it reaches a model. Pure; never raises."""
    text = str(message or "").strip()
    try:
        if not isinstance(ending, dict) or not ending.get("outcome"):
            return text
        from .task_door import ending_of_outcome

        outcome = str(ending.get("outcome") or "")
        read = ending_of_outcome(outcome)
        if read is None:
            return text
        if outcome != "done":
            lead = str(ending.get("outcome_words") or "").strip() or read.reason
            if lead and text.startswith(lead):
                text = text[len(lead):].strip()
            if lead:
                text = f"{lead}\n\n{text}" if text else lead
        provider = str(ending.get("answered_provider") or "").strip()
        model = str(ending.get("answered_model") or "").strip() or provider
        if provider == "mock":
            model = "the demo model"
        name = " ".join(str(computer or "").split())
        if model and name:
            verb = "Tried" if outcome in ("failed", "stopped") else "Answered by"
            text = f"{text}\n\n{verb} {model} on {name}." if text else f"{verb} {model} on {name}."
        return text
    except Exception:  # noqa: BLE001 — wording never loses the message
        return str(message or "").strip()


def say_in_chat(engine: Any, thread_id: str, words: str, *, event_bus: Any = None) -> dict[str, Any]:
    """Add ONE line of Iron Jarvis's own to a chat (v1.510.0): what this
    computer knows, with no agent's name on it (a wait for another computer
    that ended, ``threads.end_overdue_waits``). BLOCKING: call it off the
    loop. Returns ``append_to_chat_thread``'s answer (it refuses a
    daemon-owned chat); never raises."""
    try:
        # Lazy, by import direction (see ``deliver_to_chat``).
        from ..daemon import chat_append as _chat_append

        return _chat_append.append_to_chat_thread(
            engine, thread_id, [{"role": "assistant", "content": str(words or "")}],
            event_bus=event_bus, who="daemon",
        )
    except Exception:  # noqa: BLE001 — the room already has the line
        log.exception("could not add a line to chat %s", thread_id)
        return {"ok": False, "updated_at": None, "why": "could not add the line", "appended": 0, "skipped": 0}


# --------------------------------------------------------------------------- #
# REPORTS (idea 39, 39-a): the result of anything set up from a chat comes home
# --------------------------------------------------------------------------- #
#
# ONE field and ONE writer. A record set up from a chat (a schedule, a watcher,
# a goal, a workflow run, a night task) stores that chat's id in its own
# ``chat_thread_id`` column; each result is written into the chat by
# :func:`report_to_chat`, through the daemon's one chat writer
# (``daemon.chat_append.append_to_chat_thread``). A run its source starts
# carries ``options.report_from`` (:data:`REPORT_OPTION`) and its own line IS
# the report (:func:`line_for_session`), kept durable by the run's finalizers
# and the boot reconcile. Keeping it calm: a record that reports more than once
# a day keeps ONE line a day in the chat (the day's line is replaced, its count
# goes up); a watcher writes at most once an hour (D-39.3, the watcher's own
# pacing); a report never un-archives a chat (D-39.2: the bell says it arrived
# instead); and every report has Stop reporting (:func:`set_report_target`).

#: Who may write a report (idea 39's shared vocabulary; later ideas add their
#: callers, never a new kind of line).
REPORT_KINDS: tuple[str, ...] = (
    "schedule",
    "watcher",
    "goal",
    "workflow",
    "rule",
    "rehearsal",
    "run",
    "coding_session",
    "night",
    "paired_computer",
    "review",
    # Idea 33-a: a recording handed to a chat, turned into text and notes
    # after the reply (``documents/recording_jobs``).
    "recording",
)

#: How each kind is said in a report's first words ("From your schedule ...")
#: and on its chip (the page's ``REPORT_KIND_WORDS``, one table).
REPORT_KIND_WORDS: dict[str, str] = {
    "schedule": "schedule",
    "watcher": "watcher",
    "goal": "goal",
    "workflow": "workflow",
    "rule": "rule",
    "rehearsal": "rehearsal",
    "run": "run",
    "coding_session": "coding session",
    "night": "night task",
    "paired_computer": "other computer",
    "review": "night review",
    "recording": "recording",
}

#: Where each kind's record is (the report's "Open"), unless the report names one.
REPORT_HREFS: dict[str, str] = {
    "schedule": "/schedules",
    "watcher": "/sentinels",
    "goal": "/autonomy",
    "workflow": "/workflows",
    "rule": "/reflex",
    "night": "/chat?view=code",
}

#: A report's words are cut here; the rest stays on the run, behind its link
#: ("Keep big results out of the context").
REPORT_MAX_CHARS = 1500
REPORT_CUT_WORDS = "… The rest is on the run."

#: The run option a source's report rides under (``Session.options_json``).
REPORT_OPTION = "report_from"
#: ``report_from.by``: the SOURCE writes the report itself after the run (a
#: goal, after its checks), so the run's own ending writes nothing.
REPORT_BY_SOURCE = "source"

#: Kinds that pace themselves and are never folded to one line a day (a
#: watcher writes at most once an hour, D-39.3).
REPORT_NOT_FOLDED: frozenset[str] = frozenset({"watcher"})

#: The shortest gap between two reports of one watcher (D-39.3).
WATCHER_REPORT_GAP_S = 3600

#: The ``who`` on ``chat.thread_updated`` for a report.
REPORT_WHO = "report"

#: The words another agent's line in a chat's history opens with — the SAME
#: words ``comm.threads.agent_line_label`` and the page's
#: ``toRequestMessages`` put first (``change_claims._AGENT_LINE_PREFIX``).
#: Kept HERE, below the daemon (39-a, round 4): ``agents/threads.py`` fences
#: a report row with them and may not import from the daemon
#: (``test_agents_threads_does_not_import_from_daemon_routes``);
#: ``daemon/chat_refs`` re-exports them for the two chat lanes.
AGENT_LINE_PREFIX = "[Reply from the agent "

#: Idea 39 (39-a): the words a REPORT in a chat's history opens with (the
#: page's ``toRequestMessages`` puts ``[Report from your schedule "Friday
#: prices", written when it ran. Not your words.]`` first): a run's summary,
#: a watcher's file names, a workflow's last step. Fenced like an agent's line.
REPORT_LINE_PREFIX = "[Report from "

#: Every label a fenced line may open with.
FENCED_LINE_PREFIXES = (AGENT_LINE_PREFIX, REPORT_LINE_PREFIX)


def fenced_line_text(content: Any) -> "str | None":
    """``content`` with everything after its agent (or report) label fenced as
    outside data (``computeruse.safety.wrap_untrusted``), or None when it is
    not such a line or is fenced already. The label stays the first line.
    Pure."""
    if not isinstance(content, str) or not content.startswith(FENCED_LINE_PREFIXES):
        return None
    label, sep, rest = content.partition("\n")
    if not sep or not label.endswith("]"):
        return None
    from ..computeruse.safety import _FENCE_TOP, wrap_untrusted

    if rest.lstrip().startswith(_FENCE_TOP):
        return None
    return f"{label}\n{wrap_untrusted(rest)}"

#: The bell's words when the chat a record reported to is gone.
GONE_WORDS = "The chat this {kind} reported to is gone. Its results now show here."


def _one_line(text: Any, cap: int = 80) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= cap else words[: cap - 1].rstrip() + "…"


def report_lead(kind: str, name: str) -> str:
    """A report's first sentence, from its kind and name: "From your schedule
    “Friday prices”." Composed by code, never by a model. Pure."""
    word = REPORT_KIND_WORDS.get(kind, "task")
    name = _one_line(name)
    return f"From your {word} “{name}”." if name else f"From your {word}."


def cut_report_words(words: Any) -> str:
    """The report's body at most :data:`REPORT_MAX_CHARS` characters; a cut
    body says the rest is on the run. Pure."""
    text = str(words or "").strip()
    if len(text) <= REPORT_MAX_CHARS:
        return text
    return text[: REPORT_MAX_CHARS - len(REPORT_CUT_WORDS)].rstrip() + REPORT_CUT_WORDS


def _now() -> Any:
    """The clock a report's day is read from (local, aware). One function so a
    test injects the day instead of waiting for one."""
    from datetime import datetime

    return datetime.now().astimezone()


def report_fold_key(kind: str, record_id: str, now: Any = None) -> str:
    """The day-fold of a record's report: ``<kind>:<id>:<local date>``; "" for
    a kind that paces itself or a report with no record. Pure."""
    from datetime import datetime

    rid = str(record_id or "").strip()
    if not rid or kind in REPORT_NOT_FOLDED:
        return ""
    when = now if isinstance(now, datetime) else _now()
    if when.tzinfo is not None:
        when = when.astimezone()
    return f"{kind}:{rid}:{when.date().isoformat()}"


def report_from_of(session: Any) -> "dict[str, Any] | None":
    """The source a run reports for (``options.report_from``), or None. Junk
    (not a dict, an unknown kind) reads None. Never raises."""
    try:
        from ..scheduling.knobs import session_options

        options = session_options(session)
    except Exception:  # noqa: BLE001
        return None
    raw = options.get(REPORT_OPTION) if isinstance(options, dict) else None
    if not isinstance(raw, dict) or raw.get("kind") not in REPORT_KINDS:
        return None
    return raw


def report_line(report: dict[str, Any], *, now: Any = None) -> "dict[str, Any] | None":
    """The ONE chat line of a report (pure; None for a report of no known
    kind). ``report`` is ``{kind, name, id, run_id?, outcome?, reason?, words?,
    documents?, href?, key, receipt?}``: ``outcome`` is the badge word the
    ledger's verdict gives (``outcome.run_ending``), ``reason`` its sentence
    when it fell short; ``words`` the run's own summary (cut at
    :data:`REPORT_MAX_CHARS`). The line: ``{role: "assistant", content,
    reportFrom: {kind, name, id, href, outcome?, runId?}, reportKey,
    reportFold?, sessionId?, receipt?, documents?}``."""
    if not isinstance(report, dict):
        return None
    kind = str(report.get("kind") or "")
    if kind not in REPORT_KINDS:
        return None
    name = _one_line(report.get("name"), 120)
    rid = str(report.get("id") or "").strip()
    run_id = str(report.get("run_id") or "").strip()
    outcome = _one_line(report.get("outcome"), 40)
    reason = " ".join(str(report.get("reason") or "").split())
    body = cut_report_words(report.get("words"))
    lead = report_lead(kind, name)
    if reason:
        lead = f"{lead} {reason}"
    content = f"{lead}\n\n{body}" if body else lead
    source: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "id": rid,
        "href": str(report.get("href") or REPORT_HREFS.get(kind) or ""),
    }
    if outcome:
        source["outcome"] = outcome
    if run_id:
        source["runId"] = run_id
    key = str(report.get("key") or "").strip() or (f"run:{run_id}" if run_id else "")
    line: dict[str, Any] = {"role": "assistant", "content": content, "reportFrom": source}
    if key:
        line["reportKey"] = key
    fold = report_fold_key(kind, rid, now)
    if fold:
        line["reportFold"] = fold
    if run_id:
        line["sessionId"] = run_id
    receipt = report.get("receipt")
    if isinstance(receipt, dict) and receipt.get("found"):
        line["receipt"] = receipt
    docs = [str(p) for p in (report.get("documents") or []) if p]
    if not docs and isinstance(receipt, dict):
        docs = [str(p) for p in (receipt.get("documents") or []) if p]
    if docs:
        line["documents"] = docs
    return line


def report_line_for_session(
    session: Any, result: Any, source: dict[str, Any], *, now: Any = None
) -> dict[str, Any]:
    """A source's report, written by the run it started (a schedule's fire):
    the verdict from the LEDGER (``outcome.run_ending``), never the model's
    closing words; the run's summary as the body; the run's receipt. Pure."""
    from .outcome import result_line, run_ending

    ending = run_ending(session)
    # The intelligence plan, release 4: the badge word is the run's RESULT
    # LINE ("Done. 1 step could not be checked." when a plan step's check did
    # not run), read from the same receipt; the bare word when there is no
    # receipt to read.
    said = (result_line(result) if isinstance(result, dict) else "") or ending.words
    sid = str(getattr(session, "id", "") or "")
    summary = str(getattr(session, "summary", "") or "").strip()
    report = {
        "kind": source.get("kind"),
        "name": source.get("name"),
        "id": source.get("id"),
        "href": source.get("href"),
        "run_id": sid,
        "outcome": said if ending.ok else ending.words,
        "reason": "" if ending.ok else ending.reason,
        "words": summary,
        "key": f"run:{sid}" if sid else "",
        "receipt": result,
    }
    line = report_line(report, now=now) or {"role": "assistant", "content": content_for_session(session)}
    if sid:
        line["sessionId"] = sid
    return line


def _publish_event(event_bus: Any, etype: str, payload: dict[str, Any]) -> None:
    """Best effort: on the loop as a task, off it under ``asyncio.run`` (the
    pattern of ``chat_append._publish``). Never raises."""
    if event_bus is None:
        return
    try:
        import asyncio

        coro = event_bus.publish(etype, payload)
        if not asyncio.iscoroutine(coro):
            return
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception:  # noqa: BLE001 — a notice never breaks a report
        log.warning("could not publish %s", etype, exc_info=True)


def _write_report(
    engine: Any, event_bus: Any, thread_id: str, line: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    """Append one report line and say what happened. BLOCKING; never raises.

    A chat that is gone clears the record's link and rings the bell
    (``report.chat_gone``); an archived chat stays archived and the bell says a
    report arrived (``report.arrived``, D-39.2). A phone thread or a coding
    session refuses in words (``append_to_chat_thread``'s ``why``)."""
    from ..core.events import EventType
    from ..daemon import chat_append as _chat_append

    out = _chat_append.append_to_chat_thread(
        engine, thread_id, [line], event_bus=event_bus, who=REPORT_WHO, unarchive=False
    )
    kind = str(source.get("kind") or "")
    # Review: the bell's notices may reach the phone, so a source whose words
    # must stay on this PC (a Local only night task) names itself there only
    # by ``notice_name``; the chat line keeps the record's own name.
    name = _one_line(source.get("notice_name") or source.get("name"), 120)
    rid = str(source.get("id") or "")
    if not out.get("ok") and out.get("why") == "no such chat":
        # Review: clear the link only while it still names THIS chat (a run
        # fired for chat A must not clear a record pointed at chat B since).
        cleared = (
            set_report_target(engine, kind, rid, "", only_if=thread_id)
            if kind in REPORT_TARGET_KINDS else {}
        )
        out["gone"] = True
        # ``changed``: a record the user had stopped already clears nothing.
        out["cleared"] = bool(cleared.get("ok") and cleared.get("changed"))
        if out["cleared"]:
            # The record's own row says why it reports nowhere now.
            note_gone_on_row(engine, kind, rid)
        if out["cleared"] or kind not in REPORT_TARGET_KINDS:
            _publish_event(event_bus, EventType.REPORT_CHAT_GONE, {
                "kind": kind, "name": name, "id": rid,
                "href": str(source.get("href") or REPORT_HREFS.get(kind) or ""),
                "words": GONE_WORDS.format(kind=REPORT_KIND_WORDS.get(kind, "task")),
            })
    elif out.get("ok") and out.get("archived") and (out.get("appended") or 0) > 0:
        _publish_event(event_bus, EventType.REPORT_ARRIVED, {
            "thread_id": thread_id, "title": _one_line(out.get("title"), 120),
            "kind": kind, "name": name,
        })
    return out


def report_to_chat(platform: Any, thread_id: str, report: dict[str, Any], *, now: Any = None) -> dict[str, Any]:
    """Write ONE report into the chat ``thread_id`` (idea 39, 39-a): the one
    writer every source calls. ``platform`` gives ``engine`` and
    ``event_bus`` (anything with both). ``report`` as :func:`report_line`.
    BLOCKING (SQLite): a caller on the loop wraps it in ``asyncio.to_thread``.
    Returns ``append_to_chat_thread``'s answer (``gone`` when the chat no
    longer exists); never raises."""
    empty = {"ok": False, "updated_at": None, "why": "", "appended": 0, "skipped": 0}
    try:
        tid = clean_chat_thread_id(thread_id)
        if not tid:
            return {**empty, "why": "no chat"}
        line = report_line(report, now=now)
        if line is None:
            return {**empty, "why": "not a report"}
        engine = getattr(platform, "engine", None)
        if engine is None:
            return {**empty, "why": "no database"}
        return _write_report(engine, getattr(platform, "event_bus", None), tid, line, report)
    except Exception as exc:  # noqa: BLE001 — a report never breaks its source
        log.exception("could not write a report into chat %s", thread_id)
        return {**empty, "why": f"could not report: {type(exc).__name__}"}


# --- the one field: which chat a record reports to ---------------------------


def _record_class(kind: str) -> Any:
    """The table whose ``chat_thread_id`` says where ``kind`` reports, or None."""
    if kind == "schedule":
        from ..scheduling.models import ScheduledTaskRecord

        return ScheduledTaskRecord
    if kind == "watcher":
        from ..sentinels.models import SentinelRecord

        return SentinelRecord
    if kind == "goal":
        from ..goals.models import GoalContractRecord

        return GoalContractRecord
    if kind == "workflow":
        from ..workflows.models import WorkflowRunRecord

        return WorkflowRunRecord
    if kind == "night":
        from ..harness.night import NightTaskRecord

        return NightTaskRecord
    if kind == "rule":
        # Leap 05-a: a rule made in a chat reports each of its runs there.
        from ..reflex.models import ReflexRule

        return ReflexRule
    return None


#: The kinds whose record holds the field (the rest report through a run).
REPORT_TARGET_KINDS: tuple[str, ...] = ("schedule", "watcher", "goal", "workflow", "night", "rule")


def report_target_of(engine: Any, kind: str, record_id: str) -> str:
    """The chat ``kind``'s record ``record_id`` reports to, "" for none.
    BLOCKING; never raises."""
    cls = _record_class(kind)
    if cls is None or not record_id:
        return ""
    try:
        from ..core.db import session_scope

        with session_scope(engine) as db:
            row = db.get(cls, str(record_id))
            return clean_chat_thread_id(getattr(row, "chat_thread_id", "") or "") if row is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _takes_reports(chat: Any) -> bool:
    """Whether a saved chat row may take reports: a chat on the Chat page,
    never a phone conversation (``owner == "daemon"``, until 39-c) or a coding
    session (``surface == "build"``, its own turns write it). Pure."""
    if chat is None:
        return False
    if (getattr(chat, "owner", "user") or "user") == "daemon":
        return False
    return str(getattr(chat, "surface", "") or "") != "build"


def set_report_target(
    engine: Any, kind: str, record_id: str, thread_id: str, *, only_if: "str | None" = None
) -> dict[str, Any]:
    """Point ``kind``'s record ``record_id`` at the chat ``thread_id`` ("" =
    Stop reporting). Returns ``{ok, why, previous, thread_id, changed}``;
    ``previous`` is the chat it reported to before (the Undo puts it back).
    A chat id must name a saved chat of the user's (never a phone thread or a
    coding session). ``only_if`` (review): change it only while the record
    still names that chat (compare-and-clear; ``why`` says so otherwise).
    A STOP with ``only_if`` on a record that already reports nowhere (39-a,
    round 4: the user stopped it on its row first) is ok with ``changed``
    False: it IS stopped, so the report's press says "Reports stopped.", never
    "It reports to another chat now.". BLOCKING; never raises."""
    out: dict[str, Any] = {"ok": False, "why": "", "previous": "", "thread_id": "", "changed": False}
    cls = _record_class(kind)
    if cls is None:
        out["why"] = f"A {REPORT_KIND_WORDS.get(kind, 'record')} does not report to a chat."
        return out
    tid = clean_chat_thread_id(thread_id)
    try:
        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord

        with session_scope(engine) as db:
            if tid:
                chat = db.get(ChatThreadRecord, tid)
                if chat is None:
                    out["why"] = "That chat is not saved, so nothing can report to it."
                    return out
                if not _takes_reports(chat):
                    out["why"] = "Reports go to a chat on the Chat page, not to this one."
                    return out
            row = db.get(cls, str(record_id or ""))
            if row is None:
                out["why"] = f"There is no such {REPORT_KIND_WORDS.get(kind, kind)}."
                return out
            out["previous"] = clean_chat_thread_id(getattr(row, "chat_thread_id", "") or "")
            if only_if is not None and out["previous"] != clean_chat_thread_id(only_if):
                if not tid and not out["previous"]:
                    out["ok"] = True  # stopped already: true, and nothing to undo
                    return out
                out["why"] = "It reports to another chat now."
                return out
            row.chat_thread_id = tid
            db.add(row)
            db.commit()
        out["ok"] = True
        out["thread_id"] = tid
        out["changed"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("could not change where a %s reports", kind)
        out["why"] = f"could not change it: {type(exc).__name__}"
        return out


#: Idea 39 (39-a, review): what a record's own row says once the chat it
#: reported to is gone (the link is cleared; the bell says it too).
GONE_ROW_WORDS = "Its reports stopped: the chat they went to is gone."


def note_gone_on_row(engine: Any, kind: str, record_id: str) -> bool:
    """Write :data:`GONE_ROW_WORDS` on the record's own row, where its page
    shows words: a schedule's last result line (``last_detail``), a night
    task's notes, a workflow run's notes (``notes_json``). Once (a second call
    keeps one). A watcher's only words on its row are its scan error (cleared
    by its next scan) and a goal's row has none, so for those the bell's
    ``report.chat_gone`` says it. BLOCKING; never raises."""
    rid = str(record_id or "").strip()
    if not rid:
        return False
    try:
        if kind == "night":
            from ..harness.night import add_note

            return bool(add_note(engine, rid, "info", GONE_ROW_WORDS))
        if kind == "workflow":
            import json as _json

            from ..core.db import session_scope
            from ..workflows.models import WorkflowRunRecord

            with session_scope(engine) as db:
                run = db.get(WorkflowRunRecord, rid)
                if run is None:
                    return False
                try:
                    notes = _json.loads(run.notes_json or "[]")
                except (TypeError, ValueError):
                    notes = []
                notes = notes if isinstance(notes, list) else []
                if GONE_ROW_WORDS not in notes:
                    run.notes_json = _json.dumps([*notes, GONE_ROW_WORDS])
                    db.add(run)
                    db.commit()
            return True
        if kind == "schedule":
            from ..core.db import session_scope
            from ..scheduling.models import ScheduledTaskRecord

            with session_scope(engine) as db:
                row = db.get(ScheduledTaskRecord, rid)
                if row is None:
                    return False
                detail = " ".join(str(row.last_detail or "").split())
                if GONE_ROW_WORDS in detail:
                    return True
                keep = 300 - len(GONE_ROW_WORDS) - 1
                row.last_detail = (f"{detail[:keep]} {GONE_ROW_WORDS}").strip()
                db.add(row)
                db.commit()
            return True
    except Exception:  # noqa: BLE001 — the bell still says it
        log.warning("could not note on a %s's row that its chat is gone", kind, exc_info=True)
    return False


def note_gone_if_cleared(engine: Any, kind: str, record_id: str, thread_id: str) -> bool:
    """After a source wrote its own row (a schedule's fire stamps its last
    result AFTER its run's report): when the record no longer reports and the
    chat ``thread_id`` it reported to is gone, say so on the row again.
    BLOCKING; never raises."""
    tid = clean_chat_thread_id(thread_id)
    if not tid or report_target_of(engine, kind, record_id):
        return False
    if chat_titles(engine, [tid]):
        return False  # the chat is there: the user stopped it, nothing is gone
    return note_gone_on_row(engine, kind, record_id)


def stampable_chat(engine: Any, thread_id: Any) -> str:
    """The chat a create tool may stamp on its record (idea 39, 39-a): the
    saved chat it was asked in (``ToolContext.thread_id``, set by both chat
    lanes and by a run started from a chat), "" when there is none, it is not
    saved, it is a coding session (its own turns write it) or a phone
    conversation (review: nothing can report there until 39-c, so nothing
    says it does). BLOCKING; never raises."""
    tid = clean_chat_thread_id(thread_id)
    if not tid or engine is None:
        return ""
    try:
        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord

        with session_scope(engine) as db:
            row = db.get(ChatThreadRecord, tid)
            if not _takes_reports(row):
                return ""
        return tid
    except Exception:  # noqa: BLE001 — unreadable: nothing is stamped
        return ""


# --- a NEW chat's first turn: the chat is named after the call (review) -----
#
# A new chat's first question goes out before its first save has given the
# chat an id (the page sends no ``thread_id`` until then), and the page links
# the turn to the chat a moment later (``POST /chat/turns/{id}/thread``). A
# create tool on that turn therefore reads the chat through the TURN: the
# link the page already made (the live turn, a link waiting for the turn, the
# turn's record), and when the link has not landed yet, the record waits here
# (keyed by the turn, bounded, under one lock: the media jobs' and Local
# only's rule) and the link route stamps it (``link_turn_reports``).

_TURN_LOCK = threading.Lock()
#: turn id -> the records it set up with no chat yet, ``[(kind, id)]``.
_PENDING_REPORTS: "dict[str, list[tuple[str, str]]]" = {}
#: turn id -> the chat the page linked it to (so a record made AFTER the
#: link route ran is stamped by its own tool, never left waiting).
_LINKED_TURNS: dict[str, str] = {}
_PENDING_MAX = 200


def _turn_key(turn_id: Any) -> str:
    return str(turn_id or "").strip()[:80]


def chat_of_turn(platform: Any, turn_id: Any) -> str:
    """The saved chat the page linked chat turn ``turn_id`` to: the link the
    link route kept, the live turn's link or a link waiting for the turn
    (``TurnLogs.thread_for``), then the turn's record. "" when nobody said
    yet. BLOCKING (one row read); never raises."""
    turn = _turn_key(turn_id)
    if not turn:
        return ""
    with _TURN_LOCK:
        linked = _LINKED_TURNS.get(turn, "")
    if linked:
        return linked
    logs = getattr(platform, "turn_logs", None)
    if logs is not None:
        try:
            tid = clean_chat_thread_id(logs.thread_for(turn) or "")
        except Exception:  # noqa: BLE001 — an older holder reads as unknown
            tid = ""
        if tid:
            return tid
    engine = getattr(platform, "engine", None)
    if engine is None:
        return ""
    try:
        from ..core.db import session_scope
        from ..core.models import ChatTurnRecord

        with session_scope(engine) as db:
            rec = db.get(ChatTurnRecord, turn)
            return clean_chat_thread_id(getattr(rec, "thread_id", "") or "") if rec is not None else ""
    except Exception:  # noqa: BLE001 — unreadable: not known yet
        return ""


def report_chat_for(platform: Any, ctx: Any) -> str:
    """The chat a record set up by this tool call reports to (D-39.1): the
    saved chat on the call (``ctx.thread_id``), else, for a NEW chat's first
    turn that carried none, the chat the page linked the turn to
    (:func:`chat_of_turn`). "" when there is none or it cannot take reports
    (:func:`stampable_chat`). BLOCKING; never raises."""
    engine = getattr(platform, "engine", None)
    if engine is None:
        engine = getattr(ctx, "engine", None)
    tid = clean_chat_thread_id(getattr(ctx, "thread_id", "") or "")
    if not tid:
        tid = chat_of_turn(platform, getattr(ctx, "turn_id", ""))
    return stampable_chat(engine, tid)


def note_pending_report(turn_id: Any, kind: str, record_id: str) -> str:
    """A record turn ``turn_id`` set up before the page named the turn's chat:
    when the page has ALREADY linked the turn, answer that chat (the caller
    stamps it now); else keep ``(kind, record_id)`` for
    :func:`link_turn_reports` and answer "". Pure (memory only)."""
    turn = _turn_key(turn_id)
    rid = str(record_id or "").strip()
    if not turn or not rid or kind not in REPORT_TARGET_KINDS:
        return ""
    with _TURN_LOCK:
        linked = _LINKED_TURNS.get(turn, "")
        if linked:
            return linked
        if turn not in _PENDING_REPORTS and len(_PENDING_REPORTS) >= _PENDING_MAX:
            _PENDING_REPORTS.pop(next(iter(_PENDING_REPORTS)))
        waiting = _PENDING_REPORTS.setdefault(turn, [])
        if (kind, rid) not in waiting:
            waiting.append((kind, rid))
    return ""


def link_turn_reports(turn_id: Any, thread_id: Any) -> "list[tuple[str, str]]":
    """The page linked ``turn_id`` to the chat ``thread_id``: remember it (a
    record the turn sets up later is stamped by its own tool) and answer the
    records that waited for it, for the caller to stamp. Pure (memory)."""
    turn = _turn_key(turn_id)
    tid = clean_chat_thread_id(thread_id)
    if not turn:
        return []
    with _TURN_LOCK:
        if tid:
            if turn not in _LINKED_TURNS and len(_LINKED_TURNS) >= _PENDING_MAX:
                _LINKED_TURNS.pop(next(iter(_LINKED_TURNS)))
            _LINKED_TURNS[turn] = tid
        return list(_PENDING_REPORTS.pop(turn, []))


def stamp_linked_reports(engine: Any, turn_id: Any, thread_id: Any) -> int:
    """The link route's half: the records ``turn_id`` set up before its chat
    was named now report to ``thread_id``, each only while it still reports
    nowhere (compare-and-set: a record pointed elsewhere since keeps it).
    Answers how many were stamped. BLOCKING; never raises."""
    tid = clean_chat_thread_id(thread_id)
    done = 0
    for kind, rid in link_turn_reports(turn_id, tid):
        if not tid:
            continue
        try:
            if set_report_target(engine, kind, rid, tid, only_if="").get("ok"):
                done += 1
        except Exception:  # noqa: BLE001 — the record stays as it was made
            log.warning("could not point a %s at its new chat", kind, exc_info=True)
    return done


async def stamp_when_linked(platform: Any, ctx: Any, kind: str, record_id: str) -> bool:
    """After a create tool made a record with no chat: on a NEW chat's first
    turn (no ``ctx.thread_id``, a ``ctx.turn_id``), the record reports to the
    chat the page names for the turn: now when the link just landed (answers
    True: the card may say Reports here), else when it lands. Never raises."""
    try:
        if clean_chat_thread_id(getattr(ctx, "thread_id", "") or ""):
            return False  # the call named its chat, and that chat takes no reports
        linked = note_pending_report(getattr(ctx, "turn_id", ""), kind, record_id)
        if not linked:
            return False
        import asyncio

        out = await asyncio.to_thread(
            set_report_target, getattr(platform, "engine", None), kind, record_id, linked, only_if=""
        )
        return bool(out.get("ok"))
    except Exception:  # noqa: BLE001 — the record stands as made
        return False


def chat_titles(engine: Any, thread_ids: Any) -> dict[str, str]:
    """The titles of the saved chats ``thread_ids`` (one read), for a row's
    "Reports to". A chat that is gone is absent. BLOCKING; never raises."""
    ids = sorted({clean_chat_thread_id(t) for t in (thread_ids or []) if clean_chat_thread_id(t)})
    if not ids:
        return {}
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord as T

        with session_scope(engine) as db:
            rows = db.exec(select(T.id, T.title).where(T.id.in_(ids))).all()  # type: ignore[attr-defined]
        return {str(r[0]): str(r[1] or "") or "Untitled chat" for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def reports_to_view(thread_id: Any, titles: dict[str, str]) -> "dict[str, str] | None":
    """A row's ``reports_to``: ``{thread_id, title}`` while the chat exists,
    None for none (or a chat that is gone). Pure."""
    tid = clean_chat_thread_id(thread_id or "")
    if not tid or tid not in titles:
        return None
    return {"thread_id": tid, "title": titles[tid]}
