/**
 * A CODING SESSION'S REPLY CARRIED ON AFTER A RESTART (v1.391.0, the long-jobs
 * plan's Phase 2, decision 2).
 *
 * When an update, a restart or a crash cuts off a reply that was WORKING, the
 * app carries it on by itself at the next start, from its last finished step:
 * the daemon writes a hidden "carry on" question with `resumedFrom` (the cut
 * off reply's turn id), and the reply that carries it on keeps `resumedFrom`
 * too (`harness/sessions.LINE_KEYS`). This module is the screen's ONE reading
 * of that, and its words:
 *
 *  - the reply that carried on says so in one plain line (`RESUMED_LINE`);
 *  - the cut-off reply above it no longer says "press Retry" — it says it
 *    carried on below (`CUT_CARRIED_ON`), with no Retry (the work goes on);
 *  - the Timeline names both (`timelineResumeNote`).
 *
 * A reply that was WAITING on the user when the app stopped is never carried
 * on by itself: it keeps v1.389.0's line and Retry.
 *
 * Pure; no React, no fetch.
 */

import type { BuildMessage } from "@/lib/buildSessions";

/** The one line on a reply that carried on a cut-off one. */
export const RESUMED_LINE = "Carried on by itself after Iron Jarvis restarted.";
/** A cut-off reply's line once the app carried it on. */
export const CUT_CARRIED_ON =
  "Iron Jarvis stopped here (an update, a restart or a crash). It carried on by itself below.";
/** The Timeline's note on a reply that carried on a cut-off one. */
export const TIMELINE_RESUMED = "This reply carried on the one a restart cut off.";
/** The Timeline's note on a reply a stop cut off (it kept what ran). */
export const TIMELINE_CUT = "Iron Jarvis stopped while this reply worked. This is what ran before it stopped.";
/** …and, when it was carried on. */
export const TIMELINE_CUT_CARRIED = `${TIMELINE_CUT} It carried on in the next reply.`;
/** The Timeline's reply list marks a reply that carried on. */
export const CARRIED_CHOICE = "(carried on)";

/** The turn id a line carries on ("" for every other line). */
export function resumedFrom(m: BuildMessage | null | undefined): string {
  const v = (m as { resumedFrom?: unknown } | null | undefined)?.resumedFrom;
  return typeof v === "string" ? v.trim().slice(0, 120) : "";
}

/** The turn ids the app carried on (a later line names them). */
export function carriedOnTurns(messages: readonly BuildMessage[]): Set<string> {
  const out = new Set<string>();
  for (const m of messages) {
    const from = resumedFrom(m);
    if (from) out.add(from);
  }
  return out;
}

/** Was this reply cut off by a stop of the app (not by the user's Stop)? The
 *  daemon gives such a line `interrupted` AND an `error`; a reply the user
 *  stopped has no error. */
export function cutByStop(m: BuildMessage | null | undefined): boolean {
  return !!m && m.role === "assistant" && m.interrupted === true && typeof m.error === "string" && m.error.trim() !== "";
}

/** Was this cut-off reply carried on by the app? */
export function carriedOn(m: BuildMessage | null | undefined, carried: ReadonlySet<string>): boolean {
  return cutByStop(m) && !!m?.turnId && carried.has(m.turnId);
}

/** The Timeline's note for a saved reply, or null. */
export function timelineResumeNote(m: BuildMessage | null | undefined, carried: ReadonlySet<string>): string | null {
  if (!m || m.role !== "assistant") return null;
  if (resumedFrom(m)) return TIMELINE_RESUMED;
  if (cutByStop(m)) return carriedOn(m, carried) ? TIMELINE_CUT_CARRIED : TIMELINE_CUT;
  return null;
}
