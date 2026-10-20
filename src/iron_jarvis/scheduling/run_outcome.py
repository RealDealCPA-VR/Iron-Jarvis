"""What a schedule's last fire came to, in words a person reads (v1.351.0).

THE FAILURE THIS ANSWERS (readiness audit G01/G02). A scheduled run was
recorded ``ok`` from its STATUS alone, so a run whose ask expired unanswered
(``Session.outcome == "needs_you"``) or whose mutating step failed
(``completed_with_failures``) wore a green "✓ ok" and was announced "done" —
three of the last four weekly memory reviews on the user's install. A failed
row read ``RuntimeError: session ended failed: …`` and a good one read the
report's raw markdown (``# Final Report ## What happened…``).

This module is the ONE vocabulary the scheduler writes and the readers count:

* ``last_status`` values — ``ok`` | ``needs_you`` | ``partial`` | ``error`` |
  ``cancelled`` (a run stopped mid-way, by the user or by Emergency stop) |
  ``skipped`` | ``missed`` (the last two written by ``scheduling/service.py``
  and by an Emergency-stop skip) | ``""`` (never fired);
* ``FAILED_STATUSES`` / ``NEEDS_A_LOOK_STATUSES`` — what the Status map's
  Schedules star and the home line count (``routes/system_map.py``,
  ``routes/ui.status_line``);
* the row detail is a PLAIN sentence (``plain_sentence``), a failure reads
  ``The run failed: <reason>`` with no Python class name (``failure_detail``),
  and the notification head says the same thing the row does
  (``delivery_head``).

Pure and never raises: it runs on the scheduler thread inside the fire's own
error handling, where an exception would lose the very record it exists for.
"""

from __future__ import annotations

import re

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_NEEDS_YOU = "needs_you"
STATUS_PARTIAL = "partial"
STATUS_CANCELLED = "cancelled"
STATUS_SKIPPED = "skipped"

#: A row whose last run FAILED (the Status map's "failed on its last run").
#: ``failed`` is not a value the scheduler writes; it is counted too so a row
#: planted by an older build or by hand is never invisible.
FAILED_STATUSES = (STATUS_ERROR, "failed")
#: A row whose last run FINISHED but fell short of the job.
NEEDS_A_LOOK_STATUSES = (STATUS_NEEDS_YOU, STATUS_PARTIAL)
#: Every row the home line offers "N scheduled jobs need a look" for.
SHORT_STATUSES = FAILED_STATUSES + NEEDS_A_LOOK_STATUSES

#: The words on a row Emergency stop kept from starting. (A run it cut off
#: mid-way reads the session's own summary, worded once by the orchestrator:
#: ``agents/orchestrator.EMERGENCY_STOP_STOPPED``.)
EMERGENCY_STOP_SKIP = (
    "Emergency stop is on, so this run was skipped. "
    "Turn it off on the Autonomy page to let schedules run again."
)
#: A run stopped mid-way whose session carries no words of its own.
STOPPED_NO_WORDS = "The run was stopped before it finished."

#: The detail on a needs_you / partial row, BEFORE the run's own sentence:
#: the status word says THAT it fell short, this says WHY in plain words.
NEEDS_YOU_LEAD = "It asked for your OK and nobody answered in time, so some steps did not run."
PARTIAL_LEAD = "Some of its steps failed."

#: A FAILED run whose ask expired (v1.358.0, lane 8A): the row says
#: "Failed", like the run's own badge, and the reason says the ask went
#: unanswered first — the person still learns they were asked.
ASK_EXPIRED_REASON = "it asked for your OK and nobody answered in time."

_FAILED_LEAD = "The run failed: "
#: v1.358.0 (lane 8A): plain words, never "the daemon log" — the app's own
#: name and where a person can look (Status).
_NO_REASON = (
    "The run failed and gave no reason. "
    "Iron Jarvis kept the details in its log; Status shows how the app is doing."
)

