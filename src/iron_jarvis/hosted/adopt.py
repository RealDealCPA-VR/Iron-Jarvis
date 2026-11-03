"""Build › Code sessions carried across an Iron Jarvis restart (v1.422.0).

The tools run in the CODE HOST (``iron_jarvis/codehost.py``), which outlives
a restart. This module is how a restarted Iron Jarvis takes each of them back
WITHOUT starting anything, resuming anything, or showing anything twice:

1. WHAT IS KEPT. About a second after a tool's lines arrive and just after
   every write, the session's state goes to the host (:func:`state_blob`):
   the driver's own state (``Driver.state`` — every attribute with a JSON
   form: the tool's session id, request counters, the stream it is in, its
   open steps, its helper agents …) and the session's (the running reply's
   id and start, the cards still open, the title, the Iron Jarvis tools'
   token BY ITS DIGEST). The host keeps the newest state and only the lines
   after it.

2. WHAT WAS ALREADY SHOWN. Every event the session logs carries the tool's
   line it came from (:func:`stamped`, ``htool`` / ``hseq``). The newest one
   in the log (``shown``) says how far the user has seen: the log is written
   in order, so if that event reached the file, so did every event of every
   earlier line. And every write the old daemon sent says which line it was
   reading (the host's ``writes_upto``).

3. TAKING IT BACK (:func:`adopt_all`, at boot, BEFORE the window can ask):
   a fresh driver is made exactly as a start makes one, its kept state put
   back (``Driver.restore`` + each driver's ``after_restore``, which re-arms
   the waits that have no JSON form — an ACP prompt's answer, Pi's prompt,
   Codex's turn id, Claude Code's held reply end), and then every line the
   host kept is read again IN ORDER through the same door as a live line
   (``hostlink.RemoteProcess``): a line at or below ``shown`` rebuilds the
   driver but says nothing (its events are in the log already); a line at or
   below ``writes_upto`` writes nothing (whatever it set going was sent
   already); later lines are live. A card raised by a line already shown
   gets the SAME id as before (``driver.ASK_SEED``) and is put back as open
   unless the log has the user's answer to it — then the driver is told that
   answer instead. The reply that was running gets its turn back (same id,
   its events so far from the log), so the window follows the rest of it
   live, and its end is written ONCE (``HostedService._end_turn``).

4. WHEN IT CANNOT: the host dropped lines (``overflowed``: more output than
   it keeps came while nobody was attached), or no state was kept, or the
   kept state is of another kind — the tool is stopped GRACEFULLY (its
   conversation saved), the running reply is closed with words, and the next
   message resumes the conversation with the CLI's own resume, as before.
   (v1.422.0 review) The same holds for a kept session whose adoption RAISED,
   ran out of time, or whose host went away in between: :func:`at_boot` is
   the lifespan's one step and :func:`close_unadopted` ends each such one —
   the boot reconcile left them all alone.

Per tool (pinned in ``tests/test_code_host_adopt_v14220.py``): Claude Code,
Codex, Pi, OpenCode and Grok are all carried on mid-reply by the same path —
their replies end on lines (Claude ``result``, Codex ``turn/completed``, Pi
``agent_settled``) or on a prompt's answer whose wait ``after_restore``
re-arms (OpenCode, Grok). Not kept: a note sent while it worked before the
restart is not said "read" afterwards; Pi's 0.5 s "handled with no run"
grace; a context-usage question in flight.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from . import hostlink as HL
from . import open_steps as OS
from . import store
from .driver import ALLOW_LIKE, Ask, decode_value, encode_value
from .events import FULL_KEY, coalesce, make, session_dir

log = logging.getLogger(__name__)

#: The kept state's shape.
STATE_VERSION = 1
#: The log's own names for "which tool, which line" (never sent to a window).
STAMP_TOOL = "htool"
STAMP_SEQ = "hseq"
#: How much of a session's log is read to learn what was shown.
LOG_TAIL_BYTES = 8 * 1024 * 1024

GAVE_UP_TITLE = "Iron Jarvis restarted while this was working"
GAVE_UP_OVERFLOW = ("The reply went on too long to pick up again, so {label} was stopped with the "
                    "conversation saved. It carries on from there by itself, or says why not.")
GAVE_UP_PLAIN = ("{label} could not be picked up again, so it was stopped with the conversation saved. "
                 "It carries on from there by itself, or says why not.")


# --------------------------------------------------------------------------- #
# 1. What is kept
# --------------------------------------------------------------------------- #
def stamped(ev: dict[str, Any], stamp: tuple[str, int]) -> dict[str, Any]:
    """The LOG's copy of ``ev``, naming the tool's line it came from. A step's
    kept texts come off ``ev`` itself in place (the events module's rule: the
    same dict goes to the turn's watchers next) and ride the copy only."""
    full = ev.pop(FULL_KEY, None)
    out = {**ev, STAMP_TOOL: stamp[0], STAMP_SEQ: int(stamp[1])}
    if full:
        out[FULL_KEY] = full
    return out


def busy(live: Any) -> bool:
    """Working, as the host's orphan rule reads it: a reply, an open card,
    helpers or a run of the tool's own."""
    try:
        return bool((live.turn is not None and live.turn.running) or live.asks or live.background_tasks
                    or live.working_alone)
    except Exception:  # noqa: BLE001
        return False


def _ask_state(ask: Ask) -> dict[str, Any] | None:
    try:
        return {"id": ask.id, "kind": ask.kind, "tool": ask.tool, "native": encode_value(ask.native),
                "data": encode_value(ask.data), "event": encode_value(ask.event), "asked_at": ask.asked_at,
                "can_session": bool(ask.can_session)}
    except ValueError:
        return None


def _ask_from(d: Any) -> Ask | None:
    if not isinstance(d, dict) or not d.get("id"):
        return None
    data = decode_value(d.get("data"))
    event = decode_value(d.get("event"))
    return Ask(str(d["id"]), str(d.get("kind") or "approval"), str(d.get("tool") or ""),
               decode_value(d.get("native")), data if isinstance(data, dict) else {},
               event if isinstance(event, dict) else {}, str(d.get("asked_at") or ""),
               bool(d.get("can_session")))


def _iso(v: Any) -> str:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return str(v or "")


def state_blob(svc: Any, live: Any) -> tuple[bytes, bool]:
    """ON THE LOOP: the session's state as kept by the host (see 1. above)."""
    turn = live.turn if live.turn is not None and live.turn.running and not live.turn.ending else None
    service = {
        "turn": {"id": turn.id, "started_at": _iso(turn.started_at)} if turn is not None else None,
        "asks": [s for s in (_ask_state(a) for a in list(live.asks.values())) if s is not None],
        "title": live.title,
        "caps": dict(live.caps or {}),
        "folder": live.folder,
        "restart": bool(live.restart),
        "token": svc.tokens.digests.get(live.sid, ""),
        "kept": dict(getattr(live, "kept", {}) or {}),
    }
    blob = {"v": STATE_VERSION, "harness": live.harness,
            "driver": live.driver.state() if live.driver is not None else {}, "service": service}
    return json.dumps(blob, ensure_ascii=False).encode("utf-8"), busy(live)


def watch(svc: Any, live: Any, proc: HL.RemoteProcess) -> None:
    """Keep this session's state in the host from now on."""
    proc.snapshot_source = lambda: state_blob(svc, live)


# --------------------------------------------------------------------------- #
# 2. What was already shown
# --------------------------------------------------------------------------- #
def log_facts(home: Any, sid: str, tool: str) -> dict[str, Any]:
    """BLOCKING: from the end of the session's log — the newest line of
    ``tool`` it shows (``shown``), every card the user answered
    (``resolved``: request id -> decision), and the reply still open in it
    (``open_turn``, "" for none). Never raises."""
    path = session_dir(home, sid) / "events.jsonl"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            data = fh.read()
    except OSError:
        data = b""
        size = 0
    lines = data.splitlines()
    if size > LOG_TAIL_BYTES and lines:
        lines = lines[1:]  # the first one was cut by the seek
    shown = 0
    resolved: dict[str, str] = {}
    done: set[str] = set()
    last_turn = ""
    open_steps: dict[str, dict[str, Any]] = {}  # v1.427.2
    for raw in lines:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        hs = ev.get(STAMP_SEQ)
        if ev.get(STAMP_TOOL) == tool and isinstance(hs, int) and not isinstance(hs, bool):
            shown = max(shown, hs)
        if ev.get("type") == "resolved" and ev.get("request_id"):
            resolved[str(ev["request_id"])] = str(ev.get("decision") or "")
        OS.note(open_steps, ev)
        t = ev.get("turn")
        if t:
            if ev.get("type") == "done":
                done.add(str(t))
            else:
                last_turn = str(t)
    return {"shown": shown, "resolved": resolved,
            "open_turn": last_turn if last_turn and last_turn not in done else "",
            # v1.427.2: the steps the log leaves running — the adopted tool
            # still runs them, and its exit closes them like any other.
            "open_steps": open_steps}


def keep_ids(home: Any) -> list[str]:
    """BLOCKING (boot): the sessions whose tool the code host still holds —
    the boot reconcile leaves their open replies alone. Never starts a host."""
    if not HL.enabled():
        return []
    return HL.live_session_ids(home)


# --------------------------------------------------------------------------- #
# 3. Taking it back
# --------------------------------------------------------------------------- #
async def adopt_all(svc: Any, into: list[str] | None = None) -> list[str]:
    """ON THE LOOP (boot): carry on every session the host kept. ``into``
    (v1.422.0 review): each session is added to it the moment it is carried
    on, so a boot that ran out of time still knows which ones were."""
    if not HL.enabled():
        return []
    link = await asyncio.to_thread(HL.link_for, svc.home, start=False)
    if link is None:
        return []
    adopted: list[str] = into if into is not None else []
    seen: set[str] = set()
    for t in link.tools:
        sid, tool = str(t.get("sid") or ""), str(t.get("tool") or "")
        if not tool:
            continue
        if not sid or sid in seen:
            await asyncio.to_thread(_stop_tool, link, tool)  # one process per conversation
            continue
        seen.add(sid)
        try:
            if await _adopt_one(svc, link, t):
                adopted.append(sid)
        except Exception:  # noqa: BLE001 — one session never stops the others
            log.warning("could not carry on Code-view session %s after the restart", sid, exc_info=True)
            await asyncio.to_thread(_stop_tool, link, tool)
    if adopted:
        log.info("carried on %d Code-view session(s) the code host kept across the restart", len(adopted))
        svc._ensure_reaper()
    return adopted


#: v1.422.0 review: how long the boot waits to take every kept session back.
BOOT_ADOPT_WAIT_S = 60.0


async def at_boot(platform: Any, keep: Any) -> list[str]:
    """THE LIFESPAN'S STEP (v1.422.0 review), after the boot reconcile: carry
    on every session the code host kept (``keep``, from :func:`keep_ids`) —
    and END, in words and once, every one of them that could not be carried
    on. The reconcile left ALL of them alone (their replies may still be
    going), so a session whose adoption raised, ran out of
    :data:`BOOT_ADOPT_WAIT_S`, or whose host went away in between used to
    keep its reply open in the log forever, and (a timeout) its tool running
    in the host under an attached Iron Jarvis that did not know it (the
    host's orphan rules never run while a daemon is attached): the next
    message then started a SECOND process for the same conversation. Never
    raises; returns the sessions carried on."""
    keep = [str(s) for s in (keep or ()) if s]
    if not keep:
        return []
    from .service import service_for

    svc = service_for(platform)
    adopted: list[str] = []
    try:
        await asyncio.wait_for(svc.adopt(into=adopted), timeout=BOOT_ADOPT_WAIT_S)
    except Exception:  # noqa: BLE001 — never block boot (a timeout included)
        log.warning("could not carry on every Code-view session the code host kept", exc_info=True)
    try:
        await close_unadopted(svc, keep, adopted)
    except Exception:  # noqa: BLE001
        log.warning("could not close the Code-view sessions that were not carried on", exc_info=True)
    return list(adopted)


async def close_unadopted(svc: Any, keep: Any, adopted: Any) -> None:
    """ON THE LOOP: every kept session that is NOT carried on ends the way a
    stopped one does — its tool stopped gracefully (its conversation saved),
    its cards closed, its token gone, its open reply closed in words — so the
    next message resumes it with the CLI's own resume, on ONE process."""
    from . import words as W

    done = {str(s) for s in (adopted or ())}
    todo = [str(s) for s in (keep or ()) if s and str(s) not in done]
    if not todo:
        return
    link = await asyncio.to_thread(HL.link_for, svc.home, start=False) if HL.enabled() else None
    for sid in todo:
        try:
            live = svc.live.get(sid)
            row = await asyncio.to_thread(store.get, svc.engine, sid)
            label = W.label(row.harness if row is not None else getattr(live, "harness", ""))
            words = W.error(GAVE_UP_TITLE, GAVE_UP_PLAIN.format(label=label), "")
            running = False
            if live is not None:
                turn = live.turn
                running = turn is not None and turn.running and not turn.ending
                if running:
                    svc._emit(live, dict(words))
                # The process (a half-taken one included), its cards, its token.
                await svc._stop_process(live, end_turn=False)
                if running:
                    await svc._end_turn(live, "interrupted")
            if link is not None and link.alive:
                # A tool the adoption never reached still runs in the host.
                for t in link.tools:
                    tool = str(t.get("tool") or "")
                    if tool and str(t.get("sid") or "") == sid:
                        link.unregister(tool)
                        await asyncio.to_thread(_stop_tool, link, tool)
            if row is None:
                continue
            facts = await asyncio.to_thread(log_facts, svc.home, sid, "")
            if facts["open_turn"]:
                _close_turn(svc._live(row), facts["open_turn"], words)
            if running or facts["open_turn"]:
                await asyncio.to_thread(note_cut, svc.home, sid)
        except Exception:  # noqa: BLE001 — one session never stops the others
            log.warning("could not close Code-view session %s that was not carried on", sid, exc_info=True)


def _stop_tool(link: HL.HostLink, tool: str) -> None:
    try:
        link.request({"t": "stop", "tool": tool, "graceful": True}, timeout=HL.STOP_REPLY_S)
    except Exception:  # noqa: BLE001
        log.debug("could not stop a kept tool", exc_info=True)


async def _adopt_one(svc: Any, link: HL.HostLink, t: dict[str, Any]) -> bool:
    from . import words as W

    sid, tool = str(t["sid"]), str(t["tool"])
    row = await asyncio.to_thread(store.get, svc.engine, sid)
    have = svc.live.get(sid)
    if row is None or row.archived_at is not None or (have is not None and have.running):
        await asyncio.to_thread(_stop_tool, link, tool)
        return False
    live = svc._live(row)
    live.account = row.account
    loop = asyncio.get_running_loop()
    proc = HL.RemoteProcess([], env={}, cwd=row.folder or "", name=f"hosted-{row.id}", home=svc.home,
                            sid=row.id, tool_id=tool)
    held: dict[str, Any] = {}

    def on_message(obj: dict[str, Any]) -> None:
        live.last_heard = svc.clock()
        drv = held.get("driver")
        if drv is None:
            return
        try:
            drv.on_message(obj)
        except Exception:  # noqa: BLE001 — a frame the parser cannot read never ends the session
            log.warning("a %s frame could not be read", row.harness, exc_info=True)

    def on_exit(code: int | None) -> None:
        svc._on_exit(live, proc, code)

    reply, blob = await asyncio.to_thread(proc.attach, link, loop, on_message, on_exit)
    try:
        kept = json.loads(blob.decode("utf-8")) if blob else None
    except ValueError:
        kept = None
    label = W.label(row.harness)
    if reply.get("overflowed") or not isinstance(kept, dict) or kept.get("v") != STATE_VERSION \
            or kept.get("harness") != row.harness:
        why = GAVE_UP_OVERFLOW if reply.get("overflowed") else GAVE_UP_PLAIN
        await _give_up(svc, live, proc, why.format(label=label))
        return False
    service = kept.get("service") if isinstance(kept.get("service"), dict) else {}
    facts = await asyncio.to_thread(log_facts, svc.home, sid, tool)
    resolved: dict[str, str] = facts["resolved"]
    st: dict[str, Any] = {}

    def emit(ev: dict[str, Any]) -> None:
        if proc.silent():
            if ev.get("type") == "done":
                st["done"] = ev  # the reply ended on a line already shown
            return
        svc._emit(live, ev)

    def ask(a: Ask) -> None:
        if not proc.silent():
            svc._ask(live, a)
            return
        decision = resolved.get(a.id)
        if decision is not None:
            # Answered before the restart: the driver hears the answer.
            if decision in ALLOW_LIKE or decision == "deny":
                _answered(held.get("driver"), a, decision)
            return
        _reopen(svc, live, a)

    async def write(obj: dict[str, Any]) -> None:
        await proc.awrite(obj)

    kept_service = service.get("kept") if isinstance(service.get("kept"), dict) else {}
    driver = svc._new_driver(live, row, write, emit=emit, ask=ask,
                             rules_note=str(kept_service.get("rules_note") or ""),
                             acp_servers=kept_service.get("acp_servers") or [])
    driver.restore(kept.get("driver") if isinstance(kept.get("driver"), dict) else {})
    held["driver"] = driver
    proc.ends_left_behind = bool(getattr(driver, "notes_started_on_stop", False))
    live.kept = dict(kept_service)
    live.open_steps = dict(facts.get("open_steps") or {})  # v1.427.2
    live.title = str(service.get("title") or row.title or "")
    live.caps = dict(service.get("caps") or {})
    live.folder = str(service.get("folder") or row.folder or "")
    live.restart = bool(service.get("restart"))
    token = str(service.get("token") or "")
    if token:
        svc.tokens.restore(sid, token, live.caps)
    # The cards open when the state was kept: open again unless answered since.
    for raw in service.get("asks") or []:
        a = _ask_from(raw)
        if a is None:
            continue
        decision = resolved.get(a.id)
        if decision is not None:
            if decision in ALLOW_LIKE or decision == "deny":
                _answered(driver, a, decision)
            continue
        _reopen(svc, live, a)
    # The reply that was running: the same turn again, with what it said so far.
    kept_turn = service.get("turn") if isinstance(service.get("turn"), dict) else None
    open_turn = facts["open_turn"]
    if kept_turn and open_turn == str(kept_turn.get("id") or ""):
        await _turn_back(svc, live, open_turn, str(kept_turn.get("started_at") or ""))
    elif open_turn:
        # A reply the log shows open but the kept state does not know (its
        # message may never have reached the tool): closed, in words.
        _close_turn(live, open_turn, W.error(GAVE_UP_TITLE, "Send the message again — the conversation is kept.",
                                             ""))
    live.proc, live.driver = proc, driver
    watch(svc, live, proc)
    proc.on_replayed = lambda: _caught_up(svc, live, st)
    live.last_active = live.last_heard = svc.clock()
    live.stopped_why = ""
    # (The bell, Running now, the keep-awake counts, the pool and the reaper
    # all read svc.live: an adopted session is like any other from here.)
    proc.release((int(facts["shown"]), int(reply.get("writes_upto") or 0)))
    return True


def _answered(driver: Any, a: Ask, decision: str) -> None:
    if driver is None:
        return
    try:
        driver.answered(a, decision)
    except Exception:  # noqa: BLE001 — a driver with no such bookkeeping
        log.debug("could not tell a driver an answer given before the restart", exc_info=True)


def _reopen(svc: Any, live: Any, a: Ask) -> None:
    """A card still open: answerable again (and on its way to the phone),
    never drawn twice — the log shows it already."""
    from . import news as NEWS

    live.asks[a.id] = a
    if a.kind == "approval" and a.id not in live.phones:
        watch_ = NEWS.PhoneWatch(svc, live, a, after_s=svc.phone_after_s)
        live.phones[a.id] = watch_
        watch_.start()


async def _turn_back(svc: Any, live: Any, turn_id: str, started_at: str) -> None:
    from .history import public
    from .service import HostedTurn

    events, _total, _dropped = await asyncio.to_thread(live.log.read, 0, turn=turn_id)
    turn = HostedTurn(turn_id, live.sid)
    turn.events = coalesce([public(e) for e in events])
    try:
        began = datetime.fromisoformat(started_at)
        began = began if began.tzinfo else began.replace(tzinfo=timezone.utc)
        turn.started_at = began
        elapsed = max(0.0, (datetime.now(timezone.utc) - began).total_seconds())
    except (TypeError, ValueError):
        elapsed = 0.0
    turn.started_mono = svc.clock() - elapsed
    svc.turns[turn_id] = turn
    live.turn = turn


def _close_turn(live: Any, turn_id: str, error: dict[str, Any]) -> None:
    live.log.append({**error, "turn": turn_id})
    live.log.append({**make("done", stop="interrupted"), "turn": turn_id})


def _caught_up(svc: Any, live: Any, st: dict[str, Any]) -> None:
    """ON THE LOOP: every kept line is read. A reply the tool ended on a line
    already shown (its end not yet in the log) ends now — once."""
    turn, drv = live.turn, live.driver
    if turn is None or not turn.running or turn.ending or drv is None or drv.turn_open:
        return
    done = st.get("done")
    stop = str((done or {}).get("stop") or "completed")
    asyncio.ensure_future(svc._end_turn(live, stop, dict(done) if done else None))


async def _give_up(svc: Any, live: Any, proc: HL.RemoteProcess, fix: str) -> None:
    """The fallback: the tool stops GRACEFULLY (its conversation saved) and
    the reply that was open is closed in words; the next message resumes."""
    from . import words as W

    await asyncio.to_thread(proc.stop, graceful=True)
    facts = await asyncio.to_thread(log_facts, svc.home, live.sid, proc.tool_id)
    if facts["open_turn"]:
        _close_turn(live, facts["open_turn"], W.error(GAVE_UP_TITLE, fix, ""))
        await asyncio.to_thread(note_cut, svc.home, live.sid)
    live.stopped_why = ""


def note_cut(home: Any, sid: str) -> None:
    """BLOCKING, never raises. v1.422.0 (integration of B3's host with its
    fallback): a reply the host kept but this boot could not pick up again
    was cut by the restart like any other, so it gets the same cut record
    (``hosted/carry_on.py``, ``cut.json``) the boot reconcile writes for the
    rest — the carry-on pass then carries it on ONCE through the tool's own
    resume, or offers Continue with its reason, by the very same rules. The
    reconcile itself skipped it (``keep``), and its close here is written
    with :data:`GAVE_UP_TITLE`, which a later reconcile does not read as a
    restart's cut — so this is the one place it is recorded."""
    from . import carry_on as CO

    try:
        events = CO._tail_events(session_dir(home, sid) / "events.jsonl")
        facts = CO.analyse_tail(events, stopped_title=GAVE_UP_TITLE)
        if facts is not None:
            CO.note_cut(home, sid, facts)
    except Exception:  # noqa: BLE001 — a record never fails the close
        log.warning("could not record the cut reply of Code-view session %s", sid, exc_info=True)


# --------------------------------------------------------------------------- #
# The daemon stopping
# --------------------------------------------------------------------------- #
async def shutdown(platform: Any, *, keep: bool) -> None:
    """The lifespan's end. ``keep`` (a RESTART): every tool in the host keeps
    running and the host is left. Otherwise (QUIT, an update): every tool is
    stopped gracefully and the host goes too. Never raises."""
    svc = getattr(platform, "hosted", None)
    keep = bool(keep) and HL.enabled()
    if svc is not None:
        try:
            await svc.shutdown(keep_tools=keep)
        except Exception:  # noqa: BLE001
            log.debug("code view shutdown failed", exc_info=True)
    if not HL.enabled():
        return
    home = platform.config.home
    try:
        if keep:
            await asyncio.to_thread(HL.detach_host, home)
        else:
            await asyncio.to_thread(HL.quit_host, home, HL.RETIRE_WAIT_S)
    except Exception:  # noqa: BLE001
        log.debug("could not tell the code host", exc_info=True)
