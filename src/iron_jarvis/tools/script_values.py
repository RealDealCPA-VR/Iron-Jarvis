"""Values a tool script KEPT, so a file tool can take them without the model
retyping them (v1.473.0).

THE PROBLEM. A script reads forty statements and works out 400 rows. To put
them in a workbook the model used to print the rows and type every one again
into ``write_document``'s arguments: slow (thousands of output tokens) and the
one step where a figure can change on its way to the file.

THE PATH NOW. In the script: ``tools.keep("totals", rows)``. The value crosses
the worker's private pipe once, as JSON, and is held HERE, in the app. Then
the model calls the file tool directly and names the value instead of typing
it::

    write_document(path="summary.xlsx", _from_script={"content": "totals"})

``ToolRegistry.invoke`` fills the argument from this shelf just before the
tool runs (:func:`fill_from_script`). Every gate the call has, it still has:
the same tier, the same card, the same Undo. What is written is exactly what
the script computed.

WHAT IT IS NOT. Not a way for a script to act: the model still makes the call,
and only arguments that carry CONTENT may be filled (:data:`FROM_SCRIPT_ARGS`)
— never a path, a name or a switch, so text a script read from a file cannot
choose where something is written.

BOUNDED. Values are held by the script worker's key (one chat, one run), a few
per key, a total cap across the app, and they age out. A value from a Local
only chat stays under that chat's key and is only ever handed to that chat's
own calls.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Any

#: Which arguments of which tools may be filled from a kept value: the ones
#: that carry what is WRITTEN. Anything else is refused in words.
FROM_SCRIPT_ARGS: dict[str, frozenset[str]] = {
    "write_document": frozenset({"content"}),
    "write_file": frozenset({"content"}),
    "excel_edit": frozenset({"edits", "add_sheets"}),
    "excel_apply_spec": frozenset({"spec"}),
    "docx_edit": frozenset({"operations"}),
    "pdf_form_fill": frozenset({"values"}),
    "multi_edit": frozenset({"edits"}),
    "apply_changes": frozenset({"changes"}),
}

#: The argument a direct call names kept values with.
ARG = "_from_script"

MAX_VALUES_PER_KEY = 16
MAX_VALUE_CHARS = 8_000_000
MAX_TOTAL_CHARS = 48_000_000
MAX_KEYS = 48
TTL_S = 6 * 3600.0

_SUMMARY_CHARS = 200
#: A typed value longer than this is content, never a pointer at a kept one.
_POINTER_CHARS = 200


def _head(item: Any, depth: int = 0) -> str:
    """The start of *item* as JSON, without writing all of it out: a first
    row that is one 8 MB cell must not cost 8 MB of text to show 80
    characters."""
    if isinstance(item, str):
        return json.dumps(item[:400], ensure_ascii=False)
    if isinstance(item, (list, tuple, dict)) and depth >= 4:
        return "…"
    if isinstance(item, (list, tuple)):
        return (
            "[" + ", ".join(_head(x, depth + 1) for x in list(item)[:12])
            + ("…" if len(item) > 12 else "") + "]"
        )
    if isinstance(item, dict):
        pairs = list(item.items())[:8]
        return (
            "{" + ", ".join(
                f"{json.dumps(str(k)[:80], ensure_ascii=False)}: {_head(v, depth + 1)}" for k, v in pairs
            )
            + ("…" if len(item) > 8 else "") + "}"
        )
    return json.dumps(item, ensure_ascii=False, default=str)[:400]


def _unsplit(cells: Any) -> str:
    """"2 commas" when *cells* are text lines that each hold the same number
    of one delimiter (``documents.lint.unsplit_lines``), else ""."""
    try:
        from ..documents.lint import unsplit_lines

        found = unsplit_lines(cells)
    except Exception:  # noqa: BLE001 — wording only
        return ""
    if not found:
        return ""
    word, n = found
    return f"{n} {word}{'s' if n != 1 else ''}"


def describe(value: Any) -> str:
    """One short line saying what a kept value is: its shape and its first
    item, cut. For the script's footer and a card's preview."""
    def cut(text: str, n: int = 80) -> str:
        # Bounded first (v1.473.0 review): one kept string of 8 MB was
        # split and masked whole to show 80 characters of it (0.8 s).
        text = " ".join(str(text)[: max(2_000, n * 8)].split())
        try:
            # A summary is shown on cards and kept on ledger rows: a key or a
            # token in the first row must not ride along.
            from ..detections.redact import mask

            text = mask(text)
        except Exception:  # noqa: BLE001 — wording only
            pass
        return text if len(text) <= n else text[: n - 1] + "…"

    if isinstance(value, list):
        if not value:
            return "an empty list"
        first = value[0]
        if isinstance(first, (list, tuple)):
            widest = max((len(r) for r in value if isinstance(r, (list, tuple))), default=0)
            if widest == 1:
                # Lines that were never split into columns: said HERE, where
                # the script's author and the card's reader both see it,
                # before any file is written.
                found = _unsplit(r[0] if isinstance(r, (list, tuple)) and r else None for r in value)
                if found:
                    return (
                        f"{len(value):,} rows of ONE cell each, every cell holding {found} "
                        "(lines not split into columns?); first row: " + cut(_head(first))
                    )
            return (
                f"{len(value):,} rows, up to {widest} columns; first row: " + cut(_head(first))
            )
        if isinstance(first, dict):
            return f"{len(value):,} items; first: " + cut(_head(first))
        if isinstance(first, str):
            found = _unsplit(value)
            if found:
                return (
                    f"a list of {len(value):,} text lines, every line holding {found} "
                    "(not split into columns?); first: " + cut(_head(first))
                )
        return f"a list of {len(value):,}; first: " + cut(_head(first))
    if isinstance(value, dict):
        keys = list(value)
        return f"{len(keys):,} named entries: " + cut(", ".join(str(k) for k in keys), 100)
    if isinstance(value, str):
        return f"text, {len(value):,} characters: " + cut(value)
    return cut(json.dumps(value, ensure_ascii=False, default=str))


class _Kept:
    __slots__ = ("text", "summary", "sha", "at", "plan")

    def __init__(self, text: str, summary: str, plan: bool) -> None:
        self.text = text
        self.summary = summary[:_SUMMARY_CHARS]
        self.sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        self.at = time.monotonic()
        self.plan = plan


class Shelf:
    """``worker key -> name -> kept value``. Thread-safe: values arrive on
    the script session's worker thread and are read on the event loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: "OrderedDict[str, OrderedDict[str, _Kept]]" = OrderedDict()
        self._chars = 0
        #: Keys whose NEWEST script kept a value while its turn had no tool
        #: that could take one (:meth:`note_unplaced`).
        self._unplaced: "set[str]" = set()
        #: ``key -> sorted figures`` the computing tools produced there
        #: (:func:`note_figures`).
        self._figures: "OrderedDict[str, list[float]]" = OrderedDict()
        #: ``key -> path -> (figures, when)``: see :meth:`note_unchecked`.
        self._unchecked: "OrderedDict[str, OrderedDict[str, tuple[list[str], float]]]" = OrderedDict()

    # -- writing ---------------------------------------------------------------

    def put(self, key: str, name: str, text: str, *, plan: bool = False) -> str:
        """Hold *text* (JSON) as *name* for *key*. Returns its summary.
        Raises ``ValueError`` with a sentence when it cannot be held."""
        if not key or not name:
            raise ValueError("a kept value needs a name")
        if not isinstance(text, str) or len(text) > MAX_VALUE_CHARS:
            raise ValueError(
                f"that value is too large to keep (the most is {MAX_VALUE_CHARS:,} characters)"
            )
        try:
            value = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"the value is not plain data ({exc})") from None
        kept = _Kept(text, describe(value), plan)
        with self._lock:
            self._purge_locked()
            names = self._keys.get(key)
            if names is None:
                names = self._keys[key] = OrderedDict()
            old = names.pop(name, None)
            if old is not None:
                self._chars -= len(old.text)
            names[name] = kept
            self._chars += len(text)
            self._keys.move_to_end(key)
            while len(names) > MAX_VALUES_PER_KEY:
                _, gone = names.popitem(last=False)
                self._chars -= len(gone.text)
            # The app-wide bounds: the oldest OTHER key goes first.
            while (self._chars > MAX_TOTAL_CHARS or len(self._keys) > MAX_KEYS) and len(self._keys) > 1:
                _, gone_names = self._keys.popitem(last=False)
                self._chars -= sum(len(k.text) for k in gone_names.values())
        return kept.summary

    def _purge_locked(self) -> None:
        now = time.monotonic()
        for key in [k for k, names in self._keys.items()
                    if names and all(now - v.at > TTL_S for v in names.values())]:
            names = self._keys.pop(key)
            self._chars -= sum(len(v.text) for v in names.values())

    # -- reading ---------------------------------------------------------------

    def get(self, key: str, name: str) -> "_Kept | None":
        with self._lock:
            names = self._keys.get(key)
            kept = names.get(name) if names else None
            if kept is not None:
                kept.at = time.monotonic()
            return kept

    def names(self, key: str) -> list[str]:
        with self._lock:
            return list(self._keys.get(key) or ())

    # -- a kept value with nowhere to go ----------------------------------------

    def add_figures(self, key: str, values: "list[float]") -> None:
        """Figures a computing tool produced under *key*, kept sorted. Bounded:
        the new ones always fit, older ones while there is room."""
        with self._lock:
            merged = {abs(float(v)) for v in values[:MAX_FIGURES]}
            for old in self._figures.get(key) or ():
                if len(merged) >= MAX_FIGURES:
                    break
                merged.add(old)
            self._figures[key] = sorted(merged)
            self._figures.move_to_end(key)
            while len(self._figures) > MAX_KEYS:
                self._figures.popitem(last=False)

    def figures(self, key: str) -> "list[float]":
        with self._lock:
            return list(self._figures.get(key) or ())

    def note_unchecked(self, key: str, path: str, figures: "list[str]") -> None:
        """*path* was just written holding *figures* that nobody worked out."""
        if not key or not path:
            return
        with self._lock:
            files = self._unchecked.setdefault(key, OrderedDict())
            files[path] = (list(figures), time.monotonic())
            files.move_to_end(path)
            while len(files) > MAX_UNCHECKED_FILES:
                files.popitem(last=False)
            self._unchecked.move_to_end(key)
            while len(self._unchecked) > MAX_KEYS:
                self._unchecked.popitem(last=False)

    def clear_unchecked(self, key: str, path: str) -> None:
        """*path* was written again with nothing left to check."""
        with self._lock:
            files = self._unchecked.get(key)
            if files and files.pop(path, None) is not None and not files:
                self._unchecked.pop(key, None)

    def peek_unchecked(self, key: str) -> "dict[str, list[str]]":
        """:meth:`take_unchecked` without forgetting (v1.495.0: the check-again
        loop asks while the reply can still fix the file)."""
        with self._lock:
            files = self._unchecked.get(key) or {}
            fresh = time.monotonic() - UNCHECKED_TTL_S
            return {p: list(figs) for p, (figs, at) in files.items() if at >= fresh}

    def take_unchecked(self, key: str) -> "dict[str, list[str]]":
        """The files of *key* that still hold such a figure. Reading forgets
        them: a reply says it once."""
        with self._lock:
            files = self._unchecked.pop(key, None) or {}
            fresh = time.monotonic() - UNCHECKED_TTL_S
            return {p: list(figs) for p, (figs, at) in files.items() if at >= fresh}

    def note_unplaced(self, key: str) -> None:
        """A script kept a value for a file and no tool of its turn can take
        one. The chat lanes read this ONCE (:meth:`take_unplaced`) and bring
        the file tool into the turn (``chat_turn.offer_file_tool``). Set by
        the script TOOL from what it held, never from a script's output."""
        if not key:
            return
        with self._lock:
            if len(self._unplaced) >= MAX_KEYS:
                self._unplaced.clear()
            self._unplaced.add(key)

    def take_unplaced(self, key: str) -> bool:
        """Whether *key*'s newest script kept a value nothing could take.
        Reading it forgets it. A mark whose values are gone says no."""
        with self._lock:
            if key not in self._unplaced:
                return False
            self._unplaced.discard(key)
            return key in self._keys

    def rekey(self, old: str, new: str) -> int:
        """A new chat was saved: what its first message kept (held by the
        TURN's key) is the chat's from now on."""
        if not old or not new or old == new:
            return 0
        with self._lock:
            waiting = self._unchecked.pop(old, None)
            if waiting:
                self._unchecked.setdefault(new, OrderedDict()).update(waiting)
            moved_figures = self._figures.pop(old, None)
            if moved_figures:
                self._figures[new] = sorted({*moved_figures, *(self._figures.get(new) or ())})[:MAX_FIGURES]
            names = self._keys.pop(old, None)
            if old in self._unplaced:
                self._unplaced.discard(old)
                if names:
                    self._unplaced.add(new)
            if not names:
                return 0
            target = self._keys.setdefault(new, OrderedDict())
            moved = 0
            for name, kept in names.items():
                if name in target:
                    self._chars -= len(kept.text)
                    continue
                target[name] = kept
                moved += 1
            return moved

    def drop(self, key: str) -> None:
        with self._lock:
            self._unplaced.discard(key)
            self._figures.pop(key, None)
            self._unchecked.pop(key, None)
            names = self._keys.pop(key, None)
            if names:
                self._chars -= sum(len(v.text) for v in names.values())

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
            self._unplaced.clear()
            self._figures.clear()
            self._unchecked.clear()
            self._chars = 0

    @property
    def chars(self) -> int:
        return self._chars


