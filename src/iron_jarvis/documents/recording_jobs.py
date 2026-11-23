"""Turning one recording into a transcript and notes: the tracked job (33-a).

One job per recording handed to a chat (``transcribe_recording``). It:

1. hears the recording with the transcriber the user picked
   (``recording.choose_transcriber``): the offline speech model in its OWN
   process (``ironjarvis.exe audio-worker`` / ``python -m
   iron_jarvis.audio_worker``, op ``transcribe_timed``), started through
   ``core/procs`` in the BACKGROUND LANE (below-normal priority) and killed
   as a tree when it stalls (:data:`STALL_S` with no progress) or passes its
   deadline (its length times :data:`RTF_CAP`); or the speech server chosen
   for voice, sent the recording in one-minute pieces through the SAME
   ``routes/voice.transcribe_clip`` dictation uses (which refuses one off the
   network under Local only), each piece only while the service chosen for
   voice is still the one the job started with;
2. writes ``<name> transcript.md`` in the chat's folder (a new file, never
   over another) and keeps each line as a ``sourcemark`` of kind ``time``
   (``kept``: it stays when the chat is deleted);
3. writes the notes (D-33.1): a model on this PC or your network when there
   is one, with no question; otherwise the chat's model (the default), and
   only after the user's yes on the card, once per chat
   (:func:`consent_given`); never in a Local only chat. Each item is checked
   BY CODE against the record of what was heard (``recording.check_notes``)
   and ``<name> notes.md`` is written;
4. comes home: the tool waits up to :data:`INLINE_WAIT_S` and, when the job
   is done by then, its result is the reply's. Otherwise the reply's card
   shows how far it is (``GET /documents/recording/{id}``, read only while
   the card is on screen) and the job writes ONE report into the chat when
   it ends (``agents/chat_delivery.report_to_chat``, kind ``recording``: a
   second report of the same recording the same day replaces the first).

State is a JSON file per job under ``<home>/recordings/`` (kept
:data:`RECORDING_DAYS` days, swept when a job is saved). A job the daemon was
running when it stopped is started again at the next boot, once
(:data:`MAX_ATTEMPTS`); the transcriber is chosen again then, with the
chat's Local only as it is now.

BLOCKING work (the child, the files, the state) runs in worker threads; the
job itself is a task on the daemon's loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.logging import get_logger
from . import recording as R

log = get_logger("documents.recording")

#: How long ``transcribe_recording`` waits for the job before it hands the
#: reply the card (the job then comes home to the chat by itself).
INLINE_WAIT_S = 20.0
#: How long the worker may take to start (the frozen exe took 31-35 s on a
#: busy PC, 10-a) on top of its work.
WORKER_START_S = 45.0
#: The worker is stopped when its progress has not moved for this long.
STALL_S = 240.0
#: ...or when it has worked this many times the recording's length (plus
#: the start and :data:`DEADLINE_SLACK_S`).
RTF_CAP = 1.5
DEADLINE_SLACK_S = 60.0
#: How often a running job looks at the worker's progress.
POLL_S = 2.0
#: One notes call may take this long.
NOTES_CALL_TIMEOUT_S = 180.0
#: A job interrupted by a restart is started again this many times at most.
MAX_ATTEMPTS = 1
#: Job state is kept this many days.
RECORDING_DAYS = 30
#: A recording answered on the PHONE (no card there) is heard only up to
#: this long: the reply waits for it.
PHONE_MAX_S = 15 * 60

STATUS_HEARING = "hearing"
STATUS_NOTES = "notes"
STATUS_ASK = "ask"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_UNDONE = "undone"
RUNNING = (STATUS_HEARING, STATUS_NOTES)

#: The clock and the sleep a job waits with (tests inject both).
_clock: Callable[[], float] = time.monotonic
_sleep = asyncio.sleep

#: The daemon's deps (``_voice_backend``, ``_vosk_model_path``,
#: ``_VOICE_MAX_BYTES``, ``platform``), lent by ``create_app`` like the
#: phone poller's ``voice_deps``: a recording is heard with the same offline
#: model and the same chosen speech service as dictation.
_DEPS: Any = None


def lend_voice_deps(deps: Any) -> None:
    global _DEPS
    _DEPS = deps


def voice_deps() -> Any:
    return _DEPS


class NotesError(Exception):
    """The notes could not be written, in words."""


# --------------------------------------------------------------------------- #
# state on disk
# --------------------------------------------------------------------------- #
def jobs_dir(home: "str | Path") -> Path:
    return Path(home) / "recordings"


def _state_path(home: "str | Path", rid: str) -> Path:
    return jobs_dir(home) / f"{rid}.json"


def lines_path(home: "str | Path", rid: str) -> Path:
    """The record of what was heard (the notes are checked against THIS,
    never the transcript file the user may have edited)."""
    return jobs_dir(home) / f"{rid}.lines.json"


def progress_path(home: "str | Path", rid: str) -> Path:
    return jobs_dir(home) / f"{rid}.progress.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_state(home: "str | Path", state: "dict[str, Any]") -> None:
    """Write the job atomically; remove jobs older than :data:`RECORDING_DAYS`."""
    folder = jobs_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    target = _state_path(home, state["id"])
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    cutoff = time.time() - RECORDING_DAYS * 86400
    try:
        for i, old in enumerate(folder.glob("rec_*.json")):
            if i > 2000:
                break
            try:
                if not old.name.startswith(state["id"]) and old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                continue
    except OSError:
        pass


def load_state(home: "str | Path", rid: str) -> "dict[str, Any] | None":
    if not R.valid_recording_id(rid):
        return None
    try:
        state = json.loads(_state_path(home, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("id") == rid else None


def save_lines(home: "str | Path", rid: str, lines: "list[R.Line]") -> None:
    target = lines_path(home, rid)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps([{"start": ln.start, "text": ln.text} for ln in lines], ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, target)


def load_lines(home: "str | Path", rid: str) -> "list[R.Line]":
    try:
        raw = json.loads(lines_path(home, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return R.clean_lines(raw)


def read_progress(home: "str | Path", rid: str) -> "tuple[float, float] | None":
    try:
        raw = json.loads(progress_path(home, rid).read_text(encoding="ascii"))
        return float(raw.get("heard_s") or 0.0), float(raw.get("total_s") or 0.0)
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def file_sha256(path: "str | Path") -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_new(target: Path, text: str) -> Path:
    """Write ``text`` into ``target`` as a NEW file (never over another):
    when the name was taken meanwhile, the next free ``(2)`` name. Returns
    where it landed. BLOCKING."""
    stem, suffix = target.stem, target.suffix
    candidate = target
    for n in range(2, 1001):
        try:
            with open(candidate, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            return candidate
        except FileExistsError:
            candidate = target.with_name(f"{stem} ({n}){suffix}")
    raise OSError(f"no free name for {target.name} in {target.parent}")


# --------------------------------------------------------------------------- #
# the card (the tool's result, the reply's card, GET /documents/recording/{id})
# --------------------------------------------------------------------------- #
def _masked(text: str) -> str:
    try:
        from ..detections.redact import mask

        return mask(text) if text else text
    except Exception:  # noqa: BLE001
        return text


def card_of(state: "dict[str, Any]") -> "dict[str, Any]":
    """What the card shows, from the job's own record (never the model's
    words). Items are secret-masked."""
    status = str(state.get("status") or STATUS_FAILED)
    tr = state.get("transcriber") if isinstance(state.get("transcriber"), dict) else {}
    on_pc = tr.get("kind") == R.THIS_PC
    heard, total = float(state.get("heard_s") or 0.0), float(state.get("total_s") or 0.0)
    items_raw = state.get("items") if isinstance(state.get("items"), dict) else {}
    items = {
        k: [{"time": str(it.get("time") or ""), "seconds": float(it.get("seconds") or 0.0),
             "note": _masked(str(it.get("note") or ""))[:300]}
            for it in (items_raw.get(k) or [])[: R.MAX_ITEMS] if isinstance(it, dict)]
        for k in R.KINDS
    }
    counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
    offer = state.get("offer") if isinstance(state.get("offer"), dict) and status == STATUS_ASK else None
    return {
        "kind": "recording",
        "recording_id": str(state.get("id") or ""),
        "source_name": str(state.get("source_name") or ""),
        "source": str(state.get("source") or ""),
        "status": status,
        "heard_s": round(heard, 1),
        "total_s": round(total, 1),
        "progress_words": R.minutes_words(heard, total),
        "who": str(tr.get("who") or ""),
        "on_this_pc": bool(on_pc),
        "made_words": R.SMALL_MODEL_WORDS if on_pc else (
            f"Turned into text by {tr.get('who') or 'the speech service'}, chosen in Settings."),
        "transcriber_note": str(tr.get("note") or ""),
        "transcript": str(state.get("transcript") or "") if state.get("transcript_written") else "",
        "notes": str(state.get("notes") or "") if state.get("notes_written") else "",
        "words": int(state.get("words") or 0),
        "length_words": R.length_words(float(state.get("duration") or total or heard)),
        "cut_words": (f"Only the first {R.length_words(heard)} of "
                      f"{R.length_words(float(state.get('duration') or 0.0))} were turned into text."
                      if state.get("cut") else ""),
        "notes_by": str(state.get("notes_by") or ""),
        "counts": {k: int(counts.get(k) or 0) for k in (*R.KINDS, "dropped")},
        "counts_words": R.counts_words(counts) if state.get("notes_written") else "",
        "dropped_words": R.dropped_words(int(counts.get("dropped") or 0)) if state.get("notes_written") else "",
        "who_owes_words": R.WHO_OWES_WORDS,
        "items": items,
        "offer": ({"provider": str(offer.get("provider") or ""), "model": offer.get("model") or None,
                   "name": str(offer.get("name") or ""), "words": int(state.get("words") or 0)}
                  if offer else None),
        "notes_why": str(state.get("notes_why") or ""),
        "error": str(state.get("error") or ""),
    }


# --------------------------------------------------------------------------- #
# the notes model (D-33.1)
# --------------------------------------------------------------------------- #
def notes_choice(router: Any, config: Any, *, local_only: bool) -> "dict[str, Any] | None":
    """Who writes the notes. A model on this PC or your network first (the
    extracting role's model when it is one, else the default, else the
    first connected): ``local: True``, no question. None of those: under
    Local only, None (nobody); otherwise the chat's model (the default, or
    the first connected one when the default is Auto), ``local: False``,
    which is used only after the user's yes. The demo model never writes
    notes. BLOCKING (availability probes)."""
    from ..providers.egress import friendly_name, stays_on_this_network

    if router is None:
        return None
    manager = getattr(router, "manager", None)
    candidates: list[tuple[str, "str | None"]] = []
    try:
        from ..providers.roles import resolve_role

        llm = resolve_role(config, manager, "extract", fallback_provider=None, fallback_model=None)
        if getattr(llm, "applied", False) and llm.provider:
            candidates.append((str(llm.provider), llm.model or None))
    except Exception:  # noqa: BLE001 — no role: the default route
        pass
    try:
        default = str(router.default_provider or "")
    except Exception:  # noqa: BLE001
        default = ""
    try:
        order = [default, *router._candidate_order()]  # noqa: SLF001
    except Exception:  # noqa: BLE001
        order = [default]
    for p in order:
        if p and p not in ("mock", "auto") and p not in [c[0] for c in candidates]:
            candidates.append((p, None))

    def _up(provider: str) -> bool:
        try:
            return bool(router._safe_available(provider))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            return False

    for provider, model in candidates:
        if provider in ("mock", "auto") or not _up(provider):
            continue
        if stays_on_this_network(provider, manager, config, str(model or "")):
            return {"provider": provider, "model": model, "name": friendly_name(provider, manager), "local": True}
    if local_only:
        return None
    for provider, model in candidates:
        if provider in ("mock", "auto") or not _up(provider):
            continue
        return {"provider": provider, "model": model, "name": friendly_name(provider, manager), "local": False}
    return None


def _consent_path(home: "str | Path") -> Path:
    return jobs_dir(home) / "consent.json"


def consent_given(home: "str | Path", thread_id: str, provider: str) -> bool:
    """Did the user say yes, in THIS chat, to sending a recording's
    transcript to ``provider`` for its notes? (Once per chat and model: a
    yes for one model is not a yes for another.) BLOCKING."""
    if not thread_id or not provider:
        return False
    try:
        raw = json.loads(_consent_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    given = raw.get(thread_id) if isinstance(raw, dict) else None
    return isinstance(given, dict) and provider in given


_CONSENT_LOCK = threading.Lock()


def keep_consent(home: "str | Path", thread_id: str, provider: str) -> None:
    if not thread_id or not provider:
        return
    with _CONSENT_LOCK:
        _keep_consent(home, thread_id, provider)


def _keep_consent(home: "str | Path", thread_id: str, provider: str) -> None:
    target = _consent_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    raw.setdefault(thread_id, {})[provider] = _now_iso()
    # Bounded: the newest 500 chats.
    if len(raw) > 500:
        for key in list(raw)[: len(raw) - 500]:
            raw.pop(key, None)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    os.replace(tmp, target)


async def _ask_notes_model(router: Any, choice: "dict[str, Any]", prompt: str, *, local_only: bool) -> str:
    """ONE bounded notes call, PINNED to the model the choice named (the
    router raises instead of moving the transcript to another model)."""
    from ..providers.adapters.base import LLMMessage
    from ..providers.egress import friendly_name, local_only_scope, refusal_of

    name = str(choice.get("name") or "") or "the model"
    try:
        with local_only_scope(local_only):
            route = await asyncio.wait_for(
                router.complete(system=R.NOTES_SYSTEM, messages=[LLMMessage(role="user", content=prompt)],
                                tools=[], task_class="extract", provider=choice["provider"],
                                model=choice.get("model"), pin=True),
                timeout=NOTES_CALL_TIMEOUT_S,
            )
    except asyncio.TimeoutError as exc:
        raise NotesError(f"{name} did not answer within {int(NOTES_CALL_TIMEOUT_S)} seconds.") from exc
    except Exception as exc:  # noqa: BLE001 — a model that is down is said, by name
        refused = refusal_of(exc)
        if refused is not None:
            who = friendly_name(refused.provider, getattr(router, "manager", None))
            raise NotesError(f"This chat is Local only, and {who} is not on this PC or your network. "
                             "Nothing was sent.") from exc
        raise NotesError(f"{name} did not answer ({type(exc).__name__}: {str(exc)[:200]}).") from exc
    if getattr(route, "provider", "") == "mock":
        raise NotesError("Only the demo model answered, and made-up notes of a real recording are worse "
                         "than none.")
    return (getattr(getattr(route, "response", None), "text", "") or "").strip()


# --------------------------------------------------------------------------- #
# the job
# --------------------------------------------------------------------------- #
@dataclass
class Env:
    """What a job reaches: the database, the bus, the config, the router."""

    engine: Any
    event_bus: Any
    config: Any
    router_resolver: "Callable[[], Any] | None" = None

    @property
    def home(self) -> Path:
        return Path(self.config.home)

    def router(self) -> Any:
        try:
            return self.router_resolver() if callable(self.router_resolver) else None
        except Exception:  # noqa: BLE001
            return None


@dataclass
class Job:
    """One job this process is running."""

    state: dict[str, Any]
    env: Env
    transcriber: "R.Transcriber | None" = None
    task: "asyncio.Task | None" = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    #: The tool stopped waiting: the job's end is reported into the chat.
    detached: bool = False
    #: The worker process (for a stop); None between processes.
    proc: Any = None
    stopped: str = ""

    @property
    def id(self) -> str:
        return str(self.state.get("id") or "")


_LIVE: dict[str, Job] = {}


def live(rid: str) -> "Job | None":
    return _LIVE.get(rid)


def live_ids() -> list[str]:
    return [rid for rid, job in _LIVE.items() if job.task is not None and not job.task.done()]


async def _save(job: Job) -> None:
    await asyncio.to_thread(save_state, job.env.home, job.state)


def new_state(*, source: Path, sha256: str, workspace: Path, thread_id: str, turn_id: str,
              local_only: bool, transcriber: R.Transcriber, transcript: Path, notes: Path) -> "dict[str, Any]":
    return {
        "id": R.new_recording_id(),
        "version": 1,
        "created_at": _now_iso(),
        "source": str(source),
        "source_name": source.name,
        "sha256": sha256,
        "workspace": str(workspace),
        "thread_id": str(thread_id or ""),
        "turn_id": str(turn_id or ""),
        "local_only": local_only is True,
        "transcriber": transcriber.as_dict(),
        "status": STATUS_HEARING,
        "heard_s": 0.0,
        "total_s": 0.0,
        "duration": 0.0,
        "transcript": str(transcript),
        "notes": str(notes),
        "transcript_written": False,
        "notes_written": False,
        "hashes": {},
        "attempts": 1,
        "homed": False,
    }


def start(env: Env, state: "dict[str, Any]", transcriber: R.Transcriber) -> Job:
    """Start the job's task on the running loop and track it."""
    job = Job(state=state, env=env, transcriber=transcriber)
    _LIVE[job.id] = job
    job.task = asyncio.get_running_loop().create_task(_run(job), name=f"recording:{job.id}")
    return job


