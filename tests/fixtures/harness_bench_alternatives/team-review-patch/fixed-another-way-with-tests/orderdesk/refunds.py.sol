"""Refunds and the loyalty points they take back."""


def points_to_take_back(points_earned, refunded_cents, paid_cents):
    """The points a refund takes back: the order's points in proportion to the
    refunded part of what was paid, rounded UP to a whole point, and never more
    than the order earned. ``paid_cents`` must be more than 0."""
    if paid_cents <= 0:
        raise ValueError("paid_cents must be more than 0")
    if refunded_cents <= 0:
        return 0
    share = -(-points_earned * refunded_cents // paid_cents)
    return min(share, points_earned)
