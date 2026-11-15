"""Where a layered-memory entry was written, and who may be told it
(v1.501.0, north-star 1.2).

``memory_write`` used to drop ``scope_id``: two runs writing
``("session", "plan")`` shared one row, and an entry written while working in
one client's folder was recalled into any other turn. Now:

* THE KEY. A ``session`` entry is keyed by the writing session
  (:func:`session_tag`: the run's id; ``chat:<thread>`` for a saved chat). A
  ``project`` entry is keyed by the turn's project, else by its folder
  (``folder:<key>``). ``user`` and ``org`` stay one row per key, as before.
* THE STAMP. Every ``memory_write`` row carries ``origin_session`` and
  ``origin_folder`` (the folder the CLIENT WALL keyed the turn by,
  ``workmap.clients.wall.current().folder_key``; "" = none).
* THE TELLING (:func:`told`, every reader of the model's side: memory recall
  in ``fabric._memory``, ``memory_search``, ``memory_read``). A stamped row is
  told only in its scope (a ``session`` row in its own session; a ``project``
  row in its own project or folder) and only where the wall's ``may_tell``
  allows its folder and text. A wall that cannot be read tells NO stamped row
  (fail closed, as ``fabric._sessions``). The owner's own screens
  (``wall.owner_screen()``) are not walled.
* A row with no origin (written before this change, or by the owner's own
  Memory page / CLI) is told as before: the owner's existing memory is never
  hidden.

Text and one cached map read: nothing here opens a file. BLOCKING (the map
read); never raises.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The prefix of a ``project`` entry's scope when the turn has no project.
FOLDER_SCOPE = "folder:"


def chat_tag(thread_id: Any) -> str:
    """The session a CHAT's ``session`` entries are keyed by:
    ``chat:<thread>`` for a saved chat, "chat" for an unsaved one. The chat
    lanes pass it as their grounding call's ``memory_session`` so a chat
    recalls its own entries (and no other chat's). Never raises."""
    thread = str(thread_id or "").strip()
    return f"chat:{thread}" if thread else "chat"


def session_tag(ctx: Any) -> str:
    """The writing session as a ``session`` entry is keyed by: the run's
    id; :func:`chat_tag` for a chat turn (every chat turn's ``session_id``
    is "chat"). Never raises."""
    sid = str(getattr(ctx, "session_id", "") or "").strip()
    if sid == "chat":
        return chat_tag(getattr(ctx, "thread_id", ""))
    return sid


def _turn_folder() -> str:
    try:
        from ..workmap.clients import wall

        return str(wall.current().folder_key or "")
    except Exception:  # noqa: BLE001 — no wall: no folder
        return ""


def writer_scope(layer: str, ctx: Any) -> str | None:
    """The ``scope_id`` a ``memory_write`` from ``ctx`` keys its row by
    (module docstring, "THE KEY"). None = one row per key, as before."""
    if layer == "session":
        return session_tag(ctx) or None
    if layer == "project":
        project = str(getattr(ctx, "project_id", "") or "").strip()
        if project:
            return project
        folder = _turn_folder()
        return FOLDER_SCOPE + folder if folder else None
    return None


def writer_origin(ctx: Any) -> tuple[str, str]:
    """``(origin_session, origin_folder)`` for a ``memory_write`` from ``ctx``."""
    return session_tag(ctx), _turn_folder()


def stamped(record: Any) -> bool:
    """Did ``memory_write`` write this row (since v1.501.0)?"""
    return getattr(record, "origin_session", None) is not None


def _in_scope(record: Any, *, session: str | None, project_id: str | None, folder: str) -> bool:
    layer = str(getattr(record, "layer", "") or "")
    scope = getattr(record, "scope_id", None)
    if scope is None:
        return True
    scope = str(scope)
    if layer == "session":
        return bool(session) and scope == str(session)
    if layer == "project":
        if scope.startswith(FOLDER_SCOPE):
            return bool(folder) and scope == FOLDER_SCOPE + folder
        return bool(project_id) and scope == str(project_id)
    return True


def told(
    engine: Any,
    records: Iterable[Any],
    *,
    session: str | None = None,
    project_id: str | None = None,
) -> list[bool]:
    """One verdict per record, in order: may the turn in flight be told it?
    (module docstring, "THE TELLING"). ``session`` / ``project_id`` are the
    turn's own (a ``session`` / ``project`` row outside them is not told).
    Never raises (an error tells no stamped row)."""
    rows = list(records or ())
    if not rows:
        return []
    try:
        from ..workmap.clients import store as client_store
        from ..workmap.clients import wall

        if wall.on_owner_screen():
            return [True] * len(rows)
        if not any(stamped(r) for r in rows):
            return [True] * len(rows)
        turn = wall.current()
        folder = str(turn.folder_key or "")
        try:
            cmap = client_store.load_map(engine) if engine is not None else None
            if not isinstance(cmap, client_store.ClientMap):
                cmap = None
        except Exception:  # noqa: BLE001 — unreadable: no stamped row (fail closed)
            log.debug("memory: the client record could not be read", exc_info=True)
            cmap = None
        out: list[bool] = []
        for r in rows:
            if not stamped(r):
                out.append(True)
                continue
            # A record that cannot be read tells nothing to a turn that read
            # fine; a turn that already failed closed keeps to exactly its
            # folder (``may_tell`` rule 1 needs no record).
            if (cmap is None and turn.ok) or not _in_scope(
                r, session=session, project_id=project_id, folder=folder
            ):
                out.append(False)
                continue
            out.append(wall.may_tell(turn, cmap or client_store.EMPTY_MAP,
                                     folder=getattr(r, "origin_folder", "") or "",
                                     snippet=str(getattr(r, "text", "") or "")))
        return out
    except Exception:  # noqa: BLE001 — unsure: only rows with no origin
        log.debug("memory: the client wall could not be read", exc_info=True)
        return [not stamped(r) for r in rows]


__all__ = [
    "FOLDER_SCOPE",
    "chat_tag",
    "session_tag",
    "stamped",
    "told",
    "writer_origin",
    "writer_scope",
]
