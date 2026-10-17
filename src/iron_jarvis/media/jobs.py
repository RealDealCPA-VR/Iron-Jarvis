"""Background media jobs (v1.346.0, contract item 2) — a generation that
takes a while finishes without the chat waiting, comes HOME to the chat it
was asked in, and rings the bell.

``generate_media`` starts the generation itself (with the Idempotency-Key)
and, when the provider's eta is longer than the inline wait, hands the
STARTED job to :meth:`MediaJobs.start_background`. From there:

* one tracked asyncio task per job (a set + a done-callback, cancelled as a
  set at shutdown — the v1.291.0 phone-delivery shape) polls
  ``provider.poll(provider_job_id)`` with bounded backoff
  (:data:`BACKOFF_MIN_S` doubling to :data:`BACKOFF_MAX_S`; the sleep and the
  clock are injectable, so a test drives the states without waiting) until
  the job is done or ``config.media_job_max_s`` has passed ("took too long");
* on success the bytes are fetched (never with the key — the provider's
  ``fetch`` rule), landed through :func:`~iron_jarvis.media.deliver.deliver`
  (workspace file + Creative gallery copy, project-tagged), ONE line is
  appended to the chat through ``daemon.chat_append.append_to_chat_thread``
  (v1.345.0: the daemon writes the line, the page re-reads) — "Your <kind> is
  ready." then the markdown image syntax with the absolute path (the
  dashboard's MarkdownMedia renders a local .mp4/.mp3 as video/audio) —
  and :data:`MEDIA_COMPLETED` goes out for the bell;
* on failure the line says "Your <kind> could not be made: <reason>" and
  :data:`MEDIA_FAILED` goes out;
* a job finds its chat by ``thread_id``, else by the chat TURN that asked
  (``turn_id``, v1.347.0): a NEW chat's first question is sent before the
  page's first save names the chat, so the tool sees no thread id — the
  page links the turn a moment later and :meth:`MediaJobs._home_for` reads
  that link (the live turn, a link waiting for its turn, the turn's record).
  A job that finishes before the link lands keeps ``homed_at`` empty and
  :meth:`MediaJobs.home_turn` (called by ``POST /chat/turns/{id}/thread``)
  delivers it then; :meth:`MediaJobs.home_strays` does the same once at boot
  for rows an older build left with no chat (found through the turn record
  that names the job). One ``asyncio.Lock`` orders "which chat" against the
  link, and ``chat_append`` skips a second line for the same ``mediaJob``;
* a job with no chat at all (an agent run) appends nothing and only publishes;
* every job is a :class:`~iron_jarvis.media.models.MediaJobRecord` row, so a
  restart finds what was live: :meth:`MediaJobs.reconcile_at_boot` re-polls
  each queued/running row by its ``provider_job_id`` (Pixio keeps the
  record; the fake answers "lost at restart") and NEVER re-dispatches — a
  second ``start`` would be a second spend.

Secrets: a provider's words never carry its key; an unexpected exception's
text is masked (``detections.redact.mask``) before it reaches the row, the
chat or an event. Every blocking step (DB, download, file write, the append)
runs off the loop (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from ..core.models import ChatTurnRecord
from ..core.logging import get_logger
from ..detections.redact import mask
from .base import MediaError
from .deliver import deliver
from .models import (
    FAILED,
    LIVE_STATUSES,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    MediaJobRecord,
)

log = get_logger(__name__)

#: The bell's events (``NotificationBell`` maps both to the chat the job
#: was asked in). completed {job_id, kind, provider, thread_id, path,
#: cost_note}; failed {job_id, kind, provider, thread_id, error}.
MEDIA_COMPLETED = "media.completed"
MEDIA_FAILED = "media.failed"

#: Poll backoff: the first wait, doubling up to the cap.
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 15.0

#: The plain-words reasons this module itself writes.
TOO_LONG = "took too long"
LOST_AT_RESTART = "lost at restart"
NO_SERVICE = "the media service is not connected anymore"

#: The chat line's ``panelKind`` — a quiet media line, like ``progress``.
PANEL_KIND = "media"
#: Who the CHAT_THREAD_UPDATED event names as the writer.
WHO = "media"

#: The boot catch-up (:meth:`MediaJobs.home_strays`) looks this far back for
#: finished jobs that never reached a chat, and at most this many.
STRAY_LOOKBACK_DAYS = 14
STRAY_MAX = 50


@dataclass
class MediaJobSpec:
    """A STARTED generation handed to the background. ``provider_job_id`` is
    the service's id from ``provider.start`` — the only thing a poll needs.
    ``workspace`` is where the file lands (the tool's ``ctx.workspace``);
    ``cost_note`` the quote's words, kept on the row and the event."""

    provider: str
    kind: str
    model: str
    prompt: str
    provider_job_id: str
    params: dict[str, Any] = field(default_factory=dict)
    workspace: Path | None = None
    cost_note: str = ""
    eta_s: int | None = None


def _posix(path: str) -> str:
    """Forward slashes for the markdown image syntax (a backslash inside
    ``(...)`` is an escape to some markdown parsers; the dashboard keeps a
    ``C:/`` media src as a local file)."""
    return str(path).replace("\\", "/")


def media_markdown(kind: str, abs_path: str) -> str:
    """The markdown image syntax for a LOCAL file, destination in angle
    brackets: ``![video](<C:/Users/x/Iron Jarvis/media/fake/mj_1.mp4>)``.
    CommonMark refuses a bare destination holding a space and the dashboard's
    Markdown then shows the literal text — and the packaged home is
    ``%APPDATA%/Iron Jarvis/...``, the chat workfolder ``Documents/Iron Jarvis/
    <date> <title>`` (proven against the real component: with the brackets a
    <video> renders and the src decodes the space; a space-free path renders
    under both forms, so always bracketing is safe)."""
    return f"![{kind}](<{_posix(abs_path)}>)"


def ready_line(kind: str, abs_path: str, job_id: str) -> dict[str, Any]:
    """The ONE chat line a finished job appends: "Your <kind> is ready.", a
    blank line, the markdown image syntax with the absolute path in angle
    brackets (:func:`media_markdown`; video and audio use the same syntax —
    the dashboard decides by extension), the file in ``documents`` (the files
    rail), ``panelKind`` media, the job id."""
    return {
        "role": "assistant",
        "content": f"Your {kind} is ready.\n\n{media_markdown(kind, abs_path)}",
        "documents": [abs_path],
        "panelKind": PANEL_KIND,
        "mediaJob": job_id,
    }


def failed_line(kind: str, reason: str, job_id: str) -> dict[str, Any]:
    """The chat line a failed job appends — plain words, never a key."""
    return {
        "role": "assistant",
        "content": f"Your {kind} could not be made: {reason}",
        "panelKind": PANEL_KIND,
        "mediaJob": job_id,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _view(row: MediaJobRecord) -> dict[str, Any]:
    """A route-safe view of a row: the user's own paths, never a key (the
    params a generation was started with carry no credential — the provider
    adds the key at the transport)."""
    try:
        params = json.loads(row.params_json or "{}")
    except Exception:  # noqa: BLE001 — a corrupt blob reads as empty
        params = {}
    return {
        "id": row.id,
        "provider": row.provider,
        "kind": row.kind,
        "model": row.model,
        "prompt": row.prompt,
        "params": params if isinstance(params, dict) else {},
        "status": row.status,
        "thread_id": row.thread_id,
        "turn_id": row.turn_id,
        "homed_at": _iso(row.homed_at),
        "session_id": row.session_id,
        "project_id": row.project_id,
        "provider_job_id": row.provider_job_id,
        "output_path": row.output_path,
        "artifact": row.artifact,
        "error": row.error,
        "cost_note": row.cost_note,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
    }


class MediaJobs:
    """The background media jobs of one platform (``platform.media_jobs``).

    ``sleep`` / ``clock`` are the poll loop's time (tests inject a fake pair);
    ``append`` is the chat writer (``append_to_chat_thread`` by default);
    ``artifact_sink`` the gallery copy (``platform.artifacts.save`` by default).
    """

    def __init__(
        self,
        platform: Any,
        *,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        append: Callable[..., dict[str, Any]] | None = None,
        artifact_sink: Callable[..., Any] | None = None,
    ) -> None:
        self._platform = platform
        self._sleep = sleep
        self._clock = clock
        self._append = append
        self._artifact_sink = artifact_sink
        self._tasks: set[asyncio.Task] = set()
        #: Jobs this process is driving, by id — a job the tool just handed
        #: over is readable here before its row is written (one loop hop).
        self._live: dict[str, dict[str, Any]] = {}
        #: Orders "which chat does this job belong to" (a finish) against
        #: "this turn belongs to that chat" (the link route), so a result is
        #: never left with no chat because the two crossed (v1.347.0).
        self._home_lock = asyncio.Lock()

    # ------------------------------------------------------------------ doors
    def start_background(
        self,
        spec: MediaJobSpec,
        *,
        thread_id: str = "",
        session_id: str = "chat",
        project_id: str | None = None,
        turn_id: str = "",
    ) -> str:
        """Take a STARTED generation into the background; returns the job id
        at once (the row is written by the task, off the loop). Must be
        called on the daemon's loop (a tool's ``execute`` is). ``turn_id``
        is the chat turn that asked — how a job started in a NEW chat finds
        the chat once the page has saved it."""
        if not spec.provider_job_id:
            raise ValueError("a background media job needs the service's job id")
        row = MediaJobRecord(
            id=new_id("mj"),
            provider=spec.provider,
            kind=spec.kind,
            model=spec.model,
            prompt=spec.prompt,
            params_json=json.dumps(spec.params or {}, default=str),
            status=QUEUED,
            thread_id=str(thread_id or ""),
            turn_id=str(turn_id or ""),
            session_id=str(session_id or ""),
            project_id=str(project_id or ""),
            provider_job_id=spec.provider_job_id,
            cost_note=spec.cost_note or "",
        )
        self._live[row.id] = _view(row)
        self._track(self._run(row, spec, insert=True), row.id)
        return row.id

    async def reconcile_at_boot(self) -> int:
        """Re-poll every row a restart left queued/running — by its
        ``provider_job_id``, NEVER through ``provider.start`` (a second
        dispatch would be a second spend). Reads the rows off the loop and
        returns at once; the polling is each job's own tracked task, so boot
        never waits on the network. Returns how many were picked up."""
        rows = await asyncio.to_thread(self._live_rows)
        picked = 0
        for row in rows:
            if row.id in self._live:
                continue  # already driven by this process
            spec = MediaJobSpec(
                provider=row.provider,
                kind=row.kind,
                model=row.model,
                prompt=row.prompt,
                provider_job_id=row.provider_job_id,
                params=_view(row)["params"],
                workspace=None,
                cost_note=row.cost_note,
            )
            self._live[row.id] = _view(row)
            self._track(self._run(row, spec, insert=False), row.id)
            picked += 1
        if picked:
            log.info("media: re-polling %d generation(s) a restart left running", picked)
        try:
            homed = await self.home_strays()
            if homed:
                log.info("media: delivered %d finished generation(s) to their chat", homed)
        except Exception:  # noqa: BLE001 — the catch-up never blocks the reconcile
            log.warning("media: delivering finished generations to their chat failed", exc_info=True)
        return picked

    async def home_turn(self, turn_id: str, thread_id: str) -> int:
        """The page has just said which chat ``turn_id`` belongs to
        (``POST /chat/turns/{id}/thread``). Every job that turn started and
        that has no chat yet takes this one; a job that already FINISHED
        delivers its line now. Returns how many lines were delivered."""
        turn_id = str(turn_id or "").strip()
        thread_id = str(thread_id or "").strip()
        if not turn_id or not thread_id:
            return 0
        async with self._home_lock:
            finished = await asyncio.to_thread(self._adopt_turn, turn_id, thread_id)
        delivered = 0
        for row in finished:
            if await self._deliver_finished(row, thread_id):
                delivered += 1
        return delivered

    async def home_strays(self) -> int:
        """Boot catch-up: a finished job with no chat and no delivery (an
        older build lost a NEW chat's first-turn result this way) finds its
        chat through its turn — ``turn_id`` when it has one, else the turn
        whose record names the job ("... (job mj_x)" is in the tool's
        output). Bounded to :data:`STRAY_LOOKBACK_DAYS` and
        :data:`STRAY_MAX`; no bell (the bell already rang once)."""
        rows = await asyncio.to_thread(self._stray_rows)
        delivered = 0
        for row in rows:
            async with self._home_lock:
                thread_id = await asyncio.to_thread(self._stray_thread, row)
                if thread_id:
                    await asyncio.to_thread(self._update, row.id, thread_id=thread_id)
            if thread_id and await self._deliver_finished(row, thread_id):
                delivered += 1
        return delivered

    def cancel_all(self) -> int:
        """Cancel every live job task (lifespan shutdown). The rows stay
        queued/running so the next boot's reconcile re-polls them."""
        live = [t for t in self._tasks if not t.done()]
        for task in live:
            task.cancel()
        return len(live)

    async def drain(self) -> None:
        """Wait for every tracked task to land (tests + graceful stops)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def live_ids(self) -> list[str]:
        return list(self._live)

    # ------------------------------------------------------------------ reads
    def list_jobs(self, thread_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """Newest first; ``thread_id`` scopes to one chat. Jobs this process
        holds but has not written yet are merged in. SYNC (a route's
        threadpool or a ``to_thread``)."""
        limit = max(1, min(int(limit or 50), 500))
        with session_scope(self._platform.engine) as db:
            stmt = select(MediaJobRecord)
            if thread_id:
                stmt = stmt.where(MediaJobRecord.thread_id == thread_id)
            # Same-tick tie-break on insertion order (the v1.286.0 rule:
            # Windows' utcnow() ticks every 15.6 ms, so two jobs started
            # back to back share created_at and "newest" was a coin toss).
            stmt = stmt.order_by(
                MediaJobRecord.created_at.desc(),  # type: ignore[attr-defined]
                text("rowid DESC"),
            ).limit(limit)
            rows = [_view(r) for r in db.exec(stmt).all()]
        seen = {r["id"] for r in rows}
        # A SNAPSHOT: this runs on a route's threadpool thread while
        # ``_task_done`` (the loop thread's done-callback) pops the same dict;
        # iterating it live raises "dictionary changed size during iteration".
        for job_id, view in list(self._live.items()):
            if job_id in seen:
                continue
            if thread_id and view.get("thread_id") != thread_id:
                continue
            rows.insert(0, dict(view))
        return rows[:limit]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with session_scope(self._platform.engine) as db:
            row = db.get(MediaJobRecord, job_id)
            if row is not None:
                return _view(row)
        view = self._live.get(job_id)
        return dict(view) if view is not None else None

    # ------------------------------------------------------------------ guts
    def _track(self, coro: Any, job_id: str) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._task_done(t, job_id))
        return task

    def _task_done(self, task: asyncio.Task, job_id: str) -> None:
        self._tasks.discard(task)
        self._live.pop(job_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("media job %s crashed", job_id, exc_info=exc)

    def _registry(self) -> Any:
        return getattr(self._platform, "media", None)

    def _max_s(self) -> float:
        try:
            return max(1.0, float(getattr(self._platform.config, "media_job_max_s", 1800)))
        except (TypeError, ValueError):
            return 1800.0

    def _sink(self) -> Callable[..., Any] | None:
        if self._artifact_sink is not None:
            return self._artifact_sink
        artifacts = getattr(self._platform, "artifacts", None)
        if artifacts is None:
            return None

        def sink(name, blob, filename, kind, session_id=None, project_id=None):  # noqa: ANN001
            artifacts.save(
                name,
                blob,
                kind=kind,
                filename=filename,
                session_id=session_id,
                project_id=project_id,
            )

        return sink

    def _workspace(self, spec: MediaJobSpec) -> Path:
        if spec.workspace is not None:
            return Path(spec.workspace)
        return Path(self._platform.config.home) / "uploads"

    # DB -------------------------------------------------------------------
    def _live_rows(self) -> list[MediaJobRecord]:
        with session_scope(self._platform.engine) as db:
            stmt = (
                select(MediaJobRecord)
                .where(MediaJobRecord.status.in_(LIVE_STATUSES))  # type: ignore[attr-defined]
                # Oldest first, same-tick ties in insertion order, so the
                # reconcile picks jobs up in the order they were started.
                .order_by(
                    MediaJobRecord.created_at.asc(),  # type: ignore[attr-defined]
                    text("rowid"),
                )
            )
            rows = db.exec(stmt).all()
            for r in rows:
                db.expunge(r)
            return list(rows)

    def _insert(self, row: MediaJobRecord) -> None:
        with session_scope(self._platform.engine) as db:
            db.add(row)
            db.commit()

    def _update(self, job_id: str, **cols: Any) -> None:
        with session_scope(self._platform.engine) as db:
            row = db.get(MediaJobRecord, job_id)
            if row is None:
                return
            for key, value in cols.items():
                setattr(row, key, value)
            db.add(row)
            db.commit()
        view = self._live.get(job_id)
        if view is not None:
            for key, value in cols.items():
                view[key] = _iso(value) if isinstance(value, datetime) else value

    # which chat ---------------------------------------------------------------
    def _thread_of_turn(self, turn_id: str) -> str:
        """The chat a turn was linked to: the live turn, a link waiting for
        its turn to start, then the turn's record. "" when nobody said yet.
        SYNC (it reads the DB)."""
        if not turn_id:
            return ""
        logs = getattr(self._platform, "turn_logs", None)
        if logs is not None:
            try:
                tid = str(logs.thread_for(turn_id) or "")
            except Exception:  # noqa: BLE001 — an older/odd holder reads as unknown
                tid = ""
            if tid:
                return tid
        try:
            with session_scope(self._platform.engine) as db:
                rec = db.get(ChatTurnRecord, turn_id)
                return str(rec.thread_id or "") if rec is not None else ""
        except Exception:  # noqa: BLE001 — a read fault is "not known yet"
            log.warning("media: could not read chat turn %s", turn_id, exc_info=True)
            return ""

    def _home_for(self, job_id: str, fallback_thread: str, turn_id: str) -> str:
        """Which chat ``job_id``'s result goes to, decided NOW: the row's own
        ``thread_id`` (a link may have landed while it ran), else its turn's
        chat — written onto the row when found. SYNC; called under
        ``_home_lock`` from the loop via ``to_thread``."""
        current = fallback_thread
        try:
            with session_scope(self._platform.engine) as db:
                row = db.get(MediaJobRecord, job_id)
                if row is not None:
                    current = row.thread_id or current
                    turn_id = row.turn_id or turn_id
        except Exception:  # noqa: BLE001 — fall back to what the job started with
            log.warning("media: could not re-read job %s", job_id, exc_info=True)
        if current:
            return current
        found = self._thread_of_turn(turn_id)
        if found:
            self._update(job_id, thread_id=found)
        return found

    def _adopt_turn(self, turn_id: str, thread_id: str) -> list[MediaJobRecord]:
        """Give ``thread_id`` to every job of ``turn_id`` that has no chat;
        return the FINISHED, undelivered ones (detached) for delivery."""
        finished: list[MediaJobRecord] = []
        adopted: list[str] = []
        with session_scope(self._platform.engine) as db:
            rows = db.exec(
                select(MediaJobRecord).where(
                    MediaJobRecord.turn_id == turn_id,
                    MediaJobRecord.thread_id == "",
                )
            ).all()
            for row in rows:
                row.thread_id = thread_id
                db.add(row)
            db.commit()
            for row in rows:
                db.refresh(row)
                db.expunge(row)
                adopted.append(row.id)
                if row.status not in LIVE_STATUSES and row.homed_at is None:
                    finished.append(row)
        for job_id in adopted:
            view = self._live.get(job_id)
            if view is not None:
                view["thread_id"] = thread_id
        return finished

    def _stray_rows(self) -> list[MediaJobRecord]:
        """Finished chat jobs with no chat and no delivery, newest first."""
        since = (utcnow() - timedelta(days=STRAY_LOOKBACK_DAYS)).replace(tzinfo=None)
        with session_scope(self._platform.engine) as db:
            rows = db.exec(
                select(MediaJobRecord)
                .where(
                    MediaJobRecord.thread_id == "",
                    MediaJobRecord.homed_at == None,  # noqa: E711 — SQL IS NULL
                    MediaJobRecord.session_id == "chat",
                    MediaJobRecord.status.in_((SUCCEEDED, FAILED)),  # type: ignore[attr-defined]
                    MediaJobRecord.created_at >= since,
                )
                .order_by(MediaJobRecord.created_at.desc(), text("rowid DESC"))  # type: ignore[attr-defined]
                .limit(STRAY_MAX)
            ).all()
            for r in rows:
                db.expunge(r)
            return list(rows)

    def _stray_thread(self, row: MediaJobRecord) -> str:
        """A stray's chat: its turn's link, else the ONE linked turn whose
        record names the job (the tool's output says "(job <id>)"), started
        within two hours before the job and ten minutes after."""
        if row.turn_id:
            return self._thread_of_turn(row.turn_id)
        created = row.created_at
        if created is None:
            return ""
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        needle = f"(job {row.id})"
        with session_scope(self._platform.engine) as db:
            hits = db.exec(
                select(ChatTurnRecord.thread_id).where(
                    ChatTurnRecord.thread_id != "",
                    ChatTurnRecord.started_at >= created - timedelta(hours=2),
                    ChatTurnRecord.started_at <= created + timedelta(minutes=10),
                    text("instr(frames_json, :needle) > 0").bindparams(needle=needle),
                )
            ).all()
        threads = {str(t) for t in hits if t}
        return threads.pop() if len(threads) == 1 else ""

    # the job ----------------------------------------------------------------
    async def _run(self, row: MediaJobRecord, spec: MediaJobSpec, *, insert: bool) -> None:
        # Read every scalar BEFORE the insert: a committed row is expired and
        # re-reading it outside a session raises (DetachedInstanceError).
        job_id = row.id
        thread_id = row.thread_id
        turn_id = row.turn_id
        session_id = row.session_id or None
        project_id = row.project_id or None
        try:
            if insert:
                await asyncio.to_thread(self._insert, row)
            registry = self._registry()
            provider = registry.get(spec.provider) if registry is not None else None
            if provider is None:
                await self._finish_failed(job_id, spec, thread_id, turn_id, NO_SERVICE)
                return
            await asyncio.to_thread(self._update, job_id, status=RUNNING)
            started = self._clock()
            delay = BACKOFF_MIN_S
            while True:
                state = await provider.poll(spec.provider_job_id)
                if state.done:
                    break
                if self._clock() - started >= self._max_s():
                    await self._finish_failed(
                        job_id, spec, thread_id, turn_id, f"{TOO_LONG} (over {int(self._max_s())} s)"
                    )
                    return
                await self._sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
            if state.status != "succeeded":
                await self._finish_failed(
                    job_id, spec, thread_id, turn_id, state.error or "the service reported a failure"
                )
                return
            data = await provider.fetch(state)
            ext = state.ext or provider.ext_for(spec.kind)
            landed = await asyncio.to_thread(
                deliver,
                data,
                kind=spec.kind,
                provider=spec.provider,
                job_id=job_id,
                ext=ext,
                workspace=self._workspace(spec),
                session_id=session_id,
                project_id=project_id,
                artifact_sink=self._sink(),
            )
            cost_note = state.cost_note or spec.cost_note or ""
            await self._finish_succeeded(job_id, spec, thread_id, turn_id, landed, cost_note)
        except asyncio.CancelledError:
            # Shutdown: the row stays live for the next boot's reconcile.
            raise
        except MediaError as exc:
            await self._finish_failed(job_id, spec, thread_id, turn_id, exc.words)
        except Exception as exc:  # noqa: BLE001 — a job never crashes the daemon; words
            # Masked ONCE, in _finish_failed — the one door every failure
            # (provider words, the cap, the reconcile, this) passes through.
            await self._finish_failed(job_id, spec, thread_id, turn_id, f"{type(exc).__name__}: {exc}")

    async def _finish_succeeded(
        self,
        job_id: str,
        spec: MediaJobSpec,
        thread_id: str,
        turn_id: str,
        landed: dict[str, Any],
        cost_note: str,
    ) -> None:
        abs_path = str(landed.get("abs_path") or "")
        async with self._home_lock:
            # Which chat is decided and the row marked finished in ONE hold of
            # the lock the link route takes: a link either lands first (and
            # this finish sees the chat) or after (and finds a finished,
            # undelivered row it delivers itself).
            thread_id = await asyncio.to_thread(self._home_for, job_id, thread_id, turn_id)
            await asyncio.to_thread(
                self._update,
                job_id,
                status=SUCCEEDED,
                output_path=abs_path,
                artifact=str(landed.get("artifact") or ""),
                cost_note=cost_note,
                finished_at=utcnow(),
            )
        if thread_id and await self._append_line(thread_id, ready_line(spec.kind, abs_path, job_id)):
            await asyncio.to_thread(self._update, job_id, homed_at=utcnow())
        await self._publish(
            MEDIA_COMPLETED,
            {
                "job_id": job_id,
                "kind": spec.kind,
                "provider": spec.provider,
                "thread_id": thread_id,
                "path": abs_path,
                "cost_note": cost_note,
            },
        )

    async def _finish_failed(
        self, job_id: str, spec: MediaJobSpec, thread_id: str, turn_id: str, error: str
    ) -> None:
        error = mask(str(error or "")) or "unknown failure"
        async with self._home_lock:
            try:
                thread_id = await asyncio.to_thread(self._home_for, job_id, thread_id, turn_id)
            except Exception:  # noqa: BLE001 — the words still go out
                log.warning("media job %s: chat not resolved", job_id, exc_info=True)
            try:
                await asyncio.to_thread(
                    self._update, job_id, status=FAILED, error=error, finished_at=utcnow()
                )
            except Exception:  # noqa: BLE001 — the row is best-effort; the words still go out
                log.warning("media job %s: failed status not written", job_id, exc_info=True)
        if thread_id and await self._append_line(thread_id, failed_line(spec.kind, error, job_id)):
            try:
                await asyncio.to_thread(self._update, job_id, homed_at=utcnow())
            except Exception:  # noqa: BLE001
                log.warning("media job %s: delivery not recorded", job_id, exc_info=True)
        await self._publish(
            MEDIA_FAILED,
            {
                "job_id": job_id,
                "kind": spec.kind,
                "provider": spec.provider,
                "thread_id": thread_id,
                "error": error,
            },
        )

    async def _deliver_finished(self, row: MediaJobRecord, thread_id: str) -> bool:
        """The line for a job that finished before its chat was known
        (``home_turn`` / ``home_strays``); marks it delivered. No bell — the
        bell rang when the job finished."""
        if row.status == SUCCEEDED and row.output_path:
            line = ready_line(row.kind, row.output_path, row.id)
        elif row.status == FAILED:
            line = failed_line(row.kind, row.error or "unknown failure", row.id)
        else:
            return False
        if not await self._append_line(thread_id, line):
            return False
        await asyncio.to_thread(self._update, row.id, homed_at=utcnow())
        return True

    async def _append_line(self, thread_id: str, line: dict[str, Any]) -> bool:
        """True when the line is in the chat (newly, or already there)."""
        append = self._append
        if append is None:
            from ..daemon.chat_append import append_to_chat_thread as append  # lazy: keeps media/ free of daemon/ at import

        try:
            out = await asyncio.to_thread(
                append,
                self._platform.engine,
                thread_id,
                [line],
                event_bus=getattr(self._platform, "event_bus", None),
                who=WHO,
            )
            if not (out or {}).get("ok"):
                log.warning(
                    "media: the chat line did not land in %s: %s", thread_id, (out or {}).get("why")
                )
                return False
            return True
        except Exception:  # noqa: BLE001 — the bell still rings
            log.warning("media: chat append failed for %s", thread_id, exc_info=True)
            return False

    async def _publish(self, kind: str, payload: dict[str, Any]) -> None:
        bus = getattr(self._platform, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(kind, payload)
        except Exception:  # noqa: BLE001 — an event must never fail a job
            log.warning("media: %s publish failed", kind, exc_info=True)
