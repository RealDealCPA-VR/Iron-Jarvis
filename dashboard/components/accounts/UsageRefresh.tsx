"use client";

/**
 * "Check usage now" (v1.439.0) — ONE small control shared by the Connections
 * card's account rows, Chat › Code's account cards and Build › Code's Usage
 * tab: a 14 px refresh icon, spinning while the account's usage is read from
 * its own CLI, then "as of N min ago" — or the daemon's own words when the
 * read failed or was refused. The words sit in a live region mounted with the
 * button, so a screen reader hears the answer (a region mounted WITH its
 * words is often not announced).
 *
 * The caller passes `readAt` (the row's `usage_read_at`) and gets the answer
 * through `onRead` (the fresh account row, raw) to redraw its numbers.
 */

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { asOfWords, CHECK_USAGE_NOW, checkUsageLabel, refreshAccountUsage, type UsageRefreshResult } from "@/lib/accountUsage";

export function UsageRefresh({
  accountId,
  accountName,
  readAt = null,
  onRead,
  className = "",
}: {
  accountId: string;
  accountName: string;
  /** When this account's usage was last read now (ISO), or null. */
  readAt?: string | null;
  onRead?: (res: UsageRefreshResult) => void;
  className?: string;
}) {
  const [reading, setReading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [at, setAt] = useState<string | null>(readAt);
  useEffect(() => setAt((mine) => (readAt && (!mine || readAt > mine) ? readAt : mine)), [readAt]);
  const press = async () => {
    if (reading) return;
    setReading(true);
    setProblem(null);
    try {
      const res = await refreshAccountUsage(accountId);
      if (res.read.at) setAt(res.read.at);
      setProblem(res.read.ok ? null : res.read.words || "The usage could not be read.");
      onRead?.(res);
    } catch (e) {
      setProblem(e instanceof Error && e.message ? e.message : "The usage could not be read.");
    } finally {
      setReading(false);
    }
  };
  const asOf = asOfWords(at);
  return (
    <span className={`inline-flex min-w-0 items-center gap-1.5 ${className}`}>
      <button
        type="button"
        data-testid={`usage-refresh-${accountId}`}
        title={CHECK_USAGE_NOW}
        aria-label={checkUsageLabel(accountName)}
        aria-busy={reading || undefined}
        onClick={press}
        disabled={reading}
        className="inline-grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-60"
      >
        <RefreshCw size={14} aria-hidden className={reading ? "animate-spin" : undefined} />
      </button>
      <span
        role="status"
        aria-live="polite"
        data-testid={`usage-refresh-note-${accountId}`}
        className={`min-w-0 truncate text-[11px] ${problem ? "text-tone-warn" : "text-zinc-500"}`}
      >
        {reading ? "Checking…" : problem ?? asOf}
      </span>
    </span>
  );
}
