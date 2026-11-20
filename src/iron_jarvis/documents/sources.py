"""Figures in a reply link to the place they were read (v1.547.0, idea 30-a).

THE RULE: a link comes only from Iron Jarvis's own record of what it read,
written by code at the moment of the read, never from what a model wrote.

* WHEN A FILE IS READ (``read_document``, ``extract_pdf``, ``excel_read``,
  ``excel_query``, and a file attached to a chat message), the reader says
  how its answer is laid out: a ``source_map`` in ``ToolResult.data`` (which
  stretch of the answer is the file's own text, where each page, paragraph,
  table row or sheet row starts). After the call's ledger row is written,
  the registry turns the answer and its map into MARKS (:func:`record_marks`):
  every figure in the file's text, by code (``core/numbers.figures``), with
  its place (``{page, line}``, ``{paragraph}``, ``{table, row, col}``,
  ``{line}`` (and ``col`` for a CSV), ``{sheet, cell}``) and the line it
  sat on (secret-masked). They are kept in ``sourcemark``
  (``documents/source_store``). At most :data:`MAX_MARKS_PER_READ` a read.
  Marks are written only for a chat turn (its tool context carries the
  turn's :class:`SourceTurn`); an agent run writes none yet (idea 30-c).
* WHEN THE REPLY SETTLES, both chat lanes call :func:`link_reply` on the
  model's own words (never on Iron Jarvis's notes under them): a figure
  whose key matches a mark of THIS turn becomes ``[48,210](#source/<id>)``
  (several places with the same value: one link, several ids, a chooser);
  any other figure becomes ``[73,519](#source/none)``, which the page draws
  with a faint dotted line and whose press says there is no record. Only
  when the turn read at least one file: a plain chat is left alone. A
  ``#source/`` link the MODEL wrote is turned back into plain text first, so
  a model can never mint one. Code, links, addresses and HTML are never
  touched. The linked text is what the done frame carries, so the saved
  chat keeps its links.

What a classifier (or this module) may never do: change what is read. Marks
are computed from text that was already extracted for the model; nothing
here reads a file a second time except to hash it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.numbers import figures, number_key

log = logging.getLogger(__name__)

#: A figure's link: ``#source/<id>[,<id>…]``. Twinned in
#: ``dashboard/lib/sourceLink.ts`` (pinned equal by the tests).
SOURCE_LINK_PREFIX = "#source/"
#: The link of a figure the turn has no record for.
NO_RECORD = "none"
#: What pressing such a figure says (twinned in ``lib/sourceLink.ts``).
NO_RECORD_WORDS = "No record of where this came from: Jarvis worked it out or wrote it itself."

MAX_MARKS_PER_READ = 3000
#: A turn that reads many files keeps at most this many marks in memory.
MAX_MARKS_PER_TURN = 20_000
CONTEXT_CHARS = 200
#: The most places one link offers (a chooser).
MAX_IDS_PER_LINK = 6
#: A file larger than this is not hashed (the mark keeps no fingerprint).
HASH_MAX_BYTES = 64 * 1024 * 1024
#: How far into a reader's text the layout is kept (the model is shown at
#: most 16,000 characters of it).
LAYOUT_LIMIT = 65_536

#: A page label: ``[page 7]`` on a line of its own (the OCR transcript and the
#: attachment reader both write it).
PAGE_LABEL_RX = re.compile(r"^\[page (\d+)\][ \t]*$", re.MULTILINE)

#: Files read as plain lines. A CSV or TSV also gets a column.
TEXT_KINDS = frozenset({".txt", ".md", ".markdown", ".log", ".json", ".yaml", ".yml", ".xml", ".rtf"})
TABLE_TEXT = frozenset({".csv", ".tsv"})
SHEET_SUFFIXES = frozenset({".xlsx", ".xlsm"})


@dataclass(frozen=True)
class Mark:
    """One figure read from a file: where (``locator``), as written
    (``shown``), as compared (``key``), and the line it sat on (``context``,
    at most 200 characters, secret-masked)."""

    locator: dict
    shown: str
    key: str
    context: str


# --------------------------------------------------------------------------- #
# finding the figures
# --------------------------------------------------------------------------- #
def _mask(text: str) -> str:
    from ..detections.redact import mask

    return mask(text) if text else text


def _clip(line: str, start: int, end: int, cap: int = CONTEXT_CHARS) -> str:
    """The line around ``line[start:end]``, at most ``cap`` characters."""
    text = line.replace("\t", "  ") if "\t" in line else line
    if len(text.strip()) <= cap:
        return text.strip()
    room = cap - 2  # the two ellipses
    a = max(0, start - (room - (end - start)) // 2)
    b = min(len(text), a + room)
    a = max(0, b - room)
    return ("…" if a > 0 else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def _col_letter(col: int) -> str:
    out = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


#: How a stretch of text is placed: ``lines`` (a line number), ``lines_cols``
#: (line and column, a CSV), ``cols`` (a Word table row: its column),
#: ``cells`` (a sheet row: the cell), ``plain`` (the stretch's own place).
_MODES = frozenset({"lines", "lines_cols", "cols", "cells", "plain"})


def _marks(segments: Iterable[tuple], *, limit: int = MAX_MARKS_PER_READ,
           visible: "Any | None" = None) -> "list[Mark]":
    """Marks for ``segments``: ``(base locator, absolute start, text, mode,
    context override)``. ``visible(start, end)`` (optional) says whether a
    line at those absolute offsets was shown."""
    out: list[Mark] = []
    for seg in segments:
        base, at, text, mode = seg[0], seg[1], seg[2], seg[3]
        over = seg[4] if len(seg) > 4 else ""
        if mode not in _MODES or not text:
            continue
        pos = at
        tabs_before = 0
        for n, line in enumerate(text.split("\n"), start=1):
            line_at = pos
            pos += len(line) + 1
            if not line.strip():
                tabs_before += line.count("\t")
                continue
            if visible is not None and at >= 0 and not visible(line_at, line_at + len(line)):
                tabs_before += line.count("\t")
                continue
            masked = _mask(line)
            for fig in figures(masked):
                loc = dict(base)
                if mode in ("lines", "lines_cols"):
                    loc["line"] = n
                if mode == "lines_cols" or mode == "cols":
                    loc["col"] = masked.count("\t", 0, fig.start) + 1
                elif mode == "cells":
                    col = tabs_before + masked.count("\t", 0, fig.start) + 1
                    row = loc.pop("row", 0)
                    loc["cell"] = f"{_col_letter(col)}{row}"
                context = _mask(over) if over else _clip(masked, fig.start, fig.end)
                out.append(Mark(loc, fig.raw, fig.key, context[: CONTEXT_CHARS + 2]))
                if len(out) >= limit:
                    return out
            tabs_before += line.count("\t")
    return out


def marks_from_pages(pages: "Iterable[tuple[dict, str]]", kind: str, *,
                     limit: int = MAX_MARKS_PER_READ) -> "list[Mark]":
    """Every figure in already-extracted text, by code. ``pages`` is
    ``(place, text)`` per stretch: ``({"page": 7}, page_text)`` for a PDF,
    ``({}, whole)`` for a text file or CSV, ``({"paragraph": 4}, text)`` or
    ``({"table": 1, "row": 3}, row_text)`` for Word, ``({"sheet": "Summary",
    "row": 7}, row_text)`` for a sheet row (tabs between the cells)."""
    mode = {"pdf": "lines", "text": "lines", "pptx": "lines", "csv": "lines_cols",
            "sheet": "cells"}.get(kind)
    segs = []
    for base, text in pages:
        m = mode
        if kind == "docx":
            m = "cols" if "table" in base else "plain"
        segs.append((dict(base), -1, str(text or ""), m or "plain"))
    return _marks(segs, limit=limit)


# --------------------------------------------------------------------------- #
# the map a reader returns, and the marks it gives
# --------------------------------------------------------------------------- #
def _shift(rows: list, start: int, end: int, *, spans: int = 1) -> list:
    """Layout rows (their first ``spans`` items are offsets into the reader's
    text) moved to the answer's offsets and cut to what was shown."""
    out = []
    for row in rows:
        moved = [int(v) + start for v in row[:spans]] + list(row[spans:])
        if moved[0] >= end:
            break
        out.append(moved)
    return out