async def wait_inline(job: Job, timeout: float) -> bool:
    """Wait up to ``timeout`` for the job; True when it ended. On False the
    job is DETACHED: its end is reported into the chat. Decided with no
    await between the look and the mark, so a job that ends at that moment
    is either in the reply or in the chat, never neither."""
    try:
        await asyncio.wait_for(asyncio.shield(job.done.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    if job.done.is_set():
        return True
    job.detached = True
    return False


async def _run(job: Job) -> None:
    try:
        await _hear(job)
        if job.state.get("status") == STATUS_NOTES:
            await _notes(job)
    except asyncio.CancelledError:
        # The daemon is stopping: the state stays "hearing" or "notes", and
        # the next boot starts it again (MAX_ATTEMPTS).
        job.done.set()
        raise
    except Exception as exc:  # noqa: BLE001 — a job always ends in words
        log.warning("recording %s failed", job.id, exc_info=True)
        job.state["status"] = STATUS_FAILED
        job.state["error"] = job.state.get("error") or (
            f"{job.state.get('source_name')} could not be turned into text ({type(exc).__name__}: "
            f"{str(exc)[:200]}). Nothing was saved.")
        try:
            await _save(job)
        except Exception:  # noqa: BLE001
            pass
    job.done.set()
    if job.detached and job.state.get("status") != STATUS_UNDONE:
        await _report(job)


def _fail(job: Job, words: str) -> None:
    """The job ends in ``words``; a job its Undo stopped ends UNDONE (no
    report: the user took it back)."""
    job.state["status"] = STATUS_UNDONE if job.stopped == "undo" else STATUS_FAILED
    job.state["error"] = words


# -- hearing ------------------------------------------------------------------
def _worker_argv() -> list[str]:
    from ..comm.voice_note import _worker_argv as argv

    return argv()


def _run_worker(job: Job, request: dict, timeout: float) -> "dict | None":
    """One request to a fresh worker, in the background lane; ``None`` on
    any failure (a stall or a stop kills its tree). BLOCKING."""
    from ..core import procs

    def _spawned(proc: Any) -> bool:
        job.proc = proc
        return bool(job.stopped)

    try:
        with procs.background_lane(True):
            result = procs.run(_worker_argv(), timeout=timeout, input=json.dumps(request).encode("ascii"),
                               on_spawn=_spawned)
    except Exception:  # noqa: BLE001 — a missing program, a refused spawn
        log.warning("recording: the audio worker could not start")
        return None
    finally:
        job.proc = None
    if result.timed_out or result.returncode != 0:
        return None
    try:
        out = json.loads(result.stdout.decode("ascii", errors="replace") or "null")
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def _stop_worker(job: Job, why: str) -> None:
    job.stopped = job.stopped or why
    proc = job.proc
    if proc is not None:
        from ..core import procs

        procs.kill_tree(proc)


async def _watch(job: Job, fut: "asyncio.Future") -> None:
    """While the worker runs: its progress into the state, and a stop when
    it stalls or passes its deadline."""
    home = job.env.home
    started = _clock()
    last_heard, last_move = -1.0, started
    while not fut.done():
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout=POLL_S)
        except asyncio.TimeoutError:
            pass
        except Exception:  # noqa: BLE001 — the worker's own end is read by the caller
            return
        if fut.done():
            return
        now = _clock()
        got = await asyncio.to_thread(read_progress, home, job.id)
        if got is not None:
            heard, total = got
            if total and not job.state.get("total_s"):
                job.state["total_s"] = total
            if heard > last_heard:
                last_heard, last_move = heard, now
                job.state["heard_s"] = heard
        total = float(job.state.get("total_s") or 0.0)
        limit = WORKER_START_S + (total or R.MAX_AUDIO_S) * RTF_CAP + DEADLINE_SLACK_S
        if now - started > limit:
            _stop_worker(job, "deadline")
        elif now - last_move > STALL_S + (WORKER_START_S if last_heard < 0 else 0.0):
            _stop_worker(job, "stall")


def _stopped_words(job: Job) -> str:
    name = job.state.get("source_name")
    at = R.minutes_words(float(job.state.get("heard_s") or 0.0), float(job.state.get("total_s") or 0.0))
    if job.stopped == "undo":
        return f"Stopped: the transcript of {name} was undone."
    if job.stopped == "deadline":
        return (f"The offline speech model took too long on {name} and was stopped after {at}. "
                "Nothing was saved.")
    return (f"The offline speech model stopped making progress on {name} after {at}, and was stopped. "
            "Nothing was saved.")


async def _hear(job: Job) -> None:
    tr = job.transcriber
    if tr is None:
        _fail(job, "No transcriber was chosen. Nothing was sent anywhere.")
        return
    if tr.kind == R.THIS_PC:
        got = await _hear_here(job, tr)
    else:
        got = await _hear_by_service(job, tr)
    if got is None:
        await _save(job)
        return
    lines, heard, duration, cut = got
    if job.stopped:
        _fail(job, _stopped_words(job))
        await _save(job)
        return
    job.state.update({"heard_s": heard, "duration": duration, "total_s": duration if duration else heard,
                      "cut": bool(cut), "words": R.word_count(lines), "lines": len(lines)})
    await asyncio.to_thread(_write_transcript, job, lines)
    job.state["status"] = STATUS_NOTES
    await _save(job)


async def _hear_here(job: Job, tr: R.Transcriber) -> "tuple[list[R.Line], float, float, bool] | None":
    home = job.env.home
    await asyncio.to_thread(lambda: jobs_dir(home).mkdir(parents=True, exist_ok=True))
    request = {
        "op": "transcribe_timed", "path": job.state["source"], "model": tr.model,
        "max_audio_s": R.MAX_AUDIO_S, "piece_s": R.PIECE_S, "progress": str(progress_path(home, job.id)),
    }
    hard = WORKER_START_S + R.MAX_AUDIO_S * RTF_CAP + 60.0
    fut = asyncio.ensure_future(asyncio.to_thread(_run_worker, job, request, hard))
    await _watch(job, fut)
    out = await fut
    try:
        await asyncio.to_thread(lambda: progress_path(home, job.id).unlink(missing_ok=True))
    except OSError:
        pass
    if job.stopped:
        _fail(job, _stopped_words(job))
        return None
    if out is None:
        _fail(job, f"The offline speech model could not read {job.state.get('source_name')}: its process "
                   "ended without an answer. Nothing was saved.")
        return None
    if out.get("error"):
        _fail(job, _worker_error_words(job, str(out["error"])))
        return None
    lines = R.clean_lines(out.get("lines"))
    return lines, float(out.get("seconds") or 0.0), float(out.get("duration") or 0.0), bool(out.get("cut"))


def _worker_error_words(job: Job, error: str) -> str:
    name = job.state.get("source_name")
    suffix = Path(str(name or "")).suffix.lower() or "this kind of"
    if "PyAV" in error or "decoder" in error:
        return (f"{name} could not be read: the audio decoder is missing from this install. In a source "
                "install, run: uv sync --extra voice. Nothing was sent anywhere.")
    if "no sound" in error:
        return f"{name} has no sound in it, so there was nothing to turn into text."
    return (f"{name} could not be read as a recording ({error[:160]}). If it is a rare {suffix} file, "
            "installing ffmpeg and saving it again as .m4a or .mp3 may help. Nothing was sent anywhere.")


def _service_key(backend: Any) -> str:
    try:
        return f"{backend[0]}|{backend[1]}" if backend else ""
    except Exception:  # noqa: BLE001
        return ""


async def _hear_by_service(job: Job, tr: R.Transcriber) -> "tuple[list[R.Line], float, float, bool] | None":
    """The speech service chosen for voice, a piece at a time, through the
    real ``transcribe_clip`` (Local only refuses one off the network there
    too). Each piece goes only while that service is still the one chosen
    for voice: a change in Settings mid-way sends nothing more."""
    from fastapi import HTTPException

    deps = voice_deps()
    if deps is None:
        _fail(job, f"{tr.who[:1].upper() + tr.who[1:]} cannot be reached from here right now. "
                   "Nothing was sent.")
        return None
    from ..daemon.routes import voice as voice_routes
    from ..providers.egress import thread_local_only

    home = job.env.home
    folder = await asyncio.to_thread(lambda: Path(tempfile.mkdtemp(prefix=f"{job.id}-", dir=str(_tmp_root(home)))))
    try:
        hard = WORKER_START_S + R.MAX_AUDIO_S * 0.25 + 60.0
        out = await asyncio.to_thread(_run_worker, job, {
            "op": "pieces", "path": job.state["source"], "out_dir": str(folder),
            "max_audio_s": R.MAX_AUDIO_S, "piece_s": R.PIECE_S,
        }, hard)
        if job.stopped:
            _fail(job, _stopped_words(job))
            return None
        if out is None or out.get("error"):
            _fail(job, _worker_error_words(job, str((out or {}).get("error") or "the decoder did not answer")))
            return None
        pieces = [p for p in out.get("pieces") or [] if isinstance(p, dict)]
        duration = float(out.get("duration") or 0.0)
        # What will be sent (a recording past the bound is cut there).
        job.state["total_s"] = float(out.get("seconds") or 0.0)
        await _save(job)
        want = f"{tr.label}|{tr.url}"
        lines: list[dict[str, Any]] = []
        for piece in pieces:
            if job.stopped:
                _fail(job, _stopped_words(job))
                return None
            backend = await asyncio.to_thread(deps._voice_backend)
            if _service_key(backend) != want:
                _fail(job, (f"The speech service chosen for voice changed while {job.state.get('source_name')} "
                            f"was being sent to {tr.who}, so nothing more was sent. Nothing was saved."))
                return None
            local_only = bool(job.state.get("local_only")) or await asyncio.to_thread(
                thread_local_only, job.env.engine, job.state.get("thread_id") or "")
            data = await asyncio.to_thread(Path(str(piece.get("path"))).read_bytes)
            try:
                got = await voice_routes.transcribe_clip(deps, data=data, mime="audio/wav", local_only=local_only)
            except voice_routes.SpeechKeptHere as exc:
                _fail(job, f"{exc} Nothing more was sent. Nothing was saved.")
                return None
            except HTTPException as exc:
                _fail(job, (f"{tr.who[:1].upper() + tr.who[1:]} could not turn the piece at "
                            f"{R.stamp(float(piece.get('start') or 0))} into text: {exc.detail} Nothing else "
                            "was tried. Nothing was saved."))
                return None
            except Exception as exc:  # noqa: BLE001 — never a crash over a recording
                _fail(job, (f"{tr.who[:1].upper() + tr.who[1:]} did not answer ({type(exc).__name__}). "
                            "Nothing else was tried. Nothing was saved."))
                return None
            text = " ".join(str((got or {}).get("text") or "").split())
            if text:
                lines.append({"start": float(piece.get("start") or 0.0), "text": text})
            job.state["heard_s"] = float(piece.get("start") or 0.0) + float(piece.get("seconds") or 0.0)
            await asyncio.to_thread(lambda p=piece: Path(str(p.get("path"))).unlink(missing_ok=True))
        return (R.clean_lines(lines), float(out.get("seconds") or 0.0), duration, bool(out.get("cut")))
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)


