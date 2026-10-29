/**
 * What a reply's receipt says about what happened AROUND the answer
 * (v1.355.0, product-readiness G55 + G64):
 *
 *  - `refit` — the model refused the chat as too long, so Iron Jarvis left
 *    older messages out and tried once more (`chat_turn._overflow_refit`).
 *    Only the model used to be told; the reply now says "Left out 12 older
 *    messages to fit this model", because an answer that ignored earlier
 *    context otherwise looks complete.
 *  - `approved` — the cards the user ALLOWED this turn ([{tool, decision,
 *    count?}], the stream lane's `approval_resolved` answers). Once a turn
 *    ended the saved reply recorded only refusals; the expanded receipt now
 *    lists "You approved: save a file (once)".
 *
 * Since v1.355.0 the POST /chat response and the /chat/stream done frame
 * ALWAYS carry both keys (`refit` null when nothing was left out, `approved`
 * [] — always [] on the POST lane, which never asks). They cross a JSON
 * boundary twice (the wire, then a saved thread read back from disk), so
 * every reader goes through the decoders here: a WHITELIST, like every other
 * receipt field (lib/turnReads).
 *
 * Pure module, no React: the stream hook, the page and the receipt import it,
 * and the tests that mock `@/lib/useChatStream` are unaffected.
 */

import { askPhrase } from "@/lib/approvalWords";

/** What a too-long refit left out (`{dropped: n}`; n may be 0 when only the
 *  turn's own earlier tool output was trimmed). */
export interface TurnRefit {
  dropped: number;
}

/** One card the user allowed this turn. `decision` is the daemon's answer
 *  VALUE (`once` | `conversation` | `tab` | `always`); `count` > 1 when ONE
 *  card covered a batch of the same kind of call. */
export interface ApprovedCall {
  tool: string;
  decision: string;
  count?: number;
}

/** The answers that let a call run (a deny or a timeout never lands here). */
const APPROVING = new Set(["once", "conversation", "tab", "always"]);

/** Most approvals one reply keeps (the daemon's `APPROVED_MAX`). */
export const APPROVED_MAX = 20;

/** The `refit` field's normal form, or null for anything else (null, an
 *  older daemon, a hand-edited thread). */
export function decodeRefit(raw: unknown): TurnRefit | null {
  if (!raw || typeof raw !== "object") return null;
  const n = (raw as { dropped?: unknown }).dropped;
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return null;
  return { dropped: Math.floor(n) };
}

/** The `approved` field's normal form: rows with a tool name and an
 *  approving decision, `count` only when it is a whole number above 1, at
 *  most APPROVED_MAX. Anything that is not a list reads as none. */
export function decodeApproved(raw: unknown): ApprovedCall[] {
  if (!Array.isArray(raw)) return [];
  const out: ApprovedCall[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const r = row as { tool?: unknown; decision?: unknown; count?: unknown };
    const tool = typeof r.tool === "string" ? r.tool.trim().slice(0, 200) : "";
    const decision = typeof r.decision === "string" ? r.decision.trim().toLowerCase() : "";
    if (!tool || !APPROVING.has(decision)) continue;
    const count =
      typeof r.count === "number" && Number.isFinite(r.count) && r.count > 1
        ? Math.floor(r.count)
        : 0;
    out.push({ tool, decision, ...(count > 1 ? { count } : {}) });
    if (out.length >= APPROVED_MAX) break;
  }
  return out;
}

/**
 * The receipt's words for a refit, or null when there was none: "Left out 12
 * older messages to fit this model" (one: "1 older message"). A retry that
 * left no MESSAGE out only trimmed the turn's own earlier tool output, and
 * says that instead.
 */
export function refitLine(refit: TurnRefit | null | undefined): string | null {
  const r = decodeRefit(refit);
  if (!r) return null;
  if (r.dropped === 0) return "Shortened earlier tool results to fit this model";
  return `Left out ${r.dropped} older message${r.dropped === 1 ? "" : "s"} to fit this model`;
}

/** Hover words for the refit part: why, and what it means for the answer. */
export const REFIT_TITLE =
  "The model said this chat was too long for it, so Iron Jarvis left the oldest messages out and asked again. The answer did not see them.";

/** The same "why" for a retry that left NO message out (`dropped: 0`): only
 *  the results of this reply's own earlier steps were shortened, so the
 *  REFIT_TITLE words ("left the oldest messages out") would be untrue. */
export const REFIT_TOOLS_TITLE =
  "The model said this chat was too long for it, so Iron Jarvis shortened the results of this reply's earlier steps and asked again. No messages were left out.";

/** The "why" for a refit (hover and the expanded receipt), or null when
 *  there was none — the words for what actually happened. */
export function refitTitle(refit: TurnRefit | null | undefined): string | null {
  const r = decodeRefit(refit);
  if (!r) return null;
  return r.dropped === 0 ? REFIT_TOOLS_TITLE : REFIT_TITLE;
}

/** How far an approval reached, in the card's own words (lib/approvalWords:
 *  "Allow once" / "Allow for this chat" / "Allow for this tab" / "Always
 *  allow exactly this"). */
