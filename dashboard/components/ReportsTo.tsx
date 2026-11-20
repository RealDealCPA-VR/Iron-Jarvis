"use client";

/**
 * "Reports to <chat>" on a Schedules, Watchers or Goals row (idea 39, 39-a):
 * the chat this was set up from, where each result is written, and **Stop
 * reporting** with Undo right after it. Nothing is drawn for a record that
 * reports nowhere (made on a page, or stopped), so a row stays as it was.
 */

import Link from "next/link";
import { useState } from "react";

import {
  type ReportsTo as ReportsToRow,
  REPORTS_STOPPED,
  REPORTS_TO,
  STOP_REPORTING,
  setReportTarget,
} from "@/lib/reports";

export function ReportsTo({
  kind,
  id,
  to,
  onChanged,
}: {
  kind: string;
  id: string;
  to: ReportsToRow | null | undefined;
  onChanged?: () => void;
}) {
  const [stopped, setStopped] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!to && !stopped) return null;

  async function change(threadId: string) {
    setBusy(true);
    setError("");
    try {
      const out = await setReportTarget(kind, id, threadId);
      setStopped(threadId ? "" : out.previous);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span
      data-testid={`reports-to-${kind}`}
      className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-[11px] text-zinc-500"
    >
      {to && !stopped ? (
        <>
          <span>{REPORTS_TO}</span>
          <Link
            href={`/chat?thread=${encodeURIComponent(to.thread_id)}`}
            className="min-w-0 max-w-[200px] truncate text-accent-soft transition-colors hover:text-accent"
            title={to.title}
          >
            {to.title}
          </Link>
          <button
            type="button"
            data-testid="reports-to-stop"
            disabled={busy}
            onClick={() => change("")}
            className="text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-200 hover:underline disabled:opacity-50"
          >
            {STOP_REPORTING}
          </button>
        </>
      ) : (
        <>
          <span>{REPORTS_STOPPED}</span>
          <button
            type="button"
            data-testid="reports-to-undo"
            disabled={busy}
            onClick={() => change(stopped)}
            className="text-accent-soft transition-colors hover:text-accent disabled:opacity-50"
          >
            Undo
          </button>
        </>
      )}
      {error ? (
        <span role="alert" className="text-tone-danger">
          {error}
        </span>
      ) : null}
    </span>
  );
}

export default ReportsTo;
