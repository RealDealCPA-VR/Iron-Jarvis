"""THE PROJECT MAP (IJ harness part 3, Phase 3 — ``docs/IJ-HARNESS-PLAN-3.md``,
the user's decision 7).

A Build session's turn starts KNOWING THE SHAPE OF ITS PROJECT: the harness
block carries the folder's tree to :data:`MAP_DEPTH` levels and, beside each
source file, its top-level classes and functions. The Phase 1 measurement
(Jarvis on Claude, 126 runs) spent 0.38 calls a run on ``glob`` /
``list_files`` and 0.40 on shell listings (``ls``, ``dir``, ``find``) before
it knew where anything was; the map answers those questions before the first
call.

THE RULES IT KEEPS:

* NEVER RUNS PROJECT CODE. Python names come from ``ast.parse`` (a parse,
  never an import or an exec); JavaScript / TypeScript names from a BOUNDED,
  honest text scan of top-level lines (:func:`script_names` — the map says
  so: a declaration inside a comment can show, a name built at run time
  cannot). Other files are listed by name only.
* THE SAME WALK AS ``list_files``: the heavy and hidden folders it prunes
  (``tools/builtins._SKIP_DIRS`` and every ``.``-folder) and its entry cap
  (``_MAX_WALK_ENTRIES``), with a shorter deadline (:data:`WALK_DEADLINE_S` —
  the map is built at the start of EVERY turn) — and, like ``list_files``,
  a walk that stopped SAYS where it stopped.
* A FIXED SHARE OF THE WINDOW (:func:`share_chars`): at most
  :data:`MAP_SHARE` of the answering model's context window, never more than
  :data:`MAP_MAX_CHARS`. A folder too big for the share is shown SMALLER, in
  steps (fewer files with their names, then fewer levels, then fewer
  entries), and the map's last line names exactly what was left out — never a
  silent cut. Added to the system prompt BEFORE the history planner, so its
  cost is counted like every other part of the prompt.
* CACHED UNTIL THE FILES CHANGE: every turn re-walks the folder (the walk is
  the change detection: each file's size and modification time), and a file
  is parsed again only when those changed (:data:`_NAMES`); an unchanged
  folder reuses its rendered map (:data:`_MAPS`). Built OFF the event loop.
* UNTRUSTED TEXT: file and symbol names are the folder's words, so the map
  goes through ``core.promptguard`` like every folder text.
* NOT IN THE FINGERPRINT: the map is per folder and per turn, like the date
  and the branch line (``mode.HarnessTurn.block``). The bench's identity
  carries this module's SOURCE instead, so a change to how maps are made is a
  new harness configuration.
* ONLY IN A BUILD SESSION'S TURN (``build:<id>`` — the benchmark's turns
  are Build sessions too); never in plain chat, never in an agent run.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The map's share of the answering model's context window.
MAP_SHARE = 0.02
#: Never more than this many characters, whatever the window.
MAP_MAX_CHARS = 8_000
#: Below this many characters a map is not worth its tokens: one line says so.
MAP_MIN_CHARS = 400
#: The planner's own estimate (``context/budget``: ~4 characters a token).
CHARS_PER_TOKEN = 4
#: Folder levels shown (1 = only the folder's own entries).
MAP_DEPTH = 3
#: Top-level names shown per file (the rest counted).
MAX_NAMES = 12
MAX_NAME_CHARS = 60
#: A bigger source file is listed by name only (``ast.parse`` holds the GIL).
MAX_SOURCE_BYTES = 256 * 1024
#: The walk's own deadline (``list_files`` allows 10 s for ONE call; the map
#: is built at the start of every turn).
WALK_DEADLINE_S = 3.0
#: Files whose names are read.
PY_SUFFIXES = frozenset({".py", ".pyi"})
SCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
HEADING = "## Project map"
#: How many folders' maps / files' names are remembered.
_MAX_MAPS = 16
_MAX_NAMED_FILES = 20_000


@dataclass
class MapResult:
    """One turn's map. ``text`` is the prompt section ("" = no map)."""

    text: str
    #: files the walk found (at most the walk cap)
    files: int = 0
    #: files the tree shows by name / files shown WITH their top-level names
    shown: int = 0
    named: int = 0
    #: nothing was left out (the whole tree to MAP_DEPTH and every name)
    complete: bool = True
    #: the character share it was given
    limit: int = 0
    #: why the walk stopped early ("" = it saw the whole folder)
    walk_note: str = ""
    #: promptguard placeholders in the text
    blocked: int = 0
    left_out: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    def receipt(self) -> dict[str, Any]:
        """What the turn's route says about its map (``route.harness.map``)."""
        out: dict[str, Any] = {
            "files": self.files, "chars": self.chars, "limit": self.limit,
            "complete": bool(self.complete),
        }
        if self.left_out:
            out["left_out"] = list(self.left_out)
        return out