#: The one shelf of this process.
SHELF = Shelf()


def _key_for(ctx: Any) -> tuple[str, bool]:
    from .tool_script import worker_key

    return worker_key(ctx)


def has_refs(args: Any) -> bool:
    return isinstance(args, dict) and ARG in args


#: What a model types into an argument it believes it has to fill, beside the
#: reference (seen live: 'test', 'Placeholder', 'placeholder', '{{ rows }}').
#: Never content. Kept SHORT on purpose: a word that could be a real one-word
#: file ("merged", a title) is not here and is still refused.
_PLACEHOLDERS = frozenset({
    "test", "placeholder", "todo", "tbd", "none", "null", "n/a",
    "content", "data", "rows", "row", "value", "values", "...", "\u2026",
})

_UNKNOWN = object()


def _first_row(value: Any) -> "list[Any] | None":
    """The heading row of a kept table, in whichever shape it was kept."""
    if isinstance(value, dict):
        for key in ("header", "headers", "columns"):
            if isinstance(value.get(key), list):
                return list(value[key])
        sheets = value.get("sheets")
        if isinstance(sheets, dict) and sheets:
            return _first_row(next(iter(sheets.values())))
        return None
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, (list, tuple)):
            return list(first)
        if isinstance(first, dict):
            return list(first)
    return None


