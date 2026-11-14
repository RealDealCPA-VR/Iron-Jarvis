"""The Improvements inbox's route (v1.495.0, vision plan leap 5, Phase 1).

* ``GET /improvements`` → ``{items, groups, total, unreadable}``: every
  suggestion Iron Jarvis is waiting on the user to answer, from every store
  that keeps one (``inbox/collect.py`` has the list and the rules).

  - ``items``: ``[{id, kind, ref, title, about, why, evidence: [str],
    created_at, detail: {mode: "diff"|"text", before, after, label, cut} |
    null, accept: {label, method, path, body, note, confirm, blocked} | null,
    decline: {label, method, path, body, ...} | null, home: {href, label} |
    null, extra}]``. ``accept`` and ``decline`` name the suggestion's OWN
    route: this module decides nothing.
  - ``groups``: ``[{kind, label, count, shown}]`` in display order;
    ``count`` is how many are waiting, ``shown`` how many are in ``items``.
  - ``unreadable``: ``[{kind, label}]`` for a store that could not be read.
    It is never answered as "nothing waiting".

Reads only (SQLite), so the handler is a plain ``def`` (FastAPI runs it off
the loop). No model is called and nothing is written.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ...inbox import collect


def register(app: FastAPI, d) -> None:
    """Attach the route to *app*; ``d`` is the create_app deps object."""

    @app.get("/improvements")
    def improvements_inbox() -> dict[str, Any]:
        return collect(d)
