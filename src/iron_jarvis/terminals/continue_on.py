"""Continue a Claude Code conversation on the NEXT Iron-Proxy account (v1.303.0).

A running CLI cannot change account (CLAUDE.md v1.302.0), so "continue on the
next account" is: tell Iron-Proxy this account hit its limit, pick the next
one, carry the conversation file over, and start ``claude --resume <id>`` in a
NEW pane on that account. The old pane is left exactly as it is.

Where the conversation lives (Claude Code 2.1.288, verified): ``<config
home>/projects/<folder>/<session id>.jsonl`` plus an optional ``<session id>/``
folder beside it. The folder name is Claude Code's encoding of the cwd (every
non-alphanumeric -> ``-``; long paths are truncated/hashed), so it is NEVER
recomputed here: the file is FOUND by glob and copied into the next account's
folder of the SAME name. A copy there is found by ``claude --resume <id>`` run
from that cwd. Whether the next account's API accepts the other account's
transcript is NOT proven, so the answer always says what was done and how to
start fresh.

WHICH conversation — never "the newest by mtime" (another pane, another
project, a background run could own it):

1. a pane THIS app started Claude in records its id (``claude --session-id
   <uuid>`` / ``claude --resume <id>``); that file is carried when it was
   still being written around the limit (its last record is no older than
   the limit minus :data:`RECENT_S`) — a pane whose user ``/clear``-ed or
   ``/resume``-d elsewhere falls through to (2);
2. otherwise ONLY a session whose LAST record is Claude Code's own limit
   record (``isApiErrorMessage`` + ``error: "rate_limit"``) for this pane's
   cwd, written no earlier than the limit minus :data:`RECENT_S`. Exactly one
   is carried; several mean "none" (said so); none means a fresh start.

Each candidate's LAST record is read from a bounded tail of the file.

THE RULES:

* Iron-Proxy is handed ONLY the matched limit line (``limit_state``) — never
  the rest of the scrollback, which holds the user's prompts.
* The copy never overwrites (a same-named file that differs → nothing is
  written and Claude starts fresh), never touches the source, and every
  destination must resolve under the NEXT account's home.
* Same provider only; never this PC's login implicitly — a pane that is not on
  an Iron-Proxy account is refused in one sentence.

Everything here BLOCKS (loopback HTTP, file copies): the route is a sync
``def`` on the threadpool.
"""

from __future__ import annotations

import filecmp
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..iron_proxy import accounts as _acc
from . import pane_accounts as _pa

log = logging.getLogger(__name__)

PROVIDER = "anthropic"

THIS_PC = (
    "This pane runs on this PC's login — open a pane on an Iron-Proxy account "
    "from Launch to use account switching."
)
NO_OTHER = (
    "There is no other Claude account in Iron-Proxy to continue on — add one on "
    "the Connections page (Iron-Proxy card)."
)

class ContinueRefused(Exception):
    def __init__(self, sentence: str, status: int = 409) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status


def encode_cwd(cwd: str) -> str:
    """Claude Code's folder name for a cwd: every non-alphanumeric -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


#: A conversation counts as the one that hit the limit when its last record is
#: no older than the limit's first sighting minus this.
RECENT_S = 120.0

#: How much of a conversation file's END is read to find its last record.
_TAIL_BYTES = 256 * 1024

#: A Claude Code session id (a UUID) — the only shape typed into a shell.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SEVERAL = (
    "Several conversations hit the limit at once, so none was carried — use /resume in "
    "the new pane to pick one."
)


def is_session_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID.match(value))


def last_record(path: Path) -> dict[str, Any] | None:
    """The LAST JSON record of a ``.jsonl`` file, read from a bounded tail.
    None when there is none or it does not parse. Never raises."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            data = fh.read()
    except OSError:
        return None
    for raw in reversed(data.splitlines()):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            return None  # a cut first line, or a torn write: no claim
        return rec if isinstance(rec, dict) else None
    return None


