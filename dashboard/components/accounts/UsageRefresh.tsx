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
 * v1.441.0 (review U5/U6): the words of a failed read WRAP (a phone cut them
 * after a few words) and the note's whole text is also its tooltip; "as of"
 * redraws itself every USAGE_AS_OF_TICK_MS while the window is seen, so an
 * idle pane never keeps saying "just now".
 *
 * The caller passes `readAt` (the row's `usage_read_at`: the last read that
 * WORKED — a failed read never moves "as of") and gets the answer
 * through `onRead` (the fresh account row, raw) to redraw its numbers.
 */

import { useEffect, useState } from "react";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { RefreshCw } from "lucide-react";
import { asOfWords, CHECK_USAGE_NOW, checkUsageLabel, refreshAccountUsage, type UsageRefreshResult } from "@/lib/accountUsage";

/** How often "as of N min ago" redraws itself (only while the window is seen). */
export const USAGE_AS_OF_TICK_MS = 30_000;

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
  // "as of" is relative to NOW: redraw it on a clock, only while it is shown.
  const [, setTick] = useState(0);
  useVisibleInterval(() => setTick((n) => n + 1), USAGE_AS_OF_TICK_MS, Boolean(at) && !reading && problem === null);
  // A newer good read (the list's own time: the in-use loop read it since)
  // replaces the time — and the words of a press that failed before it.
  useEffect(() => {
    if (!readAt) return;
    setAt((mine) => (!mine || readAt > mine ? readAt : mine));
    setProblem((p) => (p !== null && (!at || readAt > at) ? null : p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readAt]);
  const press = async () => {
    if (reading) return;
    setReading(true);
    setProblem(null);
    try {
      const res = await refreshAccountUsage(accountId);
      // "as of" speaks for the number shown: a read that failed read none.
      if (res.read.ok && res.read.at) setAt(res.read.at);
      setProblem(res.read.ok ? null : res.read.words || "The usage could not be read.");
      onRead?.(res);
    } catch (e) {
      setProblem(e instanceof Error && e.message ? e.message : "The usage could not be read.");
    } finally {
      setReading(false);
    }
  };
  const asOf = asOfWords(at);
  const said = reading ? "Checking…" : problem ?? asOf;
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
        title={said || undefined}
        className={`min-w-0 text-[11px] ${
          problem ? "whitespace-normal break-words text-tone-warn" : "truncate text-zinc-500"
        }`}
      >
        {said}
      </span>
    </span>
  );
}
