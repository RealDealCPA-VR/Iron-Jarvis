"""Build › Code CARRIES ON BY ITSELF after an interruption (v1.422.0, Wave 2
"Never lose work": B3's fallback and B5).

Chat › Code has done this since v1.391.0 (a reply a restart cut off) and
v1.393.0 (an account's limit). Build › Code drives the user's REAL Claude
Code / Codex / Pi / OpenCode / Grok, so the work happens in the TOOL's own
conversation; carrying on is one more message to that conversation, sent
through the tool's OWN resume (the service reopens the saved conversation
when a process starts for it: ``claude --resume``, Codex ``thread/resume``,
Pi ``--session-id``, OpenCode ``session/load``, Grok ``session/resume``).
The message is HIDDEN on the page (the ``user`` event carries ``carry``) and
tells the tool what happened and to check what is already done first.

TWO HALVES, two small files beside the session's ``events.jsonl``:

* ``cut.json`` — a reply a RESTART cut off (an update, a PC restart, a
  crash, a host that is gone). :func:`analyse_tail` reads the newest turn of
  a session's log at boot (``service.reconcile_cut_turns``): a turn with no
  ``done`` (a crash) or one Iron Jarvis's own stop ended
  (``service.STOPPED_TITLE`` beside an ``interrupted`` done — every graceful
  shutdown) is a CUT. The record says whether a card or a question was open
  (``waiting``: the question is lost, so it is offered, never carried on by
  itself), whether the cut reply was ITSELF a restart carry-on (``carried``:
  never twice — a carry-on that is cut again does not loop) and whether it
  stopped on an account's limit. :func:`carry_on_after_restart` (one pass
  after boot, from :func:`carry_loop`) carries each one on ONCE, by itself,
  with Chat › Code's v1.391.0 rules: never for a reply the user stopped (no
  record), one that ended (no record), one cut while a card or question was
  open, a session with a newer message, an archived session, a conversation
  the tool never saved, one open in a terminal, beside an account's limit
  card or wait, or while Emergency stop is on (unattended starts are refused;
  the record stays for a later start). Whatever it does not carry on keeps a
  "Continue" press on the page (:func:`continue_cut` — a person's press, so
  Emergency stop does not refuse it).

* ``limits.json`` — an account's LIMIT (B5, Chat › Code's v1.393.0 rules):
  ``card`` (the limit: account, kind, reset), ``wait`` (armed BY ITSELF when
  ``build_auto_resume`` is on and either the reset is known or the account
  shares a group with another account of its vendor) and ``chain`` (how many
  times in a row Iron Jarvis carried on by itself without the account
  serving any work — at :data:`MAX_AUTO_IN_A_ROW` the card waits for a
  press). The loop (:func:`resume_due`) carries the reply on at the reset
  (time through an injected clock), or MOVES it within the user's account
  group (``Config.account_groups``: same vendor, signed in, not parked, below
  the 95% floor — ``harness/build_accounts.takeover``, never forked here) by
  the Continue-on-another-account path (Claude Code's copy, and since this
  release Codex's: :mod:`.codex_move`). A Cancel or a new message ends the
  wait. Grok: waits for the reset when its limit says when; it never moves.

Every write of these files is a read-modify-write under one lock, off the
loop; a file a reader cannot parse is no record. The bell hears what
happened (``build.account_limit`` / ``build.resumed`` with ``surface:
"build_app"`` — the same types Chat › Code uses, only the link differs).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .events import clip, session_dir

log = logging.getLogger(__name__)

CUT_FILE = "cut.json"
LIMITS_FILE = "limits.json"

#: The tools whose conversation can move to another account (Claude Code's
#: copy since v1.303.0; Codex's since v1.422.0, measured — :mod:`.codex_move`).
MOVABLE: tuple[str, ...] = ("claude-code", "codex")
#: At most this many carry-ons in a row Iron Jarvis started by itself after a
#: limit (a move or a reset) without the account serving any work — then the
#: card waits for the user (Chat › Code's ``MAX_GROUP_MOVES``).
MAX_AUTO_IN_A_ROW = 4
#: The loop's own pace (it also wakes when a wait is armed).
TICK_S = 30.0
#: A carry-on the session could not take (another process start was refused,
#: the pool was full) is tried again this long after.
RECHECK_S = 300.0
#: ...at most this many times, then the wait ends and the card waits for a press.
MAX_TRIES = 3
#: v1.422.0 review: a reply a restart cut off is carried on BY ITSELF only when
#: the cut is this recent (Chat › Code keeps its cut-off replies this long:
#: ``harness/reply_record.KEEP_DAYS``). An older cut — every reply an earlier
#: update cut off that the user never came back to, read by the first start of
#: this release — is offered (Continue), never carried on with nobody asking.
CUT_KEEP_DAYS = 7

#: What the TOOL is told (its user message; hidden on the page).
CONTINUE_AFTER_RESTART = (
    "Iron Jarvis restarted while you were working on my last message, so your reply was cut off "
    "before it finished. First check what was already done (look at the files and at what you "
    "ran), then carry on with the task from where it stopped. Do not redo work that is already "
    "finished."
)
CONTINUE_AFTER_RESET = (
    "Your last reply stopped because this account reached its usage limit, and the limit has now "
    "reset. First check what was already done, then carry on with the task from where it "
    "stopped. Do not redo work that is already finished."
)
CONTINUE_AFTER_MOVE = (
    "Your last reply stopped because the account it ran on reached its usage limit, so this "
    "conversation now continues on another account. First check what was already done, then "
    "carry on with the task from where it stopped. Do not redo work that is already finished."
)

# Words a person reads (why a cut reply was not carried on by itself).
WHY_WAITING = (
    "it was waiting for your answer when Iron Jarvis stopped, and that question was lost — "
    "press Continue to carry on"
)
WHY_CARRIED = "it was already carried on once after a restart and was cut off again"
WHY_ARCHIVED = "its session is archived"
WHY_TERMINAL = "its conversation is open in a terminal"
WHY_NOT_SAVED = "the coding tool had not saved this conversation yet"
WHY_LIMIT = "it waits on its account's limit"
#: v1.422.0 review: the account's limit card is there but nothing waits (the
#: setting is off, the chain was used up, it was cancelled, or the reply was a
#: carry-on that had not been served yet) — never "it waits".
WHY_LIMIT_CARD = "it had stopped on its account's limit"
WHY_OLD = "it was cut off more than a week ago"
WHY_NEWER = "a newer message was sent"
WHY_BUSY = "the session is already working"
WHY_POOL = "too many coding sessions were working at once"
WHY_STOP = "Emergency stop is on"
NOTHING_CUT = "Nothing here was cut off by a restart — send your message instead."
NO_WAIT = "This session is not waiting for an account's limit to reset."
MOVED_NOTE = (
    '"{frm}" reached its limit, so this conversation was copied to "{to}" (in your group '
    '"{group}") and carries on there by itself.'
)
#: v1.422.0 review: the carry-on at the reset was refused MAX_TRIES times (the
#: conversation open in a terminal, …): the wait ends, and the card says why.
GAVE_UP_NOTE = "It did not carry on by itself: {why}"
CHAIN_NOTE = (
    "Iron Jarvis carried this on by itself {n} times in a row and the account was at its limit "
    "each time, so it now waits for you."
)

#: (loop, Event) the loop sleeps on — set by :func:`wake`. Holds no service.
_WAKE: dict[str, Any] = {}
#: Every read-modify-write of a session's two files.
_LOCK = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


#: THE clock the waits are armed and judged by (a test sets it; never a sleep).
CLOCK = now_utc


def _iso(when: datetime | None) -> str:
    return when.astimezone(timezone.utc).isoformat() if when is not None else ""


def _parse(value: Any) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# the two files
# --------------------------------------------------------------------------- #
def _path(home: Any, sid: str, name: str) -> Path:
    return session_dir(home, sid) / name


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    if not data:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def read_cut(home: Any, sid: str) -> dict[str, Any]:
    return _read(_path(home, sid, CUT_FILE))


def read_limits(home: Any, sid: str) -> dict[str, Any]:
    return _read(_path(home, sid, LIMITS_FILE))


def _amend_cut(home: Any, sid: str, turn: str, **changes: Any) -> bool:
    """Change the cut record of ``turn`` (only while it is still that turn's)."""
    with _LOCK:
        rec = read_cut(home, sid)
        if not rec or rec.get("turn") != turn:
            return False
        rec.update(changes)
        _write(_path(home, sid, CUT_FILE), rec)
        return True


def drop_cut(home: Any, sid: str) -> None:
    with _LOCK:
        _write(_path(home, sid, CUT_FILE), {})


def note_stopped(home: Any, sid: str, turn: str) -> None:
    """BLOCKING (v1.422.0 review): the user pressed Stop on ``turn``. Kept
    beside the log BEFORE the coding tool is told, so a reply the user
    stopped is never read as one a restart cut off — even when the app goes
    (a Quit, Restart to update, a crash) before the tool confirms the Stop.
    A record already kept for an older turn stays as it is."""
    turn = str(turn or "")
    if not turn:
        return
    with _LOCK:
        rec = read_cut(home, sid)
        rec["stopped"] = turn
        _write(_path(home, sid, CUT_FILE), rec)


# --------------------------------------------------------------------------- #
# boot: what a restart cut off
# --------------------------------------------------------------------------- #
#: Events that are the reply's own progress (a card expired by the stop and
#: then nothing after it means the card was open at the cut).
_PROGRESS = frozenset({"text", "text_done", "thinking", "tool", "plan", "usage", "approval", "question"})


def analyse_tail(events: list[dict[str, Any]], *, stopped_title: str) -> dict[str, Any] | None:
    """The newest turn of a log tail as the restart pass needs it, or None
    (no turn, or one that ended by itself or was stopped by the user):
    ``{turn, closed (a done is there), waiting, carried, limit}``.

    A turn is CUT when it has no ``done`` (a crash), or when its ``done`` is
    ``interrupted`` and Iron Jarvis's own stop said so (``stopped_title`` —
    only the app stopping writes it: a shutdown, a cancelled turn, this
    reconcile). The user's Stop writes no such error: no record."""
    turn = ""
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("turn"):
            turn = str(ev["turn"])
            break
    if not turn:
        return None
    mine = [ev for ev in events if isinstance(ev, dict) and str(ev.get("turn") or "") == turn]
    done = next((ev for ev in reversed(mine) if ev.get("type") == "done"), None)
    by_app = any(ev.get("type") == "error" and ev.get("title") == stopped_title for ev in mine)
    if done is not None and not (done.get("stop") == "interrupted" and by_app):
        return None
    open_asks: set[str] = set()
    expired_last: set[str] = set()
    carried = False
    limit = False
    for ev in mine:
        t = ev.get("type")
        if t == "user":
            carry = ev.get("carry")
            carried = isinstance(carry, dict) and carry.get("why") == "restart"
        elif t in ("approval", "question") and ev.get("request_id"):
            open_asks.add(str(ev["request_id"]))
            expired_last.clear()
        elif t == "resolved":
            rid = str(ev.get("request_id") or "")
            if rid in open_asks:
                open_asks.discard(rid)
                if ev.get("decision") == "expired":
                    # Closed by the stop itself (a graceful shutdown expires
                    # every open card first) — unless the reply went on after.
                    expired_last.add(rid)
        elif t == "error" and ev.get("fix_kind") == "account_limit":
            limit = True
        if t in _PROGRESS and t not in ("approval", "question"):
            expired_last.clear()
    # v1.422.0 review: WHEN it was cut — the newest time on the reply's own
    # events (read before the boot reconcile appends its closing rows, which
    # carry the boot's time): a cut from long ago is never carried on by itself.
    times = [t for t in (_parse(ev.get("at")) for ev in mine) if t is not None]
    return {
        "turn": turn,
        "closed": done is not None,
        "waiting": bool(open_asks or expired_last),
        "carried": carried,
        "limit": limit,
        "cut_at": _iso(max(times)) if times else "",
    }


def note_cut(home: Any, sid: str, facts: dict[str, Any]) -> None:
    """Keep the boot's finding for the pass (BLOCKING). A record already kept
    for the SAME turn stays as it is (its decision is kept: a reply is never
    carried on twice); a cut of the turn a carry-on STARTED is ``carried``."""
    with _LOCK:
        old = read_cut(home, sid)
        turn = str(facts.get("turn") or "")
        if old.get("turn") == turn:
            return
        if turn and old.get("stopped") == turn:
            # v1.422.0 review: the USER pressed Stop on this reply, and the app
            # went before the coding tool confirmed it (Iron Jarvis's own stop
            # then ended it): never a cut — never carried on, never offered.
            return
        rec = {
            "turn": turn,
            "waiting": bool(facts.get("waiting")),
            "carried": bool(facts.get("carried")) or (bool(turn) and old.get("to") == turn),
            "limit": bool(facts.get("limit")),
            "at": _iso(CLOCK()),
            "cut_at": str(facts.get("cut_at") or ""),
            "auto": "",
        }
        _write(_path(home, sid, CUT_FILE), rec)


def _tail_events(path: Path, size: int = 256 * 1024) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            fh.seek(max(0, end - size))
            raw = fh.read().splitlines()
    except OSError:
        return out
    for line in raw:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def newest_turn(events: list[dict[str, Any]]) -> str:
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("turn"):
            return str(ev["turn"])
    return ""


def _pending_cuts(home: Any) -> list[tuple[str, dict[str, Any]]]:
    out = []
    try:
        paths = list((Path(home) / "hosted").glob(f"*/{CUT_FILE}"))
    except OSError:
        return out
    for p in paths[:2000]:
        rec = _read(p)
        if rec.get("turn") and not rec.get("auto"):
            out.append((p.parent.name, rec))
    return out


def _restart_refusal(svc: Any, sid: str, rec: dict[str, Any]) -> str:
    """Why the cut reply is NOT carried on by itself (plain words), or "".
    BLOCKING (a row read, a log tail)."""
    from . import store

    row = store.get(svc.engine, sid)
    if row is None:
        drop_cut(svc.home, sid)
        return "its session is gone"
    if row.archived_at is not None:
        return WHY_ARCHIVED
    if getattr(row, "terminal_id", ""):
        return WHY_TERMINAL
    if not row.harness_session_id:
        return WHY_NOT_SAVED
    if rec.get("waiting"):
        return WHY_WAITING
    if rec.get("carried"):
        return WHY_CARRIED
    cut_at = _parse(rec.get("cut_at"))
    if cut_at is None or CLOCK() - cut_at > timedelta(days=CUT_KEEP_DAYS):
        return WHY_OLD
    st = read_limits(svc.home, sid)
    if isinstance(st.get("wait"), dict):
        return WHY_LIMIT
    if rec.get("limit") or st.get("card"):
        return WHY_LIMIT_CARD
    live = svc.live.get(sid)
    if live is not None and ((live.turn is not None and live.turn.running) or live.claim):
        return WHY_BUSY
    log_ = live.log if live is not None else None
    if log_ is not None and hasattr(log_, "flush"):
        with contextlib.suppress(Exception):
            log_.flush()
    if newest_turn(_tail_events(session_dir(svc.home, sid) / "events.jsonl")) != rec.get("turn"):
        return WHY_NEWER
    return ""


def _pool_full(svc: Any) -> bool:
    """v1.422.0 review: every session that is WORKING counts — a reply this
    pass just started has no process yet (its tool starts in the turn's own
    task), and counting only running processes let it start more replies than
    can run at once (the extra ones failed at once, their record used up)."""
    from .service import MAX_LIVE

    busy = [lv for lv in svc.live.values() if lv.busy]
    return len(busy) >= MAX_LIVE


async def _publish(platform: Any, event_type: str, sid: str, payload: dict[str, Any]) -> None:
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return
    from . import news as NEWS

    try:
        await bus.publish(event_type, {**payload, "thread_id": sid, "surface": NEWS.SURFACE},
                          session_id=NEWS.session_id_for(sid))
    except Exception:  # noqa: BLE001 — news never fails the carry-on
        log.warning("could not tell the bell about a Code-view session", exc_info=True)


async def _start(svc: Any, sid: str, text: str, carry: dict[str, Any]) -> Any:
    from .service import Refused

    if not carry.get("pressed") and _emergency_stop(svc.platform):
        # v1.422.0 review: a pass reads Emergency stop once at its top, and
        # the user may turn it on while the pass goes on (each start is
        # awaited): every start Iron Jarvis makes BY ITSELF reads it again.
        raise Refused(WHY_STOP[:1].upper() + WHY_STOP[1:] + ".")
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    return await svc.start_turn(sid, text, turn_id, carry=carry)


def _emergency_stop(platform: Any) -> bool:
    from ..agents.orchestrator import emergency_stop_on

    return emergency_stop_on(getattr(platform, "config", None))


async def carry_on_after_restart(svc: Any) -> list[str]:
    """THE BOOT'S ONE PASS: each cut reply the boot found (``cut.json`` with
    no decision yet) is carried on ONCE, by itself, through the tool's own
    resume — or offered (its record says why not, and the page shows
    Continue). Returns the session ids carried on. Never raises for one
    session."""
    from .service import Refused

    cuts = await asyncio.to_thread(_pending_cuts, svc.home)
    if not cuts:
        return []
    if _emergency_stop(svc.platform):
        # Nothing carries on by itself while Emergency stop is on: the records
        # stay undecided for a later start; the page still offers Continue.
        log.info("not carrying on %d cut-off Code-view repl(ies): Emergency stop is on", len(cuts))
        return []
    from ..core.events import EventType

    carried: list[str] = []
    for sid, rec in cuts:
        turn = str(rec.get("turn") or "")
        try:
            why = await asyncio.to_thread(_restart_refusal, svc, sid, rec)
            if not why and _pool_full(svc):
                why = WHY_POOL
            if why:
                log.info("not carrying on the cut-off Code-view reply %s: %s", turn, why)
                await asyncio.to_thread(_amend_cut, svc.home, sid, turn, auto="offered", why_not=why)
                continue
            try:
                new = await _start(svc, sid, CONTINUE_AFTER_RESTART, {"why": "restart", "from": turn})
            except Refused as r:
                stop_now = _emergency_stop(svc.platform)
                await asyncio.to_thread(_amend_cut, svc.home, sid, turn, auto="offered",
                                        why_not=WHY_STOP if stop_now else clip(r.sentence, 300))
                continue
            await asyncio.to_thread(_amend_cut, svc.home, sid, turn, auto="carried", to=new.id)
            live = svc.live.get(sid)
            await _publish(svc.platform, EventType.BUILD_RESUMED, sid, {
                "title": getattr(live, "title", "") or "",
                "turn_id": new.id,
                "resumed_from": turn,
                "reason": "restart",
                "harness": getattr(live, "harness", "") or "",
            })
            carried.append(sid)
        except Exception:  # noqa: BLE001 — one session never stops the pass
            log.warning("carrying on a cut-off Code-view reply failed", exc_info=True)
    return carried


async def continue_cut(svc: Any, sid: str) -> dict[str, Any]:
    """The "Continue" press on a reply a restart cut off: the same hidden
    message, started by a PERSON (so Emergency stop does not refuse it, and a
    card that was open or a reply already carried on once does not either —
    the press is the consent)."""
    from .service import Refused

    row = await svc._row(sid)
    rec = await asyncio.to_thread(read_cut, svc.home, sid)
    turn = str(rec.get("turn") or "")
    live = svc.live.get(sid)
    events = await _newest_events(svc, sid, live)
    if not turn or rec.get("auto") == "carried" or newest_turn(events) != turn:
        raise Refused(NOTHING_CUT, 409)
    if row.archived_at is not None:
        from . import words as W

        raise Refused(W.ARCHIVED)
    new = await _start(svc, sid, CONTINUE_AFTER_RESTART, {"why": "restart", "from": turn, "pressed": True})
    await asyncio.to_thread(_amend_cut, svc.home, sid, turn, auto="carried", to=new.id, pressed=True)
    return {"ok": True, "turn_id": new.id}


async def _newest_events(svc: Any, sid: str, live: Any) -> list[dict[str, Any]]:
    log_ = live.log if live is not None else None
    if log_ is not None and hasattr(log_, "served"):
        got = log_.served(50)
        if got is not None:
            return list(got["events"])
    if log_ is not None:
        await asyncio.to_thread(log_.flush)
    return await asyncio.to_thread(_tail_events, session_dir(svc.home, sid) / "events.jsonl")


# --------------------------------------------------------------------------- #
# limits (B5)
# --------------------------------------------------------------------------- #
def reset_from(driver: Any, ev: dict[str, Any] | None = None) -> datetime | None:
    """When the limit the tool just hit resets, best known first: the tool's
    own limit record (Claude Code's ``rate_limit_event`` / the message's
    "resets 3:45pm (zone)"), else the usage windows it reported that are full
    (Codex's ``rateLimits``: the LATEST reset among windows at 100%), else
    the words of the error itself ("try again at 3:45 PM"). None = not known."""
    lim = getattr(driver, "limit", None) if driver is not None else None
    at = getattr(lim, "reset_at", None)
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    windows = getattr(driver, "seen_windows", None) if driver is not None else None
    full = []
    now = CLOCK()
    for w in windows or []:
        if not isinstance(w, dict):
            continue
        pct = w.get("percent")
        when = _parse(w.get("resets_at"))
        # v1.422.0 review: a window whose reset already PASSED is an older
        # report, not this limit's reset — it never hides the reset the
        # message itself names (the reply would wait for nothing).
        if (isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct >= 100
                and when is not None and when > now):
            full.append(when)
    if full:
        return max(full)
    text = " ".join(str((ev or {}).get(k) or "") for k in ("technical", "title", "fix"))
    return _reset_in_words(text)


def _reset_in_words(text: str) -> datetime | None:
    import re

    from ..terminals.limit_state import detect_limit, parse_reset

    found = detect_limit(text)
    if found is not None and found.reset_at is not None:
        return found.reset_at
    m = re.search(r"(?:try again|resets?|available again)\s+((?:at|in|on)\s+[^.\n]{1,60})", text, re.I)
    return parse_reset(m.group(1)) if m else None


def _served(events: list[dict[str, Any]]) -> bool:
    """Did the account answer any work in this turn before its limit?"""
    for ev in events:
        t = ev.get("type")
        if t == "error" and ev.get("fix_kind") == "account_limit":
            return False
        if t in ("text", "text_done", "thinking", "tool"):
            return True
    return False


def _ip(platform: Any) -> Any:
    return getattr(platform, "iron_proxy", None)


def _arm(platform: Any, harness: str, account: str, account_name: str, reset: datetime | None,
         chain: int, turn: str, now: datetime) -> dict[str, Any] | None:
    """The wait a limit ARMS BY ITSELF, or None (the card waits for a press):
    the setting off, the chain used up, or nothing to wait for (no reset
    known and no group to move within)."""
    from ..harness import build_accounts as BA

    cfg = getattr(platform, "config", None)
    if not bool(getattr(cfg, "build_auto_resume", True)) or chain >= MAX_AUTO_IN_A_ROW:
        return None
    group = ""
    if harness in MOVABLE and account:
        group = BA.group_partners(_ip(platform), getattr(cfg, "account_groups", None), account)
    future = reset if reset is not None and reset > now else None
    if not group and future is None:
        return None
    wait = {
        "id": uuid.uuid4().hex[:12],
        "account": account,
        "account_name": account_name,
        "resets_at": _iso(future),
        "armed_at": _iso(now),
        "next_check_at": _iso(now if group else future),
        "auto": True,
        "turn": turn,
    }
    if group:
        wait["group"] = group
    return wait


def record_turn_end(svc: Any, live: Any, turn: Any, stop: str) -> dict[str, Any] | None:
    """BLOCKING (a file, a cached snapshot): what a turn's end means for the
    session's limit record — written BEFORE the turn's ``done`` reaches
    anyone, so the page's re-read right after shows the waiting card. Returns
    the new state when this turn ended on a limit (the caller tells the bell
    and wakes the loop), else None."""
    from .store import account_record, get

    home, sid = svc.home, live.sid
    events = list(getattr(turn, "events", []) or [])
    served = _served(events)
    with _LOCK:
        st = read_limits(home, sid)
        chain = 0 if served else int(st.get("chain") or 0)
        if not getattr(turn, "account_limit", False):
            if served or stop == "completed":
                _write(_path(home, sid, LIMITS_FILE), {})
            elif st:
                st["chain"] = chain
                _write(_path(home, sid, LIMITS_FILE), st)
            return None
        row = get(svc.engine, sid)
        acc = account_record(row) if row is not None else {}
        account = str(getattr(live, "account", "") or "")
        name = str(acc.get("title") or "") if acc.get("id") == account or not account else ""
        reset = getattr(live, "limit_reset", None)
        now = CLOCK()
        card = {
            "account": account,
            "account_name": name,
            "resets_at": _iso(reset) if isinstance(reset, datetime) and reset > now else "",
            "turn": getattr(turn, "id", ""),
            "at": _iso(now),
            "harness": getattr(live, "harness", ""),
        }
        wait = _arm(svc.platform, card["harness"], account, name, reset if isinstance(reset, datetime) else None,
                    chain, card["turn"], now)
        if wait is None and chain >= MAX_AUTO_IN_A_ROW:
            card["note"] = CHAIN_NOTE.format(n=chain)
        state = {"card": card, "wait": wait, "chain": chain}
        _write(_path(home, sid, LIMITS_FILE), state)
        return state


async def turn_ended(svc: Any, live: Any, turn: Any, stop: str) -> None:
    """The service's hook (``_end_turn``, before the done): record, then —
    once the done is out (a task) — tell the bell and wake the loop. A
    grouped wait is said by the loop instead (once, ``told``), so a move and
    a limit are never both announced. Never raises."""
    try:
        state = await asyncio.to_thread(record_turn_end, svc, live, turn, stop)
    except Exception:  # noqa: BLE001 — the turn ends regardless
        log.warning("could not record a Code-view session's limit", exc_info=True)
        return
    if not state:
        return
    wait = state.get("wait")
    if wait is not None and wait.get("group"):
        wake()
        return

    async def tell() -> None:
        await _announce_limit(svc, live.sid, state["card"], waiting=wait is not None)
        wake()

    from ..harness.stall import keep

    with contextlib.suppress(RuntimeError):
        keep(asyncio.ensure_future(tell()))


async def _announce_limit(svc: Any, sid: str, card: dict[str, Any], *, waiting: bool) -> None:
    from ..core.events import EventType
    from . import words as W

    live = svc.live.get(sid)
    harness = str(card.get("harness") or getattr(live, "harness", "") or "")
    await _publish(svc.platform, EventType.BUILD_ACCOUNT_LIMIT, sid, {
        "title": getattr(live, "title", "") or "",
        "account_name": card.get("account_name") or "",
        "vendor": W.label(harness) if harness else "",
        "kind": "usage",
        "resets_at": card.get("resets_at") or None,
        "waiting": bool(waiting),
        "harness": harness,
    })


def on_user_message(home: Any, sid: str) -> None:
    """BLOCKING: the user's own message ends a wait (and supersedes the limit
    card and a cut record — they belong to an older turn). The chain stays:
    it ends only when an account serves."""
    with _LOCK:
        st = read_limits(home, sid)
        if st.get("wait") or st.get("card"):
            st.pop("wait", None)
            st.pop("card", None)
            _write(_path(home, sid, LIMITS_FILE), {k: v for k, v in st.items() if v})
        _write(_path(home, sid, CUT_FILE), {})


def take_wait(home: Any, sid: str, wait_id: str) -> dict[str, Any] | None:
    """ATOMIC: take the wait ``wait_id`` (a Cancel, a new message or another
    pass that got there first leaves nothing to take). The card stays."""
    with _LOCK:
        st = read_limits(home, sid)
        wait = st.get("wait")
        if not isinstance(wait, dict) or wait.get("id") != wait_id:
            return None
        st["wait"] = None
        _write(_path(home, sid, LIMITS_FILE), st)
        return wait


def _amend_wait(home: Any, sid: str, wait_id: str, changes: dict[str, Any] | None) -> bool:
    """Change (or, None, end) the wait ``wait_id`` — only that one."""
    with _LOCK:
        st = read_limits(home, sid)
        wait = st.get("wait")
        if not isinstance(wait, dict) or wait.get("id") != wait_id:
            return False
        st["wait"] = None if changes is None else {**wait, **changes}
        _write(_path(home, sid, LIMITS_FILE), st)
        return True


def _put_back(home: Any, sid: str, wait: dict[str, Any], **changes: Any) -> None:
    """A wait taken for a carry-on that could not start goes back (unless a
    newer wait or a message replaced it meanwhile)."""
    with _LOCK:
        st = read_limits(home, sid)
        if st.get("wait") is not None or not st.get("card"):
            return
        st["wait"] = {**wait, **changes}
        _write(_path(home, sid, LIMITS_FILE), st)


def _note_card(home: Any, sid: str, note: str) -> None:
    """The limit card's own note (only while the card is there)."""
    with _LOCK:
        st = read_limits(home, sid)
        if not isinstance(st.get("card"), dict):
            return
        st["card"]["note"] = note
        _write(_path(home, sid, LIMITS_FILE), st)


def _bump_chain(home: Any, sid: str) -> None:
    with _LOCK:
        st = read_limits(home, sid)
        st["chain"] = int(st.get("chain") or 0) + 1
        _write(_path(home, sid, LIMITS_FILE), st)


def cancel_wait(home: Any, sid: str) -> bool:
    """BLOCKING: Cancel — the wait ends; the card (and its press) stays."""
    with _LOCK:
        st = read_limits(home, sid)
        if not st.get("wait"):
            return False
        st["wait"] = None
        _write(_path(home, sid, LIMITS_FILE), st)
        return True


def _all_waits(home: Any) -> list[tuple[str, dict[str, Any]]]:
    out = []
    try:
        paths = list((Path(home) / "hosted").glob(f"*/{LIMITS_FILE}"))
    except OSError:
        return out
    for p in paths[:2000]:
        st = _read(p)
        if isinstance(st.get("wait"), dict):
            out.append((p.parent.name, st))
    return out


def _parked_until(platform: Any, account: str, now: datetime) -> datetime | None:
    """Iron-Proxy's cached word that the account is still parked past ``now``
    (when it parks one after a limit), else None. Cached reads only."""
    from ..harness import build_accounts as BA

    if not account:
        return None
    st = BA.state_of(_ip(platform), account)
    if st.get("status") != "parked":
        return None
    until = _parse(st.get("parkedUntil"))
    return until if until is not None and until > now else None


async def resume_due(svc: Any, *, now: datetime | None = None) -> list[str]:
    """ONE pass of the loop over every session's armed wait: a grouped wait
    looks for another account of its group at once and on every pass (a
    move: :func:`_move`); a wait whose reset passed carries the reply on with
    the same account (unless Iron-Proxy's cached snapshot says it is parked
    for longer — then the next look is then). Skipped while the session
    works, is held or waits on a card; a wait for an account the session no
    longer runs on, or on an archived session, ends. Nothing while Emergency
    stop is on (the waits stay). Returns the session ids carried on."""
    from ..core.events import EventType
    from ..harness import build_accounts as BA
    from . import store
    from .service import Refused

    when = now or CLOCK()
    waits = await asyncio.to_thread(_all_waits, svc.home)
    if not waits or getattr(svc, "_closing", False) or _emergency_stop(svc.platform):
        return []
    cfg = getattr(svc.platform, "config", None)
    groups = getattr(cfg, "account_groups", None) or {}
    auto_on = bool(getattr(cfg, "build_auto_resume", True))
    done: list[str] = []
    for sid, st in waits:
        wait = st["wait"]
        wid = str(wait.get("id") or "")
        try:
            if not auto_on and wait.get("auto"):
                # v1.422.0 review: "Carry on by itself after an account's
                # limit" was turned OFF after this wait was armed. OFF means
                # nothing carries on by itself (a move included): the wait
                # ends; the card and its press stay.
                await asyncio.to_thread(_amend_wait, svc.home, sid, wid, None)
                continue
            row = await asyncio.to_thread(store.get, svc.engine, sid)
            if row is None or row.archived_at is not None or str(row.account or "") != str(wait.get("account") or ""):
                await asyncio.to_thread(_amend_wait, svc.home, sid, wid, None)
                continue
            live = svc.live.get(sid)
            if live is not None and ((live.turn is not None and live.turn.running) or live.claim or live.asks):
                continue
            chain = int(st.get("chain") or 0)
            account = str(wait.get("account") or "")
            if wait.get("group") and row.harness in MOVABLE and chain < MAX_AUTO_IN_A_ROW:
                # v1.422.0 review: a move STOPS the tool's process (a tree
                # kill); background work still running in it (a dev server, a
                # long test run, a helper agent) would die with it. Like the
                # reaper, the pool and a restart (v1.417.0), a move Iron Jarvis
                # makes by itself waits for that work (the wait stays).
                background = live is not None and live.keeps_working(svc.clock())
                target = None if background else BA.takeover(_ip(svc.platform), groups, account)
                if target is not None and target.get("id") not in (wait.get("tried") or []):
                    outcome = await _move(svc, sid, row, wait, target)
                    if outcome in ("moved", "raced"):
                        if outcome == "moved":
                            done.append(sid)
                        continue
                if not wait.get("told"):
                    # Nothing in the group took over: the limit is said now, once.
                    if await asyncio.to_thread(_amend_wait, svc.home, sid, wid, {"told": True}):
                        await _announce_limit(svc, sid, st.get("card") or {}, waiting=bool(wait.get("resets_at")))
                        wait = {**wait, "told": True}
            reset = _parse(wait.get("resets_at"))
            if reset is None:
                continue  # a grouped wait with no reset: it only looks for a move
            # The reset decides — a grouped wait's next look is NOW (for a
            # move), never a carry-on with an account still at its limit.
            nxt = _parse(wait.get("next_check_at"))
            due_at = max(reset, nxt) if nxt is not None else reset
            if due_at > when:
                continue
            parked = _parked_until(svc.platform, account, when)
            if parked is not None:
                await asyncio.to_thread(_amend_wait, svc.home, sid, wid,
                                        {"next_check_at": _iso(parked), "resets_at": _iso(parked)})
                continue
            if _emergency_stop(svc.platform):
                continue  # v1.422.0 review: turned on during this pass — the wait stays
            taken = await asyncio.to_thread(take_wait, svc.home, sid, wid)
            if taken is None:
                continue  # cancelled, a message, or another pass got there first
            # Counted BEFORE the start: a carry-on the account refuses at once
            # ends (and records its limit) before a count after it would land.
            await asyncio.to_thread(_bump_chain, svc.home, sid)
            try:
                new = await _start(svc, sid, CONTINUE_AFTER_RESET, {
                    "why": "reset", "account": account, "account_name": str(wait.get("account_name") or ""),
                })
            except Refused as r:
                tries = int(taken.get("tries") or 0) + 1
                if tries < MAX_TRIES and r.sentence not in _busy_words():
                    await asyncio.to_thread(_put_back, svc.home, sid, taken, tries=tries,
                                            next_check_at=_iso(when + timedelta(seconds=RECHECK_S)))
                elif r.sentence not in _busy_words():
                    # v1.422.0 review: the wait gives up — said on the card,
                    # never a "waiting" line that simply vanishes.
                    await asyncio.to_thread(_note_card, svc.home, sid, GAVE_UP_NOTE.format(why=clip(r.sentence, 300)))
                continue
            await _publish(svc.platform, EventType.BUILD_RESUMED, sid, {
                "title": getattr(svc.live.get(sid), "title", "") or "",
                "turn_id": new.id,
                "account": account,
                "account_name": str(wait.get("account_name") or ""),
                "reason": "reset",
                "harness": row.harness,
            })
            done.append(sid)
        except Exception:  # noqa: BLE001 — one session never stops the pass
            log.warning("a Code-view session's wait could not be looked at", exc_info=True)
    return done


def _busy_words() -> tuple[str, ...]:
    from . import words as W

    return (W.BUSY, W.RUNNING, W.ARCHIVED)


async def _move(svc: Any, sid: str, row: Any, wait: dict[str, Any], target: dict[str, Any]) -> str:
    """Carry the reply on with ``target`` (another account of the SAME group
    and vendor, usable now) through Continue on another account — the copy,
    the account change and Iron-Proxy being told are that path's — then the
    hidden carry-on message. The wait is TAKEN first (a Cancel or a message
    that landed meanwhile moves nothing). ``moved`` | ``raced`` | ``failed``
    (the move was refused: the wait goes back, that account remembered as
    tried)."""
    from ..core.events import EventType
    from .service import Refused

    wid = str(wait.get("id") or "")
    if _emergency_stop(svc.platform):
        return "raced"  # v1.422.0 review: turned on during this pass — the wait stays
    taken = await asyncio.to_thread(take_wait, svc.home, sid, wid)
    if taken is None:
        return "raced"
    frm = str(wait.get("account_name") or wait.get("account") or "")
    to_id = str(target.get("id") or "")
    to_name = str(target.get("name") or to_id)
    group = str(target.get("group") or wait.get("group") or "")
    try:
        await svc.continue_on(sid, to_id, note=MOVED_NOTE.format(frm=frm, to=to_name, group=group))
    except Refused as r:
        log.info("moving Code-view session %s within its group was refused: %s", sid, r.sentence)
        tried = list(taken.get("tried") or []) + [to_id]
        await asyncio.to_thread(_put_back, svc.home, sid, taken, tried=tried)
        return "failed"
    # The session is on the new account now: the old card and wait are not
    # its. Counted BEFORE the start (see :func:`resume_due`).
    await asyncio.to_thread(_bump_chain, svc.home, sid)
    try:
        new = await _start(svc, sid, CONTINUE_AFTER_MOVE, {
            "why": "move", "from_account": frm, "to_account": to_name, "group": group,
        })
    except Refused:
        return "raced"  # the user's own message got there first: it runs on the new account
    await _publish(svc.platform, EventType.BUILD_RESUMED, sid, {
        "title": getattr(svc.live.get(sid), "title", "") or "",
        "turn_id": new.id,
        "account": to_id,
        "account_name": to_name,
        "moved_from": str(wait.get("account") or ""),
        "moved_from_name": frm,
        "group": group,
        "harness": row.harness,
    })
    return "moved"


# --------------------------------------------------------------------------- #
# what the page reads
# --------------------------------------------------------------------------- #
def view_facts(home: Any, sid: str, row: Any, events: list[dict[str, Any]], *, running: bool) -> dict[str, Any]:
    """BLOCKING: ``cut`` (the newest reply was cut off by a restart and was
    not carried on: the page's Continue press, and why it did not carry on by
    itself) and ``account_wait`` (the reply waits for its account's reset, or
    looks for another account of its group, by itself — the card says until
    when, with Cancel). Each key only when it applies."""
    out: dict[str, Any] = {}
    rec = read_cut(home, sid)
    if rec.get("turn") and not running and rec.get("auto") != "carried" and newest_turn(events) == rec["turn"]:
        out["cut"] = {
            "turn": rec["turn"],
            "resumable": bool(getattr(row, "harness_session_id", "")),
            "why_not": str(rec.get("why_not") or ""),
            "waiting": bool(rec.get("waiting")),
        }
    st = read_limits(home, sid)
    wait = st.get("wait")
    if isinstance(wait, dict) and str(wait.get("account") or "") == str(getattr(row, "account", "") or ""):
        out["account_wait"] = {
            "account": wait.get("account") or "",
            "account_name": wait.get("account_name") or "",
            "resets_at": wait.get("resets_at") or None,
            "group": wait.get("group") or "",
            "armed_at": wait.get("armed_at") or "",
        }
    card = st.get("card")
    if isinstance(card, dict) and card.get("note"):
        out["account_limit_note"] = str(card["note"])
    return out


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def wake() -> None:
    """Make the loop look now (a wait was armed). Thread-safe; a no-op when
    no loop runs."""
    loop, event = _WAKE.get("loop"), _WAKE.get("event")
    if loop is None or event is None:
        return
    with contextlib.suppress(RuntimeError):
        if loop.is_running():
            loop.call_soon_threadsafe(event.set)


def _service(platform: Any) -> Any:
    from .service import service_for

    return service_for(platform)


def _anything(home: Any, name: str) -> bool:
    try:
        return any((Path(home) / "hosted").glob(f"*/{name}"))
    except OSError:
        return False


async def carry_loop(platform: Any, on_tick: Any) -> None:
    """The lifespan loop: FIRST the restart pass (once), then the waits every
    :data:`TICK_S` and at once when one is armed. Each pass reports ITS OWN
    outcome (``on_tick(ok, exc)``). The Code view's service is made only when
    a file asks for it (a PC that never used the Code view costs one glob)."""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _WAKE.update(loop=loop, event=event)
    home = getattr(getattr(platform, "config", None), "home", None)
    try:
        try:
            if home is not None and await asyncio.to_thread(_anything, home, CUT_FILE):
                carried = await carry_on_after_restart(_service(platform))
                if carried:
                    log.info("carried on %d Code-view repl(ies) a restart cut off", len(carried))
            on_tick(True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
            on_tick(False, exc)
        while True:
            event.clear()
            try:
                if home is not None and await asyncio.to_thread(_anything, home, LIMITS_FILE):
                    await resume_due(_service(platform))
                on_tick(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                on_tick(False, exc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=TICK_S)
    finally:
        if _WAKE.get("event") is event:
            _WAKE.clear()


def stop_unattended_turns(platform: Any) -> list[str]:
    """Emergency stop's half for Build › Code (v1.422.0 review — Chat › Code's
    rule since v1.398.0, ``harness/night.stop_unattended_turns``): every reply
    Iron Jarvis carried on BY ITSELF (after a restart, an account's reset, a
    move within a group) is stopped, the way the user's Stop stops it (so it
    is never read as cut, never carried on again). A reply the user started,
    and a Continue the user pressed, keep going. Thread-safe (the stop runs on
    the turn's own loop); never makes the Code view's service; returns
    ``build_app:<turn id>`` for each; never raises."""
    svc = getattr(platform, "hosted", None)
    if svc is None:
        return []
    stopped: list[str] = []
    try:
        lives = list(svc.live.items())
    except RuntimeError:  # the loop changed the map mid-read: read it again
        lives = list(dict(svc.live).items())
    for sid, live in lives:
        try:
            turn = live.turn
            carry = getattr(turn, "carry", None) if turn is not None else None
            if turn is None or not turn.running or not isinstance(carry, dict) or carry.get("pressed"):
                continue
            task = turn.task
            if task is None:
                continue
            asyncio.run_coroutine_threadsafe(svc.interrupt(sid), task.get_loop())
            stopped.append(f"build_app:{turn.id}")
        except Exception:  # noqa: BLE001 — one turn never keeps the rest going
            log.warning("Emergency stop could not stop a Code-view carry-on", exc_info=True)
    return stopped


def reconcile_keep(keep: Collection[str] | None) -> set[str]:
    return {str(k) for k in (keep or ()) if str(k)}
