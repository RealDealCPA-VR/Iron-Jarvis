"""Folder keys and search words for the work record (v1.491.0).

PURE TEXT. Nothing here touches the disk: a folder is compared by how it is
spelled, never by asking Windows about it (a path on another computer must
never be looked up just to compare it; the v1.379.0 lesson).
"""

from __future__ import annotations

import os
import re

#: The folder beside a repository that holds a coding session's own copies
#: (``harness/branches.WORKTREES_SUFFIX``; pinned equal by the tests).
WORKTREES_SUFFIX = ".ij-worktrees"

_SLASHES = re.compile(r"/+")
_WORD = re.compile(r"[a-z0-9]{3,}")

#: Words that say nothing about WHICH work is meant.
STOP_WORDS = frozenset(
    """
    the and for with from that this these those what when where which who whom why how
    have has had are was were will would should could can may might must did does doing
    done make made please just about into onto over under then than them they their there
    here you your yours our ours mine its his her hers him she not but all any some more
    most much many few one two also only very too out off again new old last next first
    file files folder folders work working chat chats task tasks thing things need needs
    want wants like get got put use used using let lets tell show give take look see find
    help thanks thank yes okay sure now today tomorrow yesterday week month year
    """.split()
)

#: Folder names too common to name a piece of work by themselves.
GENERIC_FOLDER_WORDS = frozenset(
    """
    documents desktop downloads projects project users user home onedrive dropbox drive
    google shared share public private personal clients client data temp tmp src source
    code repos repo github git iron jarvis misc stuff archive archives backup backups
    """.split()
)


def folder_key(path: object) -> str:
    """``path`` normalised for comparing folders: forward slashes, no
    trailing slash, ``.`` / ``..`` folded by TEXT, lower case on Windows.
    "" for nothing. Never touches the disk."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        return ""
    unc = raw.startswith(("\\\\", "//"))
    text = os.path.normpath(raw).replace("\\", "/")
    text = _SLASHES.sub("/", text)
    if unc:
        text = "//" + text.lstrip("/")
    if len(text) > 1:
        text = text.rstrip("/")
        if len(text) == 2 and text[1] == ":":  # "C:" is the drive's top
            text += "/"
    return text.lower() if os.name == "nt" else text


def inside(key: str, base: str) -> bool:
    """Is the folder ``key`` the folder ``base`` or somewhere under it?"""
    if not key or not base:
        return False
    if key == base:
        return True
    return key.startswith(base if base.endswith("/") else base + "/")


def home_folder(path: object) -> str:
    """The folder a coding session's OWN COPY belongs to: for a path inside
    ``<repo>.ij-worktrees/<name>`` that is ``<repo>``; any other path comes
    back as it was. Text only."""
    raw = str(path or "").strip()
    if WORKTREES_SUFFIX not in raw:
        return raw
    parts = re.split(r"[\\/]", raw)
    for i, part in enumerate(parts):
        if part.endswith(WORKTREES_SUFFIX) and len(part) > len(WORKTREES_SUFFIX):
            sep = "\\" if "\\" in raw else "/"
            head = parts[:i] + [part[: -len(WORKTREES_SUFFIX)]]
            return sep.join(head)
    return raw


def folder_name(path: object) -> str:
    """The folder's own name (its last part), for a heading."""
    key = str(path or "").strip().rstrip("\\/")
    if not key:
        return ""
    return re.split(r"[\\/]", key)[-1] or key


def file_name(path: object) -> str:
    return re.split(r"[\\/]", str(path or "").strip().rstrip("\\/"))[-1]


#: File endings: a word that says what KIND of file, never which work.
EXTENSION_WORDS = frozenset(
    """
    xlsx xlsm xls csv pdf docx doc pptx ppt txt json html htm xml yaml yml md png jpg jpeg
    gif svg zip py js ts tsx jsx css sql log bak tmp
    """.split()
)

#: A capitalised word that does not start its sentence: how a NAME is written
#: ("…for Hartleys"). Used only to let one word point at one folder.
_NAMED = re.compile(r"(?<![.!?:;\n])[ \t]+[\"(]?([A-Z][A-Za-z0-9]{3,})")


def words(text: object) -> set[str]:
    """The words of ``text`` that can point at a piece of work: lower-case
    runs of FOUR or more letters or digits that are not a stop word, not a
    bare number (a year matches every client) and not a file ending. Linear
    in the text."""
    return {
        w for w in _WORD.findall(_no_apostrophes(str(text or "")[:4000].lower()))
        if len(w) >= 4 and w not in STOP_WORDS and w not in EXTENSION_WORDS and not w.isdigit()
    }


def _no_apostrophes(text: str) -> str:
    """"Hartley's" and "Hartleys" are one name."""
    return text.replace("'", "").replace("’", "")


def named_words(text: object) -> set[str]:
    """The words of ``text`` written as NAMES: capitalised, and not the first
    word of a sentence ("what did we do for Hartleys" → {"hartleys"};
    "Invoices: how do I number them?" → nothing). Lower case, apostrophes
    dropped. Linear in the text."""
    out = set()
    for m in _NAMED.finditer(_no_apostrophes(str(text or "")[:4000])):
        out.add(m.group(1).lower())
    return out


__all__ = [
    "EXTENSION_WORDS",
    "GENERIC_FOLDER_WORDS",
    "STOP_WORDS",
    "WORKTREES_SUFFIX",
    "file_name",
    "folder_key",
    "folder_name",
    "home_folder",
    "inside",
    "named_words",
    "words",
]
