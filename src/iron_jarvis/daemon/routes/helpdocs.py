"""Help-doc routes: serve the user-facing guides to the dashboard's Help page.

`docs/HANDBOOK.md`, `docs/RECOMMENDED-SETTINGS.md` and `docs/LOCAL-MODELS.md`
were referenced NOWHERE in the dashboard, so a packaged-app user — the only
kind this project has — could never read them. These two GET routes hand the
markdown to the Help page so the guides render in-app (v1.198.0).

The `_DOCS` allowlist IS the traversal guard: a slug is only ever a dict key,
never a path component, so no user input is joined into a filesystem path.
Anything not in the allowlist — including `../SIGNING`-shaped probes and the
TOFIX/audit files that live in the same `docs/` dir — is a plain 404.

In a frozen build the files come from `sys._MEIPASS/ijdocs` (bundled by
`packaging/ironjarvis.spec`); in dev they come from the repo's `docs/` dir.
A missing file is an honest 404 naming the file — never a 500, never an
empty 200 pretending the guide is blank.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

# slug -> (title, filename, description). Fixed order — the Help page renders
# the list as-is. Keep this to the three USER-FACING guides: the rest of
# docs/ (TOFIX, audits, plans) is maintainer material and must not ship.
_DOCS: dict[str, tuple[str, str, str]] = {
    "handbook": (
        "The Handbook",
        "HANDBOOK.md",
        "Every surface, the trust model, and troubleshooting.",
    ),
    "recommended-settings": (
        "Recommended Settings",
        "RECOMMENDED-SETTINGS.md",
        "A tuned daily-driver profile.",
    ),
    "local-models": (
        "Local Models by RAM Tier",
        "LOCAL-MODELS.md",
        "What to run at each RAM size.",
    ),
}


def _docs_dir() -> Path:
    """Where the guides live: `_MEIPASS/ijdocs` when frozen (the .spec bundles
    exactly the allowlisted files there), else the repo's `docs/` dir —
    parents[4] of this file is the repo root (routes → daemon → iron_jarvis →
    src → repo)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ijdocs"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[4] / "docs"


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object
    (unused here — the guides are static files, not daemon state)."""

    @app.get("/helpdocs")
    async def helpdocs_list() -> dict[str, Any]:
        """The catalog the Help page renders: the three guides, fixed order."""
        return {
            "docs": [
                {"slug": slug, "title": title, "description": description}
                for slug, (title, _filename, description) in _DOCS.items()
            ]
        }

    @app.get("/helpdocs/{slug}")
    async def helpdocs_get(slug: str) -> dict[str, Any]:
        """One guide's markdown. Unknown slug OR a missing file -> 404; the
        read hops off the event loop (hard repo rule — HANDBOOK.md is ~100KB
        and nothing blocking runs on the loop)."""
        entry = _DOCS.get(slug)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown help doc {slug!r}")
        title, filename, _description = entry
        path = _docs_dir() / filename
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            # Someone deleted the doc (or a build shipped without it): say so.
            raise HTTPException(
                status_code=404,
                detail=f"help doc {slug!r} is missing from this install ({filename}): {exc}",
            )
        return {
            "slug": slug,
            "title": title,
            "markdown": raw.decode("utf-8", errors="replace"),
        }
