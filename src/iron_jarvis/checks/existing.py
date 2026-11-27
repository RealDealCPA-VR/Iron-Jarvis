"""The checks the app ALREADY made, as rows of the one record.

Each function here turns one existing check's own answer into
:class:`~.record.Check` rows. None of them judges anything again, and none
changes what its check does, says or publishes: the check-again loop still
sends the model back and writes its line, the required files still get their
note, a coding reply's tests still write their event. They also write here,
so "what did you check?" has one place to read.

WHICH OUTCOME EACH WRITER MAY USE (``record.PROBLEM_CHECKS`` is the
allowlist; a row of any other check is never a "Problem"):

* :func:`claims_check` (the reply's own claims): ``passed`` only when the
  reply NAMED something it saved, changed or sent and the record holds it;
  what the reading found unmatched is ``look`` (a reading of prose).
* :func:`required_file_checks`: ``failed`` ONLY for a required file that is
  not on disk (a fact). One that is there and empty, or that the older check
  could not open, is ``look``.
* :func:`proof_check`, :func:`finish_checks` (tests): ``failed`` for a test
  command that ran and failed (a fact). A reviewer that asked for changes is
  ``look`` (a model's opinion; never its words).
* :func:`step_check`, :func:`workflow_check`: a step whose check did not
  pass is ``look``, whether a model judged it or a rule looked for a file
  the plan named.

NO MODEL'S WORDS. A row's detail is words this module (or the check's own
code) built: a judging model's reason, a reviewer's read and a tool's raw
error are never stored, so they can never reach the line under a reply or
the list a press opens. A model-judged step that failed reads "its check did
not pass", with the model's name in ``by``, and nothing more. PLAIN WORDS:
an older check's reason is put through ``record.open_words`` /
``record.plain_words`` (never an exception's name or a folder).

Pure and never raising: a row that cannot be built is no row.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from .record import (
    BY_RULE, CLAIMS, FAILED, LINE_HEAD, LINE_NOT_CHECKED, LOOK, NOT_CHECKED, PASSED, REQUIRED_FILE, REVIEW,
    STEP_CHECK, TESTS, WORKFLOW_STEP, Check, file_name, open_words, plain_words, quoted,
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
#: The good side of the reply's claims check. No wider than what is READ:
#: the files a reply says it saved or changed, and what it says it sent
#: (never "what it says matches what it did": a reply also says it ran
#: tests, printed, booked, deleted, and none of that is read).
CLAIMS_PASSED = ("the files it says it saved or changed, and what it says it sent, are in Iron Jarvis's record")
#: A required file's states that are a FACT about the disk: it is not there
#: (or a folder is where the file should be).
NOT_ON_DISK = frozenset({"missing", "folder"})
#: A model-judged step that failed: this, and the model's name in ``by``.
STEP_FAILED = "its check did not pass"
#: Why a coding reply's tests did not run, where the finish check's own code
#: says so (``harness/finish``); any other reason reads "they did not run".
TESTS_NOT_RUN_WORDS = frozenset({
    "this folder turned the test run off",
    "this reply is in low trust, so the check runs no command",
    "this reply has no command tool",
})
#: How a review that gave no verdict ended (``harness/finish.REVIEW_*``).
REVIEW_ENDED_WORDS = {
    "not_run": "the review did not run",
    "stopped": "the review was stopped before it finished",
    "failed": "the review did not finish",
    "not_counted": "its answer was not counted",
}
#: The reasons the finish check's OWN code writes for a review with no
#: verdict (``harness/finish``; a test holds them equal to its constants).
#: Only these are kept as they are. Any other reason may be the reviewer's
#: answer or a tool's raw error, and reads by :data:`REVIEW_ENDED_WORDS`.
REVIEW_WHY_WORDS = frozenset({
    "it ended without a report",
    "its answer was not counted: the reviewer read something that looked like an instruction, "
    "so its run was at low trust",
    "its answer was not counted: its report read like an instruction, so it was kept from the reply",
    "its answer was not counted: its run could not be read back",
    "this reply is in low trust, so it was not handed to a reviewer",
    "the change could not be read for the reviewer",
    "the reply was stopped during the review",
    "the review was stopped before it finished",
    "the review did not run",
})
_REVIEW_LATE_RX = re.compile(r"^the review took longer than \d{1,3} minutes?, so the reply went on without it$")


def _review_words(state: str, why: Any) -> str:
    said = " ".join(str(why or "").split())
    if said in REVIEW_WHY_WORDS or _REVIEW_LATE_RX.match(said):
        return said
    return REVIEW_ENDED_WORDS.get(state, "it gave no verdict")


def claims_check(until: Any) -> "Check | None":
    """The check-again loop's last look at a reply (``daemon/until_done``).
    None when the loop never looked (the demo model, a text-only reply, a
    coding session), and when it looked, found nothing unmatched, and the
    reply named NOTHING it saved, changed or sent (``until.claims``): there
    is then nothing to say was checked. What the reading found unmatched is
    ``look``: it is read out of the reply's prose."""
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
            if not int(getattr(until, "claims", 0) or 0):
                return None
            return Check(CLAIMS, PASSED, CLAIMS_PASSED)
        said = [OPEN_WORDS.get(k, "something it says does not match what it did") for k in still]
        return Check(CLAIMS, LOOK, "; ".join(dict.fromkeys(said)))
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
            # A PROBLEM only for the fact "it is not on disk". A file that is
            # there and empty, or that the older check could not open (it
            # reads a cut piece of a large text file), is worth a look.
            result = (PASSED if status == "ok" else NOT_CHECKED if status == "unchecked"
                      else FAILED if status in NOT_ON_DISK else LOOK)
            detail = str(getattr(s, "detail", "") or "")
            # The older check's own reason for a file that does not open may
            # name an exception or a part of the package: plain words only.
            said = open_words(detail) if status == "unreadable" else plain_words(detail)
            out.append(Check(REQUIRED_FILE, result, said, file=str(getattr(s, "rel", "") or "")))
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
            # The reason is kept only when it is one the finish check's own
            # code wrote: a command's raw error is not shown.
            why = " ".join(str(record.get("why") or "").split())
            out.append(Check(TESTS, NOT_CHECKED, why if why in TESTS_NOT_RUN_WORDS else "they did not run"))
        elif tests == "none":
            out.append(Check(TESTS, NOT_CHECKED, "no tests were found to run"))
        review = record.get("review")
        if isinstance(review, dict):
            who = " ".join(str(review.get("model") or review.get("member") or "").split()) or "a reviewer"
            state, verdict = str(review.get("state") or ""), str(review.get("verdict") or "")
            if state == "done" and verdict == "approved":
                out.append(Check(REVIEW, PASSED, "it approved the change", by=who))
            elif state == "done" and verdict == "changes_needed":
                # A model's opinion: worth a look, never a problem (and never its words).
                out.append(Check(REVIEW, LOOK, "it asked for changes", by=who))
            else:
                # Never the reviewer's own words (its answer, a tool's raw
                # error): a reason the finish check's own code wrote, else
                # one plain sentence by how the review ended.
                out.append(Check(REVIEW, NOT_CHECKED, _review_words(state, review.get("why")), by=who))
    except Exception:  # noqa: BLE001
        log.debug("a coding reply's finish check could not be recorded", exc_info=True)
    return out