def source_map_for(path: "str | Path", raw: str, text: str, layout: "dict | None", *,
                   note: str = "", start: int = 0, shown: "int | None" = None) -> "dict | None":
    """The ``source_map`` of a read: ``raw`` is what the reader returned,
    ``text`` what the model was given after OCR (``start`` characters into
    the answer, ``shown`` of them). None when the place of a figure could
    not be told (an image, a transcript merged into the native pages, a
    text the safety scan changed)."""
    layout = layout or {}
    p = Path(str(path))
    suffix = p.suffix.lower()
    end = start + (len(text) if shown is None else max(0, int(shown)))
    base: dict[str, Any] = {"path": str(p), "start": int(start), "end": int(end)}
    changed = text != raw
    kind = layout.get("kind") or ""
    if kind == "pdf" or (suffix == ".pdf" and not kind):
        if not changed and layout.get("pages"):
            pages = [[int(n), int(off) + start] for n, off in layout["pages"] if int(off) + start < end]
            return {**base, "kind": "pdf", "pages": pages} if pages else None
        if PAGE_LABEL_RX.search(text or "") and "merged into" not in (note or ""):
            return {**base, "kind": "pdf", "labels": True}
        return None
    if changed:
        return None
    if kind == "docx" and layout.get("blocks"):
        blocks = _shift(layout["blocks"], start, end, spans=2)
        return {**base, "kind": "docx", "blocks": blocks} if blocks else None
    if kind == "sheet" and layout.get("rows"):
        rows = _shift(layout["rows"], start, end, spans=2)
        return {**base, "kind": "sheet", "rows": rows} if rows else None
    if suffix in TABLE_TEXT:
        return {**base, "kind": "csv"}
    if suffix == ".pptx":
        return {**base, "kind": "pptx"}
    if suffix in TEXT_KINDS:
        return {**base, "kind": "text"}
    return None


