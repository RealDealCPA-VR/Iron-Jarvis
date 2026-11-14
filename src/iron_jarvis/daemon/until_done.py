"""A plain chat reply, and an agent run's answer, is CHECKED AGAIN until what it
says is true (v1.495.0). The coding-session loop of v1.493.0 brought to the
rest of the app.

The owner, 2026-11-14: "i need some form of loop so it doesnt just stop and so
it doesn't claim it cant be done ... dont lie about being done if you are not",
and then "Yes, extend it" (to plain chat and agent runs).

WHAT IS CHECKED. Nothing new is judged here: every reading is one the app
already made, but only as a note under the finished reply. Now it is made when
the reply is ABOUT to end, while rounds are left, and the model is sent back:

- a file the reply says it SAVED that no tool wrote in this reply
  (``chat_turn._claimed_write_names``, the note ``_claimed_write_note`` uses);
- a file the reply says it CHANGED that no file-changing tool changed;
- a file the USER ASKED FOR that no tool wrote (``_creation_honesty_note``'s
  own predicate) — never when the reply ends by asking the user something;
- a setting or record the reply says changed that no call changed
  (``change_claims.claimed_change_note``; chat only);
- a figure in a file this reply wrote that no script or calculator worked out
  (``script_values.unchecked_now``);
- a required file of the task missing, empty or broken on disk
  (``deliverables``; chat only, as before);
- a reply that ENDS BY GIVING UP (``harness/give_up.gives_up``) after it did
  some work with its tools — once, as in a coding session.

NOT here on purpose: a change to the workbook open in Excel that did not land
(``excel_live.unchanged_note`` keeps its note under the reply). Excel itself
refuses most of those (a protected sheet, a cell being typed in), and a model
sent back cannot fix that; it only spent two more rounds before the note.

A problem is put to the model ONLY when the turn holds a tool that can fix it
(a file writer for a missing file, the calculator or a script for a figure):
telling a model to "write it now" with no writer would be an order it cannot
follow. The note authorises nothing: every call it leads to goes through every
gate and card a call goes through.

LIMITS, each an ending (:data:`ENDS`): every problem fixed (``done``), the
model wrote a "Blocked:" line (``blocked``), :data:`MAX_ROUNDS` notes sent
(``limit``), :data:`IDLE_MAX` tries in a row that changed nothing (``stuck``),
fewer than :data:`MIN_ROUNDS_LEFT` rounds left (``steps``). Never in low trust,
never for a reply cut at the output limit, never after the user declined a
call in this reply (their no stands: ``declined``), never for the demo model,
never in the browser sidebar.

WHAT THE USER READS. The notes the app already wrote under a finished reply
are unchanged (they are the record, and say WHAT is wrong). A reply that was
sent back and STILL has a problem gets one more line (:meth:`UntilDone.verdict`):
how many times it was sent back and why it stopped, then "Tell it to keep going
and it will carry on." A reply the loop fixed gets nothing (the notes then find
nothing either).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The most notes one reply is sent (the coding loop's ``AGAIN_MAX_ROUNDS``).
MAX_ROUNDS = 5
#: Tries in a row that changed nothing before the loop gives up.
IDLE_MAX = 2
#: Rounds that must be left after this one for a note to be sent (the model
#: needs one to fix and one to answer).
MIN_ROUNDS_LEFT = 2
#: Tools that only say what Iron Jarvis can do or where something is: running
#: them is not WORK, so "I could not do that here" after only these is an
#: honest limit, never a give-up to send back.
NOT_WORK = frozenset({
    "find_capability", "guide_search", "guide_read", "app_search", "app_status", "config_list",
})

DONE = "done"
BLOCKED = "blocked"
LIMIT = "limit"
STUCK = "stuck"
STEPS = "steps"
DECLINED = "declined"
ENDS = (DONE, BLOCKED, LIMIT, STUCK, STEPS, DECLINED)

#: Why the loop stopped, as the line under the reply says it.
END_WORDS = {
    LIMIT: f"it reached the most tries ({MAX_ROUNDS})",
    STUCK: "two tries in a row changed nothing",
    STEPS: "the reply ran out of steps",
}

NOTE_HEAD = (
    "FROM IRON JARVIS, NOT FROM THE USER. Iron Jarvis checked your answer against what really "
    "happened in this reply, and it is not finished:"
)
NOTE_TAIL = (
    "Fix this now with your tools, then answer again. Do not say a file was saved, or a change "
    "was made, unless a tool did it in this reply. If something outside your reach stops you (a "
    "sign-in, a file or a decision only the user has), end with a line that starts \"Blocked:\" "
    "saying what you tried, exactly what stops you and what the user must do."
)
VERDICT_CARRY_ON = "Tell it to keep going and it will carry on."

_BLOCKED_LINE_RX = re.compile(
    r"^[^\S\n]*(?:[-*]\s*)?(?:\*\*\s*)?blocked\s*(?:\*\*\s*)?:", re.IGNORECASE | re.MULTILINE,
)
_NOTE_RX = re.compile(r"^\s*_Note:\s*", re.IGNORECASE)
#: The user's own message says not to use tools or make a file ("do not call
#: any tool", "don't create a file", "without saving it"): their no stands, and
#: the reply is never pushed to write or change anything. Bounded: linear.
_SAID_NO_RX = re.compile(
    r"\b(?:do\s+not|don['’]?t|no\s+need\s+to|never|without)\s+"
    r"(?:call(?:ing)?|us(?:e|ing)|run(?:ning)?|creat(?:e|ing)|sav(?:e|ing)|writ(?:e|ing)|touch(?:ing)?)"
    r"(?:\s+\w+){0,3}?\s+(?:tools?|files?|documents?|docs?|anything|disk|it)\b",
    re.IGNORECASE,
)


def said_no(request: str) -> bool:
    """Does the user's message say not to use tools or write anything?"""
    return bool(_SAID_NO_RX.search(str(request or "")[:4000]))


