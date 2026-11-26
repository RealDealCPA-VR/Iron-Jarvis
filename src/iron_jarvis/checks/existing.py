"""The checks the app ALREADY made, as rows of the one record.

Each function here turns one existing check's own answer into
:class:`~.record.Check` rows. None of them judges anything again, and none
changes what its check does, says or publishes: the check-again loop still
sends the model back and writes its line, the required files still get their
note, a coding reply's tests still write their event. They also write here,
so "what did you check?" has one place to read.

Pure and never raising: a row that cannot be built is no row.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .record import (
    BY_RULE, CLAIMS, FAILED, LINE_HEAD, LINE_NOT_CHECKED, NOT_CHECKED, PASSED, REQUIRED_FILE, REVIEW, STEP_CHECK,
    TESTS, WORKFLOW_STEP, Check,
)

log = logging.getLogger(__name__)

#: A problem the check-again loop left open (``until_done.Problem.key``'s
#: first part), as the record says it. Read after "the reply:".
OPEN_WORDS = {
    "claimed_file": "it says a file was saved that no step wrote",
    "changed_file": "it says a file was changed that no step changed",
    "asked_file": "a file was asked for and none was written",
    "claimed_change": "it says a change was made that no step made",
    "claimed_sent": "it says something was sent that no step sent",
    "figures": "a file it wrote holds a figure nobody worked out",
    "deliverables": "a required file is not done",
}
#: Why the check-again loop did not look at a reply (``UntilDone.skipped``).
SKIPPED_WORDS = {
    "low_trust": "it read content from outside, so it was not checked against what it did",
    "truncated": "it was cut off at its length limit, so it was not checked against what it did",
    "declined": "a step was declined or no tools were wanted, so it was not checked against what it did",
    "blocked": "it ended by saying what blocks it, so it was not checked again",
}
CLAIMS_PASSED = "what it says matches what it did"


def claims_check(until: Any) -> "Check | None":
    """The check-again loop's last look at a reply (``daemon/until_done``):
    whether what the reply says matches what it did. None when the loop
    never looked (the demo model, a text-only reply, a coding session)."""
    try:
        if until is None:
            return None
        skipped = str(getattr(until, "skipped", "") or "")
        if skipped:
            return Check(CLAIMS, NOT_CHECKED, SKIPPED_WORDS.get(skipped, "it was not checked against what it did"))
        if not int(getattr(until, "looked", 0) or 0):
            return None
        still = list(dict.fromkeys(str(k) for k in (getattr(until, "open", None) or [])))
        if not still:
            return Check(CLAIMS, PASSED, CLAIMS_PASSED)
        said = [OPEN_WORDS.get(k, "something it says does not match what it did") for k in still]
        return Check(CLAIMS, FAILED, "; ".join(dict.fromkeys(said)))
    except Exception:  # noqa: BLE001
        log.debug("the check-again verdict could not be recorded", exc_info=True)
        return None


def required_file_checks(states: Iterable[Any]) -> list[Check]:
    """One row per required file of the task (``deliverables/check``'s own
    look on disk: ``FileState``)."""
    out: list[Check] = []
    try:
        for s in states or ():
            status = str(getattr(s, "status", "") or "")
            result = PASSED if status == "ok" else NOT_CHECKED if status == "unchecked" else FAILED
            out.append(Check(REQUIRED_FILE, result, str(getattr(s, "detail", "") or ""),
                             file=str(getattr(s, "rel", "") or "")))
    except Exception:  # noqa: BLE001
        log.debug("the required files could not be recorded", exc_info=True)
    return out


def _after_head(line: Any) -> str:
    """A coding reply's own line without its opening words and last stop."""
    text = " ".join(str(line or "").split())
    for head in (LINE_HEAD, LINE_NOT_CHECKED):
        if text.startswith(head):
            text = text[len(head):].strip()
    return text.rstrip(".")


def proof_check(record: Any) -> "Check | None":
    """Build › Code's own test run after a reply (``hosted/proof``'s
    finished record). None for a record that is a question or still runs."""
    try:
        if not isinstance(record, dict) or record.get("state") != "done":
            return None
        tests = str(record.get("tests") or "")
        if tests == "ask":
            return None
        result = PASSED if tests == "passed" else FAILED if tests == "failed" else NOT_CHECKED
        return Check(TESTS, result, _after_head(record.get("line")) or "the tests were not run")
    except Exception:  # noqa: BLE001
        log.debug("a coding reply's test run could not be recorded", exc_info=True)
        return None


