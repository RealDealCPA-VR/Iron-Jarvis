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
 * the workflow run and step badges, the Schedules row (`scheduleRunWords`)
 * and a project's Schedules list (lib/scheduleOutcome.scheduleStatusWords
 * returns `scheduleStatusLabel`). The words were merged from the three tables that
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
  /** What the badge or row says, already cased ("Done", "Needs you").
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

/** A COMPLETED run whose ledger verdict (`Session.outcome`) fell short.
 *
 *  v1.358.0 (lane 8A): the word LEADS with the outcome — "Needs you" /
 *  "Part failed" — never "Done · …": a person reads the first word, and
 *  "Done" over work that still needs them (or that partly failed) was the
 *  re-audit's regression on the Schedules row. The same object a blocked
 *  assignment and a waiting teammate say, so the user is next in one word. */
const PART_FAILED = w("Part failed", "amber");
const OUTCOME: Readonly<Record<string, StatusWords>> = {
  // An ask expired unanswered: the user is the headline.
  needs_you: NEEDS_YOU,
  // A mutating step failed (or a mission's worklist kept failed items).
  completed_with_failures: PART_FAILED,
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
 * has one. Only a COMPLETED run reads its outcome: "Needs you" / "Part
 * failed" (amber); a failed or stopped run already says what happened.
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
 * scheduler writes) in the SAME words: ok is "Done", a run that needs the
 * user (an expired ask, or a workflow parked on the user's answer) reads
 * "Needs you" and one that partly failed "Part failed" — a completed
 * session's outcome words — error is "Failed" (a FAILED run whose ask
 * expired too, v1.358.0: the run's own badge says Failed), a run stopped
 * mid-way is "Stopped". Only three are schedule-only: "Skipped" and
 * "Missed" (amber: it did not run, and the user may want to know why) and
 * "Not run yet" for a schedule that never fired.
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

/**
 * The WORD's own ink on a calm row (v1.358.0, lane 8A). The calm Badge keeps
 * its words neutral grey and puts the tone on the dot alone, so a schedule
 * that needed the user read a grey "Done · needs you" with only an amber dot.
 * A word in the warn or danger tone ("Needs you", "Part failed", "Skipped",
 * "Missed" / "Failed") now reads in that tone token itself; every other word
 * stays the calm grey (""). The `!` wins over the calm shell's grey text —
 * theme tokens only, so a palette re-inks it.
 */
export function toneTextClass(tone: Tone): string {
  if (tone === "amber") return "!text-tone-warn";
  if (tone === "red") return "!text-tone-danger";
  return "";
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
