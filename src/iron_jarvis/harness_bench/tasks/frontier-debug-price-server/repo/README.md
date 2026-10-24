# Price server

`priceserver.py` answers price requests over HTTP for the shop's front end.
`pricing.py` computes the prices from the price list in `catalog.py`.

## Running it

    python priceserver.py --port 0

- It listens on 127.0.0.1; `--port 0` picks any free port.
- It appends the line `listening <port>` to `server.log` (in this folder) and
  prints the same line.
- It runs until it gets `GET /shutdown`: it answers `{"stopping": true}`,
  stops, and appends the line `stopped` to `server.log`. That is the only way
  to stop it cleanly.

## What it answers

- `GET /price?sku=A100&tier=gold&currency=EUR&qty=12` — 200 and
  `{"sku": "A100", "tier": "gold", "currency": "EUR", "qty": 12, "unit": "...", "total": "..."}`.
  `tier` defaults to `regular`, `currency` to `USD` and `qty` to 1. Codes are
  case-insensitive. A request the price list cannot answer (an unknown sku,
  tier or currency, a bad quantity) gets 400 and `{"error": "..."}`.
- `GET /debug/cache` — the price cache: `{"size": ..., "keys": [...], "compute_calls": ...}`
  (`compute_calls` is `pricing.COMPUTE_CALLS`).
- `GET /debug/version` — `{"version": "<sha256>", "files": [...]}`: the
  sha256 of `priceserver.py`, `pricing.py` and `catalog.py` (their contents one
  after another, in that order, with CRLF line endings read as LF) as they
  were when this server STARTED. A server keeps running the code it started
  with: after a change to the code, restart it.
- `GET /shutdown` — stop (see above).

## How a price is computed

    unit  = base price x (1 - tier discount) x exchange rate x (1 - quantity discount)
    total = unit x qty

`unit` is rounded half-to-even to the currency's decimal places (JPY has
none), and `total` is rounded the same way. The numbers are in `catalog.py`.
Computing a price is expensive: `PERFORMANCE.md` says what the cache in
`pricing.py` must do about that.

## Replaying traffic

`traffic.jsonl` is a recording of real requests, one JSON object per line,
with the answer each one must get. With the server running,

    python replay.py

sends them to the server in order (on the port from the last `listening` line
of `server.log`), compares every answer with the recorded one, and writes
`replay_report.json`:

    {"server_version": "<the server's /debug/version>", "requests": 41,
     "mismatches": 0, "first_mismatches": []}

It exits with 1 when any answer was wrong.
