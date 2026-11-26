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
    CHAT_FREE_LABEL,
    CHAT_LABELS,
    CHAT_TOOLS_LABEL,
    RECORD_PARTS,
    chat_reading,
    decode_record,
    over_room,
)

TOOL_NAME = "chat_fullness"
NOTHING = (
    "Iron Jarvis has no record yet of what is filling this chat. It keeps one for each reply: how big "
    "your messages, attached files, what it remembered and its tools were. Sizes only, never the text."
)
#: Asked outside a chat's own reply (inside an agent's task, most often):
#: there is no chat there to be full.
NOT_A_CHAT = (
    "This was not asked in a chat (an agent's task is not one), so there is no chat here to be full. To see "
    "what is filling a chat, ask in that chat or press its fullness meter."
)
#: VOCABULARY.md keeps "model server" for a machine on the user's own
#: network, and has no word for whoever serves a model in general: "the
#: model" (the page, this answer and the script say the same).
NOT_REPORTED = "The model did not report how much it reused from the reply before."
REUSED = "The model reused about {percent}% of what was sent."
ESTIMATE_NOTE = "Every size is an estimate."

# --------------------------------------------------------------------------- #
# ONE SET OF WORDS for the page's list and the chat's answer. The page's twin
# is dashboard/lib/contextWords.ts (``fillWords``) and lib/chatFilling.ts
# (``overWords``, ``overLeftWords``, ``OVER_SHARES``, ``ROOM_LEFT_NONE``,
# ``OF_THE_ROOM``, ``MORE_THAN_THE_ROOM``); the contract fixture
# (tests/test_intel_06_chat_fullness.py) holds both readers' words for the
# same record, and dashboard/__tests__/intel-06-chat-filling.test.tsx reads
# them side by side.
# --------------------------------------------------------------------------- #
SAFE_SIZE_NOTE = "This model's own limit is not known, so a safe size is used."
#: A chat that holds more than the room: said ONCE, in place of a percent.
OVER_LEFT = "Iron Jarvis leaves the oldest messages out of each reply so it fits"
OVER_SHARES = "Each share here is of everything this chat holds."
ROOM_LEFT_NONE = "none"
#: After a tool row's share when the list's other shares are of what the chat
#: holds ("about 7% of the room").
OF_THE_ROOM = " of the room"
#: A size bigger than the whole room, where a share of the room is said (only
#: the tools can be): never a percent above 100.
MORE_THAN_THE_ROOM = "more than the room"


def fill_words(percent: int, window_known: bool) -> str:
    """The gauge's own sentence (``fillWords("chat", ...)`` on the page)."""
    if window_known:
        return f"This chat fills about {percent}% of what this model can read at once."
    return f"This chat fills about {percent}% of what Iron Jarvis sends this model at once. {SAFE_SIZE_NOTE}"


def over_words(window_known: bool) -> str:
    """The first line for a chat that holds more than the room."""
    if window_known:
        return "This chat holds more than this model can read at once."
    return f"This chat holds more than Iron Jarvis sends this model at once. {SAFE_SIZE_NOTE}"


def over_left_words(left_out: int) -> str:
    """Said once for a chat that holds more than the room: the oldest
    messages are left out, with the planner's count when the record saved
    one ("at least": the fit made for each model call may leave out more)."""
    n = int(left_out or 0)
    return f"{OVER_LEFT} (at least {n} in the newest reply)." if n > 0 else f"{OVER_LEFT}."


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
    """The share of what a reply sent that the model reused from last time:
    None when the provider reported no figure (never 0 for "unknown")."""
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
def _share(tokens: int, of: int) -> str:
    """A size as a share of *of* ("about 12%", "less than 1%"). Never a
    percent above 100: a size bigger than the whole is said in words."""
    if of <= 0:
        return ""
    p = 100 * tokens / of
    if p > 100:
        return MORE_THAN_THE_ROOM
    # Half up, as the page rounds (``Math.round``), so the two never differ by one.
    return "less than 1%" if 0 < p < 1 else f"about {int(p + 0.5)}%"


def _lines(record: "dict[str, Any]") -> list[str]:
    """One record as words: the fullness, then each part's share. A chat
    that holds more than the room says so in place of a percent, and its
    shares are of what the chat HOLDS (they add up to 100), never of the
    room (a part would read above 100)."""
    window = int(record.get("window") or 0)
    parts = record.get("parts") or {}
    known = record.get("window_known") is not False
    over = over_room(record)
    said: list[str] = []
    if over:
        said.append(over_words(known))
    elif window and record.get("percent") is not None:
        said.append(fill_words(int(record["percent"]), known))
    whole = int(record.get("used") or 0) or sum(int(parts.get(k) or 0) for k in RECORD_PARTS)
    of = whole if over else window
    for key in RECORD_PARTS:
        n = int(parts.get(key) or 0)
        if not n:
            continue
        share = _share(n, of)
        said.append(f"- {CHAT_LABELS[key]}: {share}" if share else f"- {CHAT_LABELS[key]}")
    left = int(record.get("left_out") or 0)
    if over:
        said.append(f"- {CHAT_FREE_LABEL}: {ROOM_LEFT_NONE}")
        said.append(f"- {over_left_words(left)} {OVER_SHARES}")
    elif left:
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
    # The tools are sent beside the chat: their share is of the ROOM, and
    # says so where the list's other shares are of what the chat holds.
    share = _share(size, int(record.get("window") or 0))
    if share and share != MORE_THAN_THE_ROOM and over_room(record):
        share += OF_THE_ROOM
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
        said.append(f"- {REUSED.format(percent=reused)}" if reused is not None else f"- {NOT_REPORTED}")
    said.append(ESTIMATE_NOTE + " A long chat gets full mostly from its messages and attached files; "
                "starting a new chat, or summarizing the earlier messages, makes room.")
    return "\n".join(said)


__all__ = [
    "ESTIMATE_NOTE", "MORE_THAN_THE_ROOM", "NOTHING", "NOT_A_CHAT", "NOT_REPORTED", "OF_THE_ROOM", "OVER_LEFT", "OVER_SHARES",
    "REUSED", "ROOM_LEFT_NONE", "SAFE_SIZE_NOTE", "TOOL_NAME", "answer", "fill_words", "for_thread", "newest_row",
    "over_left_words", "over_words", "reused_percent",
]
