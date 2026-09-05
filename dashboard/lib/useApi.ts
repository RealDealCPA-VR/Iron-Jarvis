"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";
import { useDaemon } from "./daemon";
import { etagOf, isNotModified } from "./etag";
import { useDocumentVisible } from "./useDocumentVisible";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Runtime GET hook. `path === null` disables the fetch.
 * Errors are captured (never thrown) so a render can show an offline hint.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  // v1.226.0 (contract C7): when the daemon comes back (DaemonProvider's
  // `epoch` ticks on each offline->online edge) re-fetch ONLY if our last
  // error was status 0 — the request that died in the restart gap. A page
  // whose data loaded fine is left alone (no refetch storm on a transition),
  // and a real 4xx/5xx is not retried by a health flip either. Implemented as
  // an epoch->nonce edge rather than a raw dep so recovery itself (error
  // clearing after the retry) cannot trigger a second fetch.
  const { epoch } = useDaemon();
  const errorRef = useRef<ApiError | null>(null);
  errorRef.current = error;
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

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const held = etagRef.current && etagRef.current.path === path ? etagRef.current.etag : null;
    (held ? get<T>(path, { ifNoneMatch: held }) : get<T>(path))
      .then((d) => {
        if (cancelled) return;
        if (isNotModified(d)) {
          // Nothing changed: keep the data we hold (it is what the ETag named).
          setError(null);
          return;
        }
        setData(d);
        setError(null);
        const etag = etagOf(d);
        etagRef.current = etag ? { path, etag } : null;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e : new ApiError(String(e), 0));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  return { data, error, loading, reload };
}

/**
 * Poll a GET endpoint every `intervalMs` — while the document is VISIBLE.
 *
 * v1.230.0 (FP2): a hidden/minimised window issues no polls (the interval is
 * torn down, not merely ignored) and gets ONE refetch the moment it is visible
 * again, so the page is current when the user looks at it. An explicit
 * `reload()` and the daemon's offline->online epoch still fetch while hidden.
 */
export function usePolledApi<T>(
  path: string | null,
  intervalMs = 5000,
  deps: unknown[] = [],
): ApiState<T> {
  const [tick, setTick] = useState(0);
  const visible = useDocumentVisible();
  useEffect(() => {
    if (path === null || !visible) return;
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [path, intervalMs, visible]);
  // Refetch once on the hidden->visible edge (never on mount: the first
  // fetch is useApi's own, and a window that mounts hidden should stay quiet).
  const wasHiddenRef = useRef(false);
  useEffect(() => {
    if (!visible) {
      wasHiddenRef.current = true;
      return;
    }
    if (wasHiddenRef.current) {
      wasHiddenRef.current = false;
      setTick((t) => t + 1);
    }
  }, [visible]);
  return useApi<T>(path, [tick, ...deps]);
}
