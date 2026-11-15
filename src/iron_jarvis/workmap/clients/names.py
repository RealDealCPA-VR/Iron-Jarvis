"""Names in folder paths and in the owner's words (v1.499.0, builder A).

PURE TEXT. Nothing here touches the disk, calls a model or reads the
database: a folder is compared by how it is spelled (``keys.folder_key``,
``keys.inside``, ``keys.home_folder``; no third normalizer). ``keys.py`` is
not edited; this module imports its word sets.

STUB until builder A lands: every function answers "no name" so nothing is
noticed and nothing is named. The docstrings are the rule to build.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .. import keys  # noqa: F401  (the word sets the bodies use)
from . import CLIENT_PARENT_PHRASES, WORK_FOLDER_WORDS  # noqa: F401

#: ``id -> (name, signature, aliases)``: the shape of ``ClientMap.names``.
NameTable = Mapping[str, "tuple[str, str, tuple[str, ...]]"]


def path_parts(folder: object) -> tuple[str, ...]:
    """The segments of ``folder`` as spelled, outermost first.

    Drops: the drive and a UNC server and share; ``users/<name>`` and
    ``home/<name>``; everything at or below the first code folder (``src lib
    app node_modules dist build tests test scripts packages .venv venv bin obj
    components``); ``.``-prefixed segments, ``.ij-worktrees`` and segments
    that end in a known file extension. ``()`` for nothing."""
    return ()


def name_key(s: object) -> str:
    """``s`` folded for matching: lower case; apostrophes removed; ``& - _ .
    , ( ) /`` as spaces; tokens dropped: periods (``19xx``/``20xx``,
    ``fy\\d\\d``, ``ty\\d{4}``, ``q1``–``q4``, ``ytd``, month and weekday
    names and abbreviations), bare numbers, single letters,
    :data:`WORK_FOLDER_WORDS`, ``keys.GENERIC_FOLDER_WORDS`` and
    ``keys.STOP_WORDS``. Single spaces. "" when nothing is left."""
    return ""


def signature(s: object) -> str:
    """The never-ask-twice key: the :func:`name_key` tokens with company
    suffixes (``llc inc corp co ltd pllc pc lp llp plc``) dropped and a
    trailing ``s`` stripped from tokens of 5+ letters that do not end in
    ``ss``, joined by single spaces. "Hartley's 2026", "Hartleys", "Hartley"
    and "Hartleys LLC" all give ``hartley``; "Weiss" stays ``weiss``."""
    return ""


def is_client_parent(segment: object) -> bool:
    """Is this one path segment Clients-type (:data:`CLIENT_PARENT_PHRASES`,
    compared lower case, apostrophes dropped, ``& - _ .`` as spaces, single
    spaces)? Used by noticing, ``ClientMap.provisional_of`` and
    ``ClientMap.spans``."""
    return False


def is_name_segment(segment: object, *, under_clients: bool = False) -> bool:
    """A segment that can name a client: its :func:`signature` is non-empty
    and has at least 4 letters, or at least 2 letters directly under a
    Clients-type parent (``under_clients``: Lee, Ng)."""
    return False


def display_name(spellings: "Iterable[str] | str") -> str:
    """The name to show: the most frequent of ``spellings`` (a str is one
    spelling), with period and work words cut ("Hartleys 2026" → "Hartleys").
    "" when nothing is left."""
    return ""


def possessive(name: str) -> str:
    """The possessive, by ONE table pinned on both sides (``dashboard/lib/
    clients.ts``): "Hartley's" stays "Hartley's", "Hartleys" becomes
    "Hartleys'", "Smith" becomes "Smith's"."""
    return ""


def named_in(text: object, names: NameTable) -> frozenset[str]:
    """The ids in ``names`` that ``text`` names. Linear in the text.

    A single-word name counts only when written capitalised as a whole word
    (apostrophes fold: "Hartley's" names Hartleys); a lower-case "hartleys"
    names nothing. A multi-word name counts in any case, as contiguous
    signature tokens. Aliases count."""
    return frozenset()


__all__ = [
    "NameTable",
    "display_name",
    "is_client_parent",
    "is_name_segment",
    "name_key",
    "named_in",
    "path_parts",
    "possessive",
    "signature",
]
