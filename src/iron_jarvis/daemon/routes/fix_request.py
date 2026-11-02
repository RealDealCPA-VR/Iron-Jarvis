"""COPY AS A FIX REQUEST's route (v1.423.0, Wave 3 lane B).

``GET /team/fix-request?days=7&session=<thread id>`` → ``{"text",
"problems", "since", "until", "sessions"}``: the Markdown report of the
problems Iron Jarvis's own coding sessions and model team ran into
(``agents/fix_request`` — what it reads, what it leaves out and why), for the
owner to paste to the coding agent that works on Iron Jarvis.

* ``days`` — 1 to 30 (anything else is a 422 before anything is read);
* ``session`` — optional: ONE coding session (its thread id, or
  ``build:<id>``), its helpers included. A session that is not a coding
  session answers 404 in words.

A sync ``def``: FastAPI runs it in its threadpool, so the SQLite reads never
run on the event loop. A source that cannot be read is NAMED in the report
(``Could not read: …``) and the answer is still a 200 — never a 500.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query

from ...agents import fix_request as FR

#: What the 404 says (a session that is not a coding session).
UNKNOWN_SESSION = "That coding session was not found. It may have been deleted."


def register(app: FastAPI, d) -> None:
    @app.get("/team/fix-request")
    def fix_request(
        days: int = Query(FR.DEFAULT_DAYS, ge=FR.MIN_DAYS, le=FR.MAX_DAYS),
        session: str = Query("", max_length=200),
    ) -> dict[str, Any]:
        """The fix request (see the module docstring). Never a 500 for an
        unreadable record; a 404 only for an unknown ``session``."""
        engine = getattr(getattr(d, "platform", None), "engine", None)
        try:
            return FR.build_report(engine, days=days, session=session.strip())
        except FR.UnknownSession:
            raise HTTPException(status_code=404, detail=UNKNOWN_SESSION) from None


__all__ = ["UNKNOWN_SESSION", "register"]
