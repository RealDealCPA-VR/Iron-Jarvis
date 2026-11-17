"""One Linux update manifest for the single file AND the package.

    python scripts/linux_update_manifest.py OUT --version X.Y.Z --dir FILES MANIFEST [MANIFEST ...]

WHY. The Linux app is published as two files: ``Iron-Jarvis-x86_64.AppImage``
(one file, runs anywhere, updates itself silently) and ``Iron-Jarvis-amd64.deb``
(a proper install on Ubuntu, Debian and Mint). The builder is run ONCE PER FILE
on purpose: built together, the note that tells the updater "this copy is a
package" (``resources/package-type``) would sit in both. Each run writes its own
``latest-linux.yml`` naming one file; the updater needs ONE that names both (it
picks the entry whose ending matches the copy it runs in).

This merges them and then PROVES the result against the files themselves: every
entry's ``sha512`` and ``size`` are recomputed from the file in ``--dir``, and a
manifest for another version, an entry with no file, or a file with no entry is
refused. A manifest that does not describe the files beside it must never be
published. Standard library only, so the workflow can run it without the app.

The first manifest's file is the main one (``path:`` / top-level ``sha512:``):
give the single file's manifest first.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
from pathlib import Path

_VERSION = re.compile(r"^version:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
_URL = re.compile(r"^\s*-\s*url:\s*(\S+)\s*$")
_FIELD = re.compile(r"^\s+(sha512|size|blockMapSize):\s*(\S+)\s*$")
_DATE = re.compile(r"^releaseDate:\s*(.+?)\s*$")


class ManifestError(ValueError):
    """The manifests cannot be merged into one that is safe to publish."""


def parse(text: str) -> dict:
    """``{"version", "releaseDate", "files": [{"url", "sha512", "size", "blockMapSize"?}]}``
    from one of the builder's ``latest-linux.yml`` files. Line based, like
    ``publish_update_manifest``: it reads only the lines it knows."""
    out: dict = {"version": None, "releaseDate": None, "files": []}
    current: dict | None = None
    in_files = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "-")):
            in_files = line.startswith("files:")
            current = None
            m = _VERSION.match(line)
            if m:
                out["version"] = m.group(1)
            m = _DATE.match(line)
            if m:
                out["releaseDate"] = m.group(1).strip("'\"")
            continue
        if not in_files:
            continue
        m = _URL.match(line)
        if m:
            current = {"url": m.group(1)}
            out["files"].append(current)
            continue
        m = _FIELD.match(line)
        if m and current is not None:
            current[m.group(1)] = m.group(2)
    return out


def sha512_of(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return base64.b64encode(h.digest()).decode("ascii")


def merge(texts: list[str], version: str, files_dir: Path) -> str:
    """The one manifest; raises :class:`ManifestError` with the reason."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ManifestError(f"version must be X.Y.Z, got {version!r}")
    entries: list[dict] = []
    date = None
    for text in texts:
        doc = parse(text)
        if doc["version"] != version:
            raise ManifestError(f"a manifest is for {doc['version']}, not the {version} being built")
        if not doc["files"]:
            raise ManifestError("a manifest lists no files")
        date = date or doc["releaseDate"]
        for entry in doc["files"]:
            name = entry["url"]
            if "/" in name or "\\" in name or name.startswith("."):
                raise ManifestError(f"a manifest names {name!r}, which is not a plain file name")
            if any(e["url"] == name for e in entries):
                raise ManifestError(f"{name} is listed twice")
            entries.append(entry)
    for entry in entries:
        path = files_dir / entry["url"]
        if not path.is_file():
            raise ManifestError(f"{entry['url']} is in a manifest but not in {files_dir}")
        size = path.stat().st_size
        digest = sha512_of(path)
        if entry.get("sha512") != digest:
            raise ManifestError(f"{entry['url']}: the manifest's sha512 is not this file's")
        if str(entry.get("size")) != str(size):
            raise ManifestError(f"{entry['url']}: the manifest says {entry.get('size')} bytes, the file has {size}")
    lines = [f"version: {version}", "files:"]
    for entry in entries:
        lines.append(f"  - url: {entry['url']}")
        lines.append(f"    sha512: {entry['sha512']}")
        lines.append(f"    size: {entry['size']}")
        if entry.get("blockMapSize"):
            lines.append(f"    blockMapSize: {entry['blockMapSize']}")
    main = entries[0]
    lines.append(f"path: {main['url']}")
    lines.append(f"sha512: {main['sha512']}")
    if date:
        lines.append(f"releaseDate: '{date}'")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dst", type=Path)
    ap.add_argument("manifests", type=Path, nargs="+")
    ap.add_argument("--version", required=True, help="the version being built, X.Y.Z")
    ap.add_argument("--dir", required=True, type=Path, help="the folder holding the files the manifests name")
    args = ap.parse_args(argv)
    try:
        result = merge([p.read_text(encoding="utf-8") for p in args.manifests], args.version, args.dir)
    except (OSError, ManifestError) as exc:
        print(f"linux_update_manifest: {exc}", file=sys.stderr)
        return 1
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(result, encoding="utf-8", newline="\n")
    names = [m.group(1) for m in (_URL.match(line) for line in result.splitlines()) if m]
    print(f"wrote {args.dst} for {args.version}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
