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

#: At most this many attempts for one charge (server_api.md, "retries").
MAX_ATTEMPTS = 3


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
        body = {"customer": customer, "amount_cents": amount_cents, "memo": memo}
        # ONE key per logical charge, made before the first attempt: every
        # retry must carry it, or a lost RESPONSE (the server did charge)
        # followed by a retry would charge the customer a second time.
        key = idempotency_key or self._new_key()
        last_error: PaymentError | None = None
        for _attempt in range(MAX_ATTEMPTS):
            try:
                answer = self._post("/charges", body, idempotency_key=key)
            except (TransportTimeout, ServerBusy) as exc:
                last_error = exc
                continue
            return Charge(
                id=answer["id"],
                customer=answer["customer"],
                amount_cents=answer["amount_cents"],
                memo=answer["memo"],
            )
        assert last_error is not None
        raise last_error

    def _post(self, path: str, body: dict, *, idempotency_key: str) -> dict:
        """Send one POST and return the body of a 200 answer; raise for anything else."""
        headers = {
            "Idempotency-Key": idempotency_key,
            "User-Agent": USER_AGENT,
        }
        request = {"method": "POST", "path": path, "headers": headers, "body": dict(body)}
        response = self._transport.send(request)
        _raise_for_status(response)
        return response["body"]


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
