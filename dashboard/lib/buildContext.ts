/**
 * How full a Build session's conversation is, and Compact (v1.374.0, IJ
 * harness part 2 Phase 3).
 *
 * THE NUMBERS ARE THE DAEMON'S. Every reply line the daemon writes carries
 * `context` — the planner's own figures for that turn (routes/build
 * `context_receipt`): `window` (what the model can read at once), `used`
 * (what the planned conversation sent), `percent` (what the WHOLE
 * conversation needs against that window — the figure the Compact offer keys
 * off, never the planned one, which fits by construction) and `level`
 * ("ok" | "suggest" | "auto"). The meter shows them; it computes nothing but
 * a fallback ratio when an answer carries no `percent`.
 *
 * COMPACT (`POST /build/sessions/{id}/compact`) summarizes the older turns
 * with the session's own model; the summary stands on the session
 * (`compaction` on its view) and later turns read it in their place. The
 * transcript is never cut.
 *
 * Pure (decoders and words), so the rules are tested on their own; the one
 * fetcher goes through lib/api.
 */

import { post } from "@/lib/api";
import { readSavedPlan, type PlanView } from "@/lib/planChecklist";
import type { CompactionInfo } from "@/components/chat/CompactionCard";

/** The daemon's own offer threshold (context/compaction SUGGEST_AT), used
 *  only when a reading names none. */
export const SUGGEST_AT_DEFAULT = 70;

/** One turn's reading, as the reply line keeps it. */
export interface TurnContext {
  window: number;
  used: number | null;
  /** How full the whole conversation is for this model, in percent. */
  percent: number;
  level: "ok" | "suggest" | "auto";
  suggestAt: number;
  /** Review (v1.374.0): no pin, measurement, catalog or probe knows this
   *  model's window, so `window` is the app's own safe size — the room the
   *  conversation is given, not the model's limit (the daemon's
   *  `window_known: false`). */
  appSized: boolean;
  /** Compaction is turned off in Settings: the reading is still true, but
   *  there is no Compact to offer (the chat's own rule). */
  compactionOff: boolean;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;

/** A reply line's `context`, or null when it holds no reading (an older
 *  line, a failed turn, no window). */
export function decodeTurnContext(raw: unknown): TurnContext | null {
  const r = rec(raw);
  const window = num(r?.window);
  if (!r || !window) return null;
  const used = num(r.used);
  const pct = num(r.percent);
  // The daemon says the percent (whole-conversation demand). Only an answer
  // that does not falls back to what the planned turn used.
  const percent = pct !== null ? Math.round(pct) : used !== null ? Math.round((used / window) * 100) : null;
  if (percent === null) return null;
  const suggestAt = num(r.suggest_at) ?? SUGGEST_AT_DEFAULT;
  const level =
    r.level === "ok" || r.level === "suggest" || r.level === "auto"
      ? r.level
      : percent >= suggestAt
        ? "suggest"
        : "ok";
  return {
    window: Math.round(window),
    used: used !== null ? Math.round(used) : null,
    percent,
    level,
    suggestAt,
    appSized: r.window_known === false,
    compactionOff: r.disabled === true,
  };
}

/** The newest reading in a session's transcript and the line it came from. */
export function latestContext(
  messages: readonly { role: string; context?: unknown; at?: string; turnId?: string }[],
): { context: TurnContext; at: string | null; turnId: string | null } | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const c = decodeTurnContext(m.context);
    if (c) return { context: c, at: typeof m.at === "string" ? m.at : null, turnId: m.turnId ?? null };
  }
  return null;
}

/** The summary standing on a session (its view's `compaction`). */
export interface SessionCompaction {
  summary: string;
  throughTurn: string;
  covers: number;
  stripped: number;
  strippedClaims: string[];
  provider: string;
  model: string;
  at: string | null;
}

