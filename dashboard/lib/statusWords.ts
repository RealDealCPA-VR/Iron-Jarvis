/**
 * ONE status word table for every surface (v1.352.0, readiness audit G17).
 *
 * THE FAILURE THIS ANSWERS. The same state read six ways across the app: a
 * running job was "Active", "Working", "working", "running" or "In use now";
 * a queued teammate was "Waiting" on its card and "Queued" in the mission
 * list; done was "Completed", "Done", "Finished" or "✓ ok"; a run the user
 * STOPPED wore a red "Cancelled" (the shared tone map painted it like a
 * failure); the Sessions filter listed raw ids and the Your-team inbox showed
 * raw assignment statuses ("claimed", "blocked").
 *
 * Every surface that says what state a piece of work is in now reads its word
 * AND its tone here: the session badge (components/sessions/SessionStatusBadge),
 * the Sessions filter, the Session board's lanes (lib/kanban), the mission
 * cards (lib/mission.statusWord), the inbox rows (components/agents/AgentInbox),
 * the workflow run and step badges, and the Schedules row
 * (`scheduleRunWords`). The words were merged from the three tables that
 * already existed (lib/mission.statusWord, MissionComposer's STATUS_WORD and
 * SessionStatusBadge.outcomeLabel) — the mission screen's plain words won.
 *
 * Plain data, no React, no runtime import (`Tone` is a type): safe to import
 * from any lib file, and unaffected by the tests that mock components/ui.
 * An unknown status is shown AS ITSELF ("brand new" for `brand_new`), never
 * guessed into a word it might not mean.
 */

import type { Tone } from "@/components/ui";

export interface StatusWords {
  /** What the badge or row says, already cased ("Done", "Done · needs you").
   *  Render it with `keepCase` — CSS `capitalize` would make "Needs You". */
  word: string;
  /** The tone family (components/ui `Tone`), drawn through the theme tokens. */
  tone: Tone;
}

function w(word: string, tone: Tone): StatusWords {
  return { word, tone };
}

/** The shared words, named once so every alias below is the same object. */
const QUEUED = w("Queued", "slate");
const WORKING = w("Working", "cyan");
const DONE = w("Done", "green");
const FAILED = w("Failed", "red");
// A run the user (or Emergency stop) stopped is not a failure: it reads in
// the neutral slate, never the danger red.
const STOPPED = w("Stopped", "slate");
// Finished or parked, and the user is the next step.
const NEEDS_YOU = w("Needs you", "amber");
// Paused on an ask right now (a review to approve, a question to answer).
const WAITING_FOR_YOU = w("Waiting for you", "amber");

/** Every work status the daemon writes, by its raw value (lower case). */
const STATUS: Readonly<Record<string, StatusWords>> = {
  // Sessions: active | queued | completed | failed | cancelled.
  queued: QUEUED,
  active: WORKING,
  completed: DONE,
  failed: FAILED,
  cancelled: STOPPED,
  // Assignments: queued | claimed | running | done | failed | blocked | cancelled.
  claimed: WORKING,
  running: WORKING,
  done: DONE,
  blocked: NEEDS_YOU,
  // Mission members: queued | working | waiting_you | done | failed | cancelled.
  working: WORKING,
  waiting_you: NEEDS_YOU,
  // Workflow runs: running | resuming | waiting | interrupted | completed |
  // failed | cancelled | error; a step: completed | failed | cancelled |
  // skipped, or no output yet (the page says "pending").
  resuming: w("Resuming", "cyan"),
  waiting: WAITING_FOR_YOU,
  interrupted: w("Interrupted", "amber"),
  error: FAILED,
  skipped: w("Skipped", "slate"),
  pending: w("Pending", "amber"),
  // The Session board's review lane: a review to approve, or a paused ask.
  review: WAITING_FOR_YOU,
  // Spellings other code has used for the same states.
  canceled: STOPPED,
  stopped: STOPPED,
  ok: DONE,
  succeeded: DONE,
  success: DONE,
};

