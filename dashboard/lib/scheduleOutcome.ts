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
//   needs_you   the user is the headline: an ask timed out, or (v1.358.0) a
//               workflow is parked on the user's answer
//   partial     it finished, but a step failed
//   error       the run failed ("The run failed: <reason>") — v1.358.0: a
//               FAILED run whose ask expired too, like the run's own badge
//   cancelled   it was stopped mid-way (a Cancel, or Emergency stop)
//   skipped     it did not start (still running, Emergency stop is on, or
//               — v1.358.0 — the goal engine refused this iteration)
//   missed      the app was closed or asleep at fire time
//   ""          never fired
//
// ONE table for the words and the tone, read by the Schedules page and a
// project's Schedules list, so the two cannot drift again. An unknown value is
// shown as itself, never as "not run yet" (that was the lie).
//
// v1.352.0 (G17): that table is now the app's ONE status word table
// (lib/statusWords.scheduleRunWords) — "Done", "Needs you", "Part failed"
// (v1.358.0: the row LEADS with the outcome, never "Done · …"), "Failed",
// "Stopped" (slate: a stop is not a failure), "Skipped", "Missed", "Not run
// yet" — the words a session, a mission and the Session board use for the
// same states. `scheduleStatusWords` keeps its {label, tone} shape for the
// project list; the second copy of the words that lived here ("✓ ok",
// "finished · needs you", an amber "stopped") is gone.
//
// v1.358.0 (lane 8A): the row detail is flattened by lib/plainText's
// `summaryLine` — the SAME helper the bell's rows use for a run's summary, so
// "# Final Report ## What happened …" reads as words on both. lib/plainText is
// dependency-free, so this file no longer pulls the chat's Markdown renderer.

import { summaryLine } from "@/lib/plainText";
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
 *  `plainText`'s line-start rule cannot see them — dropped first
 *  (lib/plainText.summaryLine, the one helper the bell reuses). */
export function scheduleDetail(detail: string | null | undefined): string {
  return summaryLine(detail ?? "");
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
 *  shows, in amber when it finished short or did not run, never "Ran ✓".
 *  v1.358.0 (lane 8A): a run that needs the user reads "needs you" — the
 *  row's "Needs you", the notice's and the bell's — never "finished" (a
 *  workflow parked on the user's answer has not finished). */
export function runNowNote(name: string, status: string | null | undefined, detail: string | null | undefined): RunNowNote {
  const d = scheduleDetail(detail);
  const tail = d ? ` — ${d}` : "";
  switch ((status ?? "").trim()) {
    case "error":
    case "failed":
      return { tone: "danger", text: `"${name}" failed — ${d || "no detail"}` };
    case "needs_you":
      return { tone: "warn", text: `"${name}" needs you${tail}` };
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
