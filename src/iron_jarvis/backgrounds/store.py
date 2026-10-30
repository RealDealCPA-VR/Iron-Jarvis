"""Downloaded backgrounds on this PC (v1.410.0).

* WHERE: ``<userData>/backgrounds/<id>.html`` — the desktop's folder beside the
  state home (the same "is there a ``logs`` folder beside the home" rule the
  freeze trace uses), so a backup never carries them; a dev or test home keeps
  them in ``<home>/backgrounds`` and ``create_backup`` leaves that folder out.
  They are re-downloadable: nothing in them is the user's.
* DOWNLOAD: only the one file the catalog names, from the address pinned to
  the running version's tag (``catalog.source_url``); redirects are NOT
  followed, the body is capped at the catalog's size, the whole call has a
  timeout, and bytes whose sha256 is not the catalog's are refused in a plain
  sentence and never written. A file already on disk that matches is never
  fetched again. After an update a file whose sha256 no longer matches the
  catalog reads ``outdated``: it is not served, and the next use fetches the
  new one.
* SERVE: :func:`read_verified` hands out a file only while it still hashes to
  the catalog (the hash is cached per size + mtime).
* Every function here blocks (disk, network): routes call them from FastAPI's
  threadpool (sync ``def`` routes), never on the event loop.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import catalog

log = logging.getLogger("ironjarvis.backgrounds")

FOLDER = "backgrounds"
#: A hard ceiling whatever the catalog says (the files are a few KB).
MAX_BYTES = 100_000
CONNECT_TIMEOUT_S = 5.0
TOTAL_TIMEOUT_S = 20.0
#: Dev / test only: download from a LOOPBACK http server instead of GitHub
#: (the e2e serves the repository folder). Anything else is ignored.
BASE_ENV = "IRONJARVIS_BACKGROUNDS_BASE"

NOT_FOUND = "There is no background called that."
MISMATCH = (
    "The downloaded background did not match the one this version of Iron Jarvis "
    "expects, so it was not saved."
)
TOO_BIG = "The download was larger than this background should be, so it was not saved."
REDIRECTED = (
    "The download address answered with a redirect. Iron Jarvis only downloads "
    "backgrounds from their own address, so nothing was saved."
)
UNREACHABLE = "Iron Jarvis could not reach GitHub to download this background. Check the internet connection and try again."
TIMED_OUT = "The download took too long, so it was stopped. Try again in a moment."


class BackgroundError(Exception):
    """A refusal in one plain sentence; ``status`` is the HTTP code to answer."""

    def __init__(self, words: str, status: int = 502):
        super().__init__(words)
        self.words = words
        self.status = status


def folder_for(home: str | os.PathLike[str]) -> Path:
    home_path = Path(home)
    if (home_path.parent / "logs").is_dir():
        return home_path.parent / FOLDER
    return home_path / FOLDER


def file_for(home: str | os.PathLike[str], bg_id: str) -> Path:
    return folder_for(home) / f"{bg_id}.html"


_hash_cache: dict[str, tuple[int, int, str]] = {}
_hash_lock = threading.Lock()
_download_locks: dict[str, threading.Lock] = {}


def _sha_of(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _hash_lock:
        hit = _hash_cache.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    with _hash_lock:
        _hash_cache[key] = (st.st_size, st.st_mtime_ns, digest)
    return digest


def state_of(home: str | os.PathLike[str], bg: catalog.Background) -> dict[str, Any]:
    path = file_for(home, bg.id)
    try:
        on_disk = path.stat().st_size
    except OSError:
        on_disk = None
    if on_disk is None:
        state = "absent"
    else:
        state = "ready" if _sha_of(path) == bg.sha256 else "outdated"
    return {
        "id": bg.id,
        "name": bg.name,
        "size": bg.size,
        "state": state,
        "bytes": on_disk or 0,
    }


def list_state(home: str | os.PathLike[str]) -> dict[str, Any]:
    rows = [state_of(home, bg) for bg in catalog.BACKGROUNDS]
    return {
        "backgrounds": rows,
        "total_bytes": sum(r["bytes"] for r in rows),
    }


def _source(bg_id: str) -> str:
    base = (os.environ.get(BASE_ENV) or "").strip().rstrip("/")
    if base:
        parts = urlsplit(base)
        host = parts.hostname or ""
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parts.scheme == "http" and loopback:
            return f"{base}/backgrounds/{bg_id}/index.html"
        log.warning("%s is not a loopback http address; using GitHub", BASE_ENV)
    return catalog.source_url(bg_id)


def _fetch(url: str, cap: int) -> bytes:
    """GET ``url``: no redirects, a connect + total timeout, at most ``cap``
    bytes. Raises :class:`BackgroundError` in words."""
    import httpx

    from ..core import http as _http

    timeout = httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    chunks: list[bytes] = []
    got = 0
    try:
        with _http.client(follow_redirects=False, timeout=timeout) as client:
            with client.stream("GET", url) as res:
                if 300 <= res.status_code < 400:
                    raise BackgroundError(REDIRECTED)
                if res.status_code == 404:
                    raise BackgroundError(
                        "GitHub does not have this background for this version of Iron Jarvis yet. "
                        "If the update was just published, try again in a few minutes."
                    )
                if res.status_code != 200:
                    raise BackgroundError(
                        f"GitHub answered {res.status_code} instead of the background. Try again later."
                    )
                for chunk in res.iter_bytes():
                    got += len(chunk)
                    if got > cap:
                        raise BackgroundError(TOO_BIG)
                    chunks.append(chunk)
    except BackgroundError:
        raise
    except httpx.TimeoutException as exc:
        raise BackgroundError(TIMED_OUT) from exc
    except httpx.HTTPError as exc:
        raise BackgroundError(UNREACHABLE) from exc
    return b"".join(chunks)


#: The fetcher; tests replace it (never the network in the suite).
FETCH: Callable[[str, int], bytes] = _fetch


def download(home: str | os.PathLike[str], bg_id: str) -> dict[str, Any]:
    """Make background ``bg_id`` ready on this PC. A matching file already on
    disk is kept and nothing is fetched (``fetched: False``)."""
    bg = catalog.get(bg_id)
    if bg is None:
        raise BackgroundError(NOT_FOUND, 404)
    with _hash_lock:
        lock = _download_locks.setdefault(bg.id, threading.Lock())
    with lock:
        row = state_of(home, bg)
        if row["state"] == "ready":
            return {**row, "fetched": False}
        cap = min(MAX_BYTES, bg.size) if bg.size > 0 else MAX_BYTES
        body = FETCH(_source(bg.id), cap)
        if len(body) > cap:
            raise BackgroundError(TOO_BIG)
        if hashlib.sha256(body).hexdigest() != bg.sha256:
            raise BackgroundError(MISMATCH)
        path = file_for(home, bg.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        return {**state_of(home, bg), "fetched": True}


def delete(home: str | os.PathLike[str], bg_id: str) -> dict[str, Any]:
    bg = catalog.get(bg_id)
    if bg is None:
        raise BackgroundError(NOT_FOUND, 404)
    path = file_for(home, bg.id)
    try:
        freed = path.stat().st_size
        path.unlink()
    except FileNotFoundError:
        freed = 0
    except OSError as exc:
        raise BackgroundError(f"The background file could not be removed: {exc.strerror or 'it is in use'}.", 409) from exc
    with _hash_lock:
        _hash_cache.pop(str(path), None)
    return {"id": bg.id, "freed_bytes": freed}


def read_verified(home: str | os.PathLike[str], bg_id: str) -> bytes | None:
    """The saved file's bytes, only while they still match the catalog."""
    bg = catalog.get(bg_id)
    if bg is None:
        return None
    path = file_for(home, bg.id)
    try:
        body = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(body).hexdigest() != bg.sha256:
        return None
    return body
