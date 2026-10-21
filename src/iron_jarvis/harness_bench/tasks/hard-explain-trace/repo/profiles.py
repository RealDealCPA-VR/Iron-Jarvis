"""Which rules each kind of customer gets."""

from rules import RULES

PROFILES = {
    "retail": ["tax", "shipping", "small_order_fee", "coupon", "bulk_discount", "member_discount"],
    "wholesale": ["bulk_discount", "tax"],
}


def build_pipeline(profile, disabled=()):
    """The rule functions for ``profile``, minus ``disabled``, in run order."""
    names = [name for name in PROFILES[profile] if name not in disabled]
    return [RULES[name][1] for name in sorted(names, key=lambda name: RULES[name][0])]