def _tmp_root(home: Path) -> Path:
    root = jobs_dir(home) / "pieces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_transcript(job: Job, lines: "list[R.Line]") -> None:
    """``<name> transcript.md`` (a new file), the record of what was heard,
    and each line kept as a ``time`` mark. BLOCKING."""
    st = job.state
    tr = st.get("transcriber") or {}
    text = R.transcript_markdown(
        st["source_name"], lines, who=str(tr.get("who") or ""), on_this_pc=tr.get("kind") == R.THIS_PC,
        duration=float(st.get("duration") or st.get("heard_s") or 0.0), heard=float(st.get("heard_s") or 0.0),
        cut=bool(st.get("cut")),
    )
    save_lines(job.env.home, job.id, lines)
    target = write_new(Path(st["transcript"]), text)
    st["transcript"] = str(target)
    st["transcript_written"] = True
    st.setdefault("hashes", {})[str(target)] = file_sha256(target)
    try:
        keep_minutes(job.env.engine, target, lines, thread_id=str(st.get("thread_id") or ""),
                     turn_id=str(st.get("turn_id") or ""), long=float(st.get("duration") or 0) >= 3600)
    except Exception:  # noqa: BLE001 — a kept minute never fails the transcript
        log.warning("recording %s: the minutes could not be kept", job.id, exc_info=True)


