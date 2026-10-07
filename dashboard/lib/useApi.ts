"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";
import { cacheSet, cachedGet } from "./apiCache";
import { useDaemonEpoch } from "./daemonEpoch";
import { etagOf, isNotModified } from "./etag";
import { useDocumentVisible } from "./useDocumentVisible";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/* ---- One request per identical GET (v1.311.0, wave 3) -------------------- */

// v1.311.0 (duplicate-pollers-no-dedupe): the GETs in flight right now, keyed
// by path + the If-None-Match they carry. The bell and the Overview both
// polled /diagnostics, and two hooks mounted together each sent their own
// copy. A mount or a poll tick now JOINS a request for the same key that is
// already on the wire. The ETag is part of the key ON PURPOSE: a hook holding
// a different tag must never receive someone else's 304 (it would keep a
// payload the 304 did not name). An explicit reload() never joins — it is
// usually "I just changed something", and a request sent before the change
// would hand back the old answer.
//
// An entry lives only while some mounted hook is waiting on it (`waiters`):
// a request nobody is waiting for any more — an unmounted page, a test's
// cleanup — must not be joined by the next mount, or one hung GET would hang
// every later reader of its path.
interface Flight {
  promise: Promise<unknown>;
  waiters: number;
}
const inflight = new Map<string, Flight>();

function flightKey(path: string, ifNoneMatch: string | null): string {
  return ifNoneMatch ? `${path}\u0000${ifNoneMatch}` : path;
}

/** Issue (or, when `join`, share) the GET. Returns the promise plus the
 *  release the caller runs when it stops waiting. `avoid` is the promise the
 *  caller is ALREADY waiting on: a tick never re-joins its own earlier
 *  request, so one hung GET costs one tick, not the poll (each tick used to
 *  issue a fresh request, and that recovery is kept). */
function sharedGet<T>(
  path: string,
  ifNoneMatch: string | null,
  join: boolean,
  avoid: Promise<unknown> | null,
): { promise: Promise<T>; release: () => void } {
  const key = flightKey(path, ifNoneMatch);
  let flight = join ? inflight.get(key) : undefined;
  if (flight && flight.promise === avoid) flight = undefined;
  if (!flight) {
    // The call forms are unchanged — ~70 suites mock `get` and some assert
    // the exact arguments: one-argument unless we hold an ETag.
    const promise: Promise<unknown> = ifNoneMatch
      ? get<T>(path, { ifNoneMatch })
      : get<T>(path);
    const fresh: Flight = { promise, waiters: 0 };
    flight = fresh;
    inflight.set(key, fresh);
    const done = () => {
      if (inflight.get(key) === fresh) inflight.delete(key);
    };
    promise.then(done, done);
  }
  const f = flight;
  f.waiters += 1;
  let released = false;
  return {
    promise: f.promise as Promise<T>,
    release: () => {
      if (released) return;
      released = true;
      f.waiters -= 1;
      if (f.waiters <= 0 && inflight.get(key) === f) inflight.delete(key);
    },
  };
}

/** JSON of a payload, or null when it cannot be serialised (then it is
 *  always treated as changed — never wrongly as equal). */
function jsonOf(value: unknown): string | null {
  try {
    const s = JSON.stringify(value);
    return typeof s === "string" ? s : null;
  } catch {
    return null;
  }
}

/* ---- The hook -------------------------------------------------------------- */

/**
 * Runtime GET hook. `path === null` disables the fetch.
 * Errors are captured (never thrown) so a render can show an offline hint.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  // A one-shot read never polls, so it never subscribes to visibility either
  // (a tab switch must not re-render every data-fetching component).
  return useApiCore<T>(path, deps, null, true);
}

/**
 * The one implementation behind `useApi` and `usePolledApi`. `intervalMs`
 * null = no polling; `visible` is the document's visibility (usePolledApi
 * reads it; a one-shot `useApi` passes true and never subscribes).
 *
 * v1.311.0 (wave 3, useapi-poll-double-render): a background poll tick is no
 * longer React state. It used to be: the tick rendered the host, the refetch
 * it triggered flipped `loading` (a second render, and a spinner over held
 * data), and the answer — a NEW object even when nothing changed — rendered a
 * third time. The Overview re-rendered ~64 times a minute while idle. Now the
 * interval calls `revalidate` directly, which never touches `loading`, and an
 * answer equal (by JSON) to the payload held for that path keeps the held
 * reference and calls no setter at all. Unchanged tick: zero renders. Changed
 * tick: one. Every setter below is guarded by a ref so a same-value update is
 * never queued (a queued one can still run the component function).
 */
