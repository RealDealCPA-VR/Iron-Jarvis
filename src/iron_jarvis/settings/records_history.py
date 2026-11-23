"""Goals from Chat, Local only and the change history, from chat (v1.435.0,
chat-first plan Wave 3 group I; the owner's decisions D3 and D4).

Three gaps the plan's B2 table measured, each closed the way the other
record tools are (``settings.records``):

* GOALS FROM CHAT. The goal contracts chat makes (``goals/``, the "Goals from
  Chat" list on the Autonomy page) were invisible to chat: ``goal_list``
  showed only the simple standing goals and ``goal_update`` / ``goal_delete``
  acted only on those. Now ``goal_list`` lists the contracts too (marked as
  such), and the two writers pause, resume, stop and delete one — through the
  engine's OWN doors (``GoalEngine.resume_goal`` / ``stop_goal`` /
  ``delete_goal`` and the store's guarded transition, the very code the
  ``/goals`` routes run). Each change has an Undo (:func:`restore_goal_contract`
  puts the whole row back, its schedule and the always-allows a delete took
  back); a STOP that ended a run in progress cannot start that run again, and
  the result says so.

* LOCAL ONLY FOR THIS CHAT (D3). ``chat_local_only`` turns it on (no card)
  or off (a card EVERY time, in every posture, past every grant — a
  once-only spend check, the plan door's rule). A chat an app holds Local
  only (Microsoft 365, ``egress.HOLDS_KEY``) is never turned off. The
  reply being written keeps the scope it started with (``egress.LOCAL_ONLY``
  is fixed when a turn starts), so the result says the change applies from
  the NEXT message — never that this reply is now local. A new chat's first
  turn has no saved id: its choice waits by turn id (``egress.
  note_pending_local_only``) and the link route applies it. The result
  carries a card keyed :data:`LOCAL_ONLY_CARD_KEY`: the chat page reads it to
  set its own switch, which every later save of the chat carries (without it
  the page's next save would put the old value back).

  The default for NEW chats is a setting row (``chat_new_local_only``,
  "Start new chats local only"): changed through the settings tools (off is
  ask-floor), read by the chat page when it starts a new chat.

* UNDO AND HISTORY (D4). ``changes_list`` reads the undo journal — what Iron
  Jarvis changed recently: settings, records, files, memory notes — names and
  plain words only, never a value or a file's contents. ``change_undo``
  reverses one through THE code ``POST /undo/{id}`` runs
  (``routes.undo.perform_undo``, the one ``config_undo`` already shares);
  it ALWAYS shows a card naming exactly the change it will undo, and undoes
  that one even if a newer change lands while the card waits. What the
  journal cannot reverse (no inverse kept, a message sent, money spent, a
  change already undone) is refused in plain words before anything runs.

REVIEW (v1.435.0), each pinned in tests/test_chat_history_review_v14350.py:

* ``chat_local_only`` and ``change_undo`` act only in a chat on the Chat page
  (session "chat", the chat's failure surface — :func:`_chat_page_problem`):
  an agent run's card is a phone alert anyone can answer "approve" to, the
  browser sidebar has no saved chat, the phone and a coding session neither.
  Elsewhere there is no card (``SpendCheck(ask=False)``) and execute refuses.
* Both cards are SAFETY cards (``once_reason`` :data:`LOCAL_ONLY_REASON` /
  :data:`UNDO_REASON`, in ``harness/stall.SAFETY_REASONS`` and
  ``dashboard/lib/onceReason.ts``): Enter never allows them.
* Turning Local only ON in a reply that is not Local only HOLDS that reply's
  steer notes (``core/turns.TurnHandle.hold_steers``): a note typed after it
  stays in the box and goes with the next, Local only, message.
* An undo of a pause / resume / stop of a goal from chat puts back its STATE
  only, never its books (spend, checkpoint); only an undo of a DELETE gives
  its always-allows back.
* The history never tells a turn that may not recall Local only chats the
  NAME of a change made in Local only work (:func:`local_only_rows`).
* ``config_undo`` (records.py) refuses an undo that would put a setting back
  at a value that always asks (``records.protected_restores``).

Every snapshot, restore and read is BLOCKING (SQLite) and runs through
``asyncio.to_thread``. Every pattern here is linear (each repeat bounded, no
two adjacent unbounded runs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

#: The two history tools and their default permission. Kept OUT of
#: ``records.RECORD_TOOL_TIERS`` (as ``config_undo`` is): neither carries a
#: card, and neither is itself a configuration change on the ledger.
HISTORY_TOOL_TIERS: dict[str, str] = {"changes_list": "allow", "change_undo": "ask"}
LOCAL_ONLY_TOOL = "chat_local_only"
#: The card key the chat page reads to set its Local only switch
#: (dashboard/lib/localOnlyChat.ts, pinned equal).
LOCAL_ONLY_CARD_KEY = "chat.local_only"
#: Review (v1.435.0): the once-only cards these tools put up are SAFETY cards
#: (``harness/stall.SAFETY_REASONS``, ``dashboard/lib/onceReason.ts``): Enter
#: never allows them and they never go to the phone. Turning Local only off is
#: a privacy decision; an undo cannot itself be undone.
LOCAL_ONLY_REASON = "privacy"
UNDO_REASON = "undo"
#: Review: both tools act for the person looking at a chat on the Chat page —
#: never from an agent run (its ask can be answered from the phone), the
#: browser sidebar (it has no saved chat: "now on" would be a lie), the phone
#: or a coding session.
CHAT_PAGE_ONLY = "{what} can be changed only from a chat on the Chat page, by the person looking at it."


def _chat_page_problem(ctx: Any, what: str) -> str:
    """"" when this call is a chat turn on the Chat page (session "chat", the
    chat's own failure surface — ``mcp.tools.chat_only_refusal``'s rule),
    else the refusal. Never raises (unknown = refused)."""
    try:
        from ..providers.failure_words import SURFACE_CHAT, current_surface

        if str(getattr(ctx, "session_id", "") or "") == "chat" and current_surface() == SURFACE_CHAT:
            return ""
    except Exception:  # noqa: BLE001 — unknown: refused
        pass
    return CHAT_PAGE_ONLY.format(what=what)

# --------------------------------------------------------------------------
# arming
# --------------------------------------------------------------------------

#: THIS chat — never "every chat", "new chats".
_THIS_CHAT = r"(?:this|the|our|my)\s+(?:chat|conversation|thread)"
_ON_MY_PC = (
    r"(?:on|to|in)\s+(?:my|this|the|our)\s+(?:own\s+)?"
    r"(?:pc|computer|machine|laptop|desktop|device|network)\b"
)
_LOCAL = r"local(?:[\s-]+only)?\b"
_LOCAL_ONLY_RX = re.compile(
    # "keep this chat on my PC", "make this conversation local only"
    r"\b(?:keep|make|set|switch|turn|put|mark|move)\s+" + _THIS_CHAT
    + r"\s+(?:to\s+|as\s+|back\s+to\s+)?(?:(?:just|only|strictly|completely|entirely|purely)\s+)?"
    r"(?:" + _LOCAL + r"|" + _ON_MY_PC + r"|off\s+the\s+cloud\b|private\b|offline\b)"
    # "turn on local only", "turn local only off", "disable local only"
    r"|\b(?:turn|switch|toggle|set)\s+(?:on|off|back\s+on)\s+(?:the\s+)?local[\s-]+only\b"
    r"|\b(?:turn|switch|toggle|set)\s+(?:the\s+)?local[\s-]+only\s+(?:on|off|back\s+on)\b"
    r"|\b(?:enable|disable|activate|deactivate)\s+(?:the\s+)?local[\s-]+only\b"
    r"|\blocal[\s-]+only\s+(?:for|in|on)\s+" + _THIS_CHAT
    # "don't send this chat to the cloud"
    + r"|\b(?:don'?t|do\s+not|never)\s+send\s+(?:" + _THIS_CHAT + r"|anything\s+(?:here|in\s+" + _THIS_CHAT
    + r"))\s+to\s+(?:the\s+)?cloud\b"
    # "this chat should stay on my PC" / "this chat can use the cloud again"
    r"|\b" + _THIS_CHAT + r"\s+(?:should|must|needs?\s+to|has\s+to)\s+(?:stay|be\s+kept|remain)\s+(?:"
    + _LOCAL + r"|" + _ON_MY_PC + r"|off\s+the\s+cloud\b)"
    r"|\b(?:let|allow)\s+" + _THIS_CHAT + r"\s+(?:to\s+)?use\s+(?:the\s+)?cloud\b"
    r"|\b(?:stop|quit)\s+keeping\s+" + _THIS_CHAT + r"\s+(?:local\b|" + _ON_MY_PC + r")"
    r"|\btake\s+" + _THIS_CHAT + r"\s+off\s+local[\s-]+only\b",
    re.IGNORECASE,
)
#: The NEW-chat default is a setting (the settings tools), not this chat.
_OTHER_CHATS_RX = re.compile(
    r"\b(?:new|future|every|all|other|later)\s+(?:chats|conversations)\b|\bby\s+default\b", re.IGNORECASE
)
_THIS_CHAT_RX = re.compile(r"\b" + _THIS_CHAT + r"\b", re.IGNORECASE)

_ACTOR = r"(?:you|jarvis|iron\s+jarvis)"
#: Next leaps 40-a: the words that put many changes back at once.
_UNDO_VERB = r"(?:undo|revert|reverse|roll\s*back|put\s+back|take\s+back)"
_MANY = (
    r"(?:everything|every\s+change|all(?:\s+of)?(?:\s+(?:the|my|your|these|those|them))?"
    r"(?:\s+(?:changes|edits|files))?)"
)
_CHANGES_LIST_RX = re.compile(
    # "what did you change today?", "what have you edited?"
    r"\bwhat\s+(?:did|have|has)\s+" + _ACTOR + r"\s+(?:just\s+|already\s+)?"
    r"(?:change|changed|edit|edited|modify|modified|touch|touched|undo|undone)\b"
    # "what changes did you make", "which files did you change"
    r"|\b(?:what|which)\s+(?:changes|edits|files|settings)\s+(?:did|have|has)\s+" + _ACTOR + r"\b"
    # "show me the changes you made", "list your recent changes"
    r"|\b(?:show|list|give|tell)\s+(?:me\s+)?(?:about\s+)?(?:(?:the|your|all|recent|latest|last)\s+){0,3}"
    r"(?:changes|edits)\s+(?:you|jarvis|iron\s+jarvis)\b"
    r"|\b(?:show|list|give)\s+(?:me\s+)?(?:(?:your|all|recent|latest|last)\s+){1,3}(?:changes|edits)\b"
    r"|\b(?:show|list|tell)\s+(?:me\s+)?what\s+" + _ACTOR + r"\s+(?:changed|edited|did)\b"
    r"|\bchanges\s+(?:you(?:'ve|\s+have)?\s+made|you\s+did|(?:iron\s+)?jarvis\s+made)\b"
    r"|\b(?:change|undo)\s+history\b"
    r"|\bhistory\s+of\s+(?:your|the|my)\s+changes\b"
    # Next leaps 40-a: "what did the 3 pm run change?"
    r"|\bwhat\s+(?:did|has)\s+(?:the|my|this|that|today'?s|yesterday'?s)\s+[^.!?\n]{0,30}?\b(?:run|schedule)\s+"
    r"(?:change|changed|edit|edited|touch|touched|do|did)\b",
    re.IGNORECASE,
)
_CHANGE_UNDO_RX = re.compile(
    # "undo the last file edit", "revert your last change", "roll back my recent change"
    r"\b(?:undo|revert|reverse|roll\s*back)\s+(?:(?:the|my|your|that|this)\s+)?"
    r"(?:(?:last|latest|previous|recent|most\s+recent)\s+)"
    r"(?:file\s+|setting\s+)?(?:edit|edits|change|changes|write|save|setting)\b"
    # "undo that file edit", "undo the file change"
    r"|\b(?:undo|revert|reverse|roll\s*back)\s+(?:(?:the|my|your|that|this)\s+)?file\s+"
    r"(?:edit|edits|change|changes|write|save)\b"
    # "undo the change you made to notes.txt", "revert what you did"
    r"|\b(?:undo|revert|reverse|roll\s*back)\s+(?:(?:the|that|this)\s+)?(?:change|changes|edit|edits)\s+"
    + _ACTOR + r"\b"
    r"|\b(?:undo|revert|reverse|roll\s*back|put\s+back|take\s+back)\s+what\s+" + _ACTOR
    + r"\s+(?:just\s+)?(?:did|changed|edited|wrote)\b"
    # v1.487.0: "put it back the way it was", "put my spreadsheet back how it was".
    r"|\bput\s+(?:it|that|this|things|everything|(?:my|the)\s+\w+)\s+back\s+(?:the\s+way|how|as)\s+"
    r"(?:it|they)\s+(?:was|were)\b"
    # "undo that" — config_undo's own phrases (records._UNDO_WORDS): the
    # newest change of ANY kind, named on the card.
    r"|\bundo\s+(?:that|it|this|my\s+last\s+change)\b"
    # Next leaps 40-a: many at once. "undo everything from the 3 pm run",
    # "revert all the changes from the 3pm task", "roll back everything the
    # 15:00 schedule did" — a run, task or schedule is named (never "undo
    # everything in this paragraph").
    + r"|\b" + _UNDO_VERB + r"\s+" + _MANY
    + r"\s+(?:from|in|by|during|of|that|the|made\s+(?:by|in|during))\s+[^.!?\n]{0,40}?\b(?:run|task|schedule)s?\b"
    # "put back everything you changed today"
    + r"|\b" + _UNDO_VERB + r"\s+(?:everything|all)\s+" + _ACTOR
    + r"\s+(?:just\s+)?(?:did|changed|made|wrote|edited)\b"
    # "undo all of these changes", "undo these two changes" (after a list)
    + r"|\b" + _UNDO_VERB + r"\s+(?:all\s+(?:of\s+)?)?(?:these|those)(?:\s+(?:\d{1,3}|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve))?\s+(?:changes|edits)\b"
    # "undo everything from today", "undo all the changes made this morning"
    + r"|\b" + _UNDO_VERB + r"\s+" + _MANY + r"\s+(?:from|made)\s+(?:today|yesterday|this\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)


def history_tools_for(text: str) -> list[str]:
    """The tools of this module a chat message plausibly asks for (called
    ONCE by ``records.record_tools_for``). A QUESTION keeps only the reader
    ``changes_list`` (``records.is_question``): "how do I keep this chat on
    my PC?" never arms the switch. Never raises."""
    try:
        t = text or ""
        out: list[str] = []
        question = R.is_question(t)
        if _CHANGES_LIST_RX.search(t):
            out.append("changes_list")
        if question:
            return out
        if _CHANGE_UNDO_RX.search(t):
            out.append("change_undo")
        if _LOCAL_ONLY_RX.search(t) and not (_OTHER_CHATS_RX.search(t) and not _THIS_CHAT_RX.search(t)):
            out.append(LOCAL_ONLY_TOOL)
        return out
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        return []


