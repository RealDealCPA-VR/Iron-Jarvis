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

import { Badge, type Tone } from "@/components/ui";
import type { SessionOutcome, SessionWaitingOn } from "@/lib/types";

export interface OutcomeSource {
  status: string;
  outcome?: SessionOutcome | null;
  waiting_on?: SessionWaitingOn | null;
}

/** "Completed · needs you" / "Completed · with failures", or null when the
 *  plain status badge tells the truth (a completed run that completed, or a
 *  failed/cancelled run — red already says it). */
export function outcomeLabel(session: OutcomeSource): string | null {
  if ((session.status ?? "").toLowerCase() !== "completed") return null;
  if (session.outcome === "needs_you") return "Completed · needs you";
  if (session.outcome === "completed_with_failures") return "Completed · with failures";
  return null;
}

/** "Waiting for you · <tool>" while the run is paused on an ask, else null. */
export function waitingLabel(session: OutcomeSource): string | null {
  const w = session.waiting_on;
  if (!w || !w.approval_id) return null;
  return `Waiting for you · ${w.tool || "a tool"}`;
}

/** The status badge every session surface renders: amber when the run is
 *  paused for the user or finished short of the job, else the plain status
 *  badge exactly as before (`tone` passes through for that case only). */
export function SessionStatusBadge({
  session,
  tone,
}: {
  session: OutcomeSource;
  tone?: Tone;
}) {
  const waiting = waitingLabel(session);
  if (waiting) {
    return (
      <span data-testid="session-waiting-chip" className="contents">
        <Badge value={waiting} tone="amber" />
      </span>
    );
  }
  const label = outcomeLabel(session);
  if (label) {
    return (
      <span data-testid="session-outcome-chip" className="contents">
        <Badge value={label} tone="amber" />
      </span>
    );
  }
  return <Badge value={session.status} tone={tone} />;
}

export default SessionStatusBadge;
