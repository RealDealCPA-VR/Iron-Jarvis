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
  vault per session (``REPLY_TOKEN_PREFIX``) and removed the moment the reply
  is attempted. One attempt, bounded, masked, off the loop; a reply that fails
  is RECORDED on the session's summary and on the door's status, named (who,
  why) — never retried blindly.
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

#: The task text's cap — the inbound door's message cap, the same number.
TASK_MAX_CHARS = 12_000

#: The whole request body's cap (history rides along).
TASK_MAX_BODY_BYTES = 2 * 1024 * 1024

#: How much of the sender's conversation the session's task carries.
HISTORY_ROWS_IN_TASK = 20
HISTORY_CHARS_IN_TASK = 8_000

#: The reply's bound (the WHOLE call, not a per-phase httpx timeout).
REPLY_TIMEOUT_S = 20.0

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
    "This computer is not taking tasks from another computer. Turn it on in "
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

    def __init__(self, home: str | Path, secrets: Any) -> None:
        self.home = Path(home)
        self.secrets = secrets
        self._lock = threading.Lock()
        self._hits: deque[float] = deque()

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
        over for tasks still running stay until their reply is attempted —
        the sender is owed that answer."""
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

    def rate_ok(self) -> bool:
        """``TASK_RATE_MAX`` tasks per ``TASK_RATE_WINDOW_S``; past it, False
        (nothing is counted then)."""
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > TASK_RATE_WINDOW_S:
                self._hits.popleft()
            if len(self._hits) >= TASK_RATE_MAX:
                return False
            self._hits.append(now)
            return True

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

#: Runs whose answer has been attempted in this process (one attempt each).
_ATTEMPTED: set[str] = set()
_ATTEMPTED_LOCK = threading.Lock()


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


def build_task_text(task: str, history: Any, caller: str) -> str:
    """The session's task: where it came from, the task itself, and the tail
    of the sender's conversation (bounded; newest kept) when one rode along."""
    from .remote import _history_messages

    text = TASK_INTRO.format(caller=clean_caller(caller)) + "\n\n" + str(task or "").strip()
    rows = _history_messages(history)[-HISTORY_ROWS_IN_TASK:]
    if not rows:
        return text
    lines: list[str] = []
    used = 0
    for row in reversed(rows):
        who = "The person" if row["role"] == "user" else "Assistant"
        line = f"{who}: {row['content']}"
        if used + len(line) > HISTORY_CHARS_IN_TASK:
            room = HISTORY_CHARS_IN_TASK - used
            if room > 200:
                lines.append(line[:room].rstrip() + " …")
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return text
    lines.reverse()
    return text + "\n\nThe conversation so far on that computer:\n" + "\n".join(lines)


def reply_spec(*, reply_to: str, conversation_id: str, caller: str) -> dict[str, str]:
    """The run option a task carries when its answer has somewhere to go."""
    return {
        "to": str(reply_to or "").strip(),
        "conversation_id": str(conversation_id or "").strip(),
        "caller": clean_caller(caller),
    }


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
    return {
        "to": to,
        "conversation_id": str(spec.get("conversation_id") or "").strip(),
        "caller": clean_caller(spec.get("caller")),
    }


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


def _take_reply_token(secrets: Any, session_id: str) -> str:
    """Read the session's reply key and REMOVE it in the same hop (BLOCKING):
    the reply is attempted once, so the key is never needed twice."""
    name = REPLY_TOKEN_PREFIX + session_id
    try:
        token = secrets.get(name) or ""
    except Exception:  # noqa: BLE001
        token = ""
    try:
        secrets.delete(name)
    except Exception:  # noqa: BLE001
        pass
    return token


# --------------------------------------------------------------------------- #
# the answer goes home
# --------------------------------------------------------------------------- #


