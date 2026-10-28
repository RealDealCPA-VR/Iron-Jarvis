"""Iron Jarvis nudging a run on BY ITSELF (v1.402.0, autonomy changes 2-4).

Where the user used to have to type "continue" or press a button, the
product now moves the run on itself in a few bounded places. Each place says
so with ONE ``autonomy.nudged`` event, so the autonomy metrics (``GET
/autonomy/metrics``) count the system's nudges apart from the user's own.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: An approved plan started running without a new message from the user.
PLAN_RUN = "plan_run"
#: An attended agent run hit its step budget and carried on once by itself.
STEP_LIMIT_CONTINUE = "step_limit_continue"
#: A document / browser chat turn ran out of rounds and got one more allotment.
ROUNDS_EXTENDED = "rounds_extended"
#: A reply ended on an offer to do more ("Want me to ...?") with work left,
#: and was told to do it unless only the user can decide.
CLOSING_QUESTION = "closing_question"

NUDGE_KINDS = (PLAN_RUN, STEP_LIMIT_CONTINUE, ROUNDS_EXTENDED, CLOSING_QUESTION)


async def note_nudge(bus: Any, kind: str, *, session_id: "str | None" = None, **facts: Any) -> None:
    """Publish ONE ``autonomy.nudged`` event. Never raises (a metric never
    breaks a run). ``facts``: plain, short values (thread_id, turn_id, ...)
    — never a message's text."""
    if bus is None or kind not in NUDGE_KINDS:
        return
    try:
        from .events import EventType

        payload = {"kind": kind}
        payload.update({k: v for k, v in facts.items() if isinstance(v, (str, int, float, bool))})
        await bus.publish(EventType.AUTONOMY_NUDGED, payload, session_id=session_id)
    except Exception:  # noqa: BLE001
        log.debug("autonomy nudge event failed", exc_info=True)


__all__ = ["CLOSING_QUESTION", "NUDGE_KINDS", "PLAN_RUN", "ROUNDS_EXTENDED", "STEP_LIMIT_CONTINUE",
           "note_nudge"]
