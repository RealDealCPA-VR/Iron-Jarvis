"""What went wrong in a chat leaves ONE suggestion (v1.507.0, north-star plan 7.1).

Chat is the surface the owner uses most, and until now it learned only from a
correction of STYLE said twice (``learning.preferences.suggest_for_turn``).
Three more things the owner does there say "that was wrong", and each now
leaves one suggestion that waits in Improvements:

* :func:`suggest_from_undo` — the owner undid a change a chat reply made
  (``POST /undo/{id}``, chat's own "undo that"). The reply is found from the
  chat's OWN record: the stored message that carries the change's id (a
  settings, record or key card) or, for a file, the one reply in all saved
  chats that lists that file. When the reply cannot be told for certain,
  nothing is suggested.
* :func:`suggest_from_correction` — the owner's next message corrects the
  reply's substance in the one shape ``corrections.is_substance_correction``
  reads ("no, metric units, not imperial"). No model is called.
* :func:`suggest_from_note` — a thumbs-down with a note ("What should be
  different next time?").

The rules every path keeps:

* THE OWNER'S WORDS. The suggestion is their correction, their note, or (for
  an undo, which has no words of its own) the change as the app's own log
  words it and the request they made, quoted. Never a model's guess.
* ONE PATH. Every suggestion goes through ``preferences.mint_noticed``:
  status proposed, origin "noticed", the same never-ask-twice rule and the
  same open limit (:data:`preferences.MAX_OPEN`), under the same lock.
* THE TURN IS THE EVIDENCE, with the link to its chat
  (``preferences.chat_evidence``). A chat that is not saved has no link, so
  nothing is suggested from it.
* KEPT TO THE FOLDER BY THE WALL. The rows are ``source="went_wrong"``
  (``preferences.SOURCE_WENT_WRONG``): until kept they reach no prompt (as
  every proposed row), and once kept ``preferences.told_lessons`` tells one
  only where the client wall lets its chat be told.

BLOCKING (SQLite): callers run these off the event loop. Nothing here logs
the owner's words.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from .corrections import substance_text
from .preferences import (
    MAX_OPEN,
    NOTICED_DECLINED,
    NOTICED_FULL,
    NOTICED_KEPT,
    NOTICED_SUGGESTED,
    NOTICED_WAITING,
    SOURCE_WENT_WRONG,
    chat_evidence,
    mint_noticed,
)

log = logging.getLogger(__name__)

#: The longest note a thumbs-down suggestion keeps (``preferences.MAX_TEXT``).
NOTE_CHARS = 280
#: The longest request quoted in an undo's suggestion.
REQUEST_CHARS = 120
#: Saved chats read to find the reply an undone change came from (newest
#: first, touched since a little before the change).
MAX_THREADS = 200
#: The slack before the change's time a chat may have been saved at.
THREAD_SLACK = timedelta(minutes=5)
#: The session id chat's tools run under (``chat_turn``; a coding session's
#: is ``build:<thread>`` and is not read here).
CHAT_SESSION = "chat"

#: The sentence an undo's suggestion says: the change in the log's own words
#: and the owner's request, quoted.
UNDO_TEXT = "This was undone: {what}, after the request “{request}”."

#: What the owner reads under a thumbs-down with a note, per outcome.
NOTE_WORDS = {
    NOTICED_SUGGESTED: "Noted. Your note waits in Improvements as a suggestion; it is used once you keep it.",
    NOTICED_KEPT: "Noted. You already keep a preference like this.",
    NOTICED_WAITING: "Noted. A suggestion like this already waits in Improvements.",
    NOTICED_DECLINED: "Noted. You said “not this” to a suggestion like this before, so it is not suggested again.",
    NOTICED_FULL: (
        f"Noted. {MAX_OPEN} suggestions already wait in Improvements, so this note was not added; "
        "answer those first."
    ),
}
#: The outcome (and its words) for a note in a chat that is not saved yet.
NOTE_UNSAVED = "unsaved"
NOTE_WORDS[NOTE_UNSAVED] = "Noted. This chat is not saved yet, so the note could not wait as a suggestion."


def _clean(text: Any) -> str:
    from .engine import without_secrets

    return without_secrets(" ".join(str(text or "").split()))


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# a thumbs-down with a note
# --------------------------------------------------------------------------- #


def suggest_from_note(engine: Any, thread_id: Any, note: Any) -> tuple[Any, str]:
    """``(row, outcome)`` for a thumbs-down's note: the note itself (the
    owner's words) as ONE suggestion, the reply's chat as evidence.
    ``(None, NOTE_UNSAVED)`` for a chat that is not saved. Blocking."""
    words = _clip(_clean(note), NOTE_CHARS)
    if not words:
        return None, ""
    ev = chat_evidence(words, str(thread_id or ""))
    if ev is None:
        return None, NOTE_UNSAVED
    text = words if words.endswith((".", "!", "?", "…")) else words + "."
    text = text[:1].upper() + text[1:]
    return mint_noticed(engine, text, evidence=[ev], source=SOURCE_WENT_WRONG)


# --------------------------------------------------------------------------- #
# a correction of substance in the next message
# --------------------------------------------------------------------------- #


def _role(m: Any) -> str:
    return str(m.get("role") if isinstance(m, dict) else getattr(m, "role", "") or "")


def _content(m: Any) -> str:
    raw = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
    return raw if isinstance(raw, str) else ""


def suggest_from_correction(engine: Any, body: Any) -> Any:
    """ONE suggestion when the turn's message corrects the reply before it in
    the one shape ``corrections.is_substance_correction`` reads, in a saved
    chat (not a coding session's pane). The row, or None. Blocking."""
    if str(getattr(body, "pane_id", "") or "").strip():
        return None
    msgs = list(getattr(body, "messages", None) or [])
    if len(msgs) < 2 or _role(msgs[-1]) != "user" or _role(msgs[-2]) != "assistant":
        return None
    if not _content(msgs[-2]).strip():
        return None  # nothing was said to correct
    said = _content(msgs[-1])
    text = substance_text(said)
    if not text:
        return None
    ev = chat_evidence(_clean(said), str(getattr(body, "thread_id", "") or ""))
    if ev is None:
        return None
    row, _outcome = mint_noticed(engine, _clean(text), evidence=[ev], source=SOURCE_WENT_WRONG)
    return row


# --------------------------------------------------------------------------- #
# an undo of a change a chat reply made
# --------------------------------------------------------------------------- #


def _norm_path(path: Any) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.normpath(raw))
    except Exception:  # noqa: BLE001
        return ""


