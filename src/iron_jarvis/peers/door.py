"""The peer door (next leaps 41-a): what only a paired computer's own token opens.

A paired computer reaches this one through doors that take the token THIS
computer made for it (``peer.<id>.in``), never the install bearer: the task
door (``auth.TASK_DOOR_PATH``) and the routes under :data:`PREFIX`, each one
spelled once in ``daemon/auth.PEER_DOOR_PATHS`` and exempt from the install
bearer for exactly itself. Each of them asks :func:`peer_for` (or, for the task
door, which also lets the hand-made door in, :func:`refuse_unless`), in the
order the task door always used (v1.509.0, F14):

1. one try from the request's source address (``TaskDoor.address_ok``,
   memory only) — an address past its budget is refused before any database
   or vault read;
2. the token, compared with every pairing's (``PeerStore.verify``, off the
   loop, fail-closed): none is a 401 in words — so the install bearer, a
   pane's token or a browser's pairing token never opens a peer door;
3. the door's own switch (``peers/allows``): off is a 403 in words naming
   this computer and the switch, and nothing is started or kept.

A token never widens what a computer may do: the switch decides, every time.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, Request

from ..core.logging import get_logger
from . import allows as A

log = get_logger(__name__)

#: Every peer door's path starts so (``daemon/auth.PEER_DOOR_PATHS``).
PREFIX = "/peer/v1/"

#: The answer to a token no pairing here made.
UNKNOWN_TOKEN = "This computer does not know that token: it is not paired with the computer that sent it."
TOO_MANY = "too many tries from that address — wait a minute"


def _source(request: Request) -> str:
    """The TCP client's host (never anything the request says)."""
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "") if client else ""


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


async def refuse_unless(platform: Any, peer: Any, switch: str) -> None:
    """403 in words when ``switch`` is off for ``peer`` (a pairing already
    verified). The words name this computer, so the sender's chat says where
    to turn it on."""
    if A.may(peer, switch):
        return
    from .store import store_for

    try:
        here = await asyncio.to_thread(lambda: store_for(platform).this_name())
    except Exception:  # noqa: BLE001 — the refusal stands; only the name is missing
        here = ""
    raise HTTPException(
        status_code=403,
        detail=A.switch_off_words(switch, here, str(getattr(peer, "name", "") or "that computer")),
    )


async def peer_for(platform: Any, request: Request, switch: str | None = None) -> Any:
    """The pairing whose token ``request`` carries, with ``switch`` on (None:
    any paired computer — a door that only tells this computer something).
    Raises 429 / 401 / 403 in words, in the order the module says."""
    from ..agents.task_door import door_for
    from .store import store_for

    if not door_for(platform).address_ok(_source(request), "peer"):
        raise HTTPException(status_code=429, detail=TOO_MANY)
    token = _bearer(request)
    try:
        peer = await asyncio.to_thread(store_for(platform).verify, token) if token else None
    except Exception:  # noqa: BLE001 — a store that cannot be read lets nobody in by it
        log.warning("peer door: the paired computers could not be read", exc_info=True)
        peer = None
    if peer is None:
        raise HTTPException(status_code=401, detail=UNKNOWN_TOKEN)
    if switch is not None:
        await refuse_unless(platform, peer, switch)
    return peer
