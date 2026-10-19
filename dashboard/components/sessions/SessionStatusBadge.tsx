"use client";

// The honest status chip (v1.227.0, wave 1 — "the job finishes").
//
// THE FAILURE THIS ANSWERS. A 28-file rename job whose every mutating call
// expired unanswered finished as status "completed", and four surfaces — the
// session header, the sessions list, the kanban card and the project's recent
// runs — each wore a green "Completed" badge over a run that had renamed
// 3 files and abandoned 25. The green was TRUE (the session did complete) and
// it was the wrong thing to say, because the user reads "completed" as
// "done", and the job was not done.
//
// The daemon now sets `Session.outcome` at finalize from the ledger:
//   completed                 every mutating call ran
//   completed_with_failures   a mutating call failed
//   needs_you                 an ask for this session expired unanswered
// and `waiting_on` while a run is PAUSED on an ask. This is the ONE renderer
// for both, so the four surfaces cannot drift: they all call it, and a plain
// green "completed" is what it renders only when the outcome earned it.
// Labels are exported as pure functions so the tests pin the words, not the
// markup.

import { Badge, type BadgeVariant, type Tone } from "@/components/ui";
import type { SessionOutcome, SessionWaitingOn } from "@/lib/types";
import { outcomeWords, statusWords, type StatusWords } from "@/lib/statusWords";

export interface OutcomeSource {
  status: string;
  outcome?: SessionOutcome | null;
  waiting_on?: SessionWaitingOn | null;
  /** v1.354.0 (G20): set by the boot reconcile when a restart cut the run
   *  off; cleared once it is continued or dismissed. */
  interrupted_at?: string | null;
}

/** v1.354.0 (G20): "Interrupted" for a run a RESTART cut off (an update or a
 *  crash), else null. The boot reconcile marks such a run failed and tags it
 *  `interrupted_at`; it did not fail at its job, and it can be continued, so
 *  it never wears a failure's red "Failed" while the tag stands. */
export const INTERRUPTED_WORD = "Interrupted";

export function interruptedLabel(session: OutcomeSource): string | null {
  const s = (session.status ?? "").trim().toLowerCase();
  if (s !== "failed") return null;
  return session.interrupted_at ? INTERRUPTED_WORD : null;
}

/** "Done · needs you" / "Done · part failed", or null when the plain status
 *  badge tells the truth (a completed run that completed, or a failed or
 *  stopped run — its own word already says it).
 *
 *  v1.352.0 (G17): the words come from the ONE status table
 *  (lib/statusWords), the same "Done" the plain badge, the board's lane, the
 *  mission cards and the inbox say — it read "Completed · with failures"
 *  here and "part failed" on the mission screen. */
export function outcomeLabel(session: OutcomeSource): string | null {
  if ((session.status ?? "").toLowerCase() !== "completed") return null;
  return outcomeWords(session.outcome)?.word ?? null;
}

/** v1.352.0 (G17): a run the user (or Emergency stop) STOPPED, in the one
 *  table's words and tone — "Stopped", slate, never a failure's red. Null for
 *  any other run. The Session board puts it on a stopped card, which shares
 *  the Failed lane with real failures. */
export function stoppedChip(session: OutcomeSource): StatusWords | null {
  const s = (session.status ?? "").trim().toLowerCase();
  return s === "cancelled" || s === "canceled" ? statusWords(s) : null;
}

/** "Waiting for you · <tool>" while the run is paused on an ask, else null. */
export function waitingLabel(session: OutcomeSource): string | null {
  const w = session.waiting_on;
  if (!w || !w.approval_id) return null;
  return `Waiting for you · ${w.tool || "a tool"}`;
}

/** The status badge every session surface renders: amber when the run is
 *  paused for the user or finished short of the job, else the plain status in
 *  the one table's words and tone (v1.352.0, G17: "Working", "Queued",
 *  "Done", "Failed", "Stopped" — never the raw id; `tone` passes through for
 *  that case only, and still wins over the table's).
 *
 *  `variant` (v1.330.0, calm wave 12) picks the Badge's look for all three
 *  cases: "default" is the bordered pill the Sessions pages draw (and what a
 *  caller that says nothing gets); "calm" is the quiet chip of the calm
 *  surfaces (a project's Tasks list), where only the dot carries the tone.
 *  The words and the tone are the same either way. */
export function SessionStatusBadge({
  session,
  tone,
  variant = "default",
}: {
  session: OutcomeSource;
  tone?: Tone;
  variant?: BadgeVariant;
}) {
  const waiting = waitingLabel(session);
  if (waiting) {
    return (
      <span data-testid="session-waiting-chip" className="contents">
        <Badge value={waiting} tone="amber" keepCase variant={variant} />
      </span>
    );
  }
  const cut = interruptedLabel(session);
  if (cut) {
    return (
      <span data-testid="session-interrupted-chip" className="contents">
        <Badge value={cut} tone="amber" keepCase variant={variant} />
      </span>
    );
  }
  const label = outcomeLabel(session);
  if (label) {
    return (
      <span data-testid="session-outcome-chip" className="contents">
        <Badge value={label} tone="amber" keepCase variant={variant} />
      </span>
    );
  }
  const plain = statusWords(session.status);
  return <Badge value={plain.word} tone={tone ?? plain.tone} keepCase variant={variant} />;
}

export default SessionStatusBadge;
