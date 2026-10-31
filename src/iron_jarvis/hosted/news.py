"""Build › Code's running work, seen by the rest of the app (v1.414.0).

Build › Code drives the user's REAL Claude Code / Codex / Pi / OpenCode
(``hosted/``). Until v1.414.0 a reply there was invisible everywhere else:
Running now said "Nothing is running", the status orb sat idle, the update
warning named nothing, the keep-awake blocker let the PC sleep in the middle
of an hour-long edit, a long reply ended with no notice, and a card left
unanswered never reached the phone. This module is the ONE place those
facts come from — in memory, never a database read on the poll paths:

* :func:`running_rows` — one row per Build › Code reply running now, the
  shape ``durable_turns.running_replies`` gives a chat's or a Chat › Code
  reply (``surface: "build_app"``), with ``waiting`` = a card or a question
  is open on it (the reply goes on only when the user answers).
* :func:`counts` — ``{running, working}`` for ``/health`` and
  ``/system/activity``. WORKING follows the v1.391.0 rule exactly: a reply
  parked on an unanswered card is BUSY (the update still cuts it off, so the
  warning names it) but NOT working (the PC may sleep; the card waits).
* :func:`announce_finished` — ``build.turn_finished`` for a reply that ran
  longer than :data:`FINISH_NOTICE_AFTER_S` and ENDED (finished or failed),
  mirroring Chat › Code's (``routes/build._announce_turn_finished``): never
  for a Stop, a shutdown cut-off or an account's limit card. The SAME event
  type with ``surface: "build_app"`` — the bell, the tray and
  ``comm/notifier.bell_pings`` read one type, and only the LINK differs
  (``/terminals?view=code&code=<id>``).
* :class:`PhoneWatch` — a card nobody answered in the app for
  ``harness/stall.PHONE_AFTER_S`` goes to the phone through the SAME path as
  Chat › Code's (``stall.PhoneAsk``: ``approval.requested`` with
  ``surface: "build"``, ``comm/prompts`` registers the answerable prompt),
  filed in ``platform.approvals`` so the phone's answer resolves it; a phone
  "approve" answers ONCE (``allow``), never for the session. NEVER sent: a
  question (the phone can only say yes or no), a once-only card, any card
  while the session is in Full access, and every kind ``stall.phone_may_ask``
  refuses — the phone is a cloud service.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: The bell's "Your coding session finished" waits for a reply longer than
#: this (Chat › Code's own value, ``routes/build.FINISH_NOTICE_AFTER_S``).
FINISH_NOTICE_AFTER_S = 60.0
#: The ``surface`` every Build › Code row and notice carries.
SURFACE = "build_app"
#: The ledger / event session id of a Build › Code session.
SESSION_PREFIX = "build_app:"


def session_id_for(sid: str) -> str:
    return f"{SESSION_PREFIX}{sid}"


def _iso(dt: Any) -> str:
    try:
        from ..daemon.durable_turns import _iso_utc

        return _iso_utc(dt)
    except Exception:  # noqa: BLE001
        return ""


def running_rows(platform: Any) -> list[dict[str, Any]]:
    """Every Build › Code reply running now. In memory; never raises; [] when
    the Code view was never used (the service is made on first use)."""
    svc = getattr(platform, "hosted", None)
    if svc is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        for live in list(svc.live.values()):
            turn = live.turn
            if turn is None or not turn.running:
                continue
            rows.append({
                "turn_id": turn.id,
                "thread_id": live.sid,
                "surface": SURFACE,
                "harness": live.harness,
                "started_at": _iso(turn.started_at),
                "waiting": bool(live.asks),
                "title": getattr(live, "title", "") or "",
            })
    except Exception:  # noqa: BLE001 — a listing never fails over this
        log.warning("could not list running Build › Code replies", exc_info=True)
        return []
    rows.sort(key=lambda r: r["started_at"])
    return rows


def counts(platform: Any) -> dict[str, Any]:
    """``{running, working, tools}``: replies running, the part not parked on
    the user, and the tools' names (the update warning says them). Never
    raises."""
    rows = running_rows(platform)
    try:
        from .words import label

        tools = sorted({label(r["harness"]) for r in rows})
    except Exception:  # noqa: BLE001
        tools = []
    return {
        "running": len(rows),
        "working": sum(1 for r in rows if not r["waiting"]),
        "tools": tools,
    }


def outcome_of(stop: str) -> str:
    """"done" | "failed" | "" (no notice: stopped, cut off)."""
    stop = str(stop or "")
    if stop in ("completed", "done", "max_turns"):
        return "done"
    if stop in ("failed", "error"):
        return "failed"
    return ""


async def announce_finished(platform: Any, sid: str, turn_id: str, title: str, harness: str,
                            stop: str, seconds: float) -> bool:
    """Publish ``build.turn_finished`` for a long reply that ended. Returns
    whether it did. Never raises (a notice never fails a turn)."""
    outcome = outcome_of(stop)
    if not outcome or seconds <= FINISH_NOTICE_AFTER_S:
        return False
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return False
    try:
        from ..core.events import EventType

        await bus.publish(EventType.BUILD_TURN_FINISHED, {
            "thread_id": sid,
            "turn_id": turn_id,
            "title": str(title or ""),
            "seconds": int(seconds),
            "outcome": outcome,
            "surface": SURFACE,
            "harness": harness,
        }, session_id=session_id_for(sid))
        return True
    except Exception:  # noqa: BLE001
        log.warning("announcing a Build › Code reply's end failed", exc_info=True)
        return False


def phone_may_send(ask: Any, mode: str) -> bool:
    """May this Build › Code card go to the phone? Only an APPROVAL (a
    question needs words the phone's yes/no cannot give), never a once-only
    card, never any card while the session is in Full access, and never a
    kind ``harness/stall.phone_may_ask`` refuses."""
    from ..harness.stall import phone_may_ask

    if getattr(ask, "kind", "") != "approval":
        return False
    if str(mode or "") == "full":
        return False
    event = getattr(ask, "event", None) or {}
    once = bool(event.get("once_only"))
    priced = bool(event.get("cost_note"))
    local_ask = bool(event.get("local_only"))
    return phone_may_ask(once_only=once, local_ask=local_ask, priced=priced)


class _BuildAppPhoneAsk:
    """``stall.PhoneAsk`` for a Build › Code session: its title is read from
    the Code view's own row (a Chat › Code thread lookup finds nothing)."""

    @staticmethod
    def make(platform: Any, approval_id: str, tool: str, sid: str, title: str) -> Any:
        from ..harness.stall import PhoneAsk

        class _Ask(PhoneAsk):
            async def _title(self) -> str:  # noqa: D401
                return title

        return _Ask(platform, approval_id, tool, thread_id=sid, after_s=0.0)


class PhoneWatch:
    """One Build › Code card on its way to the phone (see the module doc).

    :meth:`start` waits :data:`after_s` (``stall.PHONE_AFTER_S``); if the card
    is still open, it is filed in ``platform.approvals`` and announced.
    The phone's answer resolves that entry: ``once`` → the card is answered
    ``allow`` (ONCE), ``deny`` → ``deny``. :meth:`close` (answered in the app,
    or the card expired with its process) cancels the wait, takes the entry
    back and closes the phone's prompt."""

    def __init__(self, svc: Any, live: Any, ask: Any, *, after_s: float | None = None) -> None:
        from ..harness.stall import PHONE_AFTER_S

        self.svc = svc
        self.live = live
        self.ask = ask
        self.after_s = PHONE_AFTER_S if after_s is None else float(after_s)
        self.task: asyncio.Task | None = None
        self.approval_id = ""
        self.phone: Any = None
        self.closed = False
        self._answering = False

    def start(self) -> None:
        from ..harness.stall import keep

        with contextlib.suppress(RuntimeError):
            self.task = keep(asyncio.ensure_future(self._run()))

    async def _run(self) -> None:
        platform = self.svc.platform
        try:
            await asyncio.sleep(self.after_s)
            if self.closed or self.ask.id not in self.live.asks:
                return
            from . import store

            row = await asyncio.to_thread(store.get, self.svc.engine, self.live.sid)
            mode = str(getattr(row, "mode", "") or "") if row is not None else ""
            title = str(getattr(row, "title", "") or "") if row is not None else ""
            # Read again after the hop: answered in the app meanwhile.
            if self.closed or self.ask.id not in self.live.asks or not phone_may_send(self.ask, mode):
                return
            approvals = getattr(platform, "approvals", None)
            if approvals is None or not callable(getattr(approvals, "request", None)):
                return
            # Names only on the registry entry (the bell's posture).
            self.approval_id, fut = approvals.request(
                self.ask.tool, {}, session_id=session_id_for(self.live.sid),
            )
            self.phone = _BuildAppPhoneAsk.make(
                platform, self.approval_id, self.ask.tool, self.live.sid, title,
            )
            self.phone.tick()
            try:
                decision = await fut
            finally:
                with contextlib.suppress(Exception):
                    approvals.pop(self.approval_id)
            if self.closed or self.ask.id not in self.live.asks:
                return
            # A phone answer is ONCE, never for the session (v1.200.0).
            mapped = "deny" if decision == "deny" else "allow" if decision in ("once", "conversation") else ""
            if not mapped:
                return
            # The answer's own ``resolved`` closes this watch (``close``): it
            # must never cancel the task that is delivering the answer.
            self._answering = True
            try:
                await self.svc.answer(self.live.sid, self.ask.id, mapped)
            except Exception:  # noqa: BLE001 — answered elsewhere meanwhile
                log.info("a Build › Code card answered from the phone was already gone")
            if not self.closed:
                self.closed = True
                if self.phone is not None:
                    self.phone.close(mapped)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — the card in the app still waits
            log.warning("sending a Build › Code card to the phone failed", exc_info=True)

    def close(self, decision: str) -> None:
        if self.closed:
            return
        self.closed = True
        if self.task is not None and not self.task.done() and not self._answering:
            self.task.cancel()
        approvals = getattr(self.svc.platform, "approvals", None)
        if self.approval_id and approvals is not None:
            with contextlib.suppress(Exception):
                approvals.pop(self.approval_id)
        if self.phone is not None:
            self.phone.close(decision)


def now() -> float:
    return time.monotonic()