#: The most minutes one transcript keeps as marks.
MAX_MARKS = 3000


def keep_minutes(engine: Any, path: Path, lines: "list[R.Line]", *, thread_id: str, turn_id: str,
                 long: bool) -> int:
    """Each transcript line as a kept ``sourcemark`` of kind ``time``: the
    time it was said, its words (200 characters, secret-masked). BLOCKING."""
    from . import source_store
    from .sources import Mark

    marks = [
        Mark(locator={"time": ln.start, "line": i + 1}, shown=f"[{R.stamp(ln.start, long=long)}]",
             key=f"t{int(ln.start)}", context=_masked(ln.text)[:200])
        for i, ln in enumerate(lines[:MAX_MARKS])
    ]
    if not marks or engine is None:
        return 0
    rows = source_store.write_marks(engine, marks, path=str(path), kind="time", thread_id=thread_id,
                                    turn_id=turn_id, file_sha256=file_sha256(path), kept=True)
    return len(rows)


# -- the notes ----------------------------------------------------------------
async def _notes(job: Job) -> None:
    """Who writes the notes, and the notes (D-33.1)."""
    env = job.env
    st = job.state
    router = env.router()
    local_only = await _local_only_now(job)
    choice = await asyncio.to_thread(notes_choice, router, env.config, local_only=local_only)
    if choice is None:
        st["status"] = STATUS_DONE
        st["notes_why"] = (
            "No model on this PC or your network can write the notes, and this chat is Local only, so the "
            "transcript was not sent anywhere for them." if local_only else
            "No model is set up to write the notes. The transcript is saved.")
        await _save(job)
        return
    if not choice.get("local"):
        if not await asyncio.to_thread(consent_given, env.home, str(st.get("thread_id") or ""),
                                       str(choice["provider"])):
            if _answers_the_phone():
                st["status"] = STATUS_DONE
                st["notes_why"] = (f"Writing the notes would send the transcript to {choice['name']}, which is "
                                   "asked on the recording's card in Iron Jarvis on this PC.")
            else:
                st["status"] = STATUS_ASK
                st["offer"] = {"provider": choice["provider"], "model": choice.get("model"),
                               "name": choice["name"]}
            await _save(job)
            return
    await write_notes(job, choice, local_only=local_only)


