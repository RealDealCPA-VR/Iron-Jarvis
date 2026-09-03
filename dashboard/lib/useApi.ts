"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";
import { useDaemon } from "./daemon";

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

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    get<T>(path)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
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

/** Poll a GET endpoint every `intervalMs`. */
export function usePolledApi<T>(
  path: string | null,
  intervalMs = 5000,
  deps: unknown[] = [],
): ApiState<T> {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (path === null) return;
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [path, intervalMs]);
  return useApi<T>(path, [tick, ...deps]);
}