def _repeats_first_row(typed: Any, value: Any) -> bool:
    """Is *typed* nothing but the heading row the kept table already starts
    with (typed as rows, or as the text of a list)? Then it adds nothing."""
    row = _first_row(value)
    if not row:
        return False
    if isinstance(typed, str):
        try:
            typed = ast.literal_eval(typed)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            try:
                typed = json.loads(typed)
            except (ValueError, RecursionError):
                return False
    if isinstance(typed, (list, tuple)) and len(typed) == 1 and isinstance(typed[0], (list, tuple)):
        typed = typed[0]
    if not isinstance(typed, (list, tuple)) or not typed:
        return False
    if len(typed) != len(row) or any(isinstance(c, (list, tuple, dict)) for c in typed):
        return False
    return [str(c).strip().lower() for c in typed] == [str(c).strip().lower() for c in row]


def _left_out(typed: Any, name: str, kept: Any = _UNKNOWN) -> bool:
    """Did the model leave the argument to the reference? Yes when it typed
    nothing, and yes when what it typed is not content: the kept value's
    name or the reference written out, a placeholder, the name inside a
    structure, or the heading row the kept table (*kept*) already starts
    with. All seen with a real model (v1.473.0): of twelve "given twice"
    refusals, ten were one of these, and each refusal cost the reply a step.
    Anything else typed is real content, and giving both is refused: the two
    are never joined."""
    if typed in (None, "", [], {}):
        return True
    if isinstance(typed, (list, tuple)):
        return kept is not _UNKNOWN and _repeats_first_row(typed, kept)
    if not isinstance(typed, str) or len(typed) > _POINTER_CHARS:
        return False
    text = typed.strip()
    if not text:
        return True
    bare = text.strip("`'\"{}<>$()[] ").strip()
    # The kept NAME typed there ("payment_rows"), or the reference itself
    # ('_from_script={"content": "rows"}'): both only point at the value.
    if ARG in text or bare == name:
        return True
    if bare.lower() in _PLACEHOLDERS:
        return True
    # A structure that names the kept value where the rows should be:
    # "{'sheets': {'Statements': {'rows': 'excel_data'}}}".
    if text[:1] in "{[" and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text):
        return True
    return kept is not _UNKNOWN and _repeats_first_row(text, kept)