_DETAIL_LIMIT = 200
_REASON_LIMIT = 260
_MIN_SENTENCE = 40


def fire_status(outcome: object) -> str:
    """``last_status`` for a fire that returned, from the session's ledger
    verdict (``Session.outcome``) — ``ok`` when there is no verdict to read
    (an event, a goal iteration that ran, an older session).

    v1.358.0 (lane 8A): the fire paths that have no session say their own
    verdict through the same door — a workflow PARKED on the user's answer
    is ``needs_you`` and a goal iteration the engine refused is
    ``skipped`` (both read "ok" and a "Done" row before)."""
    value = str(outcome or "").strip().lower()
    if value == "needs_you":
        return STATUS_NEEDS_YOU
    if value == "completed_with_failures":
        return STATUS_PARTIAL
    if value == STATUS_CANCELLED:
        return STATUS_CANCELLED
    if value == STATUS_SKIPPED:
        return STATUS_SKIPPED
    return STATUS_OK


def delivery_head(status: str) -> str:
    """The notification's head word(s) — the row's verdict, said once.
    ``done`` and ``FAILED`` are unchanged since v1.119.0 (pinned).

    v1.358.0 (lane 8A): ``needs_you`` reads "needs you" — the row's own
    "Needs you" and the bell's — never "finished": a workflow parked on the
    user's answer has not finished, and a run whose ask expired is not done."""
    return {
        STATUS_OK: "done",
        STATUS_NEEDS_YOU: "needs you",
        STATUS_PARTIAL: "finished, but part of it failed",
        STATUS_ERROR: "FAILED",
        STATUS_CANCELLED: "stopped",
        STATUS_SKIPPED: "skipped",
    }.get(str(status or ""), str(status or "") or "done")


def failed_reason(summary: object, status: object, *, asked: bool = False) -> str:
    """The reason a scheduled session that ended FAILED gives its row (the
    words ``failure_detail`` puts after "The run failed: "). A run whose ask
    expired first says so (v1.358.0, lane 8A): the row reads "Failed", as
    the run's own badge does, and the person still learns it asked them."""
    text = plain_text(summary)
    if asked:
        # v1.358.0 (integrate): an asked run with no summary ends on the ask
        # sentence — "...in time. the run ended failed" read as a typo.
        return f"{ASK_EXPIRED_REASON} {text}" if text else ASK_EXPIRED_REASON
    return text or f"the run ended {status}"


def workflow_waiting_detail(ref: object) -> str:
    """The row detail for a scheduled workflow PARKED on the user's answer
    (``needs_you``, v1.358.0 lane 8A): where the answer is given."""
    name = " ".join(str(ref or "").split()) or "inline"
    return f"Your workflow “{name}” is waiting for your answer on the Workflows page."


def workflow_answered(run_status: object, ref: object) -> tuple[str, str] | None:
    """A PARKED scheduled workflow the user answered has finished (v1.358.0,
    integrate): the ``(last_status, detail)`` its row now says, or None for
    a run status that settles nothing. The row stops saying "Needs you" the
    moment the run's own page stops saying it."""
    name = " ".join(str(ref or "").split()) or "inline"
    status = str(run_status or "").strip().lower()
    if status == "completed":
        return STATUS_OK, f"Your workflow “{name}” finished after your answer."
    if status == "failed":
        return STATUS_ERROR, failure_detail(
            f"your workflow “{name}” failed after your answer — its run history on the "
            "Workflows page says where."
        )
    if status == "cancelled":
        return STATUS_CANCELLED, f"Your workflow “{name}” was stopped after your answer."
    return None


