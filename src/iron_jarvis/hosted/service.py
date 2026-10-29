"""The Code view's sessions, turns, asks and processes (v1.409.0).

ONE :class:`HostedService` per platform (``platform.hosted``, made on first
use by :func:`service_for`). The routes (``daemon/routes/build_app.py``) are
thin: every rule lives here.

THE TURN IS THE DAEMON'S (the ``durable_turns`` pattern): ``POST …/turn``
starts it as its own task and the response only FOLLOWS it
(:meth:`HostedTurn.follow`: what it said so far, then each new event, then
the end). Closing the page never stops a turn — only Stop (``POST
…/interrupt``) does, or the harness ending. Any number of windows rejoin
(``GET …/turns/{id}/stream``); a turn this process no longer holds is replayed
from the session's ``events.jsonl``.

ONE TURN AT A TIME: a message sent while one runs is refused in words
(:data:`hosted.words.BUSY`) — Claude Code would queue it, Codex would not, and
a queued message would have no turn of its own to follow.

THE POOL: at most :data:`MAX_LIVE` harness processes; when a new one is needed
the oldest IDLE one (no turn, no open ask) is stopped first, else the start is
refused in words. A process with no turn for :data:`IDLE_S` is stopped by the
reaper; the next message starts it again RESUMING THE SAME harness session
(``claude --resume <id>`` / ``thread/resume``). Archive, delete and the
daemon's shutdown stop it. Every stop is a tree kill, off the loop.

NOTHING IS GRANTED HERE: every permission question the harness asks becomes a
card (``approval`` / ``question``); only the user's answer goes back. "Allow
for this session" is the harness's OWN session-wide allow, offered only when
the harness offers one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.ids import utcnow
from . import store
from . import words as W
from .claude import HARNESS as CLAUDE
from .claude import MODE_LABELS
from .claude import ClaudeDriver
from .claude import MODES as CLAUDE_MODES
from .codex import HARNESS as CODEX
from .codex import HIDDEN_MODES as CODEX_HIDDEN
from .codex import MODES as CODEX_MODES
from .codex import CodexDriver
from .driver import DECISIONS, Ask, AskRefused, ControlRefused, Driver
from .events import TEXT_CHARS, EventLog, clip, make, session_dir
from .process import HarnessGone, HarnessProcess, binary_for, build_env

log = logging.getLogger(__name__)

HARNESSES: tuple[str, ...] = (CLAUDE, CODEX)
#: At most this many harness processes at once.
MAX_LIVE = 6
#: A process with no turn for this long is stopped by the reaper.
IDLE_S = 15 * 60.0
#: How often the reaper looks.
REAP_EVERY_S = 60.0
#: How long a finished turn stays in memory for a rejoin.
FINISHED_KEEP_S = 600.0
#: The newest events ``GET /build/app/sessions/{id}`` returns by default.
DEFAULT_EVENTS = 2000

#: Effort levels each harness takes.
EFFORTS: dict[str, tuple[str, ...]] = {
    CLAUDE: ("low", "medium", "high", "xhigh", "max"),
    CODEX: ("minimal", "low", "medium", "high", "xhigh"),
}
_MODEL_RX = re.compile(r"^[A-Za-z0-9._:/\-\[\]]{1,120}$")
#: A harness session id typed into a terminal: a UUID (Claude) or Codex's
#: UUID-shaped thread id — nothing else is ever typed.
_SID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class Refused(Exception):
    """A request the Code view refuses, in one plain sentence."""

    def __init__(self, sentence: str, status: int = 409) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status


def same_folder(a: Any, b: Any) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (TypeError, ValueError):
        return False


def modes_for(harness: str) -> dict[str, Any]:
    return CLAUDE_MODES if harness == CLAUDE else CODEX_MODES


def mode_rows(harness: str) -> list[dict[str, Any]]:
    return [
        {"id": m, "label": MODE_LABELS[m], "dangerous": m == "full"}
        for m in ("ask", "edits", "plan", "full") if m in modes_for(harness)
    ]


def hidden_mode_rows(harness: str) -> list[dict[str, str]]:
    if harness == CLAUDE:
        return []
    return [{"id": m, "label": MODE_LABELS[m], "reason": why} for m, why in CODEX_HIDDEN.items()]


def check_mode(harness: str, mode: str, confirm: bool) -> str:
    mode = str(mode or "ask").strip().lower()
    if mode not in modes_for(harness):
        hidden = dict((r["id"], r["reason"]) for r in hidden_mode_rows(harness))
        if mode in hidden:
            raise Refused(f"{MODE_LABELS.get(mode, mode)} is not offered for {W.label(harness)}: {hidden[mode]}", 400)
        raise Refused(f'"{mode}" is not a mode — use ask, edits, plan or full.', 400)
    if mode == "full" and not confirm:
        raise Refused(W.FULL_NEEDS_CONFIRM.format(label=W.label(harness)), 400)
    return mode


def check_model(model: Any) -> str:
    text = str(model or "").strip()
    if text and not _MODEL_RX.match(text):
        raise Refused("That model name has characters a model id never has.", 400)
    return text


def check_effort(harness: str, effort: Any) -> str:
    text = str(effort or "").strip().lower()
    if text and text not in EFFORTS[harness]:
        raise Refused(f'"{text}" is not an effort {W.label(harness)} takes — use '
                      f"{', '.join(EFFORTS[harness])}.", 400)
    return text


# --------------------------------------------------------------------------- #
# Turns
# --------------------------------------------------------------------------- #
def sse(event: str, data: Any) -> str:
    from ..daemon.durable_turns import sse as _sse

    return _sse(event, data)


class HostedTurn:
    """One message and everything the harness did with it."""

    def __init__(self, turn_id: str, sid: str) -> None:
        self.id = turn_id
        self.sid = sid
        self.events: list[dict[str, Any]] = []
        self.status = "running"
        self._followers: set[asyncio.Queue] = set()
        self.finished = asyncio.Event()
        self.finished_at: float | None = None
        self.task: asyncio.Task | None = None
        self.started_at = utcnow()

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def watched(self) -> bool:
        return bool(self._followers)

    def add(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)
        for q in list(self._followers):
            q.put_nowait(ev)

    def end(self, status: str) -> None:
        if not self.running:
            return
        self.status = status
        self.finished_at = time.monotonic()
        for q in list(self._followers):
            q.put_nowait(None)
        self.finished.set()

    async def follow(self) -> AsyncIterator[str]:
        """Everything so far as one chunk, then each new event, until the end.
        Snapshot and subscription in one step (no await between)."""
        q: asyncio.Queue = asyncio.Queue()
        head = "".join(sse(e["type"], e) for e in self.events)
        running = self.running
        if running:
            self._followers.add(q)
        try:
            if head:
                yield head
            if not running:
                return
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield sse(ev["type"], ev)
        finally:
            self._followers.discard(q)


class LiveSession:
    """One session as this process holds it."""

    def __init__(self, sid: str, harness: str, log_: EventLog) -> None:
        self.sid = sid
        self.harness = harness
        self.log = log_
        self.proc: HarnessProcess | None = None
        self.driver: Driver | None = None
        self.turn: HostedTurn | None = None
        self.asks: dict[str, Ask] = {}
        self.last_active = time.monotonic()
        self.lock = asyncio.Lock()
        #: The process must restart before the next message (an effort change).
        self.restart = False
        self.limit: Any = None
        self.limit_since = ""
        self.last_error = ""
        #: The Iron-Proxy account the session runs on ("" = this PC's login).
        self.account = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.alive

    @property
    def busy(self) -> bool:
        return (self.turn is not None and self.turn.running) or bool(self.asks)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
class HostedService:
    def __init__(self, platform: Any, *, clock: Any = time.monotonic) -> None:
        self.platform = platform
        self.engine = platform.engine
        self.home = Path(platform.config.home)
        self.clock = clock
        self.live: dict[str, LiveSession] = {}
        self.turns: dict[str, HostedTurn] = {}
        self._reaper: asyncio.Task | None = None
        self._closing = False
        self._pool_lock: asyncio.Lock | None = None

    # -- helpers -----------------------------------------------------------------
    def _lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    async def _row(self, sid: str) -> Any:
        row = await asyncio.to_thread(store.get, self.engine, sid)
        if row is None:
            raise Refused("No such Code-view session.", 404)
        return row

    def _live(self, row: Any) -> LiveSession:
        live = self.live.get(row.id)
        if live is None:
            live = LiveSession(row.id, row.harness, EventLog(self.home, row.id))
            self.live[row.id] = live
        return live

    def _evict_turns(self) -> None:
        now = time.monotonic()
        for tid, t in list(self.turns.items()):
            if t.finished_at is not None and now - t.finished_at > FINISHED_KEEP_S:
                self.turns.pop(tid, None)

    # -- the harnesses -----------------------------------------------------------
    def harnesses(self) -> list[dict[str, Any]]:
        """What can be started, from CACHED reads only (never a probe)."""
        from ..harness import build_accounts as BA
        from ..providers.cli_auth import DEFAULT_PROBE, never_probe

        snap = BA.snapshot(getattr(self.platform, "iron_proxy", None))
        out = []
        for h in HARNESSES:
            cli = W.CLI[h]
            installed = binary_for(h) is not None
            with never_probe():
                try:
                    signed = DEFAULT_PROBE.verdict(cli)
                except Exception:  # noqa: BLE001
                    signed = None
            accounts = []
            if snap is not None:
                for r in BA.vendor_rows(snap, "claude" if h == CLAUDE else "codex"):
                    accounts.append({"id": r["id"], "name": r["name"], "status": r["status"]})
            out.append({
                "id": h,
                "label": W.label(h),
                "installed": installed,
                "signed_in": signed if installed else None,
                "version": None,
                "modes": mode_rows(h),
                "hidden_modes": hidden_mode_rows(h),
                "models": self._models(h),
                "efforts": list(EFFORTS[h]),
                "accounts": accounts,
            })
        return out

    def _models(self, harness: str) -> list[dict[str, str]]:
        rows = [{"id": "", "label": f"{W.label(harness)}'s default"}]
        if harness == CLAUDE:
            try:
                from ..providers import claude_models

                cat = claude_models.catalog(refresh=False)
                for m in cat.get("models") or []:
                    if isinstance(m, dict) and m.get("id"):
                        rows.append({"id": str(m["id"]), "label": str(m.get("label") or m["id"])})
            except Exception:  # noqa: BLE001 — the default row still works
                log.debug("claude model catalog unreadable", exc_info=True)
        return rows

    # -- sessions ----------------------------------------------------------------
    async def create(self, body: dict[str, Any]) -> dict[str, Any]:
        harness = str(body.get("harness") or "").strip().lower()
        if harness not in HARNESSES:
            raise Refused('Choose "claude-code" or "codex".', 400)
        folder = str(body.get("folder") or "").strip()
        if not folder:
            raise Refused("Choose a folder for the session.", 400)
        from ..core.fs_policy import root_problem

        problem = await asyncio.to_thread(root_problem, folder)
        if problem:
            raise Refused(problem, 400)
        mode = check_mode(harness, body.get("mode") or "ask", bool(body.get("confirm")))
        model = check_model(body.get("model"))
        effort = check_effort(harness, body.get("effort"))
        resume_id = str(body.get("harness_session_id") or "").strip()
        title = clip(body.get("title") or "", 120)
        if resume_id:
            found = await asyncio.to_thread(self._disk_session, harness, folder, resume_id)
            if found is None:
                raise Refused(f"{W.label(harness)} has no saved conversation with that id in this folder.", 404)
            title = title or clip(found.get("title") or "", 120)
        account = str(body.get("account") or "").strip()
        acc = await asyncio.to_thread(self._resolve_account, harness, account)
        row = await asyncio.to_thread(
            store.create, self.engine, harness=harness, folder=folder, title=title,
            account=str(acc.get("id") or ""), account_json=json.dumps(acc) if acc else "",
            model=model, effort=effort, mode=mode, harness_session_id=resume_id,
        )
        return store.view(row)

    def _resolve_account(self, harness: str, account: str) -> dict[str, Any]:
        """BLOCKING: the account record a session starts on (a pane's rules:
        an explicit id or "default" is exactly that; none = Iron-Proxy's
        first free account from the CACHED snapshot, else this PC's login)."""
        from ..terminals import pane_accounts as PA

        provider = W.PROXY[harness]
        svc = getattr(self.platform, "iron_proxy", None)
        request = {provider: account} if account else {}
        try:
            got = PA.resolve(svc, request)
        except PA.PaneAccountRefused as refused:
            raise Refused(refused.sentence, refused.status) from None
        rec = got.records.get(provider)
        return dict(rec) if rec else {}

    async def list_sessions(self, *, archived: bool = False, folder: str = "", disk: bool = False) -> dict[str, Any]:
        rows = await asyncio.to_thread(store.list_rows, self.engine, archived=archived, folder=folder)
        out = [self._with_live(store.view(r)) for r in rows]
        result: dict[str, Any] = {"sessions": out}
        if disk:
            if not folder:
                raise Refused("Name the folder whose saved sessions to list.", 400)
            used = {r.harness_session_id for r in rows if r.harness_session_id}
            result["disk"] = await asyncio.to_thread(self._disk_rows, folder, used)
        return result

    def _with_live(self, view: dict[str, Any]) -> dict[str, Any]:
        live = self.live.get(view["id"])
        view["running"] = bool(live and live.turn and live.turn.running)
        view["process"] = bool(live and live.running)
        view["waiting"] = bool(live and live.asks)
        return view

    def _disk_rows(self, folder: str, used: set[str]) -> list[dict[str, Any]]:
        """BLOCKING: the harnesses' OWN saved sessions for ``folder`` (the
        history reader: read-only, names only)."""
        from .. import history

        out = []
        try:
            rows = history.list_sessions(None, limit=500)
        except Exception:  # noqa: BLE001 — a listing never fails over this
            return []
        for s in rows:
            if s.get("harness") not in HARNESSES or not same_folder(s.get("project"), folder):
                continue
            if s.get("id") in used:
                continue
            out.append({
                "source": "disk", "harness": s["harness"], "harness_session_id": s.get("id"),
                "title": clip(s.get("title") or "", 120), "folder": folder,
                "started": s.get("started"), "ended": s.get("ended"),
            })
        return out

    def _disk_session(self, harness: str, folder: str, sid: str) -> dict[str, Any] | None:
        from .. import history

        try:
            for s in history.list_sessions(harness, limit=500):
                if s.get("id") == sid and same_folder(s.get("project"), folder):
                    return s
        except Exception:  # noqa: BLE001
            return None
        return None

    async def view(self, sid: str, *, limit: int = DEFAULT_EVENTS) -> dict[str, Any]:
        row = await self._row(sid)
        log_ = self.live[sid].log if sid in self.live else EventLog(self.home, sid)
        events, total, dropped = await asyncio.to_thread(log_.read, max(1, min(int(limit or DEFAULT_EVENTS), 20_000)))
        live = self.live.get(sid)
        out: dict[str, Any] = {
            "session": self._with_live(store.view(row)),
            "events": events,
            "events_total": total,
            "dropped": dropped,
            "running": bool(live and live.turn and live.turn.running),
            "turn_id": live.turn.id if live and live.turn and live.turn.running else None,
            "pending": [a.event for a in (live.asks.values() if live else [])],
        }
        if live and live.driver and live.driver.commands:
            out["commands"] = list(live.driver.commands)
        return out

    async def patch(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        row = await self._row(sid)
        fields: dict[str, Any] = {}
        applied: dict[str, str] = {}
        live = self.live.get(sid)
        if "title" in body and body["title"] is not None:
            fields["title"] = clip(body["title"], 120)
        if body.get("mode") is not None:
            mode = check_mode(row.harness, body["mode"], bool(body.get("confirm")))
            if live and live.running and live.driver:
                try:
                    applied["mode"] = await live.driver.set_mode(mode)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                applied["mode"] = "next"
            fields["mode"] = mode
        if body.get("model") is not None:
            model = check_model(body["model"])
            if live and live.running and live.driver and model:
                try:
                    applied["model"] = await live.driver.set_model(model)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                applied["model"] = "next"
                if live and live.running and row.harness == CLAUDE:
                    live.restart = True  # back to the CLI's default: a restart
            fields["model"] = model
        if body.get("effort") is not None:
            effort = check_effort(row.harness, body["effort"])
            if live and live.driver:
                live.driver.effort = effort
            if row.harness == CLAUDE and live and live.running:
                live.restart = True  # --effort is read at start only
            applied["effort"] = "next"
            fields["effort"] = effort
        if fields:
            row = await asyncio.to_thread(store.update, self.engine, sid, **fields)
        return {"session": self._with_live(store.view(row)), "applied": applied}

    async def archive(self, sid: str, archived: bool = True) -> dict[str, Any]:
        row = await self._row(sid)
        if archived:
            await self._stop(sid, why="archived")
        row = await asyncio.to_thread(
            store.update, self.engine, sid, archived_at=utcnow() if archived else None,
        )
        return self._with_live(store.view(row))

    async def delete(self, sid: str) -> dict[str, Any]:
        await self._row(sid)
        live = self.live.get(sid)
        if live and live.turn and live.turn.running:
            raise Refused(W.RUNNING)
        await self._stop(sid, why="deleted")
        live = self.live.pop(sid, None)
        if live:
            await asyncio.to_thread(live.log.close)
        await asyncio.to_thread(store.delete, self.engine, sid)
        moved = await asyncio.to_thread(self._trash_events, sid)
        return {"ok": True, "deleted": sid, "events_moved_to": moved}

    def _trash_events(self, sid: str) -> str:
        """BLOCKING: the session's events folder moved to Iron Jarvis's own
        trash (never deleted). The harness's own session file is untouched."""
        src = session_dir(self.home, sid)
        if not src.exists():
            return ""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        dest = self.home / "trash" / stamp / "hosted" / sid
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            return str(dest)
        except OSError:
            log.warning("could not move a Code-view session's events to the trash", exc_info=True)
            return ""

    # -- the process ---------------------------------------------------------------
    async def _ensure_running(self, live: LiveSession, row: Any) -> None:
        if live.running and not live.restart:
            return
        if live.running and live.restart:
            await self._stop_process(live)
        live.restart = False
        argv0 = binary_for(row.harness)
        if argv0 is None:
            raise Refused(W.NOT_INSTALLED.format(label=W.label(row.harness)))
        async with self._lock():
            others = [lv for lv in self.live.values() if lv is not live and lv.running]
            if len(others) >= MAX_LIVE:
                idle = sorted((lv for lv in others if not lv.busy), key=lambda lv: lv.last_active)
                if not idle:
                    raise Refused(W.POOL_FULL)
                await self._stop_process(idle[0])
            await self._start_process(live, row, argv0)
        self._ensure_reaper()

    async def _start_process(self, live: LiveSession, row: Any, argv0: list[str]) -> None:
        from ..terminals import pane_accounts as PA

        loop = asyncio.get_running_loop()
        acc = store.account_record(row)
        accounts = PA.from_recorded({W.PROXY[row.harness]: acc})[0] if acc else None
        env = build_env(accounts)
        resume = bool(row.harness_session_id)
        sid = row.harness_session_id or (str(uuid.uuid4()) if row.harness == CLAUDE else "")
        cls = ClaudeDriver if row.harness == CLAUDE else CodexDriver
        proc_holder: dict[str, HarnessProcess] = {}

        async def write(obj: dict[str, Any]) -> None:
            p = proc_holder["p"]
            await asyncio.to_thread(p.write, obj)

        driver = cls(
            write=write,
            emit=lambda ev: self._emit(live, ev),
            ask=lambda a: self._ask(live, a),
            on_session_id=lambda s: self._on_session_id(live, s),
            mode=row.mode, model=row.model, effort=row.effort, cwd=row.folder,
        )
        proc = HarnessProcess(
            argv0 + driver.argv(session_id=sid, resume=resume), env=env, cwd=row.folder,
            name=f"hosted-{row.id}",
        )
        proc_holder["p"] = proc

        def on_exit(code: int | None) -> None:
            self._on_exit(live, proc, code)

        def on_message(obj: dict[str, Any]) -> None:
            # A frame the parser cannot read must never end the session.
            try:
                driver.on_message(obj)
            except Exception:  # noqa: BLE001
                log.warning("a %s frame could not be read", row.harness, exc_info=True)

        await asyncio.to_thread(proc.start, loop, on_message, on_exit)
        live.proc, live.driver = proc, driver
        try:
            await driver.open(session_id=sid, resume=resume)
        except Exception as exc:  # noqa: BLE001 — said in words below
            await self._stop_process(live)
            said = getattr(exc, "args", [""])[0] if exc.args else type(exc).__name__
            raise Refused(f"{W.label(row.harness)} did not start: {clip(said, 200)}") from None

    def _on_session_id(self, live: LiveSession, sid: str) -> None:
        async def save() -> None:
            try:
                row = await asyncio.to_thread(store.get, self.engine, live.sid)
                if row is not None and row.harness_session_id != sid:
                    await asyncio.to_thread(store.update, self.engine, live.sid, harness_session_id=sid)
            except Exception:  # noqa: BLE001 — the next start mints again, said nowhere
                log.warning("could not record a Code-view session's harness id", exc_info=True)

        asyncio.ensure_future(save())

    def _on_exit(self, live: LiveSession, proc: HarnessProcess, code: int | None) -> None:
        if live.proc is not proc:
            return
        drv = live.driver
        if drv is not None:
            drv.on_exit()
        live.proc, live.driver = None, None
        self._expire_asks(live)
        turn = live.turn
        if turn is not None and turn.running and not proc.stopped:
            technical = proc.stderr_text() or f"exit code {code}"
            ev = W.failure_for(live.harness, technical)
            ev["title"] = W.GONE_DURING_TURN.format(label=W.label(live.harness))
            self._emit(live, ev)
            self._emit(live, make("done", stop="failed"))

    async def _stop_process(self, live: LiveSession) -> None:
        proc = live.proc
        if proc is None:
            return
        live.proc = None
        drv, live.driver = live.driver, None
        await asyncio.to_thread(proc.stop)
        if drv is not None:
            drv.on_exit()

    async def _stop(self, sid: str, *, why: str) -> None:
        live = self.live.get(sid)
        if live is None:
            return
        turn = live.turn
        await self._stop_process(live)
        self._expire_asks(live)
        if turn is not None and turn.running:
            self._emit(live, make("notice", text=f"Stopped because the session was {why}."))
            self._emit(live, make("done", stop="interrupted"))

    # -- events ----------------------------------------------------------------
    def _emit(self, live: LiveSession, ev: dict[str, Any]) -> None:
        turn = live.turn
        if turn is not None and turn.running:
            ev["turn"] = turn.id
        if ev.get("type") == "error":
            live.last_error = str(ev.get("title") or "")
            if ev.get("fix_kind") == "account_limit":
                ev.setdefault("account", live.account)
                live.limit = getattr(live.driver, "limit", None)
                live.limit_since = datetime.now(timezone.utc).isoformat()
        live.log.append(ev)
        if turn is not None and turn.running:
            turn.add(ev)
            if ev.get("type") == "done":
                live.last_active = self.clock()
                turn.end("done" if ev.get("stop") == "completed" else str(ev.get("stop") or "done"))
                asyncio.ensure_future(self._after_turn(live))

    async def _after_turn(self, live: LiveSession) -> None:
        try:
            await asyncio.to_thread(store.update, self.engine, live.sid, last_error=live.last_error)
        except Exception:  # noqa: BLE001
            log.debug("could not stamp a Code-view session", exc_info=True)
        live.last_error = ""

    def _expire_asks(self, live: LiveSession) -> None:
        """Every open card closes when its process goes (nobody can answer it)."""
        for ask_id in list(live.asks):
            live.asks.pop(ask_id, None)
            self._emit(live, make("resolved", request_id=ask_id, decision="expired"))

    def _ask(self, live: LiveSession, ask: Ask) -> None:
        live.asks[ask.id] = ask
        self._emit(live, ask.event)

    # -- turns -------------------------------------------------------------------
    async def start_turn(self, sid: str, text: str, turn_id: str, files: list[str] | None = None) -> HostedTurn:
        self._evict_turns()
        turn_id = str(turn_id or "").strip()[:120] or f"turn-{uuid.uuid4().hex[:12]}"
        held = self.turns.get(turn_id)
        if held is not None:
            if held.sid != sid:
                raise Refused("That turn id belongs to another session.", 409)
            return held  # a repeated send of the same turn: follow it
        row = await self._row(sid)
        if row.archived_at is not None:
            raise Refused(W.ARCHIVED)
        text = str(text or "")
        files = [str(f) for f in (files or []) if str(f).strip()]
        if not text.strip() and not files:
            raise Refused("Type a message first.", 400)
        problems = await asyncio.to_thread(self._file_problems, files)
        if problems:
            raise Refused(problems, 400)
        live = self._live(row)
        live.account = row.account
        if live.turn is not None and live.turn.running:
            raise Refused(W.BUSY)
        turn = HostedTurn(turn_id, sid)
        self.turns[turn_id] = turn
        live.turn = turn
        live.last_active = self.clock()
        turn.task = asyncio.ensure_future(self._drive(live, row, turn, text, files))
        return turn

    def _file_problems(self, files: list[str]) -> str:
        """BLOCKING: an attached file must exist and pass the app's read policy."""
        from ..core.fs_policy import fs_read_ok

        for f in files:
            if not os.path.isabs(f) or not os.path.isfile(f):
                return f"Attached file not found: {clip(os.path.basename(f), 120)}"
            ok, why = fs_read_ok(f)
            if not ok:
                return why or f"Iron Jarvis may not read {clip(os.path.basename(f), 120)}."
        return ""

    async def _drive(self, live: LiveSession, row: Any, turn: HostedTurn, text: str, files: list[str]) -> None:
        user = make("user", text=clip(text, TEXT_CHARS))
        if files:
            user["files"] = [clip(f, 500) for f in files]
        self._emit(live, user)
        try:
            async with live.lock:
                await self._ensure_running(live, row)
            if live.driver is None:
                raise HarnessGone("the coding tool stopped")
            await live.driver.send_user(text, files)
            if turn.running:
                await turn.finished.wait()
        except Refused as r:
            self._emit(live, W.error(r.sentence, "Fix that, then send your message again.", ""))
            self._emit(live, make("done", stop="failed"))
        except HarnessGone as exc:
            if turn.running:
                self._emit(live, W.generic_error(live.harness, str(exc)))
                self._emit(live, make("done", stop="failed"))
        except asyncio.CancelledError:
            if turn.running:
                self._emit(live, W.error(
                    "Iron Jarvis stopped while this was working",
                    "Send the message again — the conversation is kept.", "",
                ))
                self._emit(live, make("done", stop="interrupted"))
            raise
        except Exception as exc:  # noqa: BLE001 — every failure becomes words
            log.warning("a Code-view turn failed", exc_info=True)
            if turn.running:
                self._emit(live, W.generic_error(live.harness, f"{type(exc).__name__}: {exc}"))
                self._emit(live, make("done", stop="failed"))
        finally:
            if turn.running:  # never leave a follower hanging
                turn.end("failed")
            if live.turn is turn:
                live.turn = None
            live.last_active = self.clock()

    def turn(self, turn_id: str) -> HostedTurn | None:
        self._evict_turns()
        return self.turns.get(turn_id)

    async def replay(self, sid: str, turn_id: str) -> str | None:
        """A turn this process no longer holds, from the session's log."""
        await self._row(sid)
        log_ = self.live[sid].log if sid in self.live else EventLog(self.home, sid)
        events, _total, _dropped = await asyncio.to_thread(log_.read, 0, turn=turn_id)
        if not events:
            return None
        return "".join(sse(e.get("type") or "notice", e) for e in events)

    async def answer(self, sid: str, request_id: str, decision: str, message: str = "",
                     answers: Any = None) -> dict[str, Any]:
        await self._row(sid)
        live = self.live.get(sid)
        ask = live.asks.get(str(request_id or "")) if live else None
        if live is None or ask is None or live.driver is None:
            raise Refused("No such question is waiting (it was answered, or the session stopped).", 404)
        decision = str(decision or "").strip()
        if decision not in DECISIONS:
            raise Refused(f"Answer with one of: {', '.join(DECISIONS)}.", 400)
        if ask.kind == "question" and decision == "allow_session":
            decision = "allow"
        if decision == "allow_session" and not ask.can_session:
            raise Refused(f"{W.label(live.harness)} offers no session-wide allow for this one — allow it once.", 400)
        try:
            await live.driver.answer(ask, decision, clip(message or "", 2000), answers)
        except AskRefused as exc:
            raise Refused(str(exc), 400) from None
        except HarnessGone:
            raise Refused("The session stopped before your answer reached it.", 409) from None
        live.asks.pop(ask.id, None)
        self._emit(live, make("resolved", request_id=ask.id, decision=decision))
        return {"ok": True, "request_id": ask.id, "decision": decision}

    async def interrupt(self, sid: str) -> dict[str, Any]:
        await self._row(sid)
        live = self.live.get(sid)
        if live is None or live.turn is None or not live.turn.running:
            return {"ok": True, "running": False}
        if live.driver is not None and live.running:
            for ask_id in list(live.asks):  # an unanswered card goes with the stop
                ask = live.asks.pop(ask_id)
                try:
                    await live.driver.answer(ask, "deny", "The user pressed Stop.", None)
                except Exception:  # noqa: BLE001
                    pass
                self._emit(live, make("resolved", request_id=ask_id, decision="deny"))
            try:
                await live.driver.interrupt()
            except Exception:  # noqa: BLE001 — a gone process ends the turn below
                pass
        else:
            self._emit(live, make("done", stop="interrupted"))
        return {"ok": True, "running": True}

    # -- the bell ----------------------------------------------------------------
    def waiting_rows(self) -> list[dict[str, Any]]:
        """One row per session WAITING on the user with nobody following its
        turn — NAMES ONLY (the tool's name), never arguments."""
        rows = []
        for live in list(self.live.values()):
            if not live.asks:
                continue
            if live.turn is not None and live.turn.watched:
                continue
            newest = list(live.asks.values())[-1]
            rows.append({
                "id": f"build_app:{live.sid}",
                "kind": "chat",
                "thread_id": live.sid,
                "tool": newest.tool,
                "requested_at": newest.asked_at,
                "mission_id": None,
                "project_id": None,
                "surface": "build_app",
                "app_session": live.sid,
            })
        return rows

    # -- the reaper --------------------------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.ensure_future(self._reap_loop())

    async def _reap_loop(self) -> None:
        while not self._closing:
            await asyncio.sleep(REAP_EVERY_S)
            await self.reap_once()

    async def reap_once(self, now: float | None = None) -> list[str]:
        """Stop every process idle longer than :data:`IDLE_S`."""
        now = self.clock() if now is None else now
        stopped = []
        for live in list(self.live.values()):
            if live.running and not live.busy and now - live.last_active >= IDLE_S:
                await self._stop_process(live)
                stopped.append(live.sid)
        return stopped

    async def shutdown(self) -> None:
        """The daemon is stopping: every harness process goes (tree kill, off
        the loop), every running turn ends with what it wrote kept."""
        self._closing = True
        if self._reaper is not None:
            self._reaper.cancel()
        lives = list(self.live.values())
        for live in lives:
            self._expire_asks(live)
            if live.turn is not None and live.turn.running:
                self._emit(live, W.error(
                    "Iron Jarvis stopped while this was working",
                    "Send the message again — the conversation is kept.", "",
                ))
                self._emit(live, make("done", stop="interrupted"))
        await asyncio.gather(*(self._stop_process(lv) for lv in lives), return_exceptions=True)
        await asyncio.gather(*(asyncio.to_thread(lv.log.close) for lv in lives), return_exceptions=True)

    # -- terminals -----------------------------------------------------------------
    async def open_terminal(self, sid: str) -> dict[str, Any]:
        row = await self._row(sid)
        live = self.live.get(sid)
        if live and live.turn and live.turn.running:
            raise Refused(W.RUNNING)
        hid = row.harness_session_id
        if not hid:
            raise Refused(W.NO_SESSION_YET)
        if not _SID_RX.match(hid):
            raise Refused("That session's id is not one Iron Jarvis types into a terminal.", 409)
        if live:
            await self._stop_process(live)  # two processes on one session corrupt it
        session = await asyncio.to_thread(self._launch_terminal, row, hid)
        return {"terminal_id": session.id}

    def _launch_terminal(self, row: Any, hid: str) -> Any:
        """BLOCKING: a Build terminal in the session's folder, on the
        session's account, typing the harness's own resume line."""
        from ..daemon.routes.terminals import launch_pane
        from ..daemon.schemas import TerminalLaunch
        from ..terminals import pane_accounts as PA
        from ..terminals.ai_clis import launch_entry

        cli = W.CLI[row.harness]
        entry = launch_entry(cli) or {"command": cli}
        acc = store.account_record(row)
        accounts = PA.from_recorded({W.PROXY[row.harness]: acc})[0] if acc else PA.PaneAccounts()
        if row.harness == CLAUDE:
            command = f"{entry['command']} --resume {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command, claude_session_id=hid)
        command = f"{entry['command']} resume {hid}"
        return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                           accounts=accounts, command=command)

    async def from_terminal(self, pane_id: str) -> dict[str, Any]:
        terminals = getattr(self.platform, "terminals", None)
        pane = terminals.get(pane_id) if terminals is not None else None
        if pane is None:
            raise Refused("No such terminal.", 404)
        facts = await asyncio.to_thread(self._pane_facts, pane)
        harness, hid, title = facts
        acc = (getattr(pane, "accounts", None) or {}).get(W.PROXY[harness]) or {}
        acc = {k: v for k, v in dict(acc).items() if k in ("id", "title", "source", "home", "unset", "note")}
        row = await asyncio.to_thread(
            store.create, self.engine, harness=harness, folder=str(pane.cwd or ""),
            title=title or "From a terminal", account=str(acc.get("id") or ""),
            account_json=json.dumps(acc) if acc else "", mode="ask", harness_session_id=hid,
        )
        return store.view(row)

    def _pane_facts(self, pane: Any) -> tuple[str, str, str]:
        """BLOCKING: ``(harness, session id, title)`` of a terminal whose
        Claude Code / Codex has EXITED, else :class:`Refused`."""
        from ..daemon.routes.terminals import _claude_ui_visible
        from ..terminals.handoff import at_shell_prompt

        cli = str(getattr(pane, "agent_cli", None) or getattr(pane, "resume_cli", None) or "").lower()
        if not cli:
            try:
                cli = str(pane.activity().cli or "").lower()
            except Exception:  # noqa: BLE001
                cli = ""
        harness = {"claude": CLAUDE, "codex": CODEX}.get(cli)
        if harness is None:
            raise Refused("This terminal is not running Claude Code or Codex.")
        tail = pane.output_tail()
        if not at_shell_prompt(tail, pane.shell) or _claude_ui_visible(tail):
            raise Refused(
                f"{W.label(harness)} is still running in this terminal — exit it there first "
                "(type /exit), then open it in the Code view."
            )
        hid = str(getattr(pane, "claude_session_id", None) or "") if harness == CLAUDE else ""
        title = ""
        if not hid or not _SID_RX.match(hid):
            newest = self._newest_disk(harness, str(pane.cwd or ""))
            if newest is None:
                raise Refused(
                    "This terminal has no saved conversation the Code view can open — start one in "
                    "the terminal first."
                )
            hid, title = str(newest.get("id") or ""), str(newest.get("title") or "")
        return harness, hid, clip(title, 120)

    def _newest_disk(self, harness: str, folder: str) -> dict[str, Any] | None:
        from .. import history

        try:
            for s in history.list_sessions(harness, limit=200):
                if same_folder(s.get("project"), folder):
                    return s
        except Exception:  # noqa: BLE001
            return None
        return None

    # -- continue on the next account (Claude Code) -------------------------------
    async def continue_on(self, sid: str, account: str = "") -> dict[str, Any]:
        row = await self._row(sid)
        if row.harness != CLAUDE:
            raise Refused(W.CODEX_CONTINUE)
        live = self.live.get(sid)
        if live and live.turn and live.turn.running:
            raise Refused(W.RUNNING)
        if not row.harness_session_id:
            raise Refused(W.NO_SESSION_YET)
        limit = live.limit if live else None
        since = live.limit_since if live else ""
        plan = await asyncio.to_thread(self._continue_blocking, row, str(account or "").strip(), limit, since)
        if live:
            await self._stop_process(live)
        lease, note = plan
        from ..terminals import pane_accounts as PA

        out = PA.PaneAccounts()
        out.use_lease(W.PROXY[CLAUDE], lease)
        rec = out.records[W.PROXY[CLAUDE]]
        row = await asyncio.to_thread(
            store.update, self.engine, sid, account=str(lease.profile_id), account_json=json.dumps(rec),
        )
        if live:
            live.limit, live.limit_since = None, ""
            self._emit(live, make("notice", text=clip(note, 500)))
        return {"session": self._with_live(store.view(row)), "account": lease.profile_id,
                "account_name": lease.title, "note": note}

    def _continue_blocking(self, row: Any, account: str, limit: Any, since: str) -> tuple[Any, str]:
        """BLOCKING: pick the next account, tell Iron-Proxy (only the limit
        message), COPY the conversation into the next account's home
        (``continue_on.carry_over`` — never moved, a diverged copy never
        overwritten). Refuses in words BEFORE anything is told or copied."""
        from ..iron_proxy import accounts as _acc
        from ..terminals import continue_on as CO
        from ..terminals import pane_accounts as PA

        rec = store.account_record(row)
        current = str(rec.get("id") or "")
        if rec.get("source") != "iron-proxy" or not current:
            raise Refused(W.THIS_PC)
        svc = getattr(self.platform, "iron_proxy", None)
        if svc is None or not getattr(svc, "enabled", False):
            raise Refused(PA._OFF)
        try:
            client = svc.lease_client(timeout_s=PA.LEASE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            raise Refused(PA._unavailable_sentence(exc)) from None
        if client is None:
            raise Refused(PA._OFF)
        try:
            profiles, states = client.profiles(), client.states()
        except Exception as exc:  # noqa: BLE001
            raise Refused(PA._unavailable_sentence(exc)) from None
        profiles = [p for p in (profiles if isinstance(profiles, list) else []) if isinstance(p, dict)]
        states = states if isinstance(states, dict) else {}
        if account:
            lease = None
            for prof in CO._others(profiles, current):
                if str(prof.get("id")) != account:
                    continue
                st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), dict) else {}
                cli = prof.get("cli") if isinstance(prof.get("cli"), dict) else {}
                home = str(cli.get("home") or "").strip()
                if home and CO._usable(st):
                    pid = str(prof.get("id"))
                    lease = _acc.make_lease(PA.SPECS[CO.PROVIDER], pid, str(prof.get("title") or pid),
                                            home, sorted(PA.PANE_OWN_KEYS[CO.PROVIDER]))
                else:
                    raise Refused(f'"{prof.get("title") or account}" cannot take over right now.')
            if lease is None:
                raise Refused("That account is not another Claude account in Iron-Proxy.", 404)
        else:
            lease = CO._own_choice(profiles, states, current)
            if lease is None:
                raise Refused(CO._none_free(profiles, states, current).sentence)
        if limit is not None:
            try:
                client.signal(current, text=limit.signal_text())
            except Exception as exc:  # noqa: BLE001 — the user pressed the button: carry on
                _acc._note("continue signal", exc)
        home = str(rec.get("home") or "")
        source = CO.newest_recorded(home, row.harness_session_id) if home else None
        if source is None and home:
            try:
                hits = list((Path(home) / "projects").glob(f"*/{row.harness_session_id}.jsonl"))
            except OSError:
                hits = []
            source = hits[0] if hits else None
        carried = CO.carry_over(source, lease.home, lease.title, "", self.home / "trash")
        if not carried.resumed:
            raise Refused(carried.note)
        return lease, f'Continued on "{lease.title}" — the conversation was copied there.'


def service_for(platform: Any) -> HostedService:
    svc = getattr(platform, "hosted", None)
    if svc is None:
        svc = HostedService(platform)
        platform.hosted = svc
    return svc


def waiting_rows(platform: Any) -> list[dict[str, Any]]:
    """The bell's rows (see :meth:`HostedService.waiting_rows`); [] when the
    Code view was never used. Never raises."""
    svc = getattr(platform, "hosted", None)
    if svc is None:
        return []
    try:
        return svc.waiting_rows()
    except Exception:  # noqa: BLE001 — a listing never fails over this
        log.warning("could not list waiting Code-view sessions", exc_info=True)
        return []
