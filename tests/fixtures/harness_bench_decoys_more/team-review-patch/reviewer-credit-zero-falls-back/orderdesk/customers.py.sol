"""Customers."""

from dataclasses import dataclass

#: The loyalty tiers, from the lowest.
TIERS = ("bronze", "silver", "gold")


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    email: str
    tier: str = "bronze"


def normalize_tier(value):
    """A tier as an order file writes it -> one of TIERS: any case, spaces
    around it dropped; None or "" is "bronze"; ValueError for anything else."""
    tier = str(value or "").strip().lower() or "bronze"
    if tier not in TIERS:
        raise ValueError(f"unknown tier {value!r} (one of {', '.join(TIERS)})")
    return tier


def parse_customer(row):
    """A Customer from the "customer" object of an order file.

    "id", "name" and "email" are required (spaces around them are dropped);
    ValueError when one is missing or empty, or the e-mail has no "@".
    "tier" is optional (see normalize_tier).
    """
    try:
        cid, name, email = (str(row[key]).strip() for key in ("id", "name", "email"))
    except KeyError as exc:
        raise ValueError(f"the customer has no {exc.args[0]!r}") from None
    if not cid or not name or "@" not in email:
        raise ValueError("a customer needs an id, a name and an e-mail address")
    return Customer(cid, name, email, normalize_tier(row.get("tier")))
