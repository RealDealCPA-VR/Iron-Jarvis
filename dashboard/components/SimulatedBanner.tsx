"use client";

import Link from "next/link";
import { Cable, TriangleAlert } from "lucide-react";
import { useDaemon } from "@/lib/daemon";

/**
 * A slim, PERSISTENT (deliberately non-dismissable) strip shown whenever the
 * daemon is online but no real AI provider is connected — i.e. every reply is
 * fabricated by the offline mock model. This is the product's biggest trust
 * hazard, so unlike the onboarding checklist there is no way to hide it; the
 * only way to make it go away is to actually connect a model.
 *
 * Show/hide contract:
 *  - hidden until the first /health poll resolves (no flash on load)
 *  - hidden while the daemon is offline (DaemonBanner owns that state)
 *  - hidden as soon as any provider reports `available: true` — /health
 *    already filters the mock out of `providers`, so "no available entry"
 *    is exactly "simulated mode".
 */
export function SimulatedBanner() {
  const { online, checking, health } = useDaemon();

  if (checking || !online || !health) return null;

  const hasRealProvider = health.providers?.some((p) => p.available) ?? false;
  if (hasRealProvider) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b border-amber-500/25 bg-amber-500/[0.08] backdrop-blur-sm"
    >
      <div className="flex items-center gap-3 px-6 py-2 lg:px-10">
        <TriangleAlert size={15} className="shrink-0 text-amber-300" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-amber-100/90">
          <span className="font-semibold text-amber-200">Simulated mode</span>
          <span className="text-amber-100/70">
            {" "}
            — no AI model is connected, so replies are fabricated by an offline mock.
          </span>
        </p>
        <Link
          href="/connections"
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15"
        >
          <Cable size={12} aria-hidden="true" /> Connect a model
        </Link>
      </div>
    </div>
  );
}
