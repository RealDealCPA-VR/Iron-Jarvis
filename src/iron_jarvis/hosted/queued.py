"""The messages QUEUED behind a running reply in Build › Code (v1.418.0, C4.8).

Typed while a reply runs and sent with Enter, a message WAITS here instead of
being refused: when the reply ends cleanly the daemon sends the first one as
the next message (the turn is the daemon's, so a closed page changes
nothing). A reply that ends any other way — Stop, a failure, an account's
limit card — sends nothing: the queue is HELD, and the page puts the words
back in the box (``GET …`` says ``queue_held``) — a queued message is never
sent after the user stopped, and never dropped without a word.

Kept per session in ``<home>/hosted/<id>/queue.json`` (the session's own
folder: it goes to the trash with the session). The in-memory list is the
truth while the daemon runs (every change happens on the loop, with no await
between the check and the change); each change is written in the background,
newest wins.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .events import TEXT_CHARS, clip, session_dir

log = logging.getLogger(__name__)

#: At most this many messages wait behind one reply.
MAX_QUEUED = 5
#: Files one queued message carries at most.
MAX_FILES = 20


def queue_path(home: Any, sid: str) -> Path:
    return session_dir(home, sid) / "queue.json"


def read(home: Any, sid: str) -> dict[str, Any]:
    """BLOCKING: ``{items, held}`` as saved ({} parts default)."""
    try:
        data = json.loads(queue_path(home, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": [], "held": False}
    items = []
    for it in data.get("items") or [] if isinstance(data, dict) else []:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            items.append({
                "id": it["id"],
                "text": str(it.get("text") or ""),
                "files": [str(f) for f in (it.get("files") or []) if isinstance(f, str)][:MAX_FILES],
            })
    return {"items": items[:MAX_QUEUED], "held": bool(isinstance(data, dict) and data.get("held"))}


class SessionQueue:
    """One session's queue (memory first, the file behind it)."""

    def __init__(self, home: Any, sid: str, saved: dict[str, Any] | None = None) -> None:
        self.home = home
        self.sid = sid
        saved = saved or {"items": [], "held": False}
        self.items: list[dict[str, Any]] = list(saved.get("items") or [])
        self.held = bool(saved.get("held"))
        self._version = 0
        self._written = 0
        self._lock = threading.Lock()

    def view(self) -> dict[str, Any]:
        return {"queued": [dict(i) for i in self.items], "queue_held": self.held and bool(self.items)}

    def add(self, text: str, files: list[str]) -> dict[str, Any]:
        item = {
            "id": f"q-{uuid.uuid4().hex[:10]}",
            "text": clip(text, TEXT_CHARS),
            "files": [clip(f, 500) for f in files][:MAX_FILES],
        }
        self.items.append(item)
        self._version += 1
        return item

    def remove(self, qid: str) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i["id"] != qid]
        if len(self.items) != before:
            self._version += 1
            if not self.items:
                self.held = False
            return True
        return False

    def clear(self) -> list[dict[str, Any]]:
        out, self.items, self.held = self.items, [], False
        self._version += 1
        return out

    def pop_first(self) -> dict[str, Any] | None:
        if not self.items:
            return None
        first = self.items.pop(0)
        self._version += 1
        return first

    def put_back(self, item: dict[str, Any]) -> None:
        self.items.insert(0, item)
        self._version += 1

    def hold(self) -> None:
        if self.items and not self.held:
            self.held = True
            self._version += 1

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        return self._version, {"items": [dict(i) for i in self.items], "held": self.held}

    def write(self, version: int, data: dict[str, Any]) -> None:
        """BLOCKING: save ``data`` unless a newer version was saved already."""
        with self._lock:
            if version < self._written:
                return
            path = queue_path(self.home, self.sid)
            try:
                if not data["items"] and not path.exists():
                    self._written = version
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
                self._written = version
            except OSError:
                log.warning("could not save a Code-view session's queued messages", exc_info=True)
