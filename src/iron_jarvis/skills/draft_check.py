"""A skill draft is checked when it is WRITTEN (the intelligence plan,
release 10, on top of next leaps 19-a).

Every draft a model writes for Improvements passes through
:func:`check` at the ONE door that makes a draft
(``SkillLearningEngine._mint_proposal``), whatever it was written from (a
run, an improved skill, the same task asked for in chat or as a task on
three days):

* **masked** — a secret-shaped value (``detections/redact.mask`` would change
  the text) or an identifier's shape (``documents/redact.find_pii_spans``: an
  email address, a phone number, an account number, a street address …)
  REFUSES the draft;
* **scanned** — a line ``core/promptguard`` reads as an instruction to the
  model REFUSES it (a scan that cannot run refuses too);
* **checked against client names** — a draft that names a confirmed or
  waiting client (``learning.names_a_client``, the wall's own lists; a list
  that cannot be read counts as naming one) is refused as ``client``: the
  caller HOLDS it (19-c's client-only skills), whatever its source.

A refused draft never reaches Improvements.

Then, separately, whether the draft is CLEAN: no figure and no name from the
work it was written from. Only a clean draft carries the line "No names or
figures from your work are in it." (``repeats.checked_line``), so the line
is true by construction:

* a FIGURE is any run of digits in the record (all of it, as saved, before
  any mask or cut), compared without leading zeros; the draft's own ordered
  list numbers ("1." at the start of a line) are not figures;
* a NAME is a word in the record that starts with a capital letter where no
  sentence starts (a person's, a client's, a company's or a place's name
  written in the middle of a sentence), compared case for case as a whole
  word; the app's own name is not one;
* a record cut short by :data:`RECORD_MAX` is never clean (what was not read
  cannot be promised).

Strict on purpose: a draft that uses "Excel" where the record said "Excel"
loses the line, never the other way round.

:func:`check` is BLOCKING (the client list is read from the database).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: The most record text the figure and name checks read (all of the work's
#: own words, raw). Past it the draft is not called clean.
RECORD_MAX = 400_000

#: Why a draft is refused.
SECRET = "secret"
INSTRUCTION = "instruction"
CLIENT = "client"

_DIGITS = re.compile(r"\d+")
#: An ordered list number at the start of a line ("1." / "12)").
_LIST_NUMBER = re.compile(r"^([ \t]*)\d{1,3}([.)])(?=[ \t])", re.MULTILINE)
#: A word that starts with a capital letter (letters, digits, ' and - inside).
_CAPITAL_WORD = re.compile(r"(?<![\w'’-])([A-Z][\w'’-]*)")
#: What starts a sentence before a word: the end of one, a new line (with a
#: list mark), an opening quote or bracket. A colon does NOT ("Client:
#: Hartleys" names one).
_SENTENCE_START = re.compile(r"(?:[.!?]\s+|\n\s*(?:[-*#>]+\s+|\d{1,3}[.)]\s+)?|[\"“(\[]\s*)$")
#: Never a name from the owner's work: the app's own name, and the little
#: words a line after a colon starts with.
_NOT_NAMES = frozenset({
    "Iron", "Jarvis", "I", "A", "An", "The", "This", "That", "These", "Those", "It", "We", "You", "He",
    "She", "They", "My", "Your", "Our", "Its", "And", "But", "Or", "If", "When", "Then", "Here",
    "There", "Done", "Yes", "No", "Please", "Thanks", "Hi", "Hello",
})


@dataclass(frozen=True)
class Verdict:
    """What :func:`check` found. ``refused`` is "" (the draft may be shown),
    :data:`SECRET`, :data:`INSTRUCTION` or :data:`CLIENT`. ``clean``: no
    figure and no name from the record is in it (only then may the line be
    said); always False when refused."""

    refused: str = ""
    clean: bool = False


def _bounded(parts: Iterable[Any]) -> tuple[str, bool]:
    """The record's own words, joined, at most :data:`RECORD_MAX`; and
    whether all of them were read."""
    out: list[str] = []
    size = 0
    for part in parts or ():
        text = str(part or "")
        if not text:
            continue
        room = RECORD_MAX - size
        if len(text) > room:
            out.append(text[:max(0, room)])
            return "\n".join(out), False
        out.append(text)
        size += len(text) + 1
    return "\n".join(out), True


def figures(text: Any) -> set[str]:
    """Every run of digits in ``text``, without leading zeros. Pure."""
    return {m.lstrip("0") or "0" for m in _DIGITS.findall(str(text or ""))}


def draft_figures(text: Any) -> set[str]:
    """:func:`figures` of a DRAFT, its ordered list numbers left out. Pure."""
    return figures(_LIST_NUMBER.sub(r"\1\2", str(text or "")))


def names(text: Any) -> set[str]:
    """The words of ``text`` that start with a capital letter where no
    sentence starts (see the module docstring). Pure, linear."""
    body = str(text or "")
    found: set[str] = set()
    for m in _CAPITAL_WORD.finditer(body):
        word = m.group(1).rstrip("'’-")
        if not word or word in _NOT_NAMES:
            continue
        before = body[max(0, m.start() - 12):m.start()]
        if m.start() == 0 or _SENTENCE_START.search(before):
            continue
        found.add(word)
    return found


def _words(text: str) -> set[str]:
    return {m.group(1).rstrip("'’-") for m in _CAPITAL_WORD.finditer(text)}


def new_lines(text: Any, before: Any = "") -> str:
    """The lines of ``text`` that are not, stripped, a line of ``before``:
    what an IMPROVED skill adds to the owner's own text. Pure."""
    if not before:
        return str(text or "")
    kept = {line.strip() for line in str(before).splitlines() if line.strip()}
    return "\n".join(line for line in str(text or "").splitlines() if line.strip() not in kept)