def _change(engine: Any, action_id: str) -> dict[str, Any] | None:
    """The undone change as its own records say: session, tool, arguments,
    when, the ids a reply's card carries, and the file it wrote."""
    from ..core.models import ToolInvocation, UndoJournal

    with session_scope(engine) as db:
        inv = db.get(ToolInvocation, action_id)
        if inv is None:
            return None
        journal = db.get(UndoJournal, action_id)
        try:
            args = json.loads(inv.args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        env: Any = {}
        if journal is not None:
            try:
                env = json.loads(journal.pre_inline or "{}")
            except (TypeError, ValueError):
                env = {}
        out = {
            "session_id": str(inv.session_id or ""),
            "tool": str(inv.tool or ""),
            "args": args if isinstance(args, dict) else {},
            "created_at": inv.created_at,
            "ids": {str(action_id)},
            "path": "",
        }
    if isinstance(env, dict):
        if isinstance(env.get("change_id"), str) and env["change_id"]:
            out["ids"].add(env["change_id"])
        rel, ws = env.get("path"), env.get("workspace")
        if isinstance(rel, str) and rel:
            joined = rel if os.path.isabs(rel) else (os.path.join(ws, rel) if isinstance(ws, str) and ws else "")
            out["path"] = _norm_path(joined)
    return out


def _reply_hits(engine: Any, change: dict[str, Any]) -> list[tuple[str, str, int, list[Any]]]:
    """Every saved chat reply that names the change: ``(thread id, owner,
    message index, messages)``. A reply names it when its stored message
    carries the change's id (a card), or lists the file it wrote."""
    from ..core.models import ChatThreadRecord, chat_surface_only

    since = change["created_at"] - THREAD_SLACK if change.get("created_at") else None
    with session_scope(engine) as db:
        stmt = select(ChatThreadRecord.id, ChatThreadRecord.owner, ChatThreadRecord.messages_json).where(
            chat_surface_only(ChatThreadRecord.surface)
        )
        if since is not None:
            stmt = stmt.where(ChatThreadRecord.updated_at >= since)
        rows = list(db.exec(stmt.order_by(ChatThreadRecord.updated_at.desc()).limit(MAX_THREADS)))
    ids = [i for i in change["ids"] if i]
    path = change.get("path") or ""
    hits: list[tuple[str, str, int, list[Any]]] = []
    for tid, owner, blob in rows:
        if not blob or not (any(i in blob for i in ids) or path):
            continue
        try:
            msgs = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if not isinstance(msgs, list):
            continue
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            named = any(x in json.dumps(m, ensure_ascii=False) for x in ids)
            if not named and path:
                docs = m.get("documents")
                named = isinstance(docs, list) and any(_norm_path(p) == path for p in docs if isinstance(p, str))
            if named:
                hits.append((str(tid), str(owner or ""), i, msgs))
    return hits


def suggest_from_undo(engine: Any, action_id: Any) -> Any:
    """ONE suggestion when the owner undid a change a CHAT reply made: the
    change in the log's own words and the request the reply answered, the
    chat as evidence. Only when exactly one saved reply names the change.
    The row, or None. Blocking; never raises."""
    try:
        change = _change(engine, str(action_id or ""))
        if change is None or change["session_id"] != CHAT_SESSION:
            return None
        hits = _reply_hits(engine, change)
        if len(hits) != 1:
            return None  # not found, or more than one reply could be it
        thread_id, owner, index, msgs = hits[0]
        request = ""
        for m in reversed(msgs[:index]):
            if isinstance(m, dict) and m.get("role") == "user" and _content(m).strip():
                request = _clean(_content(m))
                break
        if not request:
            return None
        from ..agents.mission import tool_line

        what = _clean(tool_line(change["tool"], change["args"], True))
        text = UNDO_TEXT.format(what=what, request=_clip(request, REQUEST_CHARS))
        ev = chat_evidence(request, thread_id)
        if ev is None:
            return None
        if owner == "daemon":
            ev["where"] = "phone"
        row, _outcome = mint_noticed(engine, text, evidence=[ev], source=SOURCE_WENT_WRONG)
        return row
    except Exception as exc:  # noqa: BLE001 — a suggestion never fails an undo
        log.warning("no suggestion after an undo (%s)", type(exc).__name__)
        return None


async def publish_suggested(bus: Any, row: Any) -> None:
    """``preference.suggested`` (via "chat") for a row made here: the
    Improvements page re-reads on it. Never raises."""
    from ..core.events import EventType
    from .preferences import evidence_of

    if bus is None or row is None:
        return
    try:
        await bus.publish(
            EventType.PREFERENCE_SUGGESTED,
            {"id": row.id, "text": row.text, "count": len(evidence_of(row)), "via": "chat"},
        )
    except Exception:  # noqa: BLE001 — a bus hiccup never loses the row
        log.debug("preference.suggested publish failed", exc_info=True)


__all__ = [
    "NOTE_UNSAVED",
    "NOTE_WORDS",
    "UNDO_TEXT",
    "publish_suggested",
    "suggest_from_correction",
    "suggest_from_note",
    "suggest_from_undo",
]
