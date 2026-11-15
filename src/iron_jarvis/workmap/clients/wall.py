"""THE WALL (v1.499.0, builder B): the one rule for what each turn may be
told about clients' work.

It decides the work block (both parts), memory-recall passages and run
summaries, the history search tool and the client chat tools. It is stricter
than before the moment the update lands, before the owner answers anything:
folders under a Clients-type folder and waiting homes are walled too. If the
client tables cannot be read it fails CLOSED (``TurnWall.ok`` False: the work
block keeps to this exact folder, memory recall drops every chat passage).

The rule (``may_tell``) is written out in the package docstring
(``workmap/clients/__init__.py``, "The wall"). Text only: nothing here
touches the disk (pinned with the disk calls patched to raise).

The turn in flight is a ContextVar, :data:`CURRENT`, mirroring
``providers/egress.LOCAL_ONLY`` and ``egress.scoped_frames``: the chat lanes
set it around the whole turn (:func:`turn_scope` for the POST lane,
:func:`scoped_frames` next to ``egress.scoped_frames`` for the stream lane),
and the agent runtime around a run, next to its Local only scope. Unset, it
answers :data:`NO_CLIENT`, so a prompt caller added later gets the no-client
rule (fail safe for facts). ``asyncio.to_thread`` and ``create_task`` copy
the context; ``run_in_executor`` does NOT (wrap it in
``contextvars.copy_context().run``). The owner's own screens (the Memory
page's recall, the search palette) wrap their calls in :func:`owner_screen`
and are not walled: a reader asks :func:`on_owner_screen` FIRST, and
otherwise filters with ``may_tell(current(), …)``.

STUB until builder B lands: ``turn_wall`` answers :data:`NO_CLIENT` and
``may_tell`` True (the Phase 1 and 2 rules, unchanged). The ContextVar and
its three scopes already work.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterable, Iterator

from . import NO_CLIENT, TurnWall
from .store import ClientMap

#: The wall of the turn in flight in THIS context.
CURRENT: ContextVar[TurnWall] = ContextVar("iron_jarvis_client_wall", default=NO_CLIENT)
#: True inside :func:`owner_screen` (the owner's own screens: not walled).
OWNER_SCREEN: ContextVar[bool] = ContextVar("iron_jarvis_client_owner_screen", default=False)


def turn_wall(engine: Any, folder: object, user_text: object) -> TurnWall:
    """The wall for one turn: its folder's owner (``client:<id>`` /
    ``waiting:<id>`` / ``folder:<key>`` / None, after ``keys.home_folder`` +
    ``keys.folder_key``), ``client`` for a confirmed owner, ``spans``, and
    ``named`` = the confirmed clients ``names.named_in(user_text,
    cmap.names)`` finds in the OWNER's own words (``_compose_recall_query
    (body.messages)``; for a run, ``session.task``), never the model's. A map
    that cannot be read gives ``ok=False`` with the turn's ``folder_key``.
    BLOCKING (``store.load_map``, cached). NEVER RAISES."""
    return NO_CLIENT


def may_tell(
    turn: TurnWall,
    cmap: ClientMap,
    *,
    folder: object,
    title: str = "",
    files: Iterable[str] = (),
    snippet: str = "",
) -> bool:
    """May ``turn`` be told about a row or passage whose folder is
    ``folder``? The rule, in this order (``title``, ``files`` stems and
    ``snippet`` feed NAMED ATTRIBUTION when the folder has no owner):

    1. ``not turn.ok``: only rows in exactly the turn's folder.
    2. The row owner is None: True.
    3. ``turn.owner`` is set: True only if the row owner is exactly
       ``turn.owner`` (a named set must equal ``{turn.owner}``).
    4. The row owner is confirmed: True only if it is in ``turn.named``.
    5. ``turn.spans``: False.
    6. Otherwise True.

    Pure and text only. NEVER RAISES (an error is False)."""
    return True


def current() -> TurnWall:
    """The wall of the turn in flight in THIS context (:data:`NO_CLIENT`
    when nothing set it). Never raises."""
    try:
        wall = CURRENT.get()
    except Exception:  # noqa: BLE001 — never raises
        return NO_CLIENT
    return wall if isinstance(wall, TurnWall) else NO_CLIENT


def on_owner_screen() -> bool:
    """Is the work in flight one of the owner's own screens
    (:func:`owner_screen`)? Never raises."""
    try:
        return OWNER_SCREEN.get() is True
    except Exception:  # noqa: BLE001 — never raises
        return False


def _reset(var: ContextVar[Any], token: Any, default: Any) -> None:
    try:
        var.reset(token)
    except (ValueError, LookupError, RuntimeError):  # a token from another context
        var.set(default)


@contextmanager
def turn_scope(turn: TurnWall | None) -> Iterator[TurnWall]:
    """Set :data:`CURRENT` to ``turn`` for the body of the ``with`` (reset
    after, however it ends). Not a TurnWall (None) sets :data:`NO_CLIENT`. A
    turn is never an owner screen, so the body also runs with
    :data:`OWNER_SCREEN` off."""
    wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
    token = CURRENT.set(wall)
    screen = OWNER_SCREEN.set(False)
    try:
        yield wall
    finally:
        _reset(OWNER_SCREEN, screen, False)
        _reset(CURRENT, token, NO_CLIENT)


async def scoped_frames(agen: AsyncIterator[Any], turn: TurnWall | None) -> AsyncIterator[Any]:
    """``agen`` iterated with ``turn`` set in the iterating task's context.

    For a turn that is a generator (the stream lane), beside
    ``egress.scoped_frames``: the wall is set when the first frame is asked
    for, so every task the turn creates from then on (the model pump, the tool
    rounds) copies it, and it is reset when the stream ends however it ends.
    Closing this generator closes ``agen`` at once (a dropped connection must
    reach the turn's own ``finally`` blocks, never at garbage collection)."""
    wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
    token = CURRENT.set(wall)
    screen = OWNER_SCREEN.set(False)
    try:
        async for frame in agen:
            yield frame
    finally:
        try:
            close = getattr(agen, "aclose", None)
            if close is not None:
                await close()
        finally:
            _reset(OWNER_SCREEN, screen, False)
            _reset(CURRENT, token, NO_CLIENT)


@contextmanager
def owner_screen() -> Iterator[None]:
    """The owner's own screens (``routes/learning.py`` recall,
    ``routes/search.py`` palette): readers inside the ``with`` filter
    nothing (:func:`on_owner_screen` is True). Reset after."""
    token = OWNER_SCREEN.set(True)
    try:
        yield
    finally:
        _reset(OWNER_SCREEN, token, False)


def thread_owners(engine: Any, cmap: ClientMap, ids: Iterable[Any]) -> dict[str, str] | None:
    """The FOLDER of each chat thread in ``ids`` (``{thread_id: folder}``,
    "" for one with none), for ``may_tell(folder=…)``: one bounded IN query
    over the saved ``setup_json`` folder (``harness.sessions.folder_of``),
    else the thread's project root. None when it cannot be read: the caller
    then drops every chat passage. BLOCKING. Never raises."""
    return {str(i): "" for i in ids if i}


def session_owners(engine: Any, cmap: ClientMap, ids: Iterable[Any]) -> dict[str, str] | None:
    """The same for agent runs (``{session_id: folder}``: the run's
    ``workspace_path``, else its project root; a scratch folder is "").
    None when it cannot be read. BLOCKING. Never raises."""
    return {str(i): "" for i in ids if i}


__all__ = [
    "CURRENT",
    "NO_CLIENT",
    "OWNER_SCREEN",
    "TurnWall",
    "current",
    "may_tell",
    "on_owner_screen",
    "owner_screen",
    "scoped_frames",
    "session_owners",
    "thread_owners",
    "turn_scope",
    "turn_wall",
]