def cell_entry(sheet: str, row: int, col: int, value: Any, *, header: str = "",
               label: str = "") -> "list | None":
    """One sheet cell for a ``cells`` map: ``[sheet, cell, text, context]``,
    or None when it holds no figure."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = repr(value) if isinstance(value, float) else str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    if not figures(text):
        return None
    coord = f"{_col_letter(int(col))}{int(row)}"
    lead = " · ".join(s for s in (str(label or "").strip(), str(header or "").strip()) if s)
    context = f"{sheet}!{coord}" + (f" · {lead}" if lead else "") + f": {text}"
    return [str(sheet), coord, text, context[:CONTEXT_CHARS]]


def cells_map(path: "str | Path", entries: "Iterable[list | None]") -> "dict | None":
    cells = [e for e in entries if e][:MAX_MARKS_PER_READ]
    if not cells:
        return None
    return {"path": str(path), "kind": "cells", "cells": cells}


def grid_entries(sheet: str, rows: "list[list[Any]]", *, first_row: int = 1,
                 first_col: int = 1) -> "list[list]":
    """Cells of a block read from a sheet (``excel_read``), with the column's
    heading (row 1, when it was read) and the row's first words as context."""
    out: list[list] = []
    headers = rows[0] if rows and first_row == 1 else []
    for i, row in enumerate(rows):
        label = next((v for v in row if isinstance(v, str) and v.strip() and not figures(v)), "")
        for j, value in enumerate(row):
            header = headers[j] if i > 0 and j < len(headers) and isinstance(headers[j], str) else ""
            entry = cell_entry(sheet, first_row + i, first_col + j, value,
                               header=header, label=label if label is not value else "")
            if entry:
                out.append(entry)
                if len(out) >= MAX_MARKS_PER_READ:
                    return out
    return out


def _segments(output: str, smap: dict) -> list:
    start = max(0, int(smap.get("start") or 0))
    end = min(len(output), int(smap.get("end") if smap.get("end") is not None else len(output)))
    kind = smap.get("kind")
    segs: list = []
    if kind == "pdf":
        if smap.get("labels"):
            body = output[start:end]
            labels = list(PAGE_LABEL_RX.finditer(body))
            for i, m in enumerate(labels):
                stop = labels[i + 1].start() if i + 1 < len(labels) else len(body)
                segs.append(({"page": int(m.group(1))}, start + m.end() + 1,
                             body[m.end() + 1: stop], "lines"))
        else:
            pages = smap.get("pages") or []
            for i, (n, off) in enumerate(pages):
                s = max(int(off), start)
                e = min(int(pages[i + 1][1]) - 1 if i + 1 < len(pages) else end, end)
                if s < e:
                    segs.append(({"page": int(n)}, s, output[s:e], "lines"))
    elif kind in ("text", "pptx"):
        segs.append(({}, start, output[start:end], "lines"))
    elif kind == "csv":
        segs.append(({}, start, output[start:end], "lines_cols"))
    elif kind == "docx":
        for blk in smap.get("blocks") or []:
            s, e = int(blk[0]), min(int(blk[1]), end)
            if s >= e:
                continue
            if blk[2] == "t":
                segs.append(({"table": int(blk[3]), "row": int(blk[4])}, s, output[s:e], "cols"))
            else:
                segs.append(({"paragraph": int(blk[3])}, s, output[s:e], "plain"))
    elif kind == "sheet":
        for r in smap.get("rows") or []:
            s, e = int(r[0]), min(int(r[1]), end)
            if s < e:
                segs.append(({"sheet": str(r[2]), "row": int(r[3])}, s, output[s:e], "cells"))
    elif kind == "cells":
        for c in smap.get("cells") or []:
            segs.append(({"sheet": str(c[0]), "cell": str(c[1])}, -1, str(c[2]), "plain",
                         str(c[3]) if len(c) > 3 else ""))
    return segs