#: The WHOLE of a typed argument that is nothing but a reference written
#: out as text: ``_from_script={"content": "rows"}``, ``{"_from_script":
#: {...}}``, with or without quotes or backticks round it. No nested repeat
#: and one bounded run inside the braces, so linear on any text (pinned in a
#: child process with a deadline).
_REFERENCE_TEXT_RX = re.compile(
    r"""[`'"({\s]*_from_script[`'"\s]*[=:]\s*\{([^{}]{0,160})\}[`'")}\s.;,]*"""
)
_REFERENCE_PAIR_RX = re.compile(
    r"""["']?([A-Za-z_]\w{0,40})["']?\s*:\s*["']([^"']{1,80})["']"""
)


def misplaced_reference(tool: str, args: Any, ctx: Any) -> str:
    """A sentence refusing a call whose CONTENT argument holds nothing but a
    reference to a kept value, written out as text; "" for every other call.

    Seen live on the packaged app (v1.473.0): a script kept 640 rows, and
    the model then called ``write_document(path=..., content='_from_script=
    {"content": "payment_rows"}')`` with no ``_from_script`` argument. The
    workbook held that one line of text, the self-check read "clean", and
    the reply said "640 rows". The reference is a separate ARGUMENT; typed
    into the content it is just text.

    REFUSED, not guessed at: turning the text into the argument here would
    run a kept value no approval card had named or pinned. The refusal
    spells the call out, so the model's next call is the right one.

    Narrow on purpose, so no other write changes: only a tool that can take
    a kept value, only an argument that can be filled, only when the WHOLE
    text is the reference, and only while a script of this chat or run has
    kept something. A file that should really hold such a line can still be
    written everywhere else. Sync and cheap; never raises."""
    try:
        if not isinstance(args, dict) or ARG in args:
            return ""
        fillable = FROM_SCRIPT_ARGS.get(tool)
        if not fillable:
            return ""
        for arg in sorted(fillable):
            typed = args.get(arg)
            if not isinstance(typed, str) or ARG not in typed:
                continue
            found = _REFERENCE_TEXT_RX.fullmatch(typed.strip())
            if found is None:
                continue
            # (A caller with no chat and no run gets a key of its own each
            # time, so nothing is ever kept under it.)
            have = SHELF.names(_key_for(ctx)[0])
            if not have:
                return ""
            pair = _REFERENCE_PAIR_RX.search(found.group(1))
            target = pair.group(1) if pair and pair.group(1) in fillable else arg
            name = pair.group(2).strip() if pair else ""
            if name not in have:
                name = have[0] if len(have) == 1 else (name or "<name>")
            return (
                f"NOTHING WAS CHANGED. `{arg}` holds the words of a reference, not "
                f"the kept value itself. `{ARG}` is a separate argument of the "
                f"call, beside the others. Make the call again with the arguments "
                f'{{..., "{ARG}": {{"{target}": "{name}"}}}} and leave `{arg}` out'
                + (f". Kept now: {', '.join(have)}" if name not in have else "")
            )
    except Exception:  # noqa: BLE001 — a check never fails a call
        return ""
    return ""