# --------------------------------------------------------------------------
# goals from chat (goal contracts)
# --------------------------------------------------------------------------

#: A contract's state in words (the Autonomy page's own vocabulary).
CONTRACT_STATE_WORDS = {
    "active": "running",
    "paused": "paused",
    "satisfied": "done",
    "failed": "failed",
    "stopped": "stopped",
    "tripped": "stopped after failures",
}
#: goal_list's ``status`` filter, read for a contract.
_STATUS_TO_STATES = {
    "active": ("active",),
    "paused": ("paused",),
    "done": ("satisfied",),
    "abandoned": ("stopped", "failed", "tripped"),
    "stopped": ("stopped", "failed", "tripped"),
}
_CONTRACT_DATES = ("created_at", "updated_at", "last_run_at")
CONTRACT_FIELDS_REFUSAL = (
    "A goal from chat can be paused, resumed, stopped or deleted from here. Its contract, "
    "schedule and budget are not changed from chat."
)
CONTRACT_DONE_REFUSAL = (
    "A goal from chat is marked done by its own check, not by hand. Stop it instead if no "
    "more work should happen on it."
)


def _goal_engine(platform: Any) -> Any:
    try:
        return getattr(platform, "goal_engine", None)
    except Exception:  # noqa: BLE001 — a bare platform has none
        return None


def contract_store(platform: Any) -> Any:
    eng = _goal_engine(platform)
    return getattr(eng, "store", None) if eng is not None else None


def contract_rows(platform: Any, status: Any = None) -> list[dict[str, Any]]:
    """The goal contracts for ``goal_list``, newest first. Blocking. ``[]``
    when there is no goal engine or it cannot be read (the store's own reads
    never raise)."""
    store = contract_store(platform)
    if store is None:
        return []
    states = _STATUS_TO_STATES.get(str(status or ""), None)
    out: list[dict[str, Any]] = []
    try:
        records = store.list()
    except Exception:  # noqa: BLE001
        return []
    for rec in records:
        if states is not None and rec.state not in states:
            continue
        contract = " ".join(str(rec.contract_text or "").split())
        out.append(
            {
                "id": rec.id,
                "name": rec.name,
                "state": rec.state,
                "state_words": CONTRACT_STATE_WORDS.get(rec.state, rec.state),
                "schedule": rec.schedule or "",
                "contract": contract if len(contract) <= 160 else contract[:159].rstrip() + "…",
                "kind": "goal_from_chat",
            }
        )
    return out


def contract_lines(rows: list[dict[str, Any]]) -> list[str]:
    """``goal_list``'s lines for the contracts (empty for none)."""
    if not rows:
        return []
    lines = ["Goals from chat (each runs as agent work; pause, resume, stop or delete one by its id):"]
    for r in rows:
        tail = f", on schedule {r['schedule']}" if r["schedule"] else ""
        lines.append(f"- [goal from chat, {r['state_words']}] {r['name']} (id {r['id']}){tail}")
    return lines


def resolve_goal_kind(platform: Any, ref: str) -> tuple[str, str, str]:
    """``(record, id, problem)`` for a goal named by its id or its exact words
    — a standing goal (``records.resolve_goal``'s rule, record ``"goal"``) or a
    goal from chat (its name or its contract, record ``"goal_contract"``).
    Blocking. ``("", "", "")`` when nothing matches; ``problem`` when more
    than one goal does."""
    ref = (ref or "").strip()
    if not ref:
        return "", "", ""
    intent = getattr(platform, "intent", None)
    store = contract_store(platform)
    if intent is not None and intent.get_goal(ref) is not None:
        return "goal", ref, ""
    if store is not None and store.get(ref) is not None:
        return "goal_contract", ref, ""
    folded = ref.casefold()
    hits: list[tuple[str, str, str]] = []
    if intent is not None:
        hits += [("goal", g.id, g.text) for g in intent.list_goals() if (g.text or "").strip().casefold() == folded]
    if store is not None:
        hits += [
            ("goal_contract", c.id, c.name)
            for c in store.list()
            if folded in ((c.name or "").strip().casefold(), (c.contract_text or "").strip().casefold())
        ]
    if len(hits) == 1:
        return hits[0][0], hits[0][1], ""
    if not hits:
        return "", "", ""
    rows = "; ".join(f"{gid} — {words}" for _k, gid, words in hits[:10])
    return "", "", f"{len(hits)} goals match {ref!r}; name one by its id: {rows}"


