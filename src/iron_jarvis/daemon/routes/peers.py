"""Your other computers: pair two PCs running Iron Jarvis in one step (v1.497.0).

The vision plan's leap 7, Phase 1. One computer shows a six-digit code
(``POST /peers/code``); on the other the user types that computer's address and
the code (``POST /peers/join``), which asks the first one to pair
(``POST /peers/pair``, the door the OTHER computer calls). Each side then holds:

* a token it made for the other (checked at its task door and its unpair door),
* the token the other made for it,
* a remote agent named for the other computer (its task door, message-back on),
  so "@desk do X" in chat hands the other computer the task and the answer
  comes back to that chat through the code that already does it.

``/peers/pair`` and ``/peers/unpair`` are exempt from the install token
(``auth._is_exempt``): the other computer never holds this one's install token.
``/peers/pair`` is checked by the code (one at a time, ten minutes, works once,
five wrong tries end it, ten tries a minute from anyone); ``/peers/unpair`` by
the token this computer made for that peer. Every other route here needs the
install token as usual.

Tasks a paired computer sends run exactly as the task door runs them: low
trust, on their own, nobody here answers their asks, Emergency stop refuses
them (``routes/agents.task_door_task``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets as _secrets
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ...core.events import EventType
from ...core.logging import get_logger
from ...peers import codes as C
from ...peers import link as L
from ...peers import store as S

log = get_logger("daemon.routes.peers")
_CODES_LOCK = threading.Lock()

#: The most a pairing request may carry (it holds a code, two names, a port
#: and a token).
PAIR_MAX_BODY_BYTES = 16 * 1024

#: The other computer's refusals, read on the screen of the one typing the
#: code ("that computer" is this one, seen from there).
NOT_SHOWING = (
    "That computer is not showing a pairing code. On it, open Settings › Connections › Your other "
    "computers and press Show a pairing code, then type the new code here."
)
CODE_EXPIRED = (
    f"That code has run out (a code works for {C.CODE_TTL_S // 60} minutes). Show a new one on that "
    "computer and type it here."
)
CODE_WRONG = "That code is not right. Check the six digits shown on that computer."
CODE_USED_UP = (
    "That code was typed wrong too many times and no longer works. Show a new one on that computer."
)
TOO_MANY_TRIES = "That computer is taking too many pairing tries. Wait a minute and try again."
OLD_VERSION = (
    "The two computers run different versions of Iron Jarvis and cannot pair. Update both to the "
    "newest version, then try again."
)
NO_ADDRESS = (
    "That computer could not tell where this pairing request came from, so it did not pair and kept "
    "nothing. Try again, typing its address as its pairing screen shows it."
)
UNKNOWN_TOKEN = "This computer does not know that token."
#: The unpair door could not read or change its list just now (review,
#: v1.497.0): the other computer's Remove then says it could not tell this one.
UNPAIR_FAILED = "This computer could not end the pairing just now. Try again."
#: The computer showing the code could not keep the pairing (review,
#: v1.497.0): said to the one typing it, in place of an internal error.
KEEP_FAILED = (
    "That computer could not keep the pairing, so nothing was kept on either computer. Show a new "
    "code on it and try again."
)


#: How long the door that took the code may spend keeping the pairing before
#: it answers "nothing was kept" (v1.499.1 review). Well under the joining
#: side's whole wait (``link.PAIR_TIMEOUT_S``), so the two never disagree.
KEEP_MAX_S = 10.0

#: Keeps that finished after the door gave up: each is taken back when it
#: lands (tests wait on these).
_LATE_KEEPS: set[asyncio.Task] = set()


def join_failed_words(name: str, told: bool) -> str:
    """THIS computer could not keep a pairing the other one already kept: it
    was told to drop its side (or could not be)."""
    if told:
        return (
            f"This computer could not keep the pairing, so nothing was kept on either computer: {name} "
            "dropped its side too. Show a new code there and try again."
        )
    return (
        f"This computer could not keep the pairing, and {name} could not be told to drop its side. "
        "Remove this computer there (Settings › Connections › Your other computers on that computer), "
        "then show a new code there and try again."
    )

#: What this computer's screen says after a pairing it made.
def paired_words(name: str) -> str:
    return f"Paired with {name}. In chat, write “@{name}” and a task to hand it to that computer."


def removed_words(name: str, told: bool, stopped: int = 0) -> str:
    if told:
        said = f"Removed. {name} removed this computer from its list too."
    else:
        said = (
            f"Removed here. {name} could not be told, so remove this computer there too (Settings › "
            "Connections › Your other computers on that computer). It cannot reach this one any more "
            "either way."
        )
    return said + stopped_words(name, stopped)


def stopped_words(name: str, stopped: int) -> str:
    """v1.509.0 (second review F5): what Remove did to the tasks that
    computer had sent and that were still running here ("" for none)."""
    if stopped <= 0:
        return ""
    if stopped == 1:
        return f" The task {name} sent that was still running here was stopped."
    return f" The {stopped} tasks {name} sent that were still running here were stopped."


#: The summary a run that Remove stopped is left with (v1.509.0, F5).
def removed_stop_summary(name: str) -> str:
    return f"Stopped: {name} was removed from your other computers."


class CodeBody(BaseModel):
    #: What this computer calls itself when it pairs (empty = keep it).
    this_name: str = ""


class JoinBody(BaseModel):
    #: The other computer's address, as its pairing screen shows it.
    address: str = ""
    #: The six digits that computer shows.
    code: str = ""
    #: What to call that computer here (empty = the name it gives itself).
    name: str = ""
    #: What this computer calls itself (empty = keep it).
    this_name: str = ""


def _ip(text: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(str(text or "").strip().strip("[]"))
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _own_port(request: Request) -> int:
    """The port this Iron Jarvis answers on (8787 in the desktop app)."""
    port = request.url.port
    if not port:
        server = request.scope.get("server")
        if isinstance(server, (tuple, list)) and len(server) == 2 and isinstance(server[1], int):
            port = server[1]
    return int(port or S.DEFAULT_PORT)


def _lan_guess() -> str:
    """This PC's address on its network, best effort (a UDP 'connect' picks
    the route; nothing is sent). "" when there is none."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return str(s.getsockname()[0])
        finally:
            s.close()
    except Exception:  # noqa: BLE001 — offline / no route
        return ""


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def register(app: FastAPI, d: Any) -> None:
    """Install the /peers routes."""
    from ..auth import PEER_PAIR_PATH, PEER_UNPAIR_PATH

    def _store() -> S.PeerStore:
        return S.store_for(d.platform)

    def _codes() -> C.PairingCodes:
        # Made once, under a lock: ``_view`` reaches this from a worker thread
        # while a handler on the loop may make a code, and two holders made at
        # once would lose the code shown.
        with _CODES_LOCK:
            codes = getattr(d.platform, "_pairing_codes", None)
            if not isinstance(codes, C.PairingCodes):
                codes = C.PairingCodes()
                try:
                    setattr(d.platform, "_pairing_codes", codes)
                except Exception:  # noqa: BLE001
                    pass
            return codes

    async def _publish(kind: str, payload: dict[str, Any]) -> None:
        try:
            await d.platform.event_bus.publish(kind, payload)
        except Exception:  # noqa: BLE001 — the pairing stands either way
            log.warning("could not publish %s", kind, exc_info=True)

    def _network(request: Request) -> dict[str, Any]:
        port = _own_port(request)
        addresses = S.this_computers_addresses(port)
        try:
            saved = bool(getattr(d.platform.config, "lan_access", False))
        except Exception:  # noqa: BLE001
            saved = False
        return {"saved": saved, "applied": bool(addresses), "addresses": addresses}

    def _view(request: Request) -> dict[str, Any]:
        store = _store()
        active = _codes().active()
        code = (
            {"code": active[0], "shown": C.shown(active[0]), "expires_at": active[1].isoformat()}
            if active
            else None
        )
        return {
            "this_name": store.this_name(),
            "peers": [S.peer_view(p) for p in store.list()],
            "code": code,
            "network": _network(request),
        }

    @app.get("/peers")
    def list_peers(request: Request) -> dict[str, Any]:
        """The paired computers, this computer's own name, the pairing code
        being shown (if any) and whether other computers can reach this one.
        Never a token."""
        return _view(request)

    @app.post("/peers/code")
    def make_code(request: Request, body: CodeBody | None = None) -> dict[str, Any]:
        """Show a pairing code (a new one replaces the old). The other
        computer types it with this computer's address."""
        if body is not None and body.this_name.strip():
            _store().set_this_name(body.this_name)
        _codes().make()
        return _view(request)

    @app.post("/peers/code/cancel")
    def cancel_code(request: Request) -> dict[str, Any]:
        _codes().cancel()
        return _view(request)

    @app.post("/peers/join")
    async def join(request: Request, body: JoinBody) -> dict[str, Any]:
        """Pair with the computer at ``address`` using the code it shows."""
        store = await asyncio.to_thread(_store)
        try:
            address = S.normalize_address(body.address)
        except S.AddressProblem as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        code = C.normalize(body.code)
        if not code:
            raise HTTPException(
                status_code=400, detail="The code is the six digits shown on the other computer."
            )
        if body.this_name.strip():
            this_name = await asyncio.to_thread(store.set_this_name, body.this_name)
        else:
            this_name = await asyncio.to_thread(store.this_name)
        port = _own_port(request)
        guess = await asyncio.to_thread(_lan_guess)
        in_token = _secrets.token_urlsafe(32)
        try:
            answer = await L.pair(
                address, code=code, name=this_name, port=port, door_token=in_token, guess=guess
            )
        except L.LinkFailed as exc:
            raise HTTPException(status_code=409, detail=exc.words) from None
        their_name = answer["name"]
        wanted = body.name.strip() or their_name or S.FALLBACK_NAME
        # Where the other computer reaches THIS one: the address it saw this
        # request come from (that route is proven), else this PC's own guess.
        seen = _ip(answer.get("seen_as")) or _ip(guess)
        my_address = S.address_from(str(seen), port) if seen else S.address_from("127.0.0.1", port)

        def _keep() -> Any:
            name, replaces = store.free_name(wanted, address=address)
            return store.add(
                name=name,
                their_name=their_name,
                address=address,
                in_token=in_token,
                out_token=answer["door_token"],
                my_address=my_address,
                replaces=replaces,
            )

        try:
            peer = await asyncio.to_thread(_keep)
        except Exception:  # noqa: BLE001 — said in words below, never the exception's text
            # All or nothing (review, v1.497.0): the other computer has
            # already kept its side, holding a token for a pairing this one
            # never kept. Tell it to drop that side with the token it gave
            # this one (its unpair door checks exactly that token).
            log.warning("pairing: this computer could not keep the pairing", exc_info=True)
            told = await L.unpair(address, answer["door_token"])
            raise HTTPException(
                status_code=500,
                detail=join_failed_words(S.peer_name(their_name) if their_name else address, told),
            ) from None
        await _publish(EventType.PEER_PAIRED, {"name": peer.name})
        return {
            "peer": S.peer_view(peer),
            "said": paired_words(peer.name),
            **(await asyncio.to_thread(_view, request)),
        }

    @app.post(PEER_PAIR_PATH)
    async def pair_here(request: Request) -> dict[str, Any]:
        """The OTHER computer asks to pair, with the code this one shows.

        Exempt from the install token; checked by the code. In order: ten
        tries a minute from anyone (429), a small body read no further than
        its cap (413), the protocol, the token's shape and where the request
        came from (400), then the code (403 none / run out / used up, 401
        wrong). Only then is anything kept, all or nothing (500 in words)."""
        codes = _codes()
        if not codes.take_try():
            raise HTTPException(status_code=429, detail=TOO_MANY_TRIES)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > PAIR_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="That pairing request is too large.")
        # Read up to the cap and stop (review, v1.497.0): anyone on the network
        # reaches this door, and a body with no length (chunked) used to be
        # held in memory WHOLE, up to the app's 256 MB ceiling, before its size
        # was looked at.
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > PAIR_MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="That pairing request is too large.")
        try:
            body = json.loads(bytes(raw) or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body must be JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        if body.get("protocol") != L.PROTOCOL:
            raise HTTPException(status_code=400, detail=OLD_VERSION)
        their_token = body.get("door_token")
        if not S.valid_token(their_token):
            raise HTTPException(status_code=400, detail=OLD_VERSION)
        port = body.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise HTTPException(status_code=400, detail=OLD_VERSION)
        # Where the other computer is: the address its request CAME FROM, and
        # nothing it says (review, v1.497.0: the first cut fell back to the
        # body's "address", so a sender that could not be placed chose where
        # this computer would later send its token and its tasks). Where THIS
        # computer is, as the other one reaches it: the address it dialed (the
        # Host guard already accepted it). Both are read BEFORE the code, so a
        # request that cannot be placed never uses the code up.
        client = getattr(request, "client", None)
        seen = _ip(getattr(client, "host", "") if client else "")
        host = (request.headers.get("host") or "").strip()
        my_address = f"{request.url.scheme or 'http'}://{host}" if host else ""
        if seen is None or not my_address:
            raise HTTPException(status_code=400, detail=NO_ADDRESS)
        their_address = S.address_from(str(seen), port)
        verdict = codes.check(body.get("code"))
        if verdict == C.NO_CODE:
            raise HTTPException(status_code=403, detail=NOT_SHOWING)
        if verdict == C.EXPIRED:
            raise HTTPException(status_code=403, detail=CODE_EXPIRED)
        if verdict == C.USED_UP:
            # The screen showing the code is told it ended (review, v1.497.0):
            # it would otherwise keep a dead code up for the rest of its ten
            # minutes.
            await _publish(EventType.PEER_CODE_ENDED, {"why": "wrong_tries"})
            raise HTTPException(status_code=403, detail=CODE_USED_UP)
        if verdict != C.OK:
            raise HTTPException(status_code=401, detail=CODE_WRONG)
        # The code was right (and is now used up).
        their_name = S.peer_name(body.get("name"))
        in_token = _secrets.token_urlsafe(32)

        def _keep() -> tuple[Any, str]:
            store = _store()
            name, replaces = store.free_name(their_name, address=their_address)
            peer = store.add(
                name=name,
                their_name=their_name,
                address=their_address,
                in_token=in_token,
                out_token=str(their_token),
                my_address=my_address,
                replaces=replaces,
            )
            return peer, store.this_name()

        keeping = asyncio.ensure_future(asyncio.to_thread(_keep))
        try:
            peer, this_name = await asyncio.wait_for(asyncio.shield(keeping), timeout=KEEP_MAX_S)
        except asyncio.TimeoutError:
            # The joining side would give up soon and keep nothing: say so
            # now, and take this side back whenever the slow keep lands, so a
            # pairing is never left on one computer only.
            log.warning("pairing: keeping the pairing took longer than %.0f s; it is taken back", KEEP_MAX_S)
            late = asyncio.ensure_future(_take_back_late(keeping))
            _LATE_KEEPS.add(late)
            late.add_done_callback(_LATE_KEEPS.discard)
            raise HTTPException(status_code=503, detail=KEEP_FAILED) from None
        except Exception:  # noqa: BLE001 — never the exception's text to the other computer
            # ``add`` took back whatever it had written. The app's own 500
            # envelope would have sent this door's caller — anyone on the
            # network who had the code — the exception class and message.
            log.warning("pairing: could not keep the pairing", exc_info=True)
            raise HTTPException(status_code=500, detail=KEEP_FAILED) from None
        await _publish(EventType.PEER_PAIRED, {"name": peer.name})
        return {
            "protocol": L.PROTOCOL,
            "name": this_name,
            "door_token": in_token,
            "seen_as": str(seen),
        }

    async def _take_back_late(keeping: "asyncio.Future[tuple[Any, str]]") -> None:
        """A keep the door already answered "nothing was kept" for: once it
        finishes, remove what it kept (its record, tokens and remote agent).
        Never raises."""
        try:
            peer, _name = await keeping
        except Exception:  # noqa: BLE001 — it failed: ``add`` already took itself back
            return
        try:
            await asyncio.to_thread(_store().remove, peer.name)
        except Exception:  # noqa: BLE001
            log.warning("pairing: could not take back a pairing kept too late", exc_info=True)

    @app.post(PEER_UNPAIR_PATH)
    async def unpair_here(request: Request) -> dict[str, Any]:
        """The OTHER computer removed its pairing with this one: remove this
        side too. Checked by the token this computer made for that peer."""
        if not _codes().take_try():
            raise HTTPException(status_code=429, detail=TOO_MANY_TRIES)
        token = _bearer(request)

        def _end() -> Any:
            store = _store()
            peer = store.verify(token)
            if peer is not None:
                store.remove(peer.name)
            return peer

        try:
            peer = await asyncio.to_thread(_end)
        except Exception:  # noqa: BLE001 — fail closed, in words, never the exception's text
            # This door takes no install token: the app's own 500 envelope
            # would have handed its caller the exception's class and message.
            log.warning("pairing: could not end a pairing the other computer ended", exc_info=True)
            raise HTTPException(status_code=503, detail=UNPAIR_FAILED) from None
        if peer is None:
            raise HTTPException(status_code=401, detail=UNKNOWN_TOKEN)
        # v1.509.0 (F5): its tasks still running here stop too.
        await asyncio.to_thread(_stop_runs_of, peer)
        await _publish(EventType.PEER_REMOVED, {"name": peer.name, "by": "them"})
        return {"removed": True}

    async def _check_one(peer: Any) -> dict[str, Any]:
        store = await asyncio.to_thread(_store)
        token = await asyncio.to_thread(store.out_token, peer)
        ok = False
        try:
            if not token:
                raise L.LinkFailed("This computer lost its token for that one. Remove it and pair again.")
            await L.check(peer.address, token)
            await asyncio.to_thread(store.note_ok, peer.id)
            ok = True
        except L.LinkFailed as exc:
            await asyncio.to_thread(store.note_error, peer.id, exc.words)
        fresh = await asyncio.to_thread(store.get, peer.name)
        return {"ok": ok, "peer": S.peer_view(fresh or peer)}

    @app.post("/peers/check")
    async def check_all(request: Request) -> dict[str, Any]:
        """Ask every paired computer whether it still answers (all at once,
        each bounded)."""
        peers = await asyncio.to_thread(lambda: _store().list())
        if peers:
            await asyncio.gather(*(_check_one(p) for p in peers))
        return await asyncio.to_thread(_view, request)

    @app.post("/peers/{name}/check")
    async def check_one(name: str) -> dict[str, Any]:
        peer = await asyncio.to_thread(lambda: _store().get(name))
        if peer is None:
            raise HTTPException(status_code=404, detail="No paired computer has that name.")
        return await _check_one(peer)

    @app.delete("/peers/{name}")
    async def remove_peer(name: str) -> dict[str, Any]:
        """End a pairing: tell the other computer (one bounded try), then
        remove it here whatever it answered."""
        store = await asyncio.to_thread(_store)
        peer = await asyncio.to_thread(store.get, name)
        if peer is None:
            raise HTTPException(status_code=404, detail="No paired computer has that name.")
        token = await asyncio.to_thread(store.out_token, peer)
        told = await L.unpair(peer.address, token) if token else False
        await asyncio.to_thread(store.remove, name)
        # v1.509.0 (F5): the tasks it sent that still run here stop, after
        # the pairing is gone (so their answer is never sent: the reply finds
        # no pairing and records why).
        stopped = await asyncio.to_thread(_stop_runs_of, peer)
        await _publish(EventType.PEER_REMOVED, {"name": name, "by": "here"})
        return {
            "removed": True,
            "told": told,
            "stopped": len(stopped),
            "said": removed_words(name, told, len(stopped)),
        }

    def _stop_runs_of(peer: Any, *, limit: int = 500) -> list[str]:
        """Stop every live run a task from ``peer`` started here
        (``options["remote_peer"]`` is its id, written at the task door),
        through the orchestrator's one stop path (``cancel_session``, cross-
        loop safe, the way Emergency stop's ``stop_unattended`` reaches a
        run), its summary saying why. BLOCKING; returns the ids stopped;
        never raises."""
        from sqlmodel import select

        from ...agents.task_door import REMOTE_PEER_OPTION, REMOTE_TASK_ORIGIN
        from ...core.db import session_scope
        from ...core.models import Session, SessionStatus

        orch = getattr(d, "orchestrator", None)
        if orch is None or not getattr(peer, "id", ""):
            return []
        try:
            with session_scope(d.platform.engine) as db:
                rows = db.exec(
                    select(Session.id, Session.options_json)
                    .where(
                        Session.status.in_(  # type: ignore[attr-defined]
                            [SessionStatus.ACTIVE, SessionStatus.QUEUED]
                        )
                    )
                    .where(Session.origin.startswith(f"{REMOTE_TASK_ORIGIN}:"))  # type: ignore[union-attr]
                    .limit(max(int(limit), 1))
                ).all()
        except Exception:  # noqa: BLE001
            log.warning("pairing: the runs of a removed computer could not be read", exc_info=True)
            return []
        stopped: list[str] = []
        for sid, options_json in rows:
            try:
                opts = json.loads(options_json) if isinstance(options_json, str) else options_json
            except (TypeError, ValueError):
                continue
            if not isinstance(opts, dict) or opts.get(REMOTE_PEER_OPTION) != peer.id:
                continue
            if orch.stop_with_reason(sid, removed_stop_summary(peer.name)):
                stopped.append(sid)
        return stopped
