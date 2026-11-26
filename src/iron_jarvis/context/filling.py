"""What is filling this chat (the intelligence plan, release 6): the reader of
a chat's own record, for the list behind the fullness meter and for the
question "why is this chat getting full?".

Every chat reply saves the size of each part of what it was sent on its own
usage row (``AgentRun.parts_json``, ``context.breakdown.sent_record``). Two
readers, one record:

* :func:`for_thread` is the page's list (``GET /chat/threads/{id}/filling``):
  the newest reply's record in the Context drop-down's shape, in a plain
  chat's words;
* ``chat_fullness`` (``tools/chat_fullness.py``; the tool is not here
  because the providers import ``context``, which must not import ``tools``)
  answers the question in chat with :func:`answer`: the reply being written
  now (its own facts, the numbers its row will save) and, from the saved
  record, the reply before it.

Read only. It reads ONLY the chat it is asked in: the page's read names its
own chat, and the tool takes NO argument, so nothing can name another chat.
Sizes and names only: there is no text of any part to show, because none is
kept. Every size is an estimate and is said as "about".
"""

from __future__ import annotations

import json
from typing import Any

from .breakdown import (
    CHAT_BUILTIN_TOOLS,
    CHAT_LABELS,
    CHAT_TOOLS_LABEL,
    RECORD_PARTS,
    chat_reading,
    decode_record,
)

TOOL_NAME = "chat_fullness"
NOTHING = (
    "Iron Jarvis has no record yet of what is filling this chat. It keeps one for each reply: how big "
    "your messages, attached files, what it remembered and its tools were. Sizes only, never the text."
)
NOT_REPORTED = "The model server did not report how much it reused from the reply before."
ESTIMATE_NOTE = "Every size is an estimate."


