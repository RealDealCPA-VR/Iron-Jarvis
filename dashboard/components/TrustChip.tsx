"use client";

/**
 * TrustChip — the run's trust posture (v1.298.0).
 *
 * A run is in LOW trust when it started from an unattended inbound message
 * (email, chat, webhook) or when it read content the daemon flagged as
 * injection mid-run; under low trust it cannot change memory, settings,
 * agents or skills. The chip says so beside the origin chip, with the
 * daemon's own reason as its title and the moment it was tainted when that
 * happened mid-run.
 *
 * FULL trust (and an absent field — every row from before the column
 * existed) renders NOTHING: a "full trust" badge on every card would be
 * noise, and the quiet case is the honest default.
 */

import { ShieldAlert, ShieldOff } from "lucide-react";
import type { SessionTrust } from "@/lib/types";
import { clockTime } from "@/lib/format";

export interface TrustChipProps {
  trust?: SessionTrust | string | null;
  reason?: string | null;
  taintedAt?: string | null;
  className?: string;
}

export default function TrustChip({
  trust,
  reason,
  taintedAt,
  className = "",
}: TrustChipProps) {
  if (trust !== "low") return null;
  const why = (reason ?? "").trim();
  const since = taintedAt ? clockTime(taintedAt) : "";
  // ShieldOff = trust was LOST mid-run (tainted); ShieldAlert = it started low.
  const Icon = taintedAt ? ShieldOff : ShieldAlert;
  const title = [why || "This run is in low trust", since ? `since ${since}` : ""]
    .filter(Boolean)
    .join(" — ");
  return (
    <span
      title={title}
      data-testid="trust-chip"
      data-tainted={taintedAt ? "true" : undefined}
      className={`inline-flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-[1px] text-[10px] font-medium leading-4 text-amber-300 ${className}`}
    >
      <Icon size={10} aria-hidden="true" className="shrink-0" />
      low trust
      {since && <span className="text-amber-300/80">· since {since}</span>}
    </span>
  );
}
