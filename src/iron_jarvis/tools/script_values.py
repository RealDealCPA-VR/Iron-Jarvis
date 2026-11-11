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

import hashlib
import json
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
            return (
                f"{len(value):,} rows, up to {widest} columns; first row: " + cut(_head(first))
            )
        if isinstance(first, dict):
            return f"{len(value):,} items; first: " + cut(_head(first))
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
            names = self._keys.pop(key, None)
            if names:
                self._chars -= sum(len(v.text) for v in names.values())

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
            self._unplaced.clear()
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


def _left_out(typed: Any, name: str) -> bool:
    """Did the model leave the argument to the reference? Yes when it typed
    nothing, and yes when what it typed only POINTS at the kept value: its
    name, or the reference written out (both seen with a real model,
    v1.473.0; refusing them cost the turn a round each time). Anything else
    typed is real content, and giving both is refused."""
    if typed in (None, "", [], {}):
        return True
    if not isinstance(typed, str) or len(typed) > _POINTER_CHARS:
        return False
    # Seen live, twice: the kept NAME typed there ("payment_rows"), and the
    # reference itself typed there ('_from_script={"content": "rows"}').
    # Neither is content: both only point at the kept value.
    return ARG in typed or typed.strip().strip("`'\"{}<>$()").strip() == name


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
        if not _left_out(args.get(arg), name):
            # Seen live: a header row typed in `content` beside kept rows. The
            # two are not joined (which comes first is a guess), so say how
            # to give it once without losing either part.
            return {}, (
                f"`{arg}` was given twice: typed, and in `{ARG}`. Give it once: "
                f"leave `{arg}` out and put ALL of it in the kept value (a "
                "header row too), or type all of it and drop the reference"
            )
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
            run[arg] = json.loads(kept.text)
        except (ValueError, RecursionError):
            return bare, bare, f"the kept value `{name}` could not be read back"
        noted[arg] = {"name": name, "sha": kept.sha, "what": kept.summary}
    record = dict(bare)
    record[ARG] = noted
    return run, record, ""


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