def _answers_the_phone() -> bool:
    try:
        from ..providers.failure_words import answers_the_phone

        return answers_the_phone() is True
    except Exception:  # noqa: BLE001
        return True


async def _local_only_now(job: Job) -> bool:
    """The turn's Local only verdict (kept with the job: a new chat's first
    turn has no chat yet) OR the saved chat's, as it is now."""
    if job.state.get("local_only"):
        return True
    tid = str(job.state.get("thread_id") or "")
    if not tid:
        return False
    try:
        from ..providers.egress import thread_local_only

        return bool(await asyncio.to_thread(thread_local_only, job.env.engine, tid))
    except Exception:  # noqa: BLE001 — unsure: Local only
        return True


async def write_notes(job: Job, choice: "dict[str, Any]", *, local_only: bool) -> None:
    """The model in ``choice`` reads the transcript (in parts), code checks
    every item, and ``<name> notes.md`` is written."""
    env = job.env
    st = job.state
    st["status"] = STATUS_NOTES
    st["offer"] = None
    st["notes_why"] = ""
    await _save(job)
    lines = await asyncio.to_thread(load_lines, env.home, job.id)
    duration = float(st.get("duration") or st.get("heard_s") or 0.0)
    if not lines:
        st["status"] = STATUS_DONE
        st["notes_why"] = "No words were heard in the recording, so there are no notes."
        await _save(job)
        return
    router = env.router()
    parts = R.notes_parts(lines)
    raw: dict[str, list[dict[str, str]]] = {k: [] for k in R.KINDS}
    try:
        for n, part in enumerate(parts, start=1):
            prompt = R.notes_prompt(st["source_name"], part, long=duration >= 3600, part_no=n, parts=len(parts))
            answer = await _ask_notes_model(router, choice, prompt, local_only=local_only)
            got = R.parse_notes(answer)
            for k in R.KINDS:
                raw[k].extend(got.get(k) or [])
    except NotesError as exc:
        st["status"] = STATUS_DONE
        st["notes_why"] = f"The notes were not written: {exc} The transcript is saved."
        await _save(job)
        return
    if job.stopped:
        return
    checked = await asyncio.to_thread(R.check_notes, raw, lines, duration)
    tr = st.get("transcriber") or {}
    made_by = (str(tr.get("who") or "the offline speech model on this PC"))
    text = R.notes_markdown(st["source_name"], checked, writer=str(choice.get("name") or "a model"),
                            made_by=made_by, duration=duration)

    def _write() -> None:
        target = write_new(Path(st["notes"]), text)
        st["notes"] = str(target)
        st["notes_written"] = True
        st.setdefault("hashes", {})[str(target)] = file_sha256(target)

    await asyncio.to_thread(_write)
    st["notes_by"] = str(choice.get("name") or "")
    st["counts"] = checked.counts()
    st["items"] = {k: [{"time": it["time"], "seconds": it["seconds"], "note": it["note"]}
                       for it in checked.items[k]] for k in R.KINDS}
    st["status"] = STATUS_DONE
    await _save(job)