function useApiCore<T>(
  path: string | null,
  deps: unknown[],
  intervalMs: number | null,
  visible: boolean,
): ApiState<T> {
  // v1.250.0 (S-04): start from the last answer this path gave, so a return
  // visit renders what the user saw instead of a grey placeholder, and the
  // refetch below revalidates behind it. First visit: null, exactly as before.
  const [data, setData] = useState<T | null>(
    () => (path === null ? null : cachedGet<T>(path) ?? null),
  );
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  // What this hook HOLDS, mirrored in refs so the guards below can compare
  // without a render: the payload, the path it answers, and its JSON
  // (computed lazily — a seeded payload is only serialised if a tick needs to
  // compare against it).
  const heldRef = useRef<{ path: string | null; data: T | null; json: string | null | undefined }>({
    path: data === null ? null : path,
    data,
    json: undefined,
  });
  const errorRef = useRef<ApiError | null>(error);
  const loadingRef = useRef<boolean>(loading);

  const putError = useCallback((e: ApiError | null) => {
    const cur = errorRef.current;
    if (cur === e) return;
    // The same failure again (a poll against a daemon that keeps answering
    // 500) keeps the held error object: nothing new to render.
    if (cur && e && cur.status === e.status && cur.message === e.message) return;
    errorRef.current = e;
    setError(e);
  }, []);
  const putLoading = useCallback((v: boolean) => {
    if (loadingRef.current === v) return;
    loadingRef.current = v;
    setLoading(v);
  }, []);

  // v1.226.0 (contract C7): when the daemon comes back (DaemonProvider's
  // `epoch` ticks on each offline->online edge) re-fetch ONLY if our last
  // error was status 0 — the request that died in the restart gap. A page
  // whose data loaded fine is left alone (no refetch storm on a transition),
  // and a real 4xx/5xx is not retried by a health flip either. Implemented as
  // an epoch->nonce edge rather than a raw dep so recovery itself (error
  // clearing after the retry) cannot trigger a second fetch.
  // v1.311.0: read from its own context — see lib/daemonEpoch.ts.
  const epoch = useDaemonEpoch();
  const seenEpochRef = useRef(epoch);
  useEffect(() => {
    if (epoch === seenEpochRef.current) return;
    seenEpochRef.current = epoch;
    if (errorRef.current && errorRef.current.status === 0) setNonce((n) => n + 1);
  }, [epoch]);

  // v1.230.0 (FP3): the ETag of the payload this hook currently HOLDS, with
  // the path it came from. Sent as If-None-Match on the next fetch of the same
  // path, so a poll whose answer has not changed costs a 304 and no body
  // (/sessions was 60 KB every 5 s for a six-row widget). Only a path that
  // emits an ETag ever fills this; everyone else keeps one-argument `get`.
  const etagRef = useRef<{ path: string; etag: string } | null>(null);

  // Which fetches may still land. `gen` moves on every path/deps/reload
  // change and on unmount (the old effect's `cancelled`); `seq` orders the
  // fetches of one generation so an older answer arriving late never
  // overwrites a newer one. `pending` is the request this hook waits on now.
  const genRef = useRef(0);
  const seqRef = useRef(0);
  const appliedSeqRef = useRef(0);
  const pendingRef = useRef<{ promise: Promise<unknown>; release: () => void } | null>(null);
  // The path this hook last fetched: a later fetch of the SAME path from the
  // effect is a reload or a deps change (fresh request); a first fetch of a
  // path (a mount, a path switch) may join one already in flight.
  const fetchedPathRef = useRef<string | null>(null);

  /** One GET of `p`. `join`: may share an identical request in flight. */
  const fetchOnce = useCallback(
    (p: string, join: boolean) => {
      // The tag of the payload this hook HOLDS — its own from a previous
      // fetch, or, on a first render seeded from the cache (v1.250.0, S-04),
      // that payload's tag. Never a tag keyed by path alone: sending one for a
      // payload we do not hold would 304 us into stale data (v1.230.0, FP3).
      if (!etagRef.current || etagRef.current.path !== p) {
        const seeded = cachedGet<T>(p);
        const seededTag = seeded === undefined ? null : etagOf(seeded);
        etagRef.current = seededTag ? { path: p, etag: seededTag } : null;
      }
      const held = etagRef.current && etagRef.current.path === p ? etagRef.current.etag : null;
      const gen = genRef.current;
      const seq = ++seqRef.current;
      const prev = pendingRef.current;
      const req = sharedGet<T>(p, held, join, prev ? prev.promise : null);
      // A newer request supersedes the one we were waiting on (its answer, if
      // it still lands first, is applied; a late one is dropped by `seq`).
      if (prev) prev.release();
      pendingRef.current = req;
      const live = () => gen === genRef.current && seq > appliedSeqRef.current;
      const finish = () => {
        if (pendingRef.current === req) {
          pendingRef.current = null;
          req.release();
        }
      };
      req.promise
        .then((d) => {
          if (!live()) return;
          appliedSeqRef.current = seq;
          if (isNotModified(d)) {
            // Nothing changed: keep the data we hold (it is what the ETag named).
            putError(null);
            return;
          }
          const h = heldRef.current;
          if (h.path === p && h.data !== null) {
            if (h.json === undefined) h.json = jsonOf(h.data);
            const json = jsonOf(d);
            if (json !== null && json === h.json) {
              // Equal answer: keep the HELD reference (dependent useMemos do
              // not recompute) and render nothing. Its tag still moves on.
              putError(null);
              const etag = etagOf(d);
              if (etag) etagRef.current = { path: p, etag };
              return;
            }
            heldRef.current = { path: p, data: d, json };
          } else {
            heldRef.current = { path: p, data: d, json: undefined };
          }
          setData(d);
          putError(null);
          cacheSet(p, d);
          const etag = etagOf(d);
          etagRef.current = etag ? { path: p, etag } : null;
        })
        .catch((e: unknown) => {
          if (!live()) return;
          appliedSeqRef.current = seq;
          putError(e instanceof ApiError ? e : new ApiError(String(e), 0));
        })
        .finally(() => {
          finish();
          if (gen === genRef.current) putLoading(false);
        });
    },
    [putError, putLoading],
  );

  // The foreground fetch: mount, a path switch, a deps change, reload(), the
  // epoch retry. These report `loading` exactly as before — switching path
  // must still say "loading" while the new path is pending, or the OLD path's
  // data would read as settled (the v1.311.0 verifier's trap).
  useEffect(() => {
    genRef.current += 1;
    if (path === null) {
      putLoading(false);
      return;
    }
    putLoading(true);
    const join = fetchedPathRef.current !== path;
    fetchedPathRef.current = path;
    fetchOnce(path, join);
    return () => {
      genRef.current += 1;
      const pending = pendingRef.current;
      pendingRef.current = null;
      if (pending) pending.release();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  // The background revalidation a poll tick runs: same request, no `loading`.
  const pathRef = useRef(path);
  pathRef.current = path;
  const revalidate = useCallback(() => {
    const p = pathRef.current;
    if (p !== null) fetchOnce(p, true);
  }, [fetchOnce]);

  // Polling (usePolledApi) — while the document is VISIBLE.
  useEffect(() => {
    if (intervalMs === null || path === null || !visible) return;
    const id = setInterval(revalidate, intervalMs);
    return () => clearInterval(id);
  }, [path, intervalMs, visible, revalidate]);
  // Refetch once on the hidden->visible edge (never on mount: the first
  // fetch is the effect's own, and a window that mounts hidden should stay
  // quiet).
  const wasHiddenRef = useRef(false);
  useEffect(() => {
    if (intervalMs === null) return;
    if (!visible) {
      wasHiddenRef.current = true;
      return;
    }
    if (wasHiddenRef.current) {
      wasHiddenRef.current = false;
      revalidate();
    }
  }, [visible, intervalMs, revalidate]);

  return { data, error, loading, reload };
}

/**
 * Poll a GET endpoint every `intervalMs` — while the document is VISIBLE.
 *
 * v1.230.0 (FP2): a hidden/minimised window issues no polls (the interval is
 * torn down, not merely ignored) and gets ONE refetch the moment it is visible
 * again, so the page is current when the user looks at it. An explicit
 * `reload()` and the daemon's offline->online epoch still fetch while hidden.
 *
 * v1.311.0: a tick is a background revalidation — it never flips `loading`,
 * and an unchanged answer renders nothing (see `useApiCore`).
 */
export function usePolledApi<T>(
  path: string | null,
  intervalMs = 5000,
  deps: unknown[] = [],
): ApiState<T> {
  const visible = useDocumentVisible();
  return useApiCore<T>(path, deps, intervalMs, visible);
}