export function approvalReach(decision: string): string {
  switch (decision) {
    case "conversation":
      return "for this chat";
    case "tab":
      return "for this tab";
    case "always":
      return "always, exactly this";
    default:
      return "once";
  }
}

/** One approval as the expanded receipt says it: "You approved: save a file
 *  (once)"; a batch card adds the count ("(once, 3 calls)"). The words come
 *  from the approval card's own phrase (lib/approvalWords.askPhrase). */
export function approvedLine(a: ApprovedCall): string {
  const reach = approvalReach(a.decision);
  const many = a.count && a.count > 1 ? `, ${a.count} calls` : "";
  return `You approved: ${askPhrase(a.tool)} (${reach}${many})`;
}

/**
 * v1.387.0 (the reply budget, review): a reply the model was writing when it
 * ran out of room — its OUTPUT limit — before a tool call was complete. The
 * daemon ran nothing from it and either asked the model to carry on (writing
 * a large file in parts: `recovered`) or, after too many in a row, ended the
 * turn saying so. The route object carries `cut_off: {times, recovered}`
 * (both lanes, LAST, only when it happened); this is its whitelist.
 */
export interface TurnCutOff {
  times: number;
  recovered: boolean;
}

/** The route's `cut_off` field's normal form, or null (absent, an older
 *  daemon, a hand-edited thread). */
export function decodeCutOff(raw: unknown): TurnCutOff | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { times?: unknown; recovered?: unknown };
  const n = r.times;
  if (typeof n !== "number" || !Number.isFinite(n) || n < 1) return null;
  return { times: Math.floor(n), recovered: r.recovered === true };
}

/** The receipt's words, or null when no reply was cut off: "Cut off at the
 *  output limit, picked up again" (more than once: "… 2 times, picked up
 *  again"); a turn that ENDED on a cut says only that it was cut off — its
 *  reply already says what happened. */
export function cutOffLine(raw: unknown): string | null {
  const c = decodeCutOff(raw);
  if (!c) return null;
  const times = c.times > 1 ? ` ${c.times} times` : "";
  return c.recovered
    ? `Cut off at the output limit${times}, picked up again`
    : `Cut off at the output limit${times}`;
}

/** Hover words (and the expanded receipt's) for a reply that was cut off and
 *  went on. */
export const CUT_OFF_TITLE =
  "The model ran out of room for one reply (its output limit) before a tool call was complete. Iron Jarvis ran nothing from that reply and asked it to carry on, writing large files in parts.";

/** The same for a turn that ENDED on a cut: nothing from it was run. */
export const CUT_OFF_END_TITLE =
  "The model kept running out of room for one reply (its output limit) before a tool call was complete, so the reply ended there. Nothing from the cut replies was run.";

/** The "why" for a cut-off reply, or null when there was none. */
export function cutOffTitle(raw: unknown): string | null {
  const c = decodeCutOff(raw);
  if (!c) return null;
  return c.recovered ? CUT_OFF_TITLE : CUT_OFF_END_TITLE;
}

/**
 * v1.406.0 part 2 (Accept edits / Auto, the daemon's `core/auto_approve`):
 * the calls this turn's posture let through with NO card. The route object
 * carries `auto_approved: {count, calls: [{tool, rule, why}]}` (both lanes,
 * LAST, only when it happened, at most 20 calls listed); this is its
 * whitelist. `why` is the daemon's own words for the rule ("it runs the
 * project's tests", "a quick check said it is routine for what you asked").
 */
export interface AutoApprovedCall {
  tool: string;
  rule: string;
  why: string;
}

export interface TurnAutoApproved {
  count: number;
  calls: AutoApprovedCall[];
}

/** The route's `auto_approved` field's normal form, or null (absent, an
 *  older daemon, a hand-edited thread). */
export function decodeAutoApproved(raw: unknown): TurnAutoApproved | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { count?: unknown; calls?: unknown };
  const n = r.count;
  if (typeof n !== "number" || !Number.isFinite(n) || n < 1) return null;
  const calls: AutoApprovedCall[] = [];
  if (Array.isArray(r.calls)) {
    for (const row of r.calls) {
      if (!row || typeof row !== "object") continue;
      const c = row as { tool?: unknown; rule?: unknown; why?: unknown };
      const tool = typeof c.tool === "string" ? c.tool.trim().slice(0, 200) : "";
      if (!tool) continue;
      calls.push({
        tool,
        rule: typeof c.rule === "string" ? c.rule.trim().slice(0, 40) : "",
        why: typeof c.why === "string" ? c.why.trim().slice(0, 200) : "",
      });
      if (calls.length >= APPROVED_MAX) break;
    }
  }
  return { count: Math.floor(n), calls };
}

/** The collapsed receipt's words: "3 actions approved automatically" (one:
 *  "1 action approved automatically"), or null when none were. */
export function autoApprovedLine(raw: unknown): string | null {
  const a = decodeAutoApproved(raw);
  if (!a) return null;
  return `${a.count} action${a.count === 1 ? "" : "s"} approved automatically`;
}

/** One expanded row: "Approved automatically: run a command (it runs the
 *  project's tests)". */
export function autoApprovedRow(c: AutoApprovedCall): string {
  const why = c.why ? ` (${c.why})` : "";
  return `Approved automatically: ${askPhrase(c.tool)}${why}`;
}
