"""The browser's downloads, remembered — so Iron Jarvis knows where a file went.

v1.444.0, the user's request: "when the browser is needed and items are pulled
from a site like a download, the app should be aware of where the file was
downloaded and have the capability of working with these files in a safe way
and can rename, modify or move them."

Before this, a completed download reached the daemon (the add-on reports it,
:func:`~iron_jarvis.browser.extension_backend.download_bus_payload` verifies
the path is absolute and publishes ``browser.download_completed``) and was
then remembered in MEMORY, 16 at most, for one browser connection: a restart,
a reconnect or the seventeenth download forgot it, and no tool could list
them. :class:`DownloadLedger` keeps the verified ones on disk, bounded, with
where each file is NOW (a rename or move through ``download_manage`` updates
it), so a later turn — or the user — can find "the PDF I downloaded this
morning".

WHAT IS KEPT is what the browser said and the daemon verified: the absolute
path, the file name, the site it came from (scheme + host + path only — never
the query string, which is where download links carry their access tokens),
the type, the size, when. Nothing is read from the file. The list lives at
``<home>/downloads-seen.json`` (NOT under ``<home>/browser``, a protected root
that holds the add-on's key material), at most :data:`MAX_KEPT` entries,
newest kept, written atomically under a lock.

THE LIST IS ALSO THE SAFETY LINE for ``download_manage``: it acts only on a
file this list names (by its id or its current path). A model cannot point it
at an arbitrary file on the disk — only at something the user's own browser
downloaded.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: How many downloads are remembered (newest kept).
MAX_KEPT = 200
#: The file the list is kept in, under the state home.
FILE_NAME = "downloads-seen.json"
#: The bus event the add-on's verified completions arrive on.
EVENT_TYPE = "browser.download_completed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def site_of(url: Any) -> str:
    """``scheme://host/path`` of a download URL — never its query or fragment
    (download links carry access tokens there). ``""`` for anything else."""
    try:
        parts = urlsplit(str(url or ""))
    except Exception:  # noqa: BLE001
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path or ''}"[:300]


def _next_id(rows: list[dict[str, Any]]) -> str:
    """``dl_<n>``, one past the highest kept number (ids are never reused
    while their entry is kept)."""
    top = 0
    for row in rows:
        tail = str(row.get("id") or "").removeprefix("dl_")
        if tail.isdigit():
            top = max(top, int(tail))
    return f"dl_{top + 1}"


def same_path(a: str, b: str) -> bool:
    """Do two absolute paths name one file (Windows folds case)?"""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class DownloadLedger:
    """The kept list of the browser's verified downloads. Thread-safe;
    every method is BLOCKING (a small JSON file) — call it off the loop."""

    def __init__(self, home: str | Path) -> None:
        self.path = Path(home) / FILE_NAME
        self._lock = threading.Lock()

    # -- storage ------------------------------------------------------------
    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001 — a damaged list starts again, said in the log
            log.warning("downloads list unreadable; starting a new one", exc_info=True)
            return []
        rows = raw.get("downloads") if isinstance(raw, dict) else None
        return [r for r in rows or [] if isinstance(r, dict) and r.get("id") and r.get("path")]

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"downloads": rows[-MAX_KEPT:]}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- writes -------------------------------------------------------------
    def record(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Remember one VERIFIED completion (``local_path`` present); returns
        the entry, or ``None`` when the payload carries no verified path. A
        repeat of the same download (the click result and the event frame both
        carry it) updates the one entry instead of adding a second."""
        path = str((payload or {}).get("local_path") or "")
        if not path or not os.path.isabs(path):
            return None
        download_id = payload.get("download_id")
        entry = {
            "id": "",
            "download_id": download_id,
            "path": path,
            "original_path": path,
            "name": Path(path).name,
            "site": site_of(payload.get("final_url") or payload.get("source_url")),
            "mime": str(payload.get("mime") or "")[:120],
            "bytes": payload.get("bytes") if isinstance(payload.get("bytes"), int) else None,
            "at": str(payload.get("timestamp") or "") or _now(),
            "recorded_at": _now(),
        }
        with self._lock:
            rows = self._read()
            for row in rows:
                if same_path(row.get("original_path", ""), path) and (
                    download_id is None or row.get("download_id") in (None, download_id)
                ):
                    row.update({k: v for k, v in entry.items() if k not in ("id", "path")})
                    self._write(rows)
                    return dict(row)
            entry["id"] = _next_id(rows)
            rows.append(entry)
            self._write(rows)
            return dict(entry)

    def moved(self, entry_id: str, new_path: str) -> None:
        """The file of ``entry_id`` is now at ``new_path`` (a rename or move)."""
        with self._lock:
            rows = self._read()
            for row in rows:
                if row.get("id") == entry_id:
                    row["path"] = new_path
                    row["name"] = Path(new_path).name
                    self._write(rows)
                    return

    # -- reads --------------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        """Every kept download, newest first, each with ``exists`` (is the
        file still where the list says)."""
        with self._lock:
            rows = self._read()
        out = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["exists"] = os.path.isfile(item["path"])
            except OSError:
                item["exists"] = False
            out.append(item)
        return out

    def find(self, ref: str) -> dict[str, Any] | None:
        """The entry ``ref`` names: its id (``dl_3``), its current or original
        path, or — when exactly one kept download has it — its file name."""
        ref = str(ref or "").strip().strip('"')
        if not ref:
            return None
        rows = self.entries()
        for row in rows:
            if row.get("id") == ref:
                return row
        for row in rows:
            if same_path(row.get("path", ""), ref):
                return row
        for row in rows:
            if same_path(row.get("original_path", ""), ref):
                return row
        named = [r for r in rows if str(r.get("name", "")).lower() == Path(ref).name.lower()]
        if len(named) == 1 and not os.path.dirname(ref):
            return named[0]
        return None


def handler_for(ledger: DownloadLedger):
    """An event-bus handler that records every verified browser download.
    Never raises (a bus handler runs beside every other one)."""

    def _on_event(event: Any) -> None:
        try:
            if getattr(event, "type", "") != EVENT_TYPE:
                return
            ledger.record(dict(getattr(event, "payload", None) or {}))
        except Exception:  # noqa: BLE001
            log.warning("could not remember a browser download", exc_info=True)

    return _on_event
