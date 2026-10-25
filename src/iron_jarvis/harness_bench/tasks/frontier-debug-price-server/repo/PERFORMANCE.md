# Performance: the price cache

Computing a price is expensive: in production `_compute_price` asks the
pricing engine, which takes about a second for each price. Here the engine is
simulated, but the rule is the same:

**While the server runs, each distinct price is computed exactly once.**

What that means, precisely:

- A price is identified by EVERYTHING it depends on: the sku, the customer's
  tier, the currency and the quantity — after normalisation (`normalize()` in
  `pricing.py`). Codes are case-insensitive and spaces around them do not
  matter (`" a100 "` is `A100`, `Gold` is `gold`, `eur` is `EUR`), and the
  quantity is a whole number (`"05"` is 5). Two requests that normalise to the
  same four values ask for the same price; two requests that differ in any of
  the four ask for different prices.
- `pricing.COMPUTE_CALLS` counts the calls of `_compute_price`. After any
  sequence of requests it must equal the number of distinct prices that were
  asked for — never more (a price computed twice) and never less (a price that
  was not computed for its own request). Requests that are refused (an unknown
  sku, a bad quantity) do not count.
- The results stay in a cache in memory for as long as the server runs: there
  is no expiry, because prices change only when the code or `catalog.py`
  changes, and that means a restart.
- The server answers on several threads at once (it is a
  `ThreadingHTTPServer`): two requests for the same price that arrive at the
  same moment must still cost ONE computation, never two.

Load tests: start the server with the environment variable
`PRICING_SLOW_MS=<milliseconds>` and every computation also waits that long,
like the real engine does.

`GET /debug/cache` shows the cache: its size, its keys and `compute_calls`.
