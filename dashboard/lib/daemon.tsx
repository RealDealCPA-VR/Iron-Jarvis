"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  get,
  onNetworkError,
  onRequestErrorChange,
  onUnauthorizedChange,
} from "./api";
import type { Health } from "./types";

export interface DaemonState {
  /** True once a /health poll has succeeded; false when the daemon is offline. */
  online: boolean;
  /** True when a data request was rejected 401/403 (missing/stale token). The
   *  daemon is reachable but won't accept us until a valid token is entered. */
  unauthorized: boolean;
  /** True when a data request failed with a non-auth server error (4xx/5xx) — the
   *  page's data may be missing even though the daemon is online. */
  requestError: boolean;
  /** Latest /health payload, or null before the first successful poll. */
  health: Health | null;
  /** True until the first poll resolves (so we don't flash "offline" on load). */
  checking: boolean;
  /** v1.226.0 (contract C7): +1 on every offline->online transition. `useApi`
   *  re-fetches on it ONLY while its last error was status 0, so a page whose
   *  one GET died during a daemon restart heals itself when the daemon is
   *  back instead of showing "Daemon offline" until a manual reload. */
  epoch: number;
  /** Force an immediate re-poll. */
  refresh: () => void;
}

const DaemonContext = createContext<DaemonState | null>(null);

/**
 * One shared `/health` poll for the whole app. The offline banner and the
 * sidebar status dot both read from this so they never disagree.
 */
export function DaemonProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState(false);
  const [unauthorized, setUnauthorized] = useState(false);
  const [requestError, setRequestError] = useState(false);
  const [checking, setChecking] = useState(true);
  const [nonce, setNonce] = useState(0);
  const [epoch, setEpoch] = useState(0);
  const firstRef = useRef(true);
  // Last KNOWN reachability, kept in a ref so the poll (a closure) can detect
  // the offline->online edge without re-subscribing on every flip.
  const onlineRef = useRef(false);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  // A 401/403 (bad token) or a non-auth 4xx/5xx from ANY data request — the /health
  // poll can't see these (auth-exempt + narrow) — flips these on; the next good
  // response clears them.
  useEffect(() => onUnauthorizedChange(setUnauthorized), []);
  useEffect(() => onRequestErrorChange(setRequestError), []);
  // v1.226.0: ANY data request that failed to reach the daemon means we were
  // offline, whatever the 5s poll saw. Drop the known-online flag and re-poll
  // now, so the next good /health walks the offline->online edge (epoch +1 ->
  // the status-0 hooks refetch). Edge-guarded: while already offline every
  // failing request would otherwise restart the poll loop.
  useEffect(
    () =>
      onNetworkError(() => {
        if (!onlineRef.current) return;
        onlineRef.current = false;
        refresh();
      }),
    [refresh],
  );

  useEffect(() => {
    let cancelled = false;

    // v1.226.0: one place marks "reachable" so the epoch bumps on the EDGE only
    // (a steady online daemon polls every 5s and must not re-fetch every page).
    const markOnline = () => {
      setOnline(true);
      if (!onlineRef.current) {
        onlineRef.current = true;
        setEpoch((e) => e + 1);
      }
    };

    const poll = async () => {
      try {
        // Opt-in 8s timeout so a FROZEN-but-connected daemon (a blocking tool call)
        // trips "offline" instead of hanging the poll forever with a false-green dot.
        const h = await get<Health>("/health", { timeoutMs: 8000 });
        if (cancelled) return;
        setHealth(h);
        markOnline();
      } catch (err) {
        if (cancelled) return;
        // status 0 === network error === daemon unreachable.
        if (err instanceof ApiError && err.status === 0) {
          onlineRef.current = false;
          setOnline(false);
        } else {
          // Reachable but erroring — still "online" enough to not show the banner.
          markOnline();
        }
      } finally {
        if (!cancelled && firstRef.current) {
          firstRef.current = false;
          setChecking(false);
        }
      }
    };

    poll();
    const id = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [nonce]);

  return (
    <DaemonContext.Provider
      value={{ online, unauthorized, requestError, health, checking, epoch, refresh }}
    >
      {children}
    </DaemonContext.Provider>
  );
}

export function useDaemon(): DaemonState {
  const ctx = useContext(DaemonContext);
  if (ctx === null) {
    // Safe fallback if a component renders outside the provider.
    return {
      online: true,
      unauthorized: false,
      requestError: false,
      health: null,
      checking: true,
      epoch: 0,
      refresh: () => {},
    };
  }
  return ctx;
}
