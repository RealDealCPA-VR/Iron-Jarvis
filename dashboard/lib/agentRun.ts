/**
 * v1.349.0 (polish wave B, lane B2): the facts behind the helper-agent row.
 *
 * When chat hands work to an agent (an escalation, an @agent with hands, or
 * "Have X do this"), the transcript used to show TWO things: a saved line
 * ("Handing this to the researcher: …") and, while the run lasted, a separate
 * live bubble with its steps. They are ONE row now
 * (components/chat/AgentRunRow): "The researcher is working · Step 2 of 4:
 * draft the summary · 1:24" while it runs, settling to "The researcher worked
 * for 2 min 5 s · 6 steps · Open run" once it is done. The idea comes from
 * 21st.dev agent-elements' subagent row (MIT, ideas only; nothing installed).
 *
 * Nothing here touches React. This file holds:
 *  - the STAMP finalize writes onto the hand-off message when the run ends
 *    (its session, start, end and step count), so the settled row is saved
 *    with the thread and reads the same after a reload or in another window;
 *  - the READER for that stamp, which treats a saved thread as untrusted (an
 *    older version, a hand edit): anything malformed reads as no stamp, and a
 *    message with no stamp renders exactly as it did before this release;
 *  - a tiny registry of the runs a live row is showing right now, so the saved
 *    hand-off line can step aside for the live row without the chat page
 *    handing every message row the page's waiting state.
 */

import { normalizeIso } from "@/lib/format";
import { readSavedPlan, toSavedPlan, type PlanView, type SavedPlan } from "@/lib/planChecklist";

/** What the settled row needs, stamped on the hand-off message by finalize. */
export interface AgentRunStamp {
  /** The agent session that did the work (the row's "Open run" link). */
  session: string;
  /** When the run started and ended, in ms since the epoch: the SESSION's own
   *  `created_at` / `finished_at`, one clock for both ends, so the duration is
   *  the daemon's and not this window's. Null when not known. */
  startedAt: number | null;
  endedAt: number | null;
  /** How many steps the agent took: the ledger's count (`steps` on
   *  GET /sessions/{id}/result), the same number the run card under the reply
   *  shows, so the two never disagree. Null when not known. */
  steps: number | null;
  /** v1.350.0 (lane C1): the plan the run worked through, as it ended (goal
   *  + state per step, at most 20 steps of 200 characters; lib/planChecklist
   *  SavedPlan). Absent for a run that made no plan, and on every stamp from
   *  before v1.350.0, which read exactly as they did. */
  plan?: SavedPlan;
}

/** The fields of a chat message this file reads; the page's ChatMessage
 *  satisfies it, and so does a raw line off GET /chat/threads/{id}. */
export interface HandOffLike {
  escalated?: string;
  escalatedTo?: string;
  awaitingSession?: string;
}

/** The key the stamp is saved under on the hand-off message. */
export const AGENT_RUN_KEY = "agentRun";

/** A session id fit for a link: one path segment of plain id characters. A
 *  saved thread is untrusted, so anything else reads as no session. */
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

/** A daemon timestamp (naive UTC ISO, the daemon's own shape) in ms since the
 *  epoch, or null. A zone-less stamp is read as UTC (lib/format normalizeIso):
 *  read as local time, a run in a UTC-offset zone would be hours long. */
export function stampMs(iso: unknown): number | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const t = Date.parse(normalizeIso(iso.trim()));
  return Number.isFinite(t) ? t : null;
}

function epochOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

function countOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;
}

/** Build the stamp from what finalize read: the session row's times and the
 *  ledger's step count. Junk in any field becomes null, never a guess. */
export function makeAgentRunStamp(input: {
  session: string;
  createdAt?: unknown;
  finishedAt?: unknown;
  steps?: unknown;
  /** The run's checklist as it ended (settled), or nothing for no plan. */
  plan?: PlanView | null;
}): AgentRunStamp {
  const startedAt = stampMs(input.createdAt);
  let endedAt = stampMs(input.finishedAt);
  // An end before the start is a clock oddity: the length is then unknown,
  // and an unknown length is said as nothing rather than as a wrong one.
  if (startedAt !== null && endedAt !== null && endedAt < startedAt) endedAt = null;
  const plan = toSavedPlan(input.plan);
  return {
    session: input.session,
    startedAt,
    endedAt,
    steps: countOrNull(input.steps),
    // Only when there is one: a stamp with no plan keeps its old shape.
    ...(plan ? { plan } : {}),
  };
}

/** The stamp saved on a message, checked field by field, or null when there
 *  is none (a message from before v1.349.0) or it is not one. */
