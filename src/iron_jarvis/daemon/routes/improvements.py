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

17-a (the night review, ``improve/night_review``): a suggestion the review
made carries ``extra.night = {night, why}``, and

* ``GET /improvements/night`` → ``{offer: {requests, words} | null, night:
  {night, state, made, waiting, skipped_why, needs_you, line} | null}``: the
  one-time offer (D-17.1, while ``night_review`` is not set; the page answers
  it through ``PUT /settings``, so it has Undo) and the page's top line, only
  when last night's review made something that still waits, or was skipped
  for a reason only the owner can change. A read (plain ``def``).

18-a (``improve/results``): ``GET /improvements`` also carries ``results``:
``[{id, kind, ref, target, title, words, state, verdict, accepted_at,
checked_at, before: {n, good, words} | null, after: {...} | null, home}]``,
the results of changes the owner ACCEPTED (newest first, at most 30, the
last 60 days), each one's words built by code from its numbers. They wait
for nothing, so they are in no group or count. A table that cannot be read
is named in ``unreadable`` (kind ``results``), never answered as "no
results". Still a read: the rows are made by the night review's pass and
the daily pass, never here.

v1.506.0 (north-star plan 6.2): chat answers ONE suggestion through that
suggestion's OWN route (``inbox/tools.SuggestionAnswerTool``): the app is kept
on the deps object (``d.suggestion_app``) and :func:`answer_through_route`
hands the item's very method, path and body to the app's router in this
process, so the route's own handler and checks decide, exactly as for the
page's press.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI

from ...inbox import collect

#: Longest answer body read back (a route's refusal is one sentence).
_DETAIL_CHARS = 600


def register(app: FastAPI, d) -> None:
    """Attach the route to *app*; ``d`` is the create_app deps object."""
    # v1.506.0: the app whose router answers a suggestion from chat.
    d.suggestion_app = app

    @app.get("/improvements")
    def improvements_inbox() -> dict[str, Any]:
        return collect(d)

    @app.get("/improvements/night")
    def improvements_night() -> dict[str, Any]:
        from ...improve.night_review import page_view

        return page_view(d)


async def answer_through_route(d: Any, method: str, path: str, body: Any) -> tuple[int, str]:
    """Press a suggestion's own route in this process: ``(status, detail)``.

    The request goes to the app's ROUTER (the route table, each route's own
    handler and validation), the same handler the page's press reaches. The
    caller is a chat tool inside this daemon that the user approved on a card,
    so the token check in front of the router (which a press from the window
    passes with the install token) is not repeated. A route that refuses
    (``HTTPException``) answers its status and its own sentence; anything else
    that goes wrong is a 500 with a plain sentence. Never raises."""
    app = getattr(d, "suggestion_app", None)
    if app is None:
        return 503, "Iron Jarvis's window is not running."
    raw = json.dumps(body if isinstance(body, dict) else {}).encode()
    sent = {"done": False}

    async def receive() -> dict[str, Any]:
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    status = 0
    chunks: list[bytes] = []

    async def send(msg: dict[str, Any]) -> None:
        nonlocal status
        if msg.get("type") == "http.response.start":
            status = int(msg.get("status") or 0)
        elif msg.get("type") == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": str(method or "POST").upper(), "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": b"", "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 0), "app": app,
    }
    try:
        # FastAPI's own exit-stack layer (what its middleware stack puts in
        # front of the router), then the router.
        from fastapi.middleware.asyncexitstack import AsyncExitStackMiddleware

        await AsyncExitStackMiddleware(app.router)(scope, receive, send)
    except Exception as exc:  # noqa: BLE001 — a route's refusal is said, never raised
        detail = getattr(exc, "detail", None)
        code = int(getattr(exc, "status_code", 0) or 500)
        if detail is None:
            return 500, "The answer could not be saved."
        return code, str(detail)[:_DETAIL_CHARS]
    text = b"".join(chunks).decode("utf-8", "replace")
    if 200 <= status < 300:
        return status, ""
    try:
        got = json.loads(text)
        detail = got.get("detail") if isinstance(got, dict) else None
    except ValueError:
        detail = None
    return status or 500, (str(detail) if detail else "the answer was refused")[:_DETAIL_CHARS]
