"""Artifact store (SPEC §26).

Versioned agent outputs on the local filesystem. Each ``save`` of a given
``name`` writes a fresh ``v<n>`` directory under ``root/<safe_name>/``; an
optional SQLModel engine mirrors every version as an ``ArtifactRecord`` row.
Names and filenames are slugified so a stored artifact can never escape ``root``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import ArtifactRecord

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Slugify ``name`` into a single path-safe directory segment (no traversal)."""
    slug = _UNSAFE.sub("_", name.strip()).strip("._")
    return slug or "artifact"


def _safe_filename(filename: str) -> str:
    """Reduce to a slugified basename so a filename cannot escape its version dir."""
    slug = _UNSAFE.sub("_", Path(filename).name.strip()).strip("._")
    return slug or "artifact"


@dataclass
class Artifact:
    """A single saved artifact version (SPEC §26)."""

    name: str
    version: int
    kind: str
    path: Path
    size: int
    created_at: datetime


class ArtifactStore:
    """Filesystem-backed, versioned artifact store (SPEC §26)."""

    def __init__(self, root: Path | str, engine: Engine | None = None) -> None:
        self.root = Path(root)
        self.engine = engine
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        return self.root / _safe_name(name)

    def versions(self, name: str) -> list[int]:
        """Return the sorted list of stored version numbers for ``name``."""
        base = self._dir(name)
        if not base.is_dir():
            return []
        out: list[int] = []
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith("v"):
                try:
                    out.append(int(child.name[1:]))
                except ValueError:
                    continue
        return sorted(out)

    def save(
        self,
        name: str,
        content: str | bytes,
        kind: str = "file",
        filename: str | None = None,
        session_id: str | None = None,
    ) -> Artifact:
        """Write ``content`` as the next version of ``name`` (SPEC §26)."""
        existing = self.versions(name)
        version = (existing[-1] + 1) if existing else 1

        vdir = self._dir(name) / f"v{version}"
        vdir.mkdir(parents=True, exist_ok=True)
        fname = _safe_filename(filename) if filename else _safe_filename(name)
        path = vdir / fname

        if isinstance(content, (bytes, bytearray)):
            path.write_bytes(bytes(content))
        else:
            path.write_text(content, encoding="utf-8")

        size = path.stat().st_size
        created = utcnow()
        artifact = Artifact(
            name=name,
            version=version,
            kind=kind,
            path=path,
            size=size,
            created_at=created,
        )

        if self.engine is not None:
            with session_scope(self.engine) as db:
                db.add(
                    ArtifactRecord(
                        name=name,
                        version=version,
                        kind=kind,
                        path=str(path),
                        session_id=session_id,
                        size=size,
                        created_at=created,
                    )
                )
                db.commit()

        return artifact

    def _version_file(self, name: str, version: int) -> Path | None:
        vdir = self._dir(name) / f"v{version}"
        if not vdir.is_dir():
            return None
        files = sorted(p for p in vdir.iterdir() if p.is_file())
        return files[0] if files else None

    def read(self, name: str, version: int | None = None) -> bytes:
        """Return the bytes of ``name`` (latest version if ``version`` is None)."""
        vs = self.versions(name)
        if not vs:
            raise FileNotFoundError(f"no artifact named {name!r}")
        want = vs[-1] if version is None else version
        path = self._version_file(name, want)
        if path is None:
            raise FileNotFoundError(f"no artifact {name!r} v{want}")
        return path.read_bytes()

    def latest(self, name: str) -> Artifact | None:
        """Return the newest version of ``name`` as an :class:`Artifact`, or None."""
        vs = self.versions(name)
        if not vs:
            return None
        version = vs[-1]
        path = self._version_file(name, version)
        if path is None:
            return None
        st = path.stat()
        return Artifact(
            name=name,
            version=version,
            kind="file",
            path=path,
            size=st.st_size,
            created_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        )

    def list_names(self) -> list[str]:
        """Return the sorted (slugified) names of all stored artifacts."""
        if not self.root.is_dir():
            return []
        return sorted(d.name for d in self.root.iterdir() if d.is_dir())