def finish_checks(record: Any) -> list[Check]:
    """Chat › Code's finish check (``harness/finish.FinishCheck.record``):
    the folder's tests, and the reviewer's read where a team gave one (``by``
    is the reviewing model's name)."""
    out: list[Check] = []
    try:
        if not isinstance(record, dict):
            return out
        if record.get("off"):
            return [Check(TESTS, NOT_CHECKED, "this folder turns the check after a reply off")]
        tests = str(record.get("tests") or "")
        if tests == "passed":
            out.append(Check(TESTS, PASSED, "they passed"))
        elif tests == "failed":
            out.append(Check(TESTS, FAILED, "they failed"))
        elif tests == "not_run":
            out.append(Check(TESTS, NOT_CHECKED, str(record.get("why") or "") or "they did not run"))
        elif tests == "none":
            out.append(Check(TESTS, NOT_CHECKED, "no tests were found to run"))
        review = record.get("review")
        if isinstance(review, dict):
            who = " ".join(str(review.get("model") or review.get("member") or "").split()) or "a reviewer"
            state, verdict = str(review.get("state") or ""), str(review.get("verdict") or "")
            if state == "done" and verdict == "approved":
                out.append(Check(REVIEW, PASSED, "it approved the change", by=who))
            elif state == "done" and verdict == "changes_needed":
                out.append(Check(REVIEW, FAILED, "it asked for changes", by=who))
            else:
                out.append(Check(REVIEW, NOT_CHECKED, str(review.get("why") or "") or "it gave no verdict", by=who))
    except Exception:  # noqa: BLE001
        log.debug("a coding reply's finish check could not be recorded", exc_info=True)
    return out


def step_check(result: Any, *, judge: str = "", run_id: str = "") -> "Check | None":
    """A split task's per-step check (``agents/decompose.StepResult``).

    ``verified`` says how the step was checked: ``files`` (a rule looked for
    the files the step named), ``model`` (the judging model read its
    output), ``unverified`` (the check did not run: ``not_checked``, never a
    pass). A step that named nothing to check (``none``), or was never
    attempted, writes no row: nothing was checked and nothing is claimed."""
    try:
        get = result.get if isinstance(result, dict) else lambda k, d=None: getattr(result, k, d)
        method = str(get("verified") or "")
        if method not in ("files", "model", "unverified") or get("attempted", True) is False:
            return None
        step = str(int(get("index") or 0) + 1)
        who = BY_RULE if method == "files" else (" ".join(str(judge or "").split()) or "a model")
        if method == "unverified":
            return Check(STEP_CHECK, NOT_CHECKED, "its check did not run", by=who, step=step, run_id=run_id)
        if bool(get("ok")):
            return Check(STEP_CHECK, PASSED, "it passed its check", by=who, step=step, run_id=run_id)
        reason = " ".join(str(get("reason") or "").split())
        if method == "files":
            names = reason.partition(":")[2].strip()
            said = "a file it should have made is not there" + (f": {names}" if names else "")
        else:
            said = "its check did not pass" + (f": {reason[:200]}" if reason else "")
        return Check(STEP_CHECK, FAILED, said, by=who, step=step, run_id=run_id)
    except Exception:  # noqa: BLE001
        log.debug("a step's check could not be recorded", exc_info=True)
        return None


def workflow_check(step_name: Any, problem: Any, *, run_id: str = "") -> "Check | None":
    """A workflow step's stated expectation
    (``workflows/engine._expect_failure``): None = it held."""
    try:
        step = " ".join(str(step_name or "").split())
        if problem is None:
            return Check(WORKFLOW_STEP, PASSED, "it gave what it was expected to", step=step, run_id=run_id)
        text = " ".join(str(problem).split())
        if text.startswith("could not verify"):
            return Check(WORKFLOW_STEP, NOT_CHECKED, "what it should have made could not be looked for",
                         step=step, run_id=run_id)
        return Check(WORKFLOW_STEP, FAILED, text, step=step, run_id=run_id)
    except Exception:  # noqa: BLE001
        log.debug("a workflow step's check could not be recorded", exc_info=True)
        return None


__all__ = [
    "CLAIMS_PASSED", "OPEN_WORDS", "SKIPPED_WORDS", "claims_check", "finish_checks", "proof_check",
    "required_file_checks", "step_check", "workflow_check",
]