async def notes_after_yes(env: Env, rid: str, *, provider: str, model: Any, thread_id: str,
                          local_only: bool) -> "dict[str, Any]":
    """The card's "Write the notes" press: ``{"ok", "changed", "card",
    "why"}``. Sends the transcript ONLY to the model the card named, only
    while it is still the model the notes would be written with, never under
    Local only to a model off the network. The yes is kept for the chat."""
    state = await asyncio.to_thread(load_state, env.home, rid)
    if state is None:
        return {"ok": False, "why": "That recording is no longer kept."}
    job = _LIVE.get(rid)
    if job is not None and job.task is not None and not job.task.done():
        return {"ok": False, "why": "The recording is still being worked on.", "card": card_of(job.state)}
    offer = state.get("offer") if isinstance(state.get("offer"), dict) else None
    if state.get("status") != STATUS_ASK or offer is None:
        return {"ok": False, "why": "The notes do not wait on a yes any more.", "card": card_of(state)}
    # The job's own chat first: the yes is kept for the chat the recording
    # was handed to (the press's id only names a new chat linked since).
    tid = str(state.get("thread_id") or "") or str(thread_id or "")
    lo = bool(local_only) or bool(state.get("local_only"))
    if not lo and tid:
        try:
            from ..providers.egress import thread_local_only

            lo = bool(await asyncio.to_thread(thread_local_only, env.engine, tid))
        except Exception:  # noqa: BLE001
            lo = True
    choice = await asyncio.to_thread(notes_choice, env.router(), env.config, local_only=lo)
    if (choice is None or choice.get("provider") != provider or provider != offer.get("provider")
            or (choice.get("model") or None) != (offer.get("model") or None)
            or (model or None) != (offer.get("model") or None)):
        # The press named a model that is no longer the one the notes go to
        # (another was connected, Local only came on): nothing is sent, and
        # the card asks again in its new words.
        if choice is None:
            state["status"] = STATUS_DONE
            state["offer"] = None
            state["notes_why"] = ("This chat is Local only now, so the transcript was not sent anywhere for "
                                  "the notes." if lo else "No model is set up to write the notes.")
        elif choice.get("local"):
            state["offer"] = None
        else:
            state["offer"] = {"provider": choice["provider"], "model": choice.get("model"), "name": choice["name"]}
        await asyncio.to_thread(save_state, env.home, state)
        if choice is not None and choice.get("local"):
            job = Job(state=state, env=env)
            _LIVE[rid] = job
            job.detached = True
            job.task = asyncio.get_running_loop().create_task(_notes_task(job, choice, lo), name=f"notes:{rid}")
            return {"ok": True, "changed": True, "card": card_of(state)}
        return {"ok": False, "changed": True, "card": card_of(state)}
    if tid:
        await asyncio.to_thread(keep_consent, env.home, tid, provider)
    if tid and not state.get("thread_id"):
        state["thread_id"] = tid
    job = Job(state=state, env=env)
    job.detached = True
    _LIVE[rid] = job
    state["status"] = STATUS_NOTES
    state["offer"] = None
    await asyncio.to_thread(save_state, env.home, state)
    job.task = asyncio.get_running_loop().create_task(_notes_task(job, choice, lo), name=f"notes:{rid}")
    return {"ok": True, "card": card_of(state)}