def marks_for_output(output: str, smap: "dict | None", *,
                     spans: "list[tuple[int, int]] | None" = None) -> "list[Mark]":
    """The marks of one read's answer and its map. ``spans`` (optional): only
    lines inside these stretches of ``output`` were shown (an attachment's
    retrieved excerpts)."""
    if not isinstance(smap, dict) or not output:
        return []
    visible = None
    if spans is not None:
        cuts = [(int(a), int(b)) for a, b in spans]

        def visible(a: int, b: int) -> bool:
            return any(a < e and b > s for s, e in cuts)

    try:
        return _marks(_segments(output, smap), visible=visible)
    except Exception:  # noqa: BLE001 — a mark is never worth a failed read
        log.warning("could not read the figures of a read", exc_info=True)
        return []


def _store_kind(smap: dict) -> str:
    kind = str(smap.get("kind") or "")
    return "sheet" if kind == "cells" else kind


def file_hash(path: "str | Path") -> str:
    """The file's sha256, or "" when it is missing, unreadable or too big."""
    try:
        p = Path(str(path))
        if p.stat().st_size > HASH_MAX_BYTES:
            return ""
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# one chat turn's record
# --------------------------------------------------------------------------- #
#: The turns running now, by turn id, so a new chat's first turn that the
#: page links to its chat while it runs writes its later rows under that chat.
_LIVE: "weakref.WeakValueDictionary[str, SourceTurn]" = weakref.WeakValueDictionary()
_LIVE_LOCK = threading.Lock()


@dataclass
class SourceTurn:
    """What one chat turn read: its marks (``id``, ``key``, ``path``,
    ``kind``, ``locator``) and how many files it read. Both chat lanes make
    one per turn and hand it to the tool context and the attachment step;
    the registry adds to it from its worker threads."""

    thread_id: str = ""
    turn_id: str = ""
    engine: Any = None
    marks: list = field(default_factory=list)
    reads: int = 0
    #: The page named the turn's chat while it ran (``link_live_turn``).
    linked_late: bool = False
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.thread_id = str(self.thread_id or "")
        self.turn_id = str(self.turn_id or "")
        if self.turn_id:
            with _LIVE_LOCK:
                _LIVE[self.turn_id] = self

    def took(self, rows: "list[dict]") -> None:
        with self._lock:
            self.reads += 1
            room = MAX_MARKS_PER_TURN - len(self.marks)
            if room > 0:
                self.marks.extend(rows[:room])

    def chat(self) -> str:
        with self._lock:
            return self.thread_id

    def snapshot(self) -> "tuple[list[dict], int]":
        with self._lock:
            return list(self.marks), self.reads


def link_live_turn(turn_id: str, thread_id: str) -> bool:
    """The page named a running turn's chat: its rows from now on are that
    chat's (the rows already written are homed by ``source_store.home_turn``,
    and again when the reply settles)."""
    with _LIVE_LOCK:
        turn = _LIVE.get(str(turn_id or ""))
    if turn is None or not thread_id:
        return False
    with turn._lock:
        if not turn.thread_id:
            turn.thread_id = str(thread_id)
            turn.linked_late = True
    return True


def _write(engine: Any, turn: SourceTurn, marks: "list[Mark]", smap: dict, invocation_id: str) -> int:
    from .source_store import write_marks

    path = str(smap.get("path") or "")
    rows = write_marks(
        engine, marks, path=path, kind=_store_kind(smap),
        thread_id=turn.chat(), turn_id=turn.turn_id, invocation_id=invocation_id,
        file_sha256=file_hash(path) if marks else "",
    ) if marks else []
    turn.took(rows)
    return len(rows)


