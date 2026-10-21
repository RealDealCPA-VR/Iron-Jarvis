"""The pricing rules.

Each rule takes ``(total, cart, customer)`` and returns the new total. A rule
is registered under a NAME with an ORDER: the pipeline runs the rules of the
active profile from the lowest order to the highest.
"""

RULES = {}


def rule(name, order):
    def register(fn):
        RULES[name] = (order, fn)
        return fn

    return register


# Registration happens when these modules are imported.
from . import discounts, fees, promotions  # noqa: E402,F401