/** A COMPLETED run whose ledger verdict (`Session.outcome`) fell short. */
const OUTCOME: Readonly<Record<string, StatusWords>> = {
  // An ask expired unanswered: it finished, and the user is the headline.
  needs_you: w("Done · needs you", "amber"),
  // A mutating step failed.
  completed_with_failures: w("Done · part failed", "amber"),
};

function norm(v: unknown): string {
  return typeof v === "string" ? v.trim().toLowerCase() : "";
}

/** An unknown value shown as itself: "brand_new" → "Brand new", slate. */
function asItself(raw: string): StatusWords {
  const text = raw.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  if (!text) return w("Unknown", "slate");
  return w(text.charAt(0).toUpperCase() + text.slice(1), "slate");
}

/**
 * The word and tone for a work status, given the run's ledger verdict when it
 * has one. Only a COMPLETED run reads its outcome: "Done · needs you" /
 * "Done · part failed" (amber); a failed or stopped run already says what
 * happened.
 */
export function statusWords(status: string | null | undefined, outcome?: string | null): StatusWords {
  const s = norm(status);
  if (s === "completed" || s === "done") {
    const o = OUTCOME[norm(outcome)];
    if (o) return o;
  }
  return STATUS[s] ?? asItself(typeof status === "string" ? status.trim() : "");
}

/** The outcome words for a completed run, or null when it earned its "Done". */
export function outcomeWords(outcome: string | null | undefined): StatusWords | null {
  return OUTCOME[norm(outcome)] ?? null;
}

/* ------------------------------------------------------------ schedules */

/** A schedule that has never fired. */
export const NOT_RUN_YET: StatusWords = w("Not run yet", "slate");

/**
 * A schedule's `last_status` (scheduling/run_outcome.py — the verdict the
 * scheduler writes) in the SAME words: ok is "Done", a finish that needs the
 * user or partly failed reads like a completed session's outcome, error is
 * "Failed", a run stopped mid-way is "Stopped". Only three are schedule-only:
 * "Skipped" and "Missed" (amber: it did not run, and the user may want to
 * know why) and "Not run yet" for a schedule that never fired.
 */
const SCHEDULE: Readonly<Record<string, StatusWords>> = {
  ok: DONE,
  needs_you: OUTCOME.needs_you,
  partial: OUTCOME.completed_with_failures,
  error: FAILED,
  // Not written by the scheduler; a row planted by an older build reads like
  // `error` (the daemon's FAILED_STATUSES counts it too).
  failed: FAILED,
  cancelled: STOPPED,
  skipped: w("Skipped", "amber"),
  missed: w("Missed", "amber"),
};

/** The Schedules row's words for a `last_status` value. */
export function scheduleRunWords(status: string | null | undefined): StatusWords {
  const raw = typeof status === "string" ? status.trim() : "";
  if (!raw) return NOT_RUN_YET;
  return SCHEDULE[raw] ?? asItself(raw);
}

/** lib/scheduleOutcome's tone names, for a reader that speaks them. */
export type ScheduleToneName = "success" | "danger" | "warn" | "muted";

/** A Tone in lib/scheduleOutcome's tone names (green = success, red =
 *  danger, amber = warn, anything else = muted). */
export function scheduleTone(tone: Tone): ScheduleToneName {
  if (tone === "green") return "success";
  if (tone === "red") return "danger";
  if (tone === "amber") return "warn";
  return "muted";
}

/** `scheduleRunWords` in lib/scheduleOutcome's `{label, tone}` shape, so its
 *  `scheduleStatusWords` (read by a project's Schedules list) can return the
 *  one table's words instead of keeping a second copy. */
export function scheduleStatusLabel(status: string | null | undefined): { label: string; tone: ScheduleToneName } {
  const v = scheduleRunWords(status);
  return { label: v.word, tone: scheduleTone(v.tone) };
}

/** The words a Sessions filter offers, in the order a person reads them:
 *  what is moving, what waits, how it ended. Each is a real session status
 *  (core/models.SessionStatus). */
export const SESSION_FILTER_STATUSES: readonly string[] = ["active", "queued", "completed", "failed", "cancelled"];