def workflow_stopped_waiting(ref: object, *, by_emergency_stop: bool = False) -> tuple[str, str]:
    """A PARKED scheduled workflow that was STOPPED while it still waited
    for the person (v1.358.0 re-review): the ``(last_status, detail)`` its
    row now says. The Workflows page's Cancel of a waiting run publishes no
    completion, so the row kept "Needs you" — and the home line counted it —
    over a run whose own page said Stopped, until the next fire.

    ``by_emergency_stop`` (v1.359.0 integrate, lane 9A review): Emergency
    stop stopped it, not a Cancel press, and the row says so — the run's own
    page already carried ``EMERGENCY_STOP_STOPPED`` in its notes."""
    name = " ".join(str(ref or "").split()) or "inline"
    if by_emergency_stop:
        return (
            STATUS_CANCELLED,
            f"Emergency stop was turned on, so your workflow “{name}” was stopped"
            " before you answered.",
        )
    return STATUS_CANCELLED, f"Your workflow “{name}” was stopped before you answered."


def workflow_step_reason(summary: object) -> str:
    """A workflow step's own words for why it could not finish (v1.361.0,
    lane 11A): plain text, without a run's "The run failed: " lead, cut at a
    word, ending on a full stop. "" when the step gave none."""
    text = plain_text(summary)
    if text.startswith(_FAILED_LEAD):
        text = text[len(_FAILED_LEAD):].strip()
    text = _cut(text, _REASON_LIMIT)
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


def _words(value: object) -> str:
    return " ".join(str(value or "").split())


def _with_reason(head: str, summary: object) -> str:
    """``head`` and then the step's own words after a colon, else a stop."""
    why = workflow_step_reason(summary)
    return f"{head}: {why}" if why else f"{head}."


def workflow_failed_reason(ref: object, step: object = "", summary: object = "") -> str:
    """The reason a SCHEDULED workflow run that failed gives its row and its
    one line on every destination (the words ``failure_detail`` puts after
    "The run failed: "), v1.361.0 lane 11A: WHICH step stopped it, in that
    step's own words. Inside a schedule fire the schedule's line is the only
    line the phone gets, so "see its run history" alone lost the reason the
    step's own alert used to carry. With no step to name, where to look."""
    name = _words(ref) or "inline"
    step_name = _words(step)
    if not step_name:
        return (
            f"your workflow “{name}” failed. Its run history on the Workflows page "
            "says where."
        )
    return _with_reason(f"workflow “{name}” stopped at its step “{step_name}”", summary)


def workflow_skipped_detail(ref: object, failures: list) -> str:
    """The row detail (and the line every destination gets) for a SCHEDULED
    workflow run that FINISHED past failed steps (``on_failure: skip``),
    v1.361.0 lane 11A — the fire is ``partial`` and the first such step is
    named with its own words; more are counted. ``failures`` is
    ``[(step name, output dict), …]`` in step order (``step_outcome``)."""
    name = _words(ref) or "inline"
    if not failures:
        return f"workflow “{name}” ran"
    step, out = failures[0]
    summary = out.get("summary") if isinstance(out, dict) else ""
    line = _with_reason(
        f"workflow “{name}” ran, but its step “{_words(step)}” could not finish", summary
    )
    more = len(failures) - 1
    if more > 0:
        line += f" {more} more step{'s' if more > 1 else ''} could not finish either."
    return line


def goal_skipped_detail(reason: object) -> str:
    """The row detail for a goal iteration the engine REFUSED (``skipped``,
    v1.358.0 lane 8A): nothing ran, and why, in the engine's own words."""
    why = " ".join(str(reason or "").split()) or "it was refused"
    if why.lower().startswith("unknown goal"):
        # v1.358.0 (integrate): the engine's "unknown goal 'goal_7f…'" put an
        # internal id on the row; the person learns what it means instead.
        why = "the goal it runs no longer exists (it was deleted)."
    return f"The goal did not run this time: {why}"


# -- markdown → plain words ---------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE = re.compile(r"^\s{0,3}>\s?")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)")
_UNDERSCORE = re.compile(r"(?<![\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w])")
_STRIKE = re.compile(r"~~(.+?)~~")
_CODE = re.compile(r"`([^`]+)`")
_SPACES = re.compile(r"\s+")
# A sentence ends at . ! ? followed by space and something that starts one.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘'(\[])")


