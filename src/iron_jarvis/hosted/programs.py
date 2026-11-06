"""What a Code-view tool still SERVES, and the session the user looks at
(v1.422.0, audit gap B4 + fix plan C4.7).

THE BUG: the idle reaper (``service.HostedService.reap_once``) stopped a
session's coding tool after :data:`service.IDLE_S` (15 min) with no turn —
and a stop is a tree kill, so a dev server the tool had started (``npm run
dev &``, ``start python -m http.server``, a ``node server.js`` it left
running) died with it, with nothing said. v1.418.0 kept a session whose tool
TRACKS background tasks (Claude Code's ``task_started``); a program the tool
started and does not track was still killed silently.

WHAT COUNTS AS "A PROGRAM IT STARTED THAT SERVES" — read from the tool's OWN
tree, never by a program's name:

* a member of the tool's process tree: its Job's members (``core.procs``: a
  Code-view tool runs in a kill-on-close Job no member may leave, so a server
  whose shell already exited is still found) plus every process below the
  tool by parent links (``hosted.process.descendants`` — a Store program
  started from a command is never a Job member, measured v1.416.0);
* that holds a TCP socket in the LISTEN state (``psutil.net_connections``:
  one system-wide read, about 1.5 ms on the owner's PC, and no admin needed —
  measured 2026-11-01);
* that is not the tool's process itself (OpenCode's own server listens in
  its root process, ``--port 0``), and that was STARTED AFTER THE TOOL FIRST
  ACTED in this process (its first ``tool`` step, noted by :func:`note_event`
  from ``service._emit``). The tool's own helpers start before that: MCP
  servers are started while the tool comes up, before its model is asked
  anything. "MCP servers never listen" IS FALSE — measured on the owner's
  PC: the QuickBooks MCP server (a ``node`` child of the app that runs it)
  listens on 8765. The birth-time rule is what keeps it out. Known false
  positive, kept on purpose (bounded and SAID by name): an MCP server the tool
  starts LATE (after its first step) and that listens reads as a program.

THE RULES the reaper and the pool apply (:func:`decide`, :func:`evict_pick`):

* a session the user is LOOKING AT (the Code view read it, or beat for it,
  within :data:`LOOK_FRESH_S` — :func:`note_looked`) is stopped after
  :data:`LOOKED_AT_IDLE_S` (60 min) with no turn instead of 15 (C4.7, "the
  session in front"). The Code view beats every minute while its window is
  seen; a window that is hidden, closed or on another page stops beating, so
  within :data:`LOOK_FRESH_S` the session is back on the 15-minute rule;
* a session whose tree runs a program it started that listens is not stopped
  while that program runs — bounded by :data:`PROGRAM_KEEP_MAX_S` (8 h) with
  no turn and no word from the tool, so a forgotten server does not hold a
  process forever;
* the pool (at most ``service.MAX_LIVE`` processes) still stops one when it
  must: a session with programs is picked LAST, a looked-at one next-to-last;
* when a stop DOES end programs, it is SAID: a notice in the session's own
  conversation at once (:func:`stopped_words`), the session row
  (``stopped_note``) and the next start's notice (:func:`start_text`), each
  naming the programs, masked, one line each. Never silent.

Every process read is BLOCKING and runs off the loop through
:func:`read_many` — bounded by :data:`READ_TIMEOUT_S`, one read in flight at
a time (a stuck read never stacks a second thread under it). A read that
could not answer keeps the session for that minute and is retried, at most
:data:`UNREAD_KEEPS_MAX` times in a row; then the old rule applies.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: C4.7: the session the user looks at is stopped after this long with no
#: turn (every other one after ``service.IDLE_S``).
LOOKED_AT_IDLE_S = 60 * 60.0
#: A look (a view read, or the Code view's beat) counts this long. The Code
#: view beats every :data:`BEAT_S` while its window is seen.
LOOK_FRESH_S = 3 * 60.0
#: How often the Code view says it still shows a session (the dashboard's
#: ``CODE_SEEN_BEAT_MS`` is this, in milliseconds).
BEAT_S = 60.0
#: A session kept for a program it runs is stopped anyway after this long
#: with no turn and no word from the tool (a forgotten dev server).
PROGRAM_KEEP_MAX_S = 8 * 60 * 60.0
#: A listening process born up to this long BEFORE the tool's first step
#: still counts as started by it (the step's event reaches the loop a moment
#: after the tool already began it).
STARTED_SLACK_S = 2.0
#: One process read (every session at once) gives up after this long.
READ_TIMEOUT_S = 5.0
#: A read that could not answer keeps the session at most this many minutes
#: in a row (the reaper looks once a minute); then the old rule applies.
UNREAD_KEEPS_MAX = 5
#: At most this many programs are named (the rest are counted).
SHOWN_MAX = 5
#: A program's command line as shown: one line, masked, at most this long.
COMMAND_CHARS = 120


#: v1.422.0 review: "Close <tool>" pressed while the tool works with no
#: message of the user's running (a reply of its own, helpers, a card).
CLOSE_WAITS = (
    "{label} is still working on its own (writing a reply, running helpers, or waiting for your "
    "answer) — let it finish or stop it, then close it."
)


@dataclass(frozen=True)
class Program:
    """One listening program the tool started (never shown with its pid)."""

    pid: int
    created: float
    ports: tuple[int, ...]
    command: str

    def words(self) -> str:
        """``node server.js (port 3000)``."""
        return f"{self.command} ({port_words(self.ports)})"

    def row(self) -> dict[str, Any]:
        """What the session row carries: the masked command and the ports."""
        return {"command": self.command, "ports": list(self.ports)}


@dataclass
class Reading:
    """One look at a session's tree. ``known`` False: the read did not
    answer (timed out, failed) — nothing may be concluded from it."""

    known: bool
    programs: list[Program] = field(default_factory=list)


def port_words(ports: tuple[int, ...] | list[int]) -> str:
    ps = [str(p) for p in ports]
    if not ps:
        return "no port"
    if len(ps) == 1:
        return f"port {ps[0]}"
    return f"ports {', '.join(ps[:-1])} and {ps[-1]}"


# --------------------------------------------------------------------------- #
# What the service notes on its LiveSession (no state of this module's own)
# --------------------------------------------------------------------------- #
def note_event(live: Any, ev: dict[str, Any]) -> None:
    """``service._emit`` calls this for every event: the FIRST ``tool`` step
    of the CURRENT process is when the tool started acting (wall clock, the
    clock ``psutil``'s start times use). A new process (a restart, a resume
    after a stop) starts again from none. Never raises."""
    try:
        if ev.get("type") != "tool":
            return
        proc = getattr(live, "proc", None)
        if proc is None:
            return
        if getattr(live, "first_step_proc", None) is proc:
            return
        live.first_step_proc = proc
        live.first_step_at = time.time()
    except Exception:  # noqa: BLE001 — a note must never break an event
        log.debug("could not note a Code-view step", exc_info=True)


def first_step(live: Any) -> float | None:
    """When the CURRENT process first acted, or None (not yet, or another
    process noted it)."""
    proc = getattr(live, "proc", None)
    if proc is None or getattr(live, "first_step_proc", None) is not proc:
        return None
    at = getattr(live, "first_step_at", None)
    return float(at) if at else None


def note_looked(live: Any, now: float) -> None:
    """The Code view read this session, or beat for it (``now`` = the
    service's clock)."""
    live.looked_at = float(now)


def looked_at(live: Any, now: float) -> bool:
    return now - float(getattr(live, "looked_at", float("-inf"))) < LOOK_FRESH_S


def idle_limit(live: Any, now: float, base: float) -> float:
    """How long with no turn before the reaper may stop this session:
    :data:`LOOKED_AT_IDLE_S` for one the user looks at, else ``base``."""
    return max(LOOKED_AT_IDLE_S, base) if looked_at(live, now) else base


# --------------------------------------------------------------------------- #
# Reading the tree (BLOCKING)
# --------------------------------------------------------------------------- #
def _listening(psutil: Any) -> dict[int, set[int]]:
    """pid -> the TCP ports it listens on (one system-wide read)."""
    out: dict[int, set[int]] = {}
    for c in psutil.net_connections(kind="tcp"):
        if c.status != psutil.CONN_LISTEN or not c.pid:
            continue
        port = getattr(c.laddr, "port", None) if c.laddr else None
        if port:
            out.setdefault(int(c.pid), set()).add(int(port))
    return out


def _members(root: int, job: Any) -> set[int]:
    """Every process of the tool's tree: its Job's members and everything
    below it by parent links. Never the root."""
    from .process import descendants

    found: set[int] = set()
    try:
        if job is not None and not getattr(job, "closed", False):
            found.update(int(p) for p in job.member_pids())
    except Exception:  # noqa: BLE001 — the parent links still count
        log.debug("could not read a Code-view tool's job", exc_info=True)
    found.update(pid for pid, _made in descendants(root))
    found.discard(int(root))
    return found


def short_argv(argv: list[str]) -> str:
    """A command line a person reads: the program and every absolute path
    by their last part (``node vite.js --port 3000``, not the folders), then
    masked and cut by :func:`_command`."""
    import ntpath
    import posixpath

    from ..mcp.secrets import SECRET_KEY_RX

    out = []
    for i, word in enumerate(argv):
        w = str(word)
        if i == 0 or ntpath.isabs(w) or posixpath.isabs(w):
            w = ntpath.basename(w.rstrip("\\/")) or w
        key, eq, value = w.partition("=")
        if eq and any(ch.isspace() for ch in value) and SECRET_KEY_RX.search(key.lstrip("-")):
            # v1.422.0 review: ``--password=my secret phrase`` in ONE word —
            # the key=value masker stops at the first space ("***secret
            # phrase"), so a secret-named key's spaced value goes whole.
            w = f"{key}=***"
        if any(ch.isspace() for ch in w):
            # v1.422.0 review: ONE word, quoted, so the masker that splits the
            # line again (``_shown_command``, quotes kept together) hides a
            # value with a space WHOLE — ``--password "my secret phrase"``
            # showed "*** secret phrase". A quote inside never ends it early.
            w = '"' + " ".join(w.replace('"', "'").split()) + '"'
        out.append(w)
    return " ".join(out)


def _command(psutil: Any, p: Any) -> str:
    from ..harness.process import _shown_command
    from .events import clip

    try:
        words = short_argv(p.cmdline())
    except (psutil.Error, OSError):
        words = ""
    if not words.strip():
        try:
            words = p.name()
        except (psutil.Error, OSError):
            words = "a program"
    return clip(_shown_command(words), COMMAND_CHARS) or "a program"


def read_one(root: int | None, job: Any, started: float | None, listening: dict[int, set[int]]) -> Reading:
    """BLOCKING: the listening programs the tool rooted at ``root`` started
    at or after ``started`` (its first step), oldest first."""
    if not root or not started:
        return Reading(True)
    import psutil

    programs: list[Program] = []
    for pid in sorted(_members(int(root), job) & set(listening)):
        try:
            p = psutil.Process(pid)
            made = p.create_time()
        except (psutil.Error, OSError):
            continue
        if made < started - STARTED_SLACK_S:
            continue  # it was there before the tool acted: the tool's own
        programs.append(Program(pid, made, tuple(sorted(listening[pid])), _command(psutil, p)))
    programs.sort(key=lambda pr: pr.created)
    return Reading(True, programs)


def read(specs: list[tuple[str, int | None, Any, float | None]]) -> dict[str, Reading]:
    """BLOCKING: ``(sid, root pid, job, first step)`` -> each session's
    :class:`Reading`, from ONE system-wide socket read."""
    import psutil

    listening = _listening(psutil)
    out: dict[str, Reading] = {}
    for sid, root, job, started in specs:
        try:
            out[sid] = read_one(root, job, started, listening)
        except Exception:  # noqa: BLE001 — that session is unknown, the others are not
            log.debug("could not read a Code-view session's programs", exc_info=True)
            out[sid] = Reading(False)
    return out


_READING = threading.Lock()


def spec_of(live: Any) -> tuple[str, int | None, Any, float | None]:
    """What :func:`read` needs of a live session (read ON the loop)."""
    from ..core import procs

    proc = getattr(live, "proc", None)
    pid = getattr(proc, "pid", None) if proc is not None else None
    job = procs._job_of(getattr(proc, "proc", None)) if proc is not None else None
    return (live.sid, pid if isinstance(pid, int) else None, job, first_step(live))


def _unknown(specs: list[tuple[str, int | None, Any, float | None]]) -> dict[str, Reading]:
    return {s[0]: Reading(False) for s in specs}


def _read_on_own_thread(specs: list[tuple[str, int | None, Any, float | None]], done: Any) -> bool:
    """Start ONE :func:`read` of ``specs`` on a thread of its own and hand its
    answer (None when it failed) to ``done`` from that thread. False — nothing
    started — while an earlier read still runs (never a second thread stacked
    under a stuck one).

    v1.422.0 review: never the loop's default executor (``asyncio.to_thread``,
    the first cut). That pool is SHARED with every offloaded job of the
    daemon; behind a busy pool the read waited out its bound, the session read
    "unknown" minute after minute, and after :data:`UNREAD_KEEPS_MAX` of those
    the idle stop ended a dev server it had never been able to see — silently
    (the v1.335.0 lesson: a bounded wait on a shared pool queues behind
    everyone). A daemon thread: a stuck read never holds up shutdown."""
    if not _READING.acquire(blocking=False):
        return False

    def run() -> None:
        got: dict[str, Reading] | None = None
        try:
            got = read(specs)
        except Exception:  # noqa: BLE001 — nothing known; the callers say so
            log.warning("could not read what the Code view's coding tools are running", exc_info=True)
        finally:
            _READING.release()
        try:
            done(got)
        except Exception:  # noqa: BLE001 — a caller that went away
            log.debug("a Code-view program read finished for nobody", exc_info=True)

    try:
        threading.Thread(target=run, name="code-programs-read", daemon=True).start()
    except BaseException:
        _READING.release()
        raise
    return True


async def read_many(lives: list[Any]) -> dict[str, Reading]:
    """Each session's :class:`Reading`, off the loop and bounded. A read that
    times out (or one still running from before) answers ``known=False`` for
    every session: never a second thread stacked under a stuck one."""
    if not lives:
        return {}
    specs = [spec_of(lv) for lv in lives]
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def settle(got: dict[str, Reading] | None) -> None:
        if not fut.done():
            fut.set_result(got)

    def done(got: dict[str, Reading] | None) -> None:
        try:
            loop.call_soon_threadsafe(settle, got)
        except RuntimeError:  # the loop is gone (shutdown): nobody waits
            pass

    if not _read_on_own_thread(specs, done):
        return _unknown(specs)
    try:
        got = await asyncio.wait_for(fut, READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("reading what the Code view's coding tools run took over %.0f s", READ_TIMEOUT_S)
        return _unknown(specs)
    return got if got is not None else _unknown(specs)


#: v1.422.0 review: the update warning's read (``GET /system/activity``) is
#: bounded well inside the desktop's 3 s wait for that answer.
ACTIVITY_READ_S = 1.5


def serving_counts(platform: Any, *, timeout: float = ACTIVITY_READ_S) -> dict[str, Any]:
    """BLOCKING (a sync route's thread): ``{serving, tools}`` — the Build ›
    Code sessions whose coding tool runs a program it started that serves (a
    dev server), not already counted as a reply (``news.running_rows``: a
    reply of the user's, helpers at work, a reply of the tool's own), and the
    tools' names.

    v1.422.0 review: the update warning counted only replies, so installing
    an update ended a dev server with nothing said. These count toward
    ``busy`` (the warning names them) and NEVER toward ``working`` (a server
    waiting for requests must not keep the PC awake). Read fresh — a dev
    server started a minute ago dies in an update as surely as one the idle
    stop already kept — and bounded by ``timeout``; when the read cannot
    answer, a session counts by what the idle stop last kept it for. A
    session whose tool has not acted yet runs nothing of its own: no read at
    all. Never raises."""
    svc = getattr(platform, "hosted", None)
    if svc is None:
        return {"serving": 0, "tools": []}
    try:
        from .words import label

        now = svc.clock()
        lives = []
        for live in list(svc.live.values()):
            if not live.running or first_step(live) is None:
                continue
            if (live.turn is not None and live.turn.running) or live.helpers_working(now) \
                    or live.replying_alone(now):
                continue  # a reply row: counted (and named) as a coding session working
            lives.append(live)
        if not lives:
            return {"serving": 0, "tools": []}
        specs = [spec_of(lv) for lv in lives]
        box: dict[str, Any] = {}
        ready = threading.Event()

        def done(got: dict[str, Reading] | None) -> None:
            box["got"] = got
            ready.set()

        readings = _unknown(specs)
        if _read_on_own_thread(specs, done) and ready.wait(timeout) and box.get("got") is not None:
            readings = box["got"]
        serving = [lv for lv in lives if known_programs(readings.get(lv.sid), lv)]
        return {"serving": len(serving), "tools": sorted({label(lv.harness) for lv in serving})}
    except Exception:  # noqa: BLE001 — the warning never fails over this
        log.warning("could not count the Code view's sessions that run programs", exc_info=True)
        return {"serving": 0, "tools": []}


# --------------------------------------------------------------------------- #
# The decisions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Verdict:
    keep: bool
    #: The programs a stop would end (named in the words), oldest first.
    programs: tuple[Program, ...] = ()
    #: How long the session had gone quiet when it was stopped (for the words).
    after_s: float = 0.0
    #: Why a stop happens: "idle" (its idle limit) or "bound" (kept for its
    #: programs until :data:`PROGRAM_KEEP_MAX_S`).
    why: str = "idle"


def decide(live: Any, reading: Reading | None, *, quiet: float, now: float, base: float) -> Verdict:
    """Keep or stop ``live``, whose tool has been quiet ``quiet`` seconds
    (already past ``base``). Updates ``live.keeping`` (what the row says) and
    ``live.unread``."""
    limit = idle_limit(live, now, base)
    if reading is None or not reading.known:
        live.unread = int(getattr(live, "unread", 0) or 0) + 1
        last = getattr(live, "keeping", None)
        if live.unread <= UNREAD_KEEPS_MAX and quiet < PROGRAM_KEEP_MAX_S:
            return Verdict(True)  # cannot tell this minute: look again next minute
        programs = tuple(last.programs) if last is not None else ()
    else:
        live.unread = 0
        programs = tuple(reading.programs)
    if programs and quiet < PROGRAM_KEEP_MAX_S:
        live.keeping = Reading(True, list(programs))
        return Verdict(True, programs)
    live.keeping = None
    if quiet < limit:
        return Verdict(True)
    return Verdict(False, programs, after_s=limit if not programs else quiet,
                   why="bound" if programs else "idle")


def known_programs(reading: Reading | None, live: Any) -> list[Program]:
    """What a stop of ``live`` ends, as far as is known: this reading's
    programs, else (it did not answer) the last ones the reaper kept it for."""
    if reading is not None and reading.known:
        return list(reading.programs)
    last = getattr(live, "keeping", None)
    return list(last.programs) if last is not None else []


def evict_pick(lives: list[Any], readings: dict[str, Reading], now: float, key: Any) -> Any:
    """The session the pool stops: one with no program first, then one the
    user is not looking at, then the least recently active (``key``). A read
    that did not answer counts as running programs (never the first pick)."""

    def serves(lv: Any) -> bool:
        r = readings.get(lv.sid)
        return r is None or not r.known or bool(r.programs)

    return sorted(lives, key=lambda lv: (serves(lv), looked_at(lv, now), key(lv)))[0]


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #
def _duration(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60.0)))
    if minutes >= 120:
        return f"{int(round(minutes / 60.0))} hours"
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def _named(programs: list[Program] | tuple[Program, ...]) -> list[str]:
    shown = [p.words() for p in list(programs)[:SHOWN_MAX]]
    more = len(programs) - len(shown)
    if more > 0:
        shown.append(f"and {more} more")
    return shown