# --------------------------------------------------------------------------- #
# Figures the model typed beside a script's own (v1.473.0)
# --------------------------------------------------------------------------- #
#
# THE PROBLEM, seen live twice on the packaged app. A script printed sixteen
# file totals. The model typed them into a Word document correctly, and added
# a "Grand Total" it had worked out in its head: 793,542.46 one time,
# 792,345.26 the next (the true figure was 793,184.46). Telling the model
# "work out every figure in the script" did not stop it.
#
# WHAT IS DONE. The app remembers the figures the computing tools PRODUCED in
# this chat or run (a tool script's printed output, a `calculate` answer).
# When a file tool is then given TYPED content that holds at least
# :data:`FIGURES_MIN_MATCHED` of those figures (so the file is plainly built
# from them) AND a money figure that is none of them, the tool's answer names
# that figure and says to work it out properly. The file is still written:
# this is a check the model reads, never a refusal (a figure the user gave is
# legitimate, and the app cannot tell).
#
# A figure in the file matches a produced one when it is that number rounded
# to the places the file shows (54453.149999999994 is "54,453.15", and
# "54,453"). Only figures that look like money are ever named: thousands
# commas, or exactly two decimals. Dates, ids, counts, versions and
# percentages are not figures here.

#: The tools whose answers are computed figures.
FIGURE_SOURCES = frozenset({"tool_script", "calculate"})

MAX_FIGURES = 20_000
FIGURES_MIN_MATCHED = 3
#: Files whose newest write still holds a figure nobody worked out, per chat
#: or run, until the reply says so (:func:`unchecked_figures_note`).
MAX_UNCHECKED_FILES = 8
UNCHECKED_TTL_S = 1800.0
_FIGURES_LISTED = 5
_FIGURE_SCAN_CHARS = 200_000

#: One number as people write it: 1,234.50 / 1234.5 / 12345. Never part of a
#: word, an id (statement_01), a date (2026-01-31, 01/31/2026), a time, a
#: version (1.473.0), a percentage, or a list written without spaces. A token
#: can only START after something that is not a digit, letter, dot or comma,
#: so a long run of digits or of comma groups is read once: linear (pinned in
#: a child process).
_FIGURE_RX = re.compile(
    r"(?<![\w.,])(?<!\d[\-/:])"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,})"
    r"(?![\w%]|[.\-/:]\d|,\d)"
)


def figures_in(text: Any) -> "list[tuple[str, float, int]]":
    """``(as written, value, decimal places)`` of each number in *text*
    (bounded: the head and the tail of a very long text)."""
    if not isinstance(text, str) or not text:
        return []
    if len(text) > _FIGURE_SCAN_CHARS:
        half = _FIGURE_SCAN_CHARS // 2
        text = text[:half] + "\n" + text[-half:]
    out: list[tuple[str, float, int]] = []
    for found in _FIGURE_RX.finditer(text):
        token = found.group(1)
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        places = len(token.rpartition(".")[2]) if "." in token else 0
        out.append((token, value, places))
        if len(out) >= MAX_FIGURES:
            break
    return out