def _inline(text: str) -> str:
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC.sub(r"\1", text)
    text = _UNDERSCORE.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    return _SPACES.sub(" ", text).strip()


def _units(md: object) -> tuple[list[str], list[str]]:
    """``(units, headings)``: the text's paragraphs and list items, each its
    own plain-words unit (a list item is a separate thought even when it ends
    without a full stop), and the heading words kept aside. Heading LINES,
    fence markers, rules and table separators are dropped — a title is not a
    sentence; quotes, emphasis, links and inline code keep their words."""
    units: list[str] = []
    headings: list[str] = []
    current: list[str] = []

    def close() -> None:
        if current:
            text = _inline(" ".join(current))
            if text:
                units.append(text)
            current.clear()

    for line in str(md or "").splitlines():
        if not line.strip():
            close()
            continue
        if _FENCE.match(line) or _RULE.match(line) or _TABLE_SEP.match(line):
            close()
            continue
        if _HEADING.match(line):
            close()
            headings.append(_inline(_HEADING.sub("", line).strip().strip("#")))
            continue
        line = _QUOTE.sub("", line)
        if _BULLET.match(line):
            close()
            line = _BULLET.sub("", line)
        if line.strip().startswith("|"):
            close()
            current.append(" ".join(c.strip() for c in line.strip().strip("|").split("|")))
            close()
            continue
        current.append(line)
    close()
    return units, [h for h in headings if h]


def plain_text(md: object) -> str:
    """Markdown flattened to plain words on one line (see ``_units``). The
    Python twin of the dashboard's ``components/Markdown.plainText``, which
    the Schedules rows still apply on top. Falls back to the heading words
    when the text is ONLY headings, so it never says nothing."""
    try:
        units, headings = _units(md)
        return " ".join(units) or " · ".join(headings)
    except Exception:  # noqa: BLE001 — a record must never be lost to its words
        return _SPACES.sub(" ", str(md or "")).strip()


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 1].rstrip()
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space].rstrip(" ,;:—-")
    return head + "…"


def plain_sentence(md: object, limit: int = _DETAIL_LIMIT) -> str:
    """The FIRST plain sentence of a model-written summary — what the
    Schedules row and the notification show (``# Final Report ## What
    happened I reviewed 12 conversations. …`` → ``I reviewed 12
    conversations.``). Cut at a word with "…" past ``limit``."""
    try:
        units, headings = _units(md)
    except Exception:  # noqa: BLE001
        units, headings = [_SPACES.sub(" ", str(md or "")).strip()], []
    sentences = [s.strip() for u in units for s in _SENTENCE_END.split(u) if s.strip()]
    if not sentences:
        return _cut(" · ".join(headings), limit) if headings else ""
    # A very short opener ("Done.") says nothing alone: it takes the next
    # sentence with it until the line carries a real thought.
    out = sentences[0]
    for nxt in sentences[1:]:
        if len(out) >= _MIN_SENTENCE:
            break
        out = f"{out} {nxt}"
    return _cut(out, limit)


def failure_detail(reason: object) -> str:
    """``The run failed: <reason>`` — the reason flattened to plain words
    (every sentence kept: a remedy usually follows the cause), never a
    Python class name. An empty reason says so instead of printing one."""
    text = plain_text(reason)
    if text.startswith(_FAILED_LEAD):
        text = text[len(_FAILED_LEAD):].strip()
    if not text:
        return _NO_REASON
    return _FAILED_LEAD + _cut(text, _REASON_LIMIT)


def short_detail(status: str, summary: object) -> str:
    """The row detail for a fire that FINISHED: the lead that says why a
    needs_you / partial run fell short, then the run's own first sentence."""
    sentence = plain_sentence(summary)
    lead = {STATUS_NEEDS_YOU: NEEDS_YOU_LEAD, STATUS_PARTIAL: PARTIAL_LEAD}.get(status, "")
    if lead and sentence:
        return f"{lead} {sentence}"
    return lead or sentence
