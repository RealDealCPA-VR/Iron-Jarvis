"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import Link from "next/link";
import { useEvents } from "@/lib/useEvents";

/** The one /events type the banner reads (v1.311.0). */
export const PROVIDER_DOWNGRADE_EVENT_TYPES: readonly string[] = ["provider.downgraded"];

/**
 * Loud, dismissible banner shown when the daemon emits `provider.downgraded` —
 * i.e. a session ran on the offline `mock` model instead of the model you
 * expected (the default is still "mock", or the requested provider isn't
 * connected). Without this, fabricated mock output looks real. Auto-clears when
 * dismissed; re-appears on the next downgrade.
 */
export function ProviderDowngradeBanner() {
  // v1.311.0 (wave 3): only the one type it reads. It sits on the Overview,
  // and every other frame re-rendered the page's tree for nothing.
  const { events } = useEvents(40, { types: PROVIDER_DOWNGRADE_EVENT_TYPES });
  const [dismissedTs, setDismissedTs] = useState<string | null>(null);

  const latest = events.find((e) => e.type === "provider.downgraded");
  if (!latest || latest.ts === dismissedTs) return null;

  const reason =
    (latest.payload?.reason as string) ||
    "a session ran on the offline mock model instead of a real provider";

  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-400/30 bg-amber-400/[0.08] px-4 py-3 text-sm text-amber-200">
      <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-300" />
      <div className="flex-1">
        <div className="font-medium text-amber-100">Output came from the mock model</div>
        <div className="mt-0.5 text-amber-200/90">{reason}</div>
        <Link
          href="/connections"
          className="mt-1 inline-block text-amber-100 underline underline-offset-2 hover:text-white"
        >
          Open Connections →
        </Link>
      </div>
      <button
        aria-label="Dismiss"
        onClick={() => setDismissedTs(latest.ts)}
        className="shrink-0 rounded-md p-1 text-amber-300/70 hover:bg-white/10 hover:text-amber-100"
      >
        <X size={16} />
      </button>
    </div>
  );
}