def record_marks(ctx: Any, output: str, smap: "dict | None", invocation_id: str = "") -> int:
    """After a read's ledger row (``tools/registry._record``, on its worker
    thread): its marks, written and added to the turn. Only for a chat turn
    (``ctx.source_turn``); never from a model's argument (``smap`` is the
    reader's own, its ``path`` the file the reader opened)."""
    turn = getattr(ctx, "source_turn", None)
    if not isinstance(turn, SourceTurn) or not isinstance(smap, dict) or not smap.get("path"):
        return 0
    marks = marks_for_output(output or "", smap)
    return _write(ctx.engine, turn, marks, smap, invocation_id)


def record_attachment(engine: Any, turn: "SourceTurn | None", text: str, smap: "dict | None", *,
                      spans: "list[tuple[int, int]] | None" = None) -> int:
    """An attached file's marks (both chat lanes' attachment step): the text
    the model was given (``spans``: only the excerpts retrieved for it)."""
    if not isinstance(turn, SourceTurn) or not isinstance(smap, dict) or not text:
        return 0
    marks = marks_for_output(text, smap, spans=spans)
    return _write(engine, turn, marks, smap, "")


# --------------------------------------------------------------------------- #
# the reply
# --------------------------------------------------------------------------- #
_FENCE_RX = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
#: What a figure inside is never linked in: inline code, a link or picture
#: (its words and its address), an autolink, a bare web address, an HTML tag,
#: a link definition.
_PROTECTED_RX = re.compile(
    r"(`+)[^`\n]*?\1"
    r"|!?\[[^\]\n]*\]\([^)\n]*\)"
    r"|<[a-zA-Z][^<>\n]*>|</[a-zA-Z][^<>\n]*>"
    r"|\b(?:https?|ftp|file)://[^\s)\]>]+"
    r"|\bwww\.[^\s)\]>]+"
    r"|^\s{0,3}\[[^\]\n]+\]:\s*\S.*$"
)
#: A ``#source/`` link a model wrote itself: its words stay, the link goes.
_MODEL_LINK_RX = re.compile(r"\[([^\]\n]*)\]\(\s*" + re.escape(SOURCE_LINK_PREFIX) + r"[^)\n]*\)")


def strip_model_links(text: str) -> str:
    """``[x](#source/…)`` → ``x``: only Iron Jarvis's own record makes one."""
    if SOURCE_LINK_PREFIX not in (text or ""):
        return text
    return _MODEL_LINK_RX.sub(lambda m: m.group(1), text)


#: A reply's words without its figure links (the phone, a copy).
without_figure_links = strip_model_links


def _places(marks: "list[dict]") -> "dict[str, list[str]]":
    """key → mark ids, one per place (path + locator), in reading order."""
    by_key: dict[str, list[str]] = {}
    seen: set[tuple] = set()
    for m in marks:
        key = str(m.get("key") or "")
        mid = str(m.get("id") or "")
        if not key or not mid:
            continue
        place = (key, str(m.get("path") or ""), json.dumps(m.get("locator") or {}, sort_keys=True))
        if place in seen:
            continue
        seen.add(place)
        ids = by_key.setdefault(key, [])
        if len(ids) < MAX_IDS_PER_LINK:
            ids.append(mid)
    return by_key


def _link_line(line: str, by_key: "dict[str, list[str]]", seen: "set[str] | None") -> str:
    # Protected stretches are blanked (same length) for the search, so every
    # position still points into the real line.
    scan = _PROTECTED_RX.sub(lambda m: "\x00" * (m.end() - m.start()), line)
    figs = figures(scan)
    if not figs:
        return line
    out: list[str] = []
    last = 0
    for fig in figs:
        if "\x00" in scan[fig.start:fig.end]:
            continue
        ids = by_key.get(fig.key)
        if ids:
            target = ",".join(ids)
        elif seen is not None and fig.key in seen:
            # In something the model was given this turn (a page, an e-mail,
            # memory, the user's own words) but not at a recorded place:
            # left as it is, never called "worked out".
            continue
        else:
            target = NO_RECORD
        out.append(line[last:fig.start])
        out.append(f"[{line[fig.start:fig.end]}]({SOURCE_LINK_PREFIX}{target})")
        last = fig.end
    out.append(line[last:])
    return "".join(out)


def given_figures(*texts: Any) -> "set[str]":
    """Every number (as compared) in what the model was given: the system
    prompt and every message's words (``LLMMessage`` or dict)."""
    from ..core.numbers import figures_in

    out: set[str] = set()

    def take(value: Any) -> None:
        if isinstance(value, str):
            if value:
                out.update(figures_in(value))
        elif isinstance(value, dict):
            take(value.get("content"))
            take(value.get("text"))
        elif isinstance(value, (list, tuple)):
            for item in value:
                take(item)
        elif value is not None and hasattr(value, "content"):
            take(getattr(value, "content", ""))

    for t in texts:
        take(t)
    return out


