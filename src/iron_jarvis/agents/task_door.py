"""Another computer running Iron Jarvis can hand THIS one a task (v1.357.0, G61).

The readiness audit's finding: there was no way to connect a second Iron
Jarvis as a teammate. Remote agents were other systems, fleet nodes were model
servers, and Iron Jarvis exposed no task endpoint another Iron Jarvis could
register. This module is that endpoint's whole contract, and it is built from
pieces that already existed — nothing on the SENDING side is new except what
the reply needs:

* THE DOOR speaks exactly the ``http-task`` body another Iron Jarvis's
  :class:`~iron_jarvis.agents.remote.RemoteAgentRegistry` already sends
  (v1.285.0): ``{task, conversation_id?, history?, reply_to?}``. So the OTHER
  computer registers this one through its EXISTING remote-agent form —
  dialect ``http-task``, this computer's door address, this door's token —
  and "@desk do X" in its chat reaches this door.
* THE TOKEN is minted into the vault (``DOOR_TOKEN_SECRET``) and shown ONCE;
  turning the door on again ROTATES it (the old one stops at once); the check
  is constant-time (``auth.token_matches``) and FAIL-CLOSED (door off, no
  vault entry, an empty candidate, a vault error → refused). The route is
  exempt from the install bearer for exactly its own leaf
  (``auth.TASK_DOOR_PATH``) and verified in its handler.
* A TASK becomes a session through ``Orchestrator.create_session`` with the
  ONE origin string this module defines (``remote:<caller>``,
  :func:`remote_origin`) at LOW trust (a message from another machine may be
  read and answered, not change what the next run reads — the phone's
  posture). That origin is UNATTENDED: nobody at this computer answers its
  asks (it is not in ``runtime.ASKING_ORIGINS``, so an ask-tier tool takes the
  instant headless refusal), it runs in the background lane, and Emergency
  stop refuses and stops it (``orchestrator.emergency_stop_covers``). The door
  answers ``202 {"accepted": true}`` at once — the sender's room records
  "working, will report back".
* THE ANSWER COMES HOME: when the session ends — finished, failed, stopped,
  or cut off by a restart — :func:`send_reply` POSTs ``{conversation_id,
  message, kind: "done", files?}`` to the caller's ``reply_to`` (its OWN
  inbound door, ``/agents/remote/<name>/inbound``) with the caller's inbound
  token, which the caller sent for that purpose as ``reply_token`` beside
  ``reply_to`` (the one, backward-compatible addition to the body; only a
  sender talking to an Iron Jarvis door adds it). The reply key is kept in the
  vault per session (``REPLY_TOKEN_PREFIX``) and removed only once the other
  computer TOOK the answer (a 2xx) or refused it for good (v1.510.0, second
  review F3). Each attempt is bounded, masked, off the loop; a failed one is
  tried again on a fixed ladder (``REPLY_RETRY_AFTER_S``), then whenever that
  computer is next heard from (``retry_owed_soon``); every state is RECORDED
  on the session's summary and on the door's status, named (who, why).
* REACHABILITY is said, never opened: the packaged app listens on 127.0.0.1
  and admits loopback Hosts only, so the door's answer carries the same
  ``reachable: {host_allowed, note}`` sentence the v1.285.0 inbound door
  gives. This module never opens a bind or edits ``IRONJARVIS_HOST_ALLOWLIST``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.ids import utcnow
from ..core.logging import get_logger

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# the names, each spelled once
# --------------------------------------------------------------------------- #

#: The origin PREFIX a task from another computer is stamped with — the ONE
#: definition (``remote:<caller>``). It is not in ``runtime.ATTENDED_ORIGINS``
#: and not in ``runtime.ASKING_ORIGINS``, on purpose: see the module doc.
REMOTE_TASK_ORIGIN = "remote"

#: What the computer that sends tasks is called when the user names nothing.
DEFAULT_CALLER = "another computer"

#: The vault key holding the door's own token. The "." cannot appear in a
#: remote agent's name, so it can never collide with ``remote_agent_<name>``.
DOOR_TOKEN_SECRET = "task_door.token"

#: The vault key prefix for the caller's reply key, one per session.
REPLY_TOKEN_PREFIX = "task_door.reply."

#: The run option the reply address rides under (``Session.options_json``):
#: ``{"to", "conversation_id", "caller"}`` — never the key itself.
REPLY_OPTION = "remote_reply"

#: The door's state file under the home (no secret in it).
STATE_FILE = "task-door.json"

#: Tasks per minute before a 429 — a run each, so tighter than the inbound
#: door's 60 messages a minute.
TASK_RATE_MAX = 20
TASK_RATE_WINDOW_S = 60.0

#: v1.509.0 (second review F14): tries a minute from ONE source address,
#: taken BEFORE any token is read (the pairing doors' ``take_try`` rule), so
#: junk tokens from an address past its budget cost no database or vault read.
#: The task door: three times one computer's task budget (a Check, a task and
#: its retries fit). The message-back door (``/agents/remote/<name>/inbound``):
#: four times one remote's 60 messages a minute.
ADDRESS_TRIES_MAX = 60
INBOUND_ADDRESS_TRIES_MAX = 240
ADDRESS_WINDOW_S = 60.0
#: Addresses remembered at once (the oldest is dropped past it).
ADDRESSES_KEPT = 2048

#: The run option naming the pairing a task came from (``PeerRecord.id``),
#: so removing that pairing finds and stops its runs (v1.509.0, F5).
REMOTE_PEER_OPTION = "remote_peer"

#: The task text's cap — the inbound door's message cap, the same number.
TASK_MAX_CHARS = 12_000

#: The whole request body's cap (history rides along).
TASK_MAX_BODY_BYTES = 2 * 1024 * 1024

#: How much of the sender's conversation the session's task carries
#: (build-out R0.1). It is PLANNED by ``context.budget.plan_history``, the
#: planner both chat lanes use, never cut to its newest N rows: the newest
#: messages that fit are kept whole and the rest are counted and summed up in
#: one short list ("History is budgeted, never sliced", docs/rules/context.md).
#: The room is for the WHOLE task (the opening line, the task, the
#: conversation): a share of the window the run will be planned against
#: (:func:`planned_task_text` asks the run's own resolver), never more than
#: ``TASK_TOKENS_MAX``. The rest of that window stays the run's own: its
#: instructions, its tools, its steps and its answer. The run's planner
#: (``context.agent_window``) keeps the task whole and cuts it only as a last
#: resort, from the END, which is where the newest messages are: so the door
#: fits the conversation first.
TASK_WINDOW_SHARE = 0.25
TASK_TOKENS_MAX = 8_000

#: The line the sender's conversation opens with inside the task.
CONVERSATION_HEAD = "The conversation so far on that computer:"

#: The body field a sender adds when it did not send its whole conversation
#: (build-out R0.1 review): how many OLDER rows it held back (its own 80-row
#: tail, the chat page's and the phone's bounds). Sent only to a task door,
#: only when not 0; the door adds it to what it leaves out, so the count the
#: task states is the sender's, not only the rows that arrived. An older door
#: ignores it; an older sender does not send it (the count is then what
#: arrived).
HISTORY_HELD_BACK_FIELD = "history_held_back"
#: The largest count a door believes (anything past it is read as this).
HISTORY_HELD_BACK_MAX = 1_000_000

#: The reply's bound (the WHOLE call, not a per-phase httpx timeout).
REPLY_TIMEOUT_S = 20.0

#: v1.510.0 (second review F3): an answer that did not arrive is tried again
#: after these waits (the first try is when the run ends, so: now, +1 minute,
#: +5 minutes, +30 minutes), read through the injected clock
#: (:func:`now_for`) by the daemon's periodic pass (:func:`periodic_pass`).
REPLY_RETRY_AFTER_S: tuple[float, ...] = (60.0, 300.0, 1800.0)
#: Tries on the ladder; after the last the answer waits for that computer to
#: be heard from again (a task, a probe, a Check press: :func:`retry_owed_soon`).
REPLY_TRIES = 1 + len(REPLY_RETRY_AFTER_S)
#: How long an answer is kept for a computer that is never heard from again;
#: past it the key is removed and the run says the answer was not sent.
REPLY_OWED_FOR_S = 7 * 24 * 3600.0
#: Being heard from tries a computer's owed answers at most this often (two
#: computers each owing the other must not answer each other's tries in a loop).
HEARD_RETRY_EVERY_S = 60.0
#: Owed answers read per pass (newest first).
REPLY_OWED_SCAN = 200

#: Where an answer stands, kept INSIDE the run's reply spec (its options), so
#: it survives a restart and leaves with the spec when a rerun drops it:
#: ``{"state", "tries", "next_at"}``. "owed": a try is due at ``next_at``
#: (none = now); "waiting": the ladder is over, sent when heard from; "sent":
#: the other computer took it; "final": refused or let go, never sent again.
REPLY_OWED = "owed"
REPLY_WAITING = "waiting"
REPLY_SENT = "sent"
REPLY_FINAL = "final"

#: A refusal that no retry can change (a wrong or expired key, no message back,
#: an unknown conversation, too large, a bad body). 408 / 425 / 429 are "later".
_RETRY_LATER_4XX = frozenset({408, 425, 429})

#: The reply message cap: the receiving inbound door refuses longer (413).
REPLY_MESSAGE_CHARS = 12_000

#: Files the reply carries (what the run made on this computer).
REPLY_FILES_MAX = 5
REPLY_FILE_BYTES = 10 * 1024 * 1024
REPLY_FILES_TOTAL_BYTES = 25 * 1024 * 1024

#: The words a run's task opens with, so the run (and a person reading its
#: page) knows where the task came from.
TASK_INTRO = "A task sent from {caller}, another computer running Iron Jarvis:"

#: The door refuses while it is off — the sender's room shows this.
DOOR_OFF = (
    "This computer is not taking tasks from that one. Pair the two computers in "
    "Settings › Connections › Your other computers, or turn tasks on by hand in "
    "Agents, Your team, New & manage, Another computer running Jarvis."
)

_CALLER_OK = re.compile(r"[^A-Za-z0-9 _.\-]")

#: ``reply_to`` must be another Iron Jarvis's inbound door (any host, the
#: exact leaf) — a sender cannot point this computer's answer at an arbitrary
#: address on its network.
_REPLY_PATH = re.compile(r"^/agents/remote/[^/]+/inbound$")


def clean_caller(raw: object) -> str:
    """A safe name for the computer that sends tasks: letters, digits, space,
    ``_ . -``; runs of spaces collapsed; at most 40 characters; the default
    when nothing usable is left."""
    text = _CALLER_OK.sub(" ", str(raw or ""))
    text = " ".join(text.split())[:40].strip(" .-_")
    return text or DEFAULT_CALLER


def remote_origin(caller: object) -> str:
    """The origin a task from ``caller`` is stamped with: ``remote:<caller>``."""
    return f"{REMOTE_TASK_ORIGIN}:{clean_caller(caller)}"


def is_remote_task_origin(origin: object) -> bool:
    """Was this run sent by another computer running Iron Jarvis?"""
    return str(origin or "").startswith(f"{REMOTE_TASK_ORIGIN}:")


def _door_path() -> str:
    # Lazy, by import direction: the daemon layer owns the path (beside the
    # other exempt leaves), the agent layer only reads it.
    from ..daemon.auth import TASK_DOOR_PATH

    return TASK_DOOR_PATH


def is_task_door_url(url: object) -> bool:
    """Does ``url`` point at an Iron Jarvis task door? (Its path is exactly
    the door's leaf; any host — a tunnel or proxy keeps the path.)"""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return (parsed.path or "").rstrip("/") == _door_path()


def door_address(url: object) -> str:
    """The address to DIAL for ``url``: a task door's address with the
    trailing slashes of its path removed, anything else unchanged.

    v1.357.0 re-review: :func:`is_task_door_url` accepts ``…/task/`` (the
    form says "another Iron Jarvis" for it and message-back is turned on), but
    the install-bearer exemption is an EXACT match on ``TASK_DOOR_PATH``. Sent
    as typed, every task and every Test came back 401 from the install guard,
    and the sender was told "its token does not match — copy the token again",
    a loop no new token could end. The sender dials the exact leaf instead."""
    text = str(url or "").strip()
    if not is_task_door_url(text):
        return str(url or "")
    parsed = urlparse(text)
    return parsed._replace(path=(parsed.path or "").rstrip("/")).geturl()


def plain_address(host: object) -> str:
    """A source or stored host as one comparable spelling (v1.509.0, F6):
    an IP address without brackets, an IPv4-mapped IPv6 address as its IPv4
    (``::ffff:10.0.0.1`` is ``10.0.0.1``), anything else (a name) lower case.
    The port is never part of it. ``""`` for nothing."""
    import ipaddress

    text = str(host or "").strip().strip("[]")
    if not text:
        return ""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return text.lower()
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)


