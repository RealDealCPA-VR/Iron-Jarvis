/**
 * The words an @-agent's chat line says about itself (v1.420.0, C6.4).
 *
 * A remote's inbound line carries a `kind` (agents/remote.INBOUND_KINDS:
 * message | progress | question | done) and the page adds `pending` (it took
 * the task and will report back). The page used to print the kind itself, so
 * a finished remote's reply said "done" and an unknown kind leaked straight
 * onto the screen. Now each kind is a phrase from this table, or nothing:
 * a plain reply and a finished one need no label (the reply IS the answer),
 * and a kind this table does not know shows nothing rather than a raw word.
 */

/** The label beside the agent's name, or "" for none. */
export function panelKindWords(kind: string | undefined | null): string {
  switch (String(kind ?? "").trim()) {
    case "progress":
      return "working…";
    case "pending":
      return "working, will report back";
    case "question":
      return "has a question";
    default:
      // "message", "done", "" and anything unknown: no label.
      return "";
  }
}

/** The label beside a seat that could not answer. Its reason is the line's
 *  own content: the daemon writes it there in plain words
 *  (agents/threads.seat_error_words), never the raw error. */
export const PANEL_ERROR_LABEL = "couldn't answer";

/** The line a Stop leaves when an @-round is ended before anyone answered. */
export const PANEL_STOPPED_LINE = "Stopped.";

/** The daemon's own 404 words for a stop whose round is no longer running
 *  (routes/agents.py `stop_chat_panel`; pinned equal by
 *  tests/test_panel_round_words_stop_review_v1420.py). */
export const PANEL_ROUND_ENDED_DETAIL = "That round isn't running.";

/** Did a stop answer say the round had ALREADY ended on its own (v1.420.0
 *  review)? Then its replies are on their way — the page must let them land,
 *  never write "Stopped." over them. Read by shape (status + the daemon's own
 *  words), never by class: a missing route (an older Iron Jarvis) is a 404
 *  too, with other words, and still stops the wait. */
export function panelRoundEnded(e: unknown): boolean {
  const err = e as { status?: unknown; message?: unknown } | null;
  return !!err && err.status === 404 && String(err.message ?? "") === PANEL_ROUND_ENDED_DETAIL;
}
