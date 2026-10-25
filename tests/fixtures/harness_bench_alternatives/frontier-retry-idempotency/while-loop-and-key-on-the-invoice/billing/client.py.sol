"""The payments client: charges customers through the payments server.

The server's API is described in ``server_api.md`` (next to this file).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from .errors import (
    BadRequest,
    IdempotencyConflict,
    PaymentDeclined,
    PaymentError,
    ServerBusy,
    TransportTimeout,
)
from .transport import Transport

USER_AGENT = "billing-client/2.3"


@dataclass(frozen=True)
class Charge:
    """A charge the server made."""

    id: str
    customer: str
    amount_cents: int
    memo: str


def _random_key() -> str:
    return uuid.uuid4().hex


class PaymentsClient:
    """Talks to the payments server through ``transport``.

    ``new_key`` makes a fresh Idempotency-Key (a random one by default).
    """

    def __init__(self, transport: Transport, *, new_key: Callable[[], str] | None = None) -> None:
        self._transport = transport
        self._new_key = new_key or _random_key

    def charge(self, customer: str, amount_cents: int, memo: str = "", *,
               idempotency_key: str | None = None) -> Charge:
        """Charge ``customer`` ``amount_cents`` (an int, in cents), with ``memo``
        on their statement, and return the server's :class:`Charge`.

        ``idempotency_key`` identifies this logical charge to the server
        (``server_api.md``, "Idempotency"); when it is not given, the client
        makes a fresh one.

        Raises ``PaymentDeclined`` (402), ``BadRequest`` (400),
        ``IdempotencyConflict`` (409), ``ServerBusy`` (503),
        ``TransportTimeout`` (no response) or ``PaymentError`` (anything else).
        """
        key = idempotency_key if idempotency_key else self._new_key()
        request = {
            "method": "POST",
            "path": "/charges",
            "headers": {"Idempotency-Key": key, "User-Agent": USER_AGENT},
            "body": {"customer": customer, "amount_cents": amount_cents, "memo": memo},
        }
        attempts_left = _ATTEMPTS
        while True:
            attempts_left -= 1
            try:
                response = self._transport.send(dict(request, headers=dict(request["headers"]),
                                                     body=dict(request["body"])))
            except TransportTimeout:
                if attempts_left <= 0:
                    raise
                continue
            if response.get("status") == 503 and attempts_left > 0:
                continue
            _raise_for_status(response)
            answer = response["body"]
            return Charge(id=answer["id"], customer=answer["customer"],
                          amount_cents=answer["amount_cents"], memo=answer["memo"])


#: server_api.md: at most 3 attempts for one charge, on a timeout or a 503 only.
_ATTEMPTS = 3


def _raise_for_status(response: dict) -> None:
    status = response.get("status")
    body = response.get("body") or {}
    message = body.get("message") or body.get("error") or f"status {status}"
    if status == 200:
        return
    if status == 400:
        raise BadRequest(message)
    if status == 402:
        raise PaymentDeclined(message)
    if status == 409:
        raise IdempotencyConflict(message)
    if status == 503:
        raise ServerBusy(message)
    raise PaymentError(f"unexpected answer from the payments server: {status} {message}")
