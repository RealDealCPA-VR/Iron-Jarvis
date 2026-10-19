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
//
// v1.352.0 (G17): that table is now the app's ONE status word table
// (lib/statusWords.scheduleRunWords) — "Done", "Done · needs you",
// "Done · part failed", "Failed", "Stopped" (slate: a stop is not a failure),
// "Skipped", "Missed", "Not run yet" — the words a session, a mission and the
// Session board use for the same states. `scheduleStatusWords` keeps its
// {label, tone} shape for the project list; the second copy of the words that
// lived here ("✓ ok", "finished · needs you", an amber "stopped") is gone.

import { plainText } from "@/components/Markdown";
import { scheduleStatusLabel } from "@/lib/statusWords";

export type ScheduleTone = "success" | "danger" | "warn" | "muted";

export interface ScheduleStatusWords {
  /** What the row says. */
  label: string;
  /** success = green, danger = red, warn = amber, muted = grey. */
  tone: ScheduleTone;
}

/** The row's status words for a `last_status` value, from the one status
 *  table (a legacy `failed` reads like `error` — the daemon's
 *  FAILED_STATUSES counts it too; an unknown value is shown as itself). */
export function scheduleStatusWords(status: string | null | undefined): ScheduleStatusWords {
  return scheduleStatusLabel(status);
}

/** The daemon's `run_outcome.SHORT_STATUSES` (FAILED_STATUSES +
 *  NEEDS_A_LOOK_STATUSES) — pinned equal by tests/test_wave1_seams_v13510.py. */
export const SHORT_SCHEDULE_STATUSES: readonly string[] = ["error", "failed", "needs_you", "partial"];

/** A last run the user should look at: it failed, or it finished short of
 *  the job (the daemon's `SHORT_STATUSES`; the home line counts the same). */
export function isShortSchedule(status: string | null | undefined): boolean {
  return SHORT_SCHEDULE_STATUSES.includes((status ?? "").trim());
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
 *  already says "Failed: " in front of it. */
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
    case "failed":
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