def share_chars(window_tokens: "int | None") -> int:
    """The characters the map may use for a model with this window (tokens;
    None = unknown, the planner's default window)."""
    from ..context.budget import DEFAULT_WINDOW

    try:
        window = int(window_tokens or DEFAULT_WINDOW)
    except (TypeError, ValueError):
        window = DEFAULT_WINDOW
    return max(0, min(MAP_MAX_CHARS, int(window * MAP_SHARE * CHARS_PER_TOKEN)))


# --------------------------------------------------------------------------- #
# top-level names — never running the project's code
# --------------------------------------------------------------------------- #


def python_names(source: str) -> list[str]:
    """The module's top-level classes (``Name``) and functions (``name()``),
    from ``ast.parse`` — a parse, never an import. [] for a file that does
    not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node.name + "()")
    return out


_IDENT = r"[A-Za-z_$][\w$]{0,80}"
#: A declaration that STARTS a line (top level, by indentation): function,
#: class, and TypeScript's interface / type / enum, optionally exported.
_SCRIPT_DECL = re.compile(
    r"^(?:export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?(?:abstract[ \t]+)?(?:async[ \t]+)?"
    rf"(function\*?|class|interface|type|enum)[ \t]+({_IDENT})",
    re.MULTILINE,
)
#: ``const name = (…) =>`` / ``= function`` / ``= async x =>`` at the start
#: of a line.
_SCRIPT_CONST_FN = re.compile(
    rf"^(?:export[ \t]+)?(?:const|let|var)[ \t]+({_IDENT})[ \t]*(?::[^=\n]{{0,80}})?=[ \t]*(?:async[ \t]+)?"
    rf"(?:function\b|\([^)\n]{{0,200}}\)[ \t]*(?::[^=\n]{{0,80}})?=>|{_IDENT}[ \t]*=>)",
    re.MULTILINE,
)


def script_names(source: str) -> list[str]:
    """Top-level declarations of a JavaScript / TypeScript file, by a
    BOUNDED text scan of lines that start at column 0 — never by running or
    bundling it. Functions as ``name()``. Honest about what it is: it cannot
    see a name built at run time, and a declaration written at the start of a
    line inside a comment would show."""
    found: list[tuple[int, str]] = []
    for m in _SCRIPT_DECL.finditer(source):
        kind, name = m.group(1), m.group(2)
        found.append((m.start(), name + "()" if kind.startswith("function") else name))
    for m in _SCRIPT_CONST_FN.finditer(source):
        found.append((m.start(), m.group(1) + "()"))
    out: list[str] = []
    for _pos, name in sorted(found):
        if name not in out:
            out.append(name)
    return out


def _names_of(path: Path, suffix: str) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if len(raw) > MAX_SOURCE_BYTES:
        return []
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return []
    if suffix in PY_SUFFIXES:
        return python_names(source)
    return script_names(source)


# --------------------------------------------------------------------------- #
# the walk — list_files' pruning and caps
# --------------------------------------------------------------------------- #


@dataclass
class _Entry:
    parts: tuple[str, ...]
    is_dir: bool
    mtime_ns: int = 0
    size: int = 0


def _walk(base: Path) -> tuple[list[_Entry], int, str]:
    """``(entries, files, note)``: every folder and file under ``base`` in a
    stable order (names sorted), pruned like ``list_files`` (heavy folders,
    ``.``-folders, no links followed), stopping at its file cap or deadline
    and saying so. BLOCKING."""
    from ..tools.builtins import _MAX_WALK_ENTRIES, _SKIP_DIRS

    started = time.monotonic()
    entries: list[_Entry] = []
    files = 0
    note = ""
    stack: list[tuple[Path, tuple[str, ...]]] = [(base, ())]
    while stack:
        here, parts = stack.pop()
        try:
            with os.scandir(here) as it:
                children = sorted(it, key=lambda e: (e.name.lower(), e.name))
        except OSError:
            continue
        subdirs: list[tuple[Path, tuple[str, ...]]] = []
        for child in children:
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if child.name in _SKIP_DIRS or child.name.startswith("."):
                    continue
                entries.append(_Entry(parts + (child.name,), True))
                subdirs.append((Path(child.path), parts + (child.name,)))
                continue
            try:
                if not child.is_file(follow_symlinks=False):
                    continue
                st = child.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(_Entry(parts + (child.name,), False, int(st.st_mtime_ns), int(st.st_size)))
            files += 1
            if files >= _MAX_WALK_ENTRIES:
                return entries, files, f"the walk stopped at {_MAX_WALK_ENTRIES:,} files"
        if time.monotonic() - started >= WALK_DEADLINE_S:
            note = f"the walk stopped after {WALK_DEADLINE_S:g} s"
            break
        # depth-first in name order: push in reverse so the first is next
        stack.extend(reversed(subdirs))
    return entries, files, note


# --------------------------------------------------------------------------- #
# rendering within the share
# --------------------------------------------------------------------------- #


class _Node:
    __slots__ = ("dirs", "files", "total")

    def __init__(self) -> None:
        self.dirs: dict[str, _Node] = {}
        self.files: list[tuple[str, tuple[str, ...]]] = []
        self.total = 0  # files at or below this folder


def _tree(entries: list[_Entry]) -> _Node:
    root = _Node()
    for e in entries:
        node = root
        for name in e.parts[:-1]:
            node = node.dirs.setdefault(name, _Node())
        if e.is_dir:
            node.dirs.setdefault(e.parts[-1], _Node())
        else:
            node.files.append((e.parts[-1], e.parts))
    def count(n: _Node) -> int:
        n.total = len(n.files) + sum(count(d) for d in n.dirs.values())
        return n.total
    count(root)
    return root


def _names_line(names: list[str]) -> str:
    shown = [n if len(n) <= MAX_NAME_CHARS else n[: MAX_NAME_CHARS - 1] + "…" for n in names[:MAX_NAMES]]
    more = f", +{len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else ""
    return ", ".join(shown) + more


def _render_lines(
    root: _Node, names: dict[tuple[str, ...], list[str]], *, depth: int, name_budget: int,
) -> tuple[list[str], list[bool], list[bool], bool]:
    """``(lines, is a file, carries names, deeper levels collapsed)`` — one
    flag per line. Files get their names in render order until
    ``name_budget`` files have them."""
    lines: list[str] = []
    is_file: list[bool] = []
    has_names: list[bool] = []
    named = 0
    collapsed = False

    def walk(node: _Node, level: int) -> None:
        nonlocal named, collapsed
        pad = "  " * level
        for fname, parts in node.files:
            got = names.get(parts) or []
            if got and named < name_budget:
                named += 1
                lines.append(f"{pad}{fname} — {_names_line(got)}")
                has_names.append(True)
            else:
                lines.append(f"{pad}{fname}")
                has_names.append(False)
            is_file.append(True)
        for dname in sorted(node.dirs, key=lambda n: (n.lower(), n)):
            child = node.dirs[dname]
            if level + 1 >= depth:
                if child.total or child.dirs:
                    collapsed = True
                lines.append(f"{pad}{dname}/ ({child.total} file{'' if child.total == 1 else 's'})")
                is_file.append(False)
                has_names.append(False)
                continue
            lines.append(f"{pad}{dname}/")
            is_file.append(False)
            has_names.append(False)
            walk(child, level + 1)

    walk(root, 0)
    return lines, is_file, has_names, collapsed


def _header(folder_name: str, files: int, depth: int, any_names: bool) -> str:
    named = (
        ", each source file followed by its top-level classes and functions() — read "
        "without running any code (Python's parser; a text scan of JavaScript / "
        "TypeScript lines, which can miss a name)"
    ) if any_names else ""
    return (
        f"{HEADING}\n"
        f"The session folder ({folder_name}) to {depth} level{'' if depth == 1 else 's'}{named}. "
        f"Heavy and hidden folders (node_modules, .git, build output) are left out. "
        f"{files:,} file{'' if files == 1 else 's'}. You need not list what it shows."
    )


def _footer(parts: list[str], walk_note: str, limit: int) -> str:
    bits = []
    if walk_note:
        bits.append(f"This folder is big: {walk_note}, so the map is not the whole folder.")
    if parts:
        bits.append(
            f"Left out to keep this map within its share of the window ({limit:,} characters): "
            + "; ".join(parts) + "."
        )
    if bits:
        bits.append("Use your file-listing and search tools for the rest.")
    return " ".join(bits)


def _compose(head: str, lines: list[str], foot: str) -> str:
    body = "\n".join(lines)
    return "\n".join(p for p in (head, body, foot) if p)


def render(
    folder_name: str, entries: list[_Entry], names: dict[tuple[str, ...], list[str]],
    *, files: int, walk_note: str, limit: int, depth: int = MAP_DEPTH,
) -> MapResult:
    """The richest map that fits ``limit`` characters, and what it left out.
    Pure. The steps, each tried only when the one before does not fit:

    1. the tree to ``depth`` levels with every shown source file's names;
    2. the deepest tree that fits (``depth`` down to 1 levels), with names
       for as many of its files as fit (in tree order);
    3. the folder's own entries, as many as fit;
    4. one sentence saying the folder is too big for the share.

    Whatever a step leaves out is named on the map's last line — and in
    :attr:`MapResult.left_out` — never cut silently."""
    if limit < MAP_MIN_CHARS:
        text = (
            f"{HEADING}\nNo map of the session folder ({files:,} files): this model's window "
            "leaves it too little room. Use your file-listing and search tools."
        )
        return MapResult(
            text=text if len(text) <= limit else "", files=files, complete=False, limit=limit,
            walk_note=walk_note, left_out=["the whole map (the window is too small)"],
        )
    root = _tree(entries)
    #: source files with names that a tree to ``d`` levels shows (a file below
    #: the shown levels is inside a "(N files)" count, not a left-out name)
    namable: dict[int, int] = {}

    def namable_at(d: int) -> int:
        if d not in namable:
            lines, is_file, has_names, _c = _render_lines(root, names, depth=d, name_budget=len(entries) + 1)
            namable[d] = sum(has_names)
        return namable[d]

    def attempt(d: int, budget: int, keep: "int | None" = None) -> tuple[str, int, int, list[str]]:
        lines, is_file, has_names, collapsed = _render_lines(root, names, depth=d, name_budget=budget)
        dropped = 0
        if keep is not None and keep < len(lines):
            dropped = len(lines) - keep
            lines, is_file, has_names = lines[:keep], is_file[:keep], has_names[:keep]
        named = sum(has_names)
        parts: list[str] = []
        if named < namable_at(d):
            left = namable_at(d) - named
            parts.append(f"the top-level names of {left} source file{'' if left == 1 else 's'}")
        if d < depth and collapsed:
            parts.append(f"the folders below level {d} (shown as file counts)")
        if dropped:
            parts.append(f"{dropped} of the folder's own {dropped + len(lines)} entries")
        text = _compose(_header(folder_name, files, d, named > 0), lines, _footer(parts, walk_note, limit))
        return text, sum(is_file), named, parts

    best = attempt(depth, namable_at(depth))  # 1.
    if len(best[0]) > limit:
        found = None
        for d in range(depth, 0, -1):
            # 2. the deepest tree that fits, with the most names that fit in it
            lo, hi = 0, namable_at(d)
            while lo <= hi:
                mid = (lo + hi) // 2
                cand = attempt(d, mid)
                if len(cand[0]) <= limit:
                    found, lo = cand, mid + 1
                else:
                    hi = mid - 1
            if found is not None:
                break
        if found is None:  # 3. the folder's own entries, as many as fit
            total = len(_render_lines(root, names, depth=1, name_budget=0)[0])
            lo, hi = 1, total
            while lo <= hi:
                mid = (lo + hi) // 2
                cand = attempt(1, 0, keep=mid)
                if len(cand[0]) <= limit:
                    found, lo = cand, mid + 1
                else:
                    hi = mid - 1
        if found is None:  # 4. too big even for that
            text = (
                f"{HEADING}\nThe session folder ({folder_name}) is too big to map in this "
                f"map's share of the window ({limit:,} characters): {files:,} files. Use your "
                "file-listing and search tools."
            )
            found = (text if len(text) <= limit else "", 0, 0,
                     ["the whole map (the folder is too big for its share)"])
        best = found
    text, shown, named, parts = best
    return MapResult(
        text=text, files=files, shown=shown, named=named, complete=not parts and not walk_note,
        limit=limit, walk_note=walk_note, left_out=list(parts) + ([walk_note] if walk_note else []),
    )


# --------------------------------------------------------------------------- #
# the cache, and the one entry point
# --------------------------------------------------------------------------- #

_LOCK = threading.Lock()
#: (folder, limit, depth) -> (signature, MapResult)
_MAPS: "OrderedDict[tuple[str, int, int], tuple[str, MapResult]]" = OrderedDict()
#: absolute file path -> (mtime_ns, size, names)
_NAMES: "OrderedDict[str, tuple[int, int, list[str]]]" = OrderedDict()


def _signature(entries: list[_Entry], note: str) -> str:
    h = hashlib.sha1()
    for e in entries:
        h.update(("\0".join(e.parts) + f"\0{int(e.is_dir)}\0{e.mtime_ns}\0{e.size}\n").encode("utf-8", "replace"))
    h.update(note.encode("utf-8"))
    return h.hexdigest()


def _names_for(base: Path, entries: list[_Entry]) -> dict[tuple[str, ...], list[str]]:
    out: dict[tuple[str, ...], list[str]] = {}
    for e in entries:
        if e.is_dir:
            continue
        suffix = os.path.splitext(e.parts[-1])[1].lower()
        if suffix not in PY_SUFFIXES and suffix not in SCRIPT_SUFFIXES:
            continue
        if e.size > MAX_SOURCE_BYTES:
            continue
        path = base.joinpath(*e.parts)
        key = os.path.normcase(str(path))
        with _LOCK:
            hit = _NAMES.get(key)
            if hit is not None and hit[0] == e.mtime_ns and hit[1] == e.size:
                _NAMES.move_to_end(key)
                names = hit[2]
            else:
                names = None
        if names is None:
            names = _names_of(path, suffix)
            with _LOCK:
                BUILDS["parsed"] += 1
                _NAMES[key] = (e.mtime_ns, e.size, names)
                _NAMES.move_to_end(key)
                while len(_NAMES) > _MAX_NAMED_FILES:
                    _NAMES.popitem(last=False)
        if names:
            out[e.parts] = names
    return out


#: How many times a map was built rather than reused (the cache pin reads it).
BUILDS = {"maps": 0, "parsed": 0}


def build(folder: Any, *, limit: int, depth: int = MAP_DEPTH) -> "MapResult | None":
    """The map of ``folder`` within ``limit`` characters, or None when the
    folder cannot be used. BLOCKING (a walk, and a parse of changed source
    files): async callers use :func:`for_turn`. Never raises."""
    try:
        base = Path(str(folder)).resolve()
        if not base.is_dir():
            return None
    except (OSError, ValueError):
        return None
    entries, files, note = _walk(base)
    sig = _signature(entries, note)
    key = (os.path.normcase(str(base)), int(limit), int(depth))
    with _LOCK:
        hit = _MAPS.get(key)
        if hit is not None and hit[0] == sig:
            _MAPS.move_to_end(key)
            cached = hit[1]
            return MapResult(**{**cached.__dict__, "left_out": list(cached.left_out)})
    names = _names_for(base, entries)
    result = render(base.name or str(base), entries, names, files=files, walk_note=note,
                    limit=limit, depth=depth)
    with _LOCK:
        BUILDS["maps"] += 1
        _MAPS[key] = (sig, result)
        _MAPS.move_to_end(key)
        while len(_MAPS) > _MAX_MAPS:
            _MAPS.popitem(last=False)
    return MapResult(**{**result.__dict__, "left_out": list(result.left_out)})


def forget() -> None:
    """Drop every remembered map and name (tests)."""
    with _LOCK:
        _MAPS.clear()
        _NAMES.clear()


async def for_turn(
    folder: Any, *, window: "int | None", event_bus: Any = None, session_id: "str | None" = "chat",
) -> "MapResult | None":
    """THE ONE ENTRY POINT: the map for a turn whose model has ``window``
    tokens — built OFF THE LOOP, then scanned by promptguard (the folder's
    names are untrusted text). None when there is no usable folder; never
    raises."""
    limit = share_chars(window)
    try:
        result = await asyncio.to_thread(build, folder, limit=limit)
    except Exception:  # noqa: BLE001 — a map must never break a turn
        log.warning("project map could not be built", exc_info=True)
        return None
    if result is None or not result.text:
        return result
    try:
        from ..core.promptguard import publish_blocked, scan_context

        scanned = scan_context(result.text, source="project map", cap=None)
        if scanned.blocked:
            publish_blocked(event_bus, session_id, scanned)
            text = scanned.text
            if len(text) > limit:
                # A placeholder is longer than what it replaced: keep the share.
                text = text[: max(0, limit - 40)].rsplit("\n", 1)[0] + "\n… (cut to fit its share)"
            result.text = text
            result.blocked = len(scanned.blocked)
            result.complete = False
    except Exception:  # noqa: BLE001 — a map we could not scan is not shown
        log.warning("project map could not be scanned; not shown", exc_info=True)
        return None
    return result


__all__ = [
    "BUILDS", "HEADING", "MAP_DEPTH", "MAP_MAX_CHARS", "MAP_MIN_CHARS", "MAP_SHARE", "MapResult",
    "build", "for_turn", "forget", "python_names", "render", "script_names", "share_chars",
]
