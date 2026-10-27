"""Customers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    email: str


def parse_customer(row):
    """A Customer from the "customer" object of an order file.

    "id", "name" and "email" are required (spaces around them are dropped);
    ValueError when one is missing or empty, or the e-mail has no "@".
    """
    try:
        cid, name, email = (str(row[key]).strip() for key in ("id", "name", "email"))
    except KeyError as exc:
        raise ValueError(f"the customer has no {exc.args[0]!r}") from None
    if not cid or not name or "@" not in email:
        raise ValueError("a customer needs an id, a name and an e-mail address")
    return Customer(cid, name, email)
