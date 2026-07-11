"""Tiny structured-markdown parser for the rich document writers.

``parse_markdown(text)`` turns a pragmatic markdown subset into a flat list of
:class:`Block` values that the docx/pdf/pptx/html writers render natively:

* ``#``–``####`` headings (deeper levels clamp to 4)
* ``-``/``*``/``+`` bullets and ``1.``/``1)`` numbered items (2-space nesting)
* fenced ``` code blocks
* ``| a | b |`` pipe tables — with or WITHOUT the outer pipes — and the
  ``|:--:|`` separator row is consumed for per-column alignment (dropped from
  the data rows)
* ``---``/``***``/``___`` horizontal rules
* everything else -> paragraphs, with inline ``**bold**`` / ``*italic*`` /
  `` `code` `` / ``[text](url)`` links / ``![alt](url)`` images. Consecutive
  soft-wrapped plain lines are JOINED into one paragraph (a blank line ends it).

Dependency-free and tolerant by design: it never raises; any line it does not
understand is kept as a plain paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class Run(tuple):
    """One styled inline fragment.

    It stays a 3-``tuple`` ``(text, bold, italic)`` for full backward compat —
    every existing consumer that unpacks ``for text, bold, italic in runs`` or
    tests membership like ``("x", True, False) in runs`` keeps working — while
    carrying the NEW inline attributes (code span / hyperlink / image) as extra
    attributes that writers read with ``getattr(run, "href", None)`` etc.
    (a tuple subtype cannot use non-empty ``__slots__``, so these live on the
    instance ``__dict__``).
    """

    def __new__(
        cls,
        text: str,
        bold: bool = False,
        italic: bool = False,
        *,
        code: bool = False,
        href: str | None = None,
        image: bool = False,
    ) -> "Run":
        obj = super().__new__(cls, (text, bold, italic))
        obj._code = code
        obj._href = href
        obj._image = image
        return obj

    @property
    def text(self) -> str:
        return self[0]

    @property
    def bold(self) -> bool:
        return self[1]

    @property
    def italic(self) -> bool:
        return self[2]

    @property
    def code(self) -> bool:
        return self._code

    @property
    def href(self) -> str | None:
        return self._href

    @property
    def image(self) -> bool:
        return self._image


@dataclass
class Block:
    """One structural element of a parsed document."""

    kind: str  # heading | paragraph | bullet | numbered | code | table | hr
    text: str = ""  # plain text with inline markers stripped
    level: int = 0  # heading level 1-4, or bullet/numbered nesting depth (0+)
    runs: list[Run] = field(default_factory=list)  # inline styling of ``text``
    rows: list[list[str]] = field(default_factory=list)  # table cells only
    aligns: list[str] = field(default_factory=list)  # per-column: "" l/r/center


_HEADING_RX = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RX = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RX = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_HR_RX = re.compile(r"^([-*_])\s*(?:\1\s*){2,}$")
_TABLE_SEP_CELL_RX = re.compile(r":?-+:?")

# Inline spans, longest/most-specific first so ``![img]`` beats ``[link]`` and
# ``**bold**`` beats ``*italic*``. A code span wins over emphasis (markdown rule).
_INLINE_RX = re.compile(
    r"(!\[[^\]]*\]\([^)]*\)"  # image  ![alt](url)
    r"|\[[^\]]*\]\([^)]*\)"  # link   [text](url)
    r"|`[^`]+`"  # code   `x`
    r"|\*\*[^*]+\*\*"  # bold   **x**
    r"|\*[^*]+\*)"  # italic *x*
)
_IMAGE_RX = re.compile(r"^!\[([^\]]*)\]\(([^)]*)\)$")
_LINK_RX = re.compile(r"^\[([^\]]*)\]\(([^)]*)\)$")


def parse_inline(text: str) -> list[Run]:
    """Split a line into styled runs. Never raises; brackets never leak."""
    runs: list[Run] = []
    for part in _INLINE_RX.split(text):
        if part == "":
            continue
        mi = _IMAGE_RX.match(part)
        if mi:
            alt, url = mi.group(1), mi.group(2)
            # alt text is what humans read; url rides along so writers can link.
            runs.append(Run(alt or url, href=url, image=True))
            continue
        ml = _LINK_RX.match(part)
        if ml:
            label, url = ml.group(1), ml.group(2)
            runs.append(Run(label or url, href=url))
            continue
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            runs.append(Run(part[1:-1], code=True))
        elif part.startswith("**") and part.endswith("**") and len(part) >= 5:
            runs.append(Run(part[2:-2], True, False))
        elif part.startswith("*") and part.endswith("*") and len(part) >= 3:
            runs.append(Run(part[1:-1], False, True))
        else:
            runs.append(Run(part, False, False))
    return runs or [Run(text, False, False)]


def _plain(text: str) -> str:
    return "".join(run[0] for run in parse_inline(text))


def _indent_level(indent: str) -> int:
    return min(len(indent.replace("\t", "  ")) // 2, 3)


# --- table helpers -------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    """Split a table row on ``|`` — outer pipes optional (GFM without borders)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_sep_row(line: str) -> bool:
    """True for a ``|---|:--:|`` alignment/separator row (every cell dashes)."""
    cells = _split_row(line)
    return (
        bool(cells)
        and all(_TABLE_SEP_CELL_RX.fullmatch(c) for c in cells)
        and any("-" in c for c in cells)
    )