def _plain(note: str) -> str:
    """A finished reply's note (``\\n\\n_Note: …_``) as one plain sentence."""
    text = _NOTE_RX.sub("", str(note or "").strip())
    if text.endswith("_"):
        text = text[:-1]
    return text.strip()


def _ends_on_question(reply: str) -> bool:
    try:
        from ..harness.closing_offer import _last_paragraph

        return _last_paragraph(str(reply or "")).rstrip().endswith("?")
    except Exception:  # noqa: BLE001
        return str(reply or "").rstrip().endswith("?")


def _names(items: list[str]) -> str:
    shown = ", ".join(f"`{n}`" for n in items[:3])
    more = len(items) - 3
    return shown + (f" and {more} more" if more > 0 else "")


@dataclass
class Problem:
    """One thing the check found. ``key`` decides progress (the same keys
    twice in a row = nothing changed); ``words`` go to the model."""

    key: str
    words: str


@dataclass
class Facts:
    """What a reply is judged on, all read from the reply's own record."""

    reply: str
    tools_used: list[str]
    armed: "set[str]"
    asked_for_file: bool = False
    #: The user's own words (the chat's newest message, a run's task).
    request: str = ""
    custom_tools: Iterable[str] = ()
    ran_writer: "bool | None" = None
    #: Chat only: the cards the reply's calls produced and the request's
    #: messages (for the change claims).
    config_cards: Iterable[Any] = ()
    history: Iterable[Any] = ()
    #: The figures shelf's context (a chat's ToolContext, a run's session).
    figures_ctx: Any = None
    #: Chat only: the reply's deliverables (``deliverables.TurnDeliverables``).
    deliverables: Any = None
    chat: bool = True


