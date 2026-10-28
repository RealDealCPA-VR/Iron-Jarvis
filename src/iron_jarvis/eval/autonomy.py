"""How hands-off Jarvis is (v1.402.0, the autonomy metrics).

The user's goal: "give Jarvis a goal, approve an authority boundary once, let
it work autonomously until completion". Three numbers say how close the app
is, each counted deterministically (no model call) from what the app already
records:

1. INTERRUPTIONS PER TASK — every card or question a lane put to the person
   (``interrupt.asked``, published by every asking site through
   ``core.approvals.InterruptNote``), and how many of them could have been
   AVOIDED (:func:`avoidable`).
2. HUMAN NUDGES PER SUCCESSFUL TASK — the lines the person typed only to move
   the work on ("continue", "try again", "still failing", a "yes" to an offer:
   :func:`classify_line`), the questions they sent again word for word or
   asked again with the chat's Try again / an edit (each earlier version is
   kept on the message, :func:`stored_versions`), and the chat's cut-off
   Continue presses (saved on the merged reply as ``continuedPresses``) —
   over the tasks that ended well.
3. TASKS COMPLETED WITHOUT HUMAN INTERVENTION — the share of tasks that ended
   well with no answered card or question, no nudge, no retry, no steer note
   and no Continue press.

WHAT A TASK IS (the one definition):

* A chat conversation or a Build session is split into tasks at each user
  line :func:`classify_line` calls a ``request``; a task is that request and
  every later line up to the next request. The FIRST user line of a
  conversation always opens a task. Lines the person did not type as a new
  ask never open one: a steer note (``steer: true``, counted as a steer), a
  line the daemon wrote (``continuation: true`` in a Build session, a boot's
  carry-on), the chat's hidden Continue turn (``continuation: true`` in a chat
  — a press, counted as a nudge), a nudge, a retry, a thanks.
* A task SUCCEEDS when its LAST reply ended cleanly: no error, no step limit
  (``stepLimit``), no cap met (``capMet``), not interrupted, not cut off
  (``truncated``), not refused, not on an account-limit card, no failed
  hand-off run, and — in Build — its finish check's tests did not fail
  (``finish.tests == "failed"`` fails it, unless ``finish.after == "passed"``:
  the reply fixed them). A task with no reply did not succeed.
* An agent run NOT started from a chat — a schedule, a goal, a mission (an
  objective), an assignment, a Projects task, a rule, the phone, autonomy,
  another computer — is ONE task (``Session`` rows by origin,
  :data:`AGENT_TASK_ORIGINS`); it succeeds when its ``outcome`` is
  ``"completed"``. A follow-up the person sent it (the Continue door) is a
  nudge on that task, never a new task. A run a chat started reports to that
  chat and is part of the chat's task; a Build session's helper is part of
  its session's task; a workflow's steps, a mission's teammates and delegated
  children are parts of something else and are never tasks of their own.
* The SYSTEM's own nudges (``autonomy.nudged``: an approved plan that ran by
  itself, a run that carried on past its step budget once, …) are counted
  apart and never make a task "not hands-off".

AVOIDABLE ("unnecessary") interruptions — conservative, deterministic. An
interruption is avoidable only when the PERSON answered it (in the app or on
the phone) and:

* a CARD they approved for a call whose exact identity (tool + ``args_hash``)
  they had already approved earlier in the same task;
* a CARD they approved for a tool they had granted "for this chat / this run"
  earlier in the same conversation (or run);
* a QUESTION they skipped or handed back ("you decide", "up to you", "your
  call", "whatever", "either", "doesn't matter", picking the first or the
  recommended option — the asker records this as one ``deferred`` flag).

Never avoidable: a PLAN approval (v1.402.0 integrate: it is the one
"approve once" the user's goal asks for — the boundary itself, not an
interruption of the work), a once-only SAFETY card (a commit or push, a
project's command lines, Local only's ask-first, a priced call, a browser
risk check), a denial, a card nobody answered, a card settled by a grant.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

#: Classifications of a user line.
NUDGE = "nudge"
RETRY = "retry"
REQUEST = "request"
#: A thanks / "great" / "got it": neither a new task nor a nudge.
ACK = "ack"

#: A nudge is short: longer lines carry a new ask.
NUDGE_MAX_WORDS = 12

# --------------------------------------------------------------------------- #
# the lexicon (anchored: a CLAUSE must be one of these, whole)
# --------------------------------------------------------------------------- #

_TAIL = r"(?: (?:please|pls|now|then|again|already|for me|thanks|thank you|yet|too|anyway))*"
_IT = r"(?:it|that|this|them|those|the rest|the job|the task|the work|up|off)"

_CARRY_ON = tuple(re.compile(p) for p in (
    rf"(?:continue|proceed|resume|go on|carry on|keep going|keep working|keep at it|go ahead|"
    rf"go for it|do it|make it so|ship it|finish up|wrap it up|wrap up|do the rest|"
    rf"finish the rest|the rest|same again)(?: with it| with that| on| from there)?{_TAIL}",
    r"(?:continue|carry on|pick up|resume) (?:from )?where you (?:left off|stopped|were)",
    r"(?:continue|carry on) (?:with|from) (?:it|that|this|there|the rest|the task|the work|the plan)",
    rf"(?:finish|complete|fix|check|test|run|redo|retry|recheck|verify|do) {_IT}{_TAIL}",
    rf"(?:try|do it|run it|test it|check it|do that|run that|check that|try it|try that)"
    rf" (?:again|once more){_TAIL}",
    rf"(?:retry|try again|again|one more time|once more){_TAIL}",
    r"(?:yes|yeah|yep|yup|sure|ok|okay|k|alright|all right|absolutely|definitely|of course|"
    r"go|please do|sounds good|that sounds good|good idea|yes please)"
    r"(?: (?:please|do it|go ahead|go for it|continue|proceed|carry on|keep going|do that|"
    r"that'?s fine|thanks|thank you))*",
    r"(?:you(?:'re| are) )?not (?:done|finished|there)(?: yet)?",
    r"(?:don'?t|do not) stop(?: now| yet)?",
    r"next(?: step| one| please)?",
    r"(?:it'?s |that'?s |this is |it is |the tests? (?:are |is )?|tests? (?:are |is )?|"
    r"the build (?:is )?|build (?:is )?)?still (?:broken|failing|failed|not working|wrong|red|"
    r"erroring|crashing|not fixed|there|the same|happening)(?: again)?",
    r"(?:it |that |this |nope |no )?(?:still )?(?:didn'?t|doesn'?t|does not|did not|won'?t|"
    r"still doesn'?t) (?:work|run|pass|compile|build|help|fix it)(?: either)?",
    r"(?:it |that |this |the tests? |tests? |the build )?(?:still )?(?:fails|failed|failing|broke|"
    r"breaks|errors|errored|crashed|crashes)(?: again)?",
    r"(?:same|the same) (?:error|problem|issue|failure|bug|thing|result)(?: again| as before)?",
    r"(?:it'?s |that'?s )?not (?:fixed|working|done|right)(?: yet)?",
    r"(?:nope|no) (?:still|same)(?: thing| error| problem)?",
))

_ACK = tuple(re.compile(p) for p in (
    r"(?:thanks|thank you|thank you so much|thanks a lot|thx|ty|cheers|great|nice|cool|perfect|awesome|"
    r"excellent|amazing|wonderful|brilliant|lovely|good|good job|great job|nice job|good work|"
    r"great work|nice work|well done|got it|lgtm|looks good|looks great|that works|it works|"
    r"works now|works|all good|fine|that'?s fine|that'?s great|that'?s perfect|ok thanks|"
    r"okay thanks|ok thank you|okay thank you|noted|understood|makes sense)"
    r"(?: (?:thanks|thank you|so much|a lot|!))*",
))

#: Words a clause may lead with before its nudge (``"ok continue"``,
#: ``"now carry on"``). Stripped one at a time.
_LEAD = (
    "ok", "okay", "so", "now", "and", "then", "alright", "please", "pls", "well", "hmm",
    "yes", "yeah", "yep", "sure", "great", "perfect", "cool", "nice", "good", "thanks",
    "nope", "no", "ugh", "hm", "sorry", "um", "uh",
)

#: A clause made only of these words is an affirmation (a nudge: it answers an
#: offer to carry on), unless it is also a thanks.
_AFFIRM = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "k", "alright", "please"}

_SPLIT = re.compile(r"\s*(?:[,.;!?:…]+|\s+(?:and|then|but|also)\s+)\s*")
_SPACE = re.compile(r"\s+")


def _norm(text: Any) -> str:
    s = str(text or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"[\"“”`*_~()\[\]{}<>]", " ", s)
    return _SPACE.sub(" ", s).strip()


def normalize_request(text: Any) -> str:
    """The form two lines are compared in for a RETRY: lower case, one space,
    no trailing punctuation."""
    return re.sub(r"[\s.!?…,;:]+$", "", _SPACE.sub(" ", str(text or "").lower()).strip())


def _clause_kind(clause: str) -> str:
    """``"nudge"`` / ``"ack"`` / ``""`` (neither) for one clause."""
    c = clause.strip()
    if not c:
        return "empty"
    for rx in _ACK:
        if rx.fullmatch(c):
            return ACK
    for rx in _CARRY_ON:
        if rx.fullmatch(c):
            return NUDGE
    words = c.split(" ")
    affirmed = False
    while words and words[0] in _LEAD:
        affirmed = affirmed or words[0] in _AFFIRM
        words = words[1:]
    rest = " ".join(words)
    if not rest:
        return NUDGE if affirmed else ACK
    for rx in _CARRY_ON:
        if rx.fullmatch(rest):
            return NUDGE
    for rx in _ACK:
        if rx.fullmatch(rest):
            return NUDGE if affirmed else ACK
    return ""


#: The words of a BARE affirmation ("ok", "yes please", "sure, thanks"): it
#: moves the work on only when it answers an offer.
_BARE_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "k", "alright", "all", "right", "absolutely",
    "definitely", "of", "course", "please", "sounds", "good", "great", "cool", "nice", "perfect", "fine",
    "awesome", "thanks", "thank", "you", "idea", "that",
})
_BARE_AFFIRM = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "k", "alright", "absolutely",
                          "definitely", "course", "please", "sounds"})
#: How far back from a reply's end an offer ("Want me to …?") is looked for.
OFFER_TAIL_CHARS = 300


def bare_affirmation(text: Any) -> bool:
    """A line made only of yes / ok / sure / please (and a thanks)."""
    words = re.findall(r"[a-z']+", _norm(text))
    return bool(words) and all(w in _BARE_WORDS for w in words) and any(w in _BARE_AFFIRM for w in words)


def ends_on_offer(reply: Any) -> bool:
    """Did a reply end by asking something ("Want me to add tests?")? A "?"
    in its last :data:`OFFER_TAIL_CHARS` characters."""
    return "?" in str(reply or "").rstrip()[-OFFER_TAIL_CHARS:]


def classify_line(text: Any, earlier_requests: Iterable[str] = (), *, after: Any = None) -> str:
    """One user line → ``"nudge"`` | ``"retry"`` | ``"request"`` (and
    ``"ack"`` for a thanks, which is neither a new task nor a nudge).

    * ``retry`` — the line, normalised (:func:`normalize_request`), equals an
      earlier request of the same conversation (Build's Retry re-sends it).
    * ``nudge`` — at most :data:`NUDGE_MAX_WORDS` words, and EVERY clause is
      one of the anchored nudge phrases (continue, keep going, go on, carry
      on, proceed, go ahead, yes do it, try again, retry, fix it/that/this,
      check it/that, finish it, still broken/failing, didn't work, same
      error, a bare yes / ok / sure …) or a thanks. "continue the essay with
      a paragraph about tax" is a request: its clause is not a bare phrase.
    * ``ack`` — every clause is a thanks / praise ("thanks!", "great job").
    * ``request`` — anything else.

    ``after`` (v1.402.0 review): the reply the line answers, when known. A
    BARE affirmation ("ok", "yes", "sure, thanks") is a nudge only when that
    reply ended on a question (an offer: "Want me to add tests?"); after a
    reply that asked nothing it is an acknowledgement (``ack``). ``None`` =
    not known: a bare affirmation stays a nudge.
    """
    norm = normalize_request(text)
    if not norm:
        return ACK
    earlier = {normalize_request(e) for e in earlier_requests if str(e or "").strip()}
    if norm in earlier:
        return RETRY
    flat = _norm(text)
    if len(flat.split()) > NUDGE_MAX_WORDS:
        return REQUEST
    kinds = [_clause_kind(c) for c in _SPLIT.split(flat)]
    kinds = [k for k in kinds if k != "empty"]
    if not kinds or any(k == "" for k in kinds):
        return REQUEST
    if NUDGE not in kinds:
        return ACK
    if after is not None and bare_affirmation(text) and not ends_on_offer(after):
        return ACK
    return NUDGE


# --------------------------------------------------------------------------- #
# a question's answer that hands the choice back
# --------------------------------------------------------------------------- #

_DEFER = re.compile(
    r"(?:(?:you|u) (?:decide|choose|pick|tell me|know best)|up to you|(?:it'?s )?your (?:call|choice|pick|decision)|"
    r"whatever(?: you (?:think|want|prefer|like|choose|decide))?(?: is (?:best|fine))?|whatever works|"
    r"either(?: one| way| is fine| works| is ok| is okay)?|any(?: of them| one| is fine)?|anything|"
    r"whichever(?: you (?:think|want|prefer))?|(?:it )?doesn'?t matter|does not matter|(?:i )?don'?t care|"
    r"no preference|not sure|(?:use |go with )?(?:your|the) (?:recommendation|recommended(?: one| option)?|"
    r"default|best guess|judg(?:e)?ment)|(?:use )?your (?:best )?judg(?:e)?ment|(?:the )?default|"
    r"recommended|(?:dealer'?s|your) choice|surprise me|pick one|you pick one|decide for me|"
    r"do what you think is best|what(?:ever)? you think is best|both are fine|all good)"
)


def _option_labels(question: Any) -> tuple[str, list[str]]:
    """``(first label, recommended labels)`` of a question's options."""
    opts = question.get("options") if isinstance(question, dict) else None
    labels: list[str] = []
    recommended: list[str] = []
    for o in opts or []:
        if isinstance(o, dict):
            label = str(o.get("label") or "")
            desc = str(o.get("description") or "")
        else:
            label, desc = str(o or ""), ""
        if not label:
            continue
        labels.append(label)
        if "recommend" in label.lower() or "recommend" in desc.lower():
            recommended.append(label)
    return (labels[0] if labels else ""), recommended


def _defers(words: Any, question: Any) -> bool:
    if isinstance(words, list):
        items = [str(w or "") for w in words if str(w or "").strip()]
        if len(items) != 1:
            return False
        words = items[0]
    text = _norm(words)
    text = re.sub(r"[.!?,;:]+$", "", text).strip()
    if not text:
        return False
    first, recommended = _option_labels(question)
    if first and text == _norm(first):
        return True
    if any(text == _norm(r) for r in recommended):
        return True
    if "(recommended)" in text:
        return True
    for lead in ("ok ", "okay ", "hmm ", "honestly ", "i think ", "eh ", "um ", "uh "):
        if text.startswith(lead):
            text = text[len(lead):]
    return bool(_DEFER.fullmatch(text))


def question_deferred(questions: Any, outcome: Any, answer: Any) -> bool:
    """Did the person hand the model's question back? ``True`` for a skip,
    and for an answer whose EVERY question was answered with "you decide /
    up to you / your call / whatever / either / doesn't matter / the
    recommended one", or with the question's first or recommended option.
    Anything else (their own words, another option, no answer) is ``False``.
    Reduced to this one flag by the asker — the words never leave it."""
    if str(outcome or "") == "skipped":
        return True
    if str(outcome or "") != "answered":
        return False
    qs = [q for q in (questions or []) if isinstance(q, dict)] or [{}]
    if len(qs) == 1 and not isinstance(answer, dict):
        return _defers(answer, qs[0])
    if not isinstance(answer, dict):
        return False
    verdicts = []
    for n, q in enumerate(qs):
        raw = answer.get(q.get("id"), answer.get(str(n), answer.get(q.get("question"))))
        if raw is None:
            continue
        verdicts.append(_defers(raw, q))
    return bool(verdicts) and all(verdicts)


# --------------------------------------------------------------------------- #
# what the stream lane would card (the benchmark's "would have asked")
# --------------------------------------------------------------------------- #


def would_card(name: str, *, permissions: Any, registry: Any = None, harness: bool = False) -> "bool | None":
    """Would the chat/Build stream lane put a CARD in front of this call
    under its DEFAULT posture (``approve_for_me``) with no grant of any kind?

    That posture's rule is the permission engine's own answer (``chat_turn.
    APPROVAL_MODES``: "cards only for what the permission engine itself marks
    unsafe"): the lane cards exactly when ``permissions.mode_for(<the tool's
    permission key>)`` is ``ask`` — an allow-tier tool runs, a deny-tier one
    is refused without a card — plus the once-only plan door
    (``harness.mode.ONCE_ONLY_TOOLS``), which cards in every posture. In a
    coding session's turn (``harness=True``, how the benchmark runs) the
    helper doors are armed granted (``harness.mode.HELPER_DOORS``), as the
    lane's own tool plan does. A priced call's spend check, a page
    screenshot's own-dev-server check and Local only's ask-first are left
    out (they depend on the call's live state; no benchmark task spends money
    or runs Local only). ``None`` for an empty name.
    ``tests/test_autonomy_metrics_v1402.py`` drives the REAL lane to keep
    this rule and the lane's in step."""
    name = str(name or "").strip()
    if not name:
        return None
    from ..tools.permissions import PermissionMode

    perm = name
    if registry is not None:
        try:
            tool = registry.get(name)
            if tool is not None:
                perm = tool.perm_key()
        except Exception:  # noqa: BLE001
            perm = name
    mode = permissions.mode_for(perm, {})
    if mode is PermissionMode.DENY:
        return False
    from ..harness.mode import HELPER_DOORS, ONCE_ONLY_TOOLS

    if name in ONCE_ONLY_TOOLS:
        return True
    if harness and name in HELPER_DOORS:
        return False
    return mode is PermissionMode.ASK


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

#: The origins of an agent run that is a task of its own (``origin == p`` or
#: ``origin.startswith(p + ":")``). ``job:mission-member`` (a mission's
#: teammate), ``job:build-helper`` (a Build session's helper), ``workflow:…``
#: (a step of a workflow run) and runs with no origin (a delegated child) are
#: parts of something else.
AGENT_TASK_ORIGINS = (
    "schedule", "goal", "job:mission", "job:agents", "assignment", "project",
    "reflex", "comm", "autonomy", "remote",
)
#: The marker the Continue door writes into a follow-up run's task
#: (``Orchestrator.continue_session``).
CONTINUE_MARKER = "[Continuing an earlier session. Original task:"

#: Bounds on what one read considers.
MAX_THREADS = 2000
MAX_SESSIONS = 5000
MAX_EVENTS = 50_000
#: Turns whose linked chat is looked up in one read (a new chat's first asks).
MAX_TURNS = 2000
#: An interruption stamped a little BEFORE its task's first line (two clocks)
#: still belongs to it.
SKEW_S = 120.0

_PERSON = ("app", "phone")
_APPROVED = ("once", "conversation", "always", "tab")


def agent_task_origin(origin: Any) -> bool:
    o = str(origin or "")
    return any(o == p or o.startswith(p + ":") for p in AGENT_TASK_ORIGINS)


def _epoch(value: Any) -> "float | None":
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Task:
    surface: str            # chat | build | agent
    key: str                # the conversation's id, or the run's (root) session id
    start: float            # epoch seconds
    end: float = math.inf   # the next task's start (same conversation)
    nudges: int = 0         # nudge lines
    retries: int = 0        # the same request sent again
    presses: int = 0        # Continue presses (a cut-off reply carried on)
    steers: int = 0         # notes typed while it worked
    success: bool = False
    asked: list = field(default_factory=list)
    answered: list = field(default_factory=list)
    steer_times: list = field(default_factory=list)

    @property
    def human_nudges(self) -> int:
        return self.nudges + self.retries + self.presses


def reply_succeeded(line: Any) -> bool:
    """Did this reply line end its work cleanly (see the module docstring)?"""
    if not isinstance(line, dict) or line.get("role") != "assistant":
        return False
    for key in ("error", "errorKind", "errorTitle", "stepLimit", "capMet", "interrupted",
                "truncated", "refused", "panelError", "accountLimit"):
        if line.get(key):
            return False
    if str(line.get("panelKind") or "") == "progress":
        return False
    for key in ("agentRun", "runResult"):
        run = line.get(key)
        if isinstance(run, dict) and str(run.get("status") or "") in ("failed", "cancelled", "canceled"):
            return False
    finish = line.get("finish")
    if isinstance(finish, dict) and str(finish.get("tests") or "") == "failed":
        if str(finish.get("after") or "") != "passed":
            return False
    return True


#: At most this many kept versions are counted on one message (the chat keeps
#: ten, ``dashboard/lib/branches.MAX_BRANCHES``).
MAX_VERSIONS = 10


def stored_versions(line: Any) -> int:
    """How many EARLIER versions a chat message keeps (v1.402.0 review): the
    chat's Try again / Regenerate and an edited-and-resent question do not
    send the question a second time — they fork the conversation and keep
    the old ending on the first message of the live one
    (``dashboard/lib/branches.ts``: ``branch = {tails: [...]}``, exactly one
    ``null`` = the live slot, every other entry a stored ending). Each stored
    ending is one more time the person had to ask: a retry. 0 for anything
    that is not a well-formed fork (the dashboard's own ``validSet`` rule)."""
    if not isinstance(line, dict):
        return 0
    branch = line.get("branch")
    tails = branch.get("tails") if isinstance(branch, dict) else None
    if not isinstance(tails, list) or len(tails) < 2:
        return 0
    if sum(1 for t in tails if t is None) != 1:
        return 0
    if any(t is not None and not (isinstance(t, list) and t) for t in tails):
        return 0
    return min(len(tails) - 1, MAX_VERSIONS)


def split_conversation(messages: list, *, surface: str, key: str, fallback_at: "float | None") -> list[Task]:
    """A conversation's tasks (see the module docstring). ``fallback_at`` is
    the start used for lines saved before they carried ``at``."""
    tasks: list[Task] = []
    requests: list[str] = []
    last_at = fallback_at
    current: "Task | None" = None
    last_reply: Any = None

    def _close() -> None:
        if current is not None:
            current.success = reply_succeeded(last_reply)

    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        at = _epoch(raw.get("at"))
        if at is None:
            at = last_at
        else:
            last_at = at
        role = raw.get("role")
        if role == "assistant":
            if current is not None:
                last_reply = raw
                presses = raw.get("continuedPresses")
                if isinstance(presses, int) and not isinstance(presses, bool) and presses > 0:
                    current.presses += min(presses, 100)
                # A Try again keeps the old reply as a version (a retry).
                current.retries += stored_versions(raw)
            continue
        if role != "user":
            continue
        if raw.get("steer"):
            if current is not None:
                current.steers += 1
                if at is not None:
                    current.steer_times.append(at)
            continue
        if raw.get("continuation") or raw.get("resumedFrom") or raw.get("accountMove"):
            # The chat's hidden Continue turn (a press the merge did not fold
            # into its reply); a line the daemon wrote in a Build session.
            if surface == "chat" and current is not None and str(raw.get("content") or "").startswith(
                "Your last reply was cut off"
            ):
                current.presses += 1
            continue
        kind = REQUEST if current is None else classify_line(
            raw.get("content"), requests,
            # (review) the reply it answers: a bare "ok" after a reply that
            # offered nothing is a thanks, not a nudge.
            after=str((last_reply or {}).get("content") or ""),
        )
        if kind == REQUEST:
            _close()
            if current is not None and at is not None:
                current.end = at
            current = Task(surface=surface, key=key, start=at if at is not None else 0.0)
            tasks.append(current)
            requests.append(str(raw.get("content") or ""))
            last_reply = None
        elif kind == NUDGE:
            current.nudges += 1
        elif kind == RETRY:
            current.retries += 1
        # An edited-and-resent question keeps the earlier one as a version.
        current.retries += stored_versions(raw)
    _close()
    return tasks


def _options(raw: Any) -> dict:
    try:
        opts = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except Exception:  # noqa: BLE001
        return {}
    return opts if isinstance(opts, dict) else {}


def agent_tasks(rows: list[dict]) -> tuple[list[Task], dict[str, Task]]:
    """``(tasks, by_session)`` from Session rows (dicts: id, origin, task,
    outcome, created_at, options_json), oldest first. A follow-up (the
    Continue door's marker in its task) is a nudge on the task it continues,
    and its outcome becomes that task's.

    WHICH task it continues (review): the Continue door writes the earlier
    run's own task into the follow-up (``Original task: <repr>. Prior
    result:`` — ``Orchestrator.continue_session``), so the follow-up is
    matched to THAT run. Every mission has the origin ``job:mission`` (every
    Agents-page job ``job:agents``), so "the newest earlier run of the same
    origin" put mission A's follow-up on mission B and overwrote B's outcome;
    it is only the fallback when no earlier run's task matches."""
    tasks: list[Task] = []
    by_session: dict[str, Task] = {}
    newest_by_origin: dict[str, Task] = {}
    #: per origin: an earlier run's task AS THE CONTINUE DOOR WRITES IT (its
    #: repr) -> the task it belongs to (the newest run wins a tie).
    by_text: dict[str, dict[str, Task]] = {}

    def _continued(origin: str, text: str) -> "Task | None":
        known = by_text.get(origin) or {}
        lead = "Original task: "
        i = text.find(lead)
        if i < 0 or not known:
            return None
        start = i + len(lead)
        j = text.find(". Prior result:", start)
        while j >= 0:
            hit = known.get(text[start:j])
            if hit is not None:
                return hit
            j = text.find(". Prior result:", j + 1)
        return None

    for r in sorted(rows, key=lambda r: _epoch(r.get("created_at")) or 0.0):
        if not agent_task_origin(r.get("origin")):
            continue
        opts = _options(r.get("options_json"))
        if opts.get("chat_thread_id") or opts.get("build_session"):
            continue
        origin = str(r.get("origin") or "")
        ok = str(r.get("outcome") or "") == "completed"
        text = str(r.get("task") or "")
        if CONTINUE_MARKER in text and origin in newest_by_origin:
            root = _continued(origin, text) or newest_by_origin[origin]
            root.nudges += 1
            root.success = ok
            by_session[str(r.get("id"))] = root
            by_text.setdefault(origin, {})[repr(text)] = root
            continue
        t = Task(surface="agent", key=str(r.get("id")), start=_epoch(r.get("created_at")) or 0.0, success=ok)
        tasks.append(t)
        by_session[str(r.get("id"))] = t
        newest_by_origin[origin] = t
        by_text.setdefault(origin, {})[repr(text)] = t
    return tasks, by_session


# --------------------------------------------------------------------------- #
# attribution and the avoidable rules
# --------------------------------------------------------------------------- #


def _task_at(tasks: list[Task], at: float) -> "Task | None":
    """The task of a conversation that was running at ``at``."""
    found = None
    for t in tasks:
        if t.start <= at < t.end:
            found = t
    if found is None and tasks and tasks[0].start - SKEW_S <= at < tasks[0].start:
        found = tasks[0]
    return found


def avoidable(answer: dict, task: Task, *, conversation_grants: set, approved_before: set) -> bool:
    """Is this settled ask an AVOIDABLE interruption (module docstring)?
    ``conversation_grants``: tools granted for the conversation / run before
    it; ``approved_before``: (tool, args_hash) approved earlier in the task."""
    if str(answer.get("actor") or "") not in _PERSON:
        return False
    kind = str(answer.get("kind") or "card")
    decision = str(answer.get("decision") or "")
    tool = str(answer.get("tool") or "")
    if kind == "question":
        return bool(answer.get("deferred")) and decision in ("answered", "skipped", "accept")
    if decision not in _APPROVED:
        return False
    if kind == "plan":
        # v1.402.0 integrate: a plan approval is never avoidable — it is
        # the one "approve once" the user's goal asks for.
        return False
    if answer.get("once_only"):
        return False
    h = str(answer.get("args_hash") or "")
    if h and (tool, h) in approved_before:
        return True
    return tool in conversation_grants


def _rate(n: float, d: float) -> float:
    return round(n / d, 2) if d else 0.0


def _pct(n: float, d: float) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _week_start(at: float) -> str:
    day = datetime.fromtimestamp(at, tz=timezone.utc).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def compute(
    *, threads: list[dict], sessions: list[dict], events: list[dict], days: int, now: float,
    turn_threads: "dict[str, str] | None" = None, truncated: "Iterable[str]" = (),
) -> dict:
    """The metrics from already-read rows (pure). ``threads``: {id, surface,
    messages_json | messages, created_at}; ``sessions``: Session rows as
    dicts; ``events``: {type, payload (dict), created_at}.

    ``turn_threads`` (review): the chat each daemon-run TURN was linked to
    (``ChatTurnRecord``). A NEW chat's first question goes out before its
    first save names the chat (``POST /chat/turns/{id}/thread`` links it
    after), so its cards may be asked with no ``thread_id``; such an ask is
    placed by its own answer's thread, then by any other ask of the same
    turn, then by this map. ``truncated``: which reads hit their bound."""
    cutoff = now - days * 86400.0
    by_key: dict[str, list[Task]] = {}
    all_tasks: list[Task] = []
    for th in threads:
        try:
            msgs = th.get("messages")
            if msgs is None:
                msgs = json.loads(th.get("messages_json") or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(msgs, list):
            continue
        surface = "build" if str(th.get("surface") or "") == "build" else "chat"
        key = str(th.get("id") or "")
        ts = split_conversation(msgs, surface=surface, key=key, fallback_at=_epoch(th.get("created_at")))
        by_key[key] = ts
        all_tasks.extend(ts)
    a_tasks, by_session = agent_tasks(sessions)
    for t in a_tasks:
        by_key.setdefault(t.key, []).append(t)
    all_tasks.extend(a_tasks)

    # The interruptions, oldest first, paired asked → answered by ask id.
    system = Counter()
    asked_at: dict[str, float] = {}
    # (review) WHERE an ask was put when it was asked with no thread (a new
    # chat's first question): its own answer, another ask of the same turn,
    # or the turn's record (``turn_threads``).
    thread_by_ask: dict[str, str] = {}
    thread_by_turn: dict[str, str] = {}
    for ev in events:
        p = ev.get("payload") or {}
        th = str(p.get("thread_id") or "") if isinstance(p, dict) else ""
        if not th:
            continue
        if p.get("ask_id"):
            thread_by_ask.setdefault(str(p["ask_id"]), th)
        if p.get("turn_id"):
            thread_by_turn.setdefault(str(p["turn_id"]), th)
    for turn, th in (turn_threads or {}).items():
        if turn and th:
            thread_by_turn.setdefault(str(turn), str(th))

    def _thread_of(p: dict) -> str:
        th = str(p.get("thread_id") or "")
        if th:
            return th
        ask = str(p.get("ask_id") or "")
        if ask and ask in thread_by_ask:
            return thread_by_ask[ask]
        turn = str(p.get("turn_id") or "")
        return thread_by_turn.get(turn, "") if turn else ""

    ordered = sorted(
        events,
        key=lambda e: _epoch((e.get("payload") or {}).get("at")) or _epoch(e.get("created_at")) or 0.0,
    )
    unattributed = 0
    #: (review) the task each ask was put to, so its ANSWER lands on that
    #: same task even when it came after the person's next request (an
    #: escalated run's card answered late). None = the ask belonged to no
    #: task counted here.
    task_by_ask: "dict[str, Task | None]" = {}
    for ev in ordered:
        p = ev.get("payload") or {}
        if not isinstance(p, dict):
            continue
        at = _epoch(p.get("at")) or _epoch(ev.get("created_at")) or 0.0
        if ev.get("type") == "autonomy.nudged":
            if at >= cutoff:
                system[str(p.get("kind") or "other")] += 1
            continue
        if str(p.get("surface") or "") == "bench" or str(p.get("actor") or "") == "bench":
            continue
        ask_id = str(p.get("ask_id") or "")
        task: "Task | None" = None
        if ev.get("type") == "interrupt.answered" and ask_id in task_by_ask:
            task = task_by_ask[ask_id]
            if task is None:
                continue
        else:
            thread = _thread_of(p)
            if thread and thread in by_key:
                task = _task_at([t for t in by_key[thread] if t.surface != "agent"], at)
            if task is None:
                sid = str(p.get("session_id") or "")
                task = by_session.get(sid) if sid else None
            if task is not None and task.start < cutoff:
                task = None
            if ev.get("type") == "interrupt.asked" and ask_id:
                task_by_ask[ask_id] = task
        if task is None:
            if at >= cutoff and ev.get("type") == "interrupt.asked":
                unattributed += 1
            continue
        if ev.get("type") == "interrupt.asked":
            task.asked.append(p)
            if ask_id:
                asked_at[ask_id] = at
        elif ev.get("type") == "interrupt.answered":
            row = dict(p)
            if ask_id and ask_id in asked_at:
                row["asked_at"] = asked_at[ask_id]
            task.answered.append(row)

    # (review) ONE interruption per thing the person acted on: an ask settled
    # by a GRANT the person made on another card a moment earlier (a run's
    # sibling woken by "Allow for this run", the sidebar's allow-everything
    # switch) is not counted a second time — the card they answered is.
    granted = {
        str(a.get("ask_id"))
        for t in all_tasks for a in t.answered
        if a.get("ask_id") and str(a.get("actor") or "") == "grant"
    }

    def _interrupts(t: Task) -> list:
        return [p for p in t.asked if not (p.get("ask_id") and str(p["ask_id"]) in granted)]

    # The avoidable rules, per conversation (grants outlive one task).
    avoid_by_task: dict[int, int] = {}
    for key, ts in by_key.items():
        grants: set = set()
        for t in sorted(ts, key=lambda t: t.start):
            approved: set = set()
            n = 0
            for a in sorted(t.answered, key=lambda a: _epoch(a.get("at")) or 0.0):
                if avoidable(a, t, conversation_grants=grants, approved_before=approved):
                    n += 1
                if str(a.get("actor") or "") in _PERSON and str(a.get("decision") or "") in _APPROVED:
                    if a.get("args_hash"):
                        approved.add((str(a.get("tool") or ""), str(a["args_hash"])))
                    if a.get("decision") == "conversation" and not a.get("once_only"):
                        grants.add(str(a.get("tool") or ""))
            avoid_by_task[id(t)] = n

    tasks = [t for t in all_tasks if t.start >= cutoff]

    def _helped(t: Task) -> bool:
        if t.human_nudges or t.steers:
            return True
        return any(
            str(a.get("actor") or "") in _PERSON and str(a.get("decision") or "") not in ("stopped", "timeout")
            for a in t.answered
        )

    def _block(ts: list[Task]) -> dict:
        n = len(ts)
        ok = [t for t in ts if t.success]
        hands = [t for t in ok if not _helped(t)]
        asked = sum(len(_interrupts(t)) for t in ts)
        avoid = sum(avoid_by_task.get(id(t), 0) for t in ts)
        human = sum(t.human_nudges for t in ts)
        return {
            "tasks": n, "successful": len(ok), "hands_off": len(hands),
            "hands_off_pct": _pct(len(hands), n),
            "interruptions": asked, "avoidable": avoid,
            "human_nudges": human,
        }

    total = _block(tasks)
    by_kind = Counter(str(p.get("kind") or "card") for t in tasks for p in _interrupts(t))
    by_tool = Counter(str(p.get("tool") or "") for t in tasks for p in _interrupts(t) if p.get("tool"))
    waits = [int(a.get("wait_ms") or 0) for t in tasks for a in t.answered
             if str(a.get("actor") or "") in _PERSON]
    ok_tasks = [t for t in tasks if t.success]
    human_ok = sum(t.human_nudges for t in ok_tasks)

    weeks: list[dict] = []
    # (review) Every week the window touches, from the one holding its first
    # day: ``ceil(days / 7)`` weeks back from this Monday dropped the first
    # day or two of a window that began late in a week, so the weekly rows
    # did not add up to the totals beside them.
    last_monday = datetime.fromisoformat(_week_start(now)).date()
    first_monday = datetime.fromisoformat(_week_start(cutoff)).date()
    n_weeks = max(1, (last_monday - first_monday).days // 7 + 1)
    buckets: dict[str, list[Task]] = {}
    for t in tasks:
        buckets.setdefault(_week_start(t.start), []).append(t)
    for i in range(n_weeks - 1, -1, -1):
        wk = (last_monday - timedelta(days=7 * i)).isoformat()
        weeks.append({"week": wk, **_block(buckets.get(wk, []))})

    return {
        "days": days,
        "tasks": total["tasks"],
        "successful": total["successful"],
        "hands_off": total["hands_off"],
        "hands_off_pct": total["hands_off_pct"],
        "interruptions": {
            "total": total["interruptions"],
            "avoidable": total["avoidable"],
            "per_task": _rate(total["interruptions"], total["tasks"]),
            "avoidable_per_task": _rate(total["avoidable"], total["tasks"]),
            "by_kind": {k: by_kind.get(k, 0) for k in ("card", "question", "plan")},
            "by_tool": [{"tool": k, "count": v} for k, v in by_tool.most_common(10)],
            "median_wait_ms": sorted(waits)[len(waits) // 2] if waits else 0,
            "unattributed": unattributed,
        },
        "nudges": {
            "human": total["human_nudges"],
            "nudge_lines": sum(t.nudges for t in tasks),
            "retries": sum(t.retries for t in tasks),
            "presses": sum(t.presses for t in tasks),
            "steers": sum(t.steers for t in tasks),
            "per_successful_task": _rate(human_ok, len(ok_tasks)),
            "system": {"total": sum(system.values()), "by_kind": dict(sorted(system.items()))},
        },
        "by_surface": {
            s: _block([t for t in tasks if t.surface == s]) for s in ("chat", "build", "agent")
        },
        "weekly": weeks,
        # (review) Which reads hit their bound (``threads`` / ``sessions`` /
        # ``events``): the numbers then count only the newest records of
        # that kind. Empty = everything in the window was read.
        "truncated": sorted({str(x) for x in truncated if str(x or "").strip()}),
    }


def empty(days: int) -> dict:
    """The answer for no data at all (zeros, never an error)."""
    return compute(threads=[], sessions=[], events=[], days=days, now=datetime.now(timezone.utc).timestamp())


def read_metrics(engine: Any, days: int = 30, *, now: "float | None" = None) -> dict:
    """BLOCKING — read the rows (bounded: the newest :data:`MAX_THREADS`
    conversations touched in the window, :data:`MAX_SESSIONS` runs,
    :data:`MAX_EVENTS` events, :data:`MAX_TURNS` turn links) and
    :func:`compute`; a read that hit its bound is named in ``truncated``.
    Callers hop off the loop."""
    from sqlmodel import Session as _DB
    from sqlmodel import select

    from ..core.models import ChatThreadRecord, ChatTurnRecord, EventRecord
    from ..core.models import Session as SessionRow

    days = max(1, min(int(days or 30), 365))
    now_ts = now if now is not None else datetime.now(timezone.utc).timestamp()
    since = datetime.fromtimestamp(now_ts - days * 86400.0, tz=timezone.utc).replace(tzinfo=None)
    with _DB(engine) as db:
        threads = [
            {"id": r[0], "surface": r[1], "messages_json": r[2], "created_at": r[3]}
            for r in db.exec(
                select(
                    ChatThreadRecord.id, ChatThreadRecord.surface,
                    ChatThreadRecord.messages_json, ChatThreadRecord.created_at,
                )
                .where(ChatThreadRecord.updated_at >= since)  # type: ignore[operator]
                .order_by(ChatThreadRecord.updated_at.desc())  # type: ignore[union-attr]
                .limit(MAX_THREADS)
            )
        ]
        sessions = [
            {"id": r[0], "origin": r[1], "task": r[2], "outcome": r[3], "created_at": r[4], "options_json": r[5]}
            for r in db.exec(
                select(
                    SessionRow.id, SessionRow.origin, SessionRow.task, SessionRow.outcome,
                    SessionRow.created_at, SessionRow.options_json,
                )
                .where(SessionRow.created_at >= since)  # type: ignore[operator]
                .where(SessionRow.origin.is_not(None))  # type: ignore[union-attr]
                .order_by(SessionRow.created_at.desc())  # type: ignore[union-attr]
                .limit(MAX_SESSIONS)
            )
        ]
        events = []
        event_rows = 0
        for r in db.exec(
            select(EventRecord.type, EventRecord.payload_json, EventRecord.created_at)
            .where(EventRecord.type.in_(  # type: ignore[union-attr]
                ("interrupt.asked", "interrupt.answered", "autonomy.nudged")
            ))
            .where(EventRecord.created_at >= since)  # type: ignore[operator]
            .order_by(EventRecord.created_at.desc())  # type: ignore[union-attr]
            .limit(MAX_EVENTS)
        ):
            event_rows += 1
            try:
                payload = json.loads(r[1] or "{}")
            except Exception:  # noqa: BLE001
                continue
            events.append({"type": r[0], "payload": payload if isinstance(payload, dict) else {},
                           "created_at": r[2]})
        # (review) The chat a turn was linked to AFTER it started (a new
        # chat's first question): read only for the turns whose asks carry no
        # thread, bounded, from the turn records (kept ~7 days).
        unplaced = list(dict.fromkeys(
            str(e["payload"].get("turn_id"))
            for e in events
            if e["payload"].get("turn_id") and not e["payload"].get("thread_id")
        ))[:MAX_TURNS]
        turn_threads: dict[str, str] = {}
        for i in range(0, len(unplaced), 500):
            chunk = unplaced[i:i + 500]
            for r in db.exec(
                select(ChatTurnRecord.id, ChatTurnRecord.thread_id)
                .where(ChatTurnRecord.id.in_(chunk))  # type: ignore[union-attr]
            ):
                if r[1]:
                    turn_threads[str(r[0])] = str(r[1])
    truncated = [
        name for name, n, cap in (
            ("threads", len(threads), MAX_THREADS),
            ("sessions", len(sessions), MAX_SESSIONS),
            ("events", event_rows, MAX_EVENTS),
        ) if n >= cap
    ]
    return compute(threads=threads, sessions=sessions, events=events, days=days, now=now_ts,
                   turn_threads=turn_threads, truncated=truncated)


__all__ = [
    "ACK", "AGENT_TASK_ORIGINS", "NUDGE", "REQUEST", "RETRY", "Task",
    "agent_tasks", "avoidable", "bare_affirmation", "classify_line", "compute", "empty", "ends_on_offer",
    "normalize_request", "question_deferred", "read_metrics", "reply_succeeded", "split_conversation",
    "stored_versions", "would_card",
]