def _aligns_from_sep(line: str) -> list[str]:
    """Read per-column alignment from a ``:---:`` separator row."""
    aligns: list[str] = []
    for c in _split_row(line):
        left, right = c.startswith(":"), c.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        elif left:
            aligns.append("left")
        else:
            aligns.append("")  # default (renderer decides)
    return aligns


def _try_table(lines: list[str], i: int) -> tuple[Block, int] | None:
    """Parse a table starting at ``i`` (outer-pipe OR GFM borderless). None if not."""
    first = lines[i]
    s = first.strip()
    has_outer = s.startswith("|") and s.count("|") >= 2
    if not has_outer:
        # Borderless GFM only counts as a table when a separator row follows,
        # so a lone "a | b" prose line stays a paragraph.
        if "|" not in s:
            return None
        if _BULLET_RX.match(first) or _NUMBERED_RX.match(first):
            return None
        if i + 1 >= len(lines) or not _is_sep_row(lines[i + 1]):
            return None

    raw: list[str] = []
    j = i
    while j < len(lines):
        t = lines[j].strip()
        if not t:
            break
        if has_outer:
            if not (t.startswith("|") and t.count("|") >= 2):
                break
        else:
            if "|" not in t or t.startswith("```") or _HEADING_RX.match(t):
                break
        raw.append(lines[j])
        j += 1

    aligns: list[str] = []
    rows: list[list[str]] = []
    for r in raw:
        if _is_sep_row(r):
            aligns = _aligns_from_sep(r)  # consumed, never a data row
            continue
        rows.append([_plain(c) for c in _split_row(r)])
    if not rows:
        return None
    return Block(kind="table", rows=rows, aligns=aligns), j


def _starts_block(lines: list[str], i: int) -> bool:
    """True if a NON-paragraph block begins at ``i`` (ends paragraph joining)."""
    line = lines[i]
    s = line.strip()
    if not s:
        return True
    if s.startswith("```"):
        return True
    if _HR_RX.match(s):
        return True
    if _HEADING_RX.match(s):
        return True
    if _BULLET_RX.match(line) or _NUMBERED_RX.match(line):
        return True
    if s.startswith("|") and s.count("|") >= 2:
        return True
    # borderless GFM table header (needs its separator row on the next line)
    if "|" in s and i + 1 < len(lines) and _is_sep_row(lines[i + 1]):
        return True
    return False


# --- main parse ---------------------------------------------------------------


def parse_markdown(text: str) -> list[Block]:
    """Parse ``text`` into blocks; tolerant — unknown lines become paragraphs."""
    raw = "" if text is None else str(text)
    try:
        return _parse(raw)
    except Exception:  # a parser bug must never take a document write down
        return [
            Block(kind="paragraph", text=ln, runs=[Run(ln, False, False)])
            for ln in raw.split("\n")
            if ln.strip()
        ]


def _parse(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:  # blank lines separate blocks but emit nothing
            i += 1
            continue

        if stripped.startswith("```"):  # fenced code (unclosed fence tolerated)
            body: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # skip the closing fence (or run off the end)
            blocks.append(Block(kind="code", text="\n".join(body)))
            continue

        tbl = _try_table(lines, i)  # pipe table, outer or borderless
        if tbl is not None:
            block, i = tbl
            blocks.append(block)
            continue

        if _HR_RX.match(stripped):
            blocks.append(Block(kind="hr"))
            i += 1
            continue

        m = _HEADING_RX.match(stripped)
        if m:
            level = min(len(m.group(1)), 4)
            body_text = m.group(2).strip()
            blocks.append(
                Block(
                    kind="heading",
                    text=_plain(body_text),
                    level=level,
                    runs=parse_inline(body_text),
                )
            )
            i += 1
            continue

        mb = _BULLET_RX.match(line)
        mn = _NUMBERED_RX.match(line)
        if mb or mn:
            m2 = mb or mn
            kind = "bullet" if mb else "numbered"
            body_text = m2.group(2).strip()
            blocks.append(
                Block(
                    kind=kind,
                    text=_plain(body_text),
                    level=_indent_level(m2.group(1)),
                    runs=parse_inline(body_text),
                )
            )
            i += 1
            continue

        # Paragraph: accumulate consecutive soft-wrapped plain lines into ONE
        # block (markdown treats a single newline as a soft wrap), stopping at a
        # blank line or the start of any other block. This keeps prose from
        # fragmenting into one paragraph per physical line.
        para = [stripped]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        joined = " ".join(para)
        blocks.append(
            Block(kind="paragraph", text=_plain(joined), runs=parse_inline(joined))
        )
    return blocks