def snap_goal_contract(platform: Any, goal_id: str) -> dict[str, Any] | None:
    """Every column of one goal contract (dates as ISO text) plus the ids of
    the always-allows it holds — what a delete takes back. Blocking."""
    store = contract_store(platform)
    if store is None:
        return None
    rec = store.get(goal_id)
    if rec is None:
        return None
    out = {k: (R._iso(v) if k in _CONTRACT_DATES else v) for k, v in rec.model_dump().items()}
    grants: list[str] = []
    store_g = getattr(platform, "grants", None)
    if store_g is not None:
        try:
            grants = [g.id for g in store_g.list("goal", goal_id)]
        except Exception:  # noqa: BLE001 — no grants known
            grants = []
    out["grants"] = grants
    return out


def restore_goal_contract(platform: Any, goal_id: str, prior: dict[str, Any] | None) -> str:
    """Put a goal from chat back as the snapshot had it: every column (a
    deleted one is recreated with its own id), its schedule row kept in step
    with its state (the store's own rule), and the always-allows a delete
    took back given back. Blocking."""
    from ..core.db import session_scope
    from ..goals.models import GoalContractRecord

    eng = _goal_engine(platform)
    store = contract_store(platform)
    if store is None:
        raise R.RecordError("the goal engine is not available on this build")
    if prior is None:
        if eng is None or eng.delete_goal(goal_id) is None:
            return "undo: the goal from chat was already gone"
        return f"undo: removed the goal from chat '{goal_id}'"
    fields = set(GoalContractRecord.model_fields)
    with session_scope(platform.engine) as db:
        row = db.get(GoalContractRecord, goal_id)
        recreated = row is None
        if row is None:
            row = GoalContractRecord(id=goal_id)
        state_now = row.state if not recreated else ""
        if recreated:
            # Undo of a DELETE: the whole row back, with its own id.
            for f, v in prior.items():
                if f in ("id", "grants") or f not in fields:
                    continue
                setattr(row, f, R._dt(v) if f in _CONTRACT_DATES else v)
        else:
            # Review (v1.435.0): undo of a pause / resume / stop puts back the
            # STATE the change moved — never the goal's books. Its spend, its
            # checkpoint and its breaker are the engine's (single writer): a
            # stop's cancelled run settled its spend AFTER the snapshot, and
            # writing the snapshot back erased that spend (the budget gate then
            # allowed more) and put back a `running_session_id` for a run that
            # had ended (the next boot read it as interrupted: a false breaker
            # strike). A tripped goal gets its breaker back with its state, so
            # it says why it stopped.
            row.state = str(prior.get("state") or row.state)
            if row.state == "tripped" and prior.get("breaker_json"):
                row.breaker_json = str(prior.get("breaker_json"))
            from ..core.ids import utcnow

            row.updated_at = utcnow()
        db.add(row)
        db.commit()
    if not recreated and prior.get(REPORT_CHANGE_MARK):
        # Idea 39 (39-a): the Undo of a report_here change puts back the chat
        # it reported to (a chat that is gone since stays unset).
        R._restore_report_target(platform, "goal", goal_id, prior)
    try:
        # The store's schedule rule (enabled IFF active) — the same call every
        # state door ends in.
        store._sync_schedule(goal_id, str(prior.get("schedule") or ""), str(prior.get("state") or ""))
    except Exception:  # noqa: BLE001 — the row is back; the schedule re-syncs on the next state change
        log.warning("goal %s: schedule not re-synced after undo", goal_id, exc_info=True)
    # Only an undo of a DELETE gives always-allows back (the delete took
    # them). An undo of a pause or a stop must never give back one the user
    # revoked on the Autonomy page since (review: that widened what runs
    # without a card).
    given_back = _unrevoke_grants(platform, list(prior.get("grants") or [])) if recreated else 0
    name = prior.get("name") or goal_id
    note = ""
    try:
        running = json.loads(prior.get("checkpoint_json") or "{}").get("running_session_id")
    except (TypeError, ValueError, AttributeError):
        running = ""
    if running and state_now == "stopped":
        note = " (a run that was stopped is not started again)"
    extra = f", and gave back {given_back} always-allow{'s' if given_back != 1 else ''}" if given_back else ""
    return f"undo: {'recreated' if recreated else 'restored'} the goal from chat '{name}'{extra}{note}"


def _unrevoke_grants(platform: Any, grant_ids: list[str]) -> int:
    """Give back the always-allows a goal delete took back (those revoked
    since the snapshot). Blocking; never raises."""
    store = getattr(platform, "grants", None)
    if store is None or not grant_ids:
        return 0
    try:
        from ..core.db import session_scope
        from ..core.grants import StandingGrantRecord

        n = 0
        with session_scope(store.engine) as db:
            for gid in grant_ids:
                row = db.get(StandingGrantRecord, str(gid))
                if row is not None and row.revoked_at is not None:
                    row.revoked_at = None
                    db.add(row)
                    n += 1
            db.commit()
        store.refresh()
        return n
    except Exception:  # noqa: BLE001 — the goal is back; its grants can be given again
        log.warning("goal grants not given back after undo", exc_info=True)
        return 0


def _describe_contract(c: dict[str, Any] | None) -> str | None:
    if c is None:
        return None
    words = CONTRACT_STATE_WORDS.get(str(c.get("state") or ""), str(c.get("state") or ""))
    return words + (f", on schedule {c['schedule']}" if c.get("schedule") else "") + R._reports_words(c)


#: Idea 39 (39-a, review): the snapshot marker of a ``goal_update`` that
#: changes where a goal from chat reports, so its Undo puts the chat back
#: (an Undo of a pause alone never re-points reports the user stopped since).
REPORT_CHANGE_MARK = "_report_change"


async def update_goal_contract(
    tool: Any, goal_id: str, args: dict[str, Any], ctx: "ToolContext | None" = None
) -> ToolResult:
    """``goal_update`` on a goal from chat: pause (``status: paused``),
    resume (``active``), stop (``stopped``, or ``abandoned`` — the standing
    goals' word for it). Through the engine's own doors. Idea 39 (39-a):
    ``report_here`` true makes each try report in THIS chat, false is Stop
    reporting; it is checked before anything changes and applied after the
    state change (which is put back if the report change then fails)."""
    platform = tool.platform
    eng = _goal_engine(platform)
    if eng is None:
        return ToolResult(ok=False, error="Goals from chat are not available right now.")
    before = await asyncio.to_thread(snap_goal_contract, platform, goal_id)
    if before is None:
        return ToolResult(ok=False, error=f"There is no goal {goal_id!r} — goal_list shows each goal's id.")
    others = [
        k for k in ("text", "category", "priority", "autonomy_level", "action_budget", "spend_budget")
        if args.get(k) is not None
    ]
    status = str(args.get("status") or "").strip().lower()
    report_here = args.get("report_here")
    if others:
        return ToolResult(ok=False, error=CONTRACT_FIELDS_REFUSAL)
    if not status and not isinstance(report_here, bool):
        return ToolResult(
            ok=False,
            error="Say what to do with it: status paused, active (resume) or stopped, "
            "or report_here (true: report each try in this chat; false: Stop reporting).",
        )
    if status == "done":
        return ToolResult(ok=False, error=CONTRACT_DONE_REFUSAL)
    from ..agents.chat_delivery import report_chat_for, set_report_target

    report_chat = ""
    if report_here is True and ctx is not None:
        # Review: a NEW chat's first turn reads its chat through the turn.
        report_chat = await asyncio.to_thread(report_chat_for, platform, ctx)
    if report_here is True and not report_chat:
        return ToolResult(ok=False, error="Reports go to a saved chat on the Chat page; this one cannot take them.")
    name = before.get("name") or goal_id
    state = str(before.get("state") or "")
    running = ""
    did = ""
    try:
        if not status:
            pass
        elif status == "paused":
            if state == "paused":
                return ToolResult(ok=False, error=f"The goal from chat '{name}' is already paused.")
            await asyncio.to_thread(eng.store.transition, goal_id, "paused")
            did = "Paused"
        elif status == "active":
            if state == "active":
                return ToolResult(ok=False, error=f"The goal from chat '{name}' is already running.")
            if state in ("paused", "tripped"):
                await asyncio.to_thread(eng.resume_goal, goal_id)
                did = "Resumed"
            else:
                # The /reopen door: a stopped, done or failed goal starts again
                # with its failure count cleared.
                await asyncio.to_thread(eng.store.reopen, goal_id)
                did = "Reopened"
        elif status in ("stopped", "abandoned"):
            if state == "stopped":
                return ToolResult(ok=False, error=f"The goal from chat '{name}' is already stopped.")
            try:
                running = str(json.loads(before.get("checkpoint_json") or "{}").get("running_session_id") or "")
            except (TypeError, ValueError, AttributeError):
                running = ""
            await eng.stop_goal(goal_id)
            did = "Stopped"
        else:
            return ToolResult(ok=False, error="status is paused, active or stopped for a goal from chat.")
    except ValueError as exc:
        # The transition guard's own sentence ("a satisfied goal cannot become paused").
        return ToolResult(ok=False, error=f"The goal from chat '{name}' could not change: {exc}.")
    reports = ""
    if isinstance(report_here, bool):
        moved = await asyncio.to_thread(set_report_target, platform.engine, "goal", goal_id, report_chat)
        if not moved.get("ok"):
            if did:
                # The state change is put back: the answer says it failed, so
                # nothing may have changed.
                await asyncio.to_thread(restore_goal_contract, platform, goal_id, before)
            return ToolResult(ok=False, error=str(moved.get("why") or "That could not be changed."))
        reports = " It reports each try in this chat." if report_chat else " It stopped reporting."
    after = await asyncio.to_thread(snap_goal_contract, platform, goal_id)
    note = (
        " Its run in progress was stopped too; Undo puts the goal back but does not start that run again."
        if running
        else ""
    )
    label = f"Goal from chat “{name}”"
    lead = f"{did} the goal from chat '{name}'." if did else f"Goal from chat '{name}':"
    return ToolResult(
        ok=True,
        output=f"{lead}{reports}{note}",
        data={
            "record_change": tool._card(
                "Goal changed", label, _describe_contract(before), _describe_contract(after), f"goal.{goal_id}"
            )
        },
    )


