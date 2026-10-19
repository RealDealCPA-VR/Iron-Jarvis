/**
 * v1.358.0 (wave 8, lane 8C): ONE receipt per helper-agent run in chat.
 *
 * THE FAILURE THIS ANSWERS (the re-audit's regression). One run that chat
 * handed to an agent showed its receipt TWICE: the settled hand-off row
 * (components/chat/AgentRunRow + AgentRunReceipt: "Ran on Opus 5.5 · cloud",
 * the action lines, the folder, "Open run") and, under the agent's reply, the
 * boxed run card (components/chat/RunResultCard: "answered by Opus 5.5", the
 * same action lines as chips, "What it did", "Full transcript →", "Task
 * complete") — the same facts in two sets of words.
 *
 * The chat page now pairs each reply that carries a run's ledger result with
 * the hand-off row ABOVE it whose saved stamp already carries the receipt
 * (`stampCarriesReceipt`). For such a pair:
 *   - the reply draws only what the row does not say (RunResultCard
 *     `withRow`): the files, an honest note, the errors folded, Try again /
 *     Revert — quiet lines, no box;
 *   - the row's opened body takes the reply's step-by-step log (its saved
 *     `activity`), so "what it did" is said once, in one place.
 * A reply with no such row (an older stamp with no receipt words, a run that
 * was started some other way, a stamp a Stop wrote before any ledger read)
 * keeps the full card exactly as before.
 *
 * Pure data, no React, no import of the run card's module (the chat page
 * loads that card on demand: a value imported from it would undo that).
 */

import { readAgentRun, type AgentRunStamp } from "@/lib/agentRun";

/** True when a settled stamp carries the run's RECEIPT: how it ended (its
 *  status) AND what served it or what it did (provider, model or tool
 *  counts) — the words finalize stamps from the ledger read since v1.352.0.
 *  A stamp from before that, or the bare one a Stop writes at once, does not:
 *  its row says only how long, and the reply keeps the full card. */
export function stampCarriesReceipt(run: AgentRunStamp | null | undefined): boolean {
  if (!run || !run.status) return false;
  return !!(run.provider || run.model || (run.tools && run.tools.length));
}

/** The fields of a chat message this file reads. */
export interface ReceiptLineLike {
  escalated?: string;
  fromSession?: string;
  runResult?: { session_id?: unknown; activity?: unknown } | null;
}

export interface RunReceiptPairs {
  /** For a hand-off message (by index): the paired reply's `activity` (the
   *  same reference the message holds, so a memoized row re-renders only
   *  when it changes). */
  activityFor: Readonly<Record<number, unknown>>;
  /** For a reply message (by index): its run's settled row, above it,
   *  already carries the receipt — draw no second copy. */
  underRow: Readonly<Record<number, true>>;
}

/** The session a reply's run result belongs to: the result's own id, else
 *  the session the reply came from. "" when neither is a string. */
function replySession(m: ReceiptLineLike): string {
  const fromResult = m.runResult && typeof m.runResult.session_id === "string" ? m.runResult.session_id : "";
  return fromResult || (typeof m.fromSession === "string" ? m.fromSession : "");
}

/**
 * Pair every reply that carries a run result with the settled hand-off row
 * above it for the same session, when that row carries the receipt. The
 * newest hand-off for a session wins (a re-run hands the same work over
 * again). O(n); read once per change of the transcript.
 */
export function pairRunReceipts(msgs: ReadonlyArray<object> | null | undefined): RunReceiptPairs {
  const activityFor: Record<number, unknown> = {};
  const underRow: Record<number, true> = {};
  if (!Array.isArray(msgs)) return { activityFor, underRow };
  // The settled rows seen so far, by session -> their message index.
  const rows = new Map<string, number>();
  msgs.forEach((raw, i) => {
    if (!raw || typeof raw !== "object") return;
    // A saved line is untrusted: read only the fields this file needs.
    const m = raw as ReceiptLineLike;
    if (m.escalated) {
      const run = readAgentRun(m);
      if (run && stampCarriesReceipt(run)) rows.set(run.session, i);
      return;
    }
    if (!m.runResult) return;
    const session = replySession(m);
    const row = session ? rows.get(session) : undefined;
    if (row === undefined) return;
    underRow[i] = true;
    activityFor[row] = m.runResult.activity;
  });
  return { activityFor, underRow };
}
