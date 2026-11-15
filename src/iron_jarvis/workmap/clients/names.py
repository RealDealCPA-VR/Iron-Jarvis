"""Names in folder paths and in the owner's words (v1.499.0, builder A).

PURE TEXT. Nothing here touches the disk, calls a model or reads the
database: a folder is compared by how it is spelled (``keys.folder_key``,
``keys.inside``, ``keys.home_folder``; no third normalizer). ``keys.py`` is
not edited; this module imports its word sets.

* :func:`path_parts` / :func:`segments`: the segments of a folder that can
  name someone (the drive, ``Users\\<name>``, code folders and file names
  left out), the second with the path prefix through each segment AS
  SPELLED (a candidate's "home").
* :func:`name_key` / :func:`signature`: the fold. "Hartley's 2026",
  "Hartleys", "Hartley" and "Hartleys LLC" are one name (``hartley``);
  "Weiss" stays ``weiss``. Periods, bare numbers, work words ("Payroll",
  "Returns", "1040"), generic folder words and stop words name nobody.
* :func:`client_child`: the first name segment after the LAST Clients-type
  segment ("C:\\Clients\\Hartleys\\2026" → Hartleys), the one place both
  noticing and ``ClientMap.provisional_of`` read it.
* :func:`named_in`: which clients a piece of text names. Linear in the text.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Mapping

from .. import keys
from . import CLIENT_PARENT_PHRASES, WORK_FOLDER_WORDS

#: ``id -> (name, signature, aliases)``: the shape of ``ClientMap.names``.
NameTable = Mapping[str, "tuple[str, str, tuple[str, ...]]"]

#: A folder at or below one of these is code, never a client's subfolder.
CODE_FOLDERS = frozenset(
    "src lib app node_modules dist build tests test scripts packages .venv venv bin obj components".split()
)
#: File endings that make a path segment a FILE, not a folder.
CODE_EXTENSIONS = frozenset(
    "py ts tsx js jsx go rs java cs cpp c h rb php toml lock yml yaml sh ps1".split()
)
FILE_EXTENSIONS = keys.EXTENSION_WORDS | CODE_EXTENSIONS | frozenset(
    "rtf odt ods qbw qbb qbo ofx tif tiff heic bmp mp3 mp4 mov wav eml msg".split()
)
#: Company suffixes: never part of a signature ("Hartleys LLC" = "Hartleys").
COMPANY_SUFFIXES = frozenset("llc inc corp co ltd pllc pc lp llp plc".split())

MONTHS = frozenset(
    """
    january february march april may june july august september october november december
    jan feb mar apr jun jul aug sep sept oct nov dec
    """.split()
)
WEEKDAYS = frozenset(
    """
    monday tuesday wednesday thursday friday saturday sunday
    mon tue tues wed thu thur thurs fri sat sun
    """.split()
)
_PERIOD_RX = re.compile(r"(?:19|20)\d\d|fy\d\d(?:\d\d)?|ty\d\d(?:\d\d)?|q[1-4]|ytd|h[12]")
_SEPARATORS_RX = re.compile(r"[&\-_.,()/\\]+")
_APOSTROPHES = ("'", "’", "‘", "`")
_FILE_RX = re.compile(r"\.([A-Za-z0-9]{1,5})$")
_DRIVE_RX = re.compile(r"[A-Za-z]:")
#: A word in running text (apostrophes stay inside it: "Hartley's").
_TOKEN_RX = re.compile(r"[^\W_](?:[^\W_]|['’])*")
#: The text :func:`named_in` reads, at most (linear and bounded).
NAMED_TEXT_MAX = 8000


def _no_apostrophes(text: str) -> str:
    for mark in _APOSTROPHES:
        text = text.replace(mark, "")
    return text


def is_period(token: str) -> bool:
    """A token that says WHEN, never whose: a year, ``fy25``, ``ty2025``,
    ``q1``–``q4``, ``ytd``, a month or a weekday (lower case)."""
    return bool(_PERIOD_RX.fullmatch(token)) or token in MONTHS or token in WEEKDAYS


def _dropped(token: str) -> bool:
    return (
        not token
        or len(token) < 2
        or token.isdigit()
        or is_period(token)
        or token in WORK_FOLDER_WORDS
        or token in keys.GENERIC_FOLDER_WORDS
        or token in keys.STOP_WORDS
    )


def _tokens(s: object) -> list[str]:
    text = _no_apostrophes(str(s or "").lower())
    return _SEPARATORS_RX.sub(" ", text).split()


def name_key(s: object) -> str:
    """``s`` folded for matching: lower case; apostrophes removed; ``& - _ .
    , ( ) /`` as spaces; tokens dropped: periods (``19xx``/``20xx``,
    ``fy\\d\\d``, ``ty\\d{4}``, ``q1``–``q4``, ``ytd``, month and weekday
    names and abbreviations), bare numbers, single letters,
    :data:`WORK_FOLDER_WORDS`, ``keys.GENERIC_FOLDER_WORDS`` and
    ``keys.STOP_WORDS``. Single spaces. "" when nothing is left."""
    return " ".join(t for t in _tokens(s) if not _dropped(t))


def _fold(token: str) -> str:
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def signature(s: object) -> str:
    """The never-ask-twice key: the :func:`name_key` tokens with company
    suffixes (``llc inc corp co ltd pllc pc lp llp plc``) dropped and a
    trailing ``s`` stripped from tokens of 5+ letters that do not end in
    ``ss``, joined by single spaces. "Hartley's 2026", "Hartleys", "Hartley"
    and "Hartleys LLC" all give ``hartley``; "Weiss" stays ``weiss``."""
    return " ".join(_fold(t) for t in name_key(s).split() if t not in COMPANY_SUFFIXES)


def _phrase(segment: object) -> str:
    text = _no_apostrophes(str(segment or "").lower())
    return " ".join(re.sub(r"[&\-_.]+", " ", text).split())


def is_client_parent(segment: object) -> bool:
    """Is this one path segment Clients-type (:data:`CLIENT_PARENT_PHRASES`,
    compared lower case, apostrophes dropped, ``& - _ .`` as spaces, single
    spaces)? Used by noticing, ``ClientMap.provisional_of`` and
    ``ClientMap.spans``."""
    return _phrase(segment) in CLIENT_PARENT_PHRASES


def _letters(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def is_name_segment(segment: object, *, under_clients: bool = False) -> bool:
    """A segment that can name a client: its :func:`signature` is non-empty
    and has at least 4 letters, or at least 2 letters directly under a
    Clients-type parent (``under_clients``: Lee, Ng)."""
    if is_client_parent(segment):
        return False
    sig = signature(segment)
    return bool(sig) and _letters(sig) >= (2 if under_clients else 4)


def segments(folder: object) -> list[tuple[str, str]]:
    """``[(segment as spelled, the path through it as spelled)]``, outermost
    first, for the segments :func:`path_parts` keeps. A coding session's own
    copy counts as its repository (``keys.home_folder``). Text only."""
    raw = keys.home_folder(str(folder or "").strip().strip('"')).strip()
    if not raw:
        return []
    sep = "\\" if "\\" in raw else "/"
    unc = raw.startswith(("\\\\", "//"))
    parts = [p for p in re.split(r"[\\/]+", raw) if p != ""]
    if unc:
        head = sep * 2 + sep.join(parts[:2])
        rest = parts[2:]
    elif parts and _DRIVE_RX.fullmatch(parts[0]):
        head, rest = parts[0], parts[1:]
    elif raw[:1] in "\\/":
        head, rest = "", parts
    else:
        head, rest = None, parts
    out: list[tuple[str, str]] = []
    prefix = head
    # C:\Users\<name>, /Users/<name> (a Mac), /home/<name>: whose PC, never
    # whose work. Only at the top: "D:\Home\Hartleys" keeps Hartleys.
    top = rest[0].lower() if rest else ""
    skip_user = head is not None and (top == "users" or (top == "home" and head == ""))
    for i, seg in enumerate(rest):
        prefix = seg if prefix is None else prefix + sep + seg
        low = seg.lower()
        if skip_user and i < 2:
            continue
        if low in CODE_FOLDERS or low.endswith(keys.WORKTREES_SUFFIX):
            break  # code (or a coding session's copies) from here down
        if low in (".", "..") or low.startswith("."):
            continue
        ext = _FILE_RX.search(seg)
        if ext and ext.group(1).lower() in FILE_EXTENSIONS:
            continue
        out.append((seg, prefix))
    return out


def path_parts(folder: object) -> tuple[str, ...]:
    """The segments of ``folder`` as spelled, outermost first.

    Drops: the drive and a UNC server and share; ``users/<name>`` and
    ``home/<name>``; everything at or below the first code folder (``src lib
    app node_modules dist build tests test scripts packages .venv venv bin obj
    components``); ``.``-prefixed segments, ``.ij-worktrees`` and segments
    that end in a known file extension. ``()`` for nothing."""
    return tuple(seg for seg, _prefix in segments(folder))


def client_child(folder: object) -> tuple[str, str] | None:
    """``(segment, path through it)`` of the first name segment after the
    LAST Clients-type segment of ``folder`` ("C:\\Clients\\2025\\Hartleys\\
    Payroll" → Hartleys; periods and work words in between are skipped), or
    None when the path has no Clients-type segment with a name under it."""
    segs = segments(folder)
    last = -1
    for i, (seg, _prefix) in enumerate(segs):
        if is_client_parent(seg):
            last = i
    if last < 0:
        return None
    for seg, prefix in segs[last + 1 :]:
        if is_name_segment(seg, under_clients=True):
            return seg, prefix
    return None


def _display_words(spelling: str) -> str:
    words = re.split(r"[\s_]+|(?<=\w)-(?=\d)|(?<=\d)-(?=\w)", str(spelling or "").strip())
    kept = []
    for word in words:
        bare = _no_apostrophes(word.lower()).strip(".,()[]-&")
        if not bare or bare.isdigit() or is_period(bare) or bare in WORK_FOLDER_WORDS:
            continue
        kept.append(word)
    shown = " ".join(kept).strip(" -_.,&")
    return shown[:80].rstrip()


def display_name(spellings: "Iterable[str] | str") -> str:
    """The name to show: the most frequent of ``spellings`` (a str is one
    spelling), with period and work words cut ("Hartleys 2026" → "Hartleys").
    "" when nothing is left."""
    if isinstance(spellings, str):
        spellings = [spellings]
    counts: Counter[str] = Counter()
    first: dict[str, int] = {}
    for i, spelling in enumerate(spellings or ()):
        shown = _display_words(str(spelling or ""))
        if not shown or not signature(shown):
            continue
        counts[shown] += 1
        first.setdefault(shown, i)
    if not counts:
        return ""
    # Most frequent; then one written with a capital; then the first seen.
    return sorted(counts, key=lambda s: (-counts[s], not s[:1].isupper(), first[s]))[0]


def possessive(name: str) -> str:
    """The possessive, by ONE table pinned on both sides (``dashboard/lib/
    clients.ts``): "Hartley's" stays "Hartley's", "Hartleys" becomes
    "Hartleys'", "Smith" becomes "Smith's"."""
    text = str(name or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low.endswith(("'s", "’s")):
        return text
    if low.endswith("s"):
        return text + "'"
    return text + "'s"


def named_in(text: object, names: NameTable) -> frozenset[str]:
    """The ids in ``names`` that ``text`` names. Linear in the text.

    A single-word name counts only when written capitalised as a whole word
    (apostrophes fold: "Hartley's" names Hartleys); a lower-case "hartleys"
    names nothing. A multi-word name counts in any case, as contiguous
    signature tokens. Aliases count."""
    body = str(text or "")[:NAMED_TEXT_MAX]
    if not body.strip() or not names:
        return frozenset()
    capital: set[str] = set()
    seq: list[str] = []
    cache: dict[str, str] = {}
    for m in _TOKEN_RX.finditer(body):
        raw = m.group(0)
        low = raw.lower()
        sig = cache.get(low)
        if sig is None:
            sig = signature(low)
            cache[low] = sig
        if not sig:
            continue
        seq.append(sig)
        if raw[:1].isupper():
            capital.add(sig)
    if not seq:
        return frozenset()
    joined = " " + " ".join(seq) + " "
    found = set()
    for cid, entry in names.items():
        try:
            _name, sig, aliases = entry
        except (TypeError, ValueError):
            continue
        for want in (sig, *tuple(aliases or ())):
            want = " ".join(str(want or "").split())
            if not want:
                continue
            if " " in want:
                hit = f" {want} " in joined
            else:
                hit = want in capital
            if hit:
                found.add(str(cid))
                break
    return frozenset(found)


__all__ = [
    "CODE_EXTENSIONS",
    "CODE_FOLDERS",
    "COMPANY_SUFFIXES",
    "NameTable",
    "client_child",
    "display_name",
    "is_client_parent",
    "is_name_segment",
    "is_period",
    "name_key",
    "named_in",
    "path_parts",
    "possessive",
    "segments",
    "signature",
]
