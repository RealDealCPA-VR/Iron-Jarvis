"""Files a remote agent sends back, landed safely in the workspace (v1.157.0).

Remote agents could only ever return TEXT: ``run()`` pulled a string out of the
reply and that was the whole contract. So a remote that produced a real .xlsx
could describe it and never hand it over.

THE RESPONSE SHAPE (``http-task``, and any kind whose body carries it):

    {
      "result": "Built the Q3 workbook.",
      "files": [
        {"name": "q3.xlsx", "content_b64": "UEsDBBQ..."},
        {"name": "notes.pdf", "url": "https://your-agent/artifacts/notes.pdf"}
      ]
    }

``result`` is unchanged, so an endpoint that adds ``files`` keeps working with
older builds and vice versa.

THIS MODULE IS THE TRUST BOUNDARY. Everything here is bytes chosen by a machine
on the other end of a network, being written to the user's disk, so the rules
are deliberately strict and every one of them is a real attack:

* **the name is never trusted** — ``../..`` , ``C:\\Windows\\...`` , a NUL, a
  1000-character name and a leading dot are all reduced to a plain basename,
  and the write still goes through ``safe_path`` so the workspace is a hard
  boundary rather than a convention;
* **a URL must live on the remote agent's OWN host** — otherwise the daemon
  becomes a fetcher for whatever address a reply names, which is an SSRF
  primitive pointed at the user's LAN;
* **sizes and counts are capped** before anything touches disk, because "return
  10,000 files" and "return one 4GB file" are both denial-of-disk;
* **nothing is executed, ever** — these are inert bytes written to a workspace;
  the extension is recorded, never acted on.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

#: Per-file ceiling. Generous for a document, far below "fills the disk".
MAX_FILE_BYTES = 25 * 1024 * 1024

#: Total across one reply.
MAX_TOTAL_BYTES = 60 * 1024 * 1024

#: How many files one reply may deliver.
MAX_FILES = 20

#: Characters that survive in a filename. Everything else becomes "_": this is
#: an allowlist on purpose — a denylist of "bad" characters has to be right
#: about every platform's quirks, and being wrong once is a path escape.
_NAME_OK = re.compile(r"[^A-Za-z0-9._ ()\[\]-]")

_MAX_NAME_LEN = 120


def safe_filename(raw: Any, *, fallback: str = "remote-file") -> str:
    """Reduce anything to a plain, harmless basename.

    Deliberately paranoid: it takes the last path segment under BOTH separators
    (a Windows daemon still receives POSIX names and vice versa), strips
    anything outside the allowlist, refuses names that are only dots, and caps
    the length. The result still goes through ``safe_path`` — this is the first
    of two locks, not the only one.
    """
    name = str(raw or "").strip()
    # Take the basename under either separator, and drop a drive letter.
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"^[A-Za-z]:", "", name)
    name = _NAME_OK.sub("_", name).strip(" .")
    if not name or set(name) <= {"_"}:
        return fallback
    if len(name) > _MAX_NAME_LEN:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 12:
            name = stem[: _MAX_NAME_LEN - len(ext) - 1] + "." + ext
        else:
            name = name[:_MAX_NAME_LEN]
    return name


def same_host(url: str, base_url: str) -> bool:
    """True when *url* points at the SAME host as the registered agent.

    A reply naming ``http://192.168.1.1/admin`` would otherwise make the daemon
    fetch it from inside the user's network. The agent the user registered is
    the only host its own reply may hand files from.
    """
    try:
        a, b = urlparse(url), urlparse(base_url)
    except ValueError:
        return False
    if a.scheme not in ("http", "https"):
        return False
    return bool(a.hostname) and a.hostname == b.hostname and (a.port or 0) == (b.port or 0)


def parse_files(data: Any) -> list[dict[str, Any]]:
    """The ``files`` entries worth trying, capped. Never raises."""
    if not isinstance(data, dict):
        return []
    raw = data.get("files")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:MAX_FILES]:
        if isinstance(item, dict) and (item.get("content_b64") or item.get("url")):
            out.append(item)
    return out


async def collect(
    entries: list[dict[str, Any]],
    *,
    base_url: str,
    fetch=None,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Turn ``files`` entries into ``(name, bytes)``. Returns ``(files, notes)``.

    *notes* records every entry that was REFUSED and why. They are surfaced to
    the user rather than dropped: "the remote sent 3 files and you got 2" is
    something they need to know, and silence there is how a redaction or a
    report goes quietly missing.
    """
    files: list[tuple[str, bytes]] = []
    notes: list[str] = []
    total = 0

    for item in entries:
        name = safe_filename(item.get("name"))
        blob: bytes | None = None

        b64 = item.get("content_b64")
        if isinstance(b64, str) and b64.strip():
            # Reject on the ENCODED length first: decoding a 1GB string to find
            # out it is too big is the denial-of-service itself.
            if len(b64) > MAX_FILE_BYTES * 4 // 3 + 16:
                notes.append(f"{name}: larger than {MAX_FILE_BYTES // (1024 * 1024)}MB")
                continue
            try:
                blob = base64.b64decode(b64, validate=True)
            except Exception:  # noqa: BLE001 — a bad payload is a skipped file
                notes.append(f"{name}: content_b64 was not valid base64")
                continue
        elif isinstance(item.get("url"), str):
            url = item["url"]
            if not same_host(url, base_url):
                notes.append(f"{name}: refused a URL on another host ({url[:60]})")
                continue
            if fetch is None:
                notes.append(f"{name}: no fetcher available for a URL")
                continue
            try:
                blob = await fetch(url)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{name}: fetch failed ({type(exc).__name__})")
                continue

        if blob is None:
            notes.append(f"{name}: no content_b64 or url")
            continue
        if len(blob) > MAX_FILE_BYTES:
            notes.append(f"{name}: larger than {MAX_FILE_BYTES // (1024 * 1024)}MB")
            continue
        if total + len(blob) > MAX_TOTAL_BYTES:
            notes.append(f"{name}: skipped — the reply exceeded the total size cap")
            continue
        total += len(blob)
        files.append((name, blob))

    return files, notes


def unique_path(directory: Path, name: str) -> Path:
    """A path that does not overwrite an existing file.

    A remote must not be able to replace something already in the workspace by
    naming its file the same thing.
    """
    target = directory / name
    if not target.exists():
        return target
    stem, dot, ext = name.rpartition(".")
    stem = stem or name
    for i in range(2, 500):
        candidate = directory / (f"{stem}-{i}.{ext}" if dot else f"{name}-{i}")
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{len(name)}{dot}{ext}"
