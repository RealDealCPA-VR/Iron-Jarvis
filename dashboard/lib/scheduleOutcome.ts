// How a schedule's last run went, in words (v1.351.0, readiness audit G01).
//
// THE FAILURE THIS ANSWERS. The scheduler recorded a run "ok" from its status
// alone, and the two places that show schedules each knew three words: "✓ ok",
// "✗ failed", and everything else — a run that finished while an ask expired
// unanswered, a run whose step failed — fell through to "not run yet" (or,
// before the daemon learned the real outcome, wore the green "✓ ok"). The
// daemon now writes the verdict (`scheduling/run_outcome.py`):
//
//   ok          the job was done
//   needs_you   it finished, but an ask timed out — the user is the headline
//   partial     it finished, but a step failed
//   error       the run failed ("The run failed: <reason>")
//   cancelled   it was stopped mid-way (a Cancel, or Emergency stop)
//   skipped     it did not start (still running, or Emergency stop is on)
//   missed      the app was closed or asleep at fire time
//   ""          never fired
//
// ONE table for the words and the tone, read by the Schedules page and a
// project's Schedules list, so the two cannot drift again. An unknown value is
// shown as itself, never as "not run yet" (that was the lie).

import { plainText } from "@/components/Markdown";

export type ScheduleTone = "success" | "danger" | "warn" | "muted";

export interface ScheduleStatusWords {
  /** What the row says. */
  label: string;
  /** success = green, danger = red, warn = amber, muted = grey. */
  tone: ScheduleTone;
}

const WORDS: Record<string, ScheduleStatusWords> = {
  ok: { label: "✓ ok", tone: "success" },
  error: { label: "✗ failed", tone: "danger" },
  needs_you: { label: "finished · needs you", tone: "warn" },
  partial: { label: "finished · part failed", tone: "warn" },
  cancelled: { label: "stopped", tone: "warn" },
  skipped: { label: "skipped", tone: "warn" },
  missed: { label: "missed", tone: "warn" },
};

/** The row's status words for a `last_status` value. */
export function scheduleStatusWords(status: string | null | undefined): ScheduleStatusWords {
  const s = (status ?? "").trim();
  if (!s) return { label: "not run yet", tone: "muted" };
  return WORDS[s] ?? { label: s.replace(/_/g, " "), tone: "muted" };
}

/** A last run the user should look at: it failed, or it finished short of
 *  the job (the daemon's `SHORT_STATUSES`; the home line counts the same). */
export function isShortSchedule(status: string | null | undefined): boolean {
  return status === "error" || status === "needs_you" || status === "partial";
}

/** The row detail as plain words. New rows are already a plain sentence; an
 *  older row may hold a report's markdown flattened onto one line ("# Final
 *  Report ## What happened …"), whose heading marks sit mid-line where
 *  `plainText`'s line-start rule cannot see them — dropped first. */
export function scheduleDetail(detail: string | null | undefined): string {
  const flat = (detail ?? "").replace(/(^|\s)#{1,6}(?=\s)/g, "$1");
  return plainText(flat);
}

const FAILED_LEAD = "The run failed: ";

/** A failed row's reason without the daemon's lead, for a surface that
 *  already says "✗ failed: " in front of it. */
export function failureReason(detail: string | null | undefined): string {
  const text = scheduleDetail(detail);
  return text.startsWith(FAILED_LEAD) ? text.slice(FAILED_LEAD.length) : text;
}

export interface RunNowNote {
  tone: "success" | "warn" | "danger";
  text: string;
}

/** What Run now says about the run it just made — the same verdict the row
 *  shows, in amber when it finished short or did not run, never "Ran ✓". */
export function runNowNote(name: string, status: string | null | undefined, detail: string | null | undefined): RunNowNote {
  const d = scheduleDetail(detail);
  const tail = d ? ` — ${d}` : "";
  switch ((status ?? "").trim()) {
    case "error":
      return { tone: "danger", text: `"${name}" failed — ${d || "no detail"}` };
    case "needs_you":
      return { tone: "warn", text: `"${name}" finished, but it needs you${tail}` };
    case "partial":
      return { tone: "warn", text: `"${name}" finished, but part of it failed${tail}` };
    case "skipped":
      return { tone: "warn", text: `"${name}" did not run${tail}` };
    case "cancelled":
      return { tone: "warn", text: `"${name}" was stopped${tail}` };
    default:
      return { tone: "success", text: `Ran "${name}" ✓${tail}` };
  }
}