def _typed_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)[: _FIGURE_SCAN_CHARS * 2]
    except (TypeError, ValueError, RecursionError):
        return ""


def note_figures(ctx: Any, output: Any) -> None:
    """Remember the figures a computing tool just produced (its *output*),
    for this chat or run. Never raises."""
    try:
        key, once = _key_for(ctx)
        if once:
            return
        values = [v for _t, v, _p in figures_in(output if isinstance(output, str) else "")]
        if values:
            SHELF.add_figures(key, values)
    except Exception:  # noqa: BLE001 — bookkeeping never fails a call
        return


def typed_figures_note(tool: str, args: Any, ctx: Any) -> str:
    """The sentence a file tool's answer gains when its TYPED content holds
    figures the computing tools produced and a money figure that is none of
    them; "" otherwise (and for content taken from a kept value, which is
    exact by construction). Sync and cheap; never raises."""
    try:
        fillable = FROM_SCRIPT_ARGS.get(tool)
        if not fillable or not isinstance(args, dict):
            return ""
        key, once = _key_for(ctx)
        if once:
            return ""
        path = str(args.get("path") or "").strip()
        produced = SHELF.figures(key)
        text = "\n".join(_typed_text(args.get(a)) for a in sorted(fillable) if args.get(a))
        if not produced or not text:
            # Written from a kept value, or nothing was produced here: the
            # file holds nothing left to check (an earlier mark is cleared).
            SHELF.clear_unchecked(key, path)
            return ""
        import bisect

        matched = 0
        other: list[str] = []
        for token, value, places in figures_in(text):
            tolerance = 0.5 * 10 ** (-min(places, 6)) + 1e-9
            at = bisect.bisect_left(produced, value)
            near = [produced[i] for i in (at - 1, at) if 0 <= i < len(produced)]
            if any(abs(p - value) <= tolerance for p in near):
                matched += 1
            elif ("," in token or places == 2) and token not in other:
                other.append(token)
        if matched < FIGURES_MIN_MATCHED or not other:
            SHELF.clear_unchecked(key, path)
            return ""
        SHELF.note_unchecked(key, path, other)
        shown = ", ".join(other[:_FIGURES_LISTED])
        more = len(other) - _FIGURES_LISTED
        one = len(other) == 1
        return (
            f"[check the figures] {matched} of the figures in this file are ones a "
            f"script or the calculator produced. {'This one is' if one else 'These are'} "
            f"not: {shown}" + (f" and {more} more" if more > 0 else "") + ". "
            f"If you worked {'it' if one else 'them'} out yourself (a total, a "
            "difference, an average), do not trust it: work it out in a tool "
            "script or with `calculate`, then write the file again with the "
            f"result. A figure the user gave you can stay as it is."
        )
    except Exception:  # noqa: BLE001 — a check never fails a call
        return ""


def _matches_produced(token: str, produced: "list[float]") -> bool:
    """*token* (a figure as written) is one a computing tool produced, to the
    places it shows (the rule :func:`typed_figures_note` matches by)."""
    import bisect

    found = figures_in(token)
    if not found:
        return False
    _t, value, places = found[0]
    tolerance = 0.5 * 10 ** (-min(places, 6)) + 1e-9
    at = bisect.bisect_left(produced, value)
    return any(abs(produced[i] - value) <= tolerance for i in (at - 1, at) if 0 <= i < len(produced))


def _still_unchecked(key: str, files: "dict[str, list[str]]") -> "dict[str, list[str]]":
    """*files* without the figures a computing tool has produced SINCE the
    file was written (v1.495.0: the model worked them out after the check
    asked it to; the figure in the file is then a worked-out one)."""
    if not files:
        return {}
    produced = SHELF.figures(key)
    out: dict[str, list[str]] = {}
    for path, figures in files.items():
        left = [t for t in figures if not (produced and _matches_produced(t, produced))]
        if left:
            out[path] = left
    return out


def unchecked_now(ctx: Any) -> "dict[str, list[str]]":
    """The files of this chat or run whose newest write still holds a figure
    nobody worked out, and those figures, WITHOUT forgetting them (v1.495.0:
    ``daemon/until_done`` asks while the reply can still fix them). Never
    raises."""
    try:
        if ctx is None:
            return {}
        key, once = _key_for(ctx)
        return {} if once else _still_unchecked(key, SHELF.peek_unchecked(key))
    except Exception:  # noqa: BLE001
        return {}


