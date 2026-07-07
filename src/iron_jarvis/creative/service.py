"""Creative gallery service — list and resolve the media Iron Jarvis has made.

The gallery is a VIEW over the artifact store: pixio generations save into it
via the artifact sink (tools/pixio.py), computer-use screenshots were already
there, and uploads land there too. One durable place, already wired to the
``artifact.generated`` live event.
"""

from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.logging import get_logger

log = get_logger("creative")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}

#: Artifact ``kind`` values that count as media even without a media extension.
_MEDIA_KINDS = {"image", "video", "audio", "screenshot"}


def media_kind(name: str) -> str | None:
    """'image' | 'video' | 'audio' from a filename's extension, else None."""
    ext = Path(str(name)).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def mime_for(name: str) -> str:
    guessed, _ = mimetypes.guess_type(str(name))
    return guessed or "application/octet-stream"


#: Longest edge of a generated thumbnail.
THUMB_SIZE = 512
#: Cache cap — oldest thumbs are pruned past this (cheap bound, not an LRU).
_THUMB_CACHE_MAX = 2000

#: At most this many thumbnails RENDER at once, process-wide. A 200-tile
#: gallery grid fires 200 near-simultaneous /creative/thumb requests; without
#: this bound each cache miss forks its own ffmpeg/Pillow job and the box
#: grinds. Waiters re-check the cache once they get a slot, so a stampede on
#: ONE key collapses to a handful of renders instead of two hundred.
_thumb_render_slots = threading.BoundedSemaphore(4)


def thumbnail_for(platform, src: Path, *, size: int = THUMB_SIZE) -> Path | None:
    """A small cached JPEG preview for a media file, or ``None`` when one
    can't be made (audio, SVG, video without ffmpeg, decode failure) — the
    caller/UI falls back to the original file or a glyph. Cache key includes
    mtime+size so an edited file re-thumbnails; cache lives under
    ``home/creative-thumbs`` and is size-capped."""
    import hashlib
    import os
    import shutil
    import subprocess
    import uuid

    kind = media_kind(src.name)
    if kind not in ("image", "video") or src.suffix.lower() == ".svg":
        return None
    try:
        st = src.stat()
    except OSError:
        return None
    cache_dir = Path(platform.config.home) / "creative-thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{src}|{st.st_mtime_ns}|{st.st_size}|{size}".encode("utf-8", "replace")
    ).hexdigest()
    out = cache_dir / f"{key}.jpg"
    if out.is_file():
        return out

    with _thumb_render_slots:
        # Re-check inside the slot: a request that held it a moment ago may
        # have just published this exact thumbnail.
        if out.is_file():
            return out
        # Render into a UNIQUE temp file, then publish atomically with
        # os.replace — a concurrent reader can never see a half-written JPEG.
        # The tmp keeps a .jpg suffix so ffmpeg picks the JPEG encoder from it.
        tmp = cache_dir / f"{key}.{uuid.uuid4().hex}.tmp.jpg"
        try:
            if kind == "image":
                from PIL import Image

                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((size, size))
                    im.save(tmp, "JPEG", quality=82)
            else:  # video — grab a frame with ffmpeg when the box has it
                ff = shutil.which("ffmpeg")
                if not ff:
                    return None
                proc = None
                for seek in ("1", "0"):  # 1s in; retry at 0 for very short clips
                    proc = subprocess.run(
                        [ff, "-y", "-ss", seek, "-i", str(src), "-frames:v", "1",
                         "-vf", f"scale='min({size},iw)':-2", str(tmp)],
                        capture_output=True, timeout=30,
                    )
                    if tmp.is_file() and tmp.stat().st_size > 0:
                        break
                else:  # both seeks produced nothing — say so, don't just 404
                    stderr = (proc.stderr or b"")[-300:].decode("utf-8", "replace") if proc else ""
                    log.warning("thumbnail: ffmpeg produced no frame for %s: %s", src, stderr)
            if not (tmp.is_file() and tmp.stat().st_size > 0):
                return None
            try:
                os.replace(tmp, out)  # atomic publish — readers see whole files only
            except OSError:
                # A concurrent renderer of the SAME key just won and a reader may
                # hold `out` open (Windows share semantics block replace-over-open).
                # Its thumbnail is equally complete — serve that one.
                if not out.is_file():
                    raise
        except subprocess.TimeoutExpired:
            log.warning("thumbnail: ffmpeg timed out on %s", src)
            return None
        except Exception as exc:  # noqa: BLE001 — a bad file just gets no thumbnail
            log.warning("thumbnail: could not render %s: %s", src, exc)
            return None
        finally:
            try:  # no-op after a successful replace; cleans up every failure path
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    try:  # bound the cache — prune the oldest fifth when past the cap
        # In-flight *.tmp.jpg files are excluded from the cap sweep: pruning
        # one would yank a render out from under the thread writing it. But
        # STRAY tmps (an AV scanner held the handle when the finally-unlink
        # ran, or a crash mid-render) would otherwise live forever — collect
        # any older than 10 minutes; no render takes remotely that long.
        import time as _time

        for stray in cache_dir.glob("*.tmp.jpg"):
            try:
                if _time.time() - stray.stat().st_mtime > 600:
                    stray.unlink(missing_ok=True)
            except OSError:
                continue
        entries = [p for p in cache_dir.glob("*.jpg") if ".tmp." not in p.name]
        if len(entries) > _THUMB_CACHE_MAX:
            entries.sort(key=lambda p: p.stat().st_mtime)
            for old in entries[: _THUMB_CACHE_MAX // 5]:
                try:
                    # Per-file tolerance: on Windows a thumb being streamed by a
                    # concurrent FileResponse raises PermissionError on unlink —
                    # skip it and keep pruning instead of aborting the sweep.
                    old.unlink(missing_ok=True)
                except OSError:
                    continue
    except OSError:  # pragma: no cover - pruning is best-effort
        pass
    return out


def list_media(platform, *, limit: int = 200) -> list[dict[str, Any]]:
    """Every media artifact, newest first: pixio generations, screenshots,
    uploads — anything in the store that IS media (by kind or extension)."""
    from ..artifacts.models import ArtifactRecord

    limit = max(1, min(int(limit), 1000))
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(ArtifactRecord).order_by(
                    ArtifactRecord.created_at.desc()  # type: ignore[attr-defined]
                )
            )
        )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        if len(items) >= limit:
            break
        kind = media_kind(r.path) or (
            "image" if r.kind == "screenshot" else r.kind if r.kind in _MEDIA_KINDS else None
        )
        if kind is None:
            continue
        if r.name in seen:  # one card per artifact name — the store versions it
            continue
        seen.add(r.name)
        items.append(
            {
                "name": r.name,
                "version": r.version,
                "media": kind,
                "kind": r.kind,
                "filename": Path(r.path).name,
                "size": r.size,
                "session_id": r.session_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "url": f"/creative/file/{r.name}",
            }
        )
    return items