def step_check(result: Any, *, judge: str = "", run_id: str = "") -> "Check | None":
    """A split task's per-step check (``agents/decompose.StepResult``).

    ``verified`` says how the step was checked: ``files`` (a rule looked for
    the files the step named), ``model`` (the judging model read its
    output), ``unverified`` (the check did not run: ``not_checked``, never a
    pass). A step that named nothing to check (``none``), or was never
    attempted, writes no row: nothing was checked and nothing is claimed.

    The judging model's REASON is never kept: it is a model's words about a
    step's output (content from anywhere), and the record's words reach the
    line under a reply. A model-judged step that failed says only that."""
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
        if method == "files":
            # The file gate's own sentence ("expected file(s) not found...:
            # a.xlsx, b.docx") is built by code: the NAMES are kept.
            listed = " ".join(str(get("reason") or "").split()).partition(":")[2]
            names = ", ".join(quoted(n) for n in (file_name(x.strip()) for x in listed.split(",")) if n)
            said = "a file it should have made is not there" + (f": {names}" if names else "")
        else:
            said = STEP_FAILED
        # Worth a look, whoever checked: a model's verdict is an opinion, and
        # the file a rule looked for is one the plan (a model) named.
        return Check(STEP_CHECK, LOOK, said, by=who, step=step, run_id=run_id)
    except Exception:  # noqa: BLE001
        log.debug("a step's check could not be recorded", exc_info=True)
        return None


_EXPECTED_FILE_RX = re.compile(r"^expected file '(?P<name>[^']*)' was not found")
_SUMMARY_RX = re.compile(r"^step summary does not contain ")


def _workflow_words(problem: str) -> str:
    """The workflow engine's own sentence for an expectation that did not
    hold (``_expect_failure``), in plain words: the file's NAME (never the
    paths it saw), never the words the step was expected to say (a workflow
    may be one a model drafted), and nothing of the engine's wording."""
    m = _EXPECTED_FILE_RX.match(problem)
    if m is not None:
        name = file_name(m.group("name"))
        return "a file it should have made was not found" + (f": {quoted(name)}" if name else "")
    if _SUMMARY_RX.match(problem) is not None:
        return "its result does not say what it was expected to say"
    if "expectations need an agent or tool step" in problem:
        return "it was expected to make a file, and this kind of step makes none"
    return "it did not give what it was expected to"


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
        return Check(WORKFLOW_STEP, LOOK, _workflow_words(text), step=step, run_id=run_id)
    except Exception:  # noqa: BLE001
        log.debug("a workflow step's check could not be recorded", exc_info=True)
        return None


__all__ = [
    "CLAIMS_PASSED", "NOT_ON_DISK", "OPEN_WORDS", "REVIEW_ENDED_WORDS", "REVIEW_WHY_WORDS", "SKIPPED_WORDS", "STEP_FAILED",
    "TESTS_NOT_RUN_WORDS",
    "claims_check", "finish_checks", "proof_check", "required_file_checks", "step_check", "workflow_check",
]