def unchecked_figures_note(ctx: Any) -> str:
    """What the USER reads under a reply whose turn left a file holding a
    figure nobody worked out: the file's name and the figure. "" when there
    is none (a file written again without it is not named).

    Decided from what ran, never from the reply's words. The model was told
    the same thing in the file tool's answer and may have ignored it (seen
    live: it answered "Done" over a wrong grand total). Never raises."""
    try:
        if ctx is None:
            return ""
        key, once = _key_for(ctx)
        files = {} if once else _still_unchecked(key, SHELF.take_unchecked(key))
        if not files:
            return ""
        parts = []
        for path, figures in list(files.items())[:3]:
            name = path.replace("\\", "/").rstrip("/").rpartition("/")[2] or path
            shown = ", ".join(figures[:3]) + (f" and {len(figures) - 3} more" if len(figures) > 3 else "")
            one = len(figures) == 1
            parts.append(
                f"{name} holds {'a figure' if one else 'figures'} Jarvis typed itself: {shown}. "
                f"No script or calculator worked {'it' if one else 'them'} out, so check "
                f"{'it' if one else 'them'} before you rely on {'it' if one else 'them'}"
            )
        more = len(files) - 3
        return "\n\n_Note: " + ". ".join(parts) + (f". {more} more files too" if more > 0 else "") + "._"
    except Exception:  # noqa: BLE001 — a note never fails a reply
        return ""


def _refs(tool: str, args: dict[str, Any]) -> "tuple[dict[str, str], str]":
    """The ``{argument: kept name}`` a call asks for, or a sentence saying
    why it cannot be filled."""
    spec = args.get(ARG)
    if not isinstance(spec, dict) or not spec:
        return {}, (
            f'`{ARG}` must look like {{"<argument>": "<name a script kept>"}}, '
            'for example {"content": "totals"}'
        )
    allowed = FROM_SCRIPT_ARGS.get(tool)
    if not allowed:
        return {}, (
            f"`{tool}` does not take a script's kept value. The tools that do: "
            + ", ".join(sorted(FROM_SCRIPT_ARGS))
        )
    out: dict[str, str] = {}
    for arg, name in spec.items():
        arg, name = str(arg), str(name or "").strip()
        if arg not in allowed:
            return {}, (
                f"`{tool}` takes a kept value only for: {', '.join(sorted(allowed))} "
                f"(not `{arg}`). Type `{arg}` yourself"
            )
        if not name:
            return {}, f"`{ARG}` names no kept value for `{arg}`"
        out[arg] = name
    return out, ""


def pins_for(tool: str, args: Any, ctx: Any) -> "dict[str, str] | None":
    """``{argument: fingerprint}`` of each kept value a call names, AS IT IS
    NOW ("" = not kept yet); None for a call with no reference.

    WHAT THE CARD SHOWED IS WHAT RUNS (v1.473.0 review). A lane builds every
    approval card of a round BEFORE any call of the round runs, so a script
    in the same round (or one still running while the user reads the card)
    could keep a different value under the same name after the card showed
    the first one: the user approved one list and another one ran. The lane
    takes these fingerprints where it builds the card and hands them to
    ``ToolRegistry.invoke(kept_pins=...)``; :func:`fill_from_script` then
    refuses the call when a value is no longer the one that was shown.
    Sync and cheap; never raises."""
    if not has_refs(args):
        return None
    out: dict[str, str] = {}
    try:
        spec = args.get(ARG)
        key, _ = _key_for(ctx)
        for arg, name in (spec.items() if isinstance(spec, dict) else ()):
            kept = SHELF.get(key, str(name or "").strip())
            out[str(arg)] = kept.sha if kept is not None else ""
    except Exception:  # noqa: BLE001 — cannot read: nothing matches ("" pins)
        return {str(a): "" for a in (args.get(ARG) or {})} if isinstance(args.get(ARG), dict) else {}
    return out


