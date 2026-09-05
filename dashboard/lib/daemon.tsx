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
import { useDocumentVisible } from "./useDocumentVisible";

/** /health cadence while the document is hidden (v1.230.0, FP2). A minimised
 *  window still learns about an outage — six times a minute less often. */
export const HIDDEN_HEALTH_INTERVAL_MS = 30_000;

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
  /** v1.230.0: true when a DaemonProvider is mounted above the caller — i.e.
   *  `health` is the app's ONE shared /health poll. The fallback object says
   *  false, and a hook that needs /health then polls for itself. */
  provided: boolean;
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
  // v1.230.0 (FP4): the poll that was ISSUED last is the only one allowed to
  // say online/offline. A poll that timed out after a newer one was issued
  // (refresh() restarts the loop) is stale and its verdict is dropped.
  const seqRef = useRef(0);
  // Consecutive status-0 misses while we believed the daemon online. ONE slow
  // /health (>8 s) on a healthy daemon used to commit an offline render (the
  // banner flashed ~0.5 s) and bump the epoch (every status-0 page refetched).
  // Now the first miss only triggers an immediate confirmation poll; the banner
  // needs two misses in a row.
  const missesRef = useRef(0);
  // v1.230.0 (FP2): stretch to 30 s while the window is hidden. The loop below
  // restarts on the edge; only the VISIBLE edge (and an explicit refresh) polls
  // at once — going hidden must not cost a request.
  const visible = useDocumentVisible();
  const lastNonceRef = useRef(0);

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

    // v1.230.0 (FP4): in-flight guard. A 5 s tick landing while the previous
    // /health is still waiting on its 8 s timeout used to issue a SECOND request
    // onto the same (possibly busy) daemon; now the tick is skipped.
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      const seq = ++seqRef.current;
      const latest = () => !cancelled && seq === seqRef.current;
      let confirm = false;
      try {
        // Opt-in 8s timeout so a FROZEN-but-connected daemon (a blocking tool call)
        // trips "offline" instead of hanging the poll forever with a false-green dot.
        const h = await get<Health>("/health", { timeoutMs: 8000 });
        if (!latest()) return;
        missesRef.current = 0;
        setHealth(h);
        markOnline();
      } catch (err) {
        if (!latest()) return;
        // status 0 === network error === daemon unreachable.
        if (err instanceof ApiError && err.status === 0) {
          missesRef.current += 1;
          if (onlineRef.current && missesRef.current < 2) {
            // First miss on a daemon we believe online: confirm before saying
            // anything. A healthy daemon answers the re-poll at once and nothing
            // renders; a dead one fails again and the second miss is honest.
            confirm = true;
          } else {
            onlineRef.current = false;
            setOnline(false);
          }
        } else {
          // Reachable but erroring — still "online" enough to not show the banner.
          missesRef.current = 0;
          markOnline();
        }
      } finally {
        inFlight = false;
        if (!cancelled && firstRef.current) {
          firstRef.current = false;
          setChecking(false);
        }
      }
      if (confirm && !cancelled) void poll();
    };

    const kicked = lastNonceRef.current !== nonce;
    lastNonceRef.current = nonce;
    // First mount, a refresh(), or the hidden->visible edge: poll now. The
    // visible->hidden edge only re-arms the slower interval.
    if (visible || kicked || seqRef.current === 0) poll();
    const id = setInterval(poll, visible ? 5000 : HIDDEN_HEALTH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [nonce, visible]);

  return (
    <DaemonContext.Provider
      value={{
        online,
        unauthorized,
        requestError,
        health,
        checking,
        epoch,
        refresh,
        provided: true,
      }}
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
      provided: false,
    };
  }
  return ctx;
}
