/**
 * v1.455.0 (readiness Wave F, item 13): the `why` and `asker` an AGENT RUN's
 * ask carries, read defensively off wherever the ask arrives — the
 * `approval.requested` event, the pending listing's row, a session's
 * `waiting_on`, the objective screen's member.
 *
 * A chat turn's approval frame has carried both since v1.417.0, and with
 * `why` the ApprovalCard is the short card that offers "No, tell it what to
 * do instead". Agent-run asks did not carry them, so the run page, the
 * listing rows and the objective screen drew the long card with no redirect.
 * The daemon half adds them; this reads them the way `useChatStream` reads a
 * frame's (a trimmed string cut to 400; `asker` only from the two words the
 * daemon sends). Absent or junk = nothing, so an older Iron Jarvis keeps the
 * old card byte for byte.
 *
 * No imports: read by the chat page, the run page and the objective screen.
 */

export type Asker = "model" | "iron_jarvis";

export interface AskWhy {
  why?: string;
  asker?: Asker;
}

/** `{why?, asker?}` off a raw ask record; `{}` when it carries neither. */
export function askWhyOf(raw: unknown): AskWhy {
  if (!raw || typeof raw !== "object") return {};
  const r = raw as { why?: unknown; asker?: unknown };
  const out: AskWhy = {};
  if (typeof r.why === "string" && r.why.trim()) out.why = r.why.trim().slice(0, 400);
  if (r.asker === "model" || r.asker === "iron_jarvis") out.asker = r.asker;
  return out;
}

/** What the run page says once a redirect's words could not reach a run
 *  that is still working (an agent run takes no note mid-run): they wait in
 *  the follow-up box. */
export const RUN_REDIRECT_KEPT =
  "Declined. Your words are in the follow-up box: send them once the run has finished.";

/** The objective screen's twin: its follow-up box is "Ask for changes",
 *  which opens once the objective has finished. */
export const MISSION_REDIRECT_KEPT =
  "Declined. Your words are kept for Ask for changes, which opens once the objective has finished.";
