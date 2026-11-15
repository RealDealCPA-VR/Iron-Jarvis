"""What a turn in a confirmed client's folders is told (v1.499.0, builder A).

The client block joins the STABLE head (``_Grounding`` slot ``client``,
``project + folder_rules + lessons + client + index``), BEFORE compaction and
the planner in both lanes, and in agent runs (after ``apply_to_prompt``) and
terminal help (after the lessons). Hop ``client`` is REQUIRED, bounded at
``CLIENT_HOP_MAX_S``.

Only a CONFIRMED client's own folders get it: a waiting name, a declined
client, a folder no client claims and a turn with no folder get "" (so the
prompt is byte-identical to a build without clients). Nothing coarse that
changes by the day (ages, counts) is in it, so the cached head stays put.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_
from sqlmodel import select

from ...core.db import session_scope
from .. import keys
from ..models import CLIENT_FOLDER_ADDED, WorkEntry
from . import CLIENT_BLOCK_CHARS, names, store

log = logging.getLogger(__name__)

#: The caps inside the block (its whole size is ``CLIENT_BLOCK_CHARS``).
MAX_PREFERENCE_LINES = 12
MAX_OTHER_FOLDERS = 4
SOURCE = "client preferences"


def _safe(line: str) -> str:
    """The line, or "" when the scanner reads it as an instruction."""
    from ...core.promptguard import scan_context

    text = " ".join(str(line or "").split())
    if not text:
        return ""
    res = scan_context(text, source=SOURCE, cap=None)
    return "" if res.blocked else (res.text or text)


def _may_recall_local(local: bool | None) -> bool:
    if local is not None:
        return bool(local)
    try:
        from ...providers.egress import may_recall_local_only_chats

        return bool(may_recall_local_only_chats())
    except Exception:  # noqa: BLE001 — unsure: keep them home (fail closed)
        return False


def shared_work(engine: Any, key: str) -> bool:
    """:func:`_shared_work` for the client list (one rule for both)."""
    return _shared_work(engine, key)


def _shared_work(engine: Any, key: str) -> bool:
    """Does this folder hold at least one piece of work that is neither Local
    only nor kept home (so a cloud turn may hear its path)?"""
    W = WorkEntry
    prefix = key if key.endswith("/") else key + "/"
    with session_scope(engine) as db:
        n = db.exec(
            select(func.count()).select_from(W).where(
                or_(W.folder_key == key, W.folder_key.startswith(prefix, autoescape=True)),  # type: ignore[attr-defined]
                W.local_only == False,  # noqa: E712
                W.keep_home == False,  # noqa: E712
            )
        ).one()
    return int(n or 0) > 0


def _preference_lines(engine: Any, client_id: str, may_local: bool) -> list[str]:
    prefs = store.preferences(engine, client_id)
    lo_ids: "frozenset[str] | None" = frozenset()
    if not may_local and any(p.get("thread_id") for p in prefs):
        try:
            from ...providers.egress import local_only_thread_ids

            lo_ids = local_only_thread_ids(engine)
        except Exception:  # noqa: BLE001 — unreadable: withhold (fail closed)
            lo_ids = None
    lines = []
    for p in prefs:
        if not may_local:
            if p.get("local_only"):
                continue  # kept in a Local only turn
            tid = str(p.get("thread_id") or "")
            if tid and (lo_ids is None or tid in lo_ids):
                continue  # its source chat is Local only NOW (or cannot be asked)
        line = _safe(f"- {p.get('text') or ''}")
        if line:
            lines.append(line)
        if len(lines) >= MAX_PREFERENCE_LINES:
            break
    return lines


def _folder_lines(platform: Any, cmap: Any, root: Any, folder: object, may_local: bool) -> list[str]:
    from ..index import covered
    from ..models import WorkForgotten

    engine = platform.engine
    turn_key = keys.folder_key(keys.home_folder(str(folder or "").strip()))
    how = {f["key"]: f["how"] for f in store.folder_rows(engine, root.client_id)}
    with session_scope(engine) as db:
        gone = [str(k) for k in db.exec(select(WorkForgotten.folder_key)) if k]
    lines = []
    for other in cmap.roots_of(root.client_id):
        if other.key == root.key or keys.inside(turn_key, other.key):
            continue  # the turn's own folder
        if any(covered(other.key, g) for g in gone):
            continue  # forgotten: still the client's, never spoken of
        if not may_local and how.get(other.key) != CLIENT_FOLDER_ADDED:
            try:
                if not _shared_work(engine, other.key):
                    continue  # only Local only work there: its path stays home
            except Exception:  # noqa: BLE001 — unsure: leave it out
                continue
        line = _safe(f"- {other.folder}")
        if line:
            lines.append(line)
        if len(lines) >= MAX_OTHER_FOLDERS:
            break
    return lines


def _assemble(name: str, prefs: list[str], folders: list[str]) -> str:
    pos = names.possessive(name)
    head = [
        f"# Client: {name}",
        f"This chat's folder belongs to {name}, a client the owner confirmed.",
    ]
    tail = [f"A preference the owner states for {name} is kept for {name} only, never with remember_preference."]

    def build(p: list[str], f: list[str]) -> str:
        lines = list(head)
        if p:
            lines.append(
                f"What the owner keeps for {name} (follow these for {pos} work unless the owner says "
                f"otherwise in this chat; they hold for {name} only):"
            )
            lines += p
        if f:
            lines.append(f"{pos} other folders:")
            lines += f
        lines += tail
        return "\n".join(lines)

    p, f = list(prefs), list(folders)
    text = build(p, f)
    while len(text) > CLIENT_BLOCK_CHARS and (f or p):
        if f:
            f.pop()
        else:
            p.pop()
        text = build(p, f)
    return text if len(text) <= CLIENT_BLOCK_CHARS else ""


def client_block(platform: Any, *, folder: str, local: bool | None = None) -> str:
    """:func:`client_block_or_raise`, "" on any error. For a reader that
    shows the block (the client page's "told" preview). A PROMPT seam calls
    :func:`client_block_or_raise` instead, so a block that could not be read
    is named on its receipt and logged, never dropped in silence (the
    independent review). BLOCKING; NEVER RAISES."""
    try:
        return client_block_or_raise(platform, folder=folder, local=local)
    except Exception:  # noqa: BLE001 — never raises
        log.debug("clients: the client block could not be built", exc_info=True)
        return ""


def client_block_or_raise(platform: Any, *, folder: str, local: bool | None = None) -> str:
    """The client block for a turn whose ``folder`` resolves to a CONFIRMED
    client (``store.load_map(...).client_of(folder)``); "" otherwise (no
    folder, no client, a waiting or declined name). RAISES (``ClientsUnreadable``
    or anything else) when the map or the preferences cannot be read: "no
    client here" and "could not read" are never the same answer. BLOCKING.
    Its only shape::

        # Client: Hartleys
        This chat's folder belongs to Hartleys, a client the owner confirmed.
        What the owner keeps for Hartleys (follow these for Hartleys' work unless the owner says otherwise in this chat; they hold for Hartleys only):
        - Use cash basis.
        Hartleys' other folders:
        - D:\\Scans\\Hartleys
        A preference the owner states for Hartleys is kept for Hartleys only, never with remember_preference.

    Caps: :data:`MAX_PREFERENCE_LINES`, :data:`MAX_OTHER_FOLDERS`,
    ``CLIENT_BLOCK_CHARS``. Every line through ``promptguard.scan_context
    (source='client preferences')``; a flagged line is dropped. A preference
    with ``local_only``, or whose source chat is NOW in
    ``egress.local_only_thread_ids`` (``None`` means withhold), is told only
    when ``egress.may_recall_local_only_chats()``. ``local``: None reads the
    turn's own scope; False is a cloud turn (the client page's "told"
    preview); True a turn that stays on this network. Other folders are
    listed in a cloud turn only when they hold work that is not Local only or
    kept home, or when the owner added them."""
    engine = getattr(platform, "engine", None)
    if engine is None or not str(folder or "").strip():
        return ""
    cmap = store.load_map(engine)
    root = cmap.client_of(folder)
    if root is None:
        return ""
    name = " ".join(str(root.name or "").split())
    if not name or _safe(f"# Client: {name}") != f"# Client: {name}":
        return ""  # a name that cannot be said safely: say nothing
    may_local = _may_recall_local(local)
    prefs = _preference_lines(engine, root.client_id, may_local)
    try:
        folders = _folder_lines(platform, cmap, root, folder, may_local)
    except Exception:  # noqa: BLE001 — the paths are extra; the preferences are not
        log.debug("clients: the other folders could not be read", exc_info=True)
        folders = []
    return _assemble(name, prefs, folders)


__all__ = ["MAX_OTHER_FOLDERS", "MAX_PREFERENCE_LINES", "client_block", "client_block_or_raise",
           "shared_work"]
