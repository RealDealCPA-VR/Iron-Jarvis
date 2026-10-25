The reference with one change: the rates reader reuses the transactions file's currency
helper WITH its default, so an empty `base` or `currency` cell in a rates file reads as USD.
Breaks SPEC.md 7.5: "base and currency: codes in any case; anything else is bad currency
'…' (an empty cell too)."