async def delete_goal_contract(tool: Any, goal_id: str) -> ToolResult:
    """``goal_delete`` on a goal from chat: ``DELETE /goals/{id}``'s door
    (the row, its schedule, its always-allows). Undo brings all three back."""
    platform = tool.platform
    eng = _goal_engine(platform)
    if eng is None:
        return ToolResult(ok=False, error="Goals from chat are not available right now.")
    before = await asyncio.to_thread(snap_goal_contract, platform, goal_id)
    if before is None:
        return ToolResult(ok=False, error=f"There is no goal {goal_id!r} — goal_list shows each goal's id.")
    revoked = await asyncio.to_thread(eng.delete_goal, goal_id)
    if revoked is None:
        return ToolResult(ok=False, error=f"There is no goal {goal_id!r} — goal_list shows each goal's id.")
    name = before.get("name") or goal_id
    took = f" and took back its {len(revoked)} always-allow{'s' if len(revoked) != 1 else ''}" if revoked else ""
    return ToolResult(
        ok=True,
        output=f"Deleted the goal from chat '{name}'{took}. Undo brings it back.",
        data={
            "record_change": tool._card(
                "Goal removed", f"Goal from chat “{name}”", _describe_contract(before), None, f"goal.{goal_id}"
            )
        },
    )


# --------------------------------------------------------------------------
# Local only for this chat (D3)
# --------------------------------------------------------------------------

LOCAL_ONLY_HELD = (
    "This chat stays Local only: it used {apps}, whose data stays on your own models, so it "
    "cannot be turned off here."
)
LOCAL_ONLY_OFF_CARD = (
    "Turn Local only off for this chat: from your next message, what you write here may go to a "
    "model off this PC and your own network."
)
_HELD_APP_WORDS = {"microsoft_365": "Microsoft 365"}
#: Owner decision 2 (after the third fix plan): a coding session's Local only
#: is its own switch, in the session's ⋯ menu (dashboard
#: ``components/harness/HarnessSession.tsx``, row ``harness-local-only``; the
#: PATCH ``/build/sessions/{id}`` writes it). Named where it really is.
LOCAL_ONLY_IN_CODE = (
    "This is a coding session: turn Local only on or off from the ⋯ menu at the top of the "
    "session in Chat › Code. Nothing changed."
)


def _chat_state(platform: Any, thread_id: str, turn_id: str) -> dict[str, Any]:
    """What is stored for this chat: ``{"saved", "local_only", "holds",
    "build"}``. A new chat's turn (no saved id) reads the chat the page linked
    the turn to, else its pending holds. Blocking; never raises."""
    from ..providers import egress

    tid = thread_id or egress.linked_thread(turn_id)
    out: dict[str, Any] = {"thread": tid, "saved": False, "local_only": False, "holds": [], "build": False}
    if tid:
        try:
            from ..core.db import session_scope
            from ..core.models import ChatThreadRecord as T, is_build_thread

            with session_scope(platform.engine) as db:
                row = db.get(T, tid)
                if row is not None:
                    out["saved"] = True
                    out["build"] = bool(is_build_thread(row))
                    setup = json.loads(row.setup_json or "{}")
                    if isinstance(setup, dict):
                        out["local_only"] = egress.setup_says_local_only(row.setup_json)
                        out["holds"] = egress.clean_holds(setup.get(egress.HOLDS_KEY))
        except Exception:  # noqa: BLE001 — unread: the scope decides
            pass
    if turn_id:
        out["holds"] = sorted({*out["holds"], *egress.pending_holds_of(turn_id)})
    return out


def _held_words(holds: list[str]) -> str:
    return " and ".join(_HELD_APP_WORDS.get(h, h) for h in holds) or "an app"


def _chat_key(ctx: Any) -> str:
    tid = str(getattr(ctx, "thread_id", "") or "").strip()[:80]
    if tid:
        return tid
    turn = str(getattr(ctx, "turn_id", "") or "").strip()[:80]
    return f"turn:{turn}" if turn else ""


def _key_thread(key: str) -> str:
    """A snapshot key back to the saved chat it names ("" = not saved)."""
    from ..providers import egress

    if key.startswith("turn:"):
        return egress.linked_thread(key[len("turn:"):])
    return key


def snap_chat_local_only(platform: Any, key: str) -> dict[str, Any] | None:
    """``{"local_only": bool}`` for the chat *key* names (a saved id, or
    ``turn:<id>`` for a new chat's first turn). Blocking."""
    tid = _key_thread(key)
    if not tid:
        return None
    st = _chat_state(platform, tid, "")
    return {"local_only": bool(st["local_only"])} if st["saved"] else None


def restore_chat_local_only(platform: Any, key: str, prior: dict[str, Any] | None) -> str:
    """Undo: the chat's Local only back to what it was. A chat an app holds
    stays on. Blocking."""
    from ..providers import egress

    tid = _key_thread(key)
    if not tid:
        raise R.RecordError("this chat was never saved, so there is nothing to put back")
    want = bool((prior or {}).get("local_only"))
    now = egress.set_thread_local_only(platform.engine, tid, want)
    if now is None:
        raise R.RecordError("this chat is gone")
    if now and not want:
        return "undo: this chat stays Local only (an app it used keeps it on your own models)"
    return f"undo: Local only is {'on' if now else 'off'} for this chat again"