def address_host(address: object) -> str:
    """The :func:`plain_address` of a stored ``scheme://host:port``."""
    try:
        return plain_address(urlparse(str(address or "").strip()).hostname or "")
    except Exception:  # noqa: BLE001
        return ""


def came_from_elsewhere(source: object, address: object) -> bool:
    """Did a request from ``source`` (the TCP client's host) come from
    somewhere other than the pairing's stored ``address``? Only an IP can be
    compared like for like: a pairing kept under a NAME (the joining side
    keeps the address as the person typed it) or a source that cannot be read
    is never called "elsewhere" (said in the report: it cannot be told)."""
    import ipaddress

    seen = plain_address(source)
    kept = address_host(address)
    if not seen or not kept:
        return False
    try:
        ipaddress.ip_address(seen)
        ipaddress.ip_address(kept)
    except ValueError:
        return False
    return seen != kept


def valid_reply_to(url: object) -> bool:
    """Is ``url`` a place this computer may send an answer to — another Iron
    Jarvis's inbound door over http(s)? Anything else is refused at the door."""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    return bool(_REPLY_PATH.match(parsed.path or ""))


# --------------------------------------------------------------------------- #
# the door: state + token + rate
# --------------------------------------------------------------------------- #


class TaskDoor:
    """The door's state (``<home>/task-door.json``, no secret in it), its
    token (the vault), and its per-minute limit (memory).

    Every method that touches disk or the vault is BLOCKING — the routes hop
    them off the loop. Nothing here raises to a caller: a broken state file
    reads as "off" (the fail-closed direction)."""

    def __init__(self, home: str | Path, secrets: Any, clock: Any = None) -> None:
        self.home = Path(home)
        self.secrets = secrets
        self._lock = threading.Lock()
        #: The time source of both limits (injected by tests; never a sleep).
        self._clock = clock or time.monotonic
        #: Tasks per CALLER (v1.509.0, F14: one window per computer, not one
        #: shared by all of them).
        self._hits: dict[str, deque[float]] = {}
        #: Tries per source address, per door ("task" / "inbound").
        self._address_hits: dict[str, dict[str, deque[float]]] = {}

    # -- state ---------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.home / STATE_FILE

    def state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:  # noqa: BLE001 — junk reads as off
            log.warning("task door: state file unreadable; reading it as off")
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, state: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.state_path)

    def _update(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            state = self.state()
            state.update(fields)
            self._write(state)
            return state

    def enabled(self) -> bool:
        """On = the state says so AND the vault holds a token. Fail-closed."""
        if self.state().get("enabled") is not True:
            return False
        try:
            return bool(self.secrets.exists(DOOR_TOKEN_SECRET))
        except Exception:  # noqa: BLE001
            return False

    def caller(self) -> str:
        return clean_caller(self.state().get("caller"))

    def enable(self, *, caller: object = "", url: str = "") -> tuple[dict[str, Any], str]:
        """Mint (or ROTATE) the door's token, store it in the vault, mark the
        door on. Returns ``(state, plaintext token)`` — the token is shown to
        the user ONCE and lives nowhere but the vault afterwards. An empty
        ``caller`` keeps the name already given (else the default)."""
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        self.secrets.set(
            DOOR_TOKEN_SECRET,
            token,
            kind="token",
            description="Lets another computer running Iron Jarvis send tasks here",
        )
        prior = self.state()
        name = clean_caller(caller) if str(caller or "").strip() else clean_caller(prior.get("caller"))
        state = self._update(
            enabled=True,
            caller=name,
            url=str(url or prior.get("url") or ""),
            enabled_at=utcnow().isoformat(),
        )
        return state, token

    def disable(self) -> dict[str, Any]:
        """Turn the door off and forget its token. Reply keys already handed
        over for tasks stay until their answer is delivered (or refused for
        good) — the sender is owed that answer."""
        try:
            self.secrets.delete(DOOR_TOKEN_SECRET)
        except Exception:  # noqa: BLE001 — an absent secret is fine
            pass
        return self._update(enabled=False)

    def verify(self, candidate: str | None) -> bool:
        """Constant-time check of a sender's token. FAIL-CLOSED: door off, no
        vault entry, an empty candidate or a vault error → False."""
        if not candidate:
            return False
        try:
            if self.state().get("enabled") is not True:
                return False
            expected = self.secrets.get(DOOR_TOKEN_SECRET) or ""
        except Exception:  # noqa: BLE001 — a vault miss is a refusal
            return False
        if not expected:
            return False
        from ..daemon.auth import token_matches

        return token_matches(str(candidate), str(expected))

    def _take(self, table: dict[str, deque[float]], key: str, limit: int, window_s: float) -> bool:
        """One try for ``key`` in ``table``: False past ``limit`` in
        ``window_s`` (nothing counted then). Under ``self._lock``."""
        now = self._clock()
        q = table.get(key)
        if q is None:
            if len(table) >= ADDRESSES_KEPT:
                table.pop(next(iter(table)))
            q = table[key] = deque()
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def rate_ok(self, caller: object = "") -> bool:
        """``TASK_RATE_MAX`` tasks per ``TASK_RATE_WINDOW_S`` from ONE caller
        (a paired computer by its name, the hand-made door by its one name);
        past it, False (nothing is counted then)."""
        with self._lock:
            return self._take(
                self._hits, clean_caller(caller), TASK_RATE_MAX, TASK_RATE_WINDOW_S
            )

    def address_ok(self, source: object, door: str = "task") -> bool:
        """One try from ``source`` (the TCP client's host) at ``door``
        ("task": the task door, ``ADDRESS_TRIES_MAX``; "inbound": the
        message-back door, ``INBOUND_ADDRESS_TRIES_MAX``) per
        ``ADDRESS_WINDOW_S``. Taken FIRST, before any token is read; past it,
        False. Pure memory, never blocks."""
        limit = INBOUND_ADDRESS_TRIES_MAX if door == "inbound" else ADDRESS_TRIES_MAX
        with self._lock:
            table = self._address_hits.setdefault(door, {})
            return self._take(table, plain_address(source), limit, ADDRESS_WINDOW_S)

    # -- what happened -------------------------------------------------------

    def note_task(self, session_id: str) -> None:
        try:
            self._update(last_task_at=utcnow().isoformat(), last_task_session=session_id)
        except Exception:  # noqa: BLE001 — bookkeeping only
            log.warning("task door: could not record the last task", exc_info=True)

    def note_reply(self, session_id: str, *, ok: bool, detail: str) -> None:
        try:
            self._update(
                last_reply={
                    "at": utcnow().isoformat(),
                    "ok": bool(ok),
                    "detail": str(detail or ""),
                    "session_id": session_id,
                }
            )
        except Exception:  # noqa: BLE001 — bookkeeping only
            log.warning("task door: could not record the last reply", exc_info=True)

    def status(self) -> dict[str, Any]:
        """What the card shows — never the token."""
        state = self.state()
        on = self.enabled()
        last_reply = state.get("last_reply") if isinstance(state.get("last_reply"), dict) else None
        return {
            "enabled": on,
            "caller": clean_caller(state.get("caller")),
            "url": str(state.get("url") or ""),
            "enabled_at": state.get("enabled_at") if on else None,
            "last_task_at": state.get("last_task_at"),
            "last_reply": last_reply,
        }


_DOORS: dict[str, TaskDoor] = {}
_DOORS_LOCK = threading.Lock()

#: Runs whose answer is being sent right now in this process (v1.510.0: one
#: try at a time per run; the run's stored reply spec says the rest).
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()
#: When each computer's owed answers were last tried because it was heard
#: from (``HEARD_RETRY_EVERY_S``), by ``peer:<id>`` / ``hand``.
_HEARD_AT: dict[str, datetime] = {}


def door_for(platform: Any) -> TaskDoor:
    """The platform's one :class:`TaskDoor` (made on first use, kept on the
    platform, else in a module table keyed by the home)."""
    door = getattr(platform, "_task_door", None)
    if isinstance(door, TaskDoor):
        return door
    home = Path(platform.config.home)
    key = str(home.resolve())
    with _DOORS_LOCK:
        door = _DOORS.get(key)
        if door is None or door.secrets is not platform.secrets:
            door = TaskDoor(home, platform.secrets)
            _DOORS[key] = door
    try:
        setattr(platform, "_task_door", door)
    except Exception:  # noqa: BLE001 — a frozen double keeps the table entry
        pass
    return door


# --------------------------------------------------------------------------- #
# the task
# --------------------------------------------------------------------------- #


def task_room(window: int | None) -> int:
    """Tokens the whole task may take: ``TASK_WINDOW_SHARE`` of the run's
    window (the planner's own guess, ``DEFAULT_WINDOW``, when it is unknown),
    never more than ``TASK_TOKENS_MAX``."""
    from ..context.budget import DEFAULT_WINDOW

    try:
        win = int(window or 0)
    except (TypeError, ValueError):
        win = 0
    if win <= 0:
        win = DEFAULT_WINDOW
    return max(1, min(TASK_TOKENS_MAX, int(win * TASK_WINDOW_SHARE)))


def _speaker(row: dict[str, str]) -> str:
    return "The person" if row.get("role") == "user" else "Assistant"


def held_back_of(value: object) -> int:
    """A sender's ``history_held_back`` (:data:`HISTORY_HELD_BACK_FIELD`): a
    whole number from 0 to :data:`HISTORY_HELD_BACK_MAX`; anything else
    (missing, text, a fraction, true/false, below 0) is 0. Never raises."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(HISTORY_HELD_BACK_MAX, value))


#: The opening words of one left-out message, at most this many characters.
LEFT_OUT_LINE_CHARS = 160

#: Characters each conversation line has that the planner does not price: the
#: longer speaker label ("The person: ") and the line break before it. The
#: planner charges a row ``_PLANNER_ROW_TOKENS`` past its words
#: (``budget.plan_history``'s "role/format overhead").
_LINE_CHARS = len("The person: ") + 1
_PLANNER_ROW_TOKENS = 4
#: Characters around the left-out list (the break before it, the blank line
#: after it) and the cut mark (" …"); and the tokens the planner may go past
#: its budget by when it cuts the newest row (the cut is sized to the budget,
#: then charged the row's 4, plus 1 for rounding).
_LAYOUT_CHARS = 5
_CUT_OVER_TOKENS = _PLANNER_ROW_TOKENS + 1


def _layout_tokens(rows: int, chars_per_token: float | None) -> int:
    """Tokens the task's layout takes past what the planner prices, at the
    run's ratio: held back from the room so the finished task fits it. At the
    planner's own ratio a label costs no more than the 4 a row is charged; at
    a low measured ratio (2 characters a token) it costs more, on every line."""
    import math

    from ..context.budget import MAX_MESSAGES, effective_chars_per_token

    ratio = effective_chars_per_token(chars_per_token)
    per_line = max(0, math.ceil(_LINE_CHARS / ratio) - _PLANNER_ROW_TOKENS)
    return (
        per_line * min(rows, MAX_MESSAGES)
        + math.ceil(_LAYOUT_CHARS / ratio)
        + _CUT_OVER_TOKENS
    )


def left_out_words(n: int, listed: int | None = None, arrived: int | None = None) -> str:
    """The sentence above the list of messages that did not fit, worded from
    the lines that really follow it (build-out R0.1, second review).

    ``n`` is how many were left out in all; ``arrived`` how many of them
    reached this computer (the NEWEST of them: the sender held the older ones
    back), None for all ``n``; ``listed`` how many lines the list shows, the
    OLDEST of those that arrived first, None for all that arrived. With no
    line to follow, the sentence introduces none."""
    lead = (
        "1 earlier message was left out to fit."
        if n == 1
        else f"{n} earlier messages were left out to fit."
    )
    here = n if arrived is None else max(0, min(arrived, n))
    shown = here if listed is None else max(0, min(listed, here))
    if shown <= 0:
        return lead
    if here == n:
        if shown == n:
            return lead + (" How it began:" if n == 1 else " How they began:")
        if shown == 1:
            return lead + " How the first of them began:"
        return lead + f" How the first {shown} of them began:"
    if shown == here:
        if shown == 1:
            return lead + " How the newest of them began:"
        return lead + f" How the newest {shown} of them began:"
    if shown == 1:
        return lead + f" How the oldest of the {here} that reached this computer began:"
    return lead + f" How the oldest {shown} of the {here} that reached this computer began:"


def _left_out(
    rows: list[dict[str, str]], chars_per_token: float | None, held_back: int = 0
) -> str:
    """How the left-out messages began, oldest first: one short line each,
    the whole list never more than the room the planner held back for it
    (``budget.RECAP_RESERVE``), so the task still fits. The count says how
    many there were (``held_back`` the sender kept, plus ``rows``) even when
    the list is cut short; the list can only show the ones that arrived, and
    the sentence above it says how many lines follow and which ones
    (:func:`left_out_words`, worded AFTER the list is made)."""
    from ..context.budget import RECAP_RESERVE, estimate_tokens

    n = held_back + len(rows)
    # The list is priced under the longest sentence it could get (cut short
    # by one, the most digits), so whichever is said, it fits the reserve.
    widest = left_out_words(n, max(1, len(rows) - 1), len(rows))
    lines: list[str] = []
    for row in rows:
        words = " ".join(row["content"].split())
        if len(words) > LEFT_OUT_LINE_CHARS:
            cut = words[:LEFT_OUT_LINE_CHARS]
            words = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip() + " …"
        line = f"- {_speaker(row)}: {words}"
        if estimate_tokens("\n".join([widest, *lines, line]), chars_per_token) > RECAP_RESERVE:
            break
        lines.append(line)
    return "\n".join([left_out_words(n, len(lines), len(rows)), *lines])


def build_task_text(
    task: str,
    history: Any,
    caller: str,
    *,
    window: int | None = None,
    chars_per_token: float | None = None,
    held_back: int = 0,
) -> str:
    """The session's task: where it came from, the task itself, and the
    sender's conversation when one rode along, PLANNED (build-out R0.1).

    ``window`` is the window the run will be planned against (None: unknown)
    and ``chars_per_token`` its measured ratio (None: the planner's default);
    :func:`planned_task_text` reads both. ``held_back`` is how many OLDER rows
    the sender did not send (:data:`HISTORY_HELD_BACK_FIELD`): they count as
    left out. The opening line and the task are priced first
    (``system_text``), so a long task leaves less room for the conversation;
    the reply's room is the run's own business: the only room held back is
    the layout's (labels, breaks, the cut mark, :func:`_layout_tokens`) and
    the sentence for rows the sender held back. Every row of the sender's
    conversation is either kept whole, kept cut (only the newest, only when
    it alone does not fit, marked " …"), or counted as left out.
    Pure and never raises on a malformed history; it measures every row, so a
    door calls it off the event loop."""
    from ..context.budget import estimate_tokens, plan_history
    from .remote import _history_messages

    text = TASK_INTRO.format(caller=clean_caller(caller)) + "\n\n" + str(task or "").strip()
    rows = _history_messages(history, tail=None)
    held_back = held_back_of(held_back)
    if not rows and not held_back:
        return text
    head = text + "\n\n" + CONVERSATION_HEAD
    if not rows:
        return head + "\n" + left_out_words(held_back, 0)
    reserve = _layout_tokens(len(rows), chars_per_token)
    if held_back:
        # Something is left out even when every row that arrived fits: the
        # sentence that says so is priced up front (when the planner leaves
        # rows out too, the list and that sentence share its RECAP_RESERVE).
        reserve += estimate_tokens(left_out_words(held_back, 0), chars_per_token)
    plan = plan_history(
        rows,
        window=task_room(window),
        system_text=head,
        chars_per_token=chars_per_token,
        reserve=reserve,
    )
    parts = [head]
    if plan.dropped or held_back:
        # The planner keeps the NEWEST messages, so the ones it left out are
        # the oldest ``plan.dropped`` rows, and the ones the sender held back
        # are older still. Its own recap is not used: it calls the person
        # "You", and here the person is on the other computer.
        parts.append(_left_out(rows[: plan.dropped], chars_per_token, held_back) + "\n")
    kept = [f"{_speaker(m)}: {m['content']}" for m in plan.messages]
    if kept and plan.clipped_last:
        kept[-1] = kept[-1].rstrip() + " …"
    parts.extend(kept)
    return "\n".join(parts)


def planned_task_text(
    platform: Any, task: str, history: Any, caller: str, held_back: int = 0
) -> str:
    """:func:`build_task_text` against the window the run will be planned
    against: the RUN's own resolver (``daemon.chat_turn._context_window`` and
    ``_history_ratio``, which ``runtime`` calls each step), with the provider
    and model a door's run gets (none named: the configured defaults, as
    ``Orchestrator.create_session`` sets them). ``held_back``: the sender's
    count of older rows it did not send. Blocking (it measures the
    whole conversation, a body may be 2 MB): the door calls it through
    ``asyncio.to_thread``. Never raises: a window that cannot be read is
    unknown, as it is for the run."""
    window: int | None = None
    ratio: float | None = None
    try:
        from types import SimpleNamespace

        from ..daemon.chat_turn import _context_window, _history_ratio

        d = SimpleNamespace(platform=platform)
        window = _context_window(d, "", "")
        ratio = _history_ratio(d, "", "")
    except Exception:  # noqa: BLE001 — the planner's own default window then
        log.debug("task door: the run's window could not be read", exc_info=True)
    return build_task_text(
        task, history, caller, window=window, chars_per_token=ratio, held_back=held_back
    )


def pinned_reply_to(peer: Any, reply_to: str) -> str:
    """Where a PAIRED computer's answer goes (v1.502.0, north-star 2.16): the
    address this computer stored when the two were paired (scheme, host and
    port) with the inbound path ``reply_to`` names — never the host the task
    named. ``""`` when the pairing holds no address (nothing is sent)."""
    address = str(getattr(peer, "address", "") or "").strip().rstrip("/")
    if not address:
        return ""
    try:
        path = urlparse(str(reply_to or "").strip()).path or ""
    except Exception:  # noqa: BLE001
        return ""
    if not _REPLY_PATH.match(path):
        return ""
    return address + path


#: Which door a task came in by, kept in its reply spec (v1.509.0, F5): a
#: paired computer's ("peer", with the pairing's id) or the hand-made door's
#: ("hand"). A spec from before has neither ("").
DOOR_PEER = "peer"
DOOR_HAND = "hand"

#: Why a paired computer's answer was not sent (v1.509.0, F5 / F6). Read on
#: the run after "Could not send this answer back to <name>: ".
PAIRING_REMOVED = (
    "it was removed from your other computers before the answer was ready, so "
    "nothing was sent"
)

#: The paired computers could not be read: nothing is sent THIS time, and the
#: answer stays owed (v1.510.0: a passing database error is not a refusal).
PAIRINGS_UNREADABLE = "this computer could not read its paired computers, so nothing was sent"


def moved_words(came_from: str, paired_at: str) -> str:
    """The task came from another address than the one the pairing keeps:
    the answer is not sent to the old one (v1.509.0, F6)."""
    return (
        f"the task came from {came_from}, not the address it was paired at "
        f"({paired_at}), so the answer was not sent there. Pair the two "
        "computers again if it moved"
    )


def _paired_reply_to(platform: Any, spec: dict[str, str]) -> tuple[bool, str, str]:
    """``(paired, address, why)`` for a session's reply (BLOCKING: one row
    read). ``why`` is the plain reason nothing is sent when ``address`` is
    "".

    v1.509.0 (F5): THE SPEC'S PAIRING DECIDES, never the caller's name. A
    task a paired computer sent answers only at :func:`pinned_reply_to` of
    THAT pairing (its id, written at the door); a pairing removed since sends
    nothing. A task from the hand-made door keeps the address it named (no
    address of the sender is stored here), even when its name is a
    pairing's. (F6) A paired task that came from another address than the
    pairing keeps is not answered at the old one.

    A spec from before v1.509.0 has no door: it keeps the old reading (the
    pairing of the caller's name)."""
    door = spec.get("door") or ""
    if door == DOOR_HAND:
        return False, spec["to"], ""
    try:
        from ..peers.store import store_for

        store = store_for(platform)
        peer = (
            store.get_by_id(spec.get("peer_id") or "")
            if door == DOOR_PEER
            else store.get(spec["caller"])
        )
    except Exception:  # noqa: BLE001 — unreadable: fail closed, nothing is sent
        log.warning("task door: the paired computers could not be read", exc_info=True)
        return True, "", PAIRINGS_UNREADABLE
    if peer is None:
        if door == DOOR_PEER:
            return True, "", PAIRING_REMOVED
        return False, spec["to"], ""
    came_from = spec.get("from") or ""
    if came_from and came_from_elsewhere(came_from, peer.address):
        return True, "", moved_words(came_from, address_host(peer.address))
    return True, pinned_reply_to(peer, spec["to"]), ""


def reply_spec(
    *,
    reply_to: str,
    conversation_id: str,
    caller: str,
    peer_id: str = "",
    address: str = "",
    came_from: str = "",
) -> dict[str, str]:
    """The run option a task carries when its answer has somewhere to go.
    v1.509.0: which door it came in by, and for a paired computer the
    pairing's id, its stored address as it was then, and the address the
    task came from (``from``)."""
    spec = {
        "to": str(reply_to or "").strip(),
        "conversation_id": str(conversation_id or "").strip(),
        "caller": clean_caller(caller),
        "door": DOOR_PEER if peer_id else DOOR_HAND,
    }
    if peer_id:
        spec["peer_id"] = str(peer_id)
        spec["address"] = str(address or "")
        spec["from"] = plain_address(came_from)
    return spec


def reply_spec_of(session: Any) -> dict[str, str] | None:
    """The session's reply address, or None for a run nobody waits on."""
    try:
        from ..scheduling.knobs import session_options

        options = session_options(session)
    except Exception:  # noqa: BLE001
        return None
    spec = options.get(REPLY_OPTION) if isinstance(options, dict) else None
    if not isinstance(spec, dict):
        return None
    to = str(spec.get("to") or "").strip()
    if not to or not valid_reply_to(to):
        return None
    out = {
        "to": to,
        "conversation_id": str(spec.get("conversation_id") or "").strip(),
        "caller": clean_caller(spec.get("caller")),
    }
    # v1.509.0 (F5 / F6): the door, the pairing and where the task came from.
    for key in ("door", "peer_id", "address", "from"):
        if isinstance(spec.get(key), str) and spec.get(key):
            out[key] = str(spec[key])
    return out


def store_reply_token(secrets: Any, session_id: str, token: str, caller: str) -> None:
    """Keep the caller's reply key for this session in the vault (BLOCKING)."""
    secrets.set(
        REPLY_TOKEN_PREFIX + session_id,
        token,
        kind="token",
        description=(
            f"Answer key for a task from {clean_caller(caller)} "
            "(removed once the answer is sent)"
        ),
    )


def _read_reply_token(secrets: Any, session_id: str) -> str:
    """The session's reply key, KEPT (BLOCKING). v1.510.0 (second review F3):
    it used to be removed in the same hop, before the POST, so an answer that
    did not arrive could never be sent again."""
    try:
        return str(secrets.get(REPLY_TOKEN_PREFIX + session_id) or "")
    except Exception:  # noqa: BLE001
        return ""


def _forget_reply_token(secrets: Any, session_id: str) -> None:
    """Remove the session's reply key (BLOCKING): only once the other
    computer took the answer, or it can never be sent. Never raises."""
    try:
        secrets.delete(REPLY_TOKEN_PREFIX + session_id)
    except Exception:  # noqa: BLE001 — an absent secret is fine
        pass


def _holds_reply_token(secrets: Any, session_id: str) -> bool:
    """Is the session's reply key still kept? (BLOCKING; False on a vault
    error: an answer that cannot be keyed is not tried.)"""
    try:
        exists = getattr(secrets, "exists", None)
        if callable(exists):
            return bool(exists(REPLY_TOKEN_PREFIX + session_id))
        return bool(secrets.get(REPLY_TOKEN_PREFIX + session_id))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# where an answer stands (v1.510.0, second review F3)
# --------------------------------------------------------------------------- #


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def now_for(platform: Any) -> datetime:
    """The wall clock the answer's tries and the asking side's limit read:
    ``platform.reply_clock`` when one is injected (tests), else now (UTC)."""
    clock = getattr(platform, "reply_clock", None)
    if callable(clock):
        try:
            value = clock()
            if isinstance(value, datetime):
                return _aware(value)
        except Exception:  # noqa: BLE001 — a broken clock reads as now
            log.warning("task door: the injected clock failed; reading now", exc_info=True)
    return utcnow()


def parse_at(raw: object) -> datetime | None:
    """A stored stamp (ISO, with or without its zone) as aware UTC, or None."""
    if isinstance(raw, datetime):
        return _aware(raw)
    try:
        return _aware(datetime.fromisoformat(str(raw or "").strip()))
    except (TypeError, ValueError):
        return None


def _stored_spec(engine: Any, session_id: str) -> dict[str, Any]:
    """The run's reply spec as STORED (state fields included), or {}
    (BLOCKING)."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session
        from ..scheduling.knobs import session_options

        with session_scope(engine) as db:
            row = db.get(Session, session_id)
            spec = session_options(row).get(REPLY_OPTION) if row is not None else None
    except Exception:  # noqa: BLE001
        log.warning("task %s: the answer's state could not be read", session_id, exc_info=True)
        return {}
    return dict(spec) if isinstance(spec, dict) else {}


def _write_reply_state(engine: Any, session_id: str, **fields: Any) -> None:
    """Write where the answer stands into the run's stored reply spec
    (BLOCKING; never raises — a state that cannot be written leaves the
    answer to be tried again, which the receiving door takes once)."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session
        from ..scheduling.knobs import session_options

        with session_scope(engine) as db:
            row = db.get(Session, session_id)
            if row is None:
                return
            options = dict(session_options(row))
            spec = options.get(REPLY_OPTION)
            if not isinstance(spec, dict):
                return
            spec = dict(spec)
            for key, value in fields.items():
                if value is None:
                    spec.pop(key, None)
                else:
                    spec[key] = value
            options[REPLY_OPTION] = spec
            row.options_json = json.dumps(options, default=str)
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — bookkeeping only
        log.warning("task %s: the answer's state could not be written", session_id, exc_info=True)


# --------------------------------------------------------------------------- #
# the answer goes home
# --------------------------------------------------------------------------- #


#: The note a DEMO answer carries home (v1.357.0 re-review). A fresh install
#: answers on the demo model, so a second computer nobody chose a model on
#: turned "@desk summarise the Q3 notes" into "Delegated the task to a
#: subagent; all subtasks complete." in the asking chat, undisclosed (the line
#: an inbound door lands has no receipt to say it). In the canon's words
#: (VOCABULARY.md; dashboard lib/demoWords DEMO_MODEL and DEMO_REPLIES), from
#: the asking computer's side: "that computer" is the one that ran it.
DEMO_ANSWER_NOTE = (
    "Note: this answer came from the demo model on that computer: a scripted "
    "demo, not real answers. Choose a model there to get real answers."
)
#: v1.359.0 integrate (lane 9A review): the demo model's own reply now opens
#: by saying what it is (``mock.DEMO_OPENING``), so the note after it says
#: only what to do — the demo is named ONCE, as the chat's ``demo_note`` does.
DEMO_ANSWER_SHORT_NOTE = "Note: choose a model on that computer to get real answers."


def _demo_note_for(text: str) -> str:
    """The demo note this answer gets: the short one when the answer already
    opens with the demo's own sentence, else :data:`DEMO_ANSWER_NOTE`."""
    try:
        from ..providers.adapters.mock import DEMO_OPENING
    except Exception:  # noqa: BLE001 — the full note is never wrong
        DEMO_OPENING = ""
    if DEMO_OPENING and str(text or "").lstrip().startswith(DEMO_OPENING):
        return DEMO_ANSWER_SHORT_NOTE
    return DEMO_ANSWER_NOTE


def _answered_by_demo(session: Any) -> bool:
    """Did the demo model write this finished run's answer? (The served
    provider is stamped on the row when the run ran; a run that failed or was
    stopped carries a sentence of ours, not the demo's.)"""
    from .chat_delivery import _status_value

    return (
        _status_value(session) == "completed"
        and str(getattr(session, "provider", "") or "").strip() == "mock"
    )


#: HOW THE RUN ENDED, on the wire (v1.514.0, second review F4). The answer
#: keeps ``kind: "done"`` (an Iron Jarvis from before this release checks
#: ``kind`` against the four it knows and would refuse a fifth) and carries
#: ``outcome`` beside it, one of these words, read from this computer's own
#: record of the run (``outcome.run_ending``): ``done`` (the job was done),
#: ``part_failed`` / ``needs_you`` / ``did_nothing`` (it finished short, the
#: ledger's verdicts), ``failed``, ``stopped``. A receiver from before this
#: release ignores the field (its message-back door reads only the keys it
#: knows), and the message itself opens with the sentence, so it still reads
#: the truth.
REPLY_OUTCOMES = ("done", "part_failed", "needs_you", "did_nothing", "failed", "stopped")
#: ``Session.outcome`` verdict -> its wire word (a completed run).
_VERDICT_WIRE = {
    "completed_with_failures": "part_failed",
    "needs_you": "needs_you",
    "did_nothing": "did_nothing",
}
#: Wire word -> ``(status, verdict)``, so a receiver reads the SAME helper's
#: words and badge for it (:func:`ending_of_outcome`).
_WIRE_ENDING = {
    "done": ("completed", None),
    "part_failed": ("completed", "completed_with_failures"),
    "needs_you": ("completed", "needs_you"),
    "did_nothing": ("completed", "did_nothing"),
    "failed": ("failed", None),
    "stopped": ("cancelled", None),
}
#: The bounds of the words beside the outcome (both sides cut to them).
REPLY_OUTCOME_WORDS_CHARS = 300
REPLY_MODEL_CHARS = 120
#: A task from another computer runs unattended here, so every tool that
#: needed an OK was refused (``runtime.ASKING_ORIGINS`` does not hold it).
#: When that is ALL that was asked, the answer says so in these words rather
#: than "some of its steps failed" (the first plan's 3.11 keeps a remote run
#: out of the ``did_nothing`` verdict, whose rules stay as they are).
NOTHING_ALLOWED_WORDS = "Nothing that needed an OK on that computer was done."


def ending_of_outcome(outcome: object) -> Any:
    """The ``outcome.run_ending`` reading of a wire outcome word, or None for
    a word not in :data:`REPLY_OUTCOMES`. Pure."""
    from .outcome import run_ending

    pair = _WIRE_ENDING.get(str(outcome or "").strip().lower())
    if pair is None:
        return None
    return run_ending(pair[0], pair[1])


def _one_line(raw: object, cap: int) -> str:
    """``raw`` masked, on one line, cut to ``cap``."""
    from ..detections.redact import mask

    text = " ".join(str(mask(str(raw or "")) or "").split())
    return text[:cap].rstrip()


def _refused_every_ask(engine: Any, session_id: str) -> bool:
    """Did this run ask for at least one OK and get none? (The ledger rows
    whose verdict was ``ask``: none succeeded.) BLOCKING; False on any error."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ToolInvocation

        with session_scope(engine) as db:
            rows = db.exec(
                select(ToolInvocation.verdict, ToolInvocation.ok).where(
                    ToolInvocation.session_id == session_id,
                    ToolInvocation.undo_of.is_(None),  # type: ignore[union-attr]
                )
            ).all()
        asked = [bool(ok) for verdict, ok in rows
                 if str(getattr(verdict, "value", verdict) or "").lower() == "ask"]
        return bool(asked) and not any(asked)
    except Exception:  # noqa: BLE001 — a reading, never the answer
        return False


def reply_ending(engine: Any, session: Any) -> dict[str, str]:
    """How this run REALLY ended, for the answer's body (v1.514.0, F4):
    ``{"outcome", "outcome_words"?, "provider"?, "model"?}``. Read from this
    computer's own record only (``outcome.run_ending`` over the row's status
    and ``Session.outcome``, the ledger, the served provider and model) —
    never from the model's closing words, and nothing else about this
    computer. ``provider`` / ``model`` only when the run's work reached that
    model (``outcome.reached_a_model``; a demo run only when it completed,
    since the row's provider reads "mock" before anything ran). No cost: this
    computer does not show a run's own cost to its user. BLOCKING."""
    from .outcome import reached_a_model, run_ending

    sid = str(getattr(session, "id", "") or "")
    end = run_ending(session)
    if end.status == "completed":
        outcome = _VERDICT_WIRE.get(str(end.outcome or ""), "done")
    elif end.status == "cancelled":
        outcome = "stopped"
    else:
        outcome = "failed"
    words = "" if outcome == "done" else end.reason
    if end.status == "completed" and sid and _refused_every_ask(engine, sid):
        words = NOTHING_ALLOWED_WORDS
        if outcome == "done":
            outcome = "part_failed"
    out: dict[str, str] = {"outcome": outcome}
    words = _one_line(words, REPLY_OUTCOME_WORDS_CHARS)
    if words:
        out["outcome_words"] = words
    provider = _one_line(getattr(session, "provider", ""), REPLY_MODEL_CHARS)
    model = _one_line(getattr(session, "model", ""), REPLY_MODEL_CHARS)
    if provider and not (provider == "mock" and end.status != "completed"):
        try:
            from ..core.db import session_scope

            with session_scope(engine) as db:
                reached = reached_a_model(db, sid, provider)
        except Exception:  # noqa: BLE001 — unknown: say nothing rather than guess
            reached = False
        if reached:
            out["provider"] = provider
            if model:
                out["model"] = model
    return out


def _reply_message(session: Any, caller: str = "", lead: str = "") -> str:
    """The answer, in the words the sender's chat would show for this run
    (``chat_delivery.content_for_session``), secrets masked, cut to what the
    receiving door accepts — and, when the demo model wrote it, saying so
    (:data:`DEMO_ANSWER_NOTE`, kept whole past the cut).

    v1.510.0: this computer's own lines about sending it (``retry_note`` /
    ``waiting_note``, written on the run while it is owed) are left out: they
    are about the answer, not part of it.

    v1.514.0 (F4): ``lead`` (the run did not end as plain done: this
    computer's own sentence for how it ended, :func:`reply_ending`) opens the
    message, so a receiver that reads only ``message`` still shows it."""
    from ..detections.redact import mask
    from .chat_delivery import content_for_session

    text = str(content_for_session(session) or "")
    if caller:
        text = _with_note(text, caller, "")
    text = str(mask(text) or "").strip()
    lead = str(lead or "").strip()
    if lead:
        text = lead if (not text or text == lead) else f"{lead}\n\n{text}"
    demo = ("\n\n" + _demo_note_for(text)) if _answered_by_demo(session) else ""
    room = REPLY_MESSAGE_CHARS - len(demo)
    if len(text) > room:
        note = "\n\n(The answer was longer; the rest is on the other computer.)"
        text = text[: room - len(note)].rstrip() + note
    return text + demo


def _reply_files(engine: Any, session_id: str) -> tuple[list[dict[str, str]], list[str]]:
    """What the run made, as ``[{name, content_b64}]`` within the caps, and the
    names left behind (BLOCKING). Never raises.

    v1.509.0 (second review, "not closed" b): only a file INSIDE the folder
    the run (or one of its workers) worked in is sent, its path resolved and
    any link followed first; a path outside is left out and named with the
    rest of what was not sent."""
    files: list[dict[str, str]] = []
    left: list[str] = []
    try:
        from .remote_files import safe_filename
        from .reply_files import Caps, files_made

        # v1.503.0 (north-star 3.13): the files the run's workers made too.
        # 10-b: the ONE reading the phone's reply uses too (agents/reply_files).
        picked = files_made(
            engine, session_id,
            Caps(count=REPLY_FILES_MAX, each=REPLY_FILE_BYTES, total=REPLY_FILES_TOTAL_BYTES),
        )
    except Exception:  # noqa: BLE001
        return [], []
    left.extend(safe_filename(name) for name, _why in picked.left)
    for path in picked.files:
        name = safe_filename(path.name)
        try:
            blob = path.read_bytes()
        except OSError:
            left.append(name)
            continue
        files.append({"name": name, "content_b64": base64.b64encode(blob).decode("ascii")})
    return files, left


def _plain_reply_failure(
    exc: BaseException | None, status: int = 0, body: str = "", *, timed_out: bool = False
) -> str:
    """Why the answer did not arrive, in plain words (never the key)."""
    from ..detections.redact import mask

    if timed_out:
        return f"the other computer did not answer within {int(REPLY_TIMEOUT_S)} seconds"
    if status == 401:
        return "the other computer refused this computer's answer key (it was turned off or changed there)"
    if status == 403:
        return "the other computer is no longer letting this one message back"
    if status == 413:
        return "the answer was too large for the other computer"
    if status == 429:
        return "the other computer asked to slow down"
    if status:
        detail = ""
        try:
            parsed = json.loads(body) if body else None
            if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
                detail = parsed["detail"]
        except ValueError:
            detail = ""
        detail = " ".join(str(mask(detail or "")).split())[:200]
        return "the other computer did not take the answer" + (f" ({detail})" if detail else "")
    text = " ".join(str(mask(str(exc or ""))).split())[:200]
    return "the other computer could not be reached" + (f" ({text})" if text else "")


async def send_reply(platform: Any, session: Any, *, force: bool = False) -> dict[str, Any]:
    """Send the finished session's answer to the computer that asked for it.

    v1.510.0 (second review F3): ONE try per call, bounded by
    ``REPLY_TIMEOUT_S`` as a total deadline, the blocking parts (vault, ledger,
    file reads, the record) off the loop, the message masked. The answer key
    is KEPT until the other computer answers 2xx (then removed) or refuses it
    for good (a 4xx such as a wrong key, an unknown conversation: removed,
    recorded, never tried again). Any other failure (cannot be reached, timed
    out, a 5xx, "slow down") keeps the answer OWED: the next try is due after
    the next wait of ``REPLY_RETRY_AFTER_S``, and after ``REPLY_TRIES`` tries
    it waits for that computer to be heard from. Where it stands is written
    into the run's reply spec BEFORE the POST (a restart mid-try finds it
    owed). A call before the next try is due does nothing unless ``force``
    (the computer was just heard from).

    Returns ``{ok, sent, detail}``; a session nobody waits on returns
    ``{"ok": False, "sent": False, "detail": "no reply address"}`` without
    touching anything. Never raises."""
    spec = reply_spec_of(session)
    sid = str(getattr(session, "id", "") or "")
    if spec is None or not sid:
        return {"ok": False, "sent": False, "detail": "no reply address"}
    # One try at a time per run, whichever path asks (a cancel that lands on
    # a run already finalizing, a pass, a computer heard from).
    with _IN_FLIGHT_LOCK:
        if sid in _IN_FLIGHT:
            return {"ok": False, "sent": False, "detail": "already being sent"}
        _IN_FLIGHT.add(sid)
    try:
        return await _send_once(platform, session, spec, sid, force=force)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(sid)


def _let_go_words(caller: str) -> str:
    days = int(REPLY_OWED_FOR_S // 86400)
    return (
        f"{clean_caller(caller)} did not connect again within {days} days, so the "
        "answer is no longer kept"
    )


async def _send_once(
    platform: Any, session: Any, spec: dict[str, str], sid: str, *, force: bool
) -> dict[str, Any]:
    import httpx

    engine = platform.engine
    caller = spec["caller"]
    now = now_for(platform)
    stored = await asyncio.to_thread(_stored_spec, engine, sid)
    state = str(stored.get("state") or "")
    if state in (REPLY_SENT, REPLY_FINAL):
        return {"ok": state == REPLY_SENT, "sent": False, "detail": "already attempted"}
    try:
        tries = max(int(stored.get("tries") or 0), 0)
    except (TypeError, ValueError):
        tries = 0
    if tries and not force:
        if state == REPLY_WAITING:
            ended = parse_at(getattr(session, "finished_at", None))
            if ended is None or (now - ended).total_seconds() <= REPLY_OWED_FOR_S:
                return {"ok": False, "sent": False, "detail": "waiting for that computer"}
        else:
            due = parse_at(stored.get("next_at"))
            if due is not None and now < due:
                return {"ok": False, "sent": False, "detail": "not due yet"}

    async def final(detail: str, *, forget: bool = True) -> dict[str, Any]:
        if forget:
            await asyncio.to_thread(_forget_reply_token, platform.secrets, sid)
        await asyncio.to_thread(_write_reply_state, engine, sid, state=REPLY_FINAL, next_at=None)
        await _record_reply(platform, session, ok=False, detail=detail, caller=caller)
        return {"ok": False, "sent": False, "detail": detail}

    ended = parse_at(getattr(session, "finished_at", None))
    if ended is not None and (now - ended).total_seconds() > REPLY_OWED_FOR_S:
        return await final(_let_go_words(caller))
    if not spec["conversation_id"]:
        return await final("the other computer sent no conversation to answer into")
    try:
        token = await asyncio.to_thread(_read_reply_token, platform.secrets, sid)
    except Exception:  # noqa: BLE001
        token = ""
    if not token:
        # Nothing is sent without the key the sender gave for its answer: a
        # POST with none could only be refused there.
        return await final(
            "no answer key for this task is held here (it never came, or was lost)",
            forget=False,
        )
    # A PAIRED COMPUTER'S ANSWER GOES TO ITS STORED ADDRESS (v1.502.0,
    # north-star 2.16): the answer and the files the run made never go to a
    # host the task named instead. v1.509.0: by the pairing's id; a removed
    # pairing or a task from another address sends nothing, for good.
    _paired, target, why = await asyncio.to_thread(_paired_reply_to, platform, spec)
    if not target and why != PAIRINGS_UNREADABLE:
        return await final(
            why or (
                "this computer does not know that paired computer's address any more (pair them again)"
            )
        )
    # Where it stands is written BEFORE the try: a restart in the middle of
    # it finds the answer owed, with its next try due.
    tries += 1
    wait = REPLY_RETRY_AFTER_S[tries - 1] if tries <= len(REPLY_RETRY_AFTER_S) else None
    next_at = (now + timedelta(seconds=wait)).isoformat() if wait is not None else now.isoformat()
    await asyncio.to_thread(
        _write_reply_state, engine, sid, state=REPLY_OWED, tries=tries, next_at=next_at
    )
    status = 0
    text = ""
    exc: BaseException | None = None
    timed_out = False
    if not target:
        detail = why
    else:
        # v1.514.0 (F4): how the run really ended, from this computer's own
        # record; the message opens with it when it was not plain done.
        try:
            ending = await asyncio.to_thread(reply_ending, engine, session)
        except Exception:  # noqa: BLE001 — the answer still goes, as before
            log.warning("task %s: how the run ended could not be read", sid, exc_info=True)
            ending = {}
        message = _reply_message(session, caller, ending.get("outcome_words", ""))
        files, left = await asyncio.to_thread(_reply_files, engine, sid)
        if left:
            message += "\n\nAlso made on this computer, not sent: " + ", ".join(left)
            if len(message) > REPLY_MESSAGE_CHARS:
                message = message[:REPLY_MESSAGE_CHARS]
        body: dict[str, Any] = {
            "conversation_id": spec["conversation_id"],
            "message": message,
            "kind": "done",
            # v1.510.0: which run this answers, so the other computer takes a
            # repeat (a try whose 2xx was lost on the way back) only once.
            "answer_id": sid,
            # v1.514.0 (F4): beside ``kind`` (which stays "done" for an older
            # receiver): ``outcome``, ``outcome_words``, ``provider``, ``model``.
            **ending,
        }
        if files:
            body["files"] = files
        headers = {"Authorization": f"Bearer {token}"}
        # A TOTAL deadline (the v1.288.0 rule): httpx's timeout is per phase,
        # so a peer trickling bytes would never trip it. Only this scope
        # expiring earns the deadline wording; a TimeoutError raised inside
        # keeps its own words.
        deadline = asyncio.timeout(REPLY_TIMEOUT_S)
        try:
            async with deadline:
                async with httpx.AsyncClient(timeout=REPLY_TIMEOUT_S, follow_redirects=False) as client:
                    resp = await client.post(target, json=body, headers=headers)
            status = int(getattr(resp, "status_code", 0) or 0)
            try:
                text = str(resp.text or "")[:500]
            except Exception:  # noqa: BLE001
                text = ""
        except TimeoutError as err:
            exc = err
            timed_out = deadline.expired()
        except Exception as err:  # noqa: BLE001 — connection / DNS / anything
            exc = err
        if exc is None and status // 100 == 2:
            await asyncio.to_thread(_forget_reply_token, platform.secrets, sid)
            await asyncio.to_thread(_write_reply_state, engine, sid, state=REPLY_SENT, next_at=None)
            await _record_reply(platform, session, ok=True, detail="", caller=caller)
            return {"ok": True, "sent": True, "detail": "sent"}
        detail = _plain_reply_failure(exc, status, text, timed_out=timed_out)
        if exc is None and 400 <= status < 500 and status not in _RETRY_LATER_4XX:
            # Refused for good: no later try can change a wrong key or an
            # unknown conversation.
            return await final(detail)
    if tries >= REPLY_TRIES:
        await asyncio.to_thread(_write_reply_state, engine, sid, state=REPLY_WAITING, next_at=None)
        interim = waiting_note(caller, detail)
    else:
        interim = retry_note(caller, detail)
    await _record_reply(platform, session, ok=False, detail=detail, caller=caller, interim=interim)
    return {"ok": False, "sent": False, "detail": detail}


def reply_failed_note(caller: str, detail: str) -> str:
    """The sentence a session's summary gains when its answer will not arrive."""
    return f"Could not send this answer back to {clean_caller(caller)}: {detail}."


def _retry_prefix(caller: str) -> str:
    return f"This answer has not reached {clean_caller(caller)} yet"


def _waiting_prefix(caller: str) -> str:
    return f"This answer is still waiting for {clean_caller(caller)}"


def retry_note(caller: str, detail: str) -> str:
    """The run's line while its answer is still on the ladder (v1.510.0)."""
    return f"{_retry_prefix(caller)}: {detail}. It will be tried again."


def waiting_note(caller: str, detail: str) -> str:
    """The run's line once the ladder is over (v1.510.0): the answer is still
    kept and goes when that computer next connects."""
    name = clean_caller(caller)
    return f"{_waiting_prefix(caller)}: {detail}. It will be sent when {name} next connects."


def _with_note(summary: str, caller: str, note: str) -> str:
    """``summary`` with this answer's earlier interim lines removed and
    ``note`` (if any) added once, as its own paragraph."""
    prefixes = (_retry_prefix(caller), _waiting_prefix(caller))
    paras = [p for p in str(summary or "").split("\n\n") if not p.strip().startswith(prefixes)]
    text = "\n\n".join(paras).strip()
    if note and note not in text:
        text = (text + "\n\n" + note).strip()
    return text


async def _record_reply(
    platform: Any,
    session: Any,
    *,
    ok: bool,
    detail: str,
    caller: str,
    interim: str | None = None,
) -> None:
    """Record the try on the door's status and the run's summary: a sent
    answer clears this answer's interim line; a final failure replaces it
    with :func:`reply_failed_note`; a failure still owed (``interim``)
    replaces it with the new interim line."""
    sid = str(getattr(session, "id", "") or "")

    def _write() -> None:
        door_for(platform).note_reply(sid, ok=ok, detail=detail)
        if not ok:
            log.warning("task %s: could not send the answer back to %s: %s", sid, caller, detail)
        note = "" if ok else (interim if interim is not None else reply_failed_note(caller, detail))
        try:
            from ..core.db import session_scope
            from ..core.models import Session

            with session_scope(platform.engine) as db:
                row = db.get(Session, sid)
                if row is not None:
                    new = _with_note(row.summary or "", caller, note)
                    if new != (row.summary or ""):
                        row.summary = new
                        db.add(row)
                        db.commit()
            try:
                session.summary = _with_note(getattr(session, "summary", "") or "", caller, note)
            except Exception:  # noqa: BLE001 — a detached double
                pass
        except Exception:  # noqa: BLE001 — bookkeeping only
            log.warning("task %s: could not record the reply", sid, exc_info=True)

    try:
        await asyncio.to_thread(_write)
    except Exception:  # noqa: BLE001
        log.warning("task %s: could not record the reply", sid, exc_info=True)


# --- answers still owed (v1.510.0, second review F3) -----------------------


def _owed_runs(
    platform: Any, *, now: datetime, peer_id: str = "", door: str = "", force: bool = False
) -> list[Any]:
    """The ended runs whose answer is still OWED (BLOCKING), newest first,
    at most ``REPLY_OWED_SCAN``: a reply spec in the options that is not
    "sent" or "final", AND the answer key still kept in the vault (so a run
    from before v1.510.0, whose key was used up, is never tried again).
    ``peer_id`` keeps one pairing's, ``door`` = :data:`DOOR_HAND` the hand-made
    door's. Without ``force`` only a try that is due (its ``next_at`` passed;
    a "waiting" one only once it is past ``REPLY_OWED_FOR_S``, to be let go)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Session, SessionStatus
    from ..scheduling.knobs import session_options

    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(Session)
                .where(Session.origin.startswith(f"{REMOTE_TASK_ORIGIN}:"))  # type: ignore[union-attr]
                .where(
                    Session.status.in_(  # type: ignore[attr-defined]
                        [SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED]
                    )
                )
                .where(Session.options_json.contains(f'"{REPLY_OPTION}"'))  # type: ignore[attr-defined]
                .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                .limit(REPLY_OWED_SCAN)
            )
        )
        for r in rows:
            db.expunge(r)
    out: list[Any] = []
    for row in rows:
        spec = session_options(row).get(REPLY_OPTION)
        if not isinstance(spec, dict) or reply_spec_of(row) is None:
            continue
        state = str(spec.get("state") or "")
        if state in (REPLY_SENT, REPLY_FINAL):
            continue
        if peer_id and str(spec.get("peer_id") or "") != peer_id:
            continue
        if door and str(spec.get("door") or "") != door:
            continue
        if not force and spec.get("tries"):
            if state == REPLY_WAITING:
                ended = parse_at(getattr(row, "finished_at", None))
                if ended is None or (now - ended).total_seconds() <= REPLY_OWED_FOR_S:
                    continue
            else:
                due = parse_at(spec.get("next_at"))
                if due is not None and now < due:
                    continue
        if not _holds_reply_token(platform.secrets, row.id):
            continue
        out.append(row)
    return out


async def reply_pass(
    platform: Any, *, peer_id: str = "", door: str = "", force: bool = False
) -> int:
    """Start a try for every answer still owed (see :func:`_owed_runs`), each
    on its own tracked task (so nothing here waits on another machine).
    Returns how many were started. Never raises."""
    try:
        rows = await asyncio.to_thread(
            _owed_runs, platform, now=now_for(platform), peer_id=peer_id, door=door, force=force
        )
    except Exception:  # noqa: BLE001 — a pass must never raise
        log.warning("task door: the answers still owed could not be read", exc_info=True)
        return 0
    started = 0
    for row in rows:
        if _start_send(platform, row, force=force) is not None:
            started += 1
    return started


def retry_owed_soon(
    platform: Any, *, peer_id: str = "", door: str = "", pressed: bool = False
) -> asyncio.Task | None:
    """The computer ``peer_id`` (or the hand-made door's, ``door``) was just
    heard from: try what is still owed to it now, whatever its ladder says —
    at most once per ``HEARD_RETRY_EVERY_S`` for that computer, unless the
    user ``pressed`` Check here. Callers keep v1.509.0's rules: a request from
    another address than the pairing's never calls this (the answer goes to
    the pairing, by id, at its stored address). A tracked task on the running
    loop; None when nothing was started. Never raises."""
    who = f"peer:{peer_id}" if peer_id else (door or "")
    if not who:
        return None
    key = f"{id(platform)}:{who}"
    now = now_for(platform)
    last = _HEARD_AT.get(key)
    if (
        not pressed
        and last is not None
        and 0 <= (now - last).total_seconds() < HEARD_RETRY_EVERY_S
    ):
        return None
    _HEARD_AT[key] = now
    if len(_HEARD_AT) > 4096:
        _HEARD_AT.pop(next(iter(_HEARD_AT)))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(reply_pass(platform, peer_id=peer_id, door=door, force=True))
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)
    return task


async def periodic_pass(platform: Any) -> None:
    """The daemon's periodic pass (``daemon/app._terminal_snapshot_loop``,
    every 30 s): the answers owed whose next try is due, and (at most every
    ``threads.SILENT_SWEEP_S``) the asking side's waits that passed their
    limit. Never raises."""
    try:
        await reply_pass(platform)
    except Exception:  # noqa: BLE001
        log.warning("task door: the owed answers pass failed", exc_info=True)
    try:
        from .threads import end_overdue_waits

        await end_overdue_waits(platform)
    except Exception:  # noqa: BLE001
        log.warning("task door: the overdue waits pass failed", exc_info=True)


# --- tracked background replies -------------------------------------------

_PENDING_TASKS: set[asyncio.Task] = set()
_PENDING_THREADS: set[threading.Thread] = set()


def send_reply_soon(platform: Any, session: Any, *, force: bool = False) -> asyncio.Task | None:
    """Start :func:`send_reply` for a finished session WITHOUT holding the
    run's teardown on another machine (a tracked task on the running loop).
    No reply address = nothing started (None). Never raises."""
    if reply_spec_of(session) is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return send_reply_in_thread(platform, session, force=force)  # type: ignore[return-value]
    task = loop.create_task(send_reply(platform, session, force=force))
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)
    return task


_start_send = send_reply_soon


def send_reply_in_thread(
    platform: Any, session: Any, *, force: bool = False
) -> threading.Thread | None:
    """The same from a place with no running loop (the boot reconcile): a
    daemon thread runs the try, so boot never waits on another machine.
    Never raises."""
    if reply_spec_of(session) is None:
        return None

    def _run() -> None:
        try:
            asyncio.run(send_reply(platform, session, force=force))
        except Exception:  # noqa: BLE001
            log.warning("task reply thread failed", exc_info=True)
        finally:
            _PENDING_THREADS.discard(thread)

    thread = threading.Thread(target=_run, name="task-door-reply", daemon=True)
    _PENDING_THREADS.add(thread)
    thread.start()
    return thread


async def drain_replies(timeout: float = 30.0) -> None:
    """Wait for every reply in flight (tests; a clean shutdown) — including
    the tries a pass started while it was waited for (v1.510.0), bounded."""
    for _round in range(8):
        tasks = [t for t in list(_PENDING_TASKS) if not t.done()]
        if not tasks:
            break
        await asyncio.wait(tasks, timeout=timeout)
    for thread in list(_PENDING_THREADS):
        await asyncio.to_thread(thread.join, timeout)
