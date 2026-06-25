"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, get } from "./api";

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
