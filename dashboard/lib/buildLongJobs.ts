/**
 * NEVER STALLS SILENTLY (v1.392.0, the long-jobs plan's Phase 3) — the
 * screen's reading of what a coding session's reply now says about itself:
 *
 * - `stall` frames (daemon harness/stall): no progress — no frame, no tool
 *   output — for ten minutes, ONCE per episode, never while the reply waits
 *   on the user. The live line says the daemon's own sentence ("No progress
 *   for 10 minutes — waiting on …") and offers Stop the step (when a tool
 *   call is what it waits on) and Stop. `{over: true}` ends the episode.
 * - `summarized` (harness/reply_summary): the reply wrote a summary of its
 *   own older rounds to fit the model ("Summarized N earlier steps to fit
 *   the model") — on the live line and on the saved reply.
 * - `capMet` (harness/reply_cap, decision 8): the session's OPTIONAL cap on a
 *   reply's time or list-price cost ended it, the step-limit way — the
 *   screen says which and offers Keep going. The cap itself is set in the
 *   session's ⋯ menu (`setup.cap`, PATCH /build/sessions/{id} `{cap}`).
 *
 * Every decoder whitelists; junk reads as absent. Kept apart from
 * lib/buildSessions (already long) and lib/api.ts (mocked wholesale).
 */

import { post } from "@/lib/api";

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const whole = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;

/* ------------------------------------------------------------ the stall */

/** The frame's event name (harness/stall STALL_FRAME). */
export const STALL_FRAME = "stall";

export interface StallNotice {
  /** The daemon's own sentence ("No progress for 10 minutes — waiting on …"). */
  words: string;
  /** The tool call Stop the step would stop; null when the reply waits on
   *  the model (or on a helper's own call): then only Stop is offered. */
  step: { id: string; name: string } | null;
}

/** A `stall` frame: a notice, `"over"` (progress ended the episode), or
 *  null (junk). */
export function decodeStall(data: unknown): StallNotice | "over" | null {
  const r = rec(data);
  if (!r) return null;
  if (r.over === true) return "over";
  const words = typeof r.words === "string" ? r.words.trim().slice(0, 400) : "";
  if (!words) return null;
  const s = rec(r.step);
  const id = typeof s?.id === "string" ? s.id.slice(0, 200) : "";
  const name = typeof s?.name === "string" ? s.name.slice(0, 200) : "";
  return { words, step: id ? { id, name } : null };
}

/** Stop the step (`POST /build/sessions/{id}/stop-step`): EXACTLY that tool
 *  call is cancelled and the reply carries on. `reply`: stop the whole
 *  reply too — first the reply, then the step, so the reply ends at once
 *  instead of waiting out a step that makes no progress. */
