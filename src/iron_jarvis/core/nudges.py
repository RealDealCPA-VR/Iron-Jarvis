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
#: v1.421.0 (A5): a reply wrote a tool call AS TEXT (GLM's ``<tool_call>…``,
#: a Claude-style ``<invoke>``, a JSON blob) instead of calling the tool, and
#: was told once to call it for real (``harness/mode.TEXT_TOOL_CALL_NUDGE``)
#: — a coding session's turn (both chat lanes) or an agent run's step.
TEXT_TOOL_CALL = "text_tool_call"

#: v1.480.0 (capability search): a chat reply about to end on words was asked
#: once whether it should look up what Iron Jarvis can do first, and DID
#: (``chat_turn.LOOK_FIRST_NOTE``); a reply that was kept is not counted.
LOOK_FIRST = "look_first"
#: v1.493.0: a coding reply ended by saying the work could not be done and was
#: told once to try another way first (``harness/give_up.GIVE_UP_NUDGE``).
GAVE_UP = "gave_up"
#: v1.495.0: a plain chat reply or an agent run's answer was about to end while
#: Iron Jarvis's own check found it unfinished (a file it claimed but no tool
#: wrote, a file asked for and never made, a change claimed but not made, an
#: unchecked figure, a missing required file) and was sent back to fix it
#: (``daemon/until_done``). One per note sent.
CHECKED_AGAIN = "checked_again"

NUDGE_KINDS = (PLAN_RUN, STEP_LIMIT_CONTINUE, ROUNDS_EXTENDED, CLOSING_QUESTION, TEXT_TOOL_CALL,
               LOOK_FIRST, GAVE_UP, CHECKED_AGAIN)


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


__all__ = ["CHECKED_AGAIN", "CLOSING_QUESTION", "GAVE_UP", "LOOK_FIRST", "NUDGE_KINDS", "PLAN_RUN", "ROUNDS_EXTENDED",
           "STEP_LIMIT_CONTINUE", "TEXT_TOOL_CALL", "note_nudge"]
