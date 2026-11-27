"""Your own models set themselves up (the intelligence plan, release 9).

The first time a model on this PC or the user's network is USED, it is
noted (:func:`note_used`, called from the router's one answered-call seam).
When the PC is idle, the noted models are tried one at a time with the same
battery Measure runs (tool calls, formats, how much it holds, how fast it
writes): made-up text only, nothing of the user's. No notice; the Model
servers card fills in by itself. The Measure button stays.

The rules, each pinned by a test:

* **Only when idle.** No keyboard or mouse input on this PC for
  :data:`IDLE_AFTER_S` (``envelope/idle``; a system that cannot tell is NOT
  idle), no chat reply being written and no run working, and Emergency stop
  off.
* **Stops when you type.** While a trial runs the same readings are taken
  every :data:`WATCH_S`; any input (or a reply, a run, Emergency stop) cancels
  it at once. A model whose trial was cut short :data:`MAX_CUTS` times is
  left for Measure.
* **One model at a time.** One worker, one trial; a model Measure is already
  measuring waits.
* **Never a cloud model, never a "local" address that forwards to one.** The
  eligibility check is the caller's (``routes/envelope.trial_refusal``) and is
  asked right before every trial: a refusal is final for that model; "not
  known yet" (a model server whose routing nobody has read) waits.
* **Never picks a model for you.** It measures; choosing stays the user's.

State is ``<home>/envelopes/trials.json`` (names and states only), written off
the loop. Every reading here is a plain callable, injected; time is an
injected clock and sleep, so tests never wait.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store import envelope_dir

log = logging.getLogger(__name__)

#: No keyboard or mouse input for this long, in seconds, is "idle".
IDLE_AFTER_S = 300.0
#: How often the worker looks while it waits, in seconds.
TICK_S = 30.0
#: How often a running trial checks that the PC is still idle, in seconds.
WATCH_S = 1.0
#: A trial cut short this many times is left for Measure.
MAX_CUTS = 3

#: What :meth:`AutoTrials.step` reports (for the loop's health row and tests).
NOTHING = "nothing"
NOT_IDLE = "not idle"
STOPPED = "emergency stop"
REFUSED = "refused"
WAITING = "waiting"
ALREADY = "already measured"
RAN = "ran"
CUT = "cut"
#: Release 9 review: the trial ran but no measurement came of it (the server
#: was asleep, the battery failed): counted like a cut, tried again later.
FAILED = "failed"

#: The offline suite turns the LIFESPAN loop off (tests/conftest.py): a test
#: that runs the app's lifespan must never read this PC's real idle time and
#: try a model. Tests of the worker drive :meth:`AutoTrials.step` directly.
_FORCED_OFF = False

#: What an eligibility check answers for "not known yet: ask again later".
WAIT = "wait"

PENDING = "pending"
DONE = "done"
LEFT = "left"


@dataclass
class Readings:
    """One look at the PC, taken off the loop."""

    idle_s: float | None = None
    busy: bool = True
    stopped: bool = True

    def idle(self, after: float) -> bool:
        return (
            not self.stopped and not self.busy and self.idle_s is not None and self.idle_s >= after
        )


@dataclass
class _Entry:
    state: str = PENDING
    cuts: int = 0
    why: str = ""
    #: What the last verdict was decided on (``fingerprint``); when it
    #: changes, a model left alone is looked at again.
    seen: str = ""


@dataclass
class AutoTrials:
    """The idle trial's worker. Every dependency is injected (see the module
    docstring); production builds it in ``routes/envelope.register``."""

    home: Path
    #: ``(provider) -> bool``: THE trusted-provider oracle (cloud and CLI).
    is_trusted: Callable[[str], bool]
    #: ``(provider, model) -> bool``: does a measurement already exist? BLOCKING.
    measured: Callable[[str, str], bool]
    #: ``(provider, model) -> ""|WAIT|reason``: may it be tried now? BLOCKING.
    refusal: Callable[[str, str], str]
    #: ``(provider, model) -> awaitable``: run the trial (the Measure battery).
    run: Callable[[str, str], Awaitable[Any]]
    #: ``() -> seconds since the last input on this PC | None``. BLOCKING.
    idle_seconds: Callable[[], "float | None"]
    #: ``() -> bool``: a chat reply is being written or a run is working. BLOCKING.
    busy: Callable[[], bool]
    #: ``() -> bool``: Emergency stop is on. BLOCKING.
    stopped: Callable[[], bool]
    #: ``(provider, model) -> str``: what a verdict is decided on (the
    #: address, the proxy's routing). BLOCKING-cheap. Release 9 review: when
    #: it changes, a model LEFT alone (refused, cut short, failed) is looked
    #: at again, its count starting afresh.
    fingerprint: Callable[[str, str], str] = lambda provider, model: ""
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    idle_after: float = IDLE_AFTER_S
    tick: float = TICK_S
    watch: float = WATCH_S
    _models: dict[str, _Entry] = field(default_factory=dict)
    _dirty: bool = False
    _loaded: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: The (provider, model) being tried right now, or None.
    running: "tuple[str, str] | None" = None

    # ------------------------------------------------------------------ #
    # noting a model (the router's seam: sync, cheap, never raises)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}\u0000{model}"

    def note_used(self, provider: str, model: str) -> None:
        """A model answered a call. The first time an untrusted one does, it
        waits for an idle moment. A dict look-up after that."""
        try:
            provider = str(provider or "").strip()
            model = str(model or "").strip()
            if not provider or not model or provider == "mock":
                return
            key = self._key(provider, model)
            with self._lock:
                if key in self._models:
                    return
            if self.is_trusted(provider):
                return
            with self._lock:
                if key not in self._models:
                    self._models[key] = _Entry()
                    self._dirty = True
        except Exception:  # noqa: BLE001 — a note never breaks a reply
            pass

    def pending(self) -> list[tuple[str, str]]:
        with self._lock:
            keys = [k for k, e in self._models.items() if e.state == PENDING]
        return [tuple(k.split("\u0000", 1)) for k in keys]  # type: ignore[misc]

    def state_of(self, provider: str, model: str) -> str:
        with self._lock:
            entry = self._models.get(self._key(provider, model))
        return entry.state if entry is not None else ""

    # ------------------------------------------------------------------ #
    # persistence (off the loop)
    # ------------------------------------------------------------------ #
    def _path(self) -> Path:
        return envelope_dir(self.home) / "trials.json"

    def _load(self) -> None:
        """BLOCKING. A missing or broken file is an empty one."""
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
            rows = data.get("models") if isinstance(data, dict) else None
        except (OSError, ValueError):
            rows = None
        with self._lock:
            for key, row in (rows or {}).items():
                if not isinstance(key, str) or "\u0000" not in key or not isinstance(row, dict):
                    continue
                state = row.get("state")
                if state not in (PENDING, DONE, LEFT):
                    continue
                cuts = row.get("cuts")
                self._models.setdefault(key, _Entry(
                    state=state,
                    cuts=cuts if isinstance(cuts, int) and not isinstance(cuts, bool) and cuts >= 0 else 0,
                    why=str(row.get("why") or "")[:200],
                    seen=str(row.get("seen") or "")[:500],
                ))
            self._loaded = True

    def _save(self) -> None:
        """BLOCKING; atomic (write a sibling, then replace)."""
        with self._lock:
            if not self._dirty:
                return
            payload = {
                "v": 1,
                "models": {
                    k: {"state": e.state, "cuts": e.cuts, "why": e.why, "seen": e.seen}
                    for k, e in self._models.items()
                },
            }
            self._dirty = False
        try:
            path = self._path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.debug("model trials: could not save", exc_info=True)
            with self._lock:
                self._dirty = True

    def _set(
        self, provider: str, model: str, state: str, why: str = "", cut: bool = False, seen: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._models.setdefault(self._key(provider, model), _Entry())
            entry.state = state
            entry.why = why[:200]
            if cut:
                entry.cuts += 1
            if seen is not None:
                entry.seen = seen
            self._dirty = True
            # A model that waits goes to the back of the line.
            key = self._key(provider, model)
            self._models[key] = self._models.pop(key)

    # ------------------------------------------------------------------ #
    # the worker
    # ------------------------------------------------------------------ #
    def _read(self) -> Readings:
        """BLOCKING: one look at the PC. A reading that fails reads as busy."""
        out = Readings()
        try:
            out.stopped = bool(self.stopped())
        except Exception:  # noqa: BLE001
            out.stopped = True
        try:
            out.busy = bool(self.busy())
        except Exception:  # noqa: BLE001
            out.busy = True
        try:
            value = self.idle_seconds()
            out.idle_s = float(value) if value is not None else None
        except Exception:  # noqa: BLE001
            out.idle_s = None
        return out

    async def step(self) -> str:
        """One decision. Tries at most ONE model, and returns what happened."""
        if not self._loaded:
            await asyncio.to_thread(self._load)
        try:
            return await self._step()
        finally:
            await asyncio.to_thread(self._save)

    def _look_again(self) -> None:
        """BLOCKING-cheap: a model LEFT alone, or waiting with cuts counted,
        whose address or proxy routing changed since its verdict is looked
        at again, its count afresh."""
        with self._lock:
            rows = [(k, e.seen) for k, e in self._models.items()
                    if e.state == LEFT or (e.state == PENDING and e.cuts)]
        for key, seen in rows:
            provider, model = key.split("\u0000", 1)
            try:
                now = str(self.fingerprint(provider, model) or "")
            except Exception:  # noqa: BLE001
                continue
            if now and now != seen:
                with self._lock:
                    entry = self._models.get(key)
                    if entry is not None:
                        entry.state, entry.cuts, entry.why, entry.seen = PENDING, 0, "looked at again", now
                        self._dirty = True

    def _fp(self, provider: str, model: str) -> str:
        try:
            return str(self.fingerprint(provider, model) or "")
        except Exception:  # noqa: BLE001
            return ""

    async def _step(self) -> str:
        await asyncio.to_thread(self._look_again)
        waiting = self.pending()
        if not waiting:
            return NOTHING
        look = await asyncio.to_thread(self._read)
        if look.stopped:
            return STOPPED
        if not look.idle(self.idle_after):
            return NOT_IDLE
        provider, model = waiting[0]
        if await asyncio.to_thread(self.measured, provider, model):
            self._set(provider, model, DONE, "measured already")
            return ALREADY
        why = await asyncio.to_thread(self.refusal, provider, model)
        if why == WAIT:
            self._set(provider, model, PENDING, "not known yet")
            return WAITING
        if why:
            self._set(provider, model, LEFT, why, seen=await asyncio.to_thread(self._fp, provider, model))
            return REFUSED
        return await self._try(provider, model)

    async def _count_against(self, provider: str, model: str, why: str) -> None:
        """A trial cut short or that measured nothing: counted; at
        :data:`MAX_CUTS` the model is left for Measure (until its address or
        routing changes)."""
        with self._lock:
            entry = self._models.get(self._key(provider, model))
            cuts = (entry.cuts if entry else 0) + 1
        seen = await asyncio.to_thread(self._fp, provider, model)
        if cuts >= MAX_CUTS:
            self._set(provider, model, LEFT, f"{why} {cuts} times", cut=True, seen=seen)
        else:
            self._set(provider, model, PENDING, why, cut=True, seen=seen)

    async def _try(self, provider: str, model: str) -> str:
        """Run ONE trial, watching the PC every :data:`WATCH_S`; cancel it the
        moment the PC is not idle any more."""
        self.running = (provider, model)
        task = asyncio.ensure_future(self.run(provider, model))
        cut = False
        stopped = False
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=self.watch)
                if task.done():
                    break
                look = await asyncio.to_thread(self._read)
                if not look.idle(self.idle_after):
                    cut = True
                    # Emergency stop is not the model's fault: not a cut.
                    stopped = look.stopped
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    break
            if not cut:
                with contextlib.suppress(Exception):
                    task.result()
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise
        finally:
            self.running = None
        if cut and stopped:
            self._set(provider, model, PENDING, "stopped by Emergency stop")
            return STOPPED
        if cut:
            await self._count_against(provider, model, "cut short")
            return CUT
        # Release 9 review: DONE only when a measurement really landed (an
        # asleep server, a battery that failed: tried again later).
        if not await asyncio.to_thread(self.measured, provider, model):
            await self._count_against(provider, model, "measured nothing")
            return FAILED
        self._set(provider, model, DONE, "tried")
        return RAN

    async def run_forever(self, on_tick: "Callable[[bool, Exception | None], Any] | None" = None) -> None:
        """The lifespan's loop: one :meth:`step`, then :data:`TICK_S`. A step
        that raises is reported, never the end of the loop."""
        while True:
            try:
                if not _FORCED_OFF:
                    await self.step()
                if on_tick is not None:
                    on_tick(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("model trials: a step failed", exc_info=True)
                if on_tick is not None:
                    on_tick(False, exc)
            await self.sleep(self.tick)


#: The service the router's seam reaches (installed by
#: ``routes/envelope.register``; None in a bare test platform).
_SERVICE: "AutoTrials | None" = None


def install(service: "AutoTrials | None") -> None:
    global _SERVICE
    _SERVICE = service


def note_used(provider: str, model: str) -> None:
    """THE ROUTER'S SEAM: a model answered a call. Never raises."""
    service = _SERVICE
    if service is not None:
        service.note_used(provider, model)


__all__ = [
    "ALREADY", "AutoTrials", "CUT", "IDLE_AFTER_S", "MAX_CUTS", "NOTHING", "NOT_IDLE", "RAN",
    "REFUSED", "STOPPED", "WAIT", "WAITING", "FAILED", "install", "note_used",
]
