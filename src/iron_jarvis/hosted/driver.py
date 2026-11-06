"""What the service needs from a harness's protocol (v1.409.0).

A :class:`Driver` turns the harness's own frames into the normalized events
(:mod:`.events`) and the user's actions into the harness's own messages. It
never decides anything for the user: every permission question becomes an
:class:`Ask` the service shows as a card, and only the user's answer goes back.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..core.ids import new_id

#: The decisions a user can give an ask (the route's vocabulary).
DECISIONS: tuple[str, ...] = ("allow", "allow_session", "deny")
#: v1.422.0: every answer that let a card through (Claude Code's "Allow all
#: commands this session" included) — what a card answered before a restart
#: tells its driver again (``hosted/adopt.py``).
ALLOW_LIKE: tuple[str, ...] = ("allow", "allow_session", "allow_commands")

#: v1.422.0 (the code host): while a tool's line is handed to its driver, the
#: line's own name (``[<tool>-<seq>, n]``, set by ``hostlink.RemoteProcess``).
#: A card raised by that line then gets an id DERIVED from it, so the same
#: line read again after an Iron Jarvis restart raises the SAME card — the
#: one the session's log already shows, and the one the user's answer names.
ASK_SEED: ContextVar[list[Any] | None] = ContextVar("ij_hosted_ask_seed", default=None)


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
    seed = ASK_SEED.get()
    if seed is not None:
        seed[1] += 1
        return f"ask_{seed[0]}_{seed[1]}"
    return new_id("ask")


# --------------------------------------------------------------------------- #
# v1.422.0: a driver's state, kept across an Iron Jarvis restart
# --------------------------------------------------------------------------- #
#: A dict attribute keeps at most this many (newest) entries in a snapshot —
#: maps that grow for a whole session (Claude Code's tool inputs, every
#: message id) must not make each snapshot bigger forever.
STATE_DICT_MAX = 500
_TAGS = ("$t", "$s", "$d")


class _NotKept(Exception):
    """A value with no JSON form (a future, a timer, a path): not kept."""


def _enc(v: Any, depth: int = 0, cap: int = 0, text: int = 0) -> Any:
    """``cap``: a dict keeps its newest ``cap`` entries (top level only);
    ``text``: a string anywhere inside is cut to ``text`` characters."""
    if depth > 40:
        raise _NotKept
    if isinstance(v, str):
        return v[:text] if text and len(v) > text else v
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, list):
        return [_enc(x, depth + 1, 0, text) for x in v]
    if isinstance(v, tuple):
        return {"$t": [_enc(x, depth + 1, 0, text) for x in v]}
    if isinstance(v, (set, frozenset)):
        return {"$s": [_enc(x, depth + 1, 0, text) for x in v]}
    if isinstance(v, dict):
        items = list(v.items())
        if cap and len(items) > cap:
            items = items[-cap:]
        if all(isinstance(k, str) and k not in _TAGS for k, _ in items):
            return {k: _enc(x, depth + 1, 0, text) for k, x in items}
        return {"$d": [[_enc(k, depth + 1), _enc(x, depth + 1, 0, text)] for k, x in items]}
    raise _NotKept


def _dec(v: Any) -> Any:
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        if len(v) == 1:
            (k, x), = v.items()
            if k == "$t" and isinstance(x, list):
                return tuple(_dec(y) for y in x)
            if k == "$s" and isinstance(x, list):
                return {_hashable(_dec(y)) for y in x}
            if k == "$d" and isinstance(x, list):
                return {_hashable(_dec(p[0])): _dec(p[1]) for p in x if isinstance(p, list) and len(p) == 2}
        return {k: _dec(x) for k, x in v.items()}
    return v


def _hashable(v: Any) -> Any:
    return tuple(v) if isinstance(v, list) else v


def encode_value(v: Any) -> Any:
    """A value in the kept state's JSON form (raises ValueError when it has none)."""
    try:
        return _enc(v)
    except _NotKept:
        raise ValueError("no JSON form") from None


def decode_value(v: Any) -> Any:
    return _dec(v)


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
        #: v1.426.0 (E4): the tool's OWN MCP servers as it last reported them
        #: (``hosted/tool_setup``); None = it has not said.
        self.mcp_servers: list[dict[str, str]] | None = None
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

    async def rename(self, title: str) -> None:
        """v1.439.0: the user renamed this session — tell the tool, so its own
        list (``claude --resume``, ``codex resume``) shows the same name. Best
        effort: the service calls it in the background, only while the
        process runs, and never waits for it. Most tools have no way."""
        return None

    #: v1.415.0: may :meth:`context_breakdown` be asked while a turn runs?
    #: (Pi answers its stats at once; Claude Code is asked only when idle.)
    context_while_working = False

    async def context_breakdown(self) -> dict[str, Any] | None:
        """v1.415.0: what fills the conversation, in the tool's OWN numbers
        (``context.breakdown``'s shape), read without a model call — or None
        when the tool has no way to say (Codex and OpenCode report only a
        total, which their turn's ``usage`` event already carries)."""
        return None

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

    # -- v1.422.0: kept across an Iron Jarvis restart (the code host) -----------
    #: Attributes never kept (whatever their value). Callables, futures,
    #: timers, waiters and paths are never kept anyway (no JSON form); a
    #: subclass names what it re-makes itself in :meth:`after_restore`.
    STATE_SKIP: frozenset[str] = frozenset({"_write", "emit", "raise_ask", "on_session_id", "steer_read"})
    #: Per attribute: how many (newest) dict entries are kept (default
    #: :data:`STATE_DICT_MAX`).
    STATE_DICT_CAPS: dict[str, int] = {}
    #: Per attribute: the longest string kept inside it (0 = whole).
    STATE_TEXT_CAPS: dict[str, int] = {}

    def state(self) -> dict[str, Any]:
        """Everything this driver knows that has a JSON form — a snapshot the
        code host keeps, so a restarted Iron Jarvis carries the tool's
        conversation on from it (``hosted/adopt.py``). An attribute with no
        JSON form is left out and keeps its fresh value after a restore."""
        out: dict[str, Any] = {}
        for name, value in vars(self).items():
            if name in self.STATE_SKIP or callable(value):
                continue
            try:
                out[name] = _enc(value, cap=self.STATE_DICT_CAPS.get(name, STATE_DICT_MAX),
                                 text=self.STATE_TEXT_CAPS.get(name, 0))
            except _NotKept:
                continue
        return out

    def restore(self, kept: dict[str, Any]) -> None:
        """Put a :meth:`state` back (on a driver made for the same session),
        then :meth:`after_restore`."""
        for name, value in (kept or {}).items():
            if name in self.STATE_SKIP or not isinstance(name, str):
                continue
            try:
                setattr(self, name, _dec(value))
            except Exception:  # noqa: BLE001 — a read-only attribute keeps its own value
                continue
        self.after_restore()

    def quiet_frame(self, obj: dict[str, Any]) -> bool:
        """v1.441.0 review: True for a frame that is only the answer to a
        background read Iron Jarvis made itself (Codex's terminal list): it
        never counts as hearing from the tool, or the reaper's two silent
        hours (``BACKGROUND_SILENT_MAX_S``) would never run out."""
        return False

    def after_restore(self) -> None:
        """Re-make what has no JSON form (a reply this driver waits for, a
        timer): a subclass's hook. The base driver needs none."""


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