def is_clean(draft: Any, record: Iterable[Any]) -> bool:
    """No figure and no name from ``record`` in ``draft`` (and all of the
    record was read). Pure."""
    source, whole = _bounded(record)
    if not whole:
        return False
    text = str(draft or "")
    if draft_figures(text) & figures(source):
        return False
    return not (_words(text) & names(source))


def secret_shaped(text: Any) -> bool:
    """Would the mask change ``text``, or does it hold an identifier's
    shape? Pure."""
    from ..detections.redact import mask
    from ..documents.redact import find_pii_spans

    body = str(text or "")
    return str(mask(body) or "") != body or bool(find_pii_spans(body))


def read_as_instruction(text: Any) -> bool:
    """Does ``core/promptguard`` read a line of ``text`` as an instruction
    to the model? A scan that cannot run answers True (fail closed)."""
    from ..core.promptguard import scan_context

    try:
        return bool(scan_context(str(text or ""), source="a skill draft", cap=None, strict=True).blocked)
    except Exception:  # noqa: BLE001
        return True


def check(engine: Any, draft: Any, record: Iterable[Any], *, before: Any = "") -> Verdict:
    """Check a draft's final text (see the module docstring). ``record``:
    the work's own words, raw (every piece the draft was written from).
    ``before``: an improved skill's current text; only the lines the draft
    ADDS are checked (the owner's own text is theirs). BLOCKING."""
    from .learning import names_a_client

    text = new_lines(draft, before)  # bounded: the parser caps a draft
    if secret_shaped(text):
        return Verdict(refused=SECRET)
    if read_as_instruction(text):
        return Verdict(refused=INSTRUCTION)
    if names_a_client(engine, text):
        return Verdict(refused=CLIENT)
    return Verdict(clean=is_clean(text, record))


__all__ = [
    "CLIENT", "INSTRUCTION", "RECORD_MAX", "SECRET", "Verdict", "check", "draft_figures",
    "figures", "is_clean", "names", "new_lines", "read_as_instruction", "secret_shaped",
]
