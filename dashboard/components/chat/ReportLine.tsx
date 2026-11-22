"use client";

/**
 * The line above a REPORT in a chat (idea 39, 39-a): where it came from
 * (a quiet chip: "Schedule · Friday prices"), how it went (the ledger's word,
 * never the model's), how many it holds today when a record reports more than
 * once a day (the daemon folds those into one line a day), "Open" (the
 * record's page) and **Stop reporting**, with Undo right after it.
 *
 * Calm: one small muted line that wraps at 390 px; the main action of the
 * chat (the composer) stays the main action.
 */

import Link from "next/link";
import { useState } from "react";

import {
  type ReportFrom,
  REPORTS_STOPPED,
  STOP_REPORTING,
  STOPPABLE_KINDS,
  reportKindLabel,
  setReportTarget,
} from "@/lib/reports";

/** `chatId`: the chat this report is in. Stop reporting then stops the
 *  record only while it still reports HERE (review: it may report to another
 *  chat since), and says so in words otherwise. */
export function ReportLine({ from, count, chatId }: { from: ReportFrom; count?: number; chatId?: string }) {
  const [stopped, setStopped] = useState<{ previous: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const kind = from.kind;
  const canStop = STOPPABLE_KINDS.has(kind) && !!from.id;

  async function stop() {
    if (!from.id) return;
    setBusy(true);
    setError("");
    try {
      const out = await setReportTarget(kind, from.id, "", chatId || undefined);
      setStopped({ previous: out.previous });
    } catch (e) {
      setError(e instanceof Error ? e.message : "That could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!from.id || !stopped?.previous) return;
    setBusy(true);
    setError("");
    try {
      await setReportTarget(kind, from.id, stopped.previous);
      setStopped(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="report-line"
      data-report-kind={kind}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-zinc-500"
    >
      <span data-testid="report-chip" className="font-medium text-zinc-400">
        {reportKindLabel(kind)}
      </span>
      {from.name ? (
        <span className="min-w-0 max-w-full truncate text-zinc-400" title={from.name}>
          {from.name}
        </span>
      ) : null}
      {from.outcome ? <span data-testid="report-outcome">· {from.outcome}</span> : null}
      {count && count > 1 ? <span data-testid="report-count">· {count} today</span> : null}
      {from.href ? (
        <Link href={from.href} className="text-accent-soft transition-colors hover:text-accent">
          Open
        </Link>
      ) : null}
      {canStop && !stopped ? (
        <button
          type="button"
          data-testid="report-stop"
          disabled={busy}
          onClick={stop}
          className="text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-200 hover:underline disabled:opacity-50"
        >
          {STOP_REPORTING}
        </button>
      ) : null}
      {stopped ? (
        <span data-testid="report-stopped" className="inline-flex items-center gap-1.5">
          {REPORTS_STOPPED}
          {stopped.previous ? (
            <button
              type="button"
              data-testid="report-undo"
              disabled={busy}
              onClick={undo}
              className="text-accent-soft transition-colors hover:text-accent disabled:opacity-50"
            >
              Undo
            </button>
          ) : null}
        </span>
      ) : null}
      {error ? (
        <span role="alert" className="text-tone-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export default ReportLine;
