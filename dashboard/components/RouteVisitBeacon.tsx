"use client";

/**
 * One `POST /ui/visit` per page change (calm UI redesign S0), so the daemon
 * can count which surfaces are opened over time (`GET /ui/visits`). Fire and
 * forget: a failure is ignored, nothing is retried, nothing renders. The
 * daemon keeps only the first path segment — never an id or a query.
 */

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { post } from "@/lib/api";
import { takeVia } from "@/lib/visitVia";

/** Settle time, so a redirect or a fast double navigation counts once. */
export const VISIT_SETTLE_MS = 400;

export function RouteVisitBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (!pathname) return;
    const t = setTimeout(() => {
      post("/ui/visit", { route: pathname, via: takeVia() }).catch(() => {
        /* an older daemon (404) or a blip: usage counting is best-effort */
      });
    }, VISIT_SETTLE_MS);
    return () => clearTimeout(t);
  }, [pathname]);
  return null;
}
