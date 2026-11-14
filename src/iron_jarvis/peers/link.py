"""Calls to the other computer: pair, check, unpair (v1.497.0).

Each call is ONE request with a total deadline (``asyncio.timeout``: httpx's
own timeout is per phase), no redirects followed, and an answer read up to a
small cap. Every failure comes back as plain words for the screen; a token is
never in them.

:data:`CLIENT` is the one seam: the tests hand it a client wired to a second
Iron Jarvis in the same process; the app leaves it None and a fresh
``httpx.AsyncClient`` is made per call.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from ..core.logging import get_logger
from ..daemon.auth import PEER_PAIR_PATH as PAIR_PATH
from ..daemon.auth import PEER_UNPAIR_PATH as UNPAIR_PATH

log = get_logger(__name__)

#: The pairing protocol's version, sent both ways.
PROTOCOL = 1

__all__ = ["PAIR_PATH", "UNPAIR_PATH", "PROTOCOL", "LinkFailed", "pair", "check", "unpair"]

#: What the Host guard answers for an address this computer does not answer
#: to (``daemon/auth``: ``_reject(..., "host not allowed")``).
HOST_REFUSED = "host not allowed"

#: Total deadlines.
PAIR_TIMEOUT_S = 15.0
CHECK_TIMEOUT_S = 6.0
UNPAIR_TIMEOUT_S = 8.0

#: The most of an answer read.
MAX_ANSWER_BYTES = 64 * 1024

#: Tests replace this with ``lambda: httpx.AsyncClient(transport=...)``.
CLIENT: Callable[[], Any] | None = None


def _client() -> Any:
    import httpx

    if CLIENT is not None:
        return CLIENT()
    return httpx.AsyncClient(timeout=PAIR_TIMEOUT_S, follow_redirects=False)


class LinkFailed(Exception):
    """The other computer could not be asked, or said no. ``words`` is the
    plain sentence for the screen; ``status`` the HTTP status (0 = none)."""

    def __init__(self, words: str, status: int = 0) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


def _detail(text: str) -> str:
    """The other computer's own ``detail`` sentence, masked and short."""
    from ..detections.redact import mask

    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = None
    detail = parsed.get("detail") if isinstance(parsed, dict) else ""
    if not isinstance(detail, str):
        return ""
    return " ".join(str(mask(detail)).split())[:240]


def _unreached(address: str, exc: BaseException | None, timed_out: bool, seconds: float) -> LinkFailed:
    if timed_out:
        return LinkFailed(
            f"The computer at {address} did not answer within {int(seconds)} seconds. Check that "
            "Iron Jarvis is open there, that it lets other computers reach it, and that the address is right."
        )
    return LinkFailed(
        f"No computer answered at {address}. Check the address, that Iron Jarvis is open there, and that "
        "it lets other computers reach it (Settings › Connections › Your other computers on that computer)."
    )


async def _request(method: str, url: str, *, address: str, token: str = "", body: Any = None,
                   seconds: float) -> tuple[int, str]:
    """One bounded request. Returns ``(status, text)``; raises LinkFailed when
    nothing answered."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    deadline = asyncio.timeout(seconds)
    exc: BaseException | None = None
    try:
        async with deadline:
            async with _client() as client:
                async with client.stream(method, url, json=body, headers=headers) as resp:
                    raw = bytearray()
                    async for chunk in resp.aiter_bytes():
                        raw += chunk
                        if len(raw) >= MAX_ANSWER_BYTES:
                            break
                    return int(resp.status_code), bytes(raw[:MAX_ANSWER_BYTES]).decode(
                        "utf-8", errors="replace"
                    )
    except TimeoutError as err:
        exc = err
        if deadline.expired():
            raise _unreached(address, exc, True, seconds) from None
    except LinkFailed:
        raise
    except Exception as err:  # noqa: BLE001 — refused / no route / DNS / anything
        exc = err
    log.info("paired computer at %s not reached (%s)", address, type(exc).__name__ if exc else "?")
    raise _unreached(address, exc, False, seconds) from None


def _refused(status: int, text: str, address: str) -> LinkFailed:
    """The other computer answered with a refusal: its own words where it
    gave them, else what the status means."""
    said = _detail(text)
    if status == 403 and said == HOST_REFUSED:
        return LinkFailed(
            f"The computer at {address} did not accept that address. Type one of the addresses its pairing "
            "screen shows, exactly as shown.", status
        )
    if status == 400 and not said:
        return LinkFailed(
            f"The computer at {address} did not accept that address. Type one of the addresses its pairing "
            "screen shows, exactly as shown.", status
        )
    if status in (403, 401) and said:
        return LinkFailed(said, status)
    if status == 404:
        return LinkFailed(
            f"The computer at {address} answered, but it cannot pair. Update Iron Jarvis there to the "
            "newest version, then try again.", status
        )
    if status == 429:
        return LinkFailed(said or "That computer is taking too many pairing tries. Wait a minute and try again.",
                          status)
    if said:
        return LinkFailed(said, status)
    return LinkFailed(f"The computer at {address} turned the pairing away (it answered {status}).", status)


async def pair(address: str, *, code: str, name: str, port: int, door_token: str,
               guess: str) -> dict[str, Any]:
    """Ask the computer at ``address`` to pair with this one. Returns its
    answer ``{name, door_token, seen_as}``; raises LinkFailed in words."""
    from .store import valid_token

    body = {
        "protocol": PROTOCOL,
        "code": code,
        "name": name,
        "port": int(port),
        "address": guess,
        "door_token": door_token,
    }
    status, text = await _request("POST", address.rstrip("/") + PAIR_PATH, address=address,
                                  body=body, seconds=PAIR_TIMEOUT_S)
    if status // 100 != 2:
        raise _refused(status, text, address)
    try:
        answer = json.loads(text)
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not valid_token(answer.get("door_token")):
        raise LinkFailed(
            f"The computer at {address} answered, but not as Iron Jarvis does. Check the address.", status
        )
    return {
        "name": str(answer.get("name") or ""),
        "door_token": str(answer["door_token"]),
        "seen_as": str(answer.get("seen_as") or ""),
    }


async def check(address: str, token: str) -> None:
    """Does the paired computer at ``address`` still answer, and still know
    this computer's token? Raises LinkFailed in words when not."""
    from .store import door_url

    status, text = await _request("GET", door_url(address), address=address, token=token,
                                  seconds=CHECK_TIMEOUT_S)
    if status == 200:
        return
    # 401 (it does not know the token) or the door's own 403 (it takes no
    # tasks from this computer): either way the pairing is gone there.
    if status == 401 or (status == 403 and _detail(text) != HOST_REFUSED):
        raise LinkFailed(
            "That computer no longer knows this one: the pairing was removed there. Remove it here and pair "
            "again.", status
        )
    raise _refused(status, text, address)


async def unpair(address: str, token: str) -> bool:
    """Tell the paired computer at ``address`` that this one removed the
    pairing, so it removes its side too. True when it did. Never raises."""
    try:
        status, _text = await _request("POST", address.rstrip("/") + UNPAIR_PATH, address=address,
                                       token=token, body={"protocol": PROTOCOL},
                                       seconds=UNPAIR_TIMEOUT_S)
    except LinkFailed:
        return False
    return status // 100 == 2
