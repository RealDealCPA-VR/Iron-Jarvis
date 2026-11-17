"""One run per chat hand-off (v1.529.0, owner decision 9 after the third fix plan).

Since v1.521.0 every window following a chat reply can act on its end, so with
two windows open on one chat, a reply that hands the request to an agent was
posted twice and started the agent run twice (two runs' cost, possibly two
sets of changes). The hand-off now names the chat turn that handed off (the
reply's name, ``turnId`` on the page), and every door that starts a hand-off
run passes it through here:

* :func:`clean_turn` normalises the name ("" = no name: the door behaves
  exactly as before, nothing is looked up or stored);
* :func:`held` serialises the creates that name ONE turn (per daemon, per
  name), so two requests at the same moment cannot both miss the lookup;
* :func:`find_handed` answers "did this turn already start a run" from the
  session rows (``Session.handed_from_turn``), so the guard survives a
  restart; the oldest row wins;
* :func:`stamp` writes the name on the run the door just made, BEFORE the
  door lets go of the lock.

A door uses them in this order::

    turn = clean_turn(body.handed_from_turn)
    async with held(engine, turn):
        first = turn and await asyncio.to_thread(find_handed, engine, turn)
        if first: return the first run (its row, its status)
        ...make the run...
        if turn: await asyncio.to_thread(stamp, engine, run.id, turn)
    ...start it...

The two lookups are blocking DB calls: callers hop off the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator

#: Longest name kept. The page's names are ``chat-<time>-<uuid>`` (~50 chars).
TURN_MAX_CHARS = 200

#: Per daemon (engine) and per name: [lock, users]. An entry lives only while
#: a create naming that turn is in flight, so the map never grows.
_LOCKS: dict[tuple[int, str], list[Any]] = {}


def clean_turn(value: Any) -> str:
    """The hand-off's turn name as stored: whitespace collapsed, capped; ""
    for none (``None``, blank, not text)."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:TURN_MAX_CHARS]


@contextlib.asynccontextmanager
async def held(engine: Any, turn: str) -> AsyncIterator[None]:
    """Hold the one lock for creates that name *turn* on this daemon. No
    name, no lock: a door without one is untouched."""
    if not turn:
        yield
        return
    key = (id(engine), turn)
    entry = _LOCKS.get(key)
    if entry is None:
        entry = _LOCKS[key] = [asyncio.Lock(), 0]
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0 and _LOCKS.get(key) is entry:
            del _LOCKS[key]


def find_handed(engine: Any, turn: str) -> str | None:
    """The id of the FIRST run *turn* handed off, or None. BLOCKING."""
    if not turn:
        return None
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session

    with session_scope(engine) as db:
        return db.exec(
            select(Session.id)
            .where(Session.handed_from_turn == turn)
            .order_by(Session.created_at)  # type: ignore[arg-type]
            .limit(1)
        ).first()


def stamp(engine: Any, session_id: str, turn: str) -> None:
    """Record that *turn* handed off to *session_id*. BLOCKING."""
    if not turn:
        return
    from ..core.db import session_scope
    from ..core.models import Session

    with session_scope(engine) as db:
        row = db.get(Session, session_id)
        if row is None:
            return
        row.handed_from_turn = turn
        db.add(row)
        db.commit()