def find_problems(f: Facts) -> list[Problem]:
    """Every problem the reply has now that the turn's tools could fix.
    BLOCKING only for the deliverables (call through ``to_thread``). Never
    raises: a reading that fails finds nothing."""
    from . import chat_turn as CT

    out: list[Problem] = []
    armed = set(f.armed or ())
    writers = sorted(armed & CT._FILE_WRITING_TOOLS)
    changers = armed & CT._FILE_CHANGING_TOOLS
    reply = str(f.reply or "")
    try:
        named, changed = CT._claimed_write_names(
            reply, list(f.tools_used), custom_tools=f.custom_tools, ran_writer=f.ran_writer,
        )
        if named and writers:
            one = len(named) == 1
            out.append(Problem(
                "claimed_file:" + "|".join(sorted(named)),
                f"Your answer says {_names(named)} {'is' if one else 'are'} saved, but no tool "
                f"wrote {'it' if one else 'them'} in this reply, so {'it does' if one else 'they do'} "
                f"not exist. Write {'it' if one else 'them'} now with {', '.join(writers)}.",
            ))
        if changed and changers:
            one = len(changed) == 1
            out.append(Problem(
                "changed_file:" + "|".join(sorted(changed)),
                f"Your answer says you changed {_names(changed)}, but no tool that changes "
                f"files succeeded in this reply, so {'that file is' if one else 'those files are'} "
                "unchanged. Make the change now with your tools.",
            ))
    except Exception:  # noqa: BLE001
        log.debug("until-done: the file claims could not be read", exc_info=True)
    try:
        if (
            f.asked_for_file and writers and not (set(f.tools_used) & CT._FILE_CHANGING_TOOLS)
            and not CT._ran_writer(f.ran_writer, list(f.tools_used), f.custom_tools)
            and not _ends_on_question(reply)
            and not any(p.key.startswith("claimed_file:") for p in out)
        ):
            out.append(Problem(
                "asked_file",
                "The user asked for a file, and no tool wrote one in this reply. Write it now "
                f"with {', '.join(writers)}: describing the file in your answer does not make it.",
            ))
    except Exception:  # noqa: BLE001
        log.debug("until-done: the asked-for file could not be read", exc_info=True)
    if f.chat and armed:
        try:
            from .change_claims import claimed_change_note

            said = _plain(claimed_change_note(
                reply, list(f.tools_used), list(f.config_cards or ()),
                history=list(f.history or ()), custom_tools=list(f.custom_tools or ()),
            ))
            if said:
                out.append(Problem(
                    "claimed_change:" + said[:200],
                    f"Iron Jarvis's own check says: {said} Make the change now with the right "
                    "tool, or say plainly in your answer that it was not made.",
                ))
        except Exception:  # noqa: BLE001
            log.debug("until-done: the change claims could not be read", exc_info=True)
    if armed & {"calculate", "tool_script"} and f.figures_ctx is not None:
        try:
            from ..tools.script_values import unchecked_now

            for path, figures in list(unchecked_now(f.figures_ctx).items())[:3]:
                name = path.replace("\\", "/").rstrip("/").rpartition("/")[2] or path
                one = len(figures) == 1
                shown = ", ".join(figures[:5])
                out.append(Problem(
                    f"figures:{path}:{'|'.join(figures[:20])}",
                    f"`{name}` holds {'a figure' if one else 'figures'} that no script or "
                    f"calculator worked out: {shown}. Work {'it' if one else 'them'} out now with "
                    "`calculate` or a tool script; if the result differs, write the file again "
                    "with it. A figure the user gave you can stay as it is.",
                ))
        except Exception:  # noqa: BLE001
            log.debug("until-done: the figures could not be read", exc_info=True)
    if f.chat and f.deliverables is not None and armed:
        try:
            lines, sig = f.deliverables.open_now()
            if lines:
                out.append(Problem("deliverables:" + sig, lines))
        except Exception:  # noqa: BLE001
            log.debug("until-done: the deliverables could not be read", exc_info=True)
    return out


