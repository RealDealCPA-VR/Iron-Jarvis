"""Which finished files go back with an answer (10-b).

ONE reading, two doors: a paired computer's answer (``agents/task_door``)
and the phone's reply (``comm/inbound``) pick the files a run made the same
way, so a file outside the folders the work was done in never leaves this
PC by either door.

* A run's files are read off its own ledger (``outcome.session_result(...,
  team=True)``: the files its tools created or changed, its workers' too),
  never from the model's closing words; :func:`files_made`.
* A plain chat turn's files are its ``documents`` (the files its own
  successful tool calls created or changed, both chat lanes,
  ``chat_turn.reported_document_paths``): ``ToolInvocation`` rows carry no
  turn id, so the turn's own record is that list; :func:`pick`.
* Only a file INSIDE one of the work's folders is picked, its path resolved
  and any link followed first (the workspace check every file tool uses,
  ``tools.base.safe_path``). A path outside is left out and named.
* Then the caps (:class:`Caps`): how many, how large each, how large in all.
  What does not fit is named with the reason, never dropped in silence.

Everything here is BLOCKING (rows and the disk): call it through
``asyncio.to_thread``. Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging import get_logger

log = get_logger("agents.reply_files")

_MB = 1024 * 1024


@dataclass(frozen=True)
class Caps:
    """How many files, how large each and (when set) how large in all."""

    count: int = 5
    each: int = 20 * _MB
    total: int | None = None


#: The phone's caps (10-b): at most five files, 20 MB each (Telegram's own
#: limit for a bot is 50 MB; email adds its own 20 MB in all).
PHONE_CAPS = Caps(count=5, each=20 * _MB, total=None)


@dataclass
class Picked:
    """The files picked, and the ones left out with why (names only)."""

    files: list[Path] = field(default_factory=list)
    #: ``(name, why)`` for every file not picked, in order.
    left: list[tuple[str, str]] = field(default_factory=list)
    #: The bytes picked, in all.
    total: int = 0


def _mb(n: float) -> str:
    mb = float(n) / _MB
    return f"{mb:.0f} MB" if mb >= 1 else f"{max(mb, 0.1):.1f} MB"


def inside_a_folder(raw: str, folders: list[Path]) -> Path | None:
    """``raw`` resolved (links followed) when it is inside one of
    ``folders``, else ``None``: the workspace check every file tool uses
    (``tools.base.safe_path``). Never raises."""
    from ..tools.base import safe_path

    for folder in folders:
        try:
            return safe_path(folder, raw)
        except Exception:  # noqa: BLE001 — outside this one (or unreadable)
            continue
    return None


def pick(paths: list[str], folders: list[Path], caps: Caps) -> Picked:
    """The files of ``paths`` that are inside ``folders`` and fit ``caps``,
    in order, each once; the rest named with why. BLOCKING. Never raises."""
    out = Picked()
    seen: set[str] = set()
    for raw in paths or []:
        raw = str(raw or "").strip()
        if not raw:
            continue
        name = Path(raw).name or raw
        inside = inside_a_folder(raw, folders) if folders else None
        if inside is None:
            out.left.append((name, "it is not in the folder this work was done in"))
            continue
        key = str(inside).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if not inside.is_file():
                out.left.append((name, "it is not on this PC any more"))
                continue
            size = inside.stat().st_size
        except OSError:
            out.left.append((name, "it could not be read on this PC"))
            continue
        if len(out.files) >= caps.count:
            out.left.append((name, f"at most {caps.count} files go back with one answer"))
            continue
        if size > caps.each:
            out.left.append((name, f"it is {_mb(size)}, and the most sent is {_mb(caps.each)}"))
            continue
        if caps.total is not None and out.total + size > caps.total:
            out.left.append((name, f"the files sent with one answer are at most {_mb(caps.total)} in all"))
            continue
        out.files.append(inside)
        out.total += size
    return out


def run_folders(engine: Any, session_id: str) -> list[Path]:
    """The folders the run and its workers worked in (BLOCKING): the run's
    own ``workspace_path`` and each delegated member's (``mission._walk``,
    the same tree ``session_result(team=True)`` reads its files from).
    ``[]`` on any error (then nothing is picked)."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session
        from .mission import _walk

        with session_scope(engine) as db:
            root = db.get(Session, session_id)
            members, _runs = _walk(db, session_id)
            rows = ([root] if root is not None else []) + list(members)
            spelled = [str(getattr(r, "workspace_path", "") or "").strip() for r in rows]
    except Exception:  # noqa: BLE001 — unreadable: no folder, nothing picked
        log.warning("reply files: the run's folders could not be read", exc_info=True)
        return []
    out: list[Path] = []
    for text in spelled:
        if text and Path(text) not in out:
            out.append(Path(text))
    return out


def files_made(engine: Any, session_id: str, caps: Caps) -> Picked:
    """What the run ``session_id`` (and its workers) made, read off its own
    ledger, inside the folders it worked in, within ``caps``. BLOCKING.
    Never raises (a run that cannot be read picks nothing)."""
    try:
        from .outcome import session_result

        result = session_result(engine, session_id, team=True)
        docs = [str(p) for p in (result.get("documents") or []) if p] if isinstance(result, dict) else []
    except Exception:  # noqa: BLE001
        log.warning("reply files: the run's record could not be read", exc_info=True)
        return Picked()
    if not docs:
        return Picked()
    return pick(docs, run_folders(engine, session_id), caps)
