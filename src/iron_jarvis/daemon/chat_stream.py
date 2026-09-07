"""The STREAMING chat turn, as a thing a non-HTTP caller can run and stop.

THE SILENT FAILURE THIS MODULE PREVENTS: a Stop button that does nothing.

``run_chat_turn`` was extracted verbatim from ``chat_complete`` in v1.136.0
precisely so non-dashboard callers could run the same engine. v1.241.0 does
the same for the streaming twin, for the same reason and one more:

* **Same engine, no second copy.** A surface that re-implemented the SSE loop
  would drift the way the two chat lanes have drifted before — one lane gets
  the fix, the other ships the old bug for a release (v1.167.0, v1.186.0).
* **Stop had to become addressable.** ``/chat/stream`` cancellation was
  CONNECTION-BOUND: the only cooperative check was
  ``request.is_disconnected()`` once per tool round, and mid-generation stop
  worked solely because Starlette cancels the response generator when the HTTP
  client goes away. A caller with no connection to drop — a browser side
  panel, whose turn the daemon runs on its behalf
  (``docs/BROWSER-SIDEBAR-PLAN.md`` §4, F3) — could not use any of that. Its
  Stop would have rendered, clicked, and changed nothing.

WHAT THIS MODULE IS: the import seam. The lifted turn itself still lives in
``daemon/routes/chat.py`` beside the ``/chat/*`` routes and the module-level
helpers it resolves by name — several existing tests source-pin the streaming
lane's prep sites to that file, and monkeypatch its module namespace to reach
the lane, so the function's *home module* is load-bearing in a way its call
signature is not. Callers import it from here and stay indifferent to that.

WHAT STOP CAN AND CANNOT DO — say this on every surface built on it:

* It ends the answer being generated, at the next token frame.
* It prevents the NEXT tool round.
* It does **not** kill a tool that is already executing. That tool runs on a
  worker thread (v1.228.0); the thread finishes and its write lands. A file
  already being written is still written. Stop is not an abort of work in
  flight, and a surface that implies otherwise is lying to the user about what
  their click did.

USAGE::

    from iron_jarvis.daemon.chat_stream import TURNS, stream_chat_turn

    gen = await stream_chat_turn(platform, personas, body)   # body.turn_id set
    async for sse_text in gen:
        ...
    # ...and from anywhere else, on any loop or thread:
    TURNS.stop(body.turn_id)

``stream_chat_turn`` is a coroutine returning an async iterator, not an async
generator: the prep raises ``HTTPException`` (400 empty messages, 404 unknown
skill) and those must land before any response has begun, exactly as they did
when it was the route body.
"""

from __future__ import annotations

from ..core.turns import TURNS, TurnHandle, TurnRegistry
from .routes.chat import stream_chat_turn

__all__ = ["TURNS", "TurnHandle", "TurnRegistry", "stream_chat_turn"]