export function stopStep(threadId: string, turnId: string, stepId: string, reply = false): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(threadId)}/stop-step`, {
    step_id: stepId,
    ...(turnId ? { turn_id: turnId } : {}),
    ...(reply ? { reply: true } : {}),
  });
}

/** What the live line says once a step was stopped from it. */
export const STEP_STOPPED_NOTE = "Stopped that step. The reply carries on without it.";

/* ------------------------------------------------------------ the summary */

/** The frame's event name (harness/reply_summary SUMMARY_FRAME). */
export const SUMMARY_FRAME = "summarized";

export interface Summarized {
  steps: number;
  times: number;
}

/** A `summarized` frame or a reply line's `summarized`. */
export function decodeSummarized(raw: unknown): Summarized | null {
  const r = rec(raw);
  const steps = whole(r?.steps);
  if (!r || steps === null) return null;
  const times = whole(r.times);
  return { steps, times: times && times > 0 ? times : 1 };
}

/** "Summarized 12 earlier steps to fit the model" (the daemon's words). */
export function summarizedWords(s: Pick<Summarized, "steps">): string {
  return `Summarized ${s.steps} earlier step${s.steps === 1 ? "" : "s"} to fit the model`;
}

/* ------------------------------------------------------------ the cap */

export type CapKind = "time" | "cost";

/** The session's cap as set (`setup.cap`): minutes and/or dollars. */
export interface SessionCap {
  minutes?: number;
  usd?: number;
}

/** A cap the reply met (`capMet` on its line, `cap` on the done frame). */
export interface CapMet {
  kind: CapKind;
  limit: number;
}

export const CAP_MAX_MINUTES = 10_080;
export const CAP_MAX_USD = 10_000;
export const CAP_MIN_USD = 0.01;

export function decodeSessionCap(raw: unknown): SessionCap | null {
  const r = rec(raw);
  if (!r) return null;
  const out: SessionCap = {};
  if (typeof r.minutes === "number" && Number.isInteger(r.minutes) && r.minutes >= 1 && r.minutes <= CAP_MAX_MINUTES)
    out.minutes = r.minutes;
  if (typeof r.usd === "number" && Number.isFinite(r.usd) && r.usd >= CAP_MIN_USD && r.usd <= CAP_MAX_USD)
    out.usd = r.usd;
  return out.minutes !== undefined || out.usd !== undefined ? out : null;
}

export function decodeCapMet(raw: unknown): CapMet | null {
  const r = rec(raw);
  if (!r || (r.kind !== "time" && r.kind !== "cost")) return null;
  const limit = r.limit;
  if (typeof limit !== "number" || !Number.isFinite(limit) || limit <= 0) return null;
  return { kind: r.kind, limit };
}

export function usdWords(usd: number): string {
  return `$${usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function minutesWords(minutes: number): string {
  if (minutes >= 60 && minutes % 60 === 0) {
    const h = minutes / 60;
    return `${h} hour${h === 1 ? "" : "s"}`;
  }
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/** "30-minute" / "2-hour" — a time limit as an adjective. */
export function limitAdjective(minutes: number): string {
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60}-hour` : `${minutes}-minute`;
}

/** What the screen says under a reply that met the session's cap (the
 *  daemon's twin: harness/reply_cap.met_words). */
export function capMetWords(c: CapMet): string {
  return c.kind === "cost"
    ? `This reply stopped at this session's ${usdWords(c.limit)} cost limit (list price).`
    : `This reply stopped at this session's ${limitAdjective(c.limit)} time limit.`;
}

/** The menu's line for the session's cap ("No limit" when none). */
export function sessionCapWords(c: SessionCap | null): string {
  if (!c) return "No limit on a reply's time or cost.";
  const bits: string[] = [];
  if (c.minutes !== undefined) bits.push(minutesWords(c.minutes));
  if (c.usd !== undefined) bits.push(`${usdWords(c.usd)} list price`);
  return `Each reply stops at ${bits.join(" or ")}, whichever comes first.`;
}

/** The form's two boxes as typed → the cap to send, or an error sentence.
 *  Both empty = remove the cap (`null`). */
export function capFromForm(minutes: string, usd: string): { cap: SessionCap | null } | { error: string } {
  const m = minutes.trim();
  const u = usd.trim().replace(/^\$/, "");
  const out: SessionCap = {};
  if (m) {
    const n = Number(m);
    if (!Number.isInteger(n) || n < 1 || n > CAP_MAX_MINUTES)
      return { error: `Minutes must be a whole number from 1 to ${CAP_MAX_MINUTES.toLocaleString("en-US")}.` };
    out.minutes = n;
  }
  if (u) {
    const n = Number(u);
    if (!Number.isFinite(n) || n < CAP_MIN_USD || n > CAP_MAX_USD)
      return { error: `Dollars must be from 0.01 to ${CAP_MAX_USD.toLocaleString("en-US")}.` };
    out.usd = Math.round(n * 100) / 100;
  }
  return { cap: out.minutes !== undefined || out.usd !== undefined ? out : null };
}
