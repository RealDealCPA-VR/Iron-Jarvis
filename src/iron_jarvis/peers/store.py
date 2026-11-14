"""The paired computers: their records, their tokens, their remote agents (v1.497.0).

Every method here is BLOCKING (the database and the vault): the routes call
them off the event loop. Nothing returns or logs a token except
:meth:`PeerStore.add`'s caller, which already holds both.

A pairing is three things kept together:

* a :class:`~iron_jarvis.peers.models.PeerRecord`;
* two vault entries, ``peer.<id>.in`` (the token this computer made for the
  other, checked at this computer's task door) and ``peer.<id>.out`` (the token
  the other computer made for this one);
* a remote agent of the same name whose address is the other computer's task
  door and whose credential is ``peer.<id>.out``, with message-back on (its own
  inbound token, which rides with each task as ``reply_token``) — so "@<name>"
  in chat, delegation and the answer coming home all use the code that already
  does them (``agents/remote.py``, ``agents/task_door.py``).

Removing a pairing removes all three.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import PeerRecord

log = get_logger(__name__)

#: The port Iron Jarvis listens on in the desktop app (the dashboard's address
#: is built for it), used when an address is typed without one.
DEFAULT_PORT = 8787

#: The state file under the home: this computer's own name. No secret in it.
STATE_FILE = "peers.json"

#: A name for a computer when none can be made from what was given.
FALLBACK_NAME = "computer"

#: The remote-agent name rule (``routes/agents.add_remote_agent``).
_REMOTE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
_NOT_NAME = re.compile(r"[^A-Za-z0-9_-]+")

#: Tokens are ``secrets.token_urlsafe(32)``: these characters, this length.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,200}$")

_LOCK = threading.Lock()


def peer_name(raw: object) -> str:
    """A name a person can write after "@": letters, digits, ``-`` and ``_``,
    starting with a letter, at most 40 characters. ``"Valentino's Laptop"``
    becomes ``"Valentino-s-Laptop"``."""
    text = _NOT_NAME.sub("-", str(raw or "").strip())
    text = text.lstrip("-_0123456789")[:40].rstrip("-_")
    return text if _REMOTE_NAME.match(text or "") else FALLBACK_NAME


def valid_token(raw: object) -> bool:
    return isinstance(raw, str) and bool(_TOKEN.match(raw))


def in_secret_name(peer_id: str) -> str:
    """The vault key of the token THIS computer made for a peer. The "." can
    never be produced by a remote-agent name, so it never collides with one."""
    return f"peer.{peer_id}.in"


def out_secret_name(peer_id: str) -> str:
    """The vault key of the token a peer made for this computer."""
    return f"peer.{peer_id}.out"


# --------------------------------------------------------------------------- #
# addresses
# --------------------------------------------------------------------------- #


class AddressProblem(ValueError):
    """A typed address that cannot be a computer's address, in plain words."""


def normalize_address(typed: object) -> str:
    """``http://host:port`` from what a person typed: ``192.168.1.20``,
    ``192.168.1.20:8787``, ``http://desk:8787/``. A path, a query, a user name
    or another scheme is refused in words."""
    text = str(typed or "").strip()
    if not text:
        raise AddressProblem("Type the other computer's address, shown on its pairing screen.")
    if "\\" in text or any(ord(ch) < 32 for ch in text):
        raise AddressProblem("That is not a computer's address. Type it as it is shown, for example 192.168.1.20.")
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError:
        raise AddressProblem("That address has a port that is not a number.") from None
    if parsed.scheme not in ("http", "https"):
        raise AddressProblem("The address must start with http:// or https://, or with the computer's number.")
    if parsed.username or parsed.password:
        raise AddressProblem("Type the address alone, with no name or password in it.")
    if (parsed.path or "/") != "/" or parsed.query or parsed.fragment:
        raise AddressProblem("Type the address alone, for example 192.168.1.20:8787, with nothing after it.")
    host = parsed.hostname or ""
    if not host:
        raise AddressProblem("Type the other computer's address, shown on its pairing screen.")
    port = port or DEFAULT_PORT
    return f"{parsed.scheme}://{_host_for_url(host)}:{port}"


def _host_for_url(host: str) -> str:
    """A host as it goes in an address: an IPv6 address in brackets."""
    h = host.strip().strip("[]")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return h.lower()
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return str(mapped)
    return f"[{ip}]" if ip.version == 6 else str(ip)


def address_from(host: str, port: int, scheme: str = "http") -> str:
    """``scheme://host:port`` for a host this computer saw (an IP)."""
    return f"{scheme}://{_host_for_url(host)}:{int(port)}"


def door_url(address: str) -> str:
    """The other computer's task door, from its address."""
    from ..daemon.auth import TASK_DOOR_PATH

    return address.rstrip("/") + TASK_DOOR_PATH


