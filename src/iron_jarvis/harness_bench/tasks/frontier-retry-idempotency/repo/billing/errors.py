"""The errors the payments client raises."""

from __future__ import annotations


class PaymentError(Exception):
    """A charge failed. The more specific classes below say how."""


class TransportTimeout(PaymentError):
    """No response arrived from the server.

    The request may have been lost on its way to the server (nothing
    happened), or the server may have processed it and its RESPONSE was lost
    (the charge was made). The client cannot tell which.
    """


class ServerBusy(PaymentError):
    """The server answered 503: it was overloaded and did not process the request."""


class PaymentDeclined(PaymentError):
    """The server answered 402: the customer's card was declined."""


class IdempotencyConflict(PaymentError):
    """The server answered 409: the Idempotency-Key was already used for a different request."""


class BadRequest(PaymentError):
    """The server answered 400: the request itself is wrong (a bad amount, no customer, ...)."""