class ChatLocalOnlyTool(R._RecordTool):
    """Local only for THIS chat (D3) — see the module docstring."""

    name = LOCAL_ONLY_TOOL
    RECORD = "chat_local_only"
    description = (
        "Turn Local only on or off for THIS chat (`on`: true or false). While it is on, nothing in "
        "this chat goes to a model off this PC or the user's own network without asking first. "
        "Turning it on needs no approval; turning it off always asks the user on a card. It "
        "applies from the user's NEXT message: the reply being written now keeps the setting it "
        "started with — say so. A chat that used an app kept on the user's own models "
        "(Microsoft 365) stays Local only. For NEW chats the setting is \"Start new chats local "
        "only\" (config_set chat_new_local_only), not this tool. Never tell the user Local only is "
        "on unless this tool said it is."
    )
    input_schema = {
        "type": "object",
        "properties": {"on": {"type": "boolean"}},
        "required": ["on"],
    }
    #: How long an OFF call's card stays the one that lets it run.
    _ASKED_S = 3600.0

    def __init__(self, platform: Any) -> None:
        super().__init__(platform)
        #: OFF calls whose spend check showed the card, by call key — execute
        #: turns Local only off ONLY for one of these (the card is the rule,
        #: not a race: a chat that became Local only after a check that said
        #: "nothing to change" is never turned off uncarded).
        self._asked: dict[str, float] = {}

    @staticmethod
    def _call_key(ctx: ToolContext) -> str:
        return json.dumps(
            [
                str(getattr(ctx, "session_id", "") or ""),
                str(getattr(ctx, "turn_id", "") or ""),
                str(getattr(ctx, "thread_id", "") or ""),
            ]
        )

    def _note_asked(self, ctx: ToolContext) -> None:
        now = time.monotonic()
        for k in [k for k, exp in self._asked.items() if exp < now]:
            self._asked.pop(k, None)
        if len(self._asked) > 200:
            self._asked.pop(next(iter(self._asked)))
        self._asked[self._call_key(ctx)] = now + self._ASKED_S

    def _was_asked(self, ctx: ToolContext) -> bool:
        exp = self._asked.pop(self._call_key(ctx), None)
        return exp is not None and exp >= time.monotonic()

    async def _state(self, ctx: ToolContext) -> tuple[dict[str, Any], bool]:
        """``(stored state, effective on now)`` — on now when this turn runs
        Local only OR the saved chat says so."""
        from ..providers import egress

        st = await asyncio.to_thread(
            _chat_state,
            self.platform,
            str(getattr(ctx, "thread_id", "") or "").strip()[:80],
            str(getattr(ctx, "turn_id", "") or "").strip()[:80],
        )
        return st, bool(egress.current_scope().on or st["local_only"] or st["holds"])

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """Turning it OFF asks every time (once only, every posture, past every
        grant); turning it on — or a call that will change nothing — has no
        opinion (the tool's own allow tier). Outside a chat on the Chat page
        there is no card at all (review): "nothing to ask" (``ask=False`` —
        the verdict a call that will change nothing gets) and execute refuses
        in words, so an agent run never parks this ask where the phone could
        answer it, whatever tier the user gave the tool."""
        if _chat_page_problem(ctx, "Local only"):
            self._asked.pop(self._call_key(ctx), None)
            return SpendCheck(ask=False)
        if args.get("on") is not False:
            return None
        try:
            st, on_now = await self._state(ctx)
        except Exception:  # noqa: BLE001 — unknown: ask
            st, on_now = {"holds": [], "build": False}, True
        if st["holds"] or not on_now or st["build"]:
            self._asked.pop(self._call_key(ctx), None)  # no card: nothing may turn it off
            return None  # execute refuses in words; nothing changes
        self._note_asked(ctx)
        return SpendCheck(
            ask=True, words=LOCAL_ONLY_OFF_CARD, once_only=True, attended_only=True, reason=LOCAL_ONLY_REASON
        )

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        R._CHANGE_ID.set("")  # never a previous call's id on this card
        R._PENDING.set(None)
        key = _chat_key(ctx)
        if not key or not isinstance(args.get("on"), bool):
            return None
        try:
            _st, on_now = await self._state(ctx)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        change_id = new_id("rec")
        R._CHANGE_ID.set(change_id)
        desc = {"record": self.RECORD, "name": key, "prior": {"local_only": on_now}, "change_id": change_id}
        return {"kind": R.RECORD_RESTORE, "pre_inline": json.dumps(desc)}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..providers import egress

        on = args.get("on")
        if not isinstance(on, bool):
            return ToolResult(ok=False, error="Say on: true or on: false.")
        problem = _chat_page_problem(ctx, "Local only")
        if problem:
            self._asked.pop(self._call_key(ctx), None)
            return ToolResult(ok=False, error=problem + " Nothing changed.")
        thread_id = str(getattr(ctx, "thread_id", "") or "").strip()[:80]
        turn_id = str(getattr(ctx, "turn_id", "") or "").strip()[:80]
        if not thread_id and not turn_id:
            return ToolResult(ok=False, error="Local only can be changed from the chat page only.")
        st, on_now = await self._state(ctx)
        if st["build"]:
            return ToolResult(ok=False, error=LOCAL_ONLY_IN_CODE)
        if not on and st["holds"]:
            return ToolResult(ok=False, error=LOCAL_ONLY_HELD.format(apps=_held_words(st["holds"])))
        if on and on_now:
            return ToolResult(ok=False, error="This chat is already Local only.")
        if not on and not on_now:
            return ToolResult(ok=False, error="This chat is not Local only.")
        if not on and not self._was_asked(ctx):
            # Off is never uncarded: only a call whose own check showed the
            # card (and the user allowed it) gets here.
            return ToolResult(
                ok=False,
                error="Turning Local only off needs the user's OK on a card. Nothing changed; ask again.",
            )
        scope_on = bool(egress.current_scope().on)
        target = st["thread"] if st["saved"] else ""
        if not target and turn_id:
            target = egress.note_pending_local_only(turn_id, on)
        if target:
            stored = await asyncio.to_thread(egress.set_thread_local_only, self.platform.engine, target, on)
            if stored is None:
                return ToolResult(ok=False, error="Local only could not be saved for this chat. Nothing changed.")
            if stored is not on:
                return ToolResult(ok=False, error=LOCAL_ONLY_HELD.format(apps=_held_words(st["holds"])))
        if on:
            held = ""
            if not scope_on and turn_id:
                # Review (v1.435.0): "from your next message" must hold for a
                # NOTE typed while this (not local) reply runs too — a steer
                # note joins THIS reply. From now on this turn takes none: a
                # note stays in the box (the steer route answers 404) and a
                # note already queued comes back with the reply (unread), so
                # each goes with the next, Local only, message.
                from ..core.turns import TURNS

                if TURNS.hold_steers(turn_id):
                    held = (
                        " Anything you type before this reply ends stays in the box and goes with your "
                        "next message."
                    )
            output = (
                "Local only is now on for this chat. It applies from your next message: this reply was "
                "already being written when you asked, so it finishes the way it started." + held
            )
        else:
            output = (
                "Local only is now off for this chat. It applies from your next message"
                + (": this reply stays Local only until it ends." if scope_on else ".")
            )
        card = self._card(
            "Local only changed", "This chat: Local only", "on" if on_now else "off", "on" if on else "off",
            LOCAL_ONLY_CARD_KEY,
        )
        return ToolResult(ok=True, output=output, data={"local_only": on, "record_change": card})


# --------------------------------------------------------------------------
# the change history (D4)
# --------------------------------------------------------------------------

CHANGE_KINDS = ("any", "setting", "record", "file", "memory", "other")
_RECORD_WORDS = {
    "schedule": "the schedule",
    "workflow": "the saved workflow",
    "channel": "the notification destination",
    "app": "the app",
    "webhook": "the webhook",
    "sentinel": "the watcher",
    "goal": "the goal",
    "goal_contract": "the goal from chat",
    "reflex": "the rule",
    "grant": "the always-allow",
    "chat_local_only": "Local only for a chat",
}
_SCAN_ROWS = 400


def kind_of(journal_kind: str) -> str:
    k = str(journal_kind or "")
    if k in ("setting_restore", "secret_restore"):
        return "setting"
    if k == "record_restore":
        return "record"
    if k.startswith("memory"):
        return "memory"
    if k.startswith("file") or k.startswith("pdf") or k == "files_patch":
        return "file"
    return "other"


