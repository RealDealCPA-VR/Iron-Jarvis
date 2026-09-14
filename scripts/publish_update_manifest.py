"""Turn electron-builder's ``latest.yml`` into the feed-independent update manifest.

    uv run python scripts/publish_update_manifest.py IN OUT --repo OWNER/NAME --version X.Y.Z

WHY (v1.260.0). The desktop updater used to find a release through GitHub's
``releases.atom`` feed — a page GitHub renders in 2–10 s and cuts off at ~10 s, so
about every other update check on 2026-09-14 ended in a 504. The release job now
also publishes ``latest.yml`` to the repo's ``updates`` branch, where
raw.githubusercontent.com serves it in a fraction of a second with no feed in the
way. Served from there, the manifest's file names must be ABSOLUTE URLs — the
installer and its blockmap still live on the GitHub release — so this rewrites
``url:`` and ``path:`` to ``https://github.com/OWNER/NAME/releases/download/vX.Y.Z/<name>``.

It is a LINE rewrite, not a YAML round trip: every other line — ``sha512``,
``size``, ``releaseDate`` — leaves byte-for-byte as it came in, so the checksum the
updater verifies is the one electron-builder wrote. It refuses a manifest whose
``version`` is not the one being published and one that carries no checksum: a
manifest that cannot be verified must never reach the branch.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_URL_LINE = re.compile(r"^(\s*-\s*url:\s*)(\S+)\s*$")
_PATH_LINE = re.compile(r"^(path:\s*)(\S+)\s*$")
_VERSION_LINE = re.compile(r"^version:\s*(\S+)\s*$")
_ABSOLUTE = re.compile(r"^https?://", re.I)


def download_base(repo: str, version: str) -> str:
    repo = repo.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"repo must be OWNER/NAME, got {repo!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"version must be X.Y.Z, got {version!r}")
    return f"https://github.com/{repo}/releases/download/v{version}"


def rewrite(text: str, repo: str, version: str) -> str:
    """The manifest with absolute file URLs; raises on anything unpublishable."""
    base = download_base(repo, version)
    found_version = None
    has_checksum = False
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        m = _VERSION_LINE.match(body)
        if m:
            found_version = m.group(1).strip("'\"")
        if re.match(r"^\s*(-\s*)?sha512:\s*\S+", body):
            has_checksum = True
        for pattern in (_URL_LINE, _PATH_LINE):
            m = pattern.match(body)
            if m:
                name = m.group(2)
                if not _ABSOLUTE.match(name):
                    body = f"{m.group(1)}{base}/{name}"
                break
        out.append(body + eol)
    if found_version is None:
        raise ValueError("manifest has no version line")
    if found_version != version:
        raise ValueError(f"manifest is for {found_version}, not the {version} being published")
    if not has_checksum:
        raise ValueError("manifest carries no sha512 — refusing to publish an unverifiable update")
    if not any(_URL_LINE.match(l.rstrip("\r\n")) for l in out):
        raise ValueError("manifest lists no files")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--repo", required=True, help="OWNER/NAME on GitHub")
    ap.add_argument("--version", required=True, help="the version being published, X.Y.Z")
    args = ap.parse_args(argv)
    try:
        result = rewrite(args.src.read_text(encoding="utf-8"), args.repo, args.version)
    except (OSError, ValueError) as exc:
        print(f"publish_update_manifest: {exc}", file=sys.stderr)
        return 1
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(result, encoding="utf-8", newline="\n")
    urls = [m.group(2) for m in (_URL_LINE.match(l) for l in result.splitlines()) if m]
    print(f"wrote {args.dst} for {args.version}: {len(urls)} file(s) -> {urls[0] if urls else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
