"use client";

// Jobs a RESTART cut off (v1.249.0, R-02).
//
// An update (or a crash) ends every running session, and the boot reconcile
// marks them FAILED with "interrupted by a daemon restart". Until now that was
// the end of it: the work was gone, nothing offered to pick it up, and the
// user had to find the session and press Continue themselves — if they even
// knew it had been running.
//
// ONE source, TWO surfaces: the bell renders `InterruptedJobRow` (it waits on
// the user, like the other bell rows) and the Overview renders
// `InterruptedJobsNote` (the page the user lands on after a restart). Both
// read the same polled list and the same two actions, so they can never
// disagree about what is offered.

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { History } from "lucide-react";

import { ApiError, post } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { shortId } from "@/lib/format";

export const INTERRUPTED_PATH = "/sessions/interrupted";

/** What a continued run is told. The user pressed a button rather than typing
 *  a follow-up, so the message says exactly what happened — an agent reading
 *  "continue" with no reason invents one. */
export const CONTINUE_AFTER_RESTART =
  "Continue where you left off — Iron Jarvis restarted while this was running.";

export interface InterruptedJob {
  id: string;
  task: string;
  agentType: string;
  interruptedAt: string;
}

/** Parse one row of GET /sessions/interrupted (null = not usable). */
export function parseInterrupted(raw: unknown): InterruptedJob | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = typeof r.id === "string" ? r.id : "";
  if (!id) return null;
  return {
    id,
    task: typeof r.task === "string" && r.task.trim() ? r.task.trim() : "(no description)",
    agentType: typeof r.agent_type === "string" ? r.agent_type : "",
    interruptedAt: typeof r.interrupted_at === "string" ? r.interrupted_at : "",
  };
}

/** The polled list, minus anything answered from this window (so the row
 *  leaves on the click instead of lingering until the next poll). */
export function useInterruptedJobs(intervalMs = 15000) {
  const api = usePolledApi<{ sessions?: unknown[] }>(INTERRUPTED_PATH, intervalMs);
  const [handled, setHandled] = useState<ReadonlySet<string>>(new Set());
  const jobs = useMemo(() => {
    const out: InterruptedJob[] = [];
    for (const raw of api.data?.sessions ?? []) {
      const job = parseInterrupted(raw);
      if (job && !handled.has(job.id)) out.push(job);
    }
    return out;
  }, [api.data, handled]);
  const reload = api.reload;
  const forget = useCallback(
    (id: string) => {
      setHandled((prev) => new Set(prev).add(id));
      reload();
    },
    [reload],
  );
  return { jobs, reload, forget, error: api.error };
}

/** Continue / Dismiss. Continue starts the follow-up run through the SAME
 *  route the session page's Continue uses; Dismiss only clears the prompt —
 *  the run itself is untouched, and it stays in the session list. */
export function InterruptedJobActions({
  job,
  onGone,
}: {
  job: InterruptedJob;
  onGone: (id: string) => void;
}) {
  const [busy, setBusy] = useState<"continue" | "dismiss" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "continue" | "dismiss") {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "continue") {
        await post(`/sessions/${encodeURIComponent(job.id)}/continue`, {
          message: CONTINUE_AFTER_RESTART,
          wait: false,
        });
      } else {
        await post(`/sessions/${encodeURIComponent(job.id)}/interrupted/dismiss`, {});
      }
    } catch (e) {
      // 404: the session is gone (cleared elsewhere) — the offer goes with it.
      if (e instanceof ApiError && e.status === 404) {
        onGone(job.id);
        return;
      }
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null); // a daemon blip keeps the buttons live
      return;
    }
    onGone(job.id); // unmounts this row — no state updates past this point
  }

  return (
    <>
      <div className="mt-1.5 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => void act("continue")}
          disabled={busy !== null}
          className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
        >
          {busy === "continue" ? "Continuing…" : "Continue"}
        </button>
        <button
          type="button"
          onClick={() => void act("dismiss")}
          disabled={busy !== null}
          title="Stop offering to continue this job"
          className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100 disabled:opacity-50"
        >
          {busy === "dismiss" ? "Dismissing…" : "Dismiss"}
        </button>
      </div>
      {error && <p className="mt-1 text-[11px] text-rose-300">{error}</p>}
    </>
  );
}

/** One interrupted job in the bell's dropdown. */
export function InterruptedJobRow({
  job,
  onGone,
}: {
  job: InterruptedJob;
  onGone: (id: string) => void;
}) {
  return (
    <li data-testid="bell-interrupted-job" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-amber-500/25 bg-amber-500/[0.08] text-amber-300">
          <History size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            A job stopped when Iron Jarvis restarted
          </span>
          <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-amber-200">{job.task}</p>
          <InterruptedJobActions job={job} onGone={onGone} />
          <span className="mt-1 block font-mono text-[10px] text-zinc-600">
            <Link
              href={`/sessions/${encodeURIComponent(job.id)}`}
              className="text-accent-soft transition-colors hover:text-accent"
            >
              {shortId(job.id)}
            </Link>
          </span>
        </div>
      </div>
    </li>
  );
}

/** The Overview's note: the same offer, on the page the user lands on after a
 *  restart. Renders NOTHING when there is nothing to continue. */
export function InterruptedJobsNote({ intervalMs = 30000 }: { intervalMs?: number } = {}) {
  const { jobs, forget } = useInterruptedJobs(intervalMs);
  if (!jobs.length) return null;
  const shown = jobs.slice(0, 3);
  return (
    <div
      role="status"
      data-testid="interrupted-jobs-note"
      className="rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3 text-[12px] text-amber-200"
    >
      <div className="flex items-center gap-2">
        <History size={14} className="shrink-0" />
        <span className="font-medium">
          {jobs.length} job{jobs.length === 1 ? "" : "s"} stopped when Iron Jarvis restarted
        </span>
      </div>
      <ul className="mt-2 space-y-2">
        {shown.map((job) => (
          <li key={job.id} className="min-w-0">
            <span className="block truncate text-zinc-200">{job.task}</span>
            <InterruptedJobActions job={job} onGone={forget} />
          </li>
        ))}
      </ul>
      {jobs.length > shown.length && (
        <p className="mt-2 text-[11px] text-amber-200/80">
          …and {jobs.length - shown.length} more in the notification bell.
        </p>
      )}
    </div>
  );
}