def inbound_url(address: str, remote_name: str) -> str:
    """Where the other computer posts its answers for ``remote_name`` here,
    from THIS computer's address as the other one reaches it."""
    return address.rstrip("/") + f"/agents/remote/{quote(remote_name, safe='')}/inbound"


def this_computers_addresses(port: int) -> list[str]:
    """The addresses another computer can type to reach this one: the private
    addresses the desktop app opened (``IRONJARVIS_HOST_ALLOWLIST``, set only
    while "Let other computers on my network reach this Iron Jarvis" is on and
    applied), each with the port. Empty while other computers cannot reach it."""
    import os

    out: list[str] = []
    for name in (os.environ.get("IRONJARVIS_HOST_ALLOWLIST") or "").split(","):
        name = name.strip()
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_unspecified:
            continue
        addr = f"{_host_for_url(name)}:{int(port)}"
        if addr not in out:
            out.append(addr)
    return out


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


class PeerStore:
    """The paired computers of one Iron Jarvis (BLOCKING methods)."""

    def __init__(self, engine: Any, secrets: Any, home: str | Path) -> None:
        self.engine = engine
        self.secrets = secrets
        self.home = Path(home)
        try:
            PeerRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — made concurrently / already there
            pass

    # -- this computer's own name -------------------------------------------

    def this_name(self) -> str:
        """What this computer calls itself when it pairs: the name the user
        gave, else this PC's own name."""
        try:
            raw = json.loads((self.home / STATE_FILE).read_text(encoding="utf-8"))
            name = raw.get("this_name") if isinstance(raw, dict) else ""
        except Exception:  # noqa: BLE001 — no file yet / junk
            name = ""
        if name and _REMOTE_NAME.match(str(name)):
            return str(name)
        try:
            return peer_name(socket.gethostname())
        except Exception:  # noqa: BLE001
            return FALLBACK_NAME

    def set_this_name(self, raw: object) -> str:
        name = peer_name(raw)
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"this_name": name}, indent=2), encoding="utf-8")
        tmp.replace(path)
        return name

    # -- reading -------------------------------------------------------------

    def list(self) -> list[PeerRecord]:
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(PeerRecord)))
            for r in rows:
                db.expunge(r)
        return sorted(rows, key=lambda r: r.name.lower())

    def get(self, name: str) -> PeerRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(select(PeerRecord).where(PeerRecord.name == name)).first()
            if row is not None:
                db.expunge(row)
            return row

    def names(self) -> set[str]:
        return {r.name for r in self.list()}

    def out_token(self, peer: PeerRecord) -> str:
        """The token this computer shows at the peer's door ("" when gone)."""
        try:
            return str(self.secrets.get(peer.out_secret) or "")
        except Exception:  # noqa: BLE001
            return ""

    def verify(self, candidate: str | None) -> PeerRecord | None:
        """The peer whose token ``candidate`` is, or None. FAIL-CLOSED: an
        empty candidate, a vault error or no match is None. Every peer's token
        is compared (constant-time each), so the time does not say which."""
        if not candidate:
            return None
        from ..daemon.auth import token_matches

        found: PeerRecord | None = None
        for peer in self.list():
            try:
                expected = str(self.secrets.get(peer.in_secret) or "")
            except Exception:  # noqa: BLE001 — a vault miss is no match
                expected = ""
            if expected and token_matches(str(candidate), expected) and found is None:
                found = peer
        return found

    # -- names ---------------------------------------------------------------

    def free_name(self, wanted: object, *, address: str = "") -> tuple[str, PeerRecord | None]:
        """A name for a new pairing, and the pairing it replaces.

        The same name AND the same address is the same computer pairing again:
        that pairing is replaced. A name another pairing or a remote agent the
        user added by hand already has gets ``-2``, ``-3`` ... so nothing the
        user made is ever overwritten."""
        from ..agents.remote import RemoteAgentRegistry

        base = peer_name(wanted)
        peers = {p.name: p for p in self.list()}
        remotes = {r.name for r in RemoteAgentRegistry(self.engine).list()}
        same = peers.get(base)
        if same is not None and address and same.address == address:
            return base, same
        name = base
        n = 2
        while name in peers or name in remotes:
            suffix = f"-{n}"
            name = base[: 40 - len(suffix)] + suffix
            n += 1
        return name, None

    # -- writing -------------------------------------------------------------

    def add(
        self,
        *,
        name: str,
        their_name: str,
        address: str,
        in_token: str,
        out_token: str,
        my_address: str,
        replaces: PeerRecord | None = None,
    ) -> PeerRecord:
        """Keep a new pairing: the record, both tokens in the vault, and its
        remote agent (task door address, ``out`` token, message-back on with
        answers posted to ``my_address``). ``replaces`` (the same computer
        pairing again) is removed first."""
        from ..agents.remote import RemoteAgentRegistry

        with _LOCK:
            if replaces is not None:
                self._remove_parts(replaces)
            row = PeerRecord(name=name, their_name=peer_name(their_name) if their_name else "",
                             address=address)
            row.in_secret = in_secret_name(row.id)
            row.out_secret = out_secret_name(row.id)
            row.last_ok_at = utcnow()
            self.secrets.set(row.in_secret, in_token, kind="token",
                             description=f"Lets {name}, a paired computer, reach this one")
            self.secrets.set(row.out_secret, out_token, kind="token",
                             description=f"Lets this computer reach {name}, a paired computer")
            reg = RemoteAgentRegistry(self.engine)
            reg.upsert(name, door_url(address), "http-task", secret_name=row.out_secret,
                       enabled=True, timeout_s=120)
            reg.enable_inbound(name, self.secrets.set, url=inbound_url(my_address, name))
            with session_scope(self.engine) as db:
                db.add(row)
                db.commit()
                db.refresh(row)
                db.expunge(row)
            return row

    def remove(self, name: str) -> bool:
        """End a pairing here: its record, its tokens and its remote agent."""
        with _LOCK:
            peer = self.get(name)
            if peer is None:
                return False
            self._remove_parts(peer)
            return True

    def _remove_parts(self, peer: PeerRecord) -> None:
        from ..agents.remote import RemoteAgentRegistry

        reg = RemoteAgentRegistry(self.engine)
        rec = reg.get(peer.name)
        if rec is not None:
            for key in (rec.inbound_secret_name, rec.secret_name):
                if key:
                    self._forget(key)
            reg.remove(peer.name)
        for key in (peer.in_secret, peer.out_secret):
            if key:
                self._forget(key)
        with session_scope(self.engine) as db:
            row = db.get(PeerRecord, peer.id)
            if row is not None:
                db.delete(row)
                db.commit()

    def _forget(self, key: str) -> None:
        try:
            self.secrets.delete(key)
        except Exception:  # noqa: BLE001 — an absent secret is fine
            pass

    def _touch(self, peer_id: str, **fields: Any) -> None:
        try:
            with session_scope(self.engine) as db:
                row = db.get(PeerRecord, peer_id)
                if row is None:
                    return
                for key, value in fields.items():
                    setattr(row, key, value)
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001 — bookkeeping only
            log.warning("paired computer: could not record what happened", exc_info=True)

    def note_heard(self, peer_id: str) -> None:
        """The peer reached this computer (a task, an answer)."""
        self._touch(peer_id, last_heard_at=utcnow())

    def note_ok(self, peer_id: str) -> None:
        """The peer answered this computer."""
        self._touch(peer_id, last_ok_at=utcnow(), last_error="", last_error_at=None)

    def note_error(self, peer_id: str, words: str) -> None:
        """The peer did not answer this computer, and why."""
        self._touch(peer_id, last_error=str(words or "")[:300], last_error_at=utcnow())

    def note_heard_by_remote(self, remote_name: str) -> None:
        """The remote agent ``remote_name`` messaged back: when it is a paired
        computer, it was heard from. Never raises."""
        try:
            peer = self.get(remote_name)
        except Exception:  # noqa: BLE001
            peer = None
        if peer is not None:
            self.note_heard(peer.id)