async def _notes_task(job: Job, choice: "dict[str, Any]", local_only: bool) -> None:
    try:
        await write_notes(job, choice, local_only=local_only)
    except asyncio.CancelledError:
        job.done.set()
        raise
    except Exception as exc:  # noqa: BLE001
        job.state["status"] = STATUS_DONE
        job.state["notes_why"] = f"The notes were not written ({type(exc).__name__}). The transcript is saved."
        try:
            await _save(job)
        except Exception:  # noqa: BLE001
            pass
    job.done.set()
    await _report(job)


# -- coming home ---------------------------------------------------------------
def report_words(state: "dict[str, Any]") -> str:
    """The report's body, from the job's record (bounded well under the
    report's own cut)."""
    name = state.get("source_name")
    status = state.get("status")
    if status == STATUS_FAILED:
        return str(state.get("error") or f"{name} could not be turned into text.")
    words = int(state.get("words") or 0)
    lines = [f"The transcript is ready ({words} words): {state.get('transcript')}"]
    if state.get("cut"):
        card = card_of(state)
        lines.append(card["cut_words"])
    if state.get("notes_written"):
        counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
        lines.append(f"The notes ({R.counts_words(counts)}), written by {state.get('notes_by')} and checked "
                     f"against the transcript: {state.get('notes')}")
        gone = R.dropped_words(int(counts.get("dropped") or 0))
        if gone:
            lines.append(gone)
        items = state.get("items") if isinstance(state.get("items"), dict) else {}
        shown = 0
        for kind in R.KINDS:
            for it in items.get(kind) or []:
                if shown >= 8:
                    break
                lines.append(f"- {R.KIND_WORDS[kind]}: [{it.get('time')}] {_masked(str(it.get('note') or ''))[:160]}")
                shown += 1
    elif status == STATUS_ASK:
        offer = state.get("offer") or {}
        lines.append(f"The notes are not written yet: writing them sends the transcript to {offer.get('name')}, "
                     "and the card above asks you first.")
    elif state.get("notes_why"):
        lines.append(str(state.get("notes_why")))
    return "\n".join(lines)


async def _report(job: Job) -> None:
    """ONE report into the chat (idea 39's one writer); a chat not known yet
    (a new chat's first turn) is reported to when the page links it
    (:func:`home_turn`)."""
    st = job.state
    tid = str(st.get("thread_id") or "")
    if not tid or _answers_the_phone():
        st["homed"] = False
        await _save(job)
        return
    from ..agents.chat_delivery import report_to_chat

    stage = "notes" if st.get("notes_written") else str(st.get("status") or "")
    docs = [p for p in (st.get("transcript") if st.get("transcript_written") else "",
                        st.get("notes") if st.get("notes_written") else "") if p]
    report = {"kind": "recording", "name": st.get("source_name"), "id": job.id,
              "words": report_words(st), "documents": docs, "key": f"recording:{job.id}:{stage}"}
    platform = _PlatformView(job.env.engine, job.env.event_bus)
    out = await asyncio.to_thread(report_to_chat, platform, tid, report)
    st["homed"] = bool(out.get("ok"))
    await _save(job)


@dataclass
class _PlatformView:
    engine: Any
    event_bus: Any


