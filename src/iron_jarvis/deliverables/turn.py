"""One chat turn's deliverables: the small object both chat lanes hold
(v1.481.0). MIRROR NOTE: ``daemon/chat_turn.run_chat_turn`` and
``daemon/routes/chat.py``'s stream lane make the same five calls at the same
seams (start, prompt, top of a round, the reply ending on words, the end).

Plain chat only (never a coding session: it has its own finish check), and
only when the chat is bound to a folder and the user's message names a
required file or a write rule. With no scope nothing here runs and the turn is
byte-identical.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .check import missing_now, scope_block, turn_note
from .scope import TaskScope, scope_for_turn
from .steps import StepLog, failed_step_note

#: Steps left in the reply when Iron Jarvis says what is still missing (once).
RESERVE_ROUNDS = 3


class TurnDeliverables:
    def __init__(self, scope: TaskScope) -> None:
        self.scope = scope
        self.steps = StepLog()
        self.nudged = False
        self.warned = False
        #: The user declined (or the permission table refused) a call in this
        #: reply: their answer stands, and the model is never asked to try
        #: again. Set by both lanes.
        self.declined = False

    @classmethod
    async def start(cls, messages: Any, ws_resolved: "tuple[Path, bool] | None") -> "TurnDeliverables | None":
        """The turn's deliverables, read off the loop; None = nothing to do.
        Only for a folder the chat is really bound to (``ws_resolved[1]``)."""
        if not ws_resolved or not ws_resolved[1]:
            return None
        scope = await asyncio.to_thread(scope_for_turn, messages, ws_resolved[0])
        return cls(scope) if scope is not None else None

    def resolved(self, ws_resolved: "tuple[Path, bool] | None") -> "tuple[Path, bool] | None":
        """The folder the turn's tools work in: the task folder when the
        user's paths mean it (``rebase``), else unchanged."""
        if self.scope.rebase:
            return (self.scope.root, True)
        return ws_resolved

    def prompt(self, armed: Any = None) -> str:
        return scope_block(self.scope, armed or ())

    async def reserve_note(self, round_index: int, rounds: int) -> str:
        """ONCE, when only :data:`RESERVE_ROUNDS` steps are left and a
        required file is still missing: what is missing (a user-role note)."""
        left = rounds - round_index
        if self.declined or self.warned or round_index == 0 or left > RESERVE_ROUNDS or left < 2:
            return ""
        self.warned = True
        return await asyncio.to_thread(missing_now, self.scope, left)

    async def end_note(self, round_index: int, rounds: int) -> str:
        """ONCE, when the model ends on words with a step left: the files still
        missing, for one more try. "" = let the reply end."""
        if self.declined or self.nudged or rounds - round_index < 2:
            return ""
        self.nudged = True
        return await asyncio.to_thread(missing_now, self.scope, None)

    def observe(self, name: str, args: Any, result: Any) -> None:
        self.steps.note(name, args, result)

    async def final(self) -> str:
        return await asyncio.to_thread(turn_note, self.scope, self.steps)


def step_note(name: str, result: Any) -> str:
    """Added to a program's result when it answered ok but printed an error
    (every chat turn, scoped or not)."""
    try:
        return failed_step_note(name, result)
    except Exception:  # noqa: BLE001
        return ""