def _reply_message(session: Any) -> str:
    """The answer, in the words the sender's chat would show for this run
    (``chat_delivery.content_for_session``), secrets masked, cut to what the
    receiving door accepts."""
    from ..detections.redact import mask
    from .chat_delivery import content_for_session

    text = str(mask(content_for_session(session)) or "").strip()
    if len(text) > REPLY_MESSAGE_CHARS:
        note = "\n\n(The answer was longer; the rest is on the other computer.)"
        text = text[: REPLY_MESSAGE_CHARS - len(note)].rstrip() + note
    return text


def _reply_files(engine: Any, session_id: str) -> tuple[list[dict[str, str]], list[str]]:
    """What the run made, as ``[{name, content_b64}]`` within the caps, and the
    names left behind (BLOCKING). Never raises."""
    files: list[dict[str, str]] = []
    left: list[str] = []
    try:
        from .outcome import session_result
        from .remote_files import safe_filename

        result = session_result(engine, session_id)
        docs = [str(p) for p in (result.get("documents") or []) if p] if isinstance(result, dict) else []
    except Exception:  # noqa: BLE001
        return [], []
    total = 0
    for raw in docs:
        path = Path(raw)
        name = safe_filename(path.name)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if (
            len(files) >= REPLY_FILES_MAX
            or size > REPLY_FILE_BYTES
            or total + size > REPLY_FILES_TOTAL_BYTES
        ):
            left.append(name)
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            left.append(name)
            continue
        total += len(blob)
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


async def send_reply(platform: Any, session: Any) -> dict[str, Any]:
    """Send the finished session's answer to the computer that asked for it.

    ONE attempt, bounded by ``REPLY_TIMEOUT_S`` as a total deadline, the
    blocking parts (vault, ledger, file reads, the record) off the loop, the
    message masked. Returns ``{ok, sent, detail}``; a session nobody waits on
    returns ``{"ok": False, "sent": False, "detail": "no reply address"}``
    without touching anything. Never raises. A failure is RECORDED on the
    session's summary and on the door's status, naming who and why."""
    import httpx

    spec = reply_spec_of(session)
    sid = str(getattr(session, "id", "") or "")
    if spec is None or not sid:
        return {"ok": False, "sent": False, "detail": "no reply address"}
    # ONE attempt per run, whichever terminal path asks twice (a cancel that
    # lands on a run already finalizing): the second ask records nothing.
    with _ATTEMPTED_LOCK:
        if sid in _ATTEMPTED:
            return {"ok": False, "sent": False, "detail": "already attempted"}
        if len(_ATTEMPTED) > 10_000:
            _ATTEMPTED.clear()
        _ATTEMPTED.add(sid)
    caller = spec["caller"]
    try:
        token = await asyncio.to_thread(_take_reply_token, platform.secrets, sid)
    except Exception:  # noqa: BLE001
        token = ""
    if not spec["conversation_id"]:
        detail = "the other computer sent no conversation to answer into"
        await _record_reply(platform, session, ok=False, detail=detail, caller=caller)
        return {"ok": False, "sent": False, "detail": detail}
    if not token:
        # Nothing is sent without the key the sender gave for its answer: a
        # POST with none could only be refused there.
        detail = "no answer key for this task is held here (it never came, or was lost)"
        await _record_reply(platform, session, ok=False, detail=detail, caller=caller)
        return {"ok": False, "sent": False, "detail": detail}
    message = _reply_message(session)
    files, left = await asyncio.to_thread(_reply_files, platform.engine, sid)
    if left:
        message += "\n\nAlso made on this computer, not sent: " + ", ".join(left)
        if len(message) > REPLY_MESSAGE_CHARS:
            message = message[:REPLY_MESSAGE_CHARS]
    body: dict[str, Any] = {
        "conversation_id": spec["conversation_id"],
        "message": message,
        "kind": "done",
    }
    if files:
        body["files"] = files
    headers = {"Authorization": f"Bearer {token}"}
    status = 0
    text = ""
    exc: BaseException | None = None
    timed_out = False
    # A TOTAL deadline (the v1.288.0 rule): httpx's timeout is per phase, so a
    # peer trickling bytes would never trip it. Only this scope expiring earns
    # the deadline wording; a TimeoutError raised inside keeps its own words.
    deadline = asyncio.timeout(REPLY_TIMEOUT_S)
    try:
        async with deadline:
            async with httpx.AsyncClient(timeout=REPLY_TIMEOUT_S, follow_redirects=False) as client:
                resp = await client.post(spec["to"], json=body, headers=headers)
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
        await _record_reply(platform, session, ok=True, detail="", caller=caller)
        return {"ok": True, "sent": True, "detail": "sent"}
    detail = _plain_reply_failure(exc, status, text, timed_out=timed_out)
    await _record_reply(platform, session, ok=False, detail=detail, caller=caller)
    return {"ok": False, "sent": False, "detail": detail}