async def home_turn(env: Env, turn_id: str, thread_id: str) -> int:
    """A new chat's first turn was saved as ``thread_id``: its jobs belong
    to that chat now, and one that already ended (detached) reports there."""
    if not turn_id or not thread_id:
        return 0
    homed = 0
    for job in list(_LIVE.values()):
        st = job.state
        if st.get("turn_id") != turn_id or st.get("thread_id"):
            continue
        st["thread_id"] = thread_id
        homed += 1
        if job.done.is_set() and job.detached and not st.get("homed") and st.get("status") != STATUS_UNDONE:
            await _report(job)
        else:
            await _save(job)
    return homed


async def stop(rid: str, why: str = "undo") -> bool:
    """Stop a running job (its Undo): the worker's tree is killed and nothing
    more is sent or written."""
    job = _LIVE.get(rid)
    if job is None or job.task is None or job.task.done():
        return False
    _stop_worker(job, why)
    job.state["status"] = STATUS_UNDONE if why == "undo" else job.state.get("status")
    try:
        await asyncio.wait_for(asyncio.shield(job.done.wait()), timeout=10.0)
    except asyncio.TimeoutError:
        job.task.cancel()
    return True


async def reconcile_at_boot(env: Env) -> int:
    """A job the last process was running is started again, once more at
    most (:data:`MAX_ATTEMPTS`), with the transcriber chosen again under the
    chat's Local only as it is now; past that, or when it cannot be chosen,
    it ends in words in its chat. Returns how many were started."""
    states = await asyncio.to_thread(_interrupted, env.home)
    # Nothing is being heard at boot: one-minute pieces a stopped process
    # left for a speech server are removed (they are a copy of the sound).
    await asyncio.to_thread(shutil.rmtree, jobs_dir(env.home) / "pieces", True)
    started = 0
    for st in states:
        attempts = int(st.get("attempts") or 1) + 1
        st["attempts"] = attempts
        job = Job(state=st, env=env)
        job.detached = True
        if attempts > MAX_ATTEMPTS + 1 or not Path(str(st.get("source") or "")).is_file():
            _fail(job, (f"Turning {st.get('source_name')} into text stopped when Iron Jarvis restarted, and "
                        "it was not started again. Nothing was saved. Ask again to start over."))
            await _save(job)
            await _report(job)
            continue
        if st.get("status") == STATUS_NOTES and st.get("transcript_written"):
            _LIVE[job.id] = job
            job.task = asyncio.get_running_loop().create_task(_notes_again(job), name=f"notes:{job.id}")
            started += 1
            continue
        try:
            tr = await choose_now(env, local_only=await _local_only_now(job))
        except R.RecordingRefused as exc:
            _fail(job, str(exc))
            await _save(job)
            await _report(job)
            continue
        st["transcriber"] = tr.as_dict()
        st["status"] = STATUS_HEARING
        st["heard_s"] = 0.0
        job.transcriber = tr
        _LIVE[job.id] = job
        job.task = asyncio.get_running_loop().create_task(_run(job), name=f"recording:{job.id}")
        started += 1
    return started


async def _notes_again(job: Job) -> None:
    try:
        await _notes(job)
    except asyncio.CancelledError:
        job.done.set()
        raise
    except Exception:  # noqa: BLE001
        job.state["status"] = STATUS_DONE
        job.state["notes_why"] = "The notes were not written. The transcript is saved."
        await _save(job)
    job.done.set()
    await _report(job)


def _interrupted(home: Path) -> "list[dict[str, Any]]":
    out: list[dict[str, Any]] = []
    folder = jobs_dir(home)
    if not folder.is_dir():
        return out
    for i, path in enumerate(sorted(folder.glob("rec_*.json"))):
        if i > 2000 or path.name.endswith((".lines.json", ".progress.json")):
            continue
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and st.get("status") in RUNNING and R.valid_recording_id(st.get("id")):
            out.append(st)
    return out


async def choose_now(env: Env, *, local_only: bool) -> R.Transcriber:
    """The transcriber the user's pick names right now (off the loop: the
    model's folder is stat'ed, the vault read for the service)."""
    deps = voice_deps()
    pick = getattr(env.config, "recording_transcriber", R.PICK_SAME_AS_VOICE)

    def _read() -> "tuple[Any, Any]":
        backend = None
        try:
            backend = deps._voice_backend() if deps is not None else None
        except Exception:  # noqa: BLE001 — no service readable: none chosen
            backend = None
        try:
            from ..voice import vosk_model_path

            here = (deps._vosk_model_path() if deps is not None else vosk_model_path(env.config))
        except Exception:  # noqa: BLE001
            here = None
        return backend, here

    backend, here = await asyncio.to_thread(_read)
    return R.choose_transcriber(pick, backend, here, local_only=local_only)


def cancel_all() -> None:
    """At shutdown: every job's worker is stopped and its task cancelled (the
    state stays as it was, so the next boot starts it again)."""
    for job in list(_LIVE.values()):
        if job.task is not None and not job.task.done():
            proc = job.proc
            if proc is not None:
                try:
                    from ..core import procs

                    procs.kill_tree(proc)
                except Exception:  # noqa: BLE001
                    pass
            job.task.cancel()


__all__ = [
    "Env", "INLINE_WAIT_S", "Job", "MAX_ATTEMPTS", "NotesError", "PHONE_MAX_S", "RECORDING_DAYS",
    "STALL_S", "STATUS_ASK", "STATUS_DONE", "STATUS_FAILED", "STATUS_HEARING", "STATUS_NOTES",
    "STATUS_UNDONE", "cancel_all", "card_of", "choose_now", "consent_given", "home_turn", "keep_consent",
    "keep_minutes", "lend_voice_deps", "live", "live_ids", "load_lines", "load_state", "new_state",
    "notes_after_yes", "notes_choice", "reconcile_at_boot", "report_words", "save_state", "start", "stop",
    "voice_deps", "wait_inline", "write_new", "write_notes",
]
