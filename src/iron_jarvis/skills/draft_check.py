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

Then, separately, whether the draft is CLEAN. Only a clean draft carries
the line "No client names, contact details or figures from your work are in
it." (``repeats.checked_line``). The line promises ONLY what these checks
guarantee (the review of release 10: a person's name written in lower case
cannot be found in general, so it never says "no names"):

* CLIENT NAMES: no name, signature or alias the client list holds (confirmed
  or waiting) is in the WHOLE draft in any case or script ("hartwell",
  "HARTWELL", "“Hartwell”", "иванова"), as whole words (:func:`client_named_any_case`;
  a list that cannot be read is never clean);
* CONTACT DETAILS: the mask and the identifier patterns find nothing in the
  WHOLE draft (an improvement keeps the owner's own lines, so this is
  checked again on all of it);
* FIGURES: no figure of the record (all of it, as saved, before any mask or
  cut) is in the draft, in any of its forms (:func:`figure_forms`: every run
  of digits, and every run joined to the runs after it across ``,`` ``.``
  ``'`` and a space, so "1,250.00", "1.250,00", "1'250" and "1 250" all
  hold "1250"), compared both ways without leading zeros; the draft's own
  ordered list numbers ("1." at the start of a line) are not figures;
* a record cut short by :data:`RECORD_MAX` is never clean (what was not read
  cannot be promised).

Strict on purpose: a draft that says "3" where the record said "3" loses the
line, never the other way round.

:func:`check` is BLOCKING (the client list is read from the database).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: The most record text the figure check reads (all of the work's
#: own words, raw). Past it the draft is not called clean.
RECORD_MAX = 400_000

#: Why a draft is refused.
SECRET = "secret"
INSTRUCTION = "instruction"
CLIENT = "client"

#: A figure: runs of digits joined by one separator (``,`` ``.`` ``'`` ``’``
#: or a space, a no-break space or a thin space) between digits.
_FIGURE = re.compile("\\d+(?:[,.'’ \u00a0\u202f]\\d+)*")
_DIGITS = re.compile(r"\d+")
#: An ordered list number at the start of a line ("1." / "12)").
_LIST_NUMBER = re.compile(r"^([ \t]*)\d{1,3}([.)])(?=[ \t])", re.MULTILINE)


@dataclass(frozen=True)
class Verdict:
    """What :func:`check` found. ``refused`` is "" (the draft may be shown),
    :data:`SECRET`, :data:`INSTRUCTION` or :data:`CLIENT`. ``clean``: no
    client name, contact detail or figure of the record is in it (only then
    may the line be said); always False when refused."""

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


def figure_forms(text: Any) -> set[str]:
    """Every form of every figure in ``text``, without leading zeros: each
    run of digits, and each run joined to the runs that follow it in the
    same figure ("1,250.00" gives 1, 250, 0, 1250 and 125000). Pure, linear
    in the figures' size."""
    out: set[str] = set()
    for m in _FIGURE.finditer(str(text or "")):
        runs = _DIGITS.findall(m.group(0))
        for i in range(len(runs)):
            joined = ""
            for run in runs[i:]:
                joined += run
                out.add(joined.lstrip("0") or "0")
    return out


#: The old name (every run of digits is one of the forms).
figures = figure_forms


def draft_figures(text: Any) -> set[str]:
    """:func:`figure_forms` of a DRAFT, its ordered list numbers left out.
    Pure."""
    return figure_forms(_LIST_NUMBER.sub(r"\1\2", str(text or "")))


def new_lines(text: Any, before: Any = "") -> str:
    """The lines of ``text`` that are not, stripped, a line of ``before``:
    what an IMPROVED skill adds to the owner's own text. Pure."""
    if not before:
        return str(text or "")
    kept = {line.strip() for line in str(before).splitlines() if line.strip()}
    return "\n".join(line for line in str(text or "").splitlines() if line.strip() not in kept)


def is_clean(draft: Any, record: Iterable[Any]) -> bool:
    """No figure of ``record`` in ``draft``, in any of its forms, and all of
    the record was read. Pure. (Client names and contact details are
    :func:`check`'s.)"""
    source, whole = _bounded(record)
    if not whole:
        return False
    return not (draft_figures(draft) & figure_forms(source))


def _signed(text: str) -> str:
    """``text`` as client-name signature tokens, lower case, any script,
    joined by single spaces with a space at each end (the client list's own
    folding, ``workmap/clients/names``)."""
    from ..workmap.clients import names as N

    out: list[str] = []
    for m in N._TOKEN_RX.finditer(text):
        sig = N.signature(m.group(0).lower())
        if sig:
            out.append(sig)
    return " " + " ".join(out) + " "


def client_named_any_case(engine: Any, text: Any) -> bool:
    """Does ``text`` hold a name the client list has (confirmed or waiting:
    its display name, signature or an alias), in ANY case or script, as
    whole words? A list that cannot be read answers True. BLOCKING."""
    try:
        from ..workmap.clients import names as N
        from ..workmap.clients import store

        cmap = store.load_map(engine)
        joined = _signed(str(text or ""))
        for table in (cmap.names, cmap.waiting_names):
            for entry in (table or {}).values():
                name, sig, aliases = entry
                for want in (N.signature(str(name or "").lower()), str(sig or ""), *tuple(aliases or ())):
                    want = " ".join(str(want or "").lower().split())
                    if want and f" {want} " in joined:
                        return True
        return False
    except Exception:  # noqa: BLE001 — unreadable: never called clean
        return True


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
    ADDS can refuse it (the owner's own text is theirs), but whether it is
    clean is read off the WHOLE draft. BLOCKING."""
    from .learning import names_a_client

    whole = str(draft or "")  # bounded: the parser caps a draft
    text = new_lines(whole, before)
    if secret_shaped(text):
        return Verdict(refused=SECRET)
    if read_as_instruction(text):
        return Verdict(refused=INSTRUCTION)
    if names_a_client(engine, text):
        return Verdict(refused=CLIENT)
    clean = (
        is_clean(whole, record)
        and not secret_shaped(whole)
        and not client_named_any_case(engine, whole)
    )
    return Verdict(clean=clean)


__all__ = [
    "CLIENT", "INSTRUCTION", "RECORD_MAX", "SECRET", "Verdict", "check", "client_named_any_case",
    "draft_figures", "figure_forms", "figures", "is_clean", "new_lines", "read_as_instruction", "secret_shaped",
]
