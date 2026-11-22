"""Pick up where you left off: the card when you open a folder (next leaps
04-a; build-out plan R2.1, idea 04; ``docs/next-leaps/01-comes-to-you-and-
scroll-back.md``).

:func:`pick_up` answers ``GET /work-record/pick-up?folder=&local=``: what a NEW
chat, a new Chat › Code session or a new Build › Code session in ``folder``
shows before its first message. "Last time (12 Mar): <title>. Decided: …
Still open: …", with Continue. Continue sends one fixed sentence written in
the dashboard's code (:data:`CONTINUE_SENTENCE`, "Continue where we left off
in this folder."); the lines below reach the model the way they already do,
through the work block (``workmap/block.work_block``), as a record. Nothing a
model wrote is ever sent as the owner's words. The block takes the sentence
out of the turn's words before it matches anything (``block.without_continue``),
so Continue's turn is told this folder's work and nothing the sentence's own
words match elsewhere. The chat or session Continue starts is named by the
dashboard after the work it continues ("<title> (continued)"), never after
the sentence.

THE CARD SHOWS ONLY WHAT CONTINUE'S TURN IS TOLD (D-04.1: "the card already
shows what will be picked up"). It reads through THE ONE READER
(``lookup.Reader``) with the wall of a turn in this folder whose words name
no one (the fixed sentence names nobody) and the Local only rule of the turn
that follows (``local``: the new chat or session is Local only, so its turn
stays on this network), and it is built from THE BLOCK'S OWN "In this folder"
part (``block.here_part``): the same rows, the same six, the same recap lines
and the same room. So:

* EXACTLY THAT FOLDER: of the pieces the block tells, only those in exactly
  ``folder`` are on the card (never a folder under it, never another
  folder); a coding session's own copy of a repository counts as the
  repository. Repeats are folded.
* THE SOURCES ASKED AGAIN (``Reader.live``): work deleted a moment ago is
  never shown from a row not rebuilt yet; archived work is left out; a
  forgotten folder has no rows. Local only and kept-home work only when
  ``local``. The client wall (``Reader.told``), as for the block.
* ONLY RECAPS STILL CURRENT (``recap.current_for`` against the source's own
  ``updated_at``): a recap written before the latest messages is not shown,
  and ``recap_pending`` says one is waiting to be written again.
* EVERY LINE MASKED AND SCANNED (``block._safe``, the block's own): a recap
  line that reads as an instruction is left out, and a flagged title drops
  its piece of work. No model call, no file opened.
* D-04.4: ``full`` is True when something is still open or the last work
  was in the past :data:`FRESH_DAYS` days; otherwise the dashboard shows one
  quiet line ("Last here: 12 Mar") that opens the card.
* INSIDE A PROJECT, NO CARD: a turn grounded in a project is told the
  project's own recent activity instead of the "In this folder" part
  (``chat_turn._gather_grounding``, ``list_folder=False``), which carries no
  recap. The new chat page never mounts the card inside a project; a new
  Chat › Code session (``chat_code``) is grounded in the project that holds
  its folder (``routes/build.py``, ``project_for_path``), so for such a
  folder the answer is the empty card with ``in_project`` True.

A record that cannot be read (no record, the rows, the sources or the recaps)
RAISES :class:`Unreadable`: the route answers 503 in words, never "nothing
here". BLOCKING (SQLite); the route is a plain ``def``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.db import session_scope
from . import block as _block
from . import keys
from . import lookup as _lookup
from . import recap as _recap
from .index import _iso, link_for
from .models import KIND_CHAT, KIND_CODE

log = logging.getLogger(__name__)

#: What Continue sends, word for word (the block's own constant).
CONTINUE_SENTENCE = _block.CONTINUE_SENTENCE
#: Rows read for the folder (the block reads as many).
ROWS_READ = _block.HERE_MAX * 6
#: Lines of each kind the card carries, at most.
DONE_MAX = 4
DECIDED_MAX = 6
OPEN_MAX = 6
#: D-04.4: the full card when the last work is at most this many days old.
FRESH_DAYS = 30
#: The kinds of work that get recaps (``recap.py``: chats and Chat › Code).
RECAPPED_KINDS = frozenset({KIND_CHAT, KIND_CODE})

NOTHING_WORDS = "No earlier work on record in this folder."
IN_PROJECT_WORDS = (
    "This folder is in a project: a coding session here is told the project's own recent activity."
)

_LINE_RX = re.compile(r"^(Done|Decided|Open):\s*(.+)$")


class Unreadable(RuntimeError):
    """The record, its sources or its recaps could not be read."""


def _when_naive(value: Any) -> "datetime | None":
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _labelled(line: str) -> "tuple[str, str] | None":
    """``("Open", "Call the bank")`` for a recap line as the block tells it
    (already masked and scanned by ``block.recap_lines``); None for a line
    with no label."""
    m = _LINE_RX.match(" ".join(str(line or "").split()))
    if not m:
        return None
    text = m.group(2).strip()
    return (m.group(1), text) if text else None


def _nothing(folder: str, client: "dict[str, str] | None", *, in_project: bool = False) -> dict[str, Any]:
    return {
        "folder": folder, "name": keys.folder_name(folder), "client": client, "last": None,
        "done": [], "decided": [], "open": [], "pieces": 0, "recap_pending": False,
        "full": False, "in_project": in_project,
        "words": IN_PROJECT_WORDS if in_project else NOTHING_WORDS,
    }


def _in_a_project(engine: Any, folder: str) -> bool:
    """Is ``folder`` inside an active project (``projects/locate``, the rule
    Chat › Code's turns are grounded by)? Its own answer to a store that
    cannot be read is "no project", the same as the turn's."""
    from ..projects.locate import project_for_path

    return project_for_path(engine, folder) is not None


def _client_of(turn: Any) -> "dict[str, str] | None":
    """The confirmed client whose folder this is (the wall's own answer)."""
    root = getattr(turn, "client", None) if getattr(turn, "ok", False) else None
    if root is None:
        return None
    return {"id": str(root.client_id), "name": str(root.name)}


def pick_up(platform: Any, folder: str, now: "datetime | None" = None, *,
            local: bool = False, chat_code: bool = False) -> dict[str, Any]:
    """The card for ``folder`` (see the module docstring). ``local``: the new
    chat or session is Local only. ``chat_code``: the card is for a new
    Chat › Code session (no card for a folder inside a project). RAISES
    :class:`Unreadable` when the record cannot be read, ``ValueError`` for
    no folder. BLOCKING."""
    place = str(folder or "").strip()
    if not place:
        raise ValueError("no folder")
    now = _when_naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        reader = _lookup.Reader.open(platform, folder=place, query="", withhold=not local)
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the record could not be opened") from exc
    if reader is None:
        raise Unreadable("there is no record")
    client = _client_of(reader.turn)
    if chat_code and _in_a_project(reader.engine, place):
        return _nothing(place, client, in_project=True)
    try:
        # The block's own read of "In this folder" (the folder and under it).
        rows, _total = reader.wm.for_folder(place, limit=ROWS_READ)
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the folder's work could not be read") from exc
    if not rows:
        return _nothing(place, client)
    try:
        live = reader.live(rows)
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the sources could not be asked") from exc
    here_rows = [live[r["id"]] for r in rows
                 if r["id"] in live and reader.allowed(live[r["id"]]) and reader.told(live[r["id"]])]
    own = keys.folder_key(keys.home_folder(place))
    exact = [r for r in here_rows if own and keys.folder_key(keys.home_folder(r.get("folder") or "")) == own]
    try:
        with session_scope(reader.engine) as db:
            kept = _recap.recaps_for(db, [r["id"] for r in exact])
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the recaps could not be read") from exc
    told, _groups = _block.here_part(reader.engine, place, here_rows, now, kept=kept)
    pieces: list[dict[str, Any]] = []
    for t in told:
        row = t["row"]
        if keys.folder_key(keys.home_folder(row.get("folder") or "")) != own:
            continue  # work in a folder under this one: told by title, never on the card
        title = _block._safe(_block._clip(row.get("title") or "", _block.TITLE_CHARS)).strip()
        if not title:
            continue
        pieces.append({**t, "title": title})
    if not pieces:
        return _nothing(place, client)

    done: list[str] = []
    decided: list[dict[str, Any]] = []
    still_open: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pending = False
    for i, p in enumerate(pieces):
        row = p["row"]
        got = kept.get(row["id"])
        if got is not None and not _recap.current_for(got, row.get("live_updated") or ""):
            if row["kind"] in RECAPPED_KINDS:
                pending = True  # written before the latest messages: not shown
            continue
        source = {"entry_id": row["id"], "title": p["title"], "link": link_for(row["kind"], row["ref"]),
                  "at": _iso(row.get("updated"))}
        for raw in p["recap"]:
            parsed = _labelled(raw)
            if parsed is None:
                continue
            label, text = parsed
            key = (label, text.lower())
            if key in seen:
                continue
            seen.add(key)
            if label == "Done":
                if i == 0 and len(done) < DONE_MAX:
                    done.append(text)
            elif label == "Decided":
                if len(decided) < DECIDED_MAX:
                    decided.append({"text": text, **source})
            elif len(still_open) < OPEN_MAX:
                still_open.append({"text": text, **source})

    first = pieces[0]["row"]
    title = pieces[0]["title"]
    when = _when_naive(first.get("updated"))
    fresh = when is not None and now - when <= timedelta(days=FRESH_DAYS)
    last = {
        "id": first["id"], "kind": first["kind"], "title": title, "tool": first.get("tool") or "",
        "at": _iso(first.get("updated")), "link": link_for(first["kind"], first["ref"]),
        "outcome": first.get("outcome") or "",
    }
    day = _block.date_words(first.get("updated"))
    words = f"Last time ({day}): {title}." if day else f"Last time: {title}."
    if still_open:
        words += f" {len(still_open)} still open."
    log.info("pick-up: %d pieces, %d decided, %d open, pending=%s", len(pieces), len(decided),
             len(still_open), pending)
    return {
        "folder": place, "name": keys.folder_name(place), "client": client, "last": last,
        "done": done, "decided": decided, "open": still_open, "pieces": len(pieces),
        "recap_pending": pending, "full": bool(still_open) or fresh, "in_project": False,
        "words": words,
    }


__all__ = [
    "CONTINUE_SENTENCE", "DECIDED_MAX", "DONE_MAX", "FRESH_DAYS", "IN_PROJECT_WORDS", "NOTHING_WORDS",
    "OPEN_MAX", "Unreadable", "pick_up",
]
