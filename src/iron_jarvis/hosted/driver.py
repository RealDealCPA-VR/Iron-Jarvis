"""What the service needs from a harness's protocol (v1.409.0).

A :class:`Driver` turns the harness's own frames into the normalized events
(:mod:`.events`) and the user's actions into the harness's own messages. It
never decides anything for the user: every permission question becomes an
:class:`Ask` the service shows as a card, and only the user's answer goes back.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..core.ids import new_id

#: The decisions a user can give an ask (the route's vocabulary).
DECISIONS: tuple[str, ...] = ("allow", "allow_session", "deny")


class AskRefused(ValueError):
    """An answer the ask cannot take (said in words; the route's 400)."""


class ControlRefused(RuntimeError):
    """The harness refused a live change (its own words)."""


@dataclass
class Ask:
    """One open permission question or question to the user."""

    #: Iron Jarvis's own id (the card's ``request_id``) — never the harness's.
    id: str
    #: ``approval`` | ``question``.
    kind: str
    #: The tool's NAME (the bell lists names, never arguments).
    tool: str
    #: The harness's own handle on the request (opaque to the service).
    native: Any = None
    #: Whatever the driver needs to answer it (input, suggestions, item…).
    data: dict[str, Any] = field(default_factory=dict)
    #: The event the card was drawn from.
    event: dict[str, Any] = field(default_factory=dict)
    asked_at: str = ""
    can_session: bool = False


def new_ask_id() -> str:
    return new_id("ask")


class Driver:
    """Base: one per running harness process."""

    harness = ""

    def __init__(
        self,
        *,
        write: Callable[[dict[str, Any]], Awaitable[None]],
        emit: Callable[[dict[str, Any]], None],
        ask: Callable[[Ask], None],
        on_session_id: Callable[[str], None],
        mode: str,
        model: str = "",
        effort: str = "",
        cwd: str = "",
    ) -> None:
        self._write = write
        self.emit = emit
        self.raise_ask = ask
        self.on_session_id = on_session_id
        self.mode = mode
        self.model = model
        self.effort = effort
        self.cwd = cwd
        self.session_id = ""
        #: True while a turn the user started has not ended.
        self.turn_open = False
        self.commands: list[dict[str, str]] = []
        #: v1.412.0: the service's way to say a note sent while the tool works
        #: (a STEER) reached it — called with the note's ref.
        self.steer_read: Callable[[str], None] | None = None
        #: ref -> "sent" | "read" | "cancelled": the notes of the open turn.
        self.steers: dict[str, str] = {}

    async def write(self, obj: dict[str, Any]) -> None:
        await self._write(obj)

    # -- what each harness implements -----------------------------------------
    def argv(self, *, session_id: str, resume: bool) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def open(self, *, session_id: str, resume: bool) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_message(self, obj: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_user(self, text: str, files: list[str]) -> None:  # pragma: no cover
        raise NotImplementedError

    async def answer(self, ask: Ask, decision: str, message: str, answers: Any) -> str:  # pragma: no cover
        raise NotImplementedError

    async def interrupt(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def set_mode(self, mode: str) -> str:  # pragma: no cover
        """Apply ``mode`` live. Returns how it applies ("now" | "next")."""
        raise NotImplementedError

    async def set_model(self, model: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def steer(self, ref: str, text: str, files: list[str]) -> str:
        """v1.412.0: give the RUNNING turn a note the user typed while it
        works, through the tool's OWN way of taking one (Claude Code: a user
        message written mid-turn; Codex: ``turn/steer``; Pi: ``steer``).
        Answers how it arrives ("step": the tool reads it at its next step).
        Raises ControlRefused (said in words) when it cannot take one now."""
        raise ControlRefused("This coding tool takes no notes while it works — wait for it, or press Stop.")

    def note_read(self, ref: str) -> None:
        """A note reached the tool: said once."""
        if self.steers.get(ref) == "sent":
            self.steers[ref] = "read"
            if self.steer_read is not None:
                self.steer_read(ref)

    def unread_steers(self) -> list[str]:
        """The notes of the open turn the tool never read (the service hands
        them back to the box when the turn ends — never dropped)."""
        return [ref for ref, state in self.steers.items() if state != "read"]

    def on_exit(self) -> None:
        """The process ended: fail every pending control request."""


class ControlWaiter:
    """Request id -> future for a request the harness answers."""

    def __init__(self) -> None:
        self._waiting: dict[Any, asyncio.Future] = {}

    def make(self, key: Any) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._waiting[key] = fut
        return fut

    def settle(self, key: Any, value: Any = None, error: BaseException | None = None) -> bool:
        fut = self._waiting.pop(key, None)
        if fut is None or fut.done():
            return False
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(value)
        return True

    def fail_all(self, error: BaseException) -> None:
        for key in list(self._waiting):
            self.settle(key, error=error)
