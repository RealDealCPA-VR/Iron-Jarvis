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
 *
 * v1.356.0 (G77): the hover also says what the run CANNOT do (settings,
 * schedules, rules, connections, memory, …) — lib/trustWords, pinned
 * against the daemon's own deny set (core/trust.LOW_TRUST_DENY).
 */

import { ShieldAlert, ShieldOff } from "lucide-react";
import type { SessionTrust } from "@/lib/types";
import { clockTime } from "@/lib/format";
import { LOW_TRUST_CANNOT, lowTrustTitle } from "@/lib/trustWords";

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
  const since = taintedAt ? clockTime(taintedAt) : "";
  // ShieldOff = trust was LOST mid-run (tainted); ShieldAlert = it started low.
  const Icon = taintedAt ? ShieldOff : ShieldAlert;
  const title = lowTrustTitle(reason, since);
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
      {/* The hover is mouse-only; a screen reader hears the same words. */}
      <span className="sr-only">. {LOW_TRUST_CANNOT}</span>
    </span>
  );
}
