Correct, written another way: single-flight per price — the first request for a key computes it OUTSIDE the global lock while later ones wait on that key's Event; different prices compute in parallel (the counter has its own lock).
Keeps every rule of PERFORMANCE.md; the _solve.py is the reference's (start, replay until clean, /shutdown).
