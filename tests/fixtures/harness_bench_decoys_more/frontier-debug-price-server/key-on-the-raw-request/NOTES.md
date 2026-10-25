Keys the cache on the request as it came in (not normalized): "Gold" and "gold", "012" and "12" are computed twice.
Breaks PERFORMANCE.md: a price is identified by its four values "after normalisation"; two requests that normalise to the same values ask for the same price, computed once.
