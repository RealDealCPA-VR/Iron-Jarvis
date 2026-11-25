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


#: What a missed edit says when no line of the file is even close to ``old``.
NOTHING_CLOSE = (
    " No line of the file is close to it, so the file does not hold what you"
    " expect. Read the file again, then copy the text to replace from what it"
    " holds now."
)

#: v1.572.0: ...and when that file is SHORT, the refusal shows all of it, so
#: the next call is a copy instead of one more read (the benchmark: every
#: such miss was followed by a read of a file this small, or by a guess).
#: Past either bound the refusal says to read the file, as before.
SHORT_FILE_LINES = 60
SHORT_FILE_CHARS = 4000
NOTHING_CLOSE_SHOWN = (
    " No line of the file is close to it, so the file does not hold what you"
    " expect. It is a short file; all of it, as it is now (copy the text to"
    " replace from this, without the line numbers):"
)


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


#: v1.508.0 — what a missed edit SHOWS of the file as it is now. The ledger
#: (v1.500.2) had a worker send eleven refused edits of two test files in a
#: row, between grep calls: each refusal named one line and the model guessed
#: again. A refusal that shows the lines around the closest one (and, for text
#: that differs only in spacing, the exact text there) makes the next call a
#: copy instead of a guess. The engine still REFUSES: nothing is applied on a
#: loose match (``parity.py``: "strict: unique match or refuse").
_WINDOW_BEFORE = 3
_WINDOW_AFTER = 3
_WINDOW_LINE_CHARS = 160
#: The exact text of a spacing-only match is shown up to this size; past it
#: the refusal names the lines to read instead.
_LOOSE_SHOW_LINES = 30
_LOOSE_SHOW_CHARS = 3000


def numbered_window(text: str, first: int, last: "int | None" = None, *,
                    before: int = _WINDOW_BEFORE, after: int = _WINDOW_AFTER) -> str:
    """Lines ``first``..``last`` (1-based) of ``text`` with ``before`` /
    ``after`` lines of context, each as ``"  N| line"`` (long lines cut).
    "" for a line outside the file. Pure, bounded."""
    lines = text.split("\n")
    last = first if last is None else last
    if first < 1 or first > len(lines):
        return ""
    lo = max(1, first - before)
    hi = min(len(lines), last + after)
    width = len(str(hi))
    out = []
    for no in range(lo, hi + 1):
        line = lines[no - 1]
        if len(line) > _WINDOW_LINE_CHARS:
            line = line[: _WINDOW_LINE_CHARS - 1] + "…"
        out.append(f"  {str(no).rjust(width)}| {line}")
    return "\n".join(out)


def _nearest_line_no(hint: str) -> int:
    """The 1-based line a :func:`nearest_line_hint` sentence names, or 0."""
    import re

    m = re.search(r"is line (\d+):", hint or "")
    return int(m.group(1)) if m else 0


def loose_matches(text: str, old: str, limit: int = 3) -> list[tuple[int, int]]:
    """Where ``old`` matches WHOLE LINES of ``text`` when each line's leading
    and trailing spacing is ignored: up to ``limit`` ``(first, last)`` 1-based
    line spans. Blank lines at either end of ``old`` are not compared. Pure,
    bounded (:data:`_HINT_MAX_LINES`)."""
    probe = [ln.strip() for ln in normalize_newlines(old).split("\n")]
    while probe and not probe[0]:
        probe.pop(0)
    while probe and not probe[-1]:
        probe.pop()
    if not probe:
        return []
    lines = [ln.strip() for ln in text.split("\n")[:_HINT_MAX_LINES]]
    n = len(probe)
    found: list[tuple[int, int]] = []
    for i in range(0, len(lines) - n + 1):
        if lines[i] == probe[0] and lines[i:i + n] == probe:
            found.append((i + 1, i + n))
            if len(found) >= limit:
                break
    return found


def occurrence_lines(text: str, old: str, limit: int = 5) -> list[int]:
    """The 1-based line each of the first ``limit`` occurrences of ``old``
    starts on. Pure."""
    out: list[int] = []
    start = 0
    while old and len(out) < limit:
        at = text.find(old, start)
        if at < 0:
            break
        out.append(text.count("\n", 0, at) + 1)
        start = at + len(old)
    return out


def _lines_words(nos: list[int]) -> str:
    words = [str(n) for n in nos]
    if len(words) <= 1:
        return "line " + "".join(words)
    return "lines " + ", ".join(words[:-1]) + " and " + words[-1]


def _not_found_message(text: str, old_n: str, where: str) -> str:
    """The refusal for ``old`` text that is not in the file, with what the
    file holds now (v1.508.0)."""
    msg = f"`old` text was not found in {where}."
    total = len(text.split("\n"))
    if total > _HINT_MAX_LINES:
        return msg + " Copy the text exactly, including indentation."
    loose = loose_matches(text, old_n)
    if len(loose) == 1:
        first, last = loose[0]
        span = f"line {first}" if first == last else f"lines {first}-{last}"
        exact = "\n".join(text.split("\n")[first - 1:last])
        said = (f"{msg} It matches {span} only when the spacing at the start and end of each "
                "line is ignored.")
        if last - first + 1 <= _LOOSE_SHOW_LINES and len(exact) <= _LOOSE_SHOW_CHARS:
            return (f"{said} The file holds exactly this there; copy it as `old`, with its "
                    f"indentation:\n<<<\n{exact}\n>>>")
        return (f"{said} Read {span} (read_file with offset and numbered: true) and copy the "
                "text exactly.")
    if len(loose) > 1:
        return (f"{msg} When spacing is ignored it matches {len(loose)} places "
                f"({_lines_words([a for a, _b in loose])}): copy the exact text of the one you "
                "mean, with a line around it that makes it unique.")
    hint = nearest_line_hint(text, old_n)
    no = _nearest_line_no(hint)
    if no:
        lo, hi = max(1, no - _WINDOW_BEFORE), min(total, no + _WINDOW_AFTER)
        return (f"{msg} {hint} Copy the text exactly, including "
                f"indentation. Lines {lo}-{hi} of the file now:\n{numbered_window(text, no)}")
    # v1.462.0. Read off the live ledger: 83 of 101 failed edits by team
    # workers had NO line of the file close to `old` (the text was written
    # from memory, or the file had changed). "Copy the text exactly" is the
    # wrong advice for that: there is nothing to copy until the file is read
    # again.
    if text.strip() and total <= SHORT_FILE_LINES and len(text) <= SHORT_FILE_CHARS:
        # (a file that ends in a newline has no line after it to show)
        last = total - 1 if text.endswith("\n") and total > 1 else total
        return msg + NOTHING_CLOSE_SHOWN + "\n" + numbered_window(text, 1, last, before=0, after=0)
    return msg + NOTHING_CLOSE


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
        raise EditError(_not_found_message(text, old_n, where))
    if count > 1 and not replace_all:
        at = occurrence_lines(text, old_n)
        more = "" if len(at) >= count else " and more"
        raise EditError(
            f"`old` text occurs {count} times in {where} ({_lines_words(at)}{more}) — add "
            "surrounding lines so it matches exactly one place, or pass replace_all: true to "
            "change every occurrence"
        )
    if replace_all:
        return text.replace(old_n, new_n), count
    return text.replace(old_n, new_n, 1), 1
