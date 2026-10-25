"""How the client reaches the payments server.

A request and a response are plain dicts:

    request  = {"method": "POST", "path": "/charges",
                "headers": {"Idempotency-Key": "...", ...},
                "body": {"customer": "...", "amount_cents": 1250, "memo": "..."}}
    response = {"status": 200, "body": {...}}

``Transport.send(request)`` returns the response, or raises
``TransportTimeout`` when no response arrived.

``FakeTransport`` is an in-memory payments server that follows
``server_api.md`` exactly, including the network faults the real one suffers.
The tests use it; do not change it to make a test pass — it is the model of
the real server.
"""

from __future__ import annotations

import collections
import copy
import json

from .errors import TransportTimeout

#: How long the server remembers an Idempotency-Key (server_api.md).
KEY_LIFETIME_S = 24 * 60 * 60

#: The faults ``FakeTransport.fail_next`` accepts.
FAULTS = ("drop_request", "drop_response", "busy")


class Transport:
    """Sends one request to the payments server and returns its response."""

    def send(self, request: dict) -> dict:
        raise NotImplementedError


class FakeTransport(Transport):
    """An in-memory payments server (see ``server_api.md``), faults included.

    What it keeps, for tests to inspect:

    * ``requests`` — a copy of EVERY request ``send`` was given, in order,
      including the ones the "network" lost;
    * ``ledger`` — every charge the server MADE, in order: dicts with ``id``,
      ``customer``, ``amount_cents``, ``memo`` and ``idempotency_key`` (``None``
      when the request carried no key).

    Faults are scheduled per upcoming request with :meth:`fail_next`, which
    takes one entry per request, in order:

    * ``"drop_request"`` — the request is lost on its way: the server never
      sees it, and ``send`` raises ``TransportTimeout``;
    * ``"drop_response"`` — the server receives and PROCESSES the request (a
      charge may be made), but the response is lost: ``send`` raises
      ``TransportTimeout`` all the same;
    * ``"busy"`` — the server answers 503 without processing the request;
    * ``None`` — no fault for that request.

    ``decline(customer)`` makes every charge for that customer answer 402.
    ``advance(seconds)`` moves the server's clock (keys expire after 24 h).
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.ledger: list[dict] = []
        self.now = 0.0
        self._faults: collections.deque = collections.deque()
        self._declined: set[str] = set()
        self._keys: dict[str, tuple[str, dict, float]] = {}
        self._next_id = 1

    # -- test controls ---------------------------------------------------
    def fail_next(self, *faults: str | None) -> None:
        for fault in faults:
            if fault is not None and fault not in FAULTS:
                raise ValueError(f"unknown fault {fault!r}; use one of {FAULTS} or None")
            self._faults.append(fault)

    def decline(self, customer: str) -> None:
        self._declined.add(customer)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def charges_for(self, customer: str) -> list[dict]:
        """The charges the server made for ``customer``."""
        return [c for c in self.ledger if c["customer"] == customer]

    # -- the network -----------------------------------------------------
    def send(self, request: dict) -> dict:
        self.requests.append(copy.deepcopy(request))
        fault = self._faults.popleft() if self._faults else None
        if fault == "drop_request":
            raise TransportTimeout("no response from the payments server")
        if fault == "busy":
            return {"status": 503, "body": {"error": "busy", "message": "try again"}}
        response = self._serve(copy.deepcopy(request))
        if fault == "drop_response":
            raise TransportTimeout("no response from the payments server")
        return copy.deepcopy(response)

    # -- the server ------------------------------------------------------
    def _serve(self, request: dict) -> dict:
        if request.get("method") != "POST" or request.get("path") != "/charges":
            return {"status": 404, "body": {"error": "not_found", "message": "no such endpoint"}}
        headers = request.get("headers") or {}
        key = headers.get("Idempotency-Key") or None
        body = request.get("body") or {}
        fingerprint = json.dumps(body, sort_keys=True, default=str)
        if key is not None:
            self._forget_expired_keys()
            if key in self._keys:
                seen_fingerprint, first_response, _ = self._keys[key]
                if seen_fingerprint != fingerprint:
                    return {
                        "status": 409,
                        "body": {"error": "idempotency_conflict",
                                 "message": "this key was used for a different request"},
                    }
                return copy.deepcopy(first_response)
        response = self._charge(body, key)
        if key is not None and response["status"] != 400:
            self._keys[key] = (fingerprint, copy.deepcopy(response), self.now)
        return response

    def _forget_expired_keys(self) -> None:
        for key in [k for k, (_, _, at) in self._keys.items() if self.now - at >= KEY_LIFETIME_S]:
            del self._keys[key]

    def _charge(self, body: dict, key: str | None) -> dict:
        customer = body.get("customer")
        amount = body.get("amount_cents")
        memo = body.get("memo", "")
        if not isinstance(customer, str) or not customer:
            return _bad_request("customer must be a non-empty string")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return _bad_request("amount_cents must be a positive integer")
        if not isinstance(memo, str):
            return _bad_request("memo must be a string")
        if customer in self._declined:
            return {"status": 402, "body": {"error": "card_declined",
                                            "message": f"the card of {customer} was declined"}}
        charge = {
            "id": f"ch_{self._next_id:04d}",
            "customer": customer,
            "amount_cents": amount,
            "memo": memo,
            "idempotency_key": key,
        }
        self._next_id += 1
        self.ledger.append(charge)
        return {"status": 200, "body": {"id": charge["id"], "status": "succeeded",
                                        "customer": customer, "amount_cents": amount, "memo": memo}}


def _bad_request(message: str) -> dict:
    return {"status": 400, "body": {"error": "invalid_request", "message": message}}
