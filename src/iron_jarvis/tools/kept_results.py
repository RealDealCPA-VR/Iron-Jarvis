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
   remembers the whole text until the round ends. It writes nothing.
2. :func:`settle`, once per round, saves each remembered result (off the
   event loop, bounded), puts ``kept_result_read`` on the turn's roster
   through the lane's own arming (``arm``), and only then rewrites the tool
   message: the start (and, where the lane's cut kept it, the end), and ONE
   line of Iron Jarvis's own words under the result with the handle. A save
   that fails, a reader that may not join (the user's deny, a surface's own
   tool list, a small model's tool budget), a result too big to keep: the
   message stays the lane's old cut.

WHOSE IT IS. A kept result is filed under ``tools/registry.values_key``, the
key release 1 gave a chat's stored values: the saved chat (``chat:<id>``),
else the turn (a new chat's first message; :func:`rekey` moves it when the
chat is saved), a coding session's ``build:<id>``, an agent run's session id.
The folder on disk is named by a hash of that key and the reader looks ONLY
in the caller's own folder, so another chat, another run, a helper, a guessed
or copied handle and a call with no owner all get the same answer
(:data:`NOT_FOUND`) and learn nothing, not even that the handle exists.

LOCAL ONLY. A result kept while the chat was Local only is marked so, and is
handed back only to work that itself stays on this network (the scope is on
and no cloud model was allowed for the message:
``providers.egress.may_recall_local_only_chats``, the rule recall uses for a
Local only chat's text). Switching the chat off Local only later does not
open it.

AT REST. ``<home>/kept-results/<hash of the owner>/<handle>.txt`` (+ a small
``.json`` beside it: size, when, Local only, fenced). On this PC only; left
out of every backup (``maintenance.create_backup``) and in no export; removed
with its chat or run (:func:`drop_owners`, called by the delete paths), after
:data:`KEEP_DAYS` days by the daily tidy-up (:func:`sweep`, from
``daemon/app._event_prune_loop``), and when a chat holds more than
:data:`MAX_PER_OWNER` results or :data:`MAX_OWNER_CHARS` characters (the
oldest goes). The text is what the model was being shown, no rawer: the
result after the lane's untrusted-data fence took out any fence marker.
Never written to a log line, an event, a usage row or an error message.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

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
#: The most results one chat, run or coding session holds, and the most
#: characters in all: the script shelf's own bounds for one chat's kept values
#: (``script_values.MAX_VALUES_PER_KEY`` / ``MAX_VALUE_CHARS``). Over either,
#: the oldest goes.
MAX_PER_OWNER = 16
MAX_OWNER_CHARS = 8_000_000
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
_NOTE = (
    NOTE_LEAD + "{where}: {n} characters. Shown above: {shown}. To read the rest, call "
    + READER + ' with handle "{handle}" and start (the character to read from); '
    "end=true reads the last part.]"
)
#: The whole line, as a pattern (``context/agent_window`` keeps it when it
#: trims an old tool result to a marker).
NOTE_RX = re.compile(
    re.escape(NOTE_LEAD) + r"(?:chat|coding session|run): [0-9,]+ characters\. Shown above: "
    r"characters 1 to [0-9,]+(?: and [0-9,]+ to [0-9,]+)?\. To read the rest, call "
    + READER + r' with handle "kr-[0-9a-f]{16}" and start \(the character to read from\); '
    r"end=true reads the last part\.\]"
)
#: Where the middle was left out (inside the result, so inside its fence).
_GAP = "\n[… characters {a} to {b} are not shown here: see the note under this result …]\n"
#: A result over :data:`MAX_RESULT_CHARS`: the lane's cut, and this said.
_TOO_BIG = (
    "\n[Iron Jarvis did not keep the rest of this result: it has {n} characters, and a "
    "result is kept only up to {most}.]"
)

#: What every call gets that names no kept result of its own chat.
NOT_FOUND = (
    READER + ": there is no kept result with that handle in this chat. A kept result can be read "
    "only in the chat, run or coding session it was kept in, for " + str(KEEP_DAYS) + " days."
)
_LOCAL_OFF = (
    READER + ": this result was kept while the chat was Local only, so Iron Jarvis gives it only "
    "to a model on your own network. This chat is not Local only now, so it was not read. Turn "
    "Local only back on for this chat to read it."
)
_LOCAL_ONCE = (
    READER + ": this result was kept while the chat was Local only, so Iron Jarvis gives it only "
    "to a model on your own network. {name} runs off your network and was allowed for this one "
    "message, so the kept result was not read. Ask again on a model on your own network."
)


def _now() -> float:
    """The clock a kept result's age is read from. A seam: a test says what
    time it is, so "thirty days later" never waits."""
    return time.time()


# --------------------------------------------------------------------------- #
# The words (pure text: no disk, no clock)
# --------------------------------------------------------------------------- #


def defang(text: str) -> str:
    """*text* with any copy of the note's opening made unlike it (``[`` to
    ``(``, the fence's own rule: nothing is hidden, it only stops reading as
    Iron Jarvis's line). Same length."""
    return text.replace(NOTE_LEAD, "(" + NOTE_LEAD[1:])


#: The fence's opening and closing lines, read once.
_FENCE: list[str] = []


def split_fence(text: str) -> tuple[str, str, str]:
    """``(before, result, after)``: the lane's untrusted-data fence around a
    result, when it has one (``computeruse.safety.wrap_untrusted``, read from
    that function so its words can change). ``after`` starts at the fence's
    closing lines and holds everything the lane added under it. A result with
    no fence is ``("", text, "")``."""
    if not _FENCE:
        from ..computeruse.safety import wrap_untrusted

        _FENCE.extend(wrap_untrusted("\x00").split("\x00", 1))
    before, closing = _FENCE
    if before and closing and text.startswith(before):
        end = text.find(closing, len(before))
        if end >= 0:
            return before, text[len(before):end], text[end:]
    return "", text, ""


def note_of(content: Any) -> str:
    """The kept-result line of a tool message, or "". Only a line UNDER the
    result counts: after the fence when the message has one, so a line a
    fenced result carries inside itself is never taken for Iron Jarvis's."""
    text = str(content or "")
    _before, _body, after = split_fence(text)
    found = NOTE_RX.findall(after if after else text)
    return found[-1] if found else ""


def _keep_note_on_trim() -> None:
    """A tool result the context planner trims to a marker keeps its
    kept-result line (``context.agent_window.KEEP_ON_TRIM``)."""
    from ..context import agent_window

    if note_of not in agent_window.KEEP_ON_TRIM:
        agent_window.KEEP_ON_TRIM.append(note_of)


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
#: Owners whose kept results cannot be reached after the message that kept
#: them (a call that named no chat and no turn): their folders are removed at
#: the next start.
_ONCE_PREFIXES = ("chat:call-", "chat:once-")


def root_of(engine: Any) -> "Path | None":
    """Where this app keeps results: ``<home>/kept-results``; None for a
    database in memory (nothing is kept then: the lane's old cut)."""
    from .registry import _home_of

    home = _home_of(engine)
    return (home / FOLDER) if home is not None else None


def _owner_dir(root: Path, owner: str) -> Path:
    name = hashlib.sha256(owner.encode("utf-8", "surrogatepass")).hexdigest()[:40]
    return root / (("once-" if owner.startswith(_ONCE_PREFIXES) else "") + name)


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
    for suffix in (".json", ".txt"):
        try:
            (folder / (handle + suffix)).unlink()
        except OSError:
            pass


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    # newline="" and surrogatepass: the text comes back character for
    # character (a "\r\n" stays two characters; a lone surrogate is kept).
    with open(tmp, "w", encoding="utf-8", errors="surrogatepass", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def put(root: Path, owner: str, text: str, *, fenced: bool, local_only: bool) -> str:
    """Keep *text* for *owner*; returns its handle. Over the owner's bounds
    the oldest results go. Raises on any failure (the caller then cuts as it
    always did)."""
    if not owner or len(text) > MAX_RESULT_CHARS:
        raise ValueError("not kept")
    handle = "kr-" + secrets.token_hex(8)
    folder = _owner_dir(root, owner)
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        _write(folder / (handle + ".txt"), text)
        _write(folder / (handle + ".json"), json.dumps({
            "v": 1, "chars": len(text), "created": _now(),
            "local_only": bool(local_only), "fenced": bool(fenced),
        }))
        rows = _metas(folder)
        total = sum(int(meta.get("chars") or 0) for _at, _h, meta in rows)
        while rows and (len(rows) > MAX_PER_OWNER or total > MAX_OWNER_CHARS):
            _at, oldest, meta = rows[0]
            if oldest == handle:
                break
            rows.pop(0)
            total -= int(meta.get("chars") or 0)
            _remove(folder, oldest)
    return handle


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


def forget(root: Path, owner: str, handles: Iterable[str]) -> None:
    """Remove these kept results of *owner* (a save whose note never reached
    the model). Never raises."""
    folder = _owner_dir(root, owner)
    with _LOCK:
        for handle in handles:
            if _HANDLE_RX.match(str(handle)):
                _remove(folder, str(handle))


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
    session was deleted. Returns how many folders went. Never raises."""
    gone = 0
    try:
        root = root_of(engine)
        if root is None:
            return 0
        with _LOCK:
            for owner in owners:
                folder = _owner_dir(root, str(owner or ""))
                if owner and folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
                    gone += 1
    except Exception:  # noqa: BLE001 — a delete always completes
        log.warning("kept results of a deleted chat could not be removed", exc_info=True)
    return gone


def rekey(engine: Any, old: str, new: str) -> int:
    """A new chat was saved: what its first message kept (filed under the
    turn) becomes the chat's. Returns how many results moved. Never raises."""
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
    except Exception:  # noqa: BLE001 — the first message's results then end with their 30 days
        log.warning("kept results did not follow their chat", exc_info=True)
    return moved


def sweep(home: Any, *, boot: bool = False) -> int:
    """The tidy-up: remove every kept result older than :data:`KEEP_DAYS`
    days, every half-written file, every folder left empty and, at start-up
    (``boot``), the folders no later message can reach. Returns how many
    results were removed. Never raises."""
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
                if boot and folder.name.startswith("once-"):
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
                    if name.rsplit(".", 1)[0] not in live or name.endswith(".tmp"):
                        try:
                            (folder / name).unlink()
                        except OSError:
                            pass
                if not live:
                    shutil.rmtree(folder, ignore_errors=True)
    except Exception:  # noqa: BLE001 — a tidy-up never stops the daemon
        log.warning("the kept-results tidy-up failed", exc_info=True)
    return removed


# --------------------------------------------------------------------------- #
# The lanes' two steps
# --------------------------------------------------------------------------- #


@dataclass
class _Held:
    old: str
    text: str
    cap: int
    tail: bool


#: Results over the limit, waiting for their round's :func:`settle`: keyed by
#: the turn's own context and its owner. In memory only, and bounded.
_PENDING: dict[tuple[str, str], list[_Held]] = {}
_PENDING_KEYS = 32
_PENDING_EACH = 32


def _owner(ctx: Any) -> str:
    from .registry import values_key

    try:
        return str(values_key(ctx) or "")
    except Exception:  # noqa: BLE001 — no owner: nothing is kept
        return ""


def hold(ctx: Any, content: Any, cap: int, *, cut: Callable[[Any, int], str],
         tail: bool = True) -> str:
    """The lane's cut of one tool result (``cut(content, cap)``: its own
    function, so what comes back is what always came back), and a result over
    *cap* is remembered for :func:`settle`. ``tail``: that cut keeps the end
    too. Writes nothing; never raises past the lane's own cut."""
    old = cut(content, cap)
    try:
        text = str(content)
        if len(text) <= cap:
            return old
        owner = _owner(ctx)
        if not owner:
            return old
        if len(split_fence(text)[1]) > MAX_RESULT_CHARS:
            said = _TOO_BIG.format(n=f"{len(split_fence(text)[1]):,}", most=f"{MAX_RESULT_CHARS:,}")
            return cut(content, cap - len(said)) + said
        key = (str(getattr(ctx, "own_id", "") or ""), owner)
        if key not in _PENDING and len(_PENDING) >= _PENDING_KEYS:
            _PENDING.pop(next(iter(_PENDING)))
        waiting = _PENDING.setdefault(key, [])
        if len(waiting) < _PENDING_EACH:
            waiting.append(_Held(old=old, text=text, cap=int(cap), tail=bool(tail)))
    except Exception:  # noqa: BLE001 — the old cut stands
        log.debug("a long result was not put by", exc_info=True)
    return old


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


async def settle(platform: Any, ctx: Any, msgs: list, arm: Callable[[], bool]) -> int:
    """The end of a tool round: keep what :func:`hold` remembered and rewrite
    those tool messages (see the module docstring). ``arm()`` puts the reader
    on the turn's roster, in place, and answers whether it is there; it is
    called only after a save succeeded, and when it says no the saves are
    undone. Returns how many results were kept. Never raises."""
    owner = _owner(ctx)
    waiting = _PENDING.pop((str(getattr(ctx, "own_id", "") or ""), owner), None)
    if not waiting:
        return 0
    saved: list[str] = []
    told = False
    root = None
    try:
        root = root_of(getattr(ctx, "engine", None) or getattr(platform, "engine", None))
        if root is None:
            return 0
        from ..providers import egress as _egress

        local_only = bool(_egress.current_scope().on)
        where = _where(ctx)
        used: set[int] = set()
        plans: list[tuple[Any, str, str]] = []
        for item in waiting:
            target = next(
                (m for m in reversed(msgs)
                 if getattr(m, "role", "") == "tool" and id(m) not in used
                 and str(getattr(m, "content", "") or "").startswith(item.old)),
                None,
            )
            # (Every handle is the same length, so whether the note leaves
            # enough to show is known before anything is written.)
            if target is None or kept_form(item.text, item.cap, _NO_HANDLE, where, tail=item.tail) is None:
                continue
            before, body, _after = split_fence(item.text)
            handle = await asyncio.wait_for(
                asyncio.to_thread(put, root, owner, body, fenced=bool(before), local_only=local_only),
                IO_TIMEOUT_S,
            )
            saved.append(handle)
            used.add(id(target))
            plans.append((target, item.old, str(kept_form(item.text, item.cap, handle, where, tail=item.tail))))
        if not plans or not arm():
            return 0
        for target, old, form in plans:
            target.content = form + str(target.content)[len(old):]
        told = True
        return len(plans)
    except Exception:  # noqa: BLE001 — the round's messages stay the old cut
        log.warning("a long result could not be kept; it was cut as before", exc_info=True)
        return 0
    finally:
        if saved and not told and root is not None:
            # Saved, but no message names it: it is not left on disk.
            try:
                await asyncio.to_thread(forget, root, owner, saved)
            except Exception:  # noqa: BLE001
                pass


def arm_chat(
    d: Any, *, armed: list, auto_armed: list, tool_specs: list, overrides: dict, armed_grant: set,
    ask_armed: Any = (), tool_ceiling: Any = None, ceiling: "int | None" = None,
    max_tools: int = 6, harness: Any = None,
) -> bool:
    """``kept_result_read`` joins a chat turn (plain chat or a coding session)
    for its next round: armed and granted the way the arming pass arms a tool
    that only reads, changed IN PLACE because the round loop reads these same
    lists. True when it is on the roster (already, or now).

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


def arm_run(platform: Any, tool_specs: list, overrides: Any = None) -> bool:
    """``kept_result_read`` joins an agent run's tool list, in place (the
    runtime's roster is the names in this list). True when it is there."""
    try:
        if any(isinstance(s, dict) and s.get("name") == READER for s in tool_specs):
            return True
        if not _may_read(platform, overrides):
            return False
        tool_specs.extend(platform.registry.specs([READER]))
        return True
    except Exception:  # noqa: BLE001 — not armed: the old cut
        return False


async def held(platform: Any, owner: str) -> bool:
    """Does *owner* already hold a kept result? An agent run asks as it
    starts: a run picked up after a restart reads its saved transcript, the
    line with the handle is in it, so the reader joins from the first step.
    Off the event loop; never raises."""
    try:
        root = root_of(getattr(platform, "engine", None))
        return bool(await asyncio.wait_for(
            asyncio.to_thread(holds, root, str(owner or "")), IO_TIMEOUT_S))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# The one reader
# --------------------------------------------------------------------------- #


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
        owner = _owner(ctx)
        root = root_of(getattr(ctx, "engine", None))
        handle = str((args or {}).get("handle") or "").strip()
        got = None
        if root is not None and owner:
            try:
                got = await asyncio.wait_for(asyncio.to_thread(get, root, owner, handle), IO_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — unreadable is "not there"
                got = None
        if got is None:
            return ToolResult(ok=False, error=NOT_FOUND)
        meta, text = got
        if meta.get("local_only"):
            from ..providers import egress as _egress

            scope = _egress.current_scope()
            if not scope.on:
                return ToolResult(ok=False, error=_LOCAL_OFF)
            if scope.allowed_once:
                name = _egress.friendly_name(
                    scope.allowed_once, getattr(self.platform, "providers", None))
                return ToolResult(ok=False, error=_LOCAL_ONCE.format(name=name[:1].upper() + name[1:]))
        n = len(text)
        raw = (args or {}).get("start")
        if (args or {}).get("end") is True:
            first = max(0, n - READ_CHARS)
        else:
            try:
                start = 1 if raw is None or isinstance(raw, bool) else int(raw)
            except (TypeError, ValueError):
                start = 0
            first = n + start if start < 0 else start - 1
            if start == 0 or first < 0 or first >= n:
                return ToolResult(ok=False, error=(
                    f"{READER}: this kept result has {n:,} characters, so start is a number from 1 "
                    f"to {n:,} (or a negative number counting back from the end)."
                ))
        last = min(n, first + READ_CHARS)
        where = (
            "this is the end" if last >= n
            else f"the next part starts at {last + 1}"
        )
        lead = f"[Characters {first + 1:,} to {last:,} of {n:,} of the kept result; {where}.]\n"
        data: dict[str, Any] = {"handle": handle, "from": first + 1, "to": last, "of": n,
                                "end": last >= n}
        if not meta.get("fenced"):
            # The whole was not outside text (a coding session's own source,
            # a tool whose words are Iron Jarvis's): neither is a part of it.
            from .builtins import OWN_SOURCE_KEY

            data[OWN_SOURCE_KEY] = True
        return ToolResult(ok=True, output=lead + defang(text[first:last]), data=data)


def kept_result_tools(platform: Any = None) -> list[Tool]:
    return [KeptResultReadTool(platform)]


__all__ = [
    "FOLDER", "KEEP_DAYS", "KeptResultReadTool", "MAX_OWNER_CHARS", "MAX_PER_OWNER",
    "MAX_RESULT_CHARS", "NOTE_RX", "NOT_FOUND", "READER", "READ_CHARS", "arm_chat", "arm_run",
    "defang", "drop_owners", "held", "hold", "kept_form", "kept_result_tools",
    "note_of", "rekey", "settle", "split_fence", "sweep",
]
