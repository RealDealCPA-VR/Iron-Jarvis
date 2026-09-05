// v1.230.0 (audit FP3): the ETag that rides with a parsed payload, and the
// marker `api()` resolves to on a 304.
//
// A module of its own ON PURPOSE: the dashboard's tests mock `@/lib/api`
// wholesale (71 files), and `useApi` must keep working against a mock that has
// never heard of ETags — importing these from `./api` made every mocked `get`
// look like a failed request. Nothing here touches the network.

/** What `api()` resolves to on a 304 — ONLY possible when the caller passed
 *  `ifNoneMatch`. A marker, not an ApiError: "nothing changed" is a success the
 *  caller answers by keeping what it already holds. */
export const NOT_MODIFIED: unique symbol = Symbol("ij:not-modified");
export function isNotModified(value: unknown): value is typeof NOT_MODIFIED {
  return value === NOT_MODIFIED;
}

// The ETag of the response a parsed payload came from, keyed by the payload
// object itself. Per-RESPONSE by construction (a path-keyed table would let
// hook A send hook B's newer tag and 304 itself into stale data), and invisible
// to every caller that does not ask — `get(path)` calls stay one-argument.
const ETAGS = new WeakMap<object, string>();

/** Record the ETag `payload` (a parsed JSON object) arrived with. */
export function rememberEtag(payload: unknown, etag: string | null): void {
  if (etag && payload !== null && typeof payload === "object") ETAGS.set(payload as object, etag);
}

/** ETag the daemon sent with `payload` (a `get()` result), or null. */
export function etagOf(payload: unknown): string | null {
  return payload !== null && typeof payload === "object"
    ? (ETAGS.get(payload as object) ?? null)
    : null;
}
