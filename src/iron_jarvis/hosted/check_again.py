"""Build › Code's reply is CHECKED AGAIN when its own record says it is not
done (v1.497.0). The check-again loop of Chat › Code (v1.493.0,
``harness/finish``) and of plain chat and agent runs (v1.495.0,
``daemon/until_done``) brought to the Code view.

The owner: "keep working until what it says is true, never claim done when it
isn't, never stop early on 'I can't'".

WHY IT IS SMALLER HERE. In Build › Code the user's REAL coding tool runs its
own tools; Iron Jarvis only draws what it says. So "no tool wrote it" proves
nothing (the tool runs commands that can write anything). Only CERTAIN
evidence counts:

- a file the reply's final words say it SAVED or CREATED
  (``daemon.chat_turn._claimed_write_names``, the same reading the other lanes
  use) that is NOT IN THE SESSION'S FOLDER (:func:`missing_files`): a bounded
  walk that follows no link, never touches a network or device spelling,
  leaves a name alone when any of the reply's own steps mentions it (it may
  have been written somewhere else on purpose) and says nothing when it could
  not look everywhere;
- a reply that ENDS BY GIVING UP (``harness.give_up.gives_up``) after it did
  real work (a finished step), once per user message.

WHEN. Only after a reply the USER started (or this check's own follow-up)
ended CLEANLY: completed, no account-limit card, no card or question open, no
waiting message (the queue goes first), no background work of the tool's own,
not a helper's piece, never while Emergency stop is on, never while Iron
Jarvis is closing.

WHAT HAPPENS. ONE hidden message is sent to the same tool session, the way a
carry-on is sent (``carry_on._start``: ``start_turn(carry=…)``, the ``user``
event carries ``carry`` with ``why: "check"`` and the plain words the page
draws as Iron Jarvis's own line, never as the user's bubble). At most
:data:`MAX_FOLLOW_UPS` per user message (it spends the user's plan); it stops
when the same problems come back (``stuck``), when the reply ends on a
"Blocked:" line, when the user sends a message, presses Stop, archives or
deletes the session. When the limit or ``stuck`` ends it with a problem still
open, ONE notice under the reply says so (:func:`verdict_words`).

Every file name shown (to the tool, on the page) is masked and cut short; no
tool output is quoted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..detections.redact import mask
from .events import clip, make

log = logging.getLogger(__name__)

#: The carry mark's ``why`` (the page's ``decodeCarryMark`` knows it).
WHY = "check"
#: At most this many follow-ups for one user message (Build › Code spends the
#: user's own plan, so fewer than Chat › Code's five).
MAX_FOLLOW_UPS = 2
#: The folder walk's bounds: past either, nothing is said (not certain).
WALK_MAX_ENTRIES = 20_000
WALK_DEADLINE_S = 2.0
#: Folders the walk does not enter. A claimed name whose path names one of
#: them is left alone (the walk could not have seen it).
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})
#: At most this many claimed names are judged.
MAX_NAMES = 8
#: A shown name is cut to this many characters.
NAME_CHARS = 80

END_LIMIT = "limit"
END_STUCK = "stuck"
END_WORDS = {
    END_LIMIT: f"it reached the most tries ({MAX_FOLLOW_UPS})",
    END_STUCK: "the last try changed nothing",
}

NOTE_HEAD = (
    "FROM IRON JARVIS, NOT FROM THE USER. Iron Jarvis checked your last reply against the "
    "folder, and it is not finished:"
)
NOTE_TAIL = (
    "Fix this now with your tools, then answer again. Do not say a file was saved unless it is "
    "really in the folder. If something outside your reach stops you (a sign-in, a file or a "
    "decision only the user has), end with a line that starts \"Blocked:\" saying what you "
    "tried, exactly what stops you and what the user must do."
)
GIVE_UP_WORDS = (
    "Your reply ends by saying this cannot be done. Try another way first with your tools (read "
    "the error again, look at how the folder already does it, take a different approach) before "
    "you stop."
)
VERDICT_CARRY_ON = "Tell it to keep going and it will carry on."

_BLOCKED_LINE_RX = re.compile(
    r"^[^\S\n]*(?:[-*]\s*)?(?:\*\*\s*)?blocked\s*(?:\*\*\s*)?:", re.IGNORECASE | re.MULTILINE,
)


def shown(name: str) -> str:
    """A file name as Iron Jarvis shows it: masked, then cut short."""
    return clip(mask(str(name or "")), NAME_CHARS)


@dataclass
class Chain:
    """One user message and the follow-ups Iron Jarvis sent for it."""

    origin: str
    sent: int = 0
    last_keys: "frozenset[str] | None" = None
    gave_up_fired: bool = False
    #: The turn being judged now (a newer turn makes this one stale).
    pending: str = ""
    ended: str = ""
    open: list[str] = field(default_factory=list)


def chains(svc: Any) -> dict[str, Chain]:
    """The service's chains, by session id (in memory: a restart forgets them)."""
    return svc.__dict__.setdefault("_check_again", {})