def iso(value: Any) -> str | None:
    """A stored time as an ISO string with its zone (SQLite drops it)."""
    if value is None:
        return None
    try:
        if value.tzinfo is None:
            from datetime import timezone

            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except Exception:  # noqa: BLE001
        return None


def peer_view(peer: PeerRecord) -> dict[str, Any]:
    """What a screen may see of a pairing: never a token or a vault key."""
    return {
        "name": peer.name,
        "their_name": peer.their_name,
        "address": peer.address,
        "paired_at": iso(peer.paired_at),
        "last_ok_at": iso(peer.last_ok_at),
        "last_heard_at": iso(peer.last_heard_at),
        "last_error": peer.last_error or "",
        "last_error_at": iso(peer.last_error_at),
    }


def store_for(platform: Any) -> PeerStore:
    """The platform's one :class:`PeerStore`."""
    store = getattr(platform, "_peer_store", None)
    if isinstance(store, PeerStore) and store.engine is platform.engine:
        return store
    store = PeerStore(platform.engine, platform.secrets, platform.config.home)
    try:
        setattr(platform, "_peer_store", store)
    except Exception:  # noqa: BLE001
        pass
    return store


def is_paired_remote(platform: Any, remote_name: str) -> bool:
    """Is ``remote_name`` the remote agent a pairing made? Never raises."""
    try:
        return store_for(platform).get(remote_name) is not None
    except Exception:  # noqa: BLE001
        return False
