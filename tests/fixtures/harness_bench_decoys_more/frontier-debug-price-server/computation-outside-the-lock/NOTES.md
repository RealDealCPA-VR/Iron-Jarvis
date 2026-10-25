Normalized key, but the expensive computation runs outside the lock (check, compute, setdefault), so simultaneous requests for one price each compute it.
Breaks PERFORMANCE.md: "two requests for the same price that arrive at the same moment must still cost ONE computation, never two".
