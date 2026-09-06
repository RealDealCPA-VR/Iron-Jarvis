"use client";

import { ShieldOff } from "lucide-react";

/**
 * v1.232.0 (audit T5): the session ran a shell command on the NATIVE runtime
 * because Docker was unavailable, so the workspace/host/network limits the
 * sandbox policy asked for were advisory, not enforced. Until this chip the
 * only trace was a paragraph inside the first shell result's output — nothing
 * on the session card, no event field, no ledger column. Rendered ONCE per
 * session (the page folds the ledger rows), never per call.
 */
export default function ConfinementChip({ className = "" }: { className?: string }) {
  return (
    <span
      data-testid="confinement-chip"
      className={`inline-flex items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/[0.1] px-2 py-0.5 text-[10px] font-medium text-amber-300 ${className}`}
      title="At least one shell command in this session ran on the native runtime because Docker was unavailable — the workspace, host and network limits were advisory, not enforced."
    >
      <ShieldOff size={10} /> Shell ran unconfined (Docker unavailable)
    </span>
  );
}
