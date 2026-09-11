"use client";

/**
 * THE LAST ANSWER EACH GET PATH GAVE (v1.250.0, S-04).
 *
 * Every page started at `data: null, loading: true` on every mount, so going
 * back to Overview / Projects / Workflows — or reopening Chat, which makes
 * about ten catalog requests — showed grey placeholders and refetched
 * everything, even seconds after leaving. This module remembers the last
 * payload per path so a return visit RENDERS WHAT YOU SAW and revalidates
 * quietly behind it.
 *
 * IT LIVES OUTSIDE `lib/api.ts` ON PURPOSE. ~71 test files mock that module
 * wholesale (`vi.mock("@/lib/api")`), and a helper imported from there makes
 * every mocked `get` look like a failure — the same trap the v1.230.0 ETag
 * marker helpers hit.
 *
 * Memory-only and per window: nothing is written to disk, so a reload starts
 * clean and no client data outlives the session. Bounded by `MAX_ENTRIES`
 * (least-recently-read evicted first) so a long session cannot grow forever.
 */

export const MAX_ENTRIES = 120;

interface Entry {
  /** The payload exactly as the hook received it. */
  data: unknown;
  /** When it was stored (ms epoch) — read by `cachedAge` for diagnostics. */
  at: number;
  /** Bumped on every read so eviction can drop the coldest path. */
  seq: number;
}

const store = new Map<string, Entry>();
let clock = 0;

/** The last payload seen for `path`, or `undefined` when there is none. */
export function cachedGet<T>(path: string): T | undefined {
  const hit = store.get(path);
  if (!hit) return undefined;
  hit.seq = ++clock; // most recently used
  return hit.data as T;
}

/** Remember `data` as the answer for `path`. */
export function cacheSet(path: string, data: unknown): void {
  store.set(path, { data, at: Date.now(), seq: ++clock });
  if (store.size <= MAX_ENTRIES) return;
  // Evict the coldest entries until we are back under the cap.
  const byAge = [...store.entries()].sort((a, b) => a[1].seq - b[1].seq);
  for (const [key] of byAge.slice(0, store.size - MAX_ENTRIES)) store.delete(key);
}

/** How old the entry for `path` is in ms, or null when it is not cached. */
export function cachedAge(path: string): number | null {
  const hit = store.get(path);
  return hit ? Date.now() - hit.at : null;
}

/** Drop one path (a mutation invalidating its own list) or everything. */
export function cacheDrop(path?: string): void {
  if (path === undefined) store.clear();
  else store.delete(path);
}

/** Test seam: every suite starts with an empty cache. */
export function __resetApiCache(): void {
  store.clear();
  clock = 0;
}

/** How many paths are held (tests + diagnostics). */
export function cacheSize(): number {
  return store.size;
}
