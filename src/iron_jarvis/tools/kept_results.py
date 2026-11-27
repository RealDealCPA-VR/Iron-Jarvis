"""A result too long to show is KEPT WHOLE for its own chat, and read back in
parts (the intelligence plan, release 8: "long files are read to the end").

Every lane cuts ONE tool result at a limit before the model reads it (plain
chat 12,000 characters, a coding session up to 32,000, an agent run 16,000),
and what was cut was gone: the model either reasoned from a part or spent
calls looking for the rest. This module is the ONE place that keeps the rest,
and :class:`KeptResultReadTool` (``kept_result_read``) is the ONE tool that
reads it back. Nothing else stores an oversized result, and nothing else
reads one.

HOW A LANE USES IT (two steps, so the old cut is what happens whenever this
module cannot do better):

1. :func:`hold` takes the place of the lane's own cut where the tool message
   is built. It returns EXACTLY what the lane's cut returns (a result under
   the limit is byte for byte what it was) and, for a result over the limit,
   remembers the whole text ON THE TURN'S OWN CONTEXT until the round ends
   (nothing global holds it: it goes when the turn's context goes). It
   writes nothing.
2. :func:`settle`, once per round, first asks whether the reader may join
   this turn at all (``arm(dry=True)``: the user's deny, a surface's own tool
   list, a small model's tool budget, a task another computer sent) and
   writes NOTHING when it may not. Then it saves each remembered result (off
   the event loop, bounded), puts ``kept_result_read`` on the turn's roster
   (``arm()``), and only then rewrites the tool message: the start (and,
   where the lane's cut kept it, the end), and ONE line of Iron Jarvis's own
   words under the result with the handle. A save that fails or does not
   come back leaves no file of any kind and the message stays the old cut.

WHOSE IT IS. A kept result is filed under ``tools/registry.values_key``, the
key release 1 gave a chat's stored values: the saved chat (``chat:<id>``),
else the turn (a new chat's first message; :func:`rekey` moves it when the
chat is saved), a coding session's ``build:<id>``, an agent run's session id.
The folder on disk is named by a hash of that key and the reader looks ONLY
in the caller's own folder, so another chat, another run, a helper, a guessed
or copied handle and a call with no owner all get the same answer
(:func:`not_found`) and learn nothing, not even that the handle exists.

HOW LONG. Only as long as something can still read it:

* A PLAIN CHAT's kept results last for the reply that kept them. A later
  reply never sees the handle (chat history carries the person's and the
  assistant's words, never a tool message), so they are removed when the
  reply ends, however it ends (:func:`reply_scope` / :func:`reply_frames`,
  set by both chat lanes; a plain-chat turn outside one keeps nothing).
* An AGENT RUN's and a CODING SESSION's last up to :data:`KEEP_DAYS` days:
  their saved transcript holds the line with the handle, and
  :func:`arm_at_start` gives a turn that starts with such a line (a run or a
  coding reply picked up after a restart) the reader from its first step.
* Removed with its chat, coding session or run (:func:`drop_owners`; a save
  still running for an owner that was dropped writes nothing), when its owner
  holds more than :data:`MAX_PER_OWNER` results or :data:`MAX_OWNER_CHARS`
  characters (the oldest of EARLIER rounds goes; a round never pushes out its
  own), and when the whole store would pass :data:`MAX_STORE_BYTES` (the
  oldest of anyone's, never one saved within the last hour).

TRUST IS NEVER READ FROM THE TEXT. A part read back is outside text: the
reader is ``returns_untrusted_content``, so every lane fences it, scans it
and taints on a hit, exactly as it does the whole. The ONE exception is a
result whose original call said it was the session's own source
(``builtins.is_own_source(result)``, decided at the call and saved with the
text). What a result's text starts with decides nothing: a web page that
arrives under a title line, or a page a scanner flagged, is fenced like any
other.

LOCAL ONLY. A result kept while the chat was Local only is marked so, and is
handed back only to work that itself stays on this network (the scope is on
and no cloud model was allowed for the message). While a cloud model is
allowed for one message nothing is kept at all (the read would be refused).

A TOOL WITH A CAP OF ITS OWN. ``read_document`` and ``extract_pdf`` cut
their own answer at 16,000 characters. They still answer exactly so, and put
the uncut answer beside it (``ToolResult.whole``). :func:`settle` puts the
whole in the answer's place when the whole is clean (the injection scan the
lane ran on the answer is run on the whole, off the event loop; a whole over
:data:`MAX_WHOLE_CHARS` is not scanned and not kept) and then either shows it
whole (it fits the lane's limit) or shows the start and keeps the rest.
Wherever nothing can keep it, the model gets the tool's own cut with the
tool's own words, exactly as before.

AT REST. ``<home>/kept-results/<hash of the owner>/<handle>.txt`` (+ a small
``.json`` beside it: size, when, Local only, own source). On this PC only;
left out of every backup (``maintenance.create_backup``) and in no export.
The text is what the model was being shown, no rawer. Never written to a log
line, an event, a usage row or an error message.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

from .base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.tools.kept_results")

#: The one reader's name.
READER = "kept_result_read"
#: The folder under the app's home.
FOLDER = "kept-results"
#: A kept result is removed this many days after it was kept.
KEEP_DAYS = 30
#: The longest result that is kept: the most one tool's output may be when it
#: is handed to a script (``repl.session.MAX_TOOL_OUTPUT_CHARS``).
MAX_RESULT_CHARS = 4_000_000
#: The longest WHOLE a document reader may hand over to be shown or kept
#: (``ToolResult.whole``). The whole must be scanned for planted instructions
#: before any of it is shown, and that scan holds the interpreter: measured
#: at this size, the event loop stalls 0.09 s on prose and up to 0.65 s on
#: text built to be slow (2.7 s at 4,000,000). Over it: the tool's own cut,
#: and a line that says the rest was not kept.
MAX_WHOLE_CHARS = 1_000_000
#: The most results one chat, run or coding session holds, and the most
#: characters in all: the script shelf's own bounds for one chat's kept values
#: (``script_values.MAX_VALUES_PER_KEY`` / ``MAX_VALUE_CHARS``). Over either,
#: the oldest goes.
MAX_PER_OWNER = 16
MAX_OWNER_CHARS = 8_000_000
#: The most kept text on disk, across every chat, run and coding session.
#: When a save would pass it the oldest kept results go first, whoever they
#: belong to; the daily tidy-up holds the same line.
MAX_STORE_BYTES = 512 * 1024 * 1024
#: A result saved within this long is never removed to make room (so the one
#: being saved, and one a reply is still working with, always stay).
FRESH_S = 3600.0
#: How long a dropped owner (a deleted chat, coding session or run) is
#: remembered, so a save that was already running for it writes nothing.
DROPPED_S = 3600.0
#: The most characters one call of the reader returns. Under every lane's
#: limit for one result with room for the reader's own line and the fence, so
#: a part read back is never itself cut.
READ_CHARS = 10_000
#: A shortened result shows at least this much of itself; when the note and
#: the fence leave less room, the lane's old cut is used.
MIN_SHOWN = 2_000
#: How long one save or read may take before the lane goes on without it.
IO_TIMEOUT_S = 10.0

_HANDLE_RX = re.compile(r"^kr-[0-9a-f]{16}$")
#: A handle's shape, for sizing a note before its result is saved.
_NO_HANDLE = "kr-" + "0" * 16

#: The opening of the one line (also what :func:`defang` neutralises inside a
#: result, so a result cannot show a line that reads as this one).
NOTE_LEAD = "[Iron Jarvis kept this whole result for this "
_HOW = (
    "call " + READER + ' with handle "{handle}" and start (the character to read from); '
    "end=true reads the last part.]"
)
_NOTE = NOTE_LEAD + "{where}: {n} characters. Shown above: {shown}. To read the rest, " + _HOW
#: The line a tool message keeps when the context planner trims it to a
#: marker: nothing of the result is shown any more, so it says only how long
#: the result is and how to read it.
_NOTE_TRIMMED = NOTE_LEAD + "{where}: {n} characters. To read it, " + _HOW
#: The whole line, as a pattern.
NOTE_RX = re.compile(
    re.escape(NOTE_LEAD) + r"(chat|coding session|run): ([0-9,]+) characters\. Shown above: "
    r"characters 1 to [0-9,]+(?: and [0-9,]+ to [0-9,]+)?\. To read the rest, call "
    + READER + r' with handle "(kr-[0-9a-f]{16})" and start \(the character to read from\); '
    r"end=true reads the last part\.\]"
)
#: Where the middle was left out (inside the result, so inside its fence).
_GAP = "\n[… characters {a} to {b} are not shown here: see the note under this result …]\n"
#: A result too long to keep: the lane's cut, and this said.
_TOO_BIG = (
    "\n[Iron Jarvis did not keep the rest of this result: it has {n} characters, and a "
    "result is kept only up to {most}.]"
)
#: One round that held more long results than an owner keeps.
_NO_ROOM = (
    "\n[Iron Jarvis did not keep the rest of this result: this {where} already keeps "
    "{most} long results from this step, the most it holds.]"
)
#: The reader's own first line (:func:`defang` neutralises a copy in a part).
_LEAD = "[Characters {a} to {b} of {n} of the kept result; {then}.]\n"
_LEAD_RX = re.compile(r"\[(Characters [0-9,]+ to [0-9,]+ of [0-9,]+ of the kept result;)")


def not_found(where: str) -> str:
    """What every call gets that names no kept result of its own chat: one
    sentence for a handle that never was, one that is another chat's, one
    past its date and one that newer results replaced."""
    return (
        f"{READER}: there is no kept result with that handle in this {where}. A kept result can "
        f"be read only in the chat, run or coding session it was kept in; it may also be past its "
        f"{KEEP_DAYS} days, or have been replaced by newer results: read the source again."
    )


_LOCAL_OFF = (
    READER + ": this result was kept while this {where} was Local only, so Iron Jarvis gives it "
    "only to a model on your own network. This {where} is not Local only now, so it was not read. "
    "Turn Local only back on to read it."
)
_LOCAL_ONCE = (
    READER + ": this result was kept while this {where} was Local only, so Iron Jarvis gives it "
    "only to a model on your own network. {name} runs off your network and was allowed for this "
    "one message, so the kept result was not read. Ask again on a model on your own network."
)


def _now() -> float:
    """The clock a kept result's age is read from. A seam: a test says what
    time it is, so "thirty days later" never waits."""
    return time.time()


# --------------------------------------------------------------------------- #
# The words (pure text: no disk, no clock)
# --------------------------------------------------------------------------- #


def defang(text: str) -> str:
    """*text* with any copy of Iron Jarvis's own two lines made unlike them
    (``[`` to ``(``, the fence's own rule: nothing is hidden, it only stops
    reading as Iron Jarvis's): the kept-result line's opening, and the
    reader's first line. Same length."""
    return _LEAD_RX.sub(r"(\1", text.replace(NOTE_LEAD, "(" + NOTE_LEAD[1:]))


#: The fence's opening and closing lines, read once.
_FENCE: list[str] = []


def split_fence(text: str) -> tuple[str, str, str]:
    """``(before, result, after)`` for LAYOUT only (where the one line goes
    and which part is cut): the untrusted-data fence round a result
    (``computeruse.safety.wrap_untrusted``, read from that function so its
    words can change), wherever it opens. ``before`` ends with the fence's
    opening lines (a tool that fences its own answer puts a title first);
    ``after`` starts at its closing lines and holds everything under it. A
    result with no fence is ``("", text, "")``. This decides NO trust: see
    the module docstring."""
    if not _FENCE:
        from ..computeruse.safety import wrap_untrusted

        _FENCE.extend(wrap_untrusted("\x00").split("\x00", 1))
    opening, closing = _FENCE
    start = text.find(opening) if opening and closing else -1
    if start >= 0:
        end = text.find(closing, start + len(opening))
        if end >= 0:
            return text[:start + len(opening)], text[start + len(opening):end], text[end:]
    return "", text, ""


def note_of(content: Any) -> str:
    """The kept-result line in a tool message's TEXT, or "". Only a line
    UNDER the result counts (after the fence when the message has one). A
    line found here is a claim, not a fact: :func:`arm_at_start` checks its
    handle against the store before anything trusts it."""
    text = str(content or "")
    _before, _body, after = split_fence(text)
    found = [m.group(0) for m in NOTE_RX.finditer(after if after else text)]
    return found[-1] if found else ""


def _trimmed_note(note: str) -> str:
    """The short line for a trimmed message, from a full kept-result line."""
    got = NOTE_RX.fullmatch(note)
    if got is None:
        return ""
    return _NOTE_TRIMMED.format(where=got.group(1), n=got.group(2), handle=got.group(3))


def _kept_line(message: Any) -> str:
    """What a trimmed tool message keeps: the line :func:`settle` (or
    :func:`arm_at_start`, for a transcript read back) put ON THE MESSAGE after
    the store held its result for this owner. Never read from the text, so a
    line a result carries inside itself is never kept as Iron Jarvis's."""
    return str(getattr(message, "kept_line", "") or "")


def _keep_note_on_trim() -> None:
    """A tool result the context planner trims to a marker keeps its
    kept-result line (``context.agent_window.KEEP_ON_TRIM``)."""
    from ..context import agent_window

    if _kept_line not in agent_window.KEEP_ON_TRIM:
        agent_window.KEEP_ON_TRIM.append(_kept_line)


_keep_note_on_trim()


def _where(ctx: Any) -> str:
    sid = str(getattr(ctx, "session_id", "") or "")
    if sid == "chat":
        return "chat"
    return "coding session" if sid.startswith("build:") else "run"


def kept_form(text: str, cap: int, handle: str, where: str, *, tail: bool) -> "str | None":
    """The tool message for a kept result: at most *cap* characters. The
    fence (when the result has one) stays whole around what is shown, and the
    one line sits under it. ``tail``: the lane's cut keeps the end too (two
    thirds start, one third end, as the chat lanes always did). None when the
    note and the fence leave less than :data:`MIN_SHOWN` to show."""
    before, body, after = split_fence(text)
    n = len(body)

    def note(head: int, tail_from: int) -> str:
        shown = f"characters 1 to {head:,}"
        if tail_from:
            shown += f" and {tail_from:,} to {n:,}"
        return _NOTE.format(where=where, n=f"{n:,}", shown=shown, handle=handle)

    widest = len(note(n, n if tail else 0))
    gap = len(_GAP.format(a=f"{n:,}", b=f"{n:,}")) if tail else 0
    room = cap - len(before) - len(after) - 1 - widest - gap
    if room < MIN_SHOWN or room >= n:
        return None
    head = room * 2 // 3 if tail else room
    last = room - head
    if not last:
        return before + defang(body[:head]) + after + "\n" + note(head, 0)
    return (
        before + defang(body[:head]) + _GAP.format(a=f"{head + 1:,}", b=f"{n - last:,}")
        + defang(body[n - last:]) + after + "\n" + note(head, n - last + 1)
    )


# --------------------------------------------------------------------------- #
# The store (BLOCKING: every caller on the event loop goes through a thread)
# --------------------------------------------------------------------------- #

_LOCK = threading.Lock()
#: The bytes of kept text under each store, once known (a save adds, a
#: removal subtracts): the whole store is walked only when this is not known
#: or says the store is over its bound.
_TOTALS: dict[str, int] = {}
#: Owners that were dropped, and until when a save for them is refused.
_DROPPED: dict[tuple[str, str], float] = {}
_DROPPED_MOST = 512
#: Handles whose save the lane stopped waiting for: the save removes its own
#: files when it finishes.
_ABANDONED: dict[str, None] = {}
_ABANDONED_MOST = 512


class NoRoom(Exception):
    """This owner already holds its most results, all of them from this
    round: the new one is not kept (a round never pushes out its own)."""


def root_of(engine: Any) -> "Path | None":
    """Where this app keeps results: ``<home>/kept-results``; None for a
    database in memory (nothing is kept then: the lane's old cut)."""
    from .registry import _home_of

    home = _home_of(engine)
    return (home / FOLDER) if home is not None else None


def _owner_dir(root: Path, owner: str) -> Path:
    """An owner's folder. A plain chat's is marked ``reply-``: what it holds
    lasts for one reply, so whatever is found there at start-up is left over
    from a reply that never ended properly and is removed."""
    name = hashlib.sha256(owner.encode("utf-8", "surrogatepass")).hexdigest()[:40]
    return root / (("reply-" if owner.startswith("chat:") else "") + name)


def _metas(folder: Path) -> list[tuple[float, str, dict[str, Any]]]:
    """``(kept at, handle, meta)`` of every whole result in *folder*, oldest
    first. A file that is not ours, or is unreadable, is skipped."""
    out: list[tuple[float, str, dict[str, Any]]] = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        handle = name[:-5]
        if not name.endswith(".json") or not _HANDLE_RX.match(handle):
            continue
        try:
            meta = json.loads((folder / name).read_text(encoding="utf-8"))
            out.append((float(meta["created"]), handle, meta))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    out.sort(key=lambda row: (row[0], row[1]))
    return out


def _remove(folder: Path, handle: str) -> None:
    """Remove one result: its text, its record and anything half-written
    (under the lock). The store's running total follows."""
    for suffix in (".json", ".txt", ".json.tmp", ".txt.tmp"):
        path = folder / (handle + suffix)
        try:
            size = path.stat().st_size if suffix == ".txt" else 0
            path.unlink()
        except OSError:
            continue
        key = str(folder.parent)
        if size and key in _TOTALS:
            _TOTALS[key] = max(0, _TOTALS[key] - size)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    # newline="" and surrogatepass: the text comes back character for
    # character (a "\r\n" stays two characters; a lone surrogate is kept).
    with open(tmp, "w", encoding="utf-8", errors="surrogatepass", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _sizes(root: Path) -> dict[tuple[str, str], int]:
    """``(folder name, handle) -> bytes`` of every kept text under *root*."""
    sizes: dict[tuple[str, str], int] = {}
    for folder in os.scandir(root):
        if not folder.is_dir():
            continue
        for entry in os.scandir(folder.path):
            if entry.name.endswith(".txt") and _HANDLE_RX.match(entry.name[:-4]):
                sizes[(folder.name, entry.name[:-4])] = entry.stat().st_size
    return sizes


def _make_room(root: Path) -> int:
    """Hold the whole store under :data:`MAX_STORE_BYTES` (call it under the
    lock): the oldest kept results go first, across every owner, never one
    saved within :data:`FRESH_S` (the one being saved is one of those).
    Returns how many went. The store is walked only when its running total
    is not known or says it is over the bound; the ages are read only when
    something has to go."""
    key = str(root)
    if _TOTALS.get(key, MAX_STORE_BYTES + 1) <= MAX_STORE_BYTES:
        return 0
    sizes = _sizes(root)
    total = _TOTALS[key] = sum(sizes.values())
    if total <= MAX_STORE_BYTES:
        return 0
    rows = sorted(
        (kept_at, name, handle)
        for name in {name for name, _handle in sizes}
        for kept_at, handle, _meta in _metas(root / name)
    )
    fresh, gone = _now() - FRESH_S, 0
    for kept_at, name, handle in rows:
        if _TOTALS[key] <= MAX_STORE_BYTES:
            break
        if kept_at > fresh:
            continue
        _remove(root / name, handle)
        gone += 1
    return gone


def _dropped(root: Path, owner: str) -> bool:
    until = _DROPPED.get((str(root), owner))
    if until is None:
        return False
    if _now() > until:
        _DROPPED.pop((str(root), owner), None)
        return False
    return True


def new_handle() -> str:
    """A handle for a result about to be saved (made BEFORE the save, so a
    save nobody waits for any more can still be named and undone)."""
    return "kr-" + secrets.token_hex(8)


def put(root: Path, owner: str, text: str, *, own_source: bool, local_only: bool,
        handle: "str | None" = None, keep: Iterable[str] = ()) -> str:
    """Keep *text* for *owner* under *handle*; returns the handle. Over the
    owner's bounds the oldest results go, never one of ``keep`` (the handles
    this same round saved: :class:`NoRoom` when only those are left), and
    over the whole store's the oldest of anyone's. Raises on any failure, and
    then NOTHING of this result is left on disk: not its text, not its
    record, not a half-written file."""
    handle = handle or new_handle()
    if not owner or len(text) > MAX_RESULT_CHARS or not _HANDLE_RX.match(handle):
        raise ValueError("not kept")
    folder = _owner_dir(root, owner)
    kept_now = set(keep)
    with _LOCK:
        try:
            if _dropped(root, owner) or handle in _ABANDONED:
                raise ValueError("not kept: its chat is gone, or nobody waits for it")
            folder.mkdir(parents=True, exist_ok=True)
            rows = _metas(folder)
            total = sum(int(meta.get("chars") or 0) for _at, _h, meta in rows) + len(text)
            while rows and (len(rows) + 1 > MAX_PER_OWNER or total > MAX_OWNER_CHARS):
                oldest = next((row for row in rows if row[1] not in kept_now), None)
                if oldest is None:
                    raise NoRoom(owner)
                rows.remove(oldest)
                total -= int(oldest[2].get("chars") or 0)
                _remove(folder, oldest[1])
            _write(folder / (handle + ".txt"), text)
            _write(folder / (handle + ".json"), json.dumps({
                "v": 2, "chars": len(text), "created": _now(),
                "local_only": bool(local_only), "own_source": bool(own_source),
            }))
            if str(root) in _TOTALS:
                _TOTALS[str(root)] += (folder / (handle + ".txt")).stat().st_size
            _make_room(root)
            if handle in _ABANDONED:
                # (Given up on while it was being written. A dropped owner
                # needs no second look: the drop waits for this lock, and
                # removes what it finds.)
                raise ValueError("not kept: nobody waits for it")
        except BaseException:
            _remove(folder, handle)
            _TOTALS.pop(str(root), None)  # (counted afresh at the next save)
            try:
                folder.rmdir()  # (only when this left it empty)
            except OSError:
                pass
            raise
        finally:
            _ABANDONED.pop(handle, None)
    return handle


def abandon(handle: str) -> None:
    """The lane stopped waiting for this save: it writes nothing, or removes
    what it wrote when it finishes."""
    # (No lock: the save that is being given up may be holding it, and this
    # runs on the event loop. One dict write is all there is.)
    while len(_ABANDONED) >= _ABANDONED_MOST:
        _ABANDONED.pop(next(iter(_ABANDONED)), None)
    _ABANDONED[handle] = None


def get(root: Path, owner: str, handle: str) -> "tuple[dict[str, Any], str] | None":
    """``(meta, text)`` of *owner*'s own kept result, or None: no such handle
    HERE (another owner's handle is simply not in this folder), not a handle
    at all, or past its end date (then it is removed)."""
    if not isinstance(handle, str) or not _HANDLE_RX.match(handle):
        return None
    folder = _owner_dir(root, owner)  # (nothing is ever put under an empty owner)
    with _LOCK:
        try:
            meta = json.loads((folder / (handle + ".json")).read_text(encoding="utf-8"))
            if _now() - float(meta["created"]) > KEEP_DAYS * 86400.0:
                _remove(folder, handle)
                return None
            with open(folder / (handle + ".txt"), encoding="utf-8", errors="surrogatepass",
                      newline="") as fh:
                return meta, fh.read()
        except (OSError, ValueError, KeyError, TypeError):
            return None


def alive(root: Path, owner: str, handles: Iterable[str]) -> set[str]:
    """Which of *handles* the store really holds for *owner* now."""
    folder = _owner_dir(root, owner)
    with _LOCK:
        return {
            h for h in handles
            if isinstance(h, str) and _HANDLE_RX.match(h)
            and (folder / (h + ".json")).is_file() and (folder / (h + ".txt")).is_file()
        }


def _forget(root: Path, owner: str, handles: Iterable[str]) -> None:
    """Remove these kept results of *owner* and its folder when that left it
    empty (call it under the lock). Never raises."""
    folder = _owner_dir(root, owner)
    for handle in handles:
        if _HANDLE_RX.match(str(handle)):
            _remove(folder, str(handle))
    try:
        folder.rmdir()  # (only when this left it empty)
    except OSError:
        pass


def forget(root: Path, owner: str, handles: Iterable[str]) -> None:
    """Remove these kept results of *owner*. Never raises."""
    with _LOCK:
        _forget(root, owner, handles)


def holds(root: "Path | None", owner: str) -> bool:
    """Does *owner* hold a kept result? Never raises."""
    if root is None or not owner:
        return False
    try:
        return any(name.endswith(".json") for name in os.listdir(_owner_dir(root, owner)))
    except OSError:
        return False


def drop_owners(engine: Any, owners: Iterable[str]) -> int:
    """Remove everything kept for these owners: their chat, run or coding
    session was deleted. Each is also remembered for :data:`DROPPED_S`, so a
    save that was already running for it writes nothing. Returns how many
    folders went. Never raises."""
    gone = 0
    try:
        root = root_of(engine)
        if root is None:
            return 0
        with _LOCK:
            until = _now() + DROPPED_S
            for owner in owners:
                owner = str(owner or "")
                if not owner:
                    continue
                while len(_DROPPED) >= _DROPPED_MOST:
                    _DROPPED.pop(next(iter(_DROPPED)))
                _DROPPED[(str(root), owner)] = until
                folder = _owner_dir(root, owner)
                if folder.is_dir():
                    for _at, handle, _meta in _metas(folder):
                        _remove(folder, handle)
                    shutil.rmtree(folder, ignore_errors=True)
                    gone += 1
    except Exception:  # noqa: BLE001 — a delete always completes
        log.warning("kept results of a deleted chat could not be removed", exc_info=True)
    return gone


def rekey(engine: Any, old: str, new: str) -> int:
    """A new chat was saved while its first reply was still running: what
    that reply kept (filed under the turn) becomes the chat's, so the reply's
    own reader still finds it. Returns how many results moved. Never raises."""
    moved = 0
    try:
        root = root_of(engine)
        if root is None or not old or not new or old == new:
            return 0
        src, dst = _owner_dir(root, old), _owner_dir(root, new)
        with _LOCK:
            if not src.is_dir():
                return 0
            dst.mkdir(parents=True, exist_ok=True)
            for _at, handle, _meta in _metas(src):
                # The text first: a result is there only once its .json is.
                os.replace(src / (handle + ".txt"), dst / (handle + ".txt"))
                os.replace(src / (handle + ".json"), dst / (handle + ".json"))
                moved += 1
            shutil.rmtree(src, ignore_errors=True)
    except Exception:  # noqa: BLE001 — the first reply's results then end with the reply
        log.warning("kept results did not follow their chat", exc_info=True)
    return moved


def follow_resume(engine: Any, run_id: str, new_owner: str) -> int:
    """An agent run is CONTINUED after it was cut off: the continuation is a
    new session that starts from the old run's saved steps, lines with
    handles included, so what the old run kept becomes the continuation's
    (the old session is over; one job, one owner). BLOCKING (one row read,
    then :func:`rekey`). Returns how many results moved. Never raises."""
    try:
        from ..core.db import session_scope
        from ..core.models import AgentRun

        with session_scope(engine) as db:
            row = db.get(AgentRun, str(run_id or ""))
            old = str(getattr(row, "session_id", "") or "")
        return rekey(engine, old, str(new_owner or "")) if old else 0
    except Exception:  # noqa: BLE001 — the continuation then reads its sources again
        return 0


def sweep(home: Any, *, boot: bool = False) -> int:
    """The tidy-up: remove every kept result older than :data:`KEEP_DAYS`
    days, every half-written file, the oldest results while the whole store
    is over :data:`MAX_STORE_BYTES`, every folder left empty and, at start-up
    (``boot``), every plain chat's folder (a reply that ended removed its
    own: what is left is from one that never ended). Returns how many results
    were removed. Never raises."""
    removed = 0
    try:
        root = Path(home) / FOLDER
        if not root.is_dir():
            return 0
        limit = _now() - KEEP_DAYS * 86400.0
        with _LOCK:
            for folder in list(root.iterdir()):
                if not folder.is_dir():
                    continue
                if boot and folder.name.startswith("reply-"):
                    removed += len(_metas(folder))
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                live = set()
                for kept_at, handle, _meta in _metas(folder):
                    if kept_at < limit:
                        _remove(folder, handle)
                        removed += 1
                    else:
                        live.add(handle)
                for name in os.listdir(folder):
                    if name.split(".", 1)[0] not in live or name.endswith(".tmp"):
                        try:
                            (folder / name).unlink()
                        except OSError:
                            pass
            # ...and the whole store is held under its bound (walked afresh).
            _TOTALS.pop(str(root), None)
            removed += _make_room(root)
            for folder in list(root.iterdir()):
                if folder.is_dir() and not _metas(folder):
                    shutil.rmtree(folder, ignore_errors=True)
    except Exception:  # noqa: BLE001 — a tidy-up never stops the daemon
        log.warning("the kept-results tidy-up failed", exc_info=True)
    return removed


# --------------------------------------------------------------------------- #
# One reply: what a plain chat kept goes when its reply ends
# --------------------------------------------------------------------------- #


@dataclass
class _Reply:
    """What one chat reply kept, for the moment it ends."""

    kept: list[tuple[Path, Any, str, str]] = field(default_factory=list)  # (root, ctx, owner, handle)


_REPLY: "ContextVar[_Reply | None]" = ContextVar("iron_jarvis_kept_reply", default=None)
#: Removals in flight (held so they are not collected mid-way).
_TASKS: "set[asyncio.Task[Any]]" = set()


def _forget_kept(kept: Iterable[tuple[Path, Any, str, str]]) -> None:
    """BLOCKING: remove these kept results (``(root, ctx, owner, handle)``),
    each under the name it was saved under AND under the name its turn has
    now: a new chat may have been saved, and its results moved, meanwhile.
    The name is read UNDER the store's lock: :func:`rekey` moves a result
    only under that lock and only after the name changed, so one of the two
    names is where the result is. Never raises."""
    with _LOCK:
        for root, ctx, owner, handle in kept:
            for who in {str(owner or ""), _owner(ctx)}:
                if who:
                    _forget(root, who, [handle])


def _later(work: Callable[[], Any]) -> "asyncio.Task[Any] | None":
    """Run BLOCKING *work* off the event loop WITHOUT waiting for it: its own
    task, held in :data:`_TASKS` (so :func:`removals_done` waits for it and it
    is not collected mid-way). A turn that is ending, cancelled or past its
    bound hands its tidy-up here: it is never made to wait for the disk.
    With no loop running in this thread the work is done now (None)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        work()
        return None
    task = loop.create_task(asyncio.to_thread(work))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _end_reply(reply: "_Reply | None") -> None:
    """A reply ended (finished, stopped or failed): remove what it kept, off
    the event loop, without waiting (this runs in a ``finally``, where a
    cancelled turn must not be made to wait)."""
    if reply is None or not reply.kept:
        return
    kept, reply.kept = list(reply.kept), []
    _later(lambda: _forget_kept(kept))


async def removals_done() -> None:
    """Wait for every removal handed to :func:`_later`: a reply that ended,
    a round's own tidy-up (the daemon's shutdown, bounded; tests).

    Through ``core/tasks.drain_tasks``: a ``while tasks: await gather(...)``
    loop spins forever once every task has finished and its done callback
    has not yet taken it out of the set (docs/rules/daemon.md, v1.388.2)."""
    from ..core.tasks import drain_tasks

    await drain_tasks(_TASKS)


@contextmanager
def reply_scope() -> Iterator[None]:
    """One chat reply (the POST lane): what a plain chat keeps inside it is
    removed when it ends."""
    reply = _Reply()
    token = _REPLY.set(reply)
    try:
        yield
    finally:
        try:
            _REPLY.reset(token)
        except (ValueError, LookupError):  # a token from another context
            _REPLY.set(None)
        _end_reply(reply)


async def reply_frames(frames: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """*frames* (the stream lane's turn) as one reply: the scope is set when
    the first frame is asked for, in the task that iterates the turn, and what
    a plain chat kept is removed when the stream ends however it ends (a
    reply the daemon carries on after the page left ends when IT ends).
    Closing this closes *frames*."""
    reply = _Reply()
    token = _REPLY.set(reply)
    try:
        async for frame in frames:
            yield frame
    finally:
        try:
            close = getattr(frames, "aclose", None)
            if close is not None:
                await close()
        finally:
            try:
                _REPLY.reset(token)
            except (ValueError, LookupError):
                _REPLY.set(None)
            _end_reply(reply)


# --------------------------------------------------------------------------- #
# The lanes' two steps
# --------------------------------------------------------------------------- #


@dataclass
class _Held:
    old: str
    text: str
    cap: int
    tail: bool
    cut: Callable[[Any, int], str]
    content: Any
    #: The original call said its result is the session's own source.
    own_source: bool = False
    #: A tool that cut its own answer: the answer as it gave it, and the
    #: whole of it (``ToolResult.whole``). None for every other result.
    shown: "str | None" = None
    whole: "str | None" = None


#: The most results one round may put by.
_WAITING_MOST = 32


def _owner(ctx: Any) -> str:
    from .registry import values_key

    try:
        return str(values_key(ctx) or "")
    except Exception:  # noqa: BLE001 — no owner: nothing is kept
        return ""


def waiting(ctx: Any) -> list:
    """What :func:`hold` put by on this turn's context and :func:`settle` has
    not taken yet. It lives ON the context and nowhere else, so it is gone
    when the round is settled and, at the latest, when the turn's context is."""
    got = getattr(ctx, "_kept_waiting", None)
    return got if isinstance(got, list) else []


def hold(ctx: Any, content: Any, cap: int, *, cut: Callable[[Any, int], str],
         tail: bool = True, result: Any = None) -> str:
    """The lane's cut of one tool result (``cut(content, cap)``: its own
    function, so what comes back is what always came back), and a result over
    *cap* is remembered for :func:`settle`. ``tail``: that cut keeps the end
    too. ``result``: the call's own result: whether it is the session's own
    source is read from it HERE (never later, never from the text), and when
    its tool cut its own answer and handed the whole beside it
    (``ToolResult.whole``), the whole is remembered too, whatever the
    answer's length. Writes nothing; never raises past the lane's own cut."""
    old = cut(content, cap)
    try:
        text = str(content)
        whole = getattr(result, "whole", None)
        shown = getattr(result, "output", None)
        if not (isinstance(whole, str) and whole and isinstance(shown, str)):
            whole = shown = None
        if whole is None and len(text) <= cap:
            return old
        owner = _owner(ctx)
        if not owner:
            return old
        size, most = (len(whole), MAX_WHOLE_CHARS) if whole is not None else (
            len(split_fence(text)[1]), MAX_RESULT_CHARS)
        if size > most:
            said = _TOO_BIG.format(n=f"{size:,}", most=f"{most:,}")
            return cut(content, cap - len(said)) + said
        from .builtins import is_own_source

        put_by = waiting(ctx)
        if not put_by:
            ctx._kept_waiting = put_by = []
        if len(put_by) < _WAITING_MOST:
            put_by.append(_Held(
                old=old, text=text, cap=int(cap), tail=bool(tail), cut=cut, content=content,
                own_source=bool(is_own_source(result)), shown=shown, whole=whole,
            ))
    except Exception:  # noqa: BLE001 — the old cut stands
        log.debug("a long result was not put by", exc_info=True)
    return old


def _with_whole(content: str, shown: str, whole: str) -> "str | None":
    """*content* (a lane's fenced tool message) with the tool's WHOLE answer
    in place of the answer it cut itself, or None when that cannot be done
    safely: the message is not exactly the lane's fence round that answer
    (the lane withheld it, or reworded it), or the whole is flagged by the
    injection scan the lane ran on the answer. BLOCKING on a long text (two
    scans): call it off the event loop."""
    from ..computeruse.safety import defang_fence_markers, detect_injection

    before, body, after = split_fence(content)
    if before != _FENCE[0] or body != defang_fence_markers(shown):
        return None
    if detect_injection(whole)["flagged"]:
        return None
    return before + defang_fence_markers(whole) + after


def _may_read(platform: Any, overrides: Any = None) -> bool:
    """May ``kept_result_read`` join at all: it is registered, and neither the
    user's permission table nor a rule of this turn denies or asks for it."""
    registry = getattr(platform, "registry", None)
    tool = registry.get(READER) if registry is not None else None
    if tool is None:
        return False
    if str((overrides or {}).get(READER) or "") in ("deny", "ask"):
        return False
    perms = getattr(platform, "permissions", None)
    if perms is not None and getattr(perms.mode_for(READER), "value", "deny") != "allow":
        return False
    return True


async def settle(platform: Any, ctx: Any, msgs: list, arm: Callable[..., bool]) -> int:
    """The end of a tool round: keep what :func:`hold` remembered and rewrite
    those tool messages (see the module docstring). ``arm(dry=True)`` answers
    whether the reader may join this turn and changes nothing; ``arm()`` puts
    it on the turn's roster, in place. Nothing is written before the first
    says yes, and the second is called only after a save succeeded. Returns
    how many results were kept. Never raises, except to pass a cancel on.

    WHAT IT NEVER LEAVES BEHIND. Every handle is noted BEFORE its save is
    awaited, with the name it is saved under (read again for each save: a
    new chat may be linked, and its results moved, while a save is in its
    thread). Whatever ends the round (a cancel, a save that does not come
    back, the reader refused, a result that is not where its reader will
    look), each handle no message names is removed under the name it was
    saved under AND the name the turn has by then (:func:`_forget_kept`).
    Every wait here is bounded by :data:`IO_TIMEOUT_S`; a round that was
    cancelled, or whose save did not come back, does not wait for that
    tidy-up at all (it needs the lock the stalled save holds): it is handed
    to :func:`_later`."""
    put_by = list(waiting(ctx))
    if not put_by:
        return 0
    ctx._kept_waiting = []  # taken: nothing of the round stays put by
    saved: list[tuple[str, str]] = []  # (the name it was saved under, handle)
    told: set[str] = set()
    root = None
    hurry = False  # cancelled, or a save did not come back: no wait for the tidy-up
    try:
        from ..providers import egress as _egress

        scope = _egress.current_scope()
        if scope.on and scope.allowed_once:
            # A cloud model reads this one message: a kept result could not
            # be read back by it, so nothing is kept (the old cut's advice
            # is the one that works).
            return 0
        plain = str(getattr(ctx, "session_id", "") or "") == "chat"
        reply = _REPLY.get()
        if plain and reply is None:
            return 0  # no reply to end with: nothing a plain chat keeps may outlive it
        root = root_of(getattr(ctx, "engine", None) or getattr(platform, "engine", None))
        if root is None or not _owner(ctx):
            return 0
        local_only = bool(scope.on)
        where = _where(ctx)
        used: set[int] = set()
        plans: list[tuple[Any, str, str, str]] = []
        may: "bool | None" = None
        for item in put_by:
            target = next(
                (m for m in reversed(msgs)
                 if getattr(m, "role", "") == "tool" and id(m) not in used
                 and str(getattr(m, "content", "") or "").startswith(item.old)),
                None,
            )
            if target is None:
                continue
            under = str(target.content)[len(item.old):]
            text = item.text
            if item.whole is not None:
                # A document reader cut its own answer: the whole takes the
                # answer's place (when it is clean). Where it fits this
                # lane's own limit it is simply shown; else it is shown and
                # kept like any other long result.
                full = await asyncio.wait_for(
                    asyncio.to_thread(_with_whole, item.text, str(item.shown), item.whole),
                    IO_TIMEOUT_S,
                )
                if full is None:
                    continue
                if len(full) <= item.cap:
                    used.add(id(target))
                    target.content = full + under
                    continue
                text = full
            # (Every handle is the same length, so whether the note leaves
            # enough to show is known before anything is written.)
            if kept_form(text, item.cap, _NO_HANDLE, where, tail=item.tail) is None:
                continue
            if may is None:
                may = bool(arm(dry=True))
            if not may:
                continue
            name = _owner(ctx)  # (now: not the name the round started under)
            handle = new_handle()
            earlier = tuple(h for _name, h in saved)
            saved.append((name, handle))  # BEFORE the wait: known however the wait ends
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        put, root, name, split_fence(text)[1], own_source=item.own_source,
                        local_only=local_only, handle=handle, keep=earlier,
                    ),
                    IO_TIMEOUT_S,
                )
            except NoRoom:
                said = _NO_ROOM.format(where=where, most=MAX_PER_OWNER)
                used.add(id(target))
                target.content = item.cut(item.content, item.cap - len(said)) + said + under
                continue
            except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError):
                # Nobody waits for this save any more (it did not come back,
                # or the turn was cancelled while it was in its thread): it
                # writes nothing, or removes what it wrote when it finishes.
                abandon(handle)
                raise
            used.add(id(target))
            plans.append((target, item.old, handle,
                          str(kept_form(text, item.cap, handle, where, tail=item.tail))))
        if not plans:
            return 0
        # A note never points at a result that is gone, or that is not where
        # its reader will look: the name the turn has NOW (a save that went
        # under the turn's name just after its chat was linked is under a
        # name nothing reads any more; it gets no note and is removed).
        now = _owner(ctx)
        there = await asyncio.wait_for(
            asyncio.to_thread(alive, root, now, [h for _name, h in saved]), IO_TIMEOUT_S)
        plans = [plan for plan in plans if plan[2] in there]
        if not plans or not arm():
            return 0
        for target, old, handle, form in plans:
            target.content = form + str(target.content)[len(old):]
            target.kept_line = _trimmed_note(form.rsplit("\n", 1)[1])
            told.add(handle)
            if plain and reply is not None:
                reply.kept.append((root, ctx, now, handle))
        return len(plans)
    except asyncio.CancelledError:
        hurry = True
        raise
    except Exception as exc:  # noqa: BLE001 — the round's messages stay the old cut
        # (The kind of failure only: its own words can name a file's path.)
        log.warning("a long result could not be kept (%s); it was cut as before", type(exc).__name__)
        hurry = isinstance(exc, TimeoutError)  # (a wait that did not come back: the lock is held)
        return 0
    finally:
        left = [(root, ctx, name, h) for name, h in saved if h not in told]
        if left:
            # Saved (or on its way), but no message names it: it is not left
            # on disk. Off the loop, in its own task (held in _TASKS); the
            # round waits for it, bounded, only when nothing says the store's
            # lock is held. Past the bound it goes on by itself.
            tidy = _later(lambda: _forget_kept(left))
            if tidy is not None and not hurry:
                try:
                    await asyncio.wait_for(asyncio.shield(tidy), IO_TIMEOUT_S)
                except Exception:  # noqa: BLE001 — it goes on in its own task
                    pass


async def arm_at_start(platform: Any, owner: str, msgs: list, arm: Callable[..., bool]) -> int:
    """A turn that STARTS with a kept-result line in its messages (an agent
    run or a coding reply picked up after a restart reads its saved rounds
    back, lines included) has the reader from its first step. Each line's
    handle is checked against the store for THIS owner (off the event loop):
    only a line the store answers for arms the reader and is put on its
    message for a later trim. Returns how many lines were real. Never
    raises."""
    try:
        noted = [
            (m, got.group(0), got.group(3))
            for m in msgs or []
            if getattr(m, "role", "") == "tool"
            for got in [NOTE_RX.fullmatch(note_of(getattr(m, "content", "")))]
            if got is not None
        ]
        root = root_of(getattr(platform, "engine", None))
        if not noted or root is None or not owner:
            return 0
        there = await asyncio.wait_for(
            asyncio.to_thread(alive, root, str(owner), [handle for _m, _note, handle in noted]),
            IO_TIMEOUT_S,
        )
        real = [(m, note) for m, note, handle in noted if handle in there]
        if not real or not arm(dry=True) or not arm():
            return 0
        for m, note in real:
            m.kept_line = _trimmed_note(note)
        return len(real)
    except Exception:  # noqa: BLE001 — not armed: a call of the reader is refused by the roster
        return 0


def arm_chat(
    d: Any, *, armed: list, auto_armed: list, tool_specs: list, overrides: dict, armed_grant: set,
    ask_armed: Any = (), tool_ceiling: Any = None, ceiling: "int | None" = None,
    max_tools: int = 6, harness: Any = None, dry: bool = False,
) -> bool:
    """``kept_result_read`` joins a chat turn (plain chat or a coding session)
    for its next round: armed and granted the way the arming pass arms a tool
    that only reads, changed IN PLACE because the round loop reads these same
    lists. True when it is on the roster (already, or now). ``dry``: only
    answer whether it may join; change nothing.

    Never: over the user's deny or a rule of this turn, outside a surface's
    own ceiling (``tool_ceiling``), or past a small model's tool budget
    (``ceiling``): the same three refusals as ``chat_turn.offer_script_tool``.
    """
    if READER in armed:
        return True
    platform = d.platform
    if READER in (ask_armed or ()) or not _may_read(platform, overrides):
        return False
    if tool_ceiling is not None and READER not in tool_ceiling:
        return False
    if ceiling is not None and ceiling < max_tools and len(armed) >= ceiling:
        return False
    if dry:
        return True
    # (The spec is read before anything is changed: a failure here leaves the
    # turn's lists exactly as they were.)
    specs = platform.registry.specs([READER])
    armed.append(READER)
    auto_armed.append(READER)
    overrides[READER] = "allow"
    armed_grant.add(READER)
    plan = getattr(harness, "plan", None) if harness is not None else None
    if plan is not None:
        # A coding session shows its tools through its own plan: the reader
        # joins the names it shows as they are, so the session's own list of
        # what the model may call has it and a later rebuild of the specs
        # keeps it. (The lane's ``armed`` above is the registry's gate.)
        if READER not in plan.passthrough:
            plan.passthrough.append(READER)
    tool_specs.extend(specs)
    return True


def arm_run(platform: Any, tool_specs: list, overrides: Any = None, *, dry: bool = False) -> bool:
    """``kept_result_read`` joins an agent run's tool list, in place (the
    runtime's roster is the names in this list). True when it is there.
    ``dry``: only answer whether it may join."""
    try:
        if any(isinstance(s, dict) and s.get("name") == READER for s in tool_specs):
            return True
        if not _may_read(platform, overrides):
            return False
        if not dry:
            tool_specs.extend(platform.registry.specs([READER]))
        return True
    except Exception:  # noqa: BLE001 — not armed: the old cut
        return False


# --------------------------------------------------------------------------- #
# The one reader
# --------------------------------------------------------------------------- #


def _yes(value: Any) -> bool:
    """True for ``true`` as a model may send it: the boolean, or the words
    "true", "1", "yes"."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return value is True


def _number(value: Any) -> int:
    """A character position as a model may send it (17611, "17611",
    "17,611"); 0 for anything that is not one."""
    if isinstance(value, bool):
        return 0
    try:
        return int(str(value).replace(",", "").strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


class KeptResultReadTool(Tool):
    """Read a part of a result Iron Jarvis kept for the chat it is asked in."""

    name = READER
    description = (
        "Read another part of a long result that Iron Jarvis shortened and kept. Use the handle "
        "from the line under the shortened result. start is the character to read from, "
        "counted from 1 (a negative number counts back from the end); end=true reads the last "
        f"part. One call returns at most {READ_CHARS:,} characters and says which characters they "
        "are and where the next part starts. It reads only a result kept in this same chat, run "
        "or coding session, and changes nothing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "handle": {"type": "string", "description": 'The handle in the line under the result, like "kr-…".'},
            "start": {"type": "integer", "description": "The character to read from, counted from 1."},
            "end": {"type": "boolean", "description": "true: read the last part instead."},
        },
        "required": ["handle"],
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    # A part of a result is that result: fenced and scanned like the whole.
    returns_untrusted_content = True

    def __init__(self, platform: Any = None) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        owner = _owner(ctx)
        where = _where(ctx)
        root = root_of(getattr(ctx, "engine", None))
        raw_handle = args.get("handle")
        handle = raw_handle.strip() if isinstance(raw_handle, str) else ""
        got = None
        if root is not None and owner:
            try:
                got = await asyncio.wait_for(asyncio.to_thread(get, root, owner, handle), IO_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — unreadable is "not there"
                got = None
        if got is None:
            return ToolResult(ok=False, error=not_found(where))
        meta, text = got
        if meta.get("local_only"):
            from ..providers import egress as _egress

            scope = _egress.current_scope()
            if not scope.on:
                return ToolResult(ok=False, error=_LOCAL_OFF.format(where=where))
            if scope.allowed_once:
                name = _egress.friendly_name(
                    scope.allowed_once, getattr(self.platform, "providers", None))
                return ToolResult(ok=False, error=_LOCAL_ONCE.format(
                    where=where, name=name[:1].upper() + name[1:]))
        n = len(text)
        if _yes(args.get("end")):
            first = max(0, n - READ_CHARS)
        else:
            start = 1 if args.get("start") is None else _number(args.get("start"))
            first = n + start if start < 0 else start - 1
            if start == 0 or first < 0 or first >= n:
                return ToolResult(ok=False, error=(
                    f"{READER}: this kept result has {n:,} characters, so start is a number from 1 "
                    f"to {n:,} (or a negative number counting back from the end)."
                ))
        last = min(n, first + READ_CHARS)
        lead = _LEAD.format(
            a=f"{first + 1:,}", b=f"{last:,}", n=f"{n:,}",
            then="this is the end" if last >= n else f"the next part starts at {last + 1:,}",
        )
        data: dict[str, Any] = {"handle": handle, "from": first + 1, "to": last, "of": n,
                                "end": last >= n}
        if meta.get("own_source") is True:
            # The ORIGINAL call said this result is the session's own source
            # (decided there, saved with the text): so is a part of it. Every
            # other part is outside text, whatever it looks like.
            from .builtins import OWN_SOURCE_KEY

            data[OWN_SOURCE_KEY] = True
        return ToolResult(ok=True, output=lead + defang(text[first:last]), data=data)


def kept_result_tools(platform: Any = None) -> list[Tool]:
    return [KeptResultReadTool(platform)]


__all__ = [
    "FOLDER", "KEEP_DAYS", "KeptResultReadTool", "MAX_OWNER_CHARS", "MAX_PER_OWNER",
    "MAX_RESULT_CHARS", "MAX_STORE_BYTES", "MAX_WHOLE_CHARS", "NOTE_RX", "NoRoom", "READER",
    "READ_CHARS", "abandon", "alive", "arm_at_start", "arm_chat", "arm_run", "defang",
    "drop_owners", "hold", "kept_form", "kept_result_tools", "new_handle", "not_found",
    "note_of", "rekey", "removals_done", "reply_frames", "reply_scope", "settle", "split_fence",
    "sweep", "waiting",
]
