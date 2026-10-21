"""The exact-edit engine every text editor in the harness shares (v1.364.0).

``edit_file`` used to replace the FIRST occurrence of ``old``: an ambiguous
edit landed silently in the wrong place and reported success. Claude Code's
``Edit`` refuses instead — the text must occur exactly once, or the caller
asks for every occurrence with ``replace_all`` — and so does this engine, for
``edit_file``, ``multi_edit`` and ``apply_patch`` alike.

Three things the old ``read_text`` / ``write_text`` pair got wrong are fixed
here once, for every caller:

* LINE ENDINGS ARE KEPT. ``write_text`` on Windows turns every ``\\n`` into
  ``\\r\\n``, so editing one line of an LF file rewrote every line of it. The
  file is read as bytes, matched with LF newlines (a model sends ``\\n``), and
  written back in the file's own style.
* A BOM IS KEPT, and never sits in front of the first line where a model's
  ``old`` text cannot match it.
* A FILE THAT IS NOT TEXT is refused in one sentence (it used to surface as a
  ``UnicodeDecodeError`` traceback).

Pure functions plus one blocking reader (:func:`load_text`): callers run the
reader and the write in a worker thread.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

#: A file bigger than this is not edited in memory (a log, a dump) — the
#: caller names the size instead of freezing on it.
MAX_EDIT_BYTES = 10_000_000

#: Lines the nearest-line hint compares at most (bounded on a huge file).
_HINT_MAX_LINES = 20_000
#: The hint shows at most this much of the line.
_HINT_SHOW = 120
#: Below this similarity the hint says nothing (a guess is worse than none).
_HINT_MIN_RATIO = 0.6

_BOM_UTF8 = b"\xef\xbb\xbf"


class TextFileError(Exception):
    """A file that cannot be edited as text; the message is the sentence."""


class EditError(Exception):
    """An edit that cannot be applied; the message is the sentence."""


@dataclass
class TextFile:
    """A text file as the editors see it: LF newlines, no BOM in ``text``."""

    raw: bytes
    text: str
    newline: str = "\n"
    encoding: str = "utf-8"
    bom: bool = False

    def encode(self, text: str) -> bytes:
        """``text`` (LF newlines) as bytes in this file's own encoding, line
        ending style and BOM."""
        return encode_text(text, self.newline, self.encoding, self.bom)


def normalize_newlines(text: str) -> str:
    """``\\r\\n`` → ``\\n`` (a lone ``\\r`` is left alone: it is content)."""
    return text.replace("\r\n", "\n")


def _newline_style(text: str) -> str:
    crlf = text.count("\r\n")
    if not crlf:
        return "\n"
    # The majority wins; a tie keeps CRLF (the Windows default).
    return "\r\n" if crlf * 2 >= text.count("\n") else "\n"


def decode_text(raw: bytes, name: str = "the file") -> TextFile:
    """Bytes → :class:`TextFile`, or :class:`TextFileError` in one sentence.

    UTF-16 with a BOM (PowerShell 5.1's ``>``) is text; a NUL byte anywhere
    else means binary; anything that is not UTF-8 is refused rather than
    guessed — an edit written back in a guessed encoding changes bytes the
    model never touched."""
    encoding = "utf-8"
    bom = False
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16"
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise TextFileError(f"{name} is not readable UTF-16 text ({exc.reason})") from None
    else:
        if b"\x00" in raw:
            raise TextFileError(f"{name} is a binary file, not text — it cannot be edited here")
        body = raw
        if raw.startswith(_BOM_UTF8):
            bom = True
            body = raw[len(_BOM_UTF8):]
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise TextFileError(
                f"{name} is not UTF-8 text, so it is not edited here "
                "(re-save it as UTF-8, or use write_document for an office file)"
            ) from None
    newline = _newline_style(text)
    return TextFile(raw=raw, text=normalize_newlines(text), newline=newline,
                    encoding=encoding, bom=bom)


def encode_text(text: str, newline: str = "\n", encoding: str = "utf-8", bom: bool = False) -> bytes:
    """LF text → bytes with the given line endings, encoding and BOM."""
    body = text.replace("\n", "\r\n") if newline == "\r\n" else text
    if encoding == "utf-16":
        return body.encode("utf-16")  # Python's utf-16 codec writes the BOM
    data = body.encode("utf-8")
    return _BOM_UTF8 + data if bom else data


def load_text(path: Path, name: str | None = None) -> TextFile:
    """Read ``path`` for editing. BLOCKING — call from a worker thread."""
    label = name or path.name
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TextFileError(f"could not read {label}: {exc}") from None
    if size > MAX_EDIT_BYTES:
        raise TextFileError(
            f"{label} is {size:,} bytes — over the {MAX_EDIT_BYTES:,}-byte limit "
            "for an in-place edit"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TextFileError(f"could not read {label}: {exc}") from None
    return decode_text(raw, label)


def nearest_line_hint(text: str, old: str) -> str:
    """One short sentence naming the file's line most like ``old``'s first
    non-blank line, or "" when nothing is close. Bounded."""
    probe = next((ln.strip() for ln in normalize_newlines(old).split("\n") if ln.strip()), "")
    if not probe:
        return ""
    best_ratio = 0.0
    best_no = 0
    best_line = ""
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(probe[:400])
    for no, line in enumerate(text.split("\n")[:_HINT_MAX_LINES], 1):
        stripped = line.strip()
        if not stripped:
            continue
        matcher.set_seq1(stripped[:400])
        if matcher.real_quick_ratio() <= best_ratio or matcher.quick_ratio() <= best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_no, best_line = ratio, no, line
    if best_ratio < _HINT_MIN_RATIO:
        return ""
    shown = best_line.strip()
    if len(shown) > _HINT_SHOW:
        shown = shown[: _HINT_SHOW - 1] + "…"
    return f"The closest line in the file is line {best_no}: {shown!r}."


def apply_exact_edit(text: str, old: str, new: str, *, replace_all: bool = False,
                     where: str = "the file") -> tuple[str, int]:
    """``(new_text, replacements)`` or :class:`EditError` in one sentence.

    ``old`` must occur EXACTLY ONCE unless ``replace_all``; zero occurrences
    name the nearest line, more than one say how many. ``old``/``new`` are
    compared with LF newlines, like ``text``."""
    old_n = normalize_newlines(old)
    new_n = normalize_newlines(new)
    if not old_n:
        raise EditError(
            "`old` is empty — give the exact text to replace (to create a file "
            "use write_file)"
        )
    if old_n == new_n:
        raise EditError("`old` and `new` are the same — there is nothing to change")
    count = text.count(old_n)
    if count == 0:
        hint = nearest_line_hint(text, old_n)
        msg = f"`old` text was not found in {where}."
        if hint:
            msg += f" {hint}"
        raise EditError(msg + " Copy the text exactly, including indentation.")
    if count > 1 and not replace_all:
        raise EditError(
            f"`old` text occurs {count} times in {where} — add surrounding lines "
            "so it matches exactly one place, or pass replace_all: true to change "
            "every occurrence"
        )
    if replace_all:
        return text.replace(old_n, new_n), count
    return text.replace(old_n, new_n, 1), 1