def link_reply(reply: str, marks: "list[dict]", *, read_any: bool = True,
               seen: "set[str] | None" = None) -> str:
    """The reply with its figures linked (see the module docstring). A
    model-written ``#source/`` link always loses its link; figures are
    linked only when ``read_any`` (the turn read at least one file). With
    ``seen`` (the numbers of everything the model was given this turn), a
    figure with no mark that IS in it is left plain: only a figure found in
    nothing the model was given is called one with no record."""
    text = strip_model_links(str(reply or ""))
    if not read_any or not text:
        return text
    by_key = _places(marks)
    lines = text.split("\n")
    fence = ""
    for i, line in enumerate(lines):
        m = _FENCE_RX.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
            continue
        if m:
            fence = m.group(1)
            continue
        if line.startswith("    ") or line.startswith("\t"):
            # An indented code block (or a deep list item): left alone.
            continue
        lines[i] = _link_line(line, by_key, seen)
    return "\n".join(lines)


def _not_the_reply(given: Any, head: str) -> list:
    """``given`` without the reply itself (a lane that kept the final words
    as an assistant message must not count them as something given)."""
    want = head.strip()
    out = []
    for item in (given if isinstance(given, (list, tuple)) else [given]):
        if isinstance(item, (list, tuple)):
            out.append([
                m for m in item
                if not (getattr(m, "role", None) == "assistant"
                        and str(getattr(m, "content", "") or "").strip() == want)
            ])
        else:
            out.append(item)
    return out


def _link_given(head: str, marks: "list[dict]", read_any: bool, given: Any) -> str:
    seen = given_figures(*_not_the_reply(given, head)) if (read_any and given is not None) else None
    return link_reply(head, marks, read_any=read_any, seen=seen)


async def link_turn_reply(head: str, turn: "SourceTurn | None", *, given: Any = None) -> str:
    """Both chat lanes: the model's own words with their figures linked
    (``given``: the system prompt and the messages the model was sent, read
    on a worker thread). Off the event loop; the text unchanged when there
    is nothing to do."""
    if isinstance(turn, SourceTurn) and turn.linked_late and turn.engine is not None:
        # Rows a running turn wrote before the page named its chat.
        from .source_store import home_turn

        try:
            await asyncio.to_thread(home_turn, turn.engine, turn.turn_id, turn.chat())
        except Exception:  # noqa: BLE001
            log.warning("could not home the figures of turn %s", turn.turn_id, exc_info=True)
    if not head:
        return head
    marks, reads = turn.snapshot() if isinstance(turn, SourceTurn) else ([], 0)
    if not reads and SOURCE_LINK_PREFIX not in head:
        return head
    try:
        return await asyncio.to_thread(_link_given, head, marks, reads > 0, given)
    except Exception:  # noqa: BLE001 — a reply is never lost to its links
        log.warning("could not link the figures of a reply", exc_info=True)
        return head


def with_linked_head(reply: str, head: str, linked: str) -> str:
    """``reply`` (the model's words, then Iron Jarvis's notes) with the
    model's words swapped for their linked form. Unchanged when the words
    are no longer at the start of the reply."""
    if not head or linked == head:
        return reply
    i = reply.find(head)
    if i < 0 or reply[:i].strip():
        return reply
    return reply[:i] + linked + reply[i + len(head):]


def mark_key(text: str) -> str:
    """How a figure written as ``text`` is compared (for the routes and the
    tests)."""
    found = figures(text)
    return found[0].key if found else number_key(text)


__all__ = [
    "CONTEXT_CHARS", "MAX_IDS_PER_LINK", "MAX_MARKS_PER_READ", "MAX_MARKS_PER_TURN", "Mark",
    "NO_RECORD", "NO_RECORD_WORDS", "PAGE_LABEL_RX", "SOURCE_LINK_PREFIX", "SourceTurn",
    "cell_entry", "cells_map", "file_hash", "given_figures", "grid_entries", "link_live_turn",
    "link_reply", "link_turn_reply", "mark_key", "marks_for_output", "marks_from_pages",
    "record_attachment", "record_marks", "source_map_for", "strip_model_links",
    "with_linked_head", "without_figure_links",
]
