"""Tiny structured-markdown parser for the rich document writers.

``parse_markdown(text)`` turns a pragmatic markdown subset into a flat list of
:class:`Block` values that the docx/pdf/pptx/html writers render natively:

* ``#``–``####`` headings (deeper levels clamp to 4)
* ``-``/``*``/``+`` bullets and ``1.``/``1)`` numbered items (2-space nesting)
* fenced ``` code blocks
* ``| a | b |`` pipe tables (the ``|---|`` separator row is dropped)
* ``---``/``***``/``___`` horizontal rules
* everything else -> paragraphs, with ``**bold**`` / ``*italic*`` inline runs

Dependency-free and tolerant by design: it never raises; any line it does not
understand is kept as a plain paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: One styled fragment of a line: ``(text, bold, italic)``.
Run = tuple[str, bool, bool]

@dataclass
class Block:
    """One structural element of a parsed document."""

    kind: str  # heading | paragraph | bullet | numbered | code | table | hr
    text: str = ""  # plain text with inline markers stripped
    level: int = 0  # heading level 1-4, or bullet/numbered nesting depth (0+)
    runs: list[Run] = field(default_factory=list)  # inline styling of ``text``
    rows: list[list[str]] = field(default_factory=list)  # table cells only

_HEADING_RX = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RX = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RX = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_HR_RX = re.compile(r"^([-*_])\s*(?:\1\s*){2,}$")
_INLINE_RX = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
_TABLE_SEP_CELL_RX = re.compile(r":?-+:?")

def parse_inline(text: str) -> list[Run]:
    """Split a line into ``(text, bold, italic)`` runs. Never raises."""
    runs: list[Run] = []
    for part in _INLINE_RX.split(text):
        if part == "":
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 5:
            runs.append((part[2:-2], True, False))
        elif part.startswith("*") and part.endswith("*") and len(part) >= 3:
            runs.append((part[1:-1], False, True))
        else:
            runs.append((part, False, False))
    return runs or [(text, False, False)]

def _plain(text: str) -> str:
    return "".join(run[0] for run in parse_inline(text))

def _indent_level(indent: str) -> int:
    return min(len(indent.replace("\t", "  ")) // 2, 3)

def parse_markdown(text: str) -> list[Block]:
    """Parse ``text`` into blocks; tolerant — unknown lines become paragraphs."""
    raw = "" if text is None else str(text)
    try:
        return _parse(raw)
    except Exception:  # a parser bug must never take a document write down
        return [
            Block(kind="paragraph", text=ln, runs=[(ln, False, False)])
            for ln in raw.split("\n")
            if ln.strip()
        ]

def _parse(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:  # blank lines separate blocks but emit nothing
            i += 1
            continue

        if stripped.startswith("```"):  # fenced code (unclosed fence tolerated)
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # skip the closing fence (or run off the end)
            blocks.append(Block(kind="code", text="\n".join(body)))
            continue

        if stripped.startswith("|") and stripped.count("|") >= 2:  # pipe table
            rows: list[list[str]] = []
            while i < len(lines):
                s = lines[i].strip()
                if not (s.startswith("|") and s.count("|") >= 2):
                    break
                cells = [c.strip() for c in s.strip("|").split("|")]
                if not all(_TABLE_SEP_CELL_RX.fullmatch(c) for c in cells):
                    rows.append([_plain(c) for c in cells])
                i += 1
            if rows:
                blocks.append(Block(kind="table", rows=rows))
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

        for rx, kind in ((_BULLET_RX, "bullet"), (_NUMBERED_RX, "numbered")):
            m = rx.match(line)
            if m:
                body_text = m.group(2).strip()
                blocks.append(
                    Block(
                        kind=kind,
                        text=_plain(body_text),
                        level=_indent_level(m.group(1)),
                        runs=parse_inline(body_text),
                    )
                )
                break
        if m:
            i += 1
            continue

        blocks.append(
            Block(kind="paragraph", text=_plain(stripped), runs=parse_inline(stripped))
        )
        i += 1
    return blocks