def judging(svc: Any) -> "set[asyncio.Future]":
    """The checks still running (a test waits for them to end)."""
    return svc.__dict__.setdefault("_check_again_tasks", set())


# --------------------------------------------------------------------------- #
# Reading the turn
# --------------------------------------------------------------------------- #
def final_text(events: list[dict[str, Any]]) -> str:
    """The reply's last top-level words (a helper's own words never count)."""
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "text_done" and not ev.get("parent"):
            return str(ev.get("text") or "")
    return ""


def worked(events: list[dict[str, Any]]) -> bool:
    """At least one step of the tool's own finished."""
    return any(isinstance(ev, dict) and ev.get("type") == "tool" and ev.get("status") == "done"
               for ev in events)


def _step_text(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "tool":
            for k in ("path", "input", "title", "subtitle", "output"):
                v = ev.get(k)
                if isinstance(v, str) and v:
                    parts.append(v)
    return "\n".join(parts).replace("\\", "/").lower()


def claimed_names(reply: str) -> list[str]:
    """The files the reply's words say it saved or created. Never raises."""
    try:
        from ..daemon.chat_turn import _claimed_write_names

        named, _changed = _claimed_write_names(str(reply or ""), [], ran_writer=False)
        return list(named)[:MAX_NAMES]
    except Exception:  # noqa: BLE001 — a reading that fails finds nothing
        log.debug("check-again: the reply's claims could not be read", exc_info=True)
        return []


def network_spelling(raw: str) -> bool:
    """A share, a device or an extended-length spelling (``\\\\host``,
    ``//host``, ``\\\\?\\``): judged on the TEXT, before anything touches the
    disk (the v1.379.0 lesson)."""
    s = str(raw or "").strip().replace("/", "\\")
    return s.startswith("\\\\")


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _inside(path: str, folder: str) -> bool:
    p, f = _key(path), _key(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        return bool(junction()) if junction is not None else False
    except OSError:
        return True


def files_by_name(folder: str, *, max_entries: "int | None" = None,
                  deadline_s: "float | None" = None) -> "set[str] | None":
    """BLOCKING: every file name (case-folded) under ``folder``, or None when
    the walk could not finish inside its bounds (then nothing is certain).
    Follows no link; skips :data:`SKIP_DIRS`. The bounds are read when it is
    called (:data:`WALK_MAX_ENTRIES`, :data:`WALK_DEADLINE_S`)."""
    if max_entries is None:
        max_entries = WALK_MAX_ENTRIES
    if deadline_s is None:
        deadline_s = WALK_DEADLINE_S
    names: set[str] = set()
    stack = [folder]
    seen = 0
    end = time.monotonic() + deadline_s
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                for entry in it:
                    seen += 1
                    if seen > max_entries or time.monotonic() > end:
                        return None
                    if _is_link(entry):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in SKIP_DIRS:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            names.add(entry.name.lower())
                    except OSError:
                        return None
        except OSError:
            if here == folder:
                return None
            return None  # a folder it could not read: not certain
    return names


#: How much of the reply is read for the place a name is written at, and the
#: longest path in front of a name (bounded: linear).
REPLY_CHARS = 20_000
PATH_CHARS = 400


def _elsewhere(reply: str, base: str, folder: str) -> bool:
    r"""Does the reply write ``base`` with a place in front of it that is not
    (certainly) this folder? A share or a device (``\\host\x.xlsx``),
    the home (``~/``), a drive-relative spelling, or an absolute path outside
    the folder. Judged on the TEXT only. Never raises (cannot tell = yes)."""
    try:
        text = str(reply or "")[:REPLY_CHARS]
        rx = re.compile(r"[^\s`'\"()<>\[\]]{0,%d}?" % PATH_CHARS + re.escape(base), re.IGNORECASE)
        for m in rx.finditer(text):
            token = m.group(0)
            if network_spelling(token) or token.startswith("~"):
                return True
            if len(token) > 1 and token[1] == ":" and not os.path.isabs(token):
                return True
            if os.path.isabs(token) and not _inside(token, folder):
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def missing_files(names: list[str], folder: str, events: list[dict[str, Any]],
                  reply: str = "") -> list[str]:
    """BLOCKING: the claimed names that are CERTAINLY not in the folder.

    Left alone (not certain): a name spelled as a share or a device, a folder
    spelled so, a name the reply writes somewhere else (:func:`_elsewhere`), a
    name under a folder the walk does not enter, a name any of the reply's own
    steps mentions (it may have been written elsewhere on purpose), and
    everything when the walk could not finish. Never raises."""
    try:
        folder = str(folder or "").strip()
        if not folder or network_spelling(folder) or not names:
            return []
        candidates: list[tuple[str, str]] = []
        steps = _step_text(events)
        for raw in names:
            name = str(raw or "").strip().strip("`'\"")
            if not name or network_spelling(name) or "\x00" in name:
                continue
            spelled = name.replace("\\", "/")
            parts = [p for p in spelled.split("/") if p]
            if not parts or ".." in parts or any(p.lower() in SKIP_DIRS for p in parts[:-1]):
                continue
            if os.path.isabs(name) or (len(name) > 1 and name[1] == ":"):
                if not os.path.isabs(name) or not _inside(name, folder):
                    continue  # outside the folder, or a drive-relative spelling
            base = parts[-1]
            if _elsewhere(reply, base, folder):
                continue  # the reply puts it somewhere else
            if base.lower() in steps:
                continue  # a step of its own names it: it may be elsewhere
            candidates.append((name, base.lower()))
        if not candidates:
            return []
        present = files_by_name(folder)
        if present is None:
            return []
        return [name for name, base in candidates if base not in present]
    except Exception:  # noqa: BLE001
        log.debug("check-again: the folder could not be looked at", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
def _listed(names: list[str]) -> str:
    out = ", ".join(f"`{shown(n)}`" for n in names[:3])
    more = len(names) - 3
    return out + (f" and {more} more" if more > 0 else "")


def note_for(missing: list[str], gave_up: bool, round_no: int) -> str:
    """What the TOOL is told (its hidden user message)."""
    lines = [NOTE_HEAD]
    if missing:
        one = len(missing) == 1
        lines.append(
            f"- Your reply says {_listed(missing)} {'is' if one else 'are'} saved, but "
            f"{'it is' if one else 'they are'} not in the folder. Create "
            f"{'it' if one else 'them'} now, or say plainly that {'it was' if one else 'they were'} "
            "not made."
        )
    if gave_up:
        lines.append(f"- {GIVE_UP_WORDS}")
    lines.append(NOTE_TAIL)
    if round_no > 1:
        lines.append(f"(Check {round_no} of at most {MAX_FOLLOW_UPS}.)")
    return "\n".join(lines)


def line_for(missing: list[str], gave_up: bool) -> str:
    """The plain line the page draws for the follow-up (Iron Jarvis's step)."""
    said: list[str] = []
    if missing:
        names = ", ".join(shown(n) for n in missing[:3]) + (
            f" and {len(missing) - 3} more" if len(missing) > 3 else "")
        one = len(missing) == 1
        said.append(f"the reply said {names} {'was' if one else 'were'} saved, and "
                    f"{'it is' if one else 'they are'} not in the folder")
    if gave_up:
        said.append("the reply ended by saying it could not be done; it is asked to try another "
                    "way first")
    return "Iron Jarvis sent it back: " + "; and ".join(said) + "."


def verdict_words(sent: int, ended: str) -> str:
    """The notice under a reply that was sent back and still has a problem."""
    times = {1: "once", 2: "twice"}.get(sent, f"{sent} times")
    return (f"Checked by Iron Jarvis: this is not finished. It was sent back {times} and stopped "
            f"there: {END_WORDS[ended]}. {VERDICT_CARRY_ON}")


# --------------------------------------------------------------------------- #
# The service's hook
# --------------------------------------------------------------------------- #
def _queue_waiting(live: Any) -> bool:
    q = getattr(live, "queue", None)
    return bool(q is not None and getattr(q, "items", None))


def _busy_alone(live: Any, now: float) -> bool:
    try:
        return bool(live.working_alone or live.keeps_working(now) or live.replying_alone(now))
    except Exception:  # noqa: BLE001 — cannot tell = busy
        return True


def _emergency_stop(platform: Any) -> bool:
    try:
        from . import carry_on as CO

        return CO._emergency_stop(platform)
    except Exception:  # noqa: BLE001 — cannot tell = on
        return True


def after_turn(svc: Any, live: Any, turn: Any, done: dict[str, Any]) -> None:
    """``service._end_turn``'s hook, right after the turn's ``done`` is out:
    decide whether this reply is judged, and judge it in a task. Never raises."""
    try:
        _after_turn(svc, live, turn, done)
    except Exception:  # noqa: BLE001 — a check never breaks a reply's end
        log.debug("check-again: the turn's end could not be read", exc_info=True)


def _after_turn(svc: Any, live: Any, turn: Any, done: dict[str, Any]) -> None:
    sid = str(live.sid)
    table = chains(svc)
    carry = getattr(turn, "carry", None)
    ours = isinstance(carry, dict) and carry.get("why") == WHY
    if ours:
        chain = table.get(sid)
        if chain is None or str(carry.get("origin") or "") != chain.origin:
            table.pop(sid, None)
            return
    else:
        table.pop(sid, None)
        if carry:
            return  # another carry-on Iron Jarvis started by itself: never judged
        chain = Chain(origin=str(turn.id))
    if (
        str(done.get("stop") or "") != "completed"
        or getattr(turn, "account_limit", False)
        or getattr(svc, "_closing", False)
        or getattr(live, "asks", None)
        or _queue_waiting(live)
        or turn.id in set((getattr(svc, "handoff_turns", None) or {}).values())
        or _busy_alone(live, svc.clock())
        or _emergency_stop(svc.platform)
    ):
        table.pop(sid, None)
        return
    chain.pending = str(turn.id)
    table[sid] = chain
    events = list(getattr(turn, "events", []) or [])
    from ..harness.stall import keep

    with contextlib.suppress(RuntimeError):
        task = asyncio.ensure_future(_judge(svc, sid, chain, str(turn.id), events))
        running = judging(svc)
        running.add(task)
        task.add_done_callback(running.discard)
        keep(task)


def _still_current(svc: Any, sid: str, chain: Chain, turn_id: str) -> bool:
    live = svc.live.get(sid)
    return (
        chains(svc).get(sid) is chain and chain.pending == turn_id and live is not None
        and live.turn is None and not getattr(live, "asks", None) and not _queue_waiting(live)
        and not getattr(svc, "_closing", False)
    )


async def _judge(svc: Any, sid: str, chain: Chain, turn_id: str, events: list[dict[str, Any]]) -> None:
    try:
        await _judge_in(svc, sid, chain, turn_id, events)
    except Exception:  # noqa: BLE001
        log.warning("checking a Code-view reply again failed", exc_info=True)
        if chains(svc).get(sid) is chain:
            chains(svc).pop(sid, None)


async def _judge_in(svc: Any, sid: str, chain: Chain, turn_id: str, events: list[dict[str, Any]]) -> None:
    from . import store

    table = chains(svc)
    row = await asyncio.to_thread(store.get, svc.engine, sid)
    if row is None or row.archived_at is not None or getattr(row, "handed_from", None):
        table.pop(sid, None)
        return
    reply = final_text(events)
    if chain.sent and _BLOCKED_LINE_RX.search(reply):
        table.pop(sid, None)  # it says what stops it: the user's to answer
        return
    names = claimed_names(reply)
    missing = await asyncio.to_thread(missing_files, names, str(row.folder or ""), events, reply) if names else []
    gave_up = False
    if not chain.gave_up_fired and worked(events):
        from ..harness.give_up import gives_up

        gave_up = gives_up(reply)
    if not missing and not gave_up:
        table.pop(sid, None)
        return
    keys = frozenset(f"missing:{n.lower()}" for n in missing)
    ended = ""
    if chain.sent and keys and keys == chain.last_keys and not gave_up:
        ended = END_STUCK
    elif chain.sent >= MAX_FOLLOW_UPS:
        ended = END_LIMIT
    if ended:
        table.pop(sid, None)
        live = svc.live.get(sid)
        if missing and live is not None and live.turn is None:
            svc._emit(live, make("notice", text=verdict_words(chain.sent, ended)))
        return
    if not _still_current(svc, sid, chain, turn_id):
        return
    chain.sent += 1
    chain.last_keys = keys
    if gave_up:
        chain.gave_up_fired = True
    carry = {
        "why": WHY,
        "from": turn_id,
        "origin": chain.origin,
        "round": chain.sent,
        "note": line_for(missing, gave_up),
    }
    from . import carry_on as CO
    from .service import Refused

    try:
        new = await CO._start(svc, sid, note_for(missing, gave_up, chain.sent), carry)
    except Refused as r:
        log.info("not sending a Code-view reply back: %s", r.sentence)
        if table.get(sid) is chain:
            table.pop(sid, None)
        return
    chain.pending = str(getattr(new, "id", "") or "")
    from ..core.nudges import CHECKED_AGAIN, GAVE_UP, note_nudge
    from . import news as NEWS

    await note_nudge(
        getattr(svc.platform, "event_bus", None), GAVE_UP if gave_up and not missing else CHECKED_AGAIN,
        session_id=NEWS.session_id_for(sid), surface="build_app", thread_id=sid,
        turn_id=str(getattr(new, "id", "") or ""),
    )


__all__ = [
    "Chain", "END_LIMIT", "END_STUCK", "MAX_FOLLOW_UPS", "WHY", "after_turn", "claimed_names",
    "files_by_name", "final_text", "line_for", "missing_files", "network_spelling", "note_for",
    "verdict_words",
]