def _masked(text: Any, limit: int) -> str:
    from ..detections.redact import mask

    flat = " ".join(str(text or "").split())
    flat = mask(flat)
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _target_words(journal_kind: str, pre_inline: Any) -> str:
    """What the change was made to, by NAME only — never a value, never a
    file's bytes (the envelope's ``data`` is never read)."""
    try:
        meta = json.loads(pre_inline or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        return ""
    k = str(journal_kind or "")
    if k == "record_restore":
        record = str(meta.get("record") or "")
        name = str(meta.get("name") or "")
        if record == "chat_local_only":
            return ""  # change_row says it whole ("turned Local only on for a chat")
        words = _RECORD_WORDS.get(record, record or "a record")
        return f"{words} “{name}”" if name else words
    if k == "setting_restore":
        prior = meta.get("prior")
        keys = sorted(str(x) for x in prior) if isinstance(prior, dict) else []
        return ("settings " if len(keys) > 1 else "the setting ") + ", ".join(keys[:6]) if keys else "a setting"
    if k == "secret_restore":
        name = str(meta.get("secret") or "")
        return f"the key {name}" if name else "a key"
    path = meta.get("path")
    if isinstance(path, str) and path:
        from pathlib import PurePath

        p = PurePath(path)
        return p.name if p.is_absolute() else path
    paths = meta.get("paths")
    if isinstance(paths, list) and paths:
        return f"{len(paths)} file{'s' if len(paths) != 1 else ''}"
    return ""


def _where(session_id: str) -> str:
    s = str(session_id or "")
    if s == "chat":
        return "in chat"
    if s == "settings":
        return "on a settings page"
    if s.startswith("build:"):
        return "in a coding session"
    return "in a task" if s else ""


def _ago(at: datetime | None, now: datetime) -> str:
    if at is None:
        return ""
    when = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    secs = max(0, int((now - when).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 48 * 3600:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} days ago"


#: Review (v1.435.0): what a change made in LOCAL ONLY work says to a turn
#: that may not recall Local only chats (``egress.may_recall_local_only_
#: chats``) — what was done, never what it was done to.
LOCAL_WORK_WORDS = "in a Local only chat (its name stays out of a chat that is not Local only)"
_LOCAL_SLACK_S = 2.0


def _naive(at: Any) -> Any:
    """*at* as naive UTC (SQLite hands back naive datetimes; a caller's may be
    aware)."""
    if isinstance(at, datetime) and at.tzinfo is not None:
        return at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def local_only_rows(db: Any, engine: Any, invs: list[Any]) -> set[str]:
    """The ids of the ledger rows in *invs* made by LOCAL ONLY work (review,
    v1.435.0): a chat turn of a chat switched Local only (its ``AgentRun``
    window, or its durable turn's while it runs), a coding session whose chat
    is, an agent run started Local only (or from such a chat). Settings-page
    rows are never. When the Local only chats cannot be read, every chat /
    session row counts (fail closed). A row no window covers (a turn that
    has not saved its window yet) is not counted. Blocking."""
    from datetime import timedelta

    from sqlmodel import select

    from ..core.models import AgentRun, ChatTurnRecord, Session
    from ..providers import egress

    rows = [i for i in invs if str(getattr(i, "session_id", "") or "") not in ("settings", "")]
    if not rows:
        return set()
    local = egress.local_only_thread_ids(engine)
    if local is None:
        return {i.id for i in rows}
    out: set[str] = set()
    sess_ids = {
        i.session_id for i in rows if i.session_id != "chat" and not str(i.session_id).startswith("build:")
    }
    local_sessions: set[str] = set()
    if sess_ids:
        for sid, opts in db.exec(select(Session.id, Session.options_json).where(Session.id.in_(sess_ids))):  # type: ignore[attr-defined]
            try:
                o = json.loads(opts or "{}")
            except (TypeError, ValueError):
                o = {}
            if isinstance(o, dict) and (
                o.get("local_only") is True or str(o.get("chat_thread_id") or "") in local
            ):
                local_sessions.add(sid)
    windows: list[tuple[datetime, datetime]] = []
    chat_rows = [i for i in rows if i.session_id == "chat" and i.created_at is not None]
    if local and chat_rows:
        slack = timedelta(seconds=_LOCAL_SLACK_S)
        lo = min(_naive(i.created_at) for i in chat_rows) - slack
        hi = max(_naive(i.created_at) for i in chat_rows) + slack
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for start, end in db.exec(
            select(AgentRun.created_at, AgentRun.finished_at)
            .where(AgentRun.session_id == "chat")
            .where(AgentRun.thread_id.in_(local))  # type: ignore[union-attr]
            .where(AgentRun.created_at <= hi)
        ):
            start, end = _naive(start), _naive(end)
            if start is not None and (end is None or end >= lo):
                windows.append((start - slack, (end or now) + slack))
        for start, end, status in db.exec(
            select(ChatTurnRecord.started_at, ChatTurnRecord.updated_at, ChatTurnRecord.status)
            .where(ChatTurnRecord.thread_id.in_(local))  # type: ignore[attr-defined]
            .where(ChatTurnRecord.started_at <= hi)
        ):
            start, last = _naive(start), (now if status == "running" else _naive(end))
            if start is not None and last is not None and last >= lo:
                windows.append((start - slack, last + slack))
    for i in rows:
        sid = str(i.session_id)
        if sid.startswith("build:"):
            if sid[len("build:"):] in local:
                out.add(i.id)
        elif sid == "chat":
            at = _naive(i.created_at)
            if at is not None and any(a <= at <= b for a, b in windows):
                out.add(i.id)
        elif sid in local_sessions:
            out.add(i.id)
    return out


#: Next leaps 40-a: why a change whose before-copy is gone cannot be undone
#: (a cleanup cleared the undo history, or the day is older than it keeps).
NO_COPY_KEPT = "no copy of how it was is kept any more"


def copy_kept(journal: Any, home: Any) -> bool:
    """Next leaps 40-a: whether *journal*'s before-copy is still on this PC —
    False only for a row that refers to a blob under ``<home>/undo/`` that is
    gone. An inline copy, or a kind with no copy, is kept. BLOCKING (one
    stat); never raises (unknown = kept: the Undo itself still refuses)."""
    ref = getattr(journal, "pre_ref", None) if journal is not None else None
    if not ref or home is None:
        return True
    try:
        from pathlib import Path

        return (Path(home) / "undo" / str(ref)).is_file()
    except (OSError, TypeError, ValueError):
        return True


def change_row(
    inv: Any, journal: Any, now: datetime | None = None, *, local_work: bool = False, home: Any = None
) -> dict[str, Any]:
    """One change in plain words: ``{id, at, ago, where, kind, what, state,
    why}`` — ``state`` is ``can`` (can be undone), ``undone`` or ``cannot``.
    ``local_work`` (review): made in Local only work — ``what`` keeps its
    name for the person's card, ``what_safe`` is what a turn that may not
    recall Local only chats is told. ``target`` (next leaps 40-a) is the
    name alone ("notes.txt", "the schedule “nightly”"), "" when none;
    ``home`` (40-a): a row whose before-copy is gone from ``<home>/undo/``
    reads ``cannot`` (:data:`NO_COPY_KEPT`), never an Undo that would fail."""
    from ..agents.mission import fallback_line

    now = now or datetime.now(timezone.utc)
    jkind = getattr(journal, "kind", "") if journal is not None else ""
    target = _target_words(jkind, getattr(journal, "pre_inline", None) if journal is not None else None)
    did = fallback_line(str(inv.tool or ""), True)
    target = _masked(target, 120) if target else ""
    what = f"{did}: {target}" if target else did
    if target and jkind in ("setting_restore", "secret_restore") and did.startswith("used "):
        # Next leaps 40-a: the settings page's and the key card's own rows
        # read "changed the setting …", never "used update settings: …".
        what = f"changed {target}"
    if inv.tool == LOCAL_ONLY_TOOL:
        try:
            was_on = bool((json.loads(getattr(journal, "pre_inline", None) or "{}").get("prior") or {}).get("local_only"))
        except (TypeError, ValueError, AttributeError):
            was_on = None
        if was_on is not None:
            what = f"turned Local only {'off' if was_on else 'on'} for a chat"
    rev = (inv.reversibility or "").lower()
    if inv.undone_at is not None:
        state, why = "undone", "already undone"
    elif journal is None:
        state, why = "cannot", "Iron Jarvis kept no way back for it"
    elif rev == Reversibility.IRREVERSIBLE.value:
        state, why = "cannot", "it left this PC (a message sent or money spent)"
    elif not bool(getattr(journal, "reversible", False)):
        state, why = "cannot", "Iron Jarvis kept no safe way back for it"
    elif not copy_kept(journal, home):
        state, why = "cannot", NO_COPY_KEPT
    else:
        state, why = "can", ""
    at = inv.created_at
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return {
        "id": inv.id,
        "tool": inv.tool,
        "at": at.isoformat() if at is not None else None,
        "ago": _ago(at, now),
        "where": _where(inv.session_id),
        "session_id": str(inv.session_id or ""),
        "kind": kind_of(jkind),
        "target": target,
        "what": what,
        "what_safe": f"{did} {LOCAL_WORK_WORDS}" if local_work else what,
        "local_work": bool(local_work),
        "summary": "" if local_work else _masked(inv.output, 160),
        "state": state,
        "why": why,
    }


def _for_model(row: dict[str, Any], recall_local: bool) -> dict[str, Any]:
    """*row* as a turn is told it: a Local only change's name withheld from a
    turn that may not recall Local only chats."""
    if recall_local or not row.get("local_work"):
        return row
    out = {**row, "what": row["what_safe"], "target": ""}
    if isinstance(row.get("source"), dict):
        # Next leaps 40-a: the run or chat it came from is named no more
        # than the change itself is.
        out["source"] = {**row["source"], "name": LOCAL_SOURCE_WORDS}
    return out


#: Next leaps 40-a: where a Local only change came from, as a turn that may
#: not recall Local only chats is told it.
LOCAL_SOURCE_WORDS = "Local only work"


def list_changes(
    engine: Any,
    *,
    hours: int = 24,
    kind: str = "any",
    limit: int = 20,
    day: Any = None,
    session_ids: Any = None,
    home: Any = None,
) -> tuple[list[dict[str, Any]], int]:
    """The journaled changes of the last *hours*, newest first: ``(rows,
    total in the window)``. Next leaps 40-a: ``day`` (a ``date``) reads that
    LOCAL day instead (``records_day.day_bounds``), ``session_ids`` narrows
    to those sources (a run and its helpers), ``home`` marks a row whose
    before-copy is gone as ``cannot``. A change that left this PC is not
    here: ``records_day.cannot_rows`` lists those. Blocking; bounded
    (``_SCAN_ROWS``)."""
    from datetime import timedelta

    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    now = datetime.now(timezone.utc)
    if day is not None:
        from .records_day import day_bounds

        since, until = day_bounds(day)
    else:
        since, until = (now - timedelta(hours=hours)).replace(tzinfo=None), None
    out: list[dict[str, Any]] = []
    total = 0
    with session_scope(engine) as db:
        stmt = (
            select(ToolInvocation, UndoJournal)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.ok == True)  # noqa: E712
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .where(ToolInvocation.created_at >= since)
        )
        if until is not None:
            stmt = stmt.where(ToolInvocation.created_at < until)
        if session_ids is not None:
            stmt = stmt.where(ToolInvocation.session_id.in_(sorted(set(session_ids))))  # type: ignore[attr-defined]
        rows = db.exec(stmt.order_by(ToolInvocation.created_at.desc()).limit(_SCAN_ROWS)).all()  # type: ignore[attr-defined]
        picked = [
            (inv, j)
            for inv, j in rows
            if (kind == "any" or kind_of(j.kind) == kind)
            and (inv.reversibility or "").lower() != Reversibility.IRREVERSIBLE.value
        ]
        total = len(picked)
        picked = picked[:limit]
        local = local_only_rows(db, engine, [inv for inv, _j in picked])
        out = [change_row(inv, j, now, local_work=inv.id in local, home=home) for inv, j in picked]
    return out, total


def find_change(engine: Any, action_id: str = "", kind: str = "any", *, home: Any = None) -> dict[str, Any] | None:
    """The change ``change_undo`` reverses. With an id: that ledger row
    whatever its state (None when there is no such action, or it is itself
    an undo). Without: the NEWEST change that can still be undone, of *kind*
    (next leaps 40-a: with ``home``, one whose before-copy is gone is passed
    over, as it cannot be undone). Blocking."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    with session_scope(engine) as db:
        if action_id:
            inv = db.get(ToolInvocation, action_id)
            if inv is None or inv.undo_of is not None:
                return None
            return change_row(
                inv, db.get(UndoJournal, action_id), local_work=inv.id in local_only_rows(db, engine, [inv]),
                home=home,
            )
        rows = db.exec(
            select(ToolInvocation, UndoJournal)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.ok == True)  # noqa: E712
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .where(ToolInvocation.undone_at == None)  # noqa: E711
            .where(UndoJournal.reversible == True)  # noqa: E712
            .order_by(ToolInvocation.created_at.desc())  # type: ignore[attr-defined]
            .limit(_SCAN_ROWS)
        ).all()
        for inv, j in rows:
            if (inv.reversibility or "").lower() == Reversibility.IRREVERSIBLE.value:
                continue
            if kind != "any" and kind_of(j.kind) != kind:
                continue
            if not copy_kept(j, home):
                continue
            return change_row(inv, j, local_work=inv.id in local_only_rows(db, engine, [inv]), home=home)
    return None


def _kind_arg(args: dict[str, Any]) -> str:
    k = str(args.get("kind") or "any").strip().lower()
    return k if k in CHANGE_KINDS else "any"


#: Next leaps 40-a: what the history says of what has no Undo. They are
#: listed now (``records_day.cannot_rows``), so the note says where.
CANNOT_NOTE = "Commands Iron Jarvis ran and messages it sent have no Undo: they are listed under Can't be undone."
NONE_CANNOT_NOTE = "Commands Iron Jarvis ran and messages it sent have no Undo; there were none in this time."
_STATE_WORDS = {"can": "can be undone", "undone": "already undone", "cannot": "cannot be undone"}


def _group_head(source: dict[str, Any] | None) -> str:
    from .records_day import source_words

    src = source or {}
    if src.get("kind") == "chat":
        return "In chat:"
    if src.get("kind") == "settings":
        return "On a settings page:"
    words = source_words(src) or str(src.get("name") or "") or "Elsewhere"
    return f"From {words}:"


def _safe_source(source: dict[str, Any] | None, rows: list[dict[str, Any]], recall_local: bool) -> dict[str, Any]:
    """*source* as a turn is told it: named no more than its changes are."""
    src = dict(source or {})
    if recall_local or not any(r.get("local_work") for r in rows):
        return src
    return {**src, "kind": "other", "name": LOCAL_SOURCE_WORDS, "started": None}


class ChangesListTool(Tool):
    """What Iron Jarvis changed recently — read-only (D4); one day, or one
    run, with what cannot be undone (next leaps 40-a)."""

    name = "changes_list"
    description = (
        "List what Iron Jarvis changed — settings, keys (by name), records (schedules, "
        "workflows, goals, rules…), files and memory notes — newest first, each with its id, when, "
        "where it came from (chat, a settings page, a run, a schedule, a coding session) and whether "
        "it can still be undone, and what cannot be undone (messages sent, commands run). "
        "`hours` (default 24) is how far back; `date` (YYYY-MM-DD, today, yesterday) lists that whole "
        "day instead, grouped by where each change came from; `run` lists what one run changed (its "
        "id, or the time it started: \"the 3 pm run\", on `date`, default today); `kind` narrows it "
        "(setting, record, file, memory). Names and plain words only, never a value or a file's "
        "contents. To undo one, call change_undo with its id; several, with action_ids; everything "
        "one run changed, with run."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "hours": {"type": "integer", "minimum": 1, "maximum": 720},
            "date": {"type": "string"},
            "run": {"type": "string"},
            "kind": {"type": "string", "enum": list(CHANGE_KINDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import records_day as RD

        try:
            hours = max(1, min(int(args.get("hours") or 24), 720))
            limit = max(1, min(int(args.get("limit") or 20), 50))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="hours and limit are whole numbers.")
        kind = _kind_arg(args)
        day = None
        if str(args.get("date") or "").strip():
            day = RD.parse_day(args.get("date"))
            if day is None:
                return ToolResult(ok=False, error="date is a day as YYYY-MM-DD, or today, or yesterday.")
        from ..providers import egress

        recall_local = egress.may_recall_local_only_chats()
        engine = self.platform.engine
        source = None
        session_ids = None
        run_ref = str(args.get("run") or "").strip()
        if run_ref:
            source, session_ids, problem = await asyncio.to_thread(RD.resolve_run, engine, run_ref, day)
            if problem:
                return ToolResult(ok=False, error=problem)
        listed = await asyncio.to_thread(
            RD.listing, engine, hours=hours, kind=kind, limit=limit, day=day,
            session_ids=session_ids, home=self.platform.config.home, everything=bool(run_ref),
        )
        rows = [_for_model(r, recall_local) for r in listed["rows"]]
        cannot = [_for_model(r, recall_local) for r in listed["cannot"]] if kind == "any" else []
        total = listed["total"]
        if run_ref:
            src_safe = _safe_source(source, listed["rows"] + listed["cannot"], recall_local)
            span = RD.source_words(src_safe) or "that run"
        elif day is not None:
            span = f"on {RD.day_words(day)}"
        else:
            span = f"in the last {hours} hour{'s' if hours != 1 else ''}"
        noun = "change" if kind == "any" else f"{kind} change"
        note = CANNOT_NOTE if cannot else NONE_CANNOT_NOTE
        data = {
            "changes": rows, "count": len(rows), "total": total, "hours": hours,
            "cannot": cannot, "more": listed["more"],
            **({"date": day.isoformat()} if day is not None else {}),
            **({"source": _safe_source(source, listed["rows"] + listed["cannot"], recall_local)} if run_ref else {}),
        }
        if not rows and not cannot:
            return ToolResult(ok=True, output=f"No {noun}s {'from' if run_ref else ''} {span}. {note}".replace("  ", " "), data=data)
        lines = [
            f"{noun.capitalize()}s {'from ' if run_ref else ''}{span}, newest first ({len(rows)} of {total}):"
        ]
        if day is None and not run_ref:
            for r in rows:
                head = " · ".join(b for b in (r["id"], r["ago"], r["where"]) if b)
                why = f" ({r['why']})" if r["state"] == "cannot" and r["why"] else ""
                lines.append(f"- {head} — {r['what']} — {_STATE_WORDS[r['state']]}{why}")
        else:
            groups: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                groups.setdefault(r["session_id"], []).append(r)
            for sid, members in groups.items():
                if not run_ref:
                    src = _safe_source(listed["sources"].get(sid), members, recall_local)
                    lines.append(_group_head(src))
                for r in members:
                    head = " · ".join(b for b in (r["id"], r.get("clock") or r["ago"]) if b)
                    why = f" ({r['why']})" if r["state"] == "cannot" and r["why"] else ""
                    lines.append(f"- {head} — {r['what']} — {_STATE_WORDS[r['state']]}{why}")
        if cannot:
            lines.append("Can't be undone (they left this PC or ran):")
            for c in cannot:
                src = _safe_source(listed["sources"].get(c["session_id"]), [c], recall_local)
                where = "" if run_ref else RD.source_words(src)
                head = " · ".join(b for b in (c.get("clock") or "", where) if b)
                lines.append(f"- {head} — {c['what']} ({c['why']})")
        lines.append(note)
        lines.append(
            "To undo one, call change_undo with its id; several, with action_ids (at most "
            f"{RD.CHAT_IDS_MAX}); everything one run changed, with run."
        )
        return ToolResult(ok=True, output="\n".join(lines), data=data)


class ChangeUndoTool(Tool):
    """Undo one change Iron Jarvis made (D4), or many at once (next leaps
    40-a): ALWAYS a card naming each, then THE code ``POST /undo/{id}`` runs
    (``routes.undo.perform_undo``; many go newest first through
    ``routes.undo.undo_many``, the run revert's order) — the Undo the
    receipts, the Settings ledger and ``config_undo`` share."""

    name = "change_undo"
    description = (
        "Undo ONE change Iron Jarvis made — a file edit, a setting, a key, a schedule, workflow, "
        "goal, rule or other record — through the same Undo as the receipts. `action_id` names "
        "it (changes_list shows ids); with none it undoes the NEWEST change that can still be "
        "undone, of `kind` if given (file, setting, record, memory). Or SEVERAL at once: "
        "`action_ids` (at most 50 ids from changes_list), or `run`: everything one run changed, "
        "named by its id or by the time it started (\"the 3 pm run\", on `date`, default today). "
        "The user always approves on ONE card that names every change; what cannot be undone is "
        "said first. Commands, sent messages and money spent cannot be undone. Tell the user what "
        "was put back and what was not, with why."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action_id": {"type": "string"},
            "action_ids": {"type": "array", "items": {"type": "string"}},
            "run": {"type": "string"},
            "date": {"type": "string"},
            "kind": {"type": "string", "enum": list(CHANGE_KINDS)},
        },
    }
    reversibility = Reversibility.IRREVERSIBLE
    #: How long the change a card named stays the one this call undoes.
    _HOLD_S = 600.0

    def __init__(self, platform: Any) -> None:
        self.platform = platform
        #: Per call key, the targets the cards named, oldest first (review:
        #: two undo calls in one round each get their own card, both naming
        #: the newest change; a single slot let the second card's Allow undo
        #: a DIFFERENT, older change than the one it named). A DENIED card's
        #: target stays here unread — :meth:`_pick_held` takes the NEWEST, so
        #: it is never lent to a later card.
        self._held: dict[str, list[tuple[float, dict[str, Any] | None]]] = {}

    @staticmethod
    def _many(args: dict[str, Any]) -> bool:
        """Next leaps 40-a: several at once (``action_ids`` or ``run``)."""
        return args.get("action_ids") not in (None, []) or bool(str(args.get("run") or "").strip())

    @staticmethod
    def _key(args: dict[str, Any], ctx: ToolContext) -> str:
        key = [
            str(getattr(ctx, "session_id", "") or ""),
            str(getattr(ctx, "turn_id", "") or ""),
            str(args.get("action_id") or "").strip(),
            _kind_arg(args),
        ]
        if ChangeUndoTool._many(args):
            ids = args.get("action_ids")
            key += [
                sorted(str(x) for x in ids) if isinstance(ids, list) else str(ids),
                " ".join(str(args.get("run") or "").split()),
                str(args.get("date") or "").strip(),
            ]
        return json.dumps(key)

    async def _target(self, args: dict[str, Any]) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            find_change, self.platform.engine, str(args.get("action_id") or "").strip(), _kind_arg(args),
            home=self.platform.config.home,
        )

    @staticmethod
    def _many_problem(args: dict[str, Any]) -> str:
        """What is wrong with a many-at-once call's arguments ("" = none)."""
        from . import records_day as RD

        ids = args.get("action_ids")
        run = str(args.get("run") or "").strip()
        if ids not in (None, []) and (not isinstance(ids, list) or not all(isinstance(x, str) and x.strip() for x in ids)):
            return "action_ids is a list of change ids (changes_list shows them)."
        if isinstance(ids, list) and len(ids) > RD.CHAT_IDS_MAX:
            return (
                f"At most {RD.CHAT_IDS_MAX} changes can be undone at once by id; this asked for {len(ids)}. "
                "Ask for them in parts, or undo them on Activity › Your day."
            )
        if ids not in (None, []) and run:
            return "Give action_ids or run, not both."
        if str(args.get("date") or "").strip() and RD.parse_day(args.get("date")) is None:
            return "date is a day as YYYY-MM-DD, or today, or yesterday."
        return ""

    async def _many_targets(self, args: dict[str, Any]) -> dict[str, Any]:
        from . import records_day as RD

        ids = args.get("action_ids") if isinstance(args.get("action_ids"), list) else None
        return await asyncio.to_thread(
            RD.many_targets, self.platform.engine, home=self.platform.config.home,
            action_ids=ids, run=str(args.get("run") or "").strip(), day=RD.parse_day(args.get("date")),
        )

    def _hold(self, key: str, target: dict[str, Any] | None) -> None:
        now = time.monotonic()
        for k in list(self._held):
            kept = [(exp, t) for exp, t in self._held[k] if exp >= now]
            if kept:
                self._held[k] = kept
            else:
                self._held.pop(k, None)
        if len(self._held) > 200:
            self._held.pop(next(iter(self._held)))
        slots = self._held.setdefault(key, [])
        slots.append((now + self._HOLD_S, target))
        del slots[:-20]

    def _pick_held(self, key: str) -> tuple[bool, dict[str, Any] | None]:
        """``(found, target)`` — the change the card being answered named:
        the NEWEST held target of these arguments. A round's spend checks all
        run just before its cards (so one round's entries of one key all name
        the same change), and each later round's come after the earlier
        round's answers — so the newest entry is the card being answered.
        Never an older card's (a denied card's target is never undone by a
        later card's Allow; the target is re-read by its own id after)."""
        now = time.monotonic()
        slots = [(exp, t) for exp, t in (self._held.get(key) or []) if exp >= now]
        if not slots:
            self._held.pop(key, None)
            return False, None
        _exp, target = slots.pop()
        if slots:
            self._held[key] = slots
        else:
            self._held.pop(key, None)
        return True, target

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """A card EVERY time, naming the change (once only: a wider answer
        never covers the next undo). A call that will undo nothing (no such
        change, or one that cannot be undone) has no card: it is refused.
        Outside a chat on the Chat page there is no card either (review):
        execute refuses in words, so an agent run never parks an undo where
        the phone could answer it. Next leaps 40-a: MANY at once is ONE card
        that names every change (D-40.1), what cannot be undone first."""
        if _chat_page_problem(ctx, "An undo"):
            return SpendCheck(ask=False)  # no card: execute refuses in words
        if self._many(args):
            return await self._many_check(args, ctx)
        try:
            target = await self._target(args)
        except Exception:  # noqa: BLE001 — unknown: no card, execute reads again and refuses honestly
            return None
        self._hold(self._key(args, ctx), target)
        if target is None or target["state"] != "can":
            return SpendCheck(ask=False)
        when = ", ".join(b for b in (target["ago"], target["where"]) if b)
        words = f"Undo this change: {target['what']}" + (f" ({when})" if when else "") + "."
        return SpendCheck(ask=True, words=words[:400], once_only=True, attended_only=True, reason=UNDO_REASON)

    async def _many_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck:
        from . import records_day as RD

        key = self._key(args, ctx)
        problem = self._many_problem(args)
        if problem:
            self._hold(key, {"many": True, "asked": False, "problem": problem})
            return SpendCheck(ask=False)
        try:
            targets = await self._many_targets(args)
        except Exception:  # noqa: BLE001 — unknown: no card; execute refuses in words
            log.warning("change_undo: the changes to undo could not be read", exc_info=True)
            self._hold(key, {"many": True, "asked": False, "problem": "The changes could not be read just now."})
            return SpendCheck(ask=False)
        asked = not targets.get("problem") and bool(targets.get("can"))
        self._hold(key, {"many": True, "asked": asked, **targets})
        if not asked:
            return SpendCheck(ask=False)
        words = RD.card_words(
            targets["can"], targets["cannot"], source=targets.get("source"),
            undone=int(targets.get("undone") or 0), other=targets.get("other"),
        )
        return SpendCheck(ask=True, words=words, once_only=True, attended_only=True, reason=UNDO_REASON)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.undo import perform_undo

        problem = _chat_page_problem(ctx, "An undo")
        if problem:
            return ToolResult(ok=False, error=problem + " Nothing was undone.")
        if self._many(args):
            return await self._execute_many(args, ctx)
        d = R._d(self.platform)
        if d is None:
            return ToolResult(ok=False, error="Undo from chat needs the running app.")
        key = self._key(args, ctx)
        found, target = self._pick_held(key)
        if not found:
            target = await self._target(args)
        elif target is not None:
            # Exactly the change the card named, as it stands NOW (undone on
            # a page while the card waited → refused in words below).
            now_row = await asyncio.to_thread(
                find_change, self.platform.engine, target["id"], home=self.platform.config.home
            )
            target = now_row if now_row is not None else target
        aid = str(args.get("action_id") or "").strip()
        if target is None:
            return ToolResult(
                ok=False,
                error=(
                    f"There is no change {aid!r} to undo (changes_list shows the ids)."
                    if aid
                    else "There is no recent change that can still be undone."
                ),
            )
        from ..providers import egress as _egress

        said = _for_model(target, _egress.may_recall_local_only_chats())["what"]
        if target["state"] == "undone":
            return ToolResult(ok=False, error=f"That change was already undone: {said}.")
        if target["state"] == "cannot":
            return ToolResult(
                ok=False, error=f"That change cannot be undone ({target['why']}): {said}."
            )
        try:
            out = await perform_undo(d, target["id"])
        except HTTPException as exc:
            return ToolResult(ok=False, error=f"It could not be undone: {exc.detail}")
        return ToolResult(
            ok=True,
            output=f"Undone: {said}.",
            data={
                "undone": target["id"],
                "tool": target["tool"],
                "what": said,
                "undo_invocation_id": out.get("undo_invocation_id"),
            },
        )

    async def _execute_many(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Next leaps 40-a: exactly the changes the ONE card named, newest
        first, through the one Undo (``routes.undo.undo_many``). With no card
        held for these arguments nothing is undone (fail closed)."""
        from ..daemon.routes.undo import undo_many
        from ..providers import egress as _egress
        from . import records_day as RD

        problem = self._many_problem(args)
        if problem:
            return ToolResult(ok=False, error=problem + " Nothing was undone.")
        d = R._d(self.platform)
        if d is None:
            return ToolResult(ok=False, error="Undo from chat needs the running app.")
        found, held = self._pick_held(self._key(args, ctx))
        if not found or not isinstance(held, dict) or not held.get("many"):
            return ToolResult(ok=False, error="Nothing was undone: no card named these changes. Ask again.")
        recall_local = _egress.may_recall_local_only_chats()
        if not held.get("asked"):
            why = str(held.get("problem") or "")
            if not why:
                gone = held.get("cannot") or []
                why = "Nothing there can still be undone" + (
                    ": " + "; ".join(f"{_for_model(c, recall_local)['what']} ({c.get('why')})" for c in gone[:10])
                    if gone else "."
                )
                why = why if why.endswith(".") else why + "."
            return ToolResult(ok=False, error=why)
        can = list(held.get("can") or [])
        by_id = {r["id"]: r for r in can}
        out = await undo_many(d, [r["id"] for r in can], pressed=False)
        reverted = [{**by_id.get(x["id"], {}), **x} for x in out["reverted"]]
        skipped = [{**by_id.get(x["id"], {}), **x} for x in out["skipped"]]
        words = RD.put_back_words(reverted, skipped, safe=not recall_local)
        gone = held.get("cannot") or []
        if gone:
            words += " Left as they are (they can't be undone): " + "; ".join(
                _for_model(c, recall_local)["what"] for c in gone[:20]
            ) + "."
        data = {
            "undone": [x["id"] for x in reverted],
            "skipped": [
                {"id": x["id"], "what": _for_model(x, recall_local).get("target") or _for_model(x, recall_local).get("what"),
                 "reason": x.get("reason")}
                for x in skipped
            ],
            "put_back": len(reverted),
        }
        if not reverted:
            return ToolResult(ok=False, error=words, data=data)
        return ToolResult(ok=True, output=words, data=data)


def history_tools(platform: Any) -> list[Tool]:
    """This module's tools, for ``records.record_tools``."""
    return [ChatLocalOnlyTool(platform), ChangesListTool(platform), ChangeUndoTool(platform)]


__all__ = [
    "CHANGE_KINDS",
    "HISTORY_TOOL_TIERS",
    "LOCAL_ONLY_CARD_KEY",
    "LOCAL_ONLY_TOOL",
    "ChangeUndoTool",
    "ChangesListTool",
    "ChatLocalOnlyTool",
    "change_row",
    "contract_lines",
    "contract_rows",
    "delete_goal_contract",
    "find_change",
    "history_tools",
    "history_tools_for",
    "kind_of",
    "list_changes",
    "resolve_goal_kind",
    "restore_chat_local_only",
    "restore_goal_contract",
    "snap_chat_local_only",
    "snap_goal_contract",
    "update_goal_contract",
]