def fill_from_script(
    tool: str, args: dict[str, Any], ctx: Any, pins: "dict[str, str] | None" = None
) -> "tuple[dict[str, Any], dict[str, Any], str]":
    """``(run_args, record_args, error)`` for a call that carries
    :data:`ARG`.

    *pins* (:func:`pins_for`, taken when the approval card was built): a
    kept value whose fingerprint is no longer the pinned one is REFUSED, so
    an approval never covers a value the card did not show.

    *run_args* is what the tool is given: the kept values in place.
    *record_args* is what is checked for permission and written to the
    ledger: the reference, with each value's fingerprint and summary, never
    the value itself (a ledger row must not hold 8 MB of client rows, and an
    "always allow exactly this" grant must not cover different data under
    the same name). On an error both are the arguments without the
    reference."""
    bare = {k: v for k, v in args.items() if k != ARG}
    refs, problem = _refs(tool, args)
    if problem:
        return bare, bare, problem
    # A model that was shown the argument as optional may still send it
    # empty beside the reference: the reference is what was meant.
    sent = bare  # as the model sent it: what a "given twice" refusal records
    bare = {k: v for k, v in bare.items() if k not in refs}
    key, once = _key_for(ctx)
    if once:
        return bare, bare, (
            "a script's kept values are only available inside a chat or a run"
        )
    run = dict(bare)
    noted: dict[str, Any] = {}
    for arg, name in refs.items():
        kept = SHELF.get(key, name)
        if kept is None:
            have = SHELF.names(key)
            return bare, bare, (
                f"no script here kept a value named `{name}`. In a tool script "
                f"call tools.keep(\"{name}\", value) first"
                + (f". Kept now: {', '.join(have)}" if have else "")
            )
        if pins is not None and pins.get(arg, "") != kept.sha:
            return bare, bare, (
                f"NOTHING WAS CHANGED. The kept value `{name}` is not the one "
                "the approval showed (a script kept a new one after the user "
                "was asked). Make this call again, by itself, so the user "
                "sees what it will write"
            )
        try:
            value = json.loads(kept.text)
        except (ValueError, RecursionError):
            return bare, bare, f"the kept value `{name}` could not be read back"
        if not _left_out(args.get(arg), name, value):
            # Seen live: a heading row typed in `content` beside kept rows
            # that had none. The two are not joined (which comes first is a
            # guess), so say how to give it once without losing either part.
            return sent, sent, (
                f"`{arg}` was given twice: typed, and in `{ARG}`. Give it once: "
                f"leave `{arg}` out and put ALL of it in the kept value (a "
                "header row too), or type all of it and drop the reference"
            )
        run[arg] = _shaped(tool, arg, value, bare)
        noted[arg] = {"name": name, "sha": kept.sha, "what": kept.summary}
    record = dict(bare)
    record[ARG] = noted
    return run, record, ""


def _shaped(tool: str, arg: str, value: Any, call: "dict[str, Any]") -> Any:
    """A kept value in the shape its tool writes best.

    ONE case: rows a script kept, written to a WORKBOOK. A script that read
    CSV files holds every cell as text, and rows handed to the workbook
    writer as a plain list keep text as text: the amounts could not be added
    up in Excel (measured live: every Amount cell was text). Handed over as a
    named sheet they get what a sheet gets: clean numbers and ISO dates as
    values, a bold frozen header (the writer's own careful rule: a leading
    zero or more than 15 digits stays text). Any other value, tool or file
    type is passed as it was kept."""
    if tool != "write_document" or arg != "content":
        return value
    kind = str(call.get("kind") or "").strip().lower().lstrip(".")
    path = str(call.get("path") or "").strip().lower()
    if (kind or path.rpartition(".")[2]) != "xlsx":
        return value
    if isinstance(value, list) and value and all(isinstance(r, (list, dict)) for r in value):
        if all(isinstance(r, list) for r in value) or all(isinstance(r, dict) for r in value):
            return {"sheets": {"Sheet1": value}}
    return value


def preview_args(tool: str, args: Any, ctx: Any) -> Any:
    """*args* as an approval card should show them: each kept value named
    with its summary (never the whole value, never an error). Sync and
    cheap, so both lanes and the runtime can call it where they build a
    card."""
    if not has_refs(args):
        return args
    try:
        spec = args.get(ARG)
        key, _ = _key_for(ctx)
        shown = {k: v for k, v in args.items() if k != ARG}
        for arg, name in (spec.items() if isinstance(spec, dict) else ()):
            kept = SHELF.get(key, str(name))
            shown[str(arg)] = (
                f"(from the script's `{name}`: {kept.summary})"
                if kept is not None
                else f"(from the script's `{name}`: not kept yet)"
            )
        return shown
    except Exception:  # noqa: BLE001 — a preview never breaks a card
        return args


def kept_changes(args: Any, ctx: Any) -> "list[Any] | None":
    """The list a call's ``_from_script: {"changes": name}`` names, read for
    a card (``apply_changes``), else None."""
    try:
        spec = args.get(ARG) if isinstance(args, dict) else None
        name = spec.get("changes") if isinstance(spec, dict) else None
        if not name:
            return None
        key, _ = _key_for(ctx)
        kept = SHELF.get(key, str(name))
        value = json.loads(kept.text) if kept is not None else None
        return value if isinstance(value, list) else None
    except Exception:  # noqa: BLE001
        return None