def _record_time(rec: Mapping[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(rec.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _same_dir(a: Any, b: Any) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (TypeError, ValueError):
        return False


def is_limit_record(rec: Mapping[str, Any] | None) -> bool:
    return bool(rec) and rec.get("isApiErrorMessage") is True and rec.get("error") == "rate_limit"


@dataclass
class Found:
    path: Path | None
    #: Why nothing was carried, when it says something the user should know.
    note: str = ""
    #: The limit record's own reset (``quotaLimits.resetsAt``), when present.
    reset_at: datetime | None = None


def _reset_of(rec: Mapping[str, Any] | None) -> datetime | None:
    ql = rec.get("quotaLimits") if isinstance(rec, Mapping) else None
    at = ql.get("resetsAt") if isinstance(ql, Mapping) else None
    if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
        try:
            return datetime.fromtimestamp(float(at), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def newest_recorded(home: str, session_id: str | None) -> Path | None:
    """``<home>/projects/*/<id>.jsonl`` when it is still the NEWEST conversation
    in its folder (the user may have ``/clear``-ed into a new id since), else
    None. BLOCKING (a glob + one folder's stats)."""
    if not home or not is_session_id(session_id):
        return None
    try:
        for path in (Path(home) / "projects").glob(f"*/{session_id}.jsonl"):
            mine = path.stat().st_mtime
            newer = [
                p for p in path.parent.glob("*.jsonl")
                if p != path and is_session_id(p.stem) and p.stat().st_mtime > mine
            ]
            if not newer:
                return path
    except OSError:
        return None
    return None


def claude_home(session: Any) -> str:
    """The Claude Code home a pane's shell runs on: its Iron-Proxy account's
    recorded home; ``~/.claude`` for "this PC's login" (an inherited
    CLAUDE_CONFIG_DIR is removed there); else the daemon's own environment."""
    rec = (getattr(session, "accounts", None) or {}).get(PROVIDER) or {}
    if rec.get("home"):
        return str(rec["home"])
    if rec.get("source") == "default":
        return os.path.join(os.path.expanduser("~"), ".claude")
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def find_conversation(home: str, cwd: str, session_id: str | None, since: float) -> Found:
    """The conversation to carry from ``home`` (see the module docstring).
    ``since`` = when the limit was first seen (epoch seconds). BLOCKING."""
    projects = Path(home) / "projects" if home else None
    if projects is None or not projects.is_dir():
        return Found(None)
    floor = since - RECENT_S
    recorded = newest_recorded(home, session_id)
    if recorded is not None:
        rec = last_record(recorded)
        when = _record_time(rec) if rec else None
        if when is not None and when >= floor:
            return Found(recorded, reset_at=_reset_of(rec))
    hits: list[tuple[Path, dict[str, Any]]] = []
    try:
        folders = [f for f in projects.iterdir() if f.is_dir()]
    except OSError:
        return Found(None)
    for folder in folders:
        try:
            files = list(folder.glob("*.jsonl"))
        except OSError:
            continue
        for path in files:
            if not is_session_id(path.stem):
                continue
            try:
                if path.stat().st_mtime < floor:
                    continue  # its last record cannot be recent
            except OSError:
                continue
            rec = last_record(path)
            if not is_limit_record(rec) or not _same_dir(rec.get("cwd"), cwd):
                continue
            when = _record_time(rec)
            if when is not None and when >= floor:
                hits.append((path, rec))
    if len(hits) == 1:
        return Found(hits[0][0], reset_at=_reset_of(hits[0][1]))
    if len(hits) > 1:
        return Found(None, note=SEVERAL)
    return Found(None)


@dataclass
class Carried:
    resumed: bool
    session_id: str | None
    note: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_prefix(older: Path, newer: Path) -> bool:
    """Is ``older`` a byte-PREFIX of ``newer`` (and shorter)? A Claude Code
    conversation file is append-only JSONL, so an earlier copy of the SAME
    conversation is exactly a prefix of a later one; anything else diverged."""
    try:
        if older.stat().st_size >= newer.stat().st_size:
            return False
        with open(older, "rb") as a, open(newer, "rb") as b:
            while True:
                chunk = a.read(1 << 20)
                if not chunk:
                    return True
                if b.read(len(chunk)) != chunk:
                    return False
    except OSError:
        return False


class CarryBusy(PermissionError):
    """The destination conversation file is held open (a Claude still running
    in a pane on that account): Windows refuses to replace an open file."""


#: A sharing violation is often momentary: this many tries, this far apart
#: (well under a second in all).
_REPLACE_TRIES = 5
_REPLACE_PAUSE_S = 0.05


def _replace_keeping_old(source: Path, target: Path, trash: Path | None, rel: Path) -> None:
    """Replace ``target`` (an older prefix of ``source``) with ``source``: the
    old bytes go to Iron Jarvis's own trash FIRST (move, never delete — the
    v1.256.0 rule), then a temp copy beside the target is renamed onto it.
    A target held open is retried briefly, then :class:`CarryBusy`; the temp
    copy never outlives the attempt."""
    if trash is None:
        raise PermissionError("no trash folder to keep the older copy in")
    kept = trash / rel
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, kept)
    tmp = target.with_name(f".{target.name}.ij-carry-{os.getpid()}")
    try:
        shutil.copy2(source, tmp)
        for attempt in range(_REPLACE_TRIES):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt + 1 == _REPLACE_TRIES:
                    raise CarryBusy(str(target)) from None
                time.sleep(_REPLACE_PAUSE_S)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            log.debug("could not remove a carry temp file", exc_info=True)


def _copy_missing_tree(
    src: Path, dest: Path, root: Path, trash: Path | None = None, rel_base: Path = Path()
) -> None:
    """Copy ``src`` into ``dest`` file by file: a file that is new is copied,
    one whose destination is an older byte-prefix of it replaces that (the old
    copy kept in ``trash``), a DIVERGED one is never overwritten. Never follows
    a link; every destination confined to ``root``."""
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel = Path(dirpath).relative_to(src)
        target_dir = dest / rel
        if not _inside(target_dir, root):
            raise PermissionError("outside the next account's folder")
        target_dir.mkdir(parents=True, exist_ok=True)
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            source = Path(dirpath) / name
            target = target_dir / name
            if source.is_symlink():
                continue
            if not _inside(target, root):
                raise PermissionError("outside the next account's folder")
            if not target.exists():
                shutil.copy2(source, target)
            elif _is_prefix(target, source):
                try:
                    _replace_keeping_old(source, target, trash, rel_base / rel / name)
                except CarryBusy:
                    continue  # a side file in use: kept as it is (not the conversation)


def carry_over(
    source: Path | None,
    next_home: str,
    to_title: str,
    why_none: str = "",
    trash_root: Path | None = None,
) -> Carried:
    """Copy the conversation (``<id>.jsonl`` + its ``<id>/`` folder) into the
    next account's project folder of the SAME NAME (Claude Code's own encoding
    of the cwd — never recomputed). Never touches the source.

    The destination may already hold this conversation: ``--resume`` KEEPS the
    id, so A -> B -> A finds A holding an OLDER copy of the very conversation
    that grew on B. An older copy is a byte-prefix (the file is append-only):
    it is replaced, and the old bytes are MOVED into Iron Jarvis's own
    ``<trash_root>/<stamp>/claude-carry/`` first — never deleted. A copy that
    DIVERGED is never overwritten: nothing is written and Claude starts fresh,
    said so."""
    fresh_tail = f'Claude started a fresh conversation on "{to_title}".'
    if source is None:
        return Carried(
            False, None,
            why_none or (
                f'No conversation from this pane was found, so Claude started a fresh one on "{to_title}".'
            ),
        )
    sid = source.stem
    root = Path(next_home)
    dest_dir = root / "projects" / source.parent.name
    dest = dest_dir / source.name
    if not str(next_home or "").strip() or not _inside(dest, root):
        return Carried(False, None, f"The conversation could not be carried over (its folder is outside the next account's) — {fresh_tail}")
    trash = (
        Path(trash_root) / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") / "claude-carry"
        / source.parent.name
        if trash_root is not None else None
    )
    try:
        if dest.exists():
            if dest.resolve() == source.resolve() or filecmp.cmp(source, dest, shallow=False):
                pass  # already there, byte for byte
            elif trash is not None and _is_prefix(dest, Path(os.fspath(source))):
                # The same conversation, older: replaced, the old copy kept.
                _replace_keeping_old(Path(os.fspath(source)), dest, trash, Path(dest.name))
            else:
                return Carried(
                    False, None,
                    f'"{to_title}" already holds a different conversation with the same id, so '
                    f"nothing was copied — {fresh_tail}",
                )
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        sibling = source.with_suffix("")
        if sibling.is_dir() and not sibling.is_symlink():
            _copy_missing_tree(sibling, dest_dir / sid, root, trash, Path(sid))
    except CarryBusy:
        return Carried(
            False, None,
            f'The conversation file is open in another pane on "{to_title}" — close Claude there '
            f"and press Continue again. {fresh_tail}",
        )
    except OSError as exc:
        return Carried(False, None, f"The conversation could not be carried over ({type(exc).__name__}) — {fresh_tail}")
    return Carried(
        True, sid,
        f'Carried your conversation over to "{to_title}". If Claude cannot continue it there, '
        "type /clear in the new pane to start fresh.",
    )


@dataclass
class Plan:
    from_rec: dict[str, Any]
    lease: _acc.Lease
    parked: bool
    told: str
    carried: Carried
    #: When the old account frees: the limit record's own reset, else the
    #: words on screen parsed.
    reset_at: datetime | None = None


def _usable(state: Mapping[str, Any]) -> bool:
    if state.get("status") in ("unauthenticated", "disabled"):
        return False
    return not _pa._parked_now(state)


def _own_choice(
    profiles: list[dict[str, Any]], states: Mapping[str, Any], current: str
) -> _acc.Lease | None:
    """The first usable OTHER Claude CLI account in Iron-Proxy's order."""
    spec = _pa.SPECS[PROVIDER]
    for prof in _others(profiles, current):
        st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), Mapping) else {}
        cli = prof.get("cli") if isinstance(prof.get("cli"), Mapping) else {}
        home = str(cli.get("home") or "").strip()
        if home and _usable(st):
            pid = str(prof.get("id"))
            return _acc.make_lease(
                spec, pid, str(prof.get("title") or pid), home, sorted(_pa.PANE_OWN_KEYS[PROVIDER])
            )
    return None


def _others(profiles: list[dict[str, Any]], current: str) -> list[dict[str, Any]]:
    return sorted(
        (
            p for p in profiles
            if isinstance(p, Mapping) and p.get("provider") == PROVIDER
            and (p.get("lane") or "cli") == "cli" and p.get("enabled") is not False
            and p.get("id") != current
        ),
        key=lambda p: p.get("order") or 0,
    )


def _none_free(profiles: list[dict[str, Any]], states: Mapping[str, Any], current: str) -> ContinueRefused:
    """No other account can take over: each other account BY KIND, with when
    it frees ("…is at its request limit until 3:05 PM")."""
    others = _others(profiles, current)
    if not others:
        return ContinueRefused(NO_OTHER)
    parts: list[str] = []
    for prof in others:
        st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), Mapping) else {}
        title = str(prof.get("title") or prof.get("id"))
        until = str(st.get("parkedUntil") or "")
        words, _secs = _acc._local_time(until) if until else ("", None)
        when = f" until {words}" if words and _pa._parked_now(st) else ""
        kind = _acc._account_kind(dict(st))
        if kind == "signin":
            parts.append(f'"{title}" needs to sign in again on {_acc._CONNECTIONS}')
        elif st.get("status") == "disabled":
            parts.append(f'"{title}" is turned off on the Iron-Proxy card')
        elif kind in _acc._WHAT:
            parts.append(f'"{title}" is {_acc._WHAT[kind]}{when}')
        else:
            parts.append(f'"{title}" is parked{when}')
    return ContinueRefused("No other Claude account can take over right now: " + "; ".join(parts) + ".")