export function readAgentRun(m: unknown): AgentRunStamp | null {
  if (!m || typeof m !== "object") return null;
  const raw = (m as Record<string, unknown>)[AGENT_RUN_KEY];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.session !== "string" || !SESSION_ID.test(r.session)) return null;
  const startedAt = epochOrNull(r.startedAt);
  let endedAt = epochOrNull(r.endedAt);
  if (startedAt !== null && endedAt !== null && endedAt < startedAt) endedAt = null;
  // v1.350.0: the saved plan, read as untrusted as the rest; a malformed one
  // is no plan, and the stamp around it still reads.
  const plan = toSavedPlan(readSavedPlan(r.plan));
  return {
    session: r.session,
    startedAt,
    endedAt,
    steps: countOrNull(r.steps),
    ...(plan ? { plan } : {}),
  };
}

/** How long the run took in ms, or null when either end is unknown. */
export function runDurationMs(run: AgentRunStamp): number | null {
  if (run.startedAt === null || run.endedAt === null) return null;
  return run.endedAt >= run.startedAt ? run.endedAt - run.startedAt : null;
}

/** The hand-off message waiting on `session` (newest first), or null: the
 *  message whose saved line the live row takes the place of. */
export function handOffFor<M extends HandOffLike>(
  msgs: readonly M[],
  session: string | null | undefined,
): M | null {
  if (!session) return null;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m && m.escalated && m.awaitingSession === session) return m;
  }
  return null;
}

/** Stamp the hand-off message waiting on `session` with how the run went.
 *  Returns the SAME array when no message waits on it (a plain agent turn
 *  with no hand-off line, or a thread that never saved one), so a caller can
 *  tell "nothing to settle" by identity. Every other message keeps its
 *  object, so reference checks downstream (the save rebase) still hold. */
export function stampHandOff<M extends HandOffLike>(
  msgs: M[],
  session: string,
  stamp: AgentRunStamp,
): M[] {
  const target = handOffFor(msgs, session);
  if (!target) return msgs;
  return msgs.map((m) => (m === target ? { ...m, [AGENT_RUN_KEY]: stamp } : m));
}

/** finalize's one call (both of its paths, the composed reply and the
 *  daemon's appended line): stamp the hand-off waiting on `session` from the
 *  session row it read (`created_at`, `finished_at`) and the ledger's step
 *  count, plus (v1.350.0) the run's settled plan checklist when it made one.
 *  The SAME array back when nothing waits on it (see stampHandOff). */
export function settleHandOff<M extends HandOffLike>(
  msgs: M[],
  session: string,
  row: { created_at?: unknown; finished_at?: unknown } | null | undefined,
  steps?: unknown,
  plan?: PlanView | null,
): M[] {
  return stampHandOff(
    msgs,
    session,
    makeAgentRunStamp({ session, createdAt: row?.created_at, finishedAt: row?.finished_at, steps, plan }),
  );
}

/** The sentence the saved hand-off line says (unchanged since v1.139.0): the
 *  named specialist when one was chosen, else the plain hand-off. ONE source,
 *  for the line itself and for the row that settles it. */
export function handOffSentence(escalated: string, escalatedTo?: string | null): string {
  return escalatedTo
    ? `Handing this to ${escalatedTo}: ${escalated}.`
    : `Taking this on properly: ${escalated}.`;
}

/** The row's subject, at the start of a sentence: "the researcher" reads
 *  "The researcher", "your invoice-chaser agent" reads "Your invoice-chaser
 *  agent". An unnamed hand-off (the default agent) is "The agent". */
export function agentRunName(escalatedTo?: string | null): string {
  const flat = typeof escalatedTo === "string" ? escalatedTo.replace(/\s+/g, " ").trim() : "";
  if (!flat) return "The agent";
  const clipped = flat.length > 60 ? `${flat.slice(0, 59)}…` : flat;
  return clipped.charAt(0).toUpperCase() + clipped.slice(1);
}

/* ------------------------------------------------------- the live registry */

// The sessions a live row is on screen for, counted (two rows for one run
// must both unmount before the line comes back). A module store, not React
// state: the saved line asks it directly, so the chat page never hands every
// message row its waiting state (the v1.250.0 S-05 rule, one memoized row).
const live = new Map<string, number>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const cb of [...listeners]) cb();
}

export const liveRuns = {
  /** A live row for `session` is on screen. Returns its removal. */
  add(session: string): () => void {
    live.set(session, (live.get(session) ?? 0) + 1);
    emit();
    let done = false;
    return () => {
      if (done) return;
      done = true;
      const n = (live.get(session) ?? 0) - 1;
      if (n > 0) live.set(session, n);
      else live.delete(session);
      emit();
    };
  },
  /** Is a live row on screen for `session` right now? */
  has(session: string | null | undefined): boolean {
    return !!session && (live.get(session) ?? 0) > 0;
  },
  subscribe(cb: () => void): () => void {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  },
};