def reply_failed_note(caller: str, detail: str) -> str:
    """The sentence a session's summary gains when its answer did not arrive."""
    return f"Could not send this answer back to {clean_caller(caller)}: {detail}."


async def _record_reply(platform: Any, session: Any, *, ok: bool, detail: str, caller: str) -> None:
    sid = str(getattr(session, "id", "") or "")

    def _write() -> None:
        door_for(platform).note_reply(sid, ok=ok, detail=detail)
        if ok:
            return
        log.warning("task %s: could not send the answer back to %s: %s", sid, caller, detail)
        try:
            from ..core.db import session_scope
            from ..core.models import Session

            note = reply_failed_note(caller, detail)
            with session_scope(platform.engine) as db:
                row = db.get(Session, sid)
                if row is not None and note not in (row.summary or ""):
                    row.summary = ((row.summary or "").rstrip() + "\n\n" + note).strip()
                    db.add(row)
                    db.commit()
            if note not in (getattr(session, "summary", "") or ""):
                try:
                    session.summary = ((session.summary or "").rstrip() + "\n\n" + note).strip()
                except Exception:  # noqa: BLE001 — a detached double
                    pass
        except Exception:  # noqa: BLE001 — bookkeeping only
            log.warning("task %s: could not record the failed reply", sid, exc_info=True)

    try:
        await asyncio.to_thread(_write)
    except Exception:  # noqa: BLE001
        log.warning("task %s: could not record the reply", sid, exc_info=True)


# --- tracked background replies -------------------------------------------

_PENDING_TASKS: set[asyncio.Task] = set()
_PENDING_THREADS: set[threading.Thread] = set()


def send_reply_soon(platform: Any, session: Any) -> asyncio.Task | None:
    """Start :func:`send_reply` for a finished session WITHOUT holding the
    run's teardown on another machine (a tracked task on the running loop).
    No reply address = nothing started (None). Never raises."""
    if reply_spec_of(session) is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return send_reply_in_thread(platform, session)  # type: ignore[return-value]
    task = loop.create_task(send_reply(platform, session))
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)
    return task


def send_reply_in_thread(platform: Any, session: Any) -> threading.Thread | None:
    """The same from a place with no running loop (the boot reconcile): a
    daemon thread runs the one attempt, so boot never waits on another
    machine. Never raises."""
    if reply_spec_of(session) is None:
        return None

    def _run() -> None:
        try:
            asyncio.run(send_reply(platform, session))
        except Exception:  # noqa: BLE001
            log.warning("task reply thread failed", exc_info=True)
        finally:
            _PENDING_THREADS.discard(thread)

    thread = threading.Thread(target=_run, name="task-door-reply", daemon=True)
    _PENDING_THREADS.add(thread)
    thread.start()
    return thread


async def drain_replies(timeout: float = 30.0) -> None:
    """Wait for every reply in flight (tests; a clean shutdown)."""
    tasks = [t for t in list(_PENDING_TASKS) if not t.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    for thread in list(_PENDING_THREADS):
        await asyncio.to_thread(thread.join, timeout)