@dataclass
class UntilDone:
    """One reply's (or one run's) check-again loop. Both chat lanes and the
    agent runtime hold one; :meth:`check` is asked each time the model is
    about to end on words."""

    chat: bool = True
    notes: int = 0
    idle: int = 0
    ended: str = ""
    gave_up_fired: bool = False
    last_keys: "frozenset[str] | None" = None
    open: list[str] = field(default_factory=list)
    #: The user (or the permission table) refused a call in this reply.
    declined: bool = False

    async def check(
        self,
        facts: Facts,
        *,
        round_index: int,
        rounds: int,
        low_trust: bool = False,
        truncated: bool = False,
    ) -> str:
        """The note to send the model, or "" (let the reply end). Never
        raises."""
        try:
            return await self._check(
                facts, round_index=round_index, rounds=rounds, low_trust=low_trust, truncated=truncated,
            )
        except Exception:  # noqa: BLE001 — a check never breaks a reply
            log.debug("until-done check failed", exc_info=True)
            return ""

    async def _check(self, facts: Facts, *, round_index: int, rounds: int,
                     low_trust: bool, truncated: bool) -> str:
        import asyncio

        if low_trust or truncated:
            return ""
        if self.declined or said_no(facts.request):
            self.ended = self.ended or DECLINED
            return ""
        reply = str(facts.reply or "")
        if self.notes and _BLOCKED_LINE_RX.search(reply):
            self.ended = BLOCKED
            self.open = []
            return ""
        problems = await asyncio.to_thread(find_problems, facts)
        gave_up = False
        worked = [t for t in facts.tools_used if t not in NOT_WORK]
        if not self.gave_up_fired and worked and set(facts.armed or ()):
            from ..harness.give_up import gives_up

            gave_up = gives_up(reply)
        if not problems and not gave_up:
            if self.notes:
                self.ended = DONE
            self.open = []
            return ""
        self.open = [p.key.partition(":")[0] for p in problems]
        if int(rounds) - 1 - int(round_index) < MIN_ROUNDS_LEFT:
            self.ended = STEPS
            return ""
        keys = frozenset(p.key for p in problems)
        if self.notes and keys == self.last_keys and not gave_up:
            self.idle += 1
            if self.idle >= IDLE_MAX:
                self.ended = STUCK
                return ""
        else:
            self.idle = 0
        if self.notes >= MAX_ROUNDS:
            self.ended = LIMIT
            return ""
        self.last_keys = keys
        self.notes += 1
        if gave_up:
            self.gave_up_fired = True
        if gave_up and not problems:
            from ..harness.give_up import GIVE_UP_NUDGE

            return GIVE_UP_NUDGE
        lines = [NOTE_HEAD, *(f"- {p.words}" for p in problems)]
        if gave_up:
            lines.append(
                "- Your answer also ends by saying this cannot be done. Try another way first with "
                "your tools before you stop."
            )
        lines.append(NOTE_TAIL)
        if self.notes > 1:
            lines.append(f"(Check {self.notes} of at most {MAX_ROUNDS}.)")
        return "\n".join(lines)

    @property
    def gave_up_only(self) -> bool:
        """The last note sent was the give-up nudge alone."""
        return bool(self.gave_up_fired and not self.last_keys)

    def verdict(self) -> str:
        """The line under a reply that was sent back and still has a problem
        (how many times, why it stopped). "" otherwise."""
        if not self.notes or not self.open or self.ended not in END_WORDS:
            return ""
        times = {1: "once", 2: "twice"}.get(self.notes, f"{self.notes} times")
        return (
            f"\n\n_Checked by Iron Jarvis: this is not finished. It was sent back to fix it {times} "
            f"and stopped there: {END_WORDS[self.ended]}. {VERDICT_CARRY_ON}_"
        )


#: What a run's ledger row says when the user (or nobody, for a night task's
#: helper) refused a call it was asked about (``agents/runtime``'s deny seam).
_DECLINED_MARKS = ("the user declined", "left for the user")


def run_record(engine: Any, session_id: str) -> "tuple[list[str], bool]":
    """BLOCKING. ``(tools that succeeded, declined)`` of an agent run's session,
    from its ledger: the facts an agent run's answer is judged on. A ledger
    that cannot be read answers ``([], True)`` — nothing is pushed on a record
    nobody could read."""
    try:
        from sqlmodel import Session as _DB, select

        from ..core.models import ToolInvocation

        with _DB(engine) as db:
            rows = db.exec(
                select(ToolInvocation.tool, ToolInvocation.ok, ToolInvocation.output)
                .where(ToolInvocation.session_id == str(session_id))
            ).all()
        used = [str(t or "") for t, ok, _o in rows if ok]
        declined = any(
            not ok and any(m in str(out or "").lower() for m in _DECLINED_MARKS) for _t, ok, out in rows
        )
        return used, declined
    except Exception:  # noqa: BLE001
        log.debug("until-done: the run's ledger could not be read", exc_info=True)
        return [], True


async def note_checked_again(platform: Any, *, session_id: str, surface: str, **facts: Any) -> None:
    """ONE ``autonomy.nudged`` event (``core/nudges.CHECKED_AGAIN``) per note
    sent. Never raises."""
    from ..core.nudges import CHECKED_AGAIN, note_nudge

    await note_nudge(getattr(platform, "event_bus", None), CHECKED_AGAIN,
                     session_id=session_id, surface=surface, **facts)


__all__ = [
    "BLOCKED", "DECLINED", "DONE", "ENDS", "Facts", "IDLE_MAX", "LIMIT", "MAX_ROUNDS",
    "MIN_ROUNDS_LEFT", "NOTE_HEAD", "NOTE_TAIL", "Problem", "STEPS", "STUCK", "UntilDone",
    "VERDICT_CARRY_ON", "find_problems", "note_checked_again", "run_record",
]