def keep_words(tool: str, programs: list[Program] | tuple[Program, ...]) -> str:
    """The row's line while a session is kept for what it runs:
    "Keeping Claude Code open: it runs a program on port 3000 (node
    server.js). …" — and how to stop it."""
    if not programs:
        return ""
    if len(programs) == 1:
        p = programs[0]
        runs = f"it runs a program on {port_words(p.ports)} ({p.command})"
        stops = "that program stops"
        goes = "the program stops with it"
    else:
        runs = f"it runs {len(programs)} programs ({'; '.join(_named(programs))})"
        stops = "they stop"
        goes = "the programs stop with it"
    hours = _duration(PROGRAM_KEEP_MAX_S)
    return (
        f"Keeping {tool} open: {runs}. It closes by itself once {stops}, or after {hours} with "
        f"nothing new. To close it now, ask {tool} to stop it, or press Close {tool} ({goes})."
    )


def stopped_words(tool: str, programs: list[Program] | tuple[Program, ...], *, why: str, after_s: float = 0.0,
                  max_live: int = 6) -> str:
    """The notice when a stop ended programs ("" when it ended none — the
    next start says that stop as before). One line per program, masked."""
    if not programs:
        return ""
    if why == "evicted":
        head = (f"Iron Jarvis closed {tool} to make room for another session (at most {max_live} "
                f"run at once).")
    elif why == "restart":
        head = f"Iron Jarvis restarted {tool} for your new settings."
    elif why == "terminal":
        # v1.422.0 review: "Open in terminal" stops the tool here (two
        # processes on one conversation corrupt it).
        head = f"Iron Jarvis closed {tool} here to open this conversation in a terminal."
    elif why == "account":
        # v1.422.0 review: "Continue on the next account" stops it first.
        head = f"Iron Jarvis closed {tool} to move this conversation to another account."
    elif why == "branch":
        # v1.441.0 review: Branch from here stops a tool whose fork switched
        # (Pi) or holds the fork (Codex).
        head = f"Iron Jarvis closed {tool} here to branch this conversation."
    elif why == "rewind":
        head = f"Iron Jarvis closed {tool} to rewind this conversation."
    elif why == "bound":
        head = f"Iron Jarvis closed {tool} after {_duration(after_s)} with nothing new."
    else:
        head = f"Iron Jarvis closed {tool} after {_duration(after_s)} with nothing to do."
    which = "The program it ran stopped" if len(programs) == 1 else "The programs it ran stopped"
    lines = "\n".join(f"- {w}" for w in _named(programs))
    return f"{head} {which} with it:\n{lines}"


def start_text(base: str, programs: list[str]) -> str:
    """The next start's notice: ``base`` (``words.start_notice``), and the
    programs the last stop ended, when there were any."""
    if not programs:
        return base
    names = "; ".join(programs)
    one = len(programs) == 1
    return f"{base} It was running {names}, which stopped with it — start {'it' if one else 'them'} again if you need {'it' if one else 'them'}."


def idle_start_words(label: str, after_s: float) -> str:
    """``words.start_notice``'s "idle" sentence for a stop after a limit that
    is not the plain 15 minutes (60 for the session the user looked at, the
    8-hour bound)."""
    return f"Resuming {label} after {_duration(after_s)} idle…"