def newest_row(engine: Any, thread_id: str) -> "dict[str, Any] | None":
    """BLOCKING. The newest reply of ONE saved chat that has a record, as
    plain values: ``{record, tool_names, at, input_tokens, cache_read}``.
    None for no chat id, no such reply, or any fault (never raises)."""
    tid = str(thread_id or "").strip()[:80]
    if not tid or engine is None:
        return None
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import AgentRun

        with session_scope(engine) as db:
            row = db.exec(
                select(AgentRun)
                .where(AgentRun.thread_id == tid, AgentRun.session_id == "chat",
                       AgentRun.parts_json.is_not(None))  # type: ignore[union-attr]
                .order_by(AgentRun.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            ).first()
            if row is None:
                return None
            record = decode_record(row.parts_json)
            if record is None:
                return None
            try:
                names = [str(n) for n in json.loads(row.tools_sent_json or "[]") if isinstance(n, str)]
            except (TypeError, ValueError):
                names = []
            return {
                "record": record,
                "tool_names": names,
                "at": row.created_at.isoformat() if row.created_at else None,
                # What the share reused is read against: the input of the
                # calls that reported a cache figure, else the whole input.
                "input_tokens": int(record.get("cache_input") or row.input_tokens or 0),
                "cache_read": row.cache_read_input_tokens,
            }
    except Exception:  # noqa: BLE001 — a read never breaks a page or a reply
        return None


def reused_percent(input_tokens: Any, cache_read: Any) -> "int | None":
    """The share of what a reply sent that the model server reused from last
    time: None when the provider reported no figure (never 0 for "unknown")."""
    try:
        if cache_read is None or not int(input_tokens or 0) > 0:
            return None
        return max(0, min(100, int(round(100 * int(cache_read) / int(input_tokens)))))
    except (TypeError, ValueError):
        return None


def for_thread(engine: Any, thread_id: str) -> "dict[str, Any]":
    """BLOCKING. The page's list for one saved chat (the route's body)."""
    out: dict[str, Any] = {"thread_id": str(thread_id or "").strip()[:80], "found": False, "reading": None}
    got = newest_row(engine, thread_id)
    if got is None:
        return out
    reading = chat_reading(got["record"], got["tool_names"])
    if reading is None:
        return out
    out.update({
        "found": True,
        "reading": reading,
        "at": got["at"],
        "window_known": got["record"].get("window_known") is not False,
        "left_out": int(got["record"].get("left_out") or 0),
    })
    return out


# --------------------------------------------------------------------------- #
# The answer in chat
# --------------------------------------------------------------------------- #
def _share(tokens: int, window: int) -> str:
    if window <= 0:
        return ""
    p = 100 * tokens / window
    return "less than 1%" if 0 < p < 1 else f"about {int(round(p))}%"


def _lines(record: "dict[str, Any]") -> list[str]:
    """One record as words: the fullness, then each part's share."""
    window = int(record.get("window") or 0)
    parts = record.get("parts") or {}
    said: list[str] = []
    if window and record.get("percent") is not None:
        room = ("what this model can read at once" if record.get("window_known") is not False
                else "the safe size Iron Jarvis uses for this model (its own limit is not known)")
        pct = int(record["percent"])
        # Over the room: said as that, never as a percent above 100 (the
        # meter stops at 100 too); the shares below still say how big each
        # part is against the room.
        said.append(f"This chat fills about {pct}% of {room}." if pct <= 100
                    else f"This chat holds more than {room}, so earlier messages are left out of each reply.")
    for key in RECORD_PARTS:
        n = int(parts.get(key) or 0)
        if not n:
            continue
        share = _share(n, window)
        said.append(f"- {CHAT_LABELS[key]}: {share}" if share else f"- {CHAT_LABELS[key]}")
    left = int(record.get("left_out") or 0)
    if left:
        # The planner's count: the fit made for each model call may leave
        # out more, so it is said as "at least".
        said.append(f"- At least {left} earlier message{' was' if left == 1 else 's were'} left out of the reply "
                    "so it fits.")
    return said


def _tool_line(record: "dict[str, Any]") -> str:
    tools = record.get("tools") or {}
    builtin = tools.get("builtin") or [0, 0]
    ext = tools.get("extensions") or {}
    count = int(builtin[0]) + sum(int(v[0]) for v in ext.values())
    size = int(builtin[1]) + sum(int(v[1]) for v in ext.values())
    if not count:
        return ""
    names = ([f"{CHAT_BUILTIN_TOOLS} ({builtin[0]})"] if builtin[0] else []) + [
        f"the {name} extension ({v[0]})" for name, v in sorted(ext.items(), key=lambda kv: -kv[1][1])
    ]
    share = _share(size, int(record.get("window") or 0))
    return (f"{CHAT_TOOLS_LABEL}, sent with each message and not counted in that figure: "
            f"{count} tool{'' if count == 1 else 's'}" + (f", {share}" if share else "")
            + f": {', '.join(names)}.")


def answer(now: "dict[str, Any] | None", saved: "dict[str, Any] | None") -> str:
    """The words handed to the model as data: *now* is this reply's own
    record (as it began), *saved* the newest saved reply's row
    (:func:`newest_row`)."""
    now = decode_record(now) if now else None
    if now is None and saved is None:
        return NOTHING
    said: list[str] = []
    if now is not None:
        said.append("What is filling this chat (Iron Jarvis's own record of this reply, as it began):")
        said += _lines(now)
    if saved is not None:
        rec = saved["record"]
        if now is None:
            said.append("What is filling this chat (Iron Jarvis's own record of the newest reply):")
            said += _lines(rec)
        else:
            said.append("From the reply before this one:")
        tools = _tool_line(rec)
        if tools:
            said.append(f"- {tools}")
        reused = reused_percent(saved.get("input_tokens"), saved.get("cache_read"))
        said.append(f"- The model server reused about {reused}% of what was sent."
                    if reused is not None else f"- {NOT_REPORTED}")
    said.append(ESTIMATE_NOTE + " A long chat gets full mostly from its messages and attached files; "
                "starting a new chat, or summarizing the earlier messages, makes room.")
    return "\n".join(said)


__all__ = [
    "ESTIMATE_NOTE", "NOTHING", "NOT_REPORTED", "TOOL_NAME", "answer", "for_thread", "newest_row",
    "reused_percent",
]
