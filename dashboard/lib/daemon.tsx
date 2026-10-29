"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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
// v1.311.0: the epoch rides its own context (see ./daemonEpoch for why it
// lives in a module of its own).
import { DaemonEpochContext } from "./daemonEpoch";
import { useDocumentVisible } from "./useDocumentVisible";

/** /health cadence while the document is hidden (v1.230.0, FP2). A minimised
 *  window still learns about an outage — six times a minute less often. */
export const HIDDEN_HEALTH_INTERVAL_MS = 30_000;

/** v1.406.0 (part 6): how long after the page loads a /health that cannot
 *  reach Iron Jarvis is read as "still starting" rather than an outage — the
 *  desktop app's own start-up gate (STARTUP_TIMEOUT_MS in desktop/main.js).
 *  Only before the FIRST answer: once Iron Jarvis has answered, a miss is an
 *  outage at once, exactly as before. */
export const STARTING_GRACE_MS = 90_000;

/** While starting, /health is asked again this soon (not every 5 s), so the
 *  page lands the moment Iron Jarvis answers. */
export const STARTING_POLL_MS = 1_000;

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
  /** True until the first poll resolves (so we don't flash "offline" on load).
   *  v1.406.0 (part 6): also true while `starting` — every surface that waits
   *  for `!checking` before saying "offline" stays quiet during a start. */
  checking: boolean;
  /** v1.406.0 (part 6): Iron Jarvis has never answered this page yet and the
   *  page is younger than STARTING_GRACE_MS — it is starting, not down. The
   *  banner says "Starting Iron Jarvis…" instead of "isn't responding". */
  starting: boolean;
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
  const [starting, setStarting] = useState(false);
  // v1.406.0 (part 6): has /health EVER answered this page, and when it
  // loaded — a miss before the first answer, inside the grace, is a start.
  const everOnlineRef = useRef(false);
  const loadedAtRef = useRef(Date.now());
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
  // v1.311.0 (wave 3, daemon-ctx-rerender-storm): the JSON of the /health
  // payload we hold. /health is effectively static (version, defaults,
  // providers), yet every 5 s poll stored a brand-new object, so every
  // consumer — the whole ChatPage included — re-rendered twelve times a
  // minute for nothing. An equal answer now keeps the held object.
  const healthJsonRef = useRef<string | null>(null);

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
      everOnlineRef.current = true;
      setStarting(false);
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
    let startingTimer: number | undefined;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      const seq = ++seqRef.current;
      const latest = () => !cancelled && seq === seqRef.current;
      let confirm = false;
      let stillStarting = false;
      try {
        // Opt-in 8s timeout so a FROZEN-but-connected daemon (a blocking tool call)
        // trips "offline" instead of hanging the poll forever with a false-green dot.
        const h = await get<Health>("/health", { timeoutMs: 8000 });
        if (!latest()) return;
        missesRef.current = 0;
        let json: string | null = null;
        try {
          json = JSON.stringify(h);
        } catch {
          json = null; // unserialisable (never from a real daemon): always store
        }
        if (json === null || json !== healthJsonRef.current) {
          healthJsonRef.current = json;
          setHealth(h);
        }
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
          } else if (
            !everOnlineRef.current &&
            Date.now() - loadedAtRef.current < STARTING_GRACE_MS
          ) {
            // v1.406.0 (part 6): never answered yet, page just loaded — Iron
            // Jarvis is STARTING. `checking` stays true, so nothing says
            // "offline"; the banner says "Starting Iron Jarvis…".
            stillStarting = true;
            setStarting(true);
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
        if (!cancelled && firstRef.current && !stillStarting) {
          firstRef.current = false;
          setStarting(false);
          setChecking(false);
        }
      }
      if (confirm && !cancelled) void poll();
      if (stillStarting && !cancelled) {
        window.clearTimeout(startingTimer);
        startingTimer = window.setTimeout(() => void poll(), STARTING_POLL_MS);
      }
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
      window.clearTimeout(startingTimer);
    };
  }, [nonce, visible]);

  // v1.311.0: memoised, so the provider re-rendering for an unrelated reason
  // (a bailed-out same-value setOnline) never hands consumers a new object.
  const value = useMemo<DaemonState>(
    () => ({
      online,
      unauthorized,
      requestError,
      health,
      checking,
      starting,
      epoch,
      refresh,
      provided: true,
    }),
    [online, unauthorized, requestError, health, checking, starting, epoch, refresh],
  );

  return (
    <DaemonEpochContext.Provider value={epoch}>
      <DaemonContext.Provider value={value}>{children}</DaemonContext.Provider>
    </DaemonEpochContext.Provider>
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
      starting: false,
      epoch: 0,
      refresh: () => {},
      provided: false,
    };
  }
  return ctx;
}
