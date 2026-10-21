"""Codex's ``apply_patch`` format (V4A), parsed and applied (v1.364.0).

The grammar is Codex 0.157.0's own (``codex-rs/core/assets/tools/
apply_patch.lark``, recorded in ``tests/fixtures/harness_tool_inventory_v1364
.json``)::

    *** Begin Patch
    *** Add File: <path>          followed by "+" lines (the new file)
    *** Delete File: <path>
    *** Update File: <path>
    *** Move to: <new path>       (optional, right after Update File)
    @@ <context line>             (optional; the first chunk may omit "@@")
     unchanged line / -removed line / +added line
    *** End of File               (optional: the chunk sits at the end)
    *** End Patch

and so is the behaviour, from ``apply-patch/src`` at that tag:

* LENIENT. Whitespace around the markers is tolerated and a heredoc wrapper
  (``<<'EOF'`` … ``EOF``) is unwrapped.
* CONTEXT. An ``@@ <ctx>`` header first finds that one line; the chunk's old
  lines (context + ``-``) are then searched AFTER it. Chunks apply in order
  with a moving cursor; a chunk with no old lines appends at the end.
* THE MATCHING LADDER (``seek_sequence``): exact, then ignoring trailing
  whitespace, then ignoring both ends, then with Unicode dashes / quotes /
  odd spaces normalised to ASCII. ``*** End of File`` searches from the end.
* An old block ending in an empty line that is not found is retried without
  that last empty line.

Two deliberate differences, both toward the user's files:

* LINE ENDINGS ARE KEPT (Codex rewrites with LF; its "preserve" mode is
  still under development there). A Windows user's CRLF file stays CRLF.
* ``*** Move to:`` onto a file that already exists is REFUSED, and so is a
  path that leaves the workspace — the ``safe_path`` rule every file tool
  obeys.

Everything here is pure: :func:`parse_patch` reads text, :func:`apply_update`
transforms one file's text. The tool (``harness/tools.ApplyPatchTool``) runs
the whole patch against an in-memory view of the files first and writes
nothing unless every hunk applies — all or nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BEGIN = "*** Begin Patch"
END = "*** End Patch"
ADD = "*** Add File: "
DELETE = "*** Delete File: "
UPDATE = "*** Update File: "
MOVE = "*** Move to: "
EOF_MARK = "*** End of File"

#: A patch bigger than this is refused before parsing (a whole repository
#: pasted into one call is not an edit).
MAX_PATCH_CHARS = 2_000_000
#: At most this many files in one patch.
MAX_PATCH_FILES = 200


class PatchError(ValueError):
    """A patch that cannot be parsed or applied; the message is the
    sentence the model reads."""


@dataclass
class Chunk:
    """One ``@@`` block of an Update hunk."""

    context: str | None = None
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    end_of_file: bool = False


@dataclass
class Hunk:
    """One file operation. ``kind`` is ``add``, ``delete`` or ``update``."""

    kind: str
    path: str
    contents: str = ""  # add
    move_to: str | None = None  # update
    chunks: list[Chunk] = field(default_factory=list)  # update
    #: 1-based position in the patch, for error sentences.
    index: int = 0

    def header(self) -> str:
        label = {"add": ADD, "delete": DELETE, "update": UPDATE}[self.kind]
        return f"{label}{self.path}".strip()


def _unwrap_heredoc(lines: list[str]) -> list[str]:
    """Codex's lenient mode: ``<<EOF`` / ``<<'EOF'`` / ``<<"EOF"`` as the first
    line and a last line ending in ``EOF`` (at least 4 lines) wrap the patch.
    A leading ``apply_patch`` word (the shell form) is tolerated too."""
    if len(lines) < 4:
        return lines
    first = lines[0].strip()
    if first.startswith("apply_patch"):
        first = first[len("apply_patch"):].strip()
    if first in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].strip().endswith("EOF"):
        return lines[1:-1]
    return lines


def parse_patch(text: str) -> list[Hunk]:
    """The hunks of a patch, or :class:`PatchError` naming the first problem."""
    if not isinstance(text, str) or not text.strip():
        raise PatchError("the patch is empty")
    if len(text) > MAX_PATCH_CHARS:
        raise PatchError(
            f"the patch is {len(text):,} characters — over the {MAX_PATCH_CHARS:,} limit; "
            "split it into smaller patches"
        )
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    lines = _unwrap_heredoc(lines)
    if not lines or lines[0].strip() != BEGIN:
        raise PatchError(f"The first line of the patch must be '{BEGIN}'")
    if lines[-1].strip() != END:
        raise PatchError(f"The last line of the patch must be '{END}'")
    body = lines[1:-1]
    hunks: list[Hunk] = []
    i = 0
    while i < len(body):
        raw = body[i]
        line = raw.strip()
        if not line:
            i += 1
            continue
        index = len(hunks) + 1
        if raw.lstrip().startswith(ADD):
            path = raw.lstrip()[len(ADD):].strip()
            if not path:
                raise PatchError(f"hunk {index}: '*** Add File:' needs a path")
            i += 1
            content_lines: list[str] = []
            while i < len(body) and body[i].startswith("+"):
                content_lines.append(body[i][1:])
                i += 1
            contents = "".join(f"{ln}\n" for ln in content_lines)
            hunks.append(Hunk(kind="add", path=path, contents=contents, index=index))
            continue
        if raw.lstrip().startswith(DELETE):
            path = raw.lstrip()[len(DELETE):].strip()
            if not path:
                raise PatchError(f"hunk {index}: '*** Delete File:' needs a path")
            hunks.append(Hunk(kind="delete", path=path, index=index))
            i += 1
            continue
        if raw.lstrip().startswith(UPDATE):
            path = raw.lstrip()[len(UPDATE):].strip()
            if not path:
                raise PatchError(f"hunk {index}: '*** Update File:' needs a path")
            i += 1
            move_to: str | None = None
            if i < len(body) and body[i].lstrip().startswith(MOVE):
                move_to = body[i].lstrip()[len(MOVE):].strip() or None
                i += 1
            chunks, i = _parse_chunks(body, i, path, index)
            if not chunks:
                raise PatchError(f"Update file hunk for path '{path}' is empty")
            hunks.append(Hunk(kind="update", path=path, move_to=move_to, chunks=chunks, index=index))
            continue
        raise PatchError(
            f"'{line}' is not a valid hunk header. Valid hunk headers: "
            f"'{ADD}{{path}}', '{DELETE}{{path}}', '{UPDATE}{{path}}'"
        )
    if not hunks:
        raise PatchError("the patch has no hunks — nothing to apply")
    if len(hunks) > MAX_PATCH_FILES:
        raise PatchError(
            f"the patch changes {len(hunks)} files — at most {MAX_PATCH_FILES} per call"
        )
    return hunks


def _is_hunk_header(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(ADD) or s.startswith(DELETE) or s.startswith(UPDATE)


def _parse_chunks(body: list[str], i: int, path: str, index: int) -> tuple[list[Chunk], int]:
    chunks: list[Chunk] = []
    current: Chunk | None = None
    while i < len(body):
        raw = body[i]
        stripped = raw.strip()
        # Markers sit at column 0 inside an Update hunk: " *** x" is a
        # context line whose text starts with "***".
        if raw.startswith("***") and _is_hunk_header(raw):
            break
        if raw.startswith("***") and stripped == EOF_MARK:
            if current is None:
                current = Chunk()
                chunks.append(current)
            current.end_of_file = True
            current = None  # the next line starts a new chunk (or a hunk)
            i += 1
            continue
        if raw.startswith("@@"):
            # A header sits at column 0; " @@ x" is a context line whose
            # text starts with "@@".
            ctx = raw.strip()[2:].strip()
            current = Chunk(context=ctx or None)
            chunks.append(current)
            i += 1
            continue
        if current is None:
            if chunks:
                # After a chunk that ended at "*** End of File": blank lines
                # between chunks are skipped, anything else needs its "@@".
                if not stripped:
                    i += 1
                    continue
                raise PatchError(
                    f"hunk {index} ({UPDATE}{path}): Expected update hunk to start "
                    f"with a @@ context marker, got: '{raw}'"
                )
            # The first chunk may omit its "@@" header.
            current = Chunk()
            chunks.append(current)
        if raw == "":
            # A bare empty line inside an update hunk is an empty context line.
            current.old_lines.append("")
            current.new_lines.append("")
        elif raw[0] == " ":
            current.old_lines.append(raw[1:])
            current.new_lines.append(raw[1:])
        elif raw[0] == "-":
            current.old_lines.append(raw[1:])
        elif raw[0] == "+":
            current.new_lines.append(raw[1:])
        elif stripped.startswith("***"):
            raise PatchError(
                f"hunk {index} ({UPDATE}{path}): '{stripped}' is not a valid line here"
            )
        else:
            raise PatchError(
                f"hunk {index} ({UPDATE}{path}): Unexpected line found in update hunk: "
                f"'{raw}'. Every line should start with ' ' (context line), '+' (added "
                "line), or '-' (removed line)"
            )
        i += 1
    # A chunk header with nothing under it is no change.
    chunks = [c for c in chunks if c.old_lines or c.new_lines or c.context is not None]
    return chunks, i


# --------------------------------------------------------------------------- #
# Matching (seek_sequence) and applying one Update hunk
# --------------------------------------------------------------------------- #

_PUNCT = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", "　": " ",
})


def _norm_punct(s: str) -> str:
    return s.strip().translate(_PUNCT)


#: The ladder, loosest last. Each rung maps a line to the form compared.
_LADDER = (
    lambda s: s,
    lambda s: s.rstrip(),
    lambda s: s.strip(),
    _norm_punct,
)


def seek_sequence(lines: list[str], pattern: list[str], start: int, eof: bool) -> int | None:
    """Where ``pattern`` starts in ``lines`` at or after ``start``, trying the
    ladder rung by rung; ``eof`` searches from the end of the file first."""
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    last = len(lines) - len(pattern)
    # ``*** End of File`` anchors the block to the LAST lines (Codex: the
    # search starts at len(lines) - len(pattern), so only that spot fits).
    begin = last if eof else start
    for rung in _LADDER:
        want = [rung(p) for p in pattern]
        for i in range(max(begin, 0), last + 1):
            if all(rung(lines[i + k]) == want[k] for k in range(len(pattern))):
                return i
    return None


def apply_update(text: str, hunk: Hunk) -> str:
    """``text`` (LF newlines) with every chunk of ``hunk`` applied, or
    :class:`PatchError` naming the chunk that did not fit. The result ends
    with one newline, as Codex writes it."""
    original = text.split("\n")
    if original and original[-1] == "":
        original.pop()
    replacements: list[tuple[int, int, list[str]]] = []
    cursor = 0
    where = hunk.header()
    for n, chunk in enumerate(hunk.chunks, 1):
        if chunk.context is not None:
            idx = seek_sequence(original, [chunk.context], cursor, False)
            if idx is None:
                raise PatchError(
                    f"hunk {hunk.index} ({where}), chunk {n}: Failed to find context "
                    f"'{chunk.context}' in {hunk.path}"
                )
            cursor = idx + 1
        if not chunk.old_lines:
            # A pure addition goes at the end of the file.
            replacements.append((len(original), 0, list(chunk.new_lines)))
            continue
        pattern = chunk.old_lines
        new_slice = chunk.new_lines
        found = seek_sequence(original, pattern, cursor, chunk.end_of_file)
        if found is None and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = seek_sequence(original, pattern, cursor, chunk.end_of_file)
        if found is None:
            shown = "\n".join(chunk.old_lines)
            if len(shown) > 1200:
                shown = shown[:1200] + "\n…"
            raise PatchError(
                f"hunk {hunk.index} ({where}), chunk {n}: Failed to find expected lines "
                f"in {hunk.path}:\n{shown}"
            )
        replacements.append((found, len(pattern), list(new_slice)))
        cursor = found + len(pattern)
    replacements.sort(key=lambda r: r[0])
    # Overlapping chunks would silently double-apply; refuse instead.
    for (a_start, a_len, _), (b_start, _b_len, _) in zip(replacements, replacements[1:]):
        if a_start + a_len > b_start:
            raise PatchError(
                f"hunk {hunk.index} ({where}): two chunks change overlapping lines of {hunk.path}"
            )
    result = list(original)
    for start, length, new in reversed(replacements):
        result[start : start + length] = new
    if not result or result[-1] != "":
        result.append("")
    return "\n".join(result)
