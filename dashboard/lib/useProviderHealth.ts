"use client";

// Provider availability, polled from GET /health, for PREFLIGHT warnings —
// the point is to warn the user while they are CHOOSING a model and BEFORE
// they send a turn, not after the turn has already failed against a dead
// endpoint (the fleet-custom incident this exists for).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import type { Health } from "@/lib/types";

// Opt-in request timeout (see lib/api.ts): a hung fetch would otherwise pin
// the overlap guard forever and silently stop all future polls.
const HEALTH_TIMEOUT_MS = 10_000;

export interface ProviderHealthState {
  /** provider name → reachable. LAST-KNOWN map: kept across failed polls,
   *  because a daemon blip must not flash every provider "down". If the FIRST
   *  poll fails there is no last-known map — this stays `{}` (with
   *  `stale: true`), so every provider reads as UNKNOWN and PreflightNote
   *  stays silent. Deliberate: nothing was ever observed, so no provider can
   *  honestly be accused; a daemon that is down is the offline banner's job
   *  (lib/api maps a dead fetch to status 0), not this hook's. */
  byProvider: Record<string, boolean>;
  /** The daemon's current default provider ("" until the first poll lands). */
  defaultProvider: string;
  /** True until the FIRST poll settles (success or failure). */
  loading: boolean;
  /** True when the LATEST poll could not reach the daemon — byProvider is the
   *  last-known map, not live truth. Cleared by the next successful poll. */
  stale: boolean;
  /** Kick an immediate re-poll (no-op while one is already in flight). */
  refresh: () => void;
}

/**
 * Per-provider availability from GET /health.
 *
 * v1.230.0: inside a `DaemonProvider` (the whole app) this hook READS the
 * shared /health poll — the same payload the topbar ModelSwitcher's amber dot
 * and the offline banner use, so the three can never disagree and the window
 * carries ONE /health poll. It polls for itself ONLY when mounted outside a
 * provider (`provided === false`), at `intervalMs` (default 5 s).
 *
 * Guarantees (of the private poll):
 * - ONE interval per mount, cleaned up on unmount (no polling storm).
 * - Overlapping requests are skipped: an interval tick (or manual refresh)
 *   while a request is in flight does nothing.
 * - A fetch error KEEPS the last-known map and sets `stale` instead of
 *   emptying it — stale beats empty for a preflight indicator.
 */
export function useProviderHealth(intervalMs = 5_000): ProviderHealthState {
  const daemon = useDaemon();
  const shared = daemon.provided;
  const [byProvider, setByProvider] = useState<Record<string, boolean>>({});
  const [defaultProvider, setDefaultProvider] = useState("");
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);

  // Refs, not state: the guard must flip synchronously (state batching would
  // let two ticks both read "not in flight") and must not retrigger effects.
  const inFlight = useRef(false);
  const mounted = useRef(true);

  const refresh = useCallback(() => {
    if (inFlight.current) return; // overlap guard — one request at a time
    inFlight.current = true;
    get<Health>("/health", { timeoutMs: HEALTH_TIMEOUT_MS })
      .then((h) => {
        if (!mounted.current) return;
        const map: Record<string, boolean> = {};
        for (const p of h.providers ?? []) map[p.provider] = p.available;
        setByProvider(map);
        setDefaultProvider(h.default_provider ?? "");
        setStale(false);
      })
      .catch(() => {
        // Daemon unreachable (or a bad response): KEEP the last-known map —
        // clearing it would flash every provider "down" on a daemon blip —
        // but say honestly that it is no longer live truth.
        if (mounted.current) setStale(true);
      })
      .finally(() => {
        inFlight.current = false;
        if (mounted.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (shared) return; // the DaemonProvider's poll is the source — no second one
    mounted.current = true; // re-armed on StrictMode remount
    refresh();
    const id = setInterval(refresh, intervalMs);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [refresh, intervalMs, shared]);

  // The shared payload, mapped the same way. DaemonProvider keeps the last
  // successful /health across misses, so this is the same last-known map.
  const sharedMap = useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const p of daemon.health?.providers ?? []) map[p.provider] = p.available;
    return map;
  }, [daemon.health]);

  if (shared) {
    return {
      byProvider: sharedMap,
      defaultProvider: daemon.health?.default_provider ?? "",
      loading: daemon.checking,
      // The provider says offline after two consecutive misses (FP4); until
      // then the map is live truth as far as the app knows.
      stale: !daemon.checking && !daemon.online,
      refresh: daemon.refresh,
    };
  }
  return { byProvider, defaultProvider, loading, stale, refresh };
}
