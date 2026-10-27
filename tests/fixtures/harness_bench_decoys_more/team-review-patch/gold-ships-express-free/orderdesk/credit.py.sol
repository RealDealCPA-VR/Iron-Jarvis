"""Store credit (from returns and goodwill gestures), spent on an order."""


def apply_store_credit(amount_cents, credit_cents):
    """Pay ``amount_cents`` with the customer's store credit, as
    ``(used_cents, left_to_pay_cents)``. The credit pays as much of the amount
    as it can: never more than the amount and never more than the credit;
    whatever it does not cover is left to pay. An amount of 0 or less uses no
    credit and leaves nothing to pay. A negative credit is a ValueError."""
    if credit_cents < 0:
        raise ValueError(f"store credit cannot be negative: {credit_cents}")
    amount = max(amount_cents, 0)
    used = min(amount, credit_cents)
    return used, amount - used