FULL = "Build already has as many panes open as it can — close one, then try again."


def prepare(
    svc: Any, session: Any, *, room_for_pane: bool = True, trash_root: Path | None = None
) -> Plan:
    """Tell, find, pick, copy — in the order that refuses BEFORE anything is
    told or copied: this PC's login, Iron-Proxy off/unavailable, no room for a
    pane, no other usable account. BLOCKING. Raises :class:`ContinueRefused`
    with one sentence; nothing is opened by it."""
    rec = (getattr(session, "accounts", None) or {}).get(PROVIDER) or {}
    current = str(rec.get("id") or "")
    if rec.get("source") != "iron-proxy" or not current:
        raise ContinueRefused(THIS_PC)
    if not room_for_pane:
        raise ContinueRefused(FULL, 429)
    if svc is None or not getattr(svc, "enabled", False):
        raise ContinueRefused(_pa._OFF)
    try:
        client = svc.lease_client(timeout_s=_pa.LEASE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — IronProxyUnavailable and the rest
        raise ContinueRefused(_pa._unavailable_sentence(exc)) from None
    if client is None:
        raise ContinueRefused(_pa._OFF)
    title = str(rec.get("title") or current)

    # The accounts, read now: refuse before telling anyone when no OTHER
    # account could take over.
    try:
        profiles, states = client.profiles(), client.states()
    except Exception as exc:  # noqa: BLE001
        raise ContinueRefused(_pa._unavailable_sentence(exc)) from None
    profiles = [p for p in (profiles if isinstance(profiles, list) else []) if isinstance(p, dict)]
    states = states if isinstance(states, dict) else {}
    try:
        svc.note_accounts(profiles, states)
    except Exception:  # noqa: BLE001
        log.debug("iron-proxy snapshot refresh failed", exc_info=True)
    fallback = _own_choice(profiles, states, current)
    if fallback is None:
        raise _none_free(profiles, states, current)

    # Tell Iron-Proxy — ONLY the matched limit message (normalised so its own
    # classifier reads the kind and the reset; built from nothing else).
    parked = False
    limit = session.limit_info() if hasattr(session, "limit_info") else None
    found_limit = getattr(session, "_limit", None)
    if limit and found_limit is not None:
        try:
            answer = client.signal(current, text=found_limit.signal_text())
            parked = bool(isinstance(answer, dict) and answer.get("parked") is True)
            told = "" if parked else (
                f'Iron-Proxy did not mark "{title}" as limited from that message, so it may '
                "still offer it."
            )
        except Exception as exc:  # noqa: BLE001 — the user pressed the button: continue
            _acc._note("continue signal", exc)
            told = f'Iron-Proxy could not be told that "{title}" hit its limit.'
    else:
        told = f'No limit message was on screen, so Iron-Proxy was not told about "{title}".'

    # The next account: Iron-Proxy's pick, unless it hands back this one (it
    # declined to park it) or nothing — then the next usable one in its order.
    lease: _acc.Lease | None = None
    try:
        lease = _acc._lease_of(_pa.SPECS[PROVIDER], client.pick(PROVIDER, lane="cli"))
    except Exception as exc:  # noqa: BLE001 — sorted below
        code = str(getattr(exc, "code", "") or "")
        if code not in ("ALL_PROFILES_EXHAUSTED", "QUOTA_EXCEEDED", "AUTH_REQUIRED", "NO_PROFILE"):
            raise ContinueRefused(_pa._unavailable_sentence(exc)) from None
    if lease is None or lease.profile_id == current:
        lease = fallback

    # The conversation, in THIS account's folder — never another account's.
    since_iso = (limit or {}).get("since")
    try:
        since = datetime.fromisoformat(str(since_iso)).timestamp() if since_iso else None
    except ValueError:
        since = None
    if since is None:
        since = datetime.now(timezone.utc).timestamp()
    found = find_conversation(
        str(rec.get("home") or ""), session.cwd, getattr(session, "claude_session_id", None), since
    )
    carried = carry_over(found.path, lease.home, lease.title, found.note, trash_root)
    reset_at = found.reset_at or (found_limit.reset_at if found_limit is not None else None)
    return Plan(
        from_rec={"id": current, "title": title},
        lease=lease,
        parked=parked,
        told=told,
        carried=carried,
        reset_at=reset_at,
    )