export function decodeCompaction(raw: unknown): SessionCompaction | null {
  const r = rec(raw);
  const summary = typeof r?.summary === "string" ? r.summary : "";
  const through = typeof r?.through_turn === "string" ? r.through_turn : "";
  if (!r || !summary.trim() || !through) return null;
  return {
    summary,
    throughTurn: through,
    covers: num(r.covers) ?? 0,
    stripped: num(r.stripped) ?? 0,
    strippedClaims: Array.isArray(r.stripped_claims)
      ? r.stripped_claims.filter((c): c is string => typeof c === "string" && !!c.trim()).slice(0, 20)
      : [],
    provider: typeof r.provider === "string" ? r.provider : "",
    model: typeof r.model === "string" ? r.model : "",
    at: typeof r.at === "string" && r.at ? r.at : null,
  };
}

/** The chat's compaction card reads this shape (components/chat/CompactionCard). */
export function compactionCardInfo(c: SessionCompaction): CompactionInfo {
  return {
    found: true,
    summary: c.summary,
    covers: c.covers,
    stripped: c.stripped,
    stripped_claims: c.strippedClaims,
    trigger: "manual",
    provider: c.provider,
    model: c.model,
    ...(c.at ? { created_at: c.at } : {}),
  };
}

function when(iso: string | null | undefined): number {
  if (!iso) return NaN;
  const s = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
  return Date.parse(s);
}

/** Was the reading taken BEFORE the standing summary was made? Then its
 *  numbers describe a conversation the next turn will not send whole: the
 *  meter says the earlier turns are summarized and offers no Compact. */
export function readingIsBeforeSummary(readingAt: string | null, compaction: SessionCompaction | null): boolean {
  if (!compaction) return false;
  const r = when(readingAt);
  const c = when(compaction.at);
  if (!Number.isFinite(c)) return false;
  return !Number.isFinite(r) || r <= c;
}

/** Offer Compact? Only on the daemon's word ("suggest" or "auto" — past
 *  70 %), never for a reading the standing summary already answered, and
 *  never while compaction is turned off in Settings (the chat's rule). */
export function offersCompact(reading: TurnContext | null, beforeSummary: boolean): boolean {
  return (
    !!reading &&
    !beforeSummary &&
    !reading.compactionOff &&
    (reading.level === "suggest" || reading.level === "auto")
  );
}

/** The meter's words, in plain language (no "tokens"). Review (v1.374.0): a
 *  window nobody knows is the app's own safe size, and is called that —
 *  never "what this model can read". */
export function meterTitle(reading: TurnContext): string {
  return reading.appSized
    ? `This conversation fills about ${reading.percent}% of what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.`
    : `This conversation fills about ${reading.percent}% of what this model can read at once.`;
}

/** What a press that worked says. */
export function compactedNote(covers: number, stripped: number): string {
  const head = `Summarized ${covers} earlier message${covers === 1 ? "" : "s"}. The model reads the summary in their place; the full conversation is kept.`;
  return stripped > 0
    ? `${head} ${stripped} claim${stripped === 1 ? "" : "s"} the conversation could not back up ${stripped === 1 ? "was" : "were"} left out.`
    : head;
}

/** Compact: the daemon summarizes, checks and keeps it; the answer is the
 *  stored summary (or a refusal in words, thrown as the ApiError). */
export async function compactSession(threadId: string): Promise<SessionCompaction | null> {
  const res = await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/compact`, {});
  return decodeCompaction(res);
}

/** A reply's own task list (its line's `plan`, the checklist's saved form)
 *  as the turn left it, or null. Nothing is left working: the step it was on
 *  failed with a turn that failed, else it is simply not ticked off yet.
 *  Review (v1.374.0): a Build plan CARRIES OVER — the next turn picks it up
 *  where this one stopped — so a step this turn did not reach stays "not
 *  started", never "not attempted" (the chat's settle rule, right for a
 *  helper's run that is over, read as a verdict on work still to come). */
export function replyPlan(raw: unknown, failed: boolean): PlanView | null {
  const view = readSavedPlan(raw);
  if (!view || view.steps.length === 0) return null;
  return {
    total: view.total,
    steps: view.steps.map((st) =>
      st.state === "running" ? { ...st, state: failed ? "failed" : "waiting" } : st,
    ),
  };
}
