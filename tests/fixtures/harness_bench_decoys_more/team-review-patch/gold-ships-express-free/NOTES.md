Brings the whole change in, fixes its three wrong edits, and keeps the
project's newer features, but merges `shipping_cents` with the gold rule
FIRST: a gold order of $50.00 or more ships free even when it ships express.
The project's rule (its docstring and README) is that express shipping always
costs $12.99, whatever the order: it is never free. Every test passes (none
ships a gold order express). The hidden check must fail it.
